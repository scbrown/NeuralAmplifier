"""The producer half of the Yupana grounding contract.

Two kinds of test live here, and the split is the point.

The unit tests below fix the *shape* — three outcomes, fog scoping, byte fidelity, honest
absence. They are fast and they run everywhere. On their own they prove only that this module
agrees with itself, which is exactly the failure this feature exists to prevent: the whole
contract is cross-repository and cross-language, so a producer can be internally consistent and
still write files the consumer rejects.

So the last test runs the **real Yupana binary** against evidence this code wrote, and asserts
on what Yupana concluded. That is the acceptance criterion agreed on aegis-y12ji: identical
``grounding_id`` / ``faction_id`` / ``worldview_sha256`` through both sides. It skips when no
grounding-capable binary is present — and says so loudly, because a skipped acceptance test that
reads as a pass is the vacuous green this project has been burned by before.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.datalinks.quipu import NAMESPACE, QuipuRetriever
from neural_amplifier.grounding_evidence import (
    Cache,
    Consultation,
    canonical_bytes,
    grounding_id_for,
)
from neural_amplifier.orchestrator import Orchestrator

WORLD_VIEW_SHA = "sha256:" + "ab" * 32


def consultation(**overrides: object) -> Consultation:
    base: dict[str, object] = {
        "graph": "urn:neuralamplifier:graph:knowledge",
        "query": "SELECT ?f WHERE { ?f ?p ?o }",
        "entities": ("urn:smac:fact:recycling-tanks",),
        "turn": 42,
        "outcome": "used",
        "faction_id": "1",
        "captured_at": int(time.time()),
    }
    base.update(overrides)
    return Consultation(**base)  # type: ignore[arg-type]


def view(*labels: str, faction_id: int = 1, turn: int = 42) -> WorldView:
    return WorldView(
        turn=turn,
        faction="Gaians",
        faction_id=faction_id,
        engine="thinker",
        scope="base",
        action_space=[Action(id=f"a{i}", action=lbl) for i, lbl in enumerate(labels)],
    )


# --------------------------------------------------------------------- byte fidelity


def test_the_digest_is_the_filename_and_the_id(tmp_path: Path) -> None:
    """Yupana re-hashes the bytes it reads. Name, id and content must agree or it says
    ``unresolved`` about evidence that is in fact perfectly good."""
    ref = Cache(tmp_path).publish(consultation(), WORLD_VIEW_SHA)
    assert ref is not None
    digest = ref.grounding_id.removeprefix("sha256:")
    body = (tmp_path / f"{digest}.json").read_bytes()
    assert grounding_id_for(body) == ref.grounding_id


def test_encoding_is_deterministic_regardless_of_field_order() -> None:
    """Content addressing is only content addressing if the encoding is stable — otherwise two
    producers encoding the same facts write two files and neither is wrong."""
    a = canonical_bytes(consultation(), WORLD_VIEW_SHA)
    b = canonical_bytes(consultation(), WORLD_VIEW_SHA)
    assert a == b
    assert b"\n" not in a, "a trailing newline is invisible in an editor and changes the digest"


def test_identical_input_reuses_one_file(tmp_path: Path) -> None:
    """The fan-out from one consultation to many decisions has to be free, or the 244
    decisions measured in a single turn (na-x5n) become 244 writes."""
    cache = Cache(tmp_path)
    first = cache.publish(consultation(), WORLD_VIEW_SHA)
    second = cache.publish(
        consultation(captured_at=first.grounding_id and _at(cache, first)), WORLD_VIEW_SHA
    )
    assert first is not None and second is not None
    assert first.grounding_id == second.grounding_id
    assert cache.published == 1 and cache.reused == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def _at(cache: Cache, ref: object) -> int:
    """The captured_at of the evidence already written, so the second publish is byte-identical."""
    assert cache.root is not None
    digest = ref.grounding_id.removeprefix("sha256:")  # type: ignore[attr-defined]
    return int(json.loads((cache.root / f"{digest}.json").read_text())["captured_at"])


# --------------------------------------------------------------------- honest absence


def test_a_cache_that_cannot_be_written_reports_rather_than_raises(tmp_path: Path) -> None:
    """Grounding degrades and never stalls a turn — but the failure must leave a trace, or a
    producer broken for an hour looks exactly like one with nothing to say."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    cache = Cache(blocked / "cache")
    assert cache.publish(consultation(), WORLD_VIEW_SHA) is None
    assert cache.failures == 1
    assert cache.last_error is not None


def test_a_negative_turn_is_refused_here_rather_than_downstream(tmp_path: Path) -> None:
    """Yupana's fields are unsigned. A negative would fail to deserialise there and be reported
    as unresolved evidence — i.e. as corruption — rather than as the producer bug it is."""
    cache = Cache(tmp_path)
    assert cache.publish(consultation(turn=-1), WORLD_VIEW_SHA) is None
    assert cache.failures == 1


# --------------------------------------------------------------------- capture at the boundary


