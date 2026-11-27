"""The skill library in Quipu: seat books, a pinned commons and publication at game end.

``docs/seats-and-policy.md`` §3.2.1, §5.4 and §5.5. Four parts, each owning one rule:

- :class:`SkillBackend` — **a store that refuses bad instances.** Every write is the Turtle of
  :func:`~neural_amplifier.skill_shapes.to_turtle`, checked against the pinned ``na:Skill``
  shape on the way in. A refused write raises :class:`SkillWriteRefused` and leaves nothing
  behind. Two implementations: :class:`QpBackend` drives the ``qp`` CLI (an argv list, no
  shell, always ``--db``), and :class:`FakeBackend` is an in-memory stand-in for CI with the
  same transaction and refusal semantics.
- :class:`QuipuSkillStore` — **the same store, on Quipu.** It is a
  :class:`~neural_amplifier.skills.SkillStore` with the same interface and behaviour: the
  in-memory index is loaded from the backend and every change is written through. A read-only
  store opened at a transaction is the commons as a game sees it.
- :class:`SkillLibrary` — **one book per seat identity, one commons for everyone.** A game pins
  the commons to the transaction current when it started; a seat's :class:`SeatSkills` reads its
  own book and that pinned commons, writes only its own book, and has no way to name another
  seat's book.
- :meth:`SkillLibrary.publish_at_game_end` — **sharing is a recorded step.** When a game is
  closed, the seat's new or changed versions are copied to the commons in one write, with a
  publication record. An open or abandoned game publishes nothing; publishing twice is a no-op.

Retrieval filters by status, required knobs and applicability first, then ranks by description:
the backend's search when it has one, otherwise the lexical rank of ``skills.py``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import quote, unquote

from .skill_shapes import NA, SHAPES_PATH, SKILL_BASE, shapes_bytes, to_turtle
from .skills import (
    DEFAULT_MIN_VERIFIED_SEEDS,
    STATUSES,
    Skill,
    SkillStore,
    SuccessCheck,
    Trial,
    _words,
    content_hash,
)

#: Every subject this module writes lives under this prefix.
BASE = "http://neuralamplifier.local/"
PUBLICATION_BASE = f"{BASE}publication/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_XSD = "http://www.w3.org/2001/XMLSchema#"

#: One triple as a backend returns it: subject IRI, predicate IRI, object value, object is an IRI.
Triple = tuple[str, str, str, bool]


class SkillWriteRefused(RuntimeError):
    """The backend refused a write (the shape check failed, or ``qp`` exited nonzero)."""


class CommonsReadOnly(PermissionError):
    """A write was attempted on the commons, or on any read-only view of a store."""


# --- backends -----------------------------------------------------------------------------------


@runtime_checkable
class SkillBackend(Protocol):
    """The four operations a Quipu-backed store needs. Transactions are opaque increasing ints."""

    def validate(self, turtle: str) -> None:
        """Raise :class:`SkillWriteRefused` if ``turtle`` fails the shape. Writes nothing."""

    def knot(self, turtle: str) -> int:
        """Assert ``turtle``, shape-checked, as one transaction; refused writes leave nothing."""

    def retract(self, iri: str) -> int:
        """Retract every fact whose subject is ``iri``."""

    def triples(self, tx: int | None = None) -> list[Triple]:
        """Every fact under :data:`BASE`, at head or as of transaction ``tx``."""

    def head(self) -> int:
        """The latest transaction, ``0`` for an empty store."""


def _search_of(backend: SkillBackend) -> Callable[[str, int], list[str]] | None:
    """The backend's ranked search over descriptions (IRIs, best first), if it has one."""
    search = getattr(backend, "search", None)
    return search if callable(search) else None


Runner = Callable[..., subprocess.CompletedProcess[str]]
_TX = re.compile(r"\(tx (\d+)\)")


