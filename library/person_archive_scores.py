"""Cached Narrative Heat and Archive Recognition Index scores.

The score table is a derived read model for person lookup surfaces.  It is
cheap to query by person id or top-N sort key; refreshes should run from save
checkpoint/maintenance paths rather than browser reads.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from library.world_save import event_payload_from_row


SCORE_FORMULA_VERSION = 3

SCORE_TABLE = "simulation_person_archive_scores"
REASON_TABLE = "simulation_person_archive_score_reasons"
MAX_REASONS_PER_COMPONENT = 6
MAX_EXPLANATION_REASONS = 12

_NARRATIVE_COLUMNS: tuple[str, ...] = (
    "narrative_heat_events",
    "narrative_heat_contradictions",
    "narrative_heat_consequences",
    "narrative_heat_social",
    "narrative_heat_rarity",
    "narrative_heat_volatility",
    "narrative_heat_legacy",
)

_ARI_COLUMNS: tuple[str, ...] = (
    "ari_official_status",
    "ari_wealth",
    "ari_family_prestige",
    "ari_public_role",
    "ari_legal_records",
    "ari_knowledge_art",
    "ari_founder_institution",
    "ari_descendant_memory",
    "ari_chronicler_interest",
    "ari_low_status_visibility",
    "ari_suppression_obscurity_penalty",
)

_SCORE_COLUMNS: tuple[str, ...] = (
    "person_id",
    "score_version",
    "updated_year",
    "source_event_max_id",
    "narrative_heat_total",
    *_NARRATIVE_COLUMNS,
    "archive_recognition_index",
    *_ARI_COLUMNS,
    "hidden_heat",
    "violet_marginalia_score",
    "violet_marginalia",
    "recognition_bucket",
    "narrative_bucket",
    "recognition_scope",
    "infamy_gap",
    "prestige_gap",
    "texture_flags_json",
    "score_breakdown_json",
    "component_json",
    "updated_at",
)

_REASON_COLUMNS: tuple[str, ...] = (
    "person_id",
    "component_key",
    "axis",
    "contribution",
    "source_kind",
    "source_id",
    "source_year",
    "role",
    "label",
    "explanation",
    "sort_rank",
    "score_version",
)

_COMPONENT_META: dict[str, tuple[str, str, str]] = {
    "narrative_heat_events": (
        "Events",
        "narrative",
        "Direct event participation, weighted by role and historical importance.",
    ),
    "narrative_heat_contradictions": (
        "Contradictions",
        "narrative",
        "Tension, reversals, and conflicting public/private traces.",
    ),
    "narrative_heat_consequences": (
        "Consequences",
        "narrative",
        "Obligations, reputation marks, legal fallout, and other durable effects.",
    ),
    "narrative_heat_social": (
        "Social",
        "narrative",
        "Relationship, household, office, and social-network entanglement.",
    ),
    "narrative_heat_rarity": (
        "Rarity",
        "narrative",
        "Unusual traits, roles, circumstances, or low-frequency combinations.",
    ),
    "narrative_heat_volatility": (
        "Volatility",
        "narrative",
        "Instability, risk, and fast-changing life context.",
    ),
    "narrative_heat_legacy": (
        "Legacy",
        "narrative",
        "Signals that the life may echo through descendants, institutions, or ideas.",
    ),
    "ari_official_status": (
        "Official Status",
        "ari",
        "Formal offices, status language, and administrative roles.",
    ),
    "ari_wealth": (
        "Wealth",
        "ari",
        "Prosperity, household resources, and premium economic roles.",
    ),
    "ari_family_prestige": (
        "Family Prestige",
        "ari",
        "Dynastic ties and parentage that make a person easier to identify.",
    ),
    "ari_public_role": (
        "Public Role",
        "ari",
        "Public event participation and record visibility.",
    ),
    "ari_legal_records": (
        "Legal Records",
        "ari",
        "Legal fallout and reputation ledgers that preserve names.",
    ),
    "ari_knowledge_art": (
        "Knowledge Or Art",
        "ari",
        "Creative, scholarly, patronage, and innovation traces.",
    ),
    "ari_founder_institution": (
        "Founder Or Institution",
        "ari",
        "Founder, institution, patronage, and dynasty traces.",
    ),
    "ari_descendant_memory": (
        "Descendant Memory",
        "ari",
        "Children and living descendants who can carry family memory.",
    ),
    "ari_chronicler_interest": (
        "Chronicler Interest",
        "ari",
        "Public records, total records, and story-shaped lives that attract later notice.",
    ),
    "ari_low_status_visibility": (
        "Low-Status Visibility",
        "ari",
        "Infamy, custody, outlawry, and legal notoriety that preserve non-prestigious names.",
    ),
    "ari_suppression_obscurity_penalty": (
        "Suppression Or Obscurity",
        "obscurity",
        "Sparse public records, low status, stigma, or missing institutional traces.",
    ),
    "hidden_heat": (
        "Hidden Heat",
        "derived",
        "Narrative Heat that exceeds current archive recognition.",
    ),
    "violet_marginalia_score": (
        "Violet Marginalia",
        "derived",
        "Unusually human archive texture that may deserve later annotation.",
    ),
}

_MAJOR_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "murder",
        "property_crime",
        "affair_scandal",
        "public_virtue",
        "knowledge_culture",
        "office_selection",
        "office_succession",
        "polity_promoted",
        "polity_split_vassal",
        "polity_named",
        "polity_dissolved",
        "campaign_started",
        "campaign_ended",
        "battle_fought",
        "dynastic_marriage_alliance",
        "settlement_commercial_outpost_founded",
        "settlement_moved",
    }
)

_PUBLIC_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "death",
        "founder_created",
        "settlement_commercial_outpost_founded",
        "polity_promoted",
        "polity_split_vassal",
        "polity_named",
        "polity_dissolved",
        "office_selection",
        "office_succession",
        "campaign_started",
        "campaign_ended",
        "battle_fought",
        "dynastic_marriage_alliance",
        "public_virtue",
        "knowledge_culture",
    }
)

_OFFICIAL_JOB_TERMS: frozenset[str] = frozenset(
    {
        "administrator",
        "advocate",
        "ambassador",
        "archivist",
        "bailiff",
        "chancellor",
        "clerk",
        "council",
        "court",
        "diplomat",
        "emissary",
        "governor",
        "judge",
        "jurist",
        "magistrate",
        "minister",
        "notary",
        "official",
        "reeve",
        "scribe",
        "seneschal",
        "steward",
        "treasurer",
    }
)

_KNOWLEDGE_JOB_TERMS: frozenset[str] = frozenset(
    {
        "artist",
        "bard",
        "chronicler",
        "composer",
        "craft",
        "doctor",
        "engineer",
        "inventor",
        "law",
        "poet",
        "scholar",
        "scribe",
        "teacher",
        "writer",
    }
)

_CRIMINAL_JOB_TERMS: frozenset[str] = frozenset(
    {
        "bandit",
        "charlatan",
        "criminal",
        "fraud",
        "outlaw",
        "raider",
        "robber",
        "smuggler",
        "thief",
    }
)

_HIGH_STATUS_WORDS: frozenset[str] = frozenset(
    {
        "elite",
        "high",
        "leader",
        "middle-high",
        "noble",
        "prestige",
        "ruler",
        "upper",
    }
)

RECOGNITION_SCOPES: tuple[str, ...] = (
    "none",
    "household",
    "local_social",
    "local_legal",
    "institutional",
    "regional",
    "chronicle",
    "legendary",
)

_SOCIAL_RELATIONSHIP_EVENTS: frozenset[str] = frozenset(
    {
        "couple_formed",
        "couple_dissolved",
        "same_sex_couple_formed",
        "paramour_formed",
        "paramour_ended",
    }
)

_OUTLAW_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "outlaw_case_opened",
        "outlaw_flight",
        "outlaw_refuge_joined",
        "outlaw_pursuit",
        "outlaw_captured",
        "outlaw_custody_started",
        "outlaw_custody_released",
        "outlaw_returned",
        "outlaw_raid",
        "outlaw_killed",
    }
)


@dataclass(frozen=True)
class EventFact:
    event_id: int
    sim_year: int | None
    event_type: str
    role: str
    payload: dict[str, object]


@dataclass
class ArchiveFacts:
    child_count: int = 0
    living_child_count: int = 0
    current_partner_count: int = 0
    current_paramour_count: int = 0
    office_holding_count: int = 0
    current_office_count: int = 0
    dynasty_founder_count: int = 0
    obligation_count: int = 0
    obligation_active_count: int = 0
    reputation_count: int = 0
    reputation_positive_count: int = 0
    reputation_negative_count: int = 0
    legal_fallout_count: int = 0
    faction_memory_count: int = 0
    institution_founder_count: int = 0
    institution_patron_count: int = 0
    innovation_discoverer_count: int = 0
    innovation_patron_count: int = 0
    innovation_names: list[str] = field(default_factory=list)
    public_record_count: int = 0
    total_record_count: int = 0
    move_count: int = 0
    cross_region_move_count: int = 0
    outlaw_case_count: int = 0
    outlaw_murder_case_count: int = 0
    outlaw_custody_count: int = 0
    outlaw_refuge_count: int = 0
    outlaw_victim_count: int = 0
    outlaw_knownness_max: float = 0.0
    outlaw_pursuit_max: float = 0.0
    event_count: int = 0
    linked_person_count: int = 0
    event_types: Counter[str] = field(default_factory=Counter)
    roles: Counter[str] = field(default_factory=Counter)


@dataclass
class ScoreReason:
    person_id: int
    component_key: str
    axis: str
    contribution: float
    source_kind: str = ""
    source_id: int | None = None
    source_year: int | None = None
    role: str = ""
    label: str = ""
    explanation: str = ""
    sort_rank: int = 0
    score_version: int = SCORE_FORMULA_VERSION

    def as_insert_tuple(self) -> tuple[object, ...]:
        return (
            int(self.person_id),
            self.component_key,
            self.axis,
            round(float(self.contribution), 4),
            self.source_kind,
            self.source_id,
            self.source_year,
            self.role,
            self.label,
            self.explanation,
            int(self.sort_rank),
            int(self.score_version),
        )


class ScoreAccumulator:
    """Collect explainable score reasons beside the numeric formula."""

    def __init__(self, person_id: int):
        self.person_id = int(person_id)
        self.reasons: list[ScoreReason] = []

    def add(
        self,
        component_key: str,
        contribution: float,
        *,
        label: str,
        explanation: str,
        axis: str | None = None,
        source_kind: str = "formula",
        source_id: int | None = None,
        source_year: int | None = None,
        role: str = "",
    ) -> None:
        if abs(float(contribution)) < 0.0001:
            return
        self.reasons.append(
            ScoreReason(
                person_id=self.person_id,
                component_key=component_key,
                axis=axis or _component_axis(component_key),
                contribution=float(contribution),
                source_kind=source_kind,
                source_id=source_id,
                source_year=source_year,
                role=role,
                label=_clean_text(label),
                explanation=_clean_text(explanation),
            )
        )


def ensure_person_archive_score_schema(conn: sqlite3.Connection) -> None:
    """Create the cached person archive score table and retrieval indexes."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_person_archive_scores (
            person_id INTEGER PRIMARY KEY,
            score_version INTEGER NOT NULL DEFAULT 3,
            updated_year INTEGER,
            source_event_max_id INTEGER NOT NULL DEFAULT 0,
            narrative_heat_total REAL NOT NULL DEFAULT 0.0,
            narrative_heat_events REAL NOT NULL DEFAULT 0.0,
            narrative_heat_contradictions REAL NOT NULL DEFAULT 0.0,
            narrative_heat_consequences REAL NOT NULL DEFAULT 0.0,
            narrative_heat_social REAL NOT NULL DEFAULT 0.0,
            narrative_heat_rarity REAL NOT NULL DEFAULT 0.0,
            narrative_heat_volatility REAL NOT NULL DEFAULT 0.0,
            narrative_heat_legacy REAL NOT NULL DEFAULT 0.0,
            archive_recognition_index REAL NOT NULL DEFAULT 0.0,
            ari_official_status REAL NOT NULL DEFAULT 0.0,
            ari_wealth REAL NOT NULL DEFAULT 0.0,
            ari_family_prestige REAL NOT NULL DEFAULT 0.0,
            ari_public_role REAL NOT NULL DEFAULT 0.0,
            ari_legal_records REAL NOT NULL DEFAULT 0.0,
            ari_knowledge_art REAL NOT NULL DEFAULT 0.0,
            ari_founder_institution REAL NOT NULL DEFAULT 0.0,
            ari_descendant_memory REAL NOT NULL DEFAULT 0.0,
            ari_chronicler_interest REAL NOT NULL DEFAULT 0.0,
            ari_low_status_visibility REAL NOT NULL DEFAULT 0.0,
            ari_suppression_obscurity_penalty REAL NOT NULL DEFAULT 0.0,
            hidden_heat REAL NOT NULL DEFAULT 0.0,
            violet_marginalia_score REAL NOT NULL DEFAULT 0.0,
            violet_marginalia INTEGER NOT NULL DEFAULT 0,
            recognition_bucket TEXT NOT NULL DEFAULT '',
            narrative_bucket TEXT NOT NULL DEFAULT '',
            recognition_scope TEXT NOT NULL DEFAULT 'none',
            infamy_gap REAL NOT NULL DEFAULT 0.0,
            prestige_gap REAL NOT NULL DEFAULT 0.0,
            texture_flags_json TEXT NOT NULL DEFAULT '[]',
            score_breakdown_json TEXT NOT NULL DEFAULT '{}',
            component_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_person_archive_scores_heat
        ON simulation_person_archive_scores (narrative_heat_total DESC, person_id);
        CREATE INDEX IF NOT EXISTS idx_person_archive_scores_ari
        ON simulation_person_archive_scores (archive_recognition_index DESC, person_id);
        CREATE INDEX IF NOT EXISTS idx_person_archive_scores_hidden
        ON simulation_person_archive_scores (hidden_heat DESC, person_id);
        CREATE INDEX IF NOT EXISTS idx_person_archive_scores_violet
        ON simulation_person_archive_scores (
            violet_marginalia DESC,
            violet_marginalia_score DESC,
            person_id
        );
        CREATE TABLE IF NOT EXISTS simulation_person_archive_score_reasons (
            person_id INTEGER NOT NULL,
            component_key TEXT NOT NULL,
            axis TEXT NOT NULL,
            contribution REAL NOT NULL DEFAULT 0.0,
            source_kind TEXT NOT NULL DEFAULT '',
            source_id INTEGER,
            source_year INTEGER,
            role TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '',
            sort_rank INTEGER NOT NULL DEFAULT 0,
            score_version INTEGER NOT NULL DEFAULT 2,
            PRIMARY KEY (person_id, component_key, sort_rank)
        );
        CREATE INDEX IF NOT EXISTS idx_person_archive_score_reasons_person
        ON simulation_person_archive_score_reasons (
            person_id,
            axis,
            sort_rank
        );
        CREATE INDEX IF NOT EXISTS idx_person_archive_score_reasons_component
        ON simulation_person_archive_score_reasons (
            component_key,
            contribution DESC,
            person_id
        );
        """
    )

    score_column_defaults = {
        "ari_low_status_visibility": "REAL NOT NULL DEFAULT 0.0",
        "recognition_scope": "TEXT NOT NULL DEFAULT 'none'",
        "infamy_gap": "REAL NOT NULL DEFAULT 0.0",
        "prestige_gap": "REAL NOT NULL DEFAULT 0.0",
        "texture_flags_json": "TEXT NOT NULL DEFAULT '[]'",
        "score_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    columns = set(_table_columns(conn, SCORE_TABLE))
    for column, definition in score_column_defaults.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE {SCORE_TABLE} ADD COLUMN {_quote_identifier(column)} {definition}"
            )
    columns = set(_table_columns(conn, SCORE_TABLE))
    missing = [col for col in _SCORE_COLUMNS if col not in columns]
    if missing:
        raise RuntimeError(
            "simulation_person_archive_scores is missing expected columns: "
            + ", ".join(missing)
        )
    reason_columns = set(_table_columns(conn, REASON_TABLE))
    reason_missing = [col for col in _REASON_COLUMNS if col not in reason_columns]
    if reason_missing:
        raise RuntimeError(
            "simulation_person_archive_score_reasons is missing expected columns: "
            + ", ".join(reason_missing)
        )


def refresh_person_archive_scores(
    conn: sqlite3.Connection,
    *,
    person_ids: Iterable[int] | None = None,
    simulation_year: int | None = None,
    updated_at: str | None = None,
) -> int:
    """Refresh cached scores for detailed people.

    Passing ``person_ids`` keeps checkpoint refreshes bounded to the current RAM
    working set.  Passing ``None`` performs a full maintenance refresh for every
    row in ``simulation_people``.
    """

    ensure_person_archive_score_schema(conn)
    if not _table_exists(conn, "simulation_people"):
        return 0

    requested_ids = _normalize_person_ids(person_ids)
    people_rows = _load_people(conn, requested_ids)
    if requested_ids is not None:
        missing_ids = set(requested_ids) - {int(r["person_id"]) for r in people_rows}
        _delete_scores_for_ids(conn, missing_ids)
    else:
        conn.execute(
            """
            DELETE FROM simulation_person_archive_scores
            WHERE person_id NOT IN (SELECT person_id FROM simulation_people)
            """
        )
        conn.execute(
            """
            DELETE FROM simulation_person_archive_score_reasons
            WHERE person_id NOT IN (SELECT person_id FROM simulation_people)
            """
        )

    if not people_rows:
        return 0

    person_id_set = frozenset(int(r["person_id"]) for r in people_rows)
    _delete_reason_rows_for_ids(conn, set(person_id_set))
    facts = _build_archive_facts(conn, person_id_set)
    events_by_person = _load_events_by_person(conn, person_id_set)
    max_event_id = _max_event_id(conn)
    now = updated_at or datetime.now(timezone.utc).isoformat()

    rows = []
    reason_rows: list[tuple[object, ...]] = []
    for row in people_rows:
        person_id = int(row["person_id"])
        person = _person_from_checkpoint_row(row)
        score, reasons = _score_person(
            person_id=person_id,
            row=row,
            person=person,
            facts=facts[person_id],
            events=events_by_person.get(person_id, ()),
            simulation_year=simulation_year,
            source_event_max_id=max_event_id,
            updated_at=now,
        )
        rows.append(tuple(score[col] for col in _SCORE_COLUMNS))
        reason_rows.extend(reason.as_insert_tuple() for reason in reasons)

    placeholders = ", ".join("?" for _ in _SCORE_COLUMNS)
    columns_sql = ", ".join(_quote_identifier(col) for col in _SCORE_COLUMNS)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO simulation_person_archive_scores ({columns_sql})
        VALUES ({placeholders})
        """,
        rows,
    )
    if reason_rows:
        reason_placeholders = ", ".join("?" for _ in _REASON_COLUMNS)
        reason_columns_sql = ", ".join(_quote_identifier(col) for col in _REASON_COLUMNS)
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO simulation_person_archive_score_reasons ({reason_columns_sql})
            VALUES ({reason_placeholders})
            """,
            reason_rows,
        )
    return len(rows)


def refresh_person_archive_scores_for_file(
    save_db_path: str,
    *,
    person_ids: Iterable[int] | None = None,
    simulation_year: int | None = None,
) -> int:
    """Open ``save.sqlite`` and refresh cached person archive scores."""

    with sqlite3.connect(save_db_path) as conn:
        count = refresh_person_archive_scores(
            conn, person_ids=person_ids, simulation_year=simulation_year
        )
        conn.commit()
        return count


def load_person_archive_score(
    conn: sqlite3.Connection, person_id: int
) -> dict[str, object] | None:
    """Load one cached score row by primary key."""

    if not _table_exists(conn, SCORE_TABLE):
        return None
    row = _fetch_one_dict(
        conn,
        """
        SELECT *
        FROM simulation_person_archive_scores
        WHERE person_id = ?
        """,
        (int(person_id),),
    )
    return row


def top_person_archive_scores(
    conn: sqlite3.Connection,
    *,
    order_by: str = "narrative_heat_total",
    limit: int = 25,
) -> list[dict[str, object]]:
    """Return top cached score rows using one of the indexed sort columns."""

    allowed = {
        "narrative_heat_total",
        "archive_recognition_index",
        "hidden_heat",
        "violet_marginalia_score",
    }
    sort_col = order_by if order_by in allowed else "narrative_heat_total"
    lim = max(1, min(500, int(limit)))
    if not _table_exists(conn, SCORE_TABLE):
        return []
    return _fetch_dicts(
        conn,
        f"""
        SELECT *
        FROM simulation_person_archive_scores
        ORDER BY {_quote_identifier(sort_col)} DESC, person_id ASC
        LIMIT ?
        """,
        (lim,),
    )


def load_person_archive_explanation(
    conn: sqlite3.Connection, person_id: int, max_reasons: int = MAX_EXPLANATION_REASONS
) -> dict[str, object] | None:
    """Load one cached score with structured reasons for tools and LLM context."""

    score = load_person_archive_score(conn, person_id)
    if score is None:
        return None
    component_payload = _json_dict(score.get("component_json"))
    reasons = _load_reason_dicts(conn, int(person_id), max_reasons=max_reasons)
    if not reasons:
        fallback = component_payload.get("top_reason_summaries") or component_payload.get("top_reasons") or []
        if isinstance(fallback, list):
            reasons = [dict(item) for item in fallback if isinstance(item, dict)][
                : max(0, int(max_reasons))
            ]
    summary = component_payload.get("summary") or _fallback_score_summary(
        score.get("recognition_bucket"),
        score.get("narrative_heat_total"),
        score.get("archive_recognition_index"),
    )
    return {
        "person_id": int(person_id),
        "score_version": int(score.get("score_version") or SCORE_FORMULA_VERSION),
        "summary": _strip_summary_bucket(summary, score.get("recognition_bucket")),
        "scores": {
            "narrative_heat_total": _coerce_float(score.get("narrative_heat_total")) or 0.0,
            "archive_recognition_index": _coerce_float(score.get("archive_recognition_index")) or 0.0,
            "hidden_heat": _coerce_float(score.get("hidden_heat")) or 0.0,
            "violet_marginalia_score": _coerce_float(score.get("violet_marginalia_score")) or 0.0,
            "violet_marginalia": bool(int(score.get("violet_marginalia") or 0)),
            "infamy_gap": _coerce_float(score.get("infamy_gap")) or 0.0,
            "prestige_gap": _coerce_float(score.get("prestige_gap")) or 0.0,
        },
        "buckets": {
            "archive_quadrant": _clean_text(score.get("recognition_bucket")),
            "narrative": _clean_text(score.get("narrative_bucket")),
            "recognition_scope": _clean_text(score.get("recognition_scope")),
        },
        "components": component_payload.get("components") or _components_from_score_row(score),
        "texture_flags": component_payload.get("texture_flags") or _json_list(score.get("texture_flags_json")),
        "score_breakdown": component_payload.get("score_breakdown") or _json_dict(score.get("score_breakdown_json")),
        "top_event_types": component_payload.get("top_event_types") or [],
        "top_roles": component_payload.get("top_roles") or [],
        "evidence_counts": component_payload.get("evidence_counts") or {},
        "data_caveats": component_payload.get("data_caveats") or [],
        "top_reasons": reasons,
        "source_ids": component_payload.get("source_ids") or {},
    }


def _score_person(
    *,
    person_id: int,
    row: dict[str, object],
    person: dict[str, object],
    facts: ArchiveFacts,
    events: Iterable[EventFact],
    simulation_year: int | None,
    source_event_max_id: int,
    updated_at: str,
) -> tuple[dict[str, object], list[ScoreReason]]:
    events = tuple(events)
    unique_event_type_counts: Counter[str] = Counter()
    seen_event_ids: set[int] = set()
    for event in events:
        if event.event_id in seen_event_ids:
            continue
        seen_event_ids.add(event.event_id)
        unique_event_type_counts[event.event_type] += 1

    birthyear = _coerce_int(person.get("birthyear"))
    deathyear = _coerce_int(person.get("deathyear"))
    end_year = deathyear if deathyear is not None else simulation_year
    age = (int(end_year) - int(birthyear)) if birthyear is not None and end_year is not None else None
    is_founder = _coerce_int(row.get("is_founder")) == 1
    job = _clean_text(person.get("job") or row.get("job"))
    last_job = _clean_text(person.get("last_job") or row.get("last_job"))
    status = _clean_text(person.get("status_tendency") or row.get("status_tendency")).lower()
    job_tier = _clean_text(person.get("job_tier") or row.get("job_tier")).lower()
    employment_status = _clean_text(
        person.get("employment_status") or row.get("employment_status")
    ).lower()
    job_prosperity = _coerce_float(
        person.get("job_prosperity_01") or row.get("job_prosperity_01")
    )
    household_prosperity = _coerce_float(
        person.get("household_prosperity") or row.get("household_prosperity")
    )
    tags = _person_tags(person)
    genome = _person_genome(person)
    acc = ScoreAccumulator(person_id)

    event_heat = 0.0
    rarity_heat = 0.0
    volatility_heat = 0.0
    criminal_role = _job_has_any(job, _CRIMINAL_JOB_TERMS)
    official_role = _job_has_any(job, _OFFICIAL_JOB_TERMS)
    knowledge_role = _job_has_any(job, _KNOWLEDGE_JOB_TERMS)
    job_titles: set[str] = {job} if job else set()
    partner_ids: set[int] = set()
    paramour_ids: set[int] = set()
    linked_people: set[int] = set()
    caused_victims = 0
    property_crime_perpetrator_events = 0
    property_crime_target_events = 0
    affair_scandal_events = 0
    knowledge_events = 0
    public_role_events = 0
    late_major_events = 0
    unusual_death = False
    high_importance_events = 0
    event_type_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()

    for event in events:
        event_type = event.event_type
        role = event.role
        payload = event.payload
        event_type_counts[event_type] += 1
        role_counts[role] += 1
        facts.event_count += 1
        facts.event_types[event_type] += 1
        facts.roles[role] += 1
        historical_importance = _coerce_float(payload.get("historical_importance")) or 0.0
        if historical_importance >= 0.7:
            high_importance_events += 1
        role_heat = _event_heat_for_role(event_type, role)
        event_heat += role_heat
        acc.add(
            "narrative_heat_events",
            role_heat,
            label=_event_reason_label(event_type, role),
            explanation=f"{event_type.replace('_', ' ')} involvement as {role or 'related'} raised Narrative Heat.",
            source_kind="event",
            source_id=event.event_id,
            source_year=event.sim_year,
            role=role,
        )
        if historical_importance > 0.0:
            importance_heat = min(8.0, historical_importance * 10.0)
            event_heat += importance_heat
            acc.add(
                "narrative_heat_events",
                importance_heat,
                label="High historical importance",
                explanation=f"The event carried historical_importance {historical_importance:.2f}.",
                source_kind="event",
                source_id=event.event_id,
                source_year=event.sim_year,
                role=role,
            )
        if event_type in _PUBLIC_EVENT_TYPES:
            public_role_events += 1
        if event_type == "murder":
            if role in {"killer", "perpetrator"}:
                caused_victims += 1
                criminal_role = True
            if role == "victim":
                unusual_death = True
        elif event_type == "property_crime" and role in {"perpetrator", "accused"}:
            property_crime_perpetrator_events += 1
            criminal_role = True
        elif event_type == "property_crime" and role == "target":
            property_crime_target_events += 1
        elif event_type == "affair_scandal" and role in {"accused", "paramour"}:
            affair_scandal_events += 1
            rarity_heat += 3.0
            acc.add(
                "narrative_heat_rarity",
                3.0,
                label="Affair scandal trace",
                explanation="A scandal role is treated as a rare archive-visible life texture.",
                source_kind="event",
                source_id=event.event_id,
                source_year=event.sim_year,
                role=role,
            )
        elif event_type == "affair_scandal":
            affair_scandal_events += 1
        elif event_type == "knowledge_culture" and role in {"creator", "patron"}:
            knowledge_events += 1
            knowledge_role = True
        elif event_type in {"office_selection", "office_succession"} and role == "holder":
            official_role = True
        elif event_type in {"couple_formed", "same_sex_couple_formed"}:
            other = _other_person_from_payload(payload, person_id)
            if other is not None:
                partner_ids.add(other)
        elif event_type == "paramour_formed":
            other = _other_person_from_payload(payload, person_id)
            if other is not None:
                paramour_ids.add(other)
        elif event_type in {"job_assigned", "job_lost", "unemployment_started"}:
            payload_job = _clean_text(
                payload.get("job")
                or payload.get("new_job")
                or payload.get("old_job")
                or payload.get("last_job")
            )
            if payload_job:
                job_titles.add(payload_job)
        if event_type in _MAJOR_EVENT_TYPES and birthyear is not None and event.sim_year is not None:
            event_age = int(event.sim_year) - int(birthyear)
            if event_age >= 65:
                late_major_events += 1
        for key, value in payload.items():
            if key.endswith("_person_id"):
                linked = _coerce_int(value)
                if linked is not None and linked != person_id:
                    linked_people.add(linked)
            elif key.endswith("_person_ids"):
                for linked in _coerce_person_id_list(value):
                    if linked != person_id:
                        linked_people.add(linked)

    facts.linked_person_count = max(facts.linked_person_count, len(linked_people))
    child_event_heat = min(15.0, facts.child_count * 1.2)
    event_heat += child_event_heat
    acc.add(
        "narrative_heat_events",
        child_event_heat,
        label="Family events",
        explanation=f"{facts.child_count} recorded child link(s) add ordinary but biography-shaping life material.",
        source_kind="fact",
    )
    if facts.child_count >= 8:
        event_heat += 5.0
        acc.add(
            "narrative_heat_events",
            5.0,
            label="Large family",
            explanation="Eight or more recorded children make the life unusually visible through family history.",
            source_kind="fact",
        )
    if age is not None and age >= 90:
        event_heat += 10.0
        rarity_heat += 10.0
        acc.add(
            "narrative_heat_events",
            10.0,
            label="Extreme old age",
            explanation=f"Age {age} gives the life an unusually long observed span.",
            source_kind="person",
        )
        acc.add(
            "narrative_heat_rarity",
            10.0,
            label="Extreme old age",
            explanation=f"Age {age} is rare enough to matter as archive texture.",
            source_kind="person",
        )
    elif age is not None and age >= 75:
        rarity_heat += 5.0
        acc.add(
            "narrative_heat_rarity",
            5.0,
            label="Long life",
            explanation=f"Age {age} adds a modest rarity signal.",
            source_kind="person",
        )
    if unusual_death:
        event_heat += 15.0
        rarity_heat += 10.0
        acc.add(
            "narrative_heat_events",
            15.0,
            label="Unusual death",
            explanation="The event record marks this person as a murder victim.",
            source_kind="event",
        )
        acc.add(
            "narrative_heat_rarity",
            10.0,
            label="Unusual death",
            explanation="Violent or unusual death is treated as a rare archive trace.",
            source_kind="event",
        )
    if job_tier == "premium":
        event_heat += 6.0
        rarity_heat += 8.0
        acc.add(
            "narrative_heat_events",
            6.0,
            label="Premium role",
            explanation="A premium job tier makes ordinary events more likely to have visible story shape.",
            source_kind="person",
        )
        acc.add(
            "narrative_heat_rarity",
            8.0,
            label="Premium role",
            explanation="Premium roles are rarer and more archive-visible than common work.",
            source_kind="person",
        )
    if _status_is_high(status):
        event_heat += 5.0
        acc.add(
            "narrative_heat_events",
            5.0,
            label="High status",
            explanation="High-status language increases the chance that life events are noticed.",
            source_kind="person",
        )

    contradiction_specs = _contradiction_reason_specs(
        tags=tags,
        job=job,
        criminal_role=criminal_role,
        official_role=official_role,
        knowledge_role=knowledge_role,
        caused_victims=caused_victims,
    )
    contradiction_heat = min(30.0, sum(value for value, _, _ in contradiction_specs))
    for value, label, explanation in contradiction_specs:
        acc.add(
            "narrative_heat_contradictions",
            value,
            label=label,
            explanation=explanation,
            source_kind="person",
        )

    consequence_heat = (
        min(18.0, facts.obligation_count * 4.0)
        + min(24.0, facts.reputation_count * 8.0)
        + min(24.0, facts.legal_fallout_count * 8.0)
        + min(18.0, facts.faction_memory_count * 6.0)
        + min(36.0, caused_victims * 12.0)
        + min(20.0, knowledge_events * 10.0)
        + min(12.0, event_type_counts["household_childcare_shortfall"] * 6.0)
        + min(12.0, event_type_counts["affair_scandal"] * 4.0)
    )
    consequence_heat = min(40.0, consequence_heat)
    for value, label, explanation in (
        (
            min(18.0, facts.obligation_count * 4.0),
            "Obligation ledgers",
            f"{facts.obligation_count} obligation ledger row(s) keep consequences attached to the person.",
        ),
        (
            min(24.0, facts.reputation_count * 8.0),
            "Reputation marks",
            f"{facts.reputation_count} reputation mark(s) preserve social consequences.",
        ),
        (
            min(24.0, facts.legal_fallout_count * 8.0),
            "Legal fallout",
            f"{facts.legal_fallout_count} legal fallout row(s) preserve conflict or adjudication traces.",
        ),
        (
            min(18.0, facts.faction_memory_count * 6.0),
            "Faction memory",
            f"{facts.faction_memory_count} faction-memory row(s) preserve group-level consequences.",
        ),
        (
            min(36.0, caused_victims * 12.0),
            "Victims caused",
            f"{caused_victims} caused victim(s) make the event consequences durable.",
        ),
        (
            min(20.0, knowledge_events * 10.0),
            "Knowledge consequences",
            f"{knowledge_events} knowledge/culture event(s) add durable consequence heat.",
        ),
        (
            min(12.0, event_type_counts["household_childcare_shortfall"] * 6.0),
            "Household care crisis",
            "Childcare shortfall events create remembered household consequences.",
        ),
        (
            min(12.0, event_type_counts["affair_scandal"] * 4.0),
            "Affair scandal fallout",
            "Affair scandal events can leave durable household or legal consequences.",
        ),
    ):
        acc.add(
            "narrative_heat_consequences",
            value,
            label=label,
            explanation=explanation,
            source_kind="fact",
        )

    social_heat = (
        min(12.0, facts.child_count * 1.0)
        + min(10.0, (len(partner_ids) + facts.current_partner_count) * 2.0)
        + min(12.0, (len(paramour_ids) + facts.current_paramour_count) * 3.0)
        + min(12.0, facts.linked_person_count * 0.5)
        + min(8.0, facts.cross_region_move_count * 3.0)
        + (6.0 if facts.office_holding_count and facts.child_count else 0.0)
    )
    social_heat = min(35.0, social_heat)
    for value, label, explanation in (
        (
            min(12.0, facts.child_count * 1.0),
            "Children and descendants",
            f"{facts.child_count} child link(s) widen the visible family network.",
        ),
        (
            min(10.0, (len(partner_ids) + facts.current_partner_count) * 2.0),
            "Partners",
            "Recorded partner links make the person easier to place socially.",
        ),
        (
            min(12.0, (len(paramour_ids) + facts.current_paramour_count) * 3.0),
            "Paramours",
            "Recorded paramour links add social entanglement and archive texture.",
        ),
        (
            min(12.0, facts.linked_person_count * 0.5),
            "Linked people",
            f"{facts.linked_person_count} distinct linked person(s) appear in payload traces.",
        ),
        (
            min(8.0, facts.cross_region_move_count * 3.0),
            "Cross-region movement",
            f"{facts.cross_region_move_count} cross-region move(s) widen the social geography.",
        ),
        (
            6.0 if facts.office_holding_count and facts.child_count else 0.0,
            "Office and family overlap",
            "Office holding plus children ties public and family networks together.",
        ),
    ):
        acc.add(
            "narrative_heat_social",
            value,
            label=label,
            explanation=explanation,
            source_kind="fact",
        )

    rare_trait_extremes = sum(1 for value in genome.values() if abs(float(value)) >= 90.0)
    if rare_trait_extremes:
        rarity_heat += min(15.0, rare_trait_extremes * 3.0)
        acc.add(
            "narrative_heat_rarity",
            min(15.0, rare_trait_extremes * 3.0),
            label="Extreme genome traits",
            explanation=f"{rare_trait_extremes} genome trait(s) sit at extreme signed magnitudes.",
            source_kind="person",
        )
    rarity_heat += min(24.0, late_major_events * 12.0)
    acc.add(
        "narrative_heat_rarity",
        min(24.0, late_major_events * 12.0),
        label="Late major events",
        explanation=f"{late_major_events} major event(s) occurred after age 65.",
        source_kind="event",
    )
    if high_importance_events:
        rarity_heat += min(12.0, high_importance_events * 4.0)
        acc.add(
            "narrative_heat_rarity",
            min(12.0, high_importance_events * 4.0),
            label="High-importance events",
            explanation=f"{high_importance_events} event(s) crossed the high historical importance threshold.",
            source_kind="event",
        )
    if facts.cross_region_move_count:
        rarity_heat += min(8.0, facts.cross_region_move_count * 2.0)
        acc.add(
            "narrative_heat_rarity",
            min(8.0, facts.cross_region_move_count * 2.0),
            label="Cross-region movement",
            explanation=f"{facts.cross_region_move_count} cross-region move(s) are uncommon enough to add rarity.",
            source_kind="fact",
        )
    if facts.innovation_discoverer_count:
        rarity_heat += min(15.0, facts.innovation_discoverer_count * 8.0)
        acc.add(
            "narrative_heat_rarity",
            min(15.0, facts.innovation_discoverer_count * 8.0),
            label="Innovation discovery",
            explanation=f"{facts.innovation_discoverer_count} innovation discovery row(s) mark rare cultural agency.",
            source_kind="fact",
        )

    job_change_count = max(
        0,
        len({title.lower() for title in job_titles if title}) - 1,
    )
    partner_change_count = len(partner_ids) + len(paramour_ids)
    reputation_direction_changes = 1 if facts.reputation_positive_count and facts.reputation_negative_count else 0
    volatility_heat += min(12.0, job_change_count * 2.0)
    acc.add(
        "narrative_heat_volatility",
        min(12.0, job_change_count * 2.0),
        label="Job changes",
        explanation=f"{job_change_count} distinct job change(s) make the life less static.",
        source_kind="event",
    )
    volatility_heat += min(16.0, partner_change_count * 2.0)
    acc.add(
        "narrative_heat_volatility",
        min(16.0, partner_change_count * 2.0),
        label="Relationship changes",
        explanation=f"{partner_change_count} partner or paramour change(s) add life volatility.",
        source_kind="event",
    )
    volatility_heat += min(15.0, reputation_direction_changes * 5.0)
    acc.add(
        "narrative_heat_volatility",
        min(15.0, reputation_direction_changes * 5.0),
        label="Mixed reputation",
        explanation="Both positive and negative reputation marks exist.",
        source_kind="fact",
    )
    volatility_heat += min(12.0, facts.move_count * 2.0)
    acc.add(
        "narrative_heat_volatility",
        min(12.0, facts.move_count * 2.0),
        label="Movement",
        explanation=f"{facts.move_count} recorded move(s) change the person's context.",
        source_kind="fact",
    )
    if criminal_role and official_role:
        volatility_heat += 10.0
        acc.add(
            "narrative_heat_volatility",
            10.0,
            label="Criminal-official tension",
            explanation="Criminal and official traces coexist in one life.",
            source_kind="person",
        )
    if employment_status == "unemployed" and last_job and job:
        volatility_heat += 3.0
        acc.add(
            "narrative_heat_volatility",
            3.0,
            label="Employment reversal",
            explanation="Unemployment with a known last job and current job suggests a visible reversal.",
            source_kind="person",
        )

    legacy_heat = (
        min(16.0, facts.living_child_count * 1.0)
        + (10.0 if facts.child_count >= 4 else 0.0)
        + min(25.0, (knowledge_events + facts.innovation_discoverer_count) * 10.0)
        + min(20.0, facts.institution_founder_count * 8.0)
        + min(20.0, facts.dynasty_founder_count * 10.0)
        + min(12.0, facts.obligation_active_count * 3.0)
    )
    for value, label, explanation in (
        (
            min(16.0, facts.living_child_count * 1.0),
            "Living descendants",
            f"{facts.living_child_count} living child link(s) can carry memory forward.",
        ),
        (
            10.0 if facts.child_count >= 4 else 0.0,
            "Large descendant base",
            "Four or more children increase family-memory persistence.",
        ),
        (
            min(25.0, (knowledge_events + facts.innovation_discoverer_count) * 10.0),
            "Knowledge legacy",
            "Knowledge/culture and innovation traces can persist beyond the person.",
        ),
        (
            min(20.0, facts.institution_founder_count * 8.0),
            "Institution founder",
            f"{facts.institution_founder_count} institution founder row(s) add legacy heat.",
        ),
        (
            min(20.0, facts.dynasty_founder_count * 10.0),
            "Dynasty founder",
            f"{facts.dynasty_founder_count} dynasty founder row(s) add legacy heat.",
        ),
        (
            min(12.0, facts.obligation_active_count * 3.0),
            "Active obligations",
            f"{facts.obligation_active_count} active obligation(s) can outlive the triggering year.",
        ),
    ):
        acc.add(
            "narrative_heat_legacy",
            value,
            label=label,
            explanation=explanation,
            source_kind="fact",
        )

    relationship_event_count = sum(
        unique_event_type_counts[event_type] for event_type in _SOCIAL_RELATIONSHIP_EVENTS
    )
    outlaw_event_count = sum(unique_event_type_counts[event_type] for event_type in _OUTLAW_EVENT_TYPES)
    hardship_event_count = (
        unique_event_type_counts["begging"]
        + unique_event_type_counts["vagrancy"]
        + unique_event_type_counts["bankruptcy"]
        + unique_event_type_counts["status_fall"]
        + unique_event_type_counts["household_childcare_shortfall"]
        + unique_event_type_counts["household_prosperity_crisis"]
    )
    dead_child_count = max(0, int(facts.child_count) - int(facts.living_child_count))
    relationship_public_consequence = (
        affair_scandal_events
        + unique_event_type_counts["affair_scandal"]
        + facts.legal_fallout_count
    )
    innovation_count = facts.innovation_discoverer_count + facts.innovation_patron_count
    innovation_names = tuple(facts.innovation_names[:5])
    innovation_names_text = _human_join(innovation_names)

    raw_latent_potential = (
        rare_trait_extremes * 3.0
        + (4.0 if knowledge_role else 0.0)
        + (4.0 if any(_job_has_any(title, _KNOWLEDGE_JOB_TERMS) for title in job_titles) else 0.0)
        + (4.0 if unique_event_type_counts["career_fitness_updated"] else 0.0)
        + (3.0 if job_tier == "premium" else 0.0)
        + (3.0 if _status_is_high(status) else 0.0)
    )
    realized_support = (
        caused_victims * 5.0
        + knowledge_events * 4.0
        + facts.innovation_discoverer_count * 5.0
        + facts.legal_fallout_count * 3.0
        + facts.office_holding_count * 3.0
        + facts.outlaw_case_count * 3.0
        + relationship_public_consequence * 2.0
    )
    latent_cap = 8.0 + min(8.0, realized_support * 0.18)
    latent_potential = min(raw_latent_potential, latent_cap)

    tragic_compression = 0.0
    tragic_evidence: list[str] = []
    if deathyear is not None and age is not None and age <= 30:
        tragic_compression += 9.0
        tragic_evidence.append(f"died at {age}")
    if dead_child_count:
        tragic_compression += min(11.0, dead_child_count * 2.4)
        tragic_evidence.append(f"{dead_child_count} recorded child death(s)")
    if unique_event_type_counts["household_childcare_shortfall"]:
        tragic_compression += min(6.0, unique_event_type_counts["household_childcare_shortfall"] * 4.0)
        tragic_evidence.append("household childcare shortfall")
    if "child rearer" in {title.lower() for title in job_titles} and len(job_titles) > 1:
        tragic_compression += 4.0
        tragic_evidence.append("career compressed into childcare")
    tragic_compression = min(28.0, tragic_compression)

    realized_consequence = min(
        30.0,
        event_heat * 0.16
        + caused_victims * 4.0
        + property_crime_target_events * 1.5
        + public_role_events * 1.2
        + facts.obligation_count * 1.6
        + facts.reputation_count * 2.5
        + facts.legal_fallout_count * 2.5
        + knowledge_events * 4.0
        + facts.innovation_discoverer_count * 4.0
    )
    criminal_outlaw_consequence = min(
        38.0,
        caused_victims * 9.0
        + property_crime_perpetrator_events * 0.7
        + facts.outlaw_case_count * 3.0
        + facts.outlaw_murder_case_count * 2.0
        + facts.outlaw_refuge_count * 2.0
        + facts.outlaw_custody_count * 4.0
        + outlaw_event_count * 1.2
        + facts.outlaw_knownness_max * 4.0
        + facts.outlaw_pursuit_max * 4.0
    )
    relationship_consequence = min(
        24.0,
        relationship_public_consequence * 6.0
        + facts.legal_fallout_count * 4.0
        + min(6.0, max(0, relationship_event_count - 4) * 0.75),
    )
    public_social_consequence = min(
        18.0,
        facts.public_record_count * 0.55
        + facts.total_record_count * 0.15
        + public_role_events * 1.0
        + facts.linked_person_count * 0.25
        + facts.cross_region_move_count * 1.5
    )
    knowledge_legacy = min(
        34.0,
        knowledge_events * 6.0
        + facts.innovation_discoverer_count * 8.0
        + facts.innovation_patron_count * 3.0
        + facts.institution_founder_count * 5.0
        + facts.institution_patron_count * 3.0
        + (4.0 if knowledge_role else 0.0),
    )
    ordinary_family_trace = min(
        10.0,
        facts.child_count * 0.9
        + facts.living_child_count * 0.3
        + min(3.0, len(partner_ids) * 1.2),
    )
    raw_repeat_pattern_volume = (
        max(0, property_crime_perpetrator_events - 1) * 1.2
        + max(0, caused_victims - 1) * 3.0
        + max(0, relationship_event_count - 4) * 1.6
        + max(0, hardship_event_count - 2) * 1.2
        + max(0, job_change_count - 2) * 1.0
    )
    repeat_pattern_volume = min(8.0, _damped_score(raw_repeat_pattern_volume, repeat=1.65))

    criminal_arc_bonus = 0.0
    relationship_arc_bonus = 0.0
    achievement_arc_bonus = 0.0
    if (
        caused_victims >= 2
        and facts.outlaw_case_count
        and (facts.outlaw_refuge_count or facts.outlaw_custody_count or outlaw_event_count >= 3)
    ):
        criminal_arc_bonus = min(
            22.0,
            10.0
            + caused_victims * 2.0
            + facts.outlaw_custody_count * 2.0
            + facts.outlaw_refuge_count * 1.5
            + (4.0 if unique_event_type_counts["outlaw_killed"] or deathyear else 0.0),
        )
        acc.add(
            "narrative_heat_volatility",
            criminal_arc_bonus,
            label="Repeat outlaw/criminal arc",
            explanation=(
                "Murders, wanted/outlaw records, refuge or custody, and pursuit form an escalating criminal arc."
            ),
            source_kind="fact",
        )
    if relationship_event_count >= 6 and relationship_public_consequence:
        relationship_arc_bonus = min(
            14.0,
            8.0 + relationship_public_consequence * 3.0 + min(4.0, relationship_event_count * 0.25),
        )
        acc.add(
            "narrative_heat_volatility",
            relationship_arc_bonus,
            label="Relationship scandal arc",
            explanation=(
                "Relationship churn escalates into public scandal or legal afterlife instead of remaining private repetition."
            ),
            source_kind="fact",
        )
    if facts.innovation_discoverer_count >= 2:
        achievement_arc_bonus = min(14.0, 7.0 + facts.innovation_discoverer_count * 2.0 + hardship_event_count * 0.6)
        acc.add(
            "narrative_heat_legacy",
            achievement_arc_bonus,
            label="Public achievement arc",
            explanation=(
                f"Named innovation work{f' ({innovation_names_text})' if innovation_names_text else ''} creates durable public achievement."
            ),
            source_kind="fact",
        )

    if latent_potential:
        acc.add(
            "narrative_heat_rarity",
            latent_potential,
            label="Latent potential capped",
            explanation=(
                f"Trait, job, or career-fitness promise contributes {latent_potential:.1f}, "
                f"capped from {raw_latent_potential:.1f} until events realize it."
            ),
            source_kind="person",
        )
    if tragic_compression:
        acc.add(
            "narrative_heat_rarity",
            tragic_compression,
            label="Tragic compression",
            explanation=_sentence_from_evidence(
                tragic_evidence,
                fallback="Young death, child loss, or household pressure compresses the life into a poignant record.",
            ),
            source_kind="fact",
        )
    if criminal_outlaw_consequence:
        acc.add(
            "narrative_heat_consequences",
            criminal_outlaw_consequence,
            label="Criminal/outlaw consequence",
            explanation=(
                f"{caused_victims} caused victim(s), {facts.outlaw_case_count} outlaw case(s), "
                f"{facts.outlaw_custody_count} custody row(s), and {facts.outlaw_refuge_count} refuge trace(s) preserve infamy."
            ),
            source_kind="fact",
        )
    if relationship_consequence:
        acc.add(
            "narrative_heat_consequences",
            relationship_consequence,
            label="Relationship consequence",
            explanation=(
                f"{relationship_event_count} relationship event(s), {affair_scandal_events} affair scandal role(s), "
                f"and {facts.legal_fallout_count} legal fallout row(s) make the relationship pattern consequential."
            ),
            source_kind="fact",
        )
    if repeat_pattern_volume:
        acc.add(
            "narrative_heat_volatility",
            repeat_pattern_volume,
            label="Damped repeat pattern",
            explanation=(
                f"Repeated similar events contribute {repeat_pattern_volume:.1f}, damped from {raw_repeat_pattern_volume:.1f}."
            ),
            source_kind="formula",
        )
    if knowledge_legacy and innovation_names_text:
        acc.add(
            "narrative_heat_legacy",
            min(knowledge_legacy, 30.0),
            label="Named knowledge legacy",
            explanation=f"Named innovation trace: {innovation_names_text}.",
            source_kind="fact",
        )

    narrative_components = {
        "narrative_heat_events": min(35.0, realized_consequence + ordinary_family_trace),
        "narrative_heat_contradictions": min(30.0, contradiction_heat),
        "narrative_heat_consequences": min(
            45.0,
            max(consequence_heat * 0.35, 0.0)
            + criminal_outlaw_consequence
            + relationship_consequence,
        ),
        "narrative_heat_social": min(35.0, public_social_consequence + ordinary_family_trace),
        "narrative_heat_rarity": min(35.0, latent_potential + tragic_compression),
        "narrative_heat_volatility": min(
            35.0,
            repeat_pattern_volume
            + criminal_arc_bonus
            + relationship_arc_bonus
            + min(6.0, facts.move_count * 1.2 + job_change_count * 0.8),
        ),
        "narrative_heat_legacy": min(38.0, knowledge_legacy + achievement_arc_bonus),
    }
    channel_total = (
        realized_consequence
        + latent_potential
        + tragic_compression
        + knowledge_legacy
        + criminal_outlaw_consequence
        + relationship_consequence
        + public_social_consequence
        + repeat_pattern_volume
        + criminal_arc_bonus
        + relationship_arc_bonus
        + achievement_arc_bonus
        + ordinary_family_trace
        + min(8.0, contradiction_heat * 0.45)
    )
    narrative_total = _clamp(channel_total + _stable_jitter(person_id, 2.5), 0.0, 100.0)

    ari_components = _ari_components(
        person_id=person_id,
        row=row,
        job=job,
        status=status,
        job_tier=job_tier,
        job_prosperity=job_prosperity,
        household_prosperity=household_prosperity,
        is_founder=is_founder,
        facts=facts,
        public_role_events=public_role_events,
        knowledge_events=knowledge_events,
        narrative_total=narrative_total,
        criminal_role=criminal_role,
    )
    _add_ari_reasons(
        acc,
        ari_components=ari_components,
        facts=facts,
        job=job,
        status=status,
        job_tier=job_tier,
        job_prosperity=job_prosperity,
        household_prosperity=household_prosperity,
        public_role_events=public_role_events,
        knowledge_events=knowledge_events,
        is_founder=is_founder,
        has_parent=bool(_coerce_int(row.get("father_id")) or _coerce_int(row.get("mother_id"))),
        criminal_role=criminal_role,
    )
    ari_total = _clamp(
        sum(value for key, value in ari_components.items() if key != "ari_suppression_obscurity_penalty")
        - ari_components["ari_suppression_obscurity_penalty"],
        0.0,
        100.0,
    )
    recognition_scope = _recognition_scope(
        facts=facts,
        ari_total=ari_total,
        public_role_events=public_role_events,
        knowledge_events=knowledge_events,
        relationship_public_consequence=relationship_public_consequence,
        criminal_outlaw_consequence=criminal_outlaw_consequence,
    )
    legal_visibility_relief = ari_components.get("ari_low_status_visibility", 0.0) * 0.75
    formal_visibility_relief = (
        ari_components.get("ari_public_role", 0.0) * 0.12
        + ari_components.get("ari_knowledge_art", 0.0) * 0.18
        + ari_components.get("ari_founder_institution", 0.0) * 0.14
    )
    hidden_heat = _clamp(
        max(0.0, narrative_total - ari_total)
        + max(0.0, tragic_compression * 0.35 - ari_total * 0.03)
        - legal_visibility_relief
        - formal_visibility_relief,
        0.0,
        100.0,
    )
    if facts.legal_fallout_count:
        hidden_heat = max(0.0, hidden_heat - facts.legal_fallout_count * 6.0)
    if relationship_public_consequence:
        hidden_heat = max(0.0, hidden_heat - relationship_consequence * 0.20)
    infamy_gap = _clamp(
        criminal_outlaw_consequence
        + ari_components.get("ari_low_status_visibility", 0.0)
        - (
            ari_components.get("ari_official_status", 0.0)
            + ari_components.get("ari_wealth", 0.0)
            + ari_components.get("ari_knowledge_art", 0.0)
        )
        * 0.35,
        0.0,
        100.0,
    )
    prestige_gap = _clamp(
        (
            ari_components.get("ari_official_status", 0.0)
            + ari_components.get("ari_wealth", 0.0)
            + ari_components.get("ari_knowledge_art", 0.0)
            + ari_components.get("ari_founder_institution", 0.0)
        )
        - max(criminal_outlaw_consequence, relationship_consequence, tragic_compression) * 0.5,
        0.0,
        100.0,
    )
    texture_flags = _texture_flags(
        facts=facts,
        age=age,
        deathyear=deathyear,
        dead_child_count=dead_child_count,
        tragic_evidence=tragic_evidence,
        innovation_names=innovation_names,
        hardship_event_count=hardship_event_count,
        criminal_outlaw_consequence=criminal_outlaw_consequence,
        relationship_consequence=relationship_consequence,
        relationship_arc_bonus=relationship_arc_bonus,
        hidden_heat=hidden_heat,
    )
    texture_strength = max(
        (float(flag.get("strength") or 0.0) for flag in texture_flags),
        default=0.0,
    )
    violet_score = _clamp(
        texture_strength
        + min(0.07, hidden_heat * 0.003)
        + min(0.10, contradiction_heat * 0.003)
        - (0.04 if ari_total >= 82.0 and texture_strength < 0.5 else 0.0),
        0.0,
        1.0,
    )
    score_breakdown = _score_breakdown_payload(
        realized_consequence=realized_consequence,
        latent_potential=latent_potential,
        raw_latent_potential=raw_latent_potential,
        latent_cap=latent_cap,
        tragic_compression=tragic_compression,
        knowledge_legacy=knowledge_legacy,
        criminal_outlaw_consequence=criminal_outlaw_consequence,
        relationship_consequence=relationship_consequence,
        public_social_consequence=public_social_consequence,
        repeat_pattern_volume=repeat_pattern_volume,
        raw_repeat_pattern_volume=raw_repeat_pattern_volume,
        ordinary_family_trace=ordinary_family_trace,
        criminal_arc_bonus=criminal_arc_bonus,
        relationship_arc_bonus=relationship_arc_bonus,
        achievement_arc_bonus=achievement_arc_bonus,
        recognition_scope=recognition_scope,
    )
    acc.add(
        "hidden_heat",
        hidden_heat,
        label="Interesting but thinly preserved",
        explanation=(
            f"Hidden Heat reflects remaining narrative value after ARI {ari_total:.1f}, "
            f"low-status visibility, and formal records are considered."
        ),
        axis="derived",
        source_kind="formula",
    )
    acc.add(
        "violet_marginalia_score",
        texture_strength,
        label="Structured archive texture",
        explanation="Structured texture flags provide the main Violet Marginalia evidence.",
        axis="derived",
        source_kind="fact",
    )
    recognition_bucket = _recognition_bucket(narrative_total, ari_total)
    narrative_bucket = _narrative_bucket(narrative_total)
    ranked_reasons = _rank_reasons(acc.reasons)
    component_json = _component_json_payload(
        facts=facts,
        narrative_components=narrative_components,
        ari_components=ari_components,
        narrative_total=narrative_total,
        ari_total=ari_total,
        hidden_heat=hidden_heat,
        violet_score=violet_score,
        recognition_bucket=recognition_bucket,
        narrative_bucket=narrative_bucket,
        recognition_scope=recognition_scope,
        infamy_gap=infamy_gap,
        prestige_gap=prestige_gap,
        texture_flags=texture_flags,
        score_breakdown=score_breakdown,
        event_type_counts=event_type_counts,
        role_counts=role_counts,
        source_event_max_id=source_event_max_id,
        reasons=ranked_reasons,
        flags={
            "criminal_role": bool(criminal_role),
            "official_role": bool(official_role),
            "knowledge_role": bool(knowledge_role),
            "late_major_events": int(late_major_events),
            "unusual_death": bool(unusual_death),
            "criminal_arc": bool(criminal_arc_bonus),
            "relationship_arc": bool(relationship_arc_bonus),
        },
    )

    score: dict[str, object] = {
        "person_id": int(person_id),
        "score_version": SCORE_FORMULA_VERSION,
        "updated_year": simulation_year,
        "source_event_max_id": source_event_max_id,
        "narrative_heat_total": round(narrative_total, 4),
        "archive_recognition_index": round(ari_total, 4),
        "hidden_heat": round(hidden_heat, 4),
        "violet_marginalia_score": round(violet_score, 4),
        "violet_marginalia": 1 if violet_score >= 0.42 else 0,
        "recognition_bucket": recognition_bucket,
        "narrative_bucket": narrative_bucket,
        "recognition_scope": recognition_scope,
        "infamy_gap": round(infamy_gap, 4),
        "prestige_gap": round(prestige_gap, 4),
        "texture_flags_json": "[]",
        "score_breakdown_json": "{}",
        "component_json": json.dumps(component_json, separators=(",", ":")),
        "updated_at": updated_at,
    }
    for key in _NARRATIVE_COLUMNS:
        score[key] = round(_clamp(narrative_components[key], 0.0, 100.0), 4)
    for key in _ARI_COLUMNS:
        score[key] = round(_clamp(ari_components[key], 0.0, 100.0), 4)
    return score, ranked_reasons


def _ari_components(
    *,
    person_id: int,
    row: dict[str, object],
    job: str,
    status: str,
    job_tier: str,
    job_prosperity: float | None,
    household_prosperity: float | None,
    is_founder: bool,
    facts: ArchiveFacts,
    public_role_events: int,
    knowledge_events: int,
    narrative_total: float,
    criminal_role: bool,
) -> dict[str, float]:
    official_status = (
        min(30.0, facts.office_holding_count * 8.0 + facts.current_office_count * 6.0)
        + (8.0 if _job_has_any(job, _OFFICIAL_JOB_TERMS) else 0.0)
        + (4.0 if _status_is_high(status) else 0.0)
    )
    wealth = 0.0
    if job_prosperity is not None:
        wealth += max(0.0, min(1.0, job_prosperity)) * 12.0
    if household_prosperity is not None:
        wealth += max(0.0, min(2.0, household_prosperity)) * 5.0
    if job_tier == "premium":
        wealth += 8.0
    if _status_is_high(status):
        wealth += 5.0
    family_prestige = min(
        18.0,
        facts.dynasty_founder_count * 12.0
        + (6.0 if _coerce_int(row.get("father_id")) or _coerce_int(row.get("mother_id")) else 0.0),
    )
    public_role = min(
        18.0,
        public_role_events * 3.0
        + facts.public_record_count * 1.5
        + (6.0 if _job_has_any(job, _OFFICIAL_JOB_TERMS) else 0.0),
    )
    legal_records = min(16.0, facts.legal_fallout_count * 6.0 + facts.reputation_count * 2.0)
    knowledge_art = min(
        24.0,
        knowledge_events * 8.0
        + facts.innovation_discoverer_count * 10.0
        + facts.innovation_patron_count * 4.0
        + (6.0 if _job_has_any(job, _KNOWLEDGE_JOB_TERMS) else 0.0),
    )
    founder_institution = min(
        24.0,
        (8.0 if is_founder else 0.0)
        + facts.institution_founder_count * 8.0
        + facts.institution_patron_count * 4.0
        + facts.dynasty_founder_count * 10.0,
    )
    descendant_memory = min(16.0, facts.living_child_count * 0.8 + facts.child_count * 0.4)
    chronicler_interest = min(
        24.0,
        facts.public_record_count * 2.0
        + facts.total_record_count * 0.5
        + narrative_total * 0.25,
    )
    low_status_visibility = min(
        22.0,
        facts.outlaw_case_count * 4.0
        + facts.outlaw_murder_case_count * 3.0
        + facts.outlaw_custody_count * 5.0
        + facts.outlaw_refuge_count * 2.5
        + facts.outlaw_knownness_max * 5.0
        + facts.outlaw_pursuit_max * 5.0
        + (4.0 if criminal_role and facts.public_record_count >= 2 else 0.0),
    )
    suppression = 0.0
    if facts.public_record_count == 0 and facts.office_holding_count == 0:
        suppression += 8.0
    if job_tier != "premium" and not _status_is_high(status):
        suppression += 3.0
    if criminal_role and facts.legal_fallout_count == 0 and facts.public_record_count <= 1:
        suppression += 5.0
    return {
        "ari_official_status": official_status,
        "ari_wealth": wealth,
        "ari_family_prestige": family_prestige,
        "ari_public_role": public_role,
        "ari_legal_records": legal_records,
        "ari_knowledge_art": knowledge_art,
        "ari_founder_institution": founder_institution,
        "ari_descendant_memory": descendant_memory,
        "ari_chronicler_interest": chronicler_interest,
        "ari_low_status_visibility": low_status_visibility,
        "ari_suppression_obscurity_penalty": suppression,
    }


def _add_ari_reasons(
    acc: ScoreAccumulator,
    *,
    ari_components: dict[str, float],
    facts: ArchiveFacts,
    job: str,
    status: str,
    job_tier: str,
    job_prosperity: float | None,
    household_prosperity: float | None,
    public_role_events: int,
    knowledge_events: int,
    is_founder: bool,
    has_parent: bool,
    criminal_role: bool,
) -> None:
    acc.add(
        "ari_official_status",
        min(30.0, facts.office_holding_count * 8.0 + facts.current_office_count * 6.0),
        label="Office holding",
        explanation=f"{facts.office_holding_count} office holding row(s) and {facts.current_office_count} current office row(s) preserve formal status.",
        source_kind="fact",
    )
    acc.add(
        "ari_official_status",
        8.0 if _job_has_any(job, _OFFICIAL_JOB_TERMS) else 0.0,
        label="Official job language",
        explanation=f"The job label {job or 'unknown'} matches official/archive-administrative terms.",
        source_kind="person",
    )
    acc.add(
        "ari_official_status",
        4.0 if _status_is_high(status) else 0.0,
        label="High status language",
        explanation="High-status language makes the archive more likely to preserve the name.",
        source_kind="person",
    )

    job_wealth = (
        max(0.0, min(1.0, job_prosperity)) * 12.0
        if job_prosperity is not None
        else 0.0
    )
    household_wealth = (
        max(0.0, min(2.0, household_prosperity)) * 5.0
        if household_prosperity is not None
        else 0.0
    )
    for value, label, explanation in (
        (
            job_wealth,
            "Job prosperity",
            "Job prosperity contributes to archive recognition through wealth and status traces.",
        ),
        (
            household_wealth,
            "Household prosperity",
            "Household prosperity contributes to archive recognition through property and family traces.",
        ),
        (
            8.0 if job_tier == "premium" else 0.0,
            "Premium job tier",
            "Premium jobs are more likely to be named in records.",
        ),
        (
            5.0 if _status_is_high(status) else 0.0,
            "High status wealth trace",
            "High status contributes to the wealth/status recognition channel.",
        ),
    ):
        acc.add(
            "ari_wealth",
            value,
            label=label,
            explanation=explanation,
            source_kind="person",
        )

    acc.add(
        "ari_family_prestige",
        min(
            18.0,
            facts.dynasty_founder_count * 12.0 + (6.0 if has_parent else 0.0),
        ),
        label="Family prestige",
        explanation="Dynasty-founder rows or recorded parentage make the person easier to place in family memory.",
        source_kind="fact",
    )
    acc.add(
        "ari_public_role",
        min(
            18.0,
            public_role_events * 3.0
            + facts.public_record_count * 1.5
            + (6.0 if _job_has_any(job, _OFFICIAL_JOB_TERMS) else 0.0),
        ),
        label="Public record visibility",
        explanation=f"{public_role_events} public role event(s) and {facts.public_record_count} public record(s) preserve the person.",
        source_kind="fact",
    )
    acc.add(
        "ari_legal_records",
        min(16.0, facts.legal_fallout_count * 6.0 + facts.reputation_count * 2.0),
        label="Legal and reputation records",
        explanation=f"{facts.legal_fallout_count} legal fallout row(s) and {facts.reputation_count} reputation mark(s) preserve archive identity.",
        source_kind="fact",
    )
    acc.add(
        "ari_knowledge_art",
        min(
            24.0,
            knowledge_events * 8.0
            + facts.innovation_discoverer_count * 10.0
            + facts.innovation_patron_count * 4.0
            + (6.0 if _job_has_any(job, _KNOWLEDGE_JOB_TERMS) else 0.0),
        ),
        label="Knowledge or art trace",
        explanation="Knowledge/culture events, innovation rows, patronage, or knowledge-work job terms preserve a cultural trace.",
        source_kind="fact",
    )
    acc.add(
        "ari_founder_institution",
        min(
            24.0,
            (8.0 if is_founder else 0.0)
            + facts.institution_founder_count * 8.0
            + facts.institution_patron_count * 4.0
            + facts.dynasty_founder_count * 10.0,
        ),
        label="Founder or institution trace",
        explanation="Founder, institution, patronage, and dynasty rows are formal recognition channels.",
        source_kind="fact",
    )
    acc.add(
        "ari_descendant_memory",
        min(16.0, facts.living_child_count * 0.8 + facts.child_count * 0.4),
        label="Descendant memory",
        explanation=f"{facts.child_count} child link(s), including {facts.living_child_count} living, can preserve family memory.",
        source_kind="fact",
    )
    acc.add(
        "ari_chronicler_interest",
        ari_components["ari_chronicler_interest"],
        label="Chronicler interest",
        explanation="Public records, total records, and Narrative Heat make later chronicler attention more likely.",
        source_kind="formula",
    )
    acc.add(
        "ari_low_status_visibility",
        ari_components.get("ari_low_status_visibility", 0.0),
        label="Low-status legal visibility",
        explanation=(
            f"{facts.outlaw_case_count} outlaw case(s), {facts.outlaw_custody_count} custody row(s), "
            f"{facts.outlaw_refuge_count} refuge trace(s), max knownness {facts.outlaw_knownness_max:.2f}, "
            f"and max pursuit pressure {facts.outlaw_pursuit_max:.2f} preserve non-prestigious recognition."
        ),
        source_kind="fact",
    )

    if facts.public_record_count == 0 and facts.office_holding_count == 0:
        acc.add(
            "ari_suppression_obscurity_penalty",
            -8.0,
            label="No public or office records",
            explanation="No public record rows or office holdings were found, lowering recognition.",
            axis="obscurity",
            source_kind="fact",
        )
    if job_tier != "premium" and not _status_is_high(status):
        acc.add(
            "ari_suppression_obscurity_penalty",
            -3.0,
            label="Low-status preservation bias",
            explanation="Common job tier and non-high status make preservation less likely.",
            axis="obscurity",
            source_kind="person",
        )
    if criminal_role and facts.legal_fallout_count == 0 and facts.public_record_count <= 1:
        acc.add(
            "ari_suppression_obscurity_penalty",
            -5.0,
            label="Stigmatized but thinly recorded",
            explanation="Criminal-role evidence without legal/public records may suppress recognition.",
            axis="obscurity",
            source_kind="person",
        )


def _damped_score(raw_value: float, *, repeat: float = 1.5) -> float:
    raw = max(0.0, float(raw_value))
    if raw <= 1.0:
        return raw
    return 1.0 + (raw - 1.0) ** 0.5 * float(repeat)


def _human_join(values: Iterable[object], *, limit: int = 4) -> str:
    cleaned = [_clean_text(value) for value in values if _clean_text(value)]
    if not cleaned:
        return ""
    if len(cleaned) > limit:
        cleaned = [*cleaned[:limit], f"{len(cleaned) - limit} more"]
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _sentence_from_evidence(values: Iterable[object], *, fallback: str) -> str:
    text = _human_join(values)
    if not text:
        return fallback
    return _sentence_case(text) + "."


def _recognition_scope(
    *,
    facts: ArchiveFacts,
    ari_total: float,
    public_role_events: int,
    knowledge_events: int,
    relationship_public_consequence: int,
    criminal_outlaw_consequence: float,
) -> str:
    if facts.outlaw_case_count or criminal_outlaw_consequence >= 16.0:
        return "local_legal"
    if facts.legal_fallout_count:
        return "local_legal"
    if facts.innovation_discoverer_count >= 3 and (knowledge_events >= 3 or facts.public_record_count >= 3):
        return "regional"
    if (
        facts.innovation_discoverer_count
        or facts.institution_founder_count
        or facts.institution_patron_count
        or facts.office_holding_count
        or public_role_events >= 2
    ):
        return "institutional"
    if relationship_public_consequence or facts.reputation_count:
        return "local_social"
    if facts.public_record_count or facts.total_record_count >= 3 or facts.child_count:
        return "household"
    if ari_total >= 75.0:
        return "institutional"
    return "none"


def _texture_flags(
    *,
    facts: ArchiveFacts,
    age: int | None,
    deathyear: int | None,
    dead_child_count: int,
    tragic_evidence: list[str],
    innovation_names: tuple[str, ...],
    hardship_event_count: int,
    criminal_outlaw_consequence: float,
    relationship_consequence: float,
    relationship_arc_bonus: float,
    hidden_heat: float,
) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    if deathyear is not None and age is not None and age <= 30 and (dead_child_count or tragic_evidence):
        evidence = list(dict.fromkeys([*tragic_evidence]))
        flags.append(
            {
                "flag": "gifted_life_cut_short",
                "strength": round(_clamp(0.56 + dead_child_count * 0.035 + hidden_heat * 0.002, 0.0, 0.78), 4),
                "evidence": evidence[:5],
                "person_visible_text": "Brief promise and domestic loss leave a vivid but fragile trace.",
            }
        )
    if innovation_names and hardship_event_count:
        evidence = [
            f"{len(innovation_names)} named innovation(s): {_human_join(innovation_names)}",
            f"{hardship_event_count} hardship or standing-decline event(s)",
        ]
        flags.append(
            {
                "flag": "precarious_achievement",
                "strength": round(_clamp(0.38 + len(innovation_names) * 0.035 + hardship_event_count * 0.010, 0.0, 0.58), 4),
                "evidence": evidence,
                "person_visible_text": "Public innovation contrasts with private precarity.",
            }
        )
    if relationship_arc_bonus or (relationship_consequence >= 14.0 and facts.legal_fallout_count):
        flags.append(
            {
                "flag": "scandal_afterlife",
                "strength": round(_clamp(0.42 + relationship_arc_bonus * 0.01 + facts.legal_fallout_count * 0.04, 0.0, 0.68), 4),
                "evidence": [
                    f"{facts.legal_fallout_count} legal fallout row(s)",
                    "relationship churn escalated into public scandal",
                ],
                "person_visible_text": "Private relationship disorder became locally recordable scandal.",
            }
        )
    if criminal_outlaw_consequence >= 24.0 and facts.outlaw_case_count:
        flags.append(
            {
                "flag": "infamous_pursuit",
                "strength": round(_clamp(0.30 + facts.outlaw_case_count * 0.035 + facts.outlaw_custody_count * 0.04, 0.0, 0.52), 4),
                "evidence": [
                    f"{facts.outlaw_case_count} outlaw case(s)",
                    f"max pursuit pressure {facts.outlaw_pursuit_max:.2f}",
                    f"{facts.outlaw_custody_count} custody row(s)",
                ],
                "person_visible_text": "Legal pursuit preserves the person as infamy rather than prestige.",
            }
        )
    return flags


def _score_breakdown_payload(
    *,
    realized_consequence: float,
    latent_potential: float,
    raw_latent_potential: float,
    latent_cap: float,
    tragic_compression: float,
    knowledge_legacy: float,
    criminal_outlaw_consequence: float,
    relationship_consequence: float,
    public_social_consequence: float,
    repeat_pattern_volume: float,
    raw_repeat_pattern_volume: float,
    ordinary_family_trace: float,
    criminal_arc_bonus: float,
    relationship_arc_bonus: float,
    achievement_arc_bonus: float,
    recognition_scope: str,
) -> dict[str, object]:
    return {
        "schema": "person_archive_score_breakdown.v1",
        "recognition_scope": recognition_scope,
        "channels": {
            "realized_consequence": round(realized_consequence, 4),
            "latent_potential": round(latent_potential, 4),
            "tragic_compression": round(tragic_compression, 4),
            "knowledge_legacy": round(knowledge_legacy, 4),
            "criminal_outlaw_consequence": round(criminal_outlaw_consequence, 4),
            "relationship_consequence": round(relationship_consequence, 4),
            "public_social_consequence": round(public_social_consequence, 4),
            "repeat_pattern_volume": round(repeat_pattern_volume, 4),
            "ordinary_family_trace": round(ordinary_family_trace, 4),
        },
        "caps": {
            "latent_potential_capped_from": round(raw_latent_potential, 4),
            "latent_potential_cap": round(latent_cap, 4),
            "repeat_pattern_damped_from": round(raw_repeat_pattern_volume, 4),
        },
        "arc_bonuses": {
            "criminal_outlaw_arc": round(criminal_arc_bonus, 4),
            "relationship_scandal_arc": round(relationship_arc_bonus, 4),
            "public_achievement_arc": round(achievement_arc_bonus, 4),
        },
    }


def _component_json_payload(
    *,
    facts: ArchiveFacts,
    narrative_components: dict[str, float],
    ari_components: dict[str, float],
    narrative_total: float,
    ari_total: float,
    hidden_heat: float,
    violet_score: float,
    recognition_bucket: str,
    narrative_bucket: str,
    recognition_scope: str,
    infamy_gap: float,
    prestige_gap: float,
    texture_flags: list[dict[str, object]],
    score_breakdown: dict[str, object],
    event_type_counts: Counter[str],
    role_counts: Counter[str],
    source_event_max_id: int,
    reasons: list[ScoreReason],
    flags: dict[str, object],
) -> dict[str, object]:
    totals = {
        "narrative_heat_total": round(narrative_total, 4),
        "archive_recognition_index": round(ari_total, 4),
        "hidden_heat": round(hidden_heat, 4),
        "violet_marginalia_score": round(violet_score, 4),
        "violet_marginalia": bool(violet_score >= 0.42),
        "infamy_gap": round(infamy_gap, 4),
        "prestige_gap": round(prestige_gap, 4),
    }
    components = _components_from_values(
        {
            **narrative_components,
            **ari_components,
            "hidden_heat": hidden_heat,
            "violet_marginalia_score": violet_score,
        }
    )
    top_reason_summaries = _reason_summaries(reasons, limit=MAX_EXPLANATION_REASONS)
    data_caveats = _data_caveats(facts)
    source_event_ids = sorted(
        {
            int(reason.source_id)
            for reason in reasons
            if reason.source_kind == "event" and reason.source_id is not None
        }
    )[:MAX_EXPLANATION_REASONS]
    return {
        "schema": "person_archive_score_components.v3",
        "score_version": SCORE_FORMULA_VERSION,
        "formula_version": SCORE_FORMULA_VERSION,
        "summary": _score_summary(
            narrative_total=narrative_total,
            ari_total=ari_total,
            reasons=reasons,
        ),
        "totals": totals,
        "components": components,
        "bucket_labels": {
            "archive_quadrant": recognition_bucket,
            "narrative": narrative_bucket,
            "recognition_scope": recognition_scope,
        },
        "texture_flags": texture_flags,
        "score_breakdown": score_breakdown,
        "top_event_types": event_type_counts.most_common(8),
        "top_roles": role_counts.most_common(8),
        "evidence_counts": {
            "events": int(facts.event_count),
            "public_records": int(facts.public_record_count),
            "total_records": int(facts.total_record_count),
            "children": int(facts.child_count),
            "living_children": int(facts.living_child_count),
            "office_holdings": int(facts.office_holding_count),
            "obligations": int(facts.obligation_count),
            "reputation_marks": int(facts.reputation_count),
            "legal_fallout": int(facts.legal_fallout_count),
            "institutions": int(facts.institution_founder_count + facts.institution_patron_count),
            "innovations": int(facts.innovation_discoverer_count + facts.innovation_patron_count),
            "outlaw_cases": int(facts.outlaw_case_count),
            "outlaw_custodies": int(facts.outlaw_custody_count),
            "outlaw_refuges": int(facts.outlaw_refuge_count),
        },
        "data_caveats": data_caveats,
        "top_reason_summaries": top_reason_summaries,
        "top_reasons": top_reason_summaries,
        "source_ids": {
            "events": source_event_ids,
            "source_event_max_id": int(source_event_max_id),
        },
        "flags": flags,
        # Legacy v1 convenience keys retained for older tools/tests.
        "event_count": int(facts.event_count),
        "child_count": int(facts.child_count),
        "living_child_count": int(facts.living_child_count),
        "office_holding_count": int(facts.office_holding_count),
        "public_record_count": int(facts.public_record_count),
    }


def _components_from_values(values: dict[str, float]) -> dict[str, dict[str, object]]:
    components: dict[str, dict[str, object]] = {}
    for key, value in values.items():
        label, axis, definition = _COMPONENT_META.get(
            key, (_display_label_from_key(key), "derived", "")
        )
        if key == "violet_marginalia_score":
            score_value = _clamp(float(value), 0.0, 1.0)
        else:
            score_value = _clamp(float(value), 0.0, 100.0)
        contribution = score_value
        if axis == "obscurity":
            contribution = -abs(contribution)
        components[key] = {
            "label": label,
            "axis": axis,
            "score": round(score_value, 4),
            "contribution": round(contribution, 4),
            "definition": definition,
        }
    return components


def _components_from_score_row(score: dict[str, object]) -> dict[str, dict[str, object]]:
    values: dict[str, float] = {}
    for key in (*_NARRATIVE_COLUMNS, *_ARI_COLUMNS, "hidden_heat", "violet_marginalia_score"):
        if key in score:
            values[key] = _coerce_float(score.get(key)) or 0.0
    return _components_from_values(values)


def _data_caveats(facts: ArchiveFacts) -> list[str]:
    caveats: list[str] = []
    if facts.event_count == 0:
        caveats.append("No linked event rows were found; explanation leans on checkpoint facts.")
    if facts.public_record_count == 0:
        caveats.append("No public archive records currently attach to this person.")
    if facts.total_record_count <= 1:
        caveats.append("Sparse linked records; top reasons may be incomplete.")
    return caveats


def _score_summary(
    *,
    narrative_total: float,
    ari_total: float,
    reasons: list[ScoreReason],
) -> str:
    narrative_reason = _first_reason_label(reasons, "narrative")
    ari_reason = _first_reason_label(reasons, "ari")
    obscure_reason = _first_reason_label(reasons, "obscurity")
    if narrative_total >= 45.0 and ari_total >= 45.0:
        return f"{_sentence_case(narrative_reason)} anchors the narrative; {_clean_text(ari_reason)} preserves recognition."
    elif narrative_total >= 45.0:
        return f"{_sentence_case(narrative_reason)} is vivid, but {_clean_text(obscure_reason)} limits formal recognition."
    elif ari_total >= 45.0:
        return f"{_sentence_case(ari_reason)} preserves the person despite a comparatively quiet narrative record."
    return f"{_sentence_case(narrative_reason)} is the strongest surviving trace, with little formal recognition."


def _fallback_score_summary(_bucket: object, narrative_heat: object, ari: object) -> str:
    narrative = _coerce_float(narrative_heat) or 0.0
    recognition = _coerce_float(ari) or 0.0
    return f"Narrative Heat {narrative:.1f}, ARI {recognition:.1f}."


def _strip_summary_bucket(summary: object, bucket: object) -> str:
    text = _clean_text(summary)
    bucket_text = _clean_text(bucket)
    if not text or not bucket_text:
        return text
    prefix = f"{_sentence_case(bucket_text)}:"
    if text.lower().startswith(prefix.lower()):
        return text[len(prefix):].lstrip()
    return text


def _first_reason_label(reasons: list[ScoreReason], axis: str) -> str:
    for reason in reasons:
        if reason.axis == axis:
            return _clean_text(reason.label).lower() or "available traces"
    return "available traces"


def _reason_summaries(
    reasons: list[ScoreReason], *, limit: int
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for reason in sorted(
        reasons,
        key=lambda r: (
            -abs(float(r.contribution)),
            r.sort_rank,
            r.component_key,
            r.label,
        ),
    )[: max(0, int(limit))]:
        summaries.append(
            {
                "component_key": reason.component_key,
                "axis": reason.axis,
                "contribution": round(float(reason.contribution), 4),
                "source_kind": reason.source_kind,
                "source_id": reason.source_id,
                "source_year": reason.source_year,
                "role": reason.role,
                "label": reason.label,
                "explanation": reason.explanation,
                "sort_rank": int(reason.sort_rank),
                "score_version": int(reason.score_version),
            }
        )
    return summaries


def _build_archive_facts(
    conn: sqlite3.Connection, person_ids: frozenset[int]
) -> defaultdict[int, ArchiveFacts]:
    facts: defaultdict[int, ArchiveFacts] = defaultdict(ArchiveFacts)
    if not person_ids:
        return facts
    _add_child_counts(conn, facts, person_ids)
    _add_current_relationship_counts(conn, facts, person_ids)
    _add_ledger_counts(conn, facts, person_ids)
    _add_office_counts(conn, facts, person_ids)
    _add_institution_counts(conn, facts, person_ids)
    _add_innovation_counts(conn, facts, person_ids)
    _add_outlaw_counts(conn, facts, person_ids)
    _add_event_record_counts(conn, facts, person_ids)
    _add_move_counts(conn, facts, person_ids)
    return facts


def _add_child_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    for column in ("father_id", "mother_id"):
        for row in _group_count_by_person_column(conn, "simulation_people", column, person_ids):
            pid = int(row["person_id"])
            facts[pid].child_count += int(row["n"] or 0)
            facts[pid].living_child_count += int(row["living_n"] or 0)


def _add_current_relationship_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    for table, attr in (
        ("simulation_couples", "current_partner_count"),
        ("simulation_paramours", "current_paramour_count"),
    ):
        if not _table_exists(conn, table):
            continue
        rows = _fetch_dicts(conn, f"SELECT person_a_id, person_b_id FROM {table}")
        involved_ids = {
            pid
            for row in rows
            for pid in (_coerce_int(row.get("person_a_id")), _coerce_int(row.get("person_b_id")))
            if pid is not None
        }
        alive_ids: set[int] | None = None
        if involved_ids and _table_exists(conn, "simulation_people") and "is_alive" in _table_columns(conn, "simulation_people"):
            alive_ids = set()
            for chunk in _chunks(tuple(sorted(involved_ids))):
                placeholders = ", ".join("?" for _ in chunk)
                for row in _fetch_dicts(
                    conn,
                    f"""
                    SELECT person_id
                    FROM simulation_people
                    WHERE person_id IN ({placeholders}) AND COALESCE(is_alive, 0) = 1
                    """,
                    chunk,
                ):
                    alive_ids.add(int(row["person_id"]))
        for row in rows:
            a = _coerce_int(row.get("person_a_id"))
            b = _coerce_int(row.get("person_b_id"))
            if alive_ids is not None and (a not in alive_ids or b not in alive_ids):
                continue
            if a in person_ids:
                setattr(facts[int(a)], attr, getattr(facts[int(a)], attr) + 1)
            if b in person_ids:
                setattr(facts[int(b)], attr, getattr(facts[int(b)], attr) + 1)


def _add_ledger_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if _table_exists(conn, "simulation_obligations"):
        for column in ("owed_by_person_id", "owed_to_person_id"):
            for row in _group_status_count(conn, "simulation_obligations", column, person_ids):
                pid = int(row["person_id"])
                count = int(row["n"] or 0)
                facts[pid].obligation_count += count
                if str(row["status"] or "").lower() == "active":
                    facts[pid].obligation_active_count += count
    if _table_exists(conn, "simulation_reputation_marks"):
        for row in _group_reputation_counts(conn, person_ids):
            pid = int(row["person_id"])
            count = int(row["n"] or 0)
            facts[pid].reputation_count += count
            direction = str(row["direction"] or "").strip().lower()
            if direction == "negative":
                facts[pid].reputation_negative_count += count
            else:
                facts[pid].reputation_positive_count += count
    if _table_exists(conn, "simulation_legal_fallout"):
        for column in ("principal_person_id", "opposing_person_id", "related_person_id"):
            for row in _group_status_count(conn, "simulation_legal_fallout", column, person_ids):
                facts[int(row["person_id"])].legal_fallout_count += int(row["n"] or 0)
    if _table_exists(conn, "simulation_faction_memory"):
        for column in ("principal_person_id", "opposing_person_id"):
            for row in _group_status_count(conn, "simulation_faction_memory", column, person_ids):
                facts[int(row["person_id"])].faction_memory_count += int(row["n"] or 0)


def _add_office_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if _table_exists(conn, "simulation_office_holdings"):
        for row in _group_simple_count(conn, "simulation_office_holdings", "holder_person_id", person_ids):
            facts[int(row["person_id"])].office_holding_count += int(row["n"] or 0)
    if _table_exists(conn, "simulation_office_seats"):
        for row in _group_status_count(conn, "simulation_office_seats", "holder_person_id", person_ids):
            if str(row["status"] or "").lower() == "active":
                facts[int(row["person_id"])].current_office_count += int(row["n"] or 0)
    if _table_exists(conn, "simulation_dynasties"):
        for row in _group_simple_count(conn, "simulation_dynasties", "founder_person_id", person_ids):
            facts[int(row["person_id"])].dynasty_founder_count += int(row["n"] or 0)


def _add_institution_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if not _table_exists(conn, "simulation_institutions"):
        return
    for row in _group_simple_count(conn, "simulation_institutions", "founder_person_id", person_ids):
        facts[int(row["person_id"])].institution_founder_count += int(row["n"] or 0)
    for row in _group_simple_count(conn, "simulation_institutions", "patron_person_id", person_ids):
        facts[int(row["person_id"])].institution_patron_count += int(row["n"] or 0)


def _add_innovation_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if not _table_exists(conn, "simulation_innovation_discoveries"):
        return
    for row in _group_simple_count(conn, "simulation_innovation_discoveries", "discoverer_person_id", person_ids):
        facts[int(row["person_id"])].innovation_discoverer_count += int(row["n"] or 0)
    for row in _group_simple_count(conn, "simulation_innovation_discoveries", "patron_person_id", person_ids):
        facts[int(row["person_id"])].innovation_patron_count += int(row["n"] or 0)
    for column in ("discoverer_person_id", "patron_person_id"):
        if column not in _table_columns(conn, "simulation_innovation_discoveries"):
            continue
        for chunk in _chunks(tuple(sorted(person_ids))):
            placeholders = ", ".join("?" for _ in chunk)
            for row in _fetch_dicts(
                conn,
                f"""
                SELECT {column} AS person_id, innovation_name, innovation_id
                FROM simulation_innovation_discoveries
                WHERE {column} IN ({placeholders})
                ORDER BY discovery_year, discovery_id
                """,
                chunk,
            ):
                pid = _coerce_int(row.get("person_id"))
                if pid is None:
                    continue
                name = _clean_text(row.get("innovation_name")) or _clean_text(row.get("innovation_id"))
                if name and name not in facts[pid].innovation_names:
                    facts[pid].innovation_names.append(name)


def _add_outlaw_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if _table_exists(conn, "simulation_outlaw_cases"):
        for chunk in _chunks(tuple(sorted(person_ids))):
            placeholders = ", ".join("?" for _ in chunk)
            for row in _fetch_dicts(
                conn,
                f"""
                SELECT accused_person_id AS person_id,
                       COUNT(*) AS n,
                       COUNT(DISTINCT victim_person_id) AS victim_n,
                       SUM(CASE WHEN offense_type = 'murder' OR offense_kind = 'murder' THEN 1 ELSE 0 END) AS murder_n,
                       SUM(CASE WHEN refuge_id IS NOT NULL AND refuge_id != '' THEN 1 ELSE 0 END) AS refuge_n,
                       MAX(knownness_01) AS max_knownness,
                       MAX(pursuit_pressure_01) AS max_pursuit
                FROM simulation_outlaw_cases
                WHERE accused_person_id IN ({placeholders})
                GROUP BY accused_person_id
                """,
                chunk,
            ):
                pid = int(row["person_id"])
                facts[pid].outlaw_case_count += int(row["n"] or 0)
                facts[pid].outlaw_victim_count += int(row["victim_n"] or 0)
                facts[pid].outlaw_murder_case_count += int(row["murder_n"] or 0)
                facts[pid].outlaw_refuge_count += int(row["refuge_n"] or 0)
                facts[pid].outlaw_knownness_max = max(
                    facts[pid].outlaw_knownness_max,
                    _coerce_float(row.get("max_knownness")) or 0.0,
                )
                facts[pid].outlaw_pursuit_max = max(
                    facts[pid].outlaw_pursuit_max,
                    _coerce_float(row.get("max_pursuit")) or 0.0,
                )
    if _table_exists(conn, "simulation_outlaw_custodies"):
        for row in _group_simple_count(conn, "simulation_outlaw_custodies", "person_id", person_ids):
            facts[int(row["person_id"])].outlaw_custody_count += int(row["n"] or 0)


def _add_event_record_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if not (_table_exists(conn, "simulation_event_people") and _table_exists(conn, "simulation_event_records")):
        return
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        for row in _fetch_dicts(
            conn,
            f"""
            SELECT
                ep.person_id AS person_id,
                COUNT(DISTINCT r.record_id) AS total_records,
                SUM(CASE
                    WHEN r.visibility_state IN ('public_known', 'rumored', 'rediscovered', 'misattributed')
                    THEN 1 ELSE 0
                END) AS public_records
            FROM simulation_event_people ep
            JOIN simulation_event_records r ON r.event_id = ep.event_id
            WHERE ep.person_id IN ({placeholders})
            GROUP BY ep.person_id
            """,
            chunk,
        ):
            pid = int(row["person_id"])
            facts[pid].total_record_count += int(row["total_records"] or 0)
            facts[pid].public_record_count += int(row["public_records"] or 0)


def _add_move_counts(
    conn: sqlite3.Connection, facts: defaultdict[int, ArchiveFacts], person_ids: frozenset[int]
) -> None:
    if not _table_exists(conn, "simulation_event_moves"):
        return
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        for row in _fetch_dicts(
            conn,
            f"""
            SELECT
                moved_person_id AS person_id,
                COUNT(*) AS n,
                SUM(CASE WHEN cross_region THEN 1 ELSE 0 END) AS cross_n
            FROM simulation_event_moves
            WHERE moved_person_id IN ({placeholders})
            GROUP BY moved_person_id
            """,
            chunk,
        ):
            pid = int(row["person_id"])
            facts[pid].move_count += int(row["n"] or 0)
            facts[pid].cross_region_move_count += int(row["cross_n"] or 0)


def _load_events_by_person(
    conn: sqlite3.Connection, person_ids: frozenset[int]
) -> dict[int, list[EventFact]]:
    events: dict[int, list[EventFact]] = defaultdict(list)
    if not person_ids or not (_table_exists(conn, "simulation_events") and _table_exists(conn, "simulation_event_people")):
        return events
    payload_cache: dict[int, dict[str, object]] = {}
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        rows = _fetch_dicts(
            conn,
            f"""
            SELECT
                ep.person_id,
                ep.role,
                e.id AS event_id,
                e.sim_year,
                e.event_type,
                e.primary_person_id,
                e.secondary_person_id,
                e.settlement_key,
                e.region_key,
                e.event_origin,
                e.payload_json
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            WHERE ep.person_id IN ({placeholders})
            ORDER BY e.sim_year ASC, e.id ASC
            """,
            chunk,
        )
        for row in rows:
            event_id = int(row["event_id"])
            payload = payload_cache.get(event_id)
            if payload is None:
                payload = event_payload_from_row(row, conn, expand=True)
                payload_cache[event_id] = payload
            pid = int(row["person_id"])
            events[pid].append(
                EventFact(
                    event_id=event_id,
                    sim_year=_coerce_int(row.get("sim_year")),
                    event_type=_clean_text(row.get("event_type")),
                    role=_clean_text(row.get("role") or "related"),
                    payload=payload,
                )
            )
    return events


def _load_people(
    conn: sqlite3.Connection, person_ids: tuple[int, ...] | None
) -> list[dict[str, object]]:
    if person_ids is None:
        return _fetch_dicts(
            conn,
            """
            SELECT *
            FROM simulation_people
            ORDER BY person_id
            """,
        )
    rows: list[dict[str, object]] = []
    for chunk in _chunks(person_ids):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            _fetch_dicts(
                conn,
                f"""
                SELECT *
                FROM simulation_people
                WHERE person_id IN ({placeholders})
                ORDER BY person_id
                """,
                chunk,
            )
        )
    rows.sort(key=lambda r: int(r["person_id"]))
    return rows


def _person_from_checkpoint_row(row: dict[str, object]) -> dict[str, object]:
    data = _json_dict(row.get("person_json"))
    for key, value in row.items():
        if key == "person_json":
            continue
        if value is not None and key not in data:
            data[key] = value
    return data


def _person_tags(person: dict[str, object]) -> set[str]:
    tags: set[str] = set()
    for key in ("genome_trait_phrases", "genome_composite_names", "tags"):
        raw = person.get(key)
        if isinstance(raw, str):
            tags.add(raw.strip().lower())
        elif isinstance(raw, (list, tuple, set)):
            tags.update(str(item).strip().lower() for item in raw if str(item).strip())
    return tags


def _person_genome(person: dict[str, object]) -> dict[str, float]:
    raw = person.get("genome")
    if isinstance(raw, dict):
        return {
            str(key): float(value)
            for key, value in raw.items()
            if _coerce_float(value) is not None
        }
    slots = person.get("ts")
    values = person.get("g")
    if isinstance(slots, list) and isinstance(values, list):
        out: dict[str, float] = {}
        for trait, value in zip(slots, values):
            number = _coerce_float(value)
            if number is not None:
                out[str(trait)] = number
        return out
    return {}


def _contradiction_reason_specs(
    *,
    tags: set[str],
    job: str,
    criminal_role: bool,
    official_role: bool,
    knowledge_role: bool,
    caused_victims: int,
) -> list[tuple[float, str, str]]:
    text = " | ".join(sorted(tags | {job.lower()}))

    def has(*needles: str) -> bool:
        return all(needle in text for needle in needles)

    specs: list[tuple[float, str, str]] = []
    checks = (
        (("nurtur", "neglig"), 8.0, "Nurturing/negligent tension"),
        (("passive", "temper"), 8.0, "Passive/temper tension"),
        (("humble", "inciter"), 6.0, "Humble/inciter tension"),
        (("modest", "amorous"), 6.0, "Modest/amorous tension"),
        (("witty", "oblivious"), 6.0, "Witty/oblivious tension"),
        (("lawless", "diplomat"), 8.0, "Lawless diplomat"),
        (("miser", "patron"), 6.0, "Miser patron"),
        (("artist", "murder"), 8.0, "Artist and murder trace"),
        (("raider", "official"), 8.0, "Raider official"),
    )
    for needles, value, label in checks:
        if has(*needles):
            shown = " and ".join(needles)
            specs.append(
                (
                    value,
                    label,
                    f"Character/job text contains both {shown}, creating a contradiction signal.",
                )
            )
    if criminal_role and official_role:
        specs.append(
            (
                8.0,
                "Criminal-official contradiction",
                "Criminal and official evidence coexist in the same life.",
            )
        )
    if knowledge_role and caused_victims:
        specs.append(
            (
                8.0,
                "Creator-victim contradiction",
                "Knowledge/culture traces coexist with caused victims.",
            )
        )
    if "amorous" in text and "pious" in text:
        specs.append(
            (
                5.0,
                "Amorous/pious tension",
                "Character text contains both amorous and pious signals.",
            )
        )
    return specs


def _contradiction_heat(
    *,
    tags: set[str],
    job: str,
    criminal_role: bool,
    official_role: bool,
    knowledge_role: bool,
    caused_victims: int,
) -> float:
    heat = sum(
        value
        for value, _, _ in _contradiction_reason_specs(
            tags=tags,
            job=job,
            criminal_role=criminal_role,
            official_role=official_role,
            knowledge_role=knowledge_role,
            caused_victims=caused_victims,
        )
    )
    return min(30.0, heat)


def _event_reason_label(event_type: str, role: str) -> str:
    event = _clean_text(event_type).replace("_", " ") or "event"
    shown_role = _clean_text(role).replace("_", " ") or "related"
    return f"{_sentence_case(event)} as {shown_role}"


def _component_axis(component_key: str) -> str:
    return _COMPONENT_META.get(component_key, ("", "derived", ""))[1]


def _display_label_from_key(value: str) -> str:
    return " ".join(part for part in str(value).replace("_", " ").split()).title()


def _sentence_case(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return text[:1].upper() + text[1:]


def _event_heat_for_role(event_type: str, role: str) -> float:
    if event_type == "murder":
        if role in {"killer", "perpetrator"}:
            return 25.0
        if role == "victim":
            return 15.0
        return 2.0
    if event_type == "knowledge_culture":
        if role == "creator":
            return 15.0
        if role == "patron":
            return 8.0
        return 2.0
    if event_type == "public_virtue":
        if role == "benefactor":
            return 12.0
        if role == "beneficiary":
            return 3.0
        return 1.0
    if event_type == "property_crime":
        if role in {"perpetrator", "accused"}:
            return 8.0
        if role == "target":
            return 4.0
        return 1.0
    if event_type == "affair_scandal":
        if role in {"accused", "paramour"}:
            return 8.0
        if role == "betrayed_partner":
            return 4.0
        return 1.0
    if event_type in {"office_selection", "office_succession"}:
        return 10.0 if role == "holder" else 4.0
    if event_type in {"campaign_started", "campaign_ended", "battle_fought"}:
        return 12.0
    if event_type.startswith("polity_") or event_type == "dynastic_marriage_alliance":
        return 10.0
    if event_type in {"settlement_commercial_outpost_founded", "settlement_founded"}:
        return 30.0 if role in {"founder", "subject", "moved"} else 6.0
    if event_type == "founder_created":
        return 6.0
    if event_type in {"legal_adjudication", "inheritance_dispute"}:
        return 8.0
    if event_type == "death":
        return 2.0
    return 0.5 if event_type in _MAJOR_EVENT_TYPES else 0.0


def _group_count_by_person_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    person_ids: frozenset[int],
) -> list[dict[str, object]]:
    if not _table_exists(conn, table) or column not in _table_columns(conn, table):
        return []
    rows: list[dict[str, object]] = []
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            _fetch_dicts(
                conn,
                f"""
                SELECT
                    {_quote_identifier(column)} AS person_id,
                    COUNT(*) AS n,
                    SUM(CASE WHEN is_alive THEN 1 ELSE 0 END) AS living_n
                FROM {_quote_identifier(table)}
                WHERE {_quote_identifier(column)} IN ({placeholders})
                GROUP BY {_quote_identifier(column)}
                """,
                chunk,
            )
        )
    return rows


def _group_simple_count(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    person_ids: frozenset[int],
) -> list[dict[str, object]]:
    if not _table_exists(conn, table) or column not in _table_columns(conn, table):
        return []
    rows: list[dict[str, object]] = []
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            _fetch_dicts(
                conn,
                f"""
                SELECT {_quote_identifier(column)} AS person_id, COUNT(*) AS n
                FROM {_quote_identifier(table)}
                WHERE {_quote_identifier(column)} IN ({placeholders})
                GROUP BY {_quote_identifier(column)}
                """,
                chunk,
            )
        )
    return rows


def _group_status_count(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    person_ids: frozenset[int],
) -> list[dict[str, object]]:
    if not _table_exists(conn, table) or column not in _table_columns(conn, table):
        return []
    status_expr = "status" if "status" in _table_columns(conn, table) else "''"
    rows: list[dict[str, object]] = []
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            _fetch_dicts(
                conn,
                f"""
                SELECT
                    {_quote_identifier(column)} AS person_id,
                    {status_expr} AS status,
                    COUNT(*) AS n
                FROM {_quote_identifier(table)}
                WHERE {_quote_identifier(column)} IN ({placeholders})
                GROUP BY {_quote_identifier(column)}, {status_expr}
                """,
                chunk,
            )
        )
    return rows


def _group_reputation_counts(
    conn: sqlite3.Connection, person_ids: frozenset[int]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            _fetch_dicts(
                conn,
                f"""
                SELECT person_id, direction, COUNT(*) AS n
                FROM simulation_reputation_marks
                WHERE person_id IN ({placeholders})
                GROUP BY person_id, direction
                """,
                chunk,
            )
        )
    return rows


def _delete_scores_for_ids(conn: sqlite3.Connection, person_ids: set[int]) -> None:
    if not person_ids:
        return
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"""
            DELETE FROM simulation_person_archive_scores
            WHERE person_id IN ({placeholders})
            """,
            chunk,
        )
        conn.execute(
            f"""
            DELETE FROM simulation_person_archive_score_reasons
            WHERE person_id IN ({placeholders})
            """,
            chunk,
        )


def _delete_reason_rows_for_ids(conn: sqlite3.Connection, person_ids: set[int]) -> None:
    if not person_ids:
        return
    for chunk in _chunks(tuple(sorted(person_ids))):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"""
            DELETE FROM simulation_person_archive_score_reasons
            WHERE person_id IN ({placeholders})
            """,
            chunk,
        )


def _rank_reasons(reasons: list[ScoreReason]) -> list[ScoreReason]:
    ranked: list[ScoreReason] = []
    groups: dict[str, list[ScoreReason]] = defaultdict(list)
    for reason in reasons:
        groups[reason.component_key].append(reason)
    for component_key in sorted(groups):
        component_reasons = sorted(
            groups[component_key],
            key=lambda r: (
                1 if r.source_kind == "formula" else 0,
                -abs(float(r.contribution)),
                r.source_year if r.source_year is not None else 10**9,
                r.source_id if r.source_id is not None else 10**9,
                r.label,
            ),
        )[:MAX_REASONS_PER_COMPONENT]
        for index, reason in enumerate(component_reasons, start=1):
            reason.sort_rank = index
            reason.score_version = SCORE_FORMULA_VERSION
            ranked.append(reason)
    return sorted(
        ranked,
        key=lambda r: (
            1 if r.source_kind == "formula" else 0,
            -abs(float(r.contribution)),
            r.component_key,
            r.sort_rank,
            r.label,
        ),
    )


def _load_reason_dicts(
    conn: sqlite3.Connection, person_id: int, *, max_reasons: int
) -> list[dict[str, object]]:
    if not _table_exists(conn, REASON_TABLE):
        return []
    return _fetch_dicts(
        conn,
        """
        SELECT
            person_id,
            component_key,
            axis,
            contribution,
            source_kind,
            source_id,
            source_year,
            role,
            label,
            explanation,
            sort_rank,
            score_version
        FROM simulation_person_archive_score_reasons
        WHERE person_id = ?
        ORDER BY
            CASE WHEN source_kind = 'formula' THEN 1 ELSE 0 END,
            ABS(contribution) DESC,
            component_key ASC,
            sort_rank ASC
        LIMIT ?
        """,
        (int(person_id), max(1, min(200, int(max_reasons)))),
    )


def _fetch_dicts(
    conn: sqlite3.Connection, sql: str, params: Iterable[object] = ()
) -> list[dict[str, object]]:
    cur = conn.execute(sql, tuple(params))
    columns = [col[0] for col in cur.description or ()]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _fetch_one_dict(
    conn: sqlite3.Connection, sql: str, params: Iterable[object] = ()
) -> dict[str, object] | None:
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    if row is None:
        return None
    columns = [col[0] for col in cur.description or ()]
    return dict(zip(columns, row))


def _max_event_id(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "simulation_events"):
        return 0
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM simulation_events").fetchone()
    return int(row[0] or 0)


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


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _json_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(loaded) if isinstance(loaded, list) else []


def _clean_text(value: object) -> str:
    return str(value or "").strip()


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


def _coerce_person_id_list(value: object) -> list[int]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = text
        if isinstance(value, str):
            value = [part.strip() for part in value.split(";")]
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[int] = []
    for item in value:
        pid = _coerce_int(item)
        if pid is not None:
            out.append(pid)
    return out


def _normalize_person_ids(person_ids: Iterable[int] | None) -> tuple[int, ...] | None:
    if person_ids is None:
        return None
    seen: set[int] = set()
    out: list[int] = []
    for raw in person_ids:
        pid = _coerce_int(raw)
        if pid is None or pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return tuple(sorted(out))


def _chunks(values: tuple[int, ...], size: int = 450) -> Iterable[tuple[int, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _stable_jitter(person_id: int, scale: float) -> float:
    raw = (int(person_id) * 1103515245 + 12345) & 0x7FFFFFFF
    return (raw % 1000) / 1000.0 * float(scale)


def _job_has_any(job: str, terms: frozenset[str]) -> bool:
    low = job.lower()
    return any(term in low for term in terms)


def _status_is_high(status: str) -> bool:
    return any(word in status for word in _HIGH_STATUS_WORDS)


def _other_person_from_payload(payload: dict[str, object], person_id: int) -> int | None:
    a = _coerce_int(payload.get("person_a_id"))
    b = _coerce_int(payload.get("person_b_id"))
    if a == person_id and b is not None:
        return b
    if b == person_id and a is not None:
        return a
    return None


def _recognition_bucket(narrative_heat: float, ari: float) -> str:
    if narrative_heat >= 45.0 and ari >= 45.0:
        return "interesting and remembered"
    if narrative_heat >= 45.0:
        return "interesting but obscure"
    if ari >= 45.0:
        return "documented but quiet"
    return "ordinary or poorly preserved"


def _narrative_bucket(narrative_heat: float) -> str:
    if narrative_heat >= 70.0:
        return "high"
    if narrative_heat >= 40.0:
        return "moderate"
    return "low"
