"""The na:Skill shape (docs/seats-and-policy.md §5.4-5.5; aegis-gqi0 step 1).

Two lanes check the same instances against ``shapes/na-skill.shapes.ttl``:

- **rdflib, always.** A small evaluator for exactly the SHACL constraint components the shape
  uses. It is not a general SHACL engine; :func:`test_evaluator_covers_every_component_used`
  fails if the shape starts using one it does not understand, so the lane cannot pass by
  silently ignoring a constraint.
- **qp validate, when qp is installed.** The real validator a seat store uses, run in a scratch
  directory. Skipped, not failed, on a host without it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import XSD

from neural_amplifier import metrics, skill_shapes
from neural_amplifier.skill_shapes import (
    SHAPES_PATH,
    SHAPES_SHA256,
    ShapesError,
    SuccessCheck,
    load_shapes,
    shapes_bytes,
    to_turtle,
)
from neural_amplifier.skills import Skill, SkillStore, Trial, content_hash

SH = Namespace("http://www.w3.org/ns/shacl#")
NA = Namespace(skill_shapes.NA)
QP = shutil.which("qp")
needs_qp = pytest.mark.skipif(QP is None, reason="qp is not installed on this host")

CODE = """
def on_decision(view, min_surplus = 2):
    if view["surface_id"] != "base.production":
        return None
    return None  # "quoted", with a tab:\t and a backslash \\\\
