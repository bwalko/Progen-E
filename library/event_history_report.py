"""Event-history reporting helpers for tuning rates, visibility, and prose."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

from library.event_prose import EventRecordProse, load_public_chronicle_prose


INCIDENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "murder",
        "property_crime",
        "affair_scandal",
        "public_virtue",
        "knowledge_culture",
    }
)


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
class EventHistoryReport:
    total_events: int
    total_records: int
    save_size_bytes: int | None
    event_counts_by_type: tuple[CountRow, ...]
    event_counts_by_year_type: tuple[CountRow, ...]
    visibility_counts: tuple[CountRow, ...]
    metric_summaries: tuple[MetricSummary, ...]
    public_samples: tuple[EventRecordProse, ...]


def build_event_history_report(
    conn: sqlite3.Connection,
    *,
    save_path: Path | None = None,
    sample_limit: int = 12,
    sample_event_types: Iterable[str] | None = INCIDENT_EVENT_TYPES,
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
        SELECT event_type, payload_json
        FROM simulation_events_readable
        WHERE event_type IN (
            'murder',
            'property_crime',
            'affair_scandal',
            'public_virtue',
            'knowledge_culture'
        )
        """
    ):
        event_type = str(row["event_type"])
        payload = _payload(row["payload_json"])
        for metric in (
            "historical_importance",
            "resource_pressure",
            "loss_value",
            "relief_value",
            "novelty_value",
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


def _tsv_text(value: object) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
