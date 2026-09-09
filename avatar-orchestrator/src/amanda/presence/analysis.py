"""Diagnostics for the thing the eye notices but cannot name.

The build plan's test question is "what movement drew attention to itself as
animation?" Two answers dominate: timings that fall into a rhythm, and
sequences that repeat. Both are measurable, and measuring them is more reliable
than watching for a minute and deciding it felt fine.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntervalReport:
    count: int
    mean: float
    cv: float
    rhythm: float

    @property
    def verdict(self) -> str:
        if self.count < 6:
            return "not enough data"
        if self.rhythm > 0.7:
            return "metronomic"
        if self.rhythm > 0.4:
            return "noticeable rhythm"
        return "irregular"


def intervals_between(times: Sequence[float]) -> list[float]:
    return [later - earlier for earlier, later in zip(times, times[1:], strict=False)]


def interval_report(times: Sequence[float]) -> IntervalReport:
    """How metronomic a sequence of event times is.

    Coefficient of variation is the useful statistic: a perfectly regular
    sequence has cv 0, and human inter-blink intervals sit somewhere around
    0.5-0.8. `rhythm` rescales that so 0 is comfortably irregular and 1 is a
    metronome, because "cv 0.18" means nothing at a glance.
    """
    gaps = intervals_between(times)
    if len(gaps) < 2:
        return IntervalReport(count=len(times), mean=0.0, cv=0.0, rhythm=0.0)

    mean = statistics.fmean(gaps)
    if mean <= 0:
        return IntervalReport(count=len(times), mean=0.0, cv=0.0, rhythm=1.0)

    cv = statistics.stdev(gaps) / mean
    rhythm = max(0.0, min(1.0, 1.0 - cv / 0.5))
    return IntervalReport(count=len(times), mean=mean, cv=cv, rhythm=rhythm)


@dataclass(frozen=True, slots=True)
class RepetitionReport:
    length: int
    pattern: tuple[str, ...]
    span: int
    baseline: float

    @property
    def excess(self) -> float:
        """How much longer the repeat is than chance alone would produce."""
        return self.length - self.baseline

    @property
    def verdict(self) -> str:
        if self.span < 12:
            return "not enough data"
        if self.excess >= 4:
            return "visibly repeating"
        if self.excess >= 2:
            return "borderline"
        return "within chance"


def _chance_baseline(span: int, alphabet: int) -> float:
    """Longest repeated run expected from a genuinely random sequence.

    Without this the metric is useless: draw a few hundred symbols from five
    options and some run of six will repeat, every time. What matters is
    whether the repeat is longer than chance, and by how much. The standard
    estimate for the longest repeated substring of a random sequence is
    2 log_k(n).
    """
    if span < 4 or alphabet < 2:
        return 0.0
    return 2.0 * math.log(span) / math.log(alphabet)


def longest_repeated_run(sequence: Sequence[str]) -> RepetitionReport:
    """The longest contiguous run that occurs more than once.

    A gaze controller cycling left, user, right, user is not random even though
    each individual choice was -- and a viewer picks that up long before they
    could describe it. Quadratic, which is fine for the few hundred entries a
    session produces.
    """
    n = len(sequence)
    best: tuple[str, ...] = ()
    for length in range(min(n // 2, 16), 1, -1):
        seen: dict[tuple[str, ...], int] = {}
        for start in range(n - length + 1):
            window = tuple(sequence[start : start + length])
            if window in seen and start - seen[window] >= length:
                best = window
                break
            seen.setdefault(window, start)
        if best:
            break
    return RepetitionReport(
        length=len(best),
        pattern=best,
        span=n,
        baseline=_chance_baseline(n, len(set(sequence))),
    )


def dwell_fractions(samples: Sequence[tuple[float, str]]) -> dict[str, float]:
    """Fraction of *time* spent on each value, from (timestamp, value) samples.

    Eye contact is a time fraction, not a count of glances -- a controller can
    look at the user on a third of its shifts and still hold their gaze most of
    the conversation. Counting shifts measures the wrong thing.
    """
    totals: dict[str, float] = {}
    for (start, value), (end, _) in zip(samples, samples[1:], strict=False):
        totals[value] = totals.get(value, 0.0) + max(0.0, end - start)
    span = sum(totals.values())
    if span <= 0:
        return {}
    return {value: total / span for value, total in sorted(totals.items())}
