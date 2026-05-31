"""Ethnic proto-placeword lookup and placeholder landmark-name composition."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import random
import sqlite3
import unicodedata

from library.placenames_lexicon import (
    PlacenameLexicon,
    apply_affix_template,
    format_toponym_display,
    normalize_placename_stem,
)
from library.random_traits import DEFAULT_DB_PATH, _connect


@dataclass(frozen=True)
class EthnicProtoPlacewordRow:
    ethnic: str
    feature_type: str
    core_concept: str
    concept: str
    normalized_form: str


@dataclass(frozen=True)
class GeneratedFeatureName:
    display_name: str
    etymology: str
    ethnic: str
    feature_type: str
    core_concept: str
    normalized_form: str
    placename_category: str | None
    placename_meaning: str | None


_FEATURE_TYPE_BY_LOCAL_KIND: dict[str, tuple[str, ...]] = {
    "bay": ("gulf", "sea"),
    "bog": ("swamp", "marsh"),
    "cape": ("cape",),
    "cavern": ("cavern", "cave"),
    "cave": ("cave", "cavern"),
    "clearing": ("woodland", "forest"),
    "cliff": ("cape", "mountain"),
    "coast": ("sea", "cape", "ocean"),
    "fishery": ("river", "sea"),
    "ford": ("fords", "river"),
    "fords": ("fords", "river"),
    "forest": ("forest", "woodland"),
    "grove": ("forest", "woodland"),
    "harbor": ("natural harbor", "sea"),
    "hill": ("hill",),
    "island": ("island",),
    "lake": ("lake",),
    "landmark": ("hill", "plain"),
    "marsh": ("marsh", "swamp"),
    "meadow": ("grassland", "plain"),
    "mesa": ("hill", "mountain"),
    "mountain": ("mountain",),
    "oasis": ("well", "spring"),
    "pass": ("ridge", "mountain"),
    "pasture": ("grassland", "plain"),
    "plain": ("plain",),
    "quarry": ("mountain", "hill"),
    "ridge": ("ridge", "mountain"),
    "river": ("river",),
    "saltpan": ("plain", "sea"),
    "spring": ("spring",),
    "stream": ("river",),
    "swamp": ("swamp", "marsh"),
    "wadi": ("river",),
    "well": ("well", "spring"),
    "woodland": ("woodland", "forest"),
}

_PLACENAME_FEATURE_CATEGORIES: tuple[str, ...] = ("Topography", "Sacred", "Status")

_IE_MATRIX: dict[str, tuple[str, str, str, str]] = {
    "labial": ("p", "f", "bh", "b"),
    "dental": ("t", "th", "dh", "d"),
    "velar": ("k", "h", "gh", "g"),
}

_IE_TOKEN_TO_PLACE_MANNER: dict[str, tuple[str, int]] = {
    "bh": ("labial", 2),
    "dh": ("dental", 2),
    "gh": ("velar", 2),
    "th": ("dental", 1),
    "f": ("labial", 1),
    "h": ("velar", 1),
    "p": ("labial", 0),
    "t": ("dental", 0),
    "k": ("velar", 0),
    "c": ("velar", 0),
    "q": ("velar", 0),
    "b": ("labial", 3),
    "d": ("dental", 3),
    "g": ("velar", 3),
}

_GERMANIC_ETHNIC_HINTS = (
    "alemannic",
    "dutch",
    "english",
    "frankish",
    "frisian",
    "german",
    "germanic",
    "norse",
    "scots",
    "saxon",
)

_ITALIC_CELTIC_ETHNIC_HINTS = (
    "anglo-norman",
    "celtic",
    "french",
    "gaelic",
    "gallic",
    "gaulish",
    "greek",
    "irish",
    "italic",
    "latin",
    "norman",
    "roman",
)


def _clean_key(text: str) -> str:
    return " ".join((text or "").strip().casefold().replace("_", " ").split())


def _ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _ethnic_sound_law_branch(ethnic: str) -> str:
    key = _clean_key(ethnic)
    if any(hint in key for hint in _GERMANIC_ETHNIC_HINTS):
        return "germanic"
    if any(hint in key for hint in _ITALIC_CELTIC_ETHNIC_HINTS):
        return "italic_celtic"
    return "germanic"


def sound_law_branch_for_ethnic(ethnic: str) -> str:
    return _ethnic_sound_law_branch(ethnic)


def _normalize_sound_law_branch(branch: str) -> str:
    key = _clean_key(branch)
    if key in {"italic celtic", "italoceltic", "celtic italic"}:
        return "italic_celtic"
    if key in {"germanic", "german", "northern"}:
        return "germanic"
    return key.replace(" ", "_")


def _resolve_key(requested: str, available: list[str]) -> str | None:
    want = _clean_key(requested)
    if not want:
        return None
    by_clean = {_clean_key(a): a for a in available}
    if want in by_clean:
        return by_clean[want]
    for key, raw in by_clean.items():
        if want in key or key in want:
            return raw
    return None


@lru_cache(maxsize=8)
def _load_proto_placeword_rows(db_path_s: str) -> tuple[EthnicProtoPlacewordRow, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )
    with closing(_connect(path)) as conn:
        try:
            rows = conn.execute(
                """
                SELECT ethnic, feature_type, core_concept, concept, normalized_form
                FROM ethnic_proto_placewords
                WHERE ethnic IS NOT NULL
                  AND feature_type IS NOT NULL
                  AND core_concept IS NOT NULL
                  AND normalized_form IS NOT NULL
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return tuple()
    out: list[EthnicProtoPlacewordRow] = []
    for r in rows:
        eth = str(r["ethnic"] or "").strip()
        ft = str(r["feature_type"] or "").strip()
        core = str(r["core_concept"] or "").strip()
        norm = str(r["normalized_form"] or "").strip()
        if not eth or not ft or not core or not norm:
            continue
        out.append(
            EthnicProtoPlacewordRow(
                ethnic=eth,
                feature_type=ft,
                core_concept=core,
                concept=str(r["concept"] or "").strip(),
                normalized_form=norm,
            )
        )
    return tuple(out)


