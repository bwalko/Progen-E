"""Outlawry, pursuit, and non-settlement refuge behavior."""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from library.mind_body import work_trait_values

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext, SimulationPersonRecord


OUTLAW_STATUS_WANTED = "wanted"
OUTLAW_STATUS_FUGITIVE = "fugitive"
OUTLAW_STATUS_CLEARED = "cleared"
OUTLAW_STATUS_PUNISHED = "punished"
OUTLAW_STATUS_RETURNED = "returned"
OUTLAW_CAREER_BLOCKING_STATUSES = frozenset(
    {OUTLAW_STATUS_WANTED, OUTLAW_STATUS_FUGITIVE, OUTLAW_STATUS_PUNISHED}
)

OUTLAW_RNG_STREAM = 1_740_331
PROPERTY_OUTLAW_MIN_SEVERITY = 0.36


@dataclass(frozen=True)
class SimulationOutlawCase:
    """Runtime wanted/outlaw case persisted by ``world_save``."""

    case_key: str
    accused_person_id: int
    offense_type: str
    offense_kind: str
    status: str = "active"
    source_event_id: int | None = None
    source_event_key: str = ""
    victim_person_id: int | None = None
    target_person_id: int | None = None
    severity_01: float = 0.0
    knownness_01: float = 0.0
    pursuit_pressure_01: float = 0.0
    buyoff_power_01: float = 0.0
    start_year: int | None = None
    last_seen_year: int | None = None
    expected_forget_year: int | None = None
    resolved_year: int | None = None
    resolution: str | None = None
    region_id: str | None = None
    settlement_id: str | None = None
    refuge_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SimulationOutlawRefuge:
    """A non-settlement shelter/base used by fugitives."""

    refuge_id: str
    region_id: str
    near_settlement_id: str | None = None
    status: str = "active"
    founded_year: int | None = None
    discovered_year: int | None = None
    abandoned_year: int | None = None
    band_size: int = 1
    concealment_01: float = 0.5
    support_01: float = 0.0
    last_activity_year: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def is_outlaw_absent(person: object) -> bool:
    return str(getattr(person, "outlaw_status", "") or "").strip().lower() == OUTLAW_STATUS_FUGITIVE


def outlaw_blocks_normal_career(person: object) -> bool:
    status = str(getattr(person, "outlaw_status", "") or "").strip().lower()
    return status in OUTLAW_CAREER_BLOCKING_STATUSES


def normalize_outlaw_labor_state(
    ctx: "SimulationContext",
    rec: "SimulationPersonRecord",
    year: int,
) -> bool:
    """Keep active/punished outlaws out of ordinary employment state."""
    status = str(getattr(rec.person, "outlaw_status", "") or "").strip().lower()
    if status not in OUTLAW_CAREER_BLOCKING_STATUSES:
        return False
    last_job = rec.person.last_job or rec.person.job
    updates: dict[str, Any] = {
        "job": None,
        "job_assigned_year": None,
        "job_era": None,
        "job_tier": None,
        "last_job": last_job,
        "host_person_id": None,
        "employer_person_id": None,
    }
    if rec.person.job:
        updates["job_lost_year"] = int(year)
    if status == OUTLAW_STATUS_FUGITIVE:
        updates.update(
            {
                "current_settlement_id": None,
                "employment_status": "outlaw",
                "job_market_type": "criminal",
                "housing_status": "outlaw_refuge",
                "household_role": "fugitive",
            }
        )
    else:
        updates.update(
            {
                "employment_status": "unemployed",
                "job_market_type": "none",
            }
        )
    new_person = replace(rec.person, **updates)
    if new_person == rec.person:
        return False
    rec.person = new_person
    if status == OUTLAW_STATUS_FUGITIVE:
        ctx.invalidate_alive_census_cache()
    ctx.invalidate_annual_indexes()
    return True


def active_outlaw_case_for_person(
    ctx: "SimulationContext", person_id: int
) -> SimulationOutlawCase | None:
    for case in (getattr(ctx, "outlaw_cases", {}) or {}).values():
        if (
            int(case.accused_person_id) == int(person_id)
            and str(case.status or "").strip().lower() == "active"
        ):
            return case
    return None


