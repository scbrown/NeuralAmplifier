"""`na.toml` — the general config, and the precedence that makes it safe.

One file instead of ten `NA_*` variables, so a run is reproducible from something you can read
and commit. The property worth testing is not that keys parse — it is the ordering:

**env > file > default.** The file is what a run *is*; a variable is how you override one thing
for one run without editing the tree, which is what CI and the cloud setup script do. Reversed,
a checked-in file would silently override the harness, and a run would not be what the harness
asked for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neural_amplifier.config import ConfigError, load


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "na.toml"
    path.write_text(body)
    return path


def test_a_missing_file_is_every_previous_default(tmp_path: Path) -> None:
    """Absent is not an error. Nothing configured means the behaviour this had before the file
    existed — scripted brain, no Quipu, guard on, everything decided."""
    cfg = load(tmp_path / "absent.toml")

    assert cfg.source is None
    assert cfg.brain.kind == "scripted"
    assert cfg.knowledge.quipu_url is None
    assert cfg.knowledge.guard is True
    assert cfg.run.otel is False
    assert cfg.surfaces.allows("base.production") is True


def test_the_file_is_read(tmp_path: Path) -> None:
    cfg = load(
        write(
            tmp_path,
            '[brain]\nkind = "claude"\nmodel = "claude-haiku-4-5"\n'
            '[knowledge]\nquipu_url = "http://q:3030"\ntoken_budget = 900\nguard = false\n'
            '[run]\notel = true\ndecision_log = "d.jsonl"\n',
        )
    )
    assert cfg.brain.kind == "claude"
    assert cfg.brain.model == "claude-haiku-4-5"
    assert cfg.knowledge.quipu_url == "http://q:3030"
    assert cfg.knowledge.token_budget == 900
    assert cfg.knowledge.guard is False
    assert cfg.run.otel is True
    assert cfg.run.decision_log == "d.jsonl"


def test_environment_beats_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordering the whole module turns on."""
    path = write(tmp_path, '[brain]\nkind = "scripted"\n[knowledge]\nquipu_url = "http://a"\n')
    monkeypatch.setenv("NA_BRAIN", "claude")
    monkeypatch.setenv("NA_QUIPU_URL", "http://b")

    cfg = load(path)
    assert cfg.brain.kind == "claude"
    assert cfg.knowledge.quipu_url == "http://b"


def test_a_false_flag_in_the_environment_turns_something_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bool("0")` is True, so a naive read of these variables does the opposite of what the
    person typed — `NA_HANK_GUARD=0` would have *enabled* the guard."""
    path = write(tmp_path, "[knowledge]\nguard = true\n")
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("NA_HANK_GUARD", off)
        assert load(path).knowledge.guard is False, off
    monkeypatch.setenv("NA_HANK_GUARD", "1")
    assert load(path).knowledge.guard is True


def test_malformed_toml_refuses_to_load(tmp_path: Path) -> None:
    """Fail the process, not one turn at a time in a running game."""
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(write(tmp_path, "[brain\nkind = "))


def test_a_non_numeric_override_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NA_TOKEN_BUDGET", "lots")
    with pytest.raises(ConfigError, match="whole number"):
        load(write(tmp_path, ""))


def test_a_section_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"\[brain\] must be a table"):
        load(write(tmp_path, 'brain = "claude"\n'))


def test_the_shipped_config_loads() -> None:
    """The one in the repo root. A config nobody can load is worse than no config, and this is
    the file every `just` recipe and the service pick up by default."""
    cfg = load()
    assert cfg.source is not None and cfg.source.name == "na.toml"
    assert cfg.surfaces.allows("base.production") is True
    assert cfg.surfaces.allows("base.abandon") is False
