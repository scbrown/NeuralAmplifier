"""Parse SMAC's ``alphax.txt`` — K1 of ``docs/knowledge-architecture.md``.

**No model is used here, and that is deliberate.** ``alphax.txt`` is a fixed-arity
comma-separated file with its own column documentation inline; a parser reads it
exactly, for free, reproducibly. An LLM reading it would cost tokens to produce a
*probabilistic* answer to a deterministic question — and the failure mode is the
worst kind available to us: a hallucinated tech prerequisite is indistinguishable
from a real one downstream, and the whole point of the datalinks plane is that
canonical rules are trustworthy. Extraction is the last place to spend a model.

Three parsing traps this handles, all load-bearing:

- **Comments are line-initial only.** ``;`` starts a comment, but effect text
  contains it — "Naval Movement +2; Naval Bases". Stripping inline would silently
  truncate a facility's description.
- **…except in ``#RULES`` and ``#WORLDBUILDER``,** which are bare numbers with
  their meaning in an *inline* ``;`` comment. Those two sections have their own
  reader (:func:`_tuned`); the rule above still holds everywhere else, so the
  tempting global fix is the one that breaks the file.
- **``Disable`` is not a prerequisite.** A ``preq`` column holds a tech shortcode,
  ``None`` (no prerequisite), or ``Disable`` (excluded from this game entirely).
  Treating ``Disable`` as a tech would invent an edge and quietly hide that an
  item is unavailable. In the *other* columns that accept it — a facility's
  free-with tech, a citizen's obsoleting tech, a terraform's sea variant — it
  inverts, and means "never" rather than "excluded".
"""

from __future__ import annotations

import hashlib
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


#: Known non-vanilla file hashes, mapping sha1 to the mod that ships them
#: (``fixtures/smac/overlays.tsv``). Repo root, beside the justfile.
OVERLAYS = Path(__file__).resolve().parents[4] / "fixtures" / "smac" / "overlays.tsv"


def overlay_source(data: bytes, overlays: Path | None = None) -> str | None:
    """Which mod ships this exact file, by content hash, or ``None`` if unrecognised.

    :func:`looks_modded` reads the header, which only catches a mod polite enough to say so.
    This catches one that is not: the bytes are the bytes, and ``overlays.tsv`` already records
    every overlay hash the fixture checker knows about — including three ``alphax.txt`` variants
    from Thinker v5.4.

    Neither check subsumes the other. The header catches an unknown mod that announces itself;
    the hash catches a known mod that does not, and names its *version* rather than just
    reporting that something looked off.

    Duplicates a few lines of ``scripts/game_fixture.py``. Deliberately: that script runs under
    bare ``python3`` with the package uninstalled, so it cannot import this, and a shared helper
    would have to live somewhere both can reach — which is a bigger change than ten lines of
    TSV parsing is worth.
    """
    path = overlays or OVERLAYS
    if not path.exists():
        return None
    digest = hashlib.sha1(data).hexdigest()  # noqa: S324 — matching an existing manifest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0] == digest:
            return fields[2]
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


@dataclass(frozen=True)
class TerraformAction:
    """One thing a former can do to a tile — ``#TERRAIN``.

    ``land`` and ``sea`` are two variants of the same order, which is why they are one record
    rather than two: "Farm" and "Kelp Farm" are the same key press on different terrain. A
    ``Disable`` in the sea prerequisite means **there is no sea variant**, not that the action
    is switched off — Forest has none, and reading that as "forests are disabled" would delete a
    core terraform from the brain's picture.

    ``verb`` carries the file's own template (``Cultivate $STR0``), which is the difference
    between "Fungus" and "Fungus": the section has two rows with that name, one to *remove* it
    and one to *plant* it. Keyed by name alone they collide and one silently wins, so these are
    a list rather than a dict.
    """

    land: str
    sea: str
    #: Turns of former work, before terrain and ability modifiers.
    rate: int
    #: The file's own action template — "Plant $STR0", "Remove $STR0", "Terraform UP".
    verb: str
    requires: tuple[str, ...]
    disabled: bool
    sea_requires: tuple[str, ...]
    #: True when the order has no sea form at all, as distinct from one needing a tech.
    land_only: bool

    @property
    def name(self) -> str:
        """A stable label. Two rows share ``land`` — the verb is what separates them."""
        action = self.verb.split("$STR0")[0].strip() or self.verb.strip()
        return f"{action} {self.land}".strip()


