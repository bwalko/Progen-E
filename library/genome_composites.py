"""Score config-driven genome composites (secondary trait tags) from signed traits."""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
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
_RATING_COMPONENT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("component_1_trait", "component_1_position", "component_1_weight"),
    ("component_2_trait", "component_2_position", "component_2_weight"),
    ("component_3_trait", "component_3_position", "component_3_weight"),
    ("component_4_trait", "component_4_position", "component_4_weight"),
    ("component_5_trait", "component_5_position", "component_5_weight"),
    ("component_6_trait", "component_6_position", "component_6_weight"),
)
_RATING_DISQUALIFIER_KEYS: tuple[tuple[str, str, str], ...] = (
    ("disqualifier_1_trait", "disqualifier_1_position", "disqualifier_1_weight"),
    ("disqualifier_2_trait", "disqualifier_2_position", "disqualifier_2_weight"),
)
_RATING_SCORE_FLOOR = 0.05
_RATING_DIRECT_HIGH_NORMAL_MAX = 0.70
_RATING_SIGNED_SIDE_NORMAL_MAX = 80.0
_RATING_DEVIATION_FREE_BAND = 35.0
_RATING_DEVIATION_NORMAL_SPAN = 45.0
_RATING_COMPONENT_CURVE_EXPONENT = 1.25
_RATING_COHERENCE_EXPONENT = 0.75
_RATING_GEOMEAN_FLOOR = 0.10
_RATING_CONTEXT_BONUS_MIN_MULTIPLIER = 0.35
_RATING_CONTEXT_BONUS_KEYS: tuple[tuple[str, str, str], ...] = (
    ("gender", "male", "male_body_bonus"),
    ("gender", "female", "female_body_bonus"),
    ("gender_mind", "masculine", "masculine_mind_bonus"),
    ("gender_mind", "feminine", "feminine_mind_bonus"),
)
GENOME_COMPOSITE_REVEALS_PER_YEAR = 2
GENOME_COMPOSITE_REVEAL_ORDER: tuple[str, ...] = (
    "make_friends",
    "force_get_way_desire",
    "make_enemies",
    "isolation_preference",
    "convince_people",
    "insanity",
    "good_done_desire",
    "creative_intellect",
    "psychopathy",
    "revenge_desire",
    "disguise_motive",
    "lie_or_cheat_willingness",
    "ruthless_ambition",
    "lead_others_ability",
    "evil_done_desire",
    "practical_intellect",
    "honest_work_desire",
    "physical_strength",
    "enrich_self_desire",
    "sexual_object",
    "sexual_magnetism",
)
_GENOME_COMPOSITE_REVEAL_AGE_BY_ID = {
    rating_id: index // GENOME_COMPOSITE_REVEALS_PER_YEAR
    for index, rating_id in enumerate(GENOME_COMPOSITE_REVEAL_ORDER)
}
_GENOME_COMPOSITE_REVEAL_INDEX_BY_ID = {
    rating_id: index for index, rating_id in enumerate(GENOME_COMPOSITE_REVEAL_ORDER)
}
_score_genome_job_row = None


def normalize_composite_band(position: str) -> str:
    """Map genome_composites.csv band labels to career scoring bands."""
    b = (position or "").strip().lower()
    if b == "peak":
        return "optimal"
    if b == "excessive":
        return "excess"
    return b


def _score_trait(genome_value: float, deviation_band: str) -> float:
    global _score_genome_job_row
    if _score_genome_job_row is None:
        from library.simulation_careers import score_genome_job_row

        _score_genome_job_row = score_genome_job_row

    return _score_genome_job_row(genome_value, deviation_band)


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _score_floor(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, float(value))


def _person_field(person: object | None, field: str) -> object | None:
    if person is None:
        return None
    if isinstance(person, Mapping):
        return person.get(field)
    return getattr(person, field, None)


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def composite_score_age(
    person: object | None,
    *,
    current_year: int | None = None,
) -> int | None:
    """Return the age used for numeric composite score reveal gates."""
    birthyear = _optional_int(_person_field(person, "birthyear"))
    if birthyear is None:
        return None
    deathyear = _optional_int(_person_field(person, "deathyear"))
    end_year = deathyear if deathyear is not None else current_year
    if end_year is None:
        return None
    return max(0, int(end_year) - int(birthyear))


