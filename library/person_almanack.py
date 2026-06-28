"""Cached person leaderboards for Gradio's Almanack view."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from library.world_save import event_payload_from_row


ALMANACK_TABLE = "simulation_person_almanack_metrics"
ALMANACK_META_TABLE = "simulation_person_almanack_cache"
ALMANACK_DEFINITION_TABLE = "simulation_person_almanack_metric_definitions"
ALMANACK_EVIDENCE_TABLE = "simulation_person_almanack_evidence"
ALMANACK_SCHEMA_VERSION = 3
EVIDENCE_LIMIT_PER_METRIC = 50

_COMMON_NOTE = "Scores describe saved simulation records, not moral worth."

_METRIC_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "key": "murders_committed",
        "label": "Murders Committed",
        "category": "Crime",
        "value_type": "count",
        "sources": "detailed",
        "description": "Murder events where the person is recorded as killer.",
    },
    {
        "key": "property_crimes_committed",
        "label": "Property Crimes Committed",
        "category": "Crime",
        "value_type": "count",
        "sources": "detailed",
        "description": "Property-crime events where the person is the perpetrator.",
    },
    {
        "key": "property_loss_caused",
        "label": "Property Loss Caused",
        "category": "Crime",
        "value_type": "value",
        "sources": "detailed",
        "description": "Recorded property loss attributed to the perpetrator.",
    },
    {
        "key": "property_crimes_suffered",
        "label": "Property Crimes Suffered",
        "category": "Victim / Consequence",
        "value_type": "count",
        "sources": "detailed",
        "description": "Property-crime events where the person is the target or victim.",
    },
    {
        "key": "property_loss_suffered",
        "label": "Property Loss Suffered",
        "category": "Victim / Consequence",
        "value_type": "value",
        "sources": "detailed",
        "description": "Recorded property loss suffered as target or victim.",
    },
    {
        "key": "family_members_murdered",
        "label": "Family Members Murdered",
        "category": "Victim / Consequence",
        "value_type": "count",
        "sources": "detailed",
        "description": "Close family members recorded as murder victims.",
    },
    {
        "key": "legal_entanglements",
        "label": "Legal Entanglements",
        "category": "Victim / Consequence",
        "value_type": "count",
        "sources": "detailed",
        "description": "Legal fallout, adjudication, or crime/scandal witness rows touching the person.",
    },
    {
        "key": "children_lost_young",
        "label": "Children Lost Young",
        "category": "Victim / Consequence",
        "value_type": "count",
        "sources": "detailed",
        "description": "Recorded children who died before age 16.",
    },
    {
        "key": "displacements",
        "label": "Displacements",
        "category": "Victim / Consequence",
        "value_type": "count",
        "sources": "detailed",
        "description": "Recorded settlement moves or displacement rows.",
    },
    {
        "key": "children_recorded",
        "label": "Recorded Children",
        "category": "Family",
        "value_type": "count",
        "sources": "detailed,passive",
        "requires_detailed": 0,
        "description": "Recorded child links for detailed people, plus explicit passive child counts.",
    },
    {
        "key": "descendants_2g",
        "label": "Descendants Within 2 Generations",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Detailed descendants within two generations.",
    },
    {
        "key": "descendants_4g",
        "label": "Descendants Within 4 Generations",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Detailed descendants within four generations.",
    },
    {
        "key": "notable_descendants",
        "label": "Notable Descendants",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Descendants with cached archive notability or strong Almanack traces.",
    },
    {
        "key": "crime_legal_descendants",
        "label": "Crime / Legal Descendants",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Descendants touched by crime or legal records.",
    },
    {
        "key": "descendant_region_dispersion",
        "label": "Descendant Region Dispersion",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct regions represented by descendants.",
    },
    {
        "key": "family_gravity",
        "label": "Family Gravity",
        "category": "Family Reach",
        "value_type": "count",
        "sources": "detailed",
        "description": "Event density around close family and household-linked people.",
    },
    {
        "key": "distinct_paramours",
        "label": "Distinct Paramours",
        "category": "Romance",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct recorded paramour partners.",
    },
    {
        "key": "distinct_partners",
        "label": "Distinct Partners",
        "category": "Romance",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct recorded official partners.",
    },
    {
        "key": "largest_relationship_age_gap",
        "label": "Largest Relationship Age Gap",
        "category": "Relationship Anomaly",
        "value_type": "years",
        "sources": "detailed",
        "description": "Largest birth-year gap among recorded partner or paramour formations.",
    },
    {
        "key": "overlapping_romances",
        "label": "Overlapping Romantic Relationships",
        "category": "Relationship Anomaly",
        "value_type": "count",
        "sources": "detailed",
        "description": "Maximum simultaneous partner/paramour intervals inferred from relationship events.",
    },
    {
        "key": "children_across_distinct_partners",
        "label": "Children Across Distinct Partners",
        "category": "Relationship Anomaly",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct co-parents across recorded children.",
    },
    {
        "key": "relationship_end_consequence_count",
        "label": "Consequence-Linked Relationship Endings",
        "category": "Relationship Anomaly",
        "value_type": "count",
        "sources": "detailed",
        "description": "Relationship endings tied to death, scandal, disappearance, or abandonment when payloads say so.",
    },
    {
        "key": "risky_relationship_score",
        "label": "Socially Risky Relationship Score",
        "category": "Relationship Anomaly",
        "value_type": "score",
        "sources": "detailed",
        "description": "Highest available risk signal from paramour, kinship, crime, legal, feud, or scandal context.",
    },
    {
        "key": "distinct_jobs",
        "label": "Distinct Jobs",
        "category": "Work",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct non-empty job assignments.",
    },
    {
        "key": "job_losses",
        "label": "Job Losses",
        "category": "Work",
        "value_type": "count",
        "sources": "detailed",
        "description": "Recorded job-loss events for the person.",
    },
    {
        "key": "offices_held",
        "label": "Offices Held",
        "category": "Government",
        "value_type": "count",
        "sources": "detailed",
        "description": "Distinct office holding spells recorded in government save tables.",
    },
    {
        "key": "age_at_death",
        "label": "Age at Death",
        "category": "Life Span",
        "value_type": "years",
        "sources": "detailed",
        "description": "Age in years at recorded death year for people with both birth and death years.",
    },
    {
        "key": "crossroads_index",
        "label": "Crossroads Index",
        "category": "Entanglement",
        "value_type": "score",
        "sources": "detailed",
        "description": "Composite score for lives touching many event families, roles, places, people, jobs, legal rows, and relationships.",
    },
    {
        "key": "archive_narrative_heat",
        "label": "Archive Narrative Heat",
        "category": "Archive",
        "value_type": "score",
        "sources": "detailed",
        "column": "narrative_heat_total",
        "description": "Cached archive Narrative Heat score.",
    },
    {
        "key": "archive_recognition_index",
        "label": "Archive Recognition Index",
        "category": "Archive",
        "value_type": "score",
        "sources": "detailed",
        "column": "archive_recognition_index",
        "description": "Cached Archive Recognition Index score.",
    },
    {
        "key": "archive_hidden_heat",
        "label": "Hidden Heat",
        "category": "Archive",
        "value_type": "score",
        "sources": "detailed",
        "column": "hidden_heat",
        "description": "Cached difference between story heat and formal recognition.",
    },
    {
        "key": "archive_violet_marginalia_score",
        "label": "Violet Marginalia Score",
        "category": "Archive",
        "value_type": "score",
        "sources": "detailed",
        "column": "violet_marginalia_score",
        "description": "Cached score for unusually archive-worthy lives.",
    },
    {
        "key": "contradictory_record_score",
        "label": "Contradictory Record Score",
        "category": "Strange Records",
        "value_type": "score",
        "sources": "detailed",
        "column": "narrative_heat_contradictions",
        "description": "Archive contradiction component surfaced as an Almanack metric.",
    },
    {
        "key": "largest_event_gap",
        "label": "Largest Gap Between Major Events",
        "category": "Strange Records",
        "value_type": "years",
        "sources": "detailed",
        "description": "Largest gap between recorded event years for the person.",
    },
    {
        "key": "uncertain_role_events",
        "label": "Uncertain Role Events",
        "category": "Strange Records",
        "value_type": "count",
        "sources": "detailed",
        "description": "Events where the person appears in records with uncertainty, rumor, distortion, or reduced confidence.",
    },
    {
        "key": "posthumous_mentions",
        "label": "Posthumous Mentions",
        "category": "Strange Records",
        "value_type": "count",
        "sources": "detailed",
        "description": "Recorded event mentions after the person's death year.",
    },
    {
        "key": "witness_or_relative_only_mentions",
        "label": "Witness / Relative Only Mentions",
        "category": "Strange Records",
        "value_type": "count",
        "sources": "detailed",
        "description": "Events where the person appears only as witness, relative, household member, suspect, or dependent.",
    },
    {
        "key": "single_strange_event_score",
        "label": "Single Strange Event Score",
        "category": "Strange Records",
        "value_type": "score",
        "sources": "detailed",
        "enabled": 0,
        "description": "Disabled until historical_importance scores reach the sparse-event threshold.",
    },
    {
        "key": "disasters_survived",
        "label": "Disasters Survived",
        "category": "Future Hooks",
        "value_type": "count",
        "sources": "detailed",
        "enabled": 0,
        "description": "Disabled until disaster survival events are durable save facts.",
    },
    {
        "key": "name_variants",
        "label": "Name Variants",
        "category": "Future Hooks",
        "value_type": "count",
        "sources": "detailed,passive",
        "requires_detailed": 0,
        "enabled": 0,
        "description": "Disabled until variant names are saved per person or record.",
    },
    {
        "key": "distinct_employers",
        "label": "Distinct Employers",
        "category": "Future Hooks",
        "value_type": "count",
        "sources": "detailed",
        "enabled": 0,
        "description": "Disabled until employer or workplace identifiers are durable save facts.",
    },
)

_METRIC_BY_KEY = {str(metric["key"]): metric for metric in _METRIC_DEFINITIONS}
_ARCHIVE_METRICS = tuple(m for m in _METRIC_DEFINITIONS if m.get("column"))

_INSERT_COLUMNS = (
    "person_id",
    "source_kind",
    "metric_key",
    "metric_label",
    "metric_category",
    "metric_value",
    "metric_count",
    "first_year",
    "last_year",
    "evidence_json",
    "source_event_max_id",
    "updated_year",
    "updated_at",
    "world_rank",
    "era_rank",
    "region_rank",
    "percentile",
    "z_score",
    "comparison_population",
    "era_bucket",
    "region_key",
    "context_json",
)

_EVIDENCE_COLUMNS = (
    "source_kind",
    "person_id",
    "metric_key",
    "evidence_rank",
    "source_table",
    "source_id",
    "source_year",
    "role",
    "contribution_value",
    "summary",
    "region_key",
    "settlement_key",
    "related_people_json",
    "payload_path",
    "caveat_json",
)


@dataclass
class _MetricAccumulator:
    person_id: int
    source_kind: str
    metric_key: str
    metric_label: str
    metric_category: str
    metric_value: float = 0.0
    metric_count: int = 0
    first_year: int | None = None
    last_year: int | None = None
    region_key: int | None = None
    settlement_key: int | None = None
    distinct_values: set[str] = field(default_factory=set)
    evidence: list[dict[str, object]] = field(default_factory=list)
    component_values: dict[str, float] = field(default_factory=dict)
    context: dict[str, object] = field(default_factory=dict)

    def add_count(
        self,
        *,
        year: int | None = None,
        value: float = 1.0,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.metric_value += float(value)
        self.metric_count += 1
        self._touch_year(year)
        self._add_evidence(evidence, default_contribution=value)

    def add_value(
        self,
        *,
        year: int | None = None,
        value: float,
        count: int = 1,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.metric_value += float(value)
        self.metric_count += int(count)
        self._touch_year(year)
        self._add_evidence(evidence, default_contribution=value)

    def add_distinct(
        self,
        value: object,
        *,
        year: int | None = None,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        token = str(value).strip()
        if not token:
            return
        added = token not in self.distinct_values
        if added:
            self.distinct_values.add(token)
            self.metric_count = len(self.distinct_values)
            self.metric_value = float(self.metric_count)
            self._add_evidence(evidence, default_contribution=1.0)
        self._touch_year(year)

    def set_max(
        self,
        value: float,
        *,
        year: int | None = None,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        value = float(value)
        if value <= float(self.metric_value):
            self._touch_year(year)
            return
        self.metric_value = value
        self.metric_count = 1
        self.evidence = []
        self._touch_year(year)
        self._add_evidence(evidence, default_contribution=value)

    def add_component(self, key: str, value: float) -> None:
        self.component_values[str(key)] = float(value)

    def _touch_year(self, year: int | None) -> None:
        if year is None:
            return
        y = int(year)
        self.first_year = y if self.first_year is None else min(self.first_year, y)
        self.last_year = y if self.last_year is None else max(self.last_year, y)

    def _add_evidence(
        self,
        evidence: Mapping[str, object] | None,
        *,
        default_contribution: float = 1.0,
    ) -> None:
        if not evidence:
            return
        ev = dict(evidence)
        ev.setdefault("contribution_value", float(default_contribution))
        year = _coerce_int(ev.get("source_year") or ev.get("year"))
        if year is not None:
            self._touch_year(year)
        region_key = _coerce_int(ev.get("region_key"))
        if region_key is not None and self.region_key is None:
            self.region_key = region_key
        settlement_key = _coerce_int(ev.get("settlement_key"))
        if settlement_key is not None and self.settlement_key is None:
            self.settlement_key = settlement_key
        if len(self.evidence) < EVIDENCE_LIMIT_PER_METRIC:
            self.evidence.append(ev)

    def insert_tuple(
        self,
        *,
        source_event_max_id: int,
        updated_year: int | None,
        updated_at: str,
    ) -> tuple[object, ...]:
        payload = {
            "schema": f"person_almanack_metric.v{ALMANACK_SCHEMA_VERSION}",
            "summary": _evidence_summary(self),
            "evidence": self.evidence[:8],
        }
        if self.distinct_values:
            payload["distinct_values"] = sorted(self.distinct_values)[:25]
        if self.component_values:
            payload["components"] = {
                key: round(value, 4)
                for key, value in sorted(self.component_values.items())
            }
        return (
            self.person_id,
            self.source_kind,
            self.metric_key,
            self.metric_label,
            self.metric_category,
            round(float(self.metric_value), 6),
            int(self.metric_count),
            self.first_year,
            self.last_year,
            json.dumps(payload, separators=(",", ":")),
            int(source_event_max_id),
            updated_year,
            updated_at,
            self.context.get("world_rank"),
            self.context.get("era_rank"),
            self.context.get("region_rank"),
            self.context.get("percentile"),
            self.context.get("z_score"),
            self.context.get("comparison_population"),
            self.context.get("era_bucket"),
            self.context.get("region_key"),
            json.dumps(self.context, separators=(",", ":")),
        )


def metric_definition_choices(
    conn: sqlite3.Connection | None = None, *, enabled_only: bool = True
) -> list[tuple[str, str]]:
    """Return ``(display_name, metric_key)`` pairs for UI controls."""

    return [
        (str(metric["display_name"]), str(metric["metric_key"]))
        for metric in _metric_definitions(conn, enabled_only=enabled_only)
    ]


def metric_choices(
    conn: sqlite3.Connection | None = None, *, enabled_only: bool = True
) -> list[tuple[str, str]]:
    """Return ``(label, key)`` pairs for UI controls."""

    return metric_definition_choices(conn, enabled_only=enabled_only)


def metric_categories(
    conn: sqlite3.Connection | None = None, *, enabled_only: bool = True
) -> list[str]:
    """Return category labels for UI controls."""

    return [
        "All",
        *sorted(
            {
                str(metric["category"])
                for metric in _metric_definitions(conn, enabled_only=enabled_only)
            }
        ),
    ]


def metric_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return cached row counts keyed by metric_key."""

    if not _table_exists(conn, ALMANACK_TABLE):
        return {}
    rows = _fetch_dicts(
        conn,
        f"""
        SELECT metric_key, COUNT(*) AS row_count
        FROM {_quote_identifier(ALMANACK_TABLE)}
        GROUP BY metric_key
        """,
    )
    return {str(row["metric_key"]): int(row["row_count"] or 0) for row in rows}


