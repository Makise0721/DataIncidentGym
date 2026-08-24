# M1 Healthy Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Windows 11 / PowerShell 7 上，用一条 `uv run data-incident-gym pipeline build` 命令启动固定版本 PostgreSQL、运行固定 Jaffle Shop seeds/models/tests，并生成可连续 10 次复现的健康基线摘要。

**Architecture:** CLI 只依赖一个深模块 `BaselineBuilder`。该模块隐藏 Git submodule 校验、Docker Compose 启动、dbt 调用、artifact 校验、PostgreSQL 元数据读取和确定性摘要落盘；`Settings` 统一环境变量和固定仓库路径。M1 不实现故障注入、Agent、证据工具、doctor 或诊断权限，这些只通过稳定文件和接口为后续模块留下接缝。

**Tech Stack:** Python 3.12.10, uv 0.11.24, Typer 0.27.1, Pydantic 2.13.4, pydantic-settings 2.15.0, psycopg 3.3.4, dbt-core 1.12.3, dbt-postgres 1.11.0, PostgreSQL 17.6 Alpine, pytest 9.1.1, Ruff 0.16.4, Docker Compose

---

## 范围和验收映射

本计划只覆盖已批准需求的 M1：

| 需求 | 实现证据 |
|---|---|
| 固定 PostgreSQL | `compose.yaml` 使用 tag + digest；CLI 执行 `docker compose up -d --wait postgres` |
| 固定 Jaffle Shop | Git submodule 固定 `36bde6c...`；机器可读 provenance + 自动校验 |
| PostgreSQL dbt 适配 | 外置 `config/dbt/profiles.yml`；不修改 submodule |
| models/tests 成功 | 真实 `dbt seed --full-refresh` + `dbt build`；检查 `run_results.json` |
| artifacts 存在 | `.dig/dbt/target/{manifest,run_results}.json` 与 `.dig/dbt/logs/dbt.log` |
| 结构和行数稳定 | PostgreSQL inspector + canonical JSON SHA-256；真实环境连续 10 次一致 |
| 一条命令闭环 | `uv run data-incident-gym pipeline build` |

明确不做：M2 故障注入、M3 只读证据工具、M4 PydanticAI/Ollama、M5 评测报告、自由文本交互、Airflow/OpenLineage/Marquez、Web UI。

## 最终文件结构

```text
.
├── .github/workflows/ci.yml
├── .gitignore
├── .gitmodules
├── .python-version
├── LICENSE
├── README.md
├── THIRD_PARTY_NOTICES.md
├── compose.yaml
├── config/
│   ├── dbt/profiles.yml
│   └── upstream/jaffle_shop.json
├── docs/
│   ├── requirements.md
│   └── superpowers/plans/2026-08-24-m1-healthy-baseline.md
├── pyproject.toml
├── src/data_incident_gym/
│   ├── __init__.py
│   ├── baseline.py
│   ├── cli.py
│   └── config.py
├── tests/
│   ├── conftest.py
│   ├── e2e/test_baseline_reproducibility.py
│   ├── integration/test_pipeline_build.py
│   └── unit/
│       ├── test_baseline.py
│       ├── test_cli.py
│       ├── test_config.py
│       ├── test_package.py
│       └── test_upstream_fixture.py
├── third_party/jaffle_shop/       # Git submodule
└── uv.lock
```

运行时生成的 `.dig/`、submodule 内可能已有的 ignored 目录、`.venv/` 和 Python cache 均不提交。

---

### Task 1: 固化已批准需求并建立最小 Python 工程

**Files:**
- Commit: `docs/requirements.md`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/data_incident_gym/__init__.py`
- Create: `tests/unit/test_package.py`

- [ ] **Step 1: 单独提交已批准需求基线**

```powershell
git add docs/requirements.md docs/superpowers/plans/2026-08-24-m1-healthy-baseline.md
git commit -m "docs: approve requirements and M1 plan"
```

Expected: commit 只包含已批准的需求文档和 M1 实施计划，不包含实现代码。

- [ ] **Step 2: 先写失败的包版本测试**

```python
# tests/unit/test_package.py
from data_incident_gym import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 3: 运行测试并确认 RED**

