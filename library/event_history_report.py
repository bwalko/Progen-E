"""Event-history reporting helpers for tuning rates, visibility, and prose."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

from library.event_prose import EventRecordProse, load_public_chronicle_prose
from library.world_save import event_payload_from_row
from library.detailed_population_variance import HIGH_VARIANCE_DETAIL_COMPOSITE
from library.event_scoring import serial_predation_risk
from library.serious_crime_taxonomy import event_serious_crime_category


REMARKABLE_ARCHETYPE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "status_rise",
        "patronage_granted",
        "elite_household_investment",
        "political_crime",
        "religious_cultural_conflict",
        "private_life",
        "city_state_urban_consolidation",
        "city_state_public_works",
        "city_state_resource_dispute",
        "city_state_league_formed",
        "city_state_hegemony_declared",
        "city_state_colony_status_changed",
        "city_state_autonomy_changed",
        "city_state_civic_crisis",
        "city_state_civic_reform",
        "city_state_occupation_imposed",
        "city_state_liberated",
        "city_state_tribute_imposed",
        "city_state_garrison_installed",
        "city_state_league_broken",
        "city_state_tyranny_usurpation",
        "city_state_exile_decreed",
        "city_state_debt_relief",
    }
)

INCIDENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "murder",
        "property_crime",
        "affair_scandal",
        "public_virtue",
        "knowledge_culture",
        "outlaw_case_opened",
        "outlaw_flight",
        "outlaw_refuge_joined",
        "outlaw_raid",
        "outlaw_pursuit",
        "outlaw_captured",
        "outlaw_killed",
        "outlaw_bought_off",
        "outlaw_returned",
        "outlaw_forgotten",
    }
) | REMARKABLE_ARCHETYPE_EVENT_TYPES

SERIAL_MURDER_TARGET_SHARE_MAX = 0.01
SERIAL_MURDER_MIN_MURDER_SAMPLE = 100
SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE = 500
SERIAL_PREDATOR_PROFILE_THRESHOLD = 0.62


@dataclass(frozen=True)
class CountRow:
    keys: tuple[str, ...]
    count: int


@dataclass(frozen=True)
class MetricSummary:
    event_type: str
    metric: str
    count: int
    minimum: float
    maximum: float
    average: float


@dataclass(frozen=True)
class ConsequenceMetricSummary:
    section: str
    key: str
    metric: str
    count: int
    minimum: float
    maximum: float
    average: float


@dataclass(frozen=True)
class OutlawOutcomeSummary:
    scope: str
    metric: str
    count: int
    denominator: int | None = None
    rate: float | None = None
    average_years: float | None = None


@dataclass(frozen=True)
class HybridPopulationCalibrationSummary:
    detailed_people: int
    detailed_alive_people: int
    non_detailed_alive_people: int
    high_variance_detail_people: int
    genome_scored_detailed_people: int
    extreme_detail_people: int
    average_detail_variance_score: float | None
    serial_predator_profile_people: int
    serial_predator_profile_share: float | None
    average_serial_predator_propensity: float | None
    max_serial_predator_propensity: float | None
    event_year_span: int
    murder_events: int
    ordinary_murder_events: int
    feud_revenge_murder_events: int
    robbery_property_murder_events: int
    outlaw_raid_killing_events: int
    war_political_legal_killing_events: int
    spree_panic_killing_events: int
    predatory_murder_events: int
    serial_predatory_murder_events: int
    serial_predator_candidate_events: int
    distinct_murder_killers: int
    repeat_murder_killers_2plus: int
    serial_murder_killers_3plus: int
    serial_murder_events_by_3plus_killers: int
    murder_per_10k_detailed_person_years: float | None
    serial_candidate_share_of_murders: float | None
    serial_murder_event_share_3plus: float | None
    serial_murder_target_share_max: float
    serial_murder_calibration_status: str
    serial_murder_emergence_min_murder_sample: int
    serial_murder_emergence_status: str


@dataclass(frozen=True)
class HybridVarianceByPromotionReason:
    reason: str
    detailed_people: int
    high_variance_detail_people: int
    genome_scored_detailed_people: int
    extreme_detail_people: int
    average_detail_variance_score: float | None


@dataclass(frozen=True)
class EventHistoryReport:
    total_events: int
    total_records: int
    save_size_bytes: int | None
    event_counts_by_type: tuple[CountRow, ...]
    event_counts_by_year_type: tuple[CountRow, ...]
    visibility_counts: tuple[CountRow, ...]
    metric_summaries: tuple[MetricSummary, ...]
    consequence_counts: tuple[CountRow, ...]
    consequence_metric_summaries: tuple[ConsequenceMetricSummary, ...]
    outlaw_outcome_summary: tuple[OutlawOutcomeSummary, ...]
    hybrid_population_calibration: HybridPopulationCalibrationSummary
    hybrid_variance_by_promotion_reason: tuple[HybridVarianceByPromotionReason, ...]
    public_samples: tuple[EventRecordProse, ...]


def build_event_history_report(
    conn: sqlite3.Connection,
    *,
    save_path: Path | None = None,
    sample_limit: int = 12,
    sample_event_types: Iterable[str] | None = INCIDENT_EVENT_TYPES,
    trait_slots: Sequence[str] = (),
) -> EventHistoryReport:
    """Build a compact, deterministic report from event-readable views."""

    _require_relation(conn, "simulation_events_readable")
    _require_relation(conn, "simulation_event_records_readable")
    total_events = _count(conn, "simulation_events_readable")
    total_records = _count(conn, "simulation_event_records_readable")
    save_size = save_path.stat().st_size if save_path is not None and save_path.exists() else None
    return EventHistoryReport(
        total_events=total_events,
        total_records=total_records,
        save_size_bytes=save_size,
        event_counts_by_type=tuple(_event_counts_by_type(conn)),
        event_counts_by_year_type=tuple(_event_counts_by_year_type(conn)),
        visibility_counts=tuple(_visibility_counts(conn)),
        metric_summaries=tuple(_metric_summaries(conn)),
        consequence_counts=tuple(_consequence_counts(conn)),
        consequence_metric_summaries=tuple(_consequence_metric_summaries(conn)),
        outlaw_outcome_summary=tuple(_outlaw_outcome_summary(conn)),
        hybrid_population_calibration=_hybrid_population_calibration(
            conn, trait_slots=tuple(str(slot) for slot in trait_slots)
        ),
        hybrid_variance_by_promotion_reason=tuple(
            _hybrid_variance_by_promotion_reason(
                conn, trait_slots=tuple(str(slot) for slot in trait_slots)
            )
        ),
        public_samples=tuple(
            load_public_chronicle_prose(
                conn,
                event_types=sample_event_types,
                limit=max(0, int(sample_limit)),
            )
        ),
    )


def write_event_history_report(report: EventHistoryReport, output_dir: Path) -> None:
    """Write TSV/text report artifacts for inspection and trend comparison."""

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_counts(
        output_dir / "event_counts_by_type.tsv",
        ("event_type", "count"),
        report.event_counts_by_type,
    )
    _write_counts(
        output_dir / "event_counts_by_year_type.tsv",
        ("sim_year", "event_type", "count"),
        report.event_counts_by_year_type,
    )
    _write_counts(
        output_dir / "event_visibility_counts.tsv",
        ("event_type", "record_type", "visibility_state", "count"),
        report.visibility_counts,
    )
    _write_counts(
        output_dir / "tracked_incident_counts.tsv",
        ("event_type", "count"),
        _tracked_incident_counts(report),
    )
    _write_metric_summaries(output_dir / "event_metric_summaries.tsv", report.metric_summaries)
    _write_consequence_counts(
        output_dir / "event_consequence_counts.tsv",
        report.consequence_counts,
    )
    _write_consequence_metric_summaries(
        output_dir / "event_consequence_metrics.tsv",
        report.consequence_metric_summaries,
    )
    _write_outlaw_outcome_summary(
        output_dir / "outlaw_outcome_summary.tsv",
        report.outlaw_outcome_summary,
    )
    _write_hybrid_population_calibration(
        output_dir / "hybrid_population_calibration.tsv",
        report.hybrid_population_calibration,
    )
    _write_hybrid_variance_by_promotion_reason(
        output_dir / "hybrid_variance_by_promotion_reason.tsv",
        report.hybrid_variance_by_promotion_reason,
    )
    _write_public_samples(output_dir / "public_chronicle_samples.tsv", report.public_samples)
    (output_dir / "summary.txt").write_text(format_event_history_summary(report), encoding="utf-8")


def format_event_history_summary(report: EventHistoryReport) -> str:
    """Return a human-readable summary suitable for CLI output."""

    lines = [
        "Event History Report",
        f"total_events: {report.total_events}",
        f"total_records: {report.total_records}",
    ]
    if report.save_size_bytes is not None:
        lines.append(f"save_size_bytes: {report.save_size_bytes}")
    lines.append("")
    lines.append("Top Event Types")
    for row in sorted(report.event_counts_by_type, key=lambda item: (-item.count, item.keys)):
        lines.append(f"- {row.keys[0]}: {row.count}")
    lines.append("")
    lines.append("Tracked Incident Slices")
    for row in _tracked_incident_counts(report):
        lines.append(f"- {row.keys[0]}: {row.count}")
    lines.append("")
    lines.append("Visibility")
    for row in sorted(report.visibility_counts, key=lambda item: (item.keys, item.count)):
        event_type, record_type, visibility = row.keys
        lines.append(f"- {event_type} / {record_type} / {visibility}: {row.count}")
    lines.append("")
    lines.append("Metrics")
    if report.metric_summaries:
        for metric in sorted(report.metric_summaries, key=lambda item: (item.event_type, item.metric)):
            lines.append(
                f"- {metric.event_type} {metric.metric}: n={metric.count} "
                f"avg={metric.average:.4f} min={metric.minimum:.4f} max={metric.maximum:.4f}"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Consequence Counts")
    if report.consequence_counts:
        for row in sorted(report.consequence_counts, key=lambda item: (item.keys, item.count)):
            section = row.keys[0]
            key = " / ".join(row.keys[1:])
            lines.append(f"- {section} / {key}: {row.count}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Consequence Metrics")
    if report.consequence_metric_summaries:
        for metric in sorted(
            report.consequence_metric_summaries,
            key=lambda item: (item.section, item.key, item.metric),
        ):
            lines.append(
                f"- {metric.section} / {metric.key} / {metric.metric}: "
                f"n={metric.count} avg={metric.average:.4f} "
                f"min={metric.minimum:.4f} max={metric.maximum:.4f}"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Outlaw Outcome Summary")
    if report.outlaw_outcome_summary:
        for row in report.outlaw_outcome_summary:
            text = f"- {row.scope} / {row.metric}: count={row.count}"
            if row.denominator is not None:
                text += f" denominator={row.denominator}"
            if row.rate is not None:
                text += f" rate={row.rate:.4f}"
            if row.average_years is not None:
                text += f" avg_years={row.average_years:.2f}"
            lines.append(text)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Hybrid Population Calibration")
    h = report.hybrid_population_calibration
    lines.append(f"- detailed_people: {h.detailed_people}")
    lines.append(f"- detailed_alive_people: {h.detailed_alive_people}")
    lines.append(f"- non_detailed_alive_people: {h.non_detailed_alive_people}")
    lines.append(f"- high_variance_detail_people: {h.high_variance_detail_people}")
    lines.append(f"- genome_scored_detailed_people: {h.genome_scored_detailed_people}")
    lines.append(f"- extreme_detail_people: {h.extreme_detail_people}")
    avg_text = (
        "n/a"
        if h.average_detail_variance_score is None
        else f"{h.average_detail_variance_score:.4f}"
    )
    lines.append(f"- average_detail_variance_score: {avg_text}")
    serial_profile_share = (
        "n/a"
        if h.serial_predator_profile_share is None
        else f"{h.serial_predator_profile_share:.4f}"
    )
    avg_serial_propensity = (
        "n/a"
        if h.average_serial_predator_propensity is None
        else f"{h.average_serial_predator_propensity:.4f}"
    )
    max_serial_propensity = (
        "n/a"
        if h.max_serial_predator_propensity is None
        else f"{h.max_serial_predator_propensity:.4f}"
    )
    lines.append(f"- serial_predator_profile_people: {h.serial_predator_profile_people}")
    lines.append(f"- serial_predator_profile_share: {serial_profile_share}")
    lines.append(f"- average_serial_predator_propensity: {avg_serial_propensity}")
    lines.append(f"- max_serial_predator_propensity: {max_serial_propensity}")
    lines.append(f"- event_year_span: {h.event_year_span}")
    lines.append(f"- murder_events: {h.murder_events}")
    lines.append(f"- ordinary_murder_events: {h.ordinary_murder_events}")
    lines.append(f"- feud_revenge_murder_events: {h.feud_revenge_murder_events}")
    lines.append(
        f"- robbery_property_murder_events: {h.robbery_property_murder_events}"
    )
    lines.append(f"- outlaw_raid_killing_events: {h.outlaw_raid_killing_events}")
    lines.append(
        f"- war_political_legal_killing_events: {h.war_political_legal_killing_events}"
    )
    lines.append(f"- spree_panic_killing_events: {h.spree_panic_killing_events}")
    lines.append(f"- predatory_murder_events: {h.predatory_murder_events}")
    lines.append(
        f"- serial_predatory_murder_events: {h.serial_predatory_murder_events}"
    )
    lines.append(f"- serial_predator_candidate_events: {h.serial_predator_candidate_events}")
    lines.append(f"- distinct_murder_killers: {h.distinct_murder_killers}")
    lines.append(f"- repeat_murder_killers_2plus: {h.repeat_murder_killers_2plus}")
    lines.append(f"- serial_murder_killers_3plus: {h.serial_murder_killers_3plus}")
    lines.append(
        f"- serial_murder_events_by_3plus_killers: "
        f"{h.serial_murder_events_by_3plus_killers}"
    )
    murder_rate = (
        "n/a"
        if h.murder_per_10k_detailed_person_years is None
        else f"{h.murder_per_10k_detailed_person_years:.4f}"
    )
    serial_share = (
        "n/a"
        if h.serial_candidate_share_of_murders is None
        else f"{h.serial_candidate_share_of_murders:.4f}"
    )
    lines.append(f"- murder_per_10k_detailed_person_years: {murder_rate}")
    lines.append(f"- serial_candidate_share_of_murders: {serial_share}")
    serial_3plus_share = (
        "n/a"
        if h.serial_murder_event_share_3plus is None
        else f"{h.serial_murder_event_share_3plus:.4f}"
    )
    lines.append(f"- serial_murder_event_share_3plus: {serial_3plus_share}")
    lines.append(f"- serial_murder_target_share_max: {h.serial_murder_target_share_max:.4f}")
    lines.append(f"- serial_murder_calibration_status: {h.serial_murder_calibration_status}")
    lines.append(
        "- serial_murder_emergence_min_murder_sample: "
        f"{h.serial_murder_emergence_min_murder_sample}"
    )
    lines.append(
        f"- serial_murder_emergence_status: {h.serial_murder_emergence_status}"
    )
    if report.hybrid_variance_by_promotion_reason:
        lines.append("- variance_by_promotion_reason_top:")
        for row in report.hybrid_variance_by_promotion_reason[:5]:
            avg = (
                "n/a"
                if row.average_detail_variance_score is None
                else f"{row.average_detail_variance_score:.4f}"
            )
            lines.append(
                "  "
                f"{row.reason}: people={row.detailed_people}, "
                f"high_variance={row.high_variance_detail_people}, "
                f"extreme={row.extreme_detail_people}, avg={avg}"
            )
    lines.append("")
    lines.append("Public Chronicle Samples")
    if report.public_samples:
        for sample in report.public_samples:
            lines.append(
                f"- {sample.sim_year} {sample.event_type} "
                f"[{sample.visibility_state}]: {sample.public_prose}"
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _event_counts_by_type(conn: sqlite3.Connection) -> list[CountRow]:
    return [
        CountRow((str(row["event_type"]),), int(row["n"]))
        for row in conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM simulation_events_readable
            GROUP BY event_type
            ORDER BY n DESC, event_type
            """
        )
    ]


