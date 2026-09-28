"""CLI tests for the experiment command group (planner-comparison contract)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import data_incident_gym.cli as cli

runner = CliRunner()


def test_experiment_freeze_passes_the_experiment_pairing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_build(
        revision: str,
        *,
        manifest_id: str,
        model_name: str,
        model_base_url: str,
        project_root: object = None,
    ) -> SimpleNamespace:
        captured["revision"] = revision
        captured["manifest_id"] = manifest_id
        captured["model"] = model_name
        captured["base_url"] = model_base_url
        return SimpleNamespace(
            manifest_id=manifest_id,
            digest=lambda: "f" * 64,
        )

    monkeypatch.setattr(cli, "build_experiment_manifest", fake_build)
    monkeypatch.setattr(cli, "verify_experiment_manifest", lambda manifest, **_: manifest)
    monkeypatch.setattr(
        cli,
        "freeze_experiment_manifest",
        lambda manifest, output, **_: Path("config/benchmark") / f"{manifest.manifest_id}.json",
    )

    result = runner.invoke(
        cli.app,
        [
            "experiment",
            "freeze",
            "--manifest-id",
            "p1-planner-compare-v1",
            "--implementation-revision",
            "b" * 40,
            "--output",
            "config/benchmark/p1-planner-compare-v1.json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["manifest_id"] == "p1-planner-compare-v1"
    assert captured["model"] == "deepseek/deepseek-v4.1-flash"
    assert captured["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert "cells: 108; model_backed: 108; fixed_rule: 0" in result.output


def test_experiment_freeze_rejects_unapproved_identity() -> None:
    result = runner.invoke(
        cli.app,
        [
            "experiment",
            "freeze",
            "--manifest-id",
            "p1-planner-compare-v2",
            "--implementation-revision",
            "b" * 40,
            "--output",
            "config/benchmark/p1-planner-compare-v2.json",
        ],
    )
    assert result.exit_code == 1
    assert "approved experiment identity" in result.output


def test_experiment_verify_rejects_v1_manifest_path() -> None:
    result = runner.invoke(
        cli.app,
        [
            "experiment",
            "verify",
            "--manifest",
            "config/benchmark/p1-formal-v39.json",
        ],
    )
    assert result.exit_code == 1


def test_experiment_preflight_runs_the_planner_probe_after_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    calls: list[str] = []

    class _ProbeOutcome:
        passed = False
        observed = "MODEL_ERROR"
        transport = "transport=HTTP_502"
        detail = {"plan_step_receipts": 0}

    class _Runner:
        async def preflight(self):
            calls.append("doctor")
            return SimpleNamespace(
                result=SimpleNamespace(status=SimpleNamespace(value="PASSED")),
                model_probe_required=True,
            )

    async def fake_probe(model, identity, **kwargs):
        calls.append("probe")
        return _ProbeOutcome()

    monkeypatch.setattr(
        cli,
        "_confirmed_experiment_manifest",
        lambda path, sha: (Path("x"), SimpleNamespace(manifest_id="p1-planner-compare-v1")),
    )
    monkeypatch.setattr(cli, "create_benchmark_runner", lambda manifest: _Runner())
    monkeypatch.setattr(cli, "is_receipt_acceptable", lambda receipt: True)
    monkeypatch.setattr(cli, "_settings_bound_experiment_model", lambda loaded: (None, None))
    monkeypatch.setattr(cli, "run_planner_compatibility_probe", fake_probe)

    result = runner.invoke(
        cli.app,
        [
            "experiment",
            "preflight",
            "--manifest",
            "config/benchmark/p1-planner-compare-v1.json",
            "--confirm-sha256",
            "f" * 64,
        ],
    )

    assert result.exit_code == 1
    assert calls == ["doctor", "probe"]
    assert "PLANNER_PROBE" in result.output
