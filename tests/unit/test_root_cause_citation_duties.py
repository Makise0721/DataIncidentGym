"""Root-cause citation duties per dispatch branch, verified through finalize.

Seven shapes, each a complete decision over public evidence with only the
ROOT_CAUSE citation set varied, so a rejection can only come from the citation
requirement under test. Branch duties come from the real dispatch order in
``validate_root_cause_evidence``; nothing here asserts a universal rule.

Evidence shapes follow the p1-formal-v10 artifacts:
- seq62: SOURCE_REQUIRED_FIELD_NULL over a failed test node (general branch);
- seq66: SOURCE_SEMANTIC_PAYMENT_DUPLICATE over raw_payments (semantic branch);
- exact duplicate: its own domain data (id duplicates positive), because the
  seq66 profile has zero id duplicates and cannot satisfy that branch at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
from data_incident_gym.profiles import (
    DuplicateProfileFact,
    GroupProfileFact,
    RelationProfileSnapshot,
)

RUN_ID = "9" * 32
FAILED_TEST_NODE = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"


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


def _close(
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


# ------------------------------------------------------------------ seq62 shape


def _null_kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders", "raw_customers"),
        observable_profile_relations=("raw_orders", "raw_customers"),
        incident_subjects=(FAILED_TEST_NODE,),
        lineage_node_candidates=(FAILED_TEST_NODE,),
    )


class _NullEvidence:
    def __init__(self) -> None:
        self.run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status="FAILED",
                dbt_exit_code=1,
                failed_nodes=(FAILED_TEST_NODE,),
                skipped_nodes=(),
            ),
        )
        self.node_error = _record(
            EvidenceType.DBT_NODE_ERROR,
            EvidenceSource.DBT_RUN_RESULTS,
            FAILED_TEST_NODE,
            DbtNodeErrorFact(
                kind="DBT_NODE_ERROR",
                run_id=RUN_ID,
                node_id=FAILED_TEST_NODE,
                resource_type="test",
                status="fail",
                message="NULL customer_id found",
            ),
        )
        self.lineage = _record(
            EvidenceType.DBT_LINEAGE,
            EvidenceSource.DBT_MANIFEST,
            FAILED_TEST_NODE,
            DbtLineageFact(
                kind="DBT_LINEAGE",
                run_id=RUN_ID,
                node_id=FAILED_TEST_NODE,
                direction="upstream",
                related_nodes=(
                    DbtLineageNode(
                        node_id="model.jaffle_shop.orders",
                        resource_type="model",
                        name="orders",
                        distance=1,
                    ),
                    DbtLineageNode(
                        node_id="seed.jaffle_shop.raw_orders",
                        resource_type="seed",
                        name="raw_orders",
                        distance=2,
                    ),
                ),
            ),
        )
        self.profile = _record(
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
                    columns=(),
                    business_key_duplicates=(DuplicateProfileFact(name="id", duplicate_count=0),),
                ),
            ),
        )
        self.schema = _record(
            EvidenceType.RELATION_SCHEMA,
            EvidenceSource.POSTGRES_CATALOG,
            "raw_orders",
            RelationSchemaFact(
                kind="RELATION_SCHEMA",
                run_id=RUN_ID,
                schema_name="analytics",
                relation_name="raw_orders",
                columns=(
                    RelationSchemaColumn(
                        name="customer_id",
                        data_type="integer",
                        nullable=True,
                        ordinal_position=1,
                    ),
                ),
            ),
        )


def _null_kernel_with_evidence() -> tuple[DiagnosticKernel, _NullEvidence]:
    evidence = _NullEvidence()
    kernel = _null_kernel()
    _close(
        kernel,
        gap_id="g_run",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=evidence.run,
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_null_customer_source",
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
            ),
            Hypothesis(
                hypothesis_id="h_null_customer_transform",
                root_cause_code="TRANSFORMATION_REQUIRED_FIELD_NULL",
            ),
        ),
    )
    _close(
        kernel,
        gap_id="g_error",
        gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        tool_name="get_dbt_node_error",
        arguments={"run_id": RUN_ID, "node_id": FAILED_TEST_NODE},
        record=evidence.node_error,
        hypothesis_ids=("h_null_customer_source", "h_null_customer_transform"),
    )
    _close(
        kernel,
        gap_id="g_lineage",
        gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        tool_name="get_dbt_lineage",
        arguments={"node_id": FAILED_TEST_NODE, "direction": "upstream"},
        record=evidence.lineage,
        hypothesis_ids=("h_null_customer_source", "h_null_customer_transform"),
    )
    _close(
        kernel,
        gap_id="g_profile",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
        record=evidence.profile,
        hypothesis_ids=("h_null_customer_source", "h_null_customer_transform"),
    )
    _close(
        kernel,
        gap_id="g_schema",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_orders"},
        record=evidence.schema,
        hypothesis_ids=("h_null_customer_source", "h_null_customer_transform"),
    )
    return kernel, evidence


def _null_decision(
    evidence: _NullEvidence,
    *,
    root_citations: tuple[EvidenceRecord, ...],
) -> KernelDecision:
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id="h_null_customer_source",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_null_customer_source",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(evidence.profile.evidence_id,),
            ),
            HypothesisAssessment(
                hypothesis_id="h_null_customer_transform",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(evidence.node_error.evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_REQUIRED_FIELD_NULL",
                relation_name="raw_orders",
                evidence_ids=tuple(record.evidence_id for record in root_citations),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.orders",
                evidence_ids=(evidence.node_error.evidence_id, evidence.lineage.evidence_id),
            ),
        ),
        summary="Synthetic null-customer confirmation over public evidence.",
        recommended_actions=(),
        confidence=0.9,
    )


# ------------------------------------------------- semantic-duplicate seq66 shape


def _duplicate_kernel(codes: tuple[str, ...]) -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=codes,
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        observable_profile_relations=("raw_payments",),
        incident_subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
        lineage_node_candidates=("seed.jaffle_shop.raw_payments",),
    )


class _DuplicateEvidence:
    def __init__(self, *, id_duplicates: int, run_status: str = "SUCCEEDED") -> None:
        self.run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status=run_status,  # type: ignore[arg-type]
                dbt_exit_code=0 if run_status == "SUCCEEDED" else 1,
                failed_nodes=(),
                skipped_nodes=(),
            ),
        )
        self.lineage = _record(
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
                ),
            ),
        )
        self.schema = _record(
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
                ),
            ),
        )
        self.profile = _record(
            EvidenceType.RELATION_DATA_PROFILE,
            EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            "raw_payments",
            RelationDataProfileFact(
                kind="RELATION_DATA_PROFILE",
                run_id=RUN_ID,
                relation_name="raw_payments",
                profile_spec_version="profile_spec.v1",
                profile_spec_sha256="b" * 64,
                snapshot=RelationProfileSnapshot(
                    relation_name="raw_payments",
                    row_count=116,
                    columns=(),
                    business_key_duplicates=(
                        DuplicateProfileFact(name="id", duplicate_count=id_duplicates),
                    ),
                    business_fingerprint_duplicates=(
                        DuplicateProfileFact(name="order_payment_amount", duplicate_count=3),
                    ),
                    groups=(
                        GroupProfileFact(
                            name="payment_method",
                            columns=("payment_method",),
                            values=(("coupon",),),
                            counts=(16,),
                        ),
                    ),
                ),
            ),
        )


def _duplicate_kernel_with_evidence(
    *,
    code: str,
    id_duplicates: int,
) -> tuple[DiagnosticKernel, _DuplicateEvidence]:
    evidence = _DuplicateEvidence(id_duplicates=id_duplicates)
    kernel = _duplicate_kernel((code, "LEGITIMATE_SPLIT_PAYMENT"))
    _close(
        kernel,
        gap_id="g_run",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=evidence.run,
        new_hypotheses=(
            Hypothesis(hypothesis_id="h_dup", root_cause_code=code),
            Hypothesis(
                hypothesis_id="h_legit_split",
                root_cause_code="LEGITIMATE_SPLIT_PAYMENT",
            ),
        ),
    )
    for gap_id, gap_kind, tool, arguments, record in (
        (
            "g_schema",
            EvidenceGapKind.DISCRIMINATE_SCHEMA,
            "get_relation_schema",
            {"relation_name": "raw_payments"},
            evidence.schema,
        ),
        (
            "g_profile",
            EvidenceGapKind.PROFILE_RELATION,
            "get_relation_data_profile",
            {"relation_name": "raw_payments"},
            evidence.profile,
        ),
        (
            "g_lineage",
            EvidenceGapKind.MAP_IMPACT,
            "get_dbt_lineage",
            {"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
            evidence.lineage,
        ),
    ):
        _close(
            kernel,
            gap_id=gap_id,
            gap_kind=gap_kind,
            tool_name=tool,
            arguments=arguments,
            record=record,
            hypothesis_ids=("h_dup", "h_legit_split"),
        )
    return kernel, evidence


def _duplicate_decision(
    evidence: _DuplicateEvidence,
    *,
    code: str,
    root_citations: tuple[EvidenceRecord, ...],
) -> KernelDecision:
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id="h_dup",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_dup",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(evidence.profile.evidence_id,),
            ),
            HypothesisAssessment(
                hypothesis_id="h_legit_split",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(evidence.profile.evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value=code,
                relation_name="raw_payments",
                evidence_ids=tuple(record.evidence_id for record in root_citations),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.stg_payments",
                evidence_ids=(evidence.lineage.evidence_id,),
            ),
        ),
        summary="Synthetic duplicate confirmation over public evidence.",
        recommended_actions=(),
        confidence=0.9,
    )


def _finalize_ok(kernel: DiagnosticKernel, decision: KernelDecision) -> None:
    kernel.finalize(decision)


def _finalize_rejects(kernel: DiagnosticKernel, decision: KernelDecision) -> str:
    with pytest.raises(KernelError) as error:
        kernel.finalize(decision)
    return error.value.code


# ------------------------------------------------------------------- the shapes


def test_general_branch_accepts_node_error_with_upstream_relation_fact() -> None:
    for citations in ("node_error+profile", "node_error+schema"):
        kernel, evidence = _null_kernel_with_evidence()
        picked = (
            (evidence.node_error, evidence.profile)
            if citations == "node_error+profile"
            else (evidence.node_error, evidence.schema)
        )
        _finalize_ok(kernel, _null_decision(evidence, root_citations=picked))


def test_general_branch_rejects_profile_schema_only() -> None:
    """The seq62 shape: the failure node's error is missing from ROOT_CAUSE."""

    kernel, evidence = _null_kernel_with_evidence()
    code = _finalize_rejects(
        kernel,
        _null_decision(evidence, root_citations=(evidence.profile, evidence.schema)),
    )
    assert code == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_general_branch_rejects_lineage_without_node_error() -> None:
    """Held lineage does not stand in for the node error in ROOT_CAUSE."""

    kernel, evidence = _null_kernel_with_evidence()
    code = _finalize_rejects(
        kernel,
        _null_decision(
            evidence,
            root_citations=(evidence.run, evidence.profile, evidence.lineage),
        ),
    )
    assert code == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_semantic_duplicate_accepts_run_results_with_profile() -> None:
    kernel, evidence = _duplicate_kernel_with_evidence(
        code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        id_duplicates=0,
    )
    _finalize_ok(
        kernel,
        _duplicate_decision(
            evidence,
            code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            root_citations=(evidence.run, evidence.profile),
        ),
    )


