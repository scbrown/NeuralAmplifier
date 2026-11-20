# Patch-release version demonstration

The same `neural_amplifier.__version__` read, run in a fresh isolated
environment, changes from the published v0.3.1 wheel to the v0.3.2 candidate:

    Published v0.3.1 wheel reports __version__ = 0.1.0
    Candidate v0.3.2 wheel reports __version__ = 0.3.2
    Lock, wheel METADATA and sdist PKG-INFO: 0.3.2. Candidate artifacts only; no publication.

The v0.3.1 wheel reports the stale hardcoded value that #12 fixed. The candidate
reports its real distribution version. This demonstrates packaging, not a model
run or installation.

Download the published v0.3.1 wheel into one directory and set
`RELEASE_DEMO_OLD` to it. Build the candidate with the release workflow's
`uv build` command and set `RELEASE_DEMO_DIST` to that output. Then run from
the repository root (requires `uv`):

    just --command python3 release-notes/probes/v0.3.2/reproduce.py

Publication, downloaded hosted assets and natural release-health census
remain separate acceptance checks.