```powershell
python -m pytest tests/unit/test_package.py -q
```

Expected: 因 `data_incident_gym` 尚不存在而失败；若当前环境尚无 pytest，也只记录依赖未安装，不以此替代后续 RED。

- [ ] **Step 4: 写最小工程配置**

`.python-version`：

```text
3.12.10
```

`.gitignore`：

```gitignore
.venv/
.env
.dig/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
artifacts/
```

`pyproject.toml`：

```toml
[project]
name = "data-incident-gym"
version = "0.1.0"
description = "A reproducible data-incident diagnosis agent benchmark."
requires-python = ">=3.12,<3.13"
dependencies = [
  "dbt-core==1.12.3",
  "dbt-postgres==1.11.0",
  "psycopg[binary]==3.3.4",
  "pydantic==2.13.4",
  "pydantic-settings==2.15.0",
  "typer==0.27.1",
]

[project.scripts]
data-incident-gym = "data_incident_gym.cli:app"

[dependency-groups]
dev = [
  "pytest==9.1.1",
  "ruff==0.16.4",
]

[build-system]
requires = ["hatchling==1.32.0"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/data_incident_gym"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "integration: requires Docker and PostgreSQL",
  "e2e: runs the complete M1 build repeatedly",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

`src/data_incident_gym/__init__.py`：

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: 锁定并安装依赖**

```powershell
uv lock
uv sync --frozen
uv run pytest tests/unit/test_package.py -q
uv run ruff check src tests
```

Expected: `uv.lock` 生成；1 test passed；Ruff 通过。检查 `uv.lock` 中不存在未解析的本地路径依赖。

- [ ] **Step 6: 提交工程骨架**

```powershell
git add .python-version .gitignore pyproject.toml uv.lock src tests/unit/test_package.py
git commit -m "build: initialize Python project"
```

---

### Task 2: 固定并校验第三方 Jaffle Shop submodule

**Files:**
- Create: `.gitmodules`
- Add gitlink: `third_party/jaffle_shop`
- Create: `config/upstream/jaffle_shop.json`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `LICENSE`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_upstream_fixture.py`
- Create: `src/data_incident_gym/baseline.py`

- [ ] **Step 1: 添加并固定 submodule**

```powershell
git submodule add --branch duckdb https://github.com/dbt-labs/jaffle_shop_duckdb.git third_party/jaffle_shop
git -C third_party/jaffle_shop checkout 36bde6cba69d962b83be1d52fc65a0dce1cb4ebb
git submodule status
```

Expected: 输出 commit 以 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 开头，路径为 `third_party/jaffle_shop`。

- [ ] **Step 2: 添加机器可读 provenance**

`config/upstream/jaffle_shop.json`：

```json
{
  "name": "dbt-labs/jaffle_shop_duckdb",
  "repository_url": "https://github.com/dbt-labs/jaffle_shop_duckdb.git",
  "branch": "duckdb",
  "commit": "36bde6cba69d962b83be1d52fc65a0dce1cb4ebb",
  "license": "Apache-2.0",
  "path": "third_party/jaffle_shop"
}
```

`THIRD_PARTY_NOTICES.md` 必须逐项写明：项目名、URL、固定分支、固定 commit、Apache-2.0、submodule 路径、复用的 seeds/models/schema tests，以及“所有 PostgreSQL 配置均在 submodule 外部，未修改上游源码”。

项目 `LICENSE` 使用完整 Apache License 2.0 正文；从已固定 submodule 的 `LICENSE` 原样复制，并确认两者 SHA-256 相同：

```powershell
Copy-Item -LiteralPath third_party/jaffle_shop/LICENSE -Destination LICENSE
Get-FileHash LICENSE -Algorithm SHA256
Get-FileHash third_party/jaffle_shop/LICENSE -Algorithm SHA256
```

- [ ] **Step 3: 先写 fixture 校验测试**

```python
# tests/unit/test_upstream_fixture.py
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
```

在 `tests/conftest.py` 增加唯一的仓库根 fixture：

```python
from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

- [ ] **Step 4: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_upstream_fixture.py -q
```

Expected: `baseline` 模块或 `validate_upstream_fixture` 尚不存在。

- [ ] **Step 5: 实现最小 submodule 校验**

