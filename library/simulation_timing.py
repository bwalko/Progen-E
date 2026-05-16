"""Optional wall-clock aggregation for population simulation (env-gated).

Set ``HISTORY_SIM_PROFILE_LAST_N_YEARS`` to a positive integer before running
:func:`library.population_growth_runner.run_population_growth_simulation` to
record ``perf_counter`` sums for the last N calendar years only (cheap early
years, detailed late-year breakdown).
"""

from __future__ import annotations

import os
from collections import defaultdict

_profile_from_year: int | None = None
_profile_year_count: int = 0
_sums: dict[str, float] = defaultdict(float)


def configure_profile_window(*, start_year: int, duration_years: int) -> None:
    global _profile_from_year, _profile_year_count, _sums
    _sums.clear()
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


def accumulate(phase: str, seconds: float) -> None:
    _sums[phase] += float(seconds)


def print_report_if_configured() -> None:
    if _profile_from_year is None or _profile_year_count <= 0:
        return
    if not _sums:
        return
    total = sum(_sums.values())
    if total <= 0:
        return
    lines = [
        "",
        f"HISTORY_SIM_PROFILE_LAST_N_YEARS: {_profile_year_count} years "
        f"(sim years >= {_profile_from_year})",
        f"Total profiled CPU time: {total:.3f}s ({total / _profile_year_count:.4f}s / year)",
        "Phase (sum s)  (% of profiled total)",
    ]
    for phase in sorted(_sums.keys(), key=lambda k: -_sums[k]):
        s = _sums[phase]
        pct = 100.0 * s / total
        lines.append(f"  {phase:40s} {s:10.3f}  {pct:5.1f}%")
    print("\n".join(lines))
