from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from data_incident_gym.artifacts import (
    ArtifactRun,
    ArtifactWriter,
    BudgetSummary,
    RecoveryStatus,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import RUN_ID_PATTERN, DiagnosisRunResult, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
    DiagnosisRunner,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_inputs import (
    RecoveryProof,
    build_evaluation_input_bundle,
    evaluator_identity_for,
    scorer_name_for,
    write_evaluation_input_bundle,
)
from data_incident_gym.lab import IncidentLab, ScenarioRun
from data_incident_gym.lab_verifier import ScenarioVerification
from data_incident_gym.scenarios import ScenarioSpec, load_scenario_spec
from data_incident_gym.submission_policy import SubmissionPolicy


class EvaluationWorkflowError(RuntimeError):
    def __init__(self, code: str, *, recovery_succeeded: bool = False) -> None:
        self.code = code
        self.recovery_succeeded = recovery_succeeded
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None


class EvaluationAttemptResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: str
    run_id: str
    status: EvaluationStatus
    evaluation: EvaluationResult
    artifact_dir: Path
    scoring_inputs_dir: Path | None = None

    @model_validator(mode="after")
    def validate_result_identity(self) -> Self:
        if self.status != self.evaluation.status:
            raise ValueError("attempt status must match evaluation")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("attempt case must match evaluation")
        if self.evaluation.run_id != self.run_id or self.artifact_dir.name != self.run_id:
            raise ValueError("attempt run_id must match evaluation and artifact directory")
        if self.scoring_inputs_dir is not None and self.scoring_inputs_dir.name != self.run_id:
            raise ValueError("scoring inputs directory must match the run_id")
        return self


def _raise_workflow_error(code: str, *, recovery_succeeded: bool = False) -> NoReturn:
    raise EvaluationWorkflowError(code, recovery_succeeded=recovery_succeeded)


def _failed_evaluation(
    incident_case_id: str,
    run_id: str,
    stage_code: str,
) -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.APPLICABLE,
            passed=False,
            expected=("PRIVATE_EVALUATION_AVAILABLE",),
            actual=(stage_code,),
            reason_code=f"{code.value}_FAILED",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        incident_case_id=incident_case_id,
        run_id=run_id,
        status=EvaluationStatus.FAILED,
        checks=checks,
        failed_check_codes=tuple(EvaluationCheckCode),
        answerability="UNAVAILABLE",
        expected_status="UNAVAILABLE",
    )


ScenarioLoader = Callable[[str], ScenarioSpec]
VerificationLoader = Callable[[str], ScenarioVerification]
DiagnosisFactory = Callable[
    [str, DiagnosticStrategy, "SubmissionPolicy | None"], DiagnosisRunner
]
Evaluator = Callable[..., EvaluationResult]


