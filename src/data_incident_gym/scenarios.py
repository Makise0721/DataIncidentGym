from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.run_context import IncidentBrief

_CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ROOT_CAUSE_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"

CaseId = Annotated[StrictStr, Field(pattern=_CASE_ID_PATTERN.pattern)]
RootCauseCode = Annotated[StrictStr, Field(pattern=_ROOT_CAUSE_PATTERN)]
Digest = Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN.pattern)]

P1_M7_SCENARIO_IDS = (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
)
P1_M8_SCENARIO_IDS = (
    "required_null_payment_id",
    "required_null_order_customer_a",
    "required_null_order_customer_b",
)
REGRESSION_SCENARIO_IDS = ("schema_rename_payment_amount",)
SUPPORTED_SCENARIO_IDS = REGRESSION_SCENARIO_IDS + P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS


class ScenarioError(RuntimeError):
    """Raised when a checked-in scenario cannot be loaded safely."""


class FaultFamily(StrEnum):
    SCHEMA_RENAME = "SCHEMA_RENAME"
    SCHEMA_TYPE_CHANGE = "SCHEMA_TYPE_CHANGE"
    REQUIRED_FIELD_NULL = "REQUIRED_FIELD_NULL"
    ORDER_VOLUME_PATTERN = "ORDER_VOLUME_PATTERN"


class VariantRole(StrEnum):
    DEV_CONFIRMABLE = "DEV_CONFIRMABLE"
    TEST_CONFIRMABLE = "TEST_CONFIRMABLE"
    TEST_INSUFFICIENT = "TEST_INSUFFICIENT"
    NO_INCIDENT_CONTROL = "NO_INCIDENT_CONTROL"


class Answerability(StrEnum):
    CONFIRMABLE = "CONFIRMABLE"
    INSUFFICIENT = "INSUFFICIENT"
    NO_INCIDENT = "NO_INCIDENT"


class ForbiddenLeakage(StrEnum):
    SCENARIO_SPEC = "SCENARIO_SPEC"
    GROUND_TRUTH = "GROUND_TRUTH"
    VARIANT_ROLE = "VARIANT_ROLE"
    ANSWERABILITY = "ANSWERABILITY"
    EXPECTED_STATUS = "EXPECTED_STATUS"
    ACCEPTED_ROOT_CAUSES = "ACCEPTED_ROOT_CAUSES"
    PRIVATE_PATH = "PRIVATE_PATH"


class ColumnRenameMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["COLUMN_RENAME"]
    relation: Literal["raw_payments"]
    from_column: Literal["amount", "total_amount"]
    to_column: Literal["amount", "total_amount"]

    @model_validator(mode="after")
    def validate_distinct_columns(self) -> Self:
        if self.from_column == self.to_column:
            raise ValueError("rename mutation must change the column name")
        if {self.from_column, self.to_column} != {"amount", "total_amount"}:
            raise ValueError("unsupported rename mutation")
        return self


class ColumnTypeMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["COLUMN_TYPE_CHANGE"]
    relation: Literal["raw_payments", "raw_orders"]
    column: Literal["amount", "user_id"]
    from_type: Literal["integer", "text"]
    to_type: Literal["integer", "text"]

    @model_validator(mode="after")
    def validate_supported_change(self) -> Self:
        if self.from_type == self.to_type:
            raise ValueError("type mutation must change the type")
        if (self.relation, self.column) == ("raw_payments", "amount") and (
            self.from_type,
            self.to_type,
        ) != ("integer", "text"):
            raise ValueError("unsupported payment type mutation")
        if (self.relation, self.column) == ("raw_orders", "user_id") and (
            self.from_type,
            self.to_type,
        ) != ("integer", "text"):
            raise ValueError("unsupported order type mutation")
        return self


class AddNullableColumnMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ADD_NULLABLE_COLUMN"]
    relation: Literal["raw_payments"]
    column: Literal["source_batch_note"]
    data_type: Literal["text"]
    nullable: Literal[True]


