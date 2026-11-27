"""`[seats]` — the seat table and the `llm_factions` bitmask derived from it (aegis-8fye.3.1).

Strict at load: a seat that names something nobody built must refuse to start rather than route
nothing while looking configured. And absent means legacy: no table, nothing changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neural_amplifier.config import ConfigError, Seat, llm_factions, load, main


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "na.toml"
    path.write_text(body)
    return path


TWO_SEATS = """
[seats.1]
principal = "steve"
strategist = "agent"
reflex = "claude"
model = "claude-haiku-4-5"
budget_usd = 2.5

[seats.3]
principal = "steve"
reflex = "scripted"
"""


def test_a_valid_two_seat_table_parses(tmp_path: Path) -> None:
    cfg = load(write(tmp_path, TWO_SEATS))

    assert cfg.seats == {
        1: Seat(1, "steve", "claude", "agent", "claude-haiku-4-5", 2.5),
        3: Seat(3, "steve", "scripted"),
    }
    assert cfg.seats[3].strategist == "none"
    assert cfg.seats[3].model is None and cfg.seats[3].budget_usd is None


def test_seats_1_and_3_derive_llm_factions_10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, TWO_SEATS)
    assert llm_factions(load(path).seats) == 10

    monkeypatch.setenv("NA_CONFIG", str(path))
    assert main(["llm-factions"]) == 0
    assert capsys.readouterr().out == "10\n"


def test_no_seats_table_is_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty table means legacy: Config.seats == {} and the CLI prints nothing, which is what
    tells play-thinker.sh to leave thinker.ini alone."""
    assert load(write(tmp_path, '[brain]\nkind = "scripted"\n')).seats == {}
    assert load(write(tmp_path, "[seats]\n")).seats == {}
    assert load(tmp_path / "absent.toml").seats == {}

    monkeypatch.setenv("NA_CONFIG", str(write(tmp_path, "[seats]\n")))
    assert main(["llm-factions"]) == 0
    assert capsys.readouterr().out == ""


def test_a_config_that_does_not_load_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NA_CONFIG", str(write(tmp_path, "[seats.9]\nprincipal='x'\n")))
    assert main(["llm-factions"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "1..7" in captured.err


def test_an_unknown_key_refuses(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown key.*colour"):
        load(write(tmp_path, '[seats.1]\nprincipal = "p"\nreflex = "claude"\ncolour = "red"\n'))


@pytest.mark.parametrize("fid", ["0", "8", "x", "-1"])
def test_a_bad_faction_id_refuses(tmp_path: Path, fid: str) -> None:
    with pytest.raises(ConfigError, match=r"1\.\.7"):
        load(write(tmp_path, f'[seats."{fid}"]\nprincipal = "p"\nreflex = "claude"\n'))


def test_a_duplicate_seat_refuses(tmp_path: Path) -> None:
    """TOML itself rejects a repeated table header; the duplicate that reaches us is two
    spellings of one faction id."""
    body = '[seats.1]\nprincipal = "p"\nreflex = "claude"\n[seats."01"]\nprincipal = "q"\nreflex = "agent"\n'
    with pytest.raises(ConfigError, match="seated twice"):
        load(write(tmp_path, body))


@pytest.mark.parametrize(
    ("reflex", "slice_id"),
    [("skills", "8fye.5"), ("policy", "8fye.6"), ("copilot", "8fye.4")],
)
def test_an_unbuilt_reflex_refuses_and_names_its_slice(
    tmp_path: Path, reflex: str, slice_id: str
) -> None:
    with pytest.raises(ConfigError, match=rf"not built yet.*aegis-{slice_id}"):
        load(write(tmp_path, f'[seats.2]\nprincipal = "p"\nreflex = "{reflex}"\n'))


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ('reflex = "claude"\n', "principal is required"),
        ('principal = "p"\n', "reflex is required"),
        ('principal = "p"\nreflex = "gpt"\n', "reflex must be one of"),
        ('principal = "p"\nreflex = "claude"\nstrategist = "bot"\n', "strategist must be"),
        ('principal = "p"\nreflex = "claude"\nbudget_usd = -1\n', "budget_usd"),
        ('principal = "p"\nreflex = "claude"\nmodel = 3\n', "model must be"),
    ],
)
def test_a_malformed_seat_refuses(tmp_path: Path, body: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        load(write(tmp_path, "[seats.4]\n" + body))
