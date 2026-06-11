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
from typing import Iterable


SCORE_FORMULA_VERSION = 1

SCORE_TABLE = "simulation_person_archive_scores"

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
    "component_json",
    "updated_at",
)

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
    public_record_count: int = 0
    total_record_count: int = 0
    move_count: int = 0
    cross_region_move_count: int = 0
    event_count: int = 0
    linked_person_count: int = 0
    event_types: Counter[str] = field(default_factory=Counter)
    roles: Counter[str] = field(default_factory=Counter)


def ensure_person_archive_score_schema(conn: sqlite3.Connection) -> None:
    """Create the cached person archive score table and retrieval indexes."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_person_archive_scores (
            person_id INTEGER PRIMARY KEY,
            score_version INTEGER NOT NULL DEFAULT 1,
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
            ari_suppression_obscurity_penalty REAL NOT NULL DEFAULT 0.0,
            hidden_heat REAL NOT NULL DEFAULT 0.0,
            violet_marginalia_score REAL NOT NULL DEFAULT 0.0,
            violet_marginalia INTEGER NOT NULL DEFAULT 0,
            recognition_bucket TEXT NOT NULL DEFAULT '',
            narrative_bucket TEXT NOT NULL DEFAULT '',
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
        """
    )

    columns = set(_table_columns(conn, SCORE_TABLE))
    missing = [col for col in _SCORE_COLUMNS if col not in columns]
    if missing:
        raise RuntimeError(
            "simulation_person_archive_scores is missing expected columns: "
            + ", ".join(missing)
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

    if not people_rows:
        return 0

    person_id_set = frozenset(int(r["person_id"]) for r in people_rows)
    facts = _build_archive_facts(conn, person_id_set)
    events_by_person = _load_events_by_person(conn, person_id_set)
    max_event_id = _max_event_id(conn)
    now = updated_at or datetime.now(timezone.utc).isoformat()

    rows = []
    for row in people_rows:
        person_id = int(row["person_id"])
        person = _person_from_checkpoint_row(row)
        score = _score_person(
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

    placeholders = ", ".join("?" for _ in _SCORE_COLUMNS)
    columns_sql = ", ".join(_quote_identifier(col) for col in _SCORE_COLUMNS)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO simulation_person_archive_scores ({columns_sql})
        VALUES ({placeholders})
        """,
        rows,
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
) -> dict[str, object]:
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
        event_heat += _event_heat_for_role(event_type, role)
        if historical_importance > 0.0:
            event_heat += min(8.0, historical_importance * 10.0)
        if event_type in _PUBLIC_EVENT_TYPES:
            public_role_events += 1
        if event_type == "murder":
            if role in {"killer", "perpetrator"}:
                caused_victims += 1
                criminal_role = True
            if role == "victim":
                unusual_death = True
        elif event_type == "property_crime" and role in {"perpetrator", "accused"}:
            criminal_role = True
        elif event_type == "affair_scandal" and role in {"accused", "paramour"}:
            rarity_heat += 3.0
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
    event_heat += min(15.0, facts.child_count * 1.2)
    if facts.child_count >= 8:
        event_heat += 5.0
    if age is not None and age >= 90:
        event_heat += 10.0
        rarity_heat += 10.0
    elif age is not None and age >= 75:
        rarity_heat += 5.0
    if unusual_death:
        event_heat += 15.0
        rarity_heat += 10.0
    if job_tier == "premium":
        event_heat += 6.0
        rarity_heat += 8.0
    if _status_is_high(status):
        event_heat += 5.0

    contradiction_heat = _contradiction_heat(
        tags=tags,
        job=job,
        criminal_role=criminal_role,
        official_role=official_role,
        knowledge_role=knowledge_role,
        caused_victims=caused_victims,
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

    social_heat = (
        min(12.0, facts.child_count * 1.0)
        + min(10.0, (len(partner_ids) + facts.current_partner_count) * 2.0)
        + min(12.0, (len(paramour_ids) + facts.current_paramour_count) * 3.0)
        + min(12.0, facts.linked_person_count * 0.5)
        + min(8.0, facts.cross_region_move_count * 3.0)
        + (6.0 if facts.office_holding_count and facts.child_count else 0.0)
    )
    social_heat = min(35.0, social_heat)

    rare_trait_extremes = sum(1 for value in genome.values() if abs(float(value)) >= 90.0)
    if rare_trait_extremes:
        rarity_heat += min(15.0, rare_trait_extremes * 3.0)
    rarity_heat += min(24.0, late_major_events * 12.0)
    if high_importance_events:
        rarity_heat += min(12.0, high_importance_events * 4.0)
    if facts.cross_region_move_count:
        rarity_heat += min(8.0, facts.cross_region_move_count * 2.0)
    if facts.innovation_discoverer_count:
        rarity_heat += min(15.0, facts.innovation_discoverer_count * 8.0)

    job_change_count = max(
        0,
        len({title.lower() for title in job_titles if title}) - 1,
    )
    partner_change_count = len(partner_ids) + len(paramour_ids)
    reputation_direction_changes = 1 if facts.reputation_positive_count and facts.reputation_negative_count else 0
    volatility_heat += min(12.0, job_change_count * 2.0)
    volatility_heat += min(16.0, partner_change_count * 2.0)
    volatility_heat += min(15.0, reputation_direction_changes * 5.0)
    volatility_heat += min(12.0, facts.move_count * 2.0)
    if criminal_role and official_role:
        volatility_heat += 10.0
    if employment_status == "unemployed" and last_job and job:
        volatility_heat += 3.0

    legacy_heat = (
        min(16.0, facts.living_child_count * 1.0)
        + (10.0 if facts.child_count >= 4 else 0.0)
        + min(25.0, (knowledge_events + facts.innovation_discoverer_count) * 10.0)
        + min(20.0, facts.institution_founder_count * 8.0)
        + min(20.0, facts.dynasty_founder_count * 10.0)
        + min(12.0, facts.obligation_active_count * 3.0)
    )

    narrative_components = {
        "narrative_heat_events": event_heat,
        "narrative_heat_contradictions": contradiction_heat,
        "narrative_heat_consequences": consequence_heat,
        "narrative_heat_social": social_heat,
        "narrative_heat_rarity": rarity_heat,
        "narrative_heat_volatility": volatility_heat,
        "narrative_heat_legacy": legacy_heat,
    }
    narrative_total = sum(narrative_components.values()) + _stable_jitter(person_id, 5.0)
    narrative_total = _clamp(narrative_total, 0.0, 100.0)

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
    ari_total = _clamp(
        sum(value for key, value in ari_components.items() if key != "ari_suppression_obscurity_penalty")
        - ari_components["ari_suppression_obscurity_penalty"],
        0.0,
        100.0,
    )
    hidden_heat = max(0.0, narrative_total - ari_total)
    strange_life_bonus = 0.08 if contradiction_heat >= 8.0 or late_major_events else 0.0
    forgotten_bonus = 0.10 if hidden_heat >= 25.0 and ari_total < 45.0 else 0.0
    already_famous_penalty = 0.08 if ari_total >= 70.0 else 0.0
    violet_score = _clamp(
        narrative_total * 0.004
        + contradiction_heat * 0.006
        + hidden_heat * 0.005
        + strange_life_bonus
        + forgotten_bonus
        - already_famous_penalty,
        0.0,
        1.0,
    )
    component_json = {
        "score_version": SCORE_FORMULA_VERSION,
        "event_count": int(facts.event_count),
        "child_count": int(facts.child_count),
        "living_child_count": int(facts.living_child_count),
        "office_holding_count": int(facts.office_holding_count),
        "public_record_count": int(facts.public_record_count),
        "top_event_types": event_type_counts.most_common(8),
        "top_roles": role_counts.most_common(8),
        "flags": {
            "criminal_role": bool(criminal_role),
            "official_role": bool(official_role),
            "knowledge_role": bool(knowledge_role),
            "late_major_events": int(late_major_events),
            "unusual_death": bool(unusual_death),
        },
    }

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
        "recognition_bucket": _recognition_bucket(narrative_total, ari_total),
        "narrative_bucket": _narrative_bucket(narrative_total),
        "component_json": json.dumps(component_json, separators=(",", ":")),
        "updated_at": updated_at,
    }
    for key in _NARRATIVE_COLUMNS:
        score[key] = round(_clamp(narrative_components[key], 0.0, 100.0), 4)
    for key in _ARI_COLUMNS:
        score[key] = round(_clamp(ari_components[key], 0.0, 100.0), 4)
    return score


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
        "ari_suppression_obscurity_penalty": suppression,
    }


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
        for row in _fetch_dicts(conn, f"SELECT person_a_id, person_b_id FROM {table}"):
            a = _coerce_int(row.get("person_a_id"))
            b = _coerce_int(row.get("person_b_id"))
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
                payload = _json_dict(row.get("payload_json"))
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


def _contradiction_heat(
    *,
    tags: set[str],
    job: str,
    criminal_role: bool,
    official_role: bool,
    knowledge_role: bool,
    caused_victims: int,
) -> float:
    text = " | ".join(sorted(tags | {job.lower()}))

    def has(*needles: str) -> bool:
        return all(needle in text for needle in needles)

    heat = 0.0
    checks = (
        (("nurtur", "neglig"), 8.0),
        (("passive", "temper"), 8.0),
        (("humble", "inciter"), 6.0),
        (("modest", "amorous"), 6.0),
        (("witty", "oblivious"), 6.0),
        (("lawless", "diplomat"), 8.0),
        (("miser", "patron"), 6.0),
        (("artist", "murder"), 8.0),
        (("raider", "official"), 8.0),
    )
    for needles, value in checks:
        if has(*needles):
            heat += value
    if criminal_role and official_role:
        heat += 8.0
    if knowledge_role and caused_victims:
        heat += 8.0
    if "amorous" in text and "pious" in text:
        heat += 5.0
    return min(30.0, heat)


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
