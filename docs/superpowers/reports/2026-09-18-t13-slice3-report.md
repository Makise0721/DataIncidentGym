# T13 切片 3（离线部分）报告：两对 A/B 场景落盘

- 日期：2026-09-18。范围：设计 §6 切片 3 的**离线部分** —— 两对 A/B 场景文件、登记与合同/对称性回归。
- **明确未完成（均需授权或后续切片）**：数据库 dry run（未授权）、`certify --admit`、参考解/规划器的
  `p1.evidence_tools.v2` 接线（切片 4）、真实链路验收。本报告不宣称任何场景已认证或可答。

## 1. 落盘内容（严格按设计 §4.1）

四个新场景文件（`config/scenarios/`），两对镜像的 A/B：

| 对 | 失败节点 | mutation（冻结目标） | A（确认侧） | B（扣留侧） |
| --- | --- | --- | --- | --- |
| 对 1（T1′） | `model.jaffle_shop.customers` | `COLUMN_TYPE_CHANGE(raw_customers.id: integer→text)` | `schema_type_change_raw_customer_id_a` | `schema_type_change_raw_customer_id_b` |
| 对 2（T2′ 镜像） | 同上 | `COLUMN_TYPE_CHANGE(raw_orders.user_id: integer→text)` | `schema_type_change_raw_order_user_id_a` | `schema_type_change_raw_order_user_id_b` |

