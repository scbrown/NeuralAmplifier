# Neural Amplifier: unreleased

Changes on `main` since v0.3.3. The next release's prepare PR renames this file
to `release-notes/v<version>.md`, adds its version heading, and checks every line
below against the code it ships.

## Changes

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

## Not tested for this release

- Seat isolation is enforced at the orchestrator's API only, and that is what
  the tests cover: one seat's token cannot claim, answer, forecast or order for
  another seat. It is not isolation between processes. Every seat's MCP server
  runs as the same OS user as the orchestrator, so a seat with a shell can read
  the other seats' token files and the run directory. Process and store
  isolation are later work.
- No game was played with two or more seats, and no model calls were made.
