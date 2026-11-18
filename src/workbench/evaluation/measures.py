"""Mechanical diagnostics over the canonical result, never accuracy scores."""

from workbench.graphs.research_models import ApplicationResult
from workbench.systems.presentation import project_result


def compute_all_measures(result: ApplicationResult) -> dict:
    view = project_result(result)
    titles = sorted({e["title"] for c in view["citations"] for e in c["excerpts"]})
    return {
        "citation_count": len(result.citations),
        "citations": [c.chunk_id for c in result.citations],
        "unique_cited_titles": len(titles),
        "title_list": titles,
        "retrieved_count": result.diagnostics.retrieved,
        "opened_count": result.diagnostics.opened,
        "presented_count": result.diagnostics.presented,
        "total_latency_ms": result.diagnostics.elapsed_ms,
        "tokens_in": result.diagnostics.tokens_in,
        "tokens_out": result.diagnostics.tokens_out,
        "outcome": result.outcome,
        "delivery": result.delivery,
        "final_validation": result.final_validation.status,
        "tasks": len(result.coverage),
        "failed_tasks": sum(c.status == "failed" for c in result.coverage),
        "warnings": [w.code for w in result.warnings],
    }
