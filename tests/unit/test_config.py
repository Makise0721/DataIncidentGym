from data_incident_gym.config import Settings


def test_m1_defaults_match_compose_and_dbt_profile() -> None:
    settings = Settings(_env_file=None)

    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 55432
    assert settings.postgres_database == "data_incident_gym"
    assert settings.postgres_schema == "analytics"
    assert settings.postgres_user == "dig_admin"
    assert settings.postgres_password.get_secret_value() == "dig_admin"


def test_subprocess_environment_disables_dbt_telemetry() -> None:
    env = Settings(_env_file=None).subprocess_environment()

    assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
    assert env["DIG_POSTGRES_PORT"] == "55432"
    assert env["DIG_POSTGRES_PASSWORD"] == "dig_admin"
