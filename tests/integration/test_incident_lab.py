import json
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab


@pytest.mark.integration
def test_incident_lab_captures_expected_failure_and_recovers(
    project_root: Path,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    baseline = lab.reset(CASE_ID)

    try:
        injected = lab.inject(CASE_ID)
        run = lab.build(CASE_ID)

        assert injected.state == "INJECTED"
        assert injected.fingerprint != baseline.fingerprint
        assert run.dbt_exit_code != 0
        assert run.verification.status == "EXPECTED_FAILURE"
        assert run.verification.failed_nodes == (
            "model.jaffle_shop.stg_payments",
        )
        assert run.verification.affected_assets == (
            "model.jaffle_shop.stg_payments",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.customers",
        )
        metadata = json.loads(
            (run.artifact_dir / "metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["run_id"] == run.run_id
        assert (run.artifact_dir / "dbt/target/manifest.json").is_file()
        assert (run.artifact_dir / "dbt/target/run_results.json").is_file()
        assert (run.artifact_dir / "dbt/logs/dbt.log").is_file()
        secret = lab.settings.postgres_password.get_secret_value()
        for relative_path in (
            "metadata.json",
            "dbt/stdout.log",
            "dbt/stderr.log",
            "dbt/logs/dbt.log",
        ):
            assert secret not in (run.artifact_dir / relative_path).read_text(
                encoding="utf-8"
            )
        for json_path in run.artifact_dir.rglob("*.json"):
            assert secret not in json_path.read_text(encoding="utf-8")
    finally:
        recovered = lab.reset(CASE_ID)

    assert recovered.state == "HEALTHY"
    assert recovered.fingerprint == baseline.fingerprint
    assert run.artifact_dir.is_dir()
