"""Focused lexical-support, repair and pinned native-schema regressions."""

import copy
import json
import re

import httpx
import pytest
from pydantic import ValidationError
from tests.test_evidence_worker import Model, chunk, draft, run, setup, value
from tests.test_stage2_contracts import packet

from workbench.graphs.evidence_support import support_errors
from workbench.graphs.research_models import AnswerDraft, RewritePayload
from workbench.graphs.synthesis import validate_draft
from workbench.models.errors import ModelResponseError
from workbench.models.llamacpp_server import LlamaCppServerModel
from workbench.models.native_schema import native_schema


@pytest.mark.parametrize(
    "claim,source,category",
    [
        ("The emperor descended from Inventus.", "The emperor ruled.", "capitalized"),
        ("The war ended in 1843.", "The war ended in 1840.", "numeric"),
        ("The war ended in 43.", "The war ended in 1843.", "numeric"),
        ("The farmer said “bring rain”.", "The farmer grew wheat.", "quotation"),
        ("The words are documented.", "The words might be reconstructed.", "certainty"),
    ],
)
def test_absent_specific_details_rejected(claim, source, category):
    assert any(category in e for e in support_errors(claim, (source,)))


def test_lexical_checks_do_not_claim_entailment():
    # All words occur, but who defeated whom is reversed. Human semantic review
    # remains necessary even when these narrow checks pass.
    assert not support_errors("Alpha defeated Beta.", ("Beta defeated Alpha.",))
    assert not support_errors("The army won.", ("The army did not win.",))


def test_name_cues_allow_diacritic_and_possessive_variants():
    assert not support_errors("Choshu supported Meiji.", ("Chōshū supported Meiji.",))
    assert not support_errors("Caesar ruled.", ("Caesar's rule ended.",))
    assert not support_errors("Caesar’s rule ended.", ("Caesar ruled.",))
    assert support_errors("The farmer said “Choshu”.", ("The farmer said “Chōshū”.",))


def test_support_uses_only_explicitly_cited_excerpts_and_rechecks_repair():
    bad = draft(statement="The emperor descended from Inventus.")
    env = setup(
        [chunk(text="The emperor ruled."), chunk("b", "other", text="Inventus ruled.")],
        model=Model(bad, bad),
    )
    result = run(env)
    assert result.status == "failed" and not result.findings
    assert result.error.code == "findings_invalid"
    assert len(env.model.calls) == 2
    assert "findings.0.statement" in result.error.message
    assert "capitalized detail" in env.model.calls[1][0][-1]["content"]
    assert "descended from Inventus" not in env.model.calls[1][0][-1]["content"]


def test_repair_can_remove_unsupported_claim_and_leave_honest_gap():
    env = setup(
        [chunk(text="Latin was used in Rome.")],
        model=Model(
            draft(statement="The farmer whispered “bring rain”."),
            json.dumps(
                {"findings": [], "gaps": ["No recorded words in the excerpts."]}
            ),
        ),
    )
    result = run(env)
    assert result.status == "insufficient_evidence" and not result.findings
    assert result.error is None and len(env.model.calls) == 2


def test_date_repair_reports_index_and_rejects_joined_or_reformatted_dates():
    text = "Campaign A: March 17 - August 3, 1862. Campaign B: April 3–9, 1865."
    bad = draft(
        statement="Campaign A: March 17 - August 3, 1862.",
        date="March 17–August 3, 1862",
    )
    fixed = draft(
        statement="Campaign A: March 17 - August 3, 1862.",
        date="March 17 - August 3, 1862",
    )
    env = setup([chunk(text=text)], model=Model(bad, fixed))
    result = run(env, value(timeline=True))
    assert result.status == "complete"
    assert result.findings[0].date_text == "March 17 - August 3, 1862"
    assert "findings.0.date_text" in env.model.calls[1][0][-1]["content"]
    joined = draft(
        statement="Campaign A took place.",
        date="March 17 - August 3, 1862; April 3–9, 1865",
    )
    result = run(setup([chunk(text=text)], model=Model(joined)))
    assert result.status == "failed" and not result.findings


@pytest.mark.parametrize(
    "text", ["An event happened in 27 BC.", "An event was officially documented."]
)
def test_synthesis_rejects_new_precision_or_certainty(text):
    report = validate_draft(
        packet(), AnswerDraft(units=[{"text": text, "finding_ids": ["F1"]}])
    )
    assert report.status == "failed" and "units.0" in report.errors[0]


