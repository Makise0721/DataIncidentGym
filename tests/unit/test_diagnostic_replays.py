"""T05 versioned failure replay library.

Every entry in ``tests/fixtures/diagnostic_replays/index.json`` records one
known failure mechanism (source report, protocol versions, trigger, expected
behaviour and counterfactual). Each replay below drives the real kernel,
evaluator or fixed-rule runner fully offline and asserts BOTH the original
failure (exact code / check code) and the counterfactual recovery, plus the
budget/tool-call counts where they matter. The guard test keeps the index and
this registry from drifting apart.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym import scenario_certification
from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_agent import (
    CONTROLLER_PROTOCOL_VERSION,
    KERNEL_PROMPT_VERSION,
)
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
    KernelError,
    KernelFinalStatus,
)
from data_incident_gym.evaluation import (
    EVALUATOR_VERSION,
    DeterministicEvaluator,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_LIMIT, FixedRuleRunner
from data_incident_gym.lab_verifier import (
    ScenarioVerification,
    ScenarioVerificationStatus,
)
from data_incident_gym.profiles import ColumnProfileFact, RelationProfileSnapshot
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext
from data_incident_gym.scenarios import ScenarioSpec, load_scenario_spec

REPLAY_INDEX_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "diagnostic_replays" / "index.json"
)
REPLAY_PROTOCOL = f"{KERNEL_PROMPT_VERSION} / {CONTROLLER_PROTOCOL_VERSION} / {EVALUATOR_VERSION}"
REPLAY_SCHEMA_VERSION = "p1.diagnostic_replays.v1"
REPLAY_GROUPS = frozenset(
    {"reference-gaps", "abstention-and-receipts", "claims-assets-budget"}
)

RUN_ID = "a" * 32
TEST_ID = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
ORDERS_MODEL = "model.jaffle_shop.orders"
STG_ORDERS = "model.jaffle_shop.stg_orders"
STG_CUSTOMERS = "model.jaffle_shop.stg_customers"
OBSERVED_AT = datetime(2026, 8, 30, tzinfo=UTC)

_REPLAYS: dict[str, Callable[..., None]] = {}


def _replay(entry_id: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def register(function: Callable[..., None]) -> Callable[..., None]:
        _REPLAYS[entry_id] = function
        return function

    return register


def _replay_index() -> dict:
    return json.loads(REPLAY_INDEX_PATH.read_text(encoding="utf-8"))


def _entry(entry_id: str) -> dict:
    return next(item for item in _replay_index()["entries"] if item["id"] == entry_id)


# ---------------------------------------------------------------------------
# Shared evidence: one required-null failure over the orders customer_id test
# ---------------------------------------------------------------------------


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
        observed_at=OBSERVED_AT,
        content=content,
    )


def _failure_records() -> dict[str, EvidenceRecord]:
    """The complete public evidence set for the shared required-null replay."""

    run = _record(
        EvidenceType.DBT_RUN_RESULTS,
        EvidenceSource.DBT_RUN_RESULTS,
        RUN_ID,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(TEST_ID,),
            skipped_nodes=(),
        ),
    )
    node_error = _record(
        EvidenceType.DBT_NODE_ERROR,
        EvidenceSource.DBT_RUN_RESULTS,
        TEST_ID,
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=TEST_ID,
            resource_type="test",
            status="fail",
            message="required field is null",
        ),
    )
    lineage = _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        TEST_ID,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=TEST_ID,
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id=ORDERS_MODEL,
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="source.jaffle_shop.raw_orders",
                    resource_type="source",
                    name="raw_orders",
                    distance=2,
                ),
                DbtLineageNode(
                    node_id="source.jaffle_shop.raw_customers",
                    resource_type="source",
                    name="raw_customers",
                    distance=2,
                ),
            ),
        ),
    )
    schema_orders = _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        "raw_orders",
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="staging",
            relation_name="raw_orders",
            columns=(
                RelationSchemaColumn(
                    name="user_id",
                    data_type="integer",
                    nullable=True,
                    ordinal_position=1,
                ),
            ),
        ),
    )
    schema_customers = _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        "raw_customers",
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="staging",
            relation_name="raw_customers",
            columns=(
                RelationSchemaColumn(
                    name="last_name",
                    data_type="text",
                    nullable=True,
                    ordinal_position=1,
                ),
            ),
        ),
    )
    profile_orders = _record(
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
                row_count=99,
                columns=(
                    ColumnProfileFact(column_name="user_id", null_count=1, distinct_count=98),
                ),
            ),
        ),
    )
    profile_customers = _record(
        EvidenceType.RELATION_DATA_PROFILE,
        EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        "raw_customers",
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_customers",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name="raw_customers",
                row_count=100,
                columns=(
                    ColumnProfileFact(column_name="last_name", null_count=1, distinct_count=90),
                ),
            ),
        ),
    )
    return {
        "run": run,
        "node_error": node_error,
        "lineage": lineage,
        "schema_orders": schema_orders,
        "schema_customers": schema_customers,
        "profile_orders": profile_orders,
        "profile_customers": profile_customers,
    }


# ---------------------------------------------------------------------------
# Kernel-layer builders
# ---------------------------------------------------------------------------

_HYPOTHESES = (
    Hypothesis(hypothesis_id="h_null", root_cause_code="SOURCE_REQUIRED_FIELD_NULL"),
    Hypothesis(
        hypothesis_id="h_transform",
        root_cause_code="TRANSFORMATION_REQUIRED_FIELD_NULL",
    ),
)


def _required_null_kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders", "raw_customers"),
        observable_profile_relations=("raw_orders",),
        incident_subjects=(TEST_ID,),
    )


def _close(
    kernel: DiagnosticKernel,
    *,
    gap_id: str,
    gap_kind: EvidenceGapKind,
    tool_name: str,
    arguments: dict[str, str],
    record: EvidenceRecord,
    new_hypotheses: tuple[Hypothesis, ...] = (),
) -> None:
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=gap_kind,
            new_hypotheses=new_hypotheses,
        ),
        tool_name=tool_name,
        arguments=arguments,
    )
    kernel.record_tool_result(prepared, (record,))


def _close_required_null_gaps(kernel: DiagnosticKernel) -> dict[str, EvidenceRecord]:
    records = _failure_records()
    _close(
        kernel,
        gap_id="g_run",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=records["run"],
        new_hypotheses=_HYPOTHESES,
    )
    _close(
        kernel,
        gap_id="g_explain",
        gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        tool_name="get_dbt_node_error",
        arguments={"run_id": RUN_ID, "node_id": TEST_ID},
        record=records["node_error"],
    )
    _close(
        kernel,
        gap_id="g_source",
        gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        tool_name="get_dbt_lineage",
        arguments={"node_id": TEST_ID, "direction": "upstream"},
        record=records["lineage"],
    )
    _close(
        kernel,
        gap_id="g_schema_orders",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_orders"},
        record=records["schema_orders"],
    )
    _close(
        kernel,
        gap_id="g_schema_customers",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_customers"},
        record=records["schema_customers"],
    )
    _close(
        kernel,
        gap_id="g_profile",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
        record=records["profile_orders"],
    )
    return records


def _confirmed_decision(
    records: dict[str, EvidenceRecord],
    *,
    root_evidence: tuple[EvidenceRecord, ...] | None = None,
    asset_value: str = ORDERS_MODEL,
    asset_evidence: tuple[EvidenceRecord, ...] | None = None,
) -> KernelDecision:
    if root_evidence is None:
        root_evidence = (records["run"], records["profile_orders"], records["node_error"])
    if asset_evidence is None:
        asset_evidence = (records["lineage"],)
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id="h_null",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_null",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=tuple(record.evidence_id for record in root_evidence),
            ),
            HypothesisAssessment(
                hypothesis_id="h_transform",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(
                    records["profile_orders"].evidence_id,
                    records["lineage"].evidence_id,
                ),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_REQUIRED_FIELD_NULL",
                evidence_ids=tuple(record.evidence_id for record in root_evidence),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value=asset_value,
                evidence_ids=tuple(record.evidence_id for record in asset_evidence),
            ),
        ),
        summary="The required source field is null.",
        recommended_actions=(),
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Evaluator-layer builders
# ---------------------------------------------------------------------------


def _policy_identity(strategy: DiagnosticStrategy) -> PolicyIdentity:
    return PolicyIdentity(
        strategy=strategy,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="1" * 64,
        strategy_prompt_version="p1.static.v5",
        strategy_prompt_sha256="2" * 64,
        controller_protocol_version=CONTROLLER_PROTOCOL_VERSION,
        controller_protocol_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
    )


def _verification(scenario: ScenarioSpec) -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=scenario.incident_case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=(scenario.direct_failure,),
        skipped_nodes=(),
        affected_assets=tuple(sorted(scenario.affected_assets)),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def _tool_event(
    tool_name: str,
    arguments: dict[str, str],
    records: tuple[EvidenceRecord, ...] = (),
    error_code: str | None = None,
) -> ToolTraceEvent:
    fingerprint = hashlib.sha256(
        json.dumps({"arguments": arguments, "tool": tool_name}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=tool_name,
        arguments=arguments,
        fingerprint=fingerprint,
        evidence_ids=tuple(record.evidence_id for record in records),
        error_code=error_code,
        elapsed_ms=1,
    )


def _diagnosis_run(
    scenario: ScenarioSpec,
    diagnosis: Diagnosis,
    records: tuple[EvidenceRecord, ...],
    tool_events: tuple[ToolTraceEvent, ...],
) -> DiagnosisRunResult:
    strategy = DiagnosticStrategy.REFERENCE_ANALYST
    trace = (
        *tool_events,
        DiagnosisTerminalTraceEvent(
            event_type="DIAGNOSIS_TERMINAL",
            strategy=strategy,
            status=diagnosis.status,
            evidence_inventory=tuple(record.evidence_id for record in records),
        ),
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=_policy_identity(strategy),
        diagnosis=diagnosis,
        evidence_records=records,
        trace=trace,
        metrics=DiagnosisMetrics(
            provider="replay",
            model="replay-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=len(tool_events),
            successful_tool_calls=sum(event.error_code is None for event in tool_events),
            elapsed_ms=1,
        ),
    )


def _evaluate(scenario: ScenarioSpec, run: DiagnosisRunResult) -> EvaluationResult:
    return DeterministicEvaluator.evaluate(
        scenario, _verification(scenario), run, recovery_succeeded=True
    )


def _failed_codes(evaluation: EvaluationResult) -> tuple[str, ...]:
    return tuple(code.value for code in evaluation.failed_check_codes)


def _scenario_a_confirmed_run(
    records: dict[str, EvidenceRecord],
    *,
    root_evidence: tuple[EvidenceRecord, ...],
    cited: tuple[EvidenceRecord, ...],
    tool_events: tuple[ToolTraceEvent, ...],
) -> DiagnosisRunResult:
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        summary="The required source field is null.",
        affected_assets=(ORDERS_MODEL,),
        evidence_ids=tuple(record.evidence_id for record in cited),
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                evidence_ids=tuple(record.evidence_id for record in root_evidence),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=ORDERS_MODEL,
                evidence_ids=(records["lineage"].evidence_id,),
            ),
        ),
        confidence=0.9,
    )
    scenario = load_scenario_spec("required_null_order_customer_a")
    return _diagnosis_run(scenario, diagnosis, cited, tool_events)


def _scenario_b_insufficient_run(
    gaps: tuple[tuple[str, str, str], ...],
) -> DiagnosisRunResult:
    records = _failure_records()
    cited = (
        records["run"],
        records["node_error"],
        records["lineage"],
        records["schema_orders"],
        records["profile_customers"],
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The decisive evidence is unavailable.",
        evidence_ids=tuple(record.evidence_id for record in cited),
        unresolved_evidence=tuple(
            UnresolvedEvidence(evidence_kind=kind, subject=subject, reason_code=reason)
            for kind, subject, reason in gaps
        ),
        confidence=0.2,
    )
    tool_events = (
        _tool_event("get_dbt_run_results", {"run_id": RUN_ID}, (records["run"],)),
        _tool_event(
            "get_dbt_node_error",
            {"run_id": RUN_ID, "node_id": TEST_ID},
            (records["node_error"],),
        ),
        _tool_event(
            "get_dbt_lineage",
            {"node_id": TEST_ID, "direction": "upstream"},
            (records["lineage"],),
        ),
        _tool_event(
            "get_relation_schema",
            {"relation_name": "raw_orders"},
            (records["schema_orders"],),
        ),
        _tool_event(
            "get_relation_data_profile",
            {"relation_name": "raw_customers"},
            (records["profile_customers"],),
        ),
        _tool_event(
            "get_relation_data_profile",
            {"relation_name": "raw_orders"},
            (),
            "RELATION_NOT_ALLOWED",
        ),
    )
    scenario = load_scenario_spec("required_null_order_customer_b")
    return _diagnosis_run(scenario, diagnosis, cited, tool_events)


# ---------------------------------------------------------------------------
# Index/registry guard
# ---------------------------------------------------------------------------


def test_replay_index_matches_the_registry_exactly() -> None:
    index = _replay_index()
    assert index["schema_version"] == REPLAY_SCHEMA_VERSION
    entries = index["entries"]
    assert len(entries) == 9
    for entry in entries:
        assert entry["group"] in REPLAY_GROUPS
        assert entry["protocol"] == REPLAY_PROTOCOL
        for field in ("source", "trigger", "expected", "counterfactual"):
            assert isinstance(entry[field], str) and entry[field]
        origin = entry["origin"]
        assert origin["kind"] in {"synthetic-mechanism", "historical-replay"}
        assert origin["note"]
        if origin["kind"] == "historical-replay":
            # Historical entries must archive their original inputs: run id,
            # content digest and a redaction note. Synthetic entries must not
            # pretend to be historical replays.
            for field in ("source_run_id", "source_digest", "redaction"):
                assert isinstance(origin.get(field), str) and origin[field]
    assert {entry["id"] for entry in entries} == set(_REPLAYS)
    assert index["pending_historical_replays"], (
        "original-scenario replays are still pending archived traces"
    )


# ---------------------------------------------------------------------------
# Replay 1: reference-missing-required-schema
# ---------------------------------------------------------------------------


@_replay("reference-missing-required-schema")
def test_replay_reference_missing_required_schema() -> None:
    entry = _entry("reference-missing-required-schema")
    assert "REQUIRED_EVIDENCE_TYPES_PRESENT" in entry["expected"]
    scenario = load_scenario_spec("required_null_order_customer_a")
    records = _failure_records()
    cited = (
        records["run"],
        records["node_error"],
        records["lineage"],
        records["profile_orders"],
    )
    tool_events = (
        _tool_event("get_dbt_run_results", {"run_id": RUN_ID}, (records["run"],)),
        _tool_event(
            "get_dbt_node_error",
            {"run_id": RUN_ID, "node_id": TEST_ID},
            (records["node_error"],),
        ),
        _tool_event(
            "get_dbt_lineage",
            {"node_id": TEST_ID, "direction": "upstream"},
            (records["lineage"],),
        ),
        _tool_event(
            "get_relation_data_profile",
            {"relation_name": "raw_orders"},
            (records["profile_orders"],),
        ),
    )
    root_evidence = (records["run"], records["node_error"], records["profile_orders"])

    original_run = _scenario_a_confirmed_run(
        records, root_evidence=root_evidence, cited=cited, tool_events=tool_events
    )
    assert original_run.metrics.tool_call_attempts == 4
    original = _evaluate(scenario, original_run)
    assert _failed_codes(original) == ("REQUIRED_EVIDENCE_TYPES_PRESENT",)
    assert original.status is EvaluationStatus.FAILED

    recovered_run = _scenario_a_confirmed_run(
        records,
        root_evidence=root_evidence,
        cited=(*cited, records["schema_orders"]),
        tool_events=(
            *tool_events,
            _tool_event(
                "get_relation_schema",
                {"relation_name": "raw_orders"},
                (records["schema_orders"],),
            ),
        ),
    )
    assert recovered_run.metrics.tool_call_attempts == 5
    recovered = _evaluate(scenario, recovered_run)
    assert recovered.status is EvaluationStatus.PASSED
    assert _failed_codes(recovered) == ()

    # The certification layer must blame the reference implementation, never
    # the scoring: the run missed a contract-required evidence type.
    trace_events = tuple(
        event for event in original_run.trace if isinstance(event, ToolTraceEvent)
    )
    findings, flags = scenario_certification._build_findings(
        scenario=scenario,
        evaluation_status="FAILED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=(ORDERS_MODEL,),
        gap_keys=set(),
        collected_evidence_types={
            "DBT_RUN_RESULTS",
            "DBT_NODE_ERROR",
            "DBT_LINEAGE",
            "RELATION_DATA_PROFILE",
        },
        cited_evidence_types={
            "DBT_RUN_RESULTS",
            "DBT_NODE_ERROR",
            "DBT_LINEAGE",
            "RELATION_DATA_PROFILE",
        },
        trace_events=trace_events,
        tool_calls=len(trace_events),
    )
    types_finding = next(
        item for item in findings if item.code == "REQUIRED_EVIDENCE_TYPES_COLLECTED"
    )
    assert types_finding.satisfied is False
    assert flags["types_ok"] is False
    summary = scenario_certification.ReferenceRunSummary(
        run_id=RUN_ID,
        evaluation_status="FAILED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=(ORDERS_MODEL,),
        tool_calls=len(trace_events),
        successful_tool_calls=len(trace_events),
        tool_error_codes=(),
        failed_check_codes=("REQUIRED_EVIDENCE_TYPES_PRESENT",),
    )
    classes = scenario_certification._classify_failure(
        scenario=scenario,
        run=summary,
        expected_root_ok=flags["root_ok"],
        assets_ok=flags["assets_ok"],
        gaps_ok=flags["gaps_ok"],
        status_ok=flags["status_ok"],
        receipts_ok=flags["receipts_ok"],
        types_ok=flags["types_ok"],
        cited_types_ok=flags["cited_types_ok"],
        evaluation_passed=flags["evaluation_passed"],
    )
    assert classes == ("REFERENCE_IMPLEMENTATION",)
    assert "SCORING" not in classes

    # Collection without citation is the same failure class: the evaluator
    # reads the final diagnosis citations, not the evidence inventory.
    _, cited_flags = scenario_certification._build_findings(
        scenario=scenario,
        evaluation_status="FAILED",
        diagnosis_status="CONFIRMED",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=(ORDERS_MODEL,),
        gap_keys=set(),
        collected_evidence_types={
            "DBT_RUN_RESULTS",
            "DBT_NODE_ERROR",
            "DBT_LINEAGE",
            "RELATION_DATA_PROFILE",
            "RELATION_SCHEMA",
        },
        cited_evidence_types={
            "DBT_RUN_RESULTS",
            "DBT_NODE_ERROR",
            "DBT_LINEAGE",
            "RELATION_DATA_PROFILE",
        },
        trace_events=trace_events,
        tool_calls=len(trace_events),
    )
    assert cited_flags["types_ok"] is True
    assert cited_flags["cited_types_ok"] is False
    uncited_classes = scenario_certification._classify_failure(
        scenario=scenario,
        run=summary,
        expected_root_ok=cited_flags["root_ok"],
        assets_ok=cited_flags["assets_ok"],
        gaps_ok=cited_flags["gaps_ok"],
        status_ok=cited_flags["status_ok"],
        receipts_ok=cited_flags["receipts_ok"],
        types_ok=cited_flags["types_ok"],
        cited_types_ok=cited_flags["cited_types_ok"],
        evaluation_passed=False,
    )
    assert uncited_classes == ("REFERENCE_IMPLEMENTATION",)
    assert "SCORING" not in uncited_classes


# ---------------------------------------------------------------------------
# Replay 2: reference-wrong-transformation-subject
# ---------------------------------------------------------------------------


@_replay("reference-wrong-transformation-subject")
def test_replay_reference_wrong_transformation_subject() -> None:
    entry = _entry("reference-wrong-transformation-subject")
    assert "INSUFFICIENCY_GAP_DECLARED" in entry["expected"]
    scenario = load_scenario_spec("required_null_order_customer_b")

    wrong_subject = (
        ("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),
        ("TRANSFORMATION_DEFINITION", STG_CUSTOMERS, "NOT_OBSERVABLE"),
    )
    original = _evaluate(scenario, _scenario_b_insufficient_run(wrong_subject))
    assert _failed_codes(original) == ("INSUFFICIENCY_GAP_DECLARED",)
    assert original.status is EvaluationStatus.FAILED

    correct_subject = (
        ("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),
        ("TRANSFORMATION_DEFINITION", STG_ORDERS, "NOT_OBSERVABLE"),
    )
    recovered = _evaluate(scenario, _scenario_b_insufficient_run(correct_subject))
    assert recovered.status is EvaluationStatus.PASSED
    assert _failed_codes(recovered) == ()


# ---------------------------------------------------------------------------
# Replay 3: over-abstention-with-complete-evidence
# ---------------------------------------------------------------------------


@_replay("over-abstention-with-complete-evidence")
def test_replay_over_abstention_with_complete_evidence() -> None:
    entry = _entry("over-abstention-with-complete-evidence")
    assert "INSUFFICIENCY_GAP_REQUIRED" in entry["expected"]
    kernel = _required_null_kernel()
    records = _close_required_null_gaps(kernel)
    assert kernel.snapshot(model_requests_used=0).tool_calls_used == 6

    with pytest.raises(KernelError, match="INSUFFICIENCY_GAP_REQUIRED"):
        kernel.finalize(
            KernelDecision(
                status="INSUFFICIENT_EVIDENCE",
                run_id=RUN_ID,
                unresolved_evidence=(),
                summary="Abstaining despite a complete closed evidence set.",
                recommended_actions=(),
                confidence=0.2,
            )
        )
    assert kernel.snapshot(model_requests_used=0).final_status is None

    recovered = _required_null_kernel()
    _close_required_null_gaps(recovered)
    outcome = recovered.finalize(_confirmed_decision(records))
    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_REQUIRED_FIELD_NULL"
    assert recovered.snapshot(model_requests_used=0).tool_calls_used == 6


# ---------------------------------------------------------------------------
# Replay 4: declared-gap-without-receipt
# ---------------------------------------------------------------------------


@_replay("declared-gap-without-receipt")
def test_replay_declared_gap_without_receipt() -> None:
    entry = _entry("declared-gap-without-receipt")
    assert "UNRESOLVED_EVIDENCE_UNBOUND" in entry["expected"]
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_profile_relations=("raw_orders",),
    )
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_profile",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
            new_hypotheses=_HYPOTHESES,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
    )
    kernel.record_tool_failure(prepared, "RELATION_NOT_ALLOWED")
    assert kernel.snapshot(model_requests_used=0).tool_calls_used == 1

    def _decision(subject: str) -> KernelDecision:
        return KernelDecision(
            status="INSUFFICIENT_EVIDENCE",
            run_id=RUN_ID,
            unresolved_evidence=(
                {
                    "evidence_kind": "RELATION_DATA_PROFILE",
                    "subject": subject,
                    "reason_code": "RELATION_NOT_ALLOWED",
                },
            ),
            summary="The decisive profile is unavailable.",
            recommended_actions=(),
            confidence=0.2,
        )

    with pytest.raises(KernelError, match="UNRESOLVED_EVIDENCE_UNBOUND"):
        kernel.finalize(_decision("raw_customers"))
    assert kernel.snapshot(model_requests_used=0).final_status is None

    outcome = kernel.finalize(_decision("raw_orders"))
    assert outcome.status is KernelFinalStatus.INSUFFICIENT_EVIDENCE
    assert tuple(
        (item.evidence_kind, item.subject, item.reason_code)
        for item in outcome.unresolved_evidence
    ) == (("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),)


# ---------------------------------------------------------------------------
# Replay 5: incomplete-gap-matrix
# ---------------------------------------------------------------------------


@_replay("incomplete-gap-matrix")
def test_replay_incomplete_gap_matrix() -> None:
    entry = _entry("incomplete-gap-matrix")
    assert "INSUFFICIENCY_GAP_DECLARED" in entry["expected"]
    scenario = load_scenario_spec("required_null_order_customer_b")
    profile_gap_only = (("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),)

    original = _evaluate(scenario, _scenario_b_insufficient_run(profile_gap_only))
    assert _failed_codes(original) == ("INSUFFICIENCY_GAP_DECLARED",)
    assert original.status is EvaluationStatus.FAILED

    full_matrix = (
        ("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),
        ("TRANSFORMATION_DEFINITION", STG_ORDERS, "NOT_OBSERVABLE"),
    )
    recovered = _evaluate(scenario, _scenario_b_insufficient_run(full_matrix))
    assert recovered.status is EvaluationStatus.PASSED
    assert _failed_codes(recovered) == ()
    recovered_run = _scenario_b_insufficient_run(full_matrix)
    assert recovered_run.metrics.tool_call_attempts == 6


# ---------------------------------------------------------------------------
# Replay 6: cross-claim-citation-not-substitutable
# ---------------------------------------------------------------------------


@_replay("cross-claim-citation-not-substitutable")
def test_replay_cross_claim_citation_not_substitutable() -> None:
    entry = _entry("cross-claim-citation-not-substitutable")
    assert "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE" in entry["expected"]
    kernel = _required_null_kernel()
    records = _close_required_null_gaps(kernel)
    cross_claim = _confirmed_decision(
        records,
        asset_evidence=(records["run"], records["profile_orders"], records["node_error"]),
    )

    with pytest.raises(KernelError, match="ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"):
        kernel.finalize(cross_claim)
    assert kernel.snapshot(model_requests_used=0).final_status is None

    recovered = _required_null_kernel()
    _close_required_null_gaps(recovered)
    outcome = recovered.finalize(_confirmed_decision(records))
    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.affected_assets == (ORDERS_MODEL,)


# ---------------------------------------------------------------------------
# Replay 7: asset-id-must-be-canonical
# ---------------------------------------------------------------------------


@_replay("asset-id-must-be-canonical")
def test_replay_asset_id_must_be_canonical() -> None:
    entry = _entry("asset-id-must-be-canonical")
    assert "ASSET_CLAIM_NAME_NOT_IDENTIFIER" in entry["expected"]
    kernel = _required_null_kernel()
    records = _close_required_null_gaps(kernel)

    with pytest.raises(KernelError, match="ASSET_CLAIM_NAME_NOT_IDENTIFIER"):
        kernel.finalize(_confirmed_decision(records, asset_value="orders"))
    assert kernel.snapshot(model_requests_used=0).final_status is None

    recovered = _required_null_kernel()
    _close_required_null_gaps(recovered)
    outcome = recovered.finalize(_confirmed_decision(records))
    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.affected_assets == (ORDERS_MODEL,)


# ---------------------------------------------------------------------------
# Replay 8: schema-wrong-relation-accepted-by-kernel-rejected-by-evaluator
# ---------------------------------------------------------------------------


@_replay("schema-wrong-relation-accepted-by-kernel-rejected-by-evaluator")
def test_replay_schema_wrong_relation_accepted_by_kernel_rejected_by_evaluator() -> None:
    entry = _entry("schema-wrong-relation-accepted-by-kernel-rejected-by-evaluator")
    assert "CLAIM_EVIDENCE_COMPATIBLE" in entry["expected"]
    scenario = load_scenario_spec("required_null_order_customer_a")
    records = _failure_records()
    wrong_root = (records["run"], records["node_error"], records["schema_customers"])
    right_root = (records["run"], records["node_error"], records["profile_orders"])
    cited = (
        records["run"],
        records["node_error"],
        records["lineage"],
        records["schema_customers"],
        records["profile_orders"],
    )
    tool_events = (
        _tool_event("get_dbt_run_results", {"run_id": RUN_ID}, (records["run"],)),
        _tool_event(
            "get_dbt_node_error",
            {"run_id": RUN_ID, "node_id": TEST_ID},
            (records["node_error"],),
        ),
        _tool_event(
            "get_dbt_lineage",
            {"node_id": TEST_ID, "direction": "upstream"},
            (records["lineage"],),
        ),
        _tool_event(
            "get_relation_schema",
            {"relation_name": "raw_customers"},
            (records["schema_customers"],),
        ),
        _tool_event(
            "get_relation_data_profile",
            {"relation_name": "raw_orders"},
            (records["profile_orders"],),
        ),
    )

    # The kernel accepts the wrong-relation schema citation: raw_customers is a
    # proven upstream relation of the failed test, which satisfies the kernel's
    # required-null rule.
    kernel = _required_null_kernel()
    _close_required_null_gaps(kernel)
    outcome = kernel.finalize(_confirmed_decision(records, root_evidence=wrong_root))
    assert outcome.status is KernelFinalStatus.CONFIRMED

    # The evaluator rejects the same citation: the decisive mutated-relation
    # evidence (raw_orders profile, user_id null_count=1) is never cited.
    original_run = _scenario_a_confirmed_run(
        records, root_evidence=wrong_root, cited=cited, tool_events=tool_events
    )
    assert original_run.metrics.tool_call_attempts == 5
    original = _evaluate(scenario, original_run)
    assert _failed_codes(original) == ("CLAIM_EVIDENCE_COMPATIBLE",)
    assert original.status is EvaluationStatus.FAILED

    # Counterfactual: cite the mutated relation's own evidence; only the root
    # claim citation changes, the collected evidence set stays the same.
    recovered_run = _scenario_a_confirmed_run(
        records, root_evidence=right_root, cited=cited, tool_events=tool_events
    )
    recovered = _evaluate(scenario, recovered_run)
    assert recovered.status is EvaluationStatus.PASSED
    assert _failed_codes(recovered) == ()

    recovered_kernel = _required_null_kernel()
    _close_required_null_gaps(recovered_kernel)
    fixed_outcome = recovered_kernel.finalize(
        _confirmed_decision(records, root_evidence=right_root)
    )
    assert fixed_outcome.status is KernelFinalStatus.CONFIRMED
    assert fixed_outcome.root_cause_code == "SOURCE_REQUIRED_FIELD_NULL"


# ---------------------------------------------------------------------------
# Replay 9: budget-boundary-ninth-call-refused
# ---------------------------------------------------------------------------


class _SequentialReader:
    """Returns one fresh valid record per call and counts how many ran."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> tuple[EvidenceRecord, ...]:
        self.calls += 1
        return (
            _record(
                EvidenceType.DBT_RUN_RESULTS,
                EvidenceSource.DBT_RUN_RESULTS,
                f"call-{self.calls}",
                DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=RUN_ID,
                    run_status="SUCCEEDED",
                    dbt_exit_code=0,
                    failed_nodes=(),
                    skipped_nodes=(),
                ),
            ),
        )


