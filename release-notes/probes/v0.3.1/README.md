# Patch-release packaging demonstration

The same package-version read changes from published v0.3.0 to the candidate
v0.3.1. The actual candidate wheel and source distribution both report v0.3.1,
and the lock agrees. This demonstrates packaging, not a model run or installation.

Base: v0.3.0 (e67cea795c564b434f70d6ccb4095e738394d8be). Candidate source:
main d7d507c55b912cdfbd2dcd58fd649eef46d854ea plus the three release-preparation
files in this PR. The recording is retained here with the release preparation.

Build distributions with the existing release workflow's uv build command,
set RELEASE_DEMO_DIST to their directory, then reproduce from the repository root:

    just --command python3 release-notes/probes/v0.3.1/reproduce.py

Download terminal.log and timing.log together and replay with util-linux:

    scriptreplay --log-timing timing.log --log-out terminal.log --divisor 10

Playback requires those downloaded files and a terminal; no inline animation is
claimed. Publication, downloaded hosted assets and natural release-health census
remain separate acceptance checks.
