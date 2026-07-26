# adapters/thinker

The **near-term** engine adapter: a fork of / companion to
[Thinker](https://github.com/induktio/thinker) (MIT, C++) that bridges the original
`terranx.exe`'s AI decision hooks to the orchestrator via the [contract](../../docs/contract.md).

**License: MIT** (Thinker is MIT; preserve its notice on adapted code).

Gives Claude control of a faction in the **complete, balanced** original Alpha Centauri —
production, tech, SE, diplomacy, combat, and real fog-of-war all present from day one.

## Develop

Requires the Thinker fork and its Windows/MinGW (32-bit) toolchain; runs under Windows or
Wine. See **[../../docs/thinker-adapter-notes.md](../../docs/thinker-adapter-notes.md)** for
the build/patcher workflow, the AI hooks to intercept, and the (real) headless/testing
constraints.

```bash
just thinker build    # build the DLL (needs the Thinker toolchain)
just thinker test     # run against SMAC under Wine (integration)
```

> Scaffolded in roadmap Track A (see [../../VISION.md](../../VISION.md)).
