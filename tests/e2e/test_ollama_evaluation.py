from __future__ import annotations

import os

import pytest

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.getenv("DIG_RUN_REAL_MODEL_TESTS") != "1",
        reason="set DIG_RUN_REAL_MODEL_TESTS=1 to enable three real M5 samples",
    ),
]


@pytest.mark.asyncio
async def test_default_model_passes_at_least_two_of_three_independent_evaluations() -> None:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    attempts = []
    lab = IncidentLab(settings, PROJECT_ROOT)
    try:
        for _ in range(3):
            result = await EvaluationRunner.for_project(
                settings,
                diagnostic_settings,
                PROJECT_ROOT,
            ).run(CASE_ID)
            attempts.append(result)
            print(
                "M5_SAMPLE "
                f"run_id={result.run_id} status={result.status.value} "
                f"artifacts=artifacts/{result.run_id}"
            )
    finally:
        lab.reset(CASE_ID)

    assert len(attempts) == 3
    assert len({attempt.run_id for attempt in attempts}) == 3
    assert all(
        {path.name for path in attempt.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
        for attempt in attempts
    )
    assert all(attempt.artifact_dir.is_dir() for attempt in attempts)
    assert sum(attempt.status == EvaluationStatus.PASSED for attempt in attempts) >= 2
