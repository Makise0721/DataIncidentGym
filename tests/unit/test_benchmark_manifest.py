from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.benchmark_manifest import (
    CONFIRMABLE_SCENARIO_IDS,
    FORMAL_SCENARIO_IDS,
    FROZEN_POLICY_STRATEGIES,
    MANIFEST_ID,
    MANIFEST_PATH,
    BenchmarkManifest,
    BenchmarkManifestError,
    ManifestModelConfiguration,
    _policy_for_strategy,
    build_manifest,
    freeze_manifest,
    generate_cells,
    load_manifest,
    manifest_path_for,
    run_id_for_cell,
    verify_manifest,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.scenarios import P1_SCENARIO_IDS


def test_manifest_has_frozen_catalog_denominator_and_schedule() -> None:
    manifest = build_manifest("a" * 40)

    assert tuple(item.incident_case_id for item in manifest.scenario_catalog) == P1_SCENARIO_IDS
    assert manifest.formal_scenario_ids == FORMAL_SCENARIO_IDS
    assert manifest.artifact_files == ARTIFACT_FILENAMES
    assert manifest.total_cells == 106
    assert manifest.model_backed_count == 94
    assert manifest.fixed_rule_count == 12
    assert manifest.strategy_counts == {
        "STATIC_SKILL": 36,
        "DIAGNOSTIC_KERNEL": 36,
        "NO_TOOL": 12,
        "KERNEL_NO_LINEAGE": 5,
        "KERNEL_NO_SCHEMA": 5,
        "FIXED_RULE": 12,
    }

    assert manifest.cells == generate_cells()
    for cell in manifest.cells:
        assert cell.run_id == run_id_for_cell(
            MANIFEST_ID,
            cell.sequence,
            cell.incident_case_id,
            cell.strategy,
            cell.repeat_index,
        )
    assert tuple(cell.incident_case_id for cell in manifest.cells[72:84]) == tuple(
        reversed(FORMAL_SCENARIO_IDS)
    )
    assert tuple(cell.incident_case_id for cell in manifest.cells[84:94]) == tuple(
        case_id for case_id in CONFIRMABLE_SCENARIO_IDS for _ in (0, 1)
    )


def test_manifest_rejects_changed_schedule_or_unknown_fields() -> None:
    manifest = build_manifest("b" * 40)
    changed = manifest.model_copy(
        update={
            "cells": manifest.cells[1:] + manifest.cells[:1],
        }
    )
    with pytest.raises(ValidationError, match="frozen schedule"):
        BenchmarkManifest.model_validate(changed.model_dump(mode="json"))

    payload = manifest.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        BenchmarkManifest.model_validate(payload)


def test_manifest_rejects_unsafe_model_configuration() -> None:
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="mimo-v2.5-pro",
            base_url="https://user:password@example.invalid/v1",
        )
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="mimo-v2.5-pro",
            base_url="https://example.invalid/v1",
            settings_overrides={"temperature": 0},
        )


def test_manifest_loader_rejects_duplicate_keys_and_freeze_is_exclusive(tmp_path: Path) -> None:
    manifest = build_manifest("c" * 40)
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text('{"schema_version":"x","schema_version":"y"}', encoding="utf-8")
    with pytest.raises(BenchmarkManifestError, match="duplicate"):
        load_manifest(duplicate_path)

    output = tmp_path / "config" / "benchmark" / "p1-formal-v1.json"
    freeze_manifest(manifest, output, project_root=tmp_path)
    assert load_manifest(output) == manifest
    with pytest.raises(BenchmarkManifestError, match="already exists"):
        freeze_manifest(manifest, output, project_root=tmp_path)


def test_manifest_freeze_rejects_a_noncanonical_output_path(tmp_path: Path) -> None:
    manifest = build_manifest("0" * 40)

    with pytest.raises(BenchmarkManifestError, match="config/benchmark/p1-formal-v1.json"):
        freeze_manifest(manifest, tmp_path / "other" / "manifest.json", project_root=tmp_path)


def test_manifest_contains_no_runtime_secrets_or_paths() -> None:
    manifest = build_manifest("d" * 40)
    text = manifest.canonical_json().lower()

    assert "api_key" not in text
    assert "password" not in text
    assert ".env" not in text
    assert "c:\\users" not in text
    assert "ground_truth" not in text


def test_manifest_verification_recomputes_result_inputs() -> None:
    manifest = build_manifest("e" * 40)
    assert verify_manifest(manifest) is manifest

    changed = manifest.model_copy(
        update={
            "result_inputs": manifest.result_inputs.model_copy(
                update={"evaluator_sha256": "f" * 64}
            )
        }
    )
    with pytest.raises(BenchmarkManifestError, match="result-input hashes"):
        verify_manifest(changed)


