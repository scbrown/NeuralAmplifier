"""Parse SMAC's ``alphax.txt`` — K1 of ``docs/knowledge-architecture.md``.

**No model is used here, and that is deliberate.** ``alphax.txt`` is a fixed-arity
comma-separated file with its own column documentation inline; a parser reads it
exactly, for free, reproducibly. An LLM reading it would cost tokens to produce a
*probabilistic* answer to a deterministic question — and the failure mode is the
worst kind available to us: a hallucinated tech prerequisite is indistinguishable
from a real one downstream, and the whole point of the datalinks plane is that
canonical rules are trustworthy. Extraction is the last place to spend a model.

Two parsing traps this handles, both load-bearing:

- **Comments are line-initial only.** ``;`` starts a comment, but effect text
  contains it — "Naval Movement +2; Naval Bases". Stripping inline would silently
  truncate a facility's description.
- **``Disable`` is not a prerequisite.** A ``preq`` column holds a tech shortcode,
  ``None`` (no prerequisite), or ``Disable`` (excluded from this game entirely).
  Treating ``Disable`` as a tech would invent an edge and quietly hide that an
  item is unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

#: Sentinels in a prerequisite column. Neither is a technology.
NO_PREREQ = "None"
DISABLED = "Disable"


@dataclass(frozen=True)
class Row:
    """One raw record: its section and its comma-split fields."""

    section: str
    fields: tuple[str, ...]
    line: int

    def get(self, index: int) -> str:
        return self.fields[index] if index < len(self.fields) else ""

    def number(self, index: int, default: int = 0) -> int:
        raw = self.get(index)
        try:
            return int(raw)
        except ValueError:
            return default


#: Substrings that mark a file as a *mod's* rules rather than stock SMAC.
#: Thinker ships its own alphax.txt whose header reads "SMACX Thinker Mod";
#: ingesting it as `canonical` is precisely the masquerade the tier predicates
#: exist to prevent, and the file looks identical in every other respect.
MOD_MARKERS = ("thinker mod", "smac-in-smacx", "mod /", "modded")


def header(text: str) -> str:
    """The leading comment block, which is where a mod announces itself."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            break
        if line.startswith(";"):
            lines.append(line.lstrip("; ").rstrip())
    return "\n".join(lines)


def looks_modded(text: str) -> str | None:
    """The header marker that says this is not stock SMAC, if any.

    Advisory, not a parser concern — but the caller tagging provenance needs
    it, because nothing else in the file distinguishes a mod's rules.
    """
    top = header(text).lower()
    for marker in MOD_MARKERS:
        if marker in top:
            return marker
    return None


def sections(text: str) -> Iterator[Row]:
    """Split the file into ``#SECTION`` blocks and comma-separated rows.

    Yields nothing until the first section header, so the file's preamble of
    comments never masquerades as data.
    """
    current: str | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("#"):
            current = line[1:].strip().upper()
            continue
        if current is None:
            continue
        fields = [f.strip() for f in line.split(",")]
        while fields and not fields[-1]:  # rows end with a trailing comma
            fields.pop()
        if fields:
            yield Row(section=current, fields=tuple(fields), line=number)


def prereqs(*values: str) -> tuple[tuple[str, ...], bool]:
    """Split prerequisite columns into real techs and a *disabled* flag."""
    techs = tuple(v for v in values if v and v not in (NO_PREREQ, DISABLED))
    return techs, any(v == DISABLED for v in values)


def flags(bits: str) -> tuple[int, ...]:
    """Positions set in a right-to-left bit string.

    ``alphax.txt`` writes the least significant bit **last** — Centauri Ecology's
    ``100000000`` is bit 9 (nutrients in fungus), not bit 1. Reading it
    left-to-right inverts every flag in the file.
    """
    return tuple(len(bits) - index for index, char in enumerate(bits) if char == "1")


@dataclass(frozen=True)
class Technology:
    name: str
    abbrev: str
    ai_growth: int
    ai_tech: int
    ai_wealth: int
    ai_power: int
    requires: tuple[str, ...]
    disabled: bool
    flags: tuple[int, ...]


@dataclass(frozen=True)
class Facility:
    name: str
    cost: int
    maintenance: int
    requires: tuple[str, ...]
    disabled: bool
    #: Tech that grants this facility free at new bases; ``Disable`` means never.
    free_with: str | None
    effect: str
    #: The five AI-priority ints only secret projects carry.
    secret_project: bool
    ai_priorities: tuple[int, ...] = ()


@dataclass(frozen=True)
class Component:
    """A weapon, armour piece, or reactor — the parts a unit design picks from."""

    kind: str
    name: str
    abbrev: str
    rating: int
    cost: int
    requires: tuple[str, ...]
    disabled: bool