def metric_choice_labels(
    conn: sqlite3.Connection | None = None,
    *,
    enabled_only: bool = True,
    include_counts: bool = False,
) -> list[str]:
    """Return dropdown labels for Gradio, optionally suffixing cached row counts."""

    labels = ["All Metrics"]
    counts = metric_row_counts(conn) if conn is not None and include_counts else {}
    for display_name, metric_key in metric_definition_choices(conn, enabled_only=enabled_only):
        if include_counts:
            count = int(counts.get(metric_key, 0))
            labels.append(f"{display_name} ({count})")
        else:
            labels.append(display_name)
    return labels


def ensure_person_almanack_schema(conn: sqlite3.Connection) -> None:
    """Create cached Almanack tables, metric registry, and retrieval indexes."""

    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {ALMANACK_TABLE} (
            person_id INTEGER NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'detailed',
            metric_key TEXT NOT NULL,
            metric_label TEXT NOT NULL,
            metric_category TEXT NOT NULL,
            metric_value REAL NOT NULL DEFAULT 0.0,
            metric_count INTEGER NOT NULL DEFAULT 0,
            first_year INTEGER,
            last_year INTEGER,
            evidence_json TEXT NOT NULL DEFAULT '{{}}',
            source_event_max_id INTEGER NOT NULL DEFAULT 0,
            updated_year INTEGER,
            updated_at TEXT NOT NULL,
            world_rank INTEGER,
            era_rank INTEGER,
            region_rank INTEGER,
            percentile REAL,
            z_score REAL,
            comparison_population INTEGER,
            era_bucket INTEGER,
            region_key INTEGER,
            context_json TEXT NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (source_kind, person_id, metric_key)
        );
        CREATE INDEX IF NOT EXISTS idx_person_almanack_metric_rank
        ON {ALMANACK_TABLE} (
            metric_key,
            metric_value DESC,
            metric_count DESC,
            person_id ASC
        );
        CREATE INDEX IF NOT EXISTS idx_person_almanack_context_rank
        ON {ALMANACK_TABLE} (
            metric_key,
            era_bucket,
            region_key,
            percentile DESC,
            z_score DESC
        );
        CREATE INDEX IF NOT EXISTS idx_person_almanack_category_rank
        ON {ALMANACK_TABLE} (
            metric_category,
            metric_value DESC,
            metric_count DESC,
            person_id ASC
        );
        CREATE INDEX IF NOT EXISTS idx_person_almanack_person
        ON {ALMANACK_TABLE} (person_id, source_kind);

        CREATE TABLE IF NOT EXISTS {ALMANACK_META_TABLE} (
            cache_key TEXT PRIMARY KEY,
            row_count INTEGER NOT NULL DEFAULT 0,
            source_event_max_id INTEGER NOT NULL DEFAULT 0,
            cache_schema_version INTEGER NOT NULL DEFAULT {ALMANACK_SCHEMA_VERSION},
            updated_year INTEGER,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {ALMANACK_DEFINITION_TABLE} (
            metric_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'desc',
            person_sources_supported TEXT NOT NULL DEFAULT 'detailed',
            requires_detailed INTEGER NOT NULL DEFAULT 1,
            value_type TEXT NOT NULL DEFAULT 'count',
            normalization_mode TEXT NOT NULL DEFAULT 'ranked',
            evidence_builder TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            ethical_note TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS {ALMANACK_EVIDENCE_TABLE} (
            source_kind TEXT NOT NULL,
            person_id INTEGER NOT NULL,
            metric_key TEXT NOT NULL,
            evidence_rank INTEGER NOT NULL,
            source_table TEXT NOT NULL DEFAULT '',
            source_id INTEGER,
            source_year INTEGER,
            role TEXT NOT NULL DEFAULT '',
            contribution_value REAL NOT NULL DEFAULT 0.0,
            summary TEXT NOT NULL DEFAULT '',
            region_key INTEGER,
            settlement_key INTEGER,
            related_people_json TEXT NOT NULL DEFAULT '[]',
            payload_path TEXT NOT NULL DEFAULT '',
            caveat_json TEXT NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (source_kind, person_id, metric_key, evidence_rank)
        );
        CREATE INDEX IF NOT EXISTS idx_person_almanack_evidence_lookup
        ON {ALMANACK_EVIDENCE_TABLE} (source_kind, person_id, metric_key, evidence_rank);
        """
    )
    _ensure_columns(
        conn,
        ALMANACK_TABLE,
        {
            "world_rank": "INTEGER",
            "era_rank": "INTEGER",
            "region_rank": "INTEGER",
            "percentile": "REAL",
            "z_score": "REAL",
            "comparison_population": "INTEGER",
            "era_bucket": "INTEGER",
            "region_key": "INTEGER",
            "context_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    _ensure_columns(
        conn,
        ALMANACK_META_TABLE,
        {"cache_schema_version": f"INTEGER NOT NULL DEFAULT {ALMANACK_SCHEMA_VERSION}"},
    )
    _upsert_metric_definitions(conn)
    columns = set(_table_columns(conn, ALMANACK_TABLE))
    missing = [col for col in _INSERT_COLUMNS if col not in columns]
    if missing:
        raise RuntimeError(
            f"{ALMANACK_TABLE} is missing expected columns: " + ", ".join(missing)
        )


def refresh_person_almanack(
    conn: sqlite3.Connection,
    *,
    simulation_year: int | None = None,
    updated_at: str | None = None,
) -> int:
    """Refresh every cached Almanack metric from explicit save.sqlite facts."""

    ensure_person_almanack_schema(conn)
    now = updated_at or _utc_now_iso()
    max_event_id = _max_event_id(conn)
    accumulators: dict[tuple[str, int, str], _MetricAccumulator] = {}

    _add_event_metrics(conn, accumulators)
    _add_detailed_child_metrics(conn, accumulators)
    _add_passive_child_metrics(conn, accumulators)
    _add_legal_metrics(conn, accumulators)
    _add_displacement_metrics(conn, accumulators)
    _add_relationship_anomaly_metrics(conn, accumulators)
    _add_family_reach_metrics(conn, accumulators)
    _add_office_metrics(conn, accumulators)
    _add_life_span_metrics(conn, accumulators)
    _add_strange_record_metrics(conn, accumulators)
    _add_crossroads_metrics(conn, accumulators)
    _add_archive_metrics(conn, accumulators)
    _apply_context(conn, accumulators, simulation_year=simulation_year)

    conn.execute(f"DELETE FROM {_quote_identifier(ALMANACK_TABLE)}")
    conn.execute(f"DELETE FROM {_quote_identifier(ALMANACK_EVIDENCE_TABLE)}")
    live_accs = [
        acc
        for acc in accumulators.values()
        if float(acc.metric_value) > 0.0
        and int(_METRIC_BY_KEY.get(acc.metric_key, {}).get("enabled", 1)) != 0
    ]
    rows = [
        acc.insert_tuple(
            source_event_max_id=max_event_id,
            updated_year=simulation_year,
            updated_at=now,
        )
        for acc in live_accs
    ]
    if rows:
        columns_sql = ", ".join(_quote_identifier(col) for col in _INSERT_COLUMNS)
        placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {_quote_identifier(ALMANACK_TABLE)} ({columns_sql})
            VALUES ({placeholders})
            """,
            rows,
        )
    evidence_rows = []
    for acc in live_accs:
        evidence_rows.extend(_evidence_rows_for_accumulator(acc))
    if evidence_rows:
        columns_sql = ", ".join(_quote_identifier(col) for col in _EVIDENCE_COLUMNS)
        placeholders = ", ".join("?" for _ in _EVIDENCE_COLUMNS)
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {_quote_identifier(ALMANACK_EVIDENCE_TABLE)} ({columns_sql})
            VALUES ({placeholders})
            """,
            evidence_rows,
        )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {_quote_identifier(ALMANACK_META_TABLE)} (
            cache_key,
            row_count,
            source_event_max_id,
            cache_schema_version,
            updated_year,
            updated_at
        )
        VALUES ('default', ?, ?, ?, ?, ?)
        """,
        (len(rows), max_event_id, ALMANACK_SCHEMA_VERSION, simulation_year, now),
    )
    return len(rows)


def refresh_person_almanack_for_file(
    save_db_path: str | Path,
    *,
    simulation_year: int | None = None,
) -> int:
    """Open ``save.sqlite`` and refresh cached Almanack metrics."""

    with sqlite3.connect(save_db_path) as conn:
        conn.row_factory = sqlite3.Row
        count = refresh_person_almanack(conn, simulation_year=simulation_year)
        conn.commit()
        return count


