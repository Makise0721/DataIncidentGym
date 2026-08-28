from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.diagnostic_agent import (
    SYSTEM_PROMPT_SHA256,
    SYSTEM_PROMPT_VERSION,
    DiagnosisRunner,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceToolError,
    EvidenceType,
    RelationSchemaFact,
)

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


def _synthetic_run_results() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject="run",
        observed_at=__import__("datetime").datetime(
            2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.synthetic.node",),
            skipped_nodes=(),
        ),
    )


class _SingleEvidenceTools(NarrowEvidenceTools):
    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_run_results", (run_id,)))
        return (_synthetic_run_results(),)


class _MappedEvidenceTools(NarrowEvidenceTools):
    def __init__(self, responses: dict[str, object]) -> None:
        super().__init__()
        self.responses = responses

    def _response(self, name: str) -> tuple[EvidenceRecord, ...]:
        response = self.responses[name]
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_run_results", (run_id,)))
        return self._response("get_dbt_run_results")

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_node_error", (run_id, node_id)))
        return self._response(f"get_dbt_node_error:{node_id}")

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_relation_schema", (relation_name,)))
        return self._response(f"get_relation_schema:{relation_name}")

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_lineage", (node_id, direction)))
        return self._response(f"get_dbt_lineage:{node_id}:{direction}")


def _record(
    evidence_type: EvidenceType,
    *,
    subject: str,
    node_id: str = "model.synthetic.failed_node",
    direction: str = "downstream",
) -> EvidenceRecord:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    if evidence_type == EvidenceType.DBT_NODE_ERROR:
        content = DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=node_id,
            resource_type="model",
            status="error",
            message="synthetic failure message",
        )
        source = EvidenceSource.DBT_RUN_RESULTS
    elif evidence_type == EvidenceType.RELATION_SCHEMA:
        content = RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="synthetic_schema",
            relation_name=subject,
            columns=(),
        )
        source = EvidenceSource.POSTGRES_CATALOG
    elif evidence_type == EvidenceType.DBT_LINEAGE:
        content = DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=node_id,
            direction=direction,  # type: ignore[arg-type]
            related_nodes=(
                DbtLineageNode(
                    node_id="model.synthetic.downstream",
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
            ),
        )
        source = EvidenceSource.DBT_MANIFEST
    else:
        raise AssertionError(evidence_type)
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=observed_at,
        content=content,
    )


def _output_call(agent_info: AgentInfo, payload: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                agent_info.output_tools[0].name,
                payload,
                tool_call_id="synthetic-output",
            )
        ]
    )


def _confirmed_payload(
    evidence_ids: tuple[str, ...], *, assets: tuple[str, ...] = ("stg_payments", "orders")
) -> dict[str, object]:
    return {
        "status": "CONFIRMED",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "root_cause_code": "SYNTHETIC_ROOT_CAUSE",
        "summary": "Synthetic evidence supports this diagnosis.",
        "affected_assets": assets,
        "evidence_ids": evidence_ids,
        "recommended_actions": ("Collect additional synthetic evidence.",),
        "confidence": 0.8,
    }


def _runner(tmp_path: Path, model: object, tools: object) -> DiagnosisRunner:
    _write_metadata(tmp_path)
    return DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        model=model,  # type: ignore[arg-type]
        tools=tools,  # type: ignore[arg-type]
    )


def _tool_return_count(messages: list[ModelMessage]) -> int:
    return sum(
        isinstance(part, ToolReturnPart)
        for message in messages
        for part in message.parts
    )


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
    assert all(
        isinstance(event.elapsed_ms, int) and event.elapsed_ms >= 0
        for event in result.trace
        if event.event_type == "TOOL_CALL"
    )


def test_diagnostic_agent_exports_the_frozen_prompt_contract() -> None:
    assert SYSTEM_PROMPT_VERSION == "m5.diagnosis.v4"
    assert SYSTEM_PROMPT_SHA256


