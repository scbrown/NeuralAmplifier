"""The fairness ledger, computed rather than asserted.

``docs/game-surface.md`` §5 lists the rule asymmetries as a static table with a
fixed ``favours`` column. Reading the fork to build this module showed the table
is an index, not a source of truth: three entries change *which side* they
favour as difficulty moves (``tech_cost_factor``, ``content_pop``, the combat
modifiers — the last of which the doc labels "favours AI" when the source shows
it favours the human), and two are inert under the fork's shipped defaults. A
static table would declare handicaps that are not in force and mislabel ones
that are, so the ledger is derived from ``(slot, difficulty, config)`` here.

Policy is **record, not neutralise** — nothing here patches the game. This
produces the ``fairness`` block of the world view (``docs/contract.md``) so a
result stays interpretable, and :func:`drift` catches an adapter whose stamp
disagrees with what the rules actually say.

Every asymmetry in the fork is an ``is_human`` branch, so a **human slot has an
empty ledger** — Mode B+, the only configuration backing an unqualified
fair-play claim. Opposing AI factions still hold their bonuses there; that is
the ordinary human experience and precisely the baseline being claimed against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .contract import Fairness, Handicap

#: SMAC's six difficulty levels, in ``*DiffLevel`` order (``engine_enums.h:431``).
DIFFICULTIES = ("citizen", "specialist", "talent", "librarian", "thinker", "transcend")

Favours = Literal["self", "other", "config"]


class Config(BaseModel):
    """The ``thinker.ini`` values that move the ledger.

    Defaults mirror the fork's ``main.h:327-330`` so a stock build derives the
    stock profile. Note ``unit_support_bonus`` ships all-zero — the doc lists it
    as an AI advantage, but it is inert until someone configures it.
    """

    model_config = ConfigDict(frozen=True)

    tech_cost_factor: tuple[int, ...] = (124, 116, 108, 100, 84, 76)
    unit_support_bonus: tuple[int, ...] = (0, 0, 0, 0, 0, 0)
    content_pop_player: tuple[int, ...] = (6, 5, 4, 3, 2, 1)
    content_pop_computer: tuple[int, ...] = (3, 3, 3, 3, 3, 3)
    #: ``Rules->retool_penalty_prod_change``. Zero means nobody pays it, so the
    #: asymmetry disappears rather than merely shrinking.
    retool_penalty_prod_change: int = 50


DEFAULT_CONFIG = Config()


def index_for(difficulty: str) -> int:
    """``*DiffLevel`` for a difficulty name. Raises rather than guessing —
    a typo silently scoring as Citizen would understate every handicap."""
    try:
        return DIFFICULTIES.index(difficulty.strip().lower())
    except ValueError:
        raise ValueError(
            f"unknown difficulty {difficulty!r}; expected one of {DIFFICULTIES}"
        ) from None


@dataclass(frozen=True)
class Rule:
    """One ledger entry and the condition under which it is actually in force."""

    id: str
    selected_by: Literal["difficulty", "structural"]
    #: ``file:line`` in the Thinker fork. Kept so a wrong claim is checkable.
    source: str
    #: ``(difficulty_index, config) -> (favours, detail)``, or ``None`` when the
    #: branch is inert at this difficulty or under this config.
    evaluate: Callable[[int, Config], tuple[Favours, str] | None]

    def applies(self, level: int, config: Config) -> Handicap | None:
        result = self.evaluate(level, config)
        if result is None:
            return None
        favours, detail = result
        return Handicap(id=self.id, favours=favours, selected_by=self.selected_by, detail=detail)


def _tech_cost(level: int, config: Config) -> tuple[Favours, str] | None:
    factor = config.tech_cost_factor[level]
    if factor == 100:
        return None
    favours: Favours = "self" if factor < 100 else "other"
    return favours, f"AI tech cost x{factor / 100:.2f}; humans pay x1.00"


def _unit_support(level: int, config: Config) -> tuple[Favours, str] | None:
    bonus = config.unit_support_bonus[level]
    if bonus == 0:
        return None
    return "self", f"{bonus} extra units supported free of mineral upkeep"


def _facility_maint(level: int, _config: Config) -> tuple[Favours, str] | None:
    if level < 4:
        return None
    share = "a third" if level == 4 else "two thirds"
    return "self", f"{share} of facility maintenance refunded to the AI each turn"


def _terraform_speed(level: int, _config: Config) -> tuple[Favours, str] | None:
    if level <= 3:
        return None
    return "self", "AI terraforming jobs longer than 3 turns finish one turn sooner"


def _mind_control(level: int, _config: Config) -> tuple[Favours, str] | None:
    if level <= 3:
        return None
    return "self", f"AI mind-control cost against humans x{3 / level:.2f}"


def _combat_modifiers(level: int, _config: Config) -> tuple[Favours, str] | None:
    # Favours the *human*, contrary to the doc table: a low-difficulty human
    # attacker's offense is scaled up and an AI attacker's is scaled down.
    # The threshold is `3 - (attacker != faction 0)`, so faction-vs-faction
    # combat cuts off a level earlier than native attacks do.
    if level >= 3:
        return None
    detail = (
        f"human offense x{(4 - level) / 2:.2f} against AI, AI offense "
        f"x{(level + 1) / 4:.2f} against humans (native attacks only at Talent)"
    )
    return "other", detail


def _retool_penalty(_level: int, config: Config) -> tuple[Favours, str] | None:
    if config.retool_penalty_prod_change == 0:
        return None
    return "self", (
        f"humans forfeit up to {config.retool_penalty_prod_change}% of accumulated "
        "minerals when changing production; the AI pays nothing at any difficulty"
    )


def _global_warming(level: int, _config: Config) -> tuple[Favours, str] | None:
    if level >= 4:
        return None
    return "self", "AI eco-damage does not advance the climate below Thinker"


def _mineral_carry_over(_level: int, _config: Config) -> tuple[Favours, str] | None:
    return "self", "human minerals are capped at the retool exemption after a build; AI keeps them"


def _eco_damage(_level: int, _config: Config) -> tuple[Favours, str] | None:
    return "self", "AI eco-damage is rolled back after fungal pops; human damage persists"


def _former_automation(_level: int, _config: Config) -> tuple[Favours, str] | None:
    return "self", "automated formers take AI-only routes the human automation is barred from"


def _content_pop(level: int, config: Config) -> tuple[Favours, str] | None:
    player = config.content_pop_player[level]
    computer = config.content_pop_computer[level]
    if player == computer:
        return None
    favours: Favours = "self" if computer > player else "other"
    return favours, f"{computer} content citizens for AI factions vs {player} for humans"


def _starting_units(_level: int, _config: Config) -> tuple[Favours, str] | None:
    return "config", "AI and human factions are seeded from separate starting-unit lists"


def _colony_disband(level: int, _config: Config) -> tuple[Favours, str] | None:
    if level <= 1:
        return None
    detail = "an AI size-1 base never disbands building a colony pod"
    if level > 3:
        detail += f"; it banks {level - 2} nutrients instead"
    return "self", detail


def _project_race(_level: int, _config: Config) -> tuple[Favours, str] | None:
    return "other", "the AI will not complete a project a human base could finish this turn"


#: The ledger. Order is the reporting order, difficulty-selected first, matching
#: ``docs/game-surface.md`` §5.1 — the split exists so the two never get
#: conflated in a result, since only the structural set needs defending.
LEDGER: tuple[Rule, ...] = (
    Rule("unit_support_bonus", "difficulty", "base.cpp:1645", _unit_support),
    Rule("facility_maint_discount", "difficulty", "game.cpp:1846", _facility_maint),
    Rule("tech_cost_factor", "difficulty", "tech.cpp:1155", _tech_cost),
    Rule("terraform_speed", "difficulty", "veh_action.cpp:210", _terraform_speed),
    Rule("mind_control_cost", "difficulty", "probe.cpp:713", _mind_control),
    Rule("combat_modifiers", "difficulty", "veh_combat.cpp:1557", _combat_modifiers),
    Rule("retool_penalty", "structural", "base.cpp:1045", _retool_penalty),
    Rule("global_warming", "structural", "base.cpp:3205", _global_warming),
    Rule("mineral_carry_over", "structural", "base.cpp:3382", _mineral_carry_over),
    Rule("eco_damage_rollback", "structural", "base.cpp:3118", _eco_damage),
    Rule("former_automation", "structural", "move.cpp:1533", _former_automation),
    Rule("content_pop", "structural", "base.cpp:4220", _content_pop),
    Rule("starting_units", "structural", "faction.cpp:1759", _starting_units),
    Rule("colony_pod_disband", "structural", "base.cpp:3325", _colony_disband),
    Rule("project_race_block", "structural", "base.cpp:3639", _project_race),
)

BY_ID: dict[str, Rule] = {rule.id: rule for rule in LEDGER}


def is_structural(handicap_id: str) -> bool:
    """Whether an id is one nobody chose. Unknown ids are treated as structural
    — an unrecognised advantage is exactly the kind that needs defending."""
    rule = BY_ID.get(handicap_id)
    return rule is None or rule.selected_by == "structural"


def profile(
    slot: Literal["ai", "human"],
    difficulty: str,
    config: Config = DEFAULT_CONFIG,
) -> Fairness:
    """The ``fairness`` block for a faction in this slot at this difficulty."""
    level = index_for(difficulty)
    handicaps: list[Handicap] = []
    if slot == "ai":
        handicaps = [h for rule in LEDGER if (h := rule.applies(level, config)) is not None]
    return Fairness(slot=slot, difficulty=DIFFICULTIES[level], handicaps=handicaps)


@dataclass(frozen=True)
class Drift:
    """Disagreement between what an adapter stamped and what the rules say.

    An adapter that quietly stops stamping is the fairness equivalent of the
    all-fallback run: everything still completes and every result reads as
    clean. This is what makes that visible.
    """

    missing: frozenset[str]
    """Derived as in force, but the adapter did not declare it."""

    unexpected: frozenset[str]
    """Declared by the adapter, but not derivable from slot/difficulty/config."""

    mislabelled: frozenset[str]
    """Declared with a ``favours`` or ``selected_by`` the ledger disagrees with."""

    @property
    def clean(self) -> bool:
        return not (self.missing or self.unexpected or self.mislabelled)


def drift(stamped: Fairness | None, config: Config = DEFAULT_CONFIG) -> Drift:
    """Check an adapter-stamped block against the derived ledger.

    A block with no ``slot`` or ``difficulty`` cannot be checked at all — every
    declared id is reported as unexpected rather than silently accepted.
    """
    declared = {h.id: h for h in stamped.handicaps} if stamped else {}
    if stamped is None or stamped.slot is None or stamped.difficulty is None:
        return Drift(frozenset(), frozenset(declared), frozenset())

    expected = {h.id: h for h in profile(stamped.slot, stamped.difficulty, config).handicaps}
    mislabelled = {
        key
        for key, want in expected.items()
        if (got := declared.get(key)) is not None
        and (got.favours != want.favours or got.selected_by != want.selected_by)
    }
    return Drift(
        missing=frozenset(expected) - frozenset(declared),
        unexpected=frozenset(declared) - frozenset(expected),
        mislabelled=frozenset(mislabelled),
    )


#: Re-exported under unambiguous names at package level, where "profile"
#: and "drift" would read as generic utilities.
fairness_profile = profile
handicap_drift = drift
