"""T11 public task package: leak-free export and fail-closed verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from data_incident_gym.public_package import (
    PublicPackageError,
    build_public_task_package,
    verify_public_package,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_diagnostic_agent import RUN_ID, _write_public_run  # noqa: E402


def _package(tmp_path: Path) -> Path:
    _write_public_run(tmp_path)
    return build_public_task_package(RUN_ID, tmp_path / "package", project_root=tmp_path)


def newline_marker(text: str) -> str:
    return "\n" + text + "\n"


def test_public_package_contains_only_the_public_surface(tmp_path: Path) -> None:
    package = _package(tmp_path)

    report = verify_public_package(package, project_root=tmp_path)
    task = json.loads((package / "task.json").read_text(encoding="utf-8"))

    assert report["clean"] is True
    assert report["findings"] == []
    assert report["files"] == ["README.md", "task.json"]
    assert task["schema_version"] == "p1.public_task_package.v1"
    assert task["run_id"] == RUN_ID
    assert task["tool_allowlist"] and len(task["tool_allowlist"]) == 6
    assert task["budget"]["tool_call_limit"] == 8
    assert task["incident_brief"]["signal_code"]
    assert "expected_status" not in (package / "task.json").read_text(encoding="utf-8")
    assert "trusted_development_mode" in task["isolation"]


def test_verifier_fails_closed_on_a_copied_private_contract(tmp_path: Path) -> None:
    package = _package(tmp_path)
    private = tmp_path / "config" / "scenarios"
    private.mkdir(parents=True)
    contract = private / "some_case.json"
    contract.write_text('{"expected_status": "CONFIRMED"}\n', encoding="utf-8")
    (package / "leaked.json").write_text(
        contract.read_text(encoding="utf-8"), encoding="utf-8"
    )

    report = verify_public_package(package, project_root=tmp_path)

    codes = {finding["code"] for finding in report["findings"]}
    assert report["clean"] is False
    assert {"UNEXPECTED_FILE", "FORBIDDEN_MARKER", "PRIVATE_FILE_COPY"} <= codes


def test_verifier_rejects_a_scoring_attachment_copy(tmp_path: Path) -> None:
    package = _package(tmp_path)
    attachment = tmp_path / ".dig" / "scoring-inputs" / RUN_ID / "evaluation_inputs.json"
    attachment.parent.mkdir(parents=True)
    attachment.write_text('{"expected_status": "NO_INCIDENT"}\n', encoding="utf-8")
    (package / "task.json").write_text(
        json.dumps({"note": "see .dig/ for the answer"}), encoding="utf-8"
    )

    report = verify_public_package(package, project_root=tmp_path)

    assert report["clean"] is False
    assert any(finding["code"] == "FORBIDDEN_MARKER" for finding in report["findings"])


def test_builder_refuses_a_symlinked_target(tmp_path: Path) -> None:
    _write_public_run(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available for this account")
    with pytest.raises(PublicPackageError, match="PUBLIC_PACKAGE_PATH_INVALID"):
        build_public_task_package(RUN_ID, link, project_root=tmp_path)


def test_verifier_rejects_an_undeclared_task_field(tmp_path: Path) -> None:
    """Audit regression: a smuggled ``incident_case_id`` used to pass."""

    package = _package(tmp_path)
    task = json.loads((package / "task.json").read_text(encoding="utf-8"))
    task["incident_case_id"] = "required_null_order_customer_a"
    (package / "task.json").write_text(json.dumps(task), encoding="utf-8")

    report = verify_public_package(package, project_root=tmp_path)

    codes = {finding["code"] for finding in report["findings"]}
    assert report["clean"] is False
    assert "UNDECLARED_FIELD" in codes


def test_verifier_rejects_a_private_case_id_anywhere_in_the_package(tmp_path: Path) -> None:
    package = _package(tmp_path)
    scenarios = tmp_path / "config" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "required_null_order_customer_a.json").write_text("{}", encoding="utf-8")
    readme = package / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + newline_marker("Target: required_null_order_customer_a"),
        encoding="utf-8",
    )

    report = verify_public_package(package, project_root=tmp_path)

    assert report["clean"] is False
    assert any(finding["code"] == "PRIVATE_CASE_ID" for finding in report["findings"])


def test_acceptance_does_not_count_a_missing_driver_as_a_denied_write() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "isolation_acceptance",
        Path(__file__).resolve().parents[2] / "examples" / "isolation_acceptance.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    no_driver = module._evaluate(
        {
            "package_read": {"outcome": "ALLOWED"},
            "private_contract_read": {"outcome": "DENIED"},
            "scoring_attachment_read": {"outcome": "DENIED"},
            "other_run_read": {"outcome": "DENIED"},
            "management_env": {"outcome": "DENIED"},
            "db_write": {"outcome": "NO_DRIVER"},
            "injection_fixture": {"outcome": "READ"},
        }
    )
    refused = module._evaluate(
        {
            "package_read": {"outcome": "ALLOWED"},
            "private_contract_read": {"outcome": "DENIED"},
            "scoring_attachment_read": {"outcome": "DENIED"},
            "other_run_read": {"outcome": "DENIED"},
            "management_env": {"outcome": "DENIED"},
            "db_write": {"outcome": "DENIED"},
            "injection_fixture": {"outcome": "ABSENT"},
        }
    )

    assert no_driver["database_write_denied"] is False
    assert no_driver["database_write_attempted"] is False
    assert no_driver["injection_fixture_read"] is True
    assert refused["database_write_denied"] is True
    assert refused["injection_fixture_read"] is False
