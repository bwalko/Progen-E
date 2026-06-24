import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from library.nondetailed_population import seed_nondetailed_from_active_settlements
from library.settlement_affordances import (
    build_settlement_affordance_profile,
    growth_invariant_cap,
    new_settlement_backfill_cap,
)
from library.settlements import SettlementState
from library.world_save import ensure_checkpoint_schema


def _geo(*kinds: str, anchor: str | None = None) -> str:
    features = [
        {"feature_id": f"f{i}", "kind": kind}
        for i, kind in enumerate(kinds, start=1)
    ]
    anchor_id = anchor
    if not anchor_id and features:
        anchor_id = features[0]["feature_id"]
    return json.dumps(
        {
            "features": features,
            "settlements": [
                {
                    "settlement_slot": 0,
                    "anchor_feature_id": anchor_id or "",
                    "narrative_hint": "",
                }
            ],
        }
    )


def _ctx(region_text: str, routes: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        world="default",
        db_path=Path("unused-config.sqlite"),
        region_by_id={
            "r1": SimpleNamespace(
                region_name="R1",
                biome=region_text,
                terrain=region_text,
                keywords=region_text,
            )
        },
        settlement_affordance_route_counts={"r1": routes},
        gov_polities={},
        gov_office_seats={},
        gov_territory_rows=(),
        effective_regional_population_cap=lambda _rid: 1000,
    )


def _settlement(*kinds: str, region_text: str = "", routes: int = 0, **kwargs):
    st = SettlementState(
        settlement_id=kwargs.pop("settlement_id", "r1:s1"),
        region_id="r1",
        region_display_name="Region",
        status="active",
        resident_count=kwargs.pop("resident_count", 0),
        market_pull=kwargs.pop("market_pull", 0.0),
        founded_sim_year=kwargs.pop("founded_sim_year", None),
        site_slot=kwargs.pop("site_slot", 1),
        founding_reason=kwargs.pop("founding_reason", "organic"),
        local_geography_json=_geo(*kinds),
        **kwargs,
    )
    return _ctx(region_text, routes), st


class TestSettlementAffordances(unittest.TestCase):
    def assert_role_candidate(self, profile, role: str) -> None:
        self.assertIn(role, {candidate for candidate, _weight in profile.role_candidates})

    def test_port_city_affordance_from_harbor_river_and_fertile_hinterland(self) -> None:
        ctx, st = _settlement(
            "harbor",
            "river_mouth",
            "pasture",
            region_text="fertile coastal delta river port",
            routes=4,
        )
        profile = build_settlement_affordance_profile(ctx, st, year=1000)

        self.assertGreaterEqual(profile.water_access, 0.60)
        self.assertGreater(profile.trade_connectivity, 0.55)
        self.assertGreater(profile.agricultural_hinterland, 0.45)
        self.assert_role_candidate(profile, "major_port_city")
        self.assertTrue(profile.large_population_enablers)

    def test_affordance_roles_cover_common_geography_fixtures(self) -> None:
        fixtures = [
            (
                ("ford", "road_crossing"),
                "market road crossing",
                4,
                {"market_town", "toll_town"},
            ),
            (
                ("mountain_pass", "fortress"),
                "mountain border pass frontier",
                3,
                {"fortress_town"},
            ),
            (
                ("mine", "quarry"),
                "ore quarry arid poor farmland",
                1,
                {"extraction_town"},
            ),
            (
                ("oasis", "well", "road_crossing"),
                "dry desert caravan route",
                4,
                {"caravan_town"},
            ),
            (
                ("pasture", "spring"),
                "fertile plain weak trade",
                0,
                {"farming_village_cluster"},
            ),
            (
                ("spring", "sanctuary"),
                "remote sacred hills shrine",
                0,
                {"pilgrimage_village", "monastery"},
            ),
            (
                ("forest", "river"),
                "timber forest river coast",
                2,
                {"logging_town", "shipbuilding_settlement"},
            ),
            (
                ("marsh", "reed_bed"),
                "marsh reed wetland low connectivity refuge",
                0,
                {"fishing_reed_village", "refuge_settlement"},
            ),
        ]
        for kinds, region_text, routes, expected_roles in fixtures:
            with self.subTest(region_text=region_text):
                ctx, st = _settlement(*kinds, region_text=region_text, routes=routes)
                profile = build_settlement_affordance_profile(ctx, st, year=1000)
                candidates = {role for role, _weight in profile.role_candidates}
                self.assertTrue(candidates.intersection(expected_roles), candidates)

    def test_growth_invariant_caps_large_unexplained_population(self) -> None:
        ctx, st = _settlement("ridge", region_text="remote poor upland", routes=0)
        profile = build_settlement_affordance_profile(ctx, st, year=1000)

        self.assertFalse(profile.large_population_enablers)
        self.assertLess(growth_invariant_cap(profile, 1000), 500)

    def test_new_extra_settlement_backfill_cap_keeps_founder_year_tiny(self) -> None:
        ctx, st = _settlement(
            "pasture",
            region_text="fertile plain",
            founded_sim_year=1000,
            site_slot=2,
            founding_reason="birth spinoff",
        )
        profile = build_settlement_affordance_profile(ctx, st, year=1000)

        cap = new_settlement_backfill_cap(profile, st, year=1000, detailed_alive=4)
        self.assertIsNotNone(cap)
        self.assertLess(cap, 50)

    def test_extra_settlement_seed_can_remain_under_fifty_mixed_residents(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save = Path(td) / "save.sqlite"
            ctx = _ctx("fertile plain", routes=0)
            primary = SettlementState(
                settlement_id="r1:s1",
                region_id="r1",
                resident_count=12,
                market_pull=0.2,
                status="active",
                site_slot=1,
                local_geography_json=_geo("pasture", "spring"),
            )
            extra = SettlementState(
                settlement_id="r1:s2",
                region_id="r1",
                resident_count=4,
                market_pull=0.05,
                status="active",
                site_slot=2,
                founded_sim_year=1000,
                founding_reason="birth spinoff",
                local_geography_json=_geo("pasture"),
            )
            ctx.settlements_by_id = {primary.settlement_id: primary, extra.settlement_id: extra}
            ctx.next_person_id = 1
            ctx.count_alive_in_settlement = lambda sid: 4 if sid == "r1:s2" else 12
            ctx.mixed_population_count_in_settlement = (
                lambda sid: 4 if sid == "r1:s2" else 12
            )

            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                inserted = seed_nondetailed_from_active_settlements(
                    conn,
                    ctx,
                    year=1000,
                    population_scale=1.0,
                    start_person_id=1,
                )
                conn.commit()
                rows = conn.execute(
                    """
                    SELECT sl.settlement_id, COUNT(*) AS c
                    FROM simulation_people_nondetailed p
                    JOIN simulation_settlement_lookup sl
                      ON sl.settlement_key = p.current_settlement_key
                    WHERE p.is_alive = 1
                    GROUP BY sl.settlement_id
                    """
                ).fetchall()

        counts = {str(row["settlement_id"]): int(row["c"]) for row in rows}
        self.assertGreater(inserted, 0)
        self.assertLess(counts.get("r1:s2", 0) + 4, 50)


if __name__ == "__main__":
    unittest.main()
