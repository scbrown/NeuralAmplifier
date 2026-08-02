---
name: play-alpha-centauri
description: Play a faction in Sid Meier's Alpha Centauri as the LLM tier of Neural Amplifier, or set yourself up to do so. Use when asked to play, drive, advise on, or take a turn in Alpha Centauri / SMAC / Neural Amplifier, when asked to answer game decisions, or when a nudge says a game decision is waiting. Covers attaching to the orchestrator over MCP, polling for open decisions, and how to choose.
---

# Playing Alpha Centauri

You are the **LLM tier** of Neural Amplifier: the part that sets policy and makes the
judgement calls a heuristic cannot. A deterministic tier inside the engine handles the
mechanical volume. You are consulted a few times a turn, not for every twitch.

The game blocks while you think. A decision point is the engine sitting and waiting, exactly
as it does for a human player — so there is no clock on your reasoning, and equally no
decision resolves until you answer it.

## 1. Set yourself up

Nothing launches you. The orchestrator serves decisions and never starts a terminal, a pane or
an agent — so attaching is your job, and it is three steps.

**Check the orchestrator is up.** It must be running with the agent brain:

```bash
curl -s http://127.0.0.1:8000/health
```

`"brain":"agent"` is what you need. If it says `scripted` or `claude`, decisions are being
answered without you and the agent endpoints are not even mounted — say so rather than
polling an empty queue forever. If it is not running at all, start it:

```bash
NA_BRAIN=agent NA_DECISION_LOG=decisions.jsonl \
  uv run --directory orchestrator neural-amplifier serve
```

**Attach the MCP server.** From the repository root:

```bash
claude mcp add neural-amplifier -- \
  uv run --directory orchestrator neural-amplifier mcp --url http://127.0.0.1:8000
```

You now have `next_decision`, `submit_orders`, `decisions_waiting` and `issue_directive`.

**Decide how you will be woken.** Two ways, and polling is the one that always works:

- **Poll.** `next_decision(wait_seconds=60)` blocks server-side until something arrives. Loop
  on it. This needs no configuration and cannot be misconfigured.
- **Be nudged.** If the orchestrator has `NA_TMUX_TARGET` set to a tmux pane you are running
  in, it types a one-line notice there when a decision opens. Convenience only — the nudge
  carries no game data and can be silently lost, so never treat its absence as "no decision
  is waiting". Ask.

If you want to run under tmux so a nudge can reach you, set that up yourself:

```bash
tmux new-session -d -s smac -n brain
# then start the orchestrator with NA_TMUX_TARGET=smac:brain
```

## 2. The loop

1. `next_decision(wait_seconds=60)` — collect the decision.
2. Read the world view. Decide.
3. `submit_orders(decision_id, action_id, reason)` — answer it.
4. Repeat.

On reconnecting, or after a compaction, call `decisions_waiting` before anything else. It
tells you what is outstanding so you do not double-answer or sit idle next to an open
decision.

## 3. Reading a world view

Everything you need is in the payload. Do not go looking for game state elsewhere — there is
no other source, and inventing context is how a defensible move becomes a wrong one.

- **`action_space` is the whole truth about legality.** Every legal move is in it; nothing
  outside it exists. An `action_id` you did not read there is refused, not applied.
- **`metrics`** are named measurements — `energy_reserves`, `mineral_surplus`,
  `turns_to_completion`. These are the names a directive can be written against.
- **`grounding`** is retrieved fact, id-first (`unit:formers Formers; terraforms terrain`).
  It is the game's actual rules, not your recollection of a 1999 game. Where it contradicts
  your memory, it wins.
- **`directives`** are standing plans with their current value and whether they are satisfied.
  `satisfied: null` means *unmeasurable*, which is not the same as failing.
- **`tradeoffs`** say what each option would cost each directive, in numbers —
  "hurrying costs 81 credits, leaving reserves at 1 against a plan wanting 300."
- **`history`** (on `base.production`) is what this base was told to build over the last few
  turns, **oldest first**, each tagged with the `tier` that decided it. `llm` is you; anything
  else is the deterministic tier, and `null` means the adapter could not attribute it. `item`
  is an action-space id, so you can match a past choice against an option on offer exactly.
  Read it before choosing — see below.
- **`fairness`** lists the rule asymmetries in force. If it is non-empty you are playing with
  advantages; reason about them out loud rather than quietly banking them.

## 4. Choosing

Weigh the standing plan against the immediate move rather than obeying either absolutely.
Directive priorities are anchored: 9–10 survival, 7–8 a committed plan, 4–6 a real
preference, 1–3 a tie-breaker. A priority-7 saving plan should beat finishing a Scout Patrol
two turns sooner and lose to averting a base falling.

