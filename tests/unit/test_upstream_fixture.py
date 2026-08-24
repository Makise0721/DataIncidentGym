from pathlib import Path

import pytest

from data_incident_gym.baseline import BaselineError, validate_upstream_fixture


EXPECTED_COMMIT = "36bde6cba69d962b83be1d52fc65a0dce1cb4ebb"


def test_fixed_upstream_fixture_is_ready(project_root: Path) -> None:
    assert validate_upstream_fixture(project_root) == EXPECTED_COMMIT


def test_missing_fixture_has_actionable_message(
    tmp_path: Path,
    project_root: Path,
) -> None:
    (tmp_path / "config" / "upstream").mkdir(parents=True)
    source = (project_root / "config/upstream/jaffle_shop.json").read_text(
        encoding="utf-8"
    )
    (tmp_path / "config" / "upstream" / "jaffle_shop.json").write_text(
        source,
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="git submodule update --init --recursive"):
        validate_upstream_fixture(tmp_path)
