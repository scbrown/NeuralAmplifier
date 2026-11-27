"""The campaign ledger: a strategist seat's memory across reviews (docs/seats-and-policy.md §6).

aegis-8fye.8 slice 2. One ledger per seat IDENTITY, on the same
:class:`~neural_amplifier.skill_quipu.SkillBackend` protocol as the skill books, guarded by the
pinned ``shapes/na-ledger.shapes.ttl``. Every term is in the ``na:`` namespace.

- **Append-only, one knot per write.** A write is its records plus one ``na:LedgerState``
  naming them, asserted as one transaction; nothing is ever retracted.
- **Commitments supersede by natural key.** :meth:`CampaignLedger.commit` with a key already in
  the ledger writes a new version that ``na:supersedes`` the latest one. The earlier version
  stays readable (:meth:`CampaignLedger.history`); reads take the latest version per key.
- **Reviews are written once, when their outcome is known**: ``acted`` (a directive, plan or
  policy write landed while the review was open, with the ids it produced) or ``missed``.
- **A ledger reads only its own identity.** Each identity has its own backend, and reads also
  filter on ``na:seat``, so even a misconfigured shared backend does not leak a seat's ledger.
- **A bad instance is refused at write** with :class:`~neural_amplifier.skill_quipu.SkillWriteRefused`
  and leaves nothing behind: by ``qp knot --shapes`` in the qp lane, by
  :func:`check_ledger_facts` in the fake lane.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from .skill_quipu import (
    BASE,
    RDF_TYPE,
    Fact,
    FakeBackend,
    Kind,
    QpBackend,
    SkillBackend,
    SkillWriteRefused,
    Triple,
)
from .skill_shapes import NA, ShapesError

#: The shape file, at the repository root (source checkout only, like na-skill).
LEDGER_SHAPES_PATH = Path(__file__).resolve().parents[3] / "shapes" / "na-ledger.shapes.ttl"
#: sha256 of the shape file's bytes. Change the file, change this, in the same review.
LEDGER_SHAPES_SHA256 = "93ba0ee0ffee84405a1d27a91c375e0827e30c3bf4e3c83ac1351bc56971282f"
LEDGER_SHAPES_NAME = "na-ledger"
LEDGER_BASE = f"{BASE}ledger/"
_XSD = "http://www.w3.org/2001/XMLSchema#"

COMMITMENT_STATUSES = ("open", "kept", "broken", "superseded")
TRIGGERS = ("cadence", "discontinuity")
REASONS = ("cadence", "base_lost", "war_declared")
OUTCOMES = ("acted", "missed")
VIAS = ("directive", "plan", "policy")
#: How many past reviews the strategist's review context carries.
CONTEXT_REVIEWS = 5


def ledger_shapes_bytes(path: Path = LEDGER_SHAPES_PATH) -> bytes:
    """The ledger shape's bytes, refused unless they hash to :data:`LEDGER_SHAPES_SHA256`."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != LEDGER_SHAPES_SHA256:
        raise ShapesError(f"{path} hashes to {digest}, not the pinned {LEDGER_SHAPES_SHA256}")
    return data


# --- the shape, for the fake lane ----------------------------------------------------------------

_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ONE, _OPT, _ANY, _SOME = (1, 1), (0, 1), (0, None), (1, None)
_nonempty: Callable[[str], bool] = lambda v: len(v) >= 1  # noqa: E731
_any: Callable[[str], bool] = lambda v: True  # noqa: E731
_nonneg: Callable[[str], bool] = lambda v: int(v) >= 0  # noqa: E731
_pos: Callable[[str], bool] = lambda v: int(v) >= 1  # noqa: E731

