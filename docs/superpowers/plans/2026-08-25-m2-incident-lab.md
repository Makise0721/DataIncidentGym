# M2 Incident Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不回归 M1 健康构建的前提下，为 `schema_rename_payment_amount` 建立可重置、可注入、可稳定失败、可独立验证并保留逐次运行产物的 M2 Incident Lab 闭环。

**Architecture:** 保留 `BaselineBuilder.build()` 与 `pipeline build` 的健康语义；新增深模块 `IncidentLab`，只暴露 `reset(case_id)`、`inject(case_id)`、`build(case_id)` 三个入口。共享的 `DbtRunner` 隐藏健康构建与故障构建的命令差异，`IncidentVerifier` 只从固定 Ground Truth 和某个 run 的落盘产物验证结果，避免注入器自行证明自己正确。

**Tech Stack:** Python 3.12.10, uv 0.11.24, Typer 0.27.1, Pydantic 2.13.4, psycopg 3.3.4, dbt-core 1.12.3, dbt-postgres 1.11.0, PostgreSQL 17.6 Alpine, pytest 9.1.1, Ruff 0.16.4, Docker Compose

---

## 批准状态与执行边界

- 用户于 2026-08-25 明确确认：M1 的 Ubuntu CI 已通过，这是人工观测事实；M1 完成门槛视为满足。
- 用户于 2026-08-25 同意使用实际仓库路径 `C:\Users\29913\codex_space\DataIncidentGym`，并要求 M2 计划最多 6 个 Task。
- 用户已确认本计划采用以下接口决策：保留 `pipeline build` 的 M1 健康构建语义；新增 `lab build` 专门执行不含 seed 的故障构建；预期故障通过验证时 `lab build` exit 0。
- 本文档获准编写不等于获准执行。只有用户另行批准本计划后，才允许开始 Task 1。
- 当前工作区已有用户自己的根目录 Markdown 修改：`AGENT.md` 和 `mistake.md`。用户明确要求根目录 Markdown 暂不提交、暂不推送；`AGENT.md` 不得编辑，`mistake.md` 仍需作为工作区决策记录更新，但只能保持未暂存、未提交。所有提交必须使用显式路径，禁止 `git add .`，不得包含或撤销这些修改。

## M2 范围与验收映射

| 已批准要求 | M2 实现证据 |
|---|---|
| 恢复健康基线 | `lab reset CASE` 只接受缺失、健康或本案例已注入三种已知状态；故障态先反向改名，再复用 M1 `BaselineBuilder.build()` |
| 注入字段改名 | `lab inject CASE` 在事务中执行固定 allowlist 的 `amount → total_amount`，并检查前置与后置 Schema |
| 保存失败产物 | `lab build CASE` 使用 run-specific target/log 目录执行 `dbt build --exclude-resource-type seed` |
| 机器可读 Ground Truth | `config/incidents/schema_rename_payment_amount.json` + Pydantic 严格模型 + canonical digest |
| 独立验证 | `IncidentVerifier` 读取 Ground Truth 快照、`run_results.json`、`manifest.json` 和 Schema 快照，不调用注入代码 |
| 10 次确定性 | 真实 PostgreSQL/dbt E2E 比较失败节点、错误类别、影响模型、Schema fingerprint 和 Ground Truth digest |
| 可恢复 | 10 次循环后再次 `lab reset` 与 `pipeline build`，健康 fingerprint 回到 M1 基线 |
| 不修改第三方源码 | 所有实现位于主仓库；最终检查 submodule commit 与 clean status |

M2 明确不做：`lab replay`、自由 SQL/表名/列名输入、删除 Docker volume、M3 `EvidenceRecord` 与四个只读工具、只读数据库角色、M4 PydanticAI/Ollama/Diagnosis、M5 Agent 评测与报告、`trace.jsonl`、Web UI、RAG、多 Agent 产品能力、自动修复或生产连接。

## 用户可见命令与语义

```powershell
uv run data-incident-gym lab reset schema_rename_payment_amount
uv run data-incident-gym lab inject schema_rename_payment_amount
uv run data-incident-gym lab build schema_rename_payment_amount
```

- `pipeline build` 永远保持 M1 语义：`seed --full-refresh → dbt build → 健康校验`。
- `lab build` 永远保持 M2 语义：检查已注入状态后，只运行 `dbt build --exclude-resource-type seed`。
- dbt 子进程的非零退出码会写入 `metadata.json`。若独立验证得到 `EXPECTED_FAILURE`，实验室命令整体 exit 0；dbt 意外成功、失败节点错误、Schema 不符或 artifact 无效时 exit 非零。
- 不使用隐藏的 `.dig` 状态标记决定行为。每个命令都从真实 PostgreSQL Schema 判断当前状态，避免状态文件与数据库漂移。
- Schema 状态只在固定 Ground Truth 的关系名、列名及顺序、`data_type`、`nullable`、`ordinal_position` 和 `row_count` 全部精确匹配时判定为 `HEALTHY` 或 `INJECTED`；任一漂移均为 `DRIFTED` 并 fail closed。

## 固定产物结构

```text
config/incidents/
└── schema_rename_payment_amount.json

.dig/lab/runs/<32-char-uuid-hex>/
├── metadata.json
├── ground_truth.json
├── schema.json
├── verification.json
└── dbt/
    ├── stdout.log
    ├── stderr.log
    ├── target/
    │   ├── manifest.json
    │   └── run_results.json
    └── logs/
        └── dbt.log
```

`.dig/` 已被 `.gitignore` 排除。旧 run 不删除、不覆盖；动态的 run ID、时间和绝对路径不进入确定性比较。

## 最终文件职责

| 文件 | 单一职责 |
|---|---|
| `config/incidents/schema_rename_payment_amount.json` | 首个案例的固定 Ground Truth，不包含可执行 SQL |
| `src/data_incident_gym/incidents.py` | Ground Truth 类型、严格加载、canonical JSON 与 digest |
| `src/data_incident_gym/dbt_runner.py` | 健康和故障两种 dbt invocation；命令数组、超时和脱敏集中在此 |
| `src/data_incident_gym/baseline.py` | 继续提供 M1 健康基线 facade，改为委托 `DbtRunner.run_healthy()` |
| `src/data_incident_gym/lab.py` | Incident Lab 的 reset/inject/build、真实 Schema 状态机和 run 目录写入 |
| `src/data_incident_gym/lab_verifier.py` | 从落盘事实独立验证直接失败、血缘影响、Schema 与 Ground Truth digest |
| `src/data_incident_gym/cli.py` | 中文 CLI 适配，不包含 SQL、dbt 选择或验证逻辑 |
| `tests/unit/test_incidents.py` | Ground Truth 合同与拒绝路径 |
| `tests/unit/test_dbt_runner.py` | 健康/故障命令、非零结果与密码脱敏 |
| `tests/unit/test_lab.py` | 状态转换、事务化改名、run 隔离与错误模式 |
| `tests/unit/test_lab_verifier.py` | 独立 artifact/lineage/schema 验证 |
| `tests/integration/test_incident_lab.py` | 一次真实 reset/inject/build/recover |
| `tests/e2e/test_incident_reproducibility.py` | 十次真实故障复现与最终健康恢复 |

## 每个 Task 的交付协议

1. 主代理只负责编排；实现和修复交给 `luna_worker`。
2. 每个 Task 严格执行 RED → 最小 GREEN → 全量相关测试 → Ruff → 显式路径提交。
3. 提交后派遣一个新的、未参与该 Task 实现的 `luna_worker` 做对抗性审查，覆盖需求、计划、变更范围、测试、提交、第三方完整性和安全边界。
4. 审查不通过时，只修复已证实问题并重新审查；通过前不得进入下一 Task。
5. 每个 Task 交付记录实际命令、exit code、测试结果、审查结论和 commit hash。

---

### Task 1: 固定 M2 CLI 决策与 Ground Truth 合同

**Files:**
- Modify: `docs/requirements.md:158-172,281-306`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Create: `config/incidents/schema_rename_payment_amount.json`
- Create: `src/data_incident_gym/incidents.py`
- Create: `tests/unit/test_incidents.py`
- Track: `docs/superpowers/plans/2026-08-25-m2-incident-lab.md`

- [x] **Step 1: 先写 Ground Truth 合同失败测试**

创建 `tests/unit/test_incidents.py`：

