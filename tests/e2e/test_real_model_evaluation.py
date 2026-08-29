from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation_runner import EvaluationAttemptResult, EvaluationRunner
from data_incident_gym.lab import IncidentLab

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.getenv("DIG_RUN_REAL_MODEL_TESTS") != "1",
        reason="set DIG_RUN_REAL_MODEL_TESTS=1 to enable the bounded real-model acceptance gate",
    ),
]

CASE_IDS = (
    "schema_rename_payment_amount",
    "schema_type_change_payment_amount",
)
SAMPLES_PER_CASE = 3
MINIMUM_PASSES_PER_CASE = 2


@dataclass(frozen=True)
class SampleObservation:
    case_id: str
    sample_index: int
    status: str
    run_id: str | None
    artifact_dir: Path | None

    @classmethod
    def from_result(
        cls,
        case_id: str,
        sample_index: int,
        result: EvaluationAttemptResult,
    ) -> SampleObservation:
        return cls(
            case_id=case_id,
            sample_index=sample_index,
            status=result.status.value,
            run_id=result.run_id,
            artifact_dir=result.artifact_dir,
        )


@pytest.mark.asyncio
async def test_real_model_passes_two_of_three_for_each_incident() -> None:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    observations: list[SampleObservation] = []
    cleanup_errors: list[str] = []
    lab = IncidentLab(settings, PROJECT_ROOT)

    for case_id in CASE_IDS:
        try:
            for sample_index in range(1, SAMPLES_PER_CASE + 1):
                try:
                    result = await EvaluationRunner.for_project(
                        settings,
                        diagnostic_settings,
                        PROJECT_ROOT,
                    ).run(case_id)
                except Exception:
                    observations.append(
                        SampleObservation(
                            case_id=case_id,
                            sample_index=sample_index,
                            status="ERROR",
                            run_id=None,
                            artifact_dir=None,
                        )
                    )
                else:
                    observations.append(
                        SampleObservation.from_result(
                            case_id,
                            sample_index,
                            result,
                        )
                    )
        finally:
            try:
                lab.reset(case_id)
            except Exception:
                cleanup_errors.append(case_id)

    assert len(observations) == len(CASE_IDS) * SAMPLES_PER_CASE
    for observation in observations:
        print(
            "M6_SAMPLE "
            f"case_id={observation.case_id} "
            f"sample={observation.sample_index} "
            f"status={observation.status} "
            f"run_id={observation.run_id or 'NONE'}"
        )
        if observation.artifact_dir is not None:
            assert observation.artifact_dir.is_dir()
            assert {
                path.name for path in observation.artifact_dir.iterdir()
            } == set(ARTIFACT_FILENAMES)

    if cleanup_errors:
        pytest.fail(f"incident cleanup failed for: {', '.join(cleanup_errors)}")

    for case_id in CASE_IDS:
        case_samples = tuple(
            item for item in observations if item.case_id == case_id
        )
        assert len(case_samples) == SAMPLES_PER_CASE
        assert sum(item.status == "PASSED" for item in case_samples) >= (
            MINIMUM_PASSES_PER_CASE
        )
