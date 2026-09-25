"""Frozen scoring inputs for offline re-evaluation (T02).

The six-file artifact bundle records what a run produced. This module records
everything the deterministic evaluator consumed so the same scoring can be
re-run later without the model, the database or dbt: the private scenario
snapshot, the frozen verification, the complete diagnosis run result, the
recovery proof, the budget and the evaluator identity. The attachment lives in
``.dig/scoring-inputs/<run_id>/`` and never enters the strategy-facing view.

Strictness is the point: loading rejects unknown schema versions, duplicate
JSON keys, changed digests, cross-run content, path escapes and unknown
evaluator identities. Classification distinguishes historical runs that stay
re-scorable from those that only allow partial analysis, and never rebuilds
private verification facts from the current scenario configuration.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import re
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import pydantic
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    BudgetSummary,
    trace_schema_for_run,
    validate_trace_envelope,
)
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    RUN_ID_PATTERN,
    DiagnosisRunResult,
    DiagnosisRunResultAny,
    DiagnosisRunResultV2,
    DiagnosisRunResultV3,
    DiagnosisTerminalTraceEvent,
    DiagnosisV2,
    DiagnosticStrategy,
    KernelStateTraceEvent,
)
from data_incident_gym.diagnostic_contracts import InvestigationState
from data_incident_gym.evaluation import EVALUATOR_VERSION, EvaluationResult
from data_incident_gym.lab_verifier import ScenarioVerification, ScenarioVerificationStatus
from data_incident_gym.scenarios import ScenarioSpec

SCORING_INPUTS_SCHEMA_VERSION = "p1.evaluation_inputs.v1"
SCORING_INPUTS_INDEX_SCHEMA_VERSION = "p1.scoring_inputs_index.v1"
SCORING_INPUTS_EXPORT_SCHEMA_VERSION = "p1.scoring_inputs_export.v1"
SCORING_INPUTS_DIRNAME = "scoring-inputs"
SCORING_INPUTS_DIGEST_DIRNAME = ".dig"
INPUTS_FILENAME = "evaluation_inputs.json"
INDEX_FILENAME = "index.json"

# Versions whose archived attachments stay readable. v2 is retained so evidence
# written before the health-claim strictness fix keeps loading; new attachments
# always record the current version.
KNOWN_EVALUATOR_VERSIONS = frozenset(
    {"p1.evaluator.v2", "p1.evaluator.v3", "p1.evaluator.v4", "p1.evaluator.v5"}
)

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
Digest = Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]

#: Bounded retry for the bundle-directory rename (see ``_rename_with_retry``).
_RENAME_ATTEMPTS = 5
_RENAME_BACKOFF_SECONDS = 0.05

EVALUATOR_SOURCE_MODULES = (
    "evaluation.py",
    "diagnosis.py",
    "diagnostic_kernel.py",
    "evidence.py",
    "lab_verifier.py",
    "profiles.py",
    "scenarios.py",
)


class EvaluationInputsError(RuntimeError):
    """Fixed-code rejection raised by the scoring-input contract."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


def _error(code: str, detail: str | None = None) -> NoReturn:
    raise EvaluationInputsError(code, detail=detail)


class ArtifactInputStatus(StrEnum):
    RE_SCORABLE = "RE_SCORABLE"
    PARTIAL_ANALYSIS = "PARTIAL_ANALYSIS"
    NOT_RE_SCORABLE = "NOT_RE_SCORABLE"


class RecoveryProof(BaseModel):
    """Provenance-bearing recovery result; a bare boolean is not accepted.

    The proof keeps what the restore actually returned: the case it restored,
    the resulting state and the baseline fingerprint when the lab reported one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal["LAB_RESTORE"]
    incident_case_id: StrictStr = Field(min_length=1)
    state: Literal["HEALTHY", "FAILED"]
    fingerprint: Digest | None = None

    @property
    def recovered(self) -> bool:
        return self.state == "HEALTHY"


class EvaluatorIdentity(BaseModel):
    """Digest-attested identity of the evaluation code that produced a score.

    ``name`` distinguishes the built-in deterministic scorer from a controlled
    variant; the actual code is attested by ``source_digest`` and
    ``dependencies_digest``, so relabelling alone cannot forge an identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(min_length=1)
    version: StrictStr = Field(min_length=1)
    source_digest: Digest
    dependencies_digest: Digest