```python
import json
from pathlib import Path

import pytest

from data_incident_gym.incidents import (
    CASE_ID,
    IncidentCaseError,
    load_ground_truth,
)


def test_committed_ground_truth_is_strict_and_canonical(project_root: Path) -> None:
    truth = load_ground_truth(CASE_ID, project_root)

    assert truth.incident_case_id == CASE_ID
    assert truth.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
    assert truth.injection.relation == "raw_payments"
    assert truth.injection.from_column == "amount"
    assert truth.injection.to_column == "total_amount"
    assert truth.direct_failure == "model.jaffle_shop.stg_payments"
    assert truth.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert truth.required_evidence_types == (
        "DBT_NODE_ERROR",
        "RELATION_SCHEMA",
        "DBT_LINEAGE",
    )
    assert truth.expected_failure_category == "DBT_MODEL_ERROR"
    assert len(truth.digest()) == 64
    assert truth.to_json().endswith("\n")


def test_unknown_case_is_rejected_before_path_construction(tmp_path: Path) -> None:
    with pytest.raises(IncidentCaseError, match="未知故障案例"):
        load_ground_truth("../../outside", tmp_path)


def test_ground_truth_rejects_contract_drift(
    tmp_path: Path,
    project_root: Path,
) -> None:
    source = project_root / "config/incidents/schema_rename_payment_amount.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["root_cause_code"] = "WRONG_ROOT_CAUSE"
    target = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IncidentCaseError, match="Ground Truth 无效"):
        load_ground_truth(CASE_ID, tmp_path)
```

- [x] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_incidents.py -q
```

Expected: exit 非零，collection 因 `data_incident_gym.incidents` 不存在而失败；不得通过临时 skip 绕过。

- [x] **Step 3: 创建固定 Ground Truth**

创建 `config/incidents/schema_rename_payment_amount.json`，内容精确为：

```json
{
  "affected_assets": [
    "model.jaffle_shop.stg_payments",
    "model.jaffle_shop.orders",
    "model.jaffle_shop.customers"
  ],
  "direct_failure": "model.jaffle_shop.stg_payments",
  "expected_failure_category": "DBT_MODEL_ERROR",
  "expected_schema": {
    "fault_columns": [
      "id",
      "order_id",
      "payment_method",
      "total_amount"
    ],
    "healthy_columns": [
      "id",
      "order_id",
      "payment_method",
      "amount"
    ],
    "relation": "raw_payments",
    "row_count": 113
  },
  "incident_case_id": "schema_rename_payment_amount",
  "injection": {
    "from_column": "amount",
    "relation": "raw_payments",
    "to_column": "total_amount"
  },
  "required_evidence_types": [
    "DBT_NODE_ERROR",
    "RELATION_SCHEMA",
    "DBT_LINEAGE"
  ],
  "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
  "schema_version": "ground_truth.v1"
}
```

其中 `expected_schema` 还必须包含 `healthy_column_metadata` 与 `fault_column_metadata`；每项固定记录 `name`、`data_type`、`nullable`、`ordinal_position`，与 `.dig/baseline-summary.json` 的真实 M1 `raw_payments` 摘要一致。

该 JSON 只描述事实，不包含 SQL 字符串、脚本路径或可供用户替换的任意 identifier。

- [x] **Step 4: 实现严格类型、加载与 digest**

创建 `src/data_incident_gym/incidents.py`：

```python
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from data_incident_gym.config import PROJECT_ROOT

CASE_ID = "schema_rename_payment_amount"


class IncidentCaseError(RuntimeError):
    """Raised when a fixed incident case cannot be loaded safely."""


class InjectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    from_column: Literal["amount"]
    to_column: Literal["total_amount"]


class ExpectedColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    data_type: StrictStr
    nullable: StrictBool
    ordinal_position: StrictInt


class ExpectedSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    healthy_columns: tuple[str, ...]
    fault_columns: tuple[str, ...]
    healthy_column_metadata: tuple[ExpectedColumn, ...]
    fault_column_metadata: tuple[ExpectedColumn, ...]
    row_count: Literal[113]

    @field_validator("row_count", mode="before")
    @classmethod
    def validate_row_count_type(cls, value: object) -> object:
        if type(value) is not int or value != 113:
            raise ValueError("row_count 必须是原生 int 且为 113")
        return value


class GroundTruth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ground_truth.v1"]
    incident_case_id: Literal["schema_rename_payment_amount"]
    root_cause_code: Literal["SOURCE_SCHEMA_COLUMN_RENAMED"]
    injection: InjectionSpec
    direct_failure: Literal["model.jaffle_shop.stg_payments"]
    affected_assets: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    expected_failure_category: Literal["DBT_MODEL_ERROR"]
    expected_schema: ExpectedSchema

    @model_validator(mode="after")
    def validate_fixed_contract(self) -> Self:
        if self.affected_assets != (
            "model.jaffle_shop.stg_payments",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.customers",
        ):
            raise ValueError("affected_assets 不匹配固定案例")
        if self.required_evidence_types != (
            "DBT_NODE_ERROR",
            "RELATION_SCHEMA",
            "DBT_LINEAGE",
        ):
            raise ValueError("required_evidence_types 不匹配固定案例")
        if self.expected_schema.healthy_columns != (
            "id",
            "order_id",
            "payment_method",
            "amount",
        ):
            raise ValueError("healthy_columns 不匹配固定案例")
        if self.expected_schema.fault_columns != (
            "id",
            "order_id",
            "payment_method",
            "total_amount",
        ):
            raise ValueError("fault_columns 不匹配固定案例")
        if self.expected_schema.healthy_column_metadata != (
            ExpectedColumn(name="id", data_type="integer", nullable=True, ordinal_position=1),
            ExpectedColumn(name="order_id", data_type="integer", nullable=True, ordinal_position=2),
            ExpectedColumn(name="payment_method", data_type="text", nullable=True, ordinal_position=3),
            ExpectedColumn(name="amount", data_type="integer", nullable=True, ordinal_position=4),
        ):
            raise ValueError("healthy_column_metadata 不匹配固定案例")
        if self.expected_schema.fault_column_metadata != (
            ExpectedColumn(name="id", data_type="integer", nullable=True, ordinal_position=1),
            ExpectedColumn(name="order_id", data_type="integer", nullable=True, ordinal_position=2),
            ExpectedColumn(name="payment_method", data_type="text", nullable=True, ordinal_position=3),
            ExpectedColumn(name="total_amount", data_type="integer", nullable=True, ordinal_position=4),
        ):
            raise ValueError("fault_column_metadata 不匹配固定案例")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        ) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def parse_ground_truth(text: str, source: str) -> GroundTruth:
    try:
        return GroundTruth.model_validate_json(text)
    except ValidationError as exc:
        raise IncidentCaseError(f"Ground Truth 无效：{source}") from exc


def load_ground_truth(
    case_id: str,
    project_root: Path = PROJECT_ROOT,
) -> GroundTruth:
    if case_id != CASE_ID:
        raise IncidentCaseError(f"未知故障案例：{case_id}")
    path = project_root / "config" / "incidents" / f"{CASE_ID}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncidentCaseError(f"无法读取 Ground Truth：{path}") from exc
    return parse_ground_truth(text, str(path))
```

- [x] **Step 5: 更新批准文档和决策记录**

在 `docs/requirements.md` 的 M2 完成定义中，把“错误类别”具体化为：

```markdown
- M2 的稳定错误类别固定为 `DBT_MODEL_ERROR`，由 dbt `run_results.json` 中直接模型节点的 `status=error` 推导；不得依赖完整自然语言错误文本。
```

在 CLI 命令列表的 `lab inject` 后加入：

```powershell
uv run data-incident-gym lab build schema_rename_payment_amount
```

并在 CLI 要求中加入：

```markdown
- `pipeline build` 保持健康基线语义，始终执行 `seed --full-refresh` 后再执行健康 `dbt build`。
- `lab build` 只在已注入状态执行不含 seed 的故障构建。底层 dbt 非零且独立验证符合 Ground Truth 时，实验命令成功并返回 `EXPECTED_FAILURE`；非预期结果返回非零退出码。
```

在 `mistake.md` 末尾追加工作区决策记录。`mistake.md` 仍需更新作为工作区决策记录，但因用户明确要求根目录 Markdown 暂不提交、暂不推送，不得加入任何提交：

```markdown
## 2026-08-25：M1 完成事实与 M2 接口决策

