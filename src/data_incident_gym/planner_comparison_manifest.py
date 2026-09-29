"""Independent planner-comparison experiment manifest contracts
(p1.planner_comparison_manifest.v1/v2, namespace p1-planner-compare-vN).

Pre-registered by docs/superpowers/plans/2026-09-28-t12-planner-real-model-
measurement-proposal.md §2-§3: three policy surfaces (EVIDENCE_PLANNER,
DIAGNOSTIC_KERNEL, STATIC_SKILL) over the 12 formal dev scenarios x 3 repeats
= 108 model-backed cells; planner-vs-kernel 36 pairs keyed (case, repeat).
The v1 formal contract (six strategies / 106 cells / p1-formal-vN registry)
is untouched. Planner-comparison v2 adds a T06 attestation identity while
preserving the v1 measurement schedule and model configuration.
"""

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_incident_gym.benchmark_manifest import (
    _REVISION_PATTERN,
    ARTIFACT_FILENAMES,
    FORMAL_MODEL_BASE_URLS,
    FORMAL_SCENARIO_IDS,
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    P1_SCENARIO_IDS,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
    BenchmarkManifestError,
    ManifestBudget,
    ManifestCell,
    ManifestModelConfiguration,
    ManifestPolicy,
    ManifestResultInputs,
    ScenarioCatalogEntry,
    _catalog_for_project,
    _policy_for_strategy,
    _pretty_json,
    _reject_duplicate_json_keys,
    _run_id_for_cell,
    _sha256_bytes,
    result_inputs_for_project,
)
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.planner_admission_attestation import (
    validate_original_report_for_freeze,
    verify_admission_attestation,
)

EXPERIMENT_SCHEMA_VERSION = "p1.planner_comparison_manifest.v1"
EXPERIMENT_MANIFEST_ID = "p1-planner-compare-v1"
EXPERIMENT_MANIFEST_PATH = Path("config/benchmark/p1-planner-compare-v1.json")
EXPERIMENT_SCHEMA_VERSION_V2 = "p1.planner_comparison_manifest.v2"
EXPERIMENT_MANIFEST_ID_V2 = "p1-planner-compare-v2"
EXPERIMENT_MANIFEST_PATH_V2 = Path("config/benchmark/p1-planner-compare-v2.json")
#: v3 is a new identity INSTANCE under the same v2 contract: identical fields,
#: same admission attestation, new implementation revision and current policy
#: identities (the tool-contract-visible planner surface). No fourth schema.
EXPERIMENT_MANIFEST_ID_V3 = "p1-planner-compare-v3"
EXPERIMENT_MANIFEST_PATH_V3 = Path("config/benchmark/p1-planner-compare-v3.json")

#: The experiment schedules exactly these three strategies, in this order.
EXPERIMENT_STRATEGIES = (
    DiagnosticStrategy.EVIDENCE_PLANNER,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
    DiagnosticStrategy.STATIC_SKILL,
)

#: Within one scenario the three strategies run back-to-back; the order
#: rotates per repeat so each strategy occupies first/middle/last once per
#: repeat (proposal §3). This balances position, not time correlation.
_EXPERIMENT_STRATEGY_ROTATION = {
    1: EXPERIMENT_STRATEGIES,
    2: (EXPERIMENT_STRATEGIES[1], EXPERIMENT_STRATEGIES[2], EXPERIMENT_STRATEGIES[0]),
    3: (EXPERIMENT_STRATEGIES[2], EXPERIMENT_STRATEGIES[0], EXPERIMENT_STRATEGIES[1]),
}

_EXPERIMENT_ID_PATTERN = r"^p1-planner-compare-v[1-9][0-9]*$"

#: Proposal §3: all three strategies bind the same pairing the latest formal
#: measurements used (deepseek via commandcode), with empty overrides.
EXPERIMENT_MODEL = "deepseek/deepseek-v4.1-flash"

APPROVED_EXPERIMENT_IDS: tuple[str, ...] = (
    EXPERIMENT_MANIFEST_ID,
    EXPERIMENT_MANIFEST_ID_V2,
    EXPERIMENT_MANIFEST_ID_V3,
)


