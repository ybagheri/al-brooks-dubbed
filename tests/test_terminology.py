"""Tests for :mod:`src.terminology` (Phase 2 domain vocabulary)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.errors import TerminologyError
from src.terminology import (
    RESOURCE_PATH,
    Terminology,
    TerminologyEntry,
    _parse_entries,
    load_terminology,
    reset_terminology_cache,
)


# ----------------------------------------------------------------------
# The bundled resource
# ----------------------------------------------------------------------
def test_resource_exists() -> None:
    assert RESOURCE_PATH.is_file()


def test_resource_is_valid_json_with_supported_schema() -> None:
    payload = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert isinstance(payload["terms"], list)


def test_brief_terms_are_present() -> None:
    """Every term named in the project brief must exist."""

    terms = load_terminology()
    expected = {
        "wedge_bull_flag",
        "bull_flag",
        "bear_flag",
        "trading_range",
        "measured_move",
        "price_action",
        "gap",
        "breakout",
        "pullback",
        "reversal",
    }
    found = {entry.id for entry in terms.entries}
    assert expected <= found


def test_brief_terms_are_spoken_in_english() -> None:
    """The Persian track keeps these terms in English, by the trader's choice.

    Persian traders say "wedge bull flag" rather than a literal Persian
    rendering, so the spoken form is the English term and ``speak_original``
    records that it is deliberate.
    """

    expected = {
        "wedge_bull_flag": "wedge bull flag",
        "bull_flag": "bull flag",
        "bear_flag": "bear flag",
        "trading_range": "trading range",
        "measured_move": "measured move",
        "price_action": "price action",
        "gap": "gap",
        "breakout": "breakout",
        "pullback": "pullback",
        "reversal": "reversal",
    }
    terms = load_terminology()
    for term_id, spoken in expected.items():
        entry = terms.get(term_id)
        assert entry is not None, f"{term_id} missing from the glossary"
        assert entry.spoken_as == spoken, f"{term_id} is spoken as {entry.spoken_as!r}"
        assert entry.is_untranslated is True, f"{term_id} is not marked speak_original"
        assert entry.spoken_as == entry.canonical, f"{term_id} should speak the original"


def test_translation_count_is_reported() -> None:
    summary = load_terminology().summary()
    assert summary["translated_terms"] >= 10
    assert summary["kept_in_english"] >= 10
    assert summary["term_count"] > summary["translated_terms"]


def test_untranslated_terms_have_a_decision_flag() -> None:
    """Every spoken-as value must be paired with an explicit keep-English flag.

    Without the flag, an English value sitting in a field called "persian"
    looks like a typo to the next person who reads it.
    """

    for entry in load_terminology().entries:
        if entry.persian and entry.persian == entry.canonical:
            assert entry.speak_original is True, entry.id


def test_speak_original_defaults_to_false() -> None:
    entries = _parse_entries({"schema_version": 1, "terms": [_entry(id="x", canonical="x")]})
    assert entries[0].speak_original is False
    assert entries[0].is_untranslated is False


def test_speak_original_must_be_boolean() -> None:
    with pytest.raises(TerminologyError, match="non-boolean 'speak_original'"):
        _parse_entries(
            {
                "schema_version": 1,
                "terms": [_entry(id="x", canonical="x", speak_original="yes")],
            }
        )


def test_no_duplicate_ids_or_canonicals() -> None:
    entries = load_terminology().entries
    ids = [entry.id for entry in entries]
    canonicals = [entry.canonical.lower() for entry in entries]
    assert len(set(ids)) == len(ids)
    assert len(set(canonicals)) == len(canonicals)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------
def test_detects_multi_word_term() -> None:
    terms = load_terminology()
    assert "wedge_bull_flag" in terms.term_ids_in("This is a wedge bull flag setup.")


def test_detects_plural_alias() -> None:
    terms = load_terminology()
    assert "bull_flag" in terms.term_ids_in("We saw two bull flags today")


def test_matching_is_case_insensitive() -> None:
    terms = load_terminology()
    assert "pullback" in terms.term_ids_in("A PULLBACK formed")
    assert "pullback" in terms.term_ids_in("a pullback formed")


def test_longest_match_wins() -> None:
    """ "wedge bull flag" must not also report "wedge" and "bull flag"."""

    terms = load_terminology()
    matches = terms.find("a wedge bull flag appears")
    ids = [m.term_id for m in matches]
    assert "wedge_bull_flag" in ids
    assert "bull_flag" not in ids


def test_distinct_occurrences_both_reported() -> None:
    terms = load_terminology()
    matches = terms.find("the bulls see a wedge bull flag")
    ids = [m.term_id for m in matches]
    assert "bull" in ids  # "bulls" plural alias
    assert "wedge_bull_flag" in ids


def test_whole_word_matching_only() -> None:
    """'gapweed' must not match 'gap'."""

    terms = load_terminology()
    assert terms.find("gapweed") == []
    assert terms.find("agap") == []


def test_offsets_are_reported() -> None:
    terms = load_terminology()
    text = "I see a gap here"
    match = [m for m in terms.find(text) if m.term_id == "gap"][0]
    assert text[match.start_char : match.end_char].lower() == "gap"


def test_matches_do_not_overlap() -> None:
    terms = load_terminology()
    matches = terms.find("a wedge bull flag with a gap")
    for first, second in zip(matches, matches[1:], strict=False):
        assert first.end_char <= second.start_char


def test_empty_text_has_no_matches() -> None:
    assert load_terminology().find("") == []
    assert load_terminology().term_ids_in("") == []


def test_common_non_trading_text_finds_little(tmp_path: Path) -> None:
    terms = load_terminology()
    found = terms.term_ids_in("Hello everyone, thanks for coming back this afternoon.")
    assert found == []


def test_ambiguous_terms_are_marked() -> None:
    terms = load_terminology()
    gap = [m for m in terms.find("there is a gap") if m.term_id == "gap"][0]
    assert gap.ambiguous is True

    wedge = [m for m in terms.find("a wedge bull flag") if m.term_id == "wedge_bull_flag"][0]
    assert wedge.ambiguous is False


def test_match_serialises() -> None:
    terms = load_terminology()
    payload = [m.to_dict() for m in terms.find("a breakout")][0]
    assert set(payload) == {
        "term_id",
        "canonical",
        "category",
        "matched_text",
        "ambiguous",
    }


def test_get_by_id() -> None:
    terms = load_terminology()
    assert terms.get("gap") is not None
    assert terms.get("does_not_exist") is None


def test_summary_shape() -> None:
    summary = load_terminology().summary()
    assert summary["term_count"] > 20
    assert isinstance(summary["categories"], list)


# ----------------------------------------------------------------------
# Console encoding - Persian must survive the logging/print path
# ----------------------------------------------------------------------
def test_console_reconfiguration_is_safe() -> None:
    """Reconfiguring the streams must never raise, even under pytest capture."""

    from src.logging_utils import enable_utf8_console

    assert isinstance(enable_utf8_console(), bool)


def test_persian_survives_a_log_file(tmp_path: Path) -> None:
    """A spoken-as value written through the writer must be readable as UTF-8."""

    from src.outputs import OutputWriter

    entry = load_terminology().get("wedge_bull_flag")
    assert entry is not None and entry.spoken_as
    path = OutputWriter(tmp_path).write_transcript_text(
        tmp_path / "term.txt", f"{entry.canonical} = {entry.spoken_as}"
    )
    assert entry.spoken_as in path.read_text(encoding="utf-8")


def test_len() -> None:
    assert len(load_terminology()) == len(load_terminology().entries)


# ----------------------------------------------------------------------
# Parsing / validation
# ----------------------------------------------------------------------
def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "x",
        "canonical": "x",
        "category": "core",
        "aliases": [],
        "notes": "",
        "persian": None,
    }
    base.update(overrides)
    return base


def test_parse_entries_ok() -> None:
    entries = _parse_entries({"schema_version": 1, "terms": [_entry(id="gap", canonical="gap")]})
    assert entries[0].id == "gap"
    assert isinstance(entries[0], TerminologyEntry)


def test_unsupported_schema_version_rejected() -> None:
    with pytest.raises(TerminologyError, match="schema_version"):
        _parse_entries({"schema_version": 99, "terms": [_entry()]})


def test_missing_terms_rejected() -> None:
    with pytest.raises(TerminologyError, match="no 'terms' list"):
        _parse_entries({"schema_version": 1, "terms": []})


def test_non_object_entry_rejected() -> None:
    with pytest.raises(TerminologyError, match="not an object"):
        _parse_entries({"schema_version": 1, "terms": ["oops"]})


def test_missing_id_rejected() -> None:
    with pytest.raises(TerminologyError, match="'id' and 'canonical'"):
        _parse_entries({"schema_version": 1, "terms": [_entry(id="", canonical="")]})


def test_duplicate_id_rejected() -> None:
    with pytest.raises(TerminologyError, match="Duplicate terminology id"):
        _parse_entries({"schema_version": 1, "terms": [_entry(id="gap"), _entry(id="gap")]})


def test_non_list_aliases_rejected() -> None:
    with pytest.raises(TerminologyError, match="non-list 'aliases'"):
        _parse_entries({"schema_version": 1, "terms": [_entry(aliases="nope")]})


def test_persian_value_is_parsed_when_present() -> None:
    entries = _parse_entries({"schema_version": 1, "terms": [_entry(id="gap", persian="شکاف")]})
    assert entries[0].persian == "شکاف"


def test_custom_terminology_object() -> None:
    """A Terminology instance can be built directly for testing."""

    terms = Terminology([TerminologyEntry(id="t1", canonical="hello world", category="core")])
    assert terms.term_ids_in("say hello world now") == ["t1"]
    assert terms.find("nothing here") == []


def test_cache_can_be_cleared() -> None:
    load_terminology()
    reset_terminology_cache()
    assert load_terminology() is not None


def test_empty_alias_entries_are_ignored() -> None:
    """Blank aliases must not create broken matchers."""

    terms = Terminology(
        [TerminologyEntry(id="t", canonical="x", category="core", aliases=("", "  "))]
    )
    matches = terms.find("x marks")
    assert len(matches) == 1
    assert matches[0].term_id == "t"
