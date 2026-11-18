#!/usr/bin/env python3
"""Offline preflight through the canonical factory; never provisions assets."""

import argparse
from pathlib import Path

from workbench.graphs.research_models import ConversationInput
from workbench.systems.runner import Resources, create_application


def check_application_compiles():
    class Retriever:
        def retrieve_hydrated(self, query, top_k):
            from workbench.retrieval.hybrid import HydratedRetrieval

            return HydratedRetrieval((), 0, 0)

    class Model:
        def generate(self, messages, **settings):
            from workbench.core.types import ModelResponse

            return ModelResponse(
                text='{"tasks":[{"label":"Research","question":"Rome?","needs_timeline":false}]}',
                tokens_in=1,
                tokens_out=1,
                latency_ms=0,
            )

    with create_application(
        resources=Resources(Model(), Retriever(), "preflight-fixture")
    ) as app:
        assert (
            app.invoke(ConversationInput(question="Rome?")).outcome
            == "insufficient_evidence"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Canonical local application preflight"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--quick", action="store_true", help="Model-free application check only"
    )
    args = parser.parse_args()
    try:
        check_application_compiles()
        if not args.quick:
            with create_application(config_path=args.config) as app:
                result = app.invoke(
                    ConversationInput(question="When did the Roman Republic end?")
                )
                print(result.outcome, app.resources.snapshot_id)
                if result.outcome in {"failed", "cancelled"}:
                    raise RuntimeError(
                        result.error.code if result.error else result.outcome
                    )
        print("Application preflight passed.")
    except Exception as exc:
        parser.exit(1, f"Preflight failed: {exc}\n")


if __name__ == "__main__":
    main()