def test_semantic_duplicate_rejects_profile_only() -> None:
    """The seq66 shape: run results are missing from ROOT_CAUSE."""

    kernel, evidence = _duplicate_kernel_with_evidence(
        code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        id_duplicates=0,
    )
    code = _finalize_rejects(
        kernel,
        _duplicate_decision(
            evidence,
            code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            root_citations=(evidence.profile,),
        ),
    )
    assert code == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_exact_duplicate_needs_the_general_check_as_well() -> None:
    """Exact duplicate satisfies its own domain first (id duplicates positive),
    then still needs the general check, so run+profile alone is not enough.

    The domain premise is asserted here so a later fixture change cannot make
    this negative pass for the wrong reason.
    """

    from data_incident_gym.diagnostic_payment_rules import duplicate_root_supported

    kernel, evidence = _duplicate_kernel_with_evidence(
        code="SOURCE_EXACT_PAYMENT_DUPLICATE",
        id_duplicates=2,
    )
    cited = [evidence.run, evidence.profile]
    assert duplicate_root_supported(
        "SOURCE_EXACT_PAYMENT_DUPLICATE", cited, {"raw_payments"}
    ), "domain condition must hold, or the rejection below would be misattributed"
    # The same records do not satisfy the semantic branch, so the two branches
    # cannot share fixture data.
    assert not duplicate_root_supported(
        "SOURCE_SEMANTIC_PAYMENT_DUPLICATE", cited, {"raw_payments"}
    )
    code = _finalize_rejects(
        kernel,
        _duplicate_decision(
            evidence,
            code="SOURCE_EXACT_PAYMENT_DUPLICATE",
            root_citations=(evidence.run, evidence.profile),
        ),
    )
    assert code == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_semantic_duplicate_rejects_node_error_without_run_results() -> None:
    """Guards against writing a universal 'every ROOT_CAUSE must cite the node
    error' rule: here the node error is irrelevant and run results are required.
    """

    kernel, evidence = _duplicate_kernel_with_evidence(
        code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        id_duplicates=0,
    )
    code = _finalize_rejects(
        kernel,
        _duplicate_decision(
            evidence,
            code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            root_citations=(evidence.schema, evidence.profile),
        ),
    )
    assert code == "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"


