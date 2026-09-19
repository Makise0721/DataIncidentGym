# T13 设计：可公开验证的 schema / 转换证据增强（候选）

- 日期：2026-09-18。状态：**已完成（第 8 版；两对于 2026-09-19 第四次执行认证并准入，4/4、
  C1–C8 全部成立——结果报告 `docs/superpowers/reports/2026-09-19-t13-certification-result.md` §9；
  manifest 冻结与真实模型测量仍需各自另行授权）**。
- 依据：改进计划 T13；T12 收口结论；第 2 版（`c2be701`）的审计意见；第 5 版通过设计审查（审查在审计
  对话中完成，仓库内没有独立的审查记录文件，头部状态行的"待审"是当时提交审查时的文字）；切片 0/1/2 的
  各轮实施审计；切片 3 离线部分审计（`b62289d`）的 P2-1 范围偏离意见。
- 边界：不改 kernel/static 的既有身份与历史结果，不冻结 manifest，不做真实模型测量；T14 跨任务另行。

### 修订记录（第 8 版，切片 4 离线收尾、认证之前）

- **lineage 升为承重推导步骤（审计裁定，选 ②）**：第 7 版 §4.1 的"6 步 + 余量可留给 `get_dbt_lineage`
  复核闭包"与参考实现已分叉——第三增量落地后发现，参考解的请求推导（白名单 ∩ lineage 候选）在 A/B
  两个变体上都**依赖** upstream lineage，lineage 不是余量而是承重环节。两对合同的
  `required_evidence_types` 增加 `DBT_LINEAGE`（四个文件各一行），使合同与卡片对真实路径说实话；
  加固作用：任何绕过 lineage 推导（如退回名称启发式）的参考路径将因必需证据缺失而无法通过认证。
  **时间窗口**：两对尚未认证、未进冻结 manifest，这是唯一不使既有证书失效的修订时机。
- **逐步清单与预算按实测重写（§4.1）**：参考解（确定性，0 模型请求）实测路径 A = 8/8、B = 7 次工具
  调用；原"6 + 关闭批次 1 + 提交 1 = 8"的**模型回合**算术同步作废——模型驱动策略若复刻该路径必须
  同回合批处理（如 schema 两条一回合）才能落回 8 模型请求内，报告中不得再以旧算术表述预算条件。
- 对 1/对 2 的 A 变体必需类型现为六项（含 `DBT_LINEAGE`）、B 变体四项（含 `DBT_LINEAGE`、不含两个
  v2 事实）；`types_ok`/`cited_types_ok` 与卡片 `readonly_path` 由此自然推出 lineage 步骤。

### 修订记录（第 7 版，切片 3/4 之间的授权 dry run 之后）

- **错误行截断感知识别（审计批准，附 5 条验收条件）**：第 6 版的 dry run 实测发现 dbt 报告的错误行被截断
  （64 字符 + `...`，可截在标识符中间），join 条件全文从不出现，读器因此退化为命中**投影**、只映射单侧
  起源。对 1（偏差在 `raw_customers.id`）仍可确认，**对 2（偏差在 `raw_orders.user_id`）会在"应确认"场景
  弃答**——镜像对的方向性反例生效。规则按 §2.2 的"截断感知识别"纳入读器契约；实测事实写入 §4.1 的消息
  形态检查项；回归把该次运行的真实消息与真实编译文本**逐字节**固化为 fixture。
- **认证路径封口（审计条件 4）**：在本规则落地前，T1′ 的"投影单侧映射 → 确认"**不满足** §4.2 充分条件
  ①（要求两侧引用都解析到具体关系列），dry run 报告中"对 1 大概率成立"的表述**不得**作为认证路径使用；
  两对的认证都以本变更落地为前提。
- **新增已知边界（审计条件 3）**：截断更短、片段同时是两条 join 条件的真前缀时，两对都 UNKNOWN → 弃答
  （fail-closed，已记录在 §2.2）。
- dry run 暴露的两处实现缺陷已修复并带回归：编译文本按原始字节比较（Windows CRLF，`270d96a`）；
  校验器按 v1 字段集合校验 v2 运行记录（`125c351`）。

### 修订记录（切片 3 实施期，第 6 版）

- **切片 3/4 边界重排（审计 P2-1）**：原第 5 版把"两对 A/B 落盘、**认证与准入**"整体放在切片 3。实施中
  发现真实的依赖倒置——`certify` 的 `types_ok`/`cited_types_ok` 校验 `required_evidence_types`，而两个 A
  变体必需的 `RELATION_SCHEMA_EXPECTATION` / `DBT_NODE_DEFINITION` 只有 v2 工具面（切片 4：参考解与规划器
  接线）才可能采集，且参考解工具名单与身份（`tool_schema_sha256`）当前冻结在 v1 六工具上。因此
  **切片 3 的范围收缩为"落盘 + 离线合同/对称性回归"**，认证与准入顺延至切片 4 之后执行。