- 用户确认 Ubuntu CI 已通过，这是人工观测事实；M1 完成门槛视为满足。
- M2 保留 `pipeline build` 的健康语义，新增 `lab build` 执行无 seed 的故障构建。
- 预期 dbt 失败通过 Ground Truth 独立验证时，`lab build` exit 0；非预期结果 exit 非零。
- M2 计划限制为最多 6 个 Task，计划路径使用实际仓库的 `docs/superpowers/plans/`。
```

- [x] **Step 6: 验证 GREEN 并提交**

```powershell
uv run pytest tests/unit/test_incidents.py -q
uv run ruff check src/data_incident_gym/incidents.py tests/unit/test_incidents.py
git diff --check -- docs/requirements.md config/incidents/schema_rename_payment_amount.json src/data_incident_gym/incidents.py tests/unit/test_incidents.py docs/superpowers/plans/2026-08-25-m2-incident-lab.md
git add docs/requirements.md config/incidents/schema_rename_payment_amount.json src/data_incident_gym/incidents.py tests/unit/test_incidents.py docs/superpowers/plans/2026-08-25-m2-incident-lab.md
git commit -m "docs: define M2 incident contract"
```

Expected: focused pytest、Ruff 和 diff check exit 0；提交只包含上述非根目录文件，不包含 `AGENT.md` 或 `mistake.md`；随后独立 `luna_worker` 审查通过。

---

### Task 2: 提取健康与故障 dbt 执行 seam

**Files:**
- Create: `src/data_incident_gym/dbt_runner.py`
- Create: `tests/unit/test_dbt_runner.py`
- Modify: `src/data_incident_gym/baseline.py:111-227`
- Verify: `tests/unit/test_baseline.py`

- [ ] **Step 1: 写健康/故障命令的失败测试**

创建 `tests/unit/test_dbt_runner.py`：

```python
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner


def test_healthy_run_seeds_then_builds_while_incident_run_never_seeds(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="ok", stderr="")

    runner = DbtRunner(Settings(_env_file=None), tmp_path, fake_run)

    runner.run_healthy(tmp_path / "healthy/target", tmp_path / "healthy/logs")
    incident = runner.run_incident(
        tmp_path / "incident/target",
        tmp_path / "incident/logs",
    )

    assert [command[:2] for command in calls] == [
        ["dbt", "seed"],
        ["dbt", "build"],
        ["dbt", "build"],
    ]
    assert "--full-refresh" in calls[0]
    assert "--full-refresh" not in calls[2]
    assert calls[2][1] == "build"
    exclude_index = calls[2].index("--exclude-resource-type")
    assert calls[2][exclude_index + 1] == "seed"
    assert incident.return_code == 0


def test_incident_run_returns_redacted_nonzero_result(tmp_path: Path) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            1,
            stdout="stdout database-secret",
            stderr="stderr database-secret",
        )

    settings = Settings(_env_file=None, postgres_password="database-secret")
    result = DbtRunner(settings, tmp_path, fake_run).run_incident(
        tmp_path / "target",
        tmp_path / "logs",
    )

    assert result.return_code == 1
    assert "database-secret" not in result.stdout
    assert "database-secret" not in result.stderr
    assert "***" in result.stdout
    assert "***" in result.stderr


def test_healthy_run_rejects_nonzero_and_redacts_password(tmp_path: Path) -> None:
    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 17, stdout="database-secret", stderr="failed")

    settings = Settings(_env_file=None, postgres_password="database-secret")
    runner = DbtRunner(settings, tmp_path, fake_run)

    with pytest.raises(DbtExecutionError) as error:
        runner.run_healthy(tmp_path / "target", tmp_path / "logs")

    assert "加载固定 seeds" in str(error.value)
    assert "exit=17" in str(error.value)
    assert "database-secret" not in str(error.value)
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_dbt_runner.py -q
```

Expected: exit 非零，collection 因 `data_incident_gym.dbt_runner` 不存在而失败。

- [ ] **Step 3: 实现语义明确的 `DbtRunner`**

创建 `src/data_incident_gym/dbt_runner.py`：

```python
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess

from data_incident_gym.config import PROJECT_ROOT, Settings

RunCommand = Callable[..., CompletedProcess[str]]


@dataclass(frozen=True)
class DbtRunResult:
    return_code: int
    stdout: str
    stderr: str


class DbtExecutionError(RuntimeError):
    """Raised when dbt cannot execute or a healthy dbt stage fails."""


class DbtRunner:
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

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    def _command(
        self,
        command: str,
        target_path: Path,
        log_path: Path,
        *extra: str,
    ) -> list[str]:
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
            str(target_path),
            "--log-path",
            str(log_path),
            "--no-use-colors",
            *extra,
        ]

    def _invoke(self, stage: str, command: Sequence[str]) -> DbtRunResult:
        try:
            result = self.run_command(
                list(command),
                cwd=self.project_root,
                env=self.settings.subprocess_environment(),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DbtExecutionError(
                f"{stage} 无法执行：{self._redact(str(exc))}"
            ) from exc
        return DbtRunResult(
            return_code=result.returncode,
            stdout=self._redact(result.stdout or ""),
            stderr=self._redact(result.stderr or ""),
        )

    @staticmethod
    def _ensure_success(stage: str, result: DbtRunResult) -> None:
        if result.return_code != 0:
            raise DbtExecutionError(
                f"{stage} 失败（exit={result.return_code}）\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

    def _prepare(self, target_path: Path, log_path: Path) -> None:
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            log_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DbtExecutionError(
                f"无法创建 dbt 产物目录：{self._redact(str(exc))}"
            ) from exc

    def run_healthy(self, target_path: Path, log_path: Path) -> None:
        self._prepare(target_path, log_path)
        seed = self._invoke(
            "加载固定 seeds",
            self._command("seed", target_path, log_path, "--full-refresh"),
        )
        self._ensure_success("加载固定 seeds", seed)
        build = self._invoke(
            "执行 dbt build",
            self._command("build", target_path, log_path),
        )
        self._ensure_success("执行 dbt build", build)

    def run_incident(self, target_path: Path, log_path: Path) -> DbtRunResult:
        self._prepare(target_path, log_path)
        return self._invoke(
            "执行故障 dbt build",
            self._command(
                "build",
                target_path,
                log_path,
                "--exclude-resource-type",
                "seed",
            ),
        )
```

不要新增 `skip_seed: bool` 或 `allow_failure: bool`；健康和故障语义通过两个命名入口隔离。

- [ ] **Step 4: 让 M1 facade 委托新 seam，不改变外部行为**

在 `src/data_incident_gym/baseline.py` 导入：

```python
from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner
```

在 `BaselineBuilder.__init__()` 中保留现有参数，并增加：

```python
self.dbt_runner = DbtRunner(settings, project_root, run_command)
```

删除 `BaselineBuilder._dbt_command()`，将 `run_dbt()` 完整替换为：

```python
def run_dbt(self) -> None:
    try:
        self.dbt_runner.run_healthy(self.dbt_target, self.dbt_logs)
    except DbtExecutionError as exc:
        raise BaselineError(str(exc)) from None
```

`start_postgres()` 继续使用现有 `_run()`；`BaselineBuilder.build()` 的调用顺序、返回类型、摘要路径和 CLI 均不改变。

- [ ] **Step 5: 验证新 seam 与全部 M1 单测**

```powershell
uv run pytest tests/unit/test_dbt_runner.py -q
uv run pytest tests/unit/test_baseline.py tests/unit/test_cli.py -q
uv run pytest tests/unit -q
uv run ruff check src tests
```

Expected: 全部命令 exit 0；现有 M1 命令顺序测试仍证明 compose → seed full-refresh → build。

- [ ] **Step 6: 提交并独立审查**

```powershell
git diff --check -- src/data_incident_gym/dbt_runner.py src/data_incident_gym/baseline.py tests/unit/test_dbt_runner.py
git add src/data_incident_gym/dbt_runner.py src/data_incident_gym/baseline.py tests/unit/test_dbt_runner.py
git commit -m "refactor: separate healthy and incident dbt runs"
```

Expected: 提交只包含 dbt seam 与 M1 委托变更；独立 `luna_worker` 确认 `pipeline build` 行为未回归后才进入 Task 3。

---

### Task 3: 实现真实 Schema 状态机与 reset/inject

**Files:**
- Create: `src/data_incident_gym/lab.py`
- Create: `tests/unit/test_lab.py`

- [ ] **Step 1: 写状态转换失败测试**

创建 `tests/unit/test_lab.py`，先覆盖 reset、inject 和拒绝路径：

```python
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg import sql

from data_incident_gym.baseline import (
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import Settings
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab import (
    IncidentExecutionError,
    IncidentLab,
    InvalidIncidentState,
)


def _relation(*names: str) -> RelationSummary:
    return RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=tuple(
            ColumnSummary(name, "integer" if name != "payment_method" else "text", True, index)
            for index, name in enumerate(names, start=1)
        ),
    )


HEALTHY = _relation("id", "order_id", "payment_method", "amount")
INJECTED = _relation("id", "order_id", "payment_method", "total_amount")
DRIFTED = _relation("id", "order_id", "payment_method", "other_amount")


class FakeBaseline:
    def __init__(self, summary: BaselineSummary) -> None:
        self.summary = summary
        self.calls: list[str] = []

    def start_postgres(self) -> None:
        self.calls.append("start_postgres")

    def build(self) -> BaselineSummary:
        self.calls.append("build")
        return self.summary


def _prepare_ground_truth(tmp_path: Path) -> None:
    truth = load_ground_truth(CASE_ID)
    path = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    path.parent.mkdir(parents=True)
    path.write_text(truth.to_json(), encoding="utf-8")


def _lab(tmp_path: Path) -> tuple[IncidentLab, FakeBaseline]:
    _prepare_ground_truth(tmp_path)
    summary = make_baseline_summary("analytics", (HEALTHY,))
    baseline = FakeBaseline(summary)
    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=baseline,
    )
    return lab, baseline


def test_reset_reverses_known_fault_then_builds_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, baseline = _lab(tmp_path)
    renames: list[tuple[str, str, str]] = []
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: INJECTED)
    monkeypatch.setattr(
        lab,
        "_rename_column",
        lambda relation, source, target: renames.append((relation, source, target)),
    )

    result = lab.reset(CASE_ID)

    assert baseline.calls == ["start_postgres", "build"]
    assert renames == [("raw_payments", "total_amount", "amount")]
    assert result.state == "HEALTHY"
    assert len(result.fingerprint) == 64


