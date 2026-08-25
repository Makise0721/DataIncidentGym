from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord

RUN_ID = "0123456789abcdef0123456789abcdef"
CASE_ID = "synthetic_case"
EVIDENCE_ID = "ev_" + "a" * 64


def _write_metadata(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True)
    run_root.joinpath("metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "m2.run.v1",
                "run_id": RUN_ID,
                "incident_case_id": CASE_ID,
                "dbt_exit_code": 1,
                "ground_truth_digest": "a" * 64,
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                },
            }
        ),
        encoding="utf-8",
    )


class NarrowEvidenceTools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append("get_dbt_run_results")
        return ()

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append("get_dbt_node_error")
        return ()

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append("get_relation_schema")
        return ()

    def get_dbt_lineage(
        self, node_id: str, direction: str
    ) -> tuple[EvidenceRecord, ...]:
        self.calls.append("get_dbt_lineage")
        return ()


def _diagnosis_payload() -> dict[str, object]:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "root_cause_code": None,
        "summary": "The synthetic evidence is insufficient for confirmation.",
        "affected_assets": (),
        "evidence_ids": (EVIDENCE_ID,),
        "recommended_actions": ("Collect additional synthetic evidence.",),
        "confidence": 0.2,
    }


@pytest.mark.asyncio
async def test_runner_registers_exactly_the_four_m3_tools_and_returns_diagnosis(
    tmp_path: Path,
) -> None:
    _write_metadata(tmp_path)
    model = TestModel(
        call_tools=["get_relation_schema", "get_dbt_lineage"],
        custom_output_args=_diagnosis_payload(),
    )
    tools = NarrowEvidenceTools()
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        model=model,
        tools=tools,
    )

    result = await runner.diagnose(CASE_ID)

    assert result.diagnosis.incident_case_id == CASE_ID
    assert result.diagnosis.run_id == RUN_ID
    assert set(tools.calls) == {"get_relation_schema", "get_dbt_lineage"}
    assert {
        item.name for item in model.last_model_request_parameters.function_tools
    } == {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
    assert model.last_model_request_parameters.native_tools == []


def test_default_adapter_is_openai_chat_completions_without_a_request(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        tools=NarrowEvidenceTools(),
    )

    assert isinstance(runner._model, OpenAIChatModel)
    assert isinstance(runner._model.provider, OpenAIProvider)
    assert runner._model.model_name == "gemma4:e4b"
    assert str(runner._model.provider.client.base_url) == "http://127.0.0.1:11434/v1/"
