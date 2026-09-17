"""T11 closed-loop regressions: the isolated client cannot bypass the session.

The bridge keeps the only ``StrategySession`` in the harness process while the
strategy runs as a separate process — here the same client the container runs,
started locally so the suite needs no Docker and no database. The loop covers
MCP transport over the bridge, harness-owned counting and evidence
registration, the submission-file relay, and every refusal path the audit asked
for: forged citation, deleted client log, over-budget call, duplicate submission
and a call after the terminal.

The local configuration cannot deny the repository, so the boundary checks are
recorded (and asserted to be *not* isolated) instead of gating the loop verdict;
the container run in ``examples/isolation_acceptance.py`` is what gates it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from data_incident_gym.public_package import build_public_task_package

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
ACCEPTANCE_PATH = EXAMPLES / "isolation_acceptance.py"


def _acceptance() -> object:
    spec = importlib.util.spec_from_file_location("isolation_acceptance", ACCEPTANCE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def acceptance() -> object:
    return _acceptance()


@pytest.fixture(scope="module")
def loop(acceptance: object, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run the compliant and the attack client once, locally, behind the bridge."""

    root = tmp_path_factory.mktemp("isolation-loop")
    project_root = root / "project"
    sandbox = root / "sandbox"
    sandbox.mkdir()
    acceptance._write_synthetic_run(project_root)  # noqa: SLF001 - fixture builder
    build_public_task_package(acceptance.RUN_ID, sandbox / "package", project_root=project_root)
    shutil.copyfile(EXAMPLES / "isolated_client.py", sandbox / "isolated_client.py")
    (sandbox / "injection_fixture.txt").write_text(
        acceptance.INJECTION_FIXTURE, encoding="utf-8"
    )
    log_dir = root / "harness"
    results = {
        mode: acceptance.run_closed_loop_mode(
            sandbox,
            project_root,
            mode=mode,
            command=acceptance.local_client_command(sys.executable, sandbox, mode),
            log_dir=log_dir,
            env=acceptance._filtered_env(),
            require_boundary=False,
        )
        for mode in acceptance.LOOP_MODES
    }
    return {**results, "sandbox": sandbox, "log_dir": log_dir}


def test_compliant_client_completes_the_closed_loop(loop: dict) -> None:
    result = loop["compliant"]
    checks = result["checks"]

    assert result["failed_checks"] == []
    assert result["verified"] is True
    assert checks["terminal_status"] == "CONFIRMED"
    assert checks["counters"] == {
        "tool_call_attempts": 5,
        "registered_evidence": 5,
        "output_retries_used": 0,
        "model_requests_granted": 0,
    }
    assert checks["accepted_sequence"] == [True] * 5
    # The client's own log agrees with the harness log, call by call.
    assert checks["client_audit_matches"] is True
    assert checks["client_exit_zero"] is True


def test_attack_client_is_refused_at_every_point(loop: dict) -> None:
    result = loop["attack"]
    checks = result["checks"]

    assert result["failed_checks"] == []
    assert result["verified"] is True
    # Budget: eight counted calls, the ninth refused by the harness.
    assert checks["counters"]["tool_call_attempts"] == 9
    assert checks["counters"]["registered_evidence"] == 5
    assert checks["accepted_sequence"] == [True] * 4 + [False] + [True] * 3 + [False]
    assert checks["refusal_code_sequence"][4] == "RELATION_NOT_ALLOWED"
    assert checks["refusal_code_sequence"][8] == "TOOL_BUDGET_EXHAUSTED"
    # The undeclared tool is an MCP-level error: no receipt, no attempt.
    assert checks["undeclared_tool_not_counted"] is True
    # Forged citation, smuggled fields, duplicate submission, post-terminal call.
    assert checks["forged_citation_refused"] is True
    assert checks["smuggled_file_refused"] is True
    assert checks["smuggled_extra_fields"] == ["expected_status", "incident_case_id", "scoring"]
    assert checks["smuggled_did_not_touch_session"] is True
    assert checks["duplicate_refused"] is True
    assert checks["post_terminal_call_refused"] is True
    # The valid submission still lands after the refusals, on the same session.
    assert checks["terminal_status"] == "CONFIRMED"
    assert checks["counters"]["output_retries_used"] == 1


def test_deleting_the_client_log_does_not_hide_what_the_harness_counted(loop: dict) -> None:
    result = loop["attack"]
    checks = result["checks"]

    assert checks["client_log_deleted"] is True
    assert checks["harness_log_complete"] is True
    assert checks["client_cannot_reach_harness_log"] is True
    assert checks["false_self_report_ignored"] is True
    log_path = Path(result["harness_log"])
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["sequence"] for entry in entries] == list(range(1, 10))
    assert sum(entry["receipt"]["accepted"] for entry in entries) == 7
    assert result["session"]["tool_call_attempts"] == len(entries)
    # The authoritative log is not inside the sandbox the strategy can write.
    assert loop["sandbox"] not in log_path.parents
    assert loop["sandbox"] not in loop["log_dir"].parents


def test_local_configuration_reports_the_repository_as_reachable(loop: dict) -> None:
    """The local run is a trusted development setup; the loop says so honestly."""

    checks = loop["attack"]["checks"]
    report = json.loads(
        (loop["sandbox"] / "loop" / "attack" / "attack_report.json").read_text(encoding="utf-8")
    )

    assert checks["privileged_recorded"] is True
    assert checks["privileged_denied"] is False
    assert report["privileged_attempts"]["private_contract"]["outcome"] == "ALLOWED"
    assert report["privileged_attempts"]["management_env"]["outcome"] == "DENIED"
    assert "privileged_denied" not in loop["attack"]["required_checks"]
