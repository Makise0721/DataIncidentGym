"""Read-only offline scoring of sealed evaluation inputs (T03).

``score_run_offline`` loads a verified scoring-input attachment, replays the
deterministic evaluator without touching the model, the database, dbt or the
network, and writes a derived score under
``artifacts/rescores/<run_id>/<score_id>/``. The original six-file bundle,
manifest, ledger and reports are never modified, no score is edited in place,
and the archived round is never re-run automatically.

The derived ``score_id`` is attested: it hashes the input digest, the scorer
source and dependency digests, and the scoring configuration, so a relabelled
or edited scorer cannot reuse a previous identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from data_incident_gym.diagnosis import RUN_ID_PATTERN
from data_incident_gym.evaluation import (
    ControllerCheck,
    ControllerCheckCode,
    DeterministicEvaluator,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_inputs import (
    EvaluationInputsError,
    EvaluatorIdentity,
    evaluator_identity_for,
    load_archived_evaluation,
    load_evaluation_input_bundle,
    rename_directory_with_retry,
    verification_from_payload,
)

OFFLINE_SCORE_SCHEMA_VERSION = "p1.offline_score.v1"
SCORING_CONFIG_SCHEMA_VERSION = "p1.scoring_config.v1"
OFFLINE_SCORE_PROVENANCE_SCHEMA_VERSION = "p1.offline_score.provenance.v1"
SCORE_DIFF_SCHEMA_VERSION = "p1.score_diff.v1"
SCORE_COMPARISON_SCHEMA_VERSION = "p1.score_comparison.v1"
OFFLINE_SCORER_NAME = "DETERMINISTIC"
RESCORES_DIRNAME = "rescores"
PROVENANCE_FILENAME = "provenance.json"
SCORE_EVALUATION_FILENAME = "evaluation.json"
DIFF_FILENAME = "diff.json"
REPORT_FILENAME = "report.md"
DERIVED_FILENAMES = (
    SCORE_EVALUATION_FILENAME,
    DIFF_FILENAME,
    REPORT_FILENAME,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"

ChangeKind = Literal[
    "UNCHANGED",
    "PASSED_TO_FAILED",
    "FAILED_TO_PASSED",
    "APPLICABILITY_CHANGED",
    "ADDED",
    "REMOVED",
]


class OfflineScoreError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


def _error(code: str, detail: str | None = None) -> NoReturn:
    raise OfflineScoreError(code, detail=detail)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ScoringConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.scoring_config.v1"] = SCORING_CONFIG_SCHEMA_VERSION
    scorer: StrictStr = Field(min_length=1)


class OfflineScoreProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.offline_score.provenance.v1"] = (
        OFFLINE_SCORE_PROVENANCE_SCHEMA_VERSION
    )
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    score_id: StrictStr = Field(pattern=_SHA256_PATTERN)
    inputs_digest: StrictStr = Field(pattern=_SHA256_PATTERN)
    scorer: EvaluatorIdentity
    original_evaluator: EvaluatorIdentity
    source_evaluation_status: StrictStr | None = None
    scoring_config: ScoringConfig
    files: dict[StrictStr, StrictStr]
    created_at: datetime

    @model_validator(mode="after")
    def validate_created_at_and_files(self) -> OfflineScoreProvenance:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if tuple(sorted(self.files)) != tuple(sorted(DERIVED_FILENAMES)):
            raise ValueError("provenance must cover exactly the derived score files")
        for digest in self.files.values():
            if re.fullmatch(_SHA256_PATTERN, digest) is None:
                raise ValueError("derived file digests must be SHA-256 hex")
        return self


class CheckChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: StrictStr
    kind: Literal["EVIDENCE", "CONTROLLER"]
    change: ChangeKind
    details_changed: StrictBool = False
    before_applicability: StrictStr | None = None
    after_applicability: StrictStr | None = None
    before_passed: StrictBool | None = None
    after_passed: StrictBool | None = None
    before_expected: tuple[StrictStr, ...] | None = None
    after_expected: tuple[StrictStr, ...] | None = None
    before_actual: tuple[StrictStr, ...] | None = None
    after_actual: tuple[StrictStr, ...] | None = None

    @model_validator(mode="after")
    def validate_presence(self) -> CheckChange:
        for label, passed, expected, actual in (
            ("before", self.before_passed, self.before_expected, self.before_actual),
            ("after", self.after_passed, self.after_expected, self.after_actual),
        ):
            if (passed is None) != (expected is None) or (passed is None) != (actual is None):
                raise ValueError(f"{label} fields must be present together")
        if self.change in {"ADDED", "REMOVED"}:
            if self.before_passed is None and self.after_passed is None:
                raise ValueError("added or removed checks need one side present")
            if self.details_changed:
                raise ValueError("added or removed checks compare no details")
        return self


class ScoreDiff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.score_diff.v1"] = SCORE_DIFF_SCHEMA_VERSION
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    score_id: StrictStr = Field(pattern=_SHA256_PATTERN)
    compared_to: Literal["ARCHIVED_EVALUATION"] = "ARCHIVED_EVALUATION"
    available: StrictBool
    unavailable_reason: StrictStr | None = None
    before_status: StrictStr | None = None
    after_status: StrictStr
    changes: tuple[CheckChange, ...]


class OfflineScoreResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    score_id: StrictStr = Field(pattern=_SHA256_PATTERN)
    score_dir: Path
    evaluation: EvaluationResult
    diff: ScoreDiff
    created: StrictBool

    @model_validator(mode="after")
    def validate_identity(self) -> OfflineScoreResult:
        if self.score_dir.name != self.score_id:
            raise ValueError("score directory must be named after the score_id")
        if self.evaluation.run_id != self.run_id:
            raise ValueError("score evaluation must match the run")
        if self.diff.score_id != self.score_id or self.diff.run_id != self.run_id:
            raise ValueError("score diff must match the score identity")
        return self

    @property
    def status(self) -> EvaluationStatus:
        return self.evaluation.status

    @property
    def changed_check_codes(self) -> tuple[str, ...]:
        return tuple(
            item.code
            for item in self.diff.changes
            if item.change != "UNCHANGED" or item.details_changed
        )


class ScoreComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.score_comparison.v1"] = SCORE_COMPARISON_SCHEMA_VERSION
    run_id: StrictStr = Field(pattern=RUN_ID_PATTERN)
    score_id_a: StrictStr = Field(pattern=_SHA256_PATTERN)
    score_id_b: StrictStr = Field(pattern=_SHA256_PATTERN)
    status_a: StrictStr
    status_b: StrictStr
    scorer_a: EvaluatorIdentity
    scorer_b: EvaluatorIdentity
    changes: tuple[CheckChange, ...]

    @property
    def changed_check_codes(self) -> tuple[str, ...]:
        return tuple(
            item.code
            for item in self.changes
            if item.change != "UNCHANGED" or item.details_changed
        )


def derive_score_id(
    inputs_digest: str,
    scorer: EvaluatorIdentity,
    config: ScoringConfig,
) -> str:
    payload = {
        "schema_version": OFFLINE_SCORE_SCHEMA_VERSION,
        "inputs_digest": inputs_digest,
        "scorer": scorer.model_dump(mode="json"),
        "scoring_config": config.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _resolved_project_root(project_root: Path) -> Path:
    try:
        resolved = project_root.resolve(strict=True)
    except OSError:
        _error("OFFLINE_SCORE_SETUP_FAILED", "project root is not readable")
    if not resolved.is_dir():
        _error("OFFLINE_SCORE_SETUP_FAILED", "project root is not a directory")
    return resolved


def _rescores_root(project_root: Path, *, create: bool) -> Path:
    root = _resolved_project_root(project_root) / "artifacts" / RESCORES_DIRNAME
    if root.is_symlink():
        _error("OFFLINE_SCORE_SETUP_FAILED", "rescore root must not be a symlink")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _run_root(project_root: Path, run_id: str, *, create: bool) -> Path:
    if re.fullmatch(RUN_ID_PATTERN, run_id) is None:
        _error("OFFLINE_SCORE_SETUP_FAILED", "run_id must be 32 lowercase hex characters")
    root = _rescores_root(project_root, create=create)
    run_root = root / run_id
    if run_root.is_symlink():
        _error("OFFLINE_SCORE_SETUP_FAILED", "rescore run directory must not be a symlink")
    if run_root.resolve(strict=False).parent != root.resolve(strict=False):
        _error("OFFLINE_SCORE_SETUP_FAILED", "rescore run directory escaped its root")
    if create:
        run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def _check_change(
    before: EvaluationCheck | ControllerCheck | None,
    after: EvaluationCheck | ControllerCheck | None,
) -> ChangeKind:
    if before is None:
        return "ADDED"
    if after is None:
        return "REMOVED"
    before_applicability = getattr(before, "applicability", None)
    after_applicability = getattr(after, "applicability", None)
    if (
        before_applicability is not None
        and after_applicability is not None
        and before_applicability.value != after_applicability.value
    ):
        return "APPLICABILITY_CHANGED"
    if before.passed == after.passed:
        return "UNCHANGED"
    return "PASSED_TO_FAILED" if before.passed else "FAILED_TO_PASSED"


def _details_changed(
    before: EvaluationCheck | ControllerCheck | None,
    after: EvaluationCheck | ControllerCheck | None,
) -> bool:
    if before is None or after is None:
        return False
    return tuple(before.expected) != tuple(after.expected) or tuple(before.actual) != tuple(
        after.actual
    )


def _evidence_changes(
    before: EvaluationResult | None,
    after: EvaluationResult,
) -> list[CheckChange]:
    changes: list[CheckChange] = []
    index_before = (
        {check.code: check for check in before.checks} if before is not None else {}
    )
    index_after = {check.code: check for check in after.checks}
    for code in EvaluationCheckCode:
        old = index_before.get(code)
        new = index_after.get(code)
        if old is None and new is None:
            continue
        changes.append(
            CheckChange(
                code=code.value,
                kind="EVIDENCE",
                change=_check_change(old, new),
                details_changed=_details_changed(old, new),
                before_applicability=old.applicability.value if old is not None else None,
                after_applicability=new.applicability.value if new is not None else None,
                before_passed=old.passed if old is not None else None,
                after_passed=new.passed if new is not None else None,
                before_expected=tuple(old.expected) if old is not None else None,
                after_expected=tuple(new.expected) if new is not None else None,
                before_actual=tuple(old.actual) if old is not None else None,
                after_actual=tuple(new.actual) if new is not None else None,
            )
        )
    return changes


def _controller_changes(
    before: EvaluationResult | None,
    after: EvaluationResult,
) -> list[CheckChange]:
    changes: list[CheckChange] = []
    index_before = (
        {check.code: check for check in before.controller_checks}
        if before is not None
        else {}
    )
    index_after = {check.code: check for check in after.controller_checks}
    for code in ControllerCheckCode:
        old = index_before.get(code)
        new = index_after.get(code)
        if old is None and new is None:
            continue
        changes.append(
            CheckChange(
                code=code.value,
                kind="CONTROLLER",
                change=_check_change(old, new),
                details_changed=_details_changed(old, new),
                before_passed=old.passed if old is not None else None,
                after_passed=new.passed if new is not None else None,
                before_expected=tuple(old.expected) if old is not None else None,
                after_expected=tuple(new.expected) if new is not None else None,
                before_actual=tuple(old.actual) if old is not None else None,
                after_actual=tuple(new.actual) if new is not None else None,
            )
        )
    return changes


def _build_diff(
    run_id: str,
    score_id: str,
    evaluation: EvaluationResult,
    archived: EvaluationResult | None,
    unavailable_reason: str | None,
) -> ScoreDiff:
    if archived is None:
        return ScoreDiff(
            run_id=run_id,
            score_id=score_id,
            available=False,
            unavailable_reason=unavailable_reason or "ARCHIVED_EVALUATION_MISSING",
            before_status=None,
            after_status=evaluation.status.value,
            changes=(),
        )
    return ScoreDiff(
        run_id=run_id,
        score_id=score_id,
        available=True,
        before_status=archived.status.value,
        after_status=evaluation.status.value,
        changes=(
            *_evidence_changes(archived, evaluation),
            *_controller_changes(archived, evaluation),
        ),
    )


def _render_report(
    *,
    run_id: str,
    score_id: str,
    inputs_digest: str,
    scorer: EvaluatorIdentity,
    original_evaluator: EvaluatorIdentity,
    created_at: datetime,
    evaluation: EvaluationResult,
    diff: ScoreDiff,
) -> str:
    if not diff.available:
        diff_block = f"- 无法与归档评分比较（{diff.unavailable_reason}）"
    else:
        changed = tuple(
            f"- {item.code} ({item.kind}): {item.change}"
            + ("；expected/actual 变化" if item.details_changed else "")
            for item in diff.changes
            if item.change != "UNCHANGED" or item.details_changed
        )
        diff_block = "\n".join(changed) if changed else "- 无（与原评分逐项一致）"
    before = diff.before_status or "UNAVAILABLE"
    reason = f"- 不可对照原因：{diff.unavailable_reason}\n" if not diff.available else ""
    return (
        "# DataIncidentGym 离线重评报告（派生）\n"
        "\n"
        "## 身份\n"
        "\n"
        f"- 运行：{run_id}\n"
        f"- score_id：{score_id}\n"
        f"- inputs_digest：{inputs_digest}\n"
        f"- scorer：{scorer.name} @ {scorer.version}\n"
        f"- scorer 源码摘要：{scorer.source_digest}\n"
        f"- 原 evaluator：{original_evaluator.name} @ {original_evaluator.version}\n"
        f"- 评分时间：{created_at.isoformat()}\n"
        "\n"
        "## 结果\n"
        "\n"
        f"- 原归档状态：{before}\n"
        f"- 重算状态：{evaluation.status.value}\n"
        f"{reason}"
        "\n"
        "## 逐项变更\n"
        "\n"
        f"{diff_block}\n"
        "\n"
        "## 边界声明\n"
        "\n"
        "本文件是只读重评的派生结果，不修改原始六文件、manifest、ledger 或旧报告，\n"
        "也不改变原批次的结论或有效性判定。\n"
    )


def _write_score_dir(final: Path, payloads: dict[str, str]) -> None:
    parent = final.parent
    temporary = parent / f".{final.name}.tmp"
    try:
        temporary.mkdir(exist_ok=False)
    except OSError:
        _error("OFFLINE_SCORE_WRITE_FAILED", str(final))
    try:
        for name, payload in payloads.items():
            (temporary / name).write_text(payload, encoding="utf-8", newline="")
        if final.is_symlink() or final.exists():
            _error("OFFLINE_SCORE_EXISTS", str(final))
        # One shared bounded retry: Windows can transiently refuse a directory
        # rename while a handle is held (measured on this repository).
        rename_directory_with_retry(temporary, final)
    except OfflineScoreError:
        raise
    except OSError:
        _error("OFFLINE_SCORE_WRITE_FAILED", str(final))
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink(missing_ok=True)
            temporary.rmdir()


def _load_score_dir(
    project_root: Path,
    run_id: str,
    score_id: str,
) -> tuple[OfflineScoreProvenance, EvaluationResult, ScoreDiff]:
    if re.fullmatch(_SHA256_PATTERN, score_id) is None:
        _error("OFFLINE_SCORE_SETUP_FAILED", "score_id must be a 64-hex digest")
    directory = _rescores_root(project_root, create=False) / run_id / score_id
    if directory.is_symlink() or not directory.is_dir():
        _error("OFFLINE_SCORE_NOT_FOUND", f"{run_id}/{score_id[:12]}")
    if directory.resolve(strict=False).parent != (
        _rescores_root(project_root, create=False) / run_id
    ).resolve(strict=False):
        _error("OFFLINE_SCORE_SETUP_FAILED", "score directory escaped its run root")
    try:
        provenance = OfflineScoreProvenance.model_validate_json(
            (directory / PROVENANCE_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError):
        _error("OFFLINE_SCORE_DIR_INVALID", score_id[:12])
    if provenance.score_id != score_id or provenance.run_id != run_id:
        _error("OFFLINE_SCORE_DIR_INVALID", "provenance identity mismatch")
    for name, expected in provenance.files.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            _error("OFFLINE_SCORE_DIR_INVALID", f"{name} is missing")
        if _file_sha256(path) != expected:
            _error("OFFLINE_SCORE_DIR_INVALID", f"{name} does not match its digest")
    try:
        evaluation = EvaluationResult.model_validate_json(
            (directory / SCORE_EVALUATION_FILENAME).read_text(encoding="utf-8")
        )
        diff = ScoreDiff.model_validate_json(
            (directory / DIFF_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError):
        _error("OFFLINE_SCORE_DIR_INVALID", score_id[:12])
    if evaluation.run_id != run_id:
        _error("OFFLINE_SCORE_DIR_INVALID", "evaluation run_id mismatch")
    if diff.run_id != run_id or diff.score_id != score_id:
        _error("OFFLINE_SCORE_DIR_INVALID", "diff identity mismatch")
    if diff.after_status != evaluation.status.value:
        _error("OFFLINE_SCORE_DIR_INVALID", "diff status does not match the evaluation")
    return provenance, evaluation, diff


def score_run_offline(
    project_root: Path,
    run_id: str,
    *,
    scorer: Callable[..., EvaluationResult] | None = None,
    scorer_name: str | None = None,
    now: datetime | None = None,
) -> OfflineScoreResult:
    """Score one sealed run offline; never touches model, database or dbt."""

    bundle = load_evaluation_input_bundle(project_root, run_id)
    selected = scorer if scorer is not None else DeterministicEvaluator.evaluate
    name = (
        scorer_name
        if scorer_name is not None
        else (
            OFFLINE_SCORER_NAME
            if scorer is None
            else getattr(selected, "__qualname__", "CUSTOM")
        )
    )
    if not isinstance(name, str) or not name:
        _error("OFFLINE_SCORE_SETUP_FAILED", "scorer name is invalid")
    identity = evaluator_identity_for(selected, name=name)
    config = ScoringConfig(scorer=name)
    inputs_digest = bundle.inputs_digest()
    score_id = derive_score_id(inputs_digest, identity, config)
    run_root = _run_root(project_root, run_id, create=True)
    final = run_root / score_id
    if final.is_symlink():
        _error("OFFLINE_SCORE_SETUP_FAILED", "score directory must not be a symlink")
    if final.exists():
        provenance, evaluation, diff = _load_score_dir(project_root, run_id, score_id)
        if provenance.inputs_digest != inputs_digest:
            _error("OFFLINE_SCORE_DIR_INVALID", "inputs digest mismatch")
        return OfflineScoreResult(
            run_id=run_id,
            score_id=score_id,
            score_dir=final,
            evaluation=evaluation,
            diff=diff,
            created=False,
        )

    try:
        evaluation = selected(
            bundle.scenario,
            verification_from_payload(bundle.verification),
            bundle.diagnosis_run,
            recovery_succeeded=bundle.recovery.recovered,
        )
    except Exception:
        _error("OFFLINE_SCORE_FAILED", name)
    if not isinstance(evaluation, EvaluationResult):
        _error("OFFLINE_SCORE_FAILED", "scorer returned an unexpected shape")

    archived, reason = _load_archived_evaluation(project_root, bundle)
    diff = _build_diff(run_id, score_id, evaluation, archived, reason)
    created_at = now or datetime.now(UTC)
    payloads = {
        SCORE_EVALUATION_FILENAME: evaluation.model_dump_json(indent=2) + "\n",
        DIFF_FILENAME: diff.model_dump_json(indent=2) + "\n",
        REPORT_FILENAME: _render_report(
            run_id=run_id,
            score_id=score_id,
            inputs_digest=inputs_digest,
            scorer=identity,
            original_evaluator=bundle.original_evaluator,
            created_at=created_at,
            evaluation=evaluation,
            diff=diff,
        ),
    }
    provenance = OfflineScoreProvenance(
        run_id=run_id,
        score_id=score_id,
        inputs_digest=inputs_digest,
        scorer=identity,
        original_evaluator=bundle.original_evaluator,
        source_evaluation_status=archived.status.value if archived is not None else None,
        scoring_config=config,
        files={
            name: hashlib.sha256(payload.encode("utf-8")).hexdigest()
            for name, payload in payloads.items()
        },
        created_at=created_at,
    )
    _write_score_dir(
        final,
        {
            PROVENANCE_FILENAME: provenance.model_dump_json(indent=2) + "\n",
            **payloads,
        },
    )
    return OfflineScoreResult(
        run_id=run_id,
        score_id=score_id,
        score_dir=final,
        evaluation=evaluation,
        diff=diff,
        created=True,
    )


def _load_archived_evaluation(
    project_root: Path,
    bundle: Any,
) -> tuple[EvaluationResult | None, str | None]:
    try:
        return load_archived_evaluation(project_root, bundle)
    except EvaluationInputsError as error:
        return None, f"ARCHIVED_EVALUATION_UNAVAILABLE:{error.code}"


def compare_offline_scores(
    project_root: Path,
    run_id: str,
    score_id_a: str,
    score_id_b: str,
) -> ScoreComparison:
    provenance_a, evaluation_a, _ = _load_score_dir(project_root, run_id, score_id_a)
    provenance_b, evaluation_b, _ = _load_score_dir(project_root, run_id, score_id_b)
    changes = (
        *_evidence_changes(evaluation_a, evaluation_b),
        *_controller_changes(evaluation_a, evaluation_b),
    )
    return ScoreComparison(
        run_id=run_id,
        score_id_a=provenance_a.score_id,
        score_id_b=provenance_b.score_id,
        status_a=evaluation_a.status.value,
        status_b=evaluation_b.status.value,
        scorer_a=provenance_a.scorer,
        scorer_b=provenance_b.scorer,
        changes=changes,
    )


__all__ = [
    "DIFF_FILENAME",
    "OFFLINE_SCORER_NAME",
    "OfflineScoreError",
    "OfflineScoreProvenance",
    "OfflineScoreResult",
    "PROVENANCE_FILENAME",
    "REPORT_FILENAME",
    "RESCORES_DIRNAME",
    "SCORE_EVALUATION_FILENAME",
    "ScoreComparison",
    "ScoreDiff",
    "ScoringConfig",
    "compare_offline_scores",
    "derive_score_id",
    "score_run_offline",
]
