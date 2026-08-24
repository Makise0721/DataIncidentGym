# DataIncidentGym 需求文档

> 状态：已批准
> 版本：0.1
> 日期：2026-08-24
> 批准记录：用户于 2026-08-24 明确批准本需求基线。
> 当前约束：本文件定义 P0 基本原型及后续阶段边界；实施计划批准前不开始实现。

## 1. 产品摘要

DataIncidentGym 是一个面向数据工程师和值班开发者的、本地单用户使用的可复现数据管道故障诊断项目。它由两部分组成：

1. **Incident Lab**：能够重置数据、注入已知故障、保存标准答案并验证最终环境状态的实验环境。
2. **Diagnosis Agent**：通过受限只读工具调查 dbt 运行、PostgreSQL Schema 和 dbt 血缘，输出可引用证据的结构化诊断。

P0 不追求完整的数据可观测平台，而是证明一个最小闭环：

```text
健康数据管道
  → 确定性故障注入
  → dbt 稳定失败
  → Agent 自主选择只读工具调查
  → 输出根因、影响范围、证据和建议
  → 程序化验证诊断
  → 一条命令重置并再次重放
```

## 2. 问题与目标用户

### 2.1 问题

数据管道失败时，工程师通常需要在运行结果、日志、数据库 Schema 和模型血缘之间手工切换。监控系统可以报告“什么失败了”，但往往不能把这些分散事实组合成有证据支持的根因和下游影响范围。

### 2.2 P0 目标用户

P0 的唯一目标用户是：

> 在本地调查 dbt 数据管道失败的数据工程师或值班开发者。

### 2.3 P0 使用方式

- 单机、单用户 CLI。
- 用户主动选择案例或运行 ID 并启动诊断。
- 不接收线上告警，不连接真实生产环境。
- 不提供自由聊天入口。
- 不提供账号、角色、团队协作或通知能力。

## 3. 产品目标与非目标

### 3.1 P0 目标

1. 使用真实 PostgreSQL 和 dbt 模型复现一个确定性失败。
2. 让 Agent 仅通过类型化只读工具获得事实，不直接访问 Shell 或任意文件。
3. 要求每项诊断结论引用系统生成的证据 ID。
4. 使用确定性验证器判断根因、影响范围和证据是否正确。
5. 同时支持无需模型的自动测试和 Ollama 本地模型真实演示。
6. 在 Windows 11 / PowerShell 7 本地可运行，并在 Ubuntu CI 验证第二平台。
7. 在准备好依赖、镜像和模型后，运行时完全离线。

### 3.2 P0 非目标

- 多 Agent 协作。
- Airflow、OpenLineage 或 Marquez 集成。
- 向量数据库、embedding 或 RAG。
- Web UI、聊天界面或交互式追问。
- 自动修改 dbt SQL、数据库 Schema 或项目文件。
- 自动重跑任务或执行任何修复。
- 线上告警接入、生产部署或真实企业数据。
- 以另一个 LLM 作为主要正确性裁判。

## 4. 术语

| 术语 | 定义 |
|---|---|
| Incident Case | 一个可重置的故障案例，包含注入方式、标准答案和验收规则。 |
| Healthy Baseline | 故障注入前能够通过 `dbt build` 的固定数据与模型状态。 |
| Fault Injector | 只由 Incident Lab 使用的确定性写操作，用于制造已知故障。 |
| Run | 一次健康构建、故障构建、诊断或评测执行。 |
| EvidenceRecord | 工具返回的可审计事实，带唯一 ID、来源、类型和内容摘要。 |
| Diagnosis | Agent 的结构化最终输出，包括状态、根因、影响范围、证据引用和建议。 |
| Ground Truth | 案例预先定义的正确根因、受影响节点及必需证据类型。 |
| Blast Radius | 由故障直接或间接影响的 dbt 模型集合。 |

## 5. 开源复用与许可证策略

### 5.1 项目许可证

DataIncidentGym 使用 Apache License 2.0。

### 5.2 P0 数据模型基线

P0 通过固定 Git submodule 复用下列 Apache-2.0 项目，并在本项目外围提供 PostgreSQL 适配：

- 上游：[dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb)
- 固定分支：`duckdb`
- 固定 commit：`36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`
- submodule 路径：`third_party/jaffle_shop`。
- 复用范围：submodule 内的 `seeds/`、核心 `models/`、相关 Schema/Test 定义和上游许可证。
- 修改范围：本项目仅增加外部 PostgreSQL profile、外围运行逻辑和测试；不得直接修改 submodule 中的上游文件。

