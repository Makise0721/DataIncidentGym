from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, StrictStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DiagnosticSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIG_DIAGNOSTIC_",
        env_file=PROJECT_ROOT / ".env.diagnostic",
        extra="ignore",
        frozen=True,
    )

    postgres_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    postgres_port: int = 55432
    postgres_database: str = "data_incident_gym"
    postgres_schema: str = "analytics"
    postgres_user: Literal["dig_reader"] = "dig_reader"
    postgres_password: SecretStr = SecretStr("dig_reader")
    model_base_url: StrictStr = "http://127.0.0.1:11434/v1"
    model_name: StrictStr = "gemma4:e4b"
    model_api_key: SecretStr = SecretStr("ollama-local")

    @field_validator("model_base_url")
    @classmethod
    def validate_model_base_url(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("model_base_url must not be blank or padded")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model_base_url must be an HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("model_base_url must not contain URL credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("model_base_url must not contain query or fragment")
        return value

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model_name must not be blank")
        return value

    @field_validator("model_api_key")
    @classmethod
    def validate_model_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("model_api_key must not be blank")
        return value
