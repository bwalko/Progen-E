"""Rare remarkable-archetype events layered onto existing event systems."""

from __future__ import annotations

import random
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from library import simulation_timing
from library.event_scoring import (
    EventScoringContext,
    clamp01,
    score_propensity,
    threshold_excess_value_weights,
)
from library.incident_rates import IncidentRateParams, incident_rate_for_year
from library.remarkable_archetypes import (
    RemarkableArchetype,
    RemarkableEventOption,
    choose_weighted_archetype,
    choose_weighted_event_option,
    remarkable_archetypes,
)
from library.simulation_incidents import (
    _adult_alive,
    _build_incident_scoring_facts,
    _incident_context_for_record,
    _residence_region_id,
    _settlement_pressure,
)
from library.simulation_outlaws import open_outlaw_case

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext, SimulationPersonRecord


REMARKABLE_BASE_EXPECTED_PER_YEAR = 0.02
REMARKABLE_POPULATION_PER_EXPECTED_EVENT = 100_000
REMARKABLE_MAX_EXPECTED_PER_YEAR = 2.0
REMARKABLE_MAX_EVENTS_PER_YEAR = 2
REMARKABLE_SAMPLE_CAP = 260
REMARKABLE_MAX_ARCHETYPE_ATTEMPTS = 3
REMARKABLE_RNG_STREAM = 710_191
REMARKABLE_SAMPLE_STREAM = 710_197
REMARKABLE_PROMOTION_REASON = "remarkable_archetype_event"
REMARKABLE_PROMOTION_COOLDOWN_YEARS = 10
REMARKABLE_PROMOTION_BACKGROUND_MIN = 25


@dataclass(frozen=True)
class RemarkableOpportunity:
    settlement_id: str
    region_id: str
    detailed_records: tuple["SimulationPersonRecord", ...]
    mixed_population: int
    passive_population: int
    nondetailed_population: int
    pressure: float
    tags: frozenset[str]
    weight: float

    @property
    def detailed_count(self) -> int:
        return len(self.detailed_records)

    @property
    def background_population(self) -> int:
        return max(
            0,
            int(self.mixed_population) - int(self.detailed_count),
        )


def _rate_multiplier(rate: IncidentRateParams | None, attr: str) -> float:
    value = getattr(rate, attr, 1.0) if rate is not None else 1.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 1.0


def _annual_opportunity_count(
    *,
    mixed_population: int,
    rate: IncidentRateParams,
    rng: random.Random,
) -> int:
    base_expected = min(
        REMARKABLE_MAX_EXPECTED_PER_YEAR,
        REMARKABLE_BASE_EXPECTED_PER_YEAR
        + max(0, int(mixed_population)) / REMARKABLE_POPULATION_PER_EXPECTED_EVENT,
    )
    expected = base_expected * _rate_multiplier(rate, "chance_multiplier")
    cap_multiplier = _rate_multiplier(rate, "annual_cap_multiplier")
    if expected <= 0.0 or cap_multiplier <= 0.0:
        return 0
    cap = max(
        1,
        int(REMARKABLE_MAX_EVENTS_PER_YEAR * cap_multiplier + 0.999999),
    )
    whole = int(expected)
    count = whole + (1 if rng.random() < expected - whole else 0)
    return min(cap, max(0, count))


def _safe_counts_by_settlement(ctx: "SimulationContext", kind: str) -> dict[str, int]:
    try:
        if kind == "passive":
            return ctx.passive_population_counts_by_settlement()
        if kind == "nondetailed":
            return ctx.nondetailed_population_counts_by_settlement()
    except Exception:
        return {}
    return {}


def _job_text(rec: "SimulationPersonRecord") -> str:
    return str(getattr(rec.person, "job", "") or "").strip().lower()


