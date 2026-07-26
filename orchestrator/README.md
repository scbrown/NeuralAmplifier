# orchestrator

The LLM brain, as an external Python service (Claude Agent SDK). **License: MIT.**

It receives a per-turn **world view** (JSON) from the in-game [`mod`](../mod/), builds a
prompt, calls Claude, and returns **structured, validated moves** drawn from the turn's legal
`action_space` — plus the reasoning behind them, for the log.

## Responsibilities

- Prompt assembly (static briefing + per-turn world view + memory).
- Tool-use loop, retries, streaming, and API-key/secrets handling.
- Move validation against the versioned world-view schema (never emit an illegal action).
- Turn-to-turn strategic memory.
- Safe degradation (`end_turn`) on timeout, error, or budget exhaustion.

## Develop

```bash
just orchestrator build    # uv sync
just orchestrator test     # pytest (uses a fake Claude by default — free & deterministic)
just orchestrator lint     # ruff + mypy
just orchestrator run      # start the service
```

Most of the project's tests live here because they need no running game — see
[../docs/building-and-testing.md](../docs/building-and-testing.md).

> Scaffolded in roadmap Phase 1 (see [../VISION.md](../VISION.md)).
