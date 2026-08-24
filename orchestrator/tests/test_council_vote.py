"""Planetary Council candidate routing — na-d1g."""

from neural_amplifier.brain import ScriptedBrain
from neural_amplifier.contract import Choice, Orders, WorldView
from neural_amplifier.orchestrator import Orchestrator
from neural_amplifier.surfaces import APPLIED, NO_AI_PATH, OBSERVED


def _vote() -> WorldView:
    return WorldView.model_validate(
        {
            "schema_version": "0.1",
            "engine": "thinker",
            "scope": "turn",
            "surface_id": "council.vote",
            "turn": 143,
            "faction_id": 7,
            "faction": "University",
            "metrics": {"energy_reserves": 82},
            "action_space": [
                {
                    "id": "vote:2",
                    "action": "Vote for Hive",
                    "candidate_faction_id": 2,
                    "votes": 39,
                },
                {
                    "id": "vote:6",
                    "action": "Vote for Peacekeepers",
                    "candidate_faction_id": 6,
                    "votes": 66,
                },
            ],
        }
    )


def test_candidate_choice_survives_validation_and_reaches_the_decision_record() -> None:
    """Validation rebuilds Orders; the chosen faction id must survive that boundary."""
    brain = ScriptedBrain(
        [Orders(choices=[Choice(action_id="vote:6", reason="largest voting bloc")])]
    )
    result = Orchestrator(brain).decide(_vote())

    assert [choice.action_id for choice in result.orders.choices] == ["vote:6"]
    assert [choice["action_id"] for choice in result.record.chosen] == ["vote:6"]
    assert result.record.adherence_violations == 0


def test_council_vote_is_applied_with_a_deterministic_tier() -> None:
    assert "council.vote" in OBSERVED
    assert "council.vote" in APPLIED
    assert "council.vote" not in NO_AI_PATH
