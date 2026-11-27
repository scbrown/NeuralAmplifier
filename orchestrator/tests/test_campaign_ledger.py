"""The campaign ledger, aegis-8fye.8 slice 2 (docs/seats-and-policy.md §6).

Two lanes run the ledger itself: ``fake`` (the in-memory backend with the ledger's shape check)
and ``qp`` (the real CLI against a temporary database, skipped when ``qp`` is absent). Then the
wiring: the review cadence writes a Review per review, a missed one as outcome=missed; the
policy and plan routes attach commitments under a seat token; the review context carries the
open commitments and the last five reviews; and no seat reads another's ledger.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.config import Seat
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.doorbell import Doorbell
from neural_amplifier.ledger import (
    LEDGER_SHAPES_PATH,
    LEDGER_SHAPES_SHA256,
    CampaignLedger,
    LedgerBook,
    fake_ledger_backend,
    ledger_shapes_bytes,
    qp_ledger_backend,
)
from neural_amplifier.pending import DecisionQueue
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.skill_quipu import SkillWriteRefused
from neural_amplifier.skill_shapes import NA, ShapesError
from neural_amplifier.strategist import ReviewScheduler

HAVE_QP = shutil.which("qp") is not None
LANES = ["fake", pytest.param("qp", marks=pytest.mark.skipif(not HAVE_QP, reason="qp absent"))]


@pytest.fixture(params=LANES)
def open_backend(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "fake":
        return lambda seat: fake_ledger_backend()
    return lambda seat: qp_ledger_backend(tmp_path / f"{seat}.db")


def hold(**kw: Any) -> dict[str, Any]:
    return {"key": "hold.north", "text": "hold the northern bases", "from_turn": 3, **kw}


# ---------------------------------------------------------------- the shape and its pin


def test_the_shape_is_pinned_by_its_bytes(tmp_path: Path) -> None:
    assert hashlib.sha256(LEDGER_SHAPES_PATH.read_bytes()).hexdigest() == LEDGER_SHAPES_SHA256
    drifted = tmp_path / "na-ledger.shapes.ttl"
    drifted.write_bytes(LEDGER_SHAPES_PATH.read_bytes() + b"\n")
    with pytest.raises(ShapesError):
        ledger_shapes_bytes(drifted)
    with pytest.raises(ShapesError):
        qp_ledger_backend(tmp_path / "x.db", shapes=drifted)


def test_the_shape_mints_nothing_outside_na() -> None:
    text = LEDGER_SHAPES_PATH.read_text()
    assert f"@prefix na:   <{NA}> ." in text
    assert "aegis:" not in text.replace("aegis:GitHubReview", "")  # named only to disclaim it
    assert "@prefix aegis" not in text


# ---------------------------------------------------------------- the ledger, both lanes


def test_supersede_keeps_history_and_reads_take_the_latest(open_backend) -> None:
    ledger = CampaignLedger(open_backend("malcolm"), "malcolm")
    first = ledger.commit(hold(until_turn=20))
    second = ledger.commit(hold(until_turn=30, text="hold the north until 30"))
    kept = ledger.commit(hold(until_turn=30, status="kept"))
    assert kept.supersedes == second.iri and second.supersedes == first.iri
    history = ledger.history("hold.north")
    assert [c.status for c in history] == ["superseded", "superseded", "kept"]
    assert history[0].until_turn == 20 and history[0].text == "hold the northern bases"
    assert [c.status for c in ledger.commitments()] == ["kept"]
    assert ledger.open_commitments() == []


def test_one_knot_per_write(open_backend) -> None:
    backend = open_backend("malcolm")
    ledger = CampaignLedger(backend, "malcolm")
    ledger.commit(hold(condition="until the war ends"))
    ledger.record_review("review-s3-1", 10, "cadence", "acted", directives=["d-1"])
    assert backend.head() == 2


@pytest.mark.parametrize(
    "bad",
    [
        hold(key="Hold North", until_turn=9),  # key off the pattern
        hold(),  # no untilTurn and no condition
        hold(until_turn=9, status="done"),  # status off the enum
        hold(until_turn=9, text=""),  # empty text
        hold(until_turn=-1),  # negative turn
    ],
)
def test_a_bad_commitment_is_refused_at_write(open_backend, bad: dict[str, Any]) -> None:
    backend = open_backend("malcolm")
    ledger = CampaignLedger(backend, "malcolm")
    with pytest.raises(SkillWriteRefused):
        ledger.commit(bad)
    assert backend.head() == 0 and ledger.commitments() == []


def test_a_bad_review_is_refused_at_write(open_backend) -> None:
    backend = open_backend("malcolm")
    ledger = CampaignLedger(backend, "malcolm")
    with pytest.raises(SkillWriteRefused):
        ledger.record_review("review-s3-1", 10, "cadence", "ignored")
    assert backend.head() == 0


def test_a_missed_review_is_recorded(open_backend) -> None:
    ledger = CampaignLedger(open_backend("malcolm"), "malcolm")
    ledger.record_review("review-s3-1", 4, "base_lost", "missed")
    (review,) = ledger.reviews()
    assert (review.outcome, review.trigger, review.reason) == (
        "missed",
        "discontinuity",
        "base_lost",
    )


def test_seat_a_cannot_read_seat_bs_ledger(open_backend) -> None:
    book = LedgerBook(open_backend)
    book.ledger("alpha").commit(hold(until_turn=9))
    book.ledger("alpha").record_review("review-s3-1", 4, "cadence", "missed")
    assert book.ledger("beta").commitments() == [] and book.ledger("beta").reviews() == []
    assert book.ledger("alpha") is book.ledger("alpha")


def test_a_shared_backend_still_does_not_leak() -> None:
    backend = fake_ledger_backend()  # a misconfiguration: two identities on one store
    CampaignLedger(backend, "alpha").commit(hold(until_turn=9))
    assert CampaignLedger(backend, "beta").commitments() == []


def test_the_context_is_open_commitments_and_the_last_five_reviews() -> None:
    ledger = CampaignLedger(fake_ledger_backend(), "malcolm")
    ledger.commit(hold(until_turn=9))
    ledger.commit({"key": "tech.lasers", "text": "lasers", "from_turn": 1, "until_turn": 5})
    ledger.commit(
        {
            "key": "tech.lasers",
            "text": "lasers",
            "from_turn": 1,
            "until_turn": 5,
            "status": "broken",
        }
    )
    for n in range(7):
        ledger.record_review(f"review-s3-{n}", n * 10, "cadence", "missed")
    ctx = ledger.context()
    assert [c["key"] for c in ctx["open_commitments"]] == ["hold.north"]  # type: ignore[index]
    assert [r["review_id"] for r in ctx["recent_reviews"]] == [  # type: ignore[union-attr]
        f"review-s3-{n}" for n in range(2, 7)
    ]


# ---------------------------------------------------------------- the review cadence


def view(turn: int, faction: int = 3) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=turn,
        faction="Gaians",
        faction_id=faction,
        surface_id="base.production",
        action_space=[Action(id="facility:4", action="Recycling Tanks")],
    )


class QuietBell(Doorbell):
    def __init__(self) -> None:
        super().__init__(target="", enabled=False)

    def ring_review(self, review_id: str) -> bool:
        return True


def _join(s: ReviewScheduler) -> None:
    for t in s.threads:
        t.join(timeout=10)


def test_the_cadence_writes_a_review_per_review() -> None:
    ledger = CampaignLedger(fake_ledger_backend(), "three")
    seats = {3: Seat(3, "three", "agent", strategist="agent")}
    s = ReviewScheduler(seats, QuietBell(), every=3, ledger_for=lambda fid: ledger)
    for t in range(1, 5):  # rings at 4
        s.observe(view(t))
    s.note_write(3, "directive", "dir-1")
    s.note_write(3, "policy", "policy:focus.growth@turn4")
    for t in range(5, 11):  # 4's closes acted at 7 and rings again at 7; 7's is missed at 10
        s.observe(view(t))
    _join(s)
    acted, missed = ledger.reviews()
    assert acted.outcome == "acted" and acted.directives == ("dir-1",)
    assert acted.policy_changes == ("policy:focus.growth@turn4",)
    assert missed.outcome == "missed" and missed.review_id == s.missed[0]["review_id"]


# ---------------------------------------------------------------- the routes


@pytest.fixture
def seated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NA_ALLOW_BLIND", "1")
    monkeypatch.setenv("NA_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("NA_REVIEW_EVERY", "3")
    monkeypatch.setenv("NA_AGENT_TIMEOUT", "0.01")
    monkeypatch.delenv("NA_TMUX_TARGET", raising=False)
    monkeypatch.delenv("NA_DOORBELL_CMD", raising=False)
    queue = DecisionQueue()
    router = SeatRouter(
        {
            3: Seat(3, "malcolm", "agent", strategist="agent"),
            4: Seat(4, "ellie", "agent", strategist="agent"),
        },
        build=lambda seat: AgentBrain(
            queue=queue, doorbell=Doorbell(target="", enabled=False), timeout=0.01
        ),
    )
    app = create_app(brain=router)
    app.state.reviews.doorbell = QuietBell()
    tokens = app.state.seat_tokens.tokens
    return app, TestClient(app), {3: {SEAT_HEADER: tokens[3]}, 4: {SEAT_HEADER: tokens[4]}}


def test_a_policy_write_attaches_a_commitment(seated) -> None:
    app, client, h = seated
    r = client.post(
        "/agent/policy",
        headers=h[3],
        json={
            "knob": "focus.growth",
            "value": 1,
            "reason": "grow first",
            "commitment": hold(until_turn=20),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["commitment"]["via"] == "policy"
    ctx = client.get("/agent/review", headers=h[3]).json()
    assert [c["key"] for c in ctx["open_commitments"]] == ["hold.north"]
    # Seat 4 holds a token too, and sees none of seat 3's ledger.
    assert client.get("/agent/review", headers=h[4]).json()["open_commitments"] == []


def test_a_plan_install_attaches_a_commitment(seated) -> None:
    _, client, h = seated
    r = client.post(
        "/agent/plan",
        headers=h[4],
        json={"turn": 5, "entries": [], "commitment": hold(condition="until peace")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["commitment"]["ref"] == "plan:turn5"


def test_a_bad_commitment_is_refused_before_the_write(seated) -> None:
    app, client, h = seated
    r = client.post(
        "/agent/policy",
        headers=h[3],
        json={"knob": "focus.growth", "value": 1, "reason": "x", "commitment": hold()},
    )
    assert r.status_code == 422 and "commitment refused" in r.text
    assert client.get("/agent/policy", headers=h[3]).json()["knobs"] == {}


def test_the_review_context_needs_a_seat_token(seated) -> None:
    _, client, _ = seated
    assert client.get("/agent/review").status_code == 401


def test_a_missed_review_reaches_the_seats_ledger(seated) -> None:
    app, client, h = seated
    for t in range(1, 8):  # rings at 4, missed at 7
        body = view(t).model_dump(mode="json", exclude_none=True)
        assert client.post("/decide", json=body).status_code == 200
    _join(app.state.reviews)
    ctx = client.get("/agent/review", headers=h[3]).json()
    assert [r["outcome"] for r in ctx["recent_reviews"]] == ["missed"]
    assert ctx["review"] is not None  # the review rung at 7 is open now