# ------------------------------------------------------- prompt contract checks


def test_kernel_prompt_states_per_claim_citation_duties() -> None:
    """Few, load-bearing checks: claims cite their own records, the branches stay
    distinguishable, and the independent payment branches are excluded from the
    general failed-node requirement."""

    from data_incident_gym.diagnostic_agent import KERNEL_PROMPT

    assert "Each claim carries its own citation duty" in KERNEL_PROMPT
    assert "does not satisfy the claim that needs it" in KERNEL_PROMPT
    # Duplicate branch: both codes named, and their different duties kept apart.
    assert "SOURCE_SEMANTIC_PAYMENT_DUPLICATE needs exactly one run-results record" in (
        KERNEL_PROMPT
    )
    assert "SOURCE_EXACT_PAYMENT_DUPLICATE needs those same two records" in KERNEL_PROMPT
    # General branch: scoped by exception, not stated as a universal rule.
    assert "Every other root cause except SOURCE_PAYMENT_INGESTION_LOSS and" in KERNEL_PROMPT
    assert "SOURCE_PERMANENT_ORPHAN_PAYMENT needs the failed node's error" in KERNEL_PROMPT
    # The two independent branches are pointed back at their own sections.
    assert "follow the citation\n  requirements stated in their own sections below" in (
        KERNEL_PROMPT
    )
    assert "a failed-node error is neither required nor available for\n  them" in KERNEL_PROMPT
    # No universal wording, and no private or case-specific content.
    assert "every other root cause needs a failed" not in KERNEL_PROMPT
    assert "every ROOT_CAUSE must cite" not in KERNEL_PROMPT
    assert "all ROOT_CAUSE claims must cite" not in KERNEL_PROMPT
    assert "expected_status" not in KERNEL_PROMPT
    assert "ground_truth" not in KERNEL_PROMPT


