"""Load ``placenames`` config table and helpers for affix/stem joining."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from library.random_traits import DEFAULT_DB_PATH, _connect


def format_toponym_display(name: str) -> str:
    """Readable place styling: one leading capital, remaining letters lowercase.

    Avoids CamelCase artifacts such as ``PorthYeqse`` from title-cased stems.
    """
    s = (name or "").strip()
    if not s:
        return s
    return s.casefold().capitalize()


def normalize_placename_stem(text: str) -> str:
    """Alphanumeric stem for compounds; preserves Unicode letters."""
    s = (text or "").strip()
    if not s:
        return "Place"
    # Keep letters (including accented); drop punctuation/spaces for glue.
    out = "".join(ch for ch in s if ch.isalnum())
    return out if out else "Place"


_VOWELS = frozenset("aeiouAEIOU")


def _is_vowel(c: str) -> bool:
    return len(c) == 1 and c in _VOWELS


def join_tokens(left: str, right: str, *, prefer_hyphen: bool = False) -> str:
    """Join two morphemes; reduce awkward double consonants/vowels at the boundary."""
    a = (left or "").strip()
    b = (right or "").strip()
    if not a:
        return b
    if not b:
        return a
    if prefer_hyphen:
        return f"{a}-{b}"

    la = a[-1]
    rb = b[0]
    # Same letter (Latin letters only): drop duplicate from right.
    if la.lower() == rb.lower() and la.isascii() and la.isalpha() and rb.isascii() and rb.isalpha():
        return a + b[1:]
    # Double vowel at boundary: drop first vowel of right.
    if _is_vowel(la) and _is_vowel(rb):
        rest = b[1:]
        while rest and _is_vowel(rest[0]):
            rest = rest[1:]
        return a + rest
    # Double consonant (simple heuristic): drop one from right if same char class duplicate.
    if (
        la.isalpha()
        and rb.isalpha()
        and la.lower() == rb.lower()
        and not _is_vowel(la)
        and not _is_vowel(rb)
    ):
        return a + b[1:]
    return a + b


def _trim_duplicate_affix_edge(stem: str, fragment: str, *, trailing: bool) -> str:
    """If ``stem`` already ends (or starts) with ``fragment``, strip that overlap once."""
    if not stem or not fragment:
        return stem
    s, f = stem.casefold(), fragment.casefold()
    if trailing:
        if len(stem) >= len(fragment) and s.endswith(f):
            return stem[: len(stem) - len(fragment)]
        return stem
    if len(stem) >= len(fragment) and s.startswith(f):
        return stem[len(fragment) :]
    return stem


def split_affix_variants(cell: str | None) -> list[str]:
    """Split CSV affix cell on comma; each variant should contain exactly one ``$``."""
    if cell is None:
        return []
    raw = str(cell).strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts if parts else [raw]


@dataclass(frozen=True)
class PlacenameRow:
    culture: str
    category: str
    original_meaning: str
    affix_variants: tuple[str, ...]
    archaic_affix_variants: tuple[str, ...]

    def pick_affix_variant(self, rng) -> str:
        variants = list(self.affix_variants)
        if not variants:
            return ""
        return rng.choice(variants)

    def pick_settlement_affix_variant(self, rng) -> str:
        """Prefer **Archaic Affix** patterns from ``placenames.csv`` for built settlement names."""
        arch = [v for v in self.archaic_affix_variants if v.strip()]
        if arch:
            return rng.choice(arch)
        return self.pick_affix_variant(rng)


def apply_affix_template(template: str, stem: str) -> str:
    """Insert ``stem`` into a single ``$`` placeholder; join fragments with :func:`join_tokens`.

    When the stem already ends or starts with the same fragment as the template prefix/suffix,
    trim once to avoid doubled morphemes (e.g. ``...grund`` + ``$grund``).
    """
    t = (template or "").strip()
    st = normalize_placename_stem(stem)
    if not t:
        return st
    if "$" not in t:
        return join_tokens(t, st)

    count = t.count("$")
    if count != 1:
        # Ambiguous row: fall back to stem only (caller should prefer clean variants).
        parts = [p.strip() for p in t.split("$") if p.strip()]
        if len(parts) >= 2:
            st_mid = _trim_duplicate_affix_edge(st, parts[0], trailing=False)
            st_mid = _trim_duplicate_affix_edge(st_mid, parts[1], trailing=True)
            return join_tokens(parts[0], join_tokens(st_mid, parts[1]))
        return st

    idx = t.index("$")
    prefix = t[:idx].strip()
    suffix = t[idx + 1 :].strip()

    if not prefix and not suffix:
        return st
    if not prefix:
        st2 = _trim_duplicate_affix_edge(st, suffix, trailing=True)
        return join_tokens(st2, suffix)
    if not suffix:
        st2 = _trim_duplicate_affix_edge(st, prefix, trailing=False)
        return join_tokens(prefix, st2)
    st_mid = _trim_duplicate_affix_edge(st, prefix, trailing=False)
    st_mid = _trim_duplicate_affix_edge(st_mid, suffix, trailing=True)
    core = join_tokens(prefix, st_mid)
    return join_tokens(core, suffix)


@lru_cache(maxsize=8)
def _load_placename_rows(db_path_s: str) -> tuple[PlacenameRow, ...]:
    path = Path(db_path_s)
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT "Culture", "Category", "Original Meaning", "Affix",
                   "Archaic Affix"
            FROM placenames
            """
        ).fetchall()
        archaic_col = True
    except sqlite3.OperationalError:
        rows = conn.execute(
            """
            SELECT "Culture", "Category", "Original Meaning", "Affix"
            FROM placenames
            """
        ).fetchall()
        archaic_col = False
    finally:
        conn.close()

    out: list[PlacenameRow] = []
    for r in rows:
        culture = str(r["Culture"] or "").strip()
        cat = str(r["Category"] or "").strip()
        meaning = str(r["Original Meaning"] or "").strip()
        affix_cell = str(r["Affix"] or "").strip()
        archaic_affix_cell = (
            str(r["Archaic Affix"] or "").strip() if archaic_col else ""
        )
        variants = tuple(split_affix_variants(affix_cell))
        archaic_variants = tuple(split_affix_variants(archaic_affix_cell))
        if not culture or (not variants and not archaic_variants):
            continue
        if not variants:
            variants = archaic_variants
        out.append(
            PlacenameRow(
                culture=culture,
                category=cat or "Unknown",
                original_meaning=meaning or cat,
                affix_variants=variants,
                archaic_affix_variants=archaic_variants,
            )
        )
    return tuple(out)


class PlacenameLexicon:
    """Indexed placename rows for constrained sampling."""

    def __init__(self, rows: tuple[PlacenameRow, ...]) -> None:
        self.rows = rows
        self.by_culture: dict[str, list[PlacenameRow]] = {}
        self.by_culture_category: dict[tuple[str, str], list[PlacenameRow]] = {}
        self.by_culture_meaning: dict[tuple[str, str], list[PlacenameRow]] = {}
        for row in rows:
            self.by_culture.setdefault(row.culture, []).append(row)
            self.by_culture_category.setdefault((row.culture, row.category), []).append(row)
            key = (row.culture, row.original_meaning.lower())
            self.by_culture_meaning.setdefault(key, []).append(row)

    def cultures(self) -> Iterator[str]:
        return iter(sorted(self.by_culture.keys()))

    @classmethod
    def from_db(cls, *, db_path: Path | str | None = None) -> PlacenameLexicon:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        rows = _load_placename_rows(str(path.resolve()))
        return cls(rows)


def preload_placename_cache(*, db_path: Path | str | None = None) -> None:
    """Warm :func:`_load_placename_rows` cache."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    _load_placename_rows(str(path.resolve()))