@dataclass(frozen=True)
class ResourceYield:
    """What a special square produces — ``#RESOURCEINFO``.

    ``None`` means the file wrote ``*``, which its own comment glosses as "ignored entirely":
    the value comes from the tile's temperature, rainfall and rockiness instead. Parsing that
    as ``0`` would tell the brain Improved Land yields no minerals, which is confidently wrong
    rather than merely absent — the failure this whole plane exists to avoid.
    """

    name: str
    nutrients: int | None
    minerals: int | None
    energy: int | None


@dataclass(frozen=True)
class TunedParameter:
    """One numbered tuning constant — ``#RULES`` and ``#WORLDBUILDER``.

    Both sections are positional like ``#SOCIO``, and for the same reason: nothing in a row names
    itself. The row is bare numbers and the meaning lives in an **inline** ``;`` comment beside
    them, which makes these the only two sections where the file's "comments are line-initial"
    rule does not hold. So ``index`` is the identity and ``label`` is a courtesy — a mod that
    strips the comments still parses, with empty labels, rather than silently renumbering.

    Kept as raw values rather than a named-field record on purpose, and that costs less than it
    looks. Some of these are legible ("Population limit w/o hab complex"), but plenty are engine
    coefficients whose units are undocumented ("Encourages fractal to grow deep water"), and one
    record with ``deep_water_bias: int`` on it would dress the opaque half as understood. The
    file's own gloss travels with the value instead, so a reader sees exactly what the designers
    wrote and no more.

    Both sections are also the cleanest *mod* signal in the file. Thinker's whole balance patch
    is a handful of changed integers here, and a per-index diff against stock finds them with no
    interpretation at all — which is the ``ruleTier`` question asked as arithmetic.
    """

    #: ``"RULES"`` or ``"WORLDBUILDER"``. Index alone is not an identity across two blocks that
    #: both start at 0.
    section: str
    #: Row position within the section, and the real identity. The engine reads these blocks by
    #: offset, and ``label`` is not unique: measured against a real ``alphax.txt``, ``#RULES``
    #: rows 17, 18 and 19 all read "Psi combat offense-to-defense ratio" and are told apart only
    #: by ``note`` ("LAND"/"SEA"/"AIR unit defending"). Keying these by label loses two of them.
    index: int
    #: Text before the comment's parenthesis — "Max artillery range", "Rivers rain mod.".
    label: str
    #: The parenthesised gloss, where the file supplies one. Sometimes the only disambiguator.
    note: str
    #: A tuple, not an int: several rows carry more than one number — the artillery damage
    #: numerator and denominator, the three psi combat ratios, five continent-size ratios.
    values: tuple[int, ...]


@dataclass(frozen=True)
class MoraleLevel:
    """One rung of the morale ladder — ``#MORALE``.

    A lookup, deliberately not a model. The section is seven name pairs and nothing else; every
    *rule* about morale (that Green is the default, that "Very Green" needs a net -1, what a
    rung is worth in combat) lives in the file's prose comment or in the engine, not in these
    rows. Parsing the names and stopping is the honest read.

    It earns its place because morale is reported to a brain as a word. ``#SOCMORALE`` says
    "+2 Morale!" and a unit's status says "Hardened"; without the ordered ladder those are two
    unrelated strings, and with it they compose.

    ``native_name`` is the second column, and it is not a synonym — it is the same rung for
    native life (Hatchling, Larval Mass, … Demon Boil), which is why a Mind Worm's displayed
    "Great Boil" and a Formers squad's "Commando" are the same number.
    """

    #: Row position, which is the engine's own morale value. Green — the default — is 1.
    index: int
    name: str
    native_name: str


