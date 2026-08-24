import json
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from data_incident_gym.baseline import BaselineBuilder, BaselineError
from data_incident_gym.config import Settings


def test_build_runs_compose_seed_then_dbt_build_without_shell(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    settings = Settings(_env_file=None, postgres_password="command-secret")

    def fake_run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    builder = BaselineBuilder(
        settings=settings,
        project_root=tmp_path,
        run_command=fake_run,
    )
    builder.start_postgres()
    builder.run_dbt()

    commands = [command for command, _ in calls]
    assert commands[0][:3] == ["docker", "compose", "-f"]
    assert commands[0][-4:] == ["up", "-d", "--wait", "postgres"]
    assert commands[1][0:2] == ["dbt", "seed"]
    assert "--full-refresh" in commands[1]
    assert commands[2][0:2] == ["dbt", "build"]
    assert all(kwargs.get("shell") is not True for _, kwargs in calls)
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in calls)
    assert all(kwargs["timeout"] == settings.command_timeout_seconds for _, kwargs in calls)
    assert all(kwargs["text"] is True for _, kwargs in calls)
    assert all(kwargs["encoding"] == "utf-8" for _, kwargs in calls)
    assert all(kwargs["errors"] == "replace" for _, kwargs in calls)
    assert all(
        "command-secret" not in argument for command in commands for argument in command
    )
    for _, kwargs in calls:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["DIG_POSTGRES_PASSWORD"] == "command-secret"

    for command in commands[1:]:
        assert str(tmp_path / ".dig" / "dbt" / "target") in command
        assert str(tmp_path / ".dig" / "dbt" / "logs") in command
    assert commands[1][-1] == "--full-refresh"
    assert commands[2][-1] == "--no-use-colors"


def test_nonzero_command_is_wrapped_and_password_is_redacted(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, postgres_password="secret-password")

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            17,
            stdout="stdout secret-password",
            stderr="stderr secret-password",
        )

    builder = BaselineBuilder(settings, tmp_path, fake_run)

    with pytest.raises(BaselineError) as error:
        builder.start_postgres()

    message = str(error.value)
    assert "启动 PostgreSQL" in message
    assert "exit=17" in message
    assert "secret-password" not in message
    assert "***" in message


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("dbt", 3), OSError("not found")])
def test_command_execution_failures_are_wrapped(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        raise failure

    builder = BaselineBuilder(Settings(_env_file=None), tmp_path, fake_run)

    with pytest.raises(BaselineError, match="执行"):
        builder.start_postgres()


@pytest.mark.parametrize(
    "missing_name",
    ["manifest.json", "run_results.json", "dbt.log"],
)
def test_validate_dbt_artifacts_rejects_missing_files(
    tmp_path: Path,
    missing_name: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    target = builder.dbt_target
    logs = builder.dbt_logs
    target.mkdir(parents=True)
    logs.mkdir(parents=True)

    (target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (logs / "dbt.log").write_text("log", encoding="utf-8")
    (target / missing_name).unlink(missing_ok=True)
    (logs / missing_name).unlink(missing_ok=True)

    with pytest.raises(BaselineError, match="缺少 dbt artifact"):
        builder.validate_dbt_artifacts()


def test_validate_dbt_artifacts_rejects_empty_results(tmp_path: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": []}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="results 不能为空"):
        builder.validate_dbt_artifacts()


@pytest.mark.parametrize("status", ["error", "fail", "skipped", "warn", "unknown"])
def test_validate_dbt_artifacts_rejects_non_success_status(
    tmp_path: Path,
    status: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": status}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="非法 dbt status"):
        builder.validate_dbt_artifacts()


@pytest.mark.parametrize("empty_name", ["manifest.json", "run_results.json", "dbt.log"])
def test_validate_dbt_artifacts_rejects_empty_files(
    tmp_path: Path,
    empty_name: str,
) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text('{"nodes": {}}', encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")
    target = builder.dbt_target / empty_name
    log = builder.dbt_logs / empty_name
    (log if empty_name == "dbt.log" else target).write_text("", encoding="utf-8")

    with pytest.raises(BaselineError, match="为空"):
        builder.validate_dbt_artifacts()


def test_validate_dbt_artifacts_rejects_invalid_manifest(tmp_path: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), tmp_path)
    builder.dbt_target.mkdir(parents=True)
    builder.dbt_logs.mkdir(parents=True)
    (builder.dbt_target / "manifest.json").write_text("not-json", encoding="utf-8")
    (builder.dbt_target / "run_results.json").write_text(
        json.dumps({"results": [{"status": "success"}]}),
        encoding="utf-8",
    )
    (builder.dbt_logs / "dbt.log").write_text("log", encoding="utf-8")

    with pytest.raises(BaselineError, match="无法解析 dbt artifact"):
        builder.validate_dbt_artifacts()