"""


def stored_skill(tmp_path: Path) -> Skill:
    """A skill as skills.py makes it: saved twice (so it supersedes), with trials, promoted."""
    store = SkillStore(root=tmp_path / "book", min_verified_seeds=2)
    store.save("expand_early", "expand while the base can pay", CODE + "\n# v1\n")
    store.save(
        "expand_early",
        "expand while the base can pay for it",
        CODE,
        surfaces=["base.production"],
        params={"min_surplus": 2, "note": "two"},
        requires=["focus.fight", "target.base"],
    )
    store.record_trial("expand_early", 2, "fixture:early", 1, "pass", {"base_count": 1})
    store.record_trial("expand_early", 2, "game-7", 2, "pass", {"energy_reserves": -12.5})
    store.record_trial("expand_early", 2, "game-8", 3, "fail")
    return store.promote("expand_early", 2)


CHECK = SuccessCheck("base_count", "at_least", 3.0, 20)


# --- the rdflib evaluator ------------------------------------------------------------------

#: Every constraint parameter the evaluator below implements.
UNDERSTOOD = {
    SH.path, SH.datatype, SH.minCount, SH.maxCount, SH.pattern, SH["in"], SH["class"],
    SH.nodeKind, SH.minInclusive, SH.minLength, SH.node,
}  # fmt: skip


def _shapes() -> Graph:
    return Graph().parse(SHAPES_PATH, format="turtle")


def _value_ok(data: Graph, shapes: Graph, prop: Any, value: Any) -> list[str]:
    errs: list[str] = []
    datatype = shapes.value(prop, SH.datatype)
    if datatype is not None:
        if not isinstance(value, Literal) or value.language is not None:
            errs.append(f"{value!r} is not a typed literal")
        elif (value.datatype or XSD.string) != datatype or value.ill_typed:
            errs.append(f"{value!r} is not a valid {datatype}")
    pattern = shapes.value(prop, SH.pattern)
    if pattern is not None and not re.search(str(pattern), str(value)):
        errs.append(f"{value!r} does not match {pattern}")
    allowed = shapes.value(prop, SH["in"])
    if allowed is not None and value not in list(Collection(shapes, allowed)):
        errs.append(f"{value!r} is not in the closed list")
    cls = shapes.value(prop, SH["class"])
    if cls is not None and (value, RDF.type, cls) not in data:
        errs.append(f"{value!r} is not a {cls}")
    if shapes.value(prop, SH.nodeKind) == SH.IRI and not isinstance(value, URIRef):
        errs.append(f"{value!r} is not an IRI")
    low = shapes.value(prop, SH.minInclusive)
    if low is not None and not (isinstance(value, Literal) and value.toPython() >= low.toPython()):
        errs.append(f"{value!r} is below {low}")
    min_len = shapes.value(prop, SH.minLength)
    if min_len is not None and len(str(value)) < min_len.toPython():
        errs.append(f"{value!r} is shorter than {min_len}")
    node = shapes.value(prop, SH.node)
    if node is not None:
        errs += _node_ok(data, shapes, node, value)
    return errs


def _node_ok(data: Graph, shapes: Graph, shape: Any, focus: Any) -> list[str]:
    errs: list[str] = []
    for prop in shapes.objects(shape, SH.property):
        path = shapes.value(prop, SH.path)
        values = list(data.objects(focus, path))
        low, high = shapes.value(prop, SH.minCount), shapes.value(prop, SH.maxCount)
        if low is not None and len(values) < low.toPython():
            errs.append(f"{focus} needs at least {low} {path}")
        if high is not None and len(values) > high.toPython():
            errs.append(f"{focus} allows at most {high} {path}")
        for value in values:
            errs += [f"{focus} {path}: {e}" for e in _value_ok(data, shapes, prop, value)]
    return errs


def violations(turtle: str) -> list[str]:
    data, shapes = Graph().parse(data=turtle, format="turtle"), _shapes()
    errs: list[str] = []
    for shape, cls in shapes.subject_objects(SH.targetClass):
        for focus in data.subjects(RDF.type, cls):
            errs += _node_ok(data, shapes, shape, focus)
    return errs


def test_evaluator_covers_every_component_used() -> None:
    shapes = _shapes()
    used = {p for prop in shapes.objects(None, SH.property) for p in shapes.predicates(prop)}
    assert used <= UNDERSTOOD, f"teach the evaluator {sorted(used - UNDERSTOOD)}"


# --- the pin and the vocabulary ------------------------------------------------------------


def test_shape_bytes_match_the_pin() -> None:
    assert hashlib.sha256(SHAPES_PATH.read_bytes()).hexdigest() == SHAPES_SHA256
    assert shapes_bytes() == SHAPES_PATH.read_bytes()


def test_a_drifted_shape_file_is_refused(tmp_path: Path) -> None:
    drifted = tmp_path / "na-skill.shapes.ttl"
    drifted.write_bytes(SHAPES_PATH.read_bytes() + b"\n")
    with pytest.raises(ShapesError, match="pinned"):
        shapes_bytes(drifted)


def test_success_check_metrics_are_exactly_the_vocabulary() -> None:
    shapes = _shapes()
    (prop,) = [
        p
        for p in shapes.objects(NA.SuccessCheckShape, SH.property)
        if shapes.value(p, SH.path) == NA.metric
    ]
    listed = [str(v) for v in Collection(shapes, shapes.value(prop, SH["in"]))]
    assert listed == sorted(metrics.VOCABULARY)


def test_closed_lists_match_skills_py() -> None:
    from neural_amplifier import skills

    shapes = _shapes()

    def closed(shape: URIRef, path: URIRef) -> list[str]:
        for p in shapes.objects(shape, SH.property):
            if shapes.value(p, SH.path) == path:
                return [str(v) for v in Collection(shapes, shapes.value(p, SH["in"]))]
        raise AssertionError(path)

    assert closed(NA.SkillShape, NA.status) == list(skills.STATUSES)
    assert closed(NA.TrialShape, NA.verdict) == ["pass", "fail"]


# --- instances -----------------------------------------------------------------------------


def test_a_skill_from_skills_py_conforms(tmp_path: Path) -> None:
    skill = stored_skill(tmp_path)
    assert violations(to_turtle(skill)) == []
    assert violations(to_turtle(skill, success_check=CHECK, authored_by="seat-2")) == []
    retired = replace(skill, status="retired")
    assert violations(to_turtle(retired, retired_reason="lost to a rush")) == []


def test_turtle_round_trips_the_fields(tmp_path: Path) -> None:
    skill = stored_skill(tmp_path)
    graph = Graph().parse(data=to_turtle(skill), format="turtle")
    (node,) = graph.subjects(RDF.type, NA.Skill)
    assert str(graph.value(node, NA.code)) == skill.code
    assert str(graph.value(node, NA.contentHash)) == content_hash(skill.code)
    assert json.loads(str(graph.value(node, NA.params))) == skill.params
    assert {str(k) for k in graph.objects(node, NA.requiresKnob)} == set(skill.requires)
    assert graph.value(node, NA.supersedes) == URIRef(
        f"{skill_shapes.SKILL_BASE}{skill.name}/{skill.supersedes}"
    )
    deltas = {
        str(graph.value(d, NA.metric)): graph.value(d, NA.value).toPython()
        for t in graph.objects(node, NA.trial)
        for d in graph.objects(t, NA.delta)
    }
    assert {k: float(v) for k, v in deltas.items()} == {"base_count": 1.0, "energy_reserves": -12.5}


def _bad_instances(skill: Skill) -> dict[str, str]:
    first = skill.trials[0]
    return {
        "missing seed": to_turtle(replace(skill, trials=(replace(first, seed=None),))),
        "verdict error": to_turtle(replace(skill, trials=(replace(first, verdict="error"),))),
        "bad knob id": to_turtle(replace(skill, requires=("Focus",))),
        "unknown metric": to_turtle(
            skill, success_check=SuccessCheck("happiness", "at_least", 1.0, 5)
        ),
        "bad comparator": to_turtle(skill, success_check=replace(CHECK, comparator="above")),
        "zero horizon": to_turtle(skill, success_check=replace(CHECK, horizon_turns=0)),
        "unknown status": to_turtle(replace(skill, status="beta")),
        "bad hash": to_turtle(replace(skill, hash="not-a-hash")),
    }


BAD = [
    "missing seed",
    "verdict error",
    "bad knob id",
    "unknown metric",
    "bad comparator",
    "zero horizon",
    "unknown status",
    "bad hash",
]


@pytest.mark.parametrize("case", BAD)
def test_a_bad_instance_is_refused(tmp_path: Path, case: str) -> None:
    assert violations(_bad_instances(stored_skill(tmp_path))[case]) != []


def test_a_non_finite_number_has_no_turtle_form(tmp_path: Path) -> None:
    skill = stored_skill(tmp_path)
    with pytest.raises(ValueError, match="decimal"):
        to_turtle(skill, success_check=replace(CHECK, target=float("inf")))
    trial = Trial("fixture:early", 1, "pass", {"base_count": float("nan")})
    with pytest.raises(ValueError, match="decimal"):
        to_turtle(replace(skill, trials=(trial,)))


# --- the loader ----------------------------------------------------------------------------


def test_loader_runs_qp_as_argv_without_a_shell(tmp_path: Path) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    load_shapes(tmp_path / "seat.db", qp="/opt/qp", run=run)
    ((argv, kwargs),) = calls
    assert argv == [
        "/opt/qp", "shapes", "load", "na-skill", str(SHAPES_PATH), "--db", str(tmp_path / "seat.db")
    ]  # fmt: skip
    assert not kwargs.get("shell")


def test_loader_refuses_a_qp_failure_and_a_drifted_file(tmp_path: Path) -> None:
    def fail(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, "", "secret path")

    with pytest.raises(ShapesError, match="exited 2") as err:
        load_shapes(tmp_path / "seat.db", run=fail)
    assert "secret" not in str(err.value)

    drifted = tmp_path / "drifted.ttl"
    drifted.write_bytes(b"# not the shape\n")
    with pytest.raises(ShapesError, match="pinned"):
        load_shapes(tmp_path / "seat.db", path=drifted, run=fail)


# --- the qp lane ---------------------------------------------------------------------------


def _qp_validate(tmp_path: Path, turtle: str) -> dict[str, Any]:
    data = tmp_path / "instance.ttl"
    data.write_text(turtle, encoding="utf-8")
    assert QP is not None
    done = subprocess.run(
        [QP, "validate", "--shapes", str(SHAPES_PATH), "--data", str(data), "--format", "json"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    return json.loads(done.stdout)


@needs_qp
def test_qp_accepts_a_skill_from_skills_py(tmp_path: Path) -> None:
    skill = stored_skill(tmp_path)
    report = _qp_validate(tmp_path, to_turtle(skill, success_check=CHECK, authored_by="seat-2"))
    assert report["conforms"] is True, report


@needs_qp
@pytest.mark.parametrize("case", BAD)
def test_qp_refuses_a_bad_instance(tmp_path: Path, case: str) -> None:
    report = _qp_validate(tmp_path, _bad_instances(stored_skill(tmp_path))[case])
    assert report["conforms"] is False, report


@needs_qp
def test_qp_loads_the_shape_into_a_scratch_seat_store(tmp_path: Path) -> None:
    assert QP is not None
    db = tmp_path / "seat" / "quipu.db"
    db.parent.mkdir()
    load_shapes(db, qp=QP)
    listed = subprocess.run(
        [QP, "shapes", "list", "--db", str(db)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        check=True,
    )
    assert skill_shapes.SHAPES_NAME in listed.stdout