@pytest.mark.asyncio
async def test_exact_duplicate_call_is_blocked_before_second_m3_execution(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    tools = _SingleEvidenceTools()

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if _tool_return_count(messages) < 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID},
                        tool_call_id=f"call-{_tool_return_count(messages)}",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _diagnosis_payload(),
                    tool_call_id="output",
                )
            ]
        )

    result = await DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        model=FunctionModel(scripted),
        tools=tools,
    ).diagnose(CASE_ID)

    assert [name for name, _ in tools.calls] == ["get_dbt_run_results"]
    assert [event.error_code for event in result.trace if event.event_type == "TOOL_CALL"] == [
        None,
        "DUPLICATE_TOOL_CALL",
    ]


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
    assert runner._model.model_name == "mimo-v2.5"
    assert str(runner._model.provider.client.base_url) == "https://api.xiaomimimo.com/v1/"


@pytest.mark.asyncio
async def test_different_arguments_are_not_false_positive_duplicates(tmp_path: Path) -> None:
    tools = _MappedEvidenceTools(
        {
            "get_dbt_node_error:synthetic_node_a": (),
            "get_dbt_node_error:synthetic_node_b": (),
        }
    )
    calls = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node_a"},
                        tool_call_id="node-a",
                    ),
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node_b"},
                        tool_call_id="node-b",
                    ),
                ]
            )
        return _output_call(agent_info, _diagnosis_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert [arguments[1] for _, arguments in tools.calls] == [
        "synthetic_node_a",
        "synthetic_node_b",
    ]
    assert result.metrics.tool_call_attempts == 2
    assert all(
        event.error_code is None for event in result.trace if event.event_type == "TOOL_CALL"
    )


@pytest.mark.asyncio
async def test_ninth_tool_request_is_rejected_without_entering_m3(tmp_path: Path) -> None:
    tools = _MappedEvidenceTools(
        {
            f"get_relation_schema:synthetic_relation_{index}": EvidenceToolError(
                "synthetic failure"
            )
            for index in range(8)
        }
    )
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        index = model_requests - 1
        if model_requests == 2:
            index = 8
        parts = (
            [
                ToolCallPart(
                    "get_relation_schema",
                    {"relation_name": f"synthetic_relation_{call_index}"},
                    tool_call_id=f"schema-{call_index}",
                )
                for call_index in range(8)
            ]
            if model_requests == 1
            else [
                ToolCallPart(
                    "get_relation_schema",
                    {"relation_name": f"synthetic_relation_{index}"},
                    tool_call_id=f"schema-{index}",
                )
            ]
        )
        return ModelResponse(
            parts=parts
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.status == "MODEL_ERROR"
    assert result.diagnosis.summary == "MODEL_REQUEST_LIMIT"
    assert model_requests == 2
    assert len(tools.calls) == 8
    assert result.metrics.tool_call_attempts == 9
    assert result.metrics.successful_tool_calls == 0
    assert result.trace[-1].error_code == "TOOL_CALL_LIMIT"


@pytest.mark.asyncio
async def test_eighth_model_request_is_allowed_and_ninth_is_not_sent(tmp_path: Path) -> None:
    tools = _MappedEvidenceTools(
        {f"get_relation_schema:synthetic_relation_{index}": () for index in range(8)}
    )
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_relation_schema",
                    {"relation_name": f"synthetic_relation_{model_requests - 1}"},
                    tool_call_id=f"schema-{model_requests}",
                )
            ]
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.summary == "MODEL_REQUEST_LIMIT"
    assert model_requests == 8
    assert len(tools.calls) == 8
    assert result.metrics.model_requests == 8


