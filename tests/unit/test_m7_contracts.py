from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    ArtifactRun,
    ArtifactWriter,
    RecoveryStatus,
)
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    PolicyIdentity,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.lab import ScenarioRun
from data_incident_gym.lab_verifier import ScenarioVerificationStatus
from data_incident_gym.run_context import IncidentBrief
from data_incident_gym.scenarios import (
    P1_M7_SCENARIO_IDS,
    load_scenario_spec,
)

RUN_ID = "a" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


def _write_public_context(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True)
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": RUN_ID,
        "dbt_exit_code": 1,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {
            "schema": ["raw_payments"],
            "profile": ["raw_payments"],
            "history": ["raw_orders"],
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(
        json.dumps(runtime),
        encoding="utf-8",
    )
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A dbt model build failed.",
        subjects=("model.jaffle_shop.stg_payments",),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(
        brief.model_dump_json(),
        encoding="utf-8",
    )


def _static_model_error() -> DiagnosisRunResult:
    policy = PolicyIdentity(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="b" * 64,
        strategy_prompt_version="p1.static.v1",
        strategy_prompt_sha256="c" * 64,
        controller_protocol_version="p1.controller.v1",
        controller_protocol_sha256="d" * 64,
        tool_schema_sha256="e" * 64,
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.MODEL_ERROR,
        evidence_inventory=(),
    )
    return DiagnosisRunResult(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        policy_identity=policy,
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code="MODEL_RUNTIME_ERROR",
                accepted=True,
            ),
            terminal,
        ),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def _not_applicable_evaluation(case_id: str) -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.NOT_APPLICABLE,
            passed=True,
            expected=("NOT_APPLICABLE",),
            actual=("NOT_APPLICABLE",),
            reason_code="NOT_APPLICABLE",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        incident_case_id=case_id,
        run_id=RUN_ID,
        status=EvaluationStatus.PASSED,
        checks=checks,
        failed_check_codes=(),
        answerability="CONFIRMABLE",
        expected_status="CONFIRMED",
    )


def _kernel_evidence() -> tuple[EvidenceRecord, ...]:
    observed_at = datetime(2026, 8, 30, tzinfo=UTC)
    run_results = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=observed_at,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.jaffle_shop.stg_payments",),
            skipped_nodes=("model.jaffle_shop.orders", "model.jaffle_shop.customers"),
        ),
    )
    node_error = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            resource_type="model",
            status="error",
            message='column "amount" has type text',
        ),
    )
    upstream = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="seed.jaffle_shop.raw_payments",
                    resource_type="seed",
                    name="raw_payments",
                    distance=1,
                ),
            ),
        ),
    )
    schema = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject="analytics.raw_payments",
        observed_at=observed_at,
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="amount",
                    data_type="text",
                    nullable=True,
                    ordinal_position=4,
                ),
            ),
        ),
    )
    downstream = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="model.jaffle_shop.stg_payments",
        observed_at=observed_at,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="model.jaffle_shop.stg_payments",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.jaffle_shop.orders",
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.customers",
                    resource_type="model",
                    name="customers",
                    distance=2,
                ),
            ),
        ),
    )
    return run_results, node_error, upstream, schema, downstream


class _KernelEvidenceTools:
    def __init__(self) -> None:
        self._records = _kernel_evidence()

    def get_dbt_run_results(self, _run_id: str):
        return (self._records[0],)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return (self._records[1],)

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (self._records[2 if direction == "upstream" else 4],)

    def get_relation_schema(self, _relation_name: str):
        return (self._records[3],)

    def get_relation_data_profile(self, _relation_name: str):
        return ()

    def get_relation_history(self, _relation_name: str):
        return ()


def _kernel_binding(**extra: object) -> dict[str, object]:
    binding: dict[str, object] = {
        "kernel_hypothesis_ids": [],
        "kernel_new_hypotheses": [],
    }
    mapping = {
        "hypothesis_ids": "kernel_hypothesis_ids",
        "new_hypotheses": "kernel_new_hypotheses",
    }
    for key, value in extra.items():
        binding[mapping.get(key, key)] = value
    return binding


