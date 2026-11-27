"""The practice loop: author a skill, trial it, feed back what happened, revise, admit or shelve.

``seats-and-policy.md`` §5.2 and build step 6. Voyager's rewrite loop with a fixed starting point:

1. The :class:`Author` writes a draft — code plus a :class:`~.skills.SuccessCheck`.
2. The draft is saved as a ``candidate``. A draft with no success check, or one naming a metric
   outside the measured vocabulary, is refused at save time; the refusal is the round's feedback.
3. The :class:`TrialRunner` runs the candidate on **every** seed.
4. Each trial's script error, refusals (validation, the guard, the order gate), order outcomes
   and success-check verdict go back to the author as structured data (:class:`TrialReport`),
   and the author revises.

All seeds pass: the trials are recorded and the candidate is promoted to ``verified``, which the
store allows only with passing trials from at least ``min_verified_seeds`` distinct seeds (§5.4).
No pass by the last round (four, as Voyager): the run is **shelved** — every candidate it minted
is retired with the reason recorded, so none of them is ever retrieved.

The seam is the two protocols. :class:`OfflineTrialRunner` here trials a skill over the captured
world views with no game; a game-host runner replays a save fixture under the same protocol. A
seed is a fixture crossed with a variant label (:class:`Seed`), so one capture yields several
distinct boards and admission needs a skill that holds across them.

**What an offline trial can and cannot judge.** A capture is one board at one moment; nothing
advances it. Offline, the success check is judged against that board after the immediate
effects the ENGINE states for the accepted actions (``Action.effects``, adapter-stated, never
skill-stated) — or whatever a supplied ``project`` callable says — and the horizon is carried,
not played. A verdict that the board reached the target in N turns needs a
game, which is the game-host runner's job. Order outcomes are likewise what the orchestrator
accepted or refused; whether the engine then kept an order reads ``unknown``, never ``applied``.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .brain import ScriptedBrain
from .contract import Orders, WorldView
from .hank import StateGuard
from .knowledge import Guard
from .metrics import VOCABULARY
from .orchestrator import Orchestrator
from .queued import QueuedAnswer, QueueStore
from .skill_runner import SkillRunner
from .skills import (
    LintError,
    PolicySet,
    Skill,
    SkillBrain,
    SkillEffectRefused,
    SkillError,
    SkillRunFailed,
    SkillStore,
    SuccessCheck,
)

#: Voyager's cap: a task not solved in four rounds of rewriting is dropped (§5.1).
MAX_ROUNDS = 4

#: The captured world views shipped with the test suite.
DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "captured"

#: The variant label that leaves a capture as it was recorded.
AS_CAPTURED = "as_captured"

RefusalStage = Literal["save", "validation", "guard", "order_gate"]
Outcome = Literal["admitted", "shelved"]


# --------------------------------------------------------------------------------------------
# What the author writes, and what it is told
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    """What the skill is for. The name and description are the library entry it becomes."""

    name: str
    description: str
    surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    """One round's attempt: the code and the success check the author declares for it."""

    code: str
    check: SuccessCheck | dict[str, Any] | None
    params: dict[str, Any] = field(default_factory=dict)
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Seed:
    """One trial starting point: a captured fixture crossed with a variant label."""

    fixture: str
    label: str = AS_CAPTURED

    @property
    def source(self) -> str:
        return f"{self.fixture}#{self.label}"

    @property
    def number(self) -> int:
        """A stable integer for the store's trial record; distinct seeds give distinct numbers."""
        return zlib.crc32(self.source.encode("utf-8"))


@dataclass(frozen=True)
class Refusal:
    """Something a gate would not accept, and which gate."""

    stage: RefusalStage
    detail: str


@dataclass(frozen=True)
class OrderOutcome:
    """One thing the skill asked for, and what became of it.

    ``outcome`` is the orchestrator's: ``accepted`` or ``refused``. ``engine`` is what the game did
    with an accepted one, which only a game can say; offline it is ``unknown``.
    """

    kind: Literal["choose", "queue", "order", "policy"]
    what: str
    outcome: Literal["accepted", "refused"]
    engine: Literal["unknown", "applied", "diverged"] = "unknown"


@dataclass(frozen=True)
class CheckVerdict:
    """The success check, judged."""

    check: str
    passed: bool
    observed: float | None
    detail: str