- **数据库 dry run 的定位**：第 5 版把 dry run 记为切片 0 的构造验证项（"登记前先验证"）。目标登记已在
  切片 0 离线完成、场景文件已在切片 3 落盘，dry run 因此改为**切片 4 的前置授权项**：定论失败节点集合、
  dbt 消息文本形态、`direct_failure`/`affected_assets`，以及第二条比较点的命中情形（实施计划见
  `docs/superpowers/plans/2026-09-18-t13-dry-run-plan.md`）。dry run 未完成前，两对场景不得计入任何评估
  集合，也不得宣称已认证或可答。
- 切片 0/1/2 与切片 3 离线部分已完成；切片 4 未开始。

## 1. 证据缺口（现状实测）

### 1.1 今天的公开面提供什么

六个只读事实：`DBT_RUN_RESULTS`、`DBT_NODE_ERROR`、`RELATION_SCHEMA`、`DBT_LINEAGE`、
`RELATION_DATA_PROFILE`、`RELATION_HISTORY`。与"类型/转换"相关的实测：

- `get_relation_schema` 读**实时** `information_schema.columns` 并要求与运行快照逐列相等，否则
  `RunStateDriftError`——它给的是**观测**，诊断面上没有**期望**。
- `get_dbt_lineage` 只有**节点级**上下游与距离。
- 运行目录已有 `dbt/target/compiled/**`、manifest 的 `compiled_code`/`columns`/`depends_on`、
  `run_results` 的失败消息与 `compiled_code`——都不在证据面内。实测三者对该次运行**逐字节一致**，
  且 manifest 与 run_results 的 `metadata.invocation_id` **相等**（`2fc86c24-…`）——运行归属绑定的现成材料。
- 可信健康基线（`.dig/baseline-summary.json`，带 fingerprint）含每列 `data_type/nullable/ordinal_position`，
  只服务管理平面。
- 真实构建失败的消息**会**点名出错表达式（例：`operator does not exist: text / integer` +
  `LINE 20: amount / 100 as amount`）——今天确定性解算器正是靠该文本令牌判别；T13 要替换这种依赖。

### 1.2 合同层面的弃答清单（22 个合同的实测统计）

| gap_kind | 次数 | 性质 |
| --- | --- | --- |
| `RELATION_SCHEMA` / `RELATION_DATA_PROFILE` / `RELATION_HISTORY` | 8 | **设计性扣留**（B 变体主动不可读），不是工具能力缺口 |
| `TRANSFORMATION_DEFINITION` | 4 | **工具能力缺口**：转换定义从不公开 |
| `PAYMENT_EVENT_IDENTITY` | 1 | 事件身份族（T13 不做） |
| `INGESTION_WATERMARK` | 2 | 摄入水位族（T13 不做） |

**扣留 ≠ 不可观测**：设计性扣留的可答性不因本任务改变。

### 1.3 判别问题 → 所需事实 → 现状

| 判别问题 | 需要的公开事实 | 今天有？ | 今天的后果 |
| --- | --- | --- | --- |
| 源列类型变了 vs 模型转换的 cast 变了 | 源列**期望类型** + 失败表达式引用的列 | 都没有 | 靠错误消息令牌；同名列/改名后无法归因 |
| 源必填字段为 NULL vs 转换产生 NULL | 转换定义 | 没有 | 源 profile 无空值即弃答 |
| 同名列跨多个上游关系时的归因 | 列级映射（按依赖图解析，不靠名字） | 没有 | 不可能 |

## 2. 最小扩展提案

### 2.1 证据合同 `p1.column_evidence.v1`：两个新事实（**批量签名**，一次调用可请求多个目标）

**批量语义（本版冻结；全部计入 v2 身份）**

- **原子拒绝**：请求中任一目标不可读 → **整次调用拒绝**，不返回任何记录；权威拒绝明细为
  **逐目标的 `target_refusals`**（有序、去重）：
  ```json
  {"target_refusals": [{"target": "B", "code": "NODE_NOT_ALLOWED"},
                       {"target": "C", "code": "NODE_NOT_FOUND"}]}
  ```
  同一批里可并存**不同错误码**（未授权 vs 不在图中），不得压成单一目标码。
- **调用级码仅作概括**：调用级 `error.code` 固定为 `TARGETS_REFUSED`（新码），只概括"本批存在被拒目标"；
  任何见证、报告与缺口匹配都**不得**使用调用级码作为某目标的拒绝理由。
- **全部可读** → 返回逐目标事实记录（与现有工具返回记录元组同构）。
- **顺序**：返回顺序 = 请求顺序（去重后）；**重复目标**按首次出现去重，不报错；**空列表**拒绝
  （新码 `TARGETS_EMPTY`）；**批量上限** 8 个目标（新码 `BATCH_TOO_LARGE`）。
