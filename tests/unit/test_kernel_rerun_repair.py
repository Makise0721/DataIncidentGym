"""Task 1 of the rerun-repair plan: offline positive/negative regressions for
seq50/59/67 failure shapes, independent of the original rerun worktree.

Fixtures live in tests/fixtures/kernel_rerun (public briefs and evidence only).
Synthetic seq50 decisions are explicitly marked synthetic; they are not
recovered original model responses. No database, network or model calls.
"""

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
from data_incident_gym.diagnostic_contracts import KernelError
from data_incident_gym.diagnostic_kernel import (
    ClaimEvidence,
    ClaimKind,
    DiagnosticKernel,
    EvidenceGapKind,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    KernelDecision,
    KernelFinalStatus,
)
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.lab_verifier import (
    ScenarioVerification,
    ScenarioVerificationStatus,
)
from data_incident_gym.run_context import IncidentBrief
from data_incident_gym.scenarios import load_scenario_spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kernel_rerun"

RUN_IDS = {
    50: "870de53cf8e67a39cbcc97c0d42884e7",
    59: "d2abcf132c6a3e4d97256035e806c89e",
    67: "c9f54f9edcd39d277583c282e788efb7",
}
CASE_IDS = {
    50: "silent_payment_drop_partition_a",
    59: "schema_type_change_order_customer_b",
    67: "duplicate_payment_coupon_b",
}
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


# ---------------------------------------------------------------- fixtures


def _fixture_records(seq: int) -> tuple[EvidenceRecord, ...]:
    payload = json.loads((FIXTURES / f"seq{seq}_evidence.json").read_text(encoding="utf-8"))
    return tuple(EvidenceRecord.model_validate(item) for item in payload["records"])


def _fixture_brief(seq: int) -> dict[str, object]:
    return json.loads((FIXTURES / f"seq{seq}_brief.json").read_text(encoding="utf-8"))


def test_seq50_original_terminal_reads_back_as_model_timeout() -> None:
    """The original MODEL_ERROR terminal stays untouched as a fixture fact."""

    diagnosis = json.loads((FIXTURES / "seq50_diagnosis.json").read_text(encoding="utf-8"))
    assert diagnosis.get("status") == "MODEL_ERROR"
    assert diagnosis.get("summary") == "MODEL_TIMEOUT"


# ---------------------------------------------------------------- kernel helpers


def _kernel_for(
    seq: int,
    *,
    allowed_root_cause_codes: tuple[str, ...],
    schema: tuple[str, ...],
    profile: tuple[str, ...],
    history: tuple[str, ...],
) -> DiagnosticKernel:
    brief = _fixture_brief(seq)
    return DiagnosticKernel.start(
        run_id=str(RUN_IDS[seq]),
        allowed_root_cause_codes=allowed_root_cause_codes,
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=schema,
        observable_profile_relations=profile,
        observable_history_relations=history,
        incident_subjects=tuple(brief["subjects"]),
        incident_observations=tuple(
            (item["kind"], item["subject"], item["value"]) for item in brief["observations"]
        ),
        lineage_node_candidates=(
            "seed.jaffle_shop.raw_payments",
            "model.jaffle_shop.customers",
        ),
    )


def _accept(
    kernel: DiagnosticKernel,
    *,
    gap_id: str,
    gap_kind: EvidenceGapKind,
    tool_name: str,
    arguments: dict[str, str],
    record: EvidenceRecord,
    hypothesis_ids: tuple[str, ...] = (),
    new_hypotheses: tuple[Hypothesis, ...] = (),
) -> None:
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=gap_kind,
            hypothesis_ids=hypothesis_ids,
            new_hypotheses=new_hypotheses,
        ),
        tool_name=tool_name,
        arguments=arguments,
    )
    kernel.record_tool_result(prepared, (record,))


def _record_by(
    kind: str,
    relation: str | None = None,
    run_id: str | None = None,
) -> EvidenceRecord:
    for seq in (50, 59, 67):
        for record in _fixture_records(seq):
            content = record.content
            if content.kind != kind:
                continue
            if run_id is not None and record.run_id != run_id:
                continue
            if relation is None or getattr(content, "relation_name", None) == relation:
                return record
    raise AssertionError(f"no fixture record for {kind}/{relation}/{run_id}")


# ---------------------------------------------------------------- seq50 synthetic


