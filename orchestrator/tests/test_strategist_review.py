"""The strategist seat, slice 1 (docs/seats-and-policy.md §6; aegis-8fye.8).

Three things: the doorbell as a command template, a per-seat review cadence with early triggers
read from existing metrics, and a missed review recorded on the seat's log while the standing
policy holds. None of it may hold a turn.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import neural_amplifier.doorbell as doorbell_mod
from neural_amplifier.agent_brain import AgentBrain
from neural_amplifier.config import ConfigError, Seat
from neural_amplifier.contract import Action, WorldView
from neural_amplifier.decisions import DecisionLog
from neural_amplifier.doorbell import (
    Doorbell,
    DoorbellConfigError,
    parse_command,
    review_sentence,
)
from neural_amplifier.pending import DecisionQueue
from neural_amplifier.seats import SEAT_HEADER, SeatRouter
from neural_amplifier.service import create_app
from neural_amplifier.strategist import DEFAULT_EVERY, ReviewScheduler, every_from_env

CMD = ["st", "inbox", "-d", "AGENT", "{message}"]


def view(turn: int, faction: int = 3, **metrics: float) -> WorldView:
    return WorldView(
        engine="thinker",
        scope="base",
        turn=turn,
        faction="Gaians",
        faction_id=faction,
        surface_id="base.production",
        action_space=[Action(id="facility:4", action="Recycling Tanks")],
        metrics=metrics or None,
    )


class Recorder:
    """Captures argv instead of running it."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.argvs.append(list(argv))
        self.kwargs.append(kwargs)
        return subprocess.CompletedProcess(argv, 0)


class RecordingBell(Doorbell):
    def __init__(self) -> None:
        super().__init__(command=CMD)
        self.reviews: list[str] = []

    def ring_review(self, review_id: str) -> bool:
        self.reviews.append(review_id)
        return True


# ---------------------------------------------------------------- 1a: the command template


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(["st", "inbox", "-d", "AGENT"]),  # zero placeholders
        json.dumps(["st", "{message}", "{message}"]),  # two
        json.dumps(["st", "--a={message}{message}"]),  # two in one element
        json.dumps("st inbox {message}"),  # a string, not an argv
        json.dumps([]),
        json.dumps(["", "{message}"]),
        json.dumps(["st", 3, "{message}"]),
        "not json",
    ],
)
def test_a_template_without_exactly_one_placeholder_is_refused(raw: str) -> None:
    with pytest.raises(DoorbellConfigError):
        parse_command(raw)


def test_the_template_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_TMUX_TARGET", raising=False)
    monkeypatch.setenv("NA_DOORBELL_CMD", json.dumps(CMD))
    bell = Doorbell.from_env()
    assert bell.enabled and bell.command == CMD and bell.target == ""


def test_the_default_stays_the_tmux_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_DOORBELL_CMD", raising=False)
    monkeypatch.setenv("NA_TMUX_TARGET", "pane")
    bell = Doorbell.from_env()
    assert bell.command is None and bell.target == "pane"


def test_both_transports_set_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_TMUX_TARGET", "pane")
    monkeypatch.setenv("NA_DOORBELL_CMD", json.dumps(CMD))
    with pytest.raises(DoorbellConfigError, match="not both"):
        Doorbell.from_env()
    with pytest.raises(DoorbellConfigError):
        Doorbell(target="pane", command=CMD)


def test_the_command_runs_as_an_argv_with_no_shell_and_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Recorder()
    monkeypatch.setattr(doorbell_mod.subprocess, "run", run)
    bell = Doorbell(command=CMD)
    assert bell.ring("base.production-1", "base.production") is True
    assert bell.ring_review("review-s3-1") is True

    decision, review = run.argvs
    assert decision[:4] == ["st", "inbox", "-d", "AGENT"] and len(decision) == 5
    assert decision[4].startswith("A game decision is waiting (base.production)")
    assert decision[4].endswith("[base.production-1]")
    assert review == [*CMD[:4], review_sentence("review-s3-1")]
    for kwargs in run.kwargs:
        assert kwargs.get("shell") in (None, False)
        assert kwargs["timeout"] == 5


def test_metacharacters_in_the_template_are_passed_literally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The template is the operator's text and is never shell-parsed or format-expanded."""
    run = Recorder()
    monkeypatch.setattr(doorbell_mod.subprocess, "run", run)
    template = ["notify", "$(id); rm -rf ~ | `x` {0} {other}", "--body={message} && echo $HOME"]
    Doorbell(command=template).ring_review("review-s1-1")
    (argv,) = run.argvs
    assert argv[0] == "notify"
    assert argv[1] == "$(id); rm -rf ~ | `x` {0} {other}"
    assert argv[2] == f"--body={review_sentence('review-s1-1')} && echo $HOME"


