"""Generate settlement display names and tie them to abstract local geography."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
import random
from typing import Any

from library.ethnic_proto_placewords import (
    apply_probabilistic_sound_law_runs,
    sound_law_branch_for_ethnic,
)
from library.geography import Region
from library.placenames_lexicon import (
    PlacenameLexicon,
    PlacenameRow,
    apply_affix_template,
    format_toponym_display,
    join_tokens,
    normalize_placename_stem,
)
from library.random_names import sample_first_name_for_ethnic
from library.random_traits import DEFAULT_DB_PATH, _connect
from library.settlement_local_geography import (
    build_local_region_graph,
    category_weights_for_region,
    feature_kind_for_meaning,
    make_region_geography_rng,
)


@dataclass(frozen=True)
class GeneratedSettlementName:
    """Surface form plus lexicon metadata (categories/cultures separate from etymology text)."""

    display_name: str
    etymology: str
    primary_category: str
    secondary_category: str | None
    primary_meaning: str
    secondary_meaning: str | None
    culture_primary: str
    culture_secondary: str | None
    mode: str


_MEANING_SEP = " · "

_LOCATIVE_ANCHOR_KINDS = {
    "bay",
    "coast",
    "ford",
    "harbor",
    "lake",
    "marsh",
    "river",
    "spring",
    "stream",
    "wadi",
    "well",
}

# Visible locative compounds are flavorful, but if every anchored settlement gets
# one the map collapses into a wall of "-by" names.
LOCATIVE_DISPLAY_PROBABILITY = 0.18
SETTLEMENT_DISPLAY_IDEAL_LETTERS = 9
SETTLEMENT_DISPLAY_TARGET_LETTERS = 12
SETTLEMENT_DISPLAY_HARD_CAP_LETTERS = 16
SETTLEMENT_PERSONAL_STEM_TARGET_LETTERS = 8
SETTLEMENT_NAME_CANDIDATE_ATTEMPTS = 18


def _join_etymology_parts(*parts: str) -> str:
    bits = [p.strip() for p in parts if (p or "").strip()]
    return _MEANING_SEP.join(bits)


def _unique_display_name(name: str, used_names: set[str]) -> str:
    base = (name or "").strip() or "Place"
    taken = {n.strip().casefold() for n in used_names if n.strip()}
    if base.casefold() not in taken:
        return base
    idx = 2
    while f"{base}{idx}".casefold() in taken:
        idx += 1
    return f"{base}{idx}"


def _display_letter_count(name: str) -> int:
    return sum(1 for ch in (name or "") if ch.isalpha())


def _settlement_candidate_rank(gen: "GeneratedSettlementName") -> tuple[int, int, int, int]:
    letters = _display_letter_count(gen.display_name)
    over_target = max(0, letters - SETTLEMENT_DISPLAY_TARGET_LETTERS)
    over_hard = max(0, letters - SETTLEMENT_DISPLAY_HARD_CAP_LETTERS)
    mode_penalty = 0 if gen.mode == "patronymic" else 1
    return (1 if over_hard else 0, over_target, letters, mode_penalty)


def _best_settlement_candidate(
    current: "GeneratedSettlementName" | None,
    candidate: "GeneratedSettlementName",
) -> "GeneratedSettlementName":
    if current is None or _settlement_candidate_rank(candidate) < _settlement_candidate_rank(current):
        return candidate
    return current


def _locative_settlement_display(base_name: str, anchor_kind: str, used_names: set[str]) -> str | None:
    """Readable settlement form for a town named by position near a natural feature."""
    base = normalize_placename_stem(base_name)
    if base.casefold().endswith(("by", "byr", "haven", "ford", "well")):
        return None
    candidate = format_toponym_display(join_tokens(base, "by"))
    if _display_letter_count(candidate) > SETTLEMENT_DISPLAY_TARGET_LETTERS:
        return None
    return _unique_display_name(candidate, used_names)


def _settlement_display_with_sound_law(
    raw_name: str,
    *,
    rng: random.Random,
    culture: str,
) -> str:
    branch = sound_law_branch_for_ethnic(culture)
    return format_toponym_display(
        apply_probabilistic_sound_law_runs(raw_name, rng=rng, branch=branch)
    )


def _anchor_feature_for_slot(graph, site_slot: int):
    target_slot = max(0, int(site_slot) - 1)
    anchor_id = ""
    for pin in graph.settlements:
        if int(pin.settlement_slot) == target_slot:
            anchor_id = (pin.anchor_feature_id or "").strip()
            break
    if not anchor_id:
        return None
    for feature in graph.features:
        if feature.feature_id == anchor_id:
            return feature
    return None


def _should_use_locative_display(
    *,
    rng: random.Random,
    anchor_kind: str,
    primary_kind: str,
    probability: float = LOCATIVE_DISPLAY_PROBABILITY,
) -> bool:
    if not (anchor_kind in _LOCATIVE_ANCHOR_KINDS or primary_kind in _LOCATIVE_ANCHOR_KINDS):
        return False
    p = max(0.0, min(1.0, float(probability)))
    return rng.random() < p


def _weighted_choice(rng: random.Random, pairs: list[tuple[Any, float]]) -> Any:
    items = [(a, max(0.0, w)) for a, w in pairs if w > 0]
    if not items:
        raise ValueError("weighted_choice: empty")
    keys, ws = zip(*items)
    return rng.choices(keys, weights=ws, k=1)[0]


def resolve_placename_culture(ethnic: str, lex: PlacenameLexicon, rng: random.Random) -> str:
    """Map simulation ``ethnic`` string to a ``placenames.Culture`` label."""
    e = (ethnic or "").strip()
    if e in lex.by_culture:
        return e
    el = e.lower()
    for c in lex.by_culture.keys():
        if c.lower() == el:
            return c
        if el in c.lower() or c.lower() in el:
            return c
    return rng.choice(list(lex.by_culture.keys()))


def default_ethnic_weights_from_species(*, db_path: Path | str | None = None) -> dict[str, float]:
    """Fallback distribution: ``species.rate`` summed per ``ethnic``."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            'SELECT ethnic, rate FROM species WHERE ethnic IS NOT NULL AND TRIM(ethnic) != ""'
        ).fetchall()
    acc: dict[str, float] = {}
    for r in rows:
        eth = str(r["ethnic"] or "").strip()
        rate = float(r["rate"] or 0)
        if eth and rate > 0:
            acc[eth] = acc.get(eth, 0.0) + rate
    s = sum(acc.values())
    if s <= 0:
        return {}
    return {k: v / s for k, v in acc.items()}