def genome_composite_rating_reveal_age(rating_id: object) -> int | None:
    """Age when a configured numeric rating becomes known."""
    rid = str(rating_id or "").strip()
    if not rid:
        return None
    return _GENOME_COMPOSITE_REVEAL_AGE_BY_ID.get(rid)


def genome_composite_rating_is_known(rating_id: object, age: int | None) -> bool:
    """Whether a numeric rating should be visible/usable at ``age``."""
    if age is None:
        return True
    reveal_age = genome_composite_rating_reveal_age(rating_id)
    if reveal_age is None:
        return True
    return int(age) >= int(reveal_age)


def filter_known_genome_composite_scores(
    scores: Mapping[str, float],
    *,
    age: int | None,
) -> dict[str, float]:
    """Return only numeric composite scores already revealed for ``age``."""
    return {
        str(rating_id): float(score)
        for rating_id, score in scores.items()
        if genome_composite_rating_is_known(rating_id, age)
    }


def unknown_genome_composite_rating_ids(
    rows: tuple[Mapping[str, Any], ...],
    *,
    age: int | None,
) -> tuple[str, ...]:
    """Configured numeric ratings that exist but are not known yet at ``age``."""
    if age is None:
        return ()
    out: list[str] = []
    for row in sorted(rows, key=_rating_row_reveal_sort_key):
        rid = str(row.get("rating_id") or "").strip()
        if rid and not genome_composite_rating_is_known(rid, age):
            out.append(rid)
    return tuple(out)


def _rating_row_reveal_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    rid = str(row.get("rating_id") or "").strip()
    return (_GENOME_COMPOSITE_REVEAL_INDEX_BY_ID.get(rid, 10_000), rid)


def _score_direct_01(value: float, position: str) -> float:
    v = _clamp01(float(value))
    p = normalize_composite_band(position)
    if p in {"", "high", "excess", "excessive"}:
        return _score_floor(v / _RATING_DIRECT_HIGH_NORMAL_MAX)
    if p in {"low", "deficient"}:
        return _score_floor((1.0 - v) / _RATING_DIRECT_HIGH_NORMAL_MAX)
    if p == "optimal":
        return _score_floor(1.0 - abs(v - 0.5) * 2.0)
    if p == "deviation":
        return _score_floor((abs(v - 0.5) - 0.2) / 0.3)
    return v


def _score_signed_trait_position(value: float, position: str) -> float:
    p = normalize_composite_band(position)
    v = max(-100.0, min(100.0, float(value)))
    if p in {"", "optimal"}:
        return _score_floor(1.0 - abs(v) / 100.0)
    if p in {"excess", "high"}:
        return _score_floor(v / _RATING_SIGNED_SIDE_NORMAL_MAX)
    if p in {"deficient", "low"}:
        return _score_floor(-v / _RATING_SIGNED_SIDE_NORMAL_MAX)
    if p == "deviation":
        return _score_floor(
            (abs(v) - _RATING_DEVIATION_FREE_BAND) / _RATING_DEVIATION_NORMAL_SPAN
        )
    return _score_floor(_score_trait(v, p))


def _score_rating_component(
    trait_values: Mapping[str, float],
    trait: str,
    position: str,
    *,
    person: "Person | None" = None,
) -> float:
    trait_s = str(trait or "").strip()
    if not trait_s:
        return _RATING_SCORE_FLOOR
    positions = [
        normalize_composite_band(part)
        for part in str(position or "optimal").replace("/", "|").split("|")
        if part.strip()
    ]
    if not positions:
        positions = ["optimal"]

    value: float | None = None
    if trait_s.endswith("_01"):
        if person is not None:
            raw = _person_field(person, trait_s)
            if raw is not None:
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None
        if value is None and trait_s in trait_values:
            value = float(trait_values[trait_s])
        if value is None:
            return _RATING_SCORE_FLOOR
        return max(_score_direct_01(value, pos) for pos in positions)

    if trait_s not in trait_values:
        return _RATING_SCORE_FLOOR
    value = float(trait_values[trait_s])
    return max(_score_signed_trait_position(value, pos) for pos in positions)