@pytest.mark.asyncio
async def test_output_validation_retries_exactly_twice_then_model_error(tmp_path: Path) -> None:
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        payload = _diagnosis_payload()
        payload["run_id"] = "b" * 32
        return _output_call(agent_info, payload)

    result = await _runner(
        tmp_path, FunctionModel(scripted), NarrowEvidenceTools()
    ).diagnose(CASE_ID)

    assert model_requests == 3
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert [event.reason_code for event in result.trace if event.event_type == "EVIDENCE_GATE"] == [
        "OUTPUT_SCOPE_MISMATCH",
        "OUTPUT_SCOPE_MISMATCH",
        "OUTPUT_SCOPE_MISMATCH",
    ]


@pytest.mark.asyncio
async def test_unknown_evidence_id_retries_then_fails_closed(tmp_path: Path) -> None:
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        payload = _diagnosis_payload()
        payload["evidence_ids"] = ("ev_" + "b" * 64,)
        return _output_call(agent_info, payload)

    result = await _runner(
        tmp_path, FunctionModel(scripted), NarrowEvidenceTools()
    ).diagnose(CASE_ID)

    assert model_requests == 3
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert all(
        event.reason_code == "UNKNOWN_EVIDENCE_ID"
        for event in result.trace
        if event.event_type == "EVIDENCE_GATE"
    )


@pytest.mark.asyncio
async def test_total_timeout_returns_model_error_with_partial_usage_and_trace(
    tmp_path: Path,
) -> None:
    tools = _MappedEvidenceTools({"get_relation_schema:synthetic_relation": ()})
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        if model_requests == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    )
                ]
            )
        raise TimeoutError("synthetic timeout with secret=TEST_REDACTED_VALUE")

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.summary == "MODEL_TIMEOUT"
    assert result.metrics.model_requests == 1
    assert result.metrics.successful_tool_calls == 1
    assert len(result.trace) == 1


@pytest.mark.asyncio
async def test_evidence_tool_error_exposes_only_stable_code_to_model_and_trace(
    tmp_path: Path,
) -> None:
    tools = _MappedEvidenceTools(
        {
            "get_relation_schema:synthetic_relation": EvidenceToolError(
                "password=TEST_REDACTED_VALUE C:\\synthetic-secret\\probe.txt SELECT synthetic_sql"
            )
        }
    )
    seen_tool_returns: list[object] = []

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        seen_tool_returns.extend(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        if not seen_tool_returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    )
                ]
            )
        return _output_call(agent_info, _diagnosis_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.status == "INSUFFICIENT_EVIDENCE"
    assert seen_tool_returns == ["EVIDENCE_TOOL_ERROR"]
    event = next(event for event in result.trace if event.event_type == "TOOL_CALL")
    assert event.error_code == "EVIDENCE_TOOL_ERROR"


@pytest.mark.asyncio
async def test_testmodel_tool_error_path_is_structured_and_safe(tmp_path: Path) -> None:
    tools = _MappedEvidenceTools(
        {"get_dbt_run_results:unused": EvidenceToolError("synthetic tool failure")}
    )
    model = TestModel(call_tools=["get_dbt_run_results"], custom_output_args=_diagnosis_payload())

    result = await _runner(tmp_path, model, tools).diagnose(CASE_ID)

    assert result.diagnosis.status in {"INSUFFICIENT_EVIDENCE", "MODEL_ERROR"}
    assert all(event.event_type in {"TOOL_CALL", "EVIDENCE_GATE"} for event in result.trace)


@pytest.mark.asyncio
async def test_testmodel_invalid_output_uses_output_retry_budget(tmp_path: Path) -> None:
    payload = _diagnosis_payload()
    payload["run_id"] = "b" * 32
    model = TestModel(call_tools=[], custom_output_args=payload)

    result = await _runner(tmp_path, model, NarrowEvidenceTools()).diagnose(CASE_ID)

    assert result.diagnosis.status == "MODEL_ERROR"
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert len([event for event in result.trace if event.event_type == "EVIDENCE_GATE"]) == 3


@pytest.mark.asyncio
async def test_cross_run_evidence_is_controller_invariant_failure(tmp_path: Path) -> None:
    other_run = "b" * 32
    cross_run = _synthetic_run_results().model_copy(
        update={"run_id": other_run, "content": DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=other_run,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.synthetic.node",),
            skipped_nodes=(),
        )}
    )
    tools = _MappedEvidenceTools({"get_dbt_run_results": (cross_run,)})

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "get_dbt_run_results",
                    {"run_id": RUN_ID},
                    tool_call_id="run-results",
                )
            ]
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.evidence_records == ()
    event = next(event for event in result.trace if event.event_type == "TOOL_CALL")
    assert event.error_code == "RUN_CONTEXT_MISMATCH"


