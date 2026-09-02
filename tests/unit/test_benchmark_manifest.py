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
    MANIFEST_ID,
    BenchmarkManifest,
    BenchmarkManifestError,
    ManifestModelConfiguration,
    build_manifest,
    freeze_manifest,
    generate_cells,
    load_manifest,
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
            model="mimo-v2.5",
            base_url="https://user:password@example.invalid/v1",
        )
    with pytest.raises(ValidationError):
        ManifestModelConfiguration(
            provider="openai-compatible",
            model="mimo-v2.5",
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