def _event_counts_by_year_type(conn: sqlite3.Connection) -> list[CountRow]:
    return [
        CountRow((str(row["sim_year"]), str(row["event_type"])), int(row["n"]))
        for row in conn.execute(
            """
            SELECT sim_year, event_type, COUNT(*) AS n
            FROM simulation_events_readable
            GROUP BY sim_year, event_type
            ORDER BY sim_year, event_type
            """
        )
    ]


def _visibility_counts(conn: sqlite3.Connection) -> list[CountRow]:
    return [
        CountRow(
            (
                str(row["event_type"]),
                str(row["record_type"]),
                str(row["visibility_state"]),
            ),
            int(row["n"]),
        )
        for row in conn.execute(
            """
            SELECT event_type, record_type, visibility_state, COUNT(*) AS n
            FROM simulation_event_records_readable
            GROUP BY event_type, record_type, visibility_state
            ORDER BY event_type, record_type, visibility_state
            """
        )
    ]


def _metric_summaries(conn: sqlite3.Connection) -> list[MetricSummary]:
    values: dict[tuple[str, str], list[float]] = {}
    for row in conn.execute(
        """
        SELECT id, event_type, payload_json, primary_person_id, secondary_person_id,
               settlement_key, region_key, event_origin
        FROM simulation_events
        WHERE event_type IN (
            'murder',
            'property_crime',
            'affair_scandal',
            'public_virtue',
            'knowledge_culture',
            'status_rise',
            'patronage_granted',
            'elite_household_investment',
            'political_crime',
            'religious_cultural_conflict',
            'private_life',
            'city_state_urban_consolidation',
            'city_state_public_works',
            'city_state_resource_dispute',
            'city_state_league_formed',
            'city_state_hegemony_declared',
            'city_state_colony_status_changed',
            'city_state_autonomy_changed',
            'city_state_civic_crisis',
            'city_state_civic_reform',
            'city_state_occupation_imposed',
            'city_state_liberated',
            'city_state_tribute_imposed',
            'city_state_garrison_installed',
            'city_state_league_broken',
            'city_state_tyranny_usurpation',
            'city_state_exile_decreed',
            'city_state_debt_relief'
        )
        """
    ):
        event_type = str(row["event_type"])
        payload = event_payload_from_row(row, conn, expand=True)
        for metric in (
            "historical_importance",
            "resource_pressure",
            "justice_pressure_score",
            "pursuit_pressure_score",
            "accusation_pressure_score",
            "kin_vengeance_pressure",
            "offender_panic_pressure",
            "pattern_recognition_score",
            "loss_value",
            "relief_value",
            "novelty_value",
            "archetype_score",
            "archetype_share_weight",
        ):
            value = _float(payload.get(metric))
            if value is None:
                continue
            values.setdefault((event_type, metric), []).append(value)
    out: list[MetricSummary] = []
    for (event_type, metric), vals in values.items():
        if not vals:
            continue
        out.append(
            MetricSummary(
                event_type=event_type,
                metric=metric,
                count=len(vals),
                minimum=min(vals),
                maximum=max(vals),
                average=sum(vals) / len(vals),
            )
        )
    return out