@pytest.mark.asyncio
async def test_incomplete_evidence_types_downgrade_confirmed_to_insufficient(
    tmp_path: Path,
) -> None:
    node = _record(
        EvidenceType.DBT_NODE_ERROR,
        subject="stg_payments",
        node_id="model.synthetic.stg_payments",
    )
    schema = _record(EvidenceType.RELATION_SCHEMA, subject="payments")
    tools = _MappedEvidenceTools(
        {
            "get_dbt_node_error:synthetic_node": (node,),
            "get_relation_schema:synthetic_relation": (schema,),
        }
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node"},
                        tool_call_id="node",
                    ),
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    ),
                ]
            )
        return _output_call(agent_info, _confirmed_payload((node.evidence_id, schema.evidence_id)))

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.status == "INSUFFICIENT_EVIDENCE"
    assert result.diagnosis.summary == "EVIDENCE_TYPES_INCOMPLETE"
    assert result.trace[-1].reason_code == "EVIDENCE_TYPES_INCOMPLETE"


@pytest.mark.asyncio
async def test_unsupported_affected_asset_downgrades_confirmed_to_insufficient(
    tmp_path: Path,
) -> None:
    node = _record(
        EvidenceType.DBT_NODE_ERROR,
        subject="stg_payments",
        node_id="model.synthetic.stg_payments",
    )
    schema = _record(EvidenceType.RELATION_SCHEMA, subject="payments")
    lineage = _record(
        EvidenceType.DBT_LINEAGE,
        subject="stg_payments",
        node_id="model.synthetic.stg_payments",
    )
    tools = _MappedEvidenceTools(
        {
            "get_dbt_node_error:synthetic_node": (node,),
            "get_relation_schema:synthetic_relation": (schema,),
            "get_dbt_lineage:synthetic_node:downstream": (lineage,),
        }
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node"},
                        tool_call_id="node",
                    ),
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    ),
                    ToolCallPart(
                        "get_dbt_lineage",
                        {"node_id": "synthetic_node", "direction": "downstream"},
                        tool_call_id="lineage",
                    ),
                ]
            )
        return _output_call(
            agent_info,
            _confirmed_payload(
                (node.evidence_id, schema.evidence_id, lineage.evidence_id),
                assets=("stg_payments", "customers"),
            ),
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.status == "INSUFFICIENT_EVIDENCE"
    assert result.diagnosis.summary == "AFFECTED_ASSET_UNSUPPORTED"


@pytest.mark.asyncio
async def test_valid_error_schema_and_downstream_lineage_pass_confirmed_gate(
    tmp_path: Path,
) -> None:
    node = _record(
        EvidenceType.DBT_NODE_ERROR,
        subject="stg_payments",
        node_id="model.synthetic.stg_payments",
    )
    schema = _record(EvidenceType.RELATION_SCHEMA, subject="payments")
    lineage = _record(
        EvidenceType.DBT_LINEAGE,
        subject="stg_payments",
        node_id="model.synthetic.stg_payments",
    )
    tools = _MappedEvidenceTools(
        {
            "get_dbt_node_error:synthetic_node": (node,),
            "get_relation_schema:synthetic_relation": (schema,),
            "get_dbt_lineage:synthetic_node:downstream": (lineage,),
        }
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node"},
                        tool_call_id="node",
                    ),
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    ),
                    ToolCallPart(
                        "get_dbt_lineage",
                        {"node_id": "synthetic_node", "direction": "downstream"},
                        tool_call_id="lineage",
                    ),
                ]
            )
        return _output_call(
            agent_info,
            _confirmed_payload((node.evidence_id, schema.evidence_id, lineage.evidence_id)),
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert result.diagnosis.status == "CONFIRMED"
    assert result.trace[-1].reason_code == "CONFIRMED"


@pytest.mark.asyncio
async def test_model_returned_insufficient_contains_no_root_or_assets(tmp_path: Path) -> None:
    payload = _diagnosis_payload()

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        return _output_call(agent_info, payload)

    result = await _runner(
        tmp_path, FunctionModel(scripted), NarrowEvidenceTools()
    ).diagnose(CASE_ID)

    assert result.diagnosis.status == "INSUFFICIENT_EVIDENCE"
    assert result.diagnosis.root_cause_code is None
    assert result.diagnosis.affected_assets == ()


@pytest.mark.asyncio
async def test_trace_contains_no_prompt_completion_hidden_reasoning_secret_path_or_sql(
    tmp_path: Path,
) -> None:
    tools = _MappedEvidenceTools(
        {
            "get_relation_schema:C:\\synthetic-secret\\probe.txt SELECT synthetic_sql "
            "password=TEST_REDACTED_VALUE": ()
        }
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {
                            "relation_name": (
                                "C:\\synthetic-secret\\probe.txt SELECT synthetic_sql "
                                "password=TEST_REDACTED_VALUE"
                            )
                        },
                        tool_call_id="sensitive",
                    )
                ]
            )
        return _output_call(agent_info, _diagnosis_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)
    serialized = result.model_dump_json()

    assert "Investigate incident case" not in serialized
    assert "hidden reasoning" not in serialized
    assert "C:\\synthetic-secret\\probe.txt" not in serialized
    assert "SELECT synthetic_sql" not in serialized
    assert "TEST_REDACTED_VALUE" not in serialized


