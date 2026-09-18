# T13 设计：可公开验证的 schema / 转换证据增强（候选）

- 日期：2026-09-18。状态：**设计待审（第 4 版：冻结批量拒绝见证语义，限定读器只解析失败表达式的依赖子图）**；不进入实施。
- 依据：改进计划 T13；T12 收口结论；第 2 版（`c2be701`）的审计意见。
- 边界：不改 kernel/static 的既有身份与历史结果，不冻结 manifest，不做真实模型测量；T14 跨任务另行。

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

- **原子拒绝**：请求中任一目标不可读 → **整次调用拒绝**，不返回任何记录；拒绝记录必须**逐目标**给出被
  后端明确拒绝的 target 与真实错误码（§2.3）。
- **全部可读** → 返回逐目标事实记录（与现有工具返回记录元组同构）。
- **顺序**：返回顺序 = 请求顺序（去重后）；**重复目标**按首次出现去重，不报错；**空列表**拒绝
  （新码 `TARGETS_EMPTY`）；**批量上限** 8 个目标（新码 `BATCH_TOO_LARGE`）。
- **计数**：一次调用 = 一次工具尝试（与列表长度无关）；每条返回记录单独登记证据。被拒的批量调用同样
  消耗一次工具尝试。
- **模型可见回执**：拒绝回执携带 `refused_targets`（有序、去重）与统一错误码；事实回执携带逐项记录。

| 事实 | 工具 | 来源（只读、运行绑定） | 每项字段 | 每项语义 |
| --- | --- | --- | --- | --- |
| `RELATION_SCHEMA_EXPECTATION` | `get_relation_schema_expectation(relation_names: list[str])` | 运行目录内的基线期望快照（§2.4），目标限定在合同 `expectation_relations` | `name`、`expected_data_type`、`expected_nullable`、`ordinal_position`、`baseline_fingerprint` | 关系不在快照 → `known=false` |
| `DBT_NODE_DEFINITION` | `get_dbt_node_definition(node_ids: list[str])` | 该运行产物（§2.4 优先级），目标限定在合同 `definition_nodes ∩ 运行血缘闭包` | `node_id`、`resource_type`、`declared_columns`、`depends_on`、`compiled_sql_sha256`、`compiled_sql`、`complete` | 节点无编译产物 → `known=false`；文本截断/脱敏 → `complete=false`（禁止用作完整映射输入） |

两个事实都不做判别。批量签名是为了在 8 次工具预算内完成验收路径（§4.1 给出逐步清单）。
**v2 与 v1 不是同一预算条件**：单次调用的信息量更高、语义更复杂（原子拒绝、上限、去重、拒绝目标集），
任何报告必须同时写明政策身份不同与这一差异，不得以"仍为 8 次调用"表述为条件逐项相同。

### 2.2 列映射：窄读器 + 显式 UNKNOWN

只读 E2 已暴露的 SQL，且**只解析失败表达式所需的依赖子图**：

- **失败表达式的识别**：以 `node_error` 消息中被点名的表达式（dbt 错误行片段）为唯一候选——按规范化
  空白后的表达式文本在已暴露 SQL 中定位；无法唯一识别（0 或 >1 个匹配）→ UNKNOWN。识别只用于**选定
  表达式**，不用于列名相似度映射。
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

### 2.3 权限与拒绝见证（本版修订批量语义）

- 合同 v2 新增 `expectation_relations`、`definition_nodes`，**均默认空**；实际可读 = **合同白名单 ∩ 运行约束**
  （E1 ∩ 关系白名单；E2 ∩ 由 `parent_map` 计算的失败节点上游闭包）。
- **v1 合同与 v1 工具面不开放 E1/E2**；越界拒绝用真实码：E1 `RELATION_NOT_ALLOWED`，E2 新增
  `NODE_NOT_ALLOWED`（不在图中仍 `NODE_NOT_FOUND`）；批量形态另加 `TARGETS_EMPTY`、`BATCH_TOO_LARGE`。
- **拒绝见证只认被明确拒绝的 target（关键修订）**：批量调用原子拒绝，拒绝回执携带
  `refused_targets`（逐目标、有序、去重）与该次错误码；**缺口 `(kind, subject, tool, code)` 成立的
  充要条件是 `subject ∈ refused_targets` 且 `code` 等于该次拒绝码**——仅"出现在请求列表里"不构成见证。
  例：请求 `[可读 A, 禁止 B]` 因 B 被拒 → 只支持 subject=B 的缺口，**绝不支持 A**。
- **归档承载**：拒绝目标的逐目标信息必须进入归档轨迹（`ToolTraceEvent` 新增可选 `refused_targets`，
  成功调用为空；v2 身份的一部分），certification 的 `_receipt_proved` 与 evaluator 的
  `_insufficiency_matches` 相应改为按 `refused_targets` 匹配（带回归；不再使用"参数列表成员"匹配）。
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
- **文本完整性**：`MAX_COMPILED_SQL_BYTES`（建议 16 KiB）；超限或 redaction 改变文本 → `complete=false`，
  禁止作为完整映射输入，两个摘要均记录。
- 正常缺失（节点未执行、无编译产物）→ `known=false` 的明确 UNKNOWN。

## 3. 对照与身份影响

- 新场景用新 case id 与新合同版本（`observable_evidence.v2`）；老场景文件与摘要不动。
- **v2 联合类型（待裁定点 1，已认可方向）**：v1 独立模型与序列化；验收含旧场景 digest 不变、旧证书可
  加载、六策略身份逐字节不变；`ScenarioSpec` 总 schema 摘要变化如实记录。
