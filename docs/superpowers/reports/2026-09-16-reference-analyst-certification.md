# T04 实施报告：公开证据参考解与场景认证

- 日期：2026-09-16。
- HEAD：`e664b8e254af5864650f501f19d442d229253c36` 基线上的未提交工作树（未提交、未推送）。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) 阶段 B 的 T04；
  依赖 T02（评分输入归档）与 T03（离线重评）已交付的能力。
- 交付三分离：**实现完成**（第 1–4 节）；**环境验证已完成**（第 5 节：全目录 18/18 场景真实
  认证通过）；**真实模型行为未测量**（参考解是确定性策略，不涉及模型调用）。

## 1. 交付物

| 文件 | 内容 |
| --- | --- |
| `src/data_incident_gym/reference_solver.py` | `ReferenceAnalystRunner`：确定性公开证据参考解，策略身份 `REFERENCE_ANALYST` |
| `src/data_incident_gym/scenario_certification.py` | 逐场景认证（真实闭环）+ 私有答案见证核对 + 失败分类 + 证书报告模型 |
| `src/data_incident_gym/cli.py` | `data-incident-gym certify [--case …] [--output …] [--overwrite]` |
| `src/data_incident_gym/diagnosis.py` | `DiagnosticStrategy` 新增 `REFERENCE_ANALYST` |
| `src/data_incident_gym/benchmark_manifest.py` | 冻结排程改为显式六策略元组（`FROZEN_POLICY_STRATEGIES`），新枚举不再影响已封存 manifest |
| `src/data_incident_gym/benchmark_report.py` | 策略分组跳过无单元格成员；`main_values` 对缺失组安全取值 |
| `src/data_incident_gym/fixed_rule.py` | `_build_result` 改用 `self.strategy` / `_provider_label` 钩子（行为不变，可被子类复用） |
| `tests/unit/test_reference_solver.py` | 参考解规则 8 项（改名/类型/键列/拒绝/重复/干扰与措辞无关性/身份分离/私有访问禁词） |
| `tests/unit/test_scenario_certification.py` | 认证核对 5 项（收据、findings、分类、模型不变量、报告回读） |
| `tests/unit/test_cli.py` | `certify` 注册、未知案例拒绝、报告写出与未通过退出码 |
| `artifacts/certifications/catalog.json` | 全目录认证证书（18/18，真实运行产物） |

## 2. 身份与隔离

- **公开证据参考策略 `REFERENCE_ANALYST`**：构造入参只有 `run_id`、`DiagnosticSettings`、
  `EvidenceTools` 与 `ObservableRunContext`（公开 brief + runtime 白名单）。它不接收
  `ScenarioSpec`、不按案例名查表、不读取期望答案；结论只能来自六工具返回的记录。
  源码级禁词测试（`config/scenarios`、`ScenarioSpec`、`answerability`、`expected_status`、
  `variant_role`、`.dig/lab/private`）与"干扰列/改写措辞不改变结论"的合成用例共同锚定。
- **私有答案见证**：认证侧读取私有合同的期望状态、可接受根因、资产集合与缺口矩阵，仅用于核对
  "参考解产出 == 场景期望"的自洽性。见证不是 Agent 基线，也不是公开可解性证明；证书中与公开
  运行事实分字段存放。
- **FIXED_RULE 保留原定位**：规则引擎以子类复用（`ReferenceAnalystRunner(FixedRuleRunner)`），
  但策略身份、prompt/controller 版本（`p1.reference-analyst.v1`）与 provider 标签独立；
  已封存 manifest 的排程枚举改为显式冻结元组，`p1-formal-v22` 在新代码下仍验证通过。

## 3. 公开证据规则（逐族）

预算与被测策略一致：单次诊断 8 次模型请求、**8 次工具调用**、2 次结构化重试、300 秒；
参考解实际用量见第 5 节（最多 7 次调用）。关系证据补全遵循公开完整性投影（账本
`uncollected_relations` 的同源语义）：对被牵连关系补齐缺失的 schema/profile/history 记录，
已尝试的拒绝不重复探针。