def test_inject_requires_healthy_state_and_verifies_postcondition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, baseline = _lab(tmp_path)
    states = iter((HEALTHY, INJECTED))
    renames: list[tuple[str, str, str]] = []
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: next(states))
    monkeypatch.setattr(
        lab,
        "_rename_column",
        lambda relation, source, target: renames.append((relation, source, target)),
    )

    result = lab.inject(CASE_ID)

    assert baseline.calls == ["start_postgres"]
    assert renames == [("raw_payments", "amount", "total_amount")]
    assert result.state == "INJECTED"
    assert len(result.fingerprint) == 64


@pytest.mark.parametrize("state", [INJECTED, DRIFTED, None])
def test_inject_rejects_nonhealthy_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: RelationSummary | None,
) -> None:
    lab, _ = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: state)

    with pytest.raises(InvalidIncidentState):
        lab.inject(CASE_ID)


def test_rename_uses_fixed_quoted_identifiers(tmp_path: Path) -> None:
    executed: list[object] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, query: object) -> None:
            executed.append(query)

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

        def transaction(self) -> Transaction:
            return Transaction()

    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=SimpleNamespace(),
        db_connect=lambda **_: Connection(),
    )

    lab._rename_column("raw_payments", "amount", "total_amount")

    assert len(executed) == 1
    assert isinstance(executed[0], sql.Composed)
    assert executed[0].as_string(None) == (
        'ALTER TABLE "analytics"."raw_payments" '
        'RENAME COLUMN "amount" TO "total_amount"'
    )
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_lab.py -q
```

Expected: exit 非零，collection 因 `data_incident_gym.lab` 不存在而失败。

- [ ] **Step 3: 实现 `IncidentLab` 的状态读取与受控改名**

创建 `src/data_incident_gym/lab.py` 的初始版本：

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql

from data_incident_gym.baseline import (
    CATALOG_COLUMNS_QUERY,
    BaselineBuilder,
    BaselineError,
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.incidents import GroundTruth, load_ground_truth

DatabaseConnect = Callable[..., Any]
CaseState = Literal["MISSING", "HEALTHY", "INJECTED", "DRIFTED"]


class LabError(RuntimeError):
    code = "LAB_ERROR"


class InvalidIncidentState(LabError):
    code = "INVALID_INCIDENT_STATE"


class IncidentExecutionError(LabError):
    code = "INCIDENT_EXECUTION_ERROR"


@dataclass(frozen=True)
class ResetResult:
    case_id: str
    state: Literal["HEALTHY"]
    fingerprint: str


@dataclass(frozen=True)
class InjectionResult:
    case_id: str
    state: Literal["INJECTED"]
    fingerprint: str


class IncidentLab:
    def __init__(
        self,
        settings: Settings,
        project_root: Path = PROJECT_ROOT,
        *,
        baseline_builder: BaselineBuilder | None = None,
        db_connect: DatabaseConnect | None = None,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.db_connect = db_connect or psycopg.connect
        self.baseline_builder = baseline_builder or BaselineBuilder(
            settings,
            project_root,
            db_connect=self.db_connect,
        )

    def _connection_kwargs(self) -> dict[str, object]:
        return {
            "host": self.settings.postgres_host,
            "port": self.settings.postgres_port,
            "dbname": self.settings.postgres_database,
            "user": self.settings.postgres_user,
            "password": self.settings.postgres_password.get_secret_value(),
        }

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    def _inspect_relation(self, truth: GroundTruth) -> RelationSummary | None:
        relation_name = truth.expected_schema.relation
        try:
            with self.db_connect(**self._connection_kwargs()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        CATALOG_COLUMNS_QUERY,
                        (self.settings.postgres_schema, relation_name),
                    )
                    rows = cursor.fetchall()
                    if not rows:
                        return None
                    columns = tuple(
                        ColumnSummary(
                            name=row[0],
                            data_type=row[1],
                            nullable=row[2] == "YES",
                            ordinal_position=row[3],
                        )
                        for row in rows
                    )
                    cursor.execute(
                        sql.SQL("SELECT count(*) FROM {}.{}").format(
                            sql.Identifier(self.settings.postgres_schema),
                            sql.Identifier(relation_name),
                        )
                    )
                    count_row = cursor.fetchone()
                    if count_row is None:
                        raise IncidentExecutionError(
                            f"无法读取行数：{relation_name}"
                        )
                    return RelationSummary(relation_name, count_row[0], columns)
        except LabError:
            raise
        except Exception as exc:
            raise IncidentExecutionError(
                f"读取故障 Schema 失败：{self._redact(str(exc))}"
            ) from None

    @staticmethod
    def _classify_state(
        relation: RelationSummary | None,
        truth: GroundTruth,
    ) -> CaseState:
        if relation is None:
            return "MISSING"
        columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in relation.columns
        )
        healthy_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in truth.expected_schema.healthy_column_metadata
        )
        fault_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in truth.expected_schema.fault_column_metadata
        )
        if (
            columns == healthy_columns
            and relation.row_count == truth.expected_schema.row_count
        ):
            return "HEALTHY"
        if (
            columns == fault_columns
            and relation.row_count == truth.expected_schema.row_count
        ):
            return "INJECTED"
        return "DRIFTED"

    def _rename_column(self, relation: str, source: str, target: str) -> None:
        statement = sql.SQL(
            "ALTER TABLE {}.{} RENAME COLUMN {} TO {}"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(relation),
            sql.Identifier(source),
            sql.Identifier(target),
        )
        try:
            with self.db_connect(**self._connection_kwargs()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(statement)
        except Exception as exc:
            raise IncidentExecutionError(
                f"故障字段改名失败：{self._redact(str(exc))}"
            ) from None

    def _fingerprint(self, relation: RelationSummary) -> str:
        return make_baseline_summary(
            self.settings.postgres_schema,
            (relation,),
        ).fingerprint

    def _start_postgres(self) -> None:
        try:
            self.baseline_builder.start_postgres()
        except BaselineError as exc:
            raise IncidentExecutionError(str(exc)) from None

    def _build_healthy_baseline(self) -> BaselineSummary:
        try:
            return self.baseline_builder.build()
        except BaselineError as exc:
            raise IncidentExecutionError(str(exc)) from None

    def reset(self, case_id: str) -> ResetResult:
        truth = load_ground_truth(case_id, self.project_root)
        self._start_postgres()
        current = self._inspect_relation(truth)
        state = self._classify_state(current, truth)
        if state == "INJECTED":
            self._rename_column(
                truth.injection.relation,
                truth.injection.to_column,
                truth.injection.from_column,
            )
        elif state not in {"MISSING", "HEALTHY"}:
            raise InvalidIncidentState(
                f"无法从未知 Schema 状态重置案例：{case_id}"
            )

        summary = self._build_healthy_baseline()
        relation = next(
            (
                item
                for item in summary.relations
                if item.name == truth.expected_schema.relation
            ),
            None,
        )
        if self._classify_state(relation, truth) != "HEALTHY":
            raise InvalidIncidentState("重置后未恢复健康 Schema")
        return ResetResult(case_id, "HEALTHY", summary.fingerprint)

    def inject(self, case_id: str) -> InjectionResult:
        truth = load_ground_truth(case_id, self.project_root)
        self._start_postgres()
        before = self._inspect_relation(truth)
        state = self._classify_state(before, truth)
        if state != "HEALTHY":
            raise InvalidIncidentState(
                f"故障注入要求健康状态，当前状态：{state}"
            )
        self._rename_column(
            truth.injection.relation,
            truth.injection.from_column,
            truth.injection.to_column,
        )
        after = self._inspect_relation(truth)
        if self._classify_state(after, truth) != "INJECTED" or after is None:
            raise InvalidIncidentState("故障注入后 Schema 不符合预期")
        return InjectionResult(case_id, "INJECTED", self._fingerprint(after))
```

