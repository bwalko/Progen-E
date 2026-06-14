"""Person record produced by the people generator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Person:
    """A generated individual: identity, culture, body metrics, birth year, and genome.

    ``genome`` maps trait name → deviation from ideal (0 is best). Typical
    magnitudes cluster around ±50; small |value| is unusually good, large |value|
    unusually poor. ``sexual_nature`` / ``gender_mind`` are lowercase labels
    from the matching config tables (see ``choose_sexual_nature`` /
    ``choose_gender_mind``). ``birthyear`` is the calendar year of birth;
    implied age at a simulation year is ``simulation_year - birthyear``.

    ``maturity_height_cm`` and ``maturity_weight_kg`` are the rolled **mature**
    stature targets (adult height and matching BMI-derived weight), even when
    ``life_stage`` is still ``child``; use :func:`library.body_interpreter.interpret_physique`
    for age-scaled display metrics before maturity.
    """

    first_name: str
    last_name: str
    gender: str
    ethnic: str
    species: str
    birthyear: int
    deathyear: int | None = None
    birthplace: str = "Placeholder"
    birthplace_region_id: str | None = None
    birthplace_settlement_id: str | None = None
    current_settlement_id: str | None = None
    partner_person_id: int | None = None
    paramour_person_id: int | None = None
    last_birth_event_year: int | None = None
    job: str | None = None
    job_assigned_year: int | None = None
    job_era: str | None = None
    job_tier: str | None = None
    job_market_type: str | None = None
    housing_status: str | None = None
    household_role: str | None = None
    host_person_id: int | None = None
    employer_person_id: int | None = None
    social_class_band: str | None = None
    social_standing_01: float | None = None
    societal_impact_01: float | None = None
    perceived_worth_01: float | None = None
    status_tendency: str | None = None
    leader_quality: str | None = None
    leader_tendency: str | None = None
    outlaw_status: str | None = None
    outlaw_case_key: str | None = None
    outlaw_refuge_id: str | None = None
    outlaw_since_year: int | None = None
    last_free_settlement_id: str | None = None
    outlaw_custody_id: str | None = None
    outlaw_custody_status: str | None = None
    outlaw_custody_start_year: int | None = None
    outlaw_custody_expected_release_year: int | None = None
    outlaw_custody_release_year: int | None = None
    outlaw_custody_site_settlement_id: str | None = None
    employment_status: str | None = None
    job_lost_year: int | None = None
    unemployment_started_year: int | None = None
    last_job: str | None = None
    career_fitness_score: float | None = None
    # Last annual wage-based prosperity from ``simulation_economy`` (0..1); None before first tick.
    job_prosperity_01: float | None = None
    # Household savings carried by the implicit co-resident household.
    household_prosperity: float | None = None
    household_purseholder_person_id: int | None = None
    birth_litter_size: int = 1
    life_stage: str | None = None
    maturity_height_cm: float | None = None
    maturity_weight_kg: float | None = None
    skin_tone: str | None = None
    hair: str | None = None
    eyes: str | None = None
    min_fertility_age: int | None = None
    max_fertility_age: int | None = None
    genome: dict[str, float] = field(default_factory=dict)
    # Current physiology / cognition (mutable); jobs and attractiveness read this.
    # Initialized as a full copy of ``genome`` at birth; genome stays fixed for mating.
    mind_body: dict[str, float] = field(default_factory=dict)
    # Cached 0..1; includes elderly penalty on the rating only (symmetry value unchanged).
    attractiveness_01: float | None = None
    genome_composite_names: tuple[str, ...] = ()
    genome_trait_phrases: tuple[str, ...] = ()
    sexual_nature: str | None = None
    gender_mind: str | None = None
    father_name: str | None = None
    mother_name: str | None = None

    @property
    def full_name(self) -> str:
        ln = (self.last_name or "").strip()
        fn = (self.first_name or "").strip()
        if not ln:
            return fn
        return f"{fn} {ln}"