def _silent_kernel_with_evidence() -> tuple[DiagnosticKernel, EvidenceRecord]:
    run_id = RUN_IDS[50]
    kernel = _kernel_for(
        50,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_BUSINESS_PAYMENT_DECLINE",
        ),
        schema=("raw_payments",),
        profile=("raw_payments", "raw_orders"),
        history=("raw_payments", "raw_orders"),
    )
    hypotheses = (
        Hypothesis(
            hypothesis_id="h_ingestion_loss",
            root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
        ),
        Hypothesis(
            hypothesis_id="h_normal_decline",
            root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
        ),
    )
    run = _record_by("DBT_RUN_RESULTS")
    lineage = _record_by("DBT_LINEAGE")
    _accept(
        kernel,
        gap_id="g_auto_1",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": run_id},
        record=run,
        new_hypotheses=hypotheses,
    )
    profile_payments = _record_by("RELATION_DATA_PROFILE", "raw_payments")
    profile_orders = _record_by("RELATION_DATA_PROFILE", "raw_orders")
    history_payments = _record_by("RELATION_HISTORY", "raw_payments")
    history_orders = _record_by("RELATION_HISTORY", "raw_orders")
    schema_payments = _record_by("RELATION_SCHEMA", "raw_payments")
    steps = (
        (
            "g_auto_2",
            "get_relation_data_profile",
            {"relation_name": "raw_payments"},
            profile_payments,
        ),
        ("g_auto_3", "get_relation_data_profile", {"relation_name": "raw_orders"}, profile_orders),
        ("g_auto_4", "get_relation_history", {"relation_name": "raw_payments"}, history_payments),
        ("g_auto_5", "get_relation_history", {"relation_name": "raw_orders"}, history_orders),
        ("g_auto_6", "get_relation_schema", {"relation_name": "raw_payments"}, schema_payments),
        (
            "g_auto_7",
            "get_dbt_lineage",
            {"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
            lineage,
        ),
    )
    for gap, tool, args, record in steps:
        _accept(
            kernel,
            gap_id=gap,
            gap_kind={
                "get_relation_data_profile": EvidenceGapKind.PROFILE_RELATION,
                "get_relation_history": EvidenceGapKind.COMPARE_HISTORY,
                "get_relation_schema": EvidenceGapKind.DISCRIMINATE_SCHEMA,
                "get_dbt_lineage": EvidenceGapKind.MAP_IMPACT,
            }[tool],
            tool_name=tool,
            arguments=args,
            record=record,
            hypothesis_ids=("h_ingestion_loss", "h_normal_decline"),
        )
    return kernel, lineage


def test_seq50_synthetic_confirm_passes_and_missing_citation_is_rejected() -> None:
    """A synthetic decision citing the real evidence passes; dropping one
    necessary record from the ROOT_CAUSE claim is rejected by the domain rule."""

    run_id = RUN_IDS[50]
    kernel, lineage = _silent_kernel_with_evidence()
    run = _record_by("DBT_RUN_RESULTS")
    payment_profile = _record_by("RELATION_DATA_PROFILE", "raw_payments")
    order_profile = _record_by("RELATION_DATA_PROFILE", "raw_orders")
    payment_history = _record_by("RELATION_HISTORY", "raw_payments")
    order_history = _record_by("RELATION_HISTORY", "raw_orders")

    assets = tuple(
        sorted(
            node.node_id for node in lineage.content.related_nodes if node.resource_type == "model"
        )
    )
    necessary = (run, payment_profile, order_profile, payment_history, order_history)

    def decision(root_evidence: tuple[EvidenceRecord, ...]) -> KernelDecision:
        return KernelDecision(
            status="CONFIRMED",
            run_id=run_id,
            selected_hypothesis_id="h_ingestion_loss",
            assessments=(
                HypothesisAssessment(
                    hypothesis_id="h_ingestion_loss",
                    verdict=HypothesisVerdict.SUPPORTED,
                    evidence_ids=tuple(record.evidence_id for record in root_evidence),
                ),
                HypothesisAssessment(
                    hypothesis_id="h_normal_decline",
                    verdict=HypothesisVerdict.REFUTED,
                    evidence_ids=(lineage.evidence_id,),
                ),
            ),
            claims=(
                ClaimEvidence(
                    kind=ClaimKind.ROOT_CAUSE,
                    value="SOURCE_PAYMENT_INGESTION_LOSS",
                    evidence_ids=tuple(record.evidence_id for record in root_evidence),
                ),
                ClaimEvidence(
                    kind=ClaimKind.AFFECTED_ASSET,
                    value=assets[0],
                    evidence_ids=(lineage.evidence_id,),
                ),
            ),
            summary="SYNTHETIC: settled payment volume is missing source events.",
            recommended_actions=(),
            confidence=0.9,
        )

    # Synthetic legal decision over the real evidence shape: accepted.
    outcome = kernel.finalize(decision(necessary))
    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_PAYMENT_INGESTION_LOSS"

    # Missing one necessary record in the ROOT_CAUSE claim: rejected on a fresh
    # kernel with the same accepted evidence.
    kernel2, _ = _silent_kernel_with_evidence()
    before = kernel2.snapshot(model_requests_used=0)
    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        kernel2.finalize(decision(necessary[:-1]))
    assert kernel2.snapshot(model_requests_used=0) == before


# ---------------------------------------------------------------- seq59 shape


def _order_history_for_59() -> EvidenceRecord:
    """Derive a raw_orders history record for the seq59 run from the seq50
    fixture record (same relation and series), rebuilding identity/digest."""

    base = _record_by("RELATION_HISTORY", "raw_orders", run_id=RUN_IDS[50])
    content = base.content.model_copy(update={"run_id": RUN_IDS[59]})
    return EvidenceRecord.create(
        run_id=RUN_IDS[59],
        evidence_type=base.evidence_type,
        source=base.source,
        subject=base.subject,
        observed_at=base.observed_at,
        content=content,
    )


class _SchemaBTools:
    """Run evidence for schema_type_change_order_customer_b; history optional."""

    def __init__(self, *, include_history: bool) -> None:
        self.include_history = include_history
        self._rid = RUN_IDS[59]

    def get_dbt_run_results(self, _run_id: str):
        return (_record_by("DBT_RUN_RESULTS", run_id=self._rid),)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return (_record_by("DBT_NODE_ERROR", run_id=self._rid),)

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (_record_by("DBT_LINEAGE", run_id=self._rid),)

    def get_relation_data_profile(self, relation_name: str):
        return (_record_by("RELATION_DATA_PROFILE", relation_name, run_id=self._rid),)

    def get_relation_schema(self, relation_name: str):
        return ()

    def get_relation_history(self, relation_name: str):
        if not self.include_history or relation_name != "raw_orders":
            return ()
        return (_order_history_for_59(),)


def _write_context(project_root: Path, seq: int) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_IDS[seq]
    run_root.mkdir(parents=True, exist_ok=True)
    brief = _fixture_brief(seq)
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": RUN_IDS[seq],
        "dbt_exit_code": 1 if seq == 59 else 0,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {
            "schema": (["raw_customers", "raw_payments"] if seq == 59 else ["raw_payments"]),
            "profile": (
                ["raw_orders", "raw_customers", "raw_payments"]
                if seq == 59
                else (["raw_payments", "raw_orders"] if seq == 50 else [])
            ),
            "history": (
                ["raw_orders"]
                if seq == 59
                else (["raw_payments", "raw_orders"] if seq == 50 else [])
            ),
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    incident = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A pipeline evidence review is requested.",
        subjects=tuple(brief["subjects"]),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=tuple(
            {"kind": item["kind"], "subject": item["subject"], "value": item["value"]}
            for item in brief["observations"]
        ),
    )
    (run_root / "incident_brief.json").write_text(incident.model_dump_json(), encoding="utf-8")


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
        RUN_IDS[59],
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        project_root,
        model=model,
        tools=tools,
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


def _evaluate(seq: int, result: object) -> object:
    scenario = load_scenario_spec(CASE_IDS[seq])
    expected_failure = scenario.direct_failure is not None
    verification = ScenarioVerification(
        status=(
            ScenarioVerificationStatus.EXPECTED_FAILURE
            if expected_failure
            else ScenarioVerificationStatus.EXPECTED_ANOMALY
        ),
        incident_case_id=CASE_IDS[seq],
        run_id=str(RUN_IDS[seq]),
        dbt_exit_code=1 if expected_failure else 0,
        failed_nodes=((scenario.direct_failure,) if expected_failure else ()),
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


def _schema_b_scripted(
    *,
    include_history: bool,
) -> FunctionModel:
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
        binding = {
            "kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"],
            "kernel_new_hypotheses": [
                {
                    "hypothesis_id": "h_schema_col_type",
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                },
                {
                    "hypothesis_id": "h_transform_cast",
                    "root_cause_code": "TRANSFORMATION_COLUMN_CAST_CHANGED",
                },
            ],
        }
        steps = [
            ("call-0", "get_dbt_run_results", {"run_id": RUN_IDS[59]}, binding),
            (
                "call-1",
                "get_dbt_node_error",
                {"run_id": RUN_IDS[59], "node_id": "model.jaffle_shop.customers"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
            (
                "call-2",
                "get_dbt_lineage",
                {"node_id": "model.jaffle_shop.customers", "direction": "upstream"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
            (
                "call-3",
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
        ]
        if include_history:
            steps.append(
                (
                    "call-4",
                    "get_relation_history",
                    {"relation_name": "raw_orders"},
                    {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
                )
            )
        steps.append(
            (
                "call-probe",
                "get_relation_schema",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            )
        )
        for call_id, tool_name, arguments, extra in steps:
            if call_id not in sent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                    ]
                )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_IDS[59],
                        "selected_hypothesis_id": None,
                        "assessments": [],
                        "claims": [],
                        "unresolved_evidence": [
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
                        ],
                        "summary": (
                            "The raw_orders schema and the transformation "
                            "definition are unavailable."
                        ),
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_seq59_shape_fails_only_on_missing_history_type(
    tmp_path: Path,
) -> None:
    """Without history the real evaluator fails only REQUIRED_EVIDENCE_TYPES;
    the paired input with history passes and its evidence_ids carry it."""

    _write_context(tmp_path, 59)
    no_history = await _runner(
        tmp_path, _schema_b_scripted(include_history=False), _SchemaBTools(include_history=False)
    ).diagnose()
    assert no_history.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    result_no = _evaluate(59, no_history)
    assert result_no.status is EvaluationStatus.FAILED
    from data_incident_gym.evaluation import EvaluationCheckCode

    assert EvaluationCheckCode.REQUIRED_EVIDENCE_TYPES_PRESENT in (result_no.failed_check_codes)
    assert EvaluationCheckCode.INSUFFICIENCY_GAP_DECLARED not in (result_no.failed_check_codes)

    _write_context(tmp_path, 59)
    with_history = await _runner(
        tmp_path, _schema_b_scripted(include_history=True), _SchemaBTools(include_history=True)
    ).diagnose()
    assert with_history.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE, [
        (type(event).__name__, getattr(event, "reason_code", None))
        for event in with_history.trace[-3:]
    ]
    order_history = _order_history_for_59()
    assert order_history.evidence_id in with_history.diagnosis.evidence_ids
    result_yes = _evaluate(59, with_history)
    assert result_yes.status is EvaluationStatus.PASSED


# ---------------------------------------------------------------- seq67 shape


class _DupBTools:
    """Run evidence for duplicate_payment_coupon_b; profile is not observable."""

    def __init__(self) -> None:
        self._rid = RUN_IDS[67]
        self.profile_calls = 0

    def lineage_node_candidates(self, subjects: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(subject for subject in subjects if "raw_payments" in subject))

    def get_dbt_run_results(self, _run_id: str):
        return (_record_by("DBT_RUN_RESULTS", run_id=self._rid),)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return ()

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (_record_by("DBT_LINEAGE", run_id=self._rid),)

    def get_relation_schema(self, relation_name: str):
        return (_record_by("RELATION_SCHEMA", relation_name, run_id=self._rid),)

    def get_relation_data_profile(self, _relation_name: str):
        self.profile_calls += 1
        return ()

    def get_relation_history(self, _relation_name: str):
        return ()


def _dup_b_scripted(with_identity_declaration: bool) -> FunctionModel:
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
        hypotheses = [
            {
                "hypothesis_id": "h_exact_dup",
                "root_cause_code": "SOURCE_EXACT_PAYMENT_DUPLICATE",
            },
            {
                "hypothesis_id": "h_sem_dup",
                "root_cause_code": "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            },
            {
                "hypothesis_id": "h_legit_split",
                "root_cause_code": "LEGITIMATE_SPLIT_PAYMENT",
            },
        ]
        binding = {
            "kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"],
            "kernel_new_hypotheses": hypotheses,
        }
        steps = (
            ("call-0", "get_dbt_run_results", {"run_id": RUN_IDS[67]}, binding),
            (
                "call-1",
                "get_relation_schema",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
            (
                "call-2",
                "get_dbt_lineage",
                {"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
            (
                "call-3",
                "get_relation_data_profile",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
        )
        for call_id, tool_name, arguments, extra in steps:
            if call_id not in sent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                    ]
                )
        unresolved = [
            {
                "evidence_kind": "RELATION_DATA_PROFILE",
                "subject": "raw_payments",
                "reason_code": "RELATION_NOT_ALLOWED",
            }
        ]
        if with_identity_declaration:
            unresolved.append(
                {
                    "evidence_kind": "PAYMENT_EVENT_IDENTITY",
                    "subject": "raw_payments",
                    "reason_code": "NOT_OBSERVABLE",
                }
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_IDS[67],
                        "selected_hypothesis_id": None,
                        "assessments": [],
                        "claims": [],
                        "unresolved_evidence": unresolved,
                        "summary": "The duplicate profile and channel identity are unavailable.",
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted)


def _runner_for(
    seq: int,
    project_root: Path,
    model: FunctionModel,
    tools: object,
) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(
        RUN_IDS[seq],
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        project_root,
        model=model,
        tools=tools,
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


@pytest.mark.asyncio
async def test_seq67_shape_declaration_matrix(tmp_path: Path) -> None:
    """Only the profile declaration fails the gap gate; adding the identity
    declaration with a public-case basis passes the real evaluator."""

    from data_incident_gym.evaluation import EvaluationCheckCode

    _write_context(tmp_path, 67)
    tools = _DupBTools()
    only_profile = await _runner_for(
        67, tmp_path, _dup_b_scripted(with_identity_declaration=False), tools
    ).diagnose()
    assert only_profile.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    result_single = _evaluate(67, only_profile)
    assert result_single.status is EvaluationStatus.FAILED
    assert EvaluationCheckCode.INSUFFICIENCY_GAP_DECLARED in (result_single.failed_check_codes)

    _write_context(tmp_path, 67)
    both = await _runner_for(
        67, tmp_path, _dup_b_scripted(with_identity_declaration=True), _DupBTools()
    ).diagnose()
    assert both.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    result_both = _evaluate(67, both)
    assert result_both.status is EvaluationStatus.PASSED, [
        c.code.value for c in result_both.checks if not c.passed
    ]


def test_seq67_unfounded_identity_declaration_is_rejected_by_kernel() -> None:
    """A PAYMENT_EVENT_IDENTITY declaration whose subject is not in the public
    incident subjects is rejected by the kernel without any automatic fix."""

    kernel = _kernel_for(
        67,
        allowed_root_cause_codes=(
            "SOURCE_EXACT_PAYMENT_DUPLICATE",
            "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            "LEGITIMATE_SPLIT_PAYMENT",
        ),
        schema=("raw_payments",),
        profile=(),
        history=(),
    )
    run = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[67])
    hypotheses = (
        Hypothesis(
            hypothesis_id="h_exact_dup",
            root_cause_code="SOURCE_EXACT_PAYMENT_DUPLICATE",
        ),
        Hypothesis(
            hypothesis_id="h_sem_dup",
            root_cause_code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        ),
    )
    _accept(
        kernel,
        gap_id="g_run",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_IDS[67]},
        record=run,
        new_hypotheses=hypotheses,
    )
    with pytest.raises(KernelError, match="UNRESOLVED_EVIDENCE_UNBOUND"):
        kernel.finalize(
            KernelDecision(
                status="INSUFFICIENT_EVIDENCE",
                run_id=RUN_IDS[67],
                unresolved_evidence=(
                    {
                        "evidence_kind": "PAYMENT_EVENT_IDENTITY",
                        "subject": "raw_orders",
                        "reason_code": "NOT_OBSERVABLE",
                    },
                ),
                summary="Unfounded identity declaration.",
                recommended_actions=(),
                confidence=0.2,
            )
        )
    after = kernel.snapshot(model_requests_used=0)
    assert after.final_status is None


# ---------------------------------------------------------------- Task 2 ledger

LEDGER_HEADER = "CURRENT INVESTIGATION LEDGER"


def _ledger_payload(description: str) -> dict[str, object]:
    start = description.index(LEDGER_HEADER)
    body = description[start:].splitlines()[1]
    return json.loads(body)


def _request_ledgers(agent_info: AgentInfo) -> dict[str, object]:
    """Ledger surfaces actually handed to the model for this request."""

    instructions = agent_info.instructions or ""
    tool_ledgers = [
        tool.name
        for tool in (*agent_info.function_tools, *agent_info.output_tools)
        if LEDGER_HEADER in (tool.description or "")
    ]
    return {
        "instructions_copies": instructions.count(LEDGER_HEADER),
        "tool_ledgers": tool_ledgers,
        "instructions": instructions,
    }


def _capture_ledger_scripted() -> tuple[FunctionModel, list[dict[str, object]]]:
    captured: list[dict[str, object]] = []

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
        hypotheses = [
            {
                "hypothesis_id": "h_exact_dup",
                "root_cause_code": "SOURCE_EXACT_PAYMENT_DUPLICATE",
            },
            {
                "hypothesis_id": "h_sem_dup",
                "root_cause_code": "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            },
            {
                "hypothesis_id": "h_legit_split",
                "root_cause_code": "LEGITIMATE_SPLIT_PAYMENT",
            },
        ]
        binding = {
            "kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"],
            "kernel_new_hypotheses": hypotheses,
        }
        steps = (
            ("call-0", "get_dbt_run_results", {"run_id": RUN_IDS[67]}, binding),
            (
                "call-1",
                "get_relation_schema",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
            (
                "call-2",
                "get_relation_data_profile",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
        )
        for call_id, tool_name, arguments, extra in steps:
            if call_id not in sent:
                if call_id == "call-1":
                    captured.append(
                        {
                            "stage": "after_run_accepted",
                            **_request_ledgers(agent_info),
                        }
                    )
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                    ]
                )
        captured.append({"stage": "after_blocked_probe", **_request_ledgers(agent_info)})
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_IDS[67],
                        "selected_hypothesis_id": None,
                        "assessments": [],
                        "claims": [],
                        "unresolved_evidence": [
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
                        ],
                        "summary": "The duplicate profile and channel identity are unavailable.",
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted), captured


@pytest.mark.asyncio
async def test_ledger_exposes_evidence_inventory_and_gap_error_codes(
    tmp_path: Path,
) -> None:
    """Each request carries exactly one authoritative ledger in its
    instructions, and it holds accepted evidence (id/type/subject) plus the real
    error_code of blocked gaps from the same kernel state."""

    _write_context(tmp_path, 67)
    tools = _DupBTools()
    model, captured = _capture_ledger_scripted()
    result = await _runner_for(67, tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    run_record = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[67])
    schema_record = _record_by("RELATION_SCHEMA", "raw_payments", run_id=RUN_IDS[67])
    assert len(captured) == 2, captured
    for stage in captured:
        assert stage["instructions_copies"] == 1, stage
        assert stage["tool_ledgers"] == [], stage
    first = _ledger_payload(captured[0]["instructions"])
    first_items = {(item["evidence_id"], item["evidence_type"]) for item in first["evidence"]}
    assert (run_record.evidence_id, "DBT_RUN_RESULTS") in first_items
    second = _ledger_payload(captured[1]["instructions"])
    second_items = {(item["evidence_id"], item["evidence_type"]) for item in second["evidence"]}
    assert (schema_record.evidence_id, "RELATION_SCHEMA") in second_items
    blocked = next(gap for gap in second["gaps"] if gap["tool_name"] == "get_relation_data_profile")
    assert blocked["error_code"] == "RELATION_NOT_ALLOWED"
    assert all("error_code" in gap for gap in first["gaps"])


# ---------------------------------------------------------------- Task 3 recovery


class _SilentLossTools:
    """Real seq50 evidence shape; all relations are observable."""

    def __init__(self) -> None:
        self._rid = RUN_IDS[50]
        self.profile_calls = 0
        self.history_calls = 0

    def lineage_node_candidates(self, subjects: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(subject for subject in subjects if subject.startswith(("seed.", "model.")))
        )

    def get_dbt_run_results(self, _run_id: str):
        return (_record_by("DBT_RUN_RESULTS", run_id=self._rid),)

    def get_dbt_node_error(self, _run_id: str, _node_id: str):
        return ()

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (_record_by("DBT_LINEAGE", run_id=self._rid),)

    def get_relation_schema(self, relation_name: str):
        return (_record_by("RELATION_SCHEMA", relation_name, run_id=self._rid),)

    def get_relation_data_profile(self, relation_name: str):
        self.profile_calls += 1
        return (_record_by("RELATION_DATA_PROFILE", relation_name, run_id=self._rid),)

    def get_relation_history(self, relation_name: str):
        self.history_calls += 1
        return (_record_by("RELATION_HISTORY", relation_name, run_id=self._rid),)


def _collect_retry_text(messages: list[ModelMessage], sink: list[str]) -> None:
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                sink.append(content)


def _silent_loss_steps() -> tuple[tuple[str, str, dict[str, str], dict[str, object]], ...]:
    return (
        (
            "call-0",
            "get_dbt_run_results",
            {"run_id": RUN_IDS[50]},
            {
                "kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"],
                "kernel_new_hypotheses": [
                    {
                        "hypothesis_id": "h_ingestion_loss",
                        "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS",
                    },
                    {
                        "hypothesis_id": "h_normal_decline",
                        "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE",
                    },
                ],
            },
        ),
        (
            "call-1",
            "get_relation_data_profile",
            {"relation_name": "raw_payments"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
        (
            "call-2",
            "get_relation_data_profile",
            {"relation_name": "raw_orders"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
        (
            "call-3",
            "get_relation_history",
            {"relation_name": "raw_payments"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
        (
            "call-4",
            "get_relation_history",
            {"relation_name": "raw_orders"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
        (
            "call-5",
            "get_relation_schema",
            {"relation_name": "raw_payments"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
        (
            "call-6",
            "get_dbt_lineage",
            {"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
            {"kernel_hypothesis_ids": ["h_ingestion_loss", "h_normal_decline"]},
        ),
    )


def _silent_confirm_payload(
    root_evidence: tuple[EvidenceRecord, ...],
    lineage: EvidenceRecord,
    *,
    run_id: str,
    selected: str = "h_ingestion_loss",
) -> dict[str, object]:
    assets = tuple(
        sorted(
            node.node_id for node in lineage.content.related_nodes if node.resource_type == "model"
        )
    )
    return {
        "schema_version": "p1.kernel_decision.v1",
        "status": "CONFIRMED",
        "run_id": run_id,
        "selected_hypothesis_id": selected,
        "assessments": [
            {
                "hypothesis_id": selected,
                "verdict": "SUPPORTED",
                "evidence_ids": [record.evidence_id for record in root_evidence],
            },
            {
                "hypothesis_id": "h_normal_decline",
                "verdict": "REFUTED",
                "evidence_ids": [lineage.evidence_id],
            },
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_PAYMENT_INGESTION_LOSS",
                "evidence_ids": [record.evidence_id for record in root_evidence],
            },
            {
                "kind": "AFFECTED_ASSET",
                "value": assets[0],
                "evidence_ids": [lineage.evidence_id],
            },
        ],
        "unresolved_evidence": [],
        "summary": "SYNTHETIC corrected confirm over the seq50 evidence shape.",
        "recommended_actions": [],
        "confidence": 0.9,
    }


def _silent_recovery_scripted(
    *,
    necessary: tuple[EvidenceRecord, ...],
    lineage: EvidenceRecord,
    retry_text: list[str],
    timeout_after_rejection: bool = False,
) -> FunctionModel:
    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        _collect_retry_text(messages, retry_text)
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        steps = _silent_loss_steps()
        pending_batches = (steps[:4], steps[4:])
        for batch in pending_batches:
            unsent = [step for step in batch if step[0] not in sent]
            if unsent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                        for call_id, tool_name, arguments, extra in unsent
                    ]
                )
        if "final-1" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        agent_info.output_tools[0].name,
                        _silent_confirm_payload(necessary[:-1], lineage, run_id=RUN_IDS[50]),
                        tool_call_id="final-1",
                    )
                ]
            )
        if timeout_after_rejection:
            raise TimeoutError("controlled timeout after rejection")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _silent_confirm_payload(necessary, lineage, run_id=RUN_IDS[50]),
                    tool_call_id="final-2",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_seq50_rejected_confirm_recovers_with_corrected_citation(
    tmp_path: Path,
) -> None:
    """A ROOT_CAUSE claim missing one necessary record is rejected; after the
    corrective retry the same evidence supports CONFIRMED without any state or
    budget change from the rejection."""

    _write_context(tmp_path, 50)
    tools = _SilentLossTools()
    run = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[50])
    lineage = _record_by("DBT_LINEAGE", run_id=RUN_IDS[50])
    necessary = (
        run,
        _record_by("RELATION_DATA_PROFILE", "raw_payments", run_id=RUN_IDS[50]),
        _record_by("RELATION_DATA_PROFILE", "raw_orders", run_id=RUN_IDS[50]),
        _record_by("RELATION_HISTORY", "raw_payments", run_id=RUN_IDS[50]),
        _record_by("RELATION_HISTORY", "raw_orders", run_id=RUN_IDS[50]),
    )
    retry_text: list[str] = []
    model = _silent_recovery_scripted(necessary=necessary, lineage=lineage, retry_text=retry_text)
    result = await _runner_for(50, tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.kernel_state.final_status.value == "CONFIRMED"
    assert result.metrics.tool_call_attempts == 7
    assert len(result.evidence_records) == 7
    rejected = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
        and getattr(event, "reason_code", None) == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"
        and not event.accepted
    )
    assert len(rejected) == 1
    assert any("records cited by the ROOT_CAUSE claim" in text for text in retry_text)
    summary = rejected[0].rejected_decision
    assert summary is not None
    assert summary.schema_version == "p1.rejected_decision.v1"
    assert summary.status == "CONFIRMED"
    root = next(claim for claim in summary.claims if claim.kind == "ROOT_CAUSE")
    assert root.known_value == "SOURCE_PAYMENT_INGESTION_LOSS"
    accepted_ids = {record.evidence_id for record in result.evidence_records}
    assert set(root.evidence_ids) <= accepted_ids
    assert summary.model_request_index >= 0


@pytest.mark.asyncio
async def test_seq50_rejection_then_timeout_stays_model_error(
    tmp_path: Path,
) -> None:
    """After a rejected confirm, a controlled timeout still projects MODEL_ERROR
    and never fabricates a success or an INSUFFICIENT terminal."""

    _write_context(tmp_path, 50)
    tools = _SilentLossTools()
    run = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[50])
    lineage = _record_by("DBT_LINEAGE", run_id=RUN_IDS[50])
    necessary = (
        run,
        _record_by("RELATION_DATA_PROFILE", "raw_payments", run_id=RUN_IDS[50]),
        _record_by("RELATION_DATA_PROFILE", "raw_orders", run_id=RUN_IDS[50]),
        _record_by("RELATION_HISTORY", "raw_payments", run_id=RUN_IDS[50]),
        _record_by("RELATION_HISTORY", "raw_orders", run_id=RUN_IDS[50]),
    )
    retry_text: list[str] = []
    model = _silent_recovery_scripted(
        necessary=necessary,
        lineage=lineage,
        retry_text=retry_text,
        timeout_after_rejection=True,
    )
    result = await _runner_for(50, tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    # A timeout is never remapped onto another MODEL_ERROR reason.
    assert result.diagnosis.summary == "MODEL_TIMEOUT"
    assert result.kernel_state.final_status.value == "MODEL_ERROR"
    assert result.kernel_state.gate_reason == "MODEL_TIMEOUT"
    terminal = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "DIAGNOSIS_TERMINAL"
    )
    assert len(terminal) == 1
    assert terminal[0].status is DiagnosisStatus.MODEL_ERROR
    rejected = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
        and getattr(event, "reason_code", None) == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"
        and not event.accepted
    )
    assert len(rejected) == 1
    # The rejection summary survives even when the retry round times out.
    assert rejected[0].rejected_decision is not None
    timeout_gate = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
        and getattr(event, "reason_code", None) == "MODEL_TIMEOUT"
    )
    assert len(timeout_gate) == 1
    assert timeout_gate[0].accepted is True


def _dup_b_subject_fix_scripted(retry_text: list[str]) -> FunctionModel:
    """First finalize declares the identity gap on an unrelated subject, the
    corrective finalize uses the public subject."""

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        _collect_retry_text(messages, retry_text)
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
                {"run_id": RUN_IDS[67]},
                {
                    "kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"],
                    "kernel_new_hypotheses": [
                        {
                            "hypothesis_id": "h_exact_dup",
                            "root_cause_code": "SOURCE_EXACT_PAYMENT_DUPLICATE",
                        },
                        {
                            "hypothesis_id": "h_sem_dup",
                            "root_cause_code": "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
                        },
                        {
                            "hypothesis_id": "h_legit_split",
                            "root_cause_code": "LEGITIMATE_SPLIT_PAYMENT",
                        },
                    ],
                },
            ),
            (
                "call-1",
                "get_relation_schema",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
            (
                "call-2",
                "get_dbt_lineage",
                {"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
            (
                "call-3",
                "get_relation_data_profile",
                {"relation_name": "raw_payments"},
                {"kernel_hypothesis_ids": ["h_exact_dup", "h_sem_dup", "h_legit_split"]},
            ),
        )
        for call_id, tool_name, arguments, extra in steps:
            if call_id not in sent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                    ]
                )
        subject = "raw_orders" if "final-1" not in sent else "raw_payments"

        def payload() -> dict[str, object]:
            return {
                "schema_version": "p1.kernel_decision.v1",
                "status": "INSUFFICIENT_EVIDENCE",
                "run_id": RUN_IDS[67],
                "selected_hypothesis_id": None,
                "assessments": [],
                "claims": [],
                "unresolved_evidence": [
                    {
                        "evidence_kind": "RELATION_DATA_PROFILE",
                        "subject": "raw_payments",
                        "reason_code": "RELATION_NOT_ALLOWED",
                    },
                    {
                        "evidence_kind": "PAYMENT_EVENT_IDENTITY",
                        "subject": subject,
                        "reason_code": "NOT_OBSERVABLE",
                    },
                ],
                "summary": "The duplicate profile and channel identity are unavailable.",
                "recommended_actions": [],
                "confidence": 0.2,
            }

        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload(),
                    tool_call_id="final-1" if subject == "raw_orders" else "final-2",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_seq67_wrong_subject_declaration_is_corrected_on_retry(
    tmp_path: Path,
) -> None:
    """A PAYMENT_EVENT_IDENTITY declaration on an unrelated subject is rejected;
    the corrected subject passes the real evaluator with exactly one probe and
    zero database calls."""

    _write_context(tmp_path, 67)
    tools = _DupBTools()
    retry_text: list[str] = []
    result = await _runner_for(
        67, tmp_path, _dup_b_subject_fix_scripted(retry_text), tools
    ).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    evaluation = _evaluate(67, result)
    assert evaluation.status is EvaluationStatus.PASSED
    assert result.metrics.tool_call_attempts == 4
    blocked = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "TOOL_CALL"
        and getattr(event, "error_code", None) == "RELATION_NOT_ALLOWED"
    )
    assert len(blocked) == 1
    assert tools.profile_calls == 0
    rejected = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
        and getattr(event, "reason_code", None) == "UNRESOLVED_EVIDENCE_UNBOUND"
        and not event.accepted
    )
    assert len(rejected) == 1
    assert any("Re-check each declared gap" in text for text in retry_text)


@pytest.mark.asyncio
async def test_seq59_unbound_finalize_recovers_via_one_probe_within_budget(
    tmp_path: Path,
) -> None:
    """First finalize without a receipt is rejected; one boundary probe records
    the receipt and the corrected finalize passes the real evaluator, inside
    the 8-request/8-tool budget."""

    _write_context(tmp_path, 59)

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        _collect_retry_text(messages, retry_text)
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        binding = {
            "kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"],
            "kernel_new_hypotheses": [
                {
                    "hypothesis_id": "h_schema_col_type",
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                },
                {
                    "hypothesis_id": "h_transform_cast",
                    "root_cause_code": "TRANSFORMATION_COLUMN_CAST_CHANGED",
                },
            ],
        }
        steps = (
            ("call-0", "get_dbt_run_results", {"run_id": RUN_IDS[59]}, binding),
            (
                "call-1",
                "get_dbt_node_error",
                {"run_id": RUN_IDS[59], "node_id": "model.jaffle_shop.customers"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
            (
                "call-2",
                "get_dbt_lineage",
                {"node_id": "model.jaffle_shop.customers", "direction": "upstream"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
            (
                "call-3",
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
            (
                "call-4",
                "get_relation_history",
                {"relation_name": "raw_orders"},
                {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]},
            ),
        )
        for call_id, tool_name, arguments, extra in steps:
            if call_id not in sent:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name,
                            {**arguments, **extra},
                            tool_call_id=call_id,
                        )
                    ]
                )
        if "final-1" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        agent_info.output_tools[0].name,
                        {
                            "schema_version": "p1.kernel_decision.v1",
                            "status": "INSUFFICIENT_EVIDENCE",
                            "run_id": RUN_IDS[59],
                            "selected_hypothesis_id": None,
                            "assessments": [],
                            "claims": [],
                            "unresolved_evidence": [
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
                            ],
                            "summary": (
                                "The raw_orders schema and the transformation definition "
                                "are unavailable."
                            ),
                            "recommended_actions": [],
                            "confidence": 0.2,
                        },
                        tool_call_id="final-1",
                    )
                ]
            )
        if "call-probe" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {
                            "relation_name": "raw_orders",
                            "kernel_hypothesis_ids": [
                                "h_schema_col_type",
                                "h_transform_cast",
                            ],
                        },
                        tool_call_id="call-probe",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_IDS[59],
                        "selected_hypothesis_id": None,
                        "assessments": [],
                        "claims": [],
                        "unresolved_evidence": [
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
                        ],
                        "summary": (
                            "The raw_orders schema and the transformation definition "
                            "are unavailable."
                        ),
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final-2",
                )
            ]
        )

    retry_text: list[str] = []
    tools = _SchemaBTools(include_history=True)
    result = await _runner_for(59, tmp_path, FunctionModel(scripted), tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.metrics.tool_call_attempts == 6
    assert result.metrics.model_requests <= 8
    evaluation = _evaluate(59, result)
    assert evaluation.status is EvaluationStatus.PASSED
    unbound = tuple(
        event
        for event in result.trace
        if getattr(event, "event_type", None) == "EVIDENCE_GATE"
        and getattr(event, "reason_code", None) == "UNRESOLVED_EVIDENCE_UNBOUND"
        and not event.accepted
    )
    assert len(unbound) == 1
    assert any("Re-check each declared gap" in text for text in retry_text)


def test_rejected_decision_summary_filters_unknown_content_and_truncates() -> None:
    """Unknown subjects, claim values, hypothesis IDs and evidence references
    never reach the summary; known references resolve against the inventory."""

    from data_incident_gym.diagnostic_agent import _rejected_decision_summary

    kernel, lineage = _silent_kernel_with_evidence()
    run = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[50])
    secret_asset = "PRIVATE_ASSET_STRING"
    secret_subject = "PRIVATE_TOPIC"
    ghost_hypothesis = "h_ghost"
    fake_evidence = "ev_" + "f" * 64
    decision = KernelDecision(
        status="CONFIRMED",
        run_id=RUN_IDS[50],
        selected_hypothesis_id="h_ingestion_loss",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_ingestion_loss",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(run.evidence_id,),
            ),
            HypothesisAssessment(
                hypothesis_id=ghost_hypothesis,
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(fake_evidence,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_PAYMENT_INGESTION_LOSS",
                evidence_ids=(run.evidence_id, fake_evidence),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value=secret_asset,
                evidence_ids=(fake_evidence,),
            ),
            *(
                ClaimEvidence(
                    kind=ClaimKind.AFFECTED_ASSET,
                    value=f"asset_{index}",
                    evidence_ids=(run.evidence_id,),
                )
                for index in range(16)
            ),
        ),
        summary="PRIVATE_FREE_TEXT_MUST_NOT_SURVIVE",
        recommended_actions=("PRIVATE_ACTION",),
        confidence=0.9,
    )
    summary = _rejected_decision_summary(decision, kernel, model_request_index=3)

    assert summary.schema_version == "p1.rejected_decision.v1"
    assert summary.model_request_index == 3
    assert summary.truncated is True
    assert summary.total_claims == 18
    assert len(summary.claims) == 16
    root = next(claim for claim in summary.claims if claim.kind == "ROOT_CAUSE")
    assert root.known_value == "SOURCE_PAYMENT_INGESTION_LOSS"
    assert root.evidence_ids == (run.evidence_id,)
    assert summary.unknown_evidence_count == 2
    assert summary.unknown_hypothesis_count == 1
    assert summary.unknown_claim_count == 15
    assert summary.total_unresolved == 0
    # Identity per array: assessments drop unknown hypotheses, claims and
    # unresolved declarations redact unknown values inside the kept items.
    assert summary.total_assessments == (
        len(summary.assessments)
        + summary.unknown_hypothesis_count
        + summary.truncated_assessment_count
    )
    assert summary.total_claims == len(summary.claims) + summary.truncated_claim_count
    assert summary.total_claims == 18
    dumped = summary.model_dump_json()
    assert secret_asset not in dumped
    assert ghost_hypothesis not in dumped
    assert fake_evidence not in dumped
    assert "PRIVATE_FREE_TEXT_MUST_NOT_SURVIVE" not in dumped
    assert "PRIVATE_ACTION" not in dumped

    # Unknown unresolved subjects are dropped on the INSUFFICIENT path.
    insufficient = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_IDS[50],
        unresolved_evidence=(
            {
                "evidence_kind": "RELATION_SCHEMA",
                "subject": secret_subject,
                "reason_code": "NOT_OBSERVABLE",
            },
        ),
        summary="Private text.",
        recommended_actions=(),
        confidence=0.2,
    )
    insufficient_summary = _rejected_decision_summary(insufficient, kernel, model_request_index=4)
    assert insufficient_summary.unresolved_evidence[0].subject is None
    assert insufficient_summary.unknown_subject_count == 1
    assert insufficient_summary.total_unresolved == (
        len(insufficient_summary.unresolved_evidence)
        + insufficient_summary.truncated_unresolved_count
    )
    assert secret_subject not in insufficient_summary.model_dump_json()