- **计数**：一次调用 = 一次工具尝试（与列表长度无关）；每条返回记录单独登记证据。被拒的批量调用同样
  消耗一次工具尝试。
- **模型可见回执**：拒绝回执同时携带**调用级码 `TARGETS_REFUSED`** 与 `target_refusals` 明细；事实回执
  携带逐项记录。

| 事实 | 工具 | 来源（只读、运行绑定） | 每项字段 | 每项语义 |
| --- | --- | --- | --- | --- |
| `RELATION_SCHEMA_EXPECTATION` | `get_relation_schema_expectation(relation_names: list[str])` | 运行目录内的基线期望快照（§2.4），目标限定在合同 `expectation_relations` | `name`、`expected_data_type`、`expected_nullable`、`ordinal_position`、`baseline_fingerprint` | 关系不在快照 → `known=false` |
| `DBT_NODE_DEFINITION` | `get_dbt_node_definition(node_ids: list[str])` | 该运行产物（§2.4 优先级），目标限定在合同 `definition_nodes ∩ 运行血缘闭包` | `node_id`、`resource_type`、`declared_columns`、`depends_on`、`compiled_sql_sha256`、`compiled_sql`、`complete` | 节点无编译产物 → `known=false`；文本截断/脱敏 → `complete=false`（禁止用作完整映射输入） |

两个事实都不做判别。批量签名是为了在 8 次工具预算内完成验收路径（§4.1 给出逐步清单）。
**v2 与 v1 不是同一预算条件**：单次调用的信息量更高、语义更复杂（原子拒绝、上限、去重、逐目标拒绝明细），
任何报告必须同时写明政策身份不同与这一差异，不得以"仍为 8 次调用"表述为条件逐项相同。

### 2.2 列映射：窄读器 + 显式 UNKNOWN

只读 E2 已暴露的 SQL，且**只解析失败表达式所需的依赖子图**：

- **失败表达式的识别**：以 `node_error` 消息中被点名的表达式（dbt 错误行片段）为唯一候选——按规范化
  空白后的表达式文本在已暴露 SQL 中定位；**命中若为另一命中的子串，按同一表达式处理（只保留最大
  命中）**；无法唯一识别（0 个或 >1 个最大命中）→ UNKNOWN。识别只用于**选定表达式**，不用于列名
  相似度映射。
- **截断感知识别（第 7 版新增，第 6 版 dry run 实测驱动）**：dbt 报告的错误行被**截断**（实测：片段
  64 字符、以 `...` 结尾、可截在标识符中间），join 条件全文因此**从不出现**在消息中。触发条件精确为：
  **规范化后的消息片段以 `...` 结尾（其后为空白或消息结束）且是某候选表达式的真前缀**——该候选即被
  视为命中。片段是前缀、映射用全文：被选定候选的完整 SQL 文本仍来自 E2 归档，**不引入任何相似度或
  片段映射**；最大命中仍按命中跨度包含关系判定，跨度相同或并列 → UNKNOWN（沿用既有
  `UNKNOWN_REASONS`，不新增理由码）。**已记录的边界**：若截断更短、片段同时是两条 join 条件的真前缀
  （如只截到 `customers.customer_...`），两对都会 UNKNOWN → 弃答；方向 fail-closed，接受并记录在案。
  非截断的省略号（如引号字面量内的 `'...'`）不构成标记。
- **依赖子图**：只解析该表达式两侧引用所在的 CTE/别名链（例如 `customers.customer_id` 与
  `customer_orders.customer_id` 各自的来源链）；SQL 中与失败表达式无关的其余连接/聚合**不需要**解析，
  也不影响结论——`customers.sql` 的 `final` 虽是**三方连接**，但失败表达式只涉及其中两侧的依赖子图。
- 子图之外的部分即使形状不受支持，也不触发 UNKNOWN；**子图内**出现不支持形状 → UNKNOWN。

支持形状（按真实 fixture 实测）：

| 形状 | 例 | 首切片 |
| --- | --- | --- |
| 单源别名投影（**改名**） | `id as payment_id`、`user_id as customer_id` | 支持 |
| 算术/转换表达式 | `amount / 100 as amount` | 支持 |
| 多 CTE 链（子图内） | `customer_orders` → `orders` | 支持（按序展开） |
| 两源等值连接（**限定同名列**） | `customers.customer_id = customer_orders.customer_id` | 支持（按依赖图归因） |
| 聚合（子图内） | `sum(...) as x`、`group by` 键 | 支持 |
| 子图内的其他形状（窗口、非等值、宏生成、来源不可判定） | — | **UNKNOWN** |

**UNKNOWN 永不作为任何方向的证据**；`complete=false` 一律按 UNKNOWN。

**切片 2 审计后的实施期收紧（读器契约，全部 fail-closed；"子图之外不触发 UNKNOWN"仍然成立）**

