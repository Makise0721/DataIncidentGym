from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from time import monotonic
from typing import Any

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    HealthStateClaim,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import BASE_PROMPT, BASE_PROMPT_VERSION
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceToolError,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaFact,
)
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.profiles import parse_watermark_value
from data_incident_gym.run_context import ObservableRunContext, resolve_run_context

FIXED_RULE_VERSION = "p1.fixed-rule.v1"
FIXED_RULE_TOOL_NAMES = (
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
    "get_relation_data_profile",
    "get_relation_history",
)
FIXED_RULE_TOOL_LIMIT = 8
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    return _digest({"arguments": arguments, "run_id": run_id, "tool_name": tool_name})


def _safe_error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return (
        code
        if isinstance(code, str) and _ERROR_CODE_PATTERN.fullmatch(code)
        else "EVIDENCE_TOOL_ERROR"
    )


def _relation_names(context: ObservableRunContext, kind: str) -> tuple[str, ...]:
    observable = context.runtime.get("observable_relations", {})
    values = observable.get(kind, ()) if isinstance(observable, dict) else ()
    return tuple(value for value in values if isinstance(value, str))


def _subject_relations(context: ObservableRunContext) -> tuple[str, ...]:
    values = set(_relation_names(context, "schema"))
    values.update(_relation_names(context, "profile"))
    values.update(_relation_names(context, "history"))
    values.update(
        subject
        for subject in context.incident_brief.subjects
        if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", subject)
    )
    return tuple(sorted(values))


def _named_relation(context: ObservableRunContext, token: str) -> str | None:
    return next(
        (relation for relation in _subject_relations(context) if token in relation.lower()),
        None,
    )


def fixed_rule_policy_identity() -> PolicyIdentity:
    return PolicyIdentity(
        strategy=DiagnosticStrategy.FIXED_RULE,
        base_prompt_version=BASE_PROMPT_VERSION,
        base_prompt_sha256=hashlib.sha256(BASE_PROMPT.encode("utf-8")).hexdigest(),
        strategy_prompt_version=FIXED_RULE_VERSION,
        strategy_prompt_sha256=_digest(
            {
                "policy": FIXED_RULE_VERSION,
                "rule": "public evidence, bounded calls, fail closed",
            }
        ),
        controller_protocol_version=FIXED_RULE_VERSION,
        controller_protocol_sha256=_digest(
            {
                "policy": FIXED_RULE_VERSION,
                "tool_limit": FIXED_RULE_TOOL_LIMIT,
                "tools": FIXED_RULE_TOOL_NAMES,
            }
        ),
        tool_schema_sha256=_digest(FIXED_RULE_TOOL_NAMES),
    )


def _observation(
    context: ObservableRunContext,
    kind: str,
) -> tuple[str, str] | None:
    matches = tuple(
        (item.subject, item.value)
        for item in context.incident_brief.observations
        if item.kind == kind
    )
    return matches[0] if len(matches) == 1 else None


def _first_content(
    records: list[EvidenceRecord],
    content_type: type[Any],
    *,
    relation_name: str | None = None,
) -> Any | None:
    for record in records:
        content = record.content
        if isinstance(content, content_type) and (
            relation_name is None or getattr(content, "relation_name", None) == relation_name
        ):
            return content
    return None


