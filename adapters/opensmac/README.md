# adapters/opensmac

A third engine adapter, for **evaluation and research only**, beside the Thinker adapter.
It does not replace Thinker. [OpenSMAC](https://github.com/richardpenman/smac) (MIT) is a native
reconstruction of SMAC/SMACX that is verified field by field against the original `terranx.exe`.
Its headless runner plays a 300-turn standard game in about 25 s, so batch evals that take hours
under Wine take seconds here.

This directory holds **patch files, not a fork**. `build.sh` checks out OpenSMAC at the commit in
[`SMAC_PIN`](SMAC_PIN), applies [`patches/*.patch`](patches/) and builds `opensmac_headless`.
Nothing is pushed upstream. The game's data and `terranx.exe` never enter this repository. You
point the scripts at your own install.

## Decision hooks

`patches/0001-na-decision-hooks.patch` adds `src/engine/na_decide.{h,cpp}` and one call at
each of four AI decision seams:

| Surface | Seam (pinned commit) | Key | Choice |
| --- | --- | --- | --- |
| `base_build` | `base_build`, `base_misc.cpp` | base id | production item (unit id, or `-facility id`) |
| `tech_selection` | `tech_selection`, `tech.cpp` | `-1` | research target tech id |
| `social_ai` | `social_ai`, `social.cpp` (before `*out` / pending SE is written) | `-1` | SE choice, 4 categories x 2 bits |
| `base_hurry` | `base_hurry`, `base.cpp`, after the spend is computed and before it is applied | base id | energy credits to spend |

The hooks are inert unless `NA_OPENSMAC_MODE` is set. Unpatched behaviour is unchanged.

| `NA_OPENSMAC_MODE` | Behaviour |
| --- | --- |
| unset / `off` | No-op. The engine's own choice is applied. |
| `live` | The decider picks a choice (the built-in stub returns the engine's own choice). The choice goes through a legality check (an illegal choice falls back to the engine's own, `source: fallback`). One JSONL record is appended to `NA_OPENSMAC_LOG` and the choice is applied. |
| `replay` | Reads `NA_OPENSMAC_LOG`, finds the record for the same `(turn, faction, surface, key, seq)` and recomputes `inputs_sha256`. A missing record or a hash mismatch **exits 3** with `NA-REPLAY DIVERGENCE at turn N`. Otherwise the logged choice goes through the same legality check and is applied. No decider is called. |

One record per decision:

```json
{"turn":161,"faction":6,"surface":"base_build","key":1,"seq":4,"inputs_sha256":"e6eb…","choice":-3,"source":"stub"}
```

`seq` counts calls per `(turn, faction, surface, key)`. `inputs_sha256` is the SHA-256 of the
canonical inputs the decider sees: surface, turn, faction, key, a small observation, the legal
option list and the engine's own choice. The engine is byte-deterministic per seed, and the
decision is its only outside input. Replay therefore reproduces every RNG draw, and the replayed
game is sha256-identical to the live one.

A model bridge replaces the stub through `na::set_decider()`. Bridging that call to the
orchestrator's [contract](../../docs/contract.md) is the next step and is not built yet.

## Run

The third-party code is built and run only inside [`sandbox.sh`](sandbox.sh): bubblewrap,
no network, tmpfs `HOME` and `/tmp`, the host read-only, only the work dir writable, and the
game dir mounted read-only. The clone in `build.sh` is the one networked step, and it runs
outside the sandbox.

```bash
adapters/opensmac/build.sh ~/opensmac-work                    # clone, pin, patch, build (~45 s)
adapters/opensmac/replay_test.sh ~/opensmac-work "$SMAC_DIR"  # 300-turn record/replay test
```

`replay_test.sh` plays the std8 settings (standard map, 7 factions, seed 1003 2003) for 300
turns live with the stub decider and then replays them from the log. It requires all of these:

1. All 903 `turn_NNN.{state,rng,sav}` files (turn 0 to 300) are sha256-identical between the
   live and replayed runs, and every record is used.
2. A record whose `inputs_sha256` is edited makes replay exit 3 at that record's turn.
3. A record whose choice is edited (a legal one: a hurry spend set to 0) makes replay diverge
   at that record's turn, never earlier.

Reference run (20 cores): live 20 s, 12 618 records (5 694 `base_build`, 5 136 `base_hurry`,
1 709 `social_ai`, 79 `tech_selection`). Replay reproduced 903/903 files. Both edits failed at
turn 161, the turn of the edited record.

CI builds the patched runner on every push (the `opensmac-patch` job). The replay test needs
the game's data, so it runs on a game host.

## Licence

OpenSMAC is MIT (Copyright (c) 2026 Richard Penman). The patch adds new MIT-compatible code and
a few lines in OpenSMAC files. Keep its notice when redistributing a patched build.