- [ ] **Step 4: 补齐密码脱敏与重复注入断言**

在 `tests/unit/test_lab.py` 追加：

```python
def test_schema_read_error_redacts_password(tmp_path: Path) -> None:
    _prepare_ground_truth(tmp_path)

    def connect(**_: object) -> None:
        raise RuntimeError("failed with database-secret")

    lab = IncidentLab(
        Settings(_env_file=None, postgres_password="database-secret"),
        tmp_path,
        baseline_builder=SimpleNamespace(start_postgres=lambda: None),
        db_connect=connect,
    )

    with pytest.raises(IncidentExecutionError) as error:
        lab.inject(CASE_ID)

    assert "database-secret" not in str(error.value)
    assert "***" in str(error.value)
```

Expected: 查询、事务改名、BaselineError 和后置 Schema 校验路径的错误消息、`__cause__`、`__context__` 与格式化 traceback 均不包含数据库密码。

- [ ] **Step 5: 运行单测与 M1 回归**

```powershell
uv run pytest tests/unit/test_lab.py -q
uv run pytest tests/unit/test_baseline.py tests/unit/test_dbt_runner.py -q
uv run pytest tests/unit -q
uv run ruff check src tests
```

Expected: 全部命令 exit 0；没有真实 Docker/PostgreSQL 调用进入 unit tests。

- [ ] **Step 6: 提交并独立审查**

```powershell
git diff --check -- src/data_incident_gym/lab.py tests/unit/test_lab.py
git add src/data_incident_gym/lab.py tests/unit/test_lab.py
git commit -m "feat: add guarded incident reset and injection"
```

Expected: 独立 `luna_worker` 核对事务、固定 allowlist、未知状态 fail closed、无任意 SQL 和无 submodule 修改后给出 PASS。

---

### Task 4: 捕获逐次故障产物并独立验证

**Files:**
- Create: `src/data_incident_gym/lab_verifier.py`
- Create: `tests/unit/test_lab_verifier.py`
- Modify: `src/data_incident_gym/lab.py`
- Modify: `tests/unit/test_lab.py`

- [ ] **Step 1: 写独立验证器失败测试**

创建 `tests/unit/test_lab_verifier.py`：

```python
import json
from pathlib import Path

import pytest

from data_incident_gym.baseline import (
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab_verifier import IncidentVerifier, LabVerificationError

RUN_ID = "0123456789abcdef0123456789abcdef"


def _write_valid_run(tmp_path: Path, project_root: Path) -> Path:
    truth = load_ground_truth(CASE_ID, project_root)
    config_path = tmp_path / "config/incidents/schema_rename_payment_amount.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(truth.to_json(), encoding="utf-8")

    run_root = tmp_path / ".dig/lab/runs" / RUN_ID
    target = run_root / "dbt/target"
    logs = run_root / "dbt/logs"
    target.mkdir(parents=True)
    logs.mkdir(parents=True)
    (run_root / "ground_truth.json").write_text(truth.to_json(), encoding="utf-8")
    (run_root / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "m2.run.v1",
                "run_id": RUN_ID,
                "incident_case_id": CASE_ID,
                "dbt_exit_code": 1,
                "ground_truth_digest": truth.digest(),
            }
        ),
        encoding="utf-8",
    )
    relation = RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=(
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("total_amount", "integer", True, 4),
        ),
    )
    schema = make_baseline_summary("analytics", (relation,))
    (run_root / "schema.json").write_text(schema.to_json(), encoding="utf-8")
    (target / "run_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "unique_id": "model.jaffle_shop.stg_payments",
                        "status": "error",
                    },
                    {"unique_id": "model.jaffle_shop.orders", "status": "skipped"},
                    {"unique_id": "model.jaffle_shop.customers", "status": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.jaffle_shop.stg_payments": {"resource_type": "model"},
                    "model.jaffle_shop.orders": {"resource_type": "model"},
                    "model.jaffle_shop.customers": {"resource_type": "model"},
                },
                "child_map": {
                    "model.jaffle_shop.stg_payments": [
                        "model.jaffle_shop.orders",
                        "model.jaffle_shop.customers",
                    ],
                    "model.jaffle_shop.orders": [],
                    "model.jaffle_shop.customers": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (logs / "dbt.log").write_text("Database Error", encoding="utf-8")
    return run_root


def test_verifier_accepts_expected_failure_and_writes_stable_result(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)

    result = IncidentVerifier(tmp_path).verify(RUN_ID)

    assert result.status == "EXPECTED_FAILURE"
    assert result.failed_nodes == ("model.jaffle_shop.stg_payments",)
    assert result.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert result.error_category == "DBT_MODEL_ERROR"
    assert (run_root / "verification.json").read_text(encoding="utf-8") == (
        result.to_json()
    )


def test_verifier_rejects_wrong_failed_node(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    results_path = run_root / "dbt/target/run_results.json"
    results_path.write_text(
        json.dumps(
            {
                "results": [
                    {"unique_id": "model.jaffle_shop.orders", "status": "error"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError, match="直接失败节点"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_unexpected_dbt_success(
    tmp_path: Path,
    project_root: Path,
) -> None:
    run_root = _write_valid_run(tmp_path, project_root)
    metadata_path = run_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dbt_exit_code"] = 0
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LabVerificationError, match="dbt 意外成功"):
        IncidentVerifier(tmp_path).verify(RUN_ID)


def test_verifier_rejects_invalid_run_id(tmp_path: Path) -> None:
    with pytest.raises(LabVerificationError, match="run_id"):
        IncidentVerifier(tmp_path).verify("../../outside")
```

- [ ] **Step 2: 运行验证器测试并确认 RED**

```powershell
uv run pytest tests/unit/test_lab_verifier.py -q
```

Expected: exit 非零，collection 因 `data_incident_gym.lab_verifier` 不存在而失败。

- [ ] **Step 3: 实现只读 artifact 验证器**

创建 `src/data_incident_gym/lab_verifier.py`：

