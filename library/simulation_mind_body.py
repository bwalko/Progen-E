"""Annual mind/body sync, elder trait drift, and attractiveness refresh."""

from __future__ import annotations

import random
from dataclasses import replace
from typing import TYPE_CHECKING

from library.mind_body import (
    attractiveness_01,
    ensure_full_mind_body,
    maybe_apply_elder_mind_body_year,
    mind_body_aging_rng_seed,
)

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


def simulation_mind_body_annual_tick(ctx: "SimulationContext", year: int) -> None:
    """Before careers: sync mind_body with genome keys, elder nudges, attractiveness."""
    y = int(year)
    salt = int(ctx.placename_rng_salt)
    for rec in ctx.iter_current_people(sorted_by_id=True):
        p = rec.person
        mb = maybe_apply_elder_mind_body_year(
            replace(p, mind_body=ensure_full_mind_body(p)),
            year=y,
            rng=random.Random(mind_body_aging_rng_seed(y, rec.person_id, salt)),
        )
        p2 = replace(p, mind_body=mb)
        rec.person = replace(
            p2,
            attractiveness_01=round(attractiveness_01(p2, y), 5),
        )