@dataclass(frozen=True)
class CombatMode:
    """A weapon or armour damage type — ``#DEFENSEMODES`` / ``#OFFENSEMODES``.

    **The matchup bonuses are deliberately not modelled here.** The file states them in a prose
    comment above the sections ("Projectile weapons receive a bonus against Energy Armor") and
    the rows themselves carry nothing but four display strings — no magnitude, no matrix, not
    even which mode a given weapon *is*. Encoding the matchup from that comment would mint a
    canonical-tier numeric rule out of English we read, which is the one thing this plane exists
    to prevent; and the magnitude is not in the file at any tier, so we could not state it even
    if we wanted to.

    What the rows do give is the vocabulary: three defensive modes, three offensive ones, and
    the abbreviations the UI prints. That is enough to recognise a mode when the engine reports
    one, and it is where the parse stops.
    """

    #: ``"offense"`` or ``"defense"``. The two sections overlap but are not identical —
    #: defence has Binary, offence has Missile — so the pair a mode belongs to is a fact.
    kind: str
    name: str
    #: Name prefix the UI splices onto a component — "Proj-", "Energy-".
    prefix: str
    abbrev: str
    letter: str


@dataclass(frozen=True)
class UnitOrder:
    """One entry from ``#ORDERS`` — a name and its key.

    **This section is not the full order list, and reading it as one would understate what a
    unit can do by about two thirds.** Nine rows here cover movement and sentry; every terraform
    order (farm, mine, road, plant fungus…) is absent. The evidence is in ``#TERRAIN`` rather
    than in any comment: each of its twenty rows carries its *own* two trailing key columns
    ("f, F" for Farm, "R, R" for Road), which is exactly the data these rows carry, and there
    would be no reason for a terrain row to hold a key binding if the order list already did.
    So the engine's order menu is these rows plus ``#TERRAIN``, and :class:`TerraformAction`
    already models the other half.

    ``key`` is a UI binding, not a rule — the file's own comment for the identical ``#TERRAIN``
    columns says changing the text does not change the key mapping. It is parsed because it is
    the only stable identifier a row has (two orders can be renamed by a mod; the key is what
    the engine dispatches on), not because a brain should press it.
    """

    name: str
    key: str


@dataclass(frozen=True)
class Citizen:
    """A citizen type — ``#CITIZENS``. Specialists and the three plain drone/worker/talent rows.

    The section has **two row shapes** and the short one carries no prerequisite column at all:
    the last three rows are ``Drone, Drones`` and nothing more. They are display names for
    ordinary citizens, not specialists that can be assigned, so ``specialist`` records which
    shape a row had rather than leaving a reader to infer it from three zeroed bonuses — which
    would be indistinguishable from a specialist that happens to be worth nothing.

    ``obsoleted_by`` follows the ``#FACILITIES`` "Free" idiom: ``Disable`` in that column means
    *never obsoleted*, not "excluded from the game". Engineer carries it, and reading it as a
    disable would delete the best late-game specialist from the picture.
    """

    name: str
    plural: str
    requires: tuple[str, ...]
    disabled: bool
    #: Tech that retires this specialty. ``None`` means it is never superseded.
    obsoleted_by: str | None
    #: Energy to the economy/reserves column.
    ops: int
    #: Energy to psych.
    psych: int
    #: Energy to labs.
    research: int
    #: False for the trailing drone/worker/talent rows, which are names only.
    specialist: bool


