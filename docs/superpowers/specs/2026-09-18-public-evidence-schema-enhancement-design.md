# T13 设计：可公开验证的 schema / 转换证据增强（候选）

- 日期：2026-09-18。状态：**设计待审**；本文只定义证据缺口、最小扩展、权限与验收，不进入实施。
- 依据：改进计划 T13（"先设计可信列映射/类型期望的公共证据合同，再考虑可选工具或工具结果新版本"）；
  T12 收口结论（[2026-09-18-t12-variant-materialization.md](../reports/2026-09-18-t12-variant-materialization.md)）。
- 边界：不改 kernel/static 的既有身份与历史结果，不冻结 manifest，不做真实模型测量；T14 跨任务另行。

## 1. 证据缺口（现状实测）

### 1.1 今天的公开面提供什么

六个只读事实（`EvidenceType`）：`DBT_RUN_RESULTS`、`DBT_NODE_ERROR`、`RELATION_SCHEMA`、`DBT_LINEAGE`、
`RELATION_DATA_PROFILE`、`RELATION_HISTORY`。与"类型/转换"相关的三点实测：

- `get_relation_schema` 读**实时** `information_schema.columns`，并要求与运行快照（`schema.json`，注入后
  的观测）逐列相等，否则 `RunStateDriftError`。即：它给的是**观测**，系统里没有任何面向诊断面的**期望**。
- `get_dbt_lineage` 只有**节点级**上下游与距离，没有列级信息。
- 运行目录里其实已有可用材料：`dbt/target/compiled/**`（编译后 SQL）、manifest 节点的
  `compiled_code`/`raw_code`/`columns`/`depends_on`、`run_results` 的失败消息——但都不在
  `_EXPECTED_ARTIFACTS` 的证据面内（`run_context.py`）。
- 可信健康基线（`.dig/baseline-summary.json`，`make_baseline_summary` 产出并带 fingerprint `e5c78…`）
  含每个关系每列的 `data_type/nullable/ordinal_position`，目前只服务管理平面（lab / verifier）。

### 1.2 合同层面的弃答清单（22 个合同的实测统计）

| gap_kind | 次数 | 性质 |
| --- | --- | --- |
| `RELATION_SCHEMA` / `RELATION_DATA_PROFILE` / `RELATION_HISTORY` | 8 | **设计性扣留**（B 变体主动不可读），不是工具能力缺口 |
| `TRANSFORMATION_DEFINITION` | 4 | **工具能力缺口**：转换定义从不公开（`schema_type_change_order_customer_b`、`type_change_payment_amount_drift_b`、两个 required-null B） |
| `PAYMENT_EVENT_IDENTITY` | 1 | 事件身份族（T13 不做，见 §5） |
| `INGESTION_WATERMARK` | 2 | 摄入水位族（T13 不做，见 §5） |

关键区分：**扣留 ≠ 不可观测**。T13 不改变设计性扣留的可答性——那些 B 变体在补强后仍应合格弃答
（只是缺口词表可能更新），补强只针对"工具面本来能提供、但今天没有"的事实。

### 1.3 能力缺口的具体形态

- `TRANSFORMATION_COLUMN_CAST_CHANGED` 与 `TRANSFORMATION_REQUIRED_FIELD_NULL` 只作为 B 合同的
  **可接受替代**出现（各 2 处），因为公开面无法把"源侧变化"与"转换侧变化"分开。
- 今天的确定性判别依赖**错误文本令牌**：`_TYPE_MISMATCH_PATTERN`、`_MISSING_COLUMN_PATTERN`、
  "列名出现在消息里"的 token 匹配。改进计划明确禁止"用错误文本或列名相似度猜映射"——这是本设计
  要替换掉的判别来源。
- 列级映射（哪个上游列经什么表达式成为失败模型的哪一列）在公开面完全缺失；多上游连接、同名列、
  CTE 别名场景下没有任何事实可以做列级归因。

### 1.4 判别问题 → 所需事实 → 现状

