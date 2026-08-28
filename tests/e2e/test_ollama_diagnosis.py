from __future__ import annotations

import os

import pytest

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import Diagnosis, DiagnosisStatus
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceType
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.ollama,
    pytest.mark.skipif(
        os.getenv("DIG_RUN_OLLAMA_TESTS") != "1",
        reason="set DIG_RUN_OLLAMA_TESTS=1 to enable the local Ollama probe",
    ),
]


@pytest.mark.asyncio
async def test_default_ollama_diagnosis_uses_real_m3_tools_and_strict_output() -> None:
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    lab.reset(CASE_ID)
    try:
        lab.inject(CASE_ID)
        run = lab.build(CASE_ID)
        settings = DiagnosticSettings(_env_file=None)
        runner = DiagnosisRunner.for_run(run.run_id, settings, PROJECT_ROOT)

        result = await runner.diagnose(CASE_ID)

        assert result.metrics.provider == "openai-compatible"
        assert result.metrics.model == "qwen3.5:9b"
        assert result.metrics.successful_tool_calls >= 1
        assert result.metrics.model_requests <= 6
        assert result.metrics.tool_call_attempts <= 8
        assert result.diagnosis.status in {
            DiagnosisStatus.CONFIRMED,
            DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        }
        assert result.diagnosis.incident_case_id == CASE_ID
        assert result.diagnosis.run_id == run.run_id
        assert result.metrics.elapsed_ms <= 300_000
        assert Diagnosis.model_validate(result.diagnosis.model_dump()) == result.diagnosis
        assert {event.tool_name for event in result.trace if event.event_type == "TOOL_CALL"} <= {
            "get_dbt_run_results",
            "get_dbt_node_error",
            "get_relation_schema",
            "get_dbt_lineage",
        }
        if result.diagnosis.status == DiagnosisStatus.CONFIRMED:
            inventory = {record.evidence_id: record for record in result.evidence_records}
            cited = tuple(inventory[evidence_id] for evidence_id in result.diagnosis.evidence_ids)
            assert {record.evidence_type for record in cited} >= {
                EvidenceType.DBT_NODE_ERROR,
                EvidenceType.RELATION_SCHEMA,
                EvidenceType.DBT_LINEAGE,
            }
    finally:
        lab.reset(CASE_ID)
