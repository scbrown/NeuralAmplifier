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
        # Observed, NOT applied, and unusual among the 21: its deterministic tier already
        # existed. select_build threads a retool category through the production chooser and
        # push_item penalises a category crossing, so what was missing was never an answer —
        # it was the record (na-lnv). Instrumented behind `na_retool_observe`, with an
        # `observe-retool` probe, because a retool decision fires only when a base is mid-build
        # and the chooser wants a different category, which is far too rare to catch by playing.
        "base.retool",
        # The first of na-yd4's 27 — the bucket that already HAS a native AI path, so invariant
        # 9 holds from the first record and nothing needs building before one is safe. Picked on
        # decision-inputs.md's rule (low frequency, high stakes): `consider_staple`'s gate opens
        # rarely, and when it does the choice trades a lasting diplomatic and psych cost for
        # immediate order.
        #
        # Observed, NOT applied, like every other entry that arrives here: `na_staple_observe`
        # records what consider_staple chose and nothing applies a brain's answer.
        "base.staple",
        # Two more from na-yd4's 27, taken together because they live in one function and fire
        # on one cadence (`na_endgame_observe`). Both are AI-only — the engine gates each block
        # on `!is_human` — very low frequency, and very high stakes: cornering the energy market
        # is a move toward economic victory.
        #
        # `council.call` is observed as a STATE TRANSITION rather than a return value, because
        # `call_council` decides internally and hands back nothing useful. Convened off before
        # the call and on after IS the engine's answer; inferring it from eligibility would be a
        # guess wearing the same clothes.
        "econ.corner_market",
        "council.call",
        # The fourth from na-yd4's 27, and the first here with a genuinely ENUMERABLE action
        # space: the orbital chooser picks among exactly four satellite types, and each one's
        # availability is an engine predicate we can ask directly (has_tech for the
        # prerequisite, satellite_count against satellite_goal for whether the faction still
        # wants one). Most surfaces in this bucket are binary or open-ended.
        #
        # `available` on an option is its own eligibility, NOT a prediction of what
        # find_satellite will pick — the chooser also weighs an aerospace-complex prerequisite
        # and a randomised defence bias, so an option can be available and go unchosen.
        "base.satellite",
        # The fifth from na-yd4's 27 and the richest action space in the set: every buildable
        # secret project with the engine's OWN `facility_score` under this base's governor
        # weights, plus how many of the faction's bases are already building it.
        #
        # The scores are forwarded from the chooser's own Wgov rather than reconstructed. A
        # local reconstruction would rank options differently from the engine for reasons no
        # reader could see, while carrying the engine's authority.
        "base.project",
        # The sixth from na-yd4's 27. Its action space is what the TARGET holds and we do not —
        # deliberately NOT the research menu `faction.tech` uses. Those are different sets and
        # mostly disjoint, so reusing the research writer would have offered a plausible list of
        # the wrong options.
        #
        # Records both callers and keeps them apart: a probe team's deliberate operation and the
        # acquisition that comes free with a base capture. Same chooser, different provenance.
        "faction.tech_steal",
        # The seventh from na-yd4's 27, and the first RELATIVE one. move_upkeep assigns the
        # defender tier by PERCENTILE across the faction's whole base list, so the same base
        # with the same score is a different tier in a bigger empire. The record carries the
        # engine's priority score AND the cohort size, because the tier alone cannot be
        # compared across turns — which is the thing na-6db has to do.
        "base.defend_goal",
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
