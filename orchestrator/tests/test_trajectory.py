"""The horizon a decision cannot otherwise see (aegis-n8zmq, na-6db).

Most of these are about what the block REFUSES to say. A trajectory that fills a gap with zero,
or reports a slope from one point, hands the model a fabricated history that reads exactly like a
measured one — and unlike a missing block, it will be acted on.
"""

from __future__ import annotations

from neural_amplifier.trajectory import derive

# The real measured Peacekeeper reserve curve from the na-6db brain arm, thinned to the turns a
# faction.tech decision could have looked back at. Used rather than round numbers so the shape
# under test is a shape the game actually produced.
REAL = [
    (106, {"energy_reserves": 673.0, "base_count": 18.0}),
    (116, {"energy_reserves": 588.0, "base_count": 18.0}),
    (121, {"energy_reserves": 402.0, "base_count": 18.0}),
    (126, {"energy_reserves": 185.0, "base_count": 18.0}),
]


def test_a_falling_reserve_reads_as_falling():
    """The whole point. 185 on the way down from 673 is not 185 on the way up from 40, and the
    world view alone cannot tell them apart."""
    block = derive(REAL, 126)["energy_reserves"]
    assert block["now"] == 185.0
    assert block["t-5"] == 402.0
    assert block["t-20"] == 673.0
    assert block["slope_per_turn"] == -24.4


def test_the_same_number_rising_reads_as_rising():
    """The control. Identical `now`, opposite history, and the block must say so — otherwise it
    is decoration that happens to correlate."""
    rising = [(106, {"energy_reserves": 40.0}), (126, {"energy_reserves": 185.0})]
    assert derive(rising, 126)["energy_reserves"]["slope_per_turn"] == 7.25


def test_a_missing_offset_is_ABSENT_not_zero():
    """A gap filled with 0 manufactures a collapse out of an adapter's silence, and it would be
    the most alarming line in the block."""
    short = [(124, {"energy_reserves": 200.0}), (126, {"energy_reserves": 185.0})]
    block = derive(short, 126)["energy_reserves"]
    assert "t-5" not in block and "t-10" not in block and "t-20" not in block
    assert block["now"] == 185.0


def test_a_single_point_has_no_slope_and_does_not_say_zero():
    """A flat metric and an unknown one are different, and 0.0 is the reading a decision would
    most readily act on."""
    block = derive([(126, {"energy_reserves": 185.0})], 126)["energy_reserves"]
    assert "slope_per_turn" not in block
    assert block == {"now": 185.0}


def test_a_metric_absent_from_the_current_turn_gets_no_series():
    """`energy_income` is genuinely unreadable inside the production phase, so base-scope views
    omit it. A history with no present value to compare against is an invented fact."""
    obs = [(106, {"energy_income": 12.0}), (126, {"energy_reserves": 185.0})]
    out = derive(obs, 126)
    assert "energy_income" not in out
    assert "energy_reserves" in out


def test_it_never_looks_forward():
    """A later observation is not history. Reaching for one would let a series describe a turn
    that had not happened when the decision was made."""
    obs = [(126, {"energy_reserves": 185.0}), (140, {"energy_reserves": 1000.0})]
    block = derive(obs, 126)["energy_reserves"]
    assert block["now"] == 185.0
    assert 1000.0 not in block.values()


def test_nearest_at_or_before_fills_a_low_frequency_surface():
    """faction.tech fires every 5-10 turns, so demanding turn 121 exactly would empty the block
    on precisely the surfaces that most need a horizon. Turn 104 legitimately answers for t-20."""
    obs = [(104, {"energy_reserves": 700.0}), (126, {"energy_reserves": 185.0})]
    block = derive(obs, 126)["energy_reserves"]
    assert block["t-20"] == 700.0
    assert block["slope_per_turn"] < 0


def test_an_observation_too_old_for_its_LABEL_is_dropped():
    """A real number under a wrong name is harder to notice than an invented one and just as
    wrong to reason from. Turn 104 is 22 turns before turn 126: it may answer for `t-20` and it
    must NOT answer for `t-5` or `t-10`."""
    obs = [(104, {"energy_reserves": 700.0}), (126, {"energy_reserves": 185.0})]
    block = derive(obs, 126)["energy_reserves"]
    assert "t-5" not in block
    assert "t-10" not in block
    assert block["t-20"] == 700.0


