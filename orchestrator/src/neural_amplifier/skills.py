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
``/decide``), ``queue_answer(...)`` (a standing answer, ``queued.py``) and ``order(verb, *args)``
(the command channel, ``orders.py``).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .brain import Brain, BrainError
from .contract import Choice, Orders, WorldView
from .orders import OrderChannel, build_command
from .queued import Predicate, QueuedAnswer, QueueError, QueueStore
from .queued import validate as validate_predicates
from .skill_runner import RunResult, SkillRunner

ENTRY_DECISION = "on_decision"


class SkillError(BrainError):
    """A skill could not answer. A BrainError, so the orchestrator degrades safely."""


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

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}#{self.hash[:12]}"

    def applies_to(self, surface_id: str | None) -> bool:
        return not self.surfaces or (surface_id is not None and surface_id in self.surfaces)


_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SkillStore:
    """Versioned skills, retrievable by description.

    In memory, or backed by a directory (``<root>/<name>/v<N>.json``) when ``root`` is given, so a
    library outlives the process that wrote it. Retrieval is lexical overlap between the query and
    each latest version's description, ties broken by name: deterministic, so a replay retrieves
    what the recorded run retrieved. Quipu's hybrid search replaces it when the library moves
    there (§5.4).
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._versions: dict[str, list[Skill]] = {}
        self.root = Path(root) if root is not None else None
        if self.root is not None and self.root.is_dir():
            for path in sorted(self.root.glob("*/v*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                data["surfaces"] = tuple(data.get("surfaces") or ())
                skill = Skill(**data)
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
    ) -> Skill:
        """Lint, parse and store ``code`` as the next version of ``name``.

        Refused (``LintError``) if the name is malformed, the description empty, the code
        hard-codes an id, does not parse as Starlark, or defines no ``on_decision``. Saving code
        identical to the current version returns that version rather than minting a new one.
        """
        if not _NAME.match(name):
            raise LintError(f"skill name {name!r} must be lower_snake_case, at most 64 chars")
        if not description.strip():
            raise LintError("a skill needs a description; it is what retrieval matches")
        problems = lint(code)
        if problems:
            raise LintError("; ".join(problems))
        _parse(name, code)
        digest = content_hash(code)
        with self._lock:
            versions = self._versions.setdefault(name, [])
            if versions and versions[-1].hash == digest:
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
            )
            versions.append(skill)
            if self.root is not None:
                path = self.root / name / f"v{skill.version}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                data = asdict(skill)
                data["surfaces"] = list(skill.surfaces)
                path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            return skill

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

    def retrieve(self, description: str, surface_id: str | None = None, k: int = 5) -> list[Skill]:
        """The latest versions whose descriptions best match ``description``, best first.

        Filtered by applicability before ranking, as §5.4 specifies. A skill sharing no word with
        the query is not returned at all.
        """
        query = _words(description)
        with self._lock:
            latest = [v[-1] for v in self._versions.values() if v]
        scored = []
        for skill in latest:
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
    if not re.search(rf"^def\s+{ENTRY_DECISION}\s*\(", code, re.MULTILINE):
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


class SkillBrain:
    """Answers a decision from the skill library, or hands it to ``fallback`` untouched.

    For each raised decision: retrieve the applicable skills by description, run each in rank
    order until one answers, and return that answer as ordinary :class:`Orders`. A skill that
    returns ``None`` falls through to the next; if none answers, ``fallback.decide`` gets the
    original world view and its answer is returned as-is.
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
    ) -> None:
        self.store = store
        self.fallback = fallback
        self.runner = runner or SkillRunner()
        self.queue = queue
        self.orders = orders
        self.k = k
        #: The skill that answered the last decision, or ``None`` if the fallback did.
        self.last_skill: Skill | None = None
        #: Queue and order effects from the last decision, converted and checked, whether or not
        #: they could be applied (no queue or channel configured).
        self.last_effects: list[QueuedAnswer | str] = []

    def decide(self, world_view: WorldView) -> Orders:
        self.last_skill = None
        self.last_effects = []
        candidates = self.store.retrieve(
            decision_query(world_view), surface_id=world_view.surface_id, k=self.k
        )
        if candidates:
            view = world_view.model_dump(mode="json", exclude_none=True)
            for skill in candidates:
                result = self.runner.run(skill.name, skill.code, ENTRY_DECISION, view, skill.params)
                if not result.ok:
                    raise SkillError(
                        f"skill {skill.ref} failed closed ({result.failure}): {result.detail}"
                    )
                self._apply_effects(skill, result, world_view)
                orders = _orders_from(skill, result.answer)
                if orders is not None:
                    self.last_skill = skill
                    return orders
        return self.fallback.decide(world_view)

    def _apply_effects(self, skill: Skill, result: RunResult, world_view: WorldView) -> None:
        converted: list[QueuedAnswer | str] = []
        for effect in result.effects:
            try:
                if effect.get("kind") == "queue":
                    converted.append(_queued_from(skill, effect, world_view))
                elif effect.get("kind") == "order":
                    args = effect.get("args") or []
                    if not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
                        raise ValueError(f"order arguments must be integers, got {args!r}")
                    converted.append(build_command(str(effect.get("verb")), list(args)))
                else:
                    raise ValueError(f"unknown effect {effect.get('kind')!r}")
            except (ValueError, QueueError, TypeError) as exc:
                raise SkillError(f"skill {skill.ref} produced an invalid effect: {exc}") from exc
        self.last_effects.extend(converted)
        for item in converted:
            if isinstance(item, QueuedAnswer) and self.queue is not None:
                self.queue.install(item)
            elif isinstance(item, str) and self.orders is not None:
                self.orders.issue(item)


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