# ------------------------------------------- rejected summary bounds and coverage


def _wide_kernel(hypothesis_count: int) -> DiagnosticKernel:
    """A seq50 kernel with an explicit number of registered hypotheses."""

    kernel = _kernel_for(
        50,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_BUSINESS_PAYMENT_DECLINE",
        ),
        schema=("raw_payments",),
        profile=("raw_payments", "raw_orders"),
        history=("raw_payments", "raw_orders"),
    )
    _accept(
        kernel,
        gap_id="g_wide_1",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_IDS[50]},
        record=_record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[50]),
        new_hypotheses=tuple(
            Hypothesis(
                hypothesis_id=f"h_synthetic_{index}",
                root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
            )
            for index in range(hypothesis_count)
        ),
    )
    return kernel


def _assessment_decision(count: int, evidence_id: str) -> KernelDecision:
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_IDS[50],
        selected_hypothesis_id="h_synthetic_0",
        assessments=tuple(
            HypothesisAssessment(
                hypothesis_id=f"h_synthetic_{index}",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(evidence_id,),
            )
            for index in range(count)
        ),
        summary="Bounded projection.",
        recommended_actions=(),
        confidence=0.5,
    )


class _StubKernel:
    """Minimal kernel surface read by the rejected-decision projection."""

    def __init__(self, evidence_ids: tuple[str, ...]) -> None:
        self._records = tuple(
            SimpleNamespace(evidence_id=evidence_id, content=SimpleNamespace())
            for evidence_id in evidence_ids
        )

    @property
    def evidence_records(self) -> tuple[SimpleNamespace, ...]:
        return self._records

    def provable_relations_by_tool(self) -> dict[str, tuple[str, ...]]:
        return {}

    @property
    def incident_subjects(self) -> tuple[str, ...]:
        return ()

    @property
    def allowed_root_cause_codes(self) -> tuple[str, ...]:
        return ("SOURCE_PAYMENT_INGESTION_LOSS",)

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return ()


