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


#: Surfaces that emit a decision record today. Kept here rather than only in prose, because
#: coverage stated only in a document is coverage nobody can check — and it had already drifted:
#: ``docs/game-surface.md`` described the remaining work as three buckets that silently
#: overlapped by seven, understating the immediately-instrumentable set by a third.
#:
#: A surface belongs here once an adapter emits its ``surface_id`` with an engine-authoritative
#: action space and a side-effect-free probe. Observation-only counts: the point of the count is
#: how much of the game we can *see*, and closing the loop is tracked per surface elsewhere.
INSTRUMENTED: Final[frozenset[str]] = frozenset(
    {
        "base.production",
        "faction.tech",
        "faction.se",
        "base.hurry",
    }
)


def coverage() -> dict[str, int]:
    """The counts `docs/game-surface.md` §2.5 and the README quote — computed, not typed.

    The three buckets below **partition** what is left, which the prose version did not. A
    ``unit``-scope surface can also have no native AI path (seven do), so counting "all unit
    surfaces" and "all no-AI-path surfaces" as separate piles double-counts those seven and
    makes the remainder look smaller than it is.

    The distinction is the whole planning question. ``needs_tier_first`` has no native answer to
    fall back on, so an LLM there breaks invariant 9 until the deterministic tier is built;
    ``ready`` already has a safe fallback and can be instrumented incrementally today.
    """
    remaining = ALL - INSTRUMENTED
    needs_tier_first = remaining & NO_AI_PATH
    volume_bound = {s for s in remaining - NO_AI_PATH if scope_for(s) == "unit"}
    return {
        "total": len(ALL),
        "instrumented": len(INSTRUMENTED),
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
