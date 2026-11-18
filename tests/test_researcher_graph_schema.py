"""Legacy citation policies remain testable during consumer migration."""

import pytest

from workbench.prompting.citations import (
    RAG_DEFAULT,
    RESEARCHER_STRICT,
    CitationPolicy,
    ValidationResult,
    extract_citations,
    find_all_bracket_ids,
    validate_citations,
)


class TestCitationExtraction:
    """Verify the shared extract_citations function."""

    def test_finds_known_ids(self):
        text = "Rome fell [chunk_001] and then [chunk_002]."
        known = ["chunk_001", "chunk_002", "chunk_003"]
        assert extract_citations(text, known) == ["chunk_001", "chunk_002"]

    def test_ignores_unknown_ids(self):
        text = "Rome fell [chunk_unknown]."
        known = ["chunk_001", "chunk_002"]
        assert extract_citations(text, known) == []

    def test_empty_text(self):
        assert extract_citations("", ["chunk_001"]) == []

    def test_empty_known(self):
        assert extract_citations("has chunk_001 in it", []) == []

    def test_no_duplicates(self):
        text = "[chunk_001] and again [chunk_001]."
        result = extract_citations(text, ["chunk_001"])
        assert result == ["chunk_001"]


class TestFindAllBracketIds:
    """Verify find_all_bracket_ids picks up all IDs (valid or not)."""

    def test_finds_multiple_ids(self):
        text = "Rome [chunk_001] and [chunk_002] fell."
        assert find_all_bracket_ids(text) == ["chunk_001", "chunk_002"]

    def test_finds_unknown_ids(self):
        text = "Rome [fake_id] fell."
        assert find_all_bracket_ids(text) == ["fake_id"]

    def test_no_duplicates(self):
        text = "[a] [b] [a]"
        assert find_all_bracket_ids(text) == ["a", "b"]

    def test_empty_text(self):
        assert find_all_bracket_ids("") == []

    def test_no_brackets(self):
        assert find_all_bracket_ids("no brackets here") == []


# ------------------------------------------------------------------ #
#  Test: citation validation                                          #
# ------------------------------------------------------------------ #


class TestCitationValidation:
    """Verify validate_citations with different policies."""

    def test_strict_passes_with_good_answer(self):
        text = "Rome fell [chunk_001] in 27 BC [chunk_002]."
        vr = validate_citations(text, ["chunk_001", "chunk_002"], RESEARCHER_STRICT)
        assert vr.valid is True
        assert vr.errors == []

    def test_strict_fails_zero_citations(self):
        text = "Rome fell in 27 BC."
        vr = validate_citations(text, ["chunk_001", "chunk_002"], RESEARCHER_STRICT)
        assert vr.valid is False
        assert any("Too few" in e for e in vr.errors)

    def test_strict_fails_hallucinated_id(self):
        text = "Rome fell [chunk_fake] [chunk_001]."
        vr = validate_citations(text, ["chunk_001", "chunk_002"], RESEARCHER_STRICT)
        assert vr.valid is False
        assert any("Hallucinated" in e for e in vr.errors)
        assert "chunk_fake" in vr.hallucinated_ids

    def test_strict_fails_low_density(self):
        text = (
            "Rome fell in 27 BC.\n\n"
            "This is a second paragraph.\n\n"
            "Third paragraph.\n\n"
            "Only this one cites [chunk_001]."
        )
        vr = validate_citations(text, ["chunk_001", "chunk_002"], RESEARCHER_STRICT)
        # 1 out of 4 paragraphs = 0.25 density, below 0.5
        assert vr.valid is False
        assert any("density" in e for e in vr.errors)

    def test_strict_fails_timeline_empty_chunk_ids(self):
        timeline = [
            {"date": "27 BC", "event": "Republic ends", "supporting_chunk_ids": []},
        ]
        text = "Rome fell [chunk_001]."
        vr = validate_citations(
            text,
            ["chunk_001"],
            RESEARCHER_STRICT,
            timeline=timeline,
        )
        assert vr.valid is False
        assert any("Timeline item 0" in e for e in vr.errors)

    def test_strict_fails_timeline_unknown_chunk_ids(self):
        timeline = [
            {
                "date": "27 BC",
                "event": "Republic ends",
                "supporting_chunk_ids": ["chunk_fake_999"],
            },
        ]
        text = "Rome fell [chunk_001]."
        vr = validate_citations(
            text,
            ["chunk_001"],
            RESEARCHER_STRICT,
            timeline=timeline,
        )
        assert vr.valid is False
        assert any("unknown chunks" in e for e in vr.errors)

    def test_strict_warns_unused_chunks(self):
        text = "Rome fell [chunk_001]."
        vr = validate_citations(text, ["chunk_001", "chunk_002"], RESEARCHER_STRICT)
        # chunk_002 is unused → warning (not error)
        assert "chunk_002" in vr.uncited_opened_ids
        assert any("not cited" in w for w in vr.warnings)

    def test_rag_default_accepts_no_citations(self):
        text = "Rome fell in 27 BC."
        vr = validate_citations(text, ["chunk_001"], RAG_DEFAULT)
        assert vr.valid is True

    def test_validation_result_to_dict(self):
        vr = ValidationResult(valid=True, cited_ids=["c1"])
        d = vr.to_dict()
        assert d["valid"] is True
        assert d["cited_ids"] == ["c1"]
        assert isinstance(d["errors"], list)

    def test_custom_policy_min_citations_2(self):
        policy = CitationPolicy(min_citations=2, max_hallucinated=0)
        text = "Rome fell [chunk_001]."
        vr = validate_citations(text, ["chunk_001", "chunk_002"], policy)
        assert vr.valid is False
        assert any("Too few" in e for e in vr.errors)


class TestPolicyPresets:
    """Verify the policy presets have sensible defaults."""

    def test_rag_default_is_lenient(self):
        assert RAG_DEFAULT.min_citations == 0
        assert RAG_DEFAULT.max_repair_attempts == 0
        assert RAG_DEFAULT.require_timeline_chunk_ids is False

    def test_researcher_strict_has_requirements(self):
        assert RESEARCHER_STRICT.min_citations >= 1
        assert RESEARCHER_STRICT.max_repair_attempts >= 1
        assert RESEARCHER_STRICT.require_timeline_chunk_ids is True
        assert RESEARCHER_STRICT.min_density > 0
        assert RESEARCHER_STRICT.warn_on_unused_chunks is True

    def test_policies_are_frozen(self):
        """Policies should be immutable."""
        with pytest.raises(AttributeError):
            RAG_DEFAULT.min_citations = 5  # type: ignore[misc]
