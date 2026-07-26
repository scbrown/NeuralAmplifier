# adapters/glsmac

The **long-term** engine adapter: a GLSMAC `.gls.js` mod plus a small GSE `http` builtin, so
the open-source engine can speak the [contract](../../docs/contract.md).

**License boundary:** the `.gls.js` mod is **MIT**. The GSE `http` builtin modifies
[GLSMAC](https://github.com/afwbkbc/glsmac) and is therefore **AGPL-3.0** — keep it minimal and
contribute it upstream. Don't copy GLSMAC source into MIT-licensed parts of the repo.

## Two pieces

- **`mod/` — the `.gls.js` agent mod (MIT).** Hooks `turn` / `unit_turn` / `base_turn`,
  snapshots `um`/`bm`/`tm`/`fm` into a contract world view, calls out via the `http` builtin
  (async, so the loop never blocks), and applies orders as registered **GSE events**
  (`validate`/`apply`/`rollback` — the engine's own legality + undo).
- **`builtin/` — the GSE `http` builtin (AGPL).** The one native addition that lets scripts
  reach the orchestrator. Exact seam, threading, and skeleton are in the integration notes.

## Reality check

GLSMAC is early: units/bases/tiles/turns exist, but production, tech, diplomacy, and
**fog-of-war do not** — the world view is thin and full-ground-truth (`fog: false`) until we
build those systems. Mutation is **event-gated**, so the action space is a library of
registered GSE events.

## Develop

```bash
just glsmac build     # build the http builtin into a GLSMAC checkout (needs GLSMAC_DIR)
just glsmac test      # run the mod's logic headless via GLSMAC's --gse-tests path
just glsmac lint      # prettier on .gls.js
```

See **[../../docs/glsmac-integration-notes.md](../../docs/glsmac-integration-notes.md)** for
the source-grounded binding inventory, the `http` builtin patch plan, the headless path, and
the prioritized fork backlog.

> Scaffolded in roadmap Track B (see [../../VISION.md](../../VISION.md)).