@pytest.mark.parametrize("minimum,maximum", [(0, 2001), (1, 8000), (2050, 4001)])
def test_native_lowering_preserves_serialized_char_bounds(minimum, maximum):
    schema = {"type": "string", "minLength": minimum, "maxLength": maximum}
    original = copy.deepcopy(schema)
    lowered = native_schema(schema)
    assert schema == original
    pattern = re.compile(lowered["pattern"])
    for n in set([0, max(0, minimum - 1), minimum, maximum, maximum + 1]):
        assert bool(pattern.fullmatch("x" * n)) == (minimum <= n <= maximum)
    # Each JSON escape represents a character in the pinned native char rule.
    escaped = r"\"" * max(1, minimum)
    assert bool(pattern.fullmatch(escaped)) == (max(1, minimum) <= maximum)
    assert not pattern.fullmatch('x"y')
    assert not pattern.fullmatch("x\ny")
    assert all(
        int(n) <= 2000 for n in re.findall(r"\d+", lowered["pattern"]) if len(n) > 2
    )


def test_native_lowering_preserves_refs_null_and_original_local_validation():
    original = RewritePayload.model_json_schema()
    before = copy.deepcopy(original)
    result = native_schema(original)
    assert original == before
    branches = result["properties"]["standalone_question"]["anyOf"]
    assert branches[1] == {"type": "null"}
    assert "pattern" in branches[0] and "maxLength" not in branches[0]
    assert (
        RewritePayload.model_validate_json(
            '{"standalone_question":null}'
        ).standalone_question
        is None
    )
    with pytest.raises(ValidationError):
        RewritePayload(standalone_question="a" * 8001)
    with pytest.raises(ValueError, match="Unsupported native"):
        native_schema({"type": "string", "pattern": "^x+$", "maxLength": 8000})


def test_native_http_error_retains_diagnosis_without_retry():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            500, json={"error": {"message": "Failed to parse input at pos 0"}}
        )

    with LlamaCppServerModel() as model:
        model.client.close()
        model.client = httpx.Client(
            base_url=model.base_url, transport=httpx.MockTransport(respond)
        )
        with pytest.raises(
            ModelResponseError, match="500.*Failed to parse input at pos 0"
        ):
            model.generate([{"role": "user", "content": "Hello"}], max_tokens=10)
    assert len(requests) == 1


def test_single_task_dispatch_preserves_authoritative_scope():
    from tests.test_supervisor_orchestration import Retriever, answer, graph, run

    proposal = json.dumps(
        {
            "tasks": [
                {
                    "label": "Emperor and lineage",
                    "question": "Identify the emperor and trace his lineage from Inventus.",
                    "needs_timeline": False,
                }
            ]
        }
    )
    retriever = Retriever()
    result = run(
        graph(Model(proposal, draft(), answer()), retriever),
        question="Which emperor did the alliance support?",
    )
    assert result.error is None
    assert retriever.calls[0][0] == "Which emperor did the alliance support?"
    assert any(w.code == "single_task_scope_restored" for w in result.warnings)


def test_repair_requires_verbatim_supported_statement_not_just_known_names():
    env = setup(
        [chunk(text="Alpha defeated Beta.")],
        model=Model(
            draft(statement="Alpha defeated Inventus."),
            draft(statement="Beta defeated Alpha."),
        ),
    )
    result = run(env)
    assert result.status == "failed" and not result.findings
    assert "verbatim span" in result.error.message


@pytest.mark.parametrize(
    "prior",
    [
        "Compare the Bronze Age and the Iron Age.",
        "Compare the Ming dynasty with the Qing dynasty.",
    ],
)
def test_singular_comparison_followup_requests_clarification(prior):
    from workbench.graphs.planning import conversation_input, normalize
    from workbench.graphs.research_models import Limits

    value = conversation_input(
        "When did it begin?",
        [
            {"role": "user", "content": prior},
            {"role": "assistant", "content": "Both changed society."},
        ],
    )
    model = Model('{"standalone_question":"When did both periods begin?"}')
    result = normalize(value, model, Limits(prompt_tokens=20000))
    assert result.status == "unresolved" and result.standalone_question is None
    assert len(model.calls) == 1


def test_explicit_item_or_subsequent_focus_is_not_automatically_ambiguous():
    from workbench.graphs.planning import (
        ambiguous_comparison_reference,
        conversation_input,
    )

    prior = [{"role": "user", "content": "Compare the Bronze Age and the Iron Age."}]
    assert not ambiguous_comparison_reference(
        conversation_input("When did the Iron Age begin and how did it spread?", prior)
    )
    assert not ambiguous_comparison_reference(
        conversation_input(
            "When did it begin?",
            [*prior, {"role": "assistant", "content": "Let us focus on the Iron Age."}],
        )
    )


def test_extractive_repair_feedback_fits_existing_reserve():
    from workbench.retrieval.evidence_packing import REPAIR_RESERVE, findings_messages

    base = findings_messages(value(), ())[0]["content"]
    repaired = findings_messages(value(), (), ("é" * 1900,))[0]["content"]
    assert repaired.startswith(base)
    assert len(repaired.encode()) - len(base.encode()) <= REPAIR_RESERVE


