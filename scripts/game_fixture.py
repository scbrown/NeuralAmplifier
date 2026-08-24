#!/usr/bin/env python3
"""The SMAC game fixture: build a manifest of an install, and verify one against it.

The repo holds the manifest, never the bytes (docs/headless-harness.md §2.3). Game data is
copyrighted; only relative paths and checksums are version-controlled.

Two things this does that a bare `sha1sum -c` does not:

1. **It excludes non-fixture files by rule, not by hand.** A play directory accumulates mod
   files, savegames, and generated caches. Those are not the game, so they are not in the
   manifest, and their presence is not an error.

2. **It explains a mismatch instead of just reporting one.** A path whose bytes belong to a
   known mod overlay (fixtures/smac/overlays.tsv) is reported as *contaminated*, naming the
   mod. That distinction matters: Thinker ships its own alphax.txt, and ingesting it as
   canonical would mislabel house-rule data as game-canonical — the one thing the tier
   invariant forbids (AGENTS.md §4, `just ingest-thinker`).

Stdlib only, on purpose: the fixture is a prerequisite for everything else, so verifying it
must not require the orchestrator's Python environment to be synced first.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

# --- What is not part of the fixture ------------------------------------------------------
#
# Anything matched here is skipped by `scan` and ignored by `verify`. These are additions to
# a game directory, never the game itself.

# Directories that are entirely mod payload, per-run state, or pre-mod backups.
EXCLUDED_DIRS = frozenset(
    {
        "saves",  # savegames, incl. Thinker's saves/auto/ autosave stream
        "smac_mod",  # Thinker-only: its SMAC-mode data overlay
        "fx.org",  # a backup of fx/ made by some sound overlay (unclassified, see PROVENANCE)
        "voices.org",  # ditto for voices/
        "EmptySteamDepot",  # Steam packaging artifact, not game data
        "na-backup-stock",  # play-thinker.sh's pre-mod restore point
    }
)

# Exact relative paths that are mod payload or run state.
EXCLUDED_FILES = frozenset(
    {
        "thinker.exe",
        "thinker.dll",
        "thinker.ini",  # mod config, edited per-host
        "modmenu.txt",  # Thinker's menu definition
        "Readme.md",
        "Changelog.md",
        "Details.md",  # Thinker docs
        "debug.txt",
        "logfile.txt",  # Thinker crash/debug output
        "Alpha Centauri.Ini",  # written by the game on first run
    }
)

# Suffixes that are generated caches or logs, recreated by the game as needed.
EXCLUDED_SUFFIXES = (".tmp", ".sfk", ".log")


def is_excluded(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in EXCLUDED_DIRS for p in parts):
        return True
    if rel in EXCLUDED_FILES:
        return True
    return rel.lower().endswith(EXCLUDED_SUFFIXES)


# --- Hashing ------------------------------------------------------------------------------


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_fixture(root: Path) -> list[str]:
    """Every fixture-relevant file under root, as sorted relative POSIX paths."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune excluded directories so we never descend into them.
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if rel.startswith("./"):
                rel = rel[2:]
            if not is_excluded(rel):
                found.append(rel)
    return sorted(found)


# --- Manifest I/O -------------------------------------------------------------------------
#
# Format: `sha1 <TAB> size <TAB> relpath`, sorted by relpath, `#` comments carrying
# provenance. Line-oriented and sorted so a diff between two provenance sources is readable.


def read_manifest(path: Path) -> dict[str, tuple[str, int]]:
    entries: dict[str, tuple[str, int]] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            sys.exit(f"{path}:{lineno}: expected 3 tab-separated fields, got {len(fields)}")
        digest, size, rel = fields
        entries[rel] = (digest, int(size))
    return entries


def read_overlays(path: Path) -> dict[str, str]:
    """sha1 -> source label, for explaining a mismatch."""
    if not path.exists():
        return {}
    overlays: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3:
            overlays[fields[0]] = fields[2]
    return overlays


