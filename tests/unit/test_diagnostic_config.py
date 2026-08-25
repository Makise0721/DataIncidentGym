import pytest
from pydantic import SecretStr

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


def test_diagnostic_model_defaults_are_local_and_secret() -> None:
    settings = DiagnosticSettings(_env_file=None)

    assert settings.model_base_url == "http://127.0.0.1:11434/v1"
    assert settings.model_name == "gemma4:e4b"
    assert isinstance(settings.model_api_key, SecretStr)
    assert settings.model_api_key.get_secret_value() == "ollama-local"


def test_diagnostic_model_environment_overrides_are_prefixed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIG_DIAGNOSTIC_MODEL_BASE_URL", "https://127.0.0.1:9999/v1")
    monkeypatch.setenv("DIG_DIAGNOSTIC_MODEL_NAME", "synthetic-model")
    monkeypatch.setenv("DIG_DIAGNOSTIC_MODEL_API_KEY", "TEST_REDACTED_VALUE")
    monkeypatch.setenv("DIG_MODEL_NAME", "wrong-prefix-model")

    settings = DiagnosticSettings(_env_file=None)

    assert settings.model_base_url == "https://127.0.0.1:9999/v1"
    assert settings.model_name == "synthetic-model"
    assert settings.model_api_key.get_secret_value() == "TEST_REDACTED_VALUE"
    assert "TEST_REDACTED_VALUE" not in repr(settings)
    assert "TEST_REDACTED_VALUE" not in str(settings.model_dump())


@pytest.mark.parametrize(
    "value",
    [
        "localhost/v1",
        "ftp://127.0.0.1/v1",
        "http://user:pass@127.0.0.1/v1",
        "http://127.0.0.1/v1?token=TEST_REDACTED_VALUE",
        "http://127.0.0.1/v1#fragment",
    ],
)
def test_diagnostic_model_base_url_rejects_unsafe_forms(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("DIG_DIAGNOSTIC_MODEL_BASE_URL", value)

    with pytest.raises(ValueError) as error:
        DiagnosticSettings(_env_file=None)
    assert "TEST_REDACTED_VALUE" not in str(error.value)


@pytest.mark.parametrize("field", ["MODEL_NAME", "MODEL_API_KEY"])
def test_diagnostic_model_name_and_key_reject_blank_values(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setenv(f"DIG_DIAGNOSTIC_{field}", "   ")

    with pytest.raises(ValueError):
        DiagnosticSettings(_env_file=None)


def test_diagnostic_settings_have_no_budget_controls() -> None:
    fields = set(DiagnosticSettings.model_fields)
    assert not any(
        marker in field
        for field in fields
        for marker in ("budget", "limit", "timeout", "token")
    )