_CONSEQUENCE_COUNT_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Domain States",
        "simulation_domain_states_readable",
        ("domain", "latest_incident_kind"),
    ),
    (
        "Faction Memory",
        "simulation_faction_memory_readable",
        ("memory_type", "status", "polarity"),
    ),
    (
        "Obligations",
        "simulation_obligations_readable",
        ("obligation_type", "status"),
    ),
    (
        "Reputation Marks",
        "simulation_reputation_marks_readable",
        ("reputation_axis", "direction"),
    ),
    (
        "Legal Fallout",
        "simulation_legal_fallout_readable",
        ("fallout_type", "status"),
    ),
    (
        "Legal Adjudications",
        "simulation_legal_adjudications_readable",
        ("adjudication_type", "outcome"),
    ),
    (
        "Outlaw Cases",
        "simulation_outlaw_cases_readable",
        ("offense_type", "status", "resolution"),
    ),
    (
        "Outlaw Refuges",
        "simulation_outlaw_refuges_readable",
        ("region_id", "status"),
    ),
    (
        "Outlaw Custodies",
        "simulation_outlaw_custodies_readable",
        ("custody_type", "status"),
    ),
    (
        "Domain Diffusion",
        "simulation_domain_diffusion_readable",
        ("domain", "route_type"),
    ),
    (
        "Institutions",
        "simulation_institutions_readable",
        ("institution_type", "status", "focus_domain"),
    ),
    (
        "Innovation Discoveries",
        "simulation_innovation_discoveries_readable",
        ("category", "domain", "era_id"),
    ),
    (
        "Innovation Knowledge",
        "simulation_innovation_knowledge_readable",
        ("category", "domain", "status"),
    ),
    (
        "Innovation Era State",
        "simulation_innovation_era_state_readable",
        ("era_id", "scope_kind"),
    ),
)