@dataclass(frozen=True)
class TrialReport:
    """One trial of one candidate on one seed — everything the author is told about it."""

    seed: Seed
    verdict: Literal["pass", "fail"]
    #: The script's own failure — ``{"failure": "steps", "detail": "..."}`` — or ``None``.
    script_error: dict[str, Any] | None = None
    refusals: tuple[Refusal, ...] = ()
    orders: tuple[OrderOutcome, ...] = ()
    check: CheckVerdict | None = None
    #: True when the skill returned nothing on this seed: a trial it did not act in proves nothing.
    fell_through: bool = False
    deltas: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["seed"] = {"fixture": self.seed.fixture, "label": self.seed.label}
        return data


@dataclass(frozen=True)
class RoundFeedback:
    """One round: which version was tried, whether it saved, and every trial's report."""

    round: int
    skill: str | None
    save_refusal: Refusal | None = None
    trials: tuple[TrialReport, ...] = ()

    @property
    def passed(self) -> bool:
        return (
            self.save_refusal is None
            and bool(self.trials)
            and all(t.verdict == "pass" for t in self.trials)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "skill": self.skill,
            "save_refusal": None if self.save_refusal is None else asdict(self.save_refusal),
            "trials": [t.as_dict() for t in self.trials],
            "passed": self.passed,
        }


@dataclass(frozen=True)
class PracticeResult:
    outcome: Outcome
    #: The verified version on admission; the last version tried on a shelf, if any saved.
    skill: Skill | None
    rounds: tuple[RoundFeedback, ...]
    reason: str


class Author(Protocol):
    """Writes a draft for ``goal``; ``history`` is every earlier round's feedback, oldest first."""

    def write(self, goal: Goal, history: Sequence[RoundFeedback]) -> Draft: ...


class TrialRunner(Protocol):
    """Runs ``skill`` from ``seed`` and reports what happened."""

    def run(self, skill: Skill, seed: Seed) -> TrialReport: ...


# --------------------------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------------------------


def loop(
    goal: Goal,
    author: Author,
    trial_runner: TrialRunner,
    seeds: Sequence[Seed],
    store: SkillStore | None = None,
    max_rounds: int = MAX_ROUNDS,
) -> PracticeResult:
    """Author, trial on every seed, feed back, revise; admit as ``verified`` or shelve.

    Refuses to start (``ValueError``) when the seeds could never admit a skill: fewer distinct
    seeds than the store's ``min_verified_seeds``, or a round cap outside 1..4.
    """
    store = store if store is not None else SkillStore()
    if not 1 <= max_rounds <= MAX_ROUNDS:
        raise ValueError(f"max_rounds must be 1..{MAX_ROUNDS}, not {max_rounds}")
    distinct = list(dict.fromkeys(seeds))
    if len({s.number for s in distinct}) < store.min_verified_seeds:
        raise ValueError(
            f"{len(distinct)} distinct seed(s) can never admit a skill; verification needs "
            f"{store.min_verified_seeds}"
        )

    history: list[RoundFeedback] = []
    minted: list[int] = []
    last: Skill | None = None
    for number in range(1, max_rounds + 1):
        draft = author.write(goal, tuple(history))
        try:
            skill = store.save(
                goal.name,
                goal.description,
                draft.code,
                surfaces=goal.surfaces,
                params=draft.params,
                requires=draft.requires,
                check=draft.check,
                require_check=True,
            )
        except LintError as exc:
            history.append(RoundFeedback(number, None, save_refusal=Refusal("save", str(exc))))
            continue
        last = skill
        if skill.version not in minted and skill.status == "candidate":
            minted.append(skill.version)

        reports = tuple(trial_runner.run(skill, seed) for seed in distinct)
        feedback = RoundFeedback(number, skill.ref, trials=reports)
        history.append(feedback)
        for report in reports:
            last = store.record_trial(
                skill.name,
                skill.version,
                report.seed.source,
                report.seed.number,
                report.verdict,
                report.deltas,
            )
        if feedback.passed:
            admitted = store.promote(skill.name, skill.version)
            return PracticeResult(
                "admitted",
                admitted,
                tuple(history),
                f"{admitted.ref} passed on all {len(distinct)} seeds in round {number}",
            )

    reason = _shelf_reason(history, max_rounds)
    for version in minted:
        current = store.get(goal.name, version)
        if current is not None and current.status == "candidate":
            retired = store.retire(goal.name, version, reason)
            if last is not None and last.version == version:
                last = retired
    return PracticeResult("shelved", last, tuple(history), reason)


