import csv
import re
import unittest
from pathlib import Path


class GenomeJobsCatalogTests(unittest.TestCase):
    def test_scarce_authority_roles_are_not_normal_jobs(self):
        path = Path(__file__).resolve().parents[1] / "config" / "genome_jobs.csv"
        normal_cols = (
            "prehistoric_jobs",
            "bronze_age_jobs",
            "iron_age_jobs",
            "medieval_jobs",
            "modern_jobs",
        )
        scarce_terms = (
            "mayor",
            "sheriff",
            "judge",
            "magistrate",
            "governor-equivalent leader",
            "guild master",
            "captain",
            "general",
            "officer",
            "diplomat",
            "ambassador",
            "warlord",
            "inquisitor",
            "tyrant leader",
            "governor",
        )
        patterns = {
            term: re.compile(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", re.I)
            for term in scarce_terms
        }
        offenders: list[str] = []
        with path.open(newline="", encoding="utf-8") as f:
            for line_no, row in enumerate(csv.DictReader(f), start=2):
                for col in normal_cols:
                    value = row.get(col, "")
                    hits = [term for term, pattern in patterns.items() if pattern.search(value)]
                    if hits:
                        offenders.append(
                            f"line {line_no} {row.get('trait')} {row.get('deviation_band')} "
                            f"{col}: {value} ({', '.join(hits)})"
                        )
        self.assertEqual([], offenders)

    def test_mayor_and_sheriff_remain_premium_only(self):
        path = Path(__file__).resolve().parents[1] / "config" / "genome_jobs.csv"
        premium_cols = (
            "prehistoric_premium_jobs",
            "bronze_age_premium_jobs",
            "iron_age_premium_jobs",
            "medieval_premium_jobs",
            "modern_premium_jobs",
        )
        premium_text = []
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                premium_text.extend(str(row.get(col, "")) for col in premium_cols)
        joined = "; ".join(premium_text).lower()
        self.assertIn("mayor", joined)
        self.assertIn("sheriff", joined)


if __name__ == "__main__":
    unittest.main()
