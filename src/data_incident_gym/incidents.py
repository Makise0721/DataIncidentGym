from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from data_incident_gym.config import PROJECT_ROOT

CASE_ID = "schema_rename_payment_amount"


class IncidentCaseError(RuntimeError):
    """Raised when a fixed incident case cannot be loaded safely."""


class InjectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    from_column: Literal["amount"]
    to_column: Literal["total_amount"]


class ExpectedColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    data_type: StrictStr
    nullable: StrictBool
    ordinal_position: StrictInt


class ExpectedSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    healthy_columns: tuple[str, ...]
    fault_columns: tuple[str, ...]
    healthy_column_metadata: tuple[ExpectedColumn, ...]
    fault_column_metadata: tuple[ExpectedColumn, ...]
    row_count: Literal[113]


class GroundTruth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ground_truth.v1"]
    incident_case_id: Literal["schema_rename_payment_amount"]
    root_cause_code: Literal["SOURCE_SCHEMA_COLUMN_RENAMED"]
    injection: InjectionSpec
    direct_failure: Literal["model.jaffle_shop.stg_payments"]
    affected_assets: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    expected_failure_category: Literal["DBT_MODEL_ERROR"]
    expected_schema: ExpectedSchema

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> Self:
        if self.affected_assets != (
            "model.jaffle_shop.stg_payments",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.customers",
        ):
            raise ValueError("affected_assets 不匹配固定案例")
        if self.required_evidence_types != (
            "DBT_NODE_ERROR",
            "RELATION_SCHEMA",
            "DBT_LINEAGE",
        ):
            raise ValueError("required_evidence_types 不匹配固定案例")
        if self.expected_schema.healthy_columns != (
            "id",
            "order_id",
            "payment_method",
            "amount",
        ):
            raise ValueError("healthy_columns 不匹配固定案例")
        if self.expected_schema.fault_columns != (
            "id",
            "order_id",
            "payment_method",
            "total_amount",
        ):
            raise ValueError("fault_columns 不匹配固定案例")
        expected_healthy_metadata = (
            ExpectedColumn(name="id", data_type="integer", nullable=True, ordinal_position=1),
            ExpectedColumn(
                name="order_id", data_type="integer", nullable=True, ordinal_position=2
            ),
            ExpectedColumn(
                name="payment_method", data_type="text", nullable=True, ordinal_position=3
            ),
            ExpectedColumn(
                name="amount", data_type="integer", nullable=True, ordinal_position=4
            ),
        )
        expected_fault_metadata = (
            *expected_healthy_metadata[:3],
            ExpectedColumn(
                name="total_amount", data_type="integer", nullable=True, ordinal_position=4
            ),
        )
        if self.expected_schema.healthy_column_metadata != expected_healthy_metadata:
            raise ValueError("healthy_column_metadata 不匹配固定案例")
        if self.expected_schema.fault_column_metadata != expected_fault_metadata:
            raise ValueError("fault_column_metadata 不匹配固定案例")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        ) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def parse_ground_truth(text: str, source: str) -> GroundTruth:
    try:
        return GroundTruth.model_validate_json(text)
    except ValidationError as exc:
        raise IncidentCaseError(f"Ground Truth 无效：{source}") from exc


def load_ground_truth(
    case_id: str,
    project_root: Path = PROJECT_ROOT,
) -> GroundTruth:
    if case_id != CASE_ID:
        raise IncidentCaseError(f"未知故障案例：{case_id}")
    path = project_root / "config" / "incidents" / f"{CASE_ID}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncidentCaseError(f"无法读取 Ground Truth：{path}") from exc
    return parse_ground_truth(text, str(path))
