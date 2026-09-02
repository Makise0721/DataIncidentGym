from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    DiagnosisRunResult,
    DiagnosisStatus,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_kernel import (
    EvidenceGapStatus,
    HypothesisVerdict,
    KernelStateTraceEvent,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaFact,
)
from data_incident_gym.lab_verifier import ScenarioVerification
from data_incident_gym.profiles import parse_watermark_value
from data_incident_gym.scenarios import (
    Answerability,
    ColumnRenameMutation,
    ColumnTypeMutation,
    DeletePaymentRowsMutation,
    DuplicatePaymentRowsMutation,
    OrphanPaymentRowsMutation,
    ScenarioSpec,
    SetFieldNullMutation,
    deleted_payment_rows,
)

EVALUATOR_VERSION = "p1.evaluator.v2"


class EvaluationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class EvaluationApplicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvaluationCheckCode(StrEnum):
    ENVIRONMENT_VERIFIED = "ENVIRONMENT_VERIFIED"
    STATUS_EXACT = "STATUS_EXACT"
    ROOT_CAUSE_ACCEPTED = "ROOT_CAUSE_ACCEPTED"
    AFFECTED_ASSETS_EXACT = "AFFECTED_ASSETS_EXACT"
    EVIDENCE_IDS_EXIST = "EVIDENCE_IDS_EXIST"
    EVIDENCE_RUN_SCOPE = "EVIDENCE_RUN_SCOPE"
    REQUIRED_EVIDENCE_TYPES_PRESENT = "REQUIRED_EVIDENCE_TYPES_PRESENT"
    CLAIM_EVIDENCE_COMPATIBLE = "CLAIM_EVIDENCE_COMPATIBLE"
    INSUFFICIENCY_GAP_DECLARED = "INSUFFICIENCY_GAP_DECLARED"
    POSITIVE_HEALTH_EVIDENCE = "POSITIVE_HEALTH_EVIDENCE"
    TOOL_ALLOWLIST_EXACT = "TOOL_ALLOWLIST_EXACT"
    TRACE_READ_ONLY_SAFE = "TRACE_READ_ONLY_SAFE"
    RECOVERY_HEALTHY = "RECOVERY_HEALTHY"


class ControllerCheckCode(StrEnum):
    KERNEL_STATE_VALID = "KERNEL_STATE_VALID"
    KERNEL_HYPOTHESIS_GATE = "KERNEL_HYPOTHESIS_GATE"
    KERNEL_EVIDENCE_GAP_GATE = "KERNEL_EVIDENCE_GAP_GATE"


class EvaluationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: EvaluationCheckCode
    applicability: EvaluationApplicability
    passed: StrictBool
    expected: tuple[StrictStr, ...]
    actual: tuple[StrictStr, ...]
    reason_code: StrictStr

    @model_validator(mode="after")
    def validate_applicability(self) -> Self:
        if self.applicability is EvaluationApplicability.NOT_APPLICABLE:
            if self.passed is not True:
                raise ValueError("not-applicable checks must pass")
            if self.expected != ("NOT_APPLICABLE",) or self.actual != ("NOT_APPLICABLE",):
                raise ValueError("not-applicable checks must use fixed values")
            if self.reason_code != "NOT_APPLICABLE":
                raise ValueError("not-applicable checks must use fixed reason")
        elif self.reason_code != (
            f"{self.code.value}_{'PASSED' if self.passed else 'FAILED'}"
        ):
            raise ValueError("applicable reason_code must match result")
        return self


class ControllerCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ControllerCheckCode
    passed: StrictBool
    expected: tuple[StrictStr, ...]
    actual: tuple[StrictStr, ...]
    reason_code: StrictStr

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        expected = f"{self.code.value}_{'PASSED' if self.passed else 'FAILED'}"
        if self.reason_code != expected:
            raise ValueError("controller reason_code must match result")
        return self


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.evaluation.v1"] = "p1.evaluation.v1"
    incident_case_id: StrictStr
    run_id: StrictStr
    status: EvaluationStatus
    checks: tuple[EvaluationCheck, ...]
    failed_check_codes: tuple[EvaluationCheckCode, ...]
    controller_checks: tuple[ControllerCheck, ...] = ()
    variant_role: StrictStr | None = None
    answerability: StrictStr
    expected_status: StrictStr

    @model_validator(mode="after")
    def validate_complete_check_set(self) -> Self:
        expected = tuple(EvaluationCheckCode)
        actual = tuple(check.code for check in self.checks)
        if actual != expected:
            raise ValueError("checks must contain every code exactly once in canonical order")
        failed = tuple(
            check.code
            for check in self.checks
            if check.applicability is EvaluationApplicability.APPLICABLE and not check.passed
        )
        if self.failed_check_codes != failed:
            raise ValueError("failed_check_codes must match applicable checks")
        expected_status = EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED
        if self.status is not expected_status:
            raise ValueError("status must match checks")
        return self


