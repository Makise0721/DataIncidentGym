from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIG_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432
    postgres_database: str = "data_incident_gym"
    postgres_schema: str = "analytics"
    postgres_user: str = "dig_admin"
    postgres_password: SecretStr = SecretStr("dig_admin")
    command_timeout_seconds: int = 300

    def subprocess_environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "DIG_POSTGRES_HOST": self.postgres_host,
            "DIG_POSTGRES_PORT": str(self.postgres_port),
            "DIG_POSTGRES_DATABASE": self.postgres_database,
            "DIG_POSTGRES_SCHEMA": self.postgres_schema,
            "DIG_POSTGRES_USER": self.postgres_user,
            "DIG_POSTGRES_PASSWORD": self.postgres_password.get_secret_value(),
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        }