class EvaluationRunner:
    def __init__(
        self,
        *,
        lab: IncidentLab,
        diagnostic_settings: DiagnosticSettings,
        diagnosis_factory: DiagnosisFactory,
        private_scenario_loader: ScenarioLoader,
        private_verification_loader: VerificationLoader,
        evaluator: Evaluator,
        artifact_writer: ArtifactWriter,
        clock: Callable[[], datetime],
        benchmark_manifest_sha256: str | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        if benchmark_manifest_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", benchmark_manifest_sha256
        ):
            raise ValueError("benchmark_manifest_sha256 must be a 64-hex digest")
        self._lab = lab
        self._diagnostic_settings = diagnostic_settings
        self._diagnosis_factory = diagnosis_factory
        self._private_scenario_loader = private_scenario_loader
        self._private_verification_loader = private_verification_loader
        self._evaluator = evaluator
        self._artifact_writer = artifact_writer
        self._clock = clock
        self._benchmark_manifest_sha256 = benchmark_manifest_sha256
        self._project_root = project_root

    @classmethod
    def for_project(
        cls,
        settings: Settings,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        benchmark_manifest_sha256: str | None = None,
    ) -> EvaluationRunner:
        lab = IncidentLab(settings, project_root)
        writer = ArtifactWriter(project_root)

        def diagnosis_factory(
            run_id: str,
            strategy: DiagnosticStrategy,
            submission_policy: SubmissionPolicy | None = None,
        ) -> DiagnosisRunner:
            return DiagnosisRunner.for_run(
                run_id,
                diagnostic_settings,
                strategy,
                project_root,
                submission_policy=submission_policy,
            )

        return cls(
            lab=lab,
            diagnostic_settings=diagnostic_settings,
            diagnosis_factory=diagnosis_factory,
            private_scenario_loader=lambda case_id: load_scenario_spec(case_id, project_root),
            private_verification_loader=lab.verifier.load_verification,
            evaluator=DeterministicEvaluator.evaluate,
            artifact_writer=writer,
            clock=lambda: datetime.now(UTC),
            benchmark_manifest_sha256=benchmark_manifest_sha256,
            project_root=project_root,
        )

    async def run(
        self,
        incident_case_id: str,
        strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        *,
        run_id: str | None = None,
    ) -> EvaluationAttemptResult:
        strategy = DiagnosticStrategy(strategy)
        if run_id is not None and re.fullmatch(RUN_ID_PATTERN, run_id) is None:
            raise ValueError("preassigned run_id must be 32 lowercase hexadecimal characters")
        started_at = self._clock()
        recovery_required = True
        recovery_succeeded = False
        recovery_case: str | None = None
        recovery_fingerprint: str | None = None
        scenario_run: ScenarioRun | None = None
        diagnosis_run: DiagnosisRunResult | None = None
        primary_error_code: str | None = None
        stage = "INITIAL_RESET"

        try:
            self._lab.reset(incident_case_id)
            stage = "PREPARE"
            self._lab.prepare(incident_case_id)
            stage = "BUILD"
            scenario_run = (
                self._lab.build(incident_case_id, run_id=run_id)
                if run_id is not None
                else self._lab.build(incident_case_id)
            )
            stage = "DIAGNOSIS_SETUP"
            # Early private-scenario load for the harness-side submission gates
            # (D2). A load failure leaves the gates disabled; the existing
            # SCENARIO_LOAD_FAILED path below still decides the evaluation
            # outcome exactly as before.
            try:
                early_scenario: ScenarioSpec | None = self._private_scenario_loader(
                    incident_case_id
                )
            except Exception:
                early_scenario = None
            submission_policy = (
                SubmissionPolicy(early_scenario)
                if early_scenario is not None
                else None
            )
            diagnosis_runner = self._diagnosis_factory(
                scenario_run.run_id, strategy, submission_policy
            )
            stage = "DIAGNOSIS"
            diagnosis_run = await diagnosis_runner.diagnose()
        except Exception:
            primary_error_code = f"{stage}_FAILED"
        finally:
            if recovery_required:
                try:
                    restore_result = self._lab.restore(incident_case_id)
                    restore_state = getattr(restore_result, "state", None)
                    restore_case = getattr(restore_result, "case_id", None)
                    fingerprint = getattr(restore_result, "fingerprint", None)
                    recovery_case = (
                        restore_case
                        if isinstance(restore_case, str) and restore_case
                        else None
                    )
                    recovery_fingerprint = (
                        fingerprint if isinstance(fingerprint, str) else None
                    )
                    recovery_succeeded = restore_state == "HEALTHY"
                except Exception:
                    recovery_succeeded = False
                    recovery_case = None
                    recovery_fingerprint = None
                    if primary_error_code is None:
                        primary_error_code = "RESTORE_FAILED"

        if primary_error_code is not None and (
            scenario_run is None or diagnosis_run is None
        ):
            _raise_workflow_error(
                primary_error_code,
                recovery_succeeded=recovery_succeeded,
            )
        if scenario_run is None or diagnosis_run is None:
            _raise_workflow_error(
                "DIAGNOSIS_FAILED",
                recovery_succeeded=recovery_succeeded,
            )

        scenario: ScenarioSpec | None = None
        verification: ScenarioVerification | None = None
        try:
            scenario = (
                early_scenario
                if early_scenario is not None
                else self._private_scenario_loader(incident_case_id)
            )
        except Exception:
            evaluation = _failed_evaluation(
                incident_case_id,
                scenario_run.run_id,
                "SCENARIO_LOAD_FAILED",
            )
        else:
            try:
                verification = self._private_verification_loader(scenario_run.run_id)
            except Exception:
                evaluation = _failed_evaluation(
                    incident_case_id,
                    scenario_run.run_id,
                    "VERIFICATION_LOAD_FAILED",
                )
            else:
                try:
                    evaluation = self._evaluator(
                        scenario,
                        verification,
                        diagnosis_run,
                        recovery_succeeded=recovery_succeeded,
                    )
                except (TypeError, ValueError, ValidationError):
                    evaluation = _failed_evaluation(
                        incident_case_id,
                        scenario_run.run_id,
                        "EVALUATION_FAILED",
                    )
                except Exception:
                    evaluation = _failed_evaluation(
                        incident_case_id,
                        scenario_run.run_id,
                        "EVALUATION_FAILED",
                    )

        try:
            artifact_run = ArtifactRun(
                incident_case_id=incident_case_id,
                run_id=scenario_run.run_id,
                started_at=started_at,
                finished_at=self._clock(),
                recovery_status=(
                    RecoveryStatus.HEALTHY
                    if recovery_succeeded
                    else RecoveryStatus.FAILED
                ),
                model_base_url=self._diagnostic_settings.model_base_url,
                benchmark_manifest_sha256=self._benchmark_manifest_sha256,
                diagnosis_run=diagnosis_run,
                evaluation=evaluation,
            )
            artifact_dir = self._artifact_writer.write(artifact_run)
            scoring_inputs_dir = self._write_scoring_inputs(
                scenario=scenario,
                verification=verification,
                diagnosis_run=diagnosis_run,
                recovery_case=recovery_case,
                recovery_succeeded=recovery_succeeded,
                recovery_fingerprint=recovery_fingerprint,
                artifact_dir=artifact_dir,
            )
            return EvaluationAttemptResult(
                incident_case_id=incident_case_id,
                run_id=scenario_run.run_id,
                status=evaluation.status,
                evaluation=evaluation,
                artifact_dir=artifact_dir,
                scoring_inputs_dir=scoring_inputs_dir,
            )
        except EvaluationWorkflowError:
            raise
        except (TypeError, ValueError, ValidationError):
            _raise_workflow_error(
                "ARTIFACT_WRITE_FAILED",
                recovery_succeeded=recovery_succeeded,
            )
        except Exception:
            _raise_workflow_error(
                "ARTIFACT_WRITE_FAILED",
                recovery_succeeded=recovery_succeeded,
            )

    def _write_scoring_inputs(
        self,
        *,
        scenario: ScenarioSpec | None,
        verification: ScenarioVerification | None,
        diagnosis_run: DiagnosisRunResult,
        recovery_case: str | None,
        recovery_succeeded: bool,
        recovery_fingerprint: str | None,
        artifact_dir: Path,
    ) -> Path | None:
        if scenario is None or verification is None:
            return None
        try:
            bundle = build_evaluation_input_bundle(
                scenario=scenario,
                verification=verification,
                diagnosis_run=diagnosis_run,
                recovery=RecoveryProof(
                    source="LAB_RESTORE",
                    incident_case_id=recovery_case or scenario.incident_case_id,
                    state="HEALTHY" if recovery_succeeded else "FAILED",
                    fingerprint=recovery_fingerprint,
                ),
                artifact_dir=artifact_dir,
                budget=BudgetSummary(
                    model_request_limit=MODEL_REQUEST_LIMIT,
                    tool_call_limit=TOOL_CALL_LIMIT,
                    output_retry_limit=OUTPUT_RETRY_LIMIT,
                    timeout_seconds=TIMEOUT_SECONDS,
                ),
                evaluator=evaluator_identity_for(
                    self._evaluator,
                    name=scorer_name_for(self._evaluator),
                ),
            )
            return write_evaluation_input_bundle(
                self._project_root,
                bundle,
                created_at=self._clock(),
            )
        except Exception:
            _raise_workflow_error(
                "SCORING_INPUTS_WRITE_FAILED",
                recovery_succeeded=recovery_succeeded,
            )


__all__ = [
    "EvaluationAttemptResult",
    "EvaluationRunner",
    "EvaluationWorkflowError",
]
