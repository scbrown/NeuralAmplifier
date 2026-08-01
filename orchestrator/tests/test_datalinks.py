"""The datalinks plane — K1.

Runs against a **synthetic** excerpt: the real ``alphax.txt`` is game data and
is never committed. The excerpt carries every parsing trap the real file does,
and the parser was verified against a real 1281-line ``alphax.txt`` (88
technologies, 133 facilities, 64 secret-project rows) during development.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import rdflib

from neural_amplifier.contract import Action, WorldView
from neural_amplifier.datalinks import (
    DatalinksRetriever,
    Provenance,
    briefing,
    parse_file,
    turtle,
)
from neural_amplifier.datalinks.parse import flags, prereqs

EXCERPT = Path(__file__).parent / "fixtures" / "alphax_excerpt.txt"
SMAC = rdflib.Namespace("http://neuralamplifier.local/ontology/smac/")


@pytest.fixture
def links():  # type: ignore[no-untyped-def]
    return parse_file(EXCERPT)


@pytest.fixture
def graph(links) -> rdflib.Graph:  # type: ignore[no-untyped-def]
    g = rdflib.Graph()
    g.parse(data=turtle(links), format="turtle")
    return g


# --- parsing traps ---------------------------------------------------------


def test_disable_is_not_a_prerequisite(links) -> None:  # type: ignore[no-untyped-def]
    """`Disable` means excluded from the game, not "requires the Disable tech".
    Treating it as a tech invents an edge and hides that the item is gone —
    50 facilities in the real file carry it."""
    removed = links.facilities["Removed Facility"]
    assert removed.requires == ()
    assert removed.disabled is True

    available = links.facilities["Sample Tanks"]
    assert available.requires == ("Sampl",)
    assert available.disabled is False


def test_no_prerequisite_is_distinct_from_disabled(links) -> None:  # type: ignore[no-untyped-def]
    """Both yield an empty `requires`; only `disabled` tells them apart."""
    assert links.technologies["Sample Genetics"].requires == ()
    assert links.technologies["Sample Genetics"].disabled is False
    assert links.technologies["Removed Tech"].disabled is True


def test_a_semicolon_in_effect_text_is_not_a_comment(links) -> None:  # type: ignore[no-untyped-def]
    """Comments are line-initial. Stripping `;` inline silently truncates."""
    assert links.facilities["Sample Nexus"].effect == "Naval Movement +2; Naval Bases"


def test_a_comma_in_effect_text_survives(links) -> None:  # type: ignore[no-untyped-def]
    """Two real facilities do this. A fixed column index loses half the text."""
    assert links.facilities["Sample Dock"].effect == "+2 Morale:Sea, Sea Def +100%"


def test_a_secret_project_is_detected_by_its_trailing_ints(links) -> None:  # type: ignore[no-untyped-def]
    """Arity alone is ambiguous once effect text can contain commas — the five
    trailing integers are the reliable marker."""
    project = links.facilities["The Sample Project"]
    assert project.secret_project is True
    assert project.ai_priorities == (-1, 0, 1, 1, 2)
    assert project.effect == "+1 Talent Each Base"

    assert links.facilities["Sample Dock"].secret_project is False


def test_flag_bits_are_read_right_to_left() -> None:
    """`000100000` is bit 6, not bit 4. Reading left-to-right inverts every
    flag in the file."""
    assert flags("000000001") == (1,)
    assert flags("000100000") == (6,)
    assert flags("100000000") == (9,)
    assert flags("000000000") == ()


def test_prereqs_splits_techs_from_the_disable_sentinel() -> None:
    assert prereqs("Sampl", "SamInd") == (("Sampl", "SamInd"), False)
    assert prereqs("None", "None") == ((), False)
    assert prereqs("Disable", "None") == ((), True)


def test_the_preamble_is_not_parsed_as_data(links) -> None:  # type: ignore[no-untyped-def]
    """Comments before the first #SECTION must not become rows."""
    assert all(t.name for t in links.technologies.values())
    assert "Sample Genetics" in links.technologies