def _unresolved_decision(subjects: tuple[str, ...]) -> KernelDecision:
    return KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_IDS[50],
        unresolved_evidence=tuple(
            {
                "evidence_kind": "RELATION_SCHEMA",
                "subject": subject,
                "reason_code": "NOT_OBSERVABLE",
            }
            for subject in subjects
        ),
        summary="Bounded projection.",
        recommended_actions=(),
        confidence=0.2,
    )


def _claim_decision(count: int, evidence_id: str) -> KernelDecision:
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_IDS[50],
        selected_hypothesis_id="h_synthetic_0",
        claims=tuple(
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value=f"asset_{index}",
                evidence_ids=(evidence_id,),
            )
            for index in range(count)
        ),
        summary="Bounded projection.",
        recommended_actions=(),
        confidence=0.5,
    )


def test_rejected_decision_summary_caps_assessments_and_unresolved_evidence() -> None:
    """Both arrays obey the same 16-item cap as claims, and items dropped by the
    cap are reported as truncated instead of being counted as unknown content."""

    from data_incident_gym.diagnostic_agent import _rejected_decision_summary

    kernel = _wide_kernel(20)
    accepted_id = kernel.evidence_records[0].evidence_id

    at_cap = _rejected_decision_summary(
        _assessment_decision(16, accepted_id), kernel, model_request_index=4
    )
    assert len(at_cap.assessments) == 16
    assert at_cap.total_assessments == 16
    assert at_cap.truncated_assessment_count == 0
    assert at_cap.truncated is False

    over_cap = _rejected_decision_summary(
        _assessment_decision(20, accepted_id), kernel, model_request_index=5
    )
    assert len(over_cap.assessments) == 16
    assert over_cap.total_assessments == 20
    assert over_cap.unknown_hypothesis_count == 0
    assert over_cap.truncated_assessment_count == 4
    assert over_cap.truncated is True
    assert over_cap.selected_hypothesis_id == "h_synthetic_0"

    private_subjects = tuple(f"PRIVATE_SUBJECT_{index}" for index in range(20))
    unresolved_over_cap = _rejected_decision_summary(
        _unresolved_decision(private_subjects), kernel, model_request_index=6
    )
    assert len(unresolved_over_cap.unresolved_evidence) == 16
    assert unresolved_over_cap.total_unresolved == 20
    assert unresolved_over_cap.unknown_subject_count == 16
    assert unresolved_over_cap.truncated_unresolved_count == 4
    assert unresolved_over_cap.truncated is True
    dumped = unresolved_over_cap.model_dump_json()
    assert all(subject not in dumped for subject in private_subjects)

    unresolved_at_cap = _rejected_decision_summary(
        _unresolved_decision(private_subjects[:16]), kernel, model_request_index=7
    )
    assert len(unresolved_at_cap.unresolved_evidence) == 16
    assert unresolved_at_cap.truncated_unresolved_count == 0
    assert unresolved_at_cap.truncated is False

    claims_at_cap = _rejected_decision_summary(
        _claim_decision(16, accepted_id), kernel, model_request_index=8
    )
    assert len(claims_at_cap.claims) == 16
    assert claims_at_cap.total_claims == 16
    assert claims_at_cap.truncated_claim_count == 0
    assert claims_at_cap.truncated is False

    claims_over_cap = _rejected_decision_summary(
        _claim_decision(17, accepted_id), kernel, model_request_index=9
    )
    assert len(claims_over_cap.claims) == 16
    assert claims_over_cap.total_claims == 17
    assert claims_over_cap.truncated_claim_count == 1
    assert claims_over_cap.truncated is True


