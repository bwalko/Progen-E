import json
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path

if "gradio" not in sys.modules and importlib.util.find_spec("gradio") is None:
    sys.modules["gradio"] = types.SimpleNamespace()

import utils.gradio_data_browser as gdb
from library.config_import import load_all_csvs_into_sqlite
from utils.gradio_data_browser import (
    _event_sentence,
    _event_sentence_html,
    _person_from_row,
    _person_event_rows,
    _render_polity_sheet,
    _render_region_sheet,
    _render_town_sheet,
    _sort_rows_by_legacy_score,
    _trait_display_values,
    _trait_phrase,
    load_places_browser,
    render_world_map_selection_detail,
    render_world_map_html,
)


_OPEN_TEST_CONNECTIONS: list[sqlite3.Connection] = []


def _test_connect(database: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(database)
    _OPEN_TEST_CONNECTIONS.append(con)
    return con


def _memory_save() -> sqlite3.Connection:
    con = _test_connect(":memory:")
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


def _memory_place_save() -> sqlite3.Connection:
    con = _memory_save()
    for column_def in (
        "birthplace_region_id text",
        "birthplace_settlement_id text",
        "current_settlement_id text",
        "job text",
        "career_fitness_score real",
    ):
        con.execute(f"alter table simulation_people add column {column_def}")
    con.execute(
        """
        create table simulation_regions (
            region_id text,
            region_display_name text,
            total_population_cap integer,
            total_household_cap integer,
            food_pressure real,
            stability real,
            market_pull real,
            prosperity_pool real,
            treasury_balance real
        )
        """
    )
    con.execute(
        """
        create table simulation_settlements (
            settlement_id text,
            region_id text,
            level text,
            population_cap integer,
            household_cap integer,
            food_pressure real,
            stability real,
            market_pull real,
            display_name text,
            etymology text,
            name_category_primary text,
            name_category_secondary text,
            name_culture_primary text,
            name_culture_secondary text,
            local_geography_json text,
            founded_sim_year integer,
            abandoned_sim_year integer,
            status text,
            consecutive_empty_years integer,
            site_slot integer,
            prosperity_pool real
        )
        """
    )
    con.execute(
        """
        insert into simulation_regions values (
            'r1', 'River Country', 12, 3, 0.25, 0.71, 0.08, 1.5, 7.25
        )
        """
    )
    con.execute(
        """
        insert into simulation_settlements values (
            'r1:s1', 'r1', 'hamlet', 12, 3, 0.25, 0.71, 0.08,
            'Fordham', 'Ford · home', 'Engineering', null, 'Middle English', null,
            ?, 1, null, 'active', 0, 1, 0.8
        )
        """,
        (
            json.dumps(
                {
                    "features": [{"kind": "river", "x": 0.2, "y": 0.3}],
                    "settlements": [{"settlement_slot": 0, "x": 0.55, "y": 0.45}],
                }
            ),
        ),
    )
    con.execute(
        """
        update simulation_people
        set birthplace_region_id = 'r1',
            birthplace_settlement_id = 'r1:s1',
            current_settlement_id = 'r1:s1',
            job = 'miller'
        where person_id = 1
        """
    )
    con.execute(
        """
        update simulation_people
        set birthplace_region_id = 'r1',
            birthplace_settlement_id = 'r1:s1',
            current_settlement_id = 'r1:s1',
            job = 'guard'
        where person_id = 2
        """
    )
    return con


def _memory_legacy_place_save() -> sqlite3.Connection:
    con = _memory_save()
    con.execute(
        """
        create table simulation_regions (
            region_id text,
            region_display_name text,
            total_population_cap integer,
            total_household_cap integer,
            food_pressure real,
            stability real,
            market_pull real,
            prosperity_pool real,
            treasury_balance real
        )
        """
    )
    con.execute(
        """
        create table simulation_settlements (
            settlement_id text,
            region_id text,
            level text,
            population_cap integer,
            household_cap integer,
            food_pressure real,
            stability real,
            market_pull real,
            display_name text,
            etymology text,
            name_category_primary text,
            name_category_secondary text,
            name_culture_primary text,
            name_culture_secondary text,
            local_geography_json text,
            founded_sim_year integer,
            abandoned_sim_year integer,
            status text,
            consecutive_empty_years integer,
            site_slot integer,
            prosperity_pool real
        )
        """
    )
    con.execute(
        """
        insert into simulation_regions values (
            'boreas_peat_river', 'Peat River', 12, 3, 0.25, 0.71, 0.08, 1.5, 7.25
        )
        """
    )
    con.execute(
        """
        insert into simulation_settlements values (
            'boreas_peat_river:s11', 'boreas_peat_river', 'hamlet', 12, 3, 0.25, 0.71, 0.08,
            'Nycholinnis', 'river · hall', 'Water', null, 'Middle English', null,
            ?, 1, null, 'active', 0, 1, 0.8
        )
        """,
        (
            json.dumps(
                {
                    "features": [{"kind": "river", "x": 0.2, "y": 0.3}],
                    "settlements": [{"settlement_slot": 0, "x": 0.55, "y": 0.45}],
                }
            ),
        ),
    )
    con.execute(
        "update simulation_people set person_json = ? where person_id = 1",
        (
            json.dumps(
                {
                    "first_name": "Ada",
                    "last_name": "Forge",
                    "birthyear": 0,
                    "birthplace_region_id": "boreas_peat_river",
                    "birthplace_settlement_id": "boreas_peat_river:s11",
                    "current_settlement_id": "boreas_peat_river:s11",
                    "job": "miller",
                    "career_fitness_score": 0.99,
                }
            ),
        ),
    )
    con.execute(
        "update simulation_people set person_json = ? where person_id = 2",
        (
            json.dumps(
                {
                    "first_name": "Bea",
                    "last_name": "Forge",
                    "birthyear": 8,
                    "birthplace_region_id": "boreas_peat_river",
                    "birthplace_settlement_id": "boreas_peat_river:s11",
                    "current_settlement_id": "boreas_peat_river:s11",
                    "job": "guard",
                    "career_fitness_score": 0.5,
                }
            ),
        ),
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
    def tearDown(self) -> None:
        while _OPEN_TEST_CONNECTIONS:
            con = _OPEN_TEST_CONNECTIONS.pop()
            try:
                con.close()
            except sqlite3.ProgrammingError:
                pass

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
        con = _test_connect(":memory:")
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

    def test_person_from_row_expands_compact_trait_arrays(self) -> None:
        con = _test_connect(":memory:")
        con.row_factory = sqlite3.Row
        row = con.execute(
            "select 1 as person_id, ? as person_json",
            (
                json.dumps(
                    {
                        "g": [0.0, -80.0],
                        "mb": [5.0, -75.0],
                    }
                ),
            ),
        ).fetchone()

        person = _person_from_row(row, ("focus", "courage"))

        self.assertEqual(person["genome"], {"focus": 0.0, "courage": -80.0})
        self.assertEqual(person["mind_body"], {"focus": 5.0, "courage": -75.0})

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

    def test_person_events_use_normalized_event_people_when_available(self) -> None:
        con = _memory_save()
        con.execute(
            """
            create table simulation_event_people (
                event_id integer,
                person_id integer,
                role text
            )
            """
        )
        con.execute(
            """
            insert into simulation_events (id, world, sim_year, event_type, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            (
                7,
                "test",
                12,
                "job_assigned",
                json.dumps({"person_id": 1, "job": "scribe"}),
            ),
        )
        con.execute(
            """
            insert into simulation_event_people (event_id, person_id, role)
            values (?, ?, ?)
            """,
            (7, 1, "subject"),
        )

        rows = _person_event_rows(con, "test", 1)

        self.assertEqual([r["event_type"] for r in rows], ["job_assigned"])

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

    def test_region_sheet_summarizes_settlements_jobs_and_map(self) -> None:
        con = _memory_place_save()

        html = _render_region_sheet(con, "test", "r1")

        self.assertIn("River Country", html)
        self.assertIn("Fordham", html)
        self.assertIn("miller: 1", html)
        self.assertIn("<svg", html)

    def test_places_browser_batches_counts_and_filters_saved_world(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _test_connect(path)
            con.row_factory = sqlite3.Row
            con.execute(
                """
                create table simulation_people (
                    person_id integer,
                    world text,
                    is_alive integer,
                    birthplace_region_id text,
                    current_settlement_id text,
                    job text,
                    person_json text
                )
                """
            )
            con.execute(
                """
                create table simulation_regions (
                    world text,
                    region_id text,
                    region_display_name text,
                    total_population_cap integer,
                    total_household_cap integer,
                    food_pressure real,
                    stability real,
                    market_pull real,
                    prosperity_pool real,
                    treasury_balance real
                )
                """
            )
            con.execute(
                """
                create table simulation_settlements (
                    world text,
                    settlement_id text,
                    region_id text,
                    level text,
                    population_cap integer,
                    household_cap integer,
                    food_pressure real,
                    stability real,
                    market_pull real,
                    display_name text,
                    etymology text,
                    name_category_primary text,
                    name_category_secondary text,
                    name_culture_primary text,
                    name_culture_secondary text,
                    local_geography_json text,
                    founded_sim_year integer,
                    abandoned_sim_year integer,
                    status text,
                    consecutive_empty_years integer,
                    site_slot integer,
                    prosperity_pool real
                )
                """
            )
            con.executemany(
                """
                insert into simulation_regions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("default", "r1", "River Country", 20, 5, 0.2, 0.7, 0.1, 1.0, 3.0),
                    ("other", "r2", "Other Country", 99, 9, 0.8, 0.1, 0.2, 2.0, 5.0),
                ],
            )
            con.executemany(
                """
                insert into simulation_settlements values (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    (
                        "default",
                        "r1:s1",
                        "r1",
                        "hamlet",
                        20,
                        5,
                        0.2,
                        0.7,
                        0.1,
                        "Fordham",
                        "",
                        "",
                        None,
                        "",
                        None,
                        "{}",
                        1,
                        None,
                        "active",
                        0,
                        1,
                        1.0,
                    ),
                    (
                        "other",
                        "r2:s1",
                        "r2",
                        "city",
                        99,
                        9,
                        0.8,
                        0.1,
                        0.2,
                        "Otherham",
                        "",
                        "",
                        None,
                        "",
                        None,
                        "{}",
                        1,
                        None,
                        "active",
                        0,
                        1,
                        1.0,
                    ),
                ],
            )
            con.executemany(
                """
                insert into simulation_people values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "default", 1, "r1", "r1:s1", "miller", "{}"),
                    (2, "default", 1, "r1", "r1:s1", "guard", "{}"),
                    (3, "other", 1, "r2", "r2:s1", "scribe", "{}"),
                    (4, "default", 0, "r1", "r1:s1", "miller", "{}"),
                ],
            )
            con.commit()
            con.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: path
            try:
                region_rows, _, _, _, _ = gdb._places_browser_data("default", "Regions", "", 50)
                town_rows, _, _, _, _ = gdb._places_browser_data("default", "Towns", "", 50)
            finally:
                gdb._db_path = original_db_path

        self.assertEqual([row["Name"] for row in region_rows], ["River Country"])
        self.assertEqual(region_rows[0]["Alive"], 2)
        self.assertEqual(region_rows[0]["Settlements"], 1)
        self.assertIn("guard (1)", region_rows[0]["Top Jobs"])
        self.assertEqual([row["Name"] for row in town_rows], ["Fordham"])
        self.assertEqual(town_rows[0]["Alive"], 2)
        self.assertIn("miller (1)", town_rows[0]["Top Jobs"])

    def test_settlements_browser_loads_rows_and_opens_sheet(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                table, status, settlement_ids = gdb.load_settlements_browser("test", "", "Active", 50)
                sheet = gdb.select_settlement_from_table(settlement_ids, "test", types.SimpleNamespace(index=0))
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        self.assertEqual(table["headers"], gdb.SETTLEMENT_BROWSER_HEADERS)
        self.assertEqual(table["value"][0][0], "Fordham")
        self.assertEqual(settlement_ids, ["r1:s1"])
        self.assertIn("showing 1 of 1 settlements", status)
        self.assertIn("Fordham", sheet)
        self.assertIn("miller", sheet)

    def test_regions_browser_loads_rows_and_opens_sheet(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                table, status, region_ids = gdb.load_regions_browser_fresh("test", "", 50)
                sheet = gdb.select_region_from_fresh_table(region_ids, "test", types.SimpleNamespace(index=0))
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        self.assertEqual(table["headers"], gdb.REGION_BROWSER_HEADERS)
        self.assertEqual(table["value"][0][0], "River Country")
        self.assertEqual(region_ids, ["r1"])
        self.assertIn("showing 1 of 1 regions", status)
        self.assertIn("River Country", sheet)
        self.assertIn("Fordham", sheet)

    def test_polities_browser_loads_rows_and_opens_sheet(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.execute(
                """
                create table simulation_polities (
                    polity_id integer,
                    name text,
                    polity_type_id text,
                    status text,
                    parent_polity_id integer,
                    capital_settlement_id text,
                    founded_sim_year integer
                )
                """
            )
            con.execute(
                """
                create table simulation_polity_territory (
                    polity_id integer,
                    target_kind text,
                    target_id text,
                    since_sim_year integer,
                    until_sim_year integer
                )
                """
            )
            con.execute(
                """
                create table simulation_office_seats (
                    polity_id integer,
                    seat_id text,
                    title_id text,
                    scope_settlement_id text,
                    holder_person_id integer,
                    term_expires_sim_year integer,
                    status text,
                    slot_index integer
                )
                """
            )
            con.execute("insert into simulation_polities values (1, 'River Crown', 'duchy', 'active', null, 'r1:s1', 1)")
            con.execute("insert into simulation_polity_territory values (1, 'region', 'r1', 1, null)")
            con.execute("insert into simulation_office_seats values (1, 'seat1', 'duke', 'r1:s1', 1, null, 'active', 0)")
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                table, status, polity_ids = gdb.load_polities_browser_fresh("test", "", "Active", 50)
                sheet = gdb.select_polity_from_fresh_table(polity_ids, "test", types.SimpleNamespace(index=0))
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        self.assertEqual(table["headers"], gdb.POLITY_BROWSER_HEADERS)
        self.assertEqual(table["value"][0][0], "River Crown")
        self.assertEqual(polity_ids, [1])
        self.assertIn("showing 1 of 1 polities", status)
        self.assertIn("River Crown", sheet)
        self.assertIn("duke", sheet)

    def test_world_map_html_renders_generated_svg(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            world_dir = root / "default"
            world_dir.mkdir()
            load_all_csvs_into_sqlite(world_dir / "config.sqlite")
            old_worlds_dir = gdb.WORLDS_DIR
            gdb.WORLDS_DIR = root
            try:
                shown = render_world_map_html("default", include_overlays=False)
            finally:
                gdb.WORLDS_DIR = old_worlds_dir

        self.assertIn("Generated polygon geography", shown)
        self.assertIn("<svg", shown)
        self.assertIn('class="cell terrain-', shown)
        self.assertIn('onclick="', shown)
        self.assertIn("map-open-selection", shown)

    def test_world_map_selection_opens_existing_region_or_town_sheet(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: path
            try:
                region_html = render_world_map_selection_detail("test", json.dumps({"view": "Regions", "id": "r1"}))
                town_html = render_world_map_selection_detail("test", json.dumps({"view": "Towns", "id": "r1:s1"}))
            finally:
                gdb._db_path = original_db_path

        self.assertIn("River Country", region_html)
        self.assertIn("Fordham", town_html)

    def test_place_row_selection_uses_loaded_key_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: path
            try:
                _, _, state, _ = gdb.load_regions_browser_with_detail_reset("test", "", 50)
                html = gdb.select_region_from_table(state, "test", types.SimpleNamespace(index=0))
            finally:
                gdb._db_path = original_db_path

        self.assertEqual(state, [gdb._encode_place_key("test", "test", "r1")])
        self.assertIn("River Country", html)
        self.assertIn("Fordham", html)

    def test_town_sheet_summarizes_residents_jobs_and_name_origin(self) -> None:
        con = _memory_place_save()

        html = _render_town_sheet(con, "test", "r1:s1")

        self.assertIn("Fordham", html)
        self.assertIn("Ford · home", html)
        self.assertIn("miller: 1", html)
        self.assertIn("Ada Forge", html)

    def test_town_sheet_supports_legacy_json_person_rows(self) -> None:
        con = _memory_legacy_place_save()

        html = _render_town_sheet(con, "test", "boreas_peat_river:s11")

        self.assertIn("Nycholinnis", html)
        self.assertIn("miller: 1", html)
        self.assertIn("Ada Forge", html)

    def test_missing_town_detail_explains_stale_current_save_row(self) -> None:
        con = _memory_place_save()

        html = _render_town_sheet(con, "test", "boreas_clear_river:s26")

        self.assertIn("is no longer in the current save", html)
        self.assertNotIn("No town named", html)

    def test_encoded_place_key_uses_row_source_world(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            wrong_path = root / "wrong.sqlite"
            right_path = root / "right.sqlite"

            wrong = _memory_place_save()
            right = _memory_place_save()
            right.execute(
                """
                update simulation_regions
                set region_id = 'boreas_fjord_shore',
                    region_display_name = 'Boreas Fjord Shore'
                where region_id = 'r1'
                """
            )
            right.execute(
                "update simulation_settlements set region_id = 'boreas_fjord_shore' where region_id = 'r1'"
            )
            wrong.commit()
            right.commit()
            with closing(sqlite3.connect(wrong_path)) as out:
                wrong.backup(out)
            with closing(sqlite3.connect(right_path)) as out:
                right.backup(out)
            wrong.close()
            right.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: right_path if world == "right_world" else wrong_path
            try:
                key = gdb._encode_place_key("right_world", "test", "boreas_fjord_shore")
                html = gdb.render_place_detail("wrong_world", "Regions", key)
            finally:
                gdb._db_path = original_db_path

        self.assertIn("Boreas Fjord Shore", html)
        self.assertNotIn("No region named", html)

    def test_polities_browser_handles_empty_result_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _test_connect(path)
            con.execute(
                """
                create table simulation_polities (
                    polity_id integer primary key,
                    polity_type_id text,
                    parent_polity_id integer,
                    name text,
                    capital_settlement_id text,
                    founded_sim_year integer,
                    status text
                )
                """
            )
            con.commit()
            con.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: path
            try:
                _, status, keys = load_places_browser("test", "Polities", "", 50)
            finally:
                gdb._db_path = original_db_path

        self.assertEqual(keys, [])
        self.assertIn("showing 0 polities", status)

    def test_empty_polity_detail_explains_current_save_has_none(self) -> None:
        con = _test_connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            """
            create table simulation_polities (
                polity_id integer primary key,
                polity_type_id text,
                parent_polity_id integer,
                name text,
                capital_settlement_id text,
                founded_sim_year integer,
                status text
            )
            """
        )

        html = _render_polity_sheet(con, "test", "1")

        self.assertIn("No polities are recorded", html)
        self.assertNotIn("No polity #1", html)


if __name__ == "__main__":
    unittest.main()
