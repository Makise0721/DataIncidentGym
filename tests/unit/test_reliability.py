"""T08 repeat reliability: pass^k, group identity and incomplete-sample rules.

All fixtures are hand-checkable: n=3 with s=2 must reproduce the protocol's own
example (pass^1 = 2/3, pass^2 = 1/3, pass^3 = 0), a single repetition must never
produce pass^2/pass^3, and missing or invalid samples must leave the main number
null while a clearly labelled complete-subset analysis carries its coverage.
"""

from __future__ import annotations

import pytest

from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.reliability import (
    RELIABILITY_PROTOCOL_VERSION,
    ReliabilityError,
    ReliabilityTrial,
    build_group,
    pass_hat_k,
    reliability_for,
    scheduled_repeats,
)


def trial(
    repeat_index: int,
    *,
    passed: bool,
    invalid: bool = False,
    gate_codes: tuple[str, ...] = (),
) -> ReliabilityTrial:
    return ReliabilityTrial(
        repeat_index=repeat_index,
        passed=passed,
        invalid=invalid,
        status="MODEL_ERROR" if not passed and not invalid else "CONFIRMED",
        invalid_gate_codes=gate_codes,
    )


def test_pass_hat_follows_the_frozen_formula() -> None:
    # The protocol's own example: n=3, s=2.
    assert pass_hat_k(2, 3, 1) == pytest.approx(2 / 3)
    assert pass_hat_k(2, 3, 2) == pytest.approx(1 / 3)
    assert pass_hat_k(2, 3, 3) == 0.0
    assert pass_hat_k(3, 3, 3) == 1.0
    assert pass_hat_k(0, 3, 1) == 0.0
    assert pass_hat_k(1, 3, 1) == pytest.approx(1 / 3)


def test_pass_hat_is_null_when_fewer_repetitions_than_k() -> None:
    assert pass_hat_k(1, 1, 1) == 1.0
    assert pass_hat_k(1, 1, 2) is None
    assert pass_hat_k(1, 1, 3) is None
    assert pass_hat_k(0, 0, 1) is None


def test_pass_hat_rejects_impossible_inputs() -> None:
    with pytest.raises(ReliabilityError, match="RELIABILITY_K_INVALID"):
        pass_hat_k(1, 1, 0)
    with pytest.raises(ReliabilityError, match="RELIABILITY_COUNTS_INVALID"):
        pass_hat_k(2, 1, 1)


def test_group_reports_the_series_only_when_complete() -> None:
    group = build_group(
        incident_case_id="case_a",
        strategy="STATIC_SKILL",
        planned_repeat_indices=(1, 2, 3),
        trials=(trial(1, passed=True), trial(2, passed=True), trial(3, passed=False)),
    )

    assert group["complete"] is True
    assert group["successes"] == 2
    assert group["pass_hat"] == {
        "1": pytest.approx(2 / 3),
        "2": pytest.approx(1 / 3),
        "3": 0.0,
    }
    assert group["complete_subset"] is None


def test_missing_repeat_keeps_the_main_number_null_and_shows_coverage() -> None:
    group = build_group(
        incident_case_id="case_a",
        strategy="STATIC_SKILL",
        planned_repeat_indices=(1, 2, 3),
        trials=(trial(1, passed=True), trial(2, passed=True)),
    )

    assert group["complete"] is False
    assert group["successes"] is None
    assert group["pass_hat"] == {"1": None, "2": None, "3": None}
    assert group["missing_repeat_indices"] == (3,)
    subset = group["complete_subset"]
    assert subset["valid_trials"] == 2
    assert subset["coverage"] == pytest.approx(2 / 3)
    assert subset["excluded_repeat_indices"] == (3,)
    # The subset is scored at its own observed size, never at a shrunken plan.
    assert subset["pass_hat"] == {"1": 1.0, "2": 1.0, "3": None}