仓库必须提供 `THIRD_PARTY_NOTICES.md`，记录来源、固定 commit、许可证、submodule 路径和适配方式。克隆后的准备流程必须明确执行 `git submodule update --init --recursive`。第三方文件继续保留其原始历史、版权声明和许可证。

### 5.3 后续阶段参考

| 项目 | 许可证 | 使用阶段 | 使用方式 |
|---|---|---|---|
| [jaffle-shop-generator](https://github.com/dbt-labs/jaffle-shop-generator) commit `01c0e8370f1855a86b740fc2e7c4b910cc7f52b8` | Apache-2.0 | P1 | 生成更大规模、带趋势的虚构数据。 |
| [Correlator Demo](https://github.com/correlator-io/correlator-demo) commit `ce35640e10ddd76eee4b59bf5eae6d60935144e0` | Apache-2.0 | P2 | 参考 Jaffle Shop、Airflow、dbt、数据质量和 OpenLineage 的组合方式。 |
| [OpenLineage Airflow Quickstart](https://openlineage.io/docs/next/guides/airflow-quickstart/) | 官方文档 | P2 | 参考 Schema 改名故障、Airflow 事件和 Marquez 排障流程。 |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | MIT | P0 | 复用 Agent、Ollama Provider、类型化工具、结构化输出和 TestModel。 |

未识别出明确许可证的仓库只允许作为设计参考，不复制其源码或数据。依赖版本在实现阶段依据真实兼容性测试写入 `uv.lock`；不得使用未锁定依赖完成正式验收。

## 6. P0 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                       Python CLI                            │
│ doctor | lab reset/inject | pipeline build | diagnose | eval│
└───────────────┬───────────────────────────────┬─────────────┘
                │                               │
       管理平面（允许写）                诊断平面（严格只读）
                │                               │
       Incident Lab / dbt              PydanticAI Agent
                │                               │
       PostgreSQL + dbt artifacts       Typed Evidence Tools
                │                               │
       Ground Truth / Reset              EvidenceRecord
                └──────────────┬────────────────┘
                               │
                    Deterministic Evaluator
                               │
                    JSON / Markdown / JSONL
```

管理平面与诊断平面必须使用不同的数据库权限和配置。Agent 工具不得获得管理连接串、Fault Injector 或 Shell 执行能力。

## 7. P0 模块与闭环要求

前一模块未通过验收时，不开始依赖它的后一模块。

### 7.1 M1：数据模型闭环

**职责**

- 启动固定版本 PostgreSQL 容器。
- 载入经过归属记录的 Jaffle Shop seeds。
- 运行适配 PostgreSQL 的 dbt 模型和测试。
- 生成 `manifest.json`、`run_results.json` 和 dbt 日志。

**完成定义**

- 全新环境执行一次命令后，`dbt build` 成功。
- `raw_customers`、`raw_orders`、`raw_payments` 及下游模型存在。
- dbt tests 全部通过。
- 同一固定 seed 重置 10 次产生相同的表结构和行数摘要。

### 7.2 M2：故障实验室闭环

**职责**

- 将健康基线恢复为已知状态。
- 注入首个字段改名故障。
- 运行 dbt 并保存失败产物。
- 提供机器可读 Ground Truth。

**完成定义**

- `reset → inject → build` 连续 10 次产生相同失败节点、错误类别和 Schema 状态。
- `reset → build` 能恢复健康状态。
- 故障注入不修改第三方模型源码。
- Ground Truth 能被独立验证器读取。

### 7.3 M3：证据工具闭环

P0 仅暴露以下只读工具：

1. `get_dbt_run_results(run_id)`：读取运行状态、失败节点和跳过节点。
2. `get_dbt_node_error(run_id, node_id)`：读取指定节点的规范化错误事实。
3. `get_relation_schema(relation_name)`：通过只读 PostgreSQL 连接读取列名和类型。
4. `get_dbt_lineage(node_id, direction)`：从固定运行的 `manifest.json` 查询上下游节点。

**完成定义**

- 每个工具都有独立单元测试和真实产物集成测试。
- 每次调用返回一个或多个 `EvidenceRecord`。
- 输入非法 run/node/relation 时返回类型化错误，不返回伪造空结果。
- 工具没有写数据库、执行 Shell、访问网络或读取任意路径的能力。

### 7.4 M4：Agent 闭环

**职责**

- 使用 PydanticAI 定义单 Agent。
- 通过统一 OpenAI-compatible 模型配置工作。
- 开发和本地演示优先使用 Ollama `gemma4:e4b`。
- 测试使用 PydanticAI `TestModel` 或等价的框架内测试模型。
- 输出固定 `Diagnosis` Schema。

**完成定义**

- TestModel 能覆盖工具注册、调用、输出校验和错误路径。
- `gemma4:e4b` 真实调用能够访问工具并生成结构化输出。
- Agent 不获得自由 Shell、文件系统、网络或写数据库工具。
- 证据不足时返回 `INSUFFICIENT_EVIDENCE`。

### 7.5 M5：评测与报告闭环

**职责**

- 一次命令完成重置、故障注入、失败构建、诊断和验收。
- 以 Ground Truth 和环境事实程序化验证输出。
- 保存机器报告、人工报告和可审计轨迹。

**完成定义**

- TestModel 自动化测试 100% 通过。
- `gemma4:e4b` 在相同案例上独立运行 3 次，至少 2 次完整通过。
- 失败运行同样保存，不从统计中排除。
- 不以另一个 LLM 的主观评分作为主要判据。

## 8. P0 数据模型与首个案例

### 8.1 逻辑模型

沿用 Jaffle Shop 的核心模型：

```text
raw_customers ─→ stg_customers ───────────────→ customers
raw_orders    ─→ stg_orders    ─→ orders ─────→ customers
raw_payments  ─→ stg_payments ─→ orders
                              └───────────────→ customers
```

### 8.2 案例标识

`schema_rename_payment_amount`

### 8.3 健康状态

`raw_payments` 至少包含：

```text
id
order_id
payment_method
amount
```

`stg_payments` 读取 `amount` 并将其从分转换为金额单位。`orders` 和 `customers` 均依赖 `stg_payments` 的金额字段。

### 8.4 故障注入

Incident Lab 使用管理连接执行语义等价于下列操作的确定性变更：

```sql
ALTER TABLE raw_payments
RENAME COLUMN amount TO total_amount;
```

dbt 模型保持不变。

### 8.5 Ground Truth

```text
root_cause_code：SOURCE_SCHEMA_COLUMN_RENAMED
direct_failure：stg_payments
affected_assets：stg_payments、orders、customers
required_evidence_types：
  - DBT_NODE_ERROR
  - RELATION_SCHEMA
  - DBT_LINEAGE
```

正确诊断必须证明：

1. dbt 错误指向 `stg_payments` 对 `amount` 的读取。
2. 当前 `raw_payments` 不包含 `amount`，但包含 `total_amount`。
3. dbt manifest 显示 `orders` 和 `customers` 位于该节点下游。

## 9. CLI 需求

P0 的主入口为：

```text
data-incident-gym
```

必须提供：

```powershell
uv run data-incident-gym doctor
uv run data-incident-gym lab reset schema_rename_payment_amount
uv run data-incident-gym lab inject schema_rename_payment_amount
uv run data-incident-gym pipeline build
uv run data-incident-gym diagnose schema_rename_payment_amount
uv run data-incident-gym eval run schema_rename_payment_amount
```

要求：

- CLI 帮助和人工消息使用中文。
- JSON 字段、文件名、错误码和代码标识使用英文。
- 命令失败必须使用非零退出码。
- `eval run` 是完整闭环的一键入口。
- P0 不接受自由文本问题。

## 10. 模型与 Agent 要求

### 10.1 模型接口

- 统一使用 OpenAI-compatible 接口。
- 默认本地 Base URL：`http://127.0.0.1:11434/v1`，允许通过 `DIG_` 前缀环境变量覆盖。
- 默认开发模型：`gemma4:e4b`。
- 模型适配层不得绑定 Ollama 私有调用方式。
- P0 不使用 embedding 模型。

### 10.2 测试模式

- CI 和常规单元测试不得要求模型密钥或 Ollama。
- 优先使用 PydanticAI 自带 TestModel，不自建通用 fake model 框架。

### 10.3 兼容失败规则

真实验证必须先证明 `gemma4:e4b` 能完成工具调用和结构化输出。若连续 3 次可复现实验仍失败：

1. 停止添加正则解析、JSON 修补或模型专属循环。
2. 保存完整失败证据。
3. 保留 `gemma4:e4b` 为首选配置但标记为未通过验证。
4. 切换其他 Ollama 模型前再次获得用户确认。

### 10.4 单次诊断预算

```text
模型请求上限：6
工具调用上限：8
结构化输出校验重试：2
总超时：180 秒
```

超过限制返回 `MODEL_ERROR` 并保存轨迹。P0 记录 token、耗时和工具调用次数，但不设置固定延迟完成门槛。

## 11. 数据契约

### 11.1 EvidenceRecord

每条工具事实至少包含：

```text
evidence_id       运行内唯一且稳定
evidence_type     枚举类型
source            dbt artifact、dbt log 或 PostgreSQL catalog
subject           被观察的节点、关系或运行
observed_at       观察时间
content           规范化事实
content_digest    规范化内容摘要
```

工具不得把“未找到”伪装成正常空证据；未找到必须返回类型化工具错误。

### 11.2 Diagnosis

最终输出至少包含：

```text
status
incident_case_id
run_id
root_cause_code
summary
affected_assets[]
evidence_ids[]
recommended_actions[]
confidence（展示用途，不参与主要评分）
```

`status` 仅允许：

- `CONFIRMED`
- `INSUFFICIENT_EVIDENCE`
- `MODEL_ERROR`

当 `status=CONFIRMED` 时，根因和影响范围必须引用存在的证据 ID。当证据不足时必须返回 `INSUFFICIENT_EVIDENCE`，不得猜测。

## 12. 评测要求

### 12.1 P0 验收门槛

1. 环境重置与故障注入连续 10 次结果一致。
2. TestModel 自动化测试 100% 通过。
3. `gemma4:e4b` 独立运行 3 次，至少 2 次完整诊断正确。
4. 正确诊断必须同时满足：
   - `root_cause_code` 精确匹配。
   - 受影响模型集合精确匹配。
   - 所有 `evidence_id` 真实存在。
   - 包含 dbt 错误、Schema 差异和血缘三类必需证据。
   - 输出通过 Pydantic Schema 校验。
5. 不发生任何 Agent 写操作。
6. 所有失败运行纳入统计并保留轨迹。

### 12.2 P0 结论边界

P0 的单个案例只证明工程闭环。不得从单个案例宣称系统具有普遍准确率、优于其他方法或达到生产水平。

### 12.3 P1 后的比较

至少完成 5 类故障、每类多个变体后，才比较：

- 无工具的单次模型回答。
- 固定规则诊断。
- 完整工具调用 Agent。
- 移除血缘工具或 Schema 工具的消融版本。

简历中的 Accuracy、F1 和提升比例只能引用这套完整评测的实际结果。

## 13. 运行产物与可观测性

每次诊断生成：

```text
artifacts/<run_id>/
├── metadata.json
├── trace.jsonl
├── diagnosis.json
└── report.md
```

- `metadata.json`：案例、代码版本、模型、配置摘要、开始与结束时间。
- `trace.jsonl`：工具调用、参数、工具结果引用、耗时和错误。
- `diagnosis.json`：最终结构化输出。
- `report.md`：由结构化输出模板化生成的中文报告。

不得保存或展示模型隐藏推理。允许记录工具选择、工具参数、可验证结果、最终结论、token、耗时和重试信息。

`artifacts/` 默认不提交 Git。固定 Ground Truth、预期证据 ID 规则及脱敏示例报告可以提交。提示词受版本控制，运行元数据记录其版本或内容 hash。

## 14. 安全与权限

1. Fault Injector 和 reset 使用仅属于管理平面的数据库连接。
2. Agent 工具使用单独的 PostgreSQL 只读角色。
3. 诊断进程不得读取管理连接串。
4. Agent 不暴露 Shell、任意文件读取、网络访问、数据库写入或源码修改工具。
5. 路径读取限制在当前 run 的已知 dbt artifacts 和日志目录。
6. P0 建议动作仅为文本，不触发执行。
7. 所有数据均为虚构数据。

## 15. 非功能要求

### 15.1 可复现性

- 相同 seed、相同案例和相同版本必须产生相同环境状态和 Ground Truth。
- 环境确定性与模型输出确定性分开度量。
- 每个案例必须支持一条命令重置和完整重放。

### 15.2 离线运行

依赖、Docker 镜像和 Ollama 模型准备完成后，P0 必须在无互联网环境中完成重置、构建、诊断和评测。运行时不发送遥测、日志、Schema 或输出到外部服务。

### 15.3 跨平台

- 本地硬要求：Windows 11 + PowerShell 7。
- 不以 Bash、Makefile 或 Unix 路径作为必需入口。
- Docker Compose 仅负责 PostgreSQL。
- 核心流程通过跨平台 Python CLI 驱动。
- GitHub Actions Ubuntu 作为第二平台验证。

### 15.4 工具链

```text
Python 3.12
uv + uv.lock
PostgreSQL Docker image
dbt-core + dbt-postgres
PydanticAI
Typer
pydantic-settings
pytest + pytest-asyncio
Ruff
Jinja2
```

版本必须锁定，但以实现阶段真实兼容性测试为依据，不在需求阶段任意指定。

### 15.5 语言

- Python、SQL、文件名、JSON 字段和错误码使用英文。
- 需求文档、CLI 帮助和诊断报告使用中文。
- dbt 上游模型名保持英文。
- Agent 系统提示词优先使用英文。
- 用户报告由结构化结果模板化为中文。
- P0 完成后 README 提供中英双语摘要。

## 16. doctor 要求

`doctor` 必须以只读方式检查并明确报告：

- Python 与 uv 可用性。
- Docker/Compose 可用性。
- PostgreSQL 容器和连接。
- dbt 安装、profile 和数据库连接。
- Ollama 服务可达性。
- `gemma4:e4b` 是否存在。
- 模型工具调用和结构化输出最小探针结果。
- 失败项对应的修复建议。

doctor 的成功不能替代完整 P0 评测；它只证明依赖和最小能力就绪。

## 17. 增量路线

### P0：基本原型

依次完成 M1 至 M5，不并行扩展功能。

### P1：增加故障类型

每次只新增一个“注入器 → Ground Truth → Agent 调查 → 自动评测”的完整案例：

1. 字段类型变化。
2. 必填字段空值。
3. 重复支付记录。
4. 孤立支付记录。
5. 静默行数下降。

### P2：真实编排与血缘平台

- Airflow。
- OpenLineage。
- Marquez。
- 跨任务运行历史和变更时间线。

P2 优先参考 Correlator Demo 与 OpenLineage 官方教程，不复制许可证不清晰的实现。

### P3：受控操作与安全性

- 人工批准后重跑。
- 暂停与恢复。
- 间接提示注入测试。
- 权限和副作用审计。

只有 P3 出现明确的持久化、暂停和恢复需求时，才重新评估 LangGraph Functional API；P0 不引入 LangGraph。

## 18. P0 总验收清单

- [ ] Apache-2.0 项目许可证和第三方归属完整。
- [ ] Windows PowerShell 从全新环境说明可启动 PostgreSQL。
- [ ] 健康 Jaffle Shop PostgreSQL 适配版 `dbt build` 通过。
- [ ] 首个案例可重复注入、稳定失败并完整重置。
- [ ] 四个只读证据工具分别通过测试。
- [ ] PydanticAI TestModel 流程通过。
- [ ] Ollama `gemma4:e4b` 完成真实工具调用和结构化输出。
- [ ] 单次运行预算被强制执行。
- [ ] Agent 证据不足时明确拒答。
- [ ] 确定性验证器拒绝错误根因、错误影响范围和不存在的证据。
- [ ] TestModel 测试 100% 通过。
- [ ] 真实模型 3 次中至少 2 次通过。
- [ ] JSON、Markdown 和 JSONL 产物齐全。
- [ ] 未保存隐藏推理，未发生 Agent 写操作或外部网络访问。
- [ ] P0 结果未被误述为通用准确率结论。

## 19. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 本地小模型不能稳定调用工具 | 先运行最小能力探针；连续 3 次失败后停止补丁堆叠并请求模型决策。 |
| dbt 上游示例更新导致漂移 | 固定上游 commit，保留许可证和变更记录。 |
| PostgreSQL 与 DuckDB SQL 差异 | 只做最小方言适配，并以健康 `dbt build` 测试证明。 |
| Agent 猜测或引用不存在证据 | EvidenceRecord、结构化引用和确定性验证器共同拒绝。 |
| 管理连接泄漏给 Agent | 管理平面与诊断平面使用不同配置和数据库角色。 |
| 原型被基础设施拖累 | P0 不接 Airflow、OpenLineage、Marquez 或 Web UI。 |
| 单案例结果被过度宣传 | P0 明确只证明闭环，P1 后才报告比较指标。 |

## 20. 需求变更规则

- P0 范围、完成定义、非目标或安全边界发生变化时，必须先修改并重新批准本文件。
- 不得以“顺便实现”为由把 P1–P3 功能提前塞入 P0。
- 需求文档批准后，再单独编写带确切文件路径、测试和命令的实施计划。
- 实施计划批准后才开始代码开发。