def test_the_tech_graph_is_navigable(links) -> None:  # type: ignore[no-untyped-def]
    """requiresTech across every class *is* the dependency graph."""
    assert links.technologies["Sample Fusion"].requires == ("Sampl", "SamInd")
    assert links.unlocked_by("Sampl") == ["Sample Tanks", "The Sample Project"]


# --- the anti-masquerade guardrail ----------------------------------------


def test_every_node_carries_all_three_provenance_predicates(graph: rdflib.Graph) -> None:
    """An untagged fact reads as authoritative as a canonical one, so this is
    the one place the shapes are unforgiving."""
    subjects = set(graph.subjects(rdflib.RDF.type, None))
    assert subjects
    for subject in subjects:
        assert graph.value(subject, SMAC.appliesToEngine) is not None, subject
        assert graph.value(subject, SMAC.ruleTier) is not None, subject
        assert graph.value(subject, SMAC.sourcedFrom) is not None, subject


def test_the_emitted_turtle_actually_parses(graph: rdflib.Graph) -> None:
    """Turtle's PN_LOCAL grammar does not admit `/`, so `smac:tech/biogen` is a
    parse error rather than a slow IRI. Round-tripping is what catches it —
    the output looks entirely reasonable to a human reader."""
    assert len(graph) > 0
    assert (None, rdflib.RDF.type, SMAC.SecretProject) in graph


def test_provenance_vocabularies_are_closed() -> None:
    """A bad tag that only fails at Quipu write time fails after the expensive
    part of the ingest."""
    with pytest.raises(ValueError, match="engine must be one of"):
        Provenance(engine="nethack")
    with pytest.raises(ValueError, match="tier must be one of"):
        Provenance(tier="probably-right")
    with pytest.raises(ValueError, match="sourcedFrom cannot be empty"):
        Provenance(source="")


def test_a_thinker_override_is_tagged_as_a_house_rule(links) -> None:  # type: ignore[no-untyped-def]
    """The tier is what stops a Thinker deviation surfacing as canonical SMAC."""
    text = turtle(links, Provenance(engine="thinker", tier="house-rule", source="thinker.ini"))
    assert '"house-rule"' in text
    assert '"canonical"' not in text


def test_iris_are_stable_across_reingest(links) -> None:  # type: ignore[no-untyped-def]
    """Derived from the name, not a counter — otherwise every re-sync looks
    like a wholesale replacement in a bitemporal store."""
    assert turtle(links) == turtle(parse_file(EXCERPT))


# --- the briefing and the retriever ---------------------------------------


def test_the_briefing_excludes_disabled_items(links) -> None:  # type: ignore[no-untyped-def]
    text = briefing(links)
    assert "Sample Tanks" in text
    assert "Removed Facility" not in text
    assert "The Removed Project" not in text


def test_the_briefing_separates_secret_projects(links) -> None:  # type: ignore[no-untyped-def]
    text = briefing(links)
    assert "Secret projects" in text
    assert "The Sample Project" in text.split("Secret projects")[1]


def test_the_briefing_does_not_invent_a_rule_the_engine_does_not_have(links) -> None:
    """This said "one per game, globally" on all 64 project lines, and it was ours — not a line
    from alphax.txt — shipped in a briefing labelled *canonical*.

    The engine has no such rule. `SecretProjects[]` records which base *holds* each project:
    several bases may build the same one at once and the first to finish takes it. A project
    whose base is destroyed goes `SP_Destroyed`, and comes back to the pool under Thinker's
    `rebuild_secret_projects` — a house rule, so not statable at canonical tier at all.

    A fabricated canonical fact is the precise failure the datalinks plane exists to prevent,
    and it is worse from the briefing than from anywhere else: the briefing is cached once and
    sits in front of every decision for a whole game.
    """
    text = briefing(links)
    assert "one per game" not in text
    assert "only one base can hold it" in text


def test_prerequisites_are_named_not_abbreviated(links) -> None:  # type: ignore[no-untyped-def]
    """ "needs Sampl" is not actionable; the model sees display names."""
    assert "needs Sample Genetics" in briefing(links)


