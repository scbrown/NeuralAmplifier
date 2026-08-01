"""Per-surface tier policy — which surfaces the LLM is allowed to decide.

Read from ``surfaces.toml`` (or ``NA_SURFACES_CONFIG``). One toggle per surface id, plus a
``default`` for everything unlisted.

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
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .surfaces import ALL

#: Where to look when nothing is passed. Repo root, beside the justfile.
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "surfaces.toml"

#: Environment override, so a run can point at a different policy without editing the tree.
ENV_VAR = "NA_SURFACES_CONFIG"


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
        """
        if self.source is None or surface_id is None:
            return True
        return self.toggles.get(surface_id, self.default)


def load(path: Path | None = None) -> SurfacePolicy:
    """Read the policy, or return the no-opinion default when there is no file.

    Absent is not empty: a missing file means nothing has been configured, and everything is
    allowed. An empty ``[surfaces]`` table with ``default = false`` means someone deliberately
    turned everything off, and that must be honoured.
    """
    if path is None:
        override = os.environ.get(ENV_VAR)
        path = Path(override) if override else DEFAULT_PATH
    if not path.exists():
        return SurfacePolicy()

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{path} is not valid TOML: {exc}") from exc

    table = data.get("surfaces", {})
    if not isinstance(table, dict):
        raise PolicyError(f"{path}: [surfaces] must be a table of id = true/false")

    unknown = sorted(set(table) - ALL)
    if unknown:
        raise PolicyError(
            f"{path}: not in the frozen surface registry: {', '.join(unknown)}. "
            "A toggle on an id nothing emits is a setting that appears to work and does not."
        )

    bad = sorted(k for k, v in table.items() if not isinstance(v, bool))
    if bad:
        raise PolicyError(f"{path}: these must be true or false: {', '.join(bad)}")

    return SurfacePolicy(
        toggles=dict(table),
        default=bool(data.get("default", False)),
        source=path,
    )
