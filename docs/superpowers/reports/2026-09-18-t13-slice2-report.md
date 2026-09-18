# T13 切片 2 报告：窄列映射读器与形状矩阵

- 日期：2026-09-18。范围：设计 §6 切片 2 —— 窄列映射读器（`column_mapping.py`）与形状矩阵回归。
- 本版含**两轮审计修复**：第一轮（别名作用域、不支持的 SQL 被部分解析、缺定义被当成源关系）、
  第二轮（关系身份退化为末段名、未知函数被当作列）。
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
  非文档化条目形态、子句次序/重复违规一律整体拒绝（`UNSUPPORTED_SELECT`）；表达式内出现未知符号
  （`::`、`||`）、未支持关键字（`between`、`like`、窗口关键字）、未知函数（`mystery(...)`）一律拒绝
  （`UNSUPPORTED_EXPRESSION`），不允许"跳过不认识的部分继续确认"。
- **作用域**：书写别名取代关系名。
- **关系身份 = 完整名**（去引号、统一大小写）：定义与终止关系均按完整身份匹配，不退化为末段名；等价写法
  由调用方显式并列（同一份定义两个键），无法证明同一来源 → UNKNOWN；起源按 SQL 实际使用的完整身份上报。
- **终止条件**：只有调用方显式声明为 seed/source 的关系可以终止追溯，其余关系缺定义为
  `DEFINITION_MISSING`；定义与终止声明同时存在时以定义为准。
- **形状矩阵**：单源别名投影（改名）、算术/转换表达式、多 CTE 链、两源**单一等值**连接（两侧各为单列
  引用）、聚合（映射到表达式引用列）——以上支持；窗口函数、子查询、`filter(...)`、`distinct`、非等值或
  合取连接条件、未知函数/符号/关键字、未声明终止关系、深度超限（含定义环链）等一律 **UNKNOWN**，
  `complete=false` 的上游定义拒绝穿透。
- **UNKNOWN 理由固定**（`UNKNOWN_REASONS`，第一轮新增 `DEFINITION_MISSING`），调用方不得自造；UNKNOWN
  永不作为任何方向的证据。同一身份出现两份不同定义 → `ValueError`（调用方错误，不静默选边）。

## 2. 验收形状（实证）

| 形状 | 输入 | 结果（起源按完整身份上报） |
| --- | --- | --- |
| **T1′（对 1-A 的决定性路径）** | `customers` 的 `on customers.customer_id = customer_orders.customer_id`（失败节点 `customers`） | `RESOLVED`：左 `analytics.raw_customers.id`、右 `analytics.raw_orders.user_id`；顺序与条件左右侧一致 |
| T2′（对 2 镜像） | 与 T1′ **同一失败表达式**（失败节点同为 `customers`）；差异在偏差侧（`raw_orders.user_id`） | 读器层面与 T1′ 同结果；本对的价值是"方向性反例"——引用顺序固定为条件左/右侧，切片 3 才能据此把偏差归到正确起源 |
| 额外形状（非验收对） | `orders` 的 `on orders.order_id = order_payments.order_id` | `RESOLVED`：`analytics.raw_orders.id` 与 `analytics.raw_payments.order_id` |
| 真实归档文本 | 真实运行的 `customers.sql`/`stg_*.sql` 逐字节文本（多行、`left join`、`"db"."schema"."relation"` 三段限定名） | `RESOLVED`：`data_incident_gym.analytics.raw_customers.id` + `...raw_orders.user_id`（**已作为回归固定进测试**） |
| 算术投影 | `amount / 100 as amount` | `RESOLVED`：`analytics.raw_payments.amount` |
| 聚合 | `sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount` | `RESOLVED`：`analytics.raw_payments.payment_method` + `analytics.raw_payments.amount` |
| 文档化函数 | `coalesce(amount, 0) as amount` | `RESOLVED`：`analytics.raw_payments.amount` |

两条验收形状与审计更正后的源列事实一致（`raw_orders` 的源列是 `id`，`order_id` 是 stg 改名结果）。
**起源名与 E1 期望名的对应由调用方按公开元数据建立**（见 §5）：读器只保证身份完整、不做名称归一。

## 3. 审计修复（两轮，共五处 P1，均先复现后修）

复现方式：把被审版本的读器模块单独加载（`git show <rev>:...`），跑审计给出的反例（脚本一次性，未入库）。

