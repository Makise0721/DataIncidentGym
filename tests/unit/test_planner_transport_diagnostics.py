"""Planner-path transport diagnostics (proposal §2.4): sanitized, strictly
reloadable, provider-origin only — four synthetic classes (connection,
timeout, HTTP status, non-transport)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosisRunResult, DiagnosisStatus
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.planner_agent import EvidencePlannerRunner
from data_incident_gym.strategy_adapter import StrategySession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_evidence_planner import _PlannerTools  # noqa: E402
from unit.test_kernel_ledger_instructions import _write_run_context  # noqa: E402
from unit.test_planner_runner import RUN_ID, _abstain  # noqa: E402
from unit.test_strategy_adapter import _context, _declaration  # noqa: E402


def _runner(project_root: Path, play) -> EvidencePlannerRunner:
    session = StrategySession(
        run_id=RUN_ID,
        tools=_PlannerTools(),
        context=_context(),
        declaration=_declaration(),
    )
    return EvidencePlannerRunner.for_run(
        RUN_ID,
        SimpleNamespace(),
        project_root,
        model=FunctionModel(play),
        model_identity=ModelIdentity("test", "scripted"),
        session=session,
    )


def _protocol_event(result: DiagnosisRunResult):
    return next(
        (event for event in result.trace if getattr(event, "event_type", None) == "MODEL_PROTOCOL"),
        None,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    _write_run_context(tmp_path, RUN_ID)
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_factory, expected_transport",
    [
        (lambda: ModelHTTPError(429, "DO_NOT_LEAK", {"secret": "x"}), "transport=HTTP_429"),
        (lambda: ModelHTTPError(503, "DO_NOT_LEAK", None), "transport=HTTP_503"),
    ],
)
async def test_planner_provider_failure_records_sanitized_transport_diagnostic(
    project_root: Path,
    error_factory,
    expected_transport: str,
) -> None:
    def fail(_messages: object, _info: object) -> ModelResponse:
        raise error_factory()

    result = await _runner(project_root, fail).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.stage == "PROVIDER_RESPONSE"
    assert event.error_origin == "PROVIDER"
    assert event.transport_diagnostic == expected_transport
    dumped = result.model_dump_json()
    assert "DO_NOT_LEAK" not in dumped
    assert "secret" not in dumped
    reloaded = DiagnosisRunResult.model_validate_json(result.model_dump_json())
    assert reloaded == result


@pytest.mark.asyncio
async def test_planner_non_transport_error_carries_no_transport_diagnostic(
    project_root: Path,
) -> None:
    def fail(_messages: object, _info: object) -> ModelResponse:
        raise ValueError("DO_NOT_LEAK_RUNTIME")

    result = await _runner(project_root, fail).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_RUNTIME_ERROR"
    assert _protocol_event(result) is None
    assert "DO_NOT_LEAK_RUNTIME" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_planner_connection_and_timeout_through_real_sdk(
    project_root: Path,
) -> None:
    import httpx2
    from openai import AsyncOpenAI
    from pydantic_ai.models import override_allow_model_requests
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    outcomes = {}

    def make_handler(error):
        def handler(request):
            raise error("DO_NOT_LEAK", request=request)

        return handler

    for label, error, expected in (
        # The shared M23 classifier decides on the outer exception type: an
        # SDK-wrapped read-timeout arrives as ModelAPIError without a status
        # and classifies CONNECTION_ERROR, exactly like the kernel path.
        ("CONNECTION_ERROR", httpx2.ConnectError, "transport=CONNECTION_ERROR"),
        ("SDK_READ_TIMEOUT", httpx2.ReadTimeout, "transport=CONNECTION_ERROR"),
    ):
        handler = make_handler(error)
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http,
            AsyncOpenAI(
                api_key="DO_NOT_LEAK",
                base_url="https://example.invalid/v1",
                http_client=http,
                max_retries=0,
            ) as client,
        ):
            session = StrategySession(
                run_id=RUN_ID,
                tools=_PlannerTools(),
                context=_context(),
                declaration=_declaration(),
            )
            runner = EvidencePlannerRunner.for_run(
                RUN_ID,
                SimpleNamespace(),
                project_root,
                model=OpenAIChatModel("test", provider=OpenAIProvider(openai_client=client)),
                model_identity=ModelIdentity("openai-compatible", "test"),
                session=session,
            )
            with override_allow_model_requests(True):
                result = await runner.diagnose()
        assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
        event = _protocol_event(result)
        assert event is not None, label
        assert event.transport_diagnostic == expected, label
        assert "DO_NOT_LEAK" not in result.model_dump_json()
        outcomes[label] = True
    assert outcomes == {"CONNECTION_ERROR": True, "SDK_READ_TIMEOUT": True}


@pytest.mark.asyncio
async def test_planner_direct_api_timeout_exercises_the_timeout_arm(
    project_root: Path,
) -> None:
    import httpx2
    from openai import APITimeoutError

    request = httpx2.Request("POST", "https://example.invalid/v1/chat/completions")

    def fail(_messages: object, _info: object) -> ModelResponse:
        raise APITimeoutError(request)

    result = await _runner(project_root, fail).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.transport_diagnostic == "transport=TIMEOUT"
    assert "DO_NOT_LEAK" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_planner_successful_run_appends_no_protocol_event(
    project_root: Path,
) -> None:
    def play(_messages: object, _info: object) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])

    result = await _runner(project_root, play).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _protocol_event(result) is None