- **失败运行（dbt 非零）**，要求恰好 1 个失败节点，依次采集节点错误与上游血缘：
  - **测试失败**：对牵连源关系采集 profile。
    - 任一列 `null_count > 0` → 确认 `SOURCE_REQUIRED_FIELD_NULL`，资产为测试的距离 1 上游模型，
      引用血缘记录；
    - 否则检查重复指纹：`business_key_duplicates` 为正 → `SOURCE_EXACT_PAYMENT_DUPLICATE`；
      `business_fingerprint_duplicates` 为正 → `SOURCE_SEMANTIC_PAYMENT_DUPLICATE`；
    - profile 被拒（B 类）→ 弃答，缺口为该关系的 profile 拒绝收据 + 转换定义不可观测
      （主体 = 按源关系名匹配到的 stg 模型），并补采该关系允许的 schema 等对照事实。
  - **构建失败（模型节点）**：对上游 seed/source 候选关系依次读 schema。
    - 错误文本含 `column "X" does not exist` 且 X 不在 schema 中 → 确认
      `SOURCE_SCHEMA_COLUMN_RENAMED`（引用该关系 schema + 节点错误 + run results）；
    - 错误文本为类型/运算符不匹配时：消息中点名且声明为字符串类型的唯一列 → 确认
      `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`；否则退化到"键形列（`*_id`）中唯一的字符串类型列"；
    - schema 被拒（B 类）→ 弃答，缺口为该关系 schema 拒绝收据 + 转换定义不可观测，
      并补采允许的 profile/history 对照事实；
    - 资产为失败模型及其已接受下游血缘中的全部模型。
  - 任何不确定 → `INSUFFICIENT_EVIDENCE`（fail closed）。
- **成功运行（业务告警信号）**：沿用固定规则引擎的既有公开规则——支付重复（精确/语义）、
  孤儿支付的已结算判定、静默丢失的期望/现值比对，以及订单量的正向健康证据
  （profile + history + watermark/SLA 或历史区间）。信号不可识别时弃答。

## 4. 认证核对与失败分类

`certify` 对每个场景按序执行 `reset → inject → build → 参考解诊断 → evaluator → recover`，
然后核对：

- evaluator 对参考运行给出 `PASSED`（含 B 类的缺口矩阵精确匹配与真实拒绝收据）；
- 见证：诊断状态 == 合同期望状态；确认场景根因可接受且资产集合一致；不足场景缺口集合一致；
- 预算：工具调用 ≤ 8；无非预期工具错误（`RELATION_NOT_ALLOWED` 收据除外）。

失败按固定码分类：`TOOL`（非预期工具错误）、`BUDGET`（调用达上限）、`SCORING`（产出与合同一致
但 evaluator 仍拒绝）、`REFERENCE_IMPLEMENTATION`（未达到期望答案）、`ENVIRONMENT`（运行前置
失败）。分类不把参考实现或评分问题判成场景缺陷；未解释的失败会阻止场景准入（T06 使用）。

## 5. 认证结果（全目录 18/18）

2026-09-16 在真实 PostgreSQL 17.6 + dbt 上执行 `certify --output
artifacts/certifications/catalog.json`，**18/18 通过**。逐场景结果与公开证据路径：

