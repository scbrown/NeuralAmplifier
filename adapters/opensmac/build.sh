#!/usr/bin/env bash
# Build the patched OpenSMAC headless runner for the NA eval adapter.
#
#   build.sh WORK_DIR
#
# Clones richardpenman/smac into WORK_DIR/smac (the only networked step, outside the sandbox),
# checks out the pinned commit, applies patches/*.patch and builds opensmac_headless inside the
# sandbox (sandbox.sh: no network, tmpfs HOME). The result is WORK_DIR/smac/build/opensmac_headless.
# Nothing is pushed anywhere; upstream is never written.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
pin=$(cat "$here/SMAC_PIN")
work=$(mkdir -p "$1" && realpath "$1")
src="$work/smac"

if [[ ! -d $src/.git ]]; then
  git clone --quiet https://github.com/richardpenman/smac.git "$src"
fi
git -C "$src" fetch --quiet origin "$pin" 2>/dev/null || true
git -C "$src" checkout --quiet --force "$pin"
git -C "$src" clean --quiet -fdx -e build
[[ $(git -C "$src" rev-parse HEAD) == "$pin" ]] || { echo "smac is not at $pin" >&2; exit 1; }
for p in "$here"/patches/*.patch; do
  git -C "$src" apply --whitespace=nowarn "$p"
done

"$here/sandbox.sh" "$work" - -- bash -c '
  set -euo pipefail
  cmake -S /tmp/w/smac -B /tmp/w/smac/build -DCMAKE_BUILD_TYPE=RelWithDebInfo >/tmp/w/cmake-configure.log
  cmake --build /tmp/w/smac/build --target opensmac_headless -j"$(nproc)" >/tmp/w/cmake-build.log
'
echo "built $src/build/opensmac_headless at $pin with $(ls "$here"/patches/*.patch | wc -l) patch(es)"
