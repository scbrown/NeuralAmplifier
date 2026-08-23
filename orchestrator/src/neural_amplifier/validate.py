"""Action-space adherence — the anti-hallucination guarantee.

VISION §4: Claude picks from the engine-supplied ``action_space`` and never
invents an action. That makes an out-of-space ``action_id`` a **broken
invariant**, not a warning: the harness asserts exactly zero
(``docs/observability.md`` §5.5).

Filtering here is belt-and-suspenders — the engine validates too — but doing it
in the orchestrator means the violation is *counted*, which is what makes it
detectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import Choice, Orders, WorldView


@dataclass
class Validation:
    """The result of checking orders against a world view."""

    kept: list[Choice] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    #: Carried through untouched from the brain's answer. Validation rules on ACTIONS; a
    #: citation is not an action and is not something this layer can judge. Dropping it here
    #: made every downstream citation look absent, so the guard reported "grounding unread"
    #: on decisions that had cited correctly.
    cited: list[str] = field(default_factory=list)

    @property
    def adherent(self) -> bool:
        """True when every returned action_id was legal. Should always hold."""
        return not self.unknown

    def orders(
        self,
        notes: str | None = None,
        degraded: bool = False,
        usage: dict[str, int] | None = None,
    ) -> Orders:
        """The validated orders, REBUILT — which is why `usage` has to be handed back in.

        This constructs a fresh `Orders` from the kept choices, so anything the brain attached to
        the original and did not put in a choice is dropped here. That silently zeroed the token
        accounting on every decision: the brain filled `usage`, validation rebuilt the object
        without it, and the record recorded 0/0 having made a real paid call.
        """
        return Orders(
            choices=self.kept, notes=notes, degraded=degraded, cited=self.cited, usage=usage
        )


def validate(orders: Orders, world_view: WorldView) -> Validation:
    """Drop choices the world view never offered, and repeated ones.

    Order is preserved — the adapter applies choices in sequence, so reordering
    would silently change what the turn does.
    """
    legal = world_view.action_ids()
    result = Validation(cited=list(orders.cited))
    seen: set[str] = set()

    for choice in orders.choices:
        if choice.action_id not in legal:
            result.unknown.append(choice.action_id)
            continue
        if choice.action_id in seen:
            result.duplicates.append(choice.action_id)
            continue
        seen.add(choice.action_id)
        result.kept.append(choice)

    return result