_M8_NULL_TARGETS: dict[tuple[str, str, str, str, int], int | str] = {
    ("FAULT", "raw_payments", "id", "order_id", 1): 1,
    ("FAULT", "raw_orders", "user_id", "id", 42): 92,
    ("DISTRACTOR", "raw_customers", "last_name", "id", 7): "M.",
}


class SetFieldNullMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["SET_FIELD_NULL"]
    purpose: Literal["FAULT", "DISTRACTOR"]
    relation: Literal["raw_payments", "raw_orders", "raw_customers"]
    column: Literal["id", "user_id", "last_name"]
    selector_column: Literal["id", "order_id"]
    selector_value: StrictInt
    expected_value: StrictInt | StrictStr

    @model_validator(mode="after")
    def validate_frozen_target(self) -> Self:
        key = (
            self.purpose,
            self.relation,
            self.column,
            self.selector_column,
            self.selector_value,
        )
        if _M8_NULL_TARGETS.get(key) != self.expected_value:
            raise ValueError("unsupported required-field NULL mutation")
        return self


class NoMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["NO_MUTATION"]


ScenarioMutation = Annotated[
    ColumnRenameMutation
    | ColumnTypeMutation
    | AddNullableColumnMutation
    | SetFieldNullMutation
    | NoMutation,
    Field(discriminator="kind"),
]


class SeedContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["seed.v1"]
    fixture_path: StrictStr
    fixture_commit: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    seed_names: tuple[StrictStr, ...]
    refresh: Literal["FULL_REFRESH"]

    @model_validator(mode="after")
    def validate_seed_contract(self) -> Self:
        if self.fixture_path != "third_party/jaffle_shop":
            raise ValueError("fixture_path must point to the pinned fixture")
        if self.seed_names != ("raw_customers", "raw_orders", "raw_payments"):
            raise ValueError("seed_names must match the fixed fixture")
        return self


class ResetAndInjectionContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["reset_injection.v1"]
    mutations: tuple[ScenarioMutation, ...]
    restore_strategy: Literal["FULL_REFRESH_BASELINE"]

    @model_validator(mode="after")
    def reject_duplicate_mutations(self) -> Self:
        keys = tuple(
            (mutation.kind, getattr(mutation, "relation", ""), getattr(mutation, "column", ""))
            for mutation in self.mutations
        )
        if len(keys) != len(set(keys)):
            raise ValueError("mutations must not repeat the same target")
        return self


class ObservableEvidenceGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_kind: Literal[
        "RELATION_SCHEMA",
        "RELATION_DATA_PROFILE",
        "TRANSFORMATION_DEFINITION",
    ]
    subject: StrictStr
    reason_code: Literal["NOT_OBSERVABLE", "RELATION_NOT_ALLOWED"]
    tool_name: Literal["get_relation_schema", "get_relation_data_profile"] | None

    @model_validator(mode="after")
    def validate_tool_binding(self) -> Self:
        expected = {
            "RELATION_SCHEMA": "get_relation_schema",
            "RELATION_DATA_PROFILE": "get_relation_data_profile",
            "TRANSFORMATION_DEFINITION": None,
        }[self.gap_kind]
        if self.tool_name != expected:
            raise ValueError("observable evidence gap/tool mismatch")
        return self


class ObservableEvidenceContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["observable_evidence.v1"]
    schema_relations: tuple[StrictStr, ...]
    profile_relations: tuple[StrictStr, ...]
    history_relations: tuple[StrictStr, ...]
    unresolved_gaps: tuple[ObservableEvidenceGap, ...]

    @field_validator("schema_relations", "profile_relations", "history_relations")
    @classmethod
    def validate_relation_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            _IDENTIFIER_PATTERN.fullmatch(value) is None for value in values
        ):
            raise ValueError("relation names must be unique identifiers")
        return values


