import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner


def test_healthy_run_seeds_then_builds_while_incident_run_never_seeds(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    settings = Settings(_env_file=None, postgres_password="runner-secret")

    def fake_run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    runner = DbtRunner(settings, tmp_path, fake_run)

    runner.run_healthy(tmp_path / "healthy/target", tmp_path / "healthy/logs")
    incident = runner.run_incident(
        tmp_path / "incident/target",
        tmp_path / "incident/logs",
    )

    commands = [command for command, _ in calls]
    assert [command[:2] for command in commands] == [
        ["dbt", "seed"],
        ["dbt", "build"],
        ["dbt", "build"],
    ]
    assert commands[0] == [
        "dbt",
        "seed",
        "--project-dir",
        str(tmp_path / "third_party" / "jaffle_shop"),
        "--profiles-dir",
        str(tmp_path / "config" / "dbt"),
        "--target",
        "dev",
        "--target-path",
        str(tmp_path / "healthy/target"),
        "--log-path",
        str(tmp_path / "healthy/logs"),
        "--no-use-colors",
        "--full-refresh",
    ]
    assert commands[1] == [
        "dbt",
        "build",
        "--project-dir",
        str(tmp_path / "third_party" / "jaffle_shop"),
        "--profiles-dir",
        str(tmp_path / "config" / "dbt"),
        "--target",
        "dev",
        "--target-path",
        str(tmp_path / "healthy/target"),
        "--log-path",
        str(tmp_path / "healthy/logs"),
        "--no-use-colors",
    ]
    assert commands[2] == [
        "dbt",
        "build",
        "--project-dir",
        str(tmp_path / "third_party" / "jaffle_shop"),
        "--profiles-dir",
        str(tmp_path / "config" / "dbt"),
        "--target",
        "dev",
        "--target-path",
        str(tmp_path / "incident/target"),
        "--log-path",
        str(tmp_path / "incident/logs"),
        "--no-use-colors",
        "--exclude-resource-type",
        "seed",
    ]
    for _, kwargs in calls:
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == settings.command_timeout_seconds
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["DIG_POSTGRES_PASSWORD"] == "runner-secret"
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


@pytest.mark.parametrize(
    "failure",
    [
        OSError("failed with database-secret"),
        subprocess.TimeoutExpired(
            "dbt",
            1,
            output="database-secret",
            stderr="database-secret",
        ),
    ],
)
def test_execution_failures_do_not_retain_unredacted_cause(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        raise failure

    settings = Settings(_env_file=None, postgres_password="database-secret")

    with pytest.raises(DbtExecutionError) as error:
        DbtRunner(settings, tmp_path, fake_run).run_incident(
            tmp_path / "target",
            tmp_path / "logs",
        )

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "database-secret" not in str(error.value)
