"""Fog gating for the foreign-diplomacy feed — ``docs/headless-harness.md`` §4.2.

Thinker's `foreign_treaty_popup` routes *every* foreign treaty change through
`mod_NetMsg_pop`, including pacts between factions our faction has never met.
Handed straight to the brain that is an information cheat wearing a feature's
clothes, and worse, an invisible one: the run completes, the decisions look
sharp, and nothing in the log says why.

The adapter is meant to filter before it builds the world view. This is the
second line — a guard the orchestrator applies anyway, because the adapter is a
thin DLL under Wine and "we'll remember to filter it there" is not a control.
Redactions are counted onto the decision record, so an adapter that starts
leaking shows up as a number rather than as a suspiciously good game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract import WorldView


@dataclass(frozen=True)
class Redaction:
    """What the gate removed, and whether it could act at all."""

    world_view: WorldView
    removed: int = 0

    #: ``False`` when the adapter sent no ``contacts`` list. Nothing is removed
    #: — we cannot tell a legitimate delta from a leaked one — but the run must
    #: not be reported as fog-clean. Absence of evidence, recorded as such.
    enforced: bool = True


def parties(delta: dict[str, Any]) -> set[str]:
    """Factions named as participants in a delta.

    Only ``parties`` counts. A delta *about* a faction (a score change, say) is
    not a private exchange between two others, so widening this to every
    faction-shaped field would redact ordinary public information.
    """
    raw = delta.get("parties")
    if not isinstance(raw, list):
        return set()
    return {p for p in raw if isinstance(p, str)}


def redact(world_view: WorldView) -> Redaction:
    """Drop deltas naming a faction we have not contacted.

    A faction always knows about its own dealings, so the viewing faction is
    implicitly contacted. A delta naming *no* parties is public news and is
    left alone — this gate exists to hide private pacts, not to blind the brain.
    """
    deltas = world_view.deltas
    if world_view.contacts is None:
        return Redaction(world_view=world_view, enforced=False)
    if not deltas:
        return Redaction(world_view=world_view)

    known = set(world_view.contacts) | {world_view.faction}
    kept = [d for d in deltas if parties(d) <= known]
    removed = len(deltas) - len(kept)
    if not removed:
        return Redaction(world_view=world_view)

    return Redaction(world_view=world_view.model_copy(update={"deltas": kept}), removed=removed)
