# T13 切片 2 报告：窄列映射读器与形状矩阵

- 日期：2026-09-18。范围：设计 §6 切片 2 —— 窄列映射读器（`column_mapping.py`）与形状矩阵回归。
- 本版含**审计修复轮**（审计意见：别名被丢弃、不支持的 SQL 被部分解析、缺定义被当成源关系）。
- **未包含**：读器接入参考解/规划器（切片 4）、数据库 dry run、manifest 冻结与真实模型测量。

## 1. 读器契约（与设计 §2.2 及其"实施期收紧"一致）

`map_failing_expression(node_sql, message, *, upstream, terminal_relations) -> MappingResult`：

- **失败表达式识别**（只用于选定表达式，不用于映射）：候选 = 该节点 SQL 内各 SELECT 的投影与连接条件；
  候选的规范化文本（折叠空白、去注释、统一小写）必须**以自身边界**出现在报错消息中。仅出现在更长引用
  内部的命中（`customer_id` ⊂ `customers.customer_id`）是另一个表达式；**命中若为另一命中的子串，按同一
  表达式处理（只保留最大命中）**；最大命中恰好一个才继续，否则 `EXPRESSION_NOT_IDENTIFIED` /
  `EXPRESSION_AMBIGUOUS`。
- **依赖子图**：只解析该表达式两侧引用所在的 CTE/别名链；同一 SQL 中无关的连接、聚合与子句既不解析
  也不触发 UNKNOWN（实证：`customers` 的 `final` 是三方连接，失败表达式只涉及其中两侧，第三方不进入
  子图）。
- **完整消费**：被遍历的每个 SELECT 必须被文档化语法整体消费——集合运算、逗号连接、无 `ON` 的连接、
  非文档化条目形态、子句次序/重复违规一律整体拒绝（`UNSUPPORTED_SELECT`）。
- **作用域**：书写别名取代关系名；只有调用方显式声明为 seed/source 的关系可以终止追溯，其余关系缺定义
  为 `DEFINITION_MISSING`。
- **形状矩阵**：单源别名投影（改名）、算术/转换表达式、多 CTE 链、两源**单一等值**连接（两侧各为单列
  引用）、聚合（映射到表达式引用列）——以上支持；窗口函数、子查询、`filter(...)`、`distinct`、非等值或
  合取连接条件、未声明终止关系、深度超限（含定义环链）等一律 **UNKNOWN**，`complete=false` 的上游定义
  （`UpstreamDefinition`）拒绝穿透。
- **UNKNOWN 理由固定**（`UNKNOWN_REASONS`，本轮新增 `DEFINITION_MISSING`），调用方不得自造；UNKNOWN
  永不作为任何方向的证据。上游定义键按非限定关系名归一（`analytics.stg_customers` → `stg_customers`）；
  两个键归一后同名 → `ValueError`（调用方错误，不静默选边）。
- 源关系名归一为非限定名（`analytics.raw_customers` → `raw_customers`），供 E1 期望比对；完整路径保留在
  `chain` 里供审计。

## 2. 验收形状（实证）

| 形状 | 输入 | 结果 |
| --- | --- | --- |
| **T1′（对 1-A 的决定性路径）** | `customers` 的 `on customers.customer_id = customer_orders.customer_id`（失败节点 `customers`） | `RESOLVED`：左 `raw_customers.id`（经 `stg_customers: id as customer_id`）、右 `raw_orders.user_id`（经 `stg_orders: user_id as customer_id`）；顺序与条件左右侧一致 |
| T2′（对 2 镜像） | 与 T1′ **同一失败表达式**（失败节点同为 `customers`）；差异在偏差侧（`raw_orders.user_id`） | 读器层面与 T1′ 同结果；本对的价值是"方向性反例"——引用顺序固定为条件左/右侧，切片 3 才能据此把偏差归到正确起源 |
| 额外形状（非验收对） | `orders` 的 `on orders.order_id = order_payments.order_id` | `RESOLVED`：`raw_orders.id`（经 `id as order_id`）与 `raw_payments.order_id`（直通） |
| 真实归档文本 | 真实运行的 `customers.sql`/`stg_*.sql` 逐字节文本（多行、`left join`、`"db"."schema"."relation"` 三段限定名） | `RESOLVED`：`raw_customers.id` + `raw_orders.user_id`（**已作为回归固定进测试**） |
| 算术投影 | `amount / 100 as amount` | `RESOLVED`：`raw_payments.amount` |
| 聚合 | `sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount` | `RESOLVED`：`raw_payments.payment_method` + `raw_payments.amount` |

两条验收形状与审计更正后的源列事实一致（`raw_orders` 的源列是 `id`，`order_id` 是 stg 改名结果）。