| 判别问题 | 需要的公开事实 | 今天有？ | 今天的后果 |
| --- | --- | --- | --- |
| 源列类型变了 vs 模型转换的 cast 变了 | 源列的**期望类型**（健康基线）+ 转换定义中被 cast 的列 | 都没有 | 靠错误消息猜；消息不点名该列时只能弃答 |
| 源必填字段为 NULL vs 转换产生 NULL | 转换定义（模型读取/投影的列） | 没有 | 源 profile 无空值即弃答（`TRANSFORMATION_*` 只能作为替代列出） |
| 源列被改名 vs 模型引用了不存在的列 | 期望列集合（基线）+ 转换定义引用的列 | 没有 | 仅靠 `column "x" does not exist` 文本 |
| 多上游连接/同名列/CTE 别名下的列级归因 | 列级映射 | 没有 | 不可能，只能整体弃答 |

## 2. 最小扩展提案

### 2.1 证据合同 `p1.column_evidence.v1`：两个新事实，只陈述事实、不给结论

| 事实 | 工具 | 来源（只读、运行绑定） | 字段 | UNKNOWN 语义 |
| --- | --- | --- | --- | --- |
| `RELATION_SCHEMA_EXPECTATION` | `get_relation_schema_expectation(relation_name)` | 可信健康基线（带 `baseline_fingerprint`）；关系限定在该场景声明的期望白名单 | `name`、`expected_data_type`、`expected_nullable`、`ordinal_position`、`baseline_fingerprint` | 关系不在基线 → `known=false`，无列项 |
| `DBT_NODE_DEFINITION` | `get_dbt_node_definition(node_id)` | 该运行产物：manifest 的 `columns`/`depends_on`/`compiled_code` 与 `target/compiled/**` | `node_id`、`resource_type`、`declared_columns`、`depends_on`、`compiled_sql_sha256`、`compiled_sql`（有界、换行规范化、沿用既有 redaction） | 该节点本次运行没有编译产物（如被 skip）→ `known=false` |

两个事实都**不做判别**：不返回"这是源侧问题"之类的结论；判别仍属于策略。

### 2.2 列映射：窄读器 + 显式 UNKNOWN（不用相似度猜）

列映射不是新的独立事实，而是确定性解算器/参考解内部的**窄读器**，只读已暴露的 E2 SQL，且只支持按
真实 fixture 实测列出的形状：

| 形状 | 例（fixture 实测） | 首切片 |
| --- | --- | --- |
| 单源别名投影 | `id as payment_id`、`user_id as customer_id` | 支持 |
| 算术/转换表达式 | `amount / 100 as amount` | 支持 |
| 多 CTE 链 | `orders` → `payments` → `order_payments` → `final` | 支持（按序展开，单源或两源连接） |
| 两源等值连接 + 限定同名列 | `orders.order_id = order_payments.order_id` | 支持 |
| 聚合 | `sum(case when … end) as credit_card_amount` | 支持（列级映射到分组键+表达式引用列） |
| 其他（窗口、非等值连接、三源以上、宏生成、无法判定来源的 `select *`） | — | **UNKNOWN** |

**UNKNOWN 永不作为任何方向的证据**：既不确认、也不拒绝、不参与缺口矩阵之外的一切判定。

### 2.3 权限

- 只读、运行绑定；E1 仅限场景合同新版本声明的 `expectation_relations`（**默认空**：老场景与新证据无
  任何交集）；E2 仅限该运行中与失败节点相关的节点集合（失败节点及其上游血缘闭包），越界返回与现有
  工具一致的 REFUSED 语义与真实错误码。
- 不新增数据库查询：E1 读基线产物，E2 读运行产物；诊断面仍不能读取 `config/scenarios`、
  `.dig/scoring-inputs` 或任何管理平面文件。
- 基线暴露仅限列级期望本身，不带行数/数值/标签；`baseline_fingerprint` 使"该运行依据哪份基线"
  可复核。

### 2.4 证据格式与版本身份

- 新 `EvidenceType` 成员 + `_CONTENT_TYPES`/`_SOURCE_TYPES` 映射 + 摘要规则与现有一致；SQL 设长度
  上限并复用 redaction。
- 运行上下文补一项：`runtime.json` 需记录 `baseline_fingerprint`（当前 `p1.runtime.v1` 没有）→ 上下文
  升 `p1.runtime.v2`，老运行按 v1 读取保持兼容。
- 工具面版本化：`p1.evidence_tools.v2` = 六工具 + E1/E2。**六工具 v1 的身份必须逐字节不变**——新证据
  以新身份交付，旧六工具场景与 NoSchema 消融沿用旧结果（改进计划原文），任何测量必须用新冻结身份。

### 2.5 预算

