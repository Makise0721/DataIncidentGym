"""Repeat reliability protocol (T08): ``pass^k`` over planned repetitions.

Protocol ``p1.reliability.v1``; the frozen rules live in
``docs/superpowers/specs/2026-09-16-repeat-reliability-protocol.md``.

The unit of comparison is a **group**: one scenario, one strategy, one frozen
suite identity. Trials never move between groups; the planned repetition count
comes from the frozen schedule and never from what happened to be observed; a
sample whose applicable safety gate failed is *invalid* and makes its group
incomplete instead of counting as a success or a failure; ``MODEL_ERROR``
(timeouts, budget exhaustion, protocol or runtime errors) is a failed trial.

``pass^k = C(s, k) / C(n, k)`` estimates the probability that k of k repetitions
succeed — not "at least one of k" (that is ``pass@k``). ``s < k`` gives 0 and
``n < k`` gives null. Incomplete groups report null for the main number plus a
separate complete-subset analysis that always carries its coverage.

An *invalid* sample means the harness was unsound (scenario verification, run
scope or recovery failed), never an agent-side rule violation such as a
fabricated citation or a forbidden tool: those are failed trials and stay in the
denominators.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import comb
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

RELIABILITY_PROTOCOL_VERSION = "p1.reliability.v1"
REPORTED_K_VALUES = (1, 2, 3)


class ReliabilityError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


class ReliabilityTrial(BaseModel):
    """One archived repetition as the protocol sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repeat_index: StrictInt = Field(ge=1)
    passed: StrictBool
    invalid: StrictBool
    status: StrictStr
    invalid_gate_codes: tuple[StrictStr, ...] = ()


def pass_hat_k(successes: int, trials: int, k: int) -> float | None:
    """Unbiased ``pass^k`` estimator ``C(s, k) / C(n, k)``.

    ``None`` when fewer than ``k`` repetitions were planned or observed; exactly
    ``0.0`` when the group had fewer than ``k`` successes.
    """

    if k < 1:
        raise ReliabilityError("RELIABILITY_K_INVALID", detail=str(k))
    if successes < 0 or trials < 0 or successes > trials:
        raise ReliabilityError("RELIABILITY_COUNTS_INVALID", detail=f"{successes}/{trials}")
    if trials < k:
        return None
    if successes < k:
        return 0.0
    return comb(successes, k) / comb(trials, k)


def pass_hat_series(successes: int, trials: int, planned: int) -> dict[str, float | None]:
    """``pass^k`` for every k up to the planned repetition count.

    The series always spans k = 1..planned so that a group whose observed trials
    are fewer than planned still reports explicit nulls instead of silent gaps.
    """

    return {str(k): pass_hat_k(successes, trials, k) for k in range(1, planned + 1)}


def scheduled_repeats(
    schedule: Iterable[tuple[str, str, int]],
) -> dict[tuple[str, str], tuple[int, ...]]:
    """Planned repeat indices per (scenario, strategy) from the frozen schedule."""

    planned: dict[tuple[str, str], list[int]] = {}
    for incident_case_id, strategy, repeat_index in schedule:
        planned.setdefault((incident_case_id, strategy), []).append(repeat_index)
    return {key: tuple(sorted(indices)) for key, indices in planned.items()}


def _validate_plan(key: tuple[str, str], planned: tuple[int, ...]) -> None:
    expected = tuple(range(1, len(planned) + 1))
    if planned != expected:
        raise ReliabilityError(
            "RELIABILITY_SCHEDULE_INVALID",
            detail=f"{key[0]}/{key[1]} repeat indices {planned} are not 1..{len(planned)}",
        )


def build_group(
    *,
    incident_case_id: str,
    strategy: str,
    planned_repeat_indices: Sequence[int],
    trials: Sequence[ReliabilityTrial],
) -> dict[str, Any]:
    """One scheduled group's stability record, main number and subset analysis."""

    key = (incident_case_id, strategy)
    planned = tuple(sorted(planned_repeat_indices))
    if not planned:
        raise ReliabilityError(
            "RELIABILITY_SCHEDULE_INVALID", detail=f"{incident_case_id}/{strategy} has no plan"
        )
    _validate_plan(key, planned)
    indices = tuple(trial.repeat_index for trial in trials)
    if len(indices) != len(set(indices)):
        raise ReliabilityError(
            "RELIABILITY_TRIAL_DUPLICATE", detail=f"{incident_case_id}/{strategy}"
        )
    unscheduled = tuple(index for index in indices if index not in set(planned))
    if unscheduled:
        raise ReliabilityError(
            "RELIABILITY_TRIAL_UNSCHEDULED",
            detail=f"{incident_case_id}/{strategy}: {unscheduled}",
        )
    valid = tuple(trial for trial in trials if not trial.invalid)
    seen = set(indices)
    missing = tuple(index for index in planned if index not in seen)
    invalid = tuple(sorted(trial.repeat_index for trial in trials if trial.invalid))
    complete = not missing and not invalid
    successes = sum(trial.passed for trial in valid)
    pass_hat = (
        pass_hat_series(successes, len(planned), len(planned))
        if complete
        else dict.fromkeys((str(k) for k in range(1, len(planned) + 1)), None)
    )
    subset = None
    if not complete:
        subset = {
            "valid_trials": len(valid),
            "successes": successes,
            "coverage": len(valid) / len(planned),
            "excluded_repeat_indices": tuple(sorted((*missing, *invalid))),
            "pass_hat": pass_hat_series(successes, len(valid), len(planned)),
        }
    return {
        "group_id": f"{incident_case_id}/{strategy}",
        "incident_case_id": incident_case_id,
        "strategy": strategy,
        "in_schedule": True,
        "planned_repetitions": len(planned),
        "observed_trials": len(trials),
        "valid_trials": len(valid),
        "successes": successes if complete else None,
        "complete": complete,
        "missing_repeat_indices": missing,
        "invalid_repeat_indices": invalid,
        "invalid_gate_codes": sorted(
            {code for trial in trials for code in trial.invalid_gate_codes}
        ),
        "pass_hat": pass_hat,
        "complete_subset": subset,
    }