- **完整消费**：读器实际遍历的每个 SELECT 必须被文档化语法**整体**消费。集合运算（`union`/`intersect`/
  `except`）、逗号连接、无 `ON` 的连接、`using` 等非文档化条目形态、子句次序或重复违规 → 整体拒绝
  （`UNSUPPORTED_SELECT`），不允许"保留可识别前缀、丢弃其余"。
- **作用域按 SQL 语义**：书写别名**取代**关系名——`from raw_orders as raw_customers` 之下限定符
  `raw_customers` 指向 `raw_orders`，原关系名不再是合法限定符；不支持的别名形态一律 UNKNOWN。
- **关系身份 = 完整分段 + 引用语义**：按分段匹配，**绝不退化为末段名**，也不做"去引号 + 统一小写"的
  文本归一——未引用的标识符折叠为小写，**引号内保持原样**，且**引号内的点属于名称本身**
  （`analytics."raw.customers"` 是"schema `analytics` 下名为 `raw.customers` 的关系"，不是三段名）。
  因此 `analytics."STG_CUSTOMERS"` ≠ `analytics.stg_customers`，`analytics.raw_orders` ≠
  `other.analytics.raw_orders`。同一关系的两种写法（二段/三段）须由调用方按公开运行元数据**显式**建立
  （例如同一份定义下并列两个键）；无法证明同一来源 → UNKNOWN。起源按 SQL 中实际使用的身份上报，渲染时
  对需要引号的段重新加引号，保证不同身份不会渲染成同一文本。带引号的**列**引用（如 `a."ID"`）属未支持
  形态 → UNKNOWN（列名按小写匹配，假装 `"ID"` 是 `id` 会归因到错误的列）。
- **终止条件**：只有公开节点类型判定为 seed/source 的关系可以终止追溯（调用方显式声明，按完整身份）；
  其余关系缺定义 → UNKNOWN（新固定码 `DEFINITION_MISSING`），不得当作源列。定义与终止声明同时存在时
  以定义为准。
- **表达式完整消费**：词法扫描必须消费整个表达式——未知符号（`::`、`||` 等）、未支持关键字
  （`between`、`like`、窗口关键字等）一律拒绝；**被 `(` 跟随的词是函数调用**，只有文档化函数集
  （`cast`/`coalesce`/`nullif`/`sum`/`min`/`max`/`count`/`avg`）接受，未知函数拒绝——绝不把无法分类的
  词当作列引用。
- **命中边界**：候选表达式必须在消息中以**自身边界**出现；仅出现在更长引用内部的命中
  （`customer_id` ⊂ `customers.customer_id`）是另一个表达式，不构成识别。
- **连接条件形状**：只有"单一等值比较、两侧各为单列引用"支持；非等值（`>`、`<>`）、多条件合取、
  一侧为表达式 → UNKNOWN。
- **UNKNOWN 词表新增一条**：`DEFINITION_MISSING`（关系既无定义也不是公开终止源）。

### 2.3 权限与拒绝见证（本版修订批量语义）

- 合同 v2 新增 `expectation_relations`、`definition_nodes`，**均默认空**；实际可读 = **合同白名单 ∩ 运行约束**
  （E1 ∩ 关系白名单；E2 ∩ 由 `parent_map` 计算的失败节点上游闭包）。
- **v1 合同与 v1 工具面不开放 E1/E2**；越界拒绝用真实码：E1 `RELATION_NOT_ALLOWED`，E2 新增
  `NODE_NOT_ALLOWED`（不在图中仍 `NODE_NOT_FOUND`）；批量形态另加调用级 `TARGETS_REFUSED` 与
  `TARGETS_EMPTY`、`BATCH_TOO_LARGE`。
- **拒绝见证按工具协议身份分流、按逐目标 `(target, code)` 精确匹配（第 5 版关键修订 + 实施期收紧）**：
  分流依据是**冻结的工具面身份**（`EVIDENCE_BATCH_TOOLS`），**不是**"事件是否恰好带拒绝字段"——v1 工具
  不得借新字段切换规则。v2 见证要求四条件同时成立：① 调用级 `error_code == TARGETS_REFUSED`；
  ② 无成功证据（`evidence_ids` 为空）；③ target 出现在该次调用的**实际请求**中；④ `(subject, code)`
  精确等于某条 `target_refusals` 条目。调用级码与"出现在请求列表里"都不构成见证。
  事件层不变量（模型校验）：带拒绝条目 ⇒ 调用级码必为 `TARGETS_REFUSED` 且无证据；`TARGETS_REFUSED`
  只允许出现在批量工具上且至少一条条目。
  例 1：请求 `[可读 A, 禁止 B]` 因 B 被拒 → 只支持 subject=B 的缺口，**绝不支持 A**。
  例 2：同一批中 B 为 `NODE_NOT_ALLOWED`、C 为 `NODE_NOT_FOUND` → 该批只支撑
  `(B, NODE_NOT_ALLOWED)` 与 `(C, NODE_NOT_FOUND)` 两条；**不得**用统一码为 C 证明权限扣留，
  反之亦然。