def test_invalid_sample_is_neither_success_nor_failure() -> None:
    group = build_group(
        incident_case_id="case_a",
        strategy="STATIC_SKILL",
        planned_repeat_indices=(1, 2, 3),
        trials=(
            trial(1, passed=True),
            trial(2, passed=True),
            trial(3, passed=False, invalid=True, gate_codes=("ENVIRONMENT_VERIFIED",)),
        ),
    )

    assert group["complete"] is False
    assert group["invalid_repeat_indices"] == (3,)
    assert group["invalid_gate_codes"] == ["ENVIRONMENT_VERIFIED"]
    assert group["pass_hat"] == {"1": None, "2": None, "3": None}
    # Two valid successes but an incomplete plan: never reported as pass^3 = 0.
    assert group["complete_subset"]["successes"] == 2
    assert group["complete_subset"]["coverage"] == pytest.approx(2 / 3)


def test_model_error_is_a_failed_trial_not_an_invalid_sample() -> None:
    group = build_group(
        incident_case_id="case_a",
        strategy="STATIC_SKILL",
        planned_repeat_indices=(1, 2, 3),
        trials=(
            trial(1, passed=False),
            trial(2, passed=False),
            trial(3, passed=False),
        ),
    )

    assert group["complete"] is True
    assert group["successes"] == 0
    assert group["pass_hat"] == {"1": 0.0, "2": 0.0, "3": 0.0}


def test_group_identity_is_validated() -> None:
    with pytest.raises(ReliabilityError, match="RELIABILITY_TRIAL_DUPLICATE"):
        build_group(
            incident_case_id="case_a",
            strategy="STATIC_SKILL",
            planned_repeat_indices=(1, 2, 3),
            trials=(trial(1, passed=True), trial(1, passed=False)),
        )
    with pytest.raises(ReliabilityError, match="RELIABILITY_TRIAL_UNSCHEDULED"):
        build_group(
            incident_case_id="case_a",
            strategy="STATIC_SKILL",
            planned_repeat_indices=(1, 2, 3),
            trials=(trial(1, passed=True), trial(4, passed=True)),
        )
    with pytest.raises(ReliabilityError, match="RELIABILITY_SCHEDULE_INVALID"):
        build_group(
            incident_case_id="case_a",
            strategy="STATIC_SKILL",
            planned_repeat_indices=(1, 3),
            trials=(),
        )
    # An empty plan is not a group: nothing may be scored without a schedule.
    with pytest.raises(ReliabilityError, match="RELIABILITY_SCHEDULE_INVALID"):
        build_group(
            incident_case_id="case_a",
            strategy="STATIC_SKILL",
            planned_repeat_indices=(),
            trials=(),
        )


def test_scheduled_repeats_groups_by_scenario_and_strategy() -> None:
    planned = scheduled_repeats(
        (
            ("case_a", "STATIC_SKILL", 3),
            ("case_a", "STATIC_SKILL", 1),
            ("case_a", "DIAGNOSTIC_KERNEL", 1),
            ("case_a", "STATIC_SKILL", 2),
            ("case_b", "STATIC_SKILL", 1),
        )
    )

    assert planned[("case_a", "STATIC_SKILL")] == (1, 2, 3)
    assert planned[("case_a", "DIAGNOSTIC_KERNEL")] == (1,)
    assert planned[("case_b", "STATIC_SKILL")] == (1,)


