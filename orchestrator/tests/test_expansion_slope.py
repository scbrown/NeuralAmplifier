from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def module():
    spec = importlib.util.spec_from_file_location(
        "expansion_slope", REPO / "scripts/expansion_slope.py"
    )
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def write(path: Path, *, compound_difficulty: str = "talent", missing: bool = False) -> Path:
    census = {"55": {"7": 7}, "91": {"7": 10}, "140": {"7": 12}}
    improved = {"55": {"7": 8}, "91": {"7": 20}, "140": {"7": 45}}
    if missing:
        improved.pop("91")
    rows = [
        {
            "seed": 1,
            "arm": "baseline",
            "fairness": {"slot": "human", "difficulty": "talent", "handicaps": []},
            "census": census,
        },
        {
            "seed": 1,
            "arm": "compound",
            "fairness": {
                "slot": "human",
                "difficulty": compound_difficulty,
                "handicaps": [],
            },
            "census": improved,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def run(tmp_path: Path, monkeypatch, capsys, **kwargs) -> tuple[int, str]:
    path = write(tmp_path / "results.jsonl", **kwargs)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "expansion_slope",
            str(path),
            "--seed",
            "1",
            "--baseline",
            "baseline",
            "--compound",
            "compound",
            "--faction",
            "7",
            "--checkpoints",
            "55,91,140",
        ],
    )
    rc = module().main()
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


def test_reports_interval_slopes_without_a_verdict(tmp_path, monkeypatch, capsys) -> None:
    rc, out = run(tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert "55-91" in out and "0.333" in out
    assert "No verdict" in out


def test_refuses_different_fairness(tmp_path, monkeypatch, capsys) -> None:
    rc, out = run(tmp_path, monkeypatch, capsys, compound_difficulty="thinker")
    assert rc == 1 and "different fairness" in out


def test_refuses_a_missing_checkpoint(tmp_path, monkeypatch, capsys) -> None:
    rc, out = run(tmp_path, monkeypatch, capsys, missing=True)
    assert rc == 1 and "no numeric census at turn 91" in out
