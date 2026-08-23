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

G = sys.argv[1]
OUT = sys.argv[2]
DEADLINE = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 5400)


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
    while time.time() < DEADLINE:
        r = cmd("shot")
        if r is None:
            log.write("%s no-result (a modal can deafen the channel)\n" % time.strftime("%H:%M:%S"))
            time.sleep(5)
            continue
        turn = r.get("turn")
        halted = r.get("halted")
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
            row = rows[-1] if stuck < 3 else rows[(stuck // 3) % len(rows)]
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
                key(VK_SPACE if stuck % 2 else VK_RETURN)
                note += " — sent %s" % ("SPACE" if stuck % 2 else "RETURN")
        stuck = stuck + 1 if turn == last_turn else 0
        last_turn = turn
        log.write(
            "%s turn=%s halted=%s stuck=%d %s\n"
            % (time.strftime("%H:%M:%S"), turn, halted, stuck, note)
        )
        if halted:
            log.write("halted — stopping\n")
            break
        time.sleep(3)
    log.write("driver end %s\n" % time.strftime("%H:%M:%S"))


if __name__ == "__main__":
    main()
