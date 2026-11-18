"""Frozen suite contracts and model-free validation. Seed questions remain unchanged."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from workbench.graphs.planning import conversation_input

DATA = Path(__file__).with_name("data")


def load_suite(path=None):
    suite = json.loads(Path(path or DATA / "history-v1.json").read_text())
    validate_suite(suite)
    return suite


def validate_suite(suite):
    cases = suite["cases"]
    if suite["version"] != "history-v1" or not suite["frozen_before_runs"]:
        raise ValueError("Unknown or unfrozen suite")
    if len(cases) != 24 or len({c["id"] for c in cases}) != 24:
        raise ValueError("Suite requires 24 unique cases")
    if Counter(c["category"] for c in cases) != dict.fromkeys(
        ["single", "comparative", "temporal", "insufficient"], 6
    ):
        raise ValueError("Suite requires six cases per category")
    if Counter(c["split"] for c in cases) != {"development": 8, "held_out": 16}:
        raise ValueError("Frozen split must contain 8 development / 16 held out")
    conversation = [c for c in cases if c.get("history") or c.get("summary")]
    if len(conversation) < 4 or not any(
        c.get("summary") and not c.get("history") for c in conversation
    ):
        raise ValueError("Missing conversation/summary-only coverage")
    if not any(c["category"] == "insufficient" for c in conversation):
        raise ValueError("Missing ambiguous conversation")
    for c in cases:
        conversation_input(c["question"], c.get("history"), c.get("summary"))
        if c["snapshot_id"] != suite["snapshot_id"] or c["rubric"] != suite["rubric"]:
            raise ValueError("Case identity mismatch")
        if not c["scope"] or not c["task_facets"] or not c["annotation"]:
            raise ValueError("Missing scope/annotation")
        if c["category"] == "insufficient":
            if not c.get("insufficiency_basis"):
                raise ValueError("Missing insufficiency rationale")
        elif not any(e["relevance"] > 0 for e in c["evidence"]):
            raise ValueError("Answerable case needs positive evidence")
        for e in c["evidence"]:
            if e["relevance"] not in [0, 1, 2] or not e["article"] or not e["chunk_id"]:
                raise ValueError("Invalid evidence label")
            if e["end"] - e["start"] != len(e["excerpt"]) or e["start"] < 0:
                raise ValueError("Invalid excerpt offsets")
            if hashlib.sha256(e["excerpt"].encode()).hexdigest() != e["excerpt_sha256"]:
                raise ValueError("Invalid excerpt hash")
    return suite


def validate_corpus(suite, store, snapshot_id):
    if suite["snapshot_id"] != snapshot_id:
        raise ValueError("Suite is pinned to a different snapshot; relabel explicitly")
    checked = set()
    for c in suite["cases"]:
        for e in c["evidence"]:
            chunk = store.get(e["chunk_id"])
            if chunk is None or chunk.text[e["start"] : e["end"]] != e["excerpt"]:
                raise ValueError(f"Evidence no longer resolves: {e['chunk_id']}")
            checked.add(e["chunk_id"])
    return len(checked)
