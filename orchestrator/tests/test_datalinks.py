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
    CombatMode,
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


# --- #TERRAIN and #RESOURCEINFO (na-3qy) --------------------------------------


def test_two_terraform_rows_share_a_name_and_must_not_collide() -> None:
    """`#TERRAIN` has two rows called "Fungus": one removes it, one plants it.

    Keyed by name alone one silently wins, and the brain loses a core former order. The verb is
    what separates them, which is why these are a list rather than a dict.
    """
    from neural_amplifier.datalinks.parse import parse

    text = (
        "#TERRAIN\n"
        "Fungus, None, Sea Fungus, None, 6, Remove $STR0, F, F\n"
        "Fungus, EcoEng, Sea Fungus, EcoEng, 6, Plant $STR0, F, Ctrl+F\n"
    )
    actions = parse(text).terraform

    assert [a.name for a in actions] == ["Remove Fungus", "Plant Fungus"]
    assert actions[1].requires == ("EcoEng",)


def test_no_sea_variant_is_not_the_same_as_disabled() -> None:
    """The file writes "no sea form" as the literal `Disable` in the sea prerequisite — the
    same token that means "switched off" in the land column.

    Reading Forest's sea column as a disable would delete a core terraform from the picture.
    """
    from neural_amplifier.datalinks.parse import parse

    text = (
        "#TERRAIN\n"
        "Forest, None, ..., Disable, 4, Plant $STR0, F, Shift+F\n"
        "Monolith, Disable, Monolith, Disable, 8, Place Monolith, ?, ?\n"
    )
    forest, monolith = parse(text).terraform

    assert forest.land_only is True and forest.disabled is False
    assert monolith.disabled is True


def test_a_star_yield_is_absent_not_zero() -> None:
    """The file's own comment says `*` columns are "ignored entirely" — the engine derives
    them from the tile's temperature, rainfall and rockiness.

    Parsing one as 0 would tell the brain Improved Land produces no minerals, which is
    confidently wrong rather than merely missing. An absent predicate is a gap a reader can
    see; a zero is an answer.
    """
    from neural_amplifier.datalinks.parse import parse

    text = "#RESOURCEINFO\nImproved Land, 1, *, *, 0,\nBorehole Square, 0, 6, 6, 0,\n"
    res = parse(text).resources

    assert res["Improved Land"].nutrients == 1
    assert res["Improved Land"].minerals is None
    assert res["Borehole Square"].minerals == 6
    # And a real zero survives as a zero.
    assert res["Borehole Square"].nutrients == 0


def test_a_star_yield_is_omitted_from_the_graph() -> None:
    """Same rule at the RDF boundary: no predicate rather than a zero one."""
    from neural_amplifier.datalinks import Provenance, turtle
    from neural_amplifier.datalinks.parse import parse

    graph = turtle(parse("#RESOURCEINFO\nImproved Land, 1, *, *, 0,\n"), Provenance())

    assert "smac:yieldNutrients 1" in graph
    assert "yieldMinerals" not in graph


# --- the #SOC* ladders, #CITIZENS, #MORALE, modes, orders, tuning (na-3qy) ----


def test_a_social_ladder_gives_the_socio_deltas_a_meaning() -> None:
    """`#SOCIO` says Free Market is `economy +2`. On its own that is a token, and a brain shown
    only the number has to supply the meaning from its recollection of a 1999 game.

    The `#SOC*` tables are the designers' own gloss of the same +2, in the file, at canonical
    tier — which is the difference between grounding the decision and hoping.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse("#SOCECONOMY\n-1, Sample penalty\n 0, Sample standard\n 2, Sample bonus!\n")

    assert links.effect_meaning("economy", 2) == "Sample bonus!"
    assert links.effect_meaning("economy", -1) == "Sample penalty"


def test_an_absent_ladder_rung_is_none_not_the_nearest_one() -> None:
    """The ladders are ragged — talent runs -1..+1 where research runs -5..+5 — so "clamp to the
    end of the table" is a reachable implementation and a wrong one.

    Reporting the +1 talent text for a +3 talent would state a consequence the file does not
    have. A *plausible* fabricated rule is the failure this plane exists to prevent; an
    obviously wrong one would at least be visible.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse("#SOCTALENT\n-1, Sample drones\n 0,\n 1, Sample talents!\n")

    assert links.effect_meaning("talent", 1) == "Sample talents!"
    assert links.effect_meaning("talent", 3) is None
    assert links.effect_meaning("research", 1) is None  # ladder not present at all


