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
