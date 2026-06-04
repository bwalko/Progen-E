"""Narrative callouts for genome traits based on distance from typical magnitude (~50)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from library.person import Person
from library.random_traits import DEFAULT_DB_PATH, DEFAULT_GENOME_MAGNITUDE_STDEV

# Upper bounds on z = (|v| - ref)/sigma (bad) or (ref - |v|)/sigma (good).
# z <= c0: no callout. (c0, c1]: descriptor only. (c1, c2]: very. (c2, c3]:
# extremely / remarkably. z > c3: clinically / incredibly.
DEFAULT_PERSONALITY_Z_CUTOFFS: tuple[float, float, float, float] = (
    1.0,
    2.0,
    2.5,
    3.0,
)


def _connect(db_path: Path | str | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True)
class GenomeTraitNote:
    """A single trait worth calling out (|magnitude| unusually far from ~50)."""

    trait: str
    value: float
    magnitude: float
    phrase: str


@lru_cache(maxsize=8)
def _genome_rows_cached(db_path_s: str) -> tuple[dict[str, str], ...]:
    """Parsed ``genome`` rows keyed by resolved-path string; ``()`` if table missing."""
    with closing(_connect(db_path_s)) as conn:
        try:
            rows = [
                {str(k): "" if row[k] is None else str(row[k]).strip() for k in row.keys()}
                for row in conn.execute("SELECT * FROM genome")
            ]
        except sqlite3.OperationalError:
            return ()
    return tuple(rows)


def _label_for(row: dict[str, str], side: str) -> str:
    """Prefer narrative ``*_description`` columns; fall back to deviation/centerpoint."""
    if side == "deficient":
        return row.get("deficient description") or row.get("deficient deviation") or ""
    if side == "excess":
        return row.get("excess description") or row.get("excess deviation") or ""
    return row.get("optimal description") or row.get("optimal centerpoint") or ""


def _callout_level(
    z: float,
    c0: float,
    c1: float,
    c2: float,
    c3: float,
) -> int:
    """0 = silent; 1 = plain label; 2 = very; 3 = extremely/remarkably; 4 = top."""
    if z <= c0:
        return 0
    if z <= c1:
        return 1
    if z <= c2:
        return 2
    if z <= c3:
        return 3
    return 4


def _phrase_for_level(level: int, *, label: str, toward_bad: bool) -> str:
    if level <= 0:
        return ""
    if level == 1:
        return label
    if level == 2:
        return f"very {label}"
    if level == 3:
        w = "extremely" if toward_bad else "remarkably"
        return f"{w} {label}"
    w = "clinically" if toward_bad else "incredibly"
    return f"{w} {label}"


def interpret_genome_personality(
    person: Person,
    *,
    db_path: Path | str | None = None,
    sigma: float = DEFAULT_GENOME_MAGNITUDE_STDEV,
    reference_magnitude: float = 50.0,
    z_cutoffs: tuple[float, float, float, float] = DEFAULT_PERSONALITY_Z_CUTOFFS,
) -> list[GenomeTraitNote]:
    """Return narrative notes for traits unusually far from ordinary magnitude.

    Genome values are signed deviations from **0** (ideal). **Magnitude** ``|v|``
    is what ``choose_genome`` draws around mean ``reference_magnitude`` (50) with
    spread ``sigma`` (default 15): ordinary people cluster near |v|≈50; |v|
    toward **100** is unusually dysfunctional; |v| toward **0** is unusually
    close to the ideal.

    Let ``z`` be ``(|v| - ref) / sigma`` when ``|v| > ref`` (toward dysfunction)
    or ``(ref - |v|) / sigma`` when ``|v| < ref`` (toward ideal). With default
    ``z_cutoffs = (1, 2, 2.5, 3)`` as ``(c0, c1, c2, c3)``:

    - ``z <= c0``: no callout.
    - ``c0 < z <= c1``: **deficient / excess / optimal** label only (no adverb).
    - ``c1 < z <= c2``: **very** + label.
    - ``c2 < z <= c3``: **extremely** + label (bad) or **remarkably** + optimal.
    - ``z > c3``: **clinically** + pole (bad) or **incredibly** + optimal.

    ``z_cutoffs`` must be strictly increasing. Override for different banding.

    Phrases use the narrative ``deficient description`` / ``optimal description`` /
    ``excess description`` columns when present (designed to read as
    "Steve is <phrase>"), falling back to the legacy ``*deviation`` / ``optimal
    centerpoint`` labels when descriptions are missing.

    Traits within the ordinary band produce **no** entry (nothing listed): only
    extremes are returned, so callers can record this as a fixed-size summary.
    """
    c0, c1, c2, c3 = z_cutoffs
    if not (0 < c0 < c1 < c2 < c3):
        raise ValueError("z_cutoffs must be four strictly increasing positive values.")

    sigma = float(sigma)
    if sigma <= 0:
        raise ValueError("sigma must be positive.")

    ref = float(reference_magnitude)
    notes: list[GenomeTraitNote] = []

    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    rows = _genome_rows_cached(str(path.resolve()))

    row_by_trait: dict[str, dict[str, str]] = {}
    for row in rows:
        key = (row.get("trait") or "").strip()
        if key:
            row_by_trait[key] = row

    for trait, raw in person.genome.items():
        row = row_by_trait.get(trait)
        if row is None:
            continue
        deficient = _label_for(row, "deficient")
        optimal = _label_for(row, "optimal")
        excess = _label_for(row, "excess")
        if not deficient or not optimal or not excess:
            continue

        v = float(raw)
        m = abs(v)
        z_bad = (m - ref) / sigma
        z_good = (ref - m) / sigma
        level_bad = _callout_level(z_bad, c0, c1, c2, c3) if m > ref else 0
        level_good = _callout_level(z_good, c0, c1, c2, c3) if m < ref else 0

        if level_bad > 0:
            label = excess if v > 0 else deficient
            phrase = _phrase_for_level(level_bad, label=label, toward_bad=True)
            notes.append(GenomeTraitNote(trait=trait, value=v, magnitude=m, phrase=phrase))
        elif level_good > 0:
            phrase = _phrase_for_level(level_good, label=optimal, toward_bad=False)
            notes.append(GenomeTraitNote(trait=trait, value=v, magnitude=m, phrase=phrase))

    return notes


__all__ = [
    "DEFAULT_PERSONALITY_Z_CUTOFFS",
    "GenomeTraitNote",
    "interpret_genome_personality",
]
