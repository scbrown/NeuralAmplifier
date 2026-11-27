"""The ``na:Skill`` SHACL shape, its byte pin, a serialiser and a seat-store loader.

``docs/seats-and-policy.md`` §5.4-5.5: a seat's skill book lives in that seat's own Quipu store,
shape-checked on write. The shape is ``shapes/na-skill.shapes.ttl`` in this repository. This
module holds the three things the Quipu-backed store will need from it:

- :data:`SHAPES_SHA256` — **the exact bytes.** The shape is pinned by sha256 so a store is never
  guarded by a file nobody reviewed. :func:`shapes_bytes` refuses a file that does not match.
- :func:`to_turtle` — **one skill version as Turtle**, faithfully: whatever the
  :class:`~neural_amplifier.skills.Skill` holds is written, so the shape (not this function) is
  what refuses a bad instance. The only refusal here is a value Turtle cannot carry: a
  non-finite number has no ``xsd:decimal`` form.
- :func:`load_shapes` — **put the shape into a seat store** with ``qp shapes load``, as an argv
  list with no shell.

The Quipu-backed store that uses all three is ``skill_quipu.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from .skills import Skill, Trial

#: The shape file, at the repository root. Source-checkout only: it is not shipped as package
#: data, so an installed wheel has no shape file and shapes_bytes() raises FileNotFoundError.
SHAPES_PATH = Path(__file__).resolve().parents[3] / "shapes" / "na-skill.shapes.ttl"
#: sha256 of the shape file's bytes. Change the file, change this, in the same review.
SHAPES_SHA256 = "396cf93384841151666c5155a057e6b950cd027e32c4518eadc295c14f976d1a"
#: The name the shape is loaded under in a seat store.
SHAPES_NAME = "na-skill"

NA = "http://neuralamplifier.local/ontology/skill/"
#: Instance IRIs: one per skill version, ``<SKILL_BASE><name>/v<version>``. The content hash is a
#: property of the version, not its identity: two versions may carry the same code (a new success
#: check, or a revert A->B->A) and each still gets its own node.
SKILL_BASE = "http://neuralamplifier.local/skill/"


class ShapesError(RuntimeError):
    """The shape file does not match its pin, or ``qp`` refused to load it."""


def shapes_bytes(path: Path = SHAPES_PATH) -> bytes:
    """The shape file's bytes, refused unless they hash to :data:`SHAPES_SHA256`."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHAPES_SHA256:
        raise ShapesError(f"{path} hashes to {digest}, not the pinned {SHAPES_SHA256}")
    return data


def version_iri(name: str, version: int, prefix: str = SKILL_BASE) -> str:
    """The bare IRI of ``name@v<version>`` under ``prefix``: unique per version, not per hash."""
    return f"{prefix}{name}/v{int(version)}"


def trial_turtle(version: str, index: int, trial: Trial) -> list[str]:
    """Turtle blocks for trial ``index`` of the version at bare IRI ``version``, deltas included."""
    trial_iri = f"<{version}/trial/{index}>"
    trial_props: list[tuple[str, str]] = [("source", _str(trial.source))]
    if trial.seed is not None:
        trial_props.append(("seed", _int(trial.seed)))
    trial_props.append(("verdict", _str(trial.verdict)))
    blocks: list[str] = []
    for j, (metric, value) in enumerate(sorted(trial.deltas.items())):
        delta = f"<{version}/trial/{index}/delta/{j}>"
        trial_props.append(("delta", delta))
        blocks.append(f"{delta} na:metric {_str(metric)} ;\n    na:value {_decimal(value)} .")
    blocks.append(_block(trial_iri, "Trial", trial_props))
    return blocks


def _str(value: str) -> str:
    # JSON string escapes (\" \\ \n \t \uXXXX ...) are all valid Turtle ECHAR/UCHAR escapes.
    return json.dumps(value, ensure_ascii=False)


