"""The skill library in Quipu: seat books, a pinned commons and publication at game end.

``docs/seats-and-policy.md`` §3.2.1, §5.4 and §5.5. Four parts, each owning one rule:

- :class:`SkillBackend` — **a store that refuses bad instances.** Every write is the Turtle of
  :func:`~neural_amplifier.skill_shapes.to_turtle`, checked against the pinned ``na:Skill``
  shape on the way in. A refused write raises :class:`SkillWriteRefused` and leaves nothing
  behind. Two implementations: :class:`QpBackend` drives the ``qp`` CLI (an argv list, no
  shell, always ``--db``), and :class:`FakeBackend` is an in-memory stand-in for CI with the
  same transaction and refusal semantics, checking the merged post-write state as ``qp`` does.
- :class:`QuipuSkillStore` — **the same store, on Quipu, append-only.** It is a
  :class:`~neural_amplifier.skills.SkillStore` with the same interface and behaviour. A version
  is the node ``skill/<name>/v<version>`` (the content hash is a property), written once; a
  trial, promotion or retirement is a new immutable ``na:SkillState``, and readers take the
  latest state per version. Every write is one knot and nothing is retracted.
- :class:`CommonsView` — **the commons as a game sees it**: read-only, pinned to a transaction,
  entries keyed by ``(seat, name, version)`` and resolved deterministically (verified first, then
  the latest publication, then seat id). Only states a committed ``na:Publication`` carries count.
- :class:`SkillLibrary` — **one book per seat identity, one commons for everyone.** A game pins
  the commons to the transaction current when it started; a seat's :class:`SeatSkills` reads its
  own book and that pinned commons, writes only its own book, and has no way to name another
  seat's book.
- :meth:`SkillLibrary.publish_at_game_end` — **sharing is a recorded step.** When a game is
  closed, the seat's new versions and changed states go to the commons in one knot, with the
  publication record that lists exactly those states. An open or abandoned game publishes
  nothing; publishing twice is a no-op.

Retrieval filters by status, required knobs and applicability first, then ranks by description:
the backend's search when it has one, otherwise the lexical rank of ``skills.py``.
"""

from __future__ import annotations

import contextlib
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

from .skill_shapes import (
    NA,
    PREFIX,
    SHAPES_PATH,
    SKILL_BASE,
    shapes_bytes,
    to_turtle,
    trial_turtle,
    version_iri,
)
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
    """The four operations a Quipu-backed store needs. Transactions are opaque increasing ints.

    There is no retract: the store is append-only (``qp knot`` cannot retract and assert in one
    transaction, so a retract-then-assert could never be one recorded step).
    """

    def validate(self, turtle: str) -> None:
        """Raise :class:`SkillWriteRefused` if ``turtle`` fails the shape. Writes nothing."""

    def knot(self, turtle: str) -> int:
        """Assert ``turtle``, shape-checked, as one transaction; refused writes leave nothing."""

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


# A tokenizer for the Turtle this module writes: to_turtle's output and the state and publication
# records.
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
Fact = tuple[str, str, str, Kind]


def _parse_turtle(text: str) -> list[Fact]:
    """Parse the restricted Turtle this module writes into typed triples. Raises on anything else."""
    prefixes: dict[str, str] = {}
    out: list[Fact] = []
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

Rule = tuple[str, Kind, tuple[int, int | None], Callable[[str], bool]]

# (predicate, kind, (min, max), check) per class: the constraints of na-skill.shapes.ttl. "Delta"
# is the sh:node shape of a trial's na:delta objects rather than a target class.
_RULES: dict[str, list[Rule]] = {
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
    "Delta": [
        ("metric", "str", _ONE, lambda v: True),
        ("value", "dec", _ONE, lambda v: True),
    ],
    "SkillState": [
        ("ofVersion", "iri", _ONE, lambda v: True),
        ("status", "str", _ONE, lambda v: v in STATUSES),
        ("retiredReason", "str", _OPT, lambda v: len(v) >= 1),
        ("trial", "iri", _ANY, lambda v: True),
        ("tx", "int", _ONE, lambda v: int(v) >= 1),
    ],
    "Publication": [
        ("publishedBy", "str", _ONE, lambda v: len(v) >= 1),
        ("game", "str", _ONE, lambda v: len(v) >= 1),
        ("tx", "int", _ONE, lambda v: int(v) >= 1),
        ("carriesState", "iri", _ANY, lambda v: True),
    ],
}

