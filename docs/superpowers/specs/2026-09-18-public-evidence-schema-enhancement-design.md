# T13 设计：可公开验证的 schema / 转换证据增强（候选）

- 日期：2026-09-18。状态：**设计待审（第 2 版，按审计意见修订权限模型、充分条件与产物完整性）**；不进入实施。
- 依据：改进计划 T13（"先设计可信列映射/类型期望的公共证据合同，再考虑可选工具或工具结果新版本"）；
  T12 收口结论；本稿第 1 版（`800dfac`）的审计意见。
- 边界：不改 kernel/static 的既有身份与历史结果，不冻结 manifest，不做真实模型测量；T14 跨任务另行。

## 1. 证据缺口（现状实测）

### 1.1 今天的公开面提供什么

六个只读事实：`DBT_RUN_RESULTS`、`DBT_NODE_ERROR`、`RELATION_SCHEMA`、`DBT_LINEAGE`、
`RELATION_DATA_PROFILE`、`RELATION_HISTORY`。与"类型/转换"相关的实测：

- `get_relation_schema` 读**实时** `information_schema.columns` 并要求与运行快照逐列相等，否则
  `RunStateDriftError`——它给的是**观测**，诊断面上没有任何**期望**。
- `get_dbt_lineage` 只有**节点级**上下游与距离。
- 运行目录已有 `dbt/target/compiled/**`、manifest 的 `compiled_code`/`columns`/`depends_on`、
  `run_results` 的失败消息与 `compiled_code`——但都不在证据面内。
- 可信健康基线（`.dig/baseline-summary.json`，带 fingerprint）含每列 `data_type/nullable/ordinal_position`，
  只服务管理平面。
- 实测行为参照：真实构建失败的报错消息**会**点名出错表达式（例：`operator does not exist: text / integer`
  + `LINE 20: amount / 100 as amount`）——今天确定性解算器正是靠该文本令牌判别；T13 要替换掉这种依赖。

### 1.2 合同层面的弃答清单（22 个合同的实测统计）

| gap_kind | 次数 | 性质 |
| --- | --- | --- |
| `RELATION_SCHEMA` / `RELATION_DATA_PROFILE` / `RELATION_HISTORY` | 8 | **设计性扣留**（B 变体主动不可读），不是工具能力缺口 |
| `TRANSFORMATION_DEFINITION` | 4 | **工具能力缺口**：转换定义从不公开 |
| `PAYMENT_EVENT_IDENTITY` | 1 | 事件身份族（T13 不做，见 §5） |
| `INGESTION_WATERMARK` | 2 | 摄入水位族（T13 不做，见 §5） |

**扣留 ≠ 不可观测**：设计性扣留的可答性不因本任务改变；补强只针对"工具面本可提供、今天没有"的事实。

### 1.3 判别问题 → 所需事实 → 现状

| 判别问题 | 需要的公开事实 | 今天有？ | 今天的后果 |
| --- | --- | --- | --- |
| 源列类型变了 vs 模型转换的 cast 变了 | 源列**期望类型** + 失败表达式引用的列 | 都没有 | 靠错误消息令牌；消息不点名或指向同名列时无法归因 |
| 源必填字段为 NULL vs 转换产生 NULL | 转换定义（模型读取/投影的列） | 没有 | 源 profile 无空值即弃答 |
| 同名列跨多个上游关系时的归因 | 列级映射（按依赖图解析，不靠名字） | 没有 | 不可能，只能整体弃答 |
| 列被改名 vs 删除旧列+新增其他列 | **两者公开观测相同**（见 §4.2） | 没有可信变更证据 | 保留弃答（T13 给出负面结论，不新增猜测路径） |

## 2. 最小扩展提案

### 2.1 证据合同 `p1.column_evidence.v1`：两个新事实，只陈述事实、不给结论

