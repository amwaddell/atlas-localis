"""
Task decomposer: splits multi-part questions into parallel sub-retrievals.

When a user asks a complex question with multiple independent parts
(e.g., "When did WW1 start and when did it end?"), this module:

1. Uses the LLM to detect multi-part questions and extract sub-questions
2. Runs each sub-question through the RAG pipeline in parallel
3. Synthesises all sub-answers into a single coherent response

Citation handling
-----------------
Small local models cannot reliably copy 16-character hex chunk_ids
during synthesis.  To work around this, the synthesise node:

1. Replaces raw ``[chunk_id_hash]`` in sub-answers with short labels
   (``[REF_A1]``, ``[REF_B1]``, etc.) before sending to the LLM.
2. Tells the LLM to preserve these short labels.
3. After synthesis, maps labels back to real chunk_ids.
4. Lets ``numberize_result()`` do the final ``[chunk_id]`` → ``[1]``
   conversion for display.

None-safety
-----------
LangGraph may set TypedDict fields to ``None`` rather than leaving
them absent.  ``dict.get("key", [])`` returns ``None`` when the key
exists with value ``None`` — the default only applies to *missing*
keys.  All state access here uses ``state.get(key) or default``
to handle both cases.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from workbench.observability.tracing import add_span_attributes, start_span
from workbench.prompting.prompt_builder import (
    PromptBuilder,
    format_conversation_context,
)

# ------------------------------------------------------------------ #
#  Constants                                                          #
# ------------------------------------------------------------------ #

MAX_SUB_QUESTIONS = 5
DEFAULT_MAX_WORKERS = 4

# Same pattern as citations.py _BRACKET_TOKEN_RE
_BRACKET_TOKEN_RE = re.compile(r"\[([A-Za-z0-9_]+)\]")

# Letters for per-sub-question label prefixes
_LABEL_PREFIXES = "ABCDE"


# ------------------------------------------------------------------ #
#  Safe state getters                                                 #
# ------------------------------------------------------------------ #


def _slist(state: dict[str, Any], key: str) -> list:
    return state.get(key) or []


def _sstr(state: dict[str, Any], key: str) -> str:
    return state.get(key) or ""


def _sint(state: dict[str, Any], key: str) -> int:
    return state.get(key) or 0


# ------------------------------------------------------------------ #
#  Decomposition prompt (fallback)                                    #
# ------------------------------------------------------------------ #

_FALLBACK_DECOMPOSE_PROMPT = """\
You are a question analyser.  Determine whether the following question \
contains multiple independent sub-questions that would each benefit from \
a separate search of the knowledge base.

RULES:
- "When did WW1 start and when did it end?" → TWO sub-questions \
(start date, end date)
- "What caused WW1?" → SINGLE (complex answer but one research topic)
- "Who was Napoleon and what battles did he fight?" → TWO sub-questions
- "What is the capital of France?" → SINGLE
- Only split when parts are truly independent retrieval tasks.
- Each sub-question must be fully self-contained (include enough \
context from the original so it can be searched on its own).

{conversation_context}
QUESTION: {question}

If the question is simple (one retrieval task), respond with exactly:
SINGLE

If the question has multiple independent parts, respond with ONLY a \
JSON array of self-contained sub-questions — no commentary:
["sub-question 1", "sub-question 2"]\
"""

# ------------------------------------------------------------------ #
#  Synthesis prompt (fallback)                                        #
# ------------------------------------------------------------------ #

_FALLBACK_SYNTHESISE_PROMPT = """\
You are a research assistant.  The user asked a multi-part question.  \
Each part was researched separately.  Your job is to combine the \
sub-answers into a single coherent response.

CITATION RULES (CRITICAL):
1. Each sub-answer contains short reference labels like [REF_A1], \
[REF_B1], etc.
2. You MUST copy these labels exactly into your combined answer.
3. Place labels immediately after the claim they support.
4. Do NOT invent new labels.  Do NOT use [1], [2], etc.  Only use \
the [REF_xx] labels from the sub-answers.

STRUCTURE:
- Write a unified, paragraph-based answer — not a list of sub-answers.
- If a sub-answer says it lacks evidence, acknowledge that gap.
- End with a brief summary sentence.

{conversation_context}
ORIGINAL QUESTION: {question}

--- SUB-ANSWERS ---
{sub_answers_block}