| 场景 | 终态 | 根因 | 调用 | 拒绝收据 | 公开路径要点 |
| --- | --- | --- | ---: | --- | --- |
| schema_rename_payment_amount | CONFIRMED | SOURCE_SCHEMA_COLUMN_RENAMED | 5 | - | 节点错误点名缺失列 `amount`；raw_payments schema 无该列、有 `total_amount` |
| schema_type_change_payment_amount | CONFIRMED | SOURCE_SCHEMA_COLUMN_TYPE_CHANGED | 5 | - | `text / integer` 类型错误；raw_payments.amount 声明为 text |
| schema_type_change_order_customer_a | CONFIRMED | SOURCE_SCHEMA_COLUMN_TYPE_CHANGED | 7 | - | `integer = text` join 错误；上游键形列中唯一字符串类型为 raw_orders.user_id |
| schema_type_change_order_customer_b | INSUFFICIENT_EVIDENCE | - | 7 | 1×schema(raw_orders) | raw_orders schema 被拒；源空值与转换产生空值两种解释不可区分 |
| required_null_payment_id | CONFIRMED | SOURCE_REQUIRED_FIELD_NULL | 5 | - | raw_payments.id null_count=1 |
| required_null_order_customer_a | CONFIRMED | SOURCE_REQUIRED_FIELD_NULL | 5 | - | raw_orders.user_id null_count=1 |
| required_null_order_customer_b | INSUFFICIENT_EVIDENCE | - | 6 | 1×profile(raw_orders) | profile 被拒；转换定义不可观测（stg_orders） |
| duplicate_payment_record | CONFIRMED | SOURCE_EXACT_PAYMENT_DUPLICATE | 5 | - | 测试失败 + raw_payments id 重复指纹为正 |
| duplicate_payment_coupon_a | CONFIRMED | SOURCE_SEMANTIC_PAYMENT_DUPLICATE | 4 | - | id 重复为零、order_payment_amount 指纹重复为正 |
| duplicate_payment_coupon_b | INSUFFICIENT_EVIDENCE | - | 4 | 1×profile(raw_payments) | profile 被拒；语义重复与合法拆分不可区分 |
| orphan_payment_record | CONFIRMED | SOURCE_PERMANENT_ORPHAN_PAYMENT | 5 | - | 关系违例为正 + 订单历史水位越过已结算窗口 |
| orphan_payment_coupon_a | CONFIRMED | SOURCE_PERMANENT_ORPHAN_PAYMENT | 5 | - | 同上（coupon 干扰项存在但不影响判定） |
| orphan_payment_coupon_b | INSUFFICIENT_EVIDENCE | - | 5 | 1×history(raw_orders) | 订单历史被拒；永久孤儿与正常晚到不可区分 |
| silent_payment_drop_record | CONFIRMED | SOURCE_PAYMENT_INGESTION_LOSS | 6 | - | 期望/现值差额与关系计数一致 + 双历史与水位齐备 |
| silent_payment_drop_partition_a | CONFIRMED | SOURCE_PAYMENT_INGESTION_LOSS | 6 | - | 同上（分区变体） |
| silent_payment_drop_partition_b | INSUFFICIENT_EVIDENCE | - | 6 | 2×history | 双历史均被拒；真实丢失与业务下滑不可区分 |
| order_volume_pattern_a | NO_INCIDENT | - | 3 | - | 现值在历史区间内（非当期分区，≥4 个同周期前值） |
| order_volume_within_sla | NO_INCIDENT | - | 3 | - | 当期分区 + SLA 时延内（watermark 与逻辑观察时间） |

- 全部场景工具调用 ≤ 7（预算 8）；5 个不足场景的缺口集合与合同矩阵精确一致，且每条带工具的
  缺口都有恰好一条参数含该关系、错误码一致的拒绝事件。
- 开发期曾暴露并被修复的缺口（作为分类机制的实例）：参考解最初在测试分支不采集 schema 证据，
  evaluator 以 `REQUIRED_EVIDENCE_TYPES_PRESENT` 拒绝。该情形最初被认证误分类为 `SCORING`；
  经审计修正分类口径后，缺采必需证据属于**参考实现未满足证据合同**（`REFERENCE_IMPLEMENTATION`），
  正确的根因与资产不构成评分问题的证据。认证已新增 `REQUIRED_EVIDENCE_TYPES_COLLECTED` 见证
  核对并把 `SCORING` 收紧为"合同要求的全部核对均满足而 evaluator 仍拒绝"。按公开完整性投影补采
  schema 后该场景通过。schema 分支的转换主体选择（stg_customers vs stg_orders）由"源关系名
  token 匹配 stg 模型名"的公开规则修正；该命名匹配是已知假设，重命名与歧义名称的反例由
  T05 回放库覆盖。

## 6. 验证

| 检查 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run pytest tests/unit -q` | 627 passed, 4 skipped（T04 新增 13 项） |
| `certify` 全目录（真实 DB/dbt，18 场景） | 18/18 certified |
| `uv run pytest tests/e2e/test_p1_policy_matrix.py -k fixed` | 通过（首跑失败为与目录认证收尾并发的环境抖动，复跑通过） |
| `uv run pytest tests/integration/test_evaluation_runner.py` | 通过（真实 DB/dbt 评测链路回归） |
| `benchmark verify`（p1-formal-v22） | 通过：新枚举与冻结排程解耦后，已封存 manifest 仍可验证 |
| `uv lock --check` / `git diff --check` | 通过 |

未运行及原因：`real_model` 用例（需显式授权与模型预算）；全量 e2e 复跑（枚举与报表改动已被
单元与定向子集覆盖，可在阶段 B 收尾时一并复跑）。

## 7. 边界与后续

- 参考解证明的是"公开证据 + 预算内存在确定性解题路径"，不证明真实模型会走该路径；模型能力
  的度量仍归 T08 的冻结比较。
- `certify` 目前面向本机开发与认证；开发/保留集管理、场景卡片与准入命令的完整形态在 T06 交付。
- T05（失败回放库）可直接复用参考解与认证产物作为"预期行为"的离线锚点。