| 轮 | # | 审计反例 | 修复前（实测） | 修复后 |
| --- | --- | --- | --- | --- |
| 1 | 1 | `from raw_orders as raw_customers join raw_customers as raw_orders on raw_customers.id = raw_orders.id` | `RESOLVED`，来源被别名反转 | `RESOLVED → raw_orders.id, raw_customers.id`（按条件左右侧）；原关系名不再是合法限定符 → `UNKNOWN_SOURCE` |
| 1 | 2a | 上游定义含 `union all` | `RESOLVED → raw_orders.id`，第二分支被静默丢弃 | `UNKNOWN / UNSUPPORTED_SELECT` |
| 1 | 2b | 非等值连接 `a.order_id > b.order_id` | `RESOLVED` | `UNKNOWN / UNSUPPORTED_EXPRESSION` |
| 1 | 3 | `select customer_id / 2 from analytics.stg_customers`（无定义） | `RESOLVED → stg_customers.customer_id`（未取到定义的**模型**被当作源关系） | `UNKNOWN / DEFINITION_MISSING` |
| 2 | 4 | 只提供 `analytics.stg_customers` 的定义，SQL 读 `stg_customers` / `other.stg_customers` / `other.analytics.stg_customers` | 三种写法都 `RESOLVED → raw_customers.id`（按末段名套用了别的 schema 的定义） | 三种写法都 `UNKNOWN / DEFINITION_MISSING`；身份改为完整名匹配，等价写法须调用方显式并列 |
| 2 | 5 | `mystery(amount)` | `RESOLVED → raw_payments.mystery, raw_payments.amount`（函数名被当作列，凭空多一个来源列） | `UNKNOWN / UNSUPPORTED_EXPRESSION`；未知符号（`amount::numeric`、`||`）与未支持关键字（`between`）同样拒绝 |

## 4. 修复轮中自行发现的三处问题（如实记录）

1. **真实文本上的错误 `RESOLVED`（第一轮发现）**：用真实归档的 `customers.sql` 复跑时，读器返回
   `RESOLVED → stg_customers.customer_id, stg_orders.customer_id`——两个**模型**列被当作起源。两条原因叠加：
   ① 上游定义按 `analytics.stg_*` 键索引，而真实编译文本写的是三段名，查不到 → 落到"未登记关系即自身
   origin"的旧规则；② 失败表达式的候选因 `final` 未解析而缺失，识别退化为命中**裸列名** `customer_id`——
   它只是消息中 `customers.customer_id` 的一段。修复：身份按完整名（第二轮进一步收紧为"不退化为末段名"）
   + 命中必须以自身边界出现。
2. **第一轮引入又修掉的解析回归**：子句状态机初版把 `left join` 等修饰连接识别为未知关键字，导致含
   `left join` 的 SELECT 整体不可解析。修复是把 join 修饰统一规范化为 `join`，并把**真实归档文本逐字节**
   固化为回归（`REAL_*` 常量与归档文件比对一致）。这条记录也说明：切片 2 原报告"形状矩阵通过"的结论
   只覆盖合成 fixture。
3. **第二轮修复后自查出的漏配**：`coalesce(amount, 0)` 因逗号未被识别为合法标点而误判 UNKNOWN——由
   "文档化函数仍可解析"的正例捕获，已修正（正例保留在回归里，防止过度收紧）。

## 5. 回归与验证

`tests/unit/test_t13_column_mapping.py`（**35 条**，全部离线）：T1′ 与 orders 额外形状、真实归档文本形状、
算术投影、聚合、文档化函数正例、边界命中（负例 + 标点相邻正例）、无关消息、两条最大命中、别名取代关系名、
原关系名不可用、**跨 schema/裸名/跨库三种写法的定义隔离（参数化）**、两种写法显式并列可用、
显式终止关系、缺定义 → `DEFINITION_MISSING`、未声明关系不产生半答案、窗口函数、子查询、
**未知函数不产生列引用**、**表达式完整消费（`::` / `||` / `between`，参数化）**、非等值连接、合取连接、
`complete=false`、不可完整消费的定义（union / 逗号连接 / 无 `ON` 连接，参数化）、定义环链、多源未限定列、
同一身份两份定义 → `ValueError`、空白/注释差异。

对照验证：把本轮两个反例对着 `0956517` 的读器复跑，`mystery(amount)` 与三种 `stg_customers` 写法全部
复现为错误 `RESOLVED`；修好后同样输入全部为 UNKNOWN。新测试对被审读器的失败面为 **17/35**（含全部身份
类断言与扫描类负例）。

`ruff check .`、`git diff --check` 通过；全量单测 **931 passed / 5 skipped**。

## 6. 边界（如实）

- 读器尚未接入任何策略路径（切片 4）；本切片的结论只覆盖读器本身与形状矩阵。
- 表达式识别规则以镜像 dbt `LINE n: <sql>` 形态的消息验证；真实失败消息的形态确认属数据库 dry run
  的检查项（设计 §4.1 已列）。
- **切片 4 的接线要求（本轮新增，必须随接线实现）**：读器上报的起源是**完整关系身份**（如
  `data_incident_gym.analytics.raw_customers`）；把它对应到合同 `expectation_relations` 与 E1 记录中的
  `raw_customers`，以及把二段/三段写法并列声明，都必须由接线方按公开运行元数据（manifest 的
  `relation_name` 等）显式建立，读器不做名称归一。
- **已知窄点（fail-closed，不是错误结论）**：无别名的限定投影（如 `select customers.customer_id`）不参与
  "按输出列名查找"，因此经该投影向下追溯列时会返回 `UNKNOWN_COLUMN`；T1′/T2′ 的验收路径不经过它。
- 文档化函数集是封闭列表（`cast`/`coalesce`/`nullif`/`sum`/`min`/`max`/`count`/`avg`）；出现新函数时按
  UNKNOWN 处理，需要支持再按最小形状逐项加入，不随修复扩张。
- UNKNOWN 语义与切片 0/1 一致：不作证据、不产生确认；设计性扣留的可答性不受影响。
