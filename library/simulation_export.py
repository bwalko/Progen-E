"""Split birth/founding snapshots vs evolving simulation state for JSON exports."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from library.person import Person
from library.settlements import SettlementState
from library.simulation_context import SimulationPersonRecord

# Simulation mutates these on ``Person``; the rest mirror creation/birth genetics & identity.
PERSON_SIMULATION_OVERLAY_FIELDS: frozenset[str] = frozenset(
    {
        "deathyear",
        "current_settlement_id",
        "partner_person_id",
        "paramour_person_id",
        "last_birth_event_year",
        "life_stage",
        "job",
        "job_assigned_year",
        "job_era",
        "job_tier",
        "status_tendency",
        "leader_quality",
        "leader_tendency",
        "employment_status",
        "job_lost_year",
        "unemployment_started_year",
        "last_job",
        "career_fitness_score",
        "job_prosperity_01",
        "household_prosperity",
        "household_purseholder_person_id",
    }
)


def split_person_for_export(
    person: Person, *, as_of_simulation_year: int | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(at_creation_or_birth, current_simulation_state)`` payloads.

    When ``as_of_simulation_year`` is set, ``current_simulation_state`` includes
    ``age`` (years) as of that simulation year, capped by ``deathyear`` when present.
    """
    raw = asdict(person)
    fixed = {
        k: raw[k]
        for k in raw
        if k not in PERSON_SIMULATION_OVERLAY_FIELDS
    }
    overlay: dict[str, Any] = {k: raw[k] for k in PERSON_SIMULATION_OVERLAY_FIELDS}
    if as_of_simulation_year is not None:
        by = int(person.birthyear)
        end_y = int(as_of_simulation_year)
        dy = person.deathyear
        if dy is not None:
            end_y = min(end_y, int(dy))
        overlay["age"] = max(0, end_y - by)
    return fixed, overlay


def simulation_person_record_to_export_dict(
    rec: SimulationPersonRecord, *, as_of_simulation_year: int | None = None
) -> dict[str, Any]:
    """Serialize one person for reports: genealogy + immutable origin + runtime overlay."""
    origin, overlay = split_person_for_export(
        rec.person, as_of_simulation_year=as_of_simulation_year
    )
    return {
        "person_id": rec.person_id,
        "is_founder": rec.is_founder,
        "father_id": rec.father_id,
        "mother_id": rec.mother_id,
        "at_creation_or_birth": origin,
        "current_simulation_state": overlay,
    }


def _parse_local_geography(local_geography_json: str | None) -> dict[str, Any] | list[Any] | str | None:
    raw = local_geography_json
    if raw is None or not str(raw).strip():
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, (dict, list)) else raw
    except json.JSONDecodeError:
        return raw


def settlement_state_to_export_dict(state: SettlementState) -> dict[str, Any]:
    """Split naming / founding layout from evolving civic and census metrics."""
    at_founding: dict[str, Any] = {
        "settlement_id": state.settlement_id,
        "region_id": state.region_id,
        "region_display_name": state.region_display_name,
        "site_slot": state.site_slot,
        "founded_sim_year": state.founded_sim_year,
        "display_name": state.display_name,
        "etymology": state.etymology,
        "name_category_primary": state.name_category_primary,
        "name_category_secondary": state.name_category_secondary,
        "name_culture_primary": state.name_culture_primary,
        "name_culture_secondary": state.name_culture_secondary,
        "local_geography": _parse_local_geography(state.local_geography_json),
    }
    current: dict[str, Any] = {
        "level": state.level,
        "resident_count": state.resident_count,
        "household_cap": state.household_cap,
        "food_pressure": state.food_pressure,
        "prosperity_pool": float(getattr(state, "prosperity_pool", 1.0)),
        "stability": state.stability,
        "market_pull": state.market_pull,
        "status": state.status,
        "abandoned_sim_year": state.abandoned_sim_year,
        "consecutive_empty_years": state.consecutive_empty_years,
    }
    return {"at_founding": at_founding, "current_simulation_state": current}


def settlements_geo_export_payload(
    settlements_by_id: dict[str, SettlementState],
) -> dict[str, dict[str, Any]]:
    """Map ``settlement_id`` → `{ at_founding, current_simulation_state }`."""
    return {
        sid: settlement_state_to_export_dict(st)
        for sid, st in sorted(settlements_by_id.items())
    }


def people_export_payload(
    people: Sequence[SimulationPersonRecord],
    *,
    random_seed: int,
    simulation_start_year: int,
    simulation_end_year_exclusive: int,
    as_of_simulation_year: int | None = None,
) -> dict[str, Any]:
    """Top-level object for population JSON export."""
    snapshot_year = (
        int(as_of_simulation_year)
        if as_of_simulation_year is not None
        else int(simulation_end_year_exclusive) - 1
    )
    return {
        "format": {
            "at_creation_or_birth": (
                "Fields fixed when the Person was generated (identity, birthplace, genome, litter size, …)."
            ),
            "current_simulation_state": (
                "Fields updated during the run (death, residence, life stage, age at export snapshot, "
                "fertility-year marker, partnerships, employment)."
            ),
        },
        "random_seed": int(random_seed),
        "simulation_start_year": int(simulation_start_year),
        "simulation_end_year_exclusive": int(simulation_end_year_exclusive),
        "as_of_simulation_year": snapshot_year,
        "people": [
            simulation_person_record_to_export_dict(
                rec, as_of_simulation_year=snapshot_year
            )
            for rec in people
        ],
    }
