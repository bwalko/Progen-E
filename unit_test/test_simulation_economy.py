"""Pooled prosperity, job_economics catalog, and treasury tick."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "numpy" not in sys.modules and importlib.util.find_spec("numpy") is None:
    class _NumpyRandomStub:
        def seed(self, *_):
            return None

        def normal(self, loc=0.0, scale=1.0, size=None):
            if size is None:
                return float(loc)
            return [float(loc)] * int(size)

    sys.modules["numpy"] = types.SimpleNamespace(
        random=_NumpyRandomStub(),
        clip=lambda value, lo, hi: max(lo, min(hi, value)),
    )

from library.job_economics import normalize_job_catalog_key
from library.person import Person
from library.reproduction import pair_prosperity_01
from library.settlements import SettlementState
from library.simulation_economy import simulation_economy_annual_tick
from library.simulation_context import SimulationContext, SimulationPersonRecord

_ROOT = Path(__file__).resolve().parent.parent
_GENOME_JOBS = _ROOT / "config" / "genome_jobs.csv"
_JOB_ECON = _ROOT / "config" / "job_economics.csv"
_JOB_MARKET = _ROOT / "config" / "job_market.csv"


class TestSimulationEconomy(unittest.TestCase):
    def test_normalize_strips_sex_tag(self) -> None:
        self.assertEqual(normalize_job_catalog_key("soldier [M]"), "soldier")
        self.assertEqual(normalize_job_catalog_key("  Midwife [F]  "), "midwife")

    def test_each_era_has_base_row_in_csv(self) -> None:
        if not _JOB_ECON.is_file():
            self.skipTest("job_economics.csv missing")
        with _JOB_ECON.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            bases = {
                (r["job_key"].strip(), (r["era"] or "").strip().lower())
                for r in reader
                if (r.get("row_kind") or "").strip().lower() == "base"
            }
        for era in ("prehistoric", "bronze_age", "iron_age", "medieval", "modern"):
            self.assertIn(("*", era), bases)
        self.assertIn(("*", "*"), bases)

    def test_job_market_csv_has_human_editable_defaults(self) -> None:
        if not _JOB_MARKET.is_file():
            self.skipTest("job_market.csv missing")
        with _JOB_MARKET.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows)
        self.assertIn("job_family", rows[0])
        self.assertIn("saturation_curve", rows[0])
        self.assertTrue(any(r["job_key"] == "*" for r in rows))

    def test_elite_warrior_beats_gatherer_prehistoric(self) -> None:
        from library.job_economics import JobEconomicsCatalog

        if not (_ROOT / "worlds" / "default" / "config.sqlite").is_file():
            self.skipTest("default config.sqlite missing")
        cat = JobEconomicsCatalog.load(_ROOT / "worlds" / "default" / "config.sqlite")
        if cat._legacy_rows is not None:
            self.skipTest("legacy job_economics format")
        g = cat.lookup("gatherer near camp", "prehistoric")
        w = cat.lookup("band defender", "prehistoric")
        self.assertGreater(w.wage_yield, g.wage_yield * 3.0)
        self.assertGreater(w.value_add, g.value_add * 3.0)

    def test_pair_prosperity_uses_job_prosperity(self) -> None:
        a = Person(
            first_name="A",
            last_name="X",
            gender="Female",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            employment_status="employed",
            job="analyst",
            job_prosperity_01=0.9,
        )
        b = Person(
            first_name="B",
            last_name="Y",
            gender="Male",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            employment_status="employed",
            job="engineer",
            job_prosperity_01=0.1,
        )
        high = pair_prosperity_01(a, b, pressure_a=0.2, pressure_b=0.2)
        b2 = Person(
            first_name="B",
            last_name="Y",
            gender="Male",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            employment_status="employed",
            job="engineer",
            job_prosperity_01=0.9,
        )
        higher = pair_prosperity_01(a, b2, pressure_a=0.2, pressure_b=0.2)
        self.assertGreater(higher, high)

    def test_economy_tick_nonnegative_pool_and_tax(self) -> None:
        rid = "fixture_region"
        sid = f"{rid}:s1"
        st = SettlementState(
            region_id=rid,
            settlement_id=sid,
            resident_count=2,
            prosperity_pool=0.05,
            food_pressure=0.4,
            stability=0.5,
            market_pull=0.1,
        )
        p1 = Person(
            first_name="U",
            last_name="One",
            gender="Female",
            ethnic="Old Norse",
            species="Human",
            birthyear=1990,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
            employment_status="employed",
            job="analyst",
            job_era="modern",
            leader_quality="strong",
            leader_tendency="high",
            genome={"focus": 5.0},
        )
        p2 = Person(
            first_name="V",
            last_name="Two",
            gender="Male",
            ethnic="Old Norse",
            species="Human",
            birthyear=1990,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
            employment_status="employed",
            job="software engineer",
            job_era="modern",
            leader_quality="strong",
            leader_tendency="high",
            genome={"focus": 5.0},
        )
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            db_path = Path(tmp.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE job_economics (
                    job_key TEXT,
                    era TEXT,
                    row_kind TEXT,
                    pool_draw REAL,
                    wage_yield REAL,
                    value_add REAL,
                    tax_rate REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
                VALUES ('*', 'modern', 'base', 0.21, 0.31, 0.29, 0.078)
                """
            )
            conn.execute(
                """
                INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
                VALUES ('analyst', 'modern', 'deviation', '', '1.2', '', '')
                """
            )
            conn.execute(
                """
                INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
                VALUES ('software engineer', 'modern', 'deviation', '', '1.3', '', '')
                """
            )
            conn.commit()
            conn.close()

            ctx = SimulationContext(
                db_path=db_path,
                save_db_path=db_path,
                current_year=2000,
                simulation_start_year=2000,
                history_equivalent_start_year=2000,
            )
            ctx.settlements_by_id = {sid: st}
            ctx.settlement_ids_by_region = {rid: [sid]}
            ctx.region_prosperity_pool = {rid: 0.4}
            ctx.region_treasury_balance = {rid: 0.0}
            r1 = SimulationPersonRecord(person_id=1, person=p1, is_founder=False)
            r2 = SimulationPersonRecord(person_id=2, person=p2, is_founder=False)
            ctx.people = [r1, r2]
            ctx.id_to_record = {1: r1, 2: r2}
            ctx.current_people_ids = {1, 2}

            with patch.object(
                SimulationContext,
                "effective_regional_population_cap",
                lambda self, region_id: 1000,
            ):
                simulation_economy_annual_tick(ctx, 2000)
            self.assertGreaterEqual(ctx.settlements_by_id[sid].prosperity_pool, 0.0)
            self.assertGreaterEqual(ctx.region_treasury_balance.get(rid, 0.0), 0.0)
            self.assertIsNotNone(r1.person.job_prosperity_01)
            self.assertGreater(r1.person.job_prosperity_01, 0.09)
        finally:
            db_path.unlink(missing_ok=True)

    def test_economy_tick_updates_household_prosperity_and_purseholder(self) -> None:
        rid = "fixture_region"
        sid = f"{rid}:s1"
        st = SettlementState(
            region_id=rid,
            settlement_id=sid,
            resident_count=3,
            prosperity_pool=1.2,
            food_pressure=0.2,
            stability=0.7,
            market_pull=0.1,
        )
        p1 = Person(
            first_name="A",
            last_name="House",
            gender="Female",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
            partner_person_id=2,
            employment_status="employed",
            job="analyst",
            job_era="modern",
            household_prosperity=1.0,
            genome={"assertiveness": 60.0, "frugality": 40.0},
        )
        p2 = Person(
            first_name="B",
            last_name="House",
            gender="Male",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
            partner_person_id=1,
            employment_status="employed",
            job="software engineer",
            job_era="modern",
            household_prosperity=1.0,
            genome={"assertiveness": -20.0, "frugality": 20.0},
        )
        child = Person(
            first_name="C",
            last_name="House",
            gender="Female",
            ethnic="Old Norse",
            species="Human",
            birthyear=1998,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
        )
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            db_path = Path(tmp.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE job_economics (
                    job_key TEXT,
                    era TEXT,
                    row_kind TEXT,
                    pool_draw REAL,
                    wage_yield REAL,
                    value_add REAL,
                    tax_rate REAL
                )
                """
            )
            conn.execute("INSERT INTO job_economics VALUES ('*','modern','base',0.21,0.8,0.29,0.078)")
            conn.commit()
            conn.close()

            ctx = SimulationContext(
                db_path=db_path,
                save_db_path=db_path,
                current_year=2000,
                simulation_start_year=2000,
                history_equivalent_start_year=2000,
            )
            ctx.settlements_by_id = {sid: st}
            ctx.settlement_ids_by_region = {rid: [sid]}
            ctx.region_prosperity_pool = {rid: 1.0}
            ctx.region_treasury_balance = {rid: 0.0}
            r1 = SimulationPersonRecord(person_id=1, person=p1, is_founder=False)
            r2 = SimulationPersonRecord(person_id=2, person=p2, is_founder=False)
            r3 = SimulationPersonRecord(
                person_id=3, person=child, is_founder=False, father_id=2, mother_id=1
            )
            ctx.people = [r1, r2, r3]
            ctx.id_to_record = {1: r1, 2: r2, 3: r3}
            ctx.current_people_ids = {1, 2, 3}

            with patch.object(
                SimulationContext,
                "effective_regional_population_cap",
                lambda self, region_id: 1000,
            ):
                simulation_economy_annual_tick(ctx, 2000)

            self.assertEqual(r1.person.household_purseholder_person_id, 1)
            self.assertEqual(r2.person.household_purseholder_person_id, 1)
            self.assertEqual(r3.person.household_purseholder_person_id, 1)
            self.assertGreater(r1.person.household_prosperity or 0.0, 1.0)
            self.assertEqual(r1.person.household_prosperity, r2.person.household_prosperity)
        finally:
            db_path.unlink(missing_ok=True)

    def test_food_job_market_effect_reduces_local_food_pressure(self) -> None:
        rid = "fixture_region"
        sid = f"{rid}:s1"
        st = SettlementState(
            region_id=rid,
            settlement_id=sid,
            resident_count=1,
            prosperity_pool=1.0,
            food_pressure=1.2,
            stability=0.5,
            market_pull=0.1,
        )
        farmer = Person(
            first_name="F",
            last_name="Food",
            gender="Female",
            ethnic="Old Norse",
            species="Human",
            birthyear=1980,
            current_settlement_id=sid,
            birthplace_settlement_id=sid,
            birthplace_region_id=rid,
            employment_status="employed",
            job="farmer",
            job_era="modern",
            job_tier="common",
            genome={"frugality": 0.0},
        )
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            db_path = Path(tmp.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE job_economics (
                    job_key TEXT, era TEXT, row_kind TEXT,
                    pool_draw REAL, wage_yield REAL, value_add REAL, tax_rate REAL
                )
                """
            )
            conn.execute("INSERT INTO job_economics VALUES ('*','modern','base',0.21,1.2,0.29,0.078)")
            conn.execute(
                """
                CREATE TABLE job_market (
                    job_key TEXT, job_family TEXT, essential_need REAL, luxury_need REAL,
                    urban_scale REAL, scarcity_resilience REAL, saturation_curve TEXT,
                    food_delta REAL, stability_delta REAL, care_delta REAL,
                    capacity_delta REAL, taxability REAL
                )
                """
            )
            conn.execute("INSERT INTO job_market VALUES ('farmer','food',1.0,0.0,0.1,1.0,'steep',1.0,0.0,0.0,0.0,0.3)")
            conn.commit()
            conn.close()

            ctx = SimulationContext(
                db_path=db_path,
                save_db_path=db_path,
                current_year=2000,
                simulation_start_year=2000,
                history_equivalent_start_year=2000,
            )
            ctx.settlements_by_id = {sid: st}
            ctx.settlement_ids_by_region = {rid: [sid]}
            ctx.region_prosperity_pool = {rid: 1.0}
            rec = SimulationPersonRecord(person_id=1, person=farmer, is_founder=False)
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}
            with patch.object(
                SimulationContext,
                "effective_regional_population_cap",
                lambda self, region_id: 1000,
            ):
                simulation_economy_annual_tick(ctx, 2000)

            self.assertLess(ctx.settlements_by_id[sid].food_pressure, 1.2)
            event_types = [et for _y, et, _payload in ctx._pending_simulation_events]
            self.assertIn("settlement_job_market_effect", event_types)
        finally:
            db_path.unlink(missing_ok=True)
