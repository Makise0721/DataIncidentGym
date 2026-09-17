"""The external script client must complete the three terminal paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from data_incident_gym.diagnosis import DiagnosisStatus

CLIENT_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "external_strategy_client.py"
)


def _client() -> object:
    spec = importlib.util.spec_from_file_location("external_strategy_client", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_client_completes_all_three_terminal_paths() -> None:
    client = _client()

    confirmed = client.diagnose(client.open_session(client.PathBackend(fail=True)))
    abstain = client.diagnose(
        client.open_session(
            client.PathBackend(fail=True, refuse_profile_for="raw_orders")
        )
    )
    health = client.diagnose(client.open_session(client.PathBackend(fail=False)))

    assert confirmed == {"accepted": True, "status": DiagnosisStatus.CONFIRMED.value}
    assert abstain == {
        "accepted": True,
        "status": DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        "refusal_code": "RELATION_NOT_ALLOWED",
    }
    assert health == {"accepted": True, "status": DiagnosisStatus.NO_INCIDENT.value}


def test_client_conclusion_follows_the_decisive_fact() -> None:
    """T09 audit regression: the client concludes from the receipts' facts, so
    changing one decisive fact through the same code path changes the terminal —
    a preset answer could not do that."""

    client = _client()
    failed = client.diagnose(client.open_session(client.PathBackend(fail=True)))
    succeeded = client.diagnose(client.open_session(client.PathBackend(fail=False)))
    refused = client.diagnose(
        client.open_session(client.PathBackend(fail=True, refuse_profile_for="raw_orders"))
    )

    assert failed["status"] == DiagnosisStatus.CONFIRMED.value
    assert succeeded["status"] == DiagnosisStatus.NO_INCIDENT.value
    assert refused["status"] == DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    assert refused["refusal_code"] == "RELATION_NOT_ALLOWED"
    assert len({failed["status"], succeeded["status"], refused["status"]}) == 3


def test_client_sessions_stay_inside_the_harness_budget() -> None:
    client = _client()

    for backend in (
        client.PathBackend(fail=True),
        client.PathBackend(fail=True, refuse_profile_for="raw_orders"),
        client.PathBackend(fail=False),
    ):
        function = client.diagnose
        session = client.open_session(backend)
        function(session)
        snapshot = session.snapshot()
        assert snapshot["tool_call_attempts"] <= 8
        assert snapshot["submitted"] is True
        assert snapshot["cancelled"] is None


def test_client_main_runs(capsys) -> None:
    client = _client()

    assert client.main() == 0
    output = capsys.readouterr().out
    assert "[confirmed] outcome=" in output
    assert "[abstain] outcome=" in output
    assert "[health] outcome=" in output
    assert "usage=" in output