class VerificationPayload(BaseModel):
    """Serializable mirror of the frozen ``ScenarioVerification`` dataclass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["EXPECTED_FAILURE", "EXPECTED_ANOMALY", "HEALTHY_CONTROL"]
    incident_case_id: StrictStr = Field(min_length=1)
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    dbt_exit_code: StrictInt
    failed_nodes: tuple[StrictStr, ...]
    skipped_nodes: tuple[StrictStr, ...]
    affected_assets: tuple[StrictStr, ...]
    schema_fingerprint: Digest
    profile_spec_sha256: Digest

    @model_validator(mode="after")
    def validate_node_tuples(self) -> VerificationPayload:
        for label, values in (
            ("failed_nodes", self.failed_nodes),
            ("skipped_nodes", self.skipped_nodes),
            ("affected_assets", self.affected_assets),
        ):
            if any(not value for value in values):
                raise ValueError(f"{label} must contain non-empty strings")
            if tuple(values) != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be unique and sorted")
        return self


class ArtifactDigest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    sha256: Digest


class EvaluationInputBundle(BaseModel):
    """Frozen scoring inputs for one run; everything the evaluator consumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.evaluation_inputs.v1"] = SCORING_INPUTS_SCHEMA_VERSION
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    incident_case_id: StrictStr = Field(min_length=1)
    strategy: DiagnosticStrategy
    scenario: ScenarioSpec
    scenario_digest: Digest
    verification: VerificationPayload
    verification_digest: Digest
    diagnosis_run: DiagnosisRunResult
    diagnosis_run_digest: Digest
    recovery: RecoveryProof
    budget: BudgetSummary
    original_evaluator: EvaluatorIdentity
    artifact_digests: tuple[ArtifactDigest, ...]

    @model_validator(mode="after")
    def validate_cross_identity(self) -> EvaluationInputBundle:
        if self.diagnosis_run.diagnosis.run_id != self.run_id:
            raise ValueError("bundle run_id must match the diagnosis run")
        if self.diagnosis_run.strategy is not self.strategy:
            raise ValueError("bundle strategy must match the diagnosis run")
        if self.verification.run_id != self.run_id:
            raise ValueError("verification run_id must match the bundle")
        if self.verification.incident_case_id != self.incident_case_id:
            raise ValueError("verification case must match the bundle")
        if self.scenario.incident_case_id != self.incident_case_id:
            raise ValueError("scenario case must match the bundle")
        if self.recovery.incident_case_id != self.incident_case_id:
            raise ValueError("recovery case must match the bundle")
        if self.scenario_digest != self.scenario.digest():
            raise ValueError("scenario digest does not match the scenario snapshot")
        if self.verification_digest != _payload_digest(self.verification):
            raise ValueError("verification digest does not match the verification")
        if self.diagnosis_run_digest != self.diagnosis_run.digest():
            raise ValueError("diagnosis digest does not match the diagnosis run")
        names = tuple(item.name for item in self.artifact_digests)
        if len(names) != len(set(names)) or set(names) != set(ARTIFACT_FILENAMES):
            raise ValueError("artifact digests must cover exactly the canonical six files")
        return self

    def semantic_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def inputs_digest(self) -> str:
        return hashlib.sha256(self.semantic_json().encode("utf-8")).hexdigest()


class EvaluationInputBundleV2(EvaluationInputBundle):
    """Scoring inputs bound to the provenance-aware run and trace schemas."""

    schema_version: Literal["p1.evaluation_inputs.v2"] = "p1.evaluation_inputs.v2"
    diagnosis_run: DiagnosisRunResultV2

    @model_validator(mode="after")
    def validate_v2_evaluator(self) -> EvaluationInputBundleV2:
        if self.original_evaluator.version != "p1.evaluator.v4":
            raise ValueError("v2 evaluation-input bundle requires the v4 evaluator")
        return self


