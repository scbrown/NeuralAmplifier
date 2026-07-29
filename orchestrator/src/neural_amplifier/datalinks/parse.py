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

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class Datalinks:
    """Everything parsed, keyed for lookup."""

    technologies: dict[str, Technology] = field(default_factory=dict)
    facilities: dict[str, Facility] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)

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


def _is_int(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


#: Sections parsed today. Chassis, units, terrain, and the social-engineering
#: tables share the file but not the shape, and are tracked separately rather
#: than half-parsed — a partial fact tagged canonical is worse than a missing one.
COMPONENT_SECTIONS = {"WEAPONS": "weapon", "DEFENSES": "armor"}


def parse(text: str) -> Datalinks:
    """Read the sections we model. Unmodelled sections are skipped, not guessed."""
    out = Datalinks()
    for row in sections(text):
        if row.section == "TECHNOLOGY":
            tech = _technology(row)
            out.technologies[tech.name] = tech
        elif row.section == "FACILITIES":
            facility = _facility(row)
            out.facilities[facility.name] = facility
        elif row.section in COMPONENT_SECTIONS:
            out.components.append(_component(COMPONENT_SECTIONS[row.section], row))
    return out


def parse_file(path: Path | str) -> Datalinks:
    # alphax.txt is a DOS-era file; latin-1 never fails and preserves the bytes.
    return parse(Path(path).read_text(encoding="latin-1"))