def query_person_almanack(
    conn: sqlite3.Connection,
    *,
    metric_key: str | None = None,
    category: str = "All",
    life_filter: str = "All",
    source_filter: str = "Both",
    search: str = "",
    min_value: object = None,
    limit: int = 50,
    rank_mode: str = "Raw Value",
    include_context: bool = True,
    enabled_only: bool = True,
) -> list[dict[str, object]]:
    """Return enriched leaderboard rows from the cached Almanack table."""

    if not _table_exists(conn, ALMANACK_TABLE):
        return []
    row_limit = max(1, min(500, int(limit)))
    clauses: list[str] = []
    params: list[object] = []
    selected_metric = str(metric_key or "").strip()
    if selected_metric and selected_metric != "All":
        clauses.append("metric_key = ?")
        params.append(selected_metric)
    selected_category = str(category or "All").strip()
    if selected_category and selected_category != "All":
        clauses.append("metric_category = ?")
        params.append(selected_category)
    if enabled_only and _table_exists(conn, ALMANACK_DEFINITION_TABLE):
        clauses.append(
            f"""
            metric_key IN (
                SELECT metric_key
                FROM {_quote_identifier(ALMANACK_DEFINITION_TABLE)}
                WHERE enabled = 1
            )
            """
        )
    source = str(source_filter or "Both").strip().lower()
    if source.startswith("detailed"):
        clauses.append("source_kind = 'detailed'")
    elif source.startswith("passive"):
        clauses.append("source_kind = 'passive'")
    minimum = _coerce_float(min_value)
    if minimum is not None:
        clauses.append("metric_value >= ?")
        params.append(minimum)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_clause = _rank_order_clause(rank_mode)
    fetch_limit = max(row_limit * 20, 500)
    rows = _fetch_dicts(
        conn,
        f"""
        SELECT *
        FROM {_quote_identifier(ALMANACK_TABLE)}
        {where_sql}
        ORDER BY {order_clause}
        LIMIT ?
        """,
        (*params, fetch_limit),
    )
    enriched = _enrich_rows(conn, rows)
    filtered = _filter_enriched_rows(
        enriched,
        life_filter=life_filter,
        search=search,
    )
    for idx, row in enumerate(filtered[:row_limit], start=1):
        row["rank"] = idx
    if not include_context:
        for row in filtered[:row_limit]:
            for key in (
                "world_rank",
                "era_rank",
                "region_rank",
                "percentile",
                "z_score",
                "comparison_population",
                "era_bucket",
                "region_key",
                "context_json",
            ):
                row.pop(key, None)
    return filtered[:row_limit]


def query_person_almanack_evidence(
    conn: sqlite3.Connection,
    source_kind: str,
    person_id: int,
    metric_key: str,
    limit: int = 50,
) -> list[dict[str, object]]:
    """Return stored evidence rows for one Almanack person/metric row."""

    if not _table_exists(conn, ALMANACK_EVIDENCE_TABLE):
        return []
    row_limit = max(1, min(100, int(limit)))
    rows = _fetch_dicts(
        conn,
        f"""
        SELECT *
        FROM {_quote_identifier(ALMANACK_EVIDENCE_TABLE)}
        WHERE source_kind = ?
          AND person_id = ?
          AND metric_key = ?
        ORDER BY evidence_rank ASC
        LIMIT ?
        """,
        (str(source_kind or "detailed"), int(person_id), str(metric_key), row_limit),
    )
    for row in rows:
        row["related_people"] = _json_list(row.get("related_people_json"))
        row["caveat"] = _json_dict(row.get("caveat_json"))
    return rows


def query_person_almanack_duel(
    conn: sqlite3.Connection, person_a_id: int, person_b_id: int
) -> dict[str, object]:
    """Compare two people across cached Almanack rows."""

    if not _table_exists(conn, ALMANACK_TABLE):
        return {"person_a": {}, "person_b": {}, "categories": []}
    ids = (int(person_a_id), int(person_b_id))
    rows = _fetch_dicts(
        conn,
        f"""
        SELECT *
        FROM {_quote_identifier(ALMANACK_TABLE)}
        WHERE source_kind = 'detailed'
          AND person_id IN (?, ?)
        """,
        ids,
    )
    people = _load_detailed_people(conn, ids)
    by_person_metric = {
        (int(row["person_id"]), str(row["metric_key"])): row
        for row in rows
    }
    groups = [
        ("violence", ("murders_committed", "family_members_murdered")),
        (
            "family reach",
            (
                "children_recorded",
                "descendants_2g",
                "descendants_4g",
                "family_gravity",
            ),
        ),
        (
            "property caused/suffered",
            (
                "property_loss_caused",
                "property_loss_suffered",
                "property_crimes_committed",
                "property_crimes_suffered",
            ),
        ),
        (
            "romance volatility",
            (
                "distinct_partners",
                "distinct_paramours",
                "overlapping_romances",
                "risky_relationship_score",
            ),
        ),
        (
            "legal/consequence involvement",
            ("legal_entanglements", "displacements", "children_lost_young"),
        ),
        (
            "work and office",
            ("distinct_jobs", "job_losses", "offices_held"),
        ),
        ("life span", ("age_at_death",)),
        (
            "archive recognition",
            ("archive_narrative_heat", "archive_recognition_index"),
        ),
        (
            "hidden/strange heat",
            (
                "archive_hidden_heat",
                "archive_violet_marginalia_score",
                "contradictory_record_score",
                "single_strange_event_score",
            ),
        ),
    ]
    definitions = {d["metric_key"]: d for d in _metric_definitions(conn, enabled_only=False)}
    categories = []
    for label, metric_keys in groups:
        lines = []
        total_a = 0.0
        total_b = 0.0
        for key in metric_keys:
            row_a = by_person_metric.get((ids[0], key))
            row_b = by_person_metric.get((ids[1], key))
            value_a = _coerce_float(row_a.get("metric_value") if row_a else None) or 0.0
            value_b = _coerce_float(row_b.get("metric_value") if row_b else None) or 0.0
            total_a += value_a
            total_b += value_b
            if value_a == 0.0 and value_b == 0.0:
                continue
            display = definitions.get(key, {}).get("display_name", key)
            if value_a > value_b:
                leader = "A"
            elif value_b > value_a:
                leader = "B"
            else:
                leader = "Tie"
            lines.append(
                {
                    "metric_key": key,
                    "metric": display,
                    "a": round(value_a, 3),
                    "b": round(value_b, 3),
                    "leader": leader,
                    "why": _duel_reason(display, value_a, value_b),
                }
            )
        leader = "Tie"
        if total_a > total_b:
            leader = "A"
        elif total_b > total_a:
            leader = "B"
        categories.append(
            {
                "category": label,
                "leader": leader,
                "a_total": round(total_a, 3),
                "b_total": round(total_b, 3),
                "lines": lines,
            }
        )
    return {
        "person_a": _duel_person_payload(ids[0], people.get(ids[0])),
        "person_b": _duel_person_payload(ids[1], people.get(ids[1])),
        "categories": categories,
    }