Write your combined answer now.  Copy every [REF_xx] label exactly.\
"""

# ------------------------------------------------------------------ #
#  Parsing helpers                                                    #
# ------------------------------------------------------------------ #


def _parse_decompose_response(text: str) -> list[str] | None:
    """
    Parse the LLM's decomposition response.

    Returns ``None`` for SINGLE, a list of sub-question strings otherwise.
    """
    cleaned = text.strip()

    if cleaned.upper().startswith("SINGLE"):
        return None

    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list) and all(isinstance(q, str) for q in parsed):
            sub_qs = [q.strip() for q in parsed if q.strip()]
            if len(sub_qs) >= 2:
                return sub_qs[:MAX_SUB_QUESTIONS]
    except (json.JSONDecodeError, TypeError):
        pass

    return None


# ------------------------------------------------------------------ #
#  Citation label helpers                                             #
# ------------------------------------------------------------------ #


def _build_label_map(sub_results: list[dict[str, Any]]) -> dict[str, str]:
    """
    Build chunk_id → REF_A1 / REF_B1 / … label mapping.

    Each sub-question gets a letter prefix (A, B, C, …).
    Each unique chunk within that sub-question gets a number.
    """
    chunk_id_to_label: dict[str, str] = {}
    label_counter: dict[str, int] = {}

    for i, sr in enumerate(sub_results):
        if sr.get("error"):
            continue
        prefix = _LABEL_PREFIXES[i] if i < len(_LABEL_PREFIXES) else chr(65 + i)

        # Gather from opened evidence
        for chunk in sr.get("opened") or []:
            cid = chunk.get("chunk_id") or ""
            if cid and cid not in chunk_id_to_label:
                label_counter.setdefault(prefix, 0)
                label_counter[prefix] += 1
                chunk_id_to_label[cid] = f"REF_{prefix}{label_counter[prefix]}"

    return chunk_id_to_label


def _replace_ids_with_labels(text: str, id_to_label: dict[str, str]) -> str:
    """Replace [chunk_id] → [REF_A1] in text."""

    def _sub(match: re.Match) -> str:
        token = match.group(1)
        if token in id_to_label:
            return f"[{id_to_label[token]}]"
        return match.group(0)

    return _BRACKET_TOKEN_RE.sub(_sub, text)


def _replace_labels_with_ids(text: str, label_to_id: dict[str, str]) -> str:
    """Replace [REF_A1] → [chunk_id] in synthesised text."""

    def _sub(match: re.Match) -> str:
        token = match.group(1)
        if token in label_to_id:
            return f"[{label_to_id[token]}]"
        return match.group(0)

    return re.sub(r"\[(REF_[A-Z]\d+)\]", _sub, text)


def _checked_citations(
    text: str, valid_ids: set[str], urls: list[str]
) -> tuple[list[str], list[str]]:
    """Reject invalid/uncited prose; this checks references, never entailment."""
    from workbench.prompting.citations import extract_web_citations

    tokens = re.findall(r"\[([^\[\]\n]*)\]", text)
    if any(token not in valid_ids for token in tokens):
        raise ValueError("Unknown citation marker")
    citations = list(dict.fromkeys(tokens))
    web_citations = extract_web_citations(text, urls)
    paragraphs = [part for part in text.split("\n\n") if part.strip()]
    cited_paragraphs = sum(
        bool(_BRACKET_TOKEN_RE.search(part))
        or any(url in part for url in web_citations)
        for part in paragraphs
    )
    if (
        not paragraphs
        or not (citations or web_citations)
        or cited_paragraphs / len(paragraphs) < 0.5
    ):
        raise ValueError("Insufficient citation coverage")
    return citations, web_citations


def _format_sub_answers_labelled(
    sub_results: list[dict[str, Any]],
    id_to_label: dict[str, str],
) -> str:
    """Format sub-answers for synthesis prompt, with REF labels."""
    parts: list[str] = []
    for i, sr in enumerate(sub_results, 1):
        q = sr.get("question") or f"Sub-question {i}"
        a = (
            "(research failed)"
            if sr.get("error")
            else sr.get("answer_text") or "(no answer)"
        )
        a_labelled = _replace_ids_with_labels(a, id_to_label)
        parts.append(f"--- Part {i}: {q} ---\n{a_labelled}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ #
#  Node factories                                                     #
# ------------------------------------------------------------------ #


def make_decompose_node(
    model: Any,
    prompt_builder: PromptBuilder | None = None,
    generation_settings: dict[str, Any] | None = None,
    enabled: bool = True,
) -> Any:
    """
    Create the decomposition-check node.

    State writes:
        ``is_decomposed``, ``sub_questions``, ``decompose_latency_ms``
    """
    gen_settings = {
        **(generation_settings or {}),
        "temperature": 0.1,
        "max_tokens": 300,
    }

    def decompose_node(state: dict[str, Any]) -> dict[str, Any]:
        question = _sstr(state, "rewritten_query") or state["question"]
        chat_history = _slist(state, "chat_history")
        conversation_summary = _sstr(state, "conversation_summary")

        if not enabled:
            return {
                "is_decomposed": False,
                "sub_questions": [],
                "decompose_latency_ms": 0.0,
            }

        with start_span(
            "graph.decompose_check",
            attributes={"question": question[:200], "enabled": enabled},
        ):
            t0 = time.time()

            try:
                conversation_context = ""
                if chat_history:
                    conversation_context = (
                        "CONVERSATION CONTEXT:\n"
                        + format_conversation_context(
                            chat_history=chat_history,
                            conversation_summary=conversation_summary,
                        )
                        + "\n"
                    )

                if prompt_builder is not None:
                    try:
                        user_prompt = prompt_builder.render(
                            "decompose_question",
                            question=question,
                            conversation_context=conversation_context,
                        )
                    except FileNotFoundError:
                        user_prompt = _FALLBACK_DECOMPOSE_PROMPT.format(
                            question=question,
                            conversation_context=conversation_context,
                        )
                else:
                    user_prompt = _FALLBACK_DECOMPOSE_PROMPT.format(
                        question=question,
                        conversation_context=conversation_context,
                    )

                messages = [{"role": "user", "content": user_prompt}]
                response = model.generate(messages, **gen_settings)

                sub_questions = _parse_decompose_response(response.text)
                is_decomposed = sub_questions is not None

                latency_ms = (time.time() - t0) * 1000

                add_span_attributes(
                    {
                        "is_decomposed": is_decomposed,
                        "sub_question_count": len(sub_questions)
                        if sub_questions
                        else 0,
                        "decompose_latency_ms": latency_ms,
                        "raw_response": response.text[:300],
                    }
                )

                return {
                    "is_decomposed": is_decomposed,
                    "sub_questions": sub_questions or [],
                    "decompose_latency_ms": latency_ms,
                }

            except Exception as exc:
                latency_ms = (time.time() - t0) * 1000
                add_span_attributes({"decompose_error": str(exc)[:200]})
                return {
                    "is_decomposed": False,
                    "sub_questions": [],
                    "decompose_latency_ms": latency_ms,
                }

    return decompose_node


def decompose_dispatcher(state: dict[str, Any]) -> str:
    """Route to parallel retrieval or normal routing."""
    if state.get("is_decomposed", False) and _slist(state, "sub_questions"):
        return "parallel_retrieve"
    return "route_question"


def make_parallel_retrieve_node(
    rag_subgraph: Any,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> Any:
    """
    Create the parallel retrieval node.

    State writes:
        ``sub_results``, ``retrieved``, ``opened``, ``citations``,
        ``tokens_in``, ``tokens_out``
    """

    def parallel_retrieve_node(state: dict[str, Any]) -> dict[str, Any]:
        sub_questions = _slist(state, "sub_questions")
        chat_history = _slist(state, "chat_history")
        conversation_summary = _sstr(state, "conversation_summary")

        with start_span(
            "graph.parallel_retrieve",
            attributes={
                "sub_question_count": len(sub_questions),
                "max_workers": max_workers,
            },
        ):
            t0 = time.time()

            def _run_sub(question: str) -> dict[str, Any]:
                sub_input: dict[str, Any] = {"question": question}
                if chat_history:
                    sub_input["chat_history"] = chat_history
                if conversation_summary:
                    sub_input["conversation_summary"] = conversation_summary

                result = rag_subgraph.invoke(sub_input)

                return {
                    "question": question,
                    "answer_text": result.get("answer_text") or "",
                    "citations": result.get("citations") or [],
                    "web_citations": result.get("web_citations") or [],
                    "retrieved": result.get("retrieved") or [],
                    "opened": result.get("opened") or [],
                    "tokens_in": result.get("tokens_in") or 0,
                    "tokens_out": result.get("tokens_out") or 0,
                    "model_latency_ms": result.get("model_latency_ms") or 0.0,
                    "error": result.get("error"),
                }

            sub_results: list[dict[str, Any]] = []

            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_q = {
                        executor.submit(_run_sub, q): q for q in sub_questions
                    }
                    for future in as_completed(future_to_q):
                        q = future_to_q[future]
                        try:
                            sub_results.append(future.result())
                        except Exception as exc:
                            sub_results.append(
                                {
                                    "question": q,
                                    "answer_text": "",
                                    "citations": [],
                                    "web_citations": [],
                                    "retrieved": [],
                                    "opened": [],
                                    "tokens_in": 0,
                                    "tokens_out": 0,
                                    "model_latency_ms": 0.0,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
            except Exception as exc:
                add_span_attributes({"parallel_error": str(exc)[:200]})

            # Sort to match original question order
            order = {q: i for i, q in enumerate(sub_questions)}
            sub_results.sort(key=lambda r: order.get(r.get("question", ""), 999))

            # Aggregate
            all_retrieved: list = []
            all_opened: list = []
            all_citations: list = []
            all_web_citations: list = []
            total_in = 0
            total_out = 0

            for sr in sub_results:
                all_retrieved.extend(sr.get("retrieved") or [])
                all_opened.extend(sr.get("opened") or [])
                all_citations.extend(sr.get("citations") or [])
                all_web_citations.extend(sr.get("web_citations") or [])
                total_in += sr.get("tokens_in") or 0
                total_out += sr.get("tokens_out") or 0

            latency_ms = (time.time() - t0) * 1000

            add_span_attributes(
                {
                    "sub_results_count": len(sub_results),
                    "total_retrieved": len(all_retrieved),
                    "total_opened": len(all_opened),
                    "parallel_latency_ms": latency_ms,
                }
            )

            return {
                "sub_results": sub_results,
                "retrieved": all_retrieved,
                "opened": all_opened,
                "citations": list(dict.fromkeys(all_citations)),
                "web_citations": list(dict.fromkeys(all_web_citations)),
                "tokens_in": total_in,
                "tokens_out": total_out,
            }

    return parallel_retrieve_node


def make_synthesise_node(
    model: Any,
    prompt_builder: PromptBuilder | None = None,
    generation_settings: dict[str, Any] | None = None,
) -> Any:
    """
    Create the synthesis node.

    Uses short REF labels so the LLM can copy citations reliably,
    then maps them back to real chunk_ids afterward.  The downstream
    ``numberize_result()`` handles the final [chunk_id] → [1] display.

    State writes:
        ``answer_text``, ``citations``, ``web_citations``,
        ``tokens_in``, ``tokens_out``, ``model_latency_ms``,
        ``prompt_meta``, ``subgraph_used``
    """
    gen_settings = generation_settings or {}

    def synthesise_node(state: dict[str, Any]) -> dict[str, Any]:
        question = state["question"]
        sub_results = _slist(state, "sub_results")
        chat_history = _slist(state, "chat_history")
        conversation_summary = _sstr(state, "conversation_summary")

        if not sub_results:
            return {
                "answer_text": (
                    "I don't have enough evidence in the provided "
                    "articles to fully answer this question."
                ),
                "citations": [],
                "web_citations": [],
                "subgraph_used": "decomposer",
                "prompt_meta": {"variant": "synthesise_empty"},
            }

        # Only locally opened evidence can establish a chunk reference.
        all_urls = list(
            dict.fromkeys(
                url
                for sr in sub_results
                if not sr.get("error")
                for url in sr.get("web_citations") or []
                if url
            )
        )

        response = None

        def fallback(error: str | None = None) -> dict[str, Any]:
            parts = []
            citations = []
            web_citations = []
            missing = []
            for i, sr in enumerate(sub_results, 1):
                text = sr.get("answer_text") or ""
                try:
                    if sr.get("error"):
                        raise ValueError("Worker failed")
                    ids = set(_build_label_map([sr]))
                    cited, web = _checked_citations(
                        text, ids, sr.get("web_citations") or []
                    )
                except ValueError:
                    missing.append(sr.get("question") or f"Part {i}")
                    continue
                parts.append(text)
                citations.extend(cited)
                web_citations.extend(web)
            if not parts:
                parts.append(
                    "I don't have enough evidence in the provided articles to fully answer this question."
                )
            if missing:
                parts.append("Research incomplete for: " + "; ".join(missing) + ".")
            return {
                "answer_text": "\n\n".join(parts),
                "citations": list(dict.fromkeys(citations)),
                "web_citations": list(dict.fromkeys(web_citations)),
                "tokens_in": _sint(state, "tokens_in")
                + ((response.tokens_in or 0) if response else 0),
                "tokens_out": _sint(state, "tokens_out")
                + ((response.tokens_out or 0) if response else 0),
                "model_latency_ms": (response.latency_ms or 0.0) if response else 0.0,
                "subgraph_used": "decomposer",
                "prompt_meta": {
                    "variant": "synthesise_fallback",
                    "missing_parts": missing,
                },
                "error": error,
            }

        successful = [
            sr for sr in sub_results if sr.get("answer_text") and not sr.get("error")
        ]
        if len(successful) <= 1:
            result = fallback()
            result["prompt_meta"]["variant"] = "synthesise_single_passthrough"
            return result

        with start_span(
            "graph.synthesise",
            attributes={
                "question": question[:200],
                "sub_results_count": len(sub_results),
            },
        ):
            try:
                # --- Build label map ----------------------------------
                id_to_label = _build_label_map(sub_results)

                # --- Conversation context -----------------------------
                conversation_context = ""
                if chat_history:
                    conversation_context = (
                        "CONVERSATION CONTEXT:\n"
                        + format_conversation_context(
                            chat_history=chat_history,
                            conversation_summary=conversation_summary,
                        )
                        + "\n"
                    )

                # --- Build labelled sub-answers -----------------------
                sub_answers_block = _format_sub_answers_labelled(
                    sub_results, id_to_label
                )

                # Only labels actually supplied in worker prose are eligible.
                supplied = set(_BRACKET_TOKEN_RE.findall(sub_answers_block))
                label_to_id = {v: k for k, v in id_to_label.items() if v in supplied}

                # --- Build synthesis prompt ---------------------------
                if prompt_builder is not None:
                    try:
                        user_prompt = prompt_builder.render(
                            "synthesize_answers",
                            question=question,
                            conversation_context=conversation_context,
                            sub_answers_block=sub_answers_block,
                        )
                    except FileNotFoundError:
                        user_prompt = _FALLBACK_SYNTHESISE_PROMPT.format(
                            question=question,
                            conversation_context=conversation_context,
                            sub_answers_block=sub_answers_block,
                        )
                else:
                    user_prompt = _FALLBACK_SYNTHESISE_PROMPT.format(
                        question=question,
                        conversation_context=conversation_context,
                        sub_answers_block=sub_answers_block,
                    )

                messages = [{"role": "user", "content": user_prompt}]
                response = model.generate(messages, **gen_settings)

                # --- Post-process citations ---------------------------
                # 1. REF labels → real chunk_ids
                answer_with_ids = _replace_labels_with_ids(response.text, label_to_id)
                # Reject the whole draft rather than erase markers and retain claims.
                clean_answer = answer_with_ids
                final_citations, final_web_citations = _checked_citations(
                    clean_answer, set(label_to_id.values()), all_urls
                )
                missing = [
                    sr.get("question") or "Unknown part"
                    for sr in sub_results
                    if not sr.get("answer_text") or sr.get("error")
                ]
                if missing:
                    clean_answer += (
                        "\n\nResearch incomplete for: " + "; ".join(missing) + "."
                    )
                prev_in = _sint(state, "tokens_in")
                prev_out = _sint(state, "tokens_out")

                add_span_attributes(
                    {
                        "synthesised_length": len(clean_answer),
                        "labels_mapped": len(id_to_label),
                        "final_citation_count": len(final_citations),
                    }
                )

                return {
                    "answer_text": clean_answer,
                    "citations": final_citations,
                    "web_citations": final_web_citations,
                    "tokens_in": prev_in + (response.tokens_in or 0),
                    "tokens_out": prev_out + (response.tokens_out or 0),
                    "model_latency_ms": response.latency_ms or 0.0,
                    "prompt_meta": {
                        "variant": "synthesise_decomposed",
                        "sub_question_count": len(sub_results),
                        "labels_mapped": len(id_to_label),
                    },
                    "subgraph_used": "decomposer",
                }

            except Exception as exc:
                return fallback(f"synthesise_node: {type(exc).__name__}: {exc}")

    return synthesise_node
