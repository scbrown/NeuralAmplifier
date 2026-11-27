"""The Quipu-backed skill store, seat books, the pinned commons and publication (§5.4-5.5).

Two lanes: ``fake`` (the in-memory backend, always runs) and ``qp`` (the real CLI against a
temporary database, skipped when ``qp`` is not installed). The parity suite reruns every
``SkillStore`` test in ``test_skills.py`` and ``test_practice.py`` with ``SkillStore`` replaced by
a ``QuipuSkillStore`` on each lane.
"""

from __future__ import annotations

import inspect
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from neural_amplifier.skill_quipu import (
    CommonsReadOnly,
    FakeBackend,
    QpBackend,
    QuipuSkillStore,
    SeatSkills,
    SkillBackend,
    SkillLibrary,
    SkillWriteRefused,
    skills_from_triples,
)
from neural_amplifier.skill_shapes import SKILL_BASE, to_turtle
from neural_amplifier.skills import Skill, SkillStore, content_hash

sys.path.insert(0, str(Path(__file__).parent))
import test_practice  # noqa: E402
import test_skills  # noqa: E402
from test_practice import trials  # noqa: E402,F401  (fixtures the parity tests take)
from test_skills import runner  # noqa: E402,F401

HAVE_QP = shutil.which("qp") is not None
LANES = ["fake", pytest.param("qp", marks=pytest.mark.skipif(not HAVE_QP, reason="qp absent"))]

CODE = 'def on_decision(view):\n    return choose(view["action_space"][0]["id"], "first")\n'
OTHER = CODE.replace('"first"', '"first offered"')