#: sh:class constraints: (class, predicate) -> the class every object must have. A state's and a
#: publication's references cross transactions, so the shape gives them sh:nodeKind only.
_CLASSES = {
    ("Skill", "successCheck"): "SuccessCheck",
    ("Skill", "trial"): "Trial",
}


def check_facts(facts: Iterable[Fact]) -> None:
    """Apply the ``na-skill`` constraints to a whole graph of typed facts, as SHACL would.

    Run over a submitted document, it is ``qp``'s shape check; run over the MERGED state (what
    a store would hold after the write), it catches a second value knotted onto an existing
    node, which ``qp`` refuses at ingest.
    """
    by_subject: dict[str, list[tuple[str, str, Kind]]] = {}
    for s, p, v, k in facts:
        by_subject.setdefault(s, []).append((p, v, k))
    types = {s: {v for p, v, _ in props if p == RDF_TYPE} for s, props in by_subject.items()}
    deltas = {v for props in by_subject.values() for p, v, _ in props if p == f"{NA}delta"}
    problems: list[str] = []
    for s, props in by_subject.items():
        classes = {cls for cls in _RULES if f"{NA}{cls}" in types[s]}
        if s in deltas:
            classes.add("Delta")
        for cls in sorted(classes):
            for pred, kind, (lo, hi), ok in _RULES[cls]:
                values = [(v, k) for p, v, k in props if p == f"{NA}{pred}"]
                if len(values) < lo or (hi is not None and len(values) > hi):
                    problems.append(f"{s}: na:{pred} count {len(values)}")
                need = _CLASSES.get((cls, pred))
                for v, k in values:
                    if k != kind or not ok(v):
                        problems.append(f"{s}: na:{pred} value {v!r} violates the shape")
                    elif need is not None and f"{NA}{need}" not in types.get(v, set()):
                        problems.append(f"{s}: na:{pred} {v} is not a na:{need}")
    if problems:
        raise SkillWriteRefused("shape check failed: " + "; ".join(problems[:8]))


def fake_validate(turtle: str) -> None:
    """The fake lane's shape check of one standalone document. The qp lane is the authority."""
    check_facts(_parse_turtle(turtle))


class FakeBackend:
    """An in-memory store with transactions, shape-checked writes and optional search.

    Transaction ``n`` is the state after the ``n``-th write; ``0`` is empty. A write is checked
    twice, as ``qp knot --shapes`` (0.19.5) does: the shape over the submitted document, then
    over the state as the store would hold it AFTER the write (the merged state), so a second
    ``na:status`` on an existing version is refused. A refused write leaves nothing. ``search``, when given, is called as
    ``search(query, limit, triples)`` and returns subject IRIs best first.
    """

    def __init__(self, search: Callable[[str, int, list[Triple]], list[str]] | None = None) -> None:
        self._states: list[frozenset[Fact]] = [frozenset()]
        self._lock = threading.Lock()
        if search is not None:
            self.search = lambda query, limit: search(query, limit, self.triples())

    def validate(self, turtle: str) -> None:
        fake_validate(turtle)

    def knot(self, turtle: str) -> int:
        facts = frozenset(_parse_turtle(turtle))
        check_facts(facts)
        with self._lock:
            merged = self._states[-1] | facts
            check_facts(merged)
            self._states.append(merged)
            return len(self._states) - 1

    def head(self) -> int:
        with self._lock:
            return len(self._states) - 1

    def triples(self, tx: int | None = None) -> list[Triple]:
        with self._lock:
            state = self._states[-1] if tx is None else self._states[tx]
        return sorted({(s, p, v, k == "iri") for s, p, v, k in state})