_CONSEQUENCE_METRIC_SPECS: tuple[
    tuple[str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "Domain States",
        "simulation_domain_states_readable",
        ("domain", "latest_incident_kind"),
        ("domain_score", "breakthrough_count"),
    ),
    (
        "Faction Memory",
        "simulation_faction_memory_readable",
        ("memory_type", "status"),
        ("strength",),
    ),
    (
        "Obligations",
        "simulation_obligations_readable",
        ("obligation_type", "status"),
        ("strength",),
    ),
    (
        "Reputation Marks",
        "simulation_reputation_marks_readable",
        ("reputation_axis", "direction"),
        ("mark_strength",),
    ),
    (
        "Legal Fallout",
        "simulation_legal_fallout_readable",
        ("fallout_type", "status"),
        ("severity",),
    ),
    (
        "Legal Adjudications",
        "simulation_legal_adjudications_readable",
        ("adjudication_type", "outcome"),
        ("severity",),
    ),
    (
        "Outlaw Cases",
        "simulation_outlaw_cases_readable",
        ("offense_type", "status"),
        ("severity_01", "knownness_01", "pursuit_pressure_01", "buyoff_power_01"),
    ),
    (
        "Outlaw Refuges",
        "simulation_outlaw_refuges_readable",
        ("region_id", "status"),
        ("band_size", "concealment_01", "support_01", "active_case_count"),
    ),
    (
        "Outlaw Custodies",
        "simulation_outlaw_custodies_readable",
        ("custody_type", "status"),
        ("severity_01",),
    ),
    (
        "Domain Diffusion",
        "simulation_domain_diffusion_readable",
        ("domain", "route_type"),
        ("route_friction", "state_delta"),
    ),
    (
        "Institutions",
        "simulation_institutions_readable",
        ("institution_type", "status", "focus_domain"),
        ("strength", "influence_score"),
    ),
    (
        "Innovation Discoveries",
        "simulation_innovation_discoveries_readable",
        ("category", "domain"),
        ("novelty_score",),
    ),
    (
        "Innovation Knowledge",
        "simulation_innovation_knowledge_readable",
        ("category", "domain", "status"),
        ("adoption_score",),
    ),
    (
        "Innovation Era State",
        "simulation_innovation_era_state_readable",
        ("era_id", "scope_kind"),
        ("adopted_count", "next_era_adopted_count"),
    ),
)


def _consequence_counts(conn: sqlite3.Connection) -> list[CountRow]:
    out: list[CountRow] = []
    for section, relation, columns in _CONSEQUENCE_COUNT_SPECS:
        if not _relation_exists(conn, relation):
            continue
        select_cols = ", ".join(
            f"COALESCE(CAST({col} AS TEXT), '') AS c{i}"
            for i, col in enumerate(columns)
        )
        group_cols = ", ".join(f"c{i}" for i, _col in enumerate(columns))
        for row in conn.execute(
            f"""
            SELECT {select_cols}, COUNT(*) AS n
            FROM {relation}
            GROUP BY {group_cols}
            ORDER BY {group_cols}
            """
        ):
            values = tuple(_summary_key(row[f"c{i}"]) for i, _col in enumerate(columns))
            out.append(CountRow((section, *values), int(row["n"])))
    return out


def _consequence_metric_summaries(
    conn: sqlite3.Connection,
) -> list[ConsequenceMetricSummary]:
    out: list[ConsequenceMetricSummary] = []
    for section, relation, key_columns, metric_columns in _CONSEQUENCE_METRIC_SPECS:
        if not _relation_exists(conn, relation):
            continue
        key_select = ", ".join(
            f"COALESCE(CAST({col} AS TEXT), '') AS c{i}"
            for i, col in enumerate(key_columns)
        )
        group_cols = ", ".join(f"c{i}" for i, _col in enumerate(key_columns))
        for metric in metric_columns:
            for row in conn.execute(
                f"""
                SELECT {key_select},
                       COUNT({metric}) AS n,
                       MIN({metric}) AS min_value,
                       MAX({metric}) AS max_value,
                       AVG({metric}) AS avg_value
                FROM {relation}
                WHERE {metric} IS NOT NULL
                GROUP BY {group_cols}
                HAVING COUNT({metric}) > 0
                ORDER BY {group_cols}
                """
            ):
                values = tuple(
                    _summary_key(row[f"c{i}"]) for i, _col in enumerate(key_columns)
                )
                out.append(
                    ConsequenceMetricSummary(
                        section=section,
                        key=" / ".join(values),
                        metric=metric,
                        count=int(row["n"]),
                        minimum=float(row["min_value"]),
                        maximum=float(row["max_value"]),
                        average=float(row["avg_value"]),
                    )
                )
    return out


