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
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_SHA256,
    SYSTEM_PROMPT_VERSION,
    DiagnosisRunner,
    ModelIdentity,
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
    RelationSchemaColumn,
    RelationSchemaFact,
)

RUN_ID = "a" * 32
CASE_ID = "synthetic_case"
FAILED_NODE = "model.synthetic.stg_payments"
UPSTREAM_NODE = "seed.synthetic.raw_payments"


def _write_metadata(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
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


def _run_results_record(*, run_id: str = RUN_ID) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=run_id,
        observed_at=datetime(2026, 8, 25, 9, tzinfo=UTC),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=run_id,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(FAILED_NODE,),
            skipped_nodes=("model.synthetic.orders", "model.synthetic.customers"),
        ),
    )


def _node_error_record() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=FAILED_NODE,
        observed_at=datetime(2026, 8, 25, 9, 1, tzinfo=UTC),
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=FAILED_NODE,
            resource_type="model",
            status="error",
            message='column "amount" does not exist',
        ),
    )


def _lineage_record(direction: str) -> EvidenceRecord:
    related_nodes = (
        (
            DbtLineageNode(
                node_id=UPSTREAM_NODE,
                resource_type="seed",
                name="raw_payments",
                distance=1,
            ),
        )
        if direction == "upstream"
        else (
            DbtLineageNode(
                node_id="model.synthetic.orders",
                resource_type="model",
                name="orders",
                distance=1,
            ),
            DbtLineageNode(
                node_id="model.synthetic.customers",
                resource_type="model",
                name="customers",
                distance=1,
            ),
        )
    )
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject=FAILED_NODE,
        observed_at=datetime(
            2026, 8, 25, 9, 2 if direction == "upstream" else 9, tzinfo=UTC
        ),
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=FAILED_NODE,
            direction=direction,  # type: ignore[arg-type]
            related_nodes=related_nodes,
        ),
    )


def _schema_record() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject="analytics.raw_payments",
        observed_at=datetime(2026, 8, 25, 9, 3, tzinfo=UTC),
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="id", data_type="integer", nullable=True, ordinal_position=1
                ),
                RelationSchemaColumn(
                    name="order_id", data_type="integer", nullable=True, ordinal_position=2
                ),
                RelationSchemaColumn(
                    name="payment_method", data_type="text", nullable=True, ordinal_position=3
                ),
                RelationSchemaColumn(
                    name="amount", data_type="integer", nullable=True, ordinal_position=4
                ),
            ),
        ),
    )


def _records() -> tuple[EvidenceRecord, ...]:
    return (
        _run_results_record(),
        _node_error_record(),
        _lineage_record("upstream"),
        _schema_record(),
        _lineage_record("downstream"),
    )


class SyntheticEvidenceTools:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        records = _records()
        self.responses: dict[str, object] = {
            "get_dbt_run_results": (records[0],),
            f"get_dbt_node_error:{FAILED_NODE}": (records[1],),
            f"get_dbt_lineage:{FAILED_NODE}:upstream": (records[2],),
            "get_relation_schema:raw_payments": (records[3],),
            f"get_dbt_lineage:{FAILED_NODE}:downstream": (records[4],),
        }
        if responses:
            self.responses.update(responses)

    def _response(self, key: str) -> tuple[EvidenceRecord, ...]:
        response = self.responses.get(key, ())
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


def _insufficient_payload() -> dict[str, object]:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "selected_hypothesis_id": None,
        "assessments": (),
        "claims": (),
        "summary": "The available evidence is insufficient for confirmation.",
        "recommended_actions": ("Collect additional evidence.",),
        "confidence": 0.2,
    }