def _shelf_reason(history: Sequence[RoundFeedback], max_rounds: int) -> str:
    final = history[-1] if history else None
    if final is None:
        return f"shelved: no round ran in {max_rounds}"
    if final.save_refusal is not None:
        why = f"the last draft was refused at save: {final.save_refusal.detail}"
    else:
        failed = [t for t in final.trials if t.verdict == "fail"]
        why = f"the last version failed {len(failed)} of {len(final.trials)} seed(s)"
        if failed:
            why += f" (first: {_trial_summary(failed[0])})"
    return f"shelved after {len(history)} round(s) without passing every seed: {why}"


def _trial_summary(report: TrialReport) -> str:
    if report.script_error is not None:
        return f"{report.seed.source} script error {report.script_error.get('failure')}"
    if report.refusals:
        return f"{report.seed.source} refused by {report.refusals[0].stage}"
    if report.fell_through:
        return f"{report.seed.source} fell through"
    if report.check is not None:
        return f"{report.seed.source} check: {report.check.detail}"
    return report.seed.source


# --------------------------------------------------------------------------------------------
# The offline trial runner
# --------------------------------------------------------------------------------------------

#: A projection from a board and the skill's accepted orders to the metrics it becomes.
Projection = Callable[[WorldView, Orders], Mapping[str, float]]


def immediate_effects(world_view: WorldView, orders: Orders) -> dict[str, float]:
    """The board's metrics plus the engine-stated immediate effects of each accepted action.

    Only what the adapter put in ``Action.effects``: a skill cannot describe its own move.
    """
    after = {k: float(v) for k, v in (world_view.metrics or {}).items()}
    by_id = {a.id: a for a in world_view.action_space}
    for choice in orders.choices:
        action = by_id.get(choice.action_id)
        for metric, change in ((action.effects or {}) if action else {}).items():
            if metric in after:
                after[metric] += float(change)
    return after


