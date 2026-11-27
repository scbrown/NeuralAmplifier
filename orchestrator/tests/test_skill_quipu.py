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
    PREFIX,
    CommonsReadOnly,
    FakeBackend,
    QpBackend,
    QuipuSkillStore,
    SeatSkills,
    SkillBackend,
    SkillLibrary,
    SkillWriteRefused,
    commons_from_triples,
    commons_prefix,
)
from neural_amplifier.skill_shapes import SKILL_BASE, to_turtle, version_iri
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
    head = library._commons.head()
    [changed] = library.publish_at_game_end("alpha", "g2")
    assert library._commons.head() == head + 1  # a changed version is one recorded step too
    assert (changed.name, changed.status) == ("expand", "verified")
    library.start_game("g3", ["beta"])
    expand = library.commons("g3").get("expand")
    assert expand is not None and expand.status == "verified" and len(expand.trials) == 1
    assert [(e.seat, e.skill.name) for e in commons_from_triples(library._commons.triples())] == [
        ("alpha", "expand"),
        ("alpha", "hurry"),
    ]


# --- version identity and append-only writes (review eph-review-c1bc0f) -------------------------


CHECK = {"metric": "base_count", "comparator": "at_least", "target": 2, "horizon_turns": 5}


def _subjects(backend: SkillBackend) -> set[str]:
    return {t[0] for t in backend.triples()}


def test_same_code_with_a_new_check_is_its_own_version_node(
    new_backend: Callable[[], SkillBackend],
) -> None:
    backend = new_backend()
    book = QuipuSkillStore(backend)
    v1 = book.save("expand", "colony pod", CODE)
    v2 = book.save("expand", "colony pod", CODE, check=CHECK)
    assert (v1.version, v2.version, v1.hash) == (1, 2, v2.hash)
    assert {version_iri("expand", 1), version_iri("expand", 2)} <= _subjects(backend)
    assert QuipuSkillStore(backend).history("expand") == [v1, v2]


def test_a_revert_a_b_a_keeps_three_versions(new_backend: Callable[[], SkillBackend]) -> None:
    backend = new_backend()
    book = QuipuSkillStore(backend, min_verified_seeds=1)
    a1 = book.save("expand", "colony pod", CODE)
    book.record_trial("expand", 1, "g1", 1, "pass")
    book.promote("expand", 1)
    book.save("expand", "colony pod", OTHER)
    a3 = book.save("expand", "colony pod", CODE)
    assert a3.version == 3 and a3.hash == a1.hash and a3.status == "candidate"
    reopened = QuipuSkillStore(backend)
    assert reopened.history("expand") == book.history("expand")
    assert reopened.get("expand", 1).status == "verified"  # untouched by the revert
    assert [s.version for s in reopened.history("expand")] == [1, 2, 3]


def test_every_write_only_adds(new_backend: Callable[[], SkillBackend]) -> None:
    backend = new_backend()
    assert not hasattr(backend, "retract")
    book = QuipuSkillStore(backend, min_verified_seeds=1)
    seen: set[Any] = set()
    steps: list[Callable[[], Any]] = [
        lambda: book.save("expand", "colony pod", CODE),
        lambda: book.record_trial("expand", 1, "g1", 4, "pass", {"base_count": 1}),
        lambda: book.promote("expand", 1),
        lambda: book.save("expand", "colony pod", OTHER),
        lambda: book.retire("expand", 2, "worse"),
        lambda: book.retire("expand", 1, "superseded"),
    ]
    for step in steps:
        head = backend.head()
        step()
        now = set(backend.triples())
        assert seen <= now and backend.head() == head + 1  # one knot, nothing retracted
        seen = now
    assert [s.status for s in QuipuSkillStore(backend).history("expand")] == ["retired"] * 2


