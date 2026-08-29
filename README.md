# DataIncidentGym

DataIncidentGym 是一个可复现的数据事故诊断实验场：它在真实 PostgreSQL 与 dbt 项目上注入固定故障，让受限 Agent 通过只读工具调查，并用独立的确定性评测器核验诊断结果。

## 当前状态

项目已实现 M1–M6，形成“健康基线 → 故障注入 → 证据调查 → 结构化诊断 → 确定性评测 → 六文件报告 → 健康恢复”的完整闭环。

当前固定支持两个案例：

| case_id | 故障 | root_cause_code |
| --- | --- | --- |
| `schema_rename_payment_amount` | `raw_payments.amount` 改名为 `total_amount` | `SOURCE_SCHEMA_COLUMN_RENAMED` |
| `schema_type_change_payment_amount` | `raw_payments.amount` 从 `integer` 改为 `text` | `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED` |

M6 引入 Diagnostic Kernel v1，显式维护候选假设、证据缺口、主张与证据绑定及剩余预算。Kernel 只验证、拒绝和投影模型声明；Ground Truth 仅由独立 evaluator 读取。

## 前置条件

- Windows 11
- PowerShell 7
- Git
- Python 3.12.10
- uv 0.11.24
- Docker Desktop
- 运行诊断时可访问配置的 OpenAI-compatible 模型 endpoint

版本约束以 `pyproject.toml`、`uv.lock` 和 `compose.yaml` 为准。

## 初始化

在项目根目录使用 PowerShell 7 执行：

```powershell
git submodule update --init --recursive
uv sync --frozen
```

固定的 Jaffle Shop 模型位于 `third_party/jaffle_shop` submodule。PostgreSQL 由 `compose.yaml` 管理，默认监听本机 `55432` 端口。

## 诊断配置

复制显式配置模板：

```powershell
Copy-Item -LiteralPath .env.diagnostic.example -Destination .env.diagnostic
$env:MIMO_API_KEY = '<your-api-key>'
```

`.env.diagnostic` 已被 Git 忽略。默认模型为 `mimo-v2.5`，endpoint 为 `https://api.xiaomimimo.com/v1`；密钥不得写入仓库。

在真实诊断前运行只读环境检查：

```powershell
uv run data-incident-gym doctor
```

`doctor` 会执行模型最小能力探针，成功只表示环境与工具调用能力就绪，不代表完整评测通过。

## 运行

构建健康基线：

```powershell
uv run data-incident-gym pipeline build
```

运行一个完整案例闭环：

```powershell
uv run data-incident-gym eval run schema_rename_payment_amount
uv run data-incident-gym eval run schema_type_change_payment_amount
```

`eval run` 会依次执行初始 reset、故障注入、预期失败构建、Agent 诊断、确定性评测、artifact 写入和最终 reset。单独排障时也可以拆开执行：

```powershell
$caseId = 'schema_rename_payment_amount'
uv run data-incident-gym pipeline build
uv run data-incident-gym lab inject $caseId
uv run data-incident-gym lab build $caseId
uv run data-incident-gym diagnose $caseId
uv run data-incident-gym lab reset $caseId
```

## 运行产物

每次完整评测在 `artifacts/<run_id>/` 写入且只写入六个文件：

```text
metadata.json
trace.jsonl
evidence.json
diagnosis.json
evaluation.json
report.md
```

`artifacts/` 与 `.dig/` 默认不提交 Git。报告只展示结构化诊断、调查状态和安全的确定性评测结果，不保存模型隐藏推理、原始 provider 回复、凭据或 Ground Truth。

## 安全边界

- Agent 只有 `get_dbt_run_results`、`get_dbt_node_error`、`get_relation_schema`、`get_dbt_lineage` 四个只读工具。
- PostgreSQL Schema 查询使用独立的 `dig_reader` 只读角色。
- 不提供 Shell、任意文件、任意 SQL、数据库写入、源码修改或自动修复能力。
- 每次诊断最多 8 次模型请求、8 次工具调用，运行上限 300 秒。
- 只有 evaluator 读取 Ground Truth；Kernel、Agent、工具层、trace 和报告生成均不读取 Ground Truth。
- 依赖准备完成后，基线、故障实验、证据读取和确定性评测可离线运行；`diagnose`、`eval run` 和 `doctor` 的模型探针需要访问配置的模型 endpoint。

## 测试

常规确定性验证：

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv run pytest -m 'not real_model' -q
uv lock --check
git diff --check
```

`integration` 和普通 `e2e` 需要 Docker Desktop 与 PostgreSQL。真实模型验收必须显式启用，会产生外部模型请求：

```powershell
$env:DIG_RUN_REAL_MODEL_TESTS = '1'
try {
    uv run pytest tests/e2e/test_real_model_evaluation.py -q -s
}
finally {
    Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS -ErrorAction SilentlyContinue
}
```

## 项目文档

- [`docs/requirements.md`](docs/requirements.md)：唯一需求与验收合同。
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：第三方来源、固定版本与复用范围。

## 第三方许可证

固定的 Jaffle Shop 数据模型来自 [dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb)，使用 Apache-2.0 许可，并以 commit `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 保存在 `third_party/jaffle_shop` submodule 中。本项目使用 Apache License 2.0，完整文本见 [`LICENSE`](LICENSE)。第三方文件保留其原始许可证和版权声明。
