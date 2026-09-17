"""Versioned dev/holdout scenario-set registry (T06, management plane).

The registry answers one question for every catalog scenario: has it already
been used for prompt or rule development (dev), or is it reserved as an
unseen sample (holdout)? The partition policy is structural, never cosmetic:

- holdout assignment is decided by fault mechanism and task structure, never
  by seed alone;
- a scenario that has already been used for prompt or rule development can
  never be re-marked as holdout;
- every formal benchmark scenario must be a dev scenario.

The registry file lives at ``config/scenario-sets.json``. Like the scenario
specs it is a checked-in, versioned contract: tampering must fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, model_validator

from data_incident_gym.benchmark_manifest import FORMAL_SCENARIO_IDS
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.scenarios import SUPPORTED_SCENARIO_IDS, CaseId

SCENARIO_SETS_SCHEMA_VERSION = "p1.scenario_sets.v1"
SCENARIO_SETS_PATH = Path("config") / "scenario-sets.json"

# The complete historical development set, frozen at holdout-mechanism
# adoption (2026-09-16): every catalog scenario had already been used for
# prompt or rule development through p1-formal-v22, so none of them can ever
# be re-marked as an unseen holdout sample — not only the formal twelve.
HISTORICAL_DEVELOPMENT_SCENARIO_IDS = (
    "schema_rename_payment_amount",
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "required_null_payment_id",
    "required_null_order_customer_a",
    "required_null_order_customer_b",
    "duplicate_payment_record",
    "duplicate_payment_coupon_a",
    "duplicate_payment_coupon_b",
    "orphan_payment_record",
    "orphan_payment_coupon_a",
    "orphan_payment_coupon_b",
    "silent_payment_drop_record",
    "silent_payment_drop_partition_a",
    "silent_payment_drop_partition_b",
    "order_volume_pattern_a",
    "order_volume_within_sla",
)

SetMembership = Literal["dev", "holdout", "unknown"]


class ScenarioSetsError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


class ScenarioSetEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    reason: StrictStr


class ScenarioSets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scenario_sets.v1"] = SCENARIO_SETS_SCHEMA_VERSION
    partition_rule: StrictStr
    dev_scenarios: tuple[ScenarioSetEntry, ...]
    holdout_scenarios: tuple[ScenarioSetEntry, ...] = ()

    @model_validator(mode="after")
    def validate_partitions(self) -> ScenarioSets:
        dev_ids = tuple(entry.case_id for entry in self.dev_scenarios)
        holdout_ids = tuple(entry.case_id for entry in self.holdout_scenarios)
        if len(dev_ids) != len(set(dev_ids)) or len(holdout_ids) != len(set(holdout_ids)):
            raise ValueError("scenario sets must not repeat a case id")
        overlap = sorted(set(dev_ids) & set(holdout_ids))
        if overlap:
            raise ValueError(f"dev and holdout sets overlap: {', '.join(overlap)}")
        if any(not entry.reason.strip() for entry in self.dev_scenarios + self.holdout_scenarios):
            raise ValueError("every set entry must carry a reason")
        return self

    @property
    def dev_case_ids(self) -> tuple[str, ...]:
        return tuple(entry.case_id for entry in self.dev_scenarios)

    @property
    def holdout_case_ids(self) -> tuple[str, ...]:
        return tuple(entry.case_id for entry in self.holdout_scenarios)

    def set_for(self, case_id: str) -> SetMembership:
        if case_id in self.dev_case_ids:
            return "dev"
        if case_id in self.holdout_case_ids:
            return "holdout"
        return "unknown"


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _catalog_invariants(sets: ScenarioSets) -> None:
    known = set(SUPPORTED_SCENARIO_IDS)
    unknown = sorted((set(sets.dev_case_ids) | set(sets.holdout_case_ids)) - known)
    if unknown:
        raise ScenarioSetsError("SCENARIO_SETS_UNKNOWN_CASE", detail=", ".join(unknown))
    not_dev = sorted(case_id for case_id in FORMAL_SCENARIO_IDS if sets.set_for(case_id) != "dev")
    if not_dev:
        raise ScenarioSetsError("SCENARIO_SETS_FORMAL_NOT_DEV", detail=", ".join(not_dev))
    # The complete historical development set is frozen: a scenario that has
    # already been used for prompt or rule development can never be re-marked
    # as an unseen holdout sample — not only the formal twelve.
    held_out = sorted(set(sets.holdout_case_ids) & set(HISTORICAL_DEVELOPMENT_SCENARIO_IDS))
    if held_out:
        raise ScenarioSetsError(
            "SCENARIO_SETS_HISTORICAL_DEV_HELD_OUT", detail=", ".join(held_out)
        )


def load_scenario_sets(project_root: Path = PROJECT_ROOT) -> ScenarioSets:
    """Load and validate the checked-in dev/holdout registry."""

    path = project_root / SCENARIO_SETS_PATH
    if not path.is_file():
        raise ScenarioSetsError("SCENARIO_SETS_MISSING", detail=str(path))
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeError, ValueError, _DuplicateJsonKey):
        raise ScenarioSetsError("SCENARIO_SETS_INVALID", detail=str(path)) from None
    try:
        sets = ScenarioSets.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        raise ScenarioSetsError("SCENARIO_SETS_INVALID", detail=str(path)) from None
    _catalog_invariants(sets)
    return sets


__all__ = [
    "HISTORICAL_DEVELOPMENT_SCENARIO_IDS",
    "SCENARIO_SETS_PATH",
    "SCENARIO_SETS_SCHEMA_VERSION",
    "ScenarioSetEntry",
    "ScenarioSets",
    "ScenarioSetsError",
    "SetMembership",
    "load_scenario_sets",
]
