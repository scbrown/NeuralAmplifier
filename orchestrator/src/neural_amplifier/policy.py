"""Per-surface tier policy — which surfaces the LLM is allowed to decide.

The ``[surfaces]`` section of ``na.toml`` (see :mod:`.config`). One toggle per surface id, plus
``surface_default`` for everything unlisted.

The reason this is a file and not a constant: turning a surface off is an *operational* choice
that has to be reversible without a deploy, and it is how a surface gets rolled out one at a
time — instrument it, watch it observe, then let it decide. A surface that is off is decided by
the engine, exactly as it was before we arrived.

**Off is not degraded.** A degraded decision is one the brain was supposed to make and could
not; a disabled one is a decision the brain was never asked for. Collapsing them would put a
deliberate configuration into ``degrade_rate``, which is the number that catches a run where the
brain was silently absent — the one metric that must not be able to lie in that direction.

**An unknown id is refused, not ignored.** A typo'd surface in this file would otherwise be a
toggle that appears to be set and does nothing, and the failure surfaces only as "why is the
brain still deciding that".

**A surface with no native AI path cannot be toggled on at all.** ``surfaces.NO_AI_PATH`` is the
set the engine never decides for itself, so there is nothing to fall back *to* — and invariant 9
(degrade safely) is a precondition, not an aspiration: a slow or broken model on one of those
surfaces stalls or corrupts a turn instead of quietly reverting to a competent default. That is
a fact about the engine rather than an opinion about a run, so it is not something a config file
gets a vote on. See ``na-2mn``: the deterministic tier is built first, in the fork, and a surface
leaves ``NO_AI_PATH`` when it has an answer to degrade to — at which point this gate releases
itself with no change here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .surfaces import ALL, NO_AI_PATH


class PolicyError(ValueError):
    """A policy file that cannot be trusted. Raised at load, never at decision time."""


@dataclass(frozen=True)
class SurfacePolicy:
    """Which surfaces the LLM tier owns.

    Permissive when *no policy is configured at all* — absent means nobody has expressed an
    opinion, and the orchestrator's behaviour before this module existed was to decide
    everything it was handed. A file that exists and omits a surface is a different thing: it
    has an opinion, and ``default`` is it.
    """

    #: Explicit per-surface toggles. Both directions are kept: an id set to ``false`` under a
    #: ``default = true`` policy must stay off, so storing only the enabled ids would silently
    #: re-enable everything anyone had deliberately switched off.
    toggles: dict[str, bool] = field(default_factory=dict)
    default: bool = True
    #: None when no file was found, which :meth:`allows` treats as "no opinion".
    source: Path | None = None

    def allows(self, surface_id: str | None) -> bool:
        """Whether the brain may decide this surface.

        A world view with no ``surface_id`` is allowed through. The adapter is supposed to stamp
        one (invariant 5) and a missing one is already counted as a coverage gap; refusing to
        decide it here would turn an instrumentation bug into a silently deterministic game.

        A ``NO_AI_PATH`` surface is refused unconditionally, ahead of every other branch — no
        file, a permissive ``surface_default``, and an explicit toggle all lose to it. Load-time
        refusal already catches the explicit ``true``; this catches the two paths that never pass
        through the table at all, where the brain would acquire a fallback-less surface because
        nobody wrote its name down.
        """
        if surface_id is not None and surface_id in NO_AI_PATH:
            return False
        if self.source is None or surface_id is None:
            return True
        return self.toggles.get(surface_id, self.default)


def parse_surfaces(
    table: dict[str, object], default: object = None, source: Path | None = None
) -> SurfacePolicy:
    """Build a policy from the ``[surfaces]`` table of an already-parsed config.

    ``source`` is what tells :meth:`SurfacePolicy.allows` that somebody has an opinion at all.
    Absent is not empty: no config file means nothing is configured and everything is decided,
    which is how the orchestrator behaved before any of this existed. A file that exists and
    lists nothing, with ``surface_default = false``, is someone deliberately turning everything
    off and must be honoured.
    """
    unknown = sorted(set(table) - ALL)
    if unknown:
        raise PolicyError(
            f"[surfaces]: not in the frozen surface registry: {', '.join(unknown)}. "
            "A toggle on an id nothing emits is a setting that appears to work and does not."
        )
    bad = sorted(k for k, v in table.items() if not isinstance(v, bool))
    if bad:
        raise PolicyError(f"[surfaces]: these must be true or false: {', '.join(bad)}")

    fallbackless = sorted(k for k, v in table.items() if v is True and k in NO_AI_PATH)
    if fallbackless:
        raise PolicyError(
            f"[surfaces]: no native AI path, so the LLM tier cannot own these yet: "
            f"{', '.join(fallbackless)}. The engine never decides them, so there is no safe "
            "fallback for a slow or broken model to degrade to (invariant 9). Build the "
            "deterministic tier first — na-2mn."
        )

    return SurfacePolicy(
        toggles={k: bool(v) for k, v in table.items()},
        default=bool(default) if default is not None else False,
        source=source,
    )
