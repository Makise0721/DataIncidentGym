"""Narrow runner adaptation for the planner-comparison experiment manifest.

The v1 formal behavior (six-strategy registry, verify, checkout path) must be
byte-for-byte unchanged; the experiment contract dispatches to its own
registry, verifier, and canonical path.
"""

from pathlib import Path

import pytest

from data_incident_gym.benchmark_manifest import BenchmarkManifestError, build_manifest
from data_incident_gym.benchmark_runner import (
    BenchmarkRunner,
    BenchmarkRunnerError,
    _manifest_identity_approved,
    _manifest_relpath_for,
    _verify_manifest_for,
)
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.doctor import DoctorCheckCode, DoctorResult, DoctorRunner, DoctorStatus
from data_incident_gym.planner_comparison_manifest import (
    build_experiment_manifest,
    build_experiment_manifest_v2,
)
from data_incident_gym.planner_probe import PlannerProbeResult
from data_incident_gym.planner_probe_receipt import (
    PLANNER_PROBE_RECEIPT_FILENAME,
    PlannerProbeReceiptError,
    load_planner_probe_receipt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Dummy:
    pass


def _experiment() -> object:
    return build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)


def _v1() -> object:
    # Built from the current tree: frozen v39/v42 bind the retired v19 policy
    # surface, so loading them here would trip drift protection by design.
    return build_manifest("a" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v39")


def test_identity_approval_dispatches_by_schema() -> None:
    assert _manifest_identity_approved(_v1())
    assert _manifest_identity_approved(_experiment())
    assert _manifest_identity_approved(
        build_experiment_manifest_v2("a" * 40, "b" * 64, project_root=PROJECT_ROOT)
    )

    stranger = _experiment().model_copy(update={"manifest_id": "p1-planner-compare-v9"})
    assert not _manifest_identity_approved(stranger)
    with pytest.raises(BenchmarkRunnerError):
        BenchmarkRunner(
            stranger,
            project_root=PROJECT_ROOT,
            doctor_factory=_Dummy,
            evaluation_runner_factory=_Dummy,
            artifact_writer=_Dummy,  # type: ignore[arg-type]
        )

    forged_base = _experiment().model_copy(update={"manifest_id": "p1-planner-compare-v2"})
    assert not _manifest_identity_approved(forged_base)
    with pytest.raises(BenchmarkRunnerError):
        BenchmarkRunner(
            forged_base,
            project_root=PROJECT_ROOT,
            doctor_factory=_Dummy,
            evaluation_runner_factory=_Dummy,
            artifact_writer=_Dummy,  # type: ignore[arg-type]
        )


def test_runner_accepts_experiment_manifest_identity() -> None:
    runner = BenchmarkRunner(
        _experiment(),
        project_root=PROJECT_ROOT,
        doctor_factory=_Dummy,
        evaluation_runner_factory=_Dummy,
        artifact_writer=_Dummy,  # type: ignore[arg-type]
    )
    assert runner._manifest.manifest_id == "p1-planner-compare-v1"


def _doctor_result() -> DoctorResult:
    checks = tuple(DoctorRunner._check(code, True, "OK") for code in DoctorCheckCode)
    return DoctorResult(status=DoctorStatus.PASSED, checks=checks)


class _Doctor:
    def run(self) -> DoctorResult:
        return _doctor_result()


def _runner_with_probe(
    tmp_path: Path,
    manifest,
    *,
    probe_factory=None,
) -> BenchmarkRunner:
    return BenchmarkRunner(
        manifest,
        project_root=tmp_path,
        doctor_factory=_Doctor,
        evaluation_runner_factory=lambda: pytest.fail("cells must not start in this test"),
        artifact_writer=_Dummy(),  # type: ignore[arg-type]
        checkout_verifier=lambda _manifest: None,
        checkout_revision_reader=lambda: "b" * 40,
        planner_probe_factory=probe_factory,
    )


def _passing_probe_factory(manifest, calls: list[str] | None = None):
    async def factory():
        if calls is not None:
            calls.append("probe")
        return (
            ModelIdentity(
                provider=manifest.model_configuration.provider,
                model=manifest.model_configuration.model,
            ),
            PlannerProbeResult(
                passed=True,
                observed="PLAN_LOOP_COMPLETED",
                detail={"plan_step_receipts": 1},
            ),
        )

    return factory


def test_experiment_runner_requires_probe_before_preflight_or_run(tmp_path: Path) -> None:
    manifest = _experiment()
    runner = _runner_with_probe(tmp_path, manifest)

    with pytest.raises(BenchmarkRunnerError, match="dedicated compatibility probe"):
        import asyncio

        asyncio.run(runner.preflight())
    with pytest.raises(BenchmarkRunnerError, match="dedicated compatibility probe"):
        import asyncio

        asyncio.run(runner.run())
    assert not (tmp_path / "artifacts").exists()


def test_experiment_preflight_seals_probe_and_run_rejects_tampering(
    tmp_path: Path,
) -> None:
    import asyncio

    manifest = _experiment()
    calls: list[str] = []
    runner = _runner_with_probe(
        tmp_path,
        manifest,
        probe_factory=_passing_probe_factory(manifest, calls),
    )

    asyncio.run(runner.preflight())
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    receipt_path = suite_root / PLANNER_PROBE_RECEIPT_FILENAME
    receipt = load_planner_probe_receipt(
        receipt_path,
        manifest,
        checkout_revision="b" * 40,
    )
    assert receipt.passed is True
    assert receipt.scope_cell_count == 108
    assert calls == ["probe"]

    receipt_path.write_text(receipt_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BenchmarkRunnerError, match="tampered"):
        asyncio.run(runner.run())
    assert not (suite_root / "subset.json").exists()


def test_failed_probe_receipt_is_preserved_and_blocks_run(tmp_path: Path) -> None:
    import asyncio

    manifest = _experiment()

    async def failed_factory():
        return (
            ModelIdentity("wrong-provider", manifest.model_configuration.model),
            PlannerProbeResult(
                passed=True,
                observed="PLAN_LOOP_COMPLETED",
                detail={"plan_step_receipts": 1},
            ),
        )

    runner = _runner_with_probe(tmp_path, manifest, probe_factory=failed_factory)
    with pytest.raises(BenchmarkRunnerError, match="probe failed"):
        asyncio.run(runner.preflight())

    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    receipt_path = suite_root / PLANNER_PROBE_RECEIPT_FILENAME
    assert receipt_path.is_file()
    with pytest.raises(PlannerProbeReceiptError, match="identity or scope"):
        load_planner_probe_receipt(
            receipt_path,
            manifest,
            checkout_revision="b" * 40,
        )
    with pytest.raises(BenchmarkRunnerError):
        asyncio.run(runner.run())
    assert not (suite_root / "subset.json").exists()


def test_verify_and_path_dispatch_by_schema() -> None:
    experiment = _experiment()
    v1 = _v1()

    assert _manifest_relpath_for(experiment) == Path(
        "config/benchmark/p1-planner-compare-v1.json"
    )
    assert _manifest_relpath_for(v1) == Path("config/benchmark/p1-formal-v39.json")

    assert _verify_manifest_for(experiment, project_root=PROJECT_ROOT) is experiment
    assert _verify_manifest_for(v1, project_root=PROJECT_ROOT) is v1

    drifted = experiment.model_copy(update={"policies": ()})
    with pytest.raises(BenchmarkManifestError):
        _verify_manifest_for(drifted, project_root=PROJECT_ROOT)


def test_direct_v2_runner_paths_recheck_admission_identity() -> None:
    import asyncio

    manifest = build_experiment_manifest_v2(
        "a" * 40,
        "f" * 64,
        project_root=PROJECT_ROOT,
    )
    calls: list[str] = []
    runner = BenchmarkRunner(
        manifest,
        project_root=PROJECT_ROOT,
        doctor_factory=_Dummy,
        evaluation_runner_factory=lambda: pytest.fail("proof failure must precede cell start"),
        artifact_writer=_Dummy(),  # type: ignore[arg-type]
        checkout_verifier=lambda _manifest: pytest.fail(
            "v2 admission verification must run even with an injected checkout verifier"
        ),
        checkout_revision_reader=lambda: "b" * 40,
        planner_probe_factory=_passing_probe_factory(manifest, calls),
    )

    with pytest.raises(BenchmarkManifestError, match="admission attestation"):
        asyncio.run(runner.preflight())
    with pytest.raises(BenchmarkManifestError, match="admission attestation"):
        asyncio.run(runner.run())
    assert calls == []
