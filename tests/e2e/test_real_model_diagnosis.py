from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.real_model]


@pytest.mark.skip(reason="M6 single-case probe superseded by the exact M7 smoke matrix")
def test_historical_single_case_probe_is_not_an_m7_acceptance_gate() -> None:
    pass