def region_ethnic_weights(
    ctx: "SimulationContext",
    region_id: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, float]:
    """Histogram of living residents' ``ethnic`` in ``region_id``; species fallback if empty."""
    rid = region_id.strip()
    counts: dict[str, float] = {}
    if ctx.current_year is not None:
        for pid in ctx.current_people_ids:
            rec = ctx.id_to_record.get(pid)
            if rec is None:
                continue
            if (rec.person.birthplace_region_id or "").strip() != rid:
                continue
            eth = (rec.person.ethnic or "").strip()
            if eth:
                counts[eth] = counts.get(eth, 0.0) + 1.0
    if not counts:
        raw = default_ethnic_weights_from_species(db_path=db_path or ctx.db_path)
        return raw
    s = sum(counts.values())
    return {k: v / s for k, v in counts.items()}


def _row_weight(
    row: PlacenameRow,
    *,
    ethnic_weights: dict[str, float],
    category_weights: dict[str, float],
    lex: PlacenameLexicon,
    rng: random.Random,
) -> float:
    culture = resolve_placename_culture(row.culture, lex, rng)
    # Use row's listed culture for weight lookup when exact key exists.
    ew = ethnic_weights.get(row.culture, ethnic_weights.get(culture, 0.0))
    if ew <= 0:
        ew = 1.0 / max(1, len(ethnic_weights))
    cw = category_weights.get(row.category, 0.15)
    return max(0.0, ew) * max(0.05, cw)