_OUTLAW_SOURCE_EVENT_TYPES: tuple[str, ...] = ("murder", "property_crime")
_OUTLAW_LIFECYCLE_EVENT_TYPES: tuple[str, ...] = (
    "outlaw_case_opened",
    "outlaw_flight",
    "outlaw_refuge_joined",
    "outlaw_raid",
    "outlaw_pursuit",
    "outlaw_captured",
    "outlaw_killed",
    "outlaw_bought_off",
    "outlaw_returned",
    "outlaw_forgotten",
)


def _outlaw_outcome_summary(conn: sqlite3.Connection) -> list[OutlawOutcomeSummary]:
    source_total = sum(_event_count(conn, event_type) for event_type in _OUTLAW_SOURCE_EVENT_TYPES)
    out = [_outlaw_summary_row("all", "source_crimes", source_total)]
    if not _relation_exists(conn, "simulation_outlaw_cases_readable"):
        opened_events = _event_count(conn, "outlaw_case_opened")
        out.append(_outlaw_summary_row("all", "opened_cases", opened_events, source_total))
        for event_type in _OUTLAW_LIFECYCLE_EVENT_TYPES:
            out.append(
                _outlaw_summary_row(
                    "all",
                    f"{event_type}_events",
                    _event_count(conn, event_type),
                    opened_events,
                )
            )
        return out

    case_total = _count(conn, "simulation_outlaw_cases_readable")
    out.append(_outlaw_summary_row("all", "opened_cases", case_total, source_total))
    out.append(
        _outlaw_summary_row(
            "all",
            "active_cases",
            _outlaw_case_count(conn, "status = 'active'"),
            case_total,
        )
    )
    out.append(
        _outlaw_summary_row(
            "all",
            "resolved_cases",
            _outlaw_case_count(conn, "status = 'resolved'"),
            case_total,
        )
    )
    for event_type in _OUTLAW_LIFECYCLE_EVENT_TYPES:
        out.append(
            _outlaw_summary_row(
                "all",
                f"{event_type}_events",
                _event_count(conn, event_type),
                case_total,
            )
        )
    for row in conn.execute(
        """
        SELECT COALESCE(resolution, '') AS resolution, COUNT(*) AS n
        FROM simulation_outlaw_cases_readable
        WHERE status = 'resolved' OR COALESCE(resolution, '') <> ''
        GROUP BY COALESCE(resolution, '')
        ORDER BY resolution
        """
    ):
        out.append(
            _outlaw_summary_row(
                "all",
                f"resolution:{_summary_key(row['resolution'])}",
                int(row["n"]),
                case_total,
            )
        )
    for offense_type in _OUTLAW_SOURCE_EVENT_TYPES:
        offense_cases = _outlaw_case_count(
            conn,
            "offense_type = ?",
            (offense_type,),
        )
        out.append(
            _outlaw_summary_row(
                f"offense:{offense_type}",
                "opened_cases",
                offense_cases,
                _event_count(conn, offense_type),
            )
        )
    _append_average_years(
        out,
        conn,
        scope="all",
        metric="years_to_resolution",
        relation="simulation_outlaw_cases_readable",
        expression="resolved_year - start_year",
        where="resolved_year IS NOT NULL AND start_year IS NOT NULL",
    )
    _append_average_years(
        out,
        conn,
        scope="all",
        metric="expected_years_to_forget",
        relation="simulation_outlaw_cases_readable",
        expression="expected_forget_year - start_year",
        where="expected_forget_year IS NOT NULL AND start_year IS NOT NULL",
    )
    if _relation_exists(conn, "simulation_outlaw_refuges_readable"):
        out.append(
            _outlaw_summary_row(
                "all",
                "active_refuges",
                _count_where(conn, "simulation_outlaw_refuges_readable", "status = 'active'"),
                case_total,
            )
        )
    if _relation_exists(conn, "simulation_outlaw_custodies_readable"):
        out.append(
            _outlaw_summary_row(
                "all",
                "active_custodies",
                _count_where(conn, "simulation_outlaw_custodies_readable", "status = 'active'"),
                case_total,
            )
        )
        _append_average_years(
            out,
            conn,
            scope="all",
            metric="custody_years",
            relation="simulation_outlaw_custodies_readable",
            expression="expected_release_year - start_year",
            where="expected_release_year IS NOT NULL AND start_year IS NOT NULL",
        )
    return out


def _outlaw_summary_row(
    scope: str,
    metric: str,
    count: int,
    denominator: int | None = None,
    average_years: float | None = None,
) -> OutlawOutcomeSummary:
    denom = int(denominator) if denominator is not None else None
    rate = (int(count) / denom) if denom and denom > 0 else None
    return OutlawOutcomeSummary(
        scope=scope,
        metric=metric,
        count=int(count),
        denominator=denom,
        rate=rate,
        average_years=average_years,
    )


