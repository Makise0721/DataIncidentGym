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
from data_incident_gym.planner_comparison_manifest import (
    build_experiment_manifest,
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


def test_runner_accepts_experiment_manifest_identity() -> None:
    runner = BenchmarkRunner(
        _experiment(),
        project_root=PROJECT_ROOT,
        doctor_factory=_Dummy,
        evaluation_runner_factory=_Dummy,
        artifact_writer=_Dummy,  # type: ignore[arg-type]
    )
    assert runner._manifest.manifest_id == "p1-planner-compare-v1"


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