| 事实 | 工具 | 来源（只读、运行绑定） | 字段 | UNKNOWN 语义 |
| --- | --- | --- | --- | --- |
| `RELATION_SCHEMA_EXPECTATION` | `get_relation_schema_expectation(relation_name)` | 运行目录内的基线期望快照（见 §2.4），关系限定在合同 `expectation_relations` | `name`、`expected_data_type`、`expected_nullable`、`ordinal_position`、`baseline_fingerprint` | 关系不在快照 → `known=false` |
| `DBT_NODE_DEFINITION` | `get_dbt_node_definition(node_id)` | 该运行产物（`run_results` / `target/compiled` / manifest，优先级见 §2.4），节点限定在合同 `definition_nodes ∩ 运行血缘闭包` | `node_id`、`resource_type`、`declared_columns`、`depends_on`、`compiled_sql_sha256`、`compiled_sql`、`complete` | 该节点无任何编译产物 → `known=false`；文本被截断/脱敏 → `complete=false`（禁止用作完整映射输入） |

两个事实都**不做判别**：判别属于策略与确定性窄读器。

### 2.2 列映射：窄读器 + 显式 UNKNOWN（不用相似度猜）

列映射是确定性侧的**窄读器**，只读 E2 的已暴露 SQL，且只支持按真实 fixture 实测列出的形状：

| 形状 | 例（fixture 实测） | 首切片 |
| --- | --- | --- |
| 单源别名投影 | `id as payment_id`、`user_id as customer_id` | 支持 |
| 算术/转换表达式 | `amount / 100 as amount` | 支持 |
| 多 CTE 链 | `orders` → `payments` → `order_payments` → `final` | 支持（按序展开，单源或两源连接） |
| 两源等值连接 + 限定同名列 | `orders.order_id = order_payments.order_id` | 支持（按依赖图归因，不按列名相似度） |
| 聚合 | `sum(case when … end) as credit_card_amount` | 支持（映射到分组键 + 表达式引用列） |
| 其他（窗口、非等值连接、三源以上、宏生成、来源不可判定） | — | **UNKNOWN** |

**UNKNOWN 永不作为任何方向的证据**；`complete=false` 的 E2 一律按 UNKNOWN 处理。

### 2.3 权限（修订：两个白名单，v1 不开放）

- 合同 v2 新增两个列表，**均默认空**：`expectation_relations`（E1）与 `definition_nodes`（E2）。
- 实际可读集合：**合同白名单 ∩ 运行约束**。E1 = `expectation_relations` ∩ 该运行的**关系白名单**
  （沿用现有 `observable_relations` 校验路径）；E2 = `definition_nodes` ∩ **该运行的血缘闭包**
  （失败节点及其上游闭包，由 manifest 的 `parent_map` 计算）。
- **v1 合同与 v1 工具面不开放 E1/E2**：老场景与新证据零交集。
- 越界拒绝使用**真实错误码**：E1 沿用 `RELATION_NOT_ALLOWED`；E2 新增 `NODE_NOT_ALLOWED`
  （证据面新增固定码，纳入版本身份；节点不在运行图中仍用 `NODE_NOT_FOUND`）。
- 缺口必须由**真实拒绝收据**支撑：每个带 `tool_name` 的 gap 需恰好一次对应工具的真实拒绝
  （沿用 evaluator/certification 的 `_receipt_proved` 规则）。
- 不新增数据库查询：E1 读运行目录内的基线期望快照，E2 读运行产物。

### 2.4 运行绑定与产物完整性（修订：本稿新增）

**E1 —— 基线与运行绑定，防止"后来替换的全局基线"**

- `lab.build` 在运行目录写入**基线期望快照**（`baseline_evidence.json`），内容为可信基线的列级期望
  按合同 `expectation_relations` 裁剪后的副本，附 `baseline_fingerprint`；快照 sha256 记入
  `runtime.json`（上下文升 `p1.runtime.v2`）。
- E1 **只读运行目录快照**，不读全局 `.dig/baseline-summary.json`；读取时校验快照 sha256 与
  `runtime.json` 记录一致，不一致 → **完整性报错**（新固定码 `EVIDENCE_INTEGRITY_ERROR`），不是 UNKNOWN。
- 离线重评只依赖归档：事后替换全局基线**不得**改变任何既有运行的重评结果（回归要求，见 §4.3）。

**E2 —— 来源优先级、冲突、过期与文本完整性**

