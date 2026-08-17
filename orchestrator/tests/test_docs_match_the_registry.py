"""`docs/decision-inputs.md` status lines, checked against the frozen registry.

Two of them were wrong. `tech.choose` and `social.engineering` both said "not yet implemented"
while `faction.tech` and `faction.se` had been APPLIED for some time — the brain's choice
executes on both.

That is not a cosmetic slip. This document is what someone reads to decide *what to build next*:
it ranks surfaces by LLM fit and recommends an order. A surface marked unimplemented when it is
applied invites someone to build it a second time, and the same class of stale statement — a
count taken as current rather than re-derived — is what left na-yd4 claiming 19 instrumentable
surfaces when the real number was near zero.

The staleness survived because the section headings use working names that predate the frozen
registry, so a reader grepping for `faction.tech` finds nothing in this file. The headings now
carry their registry id, and this test keeps the two in agreement.
"""

from __future__ import annotations

import re
from pathlib import Path

from neural_amplifier.surfaces import ALL, APPLIED

DOC = Path(__file__).resolve().parents[2] / "docs" / "decision-inputs.md"

#: Section heading -> registry id. The headings are working names kept for continuity; this is
#: the mapping the document itself now states inline.
SECTIONS = {
    "base.production": "base.production",
    "tech.choose": "faction.tech",
    "social.engineering": "faction.se",
    "unit.move": "unit.move",
}


def sections() -> dict[str, str]:
    """{heading name: the Status paragraph that follows it}."""
    text = DOC.read_text()
    out: dict[str, str] = {}
    parts = re.split(r"^## \d+\. `([^`]+)`", text, flags=re.MULTILINE)
    for name, body in zip(parts[1::2], parts[2::2], strict=False):
        match = re.search(r"\*\*Status:\*\*(.+?)(?=\n\n)", body, flags=re.DOTALL)
        if match:
            out[name] = " ".join(match.group(1).split())
    return out


def test_every_documented_section_names_a_real_surface() -> None:
    """A heading naming a surface the registry does not have is a plan for a decision the game
    never asks about."""
    for heading, surface in SECTIONS.items():
        assert surface in ALL, f"{heading} -> {surface} is not in the frozen registry"


def test_an_applied_surface_is_never_documented_as_unimplemented() -> None:
    """The defect this file is named for.

    Someone reads this document to choose what to build next. A surface marked "not yet
    implemented" when its decide path is live invites the work to be done twice — and nothing
    else in the repository would contradict it, because the heading uses a name the registry
    does not know.
    """
    found = sections()
    for heading, surface in SECTIONS.items():
        if surface not in APPLIED:
            continue
        status = found.get(heading, "")
        assert status, f"{heading} has no Status line"
        assert "not yet implemented" not in status.lower(), (
            f"{heading} ({surface}) is APPLIED but documented as unimplemented"
        )
        # No positive wording is required, and the first draft of this test demanded the word
        # "applied" — which failed on base.production, whose status reads "first LLM-tier
        # surface (A1)". That is accurate and better prose than the assertion wanted. A test
        # that dictates phrasing to a document it does not own ends up editing the document to
        # suit itself, so this checks only for the claim that is false.


def test_the_headings_carry_their_registry_id() -> None:
    """The reason the staleness survived: a reader grepping `faction.tech` found nothing here.

    Renaming the sections would break every inbound link and every reference in the beads, so
    the ids are stated inline instead — which is what makes this file findable from the name
    everything else uses.
    """
    text = DOC.read_text()
    for heading, surface in SECTIONS.items():
        if heading == surface:
            continue
        assert f"**`{surface}`**" in text, f"{heading} does not state its registry id {surface}"