def unscheduled_group(
    *,
    incident_case_id: str,
    strategy: str,
    trials: Sequence[ReliabilityTrial],
) -> dict[str, Any]:
    """A group outside the frozen schedule: counted, never scored.

    Nothing is computed for it — the protocol's n comes from the frozen
    schedule, so a group without one has no planned repetitions to score.
    """

    return {
        "group_id": f"{incident_case_id}/{strategy}",
        "incident_case_id": incident_case_id,
        "strategy": strategy,
        "in_schedule": False,
        "planned_repetitions": None,
        "observed_trials": len(trials),
        "valid_trials": sum(not trial.invalid for trial in trials),
        "successes": None,
        "complete": False,
        "missing_repeat_indices": (),
        "invalid_repeat_indices": tuple(
            sorted(trial.repeat_index for trial in trials if trial.invalid)
        ),
        "invalid_gate_codes": sorted(
            {code for trial in trials for code in trial.invalid_gate_codes}
        ),
        "pass_hat": {},
        "complete_subset": None,
        "note": "group is not part of the frozen schedule",
    }


def macro_pass_hat(
    groups: Sequence[Mapping[str, Any]],
    k_values: Sequence[int] = REPORTED_K_VALUES,
) -> dict[str, dict[str, Any]]:
    """Group-level macro average over complete groups only.

    Trials are never pooled across groups: the sample size is reported as the
    number of included groups and their planned trials.
    """

    complete = [group for group in groups if group.get("in_schedule") and group.get("complete")]
    macro: dict[str, dict[str, Any]] = {}
    for k in k_values:
        key = str(k)
        included = [group for group in complete if group["pass_hat"].get(key) is not None]
        values = [group["pass_hat"][key] for group in included]
        macro[key] = {
            "value": sum(values) / len(values) if values else None,
            "groups": len(included),
            "trials": sum(int(group["planned_repetitions"]) for group in included),
            "zero_denominator_reason": None
            if values
            else f"no complete group with n >= {k}",
        }
    return macro


def reliability_for(
    *,
    schedule: Iterable[tuple[str, str, int]],
    observed: Mapping[tuple[str, str], Sequence[ReliabilityTrial]],
    k_values: Sequence[int] = REPORTED_K_VALUES,
) -> dict[str, Any]:
    """Full stability block: per-group records plus the macro summary."""

    planned = scheduled_repeats(schedule)
    groups = [
        build_group(
            incident_case_id=incident_case_id,
            strategy=strategy,
            planned_repeat_indices=planned[(incident_case_id, strategy)],
            trials=tuple(observed.get((incident_case_id, strategy), ())),
        )
        if (incident_case_id, strategy) in planned
        else unscheduled_group(
            incident_case_id=incident_case_id,
            strategy=strategy,
            trials=tuple(observed.get((incident_case_id, strategy), ())),
        )
        for incident_case_id, strategy in sorted(set(planned) | set(observed))
    ]
    scheduled = [group for group in groups if group["in_schedule"]]
    return {
        "protocol_version": RELIABILITY_PROTOCOL_VERSION,
        "k_values": list(k_values),
        "groups": groups,
        "groups_total": len(groups),
        "groups_scheduled": len(scheduled),
        "groups_complete": sum(bool(group["complete"]) for group in scheduled),
        "groups_incomplete": sum(not group["complete"] for group in scheduled),
        "groups_unscheduled": len(groups) - len(scheduled),
        "macro_pass_hat": macro_pass_hat(groups, k_values),
        "note": (
            "pass^k = C(s, k)/C(n, k) is the probability that k of k repetitions succeed, "
            "not pass@k; the macro average covers complete groups only and never pools trials "
            "across scenarios, strategies or versions"
        ),
    }


__all__ = [
    "RELIABILITY_PROTOCOL_VERSION",
    "REPORTED_K_VALUES",
    "ReliabilityError",
    "ReliabilityTrial",
    "build_group",
    "macro_pass_hat",
    "pass_hat_k",
    "pass_hat_series",
    "reliability_for",
    "scheduled_repeats",
    "unscheduled_group",
]