ALLOWED_DIAGNOSTIC_TOOLS = frozenset(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_data_profile",
        "get_relation_history",
    }
)
TRACE_FORBIDDEN_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]"
    r"|\bbearer\s+"
    r"|\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b"
    r"|(?:[a-z]:[\\/]|\\\\|/)[^\s]*"
)


def _check(
    code: EvaluationCheckCode,
    passed: bool,
    expected: tuple[str, ...],
    actual: tuple[str, ...],
    *,
    applicable: bool = True,
) -> EvaluationCheck:
    if not applicable:
        return EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.NOT_APPLICABLE,
            passed=True,
            expected=("NOT_APPLICABLE",),
            actual=("NOT_APPLICABLE",),
            reason_code="NOT_APPLICABLE",
        )
    return EvaluationCheck(
        code=code,
        applicability=EvaluationApplicability.APPLICABLE,
        passed=passed,
        expected=expected,
        actual=actual,
        reason_code=f"{code.value}_{'PASSED' if passed else 'FAILED'}",
    )


def _record_ids(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    return tuple(record.evidence_id for record in records)


def _health_evidence_valid(scenario: ScenarioSpec, diagnosis_run: DiagnosisRunResult) -> bool:
    inventory = {record.evidence_id: record for record in diagnosis_run.evidence_records}
    diagnosis = diagnosis_run.diagnosis
    if diagnosis.status is not DiagnosisStatus.NO_INCIDENT:
        return False
    alert_subjects = {
        observation.subject
        for observation in scenario.incident_brief.observations
        if observation.kind == "CURRENT_PERIOD_COUNT"
    }
    if not alert_subjects:
        return False
    for claim in diagnosis.claims:
        if claim.kind != "HEALTH_STATE":
            return False
        if f"{claim.relation_name}/{claim.history_name}/{claim.bucket}" not in alert_subjects:
            return False
        records = [inventory.get(evidence_id) for evidence_id in claim.evidence_ids]
        if any(record is None for record in records):
            return False
        profile = next(
            (
                record.content
                for record in records
                if isinstance(record.content, RelationDataProfileFact)
                and record.content.relation_name == claim.relation_name
            ),
            None,
        )
        history = next(
            (
                record.content
                for record in records
                if isinstance(record.content, RelationHistoryFact)
                and record.content.relation_name == claim.relation_name
            ),
            None,
        )
        run = next(
            (
                record.content
                for record in records
                if isinstance(record.content, DbtRunResultsFact)
            ),
            None,
        )
        if profile is None or history is None or run is None:
            return False
        if (
            profile.snapshot.relation_name != claim.relation_name
            or history.snapshot.relation_name != claim.relation_name
        ):
            return False
        if (
            run.run_status != "SUCCEEDED"
            or run.dbt_exit_code != 0
            or run.failed_nodes
            or run.skipped_nodes
        ):
            return False
        history_series = next(
            (series for series in history.snapshot.histories if series.name == claim.history_name),
            None,
        )
        if history_series is None:
            return False
        current = next(
            (
                point
                for series in (history_series,)
                for point in series.points
                if point.bucket == claim.bucket
            ),
            None,
        )
        if current is None or current.value != claim.current_value:
            return False
        if (
            history_series.watermark_column != "order_date"
            or history_series.watermark_value is None
        ):
            return False
        is_current_partition = current.bucket == history_series.watermark_value
        try:
            watermark = parse_watermark_value(history_series.watermark_value)
        except (TypeError, ValueError):
            return False
        if is_current_partition:
            if history_series.sla_seconds is None:
                return False
            logical_observed_at = scenario.incident_brief.logical_observed_at
            if logical_observed_at.tzinfo is None or logical_observed_at.utcoffset() is None:
                return False
            lag = (
                logical_observed_at.astimezone(watermark.tzinfo) - watermark
            ).total_seconds()
            if lag < 0 or lag > history_series.sla_seconds:
                return False
        else:
            try:
                current_bucket = parse_watermark_value(current.bucket)
            except (TypeError, ValueError):
                return False
            if current_bucket > watermark:
                return False
        if not is_current_partition:
            for series in history.snapshot.histories:
                if series.name != claim.history_name:
                    continue
                prior = [
                    point.value
                    for point in series.points
                    if point.periodic_key == current.periodic_key and point.bucket < current.bucket
                ]
                return len(prior) >= 4 and min(prior) <= current.value <= max(prior)
        return True
    return bool(diagnosis.claims)


def _scenario_observation(
    scenario: ScenarioSpec,
    kind: str,
) -> tuple[str, str] | None:
    matches = tuple(
        (observation.subject, observation.value)
        for observation in scenario.incident_brief.observations
        if observation.kind == kind
    )
    return matches[0] if len(matches) == 1 else None


def _silent_drop_evidence_compatible(
    scenario: ScenarioSpec,
    mutation: DeletePaymentRowsMutation,
    root_records: list[EvidenceRecord],
    supporting_records: tuple[EvidenceRecord, ...],
) -> bool:
    current = _scenario_observation(scenario, "CURRENT_PERIOD_COUNT")
    expected = _scenario_observation(scenario, "EXPECTED_PERIOD_COUNT")
    relation_count = _scenario_observation(scenario, "CURRENT_RELATION_COUNT")
    settled = _scenario_observation(scenario, "SETTLED_PAYMENT_WINDOW_END")
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
    if expected_count <= current_count:
        return False
    deleted_rows = deleted_payment_rows(mutation)
    expected_channels = {
        "bank_transfer": 33,
        "coupon": 13,
        "credit_card": 55,
        "gift_card": 12,
    }
    for _, _, payment_method, _ in deleted_rows:
        expected_channels[payment_method] -= 1

    runs = [
        record.content
        for record in root_records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    payment_profiles = [
        record.content
        for record in root_records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name == payment_relation
    ]
    order_profiles = [
        record.content
        for record in root_records
        if isinstance(record.content, RelationDataProfileFact)
        and record.content.relation_name == order_relation
    ]
    payment_histories = [
        record.content
        for record in root_records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name == payment_relation
    ]
    order_histories = [
        record.content
        for record in root_records
        if isinstance(record.content, RelationHistoryFact)
        and record.content.relation_name == order_relation
    ]
    if (
        len(runs) != 1
        or len(payment_profiles) != 1
        or len(order_profiles) != 1
        or len(payment_histories) != 1
        or len(order_histories) != 1
    ):
        return False
    run = runs[0]
    payment_profile = payment_profiles[0]
    order_profile = order_profiles[0]
    if (
        run.run_status != "SUCCEEDED"
        or run.dbt_exit_code != 0
        or run.failed_nodes
        or run.skipped_nodes
        or payment_profile.snapshot.row_count != 113 - len(deleted_rows)
        or payment_profile.snapshot.row_count != current_relation_count
        or order_profile.snapshot.row_count != 99
    ):
        return False
    relationship = next(
        (
            item
            for item in order_profile.snapshot.relationship_violations
            if item.name == "id_to_raw_payments_order_id"
        ),
        None,
    )
    key = next(
        (item for item in payment_profile.snapshot.business_key_duplicates if item.name == "id"),
        None,
    )
    fingerprint = next(
        (
            item
            for item in payment_profile.snapshot.business_fingerprint_duplicates
            if item.name == "order_payment_amount"
        ),
        None,
    )
    group = next(
        (item for item in payment_profile.snapshot.groups if item.name == "payment_method"),
        None,
    )
    if relationship is None or relationship.violation_count != len(deleted_rows):
        return False
    if key is None or fingerprint is None or key.duplicate_count or fingerprint.duplicate_count:
        return False
    if group is None or group.columns != ("payment_method",):
        return False
    channel_counts = {
        values[0]: count
        for values, count in zip(group.values, group.counts, strict=True)
        if len(values) == 1
    }
    if channel_counts != expected_channels:
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
    payment_points = tuple(
        point for point in payment_series[0].points if point.bucket == bucket
    )
    order_history = order_series[0]
    if (
        len(payment_points) != 1
        or payment_points[0].value != current_count
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
    lineage_records = (*root_records, *supporting_records)
    return any(
        isinstance(record.content, DbtLineageFact)
        and record.content.direction == "downstream"
        and record.content.node_id in scenario.incident_brief.subjects
        and any(
            node.resource_type == "model" and node.distance >= 1
            for node in record.content.related_nodes
        )
        for record in lineage_records
    )


def _root_cause_evidence_compatible(
    scenario: ScenarioSpec,
    root_cause_code: str,
    root_records: list[EvidenceRecord],
    supporting_records: tuple[EvidenceRecord, ...] = (),
) -> bool:
    silent = next(
        (
            item
            for item in scenario.reset_and_injection_contract.mutations
            if isinstance(item, DeletePaymentRowsMutation)
        ),
        None,
    )
    if silent is not None:
        return (
            root_cause_code == "SOURCE_PAYMENT_INGESTION_LOSS"
            and _silent_drop_evidence_compatible(
                scenario,
                silent,
                root_records,
                supporting_records,
            )
        )
    duplicate = next(
        (
            item
            for item in scenario.reset_and_injection_contract.mutations
            if isinstance(item, DuplicatePaymentRowsMutation)
        ),
        None,
    )
    if duplicate is not None:
        run = next(
            (
                record.content
                for record in root_records
                if isinstance(record.content, DbtRunResultsFact)
            ),
            None,
        )
        profile = next(
            (
                record.content
                for record in root_records
                if isinstance(record.content, RelationDataProfileFact)
                and record.content.relation_name == duplicate.relation
            ),
            None,
        )
        if run is None or profile is None:
            return False
        key = next(
            (
                item
                for item in profile.snapshot.business_key_duplicates
                if item.name == "id"
            ),
            None,
        )
        fingerprint = next(
            (
                item
                for item in profile.snapshot.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            ),
            None,
        )
        payment_method_group = next(
            (
                item
                for item in profile.snapshot.groups
                if item.name == "payment_method"
            ),
            None,
        )
        if key is None or fingerprint is None or payment_method_group is None:
            return False
        grouped_counts = dict(
            zip(
                (values[0] for values in payment_method_group.values),
                payment_method_group.counts,
                strict=True,
            )
        )
        if duplicate.mode == "EXACT_RECORD":
            return (
                root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE"
                and key.duplicate_count == 1
                and fingerprint.duplicate_count == 1
                and grouped_counts.get("credit_card") == 56
                and any(
                    isinstance(record.content, DbtNodeErrorFact)
                    and record.content.node_id == scenario.direct_failure
                    for record in root_records
                )
            )
        return (
            root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
            and run.run_status == "SUCCEEDED"
            and not run.failed_nodes
            and key.duplicate_count == 0
            and fingerprint.duplicate_count == len(duplicate.inserted_payment_ids)
            and grouped_counts.get("coupon") == 16
        )

    orphan = next(
        (
            item
            for item in scenario.reset_and_injection_contract.mutations
            if isinstance(item, OrphanPaymentRowsMutation)
        ),
        None,
    )
    if orphan is not None:
        if root_cause_code != "SOURCE_PERMANENT_ORPHAN_PAYMENT":
            return False
        runs = [
            record.content
            for record in root_records
            if isinstance(record.content, DbtRunResultsFact)
        ]
        profiles = [
            record.content
            for record in root_records
            if isinstance(record.content, RelationDataProfileFact)
            and record.content.relation_name == orphan.relation
        ]
        histories = [
            record.content
            for record in root_records
            if isinstance(record.content, RelationHistoryFact)
            and record.content.relation_name == "raw_orders"
        ]
        if len(runs) != 1 or len(profiles) != 1 or len(histories) != 1:
            return False
        run = runs[0]
        profile = profiles[0]
        history = histories[0]
        if (
            run.run_status != "SUCCEEDED"
            or run.dbt_exit_code != 0
            or run.failed_nodes
            or run.skipped_nodes
            or profile.snapshot.relation_name != orphan.relation
            or history.snapshot.relation_name != "raw_orders"
            or profile.snapshot.row_count != 113 + len(orphan.inserted_payment_ids)
        ):
            return False
        key = next(
            (item for item in profile.snapshot.business_key_duplicates if item.name == "id"),
            None,
        )
        fingerprint = next(
            (
                item
                for item in profile.snapshot.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            ),
            None,
        )
        relationship = next(
            (
                item
                for item in profile.snapshot.relationship_violations
                if item.name == "order_id_to_raw_orders_id"
            ),
            None,
        )
        group = next(
            (item for item in profile.snapshot.groups if item.name == "payment_method"),
            None,
        )
        expected_channel, expected_channel_count = {
            "SINGLE_REFERENCE": ("credit_card", 56),
            "SETTLED_COUPON_WINDOW": ("coupon", 16),
        }[orphan.mode]
        channel_count = next(
            (
                count
                for values, count in zip(group.values, group.counts, strict=True)
                if values == (expected_channel,)
            ),
            None,
        ) if group is not None else None
        if (
            key is None
            or fingerprint is None
            or relationship is None
            or key.duplicate_count != 0
            or fingerprint.duplicate_count != 0
            or relationship.violation_count != len(orphan.inserted_payment_ids)
            or channel_count != expected_channel_count
        ):
            return False
        series = next(
            (
                item
                for item in history.snapshot.histories
                if item.name == "order_count_by_day"
            ),
            None,
        )
        settled_window = next(
            (
                observation.value
                for observation in scenario.incident_brief.observations
                if observation.kind == "SETTLED_ORDER_WINDOW_END"
                and observation.subject == "raw_orders"
            ),
            None,
        )
        if (
            series is None
            or not series.points
            or series.watermark_column != "order_date"
            or series.watermark_value is None
            or settled_window is None
        ):
            return False
        try:
            watermark_date = datetime.fromisoformat(series.watermark_value).date()
            settled_window_date = datetime.fromisoformat(settled_window).date()
        except (TypeError, ValueError):
            return False
        return watermark_date >= settled_window_date

    if not any(
        isinstance(record.content, DbtNodeErrorFact)
        and record.content.node_id == scenario.direct_failure
        for record in root_records
    ):
        return False

    mutation = next(
        (
            item
            for item in scenario.reset_and_injection_contract.mutations
            if isinstance(item, (ColumnRenameMutation, ColumnTypeMutation, SetFieldNullMutation))
        ),
        None,
    )
    if isinstance(mutation, SetFieldNullMutation):
        profile = next(
            (
                record.content
                for record in root_records
                if isinstance(record.content, RelationDataProfileFact)
                and record.content.relation_name == mutation.relation
            ),
            None,
        )
        column = next(
            (
                item
                for item in profile.snapshot.columns
                if item.column_name == mutation.column
            ),
            None,
        ) if profile is not None else None
        return (
            root_cause_code == "SOURCE_REQUIRED_FIELD_NULL"
            and column is not None
            and column.null_count == 1
        )
    schema = next(
        (
            record.content
            for record in root_records
            if isinstance(record.content, RelationSchemaFact)
            and mutation is not None
            and record.content.relation_name == mutation.relation
        ),
        None,
    )
    if schema is None or mutation is None:
        return False
    columns = {column.name: column for column in schema.columns}
    if isinstance(mutation, ColumnRenameMutation):
        return (
            root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
            and mutation.from_column not in columns
            and mutation.to_column in columns
        )
    return (
        root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
        and mutation.column in columns
        and columns[mutation.column].data_type == mutation.to_type
    )


def _claim_evidence_compatible(
    scenario: ScenarioSpec,
    diagnosis_run: DiagnosisRunResult,
) -> bool:
    diagnosis = diagnosis_run.diagnosis
    inventory = {record.evidence_id: record for record in diagnosis_run.evidence_records}
    if diagnosis.status is DiagnosisStatus.NO_INCIDENT:
        return _health_evidence_valid(scenario, diagnosis_run)
    if diagnosis.status is not DiagnosisStatus.CONFIRMED:
        return False
    roots = [claim for claim in diagnosis.claims if claim.kind == "ROOT_CAUSE"]
    assets = [claim for claim in diagnosis.claims if claim.kind == "AFFECTED_ASSET"]
    if len(roots) != 1 or not assets:
        return False
    root_records = [inventory.get(item) for item in roots[0].evidence_ids]
    if any(record is None for record in root_records):
        return False
    if not _root_cause_evidence_compatible(
        scenario,
        roots[0].root_cause_code,
        root_records,
        tuple(diagnosis_run.evidence_records),
    ):
        return False
    for claim in assets:
        records = [inventory.get(item) for item in claim.evidence_ids]
        if any(record is None for record in records):
            return False
        direct = any(
            isinstance(record.content, DbtNodeErrorFact)
            and record.content.node_id == claim.asset
            for record in records
        )
        downstream = any(
            isinstance(record.content, DbtLineageFact)
            and record.content.direction == "downstream"
            and any(
                node.node_id == claim.asset or node.name == claim.asset
                for node in record.content.related_nodes
            )
            for record in records
        )
        upstream_test_model = any(
            isinstance(record.content, DbtLineageFact)
            and record.content.direction == "upstream"
            and record.content.node_id == scenario.direct_failure
            and any(
                node.node_id == claim.asset
                and node.resource_type == "model"
                and node.distance == 1
                for node in record.content.related_nodes
            )
            for record in records
        )
        failed_test_claim = any(
            isinstance(record.content, DbtNodeErrorFact)
            and record.content.node_id == scenario.direct_failure
            and record.content.resource_type == "test"
            for record in records
        ) and claim.asset == scenario.direct_failure
        if failed_test_claim or (not direct and not downstream and not upstream_test_model):
            return False
    return True


def _insufficiency_matches(scenario: ScenarioSpec, diagnosis_run: DiagnosisRunResult) -> bool:
    diagnosis = diagnosis_run.diagnosis
    if diagnosis.status is not DiagnosisStatus.INSUFFICIENT_EVIDENCE:
        return False
    expected = {
        (gap.gap_kind, gap.subject, gap.reason_code)
        for gap in scenario.observable_evidence_contract.unresolved_gaps
    }
    actual = {
        (item.evidence_kind, item.subject, item.reason_code)
        for item in diagnosis.unresolved_evidence
    }
    if expected != actual:
        return False
    trace = tuple(event for event in diagnosis_run.trace if isinstance(event, ToolTraceEvent))
    for gap in scenario.observable_evidence_contract.unresolved_gaps:
        if gap.tool_name is None:
            continue
        matching_events = tuple(
            event
            for event in trace
            if event.tool_name == gap.tool_name
            and event.error_code is not None
            and gap.subject in event.arguments.values()
        )
        if len(matching_events) != 1:
            return False
        if matching_events[0].error_code != gap.reason_code:
            return False
    return True


def _environment_verified(
    scenario: ScenarioSpec,
    verification: ScenarioVerification,
    run_id: str,
) -> bool:
    if verification.run_id != run_id or verification.incident_case_id != scenario.incident_case_id:
        return False
    if scenario.answerability is Answerability.NO_INCIDENT:
        return (
            verification.status == "HEALTHY_CONTROL"
            and verification.dbt_exit_code == 0
            and not verification.failed_nodes
            and not verification.skipped_nodes
        )
    if scenario.direct_failure is None:
        return (
            verification.status == "EXPECTED_ANOMALY"
            and verification.dbt_exit_code == 0
            and not verification.failed_nodes
            and not verification.skipped_nodes
            and tuple(sorted(verification.affected_assets))
            == tuple(sorted(scenario.affected_assets))
        )
    return (
        verification.status == "EXPECTED_FAILURE"
        and verification.dbt_exit_code != 0
        and verification.failed_nodes == (scenario.direct_failure,)
        and tuple(sorted(verification.affected_assets)) == tuple(sorted(scenario.affected_assets))
    )


def _controller_checks(diagnosis_run: DiagnosisRunResult) -> tuple[ControllerCheck, ...]:
    if diagnosis_run.strategy not in KERNEL_STRATEGIES:
        return ()
    state = diagnosis_run.kernel_state
    if state is None:
        return (
            ControllerCheck(
                code=ControllerCheckCode.KERNEL_STATE_VALID,
                passed=False,
                expected=("KERNEL_STATE",),
                actual=("MISSING",),
                reason_code="KERNEL_STATE_VALID_FAILED",
            ),
        )
    terminal = tuple(
        event for event in diagnosis_run.trace if isinstance(event, KernelStateTraceEvent)
    )
    state_valid = (
        len(terminal) == 1
        and diagnosis_run.trace[-2] == terminal[0]
        and terminal[0].state == state
        and state.run_id == diagnosis_run.diagnosis.run_id
        and state.final_status is not None
        and state.final_status.value == diagnosis_run.diagnosis.status.value
    )
    hypothesis_gate = diagnosis_run.diagnosis.status is not DiagnosisStatus.CONFIRMED or (
        len(state.hypotheses) >= 2
        and state.selected_hypothesis_id is not None
        and sum(item.verdict is HypothesisVerdict.SUPPORTED for item in state.assessments) == 1
        and any(item.verdict is HypothesisVerdict.REFUTED for item in state.assessments)
    )
    gap_gate = not any(
        gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
        for gap in state.gaps
    ) or diagnosis_run.diagnosis.status in {
        DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        DiagnosisStatus.MODEL_ERROR,
    }
    return (
        ControllerCheck(
            code=ControllerCheckCode.KERNEL_STATE_VALID,
            passed=state_valid,
            expected=("TERMINAL_STATE_MATCHES_RESULT",),
            actual=(("VALID",) if state_valid else ("INVALID",)),
            reason_code=f"KERNEL_STATE_VALID_{'PASSED' if state_valid else 'FAILED'}",
        ),
        ControllerCheck(
            code=ControllerCheckCode.KERNEL_HYPOTHESIS_GATE,
            passed=hypothesis_gate,
            expected=("TWO_HYPOTHESES_AND_REFUTED_ALTERNATIVE",),
            actual=(("VALID",) if hypothesis_gate else ("INVALID",)),
            reason_code=f"KERNEL_HYPOTHESIS_GATE_{'PASSED' if hypothesis_gate else 'FAILED'}",
        ),
        ControllerCheck(
            code=ControllerCheckCode.KERNEL_EVIDENCE_GAP_GATE,
            passed=gap_gate,
            expected=("STATUS_APPROPRIATE_GAPS",),
            actual=(("VALID",) if gap_gate else ("INVALID",)),
            reason_code=f"KERNEL_EVIDENCE_GAP_GATE_{'PASSED' if gap_gate else 'FAILED'}",
        ),
    )


class DeterministicEvaluator:
    @staticmethod
    def evaluate(
        scenario: ScenarioSpec,
        verification: ScenarioVerification,
        diagnosis_run: DiagnosisRunResult,
        *,
        recovery_succeeded: bool,
    ) -> EvaluationResult:
        diagnosis = diagnosis_run.diagnosis
        records = diagnosis_run.evidence_records
        inventory = {record.evidence_id: record for record in records}
        cited_ids = tuple(dict.fromkeys(diagnosis.evidence_ids))
        all_claim_ids = tuple(
            evidence_id
            for claim in diagnosis.claims
            for evidence_id in claim.evidence_ids
        )
        all_citations = tuple(dict.fromkeys((*cited_ids, *all_claim_ids)))
        unknown = tuple(item for item in all_citations if item not in inventory)
        trace_events = tuple(
            event for event in diagnosis_run.trace if isinstance(event, ToolTraceEvent)
        )
        trace_evidence_ids = tuple(
            evidence_id for event in trace_events for evidence_id in event.evidence_ids
        )
        trace_violations = tuple(
            sorted(
                {
                    "UNKNOWN_TOOL"
                    for event in trace_events
                    if event.tool_name not in ALLOWED_DIAGNOSTIC_TOOLS
                }
                | {
                    "TRACE_ARGUMENT_LEAK"
                    for event in trace_events
                    if any(
                        TRACE_FORBIDDEN_PATTERN.search(value)
                        for value in event.arguments.values()
                    )
                }
                | {
                    "TRACE_EVIDENCE_INVENTORY_MISMATCH"
                    if set(trace_evidence_ids) != set(inventory)
                    else ""
                }
                - {""}
            )
        )
        cited_types = tuple(
            sorted(
                {
                    inventory[item].evidence_type.value
                    for item in cited_ids
                    if item in inventory
                }
            )
        )
        required_types = tuple(sorted(scenario.required_evidence_types))
        confirmed = scenario.expected_status == "CONFIRMED"
        insufficient = scenario.expected_status == "INSUFFICIENT_EVIDENCE"
        health = scenario.expected_status == "NO_INCIDENT"
        assets_exact = set(diagnosis.affected_assets) == set(scenario.affected_assets)
        root_accepted = (
            diagnosis.root_cause_code in scenario.ground_truth_or_acceptable_root_causes
        )
        checks = (
            _check(
                EvaluationCheckCode.ENVIRONMENT_VERIFIED,
                _environment_verified(scenario, verification, diagnosis.run_id),
                ("PRIVATE_SCENARIO_VERIFIED",),
                ("VERIFIED",)
                if _environment_verified(scenario, verification, diagnosis.run_id)
                else ("INVALID",),
            ),
            _check(
                EvaluationCheckCode.STATUS_EXACT,
                diagnosis.status.value == scenario.expected_status,
                (scenario.expected_status,),
                (diagnosis.status.value,),
            ),
            _check(
                EvaluationCheckCode.ROOT_CAUSE_ACCEPTED,
                root_accepted,
                tuple(scenario.ground_truth_or_acceptable_root_causes),
                (diagnosis.root_cause_code,) if diagnosis.root_cause_code else (),
                applicable=confirmed,
            ),
            _check(
                EvaluationCheckCode.AFFECTED_ASSETS_EXACT,
                assets_exact,
                tuple(scenario.affected_assets),
                tuple(diagnosis.affected_assets),
                applicable=confirmed,
            ),
            _check(
                EvaluationCheckCode.EVIDENCE_IDS_EXIST,
                not unknown,
                ("ALL_CITED_IDS_EXIST",),
                unknown if unknown else ("ALL_CITED_IDS_EXIST",),
            ),
            _check(
                EvaluationCheckCode.EVIDENCE_RUN_SCOPE,
                all(record.run_id == diagnosis.run_id for record in records)
                and all(
                    inventory[item].run_id == diagnosis.run_id
                    for item in all_citations
                    if item in inventory
                ),
                (diagnosis.run_id,),
                (diagnosis.run_id,)
                if all(record.run_id == diagnosis.run_id for record in records)
                else ("OUT_OF_SCOPE",),
            ),
            _check(
                EvaluationCheckCode.REQUIRED_EVIDENCE_TYPES_PRESENT,
                set(required_types).issubset(cited_types),
                required_types,
                cited_types,
            ),
        _check(
            EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE,
                _claim_evidence_compatible(scenario, diagnosis_run),
                ("TYPE_COMPATIBLE_CLAIMS",),
                ("TYPE_COMPATIBLE_CLAIMS",)
                if _claim_evidence_compatible(scenario, diagnosis_run)
                else ("CLAIM_MATRIX_INVALID",),
                applicable=confirmed or health,
            ),
            _check(
                EvaluationCheckCode.INSUFFICIENCY_GAP_DECLARED,
                _insufficiency_matches(scenario, diagnosis_run),
                ("DECLARED_UNRESOLVED_GAPS",),
                ("DECLARED_UNRESOLVED_GAPS",)
                if _insufficiency_matches(scenario, diagnosis_run)
                else ("GAP_MATRIX_INVALID",),
                applicable=insufficient,
            ),
        _check(
            EvaluationCheckCode.POSITIVE_HEALTH_EVIDENCE,
                _health_evidence_valid(scenario, diagnosis_run),
                ("RUN_PROFILE_HISTORY_RANGE_PROVEN",),
                ("RUN_PROFILE_HISTORY_RANGE_PROVEN",)
                if _health_evidence_valid(scenario, diagnosis_run)
                else ("HEALTH_EVIDENCE_INVALID",),
                applicable=health,
            ),
            _check(
                EvaluationCheckCode.TOOL_ALLOWLIST_EXACT,
                all(event.tool_name in ALLOWED_DIAGNOSTIC_TOOLS for event in trace_events),
                tuple(sorted(ALLOWED_DIAGNOSTIC_TOOLS)),
                tuple(sorted({event.tool_name for event in trace_events})),
            ),
            _check(
                EvaluationCheckCode.TRACE_READ_ONLY_SAFE,
                not trace_violations,
                ("READ_ONLY_TRACE",),
                trace_violations if trace_violations else ("READ_ONLY_TRACE",),
            ),
            _check(
                EvaluationCheckCode.RECOVERY_HEALTHY,
                recovery_succeeded,
                ("HEALTHY",),
                ("HEALTHY",) if recovery_succeeded else ("FAILED",),
            ),
        )
        failed = tuple(
            item.code
            for item in checks
            if item.applicability is EvaluationApplicability.APPLICABLE and not item.passed
        )
        return EvaluationResult(
            incident_case_id=scenario.incident_case_id,
            run_id=diagnosis.run_id,
            status=EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED,
            checks=checks,
            failed_check_codes=failed,
            controller_checks=_controller_checks(diagnosis_run),
            variant_role=(
                scenario.variant_role.value if scenario.variant_role is not None else None
            ),
            answerability=scenario.answerability.value,
            expected_status=scenario.expected_status,
        )


__all__ = [
    "ALLOWED_DIAGNOSTIC_TOOLS",
    "ControllerCheck",
    "ControllerCheckCode",
    "DeterministicEvaluator",
    "EvaluationApplicability",
    "EvaluationCheck",
    "EvaluationCheckCode",
    "EvaluationResult",
    "EvaluationStatus",
    "TRACE_FORBIDDEN_PATTERN",
]
