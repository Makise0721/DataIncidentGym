"""Probe-receipt flows: one boundary probe records a blocked gap that binds
unresolved declarations, and the deterministic evaluator accepts the same
receipt contract the kernel enforces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.lab_verifier import (
    ScenarioVerification,
    ScenarioVerificationStatus,
)
from data_incident_gym.profiles import (
    HistoryPoint,
    HistorySeries,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
)
from data_incident_gym.run_context import IncidentBrief
from data_incident_gym.scenarios import load_scenario_spec

RUN_ID = "a" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


def _record(
    evidence_type: EvidenceType,
    source: EvidenceSource,
    subject: str,
    content: object,
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=content,
    )


def _write_context(
    project_root: Path,
    *,
    subjects: tuple[str, ...],
    dbt_exit_code: int,
    schema_relations: tuple[str, ...],
    profile_relations: tuple[str, ...],
    history_relations: tuple[str, ...],
) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True)
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": RUN_ID,
        "dbt_exit_code": dbt_exit_code,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {
            "schema": list(schema_relations),
            "profile": list(profile_relations),
            "history": list(history_relations),
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A pipeline evidence review is requested.",
        subjects=subjects,
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(brief.model_dump_json(), encoding="utf-8")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


def _runner(
    project_root: Path,
    model: FunctionModel,
    tools: object,
) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        project_root,
        model=model,
        tools=tools,
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


def _hypothesis_payload() -> dict[str, object]:
    return {
        "kernel_hypothesis_ids": ["h_type", "h_cast"],
        "kernel_new_hypotheses": [
            {
                "hypothesis_id": "h_type",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            },
            {
                "hypothesis_id": "h_cast",
                "root_cause_code": "TRANSFORMATION_COLUMN_CAST_CHANGED",
            },
        ],
    }


def _insufficient_payload(unresolved: list[dict[str, str]]) -> dict[str, object]:
    return {
        "schema_version": "p1.kernel_decision.v1",
        "status": "INSUFFICIENT_EVIDENCE",
        "run_id": RUN_ID,
        "selected_hypothesis_id": None,
        "assessments": [],
        "claims": [],
        "unresolved_evidence": unresolved,
        "summary": "The decisive evidence is not observable in this run.",
        "recommended_actions": [],
        "confidence": 0.2,
    }


def _evaluate(
    case_id: str,
    result: object,
    *,
    dbt_exit_code: int,
) -> object:
    scenario = load_scenario_spec(case_id)
    expected_failure = scenario.direct_failure is not None
    verification = ScenarioVerification(
        status=(
            ScenarioVerificationStatus.EXPECTED_FAILURE
            if expected_failure
            else ScenarioVerificationStatus.EXPECTED_ANOMALY
        ),
        incident_case_id=case_id,
        run_id=RUN_ID,
        dbt_exit_code=1 if expected_failure else 0,
        failed_nodes=(
            (scenario.direct_failure,) if expected_failure else ()
        ),
        skipped_nodes=(),
        affected_assets=tuple(sorted(scenario.affected_assets)),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )
    return DeterministicEvaluator.evaluate(
        scenario,
        verification,
        result,
        recovery_succeeded=True,
    )


class _DuplicateProfileTools:
    """Raw payments run evidence; the profile relation is not observable."""

    def __init__(self) -> None:
        self.profile_calls = 0
        self._run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status="SUCCEEDED",
                dbt_exit_code=0,
                failed_nodes=(),
                skipped_nodes=(),
            ),
        )
        self._lineage = _record(
            EvidenceType.DBT_LINEAGE,
            EvidenceSource.DBT_MANIFEST,
            "seed.jaffle_shop.raw_payments",
            DbtLineageFact(
                kind="DBT_LINEAGE",
                run_id=RUN_ID,
                node_id="seed.jaffle_shop.raw_payments",
                direction="downstream",
                related_nodes=(
                    DbtLineageNode(
                        node_id="model.jaffle_shop.stg_payments",
                        resource_type="model",
                        name="stg_payments",
                        distance=1,
                    ),
                    DbtLineageNode(
                        node_id="model.jaffle_shop.orders",
                        resource_type="model",
                        name="orders",
                        distance=2,
                    ),
                ),
            ),
        )
        self._schema = _record(
            EvidenceType.RELATION_SCHEMA,
            EvidenceSource.POSTGRES_CATALOG,
            "raw_payments",
            RelationSchemaFact(
                kind="RELATION_SCHEMA",
                run_id=RUN_ID,
                schema_name="analytics",
                relation_name="raw_payments",
                columns=(
                    RelationSchemaColumn(
                        name="id",
                        data_type="integer",
                        nullable=True,
                        ordinal_position=1,
                    ),
                    RelationSchemaColumn(
                        name="order_id",
                        data_type="integer",
                        nullable=True,
                        ordinal_position=2,
                    ),
                ),
            ),
        )

    def lineage_node_candidates(self, subjects: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(subject for subject in subjects if "raw_payments" in subject))

    def get_dbt_run_results(self, _run_id: str):
        return (self._run,)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return ()

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (self._lineage,)

    def get_relation_schema(self, relation_name: str):
        if relation_name == "raw_payments":
            return (self._schema,)
        return ()

    def get_relation_data_profile(self, _relation_name: str):
        self.profile_calls += 1
        return ()

    def get_relation_history(self, _relation_name: str):
        return ()


@pytest.mark.asyncio
async def test_profile_probe_records_receipt_and_binds_declared_gap(
    tmp_path: Path,
) -> None:
    """seq67 shape: profile is blocked, one probe obtains the receipt, the
    INSUFFICIENT decision declares both expected gaps and passes evaluation."""

    _write_context(
        tmp_path,
        subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
        dbt_exit_code=0,
        schema_relations=("raw_payments",),
        profile_relations=(),
        history_relations=(),
    )
    tools = _DuplicateProfileTools()

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {**{"run_id": RUN_ID}, **_hypothesis_payload()},
                        tool_call_id="call-0",
                    ),
                ]
            )
        if "call-1" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_lineage",
                        {
                            "node_id": "seed.jaffle_shop.raw_payments",
                            "direction": "downstream",
                            "kernel_hypothesis_ids": ["h_type", "h_cast"],
                        },
                        tool_call_id="call-1",
                    ),
                ]
            )
        if "call-2" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {
                            "relation_name": "raw_payments",
                            "kernel_hypothesis_ids": ["h_type", "h_cast"],
                        },
                        tool_call_id="call-2",
                    ),
                ]
            )
        if "call-3" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_data_profile",
                        {
                            "relation_name": "raw_payments",
                            "kernel_hypothesis_ids": ["h_type", "h_cast"],
                        },
                        tool_call_id="call-3",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(
                        [
                            {
                                "evidence_kind": "RELATION_DATA_PROFILE",
                                "subject": "raw_payments",
                                "reason_code": "RELATION_NOT_ALLOWED",
                            },
                            {
                                "evidence_kind": "PAYMENT_EVENT_IDENTITY",
                                "subject": "raw_payments",
                                "reason_code": "NOT_OBSERVABLE",
                            },
                        ]
                    ),
                    tool_call_id="final",
                )
            ]
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.metrics.successful_tool_calls == 3
    blocked = tuple(
        gap
        for gap in result.kernel_state.gaps
        if gap.subject == "raw_payments" and gap.error_code == "RELATION_NOT_ALLOWED"
    )
    assert len(blocked) == 1
    assert blocked[0].tool_name == "get_relation_data_profile"
    # The probe never reached the tool layer: no database call happened.
    assert tools.profile_calls == 0
    evaluation = _evaluate(
        "duplicate_payment_coupon_b",
        result,
        dbt_exit_code=0,
    )
    assert evaluation.status is EvaluationStatus.PASSED


class _SchemaTypeBlockedTools:
    """Customers failure evidence; the raw_orders schema is not observable."""

    def __init__(self) -> None:
        self.schema_calls = 0
        self.profile_calls = 0
        self.history_calls = 0
        self._run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status="FAILED",
                dbt_exit_code=1,
                failed_nodes=("model.jaffle_shop.customers",),
                skipped_nodes=(),
            ),
        )
        self._node_error = _record(
            EvidenceType.DBT_NODE_ERROR,
            EvidenceSource.DBT_RUN_RESULTS,
            "model.jaffle_shop.customers",
            DbtNodeErrorFact(
                kind="DBT_NODE_ERROR",
                run_id=RUN_ID,
                node_id="model.jaffle_shop.customers",
                resource_type="model",
                status="error",
                message="cannot cast text to integer",
            ),
        )
        self._lineage = _record(
            EvidenceType.DBT_LINEAGE,
            EvidenceSource.DBT_MANIFEST,
            "model.jaffle_shop.customers",
            DbtLineageFact(
                kind="DBT_LINEAGE",
                run_id=RUN_ID,
                node_id="model.jaffle_shop.customers",
                direction="upstream",
                related_nodes=(
                    DbtLineageNode(
                        node_id="model.jaffle_shop.stg_orders",
                        resource_type="model",
                        name="stg_orders",
                        distance=1,
                    ),
                    DbtLineageNode(
                        node_id="seed.jaffle_shop.raw_orders",
                        resource_type="seed",
                        name="raw_orders",
                        distance=2,
                    ),
                    DbtLineageNode(
                        node_id="seed.jaffle_shop.raw_customers",
                        resource_type="seed",
                        name="raw_customers",
                        distance=2,
                    ),
                ),
            ),
        )
        self._profile = _record(
            EvidenceType.RELATION_DATA_PROFILE,
            EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            "raw_orders",
            RelationDataProfileFact(
                kind="RELATION_DATA_PROFILE",
                run_id=RUN_ID,
                relation_name="raw_orders",
                profile_spec_version="profile_spec.v1",
                profile_spec_sha256="b" * 64,
                snapshot=RelationProfileSnapshot(
                    relation_name="raw_orders",
                    row_count=112,
                    columns=(),
                    relationship_violations=(),
                ),
            ),
        )
        self._history = _record(
            EvidenceType.RELATION_HISTORY,
            EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            "raw_orders",
            RelationHistoryFact(
                kind="RELATION_HISTORY",
                run_id=RUN_ID,
                relation_name="raw_orders",
                profile_spec_version="profile_spec.v1",
                profile_spec_sha256="b" * 64,
                snapshot=RelationHistorySnapshot(
                    relation_name="raw_orders",
                    histories=(
                        HistorySeries(
                            name="order_count_by_day",
                            metric="count",
                            points=(
                                HistoryPoint(
                                    bucket="2018-04-08",
                                    periodic_key="2018-04-08",
                                    value=2,
                                ),
                            ),
                            watermark_column="order_date",
                            watermark_value="2018-04-09",
                        ),
                    ),
                ),
            ),
        )
        self._schema_customers = _record(
            EvidenceType.RELATION_SCHEMA,
            EvidenceSource.POSTGRES_CATALOG,
            "raw_customers",
            RelationSchemaFact(
                kind="RELATION_SCHEMA",
                run_id=RUN_ID,
                schema_name="analytics",
                relation_name="raw_customers",
                columns=(
                    RelationSchemaColumn(
                        name="id",
                        data_type="integer",
                        nullable=True,
                        ordinal_position=1,
                    ),
                ),
            ),
        )

    def get_dbt_run_results(self, _run_id: str):
        return (self._run,)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return (self._node_error,)

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (self._lineage,)

    def get_relation_schema(self, relation_name: str):
        self.schema_calls += 1
        if relation_name == "raw_customers":
            return (self._schema_customers,)
        return ()

    def get_relation_data_profile(self, relation_name: str):
        self.profile_calls += 1
        if relation_name == "raw_orders":
            return (self._profile,)
        return ()

    def get_relation_history(self, relation_name: str):
        self.history_calls += 1
        if relation_name == "raw_orders":
            return (self._history,)
        return ()


@pytest.mark.asyncio
async def test_schema_probe_records_receipt_and_insufficient_finishes_in_budget(
    tmp_path: Path,
) -> None:
    """seq44/59 shape: raw_orders schema is blocked; the model probes once,
    declares both expected gaps, and finishes inside the 8-call budget."""

    _write_context(
        tmp_path,
        subjects=("model.jaffle_shop.customers",),
        dbt_exit_code=1,
        schema_relations=("raw_customers", "raw_payments"),
        profile_relations=("raw_orders", "raw_customers", "raw_payments"),
        history_relations=("raw_orders",),
    )
    tools = _SchemaTypeBlockedTools()

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        steps = (
            (
                "call-0",
                "get_dbt_run_results",
                {"run_id": RUN_ID},
                _hypothesis_payload(),
            ),
            (
                "call-1",
                "get_dbt_node_error",
                {"run_id": RUN_ID, "node_id": "model.jaffle_shop.customers"},
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
            (
                "call-2",
                "get_dbt_lineage",
                {
                    "node_id": "model.jaffle_shop.customers",
                    "direction": "upstream",
                },
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
            (
                "call-3",
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
            (
                "call-4",
                "get_relation_history",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
            (
                "call-5",
                "get_relation_schema",
                {"relation_name": "raw_customers"},
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
            (
                "call-6",
                "get_relation_schema",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_type", "h_cast"]},
            ),
        )
        for call_id, tool_name, arguments, binding in steps:
            if call_id not in sent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **binding},
                            tool_call_id=call_id,
                        )
                    ]
                )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(
                        [
                            {
                                "evidence_kind": "RELATION_SCHEMA",
                                "subject": "raw_orders",
                                "reason_code": "RELATION_NOT_ALLOWED",
                            },
                            {
                                "evidence_kind": "TRANSFORMATION_DEFINITION",
                                "subject": "model.jaffle_shop.stg_orders",
                                "reason_code": "NOT_OBSERVABLE",
                            },
                        ]
                    ),
                    tool_call_id="final",
                )
            ]
        )

    result = await _runner(tmp_path, FunctionModel(scripted), tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.metrics.tool_call_attempts == 7
    assert result.metrics.successful_tool_calls == 6
    # The schema probe was blocked by the controller before any database call.
    assert tools.schema_calls == 1
    assert tools.profile_calls == 1
    assert tools.history_calls == 1
    blocked = tuple(
        gap
        for gap in result.kernel_state.gaps
        if gap.subject == "raw_orders" and gap.error_code == "RELATION_NOT_ALLOWED"
    )
    assert len(blocked) == 1
    assert blocked[0].tool_name == "get_relation_schema"
    evaluation = _evaluate(
        "schema_type_change_order_customer_b",
        result,
        dbt_exit_code=1,
    )
    assert evaluation.status is EvaluationStatus.PASSED


class _RunResultsOnlyTools:
    def __init__(self) -> None:
        self._run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status="SUCCEEDED",
                dbt_exit_code=0,
                failed_nodes=(),
                skipped_nodes=(),
            ),
        )

    def get_dbt_run_results(self, _run_id: str):
        return (self._run,)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return ()

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return ()

    def get_relation_schema(self, _relation_name: str):
        return ()

    def get_relation_data_profile(self, _relation_name: str):
        return ()

    def get_relation_history(self, _relation_name: str):
        return ()


def _ledger_visible_scripted() -> tuple[FunctionModel, list[dict[str, str]]]:
    ledgers: list[dict[str, str]] = []

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {**{"run_id": RUN_ID}, **_hypothesis_payload()},
                        tool_call_id="call-0",
                    ),
                ]
            )
        if not ledgers:
            ledgers.append(
                {
                    "instructions": agent_info.instructions or "",
                    "tools": "\n".join(
                        tool.description or ""
                        for tool in (*agent_info.function_tools, *agent_info.output_tools)
                    ),
                }
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _insufficient_payload(
                        [
                            {
                                "evidence_kind": "PAYMENT_EVENT_IDENTITY",
                                "subject": "raw_payments",
                                "reason_code": "NOT_OBSERVABLE",
                            }
                        ]
                    ),
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted), ledgers


@pytest.mark.asyncio
async def test_first_kernel_prepare_attaches_ledger_with_lineage_candidates(
    tmp_path: Path,
) -> None:
    """The very first prepared request shows provable nodes in exactly one ledger."""

    _write_context(
        tmp_path,
        subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
        dbt_exit_code=0,
        schema_relations=("raw_payments",),
        profile_relations=(),
        history_relations=(),
    )
    tools = _DuplicateProfileTools()
    model, ledgers = _ledger_visible_scripted()
    result = await _runner(tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len(ledgers) == 1
    instructions = ledgers[0]["instructions"]
    assert instructions.count("CURRENT INVESTIGATION LEDGER") == 1
    assert '"provable_lineage_nodes"' in instructions
    assert "seed.jaffle_shop.raw_payments" in instructions
    assert "CURRENT INVESTIGATION LEDGER" not in ledgers[0]["tools"]


@pytest.mark.asyncio
async def test_first_kernel_prepare_attaches_ledger_when_candidates_empty(
    tmp_path: Path,
) -> None:
    """Even without candidates the initial ledger is visible on the first request."""

    _write_context(
        tmp_path,
        subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
        dbt_exit_code=0,
        schema_relations=("raw_payments",),
        profile_relations=(),
        history_relations=(),
    )
    tools = _RunResultsOnlyTools()
    model, ledgers = _ledger_visible_scripted()
    result = await _runner(tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len(ledgers) == 1
    assert '"provable_lineage_nodes":[]' in ledgers[0]["instructions"]
    assert "CURRENT INVESTIGATION LEDGER" not in ledgers[0]["tools"]