- 来源优先级（冻结）：① `run_results.results[unique_id].compiled_code`（本次运行**实际执行**的文本）；
  ② manifest `compiled_path` 指向的 `target/compiled/**` 文件（`compiled_path` 实测是绝对路径，解析必须
  限定在运行根内、拒绝符号链接与越界路径，沿用现有 `_artifact_path` 语义）；③ manifest `compiled_code`。
- 冲突：存在 ≥2 个来源且其规范化文本 sha256 不同 → **完整性报错**，不静默选边。
- 过期：仅当回退到 ② 时，文件内容必须与 manifest 记录的 `compiled_code` 摘要一致；不一致即判过期 →
  完整性报错。
- 文本完整性：设上限 `MAX_COMPILED_SQL_BYTES`（建议 16 KiB）；超限或既有 redaction 改变文本
  （摘要随之变化）→ 事实带 `complete=false`，**禁止作为完整映射输入**，读器一律 UNKNOWN；两个摘要
  均记录以便离线复核。
- 正常缺失（节点未执行、无任何编译产物）→ `known=false` 的明确 UNKNOWN。

## 3. 对照与身份影响

- 新场景使用新 case id 与新合同版本（`observable_evidence.v2`）；老场景文件与摘要一个字节不动。
- **v2 联合类型（待裁定点 1，同意方向，附条件）**：v1 保持独立模型与序列化；验收包含——旧场景
  digest 不变、旧认证/准入证书可加载、六策略身份逐字节不变（回归钉住）；`ScenarioSpec` 总 schema
  摘要变化如实记录（已知且有意的漂移轴）。
- 工具面版本：`p1.evidence_tools.v2` = 六工具 + E1/E2 + `NODE_NOT_ALLOWED`/`EVIDENCE_INTEGRITY_ERROR`
  两个新固定码；v1 面逐字节不变。
- 影响面：需求 §10.6/§11、MCP 白名单、evaluator gap 词表（新增 `evidence_kind` 字符串）、身份版本号。

## 4. 验收（确定性优先，离线先行）

### 4.1 新 A/B 对（2 对 = 4 变体，dev 扩展回归集）

每对共享故障与表面症状，仅可答性不同；**每个变体的 E1/E2 白名单、缺口 subject、工具与真实拒绝码
逐一列出**：

| 变体 | E1 `expectation_relations` | E2 `definition_nodes` | 期望终态 | 缺口（kind, subject, tool, 拒绝码） |
| --- | --- | --- | --- | --- |
| 对 1-A | `[raw_payments]` 等（含决定性关系） | `[model.jaffle_shop.stg_payments]` | `CONFIRMED`（充分条件见 §4.2） | — |
| 对 1-B | `[]` | `[]` | `INSUFFICIENT_EVIDENCE` | `(RELATION_SCHEMA_EXPECTATION, raw_payments, get_relation_schema_expectation, RELATION_NOT_ALLOWED)`；`(DBT_NODE_DEFINITION, model.jaffle_shop.stg_payments, get_dbt_node_definition, NODE_NOT_ALLOWED)` |
| 对 2-A | 含决定性关系 | 含决定性节点 | `CONFIRMED`（充分条件见 §4.2） | — |
| 对 2-B | `[]` | `[]` | `INSUFFICIENT_EVIDENCE` | 同上两项（拒绝码按实际工具归属） |

（B 变体两个缺口均来自**真实拒绝收据**；`ScenarioSpec` 对 insufficient 变体本就要求 ≥2 个缺口。）

**故障构造（切片 0，逐项登记、待批准，不得以扩大通配范围代替）**

| 候选 | relation.column | mutation | selector | 恢复 | 允许范围 |
| --- | --- | --- | --- | --- | --- |
| T1 | `raw_payments.order_id` | `COLUMN_TYPE_CHANGE` integer→text | 无（整列类型变更） | `FULL_REFRESH_BASELINE` | 仅 T13 新场景 |
| T2 | `raw_orders.id` | `COLUMN_TYPE_CHANGE` integer→text | 无 | `FULL_REFRESH_BASELINE` | 仅 T13 新场景 |

- T1 的意图：失败表达式 `orders.order_id = order_payments.order_id` 涉及**跨上游同名列**
  （`raw_orders` 与 `raw_payments` 都有 `order_id`），考察"按依赖图归因、不按列名相似度"。
