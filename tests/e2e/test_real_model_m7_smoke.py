from __future__ import annotations

import os

import pytest

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.scenarios import P1_M7_SCENARIO_IDS

SMOKE_CASES = (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
)
SMOKE_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
assert SMOKE_CASES == P1_M7_SCENARIO_IDS
assert len(SMOKE_CASES) * len(SMOKE_STRATEGIES) == 8

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.getenv("DIG_RUN_REAL_MODEL_TESTS") != "1",
        reason="set DIG_RUN_REAL_MODEL_TESTS=1 to authorize exactly the M7 smoke matrix",
    ),
]


@pytest.mark.parametrize("case_id", SMOKE_CASES, ids=SMOKE_CASES)
@pytest.mark.parametrize("strategy", SMOKE_STRATEGIES, ids=lambda value: value.value)
@pytest.mark.asyncio
async def test_m7_real_model_smoke_cell_runs_once(
    case_id: str,
    strategy: DiagnosticStrategy,
) -> None:
    result = await EvaluationRunner.for_project(
        Settings(),
        DiagnosticSettings(),
        PROJECT_ROOT,
    ).run(case_id, strategy)

    print(
        "M7 development smoke; excluded from the formal 94-run denominator: "
        f"case={case_id} strategy={strategy.value} run_id={result.run_id} "
        f"status={result.status.value}"
    )
    assert result.status in {EvaluationStatus.PASSED, EvaluationStatus.FAILED}
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
    assert result.artifact_dir.is_dir()
