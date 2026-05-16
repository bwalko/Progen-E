"""Same-sex official couples: romantic score, prosperity, social friction."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.simulation_social import (
    _same_sex_acceptance_probability,
    maybe_form_same_sex_couples,
)


class TestSimulationSameSexCouples(unittest.TestCase):
    def test_acceptance_increases_with_prosperity(self) -> None:
        low = _same_sex_acceptance_probability(romantic_01=1.0, prosperity_01=0.05)
        high = _same_sex_acceptance_probability(romantic_01=1.0, prosperity_01=1.0)
        self.assertGreater(high, low)

    def test_acceptance_strictly_below_unfrictioned_romantic(self) -> None:
        """Social friction keeps same-sex acceptance below romantic * prosperity ceiling."""
        p = _same_sex_acceptance_probability(romantic_01=1.0, prosperity_01=1.0)
        self.assertLess(p, 1.0 * 1.0)

    def test_same_sex_couple_forms_with_high_acceptance_patch(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=1000,
                refresh_config=False,
                placename_rng_salt=901,
            ) as ctx:
                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                f1 = replace(
                    generate_person_random(
                        gender="Female",
                        age=28,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1972,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    genome={k: 0.0 for k in ("mating drive", "persuasion", "symmetry", "wit", "neurochemical")},
                    job="teacher",
                )
                f2 = replace(
                    generate_person_random(
                        gender="Female",
                        age=27,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1973,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    genome={k: 0.0 for k in ("mating drive", "persuasion", "symmetry", "wit", "neurochemical")},
                    job="nurse",
                )
                a = ctx.add_person(person=f1, is_founder=False)
                b = ctx.add_person(person=f2, is_founder=False)
                rm = MagicMock()
                rm.random.return_value = 0.0
                with patch(
                    "library.simulation_social._same_sex_acceptance_probability",
                    return_value=0.95,
                ), patch("library.simulation_social._same_sex_pair_rng", return_value=rm):
                    maybe_form_same_sex_couples(ctx, 2000)
                self.assertEqual(a.person.partner_person_id, b.person_id)
                self.assertEqual(b.person.partner_person_id, a.person_id)
                ev = [
                    pl
                    for _y, et, pl in ctx._pending_simulation_events
                    if et == "same_sex_couple_formed"
                ]
                self.assertTrue(ev)
                self.assertEqual(ev[-1].get("partnership_motive"), "same_sex_romantic")

    def test_siblings_not_paired(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=1000,
                refresh_config=False,
                placename_rng_salt=902,
            ) as ctx:
                st = ctx.ensure_active_settlement_for_region("aeria_north")
                sid = st.settlement_id
                p = replace(
                    generate_person_random(
                        gender="Female",
                        age=40,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1960,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                )
                pr = ctx.add_person(person=p, is_founder=False)
                c1 = replace(
                    generate_person_random(
                        gender="Female",
                        age=20,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1980,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    genome={"mating drive": 0.0},
                )
                c2 = replace(
                    generate_person_random(
                        gender="Female",
                        age=19,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1981,
                    birthplace_settlement_id=sid,
                    current_settlement_id=sid,
                    genome={"mating drive": 0.0},
                )
                r1 = ctx.add_person(
                    person=c1,
                    is_founder=False,
                    mother_id=pr.person_id,
                    father_id=None,
                )
                r2 = ctx.add_person(
                    person=c2,
                    is_founder=False,
                    mother_id=pr.person_id,
                    father_id=None,
                )
                rm = MagicMock()
                rm.random.return_value = 0.0
                with patch(
                    "library.simulation_social._same_sex_acceptance_probability",
                    return_value=0.99,
                ), patch("library.simulation_social._same_sex_pair_rng", return_value=rm):
                    maybe_form_same_sex_couples(ctx, 2000)
                self.assertIsNone(ctx.id_to_record[r1.person_id].person.partner_person_id)
                self.assertIsNone(ctx.id_to_record[r2.person_id].person.partner_person_id)


if __name__ == "__main__":
    unittest.main()
