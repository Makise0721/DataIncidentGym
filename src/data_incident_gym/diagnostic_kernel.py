from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel

from data_incident_gym import diagnostic_contracts as _contracts
from data_incident_gym.diagnosis import KernelStateTraceEvent, UnresolvedEvidence
from data_incident_gym.diagnostic_contracts import (
    ClaimEvidence,
    ClaimKind,
    EvidenceGap,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    InvestigationState,
    KernelDecision,
    KernelError,
    KernelFinalStatus,
    KernelOutcome,
    PreparedToolCall,
    expected_tool_for_gap,
)
from data_incident_gym.diagnostic_validation import (
    ValidationContext,
    validate_asset_claims,
    validate_health_claim_shape,
    validate_health_claims,
    validate_health_run_evidence,
    validate_root_cause_evidence,
    validate_unresolved_declarations,
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

# Private pattern aliases preserved for existing importers (e.g. diagnostic_agent).
_GAP_ID_PATTERN = _contracts._GAP_ID_PATTERN
_HYPOTHESIS_ID_PATTERN = _contracts._HYPOTHESIS_ID_PATTERN
_RUN_ID_PATTERN = _contracts._RUN_ID_PATTERN
_EVIDENCE_ID_PATTERN = _contracts._EVIDENCE_ID_PATTERN
_FINGERPRINT_PATTERN = _contracts._FINGERPRINT_PATTERN
_ROOT_CAUSE_PATTERN = _contracts._ROOT_CAUSE_PATTERN

_SAFE_TOOL_ERRORS = {
    "EVIDENCE_TOOL_ERROR",
    "INVALID_ARTIFACT",
    "NODE_ERROR_NOT_FOUND",
    "NODE_NOT_FOUND",
    "PROFILE_METRIC_UNAVAILABLE",
    "PROFILE_OUTPUT_LIMIT",
    "PROFILE_SNAPSHOT_MISMATCH",
    "PROFILE_SPEC_INVALID",
    "READ_ONLY_DATABASE_ERROR",
    "RELATION_NOT_ALLOWED",
    "RELATION_NOT_FOUND",
    "RUN_CONTEXT_MISMATCH",
    "RUN_NOT_FOUND",
    "RUN_STATE_DRIFT",
}
_SAFE_MODEL_ERRORS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TOOL_CALL_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    canonical = json.dumps(
        {"arguments": arguments, "run_id": run_id, "tool_name": tool_name},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiagnosticKernel:
    def __init__(
        self,
        *,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
        observable_relations: tuple[str, ...] = (),
        observable_schema_relations: tuple[str, ...] | None = None,
        observable_profile_relations: tuple[str, ...] | None = None,
        observable_history_relations: tuple[str, ...] | None = None,
        incident_subjects: tuple[str, ...] = (),
        health_target_subjects: tuple[str, ...] = (),
        incident_logical_observed_at: datetime | None = None,
        incident_observations: tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        self._run_id = run_id
        self._allowed_root_cause_codes = allowed_root_cause_codes
        self._model_request_limit = model_request_limit
        self._tool_call_limit = tool_call_limit
        default_relations = set(observable_relations)
        self._observable_relations_by_tool = {
            "get_relation_schema": set(
                observable_schema_relations
                if observable_schema_relations is not None
                else default_relations
            ),
            "get_relation_data_profile": set(
                observable_profile_relations
                if observable_profile_relations is not None
                else default_relations
            ),
            "get_relation_history": set(
                observable_history_relations
                if observable_history_relations is not None
                else default_relations
            ),
        }
        self._incident_subjects = set(incident_subjects)
        self._health_target_subjects = set(health_target_subjects)
        self._incident_logical_observed_at = incident_logical_observed_at
        self._incident_observations = incident_observations
        self._revision = 0
        self._hypotheses: list[Hypothesis] = []
        self._gaps: list[EvidenceGap] = []
        self._assessments: tuple[HypothesisAssessment, ...] = ()
        self._claims: tuple[ClaimEvidence, ...] = ()
        self._records: list[EvidenceRecord] = []
        self._fingerprints: list[str] = []
        self._prepared_calls: dict[str, PreparedToolCall] = {}
        self._final_status: KernelFinalStatus | None = None
        self._gate_reason: str | None = None
        self._selected_hypothesis_id: str | None = None

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
        observable_relations: tuple[str, ...] = (),
        observable_schema_relations: tuple[str, ...] | None = None,
        observable_profile_relations: tuple[str, ...] | None = None,
        observable_history_relations: tuple[str, ...] | None = None,
        incident_subjects: tuple[str, ...] = (),
        health_target_subjects: tuple[str, ...] = (),
        incident_logical_observed_at: datetime | None = None,
        incident_observations: tuple[tuple[str, str, str], ...] = (),
    ) -> DiagnosticKernel:
        if len(allowed_root_cause_codes) < 2:
            raise ValueError("Diagnostic Kernel requires at least two ontology members")
        if len(allowed_root_cause_codes) != len(set(allowed_root_cause_codes)):
            raise ValueError("ontology members must be unique")
        if type(model_request_limit) is not int or model_request_limit <= 0:
            raise ValueError("model request limit must be positive")
        if type(tool_call_limit) is not int or tool_call_limit <= 0:
            raise ValueError("tool call limit must be positive")
        return cls(
            run_id=run_id,
            allowed_root_cause_codes=allowed_root_cause_codes,
            model_request_limit=model_request_limit,
            tool_call_limit=tool_call_limit,
            observable_relations=observable_relations,
            observable_schema_relations=observable_schema_relations,
            observable_profile_relations=observable_profile_relations,
            observable_history_relations=observable_history_relations,
            incident_subjects=incident_subjects,
            health_target_subjects=health_target_subjects,
            incident_logical_observed_at=incident_logical_observed_at,
            incident_observations=incident_observations,
        )

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def provable_relations_by_tool(self) -> dict[str, tuple[str, ...]]:
        """Return the exact relation whitelist each relation tool would accept."""

        return {
            tool_name: tuple(sorted(relations))
            for tool_name, relations in self._observable_relations_by_tool.items()
        }

    def snapshot(self, *, model_requests_used: int) -> InvestigationState:
        if type(model_requests_used) is not int or not 0 <= model_requests_used <= (
            self._model_request_limit
        ):
            raise ValueError("model request usage exceeds Kernel budget")
        return InvestigationState(
            schema_version="p1.investigation.v1",
            run_id=self._run_id,
            revision=self._revision,
            allowed_root_cause_codes=self._allowed_root_cause_codes,
            hypotheses=tuple(self._hypotheses),
            gaps=tuple(self._gaps),
            assessments=self._assessments,
            claims=self._claims,
            evidence_inventory=tuple(record.evidence_id for record in self._records),
            tool_fingerprints=tuple(self._fingerprints),
            model_request_limit=self._model_request_limit,
            model_requests_used=model_requests_used,
            model_requests_remaining=self._model_request_limit - model_requests_used,
            tool_call_limit=self._tool_call_limit,
            tool_calls_used=len(self._fingerprints),
            tool_calls_remaining=self._tool_call_limit - len(self._fingerprints),
            final_status=self._final_status,
            gate_reason=self._gate_reason,
            selected_hypothesis_id=self._selected_hypothesis_id,
        )

    def _error(self, code: str, fingerprint: str | None = None) -> None:
        raise KernelError(code, fingerprint=fingerprint) from None

    def _validate_arguments(self, arguments: object, fingerprint: str) -> dict[str, str]:
        if type(arguments) is not dict or any(
            type(key) is not str or type(value) is not str for key, value in arguments.items()
        ):
            self._error("ARGUMENTS_INVALID", fingerprint)
        return dict(arguments)

    def _validate_tool_mapping(
        self,
        intent: InvestigationIntent,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
    ) -> None:
        expected_tool, expected_direction = expected_tool_for_gap(intent.gap_kind)
        if tool_name != expected_tool:
            self._error("GAP_TOOL_MISMATCH", fingerprint)
        if expected_direction is not None and arguments.get("direction") != expected_direction:
            self._error("GAP_TOOL_MISMATCH", fingerprint)
        expected_keys = {
            "get_dbt_run_results": {"run_id"},
            "get_dbt_node_error": {"run_id", "node_id"},
            "get_relation_schema": {"relation_name"},
            "get_dbt_lineage": {"node_id", "direction"},
            "get_relation_data_profile": {"relation_name"},
            "get_relation_history": {"relation_name"},
        }
        if set(arguments) != expected_keys[tool_name]:
            self._error("ARGUMENTS_INVALID", fingerprint)
        if arguments.get("run_id") is not None and arguments["run_id"] != self._run_id:
            self._error("RUN_CONTEXT_MISMATCH", fingerprint)

    def _known_failed_nodes(self) -> set[str]:
        return {
            node
            for record in self._records
            if isinstance(record.content, DbtRunResultsFact)
            for node in record.content.failed_nodes
        }

    def _known_lineage_nodes(self) -> set[str]:
        return {
            node_id
            for record in self._records
            if isinstance(record.content, DbtLineageFact)
            for node_id in (
                record.content.node_id,
                *(item.node_id for item in record.content.related_nodes),
            )
        }

    def _validate_argument_provenance(
        self,
        tool_name: str,
        arguments: dict[str, str],
        fingerprint: str,
    ) -> None:
        if tool_name == "get_dbt_node_error" and arguments["node_id"] not in (
            self._known_failed_nodes()
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
        if tool_name == "get_dbt_lineage" and arguments["node_id"] not in (
            self._known_failed_nodes() | self._known_lineage_nodes() | self._incident_subjects
        ):
            self._error("NODE_ARGUMENT_NOT_PROVEN", fingerprint)
        if (
            tool_name
            in {"get_relation_schema", "get_relation_data_profile", "get_relation_history"}
            and arguments["relation_name"]
            not in self._observable_relations_by_tool[tool_name]
        ):
            self._error("RELATION_NOT_ALLOWED", fingerprint)

    def prepare_tool(
        self,
        *,
        intent: InvestigationIntent,
        tool_name: str,
        arguments: dict[str, str],
    ) -> PreparedToolCall:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        fingerprint = _fingerprint(self._run_id, tool_name, arguments)
        if len(self._fingerprints) >= self._tool_call_limit:
            self._error("TOOL_CALL_LIMIT", fingerprint)
        if fingerprint in self._fingerprints:
            self._error("DUPLICATE_TOOL_CALL", fingerprint)
        if any(gap.gap_id == intent.gap_id for gap in self._gaps):
            self._error("DUPLICATE_GAP_ID", fingerprint)
        validated = self._validate_arguments(arguments, fingerprint)
        self._validate_tool_mapping(intent, tool_name, validated, fingerprint)
        existing_ids = {item.hypothesis_id for item in self._hypotheses}
        for hypothesis in intent.new_hypotheses:
            if hypothesis.hypothesis_id in existing_ids:
                self._error("DUPLICATE_HYPOTHESIS", fingerprint)
            if hypothesis.root_cause_code not in self._allowed_root_cause_codes:
                self._error("ONTOLOGY_CODE_UNKNOWN", fingerprint)
            existing_ids.add(hypothesis.hypothesis_id)
        if any(
            hypothesis_id not in existing_ids for hypothesis_id in intent.hypothesis_ids
        ):
            self._error("HYPOTHESIS_REFERENCE_UNKNOWN", fingerprint)
        try:
            self._validate_argument_provenance(tool_name, validated, fingerprint)
        except KernelError as error:
            if error.code == "RELATION_NOT_ALLOWED" and error.fingerprint == fingerprint:
                self._record_blocked_relation_gap(intent, tool_name, validated, fingerprint)
            raise
        prepared = PreparedToolCall(
            gap_id=intent.gap_id,
            tool_name=tool_name,
            arguments=validated,
            fingerprint=fingerprint,
        )
        self._fingerprints.append(fingerprint)
        self._revision += 1
        self._hypotheses.extend(intent.new_hypotheses)
        self._gaps.append(
            EvidenceGap(
                gap_id=intent.gap_id,
                gap_kind=intent.gap_kind,
                hypothesis_ids=intent.hypothesis_ids,
                tool_name=tool_name,
                subject=(
                    validated.get("relation_name")
                    or validated.get("node_id")
                    or validated.get("run_id")
                    or tool_name
                ),
                status=EvidenceGapStatus.OPEN,
            )
        )
        self._prepared_calls[fingerprint] = prepared
        return prepared

    def _record_blocked_relation_gap(
        self,
        intent: InvestigationIntent,
        tool_name: str,
        validated: dict[str, str],
        fingerprint: str,
    ) -> None:
        """Mirror the tool layer's verdict: the attempt counts and the gap blocks."""

        self._fingerprints.append(fingerprint)
        self._hypotheses.extend(intent.new_hypotheses)
        self._gaps.append(
            EvidenceGap(
                gap_id=intent.gap_id,
                gap_kind=intent.gap_kind,
                hypothesis_ids=intent.hypothesis_ids,
                tool_name=tool_name,
                subject=validated.get("relation_name", tool_name),
                status=EvidenceGapStatus.BLOCKED,
                error_code="RELATION_NOT_ALLOWED",
            )
        )
        self._revision += 1

    def _prepared_gap_index(self, prepared: PreparedToolCall) -> int:
        if self._prepared_calls.get(prepared.fingerprint) != prepared:
            self._error("PREPARED_CALL_INVALID", prepared.fingerprint)
        for index in range(len(self._gaps) - 1, -1, -1):
            gap = self._gaps[index]
            if gap.gap_id == prepared.gap_id and gap.status is EvidenceGapStatus.OPEN:
                return index
        self._error("GAP_NOT_OPEN", prepared.fingerprint)
        raise AssertionError("unreachable")

    @staticmethod
    def _expected_record_type(gap: EvidenceGap) -> type[BaseModel]:
        return {
            EvidenceGapKind.LOCATE_FAILURE: DbtRunResultsFact,
            EvidenceGapKind.EXPLAIN_FAILURE: DbtNodeErrorFact,
            EvidenceGapKind.DISCOVER_SOURCE_RELATION: DbtLineageFact,
            EvidenceGapKind.DISCRIMINATE_SCHEMA: RelationSchemaFact,
            EvidenceGapKind.MAP_IMPACT: DbtLineageFact,
            EvidenceGapKind.PROFILE_RELATION: RelationDataProfileFact,
            EvidenceGapKind.COMPARE_HISTORY: RelationHistoryFact,
        }[gap.gap_kind]

    def _validate_record_compatibility(
        self,
        prepared: PreparedToolCall,
        gap: EvidenceGap,
        records: tuple[EvidenceRecord, ...],
    ) -> None:
        if not records:
            self._error("EVIDENCE_EMPTY", prepared.fingerprint)
        expected = self._expected_record_type(gap)
        known = {record.evidence_id: record for record in self._records}
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, EvidenceRecord):
                self._error("EVIDENCE_RECORD_INVALID", prepared.fingerprint)
            if record.run_id != self._run_id:
                self._error("RUN_CONTEXT_MISMATCH", prepared.fingerprint)
            if record.evidence_id in seen or (
                record.evidence_id in known and known[record.evidence_id] != record
            ):
                self._error("DUPLICATE_EVIDENCE", prepared.fingerprint)
            seen.add(record.evidence_id)
            if not isinstance(record.content, expected):
                self._error("EVIDENCE_TYPE_MISMATCH", prepared.fingerprint)
            content = record.content
            if (
                isinstance(content, (DbtNodeErrorFact, DbtLineageFact))
                and content.node_id != prepared.arguments.get("node_id")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, DbtLineageFact)
                and content.direction != prepared.arguments.get("direction")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, RelationSchemaFact)
                and content.relation_name != prepared.arguments.get("relation_name")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)
            if (
                isinstance(content, (RelationDataProfileFact, RelationHistoryFact))
                and content.relation_name != prepared.arguments.get("relation_name")
            ):
                self._error("EVIDENCE_SUBJECT_MISMATCH", prepared.fingerprint)

    def record_tool_result(
        self,
        prepared: PreparedToolCall,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        index = self._prepared_gap_index(prepared)
        records = tuple(records)
        self._validate_record_compatibility(prepared, self._gaps[index], records)
        known = {record.evidence_id for record in self._records}
        new_records = tuple(record for record in records if record.evidence_id not in known)
        self._records.extend(new_records)
        self._gaps[index] = self._gaps[index].model_copy(
            update={
                "status": EvidenceGapStatus.CLOSED,
                "evidence_ids": tuple(record.evidence_id for record in records),
            }
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1
        return new_records

    def record_tool_failure(self, prepared: PreparedToolCall, error_code: str) -> None:
        index = self._prepared_gap_index(prepared)
        safe_code = error_code if error_code in _SAFE_TOOL_ERRORS else "EVIDENCE_TOOL_ERROR"
        self._gaps[index] = self._gaps[index].model_copy(
            update={"status": EvidenceGapStatus.BLOCKED, "error_code": safe_code}
        )
        self._prepared_calls.pop(prepared.fingerprint, None)
        self._revision += 1

    def _closed_records(self, evidence_ids: tuple[str, ...]) -> dict[str, EvidenceRecord]:
        closed = {
            evidence_id
            for gap in self._gaps
            if gap.status is EvidenceGapStatus.CLOSED
            for evidence_id in gap.evidence_ids
        }
        inventory = {record.evidence_id: record for record in self._records}
        if any(
            evidence_id not in inventory or evidence_id not in closed
            for evidence_id in evidence_ids
        ):
            self._error("CLAIM_EVIDENCE_UNBOUND")
        return inventory

    def _validate_confirmed(self, decision: KernelDecision) -> KernelOutcome:
        if len(self._hypotheses) < 2:
            self._error("ALTERNATIVE_HYPOTHESIS_REQUIRED")
        if any(gap.status is not EvidenceGapStatus.CLOSED for gap in self._gaps):
            self._error("EVIDENCE_GAP_OPEN")
        assessments = {item.hypothesis_id: item for item in decision.assessments}
        if set(assessments) != {item.hypothesis_id for item in self._hypotheses}:
            self._error("HYPOTHESIS_ASSESSMENT_INCOMPLETE")
        self._closed_records(
            tuple(
                evidence_id
                for assessment in decision.assessments
                for evidence_id in assessment.evidence_ids
            )
        )
        selected_id = decision.selected_hypothesis_id
        assert selected_id is not None
        selected = next(item for item in self._hypotheses if item.hypothesis_id == selected_id)
        if assessments[selected_id].verdict is not HypothesisVerdict.SUPPORTED:
            self._error("SELECTED_HYPOTHESIS_NOT_SUPPORTED")
        if not any(
            item.hypothesis_id != selected_id and item.verdict is HypothesisVerdict.REFUTED
            for item in decision.assessments
        ):
            self._error("REFUTED_HYPOTHESIS_REQUIRED")
        inventory = self._closed_records(
            tuple(evidence_id for claim in decision.claims for evidence_id in claim.evidence_ids)
        )
        root_claims = tuple(item for item in decision.claims if item.kind is ClaimKind.ROOT_CAUSE)
        asset_claims = tuple(
            item for item in decision.claims if item.kind is ClaimKind.AFFECTED_ASSET
        )
        if len(root_claims) != 1 or not asset_claims:
            self._error("CLAIMS_INCOMPLETE")
        root_claim = root_claims[0]
        if root_claim.value != selected.root_cause_code:
            self._error("ROOT_CLAIM_MISMATCH")
        root_records = [inventory[evidence_id] for evidence_id in root_claim.evidence_ids]
        context = self._validation_context()
        validate_root_cause_evidence(context, root_claim.value, root_records)
        validate_asset_claims(context, asset_claims, root_records, inventory)
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for claim in decision.claims for evidence_id in claim.evidence_ids
            )
        )
        self._assessments = decision.assessments
        self._claims = decision.claims
        self._selected_hypothesis_id = selected_id
        self._final_status = KernelFinalStatus.CONFIRMED
        self._gate_reason = "CONFIRMED"
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.CONFIRMED,
            root_cause_code=selected.root_cause_code,
            affected_assets=tuple(claim.value for claim in asset_claims),
            evidence_ids=evidence_ids,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def _validate_health(self, decision: KernelDecision) -> KernelOutcome:
        if any(gap.status is not EvidenceGapStatus.CLOSED for gap in self._gaps):
            self._error("EVIDENCE_GAP_OPEN")
        context = self._validation_context()
        validate_health_run_evidence(context)
        health_claims = tuple(
            item for item in decision.claims if item.kind is ClaimKind.HEALTH_STATE
        )
        validate_health_claim_shape(decision, health_claims)
        inventory = self._closed_records(
            tuple(evidence_id for claim in health_claims for evidence_id in claim.evidence_ids)
        )
        validate_health_claims(context, health_claims, inventory)
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for claim in health_claims for evidence_id in claim.evidence_ids
            )
        )
        self._claims = decision.claims
        self._final_status = KernelFinalStatus.NO_INCIDENT
        self._gate_reason = "NO_INCIDENT"
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.NO_INCIDENT,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=evidence_ids,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def _validation_context(self) -> ValidationContext:
        return ValidationContext(
            incident_subjects=frozenset(self._incident_subjects),
            health_target_subjects=frozenset(self._health_target_subjects),
            incident_logical_observed_at=self._incident_logical_observed_at,
            incident_observations=self._incident_observations,
            all_records=tuple(self._records),
        )

    def _validate_unresolved_declarations(
        self,
        declarations: tuple[UnresolvedEvidence, ...],
    ) -> None:
        def blocked_gaps(gap_kind: EvidenceGapKind) -> frozenset[tuple[str, str]]:
            return frozenset(
                (gap.subject, gap.error_code)
                for gap in self._gaps
                if gap.gap_kind is gap_kind and gap.status is EvidenceGapStatus.BLOCKED
            )

        validate_unresolved_declarations(
            self._validation_context(),
            declarations,
            blocked_schema=blocked_gaps(EvidenceGapKind.DISCRIMINATE_SCHEMA),
            blocked_profiles=blocked_gaps(EvidenceGapKind.PROFILE_RELATION),
            blocked_histories=blocked_gaps(EvidenceGapKind.COMPARE_HISTORY),
        )

    def finalize(self, decision: KernelDecision) -> KernelOutcome:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        if decision.run_id != self._run_id:
            self._error("DECISION_SCOPE_MISMATCH")
        if decision.status == "CONFIRMED":
            return self._validate_confirmed(decision)
        if decision.status == "NO_INCIDENT":
            return self._validate_health(decision)
        if len(self._hypotheses) < 2:
            self._error("ALTERNATIVE_HYPOTHESIS_REQUIRED")
        if not any(
            gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
            for gap in self._gaps
        ) and not decision.unresolved_evidence:
            self._error("INSUFFICIENCY_GAP_REQUIRED")
        self._validate_unresolved_declarations(decision.unresolved_evidence)
        self._claims = ()
        self._final_status = KernelFinalStatus.INSUFFICIENT_EVIDENCE
        self._gate_reason = "INSUFFICIENT_EVIDENCE"
        self._revision += 1
        derived_unresolved = tuple(
            UnresolvedEvidence(
                evidence_kind=(
                    "RELATION_DATA_PROFILE"
                    if gap.gap_kind is EvidenceGapKind.PROFILE_RELATION
                    else (
                        "RELATION_HISTORY"
                        if gap.gap_kind is EvidenceGapKind.COMPARE_HISTORY
                        else "RELATION_SCHEMA"
                    )
                ),
                subject=gap.subject,
                reason_code="RELATION_NOT_ALLOWED"
                if gap.status is EvidenceGapStatus.BLOCKED
                else "NOT_OBSERVABLE",
            )
            for gap in self._gaps
            if gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
            and gap.gap_kind
            in {
                EvidenceGapKind.DISCRIMINATE_SCHEMA,
                EvidenceGapKind.PROFILE_RELATION,
                EvidenceGapKind.COMPARE_HISTORY,
            }
        )
        unresolved = tuple(dict.fromkeys((*derived_unresolved, *decision.unresolved_evidence)))
        return KernelOutcome(
            status=KernelFinalStatus.INSUFFICIENT_EVIDENCE,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=tuple(record.evidence_id for record in self._records),
            unresolved_evidence=unresolved,
            summary=decision.summary,
            recommended_actions=decision.recommended_actions,
            confidence=decision.confidence,
        )

    def terminate_model_error(self, reason_code: str) -> KernelOutcome:
        if self._final_status is not None:
            self._error("KERNEL_FINALIZED")
        if reason_code not in _SAFE_MODEL_ERRORS:
            self._error("MODEL_ERROR_REASON_INVALID")
        self._final_status = KernelFinalStatus.MODEL_ERROR
        self._gate_reason = reason_code
        self._revision += 1
        return KernelOutcome(
            status=KernelFinalStatus.MODEL_ERROR,
            root_cause_code=None,
            affected_assets=(),
            evidence_ids=(),
            summary=reason_code,
            recommended_actions=(),
            confidence=0.0,
        )


__all__ = [
    "ClaimEvidence",
    "ClaimKind",
    "DiagnosticKernel",
    "EvidenceGap",
    "EvidenceGapKind",
    "EvidenceGapStatus",
    "Hypothesis",
    "HypothesisAssessment",
    "HypothesisVerdict",
    "InvestigationIntent",
    "InvestigationState",
    "KernelDecision",
    "KernelError",
    "KernelFinalStatus",
    "KernelOutcome",
    "KernelStateTraceEvent",
    "PreparedToolCall",
]
