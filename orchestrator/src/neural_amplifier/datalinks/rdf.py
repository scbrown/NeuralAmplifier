"""Emit the ``smac:`` datalinks plane as Turtle for Quipu (K1).

Every fact carries the three anti-masquerade predicates from
``docs/knowledge-architecture.md``: ``appliesToEngine``, ``ruleTier``, and
``sourcedFrom``. They are not optional decoration — the tier tag is the reader's
only signal of trust, and an untagged fact reads as authoritative as a canonical
one. So they are emitted by construction here rather than left to the caller,
and :func:`turtle` refuses to write a node without them.

Stock ``alphax.txt`` is ``ruleTier "canonical"``, ``appliesToEngine "smac"`` — a
Thinker override or a GLSMAC deviation is a *different* tier written separately,
never a quiet edit of these triples.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .parse import Ability, Chassis, Component, Datalinks, Facility, Reactor, Technology, Unit

NAMESPACE = "http://neuralamplifier.local/ontology/smac/"
#: Per-class prefixes rather than one ``smac:``. Turtle's PN_LOCAL grammar does
#: not admit ``/``, so ``smac:tech/biogen`` is a parse error, not a slow IRI —
#: caught by round-tripping the real output through rdflib rather than by
#: eyeballing it.
PREFIXES = (
    f"@prefix smac: <{NAMESPACE}> .",
    f"@prefix tech: <{NAMESPACE}tech/> .",
    f"@prefix fac:  <{NAMESPACE}facility/> .",
    f"@prefix unit: <{NAMESPACE}unit/> .",
    f"@prefix chas: <{NAMESPACE}chassis/> .",
    f"@prefix rct:  <{NAMESPACE}reactor/> .",
    f"@prefix abil: <{NAMESPACE}ability/> .",
    f"@prefix comp: <{NAMESPACE}component/> .",
    f"@prefix src:  <{NAMESPACE}source/> .",
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
)

Engine = str
Tier = str

#: Closed vocabularies, mirroring the SHACL shapes. Enforced here too, because a
#: bad tag that only fails at write time fails after the expensive part.
ENGINES = ("smac", "thinker", "glsmac")
TIERS = ("canonical", "house-rule", "engine-observed", "aspirational")


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from and how far to trust it."""

    engine: Engine = "smac"
    tier: Tier = "canonical"
    source: str = "alphax.txt"

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ValueError(f"engine must be one of {ENGINES}, got {self.engine!r}")
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")
        if not self.source:
            raise ValueError("sourcedFrom cannot be empty — it is the audit trail")

    def triples(self) -> tuple[str, ...]:
        return (
            f'smac:appliesToEngine "{self.engine}"',
            f'smac:ruleTier "{self.tier}"',
            f"smac:sourcedFrom src:{slug(self.source)}",
        )


def slug(value: str) -> str:
    """A stable, URL-safe local name.

    Derived from the display name rather than a counter so the same rule keeps
    the same IRI across re-ingests — otherwise every re-sync looks like a
    wholesale replacement in a bitemporal store.
    """
    out = []
    for char in value.strip().lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "unnamed"


