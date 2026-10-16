"""Recompute the context control from committed provider usage, without a model."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
baseline = json.loads((root / "baseline.json").read_text())
isolated = json.loads((root / "isolated.json").read_text())
control = json.loads((root / "integrated-control.json").read_text())
for key in ("system_sha256", "payload_sha256", "model"):
    assert baseline[key] == isolated[key], key
for sample in (baseline, isolated):
    assert sample["valid_action"] and sample["choice"] == "a2"
    assert sample["returncode"] == 0 and sample["is_error"] is False

def total(usage):
    return sum(usage[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))

before, after = total(baseline["usage"]), total(isolated["usage"])
assert control["orders_validated"] and control["tier"] == "llm" and not control["degraded"]
assert after < before
print(f"one paired control: total input {before} -> {after} ({100 * (1 - after / before):.1f}% lower)")
print("integrated Orchestrator: valid LLM orders; no strategy or fleet savings conclusion")