def test_two_independent_payment_branches_confirm_without_a_node_error() -> None:
    """Both independent branches confirm a decision whose evidence contains no
    node error at all, which is what the prompt exception states.

    The real pass/fail evidence for these branches lives with their own fixtures
    in ``test_diagnostic_kernel.py``; this check only pins the premise the prompt
    relies on: the confirming run is SUCCEEDED with no failed nodes, and the
    whole evidence set contains no node-error record to cite.
    """

    kernel, evidence = _duplicate_kernel_with_evidence(
        code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        id_duplicates=0,
    )
    run = evidence.run.content
    assert run.run_status == "SUCCEEDED"
    assert run.dbt_exit_code == 0
    assert run.failed_nodes == ()
    assert run.skipped_nodes == ()
    # A failed-node error cannot be cited here because none exists in the run.
    held_kinds = {record.content.kind for record in (evidence.run, evidence.profile)}
    assert "DBT_NODE_ERROR" not in held_kinds
    assert kernel is not None


def test_kernel_prompt_requires_a_receipt_before_declaring() -> None:
    """Receipt self-check and the (narrow) probe prohibitions.

    The existing contract allows a probe after a rejected decision while the tool
    budget lasts, so the prompt must not forbid that; it forbids only probing
    once the budget is gone, repeating a rejected call, and probing variants.
    """

    from data_incident_gym.diagnostic_agent import KERNEL_PROMPT

    assert "verify that a receipt for that" in KERNEL_PROMPT
    assert "do not declare that item at all" in KERNEL_PROMPT
    # A rejected decision does not bar a later, budgeted probe.
    assert "A\nrejected decision does not by itself bar a later probe" in KERNEL_PROMPT
    assert "take the one permitted boundary probe in a following" in KERNEL_PROMPT
    # The actual prohibitions.
    assert "probing once the tool\nbudget is exhausted" in KERNEL_PROMPT
    assert "repeating a call whose relation was already rejected" in KERNEL_PROMPT
    assert "probing a\nvariant of a blocked relation" in KERNEL_PROMPT
    # The over-broad wording the audit flagged must stay gone.
    assert "never probe after a rejection" not in KERNEL_PROMPT