class NullableColumnSchemaDriftDistractor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["NULLABLE_COLUMN_SCHEMA_DRIFT"]
    relation: Literal["raw_payments"]
    column: Literal["source_batch_note"]
    data_type: Literal["text"]
    nullable: Literal[True]


class NullableFieldNullDistractor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["NULLABLE_FIELD_NULL"]
    relation: Literal["raw_customers"]
    column: Literal["last_name"]
    selector_column: Literal["id"]
    selector_value: Literal[7]


DistractorSpec = Annotated[
    NullableColumnSchemaDriftDistractor | NullableFieldNullDistractor,
    Field(discriminator="kind"),
]


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["scenario.v1"]
    incident_case_id: CaseId
    suite: Literal["P0_REGRESSION", "P1"]
    fault_family: FaultFamily
    variant_role: VariantRole | None
    answerability: Answerability
    seed: SeedContract
    incident_brief: IncidentBrief
    reset_and_injection_contract: ResetAndInjectionContract
    ground_truth_or_acceptable_root_causes: tuple[RootCauseCode, ...]
    direct_failure: StrictStr | None
    affected_assets: tuple[StrictStr, ...]
    observable_evidence_contract: ObservableEvidenceContract
    required_evidence_types: tuple[StrictStr, ...]
    forbidden_leakage: tuple[ForbiddenLeakage, ...]
    distractors: tuple[DistractorSpec, ...]
    expected_status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE", "NO_INCIDENT"]

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.incident_brief.schema_version != "incident_brief.v1":
            raise ValueError("incident_brief schema version is invalid")
        for field_name in (
            "ground_truth_or_acceptable_root_causes",
            "affected_assets",
            "required_evidence_types",
            "forbidden_leakage",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")

        required_leakage = {
            ForbiddenLeakage.SCENARIO_SPEC,
            ForbiddenLeakage.GROUND_TRUTH,
            ForbiddenLeakage.VARIANT_ROLE,
            ForbiddenLeakage.ANSWERABILITY,
            ForbiddenLeakage.EXPECTED_STATUS,
        }
        if not required_leakage.issubset(self.forbidden_leakage):
            raise ValueError("forbidden_leakage must cover all private scenario fields")

        mutations = self.reset_and_injection_contract.mutations
        if self.suite == "P0_REGRESSION":
            if self.variant_role is not None or self.answerability is not Answerability.CONFIRMABLE:
                raise ValueError("P0 regression must not declare a P1 role")
            if self.incident_case_id != "schema_rename_payment_amount":
                raise ValueError("unsupported P0 regression scenario")
            if self.fault_family is not FaultFamily.SCHEMA_RENAME:
                raise ValueError("P0 regression fault family mismatch")
            if (
                self.expected_status != "CONFIRMED"
                or not self.ground_truth_or_acceptable_root_causes
            ):
                raise ValueError("P0 regression must be confirmable")
        else:
            if self.variant_role is None:
                raise ValueError("P1 scenarios require a variant role")
            expected = {
                VariantRole.DEV_CONFIRMABLE: (Answerability.CONFIRMABLE, "CONFIRMED"),
                VariantRole.TEST_CONFIRMABLE: (Answerability.CONFIRMABLE, "CONFIRMED"),
                VariantRole.TEST_INSUFFICIENT: (
                    Answerability.INSUFFICIENT,
                    "INSUFFICIENT_EVIDENCE",
                ),
                VariantRole.NO_INCIDENT_CONTROL: (Answerability.NO_INCIDENT, "NO_INCIDENT"),
            }[self.variant_role]
            if (self.answerability, self.expected_status) != expected:
                raise ValueError("variant role, answerability and status do not match")

        if self.answerability is Answerability.INSUFFICIENT:
            if len(self.ground_truth_or_acceptable_root_causes) < 2:
                raise ValueError("insufficient scenarios require two compatible causes")
            if len(self.observable_evidence_contract.unresolved_gaps) < 2:
                raise ValueError("insufficient scenarios require decisive evidence gaps")
            if self.direct_failure is None or not self.affected_assets:
                raise ValueError("insufficient scenarios retain the observed failure scope")
        elif self.answerability is Answerability.CONFIRMABLE:
            if not self.ground_truth_or_acceptable_root_causes:
                raise ValueError("confirmable scenarios require an accepted root cause")
        else:
            if self.ground_truth_or_acceptable_root_causes:
                raise ValueError("health controls must not contain root-cause answers")
            if self.direct_failure is not None or self.affected_assets:
                raise ValueError("health controls must not contain failure scope")
            if not mutations or any(not isinstance(mutation, NoMutation) for mutation in mutations):
                raise ValueError("health controls must use only NO_MUTATION")

        if (
            self.variant_role is VariantRole.NO_INCIDENT_CONTROL
            and self.fault_family is not FaultFamily.ORDER_VOLUME_PATTERN
        ):
            raise ValueError("health control fault family mismatch")
        if self.fault_family is FaultFamily.SCHEMA_TYPE_CHANGE and any(
            isinstance(mutation, ColumnRenameMutation) for mutation in mutations
        ):
            raise ValueError("type-change scenario cannot contain rename mutation")
        if self.fault_family is FaultFamily.REQUIRED_FIELD_NULL:
            faults = tuple(
                mutation
                for mutation in mutations
                if isinstance(mutation, SetFieldNullMutation) and mutation.purpose == "FAULT"
            )
            distractor_mutations = tuple(
                mutation
                for mutation in mutations
                if isinstance(mutation, SetFieldNullMutation)
                and mutation.purpose == "DISTRACTOR"
            )
            if len(faults) != 1 or any(
                not isinstance(mutation, SetFieldNullMutation)
                for mutation in mutations
            ):
                raise ValueError("required-null scenarios must use one NULL fault mutation")
            is_test_role = self.variant_role in {
                VariantRole.TEST_CONFIRMABLE,
                VariantRole.TEST_INSUFFICIENT,
            }
            if len(distractor_mutations) != (1 if is_test_role else 0):
                raise ValueError("required-null test roles must use one NULL distractor")
            if is_test_role:
                if len(self.distractors) != 1 or not isinstance(
                    self.distractors[0], NullableFieldNullDistractor
                ):
                    raise ValueError("required-null test roles need a NULL distractor declaration")
                distractor = distractor_mutations[0]
                declared = self.distractors[0]
                if (
                    distractor.relation != declared.relation
                    or distractor.column != declared.column
                    or distractor.selector_column != declared.selector_column
                    or distractor.selector_value != declared.selector_value
                ):
                    raise ValueError("required-null distractor does not match its declaration")
            elif self.distractors:
                raise ValueError("required-null development role must not contain distractors")
        elif any(isinstance(mutation, SetFieldNullMutation) for mutation in mutations):
            raise ValueError("SET_FIELD_NULL is only valid for required-null scenarios")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _read_json(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ScenarioError(f"无法读取 ScenarioSpec：{path.name}") from None


def parse_scenario_spec(text: str, source: str) -> ScenarioSpec:
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        spec = ScenarioSpec.model_validate(payload)
    except (ValueError, TypeError, ValidationError, _DuplicateJsonKey):
        raise ScenarioError(f"ScenarioSpec 无效：{source}") from None
    return spec


def load_scenario_spec(
    case_id: str,
    project_root: Path = PROJECT_ROOT,
) -> ScenarioSpec:
    if not isinstance(case_id, str) or _CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ScenarioError("未知场景 ID")
    if case_id not in SUPPORTED_SCENARIO_IDS:
        raise ScenarioError(f"未知场景 ID：{case_id}")
    path = project_root / "config" / "scenarios" / f"{case_id}.json"
    spec = parse_scenario_spec(_read_json(path), str(path))
    if spec.incident_case_id != case_id:
        raise ScenarioError("ScenarioSpec ID 与路径不一致")
    return spec