def person_almanack_cache_status(conn: sqlite3.Connection) -> dict[str, object]:
    """Return row counts and freshness metadata for the Almanack cache."""

    max_event_id = _max_event_id(conn)
    if not _table_exists(conn, ALMANACK_TABLE):
        return {
            "exists": False,
            "row_count": 0,
            "source_event_max_id": 0,
            "current_event_max_id": max_event_id,
            "stale": bool(max_event_id),
            "updated_at": "",
            "updated_year": None,
        }
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS row_count,
            COALESCE(MAX(source_event_max_id), 0) AS source_event_max_id,
            MAX(updated_at) AS updated_at,
            MAX(updated_year) AS updated_year
        FROM {_quote_identifier(ALMANACK_TABLE)}
        """
    ).fetchone()
    meta = None
    if _table_exists(conn, ALMANACK_META_TABLE):
        meta_columns = set(_table_columns(conn, ALMANACK_META_TABLE))
        version_expr = (
            "cache_schema_version"
            if "cache_schema_version" in meta_columns
            else "NULL AS cache_schema_version"
        )
        meta = conn.execute(
            f"""
            SELECT row_count, source_event_max_id, {version_expr}, updated_at, updated_year
            FROM {_quote_identifier(ALMANACK_META_TABLE)}
            WHERE cache_key = 'default'
            """
        ).fetchone()
    source_event_max_id = (
        int(meta["source_event_max_id"] or 0)
        if meta is not None
        else int(row["source_event_max_id"] or 0)
        if row
        else 0
    )
    cache_schema_version = (
        _coerce_int(meta["cache_schema_version"]) if meta is not None else None
    )
    return {
        "exists": True,
        "row_count": int(row["row_count"] or 0) if row else 0,
        "source_event_max_id": source_event_max_id,
        "current_event_max_id": max_event_id,
        "cache_schema_version": cache_schema_version,
        "expected_cache_schema_version": ALMANACK_SCHEMA_VERSION,
        "stale": max_event_id > source_event_max_id
        or cache_schema_version != ALMANACK_SCHEMA_VERSION,
        "updated_at": (
            str(meta["updated_at"] or "")
            if meta is not None
            else str(row["updated_at"] or "")
            if row
            else ""
        ),
        "updated_year": (
            meta["updated_year"]
            if meta is not None
            else row["updated_year"]
            if row
            else None
        ),
    }


def _add_event_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_events"):
        return
    family_links = _family_links(conn)
    for event in conn.execute(
        """
        SELECT id, sim_year, event_type, settlement_key, region_key, payload_json
        FROM simulation_events
        ORDER BY id
        """
    ):
        event_id = int(event["id"])
        year = _coerce_int(event["sim_year"])
        event_type = str(event["event_type"] or "").strip()
        region_key = _coerce_int(event["region_key"])
        settlement_key = _coerce_int(event["settlement_key"])
        payload = event_payload_from_row(event, conn, expand=True)
        related = _payload_person_ids(payload)
        if event_type == "murder":
            killer_id = _coerce_int(payload.get("killer_person_id"))
            victim_id = _coerce_int(payload.get("victim_person_id"))
            if killer_id is not None:
                _acc(accumulators, "detailed", killer_id, "murders_committed").add_count(
                    year=year,
                    evidence=_event_evidence(
                        event_id,
                        year,
                        event_type,
                        "killer",
                        payload.get("incident_kind") or "murder",
                        payload_path="$.killer_person_id",
                        region_key=region_key,
                        settlement_key=settlement_key,
                        related_people=related,
                    ),
                )
            if victim_id is not None:
                for relative_id in family_links.get(victim_id, set()):
                    _acc(
                        accumulators,
                        "detailed",
                        relative_id,
                        "family_members_murdered",
                    ).add_distinct(
                        victim_id,
                        year=year,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            "family_of_victim",
                            f"family member {victim_id}",
                            payload_path="$.victim_person_id",
                            region_key=region_key,
                            settlement_key=settlement_key,
                            related_people=[victim_id, killer_id],
                        ),
                    )
        elif event_type == "property_crime":
            perpetrator_id = _coerce_int(
                payload.get("perpetrator_person_id") or payload.get("person_id")
            )
            target_id = _coerce_int(
                payload.get("target_person_id") or payload.get("victim_person_id")
            )
            loss = max(0.0, _coerce_float(payload.get("loss_value")) or 0.0)
            if perpetrator_id is not None:
                detail = payload.get("incident_kind") or payload.get("motive") or "property crime"
                _acc(
                    accumulators,
                    "detailed",
                    perpetrator_id,
                    "property_crimes_committed",
                ).add_count(
                    year=year,
                    evidence=_event_evidence(
                        event_id,
                        year,
                        event_type,
                        "perpetrator",
                        detail,
                        payload_path="$.perpetrator_person_id",
                        region_key=region_key,
                        settlement_key=settlement_key,
                        related_people=related,
                    ),
                )
                if loss > 0.0:
                    _acc(
                        accumulators,
                        "detailed",
                        perpetrator_id,
                        "property_loss_caused",
                    ).add_value(
                        year=year,
                        value=loss,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            "perpetrator",
                            f"loss {loss:.3f}",
                            payload_path="$.loss_value",
                            region_key=region_key,
                            settlement_key=settlement_key,
                            related_people=related,
                            contribution_value=loss,
                        ),
                    )
            if target_id is not None:
                _acc(
                    accumulators,
                    "detailed",
                    target_id,
                    "property_crimes_suffered",
                ).add_count(
                    year=year,
                    evidence=_event_evidence(
                        event_id,
                        year,
                        event_type,
                        "target",
                        payload.get("incident_kind") or "property crime",
                        payload_path="$.target_person_id",
                        region_key=region_key,
                        settlement_key=settlement_key,
                        related_people=related,
                    ),
                )
                if loss > 0.0:
                    _acc(
                        accumulators,
                        "detailed",
                        target_id,
                        "property_loss_suffered",
                    ).add_value(
                        year=year,
                        value=loss,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            "target",
                            f"loss {loss:.3f}",
                            payload_path="$.loss_value",
                            region_key=region_key,
                            settlement_key=settlement_key,
                            related_people=related,
                            contribution_value=loss,
                        ),
                    )
        elif event_type in {"couple_formed", "same_sex_couple_formed"}:
            _add_pair_metric(
                accumulators,
                payload,
                "distinct_partners",
                event_id=event_id,
                year=year,
                event_type=event_type,
                role="partner",
                region_key=region_key,
                settlement_key=settlement_key,
            )
        elif event_type == "paramour_formed":
            _add_pair_metric(
                accumulators,
                payload,
                "distinct_paramours",
                event_id=event_id,
                year=year,
                event_type=event_type,
                role="paramour",
                region_key=region_key,
                settlement_key=settlement_key,
            )
        elif event_type == "job_assigned":
            person_id = _coerce_int(payload.get("person_id"))
            job = _clean_job(payload.get("job") or payload.get("job_family"))
            if person_id is not None and job:
                _acc(accumulators, "detailed", person_id, "distinct_jobs").add_distinct(
                    job.lower(),
                    year=year,
                    evidence=_event_evidence(
                        event_id,
                        year,
                        event_type,
                        "worker",
                        job,
                        payload_path="$.job",
                        region_key=region_key,
                        settlement_key=settlement_key,
                        related_people=[person_id],
                    ),
                )
        elif event_type == "job_lost":
            person_id = _coerce_int(payload.get("person_id"))
            if person_id is not None:
                old_job = _clean_job(payload.get("old_job") or payload.get("job"))
                detail = old_job or "job loss"
                _acc(accumulators, "detailed", person_id, "job_losses").add_count(
                    year=year,
                    evidence=_event_evidence(
                        event_id,
                        year,
                        event_type,
                        "worker",
                        detail,
                        payload_path="$.old_job",
                        region_key=region_key,
                        settlement_key=settlement_key,
                        related_people=[person_id],
                    ),
                )


def _add_pair_metric(
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
    payload: Mapping[str, object],
    metric_key: str,
    *,
    event_id: int,
    year: int | None,
    event_type: str,
    role: str,
    region_key: int | None,
    settlement_key: int | None,
) -> None:
    person_a = _coerce_int(payload.get("person_a_id"))
    person_b = _coerce_int(payload.get("person_b_id"))
    if person_a is None or person_b is None:
        return
    _acc(accumulators, "detailed", person_a, metric_key).add_distinct(
        person_b,
        year=year,
        evidence=_event_evidence(
            event_id,
            year,
            event_type,
            role,
            f"person {person_b}",
            payload_path="$.person_b_id",
            region_key=region_key,
            settlement_key=settlement_key,
            related_people=[person_a, person_b],
        ),
    )
    _acc(accumulators, "detailed", person_b, metric_key).add_distinct(
        person_a,
        year=year,
        evidence=_event_evidence(
            event_id,
            year,
            event_type,
            role,
            f"person {person_a}",
            payload_path="$.person_a_id",
            region_key=region_key,
            settlement_key=settlement_key,
            related_people=[person_a, person_b],
        ),
    )


def _add_detailed_child_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    people = _people_context(conn)
    if not people:
        return
    for child_id, child in people.items():
        birthyear = _coerce_int(child.get("birthyear"))
        deathyear = _coerce_int(child.get("deathyear"))
        child_name = str(child.get("name") or "").strip() or f"child {child_id}"
        is_alive = bool(_coerce_int(child.get("is_alive")))
        for parent_key in ("father_id", "mother_id"):
            parent_id = _coerce_int(child.get(parent_key))
            if parent_id is None:
                continue
            _acc(accumulators, "detailed", parent_id, "children_recorded").add_distinct(
                child_id,
                year=birthyear,
                evidence={
                    "source_table": "simulation_people",
                    "source_id": child_id,
                    "source_year": birthyear,
                    "role": parent_key.replace("_id", ""),
                    "summary": f"{child_name} recorded as child",
                    "payload_path": parent_key,
                    "region_key": child.get("birthplace_region_key") or child.get("current_region_key"),
                    "settlement_key": child.get("birthplace_settlement_key") or child.get("current_settlement_key"),
                    "related_people": [child_id],
                },
            )
            if (
                not is_alive
                and birthyear is not None
                and deathyear is not None
                and deathyear - birthyear < 16
            ):
                age_at_death = deathyear - birthyear
                _acc(
                    accumulators,
                    "detailed",
                    parent_id,
                    "children_lost_young",
                ).add_distinct(
                    child_id,
                    year=deathyear,
                    evidence={
                        "source_table": "simulation_people",
                        "source_id": child_id,
                        "source_year": deathyear,
                        "role": "parent",
                        "summary": f"{child_name} died in {deathyear} at age {age_at_death}",
                        "payload_path": "deathyear",
                        "region_key": child.get("current_region_key") or child.get("birthplace_region_key"),
                        "settlement_key": child.get("current_settlement_key") or child.get("birthplace_settlement_key"),
                        "related_people": [child_id],
                        "caveat": {"age_at_death": age_at_death, "young_threshold": 16},
                    },
                )


def _add_passive_child_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_people_light"):
        return
    columns = set(_table_columns(conn, "simulation_people_light"))
    if "child_count" not in columns:
        return
    rows = conn.execute(
        """
        SELECT person_id, child_count, child_birthyears_json, child_person_ids_json
        FROM simulation_people_light
        WHERE child_count > 0
        """
    ).fetchall()
    for row in rows:
        person_id = _coerce_int(row["person_id"])
        child_count = _coerce_int(row["child_count"]) or 0
        if person_id is None or child_count <= 0:
            continue
        child_years = _json_list(row["child_birthyears_json"])
        child_ids = _json_list(row["child_person_ids_json"])
        evidence = {
            "source_table": "simulation_people_light",
            "source_id": person_id,
            "role": "passive_parent",
            "summary": f"{child_count} explicit passive child count",
            "payload_path": "child_count",
            "child_count": child_count,
            "related_people": child_ids[:8],
        }
        years = [_coerce_int(year) for year in child_years]
        valid_years = [int(year) for year in years if year is not None]
        acc = _acc(accumulators, "passive", person_id, "children_recorded")
        acc.metric_value = float(child_count)
        acc.metric_count = child_count
        if valid_years:
            acc.first_year = min(valid_years)
            acc.last_year = max(valid_years)
            evidence["source_year"] = min(valid_years)
        acc._add_evidence(evidence, default_contribution=child_count)


def _add_legal_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if _table_exists(conn, "simulation_legal_fallout"):
        for row in conn.execute(
            """
            SELECT fallout_id, source_event_id, fallout_type, status,
                   principal_person_id, opposing_person_id, related_person_id,
                   region_key, settlement_key, severity, start_year
            FROM simulation_legal_fallout
            """
        ):
            for role_key, role in (
                ("principal_person_id", "principal"),
                ("opposing_person_id", "opposing"),
                ("related_person_id", "related"),
            ):
                person_id = _coerce_int(row[role_key])
                if person_id is None:
                    continue
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "legal_entanglements",
                ).add_count(
                    year=_coerce_int(row["start_year"]),
                    evidence={
                        "source_table": "simulation_legal_fallout",
                        "source_id": row["fallout_id"],
                        "source_year": row["start_year"],
                        "role": role,
                        "summary": f"{row['fallout_type']} / {row['status']}",
                        "payload_path": role_key,
                        "region_key": row["region_key"],
                        "settlement_key": row["settlement_key"],
                        "contribution_value": max(1.0, _coerce_float(row["severity"]) or 0.0),
                        "related_people": [
                            row["principal_person_id"],
                            row["opposing_person_id"],
                            row["related_person_id"],
                        ],
                    },
                )
    if _table_exists(conn, "simulation_legal_adjudications"):
        for row in conn.execute(
            """
            SELECT adjudication_id, source_event_id, adjudication_type, outcome,
                   principal_person_id, opposing_person_id, related_person_id,
                   region_key, settlement_key, severity, adjudication_year
            FROM simulation_legal_adjudications
            """
        ):
            for role_key, role in (
                ("principal_person_id", "principal_adjudication"),
                ("opposing_person_id", "opposing_adjudication"),
                ("related_person_id", "related_adjudication"),
            ):
                person_id = _coerce_int(row[role_key])
                if person_id is None:
                    continue
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "legal_entanglements",
                ).add_count(
                    year=_coerce_int(row["adjudication_year"]),
                    evidence={
                        "source_table": "simulation_legal_adjudications",
                        "source_id": row["adjudication_id"],
                        "source_year": row["adjudication_year"],
                        "role": role,
                        "summary": f"{row['adjudication_type']} / {row['outcome']}",
                        "payload_path": role_key,
                        "region_key": row["region_key"],
                        "settlement_key": row["settlement_key"],
                        "contribution_value": max(1.0, _coerce_float(row["severity"]) or 0.0),
                        "related_people": [
                            row["principal_person_id"],
                            row["opposing_person_id"],
                            row["related_person_id"],
                        ],
                    },
                )
    if _table_exists(conn, "simulation_event_people") and _table_exists(conn, "simulation_events"):
        for row in conn.execute(
            """
            SELECT ep.event_id, ep.person_id, ep.role, e.sim_year, e.event_type,
                   e.region_key, e.settlement_key
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            WHERE ep.role = 'witness'
              AND e.event_type IN ('murder', 'property_crime', 'affair_scandal')
            """
        ):
            person_id = _coerce_int(row["person_id"])
            if person_id is None:
                continue
            _acc(accumulators, "detailed", person_id, "legal_entanglements").add_count(
                year=_coerce_int(row["sim_year"]),
                evidence={
                    "source_table": "simulation_event_people",
                    "source_id": row["event_id"],
                    "source_year": row["sim_year"],
                    "role": "witness",
                    "summary": f"witness in {row['event_type']}",
                    "payload_path": "witness_person_ids",
                    "region_key": row["region_key"],
                    "settlement_key": row["settlement_key"],
                    "related_people": [person_id],
                },
            )


def _add_displacement_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_event_moves"):
        return
    for row in conn.execute(
        """
        SELECT m.event_id, m.moved_person_id, m.from_settlement_key, m.to_settlement_key,
               m.from_region_key, m.to_region_key, m.move_reason, e.sim_year
        FROM simulation_event_moves m
        LEFT JOIN simulation_events e ON e.id = m.event_id
        """
    ):
        person_id = _coerce_int(row["moved_person_id"])
        if person_id is None:
            continue
        _acc(accumulators, "detailed", person_id, "displacements").add_count(
            year=_coerce_int(row["sim_year"]),
            evidence={
                "source_table": "simulation_event_moves",
                "source_id": row["event_id"],
                "source_year": row["sim_year"],
                "role": "moved",
                "summary": str(row["move_reason"] or "settlement move"),
                "payload_path": "moved_person_id",
                "region_key": row["to_region_key"] or row["from_region_key"],
                "settlement_key": row["to_settlement_key"] or row["from_settlement_key"],
                "related_people": [person_id],
            },
        )


def _add_relationship_anomaly_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_events"):
        return
    people = _people_context(conn)
    crime_people = _crime_or_legal_people(conn)
    intervals: dict[int, list[tuple[int, int, str, int]]] = defaultdict(list)
    active: dict[tuple[str, int, int], tuple[int, int]] = {}
    current_year = _current_year(conn) or 0
    for event in conn.execute(
        """
        SELECT id, sim_year, event_type, region_key, settlement_key, payload_json
        FROM simulation_events
        WHERE event_type IN (
            'couple_formed', 'same_sex_couple_formed', 'paramour_formed',
            'couple_dissolved', 'paramour_ended'
        )
        ORDER BY COALESCE(sim_year, 0), id
        """
    ):
        event_id = int(event["id"])
        year = _coerce_int(event["sim_year"]) or current_year
        payload = event_payload_from_row(event, conn, expand=True)
        event_type = str(event["event_type"] or "")
        person_a = _coerce_int(payload.get("person_a_id"))
        person_b = _coerce_int(payload.get("person_b_id"))
        if person_a is None or person_b is None:
            continue
        pair = tuple(sorted((person_a, person_b)))
        relation_kind = "paramour" if "paramour" in event_type else "partner"
        active_key = (relation_kind, pair[0], pair[1])
        related = [person_a, person_b]
        if event_type in {"couple_formed", "same_sex_couple_formed", "paramour_formed"}:
            birth_a = _coerce_int(people.get(person_a, {}).get("birthyear"))
            birth_b = _coerce_int(people.get(person_b, {}).get("birthyear"))
            if birth_a is not None and birth_b is not None:
                gap = abs(birth_a - birth_b)
                for person_id in pair:
                    _acc(
                        accumulators,
                        "detailed",
                        person_id,
                        "largest_relationship_age_gap",
                    ).set_max(
                        gap,
                        year=year,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            relation_kind,
                            f"age gap {gap}",
                            payload_path="person_a_id/person_b_id",
                            region_key=_coerce_int(event["region_key"]),
                            settlement_key=_coerce_int(event["settlement_key"]),
                            related_people=related,
                            contribution_value=gap,
                        ),
                    )
            active[active_key] = (year, event_id)
            risk = 0.0
            if event_type == "paramour_formed":
                risk += 1.0
            if payload.get("kinship_exception"):
                risk += 3.0
            if person_a in crime_people or person_b in crime_people:
                risk += 1.0
            if risk > 0.0:
                for person_id in pair:
                    _acc(
                        accumulators,
                        "detailed",
                        person_id,
                        "risky_relationship_score",
                    ).set_max(
                        risk,
                        year=year,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            "relationship_risk",
                            f"risk {risk:.1f}",
                            payload_path="kinship_exception",
                            region_key=_coerce_int(event["region_key"]),
                            settlement_key=_coerce_int(event["settlement_key"]),
                            related_people=related,
                            contribution_value=risk,
                        ),
                    )
        else:
            start_year, start_event_id = active.pop(active_key, (year, event_id))
            for person_id, other_id in ((person_a, person_b), (person_b, person_a)):
                intervals[person_id].append((start_year, year, relation_kind, other_id))
            reasons = [
                str(payload.get("end_reason") or payload.get("breakup_reason") or "").lower()
            ]
            reasons.extend(str(v).lower() for v in _json_list(payload.get("end_reasons")))
            reason_blob = " ".join(reasons)
            if any(token in reason_blob for token in ("death", "scandal", "disappear", "abandon")):
                for person_id in pair:
                    _acc(
                        accumulators,
                        "detailed",
                        person_id,
                        "relationship_end_consequence_count",
                    ).add_count(
                        year=year,
                        evidence=_event_evidence(
                            event_id,
                            year,
                            event_type,
                            "relationship_end",
                            reason_blob or "consequence-linked ending",
                            payload_path="end_reason",
                            region_key=_coerce_int(event["region_key"]),
                            settlement_key=_coerce_int(event["settlement_key"]),
                            related_people=related,
                        ),
                    )
    for (relation_kind, a, b), (start_year, _event_id) in active.items():
        for person_id, other_id in ((a, b), (b, a)):
            intervals[person_id].append((start_year, current_year or start_year, relation_kind, other_id))
    for person_id, person_intervals in intervals.items():
        overlap = _max_interval_overlap(person_intervals)
        if overlap > 0:
            _acc(accumulators, "detailed", person_id, "overlapping_romances").set_max(
                overlap,
                evidence={
                    "source_table": "simulation_events",
                    "source_id": None,
                    "role": "relationship_overlap",
                    "summary": f"maximum {overlap} simultaneous relationships",
                    "payload_path": "relationship intervals",
                    "contribution_value": overlap,
                    "related_people": [item[3] for item in person_intervals[:8]],
                },
            )
    co_parents: dict[int, set[int]] = defaultdict(set)
    for child in people.values():
        father = _coerce_int(child.get("father_id"))
        mother = _coerce_int(child.get("mother_id"))
        if father is not None and mother is not None:
            co_parents[father].add(mother)
            co_parents[mother].add(father)
    for person_id, partners in co_parents.items():
        if partners:
            _acc(
                accumulators,
                "detailed",
                person_id,
                "children_across_distinct_partners",
            ).add_value(
                value=len(partners),
                count=len(partners),
                evidence={
                    "source_table": "simulation_people",
                    "role": "co_parent",
                    "summary": f"{len(partners)} distinct recorded co-parent(s)",
                    "payload_path": "father_id/mother_id",
                    "contribution_value": len(partners),
                    "related_people": sorted(partners)[:12],
                },
            )


def _add_family_reach_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    people = _people_context(conn)
    if not people:
        return
    children_by_parent: dict[int, set[int]] = defaultdict(set)
    for child_id, child in people.items():
        for parent_key in ("father_id", "mother_id"):
            parent_id = _coerce_int(child.get(parent_key))
            if parent_id is not None:
                children_by_parent[parent_id].add(child_id)
    notable_people = _notable_people(conn, accumulators)
    crime_legal_people = _crime_or_legal_people(conn)
    event_people = _event_people_by_person(conn)
    person_regions = {
        person_id: _primary_region_for_person(person)
        for person_id, person in people.items()
    }
    for person_id in people:
        descendants_by_depth = _descendants_by_depth(person_id, children_by_parent, max_depth=4)
        descendants_2 = set().union(*[descendants_by_depth.get(i, set()) for i in (1, 2)])
        descendants_4 = set().union(*[descendants_by_depth.get(i, set()) for i in (1, 2, 3, 4)])
        if descendants_2:
            _acc(accumulators, "detailed", person_id, "descendants_2g").add_value(
                value=len(descendants_2),
                count=len(descendants_2),
                evidence={
                    "source_table": "simulation_people",
                    "role": "ancestor",
                    "summary": f"{len(descendants_2)} descendants within 2 generations",
                    "payload_path": "father_id/mother_id",
                    "contribution_value": len(descendants_2),
                    "related_people": sorted(descendants_2)[:12],
                },
            )
        if descendants_4:
            _acc(accumulators, "detailed", person_id, "descendants_4g").add_value(
                value=len(descendants_4),
                count=len(descendants_4),
                evidence={
                    "source_table": "simulation_people",
                    "role": "ancestor",
                    "summary": f"{len(descendants_4)} descendants within 4 generations",
                    "payload_path": "father_id/mother_id",
                    "contribution_value": len(descendants_4),
                    "related_people": sorted(descendants_4)[:12],
                },
            )
            notable = sorted(descendants_4 & notable_people)
            if notable:
                _acc(accumulators, "detailed", person_id, "notable_descendants").add_value(
                    value=len(notable),
                    count=len(notable),
                    evidence={
                        "source_table": "simulation_person_archive_scores",
                        "role": "ancestor",
                        "summary": f"{len(notable)} notable descendant(s)",
                        "payload_path": "archive thresholds",
                        "contribution_value": len(notable),
                        "related_people": notable[:12],
                    },
                )
            crime_legal = sorted(descendants_4 & crime_legal_people)
            if crime_legal:
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "crime_legal_descendants",
                ).add_value(
                    value=len(crime_legal),
                    count=len(crime_legal),
                    evidence={
                        "source_table": "simulation_event_people",
                        "role": "ancestor",
                        "summary": f"{len(crime_legal)} crime/legal descendant(s)",
                        "payload_path": "event roles/legal ledgers",
                        "contribution_value": len(crime_legal),
                        "related_people": crime_legal[:12],
                    },
                )
            regions = {person_regions.get(desc) for desc in descendants_4}
            regions.discard(None)
            if regions:
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "descendant_region_dispersion",
                ).add_value(
                    value=len(regions),
                    count=len(regions),
                    evidence={
                        "source_table": "simulation_people",
                        "role": "ancestor",
                        "summary": f"{len(regions)} descendant region(s)",
                        "payload_path": "current/birthplace region",
                        "contribution_value": len(regions),
                    },
                )
        close_family = set(children_by_parent.get(person_id, set()))
        for key in ("father_id", "mother_id", "partner_person_id", "paramour_person_id"):
            rel = _coerce_int(people.get(person_id, {}).get(key))
            if rel is not None:
                close_family.add(rel)
        gravity_events: set[int] = set()
        for rel in close_family:
            gravity_events.update(event_people.get(rel, set()))
        if gravity_events:
            _acc(accumulators, "detailed", person_id, "family_gravity").add_value(
                value=len(gravity_events),
                count=len(gravity_events),
                evidence={
                    "source_table": "simulation_event_people",
                    "role": "close_family_events",
                    "summary": f"{len(gravity_events)} event(s) around close family",
                    "payload_path": "event people roles",
                    "contribution_value": len(gravity_events),
                    "related_people": sorted(close_family)[:12],
                },
            )


def _add_office_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_office_holdings"):
        return
    columns = set(_table_columns(conn, "simulation_office_holdings"))
    if "holder_person_id" not in columns:
        return
    select_cols = ["holding_id", "holder_person_id", "start_sim_year", "end_sim_year"]
    if "seat_id" in columns:
        select_cols.append("seat_id")
    if "end_reason" in columns:
        select_cols.append("end_reason")
    rows = conn.execute(
        f"""
        SELECT {", ".join(_quote_identifier(col) for col in select_cols)}
        FROM simulation_office_holdings
        WHERE holder_person_id IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        person_id = _coerce_int(row["holder_person_id"])
        holding_id = _coerce_int(row["holding_id"])
        if person_id is None or holding_id is None:
            continue
        start_year = _coerce_int(row["start_sim_year"])
        end_year = _coerce_int(row["end_sim_year"])
        seat_id = _coerce_int(row["seat_id"]) if "seat_id" in columns else None
        end_reason = str(row["end_reason"] or "").strip() if "end_reason" in columns else ""
        summary_bits = [f"holding {holding_id}"]
        if seat_id is not None:
            summary_bits.append(f"seat {seat_id}")
        if end_reason:
            summary_bits.append(end_reason)
        _acc(accumulators, "detailed", person_id, "offices_held").add_distinct(
            holding_id,
            year=start_year,
            evidence={
                "source_table": "simulation_office_holdings",
                "source_id": holding_id,
                "source_year": start_year,
                "role": "office_holder",
                "summary": ", ".join(summary_bits),
                "payload_path": "holder_person_id",
                "contribution_value": 1.0,
                "related_people": [person_id],
                "caveat": {
                    "start_sim_year": start_year,
                    "end_sim_year": end_year,
                    "seat_id": seat_id,
                },
            },
        )


