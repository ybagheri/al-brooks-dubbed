"""Al Brooks price-action terminology used to annotate transcripts.

The terminology is a *read-only annotation* resource. Phase 2 uses it to mark
where domain vocabulary occurs so that a later translation phase can attach
approved Persian equivalents. It is never used to rewrite the source text: a
term match never changes what the speaker said.

The data lives in ``src/resources/terminology.json`` so it can be extended by
non-developers. Adding an approved Persian equivalent is a one-field change::

    {"id": "bull_flag", "canonical": "bull flag", ..., "persian": "پرچم صعودی"}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from .errors import TerminologyError
from .text_cleaning import comparison_key, damerau_levenshtein, word_tokens

logger = logging.getLogger(__name__)

RESOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "resources" / "terminology.json"

SUPPORTED_SCHEMA_VERSION: Final[int] = 1

#: Terms whose plain English meaning is common outside trading. Matches are
#: reported at a lower confidence so downstream stages do not over-trust them.
#: Very common English words that must never be reported as a confused term.
#: Without this list almost every function word collides with some domain word
#: ("but"/"bull", "near"/"bear", "more"/"move").
CONFUSABLE_STOPWORDS: frozenset[str] = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "nor",
        "so",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "there",
        "here",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "doing",
        "done",
        "have",
        "has",
        "had",
        "having",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "not",
        "no",
        "yes",
        "i",
        "we",
        "you",
        "he",
        "she",
        "it",
        "they",
        "them",
        "him",
        "her",
        "his",
        "hers",
        "their",
        "ours",
        "your",
        "my",
        "mine",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "by",
        "as",
        "into",
        "onto",
        "about",
        "over",
        "under",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "again",
        "once",
        "more",
        "most",
        "much",
        "many",
        "some",
        "any",
        "all",
        "both",
        "each",
        "every",
        "other",
        "another",
        "such",
        "same",
        "own",
        "very",
        "just",
        "only",
        "also",
        "even",
        "still",
        "too",
        "get",
        "got",
        "go",
        "goes",
        "going",
        "come",
        "came",
        "come",
        "see",
        "saw",
        "seen",
        "say",
        "says",
        "said",
        "look",
        "looks",
        "looking",
        "know",
        "knows",
        "knew",
        "think",
        "thinks",
        "thought",
        "good",
        "better",
        "best",
        "bad",
        "worse",
        "worst",
        "big",
        "bigger",
        "small",
        "smaller",
        "high",
        "higher",
        "low",
        "lower",
        "long",
        "longer",
        "short",
        "shorter",
        "new",
        "old",
        "first",
        "second",
        "third",
        "last",
        "next",
        "now",
        "today",
        "yesterday",
        "time",
        "times",
        "way",
        "ways",
        "part",
        "parts",
        "thing",
        "things",
        "number",
        "numbers",
        "lot",
        "lots",
        "kind",
        "sort",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "morning",
        "because",
        "before",
        "after",
        "during",
        "while",
        "until",
        "since",
        "though",
        "although",
        "who",
        "whom",
        "whose",
        "what",
        "where",
        "when",
        "which",
        "why",
        "how",
    ]
)

AMBIGUOUS_CATEGORIES: Final[frozenset[str]] = frozenset({"structure", "market"})


@dataclass(frozen=True)
class TerminologyEntry:
    """One domain term."""

    id: str
    canonical: str
    category: str
    notes: str = ""
    persian: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def ambiguous(self) -> bool:
        """True when the term is also an ordinary English word."""

        return self.category in AMBIGUOUS_CATEGORIES

    def surfaces(self) -> tuple[str, ...]:
        """Every spelling that should match this term, canonical form first."""

        return (self.canonical, *self.aliases)


@dataclass(frozen=True)
class TerminologyMatch:
    """A single occurrence of a term inside a piece of text."""

    term_id: str
    canonical: str
    category: str
    matched_text: str
    start_char: int
    end_char: int
    ambiguous: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "canonical": self.canonical,
            "category": self.category,
            "matched_text": self.matched_text,
            "ambiguous": self.ambiguous,
        }


def _build_pattern(surface: str) -> re.Pattern[str]:
    """Compile a whole-word matcher for one spelling of a term.

    Periods match an optional period so that abbreviations such as ``i.e.``
    also match the way they are spoken.
    """

    tokens = surface.split()
    parts = [re.escape(token).replace(r"\.", r"\.?") for token in tokens]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class KnownConfusion:
    """A mistranscription that a human has confirmed occurs in this material."""

    wrong: str
    right: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"wrong": self.wrong, "right": self.right, "note": self.note}


@dataclass(frozen=True)
class TermConfusion:
    """A token that looks like a mistranscription of a known domain term.

    This is a *review hint*, never a correction: speech-to-text renders
    "trading range" as "training range" and "bear bar" as "bare bar", and both
    would produce a wrong translation if they reached the translator.
    """

    word: str
    position: int
    suspected_word: str
    distance: int
    position_in_sentence: int
    context: str = ""
    suspected_term_id: str = ""
    source: str = "anchored_edit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "suggested_word": self.suspected_word,
            "edit_distance": self.distance,
            "context": self.context,
            "source": self.source,
        }


#: The word being checked must be at least this long to be worth comparing.
MIN_CONFUSABLE_LENGTH: Final[int] = 4

#: An anchor's second word may be a little shorter ("bear bar") but must still
#: be a substantial word rather than a function word.
MIN_ANCHOR_WORD_LENGTH: Final[int] = 3

#: Edit distance allowed for the *generic* anchored check. One edit is safe;
#: two produces false positives such as "clear breakout" against "bear
#: breakout", which is indistinguishable from the real "bare bar" mistake
#: without human judgement. Distance-two cases belong in ``known_confusions``.
ANCHOR_MAX_DISTANCE: Final[int] = 1

#: Retained name for the curated/documented limit, used in documentation.
CONFUSABLE_MAX_DISTANCE: Final[int] = ANCHOR_MAX_DISTANCE


def _usable_anchor_word(*words: str) -> bool:
    """An anchor needs a substantial first word and a non-stopword second word.

    The first word is the one a mistranscription replaces, so it must be long
    enough for edit distance to mean something. The second word is matched
    exactly, so it only has to be a real word.
    """

    if len(words) != 2:
        return False
    first, second = words
    return (
        len(first) >= MIN_CONFUSABLE_LENGTH
        and len(second) >= MIN_ANCHOR_WORD_LENGTH
        and first not in CONFUSABLE_STOPWORDS
        and second not in CONFUSABLE_STOPWORDS
    )


class Terminology:
    """Lookup and annotation helper over the terminology resource."""

    def __init__(
        self,
        entries: list[TerminologyEntry],
        extra_anchors: tuple[tuple[str, str], ...] = (),
        known_confusions: tuple[KnownConfusion, ...] = (),
    ) -> None:
        self._entries = tuple(entries)
        self._extra_anchors = tuple(extra_anchors)
        self._known_confusions = tuple(known_confusions)
        self._vocabulary: dict[str, str] | None = None
        self._anchors: tuple[tuple[str, str], ...] | None = None
        self._domain_vocabulary: frozenset[str] | None = None
        self._anchors_by_second: dict[str, tuple[str, ...]] | None = None
        self._patterns: list[tuple[TerminologyEntry, re.Pattern[str]]] = []
        for entry in self._entries:
            for surface in entry.surfaces():
                if surface.strip():
                    self._patterns.append((entry, _build_pattern(surface)))
        # Longest spelling first so "wedge bull flag" wins over "wedge".
        self._patterns.sort(key=lambda pair: len(pair[1].pattern), reverse=True)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[TerminologyEntry, ...]:
        return self._entries

    def get(self, term_id: str) -> TerminologyEntry | None:
        for entry in self._entries:
            if entry.id == term_id:
                return entry
        return None

    def find(self, text: str) -> list[TerminologyMatch]:
        """Return non-overlapping matches, preferring the longest spelling.

        All candidate matches are gathered first and then swept in position
        order, keeping the longest match at each position. A single watermark
        replaces an interval search, which keeps this linearithmic rather than
        quadratic - important because a full lecture transcript is large.
        """

        if not text:
            return []

        candidates: list[tuple[int, int, TerminologyEntry, str]] = []
        for entry, pattern in self._patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                candidates.append((start, end, entry, match.group(0)))

        # Earliest position first; at the same position the longest match wins.
        candidates.sort(key=lambda item: (item[0], -item[1]))

        matches: list[TerminologyMatch] = []
        last_end = -1
        for start, end, entry, matched in candidates:
            if start < last_end:
                continue
            last_end = end
            matches.append(
                TerminologyMatch(
                    term_id=entry.id,
                    canonical=entry.canonical,
                    category=entry.category,
                    matched_text=matched,
                    start_char=start,
                    end_char=end,
                    ambiguous=entry.ambiguous,
                )
            )
        return matches

    def term_ids_in(self, text: str) -> list[str]:
        """Unique term ids found in ``text``, in order of first appearance."""

        seen: list[str] = []
        for match in self.find(text):
            if match.term_id not in seen:
                seen.append(match.term_id)
        return seen

    # ------------------------------------------------------------------
    # Confusable detection
    # ------------------------------------------------------------------
    def confusable_vocabulary(self) -> dict[str, str]:
        """Deprecated single-word index; retained for diagnostics only.

        Single-word edit distance is far too noisy on real lecture text
        ("bulls"/"bull", "near"/"bear", "reverse"/"reversal" are all correct
        English). Use :meth:`confusable_anchors` and :meth:`find_confusables`,
        which anchor each check on an exact neighbouring word.
        """

        if self._vocabulary is None:
            vocabulary: dict[str, str] = {}
            for entry in self._entries:
                for word in comparison_key(entry.canonical).split():
                    if len(word) >= 4:
                        vocabulary.setdefault(word, entry.id)
            self._vocabulary = vocabulary
        return self._vocabulary

    def confusable_anchors(self) -> tuple[tuple[str, str], ...]:
        """Word pairs that anchor a confusable check.

        Built from the consecutive word pairs of every multi-word term, plus
        the explicitly configured ``confusable_extra_anchors`` (the
        adjective+noun collocations such as "bear bar" that are Al Brooks
        vocabulary without being terms of their own).

        Both words must be at least four characters and must not be ordinary
        English words, otherwise weak anchors such as "head and" produce
        nothing but noise.
        """

        if self._anchors is None:
            anchors: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for entry in self._entries:
                words = comparison_key(entry.canonical).split()
                for first, second in zip(words, words[1:], strict=False):
                    pair = (first, second)
                    if pair in seen or not _usable_anchor_word(first, second):
                        continue
                    seen.add(pair)
                    anchors.append(pair)
            for pair in self._extra_anchors:
                if pair in seen or not _usable_anchor_word(*pair):
                    continue
                seen.add(pair)
                anchors.append(pair)
            self._anchors = tuple(anchors)
        return self._anchors

    def domain_vocabulary(self) -> frozenset[str]:
        """Every domain word, from canonical forms *and* aliases.

        A token that is already correct domain vocabulary is never reported:
        "bull" is not a misspelling of "bear", and without this check
        "a bull bar" would be flagged against the "bear bar" anchor.
        """

        if self._domain_vocabulary is None:
            words: set[str] = set()
            for entry in self._entries:
                for surface in entry.surfaces():
                    words.update(comparison_key(surface).split())
            self._domain_vocabulary = frozenset(words)
        return self._domain_vocabulary

    def find_confusables(self, text: str) -> list[TermConfusion]:
        """Find tokens that are probably mis-transcriptions of domain terms.

        Two complementary mechanisms, because neither alone is precise enough:

        1. **Curated confusions** from ``known_confusions`` in the resource.
           These are mistakes actually heard in this lecture series. They are
           reliable because a human decided they are errors.
        2. **Anchored edit distance** at a single edit, where the neighbouring
           word must match a domain word exactly. This catches unforeseen
           single-character slips without generating noise.

        A wider edit distance is deliberately *not* used generically: at
        distance two, "clear breakout" is indistinguishable from the real
        "bare bar" mistake, and the false positives swamp the true findings.

        The result is advisory only; nothing in the transcript is changed.
        """

        if not text:
            return []

        tokens = word_tokens(text)
        results: list[TermConfusion] = []
        seen: set[tuple[str, str]] = set()

        for confusion in self.known_confusions():
            wrong = comparison_key(confusion.wrong)
            right = comparison_key(confusion.right)
            if not wrong or not right:
                continue
            for position, token in enumerate(tokens):
                if token != wrong:
                    continue
                key = (token, right)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    TermConfusion(
                        word=token,
                        position=position,
                        suspected_word=right,
                        distance=1,
                        position_in_sentence=position,
                        context=" ".join(tokens[max(0, position - 2) : position + 3]).strip(),
                        source="known_confusion",
                    )
                )

        vocabulary = self.domain_vocabulary()
        by_second_word = self.anchors_by_second_word()
        for position in range(len(tokens) - 1):
            candidates = by_second_word.get(tokens[position + 1])
            if not candidates:
                continue
            token = tokens[position]
            if len(token) < MIN_CONFUSABLE_LENGTH:
                continue
            if token in CONFUSABLE_STOPWORDS or token in vocabulary:
                continue
            for first_word in candidates:
                if token == first_word:
                    continue
                distance = damerau_levenshtein(token, first_word, ANCHOR_MAX_DISTANCE)
                if distance is None or distance == 0:
                    continue
                key = (token, first_word)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    TermConfusion(
                        word=token,
                        position=position,
                        suspected_word=first_word,
                        distance=distance,
                        position_in_sentence=position,
                        context=f"{token} {tokens[position + 1]}",
                        source="anchored_edit",
                    )
                )

        results.sort(key=lambda item: (item.word, item.suspected_word))
        return results

    def anchors_by_second_word(self) -> dict[str, tuple[str, ...]]:
        """Index the anchors by their second word for a fast sweep.

        Looking the second word up in a dict first turns the scan from
        "every token against every anchor" into a single dictionary hit for
        almost every position, which keeps a full-length lecture fast.
        """

        if self._anchors_by_second is None:
            grouped: dict[str, list[str]] = {}
            for first_word, second_word in self.confusable_anchors():
                grouped.setdefault(second_word, []).append(first_word)
            self._anchors_by_second = {key: tuple(value) for key, value in grouped.items()}
        return self._anchors_by_second

    def known_confusions(self) -> tuple[KnownConfusion, ...]:
        """The curated, human-approved list of mistranscriptions."""

        return self._known_confusions

    def summary(self) -> dict[str, Any]:
        return {
            "term_count": len(self._entries),
            "categories": sorted({entry.category for entry in self._entries}),
            "translated_terms": sum(1 for entry in self._entries if entry.persian),
        }


def _parse_entries(payload: dict[str, Any]) -> list[TerminologyEntry]:
    schema_version = payload.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise TerminologyError(
            f"Unsupported terminology schema_version {schema_version!r}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}."
        )

    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list) or not raw_terms:
        raise TerminologyError("Terminology resource contains no 'terms' list.")

    entries: list[TerminologyEntry] = []
    seen_ids: set[str] = set()
    for position, raw in enumerate(raw_terms):
        if not isinstance(raw, dict):
            raise TerminologyError(f"Terminology entry #{position} is not an object.")
        term_id = str(raw.get("id", "")).strip()
        canonical = str(raw.get("canonical", "")).strip()
        if not term_id or not canonical:
            raise TerminologyError(
                f"Terminology entry #{position} needs a non-empty 'id' and 'canonical'."
            )
        if term_id in seen_ids:
            raise TerminologyError(f"Duplicate terminology id '{term_id}' in the resource.")
        seen_ids.add(term_id)

        aliases_raw = raw.get("aliases") or []
        if not isinstance(aliases_raw, list):
            raise TerminologyError(f"Terminology entry '{term_id}' has a non-list 'aliases'.")

        persian = raw.get("persian")
        entries.append(
            TerminologyEntry(
                id=term_id,
                canonical=canonical,
                category=str(raw.get("category", "general")),
                notes=str(raw.get("notes", "")),
                persian=str(persian) if persian else None,
                aliases=tuple(str(alias) for alias in aliases_raw),
            )
        )
    return entries


@lru_cache(maxsize=1)
def load_terminology() -> Terminology:
    """Load (and cache) the bundled terminology resource."""

    if not RESOURCE_PATH.is_file():
        raise TerminologyError(f"Terminology resource is missing: {RESOURCE_PATH}")
    try:
        payload = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise TerminologyError(f"Could not read the terminology resource: {exc}") from exc
    if not isinstance(payload, dict):
        raise TerminologyError("Terminology resource must contain a JSON object.")
    entries = _parse_entries(payload)
    return Terminology(
        entries,
        extra_anchors=_parse_anchors(payload),
        known_confusions=_parse_known_confusions(payload),
    )


def _parse_known_confusions(payload: dict[str, Any]) -> tuple[KnownConfusion, ...]:
    """Read the optional ``known_confusions`` list from the resource.

    These are mistakes a human has confirmed occur in this material. They are
    the reliable half of the confusable check, because the generic anchored
    scan cannot tell a real "bare bar" from an innocent "clear breakout".
    """

    raw = payload.get("known_confusions") or []
    if not isinstance(raw, list):
        raise TerminologyError("'known_confusions' must be a list of objects.")

    confusions: list[KnownConfusion] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TerminologyError(f"known_confusions entry #{position} is not an object.")
        wrong = str(item.get("wrong", "")).strip()
        right = str(item.get("right", "")).strip()
        if not wrong or not right:
            raise TerminologyError(
                f"known_confusions entry #{position} needs both 'wrong' and 'right'."
            )
        if wrong.lower() == right.lower():
            raise TerminologyError(
                f"known_confusions entry #{position} has identical 'wrong' and 'right'."
            )
        confusions.append(
            KnownConfusion(wrong=wrong, right=right, note=str(item.get("note", "")).strip())
        )
    return tuple(confusions)


def _parse_anchors(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Read the optional ``confusable_extra_anchors`` list from the resource.

    Each entry is a two-word phrase. Both words are normalised through the
    same comparison key used for matching, so ``"Bear Bar"`` and ``"bear bar"``
    behave identically.
    """

    raw = payload.get("confusable_extra_anchors") or []
    if not isinstance(raw, list):
        raise TerminologyError("'confusable_extra_anchors' must be a list of strings.")

    anchors: list[tuple[str, str]] = []
    for position, item in enumerate(raw):
        if not isinstance(item, str):
            raise TerminologyError(f"confusable_extra_anchors entry #{position} is not a string.")
        words = comparison_key(item).split()
        if len(words) != 2:
            raise TerminologyError(
                f"confusable_extra_anchors entry #{position} ('{item}') must contain "
                "exactly two words, because an anchor is an exact match on the "
                "neighbouring word."
            )
        anchors.append((words[0], words[1]))
    return tuple(anchors)


def reset_terminology_cache() -> None:
    """Clear the module-level cache (used by tests)."""

    load_terminology.cache_clear()
