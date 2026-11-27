"""Skills: saved scripts that answer decisions with no model in the loop (``seats-and-policy.md`` §5).

Three parts, each owning one rule:

- :func:`lint` — **parameters, not literals.** A skill that names base 7 works in one game. A
  skill that hard-codes a faction, base or unit id, or an engine-qualified id string such as
  ``"unit:0"``, is refused at save time; it must find its subject through the view.
- :class:`SkillStore` — **a rewrite is a new version.** Saving under an existing name appends a
  version that ``supersedes`` the previous content hash; the old one stays readable. Retrieval is
  by description, as Voyager's is. This is the local stand-in for the Quipu library of §5.4,
  which goes through the governed ontology process (``docs/proposals/na-skill-shape.ttl``).
- :class:`SkillBrain` — **a skill is a Brain.** Its answer is returned as ordinary
  :class:`Orders`, so it passes validation, the guard and the record exactly as a model's would,
  and it cannot name an action the engine did not offer. No matching skill, or every match
  falling through, hands the world view to the fallback brain untouched. A skill that breaches
  its budget or errors fails closed: :class:`SkillError` is a :class:`BrainError`, so the
  orchestrator degrades to the safe fallback and records why.

The primitives a skill gets need no fork change: ``choose(action_id, reason)`` (the reply to
``/decide``), ``queue_answer(...)`` (a standing answer, ``queued.py``), ``order(verb, *args)``
(the command channel, ``orders.py``) and, from ``on_turn`` only, ``set_policy(knob, value,
reason)`` (the knob registry, ``faction_policy.py``).

**Two entry points** (§5.3). ``on_decision(view, ...)`` runs on a raised decision and returns an
answer or ``None``. ``on_turn(view, ...)`` runs when the turn is announced, may set policy, queue
answers and issue orders, and returns nothing. Both run under the same runner budget, and every
effect of one run is checked before any is applied: one refused knob and none of them land.

**Status and trials** (§5.4). Every version carries a ``status`` — ``candidate`` (the default),
``verified`` or ``retired`` — and the trials it has been run in: source, seed, verdict and the
metric deltas. Promotion to ``verified`` needs passing trials from at least N distinct seeds
(``SkillStore(min_verified_seeds=...)``, default 3). A live :class:`SkillBrain` answers only
with ``verified`` skills; a practice brain (fixtures, offline trials) also uses candidates.
``retired`` is never retrieved by anyone.

**Requires** (§5.4). The knob ids a skill touches. Retrieval drops a skill needing a knob the
running engine lacks, and a skill that sets a knob it did not declare fails closed.

**Success check** (§5.2). What success means, declared before the skill runs: a metric from the
measured vocabulary (``metrics.py``), a comparator, a target and a horizon in turns. The game is
the critic, so a check naming anything outside the vocabulary is refused at save time, and a
skill authored by the practice loop (``practice.py``) without one is refused outright.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, get_args

from .brain import Brain, BrainError
from .contract import Choice, Orders, WorldView
from .faction_policy import FACTION_IDS, KNOBS, KnobError, PolicyStore
from .faction_policy import validate as validate_knob
from .metrics import VOCABULARY
from .orders import OrderChannel, build_command
from .queued import COMPARATORS, Predicate, QueuedAnswer, QueueError, QueueStore
from .queued import validate as validate_predicates
from .skill_runner import RunResult, SkillRunner

ENTRY_DECISION = "on_decision"
ENTRY_TURN = "on_turn"

Status = Literal["candidate", "verified", "retired"]
STATUSES: tuple[str, ...] = get_args(Status)
Verdict = Literal["pass", "fail"]
#: Passing trials from this many distinct seeds promote a candidate (§5.4).
DEFAULT_MIN_VERIFIED_SEEDS = 3


class SkillError(BrainError):
    """A skill could not answer. A BrainError, so the orchestrator degrades safely."""


class SkillRunFailed(SkillError):
    """The script itself failed: a budget breach, a crash, a Starlark error."""

    def __init__(self, message: str, failure: str | None, detail: str) -> None:
        super().__init__(message)
        self.failure = failure
        self.detail = detail


class SkillEffectRefused(SkillError):
    """The script ran, but one of its effects was refused; none of them were applied."""

    def __init__(self, message: str, kind: str, detail: str) -> None:
        super().__init__(message)
        #: The refused effect's kind: ``queue``, ``order``, ``policy`` or what the script sent.
        self.kind = kind
        self.detail = detail


class LintError(ValueError):
    """A skill refused at save time."""


# --------------------------------------------------------------------------------------------
# The literal-id lint
# --------------------------------------------------------------------------------------------

#: An engine-qualified numeric id: ``unit:0``, ``facility:3``, ``se:1:2``, ``base:7``. These are
#: what an action space or a board offers for THIS game; a skill must read them from the view.
_QUALIFIED_ID = re.compile(r"^[A-Za-z_]+(?::\d+)+$")

#: A name holds an entity id when its last ``_``-separated word is one of these (``id``,
#: ``base_id``, ``veh_ids``), or it is a bare ``faction``. ``unit_cost`` and ``basement`` do not.
_ID_SUFFIXES = {"id", "ids"}

_COMPARE = {"==", "!=", "=", ":", "in", "<", ">", "<=", ">="}
_SKIP_BACK = {",", "(", "[", "{"}


def _is_id_name(tok: tokenize.TokenInfo) -> bool:
    if tok.type == tokenize.STRING:
        text = tok.string.strip("'\"")
    elif tok.type == tokenize.NAME:
        text = tok.string
    else:
        return False
    words = [w for w in re.split(r"[^A-Za-z]+", text.lower()) if w]
    if not words:
        return False
    return words[-1] in _ID_SUFFIXES or text.lower() == "faction"


def lint(code: str) -> list[str]:
    """Every literal-id problem in ``code``; empty means it may be stored.

    Token-level and deliberately conservative. It refuses:

    1. a string literal that is an engine-qualified numeric id (``"unit:0"``, ``"se:1:2"``);
    2. an integer compared, assigned or keyed against an id-bearing name — ``base_id == 7``,
       ``faction_id=3``, ``{"id": 12}``, ``b["id"] in (4, 5)``;
    3. any integer literal among an ``order(...)``'s arguments, which are unit ids, base ids and
       tile coordinates, all of which belong to one game.

    Numbers used as thresholds (``metrics["mineral_surplus"] >= 2``) are untouched.
    """
    problems: list[str] = []
    try:
        tokens = [
            t
            for t in tokenize.generate_tokens(io.StringIO(code).readline)
            if t.type
            not in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.COMMENT,
                tokenize.INDENT,
                tokenize.DEDENT,
            )
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError) as exc:
        return [f"cannot be linted: {exc}"]

    order_depth: list[int] = []  # paren depth at which each open order( call started
    depth = 0
    for i, tok in enumerate(tokens):
        line = tok.start[0]
        if tok.type == tokenize.OP and tok.string in "([{":
            if tok.string == "(" and i > 0 and tokens[i - 1].string == "order":
                order_depth.append(depth)
            depth += 1
            continue
        if tok.type == tokenize.OP and tok.string in ")]}":
            depth -= 1
            if order_depth and order_depth[-1] == depth:
                order_depth.pop()
            continue

        if tok.type == tokenize.STRING:
            text = tok.string.strip("'\"")
            if _QUALIFIED_ID.match(text):
                problems.append(
                    f"line {line}: literal id {tok.string} — find the action or entity through "
                    f"the view instead"
                )
            continue

        if tok.type != tokenize.NUMBER:
            continue
        if order_depth:
            problems.append(
                f"line {line}: literal {tok.string} in an order() — unit, base and tile ids "
                f"belong to one game; read them from the view"
            )
            continue
        # Walk back over list/tuple punctuation and sibling numbers to the operator.
        j = i - 1
        while j >= 0 and (tokens[j].string in _SKIP_BACK or tokens[j].type == tokenize.NUMBER):
            j -= 1
        if j >= 1 and tokens[j].string in _COMPARE:
            subject = tokens[j - 1]
            if subject.string in ("]", ")") and j >= 2:
                subject = tokens[j - 2]
            if _is_id_name(subject):
                problems.append(
                    f"line {line}: literal {tok.string} against {subject.string} — a skill "
                    f"must read faction, base and unit ids from the view"
                )
                continue
        # And forward: `7 == base_id`, `7 == view["base_id"]`. The subject is the last name or
        # key in the primary expression after the operator.
        if i + 1 < len(tokens) and tokens[i + 1].string in ("==", "!=", "<", ">", "<=", ">="):
            k = i + 2
            last: tokenize.TokenInfo | None = None
            while k < len(tokens) and (
                tokens[k].type in (tokenize.NAME, tokenize.STRING)
                or tokens[k].string in (".", "[", "]")
            ):
                if tokens[k].type in (tokenize.NAME, tokenize.STRING):
                    last = tokens[k]
                k += 1
            if last is not None and _is_id_name(last):
                problems.append(
                    f"line {line}: literal {tok.string} against {last.string} — a skill "
                    f"must read faction, base and unit ids from the view"
                )
    return problems


# --------------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------------


def content_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Trial:
    """One run of one skill version: where, under which seed, how it went, what moved."""

    #: A fixture name or a game id.
    source: str
    seed: int
    verdict: Verdict
    #: Metric name to change over the trial, e.g. ``{"bases": 2.0}``.
    deltas: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SuccessCheck:
    """What success means for a skill, in numbers the engine reports (§5.2, §5.4).

    "``base_count`` at_least 6 within 25 turns". The comparator grammar is the one queued-answer
    predicates and directives use, so one grammar covers every checkable claim about the board.
    """

    metric: str
    comparator: str
    target: float
    horizon_turns: int

    def validate(self) -> None:
        """Refuse a check the game cannot settle; ``LintError`` says why."""
        if self.metric not in VOCABULARY:
            raise LintError(
                f"success check names unknown metric {self.metric!r}; use one of "
                f"{', '.join(sorted(VOCABULARY))}"
            )
        if self.comparator not in COMPARATORS:
            raise LintError(
                f"success check comparator {self.comparator!r}; use one of {', '.join(COMPARATORS)}"
            )
        if (
            isinstance(self.target, bool)
            or not isinstance(self.target, int | float)
            or not math.isfinite(self.target)
        ):
            raise LintError(f"success check target must be a finite number, not {self.target!r}")
        if (
            isinstance(self.horizon_turns, bool)
            or not isinstance(self.horizon_turns, int)
            or self.horizon_turns < 1
        ):
            raise LintError(
                f"success check horizon_turns must be a whole number of turns >= 1, not "
                f"{self.horizon_turns!r}"
            )

    def describe(self) -> str:
        return (
            f"{self.metric} {self.comparator.replace('_', ' ')} {self.target:g} "
            f"within {self.horizon_turns} turn(s)"
        )

    def judge(self, measurements: dict[str, float]) -> tuple[bool, float | None, str]:
        """Pass or fail against ``measurements``, the observed value, and a sentence.

        A metric the measurements omit fails: an unread instrument is not a met target.
        """
        predicate = Predicate(self.metric, self.comparator, float(self.target))  # type: ignore[arg-type]
        ok, detail = predicate.check(measurements)
        observed = measurements.get(self.metric)
        return ok, None if observed is None else float(observed), detail


def _check_from(value: SuccessCheck | dict[str, Any] | None) -> SuccessCheck | None:
    if value is None or isinstance(value, SuccessCheck):
        return value
    if not isinstance(value, dict):
        raise LintError(
            f"a success check is metric, comparator, target, horizon_turns; got {value!r}"
        )
    try:
        return SuccessCheck(
            metric=value["metric"],
            comparator=value["comparator"],
            target=value["target"],
            horizon_turns=value["horizon_turns"],
        )
    except KeyError as exc:
        raise LintError(f"success check is missing {exc.args[0]!r}") from exc


@dataclass(frozen=True)
class Skill:
    """One version of one skill. Immutable: a change is a new version."""

    name: str
    version: int
    description: str
    code: str
    hash: str
    #: Surface ids this skill applies to; empty means any. Retrieval filters on it first.
    surfaces: tuple[str, ...] = ()
    #: Default parameters passed to every entry point.
    params: dict[str, Any] = field(default_factory=dict)
    #: The content hash this version replaces, or ``None`` for a first version.
    supersedes: str | None = None
    #: Knob ids this skill touches. Retrieval drops it when the engine lacks one.
    requires: tuple[str, ...] = ()
    #: ``candidate`` until promoted; only ``verified`` answers in a live game.
    status: Status = "candidate"
    #: Every recorded run of THIS version. A new version starts with none.
    trials: tuple[Trial, ...] = ()
    #: What success means for this version, or ``None`` for a hand-written skill without one.
    check: SuccessCheck | None = None
    #: Why this version was retired, when it was given one (a shelved practice run records it).
    retired_reason: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}#{self.hash[:12]}"

    def applies_to(self, surface_id: str | None) -> bool:
        return not self.surfaces or (surface_id is not None and surface_id in self.surfaces)

    @property
    def has_on_turn(self) -> bool:
        return _defines(self.code, ENTRY_TURN)

    def passing_seeds(self) -> set[int]:
        return {t.seed for t in self.trials if t.verdict == "pass"}


def _defines(code: str, entry: str) -> bool:
    return re.search(rf"^def\s+{entry}\s*\(", code, re.MULTILINE) is not None


def _skill_from_json(data: dict[str, Any]) -> Skill:
    data = dict(data)
    data["surfaces"] = tuple(data.get("surfaces") or ())
    data["requires"] = tuple(data.get("requires") or ())
    data["trials"] = tuple(Trial(**t) for t in data.get("trials") or ())
    data["check"] = _check_from(data.get("check"))
    data.setdefault("status", "candidate")
    if data["status"] not in STATUSES:
        raise ValueError(f"unknown skill status {data['status']!r}")
    return Skill(**data)


def _skill_to_json(skill: Skill) -> dict[str, Any]:
    data = asdict(skill)
    data["surfaces"] = list(skill.surfaces)
    data["requires"] = list(skill.requires)
    data["trials"] = [asdict(t) for t in skill.trials]
    data["check"] = None if skill.check is None else asdict(skill.check)
    return data


_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: A knob id as the registry spells them: ``focus.fight``, ``target.base``.
_KNOB_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class SkillStore:
    """Versioned skills, retrievable by description.

    In memory, or backed by a directory (``<root>/<name>/v<N>.json``) when ``root`` is given, so a
    library outlives the process that wrote it. Retrieval is lexical overlap between the query and
    each latest version's description, ties broken by name: deterministic, so a replay retrieves
    what the recorded run retrieved. Quipu's hybrid search replaces it when the library moves
    there (§5.4).

    Status and trials are recorded on the version they describe; they never change its content
    hash, which covers the code alone.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        min_verified_seeds: int = DEFAULT_MIN_VERIFIED_SEEDS,
    ) -> None:
        if min_verified_seeds < 1:
            raise ValueError("min_verified_seeds must be at least 1")
        self.min_verified_seeds = min_verified_seeds
        self._lock = threading.Lock()
        self._versions: dict[str, list[Skill]] = {}
        self.root = Path(root) if root is not None else None
        if self.root is not None and self.root.is_dir():
            for path in sorted(self.root.glob("*/v*.json")):
                skill = _skill_from_json(json.loads(path.read_text(encoding="utf-8")))
                if content_hash(skill.code) != skill.hash:
                    raise ValueError(f"{path}: stored hash does not match its code")
                self._versions.setdefault(skill.name, []).append(skill)
            for versions in self._versions.values():
                versions.sort(key=lambda s: s.version)

    def save(
        self,
        name: str,
        description: str,
        code: str,
        surfaces: Iterable[str] = (),
        params: dict[str, Any] | None = None,
        requires: Iterable[str] = (),
        check: SuccessCheck | dict[str, Any] | None = None,
        require_check: bool = False,
    ) -> Skill:
        """Lint, parse and store ``code`` as the next version of ``name``.

        Refused (``LintError``) if the name is malformed, the description empty, a required
        knob id malformed, the success check names a metric outside the vocabulary (or is
        absent when ``require_check`` — the practice loop's rule), the code hard-codes an id,
        does not parse as Starlark, or defines no ``on_decision``. Saving code and check
        identical to the current version returns that version rather than minting a new one.
        A new version is always a ``candidate``.
        """
        if not _NAME.match(name):
            raise LintError(f"skill name {name!r} must be lower_snake_case, at most 64 chars")
        if not description.strip():
            raise LintError("a skill needs a description; it is what retrieval matches")
        needs = tuple(sorted(set(requires)))
        bad = [k for k in needs if not isinstance(k, str) or not _KNOB_ID.match(k)]
        if bad:
            raise LintError(f"requires names knob ids like 'focus.fight', not {bad!r}")
        success = _check_from(check)
        if success is None and require_check:
            raise LintError(
                "a practice-authored skill needs a success check (metric, comparator, target, "
                "horizon_turns); a skill with no checkable outcome cannot be admitted"
            )
        if success is not None:
            success.validate()
        problems = lint(code)
        if problems:
            raise LintError("; ".join(problems))
        _parse(name, code)
        digest = content_hash(code)
        with self._lock:
            versions = self._versions.setdefault(name, [])
            if versions and versions[-1].hash == digest and versions[-1].check == success:
                return versions[-1]
            skill = Skill(
                name=name,
                version=len(versions) + 1,
                description=description.strip(),
                code=code,
                hash=digest,
                surfaces=tuple(surfaces),
                params=dict(params or {}),
                supersedes=versions[-1].hash if versions else None,
                requires=needs,
                check=success,
            )
            versions.append(skill)
            self._write(skill)
            return skill

    def _write(self, skill: Skill) -> None:
        if self.root is None:
            return
        path = self.root / skill.name / f"v{skill.version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(_skill_to_json(skill), indent=2, sort_keys=True)
        path.write_text(text, encoding="utf-8")

    def _replace(self, name: str, version: int, **changes: Any) -> Skill:
        """Swap one version for an updated copy. Caller holds the lock."""
        versions = self._versions.get(name) or []
        for i, current in enumerate(versions):
            if current.version == version:
                updated = replace(current, **changes)
                versions[i] = updated
                self._write(updated)
                return updated
        raise KeyError(f"no skill {name}@v{version}")

    def record_trial(
        self,
        name: str,
        version: int,
        source: str,
        seed: int,
        verdict: Verdict,
        deltas: dict[str, float] | None = None,
    ) -> Skill:
        """Append one trial to ``name@v<version>``. Status is untouched: promotion is explicit."""
        if verdict not in get_args(Verdict):
            raise ValueError(f"verdict must be 'pass' or 'fail', not {verdict!r}")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"seed must be an integer, got {seed!r}")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("a trial needs a source: the fixture or game it ran in")
        moved = {str(k): float(v) for k, v in (deltas or {}).items()}
        trial = Trial(source=source.strip(), seed=seed, verdict=verdict, deltas=moved)
        with self._lock:
            current = self._get(name, version)
            return self._replace(name, version, trials=(*current.trials, trial))

    def promote(self, name: str, version: int) -> Skill:
        """Mark ``name@v<version>`` verified, or ``ValueError`` saying why it may not be.

        Needs passing trials from at least ``min_verified_seeds`` distinct seeds. A retired
        version stays retired; promoting a verified one is a no-op.
        """
        with self._lock:
            current = self._get(name, version)
            if current.status == "retired":
                raise ValueError(f"{current.ref} is retired; save a new version instead")
            seeds = current.passing_seeds()
            if len(seeds) < self.min_verified_seeds:
                raise ValueError(
                    f"{current.ref} has passing trials from {len(seeds)} distinct seed(s); "
                    f"verification needs {self.min_verified_seeds}"
                )
            if current.status == "verified":
                return current
            return self._replace(name, version, status="verified")

    def retire(self, name: str, version: int, reason: str | None = None) -> Skill:
        """Mark ``name@v<version>`` retired, with ``reason`` if given. Never retrieved again."""
        with self._lock:
            current = self._get(name, version)
            why = reason.strip() if isinstance(reason, str) and reason.strip() else None
            return self._replace(
                name, version, status="retired", retired_reason=why or current.retired_reason
            )

    def _get(self, name: str, version: int) -> Skill:
        found = next((s for s in self._versions.get(name) or [] if s.version == version), None)
        if found is None:
            raise KeyError(f"no skill {name}@v{version}")
        return found

    def get(self, name: str, version: int | None = None) -> Skill | None:
        with self._lock:
            versions = self._versions.get(name) or []
            if not versions:
                return None
            if version is None:
                return versions[-1]
            return next((s for s in versions if s.version == version), None)

    def history(self, name: str) -> list[Skill]:
        with self._lock:
            return list(self._versions.get(name) or [])

    def eligible(
        self,
        statuses: Iterable[str] = ("verified",),
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        """Per name, the newest version that may run, sorted by name.

        A version may run when its status is in ``statuses`` (``retired`` never is) and, when
        ``knobs`` is given, every knob it requires is among them. A newer candidate therefore
        does not displace the verified version it revises in a live game.
        """
        allowed = set(statuses) - {"retired"}
        have = None if knobs is None else set(knobs)
        with self._lock:
            by_name = sorted(self._versions.items())
        out = []
        for _, versions in by_name:
            for skill in reversed(versions):
                if skill.status not in allowed:
                    continue
                if have is not None and not set(skill.requires) <= have:
                    continue
                out.append(skill)
                break
        return out

    def retrieve(
        self,
        description: str,
        surface_id: str | None = None,
        k: int = 5,
        statuses: Iterable[str] = STATUSES,
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        """The runnable versions whose descriptions best match ``description``, best first.

        Filtered by status, required knobs and applicability before ranking, as §5.4
        specifies; see :meth:`eligible`. ``retired`` is never returned whatever ``statuses``
        says. A skill sharing no word with the query is not returned at all.
        """
        query = _words(description)
        scored = []
        for skill in self.eligible(statuses, knobs):
            if not skill.applies_to(surface_id):
                continue
            score = len(query & _words(skill.description))
            if score:
                scored.append((-score, skill.name, skill))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [s for _, _, s in scored[:k]]


def _parse(name: str, code: str) -> None:
    import starlark

    try:
        ast = starlark.parse(f"{name}.star", code)
    except starlark.StarlarkError as exc:
        raise LintError(f"does not parse as Starlark: {exc}") from exc
    if not _defines(code, ENTRY_DECISION):
        raise LintError(f"a skill must define {ENTRY_DECISION}(view, ...)")
    if ast.loads():
        raise LintError("a skill may not load() other files")


# --------------------------------------------------------------------------------------------
# The brain
# --------------------------------------------------------------------------------------------


def decision_query(world_view: WorldView) -> str:
    """What a raised decision is, in words a description can match."""
    parts = [world_view.surface_id or "", world_view.scope]
    return " ".join(p.replace(".", " ").replace("_", " ") for p in parts if p)


@dataclass(frozen=True)
class PolicySet:
    """A checked ``set_policy`` from ``on_turn``, ready for :meth:`PolicyStore.set_policy`."""

    faction_id: int
    knob: str
    value: Any
    reason: str


Effect = QueuedAnswer | str | PolicySet


class SkillBrain:
    """Answers a decision from the skill library, or hands it to ``fallback`` untouched.

    For each raised decision: retrieve the applicable skills by description, run each in rank
    order until one answers, and return that answer as ordinary :class:`Orders`. A skill that
    returns ``None`` falls through to the next; if none answers, ``fallback.decide`` gets the
    original world view and its answer is returned as-is.

    Live (the default), only ``verified`` skills run; ``practice=True`` — fixtures, offline
    trials — also runs candidates. ``retired`` never runs. ``engine_knobs`` is the knob ids the
    running engine build has (default: the whole registry); a skill requiring any other is not
    retrieved. ``policy`` is where ``on_turn``'s ``set_policy`` lands, keyed by the view's own
    faction.
    """

    name = "skill"

    def __init__(
        self,
        store: SkillStore,
        fallback: Brain,
        runner: SkillRunner | None = None,
        queue: QueueStore | None = None,
        orders: OrderChannel | None = None,
        k: int = 5,
        practice: bool = False,
        engine_knobs: Iterable[str] | None = None,
        policy: PolicyStore | None = None,
    ) -> None:
        self.store = store
        self.fallback = fallback
        self.runner = runner or SkillRunner()
        self.queue = queue
        self.orders = orders
        self.k = k
        self.practice = practice
        self.engine_knobs = frozenset(KNOBS if engine_knobs is None else engine_knobs)
        self.policy = policy
        #: The skill that answered the last decision, or ``None`` if the fallback did.
        self.last_skill: Skill | None = None
        #: Queue and order effects from the last decision, converted and checked, whether or not
        #: they could be applied (no queue, channel or policy store configured).
        self.last_effects: list[Effect] = []
        #: The skills whose ``on_turn`` ran at the last turn announcement.
        self.last_turn_skills: list[Skill] = []

    @property
    def statuses(self) -> tuple[str, ...]:
        return ("verified", "candidate") if self.practice else ("verified",)

    def decide(self, world_view: WorldView) -> Orders:
        self.last_skill = None
        self.last_effects = []
        candidates = self.store.retrieve(
            decision_query(world_view),
            surface_id=world_view.surface_id,
            k=self.k,
            statuses=self.statuses,
            knobs=self.engine_knobs,
        )
        if candidates:
            view = world_view.model_dump(mode="json", exclude_none=True)
            for skill in candidates:
                orders = self._decide_with(skill, world_view, view)
                if orders is not None:
                    return orders
        return self.fallback.decide(world_view)

    def run_skill(self, skill: Skill, world_view: WorldView) -> Orders | None:
        """Run one named skill's ``on_decision``, skipping retrieval; ``None`` if it falls through.

        Same checks and effects as :meth:`decide`, for a caller that already chose the skill —
        a practice trial runs exactly the candidate under test, whatever its description says.
        """
        self.last_skill = None
        self.last_effects = []
        view = world_view.model_dump(mode="json", exclude_none=True)
        return self._decide_with(skill, world_view, view)

    def _decide_with(
        self, skill: Skill, world_view: WorldView, view: dict[str, Any]
    ) -> Orders | None:
        result = self._run(skill, ENTRY_DECISION, view)
        self._apply_effects(skill, result, world_view, allow_policy=False)
        orders = _orders_from(skill, result.answer)
        if orders is not None:
            self.last_skill = skill
        return orders

    def on_turn(self, world_view: WorldView, names: Iterable[str] | None = None) -> None:
        """Run ``on_turn`` of each eligible skill that defines one, in name order.

        ``names`` restricts it to the skills the strategist chose for this game; ``None`` runs
        every eligible one. Each run may set policy, queue answers and issue orders, and must
        return nothing. A failure, a return value, or any refused effect raises
        :class:`SkillError` with none of that skill's effects applied.
        """
        self.last_effects = []
        self.last_turn_skills = []
        wanted = None if names is None else set(names)
        skills = [
            s
            for s in self.store.eligible(self.statuses, self.engine_knobs)
            if s.has_on_turn and (wanted is None or s.name in wanted)
        ]
        if not skills:
            return
        view = world_view.model_dump(mode="json", exclude_none=True)
        for skill in skills:
            result = self._run(skill, ENTRY_TURN, view)
            if result.answer is not None:
                raise SkillError(
                    f"skill {skill.ref}: {ENTRY_TURN} returns nothing; got {result.answer!r}"
                )
            self._apply_effects(skill, result, world_view, allow_policy=True)
            self.last_turn_skills.append(skill)

    def _run(self, skill: Skill, entry: str, view: dict[str, Any]) -> RunResult:
        result = self.runner.run(skill.name, skill.code, entry, view, skill.params)
        if not result.ok:
            raise SkillRunFailed(
                f"skill {skill.ref} failed closed ({result.failure}): {result.detail}",
                result.failure,
                result.detail,
            )
        return result

    def _apply_effects(
        self, skill: Skill, result: RunResult, world_view: WorldView, allow_policy: bool
    ) -> None:
        converted: list[Effect] = []
        for effect in result.effects:
            kind = str(effect.get("kind"))
            try:
                if effect.get("kind") == "queue":
                    converted.append(_queued_from(skill, effect, world_view))
                elif effect.get("kind") == "order":
                    args = effect.get("args") or []
                    if not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
                        raise ValueError(f"order arguments must be integers, got {args!r}")
                    converted.append(build_command(str(effect.get("verb")), list(args)))
                elif effect.get("kind") == "policy":
                    if not allow_policy:
                        raise ValueError(f"set_policy is for {ENTRY_TURN}, not {ENTRY_DECISION}")
                    converted.append(self._policy_from(skill, effect, world_view))
                else:
                    raise ValueError(f"unknown effect {effect.get('kind')!r}")
            except (ValueError, QueueError, TypeError) as exc:  # KnobError is a ValueError
                raise SkillEffectRefused(
                    f"skill {skill.ref} produced an invalid effect: {exc}", kind, str(exc)
                ) from exc
        self.last_effects.extend(converted)
        for item in converted:
            if isinstance(item, QueuedAnswer) and self.queue is not None:
                self.queue.install(item)
            elif isinstance(item, str) and self.orders is not None:
                self.orders.issue(item)
            elif isinstance(item, PolicySet) and self.policy is not None:
                self.policy.set_policy(item.faction_id, item.knob, item.value, item.reason)

    def _policy_from(
        self, skill: Skill, effect: dict[str, Any], world_view: WorldView
    ) -> PolicySet:
        knob = effect.get("knob")
        if not isinstance(knob, str):
            raise KnobError(f"set_policy needs a knob id, got {knob!r}")
        if knob not in skill.requires:
            raise KnobError(f"{knob!r} is not in this skill's requires {list(skill.requires)}")
        if knob not in self.engine_knobs:
            raise KnobError(f"this engine has no knob {knob!r}")
        faction = world_view.faction_id
        if faction is None or faction not in FACTION_IDS:
            raise KnobError(f"faction {faction!r} cannot carry a policy")
        value = effect.get("value")
        checked = None if value is None else validate_knob(knob, value)
        why = effect.get("reason")
        reason = f"skill {skill.ref}" + (f": {why}" if isinstance(why, str) and why else "")
        # The faction is the view's, never the skill's: a skill steers one seat (§5.3).
        return PolicySet(faction_id=faction, knob=knob, value=checked, reason=reason)


def _orders_from(skill: Skill, answer: Any) -> Orders | None:
    if answer is None:
        return None
    picks: Sequence[Any] = answer if isinstance(answer, list) else [answer]
    choices = []
    for pick in picks:
        if not (
            isinstance(pick, dict)
            and pick.get("kind") == "choose"
            and isinstance(pick.get("action_id"), str)
        ):
            raise SkillError(
                f"skill {skill.ref} returned {pick!r}; on_decision must return choose(id), a "
                f"list of them, or None"
            )
        why = pick.get("reason")
        reason = f"skill {skill.ref}" + (f": {why}" if isinstance(why, str) and why else "")
        choices.append(Choice(action_id=pick["action_id"], reason=reason))
    if not choices:
        return None
    return Orders(choices=choices, notes=f"skill {skill.ref}")


def _queued_from(skill: Skill, effect: dict[str, Any], world_view: WorldView) -> QueuedAnswer:
    if world_view.faction_id is None:
        raise ValueError("the world view has no faction_id to queue an answer for")
    raw = effect.get("predicates") or []
    if not isinstance(raw, list):
        raise ValueError("predicates must be a list of {metric, comparator, target}")
    predicates = [
        Predicate(metric=str(p["metric"]), comparator=p["comparator"], target=float(p["target"]))
        for p in raw
    ]
    validate_predicates(predicates)
    base_id = effect.get("base_id")
    until = effect.get("until_turn")
    if base_id is not None and not isinstance(base_id, int):
        raise ValueError(f"base_id must be an integer, got {base_id!r}")
    if until is not None and not isinstance(until, int):
        raise ValueError(f"until_turn must be an integer, got {until!r}")
    reason = effect.get("reason")
    return QueuedAnswer(
        # The faction is the view's, never the skill's: a skill reads one seat (§5.3).
        faction_id=world_view.faction_id,
        surface_id=str(effect.get("surface_id")),
        action_id=str(effect.get("action_id")),
        predicates=predicates,
        base_id=base_id,
        until_turn=until,
        reason=f"skill {skill.ref}" + (f": {reason}" if isinstance(reason, str) and reason else ""),
    )