def test_root_claim_retry_message_points_at_the_missing_category() -> None:
    from data_incident_gym.diagnostic_agent import _kernel_retry_message

    message = _kernel_retry_message("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE")

    assert "missing a category of record" in message
    assert "do not count for it" in message
    assert "already in the accepted-evidence list" in message
    # No ids, no private fields.
    import re

    assert re.search(r"ev_[0-9a-f]{64}", message) is None
    assert "expected_status" not in message
    assert "ground_truth" not in message


def test_kernel_prompt_requires_two_hypotheses_for_both_terminal_statuses() -> None:
    """The kernel rejects a one-hypothesis abstention exactly as it rejects a
    one-hypothesis confirmation (``test_kernel_requires_two_hypotheses_for_
    insufficient_evidence_too``), so the prompt must not scope the requirement to
    the confirmed path alone. v11 seq59 registered one hypothesis and spent the
    tool budget; the offline replay proved no later registration path exists, so
    the prompt also has to say that declarations ride on business calls and stop
    once the budget is gone."""

    from data_incident_gym.diagnostic_agent import KERNEL_PROMPT

    assert "for CONFIRMED and for INSUFFICIENT_EVIDENCE alike" in KERNEL_PROMPT
    assert "register\nboth competing causes before you run out of the tool budget" in (
        KERNEL_PROMPT
    )
    assert "registered\nonly by a business call that carries them" in KERNEL_PROMPT
    assert "once the tool-call budget is spent no declaration can" in KERNEL_PROMPT
    # The narrower wording the v11 report flagged must stay gone.
    assert "register at least two compatible hypotheses\nbefore attempting a confirmed" not in (
        KERNEL_PROMPT
    )


def test_a_failed_model_never_appears_in_its_own_lineage_neighbours() -> None:
    """The prompt tells the model to keep the failed model even though its
    lineage record only lists other nodes; that is only true because the record
    does not list the node itself."""

    record = _record(
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
                    node_id="model.jaffle_shop.stg_customers",
                    name="stg_customers",
                    resource_type="model",
                    distance=1,
                ),
            ),
        ),
    )

    content = record.content
    assert content.node_id == "model.jaffle_shop.customers"
    assert content.node_id not in {node.node_id for node in content.related_nodes}


def test_a_source_downstream_relation_reaches_the_downstream_models() -> None:
    """The no-failed-node branch reads the confirmed source or seed node's
    accepted downstream lineage; this pins that such a record names the models
    the rule expects, and that a comparison relation is a separate subject."""

    record = _record(
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
                    name="stg_payments",
                    resource_type="model",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.orders",
                    name="orders",
                    resource_type="model",
                    distance=2,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.customers",
                    name="customers",
                    resource_type="model",
                    distance=2,
                ),
            ),
        ),
    )

    nodes = record.content.related_nodes
    assert [node.name for node in nodes] == ["stg_payments", "orders", "customers"]
    assert all(node.resource_type == "model" for node in nodes)
    # The comparison relation of the same incident is not in this lineage.
    assert "raw_orders" not in {node.name for node in nodes}
