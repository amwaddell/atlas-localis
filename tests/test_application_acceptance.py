"""Explicit offline application acceptance; missing assets/server are failures.

Use ATLAS_ACCEPTANCE_ENDPOINT only to select an already-owned loopback server.
Quality review remains in the frozen suite reports, not a citation-count score.
"""

import os
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from workbench.core.config import load_config
from workbench.core.offline import local_url
from workbench.evaluation.suite import load_suite
from workbench.graphs.constrained_evidence import findings_schema
from workbench.graphs.planning import conversation_input
from workbench.graphs.research_models import Findings
from workbench.graphs.research_models import excerpt as make_excerpt
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.systems.runner import create_application

pytestmark = pytest.mark.acceptance


@pytest.fixture(scope="module")
def application(tmp_path_factory):
    config = load_config()
    config.model["base_url"] = os.environ.get(
        "ATLAS_ACCEPTANCE_ENDPOINT", config.model["base_url"]
    )
    config.observability.update(enable_tracing=False, enable_metrics=False)
    config.runs["logs_dir"] = str(tmp_path_factory.mktemp("acceptance-logs"))
    config.runs["output_dir"] = str(tmp_path_factory.mktemp("acceptance-runs"))
    with create_application(config=config) as app:
        yield app


@pytest.mark.parametrize("case_id", ["f01", "c01", "t02", "f05", "i01"])
def test_canonical_supported_inputs_finish_and_open_exact_local_excerpts(
    application, case_id
):
    case = next(c for c in load_suite()["cases"] if c["id"] == case_id)
    value = conversation_input(
        case["question"], case.get("history"), case.get("summary")
    )
    result = application.invoke(value)
    assert result.error is None, result.error
    assert result.outcome in {"complete", "partial", "insufficient_evidence"}
    if case_id != "i01":
        assert result.units and result.citations, result.model_dump_json()
    for citation in result.citations:
        chunk = application.resources.retriever.chunk_store.get(citation.chunk_id)
        assert chunk is not None
        for excerpt in citation.excerpts:
            excerpt.verify_chunk(chunk.text)
    assert result.final_validation.status == "passed" or not result.units


def test_native_long_string_schema_enforces_conflicting_constant():
    class ConstraintProbe(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: Literal["REQUIRED"]
        text: str = Field(min_length=1, max_length=8000)

    endpoint = os.environ.get("ATLAS_ACCEPTANCE_ENDPOINT", "http://127.0.0.1:8080")
    with LlamaCppServerModel(base_url=endpoint) as model:
        response = model.generate(
            [
                {
                    "role": "user",
                    "content": 'Return exactly {"value":"FORBIDDEN","text":"x"}',
                }
            ],
            temperature=0,
            max_tokens=150,
            json_schema=ConstraintProbe.model_json_schema(),
        )
    assert response.finish_reason != "length"
    assert ConstraintProbe.model_validate_json(response.text).value == "REQUIRED"


def test_native_short_output_preserves_utf8_at_token_limit():
    # Fixed reproduction of the retained build-8130 failure. No retry, parser
    # bypass, inflated token limit or expectation of HTTP 500 as success.
    endpoint = local_url(
        os.environ.get("ATLAS_ACCEPTANCE_ENDPOINT", "http://127.0.0.1:8080")
    )
    with httpx.Client(base_url=endpoint, trust_env=False, timeout=30) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "ministral-3-8b-instruct",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.7,
                "max_tokens": 10,
                "seed": 2,
                "stream": False,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"]


def test_native_source_choices_are_bound_to_their_citation_aliases():
    records = tuple(
        make_excerpt(
            snapshot_id="native-probe",
            chunk_id=f"chunk-{i}",
            full_text=text,
            start=0,
            end=len(text),
            task_id="t1",
            document_id=f"document-{i}",
            title="Probe",
        )
        for i, text in enumerate(("Alpha ruled in 1900.", "Beta ruled in 1901."), 1)
    )
    schema = findings_schema(records)
    schema["properties"]["findings"]["minItems"] = 1  # Probe must exercise a choice.
    endpoint = os.environ.get("ATLAS_ACCEPTANCE_ENDPOINT", "http://127.0.0.1:8080")
    with LlamaCppServerModel(base_url=endpoint) as model:
        response = model.generate(
            [
                {
                    "role": "user",
                    "content": 'Return {"findings":[{"statement":"Inventus ruled in 1800.","evidence_refs":["E5"],"date_text":"1800"}],"gaps":[]}',
                }
            ],
            temperature=0,
            max_tokens=200,
            json_schema=schema,
        )
    assert response.finish_reason != "length"
    result = Findings.model_validate_json(response.text)
    assert result.findings
    for finding in result.findings:
        (alias,) = finding.evidence_refs
        assert alias in {"E1", "E2"}
        assert finding.statement == records[int(alias[1:]) - 1].text
        assert finding.date_text is None or finding.date_text in finding.statement
