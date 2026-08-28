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

    schema_version: Literal["m5.evaluation.v1"]
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
            schema_version="m5.evaluation.v1",
            incident_case_id=diagnosis.incident_case_id,
            run_id=diagnosis.run_id,
            status=EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED,
            checks=checks,
            failed_check_codes=failed,
        )
