"""Direct unit coverage for the extracted stateless kernel validators."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnostic_contracts import KernelError
from data_incident_gym.diagnostic_validation import (
    ValidationContext,
    validate_root_cause_evidence,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationSchemaColumn,
    RelationSchemaFact,
)

RUN_ID = "a" * 32
MODEL_ID = "model.jaffle_shop.customers"
UPSTREAM_ORDER_RELATIONS = ("raw_orders", "raw_customers", "raw_payments")


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


def _node_error() -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_NODE_ERROR,
        EvidenceSource.DBT_RUN_RESULTS,
        MODEL_ID,
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=MODEL_ID,
            resource_type="model",
            status="error",
            message="cannot cast text to integer",
        ),
    )


def _upstream_lineage(*relations: str) -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        MODEL_ID,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=MODEL_ID,
            direction="upstream",
            related_nodes=tuple(
                DbtLineageNode(
                    node_id=f"seed.jaffle_shop.{relation}",
                    resource_type="seed",
                    name=relation,
                    distance=2,
                )
                for relation in relations
            ),
        ),
    )


def _schema(relation_name: str) -> EvidenceRecord:
    return _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        relation_name,
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name=relation_name,
            columns=(
                RelationSchemaColumn(
                    name="user_id",
                    data_type="text",
                    nullable=True,
                    ordinal_position=1,
                ),
            ),
        ),
    )


def _validate(
    root_records: tuple[EvidenceRecord, ...],
    *,
    relation_name: str | None,
    inventory: tuple[EvidenceRecord, ...] | None = None,
    root_cause_code: str = "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
) -> None:
    context = ValidationContext(
        incident_subjects=frozenset({MODEL_ID}),
        health_target_subjects=frozenset(),
        incident_logical_observed_at=None,
        incident_observations=(),
        all_records=tuple(inventory if inventory is not None else root_records),
    )
    validate_root_cause_evidence(
        context,
        root_cause_code,
        list(root_records),
        relation_name=relation_name,
    )


@pytest.mark.parametrize(
    "root_cause_code",
    ("SOURCE_SCHEMA_COLUMN_TYPE_CHANGED", "SOURCE_SCHEMA_COLUMN_RENAMED"),
)
def test_schema_source_root_rejects_schema_of_another_relation(
    root_cause_code: str,
) -> None:
    node_error = _node_error()
    lineage = _upstream_lineage(*UPSTREAM_ORDER_RELATIONS)
    other_relation_schema = _schema("raw_customers")

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        _validate(
            (node_error, lineage, other_relation_schema),
            relation_name="raw_orders",
            root_cause_code=root_cause_code,
        )


def test_schema_source_root_rejects_target_schema_left_uncited() -> None:
    node_error = _node_error()
    lineage = _upstream_lineage(*UPSTREAM_ORDER_RELATIONS)
    target_schema = _schema("raw_orders")
    profile_of_another_upstream = _schema("raw_customers")

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        _validate(
            (node_error, lineage, profile_of_another_upstream),
            relation_name="raw_orders",
        )
    # The target schema exists in the run inventory but is not cited by the claim.
    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        _validate(
            (node_error, lineage, profile_of_another_upstream),
            relation_name="raw_orders",
            inventory=(node_error, lineage, profile_of_another_upstream, target_schema),
        )


def test_schema_source_root_rejects_target_relation_outside_upstream_path() -> None:
    node_error = _node_error()
    lineage = _upstream_lineage("raw_customers", "raw_payments")
    unrelated_schema = _schema("raw_orders")

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        _validate((node_error, lineage, unrelated_schema), relation_name="raw_orders")


def test_schema_source_root_rejects_missing_declared_relation_name() -> None:
    node_error = _node_error()
    lineage = _upstream_lineage(*UPSTREAM_ORDER_RELATIONS)
    target_schema = _schema("raw_orders")

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        _validate((node_error, lineage, target_schema), relation_name=None)


def test_schema_source_root_accepts_target_relation_schema_on_upstream_path() -> None:
    node_error = _node_error()
    lineage = _upstream_lineage(*UPSTREAM_ORDER_RELATIONS)
    target_schema = _schema("raw_orders")

    _validate((node_error, lineage, target_schema), relation_name="raw_orders")
