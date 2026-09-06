# p1-formal-v6 Kernel v7 provenance 修订 smoke 报告

## 批次身份

- Manifest: `p1-formal-v6`，SHA-256 `5fee65718dd3169d581bbc33225ddf2d4bdef94bd55ae3474b817ab4e03d0b87`
- implementation revision: `4ac97797c57925e2c91f885e6201b6da623e5b39`（分支 CI `34015314640` 与 main CI `34017422530` 全绿）
- 包装提交：`c3c1788`；工作分支 `codex/benchmark-smoke-v6`
- 基线 fingerprint：`e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`（与 v3/v4/v5 一致）
- 归档：独占一次，`reports/benchmark/p1-formal-v6/`，来源聚合 SHA-256 `4c96f7b57640055d89e1892b7e158832b98e5a08269f1f703ec79251f5c7907e`

## 判定门结论

**未同时达标，按计划不进入 Task 7。**

| 指标 | 门槛 | v6 实测 | v5 基线 |
|---|---|---|---|
| Kernel 工具成功率 | ≥ 80% | 18/27 = **66.67%** | 75.00% |
| Kernel 非 MODEL_ERROR 终态 | ≥ 2/4 | **2/4（首次达标）** | 1/4 |

## 逐格结果（8/8 终态，0 RUN_SETUP_ERROR；ENVIRONMENT/RECOVERY 8/8 PASS）

| seq | 策略 | 诊断终态 | 评测 | 请求 | 工具尝试/成功 | 摘要 |
|---|---|---|---|---|---|---|
| 1 | STATIC | CONFIRMED | PASSED | 8 | 12/5 | customers 模型故障正确确诊 |
| 2 | KERNEL | MODEL_ERROR | FAILED | 6 | 8/5 | MODEL_TOOL_CALL_LIMIT |
| 3 | KERNEL | MODEL_ERROR | FAILED | 8 | 8/5 | MODEL_REQUEST_LIMIT |
| 4 | STATIC | MODEL_ERROR | FAILED | 4 | 6/5 | MODEL_TOOL_CALL_LIMIT |
| 5 | STATIC | CONFIRMED | PASSED | 4 | 8/8 | — |
| 6 | KERNEL | CONFIRMED | FAILED | 5 | 4/4 | 仅缺 REQUIRED_EVIDENCE_TYPES_PRESENT |
| 7 | KERNEL | INSUFFICIENT_EVIDENCE | FAILED | 8 | 7/4 | 弃权状态正确，缺 GAP_DECLARED |
| 8 | STATIC | MODEL_ERROR | FAILED | 3 | 8/7 | MODEL_TOOL_CALL_LIMIT |

Kernel 三道协议门 4/4 全 PASS；安全门无失败；数据库恢复健康基线。

## 核心发现

1. **判定门一升一降**：终态格 1/4→2/4（首次达标），但工具成功率 75%→66.67%（RELATION_NOT_ALLOWED 从 2 次涨到 9 次）。
2. **发现确定性产品合同错位（本轮最高价值产出）**：kernel 的 provenance 门接受"证据派生关系名"（lineage related_nodes 等），但证据工具层按场景证据合同执行更窄的 allowlist——被拒关系全部为 lineage 派生（seq2: customers/raw_customers/raw_payments 经 history 工具；seq3: stg_customers/stg_orders/stg_payments 经 schema；seq7: raw_orders/orders）。v7 注入的 provable 白名单以 kernel 门为源，因此反而放大了对工具层禁区的暴露。这是确定性合同错位，不是模型方差。
3. **Kernel 质量距离门槛一步之遥**：seq6 状态/根因/影响全对，仅缺一类必需证据；seq7 弃权方向正确但未按协议把未解决证据声明绑定 BLOCKED gap（INSUFFICIENCY_GAP_DECLARED）。
4. STATIC 对照臂 2/4 PASSED（seq1 首次正确确诊 customer_a），另 2 格 MODEL_TOOL_CALL_LIMIT——继续呈现多调用打包下的工具预算压力（模型方差）。

## 探针与预算

- cell 模型请求合计 46（8+6+8+4+4+5+8+3）；preflight 一次通过（≤2 POST + 1 `/models`），探针不计入 cell 分母；预算 8/8/2/300 未动。

## 边界声明

- subset smoke（`subset.json` 在案），正式 reporter 已按预期拒绝；不进入任何正式分母，不构成模型质量结论。
- v1–v5 封存证据未动；v2 身份仍未冻结。
- 后续候选方向（超出本计划范围，需用户决策）：将 kernel provenance 白名单的数据源与证据工具层 allowlist 对齐（确定性产品修复），或就此收尾。