Watch for these, all of which have actually happened:

- **Read the role, not the name.** A Colony Pod founds a *new* base; it does not grow this
  one. The `role` field on each unit says what it is for.
- **Costs are in minerals; `turns_if_switched` and `turns_if_continued` are different
  numbers.** Switching item category forfeits accumulated minerals, which is why the second
  one only appears on the item already in production.
- **Do not re-derive arithmetic that was given to you.** Turn counts are precomputed
  precisely because recomputing them went wrong.
- **Prefer continuity, and check `history` before you break it.** Production is
  re-evaluated from scratch every turn, so nothing stops you from switching — and switching
  item category forfeits the minerals already accumulated. Half-built Recycling Tanks abandoned
  for a Scout Patrol abandoned for Recycling Tanks is every individual choice being defensible
  and the sequence being useless.

  If `history` shows this base has been on the same item, the bar for changing it is what
  has changed on the board, not which option looks best in isolation today. If it shows you
  already switched last turn, switching again is the pattern to break.

  Do not rely on remembering this yourself. Your session compacts and reconnects, and a
  different harness may pick the game up entirely. The payload is the record; your recollection
  is not.

## 5. Say what you used, not just what you chose

`submit_orders` takes four things beyond the choice, and each one is a measurement channel.
Leave them empty and a run reports that you read the facts and the plan and ignored both —
which is indistinguishable from actually having done that.

- **`reason`** — the only part of your thinking that survives. Say what you weighed and what
  you gave up: "infrastructure first: reserves are thin and Recycling Tanks pays back before a
  second base would", not "building Recycling Tanks."
- **`cited`** — ids from the world view's `grounding` block that actually informed the choice.
  This is the only evidence retrieval *mattered* rather than merely happened. Cite what you
  used and nothing else; ids you were not offered are discarded, so padding gains nothing.
- **`followed`** / **`overrode`** — ids from `directives` you obeyed or deliberately went
  against. Overriding a standing plan is legitimate — priorities exist so a decision can
  outrank one — but say so. A plan quietly ignored and a plan consciously outweighed look
  identical afterwards.

## 5a. Leave something behind on a long-horizon decision

On `faction.tech` and `faction.se` you are reasoning over many turns, and without a directive
that reasoning dies with the response — the next production decision knows nothing about it.
Call `issue_directive` **before** `submit_orders` to set a standing plan later decisions will
be shown.

```text
issue_directive(
  decision_id, id="fund-weather-paradigm",
  intent="save energy for the Weather Paradigm",
  metric="energy_reserves", comparator="at_least", target=300, priority=7,
  entities=["fac:the-weather-paradigm"])
```

The constraint is the feature. `metric` must name something the world view actually reports,
so "keep reserves above 300" is checkable and "play aggressively" is refused — while you are
still holding it and can rewrite it. `entities` are lookup keys, not decoration: a later
decision that spends the resource you are saving finds this plan by walking out from them.

Do not issue one on every decision. A plan per turn is noise; a plan that shapes twenty turns
is the point.

## 6. Read what `submit_orders` returns

It reports what **actually ran**, which is not always what you asked for. Validation and the
policy guard sit between your order and the game.

- `"status": "applied to the game"` — your choice ran.
- `"status": "NOT applied — the guard replaced it with ..."` — your choice was legal by the
  action space but failed a policy check. `advisories` says why. The commonest cause is an
  order the current state cannot pay for: the option was offered when reserves were 82 and by
  the time you answered they were 40.

A denial is information, not an insult — and it is usually recoverable. If the status says a
**repair decision follows**, call `next_decision` again: you get the same surface back with the
reason in its `advisories`, and one more chance to choose. Take it seriously, because there is
only one: insist on the same refused id and the turn falls back to the deterministic tier.

Never re-submit the id that was just refused. The state that refused it has not changed.

Watch for the belief that caused it. You keep a session across the whole game, so you can
remember a base as it was twenty turns ago. The world view in front of you is the truth; your
memory of the board is not. Where they disagree, the payload wins.

## 7. When something is wrong

- **`submit_orders` refused with the legal set** — you named an action that was not offered.
  Re-read `action_space` and choose from it. Do not retry the same id.
- **"already answered"** — you submitted twice. Call `decisions_waiting` to resync.
- **"was abandoned"** — the game stopped waiting and played the deterministic tier's choice.
  The turn has moved on; collect the next decision rather than arguing with this one.
- **"orchestrator unreachable"** — the service is down. Report it. Do not keep polling.

Never invent an `action_id` to get unstuck. The engine's list is the only legal set, and a
refusal is a cheaper outcome than a wrong move.
