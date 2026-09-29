"""Contract tests for the independent planner-comparison experiment manifest
(p1.planner_comparison_manifest.v1, namespace p1-planner-compare-vN)."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_incident_gym.benchmark_manifest import (
    FORMAL_SCENARIO_IDS,
    BenchmarkManifestError,
    load_manifest,
)
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.planner_comparison_manifest import (
    APPROVED_EXPERIMENT_IDS,
    EXPERIMENT_MANIFEST_ID_V2,
    EXPERIMENT_SCHEMA_VERSION,
    EXPERIMENT_SCHEMA_VERSION_V2,
    EXPERIMENT_STRATEGIES,
    PlannerComparisonManifest,
    PlannerComparisonManifestV2,
    build_experiment_manifest,
    build_experiment_manifest_v2,
    experiment_manifest_path_for,
    freeze_experiment_manifest,
    generate_experiment_cells,
    load_experiment_manifest,
    verify_experiment_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_experiment_registry_pattern_and_path() -> None:
    assert APPROVED_EXPERIMENT_IDS == ("p1-planner-compare-v1", "p1-planner-compare-v2")
    assert experiment_manifest_path_for("p1-planner-compare-v1") == Path(
        "config/benchmark/p1-planner-compare-v1.json"
    )
    assert experiment_manifest_path_for(EXPERIMENT_MANIFEST_ID_V2) == Path(
        "config/benchmark/p1-planner-compare-v2.json"
    )
    with pytest.raises(BenchmarkManifestError):
        experiment_manifest_path_for("p1-formal-v39")
    with pytest.raises(BenchmarkManifestError):
        experiment_manifest_path_for("p1-planner-compare")
    with pytest.raises(BenchmarkManifestError):
        experiment_manifest_path_for("p2-planner-compare-v1")


def test_build_experiment_manifest_freezes_three_surfaces_and_108_cells() -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)

    assert isinstance(manifest, PlannerComparisonManifest)
    assert manifest.schema_version == EXPERIMENT_SCHEMA_VERSION
    assert manifest.manifest_id == "p1-planner-compare-v1"
    assert manifest.implementation_revision == "a" * 40
    strategies = tuple(policy.strategy for policy in manifest.policies)
    assert strategies == EXPERIMENT_STRATEGIES
    assert EXPERIMENT_STRATEGIES == (
        DiagnosticStrategy.EVIDENCE_PLANNER,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        DiagnosticStrategy.STATIC_SKILL,
    )
    assert manifest.total_cells == 108
    assert manifest.model_backed_count == 108
    assert manifest.fixed_rule_count == 0
    by_strategy = {
        strategy: sum(cell.strategy is strategy for cell in manifest.cells)
        for strategy in EXPERIMENT_STRATEGIES
    }
    assert by_strategy == {
        DiagnosticStrategy.EVIDENCE_PLANNER: 36,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL: 36,
        DiagnosticStrategy.STATIC_SKILL: 36,
    }
    assert len({cell.run_id for cell in manifest.cells}) == 108
    assert all(cell.model_backed for cell in manifest.cells)
    assert manifest.cells == generate_experiment_cells("p1-planner-compare-v1")

    policies = {policy.strategy: policy for policy in manifest.policies}
    assert set(policies[DiagnosticStrategy.EVIDENCE_PLANNER].tool_names) == {
        "plan_step",
        "close_obligation",
        "submit_diagnosis",
    }
    planner_identity = policies[DiagnosticStrategy.EVIDENCE_PLANNER].policy_identity
    kernel_identity = policies[DiagnosticStrategy.DIAGNOSTIC_KERNEL].policy_identity
    static_identity = policies[DiagnosticStrategy.STATIC_SKILL].policy_identity
    assert planner_identity.strategy_prompt_version == "p1.planner.v2"
    assert kernel_identity.strategy_prompt_version == "p1.kernel.v18"
    assert static_identity.strategy_prompt_version == "p1.static.v5"

    assert planner_identity.controller_protocol_version == "p1.planner_controller.v2"
    assert kernel_identity.controller_protocol_version == "p1.controller.v22"
    assert static_identity.controller_protocol_version == "p1.controller.v22"

    planner_keys = {
        (cell.incident_case_id, cell.repeat_index)
        for cell in manifest.cells
        if cell.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    }
    kernel_keys = {
        (cell.incident_case_id, cell.repeat_index)
        for cell in manifest.cells
        if cell.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL
    }
    assert len(planner_keys) == 36
    assert planner_keys == kernel_keys
    assert {case for case, _ in planner_keys} == set(FORMAL_SCENARIO_IDS)

    assert manifest.model_configuration.model == "deepseek/deepseek-v4.1-flash"
    assert manifest.budget.model_request_limit == 8
    assert manifest.budget.tool_call_limit == 8
    assert manifest.budget.output_retry_limit == 2
    assert manifest.budget.timeout_seconds == 300


def test_experiment_schedule_rotation_and_displacement() -> None:
    cells = generate_experiment_cells("p1-planner-compare-v1")
    rotation = {
        1: (
            DiagnosticStrategy.EVIDENCE_PLANNER,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.STATIC_SKILL,
        ),
        2: (
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.STATIC_SKILL,
            DiagnosticStrategy.EVIDENCE_PLANNER,
        ),
        3: (
            DiagnosticStrategy.STATIC_SKILL,
            DiagnosticStrategy.EVIDENCE_PLANNER,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        ),
    }
    shifts = {1: 0, 2: 4, 3: 8}
    position_counts = {
        strategy: {0: 0, 1: 0, 2: 0} for strategy in EXPERIMENT_STRATEGIES
    }
    for repeat_index in (1, 2, 3):
        shift = shifts[repeat_index]
        rotated = FORMAL_SCENARIO_IDS[shift:] + FORMAL_SCENARIO_IDS[:shift]
        block = [cell for cell in cells if cell.repeat_index == repeat_index]
        assert len(block) == 36
        for incident_case_id in rotated:
            group = [
                cell
                for cell in block
                if cell.incident_case_id == incident_case_id
            ]
            assert len(group) == 3
            assert [cell.strategy for cell in group] == list(rotation[repeat_index])
            for within, cell in enumerate(group):
                position_counts[cell.strategy][within] += 1
    for strategy, counts in position_counts.items():
        assert counts == {0: 12, 1: 12, 2: 12}, strategy


def test_experiment_manifest_rejects_drift() -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    payload = manifest.model_dump(mode="json")

    reordered = {**payload, "policies": list(reversed(payload["policies"]))}
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(reordered)

    replaced = {
        **payload,
        "cells": [
            {
                **cell,
                "strategy": (
                    DiagnosticStrategy.STATIC_SKILL.value
                    if cell["strategy"] == DiagnosticStrategy.EVIDENCE_PLANNER.value
                    else cell["strategy"]
                ),
            }
            for cell in payload["cells"]
        ],
    }
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(replaced)

    dropped = {**payload, "cells": payload["cells"][:-1]}
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(dropped)

    wrong_formal = {
        **payload,
        "formal_scenario_ids": list(reversed(payload["formal_scenario_ids"])),
    }
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(wrong_formal)

    foreign_scenario = {
        **payload,
        "cells": [
            {**cell, "incident_case_id": "required_null_payment_id"}
            if index == 0
            else cell
            for index, cell in enumerate(payload["cells"])
        ],
    }
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(foreign_scenario)


def test_experiment_loader_round_trip_and_cross_version_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = build_experiment_manifest("b" * 40, project_root=PROJECT_ROOT)
    target = tmp_path / "p1-planner-compare-v1.json"
    target.write_text(manifest.canonical_json(), encoding="utf-8")

    loaded = load_experiment_manifest(target)
    assert loaded == manifest

    renamed = tmp_path / "renamed.json"
    renamed.write_text(manifest.canonical_json(), encoding="utf-8")
    with pytest.raises(BenchmarkManifestError):
        load_experiment_manifest(renamed)

    duplicated = tmp_path / "p1-planner-compare-v1.json.duplicate"
    duplicated.write_text(
        manifest.canonical_json().replace(
            '"manifest_id": "p1-planner-compare-v1",',
            '"manifest_id": "p1-planner-compare-v1", "manifest_id": "p1-planner-compare-v1",',
        ),
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkManifestError):
        load_experiment_manifest(duplicated)

    with pytest.raises(BenchmarkManifestError):
        load_manifest(target)

    with pytest.raises(BenchmarkManifestError):
        load_experiment_manifest(PROJECT_ROOT / "config" / "benchmark" / "p1-formal-v39.json")


def test_v2_manifest_builder_and_loader_bind_a_distinct_identity(
    tmp_path: Path,
) -> None:
    manifest = build_experiment_manifest_v2(
        "d" * 40,
        "e" * 64,
        project_root=PROJECT_ROOT,
    )
    assert isinstance(manifest, PlannerComparisonManifestV2)
    assert manifest.schema_version == EXPERIMENT_SCHEMA_VERSION_V2
    assert manifest.manifest_id == EXPERIMENT_MANIFEST_ID_V2
    assert manifest.admission_attestation_sha256 == "e" * 64
    assert manifest.total_cells == 108
    assert manifest.cells == generate_experiment_cells(EXPERIMENT_MANIFEST_ID_V2)

    path = tmp_path / f"{EXPERIMENT_MANIFEST_ID_V2}.json"
    path.write_text(manifest.canonical_json(), encoding="utf-8")
    assert load_experiment_manifest(path) == manifest

    v1_payload = build_experiment_manifest("d" * 40, project_root=PROJECT_ROOT).model_dump(
        mode="json"
    )
    wrong_v1_pair = {**v1_payload, "manifest_id": EXPERIMENT_MANIFEST_ID_V2}
    with pytest.raises(ValidationError):
        PlannerComparisonManifest.model_validate(wrong_v1_pair)

    wrong_v2_pair = {**manifest.model_dump(mode="json"), "manifest_id": "p1-planner-compare-v1"}
    with pytest.raises(ValidationError):
        PlannerComparisonManifestV2.model_validate(wrong_v2_pair)

    missing_attestation = manifest.model_dump(mode="json")
    del missing_attestation["admission_attestation_sha256"]
    with pytest.raises(ValidationError):
        PlannerComparisonManifestV2.model_validate(missing_attestation)


def test_v1_loader_rejects_admission_attestation_field(tmp_path: Path) -> None:
    manifest = build_experiment_manifest("d" * 40, project_root=PROJECT_ROOT)
    payload = manifest.model_dump(mode="json")
    payload["admission_attestation_sha256"] = "e" * 64
    path = tmp_path / "p1-planner-compare-v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkManifestError):
        load_experiment_manifest(path)


def test_experiment_manifest_loader_rejects_nonfinite_json_constants(
    tmp_path: Path,
) -> None:
    path = tmp_path / "p1-planner-compare-v1.json"
    path.write_text('{"schema_version":"p1.planner_comparison_manifest.v1", "x":NaN}')

    with pytest.raises(BenchmarkManifestError):
        load_experiment_manifest(path)


def test_frozen_v1_bytes_stay_unchanged_and_the_new_policy_source_declares_drift() -> None:
    """The frozen v1 manifest is history: its bytes never change, and since the
    planner policy advanced to p1.planner.v2 the current source must refuse to
    re-verify it instead of silently accepting a policy it no longer ships."""

    path = PROJECT_ROOT / "config" / "benchmark" / "p1-planner-compare-v1.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "e4a09d16dd15b5b4433b5a25d1e5b8544a4b8c341cb97b793853fcd1d0378826"
    )
    manifest = load_experiment_manifest(path)
    with pytest.raises(BenchmarkManifestError, match="policy identity drifted"):
        verify_experiment_manifest(manifest, project_root=PROJECT_ROOT)


def test_v2_verification_dispatches_to_current_admission_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import data_incident_gym.planner_comparison_manifest as manifest_module

    manifest = build_experiment_manifest_v2(
        "d" * 40,
        "f" * 64,
        project_root=PROJECT_ROOT,
    )
    captured: dict[str, object] = {}

    def fake_verify(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(manifest_module, "verify_admission_attestation", fake_verify)
    assert verify_experiment_manifest(manifest, project_root=PROJECT_ROOT) is manifest
    assert captured == {
        "expected_sha256": "f" * 64,
        "formal_scenario_ids": FORMAL_SCENARIO_IDS,
        "project_root": PROJECT_ROOT,
    }


def test_proof_only_v2_freeze_fails_without_private_source_report() -> None:
    manifest = build_experiment_manifest_v2(
        "d" * 40,
        "f" * 64,
        project_root=PROJECT_ROOT,
    )

    with pytest.raises(BenchmarkManifestError, match="requires --admission-report"):
        freeze_experiment_manifest(
            manifest,
            Path("config/benchmark/p1-planner-compare-v2.json"),
            project_root=PROJECT_ROOT,
        )


def test_verify_experiment_manifest_recomputes_and_rejects_drift() -> None:
    manifest = build_experiment_manifest("c" * 40, project_root=PROJECT_ROOT)
    assert verify_experiment_manifest(manifest, project_root=PROJECT_ROOT) is manifest

    drifted_inputs = manifest.model_copy(
        update={
            "result_inputs": manifest.result_inputs.model_copy(
                update={"evaluator_sha256": "d" * 64}
            )
        }
    )
    with pytest.raises(BenchmarkManifestError):
        verify_experiment_manifest(drifted_inputs, project_root=PROJECT_ROOT)

    drifted_policies = manifest.model_copy(update={"policies": ()})
    with pytest.raises(BenchmarkManifestError):
        verify_experiment_manifest(drifted_policies, project_root=PROJECT_ROOT)


def test_freeze_experiment_manifest_is_exclusive_and_path_bound(
    tmp_path: Path,
) -> None:
    manifest = build_experiment_manifest("f" * 40, project_root=PROJECT_ROOT)
    wrong = tmp_path / "elsewhere.json"
    with pytest.raises(BenchmarkManifestError):
        freeze_experiment_manifest(manifest, wrong, project_root=tmp_path)

    right = tmp_path / "config" / "benchmark" / "p1-planner-compare-v1.json"
    right.parent.mkdir(parents=True)
    path = freeze_experiment_manifest(manifest, right, project_root=tmp_path)
    assert path == right
    assert right.read_text(encoding="utf-8") == manifest.canonical_json()
    with pytest.raises(BenchmarkManifestError):
        freeze_experiment_manifest(manifest, right, project_root=tmp_path)
