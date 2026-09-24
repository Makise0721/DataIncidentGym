# v29/v30 血缘节点拒绝的历史暴露面（只读审计）

审计日期：2026-09-24。此报告登记已归档运行中 `NODE_ARGUMENT_NOT_PROVEN` 的暴露面；不重跑模型、数据库或 benchmark，不修改历史归档、评分及原报告结论。

## 输入与核对范围

- 清单：`config/benchmark/p1-formal-v29.json`（SHA-256 `138d790c7880fe1ebb7460b29561894df9ab3772acf100c67d3cbba283d7a34b`）、`config/benchmark/p1-formal-v30.json`（`af5299be5903517f4897417aacb60d44011533422e6d91995a967defbb0cb5c9`）。
- 原始 ledger 与六文件位于 `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/artifacts/`。两个 ledger 的 SHA-256 分别为 `6e6254c55f5f713585a4410ca3387118ae64b030f67bb6a1017a83cd9a6575ba`、`1eb214894b02b021ac7101461f9afad5019fe5b325a1dd8430b77b1f07a3b58a`。
- v29 的 13 个终态格、v30 的 18 个终态格，合计 31 格；其中 Kernel 15 格、Static 16 格。逐格核对 ledger 的 sequence、run ID、case ID、策略与清单 cell 相等；`metadata.json` 的 manifest SHA-256 与清单相等；31 份 trace 均存在且序号连续。身份或文件缺失为 0。
- 在上述 31 份 `trace.jsonl` 中，只把 `TOOL_CALL.error_code == NODE_ARGUMENT_NOT_PROVEN` 计为命中。分母是已执行且有终态的格，不是 106 格完整赛程。

## 逐格结果

| 批次 / seq | run ID | 拒绝及后续轨迹 | 终态 |
| --- | --- | --- | --- |
| v29 / 10 | `2c09ca6d45ae9307762ec7a5bb711945` | trace 3：`get_dbt_lineage(seed.jaffle_shop.raw_payments, downstream)` 被拒；之后未再调用证据工具，trace 4–5 为 Kernel 合同拒绝，trace 6 为 provider 协议事件 | `MODEL_ERROR` / `MODEL_PROTOCOL_ERROR`；ledger `FAILED` |
| v30 / 10 | `55435d24ea0aceaa89d0b57c68dc5881` | trace 4：相同节点的血缘调用被拒；之后没有新的证据工具调用，trace 5–7 为 Kernel 合同拒绝，trace 8 接受弃答 | `INSUFFICIENT_EVIDENCE`；ledger `FAILED` |
| v30 / 14 | `f7624949a02df4d5b4b340d69b34e7d0` | trace 4：相同节点的血缘调用被拒；trace 5 改查 `raw_payments` schema 成功，trace 6–8 为 Kernel 合同拒绝，trace 9 接受弃答 | `INSUFFICIENT_EVIDENCE`；ledger `FAILED` |
| v30 / 18 | `7207a8c0ade2f195089333684f9e391d` | trace 6：相同节点的血缘调用被拒；trace 8 改查 `raw_payments` schema 成功，trace 7/9/10 为 Kernel 合同拒绝，trace 11 接受弃答 | `INSUFFICIENT_EVIDENCE`；ledger `FAILED` |

命中为 **4/31 个终态格**，按策略为 Kernel **4/15**、Static **0/16**；每个命中格恰有一次此错误码。两个 v30 格后续调用了其他工具，但四格都没有在拒绝后成功取得同一节点的血缘证据。v30 seq14、18 的 `model_requests` 均为 8，然而最终提交被接受为弃答；不能把“用满请求额度”改写为 `MODEL_REQUEST_LIMIT` 终态。v29 seq10 后续的 provider 错误也不能归因为这次节点拒绝。

该盘点证明四格经过了已登记的拒绝路径，**不证明**若血缘调用获准，原诊断就会通过；后续还存在 Kernel 合同拒绝和其他质量判据。修复后的 v31 与这些旧身份不是单变量对照。本报告只登记暴露面，不回填旧结果，也不推断完整赛程比率。
