"""Dev/holdout scenario-set registry tests (T06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_incident_gym.benchmark_manifest import FORMAL_SCENARIO_IDS
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.scenario_sets import (
    SCENARIO_SETS_PATH,
    ScenarioSetsError,
    load_scenario_sets,
)
from data_incident_gym.scenarios import (
    P1_SCENARIO_IDS,
    P1_T12_DEV_EXTENSION_IDS,
    P1_T13_PUBLIC_EVIDENCE_IDS,
    REGRESSION_SCENARIO_IDS,
    SUPPORTED_SCENARIO_IDS,
)

DEV_REASON = "used for prompt/rule development through p1-formal-v22"


def _payload() -> dict[str, object]:
    return {
        "schema_version": "p1.scenario_sets.v1",
        "partition_rule": "holdout by mechanism and task structure, never by seed alone",
        "dev_scenarios": [
            {"case_id": case_id, "reason": DEV_REASON} for case_id in SUPPORTED_SCENARIO_IDS
        ],
        "holdout_scenarios": [],
    }


def _write_registry(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / SCENARIO_SETS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_checked_in_registry_marks_the_whole_catalog_dev() -> None:
    sets = load_scenario_sets()

    assert sets.schema_version == "p1.scenario_sets.v1"
    assert sets.holdout_case_ids == ()
    assert sets.dev_case_ids == SUPPORTED_SCENARIO_IDS
    for case_id in SUPPORTED_SCENARIO_IDS:
        assert sets.set_for(case_id) == "dev"
    assert sets.set_for("not_a_catalog_case") == "unknown"
    historical = set(REGRESSION_SCENARIO_IDS) | set(P1_SCENARIO_IDS)
    for entry in sets.dev_scenarios:
        # The pre-T12 catalog shares one frozen reason; each later extension
        # carries its own creation record instead.
        if entry.case_id in historical:
            assert entry.reason == DEV_REASON
        elif entry.case_id in set(P1_T12_DEV_EXTENSION_IDS):
            assert entry.reason and "T12 dev-extension" in entry.reason
        else:
            assert entry.case_id in set(P1_T13_PUBLIC_EVIDENCE_IDS)
            assert entry.reason and "T13 public-evidence" in entry.reason


def test_partition_rule_names_mechanism_and_seed_policy() -> None:
    sets = load_scenario_sets()

    lowered = sets.partition_rule.lower()
    assert "mechanism" in lowered
    assert "seed" in lowered


def test_every_formal_scenario_is_a_dev_scenario() -> None:
    sets = load_scenario_sets()

    for case_id in FORMAL_SCENARIO_IDS:
        assert case_id in SUPPORTED_SCENARIO_IDS
        assert sets.set_for(case_id) == "dev"


def test_historical_development_scenario_cannot_be_marked_holdout(tmp_path: Path) -> None:
    """A non-formal scenario that was already used for prompt/rule development
    can never be re-marked as an unseen holdout sample (audit finding)."""

    payload = _payload()
    payload["dev_scenarios"] = [
        entry
        for entry in payload["dev_scenarios"]
        if entry["case_id"] != "required_null_payment_id"
    ]
    payload["holdout_scenarios"] = [
        {"case_id": "required_null_payment_id", "reason": "reserved unseen variant"}
    ]
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_HISTORICAL_DEV_HELD_OUT"
    assert "required_null_payment_id" in (error.value.detail or "")


def test_formal_scenario_cannot_be_marked_holdout(tmp_path: Path) -> None:
    payload = _payload()
    payload["dev_scenarios"] = [
        entry for entry in payload["dev_scenarios"] if entry["case_id"] != FORMAL_SCENARIO_IDS[0]
    ]
    payload["holdout_scenarios"] = [
        {"case_id": FORMAL_SCENARIO_IDS[0], "reason": "reserved unseen variant"}
    ]
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_FORMAL_NOT_DEV"


def test_disjoint_sets_are_enforced(tmp_path: Path) -> None:
    payload = _payload()
    payload["holdout_scenarios"] = [
        {"case_id": SUPPORTED_SCENARIO_IDS[0], "reason": "overlapping entry"}
    ]
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_INVALID"


def test_unknown_case_ids_are_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["dev_scenarios"] = payload["dev_scenarios"] + [
        {"case_id": "ghost_scenario_id", "reason": "not in the catalog"}
    ]
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_UNKNOWN_CASE"


def test_duplicate_entries_are_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["dev_scenarios"] = payload["dev_scenarios"] + [
        {"case_id": SUPPORTED_SCENARIO_IDS[0], "reason": DEV_REASON}
    ]
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_INVALID"


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["schema_version"] = "p0.scenario_sets.v1"
    _write_registry(tmp_path, payload)

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_INVALID"


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / SCENARIO_SETS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version": "p1.scenario_sets.v1", "schema_version": "p1.scenario_sets.v1"}',
        encoding="utf-8",
    )

    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_INVALID"


def test_missing_registry_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioSetsError) as error:
        load_scenario_sets(tmp_path)
    assert error.value.code == "SCENARIO_SETS_MISSING"


def test_real_registry_path_is_the_expected_location() -> None:
    assert (PROJECT_ROOT / SCENARIO_SETS_PATH).is_file()
