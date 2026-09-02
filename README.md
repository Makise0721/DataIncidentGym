# DataIncidentGym

DataIncidentGym 是一个面向数据工程师和值班开发者的可复现数据事故诊断实验场。它在真实
PostgreSQL 与 dbt 项目中确定性地注入 Schema 和数据质量故障，让 Agent 只能通过受限的只读
工具调查，再由独立 evaluator 根据 Ground Truth、证据引用和环境恢复状态核验诊断。

这个项目不是生产监控平台，也不会自动修复数据库。它提供的是一个本地、单用户、可重复运行的
CLI 环境，用来回答更具体的问题：在相同故障、相同可见信息和相同预算下，一种诊断策略能否给出
有证据支撑的根因与影响范围；当关键事实不可见时，它能否明确拒绝确认。

## 工作闭环

```text
健康基线
  → 注入固定事故
  → 执行并验证 dbt 结果
  → 发布不含答案的可观测上下文
  → Agent 调用只读工具调查
  → 确定性 evaluator 核验诊断
  → 写入六文件产物
  → 重置并验证环境恢复
```

Ground Truth 与公开调查上下文严格分离：实验编排器和 evaluator 可以读取私有
`ScenarioSpec`，Agent 只能看到 `IncidentBrief`、dbt artifacts、受白名单约束的 profile
snapshot，以及六个类型化只读工具。

## 核心能力

- **可复现事故实验**：在固定的 Jaffle Shop 数据集上执行健康构建、故障注入、结果验证和幂等重置。
- **受限 Agent 调查**：支持 `diagnostic-kernel` 与 `static-skill` 两种策略，不开放 Shell、
  任意文件、任意 SQL 或数据库写入。
