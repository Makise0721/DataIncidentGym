"""Stateless validation stages extracted from the diagnostic kernel.

Validators receive an explicit frozen context of public run facts plus the
claim evidence they must check. They only verify model declarations: on
success they return, on failure they raise the kernel's existing ``KernelError``
codes in the order the kernel previously validated them. They never select a
root cause, complete claims, or mutate investigation state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from data_incident_gym.diagnosis import UnresolvedEvidence
from data_incident_gym.diagnostic_contracts import (
    ClaimEvidence,
    KernelDecision,
    KernelError,
    reject_duplicates,
)
from data_incident_gym.diagnostic_payment_rules import (
    duplicate_root_supported,
    orphan_root_supported,
    silent_drop_root_supported,
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
from data_incident_gym.profiles import parse_watermark_value

_DUPLICATE_ROOTS = {"SOURCE_EXACT_PAYMENT_DUPLICATE", "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"}


@dataclass(frozen=True)
class ValidationContext:
    """Public run facts a validator may rely on, nothing else."""

    incident_subjects: frozenset[str]
    health_target_subjects: frozenset[str]
    incident_logical_observed_at: datetime | None
    incident_observations: tuple[tuple[str, str, str], ...]
    all_records: tuple[EvidenceRecord, ...]


def _incompatible() -> None:
    raise KernelError("ROOT_CLAIM_EVIDENCE_INCOMPATIBLE") from None


def _require_incident_node_and_upstream_evidence(
    context: ValidationContext,
    root_records: list[EvidenceRecord],
) -> None:
    node_errors = tuple(
        record.content
        for record in root_records
        if isinstance(record.content, DbtNodeErrorFact)
    )
    has_relation_fact = any(
        isinstance(record.content, (RelationSchemaFact, RelationDataProfileFact))
        for record in root_records
    )
    if context.incident_subjects and not any(
        error.node_id in context.incident_subjects for error in node_errors
    ):
        _incompatible()
    upstream_relations = {
        node.name
        for record in context.all_records
        if isinstance(record.content, DbtLineageFact)
        and record.content.direction == "upstream"
        and record.content.node_id in {error.node_id for error in node_errors}
        for node in record.content.related_nodes
    }
    if not node_errors or not has_relation_fact or not any(
        getattr(record.content, "relation_name", None) in upstream_relations
        for record in root_records
        if isinstance(record.content, (RelationSchemaFact, RelationDataProfileFact))
    ):
        _incompatible()


def validate_root_cause_evidence(
    context: ValidationContext,
    root_cause_code: str,
    root_records: list[EvidenceRecord],
) -> None:
    """Dispatch the cited root-cause evidence to its domain rule."""

    if root_cause_code == "SOURCE_PAYMENT_INGESTION_LOSS":
        if not silent_drop_root_supported(
            root_cause_code,
            root_records,
            context.incident_subjects,
            context.incident_observations,
            context.all_records,
        ):
            _incompatible()
    elif root_cause_code == "SOURCE_PERMANENT_ORPHAN_PAYMENT":
        if not orphan_root_supported(root_records, context.incident_subjects):
            _incompatible()
    elif root_cause_code in _DUPLICATE_ROOTS:
        if not duplicate_root_supported(root_cause_code, root_records, context.incident_subjects):
            _incompatible()
        if root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE":
            _require_incident_node_and_upstream_evidence(context, root_records)
    else:
        _require_incident_node_and_upstream_evidence(context, root_records)


def validate_asset_claims(
    context: ValidationContext,
    asset_claims: tuple[ClaimEvidence, ...],
    root_records: list[EvidenceRecord],
    inventory: dict[str, EvidenceRecord],
) -> None:
    node_errors = tuple(
        record.content
        for record in root_records
        if isinstance(record.content, DbtNodeErrorFact)
    )
    for claim in asset_claims:
        records = [inventory[evidence_id] for evidence_id in claim.evidence_ids]
        if not any(
            isinstance(record.content, DbtNodeErrorFact)
            and record.content.node_id == claim.value
            for record in records
        ) and not any(
            isinstance(record.content, DbtLineageFact)
            and record.content.direction == "downstream"
            and any(
                node.node_id == claim.value or node.name == claim.value
                for node in record.content.related_nodes
            )
            for record in records
        ) and not any(
            isinstance(record.content, DbtLineageFact)
            and record.content.direction == "upstream"
            and record.content.node_id in {error.node_id for error in node_errors}
            and any(
                node.node_id == claim.value
                and node.resource_type == "model"
                and node.distance == 1
                for node in record.content.related_nodes
            )
            for record in records
        ):
            raise KernelError("ASSET_CLAIM_EVIDENCE_INCOMPATIBLE") from None


def validate_health_run_evidence(context: ValidationContext) -> None:
    run_records = [
        record
        for record in context.all_records
        if isinstance(record.content, DbtRunResultsFact)
    ]
    if not run_records or any(
        record.content.run_status != "SUCCEEDED"
        or record.content.dbt_exit_code != 0
        or record.content.failed_nodes
        or record.content.skipped_nodes
        for record in run_records
    ):
        raise KernelError("HEALTH_RUN_NOT_PROVEN") from None


def validate_health_claim_shape(
    decision: KernelDecision,
    health_claims: tuple[ClaimEvidence, ...],
) -> None:
    if not health_claims or len(health_claims) != len(decision.claims):
        raise KernelError("HEALTH_CLAIM_REQUIRED") from None


def validate_health_claims(
    context: ValidationContext,
    health_claims: tuple[ClaimEvidence, ...],
    inventory: dict[str, EvidenceRecord],
) -> None:
    def _error(code: str) -> None:
        raise KernelError(code) from None

    for claim in health_claims:
        records = [inventory[evidence_id] for evidence_id in claim.evidence_ids]
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
        if profile is None or history is None:
            _error("HEALTH_EVIDENCE_INCOMPATIBLE")
        history_series = next(
            (
                series
                for series in history.snapshot.histories
                if series.name == claim.history_name
            ),
            None,
        )
        if history_series is None:
            _error("HEALTH_HISTORY_NOT_DECLARED")
        if context.health_target_subjects and (
            f"{claim.relation_name}/{claim.history_name}/{claim.bucket}"
            not in context.health_target_subjects
        ):
            _error("HEALTH_POINT_NOT_ALERT_TARGET")
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
            _error("HEALTH_POINT_MISMATCH")
        if (
            history.snapshot.relation_name != profile.snapshot.relation_name
            or history.snapshot.relation_name != claim.relation_name
        ):
            _error("HEALTH_RELATION_MISMATCH")
        if (
            history_series.watermark_column != "order_date"
            or history_series.watermark_value is None
        ):
            _error("HEALTH_WATERMARK_NOT_PROVEN")
        is_current_partition = current.bucket == history_series.watermark_value
        try:
            watermark = parse_watermark_value(history_series.watermark_value)
        except (TypeError, ValueError):
            _error("HEALTH_WATERMARK_INVALID")
        if is_current_partition:
            if history_series.sla_seconds is None:
                _error("HEALTH_SLA_NOT_DECLARED")
            if (
                context.incident_logical_observed_at is None
                or context.incident_logical_observed_at.tzinfo is None
                or context.incident_logical_observed_at.utcoffset() is None
            ):
                _error("HEALTH_WATERMARK_INVALID")
            lag = (
                context.incident_logical_observed_at.astimezone(UTC) - watermark
            ).total_seconds()
            if lag < 0 or lag > history_series.sla_seconds:
                _error("HEALTH_SLA_NOT_SATISFIED")
        else:
            try:
                current_bucket = parse_watermark_value(current.bucket)
            except (TypeError, ValueError):
                _error("HEALTH_WATERMARK_INVALID")
            if current_bucket > watermark:
                _error("HEALTH_POINT_AFTER_WATERMARK")
        if not is_current_partition:
            prior = [
                point.value
                for series in history.snapshot.histories
                if series.name == claim.history_name
                for point in series.points
                if point.periodic_key == current.periodic_key and point.bucket < current.bucket
            ]
            if len(prior) < 4 or not min(prior) <= current.value <= max(prior):
                _error("HEALTH_RANGE_NOT_PROVEN")


def validate_unresolved_declarations(
    context: ValidationContext,
    declarations: tuple[UnresolvedEvidence, ...],
    *,
    blocked_schema: frozenset[tuple[str, str]],
    blocked_profiles: frozenset[tuple[str, str]],
    blocked_histories: frozenset[tuple[str, str]],
) -> None:
    reject_duplicates(
        tuple((item.evidence_kind, item.subject, item.reason_code) for item in declarations),
        "unresolved evidence declarations",
    )
    known_subjects = {
        node_id
        for record in context.all_records
        for node_id in (
            getattr(record.content, "node_id", None),
            *(item.node_id for item in getattr(record.content, "related_nodes", ())),
        )
        if isinstance(node_id, str)
    }
    known_subjects.update(
        node_id
        for record in context.all_records
        if isinstance(record.content, DbtRunResultsFact)
        for node_id in (*record.content.failed_nodes, *record.content.skipped_nodes)
    )
    for item in declarations:
        if item.evidence_kind == "RELATION_SCHEMA":
            if (item.subject, item.reason_code) not in blocked_schema:
                raise KernelError("UNRESOLVED_EVIDENCE_UNBOUND") from None
        elif item.evidence_kind == "RELATION_DATA_PROFILE":
            if (item.subject, item.reason_code) not in blocked_profiles:
                raise KernelError("UNRESOLVED_EVIDENCE_UNBOUND") from None
        elif item.evidence_kind == "RELATION_HISTORY":
            if (item.subject, item.reason_code) not in blocked_histories:
                raise KernelError("UNRESOLVED_EVIDENCE_UNBOUND") from None
        elif item.evidence_kind in {"INGESTION_WATERMARK", "PAYMENT_EVENT_IDENTITY"}:
            if (
                item.reason_code != "NOT_OBSERVABLE"
                or item.subject not in context.incident_subjects
            ):
                raise KernelError("UNRESOLVED_EVIDENCE_UNBOUND") from None
        elif item.subject not in known_subjects:
            raise KernelError("UNRESOLVED_EVIDENCE_UNBOUND") from None