# --- decoding -----------------------------------------------------------------------------------

Props = dict[str, dict[str, list[str]]]


def _props(triples: Iterable[Triple]) -> Props:
    props: Props = {}
    for s, p, v, _ in triples:
        props.setdefault(s, {}).setdefault(p, []).append(v)
    return props


def _one(node: dict[str, list[str]], pred: str) -> str | None:
    values = node.get(f"{NA}{pred}") or []
    return values[0] if values else None


def _many(node: dict[str, list[str]], pred: str) -> list[str]:
    return node.get(f"{NA}{pred}") or []


def _is(node: dict[str, list[str]], cls: str) -> bool:
    return f"{NA}{cls}" in node.get(RDF_TYPE, [])


def _last_tx(props: Props) -> int:
    """The largest ``na:tx`` of any state or publication, ``0`` when there is none."""
    return max((int(v) for node in props.values() for v in _many(node, "tx")), default=0)


def _trials(props: Props, iris: Iterable[str]) -> tuple[Trial, ...]:
    out = []
    for t_iri in sorted(iris, key=lambda i: int(i.rsplit("/", 1)[1])):
        t = props.get(t_iri, {})
        deltas = {}
        for d_iri in _many(t, "delta"):
            d = props.get(d_iri, {})
            deltas[_one(d, "metric") or ""] = float(_one(d, "value") or "nan")
        seed = _one(t, "seed")
        out.append(
            Trial(
                source=_one(t, "source") or "",
                seed=int(seed) if seed is not None else None,  # type: ignore[arg-type]
                verdict=_one(t, "verdict"),  # type: ignore[arg-type]
                deltas=dict(sorted(deltas.items())),
            )
        )
    return tuple(out)


def _version(props: Props, iri: str) -> Skill:
    """The ``na:Skill`` node at ``iri`` as it was written (its own status and trials)."""
    node = props[iri]
    check = None
    check_iri = _one(node, "successCheck")
    if check_iri is not None:
        c = props.get(check_iri, {})
        check = SuccessCheck(
            metric=_one(c, "metric") or "",
            comparator=_one(c, "comparator") or "",
            target=float(_one(c, "target") or "nan"),
            horizon_turns=int(_one(c, "horizonTurns") or 0),
        )
    supersedes = _one(node, "supersedes")
    skill = Skill(
        name=_one(node, "name") or "",
        version=int(_one(node, "version") or 0),
        description=_one(node, "description") or "",
        code=_one(node, "code") or "",
        hash=_one(node, "contentHash") or "",
        surfaces=tuple(sorted(_many(node, "requiresSurface"))),
        params=json.loads(_one(node, "params") or "{}"),
        supersedes=None if supersedes is None else _one(props.get(supersedes, {}), "contentHash"),
        requires=tuple(sorted(_many(node, "requiresKnob"))),
        status=_one(node, "status") or "candidate",  # type: ignore[arg-type]
        trials=_trials(props, _many(node, "trial")),
        check=check,
        retired_reason=_one(node, "retiredReason"),
    )
    if content_hash(skill.code) != skill.hash:
        raise ValueError(f"{iri}: stored hash does not match its code")
    return skill


@dataclass(frozen=True)
class _State:
    iri: str
    version: str
    tx: int


def _states(props: Props, carried: set[str] | None = None) -> dict[str, _State]:
    """The latest state per version IRI; with ``carried``, only states listed there count."""
    latest: dict[str, _State] = {}
    for s, node in props.items():
        if not _is(node, "SkillState") or (carried is not None and s not in carried):
            continue
        version, tx = _one(node, "ofVersion"), _one(node, "tx")
        if version is None or tx is None:
            continue
        state = _State(s, version, int(tx))
        if version not in latest or state.tx > latest[version].tx:
            latest[version] = state
    return latest