- T2 的意图：决定性列不在失败模型的直接表达式中出现，考察"列级映射 + 期望比对"而非消息令牌。
- 两个候选都只**新增**冻结目标；既有 mutation 校验器接受的集合只做加法，老场景文件与摘要不变。
- 构造是否成立（旧六工具面确实无法给出同一结论）需在切片 0 用一次 dry run 验证并记录；若不成立，
  如实降级该对的声明（改为"新路径正确性验证"而不是"新证据必要性验证"），或按负面结论处理。

### 4.2 公开事实 → 可确认结论：充分条件表（本稿新增）

| 结论 | 充分条件（全部满足） | 反例（必须弃答） |
| --- | --- | --- |
| `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED` | ① 窄读器把失败表达式的引用解析到关系 R 的列 C（非相似度）；② E1 显示 R.C 的观测类型 ≠ 期望类型；③ 在**所有涉事上游关系**内，观测 vs 期望的偏差集合**恰为** `{C 类型偏差}`（唯一性）；④ 失败是类型错误（operator/cast 类），且 C 出现在该表达式中 | 存在第二个无关列的类型漂移（破坏唯一性）→ 弃答；类型偏差存在但无法关联到失败表达式 → 弃答；E2 `complete=false` / 读器 UNKNOWN → 弃答 |
| `SOURCE_REQUIRED_FIELD_NULL` | 维持现状：profile 空值 + 现有规则（T13 不改变该结论的证据链） | — |
| 列缺失（**不判改名**） | 只能确认"模型引用的列在观测 schema 与期望集合中都不存在"这一事实本身；**不**自动等于 `SOURCE_SCHEMA_COLUMN_RENAMED` | **删除旧列 + 新增另一列与改名产生完全相同的公开观测**（旧列缺失 + 新列出现）；SQL 映射无法证明"同一列被改名"。删除+新增、改名混淆、映射 UNKNOWN 一律弃答 |
| 转换侧结论（`TRANSFORMATION_*`） | 仅在 E2 完整且窄读器给出确定映射时，才允许作为**备选解释**出现在弃答的缺口说明中 | 读器 UNKNOWN / `complete=false` → 不得声明 |

**T13 的负面结论（如实交付）**：现有公开事实（E1+E2）**不足以确认列改名**；要确认改名需要另行设计
"可信变更证据"（如经审核的 DDL/变更日志族），不在本任务范围，也不得以私有 mutation 类别排除反例。

### 4.3 端到端与回归

- 认证 + 准入（`certify --admit`）；公开证据驱动脚本（零案例配置）跑真实证据链；B 变体缺口在归档中
  逐条复现（含真实拒绝码）；`score_run_offline` 与在线评测逐字段一致。
- **反例回归（离线，单元级）**：删除+新增观测不得判改名；非唯一偏差不得判根因；无表达式关联的类型
  偏差不得判根因；`complete=false` 与 UNKNOWN 不得作为证据。
- **完整性回归**：E1 快照摘要不符 → `EVIDENCE_INTEGRITY_ERROR`；E2 来源冲突/过期 → 同样报错；
  **替换全局基线后重评结果不变**。
- **身份回归**：六工具 v1 身份逐字节不变；旧场景 digest 不变；旧证书可加载。
- T05 九条回放全部保持通过。

## 5. 边界

- 不改 `RELATION_*` 设计性扣留的可答性；不迁移旧六工具场景与 NoSchema 消融；旧结果不重算。
- `PAYMENT_EVENT_IDENTITY`、`INGESTION_WATERMARK`、列改名的可信变更证据均不属 T13。
- 真实模型测量、新 manifest 冻结、T14 跨任务纵切不在本文范围。

## 6. 实施切片（设计通过后）

0. 管理平面：§4.1 候选目标逐项登记（dry run 验证构造）；合同 v2 校验器；
1. 事实层：E1/E2、运行上下文 v2 与基线期望快照、两个新固定码、六工具 v1 身份回归；
2. 读器：窄列映射读器 + 形状矩阵单测（含全部反例与 UNKNOWN 负例）；
3. 场景：新 A/B 对落盘、认证与准入；
4. 策略面：`p1.evidence_tools.v2` 接入（参考解与规划器）+ 端到端、归档与离线重评验收。
