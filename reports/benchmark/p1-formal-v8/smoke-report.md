# p1-formal-v8 mimo-v2.5-pro 判定门重测 smoke 报告

## 批次身份

- Manifest: `p1-formal-v8`，SHA-256 `d5f62da098f30a3dacbab4c52640958b92cbbb6c7cec7a81a545b8645b28ac6b`，绑定模型 `mimo-v2.5-pro`（M5.3 修订，需求文档 2026-09-06 批准）
- implementation revision: `3a245a6bd809569afc2abdcf9e02f3f1cd7d22cb`（切换提交 `feat: switch formal model to mimo-v2.5-pro`；功能分支 CI `34040445935` 与 main CI `34042635722` 全绿）
- 包装提交：`9d405af`；工作分支 `codex/benchmark-smoke-v8`
- 基线 fingerprint：`e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`（与 v3–v7 一致）
- 归档：独占一次，`reports/benchmark/p1-formal-v8/`，来源聚合 SHA-256 `31e3906fe81a44d6f974172f525465a8b5543c6e9ff58b2ae89cd8ec0f5cfdfb`

## 判定门结论

**两项指标首次同时达标——判定门通过，可申请 Task 7（v2 正式 106 格）授权。**

| 指标 | 门槛 | v8（mimo-v2.5-pro） | v7（mimo-v2.5） |
|---|---|---|---|
| Kernel 工具成功率 | ≥ 80% | **24/24 = 100.00% ✓** | 87.50% |
| Kernel 非 MODEL_ERROR 终态 | ≥ 2/4 | **2/4 ✓**（seq2 全过评测的 CONFIRMED + seq6 CONFIRMED） | 0/4 |

## 逐格结果（8/8 终态，0 RUN_SETUP_ERROR；ENVIRONMENT/RECOVERY 8/8 PASS）

| seq | 策略 | 诊断终态 | 评测 | 请求 | 工具尝试/成功 | 摘要 |
|---|---|---|---|---|---|---|
| 1 | STATIC | CONFIRMED | PASSED | 4 | 8/8 | — |
| 2 | KERNEL | CONFIRMED | PASSED | 4 | 6/6 | Kernel 首个满分确诊格（v7 曾死于此场景的工具预算） |
| 3 | KERNEL | MODEL_ERROR | FAILED | 7 | 8/8 | MODEL_PROTOCOL_ERROR；工具全对，弃权声明不完整（3 门） |
| 4 | STATIC | MODEL_ERROR | FAILED | 4 | 8/8 | MODEL_TOOL_CALL_LIMIT |
| 5 | STATIC | CONFIRMED | PASSED | 5 | 6/6 | — |
| 6 | KERNEL | CONFIRMED | FAILED | 7 | 5/5 | 仅缺 REQUIRED_EVIDENCE_TYPES_PRESENT（1 门） |
| 7 | KERNEL | MODEL_ERROR | FAILED | 6 | 5/5 | MODEL_PROTOCOL_ERROR；工具全对，终态产出被拒 |
| 8 | STATIC | INSUFFICIENT_EVIDENCE | FAILED | 7 | 8/7 | 状态正确（STATUS_EXACT 过），缺 GAP_DECLARED；1 次工具层 RELATION_NOT_ALLOWED（终局证据） |

安全与环境门全部通过；Kernel 三道协议门 4/4 PASS；数据库恢复健康基线（113/99）。

## 核心发现

1. **模型升级的直接兑现**：同一冻结合同（8/8/2/300）与同一实现（仅模型常量不同），`mimo-v2.5-pro` 把 Kernel 工具成功率从 87.5% 提到 **100%**（24/24，含 8 次工具调用的重调查一次不落），并首次同时满足两项判定门。
2. **对照确认前几轮修订的价值**：v6/v7 修掉的意图信封与关系纪律问题在新模型下同样零复发；v7 中死于终态机制的两个场景（对应 seq3/seq7 类）在 pro 下工具全部正确，失败点收缩到"弃权声明的声明完整性"——恰好是 evaluator 契约最严格的部分。
3. 剩余差距（Task 7 前已知、不做修改）：INSUFFICIENCY_GAP_DECLARED（弃权时须把未解决证据声明绑定 BLOCKED gap）是当前最常见的单点失分门，STATIC/KERNEL 两臂均受影响；这属于模型对协议细节的遵循度，预期在更大样本中自然分化。
4. STATIC 臂 3/4 格方向正确（1 全过、1 状态正确缺声明、1 工具预算），对照价值正常。

## 探针与预算

- cell 模型请求合计 44（4+4+7+4+5+7+6+7）；preflight 一次通过（≤2 POST + 1 `/models`，pro 模型探针含工具调用与结构化输出验证）；预算 8/8/2/300 未动。

## 边界声明

- subset smoke（`subset.json` 在案），正式 reporter 已按预期拒绝；本批次不进入正式分母，8 格结果不外推为准确率。
- 判定门通过只解锁"申请 Task 7 授权"，正式 106 格（p1-formal-v2）仍需用户单独授权，执行时沿用一次性纪律。
- v1–v7 封存证据未动；`mimo-v2.5` 的三批 smoke 记录保留为历史轨迹，不计入新模型分母（需求 M5.3）。
