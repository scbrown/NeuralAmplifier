# Neural Amplifier: unreleased

Changes on `main` since v0.3.3. The next release's prepare PR renames this file
to `release-notes/v<version>.md`, adds its version heading, and checks every line
below against the code it ships.

## Changes

- **OpenSMAC eval adapter.** A third adapter, for eval and research only, beside
  Thinker: `adapters/opensmac/` carries patch files plus a build script for
  OpenSMAC at a pinned commit (no fork). Four decision hooks (`base_build`,
  `tech_selection`, `social_ai`, `base_hurry`) write one JSONL decision record
  per call in `live` mode and re-apply the logged choices in `replay` mode.
  Replay fails loudly on an `inputs_sha256` divergence. `replay_test.sh` checks
  that a 300-turn standard game replays sha256-identical, turn by turn. Thinker
  is unchanged.
- **Seat isolation at the API.** Under a `[seats]` table the orchestrator mints
  one token per seat at start and writes it to `<run>/seat-<n>/token` (mode
  0600). Start each seat's MCP server with `neural-amplifier mcp --seat-token
  <token>` (or `NA_SEAT_TOKEN`). The token travels as the `X-NA-Seat` header on
  every call, and the service derives the faction from it:
  - no agent-facing tool takes a `faction_id` any more (`turn_forecast`,
    `turn_plan_status`, `deferred_decisions` and `submit_turn_plan` lost theirs);
  - in seat mode a missing or unknown token is refused with 401;
  - a seat claims and answers only its own decisions, and a refusal does not
    name the seat that owns the decision;
  - `issue_order` and `issue_orders` refuse a unit or base id that is not in
    that seat's latest world view.

  Without `[seats]`, the faction is the one LLM faction that has posted a
  decision; if none or several have, the call is refused with 422 `configure
  [seats]`.
- **Per-seat evidence.** In seat mode, with a run directory (`NA_RUN_DIR`, or
  the `<run>/orch/` layout of the decision log), each seated decision is also
  appended to `<run>/seat-<n>/decisions.jsonl` and its world view is also stored
  in `<run>/seat-<n>/worldviews/`. The run-level decision log and world-view
  store are unchanged and remain the operator's merged view, so the dashboard
  and existing readers work as before. A decision the seat table cannot place
  (an unseated faction, or a world view without `faction_id`) goes to the
  merged view only. Without `[seats]`, no `seat-*` directories are created.
- **Live console, first slice.** The dashboard has a Server-Sent Events stream
  at `GET /dashboard/api/events` and a **Now** pane fed by it: what is waiting
  for an answer and for how long, which seat claimed it and how long it has been
  thinking, and a per-seat spend meter when decisions record `cost_usd` or a
  seat has `budget_usd`. Events are `raised`, `claimed` (with the seat),
  `answered`, `guard` (the policy guard's verdict), and `applied` or `diverged`
  from the engine's outcome reports. The stream is one more decision-log sink,
  written after the JSONL log, so it can lag the log but never contradict it. A
  slow viewer cannot hold up a decision: each viewer has a bounded queue that
  drops its oldest events and counts the drops (`/health` → `live.dropped`).
  - Filtering is done by the service. A seat token (`X-NA-Seat`, or `?token=`
    because a browser's EventSource cannot set headers) sees its own faction
    only. The spectator token, written to `<run>/spectator/token` (mode 0600)
    or set with `NA_SPECTATOR_TOKEN`, sees every faction. A missing or unknown
    token gets 401 and no stream.
  - The first spectator connection during a run writes
    `observer_spoiled: true` and `observer_spoiled_at` to `<run>/run.json`,
    keeping any fields already in that file. Later spectator connections
    leave it unchanged.
  - Open the page as `/dashboard#token=<token>` to fill the Now pane. Without a
    token, the rest of the dashboard works as before.

## Not tested for this release

- Seat isolation is enforced at the orchestrator's API only, and that is what
  the tests cover: one seat's token cannot claim, answer, forecast or order for
  another seat. It is not isolation between processes. Every seat's MCP server
  runs as the same OS user as the orchestrator, so a seat with a shell can read
  the other seats' token files and the run directory. Process and store
  isolation are later work.
- No game was played with two or more seats, and no model calls were made.
- The live console was tested with in-process services only. It was not opened
  in a browser against a running game, and the Policy, Copilot and Graph panes
  from design §7 do not exist yet.
