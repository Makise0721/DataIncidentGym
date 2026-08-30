from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    EvidenceGapKind,
    EvidenceGapStatus,
    InvestigationIntent,
    KernelError,
)
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)

RUN_ID = "a" * 32


def _kernel(*, tool_call_limit: int = 8) -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        ),
        model_request_limit=8,
        tool_call_limit=tool_call_limit,
        observable_relations=("raw_payments",),
    )


def _run_results() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.jaffle_shop.stg_payments",),
            skipped_nodes=(),
        ),
    )


def test_kernel_state_is_public_case_neutral_and_budgeted() -> None:
    state = _kernel().snapshot(model_requests_used=0)

    assert state.schema_version == "p1.investigation.v1"
    assert state.run_id == RUN_ID
    assert state.tool_call_limit == 8
    assert state.tool_calls_used == 0
    assert state.model_requests_remaining == 8
    assert "incident_case_id" not in state.model_dump()
    assert "expected_status" not in state.model_dump()


def test_kernel_business_tool_arguments_do_not_carry_kernel_intent() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=intent,
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID, "gap_id": "g_locate"},
        )

    assert error.value.code == "ARGUMENTS_INVALID"
    assert kernel.snapshot(model_requests_used=0).tool_calls_used == 0


def test_kernel_closes_a_gap_only_after_compatible_evidence() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)
    prepared = kernel.prepare_tool(
        intent=intent,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )

    record = _run_results()
    assert kernel.record_tool_result(prepared, (record,)) == (record,)
    state = kernel.snapshot(model_requests_used=1)

    assert state.gaps[0].status is EvidenceGapStatus.CLOSED
    assert state.evidence_inventory == (record.evidence_id,)
    assert state.tool_calls_used == 1


def test_kernel_rejects_unproven_node_and_relation_arguments() -> None:
    kernel = _kernel()
    node_intent = InvestigationIntent(gap_id="g_explain", gap_kind=EvidenceGapKind.EXPLAIN_FAILURE)
    with pytest.raises(KernelError, match="NODE_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=node_intent,
            tool_name="get_dbt_node_error",
            arguments={"run_id": RUN_ID, "node_id": "model.unknown"},
        )

    relation_intent = InvestigationIntent(
        gap_id="g_schema",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
    )
    with pytest.raises(KernelError, match="RELATION_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=relation_intent,
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )


def test_kernel_rejects_duplicate_tool_attempt_before_state_change() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)
    arguments = {"run_id": RUN_ID}
    kernel.prepare_tool(intent=intent, tool_name="get_dbt_run_results", arguments=arguments)
    before = kernel.snapshot(model_requests_used=0)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(gap_id="g_again", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
            tool_name="get_dbt_run_results",
            arguments=arguments,
        )

    assert error.value.code == "DUPLICATE_TOOL_CALL"
    assert kernel.snapshot(model_requests_used=0) == before


def test_kernel_rejects_cross_run_evidence_without_recording_it() -> None:
    kernel = _kernel()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    other_run_record = _run_results().model_copy(update={"run_id": "b" * 32})

    with pytest.raises(KernelError) as error:
        kernel.record_tool_result(prepared, (other_run_record,))

    assert error.value.code == "RUN_CONTEXT_MISMATCH"
    assert kernel.evidence_records == ()
    assert kernel.snapshot(model_requests_used=0).evidence_inventory == ()


def test_kernel_rejects_tool_budget_exhaustion_before_state_change() -> None:
    kernel = _kernel(tool_call_limit=1)
    kernel.prepare_tool(
        intent=InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    before = kernel.snapshot(model_requests_used=0)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )

    assert error.value.code == "TOOL_CALL_LIMIT"
    assert kernel.snapshot(model_requests_used=0) == before