@dataclass(frozen=True)
class SocialEffectLevel:
    """One rung of a social-effect ladder — ``#SOCECONOMY`` … ``#SOCRESEARCH``.

    These tables are what make the numbers on :class:`SocialModel` mean anything. ``#SOCIO``
    says Free Market is ``economy +2``; on its own that is a token a brain can only guess at.
    This section is the game's own gloss of the same +2 — "+1 energy each square!" — written by
    the designers, in the file, at canonical tier. The pair is strictly better than either half:
    the delta is what arithmetic needs, the text is what a decision needs.

    The ladders are also **ragged and asymmetric**, which is itself a fact worth having. Economy
    runs -3..+5, talent runs -1..+1, research -5..+5. A reader assuming a uniform -3..+3 would
    invent rungs at both ends of half the tables.

    ``description`` keeps the file's trailing ``!`` marks verbatim. They are the game's own
    emphasis for a notable rung ("PARADIGM ECONOMY!!"), so stripping them would discard the one
    signal in the row about how much the change matters. ``#SOCTALENT`` level 0 has no text at
    all — an empty string, not a missing rung.
    """

    #: Long-form effect name, matching :data:`SOCIAL_EFFECTS` and ``SocialModel.effects``.
    effect: str
    level: int
    description: str


@dataclass(frozen=True)
class EnergyCategory:
    """One of the three energy allocation buckets — ``#ENERGY``.

    Two display strings, and that is the whole section. Parsed because the allocation is a
    recurring faction-level decision and the brain will be shown these exact words by the
    engine; **not** parsed as a rule, because the rules that matter (the split sums to ten, and
    social efficiency taxes it) are engine behaviour with no representation in this file.
    """

    abbrev: str
    name: str


def _terraform(row: Row) -> TerraformAction:
    land_requires, land_disabled = prereqs(row.get(1))
    sea_requires, sea_disabled = prereqs(row.get(3))
    return TerraformAction(
        land=row.get(0),
        sea=row.get(2),
        rate=row.number(4),
        verb=row.get(5),
        requires=land_requires,
        disabled=land_disabled,
        sea_requires=sea_requires,
        land_only=sea_disabled,
    )