_Rule = tuple[str, Kind, tuple[int, int | None], Callable[[str], bool]]
#: The constraints of na-ledger.shapes.ttl, per target class.
_RULES: dict[str, list[_Rule]] = {
    "Commitment": [
        ("key", "str", _ONE, lambda v: bool(_KEY.match(v))),
        ("text", "str", _ONE, _nonempty),
        ("fromTurn", "int", _ONE, _nonneg),
        ("untilTurn", "int", _OPT, _nonneg),
        ("condition", "str", _OPT, _nonempty),
        ("status", "str", _ONE, lambda v: v in COMMITMENT_STATUSES),
        ("supersedes", "iri", _OPT, _any),
        ("attachedVia", "str", _OPT, lambda v: v in VIAS),
        ("attachedRef", "str", _OPT, _nonempty),
        ("seat", "str", _ONE, _nonempty),
        ("tx", "int", _ONE, _pos),
    ],
    "Review": [
        ("reviewId", "str", _ONE, _nonempty),
        ("turn", "int", _ONE, _nonneg),
        ("trigger", "str", _ONE, lambda v: v in TRIGGERS),
        ("reason", "str", _OPT, lambda v: v in REASONS),
        ("outcome", "str", _ONE, lambda v: v in OUTCOMES),
        ("producedDirective", "str", _ANY, _nonempty),
        ("producedPlan", "str", _ANY, _nonempty),
        ("producedPolicyChange", "str", _ANY, _nonempty),
        ("seat", "str", _ONE, _nonempty),
        ("tx", "int", _ONE, _pos),
    ],
    "LedgerState": [
        ("seat", "str", _ONE, _nonempty),
        ("tx", "int", _ONE, _pos),
        ("records", "iri", _SOME, _any),
    ],
}


def check_ledger_facts(facts: Iterable[Fact]) -> None:
    """Apply the ``na-ledger`` constraints to a graph of typed facts, as SHACL would."""
    by_subject: dict[str, list[tuple[str, str, Kind]]] = {}
    for s, p, v, k in facts:
        by_subject.setdefault(s, []).append((p, v, k))
    problems: list[str] = []
    for s, props in by_subject.items():
        types = {v for p, v, _ in props if p == RDF_TYPE}
        for cls in sorted(c for c in _RULES if f"{NA}{c}" in types):
            for pred, kind, (lo, hi), ok in _RULES[cls]:
                values = [(v, k) for p, v, k in props if p == f"{NA}{pred}"]
                if len(values) < lo or (hi is not None and len(values) > hi):
                    problems.append(f"{s}: na:{pred} count {len(values)}")
                for v, k in values:
                    if k != kind or not ok(v):
                        problems.append(f"{s}: na:{pred} value {v!r} violates the shape")
            if cls == "Commitment" and not any(
                p in (f"{NA}untilTurn", f"{NA}condition") for p, _, _ in props
            ):
                problems.append(f"{s}: a commitment needs na:untilTurn or na:condition")
    if problems:
        raise SkillWriteRefused("shape check failed: " + "; ".join(problems[:8]))


def fake_ledger_backend() -> FakeBackend:
    """An in-memory ledger store with the ledger's shape check (CI, and the service default)."""
    return FakeBackend(check=check_ledger_facts)


def qp_ledger_backend(
    db: str | Path, *, shapes: Path = LEDGER_SHAPES_PATH, qp: str = "qp"
) -> QpBackend:
    """A ledger store driven through ``qp``, guarded by the pinned ledger shape."""
    return QpBackend(db, qp=qp, shapes=shapes, pin=ledger_shapes_bytes)


# --- records -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Commitment:
    key: str
    text: str
    from_turn: int
    until_turn: int | None = None
    condition: str | None = None
    status: str = "open"
    via: str | None = None
    ref: str | None = None
    tx: int = 0
    iri: str = ""
    supersedes: str | None = None

    def summary(self) -> dict[str, object]:
        return {k: v for k, v in asdict(self).items() if k not in ("iri", "supersedes")}


@dataclass(frozen=True)
class ReviewRecord:
    review_id: str
    turn: int
    trigger: str
    outcome: str
    reason: str | None = None
    directives: tuple[str, ...] = ()
    plans: tuple[str, ...] = ()
    policy_changes: tuple[str, ...] = ()
    tx: int = 0

    def summary(self) -> dict[str, object]:
        out = asdict(self)
        for k in ("directives", "plans", "policy_changes"):
            out[k] = list(out[k])
        return out


def trigger_of(reason: str) -> str:
    """The shape's trigger for a scheduler reason: cadence, or a discontinuity."""
    return "cadence" if reason == "cadence" else "discontinuity"


# --- turtle --------------------------------------------------------------------------------------