@pytest.fixture(params=LANES)
def lane(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def new_backend(lane: str, tmp_path_factory: pytest.TempPathFactory) -> Callable[[], SkillBackend]:
    def make() -> SkillBackend:
        if lane == "fake":
            return FakeBackend()
        return QpBackend(tmp_path_factory.mktemp("qp") / "skills.db")

    return make


# --- parity: the SkillStore suite against QuipuSkillStore ----------------------------------------


def _store_tests(module: Any) -> dict[str, Callable[..., Any]]:
    return {
        name: fn
        for name, fn in vars(module).items()
        if name.startswith("test_") and callable(fn) and "SkillStore" in inspect.getsource(fn)
    }


PARITY = {**_store_tests(test_skills), **_store_tests(test_practice)}


@pytest.fixture(autouse=True)
def quipu_store_class(
    lane: str, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Replace ``SkillStore`` in the two test modules with a factory over ``lane``.

    A store opened with a root reopens the same backend, as a directory store reopens its files.
    """
    by_root: dict[Path, SkillBackend] = {}

    def factory(root: str | Path | None = None, min_verified_seeds: int = 3) -> QuipuSkillStore:
        if root is not None and Path(root) in by_root:
            backend = by_root[Path(root)]
        elif lane == "fake":
            backend = FakeBackend()
        else:
            backend = QpBackend(tmp_path_factory.mktemp("qp") / "skills.db")
        if root is not None:
            by_root[Path(root)] = backend
        return QuipuSkillStore(backend, min_verified_seeds)

    for module in (test_skills, test_practice):
        monkeypatch.setattr(module, "SkillStore", factory)
    yield


@pytest.fixture
def store() -> SkillStore:
    """``test_skills``'s seeded store fixture, run against the swapped class."""
    seeded: Callable[[], SkillStore] = test_skills.store.__wrapped__  # type: ignore[attr-defined]
    return seeded()


# Each SkillStore test, collected here under its own marks; the autouse fixture swaps the store.
for _name, _fn in PARITY.items():
    globals()[f"test_parity__{_name[len('test_') :]}"] = _fn


def test_parity_covers_the_store_suite() -> None:
    assert len(PARITY) >= 30


# --- writes are shape-checked ------------------------------------------------------------------


def _bad(**changes: Any) -> Skill:
    good = Skill("first_offered", 1, "take the first action", CODE, content_hash(CODE))
    return replace(good, **changes)


@pytest.mark.parametrize(
    "bad",
    [
        _bad(status="trusted"),  # type: ignore[arg-type]
        _bad(description=""),
        _bad(name="First"),
        _bad(hash="not-a-hash"),
        _bad(requires=("growth",)),
        _bad(version=0),
    ],
)
def test_a_bad_instance_is_refused_at_write_and_leaves_nothing(
    bad: Skill, new_backend: Callable[[], SkillBackend]
) -> None:
    backend = new_backend()
    book = QuipuSkillStore(backend)
    book.save("kept", "a kept skill", CODE)
    before = backend.triples()
    with pytest.raises(SkillWriteRefused):
        backend.knot(to_turtle(bad))
    with pytest.raises(SkillWriteRefused), book._lock:
        book._versions.setdefault(bad.name, []).append(bad)
        book._write(bad)
    assert backend.triples() == before
    assert book.get(bad.name) is None or book.get(bad.name).name != bad.name
    assert [s.name for s in QuipuSkillStore(backend).eligible(("candidate",))] == ["kept"]


def test_a_refused_update_leaves_the_stored_version(
    new_backend: Callable[[], SkillBackend],
) -> None:
    backend = new_backend()
    book = QuipuSkillStore(backend)
    v1 = book.save("kept", "a kept skill", CODE)
    with pytest.raises(SkillWriteRefused), book._lock:
        book._replace("kept", 1, status="trusted")
    assert book.get("kept") == v1
    assert QuipuSkillStore(backend).get("kept") == v1


def test_round_trip_keeps_every_field(new_backend: Callable[[], SkillBackend]) -> None:
    backend = new_backend()
    book = QuipuSkillStore(backend, min_verified_seeds=1)
    book.save(
        "first_offered",
        'take the "first" action\nalways',
        CODE,
        surfaces=["base_production"],
        params={"reserve": 10},
        requires=["focus.fight"],
        check={"metric": "base_count", "comparator": "at_least", "target": 2.5, "horizon_turns": 4},
    )
    book.record_trial("first_offered", 1, "fixture-a", 3, "pass", {"bases": 1.5, "pop": -2})
    book.promote("first_offered", 1)
    book.save("first_offered", "take the first action", OTHER)
    book.retire("first_offered", 2, "worse")
    reopened = QuipuSkillStore(backend)
    assert reopened.history("first_offered") == book.history("first_offered")


# --- seat books, the commons pin and publication -----------------------------------------------


@pytest.fixture
def library(new_backend: Callable[[], SkillBackend]) -> SkillLibrary:
    books: dict[str, SkillBackend] = {}

    def open_book(seat: str) -> SkillBackend:
        books.setdefault(seat, new_backend())
        return books[seat]

    lib = SkillLibrary(open_book, new_backend(), min_verified_seeds=1)
    lib.opened = books  # type: ignore[attr-defined]
    return lib


def test_one_book_per_seat_identity(library: SkillLibrary) -> None:
    assert library.book("alpha") is library.book("alpha")
    assert library.book("alpha").backend is not library.book("beta").backend
    library.start_game("g1", ["alpha", "beta"])
    library.seat("alpha", "g1").save("mine", "colony pod", CODE)
    library.start_game("g2", ["alpha"])
    assert library.seat("alpha", "g2").get("mine") is not None  # the book outlives the game


def test_seat_a_cannot_read_seat_b_through_the_api(library: SkillLibrary) -> None:
    library.start_game("g1", ["alpha", "beta"])
    a, b = library.seat("alpha", "g1"), library.seat("beta", "g1")
    b.save("secret_plan", "colony pod while surplus", CODE)
    assert a.get("secret_plan") is None
    assert a.history("secret_plan") == []
    assert a.retrieve("colony pod") == []
    assert not any("seat" in p for p in inspect.signature(SeatSkills.retrieve).parameters)
    with pytest.raises(PermissionError):
        library.seat("gamma", "g1")


def test_a_write_to_the_commons_through_the_seat_api_is_refused(library: SkillLibrary) -> None:
    library.start_game("g1", ["alpha"])
    seat = library.seat("alpha", "g1")
    with pytest.raises(CommonsReadOnly):
        seat.commons.save("sneaky", "colony pod", CODE)
    assert library.commons("g1").history("sneaky") == []


def _play(library: SkillLibrary, game: str, seat: str, name: str, code: str = CODE) -> None:
    library.start_game(game, [seat])
    library.seat(seat, game).save(name, "colony pod while surplus", code)


def test_publish_at_game_end_and_the_commons_pin(library: SkillLibrary) -> None:
    library.start_game("long", ["beta"])  # pinned before anything is published
    _play(library, "g1", "alpha", "expand")
    library.close_game("g1")
    [published] = library.publish_at_game_end("alpha", "g1")
    assert published.name == "expand" and published.status == "candidate"

    assert library.commons("long").history("expand") == []  # tx pin: still as of its start
    assert library.seat("beta", "long").retrieve("colony pod") == []
    library.start_game("later", ["beta"])
    [hit] = library.seat("beta", "later").retrieve("colony pod")
    assert hit.hash == published.hash
    with pytest.raises(CommonsReadOnly):
        library.commons("later").promote("expand", 1)


def test_an_open_or_abandoned_game_publishes_nothing(library: SkillLibrary) -> None:
    _play(library, "g1", "alpha", "expand")
    head = library._commons.head()
    assert library.publish_at_game_end("alpha", "g1") == []  # still open
    library.abandon_game("g1")
    assert library.publish_at_game_end("alpha", "g1") == []
    assert library._commons.head() == head
    library.close_game("g1")  # closing it later releases it
    assert len(library.publish_at_game_end("alpha", "g1")) == 1


def test_publishing_twice_is_a_no_op(library: SkillLibrary) -> None:
    _play(library, "g1", "alpha", "expand")
    library.close_game("g1")
    assert len(library.publish_at_game_end("alpha", "g1")) == 1
    head = library._commons.head()
    assert library.publish_at_game_end("alpha", "g1") == []
    library._games["g1"].published.clear()  # a restarted orchestrator reads the record instead
    assert library.publish_at_game_end("alpha", "g1") == []
    assert library._commons.head() == head


def test_publication_is_one_step_and_only_new_or_changed(library: SkillLibrary) -> None:
    _play(library, "g1", "alpha", "expand")
    library.seat("alpha", "g1").save("hurry", "hurry production", OTHER)
    library.close_game("g1")
    head = library._commons.head()
    assert {s.name for s in library.publish_at_game_end("alpha", "g1")} == {"expand", "hurry"}
    assert library._commons.head() == head + 1  # one recorded step

    library.start_game("g2", ["alpha"])
    seat = library.seat("alpha", "g2")
    seat.record_trial("expand", 1, "g2", 5, "pass")
    seat.promote("expand", 1)
    library.close_game("g2")
    [changed] = library.publish_at_game_end("alpha", "g2")
    assert (changed.name, changed.status) == ("expand", "verified")
    library.start_game("g3", ["beta"])
    expand = library.commons("g3").get("expand")
    assert expand is not None and expand.status == "verified" and len(expand.trials) == 1
    assert len(skills_from_triples(library._commons.triples())) == 2


# --- retrieval ---------------------------------------------------------------------------------


def test_retrieval_filters_first_then_uses_backend_search() -> None:
    def search(query: str, limit: int, triples: list[Any]) -> list[str]:
        skills = {t[0] for t in triples if t[0].startswith(SKILL_BASE) and t[0].count("/") == 5}
        return sorted(skills, reverse=True)  # a ranking that is not the lexical one

    book = QuipuSkillStore(FakeBackend(search=search), min_verified_seeds=1)
    a = book.save("alpha", "unrelated words", CODE)
    z = book.save("zeta", "also unrelated", OTHER)
    book.save("gated", "needs a knob", CODE.replace("first", "gated"), requires=["focus.fight"])
    book.save("elsewhere", "other surface", CODE.replace("first", "x"), surfaces=["research"])
    assert book.retrieve("colony pod", knobs=[], surface_id="base") == [z, a]
    book.retire("zeta", 1)
    assert book.retrieve("colony pod", knobs=[], surface_id="base") == [a]


def test_retrieval_without_search_is_the_lexical_rank() -> None:
    plain = SkillStore()
    quipu = QuipuSkillStore(FakeBackend())
    for each in (plain, quipu):
        each.save("b_pod", "build a colony pod", CODE)
        each.save("a_pod", "colony pod", OTHER)
        each.save("lab", "research a technology", CODE.replace("first", "lab"))
    assert [s.name for s in quipu.retrieve("colony pod now")] == [
        s.name for s in plain.retrieve("colony pod now")
    ]


def test_qp_backend_runs_argv_with_db_and_shapes_and_no_shell(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kw: Any) -> Any:
        calls.append((argv, kw))
        import subprocess

        return subprocess.CompletedProcess(argv, 1, "SHACL validation failed: 1 violation(s)\n", "")

    backend = QpBackend(tmp_path / "seat.db", qp="qp-test", run=run)
    with pytest.raises(SkillWriteRefused, match="SHACL"):
        backend.knot(to_turtle(_bad()))
    [(argv, kw)] = calls
    assert argv[0] == "qp-test" and argv[1] == "knot"
    assert argv[-2:] == ["--db", str(tmp_path / "seat.db")] and "--shapes" in argv
    assert kw.get("shell") is None and kw["check"] is False
