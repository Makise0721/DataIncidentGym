from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from data_incident_gym.artifacts import (
    ArtifactRun,
    ArtifactWriter,
    RecoveryStatus,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosisRunResult
from data_incident_gym.diagnostic_agent import DiagnosisRunner
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.incidents import GroundTruth, load_ground_truth
from data_incident_gym.lab import FaultRun, IncidentLab


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


class EvaluationRunner:
    def __init__(
        self,
        *,
        lab: IncidentLab,
        diagnostic_settings: DiagnosticSettings,
        diagnosis_factory: Callable[[str], DiagnosisRunner],
        ground_truth_loader: Callable[[str], GroundTruth],
        evaluator: Callable[..., EvaluationResult],
        artifact_writer: ArtifactWriter,
        clock: Callable[[], datetime],
    ) -> None:
        self._lab = lab
        self._diagnostic_settings = diagnostic_settings
        self._diagnosis_factory = diagnosis_factory
        self._ground_truth_loader = ground_truth_loader
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

        def diagnosis_factory(run_id: str) -> DiagnosisRunner:
            return DiagnosisRunner.for_run(run_id, diagnostic_settings, project_root)

        return cls(
            lab=lab,
            diagnostic_settings=diagnostic_settings,
            diagnosis_factory=diagnosis_factory,
            ground_truth_loader=lambda case_id: load_ground_truth(case_id, project_root),
            evaluator=DeterministicEvaluator.evaluate,
            artifact_writer=writer,
            clock=lambda: datetime.now(UTC),
        )

    async def run(self, incident_case_id: str) -> EvaluationAttemptResult:
        started_at = self._clock()

        ground_truth: GroundTruth | None = None
        ground_truth_error = False
        try:
            ground_truth = self._ground_truth_loader(incident_case_id)
        except Exception:
            ground_truth_error = True
        if ground_truth_error or ground_truth is None:
            _raise_workflow_error("GROUND_TRUTH_LOAD_FAILED")

        mutation_started = False
        fault_run: FaultRun | None = None
        diagnosis_run: DiagnosisRunResult | None = None
        primary_error_code: str | None = None
        recovery_succeeded = False
        stage = "INITIAL_RESET"

        try:
            self._lab.reset(incident_case_id)
            mutation_started = True
            stage = "INJECT"
            self._lab.inject(incident_case_id)
            stage = "BUILD"
            fault_run = self._lab.build(incident_case_id)
            stage = "DIAGNOSIS_SETUP"
            diagnosis_runner = self._diagnosis_factory(fault_run.run_id)
            stage = "DIAGNOSIS"
            diagnosis_run = await diagnosis_runner.diagnose(incident_case_id)
        except Exception:
            primary_error_code = f"{stage}_FAILED"
        finally:
            if mutation_started:
                try:
                    self._lab.reset(incident_case_id)
                    recovery_succeeded = True
                except Exception:
                    recovery_succeeded = False

        if diagnosis_run is None or fault_run is None:
            _raise_workflow_error(primary_error_code or "WORKFLOW_FAILED")

        artifact_run: ArtifactRun | None = None
        evaluation: EvaluationResult | None = None
        evaluation_error = False
        try:
            evaluation = self._evaluator(
                ground_truth,
                fault_run.verification,
                diagnosis_run,
                recovery_succeeded=recovery_succeeded,
            )
            artifact_run = ArtifactRun(
                incident_case_id=incident_case_id,
                run_id=fault_run.run_id,
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
        except (TypeError, ValueError, ValidationError):
            evaluation_error = True
        if evaluation_error or evaluation is None or artifact_run is None:
            _raise_workflow_error("EVALUATION_FAILED")

        artifact_dir: Path | None = None
        attempt: EvaluationAttemptResult | None = None
        artifact_error = False
        try:
            artifact_dir = self._artifact_writer.write(artifact_run)
            attempt = EvaluationAttemptResult(
                incident_case_id=incident_case_id,
                run_id=fault_run.run_id,
                status=evaluation.status,
                evaluation=evaluation,
                artifact_dir=artifact_dir,
            )
        except Exception:
            artifact_error = True
        if artifact_error or artifact_dir is None or attempt is None:
            _raise_workflow_error("ARTIFACT_WRITE_FAILED")
        return attempt