def _add_life_span_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    people = _people_context(conn)
    for person_id, person in people.items():
        birthyear = _coerce_int(person.get("birthyear"))
        deathyear = _coerce_int(person.get("deathyear"))
        if birthyear is None or deathyear is None or deathyear < birthyear:
            continue
        age = int(deathyear - birthyear)
        _acc(accumulators, "detailed", person_id, "age_at_death").add_value(
            value=float(age),
            count=1,
            year=deathyear,
            evidence={
                "source_table": "simulation_people",
                "source_id": person_id,
                "source_year": deathyear,
                "role": "deceased",
                "summary": f"died at age {age} in {deathyear}",
                "payload_path": "birthyear/deathyear",
                "contribution_value": float(age),
                "related_people": [person_id],
                "caveat": {"birthyear": birthyear, "deathyear": deathyear},
            },
        )


def _add_strange_record_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    people = _people_context(conn)
    if _table_exists(conn, "simulation_person_archive_scores"):
        columns = set(_table_columns(conn, "simulation_person_archive_scores"))
        if "narrative_heat_contradictions" in columns:
            for row in conn.execute(
                """
                SELECT person_id, narrative_heat_contradictions, source_event_max_id
                FROM simulation_person_archive_scores
                WHERE narrative_heat_contradictions > 0
                """
            ):
                person_id = _coerce_int(row["person_id"])
                value = _coerce_float(row["narrative_heat_contradictions"]) or 0.0
                if person_id is None or value <= 0.0:
                    continue
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "contradictory_record_score",
                ).add_value(
                    value=value,
                    evidence={
                        "source_table": "simulation_person_archive_scores",
                        "source_id": row["source_event_max_id"],
                        "role": "contradiction",
                        "summary": f"contradiction score {value:.2f}",
                        "payload_path": "narrative_heat_contradictions",
                        "contribution_value": value,
                    },
                )
    if _table_exists(conn, "simulation_event_people") and _table_exists(conn, "simulation_events"):
        years_by_person: dict[int, list[int]] = defaultdict(list)
        event_count_by_person: dict[int, set[int]] = defaultdict(set)
        max_importance_by_person: dict[int, tuple[float, int, int | None]] = {}
        roles_by_event_person: dict[tuple[int, int], set[str]] = defaultdict(set)
        for row in conn.execute(
            """
            SELECT ep.event_id, ep.person_id, ep.role, e.sim_year, e.payload_json
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            ORDER BY ep.person_id, e.sim_year, ep.event_id
            """
        ):
            person_id = _coerce_int(row["person_id"])
            if person_id is None:
                continue
            event_id = int(row["event_id"])
            year = _coerce_int(row["sim_year"])
            if year is not None:
                years_by_person[person_id].append(year)
            event_count_by_person[person_id].add(event_id)
            roles_by_event_person[(person_id, event_id)].add(str(row["role"] or "related"))
            payload = event_payload_from_row(row, conn, expand=True)
            importance = _coerce_float(payload.get("historical_importance")) or 0.0
            if importance > max_importance_by_person.get(person_id, (0.0, 0, None))[0]:
                max_importance_by_person[person_id] = (importance, event_id, year)
            deathyear = _coerce_int(people.get(person_id, {}).get("deathyear"))
            if deathyear is not None and year is not None and year > deathyear:
                _acc(accumulators, "detailed", person_id, "posthumous_mentions").add_distinct(
                    event_id,
                    year=year,
                    evidence={
                        "source_table": "simulation_event_people",
                        "source_id": event_id,
                        "source_year": year,
                        "role": "posthumous_mention",
                        "summary": f"mentioned {year - deathyear} years after death",
                        "payload_path": "sim_year/deathyear",
                        "contribution_value": 1.0,
                    },
                )
        for person_id, years in years_by_person.items():
            unique_years = sorted(set(years))
            if len(unique_years) >= 2:
                gap = max(b - a for a, b in zip(unique_years, unique_years[1:]))
                if gap > 0:
                    _acc(accumulators, "detailed", person_id, "largest_event_gap").set_max(
                        gap,
                        evidence={
                            "source_table": "simulation_event_people",
                            "role": "event_gap",
                            "summary": f"largest gap {gap} years",
                            "payload_path": "sim_year",
                            "contribution_value": gap,
                        },
                    )
        passive_roles = {
            "witness",
            "father",
            "mother",
            "child",
            "household_member",
            "dependent_minor",
            "related",
            "suspect",
            "betrayed_partner",
        }
        for (person_id, event_id), roles in roles_by_event_person.items():
            if roles and roles.issubset(passive_roles):
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "witness_or_relative_only_mentions",
                ).add_distinct(
                    event_id,
                    evidence={
                        "source_table": "simulation_event_people",
                        "source_id": event_id,
                        "role": ",".join(sorted(roles)),
                        "summary": "only witness/relative/passive roles",
                        "payload_path": "role",
                    },
                )
        for person_id, (importance, event_id, year) in max_importance_by_person.items():
            if len(event_count_by_person.get(person_id, set())) <= 2 and importance >= 0.75:
                score = round(importance * 100.0, 4)
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "single_strange_event_score",
                ).set_max(
                    score,
                    year=year,
                    evidence={
                        "source_table": "simulation_events",
                        "source_id": event_id,
                        "source_year": year,
                        "role": "sparse_major_event",
                        "summary": f"importance {importance:.2f} with sparse records",
                        "payload_path": "$.historical_importance",
                        "contribution_value": score,
                    },
                )
    if _table_exists(conn, "simulation_event_records"):
        for row in conn.execute(
            """
            SELECT r.record_id, r.event_id, r.visibility_state, r.confidence,
                   r.public_actor_person_id, r.public_victim_person_id,
                   r.distortion_json, e.sim_year, e.region_key, e.settlement_key
            FROM simulation_event_records r
            LEFT JOIN simulation_events e ON e.id = r.event_id
            WHERE r.visibility_state IN ('public_unknown', 'rumored', 'misattributed')
               OR r.confidence < 1.0
               OR COALESCE(r.distortion_json, '{}') NOT IN ('', '{}')
            """
        ):
            for key, role in (
                ("public_actor_person_id", "uncertain_actor"),
                ("public_victim_person_id", "uncertain_victim"),
            ):
                person_id = _coerce_int(row[key])
                if person_id is None:
                    continue
                _acc(
                    accumulators,
                    "detailed",
                    person_id,
                    "uncertain_role_events",
                ).add_distinct(
                    row["event_id"],
                    year=_coerce_int(row["sim_year"]),
                    evidence={
                        "source_table": "simulation_event_records",
                        "source_id": row["record_id"],
                        "source_year": row["sim_year"],
                        "role": role,
                        "summary": f"{row['visibility_state']} confidence {row['confidence']}",
                        "payload_path": key,
                        "region_key": row["region_key"],
                        "settlement_key": row["settlement_key"],
                        "caveat": _json_dict(row["distortion_json"]),
                    },
                )


