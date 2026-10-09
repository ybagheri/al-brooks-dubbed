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

logger = logging.getLogger(__name__)

RESOURCE_PATH: Final[Path] = Path(__file__).resolve().parent / "resources" / "terminology.json"

SUPPORTED_SCHEMA_VERSION: Final[int] = 1

#: Terms whose plain English meaning is common outside trading. Matches are
#: reported at a lower confidence so downstream stages do not over-trust them.
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


class Terminology:
    """Lookup and annotation helper over the terminology resource."""

    def __init__(self, entries: list[TerminologyEntry]) -> None:
        self._entries = tuple(entries)
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
    return Terminology(_parse_entries(payload))


def reset_terminology_cache() -> None:
    """Clear the module-level cache (used by tests)."""

    load_terminology.cache_clear()
