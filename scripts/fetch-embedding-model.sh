#!/usr/bin/env bash
# Fetch all-MiniLM-L6-v2 so Quipu can embed — na-6td.
#
# Exact-match SPARQL grounding does not need this and works today; `quipu_context` and semantic
# search do. `QuipuRetriever` posts to /query, never /search, so nothing in the decision path
# depends on it (measured, na-6td comment).
#
# WHAT IS ACTUALLY BLOCKED, measured 2026-08-16 rather than assumed. The bead says
# "huggingface.co is NOT on the Trusted allowlist". That is wrong, and following it would waste
# somebody's afternoon:
#
#   huggingface.co          200  — the API and small files, including tokenizer.json, download fine
#   us.aws.cdn.hf.co        403 on CONNECT — where the LFS/xet weights actually live
#
# So `tokenizer.json` (466 KB) fetches and `onnx/model.onnx` (~90 MB) does not. Adding
# `huggingface.co` to a custom allowlist would NOT fix it; the host to allow is the xet CDN the
# 302 points at. That indirection is why this script reports the redirect target on failure
# rather than just the URL it asked for.
#
# The model is never committed. It is ~90 MB of weights, the pre-commit large-file check would
# refuse it, and a vendored binary in a source tree is a licence and provenance question nobody
# wants to answer later.
set -euo pipefail

dest="${1:-models/all-MiniLM-L6-v2}"
base="https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main"

mkdir -p "$dest/onnx"

fetch() {
    local path="$1" out="$2"
    printf '%-18s ' "$(basename "$path")"
    if curl -sSLf -o "$out" --max-time 600 "$base/$path"; then
        printf 'ok (%s bytes)\n' "$(wc -c <"$out")"
        return 0
    fi
    printf 'FAILED\n'
    # Name the host that actually refused, not the one we asked. The 302 to a CDN is the whole
    # reason this fails in a way the bead's own remedy would not fix.
    local target
    target="$(curl -sSI --max-time 30 "$base/$path" 2>/dev/null | awk '/^location:/{print $2}' | tail -1)"
    if [ -n "$target" ]; then
        printf '  redirects to: %s\n' "$(printf '%s' "$target" | cut -d/ -f1-3)"
        printf '  that host must be reachable — allowing huggingface.co alone is not enough.\n'
    fi
    return 1
}

ok=0
fetch "tokenizer.json" "$dest/tokenizer.json" || ok=1
fetch "onnx/model.onnx" "$dest/onnx/model.onnx" || ok=1

if [ "$ok" -ne 0 ]; then
    cat >&2 <<EOF

Not fetched. Quipu keeps working for exact-match SPARQL grounding, which is what the decision
path uses — this only costs quipu_context and semantic search.

To wire it up once the files are present, add to the Quipu config:

    [embedding]
    auto_embed = true
    model_path = "$dest/onnx/model.onnx"
    tokenizer_path = "$dest/tokenizer.json"
EOF
    exit 1
fi

cat <<EOF

Fetched. Add to the Quipu config:

    [embedding]
    auto_embed = true
    model_path = "$dest/onnx/model.onnx"
    tokenizer_path = "$dest/tokenizer.json"
EOF