# --- Commands -----------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    rels = walk_fixture(root)
    overlays = read_overlays(Path(args.overlays))

    hashed = [(rel, sha1(root / rel), (root / rel).stat().st_size) for rel in rels]

    # A manifest is a claim about *game* bytes. If the source install has a mod overlay on
    # top of it, recording those hashes would launder house-rule data into the canonical
    # fixture — so refuse by default rather than produce a quietly wrong manifest.
    dirty = [(rel, overlays[d]) for rel, d, _ in hashed if d in overlays]
    if dirty and not args.allow_contaminated:
        print(
            f"REFUSING to scan: {len(dirty)} file(s) hold mod bytes, not game bytes.",
            file=sys.stderr,
        )
        for rel, src in dirty[: args.limit]:
            print(f"  {rel}  ({src})", file=sys.stderr)
        if len(dirty) > args.limit:
            print(f"  ... and {len(dirty) - args.limit} more", file=sys.stderr)
        print(
            "\nScanning this tree would bake mod hashes into the canonical manifest.\n"
            "Restore the game files first (Steam: Properties > Installed Files >\n"
            "Verify integrity of game files), then scan a pristine tree.\n"
            "Use --allow-contaminated to record the pristine files only, listing these\n"
            "as unresolved.",
            file=sys.stderr,
        )
        return 1

    out = [
        "# SMAC game fixture manifest — paths and checksums only, never the bytes.",
        "# Generated by scripts/game_fixture.py; see docs/headless-harness.md §2.3.",
        f"# provenance: {args.provenance}",
        f"# files: {len(hashed) - len(dirty)}",
    ]
    if dirty:
        out += [
            "#",
            f"# INCOMPLETE — {len(dirty)} path(s) held mod bytes when scanned and have no",
            "# recorded vanilla hash. Restore the game files and re-scan to resolve:",
        ]
        out += [f"#   unresolved: {rel}  (was {src})" for rel, src in dirty]
    out += ["#", "# sha1<TAB>size<TAB>relpath"]

    skip = {rel for rel, _ in dirty}
    out += [f"{d}\t{size}\t{rel}" for rel, d, size in hashed if rel not in skip]

    text = "\n".join(out) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}  ({len(rels)} files)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        print(
            f"FAIL  $SMAC_DIR does not exist or is not a directory: {root}",
            file=sys.stderr,
        )
        print(
            "      Point SMAC_DIR at your extracted install (docs/headless-harness.md §2.3).",
            file=sys.stderr,
        )
        return 2

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"FAIL  no manifest at {manifest_path}", file=sys.stderr)
        print("      Generate one: just game scan", file=sys.stderr)
        return 2

    expected = read_manifest(manifest_path)
    overlays = read_overlays(Path(args.overlays))

    missing: list[str] = []
    unreadable: list[tuple[str, str]] = []
    contaminated: list[tuple[str, str]] = []
    wrong: list[tuple[str, str, str]] = []
    ok = 0

    for rel, (want_sha, _want_size) in sorted(expected.items()):
        p = root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        try:
            got_sha = sha1(p)
        except OSError as exc:
            # Present but unreadable is a different problem from absent or wrong, and a
            # traceback here would bury the other 1 600 results.
            unreadable.append((rel, exc.strerror or str(exc)))
            continue
        if got_sha == want_sha:
            ok += 1
        elif got_sha in overlays:
            contaminated.append((rel, overlays[got_sha]))
        else:
            wrong.append((rel, want_sha, got_sha))

    extra = sorted(set(walk_fixture(root)) - set(expected)) if args.strict else []

    total = len(expected)
    print(f"fixture: {root}")
    print(f"manifest: {manifest_path}  ({total} files)")
    print(f"  ok            {ok}")
    print(f"  missing       {len(missing)}")
    print(f"  contaminated  {len(contaminated)}")
    print(f"  wrong         {len(wrong)}")
    if unreadable:
        print(f"  unreadable    {len(unreadable)}")
    if args.strict:
        print(f"  unexpected    {len(extra)}")

    limit = args.limit

    if contaminated:
        print("\ncontaminated — these hold MOD bytes, not game bytes:")
        for rel, src in contaminated[:limit]:
            print(f"  {rel}  ({src})")
        if len(contaminated) > limit:
            print(f"  ... and {len(contaminated) - limit} more")
        print(
            "\n  A mod overlay is installed over the fixture. Restore the game files\n"
            "  (Steam: Properties > Installed Files > Verify integrity of game files),\n"
            "  and keep the play directory separate from $SMAC_DIR."
        )
        if any(rel == "alphax.txt" for rel, _ in contaminated):
            print(
                "\n  alphax.txt specifically: `just ingest` labels this file CANONICAL.\n"
                "  Ingesting a mod's copy would mislabel house-rule data as game-canonical.\n"
                "  Use `just ingest-thinker` for Thinker's rules — never this path."
            )

    if missing:
        print("\nmissing:")
        for rel in missing[:limit]:
            print(f"  {rel}")
        if len(missing) > limit:
            print(f"  ... and {len(missing) - limit} more")

    if wrong:
        print("\nwrong bytes (unrecognised — not a mod overlay we know):")
        for rel, want, got in wrong[:limit]:
            print(f"  {rel}\n    want {want}\n    got  {got}")
        if len(wrong) > limit:
            print(f"  ... and {len(wrong) - limit} more")

    if unreadable:
        print("\nunreadable (present, but could not be read — check permissions):")
        for rel, why in unreadable[:limit]:
            print(f"  {rel}  ({why})")
        if len(unreadable) > limit:
            print(f"  ... and {len(unreadable) - limit} more")

    if extra:
        print("\nunexpected (present but not in the manifest):")
        for rel in extra[:limit]:
            print(f"  {rel}")
        if len(extra) > limit:
            print(f"  ... and {len(extra) - limit} more")

    bad = missing or contaminated or wrong or extra or unreadable
    print("\nFAIL" if bad else "\nOK  fixture matches the manifest.")
    return 1 if bad else 0