def _float_from_row(row: Mapping[str, Any], key: str, default: float) -> float:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _rating_context_bonus(row: Mapping[str, Any], person: object | None) -> float:
    if person is None:
        return 0.0
    bonus = 0.0
    for field, target, row_key in _RATING_CONTEXT_BONUS_KEYS:
        actual = str(_person_field(person, field) or "").strip().lower()
        if actual == target:
            bonus += max(0.0, _float_from_row(row, row_key, 0.0))
    return bonus


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


def score_composite_rating_row_for_traits(
    trait_values: Mapping[str, float],
    row: Mapping[str, Any],
    *,
    person: object | None = None,
) -> float | None:
    """Return a config-driven numeric rating using nonlinear fit blending."""
    comp_scores: list[tuple[float, float]] = []
    for trait_key, pos_key, weight_key in _RATING_COMPONENT_KEYS:
        trait = str(row.get(trait_key) or "").strip()
        if not trait:
            continue
        weight = max(0.0, _float_from_row(row, weight_key, 1.0))
        if weight <= 0.0:
            continue
        score = _score_rating_component(
            trait_values,
            trait,
            str(row.get(pos_key) or "optimal"),
            person=person,
        )
        curved = _score_floor(score) ** _RATING_COMPONENT_CURVE_EXPONENT
        comp_scores.append((curved, weight))

    if not comp_scores:
        return None

    weight_total = sum(w for _score, w in comp_scores)
    if weight_total <= 0.0:
        return None
    log_total = sum(
        math.log(max(_RATING_GEOMEAN_FLOOR, score)) * weight
        for score, weight in comp_scores
    )
    geometric_fit = math.exp(log_total / weight_total)
    average_fit = (
        sum(min(1.0, score) * weight for score, weight in comp_scores) / weight_total
    )
    final = geometric_fit * (average_fit ** _RATING_COHERENCE_EXPONENT)
    for trait_key, pos_key, weight_key in _RATING_DISQUALIFIER_KEYS:
        trait = str(row.get(trait_key) or "").strip()
        if not trait:
            continue
        weight = max(0.0, _float_from_row(row, weight_key, 1.0))
        if weight <= 0.0:
            continue
        d = _score_rating_component(
            trait_values,
            trait,
            str(row.get(pos_key) or "optimal"),
            person=person,
        )
        final *= max(0.0, 1.0 - _clamp01(d) * min(1.0, weight))

    if bonus := _rating_context_bonus(row, person):
        bonus_multiplier = _RATING_CONTEXT_BONUS_MIN_MULTIPLIER + (
            1.0 - _RATING_CONTEXT_BONUS_MIN_MULTIPLIER
        ) * min(1.0, max(0.0, final))
        final += bonus * bonus_multiplier
    if not math.isfinite(final):
        return None
    return _score_floor(final)


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
    from library.mind_body import work_trait_values

    return significant_composite_names_for_traits(
        work_trait_values(person),
        rows,
        threshold=threshold,
        max_tags=max_tags,
    )


def significant_composite_names_for_traits(
    trait_values: Mapping[str, float],
    rows: tuple[dict[str, Any], ...],
    *,
    threshold: float = GENOME_COMPOSITE_MIN_SCORE,
    max_tags: int = GENOME_COMPOSITE_MAX_TAGS,
) -> tuple[str, ...]:
    """Trait-map variant of :func:`significant_composite_names` for hot paths."""
    thr = float(threshold)
    cap = max(0, int(max_tags))
    ranked: list[tuple[float, str, str]] = []
    for row in rows:
        cid = str(row.get("composite_id") or "").strip()
        label = composite_row_name(row)
        if not cid or not label:
            continue
        s = score_composite_row_for_traits(trait_values, row)
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


