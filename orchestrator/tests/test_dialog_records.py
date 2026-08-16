"""In-game dialog observations, pinned against the adapter — invariant 7, na-4lr.

`popp` is the engine's dialog function, and the fork calls it through a pointer, so writing a
wrapper into that pointer intercepts every dialog Thinker raises at once. What comes out is
*not* a world view: the hook cannot build an action space, because a dialog's buttons live in a
game text file keyed by label and that is data this project deliberately does not ship
(invariant 8). It emits the compact form the `na_verify_*` divergence records already use.

These tests are a **pin**, transcribed from the emitter in `thinker/src/neural.cpp`, the same
way `test_adapter_contract.py` pins the world views. They cannot catch the adapter changing —
only the contract or the registry changing underneath it, which is the direction that breaks
silently.

The one they exist for most: a `surface_id` in the C++ table that is not in the frozen registry
is dropped by the orchestrator with no error anywhere, so a typo there costs the surface and
looks like a dialog that never fired.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from neural_amplifier.surfaces import ALL, APPLIED, OBSERVED

REPO = Path(__file__).resolve().parents[2]


def _load_harvest() -> Any:
    spec = importlib.util.spec_from_file_location(
        "harvest_world_views", REPO / "scripts" / "harvest-world-views.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load_harvest()

#: `NaDialogTable` in `thinker/src/neural.cpp`, transcribed. `None` for `file` is the table's
#: nullptr — the fork passes a runtime `ScriptFile` there, so those entries match on label
#: alone. Chrome entries carry no surface: the main menu is not a decision.
DIALOG_TABLE = [
    ("modmenu", "MAINMENU", None),
    ("modmenu", "GAMEMENU", None),
    ("modmenu", "STATS", None),
    ("modmenu", "GENERIC", None),
    ("modmenu", "NERVESTAPLE2", "base.staple"),
    (None, "CORNERFOILED", "econ.corner_market"),
    (None, "CORNERTHEMFOIL", "econ.corner_market"),
    (None, "CORNERTHEMFOILED", "econ.corner_market"),
    (None, "SURVIVEPROJECT", "base.project"),
    (None, "HALTPROJECT", "base.project"),
    (None, "SEIZEPROJECT", "base.project"),
    (None, "LOSEPROJECT", "base.project"),
    ("modmenu", "SPYFOUND", "probe.action"),
    ("modmenu", "SPYLOST", "probe.action"),
]

#: `na_write_dialog`, for a dialog the table knows.
MAPPED = {
    "record": "dialog",
    "engine": "thinker",
    "turn": 42,
    "faction_id": 1,
    "dialog_file": "modmenu",
    "dialog_label": "NERVESTAPLE2",
    "surface_id": "base.staple",
    "native_choice": 0,
    "mapped": True,
}

#: The same emitter for a dialog the table does not know — no `surface_id` at all.
UNMAPPED = {
    "record": "dialog",
    "engine": "thinker",
    "turn": 42,
    "faction_id": 1,
    "dialog_file": "script",
    "dialog_label": "SOMEEVENTNOBODYMAPPED",
    "native_choice": 1,
    "mapped": False,
}


def test_every_mapped_surface_is_in_the_frozen_registry() -> None:
    """The failure this pins costs a surface and announces nothing.

    The orchestrator keys coverage on the frozen registry. A `surface_id` in the C++ table that
    is not in it — a typo, or a name someone shortened — is simply unknown: no exception, no
    warning, and the dialog looks like one that never fired. Since the table is written by hand
    in another repository, nothing else checks the spelling.
    """
    for _file, label, surface in DIALOG_TABLE:
        if surface is None:
            continue
        assert surface in ALL, f"{label} -> {surface} is not a known surface"


def test_chrome_carries_no_surface_and_decisions_all_do() -> None:
    """The two kinds are not allowed to blur.

    Chrome is menu plumbing — na-4lr is explicit that automating it is throwaway work, and it
    is never recorded. Giving it a surface_id would put the main menu into coverage, which
    opens on every launch.
    """
    chrome = {label for _f, label, s in DIALOG_TABLE if s is None}
    assert chrome == {"MAINMENU", "GAMEMENU", "STATS", "GENERIC"}
    for _file, label, surface in DIALOG_TABLE:
        assert (label in chrome) == (surface is None), label


def test_labels_are_unique_per_file() -> None:
    """Lookup returns the FIRST match, so a duplicated key silently shadows the later entry."""
    keys = [(f, label) for f, label, _s in DIALOG_TABLE]
    assert len(keys) == len(set(keys))


def test_a_dialog_record_is_not_harvested_as_a_world_view() -> None:
    """It carries a `surface_id`, which is exactly what makes this worth asserting.

    `harvest-world-views.py` picks a capture by `surface_id` plus the contract's four required
    fields, precisely so a compact record cannot be mistaken for one. A dialog record has
    `engine` and `turn` but no `scope` and no `faction`, so it is skipped — and if that ever
    changed, dialog records would start overwriting real captures of the surfaces they name.
    """
    assert harvest._capture(MAPPED) is None
    assert harvest._capture(UNMAPPED) is None


def test_a_dialog_record_does_not_claim_a_tier_or_an_applied() -> None:
    """The same rule the divergence and audit records follow: this is not a decision.

    A dialog observation says the engine showed something and what came back. Nobody consulted
    the brain, so claiming a tier would put one on a record it never held, and an `applied`
    would make it count as a decision that ran.
    """
    for record in (MAPPED, UNMAPPED):
        for key in ("tier", "applied", "action_space", "applied_item"):
            assert key not in record, key


def test_an_unmapped_dialog_says_so_rather_than_guessing() -> None:
    """The inventory is meant to be discovered from a real game, not invented in the table.

    Every entry in the C++ table is a (file, label) pair that appears in the fork's own source.
    Filling it out from memory would produce a map that matches nothing while looking like
    coverage. So an unrecognised dialog is still recorded, flagged, and carries no surface_id —
    a record that names a surface it is not sure about would be worse than no record.
    """
    assert UNMAPPED["mapped"] is False
    assert "surface_id" not in UNMAPPED
    assert MAPPED["mapped"] is True


#: Dialog-mapped surfaces that ALSO have an adapter hook of their own, with the hook that earns
#: it. Their place in OBSERVED comes from that hook, never from the dialog plane.
#:
#: `base.staple` is the case that created this list. The dialog table maps NERVESTAPLE2 — the
#: path a HUMAN takes to staple — while `na_staple_observe` instruments `consider_staple`, the
#: path the AI takes. Two routes to one surface, and only the second one is coverage.
INDEPENDENTLY_INSTRUMENTED = {
    "base.staple": "na_staple_observe, consider_staple (na-yd4)",
    # Same shape, and it caught this one the turn it was introduced. The dialog table maps
    # CORNERFOILED / CORNERTHEMFOIL — the NOTIFICATIONS that someone's corner attempt
    # succeeded or was foiled — while `na_endgame_observe` instruments the decision itself in
    # mod_faction_upkeep. A notice that a thing happened is not the choice to do it.
    "econ.corner_market": "na_endgame_observe, mod_faction_upkeep (na-yd4)",
    # Third time, same shape, and by now the pattern is the point: the dialog table maps
    # SURVIVEPROJECT / HALTPROJECT / SEIZEPROJECT / LOSEPROJECT — what happened to a project
    # already under way, usually someone else's — while `na_project_observe` instruments
    # find_project, the choice to start one. Outcome and decision are different surfaces
    # wearing one id, and every entry on this list so far is that same split.
    "base.project": "na_project_observe, find_project (na-yd4)",
}


def test_observing_a_dialog_does_not_move_coverage() -> None:
    """A dialog record is not coverage, and no dialog surface may reach OBSERVED on its own.

    The hook has never been seen firing against a real game. `surfaces.py` exists to stop
    exactly this kind of work from moving the number.

    Stated as a bounded exception rather than a blanket ban, because a blanket ban became FALSE
    the moment `base.staple` was instrumented through `consider_staple` — a different, real
    hook that earns its place. The looser assertion would have been to delete this test; the
    honest one names which surfaces are allowed through and why, so a NEW dialog surface
    appearing in OBSERVED still fails here.
    """
    mapped = {s for _f, _label, s in DIALOG_TABLE if s}
    unearned = (mapped & OBSERVED) - set(INDEPENDENTLY_INSTRUMENTED)
    assert not unearned, f"reached OBSERVED with only a dialog hook: {sorted(unearned)}"
    assert not (mapped & APPLIED)


def test_the_exception_list_does_not_outlive_its_reason() -> None:
    """An allowlist nobody prunes becomes a permanent hole.

    Every entry must still be both dialog-mapped and in OBSERVED. A surface that left OBSERVED,
    or that is no longer in the dialog table, has no business granting an exception to a rule it
    is not subject to.
    """
    mapped = {s for _f, _label, s in DIALOG_TABLE if s}
    for surface, why in INDEPENDENTLY_INSTRUMENTED.items():
        assert surface in mapped, f"{surface} is not dialog-mapped; drop the exception"
        assert surface in OBSERVED, f"{surface} left OBSERVED; drop the exception"
        assert why.strip(), surface


def test_the_engines_answer_is_recorded() -> None:
    """`popp` returns the button index, and that is the baseline.

    Same reason `base.retool` records `native_choice`: a surface whose engine-side answer was
    never written down cannot be compared against a brain's later. It is also the evidence that
    nothing was suppressed — a record exists precisely because the dialog was allowed to run.
    """
    for record in (MAPPED, UNMAPPED):
        assert isinstance(record["native_choice"], int)