def test_a_second_value_on_an_existing_version_is_refused(
    new_backend: Callable[[], SkillBackend],
) -> None:
    """Both lanes check the merged state: qp at ingest, the fake by construction."""
    backend = new_backend()
    book = QuipuSkillStore(backend)
    book.save("expand", "colony pod", CODE)
    before = backend.triples()
    second_status = PREFIX + f'<{version_iri("expand", 1)}> na:status "verified" .\n'
    with pytest.raises(SkillWriteRefused):
        backend.knot(second_status)
    partial_state = PREFIX + (
        f"<{version_iri('expand', 1)}/state/9> a na:SkillState ;"
        f" na:ofVersion <{version_iri('expand', 1)}> .\n"
    )
    with pytest.raises(SkillWriteRefused):  # no status, no tx: the document itself fails
        backend.knot(partial_state)
    assert backend.triples() == before
    assert QuipuSkillStore(backend).get("expand").status == "candidate"


class _Crash(RuntimeError):
    pass


class _Crashing:
    """Wraps a backend; ``armed`` makes the next knot fail before anything is committed."""

    def __init__(self, inner: SkillBackend) -> None:
        self.inner, self.armed = inner, False

    def knot(self, turtle: str) -> int:
        if self.armed:
            self.armed = False
            raise _Crash("connection lost")
        return self.inner.knot(turtle)

    def validate(self, turtle: str) -> None:
        self.inner.validate(turtle)

    def triples(self, tx: int | None = None) -> list[Any]:
        return self.inner.triples(tx)

    def head(self) -> int:
        return self.inner.head()


@pytest.mark.parametrize(
    "write",
    [
        lambda b: b.save("expand", "colony pod", OTHER),
        lambda b: b.record_trial("expand", 1, "g1", 9, "pass"),
        lambda b: b.promote("expand", 1),
        lambda b: b.retire("expand", 1, "worse"),
    ],
)
def test_a_crashed_book_write_changes_nothing(
    write: Callable[[QuipuSkillStore], Any], new_backend: Callable[[], SkillBackend]
) -> None:
    backend = _Crashing(new_backend())
    book = QuipuSkillStore(backend, min_verified_seeds=1)
    book.save("expand", "colony pod", CODE)
    book.record_trial("expand", 1, "g0", 1, "pass")
    before, history = backend.triples(), book.history("expand")
    backend.armed = True
    with pytest.raises(_Crash):
        write(book)
    assert backend.triples() == before
    assert book.history("expand") == history
    assert QuipuSkillStore(backend).history("expand") == history
    write(book)  # and the store still works afterwards
    assert QuipuSkillStore(backend).history("expand") == book.history("expand")


def _crashing_library(new_backend: Callable[[], SkillBackend]) -> tuple[SkillLibrary, _Crashing]:
    books: dict[str, SkillBackend] = {}
    commons = _Crashing(new_backend())

    def open_book(seat: str) -> SkillBackend:
        return books.setdefault(seat, new_backend())

    return SkillLibrary(open_book, commons, min_verified_seeds=1), commons


def test_a_crashed_or_refused_publication_changes_nothing(
    new_backend: Callable[[], SkillBackend],
) -> None:
    library, commons = _crashing_library(new_backend)
    _play(library, "g1", "alpha", "expand")
    library.close_game("g1")
    library.publish_at_game_end("alpha", "g1")
    library.start_game("g2", ["alpha"])
    seat = library.seat("alpha", "g2")
    seat.record_trial("expand", 1, "g2", 5, "pass")
    seat.promote("expand", 1)
    seat.save("hurry", "hurry production", OTHER)
    library.close_game("g2")
    before = commons.triples()
    commons.armed = True
    with pytest.raises(_Crash):
        library.publish_at_game_end("alpha", "g2")
    assert commons.triples() == before
    library.start_game("g3", ["beta"])
    assert library.commons("g3").get("expand").status == "candidate"  # the old state reads
    assert library.commons("g3").get("hurry") is None

    book = library.book("alpha")  # a version the shape refuses: the whole knot is refused
    bad = replace(book.get("hurry"), status="trusted")  # type: ignore[arg-type]
    with book._lock:
        book._versions["hurry"][-1] = bad
    with pytest.raises(SkillWriteRefused):
        library.publish_at_game_end("alpha", "g2")
    assert commons.triples() == before


