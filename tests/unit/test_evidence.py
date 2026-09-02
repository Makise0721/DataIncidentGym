from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationHistoryFact,
)
from data_incident_gym.profiles import (
    ColumnProfileFact,
    HistorySeries,
    ProfileSnapshot,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
    load_profile_spec,
)

RUN_ID = "a" * 32
OBSERVED_AT = datetime(2026, 8, 30, tzinfo=UTC)


def _run_record() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=OBSERVED_AT,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="SUCCEEDED",
            dbt_exit_code=0,
            failed_nodes=(),
            skipped_nodes=(),
        ),
    )


def _profile_snapshot() -> ProfileSnapshot:
    spec = load_profile_spec()
    current = RelationProfileSnapshot(
        relation_name="raw_orders",
        row_count=99,
        columns=(ColumnProfileFact(column_name="id", null_count=0, distinct_count=99),),
    )
    history = RelationHistorySnapshot(
        relation_name="raw_orders",
        histories=(
            HistorySeries(
                name="order_count_by_day",
                metric="count",
                points=(),
            ),
        ),
    )
    return ProfileSnapshot.create(spec=spec, current=(current,), history=(history,))


def test_evidence_ids_are_stable_and_content_bound() -> None:
    first = _run_record()
    second = _run_record()
    assert first == second
    changed = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject="other-subject",
        observed_at=OBSERVED_AT,
        content=first.content,
    )
    assert changed.evidence_id != first.evidence_id


def test_profile_facts_use_the_profile_snapshot_source() -> None:
    snapshot = _profile_snapshot()
    spec = load_profile_spec()
    profile = RelationDataProfileFact(
        kind="RELATION_DATA_PROFILE",
        run_id=RUN_ID,
        relation_name="raw_orders",
        profile_spec_version=spec.schema_version,
        profile_spec_sha256=spec.digest(),
        snapshot=snapshot.current[0],
    )
    history = RelationHistoryFact(
        kind="RELATION_HISTORY",
        run_id=RUN_ID,
        relation_name="raw_orders",
        profile_spec_version=spec.schema_version,
        profile_spec_sha256=spec.digest(),
        snapshot=snapshot.history[0],
    )
    profile_record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_DATA_PROFILE,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject="raw_orders",
        observed_at=OBSERVED_AT,
        content=profile,
    )
    history_record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_HISTORY,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject="raw_orders",
        observed_at=OBSERVED_AT,
        content=history,
    )

    assert profile_record.evidence_type is EvidenceType.RELATION_DATA_PROFILE
    assert history_record.source is EvidenceSource.POSTGRES_PROFILE_SNAPSHOT


def test_evidence_record_is_strict_and_frozen() -> None:
    record = _run_record()
    with pytest.raises(ValidationError):
        record.subject = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        EvidenceRecord.model_validate({**record.model_dump(), "extra": "nope"})


def test_all_six_evidence_types_are_registered() -> None:
    assert tuple(item.value for item in EvidenceType) == (
        "DBT_RUN_RESULTS",
        "DBT_NODE_ERROR",
        "RELATION_SCHEMA",
        "DBT_LINEAGE",
        "RELATION_DATA_PROFILE",
        "RELATION_HISTORY",
    )
