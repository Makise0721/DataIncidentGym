from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.providers.openai import OpenAIProvider

import data_incident_gym.doctor as doctor_module
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.doctor import (
    CHECK_ORDER,
    EXPECTED_RECOMMENDATIONS,
    RECOMMENDATION_BY_CHECK,
    DoctorCheckCode,
    DoctorRunner,
)
from data_incident_gym.profiles import ProfileError


def test_doctor_checks_include_the_four_profile_plane_checks() -> None:
    assert tuple(code.value for code in DoctorCheckCode) == CHECK_ORDER
    assert CHECK_ORDER[6:10] == (
        "PROFILE_SPEC",
        "PROFILE_SNAPSHOT",
        "PROFILE_READ_ONLY",
        "PROFILE_BOUNDS",
    )


def test_every_failed_check_has_a_fixed_recommendation() -> None:
    for code in DoctorCheckCode:
        check = DoctorRunner._check(code, False, "secret detail")
        assert check.observed == "UNAVAILABLE"
        assert check.reason_code == f"{code.value}_FAILED"
        assert check.recommendation_code == RECOMMENDATION_BY_CHECK[code]
        assert check.recommendation_code in EXPECTED_RECOMMENDATIONS


def test_passing_check_preserves_only_safe_observation() -> None:
    check = DoctorRunner._check(DoctorCheckCode.PROFILE_SPEC, True, "LOADED")

    assert check.passed is True
    assert check.observed == "LOADED"
    assert check.recommendation_code is None


def test_for_project_model_probe_uses_one_post_and_preserves_doctor_checks(
    tmp_path,
    monkeypatch,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx2.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=b'{"object": "list", "data": [{"id": "mimo-v2.5-pro"}]}',
            )
        return httpx2.Response(500, request=request)

    mock_http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    clients: list[AsyncOpenAI] = []

    def provider_factory(*, base_url: str, api_key: str) -> OpenAIProvider:
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=mock_http_client,
        )
        clients.append(client)
        return OpenAIProvider(openai_client=client)

    monkeypatch.setattr(doctor_module, "OpenAIProvider", provider_factory)
    monkeypatch.setattr(
        doctor_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    settings = DiagnosticSettings(
        _env_file=None,
        model_base_url="https://example.invalid/v1",
        model_name="mimo-v2.5-pro",
        model_api_key="offline-test-key",
    )
    runner = DoctorRunner.for_project(settings, tmp_path)

    try:
        with override_allow_model_requests(True):
            result = asyncio.run(runner.run())
    finally:
        asyncio.run(clients[0].close())

    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].url.path == "/v1/models"
    assert tuple(check.code for check in result.checks) == tuple(DoctorCheckCode)
    assert result.checks[-3].code is DoctorCheckCode.MODEL_ENDPOINT
    assert result.checks[-3].passed is True
    assert result.checks[-2].code is DoctorCheckCode.MODEL_PRESENT
    assert result.checks[-2].passed is True
    assert result.checks[-1].code is DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT
    assert result.checks[-1].passed is False


def test_endpoint_check_lists_catalog_through_the_models_list_probe() -> None:
    async def models_list() -> object:
        return SimpleNamespace(
            data=[SimpleNamespace(id="mimo-v2.5-pro"), SimpleNamespace(id="other-model")]
        )

    runner = DoctorRunner(
        DiagnosticSettings(_env_file=None),
        Path("."),
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )

    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is True
    assert check.observed == "REACHABLE"
    assert endpoint_ok is True
    assert model_ids == {"mimo-v2.5-pro", "other-model"}
    presence = runner._model_present_check(endpoint_ok, model_ids)
    assert presence.passed is True


def test_endpoint_check_fails_closed_on_transport_error() -> None:
    async def models_list() -> list[object]:
        raise RuntimeError("network unavailable")

    runner = DoctorRunner(
        DiagnosticSettings(_env_file=None),
        Path("."),
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )

    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is False
    assert check.observed == "UNAVAILABLE"
    assert endpoint_ok is False
    assert model_ids == set()
    assert runner._model_present_check(endpoint_ok, model_ids).passed is False


def test_endpoint_check_rejects_unsafe_catalog_entries() -> None:
    async def models_list() -> object:
        return SimpleNamespace(
            data=[SimpleNamespace(id="mimo-v2.5-pro"), SimpleNamespace(id="Bad Name!")]
        )

    runner = DoctorRunner(
        DiagnosticSettings(_env_file=None),
        Path("."),
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )

    check, endpoint_ok, _model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is False
    assert endpoint_ok is False
    assert check.diagnostic is not None
    assert "kind=MALFORMED:entry_id_unsafe" in check.diagnostic


