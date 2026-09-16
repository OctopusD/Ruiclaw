import pytest
from pydantic import ValidationError

from ruiclaw.config.schema import EvolutionConfig


def test_evolution_config_defaults_and_camel_case() -> None:
    config = EvolutionConfig.model_validate({
        "memoryReview": {"validTurns": 4},
        "skillReview": {"toolIterations": 9, "minimumCandidateRuns": 3},
    })

    assert config.enabled
    assert config.memory_review.valid_turns == 4
    assert config.skill_review.tool_iterations == 9
    assert config.skill_review.minimum_candidate_runs == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"memoryReview": {"validTurns": 0}},
        {"skillReview": {"toolIterations": 0}},
        {"skillReview": {"minimumCandidateRuns": 0}},
    ],
)
def test_evolution_thresholds_must_be_positive(payload) -> None:
    with pytest.raises(ValidationError):
        EvolutionConfig.model_validate(payload)