- **证据绑定诊断**：输出 `CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`NO_INCIDENT` 或
  `MODEL_ERROR`，所有事实主张必须引用系统生成的 evidence ID。
- **确定性评测**：程序化检查根因、影响范围、证据存在性、证据与主张的一致性、策略边界、环境状态
  和最终恢复结果。
- **策略对照基准**：冻结场景、模型、预算、策略和运行 ID，支持 Agent 策略、消融策略与
  `FIXED_RULE` 对照。
- **可审计产物**：每次完整评测只生成固定的六个文件，不保存隐藏思维链、原始 provider 回复、
  凭据或 Ground Truth。

## 场景目录

仓库提供 17 个目录场景和 1 个向后兼容回归场景。带 `_a` / `_b` 的场景构成配对实验：
A 变体保留确认根因所需的公开证据，B 变体隐藏关键事实，期望 Agent 返回证据不足。

| 故障族 | 场景 | 主要验证目标 |
| --- | --- | --- |
| Schema 类型漂移 | `schema_type_change_payment_amount`、`schema_type_change_order_customer_a`、`schema_type_change_order_customer_b` | 识别字段类型变化，并在 Schema 不可见时拒绝确认 |
| 必填字段空值 | `required_null_payment_id`、`required_null_order_customer_a`、`required_null_order_customer_b` | 区分真正的必填字段破坏与无关 nullable 干扰项 |
| 重复支付 | `duplicate_payment_record`、`duplicate_payment_coupon_a`、`duplicate_payment_coupon_b` | 覆盖 dbt 失败和 dbt 成功但业务指纹重复两种路径 |
| 孤立支付 | `orphan_payment_record`、`orphan_payment_coupon_a`、`orphan_payment_coupon_b` | 联合支付、订单历史与 ingestion watermark 判断引用完整性 |
| 静默支付丢失 | `silent_payment_drop_record`、`silent_payment_drop_partition_a`、`silent_payment_drop_partition_b` | 在构建成功时通过跨关系历史事实发现缺失事件 |
| 健康对照 | `order_volume_pattern_a`、`order_volume_within_sla` | 用历史范围、watermark 与 SLA 证据证明无事故 |
| Schema 重命名回归 | `schema_rename_payment_amount` | 验证从 `amount` 到 `total_amount` 的基础诊断闭环 |

正式 Manifest 从目录中冻结 12 个配对/健康场景，形成 106 个 cell：94 个模型驱动 cell 和
12 个固定规则 cell。

## 架构

```text
config/scenarios/*.json ──→ Incident Lab ──→ PostgreSQL + dbt
          │                       │                  │
          │ private Ground Truth  │ public context   │ read-only facts
          ▼                       ▼                  ▼
   Deterministic Evaluator ← Structured Diagnosis ← Diagnosis Agent
              │
              └──→ metadata / trace / evidence / diagnosis / evaluation / report
```

主要模块：

- `baseline.py`、`lab.py`、`lab_verifier.py`：健康基线、事故生命周期和期望结果验证。
- `diagnostic_agent.py`、`diagnostic_kernel.py`：模型适配、调查状态和诊断策略。
- `evidence_tools.py`、`profiles.py`、`read_only_db.py`：有界证据读取与只读数据库角色。
- `evaluation.py`、`evaluation_runner.py`：诊断契约和端到端确定性评测。
- `benchmark_manifest.py`、`benchmark_runner.py`、`benchmark_report.py`：正式套件冻结、
  执行、ledger 与汇总。
- `artifacts.py`、`run_context.py`：运行身份、路径边界和六文件原子写入。

## 环境要求

- Git（需要 submodule 支持）
- Python 3.12.10
- uv 0.11.24
- Docker Desktop，或带 Docker Compose 的 Docker Engine
- PowerShell 7（本文命令以 PowerShell 为例）
- 运行模型诊断时可访问 OpenAI-compatible endpoint

依赖与镜像的精确版本以 `pyproject.toml`、`uv.lock` 和 `compose.yaml` 为准。PostgreSQL
默认监听本机 `55432` 端口。

## 快速开始

```powershell
git clone --recurse-submodules https://github.com/Makise0721/DataIncidentGym.git
Set-Location DataIncidentGym
uv sync --frozen
uv run data-incident-gym pipeline build
```

如果仓库已经 clone：

```powershell
git submodule update --init --recursive
uv sync --frozen
```

固定的 Jaffle Shop 项目位于 `third_party/jaffle_shop` submodule。健康构建会启动/检查
PostgreSQL、重新载入 seeds、执行 `dbt build`，并生成 `.dig/baseline-summary.json` 与受
`ProfileSpec` 约束的聚合快照。

## 模型配置

复制显式配置模板，并通过环境变量提供密钥：

```powershell
Copy-Item -LiteralPath .env.diagnostic.example -Destination .env.diagnostic
$env:MIMO_API_KEY = '<your-api-key>'
```

默认配置使用 OpenAI-compatible 的 `https://api.xiaomimimo.com/v1` 和
`mimo-v2.5`。`.env.diagnostic` 已被 Git 忽略，密钥不得写入仓库。

运行诊断前可执行环境检查：

```powershell
uv run data-incident-gym doctor
```

`doctor` 检查 Python、uv、Docker、PostgreSQL、dbt profile、聚合证据边界、模型可用性和
最小工具调用能力。它会发起一个模型能力探针；通过只表示运行环境就绪，不代表诊断质量通过。

## 运行事故诊断

一条命令执行完整闭环：

```powershell
uv run data-incident-gym eval run schema_type_change_order_customer_a
uv run data-incident-gym eval run order_volume_pattern_a --strategy static-skill
```

`eval run` 会完成初始 reset、故障注入、dbt 执行、Agent 诊断、确定性评测、artifact 写入和
最终 reset。也可以拆开观察每个阶段：

```powershell
$caseId = 'schema_type_change_payment_amount'
uv run data-incident-gym pipeline build
uv run data-incident-gym lab inject $caseId
try {
    uv run data-incident-gym lab build $caseId
    uv run data-incident-gym diagnose $caseId --strategy diagnostic-kernel
}
finally {
    uv run data-incident-gym lab reset $caseId
}
```

`diagnose` 默认读取最近一次已验证运行的活动 `run_id`，也可以显式传入
`--run-id <run_id>`。

## 只读调查工具

| 工具 | 可见事实 |
| --- | --- |
| `get_dbt_run_results` | 本次 dbt 节点状态与执行摘要 |
| `get_dbt_node_error` | 白名单节点的结构化错误信息 |
| `get_relation_schema` | 白名单关系的字段名、类型和可空性 |
| `get_dbt_lineage` | 受限的上游/下游血缘 |
| `get_relation_data_profile` | 配置允许的聚合计数、空值和重复指纹 |
| `get_relation_history` | 配置允许的历史分桶与 ingestion watermark |

所有工具输入都经过场景与运行上下文校验。Agent 不能扩展关系范围、读取原始行、提交任意 SQL，
也不能访问私有场景答案。

## 运行产物

每次完整评测写入 `artifacts/<run_id>/`：

| 文件 | 内容 |
| --- | --- |
| `metadata.json` | 运行身份、代码状态、模型/策略身份和跨文件绑定信息 |
| `trace.jsonl` | 有界的模型请求与工具事件，不含隐藏思维链或原始 provider payload |
| `evidence.json` | 类型化证据记录及其来源、时间和完整性信息 |
| `diagnosis.json` | 结构化状态、根因、影响、主张、证据引用和建议 |
| `evaluation.json` | evaluator 检查结果、环境有效性和恢复结论 |
| `report.md` | 面向人工审阅的安全摘要 |

`artifacts/` 与 `.dig/` 默认不提交 Git。写入器会校验运行身份、目录边界、符号链接、重复
文件和跨文件一致性，再以临时目录完成原子发布。

## 正式基准与证据边界

仓库随附 `config/benchmark/p1-formal-v1.json`，其中固定了场景摘要与哈希、实现 revision、
模型 endpoint、预算、策略身份、106 个确定性 run ID，以及期望的结果输入。CLI 提供不调用
模型的完整性与漂移检查：

```powershell
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v1.json
```

该 Manifest 属于已封存的历史输入；修复后的 `main` 已不再等于它绑定的实现。在最新代码上
执行上述命令会以 `result-input hashes drifted` 拒绝继续，这正是防止把新实现误记为旧批次的
保护行为，不代表可以更新或重冻该文件。

正式执行还要求干净 checkout、Manifest SHA-256 的显式确认、通过 preflight、独占 suite lock
和 append-only ledger；执行过程中不提供重试、替换样本或扩展预算选项。报告命令只读取并校验
既有 suite，不调用模型、数据库或 evaluator。

随附 Manifest 对应的唯一一次历史执行已封存为 `INVALID_HARNESS`：该批次暴露了 setup
失败物化、恢复传播、fail-stop 和报告适用性判断等 harness 缺陷，因此不能作为真实模型质量
结论，也不得重新运行或重新冻结。仓库中的修复提高了后续 harness 的正确性，但不会追溯改变旧
批次的证据含义。

## 资源与安全边界

- 单次诊断最多 8 次模型请求、8 次工具调用、2 次结构化输出重试，总时限 300 秒。
- PostgreSQL 证据读取使用独立的 `dig_reader` 只读角色，并只返回配置允许的聚合事实。
- 只有 evaluator 能读取 Ground Truth；Agent、工具层、trace 与报告生成均不能读取。
- 依赖准备完成后，基线、事故实验、证据读取和确定性评测可离线运行；模型诊断需要访问配置的
  endpoint。
- 项目不接收生产告警，不连接生产数据，不提供 Web UI、自由聊天、多用户协作、自动修复或发布
  操作。

## 验证

常规确定性验证：

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv run pytest -m 'not real_model' -q
uv build
uv lock --check
git diff --check
```

`integration` 和普通 `e2e` 需要 Docker/PostgreSQL。带 `real_model` marker 的测试默认
不执行，因为它们会产生外部请求和费用；任何真实模型评测都应先固定模型、样本、预算和停止条件，
并获得显式授权。

## 项目结构

```text
src/data_incident_gym/   Python package 与 Typer CLI
config/scenarios/        私有事故规格与验证合同
config/profiles/         可公开聚合事实的 ProfileSpec
config/benchmark/        冻结的正式 Manifest
tests/unit/              纯逻辑与安全契约测试
tests/integration/       PostgreSQL、Agent 和评测边界测试
tests/e2e/               完整事故生命周期与策略矩阵
third_party/jaffle_shop/ 固定的 dbt fixture submodule
docs/requirements.md     权威需求与验收合同
```

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。固定的 Jaffle Shop 数据模型来自
[dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb)，以 commit
`36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 保存在 submodule 中；第三方来源和复用范围见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
