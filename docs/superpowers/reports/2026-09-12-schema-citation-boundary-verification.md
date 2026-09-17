# schema 引用接受边界离线核查

日期：2026-09-12。对象：seq2 附带的接口观察——kernel 对 schema-source 根因的引用校验弱于 evaluator 声明矩阵。本轮只做离线核查（真实 run `d08c3417917c7a9928e1df35a5378bf6` 的冻结证据 + 真实场景评分），不改代码与提示、不发请求。回答三个约定问题：边界是否确实不一致、公开证据能否支持更严判断、能否形成通用且不误拒的规则。

## 1. 结论

1. **边界不一致成立，已用最小重放钉死**：同一决策（根因、资产、评估、置信度全部相同），仅替换所引用的 schema 与声明的关系名——kernel 对"上游关系 + 该关系 schema"的任何组合都放行，evaluator 只接受内容上真实承载变更列类型的那个 schema；前者接受后无纠正反馈。
2. **本次未找到公开依据充分、且不会误拒正确引用的更严规则**：真实错误文本含 CTE 别名（`customer_orders` 不是关系名），且被变更列（raw_orders 的 `user_id`）**完全不出现在错误文本中**（stg 层改名，SQL 不可读）——本次检验的文本锚定思路（按错误文本的列名/关系名匹配）在真实数据上会**误拒正确引用**；这否定的是本次检验的这类思路，不是"任何可能的规则"。
3. **不形成新规则**：kernel 现行"声明关系须在上游集合内、且引用了该关系的 schema"是本次检验中公开可判定的边界；更严的内容校验只能依赖私有期望或脆弱的文本启发式。**保留为已知限制，不新增门禁。**

## 2. 最小重放

同一 kernel（真实 run 的 6 条证据、同假设登记），同一确认决策只变根因声明的 schema 引用与 `relation_name`：

| 变体 | 根因声明引用 | kernel | evaluator |
| --- | --- | --- | --- |
| V-correct | schema(raw_orders)，relation=raw_orders | ACCEPTED | **PASSED**（13/13） |
| V-wrongrel | schema(raw_customers)，relation=raw_customers | **ACCEPTED** | FAILED：仅 `CLAIM_EVIDENCE_COMPATIBLE` → `CLAIM_MATRIX_INVALID` |
| V-mismatch | schema(raw_customers)，relation=raw_orders | REJECTED：`ROOT_CLAIM_EVIDENCE_INCOMPATIBLE` | — |

判据定位（代码）：kernel `_require_schema_source_target_schema` 只要求"声明的关系在上游集合内 + 引用了该关系的 schema 事实"（`diagnostic_validation.py`），不校验 schema 内容；evaluator 的 schema 分支（`evaluation.py`）要求引用**实际发生类型变更的关系**的 schema，且其中变更列的类型已是新类型——具体关系/列由私有合同决定。V-mismatch 证明 kernel 存在"声明↔引用"一致性绑定，缺的只是内容层判断。

## 3. 公开证据能否支持更严判断（问题 2）

真实 run 的公开证据：

- 节点错误文本：`operator does not exist: integer = text ... on customers.customer_id = customer_orders.customer_...`。`customer_orders` 是 customers.sql 内的 CTE 别名，**不是任何 dbt 关系名**；比较两侧的列都叫 `customer_id`。
- schema(raw_orders)：`user_id` 为 **text**——变更列在原始层可见；schema(raw_customers)：`id/first_name/last_name`，无类型异常列。
- stg 层的改名 SQL 不可通过任何只读证据工具获得。

由此，三类公开可见的失效情形使更严规则不可靠：

- **列改名（真实存在）**：错误文本只出现 `customer_id`，而正确引用的 raw_orders 中该列叫 `user_id`。"引用的 schema 须包含错误提到的列"会把**正确引用也拒掉**。
- **别名（真实存在）**：`customer_orders` 无法映射到任何关系；映射到 `customers` 模型则更错。名称匹配规则在真实数据上即失效。
- **多比较与含糊文本（同族场景合理存在）**：多个连接比较或缺失比较细节的错误，没有任何公开信息能锁定该引用哪个关系的 schema；此时更严规则只能随机拒绝或退化为现状。

## 4. 通用且不误拒的规则（问题 3）

按约定覆盖三类：

- **正确引用**：现状已通过（V-correct 两层全过）。
- **错误关系引用**：kernel 在其现行可判定特征（上游关系 + 引用了 schema 事实）上无法区分两种引用；两份 schema 的**内容并不相同**（raw_orders 显示 `user_id` 为 text，raw_customers 无类型异常列），但该内容差异未被 kernel 校验，本次也未找到能在不误拒正确引用的前提下机械利用它的规则。内容启发式都会落入 §3 的误拒情形。
- **信息不足**：错误文本过含糊时，"该引用哪个关系的 schema"在公开层面不存在唯一答案。需要区分两层：**kernel 无法可靠机械校验引用内容，不等于模型必然无法根据证据确认根因**——本次重放不推翻 seq2 确认路径可达的结论，也不把该次弃答重新认定为必要；是否确认或弃答由模型依据证据自行判断，kernel 不做内容裁决。

本次检验中，不误拒的规则只剩"声明↔引用一致性"，即现状。**结论：不值得修复，不新增门禁**；该边界差异（kernel 弱于 evaluator、接受后无纠正反馈）作为已知限制保留，与 seq2 的"过度弃答"（模型选择问题）分开记录。

## 5. 复核入口

- 重放为临时脚本，运行后已删除；未新增任何代码、测试或提示改动。
- 冻结证据：`artifacts/d08c3417917c7a9928e1df35a5378bf6/`（trace/evidence/evaluation）；场景评分经由 `load_scenario_spec("schema_type_change_order_customer_a")`，私有期望仅在 evaluator 内部使用。
- 关联：`2026-09-12-seq2-confirmation-path-replay-verification.md` §4（观察的首次登记与准确表述）。
