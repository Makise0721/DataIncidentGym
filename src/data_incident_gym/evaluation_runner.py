from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from data_incident_gym.artifacts import ArtifactRun, ArtifactWriter, RecoveryStatus
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosisRunResult, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.lab import IncidentLab, ScenarioRun
from data_incident_gym.lab_verifier import ScenarioVerification
from data_incident_gym.scenarios import ScenarioSpec, load_scenario_spec


class EvaluationWorkflowError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
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

    @model_validator(mode="after")
    def validate_result_identity(self) -> Self:
        if self.status != self.evaluation.status:
            raise ValueError("attempt status must match evaluation")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("attempt case must match evaluation")
        if self.evaluation.run_id != self.run_id or self.artifact_dir.name != self.run_id:
            raise ValueError("attempt run_id must match evaluation and artifact directory")
        return self


def _raise_workflow_error(code: str) -> NoReturn:
    raise EvaluationWorkflowError(code)


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
DiagnosisFactory = Callable[[str, DiagnosticStrategy], DiagnosisRunner]
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
    ) -> None:
        self._lab = lab
        self._diagnostic_settings = diagnostic_settings
        self._diagnosis_factory = diagnosis_factory
        self._private_scenario_loader = private_scenario_loader
        self._private_verification_loader = private_verification_loader
        self._evaluator = evaluator
        self._artifact_writer = artifact_writer
        self._clock = clock

    @classmethod
    def for_project(
        cls,
        settings: Settings,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
    ) -> EvaluationRunner:
        lab = IncidentLab(settings, project_root)
        writer = ArtifactWriter(project_root)

        def diagnosis_factory(
            run_id: str,
            strategy: DiagnosticStrategy,
        ) -> DiagnosisRunner:
            return DiagnosisRunner.for_run(
                run_id,
                diagnostic_settings,
                strategy,
                project_root,
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
        )

    async def run(
        self,
        incident_case_id: str,
        strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
    ) -> EvaluationAttemptResult:
        strategy = DiagnosticStrategy(strategy)
        started_at = self._clock()
        mutation_started = False
        recovery_succeeded = False
        scenario_run: ScenarioRun | None = None
        diagnosis_run: DiagnosisRunResult | None = None
        primary_error_code: str | None = None
        stage = "INITIAL_RESET"

        try:
            self._lab.reset(incident_case_id)
            mutation_started = True
            stage = "PREPARE"
            self._lab.prepare(incident_case_id)
            stage = "BUILD"
            scenario_run = self._lab.build(incident_case_id)
            stage = "DIAGNOSIS_SETUP"
            diagnosis_runner = self._diagnosis_factory(scenario_run.run_id, strategy)
            stage = "DIAGNOSIS"
            diagnosis_run = await diagnosis_runner.diagnose()
        except Exception:
            primary_error_code = f"{stage}_FAILED"
        finally:
            if mutation_started:
                try:
                    recovery_succeeded = (
                        self._lab.restore(incident_case_id).state == "HEALTHY"
                    )
                except Exception:
                    recovery_succeeded = False
                    if primary_error_code is None:
                        primary_error_code = "RESTORE_FAILED"

        if primary_error_code is not None and (
            scenario_run is None or diagnosis_run is None
        ):
            _raise_workflow_error(primary_error_code)
        if scenario_run is None or diagnosis_run is None:
            _raise_workflow_error("DIAGNOSIS_FAILED")

        try:
            scenario = self._private_scenario_loader(incident_case_id)
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
                diagnosis_run=diagnosis_run,
                evaluation=evaluation,
            )
            artifact_dir = self._artifact_writer.write(artifact_run)
            return EvaluationAttemptResult(
                incident_case_id=incident_case_id,
                run_id=scenario_run.run_id,
                status=evaluation.status,
                evaluation=evaluation,
                artifact_dir=artifact_dir,
            )
        except (TypeError, ValueError, ValidationError):
            _raise_workflow_error("ARTIFACT_WRITE_FAILED")
        except Exception:
            _raise_workflow_error("ARTIFACT_WRITE_FAILED")


__all__ = [
    "EvaluationAttemptResult",
    "EvaluationRunner",
    "EvaluationWorkflowError",
]