@pytest.mark.parametrize(
    ("rows", "outcome", "entities"),
    [
        ([{"f": f"{NAMESPACE}facility/recycling"}], "used", 1),
        ([], "empty", 0),
    ],
)
def test_prime_turn_records_what_the_consultation_returned(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]], outcome: str, entities: int
) -> None:
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(retriever, "query", lambda _sparql: rows)
    retriever.prime_turn(42, 1)
    record = retriever.consultation_for(42, 1)
    assert record is not None
    assert record.outcome == outcome
    assert len(record.entities) == entities
    assert record.graph == "urn:na:dataset"


def test_a_failed_consultation_is_recorded_as_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction that a boolean would destroy. A store that is down and a store with no
    rules for this engine are both "no facts", and they have opposite fixes — so the caller
    turning this into a degraded turn announcement must not be the only record of which it was.
    """

    def boom(_sparql: str) -> list[dict[str, str]]:
        raise RuntimeError("quipu unreachable")

    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(retriever, "query", boom)
    with pytest.raises(RuntimeError):
        retriever.prime_turn(42, 1)
    record = retriever.consultation_for(42, 1)
    assert record is not None and record.outcome == "transport-error"
    assert record.entities == ()


def test_consultation_lookup_is_faction_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unscoped read hands one faction the evidence of another's consultation, and the
    record that results looks entirely ordinary."""
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(retriever, "query", lambda _sparql: [{"f": "urn:x"}])
    retriever.prime_turn(42, 1)
    assert retriever.consultation_for(42, 2) is None
    assert retriever.consultation_for(43, 1) is None
    assert retriever.consultation_for(42, None) is None


def test_the_memory_wrapper_forwards_the_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass-through that enumerates what it forwards drops whatever it was not told about —
    the ``_with_latency`` bug in ``knowledge.py``, one layer up. Wrapping a Quipu retriever in
    memory must not turn every grounded decision into one Yupana reports as ``missing``."""
    from neural_amplifier.memory import RememberingRetriever

    inner = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(inner, "query", lambda _sparql: [{"f": "urn:x"}])
    wrapper = RememberingRetriever(inner)
    wrapper.prime_turn(42, 1)
    assert wrapper.consultation_for(42, 1) is not None


# --------------------------------------------------------------------- binding to a decision


def _orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kw: object) -> Orchestrator:
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(
        retriever, "query", lambda _sparql: [{"f": f"{NAMESPACE}facility/recycling"}]
    )
    retriever.prime_turn(42, 1)
    return Orchestrator(ScriptedBrain(), retriever=retriever, grounding_cache=Cache(tmp_path), **kw)


def test_a_decision_binds_its_own_world_view_and_faction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _orchestrator(tmp_path, monkeypatch).decide(view("Recycling Tanks")).record
    assert record.grounding is not None
    assert record.grounding["scope"] == "na"
    assert record.grounding["faction_id"] == "1"
    # The binding that makes replay falsifiable: the reference names THIS decision's input.
    assert record.grounding["worldview_sha256"] == record.world_view_hash
    stored = json.loads(
        (tmp_path / f"{record.grounding['grounding_id'].removeprefix('sha256:')}.json").read_text()
    )
    assert stored["worldview_sha256"] == record.world_view_hash
    assert stored["faction_id"] == "1"


def test_without_a_cache_the_record_says_nothing_rather_than_something_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The library default. A test that could reach the live cache is a channel for
    manufacturing grounding for a real agent — evidence carries no marker saying who wrote it.
    """
    retriever = QuipuRetriever("http://q", dataset="urn:na:dataset")
    monkeypatch.setattr(retriever, "query", lambda _sparql: [{"f": "urn:x"}])
    retriever.prime_turn(42, 1)
    result = Orchestrator(ScriptedBrain(), retriever=retriever).decide(view("Recycling Tanks"))
    record = result.record
    assert record.grounding is None


def test_an_unprimed_turn_carries_no_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path, monkeypatch)
    record = orch.decide(view("Recycling Tanks", turn=99)).record
    assert record.grounding is None


# --------------------------------------------------------------------- the acceptance test


