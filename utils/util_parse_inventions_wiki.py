"""Parse a wiki invention timeline into Progen-E innovation config CSVs.

The raw source CSV is traceability-first. The gameplay CSV is an editable
starting point for local analogues; simulation code treats it as the catalog.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "Timeline of historic inventions.wiki"
DEFAULT_SOURCE_OUT = PROJECT_ROOT / "config" / "innovation_source_rows.csv"
DEFAULT_INNOVATIONS_OUT = PROJECT_ROOT / "config" / "innovations.csv"
CURRENT_YEAR = 2026

SOURCE_FIELDS = (
    "source_id",
    "source_file",
    "source_line",
    "section",
    "subsection",
    "date_text",
    "history_year_from",
    "history_year_to",
    "history_year",
    "date_quality",
    "title",
    "summary",
    "wiki_links",
    "parse_notes",
)

INNOVATION_FIELDS = (
    "innovation_id",
    "source_id",
    "source_link",
    "source_title",
    "analogue_name",
    "category",
    "domain",
    "era_id",
    "history_year",
    "history_year_from",
    "history_year_to",
    "rank",
    "spreadability",
    "complexity",
    "starter_prevalence",
    "prerequisite_ids",
    "curation_status",
    "notes",
)

ERA_BANDS: tuple[tuple[str, int, int], ...] = (
    ("paleolithic", -9_999_999, -10_001),
    ("neolithic", -10_000, -3301),
    ("bronze_age", -3300, -1201),
    ("iron_age", -1200, -501),
    ("classical", -500, 499),
    ("medieval", 500, 1499),
    ("early_modern", 1500, 1749),
    ("industrial", 1750, 1899),
    ("modern", 1900, 1979),
    ("digital", 1980, 999_999),
)

CATEGORY_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("computer|digital|software|internet|transistor|semiconductor", "computing", "computation"),
    ("weapon|spear|bow|arrow|gun|rifle|cannon|explosive|armor|tank", "military", "warfare"),
    ("medicine|surgery|dentistry|vaccine|antibiotic|anesthesia|hospital|drug", "medicine", "medicine"),
    ("ship|boat|sail|canoe|compass|navigation|anchor|harbor", "maritime", "navigation"),
    ("stone tool|bone tool|hafting|adhesive|glue|pottery|kiln|textile|weaving|clothing", "craft", "craft"),
    ("wheel|cart|road|rail|automobile|airplane|flight|engine", "transport", "transport"),
    ("writing|alphabet|paper|printing|book|newspaper|telegraph|telephone|radio", "communication", "writing"),
    ("agriculture|farming|plough|plow|irrigation|crop|domestication|bread", "agriculture", "agriculture"),
    ("smelt|metal|bronze|iron|steel|copper|glass|cement|concrete|ceramic|mining|mine", "material", "materials"),
    ("steam|electric|battery|power|windmill|watermill|solar|nuclear", "energy", "power"),
    ("calendar|mathematics|astronomy|telescope|microscope|science", "science", "scholarship"),
    ("trade|commerce|coin|money|bank|accounting|law|bureaucracy|tax|contract", "administration", "accounting"),
    ("funeral|burial|music|flute|bullroarer|art|paint|pigment|instrument|game|theatre|theater", "culture", "art"),
    ("textile|weaving|clothing|pottery|kiln|tool|adhesive|glue", "craft", "craft"),
    ("bed|well|sewer|toilet|soap|cooking|food storage|fermentation", "domestic", "household"),
)


@dataclass(frozen=True)
class ParsedSourceRow:
    source_id: str
    source_file: str
    source_line: int
    section: str
    subsection: str
    date_text: str
    history_year_from: int | None
    history_year_to: int | None
    history_year: int | None
    date_quality: str
    title: str
    summary: str
    wiki_links: tuple[str, ...]
    parse_notes: str

    def as_csv_row(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "section": self.section,
            "subsection": self.subsection,
            "date_text": self.date_text,
            "history_year_from": self.history_year_from,
            "history_year_to": self.history_year_to,
            "history_year": self.history_year,
            "date_quality": self.date_quality,
            "title": self.title,
            "summary": self.summary,
            "wiki_links": ";".join(self.wiki_links),
            "parse_notes": self.parse_notes,
        }


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_templates(text: str) -> str:
    out = text
    for _ in range(10):
        next_out = re.sub(r"\{\{[^{}]*\}\}", " ", out)
        if next_out == out:
            break
        out = next_out
    return out


def extract_wiki_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]", text):
        target = _collapse_spaces(match.group(1))
        if not target or target in seen:
            continue
        seen.add(target)
        links.append(target)
    return links


def clean_wiki_markup(text: str) -> str:
    out = re.sub(r"<ref\b[^>/]*/>", " ", text, flags=re.IGNORECASE)
    out = re.sub(r"<ref\b[^>]*>.*?</ref>", " ", out, flags=re.IGNORECASE | re.DOTALL)
    out = _strip_templates(out)
    out = re.sub(
        r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]",
        lambda m: m.group(2) or m.group(1),
        out,
    )
    out = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", out)
    out = re.sub(r"\[https?://[^\]]+\]", " ", out)
    out = re.sub(r"'{2,5}", "", out)
    out = re.sub(r"<[^>]+>", " ", out)
    out = html.unescape(out)
    return _collapse_spaces(out)


def _ordinal_number(text: str) -> int | None:
    cleaned = text.lower().strip()
    words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
        "eleventh": 11,
        "twelfth": 12,
    }
    if cleaned in words:
        return words[cleaned]
    match = re.search(r"(\d+)", cleaned)
    if match:
        return int(match.group(1))
    return None


def _years_ago_to_year(value: float, multiplier: int) -> int:
    return int(round(CURRENT_YEAR - value * multiplier))


def _date_result(years: list[int], quality: str, notes: list[str]) -> tuple[int | None, int | None, int | None, str, str]:
    if not years:
        return None, None, None, "unparsed", ";".join(notes)
    year_from = min(years)
    year_to = max(years)
    year = int(round(sum(years) / len(years)))
    return year_from, year_to, year, quality, ";".join(notes)


def normalize_date_text(date_text: str) -> tuple[int | None, int | None, int | None, str, str]:
    """Return historical year range, representative year, quality, and notes."""
    original = _collapse_spaces(date_text)
    text = original.lower()
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    text = text.replace("b.c.", "bc").replace("bce", "bc")
    text = text.replace("c.e.", "ce").replace("a.d.", "ad")
    text = text.replace(",", "")
    notes: list[str] = []
    if re.search(r"\bc\.|circa|about|approx", text):
        notes.append("approximate")

    millennium = re.search(
        r"(?P<num>\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+millennium\s*(?P<era>bc|bce|ce|ad)?",
        text,
    )
    if millennium:
        num = _ordinal_number(millennium.group("num"))
        era = (millennium.group("era") or "ce").lower()
        if num is not None:
            if era in {"bc", "bce"}:
                return -num * 1000, -((num - 1) * 1000 + 1), -num * 1000 + 500, "millennium", ";".join(notes)
            return (num - 1) * 1000 + 1, num * 1000, (num - 1) * 1000 + 500, "millennium", ";".join(notes)

    century = re.search(
        r"(?P<num>\d+(?:st|nd|rd|th)?|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+century\s*(?P<era>bc|bce|ce|ad)?",
        text,
    )
    if century:
        num = _ordinal_number(century.group("num"))
        era = (century.group("era") or "ce").lower()
        if num is not None:
            if era in {"bc", "bce"}:
                return -num * 100, -((num - 1) * 100 + 1), -num * 100 + 50, "century", ";".join(notes)
            return (num - 1) * 100 + 1, num * 100, (num - 1) * 100 + 50, "century", ";".join(notes)

    ago_matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*(mya|million years ago|kya|ka|thousand years ago)\b",
        text,
    )
    all_ago_nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if ago_matches and len(ago_matches) == len(all_ago_nums):
        years: list[int] = []
        for num_text, unit in ago_matches:
            multiplier = 1_000_000 if unit.startswith("m") else 1000
            years.append(_years_ago_to_year(float(num_text), multiplier))
        return _date_result(years, "range" if len(years) > 1 else "approximate", notes)
    if "mya" in text or "million years ago" in text:
        return _date_result([_years_ago_to_year(n, 1_000_000) for n in all_ago_nums], "range" if len(all_ago_nums) > 1 else "approximate", notes)
    if "kya" in text or "ka" in text or "thousand years ago" in text:
        return _date_result([_years_ago_to_year(n, 1000) for n in all_ago_nums], "range" if len(all_ago_nums) > 1 else "approximate", notes)

    bc_nums = [int(n) for n in re.findall(r"\d+", text)] if re.search(r"\bbc\b", text) else []
    if bc_nums:
        return _date_result([-n for n in bc_nums], "range" if len(bc_nums) > 1 else "exact", notes)

    ce_nums = [int(n) for n in re.findall(r"\d+", text)] if re.search(r"\b(ce|ad)\b", text) else []
    if ce_nums:
        return _date_result(ce_nums, "range" if len(ce_nums) > 1 else "exact", notes)

    plain_nums = [int(n) for n in re.findall(r"\d{3,4}", text)]
    if plain_nums:
        return _date_result(plain_nums, "range" if len(plain_nums) > 1 else "exact", notes)

    notes.append("date text not normalized")
    return None, None, None, "unparsed", ";".join(notes)


def era_for_year(year: int | None) -> str:
    if year is None:
        return "unknown"
    for era_id, start, end in ERA_BANDS:
        if start <= int(year) <= end:
            return era_id
    return "unknown"


def _title_from_summary(summary: str, links: list[str]) -> str:
    text = _clean_title_candidate(summary)
    link_title = _best_title_link(links)
    if "compound adhesive" in summary.lower():
        text = "Compound adhesive"
    if _title_needs_link_fallback(text):
        if link_title:
            text = _clean_title_candidate(link_title)
    elif _title_has_place_tail(text) and link_title:
        text = _clean_title_candidate(link_title)
    words = text.split()
    if len(words) > 8:
        text = _clean_title_candidate(link_title or " ".join(words[:8]))
    return text or "Unlabeled invention"


def _clean_title_candidate(text: str) -> str:
    out = _collapse_spaces(text)
    out = re.sub(r"\([^)]*(?:see|aka|found|dated|oldest|known)[^)]*\)", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\([^)]*\)", "", out)
    out = re.sub(
        r"^\s*(the\s+)?(earliest|oldest(?:-known| known)?|first|evidence of|direct evidence of)\s+",
        "",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"^\s*(likely|possible|varying estimates for the)\s+", "", out, flags=re.IGNORECASE)
    out = out.replace("–", "-").replace("—", "-")
    for marker in (
        ". ",
        "; ",
        " - ",
        ", by ",
        " by ",
        ", in ",
        " in ",
        ", from ",
        " from ",
        " found ",
        " dated ",
        " according to ",
    ):
        idx = text.lower().find(marker)
        idx = out.lower().find(marker)
        if idx > 2:
            out = out[:idx]
            break
    out = re.sub(r"^(a|an|the)\s+", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+(technology|construction|practice|method)$", r" \1", out, flags=re.IGNORECASE)
    out = _collapse_spaces(out.strip(" .;:-,\"'"))
    replacements = {
        "control of fire by early humans": "Control of fire",
        "history of hide materials": "Hide and leather working",
        "history of clothing and textiles": "Clothing",
        "acheulean": "Acheulean stone tools",
        "origin of language": "Language",
        "representation": "Representational art",
        "representation (arts)": "Representational art",
        "oldest-known mines": "Mining",
        "mines": "Mining",
        "trade": "Long-distance trade",
        "trade and long-distance transportation of resources": "Long-distance trade",
        "rope and cords for": "Rope and cordage",
        "rope": "Rope and cordage",
        "ground stone tools": "Ground stone tools",
        "simple glue": "Simple adhesive",
        "adhesive": "Adhesive",
    }
    key = out.lower()
    if key in replacements:
        out = replacements[key]
    return out[:1].upper() + out[1:] if out else ""


def clean_source_summary(summary: str, title: str) -> str:
    out = _collapse_spaces(summary)
    out = re.sub(r"\((?:The|the) oldest[^)]*\)", "", out)
    out = re.sub(r"\((?:see|See) above\)", "", out)
    out = re.sub(
        r"\s+by\s+(?:Homo|Neanderthal|Neandarthal|Neandarthals|Neanderthals|anatomically modern humans)[^.:;]*",
        "",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\s+by\s+pre-Columbian farmers", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    if out.lower().startswith("the oldest-known mines"):
        out = "Mining and pigment extraction from hematite deposits."
    if out.lower().startswith("earliest evidence of shoes"):
        out = "Footwear inferred from foot-bone morphology, with later preserved sandals and leather shoes."
    if out.lower().startswith("the oldest spear-thrower"):
        out = "Spear-thrower or atlatl technology, with direct and indirect evidence."
    if out.lower().startswith("rope and cords for"):
        out = "Rope and cordage used for hafting tools, baskets, and sewn garments."
    if len(out) > 320:
        cut = out[:320].rsplit(" ", 1)[0]
        out = cut.rstrip(" ,;:.") + "."
    out = out.strip(" ,:;")
    if out and out[-1] not in ".!?":
        out += "."
    if not out:
        out = title
    return out


def _title_needs_link_fallback(title: str) -> bool:
    text = title.lower().strip()
    if not text:
        return True
    if text.startswith(("in ", "at ", "near ", "by ")):
        return True
    if any(token in text for token in ("according to", "found so far", "\"")):
        return True
    return len(text.split()) > 10


def _title_has_place_tail(title: str) -> bool:
    text = title.lower().strip()
    if " in " in text or " from " in text:
        return True
    if ", among " in text or text.endswith(" likely"):
        return True
    return False


def _best_title_link(links: list[str]) -> str:
    skipped = {
        "kenya",
        "ethiopia",
        "zambia",
        "germany",
        "spain",
        "france",
        "china",
        "japan",
        "turkey",
        "iran",
        "iraq",
        "siberia",
        "southwest asia",
        "united states",
        "bbc news",
        "npr",
        "the atlantic",
        "washington post",
        "university of oregon",
    }
    for link in links:
        cleaned = _collapse_spaces(link.split("#", 1)[0])
        key = cleaned.lower()
        if not cleaned or key in skipped:
            continue
        if any(key.endswith(suffix) for suffix in (" cave", " province", " island")):
            continue
        return cleaned
    return ""


def infer_category_domain(title: str, summary: str) -> tuple[str, str]:
    title_text = title.lower()
    for pattern, category, domain in CATEGORY_KEYWORDS:
        if re.search(pattern, title_text):
            return category, domain
    haystack = f"{title} {summary}".lower()
    for pattern, category, domain in CATEGORY_KEYWORDS:
        if re.search(pattern, haystack):
            return category, domain
    return "craft", "toolmaking"


def _slug(text: str, fallback: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:70] or fallback


def analogue_name_from_title(title: str, category: str, domain: str) -> str:
    name = re.sub(r"\([^)]*\)", "", title)
    name = re.sub(r"\boldest known\b|\bearliest\b", "", name, flags=re.IGNORECASE)
    replacements = {
        "pesse canoe": "dugout canoe",
        "gobekli tepe": "stone sanctuary",
        "schoningen": "balanced throwing spear",
        "venuses": "ritual figurines",
        "venus": "ritual figurine",
    }
    lower = name.lower()
    for key, value in replacements.items():
        if key in lower:
            name = value
            break
    name = _collapse_spaces(name.strip(" -:;,."))
    if not name:
        name = f"{domain} practice"
    if category == "military" and "weapon" not in name.lower() and domain == "warfare":
        name = f"{name} weapon"
    return name[:1].upper() + name[1:]


def _spreadability(category: str, domain: str) -> float:
    base = {
        "agriculture": 0.72,
        "domestic": 0.70,
        "communication": 0.66,
        "administration": 0.58,
        "culture": 0.62,
        "craft": 0.55,
        "medicine": 0.52,
        "maritime": 0.50,
        "transport": 0.48,
        "material": 0.45,
        "energy": 0.42,
        "science": 0.38,
        "military": 0.34,
        "computing": 0.30,
    }.get(category, 0.50)
    if domain in {"writing", "navigation", "accounting"}:
        base += 0.05
    return round(max(0.05, min(0.95, base)), 3)


def _complexity(category: str, year: int | None) -> float:
    era_complexity = {
        "paleolithic": 0.18,
        "neolithic": 0.28,
        "bronze_age": 0.38,
        "iron_age": 0.44,
        "classical": 0.50,
        "medieval": 0.56,
        "early_modern": 0.64,
        "industrial": 0.74,
        "modern": 0.82,
        "digital": 0.92,
    }.get(era_for_year(year), 0.50)
    if category in {"military", "computing", "energy", "science"}:
        era_complexity += 0.08
    if category in {"domestic", "culture", "agriculture"}:
        era_complexity -= 0.04
    return round(max(0.05, min(0.98, era_complexity)), 3)


def _starter_prevalence(category: str, spreadability: float, year: int | None) -> float:
    if year is None:
        return 0.0
    if year <= -10_000:
        base = 0.65
    elif year <= -3000:
        base = 0.45
    elif year <= 1000:
        base = 0.32
    else:
        base = 0.18
    if category in {"military", "computing", "science", "energy"}:
        base *= 0.55
    return round(max(0.02, min(0.95, base * (0.65 + spreadability))), 3)


def _parse_bullets(lines: list[str], source_file: str) -> list[tuple[int, str, str, str, str]]:
    rows: list[tuple[int, str, str, str, str]] = []
    section = ""
    subsection = ""
    current: tuple[int, str, str, str, list[str]] | None = None
    current_has_children = False
    heading_re = re.compile(r"^(?P<marks>=+)\s*(?P<title>[^=]+?)\s*=+\s*$")
    bullet_re = re.compile(r"^\*\s*'''(?P<date>[^']+?)[:]?\s*'''\s*:?\s*(?P<body>.*)$")
    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        heading = heading_re.match(line.strip())
        if heading:
            if current is not None:
                start, sec, sub, date_text, body_lines = current
                if not current_has_children:
                    rows.append((start, sec, sub, date_text, " ".join(body_lines)))
                current = None
                current_has_children = False
            title = clean_wiki_markup(heading.group("title"))
            if len(heading.group("marks")) == 2:
                section = title
                subsection = ""
            elif len(heading.group("marks")) == 3:
                subsection = title
            continue
        bullet = bullet_re.match(line)
        if bullet:
            if current is not None:
                start, sec, sub, date_text, body_lines = current
                if not current_has_children:
                    rows.append((start, sec, sub, date_text, " ".join(body_lines)))
            current = (
                lineno,
                section,
                subsection,
                _collapse_spaces(clean_wiki_markup(bullet.group("date"))),
                [bullet.group("body").strip()],
            )
            current_has_children = False
            continue
        if current is not None and line.strip():
            if line.startswith("**"):
                start, sec, sub, date_text, body_lines = current
                parent_context = " ".join(body_lines).strip()
                child_body = line.lstrip("*").strip()
                body = f"{child_body} {parent_context}".strip()
                rows.append((lineno, sec, sub, date_text, body))
                current_has_children = True
            elif line.startswith("*") or line.startswith("="):
                start, sec, sub, date_text, body_lines = current
                if not current_has_children:
                    rows.append((start, sec, sub, date_text, " ".join(body_lines)))
                current = None
                current_has_children = False
            else:
                current[4].append(line.strip())
    if current is not None:
        start, sec, sub, date_text, body_lines = current
        if not current_has_children:
            rows.append((start, sec, sub, date_text, " ".join(body_lines)))
    return rows


def parse_wiki_timeline(path: Path | str) -> list[ParsedSourceRow]:
    source_path = Path(path)
    lines = source_path.read_text(encoding="utf-8").splitlines()
    parsed: list[ParsedSourceRow] = []
    for idx, (lineno, section, subsection, date_text, body) in enumerate(
        _parse_bullets(lines, source_path.name),
        start=1,
    ):
        links = extract_wiki_links(body)
        summary = clean_wiki_markup(body)
        title = _title_from_summary(summary, links)
        summary = clean_source_summary(summary, title)
        year_from, year_to, year, quality, notes = normalize_date_text(date_text)
        parsed.append(
            ParsedSourceRow(
                source_id=f"invsrc_{idx:04d}",
                source_file=source_path.name,
                source_line=lineno,
                section=section,
                subsection=subsection,
                date_text=date_text,
                history_year_from=year_from,
                history_year_to=year_to,
                history_year=year,
                date_quality=quality,
                title=title,
                summary=summary,
                wiki_links=tuple(links),
                parse_notes=notes,
            )
        )
    return parsed


def innovation_rows_from_sources(source_rows: list[ParsedSourceRow]) -> list[dict[str, object]]:
    raw_rows: list[dict[str, object]] = []
    for row in source_rows:
        category, domain = infer_category_domain(row.title, row.summary)
        spreadability = _spreadability(category, domain)
        innovation_id = _slug(row.title, row.source_id)
        raw_rows.append(
            {
                "innovation_id": innovation_id,
                "source_id": row.source_id,
                "source_link": f"{row.source_file}:{row.source_line}",
                "source_title": row.title,
                "analogue_name": analogue_name_from_title(row.title, category, domain),
                "category": category,
                "domain": domain,
                "era_id": era_for_year(row.history_year),
                "history_year": row.history_year,
                "history_year_from": row.history_year_from,
                "history_year_to": row.history_year_to,
                "rank": 0,
                "spreadability": spreadability,
                "complexity": _complexity(category, row.history_year),
                "starter_prevalence": _starter_prevalence(category, spreadability, row.history_year),
                "prerequisite_ids": "",
                "curation_status": "active" if row.history_year is not None else "unreviewed",
                "notes": "auto-generated from wiki timeline; curate analogue and prerequisites",
            }
        )
    seen: dict[str, int] = {}
    for item in raw_rows:
        base = str(item["innovation_id"])
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count > 1:
            item["innovation_id"] = f"{base}_{count}"

    by_category: dict[str, list[dict[str, object]]] = {}
    for item in raw_rows:
        by_category.setdefault(str(item["category"]), []).append(item)
    for items in by_category.values():
        items.sort(
            key=lambda x: (
                int(x["history_year"]) if x["history_year"] not in (None, "") else 9_999_999,
                str(x["innovation_id"]),
            )
        )
        for rank, item in enumerate(items, start=1):
            item["rank"] = rank
    return sorted(
        raw_rows,
        key=lambda x: (
            int(x["history_year"]) if x["history_year"] not in (None, "") else 9_999_999,
            str(x["innovation_id"]),
        ),
    )


def write_csvs(
    source_rows: list[ParsedSourceRow],
    source_out: Path | str,
    innovations_out: Path | str,
) -> None:
    source_path = Path(source_out)
    innovations_path = Path(innovations_out)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    innovations_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SOURCE_FIELDS)
        writer.writeheader()
        for row in source_rows:
            writer.writerow(row.as_csv_row())
    with innovations_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INNOVATION_FIELDS)
        writer.writeheader()
        for row in innovation_rows_from_sources(source_rows):
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse Timeline of historic inventions.wiki into innovation CSVs."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Wiki markup source file.")
    parser.add_argument("--source-out", default=str(DEFAULT_SOURCE_OUT), help="Trace source CSV.")
    parser.add_argument(
        "--innovations-out",
        default=str(DEFAULT_INNOVATIONS_OUT),
        help="Gameplay innovation catalog CSV.",
    )
    args = parser.parse_args(argv)
    source_rows = parse_wiki_timeline(Path(args.input))
    write_csvs(source_rows, Path(args.source_out), Path(args.innovations_out))
    print(
        f"parsed {len(source_rows)} rows -> {args.source_out}; "
        f"wrote {args.innovations_out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
