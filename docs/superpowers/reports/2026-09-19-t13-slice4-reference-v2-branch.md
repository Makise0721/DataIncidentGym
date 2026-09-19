# T13 切片 4 · 第三增量：参考解 T13 判别分支（v2 决策路径）

- 日期：2026-09-19。状态：**已完成（离线），待审计**。
- 前置：增量 1（公开身份桥）、增量 2（v2 工具面与策略身份）已审计通过；认证与准入仍需数据库授权。

## 1. 交付范围

参考解（`ReferenceAnalystRunner`）在 **v2 证据面**上的构建失败路径改走 T13 判别分支（设计 §4.1/§4.2）：
类型变更族由**窄列映射读器 + E1/E2 事实 + 身份桥**判定，不再依赖错误消息令牌与列名模式（v1 启发式
`_diagnose_failed_build` 在 v2 运行上不再执行；基于 profile 的 test 路径按设计"维持现状"保持 v1 规则）。
v1 运行的一切行为不变（`tests/unit/test_reference_solver.py` 10 条与全部冻结身份回归原样通过）。

为使该分支在认证路径上真实可行，本次同时接通了四条此前缺失的 v2 接缝（见 §5）——没有它们，B 变体的
v2 缺口要么构造不出来，要么过不了会话/评测/认证。

## 2. 判别分支的决策程序（`_diagnose_failed_build_v2`）

对 v2 运行、非 test 的单一失败节点，按序：

1. **失败必须是类型错误**（复用既有 `_TYPE_MISMATCH_PATTERN` 检查消息）→ 否则弃答。列缺失因此
   永不变成改名结论（§4.2 负面结论）。
2. **读器输入构造**（全部来自公开面）：
   - `node_sql` = E2(失败节点).compiled_sql，且必须 `known` 且 `complete`，否则弃答
     （`DBT_NODE_DEFINITION, 失败节点, NOT_OBSERVABLE`）；
   - `upstream`：每条 E2 事实以其 `relation_identity` 为键（桥显式建立，无相似度）、
     `UpstreamDefinition(compiled_sql, complete)`；无 `relation_identity` 的事实不入键（读器经它解析
     会得 `DEFINITION_MISSING` → UNKNOWN）；两条事实同一身份 → 拒绝选择，弃答（镜像构建期同名防御）；
   - `terminal_relations`：仅 E1 事实中 `resource_type ∈ {seed, source}` 且带 `relation_identity` 者。
3. **请求推导**（公开元数据，见 §4）→ 读 schema → E1 → E2 各一次批量调用。
4. **`map_failing_expression`**：UNKNOWN → 弃答（UNKNOWN 永不作证据）；RESOLVED 但去重后起源
   ≠ 2 → 弃答（§4.2 ① 预设两侧引用；投影命中或塌缩对不满足）。
5. **偏差集合**：对两条起源关系，按 E1 桥（`relation_identity` 精确相等）取期望事实、按其
   `relation_name` 取观测 schema，同名列逐列比较 `data_type`（精确串比较）。§4.2 ②③④：偏差集合
   在涉事上游关系内必须**恰为一条**，且必须是读器映射到的起源列；否则弃答。