def _add_crossroads_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    people_touched: dict[int, set[int]] = defaultdict(set)
    roles: dict[int, set[str]] = defaultdict(set)
    event_types: dict[int, set[str]] = defaultdict(set)
    settlements: dict[int, set[int]] = defaultdict(set)
    regions: dict[int, set[int]] = defaultdict(set)
    jobs: dict[int, set[str]] = defaultdict(set)
    legal_counts: dict[int, int] = defaultdict(int)
    relationship_edges: dict[int, set[int]] = defaultdict(set)
    family_edges: dict[int, set[int]] = defaultdict(set)

    people = _people_context(conn)
    for person_id, person in people.items():
        for key in ("father_id", "mother_id", "partner_person_id", "paramour_person_id"):
            other = _coerce_int(person.get(key))
            if other is not None:
                family_edges[person_id].add(other)
                people_touched[person_id].add(other)
        job = _clean_job(person.get("job"))
        if job:
            jobs[person_id].add(job.lower())

    if _table_exists(conn, "simulation_event_people") and _table_exists(conn, "simulation_events"):
        event_people: dict[int, set[int]] = defaultdict(set)
        rows = conn.execute(
            """
            SELECT ep.event_id, ep.person_id, ep.role, e.event_type, e.region_key,
                   e.settlement_key, e.payload_json
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            """
        ).fetchall()
        for row in rows:
            person_id = _coerce_int(row["person_id"])
            if person_id is None:
                continue
            event_id = int(row["event_id"])
            event_people[event_id].add(person_id)
            roles[person_id].add(str(row["role"] or "related"))
            event_types[person_id].add(str(row["event_type"] or "event"))
            region_key = _coerce_int(row["region_key"])
            settlement_key = _coerce_int(row["settlement_key"])
            if region_key is not None:
                regions[person_id].add(region_key)
            if settlement_key is not None:
                settlements[person_id].add(settlement_key)
            payload = event_payload_from_row(row, conn, expand=True)
            if str(row["event_type"] or "") == "job_assigned":
                job = _clean_job(payload.get("job") or payload.get("job_family"))
                if job:
                    jobs[person_id].add(job.lower())
            if str(row["event_type"] or "") in {
                "couple_formed",
                "same_sex_couple_formed",
                "paramour_formed",
            }:
                for other in _payload_person_ids(payload):
                    if other != person_id:
                        relationship_edges[person_id].add(other)
        for event_id, participants in event_people.items():
            for person_id in participants:
                people_touched[person_id].update(pid for pid in participants if pid != person_id)

    if _table_exists(conn, "simulation_event_moves"):
        for row in conn.execute(
            """
            SELECT moved_person_id, from_settlement_key, to_settlement_key,
                   from_region_key, to_region_key
            FROM simulation_event_moves
            """
        ):
            person_id = _coerce_int(row["moved_person_id"])
            if person_id is None:
                continue
            for key in ("from_settlement_key", "to_settlement_key"):
                value = _coerce_int(row[key])
                if value is not None:
                    settlements[person_id].add(value)
            for key in ("from_region_key", "to_region_key"):
                value = _coerce_int(row[key])
                if value is not None:
                    regions[person_id].add(value)

    for table, columns in (
        ("simulation_legal_fallout", ("principal_person_id", "opposing_person_id", "related_person_id")),
        ("simulation_legal_adjudications", ("principal_person_id", "opposing_person_id", "related_person_id")),
        ("simulation_obligations", ("owed_by_person_id", "owed_to_person_id")),
        ("simulation_reputation_marks", ("person_id",)),
    ):
        if not _table_exists(conn, table):
            continue
        select_cols = [col for col in columns if col in _table_columns(conn, table)]
        if not select_cols:
            continue
        for row in conn.execute(
            f"SELECT {', '.join(_quote_identifier(col) for col in select_cols)} FROM {_quote_identifier(table)}"
        ):
            row_people = [_coerce_int(row[col]) for col in select_cols]
            row_people = [pid for pid in row_people if pid is not None]
            for person_id in row_people:
                legal_counts[person_id] += 1
                people_touched[person_id].update(pid for pid in row_people if pid != person_id)

    candidate_ids = set(people)
    for bucket in (
        people_touched,
        roles,
        event_types,
        settlements,
        regions,
        jobs,
        legal_counts,
        relationship_edges,
        family_edges,
    ):
        candidate_ids.update(bucket.keys())
    for person_id in candidate_ids:
        components = {
            "event_families": float(len(event_types.get(person_id, set()))) * 3.0,
            "roles": float(len(roles.get(person_id, set()))) * 2.0,
            "settlements": float(len(settlements.get(person_id, set()))) * 1.5,
            "regions": float(len(regions.get(person_id, set()))) * 2.0,
            "jobs": float(len(jobs.get(person_id, set()))) * 1.5,
            "family_edges": float(len(family_edges.get(person_id, set()))) * 1.0,
            "relationship_edges": float(len(relationship_edges.get(person_id, set()))) * 2.0,
            "legal_rows": float(legal_counts.get(person_id, 0)) * 2.5,
            "distinct_people": float(len(people_touched.get(person_id, set()))) * 0.5,
        }
        score = sum(components.values())
        if score <= 0.0:
            continue
        acc = _acc(accumulators, "detailed", person_id, "crossroads_index")
        acc.metric_value = round(score, 4)
        acc.metric_count = int(sum(1 for value in components.values() if value > 0.0))
        for key, value in components.items():
            acc.add_component(key, value)
        acc._add_evidence(
            {
                "source_table": "simulation_event_people",
                "role": "crossroads_components",
                "summary": " / ".join(
                    f"{key} {value:.1f}" for key, value in components.items() if value > 0.0
                ),
                "payload_path": "derived components",
                "contribution_value": score,
                "related_people": sorted(people_touched.get(person_id, set()))[:12],
            },
            default_contribution=score,
        )


def _add_archive_metrics(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
) -> None:
    if not _table_exists(conn, "simulation_person_archive_scores"):
        return
    columns = set(_table_columns(conn, "simulation_person_archive_scores"))
    archive_columns = [m for m in _ARCHIVE_METRICS if str(m.get("column")) in columns]
    if not archive_columns:
        return
    select_cols = ["person_id", "source_event_max_id", *[str(m["column"]) for m in archive_columns]]
    rows = conn.execute(
        f"""
        SELECT {", ".join(_quote_identifier(col) for col in select_cols)}
        FROM simulation_person_archive_scores
        """
    ).fetchall()
    for row in rows:
        person_id = _coerce_int(row["person_id"])
        if person_id is None:
            continue
        for metric in archive_columns:
            metric_key = str(metric["key"])
            if metric_key == "contradictory_record_score":
                continue
            value = _coerce_float(row[str(metric["column"])]) or 0.0
            if value <= 0.0:
                continue
            acc = _acc(accumulators, "detailed", person_id, metric_key)
            acc.metric_value = float(value)
            acc.metric_count = 0
            acc._add_evidence(
                {
                    "source_table": "simulation_person_archive_scores",
                    "source_id": row["source_event_max_id"],
                    "role": "archive_score",
                    "summary": f"{metric['label']} {value:.2f}",
                    "payload_path": str(metric["column"]),
                    "contribution_value": value,
                },
                default_contribution=value,
            )


def _apply_context(
    conn: sqlite3.Connection,
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
    *,
    simulation_year: int | None,
) -> None:
    people = _people_context(conn)
    current_year = _current_year(conn) or simulation_year
    live_accs = [
        acc
        for acc in accumulators.values()
        if float(acc.metric_value) > 0.0
        and int(_METRIC_BY_KEY.get(acc.metric_key, {}).get("enabled", 1)) != 0
    ]
    for acc in live_accs:
        person = people.get(acc.person_id, {})
        if acc.region_key is None:
            acc.region_key = _primary_region_for_person(person)
        midpoint = _metric_midpoint_year(acc)
        birthyear = _coerce_int(person.get("birthyear"))
        era_year = midpoint if midpoint is not None else birthyear if birthyear is not None else current_year
        era_bucket = _era_bucket(era_year)
        acc.context["era_bucket"] = era_bucket
        acc.context["region_key"] = acc.region_key
        acc.context["comparison_basis"] = "world"
    by_metric: dict[str, list[_MetricAccumulator]] = defaultdict(list)
    by_metric_era: dict[tuple[str, int | None], list[_MetricAccumulator]] = defaultdict(list)
    by_metric_region: dict[tuple[str, int | None], list[_MetricAccumulator]] = defaultdict(list)
    for acc in live_accs:
        by_metric[acc.metric_key].append(acc)
        by_metric_era[(acc.metric_key, _coerce_int(acc.context.get("era_bucket")))].append(acc)
        by_metric_region[(acc.metric_key, _coerce_int(acc.context.get("region_key")))].append(acc)
    for metric_key, rows in by_metric.items():
        ranked = _rank_accumulators(rows)
        values = [float(acc.metric_value) for acc in ranked]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        stdev = math.sqrt(variance)
        n = len(ranked)
        for rank, acc in enumerate(ranked, start=1):
            acc.context["world_rank"] = rank
            acc.context["comparison_population"] = n
            acc.context["percentile"] = round(100.0 if n == 1 else 100.0 * (n - rank) / (n - 1), 4)
            acc.context["z_score"] = round((float(acc.metric_value) - mean) / stdev, 4) if stdev > 0.0 else 0.0
    for rows in by_metric_era.values():
        for rank, acc in enumerate(_rank_accumulators(rows), start=1):
            acc.context["era_rank"] = rank
    for rows in by_metric_region.values():
        for rank, acc in enumerate(_rank_accumulators(rows), start=1):
            acc.context["region_rank"] = rank