def _replay_context(tmp_path: Path) -> ObservableRunContext:
    return ObservableRunContext(
        run_id=RUN_ID,
        artifact_dir=tmp_path,
        runtime={"observable_relations": {"schema": [], "profile": [], "history": []}},
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_TEST_FAILED",
            summary="A required-field dbt test failed in the order pipeline.",
            subjects=(TEST_ID,),
            logical_observed_at=OBSERVED_AT,
            observations=(),
        ),
    )


@_replay("budget-boundary-ninth-call-refused")
def test_replay_budget_boundary_ninth_call_refused(tmp_path: Path) -> None:
    entry = _entry("budget-boundary-ninth-call-refused")
    assert "TOOL_CALL_LIMIT" in entry["expected"]
    reader = _SequentialReader()
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=SimpleNamespace(),
        context=_replay_context(tmp_path),
    )

    for _ in range(FIXED_RULE_TOOL_LIMIT + 1):
        runner._call("get_dbt_run_results", {"run_id": RUN_ID}, reader)

    tool_events = tuple(
        event for event in runner._trace if isinstance(event, ToolTraceEvent)
    )
    assert len(tool_events) == FIXED_RULE_TOOL_LIMIT + 1
    assert tool_events[-1].error_code == "TOOL_CALL_LIMIT"
    assert all(event.error_code is None for event in tool_events[:-1])
    # The refused ninth call never reached the tool and recorded no evidence.
    assert reader.calls == FIXED_RULE_TOOL_LIMIT
    assert len(runner._records) == FIXED_RULE_TOOL_LIMIT
