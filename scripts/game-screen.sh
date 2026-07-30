#!/usr/bin/env bash
#
# See and drive the running game's window.
#
#   scripts/game-screen.sh shot [out.png]   # capture the game window
#   scripts/game-screen.sh click X Y        # click at window-relative coords
#   scripts/game-screen.sh key <keys>       # send keystrokes (xdotool syntax)
#   scripts/game-screen.sh info             # window id and geometry
#
# Why this is not `scrot` or `import -window root`:
#
# Under XWayland every X client is composited separately, so capturing the ROOT
# window returns solid black — no error, just a useless image. You have to capture
# the game's own window by id. That black PNG is a genuinely misleading result: it
# looks like the game failed to render when in fact it is drawing fine.
#
# Coordinates here are WINDOW-relative, which is what you want: the window moves,
# and the game's own layout does not. xdotool's --window flag handles the mapping.
#
set -euo pipefail

log()  { printf '\033[1m== %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$1" >&2; }
die() { printf '\033[31mxx %s\033[0m\n' "$1" >&2; exit 1; }

: "${DISPLAY:=:0}"
export DISPLAY

command -v xdotool >/dev/null || die "xdotool missing — run scripts/setup-host.sh"

# The game window, not the "Wine Desktop" container that holds it. Matching on the
# game's own title avoids grabbing the wrapper, whose geometry is a few pixels off
# and whose coordinate origin is therefore wrong.
find_window() {
    local id
    id="$(xdotool search --name "Alpha Centauri" 2>/dev/null | while read -r w; do
        case "$(xdotool getwindowname "$w" 2>/dev/null)" in
            *"Alpha Centauri"*) echo "$w"; break ;;
        esac
    done)"
    [ -n "$id" ] || return 1
    printf '%s' "$id"
}

WIN="$(find_window)" || die "no game window found — is the game running?"

cmd="${1:-shot}"
case "$cmd" in
    info)
        xdotool getwindowgeometry --shell "$WIN"
        printf 'WINDOW_NAME=%s\n' "$(xdotool getwindowname "$WIN")"
        ;;
    shot)
        out="${2:-game.png}"
        command -v import >/dev/null || die "imagemagick missing — run scripts/setup-host.sh"
        import -window "$WIN" "$out"
        # A downscaled copy alongside it: the native capture is 2560x1440, which is
        # more than any vision budget needs and slow to move around.
        if command -v convert >/dev/null; then
            convert "$out" -resize 1400x "${out%.png}_small.png" 2>/dev/null || true
        fi
        log "captured $out ($(identify -format '%wx%h' "$out" 2>/dev/null || echo '?'))"
        ;;
    click)
        # UNVERIFIED against this game. Both message injection (--window) and XTEST
        # with warped absolute coordinates were tried against the main menu and
        # neither registered: terranx.exe reads the mouse through DirectInput rather
        # than the window message queue, and it is running inside a Wine virtual
        # desktop, so the coordinate space it believes in is not simply the X window's.
        #
        # Left in place because it is the right shape for the eventual fix, and
        # because a stub that lies about working is worse than one that warns.
        [ $# -ge 3 ] || die "usage: click X Y  (window-relative)"
        eval "$(xdotool getwindowgeometry --shell "$WIN")"
        xdotool windowactivate --sync "$WIN" 2>/dev/null || true
        xdotool mousemove $(( X + $2 )) $(( Y + $3 )) sleep 0.3 click 1
        log "clicked window-relative ($2,$3) -> screen ($(( X + $2 )),$(( Y + $3 )))"
        warn "input injection is NOT verified for this game — check with 'shot'"
        ;;
    key)
        shift
        [ $# -ge 1 ] || die "usage: key <keys>"
        xdotool windowactivate --sync "$WIN" 2>/dev/null || true
        xdotool key --window "$WIN" "$@"
        log "sent keys: $*"
        ;;
    *)
        die "unknown: $cmd. Try: shot click key info"
        ;;
esac