def _settlement_tags(
    ctx: "SimulationContext",
    facts: object,
    *,
    settlement_id: str,
    region_id: str,
    records: tuple["SimulationPersonRecord", ...],
    mixed_population: int,
    pressure: float,
) -> frozenset[str]:
    tags: set[str] = {"same_settlement"}
    st = ctx.settlements_by_id.get(settlement_id)
    level = str(getattr(st, "level", "") or "").strip().lower() if st else ""
    market_pull = float(getattr(st, "market_pull", 0.0) or 0.0) if st else 0.0
    stability = float(getattr(st, "stability", 0.5) or 0.5) if st else 0.5
    prosperity = float(getattr(st, "prosperity_pool", 1.0) or 1.0) if st else 1.0
    network = str(getattr(st, "trade_network_id", "") or "").strip() if st else ""
    founding_reason = str(getattr(st, "founding_reason", "") or "").strip().lower() if st else ""
    if int(mixed_population) >= 2_500 or level in {"town", "city"}:
        tags.update({"city_state", "urban_growth", "public_order", "civic_need"})
    if int(mixed_population) >= 10_000 or level == "city":
        tags.add("crowd")
    if pressure >= 0.75:
        tags.add("scarcity")
    if pressure >= 1.05 or stability < 0.34:
        tags.update({"social_stress", "public_crisis", "civic_need"})
    if pressure >= 1.20:
        tags.add("disaster")
    if market_pull >= 0.12 or prosperity >= 1.15:
        tags.add("market_day")
    if network and network != settlement_id:
        tags.add("trade_route")
    if "outpost" in founding_reason or "commercial" in founding_reason:
        tags.update({"trade_route", "port"})
    if settlement_id in getattr(facts, "court_settlement_ids", frozenset()):
        tags.update({"court", "office_access", "document_access"})
    if region_id in getattr(facts, "war_region_ids", frozenset()):
        tags.update({"war", "battlefield"})
    if region_id in getattr(facts, "succession_crisis_region_ids", frozenset()):
        tags.update({"succession_crisis", "office_tension"})
    if region_id in getattr(facts, "faction_tension_region_ids", frozenset()):
        tags.add("faction_tension")
    for rec in records[: min(len(records), 80)]:
        job = _job_text(rec)
        if any(token in job for token in ("priest", "shaman", "druid", "oracle")):
            tags.update({"temple", "ritual_site", "archive"})
        if any(token in job for token in ("scribe", "scholar", "judge")):
            tags.add("archive")
        if any(token in job for token in ("smith", "weaver", "potter", "carpenter", "artisan", "craft")):
            tags.add("workshop")
        if any(token in job for token in ("merchant", "trader", "sailor", "ship", "market")):
            tags.update({"market_day", "trade_route"})
    return frozenset(tags)


def _opportunities(
    ctx: "SimulationContext",
    year: int,
    facts: object,
) -> tuple[RemarkableOpportunity, ...]:
    detailed_by_settlement = ctx.current_people_by_settlement()
    passive_by_settlement = _safe_counts_by_settlement(ctx, "passive")
    nondetailed_by_settlement = _safe_counts_by_settlement(ctx, "nondetailed")
    # The context-level passive helper intentionally includes non-detailed
    # SQLite directory people for most census callers. Keep these surfaces
    # separate here so rare-opportunity math does not double-count them.
    if passive_by_settlement and nondetailed_by_settlement:
        passive_by_settlement = {
            sid: max(0, int(count) - int(nondetailed_by_settlement.get(sid, 0)))
            for sid, count in passive_by_settlement.items()
        }
    settlement_ids = (
        set(detailed_by_settlement)
        | set(passive_by_settlement)
        | set(nondetailed_by_settlement)
        | {
            sid
            for sid, st in getattr(ctx, "settlements_by_id", {}).items()
            if str(getattr(st, "status", "active") or "").strip().lower() == "active"
        }
    )
    out: list[RemarkableOpportunity] = []
    for sid in sorted(settlement_ids):
        st = ctx.settlements_by_id.get(sid)
        records = tuple(detailed_by_settlement.get(sid, ()))
        region_id = (
            str(getattr(st, "region_id", "") or "").strip()
            if st is not None
            else str(sid).split(":", 1)[0]
        )
        passive = max(0, int(passive_by_settlement.get(sid, 0)))
        nondetailed = max(0, int(nondetailed_by_settlement.get(sid, 0)))
        mixed = len(records) + passive + nondetailed
        if mixed <= 0:
            continue
        pressure = _settlement_pressure(ctx, int(year), sid)
        tags = _settlement_tags(
            ctx,
            facts,
            settlement_id=sid,
            region_id=region_id,
            records=records,
            mixed_population=mixed,
            pressure=pressure,
        )
        signal_tags = tags.intersection(
            {
                "archive",
                "battlefield",
                "city_state",
                "court",
                "market_day",
                "port",
                "public_crisis",
                "ritual_site",
                "temple",
                "trade_route",
                "workshop",
            }
        )
        weight = max(1.0, float(mixed)) * (1.0 + min(1.2, len(signal_tags) * 0.12))
        out.append(
            RemarkableOpportunity(
                settlement_id=sid,
                region_id=region_id,
                detailed_records=records,
                mixed_population=mixed,
                passive_population=passive,
                nondetailed_population=nondetailed,
                pressure=float(pressure),
                tags=tags,
                weight=weight,
            )
        )
    return tuple(out)


def _weighted_opportunity(
    opportunities: tuple[RemarkableOpportunity, ...],
    rng: random.Random,
) -> RemarkableOpportunity | None:
    if not opportunities:
        return None
    weights = [max(0.0, opp.weight) for opp in opportunities]
    if sum(weights) <= 0.0:
        return None
    return rng.choices(list(opportunities), weights=weights, k=1)[0]