def test_rejected_decision_summary_separates_truncated_evidence_refs() -> None:
    """References dropped by the per-claim cap are counted as truncated, not as
    unknown, and the two counts stay independent."""

    from data_incident_gym.diagnostic_agent import _rejected_decision_summary

    accepted = tuple("ev_" + f"{index:064d}" for index in range(40))
    unknown_refs = tuple("ev_" + char * 64 for char in "abcd")
    kernel = _StubKernel(accepted)

    def decision(evidence_ids: tuple[str, ...]) -> KernelDecision:
        return KernelDecision(
            status="CONFIRMED",
            run_id=RUN_IDS[50],
            selected_hypothesis_id="h_stub",
            claims=(
                ClaimEvidence(
                    kind=ClaimKind.ROOT_CAUSE,
                    value="SOURCE_PAYMENT_INGESTION_LOSS",
                    evidence_ids=evidence_ids,
                ),
            ),
            summary="Bounded projection.",
            recommended_actions=(),
            confidence=0.9,
        )

    assert not kernel.hypotheses

    at_cap = _rejected_decision_summary(
        decision(accepted[:32]), kernel, model_request_index=2
    )
    assert len(at_cap.claims[0].evidence_ids) == 32
    assert at_cap.unknown_evidence_count == 0
    assert at_cap.truncated_evidence_count == 0
    assert at_cap.truncated is False

    over_cap = _rejected_decision_summary(
        decision((*accepted[:32], *unknown_refs)), kernel, model_request_index=3
    )
    assert len(over_cap.claims[0].evidence_ids) == 32
    assert over_cap.unknown_evidence_count == 0
    assert over_cap.truncated_evidence_count == len(unknown_refs)
    assert over_cap.truncated is True
    assert all(ref not in over_cap.model_dump_json() for ref in unknown_refs)

    mixed = _rejected_decision_summary(
        decision((unknown_refs[0], accepted[0], *unknown_refs[1:])), kernel, model_request_index=4
    )
    assert mixed.claims[0].evidence_ids == (accepted[0],)
    assert mixed.unknown_evidence_count == len(unknown_refs)
    assert mixed.truncated_evidence_count == 0
    assert mixed.truncated is False


