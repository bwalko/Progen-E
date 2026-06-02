"""Derived leadership / governance / military quality indices from genome composites."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Mapping

from library.genome_composites import (
    normalize_composite_band,
    score_composite_row_for_traits,
)
from library.mind_body import work_trait_values

if TYPE_CHECKING:
    from library.person import Person

_GOVERNANCE_FAMILIES: frozenset[str] = frozenset(
    {
        "leadership_governance",
        "leadership_politics",
        "leadership_service",
        "political_leadership",
        "governance_social",
    }
)
_MILITARY_FAMILIES: frozenset[str] = frozenset(
    {
        "leadership_military",
        "leadership_combat",
        "leadership_coercion",
    }
)
_KNOWLEDGE_FAMILIES: frozenset[str] = frozenset(
    {
        "leadership_knowledge",
        "leadership_governance",
        "governance_social",
    }
)
_JUDICIAL_FAMILIES: frozenset[str] = frozenset(
    {
        "leadership_governance",
        "political_leadership",
    }
)

_GOVERNMENT_CANDIDATE_FAMILIES: frozenset[str] = (
    _GOVERNANCE_FAMILIES | _MILITARY_FAMILIES
)
_COMPONENT_KEYS: tuple[tuple[str, str], ...] = (
    ("component_1_trait", "component_1_position"),
    ("component_2_trait", "component_2_position"),
    ("component_3_trait", "component_3_position"),
)
_DISQUALIFIER_KEYS: tuple[tuple[str, str], ...] = (
    ("disqualifier_1_trait", "disqualifier_1_position"),
    ("disqualifier_2_trait", "disqualifier_2_position"),
)
_CompiledGovRow = tuple[str, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]
_compiled_gov_row_cache: dict[int, tuple[tuple[dict[str, Any], ...], tuple[_CompiledGovRow, ...]]] = {}
_score_genome_job_row = None


def government_candidate_composite_rows(
    rows: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """Rows needed by government candidate leadership and military scoring."""
    return tuple(
        row
        for row in rows
        if str(row.get("composite_family") or "").strip()
        in _GOVERNMENT_CANDIDATE_FAMILIES
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _max_composite_family_score(
    trait_values: Mapping[str, float],
    rows: tuple[dict[str, Any], ...],
    families: frozenset[str],
) -> float:
    best = 0.0
    for row in rows:
        fam = str(row.get("composite_family") or "").strip()
        if fam not in families:
            continue
        s = score_composite_row_for_traits(trait_values, row)
        if s is not None and math.isfinite(s):
            best = max(best, float(s))
    return _clamp01(best)


def _score_trait_fast(genome_value: float, deviation_band: str) -> float:
    global _score_genome_job_row
    if _score_genome_job_row is None:
        from library.simulation_careers import score_genome_job_row

        _score_genome_job_row = score_genome_job_row
    return _score_genome_job_row(genome_value, deviation_band)


def _compile_government_candidate_rows(
    rows: tuple[dict[str, Any], ...],
) -> tuple[_CompiledGovRow, ...]:
    key = id(rows)
    cached = _compiled_gov_row_cache.get(key)
    if cached is not None and cached[0] is rows:
        return cached[1]
    if len(_compiled_gov_row_cache) > 8:
        _compiled_gov_row_cache.clear()

    compiled: list[_CompiledGovRow] = []
    for row in rows:
        fam = str(row.get("composite_family") or "").strip()
        if fam not in _GOVERNMENT_CANDIDATE_FAMILIES:
            continue
        components: list[tuple[str, str]] = []
        for trait_key, pos_key in _COMPONENT_KEYS:
            trait = str(row.get(trait_key) or "").strip()
            if not trait:
                continue
            band = normalize_composite_band(str(row.get(pos_key) or ""))
            components.append((trait, band))
        if not components:
            continue
        disqualifiers: list[tuple[str, str]] = []
        for trait_key, pos_key in _DISQUALIFIER_KEYS:
            trait = str(row.get(trait_key) or "").strip()
            if not trait:
                continue
            band = normalize_composite_band(str(row.get(pos_key) or ""))
            disqualifiers.append((trait, band))
        compiled.append((fam, tuple(components), tuple(disqualifiers)))
    out = tuple(compiled)
    _compiled_gov_row_cache[key] = (rows, out)
    return out


def _score_compiled_composite(
    trait_values: Mapping[str, float],
    components: tuple[tuple[str, str], ...],
    disqualifiers: tuple[tuple[str, str], ...],
) -> float | None:
    if not trait_values or not components:
        return None
    prod = 1.0
    for trait, band in components:
        if trait not in trait_values:
            return None
        prod *= _clamp01(_score_trait_fast(float(trait_values[trait]), band))
    final = prod ** (1.0 / len(components))
    for trait, band in disqualifiers:
        if trait not in trait_values:
            continue
        d = _clamp01(_score_trait_fast(float(trait_values[trait]), band))
        final *= 1.0 - d
    if not math.isfinite(final):
        return None
    return _clamp01(final)


def _max_government_candidate_scores(
    trait_values: Mapping[str, float],
    rows: tuple[dict[str, Any], ...],
) -> tuple[float, float]:
    leadership_best = 0.0
    military_best = 0.0
    for fam, components, disqualifiers in _compile_government_candidate_rows(rows):
        s = _score_compiled_composite(trait_values, components, disqualifiers)
        if s is None or not math.isfinite(s):
            continue
        score = float(s)
        if fam in _GOVERNANCE_FAMILIES:
            leadership_best = max(leadership_best, score)
        if fam in _MILITARY_FAMILIES:
            military_best = max(military_best, score)
    return _clamp01(leadership_best), _clamp01(military_best)


def _trait_blend(
    mind_body: Mapping[str, float], weights: dict[str, float]
) -> float:
    if not mind_body or not weights:
        return 0.5
    total_w = 0.0
    acc = 0.0
    for trait, w in weights.items():
        t = str(trait).strip()
        if t not in mind_body:
            continue
        try:
            mag = min(100.0, abs(float(mind_body[t])))
        except (TypeError, ValueError):
            continue
        tw = float(w)
        total_w += tw
        acc += tw * max(0.0, 1.0 - mag / 100.0)
    if total_w <= 0.0:
        return 0.5
    return _clamp01(acc / total_w)


def _life_stage_multiplier(life_stage: str | None) -> float:
    ls = (life_stage or "").strip().lower()
    return {
        "child": 0.10,
        "mature": 0.60,
        "prime": 1.00,
        "middleaged": 0.95,
        "elder": 0.85,
    }.get(ls, 0.75)


def leadership_index(person: "Person", *, composite_rows: tuple[dict[str, Any], ...]) -> float:
    """0..1 governance / political leadership fitness."""
    traits = work_trait_values(person)
    base = _max_composite_family_score(traits, composite_rows, _GOVERNANCE_FAMILIES)
    blend = _trait_blend(
        person.mind_body,
        {
            "assertiveness": 2.0,
            "civics": 2.0,
            "ambition": 2.0,
            "persuasion": 1.5,
            "courage": 1.0,
            "justice": 1.0,
        },
    )
    cfs = float(person.career_fitness_score) if person.career_fitness_score is not None else 0.5
    cfs = _clamp01(cfs)
    raw = _clamp01(0.55 * base + 0.20 * cfs + 0.25 * blend)
    return _clamp01(raw * _life_stage_multiplier(person.life_stage))


def military_quality_index(person: "Person", *, composite_rows: tuple[dict[str, Any], ...]) -> float:
    """0..1 combat / command quality."""
    traits = work_trait_values(person)
    base = _max_composite_family_score(traits, composite_rows, _MILITARY_FAMILIES)
    blend = _trait_blend(
        person.mind_body,
        {
            "physical": 2.0,
            "courage": 2.0,
            "assertiveness": 1.5,
            "discipline": 1.5,
            "resilience": 1.0,
        },
    )
    cfs = float(person.career_fitness_score) if person.career_fitness_score is not None else 0.5
    raw = _clamp01(0.50 * base + 0.20 * _clamp01(cfs) + 0.30 * blend)
    return _clamp01(raw * _life_stage_multiplier(person.life_stage))


def leadership_and_military_indexes(
    person: "Person", *, composite_rows: tuple[dict[str, Any], ...]
) -> tuple[float, float]:
    """Return governance and military fitness while sharing trait materialization."""
    traits = work_trait_values(person)
    life_mult = _life_stage_multiplier(person.life_stage)
    cfs = float(person.career_fitness_score) if person.career_fitness_score is not None else 0.5
    cfs = _clamp01(cfs)

    leadership_base, military_base = _max_government_candidate_scores(
        traits, composite_rows
    )
    leadership_blend = _trait_blend(
        person.mind_body,
        {
            "assertiveness": 2.0,
            "civics": 2.0,
            "ambition": 2.0,
            "persuasion": 1.5,
            "courage": 1.0,
            "justice": 1.0,
        },
    )
    leadership = _clamp01(
        _clamp01(0.55 * leadership_base + 0.20 * cfs + 0.25 * leadership_blend)
        * life_mult
    )

    military_blend = _trait_blend(
        person.mind_body,
        {
            "physical": 2.0,
            "courage": 2.0,
            "assertiveness": 1.5,
            "discipline": 1.5,
            "resilience": 1.0,
        },
    )
    military = _clamp01(
        _clamp01(0.50 * military_base + 0.20 * cfs + 0.30 * military_blend)
        * life_mult
    )
    return leadership, military


def civic_quality_index(person: "Person", *, composite_rows: tuple[dict[str, Any], ...]) -> float:
    """0..1 administration / institution-building."""
    traits = work_trait_values(person)
    base = _max_composite_family_score(traits, composite_rows, _KNOWLEDGE_FAMILIES)
    blend = _trait_blend(
        person.mind_body,
        {
            "civics": 2.0,
            "discipline": 1.5,
            "patience": 1.5,
            "intellect": 1.0,
            "focus": 1.0,
        },
    )
    cfs = float(person.career_fitness_score) if person.career_fitness_score is not None else 0.5
    raw = _clamp01(0.45 * base + 0.25 * _clamp01(cfs) + 0.30 * blend)
    return _clamp01(raw * _life_stage_multiplier(person.life_stage))


def judicial_quality_index(person: "Person", *, composite_rows: tuple[dict[str, Any], ...]) -> float:
    """0..1 law / justice orientation."""
    traits = work_trait_values(person)
    base = _max_composite_family_score(traits, composite_rows, _JUDICIAL_FAMILIES)
    blend = _trait_blend(
        person.mind_body,
        {
            "justice": 2.5,
            "civics": 1.5,
            "honesty": 1.0,
            "temperance": 1.0,
        },
    )
    cfs = float(person.career_fitness_score) if person.career_fitness_score is not None else 0.5
    raw = _clamp01(0.50 * base + 0.20 * _clamp01(cfs) + 0.30 * blend)
    return _clamp01(raw * _life_stage_multiplier(person.life_stage))