def _candidate_binary() -> str | None:
    """Whatever Yupana is on this host — `yupana` first, since that is the name it is
    becoming, then `hank`, which is still what is installed here today (aegis-niuav)."""
    explicit = os.environ.get("NA_YUPANA_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    for name in ("yupana", "hank"):
        found = shutil.which(name)
        if found:
            return found
    return None


@functools.lru_cache(maxsize=1)
def yupana_binary() -> str | None:
    """A Yupana that actually understands grounding, or ``None`` — probed, not assumed.

    **Presence is not capability, and the difference decides who gets blamed.** The `hank`
    installed on this host is 0.6.3, which predates the grounding contract entirely: it runs the
    hook, exits 0, and emits an action record with no `grounding_outcome` at all. Selecting on
    presence therefore turned every acceptance arm into `assert None == 'used'` — seven red
    tests whose message points at this file, when the actual finding is "your Yupana is too old".
    Measured before this probe existed.

    So the gate is a control run: write known-good evidence, ask the binary, and require that it
    *says something* about grounding. The probe asserts the field EXISTS rather than checking its
    value — the value is what the tests below are for, and a probe that assumed the answer would
    be the vacuous check it is meant to replace.
    """
    binary = _candidate_binary()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        cache, metrics = Path(tmp) / "cache", Path(tmp) / "m.jsonl"
        ref = Cache(cache).publish(consultation(), WORLD_VIEW_SHA)
        if ref is None:
            return None
        payload = {
            "session_id": "probe",
            "cwd": tmp,
            "tool_name": "Bash",
            "tool_input": {"command": "ls /tmp"},
            "grounding": ref.as_dict(),
        }
        try:
            subprocess.run(
                [binary, "hook", "pre-bash"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "HANK_GROUNDING_CACHE_DIR": str(cache),
                    "HANK_METRICS_PATH": str(metrics),
                },
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if not metrics.exists():
            return None
        rows = [json.loads(line) for line in metrics.read_text().splitlines() if line.strip()]
        actions = [row for row in rows if row.get("kind") == "action"]
        if actions and "grounding_outcome" in actions[-1]:
            return binary
    return None


@pytest.mark.skipif(
    yupana_binary() is None,
    reason="ACCEPTANCE TEST NOT RUN: no grounding-capable yupana/hank on this host (the "
    "installed build may predate the contract — it runs the hook and emits no "
    "grounding_outcome). This is the only test proving the two halves of the contract "
    "agree; a green suite without it says nothing about cross-language fidelity. Point "
    "NA_YUPANA_BIN at a build that has src/grounding.rs.",
)
def test_yupana_reads_this_producers_evidence_as_used(tmp_path: Path) -> None:
    """The contract, end to end, through the real consumer.

    Asserts on Yupana's own verdict rather than on our file: the question is not whether we
    wrote what we meant to, it is whether the component that decides "was this grounded?"
    agrees. ``satisfied`` is reachable only through a digest that matches its filename, a
    faction that matches, a world view that matches, and evidence inside the freshness ceiling.
    """
    binary = yupana_binary()
    assert binary is not None
    cache = tmp_path / "cache"
    metrics = tmp_path / "metrics.jsonl"
    ref = Cache(cache).publish(consultation(), WORLD_VIEW_SHA)
    assert ref is not None

    payload = {
        "session_id": "acceptance",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "ls /tmp"},
        "grounding": ref.as_dict(),
    }
    result = subprocess.run(
        [binary, "hook", "pre-bash"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HANK_GROUNDING_CACHE_DIR": str(cache),
            "HANK_METRICS_PATH": str(metrics),
        },
    )
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in metrics.read_text().splitlines() if line.strip()]
    actions = [row for row in rows if row.get("kind") == "action"]
    assert actions, f"yupana emitted no action record: {rows}"
    action = actions[-1]

    assert action.get("grounding_outcome") == "used"
    # Identical binding through both sides — the aegis-y12ji acceptance criterion.
    assert action.get("grounding_id") == ref.grounding_id
    assert action.get("faction_id") == ref.faction_id
    assert action.get("worldview_sha256") == ref.worldview_sha256
    constraint = action["constraints"][0]
    assert constraint["id"] == "na-turn-grounding"
    assert constraint["outcome"] == "satisfied"


@pytest.mark.skipif(yupana_binary() is None, reason="see the acceptance test above")
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("tamper", "unresolved"),
        ("faction", "unresolved"),
        ("worldview", "unresolved"),
        ("stale", "stale"),
        ("empty", "empty"),
        ("transport", "transport-error"),
    ],
)
def test_yupana_never_reads_broken_evidence_as_grounded(
    tmp_path: Path, mutate: str, expected: str
) -> None:
    """The arms that make the test above mean something.

    A contract test that only ever exercises the happy path cannot distinguish a working
    verifier from one that answers ``satisfied`` to everything. Each arm below is a different
    way evidence can be wrong, and none of them may come back ``satisfied``.
    """
    binary = yupana_binary()
    assert binary is not None
    cache = tmp_path / "cache"
    metrics = tmp_path / "metrics.jsonl"

    record = consultation()
    if mutate == "stale":
        record = consultation(captured_at=int(time.time()) - 4000)
    elif mutate == "empty":
        record = consultation(outcome="empty", entities=())
    elif mutate == "transport":
        record = consultation(outcome="transport-error", entities=())

    ref = Cache(cache).publish(record, WORLD_VIEW_SHA)
    assert ref is not None
    reference = ref.as_dict()

    if mutate == "tamper":
        path = cache / f"{ref.grounding_id.removeprefix('sha256:')}.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutate == "faction":
        reference["faction_id"] = "7"
    elif mutate == "worldview":
        reference["worldview_sha256"] = "sha256:" + "ff" * 32

    payload = {
        "session_id": "acceptance",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "ls /tmp"},
        "grounding": reference,
    }
    subprocess.run(
        [binary, "hook", "pre-bash"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HANK_GROUNDING_CACHE_DIR": str(cache),
            "HANK_METRICS_PATH": str(metrics),
        },
        check=True,
    )
    rows = [json.loads(line) for line in metrics.read_text().splitlines() if line.strip()]
    action = [row for row in rows if row.get("kind") == "action"][-1]
    assert action.get("grounding_outcome") == expected
    assert action["constraints"][0]["outcome"] != "satisfied"