def experiment_manifest_path_for(manifest_id: str) -> Path:
    """Return the canonical repository path for an experiment identity."""

    if re.fullmatch(_EXPERIMENT_ID_PATTERN, manifest_id) is None:
        raise BenchmarkManifestError("experiment manifest_id must match p1-planner-compare-v<N>")
    if manifest_id not in APPROVED_EXPERIMENT_IDS:
        raise BenchmarkManifestError(
            "experiment manifest_id must be an approved experiment identity: "
            + ", ".join(APPROVED_EXPERIMENT_IDS)
        )
    return Path(f"config/benchmark/{manifest_id}.json")


def _experiment_schedule_specs(manifest_id: str) -> tuple[dict[str, Any], ...]:
    if re.fullmatch(_EXPERIMENT_ID_PATTERN, manifest_id) is None:
        raise BenchmarkManifestError("experiment manifest_id is invalid")
    payloads: list[dict[str, Any]] = []
    for repeat_index, shift in zip((1, 2, 3), (0, 4, 8), strict=True):
        rotated = FORMAL_SCENARIO_IDS[shift:] + FORMAL_SCENARIO_IDS[:shift]
        strategies = _EXPERIMENT_STRATEGY_ROTATION[repeat_index]
        for incident_case_id in rotated:
            for strategy in strategies:
                payloads.append(
                    {
                        "incident_case_id": incident_case_id,
                        "strategy": strategy,
                        "repeat_index": repeat_index,
                    }
                )
    return tuple(payloads)


def generate_experiment_cells(
    manifest_id: str = EXPERIMENT_MANIFEST_ID,
) -> tuple[ManifestCell, ...]:
    cells: list[ManifestCell] = []
    for sequence, spec in enumerate(_experiment_schedule_specs(manifest_id), start=1):
        strategy = spec["strategy"]
        incident_case_id = str(spec["incident_case_id"])
        repeat_index = int(spec["repeat_index"])
        cells.append(
            ManifestCell(
                sequence=sequence,
                run_id=_run_id_for_cell(
                    manifest_id,
                    sequence,
                    incident_case_id,
                    strategy,
                    repeat_index,
                ),
                incident_case_id=incident_case_id,
                strategy=strategy,
                repeat_index=repeat_index,
                model_backed=True,
            )
        )
    return tuple(cells)


class PlannerComparisonManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.planner_comparison_manifest.v1"]
    manifest_id: Annotated[str, Field(pattern=_EXPERIMENT_ID_PATTERN)]
    implementation_revision: Annotated[str, Field(pattern=_REVISION_PATTERN)]
    model_configuration: ManifestModelConfiguration
    budget: ManifestBudget
    artifact_files: tuple[str, ...]
    scenario_catalog: tuple[ScenarioCatalogEntry, ...] = Field(min_length=1)
    formal_scenario_ids: tuple[str, ...] = Field(min_length=1)
    result_inputs: ManifestResultInputs
    policies: tuple[ManifestPolicy, ...] = Field(min_length=1)
    cells: tuple[ManifestCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> "PlannerComparisonManifest":
        expected_schema = {
            EXPERIMENT_MANIFEST_ID: EXPERIMENT_SCHEMA_VERSION,
            EXPERIMENT_MANIFEST_ID_V2: EXPERIMENT_SCHEMA_VERSION_V2,
            EXPERIMENT_MANIFEST_ID_V3: EXPERIMENT_SCHEMA_VERSION_V2,
        }.get(self.manifest_id)
        if expected_schema is None or self.schema_version != expected_schema:
            raise ValueError("experiment schema version and manifest identity do not match")
        catalog_ids = tuple(item.incident_case_id for item in self.scenario_catalog)
        if catalog_ids != P1_SCENARIO_IDS:
            raise ValueError("scenario catalog must match the frozen P1 order")
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("scenario catalog IDs must be unique")
        if self.formal_scenario_ids != FORMAL_SCENARIO_IDS:
            raise ValueError("formal scenario order does not match the frozen schedule")
        if self.artifact_files != ARTIFACT_FILENAMES:
            raise ValueError("artifact_files must match the canonical six files")
        formal_ids = set(self.formal_scenario_ids)
        for item in self.scenario_catalog:
            if item.is_formal != (item.incident_case_id in formal_ids):
                raise ValueError("scenario catalog formal flag is invalid")

        policy_strategies = tuple(item.strategy for item in self.policies)
        if policy_strategies != EXPERIMENT_STRATEGIES:
            raise ValueError("policy order must contain the three experiment strategies")
        if len(policy_strategies) != len(set(policy_strategies)):
            raise ValueError("policy strategies must be unique")

        expected_cells = generate_experiment_cells(self.manifest_id)
        if self.cells != expected_cells:
            raise ValueError("manifest cells do not match the experiment schedule")
        if len({cell.run_id for cell in self.cells}) != len(self.cells):
            raise ValueError("manifest run IDs must be unique")
        if len(self.cells) != 108 or not all(cell.model_backed for cell in self.cells):
            raise ValueError("experiment schedule must be 108 model-backed cells")
        counts = {
            strategy: sum(cell.strategy is strategy for cell in self.cells)
            for strategy in EXPERIMENT_STRATEGIES
        }
        if counts != {strategy: 36 for strategy in EXPERIMENT_STRATEGIES}:
            raise ValueError(f"experiment cell counts are invalid: {counts}")
        if any(cell.incident_case_id not in formal_ids for cell in self.cells):
            raise ValueError("manifest cells must use formal scenarios only")
        return self

    @property
    def total_cells(self) -> int:
        return len(self.cells)

    @property
    def model_backed_count(self) -> int:
        return sum(cell.model_backed for cell in self.cells)

    @property
    def fixed_rule_count(self) -> int:
        return sum(cell.strategy is DiagnosticStrategy.FIXED_RULE for cell in self.cells)

    def canonical_json(self) -> str:
        return _pretty_json(self.model_dump(mode="json"))

    def digest(self) -> str:
        return _sha256_bytes(self.canonical_json().encode("utf-8"))


class PlannerComparisonManifestV2(PlannerComparisonManifest):
    schema_version: Literal["p1.planner_comparison_manifest.v2"]
    admission_attestation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def build_experiment_manifest(
    implementation_revision: str,
    *,
    project_root: Path,
    manifest_id: str = EXPERIMENT_MANIFEST_ID,
    model_base_url: str = FORMAL_MODEL_BASE_URLS[EXPERIMENT_MODEL],
    model_name: str = EXPERIMENT_MODEL,
) -> PlannerComparisonManifest:
    if re.fullmatch(_REVISION_PATTERN, implementation_revision) is None:
        raise BenchmarkManifestError("implementation_revision must be a 40-hex revision")
    if manifest_id != EXPERIMENT_MANIFEST_ID:
        raise BenchmarkManifestError(
            "build_experiment_manifest creates only the v1 identity"
        )
    if model_name not in FORMAL_MODEL_BASE_URLS:
        raise BenchmarkManifestError(
            "experiment model must be one of the approved pairings: "
            + ", ".join(FORMAL_MODEL_BASE_URLS)
        )
    if model_base_url != FORMAL_MODEL_BASE_URLS[model_name]:
        raise BenchmarkManifestError(
            f"model {model_name} must be frozen with its approved endpoint "
            f"{FORMAL_MODEL_BASE_URLS[model_name]}"
        )
    return PlannerComparisonManifest(
        schema_version=EXPERIMENT_SCHEMA_VERSION,
        manifest_id=manifest_id,
        implementation_revision=implementation_revision,
        model_configuration=ManifestModelConfiguration(
            provider="openai-compatible",
            model=model_name,
            base_url=model_base_url,
            settings_overrides={},
        ),
        budget=ManifestBudget(
            model_request_limit=MODEL_REQUEST_LIMIT,
            tool_call_limit=TOOL_CALL_LIMIT,
            output_retry_limit=OUTPUT_RETRY_LIMIT,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        artifact_files=ARTIFACT_FILENAMES,
        scenario_catalog=_catalog_for_project(project_root),
        formal_scenario_ids=FORMAL_SCENARIO_IDS,
        result_inputs=result_inputs_for_project(project_root),
        policies=tuple(_policy_for_strategy(strategy) for strategy in EXPERIMENT_STRATEGIES),
        cells=generate_experiment_cells(manifest_id),
    )


def build_experiment_manifest_v2(
    implementation_revision: str,
    admission_attestation_sha256: str,
    *,
    project_root: Path,
    model_base_url: str = FORMAL_MODEL_BASE_URLS[EXPERIMENT_MODEL],
    model_name: str = EXPERIMENT_MODEL,
) -> PlannerComparisonManifestV2:
    """Build v2 with the same schedule and current contracts, plus T06 identity."""

    base = build_experiment_manifest(
        implementation_revision,
        project_root=project_root,
        model_base_url=model_base_url,
        model_name=model_name,
    )
    payload = base.model_dump(mode="json")
    payload.update(
        {
            "schema_version": EXPERIMENT_SCHEMA_VERSION_V2,
            "manifest_id": EXPERIMENT_MANIFEST_ID_V2,
            "cells": [
                cell.model_dump(mode="json")
                for cell in generate_experiment_cells(EXPERIMENT_MANIFEST_ID_V2)
            ],
            "admission_attestation_sha256": admission_attestation_sha256,
        }
    )
    return PlannerComparisonManifestV2.model_validate(payload)


def build_experiment_manifest_v3(
    implementation_revision: str,
    admission_attestation_sha256: str,
    *,
    project_root: Path,
    model_base_url: str = FORMAL_MODEL_BASE_URLS[EXPERIMENT_MODEL],
    model_name: str = EXPERIMENT_MODEL,
) -> PlannerComparisonManifestV2:
    """Build v3: the v2 contract with a new identity, the current policy
    surfaces (the tool-contract-visible planner) and a fresh revision."""

    base = build_experiment_manifest(
        implementation_revision,
        project_root=project_root,
        model_base_url=model_base_url,
        model_name=model_name,
    )
    payload = base.model_dump(mode="json")
    payload.update(
        {
            "schema_version": EXPERIMENT_SCHEMA_VERSION_V2,
            "manifest_id": EXPERIMENT_MANIFEST_ID_V3,
            "cells": [
                cell.model_dump(mode="json")
                for cell in generate_experiment_cells(EXPERIMENT_MANIFEST_ID_V3)
            ],
            "admission_attestation_sha256": admission_attestation_sha256,
        }
    )
    return PlannerComparisonManifestV2.model_validate(payload)


def _validate_experiment_manifest_type_and_identity(
    manifest: PlannerComparisonManifest,
) -> None:
    if type(manifest) is PlannerComparisonManifestV2:
        valid = manifest.schema_version == EXPERIMENT_SCHEMA_VERSION_V2 and (
            manifest.manifest_id in (EXPERIMENT_MANIFEST_ID_V2, EXPERIMENT_MANIFEST_ID_V3)
        )
    elif type(manifest) is PlannerComparisonManifest:
        valid = (
            manifest.schema_version == EXPERIMENT_SCHEMA_VERSION
            and manifest.manifest_id == EXPERIMENT_MANIFEST_ID
        )
    else:
        valid = False
    if not valid:
        raise BenchmarkManifestError("experiment schema version and manifest identity do not match")


def verify_experiment_manifest(
    manifest: PlannerComparisonManifest,
    *,
    project_root: Path,
) -> PlannerComparisonManifest:
    _validate_experiment_manifest_type_and_identity(manifest)
    if manifest.scenario_catalog != _catalog_for_project(project_root):
        raise BenchmarkManifestError("ScenarioSpec catalog or digest drifted")
    if manifest.result_inputs != result_inputs_for_project(project_root):
        raise BenchmarkManifestError("result-input hashes drifted")
    if manifest.policies != tuple(
        _policy_for_strategy(strategy) for strategy in EXPERIMENT_STRATEGIES
    ):
        raise BenchmarkManifestError("policy identity drifted")
    if manifest.budget != ManifestBudget(
        model_request_limit=8,
        tool_call_limit=8,
        output_retry_limit=2,
        timeout_seconds=300,
    ):
        raise BenchmarkManifestError("budget drifted")
    if isinstance(manifest, PlannerComparisonManifestV2):
        verify_admission_attestation(
            expected_sha256=manifest.admission_attestation_sha256,
            formal_scenario_ids=manifest.formal_scenario_ids,
            project_root=project_root,
        )
    return manifest


def freeze_experiment_manifest(
    manifest: PlannerComparisonManifest,
    output: Path,
    *,
    project_root: Path,
    admission_report_path: Path | None = None,
) -> Path:
    project_root = project_root.resolve(strict=True)
    _validate_experiment_manifest_type_and_identity(manifest)
    if isinstance(manifest, PlannerComparisonManifestV2):
        validate_original_report_for_freeze(
            admission_report_path,
            expected_sha256=manifest.admission_attestation_sha256,
            project_root=project_root,
        )
        verify_experiment_manifest(manifest, project_root=project_root)
    elif admission_report_path is not None:
        raise BenchmarkManifestError("--admission-report is valid only for the v2 identity")
    raw_output = Path(output)
    if not raw_output.is_absolute():
        raw_output = project_root / raw_output
    if raw_output.is_symlink() or raw_output.exists():
        raise BenchmarkManifestError("experiment manifest output already exists")
    resolved = raw_output.resolve(strict=False)
    expected = (
        project_root / experiment_manifest_path_for(manifest.manifest_id)
    ).resolve(strict=False)
    if resolved != expected:
        expected_relative = experiment_manifest_path_for(manifest.manifest_id)
        raise BenchmarkManifestError(
            f"experiment manifest output must be {expected_relative}"
        )
    for parent in (project_root / "config", project_root / "config" / "benchmark"):
        if parent.is_symlink():
            raise BenchmarkManifestError("manifest output parent must not be a symlink")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        with resolved.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(manifest.canonical_json())
    except OSError as exc:
        raise BenchmarkManifestError(f"无法独占写入实验 manifest：{resolved}") from exc
    return resolved


def load_experiment_manifest(
    path: Path,
) -> PlannerComparisonManifest | PlannerComparisonManifestV2:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(payload, dict):
            raise BenchmarkManifestError("experiment manifest JSON must be an object")
        if payload.get("schema_version") == EXPERIMENT_SCHEMA_VERSION_V2:
            manifest = PlannerComparisonManifestV2.model_validate(payload)
        else:
            manifest = PlannerComparisonManifest.model_validate(payload)
        _validate_experiment_manifest_type_and_identity(manifest)
        if manifest.manifest_id not in APPROVED_EXPERIMENT_IDS:
            raise BenchmarkManifestError(
                "experiment manifest_id must be an approved experiment identity: "
                + ", ".join(APPROVED_EXPERIMENT_IDS)
            )
        if path.name != f"{manifest.manifest_id}.json":
            raise BenchmarkManifestError("manifest file name must match its manifest_id")
        return manifest
    except BenchmarkManifestError:
        raise
    except Exception as exc:
        raise BenchmarkManifestError(f"实验 manifest 无效：{path}") from exc


__all__ = [
    "APPROVED_EXPERIMENT_IDS",
    "EXPERIMENT_MANIFEST_ID",
    "EXPERIMENT_MANIFEST_PATH",
    "EXPERIMENT_MANIFEST_ID_V2",
    "EXPERIMENT_MANIFEST_PATH_V2",
    "EXPERIMENT_MANIFEST_ID_V3",
    "EXPERIMENT_MANIFEST_PATH_V3",
    "EXPERIMENT_SCHEMA_VERSION",
    "EXPERIMENT_SCHEMA_VERSION_V2",
    "EXPERIMENT_STRATEGIES",
    "PlannerComparisonManifest",
    "PlannerComparisonManifestV2",
    "build_experiment_manifest",
    "build_experiment_manifest_v2",
    "build_experiment_manifest_v3",
    "experiment_manifest_path_for",
    "freeze_experiment_manifest",
    "generate_experiment_cells",
    "load_experiment_manifest",
    "verify_experiment_manifest",
]