def cmd_stage(args: argparse.Namespace) -> int:
    """Copy a pristine install to a play directory and overlay the mod there.

    This is the recurrence fix for na-8ie, and the bead names it: Thinker was installed
    *directly into the Steam directory* and overwrote 17 tracked files — `alphax.txt`,
    `german/alphax.txt` and 15 `basenames/*.txt`. That is not a mistake anyone made carelessly;
    it is what happens when the mod's install instructions and the fixture's requirements point
    at the same directory. Repairing it needs the Steam client. Not doing it again needs this.

    `alphax.txt` is the one that matters. `just ingest` labels it canonical, so ingesting
    Thinker's copy would mislabel house-rule data as game-canonical — invariant 4, and a
    contamination that is invisible downstream because the graph looks the same either way.

    THE SOURCE IS NEVER WRITTEN. Every guard below exists to keep that true, because a staging
    tool that could scribble on the pristine tree would reintroduce the exact bug it exists to
    prevent.
    """
    source = Path(args.source)
    play = Path(args.play)

    if not source.is_dir():
        print(f"source is not a directory: {source}", file=sys.stderr)
        return 1

    # Refuse to stage FROM a contaminated tree. Copying a tree that already holds mod bytes
    # would produce a play directory that looks staged and carries the same overwritten
    # alphax.txt — the failure this exists to prevent, wearing a fresh directory name.
    manifest_path = Path(args.manifest)
    if manifest_path.exists():
        manifest = read_manifest(manifest_path)
        overlays = read_overlays(Path(args.overlays))
        contaminated = []
        for rel, (want, _size) in manifest.items():
            candidate = source / rel
            if not candidate.is_file():
                continue
            got = sha1(candidate)
            if got != want and got in overlays:
                contaminated.append(f"{rel} ({overlays[got]})")
        if contaminated:
            print(
                f"refusing to stage: {source} already holds mod bytes in "
                f"{len(contaminated)} tracked file(s):",
                file=sys.stderr,
            )
            for line in sorted(contaminated)[: args.limit]:
                print(f"  {line}", file=sys.stderr)
            if len(contaminated) > args.limit:
                print(f"  ... and {len(contaminated) - args.limit} more", file=sys.stderr)
            print(
                "\nThat is na-8ie. Repair the source with Steam's "
                "Properties > Installed Files > Verify integrity of game files, then re-run.",
                file=sys.stderr,
            )
            return 1

    # Never stage into the source, or into anything containing it. Both would write to the tree
    # whose whole job is to stay untouched.
    src_resolved = source.resolve()
    play_resolved = play.resolve()
    if src_resolved == play_resolved:
        print("refusing to stage a directory onto itself", file=sys.stderr)
        return 1
    if src_resolved in play_resolved.parents or play_resolved in src_resolved.parents:
        print(
            f"refusing to stage: {play_resolved} and {src_resolved} contain one another",
            file=sys.stderr,
        )
        return 1

    if play.exists() and not args.force:
        print(
            f"{play} already exists — pass --force to replace it, or remove it first",
            file=sys.stderr,
        )
        return 1

    before = sha1(source / "alphax.txt") if (source / "alphax.txt").is_file() else None

    if play.exists():
        shutil.rmtree(play)
    shutil.copytree(source, play, symlinks=True)

    copied = 0
    if args.mod:
        mod = Path(args.mod)
        if not mod.is_dir():
            print(f"mod directory not found: {mod}", file=sys.stderr)
            return 1
        for item in sorted(mod.iterdir()):
            if item.is_file():
                shutil.copy2(item, play / item.name)
                copied += 1

    # The check that gives this tool its point: the source must be byte-identical afterwards.
    # Asserted rather than assumed, because "I only read from it" is exactly what the original
    # install also believed.
    if before is not None:
        after = sha1(source / "alphax.txt")
        if after != before:
            print(
                "STAGING MODIFIED THE SOURCE — alphax.txt changed. This is the bug this "
                "command exists to prevent; do not use the result.",
                file=sys.stderr,
            )
            return 1

    print(f"staged {source} -> {play}")
    if copied:
        print(f"overlaid {copied} file(s) from {args.mod}")
    print("source unchanged (alphax.txt verified)" if before is not None else "source unchanged")
    print(f"\nPoint the harness at it:  export SMAC_PLAY_DIR={play_resolved}")
    print(f"Keep SMAC_DIR on the pristine tree:  {src_resolved}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="write a manifest for an install")
    s.add_argument("dir", help="the install to scan")
    s.add_argument("--out", help="manifest path (default: stdout)")
    s.add_argument(
        "--provenance",
        default="unspecified",
        help="where these bytes came from, e.g. 'steam app 2204130'",
    )
    s.add_argument("--overlays", default="fixtures/smac/overlays.tsv")
    s.add_argument(
        "--allow-contaminated",
        action="store_true",
        help="scan anyway, recording pristine files only and listing the rest unresolved",
    )
    s.add_argument("--limit", type=int, default=15, help="max paths to list per category")
    s.set_defaults(func=cmd_scan)

    v = sub.add_parser("verify", help="check an install against a manifest")
    v.add_argument("dir", help="the fixture to verify ($SMAC_DIR)")
    v.add_argument("--manifest", required=True)
    v.add_argument("--overlays", default="fixtures/smac/overlays.tsv")
    v.add_argument(
        "--strict",
        action="store_true",
        help="also fail on files present but absent from the manifest",
    )
    v.add_argument("--limit", type=int, default=15, help="max paths to list per category")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser(
        "stage",
        help="copy a pristine install to a play directory and overlay the mod there",
    )
    t.add_argument("source", help="the PRISTINE install ($SMAC_DIR) — never written")
    t.add_argument("play", help="the play directory to create")
    t.add_argument("--mod", help="directory whose files are overlaid onto the copy")
    t.add_argument("--manifest", default="fixtures/smac/steam-2204130.manifest")
    t.add_argument("--overlays", default="fixtures/smac/overlays.tsv")
    t.add_argument("--force", action="store_true", help="replace an existing play directory")
    t.add_argument("--limit", type=int, default=15, help="max paths to list")
    t.set_defaults(func=cmd_stage)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