class EvaluationInputBundleV3(EvaluationInputBundle):
    """Scoring inputs bound to the versioned kernel-refusal audit schemas."""

    schema_version: Literal["p1.evaluation_inputs.v3"] = "p1.evaluation_inputs.v3"
    diagnosis_run: DiagnosisRunResultV3

    @model_validator(mode="after")
    def validate_v3_evaluator(self) -> EvaluationInputBundleV3:
        if self.original_evaluator.version != "p1.evaluator.v5":
            raise ValueError("v3 evaluation-input bundle requires the v5 evaluator")
        return self


EvaluationInputBundleAny = Annotated[
    EvaluationInputBundle | EvaluationInputBundleV2 | EvaluationInputBundleV3,
    Field(discriminator="schema_version"),
]


class ScoringInputsIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scoring_inputs_index.v1"] = (
        SCORING_INPUTS_INDEX_SCHEMA_VERSION
    )
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    created_at: datetime
    inputs_digest: Digest
    files: dict[StrictStr, Digest]

    @model_validator(mode="after")
    def validate_files(self) -> ScoringInputsIndex:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if tuple(sorted(self.files)) != (INPUTS_FILENAME,):
            raise ValueError("index must reference exactly the evaluation inputs file")
        return self


class ArtifactInputClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    status: ArtifactInputStatus
    reasons: tuple[StrictStr, ...]
    inputs_digest: Digest | None = None

    @model_validator(mode="after")
    def validate_reasons(self) -> ArtifactInputClassification:
        if self.status is ArtifactInputStatus.RE_SCORABLE:
            if self.inputs_digest is None:
                raise ValueError("re-scorable runs must report an inputs digest")
        elif self.inputs_digest is not None:
            raise ValueError("only re-scorable runs carry an inputs digest")
        return self


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_digest(payload: BaseModel | dict[str, Any]) -> str:
    dumped = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return hashlib.sha256(_canonical_json(dumped).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise EvaluationInputsError("SCORING_INPUTS_INVALID", detail="duplicate JSON key")
        payload[key] = value
    return payload


def _parse_json_object(text: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except EvaluationInputsError:
        _error("SCORING_INPUTS_INVALID", detail="duplicate JSON key")
    except json.JSONDecodeError:
        _error("SCORING_INPUTS_INVALID", detail=f"invalid JSON in {label}")
    if not isinstance(payload, dict):
        _error("SCORING_INPUTS_INVALID", detail=f"{label} must be a JSON object")
    return payload


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _error("SCORING_INPUTS_MISSING", path.name)
    return _parse_json_object(text, path.name)


# ---------------------------------------------------------------------------
# Evaluator identity
# ---------------------------------------------------------------------------


def _normalized_source(target: Callable[..., Any]) -> str:
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        _error("SCORER_SOURCE_UNAVAILABLE", getattr(target, "__qualname__", "unknown"))
    return source.replace("\r\n", "\n").replace("\r", "\n")


def evaluator_source_digest() -> str:
    package = Path(__file__).resolve().parent
    try:
        payload = {
            name: hashlib.sha256((package / name).read_bytes()).hexdigest()
            for name in EVALUATOR_SOURCE_MODULES
        }
    except OSError:
        _error("SCORER_SOURCE_UNAVAILABLE", "package modules")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def evaluator_dependencies_digest() -> str:
    payload = {
        "python": platform.python_version(),
        "pydantic": pydantic.VERSION,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def evaluator_identity_for(scorer: Callable[..., Any], *, name: str) -> EvaluatorIdentity:
    base = evaluator_source_digest()
    default_scorer = _default_scorer()
    if scorer is default_scorer:
        source_digest = base
    else:
        source_digest = hashlib.sha256(
            _canonical_json(
                {
                    "base_source_digest": base,
                    "scorer_qualname": getattr(scorer, "__qualname__", "unknown"),
                    "scorer_source": _normalized_source(scorer),
                }
            ).encode("utf-8")
        ).hexdigest()
    return EvaluatorIdentity(
        name=name,
        version=EVALUATOR_VERSION,
        source_digest=source_digest,
        dependencies_digest=evaluator_dependencies_digest(),
    )


def _default_scorer() -> Callable[..., Any]:
    from data_incident_gym.evaluation import DeterministicEvaluator

    return DeterministicEvaluator.evaluate


def scorer_name_for(scorer: Callable[..., Any]) -> str:
    if scorer is _default_scorer():
        return "DETERMINISTIC"
    return str(getattr(scorer, "__qualname__", "CUSTOM"))


def default_evaluator_identity() -> EvaluatorIdentity:
    return evaluator_identity_for(_default_scorer(), name="DETERMINISTIC")


def require_known_evaluator(identity: EvaluatorIdentity) -> None:
    if identity.version not in KNOWN_EVALUATOR_VERSIONS:
        _error("UNKNOWN_EVALUATOR_VERSION", identity.version)


# ---------------------------------------------------------------------------
# Paths and writing
# ---------------------------------------------------------------------------


def _resolved_project_root(project_root: Path) -> Path:
    try:
        resolved = project_root.resolve(strict=True)
    except OSError:
        _error("SCORING_INPUTS_ESCAPE", "project root is not readable")
    if not resolved.is_dir():
        _error("SCORING_INPUTS_ESCAPE", "project root is not a directory")
    return resolved


def scoring_inputs_root(project_root: Path, *, create: bool) -> Path:
    resolved_root = _resolved_project_root(project_root)
    root = resolved_root / SCORING_INPUTS_DIGEST_DIRNAME / SCORING_INPUTS_DIRNAME
    if root.is_symlink():
        _error("SCORING_INPUTS_ESCAPE", "scoring inputs root must not be a symlink")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def scoring_inputs_dir(project_root: Path, run_id: str) -> Path:
    if re.fullmatch(RUN_ID_PATTERN, run_id) is None:
        _error("SCORING_INPUTS_INVALID", detail="run_id must be 32 lowercase hex characters")
    root = scoring_inputs_root(project_root, create=False)
    run_dir = root / run_id
    if run_dir.is_symlink():
        _error("SCORING_INPUTS_ESCAPE", "scoring inputs run directory must not be a symlink")
    if run_dir.resolve(strict=False).parent != root.resolve(strict=False):
        _error("SCORING_INPUTS_ESCAPE", "scoring inputs run directory escaped its root")
    return run_dir


def _verification_payload(verification: ScenarioVerification) -> VerificationPayload:
    return VerificationPayload(
        status=verification.status.value,
        incident_case_id=verification.incident_case_id,
        run_id=verification.run_id,
        dbt_exit_code=verification.dbt_exit_code,
        failed_nodes=tuple(verification.failed_nodes),
        skipped_nodes=tuple(verification.skipped_nodes),
        affected_assets=tuple(verification.affected_assets),
        schema_fingerprint=verification.schema_fingerprint,
        profile_spec_sha256=verification.profile_spec_sha256,
    )


def verification_from_payload(payload: VerificationPayload) -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus(payload.status),
        incident_case_id=payload.incident_case_id,
        run_id=payload.run_id,
        dbt_exit_code=payload.dbt_exit_code,
        failed_nodes=tuple(payload.failed_nodes),
        skipped_nodes=tuple(payload.skipped_nodes),
        affected_assets=tuple(payload.affected_assets),
        schema_fingerprint=payload.schema_fingerprint,
        profile_spec_sha256=payload.profile_spec_sha256,
    )


def build_evaluation_input_bundle(
    *,
    scenario: ScenarioSpec,
    verification: ScenarioVerification,
    diagnosis_run: DiagnosisRunResultAny,
    recovery: RecoveryProof,
    artifact_dir: Path,
    budget: BudgetSummary,
    evaluator: EvaluatorIdentity,
) -> EvaluationInputBundleAny:
    run_id = diagnosis_run.diagnosis.run_id
    if artifact_dir.name != run_id:
        _error("SCORING_INPUTS_INVALID", detail="artifact directory does not match run_id")
    if artifact_dir.is_symlink() or not artifact_dir.is_dir():
        _error("SCORING_INPUTS_INVALID", detail="artifact directory is not readable")
    try:
        trace_lines = (artifact_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        trace_envelopes = tuple(
            validate_trace_envelope(
                _parse_json_object(line, "trace.jsonl"),
                expected_schema_version=trace_schema_for_run(diagnosis_run),
            )
            for line in trace_lines
            if line
        )
    except Exception:
        _error("SCORING_INPUTS_INVALID", detail="trace version does not match diagnosis run")
    if (
        not trace_envelopes
        or any(item.sequence != index for index, item in enumerate(trace_envelopes, start=1))
        or not isinstance(trace_envelopes[-1].event, DiagnosisTerminalTraceEvent)
    ):
        _error("SCORING_INPUTS_INVALID", detail="trace envelope sequence is invalid")
    digests: list[ArtifactDigest] = []
    for name in ARTIFACT_FILENAMES:
        path = artifact_dir / name
        if not path.is_file():
            _error("ARTIFACT_BUNDLE_INCOMPLETE", name)
        digests.append(ArtifactDigest(name=name, sha256=_file_sha256(path)))
    verification_payload = _verification_payload(verification)
    bundle_model = (
        EvaluationInputBundleV3
        if isinstance(diagnosis_run, DiagnosisRunResultV3)
        else EvaluationInputBundleV2
        if isinstance(diagnosis_run, DiagnosisRunResultV2)
        else EvaluationInputBundle
    )
    return bundle_model(
        run_id=run_id,
        incident_case_id=scenario.incident_case_id,
        strategy=diagnosis_run.strategy,
        scenario=scenario,
        scenario_digest=scenario.digest(),
        verification=verification_payload,
        verification_digest=_payload_digest(verification_payload),
        diagnosis_run=diagnosis_run,
        diagnosis_run_digest=diagnosis_run.digest(),
        recovery=recovery,
        budget=budget,
        original_evaluator=evaluator,
        artifact_digests=tuple(digests),
    )


def rename_directory_with_retry(temporary: Path, final: Path) -> None:
    """Rename a freshly written bundle directory, tolerating Windows handles.

    On Windows a just-written directory can fail to rename with
    ``PermissionError`` while a transient handle is held; a probe on this
    repository measured ~1% of renames failing once and succeeding later. The
    code cannot tell in advance which occurrence is transient, so every
    ``PermissionError`` is retried a bounded number of times; any other OSError
    keeps its immediate, honest failure.
    """

    for attempt in range(_RENAME_ATTEMPTS):
        try:
            temporary.rename(final)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(_RENAME_BACKOFF_SECONDS)


def write_evaluation_input_bundle(
    project_root: Path,
    bundle: EvaluationInputBundleAny,
    *,
    created_at: datetime,
) -> Path:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        _error("SCORING_INPUTS_INVALID", detail="created_at must be timezone-aware")
    root = scoring_inputs_root(project_root, create=True)
    final = root / bundle.run_id
    if final.is_symlink() or final.exists():
        _error("SCORING_INPUTS_EXISTS", bundle.run_id)
    inputs_payload = (
        json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    index = ScoringInputsIndex(
        run_id=bundle.run_id,
        created_at=created_at,
        inputs_digest=bundle.inputs_digest(),
        files={
            INPUTS_FILENAME: hashlib.sha256(inputs_payload.encode("utf-8")).hexdigest()
        },
    )
    payloads = {
        INPUTS_FILENAME: inputs_payload,
        INDEX_FILENAME: index.model_dump_json(indent=2) + "\n",
    }
    temporary = root / f".{bundle.run_id}.tmp"
    try:
        temporary.mkdir(exist_ok=False)
    except OSError:
        _error("SCORING_INPUTS_WRITE_FAILED", bundle.run_id)
    try:
        for name, payload in payloads.items():
            (temporary / name).write_text(payload, encoding="utf-8", newline="")
        if final.is_symlink() or final.exists():
            _error("SCORING_INPUTS_EXISTS", bundle.run_id)
        rename_directory_with_retry(temporary, final)
    except EvaluationInputsError:
        raise
    except OSError:
        _error("SCORING_INPUTS_WRITE_FAILED", bundle.run_id)
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink(missing_ok=True)
            temporary.rmdir()
    return final


# ---------------------------------------------------------------------------
# Loading and classification
# ---------------------------------------------------------------------------


def _restore_typed_kernel_state(run: DiagnosisRunResultAny) -> DiagnosisRunResultAny:
    if run.strategy not in KERNEL_STRATEGIES:
        if run.kernel_state is not None:
            _error("SCORING_INPUTS_INVALID", detail="static run must not carry kernel state")
        return run
    if run.kernel_state is None:
        _error("SCORING_INPUTS_INVALID", detail="kernel run is missing its terminal state")
    try:
        state = InvestigationState.model_validate(run.kernel_state)
    except ValidationError:
        _error("SCORING_INPUTS_INVALID", detail="kernel state failed typed restoration")
    raw = run.model_dump(mode="python")
    trace = tuple(
        (
            KernelStateTraceEvent(event_type="KERNEL_STATE", state=state)
            if event.get("event_type") == "KERNEL_STATE"
            else event
        )
        for event in raw["trace"]
    )
    try:
        run_model = (
            DiagnosisRunResultV3
            if isinstance(run, DiagnosisRunResultV3)
            else DiagnosisRunResultV2
            if isinstance(run, DiagnosisRunResultV2)
            else DiagnosisRunResult
        )
        return run_model.model_validate({**raw, "kernel_state": state, "trace": trace})
    except ValidationError:
        _error("SCORING_INPUTS_INVALID", detail="kernel state does not match the trace")


def _restore_typed_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick the diagnosis contract by its own ``schema_version`` marker.

    The v2 diagnosis (T13) declares gap kinds outside the frozen v1
    vocabulary, so revalidating a persisted v2 run's payload against
    ``Diagnosis`` would refuse the run's own scoring inputs (certification
    stop, round 3). The marker is the contract's own declaration, never a
    content guess; anything else keeps the v1 path.
    """

    diagnosis_payload = payload.get("diagnosis_run", {}).get("diagnosis")
    if not isinstance(diagnosis_payload, dict):
        return payload
    if diagnosis_payload.get("schema_version") != "p1.diagnosis.v2":
        return payload
    return {
        **payload,
        "diagnosis_run": {
            **payload["diagnosis_run"],
            "diagnosis": DiagnosisV2.model_validate(diagnosis_payload),
        },
    }


def _finalize_bundle(payload: dict[str, Any]) -> EvaluationInputBundleAny:
    schema_version = payload.get("schema_version")
    if schema_version == "p1.evaluation_inputs.v1":
        bundle_model = EvaluationInputBundle
    elif schema_version == "p1.evaluation_inputs.v2":
        bundle_model = EvaluationInputBundleV2
    elif schema_version == "p1.evaluation_inputs.v3":
        bundle_model = EvaluationInputBundleV3
    else:
        _error("SCORING_INPUTS_INVALID", detail="unsupported bundle schema")
    try:
        bundle = bundle_model.model_validate(_restore_typed_diagnosis(payload))
    except ValidationError:
        _error("SCORING_INPUTS_INVALID", detail="bundle failed schema validation")
    require_known_evaluator(bundle.original_evaluator)
    restored_run = _restore_typed_kernel_state(bundle.diagnosis_run)
    if restored_run is not bundle.diagnosis_run:
        bundle = bundle.model_copy(update={"diagnosis_run": restored_run})
    if bundle.diagnosis_run_digest != bundle.diagnosis_run.digest():
        _error("SCORING_INPUTS_INVALID", detail="diagnosis digest changed after restoration")
    return bundle


def load_evaluation_input_bundle(project_root: Path, run_id: str) -> EvaluationInputBundleAny:
    run_dir = scoring_inputs_dir(project_root, run_id)
    if not run_dir.is_dir():
        _error("SCORING_INPUTS_MISSING", run_id)
    index = _load_json_object(run_dir / INDEX_FILENAME)
    try:
        parsed_index = ScoringInputsIndex.model_validate(index)
    except ValidationError:
        _error("SCORING_INPUTS_INVALID", detail="index failed schema validation")
    if parsed_index.run_id != run_id:
        _error("SCORING_INPUTS_INVALID", detail="index run_id does not match the directory")
    for name, expected in parsed_index.files.items():
        if name != INPUTS_FILENAME or "/" in name or "\\" in name or ".." in name:
            _error("SCORING_INPUTS_ESCAPE", name)
        path = run_dir / name
        if path.is_symlink() or not path.is_file():
            _error("SCORING_INPUTS_MISSING", name)
        if _file_sha256(path) != expected:
            _error("SCORING_INPUTS_INVALID", detail=f"{name} does not match its digest")
    payload = _load_json_object(run_dir / INPUTS_FILENAME)
    bundle = _finalize_bundle(payload)
    if bundle.run_id != run_id:
        _error(
            "SCORING_INPUTS_INVALID",
            detail="bundle run_id does not match the requested run",
        )
    if bundle.inputs_digest() != parsed_index.inputs_digest:
        _error("SCORING_INPUTS_INVALID", detail="inputs digest does not match the index")
    return bundle


def load_archived_evaluation(
    project_root: Path,
    bundle: EvaluationInputBundleAny,
) -> tuple[EvaluationResult | None, str | None]:
    """Return the archived ``EvaluationResult`` and a fixed unavailable reason."""

    recorded = {item.name: item.sha256 for item in bundle.artifact_digests}
    path = _resolved_project_root(project_root) / "artifacts" / bundle.run_id / "evaluation.json"
    if not path.is_file() or path.is_symlink():
        return None, "ARCHIVED_EVALUATION_MISSING"
    if _file_sha256(path) != recorded.get("evaluation.json"):
        return None, "ARCHIVED_EVALUATION_DIGEST_MISMATCH"
    payload = _load_json_object(path)
    try:
        return EvaluationResult.model_validate(payload), None
    except ValidationError:
        return None, "ARCHIVED_EVALUATION_INVALID"


def classify_scoring_inputs(project_root: Path, run_id: str) -> ArtifactInputClassification:
    artifact_dir = _resolved_project_root(project_root) / "artifacts" / run_id
    run_dir = scoring_inputs_dir(project_root, run_id)
    if not run_dir.is_dir():
        if not artifact_dir.is_dir():
            return ArtifactInputClassification(
                run_id=run_id,
                status=ArtifactInputStatus.NOT_RE_SCORABLE,
                reasons=("ARTIFACT_RUN_MISSING",),
            )
        missing = tuple(
            name for name in ARTIFACT_FILENAMES if not (artifact_dir / name).is_file()
        )
        if missing:
            return ArtifactInputClassification(
                run_id=run_id,
                status=ArtifactInputStatus.NOT_RE_SCORABLE,
                reasons=("ARTIFACT_BUNDLE_INCOMPLETE", *missing),
            )
        return ArtifactInputClassification(
            run_id=run_id,
            status=ArtifactInputStatus.PARTIAL_ANALYSIS,
            reasons=("SCORING_INPUTS_MISSING", "LEGACY_ARTIFACTS_READABLE"),
        )
    try:
        bundle = load_evaluation_input_bundle(project_root, run_id)
    except EvaluationInputsError as error:
        return ArtifactInputClassification(
            run_id=run_id,
            status=ArtifactInputStatus.NOT_RE_SCORABLE,
            reasons=("SCORING_INPUTS_INVALID", error.code),
        )
    return ArtifactInputClassification(
        run_id=run_id,
        status=ArtifactInputStatus.RE_SCORABLE,
        reasons=("SCORING_INPUTS_VERIFIED",),
        inputs_digest=bundle.inputs_digest(),
    )


def export_scoring_inputs(project_root: Path, run_id: str, destination: Path) -> Path:
    load_evaluation_input_bundle(project_root, run_id)
    run_dir = scoring_inputs_dir(project_root, run_id)
    index = _load_json_object(run_dir / INDEX_FILENAME)
    try:
        inputs_text = (run_dir / INPUTS_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _error("SCORING_INPUTS_MISSING", INPUTS_FILENAME)
    exported = {
        "schema_version": SCORING_INPUTS_EXPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": index["created_at"],
        "index": index,
        "inputs": inputs_text,
    }
    target = Path(destination)
    if target.is_symlink() or target.exists():
        _error("SCORING_INPUTS_EXISTS", str(target))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(exported, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )
    except OSError:
        _error("SCORING_INPUTS_WRITE_FAILED", str(target))
    return target


def load_scoring_inputs_export(path: Path) -> EvaluationInputBundleAny:
    payload = _load_json_object(Path(path))
    if payload.get("schema_version") != SCORING_INPUTS_EXPORT_SCHEMA_VERSION:
        _error("SCORING_INPUTS_INVALID", detail="unsupported export schema")
    index = payload.get("index")
    inputs_text = payload.get("inputs")
    if not isinstance(index, dict) or not isinstance(inputs_text, str):
        _error("SCORING_INPUTS_INVALID", detail="export is missing index or inputs")
    try:
        parsed_index = ScoringInputsIndex.model_validate(index)
    except ValidationError:
        _error("SCORING_INPUTS_INVALID", detail="export index failed schema validation")
    if payload.get("run_id") != parsed_index.run_id:
        _error("SCORING_INPUTS_INVALID", detail="export run_id does not match the index")
    file_digest = hashlib.sha256(inputs_text.encode("utf-8")).hexdigest()
    if parsed_index.files.get(INPUTS_FILENAME) != file_digest:
        _error("SCORING_INPUTS_INVALID", detail="export file digest does not match")
    bundle = _finalize_bundle(_parse_json_object(inputs_text, INPUTS_FILENAME))
    if bundle.run_id != parsed_index.run_id:
        _error("SCORING_INPUTS_INVALID", detail="export bundle run_id does not match")
    if bundle.inputs_digest() != parsed_index.inputs_digest:
        _error("SCORING_INPUTS_INVALID", detail="export inputs digest does not match")
    return bundle


__all__ = [
    "ArtifactDigest",
    "ArtifactInputClassification",
    "ArtifactInputStatus",
    "EvaluatorIdentity",
    "EvaluationInputBundle",
    "EvaluationInputBundleAny",
    "EvaluationInputBundleV2",
    "EvaluationInputBundleV3",
    "EvaluationInputsError",
    "INDEX_FILENAME",
    "INPUTS_FILENAME",
    "KNOWN_EVALUATOR_VERSIONS",
    "RecoveryProof",
    "ScoringInputsIndex",
    "VerificationPayload",
    "build_evaluation_input_bundle",
    "classify_scoring_inputs",
    "default_evaluator_identity",
    "evaluator_dependencies_digest",
    "evaluator_identity_for",
    "evaluator_source_digest",
    "export_scoring_inputs",
    "load_archived_evaluation",
    "load_evaluation_input_bundle",
    "load_scoring_inputs_export",
    "require_known_evaluator",
    "scorer_name_for",
    "scoring_inputs_dir",
    "scoring_inputs_root",
    "verification_from_payload",
    "write_evaluation_input_bundle",
]
