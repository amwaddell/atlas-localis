#!/usr/bin/env python3
"""Run one local research question through the shared application."""

import argparse

from workbench.graphs.planning import conversation_input
from workbench.systems.presentation import project_result
from workbench.systems.runner import ResourceBusy, create_application


def run_question(application, question, *, history=(), summary=""):
    return application.invoke(
        conversation_input(question=question, history=history, summary=summary)
    )


def main():
    parser = argparse.ArgumentParser(description="Local historical research")
    parser.add_argument("question", nargs="+")
    args = parser.parse_args()
    try:
        with create_application() as app:
            result = run_question(app, " ".join(args.question))
    except ResourceBusy as exc:
        parser.exit(1, str(exc) + "\n")
    view = project_result(result)
    print(view["answer_text"])
    for gap in view["gaps"]:
        print(f"Evidence gap: {gap}")
    for citation in view["citations"]:
        for excerpt in citation["excerpts"]:
            print(f"[{citation['number']}] {excerpt['title']}: {excerpt['text']}")
    print(f"{view['outcome']} · Run {view['run_id']}")


if __name__ == "__main__":
    main()