```python
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from data_incident_gym.baseline import (
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.incidents import (
    IncidentCaseError,
    load_ground_truth,
    parse_ground_truth,
)

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class LabVerificationError(RuntimeError):
    """Raised when persisted lab facts do not match Ground Truth."""


@dataclass(frozen=True)
class LabVerification:
    status: Literal["EXPECTED_FAILURE"]
    incident_case_id: str
    run_id: str
    failed_nodes: tuple[str, ...]
    affected_assets: tuple[str, ...]
    error_category: str
    schema_fingerprint: str
    ground_truth_digest: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


class IncidentVerifier:
    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LabVerificationError(f"无法读取验证产物：{path.name}") from exc
        if not isinstance(payload, dict):
            raise LabVerificationError(f"验证产物必须是 JSON object：{path.name}")
        return payload

    @staticmethod
    def _model_descendants(manifest: dict[str, Any], start: str) -> set[str]:
        nodes = manifest.get("nodes")
        child_map = manifest.get("child_map")
        if not isinstance(nodes, dict) or not isinstance(child_map, dict):
            raise LabVerificationError("manifest 缺少 nodes/child_map")
        found = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            children = child_map.get(current, [])
            if not isinstance(children, list):
                raise LabVerificationError("manifest child_map 无效")
            for child in children:
                node = nodes.get(child)
                if (
                    isinstance(child, str)
                    and isinstance(node, dict)
                    and node.get("resource_type") == "model"
                    and child not in found
                ):
                    found.add(child)
                    pending.append(child)
        return found

    def verify(self, run_id: str) -> LabVerification:
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise LabVerificationError(f"非法 run_id：{run_id}")
        run_root = self.project_root / ".dig" / "lab" / "runs" / run_id
        metadata = self._read_object(run_root / "metadata.json")
        if metadata.get("run_id") != run_id:
            raise LabVerificationError("metadata run_id 不一致")
        case_id = metadata.get("incident_case_id")
        if not isinstance(case_id, str):
            raise LabVerificationError("metadata 缺少 incident_case_id")
        try:
            committed_truth = load_ground_truth(case_id, self.project_root)
            snapshot_truth = parse_ground_truth(
                (run_root / "ground_truth.json").read_text(encoding="utf-8"),
                "ground_truth.json",
            )
        except (OSError, IncidentCaseError) as exc:
            raise LabVerificationError("Ground Truth 快照无效") from exc
        if snapshot_truth.digest() != committed_truth.digest():
            raise LabVerificationError("Ground Truth 快照与提交版本不一致")
        if metadata.get("ground_truth_digest") != committed_truth.digest():
            raise LabVerificationError("metadata Ground Truth digest 不一致")
        dbt_exit_code = metadata.get("dbt_exit_code")
        if (
            not isinstance(dbt_exit_code, int)
            or isinstance(dbt_exit_code, bool)
            or dbt_exit_code == 0
        ):
            raise LabVerificationError("dbt 意外成功，未触发预期故障")

        run_results = self._read_object(run_root / "dbt/target/run_results.json")
        results = run_results.get("results")
        if not isinstance(results, list):
            raise LabVerificationError("run_results 缺少 results")
        failed_nodes = tuple(
            sorted(
                result["unique_id"]
                for result in results
                if isinstance(result, dict)
                and result.get("status") == "error"
                and isinstance(result.get("unique_id"), str)
                and result["unique_id"].startswith("model.")
            )
        )
        if failed_nodes != (committed_truth.direct_failure,):
            raise LabVerificationError(
                f"直接失败节点不匹配：{failed_nodes}"
            )

        manifest = self._read_object(run_root / "dbt/target/manifest.json")
        affected = self._model_descendants(manifest, committed_truth.direct_failure)
        if affected != set(committed_truth.affected_assets):
            raise LabVerificationError(f"影响模型不匹配：{sorted(affected)}")

        schema = self._read_object(run_root / "schema.json")
        relations = schema.get("relations")
        if not isinstance(relations, list) or len(relations) != 1:
            raise LabVerificationError("Schema 快照必须只包含故障关系")
        relation = relations[0]
        if not isinstance(relation, dict):
            raise LabVerificationError("Schema relation 无效")
        columns = relation.get("columns")
        if not isinstance(columns, list):
            raise LabVerificationError("Schema columns 无效")
        try:
            column_summaries = tuple(
                ColumnSummary(
                    name=column["name"],
                    data_type=column["data_type"],
                    nullable=column["nullable"],
                    ordinal_position=column["ordinal_position"],
                )
                for column in columns
                if isinstance(column, dict)
            )
        except (KeyError, TypeError) as exc:
            raise LabVerificationError("Schema column 内容无效") from exc
        if len(column_summaries) != len(columns):
            raise LabVerificationError("Schema column 内容无效")
        column_names = tuple(column.name for column in column_summaries)
        if column_names != committed_truth.expected_schema.fault_columns:
            raise LabVerificationError(f"故障 Schema 列不匹配：{column_names}")
        if relation.get("row_count") != committed_truth.expected_schema.row_count:
            raise LabVerificationError("故障 Schema 行数不匹配")
        fingerprint = schema.get("fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise LabVerificationError("Schema fingerprint 无效")
        schema_name = schema.get("schema")
        if not isinstance(schema_name, str):
            raise LabVerificationError("Schema 名称无效")
        recomputed = make_baseline_summary(
            schema_name,
            (
                RelationSummary(
                    name=committed_truth.expected_schema.relation,
                    row_count=committed_truth.expected_schema.row_count,
                    columns=column_summaries,
                ),
            ),
        )
        if recomputed.fingerprint != fingerprint:
            raise LabVerificationError("Schema fingerprint 与内容不一致")
        dbt_log = run_root / "dbt/logs/dbt.log"
        try:
            log_is_valid = dbt_log.is_file() and dbt_log.stat().st_size > 0
        except OSError as exc:
            raise LabVerificationError("无法检查 dbt.log") from exc
        if not log_is_valid:
            raise LabVerificationError("缺少 dbt.log")

        verification = LabVerification(
            status="EXPECTED_FAILURE",
            incident_case_id=case_id,
            run_id=run_id,
            failed_nodes=failed_nodes,
            affected_assets=committed_truth.affected_assets,
            error_category=committed_truth.expected_failure_category,
            schema_fingerprint=fingerprint,
            ground_truth_digest=committed_truth.digest(),
        )
        try:
            (run_root / "verification.json").write_text(
                verification.to_json(),
                encoding="utf-8",
            )
        except OSError as exc:
            raise LabVerificationError("无法写入 verification.json") from exc
        return verification
```

- [ ] **Step 4: 为 `IncidentLab` 增加逐次故障构建**

在 `src/data_incident_gym/lab.py` 增加 imports：

```python
import json
import re
from uuid import uuid4

from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner
from data_incident_gym.lab_verifier import (
    IncidentVerifier,
    LabVerification,
    LabVerificationError,
)
```

增加类型和错误：

```python
RunIdFactory = Callable[[], str]
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class FaultVerificationError(LabError):
    code = "FAULT_VERIFICATION_ERROR"


@dataclass(frozen=True)
class FaultRun:
    case_id: str
    run_id: str
    artifact_dir: Path
    dbt_exit_code: int
    verification: LabVerification
```

将 `IncidentLab.__init__()` 扩展为以下完整参数，并在现有初始化后保存依赖：

```python
def __init__(
    self,
    settings: Settings,
    project_root: Path = PROJECT_ROOT,
    *,
    baseline_builder: BaselineBuilder | None = None,
    db_connect: DatabaseConnect | None = None,
    dbt_runner: DbtRunner | None = None,
    verifier: IncidentVerifier | None = None,
    run_id_factory: RunIdFactory | None = None,
) -> None:
    self.settings = settings
    self.project_root = project_root
    self.db_connect = db_connect or psycopg.connect
    self.baseline_builder = baseline_builder or BaselineBuilder(
        settings,
        project_root,
        db_connect=self.db_connect,
    )
    self.dbt_runner = dbt_runner or DbtRunner(settings, project_root)
    self.verifier = verifier or IncidentVerifier(project_root)
    self.run_id_factory = run_id_factory or (lambda: uuid4().hex)
```

增加以下方法：

```python
def _write_text(self, path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise IncidentExecutionError(
            f"无法写入故障运行产物：{self._redact(str(exc))}"
        ) from None


def _redact_file(self, path: Path) -> None:
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        redacted = self._redact(text)
        if redacted != text:
            path.write_text(redacted, encoding="utf-8")
    except OSError as exc:
        raise IncidentExecutionError(
            f"无法脱敏故障运行产物：{self._redact(str(exc))}"
        ) from None


def build(self, case_id: str) -> FaultRun:
    truth = load_ground_truth(case_id, self.project_root)
    self._start_postgres()
    relation = self._inspect_relation(truth)
    state = self._classify_state(relation, truth)
    if state != "INJECTED" or relation is None:
        raise InvalidIncidentState(
            f"故障构建要求已注入状态，当前状态：{state}"
        )

    run_id = self.run_id_factory()
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise IncidentExecutionError(f"run_id 生成器返回非法值：{run_id}")
    run_root = self.project_root / ".dig" / "lab" / "runs" / run_id
    try:
        run_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise IncidentExecutionError(
            f"无法创建故障运行目录：{self._redact(str(exc))}"
        ) from None

    self._write_text(run_root / "ground_truth.json", truth.to_json())
    target = run_root / "dbt" / "target"
    logs = run_root / "dbt" / "logs"
    try:
        dbt_result = self.dbt_runner.run_incident(target, logs)
    except DbtExecutionError as exc:
        raise IncidentExecutionError(str(exc)) from None
    self._write_text(run_root / "dbt/stdout.log", dbt_result.stdout)
    self._write_text(run_root / "dbt/stderr.log", dbt_result.stderr)
    self._redact_file(logs / "dbt.log")
    after_build = self._inspect_relation(truth)
    schema = make_baseline_summary(
        self.settings.postgres_schema,
        () if after_build is None else (after_build,),
    )
    self._write_text(run_root / "schema.json", schema.to_json())
    metadata = {
        "schema_version": "m2.run.v1",
        "run_id": run_id,
        "incident_case_id": case_id,
        "dbt_exit_code": dbt_result.return_code,
        "ground_truth_digest": truth.digest(),
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
        },
    }
    self._write_text(
        run_root / "metadata.json",
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
    )
    try:
        verification = self.verifier.verify(run_id)
    except LabVerificationError as exc:
        raise FaultVerificationError(str(exc)) from None
    return FaultRun(
        case_id=case_id,
        run_id=run_id,
        artifact_dir=run_root,
        dbt_exit_code=dbt_result.return_code,
        verification=verification,
    )
```

