from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    InvalidArtifactError,
    InvalidDirectionError,
    InvalidRunIdError,
    NodeErrorNotFoundError,
    NodeNotFoundError,
    ReadOnlyDatabaseError,
    RelationNotAllowedError,
    RelationNotFoundError,
    RelationSchemaColumn,
    RelationSchemaFact,
    RunContextMismatchError,
    RunNotFoundError,
    RunStateDriftError,
    raise_without_context,
)

RUN_ID = "0123456789abcdef0123456789abcdef"


def run_results_fact(
    *,
    run_id: str = RUN_ID,
    skipped_nodes: tuple[str, ...] = (
        "model.jaffle_shop.customers",
        "model.jaffle_shop.orders",
    ),
) -> DbtRunResultsFact:
    return DbtRunResultsFact(
        kind="DBT_RUN_RESULTS",
        run_id=run_id,
        run_status="FAILED",
        dbt_exit_code=1,
        failed_nodes=("model.jaffle_shop.stg_payments",),
        skipped_nodes=skipped_nodes,
    )


def make_run_results_record(
    *,
    run_id: str = RUN_ID,
    observed_at: str = "2026-08-25T09:00:00Z",
    skipped_nodes: tuple[str, ...] = (
        "model.jaffle_shop.customers",
        "model.jaffle_shop.orders",
    ),
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=run_id,
        observed_at=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
        content=run_results_fact(run_id=run_id, skipped_nodes=skipped_nodes),
    )


def test_same_run_and_content_have_stable_evidence_id() -> None:
    first = make_run_results_record(observed_at="2026-08-25T09:00:00Z")
    second = make_run_results_record(observed_at="2026-08-25T09:01:00Z")

    assert first.evidence_id == second.evidence_id
    assert first.content_digest == second.content_digest
    assert first.observed_at != second.observed_at


def test_content_or_run_change_changes_evidence_id() -> None:
    original = make_run_results_record()
    changed_content = make_run_results_record(skipped_nodes=())
    changed_run = make_run_results_record(
        run_id="fedcba9876543210fedcba9876543210"
    )

    assert original.evidence_id != changed_content.evidence_id
    assert original.evidence_id != changed_run.evidence_id


def test_tampered_digest_and_type_content_pair_are_rejected() -> None:
    record = make_run_results_record()
    payload = record.model_dump(mode="json")
    payload["content_digest"] = "0" * 64

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_LINEAGE,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=RUN_ID,
            observed_at=datetime.now(UTC),
            content=run_results_fact(),
        )


def test_content_models_are_frozen_and_reject_extra_fields() -> None:
    fact = run_results_fact()

    with pytest.raises(ValidationError):
        DbtRunResultsFact.model_validate(
            {**fact.model_dump(), "unexpected": "TEST_REDACTED_VALUE"}
        )

    with pytest.raises(ValidationError):
        fact.run_status = "SUCCESS"

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {**make_run_results_record().model_dump(mode="json"), "unexpected": 1}
        )


def test_record_rejects_naive_and_invalid_run_id() -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_RUN_RESULTS,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=RUN_ID,
            observed_at=datetime(2026, 8, 25, 9, 0),
            content=run_results_fact(),
        )

    with pytest.raises(ValidationError):
        make_run_results_record(run_id="../../outside")


def test_all_fact_variants_are_frozen_and_have_exact_discriminators() -> None:
    node_error = DbtNodeErrorFact(
        kind="DBT_NODE_ERROR",
        run_id=RUN_ID,
        node_id="model.jaffle_shop.stg_payments",
        resource_type="model",
        status="error",
        message='column "amount" does not exist',
    )
    schema = RelationSchemaFact(
        kind="RELATION_SCHEMA",
        run_id=RUN_ID,
        schema_name="analytics",
        relation_name="raw_payments",
        columns=(
            RelationSchemaColumn(
                name="total_amount",
                data_type="integer",
                nullable=True,
                ordinal_position=4,
            ),
        ),
    )
    lineage = DbtLineageFact(
        kind="DBT_LINEAGE",
        run_id=RUN_ID,
        node_id="model.jaffle_shop.stg_payments",
        direction="downstream",
        related_nodes=(),
    )

    assert node_error.kind == "DBT_NODE_ERROR"
    assert schema.kind == "RELATION_SCHEMA"
    assert lineage.kind == "DBT_LINEAGE"

    with pytest.raises(ValidationError):
        schema.columns[0].name = "TEST_REDACTED_VALUE"


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (InvalidRunIdError, "INVALID_RUN_ID"),
        (RunNotFoundError, "RUN_NOT_FOUND"),
        (RunContextMismatchError, "RUN_CONTEXT_MISMATCH"),
        (InvalidArtifactError, "INVALID_ARTIFACT"),
        (NodeNotFoundError, "NODE_NOT_FOUND"),
        (NodeErrorNotFoundError, "NODE_ERROR_NOT_FOUND"),
        (InvalidDirectionError, "INVALID_DIRECTION"),
        (RelationNotAllowedError, "RELATION_NOT_ALLOWED"),
        (RelationNotFoundError, "RELATION_NOT_FOUND"),
        (RunStateDriftError, "RUN_STATE_DRIFT"),
        (ReadOnlyDatabaseError, "READ_ONLY_DATABASE_ERROR"),
    ],
)
def test_error_subclasses_expose_stable_codes_and_redact_details(
    error_type: type[Exception], code: str
) -> None:
    error = error_type(
        "password=TEST_REDACTED_VALUE SELECT * FROM TEST_REDACTED_VALUE "
        "C:\\TEST_REDACTED_VALUE\\artifact.json"
    )

    assert error.code == code
    assert "TEST_REDACTED_VALUE" not in str(error)
    assert "SELECT" not in str(error)
    assert "artifact.json" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_raise_without_context_removes_original_exception() -> None:
    with pytest.raises(InvalidArtifactError) as captured:
        try:
            raise ValueError("TEST_REDACTED_VALUE")
        except ValueError:
            raise_without_context(InvalidArtifactError("invalid artifact"))

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
