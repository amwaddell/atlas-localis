"""Bounded contextual intent normalization and one-call independent planning."""

from __future__ import annotations

import json
import re
from pathlib import Path

from workbench.graphs.research_models import (
    ConversationInput,
    ConversationTurn,
    NormalizedIntent,
    Notice,
    PlanProposal,
    ResearchPlan,
    ResearchTask,
    RewritePayload,
    StageError,
    assign_plan,
)
from workbench.models.structured_json import generate_validated_json, json_instruction
from workbench.retrieval.evidence_packing import token_bound

_TEMPLATES = Path(__file__).parents[1] / "prompting/templates"


def conversation_input(question, history=(), summary="") -> ConversationInput:
    """Shared consumer entry: bound long transcripts before wire validation."""
    history = tuple(history or ())
    summary = summary or ""
    return bounded_conversation(
        ConversationInput(
            question=question,
            history=tuple(ConversationTurn.model_validate(t) for t in history[-100:]),
            summary=summary[:2000],
            history_truncated=len(history) > 100 or len(summary) > 2000,
        )
    )


def bounded_conversation(value: ConversationInput) -> ConversationInput:
    # Retain recent whole user-led exchanges; never retain an orphan assistant.
    budget = 6000
    summary = value.summary[:2000]
    groups = []
    for turn in value.history:
        if turn.role == "user":
            groups.append([])
        if groups:
            groups[-1].append(turn)
    selected = []
    for group in reversed(groups):
        cost = sum(len(t.content) for t in group)
        if cost + len(summary) + sum(len(t.content) for t in selected) > budget:
            break
        selected = group + selected
    return ConversationInput(
        question=value.question,
        history=tuple(selected),
        summary=summary,
        history_truncated=value.history_truncated
        or tuple(selected) != value.history
        or summary != value.summary,
    )


def messages(template, data):
    return [
        {
            "role": "user",
            "content": (_TEMPLATES / template)
            .read_text()
            .replace("{input_json}", json.dumps(data, ensure_ascii=False)),
        }
    ]


def structured(model, schema, prompt, limits, *, generation_schema=None):
    bound = (
        token_bound(json_instruction(schema, generation_schema=generation_schema))
        + 128
        + sum(token_bound(m["content"]) + token_bound(m["role"]) + 16 for m in prompt)
    )
    if bound + limits.output_tokens > limits.prompt_tokens:
        raise ValueError("request budget exceeded")
    value, response = generate_validated_json(
        model,
        prompt,
        schema,
        max_tokens=min(limits.output_tokens, 4096),
        generation_schema=generation_schema,
    )
    if (
        response.tokens_out > limits.output_tokens
        or token_bound(response.text) > limits.output_tokens
    ):
        raise ValueError("output budget exceeded")
    return value


def normalize(value, model, limits):
    if not value.history and not value.summary:
        return NormalizedIntent(status="identity", standalone_question=value.question)
    try:
        output = structured(
            model,
            RewritePayload,
            messages("normalize_intent_v1.txt", value.model_dump()),
            limits,
        )
        if ambiguous_comparison_reference(value):
            return NormalizedIntent(status="unresolved", standalone_question=None)
        return NormalizedIntent(
            status="resolved" if output.standalone_question else "unresolved",
            standalone_question=output.standalone_question,
        )
    except Exception:
        return NormalizedIntent(
            status="failed",
            standalone_question=None,
            error=StageError(
                stage="normalize_question",
                code="rewrite_failed",
                message="Contextual question normalization failed.",
            ),
        )


def ambiguous_comparison_reference(value: ConversationInput) -> bool:
    """Conservative clarification for a singular follow-up to explicit comparison.

    This covers an explicit syntactic ambiguity, not general coreference. An
    explicitly named item or a subsequent assistant focus can disambiguate it.
    No historical entity lists or case IDs are involved.
    """
    if not re.search(r"\b(?:it|its)\b", value.question, re.I):
        return False
    users = [t for t in value.history if t.role == "user"]
    if not users:
        return False
    compared = re.fullmatch(
        r"\s*compare\s+(.+?)\s+(?:and|with|versus|vs\.?)\s+(.+?)[?.!]*\s*",
        users[-1].content,
        re.I,
    )
    if not compared:
        return False
    items = [
        re.sub(r"^(?:the|a|an)\s+", "", x.strip(" .?!").casefold())
        for x in compared.groups()
    ]
    question = value.question.casefold()
    if any(item in question for item in items):
        return False
    assistant = next(
        (
            t.content.casefold()
            for t in reversed(value.history)
            if t.role == "assistant"
        ),
        "",
    )
    if sum(item in assistant for item in items) == 1:
        return False
    return True


def plan(value, intent, model, limits):
    try:
        proposal = structured(
            model,
            PlanProposal,
            messages(
                "plan_research_v1.txt",
                {
                    "original_intent": value.model_dump(),
                    "standalone_question": intent.standalone_question,
                },
            ),
            limits,
        )
        assigned = assign_plan(proposal)
        if len(assigned.tasks) == 1:
            # With no decomposition, a new question has no purpose. Keep the
            # authoritative normalized request rather than model-added scope.
            task = assigned.tasks[0]
            if task.question != intent.standalone_question:
                return ResearchPlan(
                    tasks=(
                        task.model_copy(
                            update={"question": intent.standalone_question}
                        ),
                    ),
                    warnings=(
                        Notice(
                            code="single_task_scope_restored",
                            message="Reused the standalone question for the one-task plan.",
                        ),
                    ),
                )
        return assigned
    except Exception as exc:
        return ResearchPlan(
            tasks=(
                ResearchTask(
                    task_id="t1",
                    label="Research",
                    question=intent.standalone_question,
                    needs_timeline=bool(
                        re.search(
                            r"\b(?:when|dates?|timeline|chronolog\w*)\b",
                            intent.standalone_question,
                            re.IGNORECASE,
                        )
                    ),
                ),
            ),
            warnings=(
                Notice(
                    code="planner_fallback",
                    message=f"Full-question fallback: {type(exc).__name__}.",
                ),
            ),
        )
