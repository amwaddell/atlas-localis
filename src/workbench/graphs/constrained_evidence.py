"""Tighten native generation to presented source text and admitted findings.

Selection still needs semantic review for relevance, clipped context and source
truth. These constraints prevent generated wording from inventing new relations.
The public payload shapes and original local validators remain authoritative.
"""

from __future__ import annotations

import re

from workbench.graphs.research_models import AnswerDraft, Findings, finding_aliases

_ABBREVIATION = re.compile(r"(?:\b[A-Z]|\b(?:Mr|Mrs|Dr|Prof|Gen|Maj|St|Jr|Sr))\.$")
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_ERA = r"(?:BCE?|CE|AD)"
_YEAR = rf"(?:\d{{1,4}}\s*{_ERA}|\d{{4}})"
_DATE = re.compile(
    rf"(?<![\w.,-])(?:c\.|ca\.|circa|about|around|early|late|mid|approximately)?\s*"
    rf"(?:{_MONTH}\s+\d{{1,2}}\s*[-–—]\s*(?:{_MONTH}\s+)?\d{{1,2}}(?:,\s*|\s+){_YEAR}"
    rf"|\d{{1,2}}\s+{_MONTH}\s*[-–—]\s*\d{{1,2}}\s+{_MONTH}\s+{_YEAR}"
    rf"|{_MONTH}\s+\d{{1,2}}(?:,\s*|\s+){_YEAR}"
    rf"|\d{{1,2}}\s+{_MONTH}\s+{_YEAR}"
    rf"|\d{{1,4}}(?:\s*{_ERA})?\s*(?:[-–—]|to)\s*\d{{1,4}}(?:\s*{_ERA})?"
    rf"|{_YEAR})(?!\w)",
    re.I,
)


def source_dates(statement: str) -> tuple[str, ...]:
    """Conservative exact date choices, never invented or normalized dates.

    Unsupported formats stay nullable. Maximal spans retain approximation and
    range rather than offering their contained, falsely precise year fragments.
    This does not establish that every number is historically a calendar date.
    """
    spans = []
    for match in _DATE.finditer(statement):
        start, end = match.span()
        if spans and re.fullmatch(
            r"\s*(?:[-–—]|to|until)\s*", statement[spans[-1][1] : start]
        ):
            start, _ = spans.pop()
        spans.append((start, end))
    return tuple(
        dict.fromkeys(statement[a:b].strip() for a, b in spans if b - a <= 120)
    )


def source_statements(text: str) -> tuple[str, ...]:
    """Bounded verbatim sentences/rows from whole presented lines.

    Never join lines or normalize source bytes. Abbreviation handling is modest;
    this is not a linguistic atomicity or entailment classifier.
    """
    statements = []
    for line in text.splitlines():
        start = 0
        for boundary in re.finditer(r"[.!?](?=\s+[A-Z])", line):
            end = boundary.end()
            if _ABBREVIATION.search(line[start:end]):
                continue
            candidate = line[start:end].strip()
            if candidate:
                statements.append(candidate)
            start = end
        if line[start:].strip():
            statements.append(line[start:].strip())
    return tuple(
        dict.fromkeys(
            text
            for text in statements
            if 1 <= len(text) <= 2000 and not re.search(r"[\[\]]|https?://|^\s*#", text)
        )
    )


def findings_schema(evidence) -> dict:
    schema = Findings.model_json_schema()
    variants = []
    for i, record in enumerate(evidence, 1):
        choices = source_statements(record.text)
        if not choices:
            continue
        for statement in choices:
            variant = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    # Every literal already satisfies the original string bounds;
                    # each fixed alias array satisfies its original item/count bounds.
                    "statement": {"type": "string", "const": statement},
                    "evidence_refs": {"type": "array", "const": [f"E{i}"]},
                    "date_text": {"enum": [None, *source_dates(statement)]},
                },
                "required": ["statement", "evidence_refs", "date_text"],
            }
            variants.append(variant)
    field = schema["properties"]["findings"]
    if variants:
        field["items"] = {"anyOf": variants}
        schema.pop("$defs")  # No remaining references to the generic proposal.
    else:
        field["maxItems"] = 0
    return schema


def answer_schema(packet) -> dict:
    schema = AnswerDraft.model_json_schema()
    variants = []
    for alias, finding in finding_aliases(packet).items():
        variant = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "const": finding.statement},
                "finding_ids": {"type": "array", "const": [alias]},
            },
            "required": ["text", "finding_ids"],
        }
        variants.append(variant)
    if not variants:
        raise ValueError("Synthesis requires admitted findings")
    schema["properties"]["units"]["items"] = {"anyOf": variants}
    schema.pop("$defs")
    return schema