def test_manifest_normalizes_evaluator_line_endings_for_portable_hash() -> None:
    manifest = build_manifest("a" * 40)
    evaluator_path = PROJECT_ROOT / "src" / "data_incident_gym" / "evaluation.py"
    expected = hashlib.sha256(
        evaluator_path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()

    assert manifest.result_inputs.evaluator_sha256 == expected


def test_manifest_serialization_is_valid_json() -> None:
    manifest = build_manifest("f" * 40)
    assert json.loads(manifest.canonical_json()) == manifest.model_dump(mode="json")


def test_formal_model_strategy_count_is_not_fixed_rule() -> None:
    manifest = build_manifest("0" * 40)
    assert all(
        cell.model_backed == (cell.strategy is not DiagnosticStrategy.FIXED_RULE)
        for cell in manifest.cells
    )


def test_manifest_path_is_derived_from_manifest_id() -> None:
    assert manifest_path_for(MANIFEST_ID) == MANIFEST_PATH
    assert manifest_path_for("p1-formal-v2") == Path("config/benchmark/p1-formal-v2.json")
    assert manifest_path_for("p1-formal-v3") == Path("config/benchmark/p1-formal-v3.json")
    assert manifest_path_for("p1-formal-v4") == Path("config/benchmark/p1-formal-v4.json")
    assert manifest_path_for("p1-formal-v5") == Path("config/benchmark/p1-formal-v5.json")
    assert manifest_path_for("p1-formal-v6") == Path("config/benchmark/p1-formal-v6.json")
    assert manifest_path_for("p1-formal-v7") == Path("config/benchmark/p1-formal-v7.json")
    assert manifest_path_for("p1-formal-v8") == Path("config/benchmark/p1-formal-v8.json")
    assert manifest_path_for("p1-formal-v9") == Path("config/benchmark/p1-formal-v9.json")
    assert manifest_path_for("p1-formal-v10") == Path("config/benchmark/p1-formal-v10.json")
    assert manifest_path_for("p1-formal-v11") == Path("config/benchmark/p1-formal-v11.json")
    assert manifest_path_for("p1-formal-v12") == Path("config/benchmark/p1-formal-v12.json")
    assert manifest_path_for("p1-formal-v13") == Path("config/benchmark/p1-formal-v13.json")
    assert manifest_path_for("p1-formal-v14") == Path("config/benchmark/p1-formal-v14.json")
    assert manifest_path_for("p1-formal-v15") == Path("config/benchmark/p1-formal-v15.json")
    assert manifest_path_for("p1-formal-v16") == Path("config/benchmark/p1-formal-v16.json")
    assert manifest_path_for("p1-formal-v17") == Path("config/benchmark/p1-formal-v17.json")
    assert manifest_path_for("p1-formal-v18") == Path("config/benchmark/p1-formal-v18.json")
    assert manifest_path_for("p1-formal-v19") == Path("config/benchmark/p1-formal-v19.json")
    assert manifest_path_for("p1-formal-v20") == Path("config/benchmark/p1-formal-v20.json")
    assert manifest_path_for("p1-formal-v21") == Path("config/benchmark/p1-formal-v21.json")
    assert manifest_path_for("p1-formal-v22") == Path("config/benchmark/p1-formal-v22.json")
    assert manifest_path_for("p1-formal-v23") == Path("config/benchmark/p1-formal-v23.json")


def test_manifest_path_rejects_unversioned_identity() -> None:
    with pytest.raises(BenchmarkManifestError):
        manifest_path_for("p1-formal")
    with pytest.raises(BenchmarkManifestError):
        manifest_path_for("p2-formal-v1")
    with pytest.raises(BenchmarkManifestError):
        manifest_path_for("p1-formal-v31")


def test_build_manifest_accepts_approved_rerun_identities() -> None:
    for manifest_id in (
        "p1-formal-v2",
        "p1-formal-v3",
        "p1-formal-v4",
        "p1-formal-v5",
        "p1-formal-v6",
        "p1-formal-v7",
        "p1-formal-v8",
        "p1-formal-v9",
        "p1-formal-v10",
        "p1-formal-v11",
        "p1-formal-v12",
        "p1-formal-v13",
        "p1-formal-v14",
        "p1-formal-v15",
        "p1-formal-v16",
        "p1-formal-v17",
        "p1-formal-v18",
        "p1-formal-v19",
        "p1-formal-v20",
        "p1-formal-v21",
        "p1-formal-v22",
        "p1-formal-v23",
        "p1-formal-v24",
        "p1-formal-v25",
        "p1-formal-v26",
        "p1-formal-v27",
        "p1-formal-v28",
        "p1-formal-v29",
        "p1-formal-v30",
    ):
        manifest = build_manifest(
            "b" * 40,
            project_root=PROJECT_ROOT,
            manifest_id=manifest_id,
        )

        assert manifest.manifest_id == manifest_id
        assert manifest.total_cells == 106
        assert manifest.model_backed_count == 94


def test_build_manifest_rejects_unapproved_identity() -> None:
    with pytest.raises(BenchmarkManifestError):
        build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v31")


def test_both_approved_model_pairings_build_the_same_schedule() -> None:
    """v24 authorization: a second formal model binds through its own endpoint;
    the schedule, budget, policies and catalog stay model-independent."""

    mimo = build_manifest("b" * 40, manifest_id="p1-formal-v24")
    deepseek = build_manifest(
        "b" * 40,
        manifest_id="p1-formal-v24",
        model_name="deepseek/deepseek-v4.1-flash",
        model_base_url="https://api.commandcode.ai/provider/v1",
    )

    assert mimo.model_configuration.model == "mimo-v2.5-pro"
    assert deepseek.model_configuration.model == "deepseek/deepseek-v4.1-flash"
    assert deepseek.model_configuration.base_url == "https://api.commandcode.ai/provider/v1"
    assert deepseek.cells == mimo.cells
    assert deepseek.policies == mimo.policies
    assert deepseek.formal_scenario_ids == mimo.formal_scenario_ids
    assert deepseek.budget == mimo.budget
    assert deepseek.scenario_catalog == mimo.scenario_catalog


def test_crossed_model_endpoint_pairings_are_refused() -> None:
    with pytest.raises(BenchmarkManifestError):
        build_manifest(
            "b" * 40,
            manifest_id="p1-formal-v24",
            model_name="deepseek/deepseek-v4.1-flash",
            model_base_url="https://api.xiaomimimo.com/v1",
        )
    with pytest.raises(BenchmarkManifestError):
        build_manifest(
            "b" * 40,
            manifest_id="p1-formal-v24",
            model_name="mimo-v2.5-pro",
            model_base_url="https://api.commandcode.ai/provider/v1",
        )
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="deepseek/deepseek-v4.1-flash",
            base_url="https://api.xiaomimimo.com/v1",
        )
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="mimo-v2.5-pro",
            base_url="https://api.commandcode.ai/provider/v1",
        )


