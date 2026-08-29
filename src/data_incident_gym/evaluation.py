from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from data_incident_gym.diagnosis import (
    CaseId,
    DiagnosisRunResult,
    DiagnosisStatus,
    RunId,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_kernel import (
    ClaimKind,
    EvidenceGapStatus,
    HypothesisVerdict,
    KernelStateTraceEvent,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    RelationSchemaFact,
)
from data_incident_gym.incidents import GroundTruth
from data_incident_gym.lab_verifier import LabVerification


class EvaluationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class EvaluationCheckCode(StrEnum):
    ENVIRONMENT_VERIFIED = "ENVIRONMENT_VERIFIED"
    INVESTIGATION_STATE_VALID = "INVESTIGATION_STATE_VALID"
    ALTERNATIVE_HYPOTHESIS_REFUTED = "ALTERNATIVE_HYPOTHESIS_REFUTED"
    CLAIM_EVIDENCE_COVERAGE = "CLAIM_EVIDENCE_COVERAGE"
    DIAGNOSIS_CONFIRMED = "DIAGNOSIS_CONFIRMED"
    ROOT_CAUSE_EXACT = "ROOT_CAUSE_EXACT"
    AFFECTED_ASSETS_EXACT = "AFFECTED_ASSETS_EXACT"
    EVIDENCE_IDS_EXIST = "EVIDENCE_IDS_EXIST"
    EVIDENCE_RUN_SCOPE = "EVIDENCE_RUN_SCOPE"
    REQUIRED_EVIDENCE_TYPES_PRESENT = "REQUIRED_EVIDENCE_TYPES_PRESENT"
    EVIDENCE_CONTENT_COMPATIBLE = "EVIDENCE_CONTENT_COMPATIBLE"
    TRACE_READ_ONLY_SAFE = "TRACE_READ_ONLY_SAFE"
    RECOVERY_HEALTHY = "RECOVERY_HEALTHY"


class EvaluationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: EvaluationCheckCode
    passed: StrictBool
    expected: tuple[StrictStr, ...]
    actual: tuple[StrictStr, ...]
    reason_code: StrictStr


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m6.evaluation.v1"]
    incident_case_id: CaseId
    run_id: RunId
    status: EvaluationStatus
    checks: tuple[EvaluationCheck, ...]
    failed_check_codes: tuple[EvaluationCheckCode, ...]

    @model_validator(mode="after")
    def validate_complete_check_set(self) -> Self:
        expected = tuple(EvaluationCheckCode)
        actual = tuple(check.code for check in self.checks)
        if actual != expected:
            raise ValueError("checks must contain every code exactly once in canonical order")
        for check in self.checks:
            suffix = "PASSED" if check.passed else "FAILED"
            if check.reason_code != f"{check.code.value}_{suffix}":
                raise ValueError("check reason_code must match result")
        failed = tuple(check.code for check in self.checks if not check.passed)
        if self.failed_check_codes != failed:
            raise ValueError("failed_check_codes must match checks")
        expected_status = EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED
        if self.status != expected_status:
            raise ValueError("status must match checks")
        return self


ALLOWED_DIAGNOSTIC_TOOLS = frozenset(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
)
TRACE_FORBIDDEN_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]"
    r"|\bbearer\s+"
    r"|\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b"
    r"|(?:[a-z]:[\\/]|\\\\|/)[^\s]*"
)


