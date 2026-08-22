"""Command line entry point: ``neural-amplifier``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .coverage import report
from .decisions import DecisionLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neural-amplifier")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the orchestrator HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    mcp = sub.add_parser(
        "mcp",
        help="run the MCP server an agent plays through (stdio)",
    )
    mcp.add_argument(
        "--url",
        default=None,
        help="running orchestrator to attach to (default $NA_URL or http://127.0.0.1:8000)",
    )

    learn = sub.add_parser(
        "learn",
        help="extract a finished game's decision log into learned memory (K3)",
    )
    learn.add_argument("log", type=Path)
    learn.add_argument(
        "--url",
        default=None,
        help="memory store (default $NA_MEMORY_QUIPU_URL)",
    )
    learn.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be written and write nothing",
    )

    cov = sub.add_parser("coverage", help="summarise a decision log")
    cov.add_argument("log", type=Path)
    cov.add_argument(
        "--max-degrade-rate",
        type=float,
        default=None,
        help="fail if the fallback rate exceeds this (e.g. 0.05)",
    )

    rep = sub.add_parser("replay", help="re-run a recorded log with no game")
    rep.add_argument("log", type=Path)
    rep.add_argument("--store", type=Path, required=True, help="world-view store from the run")
    rep.add_argument(
        "--exact",
        action="store_true",
        help="require identical decisions (scripted-brain runs only)",
    )

    sub.add_parser("surfaces", help="how much of the game surface is instrumented")

    ing = sub.add_parser("ingest", help="parse alphax.txt into the smac: datalinks graph")
    ing.add_argument("alphax", type=Path, help="path to alphax.txt in your SMAC install")
    ing.add_argument("--out", type=Path, help="write Turtle here")
    ing.add_argument("--engine", default="smac", help="appliesToEngine tag")
    ing.add_argument("--tier", default="canonical", help="ruleTier tag")
    ing.add_argument("--briefing", type=Path, help="also write the static briefing text here")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("neural_amplifier.service:app", host=args.host, port=args.port)
        return 0

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main(args.url)

    if args.command == "learn":
        from .memory import MemoryStore, episodes

        extraction = episodes(args.log)
        if not extraction:
            print(f"nothing to learn from {args.log}")
            return 1
        print(
            f"game {extraction.game_id}: {len(extraction.nodes)} node(s), "
            f"{len(extraction.tactics)} tactic(s)"
        )
        for tactic in extraction.tactics:
            props = tactic["properties"]
            print(
                f"  {tactic['description']} "
                f"[{props['observations']}x, confidence {props['confidence']}]"
            )
        if args.dry_run:
            print("\ndry run — nothing written")
            return 0
        store = MemoryStore(args.url)
        if not store.available:
            # Not a crash and not a silent success: extraction worked and there is nowhere to
            # put it, which is a configuration answer rather than a failure of the game.
            print("\nno memory store configured (set NA_MEMORY_QUIPU_URL); nothing written")
            return 1
        # No faction argument, deliberately and visibly: one log holds every faction's
        # decisions, and splitting the extraction per faction is not done yet (na-7bk slice 2).
        # So these nodes land in the per-GAME group, unattributed.
        sent = store.write(extraction)
        print(f"\nwrote {sent['game']} to the game group, {sent['durable']} durable")
        if sent["game"]:
            # Said out loud because the alternative is a silent asymmetry: the read path is
            # scoped per faction and fail-closed (na-7bk), so nodes written without a faction
            # are NOT reachable by a decision-loop recall. That is the safe direction — no
            # leak — but somebody will write memories, see recall return nothing, and go
            # looking for a bug in the query.
            print(
                "  note: written unattributed, so a per-faction recall will not return these.\n"
                "        Durable tactics are unaffected — they are shared by design."
            )
        return 0

    if args.command == "surfaces":
        from .config import load as load_config
        from .surfaces import coverage

        c = coverage()
        print(f"{c['applied']} of {c['total']} surfaces the brain can actually decide.\n")
        print(f"  {c['applied']:>3}  applied — the brain's choice executes")
        print(
            f"  {c['observed_not_applied']:>3}  observed only — a record is written, the"
            " engine still chooses"
        )
        print(f"  {c['remaining']:>3}  not instrumented, which divides into:")
        print(
            f"  {c['needs_tier_first']:>3}    no native AI path — the deterministic tier has to"
            " be built first,"
        )
        print("        or there is nothing to degrade to (invariant 9)")
        print(
            f"  {c['volume_bound']:>3}    unit-scope with a native path — mostly stay"
            " deterministic on volume grounds"
        )
        print(
            f"  {c['subsumed']:>3}    no separate decision to instrument — already answered under"
            " another id,"
        )
        print("        computed rather than chosen, or engine-internal (surfaces.SUBSUMED)")
        print(f"  {c['ready']:>3}    have a native path and a safe fallback — instrumentable now")
        config = load_config()
        if config.source is None:
            print("\nNo na.toml — every surface the adapter emits is decided.")
        else:
            from .surfaces import APPLIED, OBSERVED

            # Computed from APPLIED, not OBSERVED. Only a surface with an apply path CAN be
            # switched on, so listing an observe-only one as "switched off" invites someone to
            # go looking for the na.toml line that would enable it — and there isn't one. This
            # read correctly only while the two sets were identical, which stopped being true
            # the moment the first observe-only surface landed (na-yd4).
            off = sorted(s for s in APPLIED if not config.surfaces.allows(s))
            observe_only = sorted(OBSERVED - APPLIED)
            print(
                f"\nPolicy ({config.source.name}): "
                f"surface_default={str(config.surfaces.default).lower()}"
            )
            print(f"  instrumented but switched off: {', '.join(off) if off else 'none'}")
            if observe_only:
                print(f"  observed only, no apply path yet: {', '.join(observe_only)}")
        print("\nSee docs/game-surface.md §2.5 for the per-surface matrix.")
        return 0

    if args.command == "ingest":
        from .datalinks import Provenance, briefing, looks_modded, parse_file, turtle
        from .datalinks.parse import overlay_source

        raw = args.alphax.read_bytes()
        text = raw.decode("latin-1")

        # Two independent checks, and neither subsumes the other. The hash catches a known mod
        # that does not announce itself; the header catches an unknown one that does. This one
        # first because it can name the mod *and its version*.
        overlay = overlay_source(raw)
        if overlay and args.tier == "canonical":
            print(
                f"FAIL: {args.alphax} is byte-identical to {overlay}'s alphax.txt "
                "(fixtures/smac/overlays.tsv), but --tier is 'canonical'. Your $SMAC_DIR has a "
                "mod installed over it. Use `just ingest-thinker` for the house-rule graph, or "
                "point at a clean install.",
                file=sys.stderr,
            )
            return 1

        marker = looks_modded(text)
        if marker and args.tier == "canonical":
            # The whole point of the tier predicate. A mod's alphax.txt is
            # byte-for-byte the same shape as stock, so nothing downstream can
            # tell them apart once it is stored as canonical.
            print(
                f"FAIL: {args.alphax} announces itself as a mod ({marker!r} in its header) "
                "but --tier is 'canonical'. Re-run with --engine thinker --tier house-rule, "
                "or point at your own install's stock alphax.txt.",
                file=sys.stderr,
            )
            return 1

        links = parse_file(args.alphax)
        counts: dict[str, object] = {
            "technologies": len(links.technologies),
            "facilities": len(links.facilities),
            "secret_projects": len(links.secret_projects),
            "components": len(links.components),
            "disabled_facilities": sum(1 for f in links.facilities.values() if f.disabled),
            "engine": args.engine,
            "tier": args.tier,
            "modded_source": marker,
            "overlay_source": overlay,
        }
        if args.out:
            provenance = Provenance(engine=args.engine, tier=args.tier, source=args.alphax.name)
            args.out.write_text(turtle(links, provenance), encoding="utf-8")
            counts["turtle"] = str(args.out)
        if args.briefing:
            # Trailing newline: the file is committed for the Thinker graph, and
            # without it end-of-file-fixer rewrites it on every regeneration,
            # so `just ingest-thinker && just check` never comes out clean.
            args.briefing.write_text(briefing(links, args.engine) + "\n", encoding="utf-8")
            counts["briefing"] = str(args.briefing)
        print(json.dumps(counts, indent=2))
        return 0

    if args.command == "replay":
        from .brain import ScriptedBrain
        from .orchestrator import Orchestrator
        from .replay import WorldViewStore, replay

        comparison = replay(
            DecisionLog(args.log).read(),
            WorldViewStore(args.store),
            Orchestrator(ScriptedBrain()),
        )
        print(json.dumps(comparison.summary(), indent=2))
        for divergence in comparison.diverged:
            print(
                f"  turn {divergence.turn} {divergence.surface_id}: "
                f"{divergence.before} -> {divergence.after} ({divergence.reason})",
                file=sys.stderr,
            )
        problems: list[str] = []
        if comparison.replayed == 0:
            problems.append("nothing was replayed — the store has none of the log's inputs")
        if not comparison.consistent:
            problems.append("replay is inconsistent: new degradation or lost surfaces")
        if args.exact and not comparison.deterministic:
            problems.append(f"{len(comparison.diverged)} decision(s) changed")
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1 if problems else 0

    summary = report(DecisionLog(args.log).read())
    print(json.dumps(summary.summary(), indent=2))

    failures: list[str] = []
    if not summary.adherent:
        failures.append(
            f"action-space adherence broken: {summary.adherence_violations} violation(s)"
        )
    if args.max_degrade_rate is not None and summary.degrade_rate > args.max_degrade_rate:
        failures.append(
            f"degrade rate {summary.degrade_rate:.2%} exceeds "
            f"{args.max_degrade_rate:.2%} — the brain was largely absent"
        )
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
