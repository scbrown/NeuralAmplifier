"""Locate a SMAC dialog's action bars in a screenshot, geometry-independently.

Fixed pixel coordinates are the trap here: the game window is 2560x1440 in one run and
1280x1024 in the next, so a coordinate learned from one screenshot clicks outside the window in
the other — silently, because a click that lands nowhere reports ok:true exactly like one that
lands on the button. That is what stalled the first attempt at this run.

The bars are drawn as a two-colour horizontal stripe pattern. Finding runs of that colour finds
the buttons whatever the window size, and returns nothing when no dialog is up rather than
clicking the map.

## Why the width rule is anchored to the dialog and not to the window

The first version kept, per scanline, only the single WIDEST run, and only if it spanned
`0.20 * window_width`. Both halves of that rule are wrong in the same direction, and together
they hid the one button that matters:

  * one run per scanline cannot represent a SIDE-BY-SIDE pair, and SMAC's dismissing action is
    almost always the right half of one — `Don't Show As Popup` | `OK`, `Yes` | `No`;
  * a half-width button on a dialog narrower than the window falls under a window-relative
    floor. Measured on a 2560-wide window: the dialog was 677px, its OK button 335px, the floor
    512px. So OK was structurally undetectable at that geometry.

The consequence is not "one row missed". The driver rotates through whatever rows it is given,
so a detector that returns only the non-dismissing rows makes the escape hatch useless: the win
ladder sat on a PSYCH CHAPLAIN starvation notice at turn 119 for 205 cycles, clicking `Zoom to
Base Control` and `Proceed` alternately, with every click landing correctly on a real button.
Nothing reported a fault at any layer.

So the floor is a fraction of the WIDEST BAR IN THIS IMAGE — the dialog's own width — and every
run within the dialog's x-extent is kept, not just the widest per scanline.

## Why the y-cluster exists

Anchoring on x alone is not enough: the game screen UNDER the dialog draws in the same palette,
and on the measured screenshot a base-management panel 350px lower produced two runs that sat
inside the dialog's x-extent. Those would have become the "bottom row" — and the driver would
have clicked a base-screen control, which is playing the game to unblock itself rather than
dismissing a modal. Bars are therefore clustered vertically and only the cluster containing the
widest bar is returned.
"""
import sys
from PIL import Image

BAR = (103, 91, 181)     # the lighter stripe of a SMAC dialog action bar
TOL = 24
MIN_RUN = 40             # absolute floor, in px — below this it is texture, not a button
MIN_ANCHOR = 0.20        # a real dialog's widest bar spans this much of the WINDOW
#: A narrower widest-bar is still a dialog if it is CORROBORATED by a stack of bars.
#: Both numbers are anchored to measurements, not taste — see the anchor note below.
MIN_ANCHOR_ABS = 240     # px; below this a stripe is not a button at any geometry we ship
ANCHOR_CLUSTER = 4       # bars stacked with the widest before a narrow one counts
MIN_OF_DIALOG = 0.25     # a button spans at least this fraction of the dialog's own width
X_SLACK = 10             # a button may overhang the widest bar by an antialiased pixel or two
ROW_GAP = 6              # scanlines this close belong to the same bar
CLUSTER_GAP = 60         # rows further apart than this are different panels, not one dialog


def close(px, want=BAR, tol=TOL):
    return all(abs(px[i] - want[i]) <= tol for i in range(3))


def _runs(px, w, y, floor):
    """Every horizontal run of bar colour on scanline `y`, as (x_start, x_end)."""
    out = []
    run = start = 0
    for x in range(w):
        if close(px[x, y]):
            if run == 0:
                start = x
            run += 1
        else:
            if run >= floor:
                out.append((start, start + run))
            run = 0
    if run >= floor:
        out.append((start, start + run))
    return out


