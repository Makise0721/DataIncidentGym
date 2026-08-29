from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_incident_gym.run_context import (
    ACTIVE_RUN_PATH,
    ACTIVE_RUN_TEMP_PATH,
    ActiveRun,
    RunContextError,
    clear_active_run,
    publish_active_run,
    resolve_active_run,
    resolve_run_context,
)

RUN_ID = "0123456789abcdef0123456789abcdef"
CASE_ID = "synthetic_case"


def _write_metadata(project_root: Path, *, case_id: str = CASE_ID, run_id: str = RUN_ID) -> Path:
    run_root = project_root / ".dig" / "lab" / "runs" / run_id
    run_root.mkdir(parents=True)
    metadata = {
        "schema_version": "m2.run.v1",
        "run_id": run_id,
        "incident_case_id": case_id,
        "dbt_exit_code": 1,
        "ground_truth_digest": "a" * 64,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
        },
    }
    path = run_root / "metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _active_pointer(project_root: Path) -> Path:
    path = project_root / ".dig" / "lab" / ACTIVE_RUN_PATH.name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _active_payload() -> dict[str, object]:
    return {
        "incident_case_id": CASE_ID,
        "run_id": RUN_ID,
        "schema_version": "m4.active_fault_run.v1",
        "verification_status": "EXPECTED_FAILURE",
    }


def test_publish_and_resolve_active_run_requires_matching_metadata(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    active = publish_active_run(
        tmp_path,
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        verification_status="EXPECTED_FAILURE",
    )

    assert active == ActiveRun(CASE_ID, RUN_ID, "m4.active_fault_run.v1", "EXPECTED_FAILURE")
    assert resolve_active_run(tmp_path, CASE_ID) == active
    assert resolve_run_context(RUN_ID, CASE_ID, project_root=tmp_path).run_id == RUN_ID


def test_resolve_active_run_requires_matching_case_and_metadata(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    publish_active_run(
        tmp_path,
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        verification_status="EXPECTED_FAILURE",
    )

    with pytest.raises(RunContextError):
        resolve_active_run(tmp_path, "other_case")

    metadata_path = tmp_path / ".dig/lab/runs" / RUN_ID / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["incident_case_id"] = "other_case"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(RunContextError):
        resolve_active_run(tmp_path, CASE_ID)


def test_explicit_run_id_rejects_case_mismatch_and_invalid_metadata(tmp_path: Path) -> None:
    metadata_path = _write_metadata(tmp_path, case_id="other_case")
    with pytest.raises(RunContextError):
        resolve_run_context(RUN_ID, CASE_ID, project_root=tmp_path)

    metadata_path.write_bytes(b"{\xff")
    with pytest.raises(RunContextError):
        resolve_run_context(RUN_ID, "other_case", project_root=tmp_path)


def test_active_run_write_is_atomic_and_rejects_symlink(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    lab_root = tmp_path / ".dig" / "lab"
    pointer = lab_root / "active_fault_run.json"
    try:
        pointer.symlink_to(tmp_path / "outside.json")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    with pytest.raises(RunContextError):
        publish_active_run(
            tmp_path,
            incident_case_id=CASE_ID,
            run_id=RUN_ID,
            verification_status="EXPECTED_FAILURE",
        )
    assert pointer.is_symlink()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_key",
        "extra_field",
        "invalid_utf8",
        "invalid_schema",
        "invalid_status",
        "invalid_type",
    ],
)
def test_active_pointer_rejects_invalid_json_contract(
    tmp_path: Path,
    mutation: str,
) -> None:
    _write_metadata(tmp_path)
    pointer = _active_pointer(tmp_path)
    payload = _active_payload()
    if mutation == "duplicate_key":
        pointer.write_text(
            '{"incident_case_id":"synthetic_case","run_id":"0123456789abcdef0123456789abcdef",'
            '"schema_version":"m4.active_fault_run.v1","schema_version":"m4.active_fault_run.v1",'
            '"verification_status":"EXPECTED_FAILURE"}',
            encoding="utf-8",
        )
    elif mutation == "extra_field":
        payload["extra"] = "TEST_REDACTED_VALUE"
        pointer.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "invalid_utf8":
        pointer.write_bytes(b"{\xff")
    elif mutation == "invalid_schema":
        payload["schema_version"] = "m4.other.v1"
        pointer.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "invalid_status":
        payload["verification_status"] = "SUCCEEDED"
        pointer.write_text(json.dumps(payload), encoding="utf-8")
    else:
        payload["run_id"] = 1
        pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunContextError):
        resolve_active_run(tmp_path, CASE_ID)


@pytest.mark.parametrize("fixed_name", [ACTIVE_RUN_PATH.name, ACTIVE_RUN_TEMP_PATH.name])
def test_publish_rejects_fixed_symlink_without_following_it(
    tmp_path: Path,
    fixed_name: str,
) -> None:
    _write_metadata(tmp_path)
    fixed_path = tmp_path / ".dig" / "lab" / fixed_name
    try:
        fixed_path.symlink_to(tmp_path / "outside.json")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    with pytest.raises(RunContextError):
        publish_active_run(
            tmp_path,
            incident_case_id=CASE_ID,
            run_id=RUN_ID,
            verification_status="EXPECTED_FAILURE",
        )
    assert fixed_path.is_symlink()


def test_publish_cleans_fixed_temp_after_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_metadata(tmp_path)
    pointer = tmp_path / ".dig" / "lab" / ACTIVE_RUN_PATH.name
    temporary = tmp_path / ".dig" / "lab" / ACTIVE_RUN_TEMP_PATH.name
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if self == temporary and target == pointer:
            raise OSError("TEST_REDACTED_VALUE")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RunContextError):
        publish_active_run(
            tmp_path,
            incident_case_id=CASE_ID,
            run_id=RUN_ID,
            verification_status="EXPECTED_FAILURE",
        )

    assert not pointer.exists()
    assert not temporary.exists()


def test_context_rejects_duplicate_keys_extra_fields_and_case_mismatch(tmp_path: Path) -> None:
    metadata_path = _write_metadata(tmp_path)
    metadata_path.write_text(
        '{"schema_version":"m2.run.v1","schema_version":"m2.run.v1",'
        '"run_id":"0123456789abcdef0123456789abcdef",'
        '"incident_case_id":"synthetic_case","dbt_exit_code":1,'
        '"ground_truth_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"artifacts":{"manifest":"dbt/target/manifest.json","run_results":"dbt/target/run_results.json",'
        '"dbt_log":"dbt/logs/dbt.log","schema":"schema.json"}}',
        encoding="utf-8",
    )
    with pytest.raises(RunContextError):
        resolve_run_context(RUN_ID, CASE_ID, project_root=tmp_path)

    _write_metadata(tmp_path / "other", case_id="other_case")
    with pytest.raises(RunContextError):
        resolve_run_context(RUN_ID, CASE_ID, project_root=tmp_path / "other")


def test_clear_active_run_only_removes_fixed_pointer_and_temp(tmp_path: Path) -> None:
    _write_metadata(tmp_path)
    publish_active_run(
        tmp_path,
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        verification_status="EXPECTED_FAILURE",
    )
    run_root = tmp_path / ".dig" / "lab" / "runs" / RUN_ID
    (run_root / "ground_truth.json").write_text("synthetic", encoding="utf-8")

    clear_active_run(tmp_path)

    assert not (tmp_path / ".dig/lab/active_fault_run.json").exists()
    assert (run_root / "ground_truth.json").is_file()