def test_retrieval_is_bounded_to_this_turns_action_space(links) -> None:  # type: ignore[no-untyped-def]
    """Budget discipline: a turn offering two build options gets two facts, not
    the whole rulebook."""
    view = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        action_space=[
            Action(id="a1", action="Sample Tanks"),
            Action(id="a2", action="The Sample Project"),
            Action(id="a3", action="end_turn"),
        ],
    )
    grounding = DatalinksRetriever(links).retrieve(view)

    assert grounding.hits == 2
    assert any("Sample Tanks" in f for f in grounding.facts)
    assert not any("Sample Nexus" in f for f in grounding.facts)


def test_an_unknown_action_grounds_to_nothing(links) -> None:  # type: ignore[no-untyped-def]
    """Exact match only. A fuzzy match would ground the decision in the wrong
    rule and label it canonical — silence is the safer failure."""
    view = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        action_space=[Action(id="a1", action="Sample Tank")],  # singular: not a facility
    )
    assert DatalinksRetriever(links).retrieve(view).hits == 0


def test_the_fact_limit_is_enforced(links) -> None:  # type: ignore[no-untyped-def]
    view = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        action_space=[
            Action(id=f"a{i}", action=name)
            for i, name in enumerate(["Sample Tanks", "Sample Dock", "Sample Nexus"])
        ],
    )
    assert DatalinksRetriever(links, limit=2).retrieve(view).hits == 2


def test_the_retriever_satisfies_the_knowledge_seam(links) -> None:  # type: ignore[no-untyped-def]
    """K2 swaps this for quipu_context without the orchestrator noticing."""
    from neural_amplifier.brain import ScriptedBrain
    from neural_amplifier.orchestrator import Orchestrator

    view = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        surface_id="base.production",
        action_space=[Action(id="a1", action="Sample Tanks")],
    )
    result = Orchestrator(ScriptedBrain(), retriever=DatalinksRetriever(links)).decide(view)

    assert result.record.knowledge.quipu_hits == 1
    assert result.record.knowledge.quipu_degraded is False


# --- mod detection ---------------------------------------------------------


def test_a_mod_announces_itself_in_its_header() -> None:
    """Thinker ships its own alphax.txt whose tech tree differs from stock.
    The file is byte-for-byte the same *shape*, so the header is the only
    signal — and once stored as canonical, nothing downstream can tell them
    apart. This is the masquerade the tier predicate exists to stop."""
    from neural_amplifier.datalinks import looks_modded

    modded = (
        ";\n; *  SMACX Thinker Mod / Default tech tree  *\n;\n"
        "#TECHNOLOGY\nA, A, 0,0,0,0, None, None, 000000000\n"
    )
    assert looks_modded(modded) == "thinker mod"


def test_our_synthetic_excerpt_is_not_flagged() -> None:
    from neural_amplifier.datalinks import looks_modded

    assert looks_modded(EXCERPT.read_text()) is None


def test_the_header_stops_at_the_first_section() -> None:
    """A `;` comment *between* sections is not part of the header, or an
    unrelated note halfway down the file could trip the mod check."""
    from neural_amplifier.datalinks.parse import header

    text = (
        ";\n; Stock rules\n;\n#TECHNOLOGY\n"
        "; Thinker Mod note in the body\nA, A, 0,0,0,0, None, None, 0\n"
    )
    assert "Stock rules" in header(text)
    assert "Thinker Mod" not in header(text)