- **归档承载**：逐目标明细必须进入归档轨迹（`ToolTraceEvent` 新增可选 `target_refusals`，成功调用为空、
  调用级 `error_code` 记 `TARGETS_REFUSED`；属 v2 身份）。**请求编码冻结**：批量调用的请求写入
  `arguments` 的固定键（`relation_names` / `node_ids`），按请求顺序逗号连接（标识符与 dbt unique id
  不含逗号）；空请求不写该键。
- **匹配规则只对 v2 生效**：certification 的 `_receipt_proved` 与 evaluator 的 `_insufficiency_matches`
  对**带 v2 批量工具的运行**按 `target_refusals` 精确匹配；**v1 收据继续按原规则**（`error_code` 等于
  缺口 reason_code 且 subject 出现在参数值中）校验——旧归档因没有新字段而不失去见证。
- 不新增数据库查询：E1 读运行目录快照，E2 读运行产物。

### 2.4 运行绑定与产物完整性（第 3 版重写）

**E1 —— 基线与运行绑定**（保留第 2 版）：`lab.build` 在运行目录写 `baseline_evidence.json`
（按 `expectation_relations` 裁剪的基线列级期望 + `baseline_fingerprint`），摘要记入 `runtime.json` v2；
E1 只读该快照并校验摘要，不符 → `EVIDENCE_INTEGRITY_ERROR`；全局基线被替换不得影响既有运行与重评。

**E2 —— 运行归属与内容一致性分别校验**（本版核心修订）

- **归属绑定（harness 侧，构建时）**：`runtime.json` v2 记录
  `dbt_invocation_id`（取自 run_results `metadata.invocation_id`）与
  `artifacts_sha256`＝{`manifest.json`、`run_results.json`、`compiled_tree`}，其中 `compiled_tree` 为按相对
  路径排序的 (路径, 文件 sha256) 列表摘要。摘要一律对**redaction 之后的最终归档字节**计算，因此在
  redaction 通过之后再写 runtime 记录。
- **构建时不变量（fail-closed）**：harness 在记录前校验 manifest / run_results / compiled 文件对同一节点
  的编译文本一致（同一 canonical 化后逐字节），且 manifest 与 run_results 的 `invocation_id` 相等；
  不成立则**构建失败**，不允许产出内部不一致的运行。
- **E2 调用时校验**（分开的两件事）：
  1. **运行归属**：manifest、run_results 的当前字节摘要须等于 `artifacts_sha256` 所记；manifest 与
     run_results 的 `invocation_id` 须彼此相等且等于 `dbt_invocation_id`；compiled 文件须来自摘要匹配的
     `compiled_tree`。任一不符 → `EVIDENCE_INTEGRITY_ERROR`。
     **旧 manifest + 旧 compiled 一起换入**必然破坏摘要/归属，被拒——列入回归（§4.3）。
  2. **内容一致性**：来源优先级 ① `run_results.compiled_code` → ② manifest `compiled_path` 指向的文件
     （绝对路径，解析限定运行根内、拒符号链接与越界，沿用 `_artifact_path` 语义）→ ③ manifest
     `compiled_code`；三者（存在的）规范化文本互不相同 → `EVIDENCE_INTEGRITY_ERROR`，不静默选边。
     仅剩 manifest 的回退路径**同样**受归属校验（摘要 + invocation），不存在无新鲜度条件的路径。
- **文本完整性（实施期审计修正）**：`MAX_COMPILED_SQL_BYTES`（16 KiB）按 **UTF-8 字节**截断且不切断
  多字节字符（字符切片对非 ASCII 文本会返回数倍上限）。构建时在
  `build_provenance.node_definitions` 记录**逐节点**的归档文本摘要与 `redacted` 标记（脱敏是否改写了
  该文本）；E2 据此判定 `complete` 并校验文本归属，**绝不通过文本内容猜测**。该记录覆盖**本次运行的
  全部编译节点**（构建完整性），与公开 `definition_nodes` 白名单**相互独立**——白名单只约束 E2 的
  可读范围，不得用来裁剪记录（否则普通构建或 B 变体的空白名单会让 runtime 无法通过自身校验）。
  compiled 树（`target/compiled/**`）与 manifest/run_results 内的副本使用**同一套脱敏规则**——否则文件可能保留 JSON 副本
  已脱敏的秘密，且三个来源会相互矛盾。
- 正常缺失（节点未执行、无编译产物）→ `known=false` 的明确 UNKNOWN。

## 3. 对照与身份影响

- 新场景用新 case id 与新合同版本（`observable_evidence.v2`）；老场景文件与摘要不动。
- **v2 联合类型（待裁定点 1，已认可方向）**：v1 独立模型与序列化；验收含旧场景 digest 不变、旧证书可
  加载、六策略身份逐字节不变；`ScenarioSpec` 总 schema 摘要变化如实记录。