def _person_residence(ctx: "SimulationContext", rec: "SimulationPersonRecord") -> tuple[str | None, str | None]:
    sid = (
        rec.person.current_settlement_id
        or rec.person.birthplace_settlement_id
        or ""
    ).strip()
    rid = None
    if sid:
        st = ctx.settlements_by_id.get(sid)
        if st is not None:
            rid = (st.region_id or "").strip() or None
    if rid is None:
        rid = (rec.person.birthplace_region_id or "").strip() or None
    return (sid or None), rid


def _patronage_strength(ctx: "SimulationContext", person_id: int) -> float:
    best = 0.0
    for tie in (getattr(ctx, "patronage_ties", {}) or {}).values():
        if str(getattr(tie, "status", "active") or "active").strip().lower() != "active":
            continue
        if int(getattr(tie, "client_person_id", 0) or 0) == int(person_id):
            best = max(best, float(getattr(tie, "strength_01", 0.0) or 0.0))
    return clamp01(best)


def _office_power(ctx: "SimulationContext", person_id: int) -> float:
    for seat in (getattr(ctx, "gov_office_seats", {}) or {}).values():
        if int(getattr(seat, "holder_person_id", 0) or 0) == int(person_id):
            return 0.18
    return 0.0


def _buyoff_power(ctx: "SimulationContext", rec: "SimulationPersonRecord", offense_type: str) -> float:
    prosperity = clamp01(float(rec.person.household_prosperity or 0.0) / 4.0)
    standing = clamp01(float(rec.person.social_standing_01 or 0.0))
    patronage = _patronage_strength(ctx, int(rec.person_id))
    office = _office_power(ctx, int(rec.person_id))
    power = prosperity * 0.35 + standing * 0.24 + patronage * 0.28 + office
    if str(offense_type or "").strip() == "murder":
        power *= 0.35
    return clamp01(power)


def _outlaw_case_key(
    *, offense_type: str, accused_person_id: int, source_event_key: str, year: int
) -> str:
    key = str(source_event_key or "").strip()
    if key:
        return f"{offense_type}:{key}"
    return f"{offense_type}:{int(accused_person_id)}:{int(year)}"


def _forget_year(
    *, year: int, offense_type: str, severity: float, knownness: float, buyoff_power: float
) -> int:
    base = 4 + int(round(severity * 16.0 + knownness * 8.0 - buyoff_power * 5.0))
    if str(offense_type or "").strip() == "murder":
        base += 8
    return int(year) + max(3, base)


def _case_event_payload(case: SimulationOutlawCase, *, event_type: str, resolution: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": event_type,
        "case_key": case.case_key,
        "accused_person_id": int(case.accused_person_id),
        "person_id": int(case.accused_person_id),
        "offense_type": case.offense_type,
        "incident_kind": case.offense_kind,
        "status": case.status,
        "severity_01": round(float(case.severity_01), 5),
        "knownness_01": round(float(case.knownness_01), 5),
        "pursuit_pressure_01": round(float(case.pursuit_pressure_01), 5),
        "buyoff_power_01": round(float(case.buyoff_power_01), 5),
        "settlement_id": case.settlement_id,
        "region_id": case.region_id,
        "refuge_id": case.refuge_id,
    }
    if case.victim_person_id is not None:
        payload["victim_person_id"] = int(case.victim_person_id)
    if case.target_person_id is not None:
        payload["target_person_id"] = int(case.target_person_id)
    if resolution:
        payload["resolution"] = resolution
    return payload