- A 变体合同（`observable_evidence.v2`）：`schema_relations = [raw_customers, raw_orders]`；
  `expectation_relations = [raw_customers, raw_orders]`；`definition_nodes = [model.jaffle_shop.customers,
  model.jaffle_shop.stg_customers, model.jaffle_shop.stg_orders]`；`unresolved_gaps = []`；
  `required_evidence_types` 含 `RELATION_SCHEMA_EXPECTATION`、`DBT_NODE_DEFINITION`；
  `expected_status = CONFIRMED`，根因 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`。
- B 变体：观测仍可读（`schema_relations` 同 A），`expectation_relations = []`、`definition_nodes = []`，
  缺口恰为设计规定的两条：
  `RELATION_SCHEMA_EXPECTATION(raw_customers)/RELATION_NOT_ALLOWED/get_relation_schema_expectation` 与
  `DBT_NODE_DEFINITION(model.jaffle_shop.customers)/NODE_NOT_ALLOWED/get_dbt_node_definition`；
  `expected_status = INSUFFICIENT_EVIDENCE`，两个兼容根因；**不把被拒事实列入
  `required_evidence_types`**（拒绝不产生事实，列入会使认证必然不通过）。
- 两对的 A/B 表面完全一致（`incident_brief`、mutation、`forbidden_leakage`、seed 相同），仅差异字段
  为 `ab_symmetry_findings` 允许的答案侧；对 2 与对 1 的差异只在被改列（`observable_evidence_contract`
  与其余字段逐项相同）。

登记：`P1_T13_PUBLIC_EVIDENCE_IDS` 加入 `SUPPORTED_SCENARIO_IDS`（**不加入 `P1_SCENARIO_IDS`**，冻结
manifest 的 17 条目录与 18 条历史场景不受影响）；两对加入 `AB_SCENARIO_PAIRS`；`config/scenario-sets.json`
四条 dev 记录（带 T13 创建原因，沿用 T12 分区规则）。

## 2. 构造前提的离线静态预检（可复现，未连数据库）

方法：取一次**健康构建**的运行产物（`dbt_exit_code = 0`，25 个节点均有 `compiled_path`：
`.dig/lab/runs/ff3f5c47b42a4c41888048d4e673d611`），对每个候选 mutation 目标做：用切片 2 读器的
`_parse_query`/`_parse_select`/`_Resolver` 解析每个节点 SQL 中的**等值比较**（连接条件），把两侧各自
归因到源列（定义取自 manifest 的 `relation_name` 身份，终止关系为三个 seed），再按 `.dig/baseline-summary.json`
的列类型（被改列按 text）判定两侧类型是否冲突。脚本一次性使用、未入库。

| mutation | 类型冲突的比较点 | 失败**模型**集合 |
| --- | --- | --- |
| `raw_customers.id`（T1′） | `customers`: `customers.customer_id = customer_orders.customer_id` | `{model.jaffle_shop.customers}` |
| `raw_orders.user_id`（T2′） | `customers`: 同上（右起源为 `raw_orders.user_id`） | `{model.jaffle_shop.customers}` |
| `raw_orders.id`（设计已排除） | `customers` 与 `orders` 各一处 | `{customers, orders}` |
| `raw_payments.order_id`（设计已排除） | `customers` 与 `orders` 各一处 | `{customers, orders}` |

结论与设计 §4.1 的登记表一致：两对 T1′/T2′ 的失败模型是单节点，两个被排除候选确实横跨两个模型。
**仍需 dry run 确认的部分（静态分析无法替代）**：

1. dbt 实际报出的消息文本（预测为 `operator does not exist: text = integer` +
   `LINE n: on customers.customer_id = customer_orders.customer_id`）；
2. 同一节点内还有第二个类型冲突比较点
   `customers.customer_id = customer_payments.customer_id`——它落在**同一失败节点**内，不影响"单一失败
   节点"结论，但若 dbt 报出的是**这一条**，切片 2 读器对该侧（`customer_payments.customer_id` 经无别名
   限定投影 `orders.customer_id` 向下追溯）会返回 UNKNOWN，A 变体的决定性路径将落到弃答。dry run 必须
   确认消息点名的是哪一条；若为第二条，需按切片 4 的方式处理该窄点（读器不扩形状或显式设计变更，另行裁定）；
3. 关系测试 `test.jaffle_shop.relationships_orders_customer_id__customer_id__ref_customers_` 同样比较这两列；
   `dbt build` 下它是失败模型的下游节点，预期被 **skipped**（run_results 状态需确认），届时
   `direct_failure` 与 `affected_assets` 的取值才最终成立。

## 3. 认证/准入的当前阻塞（如实）

1. **参考解仍是 v1 工具面**：`REFERENCE_ANALYST_TOOL_NAMES = FIXED_RULE_TOOL_NAMES`（六工具），身份经
   `tool_schema_sha256` 冻结。两个 A 变体的 `required_evidence_types` 含 E1/E2 两个新事实，v1 参考解无法
   采集、认证的 `types_ok`/`cited_types_ok` 必然不满足 → **两对的认证要在切片 4 完成 v2 工具面接入后才可能通过**。
2. **管理平面卡片尚未认识 v2 白名单**（切片 4 待办）：`_readonly_path` 只列 v1 六工具（A 变体卡片因此漏掉
   两个决定性工具），`_decisive_difference` 只比较 schema/profile/history（B 变体卡片只描述缺口、不点名被
   扣留的 `expectation_relations`/`definition_nodes`）。二者都不是证据错误，但作为管理平面文档当前不完整。
3. 数据库 dry run 未执行（未授权），`certify --admit` 未执行。

## 4. 回归与验证

`tests/unit/test_t13_scenario_pairs.py`（11 条，全部离线）：两对已登记且互为 A/B；每条按冻结 mutation、
`direct_failure`、`affected_assets`、合同 v2 校验；A 变体的三项白名单与两个必需事实；B 变体的空白名单与
两条缺口的 `(kind, subject, code, tool)` 精确集合、且不把被拒事实列入必需证据；每对
`ab_symmetry_findings` 无未满足项；两对之间仅被改列不同；卡片成对且 `solvability` 保持未决（认证未发生）。

配套更新：`test_scenario_cards.py` 对数 7→9，`test_scenario_sets.py` 分区原因按 T12/T13 分流，
`test_scenarios.py` 目录计数 22→26，`test_t13_slice0_contracts.py` 的"v1 序列化稳定"改为只遍历 v1 场景
（并断言 v2 场景恰为 T13 四条）。

`ruff check .`、`git diff --check` 通过；全量单测 **947 passed / 5 skipped**（本提交新增 11 条）。

## 5. 边界

- 本切片不产生任何"已认证/可答/可诊断"的结论；两对场景在 dry run 与切片 4 完成前不得计入任何评估集合。
- 场景文件全部 offline 校验；`certify --admit`、数据库 dry run、manifest 冻结与真实模型测量均未执行。
