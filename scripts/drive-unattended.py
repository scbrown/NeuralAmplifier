#!/usr/bin/env python3
"""Keep an unattended SMAC run moving, and MEASURE whether it is moving.

    scripts/drive-unattended.py <play-dir> <turn-log> [seconds]

`docs/headless-harness.md` §3.1 says "the real blocker is menus, not rendering", and this is the
part that was still missing: autoload skips the MAIN menu, and then the game raises in-play
dialogs — a NETFLASH notice, a base-completed panel, a Planetary Council vote — each of which
blocks the turn until something clicks. NA_AUTO_TURN does not clear them.

Without this, an unattended run reaches the first modal and stops. Measured before it existed:
turn 101, held for 25 minutes, with every click reporting success.

Each cycle: screenshot, find the dialog action bar BY COLOUR (never by fixed coordinate — see
_dialog_bars.py for why), click its centre if there is one, and record the turn.

The turn log is the point. "The driver is running" and "the game is advancing" are different
facts, and only the second one matters — a click that lands outside the window reports success
exactly like one that lands on the button, which is what stalled the first attempt here.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _dialog_bars import bars  # noqa: E402
from win_ladder import VIABLE_BY_TURN, parse_state, viability  # noqa: E402

G = sys.argv[1]
OUT = sys.argv[2]
DEADLINE = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 5400)

#: Turn the viability checkpoint off for a DIAGNOSTIC run (`--no-viability`).
#:
#: Not a convenience. The checkpoint stops a run at turn 20 when our faction is below the bar,
#: which is exactly right for a ladder row and exactly wrong when the thing being investigated is
#: WHY it is below the bar — every diagnostic run was ending three turns after the first colony
#: pod appeared. The flag is explicit, per-invocation, and says so in the log, so a ladder row
#: cannot pick it up by accident and a reader of the log cannot mistake one for the other.
NO_VIABILITY = "--no-viability" in sys.argv

#: Stop sending SPACE (`--no-space`), for a DIAGNOSTIC run.
#:
#: SPACE is "skip this unit", and a skip consumes the unit's whole movement allowance. The driver
#: sends it when the turn will not advance, on the assumption that whatever is waiting for orders
#: is something we do not care about. A COLONY POD waiting for orders is something we care about
#: very much: na-1lj measures pods that hold a stable waypoint five tiles away, on the same land
#: region, with a full movement budget, and never change tile.
NO_SPACE = "--no-space" in sys.argv


def cmd(line, wait=15.0):
    res = os.path.join(G, "na-command-result")
    try:
        os.remove(res)
    except FileNotFoundError:
        pass
    with open(os.path.join(G, "na-command"), "w") as fh:
        fh.write(line)
    end = time.time() + wait
    while time.time() < end:
        if os.path.exists(res):
            try:
                with open(res) as fh:
                    return json.load(fh)
            except Exception:
                pass
        time.sleep(0.4)
    return None


def key(vk):
    """A virtual-key press. `key` takes a NUMERIC vk code — a name is rejected."""
    res = os.path.join(G, "na-input-result")
    try:
        os.remove(res)
    except FileNotFoundError:
        pass
    with open(os.path.join(G, "na-input"), "w") as fh:
        fh.write("key %d" % vk)
    time.sleep(1.0)


VK_RETURN = 13
VK_SPACE = 32

#: Consecutive unanswered polls after which the game is treated as gone.
#:
#: MEASURED, not assumed: a silent cycle costs the 15-second command timeout plus a 5-second
#: retry, so this is ~2 minutes and not the ~2 minutes a naive reading of "5-second retry" would
#: give. I wrote 24 here first on exactly that arithmetic — it would have been 8 minutes.
#:
#: Long enough to ride out a modal that briefly deafens the channel; short enough that a
#: finished run does not leave a driver polling for hours, which is the failure this exists for.
#:
#: This bound now applies ONLY to a silence with no live heartbeat behind it — see
#: `heartbeat()` and BUSY_LIMIT. It counted every silence until na-xl3, and that conflated a
#: game the driver had killed with a game that was simply busy answering.
SILENT_LIMIT = 6

#: Consecutive unanswered polls with a LIVE, ADVANCING heartbeat after which we stop anyway.
#:
#: A silence with the game's own tick still advancing is the game working, not the game gone:
#: the input result channel is unavailable while a model decision is synchronously in flight.
#: MEASURED on ladder-attempt4 turn 57 — four back-to-back `base.production` decisions of
#: 29.5s / 26.7s / 47.7s / 15.8s (119.7s total) produced a 5-consecutive silence, one short of
#: SILENT_LIMIT, on a game whose heartbeat never missed a beat. Decisions per turn rose 3.40 ->
#: 9.78 over turns 0-48 and the plan mandates 20 bases by turn 80, so that burst gets LONGER;
#: on the old rule the row was going to be stopped, and the log was going to say the game was
#: gone. It was not.
#:
#: Still bounded, because a game that ticks forever without ever answering is also a failure —
#: just a different one, and it deserves its own words.
BUSY_LIMIT = 90


def heartbeat():
    """The game's OWN liveness tick, written by the game thread.

    Returns `(ticks, age_seconds)`, or `(None, None)` when the file is absent or unreadable.

    This is the discriminator the no-result classification was missing. It is written by the
    game, not by us, so it separates the three cases the old message ran together:
    absent -> gone; present but FROZEN -> stalled/deafened; ADVANCING -> busy.
    """
    path = os.path.join(G, "na-input-heartbeat")
    try:
        age = time.time() - os.stat(path).st_mtime
        with open(path) as fh:
            return json.load(fh).get("ticks"), age
    except Exception:
        return None, None


def click(x, y):
    res = os.path.join(G, "na-input-result")
    try:
        os.remove(res)
    except FileNotFoundError:
        pass
    with open(os.path.join(G, "na-input"), "w") as fh:
        fh.write("click %d %d" % (x, y))
    time.sleep(1.5)


def main():
    log = open(OUT, "a", buffering=1)
    log.write("driver start %s\n" % time.strftime("%H:%M:%S"))
    last_turn = None
    stuck = 0
    started_playing = False
    last_dialog = None
    tries = 0
    silent = 0
    busy = 0
    #: Seeded BEFORE the first poll on purpose. Without a baseline the first silent cycle has
    #: nothing to compare against and falls through to "the game is gone" — the exact sentence
    #: this classification exists to stop the driver saying about a live game.
    last_ticks = heartbeat()[0]
    checked_viability = False
    while time.time() < DEADLINE:
        r = cmd("shot")
        if r is None:
            # THREE causes, and the message used to name two — then ran them together anyway.
            # A modal CAN deafen the channel; the game CAN have exited; and — the one that was
            # missing, na-xl3 — the game can simply be BUSY, because the input result channel is
            # unavailable while a model decision is synchronously in flight. All three looked
            # identical from the log, and the driver charged all three to one counter, so a busy
            # game was on a six-cycle path to being declared dead.
            #
            # The game's own heartbeat separates them, and it costs one stat + one read.
            ticks, hb_age = heartbeat()
            evidence = "ticks=%s hb_age=%s" % (
                ticks, "n/a" if hb_age is None else "%.0fs" % hb_age)
            if ticks is not None and last_ticks is not None and ticks != last_ticks:
                busy += 1
                log.write(
                    "%s no-result (%d consecutive) — BUSY: heartbeat advancing (%s, +%s "
                    "ticks). The game is working, not gone; not counted against SILENT_LIMIT.\n"
                    % (time.strftime("%H:%M:%S"), busy, evidence, ticks - last_ticks)
                )
                last_ticks = ticks
                if busy >= BUSY_LIMIT:
                    log.write(
                        "no answer for %d cycles WITH a live advancing heartbeat — the game is "
                        "alive and never answering. That is not a dead game and must not be "
                        "logged as one; stopping so it gets looked at.\n" % busy
                    )
                    break
            else:
                silent += 1
                why = ("no heartbeat file" if ticks is None
                       else "heartbeat FROZEN at %s" % ticks)
                log.write(
                    "%s no-result (%d consecutive) — %s (%s): the game is gone, or a modal has "
                    "deafened the channel\n"
                    % (time.strftime("%H:%M:%S"), silent, why, evidence)
                )
                if ticks is not None:
                    last_ticks = ticks
                if silent >= SILENT_LIMIT:
                    log.write(
                        "no answer for %d cycles and no advancing heartbeat — treating the game "
                        "as gone and stopping. A driver that outlives its game is noise, not "
                        "patience.\n" % silent
                    )
                    break
            time.sleep(5)
            continue
        silent = 0
        busy = 0
        ticks, _ = heartbeat()
        if ticks is not None:
            last_ticks = ticks
        turn = r.get("turn")
        halted = r.get("halted")

        # THE CHECKPOINT. `win_ladder.viability` existed before this line did, and nothing ever
        # called it during a run — it was written to score a results file after the fact. So on
        # seed 1 the bar was cleared to be applied and never applied: our faction sat on ONE base
        # from turn 1, which the bar would have refused at turn 20, and the run played on to turn
        # 123 and elimination. Four hours of wall clock and a paid seed measured a faction that
        # could not expand, which is not the quantity the ladder exists to measure.
        #
        # Applied ONCE, at the checkpoint turn, to all seven factions by the same rule — a check
        # that re-ran later would start refusing seeds for losing, which is a different thing.
        if turn is not None and turn >= VIABLE_BY_TURN and not checked_viability and NO_VIABILITY:
            checked_viability = True
            log.write("%s viability at turn %s: CHECK DISABLED (--no-viability). This run is a "
                      "DIAGNOSTIC and is not a ladder row.\n" % (time.strftime("%H:%M:%S"), turn))
        elif turn is not None and turn >= VIABLE_BY_TURN and not checked_viability:
            checked_viability = True
            st = cmd("game-state")
            if st and st.get("detail"):
                state = parse_state(st["detail"])
                ok, why = viability(state.get("bases") or {}, state.get("player", 0))
                log.write(
                    "%s viability at turn %s: %s%s\n"
                    % (time.strftime("%H:%M:%S"), turn, "ok" if ok else "REFUSED",
                       "" if ok else " — " + why)
                )
                if not ok:
                    log.write(
                        "stopping: playing on measures a faction that cannot expand, not the "
                        "quality of its decisions. Record the seed as unplayable and look at "
                        "the map AND the build queue before drawing the next one.\n"
                    )
                    break
            else:
                # Not a refusal: an unanswered probe is missing evidence, and refusing a seed on
                # missing evidence is how a ladder loses seeds it should have played.
                log.write("%s viability at turn %s: no game-state answer, continuing\n"
                          % (time.strftime("%H:%M:%S"), turn))
        try:
            found, (w, h) = bars(os.path.join(G, "na-screen.bmp"))
        except Exception as exc:  # a half-written BMP is normal, not a fault
            found, w, h = [], 0, 0
            note = "shot unreadable: %s" % exc
        if found:
            # The WIDEST bar is the wrong choice and cost a stall: a base-completed dialog offers
            # "Zoom to Base Control" / "Proceed" as full-width bars above a bottom row of
            # "Don't Show As Popup" | "OK", so widest-wins clicked an option that does not
            # dismiss anything and the turn sat at 102 clicking forever.
            #
            # SMAC's convention is that the dismissing action is on the BOTTOM row, at the RIGHT.
            # So: cluster bars into rows, take the last row, take its right-most bar. On a
            # single-bar notice (NETFLASH) that degenerates to the only bar, which is correct.
            rows = []
            for bar in sorted(found):
                if rows and bar[0] - rows[-1][-1][0] <= 6:
                    rows[-1].append(bar)
                else:
                    rows.append([bar])
            # ESCAPE HATCH. Bottom-right is right for most dialogs and wrong for stacked ones:
            # a Planetary Council vote shows a results panel whose OK is at the TOP above a
            # proposal panel whose VOTE is at the BOTTOM, so bottom-right clicks VOTE forever.
            # Measured: 205 consecutive clicks on the same bar with the turn unmoved.
            #
            # Rather than model every layout, rotate through the rows once clicking has visibly
            # stopped working. Any dialog with a dismissing action is then reached within
            # len(rows) cycles, and the rule needs no knowledge of which dialog it is looking at.
            # Rotation is keyed on THIS DIALOG, not on the turn. `stuck` counts cycles since the
            # turn last moved, so after one long stall the driver stayed in rotate mode forever
            # and wandered into option lists on dialogs where bottom-right would have dismissed
            # them first time — measured on a diplomacy dialog whose seven options it clicked
            # through while an OK sat on the bottom row.
            #
            # That matters beyond speed: the options on a diplomacy dialog are ACTIONS. A driver
            # that clicks them is playing the game, not unblocking it.
            signature = (len(found), rows[-1][0])
            if signature != last_dialog:
                last_dialog, tries = signature, 0
            row = rows[-1] if tries < 3 else rows[(tries // 3) % len(rows)]
            tries += 1
            y, a, b = max(row, key=lambda t: (t[1] + t[2]))
            click((a + b) // 2, y)
            note = "clicked (%d,%d) — %d bar(s), %d row(s)%s, %dx%d" % (
                (a + b) // 2, y, len(found), len(rows),
                " [rotating]" if stuck >= 3 else "", w, h)
        elif w:
            # No dialog and the turn is not moving means the game is waiting on the PLAYER, not
            # on a modal. Enter ends the turn; Space skips a unit that is still awaiting orders
            # and would otherwise refuse to let the turn end. Alternated because which one is
            # needed depends on state we cannot see, and both are harmless when not needed.
            note = "no dialog (%dx%d)" % (w, h)
            if stuck >= 2:
                send_space = bool(stuck % 2) and not NO_SPACE
                key(VK_SPACE if send_space else VK_RETURN)
                note += " — sent %s" % ("SPACE" if send_space else "RETURN")
        stuck = stuck + 1 if turn == last_turn else 0
        last_turn = turn
        log.write(
            "%s turn=%s halted=%s stuck=%d %s\n"
            % (time.strftime("%H:%M:%S"), turn, halted, stuck, note)
        )
        # `halted` is only terminal once the game has actually been PLAYING. It reads 1 at
        # startup while the save is still loading, and quitting on that ends the driver at
        # turn 0 before a single dialog is cleared — measured, on a run that then sat untouched.
        if halted and started_playing:
            log.write("halted after play — stopping\n")
            break
        if turn and turn > 0:
            started_playing = True
        time.sleep(3)
    log.write("driver end %s\n" % time.strftime("%H:%M:%S"))


if __name__ == "__main__":
    main()
