"""Deterministic isolation probe (T11): attempts the forbidden things.

Run this inside the sandbox the strategy gets. Every attempt is recorded as a
result line; the acceptance runner decides whether the boundary held. The probe
never modifies anything it is allowed to read — a write attempt is itself one of
the recorded attempts (it must fail).

Deterministic probes prove **boundary behaviour only**; passing them says nothing
about how a real model resists prompt injection.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

MANAGEMENT_ENV_VARS = (
    "DATABASE_URL",
    "PGPASSWORD",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "MODEL_API_KEY",
    "OPENAI_API_KEY",
    "API_KEY",
    "XIAOMI_API_KEY",
    "ANTHROPIC_API_KEY",
)

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


def _probe_plane(path: Path) -> dict[str, Any]:
    """A private plane is visible when its directory can be listed at all."""

    if path.is_dir():
        try:
            entries = sorted(item.name for item in path.iterdir())
        except OSError as error:
            return {"outcome": "DENIED", "detail": type(error).__name__}
        return {"outcome": "ALLOWED", "detail": f"listed {len(entries)} entries"}
    return _try_read(path)


def _try_read(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return {"outcome": "DENIED", "detail": type(error).__name__}
    return {"outcome": "ALLOWED", "detail": f"{len(text)} bytes"}


def _try_db_write() -> dict[str, Any]:
    try:
        import psycopg
    except ImportError:
        return {"outcome": "NO_DRIVER", "detail": "psycopg is not installed"}
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    connection = None
    try:
        connection = psycopg.connect(dsn, connect_timeout=3)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE isolation_probe (id int)")
        # The probe never leaves a trace: the attempt is rolled back either way.
        connection.rollback()
        return {"outcome": "ALLOWED", "detail": "write succeeded"}
    except Exception as error:  # noqa: BLE001 - any failure is the expected outcome
        return {"outcome": "DENIED", "detail": type(error).__name__}
    finally:
        if connection is not None:
            connection.close()


def _read_injection_fixture(sandbox: Path) -> dict[str, Any]:
    """Read the injection fixture, if present, and record that it was read."""

    fixture = sandbox / "injection_fixture.txt"
    if not fixture.is_file():
        return {"outcome": "ABSENT", "detail": "no injection fixture in this sandbox"}
    try:
        body = fixture.read_text(encoding="utf-8")
    except OSError as error:
        return {"outcome": "DENIED", "detail": type(error).__name__}
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return {
        "outcome": "READ",
        "detail": f"read {len(body)} bytes, sha256={digest[:16]}",
    }


def probe(*, host_repo: str, run_id: str, other_run_id: str, package: Path) -> dict[str, Any]:
    repo = Path(host_repo)
    sandbox = Path.cwd()
    attempts = {
        "package_read": _try_read(package / "task.json"),
        "injection_fixture": _read_injection_fixture(sandbox),
        "private_contract_read": _probe_plane(repo / "config" / "scenarios"),
        "scoring_attachment_read": _probe_plane(repo / ".dig" / "scoring-inputs"),
        "other_run_read": _probe_plane(repo / "artifacts"),
        "host_repo_listing": {
            "outcome": "ALLOWED" if repo.is_dir() else "DENIED",
            "detail": str(repo),
        },
        "management_env": {
            "outcome": "ALLOWED"
            if any(os.environ.get(name) for name in MANAGEMENT_ENV_VARS)
            else "DENIED",
            "detail": ", ".join(
                name for name in MANAGEMENT_ENV_VARS if os.environ.get(name)
            )
            or "no management variables present",
        },
        "db_write": _try_db_write(),
    }
    return {
        "probe": "p1.isolation_probe.v1",
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "attempts": attempts,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="T11 隔离探针（沙箱内运行）")
    parser.add_argument("--host-repo", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--other-run-id", default="0" * 32)
    parser.add_argument("--package", default="package")
    args = parser.parse_args(argv)
    result = probe(
        host_repo=args.host_repo,
        run_id=args.run_id,
        other_run_id=args.other_run_id,
        package=Path(args.package),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
