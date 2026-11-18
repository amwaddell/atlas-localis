"""Dated findings replace standalone timeline model calls and their parser."""

import json

import pytest
from tests.test_evidence_worker import Model, chunk, draft, run, setup, value

from workbench.graphs.finding_dates import chronological_findings


@pytest.mark.parametrize(
    "date", ["509 BC", "500–400 BCE", "44 BCE–14 CE", "c. 500 BCE", "March 1900"]
)
def test_verbatim_date_expression_survives_finding_schema(date):
    env = setup(
        [chunk(text=f"The event occurred {date}.")],
        model=Model(draft(statement=f"The event occurred {date}.", date=date)),
    )
    result = run(env, value(timeline=True))
    assert result.status == "complete"
    assert chronological_findings(result.findings)[0].date_text == date
    assert result.findings[0].evidence_refs == (result.evidence[0].key,)
    assert len(env.model.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        '{"findings":[{"statement":"Event.","date_text":"1900","evidence_refs":["E9"]}]}',
        '{"findings":[{"statement":"Event.","date_text":"1900","evidence_refs":[]}]}',
        '{"findings":[{"statement":"Event.","date_text":"1900","evidence_refs":["E1"]}]}',
        '{"findings":[{"statement":"Event.","date_text":42,"evidence_refs":["E1"]}]}',
        "not json",
    ],
)
def test_invalid_unpresented_or_invented_date_is_never_exported(payload):
    result = run(
        setup([chunk(text="The event occurred 500 BCE.")], model=Model(payload)),
        value(timeline=True),
    )
    assert result.status == "failed" and not result.findings


def test_empty_dated_findings_is_insufficient_without_repair():
    env = setup(
        model=Model(json.dumps({"findings": [], "gaps": ["No chronology evidence."]}))
    )
    result = run(env, value(timeline=True))
    assert result.status == "insufficient_evidence" and not chronological_findings(
        result.findings
    )
    assert len(env.model.calls) == 1