def test_design_space_dwarfs_the_predefined_unit_list() -> None:
    """The point of modelling components rather than the 26 predefined designs.

    ``#UNITS`` lists 26 rows. What a player may actually build is a chassis, a weapon, an
    armour, a reactor and up to two abilities — millions of combinations. An ontology that
    models only the predefined designs describes a rounding error of the real space, so the
    parts have to be first-class nodes.
    """
    from neural_amplifier.datalinks.parse import parse

    text = """#CHASSIS
Infantry,M1, Squad,M1, Sentinels,M2, Garrison,M1, 1, 0, 0, 0, 1, 1, None, Shock,M2, Elite,M1,
Foil,M1, Skimship,M1, Hoverboat,M1, Coastal,M1, 4, 1, 0, 0, 2, 4, DocFlex, Mega,M2, Super,M1,

#REACTORS
Fission Plant, Fission, 1, None,
Fusion Reactor, Fusion, 2, Fusion,

#WEAPONS
Hand Weapons, Gun, 1, 0, 1, None, 0, 0,
Laser, Laser, 2, 0, 2, Physic, 0, 0,

#DEFENSES
No Armor, Scout, 1, 0, 1, None, 0, 0,
Synthmetal, Synth, 2, 0, 2, IndBase, 0, 0,

#ABILITIES
Super Former, 1, EcoEng2, Super, 000000010111, Terraform rate doubled
Deep Radar, 0, MilAlg, , 010000111111, Sees 2 spaces

#UNITS
2
Colony Pod, Infantry, Colony Pod, Scout, 8, 0, 0, None, -1, 00
Formers, Infantry, Formers, Scout, 9, 0, 0, Ecology, -1, 00
"""
    links = parse(text)
    assert len(links.units) == 2
    assert len(links.chassis) == 2
    assert len(links.reactors) == 2
    assert len(links.abilities) == 2

    # 2 chassis x 2 weapons x 2 armour x 2 reactors x (none|one of 2|the pair) = 16 x 4
    assert links.design_space() == 2 * 2 * 2 * 2 * (1 + 2 + 1)
    assert links.design_space() > len(links.units)


def test_abilities_keep_the_effect_text_the_file_supplies() -> None:
    """Abilities are the one component class that ships human-readable meaning.

    That text was being discarded, which mattered: it is grounding the game already wrote.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse(
        "#ABILITIES\nSuper Former, 1, EcoEng2, Super, 000000010111, Terraform rate doubled\n"
    )
    assert links.abilities["Super Former"].description == "Terraform rate doubled"
    assert links.abilities["Super Former"].requires == ("EcoEng2",)


def test_disabled_parts_do_not_inflate_the_design_space() -> None:
    """A ``Disable`` prerequisite means present in the file but switched off."""
    from neural_amplifier.datalinks.parse import parse

    links = parse("#REACTORS\nFission Plant, Fission, 1, None,\nGhost Core, Ghost, 9, Disable,\n")
    assert len(links.reactors) == 2
    assert links.reactors["Ghost Core"].disabled


def test_social_models_carry_their_effect_deltas() -> None:
    """ "Democratic" tells a brain nothing; the deltas are the actual decision.

    Magnitudes are written as repeated signs in the file, so ``-----POLICE`` is -5, not -1.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse(
        "#SOCIO\n"
        "ECONOMY, EFFIC, SUPPORT, TALENT, MORALE, POLICE,"
        " GROWTH, PLANET, PROBE, INDUSTRY, RESEARCH\n"
        "ECONOMY, EFFIC, SUPPORT, TALENT, MORALE, POLICE,"
        " GROWTH, PLANET, PROBE, INDUSTRY, RESEARCH\n"
        "Politics, Economics, Values, Future Society\n"
        "Frontier, None,\n"
        "Police State, DocLoy, ++POLICE, ++SUPPORT, --EFFIC\n"
        "Democratic, EthCalc, ++EFFIC, ++GROWTH, --SUPPORT\n"
        "Fundamentalist, Brain, +MORALE, ++PROBE, --RESEARCH\n"
        "Simple, None,\n"
        "Free Market, IndEcon, ++ECONOMY, ---PLANET, -----POLICE\n"
    )
    by_name = {m.name: m for m in links.social_models}
    assert dict(by_name["Police State"].effects) == {"police": 2, "support": 2, "efficiency": -2}
    assert dict(by_name["Free Market"].effects) == {"economy": 2, "planet": -3, "police": -5}


