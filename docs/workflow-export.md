# Workflow Export

A finished game's decision log, exported as a **shuttle workflow run** —
so a game lives in [quipu](https://github.com/scbrown/quipu)'s windowed
operational graphs beside every other crew's runs, freezable when its
window completes. Shuttle is the workflow engine of the quipu stack
([scbrown/shuttle](https://github.com/scbrown/shuttle)); this page is the
producer seam on our side.

## What it does

```bash
neural-amplifier export-run runs/game-1/decisions.jsonl --agent importer-a
neural-amplifier export-run runs/game-1/decisions.jsonl --agent importer-a --dry-run
```

`export-run` maps the log (the record of truth for a run,
`decisions.DecisionLog`) onto shuttle's model: one `na-game` workflow
definition (`playing` → `finished`), a run named `na-<game_id>`, one
`decide` self-loop transition per **played turn** (not per decision), and a
terminal `finish`. The mapping is written as a transitions JSONL and handed
to `shuttle import-run`, which re-validates it against the state machine
and signs every transition with the **importing agent's** key.

## The invariant (module map: `workflow_export.py`)

**A finished game is exportable as a workflow run.** Deliberately off the
game path — a CLI over a finished log — so invariant #9 (the game never
stalls) holds structurally. The module writes no quipu itself: shuttle owns
that seam, including the store capability probe and the window discipline.

## Two honesty notes

- The decision log carries no wall clock (turns are game time), so the
  transition timestamps record the **export moment**, not the play. They
  attest when the run entered shuttle.
- The importer's signature attests the **mapping**, not the original play:
  the engine never signed shuttle messages, and pretending otherwise would
  forge exactly what the signature exists to prove.

## Config

`[shuttle] bin` in `na.toml`, overridden by `NA_SHUTTLE_BIN` (env > file >
default, as everywhere), default `shuttle` on `PATH`.