def open_outlaw_case(
    ctx: "SimulationContext",
    *,
    year: int,
    accused: "SimulationPersonRecord",
    offense_type: str,
    offense_kind: str,
    severity_01: float,
    knownness_01: float,
    source_event_key: str,
    victim_person_id: int | None = None,
    target_person_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> SimulationOutlawCase | None:
    """Open or strengthen a wanted case for a detailed offender."""

    severity = clamp01(severity_01)
    knownness = clamp01(knownness_01)
    if str(offense_type or "").strip() == "property_crime" and severity < PROPERTY_OUTLAW_MIN_SEVERITY:
        return None
    sid, rid = _person_residence(ctx, accused)
    buyoff = _buyoff_power(ctx, accused, offense_type)
    buyoff_relief = buyoff * (0.28 if offense_type == "murder" else 0.55)
    pressure = clamp01(0.14 + severity * 0.58 + knownness * 0.32 - buyoff_relief)
    case_key = _outlaw_case_key(
        offense_type=offense_type,
        accused_person_id=int(accused.person_id),
        source_event_key=source_event_key,
        year=int(year),
    )
    existing = (getattr(ctx, "outlaw_cases", {}) or {}).get(case_key)
    if existing is not None:
        case = replace(
            existing,
            severity_01=max(existing.severity_01, severity),
            knownness_01=max(existing.knownness_01, knownness),
            pursuit_pressure_01=max(existing.pursuit_pressure_01, pressure),
            buyoff_power_01=max(existing.buyoff_power_01, buyoff),
            last_seen_year=int(year),
            details={**existing.details, **(details or {})},
        )
        ctx.outlaw_cases[case_key] = case
        return case

    case = SimulationOutlawCase(
        case_key=case_key,
        accused_person_id=int(accused.person_id),
        offense_type=str(offense_type or "").strip(),
        offense_kind=str(offense_kind or "").strip(),
        status="active",
        source_event_key=str(source_event_key or "").strip(),
        victim_person_id=victim_person_id,
        target_person_id=target_person_id,
        severity_01=severity,
        knownness_01=knownness,
        pursuit_pressure_01=pressure,
        buyoff_power_01=buyoff,
        start_year=int(year),
        last_seen_year=int(year),
        expected_forget_year=_forget_year(
            year=int(year),
            offense_type=offense_type,
            severity=severity,
            knownness=knownness,
            buyoff_power=buyoff,
        ),
        region_id=rid,
        settlement_id=sid,
        details=dict(details or {}),
    )
    ctx.outlaw_cases[case_key] = case
    accused.person = replace(
        accused.person,
        outlaw_status=OUTLAW_STATUS_WANTED,
        outlaw_case_key=case.case_key,
        outlaw_since_year=int(year),
        last_free_settlement_id=sid,
    )
    ctx._record_simulation_event(
        int(year),
        "outlaw_case_opened",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_case_opened"),
        },
    )
    return case


def outlaw_case_from_murder(
    ctx: "SimulationContext", year: int, incident: object
) -> dict[str, Any] | None:
    witness_ids = tuple(getattr(incident, "witness_person_ids", ()) or ())
    importance = float(getattr(incident, "historical_importance", 0.0) or 0.0)
    kind = str(getattr(incident, "incident_kind", "murder") or "murder")
    kind_bonus = 0.08 if kind in {"predatory_murder", "ambush_killing", "kin_killing"} else 0.0
    case = open_outlaw_case(
        ctx,
        year=int(year),
        accused=getattr(incident, "killer"),
        offense_type="murder",
        offense_kind=kind,
        severity_01=0.76 + importance * 0.22 + kind_bonus,
        knownness_01=0.35 + len(witness_ids) * 0.12 + importance * 0.35,
        source_event_key=(
            f"murder:{getattr(incident.killer, 'person_id')}:"
            f"{getattr(incident.victim, 'person_id')}:{int(year)}"
        ),
        victim_person_id=int(getattr(incident.victim, "person_id")),
        details={"source_role": "murder_outlaw_case", "witness_count": len(witness_ids)},
    )
    return None if case is None else _case_event_payload(case, event_type="outlaw_case_opened")


