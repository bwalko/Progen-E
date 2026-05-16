"""Tests for ``library.place_namer`` and lazy naming hooks."""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.geography import Region, _population_scale_cache
from library.place_namer import (
    naming_threshold_for_world,
    placeholder_region_label,
    polity_geographic_label,
    region_geographic_label,
    region_label_after_dominant_city,
)
from library.simulation_context import SimulationContext
from library.simulation_government import simulation_government_annual_tick


def _force_population_scale(cfg_path: Path, scale: float) -> None:
    with closing(sqlite3.connect(cfg_path)) as conn:
        conn.execute("UPDATE world_start SET population_scale = ?", (str(scale),))
        conn.commit()
    _population_scale_cache.clear()


class TestPlaceNamer(unittest.TestCase):
    def test_region_geographic_label_uses_terrain_biome(self) -> None:
        r = Region(
            world="default",
            region_id="r_test",
            region_name="Canon",
            continent_id="c1",
            biome="tundra",
            terrain="basalt",
            carrying_capacity=100,
            keywords="",
        )
        lab = region_geographic_label(r, rng_seed=12345)
        self.assertIn("tundra", lab.lower())
        self.assertIn("basalt", lab.lower())

    def test_region_geographic_label_no_concatenation(self) -> None:
        """No-space concat (e.g. 'PlainsTemperate') is never produced."""
        r = Region(
            world="default",
            region_id="r_concat",
            region_name="Canon",
            continent_id="c1",
            biome="temperate",
            terrain="plains",
            carrying_capacity=100,
            keywords="slow rivers",
        )
        for seed in range(50):
            lab = region_geographic_label(r, rng_seed=seed)
            self.assertNotIn("PlainsTemperate", lab)
            self.assertNotIn("TemperatePlains", lab)
            # No leading keyword before "The".
            self.assertFalse(
                lab.split()[1:2] == ["The"],
                f"keyword should not appear before 'The': {lab!r}",
            )

    def test_region_geographic_label_collapses_redundant_terrain_biome(self) -> None:
        """When terrain and biome substring-overlap, the label uses one descriptor."""
        r = Region(
            world="default",
            region_id="r_coast",
            region_name="Canon",
            continent_id="c1",
            biome="coastal",
            terrain="coast",
            carrying_capacity=100,
            keywords="granite cliffs",
        )
        for seed in range(50):
            lab = region_geographic_label(r, rng_seed=seed).lower()
            self.assertNotIn("coast coastal", lab)
            self.assertNotIn("coastal coast", lab)

    def test_polity_geographic_label_county_vs_region(self) -> None:
        self.assertEqual(
            polity_geographic_label(
                "county",
                region_label="The North",
                anchor_settlement_display_name="Millford",
                jurisdiction_grain="settlement",
            ),
            "County of Millford",
        )
        self.assertIn(
            "Duchy of",
            polity_geographic_label(
                "duchy",
                region_label="The North",
                anchor_settlement_display_name=None,
                jurisdiction_grain="region",
            ),
        )

    def test_naming_threshold_scales(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "c.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 0.1)
            self.assertEqual(naming_threshold_for_world("default", cfg), 5)

    def test_below_naming_threshold_region_placeholder(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 10.0)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="pn1",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                rid = ctx._residence_region_id(ctx.people[0]) or ""
                self.assertTrue(rid)
                thr = naming_threshold_for_world(ctx.world, ctx.db_path)
                self.assertGreater(thr, ctx.count_alive_in_region(rid))
                from library import simulation_government as sg

                self.assertIn(
                    "Unnamed",
                    sg._region_display(ctx, rid),
                )

    def test_city_takeover_region_label_default_pool(self) -> None:
        lab = region_label_after_dominant_city("Millford", rng_seed=1)
        self.assertIn("Millford", lab)
        self.assertNotIn("Country", lab)

    def test_city_takeover_region_label_culture_specific(self) -> None:
        labs = {
            region_label_after_dominant_city(
                "Yorkby", culture="Middle English", rng_seed=s
            )
            for s in range(50)
        }
        self.assertTrue(any(lab.endswith("shire") for lab in labs))
        self.assertTrue(any("Greater" in lab for lab in labs))
        labs_fr = {
            region_label_after_dominant_city(
                "Lyon", culture="Old French", rng_seed=s
            )
            for s in range(50)
        }
        self.assertTrue(any(lab.startswith("Pays de ") for lab in labs_fr))

    def test_dominant_city_renames_region_after_threshold(self) -> None:
        random.seed(99)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "c.sqlite"
            sav = root / "s.sqlite"
            load_all_csvs_into_sqlite(cfg)
            _force_population_scale(cfg, 0.0002)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="pn2",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                rid = ""
                sid_big = ""
                sid_small = ""
                from dataclasses import replace

                from library.settlements import make_settlement_id

                p0 = ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx, simulation_year=1000
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1000)
                rid = (ctx._residence_region_id(p0) or "").strip()
                sid_big = (
                    p0.person.current_settlement_id
                    or p0.person.birthplace_settlement_id
                    or ""
                ).strip()
                st1 = ctx.settlements_by_id[sid_big]
                sid_small = make_settlement_id(rid, 2)
                ctx.settlements_by_id[sid_small] = replace(
                    st1,
                    settlement_id=sid_small,
                    display_name="Village Two",
                    resident_count=0,
                )
                lst = list(ctx.settlement_ids_by_region.get(rid, []))
                if sid_small not in lst:
                    lst.append(sid_small)
                ctx.settlement_ids_by_region[rid] = sorted(lst)
                # Many in sid_big, few in sid_small — dominant-city ratio on ``rid``.
                for i in range(25):
                    ctx.add_person(
                        person=generate_person_random(
                            simulation_context=ctx,
                            simulation_year=1001 + i,
                            birthplace_settlement_id=sid_big,
                            birthplace_region_id=rid,
                        ),
                        is_founder=True,
                    )
                ctx.add_person(
                    person=generate_person_random(
                        simulation_context=ctx,
                        simulation_year=1100,
                        birthplace_settlement_id=sid_small,
                        birthplace_region_id=rid,
                    ),
                    is_founder=True,
                )
                simulation_government_annual_tick(ctx, 1100)
                self.assertGreaterEqual(ctx.count_alive_in_region(rid), 27)
                lbl = (ctx.region_display_label_overrides or {}).get(rid, "")
                self.assertTrue(lbl)
                # Label should mention the dominant city display name in some form.
                st = ctx.settlements_by_id.get(sid_big)
                dname = (st.display_name or sid_big) if st is not None else sid_big
                self.assertIn(dname, lbl)


if __name__ == "__main__":
    unittest.main()