def _confirmed_payload(
    *,
    root_code: str = "SOURCE_SCHEMA_COLUMN_RENAMED",
    run_id: str = RUN_ID,
) -> dict[str, object]:
    by_type = {record.evidence_type.value: record for record in _records()}
    node_error = by_type["DBT_NODE_ERROR"]
    schema = by_type["RELATION_SCHEMA"]
    lineage = next(
        record
        for record in _records()
        if record.evidence_type is EvidenceType.DBT_LINEAGE
        and record.content.direction == "downstream"
    )
    selected = "h_rename" if root_code == "SOURCE_SCHEMA_COLUMN_RENAMED" else "h_type"
    alternative = "h_type" if selected == "h_rename" else "h_rename"
    return {
        "status": "CONFIRMED",
        "incident_case_id": CASE_ID,
        "run_id": run_id,
        "selected_hypothesis_id": selected,
        "assessments": (
            {
                "hypothesis_id": selected,
                "verdict": "SUPPORTED",
                "evidence_ids": (node_error.evidence_id, schema.evidence_id),
            },
            {
                "hypothesis_id": alternative,
                "verdict": "REFUTED",
                "evidence_ids": (schema.evidence_id,),
            },
        ),
        "claims": (
            {
                "kind": "ROOT_CAUSE",
                "value": root_code,
                "evidence_ids": (node_error.evidence_id, schema.evidence_id),
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": FAILED_NODE,
                "evidence_ids": (node_error.evidence_id,),
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "orders",
                "evidence_ids": (lineage.evidence_id,),
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "customers",
                "evidence_ids": (lineage.evidence_id,),
            },
        ),
        "summary": "The typed evidence supports the selected source schema cause.",
        "recommended_actions": ("Restore the source contract.",),
        "confidence": 0.9,
    }


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


def _intent(gap_id: str, gap_kind: str, **values: object) -> dict[str, object]:
    return {
        "gap_id": gap_id,
        "gap_kind": gap_kind,
        "hypothesis_ids": values.pop("hypothesis_ids", ()),
        "new_hypotheses": values.pop("new_hypotheses", ()),
        **values,
    }


def _full_tool_calls() -> list[tuple[str, dict[str, object]]]:
    return [
        (
            "get_dbt_run_results",
            {"run_id": RUN_ID, "intent": _intent("g_failure", "LOCATE_FAILURE")},
        ),
        (
            "get_dbt_node_error",
            {
                "run_id": RUN_ID,
                "node_id": FAILED_NODE,
                "intent": _intent("g_explain", "EXPLAIN_FAILURE"),
            },
        ),
        (
            "get_dbt_lineage",
            {
                "node_id": FAILED_NODE,
                "direction": "upstream",
                "intent": _intent("g_source", "DISCOVER_SOURCE_RELATION"),
            },
        ),
        (
            "get_relation_schema",
            {
                "relation_name": "raw_payments",
                "intent": _intent(
                    "g_schema",
                    "DISCRIMINATE_SCHEMA",
                    hypothesis_ids=("h_rename", "h_type"),
                    new_hypotheses=(
                        {
                            "hypothesis_id": "h_rename",
                            "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                        },
                        {
                            "hypothesis_id": "h_type",
                            "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                        },
                    ),
                ),
            },
        ),
        (
            "get_dbt_lineage",
            {
                "node_id": FAILED_NODE,
                "direction": "downstream",
                "intent": _intent("g_impact", "MAP_IMPACT"),
            },
        ),
    ]


def _full_scripted_model(
    payload: dict[str, object] | None = None,
) -> FunctionModel:
    calls = _full_tool_calls()
    final_payload = payload or _confirmed_payload()

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        tool_returns = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if tool_returns < len(calls):
            tool_name, arguments = calls[tool_returns]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name,
                        arguments,
                        tool_call_id=f"call-{tool_returns}",
                    )
                ]
            )
        return _output_call(agent_info, final_payload)

    return FunctionModel(scripted)


def _runner(
    tmp_path: Path,
    model: object,
    tools: object,
    *,
    model_identity: ModelIdentity | None = None,
) -> DiagnosisRunner:
    _write_metadata(tmp_path)
    if model_identity is None:
        model_identity = ModelIdentity("pydantic-function", "scripted-kernel-model")
    return DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        model=model,  # type: ignore[arg-type]
        tools=tools,  # type: ignore[arg-type]
        model_identity=model_identity,
    )


def test_prompt_exports_m6_gap_driven_contract() -> None:
    assert SYSTEM_PROMPT_VERSION == "m6.diagnosis.v1"
    assert SYSTEM_PROMPT_SHA256
    assert "InvestigationIntent" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_RENAMED" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED" in SYSTEM_PROMPT
    assert "at least two candidate hypotheses" in SYSTEM_PROMPT
    assert "Ground Truth" not in SYSTEM_PROMPT
    assert "schema_rename_payment_amount" not in SYSTEM_PROMPT
    assert "schema_type_change_payment_amount" not in SYSTEM_PROMPT