def test_a_state_no_publication_carries_is_ignored(library: SkillLibrary) -> None:
    _play(library, "g1", "alpha", "expand")
    library.close_game("g1")
    library.publish_at_game_end("alpha", "g1")
    entry = commons_prefix("alpha") + "expand/v1"
    stray = PREFIX + (
        f'<{entry}/state/99> a na:SkillState ; na:ofVersion <{entry}> ; na:status "retired" ;'
        ' na:tx "99"^^<http://www.w3.org/2001/XMLSchema#integer> .\n'
    )
    library._commons.knot(stray)  # e.g. half of a write that never got its publication
    library.start_game("g2", ["beta"])
    assert library.commons("g2").get("expand").status == "candidate"


def _publish(library: SkillLibrary, game: str, seat: str, code: str, verify: bool) -> None:
    library.start_game(game, [seat])
    s = library.seat(seat, game)
    s.save("expand", "colony pod while surplus", code)
    if verify:
        s.record_trial("expand", 1, game, 1, "pass")
        s.promote("expand", 1)
    library.close_game(game)
    assert library.publish_at_game_end(seat, game)


def test_a_candidate_from_one_seat_never_downgrades_anothers_verified(
    library: SkillLibrary,
) -> None:
    _publish(library, "ga", "alpha", CODE, verify=True)
    _publish(library, "gb", "beta", CODE, verify=False)  # same code, published later
    library.start_game("later", ["gamma"])
    commons = library.commons("later")
    assert [(e.seat, e.skill.status) for e in commons.entries("expand")] == [
        ("alpha", "verified"),
        ("beta", "candidate"),
    ]
    got = commons.get("expand")
    assert got is not None and got.status == "verified" and len(got.trials) == 1
    assert commons.eligible(("verified", "candidate")) == [got]
    assert [s.status for s in commons.history("expand")] == ["verified", "candidate"]


def test_commons_precedence_is_latest_publication_then_seat(
    new_backend: Callable[[], SkillBackend],
) -> None:
    def build(order: list[str]) -> SkillLibrary:
        books: dict[str, SkillBackend] = {}
        lib = SkillLibrary(lambda s: books.setdefault(s, new_backend()), new_backend(), 1)
        for i, seat in enumerate(order):
            _publish(lib, f"g{i}", seat, CODE.replace("first", seat), verify=False)
        lib.start_game("read", ["x"])
        return lib

    for order in (["alpha", "beta"], ["beta", "alpha"]):
        commons = build(order).commons("read")
        newest = commons.get("expand")
        assert newest is not None and order[-1] in newest.code  # the latest publication wins
        assert commons.get("expand", 1) is newest
        assert commons.eligible(("candidate",)) == [newest]

    lib = build([])
    tx = lib._commons.head()
    for seat in ("beta", "alpha"):  # one tie on status and tx: seat id decides
        e = commons_prefix(seat) + "expand/v1"
        skill = Skill("expand", 1, "colony pod", CODE, content_hash(CODE))
        lib._commons.knot(
            to_turtle(skill, authored_by=seat, prefix=commons_prefix(seat))
            + PREFIX
            + f'<{e}/state/{tx + 1}> a na:SkillState ; na:ofVersion <{e}> ; na:status "candidate" ;'
            f' na:tx "{tx + 1}"^^<http://www.w3.org/2001/XMLSchema#integer> .\n'
        )
    states = [commons_prefix(s) + f"expand/v1/state/{tx + 1}" for s in ("alpha", "beta")]
    lib._commons.knot(
        PREFIX
        + "<http://neuralamplifier.local/publication/both> a na:Publication ;"
        + ' na:publishedBy "both" ; na:game "tie" ;'
        + f' na:tx "{tx + 1}"^^<http://www.w3.org/2001/XMLSchema#integer> ;'
        + " ; ".join(f" na:carriesState <{s}>" for s in states)
        + " .\n"
    )
    lib.start_game("tie-read", ["x"])
    assert [e.seat for e in lib.commons("tie-read").entries("expand")] == ["alpha", "beta"]


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
