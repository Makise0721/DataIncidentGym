from pathlib import Path
from subprocess import CompletedProcess

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner


def test_healthy_run_seeds_then_builds_while_incident_run_never_seeds(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    runner = DbtRunner(Settings(_env_file=None), tmp_path, fake_run)

    runner.run_healthy(tmp_path / "healthy/target", tmp_path / "healthy/logs")
    incident = runner.run_incident(
        tmp_path / "incident/target",
        tmp_path / "incident/logs",
    )

    assert [command[:2] for command in calls] == [
        ["dbt", "seed"],
        ["dbt", "build"],
        ["dbt", "build"],
    ]
    assert "--full-refresh" in calls[0]
    assert "--full-refresh" not in calls[2]
    assert calls[2][1] == "build"
    exclude_index = calls[2].index("--exclude-resource-type")
    assert calls[2][exclude_index + 1] == "seed"
    assert incident.return_code == 0


def test_incident_run_returns_redacted_nonzero_result(tmp_path: Path) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            1,
            stdout="stdout database-secret",
            stderr="stderr database-secret",
        )

    settings = Settings(_env_file=None, postgres_password="database-secret")
    result = DbtRunner(settings, tmp_path, fake_run).run_incident(
        tmp_path / "target",
        tmp_path / "logs",
    )

    assert result.return_code == 1
    assert "database-secret" not in result.stdout
    assert "database-secret" not in result.stderr
    assert "***" in result.stdout
    assert "***" in result.stderr


def test_healthy_run_rejects_nonzero_and_redacts_password(tmp_path: Path) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 17, stdout="database-secret", stderr="failed")

    settings = Settings(_env_file=None, postgres_password="database-secret")
    runner = DbtRunner(settings, tmp_path, fake_run)

    with pytest.raises(DbtExecutionError) as error:
        runner.run_healthy(tmp_path / "target", tmp_path / "logs")

    assert "加载固定 seeds" in str(error.value)
    assert "exit=17" in str(error.value)
    assert "database-secret" not in str(error.value)
