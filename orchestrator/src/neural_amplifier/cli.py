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

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("neural_amplifier.service:app", host=args.host, port=args.port)
        return 0

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
