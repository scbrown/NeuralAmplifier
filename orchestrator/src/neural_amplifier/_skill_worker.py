"""The child process a skill runs in. Started by :mod:`.skill_runner`, never imported.

Deliberately standalone: it imports nothing from this package, so it can be launched with
``python -I <this file>`` and loads only the standard library and the Starlark binding. The
fewer modules a skill's process has loaded, the less there is that a bug in the binding could
reach.

Protocol: one JSON request per line on stdin, one JSON reply per line on the ORIGINAL stdout.
File descriptor 1 is pointed at stderr before anything else runs, so nothing a skill or the
interpreter prints can be read as a reply.

What makes a skill hermetic here, and where each property comes from:

- **No file, network, clock or randomness.** Starlark has no such builtins, and ``load`` has no
  loader, so a skill cannot reach one. The only callables it gets are the primitives below,
  which are pure: they build a value or append to this request's effect list.
- **Bounded steps.** The interpreter calls ``check_cancelled`` roughly once per thousand
  loop iterations or function calls. Counting those calls is a deterministic step meter: the
  same skill on the same input breaches at the same point every time. Wall time is not used for
  the budget because it is not deterministic.
- **Bounded memory.** ``RLIMIT_AS`` is set once the interpreter is loaded. A breach is either a
  ``MemoryError`` reported here or an allocator abort that kills the process; the parent treats
  a dead worker as a failed run, so both fail closed.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _limit_memory(extra_bytes: int) -> None:
    import resource

    current = 0
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmSize:"):
                    current = int(line.split()[1]) * 1024
                    break
    except OSError:
        current = 0
    if current == 0:
        raise RuntimeError(
            "cannot read this process's address-space size; refusing to run unbounded"
        )
    limit = current + extra_bytes
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


class _StepBudgetExceeded(Exception):
    pass


def _run(starlark: Any, request: dict[str, Any]) -> dict[str, Any]:
    max_ticks = int(request["max_ticks"])
    ticks = [0]
    breached = [False]

    def check_cancelled() -> bool:
        ticks[0] += 1
        if ticks[0] > max_ticks:
            breached[0] = True
            return True
        return False

    effects: list[dict[str, Any]] = []

    def choose(action_id: object, reason: object = None) -> dict[str, Any]:
        return {"kind": "choose", "action_id": action_id, "reason": reason}

    def queue_answer(
        surface_id: object,
        action_id: object,
        predicates: object,
        until_turn: object = None,
        reason: object = None,
        base_id: object = None,
    ) -> None:
        effects.append(
            {
                "kind": "queue",
                "surface_id": surface_id,
                "action_id": action_id,
                "predicates": predicates,
                "until_turn": until_turn,
                "reason": reason,
                "base_id": base_id,
            }
        )

    def order(verb: object, *args: object) -> None:
        effects.append({"kind": "order", "verb": verb, "args": list(args)})

    def set_policy(knob: object, value: object, reason: object = None) -> None:
        effects.append({"kind": "policy", "knob": knob, "value": value, "reason": reason})

    options = starlark.EvalOptions(
        check_cancelled=check_cancelled, max_callstack_size=int(request["max_callstack"])
    )
    module = starlark.Module()
    module.add_callable("choose", choose)
    module.add_callable("queue_answer", queue_answer)
    module.add_callable("order", order)
    module.add_callable("set_policy", set_policy)
    try:
        ast = starlark.parse(request["name"] + ".star", request["code"])
        starlark.eval_with(options, module, ast, starlark.Globals.standard())
        frozen = module.freeze()
        params = request.get("params") or {}
        answer = frozen.call_with(options, request["entry"], request["view"], **params).value
    except MemoryError:
        return {"ok": False, "failure": "memory", "detail": "memory budget exceeded"}
    except starlark.StarlarkError as exc:
        if breached[0]:
            return {
                "ok": False,
                "failure": "steps",
                "detail": f"step budget of {max_ticks} ticks exceeded",
            }
        return {"ok": False, "failure": "error", "detail": str(exc)[:2000]}
    return {"ok": True, "answer": answer, "effects": effects, "ticks": ticks[0]}


def main() -> None:
    replies = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    import starlark

    _limit_memory(int(sys.argv[1]))
    replies.write(json.dumps({"ready": True}) + "\n")
    replies.flush()
    for line in sys.stdin:
        try:
            reply = _run(starlark, json.loads(line))
            encoded = json.dumps(reply)
        except MemoryError:
            encoded = json.dumps(
                {"ok": False, "failure": "memory", "detail": "memory budget exceeded"}
            )
        except Exception as exc:  # noqa: BLE001 — every failure is a reply, never a hang
            encoded = json.dumps({"ok": False, "failure": "error", "detail": repr(exc)[:2000]})
        replies.write(encoded + "\n")
        replies.flush()


if __name__ == "__main__":
    main()
