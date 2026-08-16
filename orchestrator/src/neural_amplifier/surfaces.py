"""The frozen surface-ID registry.

Every decision hook emits one of these IDs. Coverage reports key on them, so the
scheme is **frozen**: renaming an ID invalidates every previously recorded run.
Adding a new ID is fine; changing or removing one is a breaking change.

The inventory these come from — including which have an engine AI path and which
are dialog-only — is ``docs/game-surface.md``.

Naming: ``<domain>.<decision>``, lowercase, underscore-separated. Domains are
engine-independent on purpose, so coverage can be compared across Thinker and
GLSMAC (``docs/observability.md`` §9.1).
"""

from __future__ import annotations

from typing import Final

Scope = str  # "turn" | "unit" | "base" — mirrors the contract's scope field.

#: Base and economy decisions. Contract scope: ``base``.
BASE: Final[frozenset[str]] = frozenset(
    {
        "base.production",
        "base.queue",
        "base.hurry",
        "base.workers",
        "base.specialists",
        "base.psych",
        "base.facility",
        "base.project",
        "base.satellite",
        "base.staple",
        "base.drone_riot",
        "base.growth",
        "base.defend_goal",
        "base.support",
        "base.capture",
        "base.hq_relocate",
        "base.name",
        "base.abandon",
        "base.governor_config",
        "base.hq_escape",
        "base.disband",
        "base.retool",
        "econ.energy_sliders",
        "econ.commerce",
        "econ.corner_market",
    }
)

#: Unit, military and terraforming decisions. Contract scope: ``unit``.
UNIT: Final[frozenset[str]] = frozenset(
    {
        "unit.turn_order",
        "unit.dispatch",
        "unit.move",
        "unit.attack",
        "unit.design",
        "unit.upgrade",
        "unit.retire",
        "former.item",
        "former.terraform",
        "colony.found",
        "colony.sea",
        "probe.action",
        "transport.move",
        "air.ops",
        "air.fuel",
        "unit.airdrop",
        "unit.artillery",
        "unit.retreat",
        "crawler.convoy",
        "native.move",
        "unit.monolith",
        "unit.pod",
        "unit.artifact",
        "unit.psi_gate",
        "unit.planet_buster",
        "unit.odp_attack",
        "unit.tectonic",
        "unit.fungal",
        "unit.patrol",
        "unit.disband",
        "unit.gift",
        "unit.obliterate",
    }
)

#: Faction-level decisions. Contract scope: ``turn``.
FACTION: Final[frozenset[str]] = frozenset(
    {
        "faction.tech",
        "faction.tech_steal",
        "faction.se",
        "faction.agenda",
        "diplo.declare_war",
        "diplo.treaty_break",
        "diplo.atrocity",
        "diplo.ai_to_ai",
        "diplo.tech_trade",
        "diplo.energy_loan",
        "diplo.base_swap",
        "diplo.treaty_offer",
        "diplo.surrender",
        "diplo.tribute",
        "diplo.map_trade",
        "council.call",
        "council.vote",
        "council.buy_vote",
        "victory.diplomatic",
        "victory.conquest",
    }
)

#: Every known surface ID.
ALL: Final[frozenset[str]] = BASE | UNIT | FACTION

#: Surfaces with **no** engine AI path — reachable only through a human dialog.
#: The LLM tier must own these outright, or they are fork work. See
#: ``docs/game-surface.md`` §4.
NO_AI_PATH: Final[frozenset[str]] = frozenset(
    {
        "base.abandon",
        "base.governor_config",
        "base.hq_escape",
        "base.disband",
        "base.retool",
        "unit.odp_attack",
        "unit.tectonic",
        "unit.fungal",
        "unit.patrol",
        "unit.disband",
        "unit.gift",
        "unit.obliterate",
        "diplo.tech_trade",
        "diplo.energy_loan",
        "diplo.base_swap",
        "diplo.treaty_offer",
        "diplo.surrender",
        "diplo.tribute",
        "diplo.map_trade",
        "council.vote",
        "council.buy_vote",
    }
)


