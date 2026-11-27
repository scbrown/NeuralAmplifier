"""The seat harness profile: what an attached seat session may touch (§3.2, tier 2).

Tier 1 (seat tokens, scoped claim, per-seat evidence) stops an honest agent. A crew seat on the
game host shares a unix user and a filesystem with the orchestrator, the run directory and every
sibling seat's log, so a curious agent with a shell walks straight past it. This module states
the seat's session as permission rules a harness enforces, so the only thing the seat can reach
is its own Neural Amplifier MCP server.

**Built-in tools are removed, not merely denied.** ``--tools ""`` gives the session no file or
shell tool at all, which is how ``claude -p`` already runs a model seat (``claude_code_brain``).
The deny rules below are the second lock, for a harness that honours rules but not the flag.
A path rule on ``Read`` does not bind ``Bash`` (``cat`` reads anything), so a shell is denied
outright rather than scoped.

**No other MCP server.** ``--strict-mcp-config`` drops every server the host configures: the
crew's semantic search, the knowledge graph and yupana. Only the orchestrator calls yupana
(tier 3), so a seat never holds a yupana URL, and :func:`build` refuses one in the seat's
environment rather than writing it out.

**No host settings.** ``--setting-sources ""`` keeps a project's own allow rules from merging into
the seat's; ``--permission-mode dontAsk`` denies anything not pre-approved instead of prompting,
because nobody is at the seat's terminal to say no.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The one MCP server a seat holds; its tools are ``mcp__neural-amplifier__<tool>``.
SERVER = "neural-amplifier"

#: Built-in tools that read, write or search files. Denied on every protected path.
FILE_TOOLS: tuple[str, ...] = ("Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit")

#: Built-in tools denied outright: a shell ignores path rules, and the others reach past the
#: seat (the network, or a sub-agent with its own tool set).
DENIED_TOOLS: tuple[str, ...] = ("Bash", "WebFetch", "WebSearch", "Task", "Agent")

#: Environment a seat's MCP server is given, and nothing else.
SEAT_ENV: tuple[str, ...] = ("NA_URL", "NA_SEAT_TOKEN")

#: Substrings that mark a service a seat must not be able to reach (tier 3).
FORBIDDEN_SERVICES: tuple[str, ...] = ("YUPANA", "HANK", "QUIPU")


class ProfileError(ValueError):
    """A profile that would hand a seat something it must not hold."""


def _rule_path(path: Path) -> str:
    """An absolute path in permission-rule spelling: ``//abs/path/**``."""
    return "/" + str(path.resolve()).rstrip("/") + "/**"


@dataclass(frozen=True)
class SeatProfile:
    """The settings, MCP configuration and launch flags for one seat's session."""

    seat: int
    run_dir: Path
    settings: dict[str, Any]
    mcp_config: dict[str, Any]
    protected: tuple[Path, ...] = field(default=())

    def argv(self, settings_path: Path, mcp_path: Path) -> list[str]:
        """The harness flags that make the profile binding, given where it was written."""
        return [
            "claude",
            "--tools",
            "",
            "--setting-sources",
            "",
            "--settings",
            str(settings_path),
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_path),
            "--permission-mode",
            "dontAsk",
        ]

    def write(self, out_dir: Path) -> tuple[Path, Path]:
        """Write ``settings.json`` and ``mcp.json`` to ``out_dir`` at 0600.

        Refused inside the run directory: the MCP config carries the seat's token, and a file the
        orchestrator's run tree holds is one every sibling seat is trying to read.
        """
        out = out_dir.resolve()
        run = self.run_dir.resolve()
        if out == run or run in out.parents:
            raise ProfileError(f"write the seat profile outside the run directory ({run})")
        out.mkdir(parents=True, exist_ok=True)
        paths = (out / "settings.json", out / "mcp.json")
        for path, body in zip(paths, (self.settings, self.mcp_config), strict=True):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps(body, indent=2) + "\n").encode())
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
        return paths


def _check_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        if key not in SEAT_ENV:
            raise ProfileError(f"seat env may hold only {', '.join(SEAT_ENV)}; got {key}")
        if any(s in key.upper() or s.lower() in value.lower() for s in FORBIDDEN_SERVICES):
            raise ProfileError(f"seat env {key} names a service only the orchestrator may call")


def build(
    seat: int,
    run_dir: Path,
    service_url: str,
    token: str | None = None,
    protect: tuple[Path, ...] = (),
    command: tuple[str, ...] = ("neural-amplifier", "mcp"),
) -> SeatProfile:
    """The profile for ``seat``. The token defaults to ``<run_dir>/seat-<n>/token``.

    ``protect`` adds paths beyond the run directory a seat must not read (the game's saves, an
    install with other runs in it). The run directory is always protected, the seat's own
    subdirectory included: a seat reads its own record through its tools, not off the disk.
    """
    run = Path(run_dir)
    if token is None:
        token = (run / f"seat-{seat}" / "token").read_text().strip()
    if not token:
        raise ProfileError(f"seat {seat}: empty seat token")
    env = {"NA_URL": service_url, "NA_SEAT_TOKEN": token}
    _check_env(env)

    protected = (run, *protect)
    deny = [f"{tool}({_rule_path(p)})" for p in protected for tool in FILE_TOOLS]
    deny += list(DENIED_TOOLS)
    settings = {
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": [f"mcp__{SERVER}"],
            "deny": deny,
        },
        "enableAllProjectMcpServers": False,
    }
    # The token rides in the server's environment, never on its command line: argv is world
    # readable through /proc, and `ps` on a shared host would hand every seat every token.
    mcp_config = {
        "mcpServers": {
            SERVER: {"type": "stdio", "command": command[0], "args": list(command[1:]), "env": env}
        }
    }
    return SeatProfile(seat, run, settings, mcp_config, tuple(protected))


def _matches(rule: str, tool: str, path: Path | None) -> bool:
    """Does one permission rule cover this call? Bare names and ``Tool(//abs/**)`` only."""
    if "(" not in rule:
        return rule == tool or (rule.startswith("mcp__") and tool.startswith(rule + "__"))
    name, _, pattern = rule.partition("(")
    pattern = pattern.rstrip(")")
    if name != tool or path is None or not pattern.startswith("//"):
        return False
    base = pattern[1:].removesuffix("/**")
    target = str(path.resolve())
    return target == base or target.startswith(base + "/") or fnmatch.fnmatch(target, pattern[1:])


def permits(profile: SeatProfile, tool: str, path: Path | None = None) -> bool:
    """Would the profile let ``tool`` run (on ``path``)? Deny first, then allow, else no.

    The model the leak probe asks: ``dontAsk`` refuses anything not pre-approved, and with
    ``--tools ""`` the built-ins are absent whatever a rule says. A file tool with no path is
    judged as a whole-filesystem call and refused.
    """
    perms = profile.settings["permissions"]
    if any(_matches(r, tool, path) for r in perms["deny"]):
        return False
    if tool in FILE_TOOLS and path is None:
        return False
    return any(_matches(r, tool, path) for r in perms["allow"])
