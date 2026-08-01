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

    if args.command == "surfaces":
        from .surfaces import coverage

        c = coverage()
        print(f"{c['instrumented']} of {c['total']} surfaces emit a decision record.\n")
        print(f"  {c['remaining']:>3}  remaining, which divides into:")
        print(
            f"  {c['needs_tier_first']:>3}    no native AI path — the deterministic tier has to"
            " be built first,"
        )
        print("        or there is nothing to degrade to (invariant 9)")
        print(
            f"  {c['volume_bound']:>3}    unit-scope with a native path — mostly stay"
            " deterministic on volume grounds"
        )
        print(f"  {c['ready']:>3}    have a native path and a safe fallback — instrumentable now")
        print("\nSee docs/game-surface.md §2.5 for the per-surface matrix.")
        return 0

    if args.command == "ingest":
        from .datalinks import Provenance, briefing, looks_modded, parse_file, turtle

        text = args.alphax.read_text(encoding="latin-1")
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
