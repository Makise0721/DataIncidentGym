"""Domain-specific payment root-cause rules extracted from the kernel.

Each rule re-checks the evidence the model cited for a root-cause claim and
answers whether that evidence supports the claim. Rules are read-only: they
return booleans and never mutate inputs or kernel state.
"""

from __future__ import annotations

from datetime import datetime

from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtRunResultsFact,
    EvidenceRecord,
    RelationDataProfileFact,
    RelationHistoryFact,
)
from data_incident_gym.profiles import parse_watermark_value


def _duplicate_count(
    profile: RelationDataProfileFact,
    collection: str,
    name: str,
) -> int | None:
    facts = getattr(profile.snapshot, collection)
    fact = next((item for item in facts if item.name == name), None)
    return None if fact is None else fact.duplicate_count


def duplicate_root_supported(
    root_cause_code: str,
    records: list[EvidenceRecord],
    incident_subjects: set[str],
) -> bool:
    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name in incident_subjects
    ]
    if len(runs) != 1 or len(profiles) != 1:
        return False
    profile = profiles[0]
    key_count = _duplicate_count(profile, "business_key_duplicates", "id")
    fingerprint_count = _duplicate_count(
        profile,
        "business_fingerprint_duplicates",
        "order_payment_amount",
    )
    payment_method_group = next(
        (item for item in profile.snapshot.groups if item.name == "payment_method"),
        None,
    )
    if root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE":
        return (
            key_count is not None
            and key_count > 0
            and fingerprint_count is not None
            and payment_method_group is not None
        )
    if root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE":
        return (
            runs[0].run_status == "SUCCEEDED"
            and not runs[0].failed_nodes
            and key_count == 0
            and fingerprint_count is not None
            and fingerprint_count > 0
            and payment_method_group is not None
        )
    return False


def orphan_root_supported(
    records: list[EvidenceRecord],
    incident_subjects: set[str],
) -> bool:
    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    if (
        len(runs) != 1
        or runs[0].run_status != "SUCCEEDED"
        or runs[0].dbt_exit_code != 0
        or runs[0].failed_nodes
        or runs[0].skipped_nodes
    ):
        return False

    profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name in incident_subjects
        and any(
            item.name == "order_id_to_raw_orders_id"
            and item.violation_count > 0
            for item in record.content.snapshot.relationship_violations
        )
    ]
    if len(profiles) != 1:
        return False

    histories = [
        record.content
        for record in records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name in incident_subjects
        and record.content.relation_name != profiles[0].relation_name
    ]
    if len(histories) != 1:
        return False
    series = next(
        (
            item
            for item in histories[0].snapshot.histories
            if item.name == "order_count_by_day"
        ),
        None,
    )
    if (
        series is None
        or not series.points
        or series.watermark_column != "order_date"
        or series.watermark_value is None
    ):
        return False
    try:
        datetime.fromisoformat(series.watermark_value)
    except ValueError:
        return False
    return True


def _public_observation(
    observations: tuple[tuple[str, str, str], ...],
    kind: str,
) -> tuple[str, str] | None:
    matches = tuple(
        (subject, value)
        for observation_kind, subject, value in observations
        if observation_kind == kind
    )
    return matches[0] if len(matches) == 1 else None


def silent_drop_root_supported(
    root_cause_code: str,
    records: list[EvidenceRecord],
    incident_subjects: set[str],
    observations: tuple[tuple[str, str, str], ...],
    supporting_records: tuple[EvidenceRecord, ...] = (),
) -> bool:
    if root_cause_code != "SOURCE_PAYMENT_INGESTION_LOSS":
        return False
    current = _public_observation(observations, "CURRENT_PERIOD_COUNT")
    expected = _public_observation(observations, "EXPECTED_PERIOD_COUNT")
    relation_count = _public_observation(observations, "CURRENT_RELATION_COUNT")
    settled = _public_observation(observations, "SETTLED_PAYMENT_WINDOW_END")
    if current is None or expected is None or relation_count is None or settled is None:
        return False
    current_subject, current_raw = current
    expected_subject, expected_raw = expected
    payment_relation, payment_history, bucket = current_subject.split("/") if (
        current_subject.count("/") == 2
    ) else ("", "", "")
    count_relation, relation_count_raw = relation_count
    order_relation, settled_value = settled
    if (
        expected_subject != current_subject
        or payment_relation not in incident_subjects
        or order_relation not in incident_subjects
        or count_relation != payment_relation
        or payment_history != "payment_count_by_order_date"
        or bucket != settled_value
    ):
        return False
    try:
        current_count = int(current_raw)
        expected_count = int(expected_raw)
        current_relation_count = int(relation_count_raw)
        parse_watermark_value(bucket)
        settled_at = parse_watermark_value(settled_value)
    except (TypeError, ValueError):
        return False
    if (
        current_count < 0
        or expected_count <= current_count
        or current_relation_count < 0
    ):
        return False

    runs = [
        record.content
        for record in records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    payment_profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name == payment_relation
    ]
    order_profiles = [
        record.content
        for record in records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name == order_relation
    ]
    if len(runs) != 1 or len(payment_profiles) != 1 or len(order_profiles) != 1:
        return False
    run = runs[0]
    payment_profile = payment_profiles[0]
    order_profile = order_profiles[0]
    if (
        run.run_status != "SUCCEEDED"
        or run.dbt_exit_code != 0
        or run.failed_nodes
        or run.skipped_nodes
        or payment_profile.snapshot.relation_name != payment_relation
        or order_profile.snapshot.relation_name != order_relation
        or payment_profile.snapshot.row_count != current_relation_count
    ):
        return False
    reverse_relationship = next(
        (
            item
            for item in order_profile.snapshot.relationship_violations
            if item.name == "id_to_raw_payments_order_id"
        ),
        None,
    )
    if (
        reverse_relationship is None
        or reverse_relationship.violation_count != expected_count - current_count
    ):
        return False

    payment_histories = [
        record.content
        for record in records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name == payment_relation
    ]
    order_histories = [
        record.content
        for record in records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name == order_relation
    ]
    if len(payment_histories) != 1 or len(order_histories) != 1:
        return False
    payment_series = tuple(
        item
        for item in payment_histories[0].snapshot.histories
        if item.name == payment_history
    )
    order_series = tuple(
        item
        for item in order_histories[0].snapshot.histories
        if item.name == "order_count_by_day"
    )
    if len(payment_series) != 1 or len(order_series) != 1:
        return False
    payment_point = tuple(
        point for point in payment_series[0].points if point.bucket == bucket
    )
    order_history = order_series[0]
    if (
        len(payment_point) != 1
        or payment_point[0].value != current_count
        or order_history.watermark_column != "order_date"
        or order_history.watermark_value is None
    ):
        return False
    try:
        watermark = parse_watermark_value(order_history.watermark_value)
    except (TypeError, ValueError):
        return False
    if watermark < settled_at:
        return False

    lineage_records = (*records, *supporting_records)
    return any(
        isinstance(record.content, DbtLineageFact)
        and record.content.direction == "downstream"
        and record.content.node_id in incident_subjects
        and any(
            node.resource_type == "model" and node.distance >= 1
            for node in record.content.related_nodes
        )
        for record in lineage_records
    )


__all__ = [
    "duplicate_root_supported",
    "orphan_root_supported",
    "silent_drop_root_supported",
]