class OfflineTrialRunner:
    """Trials a skill over the captured world views: no game, no process beyond the runner.

    ``variants`` maps a seed label to metric overrides applied to the capture before the skill
    sees it (``{"lean": {"energy_reserves": 20}}``); :data:`AS_CAPTURED` is always available and
    changes nothing. The decision goes through the full :class:`Orchestrator` with the guard
    (``StateGuard`` by default) and no repair, so a skill's answer meets the validation and guard
    a model's would. ``project`` turns the board and the accepted orders into the metrics the
    check is judged on; the default is :func:`immediate_effects`.
    """

    def __init__(
        self,
        fixtures: str | Path = DEFAULT_FIXTURES,
        runner: SkillRunner | None = None,
        guard: Guard | None = None,
        variants: Mapping[str, Mapping[str, float]] | None = None,
        project: Projection | None = None,
    ) -> None:
        self.fixtures = Path(fixtures)
        self.runner = runner or SkillRunner()
        self.guard: Guard = guard if guard is not None else StateGuard()
        self.variants: dict[str, dict[str, float]] = {AS_CAPTURED: {}}
        for label, overrides in (variants or {}).items():
            unknown = sorted(set(overrides) - set(VOCABULARY))
            if unknown:
                raise ValueError(f"variant {label!r} overrides unknown metric(s) {unknown}")
            self.variants[label] = {k: float(v) for k, v in overrides.items()}
        self.project: Projection = project or immediate_effects

    def seeds(self, fixtures: Sequence[str], labels: Sequence[str] | None = None) -> list[Seed]:
        """Every fixture crossed with every label (all known labels when ``labels`` is None)."""
        chosen = list(labels) if labels is not None else list(self.variants)
        return [Seed(f, label) for f in fixtures for label in chosen]

    def world_view(self, seed: Seed) -> WorldView:
        if seed.label not in self.variants:
            raise ValueError(f"unknown seed label {seed.label!r}; known: {sorted(self.variants)}")
        path = self.fixtures / f"{seed.fixture}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["metrics"] = {**(data.get("metrics") or {}), **self.variants[seed.label]}
        return WorldView.model_validate(data)

    def run(self, skill: Skill, seed: Seed) -> TrialReport:
        world_view = self.world_view(seed)
        brain = _TrialBrain(
            skill,
            SkillBrain(
                SkillStore(),
                ScriptedBrain(),
                runner=self.runner,
                queue=QueueStore(),
                practice=True,
            ),
        )
        result = Orchestrator(brain, guard=self.guard, repair_attempts=0).decide(world_view)

        if isinstance(brain.error, SkillRunFailed):
            return TrialReport(
                seed,
                "fail",
                script_error={"failure": brain.error.failure, "detail": brain.error.detail},
            )
        if isinstance(brain.error, SkillEffectRefused):
            stage: RefusalStage = "order_gate" if brain.error.kind == "order" else "validation"
            refusal = Refusal(stage, brain.error.detail)
            return TrialReport(
                seed,
                "fail",
                refusals=(refusal,),
                orders=(
                    OrderOutcome(_kind(brain.error.kind), f"{brain.error.kind} effect", "refused"),
                ),
            )
        if brain.error is not None:  # the answer itself was malformed
            return TrialReport(seed, "fail", refusals=(Refusal("validation", str(brain.error)),))
        if brain.asked is None:
            return TrialReport(seed, "fail", fell_through=True)

        offered = {a.id for a in world_view.action_space}
        kept = {c["action_id"] for c in result.record.chosen if "action_id" in c}
        if result.orders.degraded:
            kept = set()
        refusals: list[Refusal] = []
        outcomes: list[OrderOutcome] = []
        for choice in brain.asked.choices:
            if choice.action_id in kept:
                outcomes.append(OrderOutcome("choose", choice.action_id, "accepted"))
                continue
            stage = "validation" if choice.action_id not in offered else "guard"
            outcomes.append(OrderOutcome("choose", choice.action_id, "refused"))
            refusals.append(
                Refusal(
                    stage,
                    f"{choice.action_id}: "
                    + (
                        "not in the action space the engine offered"
                        if stage == "validation"
                        else (result.record.degrade_reason or "denied by the guard")
                    ),
                )
            )
        for effect in brain.effects:
            outcomes.append(_effect_outcome(effect))

        accepted = Orders(
            choices=[c for c in brain.asked.choices if c.action_id in kept],
            notes=brain.asked.notes,
        )
        before = dict(world_view.metrics or {})
        after = dict(self.project(world_view, accepted))
        deltas = {
            k: float(after[k]) - float(before.get(k, 0.0))
            for k in sorted(after)
            if k in before and float(after[k]) != float(before[k])
        }
        verdict: CheckVerdict | None = None
        if skill.check is not None:
            ok, observed, detail = skill.check.judge(after)
            verdict = CheckVerdict(skill.check.describe(), ok, observed, detail)
        passed = not refusals and verdict is not None and verdict.passed
        return TrialReport(
            seed,
            "pass" if passed else "fail",
            refusals=tuple(refusals),
            orders=tuple(outcomes),
            check=verdict,
            deltas=deltas,
        )

    def close(self) -> None:
        self.runner.close()


def _kind(kind: str) -> Literal["choose", "queue", "order", "policy"]:
    return kind if kind in ("queue", "order", "policy") else "choose"  # type: ignore[return-value]


def _effect_outcome(effect: object) -> OrderOutcome:
    if isinstance(effect, QueuedAnswer):
        return OrderOutcome("queue", f"{effect.surface_id} -> {effect.action_id}", "accepted")
    if isinstance(effect, PolicySet):
        return OrderOutcome("policy", f"{effect.knob} = {effect.value!r}", "accepted")
    return OrderOutcome("order", str(effect), "accepted")


class _TrialBrain:
    """Runs exactly the candidate under test, and keeps what the orchestrator would swallow.

    The orchestrator turns a brain error into a degraded decision; the author needs the error
    itself, so it is kept here before being re-raised.
    """

    name = "skill"

    def __init__(self, skill: Skill, brain: SkillBrain) -> None:
        self.skill = skill
        self.brain = brain
        self.error: SkillError | None = None
        self.asked: Orders | None = None
        self.effects: list[object] = []

    def decide(self, world_view: WorldView) -> Orders:
        try:
            orders = self.brain.run_skill(self.skill, world_view)
        except SkillError as exc:
            self.error = exc
            raise
        self.effects = list(self.brain.last_effects)
        if orders is None:
            raise SkillError(f"skill {self.skill.ref} fell through on this board")
        self.asked = orders
        return orders
