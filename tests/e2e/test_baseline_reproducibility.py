from pathlib import Path

import pytest

from data_incident_gym.baseline import BaselineBuilder, BaselineSummary
from data_incident_gym.config import Settings


def _schema_row_count_summary(summary: BaselineSummary) -> tuple[str, tuple[tuple[str, int], ...]]:
    return (
        summary.schema,
        tuple((relation.name, relation.row_count) for relation in summary.relations),
    )


@pytest.mark.e2e
def test_fixed_seed_has_one_fingerprint_across_ten_resets(project_root: Path) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), project_root)
    summaries: list[BaselineSummary] = []

    for run_number in range(1, 11):
        summary = builder.build()
        summaries.append(summary)
        print(
            f"baseline build {run_number}/10: "
            f"fingerprint={summary.fingerprint} "
            f"schema_row_counts={_schema_row_count_summary(summary)}"
        )

    fingerprints = {summary.fingerprint for summary in summaries}
    schema_row_counts = {_schema_row_count_summary(summary) for summary in summaries}

    print(f"unique fingerprints: {sorted(fingerprints)}")
    print(f"unique schema/row-count summaries: {sorted(schema_row_counts)}")

    assert len(fingerprints) == 1
    assert len(schema_row_counts) == 1