- **诊断合同 v1/v2 分离（实施期审计修正）**：v2 缺口词汇不得加入共享的 `UnresolvedEvidence`——那会改变
  `Diagnosis.model_json_schema()`，连带改变 static/no-tool 的策略身份与全部六策略的最终诊断 schema 摘要。
  v2 拥有独立合同：`UnresolvedEvidenceV2`（增两个 kind 与 `NODE_NOT_ALLOWED`）与 `DiagnosisV2`（同形、
  同校验，仅换缺口词表）；v1 模型与 schema 逐字节不变，冻结 manifest 的六策略身份与
  `final_diagnosis_schema_sha256` 以回归逐项钉住。注意：该共享类的**类 docstring 也会进入 schema**，
  说明只能写在注释里。
- 工具面版本 `p1.evidence_tools.v2` = 六工具 + E1/E2 + `NODE_NOT_ALLOWED` / `EVIDENCE_INTEGRITY_ERROR` /
  `TARGETS_REFUSED`（调用级概括）/ `TARGETS_EMPTY` / `BATCH_TOO_LARGE`；**批量语义（原子拒绝、上限 8、
  去重、顺序、计数）与 `ToolTraceEvent.target_refusals` 一并计入 v2 身份**；v1 面逐字节不变。影响面：
  需求 §10.6/§11、MCP 白名单、evaluator gap 词表与 v2 逐目标匹配、身份版本号。
- **条件表述纪律**：v2 的批量工具单次信息量更高，报告与文档必须同时写明"政策身份不同"与"单次调用
  信息量不同"，不得以"工具预算仍为 8 次"表述为与 v1 条件逐项相同。

## 4. 验收（确定性优先，离线先行）

### 4.1 两对场景：可执行规格（第 3 版新增）

**事实更正（第 2 版 T1 描述错误）**：`raw_orders` 只有 `id`，`order_id` 是 `stg_orders` 改名后的名字
（`id as order_id`）；mart 层连接 `orders.order_id = order_payments.order_id` 的两侧分别来自
`raw_orders.id`（改名）与 `raw_payments.order_id`（直通）——**这正是列映射要解决的地方**。

**候选目标（切片 0，逐项登记，待批准；不得以扩大通配范围代替）**

| 候选 | relation.column | mutation | selector | 恢复 | 允许范围 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| T1′ | `raw_customers.id` | `COLUMN_TYPE_CHANGE` integer→text | 无 | `FULL_REFRESH_BASELINE` | 仅 T13 对 1 | 新增目标；分析确认**单一失败节点** `model.jaffle_shop.customers`（仅 customers 消费 stg_customers） |
| T2′ | `raw_orders.user_id` | `COLUMN_TYPE_CHANGE` integer→text | 无 | 同上 | 仅 T13 对 2 | **既有冻结目标**（无需新增登记）；失败节点同为 customers |
| ~~T1~~ | `raw_payments.order_id` | 同上 | 无 | 同上 | — | **已排除（静态依赖分析）**：`orders` 与 `customers` 都会跨 stg_orders/stg_payments 比较 `order_id` → 两个失败节点，违反单一失败前提 |
| ~~T2~~ | `raw_orders.id` | 同上 | 无 | 同上 | — | **已排除（静态依赖分析）**：`stg_orders.order_id` 变 text 后，`orders` 与 `customers`（经 `payments.order_id = orders.order_id`）同时失败 → 两个失败节点 |

**对 1（期望决定性；T1′；失败节点 `model.jaffle_shop.customers`）**

- 故障与表面症状：`raw_customers.id` 变为 text；失败消息点名 `customer_id`——这是**改名后**的名字，
  raw 层叫 `id`（raw_customers）或 `user_id`（raw_orders），消息中**不出现**任何 raw 列名。
- A 变体白名单：现有 schema 工具 `schema_relations = [raw_customers, raw_orders]`；
  E1 `expectation_relations = [raw_customers, raw_orders]`；
  E2 `definition_nodes = [model.jaffle_shop.customers, model.jaffle_shop.stg_customers, model.jaffle_shop.stg_orders]`
  （三者都在 customers 的上游闭包内）。
- 公开证据路径：run_results → 唯一失败节点 customers；node_error → 类型错误表达式
  `customers.customer_id = customer_orders.customer_id`；E2(customers) → join 两侧的 CTE 来源与表达式；
  E2(stg_customers) → `id as customer_id` ⇒ 起源 `raw_customers.id`；E2(stg_orders) → `user_id as customer_id`
  ⇒ 起源 `raw_orders.user_id`；schema+E1(raw_customers) → `id` 观测 text ≠ 期望 integer（偏差）；
  schema+E1(raw_orders) → `user_id` 观测 integer = 期望（无偏差）⇒ **唯一偏差且被关联到失败表达式** →
  `CONFIRMED` / `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`。