def _event_family_for_context(option: RemarkableEventOption) -> str:
    event_type = option.event_type
    if event_type.startswith("city_state_"):
        return "civic_politics"
    return event_type


def _candidate_context(
    ctx: "SimulationContext",
    facts: object,
    *,
    year: int,
    opportunity: RemarkableOpportunity,
    rec: "SimulationPersonRecord",
    option: RemarkableEventOption,
) -> EventScoringContext:
    base = _incident_context_for_record(
        ctx,
        facts,
        year=int(year),
        settlement_id=opportunity.settlement_id,
        rec=rec,
        event_family=_event_family_for_context(option),
        pressure=opportunity.pressure,
        adults_count=max(1, len(opportunity.detailed_records)),
    )
    return EventScoringContext(
        role_tags=base.role_tags,
        pressure_tags=base.pressure_tags | opportunity.tags,
        opportunity_tags=base.opportunity_tags | opportunity.tags,
        resource_pressure=base.resource_pressure,
        crowding=base.crowding,
        prosperity=base.prosperity,
        witness_count=base.witness_count,
    )


def _adult_candidates(
    ctx: "SimulationContext",
    *,
    year: int,
    opportunity: RemarkableOpportunity,
    archetype: RemarkableArchetype,
) -> list["SimulationPersonRecord"]:
    adults = [
        rec
        for rec in opportunity.detailed_records
        if _adult_alive(rec, int(year))
    ]
    return ctx.decision_sample_records(
        adults,
        year=int(year),
        scope=f"settlement:{opportunity.settlement_id}:remarkable:{archetype.key}",
        stream=REMARKABLE_SAMPLE_STREAM,
        cap=REMARKABLE_SAMPLE_CAP,
    )


def _score_records(
    ctx: "SimulationContext",
    facts: object,
    *,
    year: int,
    opportunity: RemarkableOpportunity,
    archetype: RemarkableArchetype,
    option: RemarkableEventOption,
    records: list["SimulationPersonRecord"],
) -> tuple[list["SimulationPersonRecord"], list[float]]:
    spec = archetype.propensity_spec()
    scores: list[float] = []
    for rec in records:
        context = _candidate_context(
            ctx,
            facts,
            year=year,
            opportunity=opportunity,
            rec=rec,
            option=option,
        )
        scores.append(clamp01(score_propensity(rec, spec, context=context)))
    return records, scores


def _choose_scored_candidate(
    ctx: "SimulationContext",
    facts: object,
    *,
    year: int,
    opportunity: RemarkableOpportunity,
    archetype: RemarkableArchetype,
    option: RemarkableEventOption,
    rng: random.Random,
) -> tuple["SimulationPersonRecord", float, str] | None:
    records = _adult_candidates(
        ctx,
        year=year,
        opportunity=opportunity,
        archetype=archetype,
    )
    if not records:
        return None
    records, scores = _score_records(
        ctx,
        facts,
        year=year,
        opportunity=opportunity,
        archetype=archetype,
        option=option,
        records=records,
    )
    threshold = float(archetype.minimum_score)
    eligible: list[tuple["SimulationPersonRecord", float]] = [
        (rec, score) for rec, score in zip(records, scores) if score >= threshold
    ]
    if not eligible:
        return None
    weights = threshold_excess_value_weights(
        [score for _rec, score in eligible],
        threshold,
        exponent=2.0,
        floor=0.001,
    )
    chosen = rng.choices(eligible, weights=weights, k=1)[0]
    return chosen[0], chosen[1], "detailed_sample"


def _pending_recent_archetype_promotion(
    ctx: "SimulationContext", year: int
) -> bool:
    earliest = int(year) - REMARKABLE_PROMOTION_COOLDOWN_YEARS + 1
    for sim_year, event_type, payload in getattr(ctx, "_pending_simulation_events", []):
        try:
            event_year = int(sim_year)
        except (TypeError, ValueError):
            event_year = int(year)
        if event_year < earliest:
            continue
        if event_type not in {"passive_person_promoted", "nondetailed_person_promoted"}:
            continue
        if str(payload.get("reason") or "").strip() == REMARKABLE_PROMOTION_REASON:
            return True
    return False


