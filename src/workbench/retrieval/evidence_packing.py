"""Exact task-local evidence packing and complete model-request budgeting."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from workbench.core.types import Chunk
from workbench.graphs.constrained_evidence import findings_schema
from workbench.graphs.research_models import (
    EvidenceRecord,
    Findings,
    WorkerInput,
    excerpt,
)
from workbench.models.structured_json import json_instruction

# Actual GGUF token counts in canonical runs; conservative byte bounds in
# model-free callers. Keep explicit reserves for chat framing and repair.
MARGIN = 128
REPAIR_RESERVE = 512
_TEMPLATE = (
    Path(__file__).parents[1] / "prompting/templates/derive_findings_v1.txt"
).read_text()


_COUNTER = ContextVar("local_token_counter", default=None)


@contextmanager
def tokenizer_budget(counter):
    """Run-scoped actual tokenizer with a bounded cache; fixtures retain byte bounds."""
    cached = lru_cache(maxsize=256)(counter)
    token = _COUNTER.set(cached)
    try:
        yield
    finally:
        _COUNTER.reset(token)
        cached.cache_clear()


def token_bound(text: str) -> int:
    counter = _COUNTER.get()
    return counter(text) if counter is not None else len(text.encode("utf-8"))


def findings_messages(
    value: WorkerInput,
    evidence: tuple[EvidenceRecord, ...],
    errors: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    data = {
        "question": value.task.question,
        "needs_timeline": value.task.needs_timeline,
        "evidence": [
            {"alias": f"E{i}", "title": e.title, "section": e.section, "text": e.text}
            for i, e in enumerate(evidence, 1)
        ],
    }
    content = _TEMPLATE.replace("{input_json}", json.dumps(data, ensure_ascii=False))
    if errors:
        # Bounded feedback, never reintroduce unvalidated prose as evidence.
        prefix = "\nRepair invalid findings against the SAME evidence. For this repair, copy each statement verbatim as one plain contiguous span from ONE cited excerpt. Do not paraphrase, join spans or add details. Return fewer findings or empty findings with a gap when needed. Errors: "
        feedback = "; ".join(errors).encode("utf-8")
        content += prefix + feedback[
            : REPAIR_RESERVE - len(prefix.encode("utf-8"))
        ].decode("utf-8", errors="ignore")
    return [{"role": "user", "content": content}]


def request_bound(messages: list[dict[str, str]], evidence=()) -> int:
    # Include helper-injected schema, roles, framing and chat-template margin.
    return (
        token_bound(
            json_instruction(Findings, generation_schema=findings_schema(evidence))
        )
        + sum(token_bound(m["role"]) + token_bound(m["content"]) + 16 for m in messages)
        + MARGIN
    )


def pack_evidence(
    value: WorkerInput, chunks: tuple[Chunk, ...]
) -> tuple[EvidenceRecord, ...]:
    selected: tuple[EvidenceRecord, ...] = ()
    seen: set[str] = set()
    docs: dict[str, int] = {}
    for c in chunks:
        if len(selected) >= value.limits.max_evidence:
            break
        if c.chunk_id in seen or not c.text.strip():
            continue
        seen.add(c.chunk_id)
        if docs.get(c.document_id, 0) >= value.limits.max_per_document:
            continue
        # Whole exact chunks only. Skipping an oversized chunk preserves source
        # wording and avoids falsely presenting a truncated event as complete.
        if len(c.text) > 16000:
            continue
        record = excerpt(
            snapshot_id=value.snapshot_id,
            chunk_id=c.chunk_id,
            full_text=c.text,
            start=0,
            end=len(c.text),
            task_id=value.task.task_id,
            document_id=c.document_id,
            title=c.title,
            section=c.section,
            source_url=c.metadata.get("url", ""),
        )
        candidate = (*selected, record)
        if (
            request_bound(findings_messages(value, candidate), candidate)
            + value.limits.output_tokens
            + REPAIR_RESERVE
            > value.limits.prompt_tokens
        ):
            continue
        selected = candidate
        docs[c.document_id] = docs.get(c.document_id, 0) + 1
    return selected
