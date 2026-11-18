"""Retained conversation formatting coverage; canonical chat runs are tested at the application boundary."""


class TestConversationContextFormatting:
    """Test the format_conversation_context helper."""

    def test_empty_history_returns_empty(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        result = format_conversation_context()
        assert result == ""

    def test_empty_list_returns_empty(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        result = format_conversation_context(chat_history=[])
        assert result == ""

    def test_history_only(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        history = [
            {"role": "user", "content": "Tell me about WW2"},
            {"role": "assistant", "content": "WW2 was a global conflict."},
        ]
        result = format_conversation_context(chat_history=history)
        assert "CONVERSATION CONTEXT" in result
        assert "Tell me about WW2" in result
        assert "WW2 was a global conflict" in result

    def test_summary_only(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        result = format_conversation_context(
            conversation_summary="User asked about WW2 and the Eastern Front."
        )
        assert "Summary of earlier discussion" in result
        assert "Eastern Front" in result

    def test_summary_plus_history(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        history = [{"role": "user", "content": "What about Stalingrad?"}]
        result = format_conversation_context(
            chat_history=history,
            conversation_summary="Discussed WW2 overview.",
        )
        assert "Summary of earlier discussion" in result
        assert "Stalingrad" in result

    def test_truncates_long_assistant_responses(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        history = [
            {"role": "assistant", "content": "x" * 1000},
        ]
        result = format_conversation_context(chat_history=history)
        # Should be truncated to ~500 chars + ellipsis
        assert "…" in result

    def test_max_turns_respected(self):
        from workbench.prompting.prompt_builder import format_conversation_context

        # Create 20 turns
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(20)
        ]
        result = format_conversation_context(chat_history=history, max_turns=4)
        # Should only contain the last 4 messages
        assert "msg 16" in result
        assert "msg 17" in result
        assert "msg 18" in result
        assert "msg 19" in result
        assert "msg 0" not in result


class TestPromptBuilderChatExtensions:
    """Test the updated PromptBuilder.build() with chat params."""

    def _make_builder(self, tmp_path):
        """Create a PromptBuilder with test templates."""
        from workbench.prompting.prompt_builder import PromptBuilder

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Write a chat-aware template
        (templates_dir / "chat_answer_with_citations.txt").write_text(
            "{conversation_context}\n"
            "{timeline_section}\n"
            "--- EVIDENCE ---\n{evidence}\n"
            "--- QUESTION ---\n{question}\n"
        )

        # Write a standard template
        (templates_dir / "answer_with_citations.txt").write_text(
            "{timeline_section}\n"
            "--- EVIDENCE ---\n{evidence}\n"
            "--- QUESTION ---\n{question}\n"
        )

        return PromptBuilder(templates_dir=templates_dir)

    def test_build_with_chat_history(self, tmp_path):
        pb = self._make_builder(tmp_path)
        messages, meta = pb.build(
            "chat_answer_with_citations",
            question="What was that battle?",
            opened_chunks=[
                {
                    "chunk_id": "c1",
                    "title": "Stalingrad",
                    "section": "Overview",
                    "text": "A major battle.",
                }
            ],
            chat_history=[
                {"role": "user", "content": "Tell me about WW2"},
            ],
            conversation_summary="Discussed WW2.",
        )
        assert meta["chat_turns"] == 1
        assert meta["has_summary"] is True
        assert "Tell me about WW2" in messages[0]["content"]
        assert "Discussed WW2" in messages[0]["content"]

    def test_build_without_chat_history(self, tmp_path):
        pb = self._make_builder(tmp_path)
        messages, meta = pb.build(
            "answer_with_citations",
            question="When did Rome fall?",
            opened_chunks=[
                {
                    "chunk_id": "c1",
                    "title": "Rome",
                    "section": "",
                    "text": "Rome fell in 476 AD.",
                }
            ],
        )
        assert meta["chat_turns"] == 0
        assert meta["has_summary"] is False

    def test_build_meta_includes_chat_fields(self, tmp_path):
        pb = self._make_builder(tmp_path)
        _, meta = pb.build(
            "chat_answer_with_citations",
            question="test",
            opened_chunks=[
                {
                    "chunk_id": "c1",
                    "title": "T",
                    "section": "",
                    "text": "t",
                }
            ],
            chat_history=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        )
        assert meta["chat_turns"] == 3
        assert "has_summary" in meta