class QpBackend:
    """A seat or commons store driven through the ``qp`` CLI.

    Every command is an argv list run with no shell, and every store command names ``--db``.
    Writes pass ``--shapes`` with the pinned shape file, so ``qp`` refuses a bad instance before
    anything is committed. Values are read through ``ENCODE_FOR_URI`` so a multi-line code
    string survives the tabular output. ``qp`` output is not copied into errors beyond its
    violation lines, because it may echo the store path back.
    """

    def __init__(
        self,
        db: str | Path,
        *,
        qp: str = "qp",
        shapes: Path = SHAPES_PATH,
        run: Runner = subprocess.run,
        timeout: float = 60.0,
    ) -> None:
        shapes_bytes(shapes)  # the pin is checked before the store is touched
        self.db = Path(db)
        self.qp = qp
        self.shapes = shapes
        self._run = run
        self.timeout = timeout

    def _call(self, *args: str) -> subprocess.CompletedProcess[str]:
        argv = [self.qp, *args, "--db", str(self.db)]
        return self._run(argv, capture_output=True, text=True, timeout=self.timeout, check=False)

    def _with_file(
        self, turtle: str, fn: Callable[[str], subprocess.CompletedProcess[str]]
    ) -> subprocess.CompletedProcess[str]:
        fd, path = tempfile.mkstemp(suffix=".ttl", prefix="na-skill-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(turtle)
            return fn(path)
        finally:
            os.unlink(path)

    @staticmethod
    def _refusal(verb: str, done: subprocess.CompletedProcess[str]) -> SkillWriteRefused:
        lines = (done.stdout or "").splitlines() + (done.stderr or "").splitlines()
        why = [ln.strip() for ln in lines if "iolation" in ln or ln.startswith("SHACL")]
        detail = "; ".join(why[:8]) if why else f"exit {done.returncode}"
        return SkillWriteRefused(f"qp {verb} refused: {detail}")

    def validate(self, turtle: str) -> None:
        done = self._with_file(
            turtle,
            lambda p: self._run(
                [self.qp, "validate", "--shapes", str(self.shapes), "--data", p],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            ),
        )
        if done.returncode != 0:
            raise self._refusal("validate", done)

    def knot(self, turtle: str) -> int:
        done = self._with_file(
            turtle, lambda p: self._call("knot", p, "--shapes", str(self.shapes))
        )
        if done.returncode != 0:
            raise self._refusal("knot", done)
        return self._tx_of(done)

    def retract(self, iri: str) -> int:
        done = self._call("retract", iri)
        if done.returncode != 0:
            raise SkillWriteRefused(f"qp retract exited {done.returncode}")
        found = _TX.search(done.stdout or "")
        return int(found.group(1)) if found else self.head()

    def _tx_of(self, done: subprocess.CompletedProcess[str]) -> int:
        found = _TX.search(done.stdout or "")
        return int(found.group(1)) if found else self.head()

    def head(self) -> int:
        if not self.db.exists():
            return 0
        done = self._call("prime", "--json")
        if done.returncode != 0:
            raise RuntimeError(f"qp prime exited {done.returncode}")
        summary = (json.loads(done.stdout).get("live") or {}).get("summary") or {}
        last = (summary.get("last_tx") or {}).get("id")
        return int(last or 0)

    def triples(self, tx: int | None = None) -> list[Triple]:
        if not self.db.exists() or tx == 0:
            return []
        sparql = (
            "SELECT ?s ?p (ENCODE_FOR_URI(STR(?o)) AS ?v) (isIRI(?o) AS ?i) "
            f'WHERE {{ ?s ?p ?o . FILTER(STRSTARTS(STR(?s), "{BASE}")) }}'
        )
        args = ["read", sparql] + ([] if tx is None else ["--tx", str(tx)])
        done = self._call(*args)
        if done.returncode != 0:
            raise RuntimeError(f"qp read exited {done.returncode}")
        out: list[Triple] = []
        for line in done.stdout.splitlines()[2:]:
            if not line.strip():
                break
            cells = line.split("\t")
            if len(cells) != 4:
                raise RuntimeError(f"qp read returned an unexpected row of {len(cells)} cells")
            s, p, v, i = cells
            out.append((s.strip("<>"), p.strip("<>"), unquote(v.strip('"')), i == "true"))
        return out


# A tokenizer for the Turtle this module writes: to_turtle's output and the publication record.
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<prefix>@prefix\s+\w*:\s*<[^>]*>\s*\.)
      | (?P<iri><[^>]*>)
      | (?P<str>"(?:[^"\\]|\\.)*")(?:\^\^<(?P<dt>[^>]*)>)?
      | (?P<pname>\w+:\w+)
      | (?P<a>a)(?=\s)
      | (?P<punct>[;.,])
    )""",
    re.VERBOSE | re.DOTALL,
)

Kind = Literal["iri", "str", "int", "dec"]


def _parse_turtle(text: str) -> list[tuple[str, str, str, Kind]]:
    """Parse the restricted Turtle this module writes into typed triples. Raises on anything else."""
    prefixes: dict[str, str] = {}
    out: list[tuple[str, str, str, Kind]] = []
    pos, terms = 0, []
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = _TOKEN.match(text, pos)
        if m is None:
            raise SkillWriteRefused(f"unparseable Turtle at offset {pos}")
        pos = m.end()
        if m.group("prefix"):
            name, iri = re.match(r"@prefix\s+(\w*):\s*<([^>]*)>", m.group("prefix")).groups()  # type: ignore[union-attr]
            prefixes[name] = iri
            continue
        terms.append(m)
    subject = predicate = None
    kind: Kind
    for m in terms:
        if m.group("punct") == ".":
            subject = predicate = None
            continue
        if m.group("punct") == ";":
            predicate = None
            continue
        if m.group("punct") == ",":
            continue
        if m.group("a"):
            value, kind = RDF_TYPE, "iri"
        elif m.group("iri"):
            value, kind = m.group("iri")[1:-1], "iri"
        elif m.group("pname"):
            pfx, local = m.group("pname").split(":", 1)
            if pfx not in prefixes:
                raise SkillWriteRefused(f"undeclared prefix {pfx!r}")
            value, kind = prefixes[pfx] + local, "iri"
        else:
            value = json.loads(m.group("str"))
            dt = m.group("dt")
            kinds: dict[str | None, Kind] = {
                None: "str",
                f"{_XSD}integer": "int",
                f"{_XSD}decimal": "dec",
            }
            if dt not in kinds:
                raise SkillWriteRefused(f"unsupported datatype {dt}")
            kind = kinds[dt]
        if subject is None:
            subject = value
        elif predicate is None:
            predicate = value
        else:
            out.append((subject, predicate, value, kind))
    return out


_ONE = (1, 1)
_OPT = (0, 1)
_ANY = (0, None)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KNOB = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_METRICS = {
    "base_count", "drone_total", "energy_income", "energy_reserves", "factions_at_war",
    "labs_output", "military_units", "mineral_surplus", "minerals_remaining", "pop_size",
    "pop_total", "turns_to_completion", "units_in_foreign_territory",
}  # fmt: skip

# (predicate, kind, (min, max), check) per class: the constraints of na-skill.shapes.ttl.
_RULES: dict[str, list[tuple[str, Kind, tuple[int, int | None], Callable[[str], bool]]]] = {
    "Skill": [
        ("name", "str", _ONE, lambda v: bool(_SKILL_NAME.match(v))),
        ("version", "int", _ONE, lambda v: int(v) >= 1),
        ("description", "str", _ONE, lambda v: len(v) >= 1),
        ("code", "str", _ONE, lambda v: True),
        ("contentHash", "str", _ONE, lambda v: bool(_HEX64.match(v))),
        ("params", "str", _ONE, lambda v: v.startswith("{") and v.endswith("}")),
        ("status", "str", _ONE, lambda v: v in STATUSES),
        ("retiredReason", "str", _OPT, lambda v: len(v) >= 1),
        ("supersedes", "iri", _OPT, lambda v: True),
        ("requiresSurface", "str", _ANY, lambda v: len(v) >= 1),
        ("requiresKnob", "str", _ANY, lambda v: bool(_KNOB.match(v))),
        ("successCheck", "iri", _OPT, lambda v: True),
        ("trial", "iri", _ANY, lambda v: True),
        ("authoredBy", "str", _OPT, lambda v: True),
    ],
    "SuccessCheck": [
        ("metric", "str", _ONE, lambda v: v in _METRICS),
        ("comparator", "str", _ONE, lambda v: v in ("at_least", "at_most")),
        ("target", "dec", _ONE, lambda v: True),
        ("horizonTurns", "int", _ONE, lambda v: int(v) >= 1),
    ],
    "Trial": [
        ("source", "str", _ONE, lambda v: len(v) >= 1),
        ("seed", "int", _ONE, lambda v: True),
        ("verdict", "str", _ONE, lambda v: v in ("pass", "fail")),
    ],
}


def fake_validate(turtle: str) -> None:
    """The fake lane's shape check: the ``na-skill`` constraints, applied in Python.

    The qp lane is the authority (it runs the real SHACL shape); this mirrors it closely enough
    that CI refuses the same bad instances without ``qp`` installed.
    """
    facts = _parse_turtle(turtle)
    by_subject: dict[str, list[tuple[str, str, Kind]]] = {}
    for s, p, v, k in facts:
        by_subject.setdefault(s, []).append((p, v, k))
    problems: list[str] = []
    for s, props in by_subject.items():
        types = {v for p, v, _ in props if p == RDF_TYPE}
        for cls, rules in _RULES.items():
            if f"{NA}{cls}" not in types:
                continue
            for pred, kind, (lo, hi), ok in rules:
                values = [(v, k) for p, v, k in props if p == f"{NA}{pred}"]
                if len(values) < lo or (hi is not None and len(values) > hi):
                    problems.append(f"{s}: na:{pred} count {len(values)}")
                for v, k in values:
                    if k != kind or not ok(v):
                        problems.append(f"{s}: na:{pred} value {v!r} violates the shape")
    if problems:
        raise SkillWriteRefused("shape check failed: " + "; ".join(problems[:8]))


class FakeBackend:
    """An in-memory store with transactions, shape-checked writes and optional search.

    Transaction ``n`` is the state after the ``n``-th write; ``0`` is empty. ``search``, when
    given, is called as ``search(query, limit, triples)`` and returns subject IRIs best first.
    """

    def __init__(
        self,
        validate: Callable[[str], None] = fake_validate,
        search: Callable[[str, int, list[Triple]], list[str]] | None = None,
    ) -> None:
        self._validate = validate
        self._states: list[frozenset[Triple]] = [frozenset()]
        self._lock = threading.Lock()
        if search is not None:
            self.search = lambda query, limit: search(query, limit, self.triples())

    def validate(self, turtle: str) -> None:
        self._validate(turtle)

    def knot(self, turtle: str) -> int:
        self._validate(turtle)
        facts = {(s, p, v, k == "iri") for s, p, v, k in _parse_turtle(turtle)}
        with self._lock:
            self._states.append(self._states[-1] | facts)
            return len(self._states) - 1

    def retract(self, iri: str) -> int:
        with self._lock:
            self._states.append(frozenset(t for t in self._states[-1] if t[0] != iri))
            return len(self._states) - 1

    def head(self) -> int:
        with self._lock:
            return len(self._states) - 1

    def triples(self, tx: int | None = None) -> list[Triple]:
        with self._lock:
            state = self._states[-1] if tx is None else self._states[tx]
        return sorted(state)


# --- decoding -----------------------------------------------------------------------------------


def _node_iris(skill: Skill) -> list[str]:
    """Every subject :func:`to_turtle` writes for ``skill``, as bare IRIs."""
    base = f"{SKILL_BASE}{skill.name}/{skill.hash}"
    out = [base]
    if skill.check is not None:
        out.append(f"{base}/success")
    for i, trial in enumerate(skill.trials):
        out.append(f"{base}/trial/{i}")
        out += [f"{base}/trial/{i}/delta/{j}" for j in range(len(trial.deltas))]
    return out


def skills_from_triples(triples: Iterable[Triple]) -> list[Skill]:
    """Rebuild every ``na:Skill`` version in ``triples``, ordered by name, version and hash.

    RDF keeps no order among repeated values, so surfaces come back sorted; trials keep their
    recorded order through their indexed IRIs.
    """
    props: dict[str, dict[str, list[str]]] = {}
    for s, p, v, _ in triples:
        props.setdefault(s, {}).setdefault(p, []).append(v)

    def one(node: dict[str, list[str]], pred: str) -> str | None:
        values = node.get(f"{NA}{pred}") or []
        return values[0] if values else None

    def many(node: dict[str, list[str]], pred: str) -> list[str]:
        return node.get(f"{NA}{pred}") or []

    out = []
    for s, node in props.items():
        if f"{NA}Skill" not in node.get(RDF_TYPE, []):
            continue
        check = None
        check_iri = one(node, "successCheck")
        if check_iri is not None:
            c = props.get(check_iri, {})
            check = SuccessCheck(
                metric=one(c, "metric") or "",
                comparator=one(c, "comparator") or "",
                target=float(one(c, "target") or "nan"),
                horizon_turns=int(one(c, "horizonTurns") or 0),
            )
        trials = []
        for t_iri in sorted(many(node, "trial"), key=lambda i: int(i.rsplit("/", 1)[1])):
            t = props.get(t_iri, {})
            deltas = {}
            for d_iri in many(t, "delta"):
                d = props.get(d_iri, {})
                deltas[one(d, "metric") or ""] = float(one(d, "value") or "nan")
            seed = one(t, "seed")
            trials.append(
                Trial(
                    source=one(t, "source") or "",
                    seed=int(seed) if seed is not None else None,  # type: ignore[arg-type]
                    verdict=one(t, "verdict"),  # type: ignore[arg-type]
                    deltas=dict(sorted(deltas.items())),
                )
            )
        supersedes = one(node, "supersedes")
        skill = Skill(
            name=one(node, "name") or "",
            version=int(one(node, "version") or 0),
            description=one(node, "description") or "",
            code=one(node, "code") or "",
            hash=one(node, "contentHash") or "",
            surfaces=tuple(sorted(many(node, "requiresSurface"))),
            params=json.loads(one(node, "params") or "{}"),
            supersedes=None if supersedes is None else supersedes.rsplit("/", 1)[1],
            requires=tuple(sorted(many(node, "requiresKnob"))),
            status=one(node, "status") or "candidate",  # type: ignore[arg-type]
            trials=tuple(trials),
            check=check,
            retired_reason=one(node, "retiredReason"),
        )
        if content_hash(skill.code) != skill.hash:
            raise ValueError(f"{s}: stored hash does not match its code")
        out.append(skill)
    out.sort(key=lambda k: (k.name, k.version, k.hash))
    return out


# --- ranking ------------------------------------------------------------------------------------


def _iri(skill: Skill) -> str:
    return f"{SKILL_BASE}{skill.name}/{skill.hash}"


def rank(
    skills: Sequence[Skill],
    description: str,
    k: int,
    search: Callable[[str, int], list[str]] | None = None,
) -> list[Skill]:
    """Order already-filtered ``skills`` by ``description``, best first, at most ``k``.

    With ``search``, its order decides and a skill it does not return is dropped. Without it,
    the lexical rank of ``skills.py``: shared words, ties by name, no shared word no result.
    """
    if search is not None:
        hits = search(description, max(k, len(skills)) * 4)
        position = {iri: i for i, iri in reversed(list(enumerate(hits)))}
        found = [s for s in skills if _iri(s) in position]
        found.sort(key=lambda s: (position[_iri(s)], s.name))
        return found[:k]
    query = _words(description)
    scored = []
    for skill in skills:
        score = len(query & _words(skill.description))
        if score:
            scored.append((-score, skill.name, skill))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in scored[:k]]


# --- the store ----------------------------------------------------------------------------------


class QuipuSkillStore(SkillStore):
    """A :class:`SkillStore` whose versions live in a :class:`SkillBackend`.

    Same methods, same refusals, same results; the in-memory index is loaded from the backend
    (at head, or at ``tx`` for a pinned read) and every new or changed version is written through
    :func:`to_turtle`. A write the backend refuses raises :class:`SkillWriteRefused` and leaves
    neither the backend nor the index changed. ``read_only`` stores refuse every write with
    :class:`CommonsReadOnly`; a store opened at ``tx`` is always read-only.
    """

    def __init__(
        self,
        backend: SkillBackend,
        min_verified_seeds: int = DEFAULT_MIN_VERIFIED_SEEDS,
        *,
        tx: int | None = None,
        read_only: bool = False,
        authored_by: str | None = None,
    ) -> None:
        super().__init__(None, min_verified_seeds)
        self.backend = backend
        self.tx = tx
        self.read_only = read_only or tx is not None
        self.authored_by = authored_by
        self._persisted: dict[tuple[str, int], Skill] = {}
        for skill in skills_from_triples(backend.triples(tx)):
            self._versions.setdefault(skill.name, []).append(skill)
            self._persisted[(skill.name, skill.version)] = skill

    def _refuse_if_read_only(self) -> None:
        if self.read_only:
            raise CommonsReadOnly("this skill store is read-only; a seat writes its own book")

    def save(self, *args: Any, **kwargs: Any) -> Skill:
        self._refuse_if_read_only()
        return super().save(*args, **kwargs)

    def record_trial(self, *args: Any, **kwargs: Any) -> Skill:
        self._refuse_if_read_only()
        return super().record_trial(*args, **kwargs)

    def promote(self, name: str, version: int) -> Skill:
        self._refuse_if_read_only()
        return super().promote(name, version)

    def retire(self, name: str, version: int, reason: str | None = None) -> Skill:
        self._refuse_if_read_only()
        return super().retire(name, version, reason)

    def _write(self, skill: Skill) -> None:
        """Persist a new version; on refusal, take it back out of the index. Caller holds the lock."""
        self._refuse_if_read_only()
        try:
            self.backend.knot(to_turtle(skill, authored_by=self.authored_by))
        except Exception:
            versions = self._versions.get(skill.name) or []
            if versions and versions[-1] is skill:
                versions.pop()
                if not versions:
                    del self._versions[skill.name]
            raise
        self._persisted[(skill.name, skill.version)] = skill

    def _replace(self, name: str, version: int, **changes: Any) -> Skill:
        """Write the updated version first, then swap it into the index. Caller holds the lock."""
        self._refuse_if_read_only()
        versions = self._versions.get(name) or []
        for i, current in enumerate(versions):
            if current.version == version:
                updated = replace(current, **changes)
                self._rewrite(self._persisted.get((name, version), current), updated)
                versions[i] = updated
                return updated
        raise KeyError(f"no skill {name}@v{version}")

    def _rewrite(self, old: Skill, new: Skill) -> None:
        new_ttl = to_turtle(new, authored_by=self.authored_by)
        self.backend.validate(new_ttl)  # refused here, nothing has been touched
        replace_all(self.backend, [old], new_ttl, to_turtle(old, authored_by=self.authored_by))
        self._persisted[(new.name, new.version)] = new

    def retrieve(
        self,
        description: str,
        surface_id: str | None = None,
        k: int = 5,
        statuses: Iterable[str] = STATUSES,
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        search = _search_of(self.backend)
        if search is None:
            return super().retrieve(description, surface_id, k, statuses, knobs)
        pool = [s for s in self.eligible(statuses, knobs) if s.applies_to(surface_id)]
        return rank(pool, description, k, search)


def replace_all(
    backend: SkillBackend, old: Iterable[Skill], new_ttl: str, restore_ttl: str | None
) -> int:
    """Retract ``old`` versions' nodes and knot ``new_ttl``; on a refused knot, restore and raise."""
    for skill in old:
        for iri in _node_iris(skill):
            backend.retract(iri)
    try:
        return backend.knot(new_ttl)
    except Exception:
        if restore_ttl:
            backend.knot(restore_ttl)
        raise