def _append_average_years(
    rows: list[OutlawOutcomeSummary],
    conn: sqlite3.Connection,
    *,
    scope: str,
    metric: str,
    relation: str,
    expression: str,
    where: str,
) -> None:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n, AVG({expression}) AS avg_years
        FROM {relation}
        WHERE {where} AND ({expression}) >= 0
        """
    ).fetchone()
    count = int(row["n"] if row is not None else 0)
    if count <= 0:
        rows.append(_outlaw_summary_row(scope, metric, 0))
        return
    rows.append(
        _outlaw_summary_row(
            scope,
            metric,
            count,
            average_years=float(row["avg_years"]),
        )
    )


def _outlaw_case_count(
    conn: sqlite3.Connection,
    where: str,
    params: Sequence[object] = (),
) -> int:
    return _count_where(conn, "simulation_outlaw_cases_readable", where, params)


def _hybrid_population_calibration(
    conn: sqlite3.Connection,
    *,
    trait_slots: Sequence[str] = (),
) -> HybridPopulationCalibrationSummary:
    detailed_people = _count(conn, "simulation_people") if _relation_exists(conn, "simulation_people") else 0
    detailed_alive = (
        _count_where(conn, "simulation_people", "is_alive = 1")
        if _relation_exists(conn, "simulation_people")
        else 0
    )
    non_detailed_alive = (
        _count_where(conn, "simulation_people_nondetailed", "is_alive = 1")
        if _relation_exists(conn, "simulation_people_nondetailed")
        else 0
    )
    high_variance = 0
    variance_scores: list[float] = []
    serial_profile_scores: list[float] = []
    candidate_table_scores: list[float] | None = None
    if _relation_exists(conn, "simulation_serial_predation_candidates"):
        candidate_table_scores = [
            float(row["risk_score"] or 0.0)
            for row in conn.execute(
                """
                SELECT risk_score
                FROM simulation_serial_predation_candidates
                WHERE status IN ('active', 'dormant_throttled', 'dormant')
                  AND risk_lane <> ''
                """
            ).fetchall()
        ]
    if _relation_exists(conn, "simulation_people"):
        for row in conn.execute("SELECT person_json FROM simulation_people"):
            payload = _payload(row["person_json"])
            composites = payload.get("genome_composite_names") or ()
            if isinstance(composites, str):
                composite_values = {composites}
            else:
                try:
                    composite_values = {str(value) for value in composites or ()}
                except TypeError:
                    composite_values = set()
            if HIGH_VARIANCE_DETAIL_COMPOSITE in composite_values:
                high_variance += 1
            score = _person_payload_variance_score(payload, trait_slots=trait_slots)
            if score is not None:
                variance_scores.append(score)
            if candidate_table_scores is None:
                genome = _person_payload_genome(payload, trait_slots=trait_slots)
                if genome:
                    serial_profile_scores.append(serial_predation_risk(genome).risk_score)
    if candidate_table_scores is not None:
        serial_profile_scores = candidate_table_scores
    murder_events = _event_count(conn, "murder") if _relation_exists(conn, "simulation_events_readable") else 0
    serious_crime_category_counts: dict[str, int] = {
        "ordinary_murder": 0,
        "feud_revenge_murder": 0,
        "robbery_property_murder": 0,
        "outlaw_raid_killing": 0,
        "war_political_legal_killing": 0,
        "spree_panic_killing": 0,
        "predatory_murder": 0,
        "serial_predatory_murder": 0,
    }
    serial_candidates = 0
    murders_by_killer: dict[int, int] = {}
    predatory_murders_by_killer: dict[int, int] = {}
    if _relation_exists(conn, "simulation_events_readable"):
        for row in conn.execute(
            """
            SELECT id, event_type, payload_json, primary_person_id, secondary_person_id,
                   settlement_key, region_key, event_origin
            FROM simulation_events
            """
        ):
            event_type = str(row["event_type"] or "").strip()
            payload = event_payload_from_row(row, conn, expand=True)
            category = event_serious_crime_category(event_type, payload)
            if category in serious_crime_category_counts:
                serious_crime_category_counts[category] += 1
            if event_type != "murder":
                continue
            if bool(
                payload.get("serial_predation_candidate")
                or payload.get("serial_predator_candidate")
            ):
                serial_candidates += 1
            killer_id = _int_or_none(payload.get("killer_person_id"))
            if killer_id is not None:
                murders_by_killer[killer_id] = murders_by_killer.get(killer_id, 0) + 1
                if str(payload.get("incident_kind") or "").strip() == "predatory_murder":
                    predatory_murders_by_killer[killer_id] = (
                        predatory_murders_by_killer.get(killer_id, 0) + 1
                    )
    span = _event_year_span(conn)
    person_years = detailed_alive * span
    repeat_2plus = sum(1 for count in murders_by_killer.values() if count >= 2)
    serial_3plus = sum(1 for count in predatory_murders_by_killer.values() if count >= 3)
    serial_events_3plus = sum(
        count for count in predatory_murders_by_killer.values() if count >= 3
    )
    murder_rate = (
        float(murder_events) / float(person_years) * 10_000.0
        if person_years > 0
        else None
    )
    serial_share = (
        float(serial_candidates) / float(murder_events)
        if murder_events > 0
        else None
    )
    serial_3plus_share = (
        float(serial_events_3plus) / float(murder_events)
        if murder_events > 0
        else None
    )
    if murder_events < SERIAL_MURDER_MIN_MURDER_SAMPLE:
        serial_status = "insufficient_murder_sample"
    elif serial_3plus_share is not None and serial_3plus_share <= SERIAL_MURDER_TARGET_SHARE_MAX:
        serial_status = "within_real_life_guardrail"
    else:
        serial_status = "above_real_life_guardrail"
    if murder_events < SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE:
        serial_emergence_status = "insufficient_emergence_sample"
    elif serial_3plus <= 0:
        serial_emergence_status = "no_serial_murder_emerged"
    elif serial_status == "above_real_life_guardrail":
        serial_emergence_status = "above_real_life_guardrail"
    else:
        serial_emergence_status = "serial_murder_emerged"
    return HybridPopulationCalibrationSummary(
        detailed_people=detailed_people,
        detailed_alive_people=detailed_alive,
        non_detailed_alive_people=non_detailed_alive,
        high_variance_detail_people=high_variance,
        genome_scored_detailed_people=len(variance_scores),
        extreme_detail_people=sum(1 for score in variance_scores if score >= 0.82),
        average_detail_variance_score=(
            sum(variance_scores) / len(variance_scores) if variance_scores else None
        ),
        serial_predator_profile_people=sum(
            1
            for score in serial_profile_scores
            if (
                score >= SERIAL_PREDATOR_PROFILE_THRESHOLD
                if candidate_table_scores is None
                else score > 0.0
            )
        ),
        serial_predator_profile_share=(
            sum(
                1
                for score in serial_profile_scores
                if (
                    score >= SERIAL_PREDATOR_PROFILE_THRESHOLD
                    if candidate_table_scores is None
                    else score > 0.0
                )
            )
            / (len(serial_profile_scores) if candidate_table_scores is None else max(1, detailed_alive))
            if serial_profile_scores or candidate_table_scores is not None
            else None
        ),
        average_serial_predator_propensity=(
            (
                sum(serial_profile_scores) / len(serial_profile_scores)
                if candidate_table_scores is None
                else sum(serial_profile_scores) / max(1, detailed_alive)
            )
            if serial_profile_scores or candidate_table_scores is not None
            else None
        ),
        max_serial_predator_propensity=(
            max(serial_profile_scores) if serial_profile_scores else None
        ),
        event_year_span=span,
        murder_events=murder_events,
        ordinary_murder_events=serious_crime_category_counts["ordinary_murder"],
        feud_revenge_murder_events=serious_crime_category_counts[
            "feud_revenge_murder"
        ],
        robbery_property_murder_events=serious_crime_category_counts[
            "robbery_property_murder"
        ],
        outlaw_raid_killing_events=serious_crime_category_counts[
            "outlaw_raid_killing"
        ],
        war_political_legal_killing_events=serious_crime_category_counts[
            "war_political_legal_killing"
        ],
        spree_panic_killing_events=serious_crime_category_counts[
            "spree_panic_killing"
        ],
        predatory_murder_events=serious_crime_category_counts["predatory_murder"],
        serial_predatory_murder_events=serious_crime_category_counts[
            "serial_predatory_murder"
        ],
        serial_predator_candidate_events=serial_candidates,
        distinct_murder_killers=len(murders_by_killer),
        repeat_murder_killers_2plus=repeat_2plus,
        serial_murder_killers_3plus=serial_3plus,
        serial_murder_events_by_3plus_killers=serial_events_3plus,
        murder_per_10k_detailed_person_years=murder_rate,
        serial_candidate_share_of_murders=serial_share,
        serial_murder_event_share_3plus=serial_3plus_share,
        serial_murder_target_share_max=SERIAL_MURDER_TARGET_SHARE_MAX,
        serial_murder_calibration_status=serial_status,
        serial_murder_emergence_min_murder_sample=(
            SERIAL_MURDER_EMERGENCE_MIN_MURDER_SAMPLE
        ),
        serial_murder_emergence_status=serial_emergence_status,
    )


def _hybrid_variance_by_promotion_reason(
    conn: sqlite3.Connection,
    *,
    trait_slots: Sequence[str] = (),
) -> tuple[HybridVarianceByPromotionReason, ...]:
    if not _relation_exists(conn, "simulation_people") or not _relation_exists(
        conn, "simulation_promotion_log"
    ):
        return ()
    rows = conn.execute(
        """
        SELECT
            p.person_id,
            p.person_json,
            COALESCE(pl.reason, 'generated') AS reason
        FROM simulation_people p
        JOIN (
            SELECT person_id, MIN(promotion_id) AS promotion_id
            FROM simulation_promotion_log
            GROUP BY person_id
        ) first_promotion ON first_promotion.person_id = p.person_id
        JOIN simulation_promotion_log pl
          ON pl.promotion_id = first_promotion.promotion_id
        """
    ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        reason = str(row["reason"] or "generated").strip() or "generated"
        bucket = grouped.setdefault(
            reason,
            {
                "people": 0,
                "high_variance": 0,
                "scores": [],
            },
        )
        bucket["people"] = int(bucket["people"]) + 1
        payload = _payload(row["person_json"])
        composites = payload.get("genome_composite_names") or ()
        if isinstance(composites, str):
            composite_values = {composites}
        else:
            try:
                composite_values = {str(value) for value in composites or ()}
            except TypeError:
                composite_values = set()
        if HIGH_VARIANCE_DETAIL_COMPOSITE in composite_values:
            bucket["high_variance"] = int(bucket["high_variance"]) + 1
        score = _person_payload_variance_score(payload, trait_slots=trait_slots)
        if score is not None:
            bucket["scores"].append(score)
    out: list[HybridVarianceByPromotionReason] = []
    for reason, bucket in grouped.items():
        scores = list(bucket["scores"])
        out.append(
            HybridVarianceByPromotionReason(
                reason=reason,
                detailed_people=int(bucket["people"]),
                high_variance_detail_people=int(bucket["high_variance"]),
                genome_scored_detailed_people=len(scores),
                extreme_detail_people=sum(1 for score in scores if float(score) >= 0.82),
                average_detail_variance_score=(
                    sum(float(score) for score in scores) / len(scores)
                    if scores
                    else None
                ),
            )
        )
    return tuple(
        sorted(
            out,
            key=lambda row: (
                -int(row.detailed_people),
                row.reason,
            ),
        )
    )


def _person_payload_variance_score(
    payload: dict[str, object],
    *,
    trait_slots: Sequence[str] = (),
) -> float | None:
    raw = _person_payload_genome(payload, trait_slots=trait_slots)
    if not isinstance(raw, dict):
        return None
    values: list[float] = []
    for value in raw.values():
        try:
            values.append(abs(float(value)))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    values.sort(reverse=True)
    top = values[: min(8, len(values))]
    return max(0.0, min(1.0, sum(top) / len(top) / 99.99))


def _person_payload_genome(
    payload: dict[str, object],
    *,
    trait_slots: Sequence[str] = (),
) -> dict[str, float]:
    raw = (
        payload.get("mind_body")
        or payload.get("genome")
        or _compact_trait_payload(payload, "mb", trait_slots=trait_slots)
        or _compact_trait_payload(payload, "g", trait_slots=trait_slots)
    )
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for trait, value in raw.items():
        try:
            out[str(trait)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _compact_trait_payload(
    payload: dict[str, object],
    key: str,
    *,
    trait_slots: Sequence[str] = (),
) -> dict[str, float]:
    slots_raw = payload.get("ts")
    values_raw = payload.get(key)
    if isinstance(slots_raw, list):
        slots = tuple(str(slot) for slot in slots_raw)
    else:
        slots = tuple(str(slot) for slot in trait_slots)
    if not slots or not isinstance(values_raw, list):
        return {}
    out: dict[str, float] = {}
    for trait, value in zip(slots, values_raw):
        if value is None:
            continue
        name = str(trait or "").strip()
        if not name:
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _event_year_span(conn: sqlite3.Connection) -> int:
    if _relation_exists(conn, "world_state"):
        try:
            row = conn.execute(
                """
                SELECT start_year, current_year
                FROM world_state
                WHERE start_year IS NOT NULL AND current_year IS NOT NULL
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            try:
                return max(1, int(row["current_year"]) - int(row["start_year"]) + 1)
            except (KeyError, TypeError, ValueError):
                pass
    if not _relation_exists(conn, "simulation_events_readable"):
        return 1
    row = conn.execute(
        """
        SELECT MIN(sim_year) AS min_year, MAX(sim_year) AS max_year
        FROM simulation_events_readable
        WHERE event_type = 'murder' AND sim_year IS NOT NULL
        """
    ).fetchone()
    if row is None or row["min_year"] is None or row["max_year"] is None:
        row = conn.execute(
            """
            SELECT MIN(sim_year) AS min_year, MAX(sim_year) AS max_year
            FROM simulation_events_readable
            WHERE sim_year IS NOT NULL
            """
        ).fetchone()
    if row is None or row["min_year"] is None or row["max_year"] is None:
        return 1
    return max(1, int(row["max_year"]) - int(row["min_year"]) + 1)