def test_reliability_never_mixes_scenarios_or_strategies() -> None:
    report = reliability_for(
        schedule=(
            ("case_a", "STATIC_SKILL", 1),
            ("case_a", "STATIC_SKILL", 2),
            ("case_a", "STATIC_SKILL", 3),
            ("case_a", "DIAGNOSTIC_KERNEL", 1),
            ("case_a", "DIAGNOSTIC_KERNEL", 2),
            ("case_a", "DIAGNOSTIC_KERNEL", 3),
            ("case_b", "STATIC_SKILL", 1),
            ("case_b", "STATIC_SKILL", 2),
            ("case_b", "STATIC_SKILL", 3),
        ),
        observed={
            ("case_a", "STATIC_SKILL"): (
                trial(1, passed=True),
                trial(2, passed=True),
                trial(3, passed=False),
            ),
            ("case_a", "DIAGNOSTIC_KERNEL"): (
                trial(1, passed=True),
                trial(2, passed=True),
                trial(3, passed=True),
            ),
            ("case_b", "STATIC_SKILL"): (
                trial(1, passed=False),
                trial(2, passed=False),
                trial(3, passed=False),
            ),
        },
    )

    assert report["protocol_version"] == RELIABILITY_PROTOCOL_VERSION
    assert report["groups_scheduled"] == 3
    assert report["groups_complete"] == 3
    by_id = {group["group_id"]: group for group in report["groups"]}
    assert by_id["case_a/STATIC_SKILL"]["pass_hat"]["2"] == pytest.approx(1 / 3)
    assert by_id["case_a/DIAGNOSTIC_KERNEL"]["pass_hat"]["3"] == 1.0
    assert by_id["case_b/STATIC_SKILL"]["pass_hat"]["1"] == 0.0
    # Macro over the three complete groups: only group-level values, no pooling.
    macro = report["macro_pass_hat"]
    assert macro["1"] == {
        "value": pytest.approx((2 / 3 + 1.0 + 0.0) / 3),
        "groups": 3,
        "trials": 9,
        "zero_denominator_reason": None,
    }
    assert macro["3"]["value"] == pytest.approx((0.0 + 1.0 + 0.0) / 3)


def test_macro_average_covers_complete_groups_only() -> None:
    report = reliability_for(
        schedule=(("case_a", "STATIC_SKILL", 1), ("case_a", "STATIC_SKILL", 2)),
        observed={
            ("case_a", "STATIC_SKILL"): (trial(1, passed=True),),  # repeat 2 missing
        },
    )

    assert report["groups_complete"] == 0
    assert report["groups_incomplete"] == 1
    assert report["macro_pass_hat"]["1"]["value"] is None
    assert report["macro_pass_hat"]["1"]["zero_denominator_reason"] == (
        "no complete group with n >= 1"
    )
    assert report["macro_pass_hat"]["2"]["value"] is None
    assert report["macro_pass_hat"]["2"]["zero_denominator_reason"] == (
        "no complete group with n >= 2"
    )


def test_group_outside_the_schedule_is_counted_but_never_scored() -> None:
    report = reliability_for(
        schedule=(("case_a", "STATIC_SKILL", 1),),
        observed={
            ("case_a", "STATIC_SKILL"): (trial(1, passed=True),),
            ("case_extra", "STATIC_SKILL"): (trial(1, passed=True),),
        },
    )

    assert report["groups_unscheduled"] == 1
    extra = next(
        group for group in report["groups"] if group["group_id"] == "case_extra/STATIC_SKILL"
    )
    assert extra["in_schedule"] is False
    assert extra["pass_hat"] == {}
    assert extra["note"] == "group is not part of the frozen schedule"
    # The unscheduled group never enters the macro average.
    assert report["macro_pass_hat"]["1"]["groups"] == 1


def test_frozen_schedule_plans_three_repeats_for_main_strategies_only() -> None:
    manifest = build_manifest("a" * 40)
    planned = scheduled_repeats(
        (cell.incident_case_id, cell.strategy.value, cell.repeat_index) for cell in manifest.cells
    )
    main = {
        strategy
        for (_, strategy), indices in planned.items()
        if len(indices) == 3
    }
    single = {
        strategy
        for (_, strategy), indices in planned.items()
        if len(indices) == 1
    }

    assert main == {"STATIC_SKILL", "DIAGNOSTIC_KERNEL"}
    assert single == {"NO_TOOL", "FIXED_RULE", "KERNEL_NO_LINEAGE", "KERNEL_NO_SCHEMA"}
    assert all(indices == (1, 2, 3) for indices in planned.values() if len(indices) == 3)
