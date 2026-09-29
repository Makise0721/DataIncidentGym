# DataIncidentGym

DataIncidentGym 是一个用于研究数据诊断 Agent 行为的可复现实验平台。它在真实
PostgreSQL 与 dbt 项目中确定性地注入 Schema 和数据质量故障，让 Agent 只能通过受限的只读
工具调查，再由独立 evaluator 根据 Ground Truth、证据引用和环境恢复状态核验诊断。

它提供本地、单用户 CLI，用来检验：一种诊断策略能否给出有证据支撑的根因与影响范围；关键事实
不可见时能否正确弃答；失败来自环境、工具调用、证据采集还是结论支撑。项目不连接生产数据，
不接收线上告警，也不自动修复数据库。

## 当前结论与项目状态

**截至 2026-09-29，本阶段策略研究收口，停止继续扩大真实模型测量和围绕失败反复调参。**
实现与历史证据保留，供复现、学习和独立研究使用；重新开展实验需要新的明确目标与授权。

- 已交付真实故障实验、类型化证据、确定性评分、离线重评、拒绝复核和冻结身份管理。
- **尚未建立 Kernel 相对 Static Skill 稳定、可重复的总体优势。** 局部开发样本存在正向结果，
  不能外推到生产、未见故障或其他模型。
- Kernel v19 提示在部分场景筛选中改善，但扩大确认未满足采用条件；当前 Kernel 恢复 v18。
  规划器工具目录修复改善了已观测前缀的调用格式，仍未取得足够的端到端采用证据。
- 场景主要是开发集；暂停前缀、不同模型和不同评分身份不能直接拼成准确率排名。
  本项目当前适合作为 Agent 工程研究作品和实验工具，不宣称生产诊断收益或行业基准代表性。

结果与限制见 [RESULTS.md](RESULTS.md)，当前交接入口见 [docs/HANDOVER.md](docs/HANDOVER.md)。

## 工作闭环

```text
健康基线
  → 注入固定事故
  → 执行并验证 dbt 结果
  → 发布不含答案的可观测上下文
  → Agent 调用只读工具调查
  → 重置并验证环境恢复
  → 确定性 evaluator 核验诊断与恢复结果
  → 写入六文件产物与私有重评输入
```

Ground Truth 与公开调查上下文严格分离：实验编排器和 evaluator 可以读取私有
`ScenarioSpec`，Agent 只能看到 `IncidentBrief`、dbt artifacts、受白名单约束的 profile
snapshot，以及本 run 授权的类型化只读工具（v1 六工具，公开证据 v2 可增加两个批量工具）。

## 核心能力

- **可复现事故实验**：在固定的 Jaffle Shop 数据集上执行健康构建、故障注入、结果验证和幂等重置。
- **受限 Agent 调查**：支持 Kernel、Static Skill、实验性 Evidence Planner 与消融策略；
  固定规则和参考分析师用于确定性验证。证据接口不开放 Shell、任意 SQL 或数据库写入。
- **诊断 kernel**：维护假设登记、证据缺口与调用预算；终局经确认、弃答、健康三个类型化
  提交工具交付，资产声明必须使用所引证据中的完整节点标识符；kernel 拒绝附可执行的纠正
  反馈，弃答格的缺口矩阵由 evaluator 按期望矩阵核验。
