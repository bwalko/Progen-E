"""Normalize genome_jobs.csv titles: realistic mass-employable common jobs, cleaner premium labels.

Run from repo root:
    python utils/util_clean_genome_jobs.py
    python utils/util_extract_job_economics_skeleton.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

GENOME_JOBS = _ROOT / "config" / "genome_jobs.csv"

JOB_COLS = (
    "prehistoric_jobs",
    "prehistoric_premium_jobs",
    "bronze_age_jobs",
    "bronze_age_premium_jobs",
    "iron_age_jobs",
    "iron_age_premium_jobs",
    "medieval_jobs",
    "medieval_premium_jobs",
    "modern_jobs",
    "modern_premium_jobs",
)
COMMON_COLS = {c for c in JOB_COLS if "premium" not in c}

# Exact title replacements (preserve [M]/[F] tags).
REPLACEMENTS: dict[str, str] = {
    # Meta-labels -> real titles
    "firefighter-equivalent": "fire warden",
    "knight-equivalent soldier": "man-at-arms",
    "crusader-equivalent": "crusader",
    "nurse-equivalent": "infirmary aide",
    "scientist-equivalent natural philosopher": "natural philosopher",
    "ombudsman-equivalent": "public advocate",
    "fashion model-equivalent": "portrait model",
    "celebrity athlete-equivalent": "professional athlete",
    "martyr nurse-equivalent": "plague nurse",
    "monk/nun-equivalent": "lay brother",
    "lord-equivalent leader": "manor lord",
    "vendetta knight-equivalent": "feud knight",
    "governor-equivalent leader": "provincial governor",
    "abbess/abbot-equivalent leader": "abbess",
    "abbot/abbess-equivalent leader": "abbot",
    "abbott/abbess-equivalent leader": "abbot",
    "gladiator-persona": "gladiator",
    "monster-hunter persona": "bounty hunter",
    "courtesan-adjacent entertainer [F]": "court entertainer [F]",
    "courtesan-adjacent entertainer": "court entertainer",
    "slave-market-equivalent oppressor": "slave trader",
    "slaver-equivalent oppressor": "slave overseer",
    "slave-driver-equivalent oppressor": "plantation overseer",
    "secret police-equivalent": "secret police agent",
    "robber baron-equivalent": "industrial magnate",
    "warlord-equivalent leader": "warlord",
    "couch-surfer archetype": "day laborer",
    # Premium / game framing kept but neutralized where possible
    "vision quest guide": "spirit guide",
    "band champion": "tribal champion",
    "tool specialist": "master toolmaker",
    "duel referee": "dueling judge",
    "arena choreographer": "arena master",
    "master chariot mechanic": "chariot master",
    "master siege engineer": "siege engineer",
    "cathedral clock specialist": "clockmaker",
    "venture-scale architect": "master architect",
    "systems architect": "systems designer",
    "crisis negotiator": "crisis mediator",
    "diplomat-by-marriage": "marriage diplomat",
    "hunt captain": "hunting master",
    "camp judge": "camp arbiter",
    "diplomat-by-marriage": "marriage diplomat",
    # Behavior phrases -> occupations (common columns especially)
    "camp loafer": "camp helper",
    "camp drifter": "forager",
    "camp gambler": "dice player",
    "camp comedian": "storyteller",
    "camp mascot": "camp entertainer",
    "camp celebrity": "renowned hunter",
    "camp flirt": "camp host",
    "chaos bard": "traveling minstrel",
    "clownish storyteller": "traveling storyteller",
    "prankster": "street entertainer",
    "errand avoider": "household helper",
    "passive gatherer": "gatherer",
    "idle servant": "household servant",
    "tavern hanger-on": "tavern worker",
    "temple hanger-on": "temple attendant",
    "menial laborer": "laborer",
    "faction hanger-on": "retainer",
    "alliance hanger-on": "client retainer",
    "chief flatterer": "court attendant",
    "eager servant": "household servant",
    "gig drifter": "day laborer",
    "burnout dropout": "temp worker",
    "churn-prone temp": "temp worker",
    "churn-prone supervisor": "shift supervisor",
    "startup hopper": "contract worker",
    "career hopper": "contract worker",
    "couch-surfer archetype": "day laborer",
    "resource stealer": "poacher",
    "resource thief": "poacher",
    "resource hoarder": "granary keeper",
    "oath breaker": "smuggler",
    "exile maker": "border guard",
    "power broker": "faction broker",
    "rivalry trigger": "market seller",
    "feast chief": "feast steward",
    "debt maker": "moneylender",
    "prestige hunter": "courtier",
    "gift scatterer": "patron",
    "status dancer": "court performer",
    "rival maker": "guild rival",
    "attention seeker": "street performer",
    "braggart hunter": "bounty hunter",
    "self-promoting warrior": "mercenary",
    "court climber": "courtier",
    "patronage climber": "courtier",
    "rumor broker": "gossip monger",
    "gossip broker": "gossip monger",
    "faction splitter": "agitator",
    "cult starter": "street preacher",
    "spirit medium": "shaman",
    "feud starter": "feud witness",
    "riot leader": "mob leader",
    "vengeance speaker": "feud witness",
    "purity hunter": "temple guard",
    "taboo punisher": "temple guard",
    "single-prey hunter": "trapper",
    "feud pursuer": "bounty hunter",
    "revenge officer": "bailiff",
    "court schemer": "court advisor",
    "palace intriguer": "court advisor",
    "client-patron broker": "patronage broker",
    "patronage broker": "client broker",
    "populist agitator": "agitator",
    "loud preacher": "street preacher",
    "tournament braggart": "tournament fighter",
    "missed-watch guard": "night watch",
    "half-trained healer": "herbalist",
    "apprentice troublemaker": "apprentice",
    "young hunter": "hunter",
    "odd-job laborer": "day laborer",
    "unfocused creator": "craft apprentice",
    "unfocused researcher": "research assistant",
    "trend chaser": "market trader",
    "storm chaser": "weather watcher",
    "shock jock": "radio host",
    "meme creator": "content creator",
    "travel creator": "travel writer",
    "fandom organizer": "event organizer",
    "brand mascot": "brand ambassador",
    "brand evangelist": "sales promoter",
    "scam marketer": "market trader",
    "disinformation operator": "propagandist",
    "insider threat": "corporate spy",
    "corporate mole": "corporate spy",
    "political turncoat": "political defector",
    "scam partner": "confidence trickster",
    "office saboteur": "office clerk",
    "hostile influencer": "pundit",
    "exploitative landlord": "landlord",
    "household tyrant": "household manager",
    "moral guardian": "parish worker",
    "meddling steward": "steward",
    "child monopolizer": "nanny",
    "rescue blocker": "gatekeeper",
    "grief singer": "lament singer",
    "conflict sponge": "mediator",
    "turncoat scout": "spy",
    "mercenary turncoat": "mercenary",
    "double agent": "spy",
    "cutthroat merchant": "merchant",
    "political schemer": "court advisor",
    "cartel boss": "smuggling boss",
    "corporate raider": "corporate raider",
    "purity campaigner": "moral reformer",
    "extremist moderator": "forum moderator",
    "coercive executive": "plant manager",
    "surveillance manager": "security manager",
    "hardline prosecutor": "prosecutor",
    "punitive prosecutor": "prosecutor",
    "ideological activist": "activist",
    "whistleblower": "government clerk",
    "conspiracy influencer": "pundit",
    "organized criminal": "gang member",
    "cybercriminal": "computer fraudster",
    "conspiracy analyst": "intelligence analyst",
    "conspiracy investigator": "private investigator",
    "conspiracy influencer": "pundit",
    "celebrity entrepreneur": "entrepreneur",
    "celebrity philanthropist": "philanthropist",
    "shock comedian": "comedian",
    "shock influencer": "influencer",
    "nightlife promoter": "event promoter",
    "micromanaging manager": "office manager",
    "mercenary consultant": "security consultant",
    "corporate downsizer": "management consultant",
    "waiting-room attendant": "receptionist",
    "help desk support": "help desk agent",
    "remote support worker": "remote support agent",
    "repetitive assembly worker": "assembly worker",
    "groundskeeping assistant": "groundskeeper",
    "warehouse picker": "warehouse worker",
    "stockroom helper": "stock clerk",
    "maintenance worker": "maintenance technician",
    "legacy systems maintainer": "systems maintainer",
    "independent researcher": "research assistant",
    "night-shift technician": "night technician",
    "remote developer": "software developer",
    "hermit writer": "writer",
    "crisis broadcaster": "news reporter",
    "social media creator": "content creator",
    "customer success rep": "customer service rep",
    "luxury sales associate": "retail salesperson",
    "insurance processor": "insurance clerk",
    "parking officer": "parking attendant",
    "claims adjuster": "insurance adjuster",
    "forensic technician": "forensic analyst",
    "data-entry clerk": "data entry clerk",
    "data-entry worker": "data entry clerk",
    "routine QA tester": "quality inspector",
    "QA tester": "quality inspector",
    "clinic aide": "medical aide",
    "field technician": "field service technician",
    "dispatch coordinator": "dispatch clerk",
    "shift supervisor": "shift supervisor",
    "venture-scale architect": "master architect",
    # Bare generics in common pools (premium handled separately)
    "official": "tax clerk",
    "specialist": "craft worker",
    "leader": "foreman",
    "founder": "shop owner",
    "parent": "homemaker",
    "prophet": "street preacher",
    "oracle": "temple oracle",
    "demagogue": "agitator",
    "warlord": "mercenary captain",
    "tyrant leader": "strongman ruler",
    "celebrity": "public figure",
    "competitor": "market rival",
    "saboteur": "saboteur",
    "troll": "street heckler",
    "plague prophet": "plague preacher",
    "masked inquisitor": "inquisitor",
    "fertility cult figure": "temple priest",
    "cult founder": "cult leader",
    "cult leader": "sect leader",
    "ambush planner": "military scout",
    "raid planner": "military scout",
    "migration planner": "trail guide",
    "seasonal planner": "harvest planner",
    "winter planner": "storekeeper",
    "irrigation planner": "irrigation worker",
    "planner": "town planner",
    "famous duel referee": "dueling judge",
    "elite crisis negotiator": "crisis mediator",
    "prize-winning specialist": "prize artisan",
    "guild peak master": "guild master",
}

# Common-column-only replacements (premium may keep rarer labels).
COMMON_ONLY: dict[str, str] = {
    "inquisitor": "church warden",
    "inquisitor clerk": "church clerk",
    "inquisitor guard": "church guard",
    "inquisitor aide": "church aide",
    "zealot officer": "military officer",
    "zealot prosecutor": "prosecutor",
    "warlord": "mercenary captain",
    "gang member": "pickpocket",
    "sect leader": "street preacher",
    "cult leader": "street preacher",
    "strongman ruler": "town strongman",
    "mob leader": "rabble rouser",
    "smuggling boss": "smuggler",
    "computer fraudster": "forger",
    "confidence trickster": "huckster",
    "corporate spy": "office clerk",
    "political defector": "political aide",
    "plantation overseer": "plantation foreman",
    "slave trader": "bond broker",
    "slave overseer": "plantation foreman",
    "secret police agent": "police agent",
    "industrial magnate": "factory owner",
    "feud knight": "mounted soldier",
    "manor lord": "landowner",
    "provincial governor": "regional administrator",
    "tribal champion": "warrior",
    "spirit guide": "shaman",
    "master toolmaker": "toolmaker",
    "chariot master": "chariot builder",
    "siege engineer": "military engineer",
    "clockmaker": "clockmaker",
    "master architect": "architect",
    "systems designer": "systems engineer",
    "crisis mediator": "mediator",
    "marriage diplomat": "marriage broker",
    "hunting master": "master hunter",
    "camp arbiter": "camp elder",
    "arena master": "arena keeper",
    "dueling judge": "magistrate",
    "berserker": "shock trooper",
    "shock infantry [M]": "shock trooper [M]",
    "shock infantry": "shock trooper",
    "shock troop": "shock trooper",
    "gladiator [M]": "gladiator [M]",
    "duelist": "fencer",
    "pirate": "privateer",
    "privateer": "sailor",
    "highwayman": "highway robber",
    "grave robber": "gravedigger",
    "extortionist": "debt collector",
    "fraudster": "forger",
    "scammer": "huckster",
    "con artist": "trickster",
    "organized criminal": "gang member",
    "mercenary turncoat": "mercenary",
    "traitor": "informant",
    "deserter": "itinerant laborer",
    "outlaw": "fence",
    "tyrant leader": "strongman",
    "ration tyrant": "quartermaster",
    "winter tyrant": "storekeeper",
    "robber leader": "bandit chief",
    "bandit chief": "bandit",
}

# Premium-column overrides (rarer titles; avoid generic filler from common cleanup).
PREMIUM_OVERRIDES: dict[str, str] = {
    "craft worker": "master artisan",
    "specialist": "master artisan",
    "foreman": "works foreman",
    "shop owner": "founding merchant",
    "tax clerk": "chief tax clerk",
    "day laborer": "itinerant laborer",
    "mediator": "chief mediator",
    "architect": "celebrated architect",
    "shaman": "tribal shaman",
    "warrior": "champion warrior",
    "toolmaker": "master toolmaker",
}

# Extra mass-employable jobs to append per era when a common list is short (<6 unique).
ERA_FILLERS: dict[str, tuple[str, ...]] = {
    "prehistoric": (
        "gatherer",
        "hunter",
        "fisher",
        "herder",
        "trapper",
        "camp cook",
        "toolmaker",
        "basket maker",
        "hide preparer",
        "firewood carrier",
        "water carrier",
        "night watch",
        "scout",
        "healer",
        "child watcher",
        "net maker",
        "flint knapper",
        "bone worker",
        "leather worker",
        "fish cleaner",
    ),
    "bronze_age": (
        "farmer",
        "field hand",
        "porter",
        "dock hand",
        "quarry worker",
        "stable hand",
        "weaver",
        "potter",
        "brewer",
        "baker",
        "mill worker",
        "charcoal burner",
        "ore miner",
        "woodcutter",
        "household servant",
        "kitchen hand",
        "market seller",
        "messenger",
        "soldier",
        "guard",
        "rope maker",
        "brick maker",
        "tanner",
        "chariot driver",
        "irrigation digger",
    ),
    "iron_age": (
        "farmer",
        "farmhand",
        "road crew laborer",
        "quarry laborer",
        "dock worker",
        "teamster",
        "miller",
        "brewer",
        "tanner",
        "carpenter",
        "blacksmith helper",
        "mason helper",
        "rower",
        "sailor",
        "infantryman",
        "market helper",
        "innkeeper",
        "cook",
        "scribe",
        "household servant",
        "fuller",
        "cooper",
        "wheelwright",
        "shepherd",
        "charcoal burner",
    ),
    "medieval": (
        "farmer",
        "field hand",
        "miller",
        "brewer",
        "baker",
        "tailor",
        "weaver",
        "carpenter",
        "blacksmith",
        "mason",
        "teamster",
        "porter",
        "kitchen hand",
        "scullion",
        "stable hand",
        "shepherd",
        "fisherman",
        "market trader",
        "apprentice",
        "parish laborer",
        "cooper",
        "chandler",
        "fuller",
        "glover",
        "wheelwright",
    ),
    "modern": (
        "factory worker",
        "warehouse worker",
        "retail clerk",
        "office clerk",
        "cashier",
        "driver",
        "nurse aide",
        "teacher aide",
        "janitor",
        "security guard",
        "construction worker",
        "farm laborer",
        "restaurant worker",
        "hotel worker",
        "call center agent",
        "delivery driver",
        "machine operator",
        "maintenance worker",
        "sales clerk",
        "administrative assistant",
        "postal worker",
        "bus driver",
        "cleaner",
        "line cook",
        "store stocker",
    ),
}


def _split(cell: str | None) -> list[str]:
    if not cell:
        return []
    return [p.strip() for p in str(cell).split(";") if p.strip()]


def _join(parts: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "; ".join(out)


def _replace_token(token: str, *, common_only: bool) -> str:
    base = token.strip()
    if not base:
        return base
    sex_tag = ""
    m = re.match(r"^(.+?)\s*\[([mMfF])\]\s*$", base)
    if m:
        base = m.group(1).strip()
        sex_tag = f" [{m.group(2).upper()}]"
    new = REPLACEMENTS.get(base, base)
    if common_only:
        new = COMMON_ONLY.get(new, COMMON_ONLY.get(base, new))
    elif base in PREMIUM_OVERRIDES:
        new = PREMIUM_OVERRIDES[base]
    elif new in PREMIUM_OVERRIDES:
        new = PREMIUM_OVERRIDES[new]
    return new + sex_tag


def _fill_common_list(parts: list[str], era_col: str, row_index: int) -> list[str]:
    era = era_col.replace("_jobs", "")
    fillers = ERA_FILLERS.get(era, ())
    if len(parts) >= 6 or not fillers:
        return parts
    existing = {p.lower() for p in parts}
    start = (row_index * 3) % len(fillers)
    added = 0
    for offset in range(len(fillers)):
        if len(parts) >= 6 or added >= 3:
            break
        f = fillers[(start + offset) % len(fillers)]
        if f.lower() not in existing:
            parts.append(f)
            existing.add(f.lower())
            added += 1
    return parts


def clean_genome_jobs(path: Path = GENOME_JOBS) -> int:
    changed = 0
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row_index, row in enumerate(reader):
            new_row = dict(row)
            for col in JOB_COLS:
                if col not in new_row:
                    continue
                is_common = col in COMMON_COLS
                parts = [_replace_token(p, common_only=is_common) for p in _split(new_row[col])]
                if is_common:
                    parts = _fill_common_list(parts, col, row_index)
                joined = _join(parts)
                if joined != (new_row[col] or "").strip():
                    changed += 1
                new_row[col] = joined
            rows.append(new_row)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return changed


if __name__ == "__main__":
    n = clean_genome_jobs()
    print(f"Updated {n} job column cells in {GENOME_JOBS}")
