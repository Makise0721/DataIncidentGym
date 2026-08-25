from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord

RUN_ID = "a" * 32
CASE_ID = "synthetic_case"


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
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_run_results", (run_id,)))
        return ()

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_node_error", (run_id, node_id)))
        return ()

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_relation_schema", (relation_name,)))
        return ()

    def get_dbt_lineage(
        self, node_id: str, direction: str
    ) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_lineage", (node_id, direction)))
        return ()


def _diagnosis_payload() -> dict[str, object]:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "root_cause_code": None,
        "summary": "The synthetic evidence is insufficient for confirmation.",
        "affected_assets": (),
        "evidence_ids": (),
        "recommended_actions": ("Collect additional synthetic evidence.",),
        "confidence": 0.2,
    }


def _function_model_response(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
) -> ModelResponse:
    if any(isinstance(message, ModelResponse) for message in messages):
        output_tool = agent_info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    _diagnosis_payload(),
                    tool_call_id="synthetic_output",
                )
            ]
        )

    return ModelResponse(
        parts=[
            ToolCallPart(
                "get_dbt_run_results",
                {"run_id": RUN_ID},
                tool_call_id="synthetic_run_results",
            ),
            ToolCallPart(
                "get_dbt_node_error",
                {"run_id": RUN_ID, "node_id": "synthetic_node"},
                tool_call_id="synthetic_node_error",
            ),
            ToolCallPart(
                "get_relation_schema",
                {"relation_name": "synthetic_relation"},
                tool_call_id="synthetic_schema",
            ),
            ToolCallPart(
                "get_dbt_lineage",
                {"node_id": "synthetic_node", "direction": "upstream"},
                tool_call_id="synthetic_lineage",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_runner_registers_exactly_the_four_m3_tools_and_returns_diagnosis(
    tmp_path: Path,
) -> None:
    _write_metadata(tmp_path)
    registration_model = TestModel(
        call_tools=[],
        custom_output_args=_diagnosis_payload(),
    )
    tools = NarrowEvidenceTools()
    registration_runner = DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        model=registration_model,
        tools=tools,
    )
    await registration_runner.diagnose(CASE_ID)

    expected_tool_names = {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
    assert {
        item.name for item in registration_model.last_model_request_parameters.function_tools
    } == expected_tool_names
    assert registration_model.last_model_request_parameters.native_tools == []

    # TestModel 2.34.0 has no custom_input_args, so FunctionModel supplies legal
    # synthetic tool-call arguments while the real agent executes each tool.
    model = FunctionModel(_function_model_response)
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
    assert {name for name, _ in tools.calls} == {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
    assert ("get_dbt_run_results", (RUN_ID,)) in tools.calls
    assert [
        arguments[0]
        for name, arguments in tools.calls
        if name in {"get_dbt_run_results", "get_dbt_node_error"}
    ] == [RUN_ID, RUN_ID]


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