def test_catalog_diagnostic_falls_back_to_error_kind_for_unknown_exceptions() -> None:
    import httpx2

    async def raw_transport_timeout() -> object:
        raise httpx2.TimeoutException("raw transport timeout")

    async def arbitrary_error() -> object:
        raise RuntimeError("something unexpected")

    raw_check, _, _ = asyncio.run(_endpoint_runner(raw_transport_timeout)._endpoint_check())
    arbitrary_check, _, _ = asyncio.run(_endpoint_runner(arbitrary_error)._endpoint_check())

    assert "kind=ERROR" in raw_check.diagnostic
    assert "exc=TimeoutException" in raw_check.diagnostic
    assert "raw transport timeout" not in raw_check.diagnostic
    assert "kind=ERROR" in arbitrary_check.diagnostic
    assert "exc=RuntimeError" in arbitrary_check.diagnostic
    assert "something unexpected" not in arbitrary_check.diagnostic


def test_endpoint_check_accepts_mixed_case_catalog_and_exact_target_match() -> None:
    async def models_list() -> object:
        return SimpleNamespace(
            data=[
                SimpleNamespace(id="moonshotai/Kimi-K3"),
                SimpleNamespace(id="zai-org/GLM-5.3"),
                SimpleNamespace(id="deepseek/deepseek-v4.1-flash"),
            ]
        )

    runner = _endpoint_runner(models_list, model_name="deepseek/deepseek-v4.1-flash")
    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is True
    assert check.diagnostic is None
    assert endpoint_ok is True
    assert "moonshotai/Kimi-K3" in model_ids
    presence = runner._model_present_check(endpoint_ok, model_ids)
    assert presence.passed is True
    assert presence.observed == "deepseek/deepseek-v4.1-flash"


def test_model_present_rejects_bound_name_differing_only_in_case() -> None:
    async def models_list() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="zai-org/GLM-5.3")])

    runner = _endpoint_runner(models_list, model_name="zai-org/glm-5.3")
    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is True
    assert endpoint_ok is True
    presence = runner._model_present_check(endpoint_ok, model_ids)
    assert presence.passed is False
    assert presence.observed == "UNAVAILABLE"


def test_endpoint_check_reports_reachable_but_missing_bound_target() -> None:
    async def models_list() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="moonshotai/Kimi-K3")])

    runner = _endpoint_runner(models_list, model_name="deepseek/deepseek-v4.1-flash")
    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is True
    assert endpoint_ok is True
    presence = runner._model_present_check(endpoint_ok, model_ids)
    assert presence.passed is False


def test_endpoint_check_still_rejects_control_chars_and_overlong_ids() -> None:
    async def control_char_id() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="bad\x01id")])

    async def overlong_id() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="a" * 129)])

    control_check, _, _ = asyncio.run(_endpoint_runner(control_char_id)._endpoint_check())
    overlong_check, _, _ = asyncio.run(_endpoint_runner(overlong_id)._endpoint_check())

    assert "kind=MALFORMED:entry_id_unsafe" in control_check.diagnostic
    assert "kind=MALFORMED:entry_id_unsafe" in overlong_check.diagnostic


def test_endpoint_check_boundary_length_is_accepted() -> None:
    async def models_list() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="A" + "a" * 127)])

    check, endpoint_ok, model_ids = asyncio.run(_endpoint_runner(models_list)._endpoint_check())

    assert check.passed is True
    assert endpoint_ok is True
    assert model_ids == {"A" + "a" * 127}


def test_for_project_wires_the_catalog_probe_to_the_provider_client(
    tmp_path,
    monkeypatch,
) -> None:
    clients: list[AsyncOpenAI] = []

    def provider_factory(*, base_url: str, api_key: str) -> OpenAIProvider:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        clients.append(client)
        return OpenAIProvider(openai_client=client)

    monkeypatch.setattr(doctor_module, "OpenAIProvider", provider_factory)
    settings = DiagnosticSettings(
        _env_file=None,
        model_base_url="https://example.invalid/v1",
        model_name="mimo-v2.5-pro",
        model_api_key="offline-test-key",
    )
    runner = DoctorRunner.for_project(settings, tmp_path)

    try:
        probe = runner._models_list()
        assert inspect.isawaitable(probe)
        assert clients[0].max_retries == 0
        assert probe._options.timeout == doctor_module._URL_TIMEOUT_SECONDS
    finally:
        asyncio.run(clients[0].close())


