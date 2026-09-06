# p1-formal-v5 Kernel 契约修订 smoke 报告

## 批次身份

- Manifest: `p1-formal-v5`，SHA-256 `e0b615f4eefb84a1872437359317c2d9015023cb83154aa5b9c477e31c3d4ee4`
- implementation revision: `bdecd21089ac0eb8e6931baea1fcadbb01f3cad0`（分支 CI run `34006120877` 与默认分支 CI run `34008232005` 均全绿）
- 包装提交：`6a86f01`（仅含 v5 Manifest）；工作分支 `codex/benchmark-smoke-v5`
- 基线 fingerprint：`e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`（与 v3/v4 一致）
- 模型：`mimo-v2.5` @ `https://api.xiaomimimo.com/v1`（Manifest 绑定）；预算 8/8/2/300 未变
- 归档：独占一次，`reports/benchmark/p1-formal-v5/`，来源聚合 SHA-256 `23e00f282ae1cae663a1398b44019c4a50024f0e787f0d2a8bdf4ded566efd7f`

## 判定门结论

**未达标，按计划不进入 Task 7（v2 正式 106 格）。**

| 指标 | 门槛 | v5 实测 | v4 基线 |
|---|---|---|---|
| Kernel 工具成功率 | ≥ 80% | 21/28 = **75.00%** | 68.97% |
| Kernel 非 MODEL_ERROR 终态 | ≥ 2/4 | **1/4**（seq6 CONFIRMED） | 0/4 |

## 逐格结果（8/8 终态，0 RUN_SETUP_ERROR）

| seq | 策略 | 诊断终态 | 评测 | 模型请求 | 工具尝试/成功 | 终态原因 |
|---|---|---|---|---|---|---|
| 1 | STATIC | MODEL_ERROR | FAILED | 3 | 7/7 | MODEL_TOOL_CALL_LIMIT |
| 2 | KERNEL | MODEL_ERROR | FAILED | 8 | 7/5 | MODEL_REQUEST_LIMIT |
| 3 | KERNEL | MODEL_ERROR | FAILED | 7 | 7/6 | MODEL_PROTOCOL_ERROR |
| 4 | STATIC | MODEL_ERROR | FAILED | 4 | 8/8 | MODEL_TOOL_CALL_LIMIT |
| 5 | STATIC | CONFIRMED | PASSED | 4 | 8/8 | — |
| 6 | KERNEL | CONFIRMED | PASSED | 6 | 6/6 | — |
| 7 | KERNEL | MODEL_ERROR | FAILED | 8 | 8/4 | MODEL_PROTOCOL_ERROR |
| 8 | STATIC | MODEL_ERROR | FAILED | 4 | 8/7 | MODEL_TOOL_CALL_LIMIT |

安全与环境门：`ENVIRONMENT_VERIFIED` 8/8 PASS、`RECOVERY_HEALTHY` 8/8 PASS、`TOOL_ALLOWLIST_EXACT`/`TRACE_READ_ONLY_SAFE` 无失败；4 个 Kernel 格的 `KERNEL_STATE_VALID`/`KERNEL_HYPOTHESIS_GATE`/`KERNEL_EVIDENCE_GAP_GATE` 全部 PASS。评测失败均为诊断质量结果，不是 harness 缺陷。套件结束后数据库恢复健康基线（raw_payments 113、orders 99）。

## 与 v4 对比的核心发现

1. **意图信封传输层缺陷已消除**：v4 全部 4 个 Kernel 格的预算被 `KERNEL_INTENT_INVALID`/`KERNEL_INTENT_SHAPE_INVALID` 重试烧穿（最多 5/8 请求）；v5 中该错误码为 0。绑定参数化传输按设计工作。
2. **首次出现 Kernel 真实模型 CONFIRMED**（seq6：required_null_order_customer_a，6 请求、6/6 工具、全部评测门通过）——v4 中 Kernel 真实模型格 0/4 到达终态。
3. **剩余失败模式转移为调查纪律**：Kernel 工具失败全部为 `RELATION_ARGUMENT_NOT_PROVEN`（5 次）与 `RELATION_NOT_ALLOWED`（2 次）——模型查询未经证据证明或超出场景可观测范围的关系，重试烧穿预算后 2 格以 `MODEL_PROTOCOL_ERROR` 终态。提示词 v6 已含对应纪律条款但模型未遵守。
4. **STATIC 对照臂出现计划外方差**：3/4 Static 格以 `MODEL_TOOL_CALL_LIMIT` 终态（v4 为 0）。Static 提示词与工具 schema 逐字节未变（策略公平测试钉住），属于模型采样方差下的多调用打包行为变化；该臂仅作对照，不计入 Kernel 判定。
5. 工具成功率 68.97% → 75.00%，改善幅度不足以跨越 80% 门槛；一次性 8 格样本对方差的敏感度有限，任何结论都不外推。（初稿误算 21/29=72.41%，独立证据审计复核 artifacts 后更正为 21/28=75.00%；门槛结论不变。）

## 探针与预算核对

- 8 格 cell 模型请求合计 44（3+8+7+4+4+6+8+4），另有 STATIC/KERNEL 无额外探针。
- Preflight 共 4 次尝试（详见下节），每次最多 2 次模型探针 POST + 1 次 `/models`；合计上限 8 POST + 4 `/models`，探针不计入 cell 分母。cell 预算 8/8/2/300 未放宽。

## Preflight 环境异常记录（3 次失败均已保全 receipt）

1. attempt 1（`.dig/kernel-v6/doctor.preflight-attempt1-race.json`）：PostgreSQL 容器在工具调用间隙被宿主侧干净关闭（`Exited(0)`、非 OOM），compose/连接族检查失败。模型三项探针在该次已通过。
2. attempt 2（`...attempt2-race.json`）：容器已恢复健康，但 `POSTGRES_CONNECTION`/`DBT_PROFILE_CONNECTION`/`PROFILE_READ_ONLY` 仍失败。
3. attempt 3（`...attempt3-missing-envfile.json`）：定位根因为 `doctor.py` 的 `_has_explicit_diagnostic_database_config()` 安全门——worktree 缺少显式 `.env.diagnostic` 时连接检查按需求 §6 设计固定返回不可用。按 v3/v4 同款授权先例复制 Windows 侧 `.env.diagnostic`（mode 600）后 attempt 4 **PASSED（13/13）**。
4. 三次失败均为环境/配置问题，零 cell 启动（`started_cells: 0`），未消耗任何样本；receipt 原样保全于 ignored 目录。

## 边界声明

- 本 smoke 是 subset（`subset.json` 在案），正式 reporter 已按预期拒绝（`subset suites cannot produce a formal report`）。
- 本批次不进入任何正式分母，不构成模型质量结论，不得外推为准确率或策略优越性。
- 封存的 v1/v3/v4 证据未做任何改动；v2 身份仍未冻结。
- 后续路径由计划判定门约束：50–79% 区间要求由用户决定是否再立契约修订；不重跑本批次，不补位、不替换样本。
