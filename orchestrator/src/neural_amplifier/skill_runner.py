"""The hermetic runner a skill executes in (``docs/seats-and-policy.md`` §5.3).

**Same input, same output.** That property is what lets a recorded game be re-run against a
changed skill with an exact diff. Everything here exists to protect it, or to fail closed when
it cannot be kept.

**Why Starlark, and why ``starlark-pyo3``.** Starlark was ruled for skills on 2026-11-26: it has
no clock, file, network or randomness builtins, no ``while``, and no unbounded recursion, and it
reads like the Python a model writes well. ``starlark-pyo3`` is the maintained Python binding of
the Rust implementation (``starlark-rust``), ships wheels for every platform this project runs
on, and exposes the two hooks a budget needs: ``check_cancelled`` and ``max_callstack_size``. It
is pinned exactly in ``uv.lock``.

**Why a child process.** The interpreter has a step hook but no memory bound, and a single
``"a" * 10**9`` allocates a gigabyte before any hook runs. An address-space limit is the only
bound that holds against that, and it has to be set on a process of its own. The worker is
long-lived so a decision pays a pipe round trip rather than an interpreter start; any failure
that could leave it in doubt — a breach, a crash, a missed deadline — kills it, and the next run
starts a fresh one.

**Failure is a value, not an exception.** :meth:`SkillRunner.run` returns a :class:`RunResult`
whose ``failure`` names what went wrong: ``steps``, ``memory``, ``timeout``, ``crash`` or
``error``. The caller decides what failing closed means for it. The wall-clock timeout is a
backstop for a hung worker, never the budget: it is not deterministic, so nothing may depend on
where it falls.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Failure = Literal["steps", "memory", "timeout", "crash", "error"]

_WORKER = Path(__file__).with_name("_skill_worker.py")

#: One tick is one ``check_cancelled`` call, which the interpreter makes about once per thousand
#: loop iterations or calls. 2000 ticks is ~2M steps: far beyond what reading one world view and
#: choosing from its action space needs.
DEFAULT_MAX_TICKS = 2000
DEFAULT_MAX_CALLSTACK = 64
#: Address space a skill may allocate beyond what the loaded interpreter already uses.
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
#: Backstop only (see module docstring). Kept under the adapter's default 2500 ms deadline.
DEFAULT_WALL_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class RunResult:
    ok: bool
    answer: Any = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    ticks: int = 0
    failure: Failure | None = None
    detail: str = ""


class SkillRunner:
    """Runs one entry point of one skill against one world view, in a bounded worker."""

    def __init__(
        self,
        max_ticks: int = DEFAULT_MAX_TICKS,
        max_callstack: int = DEFAULT_MAX_CALLSTACK,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        wall_timeout_s: float = DEFAULT_WALL_TIMEOUT_S,
    ) -> None:
        self.max_ticks = max_ticks
        self.max_callstack = max_callstack
        self.memory_bytes = memory_bytes
        self.wall_timeout_s = wall_timeout_s
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def run(
        self,
        name: str,
        code: str,
        entry: str,
        view: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> RunResult:
        request = json.dumps(
            {
                "name": name,
                "code": code,
                "entry": entry,
                "view": view,
                "params": params or {},
                "max_ticks": self.max_ticks,
                "max_callstack": self.max_callstack,
            }
        )
        with self._lock:
            try:
                proc = self._worker()
            except (OSError, RuntimeError) as exc:
                self._kill()
                return RunResult(ok=False, failure="crash", detail=f"worker did not start: {exc}")
            assert proc.stdin is not None
            try:
                proc.stdin.write(request + "\n")
                proc.stdin.flush()
            except OSError as exc:
                self._kill()
                return RunResult(ok=False, failure="crash", detail=f"worker unreachable: {exc}")
            line = self._readline(proc, self.wall_timeout_s)
            if line is None:
                self._kill()
                return RunResult(
                    ok=False,
                    failure="timeout",
                    detail=f"no reply within {self.wall_timeout_s:g}s; worker killed",
                )
            if not line:
                try:
                    code_: int | None = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    code_ = None
                self._kill()
                # An allocator abort (SIGABRT) under RLIMIT_AS is how a worker dies when a single
                # allocation breaches the bound; anything else is reported as a crash.
                return RunResult(
                    ok=False,
                    failure="memory" if code_ == -6 else "crash",
                    detail=f"worker died (exit {code_})",
                )
        reply = json.loads(line)
        if not reply.get("ok"):
            failure = reply.get("failure", "error")
            if failure in ("memory",):
                with self._lock:
                    self._kill()
            return RunResult(ok=False, failure=failure, detail=str(reply.get("detail", "")))
        return RunResult(
            ok=True,
            answer=reply.get("answer"),
            effects=list(reply.get("effects") or []),
            ticks=int(reply.get("ticks", 0)),
        )

    def close(self) -> None:
        with self._lock:
            self._kill()

    def __del__(self) -> None:  # pragma: no cover — best effort at interpreter exit
        with contextlib.suppress(Exception):
            self._kill()

    def _worker(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if sys.platform != "linux":
            # The memory bound reads /proc and relies on RLIMIT_AS being enforced. Elsewhere a
            # skill would run unbounded, so it does not run at all.
            raise RuntimeError(f"skills need a Linux host for the memory bound, not {sys.platform}")
        proc = subprocess.Popen(
            [sys.executable, "-I", str(_WORKER), str(self.memory_bytes)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={},
            close_fds=True,
        )
        self._proc = proc
        ready = self._readline(proc, 30.0)
        if not ready or not json.loads(ready).get("ready"):
            raise RuntimeError("worker failed its start handshake")
        return proc

    @staticmethod
    def _readline(proc: subprocess.Popen[str], timeout_s: float) -> str | None:
        """One line from the worker, ``""`` on EOF, ``None`` on timeout."""
        assert proc.stdout is not None
        box: list[str] = []
        reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)  # type: ignore[union-attr]
        reader.start()
        reader.join(timeout_s)
        if reader.is_alive():
            return None
        return box[0] if box else ""

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — already failing closed
            pass
