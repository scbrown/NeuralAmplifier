"""The installed release identifies itself without starting a service or game."""

from importlib.metadata import version

import pytest

import neural_amplifier
from neural_amplifier.cli import main


def test_version_reports_installed_distribution_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    output = capsys.readouterr()
    assert output.out == f"neural-amplifier {version('neural-amplifier')}\n"
    assert output.err == ""


def test_package_version_matches_installed_distribution() -> None:
    assert neural_amplifier.__version__ == version("neural-amplifier")


def test_version_does_not_make_subcommand_optional(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert "command" in capsys.readouterr().err
