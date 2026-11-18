"""Honest deterministic date projection; ambiguous/approximate dates stay unsorted.

Years use historical signed notation (44 BCE = -44, no year zero).
Ranges retain both endpoints. No Python datetime conversion or invented days.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from workbench.tools.utility.dates import normalize_date


@dataclass(frozen=True)
class HistoricalDate:
    original: str
    start: tuple[int, int, int] | None = None
    end: tuple[int, int, int] | None = None
    precision: str = "unknown"
    approximate: bool = False


def historical_date(text: str) -> HistoricalDate:
    approximate = bool(
        re.search(
            r"\b(circa|c\.|ca\.|about|around|early|late|mid|approximately)\s|[?~]",
            text,
            re.I,
        )
    )
    # Year ranges, with trailing era applying to both unmarked endpoints.
    match = re.fullmatch(
        r"\s*(\d{1,4})\s*(BCE?|CE|AD)?\s*[-–—]\s*(\d{1,4})\s*(BCE?|CE|AD)?\s*",
        text,
        re.I,
    )
    if match:
        a, era_a, b, era_b = match.groups()
        era_a = (era_a or era_b or "AD").upper()
        era_b = (era_b or era_a).upper()
        start = int(a) * (-1 if era_a.startswith("BC") else 1)
        end = int(b) * (-1 if era_b.startswith("BC") else 1)
        if start and end and start <= end:
            return HistoricalDate(text, (start, 0, 0), (end, 0, 0), "range")
        return HistoricalDate(text)
    # Do not pass uncertain expressions or unrecognized ranges into a helper
    # that can erase modifiers. Preserve the original as an unordered label.
    if approximate:
        return HistoricalDate(text, approximate=True)
    if text.lstrip().startswith("-"):
        return HistoricalDate(text)
    d = normalize_date(text)
    y, m, day = d.get("year"), d.get("month"), d.get("day")
    if d["error"] or not y:
        return HistoricalDate(text)
    if d["precision"] == "decade":
        return HistoricalDate(text, precision="decade")
    if (m and not 1 <= m <= 12) or (day and not 1 <= day <= 31):
        return HistoricalDate(text)
    if m and day:
        days = [
            31,
            29 if abs(y) % 4 == 0 and (abs(y) % 100 != 0 or abs(y) % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ]
        if day > days[m - 1]:
            return HistoricalDate(text)
    return HistoricalDate(text, (y, m or 0, day or 0), precision=d["precision"])


def chronological_findings(findings):
    """Order only provably disjoint intervals; preserve input preference for ties."""
    dated = [f for f in findings if f.date_text]
    parsed = [historical_date(f.date_text) for f in dated]

    def interval(d):
        if d.start is None or d.approximate:
            return None
        y, m, day = d.start
        return d.start, d.end or (y, m or 12, day or 31)

    bounds = [interval(d) for d in parsed]
    # Stable topological order of provably disjoint intervals. Overlapping and
    # unknown expressions establish no order; retain input preference for ties.
    remaining = list(range(len(dated)))
    ordered = []
    while remaining:
        i = next(
            i
            for i in remaining
            if not any(
                j != i and bounds[j] and bounds[i] and bounds[j][1] < bounds[i][0]
                for j in remaining
            )
        )
        ordered.append(dated[i])
        remaining.remove(i)
    return ordered