def _saved_recent_archetype_promotion(ctx: "SimulationContext", year: int) -> bool:
    path = Path(getattr(ctx, "save_db_path", ""))
    if not path.is_file():
        return False
    earliest = int(year) - REMARKABLE_PROMOTION_COOLDOWN_YEARS + 1
    try:
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'simulation_events'
                """
            ).fetchone()
            if row is None:
                return False
            found = conn.execute(
                """
                SELECT 1
                FROM simulation_events
                WHERE sim_year >= ?
                  AND event_type IN ('passive_person_promoted', 'nondetailed_person_promoted')
                  AND payload_json LIKE ?
                LIMIT 1
                """,
                (earliest, f"%{REMARKABLE_PROMOTION_REASON}%"),
            ).fetchone()
            return found is not None
    except sqlite3.Error:
        return False


def _recent_archetype_promotion_exists(ctx: "SimulationContext", year: int) -> bool:
    return _pending_recent_archetype_promotion(
        ctx, int(year)
    ) or _saved_recent_archetype_promotion(ctx, int(year))


def _passive_record_for_opportunity(
    ctx: "SimulationContext",
    opportunity: RemarkableOpportunity,
    year: int,
) -> int | None:
    for passive_id, prec in sorted(getattr(ctx, "passive_people", {}).items()):
        person = prec.person
        sid = str(
            getattr(person, "current_settlement_id", None)
            or getattr(person, "birthplace_settlement_id", None)
            or ""
        ).strip()
        rid = str(getattr(person, "birthplace_region_id", "") or "").strip()
        if sid and sid != opportunity.settlement_id:
            continue
        if not sid and rid and rid != opportunity.region_id:
            continue
        deathyear = getattr(person, "deathyear", None)
        if deathyear is not None and int(deathyear) <= int(year):
            continue
        if int(year) - int(getattr(person, "birthyear", year)) < 16:
            continue
        return int(passive_id)
    return None


def _promotion_candidate(
    ctx: "SimulationContext",
    facts: object,
    *,
    year: int,
    opportunity: RemarkableOpportunity,
    archetype: RemarkableArchetype,
    option: RemarkableEventOption,
) -> tuple["SimulationPersonRecord", float, str] | None:
    if not archetype.promotion_allowed:
        return None
    if opportunity.background_population < REMARKABLE_PROMOTION_BACKGROUND_MIN:
        return None
    if _recent_archetype_promotion_exists(ctx, int(year)):
        return None
    source = {
        "archetype_key": archetype.key,
        "archetype_bucket": archetype.bucket,
        "event_type": option.event_type,
        "incident_kind": option.incident_kind,
        "settlement_id": opportunity.settlement_id,
        "region_id": opportunity.region_id,
        "mixed_population": opportunity.mixed_population,
    }
    promoted: list["SimulationPersonRecord"] = []
    if opportunity.nondetailed_population > 0:
        try:
            promoted = ctx.promote_nondetailed_people(
                year=int(year),
                reason=REMARKABLE_PROMOTION_REASON,
                settlement_id=opportunity.settlement_id,
                max_age=70,
                preferred_min_age=22,
                preferred_max_age=55,
                limit=1,
                source=source,
            )
        except Exception:
            promoted = []
    if not promoted:
        passive_id = _passive_record_for_opportunity(ctx, opportunity, int(year))
        if passive_id is not None:
            try:
                promoted = [
                    ctx.promote_passive_person(
                        passive_id,
                        year=int(year),
                        reason=REMARKABLE_PROMOTION_REASON,
                        source=source,
                    )
                ]
            except Exception:
                promoted = []
    if not promoted:
        return None
    rec = promoted[0]
    records, scores = _score_records(
        ctx,
        facts,
        year=year,
        opportunity=replace(
            opportunity,
            detailed_records=tuple(list(opportunity.detailed_records) + [rec]),
        ),
        archetype=archetype,
        option=option,
        records=[rec],
    )
    score = scores[0] if scores else 0.0
    if score < float(archetype.minimum_score) * 0.65:
        return None
    return records[0], score, "background_promotion"


def _person_name(rec: "SimulationPersonRecord") -> str:
    return " ".join(
        part
        for part in (
            str(getattr(rec.person, "first_name", "") or "").strip(),
            str(getattr(rec.person, "last_name", "") or "").strip(),
        )
        if part
    ) or f"person {rec.person_id}"


def _genome_signals(rec: "SimulationPersonRecord", archetype: RemarkableArchetype) -> dict[str, float]:
    genome = getattr(rec.person, "genome", {}) or {}
    out: dict[str, float] = {}
    for factor in archetype.trait_factors:
        if factor.trait in genome:
            try:
                out[factor.trait] = round(float(genome[factor.trait]), 5)
            except (TypeError, ValueError):
                continue
    return out


def _witness_ids(
    records: tuple["SimulationPersonRecord", ...],
    actor_id: int,
    rng: random.Random,
    limit: int = 3,
) -> list[int]:
    options = [int(rec.person_id) for rec in records if int(rec.person_id) != int(actor_id)]
    if not options:
        return []
    count = min(int(limit), len(options))
    return sorted(rng.sample(options, count))


def _related_record(
    records: tuple["SimulationPersonRecord", ...],
    actor_id: int,
    rng: random.Random,
) -> "SimulationPersonRecord | None":
    options = [rec for rec in records if int(rec.person_id) != int(actor_id)]
    if not options:
        return None
    return rng.choice(options)


def _importance(
    archetype: RemarkableArchetype,
    score: float,
    opportunity: RemarkableOpportunity,
) -> float:
    lo = min(float(archetype.importance_min), float(archetype.importance_max))
    hi = max(float(archetype.importance_min), float(archetype.importance_max))
    pop_bonus = min(0.12, max(0.0, opportunity.mixed_population / 50_000.0))
    scaled = lo + (hi - lo) * clamp01((float(score) - archetype.minimum_score) / 0.55)
    return round(clamp01(scaled + pop_bonus), 5)


def _meta_payload(
    ctx: "SimulationContext",
    *,
    year: int,
    archetype: RemarkableArchetype,
    option: RemarkableEventOption,
    opportunity: RemarkableOpportunity,
    actor: "SimulationPersonRecord",
    score: float,
    candidate_basis: str,
    importance: float,
) -> dict[str, object]:
    historical_year = ctx.get_historical_year(int(year))
    return {
        "year": int(year),
        "historical_year": int(historical_year),
        "event_type": option.event_type,
        "incident_kind": option.incident_kind,
        "person_id": int(actor.person_id),
        "actor_person_id": int(actor.person_id),
        "actor_name": _person_name(actor),
        "settlement_id": opportunity.settlement_id,
        "region_id": opportunity.region_id,
        "archetype_key": archetype.key,
        "archetype_bucket": archetype.bucket,
        "archetype_display_name": archetype.display_name,
        "archetype_score": round(float(score), 5),
        "archetype_share_weight": round(float(archetype.share_weight), 5),
        "candidate_basis": candidate_basis,
        "historical_importance": importance,
        "opportunity_context": {
            "settlement_id": opportunity.settlement_id,
            "region_id": opportunity.region_id,
            "mixed_population": int(opportunity.mixed_population),
            "detailed_population": int(opportunity.detailed_count),
            "passive_population": int(opportunity.passive_population),
            "nondetailed_population": int(opportunity.nondetailed_population),
            "resource_pressure": round(float(opportunity.pressure), 5),
            "tags": sorted(opportunity.tags),
        },
        "genome_signals": _genome_signals(actor, archetype),
    }


def _public_reputation_consequence(person_id: int, axis: str, after: str) -> dict[str, object]:
    field = "leader_tendency" if axis == "leadership" else "status_tendency"
    return {
        "person_id": int(person_id),
        f"{field}_before": "low",
        f"{field}_after": after,
    }


def _reputation_mark(person_id: int, axis: str, after: str, strength: float) -> dict[str, object]:
    return {
        "person_id": int(person_id),
        "reputation_axis": axis,
        "reputation_before": "low",
        "reputation_after": after,
        "direction": "positive",
        "mark_strength": round(clamp01(strength), 5),
        "source_role": "remarkable_archetype",
    }


def _knowledge_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    patron: "SimulationPersonRecord | None",
    option: RemarkableEventOption,
    importance: float,
    witnesses: list[int],
) -> dict[str, object]:
    domain = option.domain or "knowledge"
    novelty = round(clamp01(0.18 + float(importance) * 0.55), 5)
    payload = {
        **base,
        "creator_person_id": int(actor.person_id),
        "knowledge_domain": domain,
        "novelty_value": novelty,
        "motive": "remarkable_archetype",
        "witness_person_ids": witnesses,
        "consequences": {
            "knowledge_state": {
                "domain": domain,
                "state_delta": round(max(0.01, novelty * 0.35), 5),
                "source_role": "remarkable_archetype",
            },
            "public_reputation": _public_reputation_consequence(
                int(actor.person_id),
                "status",
                "middle-high",
            ),
            "institutions": [
                {
                    "institution_type": "school" if domain in {"scholarship", "law", "records"} else "guild",
                    "focus_domain": domain,
                    "founder_person_id": int(actor.person_id),
                    "strength_delta": round(max(0.02, novelty * 0.18), 5),
                    "source_role": "remarkable_archetype",
                }
            ],
        },
    }
    if patron is not None:
        payload["patron_person_id"] = int(patron.person_id)
        payload["consequences"]["patronage"] = {
            "patron_person_id": int(patron.person_id),
            "creator_person_id": int(actor.person_id),
            "source_role": "remarkable_archetype",
        }
    return payload


def _public_virtue_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    beneficiary: "SimulationPersonRecord | None",
    importance: float,
    witnesses: list[int],
) -> dict[str, object]:
    target = beneficiary or actor
    relief = round(clamp01(0.08 + float(importance) * 0.22), 5)
    return {
        **base,
        "benefactor_person_id": int(actor.person_id),
        "beneficiary_person_id": int(target.person_id),
        "relief_value": relief,
        "motive": "remarkable_archetype",
        "witness_person_ids": witnesses,
        "consequences": {
            "relief": {
                "relief_value": relief,
                "source_role": "remarkable_archetype",
            },
            "public_reputation": _public_reputation_consequence(
                int(actor.person_id),
                "leadership",
                "medium",
            ),
        },
    }


def _status_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    importance: float,
) -> dict[str, object]:
    old_standing = getattr(actor.person, "social_standing_01", None)
    try:
        old = float(old_standing) if old_standing is not None else 0.35
    except (TypeError, ValueError):
        old = 0.35
    new = min(1.0, old + 0.08 + float(importance) * 0.18)
    return {
        **base,
        "reason": "remarkable_archetype",
        "previous_social_standing_01": round(old, 5),
        "new_social_standing_01": round(new, 5),
        "new_job": getattr(actor.person, "job", None) or base.get("archetype_display_name"),
        "consequences": {
            "reputation_marks": [
                _reputation_mark(
                    int(actor.person_id),
                    "status",
                    "middle-high",
                    0.18 + float(importance) * 0.35,
                )
            ]
        },
    }


def _patronage_payload(
    base: dict[str, object],
    *,
    patron: "SimulationPersonRecord",
    client: "SimulationPersonRecord",
    importance: float,
) -> dict[str, object]:
    strength = round(clamp01(0.18 + float(importance) * 0.45), 5)
    return {
        **base,
        "person_id": int(client.person_id),
        "patron_person_id": int(patron.person_id),
        "client_person_id": int(client.person_id),
        "strength_01": strength,
        "consequences": {
            "obligations": [
                {
                    "obligation_key": f"remarkable_patronage:{client.person_id}:{patron.person_id}",
                    "obligation_type": "patronage_debt",
                    "owed_by_person_id": int(client.person_id),
                    "owed_to_person_id": int(patron.person_id),
                    "strength": strength,
                    "expected_duration_years": 20,
                    "source_role": "remarkable_archetype",
                }
            ],
            "reputation_marks": [
                _reputation_mark(
                    int(patron.person_id),
                    "leadership",
                    "medium",
                    strength,
                )
            ],
        },
    }


def _investment_payload(
    base: dict[str, object],
    *,
    ctx: "SimulationContext",
    actor: "SimulationPersonRecord",
    opportunity: RemarkableOpportunity,
    importance: float,
) -> dict[str, object]:
    delta = round(clamp01(0.03 + float(importance) * 0.10), 5)
    st = ctx.settlements_by_id.get(opportunity.settlement_id)
    if st is not None:
        ctx.settlements_by_id[opportunity.settlement_id] = replace(
            st,
            prosperity_pool=float(getattr(st, "prosperity_pool", 1.0) or 1.0) + delta,
            stability=min(1.0, float(getattr(st, "stability", 0.5) or 0.5) + delta * 0.25),
        )
    return {
        **base,
        "investment_kind": "remarkable_patronage",
        "investment_value": round(0.20 + float(importance) * 0.70, 5),
        "prosperity_pool_delta": delta,
        "consequences": {
            "reputation_marks": [
                _reputation_mark(
                    int(actor.person_id),
                    "leadership",
                    "medium",
                    0.20 + float(importance) * 0.30,
                )
            ]
        },
    }


def _conflict_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    target: "SimulationPersonRecord | None",
    importance: float,
) -> dict[str, object]:
    payload = {
        **base,
        "motive": "remarkable_archetype",
        "consequences": {
            "faction_memory": [
                {
                    "memory_type": "remarkable_conflict",
                    "principal_person_id": int(actor.person_id),
                    "opposing_person_id": int(target.person_id) if target else None,
                    "faction_a_key": f"person:{actor.person_id}",
                    "faction_b_key": f"person:{target.person_id}" if target else "",
                    "polarity": "negative",
                    "strength": round(clamp01(0.18 + float(importance) * 0.45), 5),
                    "expected_duration_years": 18,
                    "source_role": "remarkable_archetype",
                }
            ]
        },
    }
    if target is not None:
        payload["target_person_id"] = int(target.person_id)
    return payload


def _religious_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    target: "SimulationPersonRecord | None",
    option: RemarkableEventOption,
    importance: float,
) -> dict[str, object]:
    payload = _conflict_payload(
        base,
        actor=actor,
        target=target,
        importance=importance,
    )
    payload["doctrine_domain"] = option.domain or "doctrine"
    if option.incident_kind == "cult_founding":
        payload["consequences"]["institutions"] = [
            {
                "institution_type": "doctrine",
                "focus_domain": option.domain or "doctrine",
                "founder_person_id": int(actor.person_id),
                "strength_delta": round(clamp01(0.04 + float(importance) * 0.12), 5),
                "source_role": "remarkable_archetype",
            }
        ]
    return payload


def _city_state_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    option: RemarkableEventOption,
    importance: float,
) -> dict[str, object]:
    domain = option.domain or "civic"
    return {
        **base,
        "sponsor_person_id": int(actor.person_id),
        "city_state_action": option.incident_kind,
        "knowledge_domain": domain,
        "consequences": {
            "knowledge_state": {
                "domain": domain,
                "state_delta": round(clamp01(0.01 + float(importance) * 0.08), 5),
                "source_role": "remarkable_archetype",
            },
            "institutions": [
                {
                    "institution_type": "civic",
                    "focus_domain": domain,
                    "founder_person_id": int(actor.person_id),
                    "strength_delta": round(clamp01(0.03 + float(importance) * 0.12), 5),
                    "source_role": "remarkable_archetype",
                }
            ],
        },
    }


def _private_life_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    target: "SimulationPersonRecord | None",
    importance: float,
) -> dict[str, object]:
    payload = {
        **base,
        "source_person_id": int(actor.person_id),
        "motive": "remarkable_archetype",
        "confidence": round(clamp01(0.35 + float(importance) * 0.35), 5),
    }
    if target is not None:
        payload["target_person_id"] = int(target.person_id)
    return payload


def _property_crime_payload(
    base: dict[str, object],
    *,
    actor: "SimulationPersonRecord",
    target: "SimulationPersonRecord | None",
    importance: float,
) -> dict[str, object] | None:
    if target is None:
        return None
    loss = round(clamp01(0.05 + float(importance) * 0.30), 5)
    return {
        **base,
        "perpetrator_person_id": int(actor.person_id),
        "target_person_id": int(target.person_id),
        "loss_value": loss,
        "motive": "remarkable_archetype",
        "consequences": {
            "faction_memory": [
                {
                    "memory_type": "remarkable_property_grievance",
                    "principal_person_id": int(target.person_id),
                    "opposing_person_id": int(actor.person_id),
                    "faction_a_key": f"person:{target.person_id}",
                    "faction_b_key": f"person:{actor.person_id}",
                    "polarity": "negative",
                    "strength": round(clamp01(0.12 + loss), 5),
                    "expected_duration_years": 10,
                    "source_role": "remarkable_archetype",
                }
            ]
        },
    }


def _update_pending_outlaw_payload(
    ctx: "SimulationContext",
    *,
    case_key: str,
    additions: dict[str, object],
) -> None:
    pending = getattr(ctx, "_pending_simulation_events", [])
    for idx in range(len(pending) - 1, -1, -1):
        sim_year, event_type, payload = pending[idx]
        if event_type != "outlaw_case_opened":
            continue
        if str(payload.get("case_key") or "") != str(case_key):
            continue
        merged = dict(payload)
        merged.update(additions)
        pending[idx] = (sim_year, event_type, merged)
        return


def _emit_outlaw_case(
    ctx: "SimulationContext",
    *,
    year: int,
    actor: "SimulationPersonRecord",
    option: RemarkableEventOption,
    base: dict[str, object],
    importance: float,
) -> bool:
    case = open_outlaw_case(
        ctx,
        year=int(year),
        accused=actor,
        offense_type="property_crime",
        offense_kind=option.incident_kind or "extortion",
        severity_01=importance,
        knownness_01=clamp01(0.45 + importance * 0.35),
        source_event_key=f"remarkable_archetype:{year}:{actor.person_id}:{option.incident_kind}",
        details={
            "source_role": "remarkable_archetype",
            "archetype_key": base.get("archetype_key"),
            "archetype_bucket": base.get("archetype_bucket"),
        },
    )
    if case is None:
        return False
    _update_pending_outlaw_payload(
        ctx,
        case_key=case.case_key,
        additions={k: v for k, v in base.items() if k != "event_type"},
    )
    return True


def _emit_archetype_event(
    ctx: "SimulationContext",
    *,
    year: int,
    facts: object,
    archetype: RemarkableArchetype,
    option: RemarkableEventOption,
    opportunity: RemarkableOpportunity,
    actor: "SimulationPersonRecord",
    score: float,
    candidate_basis: str,
    rng: random.Random,
) -> bool:
    actor_region = _residence_region_id(ctx, actor)
    if actor_region and not opportunity.region_id:
        opportunity = replace(opportunity, region_id=actor_region)
    importance = _importance(archetype, score, opportunity)
    base = _meta_payload(
        ctx,
        year=int(year),
        archetype=archetype,
        option=option,
        opportunity=opportunity,
        actor=actor,
        score=score,
        candidate_basis=candidate_basis,
        importance=importance,
    )
    witnesses = _witness_ids(opportunity.detailed_records, int(actor.person_id), rng)
    related = _related_record(opportunity.detailed_records, int(actor.person_id), rng)
    event_type = option.event_type
    payload: dict[str, object] | None
    if event_type == "outlaw_case_opened":
        return _emit_outlaw_case(
            ctx,
            year=int(year),
            actor=actor,
            option=option,
            base=base,
            importance=importance,
        )
    if event_type == "knowledge_culture":
        payload = _knowledge_payload(
            base,
            actor=actor,
            patron=related,
            option=option,
            importance=importance,
            witnesses=witnesses,
        )
    elif event_type == "public_virtue":
        payload = _public_virtue_payload(
            base,
            actor=actor,
            beneficiary=related,
            importance=importance,
            witnesses=witnesses,
        )
    elif event_type in {"status_rise", "elite_job_promoted", "guild_admission"}:
        payload = _status_payload(base, actor=actor, importance=importance)
    elif event_type == "patronage_granted":
        if related is None:
            return False
        payload = _patronage_payload(
            base,
            patron=actor,
            client=related,
            importance=importance,
        )
    elif event_type == "elite_household_investment":
        payload = _investment_payload(
            base,
            ctx=ctx,
            actor=actor,
            opportunity=opportunity,
            importance=importance,
        )
    elif event_type == "religious_cultural_conflict":
        payload = _religious_payload(
            base,
            actor=actor,
            target=related,
            option=option,
            importance=importance,
        )
    elif event_type == "political_crime":
        payload = _conflict_payload(
            base,
            actor=actor,
            target=related,
            importance=importance,
        )
    elif event_type == "private_life":
        payload = _private_life_payload(
            base,
            actor=actor,
            target=related,
            importance=importance,
        )
    elif event_type == "property_crime":
        payload = _property_crime_payload(
            base,
            actor=actor,
            target=related,
            importance=importance,
        )
    elif event_type.startswith("city_state_"):
        payload = _city_state_payload(
            base,
            actor=actor,
            option=option,
            importance=importance,
        )
    else:
        payload = base
    if payload is None:
        return False
    ctx._record_simulation_event(int(year), event_type, payload)
    return True


def simulation_remarkable_archetypes_annual_tick(
    ctx: "SimulationContext", year: int
) -> None:
    """Generate very rare events around historically visible archetype patterns."""
    y = int(year)
    prof = simulation_timing.active_for_year(y)
    tpc = time.perf_counter
    start = tpc() if prof else None
    entries = remarkable_archetypes(db_path=ctx.db_path)
    if not entries:
        return
    rng = random.Random(
        y * REMARKABLE_RNG_STREAM + int(getattr(ctx, "placename_rng_salt", 0)) + 2311
    )
    facts = _build_incident_scoring_facts(ctx, y)
    opportunities = _opportunities(ctx, y, facts)
    mixed_population = sum(opp.mixed_population for opp in opportunities)
    historical_year = ctx.get_historical_year(y)
    rate = incident_rate_for_year(
        db_path=ctx.db_path,
        world=ctx.world,
        incident_key="remarkable_archetype",
        historical_year=historical_year,
    )
    event_limit = _annual_opportunity_count(
        mixed_population=mixed_population,
        rate=rate,
        rng=rng,
    )
    emitted = 0
    for _ in range(event_limit):
        opportunity = _weighted_opportunity(opportunities, rng)
        if opportunity is None:
            break
        for _attempt in range(REMARKABLE_MAX_ARCHETYPE_ATTEMPTS):
            archetype = choose_weighted_archetype(entries, rng)
            if archetype is None:
                break
            option = choose_weighted_event_option(archetype, rng)
            if option is None:
                continue
            chosen = _choose_scored_candidate(
                ctx,
                facts,
                year=y,
                opportunity=opportunity,
                archetype=archetype,
                option=option,
                rng=rng,
            )
            if chosen is None:
                chosen = _promotion_candidate(
                    ctx,
                    facts,
                    year=y,
                    opportunity=opportunity,
                    archetype=archetype,
                    option=option,
                )
            if chosen is None:
                continue
            actor, score, basis = chosen
            if _emit_archetype_event(
                ctx,
                year=y,
                facts=facts,
                archetype=archetype,
                option=option,
                opportunity=opportunity,
                actor=actor,
                score=score,
                candidate_basis=basis,
                rng=rng,
            ):
                emitted += 1
                break
    if prof:
        if start is not None:
            simulation_timing.accumulate("remarkable_archetypes.generate", tpc() - start)
        simulation_timing.record_gauge(
            y,
            "remarkable_archetypes",
            "events",
            emitted,
        )
