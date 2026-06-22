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
OUTLAW_STATUS_IMPRISONED = "imprisoned"
OUTLAW_CAREER_BLOCKING_STATUSES = frozenset(
    {OUTLAW_STATUS_WANTED, OUTLAW_STATUS_FUGITIVE, OUTLAW_STATUS_PUNISHED, OUTLAW_STATUS_IMPRISONED}
)

OUTLAW_RNG_STREAM = 1_740_331
CUSTODY_RNG_STREAM = 1_992_041
PROPERTY_OUTLAW_MIN_SEVERITY = 0.36
PASSIVE_OUTLAW_PROMOTION_REASON = "outlaw_case_accused"


@dataclass(frozen=True)
class OutlawLawProfile:
    """Polity-level legal posture that tunes outlaw handling."""

    profile_id: str
    label: str
    severity_multiplier: float = 1.0
    pursuit_pressure_add: float = 0.0
    buyoff_relief_multiplier: float = 1.0
    buyoff_threshold_multiplier: float = 1.0
    buyoff_chance_multiplier: float = 1.0
    punishment_duration_multiplier: float = 1.0
    forget_year_multiplier: float = 1.0
    return_chance_add: float = 0.0
    flee_chance_add: float = 0.0
    discovery_chance_add: float = 0.0
    capture_chance_add: float = 0.0
    death_chance_add: float = 0.0


OUTLAW_LAW_PROFILES: dict[str, OutlawLawProfile] = {
    "customary": OutlawLawProfile(
        profile_id="customary",
        label="customary compromise",
    ),
    "strict_justice": OutlawLawProfile(
        profile_id="strict_justice",
        label="strict justice",
        severity_multiplier=1.12,
        pursuit_pressure_add=0.12,
        buyoff_relief_multiplier=0.65,
        buyoff_threshold_multiplier=1.35,
        buyoff_chance_multiplier=0.55,
        punishment_duration_multiplier=1.28,
        forget_year_multiplier=1.24,
        return_chance_add=-0.10,
        flee_chance_add=0.08,
        discovery_chance_add=0.05,
        capture_chance_add=0.06,
        death_chance_add=0.02,
    ),
    "lenient_compromise": OutlawLawProfile(
        profile_id="lenient_compromise",
        label="lenient compromise",
        severity_multiplier=0.88,
        pursuit_pressure_add=-0.09,
        buyoff_relief_multiplier=1.20,
        buyoff_threshold_multiplier=0.75,
        buyoff_chance_multiplier=1.45,
        punishment_duration_multiplier=0.72,
        forget_year_multiplier=0.74,
        return_chance_add=0.12,
        flee_chance_add=-0.05,
        discovery_chance_add=-0.03,
        capture_chance_add=-0.03,
        death_chance_add=-0.02,
    ),
}
_DEFAULT_OUTLAW_LAW_PROFILE = OUTLAW_LAW_PROFILES["customary"]

OUTLAW_POLITY_TYPE_LAW_PROFILES: dict[str, str] = {
    "empire": "strict_justice",
    "kingdom": "strict_justice",
    "duchy": "strict_justice",
    "march": "strict_justice",
    "county": "customary",
    "city_state": "lenient_compromise",
    "republic": "lenient_compromise",
    "commune": "lenient_compromise",
    "tribe": "lenient_compromise",
    "band": "lenient_compromise",
}


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
    custody_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SimulationOutlawRefuge:
    """A non-settlement shelter/base used by fugitives."""

    refuge_id: str
    region_id: str
    display_name: str = ""
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


@dataclass(frozen=True)
class SimulationOutlawCustody:
    """Durable custody/imprisonment state created by nonlethal capture."""

    custody_id: str
    case_key: str
    person_id: int
    custody_type: str = "imprisonment"
    status: str = "active"
    site_settlement_id: str | None = None
    region_id: str | None = None
    start_year: int | None = None
    expected_release_year: int | None = None
    release_year: int | None = None
    severity_01: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


_REFUGE_ADJECTIVES = (
    "Ashen",
    "Blackthorn",
    "Briar",
    "Cinder",
    "Duskwater",
    "Flint",
    "Gray",
    "Hidden",
    "Lanternless",
    "Mossblack",
    "Old",
    "Red",
    "Sable",
    "Shadowed",
    "Thorn",
    "Whispering",
)

_REFUGE_NOUNS = (
    "Brake",
    "Camp",
    "Crag",
    "Cut",
    "Den",
    "Fold",
    "Hollow",
    "Ledge",
    "Mire",
    "Ravine",
    "Shelter",
    "Thicket",
    "Tor",
    "Vault",
    "Wash",
)

_REFUGE_REGION_NOUNS = {
    "bay": "Cove",
    "bog": "Mire",
    "coast": "Cove",
    "forest": "Thicket",
    "granite": "Crag",
    "hill": "Tor",
    "marsh": "Mire",
    "mount": "Crag",
    "range": "Ridge",
    "river": "Ford",
    "stone": "Ledge",
    "wood": "Brake",
}


def _stable_index(parts: tuple[object, ...]) -> int:
    value = 2_166_136_261
    for part in parts:
        for ch in str(part or ""):
            value ^= ord(ch)
            value = (value * 16_777_619) & 0xFFFFFFFF
    return int(value)


