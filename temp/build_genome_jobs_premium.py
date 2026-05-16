"""One-off: split genome_jobs era columns into common + premium, write CSV."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "config" / "genome_jobs.csv"
DST = ROOT / "config" / "genome_jobs.csv"

ERA_KEYS = [
    "prehistoric_jobs",
    "bronze_age_jobs",
    "iron_age_jobs",
    "medieval_jobs",
    "modern_jobs",
]

PREMIUM_FRAGMENTS = (
    "archetype",
    "-equivalent",
    "abbess",
    "abbot-equivalent",
    "berserker",
    "demagogue",
    "gladiator",
    "longbowman",
    "inquisitor",
    "shock troop",
    "shock jock",
    "shock influencer",
    "shock comedian",
    "shock entertainer",
    "celebrity",
    "quant trader",
    "whistleblower",
    "dark-pattern",
    "meme creator",
    "conspiracy analyst",
    "conspiracy investigator",
    "conspiracy influencer",
    "brand mascot",
    "obsessive ",
    "fanatical",
    " zealot",
    "witch-hunter",
    "crusader-equivalent",
    "plague prophet",
    "secret police",
    "palace intriguer",
    "slave-driver",
    "warlord",
    "cartel boss",
    "corporate raider",
    "predatory founder",
    "monster-hunter",
    "mad-scientist",
    "couch-surfer",
    "helicopter parent",
    "nightlife promoter",
    "podcaster",
    "travel creator",
    "startup hopper",
    "career hopper",
    "tournament fighter",
    "expedition guide",
    "systems architect",
    "ombudsman",
    "scientist-equivalent",
    "firefighter-equivalent",
    "knight-equivalent",
    "governor-equivalent",
    "storm chaser",
    "cult leader",
    "cult founder",
    " cult ",
    "fandom organizer",
    "volatile founder",
    "reckless founder",
    "disruptive founder",
    "unstable founder",
    "predatory negotiator",
    "authoritarian leader",
    "mercenary captain",
    "robber baron-equivalent",
    "oracle",
    "court astrologer",
    "alchemist",
    "duelist",
    " spirit medium",
    "false prophet",
    " plague prophet",
    "jester",
    "courtesan-adjacent",
    "fashion model-equivalent",
    "monk/nun-equivalent",
    "pirate",
    "galley rower",
    "chariot daredevil",
    "shock infantry",
)

ENRICH_POOL: dict[str, tuple[str, ...]] = {
    "prehistoric_jobs": (
        "camp cook",
        "river gatherer",
        "bone tool maker",
        "hide preparer",
    ),
    "bronze_age_jobs": (
        "brick carrier",
        "irrigation trench digger",
        "dock porter",
        "kiln assistant",
    ),
    "iron_age_jobs": (
        "village smith helper",
        "road repair crew",
        "mill assistant",
        "ferry hand",
    ),
    "medieval_jobs": (
        "brewery assistant",
        "mill worker",
        "parish laborer",
        "town crier assistant",
    ),
    "modern_jobs": (
        "shift supervisor",
        "field technician",
        "clinic aide",
        "dispatch coordinator",
    ),
}

PREMIUM_ENRICH: dict[str, tuple[str, ...]] = {
    "prehistoric_jobs": (
        "band champion",
        "vision quest guide",
        "rare-tool specialist",
    ),
    "bronze_age_jobs": (
        "royal workshop specialist",
        "temple astronomer",
        "master chariot mechanic",
    ),
    "iron_age_jobs": (
        "siege craft specialist",
        "arena choreographer",
        "famous duel referee",
    ),
    "medieval_jobs": (
        "guild peak master",
        "cathedral clock specialist",
        "master siege engineer",
    ),
    "modern_jobs": (
        "prize-winning specialist",
        "venture-scale architect",
        "elite crisis negotiator",
    ),
}

PREMIUM_ENRICH_LABELS: frozenset[str] = frozenset(
    label.strip().lower()
    for tup in PREMIUM_ENRICH.values()
    for label in tup
)


def _is_premium(job: str) -> bool:
    jl = job.strip().lower()
    if jl in PREMIUM_ENRICH_LABELS:
        return True
    return any(s.lower() in jl for s in PREMIUM_FRAGMENTS)


def _split_cell(jobs: tuple[str, ...]) -> tuple[list[str], list[str]]:
    common: list[str] = []
    premium: list[str] = []
    for j in jobs:
        if _is_premium(j):
            premium.append(j)
        else:
            common.append(j)
    return common, premium


def _rebalance(common: list[str], premium: list[str], min_common: int) -> None:
    if not common and not premium:
        return
    while len(common) < min_common and premium:
        premium.sort(key=len)
        common.append(premium.pop(0))
    if len(common) == 0 and premium:
        premium.sort(key=len)
        common.append(premium.pop(0))


def _join(cells: list[str]) -> str:
    return "; ".join(cells)


def _merged_era_jobs(row: dict[str, str], ek: str) -> tuple[str, ...]:
    """Rebuild full job list from base column plus optional premium column."""
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in (row.get(ek) or "",):
        for p in chunk.split(";"):
            s = p.strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(s)
    prem_key = ek.replace("_jobs", "_premium_jobs")
    extra = row.get(prem_key)
    if extra:
        for p in extra.split(";"):
            s = p.strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(s)
    return tuple(parts)


def main() -> None:
    with SRC.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("no header")
        fieldnames_in = list(reader.fieldnames)
        rows = list(reader)

    base_fieldnames = [n for n in fieldnames_in if not n.endswith("_premium_jobs")]
    new_fields: list[str] = []
    for name in base_fieldnames:
        new_fields.append(name)
        if name in ERA_KEYS:
            new_fields.append(name.replace("_jobs", "_premium_jobs"))

    out_rows: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        new_row: dict[str, str] = {}
        for ek in ERA_KEYS:
            jobs = _merged_era_jobs(row, ek)
            common, premium = _split_cell(jobs)
            min_c = 2 if len(jobs) >= 4 else (1 if jobs else 0)
            _rebalance(common, premium, min_c)

            seen = set(x.lower() for x in common + premium)
            pool_c = ENRICH_POOL[ek]
            extra_c = pool_c[idx % len(pool_c)]
            if extra_c.lower() not in seen:
                common.append(extra_c)
                seen.add(extra_c.lower())
            pool_p = PREMIUM_ENRICH[ek]
            extra_p = pool_p[idx % len(pool_p)]
            if extra_p.lower() not in seen:
                premium.append(extra_p)
                seen.add(extra_p.lower())

            new_row[ek] = _join(common)
            new_row[ek.replace("_jobs", "_premium_jobs")] = _join(premium)

        for k, v in row.items():
            if k in ERA_KEYS or k.endswith("_premium_jobs"):
                continue
            new_row[k] = v

        out_rows.append(new_row)

    with DST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_fields, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"wrote {len(out_rows)} rows to {DST}")


if __name__ == "__main__":
    main()
