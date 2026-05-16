"""Score config-driven genome composites (secondary trait tags) from signed traits."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from library.person import Person

GENOME_COMPOSITE_MIN_SCORE = 0.58
GENOME_COMPOSITE_MAX_TAGS = 3

_COMPONENT_KEYS: tuple[tuple[str, str], ...] = (
    ("component_1_trait", "component_1_position"),
    ("component_2_trait", "component_2_position"),
    ("component_3_trait", "component_3_position"),
)
_DISQUALIFIER_KEYS: tuple[tuple[str, str], ...] = (
    ("disqualifier_1_trait", "disqualifier_1_position"),
    ("disqualifier_2_trait", "disqualifier_2_position"),
)


def normalize_composite_band(position: str) -> str:
    """Map genome_composites.csv band labels to career scoring bands."""
    b = (position or "").strip().lower()
    if b == "peak":
        return "optimal"
    if b == "excessive":
        return "excess"
    return b


def _score_trait(genome_value: float, deviation_band: str) -> float:
    from library.simulation_careers import score_genome_job_row

    return score_genome_job_row(genome_value, deviation_band)


def composite_row_name(row: dict[str, Any]) -> str:
    """Lowercase display label from config: ``composite_name``, else ``short_definition``.

    Returned in lowercase so persisted ``Person.genome_composite_names`` and any
    derived report strings stay normalized regardless of CSV casing.
    """
    name = str(row.get("composite_name") or "").strip()
    if name:
        return name.lower()
    return str(row.get("short_definition") or "").strip().lower()


def score_composite_row_for_traits(
    trait_values: Mapping[str, float], row: dict[str, Any]
) -> float | None:
    """Return 0..1 composite strength from signed trait magnitudes (e.g. mind/body)."""
    if not trait_values:
        return None

    comp_scores: list[float] = []
    for trait_key, pos_key in _COMPONENT_KEYS:
        trait = str(row.get(trait_key) or "").strip()
        if not trait:
            continue
        if trait not in trait_values:
            return None
        band = normalize_composite_band(str(row.get(pos_key) or ""))
        comp_scores.append(_score_trait(float(trait_values[trait]), band))

    if not comp_scores:
        return None

    prod = 1.0
    for s in comp_scores:
        prod *= max(0.0, min(1.0, float(s)))
    k = len(comp_scores)
    base = prod ** (1.0 / k) if k > 0 else 0.0

    final = base
    for trait_key, pos_key in _DISQUALIFIER_KEYS:
        trait = str(row.get(trait_key) or "").strip()
        if not trait:
            continue
        if trait not in trait_values:
            continue
        band = normalize_composite_band(str(row.get(pos_key) or ""))
        d = max(0.0, min(1.0, _score_trait(float(trait_values[trait]), band)))
        final *= 1.0 - d

    if not math.isfinite(final):
        return None
    return max(0.0, min(1.0, final))


def score_composite_row(person: "Person", row: dict[str, Any]) -> float | None:
    """Return 0..1 composite strength using current mind/body (work) trait values."""
    from library.mind_body import work_trait_values

    return score_composite_row_for_traits(work_trait_values(person), row)


def significant_composite_names(
    person: "Person",
    rows: tuple[dict[str, Any], ...],
    *,
    threshold: float = GENOME_COMPOSITE_MIN_SCORE,
    max_tags: int = GENOME_COMPOSITE_MAX_TAGS,
) -> tuple[str, ...]:
    """Top ``composite_name`` values at or above ``threshold``, deduped, cap ``max_tags``.

    Sorted by score descending, then ``composite_id`` for determinism.
    """
    thr = float(threshold)
    cap = max(0, int(max_tags))
    ranked: list[tuple[float, str, str]] = []
    for row in rows:
        cid = str(row.get("composite_id") or "").strip()
        label = composite_row_name(row)
        if not cid or not label:
            continue
        s = score_composite_row(person, row)
        if s is None or s < thr:
            continue
        ranked.append((float(s), cid, label))
    ranked.sort(key=lambda t: (-t[0], t[1]))

    out: list[str] = []
    seen: set[str] = set()
    for _s, _cid, label in ranked:
        if len(out) >= cap:
            break
        if label not in seen:
            seen.add(label)
            out.append(label)
    return tuple(out)