@pytest.mark.asyncio
async def test_trace_redacts_uri_api_key_and_bearer_credentials(tmp_path: Path) -> None:
    sensitive = (
        "postgresql://synthetic:TEST_REDACTED_VALUE@host "
        "api_key=TEST_REDACTED_VALUE Bearer TEST_REDACTED_VALUE"
    )
    tools = _MappedEvidenceTools({f"get_relation_schema:{sensitive}": ()})

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": sensitive},
                        tool_call_id="credentials",
                    )
                ]
            )
        return _output_call(agent_info, _diagnosis_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    serialized = result.model_dump_json()
    assert "TEST_REDACTED_VALUE" not in serialized
    assert "postgresql://synthetic" not in serialized
    assert "Bearer" not in serialized


@pytest.mark.asyncio
async def test_tool_calls_are_executed_sequentially_in_model_emission_order(tmp_path: Path) -> None:
    tools = _MappedEvidenceTools(
        {
            "get_dbt_run_results": (),
            "get_dbt_node_error:synthetic_node": (),
            "get_relation_schema:synthetic_relation": (),
            "get_dbt_lineage:synthetic_node:upstream": (),
        }
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID},
                        tool_call_id="run-results",
                    ),
                    ToolCallPart(
                        "get_dbt_node_error",
                        {"run_id": RUN_ID, "node_id": "synthetic_node"},
                        tool_call_id="node",
                    ),
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "synthetic_relation"},
                        tool_call_id="schema",
                    ),
                    ToolCallPart(
                        "get_dbt_lineage",
                        {"node_id": "synthetic_node", "direction": "upstream"},
                        tool_call_id="lineage",
                    ),
                ]
            )
        return _output_call(agent_info, _diagnosis_payload())

    await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)

    assert [name for name, _ in tools.calls] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    ]
