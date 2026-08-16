#!/usr/bin/env python3
"""Is this machine ready for an agent to play a game? — `just play check`.

Every one of these was a real false start. The orchestrator running with the *wrong brain* is
the worst of them, because it looks completely healthy: decisions are answered, the log fills
up, the game plays on, and the agent polls an empty queue forever because the agent endpoints
were never mounted. `llm_timeout_ms` is the second, and it is worse than looking healthy — it
looks like *success*, because before the deadline coupling landed a late answer was accepted and
recorded `tier: llm, degraded: false` for a turn the engine had already decided (na-t3h).

So this reports on the things that are silently wrong rather than loudly broken, and it
separates **blocking** from **optional**. Grounding and the guard are genuine improvements to a
game and neither is required to play one; saying so is the difference between a checklist
somebody follows and one they learn to ignore.

    just play check

Exit status is 1 if anything BLOCKING is wrong, so it can gate a script. An optional component
being absent is reported and never fails the run — that is a choice about this game, not a fault.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: Loopback only. An ambient HTTPS_PROXY would otherwise send these through a proxy that cannot
#: route them, and the failure reads as "the orchestrator is down".
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.blocked = False

    def add(self, status: str, blocking: bool, what: str, detail: str) -> None:
        self.rows.append((status, "blocking" if blocking else "optional", what, detail))
        if status == FAIL and blocking:
            self.blocked = True

    def print(self) -> None:
        width = max((len(r[2]) for r in self.rows), default=0)
        for status, kind, what, detail in self.rows:
            print(f"{_MARK[status]} {what:<{width}}  {kind:<8}  {detail}")


def get_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with _OPENER.open(url, timeout=timeout) as response:
            payload = json.loads(response.read())
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def post_json(url: str, body: dict, timeout: float = 2.0) -> dict | None:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def check_orchestrator(report: Report, url: str) -> None:
    """Up, and running the brain that leaves decisions for an agent to answer."""
    health = get_json(f"{url}/health")
    if health is None:
        report.add(
            FAIL,
            True,
            "orchestrator",
            f"not reachable at {url} — start it with `just play`",
        )
        return

    brain = str(health.get("brain", "?"))
    if brain != "agent":
        # The failure this check exists for: entirely healthy, and you will never see a decision.
        report.add(
            FAIL,
            True,
            "orchestrator",
            f"up, but brain is {brain!r} — decisions are answered without you and the agent "
            "endpoints are not mounted. Restart with NA_BRAIN=agent (`just play`)",
        )
        return

    report.add(OK, True, "orchestrator", f"up at {url}, brain=agent, game {health.get('game_id')}")

    telemetry = health.get("telemetry")
    if isinstance(telemetry, dict) and not telemetry.get("healthy", True):
        report.add(
            WARN,
            False,
            "telemetry",
            f"sink failures: {telemetry.get('failures')} — the game plays, the evidence is partial",
        )


def check_agent_queue(report: Report, url: str) -> None:
    """The endpoints an agent actually uses. Mounted only for the agent brain, so this is
    also the proof that the check above was not merely reading a cached health line."""
    waiting = post_json(f"{url}/agent/waiting", {})
    if waiting is None:
        report.add(FAIL, True, "agent queue", "/agent/waiting did not answer")
        return
    open_now = waiting.get("waiting") or []
    report.add(
        OK,
        True,
        "agent queue",
        f"reachable — {len(open_now)} decision(s) waiting"
        + (" (answer them before starting a new game)" if open_now else ""),
    )


def check_mcp_config(report: Report, root: Path) -> None:
    """A checked-in `.mcp.json` is what makes this repo attachable with no setup step."""
    path = root / ".mcp.json"
    if not path.is_file():
        report.add(WARN, False, "mcp config", "no .mcp.json — attach manually with `claude mcp add`")
        return
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    except (json.JSONDecodeError, OSError) as exc:
        report.add(FAIL, True, "mcp config", f".mcp.json is unreadable: {exc}")
        return
    if "neural-amplifier" not in servers:
        report.add(WARN, False, "mcp config", ".mcp.json has no neural-amplifier server")
        return
    report.add(
        OK, False, "mcp config", "neural-amplifier in .mcp.json — Claude Code finds it on open"
    )


def check_timeout(report: Report, play_dir: str | None) -> None:
    """`llm_timeout_ms` — the setting that makes an agent-driven game possible at all.

    The default is 2500 ms, which no agent meets. Below the deadline coupling this produced
    the worst possible outcome: the engine applied its own answer, the agent's late answer was
    accepted anyway, and the record claimed `tier: llm, degraded: false` for a turn the brain
    had no part in. It is refused with a 409 now, which is correct and still means every
    decision you are handed has already been decided.
    """
    if not play_dir:
        report.add(
            WARN,
            False,
            "llm_timeout_ms",
            "SMAC_PLAY_DIR unset — cannot check thinker.ini. Default is 2500ms, too tight to play",
        )
        return

    ini = Path(play_dir) / "thinker.ini"
    if not ini.is_file():
        report.add(WARN, False, "llm_timeout_ms", f"no thinker.ini at {ini}")
        return

    setting: str | None = None
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("llm_timeout_ms"):
            _, _, value = stripped.partition("=")
            setting = value.strip()
    if setting is None:
        report.add(
            FAIL,
            True,
            "llm_timeout_ms",
            f"absent from {ini} — you get the 2500ms default and every decision is "
            "already answered by the time you read it. Set it to 0 to wait indefinitely",
        )
        return
    try:
        value = int(setting)
    except ValueError:
        report.add(FAIL, True, "llm_timeout_ms", f"not a number: {setting!r}")
        return
    if value == 0:
        report.add(OK, True, "llm_timeout_ms", "0 — the engine waits indefinitely for you")
    elif value < 30_000:
        report.add(
            FAIL,
            True,
            "llm_timeout_ms",
            f"{value}ms is too tight for an agent — raise it, or set 0 to wait indefinitely",
        )
    else:
        report.add(OK, True, "llm_timeout_ms", f"{value}ms")


def check_quipu(report: Report, url: str | None) -> None:
    """Grounding. A game plays without it and the brain argues from the prompt alone."""
    if not url:
        report.add(
            WARN, False, "quipu (grounding)", "NA_QUIPU_URL unset — decisions run ungrounded"
        )
        return
    answer = post_json(f"{url}/query", {"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"})
    if answer is None:
        report.add(WARN, False, "quipu (grounding)", f"not reachable at {url}")
        return
    if "error" in answer:
        report.add(WARN, False, "quipu (grounding)", f"rejected a trivial query: {answer['error']}")
        return
    report.add(OK, False, "quipu (grounding)", f"answering at {url}")


def check_yupana(report: Report, url: str | None) -> None:
    """The board guard. Subtracts from what is already legal; never required to play."""
    if not url:
        report.add(
            WARN, False, "yupana (guard)", "NA_YUPANA_URL unset — no board policies are enforced"
        )
        return
    # MCP streamable-HTTP answers a bare GET on the endpoint; anything that answers at all is
    # enough for a preflight. Whether the `game-state` tools are built is a question only a
    # real call can settle, and that belongs in the guard, not here.
    try:
        with _OPENER.open(url.rstrip("/") + "/mcp", timeout=2.0) as response:
            reachable = response.status < 500
    except urllib.error.HTTPError as exc:
        reachable = exc.code < 500
    except (urllib.error.URLError, OSError, TimeoutError):
        reachable = False
    if not reachable:
        report.add(WARN, False, "yupana (guard)", f"not reachable at {url}")
        return
    report.add(OK, False, "yupana (guard)", f"answering at {url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("NA_URL", "http://127.0.0.1:8000"))
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    url = args.url.rstrip("/")

    report = Report()
    check_orchestrator(report, url)
    if not report.blocked:
        check_agent_queue(report, url)
    check_mcp_config(report, root)
    check_timeout(report, os.environ.get("SMAC_PLAY_DIR"))
    check_quipu(report, os.environ.get("NA_QUIPU_URL"))
    check_yupana(report, os.environ.get("NA_YUPANA_URL"))

    report.print()

    if report.blocked:
        print("\nnot ready — fix the blocking rows above")
        return 1
    print("\nready to play. Attach Claude Code and call next_decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