def _apply(props: Props, skill: Skill, state: _State) -> Skill:
    node = props[state.iri]
    return replace(
        skill,
        status=_one(node, "status") or skill.status,  # type: ignore[arg-type]
        retired_reason=_one(node, "retiredReason"),
        trials=_trials(props, _many(node, "trial")),
    )


def skills_from_triples(triples: Iterable[Triple]) -> list[Skill]:
    """A seat book: every ``na:Skill`` version, at its latest ``na:SkillState``.

    Each write to a book is one knot that commits its own state, so the latest state (highest
    ``na:tx``) per version wins; a version with no state reads as it was written. Ordered by
    name and version. RDF keeps no order among repeated values, so surfaces come back sorted;
    trials keep their recorded order through their indexed IRIs.
    """
    props = _props(triples)
    latest = _states(props)
    out = []
    for iri, node in props.items():
        if _is(node, "Skill"):
            skill = _version(props, iri)
            out.append(_apply(props, skill, latest[iri]) if iri in latest else skill)
    out.sort(key=lambda k: (k.name, k.version))
    return out


@dataclass(frozen=True)
class CommonsEntry:
    """One commons entry. Its identity is ``(seat, skill.name, skill.version)``."""

    seat: str
    skill: Skill
    iri: str
    #: ``na:tx`` of the publication that carried the entry's current state.
    tx: int

    def order(self) -> tuple[bool, int, str, int]:
        """Commons precedence: verified first, then the latest publication, then seat id."""
        return (self.skill.status != "verified", -self.tx, self.seat, -self.skill.version)


def commons_from_triples(triples: Iterable[Triple]) -> list[CommonsEntry]:
    """The commons: per (seat, name, version), the latest state a committed publication carries.

    States no ``na:Publication`` lists in ``na:carriesState`` are ignored, and so is a version
    with no carried state. Ordered by seat, name and version.
    """
    props = _props(triples)
    carried = {
        iri for node in props.values() if _is(node, "Publication")
        for iri in _many(node, "carriesState")
    }  # fmt: skip
    latest = _states(props, carried)
    out = []
    for iri, state in latest.items():
        node = props.get(iri, {})
        seat = _one(node, "authoredBy")
        if not _is(node, "Skill") or seat is None:
            continue
        out.append(CommonsEntry(seat, _apply(props, _version(props, iri), state), iri, state.tx))
    out.sort(key=lambda e: (e.seat, e.skill.name, e.skill.version))
    return out


# --- writing ------------------------------------------------------------------------------------


def _state_iri(version: str, tx: int) -> str:
    return f"{version}/state/{tx}"


def _state_turtle(version: str, skill: Skill, tx: int) -> tuple[str, str]:
    """A new ``na:SkillState`` for the version at ``version``: its IRI and its Turtle."""
    iri = _state_iri(version, tx)
    lines = [
        f"<{iri}> a na:SkillState",
        f"na:ofVersion <{version}>",
        f"na:status {json.dumps(skill.status)}",
    ]
    if skill.retired_reason is not None:
        lines.append(f"na:retiredReason {json.dumps(skill.retired_reason, ensure_ascii=False)}")
    lines += [f"na:trial <{version}/trial/{i}>" for i in range(len(skill.trials))]
    lines.append(f'na:tx "{int(tx)}"^^<{_XSD}integer>')
    return iri, PREFIX + " ;\n    ".join(lines) + " .\n"


def _new_trials(version: str, before: Sequence[Trial], after: Sequence[Trial]) -> str:
    """Turtle for the trials in ``after`` beyond ``before``. Trials are only ever appended."""
    if tuple(after[: len(before)]) != tuple(before):
        raise SkillWriteRefused(
            f"{version}: recorded trials are immutable; only appends are written"
        )
    blocks = [b for i in range(len(before), len(after)) for b in trial_turtle(version, i, after[i])]
    return PREFIX + "\n\n".join(blocks) + "\n" if blocks else ""