@lru_cache(maxsize=16)
def composite_rows_from_db(db_path: str) -> tuple[dict[str, Any], ...]:
    """Load legacy label composites from a config DB, returning empty when absent."""
    path = Path(db_path)
    if not path.exists():
        return ()
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            has_table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='genome_composites'
                """
            ).fetchone()
            if has_table is None:
                return ()
            rows = conn.execute("SELECT * FROM genome_composites ORDER BY rowid").fetchall()
            return tuple(dict(r) for r in rows)
    except sqlite3.Error:
        return ()


@lru_cache(maxsize=16)
def composite_rating_rows_from_db(db_path: str) -> tuple[dict[str, Any], ...]:
    """Load numeric rating recipes from ``config/genome_composite_ratings.csv``."""
    path = Path(db_path)
    if not path.exists():
        return ()
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            has_table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='genome_composite_ratings'
                """
            ).fetchone()
            if has_table is None:
                return ()
            rows = conn.execute(
                "SELECT * FROM genome_composite_ratings ORDER BY rowid"
            ).fetchall()
            return tuple(dict(r) for r in rows)
    except sqlite3.Error:
        return ()


def genome_composite_scores_for_traits(
    trait_values: Mapping[str, float],
    rows: tuple[dict[str, Any], ...],
    *,
    person: object | None = None,
    age: int | None = None,
) -> dict[str, float]:
    """Score all configured numeric composite ratings."""
    scores: dict[str, float] = {}
    for row in sorted(rows, key=_rating_row_reveal_sort_key):
        rid = str(row.get("rating_id") or "").strip()
        if not rid:
            continue
        if not genome_composite_rating_is_known(rid, age):
            continue
        s = score_composite_rating_row_for_traits(trait_values, row, person=person)
        if s is None:
            continue
        scores[rid] = round(_score_floor(s), 6)
    return scores


def refresh_genome_composite_scores(
    person: "Person",
    db_path: str | Path,
    trait_values: Mapping[str, float] | None = None,
    *,
    current_year: int | None = None,
    age: int | None = None,
) -> "Person":
    """Refresh numeric composite ratings while preserving existing legacy labels."""
    from library.mind_body import work_trait_values

    values = trait_values if trait_values is not None else work_trait_values(person)
    rating_rows = composite_rating_rows_from_db(str(Path(db_path)))
    reveal_age = composite_score_age(person, current_year=current_year) if age is None else age
    scores = genome_composite_scores_for_traits(
        values,
        rating_rows,
        person=person,
        age=reveal_age,
    )
    return replace(person, genome_composite_scores=scores)


def _preserved_composite_labels(person: "Person", new_labels: tuple[str, ...]) -> tuple[str, ...]:
    preserve: list[str] = []
    try:
        from library.detailed_population_variance import HIGH_VARIANCE_DETAIL_COMPOSITE

        special = {HIGH_VARIANCE_DETAIL_COMPOSITE}
    except Exception:
        special = set()
    for label in person.genome_composite_names or ():
        s = str(label).strip()
        if s and s not in new_labels and s in special:
            preserve.append(s)
    return tuple(preserve)


def refresh_genome_composite_profile(
    person: "Person",
    db_path: str | Path,
    trait_values: Mapping[str, float] | None = None,
    *,
    current_year: int | None = None,
    age: int | None = None,
) -> "Person":
    """Refresh legacy composite labels and full numeric genome rating scores."""
    from library.mind_body import work_trait_values

    values = trait_values if trait_values is not None else work_trait_values(person)
    path_s = str(Path(db_path))
    label_rows = composite_rows_from_db(path_s)
    rating_rows = composite_rating_rows_from_db(path_s)
    labels = significant_composite_names_for_traits(values, label_rows) if label_rows else ()
    labels = (*labels, *_preserved_composite_labels(person, labels))
    reveal_age = composite_score_age(person, current_year=current_year) if age is None else age
    scores = genome_composite_scores_for_traits(
        values,
        rating_rows,
        person=person,
        age=reveal_age,
    )
    return replace(
        person,
        genome_composite_names=labels,
        genome_composite_scores=scores,
    )
