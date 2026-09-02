from __future__ import annotations

from types import SimpleNamespace

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


def _runner(tmp_path) -> DoctorRunner:
    return DoctorRunner(
        DiagnosticSettings(_env_file=None),
        tmp_path,
        run_command=lambda *_args, **_kwargs: None,
        db_connect=lambda **_kwargs: None,
        url_open=lambda *_args, **_kwargs: None,
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