def test_a_ladder_rung_with_no_text_is_still_a_rung() -> None:
    """`#SOCTALENT` level 0 has a level and no description — the neutral rung, where nothing
    happens, which is a fact rather than a gap.

    Dropping it would make `effect_meaning("talent", 0)` indistinguishable from asking about a
    level the ladder does not have.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse("#SOCTALENT\n-1, Sample drones\n 0,\n 1, Sample talents!\n")

    assert [r.level for r in links.social_levels] == [-1, 0, 1]
    assert links.effect_meaning("talent", 0) == ""


def test_a_ladder_rung_iri_keeps_the_sign() -> None:
    """`slug` strips a leading minus, so `slug("economy -3")` and `slug("economy 3")` are the
    same string — the two ends of a ladder would collapse into one node with whichever
    description was emitted last.

    Worth a test rather than a comment because the collision is silent: the Turtle parses, the
    graph loads, and one real rung is simply gone.
    """
    from neural_amplifier.datalinks import Provenance, turtle
    from neural_amplifier.datalinks.parse import parse

    graph = turtle(parse("#SOCECONOMY\n-3, Sample bad\n 3, Sample good\n"), Provenance())

    assert "rung:economy-neg3" in graph
    assert "rung:economy-3" in graph
    assert "Sample bad" in graph and "Sample good" in graph


def test_a_stray_line_in_a_ladder_is_dropped_not_filed_as_neutral() -> None:
    """`Row.number` defaults an unparseable column to 0, and 0 is the *neutral* rung — the one
    reading "nothing happens".

    A mod's stray line silently becoming "economy 0: <its text>" is worse than losing the line,
    because it answers a question rather than declining to.
    """
    from neural_amplifier.datalinks.parse import parse

    links = parse("#SOCECONOMY\nnot a level, some text\n 1, Sample bonus\n")

    assert [(r.level, r.description) for r in links.social_levels] == [(1, "Sample bonus")]


def test_citizens_have_two_row_shapes_in_one_section(links) -> None:  # type: ignore[no-untyped-def]
    """The last three `#CITIZENS` rows are a name and a plural and nothing else — drones,
    workers and talents, which are ordinary citizens rather than assignable specialists.

    Recording which shape a row had matters because a specialist with three zeroed bonuses and a
    plain citizen parse to identical numbers, and only one of them can be put to work.
    """
    by_name = {c.name: c for c in links.citizens}

    assert by_name["Sample Technician"].specialist is True
    assert by_name["Sample Technician"].ops == 3
    assert by_name["Sample Drone"].specialist is False
    assert [c.name for c in links.specialists] == ["Sample Technician", "Sample Engineer"]


def test_disable_in_the_obsolete_column_means_never_obsoleted(links) -> None:  # type: ignore[no-untyped-def]
    """Same inversion `#FACILITIES` uses for its free-with column, two fields away from a
    prerequisite column where the identical token means "excluded from the game".

    Reading Engineer's `Disable` as an exclusion would delete the best late-game specialist
    from the picture — the `#TERRAIN` sea-variant mistake in a different section.
    """
    by_name = {c.name: c for c in links.citizens}

    assert by_name["Sample Technician"].obsoleted_by == "SamFus"
    assert by_name["Sample Engineer"].obsoleted_by is None
    assert by_name["Sample Engineer"].disabled is False
    assert by_name["Sample Engineer"].requires == ("SamFus",)


def test_the_morale_ladder_is_ordered_and_the_second_column_is_not_a_synonym(links) -> None:
    """Morale reaches a brain as a word ("Hardened") and as a number ("+2 Morale"), and only the
    ladder's order composes the two.

    The second column is the same rung for native life, not an alternative spelling — which is
    why a Mind Worm's "Great Boil" and a squad's "Commando" are one fact, not two.
    """
    assert [m.index for m in links.morale_levels] == [0, 1, 2]
    assert links.morale_levels[1].name == "Sample"
    assert links.morale_levels[1].native_name == "Sample Larva"


def test_combat_modes_are_names_and_the_matchup_rule_is_not_invented(links) -> None:  # type: ignore[no-untyped-def]
    """The file states "Projectile weapons receive a bonus against Energy Armor" in a *prose
    comment*, and the rows carry four display strings with no magnitude anywhere.

    So the parser takes the vocabulary and stops. Encoding the matchup would mint a
    canonical-tier combat rule out of English we read, and the number it would need is not in
    the file at any tier.
    """
    offense = [m.name for m in links.combat_modes if m.kind == "offense"]
    defense = [m.name for m in links.combat_modes if m.kind == "defense"]

    # Overlapping but not identical, which is why `kind` is on the record and the two sections
    # are not merged by name.
    assert offense == ["Projectile", "Missile"]
    assert defense == ["Projectile", "Binary"]
    assert not [f for f in CombatMode.__dataclass_fields__ if "bonus" in f]


def test_the_order_list_is_deliberately_partial(links) -> None:  # type: ignore[no-untyped-def]
    """`#ORDERS` is movement and sentry only — every terraform order is missing from it.

    The evidence is in `#TERRAIN`: each of its rows carries its own two trailing key columns
    ("f, F" for Farm), which is exactly what an `#ORDERS` row carries, and a terrain row would
    have no reason to hold a key binding if the order list already did. So the engine's order
    menu is these rows *plus* `#TERRAIN`, and reading this section as the whole menu would
    understate what a unit can do.
    """
    assert [o.name for o in links.orders] == ["No Orders", "Sample Sentry"]
    assert links.orders[0].key == "-"
    assert not any("Farm" in o.name for o in links.orders)


def test_energy_categories_are_names_only(links) -> None:  # type: ignore[no-untyped-def]
    """The allocation split is a recurring decision, so the brain will be shown these words —
    but the rules that govern it (the split sums to ten; efficiency taxes it) are engine
    behaviour with no representation in this file, and are not manufactured here."""
    assert [(e.abbrev, e.name) for e in links.energy_categories] == [
        ("Econ", "Sample Economy"),
        ("Labs", "Sample Laboratories"),
    ]


def test_inline_comments_are_stripped_in_the_two_sections_that_have_them(links) -> None:
    """`#RULES` and `#WORLDBUILDER` break the file's own "comments are line-initial" rule.

    Handling that globally is the tempting fix and the wrong one — it would truncate every
    facility effect containing a `;`, which is the first trap this module documents. So the
    split lives in the one reader that needs it.
    """
    assert links.rules[0].values == (3,)
    assert links.rules[0].label == "Sample movement rate along roads"
    # And the effect-text trap still holds in the same parse.
    assert links.facilities["Sample Nexus"].effect == "Naval Movement +2; Naval Bases"


def test_a_comma_inside_an_inline_comment_does_not_become_a_value(links) -> None:
    """The exact inverse of the effect-text trap, in the same file. `(… x0, x1, x2)` has already
    been shredded into three fields by the row split before any reader sees it.

    Rejoining before the `;` split is what keeps "x1" from being read as a second parameter — a
    positional section, so one spurious value shifts the meaning of every row after it.
    """
    land_modifier = links.world_builder[1]

    assert land_modifier.values == (120,)
    assert land_modifier.label == "Sample land modifier"
    assert land_modifier.note == "additional land from LAND selection: x0, x1, x2"


def test_a_tuning_row_can_carry_several_numbers(links) -> None:
    """Both sections have rows that are not one value: artillery damage is a numerator and a
    denominator, and continent sizes are five ratios on one line.

    Taking column 0 and stopping would read "3,2 artillery" as a 3 — half a rule, stated with
    the confidence of a whole one.
    """
    assert links.rules[1].values == (3, 2)
    assert links.world_builder[2].values == (3, 6, 12, 18, 24)


def test_tuning_parameters_are_identified_by_position_not_label(links) -> None:
    """The label comes from a comment, so a mod that strips the comments would leave every knob
    anonymous — but the engine reads these blocks by *offset*, so position is the real identity.

    Labels are not unique either, and that is measured rather than hypothetical: in a real
    `alphax.txt`, `#RULES` rows 17, 18 and 19 all read "Psi combat offense-to-defense ratio" and
    differ only in the parenthetical. Keying by label would silently keep one of the three.

    `section` is on the record for the same reason: two blocks both starting at index 0 means an
    index alone identifies nothing.
    """
    from neural_amplifier.datalinks.parse import parse

    stripped = parse("#RULES\n3,\n7,\n")

    assert [p.index for p in stripped.rules] == [0, 1]
    assert [p.values for p in stripped.rules] == [(3,), (7,)]
    assert [p.label for p in stripped.rules] == ["", ""]

    repeated = parse(
        "#RULES\n3,2, ; Sample psi ratio (LAND unit defending)\n"
        "1,1, ; Sample psi ratio (SEA unit defending)\n"
    )
    assert [p.label for p in repeated.rules] == ["Sample psi ratio"] * 2
    assert [(p.index, p.note) for p in repeated.rules] == [
        (0, "LAND unit defending"),
        (1, "SEA unit defending"),
    ]
    assert {p.section for p in links.world_builder} == {"WORLDBUILDER"}
    assert {p.section for p in links.rules} == {"RULES"}