def test_unknown_formal_models_are_refused() -> None:
    with pytest.raises(BenchmarkManifestError):
        build_manifest(
            "b" * 40,
            manifest_id="p1-formal-v24",
            model_name="gpt-9",
            model_base_url="https://api.commandcode.ai/provider/v1",
        )
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="gpt-9",
            base_url="https://api.commandcode.ai/provider/v1",
        )


def test_the_sealed_v23_manifest_still_loads_under_the_pairing_table() -> None:
    """V1: the pairing table must not tighten the loading rules for manifests
    frozen before the second model existed."""

    manifest = load_manifest(PROJECT_ROOT / "config" / "benchmark" / "p1-formal-v23.json")

    assert manifest.model_configuration.model == "mimo-v2.5-pro"
    assert manifest.model_configuration.base_url == "https://api.xiaomimimo.com/v1"


def test_freeze_manifest_writes_to_id_derived_path(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v2")
    output = tmp_path / "config" / "benchmark" / "p1-formal-v2.json"

    written = freeze_manifest(manifest, output, project_root=tmp_path)

    assert written == output.resolve()
    assert json.loads(written.read_text(encoding="utf-8"))["manifest_id"] == "p1-formal-v2"


def test_freeze_manifest_rejects_path_that_disagrees_with_id(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v2")
    wrong = tmp_path / "config" / "benchmark" / "p1-formal-v1.json"

    with pytest.raises(BenchmarkManifestError):
        freeze_manifest(manifest, wrong, project_root=tmp_path)


def test_no_frozen_manifest_file_schedules_the_planner() -> None:
    """Registering the planner into ``MODEL_STRATEGIES`` must not leak into any
    frozen schedule: every on-disk manifest's cells and policies stay
    planner-free until a new identity is approved and frozen.

    Read as raw JSON on purpose — historical files do not (and must not) load
    against the evolved schema; only their recorded schedule matters here.
    """

    files = sorted((PROJECT_ROOT / "config" / "benchmark").glob("p1-formal-*.json"))
    assert files, "frozen manifest files must exist in the repository checkout"

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["manifest_id"] == path.stem
        assert [
            cell["strategy"] for cell in payload["cells"]
        ].count("EVIDENCE_PLANNER") == 0, path.name
        assert [
            policy["strategy"] for policy in payload["policies"]
        ].count("EVIDENCE_PLANNER") == 0, path.name


def test_frozen_v22_policy_surfaces_still_match_the_current_tree() -> None:
    """T13 audit guard: v2 work must not drift any v1 policy surface.

    Item-by-item comparison against the sealed manifest covers both the policy
    identity digests and the final-diagnosis schema digest of the six frozen
    strategies. A shared-schema change (for example widening a diagnosis
    vocabulary) fails here instead of silently invalidating sealed identities.
    """

    frozen = json.loads(
        (PROJECT_ROOT / "config" / "benchmark" / "p1-formal-v22.json").read_text(
            encoding="utf-8"
        )
    )
    current = [
        _policy_for_strategy(strategy).model_dump(mode="json")
        for strategy in FROZEN_POLICY_STRATEGIES
    ]

    assert current == frozen["policies"]