def _int(value: int) -> str:
    return f'"{int(value)}"^^<http://www.w3.org/2001/XMLSchema#integer>'


def _decimal(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{value!r} has no xsd:decimal form")
    text = format(Decimal(repr(number)), "f")
    return f'"{text}"^^<http://www.w3.org/2001/XMLSchema#decimal>'


#: The one prefix every document this package writes declares.
PREFIX = f"@prefix na: <{NA}> .\n\n"


def _block(iri: str, cls: str, props: list[tuple[str, str]]) -> str:
    body = [f"    na:{predicate} {obj}" for predicate, obj in props]
    return f"{iri} a na:{cls} ;\n" + " ;\n".join(body) + " ."


def to_turtle(skill: Skill, *, authored_by: str | None = None, prefix: str = SKILL_BASE) -> str:
    """One skill version as a Turtle document for :data:`SHAPES_PATH` to check.

    Everything comes from the :class:`~neural_amplifier.skills.Skill` itself, including its
    ``check`` (a :class:`~neural_amplifier.skills.SuccessCheck`) and ``retired_reason``, so a
    practice-authored or shelved version serialises with them. ``authored_by`` is provenance the
    store does not yet carry. ``None`` fields are omitted, so a missing value is a missing triple.

    The node is :func:`version_iri` under ``prefix``; ``na:supersedes`` names the previous version
    (``v<version - 1>``) under the same prefix, since a store only ever revises its latest version.
    """
    success_check = skill.check
    retired_reason = skill.retired_reason
    base = version_iri(skill.name, skill.version, prefix)
    me = f"<{base}>"
    props: list[tuple[str, str]] = [
        ("name", _str(skill.name)),
        ("version", _int(skill.version)),
        ("description", _str(skill.description)),
        ("code", _str(skill.code)),
        ("contentHash", _str(skill.hash)),
        ("params", _str(json.dumps(dict(skill.params), sort_keys=True))),
        ("status", _str(skill.status)),
    ]
    if retired_reason is not None:
        props.append(("retiredReason", _str(retired_reason)))
    if skill.supersedes is not None:
        props.append(("supersedes", f"<{version_iri(skill.name, skill.version - 1, prefix)}>"))
    props += [("requiresSurface", _str(s)) for s in skill.surfaces]
    props += [("requiresKnob", _str(k)) for k in skill.requires]
    if authored_by is not None:
        props.append(("authoredBy", _str(authored_by)))

    blocks: list[str] = []
    if success_check is not None:
        check = f"<{base}/success>"
        props.append(("successCheck", check))
        blocks.append(
            _block(
                check,
                "SuccessCheck",
                [
                    ("metric", _str(success_check.metric)),
                    ("comparator", _str(success_check.comparator)),
                    ("target", _decimal(success_check.target)),
                    ("horizonTurns", _int(success_check.horizon_turns)),
                ],
            )
        )
    for i, trial in enumerate(skill.trials):
        props.append(("trial", f"<{base}/trial/{i}>"))
        blocks += trial_turtle(base, i, trial)

    head = PREFIX
    return head + "\n\n".join([_block(me, "Skill", props), *blocks]) + "\n"


Runner = Callable[..., subprocess.CompletedProcess[str]]


def load_shapes(
    db: Path,
    *,
    qp: str = "qp",
    path: Path = SHAPES_PATH,
    name: str = SHAPES_NAME,
    run: Runner = subprocess.run,
    timeout: float = 30.0,
) -> None:
    """Load the pinned shape into the seat store at ``db`` with ``qp shapes load``.

    The pin is checked first, so a drifted file never reaches a store. The command is an argv
    list and no shell is involved. A nonzero exit raises :class:`ShapesError`; qp's own output
    is not copied into the message, because it may echo the store path back.
    """
    shapes_bytes(path)
    argv = [qp, "shapes", "load", name, str(path), "--db", str(db)]
    done = run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode != 0:
        raise ShapesError(f"qp shapes load exited {done.returncode}")