def outlaw_case_from_property_crime(
    ctx: "SimulationContext", year: int, incident: object
) -> dict[str, Any] | None:
    witness_ids = tuple(getattr(incident, "witness_person_ids", ()) or ())
    importance = float(getattr(incident, "historical_importance", 0.0) or 0.0)
    loss = float(getattr(incident, "loss_value", 0.0) or 0.0)
    kind = str(getattr(incident, "incident_kind", "property_crime") or "property_crime")
    kind_bonus = (
        0.18
        if kind in {"storehouse_robbery", "extortion", "market_extortion", "inheritance_fraud"}
        else 0.08 if kind in {"livestock_theft", "debt_fraud"} else 0.0
    )
    case = open_outlaw_case(
        ctx,
        year=int(year),
        accused=getattr(incident, "perpetrator"),
        offense_type="property_crime",
        offense_kind=kind,
        severity_01=loss * 2.8 + importance * 0.35 + kind_bonus,
        knownness_01=0.24 + len(witness_ids) * 0.11 + importance * 0.30,
        source_event_key=(
            f"property_crime:{getattr(incident.perpetrator, 'person_id')}:"
            f"{getattr(incident.target, 'person_id')}:{int(year)}:{kind}"
        ),
        target_person_id=int(getattr(incident.target, "person_id")),
        details={
            "source_role": "property_crime_outlaw_case",
            "loss_value": loss,
            "witness_count": len(witness_ids),
        },
    )
    return None if case is None else _case_event_payload(case, event_type="outlaw_case_opened")


def _choose_refuge(
    ctx: "SimulationContext", case: SimulationOutlawCase, year: int
) -> SimulationOutlawRefuge:
    rid = (case.region_id or "").strip() or "unknown"
    active = [
        r
        for r in (getattr(ctx, "outlaw_refuges", {}) or {}).values()
        if r.region_id == rid and str(r.status or "").strip().lower() == "active"
    ]
    if active:
        refuge = min(active, key=lambda r: (int(r.band_size), r.refuge_id))
        refuge = replace(
            refuge,
            band_size=int(refuge.band_size) + 1,
            last_activity_year=int(year),
        )
        ctx.outlaw_refuges[refuge.refuge_id] = refuge
        return refuge
    ordinal = len(getattr(ctx, "outlaw_refuges", {}) or {}) + 1
    refuge_id = f"outlaw_refuge:{rid}:{ordinal}"
    rng = random.Random(int(year) * 97_031 + ordinal * 109 + int(getattr(ctx, "placename_rng_salt", 0)))
    refuge = SimulationOutlawRefuge(
        refuge_id=refuge_id,
        region_id=rid,
        near_settlement_id=case.settlement_id,
        status="active",
        founded_year=int(year),
        band_size=1,
        concealment_01=clamp01(0.42 + rng.random() * 0.28),
        support_01=clamp01(0.08 + float(case.buyoff_power_01) * 0.22),
        last_activity_year=int(year),
        details={"origin_case_key": case.case_key},
    )
    ctx.outlaw_refuges[refuge.refuge_id] = refuge
    return refuge


