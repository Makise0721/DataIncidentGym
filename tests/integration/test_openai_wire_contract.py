from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import pydantic_ai.models
import pytest
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)

EVIDENCE_TOOLS = {
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
    "get_relation_data_profile",
    "get_relation_history",
}


def _chat_response(message: dict[str, Any], *, prompt_tokens: int = 101) -> dict[str, Any]:
    return {
        "id": "chatcmpl-wire-contract",
        "object": "chat.completion",
        "created": 1_725_000_000,
        "model": "mimo-v2.5",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 17,
            "total_tokens": prompt_tokens + 17,
        },
    }


class _OpenAIWireMock(AbstractContextManager["_OpenAIWireMock"]):
    def __init__(self, responder: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]) -> None:
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.errors: list[str] = []
        self._responder = responder
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _OpenAIWireMock:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                owner.paths.append(self.path)
                owner.requests.append(payload)
                try:
                    status, response = owner._responder(payload)
                except Exception as error:
                    owner.errors.append(repr(error))
                    status, response = 500, {"error": str(error)}
                body = json.dumps(response).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}/v1"


def _output_tool_name(request: dict[str, Any]) -> str:
    names = {
        tool["function"]["name"]
        for tool in request["tools"]
        if tool.get("type") == "function"
    }
    output_names = names - EVIDENCE_TOOLS
    assert len(output_names) == 1
    return output_names.pop()


class _EvidenceTools:
    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return (
            EvidenceRecord.create(
                run_id=run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject="dbt_run_results",
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
                content=DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=run_id,
                    run_status="FAILED",
                    dbt_exit_code=1,
                    failed_nodes=("model.jaffle_shop.stg_payments",),
                    skipped_nodes=(),
                ),
            ),
        )


def _write_public_run(project_root: Path, run_id: str) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / run_id
    run_root.mkdir(parents=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "p1.runtime.v1",
                "run_id": run_id,
                "dbt_exit_code": 1,
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                    "profile_snapshot": "profile_snapshot.json",
                    "incident_brief": "incident_brief.json",
                },
                "observable_relations": {
                    "schema": ["raw_payments"],
                    "profile": ["raw_payments"],
                    "history": ["raw_payments"],
                },
                "profile_spec_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    (run_root / "incident_brief.json").write_text(
        json.dumps(
            {
                "schema_version": "incident_brief.v1",
                "signal_code": "DBT_BUILD_FAILED",
                "summary": "The payment build failed.",
                "subjects": ["raw_payments"],
                "logical_observed_at": "2026-09-02T00:00:00+00:00",
                "observations": [],
            }
        ),
        encoding="utf-8",
    )


def _diagnosis_runner(
    project_root: Path,
    base_url: str,
    strategy: DiagnosticStrategy,
    run_id: str,
):
    settings = DiagnosticSettings(
        _env_file=None,
        model_base_url=base_url,
        model_name="mimo-v2.5",
        model_api_key="wire-test-key",
    )
    client = AsyncOpenAI(
        api_key="wire-test-key",
        base_url=base_url,
        http_client=httpx2.AsyncClient(trust_env=False),
    )
    model = OpenAIChatModel(
        "mimo-v2.5",
        provider=OpenAIProvider(openai_client=client),
    )
    return (
        DiagnosisRunner.for_run(
            run_id,
            settings,
            strategy,
            project_root,
            model=model,
            tools=_EvidenceTools(),
            model_identity=ModelIdentity("openai-compatible", "mimo-v2.5"),
        ),
        client,
    )


