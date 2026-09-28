"""Planner compatibility probe (proposal §2.3): the experimental preflight
must prove the model-visible action-tool → receipt/verdict → final-output
loop with the real planner machinery, not just generic structured output."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.planner_probe import (
    PLANNER_PROBE_RUN_ID,
    run_planner_compatibility_probe,
)

PROBE_RELATION = "raw_payments"


def _plan_step() -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                "plan_step",
                {
                    "tool_name": "get_relation_schema",
                    "arguments": {"relation_name": PROBE_RELATION},
                    "intent": "probe the schema",
                },
            )
        ]
    )


def _abstain() -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                "submit_diagnosis",
                {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "summary": "Probe loop completed without decisive evidence.",
                    "unresolved_evidence": [
                        {
                            "evidence_kind": "RELATION_DATA_PROFILE",
                            "subject": PROBE_RELATION,
                            "reason_code": "NOT_OBSERVABLE",
                        }
                    ],
                    "confidence": 0.2,
                },
            )
        ]
    )


def _identity() -> ModelIdentity:
    return ModelIdentity("test", "probe-scripted")


@pytest.mark.asyncio
async def test_probe_passes_when_the_model_completes_the_loop() -> None:
    turns = [_plan_step(), _abstain()]

    def play(_messages: object, _info: object) -> ModelResponse:
        if turns:
            return turns.pop(0)
        return _abstain()

    result = await run_planner_compatibility_probe(
        FunctionModel(play), _identity(), timeout_seconds=30
    )

    assert result.passed, result.detail
    assert result.observed == "PLAN_LOOP_COMPLETED"
    assert result.detail["plan_step_receipts"] >= 1
    assert result.detail["terminal_status"] != "MODEL_ERROR"


@pytest.mark.asyncio
async def test_probe_fails_without_an_accepted_plan_step() -> None:
    def play(_messages: object, _info: object) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", {
            "status": "INSUFFICIENT_EVIDENCE",
            "summary": "No planning happened.",
            "unresolved_evidence": [
                {
                    "evidence_kind": "RELATION_DATA_PROFILE",
                    "subject": PROBE_RELATION,
                    "reason_code": "NOT_OBSERVABLE",
                }
            ],
            "confidence": 0.2,
        })])

    result = await run_planner_compatibility_probe(
        FunctionModel(play), _identity(), timeout_seconds=30
    )

    assert not result.passed
    assert result.observed == "NO_ACCEPTED_PLAN_STEP"


@pytest.mark.asyncio
async def test_probe_fails_sanitized_on_provider_error() -> None:
    def fail(_messages: object, _info: object) -> ModelResponse:
        raise ModelHTTPError(502, "DO_NOT_LEAK", {"secret": "x"})

    result = await run_planner_compatibility_probe(
        FunctionModel(fail), _identity(), timeout_seconds=30
    )

    assert not result.passed
    assert result.observed == "MODEL_ERROR"
    assert result.transport == "transport=HTTP_502"
    assert "DO_NOT_LEAK" not in str(result.detail)
    assert "secret" not in str(result.detail)


def test_probe_run_id_is_a_valid_run_identifier() -> None:
    import re

    from data_incident_gym.diagnosis import RUN_ID_PATTERN

    assert re.fullmatch(RUN_ID_PATTERN, PLANNER_PROBE_RUN_ID) is not None
    assert len(PLANNER_PROBE_RUN_ID) == 32
