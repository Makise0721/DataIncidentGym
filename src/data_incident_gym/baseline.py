from __future__ import annotations

import json
import subprocess
from pathlib import Path


class BaselineError(RuntimeError):
    """Raised when the deterministic baseline cannot be built."""


def validate_upstream_fixture(project_root: Path) -> str:
    spec_path = project_root / "config" / "upstream" / "jaffle_shop.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fixture = project_root / spec["path"]
    required = (
        fixture / "dbt_project.yml",
        fixture / "seeds" / "raw_customers.csv",
        fixture / "seeds" / "raw_orders.csv",
        fixture / "seeds" / "raw_payments.csv",
        fixture / "models" / "staging" / "stg_payments.sql",
    )
    if not all(path.is_file() for path in required):
        raise BaselineError(
            "Jaffle Shop submodule 未初始化；请运行 "
            "git submodule update --init --recursive"
        )

    result = subprocess.run(
        ["git", "-C", str(fixture), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or actual != spec["commit"]:
        raise BaselineError(
            f"Jaffle Shop commit 不匹配：expected={spec['commit']} actual={actual or 'unknown'}"
        )
    return actual
