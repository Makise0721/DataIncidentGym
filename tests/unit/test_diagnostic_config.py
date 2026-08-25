import pytest

from data_incident_gym.diagnostic_config import DiagnosticSettings


def test_diagnostic_settings_expose_no_admin_user_or_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIG_POSTGRES_USER", "TEST_REDACTED_VALUE")
    monkeypatch.setenv("DIG_POSTGRES_PASSWORD", "TEST_REDACTED_VALUE")

    settings = DiagnosticSettings(_env_file=None)

    assert settings.postgres_user == "dig_reader"
    assert settings.postgres_password.get_secret_value() == "dig_reader"
    assert not hasattr(settings, "admin_user")
    assert not hasattr(settings, "admin_password")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "db.internal"])
def test_diagnostic_settings_reject_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    monkeypatch.setenv("DIG_DIAGNOSTIC_POSTGRES_HOST", host)

    with pytest.raises(ValueError):
        DiagnosticSettings(_env_file=None)