不要捕获后删除 run 目录；验证失败时保留已写出的 Ground Truth、Schema、stdout/stderr 和 dbt artifacts 供审计。

- [ ] **Step 5: 补齐 `IncidentLab.build()` 单测**

在 `tests/unit/test_lab.py` 追加一个固定 run 隔离测试。Fake runner 必须创建 run-specific artifacts，Fake verifier 只验证调用发生：

```python
def test_build_uses_unique_run_paths_and_returns_expected_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, _ = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: INJECTED)

    class FakeDbtRunner:
        def run_incident(self, target: Path, logs: Path):
            target.mkdir(parents=True)
            logs.mkdir(parents=True)
            (target / "manifest.json").write_text("{}", encoding="utf-8")
            (target / "run_results.json").write_text("{}", encoding="utf-8")
            (logs / "dbt.log").write_text("failure dig_admin", encoding="utf-8")
            return SimpleNamespace(return_code=1, stdout="out", stderr="err")

    verification = SimpleNamespace(status="EXPECTED_FAILURE")

    class FakeVerifier:
        def __init__(self) -> None:
            self.run_ids: list[str] = []

        def verify(self, run_id: str):
            self.run_ids.append(run_id)
            return verification

    fake_verifier = FakeVerifier()
    lab.dbt_runner = FakeDbtRunner()
    lab.verifier = fake_verifier
    lab.run_id_factory = lambda: "0123456789abcdef0123456789abcdef"

    result = lab.build(CASE_ID)

    assert result.dbt_exit_code == 1
    assert result.verification.status == "EXPECTED_FAILURE"
    assert fake_verifier.run_ids == [result.run_id]
    assert result.artifact_dir == (
        tmp_path / ".dig/lab/runs/0123456789abcdef0123456789abcdef"
    )
    assert (result.artifact_dir / "metadata.json").is_file()
    assert (result.artifact_dir / "dbt/stdout.log").read_text(encoding="utf-8") == "out"
    assert "dig_admin" not in (
        result.artifact_dir / "dbt/logs/dbt.log"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / ".dig/dbt/target/run_results.json").exists()


def test_build_rejects_noninjected_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lab, _ = _lab(tmp_path)
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: HEALTHY)

    with pytest.raises(InvalidIncidentState, match="要求已注入状态"):
        lab.build(CASE_ID)
```

- [ ] **Step 6: 运行、提交并独立审查**

```powershell
uv run pytest tests/unit/test_lab_verifier.py tests/unit/test_lab.py -q
uv run pytest tests/unit -q
uv run ruff check src tests
git diff --check -- src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py
git add src/data_incident_gym/lab.py src/data_incident_gym/lab_verifier.py tests/unit/test_lab.py tests/unit/test_lab_verifier.py
git commit -m "feat: capture and verify incident runs"
```

Expected: 全部命令 exit 0；对抗性审查特别确认 run ID 不可路径逃逸、旧 run 不覆盖、dbt 自然语言不参与分类、密码不进入产物、验证器不调用注入器。

---

### Task 5: 暴露三个中文 M2 CLI 入口

