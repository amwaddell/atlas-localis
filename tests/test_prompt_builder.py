"""Retained prompt builder coverage, independent of retired graph workflows."""

from workbench.agents.state import OpenedChunk


class TestPromptBuilderBuild:
    """Verify PromptBuilder.build() works with dict and Pydantic inputs."""

    def test_build_with_dict_chunks(self):
        """build() accepts plain dicts for opened_chunks."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [
            {"chunk_id": "c1", "document_id": "d1", "title": "T1", "text": "Hello"},
        ]
        messages, meta = pb.build(
            "answer_with_citations",
            question="What?",
            opened_chunks=chunks,
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "What?" in messages[0]["content"]
        assert "c1" in messages[0]["content"]

    def test_build_with_pydantic_chunks(self):
        """build() accepts OpenedChunk Pydantic objects."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [
            OpenedChunk(chunk_id="c1", document_id="d1", title="T1", text="Hello"),
        ]
        messages, meta = pb.build(
            "answer_with_citations",
            question="What?",
            opened_chunks=chunks,
        )
        assert len(messages) == 1
        assert "Hello" in messages[0]["content"]

    def test_build_meta_has_variant(self):
        """meta should include the variant name."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [{"chunk_id": "c1", "document_id": "d1", "title": "T", "text": "x"}]
        _, meta = pb.build(
            "answer_with_citations",
            question="Q",
            opened_chunks=chunks,
        )
        assert meta["variant"] == "answer_with_citations"
        assert meta["template"] == "answer_with_citations.txt"
        assert meta["version"].startswith("v-")

    def test_build_meta_has_counts(self):
        """meta should include evidence_count and timeline_count."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [
            {"chunk_id": "c1", "document_id": "d1", "title": "T", "text": "x"},
            {"chunk_id": "c2", "document_id": "d2", "title": "T", "text": "y"},
        ]
        _, meta = pb.build(
            "answer_with_citations",
            question="Q",
            opened_chunks=chunks,
        )
        assert meta["evidence_count"] == 2
        assert meta["timeline_count"] == 0

    def test_build_with_timeline(self):
        """build() includes timeline section when timeline is provided."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [{"chunk_id": "c1", "document_id": "d1", "title": "T", "text": "x"}]
        timeline = [
            {"date": "27 BC", "event": "Republic ended", "supporting_chunk_ids": ["c1"]}
        ]
        messages, meta = pb.build(
            "answer_with_citations",
            question="Q",
            opened_chunks=chunks,
            timeline=timeline,
        )
        assert "TIMELINE" in messages[0]["content"]
        assert "27 BC" in messages[0]["content"]
        assert meta["timeline_count"] == 1

    def test_build_with_extra_instructions(self):
        """extra_instructions should be prepended to the prompt."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        chunks = [{"chunk_id": "c1", "document_id": "d1", "title": "T", "text": "x"}]
        messages, _ = pb.build(
            "answer_with_citations",
            question="Q",
            opened_chunks=chunks,
            extra_instructions="BE CONCISE.",
        )
        # extra_instructions should appear before the template content
        content = messages[0]["content"]
        assert content.startswith("BE CONCISE.")

    def test_build_does_not_break_render(self):
        """The original render() API should still work unchanged."""
        from workbench.prompting.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        result = pb.render(
            "answer_with_citations",
            question="Q",
            evidence="E",
            timeline_section="",
        )
        assert "Q" in result
        assert "E" in result