def _refuge_region_noun(region_id: object, seed: int) -> str:
    tokens = [
        token.strip().lower()
        for token in str(region_id or "").replace("-", "_").split("_")
        if token.strip()
    ]
    for token in tokens:
        noun = _REFUGE_REGION_NOUNS.get(token)
        if noun:
            return noun
    return _REFUGE_NOUNS[(seed // max(1, len(_REFUGE_ADJECTIVES))) % len(_REFUGE_NOUNS)]


def outlaw_refuge_display_name(
    refuge_id: object,
    *,
    region_id: object = "",
    near_place_label: object = "",
    year: object = "",
    salt: object = "",
) -> str:
    """Deterministic readable name for a refuge when no saved name exists."""
    explicit = str(refuge_id or "").strip()
    seed = _stable_index((explicit, region_id, near_place_label, year, salt))
    adjective = _REFUGE_ADJECTIVES[seed % len(_REFUGE_ADJECTIVES)]
    noun = _refuge_region_noun(region_id, seed)
    return f"The {adjective} {noun}"


def _unique_refuge_display_name(name: str, used_names: set[str]) -> str:
    base = (name or "").strip() or "The Hidden Camp"
    taken = {n.strip().casefold() for n in used_names if n.strip()}
    if base.casefold() not in taken:
        return base
    idx = 2
    while f"{base} {idx}".casefold() in taken:
        idx += 1
    return f"{base} {idx}"


def is_outlaw_absent(person: object) -> bool:
    """True when a person is alive but unavailable for ordinary settlement life."""
    return str(getattr(person, "outlaw_status", "") or "").strip().lower() in {
        OUTLAW_STATUS_FUGITIVE,
        OUTLAW_STATUS_IMPRISONED,
    }


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
    elif status == OUTLAW_STATUS_IMPRISONED:
        updates.update(
            {
                "employment_status": "imprisoned",
                "job_market_type": "custody",
                "housing_status": "custody",
                "household_role": "prisoner",
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


def _case_place_from_people(
    ctx: "SimulationContext",
    *,
    accused: "SimulationPersonRecord",
    victim_person_id: int | None,
    target_person_id: int | None,
) -> tuple[str | None, str | None]:
    for pid in (victim_person_id, target_person_id):
        if pid is None:
            continue
        other = ctx.id_to_record.get(int(pid))
        if other is None:
            continue
        sid, rid = _person_residence(ctx, other)
        if sid or rid:
            return sid, rid
    return _person_residence(ctx, accused)


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


def _normalized_law_profile_key(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _known_law_profile_key(value: object) -> str | None:
    key = _normalized_law_profile_key(value)
    return key if key in OUTLAW_LAW_PROFILES else None


def _outlaw_law_profile_from_case(case: SimulationOutlawCase) -> OutlawLawProfile:
    key = _known_law_profile_key((case.details or {}).get("law_profile"))
    if key is None:
        return _DEFAULT_OUTLAW_LAW_PROFILE
    return OUTLAW_LAW_PROFILES[key]


def _outlaw_law_details(
    profile: OutlawLawProfile,
    *,
    source: str,
    polity: object | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "law_profile": profile.profile_id,
        "law_profile_label": profile.label,
        "law_profile_source": source,
    }
    if polity is not None:
        details["law_polity_id"] = getattr(polity, "polity_id", None)
        details["law_polity_type_id"] = str(
            getattr(polity, "polity_type_id", "") or ""
        ).strip()
    return details


def _outlaw_law_profile_for_polity(polity: object) -> tuple[OutlawLawProfile, dict[str, Any]]:
    notes = getattr(polity, "notes", None) or {}
    profile_key = None
    if isinstance(notes, dict):
        profile_key = _known_law_profile_key(
            notes.get("outlaw_law_profile")
            or notes.get("law_code_profile")
            or notes.get("law_profile")
        )
    source = "polity_note"
    if profile_key is None:
        polity_type_id = _normalized_law_profile_key(getattr(polity, "polity_type_id", ""))
        profile_key = OUTLAW_POLITY_TYPE_LAW_PROFILES.get(polity_type_id, "customary")
        source = "polity_type"
    profile = OUTLAW_LAW_PROFILES.get(profile_key, _DEFAULT_OUTLAW_LAW_PROFILE)
    return profile, _outlaw_law_details(profile, source=source, polity=polity)


def _outlaw_law_context(
    ctx: "SimulationContext",
    *,
    settlement_id: str | None,
    region_id: str | None,
) -> tuple[OutlawLawProfile, dict[str, Any]]:
    default_details = _outlaw_law_details(_DEFAULT_OUTLAW_LAW_PROFILE, source="default")
    try:
        from library.polity import (
            polities_in_region,
            polity_for_region,
            polity_for_settlement,
        )
    except ImportError:
        return _DEFAULT_OUTLAW_LAW_PROFILE, default_details

    polity = None
    source_scope = "default"
    sid = str(settlement_id or "").strip()
    rid = str(region_id or "").strip()
    if sid:
        polity = polity_for_settlement(ctx, sid)
        if polity is not None:
            source_scope = "settlement_polity"
    if polity is None and rid:
        polity = polity_for_region(ctx, rid)
        if polity is not None:
            source_scope = "region_polity"
    if polity is None and rid:
        candidates = polities_in_region(ctx, rid)
        if candidates:
            polity = sorted(
                candidates,
                key=lambda p: (
                    str(getattr(p, "polity_type_id", "") or ""),
                    int(getattr(p, "polity_id", 0) or 0),
                ),
            )[0]
            source_scope = "region_member_polity"
    if polity is None:
        return _DEFAULT_OUTLAW_LAW_PROFILE, default_details
    profile, details = _outlaw_law_profile_for_polity(polity)
    details["law_profile_source"] = f"{source_scope}:{details['law_profile_source']}"
    return profile, details


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
    *,
    year: int,
    offense_type: str,
    severity: float,
    knownness: float,
    buyoff_power: float,
    law_profile: OutlawLawProfile | None = None,
) -> int:
    base = 4 + int(round(severity * 16.0 + knownness * 8.0 - buyoff_power * 5.0))
    if str(offense_type or "").strip() == "murder":
        base += 8
    duration = max(3, base)
    if law_profile is not None:
        duration = max(3, int(round(duration * law_profile.forget_year_multiplier)))
    return int(year) + duration


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
        "custody_id": case.custody_id,
    }
    if case.victim_person_id is not None:
        payload["victim_person_id"] = int(case.victim_person_id)
    if case.target_person_id is not None:
        payload["target_person_id"] = int(case.target_person_id)
    for key in (
        "law_profile",
        "law_profile_label",
        "law_profile_source",
        "law_polity_id",
        "law_polity_type_id",
    ):
        value = (case.details or {}).get(key)
        if value is not None:
            payload[key] = value
    if resolution:
        payload["resolution"] = resolution
    return payload


def _custody_id_for_case(case: SimulationOutlawCase) -> str:
    return f"outlaw_custody:{case.case_key}"


def _custody_duration_years(case: SimulationOutlawCase) -> int:
    duration = 2 + int(round(float(case.severity_01) * 8.0 + float(case.knownness_01) * 4.0))
    if str(case.offense_type or "").strip() == "murder":
        duration += 6
    duration = int(
        round(duration * _outlaw_law_profile_from_case(case).punishment_duration_multiplier)
    )
    return max(1, min(30, duration))


def _open_outlaw_custody(
    ctx: "SimulationContext",
    case: SimulationOutlawCase,
    rec: "SimulationPersonRecord",
    *,
    year: int,
    site_settlement_id: str | None,
) -> SimulationOutlawCustody:
    custody_id = str(case.custody_id or _custody_id_for_case(case)).strip()
    region_id = (
        case.region_id
        or getattr(rec.person, "birthplace_region_id", None)
        or ""
    )
    expected_release_year = int(year) + _custody_duration_years(case)
    existing = (getattr(ctx, "outlaw_custodies", {}) or {}).get(custody_id)
    details = {
        "resolution": "captured",
        "offense_type": case.offense_type,
        "offense_kind": case.offense_kind,
        "refuge_id": case.refuge_id,
    }
    for key in (
        "law_profile",
        "law_profile_label",
        "law_profile_source",
        "law_polity_id",
        "law_polity_type_id",
    ):
        value = (case.details or {}).get(key)
        if value is not None:
            details[key] = value
    if existing is not None:
        details = {**existing.details, **details}
    custody = SimulationOutlawCustody(
        custody_id=custody_id,
        case_key=case.case_key,
        person_id=int(case.accused_person_id),
        custody_type="imprisonment",
        status="active",
        site_settlement_id=site_settlement_id,
        region_id=str(region_id or "").strip() or None,
        start_year=existing.start_year if existing is not None else int(year),
        expected_release_year=(
            existing.expected_release_year
            if existing is not None and existing.expected_release_year is not None
            else expected_release_year
        ),
        release_year=None,
        severity_01=clamp01(float(case.severity_01 or 0.0)),
        details=details,
    )
    ctx.outlaw_custodies[custody.custody_id] = custody
    return custody


def _custody_event_payload(custody: SimulationOutlawCustody | None) -> dict[str, Any]:
    if custody is None:
        return {}
    return {
        "custody_id": custody.custody_id,
        "custody_type": custody.custody_type,
        "custody_status": custody.status,
        "custody_site_settlement_id": custody.site_settlement_id,
        "custody_region_id": custody.region_id,
        "custody_start_year": custody.start_year,
        "custody_expected_release_year": custody.expected_release_year,
        "custody_release_year": custody.release_year,
    }


def _custody_rng(ctx: "SimulationContext", custody: SimulationOutlawCustody, year: int) -> random.Random:
    return random.Random(
        int(year) * CUSTODY_RNG_STREAM
        + int(custody.person_id) * 257
        + _stable_index((custody.custody_id, custody.case_key))
        + int(getattr(ctx, "placename_rng_salt", 0))
    )


def _custody_year_outcome(
    custody: SimulationOutlawCustody,
    rec: "SimulationPersonRecord",
    *,
    year: int,
    rng: random.Random,
) -> str | None:
    expected = custody.expected_release_year
    if expected is not None and int(year) >= int(expected):
        return "released"
    if custody.start_year is not None and int(year) <= int(custody.start_year):
        return None
    traits = work_trait_values(rec.person)
    severity = clamp01(float(custody.severity_01 or 0.0))
    neuro = clamp01(abs(float(traits.get("neurochemical", 0.0))) / 100.0)
    resilience = clamp01((100.0 + float(traits.get("resilience", 0.0))) / 200.0)
    courage = clamp01((100.0 + float(traits.get("courage", 0.0))) / 200.0)
    adaptability = clamp01((100.0 + float(traits.get("adaptability", 0.0))) / 200.0)
    death_chance = clamp01(
        0.012
        + severity * 0.020
        + neuro * 0.018
        - resilience * 0.006
    )
    escape_chance = clamp01(
        0.007
        + courage * 0.012
        + adaptability * 0.012
        - severity * 0.004
    )
    roll = rng.random()
    if roll < death_chance:
        return "died"
    if roll < death_chance + escape_chance:
        return "escaped"
    return None


def _release_outlaw_custody(
    ctx: "SimulationContext",
    custody: SimulationOutlawCustody,
    rec: "SimulationPersonRecord",
    *,
    year: int,
) -> None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(custody.case_key)
    sid = custody.site_settlement_id
    if case is not None:
        sid = _return_settlement(ctx, case, rec) or sid
    custody = replace(custody, status="released", release_year=int(year))
    ctx.outlaw_custodies[custody.custody_id] = custody
    if case is not None:
        ctx.outlaw_cases[case.case_key] = replace(
            case,
            details={
                **(case.details or {}),
                "custody_status": "released",
                "custody_release_year": int(year),
            },
        )
    rec.person = replace(
        rec.person,
        current_settlement_id=sid,
        outlaw_status=OUTLAW_STATUS_RETURNED,
        outlaw_refuge_id=None,
        outlaw_custody_id=None,
        outlaw_custody_status=None,
        outlaw_custody_start_year=None,
        outlaw_custody_expected_release_year=None,
        outlaw_custody_release_year=int(year),
        outlaw_custody_site_settlement_id=None,
        housing_status="own_household" if sid else None,
        household_role="released_outlaw",
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
    payload: dict[str, Any] = {
        "year": int(year),
        "event_type": "outlaw_returned",
        "person_id": int(rec.person_id),
        "accused_person_id": int(rec.person_id),
        "resolution": "released_from_custody",
        "settlement_id": sid,
        "region_id": custody.region_id,
        **_custody_event_payload(custody),
    }
    if case is not None:
        payload.update(
            _case_event_payload(
                case,
                event_type="outlaw_returned",
                resolution="released_from_custody",
            )
        )
        payload["settlement_id"] = sid or case.settlement_id
        payload["region_id"] = custody.region_id or case.region_id
    ctx._record_simulation_event(int(year), "outlaw_returned", payload)
    ctx.invalidate_alive_census_cache()
    ctx.invalidate_annual_indexes()


def _escape_outlaw_custody(
    ctx: "SimulationContext",
    custody: SimulationOutlawCustody,
    rec: "SimulationPersonRecord",
    *,
    year: int,
) -> None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(custody.case_key)
    custody = replace(custody, status="escaped", release_year=int(year))
    ctx.outlaw_custodies[custody.custody_id] = custody
    sid = custody.site_settlement_id or rec.person.current_settlement_id
    rec.person = replace(
        rec.person,
        current_settlement_id=sid,
        outlaw_status=OUTLAW_STATUS_WANTED,
        outlaw_case_key=custody.case_key,
        outlaw_refuge_id=None,
        outlaw_custody_id=None,
        outlaw_custody_status=None,
        outlaw_custody_start_year=None,
        outlaw_custody_expected_release_year=None,
        outlaw_custody_release_year=int(year),
        outlaw_custody_site_settlement_id=None,
        employment_status="unemployed",
        housing_status="own_household" if sid else None,
        household_role="escaped_prisoner",
        job=None,
        job_assigned_year=None,
        job_era=None,
        job_tier=None,
        job_market_type="none",
        host_person_id=None,
        employer_person_id=None,
        last_free_settlement_id=sid or rec.person.last_free_settlement_id,
    )
    if case is not None:
        case = replace(
            case,
            status="active",
            resolved_year=None,
            resolution=None,
            custody_id=None,
            last_seen_year=int(year),
            expected_forget_year=max(
                int(case.expected_forget_year or year + 3),
                int(year) + 3,
            ),
            details={
                **(case.details or {}),
                "escaped_custody_id": custody.custody_id,
                "custody_status": "escaped",
                "custody_release_year": int(year),
            },
        )
        ctx.outlaw_cases[case.case_key] = case
        ctx._record_simulation_event(
            int(year),
            "outlaw_escape",
            {
                "year": int(year),
                **_case_event_payload(case, event_type="outlaw_escape"),
                "settlement_id": sid or case.settlement_id,
                "region_id": custody.region_id or case.region_id,
                **_custody_event_payload(custody),
            },
        )
        flee_to_refuge(ctx, case.case_key, year=int(year), flight_reason="escaped_custody")
    ctx.invalidate_alive_census_cache()
    ctx.invalidate_annual_indexes()


def _kill_outlaw_in_custody(
    ctx: "SimulationContext",
    custody: SimulationOutlawCustody,
    rec: "SimulationPersonRecord",
    *,
    year: int,
) -> None:
    case = (getattr(ctx, "outlaw_cases", {}) or {}).get(custody.case_key)
    custody = replace(custody, status="died", release_year=int(year))
    ctx.outlaw_custodies[custody.custody_id] = custody
    payload: dict[str, Any] = {
        "year": int(year),
        "event_type": "outlaw_died_in_custody",
        "person_id": int(rec.person_id),
        "accused_person_id": int(rec.person_id),
        "settlement_id": custody.site_settlement_id,
        "region_id": custody.region_id,
        "resolution": "died_in_custody",
        **_custody_event_payload(custody),
    }
    if case is not None:
        case = replace(
            case,
            status="resolved",
            resolved_year=int(year),
            resolution="died_in_custody",
            details={
                **(case.details or {}),
                "custody_status": "died",
                "custody_release_year": int(year),
            },
        )
        ctx.outlaw_cases[case.case_key] = case
        payload.update(
            _case_event_payload(
                case,
                event_type="outlaw_died_in_custody",
                resolution="died_in_custody",
            )
        )
        payload["settlement_id"] = custody.site_settlement_id or case.settlement_id
        payload["region_id"] = custody.region_id or case.region_id
    ctx._record_simulation_event(int(year), "outlaw_died_in_custody", payload)
    if int(rec.person_id) in ctx.current_people_ids:
        ctx.mark_dead({int(rec.person_id)}, deathyear=int(year))


def _process_active_custodies(ctx: "SimulationContext", year: int) -> None:
    for custody in sorted(
        (getattr(ctx, "outlaw_custodies", {}) or {}).values(),
        key=lambda c: (c.start_year if c.start_year is not None else 0, c.custody_id),
    ):
        if str(custody.status or "").strip().lower() != "active":
            continue
        rec = ctx.id_to_record.get(int(custody.person_id))
        if rec is None or int(custody.person_id) not in ctx.current_people_ids:
            ctx.outlaw_custodies[custody.custody_id] = replace(
                custody,
                status="died",
                release_year=int(year),
            )
            continue
        outcome = _custody_year_outcome(
            custody,
            rec,
            year=int(year),
            rng=_custody_rng(ctx, custody, int(year)),
        )
        if outcome == "released":
            _release_outlaw_custody(ctx, custody, rec, year=int(year))
        elif outcome == "escaped":
            _escape_outlaw_custody(ctx, custody, rec, year=int(year))
        elif outcome == "died":
            _kill_outlaw_in_custody(ctx, custody, rec, year=int(year))


def _passive_person_settlement_id(person: object) -> str:
    return str(
        getattr(person, "current_settlement_id", None)
        or getattr(person, "birthplace_settlement_id", None)
        or ""
    ).strip()


def _passive_person_region_id(person: object) -> str:
    region_id = str(getattr(person, "birthplace_region_id", None) or "").strip()
    if region_id:
        return region_id
    settlement_id = _passive_person_settlement_id(person)
    if ":" in settlement_id:
        return settlement_id.split(":", 1)[0].strip()
    return ""


def _passive_person_matches_outlaw_scope(
    person: object,
    *,
    year: int,
    settlement_id: str,
    region_id: str,
) -> bool:
    deathyear = getattr(person, "deathyear", None)
    if deathyear is not None and int(deathyear) <= int(year):
        return False
    if settlement_id and _passive_person_settlement_id(person) != settlement_id:
        return False
    if region_id and _passive_person_region_id(person) != region_id:
        return False
    return True


def _law_adjusted_property_severity(
    ctx: "SimulationContext",
    *,
    severity_01: float,
    settlement_id: str | None,
    region_id: str | None,
) -> float:
    law_profile, _ = _outlaw_law_context(
        ctx,
        settlement_id=settlement_id,
        region_id=region_id,
    )
    return clamp01(clamp01(severity_01) * law_profile.severity_multiplier)


def promote_passive_outlaw_accused(
    ctx: "SimulationContext",
    *,
    year: int,
    passive_person_id: int | None = None,
    settlement_id: str | None = None,
    region_id: str | None = None,
    source: dict[str, Any] | None = None,
    min_age: int = 16,
) -> "SimulationPersonRecord" | None:
    """Materialize a passive/cohort person so they can enter an outlaw case."""

    source_payload = {
        **(source or {}),
        "outlaw_role": "accused",
    }
    sid = str(settlement_id or "").strip()
    rid = str(region_id or "").strip()
    if passive_person_id is not None:
        try:
            pid = int(passive_person_id)
        except (TypeError, ValueError):
            return None
        existing = getattr(ctx, "id_to_record", {}).get(pid)
        if existing is not None:
            if int(existing.person_id) not in getattr(ctx, "current_people_ids", set()):
                return None
            existing_sid, existing_rid = _person_residence(ctx, existing)
            if sid and existing_sid != sid:
                return None
            if rid and existing_rid != rid:
                return None
            return existing
        prec = getattr(ctx, "passive_people", {}).get(pid)
        if prec is None:
            promote = getattr(ctx, "promote_nondetailed_people", None)
            if not callable(promote):
                return None
            try:
                promoted = promote(
                    year=int(year),
                    reason=PASSIVE_OUTLAW_PROMOTION_REASON,
                    person_ids=(pid,),
                    settlement_id=sid or None,
                    region_id=rid or None,
                    min_age=int(min_age),
                    limit=1,
                    source={
                        **source_payload,
                        "selector": "nondetailed_person_id",
                        "requested_person_id": pid,
                        "requested_settlement_id": sid or None,
                        "requested_region_id": rid or None,
                    },
                )
            except (TypeError, ValueError):
                return None
            return promoted[0] if promoted else None
        if not _passive_person_matches_outlaw_scope(
            prec.person,
            year=int(year),
            settlement_id=sid,
            region_id=rid,
        ):
            return None
        return ctx.promote_passive_person(
            pid,
            year=int(year),
            reason=PASSIVE_OUTLAW_PROMOTION_REASON,
            source={
                **source_payload,
                "selector": "passive_person_id",
                "requested_person_id": pid,
                "requested_settlement_id": sid or None,
                "requested_region_id": rid or None,
            },
        )

    if not sid and not rid:
        return None
    from library.passive_population import (
        promote_passive_candidate_for_office,
        promote_passive_candidate_for_settlement_context,
    )

    if sid:
        return promote_passive_candidate_for_settlement_context(
            ctx,
            year=int(year),
            settlement_id=sid,
            min_age=int(min_age),
            reason=PASSIVE_OUTLAW_PROMOTION_REASON,
            source={
                **source_payload,
                "selector": "settlement_cohort",
                "requested_settlement_id": sid,
                "requested_region_id": rid or None,
            },
        )
    return promote_passive_candidate_for_office(
        ctx,
        year=int(year),
        region_id=rid,
        min_age=int(min_age),
        reason=PASSIVE_OUTLAW_PROMOTION_REASON,
        source={
            **source_payload,
            "selector": "region_cohort",
            "requested_region_id": rid,
        },
    )


def open_outlaw_case_from_passive(
    ctx: "SimulationContext",
    *,
    year: int,
    offense_type: str,
    offense_kind: str,
    severity_01: float,
    knownness_01: float,
    source_event_key: str,
    passive_person_id: int | None = None,
    settlement_id: str | None = None,
    region_id: str | None = None,
    victim_person_id: int | None = None,
    target_person_id: int | None = None,
    details: dict[str, Any] | None = None,
    min_age: int = 16,
) -> SimulationOutlawCase | None:
    """Promote a passive/cohort accused person, then open a normal outlaw case."""

    if str(offense_type or "").strip() == "property_crime":
        threshold_sid = str(settlement_id or "").strip()
        threshold_rid = str(region_id or "").strip()
        if passive_person_id is not None:
            try:
                pid = int(passive_person_id)
            except (TypeError, ValueError):
                pid = 0
            existing = getattr(ctx, "id_to_record", {}).get(pid)
            if existing is not None:
                existing_sid, existing_rid = _person_residence(ctx, existing)
                threshold_sid = threshold_sid or str(existing_sid or "")
                threshold_rid = threshold_rid or str(existing_rid or "")
            else:
                prec = getattr(ctx, "passive_people", {}).get(pid)
                if prec is not None:
                    threshold_sid = threshold_sid or _passive_person_settlement_id(prec.person)
                    threshold_rid = threshold_rid or _passive_person_region_id(prec.person)
        adjusted_severity = _law_adjusted_property_severity(
            ctx,
            severity_01=severity_01,
            settlement_id=threshold_sid or None,
            region_id=threshold_rid or None,
        )
        if adjusted_severity < PROPERTY_OUTLAW_MIN_SEVERITY:
            return None
    source = {
        "outlaw_case_source_event_key": str(source_event_key or "").strip(),
        "offense_type": str(offense_type or "").strip(),
        "offense_kind": str(offense_kind or "").strip(),
    }
    accused = promote_passive_outlaw_accused(
        ctx,
        year=int(year),
        passive_person_id=passive_person_id,
        settlement_id=settlement_id,
        region_id=region_id,
        source=source,
        min_age=int(min_age),
    )
    if accused is None:
        return None
    selector = (
        "passive_person_id"
        if passive_person_id is not None
        else "settlement_cohort"
        if str(settlement_id or "").strip()
        else "region_cohort"
    )
    return open_outlaw_case(
        ctx,
        year=int(year),
        accused=accused,
        offense_type=offense_type,
        offense_kind=offense_kind,
        severity_01=severity_01,
        knownness_01=knownness_01,
        source_event_key=source_event_key,
        victim_person_id=victim_person_id,
        target_person_id=target_person_id,
        details={
            **(details or {}),
            "source_role": "passive_outlaw_case",
            "promoted_from_passive": True,
            "passive_promotion_reason": PASSIVE_OUTLAW_PROMOTION_REASON,
            "passive_selector": selector,
        },
    )


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

    base_severity = clamp01(severity_01)
    knownness = clamp01(knownness_01)
    accused_sid, accused_rid = _person_residence(ctx, accused)
    sid, rid = _case_place_from_people(
        ctx,
        accused=accused,
        victim_person_id=victim_person_id,
        target_person_id=target_person_id,
    )
    law_profile, law_details = _outlaw_law_context(
        ctx,
        settlement_id=accused_sid,
        region_id=accused_rid,
    )
    severity = clamp01(base_severity * law_profile.severity_multiplier)
    if (
        str(offense_type or "").strip() == "property_crime"
        and severity < PROPERTY_OUTLAW_MIN_SEVERITY
    ):
        return None
    buyoff = _buyoff_power(ctx, accused, offense_type)
    base_buyoff_relief = buyoff * (0.28 if offense_type == "murder" else 0.55)
    buyoff_relief = base_buyoff_relief * law_profile.buyoff_relief_multiplier
    base_pressure = clamp01(0.14 + base_severity * 0.58 + knownness * 0.32 - base_buyoff_relief)
    pressure = clamp01(
        0.14
        + severity * 0.58
        + knownness * 0.32
        - buyoff_relief
        + law_profile.pursuit_pressure_add
    )
    expected_forget_year = _forget_year(
        year=int(year),
        offense_type=offense_type,
        severity=severity,
        knownness=knownness,
        buyoff_power=buyoff,
        law_profile=law_profile,
    )
    case_details = {
        **(details or {}),
        **law_details,
        "base_severity_01": round(base_severity, 5),
        "base_pursuit_pressure_01": round(base_pressure, 5),
        "base_expected_forget_year": _forget_year(
            year=int(year),
            offense_type=offense_type,
            severity=base_severity,
            knownness=knownness,
            buyoff_power=buyoff,
            law_profile=_DEFAULT_OUTLAW_LAW_PROFILE,
        ),
    }
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
            expected_forget_year=max(
                existing.expected_forget_year or expected_forget_year,
                expected_forget_year,
            ),
            last_seen_year=int(year),
            details={**existing.details, **case_details},
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
        expected_forget_year=expected_forget_year,
        region_id=rid,
        settlement_id=sid,
        details=case_details,
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
        display_name = str(refuge.display_name or "").strip()
        if not display_name:
            display_name = outlaw_refuge_display_name(
                refuge.refuge_id,
                region_id=refuge.region_id,
                near_place_label=refuge.near_settlement_id or "",
                year=refuge.founded_year or year,
                salt=getattr(ctx, "placename_rng_salt", 0),
            )
        refuge = replace(
            refuge,
            display_name=display_name,
            band_size=int(refuge.band_size) + 1,
            last_activity_year=int(year),
        )
        ctx.outlaw_refuges[refuge.refuge_id] = refuge
        return refuge
    ordinal = len(getattr(ctx, "outlaw_refuges", {}) or {}) + 1
    refuge_id = f"outlaw_refuge:{rid}:{ordinal}"
    rng = random.Random(int(year) * 97_031 + ordinal * 109 + int(getattr(ctx, "placename_rng_salt", 0)))
    display_name = _unique_refuge_display_name(
        outlaw_refuge_display_name(
            refuge_id,
            region_id=rid,
            near_place_label=case.settlement_id or "",
            year=year,
            salt=getattr(ctx, "placename_rng_salt", 0),
        ),
        {
            str(getattr(r, "display_name", "") or "")
            for r in (getattr(ctx, "outlaw_refuges", {}) or {}).values()
        },
    )
    refuge = SimulationOutlawRefuge(
        refuge_id=refuge_id,
        region_id=rid,
        display_name=display_name,
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


def _flight_partner_break_rng(
    ctx: "SimulationContext",
    *,
    year: int,
    person_a_id: int,
    person_b_id: int,
) -> random.Random:
    lo, hi = sorted((int(person_a_id), int(person_b_id)))
    return random.Random(
        int(year) * (OUTLAW_RNG_STREAM + 907)
        + int(getattr(ctx, "placename_rng_salt", 0)) * 37
        + lo * 20_021
        + hi
    )


def _spouse_breaks_after_outlaw_flight(
    spouse: "SimulationPersonRecord",
    *,
    rng: random.Random,
) -> tuple[bool, float, list[str]]:
    """Whether the spouse ends the partnership when the accused flees."""
    traits = work_trait_values(spouse.person)
    loyalty_deviation = clamp01(abs(float(traits.get("loyalty", 0.0))) / 100.0)
    neuro_deviation = clamp01(abs(float(traits.get("neurochemical", 0.0))) / 100.0)
    reasons: list[str] = []
    if neuro_deviation >= 0.88:
        return False, 0.0, ["spouse_confusion"]
    if loyalty_deviation <= 0.12:
        return False, 0.0, ["spouse_loyalty"]
    if loyalty_deviation >= 0.92:
        return True, 1.0, ["spouse_disloyalty"]
    chance = clamp01(0.08 + loyalty_deviation * 0.72 - neuro_deviation * 0.35)
    if loyalty_deviation >= 0.45:
        reasons.append("spouse_loyalty_strain")
    if neuro_deviation >= 0.45:
        reasons.append("spouse_confusion")
    if not reasons:
        reasons.append("outlaw_flight_strain")
    return rng.random() < chance, chance, reasons


def flee_to_refuge(
    ctx: "SimulationContext",
    case_key: str,
    *,
    year: int,
    flight_reason: str | None = None,
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
    for paramour_id in list(ctx.paramour_ids_for_person(int(rec.person_id))):
        try:
            ctx.end_paramour_relationship(int(rec.person_id), int(paramour_id))
            ctx._pending_simulation_events[-1][2].update(
                {
                    "end_reason": "outlaw_flight",
                    "end_reasons": ["outlaw_flight"],
                    "flight_reason": flight_reason or "ordinary_flight",
                }
            )
        except (LookupError, ValueError):
            pass
    if rec.person.partner_person_id is not None:
        partner_id = int(rec.person.partner_person_id)
        partner = ctx.id_to_record.get(partner_id)
        try:
            if partner is not None:
                breaks, chance, reasons = _spouse_breaks_after_outlaw_flight(
                    partner,
                    rng=_flight_partner_break_rng(
                        ctx,
                        year=int(year),
                        person_a_id=int(rec.person_id),
                        person_b_id=partner_id,
                    ),
                )
                if breaks:
                    ctx.dissolve_couple(int(rec.person_id), partner_id)
                    ctx._pending_simulation_events[-1][2].update(
                        {
                            "breakup_probability": round(chance, 5),
                            "breakup_reasons": reasons,
                            "breakup_trigger": "outlaw_flight",
                        }
                    )
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
            "settlement_id": last_free or case.settlement_id,
            "from_settlement_id": last_free,
            "outlaw_refuge_id": refuge.refuge_id,
            "outlaw_refuge_display_name": refuge.display_name,
            "flight_reason": flight_reason or "ordinary_flight",
            "details": (
                "escaped custody before fleeing to outlaw refuge"
                if flight_reason == "escaped_custody"
                else "fled from ordinary settlement residence"
            ),
        },
    )
    ctx._record_simulation_event(
        int(year),
        "outlaw_refuge_joined",
        {
            "year": int(year),
            **_case_event_payload(case, event_type="outlaw_refuge_joined"),
            "settlement_id": refuge.near_settlement_id or case.settlement_id,
            "region_id": refuge.region_id or case.region_id,
            "outlaw_refuge_id": refuge.refuge_id,
            "outlaw_refuge_display_name": refuge.display_name,
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
    custody: SimulationOutlawCustody | None = None
    case = replace(
        case,
        status="resolved",
        resolved_year=int(year),
        resolution=str(resolution or "").strip() or "resolved",
    )
    if rec is not None and int(rec.person_id) in ctx.current_people_ids:
        if resolution in {"captured", "punished"}:
            sid = _return_settlement(ctx, case, rec)
            custody = _open_outlaw_custody(
                ctx,
                case,
                rec,
                year=int(year),
                site_settlement_id=sid,
            )
            case = replace(
                case,
                custody_id=custody.custody_id,
                details={
                    **(case.details or {}),
                    "custody_id": custody.custody_id,
                    "custody_type": custody.custody_type,
                    "custody_expected_release_year": custody.expected_release_year,
                },
            )
            rec.person = replace(
                rec.person,
                current_settlement_id=sid,
                outlaw_status=OUTLAW_STATUS_IMPRISONED,
                outlaw_case_key=case.case_key,
                outlaw_refuge_id=None,
                outlaw_custody_id=custody.custody_id,
                outlaw_custody_status=custody.status,
                outlaw_custody_start_year=custody.start_year,
                outlaw_custody_expected_release_year=custody.expected_release_year,
                outlaw_custody_release_year=custody.release_year,
                outlaw_custody_site_settlement_id=custody.site_settlement_id,
                housing_status="custody",
                household_role="prisoner",
                employment_status="imprisoned",
                job=None,
                job_assigned_year=None,
                job_era=None,
                job_tier=None,
                last_job=rec.person.last_job or rec.person.job,
                job_lost_year=int(year) if rec.person.job else rec.person.job_lost_year,
                job_market_type="custody",
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
                outlaw_custody_id=None,
                outlaw_custody_status=None,
                outlaw_custody_start_year=None,
                outlaw_custody_expected_release_year=None,
                outlaw_custody_release_year=None,
                outlaw_custody_site_settlement_id=None,
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
                outlaw_custody_id=None,
                outlaw_custody_status=None,
                outlaw_custody_start_year=None,
                outlaw_custody_expected_release_year=None,
                outlaw_custody_release_year=None,
                outlaw_custody_site_settlement_id=None,
            )
    ctx.outlaw_cases[case.case_key] = case
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
            **_custody_event_payload(custody),
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
    law_profile = _outlaw_law_profile_from_case(case)
    threshold = (
        (0.56 if case.offense_type == "murder" else 0.32)
        * law_profile.buyoff_threshold_multiplier
    )
    if case.buyoff_power_01 < threshold:
        return False
    chance = clamp01(
        (case.buyoff_power_01 * 0.45 - case.severity_01 * 0.16)
        * law_profile.buyoff_chance_multiplier
    )
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
            "settlement_id": refuge.near_settlement_id or case.settlement_id,
            "region_id": refuge.region_id or case.region_id,
            "outlaw_refuge_id": refuge.refuge_id,
            "outlaw_refuge_display_name": refuge.display_name,
            "near_settlement_id": refuge.near_settlement_id,
            "band_size": int(refuge.band_size),
            "raid_pressure_01": round(clamp01(case.severity_01 * 0.45 + case.pursuit_pressure_01 * 0.35), 5),
        },
    )


def _pursuit_outcome(
    case: SimulationOutlawCase,
    refuge: SimulationOutlawRefuge,
    traits: dict[str, float],
    rng: random.Random,
    law_profile: OutlawLawProfile | None = None,
) -> str:
    profile = law_profile or _outlaw_law_profile_from_case(case)
    courage = clamp01((float(traits.get("courage", 0.0)) + 100.0) / 200.0)
    discipline = clamp01((float(traits.get("discipline", 0.0)) + 100.0) / 200.0)
    capture = clamp01(
        0.28
        + case.pursuit_pressure_01 * 0.42
        - refuge.concealment_01 * 0.18
        + profile.capture_chance_add
    )
    death = clamp01(
        0.08
        + case.severity_01 * 0.20
        + courage * 0.10
        - discipline * 0.06
        + profile.death_chance_add
    )
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
        law_profile = _outlaw_law_profile_from_case(case)
        current_status = str(rec.person.outlaw_status or "").strip().lower()
        if current_status != OUTLAW_STATUS_FUGITIVE:
            if _maybe_buy_off(ctx, case, int(year), rng):
                continue
            flee_chance = clamp01(
                0.18
                + case.pursuit_pressure_01 * 0.62
                - case.buyoff_power_01 * 0.22
                + law_profile.flee_chance_add
            )
            if rng.random() < flee_chance:
                flee_to_refuge(ctx, case.case_key, year=int(year))
            continue

        if case.expected_forget_year is not None and int(year) >= int(case.expected_forget_year):
            return_chance = clamp01(
                0.28
                + case.buyoff_power_01 * 0.20
                + (1.0 - case.knownness_01) * 0.24
                + law_profile.return_chance_add
            )
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
            + law_profile.discovery_chance_add
        )
        if rng.random() >= discovery_chance:
            continue
        refuge = replace(
            refuge,
            discovered_year=refuge.discovered_year or int(year),
            last_activity_year=int(year),
        )
        ctx.outlaw_refuges[refuge.refuge_id] = refuge
        ctx._record_simulation_event(
            int(year),
            "outlaw_pursuit",
            {
                "year": int(year),
                **_case_event_payload(case, event_type="outlaw_pursuit"),
                "settlement_id": refuge.near_settlement_id or case.settlement_id,
                "region_id": refuge.region_id or case.region_id,
                "outlaw_refuge_id": refuge.refuge_id,
                "outlaw_refuge_display_name": refuge.display_name,
                "band_size": int(refuge.band_size),
                "discovery_chance_01": round(discovery_chance, 5),
            },
        )
        outcome = _pursuit_outcome(
            case,
            refuge,
            work_trait_values(rec.person),
            rng,
            law_profile,
        )
        if outcome == "killed":
            kill_outlaw(ctx, case.case_key, year=int(year))
        elif outcome == "captured":
            resolve_outlaw_case(ctx, case.case_key, year=int(year), resolution="captured")
    _process_active_custodies(ctx, int(year))
