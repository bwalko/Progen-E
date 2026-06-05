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
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
    rediscover_event_record,
    upsert_public_event_record,
)
from library.world_map_geometry import MicroRegionCell, RegionCell, WorldMapGeometry
from library.world_map_svg import load_world_map_overlays
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
                    "features": [
                        {
                            "feature_id": "r1:f0",
                            "kind": "river",
                            "x": 0.2,
                            "y": 0.3,
                            "display_name": "Bluewater",
                            "etymology": "blue · river",
                        }
                    ],
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


def _memory_keyed_place_save() -> sqlite3.Connection:
    con = _memory_save()
    for column_def in (
        "birthplace_region_key integer",
        "birthplace_settlement_key integer",
        "current_settlement_key integer",
        "job text",
        "career_fitness_score real",
    ):
        con.execute(f"alter table simulation_people add column {column_def}")
    con.execute(
        """
        create table simulation_region_lookup (
            region_key integer primary key,
            region_id text not null unique
        )
        """
    )
    con.execute(
        """
        create table simulation_settlement_lookup (
            settlement_key integer primary key,
            settlement_id text not null unique,
            region_key integer not null
        )
        """
    )
    con.execute(
        """
        create table simulation_regions (
            region_key integer primary key,
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
            settlement_key integer primary key,
            region_key integer,
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
        create view simulation_regions_readable as
        select rl.region_id,
               r.region_display_name,
               r.total_population_cap,
               r.total_household_cap,
               r.food_pressure,
               r.stability,
               r.market_pull,
               r.prosperity_pool,
               r.treasury_balance
        from simulation_regions r
        join simulation_region_lookup rl on rl.region_key = r.region_key
        """
    )
    con.execute(
        """
        create view simulation_settlements_readable as
        select sl.settlement_id,
               rl.region_id,
               s.level,
               s.population_cap,
               s.household_cap,
               s.food_pressure,
               s.stability,
               s.market_pull,
               s.display_name,
               s.etymology,
               s.name_category_primary,
               s.name_category_secondary,
               s.name_culture_primary,
               s.name_culture_secondary,
               s.local_geography_json,
               s.founded_sim_year,
               s.abandoned_sim_year,
               s.status,
               s.consecutive_empty_years,
               s.site_slot,
               s.prosperity_pool
        from simulation_settlements s
        join simulation_settlement_lookup sl on sl.settlement_key = s.settlement_key
        join simulation_region_lookup rl on rl.region_key = s.region_key
        """
    )
    con.execute(
        """
        create table simulation_people_light (
            person_id integer primary key,
            name text,
            birthyear integer,
            deathyear integer,
            is_alive integer,
            gender text,
            birthplace_region_key integer,
            birthplace_settlement_key integer,
            current_settlement_key integer,
            job_family text,
            partner_person_id integer,
            father_id integer,
            mother_id integer,
            child_count integer,
            status_bucket text,
            prosperity_bucket text
        )
        """
    )
    con.execute(
        """
        create table simulation_cohorts (
            cohort_id integer primary key autoincrement,
            sim_year integer,
            region_key integer,
            settlement_key integer,
            age_band text,
            gender text,
            species text,
            culture text,
            job_family text,
            status_bucket text,
            population_count integer,
            birth_count integer,
            death_count integer
        )
        """
    )
    con.execute(
        """
        create view simulation_people_light_readable as
        select p.person_id,
               p.name,
               p.birthyear,
               p.deathyear,
               p.is_alive,
               p.gender,
               br.region_id as birthplace_region_id,
               bs.settlement_id as birthplace_settlement_id,
               cs.settlement_id as current_settlement_id,
               p.job_family,
               p.partner_person_id,
               p.father_id,
               p.mother_id,
               p.child_count,
               p.status_bucket,
               p.prosperity_bucket
        from simulation_people_light p
        left join simulation_region_lookup br on br.region_key = p.birthplace_region_key
        left join simulation_settlement_lookup bs on bs.settlement_key = p.birthplace_settlement_key
        left join simulation_settlement_lookup cs on cs.settlement_key = p.current_settlement_key
        """
    )
    con.execute(
        """
        create view simulation_cohorts_readable as
        select c.cohort_id,
               c.sim_year,
               rl.region_id,
               sl.settlement_id,
               c.age_band,
               c.gender,
               c.species,
               c.culture,
               c.job_family,
               c.status_bucket,
               c.population_count,
               c.birth_count,
               c.death_count
        from simulation_cohorts c
        left join simulation_region_lookup rl on rl.region_key = c.region_key
        left join simulation_settlement_lookup sl on sl.settlement_key = c.settlement_key
        """
    )
    con.execute("insert into simulation_region_lookup values (1, 'r1')")
    con.execute("insert into simulation_settlement_lookup values (1, 'r1:s1', 1)")
    con.execute(
        """
        insert into simulation_regions values (
            1, 'River Country', 12, 3, 0.25, 0.71, 0.08, 1.5, 7.25
        )
        """
    )
    con.execute(
        """
        insert into simulation_settlements values (
            1, 1, 'hamlet', 12, 3, 0.25, 0.71, 0.08,
            'Fordham', 'Ford - home', 'Engineering', null, 'Middle English', null,
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
        set birthplace_region_key = 1,
            birthplace_settlement_key = 1,
            current_settlement_key = 1,
            job = 'miller'
        where person_id = 1
        """
    )
    con.execute(
        """
        update simulation_people
        set birthplace_region_key = 1,
            birthplace_settlement_key = 1,
            current_settlement_key = 1,
            job = 'guard'
        where person_id = 2
        """
    )
    con.execute(
        """
        insert into simulation_people_light values (
            100, 'Cora Light', -10, null, 1, 'female', 1, 1, 1,
            'craft', null, null, null, 0, 'commoner', 'stable'
        )
        """
    )
    con.execute(
        """
        insert into simulation_cohorts (
            sim_year, region_key, settlement_key, age_band, gender, species,
            culture, job_family, status_bucket, population_count, birth_count, death_count
        )
        values (100, 1, 1, '20-39', 'mixed', 'human', 'test', 'labor', 'commoner', 10, 0, 0)
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


def _event_row(
    con: sqlite3.Connection,
    event_type: str,
    payload: dict[str, object],
    *,
    year: int = 10,
) -> sqlite3.Row:
    return con.execute(
        "select ? as sim_year, ? as event_type, ? as payload_json",
        (year, event_type, json.dumps(payload)),
    ).fetchone()


def _attach_empty_genome_config(con: sqlite3.Connection) -> None:
    con.execute("attach database ':memory:' as cfg")
    con.execute(
        """
        create table cfg.genome (
            trait text,
            "deficient deviation" text,
            "optimal centerpoint" text,
            "excess deviation" text,
            "deficient description" text,
            "optimal description" text,
            "excess description" text
        )
        """
    )


def _insert_compact_person(
    con: sqlite3.Connection,
    person_id: int,
    first_name: str,
    last_name: str,
) -> None:
    con.execute(
        """
        insert into simulation_people (
            person_id, is_founder, is_alive, first_name, last_name,
            gender, ethnic, species, birthyear, person_json
        )
        values (?, 1, 1, ?, ?, 'female', 'human', 'human', 970, '{}')
        """,
        (person_id, first_name, last_name),
    )


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

    def _history_table_rows(self, table: dict[str, object]) -> list[dict[str, object]]:
        headers = list(table["headers"])  # type: ignore[index]
        return [dict(zip(headers, row)) for row in table["value"]]  # type: ignore[index]

    def test_history_browser_loads_public_rumor_and_lost_views(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            with closing(sqlite3.connect(path)) as con:
                con.row_factory = sqlite3.Row
                ensure_checkpoint_schema(con)
                _insert_compact_person(con, 1, "Tara", "Stone")
                _insert_compact_person(con, 2, "Pell", "Ash")
                _insert_compact_person(con, 3, "Ira", "Marsh")
                _insert_compact_person(con, 4, "Lio", "Dawn")
                birth_id, crime_id, _ = append_simulation_event_rows(
                    con,
                    "default",
                    [
                        (
                            1001,
                            "birth",
                            {
                                "person_id": 4,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "incident_kind": "storehouse_robbery",
                                "motive": "scarcity",
                                "loss_value": 0.18,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1003,
                            "public_virtue",
                            {
                                "benefactor_person_id": 3,
                                "beneficiary_person_id": 4,
                                "incident_kind": "heroic_rescue",
                                "motive": "mercy",
                                "relief_value": 0.12,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(con, birth_id, lost_year=1040)
                upsert_public_event_record(
                    con,
                    crime_id,
                    public_stage="misattributed",
                    record_key="false_accusation",
                    confidence=0.3,
                    public_actor_person_id=3,
                    public_victim_person_id=2,
                )
                con.commit()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                public_table, public_status = gdb.load_history_browser(
                    "default", "Public Chronicle", "", "", 50, 0
                )
                rumor_table, _ = gdb.load_history_browser(
                    "default", "Rumors", "", "", 50, 0
                )
                lost_table, lost_status = gdb.load_history_browser(
                    "default", "Lost History", "", "", 50, 0
                )
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        public_rows = self._history_table_rows(public_table)
        rumor_rows = self._history_table_rows(rumor_table)
        lost_rows = self._history_table_rows(lost_table)
        self.assertEqual(public_table["headers"], gdb.HISTORY_BROWSER_HEADERS)
        self.assertEqual(
            [row["Event Type"] for row in public_rows],
            ["property_crime", "property_crime", "public_virtue"],
        )
        self.assertIn("Public Chronicle", public_status)
        self.assertEqual(
            [row["Event Type"] for row in rumor_rows],
            ["property_crime", "property_crime"],
        )
        self.assertEqual(
            [row["Visibility"] for row in rumor_rows],
            ["rumored", "misattributed"],
        )
        self.assertEqual(
            [row["Public Stage"] for row in rumor_rows],
            ["rumored", "rumored"],
        )
        self.assertIn("Market talk", rumor_rows[0]["Prose"])
        self.assertIn("Ira Marsh", rumor_rows[1]["Prose"])
        self.assertEqual([row["Event Type"] for row in lost_rows], ["birth"])
        self.assertEqual(lost_rows[0]["Visibility"], "lost")
        self.assertEqual(lost_rows[0]["Public Stage"], "not_public")
        self.assertIn("No living chronicle preserved", lost_rows[0]["Prose"])
        self.assertIn("Lost History", lost_status)

    def test_history_browser_separates_public_unknown_rumored_known_and_admin_truth(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            with closing(sqlite3.connect(path)) as con:
                con.row_factory = sqlite3.Row
                ensure_checkpoint_schema(con)
                _insert_compact_person(con, 1, "Fred", "Vale")
                _insert_compact_person(con, 2, "Lio", "Reed")
                murder_id = append_simulation_event_rows(
                    con,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "murder",
                                "motive": "inheritance_plot",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]
                upsert_public_event_record(
                    con,
                    murder_id,
                    public_stage="unknown",
                    record_key="default",
                    record_type="missing_person_notice",
                    public_victim_person_id=2,
                    distortion={
                        "public_unknown_summary": "{place}: Lio Reed went missing in {year}."
                    },
                )
                upsert_public_event_record(
                    con,
                    murder_id,
                    public_stage="rumored",
                    record_key="monster_rumor",
                    public_victim_person_id=2,
                    distortion={"rumored_cause": "taken by a monster"},
                )
                upsert_public_event_record(
                    con,
                    murder_id,
                    public_stage="known",
                    record_key="court_truth",
                    record_type="violent_crime_record",
                    public_actor_person_id=1,
                    public_victim_person_id=2,
                )
                con.commit()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                unknown_table, unknown_status = gdb.load_history_browser(
                    "default", "Public Unknown", "murder", "", 50, 0
                )
                rumor_table, rumor_status = gdb.load_history_browser(
                    "default", "Public Rumors", "murder", "", 50, 0
                )
                known_table, known_status = gdb.load_history_browser(
                    "default", "Public Known", "murder", "", 50, 0
                )
                admin_table, admin_status = gdb.load_history_browser(
                    "default", "Admin Truth", "murder", "", 50, 0
                )
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        unknown_rows = self._history_table_rows(unknown_table)
        rumor_rows = self._history_table_rows(rumor_table)
        known_rows = self._history_table_rows(known_table)
        admin_rows = self._history_table_rows(admin_table)
        self.assertEqual(unknown_rows[0]["Public Stage"], "unknown")
        self.assertIn("went missing", unknown_rows[0]["Prose"])
        self.assertIn("Public Unknown", unknown_status)
        self.assertEqual(rumor_rows[0]["Public Stage"], "rumored")
        self.assertIn("taken by a monster", rumor_rows[0]["Prose"])
        self.assertIn("Public Rumors", rumor_status)
        self.assertEqual(known_rows[0]["Public Stage"], "known")
        self.assertIn("Fred Vale as the killer of Lio Reed", known_rows[0]["Prose"])
        self.assertIn("Public Known", known_status)
        self.assertEqual(admin_rows[0]["Visibility"], "admin_truth")
        self.assertIn("Fred Vale killed Lio Reed", admin_rows[0]["Admin Summary"])
        self.assertIn("Admin Truth", admin_status)

    def test_history_browser_loads_admin_truth_search_and_rediscoveries(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            with closing(sqlite3.connect(path)) as con:
                con.row_factory = sqlite3.Row
                ensure_checkpoint_schema(con)
                _insert_compact_person(con, 10, "Lio", "Reed")
                _insert_compact_person(con, 41, "Sera", "Archivist")
                original_event_id = append_simulation_event_rows(
                    con,
                    "default",
                    [
                        (
                            990,
                            "birth",
                            {
                                "person_id": 10,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]
                mark_event_record_lost(con, original_event_id, lost_year=1040)
                rediscover_event_record(
                    con,
                    original_event_id,
                    rediscovered_year=1100,
                    source_person_id=41,
                    source_institution_id="temple_ledger",
                    preserving_settlement_id="aeria_north:settlement:1",
                    confidence=0.82,
                )
                con.commit()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                admin_table, admin_status = gdb.load_history_browser(
                    "default", "Admin Truth", "event_rediscovered", "", 50, 0
                )
                rediscovery_table, rediscovery_status = gdb.load_history_browser(
                    "default", "Rediscoveries", "", "", 50, 0
                )
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        admin_rows = self._history_table_rows(admin_table)
        rediscovery_rows = self._history_table_rows(rediscovery_table)
        self.assertEqual([row["Event Type"] for row in admin_rows], ["event_rediscovered"])
        self.assertEqual(admin_rows[0]["Visibility"], "admin_truth")
        self.assertIn(str(original_event_id), admin_rows[0]["Prose"])
        self.assertIn("Admin Truth", admin_status)
        self.assertEqual(
            [row["Event Type"] for row in rediscovery_rows],
            ["birth", "event_rediscovered"],
        )
        self.assertEqual(rediscovery_rows[0]["Visibility"], "rediscovered")
        self.assertIn("later hand recovered", rediscovery_rows[0]["Prose"])
        self.assertIn("Lio Reed", rediscovery_rows[0]["Prose"])
        self.assertIn(str(original_event_id), rediscovery_rows[1]["Prose"])
        self.assertIn("Rediscoveries", rediscovery_status)

    def test_history_summary_exposes_report_counts_and_lifecycle_visibility(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            with closing(sqlite3.connect(path)) as con:
                con.row_factory = sqlite3.Row
                ensure_checkpoint_schema(con)
                _insert_compact_person(con, 1, "Tara", "Stone")
                _insert_compact_person(con, 2, "Pell", "Ash")
                _insert_compact_person(con, 3, "Ira", "Marsh")
                _insert_compact_person(con, 4, "Lio", "Dawn")
                birth_id, _crime_id, _virtue_id = append_simulation_event_rows(
                    con,
                    "default",
                    [
                        (
                            1000,
                            "birth",
                            {
                                "person_id": 4,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1001,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "historical_importance": 0.33,
                                "loss_value": 0.12,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "public_virtue",
                            {
                                "benefactor_person_id": 3,
                                "beneficiary_person_id": 4,
                                "historical_importance": 0.44,
                                "relief_value": 0.2,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(con, birth_id, lost_year=1040)
                rediscover_event_record(
                    con,
                    birth_id,
                    rediscovered_year=1100,
                    source_institution_id="temple_ledger",
                    preserving_settlement_id="aeria_north:settlement:1",
                    confidence=0.82,
                )
                con.commit()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                summary_table, summary_status = gdb.load_history_summary("default")
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        rows = self._history_table_rows(summary_table)
        by_section_key = {(row["Section"], row["Key"]): row for row in rows}
        self.assertEqual(summary_table["headers"], gdb.HISTORY_SUMMARY_HEADERS)
        self.assertEqual(by_section_key[("Overview", "total_events")]["Count"], 4)
        self.assertEqual(by_section_key[("Tracked Incidents", "murder")]["Count"], 0)
        self.assertEqual(
            by_section_key[("Tracked Incidents", "property_crime")]["Count"], 1
        )
        self.assertEqual(
            by_section_key[
                ("Visibility", "birth / lineage_memory / rediscovered")
            ]["Count"],
            1,
        )
        self.assertEqual(
            by_section_key[
                (
                    "Visibility",
                    "event_rediscovered / rediscovery_record / public_known",
                )
            ]["Count"],
            1,
        )
        self.assertIn(
            "avg=0.3300",
            by_section_key[("Metrics", "property_crime historical_importance")][
                "Value"
            ],
        )
        self.assertIn("history summary rows", summary_status)

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

    def test_person_sheet_has_separate_history_sections(self) -> None:
        con = _memory_save()
        _attach_empty_genome_config(con)
        con.execute("create table world_state (id integer primary key, current_year integer)")
        con.execute("insert into world_state values (1, 120)")
        con.execute(
            """
            insert into simulation_people (
                person_id, world, is_founder, father_id, mother_id, is_alive, person_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                "test",
                1,
                None,
                None,
                1,
                json.dumps({"first_name": "Cato", "last_name": "Vale", "birthyear": 5}),
            ),
        )
        con.executemany(
            """
            insert into simulation_events (world, sim_year, event_type, payload_json)
            values (?, ?, ?, ?)
            """,
            [
                ("test", 100, "job_assigned", json.dumps({"person_id": 1, "job": "smith"})),
                ("test", 101, "couple_formed", json.dumps({"person_a_id": 1, "person_b_id": 2})),
                ("test", 103, "paramour_formed", json.dumps({"person_a_id": 1, "person_b_id": 3})),
                ("test", 105, "job_lost", json.dumps({"person_id": 1, "old_job": "smith"})),
                ("test", 105, "unemployment_started", json.dumps({"person_id": 1, "last_job": "smith"})),
                ("test", 110, "job_assigned", json.dumps({"person_id": 1, "job": "scribe"})),
                ("test", 110, "unemployment_ended", json.dumps({"person_id": 1, "new_job": "scribe"})),
                ("test", 112, "paramour_ended", json.dumps({"person_a_id": 1, "person_b_id": 3})),
                ("test", 115, "couple_dissolved", json.dumps({"person_a_id": 1, "person_b_id": 2})),
            ],
        )
        row, person = gdb._lookup_person(con, "test", 1)

        sheet = gdb._render_person_sheet(con, "test", row, person)
        share = gdb._render_person_share_text(con, "test", row, person)

        self.assertLess(sheet.index("Job History"), sheet.index(">Events</h3>"))
        self.assertLess(sheet.index("Partner History"), sheet.index(">Events</h3>"))
        self.assertLess(sheet.index("Paramour History"), sheet.index(">Events</h3>"))
        job_section = sheet[sheet.index("Job History"):sheet.index("Partner History")]
        partner_section = sheet[sheet.index("Partner History"):sheet.index("Paramour History")]
        paramour_section = sheet[sheet.index("Paramour History"):sheet.index(">Events</h3>")]
        self.assertLess(job_section.index("100-105"), job_section.index("105-110"))
        self.assertLess(job_section.index("105-110"), job_section.index("110-120"))
        self.assertIn("smith", job_section)
        self.assertIn("Unemployed", job_section)
        self.assertIn("scribe", job_section)
        self.assertIn("101-115", partner_section)
        self.assertIn(">Bea Forge", partner_section)
        self.assertIn("person-link", partner_section)
        self.assertIn("103-112", paramour_section)
        self.assertIn(">Cato Vale", paramour_section)
        self.assertIn("person-link", paramour_section)
        self.assertIn("Job History:\n- 100-105: smith", share)
        self.assertIn("Partner History:\n- 101-115: Bea Forge", share)
        self.assertIn("Paramour History:\n- 103-112: Cato Vale", share)

    def test_person_sheet_prominently_lists_consequence_ledgers(self) -> None:
        con = _memory_save()
        _attach_empty_genome_config(con)
        con.execute("create table world_state (id integer primary key, current_year integer)")
        con.execute("insert into world_state values (1, 120)")
        con.execute(
            """
            insert into simulation_people (
                person_id, world, is_founder, father_id, mother_id, is_alive, person_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                "test",
                1,
                None,
                None,
                1,
                json.dumps({"first_name": "Cato", "last_name": "Vale", "birthyear": 5}),
            ),
        )
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
                20,
                "test",
                1004,
                "knowledge_culture",
                json.dumps(
                    {
                        "creator_person_id": 1,
                        "patron_person_id": 2,
                        "incident_kind": "improved_plow",
                        "knowledge_domain": "toolmaking",
                        "novelty_value": 0.2,
                        "settlement_id": "aeria_north:settlement:1",
                        "region_id": "aeria_north",
                        "consequences": {
                            "knowledge_state": {
                                "domain": "toolmaking",
                                "state_delta": 0.07,
                            }
                        },
                    }
                ),
            ),
        )
        con.executemany(
            "insert into simulation_event_people values (?, ?, ?)",
            [(20, 1, "creator"), (20, 2, "patron")],
        )
        con.execute(
            """
            create table simulation_obligations_readable (
                obligation_id integer,
                source_event_id integer,
                source_event_year integer,
                source_event_type text,
                obligation_key text,
                obligation_type text,
                status text,
                owed_by_person_id integer,
                owed_to_person_id integer,
                region_id text,
                settlement_id text,
                strength real,
                start_year integer,
                expected_end_year integer,
                resolved_year integer,
                details_json text,
                created_at text,
                updated_at text
            )
            """
        )
        con.execute(
            """
            insert into simulation_obligations_readable
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                21,
                1001,
                "public_virtue",
                "beneficiary_to_benefactor",
                "relief_debt",
                "active",
                1,
                2,
                "aeria_north",
                "aeria_north:settlement:1",
                0.35,
                1001,
                1013,
                None,
                "{}",
                "now",
                "now",
            ),
        )
        con.execute(
            """
            create table simulation_reputation_marks_readable (
                reputation_mark_id integer,
                source_event_id integer,
                source_event_year integer,
                source_event_type text,
                mark_key text,
                person_id integer,
                reputation_axis text,
                reputation_before text,
                reputation_after text,
                direction text,
                mark_strength real,
                region_id text,
                settlement_id text,
                mark_year integer,
                details_json text,
                created_at text,
                updated_at text
            )
            """
        )
        con.execute(
            """
            insert into simulation_reputation_marks_readable
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                22,
                1002,
                "public_virtue",
                "leadership:1",
                1,
                "leadership",
                "low",
                "medium",
                "positive",
                0.33,
                "aeria_north",
                "aeria_north:settlement:1",
                1002,
                "{}",
                "now",
                "now",
            ),
        )
        con.execute(
            """
            create table simulation_legal_fallout_readable (
                fallout_id integer,
                source_event_id integer,
                source_event_year integer,
                source_event_type text,
                fallout_key text,
                fallout_type text,
                status text,
                principal_person_id integer,
                opposing_person_id integer,
                related_person_id integer,
                region_id text,
                settlement_id text,
                severity real,
                start_year integer,
                expected_resolution_year integer,
                resolved_year integer,
                details_json text,
                created_at text,
                updated_at text
            )
            """
        )
        con.execute(
            """
            insert into simulation_legal_fallout_readable
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                23,
                1003,
                "affair_scandal",
                "heir_legitimacy:1:3:2",
                "heir_legitimacy_challenge",
                "active",
                1,
                2,
                3,
                "aeria_north",
                "aeria_north:settlement:1",
                0.58,
                1003,
                1021,
                None,
                "{}",
                "now",
                "now",
            ),
        )
        row, person = gdb._lookup_person(con, "test", 1)

        sheet = gdb._render_person_sheet(con, "test", row, person)
        share = gdb._render_person_share_text(con, "test", row, person)

        self.assertLess(sheet.index("Consequences"), sheet.index(">Events</h3>"))
        self.assertLess(sheet.index("Consequences"), sheet.index("Job History"))
        self.assertIn("Active Obligations", sheet)
        self.assertIn("Reputation Marks", sheet)
        self.assertIn("Legal Fallout", sheet)
        self.assertIn("Knowledge Effects", sheet)
        self.assertIn("Obligation: relief debt", sheet)
        self.assertIn("Reputation: leadership", sheet)
        self.assertIn("Legal Fallout: heir legitimacy challenge", sheet)
        self.assertIn("Knowledge Effect: toolmaking", sheet)
        self.assertIn(">Bea Forge", sheet)
        self.assertIn(">Cato Vale", sheet)
        self.assertIn("Consequences:\n- Obligations: 1", share)
        self.assertIn("Reputation marks: 1", share)
        self.assertIn("Legal fallout: 1", share)
        self.assertIn("Knowledge effects: 1", share)
        self.assertIn("Active Obligations:\n- 1001-1013: relief debt", share)
        self.assertIn("Reputation Marks:\n- 1002: leadership low -> medium", share)
        self.assertIn("Legal Fallout:\n- 1003-1021: heir legitimacy challenge", share)
        self.assertIn("Knowledge Effects:\n- 1004: toolmaking", share)

    def test_partner_history_ignores_context_events_and_merges_repeated_pair(self) -> None:
        con = _memory_save()
        events = [
            _event_row(con, "couple_formed", {"person_a_id": 2, "person_b_id": 1}, year=1022),
            _event_row(con, "couple_dissolved", {"person_a_id": 2, "person_b_id": 1}, year=1030),
            _event_row(con, "couple_formed", {"person_a_id": 2, "person_b_id": 1}, year=1031),
            _event_row(con, "couple_dissolved", {"person_a_id": 3, "person_b_id": 4}, year=1039),
            _event_row(con, "couple_dissolved", {"person_a_id": 1, "person_b_id": 2}, year=1039),
        ]

        entries = gdb._relationship_history_entries(
            events,
            1,
            {},
            1100,
            formed_types={"couple_formed", "same_sex_couple_formed"},
            ended_types={"couple_dissolved"},
            current_person_key="partner_person_id",
        )

        self.assertEqual(
            entries,
            [{"start_year": 1022, "end_year": 1039, "person_id": 2}],
        )

    def test_murder_event_names_killer_and_victim(self) -> None:
        con = _memory_save()
        event = _event_row(
            con,
            "murder",
            {
                "killer_person_id": 1,
                "victim_person_id": 2,
                "incident_kind": "feud_murder",
                "motive": "revenge",
            },
        )

        killer_text = _event_sentence(con, "test", event, 1)
        victim_text = _event_sentence(con, "test", event, 2)
        killer_html = _event_sentence_html(con, "test", event, 1)
        victim_html = _event_sentence_html(con, "test", event, 2)

        self.assertIn("Ada killed Bea Forge", killer_text)
        self.assertIn("Bea was killed by Ada Forge", victim_text)
        self.assertIn("feud murder", killer_text)
        self.assertNotIn("murder: Ada", killer_text)
        self.assertIn("<strong>Ada</strong> killed", killer_html)
        self.assertIn("<strong>Bea</strong> was killed by", victim_html)
        self.assertIn(">Bea Forge</a>", killer_html)
        self.assertIn(">Ada Forge</a>", victim_html)

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

    def test_people_browser_searches_keyed_current_settlement(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = Path(tmp) / "save.sqlite"
            con = _test_connect(save)
            con.row_factory = sqlite3.Row
            ensure_checkpoint_schema(con)
            con.execute(
                """
                create table world_state (
                    id integer primary key check (id = 1),
                    start_year integer not null,
                    current_year integer not null
                )
                """
            )
            con.execute(
                """
                insert or replace into world_state (id, start_year, current_year)
                values (1, 970, 1000)
                """
            )
            con.execute("insert into simulation_region_lookup values (1, 'r1')")
            con.execute("insert into simulation_settlement_lookup values (1, 'r1:s1', 1)")
            _insert_compact_person(con, 1, "Ada", "Forge")
            con.execute(
                """
                update simulation_people
                set birthplace_region_key = 1,
                    birthplace_settlement_key = 1,
                    current_settlement_key = 1
                where person_id = 1
                """
            )
            con.commit()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: save
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                table, status, person_ids = gdb.load_people_browser(
                    "default",
                    "r1:s1",
                    "All",
                    "",
                    "",
                    "Default",
                    "Descending",
                    50,
                )
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        self.assertEqual(person_ids, [1])
        self.assertIn("showing 1 of 1 people", status)
        self.assertEqual(table["value"][0][8], "r1:s1")

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

    def test_settlement_move_event_uses_normalized_move_details(self) -> None:
        con = _test_connect(":memory:")
        con.row_factory = sqlite3.Row
        ensure_checkpoint_schema(con)
        _insert_compact_person(con, 1, "Ada", "Forge")
        _insert_compact_person(con, 2, "Bea", "Forge")
        append_simulation_event_rows(
            con,
            "default",
            [
                (
                    1143,
                    "settlement_moved",
                    {
                        "moved_person_ids": [1, 2],
                        "from_settlement_id": "r1:s1",
                        "to_settlement_id": "r2:s3",
                        "from_region_id": "r1",
                        "to_region_id": "r2",
                        "move_reason": "resource_pressure",
                    },
                )
            ],
        )

        rows = _person_event_rows(con, "default", 1)
        text = _event_sentence(con, "default", rows[0], 1)
        shown_html = _event_sentence_html(con, "default", rows[0], 1)

        self.assertEqual([r["event_type"] for r in rows], ["settlement_moved"])
        self.assertIn("r1:s1", text)
        self.assertIn("r2:s3", text)
        self.assertIn("resource pressure", text)
        self.assertNotIn("unknown to unknown", text)
        self.assertIn("r1:s1", shown_html)
        self.assertIn("r2:s3", shown_html)

    def test_job_seeker_migration_keeps_route_details_for_display(self) -> None:
        con = _test_connect(":memory:")
        con.row_factory = sqlite3.Row
        ensure_checkpoint_schema(con)
        _insert_compact_person(con, 1, "Ada", "Forge")
        append_simulation_event_rows(
            con,
            "default",
            [
                (
                    1034,
                    "job_seeker_migration",
                    {
                        "year": 1034,
                        "person_id": 1,
                        "moved_person_ids": [1],
                        "from_settlement_id": "r1:s1",
                        "to_settlement_id": "r2:s3",
                        "from_region_id": "r1",
                        "to_region_id": "r2",
                        "move_reason": "job_seeker_migration",
                    },
                )
            ],
        )

        rows = _person_event_rows(con, "default", 1)
        text = _event_sentence(con, "default", rows[0], 1)
        shown_html = _event_sentence_html(con, "default", rows[0], 1)
        payload = json.loads(
            str(
                con.execute(
                    """
                    select payload_json
                    from simulation_events
                    where event_type = 'job_seeker_migration'
                    """
                ).fetchone()["payload_json"]
            )
        )

        self.assertEqual(payload["from_settlement_id"], "r1:s1")
        self.assertEqual(payload["to_settlement_id"], "r2:s3")
        self.assertIn("planned a job seeker move from r1:s1 to r2:s3", text)
        self.assertNotIn("unknown to unknown", text)
        self.assertIn("planned a job seeker move from r1:s1 to r2:s3", shown_html)

    def test_compact_job_seeker_migration_uses_related_move_rows(self) -> None:
        con = _test_connect(":memory:")
        con.row_factory = sqlite3.Row
        ensure_checkpoint_schema(con)
        _insert_compact_person(con, 1, "Ada", "Forge")
        _insert_compact_person(con, 2, "Bea", "Forge")
        job_event_id = append_simulation_event_rows(
            con,
            "default",
            [
                (
                    1034,
                    "job_seeker_migration",
                    {
                        "year": 1034,
                        "person_id": 1,
                        "moved_person_ids": [1, 2],
                        "from_settlement_id": "r1:s1",
                        "to_settlement_id": "r2:s3",
                        "from_region_id": "r1",
                        "to_region_id": "r2",
                        "move_reason": "job_seeker_migration",
                    },
                )
            ],
        )[0]
        con.execute(
            """
            update simulation_events
            set payload_json = ?
            where id = ?
            """,
            (
                json.dumps(
                    {
                        "year": 1034,
                        "person_id": 1,
                        "moved_person_ids": [1, 2],
                        "move_reason": "job_seeker_migration",
                    },
                    separators=(",", ":"),
                ),
                job_event_id,
            ),
        )
        append_simulation_event_rows(
            con,
            "default",
            [
                (
                    1035,
                    "settlement_moved",
                    {
                        "moved_person_ids": [1, 2],
                        "from_settlement_id": "r1:s1",
                        "to_settlement_id": "r2:s3",
                        "from_region_id": "r1",
                        "to_region_id": "r2",
                        "move_reason": "job_seeker_migration",
                        "requested_year": 1034,
                        "planned_apply_year": 1035,
                        "source_event": "job_seeker_migration",
                        "group_id": "job_seeker:1:1034",
                    },
                )
            ],
        )

        rows = _person_event_rows(con, "default", 1)
        job_row = next(row for row in rows if row["event_type"] == "job_seeker_migration")
        text = _event_sentence(con, "default", job_row, 1)
        shown_html = _event_sentence_html(con, "default", job_row, 1)

        self.assertIn("planned a job seeker move from r1:s1 to r2:s3", text)
        self.assertNotIn("unknown to unknown", text)
        self.assertIn("planned a job seeker move from r1:s1 to r2:s3", shown_html)

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

    def test_person_sheet_genome_traits_follow_config_order(self) -> None:
        con = _memory_save()
        _attach_empty_genome_config(con)
        con.execute("create table world_state (id integer primary key, current_year integer)")
        con.execute("insert into world_state values (1, 120)")
        con.execute(
            """
            create table cfg.genome_save_columns (
                slot text,
                trait text,
                sort_order integer
            )
            """
        )
        con.executemany(
            "insert into cfg.genome_save_columns values (?, ?, ?)",
            [
                ("a", "physical", 1),
                ("b", "courage", 2),
                ("c", "wit", 3),
                ("d", "curiosity", 4),
            ],
        )
        con.execute(
            "update simulation_people set person_json = ? where person_id = 1",
            (
                json.dumps(
                    {
                        "first_name": "Ada",
                        "last_name": "Forge",
                        "birthyear": 0,
                        "mind_body": {
                            "wit": 95.0,
                            "physical": -10.0,
                            "curiosity": 0.0,
                            "courage": 40.0,
                        },
                    }
                ),
            ),
        )
        row, person = gdb._lookup_person(con, "test", 1)

        sheet = gdb._render_person_sheet(con, "test", row, person)
        trait_grid = sheet[sheet.index('<div class="trait-grid">'):]

        self.assertLess(trait_grid.index(">physical<"), trait_grid.index(">courage<"))
        self.assertLess(trait_grid.index(">courage<"), trait_grid.index(">wit<"))
        self.assertLess(trait_grid.index(">wit<"), trait_grid.index(">curiosity<"))

    def test_region_sheet_summarizes_settlements_jobs_and_map(self) -> None:
        con = _memory_place_save()

        html = _render_region_sheet(con, "test", "r1")

        self.assertIn("River Country", html)
        self.assertIn("Fordham", html)
        self.assertIn("miller: 1", html)
        self.assertIn("<svg", html)

    def test_generated_region_map_preserves_region_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            cfg = root / "config.sqlite"
            save = root / "save.sqlite"
            cfg.touch()
            save.touch()
            geometry = WorldMapGeometry(
                world="test",
                version="test",
                width=500,
                height=500,
                cells=[],
                micro_cells=[
                    MicroRegionCell(
                        micro_id="m1",
                        region_id="r1",
                        continent_id="c1",
                        center_x=200,
                        center_y=50,
                        polygon=[(0, 0), (400, 0), (400, 100), (0, 100)],
                        elevation=0.4,
                        moisture=0.5,
                        terrain_family="plains",
                        is_coastal=False,
                    )
                ],
                features=[],
                edges=[],
                rivers=[],
            )

            original_db_path = gdb._db_path
            original_geometry = gdb._cached_world_map_geometry
            gdb._db_path = lambda world, db_kind: cfg if db_kind == "Config DB" else save
            gdb._cached_world_map_geometry = lambda *args: geometry
            try:
                html = gdb._render_generated_region_map("test", "r1", [])
            finally:
                gdb._db_path = original_db_path
                gdb._cached_world_map_geometry = original_geometry

        self.assertIn('viewBox="0 0 100.00 32.50"', html)
        self.assertIn('width="100.00" height="32.50"', html)
        self.assertNotIn('viewBox="0 0 100 100"', html)

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

    def test_place_browsers_read_keyed_place_schema_through_readable_views(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_keyed_place_save()
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
            con.execute("insert into simulation_polities values (1, 'River Crown', 'duchy', 'active', null, 'r1:s1', 1)")
            con.execute("insert into simulation_polity_territory values (1, 'settlement', 'r1:s1', 1, null)")
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            original_dataframe = getattr(gdb.gr, "Dataframe", None)
            gdb._db_path = lambda world, db_kind: path
            gdb.gr.Dataframe = lambda **kwargs: kwargs
            try:
                settlement_table, _, settlement_ids = gdb.load_settlements_browser("test", "", "Active", 50)
                region_table, _, region_ids = gdb.load_regions_browser_fresh("test", "", 50)
                region_sheet = gdb.render_region_outputs("test", "r1")
                town_sheet = gdb.render_settlement_outputs("test", "r1:s1")
                place_rows, _, _, place_state, _ = gdb._places_browser_data_and_state("test", "Regions", "", 50)
            finally:
                gdb._db_path = original_db_path
                if original_dataframe is not None:
                    gdb.gr.Dataframe = original_dataframe

        self.assertEqual(settlement_table["value"][0][0], "Fordham")
        self.assertEqual(settlement_ids, ["r1:s1"])
        self.assertEqual(region_table["value"][0][0], "River Country")
        self.assertEqual(region_ids, ["r1"])
        self.assertEqual(region_table["value"][0][1], 13)
        self.assertIn("labor (10)", region_table["value"][0][-1])
        self.assertEqual(settlement_table["value"][0][2], 13)
        self.assertIn("labor (10)", settlement_table["value"][0][-1])
        self.assertIn("River Crown", region_sheet)
        self.assertIn('<span class="label">Alive</span><span class="value">13</span>', region_sheet)
        self.assertIn("labor: 10", region_sheet)
        self.assertIn("miller: 1", region_sheet)
        self.assertIn("Fordham (hamlet, active, alive 13)", region_sheet)
        self.assertIn("Ada Forge", region_sheet)
        self.assertIn("Fordham", town_sheet)
        self.assertIn('<span class="label">Alive</span><span class="value">13</span>', town_sheet)
        self.assertIn("labor (10)", town_sheet)
        self.assertIn("Bea Forge", town_sheet)
        self.assertEqual(place_rows[0]["Name"], "River Country")
        self.assertEqual(place_rows[0]["Alive"], 13)
        self.assertIn("labor (10)", place_rows[0]["Top Jobs"])
        self.assertIn("Fordham (hamlet, active, alive 13)", place_state)
        self.assertIn("Fordham", place_state)

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
        self.assertIn('class="micro-cell terrain-', shown)
        self.assertIn('onclick="', shown)
        self.assertIn("map-open-selection", shown)

    def test_world_map_html_renders_roads_and_checkbox_hides_them(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            cfg = root / "config.sqlite"
            cfg.touch()
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as con:
                con.execute("create table world_state (id integer primary key, current_year integer)")
                con.execute("insert into world_state values (1, 42)")
                con.execute(
                    """
                    create table simulation_settlements (
                        settlement_id text,
                        region_id text,
                        display_name text,
                        population_cap integer,
                        status text,
                        site_slot integer,
                        local_geography_json text
                    )
                    """
                )
                for settlement_id, name, x, y in (
                    ("r1:a", "Aston", 0.18, 0.48),
                    ("r1:b", "Barton", 0.72, 0.52),
                ):
                    con.execute(
                        "insert into simulation_settlements values (?, 'r1', ?, 25, 'active', 1, ?)",
                        (
                            settlement_id,
                            name,
                            json.dumps(
                                {
                                    "features": [],
                                    "settlements": [{"settlement_slot": 0, "x": x, "y": y}],
                                }
                            ),
                        ),
                    )
                con.execute(
                    """
                    create table simulation_event_moves_readable (
                        event_id integer,
                        sim_year integer,
                        event_type text,
                        moved_person_id integer,
                        from_settlement_id text,
                        to_settlement_id text,
                        move_reason text
                    )
                    """
                )
                for event_id in range(1, 4):
                    con.execute(
                        """
                        insert into simulation_event_moves_readable values (
                            ?, 42, 'settlement_moved', ?, 'r1:a', 'r1:b', 'unit_test'
                        )
                        """,
                        (event_id, event_id),
                    )
                con.commit()
            geometry = WorldMapGeometry(
                world="test",
                version="unit",
                width=1.0,
                height=1.0,
                cells=[
                    RegionCell(
                        region_id="r1",
                        continent_id="test",
                        center_x=0.5,
                        center_y=0.5,
                        polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                        elevation=0.0,
                        moisture=0.0,
                        ruggedness=0.0,
                        terrain_family="plains",
                        is_coastal=False,
                        feature_ids=[],
                    )
                ],
                micro_cells=[],
                features=[],
                edges=[],
                rivers=[],
            )

            original_db_path = gdb._db_path
            original_geometry_cache = gdb._cached_world_map_geometry
            gdb._db_path = lambda world, db_kind: cfg if db_kind == "Config DB" else save
            gdb._cached_world_map_geometry = lambda *args, **kwargs: geometry
            gdb._render_world_map_html_cached.cache_clear()
            try:
                shown = render_world_map_html("test", include_overlays=True, include_roads=True)
                hidden = render_world_map_html("test", include_overlays=True, include_roads=False)
            finally:
                gdb._db_path = original_db_path
                gdb._cached_world_map_geometry = original_geometry_cache
                gdb._render_world_map_html_cached.cache_clear()

        self.assertIn('class="road road-line"', shown)
        self.assertIn("data-road-usage=", shown)
        self.assertIn('data-road-actual="3.0000"', shown)
        self.assertNotIn('class="road road-line"', hidden)
        self.assertNotIn("data-road-usage", hidden)

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
                feature_html = render_world_map_selection_detail(
                    "test",
                    json.dumps(
                        {
                            "view": "Features",
                            "id": "r1:f0",
                            "region_id": "r1",
                            "name": "Bluewater",
                            "kind": "river",
                            "etymology": "blue · river",
                            "named": "1",
                        }
                    ),
                )
                generic_feature_html = render_world_map_selection_detail(
                    "test",
                    json.dumps(
                        {
                            "view": "Features",
                            "id": "r1:wf1",
                            "region_id": "r1",
                            "name": "Ford",
                            "kind": "ford",
                            "named": "0",
                        }
                    ),
                )
            finally:
                gdb._db_path = original_db_path

        self.assertIn("River Country", region_html)
        self.assertIn("Fordham", town_html)
        self.assertIn("Bluewater", feature_html)
        self.assertIn("Named river", feature_html)
        self.assertIn("blue · river", feature_html)
        self.assertIn("Ford", generic_feature_html)
        self.assertIn("Regional ford landmark", generic_feature_html)
        self.assertIn("Unnamed", generic_feature_html)

    def test_world_map_overlays_read_named_features_from_local_geography(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()
            geometry = WorldMapGeometry(
                world="test",
                version="unit",
                width=1.0,
                height=1.0,
                cells=[
                    RegionCell(
                        region_id="r1",
                        continent_id="test",
                        center_x=0.5,
                        center_y=0.5,
                        polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                        elevation=0.0,
                        moisture=0.0,
                        ruggedness=0.0,
                        terrain_family="plains",
                        is_coastal=False,
                        feature_ids=[],
                    )
                ],
                micro_cells=[],
                features=[],
                edges=[],
                rivers=[],
            )

            overlays = load_world_map_overlays(geometry=geometry, save_db_path=path)

        self.assertEqual([f.display_name for f in overlays.features], ["Bluewater"])
        self.assertEqual(overlays.features[0].kind, "river")
        self.assertEqual(overlays.features[0].region_id, "r1")

    def test_world_map_overlays_read_keyed_place_schema_through_readable_views(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_keyed_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()
            geometry = WorldMapGeometry(
                world="test",
                version="unit",
                width=1.0,
                height=1.0,
                cells=[
                    RegionCell(
                        region_id="r1",
                        continent_id="test",
                        center_x=0.5,
                        center_y=0.5,
                        polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                        elevation=0.0,
                        moisture=0.0,
                        ruggedness=0.0,
                        terrain_family="plains",
                        is_coastal=False,
                        feature_ids=[],
                    )
                ],
                micro_cells=[],
                features=[],
                edges=[],
                rivers=[],
            )

            overlays = load_world_map_overlays(geometry=geometry, save_db_path=path)

        self.assertEqual([s.settlement_id for s in overlays.settlements], ["r1:s1"])
        self.assertEqual(overlays.settlements[0].display_name, "Fordham")

    def test_world_map_region_without_save_settlements_explains_empty_region(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "save.sqlite"
            con = _memory_keyed_place_save()
            con.commit()
            with closing(sqlite3.connect(path)) as out:
                con.backup(out)
            con.close()

            original_db_path = gdb._db_path
            gdb._db_path = lambda world, db_kind: path
            try:
                html = render_world_map_selection_detail(
                    "test",
                    json.dumps({"view": "Regions", "id": "boreas_clear_river"}),
                )
            finally:
                gdb._db_path = original_db_path

        self.assertIn("No settlements are recorded for region boreas_clear_river", html)
        self.assertNotIn("No region named", html)

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
        self.assertIn("Bluewater", html)
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
