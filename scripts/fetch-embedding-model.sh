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
# THREE ALTERNATIVES MEASURED AND RULED OUT, 2026-08-16 — so nobody spends an afternoon on them:
#
#   Xenova/all-MiniLM-L6-v2  onnx/model_quantized.onnx   same CDN, same 403 (a smaller file does
#                                                        not avoid the redirect)
#   minishlab/potion-base-8M model.safetensors           same CDN, same 403 (a different model
#                                                        does not either)
#   sentence-transformers/…  config.json                 200 — because it is small and non-LFS
#
# The split is LFS, not size or repo: anything stored in LFS/xet redirects to the CDN and is
# refused; anything served directly by huggingface.co arrives. Fetching a different model or a
# quantised variant does not change which side of that line the weights fall on.
#
# PyPI IS reachable (files.pythonhosted.org 200), so a package that BUNDLED weights in its wheel
# would work. The usual candidates do not — they download from HF at runtime, which lands back
# here.
#
# The model is never committed. It is ~90 MB of weights, the pre-commit large-file check would
# refuse it, and a vendored binary in a source tree is a licence and provenance question nobody
# wants to answer later.
set -euo pipefail

# `--check` diagnoses reachability without downloading anything, so the answer to "is this still
# blocked?" costs seconds rather than a 90 MB timeout.
if [ "${1:-}" = "--check" ]; then
    printf '%-26s ' "huggingface.co"
    curl -sS -o /dev/null -w '%{http_code}\n' --max-time 20 https://huggingface.co/ || echo unreachable
    printf '%-26s ' "us.aws.cdn.hf.co (weights)"
    curl -sS -o /dev/null -w '%{http_code}\n' --max-time 20 https://us.aws.cdn.hf.co/ || echo "refused"
    echo
    echo "Weights live on the second host. If it is not reachable, this script fetches the"
    echo "tokenizer and stops — allowing huggingface.co alone does not help."
    exit 0
fi

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

# Fetch what is reachable even if the weights are not. A partial fetch is worth keeping: the
# tokenizer is a real 466 KB artefact, it does not change, and re-running later then needs only
# the one blocked file rather than starting over.
ok=0
fetch "tokenizer.json" "$dest/tokenizer.json" || ok=1
fetch "config.json" "$dest/config.json" || ok=1
fetch "onnx/model.onnx" "$dest/onnx/model.onnx" || ok=1

if [ "$ok" -ne 0 ]; then
    cat >&2 <<EOF

Incomplete. Whatever downloaded is kept in $dest — re-running fetches only what is missing.

Quipu keeps working for exact-match SPARQL grounding, which is what the decision path actually
uses (QuipuRetriever posts to /query, never /search). This costs quipu_context and semantic
search, and nothing else.

Diagnose in seconds with:  scripts/fetch-embedding-model.sh --check

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