# --- seats, games and the commons ---------------------------------------------------------------


GameState = Literal["open", "closed", "abandoned"]


@dataclass
class Game:
    game_id: str
    #: The commons transaction current when the game started; every seat reads it as of this.
    commons_tx: int
    seats: frozenset[str]
    state: GameState = "open"
    published: set[str] = field(default_factory=set)


def _as_stored(skill: Skill) -> Skill:
    """``skill`` as a read back from a store gives it: RDF keeps surfaces as a set."""
    return replace(skill, surfaces=tuple(sorted(set(skill.surfaces))))


def _check_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"a {kind} id must be a nonempty string, not {value!r}")
    return value


def _publication_iri(seat: str, game: str) -> str:
    return f"{PUBLICATION_BASE}{quote(seat, safe='')}/{quote(game, safe='')}"


def _publication_turtle(seat: str, game: str, skills: Sequence[Skill]) -> str:
    lines = [
        f"<{_publication_iri(seat, game)}> a <{NA}Publication> ;",
        f"    <{NA}publishedBy> {json.dumps(seat, ensure_ascii=False)} ;",
        f"    <{NA}game> {json.dumps(game, ensure_ascii=False)}",
    ]
    lines += [f"    ; <{NA}published> <{_iri(s)}>" for s in skills]
    return "\n".join(lines) + " .\n"