class FixedRuleRunner:
    """Apply one deterministic diagnosis policy to public evidence only."""

    def __init__(
        self,
        *,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path,
        tools: EvidenceTools,
        context: ObservableRunContext,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._tools = tools
        self._context = context
        self._started_at = monotonic()
        self._records: list[EvidenceRecord] = []
        self._trace: list[ToolTraceEvent | EvidenceGateTraceEvent] = []
        self._errors: dict[tuple[str, str], str] = {}
        self._policy_identity = self._build_policy_identity()

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        tools: EvidenceTools | None = None,
    ) -> FixedRuleRunner:
        context = resolve_run_context(run_id, project_root=project_root)
        return cls(
            run_id=run_id,
            settings=settings,
            project_root=project_root,
            tools=tools or EvidenceTools.for_run(run_id, settings, project_root),
            context=context,
        )

    @property
    def policy_identity(self) -> PolicyIdentity:
        return self._policy_identity

    @property
    def strategy(self) -> DiagnosticStrategy:
        return DiagnosticStrategy.FIXED_RULE

    def _build_policy_identity(self) -> PolicyIdentity:
        return fixed_rule_policy_identity()

    def _call(
        self,
        tool_name: str,
        arguments: dict[str, str],
        call: Callable[[], tuple[EvidenceRecord, ...]],
    ) -> tuple[EvidenceRecord, ...]:
        started_at = monotonic()
        fingerprint = _fingerprint(self._run_id, tool_name, arguments)
        if len(self._trace) >= FIXED_RULE_TOOL_LIMIT:
            error_code = "TOOL_CALL_LIMIT"
            records: tuple[EvidenceRecord, ...] = ()
        else:
            try:
                records = tuple(call())
                if not records:
                    error_code = "EVIDENCE_EMPTY"
                elif any(
                    not isinstance(record, EvidenceRecord)
                    or record.run_id != self._run_id
                    or record.evidence_id in {item.evidence_id for item in self._records}
                    for record in records
                ):
                    error_code = "EVIDENCE_RECORD_INVALID"
                else:
                    error_code = None
            except EvidenceToolError as error:
                records = ()
                error_code = _safe_error_code(error)
            except Exception:
                records = ()
                error_code = "EVIDENCE_TOOL_ERROR"

        subject = next(iter(arguments.values()), tool_name)
        if error_code is None:
            self._records.extend(records)
        else:
            self._errors[(tool_name, subject)] = error_code
        self._trace.append(
            ToolTraceEvent(
                event_type="TOOL_CALL",
                tool_name=tool_name,
                arguments=arguments,
                fingerprint=fingerprint,
                evidence_ids=tuple(record.evidence_id for record in records)
                if error_code is None
                else (),
                error_code=error_code,
                elapsed_ms=max(0, int((monotonic() - started_at) * 1000)),
            )
        )
        return records if error_code is None else ()

    def _run_results(self) -> DbtRunResultsFact | None:
        records = self._call(
            "get_dbt_run_results",
            {"run_id": self._run_id},
            lambda: self._tools.get_dbt_run_results(self._run_id),
        )
        return _first_content(records, DbtRunResultsFact)

    def _node_error(self, node_id: str) -> DbtNodeErrorFact | None:
        records = self._call(
            "get_dbt_node_error",
            {"run_id": self._run_id, "node_id": node_id},
            lambda: self._tools.get_dbt_node_error(self._run_id, node_id),
        )
        return _first_content(records, DbtNodeErrorFact)

    def _lineage(self, node_id: str, direction: str) -> DbtLineageFact | None:
        records = self._call(
            "get_dbt_lineage",
            {"node_id": node_id, "direction": direction},
            lambda: self._tools.get_dbt_lineage(node_id, direction),
        )
        return _first_content(records, DbtLineageFact)

    def _schema(self, relation_name: str) -> RelationSchemaFact | None:
        records = self._call(
            "get_relation_schema",
            {"relation_name": relation_name},
            lambda: self._tools.get_relation_schema(relation_name),
        )
        return _first_content(records, RelationSchemaFact, relation_name=relation_name)

    def _profile(self, relation_name: str) -> RelationDataProfileFact | None:
        records = self._call(
            "get_relation_data_profile",
            {"relation_name": relation_name},
            lambda: self._tools.get_relation_data_profile(relation_name),
        )
        return _first_content(records, RelationDataProfileFact, relation_name=relation_name)

    def _history(self, relation_name: str) -> RelationHistoryFact | None:
        records = self._call(
            "get_relation_history",
            {"relation_name": relation_name},
            lambda: self._tools.get_relation_history(relation_name),
        )
        return _first_content(records, RelationHistoryFact, relation_name=relation_name)

    def _source_relation(self, lineage: DbtLineageFact, text: str) -> str | None:
        sources = tuple(
            node.name for node in lineage.related_nodes if node.resource_type in {"seed", "source"}
        )
        if not sources:
            return None
        lowered = text.lower()
        return next(
            (name for name in sources if "order" in name.lower() and "order" in lowered),
            next(
                (name for name in sources if "payment" in name.lower() and "payment" in lowered),
                sources[0],
            ),
        )

    @staticmethod
    def _affected_model(
        failure_node: str,
        node_error: DbtNodeErrorFact,
        lineage: DbtLineageFact,
    ) -> str | None:
        if node_error.resource_type == "model":
            return failure_node
        return next(
            (
                node.node_id
                for node in lineage.related_nodes
                if node.resource_type == "model" and node.distance == 1
            ),
            None,
        )

    def _insufficient(self, gaps: tuple[tuple[str, str, str], ...]) -> Diagnosis:
        unique = tuple(dict.fromkeys(gaps))
        return Diagnosis(
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            run_id=self._run_id,
            summary="The public evidence does not establish one decisive explanation.",
            evidence_ids=tuple(record.evidence_id for record in self._records),
            unresolved_evidence=tuple(
                {
                    "evidence_kind": kind,
                    "subject": subject,
                    "reason_code": reason,
                }
                for kind, subject, reason in unique
            ),
            recommended_actions=("Collect the unavailable decisive evidence.",),
            confidence=0.0,
        )

    def _confirmed(
        self,
        *,
        root_cause_code: str,
        affected_assets: tuple[str, ...],
        root_evidence_ids: tuple[str, ...],
        asset_evidence_id: str,
        summary: str,
        action: str,
    ) -> Diagnosis:
        assets = tuple(dict.fromkeys(affected_assets))
        if not assets:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", "affected assets", "NOT_OBSERVABLE"),)
            )
        claims = (
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code=root_cause_code,
                evidence_ids=tuple(dict.fromkeys(root_evidence_ids)),
            ),
            *(
                AffectedAssetClaim(
                    kind="AFFECTED_ASSET",
                    asset=asset,
                    evidence_ids=(asset_evidence_id,),
                )
                for asset in assets
            ),
        )
        evidence_ids = tuple(record.evidence_id for record in self._records)
        return Diagnosis(
            status=DiagnosisStatus.CONFIRMED,
            run_id=self._run_id,
            root_cause_code=root_cause_code,
            summary=summary,
            affected_assets=assets,
            evidence_ids=evidence_ids,
            claims=claims,
            recommended_actions=(action,),
            confidence=0.9,
        )

    def _diagnose_failed(self, run: DbtRunResultsFact) -> Diagnosis:
        if len(run.failed_nodes) != 1:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", "failed nodes", "NOT_OBSERVABLE"),)
            )
        failure_node = run.failed_nodes[0]
        node_error = self._node_error(failure_node)
        if node_error is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )
        lineage = self._lineage(failure_node, "upstream")
        if lineage is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )

        text = " ".join((failure_node, node_error.message, self._context.incident_brief.summary))
        source_relation = self._source_relation(lineage, text)
        affected_model = self._affected_model(failure_node, node_error, lineage)
        if source_relation is None or affected_model is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )
        transform_subject = next(
            (
                node.node_id
                for node in lineage.related_nodes
                if node.resource_type == "model"
                and node.node_id.rsplit(".", 1)[-1].startswith("stg_")
            ),
            next(
                (
                    node.node_id
                    for node in lineage.related_nodes
                    if node.resource_type == "model" and node.distance == 1
                ),
                affected_model,
            ),
        )
        schema = self._schema(source_relation)

        if (
            node_error.resource_type == "test"
            or self._context.incident_brief.signal_code == "DBT_TEST_FAILED"
        ):
            profile = self._profile(source_relation)
            if profile is None:
                for relation in _relation_names(self._context, "profile"):
                    if relation != source_relation and self._profile(relation) is not None:
                        break
                return self._insufficient(
                    (
                        ("RELATION_DATA_PROFILE", source_relation, "RELATION_NOT_ALLOWED"),
                        ("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),
                    )
                )
            null_fact = next(
                (column for column in profile.snapshot.columns if column.null_count > 0),
                None,
            )
            if null_fact is None:
                return self._insufficient(
                    (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
                )
            return self._confirmed(
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                affected_assets=(affected_model,),
                root_evidence_ids=tuple(record.evidence_id for record in self._records),
                asset_evidence_id=next(
                    record.evidence_id
                    for record in self._records
                    if isinstance(record.content, DbtLineageFact)
                ),
                summary="A required source field contains null values.",
                action="Restore the required source field and rebuild the affected model.",
            )

        if schema is None:
            self._profile(source_relation)
            self._history(source_relation)
            return self._insufficient(
                (
                    ("RELATION_SCHEMA", source_relation, "RELATION_NOT_ALLOWED"),
                    ("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),
                )
            )

        changed_column = next(
            (
                column
                for column in schema.columns
                if column.name in {"user_id", "customer_id"}
                and column.data_type.lower() in {"text", "character varying"}
            ),
            None,
        )
        if changed_column is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
            )
        return self._confirmed(
            root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            affected_assets=(affected_model,),
            root_evidence_ids=tuple(record.evidence_id for record in self._records),
            asset_evidence_id=next(
                record.evidence_id
                for record in self._records
                if isinstance(record.content, DbtNodeErrorFact)
            ),
            summary="A source column type is incompatible with the failed transformation.",
            action="Restore the source schema contract and rebuild the affected model.",
        )

    def _downstream_assets(self, lineage: DbtLineageFact | None) -> tuple[str, ...]:
        if lineage is None:
            return ()
        return tuple(
            node.node_id
            for node in lineage.related_nodes
            if node.resource_type == "model" and node.distance >= 1
        )

    def _payment_relation(self) -> str | None:
        return _named_relation(self._context, "payment")

    def _order_relation(self) -> str | None:
        return _named_relation(self._context, "order")

    def _payment_lineage(self) -> DbtLineageFact | None:
        seed = next(
            (
                subject
                for subject in self._context.incident_brief.subjects
                if subject.startswith("seed.")
            ),
            None,
        )
        return self._lineage(seed, "downstream") if seed is not None else None

    def _schema_for_payment(self, relation: str) -> RelationSchemaFact | None:
        return self._schema(relation)

    @staticmethod
    def _group_has_value(profile: RelationDataProfileFact, value: str) -> bool:
        return any(
            value in values and count > 0
            for group in profile.snapshot.groups
            for values, count in zip(group.values, group.counts, strict=True)
        )

    def _diagnose_duplicate(self, run: DbtRunResultsFact) -> Diagnosis:
        relation = self._payment_relation()
        if relation is None:
            return self._insufficient(
                (("PAYMENT_EVENT_IDENTITY", "payment relation", "NOT_OBSERVABLE"),)
            )
        lineage = self._payment_lineage()
        schema = self._schema_for_payment(relation)
        profile = self._profile(relation)
        if profile is None:
            return self._insufficient(
                (
                    ("RELATION_DATA_PROFILE", relation, "RELATION_NOT_ALLOWED"),
                    ("PAYMENT_EVENT_IDENTITY", relation, "NOT_OBSERVABLE"),
                )
            )
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
        if (
            lineage is None
            or schema is None
            or key is None
            or fingerprint is None
            or not self._group_has_value(profile, "coupon")
        ):
            return self._insufficient((("PAYMENT_EVENT_IDENTITY", relation, "NOT_OBSERVABLE"),))
        if key.duplicate_count > 0:
            root = "SOURCE_EXACT_PAYMENT_DUPLICATE"
        elif key.duplicate_count == 0 and fingerprint.duplicate_count > 0:
            root = "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
        else:
            return self._insufficient((("PAYMENT_EVENT_IDENTITY", relation, "NOT_OBSERVABLE"),))
        lineage_id = next(
            record.evidence_id
            for record in self._records
            if isinstance(record.content, DbtLineageFact)
            and record.content.direction == "downstream"
        )
        return self._confirmed(
            root_cause_code=root,
            affected_assets=self._downstream_assets(lineage),
            root_evidence_ids=tuple(record.evidence_id for record in self._records),
            asset_evidence_id=lineage_id,
            summary="Payment evidence shows a duplicate event identity.",
            action="Reconcile the duplicate payment events before downstream publication.",
        )

    def _settled_orphan(
        self, profile: RelationDataProfileFact, history: RelationHistoryFact
    ) -> bool:
        relationship = next(
            (
                item
                for item in profile.snapshot.relationship_violations
                if item.name == "order_id_to_raw_orders_id"
            ),
            None,
        )
        settled = _observation(self._context, "SETTLED_ORDER_WINDOW_END")
        series = next(
            (item for item in history.snapshot.histories if item.name == "order_count_by_day"),
            None,
        )
        if (
            relationship is None
            or relationship.violation_count <= 0
            or settled is None
            or series is None
        ):
            return False
        if series.watermark_column != "order_date" or series.watermark_value is None:
            return False
        try:
            return parse_watermark_value(series.watermark_value) >= parse_watermark_value(
                settled[1]
            )
        except (TypeError, ValueError):
            return False

    def _diagnose_orphan(self, run: DbtRunResultsFact) -> Diagnosis:
        payment_relation = self._payment_relation()
        order_relation = self._order_relation()
        if payment_relation is None or order_relation is None:
            return self._insufficient(
                (("INGESTION_WATERMARK", "order relation", "NOT_OBSERVABLE"),)
            )
        lineage = self._payment_lineage()
        schema = self._schema_for_payment(payment_relation)
        profile = self._profile(payment_relation)
        history = self._history(order_relation)
        if history is None:
            return self._insufficient(
                (
                    ("RELATION_HISTORY", order_relation, "RELATION_NOT_ALLOWED"),
                    ("INGESTION_WATERMARK", order_relation, "NOT_OBSERVABLE"),
                )
            )
        if (
            profile is None
            or lineage is None
            or schema is None
            or not self._settled_orphan(profile, history)
        ):
            return self._insufficient((("INGESTION_WATERMARK", order_relation, "NOT_OBSERVABLE"),))
        lineage_id = next(
            record.evidence_id
            for record in self._records
            if isinstance(record.content, DbtLineageFact)
            and record.content.direction == "downstream"
        )
        return self._confirmed(
            root_cause_code="SOURCE_PERMANENT_ORPHAN_PAYMENT",
            affected_assets=self._downstream_assets(lineage),
            root_evidence_ids=tuple(record.evidence_id for record in self._records),
            asset_evidence_id=lineage_id,
            summary="Settled payment records have no corresponding order.",
            action="Reconcile or quarantine the orphaned payment events before publication.",
        )

    def _silent_supported(
        self,
        payment_profile: RelationDataProfileFact,
        order_profile: RelationDataProfileFact,
        payment_history: RelationHistoryFact,
        order_history: RelationHistoryFact,
    ) -> bool:
        current = _observation(self._context, "CURRENT_PERIOD_COUNT")
        expected = _observation(self._context, "EXPECTED_PERIOD_COUNT")
        relation_count = _observation(self._context, "CURRENT_RELATION_COUNT")
        settled = _observation(self._context, "SETTLED_PAYMENT_WINDOW_END")
        if current is None or expected is None or relation_count is None or settled is None:
            return False
        current_subject, current_raw = current
        expected_subject, expected_raw = expected
        parts = current_subject.split("/")
        if len(parts) != 3 or expected_subject != current_subject:
            return False
        payment_relation, history_name, bucket = parts
        if (
            payment_relation != payment_profile.relation_name
            or relation_count[0] != payment_relation
        ):
            return False
        try:
            current_count = int(current_raw)
            expected_count = int(expected_raw)
            relation_total = int(relation_count[1])
            settled_at = parse_watermark_value(settled[1])
        except (TypeError, ValueError):
            return False
        if current_count < 0 or expected_count <= current_count or relation_total < 0:
            return False
        if payment_profile.snapshot.row_count != relation_total:
            return False
        relationship = next(
            (
                item
                for item in order_profile.snapshot.relationship_violations
                if item.name == "id_to_raw_payments_order_id"
            ),
            None,
        )
        if relationship is None or relationship.violation_count != expected_count - current_count:
            return False
        payment_series = next(
            (item for item in payment_history.snapshot.histories if item.name == history_name),
            None,
        )
        order_series = next(
            (
                item
                for item in order_history.snapshot.histories
                if item.name == "order_count_by_day"
            ),
            None,
        )
        if payment_series is None or order_series is None:
            return False
        point = next((item for item in payment_series.points if item.bucket == bucket), None)
        if point is None or point.value != current_count:
            return False
        if order_series.watermark_column != "order_date" or order_series.watermark_value is None:
            return False
        try:
            return parse_watermark_value(order_series.watermark_value) >= settled_at
        except (TypeError, ValueError):
            return False

    def _diagnose_silent(self, run: DbtRunResultsFact) -> Diagnosis:
        payment_relation = self._payment_relation()
        order_relation = self._order_relation()
        if payment_relation is None or order_relation is None:
            return self._insufficient(
                (("INGESTION_WATERMARK", "order relation", "NOT_OBSERVABLE"),)
            )
        lineage = self._payment_lineage()
        payment_profile = self._profile(payment_relation)
        order_profile = self._profile(order_relation)
        payment_history = self._history(payment_relation)
        order_history = self._history(order_relation)
        gaps: list[tuple[str, str, str]] = []
        if payment_history is None:
            gaps.append(("RELATION_HISTORY", payment_relation, "RELATION_NOT_ALLOWED"))
        if order_history is None:
            gaps.append(("RELATION_HISTORY", order_relation, "RELATION_NOT_ALLOWED"))
        if payment_history is None or order_history is None:
            gaps.append(("INGESTION_WATERMARK", order_relation, "NOT_OBSERVABLE"))
        if gaps:
            return self._insufficient(tuple(gaps))
        if (
            lineage is None
            or payment_profile is None
            or order_profile is None
            or not self._silent_supported(
                payment_profile, order_profile, payment_history, order_history
            )
        ):
            return self._insufficient(
                (("PAYMENT_EVENT_IDENTITY", payment_relation, "NOT_OBSERVABLE"),)
            )
        lineage_id = next(
            record.evidence_id
            for record in self._records
            if isinstance(record.content, DbtLineageFact)
            and record.content.direction == "downstream"
        )
        return self._confirmed(
            root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
            affected_assets=self._downstream_assets(lineage),
            root_evidence_ids=tuple(record.evidence_id for record in self._records),
            asset_evidence_id=lineage_id,
            summary="A settled payment partition is lower than its declared expectation.",
            action="Reconcile the missing settled payment events before downstream publication.",
        )

    @staticmethod
    def _health_value(value: str) -> int | float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return int(parsed) if parsed.is_integer() else parsed

    def _diagnose_health(self, run: DbtRunResultsFact) -> Diagnosis:
        relation = self._order_relation()
        target = _observation(self._context, "CURRENT_PERIOD_COUNT")
        if relation is None or target is None:
            return self._insufficient((("RELATION_HISTORY", "order relation", "NOT_OBSERVABLE"),))
        profile = self._profile(relation)
        history = self._history(relation)
        if profile is None:
            return self._insufficient(
                (("RELATION_DATA_PROFILE", relation, "RELATION_NOT_ALLOWED"),)
            )
        if history is None:
            return self._insufficient((("RELATION_HISTORY", relation, "RELATION_NOT_ALLOWED"),))
        parts = target[0].split("/")
        current_value = self._health_value(target[1])
        if len(parts) != 3 or parts[0] != relation or current_value is None:
            return self._insufficient((("RELATION_HISTORY", relation, "NOT_OBSERVABLE"),))
        _, history_name, bucket = parts
        series = next(
            (item for item in history.snapshot.histories if item.name == history_name), None
        )
        point = (
            next((item for item in series.points if item.bucket == bucket), None)
            if series
            else None
        )
        if series is None or point is None or point.value != current_value:
            return self._insufficient((("RELATION_HISTORY", relation, "NOT_OBSERVABLE"),))
        if series.watermark_column != "order_date" or series.watermark_value is None:
            return self._insufficient(
                (("INGESTION_WATERMARK", relation, "NOT_OBSERVABLE"),)
            )
        try:
            watermark = parse_watermark_value(series.watermark_value)
            current_bucket = parse_watermark_value(bucket)
        except (TypeError, ValueError):
            return self._insufficient((
                ("INGESTION_WATERMARK", relation, "NOT_OBSERVABLE"),
            ))
        if current_bucket > watermark:
            return self._insufficient((
                ("INGESTION_WATERMARK", relation, "NOT_OBSERVABLE"),
            ))
        if bucket == series.watermark_value:
            logical = self._context.incident_brief.logical_observed_at.astimezone(UTC)
            if series.sla_seconds is None:
                return self._insufficient(
                    (("INGESTION_WATERMARK", relation, "NOT_OBSERVABLE"),)
                )
            lag = (logical - watermark).total_seconds()
            if lag < 0 or lag > series.sla_seconds:
                return self._insufficient(
                    (("INGESTION_WATERMARK", relation, "NOT_OBSERVABLE"),)
                )
        else:
            prior = tuple(
                item.value
                for item in series.points
                if item.periodic_key == point.periodic_key and item.bucket < bucket
            )
            if len(prior) < 4 or not min(prior) <= current_value <= max(prior):
                return self._insufficient((("RELATION_HISTORY", relation, "NOT_OBSERVABLE"),))
        evidence_ids = tuple(record.evidence_id for record in self._records)
        claim = HealthStateClaim(
            kind="HEALTH_STATE",
            relation_name=relation,
            history_name=history_name,
            bucket=bucket,
            current_value=current_value,
            evidence_ids=evidence_ids,
        )
        return Diagnosis(
            status=DiagnosisStatus.NO_INCIDENT,
            run_id=self._run_id,
            summary=(
                "The observed order volume is supported by the declared history and service window."
            ),
            evidence_ids=evidence_ids,
            claims=(claim,),
            recommended_actions=("Continue observing the order-volume history.",),
            confidence=0.9,
        )

    def _diagnose_successful(self, run: DbtRunResultsFact) -> Diagnosis:
        if run.dbt_exit_code != 0 or run.failed_nodes or run.skipped_nodes:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", "run results", "NOT_OBSERVABLE"),)
            )
        signal = self._context.incident_brief.signal_code
        if signal == "ORDER_VOLUME_ALERT":
            return self._diagnose_health(run)
        if signal == "PAYMENT_DUPLICATE_ALERT":
            return self._diagnose_duplicate(run)
        if signal == "PAYMENT_ORPHAN_ALERT":
            return self._diagnose_orphan(run)
        if signal == "PAYMENT_VOLUME_ALERT":
            return self._diagnose_silent(run)
        return self._insufficient(
            (("TRANSFORMATION_DEFINITION", "alert signal", "NOT_OBSERVABLE"),)
        )

    def _build_result(self, diagnosis: Diagnosis) -> DiagnosisRunResult:
        trace = [
            *self._trace,
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code=diagnosis.status.value,
                accepted=True,
            ),
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=DiagnosticStrategy.FIXED_RULE,
                status=diagnosis.status,
                evidence_inventory=tuple(record.evidence_id for record in self._records),
            ),
        ]
        return DiagnosisRunResult(
            strategy=DiagnosticStrategy.FIXED_RULE,
            policy_identity=self._policy_identity,
            diagnosis=diagnosis,
            evidence_records=tuple(self._records),
            trace=tuple(trace),
            metrics=DiagnosisMetrics(
                provider="fixed-rule",
                model="none",
                model_requests=0,
                input_tokens=0,
                output_tokens=0,
                tool_call_attempts=sum(isinstance(event, ToolTraceEvent) for event in trace),
                successful_tool_calls=sum(
                    isinstance(event, ToolTraceEvent) and event.error_code is None
                    for event in trace
                ),
                elapsed_ms=max(0, int((monotonic() - self._started_at) * 1000)),
            ),
        )

    async def diagnose(self) -> DiagnosisRunResult:
        run = self._run_results()
        if run is None:
            diagnosis = self._insufficient(
                (("TRANSFORMATION_DEFINITION", "run results", "NOT_OBSERVABLE"),)
            )
        elif run.run_status == "FAILED":
            diagnosis = self._diagnose_failed(run)
        elif run.run_status == "SUCCEEDED":
            diagnosis = self._diagnose_successful(run)
        else:
            diagnosis = self._insufficient(
                (("TRANSFORMATION_DEFINITION", "run status", "NOT_OBSERVABLE"),)
            )
        return self._build_result(diagnosis)


__all__ = [
    "FIXED_RULE_TOOL_NAMES",
    "FIXED_RULE_VERSION",
    "FixedRuleRunner",
    "fixed_rule_policy_identity",
]
