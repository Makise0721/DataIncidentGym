from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import resolve_run_context

SYSTEM_PROMPT = """
You diagnose one data incident using only the four registered read-only evidence tools.
Choose evidence based on the facts returned by tools; do not infer a root cause from the
incident identifier. Do not repeat an identical tool call. Return the required Diagnosis
object and cite only evidence IDs returned by tools. If evidence is insufficient, return
INSUFFICIENT_EVIDENCE without guessing.
""".strip()


@dataclass
class _RunState:
    run_id: str
    incident_case_id: str
    evidence_records: list[EvidenceRecord] = field(default_factory=list)

    def record(self, records: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        for record in records:
            if record.run_id != self.run_id:
                raise ValueError("evidence run context does not match")
            if record.evidence_id not in {item.evidence_id for item in self.evidence_records}:
                self.evidence_records.append(record)
        return records


class DiagnosisRunner:
    def __init__(
        self,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path,
        model: Model,
        tools: EvidenceTools,
    ) -> None:
        self._run_id = run_id
        self._settings = settings
        self._project_root = project_root
        self._model = model
        self._tools = tools

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        model: Model | None = None,
        tools: EvidenceTools | None = None,
    ) -> DiagnosisRunner:
        if model is None:
            provider = OpenAIProvider(
                base_url=str(settings.model_base_url),
                api_key=settings.model_api_key.get_secret_value(),
            )
            model = OpenAIChatModel(settings.model_name, provider=provider)
        if tools is None:
            tools = EvidenceTools.for_run(
                run_id,
                settings,
                project_root=project_root,
            )
        return cls(run_id, settings, project_root, model, tools)

    def _agent(self) -> Agent[_RunState, Diagnosis]:
        agent = Agent(
            self._model,
            deps_type=_RunState,
            output_type=Diagnosis,
            system_prompt=SYSTEM_PROMPT,
        )

        @agent.tool
        def get_dbt_run_results(
            ctx: RunContext[_RunState], run_id: str
        ) -> tuple[EvidenceRecord, ...]:
            if run_id != ctx.deps.run_id:
                raise ValueError("run context does not match")
            return ctx.deps.record(self._tools.get_dbt_run_results(ctx.deps.run_id))

        @agent.tool
        def get_dbt_node_error(
            ctx: RunContext[_RunState], run_id: str, node_id: str
        ) -> tuple[EvidenceRecord, ...]:
            if run_id != ctx.deps.run_id:
                raise ValueError("run context does not match")
            return ctx.deps.record(
                self._tools.get_dbt_node_error(ctx.deps.run_id, node_id)
            )

        @agent.tool
        def get_relation_schema(
            ctx: RunContext[_RunState], relation_name: str
        ) -> tuple[EvidenceRecord, ...]:
            return ctx.deps.record(self._tools.get_relation_schema(relation_name))

        @agent.tool
        def get_dbt_lineage(
            ctx: RunContext[_RunState],
            node_id: str,
            direction: Literal["upstream", "downstream"],
        ) -> tuple[EvidenceRecord, ...]:
            return ctx.deps.record(self._tools.get_dbt_lineage(node_id, direction))

        return agent

    async def diagnose(self, incident_case_id: str) -> DiagnosisRunResult:
        context = resolve_run_context(
            self._run_id,
            incident_case_id,
            project_root=self._project_root,
        )
        state = _RunState(self._run_id, context.incident_case_id)
        agent = self._agent()
        prompt = (
            f"Investigate incident case {context.incident_case_id!r} for verified run "
            f"{context.run_id!r}. Use the read-only evidence tools and return Diagnosis."
        )
        result = await agent.run(prompt, deps=state)
        diagnosis = result.output
        usage = result.usage
        return DiagnosisRunResult(
            diagnosis=diagnosis,
            evidence_records=tuple(state.evidence_records),
            trace=(),
            metrics=DiagnosisMetrics(
                provider=getattr(self._model, "system", "openai-compatible") or "openai-compatible",
                model=getattr(self._model, "model_name", self._settings.model_name),
                model_requests=usage.requests,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                tool_call_attempts=len(state.evidence_records),
                successful_tool_calls=len(state.evidence_records),
                elapsed_ms=0,
            ),
        )