def test_synthesis_cannot_add_lowercase_clause_even_when_all_words_are_known():
    p = packet()
    unfaithful = AnswerDraft(
        units=[
            {
                "text": "An event happened because another event happened.",
                "finding_ids": ["F1"],
            }
        ]
    )
    assert validate_draft(p, unfaithful).status == "failed"
    faithful = AnswerDraft(
        units=[{"text": "An event happened.", "finding_ids": ["F1"]}]
    )
    assert validate_draft(p, faithful).status == "passed"


def test_initial_finding_cannot_reverse_relation_using_only_known_words():
    env = setup(
        [chunk(text="Beta defeated Alpha.")],
        model=Model(
            draft(statement="Alpha defeated Beta."),
            draft(statement="Beta defeated Alpha."),
        ),
    )
    result = run(env)
    assert result.status == "complete" and len(env.model.calls) == 2
    assert result.findings[0].statement == "Beta defeated Alpha."


def test_contextual_schemas_bind_source_text_to_alias_and_final_text_to_finding():
    from tests.test_stage2_contracts import evidence

    from workbench.graphs.constrained_evidence import (
        answer_schema,
        findings_schema,
        source_statements,
    )

    records = (evidence(),)
    constrained = findings_schema(records)["properties"]["findings"]["items"]["anyOf"]
    assert len(constrained) == 1
    assert constrained[0]["properties"]["evidence_refs"]["const"] == ["E1"]
    assert constrained[0]["properties"]["statement"]["const"] == records[0].text
    assert constrained[0]["properties"]["date_text"]["enum"] == [None]
    assert source_statements("First statement. Second statement.\nA row || 1900") == (
        "First statement.",
        "Second statement.",
        "A row || 1900",
    )
    assert source_statements("General R. E. Lee surrendered. Another statement.") == (
        "General R. E. Lee surrendered.",
        "Another statement.",
    )
    assert not source_statements("[unsafe] https://invalid.example")
    assert findings_schema(())["properties"]["findings"]["maxItems"] == 0
    constrained = answer_schema(packet())["properties"]["units"]["items"]["anyOf"]
    assert constrained[0]["properties"]["text"]["const"] == "An event happened."
    assert constrained[0]["properties"]["finding_ids"]["const"] == ["F1"]


def test_date_must_belong_to_the_selected_statement_not_another_event():
    env = setup(
        [chunk(text="Event A occurred in 1900. Event B occurred in 1901.")],
        model=Model(
            draft(statement="Event A occurred in 1900.", date="1901"),
            draft(statement="Event A occurred in 1900.", date="1900"),
        ),
    )
    result = run(env, value(timeline=True))
    assert result.status == "complete" and len(env.model.calls) == 2
    assert result.findings[0].date_text == "1900"


def test_tightened_schema_is_visible_to_model_not_only_decoder():
    from workbench.models.structured_json import generate_validated_json

    schema = RewritePayload.model_json_schema()
    schema["properties"]["standalone_question"] = {"const": None}
    model = Model('{"standalone_question":null}')
    generate_validated_json(
        model,
        [{"role": "user", "content": "Clarify."}],
        RewritePayload,
        generation_schema=schema,
    )
    messages, settings = model.calls[0]
    visible = json.loads(messages[0]["content"].split("\n", 1)[1])
    assert visible == settings["json_schema"] == schema


def test_duplicate_findings_and_repeated_final_aliases_are_rejected():
    repeated = json.loads(draft())
    repeated["findings"] *= 2
    env = setup(model=Model(json.dumps(repeated), draft()))
    result = run(env)
    assert result.status == "complete" and len(result.findings) == 1
    assert len(env.model.calls) == 2
    assert "duplicate source statement" in env.model.calls[1][0][-1]["content"]
    assert (
        validate_draft(
            packet(),
            AnswerDraft(
                units=[
                    {"text": "An event happened.", "finding_ids": ["F1"]},
                    {"text": "An event happened.", "finding_ids": ["F1"]},
                ]
            ),
        ).status
        == "failed"
    )


@pytest.mark.parametrize(
    "text,dates",
    [
        ("In about 49 BC to 44 BC.", ("about 49 BC to 44 BC",)),
        ("On 16 January 27 BC.", ("16 January 27 BC",)),
        ("On April 12, 1861.", ("April 12, 1861",)),
        ("March 17 - August 3, 1862", ("March 17 - August 3, 1862",)),
        ("September 4–20, 1862", ("September 4–20, 1862",)),
        ("c. 500–400 BCE", ("c. 500–400 BCE",)),
        (
            "From March 17, 1862 to August 3, 1862.",
            ("March 17, 1862 to August 3, 1862",),
        ),
        ("There were 35,000–40,000 people.", ()),
    ],
)
def test_native_date_choices_preserve_exact_source_expressions(text, dates):
    from workbench.graphs.constrained_evidence import source_dates

    assert source_dates(text) == dates
    assert all(date in text for date in dates)
