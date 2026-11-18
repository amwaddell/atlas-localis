"""Pure shared citation/gap projection. Never numberize or mutate a result."""

from workbench.graphs.research_models import ApplicationResult


def project_result(result: ApplicationResult) -> dict:
    return {
        "answer_text": result.answer_text,
        "outcome": result.outcome,
        "delivery": result.delivery,
        "gaps": result.gaps,
        "coverage": tuple(c.model_dump() for c in result.coverage),
        "citations": tuple(
            {
                "number": c.number,
                "snapshot_id": c.snapshot_id,
                "chunk_id": c.chunk_id,
                "excerpts": tuple(
                    {
                        "title": e.title,
                        "section": e.section,
                        "text": e.text,
                        "start": e.key.start,
                        "end": e.key.end,
                    }
                    for e in c.excerpts
                ),
            }
            for c in result.citations
        ),
        "run_id": result.run_id,
    }
