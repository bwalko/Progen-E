"""Lightweight population records for hybrid-scale simulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PassivePerson:
    """Minimal persisted facts for a real person outside detailed annual simulation."""

    name: str
    birthyear: int
    deathyear: int | None = None
    gender: str = ""
    birthplace_region_id: str | None = None
    birthplace_settlement_id: str | None = None
    current_settlement_id: str | None = None
    job_family: str | None = None
    partner_person_id: int | None = None
    father_id: int | None = None
    mother_id: int | None = None
    child_count: int = 0
    status_bucket: str | None = None
    prosperity_bucket: str | None = None


@dataclass(frozen=True)
class PassivePersonRecord:
    """Runtime wrapper for a passive person row sharing the global person id space."""

    person_id: int
    person: PassivePerson


@dataclass(frozen=True)
class PassiveCohort:
    """Aggregate demographic bucket for people too numerous to materialize one by one."""

    sim_year: int
    population_count: int
    birth_count: int = 0
    death_count: int = 0
    region_id: str | None = None
    settlement_id: str | None = None
    age_band: str = ""
    gender: str = ""
    species: str = ""
    culture: str = ""
    job_family: str = ""
    status_bucket: str = ""
