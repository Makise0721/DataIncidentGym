# v29/v30 `NODE_ARGUMENT_NOT_PROVEN` 暴露面审计（2026-09-24）

## 结论

在可用的 v29/v30 归档副本中，逐一检查了 **31 个终态 run**：v29 为 13/13，v30 为 18/18。所有副本均通过 manifest、ledger、metadata、run_id、sequence 与 trace 终态的身份核对。共 **4/31 个 run** 出现 `NODE_ARGUMENT_NOT_PROVEN`：v29 **1/13**，v30 **3/18**。每个暴露 run 只出现一次。

其中 2/4 个 run 在拒绝后改走 `get_relation_schema`，随后模型请求用量达到 8/8；另 2/4 个 run 没有后续工具调用，也没有耗尽请求预算。**没有 run 在拒绝后以合法 lineage 节点重试。**这些是已有 run 轨迹的描述性分类，不据此修改或重述 v29/v30 的评分、暂停原因或模型表现结论。

## 范围与来源

权威归档位于 `C:/Users/29913/codex_space/DataIncidentGym-v25-exec`。本次也检查了 `.dig/audit-qb` 中既有的复制批次，并将清单、ledger、doctor 文件以及 31 个 run 的全部六个归档文件与该归档逐字节比较：全部一致。main checkout 自身的顶层 `artifacts/<run_id>` 未包含这些 run，因此以下身份核对基于权威归档路径：

- `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/config/benchmark/p1-formal-v29.json`，SHA-256：`138d790c7880fe1ebb7460b29561894df9ab3772acf100c67d3cbba283d7a34b`
- `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/config/benchmark/p1-formal-v30.json`，SHA-256：`af5299be5903517f4897417aacb60d44011533422e6d91995a967defbb0cb5c9`
- `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/artifacts/benchmarks/p1-formal-v29/ledger.jsonl`，SHA-256：`6e6254c55f5f713585a4410ca3387118ae64b030f67bb6a1017a83cd9a6575ba`
- `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/artifacts/benchmarks/p1-formal-v30/ledger.jsonl`，SHA-256：`1eb214894b02b021ac7101461f9afad5019fe5b325a1dd8430b77b1f07a3b58a`
- `C:/Users/29913/codex_space/DataIncidentGym-v25-exec/artifacts/<run_id>/trace.jsonl` 及同目录的其他五个归档文件

副本与上述权威归档文件逐字节一致；没有因产物缺失或身份不符而标记为不可判定的 run。

检查单位是**终态 run**，不是错误事件总数。分母为 ledger 中有唯一 `STARTED` 和唯一终态记录、且与 manifest 对应的所有 run。逐 run 检查 trace 中 `event_type == "TOOL_CALL"` 且 `error_code == "NODE_ARGUMENT_NOT_PROVEN"` 的事件。完整性核对结果：v29 13 个、v30 18 个终态 run；每个 run 的 manifest/ledger/metadata 身份一致，六个归档文件齐全，trace 序号连续且唯一的 `DIAGNOSIS_TERMINAL` 位于末尾；未发现副本内身份不一致或缺件。

## 暴露 run 与后续轨迹

| 批次 / seq | run_id | 拒绝 trace 序号 | 后续可见路径 | 终态及预算 | 分类 |
| --- | --- | ---: | --- | --- | --- |
| v29 / 10 | `2c09ca6d45ae9307762ec7a5bb711945` | 3 | 没有后续工具调用；trace seq 6 记录 `MODEL_API_ERROR`（`model_request_index=5`），seq 9 为 `MODEL_ERROR` 终态 | 模型请求 4/8；kernel 状态工具调用 2/8 | **无法判定后续恢复**：provider 错误结束了该 run；没有观察到预算耗尽或改道 |
| v30 / 10 | `55435d24ea0aceaa89d0b57c68dc5881` | 4 | 后续没有工具调用；trace seq 8 接受 `INSUFFICIENT_EVIDENCE`，seq 10 终态 | 模型请求 6/8；kernel 状态工具调用 3/8 | **未观察到耗尽或改道**：带剩余预算以不足证据结束；不能据此推断如果继续会怎样 |
| v30 / 14 | `f7624949a02df4d5b4b340d69b34e7d0` | 4 | trace seq 5 成功调用 `get_relation_schema(relation_name="raw_payments")`；没有后续合法 lineage 节点调用 | 模型请求用量 8/8；kernel 状态工具调用 4/8；trace seq 11 为 `INSUFFICIENT_EVIDENCE` 终态 | **改走其他工具路径；模型请求用量达到 8/8** |
| v30 / 18 | `7207a8c0ade2f195089333684f9e391d` | 6 | trace seq 8 成功调用 `get_relation_schema(relation_name="raw_payments")`；没有后续合法 lineage 节点调用 | 模型请求用量 8/8；kernel 状态工具调用 6/8；trace seq 13 为 `INSUFFICIENT_EVIDENCE` 终态 | **改走其他工具路径；模型请求用量达到 8/8** |

拒绝调用的 arguments 在四个 run 中均为 `get_dbt_lineage(node_id="seed.jaffle_shop.raw_payments", direction="downstream")`。四个终态 kernel snapshot 的 `lineage_node_candidates` 均为空。因此，“改走其他工具路径”只表示之后观察到另一个成功工具调用，不表示找到并改用了合法 lineage 节点。

## 分母与分类汇总

| 分类 / 观察 | 分子 / 分母 |
| --- | ---: |
| 已核对完整的终态 run | 31/31（v29 13/13；v30 18/18） |
| 至少一次 `NODE_ARGUMENT_NOT_PROVEN` 的 run | 4/31（v29 1/13；v30 3/18） |
| 后续成功改用其他工具路径 | 2/4 暴露 run |
| 后续合法 lineage 节点调用 | 0/4 暴露 run |
| 终态模型请求用量达到 8/8 | 2/4 暴露 run；与上述改走其他工具路径的两例重叠 |
| 没有后续工具调用且模型请求预算未耗尽 | 2/4 暴露 run |
| 因 provider 错误或未继续而无法判断若继续运行是否恢复 | 2/4 暴露 run |

`MODEL_TOOL_CALL_LIMIT` 与本次分类分开处理：判定预算使用量时，逐 run 读取 trace/kernel state 的模型请求预算及工具调用预算，不把被拒绝的 lineage 调用本身误算成成功工具调用。四个暴露 run 均未达到工具调用预算上限；v30 seq 14/18 的模型请求用量达到 **8/8**，但这两格最终接受的是 `INSUFFICIENT_EVIDENCE`，不应称为 `MODEL_REQUEST_LIMIT` 终态，也不能把该终态归因于 lineage 拒绝。

## 边界

- 这是已有 trace 的离线暴露面登记。没有调用模型、数据库或 benchmark runner。
- 不更改 `.dig/audit-qb` 副本、原始归档、ledger、评分文件、manifest 或既有报告结论。
- v29/v30 是按停止规则留下的重叠前缀；本报告的 `4/31` 只是这 31 个可用终态 run 中的观察比例，不代表完整赛程或总体发生率。
