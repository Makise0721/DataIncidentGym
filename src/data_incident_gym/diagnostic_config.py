from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DiagnosticSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIG_DIAGNOSTIC_",
        env_file=PROJECT_ROOT / ".env.diagnostic",
        extra="ignore",
    )

    postgres_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    postgres_port: int = 55432
    postgres_database: str = "data_incident_gym"
    postgres_schema: str = "analytics"
    postgres_user: Literal["dig_reader"] = "dig_reader"
    postgres_password: SecretStr = SecretStr("dig_reader")