- **证据绑定诊断**：输出 `CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`NO_INCIDENT` 或
  `MODEL_ERROR`，所有事实主张必须引用系统生成的 evidence ID。
- **确定性评测**：程序化检查根因、影响范围、证据存在性、证据与主张的一致性、策略边界、环境状态
  和最终恢复结果。
- **策略对照基准**：冻结场景、模型、预算、策略和运行 ID，支持 Agent 策略、消融策略与
  `FIXED_RULE` 对照。
- **可审计产物**：每次完整评测生成六文件公开产物，并在独立管理目录保存私有离线重评输入；
  支持版本化拒绝摘要和可判定范围内的离线复核，不保存隐藏思维链或原始 provider 回复。
- **接入与隔离实验**：统一策略协议、MCP 证据接口与确定性容器闭环已实现；进程内协议本身不是
  沙箱，确定性探针通过也不证明真实模型具备抗注入能力。

## 场景目录

仓库提供 26 个可用场景：17 个 P1 目录场景、1 个向后兼容回归场景，以及 8 个后续新增的
扩展场景。带 `_a` / `_b` 的场景构成配对实验：A 变体保留确认根因所需的公开证据，B 变体
隐藏关键事实，期望 Agent 返回证据不足。扩展场景刻意不进入冻结的 Manifest 场景目录，
但同样可以被 `diagnose`、`eval run` 与 `certify` 使用。

| 故障族 | 场景 | 主要验证目标 |
| --- | --- | --- |
| Schema 类型漂移 | `schema_type_change_payment_amount`、`schema_type_change_order_customer_a`、`schema_type_change_order_customer_b` | 识别字段类型变化，并在 Schema 不可见时拒绝确认 |
| 必填字段空值 | `required_null_payment_id`、`required_null_order_customer_a`、`required_null_order_customer_b` | 区分真正的必填字段破坏与无关 nullable 干扰项 |
| 重复支付 | `duplicate_payment_record`、`duplicate_payment_coupon_a`、`duplicate_payment_coupon_b` | 覆盖 dbt 失败和 dbt 成功但业务指纹重复两种路径 |
| 孤立支付 | `orphan_payment_record`、`orphan_payment_coupon_a`、`orphan_payment_coupon_b` | 联合支付、订单历史与 ingestion watermark 判断引用完整性 |
| 静默支付丢失 | `silent_payment_drop_record`、`silent_payment_drop_partition_a`、`silent_payment_drop_partition_b` | 在构建成功时通过跨关系历史事实发现缺失事件 |
| 健康对照 | `order_volume_pattern_a`、`order_volume_within_sla` | 用历史范围、watermark 与 SLA 证据证明无事故 |
| Schema 重命名回归 | `schema_rename_payment_amount` | 验证从 `amount` 到 `total_amount` 的基础诊断闭环 |
| 必填字段空值（扩展） | `required_null_payment_id_distractor_a`、`required_null_payment_id_distractor_b` | 在必填字段破坏旁识别无关可空字段空值干扰 |
| Schema 类型漂移（扩展） | `type_change_payment_amount_drift_a`、`type_change_payment_amount_drift_b` | 在字段类型变更旁识别新增可空列的漂移干扰 |
| Schema 类型漂移（公开证据 v2） | `schema_type_change_raw_customer_id_a`、`schema_type_change_raw_customer_id_b`、`schema_type_change_raw_order_user_id_a`、`schema_type_change_raw_order_user_id_b` | 同一表面症状下定位失败 join 两侧的类型偏差，携带 `observable_evidence.v2` 公开证据合同 |

P1 正式赛程从目录中冻结 12 个场景，形成 106 格（94 个模型格、12 个固定规则格）；独立规划器
比较赛程为三策略 × 12 场景 × 3 次重复，共 108 个模型格。两类实验分别绑定自己的清单。
开发/保留集登记以 `config/scenario-sets.json` 为准，不能因换 seed 就宣称是未见评估。

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
- `planner_agent.py`、`evidence_planner.py`：实验性规划器、工具目录与计划校验。
- `evidence_tools.py`、`profiles.py`、`read_only_db.py`：有界证据读取与只读数据库角色。
- `evaluation.py`、`evaluation_runner.py`：诊断契约和端到端确定性评测。
- `benchmark_manifest.py`、`benchmark_runner.py`、`benchmark_report.py`：正式套件冻结、
  执行、ledger 与汇总。
- `artifacts.py`、`run_context.py`：运行身份、路径边界和六文件原子写入。
- `evaluation_inputs.py`、`evaluation_rescore.py`：私有评分输入归档与离线派生评分。

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
`mimo-v2.5-pro`。`.env.diagnostic` 已被 Git 忽略，密钥不得写入仓库。

使用其他获准端点时，同时配置模型名、端点及 `DIG_DIAGNOSTIC_MODEL_API_KEY`。运行时优先读取
该密钥变量，再回退 `MIMO_API_KEY`；不会自动读取 `COMMANDCODE_API_KEY`。如使用 CommandCode，
须在进程环境中显式映射与该端点配对的密钥，避免沿用 `.env.diagnostic` 中另一供应方的密钥。

运行诊断前可执行环境检查：

```powershell
uv run data-incident-gym doctor
```

`doctor` 检查 Python、uv、Docker、PostgreSQL、dbt profile、聚合证据边界、模型可用性和
最小工具调用能力。它会发起一个模型能力探针；通过只表示运行环境就绪，不代表诊断质量通过。

## 运行事故诊断

以下命令会真实调用配置的模型；它们是使用说明，不代表当前研究阶段默认继续测量：

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

公开证据 v2 场景还可授权 `get_relation_schema_expectation`（健康基线列期望）和
`get_dbt_node_definition`（绑定本次运行的编译定义）两个批量工具。工具是否可用、目标是否可读
由本 run 合同决定；缺失事实显式保留为未知。

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

`.dig/scoring-inputs/<run_id>/` 是包含场景合同快照的私有管理平面，供严格重载和离线重评，
不能发送给模型或当作公共演示包。`eval score <run_id>` 生成独立派生评分，不覆盖原始评分。

## 正式基准与证据边界

`config/benchmark/` 保存 P1 正式赛程和独立规划器实验清单；部分对照臂保存在各自历史分支。
每份清单固定场景、实现修订、模型/端点、预算、策略身份、赛程及结果输入。核验必须使用该身份
对应的代码；当前 main 含后续修订，不保证兼容所有历史清单。CLI 提供不调用模型的漂移检查：

```powershell
# 在相应冻结 checkout 中，将路径替换为待核验的清单。
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v32.json
uv run data-incident-gym experiment verify --manifest config/benchmark/p1-planner-compare-v3.json
```

漂移拒绝不能通过修改旧清单来消除。最早的 `p1-formal-v1` 还存在旧模型名称在当前加载器中
不再被接受的历史兼容性限制。

正式执行还要求干净 checkout、Manifest SHA-256 的显式确认、通过 preflight、独占 suite lock
和 append-only ledger。近期测量采用最近 12 个终态模型格中至少 10 个未通过即暂停的规则，
环境/恢复失败也会停机；正式失败不能被替换或覆盖。报告与 partial 分析不调用模型或数据库，
部分报告路径会离线重算 evaluator。暂停套件应按 partial 口径读取，不能因 `subset=False`
就当作完整报告。选择性探索运行也不能当作完整正式赛程。

随附 Manifest 中 `p1-formal-v1` 的历史执行已封存为 `INVALID_HARNESS`：该批次暴露了
setup 失败物化、恢复传播、fail-stop 和报告适用性判断等 harness 缺陷，因此不能作为真实
模型质量结论，也不得重新运行或重新冻结。`p1-formal-v9` 的正式批次以 `INVALID` 结案（见
根目录 `RESULTS.md`）。后续批次包含完整配对实验和暂停前缀，须分别解释。工程修复不会追溯
改变旧批次的证据含义；批次报告是按当时身份记录的历史材料，当前研究结论以结果总览为入口。

## 资源与安全边界

- 单次诊断最多 8 次模型请求、8 次工具调用、2 次结构化输出重试，总时限 300 秒。
- PostgreSQL 证据读取使用独立的 `dig_reader` 只读角色，并只返回配置允许的聚合事实。
- 场景认证、实验编排、evaluator 和离线审计属于可读取私有合同的管理平面；模型可见消息和公共
  工具面不能包含期望答案。仓库访问权限不等于沙箱隔离，外部策略隔离另有容器验收边界。
- 依赖准备完成后，基线、事故实验、证据读取和确定性评测可离线运行；模型诊断需要访问配置的
  endpoint。
- 项目不接收生产告警，不连接生产数据，不提供 Web UI、自由聊天、多用户协作、自动修复或发布
  操作。

## 验证

根据改动影响选择确定性验证，不必每次运行完整集合：

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
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
