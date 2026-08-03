"""Issuing orders: the client half of the adapter's command channel.

The interesting tests are the ones about NOT knowing. A channel that reports an unobserved order
as applied is the failure this project keeps meeting from new angles, and the file-based channel
makes it easy to write by accident: the adapter deletes the command before acting, so "the file
is gone" is available and almost convincing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neural_amplifier.orders import OrderChannel, build_command
from neural_amplifier.service import create_app


def fake_adapter(game_dir: Path, ok: bool = True, detail: str = "done", delay: float = 0.0):
    """Stand in for na_command_tick: consume the command, then write a result.

    Consumes BEFORE writing the result, exactly as the adapter does — that ordering is what makes
    the "consumed but unanswered" case reachable at all.
    """

    def run() -> None:
        cmd = game_dir / "na-command"
        for _ in range(400):
            if cmd.exists():
                line = cmd.read_text(encoding="utf-8").strip()
                cmd.unlink()
                if delay:
                    time.sleep(delay)
                (game_dir / "na-command-result").write_text(
                    json.dumps(
                        {
                            "command": line.split()[0] if line else "",
                            "detail": detail,
                            "ok": ok,
                            "turn": 42,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# --- not knowing --------------------------------------------------------------


def test_no_result_is_unknown_never_ok(tmp_path: Path) -> None:
    """Nothing answers. The order may or may not have happened, and that is the report."""
    channel = OrderChannel(tmp_path)
    result = channel.issue("skip 3", timeout_s=0.2)
    assert result.status == "unknown"
    assert "never read" in result.detail


def test_consumed_but_unanswered_is_still_unknown(tmp_path: Path) -> None:
    """The adapter removes the command BEFORE acting, so that a crash cannot replay it.

    Which means "the file is gone" is evidence the order was READ, never that it was carried out.
    Treating consumption as success would turn a crash into a reported order.
    """
    channel = OrderChannel(tmp_path)

    def eat() -> None:
        cmd = tmp_path / "na-command"
        for _ in range(200):
            if cmd.exists():
                cmd.unlink()
                return
            time.sleep(0.01)

    threading.Thread(target=eat, daemon=True).start()
    result = channel.issue("skip 3", timeout_s=0.5)
    assert result.status == "unknown"
    assert "consumed" in result.detail


def test_a_stale_result_is_not_read_as_this_orders_answer(tmp_path: Path) -> None:
    """The result file persists between orders. Without clearing it first, the previous order's
    success would be returned instantly and attributed to this one — a confident wrong answer,
    which is worse than no answer."""
    (tmp_path / "na-command-result").write_text(
        json.dumps({"command": "move", "detail": "an OLD order", "ok": True}), encoding="utf-8"
    )
    channel = OrderChannel(tmp_path)
    result = channel.issue("skip 3", timeout_s=0.2)
    assert result.status == "unknown"
    assert "an OLD order" not in result.detail


# --- the round trip -----------------------------------------------------------


def test_a_completed_order_reports_ok(tmp_path: Path) -> None:
    fake_adapter(tmp_path, ok=True, detail="veh 3 ends its turn")
    result = OrderChannel(tmp_path).issue("skip 3", timeout_s=3.0)
    assert result.status == "ok"
    assert result.detail == "veh 3 ends its turn"
    assert result.turn == 42


def test_a_refusal_is_reported_with_its_reason(tmp_path: Path) -> None:
    """A refusal is a real answer, and distinct from not knowing: the adapter ran the order and
    declined it, so the agent should act on the reason rather than retrying blindly."""
    fake_adapter(tmp_path, ok=False, detail="not faction 1's turn (current is 2)")
    result = OrderChannel(tmp_path).issue("move 3 10 10", timeout_s=3.0)
    assert result.status == "refused"
    assert "not faction 1's turn" in result.detail


def test_orders_are_serialised(tmp_path: Path) -> None:
    """One command file, one result slot. Concurrency here does not go slowly wrong, it goes
    silently wrong — two orders in flight and a result belongs to whichever finished last."""
    seen: list[str] = []

    def adapter() -> None:
        cmd = tmp_path / "na-command"
        for _ in range(600):
            if cmd.exists():
                line = cmd.read_text(encoding="utf-8").strip()
                cmd.unlink()
                seen.append(line)
                (tmp_path / "na-command-result").write_text(
                    json.dumps({"command": line.split()[0], "detail": line, "ok": True}),
                    encoding="utf-8",
                )
            time.sleep(0.005)

    threading.Thread(target=adapter, daemon=True).start()
    channel = OrderChannel(tmp_path)
    results: list[str] = []

    def issue(n: int) -> None:
        results.append(channel.issue(f"skip {n}", timeout_s=3.0).detail)

    threads = [threading.Thread(target=issue, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 4, "every order reached the adapter"
    assert sorted(results) == sorted(seen), "each caller got its OWN order's result"


# --- shape checking, not rule checking ----------------------------------------


def test_build_command_shapes() -> None:
    assert build_command("move", [12, 40, 21]) == "move 12 40 21"
    assert build_command("skip", [3]) == "skip 3"
    assert build_command("build", [0, -4]) == "build 0 -4"


def test_wrong_arity_is_refused_here() -> None:
    with pytest.raises(ValueError, match="takes 3"):
        build_command("move", [1, 2])
    with pytest.raises(ValueError, match="unknown order verb"):
        build_command("teleport", [1])


def test_legality_is_NOT_checked_here(tmp_path: Path) -> None:
    """A nonsense tile still goes to the adapter.

    Deliberate: whether a unit can reach a tile is the engine's question, and a second opinion
    here would drift from the engine's answer silently. We check arity; the engine checks rules.
    """
    fake_adapter(tmp_path, ok=False, detail="tile (9999,9999) is off the map")
    result = OrderChannel(tmp_path).issue("move 3 9999 9999", timeout_s=3.0)
    assert result.status == "refused"


# --- over HTTP ----------------------------------------------------------------


def test_order_endpoint_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app())
    fake_adapter(tmp_path, ok=True, detail="veh 12 -> (40,21) rc=1")
    got = client.post("/order", json={"verb": "move", "args": [12, 40, 21]}).json()
    assert got["status"] == "ok"
    assert got["detail"] == "veh 12 -> (40,21) rc=1"


def test_unconfigured_is_unavailable_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """No game directory is a legitimate way to run — ordering is an addition to a run, not a
    precondition for one. It must not stop the service from starting."""
    monkeypatch.delenv("NA_GAME_DIR", raising=False)
    client = TestClient(create_app())
    status = client.get("/order").json()
    assert status["available"] is False
    assert "no game directory" in status["reason"]
    issued = client.post("/order", json={"verb": "skip", "args": [1]}).json()
    assert issued["status"] == "unavailable"


def test_bad_arity_is_422_addressed_to_the_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app())
    resp = client.post("/order", json={"verb": "move", "args": [1]})
    assert resp.status_code == 422
    assert "move <veh_id> <x> <y>" in resp.json()["detail"]


# --- batching -----------------------------------------------------------------


def batch_adapter(game_dir: Path, results: list[dict[str, object]], dropped: int = 0):
    """Stand in for na_order_batch: one envelope carrying every order's outcome."""

    def run() -> None:
        cmd = game_dir / "na-command"
        for _ in range(400):
            if cmd.exists():
                cmd.read_text(encoding="utf-8")
                cmd.unlink()
                (game_dir / "na-command-result").write_text(
                    json.dumps(
                        {
                            "command": "batch",
                            "results": results,
                            "count": len(results),
                            "dropped": dropped,
                            "ok": all(r.get("ok") for r in results) and dropped == 0,
                            "turn": 42,
                            "halted": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    threading.Thread(target=run, daemon=True).start()


def test_a_batch_goes_as_one_round_trip(tmp_path: Path) -> None:
    seen: list[str] = []

    def adapter() -> None:
        cmd = tmp_path / "na-command"
        for _ in range(400):
            if cmd.exists():
                seen.append(cmd.read_text(encoding="utf-8"))
                cmd.unlink()
                (tmp_path / "na-command-result").write_text(
                    json.dumps({"command": "batch", "results": [{"ok": True}], "ok": True}),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    threading.Thread(target=adapter, daemon=True).start()
    OrderChannel(tmp_path).issue_batch(["move 1 2 3", "skip 4"], timeout_s=3.0)
    assert len(seen) == 1, "one file, not one per order"
    assert seen[0].strip().splitlines() == ["move 1 2 3", "skip 4"]


def test_a_half_working_batch_is_not_ok(tmp_path: Path) -> None:
    """Flattening a partial failure into one boolean is how it goes unseen."""
    batch_adapter(
        tmp_path,
        [
            {"command": "move", "detail": "veh 1 -> (2,3) rc=1", "ok": True},
            {"command": "move", "detail": "faction 1 has not explored tile (9,9)", "ok": False},
        ],
    )
    result = OrderChannel(tmp_path).issue_batch(["move 1 2 3", "move 2 9 9"], timeout_s=3.0)
    assert result.status == "refused"
    assert len(result.results) == 2
    assert result.detail == "1/2 orders succeeded"


def test_dropped_orders_are_surfaced(tmp_path: Path) -> None:
    """Orders past the adapter's per-tick cap were NOT executed, and must not read as if they
    were merely unremarkable."""
    batch_adapter(tmp_path, [{"command": "skip", "ok": True}], dropped=3)
    result = OrderChannel(tmp_path).issue_batch(["skip 1"], timeout_s=3.0)
    assert result.dropped == 3
    assert result.status == "refused", "a batch that dropped orders is not a success"


def test_empty_batch_is_refused_without_touching_the_channel(tmp_path: Path) -> None:
    result = OrderChannel(tmp_path).issue_batch([], timeout_s=0.2)
    assert result.status == "refused"
    assert not (tmp_path / "na-command").exists()


def test_batch_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_GAME_DIR", str(tmp_path))
    client = TestClient(create_app())
    batch_adapter(tmp_path, [{"command": "move", "ok": True}, {"command": "skip", "ok": True}])
    got = client.post(
        "/order",
        json={"orders": [{"verb": "move", "args": [1, 2, 3]}, {"verb": "skip", "args": [4]}]},
    ).json()
    assert got["status"] == "ok"
    assert len(got["results"]) == 2
