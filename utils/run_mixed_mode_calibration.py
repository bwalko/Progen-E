"""Run full mixed-mode population calibration scenarios.

Unlike ``run_mixed_mode_scale_smoke.py``, this utility calls the canonical
population-growth runner so yearly careers, government, economy, reports, passive
cohorts, and promotion hooks all execute. It keeps detailed people bounded while
scaling aggregate passive population toward large historical targets.
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.config_import import load_all_csvs_into_sqlite  # noqa: E402
from library.detailed_population_variance import detailed_selection_profile  # noqa: E402
from library.event_history_report import (  # noqa: E402
    SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE,
    SERIAL_MURDER_MIN_MURDER_SAMPLE,
    SERIAL_MURDER_TARGET_SHARE_MAX,
    build_event_history_report,
)
from library.geography import list_regions  # noqa: E402
from library.incident_rates import incident_rate_for_year  # noqa: E402
from library.population_growth_runner import run_population_growth_simulation  # noqa: E402
from library.settlements import SettlementState  # noqa: E402
from library.simulation_context import SimulationContext  # noqa: E402
from library.world_save import ensure_checkpoint_schema, _trait_slots_from_config  # noqa: E402

_DEFAULT_TARGETS = (100_000, 1_000_000, 10_000_000)
_DEFAULT_START_YEAR = 1000
_DEFAULT_YEARS = 10
_DEFAULT_STARTING_COUPLES = 10
_DEFAULT_DETAILED_FRACTION = 0.001
_DEFAULT_MIN_DETAILED_CAP = 200
_DEFAULT_MAX_DETAILED_CAP = 2_000
_DEFAULT_OUTPUT = _ROOT / "temp" / "mixed_mode_calibration.tsv"
_MURDER_RATE_MIN_MURDER_SAMPLE = 10
_REASON_VARIANCE_MIN_SAMPLE = 10
_SERIAL_PROFILE_MIN_SCORED_SAMPLE = 100
_SERIAL_PROFILE_TARGET_SHARE_MAX = 0.02
_HYBRID_STATUS_ALIASES: dict[str, tuple[str, ...]] = {
    "calibrated": ("within_hybrid_calibration_targets",),
    "ready": (
        "within_hybrid_calibration_targets",
        "serial_murder_not_emerging",
        "serial_murder_too_common",
    ),
}


def _parse_targets(raw: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        s = part.strip().replace("_", "")
        if not s:
            continue
        n = int(s)
        if n < 1:
            raise ValueError("targets must be positive integers")
        out.append(n)
    if not out:
        raise ValueError("at least one target is required")
    return tuple(out)


def _detailed_cap_for_target(
    target: int,
    *,
    fraction: float,
    min_cap: int,
    max_cap: int,
) -> int:
    estimated = int(round(int(target) * float(fraction)))
    return max(int(min_cap), min(int(max_cap), max(1, estimated)))


def _scenario_plan(
    targets: tuple[int, ...],
    *,
    replicates: int,
    seed: int,
) -> list[dict[str, int]]:
    scenarios: list[dict[str, int]] = []
    scenario_index = 0
    for target_index, target in enumerate(targets):
        for replicate_index in range(int(replicates)):
            scenarios.append(
                {
                    "scenario_index": scenario_index,
                    "target_index": target_index,
                    "replicate_index": replicate_index,
                    "target_population": int(target),
                    "sim_seed": int(seed) + scenario_index,
                }
            )
            scenario_index += 1
    return scenarios


def _parse_hybrid_stop_statuses(raw: str | None) -> tuple[str, ...]:
    statuses: list[str] = []
    for part in str(raw or "").replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        statuses.extend(_HYBRID_STATUS_ALIASES.get(token, (token,)))
    return tuple(dict.fromkeys(statuses))


def _should_stop_for_hybrid_status(
    summary: dict[str, object],
    *,
    stop_statuses: tuple[str, ...],
    min_scenarios: int,
) -> bool:
    if not stop_statuses:
        return False
    if int(summary.get("scenario_count") or 0) < max(1, int(min_scenarios)):
        return False
    return str(summary.get("hybrid_calibration_status") or "") in set(stop_statuses)


def _should_stop_for_sample_thresholds(
    summary: dict[str, object],
    *,
    min_scenarios: int,
    stop_after_total_murders: int | None = None,
    stop_after_detailed_person_years: int | None = None,
) -> bool:
    if int(summary.get("scenario_count") or 0) < max(1, int(min_scenarios)):
        return False
    if (
        stop_after_total_murders is not None
        and int(summary.get("total_murder_events") or 0)
        >= int(stop_after_total_murders)
    ):
        return True
    if (
        stop_after_detailed_person_years is not None
        and int(summary.get("total_detailed_person_years") or 0)
        >= int(stop_after_detailed_person_years)
    ):
        return True
    return False


def _latest_mixed_counts(ctx: SimulationContext, year: int) -> dict[str, int]:
    latest_cohort_year = max((int(c.sim_year) for c in ctx.passive_cohorts), default=None)
    latest_cohorts = [
        c
        for c in ctx.passive_cohorts
        if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
    ]
    detailed_alive = len(ctx.current_people_ids)
    passive_person_alive = sum(
        1
        for rec in ctx.passive_people.values()
        if rec.person.deathyear is None or int(rec.person.deathyear) > int(year)
    )
    aggregate_alive = sum(int(c.population_count) for c in latest_cohorts)
    aggregate_births = sum(int(c.birth_count) for c in latest_cohorts)
    aggregate_deaths = sum(int(c.death_count) for c in latest_cohorts)
    aggregate_partnered = sum(
        int(c.population_count)
        for c in latest_cohorts
        if (c.status_bucket or "").strip().lower() == "partnered"
    )
    return {
        "detailed_alive": detailed_alive,
        "passive_person_alive": passive_person_alive,
        "aggregate_cohort_alive": aggregate_alive,
        "aggregate_cohort_births": aggregate_births,
        "aggregate_cohort_deaths": aggregate_deaths,
        "aggregate_cohort_partnered": aggregate_partnered,
        "mixed_mode_alive": detailed_alive + passive_person_alive + aggregate_alive,
        "cohort_rows": len(latest_cohorts),
        "promotion_count": sum(
            1
            for _, event_type, _ in ctx._pending_simulation_events
            if event_type == "passive_person_promoted"
        ),
    }


def _detailed_person_years_from_rows(rows: list[dict[str, object]]) -> int:
    total = 0
    for row in rows:
        value = row.get("detailed_alive_count")
        if value is None or value == "":
            continue
        try:
            total += max(0, int(value))
        except (TypeError, ValueError):
            continue
    return total


def _detailed_person_years_from_file_store(file_store: object) -> int:
    rows: list[dict[str, object]] = []
    rows.extend(list(getattr(file_store, "_yearly_summary_rows", ()) or ()))
    root_dir = getattr(file_store, "root_dir", None)
    if root_dir is not None:
        path = Path(root_dir) / "yearly_summary.csv"
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                rows.extend(dict(row) for row in csv.DictReader(f))
    return _detailed_person_years_from_rows(rows)


def _read_tsv_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="\t")]


def _completed_scenario_indexes(rows: list[dict[str, object]]) -> set[int]:
    out: set[int] = set()
    for row in rows:
        scenario_index = _optional_int(row.get("scenario_index"))
        if scenario_index is not None:
            out.add(int(scenario_index))
    return out


def _completed_matching_scenario_indexes(
    rows: list[dict[str, object]],
    scenarios: list[dict[str, int]],
) -> set[int]:
    planned = {int(s["scenario_index"]): s for s in scenarios}
    out: set[int] = set()
    for row in rows:
        scenario_index = _optional_int(row.get("scenario_index"))
        if scenario_index is None:
            continue
        scenario = planned.get(int(scenario_index))
        if scenario is None:
            continue
        if (
            _optional_int(row.get("target_index")) == int(scenario["target_index"])
            and _optional_int(row.get("replicate_index"))
            == int(scenario["replicate_index"])
            and _optional_int(row.get("target_population"))
            == int(scenario["target_population"])
            and _optional_int(row.get("sim_seed")) == int(scenario["sim_seed"])
        ):
            out.add(int(scenario_index))
    return out


def _hybrid_calibration_fields(
    save_path: Path,
    *,
    trait_slots: tuple[str, ...] = (),
    murder_target_per_10k: float | None = None,
    detailed_person_years: int | None = None,
) -> dict[str, object]:
    with closing(sqlite3.connect(save_path)) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        conn.commit()
        report = build_event_history_report(
            conn,
            save_path=save_path,
            sample_limit=0,
            trait_slots=trait_slots,
        )
    h = report.hybrid_population_calibration
    exposure_person_years = (
        max(0, int(detailed_person_years))
        if detailed_person_years is not None
        else max(0, int(h.detailed_alive_people)) * max(0, int(h.event_year_span))
    )
    murder_rate = (
        float(h.murder_events) / float(exposure_person_years) * 10_000.0
        if exposure_person_years > 0
        else None
    )
    murder_ratio = (
        float(murder_rate) / float(murder_target_per_10k)
        if murder_rate is not None
        and murder_target_per_10k is not None
        and float(murder_target_per_10k) > 0.0
        else None
    )
    return {
        "report_detailed_people": h.detailed_people,
        "report_detailed_alive_people": h.detailed_alive_people,
        "report_non_detailed_alive_people": h.non_detailed_alive_people,
        "high_variance_detail_people": h.high_variance_detail_people,
        "genome_scored_detailed_people": h.genome_scored_detailed_people,
        "extreme_detail_people": h.extreme_detail_people,
        "average_detail_variance_score": _format_optional_float(
            h.average_detail_variance_score
        ),
        "serial_predator_profile_people": h.serial_predator_profile_people,
        "serial_predator_profile_share": _format_optional_float(
            h.serial_predator_profile_share
        ),
        "average_serial_predator_propensity": _format_optional_float(
            h.average_serial_predator_propensity
        ),
        "max_serial_predator_propensity": _format_optional_float(
            h.max_serial_predator_propensity
        ),
        "event_year_span": h.event_year_span,
        "detailed_person_years": exposure_person_years,
        "murder_events": h.murder_events,
        "serial_predator_candidate_events": h.serial_predator_candidate_events,
        "distinct_murder_killers": h.distinct_murder_killers,
        "repeat_murder_killers_2plus": h.repeat_murder_killers_2plus,
        "serial_murder_killers_3plus": h.serial_murder_killers_3plus,
        "serial_murder_events_by_3plus_killers": h.serial_murder_events_by_3plus_killers,
        "murder_per_10k_detailed_person_years": _format_optional_float(
            murder_rate
        ),
        "murder_target_per_10k_per_year": _format_optional_float(
            murder_target_per_10k
        ),
        "murder_rate_target_ratio": _format_optional_float(murder_ratio),
        "murder_rate_calibration_status": _murder_rate_calibration_status(
            observed_per_10k=murder_rate,
            target_per_10k=murder_target_per_10k,
            murder_events=h.murder_events,
        ),
        "serial_candidate_share_of_murders": _format_optional_float(
            h.serial_candidate_share_of_murders
        ),
        "serial_murder_event_share_3plus": _format_optional_float(
            h.serial_murder_event_share_3plus
        ),
        "serial_murder_target_share_max": _format_optional_float(
            h.serial_murder_target_share_max
        ),
        "serial_murder_calibration_status": h.serial_murder_calibration_status,
        "serial_murder_emergence_min_murder_sample": (
            h.serial_murder_emergence_min_murder_sample
        ),
        "serial_murder_emergence_status": h.serial_murder_emergence_status,
    }


def _hybrid_reason_variance_rows(
    save_path: Path,
    *,
    trait_slots: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    with closing(sqlite3.connect(save_path)) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        conn.commit()
        report = build_event_history_report(
            conn,
            save_path=save_path,
            sample_limit=0,
            trait_slots=trait_slots,
        )
    return [
        _reason_variance_output_row(
            reason=row.reason,
            detailed_people=int(row.detailed_people),
            high_variance_detail_people=int(row.high_variance_detail_people),
            genome_scored_detailed_people=int(row.genome_scored_detailed_people),
            extreme_detail_people=int(row.extreme_detail_people),
            average_detail_variance_score=row.average_detail_variance_score,
        )
        for row in report.hybrid_variance_by_promotion_reason
    ]


def _format_optional_float(value: float | None) -> str:
    return "" if value is None else f"{float(value):.6f}"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _murder_rate_calibration_status(
    *,
    observed_per_10k: float | None,
    target_per_10k: float | None,
    murder_events: int,
) -> str:
    if observed_per_10k is None or target_per_10k is None or float(target_per_10k) <= 0.0:
        return "target_unavailable"
    if int(murder_events) < _MURDER_RATE_MIN_MURDER_SAMPLE:
        return "insufficient_murder_sample"
    ratio = float(observed_per_10k) / float(target_per_10k)
    if ratio < 0.50:
        return "below_target_band"
    if ratio > 1.50:
        return "above_target_band"
    return "within_target_band"


def _serial_murder_calibration_status(*, murder_events: int, share: float | None) -> str:
    if int(murder_events) < SERIAL_MURDER_MIN_MURDER_SAMPLE:
        return "insufficient_murder_sample"
    if share is not None and float(share) <= SERIAL_MURDER_TARGET_SHARE_MAX:
        return "within_real_life_guardrail"
    return "above_real_life_guardrail"


def _serial_murder_emergence_status(
    *,
    murder_events: int,
    serial_murder_killers_3plus: int,
    guardrail_status: str,
) -> str:
    if int(murder_events) < SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE:
        return "insufficient_emergence_sample"
    if int(serial_murder_killers_3plus) <= 0:
        return "no_serial_murder_emerged"
    if str(guardrail_status) == "above_real_life_guardrail":
        return "above_real_life_guardrail"
    return "serial_murder_emerged"


def _serial_predator_profile_calibration_status(
    *,
    scored_people: int,
    profile_people: int,
    profile_share: float | None,
) -> str:
    if int(scored_people) < _SERIAL_PROFILE_MIN_SCORED_SAMPLE:
        return "insufficient_profile_sample"
    if int(profile_people) <= 0:
        return "no_serial_predator_profiles"
    if profile_share is not None and float(profile_share) > _SERIAL_PROFILE_TARGET_SHARE_MAX:
        return "serial_predator_profiles_too_common"
    return "serial_predator_profiles_present"


def _overall_hybrid_calibration_status(summary: dict[str, object]) -> str:
    murder_status = str(summary.get("murder_rate_calibration_status") or "")
    profile_status = str(
        summary.get("serial_predator_profile_calibration_status") or ""
    )
    serial_status = str(summary.get("serial_murder_calibration_status") or "")
    emergence_status = str(summary.get("serial_murder_emergence_status") or "")

    if murder_status in {"target_unavailable", "insufficient_murder_sample"}:
        return "needs_more_murder_sample"
    if murder_status == "below_target_band":
        return "retune_murder_rate_below_target"
    if murder_status == "above_target_band":
        return "retune_murder_rate_above_target"
    if profile_status == "insufficient_profile_sample":
        return "needs_more_serial_profile_sample"
    if profile_status == "no_serial_predator_profiles":
        return "retune_serial_predator_profiles_absent"
    if profile_status == "serial_predator_profiles_too_common":
        return "retune_serial_predator_profiles_too_common"
    if serial_status == "insufficient_murder_sample":
        return "needs_more_serial_guardrail_sample"
    if emergence_status == "insufficient_emergence_sample":
        return "needs_more_serial_emergence_sample"
    if serial_status == "above_real_life_guardrail":
        return "serial_murder_too_common"
    if emergence_status == "no_serial_murder_emerged":
        return "serial_murder_not_emerging"
    if (
        murder_status == "within_target_band"
        and profile_status == "serial_predator_profiles_present"
        and serial_status == "within_real_life_guardrail"
        and emergence_status == "serial_murder_emerged"
    ):
        return "within_hybrid_calibration_targets"
    return "review_required"


def _next_calibration_run_hint(summary: dict[str, object]) -> dict[str, object]:
    status = str(summary.get("hybrid_calibration_status") or "")
    if status == "needs_more_murder_sample":
        return {
            "recommended_next_calibration_reason": "reach_murder_rate_sample",
            "recommended_next_calibration_stop_flag": "--stop-after-total-murders",
            "recommended_next_calibration_stop_value": _MURDER_RATE_MIN_MURDER_SAMPLE,
            "recommended_next_calibration_resume_flag": "--resume-existing",
        }
    if status == "needs_more_serial_guardrail_sample":
        return {
            "recommended_next_calibration_reason": "reach_serial_guardrail_sample",
            "recommended_next_calibration_stop_flag": "--stop-after-total-murders",
            "recommended_next_calibration_stop_value": SERIAL_MURDER_MIN_MURDER_SAMPLE,
            "recommended_next_calibration_resume_flag": "--resume-existing",
        }
    if status == "needs_more_serial_emergence_sample":
        return {
            "recommended_next_calibration_reason": "reach_serial_emergence_sample",
            "recommended_next_calibration_stop_flag": "--stop-after-total-murders",
            "recommended_next_calibration_stop_value": (
                SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE
            ),
            "recommended_next_calibration_resume_flag": "--resume-existing",
        }
    return {
        "recommended_next_calibration_reason": "",
        "recommended_next_calibration_stop_flag": "",
        "recommended_next_calibration_stop_value": "",
        "recommended_next_calibration_resume_flag": "",
    }


def _weighted_average(
    weighted_values: list[tuple[float, float]],
) -> float | None:
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight <= 0.0:
        return None
    return sum(value * weight for value, weight in weighted_values) / total_weight


def _murder_sample_projection_rate(
    *,
    murder_events: int,
    observed_per_10k: float | None,
    target_per_10k: float | None,
) -> tuple[float | None, str]:
    if (
        int(murder_events) >= _MURDER_RATE_MIN_MURDER_SAMPLE
        and observed_per_10k is not None
        and float(observed_per_10k) > 0.0
    ):
        return float(observed_per_10k), "observed"
    if target_per_10k is not None and float(target_per_10k) > 0.0:
        return float(target_per_10k), "target"
    return None, "unavailable"


def _project_additional_person_years_for_murder_sample(
    *,
    remaining_murders: int,
    projection_rate_per_10k: float | None,
) -> int | None:
    remaining = max(0, int(remaining_murders))
    if remaining == 0:
        return 0
    if projection_rate_per_10k is None or float(projection_rate_per_10k) <= 0.0:
        return None
    return int(math.ceil(float(remaining) * 10_000.0 / float(projection_rate_per_10k)))


def _project_additional_scenarios_for_person_years(
    *,
    additional_person_years: int | None,
    current_person_years: int,
    scenario_count: int,
) -> int | None:
    if additional_person_years is None:
        return None
    if int(additional_person_years) <= 0:
        return 0
    if int(current_person_years) <= 0 or int(scenario_count) <= 0:
        return None
    average_person_years = float(current_person_years) / float(scenario_count)
    if average_person_years <= 0.0:
        return None
    return int(math.ceil(float(additional_person_years) / average_person_years))


def _reason_variance_expectation(reason: str) -> dict[str, object]:
    profile = detailed_selection_profile(reason)
    target = (
        0.36
        + profile.intensity * 0.50
        + profile.extra_trait_chance * 0.10
        + profile.predatory_bias_chance * 0.05
        - profile.center_chance * 0.08
    )
    target = max(0.28, min(0.82, target))
    return {
        "selection_profile": profile.key,
        "expected_average_detail_variance_min": _format_optional_float(
            max(0.20, target - 0.14)
        ),
        "expected_average_detail_variance_max": _format_optional_float(
            min(0.95, target + 0.14)
        ),
    }


def _reason_variance_calibration_status(
    *,
    scored_people: int,
    average_score: float | None,
    expected_min: float | None,
    expected_max: float | None,
) -> str:
    if average_score is None or expected_min is None or expected_max is None:
        return "no_variance_score"
    if int(scored_people) < _REASON_VARIANCE_MIN_SAMPLE:
        return "insufficient_reason_sample"
    if float(average_score) < float(expected_min):
        return "below_profile_floor"
    if float(average_score) > float(expected_max):
        return "above_profile_ceiling"
    return "within_profile_band"


def _reason_variance_output_row(
    *,
    reason: str,
    detailed_people: int,
    high_variance_detail_people: int,
    genome_scored_detailed_people: int,
    extreme_detail_people: int,
    average_detail_variance_score: float | None,
) -> dict[str, object]:
    expectation = _reason_variance_expectation(reason)
    expected_min = _optional_float(expectation["expected_average_detail_variance_min"])
    expected_max = _optional_float(expectation["expected_average_detail_variance_max"])
    return {
        "reason": reason,
        "selection_profile": expectation["selection_profile"],
        "detailed_people": int(detailed_people),
        "high_variance_detail_people": int(high_variance_detail_people),
        "genome_scored_detailed_people": int(genome_scored_detailed_people),
        "extreme_detail_people": int(extreme_detail_people),
        "average_detail_variance_score": _format_optional_float(
            average_detail_variance_score
        ),
        "expected_average_detail_variance_min": expectation[
            "expected_average_detail_variance_min"
        ],
        "expected_average_detail_variance_max": expectation[
            "expected_average_detail_variance_max"
        ],
        "reason_variance_calibration_status": _reason_variance_calibration_status(
            scored_people=int(genome_scored_detailed_people),
            average_score=average_detail_variance_score,
            expected_min=expected_min,
            expected_max=expected_max,
        ),
    }


def _aggregate_calibration_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    person_years = 0
    target_person_years: list[tuple[float, float]] = []
    variance_people: list[tuple[float, float]] = []
    total_murders = 0
    total_serial_candidates = 0
    total_serial_3plus_killers = 0
    total_serial_3plus_events = 0
    total_genome_scored = 0
    total_high_variance = 0
    total_extreme = 0
    total_serial_profiles = 0
    serial_propensity_people: list[tuple[float, float]] = []
    max_serial_propensity: float | None = None
    total_non_detailed_alive = 0
    total_event_year_span = 0
    spinoff_disabled_count = 0
    targets: set[int] = set()
    seeds: set[int] = set()

    for row in rows:
        if str(row.get("birth_settlement_spinoff_disabled") or "").lower() == "yes":
            spinoff_disabled_count += 1
        target = _optional_int(row.get("target_population"))
        if target is not None:
            targets.add(target)
        sim_seed = _optional_int(row.get("sim_seed"))
        if sim_seed is not None:
            seeds.add(sim_seed)
        event_year_span = _optional_int(row.get("event_year_span")) or 0
        row_person_years = _optional_int(row.get("detailed_person_years"))
        if row_person_years is None:
            detailed_alive = _optional_int(row.get("report_detailed_alive_people")) or 0
            row_person_years = max(0, detailed_alive) * max(0, event_year_span)
        row_person_years = max(0, int(row_person_years))
        person_years += row_person_years
        total_event_year_span += max(0, event_year_span)

        target_rate = _optional_float(row.get("murder_target_per_10k_per_year"))
        if target_rate is not None and row_person_years > 0:
            target_person_years.append((target_rate, float(row_person_years)))

        scored = _optional_int(row.get("genome_scored_detailed_people")) or 0
        variance_score = _optional_float(row.get("average_detail_variance_score"))
        if variance_score is not None and scored > 0:
            variance_people.append((variance_score, float(scored)))
        avg_serial_propensity = _optional_float(
            row.get("average_serial_predator_propensity")
        )
        if avg_serial_propensity is not None and scored > 0:
            serial_propensity_people.append((avg_serial_propensity, float(scored)))
        row_max_serial_propensity = _optional_float(row.get("max_serial_predator_propensity"))
        if row_max_serial_propensity is not None:
            max_serial_propensity = (
                row_max_serial_propensity
                if max_serial_propensity is None
                else max(max_serial_propensity, row_max_serial_propensity)
            )

        total_murders += _optional_int(row.get("murder_events")) or 0
        total_serial_candidates += (
            _optional_int(row.get("serial_predator_candidate_events")) or 0
        )
        total_serial_3plus_killers += (
            _optional_int(row.get("serial_murder_killers_3plus")) or 0
        )
        total_serial_3plus_events += (
            _optional_int(row.get("serial_murder_events_by_3plus_killers")) or 0
        )
        total_genome_scored += scored
        total_high_variance += _optional_int(row.get("high_variance_detail_people")) or 0
        total_extreme += _optional_int(row.get("extreme_detail_people")) or 0
        total_serial_profiles += _optional_int(row.get("serial_predator_profile_people")) or 0
        total_non_detailed_alive += (
            _optional_int(row.get("report_non_detailed_alive_people")) or 0
        )

    observed_murder_rate = (
        (float(total_murders) / float(person_years)) * 10_000.0
        if person_years > 0
        else None
    )
    target_murder_rate = _weighted_average(target_person_years)
    murder_ratio = (
        observed_murder_rate / target_murder_rate
        if observed_murder_rate is not None
        and target_murder_rate is not None
        and target_murder_rate > 0.0
        else None
    )
    serial_candidate_share = (
        float(total_serial_candidates) / float(total_murders)
        if total_murders > 0
        else None
    )
    serial_3plus_share = (
        float(total_serial_3plus_events) / float(total_murders)
        if total_murders > 0
        else None
    )
    murder_rate_sample_remaining = max(
        0, _MURDER_RATE_MIN_MURDER_SAMPLE - total_murders
    )
    serial_murder_sample_remaining = max(
        0, SERIAL_MURDER_MIN_MURDER_SAMPLE - total_murders
    )
    serial_emergence_sample_remaining = max(
        0, SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE - total_murders
    )
    serial_guardrail_status = _serial_murder_calibration_status(
        murder_events=total_murders,
        share=serial_3plus_share,
    )
    serial_profile_share = (
        float(total_serial_profiles) / float(total_genome_scored)
        if total_genome_scored > 0
        else None
    )
    serial_profile_status = _serial_predator_profile_calibration_status(
        scored_people=total_genome_scored,
        profile_people=total_serial_profiles,
        profile_share=serial_profile_share,
    )
    projection_rate, projection_rate_source = _murder_sample_projection_rate(
        murder_events=total_murders,
        observed_per_10k=observed_murder_rate,
        target_per_10k=target_murder_rate,
    )
    serial_sample_projected_person_years = (
        _project_additional_person_years_for_murder_sample(
            remaining_murders=serial_murder_sample_remaining,
            projection_rate_per_10k=projection_rate,
        )
    )
    serial_emergence_projected_person_years = (
        _project_additional_person_years_for_murder_sample(
            remaining_murders=serial_emergence_sample_remaining,
            projection_rate_per_10k=projection_rate,
        )
    )
    summary = {
        "scenario_count": len(rows),
        "birth_settlement_spinoff_disabled_scenarios": spinoff_disabled_count,
        "distinct_target_count": len(targets),
        "distinct_seed_count": len(seeds),
        "total_event_year_span": total_event_year_span,
        "total_detailed_person_years": person_years,
        "total_report_non_detailed_alive_people": total_non_detailed_alive,
        "total_genome_scored_detailed_people": total_genome_scored,
        "total_high_variance_detail_people": total_high_variance,
        "total_extreme_detail_people": total_extreme,
        "weighted_average_detail_variance_score": _format_optional_float(
            _weighted_average(variance_people)
        ),
        "total_serial_predator_profile_people": total_serial_profiles,
        "serial_predator_profile_share_of_scored_detailed": _format_optional_float(
            serial_profile_share
        ),
        "serial_predator_profile_min_scored_sample": (
            _SERIAL_PROFILE_MIN_SCORED_SAMPLE
        ),
        "serial_predator_profile_sample_remaining": max(
            0, _SERIAL_PROFILE_MIN_SCORED_SAMPLE - total_genome_scored
        ),
        "serial_predator_profile_sample_ready": (
            "yes"
            if total_genome_scored >= _SERIAL_PROFILE_MIN_SCORED_SAMPLE
            else "no"
        ),
        "serial_predator_profile_target_share_max": _format_optional_float(
            _SERIAL_PROFILE_TARGET_SHARE_MAX
        ),
        "serial_predator_profile_calibration_status": serial_profile_status,
        "weighted_average_serial_predator_propensity": _format_optional_float(
            _weighted_average(serial_propensity_people)
        ),
        "max_serial_predator_propensity": _format_optional_float(max_serial_propensity),
        "total_murder_events": total_murders,
        "murder_rate_min_murder_sample": _MURDER_RATE_MIN_MURDER_SAMPLE,
        "murder_rate_murder_sample_remaining": murder_rate_sample_remaining,
        "murder_rate_sample_ready": (
            "yes" if murder_rate_sample_remaining == 0 else "no"
        ),
        "weighted_murder_target_per_10k_per_year": _format_optional_float(
            target_murder_rate
        ),
        "murder_per_10k_detailed_person_years": _format_optional_float(
            observed_murder_rate
        ),
        "murder_rate_target_ratio": _format_optional_float(murder_ratio),
        "murder_rate_calibration_status": _murder_rate_calibration_status(
            observed_per_10k=observed_murder_rate,
            target_per_10k=target_murder_rate,
            murder_events=total_murders,
        ),
        "total_serial_predator_candidate_events": total_serial_candidates,
        "serial_candidate_share_of_murders": _format_optional_float(
            serial_candidate_share
        ),
        "total_serial_murder_killers_3plus": total_serial_3plus_killers,
        "total_serial_murder_events_by_3plus_killers": total_serial_3plus_events,
        "serial_murder_event_share_3plus": _format_optional_float(serial_3plus_share),
        "serial_murder_target_share_max": _format_optional_float(
            SERIAL_MURDER_TARGET_SHARE_MAX
        ),
        "serial_murder_min_murder_sample": SERIAL_MURDER_MIN_MURDER_SAMPLE,
        "serial_murder_sample_remaining": serial_murder_sample_remaining,
        "serial_murder_sample_ready": (
            "yes" if serial_murder_sample_remaining == 0 else "no"
        ),
        "murder_sample_projection_rate_per_10k": _format_optional_float(
            projection_rate
        ),
        "murder_sample_projection_rate_source": projection_rate_source,
        "serial_murder_sample_projected_additional_detailed_person_years": (
            serial_sample_projected_person_years
        ),
        "serial_murder_sample_projected_additional_scenarios": (
            _project_additional_scenarios_for_person_years(
                additional_person_years=serial_sample_projected_person_years,
                current_person_years=person_years,
                scenario_count=len(rows),
            )
        ),
        "serial_murder_calibration_status": serial_guardrail_status,
        "serial_murder_emergence_min_murder_sample": (
            SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE
        ),
        "serial_murder_emergence_sample_remaining": (
            serial_emergence_sample_remaining
        ),
        "serial_murder_emergence_sample_ready": (
            "yes" if serial_emergence_sample_remaining == 0 else "no"
        ),
        "serial_murder_emergence_projected_additional_detailed_person_years": (
            serial_emergence_projected_person_years
        ),
        "serial_murder_emergence_projected_additional_scenarios": (
            _project_additional_scenarios_for_person_years(
                additional_person_years=serial_emergence_projected_person_years,
                current_person_years=person_years,
                scenario_count=len(rows),
            )
        ),
        "serial_murder_emergence_status": _serial_murder_emergence_status(
            murder_events=total_murders,
            serial_murder_killers_3plus=total_serial_3plus_killers,
            guardrail_status=serial_guardrail_status,
        ),
    }
    summary["hybrid_calibration_ready"] = (
        "yes"
        if summary["murder_rate_sample_ready"] == "yes"
        and summary["serial_murder_sample_ready"] == "yes"
        and summary["serial_murder_emergence_sample_ready"] == "yes"
        else "no"
    )
    summary["hybrid_calibration_status"] = _overall_hybrid_calibration_status(summary)
    summary.update(_next_calibration_run_hint(summary))
    return summary


def _aggregate_reason_variance_rows(
    rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown").strip() or "unknown"
        bucket = grouped.setdefault(
            reason,
            {
                "reason": reason,
                "scenario_count": 0,
                "detailed_people": 0,
                "high_variance_detail_people": 0,
                "genome_scored_detailed_people": 0,
                "extreme_detail_people": 0,
                "variance_scores": [],
            },
        )
        bucket["scenario_count"] = int(bucket["scenario_count"]) + 1
        detailed = _optional_int(row.get("detailed_people")) or 0
        high_variance = _optional_int(row.get("high_variance_detail_people")) or 0
        scored = _optional_int(row.get("genome_scored_detailed_people")) or 0
        extreme = _optional_int(row.get("extreme_detail_people")) or 0
        score = _optional_float(row.get("average_detail_variance_score"))
        bucket["detailed_people"] = int(bucket["detailed_people"]) + detailed
        bucket["high_variance_detail_people"] = (
            int(bucket["high_variance_detail_people"]) + high_variance
        )
        bucket["genome_scored_detailed_people"] = (
            int(bucket["genome_scored_detailed_people"]) + scored
        )
        bucket["extreme_detail_people"] = int(bucket["extreme_detail_people"]) + extreme
        if score is not None and scored > 0:
            bucket["variance_scores"].append((score, float(scored)))

    out: list[dict[str, object]] = []
    for bucket in grouped.values():
        avg = _weighted_average(list(bucket["variance_scores"]))
        out.append(
            {
                "scenario_count": int(bucket["scenario_count"]),
                **_reason_variance_output_row(
                    reason=str(bucket["reason"]),
                    detailed_people=int(bucket["detailed_people"]),
                    high_variance_detail_people=int(
                        bucket["high_variance_detail_people"]
                    ),
                    genome_scored_detailed_people=int(
                        bucket["genome_scored_detailed_people"]
                    ),
                    extreme_detail_people=int(bucket["extreme_detail_people"]),
                    average_detail_variance_score=avg,
                ),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            -int(row["detailed_people"]),
            str(row["reason"]),
        ),
    )


def _summary_output_path(row_output: Path) -> Path:
    suffix = row_output.suffix or ".tsv"
    return row_output.with_name(f"{row_output.stem}.summary{suffix}")


def _reason_output_path(row_output: Path) -> Path:
    suffix = row_output.suffix or ".tsv"
    return row_output.with_name(f"{row_output.stem}.promotion_reasons{suffix}")


def _reason_summary_output_path(row_output: Path) -> Path:
    suffix = row_output.suffix or ".tsv"
    return row_output.with_name(f"{row_output.stem}.promotion_reason_summary{suffix}")


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(("metric", "value"))
        for key, value in summary.items():
            writer.writerow((key, value))


def _write_reason_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "scenario_index",
        "target_index",
        "replicate_index",
        "target_population",
        "sim_seed",
        "reason",
        "selection_profile",
        "detailed_people",
        "high_variance_detail_people",
        "genome_scored_detailed_people",
        "extreme_detail_people",
        "average_detail_variance_score",
        "expected_average_detail_variance_min",
        "expected_average_detail_variance_max",
        "reason_variance_calibration_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=headers, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_reason_summary(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "reason",
        "selection_profile",
        "scenario_count",
        "detailed_people",
        "high_variance_detail_people",
        "genome_scored_detailed_people",
        "extreme_detail_people",
        "average_detail_variance_score",
        "expected_average_detail_variance_min",
        "expected_average_detail_variance_max",
        "reason_variance_calibration_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=headers, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_calibration_outputs(
    *,
    output_path: Path,
    summary_path: Path,
    reason_path: Path,
    reason_summary_path: Path,
    rows: list[dict[str, object]],
    reason_rows: list[dict[str, object]],
) -> dict[str, object]:
    summary = _aggregate_calibration_summary(rows)
    reason_summary = _aggregate_reason_variance_rows(reason_rows)
    _write_rows(output_path, rows)
    _write_summary(summary_path, summary)
    _write_reason_rows(reason_path, reason_rows)
    _write_reason_summary(reason_summary_path, reason_summary)
    return summary


def _seed_aggregate_settlements(ctx: SimulationContext, regions, *, start_year: int) -> None:
    for i, region in enumerate(regions, start=1):
        sid = f"{region.region_id}:mixed_calibration:1"
        if sid in ctx.settlements_by_id:
            continue
        ctx.settlements_by_id[sid] = SettlementState(
            region_id=region.region_id,
            settlement_id=sid,
            site_slot=1,
            founded_sim_year=int(start_year),
            status="active",
            display_name=f"Mixed Calibration {i}",
        )
    ctx.rebuild_settlement_region_index()


def run_calibration(
    *,
    target_population: int,
    years: int,
    starting_couples: int,
    detailed_fraction: float,
    min_detailed_cap: int,
    max_detailed_cap: int,
    sim_seed: int,
    world: str,
    cfg_path: Path,
    save_path: Path,
    disable_birth_settlement_spinoff: bool = False,
) -> dict[str, object]:
    regions = list_regions(world=world, db_path=cfg_path)
    if not regions:
        raise LookupError(f"No regions found for world={world!r}")
    base_capacity = sum(max(1, int(r.carrying_capacity)) for r in regions)
    passive_scale = float(target_population) / float(max(1, base_capacity))
    detailed_cap = _detailed_cap_for_target(
        target_population,
        fraction=detailed_fraction,
        min_cap=min_detailed_cap,
        max_cap=max_detailed_cap,
    )
    t0 = time.perf_counter()
    with SimulationContext.create(
        db_path=cfg_path,
        save_db_path=save_path,
        world_id=f"mixed_calibration_{target_population}",
        world=world,
        start_year=_DEFAULT_START_YEAR,
        refresh_config=False,
        flush_run_store=False,
        store_flush_batch_years=max(1, int(years)),
        checkpoint_full_snapshot_every_n_years=None,
        placename_rng_salt=int(sim_seed),
    ) as ctx:
        if disable_birth_settlement_spinoff:
            ctx.spinoff_min_mother_settlement_population = 10**12
            ctx.spinoff_pending_families_by_region.clear()
        _seed_aggregate_settlements(ctx, regions, start_year=_DEFAULT_START_YEAR)
        run_population_growth_simulation(
            ctx,
            sim_seed=int(sim_seed),
            start_year=_DEFAULT_START_YEAR,
            duration_years=int(years),
            starting_couples=int(starting_couples),
            passive_population_scale=passive_scale,
            detailed_active_soft_cap=detailed_cap,
            print_timing_report=False,
        )
        end_year = _DEFAULT_START_YEAR + int(years) - 1
        historical_end_year = ctx.get_historical_year(end_year)
        murder_target_per_10k = incident_rate_for_year(
            db_path=cfg_path,
            world=world,
            incident_key="murder",
            historical_year=historical_end_year,
        ).target_per_10k_per_year
        counts = _latest_mixed_counts(ctx, end_year)
        detailed_person_years = _detailed_person_years_from_file_store(ctx.file_store)
    elapsed = time.perf_counter() - t0
    hybrid_fields = _hybrid_calibration_fields(
        save_path,
        trait_slots=_trait_slots_from_config(cfg_path),
        murder_target_per_10k=murder_target_per_10k,
        detailed_person_years=detailed_person_years,
    )
    return {
        "target_population": int(target_population),
        "years": int(years),
        "world": world,
        "sim_seed": int(sim_seed),
        "starting_couples": int(starting_couples),
        "region_count": len(regions),
        "base_capacity": int(base_capacity),
        "passive_population_scale": f"{passive_scale:.8f}",
        "detailed_active_soft_cap": int(detailed_cap),
        "birth_settlement_spinoff_disabled": (
            "yes" if disable_birth_settlement_spinoff else "no"
        ),
        "elapsed_s": f"{elapsed:.6f}",
        **counts,
        **hybrid_fields,
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "scenario_index",
        "target_index",
        "replicate_index",
        "target_population",
        "years",
        "world",
        "sim_seed",
        "starting_couples",
        "region_count",
        "base_capacity",
        "passive_population_scale",
        "detailed_active_soft_cap",
        "birth_settlement_spinoff_disabled",
        "elapsed_s",
        "detailed_alive",
        "passive_person_alive",
        "aggregate_cohort_alive",
        "aggregate_cohort_births",
        "aggregate_cohort_deaths",
        "aggregate_cohort_partnered",
        "mixed_mode_alive",
        "cohort_rows",
        "promotion_count",
        "report_detailed_people",
        "report_detailed_alive_people",
        "report_non_detailed_alive_people",
        "high_variance_detail_people",
        "genome_scored_detailed_people",
        "extreme_detail_people",
        "average_detail_variance_score",
        "serial_predator_profile_people",
        "serial_predator_profile_share",
        "average_serial_predator_propensity",
        "max_serial_predator_propensity",
        "event_year_span",
        "detailed_person_years",
        "murder_events",
        "serial_predator_candidate_events",
        "distinct_murder_killers",
        "repeat_murder_killers_2plus",
        "serial_murder_killers_3plus",
        "serial_murder_events_by_3plus_killers",
        "murder_per_10k_detailed_person_years",
        "murder_target_per_10k_per_year",
        "murder_rate_target_ratio",
        "murder_rate_calibration_status",
        "serial_candidate_share_of_murders",
        "serial_murder_event_share_3plus",
        "serial_murder_target_share_max",
        "serial_murder_calibration_status",
        "serial_murder_emergence_min_murder_sample",
        "serial_murder_emergence_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=headers, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--targets",
        default=",".join(str(n) for n in _DEFAULT_TARGETS),
        help="Comma-separated target mixed-mode populations.",
    )
    p.add_argument("--years", type=int, default=_DEFAULT_YEARS)
    p.add_argument("--starting-couples", type=int, default=_DEFAULT_STARTING_COUPLES)
    p.add_argument("--seed", type=int, default=15000)
    p.add_argument(
        "--replicates",
        type=int,
        default=1,
        help="Number of seed replicates to run for each target population.",
    )
    p.add_argument("--world", default="default")
    p.add_argument("--detailed-fraction", type=float, default=_DEFAULT_DETAILED_FRACTION)
    p.add_argument("--min-detailed-cap", type=int, default=_DEFAULT_MIN_DETAILED_CAP)
    p.add_argument("--max-detailed-cap", type=int, default=_DEFAULT_MAX_DETAILED_CAP)
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    p.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional aggregate calibration TSV path. Defaults beside --output.",
    )
    p.add_argument(
        "--reason-output",
        type=Path,
        default=None,
        help="Optional per-scenario promotion-reason TSV path. Defaults beside --output.",
    )
    p.add_argument(
        "--reason-summary-output",
        type=Path,
        default=None,
        help="Optional aggregate promotion-reason TSV path. Defaults beside --output.",
    )
    p.add_argument(
        "--disable-birth-settlement-spinoff",
        action="store_true",
        help=(
            "Keep calibration probes in seeded settlements by disabling "
            "birth-driven settlement spin-off. Useful for large event-rate "
            "runs in runtimes without optional map-geometry dependencies."
        ),
    )
    p.add_argument(
        "--stop-when-hybrid-status",
        default="",
        help=(
            "Optional comma-separated aggregate hybrid_calibration_status values "
            "that stop the batch early after a scenario. Aliases: calibrated, ready."
        ),
    )
    p.add_argument(
        "--min-scenarios-before-stop",
        type=int,
        default=1,
        help="Minimum scenarios to run before honoring --stop-when-hybrid-status.",
    )
    p.add_argument(
        "--stop-after-total-murders",
        type=int,
        default=0,
        help=(
            "Stop after a scenario once the aggregate total_murder_events reaches "
            "this value. Use 100 for serial guardrail sample or 500 for emergence."
        ),
    )
    p.add_argument(
        "--stop-after-detailed-person-years",
        type=int,
        default=0,
        help=(
            "Stop after a scenario once aggregate total_detailed_person_years "
            "reaches this value."
        ),
    )
    p.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Load existing row and promotion-reason TSVs, skip completed "
            "scenario_index values, and rewrite aggregate summaries from the "
            "combined evidence."
        ),
    )
    p.add_argument(
        "--write-incremental",
        action="store_true",
        help=(
            "Rewrite row, summary, and promotion-reason TSVs after each "
            "completed scenario so interrupted long batches keep completed work."
        ),
    )
    args = p.parse_args()
    if args.years < 1:
        p.error("--years must be >= 1")
    if args.starting_couples < 1:
        p.error("--starting-couples must be >= 1")
    if args.replicates < 1:
        p.error("--replicates must be >= 1")
    if args.min_scenarios_before_stop < 1:
        p.error("--min-scenarios-before-stop must be >= 1")
    if args.stop_after_total_murders < 0:
        p.error("--stop-after-total-murders must be >= 0")
    if args.stop_after_detailed_person_years < 0:
        p.error("--stop-after-detailed-person-years must be >= 0")
    if args.detailed_fraction < 0:
        p.error("--detailed-fraction must be >= 0")
    if args.min_detailed_cap < 1 or args.max_detailed_cap < 1:
        p.error("--min-detailed-cap and --max-detailed-cap must be >= 1")
    if args.max_detailed_cap < args.min_detailed_cap:
        p.error("--max-detailed-cap must be >= --min-detailed-cap")
    try:
        args.targets = _parse_targets(args.targets)
    except ValueError as exc:
        p.error(str(exc))
    return args


def main() -> None:
    args = _parse_args()
    stop_statuses = _parse_hybrid_stop_statuses(args.stop_when_hybrid_status)
    output_path = Path(args.output)
    summary_path = (
        Path(args.summary_output)
        if args.summary_output is not None
        else _summary_output_path(output_path)
    )
    reason_path = (
        Path(args.reason_output)
        if args.reason_output is not None
        else _reason_output_path(output_path)
    )
    reason_summary_path = (
        Path(args.reason_summary_output)
        if args.reason_summary_output is not None
        else _reason_summary_output_path(output_path)
    )
    rows: list[dict[str, object]] = []
    reason_rows: list[dict[str, object]] = []
    if bool(args.resume_existing):
        rows.extend(_read_tsv_rows(output_path))
        reason_rows.extend(_read_tsv_rows(reason_path))
    scenarios = _scenario_plan(
        tuple(args.targets),
        replicates=int(args.replicates),
        seed=int(args.seed),
    )
    completed_scenarios = _completed_matching_scenario_indexes(rows, scenarios)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        cfg = root / "config.sqlite"
        load_all_csvs_into_sqlite(cfg)
        trait_slots = _trait_slots_from_config(cfg)
        for scenario in scenarios:
            if int(scenario["scenario_index"]) in completed_scenarios:
                continue
            save_path = (
                root
                / (
                    f"save_{scenario['target_population']}"
                    f"_r{scenario['replicate_index']}.sqlite"
                )
            )
            row = run_calibration(
                target_population=int(scenario["target_population"]),
                years=int(args.years),
                starting_couples=int(args.starting_couples),
                detailed_fraction=float(args.detailed_fraction),
                min_detailed_cap=int(args.min_detailed_cap),
                max_detailed_cap=int(args.max_detailed_cap),
                sim_seed=int(scenario["sim_seed"]),
                world=str(args.world).strip(),
                cfg_path=cfg,
                save_path=save_path,
                disable_birth_settlement_spinoff=bool(
                    args.disable_birth_settlement_spinoff
                ),
            )
            row.update(
                {
                    "scenario_index": int(scenario["scenario_index"]),
                    "target_index": int(scenario["target_index"]),
                    "replicate_index": int(scenario["replicate_index"]),
                }
            )
            rows.append(row)
            for reason_row in _hybrid_reason_variance_rows(
                save_path,
                trait_slots=trait_slots,
            ):
                reason_row.update(
                    {
                        "scenario_index": int(scenario["scenario_index"]),
                        "target_index": int(scenario["target_index"]),
                        "replicate_index": int(scenario["replicate_index"]),
                        "target_population": int(scenario["target_population"]),
                        "sim_seed": int(scenario["sim_seed"]),
                    }
                )
                reason_rows.append(reason_row)
            completed_scenarios.add(int(scenario["scenario_index"]))
            running_summary = _aggregate_calibration_summary(rows)
            if bool(args.write_incremental):
                _write_calibration_outputs(
                    output_path=output_path,
                    summary_path=summary_path,
                    reason_path=reason_path,
                    reason_summary_path=reason_summary_path,
                    rows=rows,
                    reason_rows=reason_rows,
                )
            if _should_stop_for_sample_thresholds(
                running_summary,
                min_scenarios=int(args.min_scenarios_before_stop),
                stop_after_total_murders=(
                    int(args.stop_after_total_murders)
                    if int(args.stop_after_total_murders) > 0
                    else None
                ),
                stop_after_detailed_person_years=(
                    int(args.stop_after_detailed_person_years)
                    if int(args.stop_after_detailed_person_years) > 0
                    else None
                ),
            ):
                print(
                    " | ".join(
                        (
                            "stopping_early=yes",
                            "matched_sample_threshold=yes",
                            f"scenario_count={running_summary['scenario_count']}",
                            f"total_murders={running_summary['total_murder_events']}",
                            f"total_detailed_person_years={running_summary['total_detailed_person_years']}",
                        )
                    )
                )
                break
            if _should_stop_for_hybrid_status(
                running_summary,
                stop_statuses=stop_statuses,
                min_scenarios=int(args.min_scenarios_before_stop),
            ):
                print(
                    " | ".join(
                        (
                            "stopping_early=yes",
                            f"matched_hybrid_status={running_summary['hybrid_calibration_status']}",
                            f"scenario_count={running_summary['scenario_count']}",
                        )
                    )
                )
                break
    summary = _write_calibration_outputs(
        output_path=output_path,
        summary_path=summary_path,
        reason_path=reason_path,
        reason_summary_path=reason_summary_path,
        rows=rows,
        reason_rows=reason_rows,
    )
    for row in rows:
        print(
            " | ".join(
                (
                    f"target={row['target_population']}",
                    f"replicate={row['replicate_index']}",
                    f"seed={row['sim_seed']}",
                    f"mixed={row['mixed_mode_alive']}",
                    f"detailed={row['detailed_alive']}/{row['detailed_active_soft_cap']}",
                    f"aggregate={row['aggregate_cohort_alive']}",
                    f"rows={row['cohort_rows']}",
                    f"promotions={row['promotion_count']}",
                    f"high_variance={row['high_variance_detail_people']}",
                    f"murder_rate_10k={row['murder_per_10k_detailed_person_years'] or 'n/a'}",
                    f"murder_target={row['murder_target_per_10k_per_year'] or 'n/a'}",
                    f"murder_status={row['murder_rate_calibration_status']}",
                    f"serial_share={row['serial_candidate_share_of_murders'] or 'n/a'}",
                    f"serial_3plus={row['serial_murder_event_share_3plus'] or 'n/a'}",
                    f"serial_status={row['serial_murder_calibration_status']}",
                    f"elapsed_s={row['elapsed_s']}",
                )
            )
        )
    print(
        " | ".join(
            (
                f"summary_scenarios={summary['scenario_count']}",
                f"summary_murders={summary['total_murder_events']}",
                f"summary_murder_rate_10k={summary['murder_per_10k_detailed_person_years'] or 'n/a'}",
                f"summary_murder_status={summary['murder_rate_calibration_status']}",
                f"summary_serial_3plus={summary['serial_murder_event_share_3plus'] or 'n/a'}",
                f"summary_serial_status={summary['serial_murder_calibration_status']}",
                f"summary_hybrid_status={summary['hybrid_calibration_status']}",
            )
        )
    )
    print(f"wrote {output_path.resolve()}")
    print(f"wrote {summary_path.resolve()}")
    print(f"wrote {reason_path.resolve()}")
    print(f"wrote {reason_summary_path.resolve()}")


if __name__ == "__main__":
    main()
