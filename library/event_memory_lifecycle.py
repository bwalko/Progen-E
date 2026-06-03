"""Annual lifecycle rules for in-world event-memory records.

The factual event table remains append-only and admin-readable. This module only
ages the separate in-world memory records attached to those facts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from library import simulation_timing
from library.world_save import (
    ensure_checkpoint_schema,
    mark_event_record_lost,
    rediscover_event_record,
)


EVENT_MEMORY_LIFECYCLE_SHARDS = 16
EVENT_MEMORY_REDISCOVERY_SHARDS = 4
EVENT_MEMORY_LIFECYCLE_CANDIDATE_LIMIT = 800
LOSS_CHANCE_MULTIPLIER = 1.0
REDISCOVERY_CHANCE_MULTIPLIER = 1.0

LOSS_VISIBILITY_STATES: tuple[str, ...] = ("private_known", "rumored", "public_known")
REDISCOVERY_VISIBILITY_STATES: tuple[str, ...] = ("lost", "sealed")


@dataclass(frozen=True)
class EventMemoryLifecycleSummary:
    year: int
    candidates_reviewed: int = 0
    records_lost: int = 0
    records_rediscovered: int = 0


@dataclass(frozen=True)
class _TransitionPolicy:
    min_age: int
    chance: float


LOSS_POLICIES: dict[str, _TransitionPolicy] = {
    "household_secret": _TransitionPolicy(12, 0.18),
    "scandal_record": _TransitionPolicy(18, 0.08),
    "property_crime_record": _TransitionPolicy(18, 0.075),
    "violent_crime_record": _TransitionPolicy(22, 0.06),
    "work_record": _TransitionPolicy(35, 0.045),
    "household_memory": _TransitionPolicy(45, 0.03),
    "event_memory": _TransitionPolicy(50, 0.02),
    "lineage_memory": _TransitionPolicy(70, 0.012),
    "public_virtue_record": _TransitionPolicy(75, 0.012),
    "knowledge_record": _TransitionPolicy(90, 0.006),
    "rediscovery_record": _TransitionPolicy(45, 0.006),
    "mortuary_memory": _TransitionPolicy(100, 0.004),
    "public_chronicle": _TransitionPolicy(110, 0.003),
    "settlement_chronicle": _TransitionPolicy(120, 0.0025),
    "court_chronicle": _TransitionPolicy(140, 0.002),
    "war_chronicle": _TransitionPolicy(140, 0.002),
}
DEFAULT_LOSS_POLICY = _TransitionPolicy(60, 0.01)

REDISCOVERY_POLICIES: dict[str, _TransitionPolicy] = {
    "violent_crime_record": _TransitionPolicy(18, 0.025),
    "property_crime_record": _TransitionPolicy(20, 0.018),
    "scandal_record": _TransitionPolicy(20, 0.016),
    "public_virtue_record": _TransitionPolicy(30, 0.016),
    "knowledge_record": _TransitionPolicy(35, 0.02),
    "court_chronicle": _TransitionPolicy(60, 0.012),
    "war_chronicle": _TransitionPolicy(60, 0.012),
    "settlement_chronicle": _TransitionPolicy(60, 0.01),
    "public_chronicle": _TransitionPolicy(60, 0.008),
    "household_secret": _TransitionPolicy(12, 0.08),
    "work_record": _TransitionPolicy(18, 0.06),
    "household_memory": _TransitionPolicy(20, 0.04),
    "event_memory": _TransitionPolicy(24, 0.025),
    "lineage_memory": _TransitionPolicy(40, 0.01),
    "mortuary_memory": _TransitionPolicy(50, 0.01),
}
DEFAULT_REDISCOVERY_POLICY = _TransitionPolicy(30, 0.015)

MIN_ANY_LOSS_AGE = min(policy.min_age for policy in LOSS_POLICIES.values())
MIN_ANY_REDISCOVERY_AGE = min(
    policy.min_age for policy in REDISCOVERY_POLICIES.values()
)

REDISCOVERY_SOURCES_BY_RECORD_TYPE: dict[str, tuple[str, ...]] = {
    "lineage_memory": ("household_chest", "temple_register", "marriage_roll"),
    "mortuary_memory": ("grave_marker", "shrine_register", "mortuary_tablet"),
    "work_record": ("guild_roll", "market_account", "tax_fragment"),
    "household_memory": ("household_ledger", "midwife_note", "parish_scrap"),
    "household_secret": ("sealed_letter", "inheritance_dispute", "confessor_note"),
    "violent_crime_record": ("court_bundle", "blood-feud_verse", "witness_roll"),
    "property_crime_record": ("market_account", "guild_complaint", "court_bundle"),
    "scandal_record": ("private_letter", "song_fragment", "court_gossip_roll"),
    "public_virtue_record": ("festival_song", "temple_mural", "civic_chronicle"),
    "knowledge_record": ("scholar_copy", "workshop_tablet", "library_fragment"),
    "court_chronicle": ("court_chronicle", "seal_register", "charter_copy"),
    "war_chronicle": ("campaign_roll", "battle_song", "veteran_testimony"),
    "settlement_chronicle": ("boundary_stone", "land_grant", "settlement_annal"),
}
DEFAULT_REDISCOVERY_SOURCES = (
    "archive_survey",
    "boundary_stone",
    "temple_ledger",
    "oral_fragment",
)


def event_memory_lifecycle_annual_tick_for_save(
    save_db_path: str | Path,
    *,
    year: int,
    world: str = "default",
    candidate_limit: int = EVENT_MEMORY_LIFECYCLE_CANDIDATE_LIMIT,
    shards: int = EVENT_MEMORY_LIFECYCLE_SHARDS,
    rediscovery_shards: int = EVENT_MEMORY_REDISCOVERY_SHARDS,
) -> EventMemoryLifecycleSummary:
    """Open ``save.sqlite`` and run the annual event-memory lifecycle."""

    path = Path(save_db_path)
    if not path.exists():
        return EventMemoryLifecycleSummary(year=int(year))
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        summary = event_memory_lifecycle_annual_tick(
            conn,
            year=int(year),
            world=world,
            candidate_limit=candidate_limit,
            shards=shards,
            rediscovery_shards=rediscovery_shards,
        )
        conn.commit()
    return summary


def event_memory_lifecycle_annual_tick(
    conn: sqlite3.Connection,
    *,
    year: int,
    world: str = "default",
    candidate_limit: int = EVENT_MEMORY_LIFECYCLE_CANDIDATE_LIMIT,
    shards: int = EVENT_MEMORY_LIFECYCLE_SHARDS,
    rediscovery_shards: int = EVENT_MEMORY_REDISCOVERY_SHARDS,
    loss_chance_multiplier: float = LOSS_CHANCE_MULTIPLIER,
    rediscovery_chance_multiplier: float = REDISCOVERY_CHANCE_MULTIPLIER,
) -> EventMemoryLifecycleSummary:
    """Age old in-world event records and occasionally rediscover lost ones.

    ``conn`` is not committed here; callers that own a file connection should
    commit after receiving the summary.
    """

    sim_year = int(year)
    review_limit = max(0, int(candidate_limit))
    shard_count = max(1, int(shards))
    rediscovery_shard_count = max(1, int(rediscovery_shards))
    if review_limit <= 0:
        return EventMemoryLifecycleSummary(year=sim_year)

    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        loss_rows = _fetch_loss_candidates(
            conn,
            year=sim_year,
            shard=sim_year % shard_count,
            shards=shard_count,
            limit=review_limit,
        )
        rediscovery_rows = _fetch_rediscovery_candidates(
            conn,
            year=sim_year,
            shard=sim_year % rediscovery_shard_count,
            shards=rediscovery_shard_count,
            limit=review_limit,
        )

        lost_count = 0
        for row in loss_rows:
            chance = _loss_chance(row, sim_year) * max(0.0, float(loss_chance_multiplier))
            if _stable_fraction("loss", sim_year, row["record_id"]) >= min(1.0, chance):
                continue
            mark_event_record_lost(
                conn,
                int(row["event_id"]),
                lost_year=sim_year,
                record_key=str(row["record_key"]),
            )
            lost_count += 1

        rediscovered_count = 0
        for row in rediscovery_rows:
            chance = _rediscovery_chance(row, sim_year) * max(
                0.0, float(rediscovery_chance_multiplier)
            )
            if (
                _stable_fraction("rediscover", sim_year, row["record_id"])
                >= min(1.0, chance)
            ):
                continue
            source = _rediscovery_source(row, sim_year)
            confidence, distortion = _rediscovery_confidence_and_distortion(
                row, sim_year
            )
            rediscover_event_record(
                conn,
                int(row["event_id"]),
                rediscovered_year=sim_year,
                world=world,
                record_key=str(row["record_key"]),
                source_institution_id=source,
                preserving_settlement_id=_optional_text(row["preserving_settlement_id"]),
                preserving_region_id=_optional_text(row["preserving_region_id"]),
                confidence=confidence,
                distortion=distortion,
            )
            rediscovered_count += 1
    finally:
        conn.row_factory = old_factory

    summary = EventMemoryLifecycleSummary(
        year=sim_year,
        candidates_reviewed=len(loss_rows) + len(rediscovery_rows),
        records_lost=lost_count,
        records_rediscovered=rediscovered_count,
    )
    _record_lifecycle_gauges(summary)
    return summary


def _fetch_loss_candidates(
    conn: sqlite3.Connection,
    *,
    year: int,
    shard: int,
    shards: int,
    limit: int,
) -> list[sqlite3.Row]:
    return _fetch_candidates(
        conn,
        visibility_states=LOSS_VISIBILITY_STATES,
        max_event_year=int(year) - MIN_ANY_LOSS_AGE,
        shard=shard,
        shards=shards,
        limit=limit,
    )


def _fetch_rediscovery_candidates(
    conn: sqlite3.Connection,
    *,
    year: int,
    shard: int,
    shards: int,
    limit: int,
) -> list[sqlite3.Row]:
    return _fetch_candidates(
        conn,
        visibility_states=REDISCOVERY_VISIBILITY_STATES,
        max_event_year=int(year) - MIN_ANY_REDISCOVERY_AGE,
        shard=shard,
        shards=shards,
        limit=limit,
    )


def _fetch_candidates(
    conn: sqlite3.Connection,
    *,
    visibility_states: Iterable[str],
    max_event_year: int,
    shard: int,
    shards: int,
    limit: int,
) -> list[sqlite3.Row]:
    states = tuple(str(s) for s in visibility_states)
    state_placeholders = ",".join("?" for _ in states)
    return list(
        conn.execute(
            f"""
            SELECT
                r.record_id,
                r.event_id,
                r.record_key,
                r.record_type,
                r.visibility_state,
                r.known_since_year,
                r.lost_year,
                r.rediscovered_year,
                r.confidence,
                r.source_institution_id,
                sl.settlement_id AS preserving_settlement_id,
                rl.region_id AS preserving_region_id,
                e.sim_year,
                e.event_type,
                e.payload_json
            FROM simulation_event_records r
            JOIN simulation_events e ON e.id = r.event_id
            LEFT JOIN simulation_settlement_lookup sl
                ON sl.settlement_key = r.preserving_settlement_key
            LEFT JOIN simulation_region_lookup rl
                ON rl.region_key = r.preserving_region_key
            WHERE r.visibility_state IN ({state_placeholders})
              AND e.sim_year IS NOT NULL
              AND e.sim_year <= ?
              AND (r.record_id % ?) = ?
            ORDER BY e.sim_year, r.record_id
            LIMIT ?
            """,
            (
                *states,
                int(max_event_year),
                int(shards),
                int(shard),
                int(limit),
            ),
        )
    )


def _loss_chance(row: sqlite3.Row, year: int) -> float:
    policy = LOSS_POLICIES.get(str(row["record_type"]), DEFAULT_LOSS_POLICY)
    event_age = _event_age(row, year)
    if event_age < policy.min_age:
        return 0.0
    visibility_factor = {
        "rumored": 1.6,
        "private_known": 1.0,
        "public_known": 0.28,
    }.get(str(row["visibility_state"]), 0.0)
    if visibility_factor <= 0:
        return 0.0
    confidence = _float(row["confidence"], default=1.0)
    confidence_factor = _clamp(1.25 - (confidence * 0.5), 0.65, 1.25)
    age_factor = 1.0 + min(3.0, max(0, event_age - policy.min_age) / 120.0)
    importance_factor = _clamp(1.1 - (_event_importance(row) * 0.6), 0.35, 1.1)
    preservation_factor = 0.85 if _optional_text(row["preserving_region_id"]) else 1.0
    if _optional_text(row["source_institution_id"]):
        preservation_factor *= 0.75
    return policy.chance * visibility_factor * confidence_factor * age_factor * importance_factor * preservation_factor


def _rediscovery_chance(row: sqlite3.Row, year: int) -> float:
    policy = REDISCOVERY_POLICIES.get(
        str(row["record_type"]), DEFAULT_REDISCOVERY_POLICY
    )
    hidden_age = _hidden_age(row, year)
    if hidden_age < policy.min_age:
        return 0.0
    visibility_factor = 1.8 if str(row["visibility_state"]) == "sealed" else 1.0
    age_factor = 1.0 + min(2.5, max(0, hidden_age - policy.min_age) / 160.0)
    importance_factor = 0.6 + (_event_importance(row) * 1.2)
    preservation_factor = 1.0
    if _optional_text(row["preserving_region_id"]) or _optional_text(
        row["source_institution_id"]
    ):
        preservation_factor = 1.25
    return policy.chance * visibility_factor * age_factor * importance_factor * preservation_factor


def _event_age(row: sqlite3.Row, year: int) -> int:
    return max(0, int(year) - int(row["sim_year"]))


def _hidden_age(row: sqlite3.Row, year: int) -> int:
    hidden_since = _int_or_none(row["lost_year"])
    if hidden_since is None:
        hidden_since = _int_or_none(row["rediscovered_year"])
    if hidden_since is None:
        hidden_since = _int_or_none(row["known_since_year"])
    if hidden_since is None:
        hidden_since = int(row["sim_year"])
    return max(0, int(year) - int(hidden_since))


def _event_importance(row: sqlite3.Row) -> float:
    payload = _payload(row)
    for key in (
        "historical_importance",
        "novelty_value",
        "relief_value",
        "loss_value",
    ):
        val = _float(payload.get(key), default=None)
        if val is not None:
            return _clamp(val, 0.0, 1.0)
    record_type = str(row["record_type"])
    if record_type in {"court_chronicle", "war_chronicle", "knowledge_record"}:
        return 0.7
    if record_type in {"public_virtue_record", "violent_crime_record"}:
        return 0.55
    if record_type in {"public_chronicle", "settlement_chronicle"}:
        return 0.45
    return 0.2


def _rediscovery_source(row: sqlite3.Row, year: int) -> str:
    existing = _optional_text(row["source_institution_id"])
    if existing and str(row["visibility_state"]) == "sealed":
        return existing
    options = REDISCOVERY_SOURCES_BY_RECORD_TYPE.get(
        str(row["record_type"]), DEFAULT_REDISCOVERY_SOURCES
    )
    idx = int(_stable_fraction("source", year, row["record_id"]) * len(options))
    return options[min(idx, len(options) - 1)]


def _rediscovery_confidence_and_distortion(
    row: sqlite3.Row, year: int
) -> tuple[float, dict | None]:
    base = _float(row["confidence"], default=0.75)
    source_bonus = 0.1 if str(row["visibility_state"]) == "sealed" else 0.0
    spread = 0.72 + (_stable_fraction("confidence", year, row["record_id"]) * 0.22)
    confidence = _clamp((base * spread) + source_bonus, 0.35, 0.94)
    distortion: dict[str, object] = {}
    if confidence < 0.8:
        distortion["source_fragmentary"] = True
    if confidence < 0.72 or _stable_fraction("date", year, row["record_id"]) < 0.25:
        distortion["date_uncertain"] = True
    if confidence < 0.68 or _stable_fraction("names", year, row["record_id"]) < 0.18:
        distortion["names_partly_uncertain"] = True
    return confidence, distortion or None


def _payload(row: sqlite3.Row) -> dict:
    raw = row["payload_json"]
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_fraction(*parts: object) -> float:
    text = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(
        text.encode("utf-8"),
        digest_size=8,
        person=b"eventmem",
    ).digest()
    return int.from_bytes(digest, "big") / 2**64


def _record_lifecycle_gauges(summary: EventMemoryLifecycleSummary) -> None:
    simulation_timing.record_gauge(
        summary.year,
        "event_memory",
        "lifecycle_candidates_reviewed",
        summary.candidates_reviewed,
    )
    simulation_timing.record_gauge(
        summary.year,
        "event_memory",
        "records_lost",
        summary.records_lost,
    )
    simulation_timing.record_gauge(
        summary.year,
        "event_memory",
        "records_rediscovered",
        summary.records_rediscovered,
    )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: object, *, default: float | None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))