def _pick_row(
    rng: random.Random,
    lex: PlacenameLexicon,
    pool: list[PlacenameRow],
    ethnic_weights: dict[str, float],
    category_weights: dict[str, float],
    *,
    forbidden_meanings: set[str] | None = None,
) -> PlacenameRow:
    forbidden_meanings = forbidden_meanings or set()
    candidates = [
        r
        for r in pool
        if r.original_meaning.lower() not in forbidden_meanings
    ]
    if not candidates:
        candidates = pool
    weights = [
        _row_weight(r, ethnic_weights=ethnic_weights, category_weights=category_weights, lex=lex, rng=rng)
        for r in candidates
    ]
    if sum(weights) <= 0:
        return rng.choice(candidates)
    return rng.choices(candidates, weights=weights, k=1)[0]


def _inhabitant_cultures_for_region(
    ctx: "SimulationContext",
    region_id: str,
    lex: PlacenameLexicon,
    rng: random.Random,
) -> set[str]:
    """Set of distinct placename cultures actually living in ``region_id`` (empty if none)."""
    rid = region_id.strip()
    cultures: set[str] = set()
    for pid in ctx.current_people_ids:
        rec = ctx.id_to_record.get(pid)
        if rec is None:
            continue
        if (rec.person.birthplace_region_id or "").strip() != rid:
            continue
        eth = (rec.person.ethnic or "").strip()
        if not eth:
            continue
        cultures.add(resolve_placename_culture(eth, lex, rng))
    return cultures


def pick_prominent_resident(
    ctx: "SimulationContext",
    region_id: str,
    rng: random.Random,
) -> tuple[str, str, str] | None:
    """Return ``(first_name, last_name, ethnic)`` for a weighted adult in the region."""
    rid = region_id.strip()
    cy = ctx.current_year
    pool: list[tuple[str, str, str, int]] = []
    for pid in ctx.current_people_ids:
        rec = ctx.id_to_record.get(pid)
        if rec is None:
            continue
        if (rec.person.birthplace_region_id or "").strip() != rid:
            continue
        age = 0 if cy is None else max(0, cy - rec.person.birthyear)
        if age < 18:
            continue
        fn = (rec.person.first_name or "").strip()
        ln = (rec.person.last_name or "").strip()
        eth = (rec.person.ethnic or "").strip()
        pool.append((fn, ln, eth, age))
    if not pool:
        return None
    weights = [max(1, p[3]) for p in pool]
    pick = rng.choices(pool, weights=weights, k=1)[0]
    return pick[0], pick[1], pick[2]


def _split_affix_template(template: str) -> tuple[str, str]:
    """Return ``(before_$, after_$)`` letters of a single-``$`` affix template."""
    t = (template or "").strip()
    if "$" not in t:
        return (t, "")
    idx = t.index("$")
    return (t[:idx].strip(), t[idx + 1 :].strip())


def _compose_dual_affix(template1: str, template2: str) -> str | None:
    """Join two affix templates into one display token by dropping ``$`` placeholders.

    Prefers prefix-shaped (``X$``) on the left and suffix-shaped (``$X``) on the right
    so e.g. ``Pen$`` + ``$mont`` reads as ``Penmont``.

    Returns ``None`` when both patterns are stem-leading (``$suffix`` + ``$suffix``):
    each morpheme expects a personal name (e.g. ``$'s tun`` + ``$fleot``), and joining
    the bare suffixes would yield garbage like ``'s tunfleot`` with no antecedent.
    """
    l1, r1 = _split_affix_template(template1)
    l2, r2 = _split_affix_template(template2)
    if l1 and r2:
        return join_tokens(l1, r2)
    if l2 and r1:
        return join_tokens(l2, r1)
    if not l1 and not l2:
        return None
    a = l1 or r1
    b = l2 or r2
    return join_tokens(a, b)