def _version_doc(skill: Skill, prefix: str, authored_by: str | None, tx: int) -> tuple[str, str]:
    """A new version written once, with its trials and its first state: (state IRI, Turtle)."""
    version = version_iri(skill.name, skill.version, prefix)
    node = to_turtle(replace(skill, trials=()), authored_by=authored_by, prefix=prefix)
    state, state_ttl = _state_turtle(version, skill, tx)
    return state, node + _new_trials(version, (), skill.trials) + state_ttl


def _update_doc(version: str, before: Skill, after: Skill, tx: int) -> tuple[str, str]:
    """A changed status, trial or retirement: new trials plus a new state, (state IRI, Turtle)."""
    state, state_ttl = _state_turtle(version, after, tx)
    return state, _new_trials(version, before.trials, after.trials) + state_ttl


# --- ranking ------------------------------------------------------------------------------------


def _book_iri(skill: Skill) -> str:
    return version_iri(skill.name, skill.version)


def rank(
    skills: Sequence[Skill],
    description: str,
    k: int,
    search: Callable[[str, int], list[str]] | None = None,
    iri_of: Callable[[Skill], str] = _book_iri,
) -> list[Skill]:
    """Order already-filtered ``skills`` by ``description``, best first, at most ``k``.

    With ``search``, its order decides (matched through ``iri_of``) and a skill it does not
    return is dropped. Without it, the lexical rank of ``skills.py``: shared words, ties by name,
    no shared word no result.
    """
    if search is not None:
        hits = search(description, max(k, len(skills)) * 4)
        position = {iri: i for i, iri in reversed(list(enumerate(hits)))}
        found = [s for s in skills if iri_of(s) in position]
        found.sort(key=lambda s: (position[iri_of(s)], s.name))
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
    """A :class:`SkillStore` whose versions live in a :class:`SkillBackend`, append-only.

    Same methods, same refusals, same results; the in-memory index is loaded from the backend
    (at head, or at ``tx`` for a pinned read). A new version is one knot: the version node, its
    trials and its first ``na:SkillState``. A trial, promotion or retirement is one knot of a new
    state (and any new trial nodes); nothing is ever retracted. A write the backend refuses, or
    one that fails for any reason, raises and leaves the index as it was; the backend's own
    transaction is all or nothing, so the old state stays readable. ``read_only`` stores refuse
    every write with :class:`CommonsReadOnly`; a store opened at ``tx`` is always read-only.
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
        triples = backend.triples(tx)
        self._last_tx = _last_tx(_props(triples))
        for skill in skills_from_triples(triples):
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

    def _knot(self, doc: str) -> None:
        """One write. On any failure, re-read the store's last ``na:tx``: the write may have
        committed before the error, and the next state must not reuse its ordinal."""
        try:
            self.backend.knot(doc)
        except Exception:
            # The original error is the one to report, whatever the re-read does.
            with contextlib.suppress(Exception):
                self._last_tx = max(self._last_tx, _last_tx(_props(self.backend.triples())))
            raise
        self._last_tx += 1

    def _write(self, skill: Skill) -> None:
        """Persist a new version; on failure, take it back out of the index. Caller holds the lock."""
        self._refuse_if_read_only()
        try:
            _, doc = _version_doc(skill, SKILL_BASE, self.authored_by, self._last_tx + 1)
            self._knot(doc)
        except Exception:
            versions = self._versions.get(skill.name) or []
            if versions and versions[-1] is skill:
                versions.pop()
                if not versions:
                    del self._versions[skill.name]
            raise
        self._persisted[(skill.name, skill.version)] = skill

    def _replace(self, name: str, version: int, **changes: Any) -> Skill:
        """Write a new state first, then swap it into the index. Caller holds the lock."""
        self._refuse_if_read_only()
        versions = self._versions.get(name) or []
        for i, current in enumerate(versions):
            if current.version == version:
                updated = replace(current, **changes)
                before = self._persisted.get((name, version), current)
                _, doc = _update_doc(version_iri(name, version), before, updated, self._last_tx + 1)
                self._knot(doc)
                versions[i] = updated
                self._persisted[(name, version)] = updated
                return updated
        raise KeyError(f"no skill {name}@v{version}")

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


class CommonsView:
    """The commons as one game sees it: read-only, pinned to a transaction, deterministic.

    Entries are keyed by ``(seat, name, version)``, so one seat's entry is never another's.
    Where several entries answer the same name, :meth:`CommonsEntry.order` decides: verified
    first, then the latest publication, then seat id. Every write raises :class:`CommonsReadOnly`.
    """

    read_only = True

    def __init__(
        self,
        backend: SkillBackend,
        tx: int | None,
        min_verified_seeds: int = DEFAULT_MIN_VERIFIED_SEEDS,
    ) -> None:
        self.backend = backend
        self.tx = tx
        self.min_verified_seeds = min_verified_seeds
        self._entries = sorted(commons_from_triples(backend.triples(tx)), key=CommonsEntry.order)
        self._iris = {id(e.skill): e.iri for e in self._entries}

    def _refuse(self, *_: Any, **__: Any) -> Skill:
        raise CommonsReadOnly("the commons is read-only; it changes only by publication")

    save = record_trial = promote = retire = _refuse

    def entries(self, name: str | None = None) -> list[CommonsEntry]:
        """Every entry (or every entry for ``name``), in commons precedence."""
        return [e for e in self._entries if name is None or e.skill.name == name]

    def iri_of(self, skill: Skill) -> str | None:
        return self._iris.get(id(skill))

    def get(self, name: str, version: int | None = None) -> Skill | None:
        for e in self.entries(name):
            if version is None or e.skill.version == version:
                return e.skill
        return None

    def history(self, name: str) -> list[Skill]:
        """Every entry for ``name``, by seat then version."""
        found = sorted(self.entries(name), key=lambda e: (e.seat, e.skill.version))
        return [e.skill for e in found]

    def eligible(
        self,
        statuses: Iterable[str] = ("verified",),
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        """Per name, the first entry in commons precedence that may run, sorted by name."""
        allowed = set(statuses) - {"retired"}
        have = None if knobs is None else set(knobs)
        out: dict[str, Skill] = {}
        for e in self._entries:
            s = e.skill
            if s.name in out or s.status not in allowed:
                continue
            if have is not None and not set(s.requires) <= have:
                continue
            out[s.name] = s
        return [out[n] for n in sorted(out)]

    def retrieve(
        self,
        description: str,
        surface_id: str | None = None,
        k: int = 5,
        statuses: Iterable[str] = STATUSES,
        knobs: Iterable[str] | None = None,
    ) -> list[Skill]:
        pool = [s for s in self.eligible(statuses, knobs) if s.applies_to(surface_id)]
        return rank(pool, description, k, _search_of(self.backend), self._iri)

    def _iri(self, skill: Skill) -> str:
        return self.iri_of(skill) or ""


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


def commons_prefix(seat: str) -> str:
    """Where a seat's commons entries live: one namespace per seat identity."""
    return f"{SKILL_BASE}seat/{quote(seat, safe='')}/"