def _run_results_with_public_nodes(nodes: tuple[str, ...]) -> EvidenceRecord:
    """Derive a seq50 run-results record that publishes the given failed nodes."""

    base = _record_by("DBT_RUN_RESULTS", run_id=RUN_IDS[50])
    content = base.content.model_copy(update={"failed_nodes": nodes})
    return EvidenceRecord.create(
        run_id=RUN_IDS[50],
        evidence_type=base.evidence_type,
        source=base.source,
        subject=base.subject,
        observed_at=base.observed_at,
        content=content,
    )


def test_rejected_decision_summary_keeps_nodes_published_by_run_results() -> None:
    """A node that only run_results made public stays visible in the summary,
    matching the public subjects the domain validator accepts."""

    from data_incident_gym.diagnostic_agent import _rejected_decision_summary

    public_node = "model.jaffle_shop.stg_orders"
    private_subject = "PRIVATE_UNKNOWN_SUBJECT"
    kernel = _kernel_for(
        50,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_BUSINESS_PAYMENT_DECLINE",
        ),
        schema=("raw_payments",),
        profile=("raw_payments", "raw_orders"),
        history=("raw_payments", "raw_orders"),
    )
    _accept(
        kernel,
        gap_id="g_auto_1",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_IDS[50]},
        record=_run_results_with_public_nodes((public_node,)),
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_ingestion_loss",
                root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
            ),
            Hypothesis(
                hypothesis_id="h_normal_decline",
                root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
            ),
        ),
    )

    # The node is published by run_results alone: no other projection carries it.
    assert public_node not in kernel.incident_subjects
    other_projection = {
        node_id
        for record in kernel.evidence_records
        for node_id in (
            getattr(record.content, "node_id", None),
            *(item.node_id for item in getattr(record.content, "related_nodes", ())),
        )
        if isinstance(node_id, str)
    }
    assert public_node not in other_projection

    decision = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_IDS[50],
        unresolved_evidence=(
            {
                "evidence_kind": "TRANSFORMATION_DEFINITION",
                "subject": public_node,
                "reason_code": "NOT_OBSERVABLE",
            },
            {
                "evidence_kind": "RELATION_SCHEMA",
                "subject": private_subject,
                "reason_code": "NOT_OBSERVABLE",
            },
        ),
        summary="Bounded projection.",
        recommended_actions=(),
        confidence=0.2,
    )
    summary = _rejected_decision_summary(decision, kernel, model_request_index=3)

    assert summary.unresolved_evidence[0].subject == public_node
    assert summary.unresolved_evidence[1].subject is None
    assert summary.unknown_subject_count == 1
    assert private_subject not in summary.model_dump_json()