def _default_diagnosis_runner(
    project_root: Path,
    base_url: str,
    strategy: DiagnosticStrategy,
    run_id: str,
) -> DiagnosisRunner:
    settings = DiagnosticSettings(
        _env_file=None,
        model_base_url=base_url,
        model_name="mimo-v2.5",
        model_api_key="wire-test-key",
    )
    return DiagnosisRunner.for_run(
        run_id,
        settings,
        strategy,
        project_root,
        tools=_EvidenceTools(),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_wire_static_tool_return_final_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    call_count = 0

    def respond(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        run_id = "a" * 32
        if call_count == 1:
            return 200, _chat_response(
                {
                    "content": None,
                    "reasoning_content": "wire-hidden-reasoning-must-not-leak",
                    "tool_calls": [
                        {
                            "id": "call-run-results",
                            "type": "function",
                            "function": {
                                "name": "get_dbt_run_results",
                                "arguments": json.dumps({"run_id": run_id}),
                            },
                        }
                    ],
                }
            )
        return 200, _chat_response(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-final",
                        "type": "function",
                        "function": {
                            "name": _output_tool_name(request),
                            "arguments": json.dumps(
                                {
                                    "status": "INSUFFICIENT_EVIDENCE",
                                    "run_id": run_id,
                                    "root_cause_code": None,
                                    "summary": "The wire contract test needs more evidence.",
                                    "affected_assets": [],
                                    "evidence_ids": [],
                                    "claims": [],
                                    "unresolved_evidence": [
                                        {
                                            "evidence_kind": "RELATION_SCHEMA",
                                            "subject": "raw_payments",
                                            "reason_code": "NOT_OBSERVABLE",
                                        }
                                    ],
                                    "recommended_actions": ["Collect relation schema evidence."],
                                    "confidence": 0.2,
                                }
                            ),
                        },
                    }
                ],
            }
        )

    _write_public_run(tmp_path, "a" * 32)
    with _OpenAIWireMock(respond) as wire:
        runner, client = _diagnosis_runner(
            tmp_path,
            wire.base_url,
            DiagnosticStrategy.STATIC_SKILL,
            "a" * 32,
        )
        try:
            result = await runner.diagnose()
        finally:
            await client.close()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE, (
        result.diagnosis.summary,
        wire.requests,
        wire.errors,
        result.trace,
    )
    assert result.metrics.model_requests == 2
    assert result.metrics.input_tokens == 202
    assert result.metrics.output_tokens == 34
    assert wire.errors == []
    assert len(wire.requests) == 2
    assert wire.paths == ["/v1/chat/completions", "/v1/chat/completions"]
    assert all(request["model"] == "mimo-v2.5" for request in wire.requests)
    assert all(request["stream"] is False for request in wire.requests)
    assert all(request["tools"] for request in wire.requests)
    assert all(
        tool["function"]["parameters"]["type"] == "object"
        for request in wire.requests
        for tool in request["tools"]
    )
    assert all(
        isinstance(call["function"]["arguments"], str)
        for request in wire.requests
        for message in request["messages"]
        for call in message.get("tool_calls", [])
    )
    assert any(message["role"] == "tool" for message in wire.requests[1]["messages"])
    assert _output_tool_name(wire.requests[1]) == "final_result"
    assert any(
        message.get("reasoning_content") == "wire-hidden-reasoning-must-not-leak"
        for message in wire.requests[1]["messages"]
    )
    assert "wire-hidden-reasoning-must-not-leak" not in result.diagnosis.model_dump_json()
    assert all("wire-hidden-reasoning-must-not-leak" not in repr(event) for event in result.trace)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_wire_kernel_intent_tool_return_fails_closed_on_bad_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    run_id = "b" * 32
    calls = 0

    def respond(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            intent = {
                "schema_version": "p1.kernel_intent.v1",
                "gap_id": "g_locate",
                "gap_kind": "LOCATE_FAILURE",
                "hypothesis_ids": [],
                "new_hypotheses": [],
            }
            return 200, _chat_response(
                {
                    "content": json.dumps(intent),
                    "tool_calls": [
                        {
                            "id": "call-run-results",
                            "type": "function",
                            "function": {
                                "name": "get_dbt_run_results",
                                "arguments": json.dumps({"run_id": run_id}),
                            },
                        }
                    ],
                }
            )
        return 200, _chat_response(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-final-{calls}",
                        "type": "function",
                        "function": {
                            "name": _output_tool_name(request),
                            "arguments": "{}",
                        },
                    }
                ],
            }
        )

    _write_public_run(tmp_path, run_id)
    with _OpenAIWireMock(respond) as wire:
        runner, client = _diagnosis_runner(
            tmp_path,
            wire.base_url,
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            run_id,
        )
        try:
            result = await runner.diagnose()
        finally:
            await client.close()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.metrics.successful_tool_calls == 1
    assert len(wire.requests) >= 2
    assert wire.paths[0] == "/v1/chat/completions"
    mixed_response = wire.requests[1]["messages"]
    assistant = next(message for message in mixed_response if message["role"] == "assistant")
    assert json.loads(assistant["content"])["schema_version"] == "p1.kernel_intent.v1"
    assert len(assistant["tool_calls"]) == 1
    assert assistant["tool_calls"][0]["function"]["name"] == "get_dbt_run_results"
    assert isinstance(assistant["tool_calls"][0]["function"]["arguments"], str)
    assert any(message["role"] == "tool" for message in mixed_response)
    assert wire.errors == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_wire_http_400_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    run_id = "c" * 32

    def respond(_request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 400, {"error": {"message": "bad request", "type": "invalid_request_error"}}

    _write_public_run(tmp_path, run_id)
    with _OpenAIWireMock(respond) as wire:
        runner, client = _diagnosis_runner(
            tmp_path,
            wire.base_url,
            DiagnosticStrategy.STATIC_SKILL,
            run_id,
        )
        try:
            result = await runner.diagnose()
        finally:
            await client.close()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert len(wire.requests) == 1
    assert wire.paths == ["/v1/chat/completions"]
    assert wire.errors == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_default_openai_provider_does_not_retry_http_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    run_id = "d" * 32

    def respond(_request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 500, {"error": {"message": "server unavailable", "type": "server_error"}}

    _write_public_run(tmp_path, run_id)
    with _OpenAIWireMock(respond) as wire:
        runner = _default_diagnosis_runner(
            tmp_path,
            wire.base_url,
            DiagnosticStrategy.STATIC_SKILL,
            run_id,
        )
        result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.metrics.model_requests == 0
    assert len(wire.requests) == 1
    assert wire.paths == ["/v1/chat/completions"]
    assert wire.errors == []
    assert runner._owned_model_client is not None
    assert runner._owned_model_client.is_closed()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_wire_malformed_success_response_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)
    run_id = "e" * 32

    def respond(_request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {"object": "chat.completion", "choices": []}

    _write_public_run(tmp_path, run_id)
    with _OpenAIWireMock(respond) as wire:
        runner, client = _diagnosis_runner(
            tmp_path,
            wire.base_url,
            DiagnosticStrategy.STATIC_SKILL,
            run_id,
        )
        try:
            result = await runner.diagnose()
        finally:
            await client.close()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert len(wire.requests) == 1
    assert wire.paths == ["/v1/chat/completions"]
    assert wire.errors == []
