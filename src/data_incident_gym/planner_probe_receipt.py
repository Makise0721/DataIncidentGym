"""Write-once, manifest-bound receipt for the planner compatibility probe."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from data_incident_gym.planner_comparison_manifest import PlannerComparisonManifest

PLANNER_PROBE_RECEIPT_FILENAME = "planner-probe.json"
PLANNER_PROBE_SEAL_FILENAME = "planner-probe.sha256"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class PlannerProbeReceiptError(RuntimeError):
    """The planner probe receipt is missing, tampered or identity-mismatched."""


class PlannerProbeReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.planner_probe_receipt.v1"] = "p1.planner_probe_receipt.v1"
    manifest_id: StrictStr
    manifest_sha256: StrictStr = Field(pattern=_DIGEST_PATTERN.pattern)
    implementation_revision: StrictStr = Field(pattern=_REVISION_PATTERN.pattern)
    checkout_revision: StrictStr = Field(pattern=_REVISION_PATTERN.pattern)
    result_inputs_sha256: StrictStr = Field(pattern=_DIGEST_PATTERN.pattern)
    model_configuration_sha256: StrictStr = Field(pattern=_DIGEST_PATTERN.pattern)
    scope_sha256: StrictStr = Field(pattern=_DIGEST_PATTERN.pattern)
    scope_cell_count: StrictInt = Field(ge=1)
    model_provider: StrictStr | None
    model_name: StrictStr | None
    passed: StrictBool
    observed: StrictStr
    transport: StrictStr | None
    detail: dict[str, Any]
    checked_at: datetime
    receipt_sha256: StrictStr = Field(pattern=_DIGEST_PATTERN.pattern)

    @model_validator(mode="after")
    def validate_receipt(self) -> PlannerProbeReceipt:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("planner probe receipt timestamp must be timezone-aware")
        if self.receipt_sha256 != _receipt_digest(self.model_dump(mode="json")):
            raise ValueError("planner probe receipt digest does not match")
        if self.passed and self.observed != "PLAN_LOOP_COMPLETED":
            raise ValueError("passing planner probe must complete the plan loop")
        if self.passed and self.detail.get("plan_step_receipts", 0) < 1:
            raise ValueError("passing planner probe must carry an accepted plan-step receipt")
        return self


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _receipt_digest(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("receipt_sha256", None)
    return _digest(unsigned)


def planner_probe_scope_sha256(manifest: PlannerComparisonManifest) -> str:
    """Digest the exact complete experiment scope; selectors are not allowed."""

    return _digest(
        {
            "manifest_id": manifest.manifest_id,
            "selector": None,
            "cells": [cell.model_dump(mode="json") for cell in manifest.cells],
        }
    )


def create_planner_probe_receipt(
    manifest: PlannerComparisonManifest,
    *,
    checkout_revision: str,
    model_provider: str | None,
    model_name: str | None,
    passed: bool,
    observed: str,
    transport: str | None,
    detail: dict[str, Any],
    checked_at: datetime,
) -> PlannerProbeReceipt:
    payload: dict[str, Any] = {
        "schema_version": "p1.planner_probe_receipt.v1",
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.digest(),
        "implementation_revision": manifest.implementation_revision,
        "checkout_revision": checkout_revision,
        "result_inputs_sha256": _digest(manifest.result_inputs.model_dump(mode="json")),
        "model_configuration_sha256": _digest(manifest.model_configuration.model_dump(mode="json")),
        "scope_sha256": planner_probe_scope_sha256(manifest),
        "scope_cell_count": manifest.total_cells,
        "model_provider": model_provider,
        "model_name": model_name,
        "passed": passed,
        "observed": observed,
        "transport": transport,
        "detail": detail,
        "checked_at": checked_at,
    }
    unsigned = PlannerProbeReceipt.model_construct(
        **payload,
        receipt_sha256="0" * 64,
    ).model_dump(mode="json")
    unsigned["receipt_sha256"] = _receipt_digest(unsigned)
    return PlannerProbeReceipt.model_validate(unsigned)


def write_planner_probe_receipt(path: Path, receipt: PlannerProbeReceipt) -> None:
    """Persist the result and an external digest seal exactly once."""

    seal_path = path.with_suffix(".sha256")
    if path.is_symlink() or seal_path.is_symlink() or path.exists() or seal_path.exists():
        raise PlannerProbeReceiptError("planner probe receipt already exists or is invalid")
    encoded = (json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        with seal_path.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(digest + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PlannerProbeReceiptError("cannot persist planner probe receipt") from exc


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def load_planner_probe_receipt(
    path: Path,
    manifest: PlannerComparisonManifest,
    *,
    checkout_revision: str,
) -> PlannerProbeReceipt:
    """Verify the write-once seal, receipt checksum and frozen run identity."""

    seal_path = path.with_suffix(".sha256")
    if path.is_symlink() or seal_path.is_symlink() or not path.is_file() or not seal_path.is_file():
        raise PlannerProbeReceiptError("required planner probe PASS receipt is missing")
    try:
        encoded = path.read_bytes()
        sealed = seal_path.read_text(encoding="ascii")
        if not sealed.endswith("\n") or sealed.count("\n") != 1:
            raise ValueError("planner probe seal has an invalid format")
        expected_file_digest = sealed[:-1]
        if _DIGEST_PATTERN.fullmatch(expected_file_digest) is None:
            raise ValueError("planner probe seal has an invalid digest")
        if hashlib.sha256(encoded).hexdigest() != expected_file_digest:
            raise ValueError("planner probe receipt does not match its seal")
        payload = json.loads(
            encoded,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
        receipt = PlannerProbeReceipt.model_validate(payload)
    except Exception as exc:
        raise PlannerProbeReceiptError("planner probe receipt is invalid or tampered") from exc

    expected = (
        manifest.manifest_id,
        manifest.digest(),
        manifest.implementation_revision,
        checkout_revision,
        _digest(manifest.result_inputs.model_dump(mode="json")),
        _digest(manifest.model_configuration.model_dump(mode="json")),
        planner_probe_scope_sha256(manifest),
        manifest.total_cells,
        manifest.model_configuration.provider,
        manifest.model_configuration.model,
    )
    actual = (
        receipt.manifest_id,
        receipt.manifest_sha256,
        receipt.implementation_revision,
        receipt.checkout_revision,
        receipt.result_inputs_sha256,
        receipt.model_configuration_sha256,
        receipt.scope_sha256,
        receipt.scope_cell_count,
        receipt.model_provider,
        receipt.model_name,
    )
    if actual != expected:
        raise PlannerProbeReceiptError("planner probe receipt identity or scope does not match")
    if not receipt.passed or receipt.observed != "PLAN_LOOP_COMPLETED":
        raise PlannerProbeReceiptError(
            f"planner compatibility probe did not pass: {receipt.observed}"
        )
    return receipt


__all__ = [
    "PLANNER_PROBE_RECEIPT_FILENAME",
    "PLANNER_PROBE_SEAL_FILENAME",
    "PlannerProbeReceipt",
    "PlannerProbeReceiptError",
    "create_planner_probe_receipt",
    "load_planner_probe_receipt",
    "planner_probe_scope_sha256",
    "write_planner_probe_receipt",
]