class SkillLibrary:
    """Seat books and the commons (§3.2.1, §5.5), behind the API the orchestrator holds.

    ``open_book(seat_identity)`` returns that identity's own backend: one database per seat
    identity, named for the identity and not the game, so a book outlives the game. ``commons``
    is the shared backend, written only by :meth:`publish_at_game_end`.
    """

    def __init__(
        self,
        open_book: Callable[[str], SkillBackend],
        commons: SkillBackend,
        min_verified_seeds: int = DEFAULT_MIN_VERIFIED_SEEDS,
    ) -> None:
        self._open_book = open_book
        self._commons = commons
        self.min_verified_seeds = min_verified_seeds
        self._books: dict[str, QuipuSkillStore] = {}
        self._games: dict[str, Game] = {}
        self._lock = threading.RLock()

    def book(self, seat: str) -> QuipuSkillStore:
        """The seat identity's own book; the same store every time it is asked for."""
        _check_id("seat", seat)
        with self._lock:
            if seat not in self._books:
                self._books[seat] = QuipuSkillStore(
                    self._open_book(seat), self.min_verified_seeds, authored_by=seat
                )
            return self._books[seat]

    def start_game(self, game: str, seats: Iterable[str]) -> Game:
        """Open ``game`` and pin the commons to the transaction current now."""
        _check_id("game", game)
        with self._lock:
            if game in self._games:
                raise ValueError(f"game {game!r} has already started")
            started = Game(game, self._commons.head(), frozenset(map(str, seats)))
            self._games[game] = started
            return started

    def game(self, game: str) -> Game:
        try:
            return self._games[game]
        except KeyError:
            raise KeyError(f"no game {game!r}") from None

    def close_game(self, game: str) -> None:
        """End ``game`` normally (an abandoned game may still be closed later)."""
        with self._lock:
            self.game(game).state = "closed"

    def abandon_game(self, game: str) -> None:
        with self._lock:
            g = self.game(game)
            if g.state == "open":
                g.state = "abandoned"

    def commons(self, game: str) -> QuipuSkillStore:
        """The commons as ``game`` sees it: read-only, as of the game-start transaction."""
        return QuipuSkillStore(
            self._commons, self.min_verified_seeds, tx=self.game(game).commons_tx
        )

    def seat(self, seat: str, game: str) -> SeatSkills:
        """What a seat may touch during ``game``: its own book and the pinned commons."""
        g = self.game(game)
        if seat not in g.seats:
            raise PermissionError(f"seat {seat!r} is not seated in game {game!r}")
        return SeatSkills(self.book(seat), self.commons(game))

    def publish_at_game_end(self, seat: str, game: str) -> list[Skill]:
        """Copy ``seat``'s new or changed versions to the commons in one write.

        Returns what was published. Nothing is published for an open or abandoned game, or a
        seat not in it; a second call for the same seat and game publishes nothing. Status
        travels with the skill: a candidate is published as a candidate.
        """
        with self._lock:
            g = self.game(game)
            if g.state != "closed" or seat not in g.seats or seat in g.published:
                return []
            marker = _publication_iri(seat, game)
            head = self._commons.triples()
            if any(t[0] == marker for t in head):
                g.published.add(seat)
                return []
            in_commons = {(s.name, s.hash): s for s in skills_from_triples(head)}
            mine = [s for versions in self.book(seat)._versions.values() for s in versions]
            fresh = [s for s in mine if in_commons.get((s.name, s.hash)) != _as_stored(s)]
            changed = [
                in_commons[(s.name, s.hash)] for s in fresh if (s.name, s.hash) in in_commons
            ]
            doc = "".join(to_turtle(s, authored_by=seat) + "\n" for s in fresh)
            doc += _publication_turtle(seat, game, fresh)
            self._commons.validate(doc)
            restore = "".join(to_turtle(s) + "\n" for s in changed) or None
            replace_all(self._commons, changed, doc, restore)
            g.published.add(seat)
            return fresh