8 请求 / 8 工具调用 / 2 输出重试 / 300 秒**全部不变**；新工具消耗同一工具预算；不新增计数器，
不给新工具任何预算豁免。

### 2.6 明确不做

不新增数据库读；不从私有故障定义生成"公开证据"；不自动补声明、不自动执行探针；不把 UNKNOWN 当证据；
不在本任务冻结 manifest；不改变 `RELATION_*` 设计性扣留的可答性。

## 3. 对照与身份影响

- 新 A/B 场景使用新 case id 与新合同版本（`observable_evidence.v2`，**仅新文件**）；老场景文件与其
  摘要（certification/admission 里的 `scenario_digest`）一个字节都不动。
- `ScenarioSpec` 需要容纳 v2 合同（联合类型）→ `scenario_spec_schema_sha256` 会变化：这是**已知且有意**
  的漂移轴。已冻结 manifest 的 `result_inputs` 本来就不匹配当前源码（evaluator 演进），但 v21/v22 的
  **策略身份目前匹配**——新工具面不得破坏这一点，用回归钉住六策略身份逐字节不变。
- 影响面清单：需求 §10.6（六工具 → 版本化工具面）、§11（新事实 schema）、MCP 服务器白名单、
  evaluator/诊断的 gap 词汇（新增 `evidence_kind` 字符串）、身份（新 prompt/controller 版本号）。

## 4. 验收（确定性优先，离线先行）

### 4.1 新 A/B 对（2 对 = 4 变体，dev 扩展回归集）

- **对 1（期望决定性）**：类型变更落在**错误消息不点名**的列上。
  A：期望可见 → 参考解由"观测 vs 期望"的列级偏差确认 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`；
  B：期望被合同扣留（REFUSED）→ 合格弃答，缺口 = `(RELATION_SCHEMA_EXPECTATION, RELATION_NOT_ALLOWED)`
  + `(TRANSFORMATION_DEFINITION, NOT_OBSERVABLE)`。
- **对 2（定义/映射决定性）**：源侧列改名，错误消息只给中间/别名。
  A：定义可见 → 窄读器给出"被引用列不存在、期望列集合里存在"→ `SOURCE_SCHEMA_COLUMN_RENAMED`；
  B：定义扣留 → 合格弃答，缺口 = `(DBT_NODE_DEFINITION, RELATION_NOT_ALLOWED)`。
- 生成参数沿用 T12 §6 的记录格式（fault family、mutation、seed、role、伙伴、开发暴露）；若需要新的
  冻结 mutation 目标（现有允许集合之外），先在管理平面校验器里显式登记——列为实施切片 0，不含隐藏放宽。
- 形状覆盖（列改名/CTE 别名/多上游/同名列/未知映射）先由**单元级读器用例**钉住，再进入 DB 场景；
  未知映射的负例必须弃答且不产生确认。

### 4.2 端到端检查（与 T12 同规格）

认证 + 准入（`certify --admit`）；公开证据驱动脚本（零案例配置）跑真实证据链；扣留变体的缺口在归档
中复现；`score_run_offline` 与在线评测逐字段一致；T05 九条回放全部保持通过；六工具 v1 身份回归通过。

### 4.3 结论口径与负面结论条款

通过 = 新证据在**声明的形状集合**上"可见能确认、扣留合格弃答、未知保留不确定性"，且归档/离线重评
贯通；不声称泛化、不声称模型能力、不声称收益。若窄读器无法在验收形状上可靠工作，按改进计划交付
**负面结论**并保留六工具边界，不强行新增工具或门禁。

## 5. 边界

- `PAYMENT_EVENT_IDENTITY`（事件身份）与 `INGESTION_WATERMARK`（摄入水位）不属 T13；它们是另一证据族，
  若需要应单独立项。
- 不迁移旧六工具场景与 NoSchema 消融；旧结果不重算。
- 真实模型测量、新 manifest 冻结、T14 跨任务纵切均不在本文范围。

## 6. 实施切片（设计通过后）

0. 管理平面：合同 v2 校验器与（如需要）新冻结 mutation 目标登记；
1. 事实层：E1/E2 事实与工具、运行上下文 v2、六工具 v1 身份回归；
2. 读器：窄列映射读器 + 形状矩阵单测（含 UNKNOWN 负例）；
3. 场景：新 A/B 对落盘、认证与准入；
4. 策略面：`p1.evidence_tools.v2` 接入（参考解与规划器）+ 端到端、归档与离线重评验收。