def test_metacharacters_actually_reach_a_real_process_literally(tmp_path: Path) -> None:
    """End to end with a real argv: the child sees one argument, byte for byte."""
    out = tmp_path / "argv.json"
    script = "import json,sys; json.dump(sys.argv[1:], open(sys.argv[1], 'w'))"
    import sys

    bell = Doorbell(command=[sys.executable, "-c", script, str(out), "$(touch x); {message}; |&"])
    assert bell.ring_review("review-s2-7") is True
    assert json.loads(out.read_text()) == [
        str(out),
        f"$(touch x); {review_sentence('review-s2-7')}; |&",
    ]
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("bad", ["", "a b", "x;rm", "$(id)", "a" * 65, "r\nx", "é"])
def test_an_unsafe_id_is_refused_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    run = Recorder()
    monkeypatch.setattr(doorbell_mod.subprocess, "run", run)
    bell = Doorbell(command=CMD)
    assert bell.ring_review(bad) is False
    assert bell.ring(bad) is False
    assert run.argvs == []


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(1, "st"),
        subprocess.TimeoutExpired("st", 5),
        FileNotFoundError("st"),
    ],
)
def test_a_failing_command_is_swallowed_and_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, exc: Exception
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(doorbell_mod.subprocess, "run", boom)
    bell = Doorbell(command=CMD)
    with caplog.at_level("WARNING", logger="neural_amplifier.doorbell"):
        assert bell.ring_review("review-s1-1") is False
        assert bell.ring("d-1") is False
    assert sum("failed" in r.getMessage() for r in caplog.records) == 1


# ---------------------------------------------------------------- 1b: cadence and triggers


def scheduler(every: int = 10, **kw: Any) -> tuple[ReviewScheduler, RecordingBell]:
    bell = RecordingBell()
    seats = {3: Seat(3, "three", "agent", strategist="agent"), 4: Seat(4, "four", "scripted")}
    return ReviewScheduler(seats, bell, every=every, **kw), bell


def _join(s: ReviewScheduler) -> None:
    for t in s.threads:
        t.join(timeout=5)


def test_the_default_cadence_is_ten_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NA_REVIEW_EVERY", raising=False)
    assert every_from_env() == DEFAULT_EVERY == 10
    monkeypatch.setenv("NA_REVIEW_EVERY", "4")
    assert every_from_env() == 4
    for bad in ("0", "-1", "ten"):
        monkeypatch.setenv("NA_REVIEW_EVERY", bad)
        with pytest.raises(ConfigError):
            every_from_env()


def test_a_review_rings_every_n_turns_per_seat() -> None:
    s, bell = scheduler(every=10)
    rung = [s.observe(view(t)) for t in range(1, 32)]
    _join(s)
    turns = [r.turn for r in rung if r is not None]
    assert turns == [11, 21, 31]
    assert all(r.reason == "cadence" for r in rung if r is not None)
    assert bell.reviews == ["review-s3-t11", "review-s3-t21", "review-s3-t31"]


def test_a_seat_with_no_strategist_is_never_reviewed() -> None:
    s, bell = scheduler(every=2)
    for t in range(1, 10):
        assert s.observe(view(t, faction=4)) is None
        assert s.observe(view(t, faction=6)) is None  # unseated
    _join(s)
    assert bell.reviews == []


def test_a_base_lost_rings_early() -> None:
    s, bell = scheduler(every=10)
    s.observe(view(1, base_count=4, factions_at_war=0))
    assert s.observe(view(2, base_count=4, factions_at_war=0)) is None
    review = s.observe(view(3, base_count=3, factions_at_war=0))
    _join(s)
    assert review is not None and review.reason == "base_lost" and review.turn == 3
    assert bell.reviews == [review.id]
    # The cadence counts from the early review, not from the start.
    assert [s.observe(view(t)) for t in range(4, 13)] == [None] * 9
    s.note_write(3, "plan")
    assert s.observe(view(13)) is not None


def test_a_war_declared_rings_early() -> None:
    s, _ = scheduler(every=10)
    s.observe(view(5, base_count=2, factions_at_war=0))
    review = s.observe(view(6, base_count=2, factions_at_war=1))
    assert review is not None and review.reason == "war_declared"


def test_peace_and_growth_are_not_discontinuities() -> None:
    s, bell = scheduler(every=10)
    s.observe(view(1, base_count=2, factions_at_war=2))
    assert s.observe(view(2, base_count=3, factions_at_war=1)) is None
    assert s.observe(view(3)) is None  # metrics absent: no comparison, no trigger
    _join(s)
    assert bell.reviews == []


def test_the_review_sentence_carries_no_game_data() -> None:
    sentence = review_sentence("review-s3-1")
    assert sentence.endswith("[review-s3-1]")
    s, bell = scheduler(every=1)
    s.observe(view(1))
    review = s.observe(view(2, base_count=1))
    assert review is not None
    assert doorbell_mod._SAFE_ID.match(review.id)
    assert "base_count" not in review.id and "base.production" not in review.id


