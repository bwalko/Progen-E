import json
import importlib.util
import sqlite3
import sys
import types
import unittest

if "gradio" not in sys.modules and importlib.util.find_spec("gradio") is None:
    sys.modules["gradio"] = types.SimpleNamespace()

from utils.gradio_data_browser import (
    _event_sentence,
    _event_sentence_html,
    _person_event_rows,
    _sort_rows_by_legacy_score,
    _trait_display_values,
    _trait_phrase,
)


def _memory_save() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        create table simulation_people (
            person_id integer,
            world text,
            is_founder integer,
            father_id integer,
            mother_id integer,
            is_alive integer,
            person_json text
        )
        """
    )
    con.execute(
        """
        insert into simulation_people (
            person_id, world, is_founder, father_id, mother_id, is_alive, person_json
        )
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "test",
            1,
            None,
            None,
            1,
            json.dumps(
                {
                    "first_name": "Ada",
                    "last_name": "Forge",
                    "birthyear": 0,
                    "career_fitness_score": 0.99,
                }
            ),
        ),
    )
    con.execute(
        """
        insert into simulation_people (
            person_id, world, is_founder, father_id, mother_id, is_alive, person_json
        )
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            2,
            "test",
            0,
            1,
            None,
            1,
            json.dumps(
                {
                    "first_name": "Bea",
                    "last_name": "Forge",
                    "birthyear": 8,
                }
            ),
        ),
    )
    con.execute(
        """
        create table simulation_events (
            id integer primary key,
            world text,
            sim_year integer,
            event_type text,
            payload_json text
        )
        """
    )
    return con


def _event_row(con: sqlite3.Connection, event_type: str, payload: dict[str, object]) -> sqlite3.Row:
    return con.execute(
        "select ? as sim_year, ? as event_type, ? as payload_json",
        (10, event_type, json.dumps(payload)),
    ).fetchone()


def _genome_row(con: sqlite3.Connection) -> sqlite3.Row:
    return con.execute(
        """
        select
            'generosity' as trait,
            'envious' as "deficient deviation",
            'generous' as "optimal centerpoint",
            'extravagant' as "excess deviation",
            'selfish' as "deficient description",
            'generous' as "optimal description",
            'extravagant' as "excess description"
        """
    ).fetchone()


def _person_sort_row(con: sqlite3.Connection, person_id: int, traits: dict[str, float]) -> sqlite3.Row:
    return con.execute(
        "select ? as person_id, ? as person_json",
        (
            person_id,
            json.dumps(
                {
                    "first_name": f"P{person_id}",
                    "last_name": "Sort",
                    "mind_body": traits,
                }
            ),
        ),
    ).fetchone()


class GradioDataBrowserEventTests(unittest.TestCase):
    def test_job_event_fitness_uses_event_payload_not_current_person_score(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "job_assigned",
            {"person_id": 1, "job": "smith", "fitness_score": 0.1234},
        )

        text = _event_sentence(con, "test", event, 1)
        html = _event_sentence_html(con, "test", event, 1)

        self.assertIn("fitness 0.12", text)
        self.assertIn("fitness 0.12", html)
        self.assertNotIn("0.99", text)
        self.assertNotIn("0.99", html)

    def test_person_sheet_event_uses_owner_first_name_only(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "job_assigned",
            {"person_id": 1, "job": "smith", "fitness_score": 0.1234},
        )

        text = _event_sentence(con, "test", event, 1)
        html = _event_sentence_html(con, "test", event, 1)

        self.assertIn("Ada became smith", text)
        self.assertNotIn("Ada Forge became smith", text)
        self.assertIn("<strong>Ada</strong> became smith", html)
        self.assertNotIn(">Ada</a> became smith", html)
        self.assertNotIn(">Ada Forge</a> became smith", html)

    def test_person_sheet_event_keeps_other_people_full_names(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "couple_formed",
            {"person_a_id": 1, "person_b_id": 2},
        )

        text = _event_sentence(con, "test", event, 1)
        html = _event_sentence_html(con, "test", event, 1)

        self.assertIn("Ada formed a household partnership with Bea Forge", text)
        self.assertIn("<strong>Ada</strong> formed a household partnership with", html)
        self.assertIn(">Bea Forge</a>", html)

    def test_career_fitness_update_uses_event_payload(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "career_fitness_updated",
            {"person_id": 1, "fitness_score": 0.4567},
        )

        self.assertIn("score 0.46", _event_sentence(con, "test", event, 1))
        self.assertIn("score 0.46", _event_sentence_html(con, "test", event, 1))

    def test_job_market_churn_event_uses_recorded_fit_nuance(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "job_lost",
            {
                "person_id": 1,
                "old_job": "scribe",
                "reason": "job_market_churn",
                "fitness_score": 0.42,
                "career_fitness_score": 0.74,
                "job_trait_match_score": 0.35,
                "trait": "focus",
                "deviation_band": "optimal",
                "resource_pressure": 0.3,
            },
        )

        text = _event_sentence(con, "test", event, 1)
        html = _event_sentence_html(con, "test", event, 1)

        self.assertIn("poor fit despite decent general ability", text)
        self.assertIn("job keyed to focus / optimal", text)
        self.assertIn("job fit 0.42", html)

    def test_people_browser_can_sort_by_legacy_score_columns(self) -> None:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        low_beauty = _person_sort_row(
            con,
            1,
            {"symmetry": 100, "wit": 100, "persuasion": 100, "mating drive": -100},
        )
        high_beauty = _person_sort_row(
            con,
            2,
            {"symmetry": 0, "wit": 0, "persuasion": 0, "mating drive": 100},
        )

        rows = _sort_rows_by_legacy_score(
            [low_beauty, high_beauty],
            sort_by="Beauty",
            sort_dir="Descending",
        )

        self.assertEqual([int(row["person_id"]) for row in rows], [2, 1])

    def test_couple_formed_shows_rare_kinship_exception(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "couple_formed",
            {
                "person_a_id": 1,
                "person_b_id": 2,
                "kinship_exception": "parent_child",
                "kinship_exception_probability": 0.000001,
            },
        )

        text = _event_sentence(con, "test", event, 1)

        self.assertIn("Rare kinship exception: parent child", text)
        self.assertIn("0.000001", text)

    def test_person_events_match_household_array_payloads(self) -> None:
        con = _memory_save()
        con.execute(
            """
            insert into simulation_events (world, sim_year, event_type, payload_json)
            values (?, ?, ?, ?)
            """,
            (
                "test",
                10,
                "household_childcare_shortfall",
                json.dumps(
                    {
                        "household_member_ids": [1],
                        "dependent_minor_ids": [2],
                        "need": 1,
                        "supply": 0.25,
                        "shortfall": 0.75,
                        "outcome": "run_away",
                        "victim_person_id": 2,
                    }
                ),
            ),
        )

        parent_events = _person_event_rows(con, "test", 1)
        child_events = _person_event_rows(con, "test", 2)

        self.assertEqual([r["event_type"] for r in parent_events], ["household_childcare_shortfall"])
        self.assertEqual([r["event_type"] for r in child_events], ["household_childcare_shortfall"])
        self.assertIn("childcare shortfall", _event_sentence(con, "test", child_events[0], 2))

    def test_person_events_match_household_prosperity_payloads(self) -> None:
        con = _memory_save()
        con.execute(
            """
            insert into simulation_events (world, sim_year, event_type, payload_json)
            values (?, ?, ?, ?)
            """,
            (
                "test",
                11,
                "household_prosperity_crisis",
                json.dumps(
                    {
                        "household_member_ids": [1, 2],
                        "purseholder_person_id": 1,
                        "prosperity_before": 0.21,
                        "prosperity_after": 0.18,
                    }
                ),
            ),
        )

        rows = _person_event_rows(con, "test", 2)

        self.assertEqual([r["event_type"] for r in rows], ["household_prosperity_crisis"])
        self.assertIn("prosperity crisis", _event_sentence(con, "test", rows[0], 2))

    def test_closest_to_ideal_can_prefer_optimal_trait_phrase(self) -> None:
        con = _memory_save()
        labels = {"generosity": _genome_row(con)}

        self.assertEqual(
            _trait_phrase("generosity", -24.4, labels, prefer_optimal=True),
            "generous",
        )
        self.assertEqual(_trait_phrase("generosity", -24.4, labels), "selfish")

    def test_trait_phrase_can_soften_typical_deviations_for_ui_titles(self) -> None:
        con = _memory_save()
        labels = {"generosity": _genome_row(con)}

        self.assertEqual(
            _trait_phrase("generosity", -52.8, labels, soften_typical=True),
            "slightly selfish",
        )
        self.assertEqual(
            _trait_phrase("generosity", 63.0, labels, soften_typical=True),
            "extravagant",
        )

    def test_trait_display_values_show_current_and_base_when_atrophied(self) -> None:
        current, base, shown = _trait_display_values(
            "physical",
            {"physical": 72.25},
            {"physical": 18.0},
        )

        self.assertEqual(current, 72.25)
        self.assertEqual(base, 18.0)
        self.assertIn("+72.2", shown)
        self.assertIn("base +18.0", shown)

    def test_trait_display_values_hide_base_when_current_matches(self) -> None:
        _, _, shown = _trait_display_values(
            "focus",
            {"focus": -4.0},
            {"focus": -4.0},
        )

        self.assertEqual(shown, "-4.0")


if __name__ == "__main__":
    unittest.main()
