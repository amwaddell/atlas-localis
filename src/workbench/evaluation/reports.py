"""Reports preserve canonical evidence, coverage, validation and fingerprints."""

import json
from pathlib import Path
from typing import Any

from workbench.graphs.research_models import ApplicationResult
from workbench.systems.presentation import project_result


def generate_report(
    result: ApplicationResult, metrics: dict, run_id=None, question_meta=None
) -> str:
    view = project_result(result)
    lines = [
        f"# Research report: {run_id or result.run_id}",
        "",
        "## Question",
        (question_meta or {}).get("question", "See recorded application input."),
        json.dumps(question_meta or {}, ensure_ascii=False),
        "",
        "## Answer",
        view["answer_text"],
        "",
        f"Outcome: {result.outcome} · {result.delivery}",
        "",
        "## Citations",
    ]
    for citation in view["citations"]:
        lines.append(
            f"[{citation['number']}] `{citation['chunk_id']}` · snapshot `{citation['snapshot_id']}`"
        )
        for excerpt in citation["excerpts"]:
            lines.extend([excerpt["title"], excerpt["text"]])
    if not result.citations:
        lines.append("No cited evidence.")
    lines.extend(
        [
            "",
            "## Coverage and gaps",
            json.dumps(view["coverage"], ensure_ascii=False),
            *result.gaps,
            "",
            "## Validation",
            result.final_validation.status,
            "",
            "## Metrics",
            json.dumps(metrics, ensure_ascii=False),
            "",
            "## Fingerprints",
            json.dumps([f.model_dump() for f in result.fingerprints]),
        ]
    )
    if result.error:
        lines.extend(["", "## Error", result.error.code, result.error.message])
    return "\n".join(lines)


def generate_summary_report(results: list[dict[str, Any]], run_id="unknown") -> str:
    lines = [
        f"# Evaluation Summary: {run_id}",
        f"Questions evaluated: {len(results)}",
        "",
        "| Question | Outcome | Delivery | Citations | Error |",
        "|----------|---------|----------|-----------|-------|",
    ]
    for row in results:
        metrics = row.get("metrics", {})
        question = row.get("question", "").replace("|", "\\|")
        lines.append(
            f"| {question} | {metrics.get('outcome', 'failed')} | "
            f"{metrics.get('delivery', 'message')} | {metrics.get('citation_count', 0)} | "
            f"{'Yes' if row.get('error') else '—'} |"
        )
    return "\n".join(lines)


def save_report(markdown: str, path: Path) -> Path:
    """
    Save a Markdown report to disk.

    Args:
        markdown: Report content.
        path: Output file path.

    Returns:
        The path written to.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def _safe_avg(values: list[float | int]) -> float:
    """Average that handles empty lists."""
    if not values:
        return 0.0
    return sum(values) / len(values)