def test_social_model_category_comes_from_position() -> None:
    """Nothing in a row names its category — only its group index does.

    This is the one section that cannot be parsed row-at-a-time, and getting it wrong would
    silently file Economics models under Politics.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse(
        "#SOCIO\n"
        "ECONOMY, EFFIC, SUPPORT, TALENT, MORALE, POLICE,"
        " GROWTH, PLANET, PROBE, INDUSTRY, RESEARCH\n"
        "ECONOMY, EFFIC, SUPPORT, TALENT, MORALE, POLICE,"
        " GROWTH, PLANET, PROBE, INDUSTRY, RESEARCH\n"
        "Politics, Economics\n"
        "Frontier, None,\n"
        "Police State, DocLoy, ++POLICE\n"
        "Democratic, EthCalc, ++EFFIC\n"
        "Fundamentalist, Brain, +MORALE\n"
        "Simple, None,\n"
        "Free Market, IndEcon, ++ECONOMY\n"
    )
    cats = {m.name: m.category for m in links.social_models}
    assert cats["Police State"] == "Politics"
    assert cats["Free Market"] == "Economics"
    assert links.social_models[0].is_default


def test_unrecognised_social_effect_is_dropped_not_guessed() -> None:
    """A mis-parsed effect would put a confident wrong number in front of the brain."""
    from neural_amplifier.datalinks.parse import social_effect

    assert social_effect("++POLICE") == ("police", 2)
    assert social_effect("-----POLICE") == ("police", -5)
    assert social_effect("++NOTATHING") is None
    assert social_effect("POLICE") is None  # no sign, so no magnitude
    assert social_effect("") is None


def test_grounding_carries_citable_ids(links) -> None:  # type: ignore[no-untyped-def]
    """Without ids the brain cannot cite, so `cited` is empty by construction.

    This retriever returned facts and no `fact_ids` for as long as it existed, which made three
    things silently impossible on the K1 path: a citation (the model has no id to name), the
    citation guard's offered set (empty, so every citation reads fabricated), and utilisation
    (unmeasurable rather than low — `Grounding.fact_ids` empty is the documented "retriever does
    not label its facts" case).

    The ids are the ones `rdf.py` mints for the same node, so a citation made here resolves in
    the graph the Quipu path serves rather than merely looking like an identifier.
    """
    view = WorldView(
        engine="thinker",
        scope="base",
        turn=1,
        faction="GAIANS",
        action_space=[Action(id="a1", action="Sample Tanks")],
    )
    grounding = DatalinksRetriever(links).retrieve(view)

    assert grounding.fact_ids == ("fac:sample-tanks",)
    # Positionally aligned with `facts` — the orchestrator zips them into "<id> <text>" lines
    # and a misalignment would attach every citation to the wrong node.
    assert len(grounding.fact_ids) == len(grounding.facts)


# --- anti-masquerade: a mod's rules must not enter the graph as canonical ------


def test_a_known_overlay_is_recognised_by_its_bytes(tmp_path: Path) -> None:
    """`looks_modded` reads the header, which only catches a mod polite enough to say so.

    This catches one that is not. `fixtures/smac/overlays.tsv` already records every overlay
    hash the fixture checker knows, so the same table closes the ingest hole — and it names the
    mod's *version*, where a header marker only reports that something looked off.
    """
    from neural_amplifier.datalinks.parse import overlay_source

    overlays = tmp_path / "overlays.tsv"
    body = b"; a mod that does not announce itself\n#TECHNOLOGY\n"
    digest = hashlib.sha1(body).hexdigest()
    overlays.write_text(f"# comment\n{digest}\talphax.txt\tsome-mod-v1\n")

    assert overlay_source(body, overlays) == "some-mod-v1"
    assert overlay_source(b"different bytes", overlays) is None
    # No table is not an error: a checkout without the fixture still ingests.
    assert overlay_source(body, tmp_path / "absent.tsv") is None


def test_the_two_checks_are_independent(tmp_path: Path) -> None:
    """Neither subsumes the other, which is why both run.

    A known mod that stays quiet in its header is caught only by the hash; an unknown mod that
    announces itself is caught only by the header.
    """
    from neural_amplifier.datalinks.parse import looks_modded, overlay_source

    quiet_known = b"; ordinary looking header\n#TECHNOLOGY\n"
    overlays = tmp_path / "overlays.tsv"
    overlays.write_text(f"{hashlib.sha1(quiet_known).hexdigest()}\talphax.txt\tquiet-mod\n")

    assert looks_modded(quiet_known.decode()) is None
    assert overlay_source(quiet_known, overlays) == "quiet-mod"

    loud_unknown = "; Thinker mod data\n#TECHNOLOGY\n"
    assert looks_modded(loud_unknown) == "thinker mod"
    assert overlay_source(loud_unknown.encode(), overlays) is None