def _event_count(conn: sqlite3.Connection, event_type: str) -> int:
    return _count_where(
        conn,
        "simulation_events_readable",
        "event_type = ?",
        (event_type,),
    )


def _count_where(
    conn: sqlite3.Connection,
    relation: str,
    where: str,
    params: Sequence[object] = (),
) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {relation} WHERE {where}",
        tuple(params),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def _write_counts(path: Path, headers: Sequence[str], rows: Sequence[CountRow]) -> None:
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join([*row.keys, str(row.count)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_metric_summaries(path: Path, rows: Sequence[MetricSummary]) -> None:
    lines = ["event_type\tmetric\tcount\tmin\tmax\tavg"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row.event_type,
                    row.metric,
                    str(row.count),
                    f"{row.minimum:.6f}",
                    f"{row.maximum:.6f}",
                    f"{row.average:.6f}",
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_consequence_counts(path: Path, rows: Sequence[CountRow]) -> None:
    lines = ["section\tkey\tcount"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row.keys[0],
                    _tsv_text(" / ".join(row.keys[1:])),
                    str(row.count),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_consequence_metric_summaries(
    path: Path, rows: Sequence[ConsequenceMetricSummary]
) -> None:
    lines = ["section\tkey\tmetric\tcount\tmin\tmax\tavg"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row.section,
                    _tsv_text(row.key),
                    row.metric,
                    str(row.count),
                    f"{row.minimum:.6f}",
                    f"{row.maximum:.6f}",
                    f"{row.average:.6f}",
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_outlaw_outcome_summary(
    path: Path, rows: Sequence[OutlawOutcomeSummary]
) -> None:
    lines = ["scope\tmetric\tcount\tdenominator\trate\taverage_years"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    _tsv_text(row.scope),
                    _tsv_text(row.metric),
                    str(row.count),
                    "" if row.denominator is None else str(row.denominator),
                    "" if row.rate is None else f"{row.rate:.6f}",
                    "" if row.average_years is None else f"{row.average_years:.6f}",
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_hybrid_population_calibration(
    path: Path, row: HybridPopulationCalibrationSummary
) -> None:
    lines = ["metric\tvalue"]
    values = {
        "detailed_people": row.detailed_people,
        "detailed_alive_people": row.detailed_alive_people,
        "non_detailed_alive_people": row.non_detailed_alive_people,
        "high_variance_detail_people": row.high_variance_detail_people,
        "genome_scored_detailed_people": row.genome_scored_detailed_people,
        "extreme_detail_people": row.extreme_detail_people,
        "average_detail_variance_score": row.average_detail_variance_score,
        "serial_predator_profile_people": row.serial_predator_profile_people,
        "serial_predator_profile_share": row.serial_predator_profile_share,
        "average_serial_predator_propensity": row.average_serial_predator_propensity,
        "max_serial_predator_propensity": row.max_serial_predator_propensity,
        "event_year_span": row.event_year_span,
        "murder_events": row.murder_events,
        "ordinary_murder_events": row.ordinary_murder_events,
        "feud_revenge_murder_events": row.feud_revenge_murder_events,
        "robbery_property_murder_events": row.robbery_property_murder_events,
        "outlaw_raid_killing_events": row.outlaw_raid_killing_events,
        "war_political_legal_killing_events": row.war_political_legal_killing_events,
        "spree_panic_killing_events": row.spree_panic_killing_events,
        "predatory_murder_events": row.predatory_murder_events,
        "serial_predatory_murder_events": row.serial_predatory_murder_events,
        "serial_predator_candidate_events": row.serial_predator_candidate_events,
        "distinct_murder_killers": row.distinct_murder_killers,
        "repeat_murder_killers_2plus": row.repeat_murder_killers_2plus,
        "serial_murder_killers_3plus": row.serial_murder_killers_3plus,
        "serial_murder_events_by_3plus_killers": row.serial_murder_events_by_3plus_killers,
        "murder_per_10k_detailed_person_years": row.murder_per_10k_detailed_person_years,
        "serial_candidate_share_of_murders": row.serial_candidate_share_of_murders,
        "serial_murder_event_share_3plus": row.serial_murder_event_share_3plus,
        "serial_murder_target_share_max": row.serial_murder_target_share_max,
        "serial_murder_calibration_status": row.serial_murder_calibration_status,
        "serial_murder_emergence_min_murder_sample": (
            row.serial_murder_emergence_min_murder_sample
        ),
        "serial_murder_emergence_status": row.serial_murder_emergence_status,
    }
    for key, value in values.items():
        if value is None:
            text = ""
        elif isinstance(value, float):
            text = f"{value:.6f}"
        else:
            text = str(value)
        lines.append(f"{key}\t{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_hybrid_variance_by_promotion_reason(
    path: Path, rows: Sequence[HybridVarianceByPromotionReason]
) -> None:
    lines = [
        "\t".join(
            [
                "reason",
                "detailed_people",
                "high_variance_detail_people",
                "genome_scored_detailed_people",
                "extreme_detail_people",
                "average_detail_variance_score",
            ]
        )
    ]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    _tsv_text(row.reason),
                    str(row.detailed_people),
                    str(row.high_variance_detail_people),
                    str(row.genome_scored_detailed_people),
                    str(row.extreme_detail_people),
                    (
                        ""
                        if row.average_detail_variance_score is None
                        else f"{row.average_detail_variance_score:.6f}"
                    ),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tracked_incident_counts(report: EventHistoryReport) -> tuple[CountRow, ...]:
    counts = {row.keys[0]: row.count for row in report.event_counts_by_type}
    return tuple(
        CountRow((event_type,), int(counts.get(event_type, 0)))
        for event_type in sorted(INCIDENT_EVENT_TYPES)
    )


def _write_public_samples(path: Path, rows: Sequence[EventRecordProse]) -> None:
    lines = [
        "sim_year\tevent_id\trecord_id\tevent_type\tvisibility_state\tprose_variant_key\tpublic_prose"
    ]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    str(row.sim_year),
                    str(row.event_id),
                    str(row.record_id),
                    row.event_type,
                    row.visibility_state,
                    row.prose_variant_key,
                    _tsv_text(row.public_prose),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count(conn: sqlite3.Connection, relation: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {relation}").fetchone()
    return int(row["n"] if isinstance(row, sqlite3.Row) else row[0])


def _require_relation(conn: sqlite3.Connection, name: str) -> None:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type IN ('table', 'view') AND name = ?
        """,
        (name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing required event-history relation: {name}")


def _relation_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type IN ('table', 'view') AND name = ?
        """,
        (name,),
    ).fetchone()
    return row is not None


def _payload(raw: object) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tsv_text(value: object) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _summary_key(value: object) -> str:
    text = str(value or "").strip()
    return text or "(blank)"