def test_endpoint_check_rejects_non_list_catalog_data() -> None:
    async def models_list() -> object:
        return SimpleNamespace(data="not-a-list")

    runner = DoctorRunner(
        DiagnosticSettings(_env_file=None),
        Path("."),
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )

    check, endpoint_ok, model_ids = asyncio.run(runner._endpoint_check())

    assert check.passed is False
    assert endpoint_ok is False
    assert model_ids == set()


def test_run_cascades_model_checks_when_catalog_get_fails(tmp_path, monkeypatch) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(403, request=request)

    mock_http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    clients: list[AsyncOpenAI] = []

    def provider_factory(*, base_url: str, api_key: str) -> OpenAIProvider:
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=mock_http_client,
        )
        clients.append(client)
        return OpenAIProvider(openai_client=client)

    monkeypatch.setattr(doctor_module, "OpenAIProvider", provider_factory)
    monkeypatch.setattr(
        doctor_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    settings = DiagnosticSettings(
        _env_file=None,
        model_base_url="https://example.invalid/v1",
        model_name="mimo-v2.5-pro",
        model_api_key="offline-test-key",
    )
    runner = DoctorRunner.for_project(settings, tmp_path)

    try:
        with override_allow_model_requests(True):
            result = asyncio.run(runner.run())
    finally:
        asyncio.run(clients[0].close())

    assert [request.method for request in requests] == ["GET"]
    assert result.status is doctor_module.DoctorStatus.FAILED
    model_plane = result.checks[-3:]
    assert [check.passed for check in model_plane] == [False, False, False]
    assert [check.observed for check in model_plane] == ["UNAVAILABLE"] * 3
    endpoint_diagnostic = model_plane[0].diagnostic
    assert endpoint_diagnostic is not None
    assert "kind=HTTP_403" in endpoint_diagnostic
    assert "timeout_ms=5000" in endpoint_diagnostic
    assert "elapsed_ms=" in endpoint_diagnostic


def _endpoint_runner(models_list, model_name: str | None = None) -> DoctorRunner:
    settings = (
        DiagnosticSettings(_env_file=None)
        if model_name is None
        else DiagnosticSettings(_env_file=None, model_name=model_name)
    )
    return DoctorRunner(
        settings,
        Path("."),
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )


def test_catalog_diagnostic_distinguishes_timeout_and_connection_errors() -> None:
    import httpx2
    from openai import APIConnectionError, APITimeoutError

    request = httpx2.Request("GET", "https://example.invalid/v1/models")

    async def timeout_list() -> object:
        raise APITimeoutError(request=request)

    async def connection_list() -> object:
        raise APIConnectionError(request=request)

    timeout_check, _, _ = asyncio.run(_endpoint_runner(timeout_list)._endpoint_check())
    connection_check, _, _ = asyncio.run(
        _endpoint_runner(connection_list)._endpoint_check()
    )

    assert "kind=TIMEOUT" in timeout_check.diagnostic
    assert "exc=APITimeoutError" in timeout_check.diagnostic
    assert "kind=CONNECTION_ERROR" in connection_check.diagnostic
    assert "exc=APIConnectionError" in connection_check.diagnostic


def test_catalog_diagnostic_records_http_status_codes() -> None:
    import httpx2
    from openai import APIStatusError

    def status_error(status: int) -> APIStatusError:
        request = httpx2.Request("GET", "https://example.invalid/v1/models")
        response = httpx2.Response(status, request=request)
        return APIStatusError("request rejected", response=response, body=None)

    for status in (401, 403, 429, 500, 503):
        payload = status_error(status)

        async def models_list(_payload=payload) -> object:
            raise _payload

        check, _, _ = asyncio.run(_endpoint_runner(models_list)._endpoint_check())

        assert check.diagnostic is not None
        assert f"kind=HTTP_{status}" in check.diagnostic, check.diagnostic
        assert "exc=APIStatusError" in check.diagnostic


def test_catalog_diagnostic_marks_malformed_stages() -> None:
    async def not_a_list() -> object:
        return SimpleNamespace(data="oops")

    async def bad_id_type() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id=123)])

    async def unsafe_id() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="Bad Name!")])

    cases = {
        "MALFORMED:data_not_list": not_a_list,
        "MALFORMED:entry_id_type": bad_id_type,
        "MALFORMED:entry_id_unsafe": unsafe_id,
    }
    for expected_kind, models_list in cases.items():
        check, _, _ = asyncio.run(_endpoint_runner(models_list)._endpoint_check())

        assert check.diagnostic is not None
        assert f"kind={expected_kind}" in check.diagnostic, check.diagnostic


