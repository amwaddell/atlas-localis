"""Narrow lexical support checks, never a general historical entailment judge.

These reject absent numbers, names and quotations, and selected certainty
upgrades. Passing leaves relation, scope, negation and historical truth for
semantic review. Only the explicitly cited sources are considered.
"""

from __future__ import annotations

import re
import unicodedata

_CONNECTIVES = frozenset(
    "a an the this that these those it its he his she her they their there "
    "in on at by for from to as after before during while although however "
    "both some many most no according".split()
)
_CERTAINTY = frozenset(
    "attested documented proven established definitive officially certainly".split()
)
_WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[,.]\d+)*")
_QUOTES = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’')


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _name_key(word: str) -> str:
    # The corpus and follow-ups vary in macrons/accents and possessives.
    # This equivalence is for lexical name cues only, never exact quotations.
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFKD", word).casefold()
            if not unicodedata.combining(c)
        )
        .removesuffix("’s")
        .removesuffix("'s")
    )


def support_errors(statement: str, sources: tuple[str, ...]) -> tuple[str, ...]:
    """Return bounded categories without echoing invalid prose into repair."""
    source = normalized(" ".join(sources))
    words = {_name_key(m.group()) for m in _WORD.finditer(source)}
    errors = []
    source_numbers = {m.group() for m in _NUMBER.finditer(source)}
    if any(m.group() not in source_numbers for m in _NUMBER.finditer(statement)):
        errors.append("numeric detail absent from cited text")
    for match in _WORD.finditer(statement):
        word = match.group()
        folded = _name_key(word)
        # Capitalization is only a conservative name cue, not entity recognition.
        # The first word may be an ordinary capitalized noun (e.g. Reforms).
        if (
            match.start() > 0
            and len(word) > 1
            and word[0].isupper()
            and folded not in _CONNECTIVES
            and folded not in words
        ):
            errors.append("capitalized detail absent from cited text")
            break
    if any(
        normalized(next(g for g in match.groups() if g is not None)) not in source
        for match in _QUOTES.finditer(statement)
    ):
        errors.append("quotation absent from cited text")
    claimed = {m.group().casefold() for m in _WORD.finditer(statement)}
    if (claimed & _CERTAINTY) - words:
        errors.append("stronger certainty wording absent from cited text")
    return tuple(errors)
