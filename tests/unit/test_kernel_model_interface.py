"""Model-interface regressions: idempotent hypothesis declarations and the
single dynamic investigation ledger.

Only public, case-neutral surfaces are exercised here; no private scenario data
and no real model provider is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    InvestigationIntent,
    KernelError,
)
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)

RUN_ID = "c" * 32
CODES = (
    "SOURCE_PAYMENT_INGESTION_LOSS",
    "NORMAL_BUSINESS_PAYMENT_DECLINE",
)

_LOSS = Hypothesis(
    hypothesis_id="h_ingestion_loss",
    root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
)
_DECLINE = Hypothesis(
    hypothesis_id="h_normal_decline",
    root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
)


def _kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=CODES,
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        observable_profile_relations=("raw_payments", "raw_orders"),
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


def _locate(kernel: DiagnosticKernel, gap_id: str = "g_locate", **extra: object):
    return kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            new_hypotheses=extra.pop("new_hypotheses", ()),  # type: ignore[arg-type]
            hypothesis_ids=extra.pop("hypothesis_ids", ()),  # type: ignore[arg-type]
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )


def _profile(
    kernel: DiagnosticKernel,
    *,
    gap_id: str,
    new_hypotheses: tuple[Hypothesis, ...] = (),
    hypothesis_ids: tuple[str, ...] = (),
    relation: str = "raw_payments",
):
    return kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
            new_hypotheses=new_hypotheses,
            hypothesis_ids=hypothesis_ids,
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": relation},
    )


def _seeded() -> DiagnosticKernel:
    """A kernel that located the failure and registered both hypotheses."""

    kernel = _kernel()
    prepared = _locate(kernel, new_hypotheses=(_LOSS, _DECLINE))
    kernel.record_tool_result(prepared, (_run_results(),))
    return kernel


# ------------------------------------------------------- idempotent declarations


def test_identical_redeclaration_allows_the_new_business_query() -> None:
    """Re-sending the same ID with the same root cause code is idempotent: the
    new query proceeds and nothing is registered twice."""

    kernel = _seeded()
    prepared = _profile(kernel, gap_id="g_profile", new_hypotheses=(_LOSS, _DECLINE))
    state = kernel.snapshot(model_requests_used=2)

    assert [item.hypothesis_id for item in state.hypotheses] == [
        "h_ingestion_loss",
        "h_normal_decline",
    ]
    assert prepared.gap_id == "g_profile"
    assert [gap.status for gap in state.gaps] == [EvidenceGapStatus.CLOSED, EvidenceGapStatus.OPEN]
    assert state.tool_calls_used == 2


def test_conflicting_redeclaration_is_still_rejected() -> None:
    """Same ID with a different root cause code keeps the original rejection and
    leaves the registered definition untouched."""

    kernel = _seeded()
    before = kernel.snapshot(model_requests_used=1)

    with pytest.raises(KernelError) as error:
        _profile(
            kernel,
            gap_id="g_profile",
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_ingestion_loss",
                    root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
                ),
            ),
        )

    assert error.value.code == "DUPLICATE_HYPOTHESIS"
    after = kernel.snapshot(model_requests_used=1)
    assert [item.root_cause_code for item in after.hypotheses] == [
        item.root_cause_code for item in before.hypotheses
    ]
    assert len(after.gaps) == len(before.gaps)
    assert after.tool_calls_used == before.tool_calls_used


def test_mixed_declaration_registers_only_the_new_hypothesis() -> None:
    """An already-registered item plus one new item registers exactly the new
    one, in declaration order."""

    kernel = _seeded()
    added = Hypothesis(
        hypothesis_id="h_late_arrival",
        root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
    )
    _profile(
        kernel,
        gap_id="g_profile",
        new_hypotheses=(_LOSS, added),
        hypothesis_ids=("h_ingestion_loss",),
    )
    state = kernel.snapshot(model_requests_used=2)

    assert [item.hypothesis_id for item in state.hypotheses] == [
        "h_ingestion_loss",
        "h_normal_decline",
        "h_late_arrival",
    ]


def test_conflicting_declaration_after_a_valid_one_commits_nothing() -> None:
    """A conflicting redefinition later in the same array must not leave the
    earlier new hypothesis half-registered, nor open a gap."""

    kernel = _seeded()
    before = kernel.snapshot(model_requests_used=1)

    with pytest.raises(KernelError) as error:
        _profile(
            kernel,
            gap_id="g_profile",
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_late_arrival",
                    root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
                ),
                Hypothesis(
                    hypothesis_id="h_normal_decline",
                    root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
                ),
            ),
        )

    assert error.value.code == "DUPLICATE_HYPOTHESIS"
    after = kernel.snapshot(model_requests_used=1)
    assert [item.hypothesis_id for item in after.hypotheses] == [
        item.hypothesis_id for item in before.hypotheses
    ]
    assert len(after.gaps) == len(before.gaps)
    assert after.tool_calls_used == before.tool_calls_used
    assert after.revision == before.revision


def test_unknown_ontology_code_is_still_rejected() -> None:
    kernel = _seeded()
    before = kernel.snapshot(model_requests_used=1)

    with pytest.raises(KernelError) as error:
        _profile(
            kernel,
            gap_id="g_profile",
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_new",
                    root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                ),
            ),
        )

    assert error.value.code == "ONTOLOGY_CODE_UNKNOWN"
    after = kernel.snapshot(model_requests_used=1)
    assert [item.hypothesis_id for item in after.hypotheses] == [
        item.hypothesis_id for item in before.hypotheses
    ]
    assert len(after.gaps) == len(before.gaps)


def test_unregistered_reference_is_still_rejected() -> None:
    kernel = _seeded()

    with pytest.raises(KernelError) as error:
        _profile(kernel, gap_id="g_profile", hypothesis_ids=("h_missing",))

    assert error.value.code == "HYPOTHESIS_REFERENCE_UNKNOWN"


def test_duplicate_query_is_still_rejected_with_identical_declarations() -> None:
    """Idempotent registration must not become a way around query deduplication."""

    kernel = _seeded()
    before = kernel.snapshot(model_requests_used=1)

    with pytest.raises(KernelError) as error:
        _locate(kernel, new_hypotheses=(_LOSS, _DECLINE))

    assert error.value.code == "DUPLICATE_TOOL_CALL"
    after = kernel.snapshot(model_requests_used=1)
    assert [item.hypothesis_id for item in after.hypotheses] == [
        item.hypothesis_id for item in before.hypotheses
    ]


def test_duplicate_ids_inside_one_intent_are_still_rejected() -> None:
    with pytest.raises(ValueError):
        InvestigationIntent(
            gap_id="g_profile",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
            new_hypotheses=(_LOSS, _LOSS),
        )


def test_same_root_code_under_two_ids_stays_two_declarations() -> None:
    kernel = _seeded()
    twin = Hypothesis(
        hypothesis_id="h_decline_twin",
        root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
    )
    _profile(kernel, gap_id="g_profile", new_hypotheses=(_DECLINE, twin))
    state = kernel.snapshot(model_requests_used=2)

    assert len(state.hypotheses) == 3
    assert len({item.hypothesis_id for item in state.hypotheses}) == 3


def test_idempotent_declaration_with_blocked_probe_keeps_one_blocked_gap() -> None:
    """A permission-boundary probe that re-sends a registered hypothesis still
    records exactly one BLOCKED receipt and registers nothing twice."""

    kernel = _seeded()
    with pytest.raises(KernelError) as error:
        _profile(
            kernel,
            gap_id="g_probe",
            new_hypotheses=(_LOSS, _DECLINE),
            relation="raw_customers",
        )

    assert error.value.code == "RELATION_NOT_ALLOWED"
    state = kernel.snapshot(model_requests_used=1)
    blocked = [gap for gap in state.gaps if gap.status is EvidenceGapStatus.BLOCKED]
    assert len(blocked) == 1
    assert blocked[0].error_code == "RELATION_NOT_ALLOWED"
    assert [item.hypothesis_id for item in state.hypotheses] == [
        "h_ingestion_loss",
        "h_normal_decline",
    ]


def test_blocked_probe_registers_only_the_new_hypothesis() -> None:
    kernel = _seeded()
    with pytest.raises(KernelError):
        _profile(
            kernel,
            gap_id="g_probe",
            new_hypotheses=(
                _LOSS,
                Hypothesis(
                    hypothesis_id="h_late_arrival",
                    root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
                ),
            ),
            relation="raw_customers",
        )

    state = kernel.snapshot(model_requests_used=1)
    assert [item.hypothesis_id for item in state.hypotheses] == [
        "h_ingestion_loss",
        "h_normal_decline",
        "h_late_arrival",
    ]
    assert len([gap for gap in state.gaps if gap.status is EvidenceGapStatus.BLOCKED]) == 1


def test_rejected_query_does_not_register_its_declarations() -> None:
    """A call rejected for a non-permission reason must not register hypotheses."""

    kernel = _seeded()
    before = kernel.snapshot(model_requests_used=1)
    late = Hypothesis(
        hypothesis_id="h_late_arrival",
        root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE",
    )

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_profile",
                gap_kind=EvidenceGapKind.PROFILE_RELATION,
                new_hypotheses=(late,),
            ),
            tool_name="get_relation_data_profile",
            arguments={"relation_name": "raw_payments", "node_id": "seed.jaffle_shop.x"},
        )

    assert error.value.code == "ARGUMENTS_INVALID"
    after = kernel.snapshot(model_requests_used=1)
    assert [item.hypothesis_id for item in after.hypotheses] == [
        item.hypothesis_id for item in before.hypotheses
    ]