def _acc(
    accumulators: dict[tuple[str, int, str], _MetricAccumulator],
    source_kind: str,
    person_id: int,
    metric_key: str,
) -> _MetricAccumulator:
    metric = _METRIC_BY_KEY[metric_key]
    key = (source_kind, int(person_id), metric_key)
    if key not in accumulators:
        accumulators[key] = _MetricAccumulator(
            person_id=int(person_id),
            source_kind=source_kind,
            metric_key=metric_key,
            metric_label=str(metric["label"]),
            metric_category=str(metric["category"]),
        )
    return accumulators[key]


def _enrich_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    current_year = _current_year(conn)
    detailed_ids = [
        int(row["person_id"])
        for row in rows
        if str(row["source_kind"]) == "detailed" and _coerce_int(row["person_id"]) is not None
    ]
    passive_ids = [
        int(row["person_id"])
        for row in rows
        if str(row["source_kind"]) == "passive" and _coerce_int(row["person_id"]) is not None
    ]
    detailed = _load_detailed_people(conn, detailed_ids)
    passive = _load_passive_people(conn, passive_ids)
    enriched: list[dict[str, object]] = []
    for row in rows:
        person_id = int(row["person_id"])
        source_kind = str(row["source_kind"])
        person = detailed.get(person_id) if source_kind == "detailed" else passive.get(person_id)
        output = dict(row)
        display_year = current_year or _coerce_int(row.get("updated_year"))
        output.update(
            _person_display_fields(
                person,
                source_kind=source_kind,
                current_year=display_year,
            )
        )
        evidence_payload = _json_dict(row.get("evidence_json"))
        output["evidence_summary"] = evidence_payload.get("summary", "")
        output["components"] = evidence_payload.get("components", {})
        enriched.append(output)
    return enriched


def _load_detailed_people(
    conn: sqlite3.Connection,
    person_ids: Iterable[int],
) -> dict[int, dict[str, object]]:
    ids = sorted({int(pid) for pid in person_ids})
    if not ids or not _table_exists(conn, "simulation_people"):
        return {}
    columns = set(_table_columns(conn, "simulation_people"))
    select_cols = [
        col
        for col in (
            "person_id",
            "is_alive",
            "first_name",
            "last_name",
            "birthyear",
            "deathyear",
            "gender",
            "species",
            "ethnic",
            "job",
            "current_settlement_key",
            "birthplace_settlement_key",
            "birthplace",
        )
        if col in columns
    ]
    if not select_cols:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT {", ".join(_quote_identifier(col) for col in select_cols)}
        FROM simulation_people
        WHERE person_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    settlement_labels = _settlement_labels(conn)
    people: dict[int, dict[str, object]] = {}
    for row in rows:
        data = {col: row[col] for col in select_cols}
        home = ""
        for key in ("current_settlement_key", "birthplace_settlement_key"):
            if key in data and data[key] is not None:
                home = settlement_labels.get(int(data[key]), "") or home
                if home:
                    break
        if not home and data.get("birthplace"):
            home = str(data.get("birthplace") or "")
        data["home"] = home
        people[int(row["person_id"])] = data
    return people


def _load_passive_people(
    conn: sqlite3.Connection,
    person_ids: Iterable[int],
) -> dict[int, dict[str, object]]:
    ids = sorted({int(pid) for pid in person_ids})
    if not ids or not _table_exists(conn, "simulation_people_light"):
        return {}
    relation = (
        "simulation_people_light_readable"
        if _table_exists(conn, "simulation_people_light_readable")
        else "simulation_people_light"
    )
    columns = set(_table_columns(conn, relation))
    select_cols = [
        col
        for col in (
            "person_id",
            "name",
            "is_alive",
            "birthyear",
            "deathyear",
            "gender",
            "species",
            "ethnic",
            "job_family",
            "current_settlement_id",
            "birthplace_settlement_id",
        )
        if col in columns
    ]
    if not select_cols:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT {", ".join(_quote_identifier(col) for col in select_cols)}
        FROM {_quote_identifier(relation)}
        WHERE person_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    people: dict[int, dict[str, object]] = {}
    for row in rows:
        data = {col: row[col] for col in select_cols}
        data["home"] = (
            data.get("current_settlement_id")
            or data.get("birthplace_settlement_id")
            or ""
        )
        people[int(row["person_id"])] = data
    return people


def _person_display_fields(
    person: Mapping[str, object] | None,
    *,
    source_kind: str,
    current_year: int | None,
) -> dict[str, object]:
    if not person:
        return {
            "name": f"Person #{source_kind}",
            "life": "",
            "birthyear": "",
            "deathyear": "",
            "age": "",
            "home": "",
        }
    if source_kind == "passive":
        name = str(person.get("name") or "").strip() or "Unnamed"
        job = person.get("job_family") or ""
    else:
        first = str(person.get("first_name") or "").strip()
        last = str(person.get("last_name") or "").strip()
        name = " ".join(part for part in (first, last) if part) or "Unnamed"
        job = person.get("job") or ""
    life = "Alive" if int(person.get("is_alive") or 0) else "Dead"
    birthyear = _coerce_int(person.get("birthyear"))
    deathyear = _coerce_int(person.get("deathyear"))
    end_year = deathyear if deathyear is not None else current_year
    age = ""
    if birthyear is not None and end_year is not None:
        age = end_year - birthyear
    return {
        "name": name,
        "life": life,
        "birthyear": birthyear if birthyear is not None else "",
        "deathyear": deathyear if deathyear is not None else "",
        "age": age,
        "home": person.get("home") or "",
        "job": job,
    }


def _filter_enriched_rows(
    rows: list[dict[str, object]],
    *,
    life_filter: str,
    search: str,
) -> list[dict[str, object]]:
    selected_life = str(life_filter or "All").strip()
    needle = str(search or "").strip().lower()
    out: list[dict[str, object]] = []
    for row in rows:
        if selected_life in {"Alive", "Dead"} and str(row.get("life") or "") != selected_life:
            continue
        if needle:
            haystack = " ".join(
                str(row.get(key, ""))
                for key in (
                    "person_id",
                    "name",
                    "home",
                    "metric_label",
                    "metric_category",
                    "evidence_summary",
                    "era_bucket",
                    "region_key",
                )
            ).lower()
            if needle not in haystack:
                continue
        out.append(row)
    return out


def _people_context(conn: sqlite3.Connection) -> dict[int, dict[str, object]]:
    if not _table_exists(conn, "simulation_people"):
        return {}
    columns = set(_table_columns(conn, "simulation_people"))
    wanted = [
        col
        for col in (
            "person_id",
            "father_id",
            "mother_id",
            "partner_person_id",
            "paramour_person_id",
            "is_alive",
            "first_name",
            "last_name",
            "name",
            "birthyear",
            "deathyear",
            "job",
            "person_json",
            "current_settlement_key",
            "birthplace_settlement_key",
        )
        if col in columns
    ]
    if not wanted:
        return {}
    settlement_regions = _settlement_region_map(conn)
    rows = conn.execute(
        f"SELECT {', '.join(_quote_identifier(col) for col in wanted)} FROM simulation_people"
    ).fetchall()
    out: dict[int, dict[str, object]] = {}
    for row in rows:
        data = {col: row[col] for col in wanted}
        person_json = _json_dict(data.get("person_json"))
        first_name = str(data.get("first_name") or person_json.get("first_name") or "").strip()
        last_name = str(data.get("last_name") or person_json.get("last_name") or "").strip()
        data["name"] = (
            str(data.get("name") or "").strip()
            or " ".join(part for part in (first_name, last_name) if part)
        )
        current_settlement_key = _coerce_int(data.get("current_settlement_key"))
        birthplace_settlement_key = _coerce_int(data.get("birthplace_settlement_key"))
        data["current_region_key"] = settlement_regions.get(current_settlement_key)
        data["birthplace_region_key"] = settlement_regions.get(birthplace_settlement_key)
        out[int(row["person_id"])] = data
    return out


def _settlement_region_map(conn: sqlite3.Connection) -> dict[int, int]:
    if not _table_exists(conn, "simulation_settlements"):
        return {}
    columns = set(_table_columns(conn, "simulation_settlements"))
    if "settlement_key" not in columns or "region_key" not in columns:
        return {}
    rows = conn.execute(
        """
        SELECT settlement_key, region_key
        FROM simulation_settlements
        WHERE region_key IS NOT NULL
        """
    ).fetchall()
    return {
        int(row["settlement_key"]): int(row["region_key"])
        for row in rows
        if row["settlement_key"] is not None and row["region_key"] is not None
    }


def _primary_region_for_person(person: Mapping[str, object]) -> int | None:
    for key in ("current_region_key", "birthplace_region_key"):
        value = _coerce_int(person.get(key))
        if value is not None:
            return value
    return None


def _family_links(conn: sqlite3.Connection) -> dict[int, set[int]]:
    people = _people_context(conn)
    links: dict[int, set[int]] = defaultdict(set)
    children_by_parent: dict[int, set[int]] = defaultdict(set)
    for child_id, child in people.items():
        for parent_key in ("father_id", "mother_id"):
            parent_id = _coerce_int(child.get(parent_key))
            if parent_id is not None:
                links[child_id].add(parent_id)
                links[parent_id].add(child_id)
                children_by_parent[parent_id].add(child_id)
        for key in ("partner_person_id", "paramour_person_id"):
            other = _coerce_int(child.get(key))
            if other is not None:
                links[child_id].add(other)
                links[other].add(child_id)
    sibling_groups: dict[tuple[int | None, int | None], set[int]] = defaultdict(set)
    for person_id, person in people.items():
        father = _coerce_int(person.get("father_id"))
        mother = _coerce_int(person.get("mother_id"))
        if father is not None or mother is not None:
            sibling_groups[(father, mother)].add(person_id)
    for siblings in sibling_groups.values():
        for person_id in siblings:
            links[person_id].update(pid for pid in siblings if pid != person_id)
    return links


def _descendants_by_depth(
    person_id: int, children_by_parent: Mapping[int, set[int]], *, max_depth: int
) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    frontier = set(children_by_parent.get(person_id, set()))
    seen: set[int] = set()
    depth = 1
    while frontier and depth <= max_depth:
        current = {pid for pid in frontier if pid not in seen}
        if not current:
            break
        out[depth].update(current)
        seen.update(current)
        next_frontier: set[int] = set()
        for child_id in current:
            next_frontier.update(children_by_parent.get(child_id, set()))
        frontier = next_frontier
        depth += 1
    return out


def _notable_people(
    conn: sqlite3.Connection,
    accumulators: Mapping[tuple[str, int, str], _MetricAccumulator],
) -> set[int]:
    notable: set[int] = set()
    if _table_exists(conn, "simulation_person_archive_scores"):
        columns = set(_table_columns(conn, "simulation_person_archive_scores"))
        select_cols = ["person_id"]
        for col in ("narrative_heat_total", "archive_recognition_index", "hidden_heat"):
            if col in columns:
                select_cols.append(col)
        rows = conn.execute(
            f"SELECT {', '.join(_quote_identifier(col) for col in select_cols)} FROM simulation_person_archive_scores"
        ).fetchall()
        for row in rows:
            if any((_coerce_float(row[col]) or 0.0) >= 25.0 for col in select_cols if col != "person_id"):
                pid = _coerce_int(row["person_id"])
                if pid is not None:
                    notable.add(pid)
    by_metric: dict[str, list[float]] = defaultdict(list)
    for acc in accumulators.values():
        if acc.source_kind == "detailed" and acc.metric_value > 0:
            by_metric[acc.metric_key].append(float(acc.metric_value))
    thresholds = {
        key: sorted(values, reverse=True)[max(0, math.ceil(len(values) * 0.1) - 1)]
        for key, values in by_metric.items()
        if values
    }
    for acc in accumulators.values():
        threshold = thresholds.get(acc.metric_key)
        if threshold is not None and float(acc.metric_value) >= threshold:
            notable.add(acc.person_id)
    return notable