def flee_to_refuge(
    ctx: "SimulationContext", case_key: str, *, year: int
) -> SimulationOutlawRefuge | None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(case_key)
    if case is None or str(case.status or "").strip().lower() != "active":
        return None
    rec = ctx.id_to_record.get(int(case.accused_person_id))
    if rec is None or int(case.accused_person_id) not in ctx.current_people_ids:
        return None
    last_free = (
        rec.person.current_settlement_id
        or rec.person.last_free_settlement_id
        or case.settlement_id
    )
    if rec.person.paramour_person_id is not None:
        try:
            ctx.end_paramour_relationship(int(rec.person_id), int(rec.person.paramour_person_id))
        except (LookupError, ValueError):
            pass
    if rec.person.partner_person_id is not None:
        try:
            ctx.dissolve_couple(int(rec.person_id), int(rec.person.partner_person_id))
        except (LookupError, ValueError):
            pass
    refuge = _choose_refuge(ctx, case, int(year))
    last_job = rec.person.last_job or rec.person.job
    rec.person = replace(
        rec.person,
        current_settlement_id=None,
        job=None,
        job_assigned_year=None,
        job_era=None,
        job_tier=None,
        last_job=last_job,
        job_lost_year=int(year) if last_job else rec.person.job_lost_year,
        job_market_type="criminal",
        employment_status="outlaw",
        housing_status="outlaw_refuge",
        household_role="fugitive",
        host_person_id=None,
        employer_person_id=None,
        outlaw_status=OUTLAW_STATUS_FUGITIVE,
        outlaw_case_key=case.case_key,
        outlaw_refuge_id=refuge.refuge_id,
        outlaw_since_year=case.start_year or int(year),
        last_free_settlement_id=last_free,
    )
    case = replace(
        case,
        last_seen_year=int(year),
        refuge_id=refuge.refuge_id,
    )
    ctx.outlaw_cases[case.case_key] = case
    ctx.invalidate_alive_census_cache()
    ctx.invalidate_annual_indexes()
    ctx._record_simulation_event(
        int(year),
        "outlaw_flight",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_flight"),
            "from_settlement_id": last_free,
            "outlaw_refuge_id": refuge.refuge_id,
            "details": "fled from ordinary settlement residence",
        },
    )
    ctx._record_simulation_event(
        int(year),
        "outlaw_refuge_joined",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_refuge_joined"),
            "outlaw_refuge_id": refuge.refuge_id,
            "band_size": int(refuge.band_size),
            "near_settlement_id": refuge.near_settlement_id,
        },
    )
    return refuge


def _return_settlement(ctx: "SimulationContext", case: SimulationOutlawCase, rec: "SimulationPersonRecord") -> str | None:
    sid = (
        rec.person.last_free_settlement_id
        or case.settlement_id
        or rec.person.birthplace_settlement_id
        or ""
    ).strip()
    if sid and sid in ctx.settlements_by_id:
        return sid
    rid = (case.region_id or rec.person.birthplace_region_id or "").strip()
    if rid:
        active = ctx.active_settlements_in_region(rid)
        if active:
            return active[0].settlement_id
    return None


def _mark_refuge_if_empty(ctx: "SimulationContext", refuge_id: str | None, year: int) -> None:
    rid = str(refuge_id or "").strip()
    if not rid:
        return
    active_count = sum(
        1
        for case in (getattr(ctx, "outlaw_cases", {}) or {}).values()
        if case.refuge_id == rid and str(case.status or "").strip().lower() == "active"
    )
    refuge = (getattr(ctx, "outlaw_refuges", {}) or {}).get(rid)
    if refuge is None:
        return
    if active_count <= 0:
        ctx.outlaw_refuges[rid] = replace(
            refuge,
            status="abandoned",
            abandoned_year=int(year),
            band_size=0,
            last_activity_year=int(year),
        )
    else:
        ctx.outlaw_refuges[rid] = replace(
            refuge,
            band_size=max(1, active_count),
            last_activity_year=int(year),
        )


