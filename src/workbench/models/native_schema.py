"""Lower long string bounds for the pinned native schema-to-GBNF converter.

Build 8130 rejects a single repetition above 2000, then can silently sample
without a grammar. Factor the SAME native JSON-character language into bounded
repetitions. This is a compiler input, not a replacement validation schema:
callers retain the original Pydantic schema and strict JSON validation.
"""

from __future__ import annotations

from copy import deepcopy

# Matches the pinned converter's `char` rule, including JSON escapes. Its pattern
# branch operates on serialized string content, rather than decoded Python text.
_CHAR = r'([^"\\\x7F\x00-\x1F]|\\(["\\bfnrt]|u[0-9a-fA-F]{4}))'
_BLOCK = 1000


def _repeat(low: int, high: int) -> str:
    block = f"({_CHAR}{{{_BLOCK}}})"
    required, remainder = divmod(low, _BLOCK)
    prefix = f"{block}{{{required}}}{_CHAR}{{{remainder}}}"
    optional, remainder = divmod(high - low, _BLOCK)
    if not optional:
        suffix = f"{_CHAR}{{0,{remainder}}}"
    else:
        # Each length has one partition into full blocks and a final partial
        # block. Independent optional blocks cause pathological backtracking.
        suffix = (
            f"({block}{{0,{optional - 1}}}{_CHAR}{{0,{_BLOCK - 1}}}"
            f"|{block}{{{optional}}}{_CHAR}{{0,{remainder}}})"
        )
    return "^" + prefix + suffix + "$"


def native_schema(schema: dict) -> dict:
    """Preserve native bounds without exceeding its per-repetition parser cap.

    Reject ambiguous combinations instead of discarding constraints. The only
    production schemas needing lowering are bounded free-text questions.
    """
    lowered = deepcopy(schema)

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            maximum = node.get("maxLength")
            if node.get("type") == "string" and maximum is not None and maximum > 2000:
                minimum = node.get("minLength", 0)
                if (
                    "pattern" in node
                    or "format" in node
                    or type(maximum) is not int
                    or type(minimum) is not int
                    or not 0 <= minimum <= maximum <= 16000
                ):
                    raise ValueError("Unsupported native long-string constraint")
                node.pop("maxLength")
                node.pop("minLength", None)
                node["pattern"] = _repeat(minimum, maximum)
            for item in node.values():
                visit(item)

    visit(lowered)
    return lowered
