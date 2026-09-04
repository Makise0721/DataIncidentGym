from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    MODEL_STRATEGIES,
    Diagnosis,
    DiagnosticStrategy,
    PolicyIdentity,
)
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
    policy_surface_for_strategy,
)
from data_incident_gym.evaluation import EVALUATOR_VERSION
from data_incident_gym.fixed_rule import (
    FIXED_RULE_TOOL_NAMES,
    fixed_rule_policy_identity,
)
from data_incident_gym.profiles import load_profile_spec
from data_incident_gym.scenarios import (
    P1_SCENARIO_IDS,
    ScenarioSpec,
    VariantRole,
    load_scenario_spec,
)

MANIFEST_SCHEMA_VERSION = "p1.benchmark_manifest.v1"
MANIFEST_ID = "p1-formal-v1"
MANIFEST_PATH = Path("config/benchmark/p1-formal-v1.json")
DEFAULT_FORMAL_PROVIDER = "openai-compatible"
DEFAULT_FORMAL_MODEL = "mimo-v2.5"
DEFAULT_FORMAL_BASE_URL = "https://api.xiaomimimo.com/v1"

FORMAL_SCENARIO_IDS = (
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "required_null_order_customer_a",
    "required_null_order_customer_b",
    "duplicate_payment_coupon_a",
    "duplicate_payment_coupon_b",
    "orphan_payment_coupon_a",
    "orphan_payment_coupon_b",
    "silent_payment_drop_partition_a",
    "silent_payment_drop_partition_b",
    "order_volume_pattern_a",
    "order_volume_within_sla",
)
CONFIRMABLE_SCENARIO_IDS = (
    "schema_type_change_order_customer_a",
    "required_null_order_customer_a",
    "duplicate_payment_coupon_a",
    "orphan_payment_coupon_a",
    "silent_payment_drop_partition_a",
)

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_REVISION_PATTERN = r"^[0-9a-f]{40}$"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_MANIFEST_ID_PATTERN = r"^p1-formal-v[1-9][0-9]*$"
APPROVED_MANIFEST_IDS = (
    "p1-formal-v1",
    "p1-formal-v2",
    "p1-formal-v3",
    "p1-formal-v4",
)


class BenchmarkManifestError(ValueError):
    """Raised when a benchmark manifest cannot be frozen or verified safely."""


def manifest_path_for(manifest_id: str) -> Path:
    """Return the canonical repository path for an approved manifest identity."""

    if re.fullmatch(_MANIFEST_ID_PATTERN, manifest_id) is None:
        raise BenchmarkManifestError("manifest_id must match p1-formal-v<N>")
    if manifest_id not in APPROVED_MANIFEST_IDS:
        raise BenchmarkManifestError(
            "manifest_id must be an approved formal identity: "
            + ", ".join(APPROVED_MANIFEST_IDS)
        )
    return Path(f"config/benchmark/{manifest_id}.json")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    except OSError as exc:
        raise BenchmarkManifestError(f"无法读取结果输入：{path}") from exc


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkManifestError("manifest JSON contains duplicate object key")
        result[key] = value
    return result


def _safe_base_url(value: str) -> str:
    if not value or value.strip() != value:
        raise ValueError("base_url must not be blank or padded")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must be a safe HTTP(S) URL")
    return value


class ManifestBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_request_limit: Literal[8]
    tool_call_limit: Literal[8]
    output_retry_limit: Literal[2]
    timeout_seconds: Literal[300]


class ManifestModelConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["openai-compatible"]
    model: Literal["mimo-v2.5"]
    base_url: StrictStr
    settings_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _safe_base_url(value)

    @field_validator("settings_overrides")
    @classmethod
    def reject_overrides(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("formal model setting overrides must be empty")
        return value


class ScenarioCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: StrictStr
    suite: Literal["P1"]
    variant_role: VariantRole | None
    scenario_spec_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    is_formal: StrictBool


class ManifestResultInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_spec_version: Literal["profile_spec.v1"]
    profile_spec_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    scenario_spec_schema_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    diagnosis_schema_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    evaluator_version: StrictStr
    evaluator_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]


class ManifestPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: DiagnosticStrategy
    policy_identity: PolicyIdentity
    tool_names: tuple[StrictStr, ...]
    final_diagnosis_schema_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]

    @model_validator(mode="after")
    def validate_policy(self) -> ManifestPolicy:
        if self.policy_identity.strategy is not self.strategy:
            raise ValueError("policy identity strategy must match policy")
        if len(self.tool_names) != len(set(self.tool_names)):
            raise ValueError("policy tool names must be unique")
        return self


class ManifestCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: StrictInt = Field(ge=1)
    run_id: Annotated[StrictStr, Field(pattern=_RUN_ID_PATTERN)]
    incident_case_id: StrictStr
    strategy: DiagnosticStrategy
    repeat_index: StrictInt = Field(ge=1, le=3)
    model_backed: StrictBool

    @model_validator(mode="after")
    def validate_model_backing(self) -> ManifestCell:
        expected = self.strategy is not DiagnosticStrategy.FIXED_RULE
        if self.model_backed != expected:
            raise ValueError("manifest cell model_backed does not match strategy")
        return self


def _run_id_for_cell(
    manifest_id: str,
    sequence: int,
    incident_case_id: str,
    strategy: DiagnosticStrategy,
    repeat_index: int,
) -> str:
    payload = f"{manifest_id}:{sequence}:{incident_case_id}:{strategy.value}:{repeat_index}"
    return _sha256_bytes(payload.encode("utf-8"))[:32]


def run_id_for_cell(
    manifest_id: str,
    sequence: int,
    incident_case_id: str,
    strategy: DiagnosticStrategy,
    repeat_index: int,
) -> str:
    """Return the frozen run ID for one manifest position."""

    return _run_id_for_cell(
        manifest_id,
        sequence,
        incident_case_id,
        DiagnosticStrategy(strategy),
        repeat_index,
    )


def _schedule_specs(manifest_id: str) -> tuple[dict[str, object], ...]:
    if re.fullmatch(_MANIFEST_ID_PATTERN, manifest_id) is None:
        raise BenchmarkManifestError("manifest_id is invalid")
    payloads: list[dict[str, object]] = []

    for repeat_index, shift in zip((1, 2, 3), (0, 4, 8), strict=True):
        rotated = FORMAL_SCENARIO_IDS[shift:] + FORMAL_SCENARIO_IDS[:shift]
        for position, incident_case_id in enumerate(rotated):
            strategies = (
                (DiagnosticStrategy.STATIC_SKILL, DiagnosticStrategy.DIAGNOSTIC_KERNEL)
                if (position + repeat_index) % 2 == 1
                else (DiagnosticStrategy.DIAGNOSTIC_KERNEL, DiagnosticStrategy.STATIC_SKILL)
            )
            for strategy in strategies:
                payloads.append(
                    {
                        "incident_case_id": incident_case_id,
                        "strategy": strategy,
                        "repeat_index": repeat_index,
                    }
                )

    for incident_case_id in reversed(FORMAL_SCENARIO_IDS):
        payloads.append(
            {
                "incident_case_id": incident_case_id,
                "strategy": DiagnosticStrategy.NO_TOOL,
                "repeat_index": 1,
            }
        )

    for incident_case_id in CONFIRMABLE_SCENARIO_IDS:
        for strategy in (
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
        ):
            payloads.append(
                {
                    "incident_case_id": incident_case_id,
                    "strategy": strategy,
                    "repeat_index": 1,
                }
            )

    for incident_case_id in FORMAL_SCENARIO_IDS:
        payloads.append(
            {
                "incident_case_id": incident_case_id,
                "strategy": DiagnosticStrategy.FIXED_RULE,
                "repeat_index": 1,
            }
        )

    return tuple(payloads)


def generate_cells(manifest_id: str = MANIFEST_ID) -> tuple[ManifestCell, ...]:
    cells: list[ManifestCell] = []
    for sequence, spec in enumerate(_schedule_specs(manifest_id), start=1):
        strategy = DiagnosticStrategy(spec["strategy"])
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
                model_backed=strategy is not DiagnosticStrategy.FIXED_RULE,
            )
        )
    return tuple(cells)


class BenchmarkManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.benchmark_manifest.v1"]
    manifest_id: Annotated[StrictStr, Field(pattern=_MANIFEST_ID_PATTERN)]
    implementation_revision: Annotated[StrictStr, Field(pattern=_REVISION_PATTERN)]
    model_configuration: ManifestModelConfiguration
    budget: ManifestBudget
    artifact_files: tuple[StrictStr, ...]
    scenario_catalog: tuple[ScenarioCatalogEntry, ...] = Field(min_length=1)
    formal_scenario_ids: tuple[StrictStr, ...] = Field(min_length=1)
    result_inputs: ManifestResultInputs
    policies: tuple[ManifestPolicy, ...] = Field(min_length=1)
    cells: tuple[ManifestCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> BenchmarkManifest:
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
        if policy_strategies != (
            DiagnosticStrategy.STATIC_SKILL,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.NO_TOOL,
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
            DiagnosticStrategy.FIXED_RULE,
        ):
            raise ValueError("policy order must contain the frozen six strategies")
        if len(policy_strategies) != len(set(policy_strategies)):
            raise ValueError("policy strategies must be unique")

        expected_cells = generate_cells(self.manifest_id)
        if self.cells != expected_cells:
            raise ValueError("manifest cells do not match the frozen schedule")
        if len({cell.run_id for cell in self.cells}) != len(self.cells):
            raise ValueError("manifest run IDs must be unique")
        counts = _strategy_counts(self.cells)
        expected_counts = {
            "total": 106,
            "model_backed": 94,
            "fixed_rule": 12,
            "main": 72,
            "no_tool": 12,
            "kernel_no_lineage": 5,
            "kernel_no_schema": 5,
        }
        if counts != expected_counts:
            raise ValueError(f"manifest cell counts are invalid: {counts}")
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

    @property
    def strategy_counts(self) -> dict[str, int]:
        return {
            strategy.value: count
            for strategy, count in _strategy_counts_by_enum(self.cells).items()
        }

    def canonical_json(self) -> str:
        return _pretty_json(self.model_dump(mode="json"))

    def digest(self) -> str:
        return _sha256_bytes(self.canonical_json().encode("utf-8"))


def _strategy_counts_by_enum(cells: tuple[ManifestCell, ...]) -> dict[DiagnosticStrategy, int]:
    return {
        strategy: sum(cell.strategy is strategy for cell in cells)
        for strategy in DiagnosticStrategy
        if any(cell.strategy is strategy for cell in cells)
    }


def _strategy_counts(cells: tuple[ManifestCell, ...]) -> dict[str, int]:
    counts = _strategy_counts_by_enum(cells)
    return {
        "total": len(cells),
        "model_backed": sum(cell.model_backed for cell in cells),
        "fixed_rule": counts.get(DiagnosticStrategy.FIXED_RULE, 0),
        "main": sum(counts.get(strategy, 0) for strategy in MODEL_STRATEGIES[:2]),
        "no_tool": counts.get(DiagnosticStrategy.NO_TOOL, 0),
        "kernel_no_lineage": counts.get(DiagnosticStrategy.KERNEL_NO_LINEAGE, 0),
        "kernel_no_schema": counts.get(DiagnosticStrategy.KERNEL_NO_SCHEMA, 0),
    }


def _catalog_for_project(project_root: Path) -> tuple[ScenarioCatalogEntry, ...]:
    return tuple(
        ScenarioCatalogEntry(
            incident_case_id=case_id,
            suite="P1",
            variant_role=(spec := load_scenario_spec(case_id, project_root)).variant_role,
            scenario_spec_sha256=spec.digest(),
            is_formal=case_id in FORMAL_SCENARIO_IDS,
        )
        for case_id in P1_SCENARIO_IDS
    )


def _policy_for_strategy(strategy: DiagnosticStrategy) -> ManifestPolicy:
    if strategy is DiagnosticStrategy.FIXED_RULE:
        identity = fixed_rule_policy_identity()
        tool_names = FIXED_RULE_TOOL_NAMES
    else:
        surface = policy_surface_for_strategy(strategy)
        identity = surface.policy_identity
        tool_names = tuple(item["name"] for item in surface.tool_schema_payload)
    return ManifestPolicy(
        strategy=strategy,
        policy_identity=identity,
        tool_names=tool_names,
        final_diagnosis_schema_sha256=_sha256_json(Diagnosis.model_json_schema()),
    )


def _result_inputs_for_project(project_root: Path) -> ManifestResultInputs:
    evaluator_path = project_root / "src" / "data_incident_gym" / "evaluation.py"
    return ManifestResultInputs(
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256=load_profile_spec(project_root).digest(),
        scenario_spec_schema_sha256=_sha256_json(ScenarioSpec.model_json_schema()),
        diagnosis_schema_sha256=_sha256_json(Diagnosis.model_json_schema()),
        evaluator_version=EVALUATOR_VERSION,
        evaluator_sha256=_sha256_file(evaluator_path),
    )


def build_manifest(
    implementation_revision: str,
    *,
    project_root: Path = PROJECT_ROOT,
    manifest_id: str = MANIFEST_ID,
    model_base_url: str = DEFAULT_FORMAL_BASE_URL,
    model_name: str = DEFAULT_FORMAL_MODEL,
) -> BenchmarkManifest:
    if re.fullmatch(_REVISION_PATTERN, implementation_revision) is None:
        raise BenchmarkManifestError("implementation_revision must be a 40-hex revision")
    if manifest_id not in APPROVED_MANIFEST_IDS:
        raise BenchmarkManifestError(
            "manifest_id must be an approved formal identity: "
            + ", ".join(APPROVED_MANIFEST_IDS)
        )
    if model_name != DEFAULT_FORMAL_MODEL:
        raise BenchmarkManifestError("formal model must be mimo-v2.5")
    return BenchmarkManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        manifest_id=manifest_id,
        implementation_revision=implementation_revision,
        model_configuration=ManifestModelConfiguration(
            provider=DEFAULT_FORMAL_PROVIDER,
            model=DEFAULT_FORMAL_MODEL,
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
        result_inputs=_result_inputs_for_project(project_root),
        policies=tuple(_policy_for_strategy(strategy) for strategy in DiagnosticStrategy),
        cells=generate_cells(manifest_id),
    )


def _current_result_inputs(
    manifest: BenchmarkManifest,
    *,
    project_root: Path,
) -> tuple[tuple[ScenarioCatalogEntry, ...], ManifestResultInputs, tuple[ManifestPolicy, ...]]:
    return (
        _catalog_for_project(project_root),
        _result_inputs_for_project(project_root),
        tuple(_policy_for_strategy(strategy) for strategy in DiagnosticStrategy),
    )


def verify_manifest(
    manifest: BenchmarkManifest,
    *,
    project_root: Path = PROJECT_ROOT,
) -> BenchmarkManifest:
    if manifest.manifest_id not in APPROVED_MANIFEST_IDS:
        raise BenchmarkManifestError(
            "manifest_id must be an approved formal identity: "
            + ", ".join(APPROVED_MANIFEST_IDS)
        )
    catalog, result_inputs, policies = _current_result_inputs(manifest, project_root=project_root)
    if manifest.scenario_catalog != catalog:
        raise BenchmarkManifestError("ScenarioSpec catalog or digest drifted")
    if manifest.result_inputs != result_inputs:
        raise BenchmarkManifestError("result-input hashes drifted")
    if manifest.policies != policies:
        raise BenchmarkManifestError("policy identity drifted")
    if manifest.budget != ManifestBudget(
        model_request_limit=8,
        tool_call_limit=8,
        output_retry_limit=2,
        timeout_seconds=300,
    ):
        raise BenchmarkManifestError("budget drifted")
    return manifest


def freeze_manifest(
    manifest: BenchmarkManifest,
    output: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    project_root = project_root.resolve(strict=True)
    raw_output = Path(output)
    if not raw_output.is_absolute():
        raw_output = project_root / raw_output
    if raw_output.is_symlink() or raw_output.exists():
        raise BenchmarkManifestError("manifest output already exists")
    output = raw_output.resolve(strict=False)
    expected = (project_root / manifest_path_for(manifest.manifest_id)).resolve(strict=False)
    if output != expected:
        raise BenchmarkManifestError(
            f"manifest output must be config/benchmark/{manifest.manifest_id}.json"
        )
    for parent in (project_root / "config", project_root / "config" / "benchmark"):
        if parent.is_symlink():
            raise BenchmarkManifestError("manifest output parent must not be a symlink")
    if output.is_symlink() or output.exists():
        raise BenchmarkManifestError("manifest output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or output.exists():
        raise BenchmarkManifestError("manifest output already exists")
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(manifest.canonical_json())
    except OSError as exc:
        raise BenchmarkManifestError(f"无法独占写入 manifest：{output}") from exc
    return output


def load_manifest(path: Path) -> BenchmarkManifest:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        manifest = BenchmarkManifest.model_validate(payload)
        if manifest.manifest_id not in APPROVED_MANIFEST_IDS:
            raise BenchmarkManifestError(
                "manifest_id must be an approved formal identity: "
                + ", ".join(APPROVED_MANIFEST_IDS)
            )
        if path.name != f"{manifest.manifest_id}.json":
            raise BenchmarkManifestError("manifest file name must match its manifest_id")
        return manifest
    except BenchmarkManifestError:
        raise
    except Exception as exc:
        raise BenchmarkManifestError(f"manifest 无效：{path}") from exc


__all__ = [
    "APPROVED_MANIFEST_IDS",
    "BenchmarkManifest",
    "BenchmarkManifestError",
    "CONFIRMABLE_SCENARIO_IDS",
    "DEFAULT_FORMAL_BASE_URL",
    "DEFAULT_FORMAL_MODEL",
    "FORMAL_SCENARIO_IDS",
    "MANIFEST_ID",
    "MANIFEST_PATH",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestBudget",
    "ManifestCell",
    "ManifestModelConfiguration",
    "ManifestPolicy",
    "ManifestResultInputs",
    "ScenarioCatalogEntry",
    "build_manifest",
    "freeze_manifest",
    "generate_cells",
    "load_manifest",
    "manifest_path_for",
    "run_id_for_cell",
    "verify_manifest",
]