class EthnicProtoPlacewordLexicon:
    def __init__(self, rows: tuple[EthnicProtoPlacewordRow, ...]) -> None:
        self.rows = rows
        self.by_ethnic_feature: dict[tuple[str, str], list[EthnicProtoPlacewordRow]] = {}
        for row in rows:
            self.by_ethnic_feature.setdefault(
                (_clean_key(row.ethnic), _clean_key(row.feature_type)), []
            ).append(row)

    @classmethod
    def from_db(cls, *, db_path: Path | str | None = None) -> EthnicProtoPlacewordLexicon:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        return cls(_load_proto_placeword_rows(str(path.resolve())))

    def ethnics(self) -> list[str]:
        vals = sorted({r.ethnic for r in self.rows})
        return vals

    def feature_types_for_ethnic(self, ethnic: str) -> list[str]:
        key = _clean_key(ethnic)
        return sorted({r.feature_type for r in self.rows if _clean_key(r.ethnic) == key})

    def rows_for(self, *, ethnic: str, feature_type: str) -> list[EthnicProtoPlacewordRow]:
        return list(
            self.by_ethnic_feature.get((_clean_key(ethnic), _clean_key(feature_type)), [])
        )


def _shift_ie_token(
    token: str,
    *,
    branch: str,
    shift: int | None,
    reverse: bool,
) -> str:
    low = token.casefold()
    if low == "kw":
        if branch == "italic_celtic" and not reverse:
            return "qu"
        if branch == "germanic" and not reverse:
            return "hw"
        return "kw"

    place_manner = _IE_TOKEN_TO_PLACE_MANNER.get(low)
    if place_manner is None:
        return token
    place, manner = place_manner

    if branch == "italic_celtic" and not reverse:
        if manner == 0:
            return _IE_MATRIX[place][0]
        if manner in (2, 3):
            return _IE_MATRIX[place][3]
        return _IE_MATRIX[place][manner]

    delta = 1 if shift is None else int(shift)
    if reverse:
        delta = -delta
    return _IE_MATRIX[place][(manner + delta) % 4]


def _apply_ie_consonant_shift(
    text: str,
    *,
    branch: str,
    shift: int | None,
    reverse: bool,
) -> str:
    raw = _ascii_fold(text).casefold()
    out: list[str] = []
    i = 0
    while i < len(raw):
        ahead = raw[i : i + 3]
        if ahead == "qu" and branch == "italic_celtic" and reverse:
            out.append("kw")
            i += 2
            continue
        if ahead[:2] == "hw" and branch == "germanic" and reverse:
            out.append("kw")
            i += 2
            continue
        two = raw[i : i + 2]
        if two in {"bh", "dh", "gh", "th", "kw"}:
            out.append(_shift_ie_token(two, branch=branch, shift=shift, reverse=reverse))
            i += 2
            continue
        out.append(_shift_ie_token(raw[i], branch=branch, shift=shift, reverse=reverse))
        i += 1
    return "".join(out)


def _smooth_ie_toponym(text: str, *, branch: str, reverse: bool) -> str:
    if reverse:
        if branch == "germanic":
            return text.replace("erg", "eorg")
        return text
    out = text
    if branch == "germanic":
        out = out.replace("eor", "er")
        out = out.replace("ontho", "anth")
        out = out.replace("ontho", "anth")
        out = out.removesuffix("o")
    elif branch == "italic_celtic":
        out = out.replace("kw", "qu")
        out = out.replace("eor", "or")
        out = out.replace("uorg", "urg")
        out = out.removesuffix("o")
    return out


def rewind_constructed_toponym_placeholder(
    text: str,
    *,
    branch: str = "germanic",
    shift: int | None = None,
    reverse: bool = False,
) -> str:
    """Apply the current IE modulo-4 placename sound law.

    ``branch="germanic"`` uses the modulo shift ``M_future = (M + 1) % 4``
    over labial/dental/velar stop rows, with ``kw`` rendered as ``hw``.
    ``branch="italic_celtic"`` keeps voiceless stops stable while merging PIE
    voiced aspirates into voiced stops, with ``kw`` rendered as Latin-like
    ``qu``. ``reverse=True`` walks the Germanic-style matrix backward for
    ancient-name reconstruction.
    """
    shifted = _apply_ie_consonant_shift(
        text,
        branch=_normalize_sound_law_branch(branch),
        shift=shift,
        reverse=reverse,
    )
    smoothed = _smooth_ie_toponym(
        shifted,
        branch=_normalize_sound_law_branch(branch),
        reverse=reverse,
    )
    return normalize_placename_stem(smoothed)


