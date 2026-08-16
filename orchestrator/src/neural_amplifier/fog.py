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

    #: ``False`` when the gate could not do its job on this world view, so its
    #: verdict is "we could not have seen a leak" rather than "there was none".
    #: Two causes: the adapter sent no ``contacts`` list, in which case nothing
    #: is removed because we cannot tell a legitimate delta from a leaked one;
    #: or a delta named parties in a shape this gate cannot read, in which case
    #: that delta *is* removed and the run still must not be reported as
    #: fog-clean. Absence of evidence, recorded as such.
    enforced: bool = True


def parties(delta: dict[str, Any]) -> set[str] | None:
    """Factions named as participants in a delta, or ``None`` if unreadable.

    Only ``parties`` counts. A delta *about* a faction (a score change, say) is
    not a private exchange between two others, so widening this to every
    faction-shaped field would redact ordinary public information.

    Three outcomes, and collapsing the last two is the bug this replaced. An
    absent or null ``parties`` is the delta declining to name anyone — public
    news, and the empty set compares clean against any contact list. A readable
    list of names is the ordinary case. Anything else — a bare string, numeric
    faction ids, a list with a non-string in it — is an adapter *naming*
    parties in a shape we cannot compare, and it returns ``None``. Filtering
    the unreadable entries out instead would turn ``["HIVE", 7]`` into a subset
    of every contact list, so the delta would pass as public and the gate would
    report itself enforced: a type drift silently disabling the control while
    the numbers stay green.
    """
    raw = delta.get("parties")
    if raw is None:
        return set()
    if not isinstance(raw, list) or any(not isinstance(p, str) for p in raw):
        return None
    return set(raw)


def redact(world_view: WorldView) -> Redaction:
    """Drop deltas naming a faction we have not contacted.

    A faction always knows about its own dealings, so the viewing faction is
    implicitly contacted. A delta naming *no* parties is public news and is
    left alone — this gate exists to hide private pacts, not to blind the brain.

    A delta whose parties are unreadable is dropped and the world view is
    marked unenforced. Dropping is the safe direction: we cannot show it is
    public, and the whole point of the second line is that the adapter may be
    wrong. Marking it unenforced is the honest one: a clean run whose input the
    gate could not parse has not been gated, and saying so is what turns an
    adapter type drift into a number rather than a suspiciously good game.
    """
    deltas = world_view.deltas
    if world_view.contacts is None:
        return Redaction(world_view=world_view, enforced=False)
    if not deltas:
        return Redaction(world_view=world_view)

    known = set(world_view.contacts) | {world_view.faction}
    named = [parties(d) for d in deltas]
    readable = all(p is not None for p in named)
    kept = [d for d, p in zip(deltas, named, strict=True) if p is not None and p <= known]
    removed = len(deltas) - len(kept)
    if not removed:
        return Redaction(world_view=world_view, enforced=readable)

    return Redaction(
        world_view=world_view.model_copy(update={"deltas": kept}),
        removed=removed,
        enforced=readable,
    )