#: Surfaces an adapter reports — the brain sees the decision and a record is written, but the
#: engine's own choice still executes.
#:
#: Observing is a prerequisite for coverage, not coverage itself. A surface here is one we can
#: *watch*; the brain has no influence over it whatsoever.
OBSERVED: Final[frozenset[str]] = frozenset(
    {
        "base.production",
        "faction.tech",
        "faction.se",
        "base.hurry",
        # Observed, NOT applied — the first of the 27 ready surfaces (na-yd4), chosen by
        # decision-inputs.md's own rule: low frequency, high stakes. It fires once per
        # faction-turn and the ratio it sets divides every base's energy for that whole turn,
        # so it reaches further per decision than anything else in that bucket.
        #
        # Deliberately absent from APPLIED. The adapter records what mod_allocate_energy chose
        # and what else was legal; nothing applies a brain's answer yet, and adding it here
        # would move the coverage number for work that has not been done.
        "econ.energy_sliders",
    }
)

#: Surfaces where the brain's choice actually executes — validated against the engine's own
#: availability tests, so an illegal order is rejected rather than applied.
#:
#: **This is the coverage number.** A surface is not covered until the decision can be applied:
#: until then the LLM tier has no effect on the game, and counting observation as coverage
#: overstates it fourfold today. Every applied surface is necessarily observed.
#:
#: The adapter is the authority on this list: a surface belongs here when `src/neural.h` in
#: `scbrown/thinker` exports a `na_decide_*` entry point for it AND the engine call site
#: assigns the return. `na_observe_*` alone does not count — it emits a record and the engine
#: still chooses, and listing such a surface would make this number claim the LLM tier affects
#: a game it cannot yet touch.
#:
#: - `base.production` — `na_decide_base_production`, assigned at `base.cpp`.
#: - `faction.tech` — `na_decide_faction_tech`, assigned at `tech.cpp`.
#: - `faction.se` — `na_decide_faction_se`, in/out params read at `faction.cpp`.
#: - `base.hurry` — `na_decide_base_hurry`, replacing the call at `build.cpp`.
#:
#: Four of the five observed surfaces apply. It briefly equalled OBSERVED, and then
#: `econ.energy_sliders` was instrumented observation-only and the gap reopened — which is the
#: rule working, not a regression. A newly instrumented surface starts observation-only and
#: must not be added here until its own decide path lands, or this number goes back to claiming
#: influence the brain does not have.
APPLIED: Final[frozenset[str]] = frozenset(
    {
        "base.production",
        "faction.tech",
        "faction.se",
        "base.hurry",
    }
)


def coverage() -> dict[str, int]:
    """The counts `docs/game-surface.md` §2.5 and the README quote — computed, not typed.

    Two numbers, and conflating them is the mistake this replaced. ``applied`` is how much of
    the game the brain can actually *decide*; ``observed`` is how much it can see. The gap
    between them is real work — an observed surface still needs its apply path built and
    validated — and reporting only the larger one claims influence the brain does not have.

    The three remaining buckets **partition** what is left, which the prose version did not: a
    ``unit``-scope surface can also have no native AI path (seven do), so counting them as
    separate piles double-counted those seven and made the remainder look smaller than it is.

    ``needs_tier_first`` has no native answer to fall back on, so an LLM there breaks invariant
    9 until the deterministic tier is built; ``ready`` already has a safe fallback.
    """
    remaining = ALL - OBSERVED
    needs_tier_first = remaining & NO_AI_PATH
    volume_bound = {s for s in remaining - NO_AI_PATH if scope_for(s) == "unit"}
    return {
        "total": len(ALL),
        "applied": len(APPLIED),
        "observed": len(OBSERVED),
        "observed_not_applied": len(OBSERVED - APPLIED),
        "remaining": len(remaining),
        "needs_tier_first": len(needs_tier_first),
        "volume_bound": len(volume_bound),
        "ready": len(remaining - needs_tier_first - volume_bound),
    }


def is_known(surface_id: str) -> bool:
    """Whether ``surface_id`` is in the frozen registry."""
    return surface_id in ALL


def scope_for(surface_id: str) -> Scope | None:
    """The contract ``scope`` a surface belongs to, or ``None`` if unknown."""
    if surface_id in BASE:
        return "base"
    if surface_id in UNIT:
        return "unit"
    if surface_id in FACTION:
        return "turn"
    return None