## 3. 本轮审计修复（三处 P1，均先复现后修）

复现方式：把 `7d67a3d` 的读器模块单独加载，跑审计给出的反例（脚本一次性，未入库）。

| # | 审计反例 | 修复前（实测） | 修复后 |
| --- | --- | --- | --- |
| 1 | `from raw_orders as raw_customers join raw_customers as raw_orders on raw_customers.id = raw_orders.id` | `RESOLVED`，来源被别名反转 | `RESOLVED → raw_orders.id, raw_customers.id`（按条件左右侧）；别名之外的原关系名不再是合法限定符 → `UNKNOWN_SOURCE` |
| 2a | 上游定义含 `union all` | `RESOLVED → raw_orders.id`，第二分支被静默丢弃 | `UNKNOWN / UNSUPPORTED_SELECT` |
| 2b | 非等值连接 `a.order_id > b.order_id` | `RESOLVED` | `UNKNOWN / UNSUPPORTED_EXPRESSION` |
| 3 | `select customer_id / 2 from analytics.stg_customers`（无定义） | `RESOLVED → stg_customers.customer_id`（把未取到定义的**模型**当作源关系） | `UNKNOWN / DEFINITION_MISSING`；只有显式声明的 seed/source 才终止追溯 |

修复实现：作用域改为"可见别名 → 关系"映射（`_source_entry`）；`_parse_select` 重写为子句状态机（FROM →
JOIN/ON → where/group by/having/order by/limit/offset 定序，集合运算与不可消费条目整体拒绝）；关系终止改为
调用方显式声明的 `terminal_relations`，新增固定码 `DEFINITION_MISSING`。

## 4. 修复轮中自行发现的两处问题（如实记录）

1. **真实文本上的错误 `RESOLVED`（比审计反例更严重）**：用真实归档的 `customers.sql` 复跑时，读器返回
   `RESOLVED → stg_customers.customer_id, stg_orders.customer_id`——两个**模型**列被当作起源。两条原因叠加：
   ① 上游定义按 `analytics.stg_*` 键索引，而真实编译文本写的是三段名，查不到 → 落到"未登记关系即自身
   origin"的旧规则；② 失败表达式的候选因 `final` 未解析（见下条）而缺失，识别退化为命中**裸列名**
   `customer_id`——它只是消息中 `customers.customer_id` 的一段。修复：定义键按非限定名归一 + 命中必须
   以自身边界出现（`_bounded_occurrence`），并补"裸列名仅出现在限定引用内部 → `EXPRESSION_NOT_IDENTIFIED`"
   的回归。
2. **本轮引入又修掉的解析回归**：子句状态机初版把 `left join` 等修饰连接识别为未知关键字，导致含
   `left join` 的 SELECT 整体不可解析。合成 fixture 用单行 SQL、真实文本用多行，两者都会命中；修复是把
   所有 join 修饰统一规范化为 `join`，并把**真实归档文本逐字节**固化为回归（`REAL_*` 常量与归档文件
   比对一致）。这条记录也说明：切片 2 原报告"形状矩阵通过"的结论只覆盖合成 fixture。

## 5. 回归与验证

`tests/unit/test_t13_column_mapping.py`（**26 条**，全部离线）：T1′ 与 orders 额外形状、真实归档文本形状、
算术投影、聚合、边界命中（负例 + 标点相邻正例）、无关消息、两条最大命中、别名取代关系名（含原关系名
不可用）、显式终止关系、缺定义 → `DEFINITION_MISSING`、未声明关系不产生半答案、窗口函数、子查询、
非等值连接、合取连接、`complete=false`、不可完整消费的定义（union / 逗号连接 / 无 `ON` 连接，参数化）、
定义环链、多源未限定列、重复定义键 → `ValueError`、空白/注释差异。

`ruff check .`、`git diff --check` 通过；全量单测 **922 passed / 5 skipped**。

## 6. 边界（如实）

- 读器尚未接入任何策略路径（切片 4）；本切片的结论只覆盖读器本身与形状矩阵。
- 表达式识别规则以镜像 dbt `LINE n: <sql>` 形态的消息验证；真实失败消息的形态确认属数据库 dry run
  的检查项（设计 §4.1 已列）。
- **已知窄点（fail-closed，不是错误结论）**：无别名的限定投影（如 `select customers.customer_id`）不参与
  "按输出列名查找"，因此经该投影向下追溯列时会返回 `UNKNOWN_COLUMN`；T1′/T2′ 的验收路径不经过它。
  需要时按"唯一匹配才解析"的方式另行收窄，不随本修复扩张。
- UNKNOWN 语义与切片 0/1 一致：不作证据、不产生确认；设计性扣留的可答性不受影响。