- **≤8 次工具调用逐步清单（第 8 版按参考实现实测重写）**：
  1 `get_dbt_run_results`；2 `get_dbt_node_error(customers)`；3 `get_dbt_lineage(customers, upstream)`
  ——**承重推导步骤**：E1/E2 的请求目标 = 公开白名单 ∩ lineage 推导候选（第 7 版把它记为"余量复核"
  是设计清单滞后于实现，见修订记录）；4 `get_relation_schema(raw_customers)`；
  5 `get_relation_schema(raw_orders)`；6 `get_relation_schema_expectation([raw_customers, raw_orders])`；
  7 `get_dbt_node_definition([customers, stg_customers, stg_orders])`；
  8 `get_dbt_lineage(customers, downstream)`（受影响资产扩展）⇒ **A = 8/8**；
  B 变体在 6/7 处被原子拒绝后弃答 ⇒ **7 次**（不取 downstream）。
  模型回合：确定性参考解 0 模型请求；模型驱动策略若复刻该路径，8 次工具调用必须同回合批处理
  （如 4/5 同回合）并加关闭批次与提交才能落回 `request_limit=8` 内，报告中须如实写明该批处理前提。
  `close_obligation` 不消耗工具预算。
- 预算约束：工具调用 A 8/8、B 7/8；模型请求 ≤8、提交重试 2、300 秒，全部在既有预算内，无任何豁免。
  **条件差异（必须写明）**：v2 的 E1/E2 是批量工具，单次信息量高于 v1 的逐目标工具；"仍在 8 次"
  不表示与 v1 预算条件逐项相同——政策身份与单次信息量都不同（§3）。

- B 变体：同一故障与表面症状；`schema_relations` 保持 `[raw_customers, raw_orders]`（观测仍可读）；
  E1 `expectation_relations = []`、E2 `definition_nodes = []`。终态 `INSUFFICIENT_EVIDENCE`，缺口两条，
  均由**真实拒绝收据**支撑：`(RELATION_SCHEMA_EXPECTATION, raw_customers, get_relation_schema_expectation,
  RELATION_NOT_ALLOWED)`、`(DBT_NODE_DEFINITION, model.jaffle_shop.customers, get_dbt_node_definition,
  NODE_NOT_ALLOWED)`（须与拒绝回执的 `target_refusals` 中某条 `(target, code)` **精确匹配**，见 §2.3；
  调用级 `TARGETS_REFUSED` 与请求列表成员均不构成见证）。

**对 2（镜像：偏差落在另一侧起源；T2′，既有目标；失败节点同为 customers）**

- 故障：`raw_orders.user_id` 变为 text；失败消息同样点名 `customer_id`；两侧起源与对 1 相同，但**偏差在
  右起源**（`user_id`）而左起源（`raw_customers.id`）正常——防止把"顺位/首个解析到的关系"当作结论。