def test_the_turn_path_never_waits_for_the_ring() -> None:
    """A ring that hangs (a dead inbox, the full timeout) must not hold observe()."""
    release = threading.Event()

    class HangingBell(Doorbell):
        def __init__(self) -> None:
            super().__init__(command=CMD)

        def ring_review(self, review_id: str) -> bool:
            release.wait(10)
            return False

    s = ReviewScheduler({3: Seat(3, "three", "agent", strategist="agent")}, HangingBell(), every=1)
    s.observe(view(1))
    start = time.monotonic()
    assert s.observe(view(2)) is not None
    assert time.monotonic() - start < 0.5
    assert s.threads[0].is_alive() and s.threads[0].daemon
    release.set()
    _join(s)


# ---------------------------------------------------------------- 1c: the missed review


def test_a_missed_review_is_logged_on_the_seats_log(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    log.split_by_seat(tmp_path)
    s, _ = scheduler(every=5, log=log)
    for t in range(1, 12):
        s.observe(view(t))
    _join(s)
    # Rung at 6, unanswered by 11 (when the next is due): one miss, and a fresh review opens.
    assert [m["review_id"] for m in s.missed] == ["review-s3-t6"]
    lines = [
        json.loads(x) for x in (tmp_path / "seat-3" / "decisions.jsonl").read_text().splitlines()
    ]
    assert lines == [s.missed[0]]
    assert lines[0]["event"] == "review_missed"
    assert lines[0]["rung_turn"] == 6 and lines[0]["due_turn"] == 11
    # Never on the merged log: its readers parse every line as a decision record.
    assert not (tmp_path / "decisions.jsonl").exists()
    assert not (tmp_path / "seat-4").exists()


@pytest.mark.parametrize("kind", ["directive", "plan", "policy"])
def test_any_write_before_the_next_review_answers_it(kind: str) -> None:
    s, _ = scheduler(every=5)
    for t in range(1, 8):
        s.observe(view(t))
    s.note_write(3, kind)  # type: ignore[arg-type]
    for t in range(8, 12):
        s.observe(view(t))
    assert s.missed == []


def test_a_write_for_another_seat_does_not_answer_it() -> None:
    s, _ = scheduler(every=5)
    for t in range(1, 8):
        s.observe(view(t))
    s.note_write(4, "policy")
    s.note_write(None, "policy")
    for t in range(8, 12):
        s.observe(view(t))
    assert len(s.missed) == 1


# ---------------------------------------------------------------- service wiring


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
        {3: Seat(3, "three", "agent", strategist="agent")},
        build=lambda seat: AgentBrain(
            queue=queue, doorbell=Doorbell(target="", enabled=False), timeout=0.01
        ),
    )
    log = DecisionLog(tmp_path / "decisions.jsonl")
    app = create_app(brain=router, log=log)
    bell = RecordingBell()
    app.state.reviews.doorbell = bell
    return app, TestClient(app), app.state.seat_tokens.tokens[3], bell, tmp_path


def _decide(client: TestClient, turn: int) -> None:
    body = view(turn).model_dump(mode="json", exclude_none=True)
    assert client.post("/decide", json=body).status_code == 200


def test_a_missed_review_leaves_the_standing_policy_unchanged(seated) -> None:
    app, client, token, bell, run_dir = seated
    headers = {SEAT_HEADER: token}
    set_ = client.post(
        "/agent/policy",
        headers=headers,
        json={"knob": "focus.growth", "value": 1, "reason": "standing posture"},
    )
    assert set_.status_code == 200, set_.text
    before = client.get("/agent/policy", headers=headers).json()["knobs"]

    for t in range(1, 8):  # rings at 4, missed at 7
        _decide(client, t)
    reviews = app.state.reviews
    for t in reviews.threads:
        t.join(timeout=5)
    assert bell.reviews and reviews.missed
    assert client.get("/agent/policy", headers=headers).json()["knobs"] == before
    assert any(
        json.loads(line).get("event") == "review_missed"
        for line in (run_dir / "seat-3" / "decisions.jsonl").read_text().splitlines()
    )
    assert client.get("/health").json()["reviews"]["seats"]["3"]["missed"] == 1


def test_a_policy_write_through_the_route_answers_the_review(seated) -> None:
    app, client, token, _, _ = seated
    headers = {SEAT_HEADER: token}
    for t in range(1, 5):  # rings at 4
        _decide(client, t)
    assert app.state.reviews.status()["seats"]["3"]["open"]
    r = client.post(
        "/agent/policy",
        headers=headers,
        json={"knob": "focus.growth", "value": None, "reason": "review answered"},
    )
    assert r.status_code == 200, r.text
    for t in range(5, 8):
        _decide(client, t)
    assert app.state.reviews.missed == []