@pytest.mark.asyncio
async def test_kernel_binds_gaps_through_arguments_and_projects_confirmed_result(
    tmp_path: Path,
) -> None:
    _write_public_context(tmp_path)
    records = _kernel_evidence()
    calls = (
        (
            "get_dbt_run_results",
            {"run_id": RUN_ID},
            _kernel_binding(),
        ),
        (
            "get_dbt_node_error",
            {"run_id": RUN_ID, "node_id": "model.jaffle_shop.stg_payments"},
            _kernel_binding(),
        ),
        (
            "get_dbt_lineage",
            {"node_id": "model.jaffle_shop.stg_payments", "direction": "upstream"},
            _kernel_binding(),
        ),
        (
            "get_relation_schema",
            {"relation_name": "raw_payments"},
            _kernel_binding(
                hypothesis_ids=["h_rename", "h_type"],
                new_hypotheses=[
                    {
                        "hypothesis_id": "h_rename",
                        "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                    },
                    {
                        "hypothesis_id": "h_type",
                        "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                    },
                ],
            ),
        ),
        (
            "get_dbt_lineage",
            {"node_id": "model.jaffle_shop.stg_payments", "direction": "downstream"},
            _kernel_binding(
                hypothesis_ids=["h_rename", "h_type"],
            ),
        ),
    )
    final_payload = {
        "schema_version": "p1.kernel_decision.v1",
        "status": "CONFIRMED",
        "run_id": RUN_ID,
        "selected_hypothesis_id": "h_type",
        "assessments": [
            {
                "hypothesis_id": "h_rename",
                "verdict": "REFUTED",
                "evidence_ids": [records[3].evidence_id],
            },
            {
                "hypothesis_id": "h_type",
                "verdict": "SUPPORTED",
                "evidence_ids": [records[1].evidence_id, records[3].evidence_id],
            },
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "evidence_ids": [records[1].evidence_id, records[3].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.stg_payments",
                "evidence_ids": [records[1].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.orders",
                "evidence_ids": [records[4].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.customers",
                "evidence_ids": [records[4].evidence_id],
            },
        ],
        "unresolved_evidence": [],
        "summary": "The payment amount source type changed.",
        "recommended_actions": ["Restore the source contract before the next build."],
        "confidence": 0.9,
    }

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        tool_returns = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if tool_returns < len(calls):
            name, arguments, binding = calls[tool_returns]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        name,
                        {**arguments, **binding},
                        tool_call_id=f"call-{tool_returns}",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    final_payload,
                    tool_call_id="final",
                )
            ]
        )

    settings = SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        settings,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(scripted),
        tools=_KernelEvidenceTools(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.summary == "The payment amount source type changed."
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert result.diagnosis.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert result.diagnosis.evidence_ids == tuple(record.evidence_id for record in records)
    assert result.kernel_state is not None
    assert result.kernel_state.final_status.value == "CONFIRMED"
    assert result.kernel_state.selected_hypothesis_id == "h_type"
    assert [gap.gap_id for gap in result.kernel_state.gaps] == [
        "g_auto_1",
        "g_auto_2",
        "g_auto_3",
        "g_auto_4",
        "g_auto_5",
    ]
    assert result.trace[-2].event_type == "KERNEL_STATE"
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"
    assert all(
        set(event.arguments) <= {"run_id", "node_id", "direction", "relation_name"}
        for event in result.trace
        if event.event_type == "TOOL_CALL"
    )


@pytest.mark.asyncio
async def test_kernel_batches_multiple_business_calls_per_request(tmp_path: Path) -> None:
    _write_public_context(tmp_path)
    records = _kernel_evidence()
    run_results_call = (
        "get_dbt_run_results",
        {"run_id": RUN_ID},
        _kernel_binding(),
    )
    node_error_call = (
        "get_dbt_node_error",
        {"run_id": RUN_ID, "node_id": "model.jaffle_shop.stg_payments"},
        _kernel_binding(),
    )
    upstream_call = (
        "get_dbt_lineage",
        {"node_id": "model.jaffle_shop.stg_payments", "direction": "upstream"},
        _kernel_binding(),
    )
    schema_call = (
        "get_relation_schema",
        {"relation_name": "raw_payments"},
        _kernel_binding(
            hypothesis_ids=["h_rename", "h_type"],
            new_hypotheses=[
                {
                    "hypothesis_id": "h_rename",
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                },
                {
                    "hypothesis_id": "h_type",
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                },
            ],
        ),
    )
    downstream_call = (
        "get_dbt_lineage",
        {"node_id": "model.jaffle_shop.stg_payments", "direction": "downstream"},
        _kernel_binding(
            hypothesis_ids=["h_rename", "h_type"],
        ),
    )
    batches = (
        (run_results_call, node_error_call),
        (upstream_call, schema_call),
        (downstream_call,),
    )
    final_payload = {
        "schema_version": "p1.kernel_decision.v1",
        "status": "CONFIRMED",
        "run_id": RUN_ID,
        "selected_hypothesis_id": "h_type",
        "assessments": [
            {
                "hypothesis_id": "h_rename",
                "verdict": "REFUTED",
                "evidence_ids": [records[3].evidence_id],
            },
            {
                "hypothesis_id": "h_type",
                "verdict": "SUPPORTED",
                "evidence_ids": [records[1].evidence_id, records[3].evidence_id],
            },
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "evidence_ids": [records[1].evidence_id, records[3].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.stg_payments",
                "evidence_ids": [records[1].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.orders",
                "evidence_ids": [records[4].evidence_id],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": "model.jaffle_shop.customers",
                "evidence_ids": [records[4].evidence_id],
            },
        ],
        "unresolved_evidence": [],
        "summary": "The payment amount source type changed.",
        "recommended_actions": ["Restore the source contract before the next build."],
        "confidence": 0.9,
    }
    tool_descriptions: list[str] = []

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        tool_descriptions.append(
            "\n".join(
                f"{tool.name}\n{tool.description or ''}"
                for tool in agent_info.function_tools
            )
        )
        completed = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        emitted = 0
        for batch in batches:
            if completed == emitted:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            name,
                            {**arguments, **binding},
                            tool_call_id=f"call-{emitted + offset}",
                        )
                        for offset, (name, arguments, binding) in enumerate(batch)
                    ]
                )
            emitted += len(batch)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    final_payload,
                    tool_call_id="final",
                )
            ]
        )

    settings = SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        settings,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(scripted),
        tools=_KernelEvidenceTools(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.metrics.model_requests == 4
    assert result.metrics.successful_tool_calls == 5
    assert sum(isinstance(event, ToolTraceEvent) for event in result.trace) == 5
    assert len(tool_descriptions) == 4
    assert "CURRENT INVESTIGATION LEDGER" not in tool_descriptions[0]
    assert '"g_auto_1"' in tool_descriptions[1]
    assert '"CLOSED"' in tool_descriptions[1]
    assert '"provable_relations"' in tool_descriptions[1]
    assert '"g_auto_4"' in tool_descriptions[2]
    assert '"raw_payments"' in tool_descriptions[2]
    assert "incident_case_id" not in tool_descriptions[1]


def test_m7_catalog_has_four_scenarios_and_actual_customer_failure() -> None:
    assert P1_M7_SCENARIO_IDS == (
        "schema_type_change_payment_amount",
        "schema_type_change_order_customer_a",
        "schema_type_change_order_customer_b",
        "order_volume_pattern_a",
    )
    for case_id in P1_M7_SCENARIO_IDS[1:3]:
        scenario = load_scenario_spec(case_id)
        assert scenario.direct_failure == "model.jaffle_shop.customers"
        assert scenario.affected_assets == ("model.jaffle_shop.customers",)


def test_static_and_kernel_share_public_contracts(tmp_path: Path) -> None:
    _write_public_context(tmp_path)
    settings = SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )
    tools = SimpleNamespace()
    static = DiagnosisRunner.for_run(
        RUN_ID,
        settings,
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        tools=tools,
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    kernel = DiagnosisRunner.for_run(
        RUN_ID,
        settings,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        tools=tools,
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    assert static.tool_schema_sha256 != kernel.tool_schema_sha256
    auto_fields = {"kernel_gap_id", "kernel_gap_kind"}
    binding_fields = {"kernel_hypothesis_ids", "kernel_new_hypotheses"}
    assert all(
        not (auto_fields & set(item["parameters"].get("properties", {})))
        for item in static._tool_schema_payload
    )
    assert all(
        not (auto_fields & set(item["parameters"].get("properties", {})))
        and binding_fields <= set(item["parameters"].get("properties", {}))
        for item in kernel._tool_schema_payload
    )
    assert static.final_diagnosis_schema_sha256 == kernel.final_diagnosis_schema_sha256
    assert static.incident_brief == kernel.incident_brief
    assert static.budget == kernel.budget
    assert static.policy_identity.strategy_prompt_sha256 != (
        kernel.policy_identity.strategy_prompt_sha256
    )


def test_static_artifact_writer_persists_exact_six_files_without_kernel_state(
    tmp_path: Path,
) -> None:
    run = _static_model_error()
    evaluation = _not_applicable_evaluation("schema_type_change_payment_amount")
    artifact_run = ArtifactRun(
        incident_case_id="schema_type_change_payment_amount",
        run_id=RUN_ID,
        started_at=datetime(2026, 8, 30, tzinfo=UTC),
        finished_at=datetime(2026, 8, 30, 0, 0, 0, 1000, tzinfo=UTC),
        recovery_status=RecoveryStatus.HEALTHY,
        model_base_url=MODEL_BASE_URL,
        diagnosis_run=run,
        evaluation=evaluation,
    )

    def git_command(argv: list[str], **_: object):
        stdout = "1" * 40 + "\n" if argv[-2:] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    output = ArtifactWriter(tmp_path, run_command=git_command).write(artifact_run)

    assert tuple(path.name for path in sorted(output.iterdir())) == tuple(
        sorted(ARTIFACT_FILENAMES)
    )
    assert "策略：STATIC_SKILL" in (output / "report.md").read_text(encoding="utf-8")
    assert "Kernel 调查状态" not in (output / "report.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_runner_freezes_diagnosis_before_loading_private_facts() -> None:
    calls: list[str] = []
    case_id = "schema_type_change_payment_amount"
    scenario_run = ScenarioRun(
        run_id=RUN_ID,
        artifact_dir=Path(".dig/lab/runs") / RUN_ID,
        verification_status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        dbt_exit_code=1,
    )

    class FakeLab:
        def reset(self, _case_id: str) -> object:
            calls.append("lab.reset")
            return object()

        def prepare(self, _case_id: str) -> object:
            calls.append("lab.prepare")
            return object()

        def build(self, _case_id: str) -> ScenarioRun:
            calls.append("lab.build")
            return scenario_run

        def restore(self, _case_id: str) -> SimpleNamespace:
            calls.append("lab.restore")
            return SimpleNamespace(state="HEALTHY")

    class FakeDiagnosis:
        async def diagnose(self) -> DiagnosisRunResult:
            calls.append("diagnosis.run")
            return _static_model_error()

    def diagnosis_factory(_run_id: str, _strategy: DiagnosticStrategy) -> FakeDiagnosis:
        calls.append("diagnosis.create")
        return FakeDiagnosis()

    def scenario_loader(_case_id: str):
        calls.append("scenario.load_private")
        return load_scenario_spec(case_id)

    def verification_loader(_run_id: str):
        calls.append("verification.load_private")
        return object()

    def evaluator(*_args: object, **_kwargs: object) -> EvaluationResult:
        calls.append("evaluate.frozen_result")
        return _not_applicable_evaluation(case_id)

    class FakeWriter:
        def write(self, _artifact_run: object) -> Path:
            calls.append("artifact.write")
            return Path("artifacts") / RUN_ID

    runner = EvaluationRunner(
        lab=FakeLab(),
        diagnostic_settings=SimpleNamespace(model_base_url=MODEL_BASE_URL),
        diagnosis_factory=diagnosis_factory,
        private_scenario_loader=scenario_loader,
        private_verification_loader=verification_loader,
        evaluator=evaluator,
        artifact_writer=FakeWriter(),
        clock=lambda: datetime(2026, 8, 30, tzinfo=UTC),
    )

    result = await runner.run(case_id, DiagnosticStrategy.STATIC_SKILL)

    assert result.run_id == RUN_ID
    assert calls == [
        "lab.reset",
        "lab.prepare",
        "lab.build",
        "diagnosis.create",
        "diagnosis.run",
        "lab.restore",
        "scenario.load_private",
        "verification.load_private",
        "evaluate.frozen_result",
        "artifact.write",
    ]