def test_injected_model_requires_truthful_runtime_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model_identity"):
        DiagnosisRunner.for_run(
            RUN_ID,
            DiagnosticSettings(_env_file=None),
            tmp_path,
            model=FunctionModel(
                lambda messages, agent_info: _output_call(
                    agent_info, _insufficient_payload()
                )
            ),
            tools=SyntheticEvidenceTools(),
        )


def test_default_adapter_is_openai_chat_completions_without_a_request(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        DiagnosticSettings(_env_file=None),
        project_root=tmp_path,
        tools=SyntheticEvidenceTools(),
    )
    assert isinstance(runner._model, OpenAIChatModel)
    assert isinstance(runner._model.provider, OpenAIProvider)
    assert runner._model.model_name == "mimo-v2.5"
    assert str(runner._model.provider.client.base_url) == "https://api.xiaomimimo.com/v1/"


@pytest.mark.asyncio
async def test_runner_registers_four_tools_and_returns_model_claims(tmp_path: Path) -> None:
    tools = SyntheticEvidenceTools()
    registration_model = TestModel(call_tools=[], custom_output_args=_insufficient_payload())
    registration_runner = _runner(tmp_path, registration_model, tools)
    await registration_runner.diagnose(CASE_ID)
    expected = {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
    assert {
        item.name for item in registration_model.last_model_request_parameters.function_tools
    } == expected
    assert registration_model.last_model_request_parameters.native_tools == []

    model = _full_scripted_model(_confirmed_payload(root_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"))
    result = await _runner(tmp_path, model, tools).diagnose(CASE_ID)
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert result.diagnosis.affected_assets == (FAILED_NODE, "orders", "customers")
    assert result.investigation_state.final_status.value == "CONFIRMED"
    assert result.trace[-1].event_type == "KERNEL_STATE"
    assert [name for name, _ in tools.calls] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
        "get_relation_schema",
        "get_dbt_lineage",
    ]


@pytest.mark.asyncio
async def test_exact_duplicate_call_is_blocked_before_second_m3_execution(tmp_path: Path) -> None:
    tools = SyntheticEvidenceTools()

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        tool_returns = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if tool_returns == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        _full_tool_calls()[0][1],
                        tool_call_id="one",
                    )
                ]
            )
        if tool_returns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {
                            "run_id": RUN_ID,
                            "intent": _intent("g_duplicate", "LOCATE_FAILURE"),
                        },
                        tool_call_id="duplicate",
                    )
                ]
            )
        return _output_call(agent_info, _insufficient_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)
    assert [name for name, _ in tools.calls] == ["get_dbt_run_results"]
    tool_events = [event for event in result.trace if event.event_type == "TOOL_CALL"]
    assert any(event.error_code == "DUPLICATE_TOOL_CALL" for event in tool_events)
    assert result.investigation_state.tool_calls_used == 2
    assert result.investigation_state.final_status.value == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_output_validation_retries_then_model_error(tmp_path: Path) -> None:
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        return _output_call(agent_info, _confirmed_payload(run_id="b" * 32))

    result = await _runner(
        tmp_path, FunctionModel(scripted), SyntheticEvidenceTools()
    ).diagnose(CASE_ID)
    assert model_requests == 3
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert [
        event.reason_code
        for event in result.trace
        if event.event_type == "EVIDENCE_GATE"
    ] == ["DECISION_SCOPE_MISMATCH"] * 3 + ["MODEL_PROTOCOL_ERROR"]


@pytest.mark.asyncio
async def test_model_returned_insufficient_preserves_empty_claims(tmp_path: Path) -> None:
    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        return _output_call(agent_info, _insufficient_payload())

    result = await _runner(
        tmp_path, FunctionModel(scripted), SyntheticEvidenceTools()
    ).diagnose(CASE_ID)
    assert result.diagnosis.status.value == "INSUFFICIENT_EVIDENCE"
    assert result.diagnosis.root_cause_code is None
    assert result.diagnosis.affected_assets == ()
    assert result.diagnosis.evidence_ids == ()
    assert result.investigation_state.gaps == ()


