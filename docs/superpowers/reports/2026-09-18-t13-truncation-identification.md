# T13 设计变更实施记录：错误行截断感知识别（设计第 7 版）

- 日期：2026-09-18。依据：审计对 `3e1e931` 的裁定（批准方案 1，附 5 条验收条件）。
- 范围：**读器识别规则**（`column_mapping.map_failing_expression`）；不改映射规则、不改归档格式、
  不涉及数据库与策略面接线。

## 1. 落地内容（逐条对应验收条件）

**条件 1 — 设计升第 7 版，实测事实写入消息形态检查项。** 设计头部改为第 7 版并新增修订记录；
§2.2 增加"截断感知识别"契约条款（触发条件精确为：规范化后的消息片段以 `...` 结尾——其后为空白或消息
结束——且是某候选表达式的真前缀）；§4.1 写入本次 dry run 的实测消息形态（`operator does not exist` +
`LINE <n>:` + `HINT` + `compiled code at <路径>`，被引用的编译 SQL 行截断为 64 字符、以 `...` 结尾、
可截在标识符中间），并写明"两侧起源均可解析"这一充分条件依赖本规则。

**条件 2 — 真实消息与真实编译文本逐字节固化。** `tests/unit/test_t13_column_mapping.py` 新增
`REAL_TRUNCATED_MESSAGE`，逐字节取自 `.dig/lab/runs/22c98522ca7e46f183a52254084e0f79` 的
`run_results` 消息（含 `compiled code at` 行与其中的运行路径；一次性脚本已核对 `==`，报告尾部给出核对
方式）。编译文本沿用切片 2 已固化的 `REAL_CUSTOMERS`/`REAL_STG_*`（与归档逐字节一致）。断言：
`RESOLVED`、表达式为**完整**连接条件 `customers.customer_id = customer_orders.customer_id`、
两侧起源为 `data_incident_gym.analytics.raw_customers.id` 与 `…raw_orders.user_id`。

**条件 3 — 新已知边界在案。** §2.2 记录：截断更短、片段同时是两条 join 条件的真前缀（如
`customers.customer_...`）时，两候选跨度相同、互不支配 → `EXPRESSION_AMBIGUOUS` → 弃答；
方向 fail-closed。对应回归 `test_a_cut_that_fits_both_conditions_stays_ambiguous`。

**条件 4 — 认证路径封口。** dry run 报告 §4 已改写：单侧投影映射**不满足**设计 §4.2 充分条件①，
"对 1 大概率成立"不得作为认证路径；两对认证以本变更落地为前提（设计修订记录同载）。

**条件 5 — UNKNOWN 纪律延续。** 未新增理由码：无命中仍 `EXPRESSION_NOT_IDENTIFIED`，并列命中仍
`EXPRESSION_AMBIGUOUS`；映射仍只用完整 SQL 文本，未引入相似度或片段映射。

## 2. 实现要点

- 识别改为按**命中跨度**判定：每个候选取"原文自身边界命中"与"截断前缀命中"中最宽的跨度；跨度被另一
  候选的跨度**严格包含**者淘汰；剩余跨度互相包含（含跨度相同）→ `EXPRESSION_AMBIGUOUS`。
  这保持了原有"只保留最大命中"的语义，并把截断命中纳入同一规则。
- 截断标记规则：`...` 之后必须是空白或消息结束。**引号字面量内的 `'...'` 不构成标记**
  （回归 `test_an_ellipsis_inside_a_literal_is_not_a_truncation_marker`）。
- 无标记的短片段不构成命中（回归 `test_a_prefix_without_the_truncation_marker_is_not_a_hit`），
  避免"只像开头就算命中"。
- 前缀命中必须是**有界**起点（不紧邻标识符字符或 `.`），与既有命中规则一致。

## 3. 回归与验证

`tests/unit/test_t13_column_mapping.py` 由 40 条增至 **44 条**，新增四条：

| 用例 | 断言 |
| --- | --- |
| `test_the_real_truncated_message_gives_the_whole_join_condition` | 真实截断消息 → 完整条件 + 两侧起源（条件 2） |
| `test_a_prefix_without_the_truncation_marker_is_not_a_hit` | 无 `...` 的短片段 → `EXPRESSION_NOT_IDENTIFIED` |
| `test_an_ellipsis_inside_a_literal_is_not_a_truncation_marker` | `'...'` 不触发截断识别 |
| `test_a_cut_that_fits_both_conditions_stays_ambiguous` | 同时前缀两条条件 → `EXPRESSION_AMBIGUOUS`（条件 3） |

对**两次真实运行的归档消息**分别复跑读器（`upstream` 按 manifest `relation_name`、终止关系为三个 seed）：

```text
22c98522… → RESOLVED | customers.customer_id = customer_orders.customer_id
                        | raw_customers.id, raw_orders.user_id
65626c9a… → RESOLVED | customers.customer_id = customer_orders.customer_id
                        | raw_customers.id, raw_orders.user_id
```

`ruff check .`、`git diff --check` 通过；全量单测 **957 passed / 5 skipped**。

## 4. 边界

- 本变更只恢复**识别**；两对场景仍需 ①dry run 第三次执行（O7 完整成立 + T2′ 的 O1–O6 实测）
  ②切片 4 的 v2 工具面接线，才能进入认证与准入。
- 片段映射被明确排除：若将来出现"截断更短、无法唯一前缀"的形态，结论一律弃答，不引入任何补全猜测。
