"""The wire contract — ``docs/contract.md`` as Pydantic models.

The orchestrator speaks only these types and never learns which engine it is
driving (invariant #2). Fields an engine lacks are **omitted**, not faked, so
almost everything outside the core four is optional.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

Scope = Literal["turn", "unit", "base"]
Engine = Literal["thinker", "glsmac"]


class _Model(BaseModel):
    # Engines grow faster than this file; tolerate fields we don't model yet
    # rather than rejecting a world view outright.
    model_config = ConfigDict(extra="allow")


class Trace(_Model):
    """W3C trace context. The adapter is the trace root — the game is the root
    of the causality — and the orchestrator continues it."""

    traceparent: str | None = None


class Handicap(_Model):
    """One declared rule asymmetry (``docs/game-surface.md`` §5)."""

    id: str
    favours: Literal["self", "other", "config"] | None = None
    selected_by: Literal["difficulty", "structural"] | None = None
    detail: str | None = None


class Fairness(_Model):
    """The rule asymmetries in force for this faction.

    Policy is to **record, not neutralise**. An empty ``handicaps`` list is what
    backs an unqualified fair-play claim — never assert one without it.
    """

    slot: Literal["ai", "human"] | None = None
    difficulty: str | None = None
    handicaps: list[Handicap] = Field(default_factory=list)

    def structural(self) -> list[Handicap]:
        """Handicaps nobody selected — the set that needs defending in a result."""
        return [h for h in self.handicaps if h.selected_by == "structural"]


#: How a directive's target relates to its metric. ``increase``/``decrease``/``hold`` are
#: relative to the value when the directive was issued, which is why a directive carries a
#: baseline — without one, "increase reserves" can never be checked against anything.
Comparator = Literal["at_least", "at_most", "increase", "decrease", "hold"]


class Directive(_Model):
    """Standing strategic intent, issued by one decision to steer later ones.

    The point of the type is the constraint it imposes. A long-horizon decision — which tech
    path, which social model — reasons over many turns, and until now its conclusion died with
    the response: ``base.production`` next turn knew nothing about it. A directive is how that
    conclusion survives.

    It is deliberately *not* free prose. ``metric`` must name something in
    :mod:`.metrics`, so every directive is a claim a later turn can confirm or refute. "Keep
    reserves above 100" is checkable; "play aggressively" is not, and the second kind would
    accumulate forever while meaning nothing. ``intent`` carries the human-readable why, which
    a model still needs in order to apply the directive sensibly to a choice the metric alone
    does not settle.

    Validation is at issue time, on purpose. Rejecting an unknown metric while the model that
    wrote it is still in the loop is worth far more than discovering on every subsequent
    decision that a directive cannot be evaluated.
    """

    id: str
    intent: str
    metric: str
    comparator: Comparator
    #: How much this matters, 1–10, higher is more important. The number exists so a minor
    #: decision can weigh its own action against a standing plan instead of either ignoring it
    #: or obeying it absolutely — "save energy for the secret project at priority 7" should lose
    #: to averting a base falling to a native attack and beat finishing a Scout Patrol two
    #: turns sooner.
    #:
    #: Anchors, so the scale means the same thing across decisions that never see each other:
    #: 9–10 survival, the game is lost otherwise; 7–8 a committed plan, break it only for
    #: something urgent; 4–6 a preference worth real cost; 1–3 a tie-breaker.
    priority: int = 5
    #: Required for ``at_least``/``at_most``; meaningless for the relative comparators, which
    #: measure against ``baseline`` instead.
    target: float | None = None
    #: The metric's value when this was issued. Set by the orchestrator, not the model — it is
    #: an observation, and asking a model to report a number it was just shown is an invitation
    #: to paraphrase it wrong.
    baseline: float | None = None
    #: Datalinks entities this directive is about, as grounding fact ids —
    #: ``fac:the-planetary-transit-system`` for a plan to fund that project.
    #:
    #: These are **lookup keys**, not decoration. A game can accumulate hundreds of directives,
    #: and putting all of them in front of every decision would cost more than it could possibly
    #: return — so a decision gets the ones that touch what it is deciding about. Entities are one
    #: half of that; ``metric`` is the other, since it names the resource at stake and an action
    #: that spends energy credits should pull every directive saving them.
    #:
    #: A directive therefore always has at least one pointer into the graph, because ``metric``
    #: is mandatory. ``entities`` is what connects it to the specific thing it is for.
    entities: list[str] = Field(default_factory=list)

    issued_turn: int | None = None
    #: The turn after which this stops applying. ``None`` means it stands until revoked, which
    #: should be rare: a plan with no horizon cannot fail, and one that cannot fail teaches
    #: nothing.
    horizon_turn: int | None = None
    rationale: str | None = None

    def is_relative(self) -> bool:
        return self.comparator in {"increase", "decrease", "hold"}


class DirectiveStatus(_Model):
    """A directive plus what its metric actually reads right now.

    Injected into the world view together, because a directive without its current value is an
    instruction the model has to guess the relevance of. With the value attached it can see
    "reserves at_least 100, now 82, not satisfied" and weigh a purchase against it directly.

    ``satisfied is None`` means **unmeasurable** — the metric was not reported in this world
    view. Kept distinct from ``False`` because they call for opposite responses: unsatisfied is
    something the decision should address, unmeasurable is something the *adapter* should fix,
    and collapsing the two would let a directive that is never evaluated look like one that is
    always passing.
    """

    directive: Directive
    current: float | None = None
    satisfied: bool | None = None
    detail: str | None = None
    #: How this directive was reached from the decision — "hurry:now changes energy_reserves by
    #: -81" at hop 0, or "fund-weather-paradigm → fac:the-weather-paradigm" further out. The
    #: chain is the reasoning: without it a decision has to guess why a plan is in front of it,
    #: and with it the 81 credits are visibly earmarked for a project serving a larger aim.
    via: str | None = None
    #: Steps from the decision. 0 means this decision directly moves what the directive is about.
    hop: int = 0


class Tradeoff(_Model):
    """What taking one action would do to one standing directive.

    This is the part that makes a priority number usable. Telling a base-scope decision "there
    is a priority-7 plan to save energy" and leaving it there asks the model to guess the cost of
    ignoring it. Telling it "hurrying costs 81 credits, which leaves reserves at 1 against a
    directive wanting at least 300, and at +14/turn that is 21 turns of setback" is a comparison
    it can actually make against the value of finishing a Colony Pod seven turns early.

    Computed by the orchestrator from ``Action.effects`` and the directive's metric, so it is
    arithmetic on declared numbers rather than an opinion.
    """

    action_id: str
    directive_id: str
    metric: str
    #: Signed change this action makes to the metric.
    delta: float
    #: The metric's value after taking the action.
    projected: float
    #: True when the directive holds now and would not after this action. The case worth
    #: spending a model's attention on — a directive already violated is not made worse by
    #: being violated again, and one that stays satisfied needs no argument.
    would_violate: bool = False
    #: Turns of setback at the current rate of change, where a rate is known. None when no
    #: rate metric is reported, because a made-up denominator is worse than an absent one.
    setback_turns: float | None = None
    directive_priority: int = 5


class Action(_Model):
    """One legal move. ``id`` is what orders reference."""

    id: str
    action: str
    #: Signed changes this action makes to named metrics (:mod:`.metrics` vocabulary), as the
    #: adapter understands them — ``{"energy_reserves": -81}`` for a purchase.
    #:
    #: Optional and usually absent. Where present it is what lets the orchestrator compute a
    #: real trade-off against a standing directive instead of leaving the model to infer one
    #: from a cost field whose unit it has to guess. Only immediate, known effects belong here;
    #: this is not a place to predict how a decision turns out.
    effects: dict[str, float] | None = None


class PriorChoice(_Model):
    """What this decision's subject was doing on an earlier turn.

    ``tier`` is the half that is easy to leave out and expensive to leave out. A decision
    re-reading its own past reasoning is in a different position from one looking at the
    deterministic tier's answer: the first can ask "do I still believe that", the second cannot,
    because there was no argument — only a default. Collapsing them would have the brain defer to
    a choice nobody made.

    ``None`` means the adapter does not track authorship, which is not the same as
    ``"deterministic"``.
    """

    turn: int
    item: str
    tier: Literal["llm", "deterministic"] | None = None


class WorldView(_Model):
    """Adapter → orchestrator. The complete input to a decision."""

    schema_version: str = SCHEMA_VERSION
    engine: Engine
    scope: Scope
    turn: int
    faction: str

    # Telemetry (optional, additive — an adapter that omits them still works,
    # it just cannot be measured).
    surface_id: str | None = None
    trace: Trace | None = None

    year: int | None = None
    fairness: Fairness | None = None
    action_space: list[Action] = Field(default_factory=list)
    memory: str | None = None

    #: What this subject chose on recent turns, oldest first. Adapter-supplied.
    #:
    #: Production is re-decided every turn — several times per turn at the engine level — and a
    #: stateless brain re-argues it from nothing each time. Every individual choice can be
    #: defensible while the sequence accumulates nothing, because a base that switches target
    #: every few turns finishes neither. This is the cheapest thing that makes a stable choice
    #: possible: a short ring buffer per subject, which the adapter can keep for nothing.
    #:
    #: Deliberately *not* a summary string like ``memory``. The brain has to be able to see that
    #: the last three turns all chose the same item and that it chose them, and a paraphrase
    #: cannot be checked against anything.
    history: list[PriorChoice] | None = None

    #: Retrieved facts and guard advisories the *orchestrator* injects before
    #: the brain call. Not adapter fields — an adapter never sets these; they
    #: live here so the brain sees one object (``knowledge.py``).
    grounding: list[str] | None = None
    advisories: list[str] | None = None

    #: Factions this faction has legitimately met. ``None`` means the adapter
    #: does not report contact, so the fog gate cannot run — see ``fog.py``.
    contacts: list[str] | None = None

    #: Datalinks entities this decision is *about*, as distinct from the entities it
    #: chooses *between*.
    #:
    #: Retrieval keys off action labels, which is right for a surface that picks among
    #: named things — ``base.production`` offers "Colony Pod" and the graph has a node
    #: called "Colony Pod". It is empty for a surface that asks a question about one
    #: entity: ``base.hurry`` offers "Hurry production" / "Do not hurry", neither of
    #: which is in any datalinks, so the whole surface retrieved zero facts and the
    #: brain decided it with no grounding at all (measured: 0.60 stability, the least
    #: stable surface we have).
    #:
    #: The subject is named here rather than dug out of ``economy`` because the
    #: orchestrator must not learn where a particular engine files it (invariant 2).
    #: The adapter knows what its decision is about; this is where it says so.
    subjects: list[str] | None = None

    #: Engine-neutral named measurements, adapter-supplied. The names are the vocabulary in
    #: :mod:`.metrics`; anything else here is ignored rather than rejected, because an adapter
    #: reporting more than we model yet is not an error.
    #:
    #: This is the one place the orchestrator reads numbers by name, and it is safe precisely
    #: because the adapter did the engine-specific work of naming them. Digging the same values
    #: out of ``economy`` would put an engine's field layout inside the orchestrator
    #: (invariant 2).
    metrics: dict[str, float] | None = None

    #: Standing directives with their current measured values. Orchestrator-injected like
    #: ``grounding`` — an adapter never sets these.
    directives: list[DirectiveStatus] | None = None

    #: What each action would cost each standing directive, keyed by ``action_id`` — the
    #: concrete value trade-off a minor decision needs in order to judge whether it outranks a
    #: standing plan. Orchestrator-injected. Only actions that actually affect a directive's
    #: metric appear, so this stays empty on the many decisions where no plan is at stake.
    tradeoffs: list[Tradeoff] | None = None

    # Engine-dependent sections, passed through to the prompt untouched.
    scores: dict[str, Any] | None = None
    economy: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    units: list[dict[str, Any]] | None = None
    bases: list[dict[str, Any]] | None = None
    deltas: list[dict[str, Any]] | None = None

    def action_ids(self) -> set[str]:
        return {a.id for a in self.action_space}

    def traceparent(self) -> str | None:
        """The adapter's W3C trace context, if it sent one.

        Tolerates a raw dict as well as a :class:`Trace`. ``model_copy`` skips
        validation, so a caller can legitimately hold a world view whose
        ``trace`` never went through the parser — and this is read *after* the
        decision, where an ``AttributeError`` would stall the game rather than
        degrade (invariant #9).
        """
        trace = self.trace
        if trace is None:
            return None
        if isinstance(trace, Trace):
            return trace.traceparent
        if isinstance(trace, dict):
            value = trace.get("traceparent")
            return value if isinstance(value, str) else None
        return None

    def fallback_action_id(self) -> str | None:
        """The safe degradation target: ``end_turn`` where present, else the
        first legal action, else nothing."""
        for a in self.action_space:
            if a.action == "end_turn":
                return a.id
        return self.action_space[0].id if self.action_space else None


class Choice(_Model):
    """One selected action, with the reasoning behind it."""

    action_id: str
    reason: str | None = None


class Orders(_Model):
    """Orchestrator → adapter."""

    schema_version: str = SCHEMA_VERSION
    choices: list[Choice] = Field(default_factory=list)
    notes: str | None = None
    degraded: bool = False
    #: Ids of the grounding facts the brain relied on. The only signal that retrieval
    #: influenced the answer rather than merely preceding it — a decision made with
    #: twelve facts all ignored is otherwise indistinguishable from one they drove.
    #: Ids not present in the offered set are discarded on the way to the record, so a
    #: hallucinated citation cannot inflate the measured utilisation.
    cited: list[str] = Field(
        default_factory=list,
        # The description matters: with structured output the model reads the JSON schema, and a
        # bare `array of strings` gets ignored no matter what the system prompt says. Measured —
        # explaining `cited` only in the system prompt left it empty on every run.
        description=(
            "Ids of the grounding facts that actually influenced this decision. Each entry in the"
            " world view's `grounding` list starts with its id, e.g. `unit:colony-pod`. Include a"
            " fact if reading it changed your assessment of an option — whether it supported the"
            " option you chose or helped you rule one out. Omit facts that made no difference, and"
            " never invent an id you were not given."
        ),
    )

    #: Directives this decision wishes to place on later ones. Empty for almost every
    #: decision — a per-base production choice has no business setting faction policy — and
    #: expected only where the world view shows a long horizon to reason over.
    #:
    #: Described here rather than only in the system prompt for the reason recorded on ``cited``
    #: above: with structured output the model reads the schema, and a field explained only in
    #: the prompt stays empty.
    directives: list[Directive] = Field(
        default_factory=list,
        description=(
            "Standing strategic intent to place on FUTURE decisions, when this decision commits"
            " to a plan that later turns must follow to be worth anything. Leave empty unless you"
            " are genuinely setting direction. `metric` must be one of the metric names listed in"
            " the system prompt — a directive naming anything else is discarded, because it could"
            " never be checked. Use at_least/at_most with a `target` for an absolute bound, or"
            " increase/decrease/hold to be measured against the value at issue time. Set"
            " `horizon_turn` to the turn by which it should have been achieved. `intent` explains"
            " the why in one sentence, for a later decision the metric alone will not settle."
        ),
    )

    #: Ids of directives this decision judged it was acting on. The same measurement idea as
    #: ``cited``: a directive nobody references on any decision is steering nothing, and the
    #: only way to know is to ask.
    followed: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of the standing directives in `directives` that influenced this choice. Include"
            " one if it changed which option you picked or ruled one out. Omit directives that"
            " made no difference to this particular decision, and never invent an id."
        ),
    )

    #: Directives this decision knowingly went against. Not a confession — an override is often
    #: correct, and a plan that can never be broken is a plan that loses games. It is recorded so
    #: override rate per directive becomes a number: a priority-7 directive overridden on every
    #: decision was mispriced, and one never overridden may be costing more than it says.
    overrode: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of standing directives this choice knowingly works against — see `tradeoffs` for"
            " what each action costs which directive. Overriding is allowed and sometimes right:"
            " compare the directive's `priority` against how much this particular decision"
            " matters, and if you override, say why in the choice's `reason`."
        ),
    )