def resolve_outlaw_case(
    ctx: "SimulationContext",
    case_key: str,
    *,
    year: int,
    resolution: str,
) -> SimulationOutlawCase | None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(case_key)
    if case is None:
        return None
    rec = ctx.id_to_record.get(int(case.accused_person_id))
    old_refuge_id = case.refuge_id
    case = replace(
        case,
        status="resolved",
        resolved_year=int(year),
        resolution=str(resolution or "").strip() or "resolved",
    )
    ctx.outlaw_cases[case.case_key] = case
    if rec is not None and int(rec.person_id) in ctx.current_people_ids:
        if resolution in {"captured", "punished"}:
            sid = _return_settlement(ctx, case, rec)
            rec.person = replace(
                rec.person,
                current_settlement_id=sid,
                outlaw_status=OUTLAW_STATUS_PUNISHED,
                outlaw_case_key=case.case_key,
                outlaw_refuge_id=None,
                housing_status="street",
                household_role="punished_returnee",
                employment_status="unemployed",
                job=None,
                job_assigned_year=None,
                job_era=None,
                job_tier=None,
                last_job=rec.person.last_job or rec.person.job,
                job_lost_year=int(year) if rec.person.job else rec.person.job_lost_year,
                job_market_type="none",
                host_person_id=None,
                employer_person_id=None,
            )
        elif resolution in {"returned", "forgotten"}:
            sid = _return_settlement(ctx, case, rec)
            rec.person = replace(
                rec.person,
                current_settlement_id=sid,
                outlaw_status=OUTLAW_STATUS_RETURNED,
                outlaw_case_key=case.case_key,
                outlaw_refuge_id=None,
                housing_status="own_household" if sid else None,
                household_role="returned_adult",
                employment_status="unemployed",
                job=None,
                job_assigned_year=None,
                job_era=None,
                job_tier=None,
                last_job=rec.person.last_job or rec.person.job,
                job_lost_year=int(year) if rec.person.job else rec.person.job_lost_year,
                job_market_type="none",
                host_person_id=None,
                employer_person_id=None,
            )
        elif resolution == "bought_off":
            rec.person = replace(
                rec.person,
                outlaw_status=OUTLAW_STATUS_CLEARED,
                outlaw_case_key=case.case_key,
                outlaw_refuge_id=None,
            )
    _mark_refuge_if_empty(ctx, old_refuge_id, int(year))
    ctx.invalidate_alive_census_cache()
    ctx.invalidate_annual_indexes()
    event_type = {
        "bought_off": "outlaw_bought_off",
        "forgotten": "outlaw_forgotten",
        "returned": "outlaw_returned",
        "captured": "outlaw_captured",
        "punished": "outlaw_captured",
    }.get(resolution, "outlaw_returned")
    ctx._record_simulation_event(
        int(year),
        event_type,
        {
            "year": int(year),
            **_case_event_payload(case, event_type=event_type, resolution=resolution),
        },
    )
    return case


def kill_outlaw(
    ctx: "SimulationContext", case_key: str, *, year: int
) -> SimulationOutlawCase | None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(case_key)
    if case is None:
        return None
    ctx._record_simulation_event(
        int(year),
        "outlaw_killed",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_killed", resolution="killed"),
        },
    )
    rec = ctx.id_to_record.get(int(case.accused_person_id))
    if rec is not None and int(rec.person_id) in ctx.current_people_ids:
        ctx.mark_dead({int(rec.person_id)}, deathyear=int(year))
    case = replace(
        case,
        status="resolved",
        resolved_year=int(year),
        resolution="killed",
    )
    ctx.outlaw_cases[case.case_key] = case
    _mark_refuge_if_empty(ctx, case.refuge_id, int(year))
    return case


def _maybe_buy_off(ctx: "SimulationContext", case: SimulationOutlawCase, year: int, rng: random.Random) -> bool:
    if case.offense_type == "murder" and case.severity_01 >= 0.82 and case.knownness_01 >= 0.48:
        return False
    threshold = 0.56 if case.offense_type == "murder" else 0.32
    if case.buyoff_power_01 < threshold:
        return False
    chance = clamp01(case.buyoff_power_01 * 0.45 - case.severity_01 * 0.16)
    if rng.random() >= chance:
        return False
    resolve_outlaw_case(ctx, case.case_key, year=int(year), resolution="bought_off")
    return True


def _record_raid(ctx: "SimulationContext", case: SimulationOutlawCase, refuge: SimulationOutlawRefuge, year: int) -> None:
    ctx._record_simulation_event(
        int(year),
        "outlaw_raid",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_raid"),
            "outlaw_refuge_id": refuge.refuge_id,
            "near_settlement_id": refuge.near_settlement_id,
            "band_size": int(refuge.band_size),
            "raid_pressure_01": round(clamp01(case.severity_01 * 0.45 + case.pursuit_pressure_01 * 0.35), 5),
        },
    )