def probabilistic_sound_law_run_count(rng: random.Random) -> int:
    """Choose how many language-law passes to apply to a generated place name."""
    roll = rng.random()
    if roll < 0.50:
        return 0
    if roll < 0.80:
        return 1
    if roll < 0.95:
        return 2
    return 3


def apply_probabilistic_sound_law_runs(
    text: str,
    *,
    rng: random.Random,
    branch: str = "germanic",
) -> str:
    out = normalize_placename_stem(text)
    for _ in range(probabilistic_sound_law_run_count(rng)):
        out = rewind_constructed_toponym_placeholder(out, branch=branch)
    return out


def _weighted_ethnic_choice(
    rng: random.Random,
    ethnic_weights: dict[str, float],
    available_ethnics: list[str],
) -> str | None:
    if not available_ethnics:
        return None
    weighted: list[tuple[str, float]] = []
    for eth, w in ethnic_weights.items():
        resolved = _resolve_key(eth, available_ethnics)
        if resolved is not None and w > 0:
            weighted.append((resolved, float(w)))
    if weighted:
        names, weights = zip(*weighted)
        return rng.choices(list(names), weights=list(weights), k=1)[0]
    return rng.choice(available_ethnics)


def _feature_type_candidates(local_kind: str) -> tuple[str, ...]:
    key = _clean_key(local_kind)
    return _FEATURE_TYPE_BY_LOCAL_KIND.get(key, (key,))


def _pick_proto_row(
    rng: random.Random,
    proto: EthnicProtoPlacewordLexicon,
    *,
    ethnic: str,
    local_kind: str,
) -> EthnicProtoPlacewordRow | None:
    available_features = proto.feature_types_for_ethnic(ethnic)
    for candidate in _feature_type_candidates(local_kind):
        resolved_feature = _resolve_key(candidate, available_features)
        if resolved_feature is None:
            continue
        rows = proto.rows_for(ethnic=ethnic, feature_type=resolved_feature)
        if not rows:
            continue
        by_core: dict[str, list[EthnicProtoPlacewordRow]] = {}
        for row in rows:
            by_core.setdefault(row.core_concept, []).append(row)
        core = rng.choice(sorted(by_core.keys()))
        return rng.choice(by_core[core])
    return None


def _pick_placename_row(rng: random.Random, lex: PlacenameLexicon, ethnic: str):
    cultures = list(lex.by_culture.keys())
    culture = _resolve_key(ethnic, cultures) or (rng.choice(cultures) if cultures else "")
    pool = [
        row
        for row in lex.by_culture.get(culture, [])
        if row.category in _PLACENAME_FEATURE_CATEGORIES
    ]
    if not pool:
        pool = [row for row in lex.rows if row.category in _PLACENAME_FEATURE_CATEGORIES]
    if not pool:
        return None
    weights = [
        1.0 if row.category == "Topography" else 0.42 if row.category == "Sacred" else 0.34
        for row in pool
    ]
    return rng.choices(pool, weights=weights, k=1)[0]


def generate_feature_name(
    *,
    rng: random.Random,
    proto: EthnicProtoPlacewordLexicon,
    placenames: PlacenameLexicon,
    ethnic_weights: dict[str, float],
    local_kind: str,
) -> GeneratedFeatureName | None:
    """Name a natural/local feature once from resident ethnicity and proto lexicon."""
    ethnic = _weighted_ethnic_choice(rng, ethnic_weights, proto.ethnics())
    if ethnic is None:
        return None
    proto_row = _pick_proto_row(rng, proto, ethnic=ethnic, local_kind=local_kind)
    if proto_row is None:
        return None
    branch = _ethnic_sound_law_branch(ethnic)
    stem = normalize_placename_stem(proto_row.normalized_form)
    place_row = _pick_placename_row(rng, placenames, ethnic)
    if place_row is not None:
        affix = place_row.pick_settlement_affix_variant(rng)
        compound = apply_affix_template(affix, stem)
        meaning = place_row.original_meaning
        category = place_row.category
    else:
        compound = stem
        meaning = None
        category = None
    display = format_toponym_display(
        apply_probabilistic_sound_law_runs(compound, rng=rng, branch=branch)
    )
    etymology_bits = [
        proto_row.normalized_form,
        proto_row.feature_type,
        proto_row.core_concept,
    ]
    if meaning:
        etymology_bits.append(str(meaning))
    return GeneratedFeatureName(
        display_name=display,
        etymology=" · ".join(bit for bit in etymology_bits if bit),
        ethnic=ethnic,
        feature_type=proto_row.feature_type,
        core_concept=proto_row.core_concept,
        normalized_form=proto_row.normalized_form,
        placename_category=category,
        placename_meaning=meaning,
    )