#: What the engine's plan number means, in words. The engine classifies every unit
#: design by plan (``PLAN_COLONY = 8``, ``PLAN_TERRAFORM = 9``, …), and that number is
#: what the ``#UNITS`` rows carry in column 4. Naming them here rather than in the
#: adapter matters: a brain given only "Colony Pod" and a cost has to fall back on its
#: own recollection of a 1999 game, and a real model did exactly that — it picked a
#: Colony Pod believing it would grow the base that built it.
PLAN_ROLES: Final[dict[int, str]] = {
    0: "attacks enemy units and bases",
    1: "general-purpose combat unit",
    2: "defends a base or position",
    3: "explores and scouts terrain",
    4: "intercepts enemy aircraft",
    5: "destroys a base outright",
    6: "controls sea zones",
    7: "carries land units across water",
    8: "founds a new base elsewhere; does not grow the base that builds it",
    9: "terraforms terrain to improve tile yields",
    10: "ferries minerals or nutrients to another base",
    11: "infiltrates and sabotages rival factions",
    12: "an artifact, not a fighting unit",
    13: "tectonic missile",
    14: "fungal missile",
}


@dataclass(frozen=True)
class SocialModel:
    """One social-engineering choice — a model within a category.

    The `#SOCIO` block is positional rather than labelled: three header lines, then four groups
    of four rows, and the group's index is the only thing that says which category a model
    belongs to. Nothing in a row names its category.

    ``effects`` is the whole reason this section is worth parsing. "Democratic" tells a brain
    nothing; ``{"efficiency": 2, "growth": 2, "support": -1}`` is the actual decision. The file
    writes those as repeated signs — ``++EFFIC``, ``-----POLICE`` — so magnitude is the number
    of characters.
    """

    category: str
    name: str
    requires: tuple[str, ...]
    disabled: bool
    effects: tuple[tuple[str, int], ...]

    @property
    def is_default(self) -> bool:
        """The first model in each category, available from the start with no effects."""
        return not self.requires and not self.effects


@dataclass(frozen=True)
class Chassis:
    """A movement platform. Determines triad, speed, and the unit's name series."""

    name: str
    speed: int
    triad: int
    cargo: int
    requires: tuple[str, ...]
    disabled: bool

    @property
    def triad_name(self) -> str:
        return {0: "land", 1: "sea", 2: "air"}.get(self.triad, "")


@dataclass(frozen=True)
class Reactor:
    """A power plant. Multiplies effective hit points and divides component cost."""

    name: str
    abbrev: str
    power: int
    requires: tuple[str, ...]
    disabled: bool


@dataclass(frozen=True)
class Ability:
    """A special ability a design may carry.

    ``description`` is why this section is worth parsing at all: unlike chassis or reactors,
    abilities ship human-readable effect text in the file ("Terraform rate doubled", "Sees 2
    spaces"). That is grounding the game already wrote, and it was being discarded.
    """

    name: str
    abbrev: str
    #: Cost modifier, not an absolute cost — it adjusts the design's computed price.
    cost_modifier: int
    requires: tuple[str, ...]
    disabled: bool
    description: str


@dataclass(frozen=True)
class Unit:
    """A predefined unit design from ``#UNITS``.

    Cost is often 0, meaning "derive it from the chassis, weapon and armour" — the
    engine computes it, so a 0 here is not missing data and must not be reported as a
    price.
    """

    name: str
    chassis: str
    weapon: str
    armor: str
    #: The engine's own classification. See ``PLAN_ROLES``.
    plan: int
    #: 0 means the engine computes it from components.
    cost: int
    carry: int
    requires: tuple[str, ...]
    disabled: bool

    @property
    def role(self) -> str:
        return PLAN_ROLES.get(self.plan, "")


class _Disableable(Protocol):
    # A read-only property, not a mutable attribute: the part classes are frozen
    # dataclasses, and a settable-attribute protocol does not match them.
    @property
    def disabled(self) -> bool: ...


def _enabled(items: Iterable[_Disableable]) -> int:
    """Count items the rules actually permit.

    A ``Disable`` prerequisite means the entry exists in the file but is switched off, and
    counting it would inflate the design space with things nobody can build.
    """
    return sum(1 for i in items if not i.disabled)