`src/data_incident_gym/baseline.py` 首先只实现：

```python
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
```

- [ ] **Step 6: 验证 GREEN 和归属完整性**

```powershell
uv run pytest tests/unit/test_upstream_fixture.py -q
git diff --check
git status --short
```

Expected: 2 tests passed；submodule 中没有 tracked modification：

```powershell
git -C third_party/jaffle_shop status --short
```

Expected: 无输出。

- [ ] **Step 7: 提交第三方基线**

```powershell
git add .gitmodules third_party/jaffle_shop config/upstream/jaffle_shop.json THIRD_PARTY_NOTICES.md LICENSE src/data_incident_gym/baseline.py tests/conftest.py tests/unit/test_upstream_fixture.py
git commit -m "build: pin Jaffle Shop fixture"
```

---

### Task 3: 固定 PostgreSQL 与外置 dbt profile

**Files:**
- Create: `compose.yaml`
- Create: `.env.example`
- Create: `config/dbt/profiles.yml`
- Create: `src/data_incident_gym/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: 先写默认配置测试**

```python
# tests/unit/test_config.py
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
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_config.py -q
```

Expected: `data_incident_gym.config` 不存在。

- [ ] **Step 3: 实现唯一配置入口**

```python
# src/data_incident_gym/config.py
from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIG_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432
    postgres_database: str = "data_incident_gym"
    postgres_schema: str = "analytics"
    postgres_user: str = "dig_admin"
    postgres_password: SecretStr = SecretStr("dig_admin")
    command_timeout_seconds: int = 300

    def subprocess_environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "DIG_POSTGRES_HOST": self.postgres_host,
            "DIG_POSTGRES_PORT": str(self.postgres_port),
            "DIG_POSTGRES_DATABASE": self.postgres_database,
            "DIG_POSTGRES_SCHEMA": self.postgres_schema,
            "DIG_POSTGRES_USER": self.postgres_user,
            "DIG_POSTGRES_PASSWORD": self.postgres_password.get_secret_value(),
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        }
```

`.env.example` 只包含本地虚构环境默认值，不包含真实凭据：

```dotenv
DIG_POSTGRES_HOST=127.0.0.1
DIG_POSTGRES_PORT=55432
DIG_POSTGRES_DATABASE=data_incident_gym
DIG_POSTGRES_SCHEMA=analytics
DIG_POSTGRES_USER=dig_admin
DIG_POSTGRES_PASSWORD=dig_admin
```

- [ ] **Step 4: 添加固定 Compose 服务**

`compose.yaml`：

```yaml
name: data-incident-gym

services:
  postgres:
    image: postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94
    environment:
      POSTGRES_DB: ${DIG_POSTGRES_DATABASE:-data_incident_gym}
      POSTGRES_USER: ${DIG_POSTGRES_USER:-dig_admin}
      POSTGRES_PASSWORD: ${DIG_POSTGRES_PASSWORD:-dig_admin}
    ports:
      - "${DIG_POSTGRES_PORT:-55432}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 2s
      timeout: 3s
      retries: 30
    volumes:
      - dig_postgres_data:/var/lib/postgresql/data

volumes:
  dig_postgres_data:
```

该 digest 是 `postgres:17.6-alpine` 的多架构 manifest digest，Windows Docker Desktop 和 Ubuntu CI 均按各自架构解析。

- [ ] **Step 5: 添加外置 dbt profile**

`config/dbt/profiles.yml`：

```yaml
config:
  send_anonymous_usage_stats: false

jaffle_shop:
  target: dev
  outputs:
    dev:
      type: postgres
      host: "{{ env_var('DIG_POSTGRES_HOST', '127.0.0.1') }}"
      port: {{ env_var('DIG_POSTGRES_PORT', '55432') | int }}
      dbname: "{{ env_var('DIG_POSTGRES_DATABASE', 'data_incident_gym') }}"
      schema: "{{ env_var('DIG_POSTGRES_SCHEMA', 'analytics') }}"
      user: "{{ env_var('DIG_POSTGRES_USER', 'dig_admin') }}"
      password: "{{ env_var('DIG_POSTGRES_PASSWORD', 'dig_admin') }}"
      threads: 1
      connect_timeout: 10