**Files:**
- Modify: `src/data_incident_gym/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: 先写 CLI 成功与错误语义测试**

在 `tests/unit/test_cli.py` 追加：

```python
def test_lab_commands_delegate_without_real_infrastructure(monkeypatch) -> None:
    class FakeLab:
        def reset(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(state="HEALTHY", fingerprint="a" * 64)

        def inject(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(state="INJECTED", fingerprint="b" * 64)

        def build(self, case_id: str):
            assert case_id == "schema_rename_payment_amount"
            return SimpleNamespace(
                run_id="0123456789abcdef0123456789abcdef",
                dbt_exit_code=1,
                artifact_dir=Path(".dig/lab/runs/0123456789abcdef0123456789abcdef"),
                verification=SimpleNamespace(status="EXPECTED_FAILURE"),
            )

    monkeypatch.setattr(cli, "create_incident_lab", lambda: FakeLab())

    reset = runner.invoke(cli.app, ["lab", "reset", "schema_rename_payment_amount"])
    inject = runner.invoke(cli.app, ["lab", "inject", "schema_rename_payment_amount"])
    build = runner.invoke(cli.app, ["lab", "build", "schema_rename_payment_amount"])

    assert reset.exit_code == 0
    assert "HEALTHY" in reset.stdout
    assert inject.exit_code == 0
    assert "INJECTED" in inject.stdout
    assert build.exit_code == 0
    assert "EXPECTED_FAILURE" in build.stdout
    assert "dbt_exit_code: 1" in build.stdout


def test_lab_error_is_chinese_stderr_with_nonzero_exit(monkeypatch) -> None:
    class FakeLab:
        def inject(self, case_id: str):
            raise InvalidIncidentState("当前状态：INJECTED")

    monkeypatch.setattr(cli, "create_incident_lab", lambda: FakeLab())

    result = runner.invoke(
        cli.app,
        ["lab", "inject", "schema_rename_payment_amount"],
    )

    assert result.exit_code != 0
    assert "INVALID_INCIDENT_STATE" in result.stderr
    assert "当前状态：INJECTED" in result.stderr
    assert "Traceback" not in result.stderr
```

并在文件顶部增加：

```python
from pathlib import Path

from data_incident_gym.lab import InvalidIncidentState
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_cli.py -q
```

Expected: 新测试失败，因为 `lab` Typer group 和 `create_incident_lab()` 尚不存在；原有 M1 CLI 测试仍通过。

- [ ] **Step 3: 实现薄 CLI 适配层**

在 `src/data_incident_gym/cli.py` 增加 imports：

```python
from data_incident_gym.incidents import IncidentCaseError
from data_incident_gym.lab import IncidentLab, LabError
```

在现有 `pipeline_app` 旁创建并注册：

```python
lab_app = typer.Typer(help="重置、注入并复现固定数据故障。")
app.add_typer(lab_app, name="lab")
```

增加 factory 和统一错误输出：

```python
def create_incident_lab() -> IncidentLab:
    return IncidentLab(Settings())


def _exit_lab_error(error: LabError | IncidentCaseError) -> None:
    code = getattr(error, "code", "INCIDENT_CASE_ERROR")
    typer.echo(f"故障实验失败 [{code}]：{error}", err=True)
    raise typer.Exit(code=1) from error
```

增加三个命令：

```python
@lab_app.command("reset")
def lab_reset(case_id: str) -> None:
    """把固定案例恢复为健康状态。"""
    try:
        result = create_incident_lab().reset(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障案例重置成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("inject")
def lab_inject(case_id: str) -> None:
    """向健康基线注入固定字段改名故障。"""
    try:
        result = create_incident_lab().inject(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("故障注入成功。")
    typer.echo(f"state: {result.state}")
    typer.echo(f"fingerprint: {result.fingerprint}")


@lab_app.command("build")
def lab_build(case_id: str) -> None:
    """运行无 seed 的 dbt build 并验证预期故障。"""
    try:
        result = create_incident_lab().build(case_id)
    except (LabError, IncidentCaseError) as exc:
        _exit_lab_error(exc)
    typer.echo("预期故障复现成功。")
    typer.echo(f"status: {result.verification.status}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"dbt_exit_code: {result.dbt_exit_code}")
    typer.echo(f"artifacts: {result.artifact_dir}")
```

CLI 不允许 `--sql`、`--table`、`--column`、`--skip-seed`、`--run-id` 或 `--path` 参数。

- [ ] **Step 4: 扩展帮助文本断言**

在 `test_help_is_in_chinese_for_app_pipeline_and_build()` 之外新增：

```python
def test_lab_help_is_chinese_and_lists_only_m2_actions() -> None:
    lab_help = runner.invoke(cli.app, ["lab", "--help"])

    assert lab_help.exit_code == 0
    assert "重置、注入并复现固定数据故障" in lab_help.stdout
    assert "reset" in lab_help.stdout
    assert "inject" in lab_help.stdout
    assert "build" in lab_help.stdout
    assert "replay" not in lab_help.stdout
```

- [ ] **Step 5: 运行 CLI 与全部 unit tests**

```powershell
uv run pytest tests/unit/test_cli.py -q
uv run pytest tests/unit -q
uv run ruff check src tests
uv run data-incident-gym lab --help
```

Expected: 测试和 Ruff exit 0；帮助文本只列出 `reset`、`inject`、`build`，不出现 M3–M5 命令。

- [ ] **Step 6: 提交并独立审查**

```powershell
git diff --check -- src/data_incident_gym/cli.py tests/unit/test_cli.py
git add src/data_incident_gym/cli.py tests/unit/test_cli.py
git commit -m "feat: expose M2 incident lab commands"
```

Expected: 审查确认 CLI 是薄 adapter、预期 dbt 非零不会错误映射为 CLI 非零、所有真正失败都有中文 stderr 和非零 exit。

---

### Task 6: 完成真实复现、恢复、文档和双平台门槛

**Files:**
- Create: `tests/integration/test_incident_lab.py`
- Create: `tests/e2e/test_incident_reproducibility.py`
- Modify: `pyproject.toml:32-36`
- Modify: `README.md`
- Verify: `.github/workflows/ci.yml`
- Verify: `third_party/jaffle_shop`

- [ ] **Step 1: 写一次真实闭环集成测试**

创建 `tests/integration/test_incident_lab.py`：

```python
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
```

- [ ] **Step 2: 运行真实集成测试并处理唯一允许的结果**

```powershell
uv run pytest tests/integration/test_incident_lab.py -q -s
```

Expected: Docker Desktop 和固定 PostgreSQL 可用时 exit 0；输出证明 dbt 子进程非零但实验验证为 `EXPECTED_FAILURE`，finally 恢复健康。若实际失败节点不是唯一的 `model.jaffle_shop.stg_payments`、`run_results.json` 缺失、reset 需要删 volume 或必须修改 submodule，立即触发停止规则，不调整 Ground Truth 掩盖事实。

- [ ] **Step 3: 写十次真实复现 E2E**

创建 `tests/e2e/test_incident_reproducibility.py`：

```python
import json
import subprocess
from pathlib import Path

import pytest

from data_incident_gym.config import Settings
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab


@pytest.mark.e2e
def test_incident_is_reproducible_across_ten_runs(project_root: Path) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    initial = lab.reset(CASE_ID)
    stable_results: list[str] = []
    run_dirs: list[Path] = []

    try:
        for run_number in range(1, 11):
            reset = lab.reset(CASE_ID)
            injection = lab.inject(CASE_ID)
            run = lab.build(CASE_ID)
            projection = {
                "failed_nodes": run.verification.failed_nodes,
                "affected_assets": run.verification.affected_assets,
                "error_category": run.verification.error_category,
                "schema_fingerprint": run.verification.schema_fingerprint,
                "ground_truth_digest": run.verification.ground_truth_digest,
            }
            stable_results.append(json.dumps(projection, sort_keys=True))
            run_dirs.append(run.artifact_dir)
            print(
                f"incident run {run_number}/10: "
                f"run_id={run.run_id} projection={projection}"
            )
            assert reset.fingerprint == initial.fingerprint
            assert injection.fingerprint != initial.fingerprint
    finally:
        recovered = lab.reset(CASE_ID)

    assert len(set(stable_results)) == 1
    assert len({path.name for path in run_dirs}) == 10
    assert all(path.is_dir() for path in run_dirs)
    assert recovered.fingerprint == initial.fingerprint
    submodule = subprocess.run(
        ["git", "-C", str(project_root / "third_party/jaffle_shop"), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert submodule.stdout.strip() == ""
```

- [ ] **Step 4: 运行十次复现和 M1 回归**

```powershell
uv run pytest tests/e2e/test_incident_reproducibility.py -q -s
uv run data-incident-gym pipeline build
uv run pytest tests/e2e/test_baseline_reproducibility.py -q -s
```

Expected: 三条命令均 exit 0；十个 run ID 各不相同但稳定 projection 只有一个；最后健康 `pipeline build` 与 M1 十次基线仍通过。不得把某次失败从统计中删除或重试覆盖。

- [ ] **Step 5: 更新测试描述与 README**

在 `pyproject.toml` 将 e2e marker 改为：

```toml
markers = [
  "integration: requires Docker and PostgreSQL",
  "e2e: runs repeated real PostgreSQL and dbt workflows",
]
```

把 README 当前状态改为：

```markdown
当前已实现 M1 健康基线和 M2 首个确定性故障实验室；M3、M4、M5 尚未实现，证据工具、Agent、诊断评测和完整事故闭环仍不可用。
```

在 M1 运行说明后增加：

````markdown
## 运行 M2 字段改名故障

```powershell
uv run data-incident-gym lab reset schema_rename_payment_amount
uv run data-incident-gym lab inject schema_rename_payment_amount
uv run data-incident-gym lab build schema_rename_payment_amount
```

`lab build` 不运行 seed。底层 dbt 按预期非零退出且独立验证通过时，命令输出 `EXPECTED_FAILURE` 并整体 exit 0；原始 dbt exit code 和脱敏输出保存在 `.dig/lab/runs/<run_id>/`。`pipeline build` 始终保留健康基线语义。

M2 只提供受控实验室写操作，不提供 Agent、任意 SQL、自动修复或生产连接。
````

Markdown 外层书写时正确嵌套代码围栏，最终 README 必须能正常渲染。

- [ ] **Step 6: 完成本地总门槛、提交并等待 Ubuntu CI**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
git diff --check
git status --short
```

Expected:

- Ruff、unit、integration、e2e、lock check 和 diff check 全部 exit 0。
- submodule HEAD 精确为 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，status 无输出。
- `git status --short` 只显示本 Task 预期文件以及用户原有的 `AGENT.md` 修改；提交前绝不 stage `AGENT.md`。

提交：

```powershell
git add tests/integration/test_incident_lab.py tests/e2e/test_incident_reproducibility.py pyproject.toml README.md
git commit -m "test: prove M2 incident reproducibility"
```

提交后派遣新的独立 `luna_worker` 对 M2 全部 commits 做最终对抗性审查。若审查通过，只有在用户明确授权 push 后才推送；观察到 Ubuntu CI 对 M2 HEAD 成功后，才能把 M2 标记完成。不得把本地等价命令写成 Ubuntu CI 已通过。

---

## M2 最终完成门槛

- [ ] 用户已批准本实施计划并明确授权开始执行。
- [ ] `pipeline build` 仍执行健康 seed full-refresh + dbt build，M1 全套测试无回归。
- [ ] `lab reset` 从缺失、健康和本案例已注入状态恢复健康；未知漂移 fail closed。
- [ ] `lab inject` 只执行固定事务化列改名，重复注入和非健康状态非零退出。
- [ ] `lab build` 的命令不包含 seed，使用逐 run target/log 路径。
- [ ] 预期 dbt 非零被保存并验证为 `EXPECTED_FAILURE`；意外成功或错误失败非零退出。
- [ ] Ground Truth 严格可读、digest 稳定、运行快照与提交版本一致。
- [ ] 独立验证器证明直接失败节点、模型血缘、Schema 状态和 Ground Truth 一致。
- [ ] 十次真实 `reset → inject → build` 的稳定 projection 完全一致，十个 run 均保留。
- [ ] 最终 `reset → pipeline build` 恢复 M1 健康 fingerprint，旧故障产物仍可读。
- [ ] stdout/stderr、异常、metadata 和 JSON 不含数据库密码。
- [ ] `third_party/jaffle_shop` commit 固定且 clean，没有任何源码修改。
- [ ] Ruff、unit、integration、e2e、`uv lock --check`、`git diff --check` 全部通过。
- [ ] 每个 Task 的独立 `luna_worker` 审查通过，最终全量审查通过。
- [ ] 用户授权推送后，Ubuntu CI 对 M2 HEAD 成功。
- [ ] README 只宣称 M1/M2 已实现，明确 M3–M5 尚未实现。

## 实施停止规则

遇到以下任一情况，保留完整脱敏证据并暂停当前 Task，向用户请求决策：

1. 真实 dbt 故障的唯一直接失败节点不是 `model.jaffle_shop.stg_payments`。
2. 只有解析易变的完整自然语言错误文本才能得到稳定错误类别。
3. `dbt build --exclude-resource-type seed` 在锁定 dbt 版本中仍执行或重建 seed。
4. reset 只有删除 Docker volume、使用 `CASCADE` 清除未知对象或修改 submodule 才能成功。
5. Ground Truth、固定上游 commit、Schema/行数或需求基线与真实结果不一致。
6. 需要更换 PostgreSQL/dbt/uv/Python 固定版本或修改 `uv.lock` 中既有依赖。
7. 任一实现会提前引入 M3 EvidenceRecord/只读角色、M4 Agent/Ollama 或 M5 诊断评测。
8. 任一日志、异常或运行产物泄漏数据库密码。
9. Ubuntu CI 与本地结果不一致，或 push/远程操作尚未得到用户明确授权。