def _crime_or_legal_people(conn: sqlite3.Connection) -> set[int]:
    out: set[int] = set()
    if _table_exists(conn, "simulation_event_people") and _table_exists(conn, "simulation_events"):
        for row in conn.execute(
            """
            SELECT DISTINCT ep.person_id
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            WHERE e.event_type IN ('murder', 'property_crime', 'affair_scandal')
              AND ep.role IN ('killer', 'victim', 'perpetrator', 'target', 'accused', 'witness')
            """
        ):
            pid = _coerce_int(row["person_id"])
            if pid is not None:
                out.add(pid)
    for table, cols in (
        ("simulation_legal_fallout", ("principal_person_id", "opposing_person_id", "related_person_id")),
        ("simulation_legal_adjudications", ("principal_person_id", "opposing_person_id", "related_person_id")),
    ):
        if not _table_exists(conn, table):
            continue
        table_cols = set(_table_columns(conn, table))
        select_cols = [col for col in cols if col in table_cols]
        if not select_cols:
            continue
        for row in conn.execute(
            f"SELECT {', '.join(_quote_identifier(col) for col in select_cols)} FROM {_quote_identifier(table)}"
        ):
            for col in select_cols:
                pid = _coerce_int(row[col])
                if pid is not None:
                    out.add(pid)
    return out


def _event_people_by_person(conn: sqlite3.Connection) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    if not _table_exists(conn, "simulation_event_people"):
        return out
    for row in conn.execute("SELECT person_id, event_id FROM simulation_event_people"):
        pid = _coerce_int(row["person_id"])
        if pid is not None:
            out[pid].add(int(row["event_id"]))
    return out


def _settlement_labels(conn: sqlite3.Connection) -> dict[int, str]:
    if not _table_exists(conn, "simulation_settlement_lookup"):
        return {}
    rows = conn.execute(
        """
        SELECT sl.settlement_key, sl.settlement_id,
               COALESCE(NULLIF(s.display_name, ''), sl.settlement_id) AS label
        FROM simulation_settlement_lookup sl
        LEFT JOIN simulation_settlements s ON s.settlement_key = sl.settlement_key
        """
    ).fetchall()
    return {int(row["settlement_key"]): str(row["label"] or "") for row in rows}


def _evidence_rows_for_accumulator(acc: _MetricAccumulator) -> list[tuple[object, ...]]:
    ranked = sorted(
        acc.evidence,
        key=lambda ev: (
            -abs(_coerce_float(ev.get("contribution_value")) or 0.0),
            -(_coerce_int(ev.get("source_year")) or -10_000_000),
            str(ev.get("summary") or ""),
        ),
    )[:EVIDENCE_LIMIT_PER_METRIC]
    rows = []
    for rank, ev in enumerate(ranked, start=1):
        rows.append(
            (
                acc.source_kind,
                acc.person_id,
                acc.metric_key,
                rank,
                str(ev.get("source_table") or ev.get("source") or ""),
                _coerce_int(ev.get("source_id") or ev.get("event_id")),
                _coerce_int(ev.get("source_year") or ev.get("year")),
                str(ev.get("role") or ""),
                _coerce_float(ev.get("contribution_value")) or 0.0,
                str(ev.get("summary") or ev.get("detail") or ""),
                _coerce_int(ev.get("region_key")),
                _coerce_int(ev.get("settlement_key")),
                json.dumps(
                    [
                        int(pid)
                        for pid in (_json_list(ev.get("related_people")) or [])
                        if _coerce_int(pid) is not None
                    ],
                    separators=(",", ":"),
                ),
                str(ev.get("payload_path") or ""),
                json.dumps(_json_dict(ev.get("caveat")), separators=(",", ":")),
            )
        )
    return rows


def _evidence_summary(acc: _MetricAccumulator) -> str:
    if acc.metric_key in {"property_loss_caused", "property_loss_suffered"}:
        return f"{acc.metric_count} property crime event(s), total loss {acc.metric_value:.3f}"
    if acc.metric_key == "age_at_death":
        return f"died at age {acc.metric_value:.0f}"
    if acc.metric_key == "offices_held":
        return f"{acc.metric_count} recorded office holding spell(s)"
    if acc.metric_key == "job_losses":
        return f"{acc.metric_count} recorded job loss event(s)"
    if acc.metric_key == "crossroads_index" and acc.component_values:
        top = sorted(acc.component_values.items(), key=lambda item: item[1], reverse=True)[:3]
        return "Crossroads components: " + ", ".join(f"{key} {value:.1f}" for key, value in top)
    if acc.metric_key.startswith("archive_"):
        return f"Cached archive score {acc.metric_value:.2f}"
    if acc.distinct_values:
        return f"{acc.metric_count} distinct recorded value(s)"
    if acc.metric_count:
        return f"{acc.metric_count} recorded event(s)"
    return f"value {acc.metric_value:.3f}"


def _event_evidence(
    event_id: int,
    year: int | None,
    event_type: str,
    role: str,
    detail: object,
    *,
    payload_path: str,
    region_key: int | None = None,
    settlement_key: int | None = None,
    related_people: Iterable[object] = (),
    contribution_value: float = 1.0,
    caveat: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "source_table": "simulation_events",
        "source_id": int(event_id),
        "source_year": year,
        "event_type": event_type,
        "role": role,
        "summary": str(detail or "").strip(),
        "payload_path": payload_path,
        "region_key": region_key,
        "settlement_key": settlement_key,
        "related_people": [pid for pid in related_people if _coerce_int(pid) is not None],
        "contribution_value": contribution_value,
        "caveat": dict(caveat or {}),
    }


def _payload_person_ids(payload: Mapping[str, object]) -> list[int]:
    out: set[int] = set()
    for key, value in payload.items():
        if key.endswith("_person_id") or key.endswith("_id"):
            pid = _coerce_int(value)
            if pid is not None:
                out.add(pid)
        elif key.endswith("_person_ids") or key.endswith("_ids"):
            for item in _json_list(value):
                pid = _coerce_int(item)
                if pid is not None:
                    out.add(pid)
    return sorted(out)


def _max_interval_overlap(intervals: Iterable[tuple[int, int, str, int]]) -> int:
    events: list[tuple[int, int]] = []
    for start, end, _kind, _other in intervals:
        start_i = int(start)
        end_i = int(end if end is not None else start)
        if end_i < start_i:
            end_i = start_i
        events.append((start_i, 1))
        events.append((end_i + 1, -1))
    current = 0
    best = 0
    for _year, delta in sorted(events):
        current += delta
        best = max(best, current)
    return best


def _rank_accumulators(rows: Iterable[_MetricAccumulator]) -> list[_MetricAccumulator]:
    return sorted(rows, key=lambda acc: (-float(acc.metric_value), -int(acc.metric_count), acc.person_id))


def _metric_midpoint_year(acc: _MetricAccumulator) -> int | None:
    if acc.first_year is not None and acc.last_year is not None:
        return int((acc.first_year + acc.last_year) / 2)
    if acc.first_year is not None:
        return int(acc.first_year)
    if acc.last_year is not None:
        return int(acc.last_year)
    return None


def _era_bucket(year: int | None) -> int | None:
    if year is None:
        return None
    return math.floor(int(year) / 100) * 100


def _rank_order_clause(rank_mode: str) -> str:
    mode = str(rank_mode or "Raw Value").strip().lower()
    if mode.startswith("world"):
        return "percentile DESC, metric_value DESC, metric_count DESC, person_id ASC"
    if mode.startswith("era"):
        return "z_score DESC, percentile DESC, metric_value DESC, person_id ASC"
    if mode.startswith("regional"):
        return "CASE WHEN region_rank IS NULL THEN 1 ELSE 0 END, region_rank ASC, metric_value DESC, person_id ASC"
    return "metric_value DESC, metric_count DESC, person_id ASC"


def _duel_person_payload(person_id: int, person: Mapping[str, object] | None) -> dict[str, object]:
    display = _person_display_fields(person, source_kind="detailed", current_year=None)
    return {"person_id": int(person_id), "name": display.get("name") or f"Person #{person_id}"}


def _duel_reason(metric: object, value_a: float, value_b: float) -> str:
    metric_label = str(metric)
    if value_a > value_b:
        return f"A is higher for {metric_label}."
    if value_b > value_a:
        return f"B is higher for {metric_label}."
    return f"{metric_label} is tied."


def _clean_job(value: object) -> str:
    label = " ".join(str(value or "").replace("_", " ").split())
    if not label or label.lower() in {"none", "unemployed", "dependent", "child"}:
        return ""
    return label


def _json_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_dicts(
    conn: sqlite3.Connection, sql: str, params: Iterable[object] = ()
) -> list[dict[str, object]]:
    cur = conn.execute(sql, tuple(params))
    columns = [col[0] for col in cur.description or ()]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _max_event_id(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_events"):
        return 0
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM simulation_events").fetchone()
    return int(row[0] or 0)


def _current_year(conn: sqlite3.Connection) -> int | None:
    if not _table_exists(conn, "world_state"):
        return None
    columns = set(_table_columns(conn, "world_state"))
    if "current_year" not in columns:
        return None
    try:
        if "id" in columns:
            row = conn.execute(
                "SELECT current_year FROM world_state WHERE id = 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT current_year FROM world_state ORDER BY rowid LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return _coerce_int(row["current_year"])
    except (KeyError, TypeError, IndexError):
        return _coerce_int(row[0])


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view') AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    if not _table_exists(conn, table):
        return ()
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: Mapping[str, str]
) -> None:
    existing = set(_table_columns(conn, table))
    for name, sql_type in columns.items():
        if name in existing:
            continue
        conn.execute(
            f"ALTER TABLE {_quote_identifier(table)} ADD COLUMN {_quote_identifier(name)} {sql_type}"
        )


def _metric_definitions(
    conn: sqlite3.Connection | None = None, *, enabled_only: bool = True
) -> list[dict[str, object]]:
    if conn is not None and _table_exists(conn, ALMANACK_DEFINITION_TABLE):
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT metric_key, display_name, category, direction,
                   person_sources_supported, requires_detailed, value_type,
                   normalization_mode, evidence_builder, description, ethical_note,
                   enabled
            FROM {_quote_identifier(ALMANACK_DEFINITION_TABLE)}
            {where}
            ORDER BY category, display_name, metric_key
            """,
        )
        if rows:
            return rows
    defs = [_definition_row(metric) for metric in _METRIC_DEFINITIONS]
    if enabled_only:
        defs = [metric for metric in defs if int(metric.get("enabled", 1)) != 0]
    return sorted(defs, key=lambda m: (str(m["category"]), str(m["display_name"])))


def _definition_row(metric: Mapping[str, object]) -> dict[str, object]:
    return {
        "metric_key": str(metric["key"]),
        "display_name": str(metric["label"]),
        "category": str(metric["category"]),
        "direction": str(metric.get("direction") or "desc"),
        "person_sources_supported": str(metric.get("sources") or "detailed"),
        "requires_detailed": int(metric.get("requires_detailed", 1)),
        "value_type": str(metric.get("value_type") or "count"),
        "normalization_mode": str(metric.get("normalization_mode") or "ranked"),
        "evidence_builder": str(metric.get("evidence_builder") or ""),
        "description": str(metric.get("description") or ""),
        "ethical_note": str(metric.get("ethical_note") or _COMMON_NOTE),
        "enabled": int(metric.get("enabled", 1)),
    }


def _upsert_metric_definitions(conn: sqlite3.Connection) -> None:
    rows = [_definition_row(metric) for metric in _METRIC_DEFINITIONS]
    conn.executemany(
        f"""
        INSERT INTO {_quote_identifier(ALMANACK_DEFINITION_TABLE)} (
            metric_key, display_name, category, direction,
            person_sources_supported, requires_detailed, value_type,
            normalization_mode, evidence_builder, description, ethical_note,
            enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_key) DO UPDATE SET
            display_name = excluded.display_name,
            category = excluded.category,
            direction = excluded.direction,
            person_sources_supported = excluded.person_sources_supported,
            requires_detailed = excluded.requires_detailed,
            value_type = excluded.value_type,
            normalization_mode = excluded.normalization_mode,
            evidence_builder = excluded.evidence_builder,
            description = excluded.description,
            ethical_note = excluded.ethical_note,
            enabled = excluded.enabled
        """,
        [
            (
                row["metric_key"],
                row["display_name"],
                row["category"],
                row["direction"],
                row["person_sources_supported"],
                row["requires_detailed"],
                row["value_type"],
                row["normalization_mode"],
                row["evidence_builder"],
                row["description"],
                row["ethical_note"],
                row["enabled"],
            )
            for row in rows
        ],
    )


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