```

不得编辑 `third_party/jaffle_shop/profiles.yml` 或任何上游文件。

- [ ] **Step 6: 验证配置和 Compose 解析**

```powershell
uv run pytest tests/unit/test_config.py -q
docker compose -f compose.yaml config --quiet
git -C third_party/jaffle_shop status --short
```

Expected: 2 tests passed；Compose exit 0；submodule 无输出。

- [ ] **Step 7: 提交基础设施配置**

```powershell
git add .env.example compose.yaml config/dbt/profiles.yml src/data_incident_gym/config.py tests/unit/test_config.py
git commit -m "feat: configure PostgreSQL baseline"
```

---

### Task 4: 用一个深模块封装 Compose 与 dbt 健康构建

**Files:**
- Modify: `src/data_incident_gym/baseline.py`
- Create: `tests/unit/test_baseline.py`

- [ ] **Step 1: 先写命令编排测试**

测试通过注入的 `run_command` 记录参数，禁止 `shell=True`，并断言严格顺序：

```python
# tests/unit/test_baseline.py
from pathlib import Path
from subprocess import CompletedProcess

from data_incident_gym.baseline import BaselineBuilder
from data_incident_gym.config import Settings


def test_build_runs_compose_seed_then_dbt_build(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    builder = BaselineBuilder(
        settings=Settings(_env_file=None),
        project_root=tmp_path,
        run_command=fake_run,
    )
    builder.start_postgres()
    builder.run_dbt()

    assert commands[0][:3] == ["docker", "compose", "-f"]
    assert commands[0][-4:] == ["up", "-d", "--wait", "postgres"]
    assert commands[1][0:2] == ["dbt", "seed"]
    assert "--full-refresh" in commands[1]
    assert commands[2][0:2] == ["dbt", "build"]
```

另加失败测试：非零 return code 必须抛 `BaselineError`，错误中包含阶段和 return code，但不得包含 `DIG_POSTGRES_PASSWORD` 的值。

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_baseline.py -q
```

Expected: `BaselineBuilder` 尚不存在。

- [ ] **Step 3: 实现命令边界**

在 `baseline.py` 中加入：

```python
from collections.abc import Callable, Sequence
from subprocess import CompletedProcess

from data_incident_gym.config import PROJECT_ROOT, Settings


RunCommand = Callable[..., CompletedProcess[str]]


class BaselineBuilder:
    def __init__(
        self,
        settings: Settings,
        project_root: Path = PROJECT_ROOT,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.run_command = run_command
        self.dbt_project = project_root / "third_party" / "jaffle_shop"
        self.dbt_profiles = project_root / "config" / "dbt"
        self.dbt_target = project_root / ".dig" / "dbt" / "target"
        self.dbt_logs = project_root / ".dig" / "dbt" / "logs"

    def _run(self, stage: str, command: Sequence[str], cwd: Path) -> CompletedProcess[str]:
        try:
            result = self.run_command(
                list(command),
                cwd=cwd,
                env=self.settings.subprocess_environment(),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BaselineError(f"{stage} 无法执行：{exc}") from exc
        if result.returncode != 0:
            secret = self.settings.postgres_password.get_secret_value()
            stdout = result.stdout.replace(secret, "***") if secret else result.stdout
            stderr = result.stderr.replace(secret, "***") if secret else result.stderr
            raise BaselineError(
                f"{stage} 失败（exit={result.returncode}）\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        return result

    def start_postgres(self) -> None:
        self._run(
            "启动 PostgreSQL",
            [
                "docker",
                "compose",
                "-f",
                str(self.project_root / "compose.yaml"),
                "up",
                "-d",
                "--wait",
                "postgres",
            ],
            self.project_root,
        )

    def _dbt_command(self, command: str, *extra: str) -> list[str]:
        return [
            "dbt",
            command,
            "--project-dir",
            str(self.dbt_project),
            "--profiles-dir",
            str(self.dbt_profiles),
            "--target",
            "dev",
            "--target-path",
            str(self.dbt_target),
            "--log-path",
            str(self.dbt_logs),
            "--no-use-colors",
            *extra,
        ]

    def run_dbt(self) -> None:
        self.dbt_target.mkdir(parents=True, exist_ok=True)
        self.dbt_logs.mkdir(parents=True, exist_ok=True)
        self._run(
            "加载固定 seeds",
            self._dbt_command("seed", "--full-refresh"),
            self.project_root,
        )
        self._run("执行 dbt build", self._dbt_command("build"), self.project_root)
```

这里不调用 shell，不把密码放入参数，只通过子进程环境传递本地凭据。dbt target/log 路径位于 `.dig/`，因此运行过程不会向 submodule 写入生成物。

- [ ] **Step 4: 添加 artifact 和结果状态校验**

在 `BaselineBuilder` 内实现 `validate_dbt_artifacts()`：

1. 必须存在 `.dig/dbt/target/manifest.json`。
2. 必须存在 `.dig/dbt/target/run_results.json`。
3. 必须存在 `.dig/dbt/logs/dbt.log`。
4. `run_results.json.results` 非空。
5. 每个 status 只能是 `success` 或 `pass`；任何 `error`、`fail`、`skipped`、`warn` 或未知值都抛 `BaselineError`。

为此先写临时目录单测，分别覆盖缺失 artifact 和含 `error` status 的 JSON，再实现至 GREEN。

- [ ] **Step 5: 运行单测**

```powershell
uv run pytest tests/unit/test_baseline.py -q
uv run ruff check src tests
```

Expected: command order、非零退出和 artifact 校验测试全部通过。

- [ ] **Step 6: 提交构建编排**

```powershell
git add src/data_incident_gym/baseline.py tests/unit/test_baseline.py
git commit -m "feat: orchestrate healthy dbt build"
```

---

### Task 5: 读取数据库并生成确定性基线摘要

**Files:**
- Modify: `src/data_incident_gym/baseline.py`
- Modify: `tests/unit/test_baseline.py`

固定关系与期望行数：

```python
EXPECTED_RELATION_COUNTS = {
    "customers": 100,
    "orders": 99,
    "raw_customers": 100,
    "raw_orders": 99,
    "raw_payments": 113,
    "stg_customers": 100,
    "stg_orders": 99,
    "stg_payments": 113,
}
```

- [ ] **Step 1: 先写 canonical summary 单测**

用相同 relations 的不同输入顺序构造两份摘要，断言：

1. relations 按 name 排序；columns 按 ordinal_position 排序。
2. 两份 `fingerprint` 相同。
3. 修改任一 column name、data type、nullable 或 row count 后 fingerprint 改变。
4. JSON 不包含时间戳、容器 ID、绝对路径或数据库内部 OID。

数据结构固定为：

```python
@dataclass(frozen=True)
class ColumnSummary:
    name: str
    data_type: str
    nullable: bool
    ordinal_position: int


@dataclass(frozen=True)
class RelationSummary:
    name: str
    row_count: int
    columns: tuple[ColumnSummary, ...]


@dataclass(frozen=True)
class BaselineSummary:
    schema: str
    relations: tuple[RelationSummary, ...]
    fingerprint: str
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_baseline.py -q
```

Expected: summary 类型和 builder 尚未实现。

- [ ] **Step 3: 实现纯函数 canonicalization**

实现 `make_baseline_summary(schema, relations)`：先排序，使用以下 canonical payload 计算 SHA-256：

```python
payload = {
    "schema": schema,
    "relations": [asdict(relation) for relation in ordered_relations],
}
canonical = json.dumps(
    payload,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
fingerprint = hashlib.sha256(canonical).hexdigest()
```

`BaselineSummary.to_dict()` 在同一 payload 顶层增加 `fingerprint`；`to_json()` 使用 `indent=2, sort_keys=True` 并以换行结尾。

- [ ] **Step 4: 实现 PostgreSQL inspector**

`BaselineBuilder.inspect_database()` 使用 `psycopg.connect` 的关键字参数连接。对每个固定 relation：

```sql
SELECT column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position
```

行数查询必须用 `psycopg.sql.Identifier` 构造，禁止字符串拼接 identifier：

```python
cursor.execute(
    sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(self.settings.postgres_schema),
        sql.Identifier(relation_name),
    )
)
```

任何关系缺失或行数不等于 `EXPECTED_RELATION_COUNTS` 都抛 `BaselineError`。数据库密码只作为 `connect(password=...)` 参数使用，不进入日志或摘要。

- [ ] **Step 5: 实现稳定落盘**

`BaselineBuilder.write_summary()` 固定写入：

```text
.dig/baseline-summary.json
```

文件只包含 `schema`、排序后的 relations/columns、row counts 和 fingerprint。每次成功构建覆盖该运行时文件；M2 再引入按 run ID 保存的故障产物。

- [ ] **Step 6: 组合公开闭环方法**

`BaselineBuilder.build()` 顺序必须固定：

```python
def build(self) -> BaselineSummary:
    validate_upstream_fixture(self.project_root)
    self.start_postgres()
    self.run_dbt()
    self.validate_dbt_artifacts()
    summary = self.inspect_database()
    self.write_summary(summary)
    return summary
```

- [ ] **Step 7: 运行全部单测并提交**

```powershell
uv run pytest tests/unit -q
uv run ruff check src tests
git add src/data_incident_gym/baseline.py tests/unit/test_baseline.py
git commit -m "feat: fingerprint healthy database state"
```

Expected: unit tests 全绿；尚未把 mock 测试冒充真实 PostgreSQL 验收。

---

### Task 6: 暴露唯一 M1 CLI 入口

**Files:**
- Create: `src/data_incident_gym/cli.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: 先写 CLI 行为测试**

用 `typer.testing.CliRunner` 和 monkeypatch 的 `create_baseline_builder()` 覆盖：

1. `pipeline build` 成功：exit 0；中文输出包含“健康基线构建成功”和 fingerprint。
2. builder 抛 `BaselineError`：exit 非 0；stderr 包含中文错误，不含 traceback。
3. `--help` 与 `pipeline --help` 包含中文说明。

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_cli.py -q
```

Expected: CLI 模块尚不存在或行为未实现。

- [ ] **Step 3: 实现薄 CLI**

```python
# src/data_incident_gym/cli.py
from __future__ import annotations

import typer

from data_incident_gym.baseline import BaselineBuilder, BaselineError
from data_incident_gym.config import Settings


app = typer.Typer(help="可复现的数据事故诊断实验场。")
pipeline_app = typer.Typer(help="构建并检查 dbt 数据管道。")
app.add_typer(pipeline_app, name="pipeline")


def create_baseline_builder() -> BaselineBuilder:
    return BaselineBuilder(Settings())


@pipeline_app.command("build")
def pipeline_build() -> None:
    """重置 seeds，运行 dbt build，并生成健康基线摘要。"""
    try:
        summary = create_baseline_builder().build()
    except BaselineError as exc:
        typer.echo(f"健康基线构建失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("健康基线构建成功。")
    typer.echo(f"schema: {summary.schema}")
    typer.echo(f"relations: {len(summary.relations)}")
    typer.echo(f"fingerprint: {summary.fingerprint}")
    typer.echo("summary: .dig/baseline-summary.json")
```

CLI 不新增 `doctor`、`lab`、`diagnose` 或 `eval` 的空壳命令；相应模块实现时再加入，避免伪完成。

- [ ] **Step 4: 验证 CLI 单测和帮助**

```powershell
uv run pytest tests/unit/test_cli.py -q
uv run data-incident-gym --help
uv run data-incident-gym pipeline --help
```

Expected: tests 通过；帮助包含中文；`pipeline build` 是唯一业务命令。

- [ ] **Step 5: 提交 CLI**

```powershell
git add src/data_incident_gym/cli.py tests/unit/test_cli.py
git commit -m "feat: expose baseline build command"
```

---

### Task 7: 用真实 PostgreSQL/dbt 完成 M1 集成闭环

**Files:**
- Create: `tests/integration/test_pipeline_build.py`
- Adjust only if proven necessary: `config/dbt/profiles.yml`, `src/data_incident_gym/baseline.py`

- [ ] **Step 1: 写真实集成测试**

```python
# tests/integration/test_pipeline_build.py
import json
from pathlib import Path

import pytest

from data_incident_gym.baseline import EXPECTED_RELATION_COUNTS, BaselineBuilder
from data_incident_gym.config import Settings


@pytest.mark.integration
def test_pipeline_build_creates_healthy_postgres_dbt_state(project_root: Path) -> None:
    summary = BaselineBuilder(Settings(_env_file=None), project_root).build()

    assert {item.name: item.row_count for item in summary.relations} == (
        EXPECTED_RELATION_COUNTS
    )
    assert len(summary.fingerprint) == 64

    target = project_root / ".dig" / "dbt" / "target"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    results = json.loads((target / "run_results.json").read_text(encoding="utf-8"))

    assert "model.jaffle_shop.stg_payments" in manifest["nodes"]
    assert "model.jaffle_shop.orders" in manifest["nodes"]
    assert "model.jaffle_shop.customers" in manifest["nodes"]
    assert results["results"]
    assert {result["status"] for result in results["results"]} <= {"success", "pass"}
```

补充断言 `raw_payments` 的 column names 至少为 `id, order_id, payment_method, amount`，以证明健康 Schema，而不硬编码 PostgreSQL 的内部类型名称之外的信息。

- [ ] **Step 2: 确认测试在基础设施未就绪时诚实失败**

```powershell
uv run pytest tests/integration/test_pipeline_build.py -q
```

若 Docker Desktop 未启动，Expected: 非零并明确停在 Compose 阶段；不得 skip 或改成 mock。启动 Docker Desktop 后原命令必须进入真实 dbt 构建。

- [ ] **Step 3: 只修复真实兼容问题**

允许的修复范围仅为：外置 profile、命令参数、PostgreSQL inspector、错误消息。禁止直接编辑 submodule SQL/CSV。若上游 SQL 确实无法在 PostgreSQL 执行，先记录准确 dbt error，再请求需求变更批准；不得静默复制并魔改上游模型。

- [ ] **Step 4: 验证真实构建产物**

```powershell
uv run data-incident-gym pipeline build
Get-Content -LiteralPath .dig/baseline-summary.json
Test-Path -LiteralPath .dig/dbt/target/manifest.json
Test-Path -LiteralPath .dig/dbt/target/run_results.json
Test-Path -LiteralPath .dig/dbt/logs/dbt.log
git -C third_party/jaffle_shop status --short
```

Expected:

- CLI exit 0，打印 8 relations 和 64 字符 fingerprint。
- 三个 `Test-Path` 均为 `True`。
- row counts 为 `100, 99, 100, 99, 113, 100, 99, 113`（按摘要中的关系名排序读取）。
- submodule status 无输出。

- [ ] **Step 5: 提交真实集成测试**

```powershell
git add tests/integration/test_pipeline_build.py
git commit -m "test: verify healthy PostgreSQL dbt build"
```

---

### Task 8: 连续 10 次证明环境可复现

**Files:**
- Create: `tests/e2e/test_baseline_reproducibility.py`

- [ ] **Step 1: 写十次真实重建测试**

```python
# tests/e2e/test_baseline_reproducibility.py
import pytest

from data_incident_gym.baseline import BaselineBuilder
from data_incident_gym.config import Settings


@pytest.mark.e2e
def test_fixed_seed_has_one_fingerprint_across_ten_resets(project_root) -> None:
    builder = BaselineBuilder(Settings(_env_file=None), project_root)
    summaries = [builder.build() for _ in range(10)]

    assert len({summary.fingerprint for summary in summaries}) == 1
    assert len(
        {
            tuple((relation.name, relation.row_count) for relation in summary.relations)
            for summary in summaries
        }
    ) == 1
```

这里的每次 `build()` 都执行 `seed --full-refresh` 和完整 `dbt build`，不是重复读取同一份 JSON。

- [ ] **Step 2: 运行并保留真实结果**

```powershell
uv run pytest tests/e2e/test_baseline_reproducibility.py -q -s
```

Expected: 1 test passed；实际发生 10 次重置/构建；最终只有一个 fingerprint。若任一次失败，先保存该次 stdout/stderr 和 `run_results.json`，不得用重试掩盖。

- [ ] **Step 3: 运行 M1 全套验收**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
git diff --check
git -C third_party/jaffle_shop status --short
```

Expected: 全绿；lock 未漂移；无 whitespace error；submodule 无 tracked changes。

- [ ] **Step 4: 提交复现性测试**

```powershell
git add tests/e2e/test_baseline_reproducibility.py
git commit -m "test: prove baseline reproducibility"
```

---

### Task 9: 文档化交付并加入 Ubuntu 第二平台验证

**Files:**
- Create: `README.md`
- Create: `.github/workflows/ci.yml`
- Modify: `THIRD_PARTY_NOTICES.md` only if verification found an omission

- [ ] **Step 1: 写 README，明确当前能力边界**

README 必须包含：

1. 一句中文摘要和一句英文摘要。
2. 当前状态明确写“仅 M1 健康基线已实现；故障注入和 Agent 尚未实现”。
3. 前置条件：Windows 11、PowerShell 7、Git、Python 3.12.10、uv 0.11.24、Docker Desktop。
4. 初始化：

```powershell
git submodule update --init --recursive
uv sync --frozen
```

5. 唯一运行命令：

```powershell
uv run data-incident-gym pipeline build
```

6. 产物位置、期望 8 个关系、已知固定行数、测试分层命令。
7. 离线边界：首次下载 Python 包、Docker image、submodule 后，M1 运行不需要外网；dbt telemetry 被关闭。
8. 第三方许可证链接和 `THIRD_PARTY_NOTICES.md`。

- [ ] **Step 2: 加入 CI**

`.github/workflows/ci.yml`：

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  m1:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12.10"
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.11.24"
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run pytest tests/unit -q
      - run: uv run pytest tests/integration -q
      - run: uv run pytest tests/e2e -q
```

CI 不安装或调用 Ollama；M4 才增加模型验证 job。

- [ ] **Step 3: 从 README 冷启动路径复跑**

在当前 Windows 工作区严格按 README 顺序执行，不使用未记录的环境变量或手工 SQL：

```powershell
git submodule update --init --recursive
uv sync --frozen
uv run data-incident-gym pipeline build
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
```

Expected: 文档命令本身足以完成 M1，不依赖 Bash、Makefile 或手工进入容器。

- [ ] **Step 4: 最终范围和许可证检查**

```powershell
rg -n "Airflow|OpenLineage|Marquez|PydanticAI|Ollama|fault inject|diagnose" src tests
git diff --check
git status --short
git log --oneline --decorate -10
```

Expected: 第一条无实现命中（测试/注释也不应提前承诺后续模块）；其余检查干净且提交历史按任务递进。

- [ ] **Step 5: 提交 M1 交付文档与 CI**

```powershell
git add README.md .github/workflows/ci.yml THIRD_PARTY_NOTICES.md
git commit -m "docs: document M1 baseline workflow"
```

---

## M1 最终完成门槛

只有同时满足以下条件，才允许把 M1 标记完成并开始 M2：

- [ ] `uv run data-incident-gym pipeline build` 在 Windows 11 / PowerShell 7 exit 0。
- [ ] PostgreSQL image 是计划中的固定 tag + digest。
- [ ] submodule HEAD 精确等于 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 且无修改。
- [ ] `raw_customers/raw_orders/raw_payments` 与五个下游关系存在。
- [ ] dbt `run_results.json` 非空且只含 `success/pass`。
- [ ] `manifest.json`、`run_results.json`、`dbt.log` 和 `baseline-summary.json` 存在。
- [ ] 10 次真实 `seed --full-refresh → dbt build` 得到同一 schema/row-count fingerprint。
- [ ] unit、integration、e2e、Ruff、`uv lock --check`、`git diff --check` 全部通过。
- [ ] Ubuntu CI 通过；若尚未推送 GitHub，只能报告“本地 CI 等价命令已通过”，不能报告 CI 已通过。
- [ ] README 没有把未实现的 M2–M5 写成已完成功能。

## 实施时的停止规则

遇到以下任一情况立即停止当前 task、保留完整错误并请求决策，不扩大修改范围：

1. 固定 submodule commit 或许可证与需求文档不一致。
2. 只有修改 submodule 源码才能通过 PostgreSQL。
3. Docker digest 在目标平台无法解析，且需要更换版本/digest。
4. 真实 dbt 结果与固定关系/行数不一致。
5. 需要删除 Docker volume 或用户数据才能继续。
6. 任一步会提前引入 M2–M5 范围。
