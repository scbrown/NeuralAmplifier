# SMAC fixture provenance

Where the game bytes came from, and which hashes we accept. **No game data lives here** — only
paths and checksums (`docs/headless-harness.md` §2.3). The bytes live in `$SMAC_DIR`, outside
the tree.

## Sources

| Provenance | Manifest | Status |
| --- | --- | --- |
| Steam — Planetary Pack, app 2204130 | `steam-2204130.manifest` | **Primary, working.** Complete after depot validation on 2026-08-24. |
| Physical media + Alien Crossfire v2.0 patch | *(not yet built)* | Optional validator. Proves the fixture is reconstructible without a storefront account. Nothing blocks on it. |

## The `terranx.exe` anchor

Thinker requires Alien Crossfire **v2.0**. Two known-good binaries:

| Build | Bytes | SHA-1 |
| --- | --- | --- |
| GOG (cited in `thinker/Technical.md:193-195`) | 3 084 288 | `4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a` |
| **Steam, app 2204130** | **3 094 576** | **`7bbcc54e64760c11a24f48862f15dbaaeab61435`** |

The Steam build is 10 288 bytes larger and hashes differently, **and works anyway** — verified
2026-07-29 on the gaming host: Thinker v5.4 attached to it and played a full game
(`saves/auto/` ran to `Autosave_2234.sav`). Since `thinker/src/patch.cpp:218,250` verify the
original bytes before patching and abort on mismatch, reaching turn-by-turn play proves every
patch site matches v2.0. Its PE link timestamp is 1999-12-20 — an original Firaxis build, not a
storefront repack.

**So the hash is provenance, not a gate.** An unlisted hash means "record a new provenance line
and let Thinker adjudicate at startup", never "reject the install". Game version 1.0 remains
genuinely unsupported.

## Unresolved: 17 paths held mod bytes when scanned

The initial scan marked 17 paths unresolved because their hashes also appeared in Thinker v5.4.
Steam depot validation on 2026-08-24 resolved the ambiguity. Vanilla `alphax.txt` changed from
Thinker's `f593347…` to depot `578519…`; the 15 `basenames/*.txt` files and
`german/alphax.txt` did not change. Thinker redistributes those 16 files byte-for-byte from the
game, so matching the mod archive was not evidence of contamination. Their hashes are now
recorded as vanilla and removed from `overlays.tsv`; the genuinely different Thinker alphax hash
remains a contamination detector.

**Why `alphax.txt` matters more than the rest.** `just ingest` labels it **canonical** and
writes the `smac:` graph. Thinker ships its own tech tree and rules, so ingesting Thinker's copy
under a canonical tag would mislabel house-rule data as game-canonical — the tier invariant
(`AGENTS.md` §4) forbids exactly that, and `just ingest-thinker` exists to carry Thinker's rules
at `house-rule` tier instead. Until this path is resolved, **do not run `just ingest` against
this directory.**

The `basenames/` and `german/` files are cosmetic by comparison (base-name lists, a German
translation) and block nothing.

## Keep the fixture and the play directory separate

`$SMAC_DIR` should be a pristine game tree. The directory you actually *play* in is a copy with
Thinker overlaid on top. Pointing `$SMAC_DIR` at a modded play directory is what produced the 17
unresolved paths above.

## Unclassified: `fx/`, `fx.org/`, `voices/`, `voices.org/`

The install has `fx.org/` (366 files) and `voices.org/` (154), written alongside `fx/` (354) and
`voices/` (141) at a single later timestamp — the `.org` naming implies a mod backed up the
originals and replaced them. It is **not** Thinker's `FixedSoundEffects.zip`, which ships only
two files, neither of which is installed. The source is unidentified.

This is audio only: it touches no decision, no world view, and no contract field, so it is not
worth chasing. `.org` directories are excluded from the fixture, and `fx/`/`voices/` hashes are
recorded as found. If the sound provenance ever matters, start here.
