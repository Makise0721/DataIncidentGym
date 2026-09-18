# T13 切片 2 报告：窄列映射读器与形状矩阵

- 日期：2026-09-18。范围：设计 §6 切片 2 —— 窄列映射读器（`column_mapping.py`）与形状矩阵回归。
- **未包含**：读器接入参考解/规划器（切片 4）、数据库 dry run、manifest 冻结与真实模型测量。

## 1. 读器契约（与设计 §2.2 一致）

`map_failing_expression(node_sql, message, *, upstream) -> MappingResult`：

- **失败表达式识别**（只用于选定表达式，不用于映射）：候选 = 该节点 SQL 内各 SELECT 的投影与连接条件；
  候选的规范化文本（折叠空白、去注释、统一小写）出现在报错消息中即命中。**命中若为另一命中的子串，
  按同一表达式处理（只保留最大命中）**；最大命中恰好一个才继续，否则 `EXPRESSION_NOT_IDENTIFIED` /
  `EXPRESSION_AMBIGUOUS`。
- **依赖子图**：只解析该表达式两侧引用所在的 CTE/别名链；同一 SQL 中无关的连接、聚合与子句既不解析
  也不触发 UNKNOWN（实证：`customers` 的 `final` 是三方连接，失败表达式只涉及其中两侧，第三方不进入
  子图）。
- **形状矩阵**：单源别名投影（改名）、算术/转换表达式、多 CTE 链、两源等值连接（限定同名列）、
  聚合（映射到表达式引用列）——以上支持；窗口函数、子查询、`filter(...)`、`distinct`、深度超限（含
  定义环链）等一律 **UNKNOWN**，且 `complete=false` 的上游定义（`UpstreamDefinition`）拒绝穿透。
- **UNKNOWN 理由固定**（`UNKNOWN_REASONS`），调用方不得自造；UNKNOWN 永不作为任何方向的证据。
- 源关系名归一为非限定名（`analytics.raw_customers` → `raw_customers`），供 E1 期望比对；完整路径保留在
  `chain` 里供审计。

## 2. 验收形状（实证）

| 形状 | 输入（真实 fixture 编译形态） | 结果 |
| --- | --- | --- |
| T1′（对 1-A 的决定性路径） | `customers` 的 `on customers.customer_id = customer_orders.customer_id` | `RESOLVED`：左 `raw_customers.id`（经 `stg_customers: id as customer_id`）、右 `raw_orders.user_id`（经 `stg_orders: user_id as customer_id`） |
| T2′ | `orders` 的 `on orders.order_id = order_payments.order_id` | `RESOLVED`：`raw_orders.id`（经 `id as order_id`）与 `raw_payments.order_id`（直通） |
| 算术投影 | `amount / 100 as amount` | `RESOLVED`：`raw_payments.amount` |
| 聚合 | `sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount` | `RESOLVED`：`raw_payments.payment_method` + `raw_payments.amount` |

两条验收形状与审计更正后的源列事实一致（`raw_orders` 的源列是 `id`，`order_id` 是 stg 改名结果）。

## 3. 开发期发现并修复的三处读器缺陷（如实记录）

1. **限定名判定错误**：用 `str.partition(".")` 判断引用是否带限定，未限定列 `amount` 会被当成
   qualifier="amount" → 一律 `UNKNOWN_SOURCE`。改为按 `.` 是否存在拆分。
2. **JOIN 引导词切分错误**：按关键字 `join` 切分会把 `left` 留在前一段的 `on` 条件尾部
   （`... customer_id left`），使真正的连接条件无法与消息文本匹配 → 误判 `EXPRESSION_AMBIGUOUS`。改为整体
   匹配 `(left|right|full|inner|cross)? join`。
3. **JOIN 源未进入作用域**：限定引用 `order_payments.order_id` 找不到来源。修正为 FROM 与 JOIN 源均进
   作用域，`select *` 直通仍只允许单一来源（多源直通一律 UNKNOWN）。

## 4. 回归与验证

`tests/unit/test_t13_column_mapping.py`（14 条，全部离线）：两条验收形状、算术投影、聚合、无关消息 →
`EXPRESSION_NOT_IDENTIFIED`、两条最大命中 → `EXPRESSION_AMBIGUOUS`、窗口函数与子查询 → UNKNOWN、
`complete=false` 定义 → `DEFINITION_INCOMPLETE`、未登记上游关系即自身 origin、多源未限定列 → UNKNOWN、
定义环链 → `DEPTH_EXCEEDED`、空白/注释差异不影响识别。

`ruff check .`、`git diff --check` 通过；全量单测 **910 passed / 5 skipped**。

## 5. 边界（如实）

- 读器尚未接入任何策略路径（切片 4）；本切片的结论只覆盖读器本身与形状矩阵。
- 表达式识别规则以镜像 dbt `LINE n: <sql>` 形态的合成消息验证；真实失败消息的形态确认属数据库 dry run
  的检查项（设计 §4.1 已列）。
- UNKNOWN 语义与切片 0/1 一致：不作证据、不产生确认；设计性扣留的可答性不受影响。