def _pursuit_outcome(
    case: SimulationOutlawCase, refuge: SimulationOutlawRefuge, traits: dict[str, float], rng: random.Random
) -> str:
    courage = clamp01((float(traits.get("courage", 0.0)) + 100.0) / 200.0)
    discipline = clamp01((float(traits.get("discipline", 0.0)) + 100.0) / 200.0)
    capture = clamp01(0.28 + case.pursuit_pressure_01 * 0.42 - refuge.concealment_01 * 0.18)
    death = clamp01(0.08 + case.severity_01 * 0.20 + courage * 0.10 - discipline * 0.06)
    roll = rng.random()
    if roll < death:
        return "killed"
    if roll < death + capture:
        return "captured"
    return "escaped"


def simulation_outlaws_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Advance active wanted cases and fugitive refuges."""
    cases = sorted(
        (getattr(ctx, "outlaw_cases", {}) or {}).values(),
        key=lambda case: (case.start_year if case.start_year is not None else 0, case.case_key),
    )
    for case in cases:
        if str(case.status or "").strip().lower() != "active":
            continue
        rec = ctx.id_to_record.get(int(case.accused_person_id))
        if rec is None or int(case.accused_person_id) not in ctx.current_people_ids:
            ctx.outlaw_cases[case.case_key] = replace(
                case,
                status="resolved",
                resolved_year=int(year),
                resolution="died",
            )
            continue
        rng = random.Random(
            int(year) * OUTLAW_RNG_STREAM
            + int(case.accused_person_id) * 131
            + int(getattr(ctx, "placename_rng_salt", 0))
        )
        current_status = str(rec.person.outlaw_status or "").strip().lower()
        if current_status != OUTLAW_STATUS_FUGITIVE:
            if _maybe_buy_off(ctx, case, int(year), rng):
                continue
            flee_chance = clamp01(0.18 + case.pursuit_pressure_01 * 0.62 - case.buyoff_power_01 * 0.22)
            if rng.random() < flee_chance:
                flee_to_refuge(ctx, case.case_key, year=int(year))
            continue

        if case.expected_forget_year is not None and int(year) >= int(case.expected_forget_year):
            return_chance = clamp01(0.28 + case.buyoff_power_01 * 0.20 + (1.0 - case.knownness_01) * 0.24)
            if rng.random() < return_chance:
                resolve_outlaw_case(ctx, case.case_key, year=int(year), resolution="forgotten")
                continue

        refuge = (getattr(ctx, "outlaw_refuges", {}) or {}).get(str(rec.person.outlaw_refuge_id or case.refuge_id or ""))
        if refuge is None:
            refuge = flee_to_refuge(ctx, case.case_key, year=int(year))
            if refuge is None:
                continue

        raid_chance = clamp01(0.05 + case.severity_01 * 0.10 + max(0, refuge.band_size - 1) * 0.025)
        if rng.random() < raid_chance:
            _record_raid(ctx, case, refuge, int(year))
            case = replace(case, last_seen_year=int(year))
            ctx.outlaw_cases[case.case_key] = case
            refuge = replace(refuge, last_activity_year=int(year))
            ctx.outlaw_refuges[refuge.refuge_id] = refuge

        discovery_chance = clamp01(
            0.06
            + case.pursuit_pressure_01 * 0.23
            + max(0, refuge.band_size - 1) * 0.035
            - refuge.concealment_01 * 0.12
        )
        if rng.random() >= discovery_chance:
            continue
        refuge = replace(refuge, discovered_year=refuge.discovered_year or int(year), last_activity_year=int(year))
        ctx.outlaw_refuges[refuge.refuge_id] = refuge
        ctx._record_simulation_event(
            int(year),
            "outlaw_pursuit",
            {
                "year": int(year),
                **_case_event_payload(case, event_type="outlaw_pursuit"),
                "outlaw_refuge_id": refuge.refuge_id,
                "band_size": int(refuge.band_size),
                "discovery_chance_01": round(discovery_chance, 5),
            },
        )
        outcome = _pursuit_outcome(case, refuge, work_trait_values(rec.person), rng)
        if outcome == "killed":
            kill_outlaw(ctx, case.case_key, year=int(year))
        elif outcome == "captured":
            resolve_outlaw_case(ctx, case.case_key, year=int(year), resolution="captured")