@dataclass
class Datalinks:
    """Everything parsed, keyed for lookup."""

    technologies: dict[str, Technology] = field(default_factory=dict)
    facilities: dict[str, Facility] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    units: dict[str, Unit] = field(default_factory=dict)
    chassis: dict[str, Chassis] = field(default_factory=dict)
    reactors: dict[str, Reactor] = field(default_factory=dict)
    abilities: dict[str, Ability] = field(default_factory=dict)
    social_models: list[SocialModel] = field(default_factory=list)

    def design_space(self) -> int:
        """How many distinct unit designs the rules permit.

        The predefined ``#UNITS`` list is 26 rows, which badly understates what a player may
        actually build: a design is a chassis, a weapon, an armour, a reactor, and up to two
        abilities. Counting it matters because an ontology that models only the predefined
        designs describes a rounding error of the real space.

        Abilities are counted as "none, one, or any unordered pair", which is the engine's
        limit. Per-ability legality flags narrow this further, so treat the result as an upper
        bound on the enabled catalogue rather than a precise count.
        """
        chassis = _enabled(self.chassis.values())
        weapons = _enabled(c for c in self.components if c.kind == "weapon")
        armour = _enabled(c for c in self.components if c.kind == "armor")
        reactors = _enabled(self.reactors.values())
        n = _enabled(self.abilities.values())
        ability_choices = 1 + n + (n * (n - 1)) // 2
        return chassis * weapons * armour * reactors * ability_choices

    @property
    def secret_projects(self) -> list[Facility]:
        return [f for f in self.facilities.values() if f.secret_project]

    def by_abbrev(self) -> dict[str, Technology]:
        """Prerequisite columns cite the six-character abbreviation, not the name."""
        return {t.abbrev: t for t in self.technologies.values()}

    def unlocked_by(self, abbrev: str) -> list[str]:
        """Facilities a technology makes available. The reverse of ``requires``."""
        return sorted(f.name for f in self.facilities.values() if abbrev in f.requires)


def _technology(row: Row) -> Technology:
    requires, disabled = prereqs(row.get(6), row.get(7))
    return Technology(
        name=row.get(0),
        abbrev=row.get(1),
        ai_growth=row.number(2),
        ai_tech=row.number(3),
        ai_wealth=row.number(4),
        ai_power=row.number(5),
        requires=requires,
        disabled=disabled,
        flags=flags(row.get(8)),
    )


def _facility(row: Row) -> Facility:
    # Secret projects append five AI-priority ints after the effect text. The
    # file has no other marker, and arity alone is not enough: two facilities
    # ("Naval Yard", "Aerospace Complex") have a comma *inside* their effect,
    # so a fixed column index truncates them. Detect the five trailing integers
    # instead, and rejoin whatever is left as the effect.
    tail = list(row.fields[5:])
    is_project = len(tail) >= 6 and all(_is_int(v) for v in tail[-5:])
    effect_parts = tail[:-5] if is_project else tail
    requires, disabled = prereqs(row.get(3))
    free = row.get(4)
    return Facility(
        name=row.get(0),
        cost=row.number(1),
        maintenance=row.number(2),
        requires=requires,
        disabled=disabled,
        free_with=None if free in ("", NO_PREREQ, DISABLED) else free,
        effect=", ".join(p for p in effect_parts if p),
        secret_project=is_project,
        ai_priorities=tuple(int(v) for v in tail[-5:]) if is_project else (),
    )


def _component(kind: str, row: Row) -> Component:
    requires, disabled = prereqs(row.get(6))
    return Component(
        kind=kind,
        name=row.get(0),
        abbrev=row.get(1),
        rating=row.number(2),
        cost=row.number(4),
        requires=requires,
        disabled=disabled,
    )


#: alphax.txt writes social effects as abbreviations; the record and the graph use the long
#: names the engine's own struct uses, so an effect reads the same wherever it surfaces.
SOCIAL_EFFECTS: Final[dict[str, str]] = {
    "ECONOMY": "economy",
    "EFFIC": "efficiency",
    "SUPPORT": "support",
    "TALENT": "talent",
    "MORALE": "morale",
    "POLICE": "police",
    "GROWTH": "growth",
    "PLANET": "planet",
    "PROBE": "probe",
    "INDUSTRY": "industry",
    "RESEARCH": "research",
}


def social_effect(token: str) -> tuple[str, int] | None:
    """``"++POLICE"`` -> ``("police", 2)``; ``"-----POLICE"`` -> ``("police", -5)``.

    Magnitude is the count of sign characters, not a digit, so a five-step penalty is five
    minus signs. Returns None for anything unrecognised rather than guessing — a silently
    mis-parsed effect would put a confident wrong number in front of the brain.
    """
    token = token.strip()
    if not token:
        return None
    sign = 0
    idx = 0
    while idx < len(token) and token[idx] in "+-":
        sign += 1 if token[idx] == "+" else -1
        idx += 1
    name = SOCIAL_EFFECTS.get(token[idx:].strip().upper())
    if name is None or sign == 0:
        return None
    return name, sign