- 工具面版本 `p1.evidence_tools.v2` = 六工具 + E1/E2 + `NODE_NOT_ALLOWED` / `EVIDENCE_INTEGRITY_ERROR` /
  `TARGETS_EMPTY` / `BATCH_TOO_LARGE`；**批量语义（原子拒绝、上限 8、去重、顺序、计数）与
  `ToolTraceEvent.refused_targets` 一并计入 v2 身份**；v1 面逐字节不变。影响面：需求 §10.6/§11、
  MCP 白名单、evaluator gap 词表与按 `refused_targets` 的匹配、身份版本号。
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
- **≤8 次工具调用逐步清单**：
  1 `get_dbt_run_results`；2 `get_dbt_node_error(customers)`；3 `get_relation_schema(raw_customers)`；
  4 `get_relation_schema(raw_orders)`；5 `get_relation_schema_expectation([raw_customers, raw_orders])`；
  6 `get_dbt_node_definition([customers, stg_customers, stg_orders])` ⇒ **6 次**（余 2 次可留给
  `get_dbt_lineage` 复核闭包）。模型回合：6 + 关闭批次 1 + 提交 1 = 8，正好在 `request_limit=8` 内；可再
  把 3/4 或 2/5 同回合批处理留出余量。`close_obligation` 不消耗工具预算。
- 预算约束：工具 8/8、模型请求 8/8、提交重试 2、300 秒，全部在既有预算内，无任何豁免。
  **条件差异（必须写明）**：v2 的 E1/E2 是批量工具，单次调用信息量高于 v1 的逐目标工具；"仍为 8 次"
  不表示与 v1 预算条件逐项相同——政策身份与单次信息量都不同（§3）。

- B 变体：同一故障与表面症状；`schema_relations` 保持 `[raw_customers, raw_orders]`（观测仍可读）；
  E1 `expectation_relations = []`、E2 `definition_nodes = []`。终态 `INSUFFICIENT_EVIDENCE`，缺口两条，
  均由**真实拒绝收据**支撑：`(RELATION_SCHEMA_EXPECTATION, raw_customers, get_relation_schema_expectation,
  RELATION_NOT_ALLOWED)`、`(DBT_NODE_DEFINITION, model.jaffle_shop.customers, get_dbt_node_definition,
  NODE_NOT_ALLOWED)`（subject 须出现在该次拒绝回执的 `refused_targets` 中，见 §2.3；仅出现在请求列表
  中不构成见证）。

**对 2（镜像：偏差落在另一侧起源；T2′，既有目标；失败节点同为 customers）**

- 故障：`raw_orders.user_id` 变为 text；失败消息同样点名 `customer_id`；两侧起源与对 1 相同，但**偏差在
  右起源**（`user_id`）而左起源（`raw_customers.id`）正常——防止把"顺位/首个解析到的关系"当作结论。
- A/B 白名单、证据路径、逐步清单与预算与对 1 同构（节点/关系同名同表），仅期望的偏差侧不同；A 确认
  `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，B 缺口 subject 相同（拒绝码相同）。
- 该对不新增冻结目标；其价值是**方向性反例**：若窄读器或判据只认一侧，对 2 会失败。

**两对的共同充分条件**（§4.2 表）：窄读器按依赖图解析出失败表达式的两侧起源，偏差集合在涉事上游
关系内**恰为该起源列的类型偏差**，且失败为类型错误 → 确认；否则弃答。

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
  **绝不支撑 A**；一次原子拒绝的 `refused_targets` 与归档轨迹一致；批量语义逐项——空列表 →
  `TARGETS_EMPTY`、超上限 → `BATCH_TOO_LARGE`、重复目标去重且顺序为首次出现、返回顺序等于请求顺序、
  一次调用计一次工具尝试。
- **子图回归**：`customers.sql` 的三方连接中，失败表达式只依赖其中两侧时读器正常工作（不因无关的
  第三侧触发 UNKNOWN）；子图内不支持形状 → UNKNOWN。
- **身份回归**：六工具 v1 身份逐字节不变；旧场景 digest 不变；旧证书加载。T05 九条回放不变。

## 5. 边界

- `RELATION_*` 设计性扣留可答性不变；不迁移旧六工具场景与 NoSchema 消融；旧结果不重算。
- `PAYMENT_EVENT_IDENTITY`、`INGESTION_WATERMARK`、列改名的可信变更证据均不属 T13。
- 真实模型测量、新 manifest 冻结、T14 跨任务纵切不在本文范围。

## 6. 实施切片（设计通过后）

0. 管理平面与前置：T1′ 目标登记 + T2′ 复用确认；两对 dry run 构造验证（含失败表达式在消息中的
   唯一识别）；合同 v2 校验器；`refused_targets` 归档字段与按此匹配的认证/评测改造；
1. 事实层：E1/E2 批量工具（原子拒绝、上限/去重/顺序/计数冻结）、运行上下文 v2（含
   `dbt_invocation_id` 与 `artifacts_sha256`，redaction 之后
   记录）、两个新固定码、六工具 v1 身份回归；
2. 读器：窄列映射读器 + 形状矩阵单测（含全部反例与 UNKNOWN 负例）；
3. 场景：两对 A/B 落盘、认证与准入；
4. 策略面：`p1.evidence_tools.v2` 接入（参考解与规划器）+ 端到端、归档与离线重评验收。