class SeatSkills:
    """A seat's view: its own book for reading and writing, the pinned commons for reading.

    Nothing here names another seat, so a seat cannot reach another seat's book through it.
    ``commons`` refuses every write with :class:`CommonsReadOnly`.
    """

    def __init__(self, book: QuipuSkillStore, commons: QuipuSkillStore) -> None:
        self._book = book
        self.commons = commons

    def save(self, *args: Any, **kwargs: Any) -> Skill:
        return self._book.save(*args, **kwargs)

    def record_trial(self, *args: Any, **kwargs: Any) -> Skill:
        return self._book.record_trial(*args, **kwargs)

    def promote(self, name: str, version: int) -> Skill:
        return self._book.promote(name, version)

    def retire(self, name: str, version: int, reason: str | None = None) -> Skill:
        return self._book.retire(name, version, reason)

    def get(self, name: str, version: int | None = None) -> Skill | None:
        return self._book.get(name, version)

    def history(self, name: str) -> list[Skill]:
        return self._book.history(name)

    def retrieve(
        self,
        description: str,
        surface_id: str | None = None,
        k: int = 5,
        statuses: Iterable[str] = STATUSES,
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        """Own book and pinned commons, filtered first; the seat's own version of a name wins."""
        own = self._book.eligible(statuses, knobs)
        names = {s.name for s in own}
        pool = own + [s for s in self.commons.eligible(statuses, knobs) if s.name not in names]
        pool = [s for s in pool if s.applies_to(surface_id)]
        searches = [_search_of(self._book.backend), _search_of(self.commons.backend)]
        if all(searches):

            def both(query: str, limit: int) -> list[str]:
                a, b = (fn(query, limit) for fn in searches)  # type: ignore[misc]
                merged = sorted(
                    {i: n for n, i in reversed([*enumerate(b), *enumerate(a)])}.items(),
                    key=lambda t: t[1],
                )
                return [i for i, _ in merged]

            return rank(pool, description, k, both)
        return rank(pool, description, k)