class DeterministicEvaluator:
    @staticmethod
    def evaluate(
        ground_truth: GroundTruth,
        verification: LabVerification,
        diagnosis_run: DiagnosisRunResult,
        *,
        recovery_succeeded: bool,
    ) -> EvaluationResult:
        diagnosis = diagnosis_run.diagnosis
        inventory = {record.evidence_id: record for record in diagnosis_run.evidence_records}
        cited = tuple(
            inventory[evidence_id]
            for evidence_id in diagnosis.evidence_ids
            if evidence_id in inventory
        )
        unknown_citations = tuple(
            evidence_id
            for evidence_id in diagnosis.evidence_ids
            if evidence_id not in inventory
        )
        trace_evidence_ids = tuple(
            evidence_id
            for event in diagnosis_run.trace
            if isinstance(event, ToolTraceEvent)
            for evidence_id in event.evidence_ids
        )

        state = diagnosis_run.investigation_state
        inventory_ids = tuple(record.evidence_id for record in diagnosis_run.evidence_records)
        terminal_events = tuple(
            event for event in diagnosis_run.trace if isinstance(event, KernelStateTraceEvent)
        )
        terminal_valid = (
            len(terminal_events) == 1
            and bool(diagnosis_run.trace)
            and diagnosis_run.trace[-1] == terminal_events[0]
            and terminal_events[0].state == state
        )
        state_identity_valid = (
            state.incident_case_id == diagnosis.incident_case_id
            and state.run_id == diagnosis.run_id
        )
        state_budget_valid = (
            state.model_requests_used == diagnosis_run.metrics.model_requests
            and state.tool_calls_used == diagnosis_run.metrics.tool_call_attempts
            and state.evidence_inventory == inventory_ids
        )
        state_final_valid = (
            state.final_status is not None
            and state.final_status.value == diagnosis.status.value
            and (
                diagnosis.status != DiagnosisStatus.CONFIRMED
                or not any(
                    gap.status in {EvidenceGapStatus.OPEN, EvidenceGapStatus.BLOCKED}
                    for gap in state.gaps
                )
            )
        )
        hypothesis_ids = tuple(item.hypothesis_id for item in state.hypotheses)
        gap_ids = tuple(item.gap_id for item in state.gaps)
        assessment_ids = tuple(item.hypothesis_id for item in state.assessments)
        hypothesis_id_set = set(hypothesis_ids)
        hypothesis_by_id = {
            item.hypothesis_id: item for item in state.hypotheses
        }
        assessment_by_id = {
            item.hypothesis_id: item for item in state.assessments
        }
        selected_id = state.selected_hypothesis_id
        hypothesis_assessments_complete = (
            diagnosis.status != DiagnosisStatus.CONFIRMED
            or (
                len(assessment_ids) == len(hypothesis_ids)
                and set(assessment_ids) == hypothesis_id_set
            )
        )
        root_claim_values = tuple(
            item.value for item in state.claims if item.kind == ClaimKind.ROOT_CAUSE
        )
        selected_root_claim_aligned = (
            diagnosis.status != DiagnosisStatus.CONFIRMED
            or (
                selected_id is not None
                and selected_id in hypothesis_by_id
                and len(root_claim_values) == 1
                and root_claim_values[0]
                == hypothesis_by_id[selected_id].root_cause_code
            )
        )
        claim_keys = tuple((item.kind, item.value) for item in state.claims)
        state_hypothesis_ref_sequences = tuple(
            item.hypothesis_ids for item in state.gaps
        )
        state_citation_sequences = tuple(
            item.evidence_ids for item in state.gaps
        ) + tuple(item.evidence_ids for item in state.assessments) + tuple(
            item.evidence_ids for item in state.claims
        )
        state_duplicate_sequences = (
            state.allowed_root_cause_codes,
            hypothesis_ids,
            gap_ids,
            assessment_ids,
            claim_keys,
            state.evidence_inventory,
        ) + state_hypothesis_ref_sequences + state_citation_sequences
        state_duplicate_free = all(
            len(values) == len(set(values))
            for values in state_duplicate_sequences
        )
        closed_evidence_ids = {
            evidence_id
            for gap in state.gaps
            if gap.status == EvidenceGapStatus.CLOSED
            for evidence_id in gap.evidence_ids
        }
        inventory_id_set = set(inventory_ids)
        gap_hypothesis_refs_valid = all(
            set(gap.hypothesis_ids).issubset(hypothesis_id_set)
            for gap in state.gaps
        )
        gap_evidence_refs_valid = all(
            set(gap.evidence_ids).issubset(inventory_id_set)
            and (
                (gap.status == EvidenceGapStatus.CLOSED and bool(gap.evidence_ids))
                or (gap.status != EvidenceGapStatus.CLOSED and not gap.evidence_ids)
            )
            for gap in state.gaps
        )
        assessment_evidence_refs_valid = all(
            set(assessment.evidence_ids).issubset(inventory_id_set)
            and set(assessment.evidence_ids).issubset(closed_evidence_ids)
            for assessment in state.assessments
        )
        approved_ontology = (
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        )
        investigation_state_valid = all(
            (
                terminal_valid,
                state_identity_valid,
                state_budget_valid,
                state_final_valid,
                tuple(state.allowed_root_cause_codes) == approved_ontology,
                state_duplicate_free,
                hypothesis_assessments_complete,
                selected_root_claim_aligned,
                gap_hypothesis_refs_valid,
                gap_evidence_refs_valid,
                assessment_evidence_refs_valid,
                all(record.run_id == diagnosis.run_id for record in diagnosis_run.evidence_records),
            )
        )

        selected_supported = (
            diagnosis.status == DiagnosisStatus.CONFIRMED
            and selected_id is not None
            and selected_id in {item.hypothesis_id for item in state.hypotheses}
            and assessment_by_id.get(selected_id) is not None
            and assessment_by_id[selected_id].verdict == HypothesisVerdict.SUPPORTED
        )
        investigation_state_valid = investigation_state_valid and (
            diagnosis.status != DiagnosisStatus.CONFIRMED or selected_supported
        )
        refuted_alternative = any(
            assessment.hypothesis_id != selected_id
            and assessment.verdict == HypothesisVerdict.REFUTED
            and bool(assessment.evidence_ids)
            and set(assessment.evidence_ids).issubset(closed_evidence_ids)
            for assessment in state.assessments
        )
        alternative_hypothesis_passed = (
            diagnosis.status == DiagnosisStatus.CONFIRMED
            and len(state.hypotheses) >= 2
            and selected_supported
            and sum(
                assessment.verdict == HypothesisVerdict.SUPPORTED
                for assessment in state.assessments
            )
            == 1
            and refuted_alternative
        )

        root_claims = [item for item in state.claims if item.kind == ClaimKind.ROOT_CAUSE]
        asset_claims = [
            item for item in state.claims if item.kind == ClaimKind.AFFECTED_ASSET
        ]
        claim_ids: list[str] = []
        for claim in state.claims:
            for evidence_id in claim.evidence_ids:
                if evidence_id not in claim_ids:
                    claim_ids.append(evidence_id)
        root_claim_evidence_ids = (
            root_claims[0].evidence_ids if len(root_claims) == 1 else ()
        )
        root_claim_records = [
            inventory[evidence_id]
            for evidence_id in root_claim_evidence_ids
            if evidence_id in inventory
        ]
        root_claim_types = {record.evidence_type.value for record in root_claim_records}
        root_claim_passed = (
            len(root_claims) == 1
            and root_claims[0].value == diagnosis.root_cause_code
            and {"DBT_NODE_ERROR", "RELATION_SCHEMA"}.issubset(root_claim_types)
        )
        asset_claims_passed = bool(asset_claims)
        for claim in asset_claims:
            records_for_claim = [
                inventory[evidence_id]
                for evidence_id in claim.evidence_ids
                if evidence_id in inventory
            ]
            direct_supported = any(
                isinstance(record.content, DbtNodeErrorFact)
                and record.content.node_id == claim.value
                for record in records_for_claim
            )
            downstream_supported = any(
                isinstance(record.content, DbtLineageFact)
                and record.content.direction == "downstream"
                and any(
                    node.node_id == claim.value or node.name == claim.value
                    for node in record.content.related_nodes
                )
                for record in records_for_claim
            )
            asset_claims_passed = asset_claims_passed and (
                direct_supported or downstream_supported
            )
        asset_values_passed = (
            tuple(item.value for item in asset_claims) == diagnosis.affected_assets
        )
        citation_scope_passed = all(
            evidence_id in inventory and inventory[evidence_id].run_id == diagnosis.run_id
            for claim in state.claims
            for evidence_id in claim.evidence_ids
        )
        claim_records = [
            inventory[evidence_id]
            for claim in state.claims
            for evidence_id in claim.evidence_ids
            if evidence_id in inventory
        ]
        claim_types = {record.evidence_type.value for record in claim_records}
        claim_evidence_passed = (
            diagnosis.status == DiagnosisStatus.CONFIRMED
            and root_claim_passed
            and asset_values_passed
            and asset_claims_passed
            and citation_scope_passed
            and set(claim_ids) == set(diagnosis.evidence_ids)
            and len(claim_ids) == len(diagnosis.evidence_ids)
            and {"DBT_NODE_ERROR", "RELATION_SCHEMA", "DBT_LINEAGE"}.issubset(claim_types)
            and all(evidence_id in closed_evidence_ids for evidence_id in claim_ids)
        )

        environment_passed = (
            verification.status == "EXPECTED_FAILURE"
            and verification.incident_case_id == ground_truth.incident_case_id
            and diagnosis.incident_case_id == ground_truth.incident_case_id
            and verification.run_id == diagnosis.run_id
            and verification.failed_nodes == (ground_truth.direct_failure,)
            and tuple(sorted(verification.affected_assets))
            == tuple(sorted(ground_truth.affected_assets))
            and verification.error_category == ground_truth.expected_failure_category
            and verification.ground_truth_digest == ground_truth.digest()
        )
        scope_violations = tuple(
            sorted(
                {
                    record.evidence_id
                    for record in diagnosis_run.evidence_records
                    if record.run_id != diagnosis.run_id
                }
                | {
                    evidence_id
                    for evidence_id in trace_evidence_ids
                    if evidence_id not in inventory
                    or inventory[evidence_id].run_id != diagnosis.run_id
                }
            )
        )
        cited_types = tuple(sorted({record.evidence_type.value for record in cited}))
        required_types = tuple(sorted(ground_truth.required_evidence_types))

        asset_candidates: dict[str, set[str]] = {}

        def add_asset_alias(alias: str, node_id: str) -> None:
            asset_candidates.setdefault(alias, set()).add(node_id)

        for record in cited:
            if isinstance(record.content, DbtNodeErrorFact):
                add_asset_alias(record.content.node_id, record.content.node_id)
                add_asset_alias(
                    record.content.node_id.rsplit(".", 1)[-1],
                    record.content.node_id,
                )
            if isinstance(record.content, DbtLineageFact):
                add_asset_alias(record.content.node_id, record.content.node_id)
                add_asset_alias(
                    record.content.node_id.rsplit(".", 1)[-1],
                    record.content.node_id,
                )
                for node in record.content.related_nodes:
                    if node.resource_type == "model":
                        add_asset_alias(node.node_id, node.node_id)
                        add_asset_alias(node.name, node.node_id)

        canonical_assets: list[str] = []
        asset_resolution_failures: list[str] = []
        for asset in diagnosis.affected_assets:
            candidates = asset_candidates.get(asset, set())
            if len(candidates) != 1:
                asset_resolution_failures.append(asset)
            else:
                canonical_assets.append(next(iter(candidates)))
        canonical_asset_tuple = tuple(sorted(canonical_assets))
        expected_asset_tuple = tuple(sorted(ground_truth.affected_assets))
        affected_assets_exact = (
            not asset_resolution_failures
            and len(canonical_assets) == len(set(canonical_assets))
            and canonical_asset_tuple == expected_asset_tuple
        )

        expected_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in ground_truth.expected_schema.fault_column_metadata
        )
        node_error_records = tuple(
            record for record in cited if isinstance(record.content, DbtNodeErrorFact)
        )
        node_error_ok = bool(node_error_records) and all(
            record.content.node_id == ground_truth.direct_failure
            and record.content.status in {"error", "fail"}
            for record in node_error_records
        )
        schema_records = tuple(
            record for record in cited if isinstance(record.content, RelationSchemaFact)
        )
        schema_ok = bool(schema_records) and all(
            record.content.relation_name == ground_truth.injection.relation
            and tuple(
                (
                    column.name,
                    column.data_type,
                    column.nullable,
                    column.ordinal_position,
                )
                for column in record.content.columns
            )
            == expected_columns
            for record in schema_records
        )
        lineage_records = tuple(
            record for record in cited if isinstance(record.content, DbtLineageFact)
        )
        downstream_assets = {
            ground_truth.direct_failure,
            *(
                node.node_id
                for record in lineage_records
                for node in record.content.related_nodes
                if node.resource_type == "model"
            ),
        }
        lineage_ok = (
            bool(lineage_records)
            and all(
                record.content.node_id == ground_truth.direct_failure
                and record.content.direction == "downstream"
                for record in lineage_records
            )
            and downstream_assets == set(ground_truth.affected_assets)
        )
        compatible_actual = tuple(
            marker
            for marker, passed in (
                ("DBT_NODE_ERROR", node_error_ok),
                ("FAULT_RELATION_SCHEMA", schema_ok),
                ("DOWNSTREAM_MODEL_LINEAGE", lineage_ok),
            )
            if passed
        )

        trace_violations: list[str] = []
        for event in diagnosis_run.trace:
            if not isinstance(event, ToolTraceEvent):
                continue
            if event.tool_name not in ALLOWED_DIAGNOSTIC_TOOLS:
                trace_violations.append("UNKNOWN_TOOL")
            if any(
                not isinstance(value, str) or TRACE_FORBIDDEN_PATTERN.search(value)
                for value in event.arguments.values()
            ):
                trace_violations.append(f"ARGUMENT_FINGERPRINT:{event.fingerprint}")
        inventory_ids = set(inventory)
        trace_inventory_ids = set(trace_evidence_ids)
        if inventory_ids != trace_inventory_ids:
            trace_violations.append(
                f"INVENTORY_ONLY_COUNT:{len(inventory_ids - trace_inventory_ids)}"
            )
            trace_violations.append(
                f"TRACE_ONLY_COUNT:{len(trace_inventory_ids - inventory_ids)}"
            )
        trace_violations.extend(f"EVIDENCE_SCOPE:{value}" for value in scope_violations)
        canonical_trace_violations = tuple(sorted(set(trace_violations)))
        if not diagnosis.evidence_ids:
            citation_actual = ("NO_CITATIONS",)
        elif unknown_citations:
            citation_actual = unknown_citations
        else:
            citation_actual = ("ALL_CITED_IDS_EXIST",)

        def check(
            code: EvaluationCheckCode,
            passed: bool,
            expected: tuple[str, ...],
            actual: tuple[str, ...],
        ) -> EvaluationCheck:
            return EvaluationCheck(
                code=code,
                passed=passed,
                expected=expected,
                actual=actual,
                reason_code=f"{code.value}_{'PASSED' if passed else 'FAILED'}",
            )

        checks = (
            check(
                EvaluationCheckCode.ENVIRONMENT_VERIFIED,
                environment_passed,
                ("EXPECTED_FAILURE", ground_truth.digest()),
                (verification.status, verification.ground_truth_digest),
            ),
            check(
                EvaluationCheckCode.INVESTIGATION_STATE_VALID,
                investigation_state_valid,
                ("TERMINAL_STATE_IDENTITY_BUDGETS",),
                (("VALID",) if investigation_state_valid else ("INVALID",)),
            ),
            check(
                EvaluationCheckCode.ALTERNATIVE_HYPOTHESIS_REFUTED,
                alternative_hypothesis_passed,
                ("SUPPORTED_SELECTED_AND_REFUTED_ALTERNATIVE",),
                (
                    ("SUPPORTED_SELECTED_AND_REFUTED_ALTERNATIVE",)
                    if alternative_hypothesis_passed
                    else ("HYPOTHESIS_MATRIX_INVALID",)
                ),
            ),
            check(
                EvaluationCheckCode.CLAIM_EVIDENCE_COVERAGE,
                claim_evidence_passed,
                ("ROOT_ASSETS_AND_CITATIONS_COVERED",),
                (
                    ("ROOT_ASSETS_AND_CITATIONS_COVERED",)
                    if claim_evidence_passed
                    else ("CLAIM_MATRIX_INVALID",)
                ),
            ),
            check(
                EvaluationCheckCode.DIAGNOSIS_CONFIRMED,
                diagnosis.status == DiagnosisStatus.CONFIRMED,
                (DiagnosisStatus.CONFIRMED.value,),
                (diagnosis.status.value,),
            ),
            check(
                EvaluationCheckCode.ROOT_CAUSE_EXACT,
                diagnosis.root_cause_code == ground_truth.root_cause_code,
                (ground_truth.root_cause_code,),
                (
                    ()
                    if diagnosis.root_cause_code is None
                    else (diagnosis.root_cause_code,)
                ),
            ),
            check(
                EvaluationCheckCode.AFFECTED_ASSETS_EXACT,
                affected_assets_exact,
                expected_asset_tuple,
                (
                    canonical_asset_tuple
                    if not asset_resolution_failures
                    else (f"UNRESOLVED_ASSET_COUNT:{len(asset_resolution_failures)}",)
                ),
            ),
            check(
                EvaluationCheckCode.EVIDENCE_IDS_EXIST,
                bool(diagnosis.evidence_ids) and not unknown_citations,
                ("ALL_CITED_IDS_EXIST",),
                citation_actual,
            ),
            check(
                EvaluationCheckCode.EVIDENCE_RUN_SCOPE,
                not scope_violations,
                (diagnosis.run_id,),
                (scope_violations if scope_violations else (diagnosis.run_id,)),
            ),
            check(
                EvaluationCheckCode.REQUIRED_EVIDENCE_TYPES_PRESENT,
                set(required_types).issubset(cited_types),
                required_types,
                cited_types,
            ),
            check(
                EvaluationCheckCode.EVIDENCE_CONTENT_COMPATIBLE,
                node_error_ok and schema_ok and lineage_ok,
                ("DBT_NODE_ERROR", "FAULT_RELATION_SCHEMA", "DOWNSTREAM_MODEL_LINEAGE"),
                compatible_actual,
            ),
            check(
                EvaluationCheckCode.TRACE_READ_ONLY_SAFE,
                not canonical_trace_violations,
                ("READ_ONLY_TRACE",),
                (
                    canonical_trace_violations
                    if canonical_trace_violations
                    else ("READ_ONLY_TRACE",)
                ),
            ),
            check(
                EvaluationCheckCode.RECOVERY_HEALTHY,
                recovery_succeeded,
                ("HEALTHY",),
                (("HEALTHY",) if recovery_succeeded else ("FAILED",)),
            ),
        )
        failed = tuple(item.code for item in checks if not item.passed)
        return EvaluationResult(
            schema_version="m6.evaluation.v1",
            incident_case_id=diagnosis.incident_case_id,
            run_id=diagnosis.run_id,
            status=EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED,
            checks=checks,
            failed_check_codes=failed,
        )