def _resolve_first_name_for_culture(
    *,
    culture: str,
    rng: random.Random,
    db_path: Path | str | None,
    max_stem_letters: int | None = None,
    attempts: int = 1,
) -> str:
    """First-name stem for ``patronymic`` mode when no prominent resident is available.

    Samples a small deterministic pool when a display budget is supplied, then
    returns the shortest covered-culture name if none land within budget.
    """
    best_name = ""
    best_len = 10**9
    max_letters = max_stem_letters if max_stem_letters is not None else 10**9
    for _ in range(max(1, int(attempts))):
        name = sample_first_name_for_ethnic(ethnic=culture, rng=rng, db_path=db_path)
        if not name:
            continue
        stem_len = _display_letter_count(normalize_placename_stem(name))
        if stem_len < best_len:
            best_name = name
            best_len = stem_len
        if stem_len <= max_letters:
            return name
    if best_name:
        return best_name
    raise LookupError(
        f"sample_first_name_for_ethnic returned empty for culture={culture!r}; "
        "expected ethnic.csv / first_name.csv coverage for placename cultures."
    )


def _pick_dual_affix_pair(
    *,
    rng: random.Random,
    lex: PlacenameLexicon,
    primary_culture: str,
    mapped_weights: dict[str, float],
    cat_w: dict[str, float],
    inhabitant_cultures: set[str] | None,
) -> tuple[PlacenameRow, PlacenameRow] | None:
    """Pick two rows for ``dual_affix`` mode honoring the meaning/culture rule.

    - When ``inhabitant_cultures`` has at least two distinct cultures, ``row2`` may
      come from a different culture (no Original-Meaning constraint then).
    - Otherwise both rows must share ``primary_culture`` and have **different**
      ``Original Meaning``.
    """
    primary_pool = list(lex.by_culture.get(primary_culture, []))
    if not primary_pool:
        return None
    row1 = _pick_row(rng, lex, primary_pool, mapped_weights, cat_w)
    occ = inhabitant_cultures or set()
    cross_culture = len({c for c in occ if c.strip()}) >= 2
    if cross_culture:
        other_cultures = [c for c in occ if c.strip() and c != primary_culture]
        if other_cultures:
            c2 = rng.choice(other_cultures)
            second_pool = list(lex.by_culture.get(c2, [])) or primary_pool
            row2 = _pick_row(rng, lex, second_pool, mapped_weights, cat_w)
            return row1, row2
    alt_pool = [
        r for r in primary_pool
        if r.original_meaning.strip().lower() != row1.original_meaning.strip().lower()
    ]
    if not alt_pool:
        return None
    row2 = _pick_row(rng, lex, alt_pool, mapped_weights, cat_w)
    return row1, row2


def _build_dual_affix_settlement_candidate(
    *,
    rng: random.Random,
    lex: PlacenameLexicon,
    primary_culture: str,
    mapped_weights: dict[str, float],
    cat_w: dict[str, float],
    inhabitant_cultures: set[str] | None,
) -> GeneratedSettlementName | None:
    pair = _pick_dual_affix_pair(
        rng=rng,
        lex=lex,
        primary_culture=primary_culture,
        mapped_weights=mapped_weights,
        cat_w=cat_w,
        inhabitant_cultures=inhabitant_cultures,
    )
    if pair is None:
        return None
    row1, row2 = pair
    v1 = row1.pick_settlement_affix_variant(rng)
    v2 = row2.pick_settlement_affix_variant(rng)
    dual_display = _compose_dual_affix(v1, v2)
    if dual_display is None:
        return None
    display = _settlement_display_with_sound_law(
        dual_display,
        rng=rng,
        culture=row1.culture,
    )
    etym = _join_etymology_parts(row1.original_meaning, row2.original_meaning)
    c2 = row2.culture if row2.culture != row1.culture else None
    cat2 = row2.category if row2.category != row1.category else None
    return GeneratedSettlementName(
        display_name=display,
        etymology=etym,
        primary_category=row1.category,
        secondary_category=cat2,
        primary_meaning=row1.original_meaning,
        secondary_meaning=row2.original_meaning,
        culture_primary=row1.culture,
        culture_secondary=c2,
        mode="dual_affix",
    )


