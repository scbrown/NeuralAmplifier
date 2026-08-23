"""Locate a SMAC dialog's action bar in a screenshot, geometry-independently.

Fixed pixel coordinates are the trap here: the game window is 2560x1440 in one run and
1280x1024 in the next, so a coordinate learned from one screenshot clicks outside the window in
the other — silently, because a click that lands nowhere reports ok:true exactly like one that
lands on the button. That is what stalled the first attempt at this run.

The bars are drawn as a two-colour horizontal stripe pattern. Finding the widest run of that
colour finds the button whatever the window size, and returns None when no dialog is up rather
than clicking the map.
"""
import sys
from PIL import Image

BAR = (103, 91, 181)     # the lighter stripe of a SMAC dialog action bar
TOL = 24
MIN_FRACTION = 0.20      # a real bar spans a good part of the dialog


def close(px, want=BAR, tol=TOL):
    return all(abs(px[i] - want[i]) <= tol for i in range(3))


def bars(path):
    """Every wide bar, as (y, x_start, x_end), top to bottom."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    out = []
    for y in range(h):  # every row: the stripes alternate, so stepping by 2 can miss the bar entirely
        run = best = 0
        start = best_start = 0
        for x in range(w):
            if close(px[x, y]):
                if run == 0:
                    start = x
                run += 1
                if run > best:
                    best, best_start = run, start
            else:
                run = 0
        if best >= w * MIN_FRACTION:
            out.append((y, best_start, best_start + best))
    # collapse adjacent scanlines of the same bar
    merged = []
    for y, a, b in out:
        if merged and y - merged[-1][0] <= 6 and abs(a - merged[-1][1]) < 40:
            continue
        merged.append((y, a, b))
    return merged, (w, h)


if __name__ == "__main__":
    found, size = bars(sys.argv[1])
    print(f"window {size[0]}x{size[1]}")
    for y, a, b in found:
        print(f"  bar y={y} x={a}..{b} centre=({(a+b)//2},{y})")
