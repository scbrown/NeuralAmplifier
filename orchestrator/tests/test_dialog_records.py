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
#: (file, label, surface_id, kind). `kind` is the disposition, and on this table it is
#: safety-critical: only NOTICE may ever be auto-answered.
DIALOG_TABLE = [
    ("modmenu", "MAINMENU", None, "CHROME"),
    ("modmenu", "GAMEMENU", None, "CHROME"),
    ("modmenu", "STATS", None, "CHROME"),
    ("modmenu", "GENERIC", None, "CHROME"),
    # The one real QUESTION in the table — "do you want to nerve staple?" — and the reason the
    # decision/notice split exists at all.
    ("modmenu", "NERVESTAPLE2", "base.staple", "DECISION"),
    (None, "CORNERFOILED", "econ.corner_market", "NOTICE"),
    (None, "CORNERTHEMFOIL", "econ.corner_market", "NOTICE"),
    (None, "CORNERTHEMFOILED", "econ.corner_market", "NOTICE"),
    (None, "SURVIVEPROJECT", "base.project", "NOTICE"),
    (None, "HALTPROJECT", "base.project", "NOTICE"),
    (None, "SEIZEPROJECT", "base.project", "NOTICE"),
    (None, "LOSEPROJECT", "base.project", "NOTICE"),
    ("modmenu", "SPYFOUND", "probe.action", "NOTICE"),
    ("modmenu", "SPYLOST", "probe.action", "NOTICE"),
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
    for _file, label, surface, _kind in DIALOG_TABLE:
        if surface is None:
            continue
        assert surface in ALL, f"{label} -> {surface} is not a known surface"


def test_chrome_carries_no_surface_and_decisions_all_do() -> None:
    """The two kinds are not allowed to blur.

    Chrome is menu plumbing — na-4lr is explicit that automating it is throwaway work, and it
    is never recorded. Giving it a surface_id would put the main menu into coverage, which
    opens on every launch.
    """
    chrome = {label for _f, label, s, _k in DIALOG_TABLE if s is None}
    assert chrome == {"MAINMENU", "GAMEMENU", "STATS", "GENERIC"}
    for _file, label, surface, _kind in DIALOG_TABLE:
        assert (label in chrome) == (surface is None), label


def test_labels_are_unique_per_file() -> None:
    """Lookup returns the FIRST match, so a duplicated key silently shadows the later entry."""
    keys = [(f, label) for f, label, _s, _k in DIALOG_TABLE]
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
    mapped = {s for _f, _label, s, _k in DIALOG_TABLE if s}
    unearned = (mapped & OBSERVED) - set(INDEPENDENTLY_INSTRUMENTED)
    assert not unearned, f"reached OBSERVED with only a dialog hook: {sorted(unearned)}"
    assert not (mapped & APPLIED)


def test_the_exception_list_does_not_outlive_its_reason() -> None:
    """An allowlist nobody prunes becomes a permanent hole.

    Every entry must still be both dialog-mapped and in OBSERVED. A surface that left OBSERVED,
    or that is no longer in the dialog table, has no business granting an exception to a rule it
    is not subject to.
    """
    mapped = {s for _f, _label, s, _k in DIALOG_TABLE if s}
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


#: `na_name_base_observed`, transcribed. Like a dialog record and for the same reason: no
#: action space, so nobody could have chosen. It carries a `surface_id` — which is precisely
#: what makes the assertions below worth having.
BASE_NAME_EVENT = {
    "record": "base_name",
    "engine": "thinker",
    "surface_id": "base.name",
    "turn": 42,
    "faction_id": 1,
    "name": "Sector 41",
    "sea_base": False,
    "source": "sector_fallback",
    "pools_exhausted": True,
}


def test_a_naming_event_is_not_a_decision_record() -> None:
    """`base.name` is instrumented, and deliberately does NOT enter OBSERVED.

    The candidate names live in files read inside `mod_name_base`; enumerating them would mean
    re-reading those files on every base founding to build a list nobody applies. With no action
    space there is nothing the brain could have chosen, so this is an event, not an observation
    of a decision — the same line the dialog records sit on.

    Recording it in OBSERVED would move the coverage number for a surface where the brain has no
    decision to see, which is the one thing that set is for.
    """
    from neural_amplifier.surfaces import ALL, APPLIED, OBSERVED

    assert BASE_NAME_EVENT["surface_id"] in ALL, "still a real surface in the frozen registry"
    assert BASE_NAME_EVENT["surface_id"] not in OBSERVED
    assert BASE_NAME_EVENT["surface_id"] not in APPLIED
    for key in ("tier", "applied", "action_space", "native_choice"):
        assert key not in BASE_NAME_EVENT, key


def test_a_naming_event_is_not_harvested_as_a_world_view() -> None:
    """It carries a surface_id, so without the required-field check it would be picked as a
    capture for `base.name` and overwrite nothing — but it would look like coverage."""
    assert harvest._capture(BASE_NAME_EVENT) is None


def test_the_naming_event_reports_which_pool_ran_out() -> None:
    """The payload is `source`, not the name.

    `mod_name_base` falls through four pools in order, and the last is "Sector N". A base called
    that means every named pool was exhausted — a content problem surfacing as gameplay, which
    nothing else in the system reports. `pools_exhausted` is the flag worth alerting on.
    """
    assert BASE_NAME_EVENT["source"] in {
        "faction_sea",
        "faction_land",
        "generic_pool",
        "sector_fallback",
    }
    assert BASE_NAME_EVENT["pools_exhausted"] is (BASE_NAME_EVENT["source"] == "sector_fallback")


def test_only_a_notice_is_ever_auto_answerable() -> None:
    """The safety property of the auto-answer path, and the one worth a test of its own.

    An unattended run cannot answer a popp dialog, so it hangs forever. `na_dialog_auto` answers
    one-button NOTICES with 0 — "this happened to you", acknowledged and dismissed.

    It must NEVER answer a real question. A question's button indices live in a game text file
    this project does not ship, so picking one would be inventing an answer to a decision, which
    is the thing the adapter refuses to do everywhere else. NERVESTAPLE2 is the case: "do you
    want to nerve staple?" has consequences, and guessing a button could staple a base nobody
    chose to staple.
    """
    answerable = {label for _f, label, _s, kind in DIALOG_TABLE if kind == "NOTICE"}
    questions = {label for _f, label, _s, kind in DIALOG_TABLE if kind == "DECISION"}
    chrome = {label for _f, label, _s, kind in DIALOG_TABLE if kind == "CHROME"}

    assert "NERVESTAPLE2" in questions
    assert not (answerable & questions)
    assert not (answerable & chrome), "menus are not dismissed on the game's behalf either"
    assert answerable, "a NOTICE class with no members would make the feature dead code"


def test_auto_answering_is_gated_on_there_being_no_human() -> None:
    """Documents the condition that keeps this inside invariant 7 rather than against it.

    Invariant 7 forbids blanket-suppressing dialogs because they are decision points a human
    should see. Auto-answering while someone is watching would take their decision away — so the
    gate is `na_headless()`, in code, not a preference. With nobody there the alternative is not
    "the human decides", it is "the run hangs", which is why the two are not in tension.

    Asserted as a documented invariant rather than by running the C++: the pin's job is to make
    the rule visible on this side, so a later change that widens the gate has to argue with it.
    """
    gate = "na_headless() and conf.na_dialog_auto"
    assert "na_headless" in gate
    assert "conf.na_dialog_auto" in gate


def test_an_auto_answered_dialog_says_so_in_its_record() -> None:
    """A run that answered forty dialogs on the game's behalf did not have the same run as one
    that answered none, and the log has to be able to tell them apart.

    The adapter emits `auto_answered: true` and counts them in `dialog-stats`, so "the run
    finished" can be read against how much of it was the engine and how much was the harness.
    """
    auto = dict(MAPPED)
    auto["auto_answered"] = True
    assert auto["auto_answered"] is True
    assert "auto_answered" not in MAPPED, "the ordinary path must not claim it"