def _build_patronymic_settlement_candidate(
    *,
    rng: random.Random,
    lex: PlacenameLexicon,
    primary_culture: str,
    mapped_weights: dict[str, float],
    cat_w: dict[str, float],
    prominent_person: tuple[str, str, str] | None,
    db_path: Path | str | None,
) -> GeneratedSettlementName:
    fn_clean = ""
    if prominent_person is not None:
        fn_clean = (prominent_person[0] or "").strip()
    if not fn_clean or (
        _display_letter_count(normalize_placename_stem(fn_clean))
        > SETTLEMENT_PERSONAL_STEM_TARGET_LETTERS
    ):
        fn_clean = _resolve_first_name_for_culture(
            culture=primary_culture,
            rng=rng,
            db_path=db_path,
            max_stem_letters=SETTLEMENT_PERSONAL_STEM_TARGET_LETTERS,
            attempts=4,
        )
    stem = normalize_placename_stem(fn_clean)

    pool = list(lex.by_culture.get(primary_culture, []))
    if not pool:
        pool = list(lex.rows)
    row = _pick_row(rng, lex, pool, mapped_weights, cat_w)
    affix = row.pick_settlement_affix_variant(rng)
    display = _settlement_display_with_sound_law(
        apply_affix_template(affix, stem),
        rng=rng,
        culture=row.culture,
    )
    etym = _join_etymology_parts(fn_clean, row.original_meaning)
    return GeneratedSettlementName(
        display_name=display,
        etymology=etym,
        primary_category=row.category,
        secondary_category=None,
        primary_meaning=row.original_meaning,
        secondary_meaning=None,
        culture_primary=row.culture,
        culture_secondary=None,
        mode="patronymic",
    )


def generate_settlement_name(
    *,
    rng: random.Random,
    lex: PlacenameLexicon,
    ethnic_weights: dict[str, float],
    region: Region,
    prominent_person: tuple[str, str, str] | None = None,
    category_weights: dict[str, float] | None = None,
    inhabitant_cultures: set[str] | None = None,
    dual_affix_probability: float = 0.18,
    db_path: Path | str | None = None,
) -> GeneratedSettlementName:
    """Compose one settlement toponym from real morphology only.

    The display name is **always** built from concrete morphemes — never from
    ``Original Meaning`` or ``Category`` text. Two composition modes:

    1. **patronymic** — prominent resident's **first name** (or a culture-sampled
       first name when no resident exists) joined to one row's
       ``Affix`` / ``Archaic Affix`` template via :func:`apply_affix_template`.
    2. **dual_affix** — two rows' affix templates joined directly (``$``
       placeholders dropped). Same culture with **different** ``Original Meaning``,
       unless ``inhabitant_cultures`` shows residents of multiple cultures.

    ``etymology``, ``primary_meaning``, ``secondary_meaning``, ``primary_category``,
    ``secondary_category`` carry the metadata; the surface form does not.
    """
    cat_w = category_weights or category_weights_for_region(region)
    effective_db_path = db_path

    mapped_weights: dict[str, float] = {}
    for eth, w in ethnic_weights.items():
        c = resolve_placename_culture(eth, lex, rng)
        mapped_weights[c] = mapped_weights.get(c, 0.0) + w
    if not mapped_weights:
        cultures = list(lex.by_culture.keys())
        mapped_weights = {c: 1.0 / len(cultures) for c in cultures}

    primary_culture = (
        resolve_placename_culture(prominent_person[2], lex, rng)
        if prominent_person is not None and (prominent_person[2] or "").strip()
        else _weighted_choice(rng, [(c, w) for c, w in mapped_weights.items()])
    )

    use_dual = rng.random() < max(0.0, min(1.0, dual_affix_probability))
    best: GeneratedSettlementName | None = None

    if use_dual:
        for _ in range(4):
            candidate = _build_dual_affix_settlement_candidate(
                rng=rng,
                lex=lex,
                primary_culture=primary_culture,
                mapped_weights=mapped_weights,
                cat_w=cat_w,
                inhabitant_cultures=inhabitant_cultures,
            )
            if candidate is None:
                continue
            best = _best_settlement_candidate(best, candidate)
            if _display_letter_count(candidate.display_name) <= SETTLEMENT_DISPLAY_IDEAL_LETTERS:
                return candidate

    for _ in range(SETTLEMENT_NAME_CANDIDATE_ATTEMPTS):
        candidate = _build_patronymic_settlement_candidate(
            rng=rng,
            lex=lex,
            primary_culture=primary_culture,
            mapped_weights=mapped_weights,
            cat_w=cat_w,
            prominent_person=prominent_person,
            db_path=effective_db_path,
        )
        best = _best_settlement_candidate(best, candidate)
        if _display_letter_count(candidate.display_name) <= SETTLEMENT_DISPLAY_IDEAL_LETTERS:
            return candidate

    if best is None:
        raise LookupError(f"No placename candidates available for culture={primary_culture!r}")
    return best


