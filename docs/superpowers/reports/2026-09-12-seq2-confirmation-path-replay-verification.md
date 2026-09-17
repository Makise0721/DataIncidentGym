# seq2 确认路径重放核查（离线）

日期：2026-09-12。对象：p1-formal-v20 seq2（`schema_type_change_order_customer_a`，run `d08c3417917c7a9928e1df35a5378bf6`）：模型在 6 次采集全部成功后**未尝试确认、直接弃答**，evaluator 判 `STATUS_EXACT` 等 4 项失败。本轮验证"证据充分"这个前提本身，并逐项核对公开证据能否支持确认、排除替代假设。未改实现与提示，未发真实请求。

## 1. 结论

**"证据充分"前提成立，且在 kernel 与 evaluator 两层同时证明**：用实跑已取得的 6 条证据构造确认路径（选中 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，根因声明引用节点错误 + schema(raw_orders)），**kernel 接受，evaluator 13 项检查全部通过**。确认路径在当轮预算与证据下完全可达——模型的弃答没有证据层面的必然性。机制确认：**模型选择问题（把确认门槛抬到合同要求之上）**，不是接口缺陷，也不是证据缺失。

附带发现一个接口观察（见 §4，单独登记）：kernel 的 schema-source 门禁弱于 evaluator 的声明矩阵，可能产生"kernel 接受、evaluator 拒绝"的提交——该次接受没有提供纠正反馈。

## 2. 最小重放

重建 kernel（同 run_id、同白名单、同预算、同假设登记 `h_schema_type_changed` / `h_transform_cast_changed`），按真实轨迹回放 6 次采集（run results、node error(customers)、upstream lineage、schema(raw_customers)、schema(raw_orders)、downstream lineage），随后提交确认决策并离线调用 `DeterministicEvaluator`（真实场景、真实证据、重放的 kernel 终态）：

| 变体 | 根因声明引用 | kernel | evaluator |
| --- | --- | --- | --- |
| v1 | node error + **schema(raw_customers)**（relation_name=raw_customers） | ACCEPTED | **FAIL**：`CLAIM_EVIDENCE_COMPATIBLE` → `CLAIM_MATRIX_INVALID` |
| v2 | node error + **schema(raw_orders)**（relation_name=raw_orders） | ACCEPTED | **PASSED（13/13）** |

v2 的 evaluator 明细：`STATUS_EXACT`、`ROOT_CAUSE_ACCEPTED`、`AFFECTED_ASSETS_EXACT`（expected 与 actual 恰为 `model.jaffle_shop.customers`）、`CLAIM_EVIDENCE_COMPATIBLE`、`REQUIRED_EVIDENCE_TYPES_PRESENT`、trace/工具白名单等全部通过。诚实记录：首版脚本把 `recovery_succeeded` 误传 False 导致 `RECOVERY_HEALTHY` 假失败，修正为真实值 HEALTHY 后重跑，属构造错误非机制。

## 3. 公开证据逐项论证（不使用私有期望补推理）

- **支持根因**：节点错误文本（公开运行证据）——`operator does not exist: integer = text ... on customers.customer_id = customer_orders.customer_...`，类型不匹配发生在 customers 模型的连接处；schema(raw_orders) 显示 `user_id` 的类型是 **text**——即不匹配的源头类型在**原始层**就存在。
- **排除替代假设 `TRANSFORMATION_COLUMN_CAST_CHANGED`**：若是 stg 变换内的 cast 改变，原始层类型应保持原样；raw 层已是 text，说明变更发生在源。这就是区分两个已登记假设的公开判据——**模型在第 5 次调用已把它采到**（schema(raw_orders)），却没有使用。
- **弃答声明的检视**：模型声明 `TRANSFORMATION_DEFINITION / model.jaffle_shop.stg_orders / NOT_OBSERVABLE`。该事实本身真实（模型定义不可读），但它不是任何一层确认义务的一部分：kernel 的确认门禁（根因声明须引用节点错误 + 被声明的上游关系的 schema）与 evaluator 的判据都不要求读取变换定义。模型把"区分假设所需的额外材料"当成了确认前提，而区分所需的判据其实已在手。
- **私有期望的使用边界**：根因与资产的期望值只出现在 evaluator 公开产物（`evaluation.json` 的 expected 字段）中，用于最后评分核对；§3 的论证全部来自公开 brief、证据内容与公开产物。场景私有矩阵仅在包内评分时使用。

## 4. 附带接口观察

v1 变体暴露：kernel 的 `_require_schema_source_target_schema` 只要求"声明的关系在上游集合中，且引用了该关系的 schema 事实"，**不校验 schema 内容是否显示变更列的新类型**；evaluator 矩阵则要求引用**被变更关系**的 schema 且其中列类型等于新类型。因此存在一条可走的路径：模型确认时引用了"错误关系"的 schema（如 raw_customers），kernel 接受、evaluator 拒绝——**该次接受没有提供纠正反馈**（kernel 已放行的提交不会再被拒绝，也就不存在反馈时点）。若未来处理，方向不是改反馈文案（对已接受的提交无效），而是加强 kernel 的内容校验；其公开可判定条件（如"schema 内容须与节点错误的类型矛盾一致"）需另行核对后再决策。本观察单独登记，不并入任何其他修复。

## 5. 是否值得修复及最小方案

- **主机制（过度弃答）**：属于模型行为而非代码缺陷，且为单格样本。本轮不修。若未来干预，方向是把确认义务写成更可判定的形式（例如明确"区分假设所需的公开判据若已在已采集证据中，即应确认而非弃答"），属提示改动，须另行立项并升身份。
- **附带观察（kernel 门禁弱于矩阵）**：单独登记（见 §4）。它不能靠反馈文案修复——已接受的提交没有反馈时点；是否加强内容校验、其公开可判定条件是否成立，是独立的后续核查项。
- 与 seq14 不同，本格**不需要**新门禁或新反馈码——判据两层一致，问题在模型未使用已采到的判据。

## 6. 边界

- 重放证明的是**可达性**（确认路径存在且两层通过），不证明模型当时"应当"确认；单格样本不做归因外推。
- v13 同格在旧契约下曾确认通过，说明确认行为跨身份不稳定——与 seq14 的资产格式不稳定同属"无稳定合同信号下的选择波动"，但两者失败层不同。
- 重放脚本为临时文件，运行后已删除；未新增持久测试或实现改动。