def _publication_iri(seat: str, game: str) -> str:
    return f"{PUBLICATION_BASE}{quote(seat, safe='')}/{quote(game, safe='')}"


def _publication_turtle(seat: str, game: str, tx: int, states: Sequence[str]) -> str:
    lines = [
        f"<{_publication_iri(seat, game)}> a na:Publication",
        f"na:publishedBy {json.dumps(seat, ensure_ascii=False)}",
        f"na:game {json.dumps(game, ensure_ascii=False)}",
        f'na:tx "{int(tx)}"^^<{_XSD}integer>',
    ]
    lines += [f"na:carriesState <{s}>" for s in states]
    return PREFIX + " ;\n    ".join(lines) + " .\n"


def _content(skill: Skill) -> Skill:
    """``skill`` without what a state changes: what must never differ for one identity."""
    return replace(skill, status="candidate", trials=(), retired_reason=None)


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

    def commons(self, game: str) -> CommonsView:
        """The commons as ``game`` sees it: read-only, as of the game-start transaction."""
        return CommonsView(self._commons, self.game(game).commons_tx, self.min_verified_seeds)

    def seat(self, seat: str, game: str) -> SeatSkills:
        """What a seat may touch during ``game``: its own book and the pinned commons."""
        g = self.game(game)
        if seat not in g.seats:
            raise PermissionError(f"seat {seat!r} is not seated in game {game!r}")
        return SeatSkills(self.book(seat), self.commons(game))

    def publish_at_game_end(self, seat: str, game: str) -> list[Skill]:
        """Copy ``seat``'s new or changed versions to the commons as ONE knot.

        The knot holds each new version node (under :func:`commons_prefix` for the seat), any new
        trial nodes, one new ``na:SkillState`` per new or changed entry, and the
        ``na:Publication`` that lists exactly those states. Nothing is retracted: only the seat's
        own entries ``(seat, name, version)`` can change, so no seat overwrites or downgrades
        another's. A refused or failed knot changes nothing.

        Returns what was published. Nothing is published for an open or abandoned game, or a
        seat not in it; a second call for the same seat and game publishes nothing. Status
        travels with the skill: a candidate is published as a candidate.
        """
        with self._lock:
            g = self.game(game)
            if g.state != "closed" or seat not in g.seats or seat in g.published:
                return []
            triples = self._commons.triples()
            props = _props(triples)
            if _is(props.get(_publication_iri(seat, game), {}), "Publication"):
                g.published.add(seat)
                return []
            tx = _last_tx(props) + 1
            prefix = commons_prefix(seat)
            have = {
                (e.skill.name, e.skill.version): e
                for e in commons_from_triples(triples)
                if e.seat == seat
            }
            mine = sorted(
                (s for versions in self.book(seat)._versions.values() for s in versions),
                key=lambda s: (s.name, s.version),
            )
            parts: list[str] = []
            states: list[str] = []
            fresh: list[Skill] = []
            for skill in map(_as_stored, mine):
                old = have.get((skill.name, skill.version))
                if old is None:
                    state, doc = _version_doc(skill, prefix, seat, tx)
                elif old.skill == skill:
                    continue
                elif _content(old.skill) != _content(skill):
                    raise SkillWriteRefused(
                        f"{old.iri}: the commons already holds different content for "
                        f"{seat!r} {skill.ref}; a published version is immutable"
                    )
                else:
                    state, doc = _update_doc(old.iri, old.skill, skill, tx)
                parts.append(doc)
                states.append(state)
                fresh.append(skill)
            parts.append(_publication_turtle(seat, game, tx, states))
            self._commons.knot("".join(parts))
            g.published.add(seat)
            return fresh


class SeatSkills:
    """A seat's view: its own book for reading and writing, the pinned commons for reading.

    Nothing here names another seat, so a seat cannot reach another seat's book through it.
    ``commons`` refuses every write with :class:`CommonsReadOnly`.
    """

    def __init__(self, book: QuipuSkillStore, commons: CommonsView) -> None:
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

        def iri_of(skill: Skill) -> str:
            return self.commons.iri_of(skill) or _book_iri(skill)

        if all(searches):

            def both(query: str, limit: int) -> list[str]:
                a, b = (fn(query, limit) for fn in searches)  # type: ignore[misc]
                merged = sorted(
                    {i: n for n, i in reversed([*enumerate(b), *enumerate(a)])}.items(),
                    key=lambda t: t[1],
                )
                return [i for i, _ in merged]

            return rank(pool, description, k, both, iri_of)
        return rank(pool, description, k)