def bars(path):
    """Every dialog button, as (y, x_start, x_end), top to bottom then left to right."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()

    per_row = {}
    widest = None
    for y in range(h):
        found = _runs(px, w, y, MIN_RUN)
        if found:
            per_row[y] = found
            for a, b in found:
                if widest is None or b - a > widest[2] - widest[1]:
                    widest = (y, a, b)
    # IS there a dialog?
    #
    # This test WAS "a bar spanning a fifth of the WINDOW", and it is the same window-relative
    # mistake the note above describes — fixed there for the per-button floor and left standing
    # here, one layer up, on the gate that decides whether any button is reported at all.
    #
    # MEASURED on the two rows that died (aegis-7iwft): the Planetary Council's VOTE bar is
    # 487px on a 2560px window. The floor was 512px. **It missed by 25 pixels**, so `bars()`
    # returned "no dialog" on a screen showing a full-width VOTE button, the driver blind-
    # alternated SPACE/RETURN 500+ times, and two deep ladder rows died on it. The colour match
    # was never the problem: the pixels at the button's centre are exactly BAR.
    #
    # A window-relative floor cannot work, because the dialog is not window-sized and nothing
    # says a modal on a 4K display must be 800px wide. But it cannot simply be lowered either:
    # it is what stops the base-management panel below the map reading as a dialog.
    #
    # So a wide bar still qualifies ALONE, and a narrower one qualifies when CORROBORATED by
    # the thing that actually distinguishes a dialog from panel chrome — several bars stacked
    # in one tight vertical group. Measured across the fixtures:
    #
    #     council (both dead rows)  15 runs, widest 487px, all inside an 18px band -> dialog
    #     comm officer modal        40 runs, widest 788px                          -> dialog
    #     plain map + HUD            0 runs                                        -> not
    #
    # The true negative has ZERO qualifying runs, so it is not near this boundary at all.
    if widest is None:
        return [], (w, h)
    widest_w = widest[2] - widest[1]
    if widest_w < MIN_ANCHOR * w:
        stacked = sum(
            1
            for y in per_row
            for a, b in per_row[y]
            if abs(y - widest[0]) <= CLUSTER_GAP
            and a >= widest[1] - X_SLACK
            and b <= widest[2] + X_SLACK
        )
        if widest_w < MIN_ANCHOR_ABS or stacked < ANCHOR_CLUSTER:
            return [], (w, h)

    dialog_w = widest[2] - widest[1]
    floor = MIN_OF_DIALOG * dialog_w
    kept = [
        (y, a, b)
        for y in sorted(per_row)
        for a, b in per_row[y]
        if a >= widest[1] - X_SLACK and b <= widest[2] + X_SLACK and (b - a) >= floor
    ]

    # One dialog, not whatever else shares its palette further down the screen. Walk outward
    # from the widest bar one row at a time, so a gap really does terminate the cluster.
    ys = sorted({r[0] for r in kept})
    lo = hi = widest[0]
    for y in [v for v in ys if v < widest[0]][::-1]:
        if lo - y > CLUSTER_GAP:
            break
        lo = y
    for y in [v for v in ys if v > widest[0]]:
        if y - hi > CLUSTER_GAP:
            break
        hi = y
    cluster = [r for r in kept if lo <= r[0] <= hi]

    # Collapse the scanlines of one bar into one entry, keeping the widest reading of it.
    merged = []
    for y, a, b in cluster:
        for i, (my, ma, mb) in enumerate(merged):
            overlap = min(b, mb) - max(a, ma)
            if y - my <= ROW_GAP and overlap > 0.5 * min(b - a, mb - ma):
                if b - a > mb - ma:
                    merged[i] = (my, a, b)
                break
        else:
            merged.append((y, a, b))
    return merged, (w, h)


if __name__ == "__main__":
    found, size = bars(sys.argv[1])
    print(f"window {size[0]}x{size[1]}")
    for y, a, b in found:
        print(f"  bar y={y} x={a}..{b} centre=({(a+b)//2},{y}) width={b - a}")