def test_catalog_diagnostic_is_secret_free_and_times_the_failure() -> None:
    import httpx2
    from openai import APIStatusError

    request = httpx2.Request("GET", "https://example.invalid/v1/models")
    response = httpx2.Response(403, request=request)
    error = APIStatusError(
        "denied for key sk-DO-NOT-LEAK with body {'error':'x'}",
        response=response,
        body=None,
    )

    async def models_list() -> object:
        raise error

    runner = _endpoint_runner(models_list)
    check, _, _ = asyncio.run(runner._endpoint_check())

    assert check.diagnostic is not None
    assert "sk-DO-NOT-LEAK" not in check.diagnostic
    assert "denied" not in check.diagnostic
    assert "body" not in check.diagnostic
    assert "timeout_ms=5000" in check.diagnostic
    elapsed = int(check.diagnostic.split("elapsed_ms=")[1])
    assert elapsed >= 0


def test_endpoint_check_success_carries_no_diagnostic() -> None:
    async def models_list() -> object:
        return SimpleNamespace(data=[SimpleNamespace(id="mimo-v2.5-pro")])

    check, _, _ = asyncio.run(_endpoint_runner(models_list)._endpoint_check())

    assert check.passed is True
    assert check.diagnostic is None


def test_doctor_check_rejects_diagnostic_on_passed_check() -> None:
    with pytest.raises(ValueError, match="diagnostic"):
        doctor_module.DoctorCheck(
            code=DoctorCheckCode.MODEL_ENDPOINT,
            passed=True,
            observed="REACHABLE",
            reason_code="MODEL_ENDPOINT_PASSED",
            recommendation_code=None,
            diagnostic="stage=catalog_list",
        )


def _runner(tmp_path) -> DoctorRunner:
    async def _models_list() -> list[object]:
        return []

    return DoctorRunner(
        DiagnosticSettings(_env_file=None),
        tmp_path,
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        models_list=_models_list,
        model=SimpleNamespace(),
        temporary_directory=lambda: None,
    )


def test_profile_checks_prove_snapshot_read_only_match_and_bounds(tmp_path, monkeypatch) -> None:
    current = SimpleNamespace(relation_name="raw_orders")
    history = SimpleNamespace(relation_name="raw_orders")
    spec = SimpleNamespace(
        schema_version="profile_spec.v1",
        digest=lambda: "a" * 64,
        max_group_rows=128,
        max_history_points=90,
    )
    snapshot = SimpleNamespace(
        schema_version="profile_snapshot.v1",
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256="a" * 64,
        current=(current,),
        history=(history,),
    )
    readers = []

    class Reader:
        def __init__(self, **kwargs) -> None:
            self.read_only = kwargs.get("read_only", False)
            readers.append(self)

        def read_current(self, relation_name: str):
            if relation_name == "invalid_relation" or ";" in relation_name:
                raise ProfileError("invalid relation")
            return current

        def read_history(self, relation_name: str):
            return history

    monkeypatch.setattr(doctor_module, "load_profile_spec", lambda _: spec)
    monkeypatch.setattr(doctor_module, "load_profile_snapshot", lambda _: snapshot)
    monkeypatch.setattr(doctor_module, "AggregateSnapshotReader", Reader)

    checks = _runner(tmp_path)._profile_checks(postgres_available=True)

    assert all(check.passed for check in checks)
    assert [reader.read_only for reader in readers] == [True, False]


def test_profile_checks_fail_closed_when_baseline_snapshot_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    spec = SimpleNamespace(digest=lambda: "a" * 64, max_group_rows=128, max_history_points=90)
    monkeypatch.setattr(doctor_module, "load_profile_spec", lambda _: spec)
    monkeypatch.setattr(
        doctor_module,
        "load_profile_snapshot",
        lambda _: (_ for _ in ()).throw(ProfileError("missing snapshot")),
    )

    checks = _runner(tmp_path)._profile_checks(postgres_available=True)

    assert checks[0].passed is True
    assert checks[1].passed is False
    assert checks[2].passed is False
    assert checks[1].observed == checks[2].observed == "UNAVAILABLE"