def test_a_flat_metric_reports_a_real_zero_slope():
    """Zero from a measurement is fine — it is zero from an ABSENCE that is forbidden."""
    assert derive(REAL, 126)["base_count"]["slope_per_turn"] == 0.0


def test_non_numbers_and_bools_are_skipped():
    """`True` is an int in Python and would become a 1.0 in a series.

    `scope=None` because the names are invented: this is about TYPE handling, and under the
    default faction filter all three would be dropped for the other reason and the test would
    pass without exercising anything.
    """
    obs = [(120, {"a": True, "b": "x", "c": 3}), (126, {"a": True, "b": "x", "c": 5})]
    assert set(derive(obs, 126, scope=None)) == {"c"}


def test_order_and_duplicates_do_not_matter():
    shuffled = list(reversed(REAL)) + [REAL[0]]
    assert derive(shuffled, 126) == derive(REAL, 126)


def test_it_reaches_the_model_because_it_is_on_the_contract():
    """na-wzw: a name the orchestrator reads that is not on the contract is silently dropped by
    the model parser — it never arrives, and nothing reports that it did not."""
    from neural_amplifier.brain import _SYSTEM
    from neural_amplifier.contract import WorldView

    assert "trajectory" in WorldView.model_fields
    view = WorldView(
        engine="thinker",
        scope="turn",
        turn=126,
        faction="Peacekeepers",
        action_space=[],
        trajectory=derive(REAL, 126),
    )
    assert "slope_per_turn" in view.model_dump_json()
    # And the prompt has to TELL the model what it is, or it is a field nobody reads — the
    # `cited` lesson, twice recorded in this repo.
    assert "trajectory" in _SYSTEM


def test_base_scope_metrics_are_kept_out_of_a_faction_series():
    """The worst thing this module could do: a well-formed, plausible, meaningless number.

    A record stream mixes base-scope metrics from whichever base the engine asked about, so
    stringing them across turns produces a series about a DIFFERENT BASE at each point. Measured
    on the na-6db stream before the filter: `pop_size` came out `now 4, t-5 4, t-10 3, slope 0.1`
    — a tidy growth curve assembled from three unrelated bases, and the slope made it look
    considered.
    """
    mixed = [
        (105, {"energy_reserves": 700.0, "pop_size": 3.0, "mineral_surplus": 2.0}),
        (110, {"energy_reserves": 400.0, "pop_size": 4.0, "mineral_surplus": 5.0}),
        (115, {"energy_reserves": 189.0, "pop_size": 4.0, "mineral_surplus": 5.0}),
    ]
    out = derive(mixed, 115)
    assert "energy_reserves" in out
    assert "pop_size" not in out
    assert "mineral_surplus" not in out


def test_base_scope_can_be_asked_for_explicitly():
    """Opt-in, for a stream the caller knows is one base's."""
    one_base = [(110, {"pop_size": 3.0}), (115, {"pop_size": 4.0})]
    assert "pop_size" in derive(one_base, 115, scope="base")


def test_an_unknown_metric_is_dropped_under_a_scope_filter():
    """The filter's promise is that every series is about one subject, and a name the vocabulary
    does not describe cannot be checked against it."""
    obs = [(110, {"invented_thing": 1.0}), (115, {"invented_thing": 2.0})]
    assert derive(obs, 115) == {}
    assert "invented_thing" in derive(obs, 115, scope=None)


def test_the_real_na6db_stream_yields_only_faction_metrics():
    """The regression on the actual data the defect was found in."""
    import json
    from pathlib import Path

    log = Path(__file__).resolve().parents[2] / "evals" / "runs" / "na-6db" / "brain.faction7.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    out = derive(
        ((r["turn"], r.get("metrics") or {}) for r in records if isinstance(r.get("turn"), int)),
        115,
    )
    assert out, "the stream should produce a series at turn 115"
    base_scope = {"pop_size", "mineral_surplus", "minerals_remaining", "turns_to_completion"}
    assert not base_scope & set(out)
    assert "energy_reserves" in out and "military_units" in out