def seed_settlement_naming_for_region(
    *,
    world: str,
    region: Region,
    ctx: "SimulationContext",
    lex: PlacenameLexicon,
    rng: random.Random,
    settlement_slots: int = 1,
    site_slot: int | None = None,
    locative_display_probability: float = LOCATIVE_DISPLAY_PROBABILITY,
) -> tuple[GeneratedSettlementName, str]:
    """Return generated name and shared region placement JSON."""
    weights = region_ethnic_weights(ctx, region.region_id, db_path=ctx.db_path)
    prominent = pick_prominent_resident(ctx, region.region_id, rng)
    inhabitant_cultures = _inhabitant_cultures_for_region(ctx, region.region_id, lex, rng)
    gen = generate_settlement_name(
        rng=rng,
        lex=lex,
        ethnic_weights=weights,
        region=region,
        prominent_person=prominent,
        inhabitant_cultures=inhabitant_cultures,
        db_path=ctx.db_path,
    )
    geo_rng = make_region_geography_rng(
        world, region.region_id, slot=0, salt=getattr(ctx, "placename_rng_salt", 0)
    )
    graph = build_local_region_graph(
        world=world,
        region=region,
        rng=geo_rng,
        settlement_slots=max(1, int(settlement_slots)),
        primary_meaning=gen.primary_meaning,
        primary_category=gen.primary_category,
        db_path=ctx.db_path,
        map_seed=getattr(ctx, "world_map_seed", None),
        ethnic_weights=weights,
        placename_lexicon=lex,
        world_geometry=(
            ctx.world_map_geometry_for_settlements()
            if hasattr(ctx, "world_map_geometry_for_settlements")
            else None
        ),
    )
    used_names = {
        (st.display_name or "").strip()
        for st in getattr(ctx, "settlements_by_id", {}).values()
        if getattr(st, "region_id", "") == region.region_id and (st.display_name or "").strip()
    }
    used_names.update(
        (feature.display_name or "").strip()
        for feature in graph.features
        if (feature.display_name or "").strip()
    )
    target_site_slot = site_slot if site_slot is not None else max(1, int(settlement_slots))
    anchor = _anchor_feature_for_slot(graph, target_site_slot)
    anchor_kind = (getattr(anchor, "kind", "") or "").strip().lower() if anchor is not None else ""
    primary_kind = feature_kind_for_meaning(gen.primary_meaning, gen.primary_category)
    has_locative_anchor = (
        anchor is not None
        and (getattr(anchor, "display_name", None) or "").strip()
        and (anchor_kind in _LOCATIVE_ANCHOR_KINDS or primary_kind in _LOCATIVE_ANCHOR_KINDS)
    )
    if has_locative_anchor:
        display = _unique_display_name(gen.display_name, used_names)
        if _should_use_locative_display(
            rng=rng,
            anchor_kind=anchor_kind,
            primary_kind=primary_kind,
            probability=locative_display_probability,
        ):
            locative_display = _locative_settlement_display(
                gen.display_name,
                anchor_kind,
                used_names,
            )
            if locative_display is not None:
                display = locative_display
        gen = replace(
            gen,
            display_name=display,
            etymology=_join_etymology_parts(
                gen.etymology,
                f"by {anchor_kind or 'landmark'} {anchor.display_name}",
            ),
        )
    else:
        gen = replace(gen, display_name=_unique_display_name(gen.display_name, used_names))
    placement_json = graph.to_json()
    return gen, placement_json