def _str(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def _int(n: int) -> str:
    return f'"{int(n)}"^^<{_XSD}integer>'


def _block(iri: str, cls: str, props: list[tuple[str, str]]) -> str:
    lines = [f"<{iri}> a na:{cls}"] + [f"    na:{p} {v}" for p, v in props]
    return " ;\n".join(lines) + " .\n"


def _doc(blocks: list[str]) -> str:
    return f"@prefix na: <{NA}> .\n\n" + "\n".join(blocks)


# --- the ledger ----------------------------------------------------------------------------------


class CampaignLedger:
    """One seat identity's ledger. Has no way to name another identity's records."""

    def __init__(self, backend: SkillBackend, seat: str) -> None:
        if not isinstance(seat, str) or not seat.strip():
            raise ValueError(f"a seat identity must be a nonempty string, not {seat!r}")
        self.backend = backend
        self.seat = seat
        self.prefix = f"{LEDGER_BASE}{quote(seat, safe='')}/"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ writing

    def _commitment_block(self, c: Commitment, tx: int, supersedes: str | None) -> tuple[str, str]:
        iri = f"{self.prefix}commitment/{quote(c.key, safe='')}/tx{tx}"
        props: list[tuple[str, str]] = [
            ("key", _str(c.key)),
            ("text", _str(c.text)),
            ("fromTurn", _int(c.from_turn)),
        ]
        if c.until_turn is not None:
            props.append(("untilTurn", _int(c.until_turn)))
        if c.condition is not None:
            props.append(("condition", _str(c.condition)))
        props.append(("status", _str(c.status)))
        if supersedes:
            props.append(("supersedes", f"<{supersedes}>"))
        if c.via is not None:
            props.append(("attachedVia", _str(c.via)))
        if c.ref is not None:
            props.append(("attachedRef", _str(c.ref)))
        props += [("seat", _str(self.seat)), ("tx", _int(tx))]
        return iri, _block(iri, "Commitment", props)

    def _state_block(self, tx: int, records: list[str]) -> str:
        iri = f"{self.prefix}state/tx{tx}"
        refs = " , ".join(f"<{r}>" for r in records)
        return _block(
            iri, "LedgerState", [("seat", _str(self.seat)), ("tx", _int(tx)), ("records", refs)]
        )

    def _knot(self, build: Callable[[int], tuple[str, str]]) -> int:
        """One write: the record and its ``na:LedgerState``, as one transaction."""
        with self._lock:
            tx = self.backend.head() + 1
            iri, block = build(tx)
            return self.backend.knot(_doc([block, self._state_block(tx, [iri])]))

    @staticmethod
    def _draft(spec: dict[str, object]) -> Commitment:
        def opt_int(name: str) -> int | None:
            v = spec.get(name)
            return None if v in (None, "") else int(str(v))

        try:
            return Commitment(
                key=str(spec.get("key") or ""),
                text=str(spec.get("text") or ""),
                from_turn=int(str(spec.get("from_turn", 0))),
                until_turn=opt_int("until_turn"),
                condition=None if spec.get("condition") in (None, "") else str(spec["condition"]),
                status=str(spec.get("status") or "open"),
                via=None if spec.get("via") in (None, "") else str(spec["via"]),
                ref=None if spec.get("ref") in (None, "") else str(spec["ref"]),
            )
        except (TypeError, ValueError) as exc:
            raise SkillWriteRefused(f"not a well-formed commitment: {exc}") from exc

    def check(self, spec: dict[str, object]) -> Commitment:
        """Shape-check a commitment without writing it (so a route can refuse it up front)."""
        draft = self._draft(spec)
        iri, block = self._commitment_block(draft, 1, None)
        self.backend.validate(_doc([block, self._state_block(1, [iri])]))
        return draft

    def commit(self, spec: dict[str, object] | None = None, **kwargs: object) -> Commitment:
        """Write a commitment; one already under the same key is superseded, not overwritten."""
        draft = self._draft({**(spec or {}), **kwargs})

        def build(tx: int) -> tuple[str, str]:
            latest = self._latest().get(draft.key)
            return self._commitment_block(draft, tx, latest.iri if latest else None)

        self._knot(build)
        return self._latest()[draft.key]

    def record_review(
        self,
        review_id: str,
        turn: int,
        reason: str,
        outcome: str,
        *,
        directives: Iterable[str] = (),
        plans: Iterable[str] = (),
        policy_changes: Iterable[str] = (),
    ) -> ReviewRecord:
        """Write one review, once its outcome is known."""

        def build(tx: int) -> tuple[str, str]:
            iri = f"{self.prefix}review/{quote(review_id, safe='')}"
            props: list[tuple[str, str]] = [
                ("reviewId", _str(review_id)),
                ("turn", _int(turn)),
                ("trigger", _str(trigger_of(reason))),
                ("reason", _str(reason)),
                ("outcome", _str(outcome)),
            ]
            props += [("producedDirective", _str(d)) for d in directives]
            props += [("producedPlan", _str(p)) for p in plans]
            props += [("producedPolicyChange", _str(p)) for p in policy_changes]
            props += [("seat", _str(self.seat)), ("tx", _int(tx))]
            return iri, _block(iri, "Review", props)

        self._knot(build)
        return next(r for r in reversed(self.reviews()) if r.review_id == review_id)

    # ------------------------------------------------------------------ reading

    def _nodes(self, tx: int | None = None) -> dict[str, dict[str, list[str]]]:
        """This identity's nodes only: under its prefix AND carrying its na:seat."""
        triples: list[Triple] = self.backend.triples(tx)
        nodes: dict[str, dict[str, list[str]]] = {}
        for s, p, v, _ in triples:
            if s.startswith(self.prefix):
                nodes.setdefault(s, {}).setdefault(p, []).append(v)
        return {s: n for s, n in nodes.items() if n.get(f"{NA}seat") == [self.seat]}

    def _commitments(self) -> list[Commitment]:
        out = []
        for iri, n in self._nodes().items():
            if f"{NA}Commitment" not in n.get(RDF_TYPE, []):
                continue

            def one(p: str, n: dict[str, list[str]] = n) -> str | None:
                vs = n.get(f"{NA}{p}")
                return vs[0] if vs else None

            until = one("untilTurn")
            out.append(
                Commitment(
                    key=one("key") or "",
                    text=one("text") or "",
                    from_turn=int(one("fromTurn") or 0),
                    until_turn=None if until is None else int(until),
                    condition=one("condition"),
                    status=one("status") or "open",
                    via=one("attachedVia"),
                    ref=one("attachedRef"),
                    tx=int(one("tx") or 0),
                    iri=iri,
                    supersedes=one("supersedes"),
                )
            )
        return sorted(out, key=lambda c: (c.tx, c.key))

    def _latest(self) -> dict[str, Commitment]:
        latest: dict[str, Commitment] = {}
        for c in self._commitments():
            latest[c.key] = c
        return latest

    def commitments(self) -> list[Commitment]:
        """The latest version of every commitment, by key."""
        return sorted(self._latest().values(), key=lambda c: c.key)

    def open_commitments(self) -> list[Commitment]:
        return [c for c in self.commitments() if c.status == "open"]

    def history(self, key: str) -> list[Commitment]:
        """Every version under ``key``, oldest first; all but the last read as superseded."""
        versions = [c for c in self._commitments() if c.key == key]
        return [
            c if i == len(versions) - 1 else Commitment(**{**asdict(c), "status": "superseded"})
            for i, c in enumerate(versions)
        ]

    def reviews(self, last: int | None = None) -> list[ReviewRecord]:
        """Recorded reviews, oldest first; ``last`` keeps only the newest ``last``."""
        out = []
        for n in self._nodes().values():
            if f"{NA}Review" not in n.get(RDF_TYPE, []):
                continue

            def one(p: str, n: dict[str, list[str]] = n) -> str | None:
                vs = n.get(f"{NA}{p}")
                return vs[0] if vs else None

            out.append(
                ReviewRecord(
                    review_id=one("reviewId") or "",
                    turn=int(one("turn") or 0),
                    trigger=one("trigger") or "",
                    outcome=one("outcome") or "",
                    reason=one("reason"),
                    directives=tuple(sorted(n.get(f"{NA}producedDirective", []))),
                    plans=tuple(sorted(n.get(f"{NA}producedPlan", []))),
                    policy_changes=tuple(sorted(n.get(f"{NA}producedPolicyChange", []))),
                    tx=int(one("tx") or 0),
                )
            )
        out.sort(key=lambda r: r.tx)
        return out if last is None else out[-last:] if last > 0 else []

    def context(self, last_reviews: int = CONTEXT_REVIEWS) -> dict[str, object]:
        """What a review's prompt context carries: open commitments and the last reviews."""
        return {
            "open_commitments": [c.summary() for c in self.open_commitments()],
            "recent_reviews": [r.summary() for r in self.reviews(last_reviews)],
        }


@dataclass
class LedgerBook:
    """One ledger per seat identity, each on that identity's own backend."""

    open_backend: Callable[[str], SkillBackend] = field(default=lambda _seat: fake_ledger_backend())
    _ledgers: dict[str, CampaignLedger] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def ledger(self, seat: str) -> CampaignLedger:
        with self._lock:
            if seat not in self._ledgers:
                self._ledgers[seat] = CampaignLedger(self.open_backend(seat), seat)
            return self._ledgers[seat]
