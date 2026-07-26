# engine

The one native addition: a **GSE HTTP builtin** for GLSMAC, so scripts can reach an external
process. GLSMAC's scripting engine ships no network IO by default; this adds it.

> ⚠️ **License: AGPL-3.0.** Code here modifies [GLSMAC](https://github.com/afwbkbc/glsmac),
> which is AGPL-3.0. These changes inherit that license and are meant to be **contributed
> upstream**. Keep this surface minimal and isolated; do not copy GLSMAC source into the
> MIT-licensed parts of the repo (`orchestrator/`, `mod/`, `docs/`).

## What it is

A small builtin (in the family of GSE's existing `Async`, `Console`, `Math`, …) exposing an
outbound HTTP call to `.gls.js` scripts. Invoked via `Async` so a slow request never freezes
the render loop. That's the entire native footprint — all behavior lives in the
[`mod`](../mod/).

## Develop

Requires a local GLSMAC checkout; point `just` at it with `GLSMAC_DIR=/path/to/glsmac`.

```bash
just engine apply    # graft the builtin into the GLSMAC source tree
just engine build    # cmake --build the GLSMAC target
just engine test     # smoke test: builtin round-trips against a local echo server
```

See [../docs/building-and-testing.md](../docs/building-and-testing.md) for the build deps and
the headless (Xvfb) story.

> Scaffolded in roadmap Phase 0 (see [../VISION.md](../VISION.md)).