def literal(value: str) -> str:
    """Turtle string literal. Escapes are the difference between a load and a
    parse error three hundred lines in."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _node(iri: str, cls: str, props: Iterable[tuple[str, str]], prov: Provenance) -> str:
    lines = [f"{iri} a {cls} ;"]
    body = [f"    {predicate} {obj}" for predicate, obj in props]
    body += [f"    {triple}" for triple in prov.triples()]
    lines.append(" ;\n".join(body) + " .")
    return "\n".join(lines)


def technology(tech: Technology, prov: Provenance) -> str:
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(tech.name)),
        ("smac:abbrev", literal(tech.abbrev)),
        ("smac:aiWeightGrowth", str(tech.ai_growth)),
        ("smac:aiWeightTech", str(tech.ai_tech)),
        ("smac:aiWeightWealth", str(tech.ai_wealth)),
        ("smac:aiWeightPower", str(tech.ai_power)),
    ]
    # Disabled items get an explicit flag rather than a missing edge — "excluded
    # from this game" and "available with no prerequisite" must not look alike.
    if tech.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(abbrev)}") for abbrev in tech.requires]
    props += [("smac:flag", str(bit)) for bit in tech.flags]
    return _node(f"tech:{slug(tech.abbrev)}", "smac:Technology", props, prov)


def facility(item: Facility, prov: Provenance) -> str:
    cls = "smac:SecretProject" if item.secret_project else "smac:Facility"
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:cost", str(item.cost)),
        ("smac:maintenance", str(item.maintenance)),
        ("smac:effectText", literal(item.effect)),
    ]
    if item.disabled:
        props.append(("smac:disabled", "true"))
    if item.free_with:
        # alphax.txt's own header calls this column "Free" — the tech that grants
        # the facility free at new bases. knowledge-architecture.md §ontology
        # lists it as `obsoletedBy`; the file wins.
        props.append(("smac:freeWithTech", f"tech:{slug(item.free_with)}"))
    props += [("smac:requiresTech", f"tech:{slug(abbrev)}") for abbrev in item.requires]
    if item.secret_project and len(item.ai_priorities) == 5:
        names = ("Fight", "Power", "Tech", "Wealth", "Growth")
        props += [
            (f"smac:aiPriority{name}", str(value))
            for name, value in zip(names, item.ai_priorities, strict=True)
        ]
    return _node(f"fac:{slug(item.name)}", cls, props, prov)


def chassis(item: Chassis, prov: Provenance) -> str:
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:speed", str(item.speed)),
        ("smac:triad", literal(item.triad_name)),
    ]
    if item.cargo:
        props.append(("smac:cargoCapacity", str(item.cargo)))
    if item.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(a)}") for a in item.requires]
    return _node(f"chas:{slug(item.name)}", "smac:Chassis", props, prov)


def reactor(item: Reactor, prov: Provenance) -> str:
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:abbrev", literal(item.abbrev)),
        ("smac:reactorPower", str(item.power)),
    ]
    if item.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(a)}") for a in item.requires]
    return _node(f"rct:{slug(item.name)}", "smac:Reactor", props, prov)


def ability(item: Ability, prov: Provenance) -> str:
    """An ability, carrying the effect text the file already provides.

    ``smac:effectText`` rather than a bespoke predicate, so retrieval finds an ability's
    meaning through the same path it finds a facility's — one query shape, not two.
    """
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:costModifier", str(item.cost_modifier)),
    ]
    if item.abbrev:
        props.append(("smac:abbrev", literal(item.abbrev)))
    if item.description:
        props.append(("smac:effectText", literal(item.description)))
    if item.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(a)}") for a in item.requires]
    return _node(f"abil:{slug(item.name)}", "smac:Ability", props, prov)


def component(item: Component, prov: Provenance) -> str:
    """A weapon or armour piece — one of the parts a design is assembled from."""
    cls = {"weapon": "smac:Weapon", "armor": "smac:Armor"}.get(item.kind, "smac:Component")
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:abbrev", literal(item.abbrev)),
        ("smac:rating", str(item.rating)),
        ("smac:cost", str(item.cost)),
    ]
    if item.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(a)}") for a in item.requires]
    return _node(f"comp:{slug(item.kind)}-{slug(item.name)}", cls, props, prov)


def unit(item: Unit, prov: Provenance) -> str:
    """A predefined unit design.

    ``smac:role`` is the point of this node. It is derived from the engine's own plan
    number, so "what is this unit for" becomes a retrievable fact with provenance rather
    than prose embedded in an adapter. Measured need: a model given only a name and a cost
    picked a Colony Pod believing it would grow the base that built it.

    Cost is emitted only when non-zero. A 0 in the file means "derive it from the chassis,
    weapon and armour", and publishing that as ``smac:cost 0`` would assert a free unit.
    """
    props: list[tuple[str, str]] = [
        ("rdfs:label", literal(item.name)),
        ("smac:plan", str(item.plan)),
        # Parts as IRIs, not strings. A predefined design is a *composition*, and the whole
        # point of modelling the parts is that the graph can then express the design space
        # rather than the 26 rows someone happened to write down. A literal "Infantry" is a
        # dead end; chas:infantry is a hop to speed, triad and prerequisite tech.
        ("smac:hasChassis", f"chas:{slug(item.chassis)}"),
        ("smac:hasWeapon", f"comp:weapon-{slug(item.weapon)}"),
        ("smac:hasArmor", f"comp:armor-{slug(item.armor)}"),
    ]
    if item.role:
        props.append(("smac:role", literal(item.role)))
    if item.cost:
        props.append(("smac:cost", str(item.cost)))
    if item.carry:
        props.append(("smac:carryCapacity", str(item.carry)))
    if item.disabled:
        props.append(("smac:disabled", "true"))
    props += [("smac:requiresTech", f"tech:{slug(abbrev)}") for abbrev in item.requires]
    return _node(f"unit:{slug(item.name)}", "smac:Unit", props, prov)


def statements(links: Datalinks, prov: Provenance | None = None) -> Iterator[str]:
    provenance = prov or Provenance()
    for tech in links.technologies.values():
        yield technology(tech, provenance)
    for item in links.facilities.values():
        yield facility(item, provenance)
    # Distinct names per loop: reusing one `part` binding across differently-typed loops
    # type-checks as whichever type came first, which mypy correctly rejected.
    for platform in links.chassis.values():
        yield chassis(platform, provenance)
    for power in links.reactors.values():
        yield reactor(power, provenance)
    for special in links.abilities.values():
        yield ability(special, provenance)
    for piece in links.components:
        yield component(piece, provenance)
    for design in links.units.values():
        yield unit(design, provenance)


def turtle(links: Datalinks, prov: Provenance | None = None) -> str:
    """The full graph, ready for ``quipu_set`` or a file-mounted datalinks group."""
    body = "\n\n".join(statements(links, prov))
    return "\n".join(PREFIXES) + "\n\n" + body + "\n"
