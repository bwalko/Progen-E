"""Person generation entry points."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from library.geography import choose_birth_region_id, get_region
from library.offspring_genome import generate_offspring_genome
from library.mind_body import attractiveness_01, mind_body_from_genome
from library.person import Person
from library.random_names import choose_random_first_last, choose_random_first_last_from_birth
from library.random_traits import (
    DEFAULT_DB_PATH,
    DEFAULT_ELDER_AGE_SKEW,
    choose_eyes,
    choose_gender,
    choose_genome,
    choose_gender_mind,
    choose_hair,
    choose_sexual_nature,
    choose_life_stage_and_age,
    choose_mature_height_cm,
    choose_skin,
    choose_species_row,
    species_ethnic_exists,
    choose_weight_kg,
)
from library.world_paths import derive_save_db_path_from_config
from library.world_time import resolve_world_current_year

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


def _with_default_residence(person: Person) -> Person:
    if person.current_settlement_id is None and person.birthplace_settlement_id:
        return replace(person, current_settlement_id=person.birthplace_settlement_id)
    return person


def _resolve_simulation_calendar_year(
    *,
    simulation_year: int | None,
    path: Path,
    world: str,
    simulation_context: "SimulationContext | None",
) -> int:
    if simulation_year is not None:
        return int(simulation_year)
    if simulation_context is not None:
        return resolve_world_current_year(
            config_db_path=path,
            save_db_path=simulation_context.save_db_path,
            world=world,
        )
    return resolve_world_current_year(
        config_db_path=path,
        save_db_path=derive_save_db_path_from_config(path),
        world=world,
    )


# Parent species pairs that may yield a half-species row in ``config/species.csv``.
_OFFSPRING_HALF_BY_PARENTS: dict[frozenset[str], str] = {
    frozenset({"Human", "Dwarf"}): "Half-Dwarf",
    frozenset({"Human", "Gnome"}): "Half-Gnome",
    frozenset({"Human", "Goblin"}): "Half-Orc",
}


def _species_ethnic_exists(
    _conn: sqlite3.Connection, species: str, ethnic: str, *, db_path: Path | str | None
) -> bool:
    return species_ethnic_exists(species=species, ethnic=ethnic, db_path=db_path)


def _pick_offspring_ethnic(parent_a: Person, parent_b: Person) -> str:
    ea = (parent_a.ethnic or "").strip()
    eb = (parent_b.ethnic or "").strip()
    if not ea and not eb:
        return ""
    if ea == eb or not eb:
        return ea
    if not ea:
        return eb
    return random.choice((ea, eb))


def _resolve_offspring_species(
    conn: sqlite3.Connection | None,
    parent_a: Person,
    parent_b: Person,
    child_ethnic: str,
    *,
    db_path: Path | str | None,
) -> str:
    sa = (parent_a.species or "").strip()
    sb = (parent_b.species or "").strip()
    ce = (child_ethnic or "").strip()
    if not sa or not sb:
        raise ValueError("Each parent must have a species for offspring resolution.")
    candidates: list[str]
    if sa == sb:
        candidates = [sa]
    else:
        candidates = [sa, sb]
        half = _OFFSPRING_HALF_BY_PARENTS.get(frozenset({sa, sb}))
        if half:
            candidates.append(half)
    valid = [
        sp
        for sp in candidates
        if _species_ethnic_exists(conn, sp, ce, db_path=db_path)
    ]
    if not valid:
        raise LookupError(
            f"No species row for ethnic={ce!r} among offspring candidates {candidates!r} "
            f"(parents {sa!r} + {sb!r})."
        )
    return random.choice(valid)


def _parent_appearance_pool(
    trait: str, parent_a: Person, parent_b: Person
) -> list[str]:
    if trait == "skin":
        vals = (parent_a.skin_tone, parent_b.skin_tone)
    elif trait == "hair":
        vals = (parent_a.hair, parent_b.hair)
    else:
        vals = (parent_a.eyes, parent_b.eyes)
    out: list[str] = []
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out.append(s)
    return out


def _offspring_appearance(
    trait: str,
    species_row: sqlite3.Row,
    gender: str,
    parent_a: Person,
    parent_b: Person,
    override: str | None,
    chooser,
) -> str:
    if override is not None:
        return override
    if random.random() < 0.1:
        return chooser(species_row, gender)
    pool = _parent_appearance_pool(trait, parent_a, parent_b)
    if not pool:
        return chooser(species_row, gender)
    return random.choice(pool)


def _resolve_birthyear(
    *,
    birthyear: int | None,
    birth_reference_year: int | None,
    current_year: int,
    age: int,
) -> int:
    """``birthyear`` wins; else ``birth_reference_year - age`` (anchor defaults to ``current_year``)."""
    if birthyear is not None:
        return int(birthyear)
    anchor = (
        int(birth_reference_year)
        if birth_reference_year is not None
        else int(current_year)
    )
    return anchor - int(age)


def _defer_adult_profile_for_age(age: int, min_fertility_age: int | None) -> bool:
    """Keep children lean; adult phenotype caches materialize at job eligibility."""
    threshold = int(min_fertility_age) if min_fertility_age is not None else 18
    return int(age) < threshold


def _initial_adult_profile_fields(
    genome: dict[str, float],
    *,
    age: int,
    min_fertility_age: int | None,
    birthyear: int,
    max_fertility_age: int | None,
) -> tuple[dict[str, float], float | None]:
    if _defer_adult_profile_for_age(age, min_fertility_age):
        return {}, None
    mb = mind_body_from_genome(genome)
    probe = Person(
        first_name="",
        last_name="",
        gender="",
        ethnic="",
        species="",
        birthyear=int(birthyear),
        max_fertility_age=max_fertility_age,
        genome=genome,
        mind_body=mb,
    )
    return mb, round(attractiveness_01(probe, int(birthyear) + int(age)), 5)


def _birthplace_display_label(
    *,
    region_id: str,
    simulation_context: "SimulationContext | None",
    db_path: Path | str | None,
    world: str,
    settlement_id: str | None = None,
) -> str:
    """Human-readable place label for names — never raw ``region_id:level`` ids."""
    rid = (region_id or "").strip()
    if not rid:
        return "Unknown"
    if simulation_context is not None:
        sid_key = (settlement_id or "").strip()
        if sid_key:
            st = simulation_context.settlements_by_id.get(sid_key)
            if st is not None and (st.status or "").strip().lower() == "active":
                if (st.display_name or "").strip():
                    return (st.display_name or "").strip()
                if (st.region_display_name or "").strip():
                    return (st.region_display_name or "").strip()
        active_here = simulation_context.active_settlements_in_region(rid)
        if active_here:
            st0 = active_here[0]
            if (st0.display_name or "").strip():
                return (st0.display_name or "").strip()
            if (st0.region_display_name or "").strip():
                return (st0.region_display_name or "").strip()
    try:
        reg = get_region(rid, world=world, db_path=db_path)
        rn = (reg.region_name or "").strip()
        if rn:
            return rn
    except LookupError:
        pass
    return rid


def _resolve_birthplace(
    *,
    birthplace: str | None,
    birthplace_region_id: str | None,
    birthplace_settlement_id: str | None,
    simulation_context: "SimulationContext | None",
    world: str,
    db_path: Path | str | None,
    spin_off_for_offspring: bool = False,
    spin_off_seed: int = 0,
    allow_secondary_settlement_spinoff: bool = True,
) -> tuple[str, str | None, str | None]:
    # Replace legacy ``region_id:hamlet`` tokens with a display name for hails/locatives.
    bp_effective = (birthplace or "").strip()
    if bp_effective and birthplace_region_id is None and ":" in bp_effective:
        left, lev = bp_effective.split(":", 1)
        if lev.strip().lower() in ("hamlet", "town", "city") and left.strip():
            bp_effective = _birthplace_display_label(
                region_id=left.strip(),
                simulation_context=simulation_context,
                db_path=db_path,
                world=world,
            )

    if birthplace_region_id is not None:
        rid = birthplace_region_id.strip()
        sid = (birthplace_settlement_id or "").strip() or None
        if simulation_context is not None:
            st = simulation_context.resolve_settlement_for_birth(rid, sid)
            rid = st.region_id
            sid = st.settlement_id
            if spin_off_for_offspring and allow_secondary_settlement_spinoff:
                rng = random.Random(
                    int(spin_off_seed)
                    ^ int(simulation_context.placename_rng_salt)
                    ^ int(
                        simulation_context.current_year
                        or simulation_context.simulation_start_year
                    )
                )
                rid, sid = simulation_context.maybe_spin_off_birth_settlement(rid, sid, rng)
            st_disp = simulation_context.settlements_by_id.get((sid or "").strip())
            human = (
                (st_disp.display_name or "").strip()
                if st_disp is not None
                else _birthplace_display_label(
                    region_id=rid,
                    simulation_context=simulation_context,
                    db_path=db_path,
                    world=world,
                    settlement_id=sid,
                )
            )
        else:
            human = _birthplace_display_label(
                region_id=rid,
                simulation_context=simulation_context,
                db_path=db_path,
                world=world,
                settlement_id=sid,
            )
        label = bp_effective or human
        return (label or human), rid or None, sid

    if simulation_context is not None and simulation_context.settlements_by_id:
        states = [
            st
            for st in simulation_context.settlements_by_id.values()
            if (st.status or "").strip().lower() == "active"
        ]
        if states:
            weights: list[int] = []
            for s in states:
                w = max(1, int(s.resident_count))
                if w <= 1:
                    try:
                        gr = get_region(s.region_id, world=world, db_path=db_path)
                        w = max(10, int(gr.carrying_capacity) // 500)
                    except LookupError:
                        w = 10
                weights.append(w)
            picked = random.choices(states, weights=weights, k=1)[0]
            rid = picked.region_id
            sid = picked.settlement_id
            human = (picked.display_name or "").strip() or _birthplace_display_label(
                region_id=rid,
                simulation_context=simulation_context,
                db_path=db_path,
                world=world,
                settlement_id=sid,
            )
            label = bp_effective or human
            return (label or human), rid, sid

    rid = choose_birth_region_id(world=world, db_path=db_path)
    if simulation_context is not None:
        st = simulation_context.ensure_active_settlement_for_region(rid)
        sid = st.settlement_id
        human = (st.display_name or "").strip() or _birthplace_display_label(
            region_id=rid,
            simulation_context=simulation_context,
            db_path=db_path,
            world=world,
            settlement_id=sid,
        )
        label = bp_effective or human
        return (label or human), rid, sid
    sid = f"{rid}:hamlet"
    human = _birthplace_display_label(
        region_id=rid,
        simulation_context=simulation_context,
        db_path=db_path,
        world=world,
    )
    label = bp_effective or human
    return (label or human), rid, sid


def _choose_max_fertility_age(species_row: sqlite3.Row, gender: str) -> int | None:
    """Assign a max fertility age: females get middleaged ±5; males are unrestricted."""
    if (gender or "").strip().lower() != "female":
        return None
    middleaged = int(species_row["middleaged"])
    rolled = middleaged + random.randint(-5, 5)
    return max(0, rolled)


def _choose_min_fertility_age(species_row: sqlite3.Row) -> int:
    """Assign min fertility age from species maturity ±2 years."""
    maturity = int(species_row["maturity"])
    rolled = maturity + random.randint(-2, 2)
    return max(0, rolled)


def _choose_fertility_ages(species_row: sqlite3.Row, gender: str) -> tuple[int, int | None]:
    """Return fertility age bounds to persist on Person at creation time."""
    return (
        _choose_min_fertility_age(species_row),
        _choose_max_fertility_age(species_row, gender),
    )


def _father_for_naming(parent_a: Person, parent_b: Person) -> Person:
    """Pick a father record for surname inheritance rules in birth naming."""
    a_male = (parent_a.gender or "").strip().lower() == "male"
    b_male = (parent_b.gender or "").strip().lower() == "male"
    if a_male and not b_male:
        return parent_a
    if b_male and not a_male:
        return parent_b
    return parent_a


__all__ = [
    "DEFAULT_DB_PATH",
    "generate_offspring_genome",
    "generate_person_from_birth",
    "generate_person_random",
]


def generate_person_random(
    *,
    species: str | None = None,
    ethnic: str | None = None,
    gender: str | None = None,
    life_stage: str | None = None,
    age: int | None = None,
    maturity_height_cm: float | None = None,
    maturity_weight_kg: float | None = None,
    skin: str | None = None,
    hair: str | None = None,
    eyes: str | None = None,
    genome: dict[str, float] | None = None,
    sexual_nature: str | None = None,
    gender_mind: str | None = None,
    db_path: Path | str | None = None,
    world: str = "default",
    elder_skew: float = DEFAULT_ELDER_AGE_SKEW,
    simulation_year: int | None = None,
    birth_reference_year: int | None = None,
    birthyear: int | None = None,
    birthplace: str | None = None,
    birthplace_region_id: str | None = None,
    birthplace_settlement_id: str | None = None,
    simulation_context: "SimulationContext | None" = None,
) -> Person:
    """Build a person using purely random selection from project data.

    Any keyword other than ``db_path``, ``world``, ``elder_skew``,
    ``simulation_year``, ``birth_reference_year``, or ``birthyear`` acts as a
    hard override and bypasses the corresponding random helper (including
    species-rate filtering). Life stage and age use cohort weights from
    ``world_start`` + ``species`` unless ``life_stage`` and/or ``age`` are set.
    Genome traits are rolled from the ``genome`` table unless ``genome`` is
    passed explicitly. ``sexual_nature`` and ``gender_mind`` are inferred from
    ``sexual_nature`` / ``gender_mind`` definition tables vs genome unless
    overridden.

    ``simulation_year`` is the world's **current calendar year** (defaults to
    ``world_start.start_year`` for ``world``). By default ``birthyear`` is
    ``current_year - age``. Set ``birth_reference_year`` to anchor the subtraction
    to a different year (same age semantics), or pass ``birthyear`` explicitly
    when someone was born in the past but only appears in the current year while
    keeping rolled ``age`` / life stage aligned to that story.
    """
    if simulation_context is not None:
        path = Path(simulation_context.db_path)
        world = simulation_context.world
    else:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    current_year = _resolve_simulation_calendar_year(
        simulation_year=simulation_year,
        path=path,
        world=world,
        simulation_context=simulation_context,
    )

    species_row = choose_species_row(species=species, ethnic=ethnic, db_path=path)
    chosen_gender = choose_gender(gender=gender)
    chosen_stage, chosen_age = choose_life_stage_and_age(
        species_row,
        life_stage=life_stage,
        age=age,
        db_path=path,
        world=world,
        elder_skew=elder_skew,
    )
    chosen_height = choose_mature_height_cm(
        species_row, chosen_gender, height_cm=maturity_height_cm
    )
    chosen_weight = choose_weight_kg(
        chosen_height,
        weight_kg=maturity_weight_kg,
        species_row=species_row,
        gender=chosen_gender,
    )
    chosen_skin = choose_skin(species_row, chosen_gender, skin=skin)
    chosen_hair = choose_hair(species_row, chosen_gender, hair=hair)
    chosen_eyes = choose_eyes(species_row, chosen_gender, eyes=eyes)
    chosen_genome = choose_genome(chosen_gender, genome=genome, db_path=path)
    chosen_sexual_nature = choose_sexual_nature(
        chosen_genome, sexual_nature=sexual_nature, db_path=path
    )
    chosen_gender_mind = choose_gender_mind(
        chosen_genome,
        chosen_gender,
        gender_mind=gender_mind,
        db_path=path,
    )
    ethnic_s = (species_row["ethnic"] or "").strip()
    resolved_birthplace, resolved_region_id, resolved_settlement_id = _resolve_birthplace(
        birthplace=birthplace,
        birthplace_region_id=birthplace_region_id,
        birthplace_settlement_id=birthplace_settlement_id,
        simulation_context=simulation_context,
        world=world,
        db_path=path,
    )
    first, last = choose_random_first_last(
        ethnic=ethnic_s,
        gender=chosen_gender,
        birthplace=resolved_birthplace,
        db_path=path,
        birthplace_region_id=resolved_region_id,
        world=world,
        simulation_context=simulation_context,
    )
    resolved_birthyear = _resolve_birthyear(
        birthyear=birthyear,
        birth_reference_year=birth_reference_year,
        current_year=current_year,
        age=int(chosen_age),
    )
    chosen_min_fertility_age, chosen_max_fertility_age = _choose_fertility_ages(
        species_row, chosen_gender
    )
    mb, initial_attractiveness = _initial_adult_profile_fields(
        chosen_genome,
        age=int(chosen_age),
        min_fertility_age=chosen_min_fertility_age,
        max_fertility_age=chosen_max_fertility_age,
        birthyear=resolved_birthyear,
    )
    base_person = Person(
        first_name=first,
        last_name=last,
        gender=chosen_gender,
        ethnic=ethnic_s,
        species=(species_row["species"] or "").strip(),
        birthplace=resolved_birthplace,
        birthplace_region_id=resolved_region_id,
        birthplace_settlement_id=resolved_settlement_id,
        current_settlement_id=resolved_settlement_id,
        birthyear=resolved_birthyear,
        life_stage=chosen_stage,
        maturity_height_cm=chosen_height,
        maturity_weight_kg=chosen_weight,
        skin_tone=chosen_skin or None,
        hair=chosen_hair or None,
        eyes=chosen_eyes or None,
        min_fertility_age=chosen_min_fertility_age,
        max_fertility_age=chosen_max_fertility_age,
        genome=chosen_genome,
        mind_body=mb,
        sexual_nature=chosen_sexual_nature,
        gender_mind=chosen_gender_mind,
    )
    if initial_attractiveness is not None:
        base_person = replace(base_person, attractiveness_01=initial_attractiveness)
    return _with_default_residence(base_person)


def generate_person_from_birth(
    parent_a: Person,
    parent_b: Person,
    *,
    db_path: Path | str | None = None,
    world: str = "default",
    simulation_year: int | None = None,
    birth_reference_year: int | None = None,
    birthyear: int | None = None,
    gender: str | None = None,
    life_stage: str | None = None,
    age: int | None = None,
    maturity_height_cm: float | None = None,
    maturity_weight_kg: float | None = None,
    skin: str | None = None,
    hair: str | None = None,
    eyes: str | None = None,
    sexual_nature: str | None = None,
    gender_mind: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    birthplace: str | None = None,
    birthplace_region_id: str | None = None,
    birthplace_settlement_id: str | None = None,
    elder_skew: float = DEFAULT_ELDER_AGE_SKEW,
    simulation_context: "SimulationContext | None" = None,
    birth_litter_size: int = 1,
    allow_secondary_settlement_spinoff: bool = True,
    mother_person_id: int | None = None,
    surname_convention: str | None = None,
) -> Person:
    """Build a person from two parents.

    ``birthplace`` should be the mother's current location at birth.
    If omitted, geography-backed region/settlement IDs are selected.

    Child ``ethnic`` is chosen uniformly from the parents' ethnics (or the
    sole value if they match). Child ``species`` is one of the parents'
    species, or a configured half-species when the parent species pair matches
    and a ``(species, ethnic)`` row exists in ``species`` for the child's ethnic.

    Skin, hair, and eyes each independently use a parent's value with 90%
    probability (random parent among those with that trait set), or with 10%
    probability a fresh draw from the child's species appearance pools
    (same helpers as random generation).

    Genome is digit-mixed from parents via :func:`generate_offspring_genome`.
    Gender, life stage, age,
    height, weight, names, ``sexual_nature``, and ``gender_mind`` follow the
    same rules as :func:`generate_person_random`, including ``birthyear`` (see
    that function for ``simulation_year``, ``birth_reference_year``, and
    ``birthyear``).
    """
    if simulation_context is not None:
        path = Path(simulation_context.db_path)
        world = simulation_context.world
    else:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    current_year = _resolve_simulation_calendar_year(
        simulation_year=simulation_year,
        path=path,
        world=world,
        simulation_context=simulation_context,
    )

    child_ethnic = _pick_offspring_ethnic(parent_a, parent_b)
    if not child_ethnic:
        raise ValueError("Parents must have ethnic set for offspring generation.")

    child_species = _resolve_offspring_species(
        None, parent_a, parent_b, child_ethnic, db_path=path
    )

    species_row = choose_species_row(
        species=child_species, ethnic=child_ethnic, db_path=path
    )
    ethnic_s = (species_row["ethnic"] or "").strip()
    chosen_gender = choose_gender(gender=gender)
    chosen_stage, chosen_age = choose_life_stage_and_age(
        species_row,
        life_stage=life_stage,
        age=age,
        db_path=path,
        world=world,
        elder_skew=elder_skew,
    )

    child_genome = generate_offspring_genome(parent_a, parent_b)
    chosen_sexual_nature = choose_sexual_nature(
        child_genome, sexual_nature=sexual_nature, db_path=path
    )
    chosen_gender_mind = choose_gender_mind(
        child_genome,
        chosen_gender,
        gender_mind=gender_mind,
        db_path=path,
    )

    chosen_height = choose_mature_height_cm(
        species_row, chosen_gender, height_cm=maturity_height_cm
    )
    chosen_weight = choose_weight_kg(
        chosen_height,
        weight_kg=maturity_weight_kg,
        species_row=species_row,
        gender=chosen_gender,
    )
    chosen_skin = _offspring_appearance(
        "skin", species_row, chosen_gender, parent_a, parent_b, skin, choose_skin
    )
    chosen_hair = _offspring_appearance(
        "hair", species_row, chosen_gender, parent_a, parent_b, hair, choose_hair
    )
    chosen_eyes = _offspring_appearance(
        "eyes", species_row, chosen_gender, parent_a, parent_b, eyes, choose_eyes
    )

    resolved_birthplace, resolved_region_id, resolved_settlement_id = _resolve_birthplace(
        birthplace=birthplace,
        birthplace_region_id=birthplace_region_id,
        birthplace_settlement_id=birthplace_settlement_id,
        simulation_context=simulation_context,
        world=world,
        db_path=path,
        spin_off_for_offspring=True,
        spin_off_seed=hash(
            (
                parent_a.birthplace_settlement_id,
                parent_b.birthplace_settlement_id,
                parent_a.birthyear,
                parent_b.birthyear,
            )
        ),
        allow_secondary_settlement_spinoff=allow_secondary_settlement_spinoff,
    )

    if first_name is not None and last_name is not None:
        first, last = first_name, last_name
    else:
        father = _father_for_naming(parent_a, parent_b)
        first, last = choose_random_first_last_from_birth(
            ethnic=ethnic_s,
            gender=chosen_gender,
            birthplace=resolved_birthplace,
            father_last_name=father.last_name,
            father_ethnic=father.ethnic,
            father_first_name=father.first_name,
            surname_convention=surname_convention,
            db_path=path,
            birthplace_region_id=resolved_region_id,
            world=world,
            simulation_context=simulation_context,
        )

    resolved_birthyear = _resolve_birthyear(
        birthyear=birthyear,
        birth_reference_year=birth_reference_year,
        current_year=current_year,
        age=int(chosen_age),
    )
    chosen_min_fertility_age, chosen_max_fertility_age = _choose_fertility_ages(
        species_row, chosen_gender
    )

    bls = max(1, min(3, int(birth_litter_size)))
    mother = (
        parent_a
        if (parent_a.gender or "").strip().lower() == "female"
        else parent_b
    )
    pre_sid = (
        (mother.current_settlement_id or mother.birthplace_settlement_id or "").strip()
    )
    post_sid = (resolved_settlement_id or "").strip()
    if (
        simulation_context is not None
        and mother_person_id is not None
        and allow_secondary_settlement_spinoff
        and pre_sid
        and post_sid
        and pre_sid != post_sid
    ):
        simulation_context.relocate_birthing_household_to_settlement(
            mother_person_id, post_sid
        )
    mb, initial_attractiveness = _initial_adult_profile_fields(
        child_genome,
        age=int(chosen_age),
        min_fertility_age=chosen_min_fertility_age,
        max_fertility_age=chosen_max_fertility_age,
        birthyear=resolved_birthyear,
    )
    child = Person(
        first_name=first,
        last_name=last,
        gender=chosen_gender,
        ethnic=ethnic_s,
        species=(species_row["species"] or "").strip(),
        birthplace=resolved_birthplace,
        birthplace_region_id=resolved_region_id,
        birthplace_settlement_id=resolved_settlement_id,
        current_settlement_id=resolved_settlement_id,
        birthyear=resolved_birthyear,
        birth_litter_size=bls,
        life_stage=chosen_stage,
        maturity_height_cm=chosen_height,
        maturity_weight_kg=chosen_weight,
        skin_tone=chosen_skin or None,
        hair=chosen_hair or None,
        eyes=chosen_eyes or None,
        min_fertility_age=chosen_min_fertility_age,
        max_fertility_age=chosen_max_fertility_age,
        genome=child_genome,
        mind_body=mb,
        sexual_nature=chosen_sexual_nature,
        gender_mind=chosen_gender_mind,
    )
    if initial_attractiveness is not None:
        child = replace(child, attractiveness_01=initial_attractiveness)
    return _with_default_residence(child)