def _social_rows(rows: list[Row]) -> list[SocialModel]:
    """Assign models to categories by position.

    Three header lines then four groups of four. The category list is the third line, and a
    model's category is its group index — there is no marker in the row itself, which is why
    this cannot be done row-at-a-time like every other section.
    """
    if len(rows) < 4:
        return []
    categories = [c for c in rows[2].fields if c]
    body = rows[3:]
    out: list[SocialModel] = []
    per = 4
    for index, row in enumerate(body):
        group = index // per
        if group >= len(categories):
            break
        requires, disabled = prereqs(row.get(1))
        effects = tuple(e for e in (social_effect(f) for f in row.fields[2:]) if e is not None)
        out.append(
            SocialModel(
                category=categories[group],
                name=row.get(0),
                requires=requires,
                disabled=disabled,
                effects=effects,
            )
        )
    return out


def _chassis(row: Row) -> Chassis:
    """``name,gender, plural,gender, defensive,gender, garrison,gender, speed, triad, ...``

    Every display name is followed by a gender marker (``M1``/``M2``), which is why the numeric
    columns do not start where a naive reading expects. Only the first name is kept: the others
    are the game's name series for the same platform, not separate facts.
    """
    requires, disabled = prereqs(row.get(14))
    return Chassis(
        name=row.get(0),
        speed=row.number(8),
        triad=row.number(9),
        cargo=row.number(11),
        requires=requires,
        disabled=disabled,
    )


def _reactor(row: Row) -> Reactor:
    requires, disabled = prereqs(row.get(3))
    return Reactor(
        name=row.get(0),
        abbrev=row.get(1),
        power=row.number(2),
        requires=requires,
        disabled=disabled,
    )


def _ability(row: Row) -> Ability:
    """``name, cost-modifier, preq, abbrev, flag-bits, description``."""
    requires, disabled = prereqs(row.get(2))
    return Ability(
        name=row.get(0),
        abbrev=row.get(3),
        cost_modifier=row.number(1),
        requires=requires,
        disabled=disabled,
        description=", ".join(f for f in row.fields[5:] if f),
    )


def _unit(row: Row) -> Unit:
    """``name, chassis, weapon, armor, plan, cost, carry, preq, icon, ability-bits``.

    Column 4 is the plan, which is what makes a unit's *purpose* available as game data
    rather than as prose someone typed. Verified against the engine's enum: Colony Pod
    is 8 (PLAN_COLONY), Formers 9 (PLAN_TERRAFORM), Scout Patrol 3 (PLAN_RECON),
    Supply Crawler 10 (PLAN_SUPPLY), Probe Team 11 (PLAN_PROBE).
    """
    requires, disabled = prereqs(row.get(7))
    return Unit(
        name=row.get(0),
        chassis=row.get(1),
        weapon=row.get(2),
        armor=row.get(3),
        plan=row.number(4),
        cost=row.number(5),
        carry=row.number(6),
        requires=requires,
        disabled=disabled,
    )


def _is_int(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


#: Sections parsed today. Chassis, terrain, and the social-engineering tables share
#: the file but not the shape, and are tracked separately rather than half-parsed — a
#: partial fact tagged canonical is worse than a missing one.
COMPONENT_SECTIONS = {"WEAPONS": "weapon", "DEFENSES": "armor"}


def parse(text: str) -> Datalinks:
    """Read the sections we model. Unmodelled sections are skipped, not guessed."""
    out = Datalinks()
    # SOCIO is the one section whose rows cannot be read independently: a model's category comes
    # from its position relative to a header line, so the block has to be buffered.
    socio: list[Row] = []
    for row in sections(text):
        if row.section == "SOCIO":
            socio.append(row)
            continue
        if row.section == "TECHNOLOGY":
            tech = _technology(row)
            out.technologies[tech.name] = tech
        elif row.section == "FACILITIES":
            facility = _facility(row)
            out.facilities[facility.name] = facility
        elif row.section == "CHASSIS":
            ch = _chassis(row)
            out.chassis[ch.name] = ch
        elif row.section == "REACTORS":
            re_ = _reactor(row)
            out.reactors[re_.name] = re_
        elif row.section == "ABILITIES":
            ab = _ability(row)
            out.abilities[ab.name] = ab
        elif row.section == "UNITS":
            # The section opens with a count line, which is not a unit.
            if len(row.fields) > 1:
                unit = _unit(row)
                out.units[unit.name] = unit
        elif row.section in COMPONENT_SECTIONS:
            out.components.append(_component(COMPONENT_SECTIONS[row.section], row))
    out.social_models = _social_rows(socio)
    return out


def parse_file(path: Path | str) -> Datalinks:
    # alphax.txt is a DOS-era file; latin-1 never fails and preserves the bytes.
    return parse(Path(path).read_text(encoding="latin-1"))
