"""Retained ingestion and obsolete-loop absence checks."""

import pytest


class TestIngestCleanup:
    """Verify WikiExtractor functions are removed and primary path exists."""

    def test_primary_path_exists(self):
        from workbench.data_build.ingest_wikipedia import ingest_wikipedia_dump

        assert callable(ingest_wikipedia_dump)

    def test_iter_articles_exists(self):
        from workbench.data_build.ingest_wikipedia import iter_articles_from_dump

        assert callable(iter_articles_from_dump)

    def test_strip_wiki_markup_exists(self):
        from workbench.data_build.ingest_wikipedia import strip_wiki_markup

        assert callable(strip_wiki_markup)

    def test_wikiextractor_functions_removed(self):
        import workbench.data_build.ingest_wikipedia as mod

        assert not hasattr(mod, "parse_extracted_file"), (
            "parse_extracted_file should be removed"
        )
        assert not hasattr(mod, "iter_extracted_dir"), (
            "iter_extracted_dir should be removed"
        )
        assert not hasattr(mod, "ingest_wikipedia"), (
            "ingest_wikipedia (legacy) should be removed"
        )


class TestLegacyRemoved:
    """Verify legacy orchestration code is deleted."""

    def test_no_legacy_package(self):
        """workbench.legacy should not be importable."""
        with pytest.raises(ImportError):
            import workbench.legacy  # noqa: F401

    def test_no_agents_loops_module(self):
        """workbench.agents.loops should not be importable."""
        with pytest.raises(ImportError):
            from workbench.agents.loops import AgentLoop  # noqa: F401

    def test_no_agents_policies_module(self):
        """workbench.agents.policies should not be importable."""
        with pytest.raises(ImportError):
            from workbench.agents.policies import ScriptedPolicy  # noqa: F401

    def test_no_chatbot_system(self):
        """workbench.systems.chatbot should not be importable."""
        with pytest.raises(ImportError):
            from workbench.systems.chatbot import ChatbotSystem  # noqa: F401

    def test_agents_init_exports_state_only(self):
        """agents.__init__ exports state types, not legacy loop/policies."""
        import workbench.agents as agents_pkg

        # State types should be present
        assert not hasattr(agents_pkg, "AgentState")
        assert hasattr(agents_pkg, "OpenedChunk")
        assert hasattr(agents_pkg, "RetrievedChunkSummary")
        assert hasattr(agents_pkg, "TimelineItem")

        # Legacy should NOT be present
        assert not hasattr(agents_pkg, "AgentLoop")
        assert not hasattr(agents_pkg, "ScriptedPolicy")
        assert not hasattr(agents_pkg, "ResearcherPolicy")