6. 全部满足 → `CONFIRMED / SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，资产经既有
   `_failed_model_assets`（失败节点 + 下游 lineage），确认引用全部已采集记录。

## 3. 弃答矩阵（审计重点）

| 类别 | 触发 | 缺口 |
| --- | --- | --- |
| 桥缺失 | E1 事实无 `relation_identity`/`resource_type`；起源关系无期望事实或 `known=false`；观测 schema 缺失 | `RELATION_SCHEMA_EXPECTATION/RELATION_SCHEMA, <关系>, NOT_OBSERVABLE`（+ 转换缺口） |
| 读器 UNKNOWN | 终端缺失（`DEFINITION_MISSING`）、`complete=false`（`DEFINITION_INCOMPLETE`）、形状不支持、识别失败等一切 UNKNOWN 理由 | `TRANSFORMATION_DEFINITION, 失败节点, NOT_OBSERVABLE` |
| 偏差非唯一 | 涉事关系内偏差 0 或 ≥2 条 | 同上 |
| 偏差无法关联 | 唯一偏差不是失败表达式读到的列 | 同上 |
| 定义不可用 | E2(失败节点) 缺失/`known=false`/`complete=false`；E2 批量被拒 | `DBT_NODE_DEFINITION, <节点>, NOT_OBSERVABLE` 或拒绝码 |
| 非类型错误 | 消息不匹配类型错误模式 | `TRANSFORMATION_DEFINITION, 失败节点, NOT_OBSERVABLE`（在调用 E1/E2 之前即弃答） |
| E1/E2 批量被拒 | 原子拒绝 | 见 §4 代表缺口规则 |

弃答缺口语义说明：`NOT_OBSERVABLE` 只用于**事实不可得**（桥缺失、定义不可用）；读器 UNKNOWN、偏差
非唯一、偏差无法关联三类中事实均已可得、失败的是**归因**，因此统一用
`TRANSFORMATION_DEFINITION, 失败节点, NOT_OBSERVABLE`——与 v1 解算器"一切未决即此缺口"的既有语义
一致，不对可观测事实谎报不可观测。

## 4. 请求推导与 B 变体的两条缺口（需审计裁定的一处设计选择）

**E1/E2 请求目标的推导只来自公开元数据**：运行自身白名单（`observable_relations.expectation` /
`observable_nodes.definition`，runtime v2 内公开）、可观测 schema 白名单、失败节点上游 lineage。

- 白名单**非空**（A 变体）：请求 = 白名单 ∩（lineage 推导的涉事候选）。A 对 1/对 2 实际请求：
  E1 `[raw_customers, raw_orders]`、E2 `[customers, stg_customers, stg_orders]`——与 §4.1 逐步清单一致；
  交集保证白名单里未被推导支持的项不会毒化原子批量。
- 白名单**为空**（B 变体）：交集为空集意味着无可读目标、且空请求会被 `TARGETS_EMPTY` 拒绝——为留下
  真实拒绝收据，按同一推导（lineage 候选 ∩ schema 白名单）请求一次：E1 `[raw_customers, raw_orders]`、
  E2 `[customers, stg_customers, stg_orders, stg_payments]`，双双被原子拒绝。

**代表缺口规则**：一次原子拒绝同等阻塞全部请求目标，而评测器 `_insufficiency_matches` 与认证
`gap_keys == _expected_gap_keys` 都要求缺口集合与合同**精确相等**（B 合同恰好两条）。因此诊断对每次
被拒批量声明**请求序第一个目标**为代表：E1 = `raw_customers`（schema 白名单公开顺序）、
E2 = `model.jaffle_shop.customers`（失败节点在请求首位）。该规则确定性、可由公开元数据复现、且不依赖
任何变异方向知识——两条 B 合同（对 1/对 2）钉的正是这两个 subject。两条缺口均满足 v2 见证四条件
（调用级 `TARGETS_REFUSED`、无证据、目标在请求中、`(target, code)` 精确命中 `target_refusals`），
`refusal_witnessed` 回归断言通过。

**工具预算实测**：A = 8/8（run_results、node_error、upstream lineage、两条 schema、E1、E2、
downstream lineage；回归断言 `== 8`），B = 7（弃答不取 downstream）。均在 8 次上限内；如实写明：v2
批量单次信息量高于 v1 逐目标工具，政策身份与单次信息量都不同，"仍为 8 次"不表示条件逐项相同（§3 纪律）。

## 5. 随本增量接通的四条 v2 接缝

1. **`strategy_adapter.py`**：新增 `FinalSubmissionV2`（v2 缺口词表）；`StrategySession.submit` 接受
   两种提交并按类型构造 `Diagnosis` 或 `DiagnosisV2`（v1 提交路径逐字节不变）；`ProtocolTools` 补上
   两个批量工具路由（此前 v2 会话的门面根本没有这两个方法，runner 一调即 AttributeError）。
2. **`fixed_rule.py` `_call`**：`EvidenceToolError` 携带 `target_refusals` 时写入 `ToolTraceEvent`。
   没有这一步，批量拒绝的逐目标明细进不了归档轨迹，`refusal_witnessed` 四条件永不满足——认证与评测
   的收据矩阵都会失败。v1 工具从不携带该字段，轨迹逐字节不变。
3. **`evaluation.py`**：`ALLOWED_DIAGNOSTIC_TOOLS_V2` = 六工具 + 两个批量工具；评测按**场景合同版本**
   选择放行面（v2 合同才放行批量工具，v1 运行无法借道）。`TOOL_ALLOWLIST_EXACT` 与 trace 违例扫描
   两处同步。
4. **`scenario_certification.py`**：意外工具错误豁免面从 `{RELATION_NOT_ALLOWED}` 扩为
   `{RELATION_NOT_ALLOWED, TARGETS_REFUSED}`（两处）。`TARGETS_REFUSED` 是批量拒绝的调用级概括码，
   其逐目标真伪由 `RECEIPTS_PRESENT` 收据矩阵精确判定，此处豁免的只是"概括码本身不算意外错误"。

## 6. 回归与验证

新增 `tests/unit/test_t13_reference_v2_branch.py`（9 条，自包含逐字节 fixture，与读器回归同源）：

1. 对 1 A：左起源偏差 → `CONFIRMED / SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，资产 `{customers}`，
   8/8 调用，E1/E2 已采集并引用；
2. 对 2 A：右起源偏差（方向反例）→ 同样确认；
3. B：精确两条合同缺口 + `refusal_witnessed` 双见证 + `DiagnosisV2` 类型 + schema 仍采集；
4. 桥缺失 → 弃答（不确认）；
5. E2 `complete=false` → 弃答（定义缺口）；
6. 双偏差（非唯一）→ 弃答；
7. 偏差在失败表达式未读的列（`first_name`）→ 弃答；
8. 非类型错误 → 立即弃答且**从不调用** E1/E2（调用记录断言）；
9. 会话接缝：`FinalSubmissionV2` 过会话得 `DiagnosisV2`；门面批量调用保留逐目标拒绝明细（异常透传）。

全量：`uv run python -m pytest tests/unit -q` → **982 passed / 5 skipped**（此前 973+新增 9，零回归，
含冻结 manifest 六策略身份与 v1 参考解全部原测试）；`uv run ruff check .`、`git diff --check` 干净。

## 7. 边界与未决

- **fixed-rule 与 kernel 在 v2 运行上仍走 v1 启发式**：本增量只改造参考解（认证路径）。fixed-rule 在
  v2 运行上的判别与 v2 缺口表达未动，属后续增量或 T14 的决定。
- **重复身份防御无独立回归**：lineage 推导出的候选节点天然互异，`upstream` 同身份冲突分支在线上不可
  经公开面构造，故只实现未单测（防御性代码，与构建期同名防御镜像）。
- 认证与准入（`certify --admit` 两对 + 端到端/离线重评）需数据库授权；两次 dry run 归档早于身份桥，
  认证运行必须重建，不得以旧归档顶替（审计已确认）。
- 第四增量（管理平面卡片 v2 派生 + 规划器模型可见列表签名）随后进行。
