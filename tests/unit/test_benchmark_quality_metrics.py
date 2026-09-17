"""T07 metrics: abstention, status confusion, claim support and citations.

Every fixture is small enough to verify by hand: the expected counts are written
next to the assertions that consume them, and every claim verdict comes from the
evaluator's own per-claim rules rather than from a hand-written boolean.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from data_incident_gym.benchmark_report import (
    _abstention_metrics,
    _citation_metrics,
    _claim_metrics,
    _status_confusion,
)
from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisClaim,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    HealthStateClaim,
    RootCauseClaim,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_agent import policy_identity_for_strategy
from data_incident_gym.evaluation import (
    EvaluationStatus,
    _claim_evidence_compatible,
    claim_support_verdicts,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
)
from data_incident_gym.profiles import ColumnProfileFact, RelationProfileSnapshot
from data_incident_gym.scenarios import load_scenario_spec

CASE_ID = "required_null_order_customer_a"
CONTROL_ID = "order_volume_pattern_a"
RUN_ID = "a" * 32
DIRECT_FAILURE = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
ORDERS_ASSET = "model.jaffle_shop.orders"
ROOT_CAUSE = "SOURCE_REQUIRED_FIELD_NULL"
OBSERVED_AT = datetime(2026, 9, 16, tzinfo=UTC)


def _node_error(node_id: str, *, resource_type: str = "test") -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=node_id,
        observed_at=OBSERVED_AT,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=node_id,
            resource_type=resource_type,
            status="fail",
            message="null value in column user_id",
        ),
    )


def _profile(column_name: str, null_count: int) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_DATA_PROFILE,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject="raw_orders",
        observed_at=OBSERVED_AT,
        content=RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_orders",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name="raw_orders",
                row_count=100,
                columns=(
                    ColumnProfileFact(
                        column_name=column_name,
                        null_count=null_count,
                        distinct_count=99,
                    ),
                ),
            ),
        ),
    )


def _orders_lineage() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject="raw_orders",
        observed_at=OBSERVED_AT,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="raw_orders",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id=ORDERS_ASSET,
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
            ),
        ),
    )


def _root_claim(*evidence_ids: str) -> RootCauseClaim:
    return RootCauseClaim(
        kind="ROOT_CAUSE",
        root_cause_code=ROOT_CAUSE,
        evidence_ids=tuple(evidence_ids),
    )


def _asset_claim(*evidence_ids: str) -> AffectedAssetClaim:
    return AffectedAssetClaim(
        kind="AFFECTED_ASSET",
        asset=ORDERS_ASSET,
        evidence_ids=tuple(evidence_ids),
    )


def _confirmed_diagnosis(claims: tuple[DiagnosisClaim, ...]) -> Diagnosis:
    roots = [claim for claim in claims if claim.kind == "ROOT_CAUSE"]
    assets = [claim for claim in claims if claim.kind == "AFFECTED_ASSET"]
    return Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        summary="fixture",
        confidence=0.5,
        root_cause_code=roots[0].root_cause_code if roots else None,
        affected_assets=tuple(claim.asset for claim in assets),
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_id for claim in claims for evidence_id in claim.evidence_ids
            )
        ),
        claims=claims,
    )


def _no_incident_diagnosis(claims: tuple[DiagnosisClaim, ...]) -> Diagnosis:
    return Diagnosis(
        status=DiagnosisStatus.NO_INCIDENT,
        run_id=RUN_ID,
        summary="fixture",
        confidence=0.5,
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_id for claim in claims for evidence_id in claim.evidence_ids
            )
        ),
        claims=claims,
    )


def _abstaining_diagnosis() -> Diagnosis:
    return Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="fixture",
        confidence=0.5,
        unresolved_evidence=(
            UnresolvedEvidence(
                evidence_kind="RELATION_SCHEMA",
                subject="raw_customers",
                reason_code="NOT_OBSERVABLE",
            ),
        ),
    )


def _model_error_diagnosis() -> Diagnosis:
    return Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )


def _cell(
    *,
    expected: str,
    diagnosis: Diagnosis,
    passed: bool = True,
    invalid: bool = False,
    records: tuple[EvidenceRecord, ...] = (),
    case_id: str = CASE_ID,
) -> dict[str, object]:
    scenario = load_scenario_spec(case_id)
    return {
        "cell": SimpleNamespace(
            incident_case_id=case_id,
            repeat_index=1,
            model_backed=True,
            run_id=RUN_ID,
        ),
        "metadata": SimpleNamespace(variant_role="TEST_CONFIRMABLE"),
        "diagnosis": diagnosis,
        "evaluation": SimpleNamespace(
            expected_status=expected,
            status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
        ),
        "trace": (),
        "scenario": scenario,
        "records": records,
        "claim_verdicts": claim_support_verdicts(scenario, diagnosis, records),
        "environment_gates": ({"gate": "RECOVERY_HEALTHY", "reason_code": "INVALID"},)
        if invalid
        else (),
    }


def test_all_abstaining_cells_report_full_over_abstention() -> None:
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_abstaining_diagnosis(),
            passed=False,
        )
        for _ in range(2)
    ]

    metrics = _abstention_metrics(cells)  # type: ignore[arg-type]

    assert metrics["cells"] == {"valid": 2, "invalid_excluded": 0}
    assert metrics["confirmable_over_abstention"]["numerator"] == 2
    assert metrics["confirmable_over_abstention"]["denominator"] == 2
    assert metrics["confirmable_over_abstention"]["rate"] == 1.0
    # No healthy-control cell exists, so its rate is null with a stable reason.
    assert metrics["health_over_abstention"]["rate"] is None
    assert metrics["health_over_abstention"]["zero_denominator_reason"] == (
        "no valid healthy-control cells"
    )
    assert metrics["qualified_abstention"]["rate"] is None
    assert metrics["qualified_abstention"]["zero_denominator_reason"] == (
        "no valid expected-abstention cells"
    )
    # Both abstentions are over-abstentions on confirmable contracts, so no
    # predicted positive is correct.
    assert metrics["abstention_precision"]["numerator"] == 0
    assert metrics["abstention_precision"]["denominator"] == 2
    assert metrics["abstention_precision"]["rate"] == 0.0


def test_abstention_precision_recall_and_qualified_rate_hand_checked() -> None:
    cells = [
        # Expected to abstain and it does, with the whole cell passing.
        _cell(expected="INSUFFICIENT_EVIDENCE", diagnosis=_abstaining_diagnosis()),
        # Expected to abstain and it does, but the cell fails its checks.
        _cell(
            expected="INSUFFICIENT_EVIDENCE",
            diagnosis=_abstaining_diagnosis(),
            passed=False,
        ),
        # Over-abstention on a confirmable contract: a false positive.
        _cell(expected="CONFIRMED", diagnosis=_abstaining_diagnosis(), passed=False),
    ]

    metrics = _abstention_metrics(cells)  # type: ignore[arg-type]

    assert metrics["abstention_recall"]["numerator"] == 2
    assert metrics["abstention_recall"]["denominator"] == 2
    assert metrics["abstention_recall"]["rate"] == 1.0
    assert metrics["abstention_precision"]["numerator"] == 2
    assert metrics["abstention_precision"]["denominator"] == 3
    assert metrics["qualified_abstention"]["numerator"] == 1
    assert metrics["qualified_abstention"]["denominator"] == 2
    assert metrics["qualified_abstention"]["rate"] == 0.5


def test_fully_confirmed_cells_report_zero_over_abstention() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    claims = (
        _root_claim(node_error.evidence_id, profile.evidence_id),
        _asset_claim(lineage.evidence_id),
    )
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(node_error, profile, lineage),
        )
        for _ in range(2)
    ]

    metrics = _abstention_metrics(cells)  # type: ignore[arg-type]

    assert metrics["confirmable_over_abstention"]["numerator"] == 0
    assert metrics["confirmable_over_abstention"]["denominator"] == 2
    assert metrics["confirmable_over_abstention"]["rate"] == 0.0
    assert metrics["abstention_recall"]["rate"] is None
    assert metrics["abstention_recall"]["zero_denominator_reason"] == (
        "no valid expected-abstention cells"
    )


def test_healthy_false_positives_are_not_abstention() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    claims = (
        _root_claim(node_error.evidence_id, profile.evidence_id),
        _asset_claim(lineage.evidence_id),
    )
    cells = [
        _cell(
            expected="NO_INCIDENT",
            diagnosis=_confirmed_diagnosis(claims),
            passed=False,
            records=(node_error, profile, lineage),
            case_id=CONTROL_ID,
        )
    ]

    abstention = _abstention_metrics(cells)  # type: ignore[arg-type]
    confusion = _status_confusion(cells)  # type: ignore[arg-type]

    assert abstention["health_over_abstention"]["numerator"] == 0
    assert abstention["health_over_abstention"]["denominator"] == 1
    assert confusion["matrix"]["NO_INCIDENT"]["CONFIRMED"] == 1
    assert confusion["matrix"]["NO_INCIDENT"]["INSUFFICIENT_EVIDENCE"] == 0


def test_empty_sets_render_null_with_reasons() -> None:
    abstention = _abstention_metrics([])
    confusion = _status_confusion([])

    for metric in (
        abstention["confirmable_over_abstention"],
        abstention["health_over_abstention"],
        abstention["qualified_abstention"],
        abstention["abstention_precision"],
        abstention["abstention_recall"],
    ):
        assert metric["numerator"] == 0
        assert metric["denominator"] == 0
        assert metric["rate"] is None
        assert metric["zero_denominator_reason"]
        assert metric["applicable_set"]
    assert confusion["model_error_rate"]["rate"] is None
    assert confusion["model_error_rate"]["zero_denominator_reason"] == "no valid cells"
    assert all(
        count == 0
        for row in confusion["matrix"].values()
        for count in row.values()
    )


def test_model_error_is_a_failed_run_not_an_abstention() -> None:
    cells = [
        _cell(expected="CONFIRMED", diagnosis=_model_error_diagnosis(), passed=False),
        _cell(expected="CONFIRMED", diagnosis=_abstaining_diagnosis(), passed=False),
    ]

    abstention = _abstention_metrics(cells)  # type: ignore[arg-type]
    confusion = _status_confusion(cells)  # type: ignore[arg-type]

    # The MODEL_ERROR cell stays in the denominator and is never an abstention.
    assert abstention["confirmable_over_abstention"]["numerator"] == 1
    assert abstention["confirmable_over_abstention"]["denominator"] == 2
    assert abstention["abstention_precision"]["denominator"] == 1
    assert confusion["matrix"]["CONFIRMED"]["MODEL_ERROR"] == 1
    assert confusion["model_error_rate"]["numerator"] == 1
    assert confusion["model_error_rate"]["denominator"] == 2
    assert confusion["model_error_rate"]["rate"] == 0.5


def test_invalid_environment_cells_are_isolated_from_every_denominator() -> None:
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(
                (
                    _root_claim(_node_error(DIRECT_FAILURE).evidence_id),
                    _asset_claim(_orders_lineage().evidence_id),
                )
            ),
            records=(_node_error(DIRECT_FAILURE), _orders_lineage()),
        ),
        _cell(expected="CONFIRMED", diagnosis=_abstaining_diagnosis(), passed=False, invalid=True),
    ]

    abstention = _abstention_metrics(cells)  # type: ignore[arg-type]
    confusion = _status_confusion(cells)  # type: ignore[arg-type]

    # The invalid cell would have been an over-abstention; it is excluded and
    # counted instead of scored.
    assert abstention["cells"] == {"valid": 1, "invalid_excluded": 1}
    assert abstention["confirmable_over_abstention"]["numerator"] == 0
    assert abstention["confirmable_over_abstention"]["denominator"] == 1
    assert confusion["valid_cells"] == 1
    assert confusion["invalid_cells"] == 1


def test_claim_support_coverage_counts_applicable_kinds_only() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    node_error_orders = _node_error(ORDERS_ASSET, resource_type="model")
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    claims = (
        _root_claim(node_error.evidence_id, profile.evidence_id),
        _asset_claim(lineage.evidence_id, node_error_orders.evidence_id),
        HealthStateClaim(
            kind="HEALTH_STATE",
            relation_name="raw_orders",
            history_name="order_count_by_day",
            bucket="2018-04-02",
            current_value=1,
            evidence_ids=(lineage.evidence_id,),
        ),
    )
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(node_error, node_error_orders, profile, lineage),
        )
    ]

    metrics = _claim_metrics(cells)  # type: ignore[arg-type]

    # Two applicable claims, both supported; the health claim carries no rule
    # under a CONFIRMED contract and is listed instead of scored.
    assert metrics["support_coverage"]["numerator"] == 2
    assert metrics["support_coverage"]["denominator"] == 2
    assert metrics["support_coverage"]["rate"] == 1.0
    assert metrics["inapplicable_claim_types"] == {"HEALTH_STATE": 1}
    assert metrics["unsupported_claim_types"] == {}


def test_unsupported_claim_lowers_coverage() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    # The root cause needs the failing test *and* the null profile; citing only
    # the profile cannot support it.
    claims = (_root_claim(profile.evidence_id), _asset_claim(lineage.evidence_id))
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(node_error, profile, lineage),
        )
    ]

    metrics = _claim_metrics(cells)  # type: ignore[arg-type]

    assert metrics["support_coverage"]["numerator"] == 1
    assert metrics["support_coverage"]["denominator"] == 2
    assert metrics["support_coverage"]["rate"] == 0.5


def test_meaningless_citations_never_raise_coverage() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    node_error_orders = _node_error(ORDERS_ASSET, resource_type="model")
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    unsupported = (_root_claim(profile.evidence_id), _asset_claim(lineage.evidence_id))
    padded = (
        # Padding the unsupported root claim with an existing but unrelated
        # citation must not create support out of nothing.
        _root_claim(profile.evidence_id, lineage.evidence_id),
        # Padding a supported asset claim with a non-contributing citation must
        # not change its verdict either.
        _asset_claim(lineage.evidence_id, node_error.evidence_id),
    )
    records = (node_error, node_error_orders, profile, lineage)

    before = _claim_metrics(
        [
            _cell(
                expected="CONFIRMED",
                diagnosis=_confirmed_diagnosis(unsupported),
                records=records,
            )
        ]  # type: ignore[list-item]
    )
    after = _claim_metrics(
        [
            _cell(
                expected="CONFIRMED",
                diagnosis=_confirmed_diagnosis(padded),
                records=records,
            )
        ]  # type: ignore[list-item]
    )
    citations = _citation_metrics(
        [
            _cell(
                expected="CONFIRMED",
                diagnosis=_confirmed_diagnosis(padded),
                records=records,
            )
        ]  # type: ignore[list-item]
    )

    assert before["support_coverage"]["numerator"] == 1
    assert after["support_coverage"]["numerator"] == 1
    assert after["support_coverage"]["denominator"] == 2
    assert citations["redundancy"]["redundant_citations"] >= 1
    assert "not an error" in citations["redundancy"]["note"]


def test_citation_metrics_hand_checked_for_joint_root_and_direct_asset() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    claims = (
        _root_claim(node_error.evidence_id, profile.evidence_id),
        _asset_claim(lineage.evidence_id),
    )
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(node_error, profile, lineage),
        )
    ]

    metrics = _citation_metrics(cells)  # type: ignore[arg-type]

    # Three distinct citations, all present in the inventory.
    assert metrics["existence"]["numerator"] == 3
    assert metrics["existence"]["denominator"] == 3
    # Only the asset claim can be supported by a single citation: the root cause
    # needs the failing test and the null profile together.
    assert metrics["single_record_support"]["numerator"] == 1
    assert metrics["single_record_support"]["denominator"] == 3
    # Every citation is load-bearing: removing any one of them withdraws support.
    assert metrics["redundancy"]["load_bearing"]["numerator"] == 3
    assert metrics["redundancy"]["load_bearing"]["denominator"] == 3
    assert metrics["redundancy"]["redundant_citations"] == 0


def test_redundant_citations_are_reported_not_penalized() -> None:
    node_error_orders = _node_error(ORDERS_ASSET, resource_type="model")
    lineage = _orders_lineage()
    claims = (
        _root_claim(_node_error(DIRECT_FAILURE).evidence_id, _profile("user_id", 1).evidence_id),
        # Either citation alone proves the asset; the second one is redundant.
        _asset_claim(node_error_orders.evidence_id, lineage.evidence_id),
    )
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(
                _node_error(DIRECT_FAILURE),
                _profile("user_id", 1),
                node_error_orders,
                lineage,
            ),
        )
    ]

    metrics = _citation_metrics(cells)  # type: ignore[arg-type]

    assert metrics["redundancy"]["load_bearing"]["numerator"] == 2
    assert metrics["redundancy"]["load_bearing"]["denominator"] == 4
    assert metrics["redundancy"]["redundant_citations"] == 2
    assert "not an error" in metrics["redundancy"]["note"]


def test_health_claim_citations_are_excluded_from_single_record_support() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    lineage = _orders_lineage()
    health = HealthStateClaim(
        kind="HEALTH_STATE",
        relation_name="raw_orders",
        history_name="order_count_by_day",
        bucket="2018-04-02",
        current_value=1,
        evidence_ids=(node_error.evidence_id, lineage.evidence_id),
    )
    cells = [
        _cell(
            expected="NO_INCIDENT",
            diagnosis=_no_incident_diagnosis((health,)),
            records=(node_error, lineage),
            case_id=CONTROL_ID,
        )
    ]

    metrics = _citation_metrics(cells)  # type: ignore[arg-type]

    assert metrics["existence"]["numerator"] == 2
    assert metrics["existence"]["denominator"] == 2
    assert metrics["health_claim_citations"] == 2
    # A health reading is proven jointly, so no citation is scored alone.
    assert metrics["single_record_support"]["denominator"] == 0
    assert metrics["single_record_support"]["rate"] is None
    assert metrics["single_record_support"]["zero_denominator_reason"] == (
        "no single-record claim citations in valid cells"
    )


def test_missing_citation_is_not_existence_and_not_support() -> None:
    node_error = _node_error(DIRECT_FAILURE)
    profile = _profile("user_id", 1)
    missing = "ev_" + "f" * 64
    claims = (
        _root_claim(node_error.evidence_id, profile.evidence_id),
        _asset_claim(missing),
    )
    cells = [
        _cell(
            expected="CONFIRMED",
            diagnosis=_confirmed_diagnosis(claims),
            records=(node_error, profile),
        )
    ]

    claims_metrics = _claim_metrics(cells)  # type: ignore[arg-type]
    citation_metrics = _citation_metrics(cells)  # type: ignore[arg-type]

    assert claims_metrics["support_coverage"]["numerator"] == 1
    assert claims_metrics["support_coverage"]["denominator"] == 2
    assert citation_metrics["existence"]["numerator"] == 2
    assert citation_metrics["existence"]["denominator"] == 3


def _run(diagnosis: Diagnosis, records: tuple[EvidenceRecord, ...]) -> DiagnosisRunResult:
    return DiagnosisRunResult(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        policy_identity=policy_identity_for_strategy(DiagnosticStrategy.STATIC_SKILL),
        diagnosis=diagnosis,
        evidence_records=records,
        trace=(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=DiagnosticStrategy.STATIC_SKILL,
                status=diagnosis.status,
                evidence_inventory=tuple(record.evidence_id for record in records),
            ),
        ),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic",
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=0,
        ),
        kernel_state=None,
    )


def test_unrelated_citations_never_flip_the_cell_level_claim_gate() -> None:
    """The hard gate stays exactly as strict as the per-claim rules: padding an
    unsupported claim with existing but unrelated citations cannot turn the
    cell's CLAIM_EVIDENCE_COMPATIBLE verdict into a pass.

    Adding a *relevant* citation can legitimately support a claim — that is the
    model citing the right record — but a record the rule does not consume
    never counts.
    """

    scenario = load_scenario_spec(CASE_ID)
    node_error = _node_error(DIRECT_FAILURE)
    node_error_orders = _node_error(ORDERS_ASSET, resource_type="model")
    profile = _profile("user_id", 1)
    lineage = _orders_lineage()
    records = (node_error, node_error_orders, profile, lineage)

    correct = _confirmed_diagnosis(
        (
            _root_claim(node_error.evidence_id, profile.evidence_id),
            _asset_claim(lineage.evidence_id),
        )
    )
    unsupported = _confirmed_diagnosis(
        (_root_claim(profile.evidence_id), _asset_claim(lineage.evidence_id))
    )
    # Same claims plus citations the root-cause and asset rules never consume.
    padded = _confirmed_diagnosis(
        (
            _root_claim(profile.evidence_id, lineage.evidence_id, node_error_orders.evidence_id),
            _asset_claim(lineage.evidence_id, node_error_orders.evidence_id),
        )
    )

    assert _claim_evidence_compatible(scenario, _run(correct, records)) is True
    assert _claim_evidence_compatible(scenario, _run(unsupported, records)) is False
    assert _claim_evidence_compatible(scenario, _run(padded, records)) is False
