"""Turn-boundary grounding evidence — the producer half of the Yupana advice contract.

Yupana (the renamed hank) advises on whether an NA action was grounded, and it does that
without ever contacting the graph: its hot path reads **one local content-addressed file** and
verifies four things about it. This module is what writes that file.

The contract is Yupana's ``src/grounding.rs`` and it is deliberately restated here rather than
referenced, because the two halves are in different repositories and different languages, and a
contract that lives only in the consumer is one the producer can drift away from silently:

* the file lives at ``<cache>/<hex>.json`` where ``hex`` is the **sha256 of the file's exact
  bytes**, and the reference carries ``grounding_id = "sha256:<hex>"``;
* the reference's ``faction_id`` and ``worldview_sha256`` must equal the evidence's, or Yupana
  reports ``unresolved`` — the binding is what makes replay falsifiable;
* ``outcome`` is three-valued (``used`` / ``empty`` / ``transport-error``) and never a boolean;
* evidence older than Yupana's freshness ceiling (default 300s) is ``stale``, not ``used``.

**Byte fidelity is the whole mechanism, not a detail.** Yupana re-hashes what it reads and
compares against the id. So anything that re-serialises the record between hashing and writing —
a pretty-printer, a different key order, an added trailing newline — produces a file whose digest
is not its name, and Yupana reports ``unresolved`` on evidence that is in fact perfectly good.
:func:`canonical_bytes` is therefore the single place bytes are made, and :meth:`Cache.publish`
hashes and writes *the same object*, never a re-encode.

**Why the record binds a world view at all, when consultation happens once per turn.** The graph
is primed once at ``/turn`` — a per-decision query is unsatisfiable at the measured 244
decisions per turn (na-x5n), which is why ``QuipuRetriever`` caches by ``(turn, faction_id)``.
But a *decision* is what a reader wants to audit, and the decision's input is its world view. So
one consultation fans out into one evidence file per distinct world view it grounded: the
consultation fields are shared, the binding is per-decision, and content-addressing makes the
fan-out free when two decisions share an input. No extra graph traffic, and the question "was
*this* decision grounded, and in whose fog?" stays answerable.

**Absence is reported, never faked.** A write that fails leaves the decision record with no
reference, and Yupana then says ``missing`` — which is the truth. The alternative, swallowing the
error and carrying on, is the failure mode this whole plane exists to prevent: a provenance block
asserting that something informed a decision when nothing did.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: The named graph NA agents are grounded in (aegis-cjlib).
DEFAULT_GRAPH = "urn:neuralamplifier:graph:knowledge"

#: Yupana recognises exactly one scope for this feature. Anything else is `unknown-scope`.
SCOPE = "na"

#: Yupana's three producer-side outcomes, kebab-case on the wire.
Outcome = Literal["used", "empty", "transport-error"]

#: Yupana's default freshness ceiling (`HANK_GROUNDING_MAX_AGE_SECS`, else 300).
DEFAULT_MAX_AGE_SECS = 300

#: Keep stale evidence around for a while rather than deleting it on the dot. Past the ceiling
#: Yupana already reports `stale`, which tells a reader "the consultation is old" — strictly more
#: informative than the `unresolved` they would get from a file we had tidied away. So the cache
#: is bounded by a generous multiple of the ceiling, not by the ceiling itself.
PRUNE_AGE_MULTIPLE = 10


def cache_dir() -> Path | None:
    """Where Yupana looks, resolved the way Yupana resolves it.

    Mirrors ``grounding::cache_dir`` precedence exactly — ``HANK_GROUNDING_CACHE_DIR``, then
    ``XDG_STATE_HOME/hank/grounding``, then ``HOME/.local/state/hank/grounding``. A producer
    that guessed a different directory would write correct evidence nobody reads, and the
    consumer would report ``unresolved`` forever with no hint that the two sides disagree about
    the address rather than the content.

    ``None`` means no directory can be resolved at all, which is the one case where publishing
    is genuinely impossible rather than merely failing.
    """
    explicit = os.environ.get("HANK_GROUNDING_CACHE_DIR")
    if explicit:
        return Path(explicit)
    state = os.environ.get("XDG_STATE_HOME")
    if state:
        return Path(state) / "hank" / "grounding"
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".local" / "state" / "hank" / "grounding"
    return None


def max_age_secs() -> int:
    """Yupana's freshness ceiling, read from the same env var it reads.

    A malformed value falls back to the default for the same reason Yupana does: a typo in an
    env var should not silently widen the window in which stale grounding passes as fresh.
    """
    raw = os.environ.get("HANK_GROUNDING_MAX_AGE_SECS")
    if raw is None:
        return DEFAULT_MAX_AGE_SECS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_SECS
    return value if value >= 0 else DEFAULT_MAX_AGE_SECS


@dataclass(frozen=True)
class Consultation:
    """One turn-boundary graph consultation, before it is bound to any decision.

    Immutable on purpose: this is evidence. It is captured once at ``/turn`` and then read by
    every decision in that turn, and a mutable record shared across a threaded decision loop is
    how the entities of one turn end up reported against another.
    """

    graph: str
    query: str
    entities: tuple[str, ...]
    turn: int
    outcome: Outcome
    faction_id: str
    captured_at: int

    @property
    def age_secs(self) -> int:
        return max(0, int(time.time()) - self.captured_at)


@dataclass(frozen=True)
class GroundingRef:
    """What travels with a decision so Yupana can find and verify the evidence.

    Deliberately not the evidence itself. Yupana's budget is a local file read; duplicating the
    query text and entity list into every action record would put the payload on the hot path
    it was designed to keep off it.
    """

    scope: str
    grounding_id: str
    faction_id: str
    worldview_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "grounding_id": self.grounding_id,
            "faction_id": self.faction_id,
            "worldview_sha256": self.worldview_sha256,
        }


def canonical_bytes(consultation: Consultation, worldview_sha256: str) -> bytes:
    """The exact bytes Yupana will hash. The one place evidence is serialised.

    ``sort_keys`` and the compact separators are load-bearing, not style: the digest IS the
    filename, so two producers encoding the same facts differently would write two files and
    neither would be wrong. Deterministic encoding is what makes the cache content-addressed
    rather than merely hashed.

    No trailing newline. It would be invisible in every editor and would change the digest.
    """
    payload: dict[str, Any] = {
        "graph": consultation.graph,
        "query": consultation.query,
        "entities": list(consultation.entities),
        "turn": consultation.turn,
        "outcome": consultation.outcome,
        "faction_id": consultation.faction_id,
        "worldview_sha256": worldview_sha256,
        "captured_at": consultation.captured_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def grounding_id_for(body: bytes) -> str:
    """``sha256:<hex>`` of exactly these bytes — the id and the filename in one derivation."""
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Cache:
    """Writes evidence where Yupana reads it.

    Every failure is reported through :attr:`last_error` and counted, never raised: grounding
    degrades and never stalls a turn (``knowledge.py``). But a swallowed error with no channel
    onto the record is indistinguishable from success, so the counters exist to make "the
    producer has been failing for an hour" a thing somebody can see.
    """

    def __init__(self, root: Path | str | None = None, *, max_age: int | None = None) -> None:
        resolved = Path(root) if root is not None else cache_dir()
        #: ``None`` disables publishing outright — no HOME, no XDG, no explicit root. Kept as a
        #: state rather than an exception so an orchestrator started in a bare environment runs.
        self.root = resolved
        self.max_age = max_age if max_age is not None else max_age_secs()
        self.published = 0
        self.reused = 0
        self.failures = 0
        self.last_error: str | None = None

    def publish(self, consultation: Consultation, worldview_sha256: str) -> GroundingRef | None:
        """Materialise one evidence file and return the reference that binds to it.

        Returns ``None`` when nothing could be written. That is the honest answer: the decision
        record then carries no reference, Yupana reports ``missing``, and the gap is visible on
        the consumer side instead of being papered over with a reference to a file that is not
        there — which Yupana would call ``unresolved`` and a reader would misread as corruption.
        """
        if self.root is None:
            self._fail("no grounding cache directory could be resolved")
            return None
        if consultation.turn < 0 or consultation.captured_at < 0:
            # Yupana's fields are unsigned; a negative would fail to deserialise there and be
            # reported as unresolved evidence rather than as the producer bug it is.
            self._fail(f"turn/captured_at must be non-negative (turn={consultation.turn})")
            return None
        body = canonical_bytes(consultation, worldview_sha256)
        gid = grounding_id_for(body)
        path = self.root / f"{gid.removeprefix('sha256:')}.json"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if path.exists():
                # Content-addressed: identical bytes, identical file. Two decisions sharing a
                # world view share their evidence, which is the fan-out being free rather than
                # 244 writes a turn.
                self.reused += 1
            else:
                self._write_atomic(path, body)
                self.published += 1
                self._prune()
        except OSError as exc:
            self._fail(f"{path}: {exc}")
            return None
        return GroundingRef(
            scope=SCOPE,
            grounding_id=gid,
            faction_id=consultation.faction_id,
            worldview_sha256=worldview_sha256,
        )

    def _write_atomic(self, path: Path, body: bytes) -> None:
        """Write via a temp file and rename, because a reader hashes whatever it finds.

        Yupana has no lock and no retry: it reads the file once and compares the digest. A
        partially written file is therefore not a transient miss, it is a hard ``unresolved``
        verdict on evidence that was about to be valid. ``rename`` within a directory is atomic,
        so a reader sees either no file or the whole one.

        The temp name carries the pid so two orchestrator processes sharing a cache cannot land
        on the same scratch path — they would otherwise truncate each other's write and both
        rename a half-file into place.
        """
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(body)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _prune(self) -> None:
        """Drop evidence far past the point Yupana would call it stale.

        Best-effort by construction. A file vanishing under a concurrent reader is exactly the
        race this must not create, which is why the threshold is a multiple of the freshness
        ceiling rather than the ceiling itself: everything eligible for deletion has already
        been useless to the consumer for a long time.
        """
        if self.root is None:
            return
        cutoff = time.time() - max(self.max_age, 1) * PRUNE_AGE_MULTIPLE
        try:
            entries = list(self.root.glob("*.json"))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue

    def _fail(self, reason: str) -> None:
        self.failures += 1
        self.last_error = reason