def _yield_value(raw: str) -> int | None:
    """``"*"`` means the engine computes it from the tile; anything else is a count."""
    raw = raw.strip()
    if raw in ("*", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resource(row: Row) -> ResourceYield:
    return ResourceYield(
        name=row.get(0),
        nutrients=_yield_value(row.get(1)),
        minerals=_yield_value(row.get(2)),
        energy=_yield_value(row.get(3)),
    )


def _tuned(section: str, index: int, row: Row) -> TunedParameter:
    """``384, ; Land base        (Seeded land size of a standard world)``

    The only two sections with inline comments, so the split happens here rather than in
    :func:`sections` — doing it globally would truncate every facility effect containing a ``;``,
    which is the trap the module docstring opens with.

    The fields are rejoined before splitting because the comment itself contains commas ("x0, x1,
    x2") and has therefore already been shredded into separate fields by the row split. Anything
    before the ``;`` that is not an integer is dropped rather than defaulted to 0: a mod's stray
    token becoming a silent zero here would look exactly like a deliberately zeroed knob, and
    these knobs are read by offset, so a dropped row would shift every meaning after it.
    """
    text = ", ".join(row.fields)
    data, _, comment = text.partition(";")
    values = tuple(int(v) for v in (p.strip() for p in data.split(",")) if _is_int(v))
    head, _, tail = comment.strip().partition("(")
    note = tail.strip()
    if note.endswith(")"):
        note = note[:-1].strip()
    return TunedParameter(
        section=section, index=index, label=head.strip(), note=note, values=values
    )


def _morale(index: int, row: Row) -> MoraleLevel:
    return MoraleLevel(index=index, name=row.get(0), native_name=row.get(1))


#: Both mode sections share a row shape; only which list a mode belongs to differs.
COMBAT_MODE_SECTIONS: Final[dict[str, str]] = {
    "OFFENSEMODES": "offense",
    "DEFENSEMODES": "defense",
}


def _combat_mode(kind: str, row: Row) -> CombatMode:
    """``Projectile, Proj-, Proj., P`` — four display strings, no rule."""
    return CombatMode(
        kind=kind,
        name=row.get(0),
        prefix=row.get(1),
        abbrev=row.get(2),
        letter=row.get(3),
    )


def _order(row: Row) -> UnitOrder:
    return UnitOrder(name=row.get(0), key=row.get(1))


def _citizen(row: Row) -> Citizen:
    """``singular, plural, preq, obsolete, ops, psych, research, flags`` — or just the first two.

    Arity is the discriminator and it is safe to use here, unlike in ``#FACILITIES``: no citizen
    field can contain a comma, so a seven-column row is a specialist and a two-column row is a
    plain citizen name. Nothing else in the section distinguishes them.
    """
    specialist = len(row.fields) >= 7
    requires, disabled = prereqs(row.get(2))
    obsolete = row.get(3)
    return Citizen(
        name=row.get(0),
        plural=row.get(1),
        requires=requires,
        disabled=disabled,
        # `Disable` here means "never obsoleted", the same inversion `#FACILITIES` uses for its
        # free-with column — not "excluded from the game", which is what the token means in a
        # prerequisite column two fields to the left.
        obsoleted_by=None if obsolete in ("", NO_PREREQ, DISABLED) else obsolete,
        ops=row.number(4),
        psych=row.number(5),
        research=row.number(6),
        specialist=specialist,
    )


def _energy(row: Row) -> EnergyCategory:
    return EnergyCategory(abbrev=row.get(0), name=row.get(1))


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
    #: A list, not a dict: ``#TERRAIN`` has two rows named "Fungus" (remove, and plant).
    terraform: list[TerraformAction] = field(default_factory=list)
    resources: dict[str, ResourceYield] = field(default_factory=dict)
    #: All eleven ``#SOC*`` ladders in one list. Flat rather than nested by effect because the
    #: only access pattern is ``(effect, level) -> text``; see :meth:`effect_meaning`.
    social_levels: list[SocialEffectLevel] = field(default_factory=list)
    citizens: list[Citizen] = field(default_factory=list)
    #: Ordered — a morale level's meaning *is* its position in the ladder.
    morale_levels: list[MoraleLevel] = field(default_factory=list)
    #: Offensive and defensive modes together; ``CombatMode.kind`` separates them. One list
    #: because "Projectile" appears in both and keying by name alone would lose one of them.
    combat_modes: list[CombatMode] = field(default_factory=list)
    orders: list[UnitOrder] = field(default_factory=list)
    energy_categories: list[EnergyCategory] = field(default_factory=list)
    #: Positional, like ``social_models``: ``TunedParameter.index`` is the identity.
    rules: list[TunedParameter] = field(default_factory=list)
    world_builder: list[TunedParameter] = field(default_factory=list)

    def effect_meaning(self, effect: str, level: int) -> str | None:
        """The game's own gloss for a social-effect rung — ``("economy", 2)`` -> "+1 energy each
        square!".

        This is the join that makes ``#SOCIO`` legible. A :class:`SocialModel` carries
        ``("economy", 2)``, and a brain shown only that has to supply the meaning from
        somewhere — which, for a 1999 game, means from recollection. The ladder tables answer it
        from the file.

        ``None`` for an absent rung rather than the nearest one. The ladders are ragged (talent
        runs -1..+1, research -5..+5), so clamping a +3 talent to the +1 text would state a
        consequence the file does not have — and a *plausible* fabricated rule is the failure
        mode this plane exists to prevent, not an obviously wrong one.
        """
        for rung in self.social_levels:
            if rung.effect == effect and rung.level == level:
                return rung.description
        return None

    @property
    def specialists(self) -> list[Citizen]:
        """Citizens that can actually be assigned, excluding the drone/worker/talent names."""
        return [c for c in self.citizens if c.specialist]

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


#: ``#SOCECONOMY`` → ``economy``. The section names are the :data:`SOCIAL_EFFECTS` keys with a
#: ``SOC`` prefix, so the ladders and the ``#SOCIO`` deltas land on the same vocabulary and a
#: model's ``("economy", 2)`` resolves against the table without a second mapping to drift.
SOCIAL_LADDER_SECTIONS: Final[dict[str, str]] = {
    f"SOC{key}": name for key, name in SOCIAL_EFFECTS.items()
}


def _social_level(effect: str, row: Row) -> SocialEffectLevel | None:
    """``-3, Murderous inefficiency`` — a signed level and the game's own gloss.

    Returns ``None`` when the first column is not an integer rather than letting
    :meth:`Row.number` default it to 0, which would file a stray line as the *neutral* rung —
    the one a brain is most likely to read as "nothing happens".
    """
    if not _is_int(row.get(0)):
        return None
    return SocialEffectLevel(
        effect=effect,
        level=int(row.get(0)),
        # Rejoined rather than taken as one column: the descriptions are free text and a mod is
        # free to put a comma in one, exactly as two facility effects already do.
        description=", ".join(f for f in row.fields[1:] if f),
    )


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


#: Weapons and armour share a row shape, so one reader serves both. Everything else with its
#: own shape has its own reader.
#:
#: What remains unparsed is skipped rather than half-read — a partial fact tagged canonical is
#: worse than a missing one — and the omissions below are decisions, not a backlog:
#:
#: - ``#TIMECONTROLS`` — multiplayer wall-clock budgets: *seconds of human thinking time* per
#:   turn, per base, per unit. It is the one numeric section in the file that constrains the
#:   players rather than the game. Nothing in it bears on a legal move, a cost or a yield, so
#:   there is no question a brain could ask that it answers.
#: - ``#COMPASS``, ``#TRIAD``, ``#PLANS``, ``#RESOURCES``, ``#BONUSNAMES``, ``#MANDATE``,
#:   ``#MOOD``, ``#REPUTE``, ``#MIGHT``, ``#DIFF`` — display-name tables, several carrying the
#:   file's own "NOTE TO TRANSLATORS" banner. The things they name are modelled where they are
#:   *used* (``PLAN_ROLES``, ``Chassis.triad_name``); parsing them too would give the same
#:   concept a second spelling and a place for a future mismatch to hide.
#: - ``#WORLDSIZE`` — map dimensions. Useful, but it describes the board rather than the rules,
#:   so it belongs to the world model; its ``|``-separated display names are also a row shape
#:   nothing else in the file has.
#: - ``#FACTIONS``/``#NEWFACTIONS`` — filename-and-search-key pairs pointing at separate faction
#:   ``.txt`` files, per the file's own comment. The faction data is not in here to parse.
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
        elif row.section == "TERRAIN":
            out.terraform.append(_terraform(row))
        elif row.section == "RESOURCEINFO":
            res = _resource(row)
            out.resources[res.name] = res
        elif row.section == "RULES":
            out.rules.append(_tuned("RULES", len(out.rules), row))
        elif row.section == "WORLDBUILDER":
            out.world_builder.append(_tuned("WORLDBUILDER", len(out.world_builder), row))
        elif row.section == "MORALE":
            out.morale_levels.append(_morale(len(out.morale_levels), row))
        elif row.section in COMBAT_MODE_SECTIONS:
            out.combat_modes.append(_combat_mode(COMBAT_MODE_SECTIONS[row.section], row))
        elif row.section == "ORDERS":
            out.orders.append(_order(row))
        elif row.section == "CITIZENS":
            out.citizens.append(_citizen(row))
        elif row.section == "ENERGY":
            out.energy_categories.append(_energy(row))
        elif row.section in SOCIAL_LADDER_SECTIONS:
            rung = _social_level(SOCIAL_LADDER_SECTIONS[row.section], row)
            if rung is not None:
                out.social_levels.append(rung)
        elif row.section in COMPONENT_SECTIONS:
            out.components.append(_component(COMPONENT_SECTIONS[row.section], row))
    out.social_models = _social_rows(socio)
    return out


def parse_file(path: Path | str) -> Datalinks:
    # alphax.txt is a DOS-era file; latin-1 never fails and preserves the bytes.
    return parse(Path(path).read_text(encoding="latin-1"))
