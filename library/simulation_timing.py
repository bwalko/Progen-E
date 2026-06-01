"""Optional wall-clock aggregation for population simulation (env-gated).

Set ``HISTORY_SIM_PROFILE_LAST_N_YEARS`` to a positive integer before running
:func:`library.population_growth_runner.run_population_growth_simulation` to
record ``perf_counter`` sums for the last N calendar years only (cheap early
years, detailed late-year breakdown).
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

_profile_from_year: int | None = None
_profile_year_count: int = 0
_sums: dict[str, float] = defaultdict(float)
_gauges: list["ProfileGauge"] = []


@dataclass(frozen=True)
class ProfilePhase:
    phase: str
    seconds: float
    percent: float


@dataclass(frozen=True)
class ProfileSnapshot:
    from_year: int
    year_count: int
    total_seconds: float
    phases: tuple[ProfilePhase, ...]


@dataclass(frozen=True)
class ProfileGauge:
    year: int
    label: str
    metric: str
    value: float


def configure_profile_window(*, start_year: int, duration_years: int) -> None:
    global _profile_from_year, _profile_year_count, _sums, _gauges
    _sums.clear()
    _gauges.clear()
    raw = os.environ.get("HISTORY_SIM_PROFILE_LAST_N_YEARS")
    if raw is None or str(raw).strip() == "":
        _profile_from_year = None
        _profile_year_count = 0
        return
    n = max(0, int(raw))
    end_exclusive = int(start_year) + int(duration_years)
    _profile_from_year = max(int(start_year), end_exclusive - n)
    _profile_year_count = end_exclusive - _profile_from_year


def active_for_year(year: int) -> bool:
    return _profile_from_year is not None and int(year) >= int(_profile_from_year)


def enabled() -> bool:
    """Return whether the env-gated timing profiler is configured."""
    return _profile_from_year is not None


def accumulate(phase: str, seconds: float) -> None:
    _sums[phase] += float(seconds)


def record_gauge(year: int, label: str, metric: str, value: int | float) -> None:
    """Record a numeric scale metric for profiled years."""
    if not active_for_year(year):
        return
    _gauges.append(
        ProfileGauge(
            year=int(year),
            label=str(label),
            metric=str(metric),
            value=float(value),
        )
    )


def gauge_rows_if_configured() -> tuple[ProfileGauge, ...]:
    if _profile_from_year is None:
        return ()
    return tuple(_gauges)


def snapshot_if_configured() -> ProfileSnapshot | None:
    if _profile_from_year is None or _profile_year_count <= 0:
        return None
    if not _sums:
        return None
    total = sum(_sums.values())
    if total <= 0:
        return None
    phases = tuple(
        ProfilePhase(
            phase=phase,
            seconds=_sums[phase],
            percent=100.0 * _sums[phase] / total,
        )
        for phase in sorted(_sums.keys(), key=lambda k: -_sums[k])
    )
    return ProfileSnapshot(
        from_year=int(_profile_from_year),
        year_count=int(_profile_year_count),
        total_seconds=float(total),
        phases=phases,
    )


def print_report_if_configured() -> None:
    snapshot = snapshot_if_configured()
    if snapshot is None:
        return
    lines = [
        "",
        f"HISTORY_SIM_PROFILE_LAST_N_YEARS: {snapshot.year_count} years "
        f"(sim years >= {snapshot.from_year})",
        f"Total profiled CPU time: {snapshot.total_seconds:.3f}s "
        f"({snapshot.total_seconds / snapshot.year_count:.4f}s / year)",
        "Phase (sum s)  (% of profiled total)",
    ]
    for phase in snapshot.phases:
        lines.append(f"  {phase.phase:40s} {phase.seconds:10.3f}  {phase.percent:5.1f}%")
    print("\n".join(lines))