- A/B 白名单、证据路径、逐步清单与预算与对 1 同构（节点/关系同名同表），仅期望的偏差侧不同；A 确认
  `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，B 缺口 subject 相同（拒绝码相同）。
- 该对不新增冻结目标；其价值是**方向性反例**：若窄读器或判据只认一侧，对 2 会失败。

**两对的共同充分条件**（§4.2 表）：窄读器按依赖图解析出失败表达式的两侧起源，偏差集合在涉事上游
关系内**恰为该起源列的类型偏差**，且失败为类型错误 → 确认；否则弃答。

**消息形态检查项（第 7 版按 dry run 实测写入）**：实测消息为
`operator does not exist: text = integer` + `LINE <n>: <编译 SQL 行>` + `HINT` + `compiled code at <路径>`；
其中被引用的编译 SQL 行**被截断为 64 字符并以 `...` 结尾**（本次截在 `customer_orders.customer_` 中间），
join 条件全文**不出现**。因此"两侧起源均可解析"这一充分条件**依赖 §2.2 的截断感知识别**：没有它，
读器只会命中投影 `customers.customer_id` 并给出单侧起源，①不成立（见修订记录"认证路径封口"）。
dry run 定论：失败模型单节点、关系测试 skipped、消息点名 `customer_orders` 侧（**风险 A**，不是风险 B）。

### 4.2 公开事实 → 可确认结论：充分条件表

| 结论 | 充分条件（全部满足） | 反例（必须弃答） |
| --- | --- | --- |
| `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED` | ① 窄读器把失败表达式两侧引用解析到具体关系列（按依赖图，非相似度）；② E1 显示其中一列观测类型 ≠ 期望；③ 涉事上游关系内偏差集合**恰为**该列；④ 失败是类型错误且该列出现在该表达式 | 无关列的第二个偏差（破坏唯一性）；类型偏差无法关联到失败表达式；`complete=false`/UNKNOWN |
| `SOURCE_REQUIRED_FIELD_NULL` | 维持现状（T13 不改该证据链） | — |
| 列缺失（**不判改名**） | 只能确认"引用的列在观测与期望中都不存在"这一事实 | **删除旧列 + 新增另一列与改名公开观测完全相同**；SQL 映射无法证明改名 → 弃答 |
| 转换侧结论（`TRANSFORMATION_*`） | 仅当 E2 完整且读器给出确定映射时，才允许作为弃答缺口说明中的备选 | 读器 UNKNOWN / `complete=false` → 不得声明 |

**负面结论（如实交付）**：E1+E2 不足以确认列改名；确认改名需另行设计可信变更证据（DDL/变更日志族），
不在本任务范围，也不得以私有 mutation 类别排除反例。

### 4.3 端到端与回归

- 认证 + 准入；公开证据驱动脚本（零案例配置）跑真实证据链；B 缺口在归档中逐条复现（含拒绝码）；
  `score_run_offline` 与在线评测逐字段一致。
- **构造 dry run（切片 0）**：登记前先验证两对确实为单一失败节点、消息形态如预期；若不成立，如实
  重设计或按负面结论处理，不得以既有场景数据顶替。
- **反例回归（离线单元）**：删除+新增不判改名；非唯一偏差不判根因；无表达式关联的类型偏差不判根因；
  `complete=false`/UNKNOWN 不作证据。
- **完整性回归**：E1 摘要不符 → `EVIDENCE_INTEGRITY_ERROR`；E2 来源冲突 → 同码；**旧 manifest + 旧
  compiled 成对换入 → 被归属校验拒绝**；仅 manifest 回退路径的归属同样被校验；替换全局基线后重评不变。
- **匹配回归（见证精确性）**：**混合权限**——请求 `[可读 A, 禁止 B]` 的拒绝只支撑 subject=B 的缺口，
  **绝不支撑 A**；**混合错误码**——同批中 B 为 `NODE_NOT_ALLOWED`、C 为 `NODE_NOT_FOUND` 时，只支撑
  `(B, NODE_NOT_ALLOWED)` 与 `(C, NODE_NOT_FOUND)`，声明 `(C, NODE_NOT_ALLOWED)` 或
  `(B, NODE_NOT_FOUND)` 的缺口必须失败；`target_refusals` 与归档轨迹一致；**v1/v2 分流**——v1 收据
  仍按原规则校验，旧归档加载后见证不减少；批量语义逐项——空列表 → `TARGETS_EMPTY`、超上限 →
  `BATCH_TOO_LARGE`、重复目标去重且顺序为首次出现、返回顺序等于请求顺序、一次调用计一次工具尝试。
- **子图回归**：`customers.sql` 的三方连接中，失败表达式只依赖其中两侧时读器正常工作（不因无关的
  第三侧触发 UNKNOWN）；子图内不支持形状 → UNKNOWN。
- **身份回归**：六工具 v1 身份逐字节不变；旧场景 digest 不变；旧证书加载。T05 九条回放不变。

## 5. 边界

- `RELATION_*` 设计性扣留可答性不变；不迁移旧六工具场景与 NoSchema 消融；旧结果不重算。
- `PAYMENT_EVENT_IDENTITY`、`INGESTION_WATERMARK`、列改名的可信变更证据均不属 T13。
- 真实模型测量、新 manifest 冻结、T14 跨任务纵切不在本文范围。

## 6. 实施切片（第 6 版：切片 3/4 边界重排后的实际顺序）

0. 管理平面与前置：T1′ 目标登记 + T2′ 复用确认；合同 v2 校验器；`target_refusals` 归档字段与 v2
   逐目标匹配（v1 保持原规则）改造；——**已完成（离线）**；
1. 事实层：E1/E2 批量工具（原子拒绝、上限/去重/顺序/计数冻结）、运行上下文 v2（含
   `dbt_invocation_id` 与 `artifacts_sha256`，redaction 之后
   记录）、两个新固定码、六工具 v1 身份回归；——**已完成（离线）**；
2. 读器：窄列映射读器 + 形状矩阵单测（含全部反例与 UNKNOWN 负例）；——**已完成（离线，三轮审计修复）**；
3. 场景：两对 A/B **落盘与离线合同/对称性回归**；——**已完成（离线，`b62289d`）**；
   **认证与准入顺延到切片 4 之后**（依赖 v2 工具面，见修订记录）；
4. 策略面：`p1.evidence_tools.v2` 接入（参考解与规划器）+ 端到端、归档与离线重评验收。
   **前置授权项：数据库 dry run**（构造验证，见修订记录与 dry run 计划文档）；未完成前不得进入
   认证与准入。——**已完成**（dry run 三轮收口；切片 4 四个增量 + 三次授权内修复
   `469ef04`/`6ede223`/`d008856`；第四次执行 4/4 认证准入、C1–C8 成立，见结果报告 §9）。
