"""The skill library in the running service (aegis-gqi0 slice 3), FakeBackend only.

One SkillLibrary per app, seat books keyed by the same seat identity as the ledgers. A seated
app starts its game when it is built, pinning the commons; ``POST /game/end`` closes it and
publishes each seat's book once, or abandons it and publishes nothing. A seat's SkillBrain reads
its own book plus the pinned commons and never another seat's book. Unseated runs are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.config import Seat
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.skill_quipu import FakeBackend, SeatSkills, SkillLibrary
from neural_amplifier.skills import SkillBrain, SkillStore

CODE = 'def on_decision(view):\n    return choose(view["action_space"][0]["id"], "from a skill")\n'
DESC = "base production colony pod while surplus"


def view(turn: int, faction: int) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=turn,
        faction="Gaians",
        faction_id=faction,
        surface_id="base.production",
        action_space=[Action(id="facility:4", action="Recycling Tanks")],
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(tmp_path))
    monkeypatch.delenv("NA_TMUX_TARGET", raising=False)
    monkeypatch.delenv("NA_DOORBELL_CMD", raising=False)


def _library() -> SkillLibrary:
    return SkillLibrary(lambda _identity: FakeBackend(), FakeBackend(), min_verified_seeds=1)


@pytest.fixture
def seated(env: None):
    library = _library()
    originals: dict[int, SkillStore] = {}

    def build(seat: Seat) -> SkillBrain:
        originals[seat.faction_id] = SkillStore()
        return SkillBrain(originals[seat.faction_id], ScriptedBrain(), practice=True)

    router = SeatRouter({3: Seat(3, "malcolm", "scripted"), 4: Seat(4, "ellie", "scripted")}, build)
    app = create_app(brain=router, skills=library)
    tokens = app.state.seat_tokens.tokens
    headers = {3: {SEAT_HEADER: tokens[3]}, 4: {SEAT_HEADER: tokens[4]}}
    return app, TestClient(app), library, router, originals, headers


def test_one_library_on_app_state_keyed_like_the_ledgers(seated) -> None:
    app, _, library, router, originals, _ = seated
    assert app.state.skills is library
    game = library.game(app.state.skill_game)
    assert app.state.skill_game == app.state.orchestrator.game_id
    assert game.state == "open"
    assert game.seats == frozenset(app.state.seat_identity.values()) == {"malcolm", "ellie"}
    for fid, driver in router.drivers.items():
        assert isinstance(driver.store, SeatSkills)
        assert driver.store is app.state.seat_skills[fid]
        assert driver.store is not originals[fid]
        assert driver.store._book is library.book(app.state.seat_identity[fid])


def test_two_seats_cannot_see_each_others_book(seated) -> None:
    app, client, _, router, _, _ = seated
    router.drivers[3].store.save("grow", DESC, CODE)
    assert router.drivers[3].store.get("grow") is not None
    assert router.drivers[4].store.get("grow") is None
    assert router.drivers[4].store.retrieve(DESC, statuses=("candidate",)) == []
    assert router.drivers[4].store.eligible(("candidate",)) == []

    # And in play: seat 3's decision is answered from its book, seat 4's falls through.
    three = client.post("/decide", json=view(1, 3).model_dump(mode="json", exclude_none=True))
    four = client.post("/decide", json=view(1, 4).model_dump(mode="json", exclude_none=True))
    assert three.status_code == four.status_code == 200, (three.text, four.text)
    assert router.drivers[3].last_skill is not None
    assert router.drivers[3].last_skill.name == "grow"
    assert router.drivers[4].last_skill is None


def test_a_commons_write_after_start_game_is_invisible_to_that_game(seated) -> None:
    app, _, library, router, _, _ = seated
    head = library._commons.head()
    # Another game publishes to the commons after this one started.
    library.start_game("elsewhere", ["outsider"])
    library.seat("outsider", "elsewhere").save("shared", DESC, CODE)
    library.close_game("elsewhere")
    assert len(library.publish_at_game_end("outsider", "elsewhere")) == 1
    assert library._commons.head() > head

    seat = router.drivers[3].store
    assert seat.retrieve(DESC, statuses=("candidate",)) == []
    assert seat.eligible(("candidate",)) == []
    # A game started later does see it: the pin, not the write, is what hid it.
    library.start_game("later", ["malcolm"])
    assert [s.name for s in library.seat("malcolm", "later").eligible(("candidate",))] == ["shared"]


def test_publish_happens_only_after_close_and_only_once(seated) -> None:
    app, client, library, router, _, headers = seated
    router.drivers[3].store.save("grow", DESC, CODE)
    head = library._commons.head()
    assert library.publish_at_game_end("malcolm", app.state.skill_game) == []  # still open
    assert library._commons.head() == head

    assert client.post("/game/end", headers=headers[3]).status_code == 403  # not a seat's call
    assert library._commons.head() == head

    r = client.post("/game/end", json={"outcome": "closed"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "closed"
    published = r.json()["published"]
    assert published["ellie"] == [] and [p.split("#")[0] for p in published["malcolm"]] == [
        "grow@v1"
    ]
    after = library._commons.head()
    assert after > head

    assert client.post("/game/end").status_code == 409
    assert library.publish_at_game_end("malcolm", app.state.skill_game) == []
    assert library._commons.head() == after


def test_an_abandoned_game_publishes_nothing(seated) -> None:
    app, client, library, router, _, _ = seated
    router.drivers[3].store.save("grow", DESC, CODE)
    head = library._commons.head()
    r = client.post("/game/end", json={"outcome": "abandoned"})
    assert r.status_code == 200, r.text
    assert r.json() == {"game_id": app.state.skill_game, "state": "abandoned", "published": {}}
    assert client.post("/game/end", json={"outcome": "closed"}).status_code == 409
    assert library.game(app.state.skill_game).state == "abandoned"
    assert library._commons.head() == head


def test_a_bad_outcome_is_refused(seated) -> None:
    app, client, library, _, _, _ = seated
    assert client.post("/game/end", json={"outcome": "won"}).status_code == 422
    assert library.game(app.state.skill_game).state == "open"


def test_the_legacy_unseated_path_is_unchanged(env: None) -> None:
    store = SkillStore()
    store.save("grow", DESC, CODE)
    brain = SkillBrain(store, ScriptedBrain(), practice=True)
    library = _library()
    app = create_app(brain=brain, skills=library)
    client = TestClient(app)
    assert brain.store is store
    assert app.state.skill_game is None and app.state.seat_skills == {}
    assert library._games == {}
    r = client.post("/decide", json=view(1, 3).model_dump(mode="json", exclude_none=True))
    assert r.status_code == 200, r.text
    assert brain.last_skill is not None and brain.last_skill.name == "grow"
    assert client.post("/game/end").status_code == 404
    assert library._commons.head() == 0


def test_the_default_library_is_in_memory(env: None) -> None:
    router = SeatRouter({3: Seat(3, "malcolm", "scripted")})
    app = create_app(brain=router)
    assert isinstance(app.state.skills, SkillLibrary)
    assert app.state.skills.game(app.state.skill_game).seats == {"malcolm"}
    assert app.state.seat_skills == {}  # a scripted driver is no SkillBrain: nothing rebound