@pytest.mark.asyncio
async def test_timeout_returns_fixed_model_error_with_terminal_state(tmp_path: Path) -> None:
    model_requests = 0

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        raise TimeoutError("synthetic timeout secret=TEST_REDACTED_VALUE")

    result = await _runner(
        tmp_path, FunctionModel(scripted), SyntheticEvidenceTools()
    ).diagnose(CASE_ID)
    assert result.diagnosis.summary == "MODEL_TIMEOUT"
    assert model_requests == 1
    assert result.trace[-1].event_type == "KERNEL_STATE"
    assert result.investigation_state.final_status.value == "MODEL_ERROR"
    assert "TEST_REDACTED_VALUE" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_evidence_tool_error_exposes_only_stable_code_and_blocks_gap(
    tmp_path: Path,
) -> None:
    tools = SyntheticEvidenceTools(
        {"get_dbt_run_results": EvidenceToolError("password=TEST_REDACTED_VALUE")}
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
                        "get_dbt_run_results",
                        _full_tool_calls()[0][1],
                        tool_call_id="run",
                    )
                ]
            )
        return _output_call(agent_info, _insufficient_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)
    assert seen_tool_returns[0] == "EVIDENCE_TOOL_ERROR"
    event = next(event for event in result.trace if event.event_type == "TOOL_CALL")
    assert event.error_code == "EVIDENCE_TOOL_ERROR"
    assert result.investigation_state.gaps[0].status.value == "BLOCKED"
    assert "TEST_REDACTED_VALUE" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_testmodel_output_path_is_structured_and_safe(tmp_path: Path) -> None:
    result = await _runner(
        tmp_path,
        TestModel(call_tools=[], custom_output_args=_insufficient_payload()),
        SyntheticEvidenceTools(),
    ).diagnose(CASE_ID)
    assert result.diagnosis.status.value == "INSUFFICIENT_EVIDENCE"
    assert result.trace[-1].event_type == "KERNEL_STATE"


@pytest.mark.asyncio
async def test_cross_run_evidence_is_blocked_without_entering_inventory(tmp_path: Path) -> None:
    other_run = "b" * 32
    tools = SyntheticEvidenceTools(
        {"get_dbt_run_results": (_run_results_record(run_id=other_run),)}
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        _full_tool_calls()[0][1],
                        tool_call_id="run",
                    )
                ]
            )
        return _output_call(agent_info, _insufficient_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)
    assert result.evidence_records == ()
    assert result.investigation_state.gaps[0].error_code == "RUN_CONTEXT_MISMATCH"
    assert result.investigation_state.evidence_inventory == ()


@pytest.mark.asyncio
async def test_trace_redacts_prompt_completion_credentials_path_and_sql(tmp_path: Path) -> None:
    sensitive = (
        "postgresql://synthetic:TEST_REDACTED_VALUE@host "
        "C:\\synthetic-secret\\probe.txt SELECT synthetic_sql"
    )

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {
                            "relation_name": sensitive,
                            "intent": _intent("g_schema", "DISCRIMINATE_SCHEMA"),
                        },
                        tool_call_id="sensitive",
                    )
                ]
            )
        return _output_call(agent_info, _insufficient_payload())

    result = await _runner(
        tmp_path, FunctionModel(scripted), SyntheticEvidenceTools()
    ).diagnose(CASE_ID)
    serialized = result.model_dump_json()
    assert "TEST_REDACTED_VALUE" not in serialized
    assert "postgresql://synthetic" not in serialized
    assert "C:\\synthetic-secret\\probe.txt" not in serialized
    assert "SELECT synthetic_sql" not in serialized
    assert "Investigate incident case" not in serialized
    assert "hidden reasoning" not in serialized


@pytest.mark.asyncio
async def test_tool_calls_are_executed_sequentially_in_model_emission_order(
    tmp_path: Path,
) -> None:
    tools = SyntheticEvidenceTools()

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(name, arguments, tool_call_id=f"call-{index}")
                    for index, (name, arguments) in enumerate(_full_tool_calls())
                ]
            )
        return _output_call(agent_info, _confirmed_payload())

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose(CASE_ID)
    assert [name for name, _ in tools.calls] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
        "get_relation_schema",
        "get_dbt_lineage",
    ]
    assert all(
        isinstance(event.elapsed_ms, int) and event.elapsed_ms >= 0
        for event in result.trace
        if event.event_type == "TOOL_CALL"
    )
