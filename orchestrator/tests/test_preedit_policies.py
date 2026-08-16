"""The dev-time guardrail's two representations, held to each other.

`.bobbin/config.toml` is what `yupana hook pre-edit` reads today. `policies/preedit.ttl` is
where those rules are meant to live — in Quipu, as `aegis:Policy` nodes, so a rule that can
block an edit has provenance and an owner rather than being a file somebody edited (Hank role
(b), na-kdw).

Two representations of one rule set is a drift generator, and the drift is silent in the
direction that matters: the TOML is what runs, so a TTL that falls behind describes governance
nobody is subject to while looking exactly like governance. These tests are what makes
"canonical source" a fact instead of an intention — every field is compared, and a rule present
on one side and absent on the other fails.

They are also a **projectability** check. yupana's structural projection
(`src/project_queries.rs`) silently returns nothing for a policy missing `boundary "action"`,
`aegis:tier "tree-sitter"` or `aegis:language` — not an error, an absence. A guard with no
rules cannot be told from a guard with nothing to complain about, so the requirements are
asserted here where they are visible.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDFS

AEGIS = Namespace("http://aegis.gastown.local/ontology/")

REPO = Path(__file__).resolve().parents[2]
TTL = REPO / "policies" / "preedit.ttl"
TOML = REPO / ".bobbin" / "config.toml"


def _toml_rules() -> dict[str, dict]:
    data = tomllib.loads(TOML.read_text())
    rules = data["yupana"]["policy"]["rules"]
    return {r["name"]: r for r in rules}


def _ttl_policies() -> dict[str, dict]:
    """Every `aegis:Policy` in the TTL, flattened to the shape a `Rule` has.

    Deliberately reads through rdflib rather than pattern-matching the file: the whole claim
    is that this file is valid governance a SPARQL query can project, and a regex over Turtle
    would pass on a file no triplestore accepts.
    """
    g = Graph()
    g.parse(TTL, format="turtle")

    out: dict[str, dict] = {}
    for policy in g.subjects(URIRef(AEGIS.boundary), None):
        sel = g.value(policy, AEGIS.selector)
        pred = g.value(policy, AEGIS.predicate)
        name = str(g.value(policy, RDFS.label))
        out[name] = {
            "name": name,
            "boundary": str(g.value(policy, AEGIS.boundary)),
            "effect": str(g.value(policy, AEGIS.effect)),
            "claim": str(g.value(policy, AEGIS.claim)),
            "applies_to": sorted(str(o) for o in g.objects(policy, AEGIS.appliesTo)),
            "language": str(g.value(sel, AEGIS.language)),
            "tier": str(g.value(sel, AEGIS.tier)),
            "query": str(g.value(sel, AEGIS.evidenceSource)),
            "match_type": str(g.value(pred, AEGIS.matchType)),
            "pattern": str(g.value(pred, AEGIS.evidenceSource)),
            "gate": str(g.value(pred, AEGIS.gate)) if g.value(pred, AEGIS.gate) else None,
        }
    return out


def test_the_turtle_is_parseable_governance() -> None:
    """If it does not parse, nothing downstream is true — including every other test here."""
    assert _ttl_policies(), f"no aegis:Policy nodes in {TTL}"


def test_both_representations_hold_the_same_rules() -> None:
    """A rule on one side only is the drift this pair exists to prevent."""
    assert set(_ttl_policies()) == set(_toml_rules())


@pytest.mark.parametrize("field", ["language", "query", "pattern", "match_type", "gate"])
def test_every_projected_field_matches_the_evaluated_one(field: str) -> None:
    """Field-for-field, because a rule that differs in its regex is a *different rule*.

    `query` is `aegis:Selector.evidenceSource`, `pattern` is `aegis:Predicate.evidenceSource`
    and `match_type` is `aegis:matchType` — the TOML's field names were chosen to mirror
    those atoms 1:1 precisely so this comparison is a rename and not a translation.
    """
    ttl, toml = _ttl_policies(), _toml_rules()
    for name, rule in toml.items():
        assert ttl[name][field] == rule.get(field), f"{name}.{field}"


def test_the_path_scope_survives_into_the_governed_form() -> None:
    """The scope is the field most likely to be lost, and losing it fails OPEN-ended.

    A rule whose scope is dropped does not stop working — it starts applying everywhere, which
    reads as a rule that works. yupana projected `applies_to` as empty until scbrown/yupana
    35ebc90 for exactly this reason and nothing noticed. `no-engine-specifics-in-the-
    orchestrator` is scoped to a single file and is the one that would have hurt.
    """
    ttl, toml = _ttl_policies(), _toml_rules()
    for name, rule in toml.items():
        assert ttl[name]["applies_to"] == sorted(rule.get("paths", [])), name
    assert ttl["no-engine-specifics-in-the-orchestrator"]["applies_to"] == [
        "orchestrator/src/neural_amplifier/orchestrator.py"
    ], "the single-file scope is the whole point of this rule"


def test_every_policy_is_actually_projectable() -> None:
    """The three requirements yupana's POLICY_QUERY imposes, each of which fails as SILENCE.

    A policy missing any of these does not error anywhere. It returns no rows, the guard has
    one less rule, and a guard with fewer rules is indistinguishable from a codebase with
    fewer violations.

    `boundary` is the one worth staring at: `policies/board.ttl` correctly uses `"order"`,
    yupana's extension for the game pre-apply seam. Copying that value here would be the
    natural mistake and would silently project nothing.
    """
    for name, policy in _ttl_policies().items():
        assert policy["boundary"] == "action", f"{name}: 'order' is the board plane's boundary"
        assert policy["tier"] == "tree-sitter", f"{name}: no structural evidence to project"
        assert policy["language"], f"{name}: a rule only evaluates files of its own language"


def test_the_governed_effects_are_advisory() -> None:
    """These rules match a shape and cannot tell intent, so they warn.

    Pinned rather than left to judgement because `effect` is projected from the graph and
    overrides the local `mode` — a `deny` here would block edits on a heuristic regardless of
    how conservatively yupana is configured, and a guard that does that gets switched off.
    """
    for name, policy in _ttl_policies().items():
        assert policy["effect"] == "warn", name


def test_the_namespace_is_the_one_yupana_queries() -> None:
    """Not the board plane's namespace, and this is not tidiable.

    `orchestrator/src/neural_amplifier/yupana.py` owns the board query and names
    `https://aegis.local/ontology/`. yupana's own `src/project_queries.rs` names
    `http://aegis.gastown.local/ontology/` and is not ours to change from here. A file written
    against the wrong one parses fine, projects zero rows, and reports nothing.

    Asserted over the PARSED graph, not the file text. The header comment here explains the
    split and necessarily names both namespaces, so a substring check over the source would
    fail on its own documentation — and would pass on a file that merely mentioned the right
    prefix without using it.
    """
    g = Graph()
    g.parse(TTL, format="turtle")

    bound = dict(g.namespaces())
    assert bound["aegis"] == URIRef("http://aegis.gastown.local/ontology/")

    predicates = {str(p) for p in g.predicates(None, None)}
    governance = {p for p in predicates if "aegis" in p}
    assert governance, "no governance predicates at all"
    for p in governance:
        assert p.startswith("http://aegis.gastown.local/ontology/"), p
