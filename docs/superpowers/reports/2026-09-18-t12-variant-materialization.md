# T12 运行记录：4 个 dev 变体物化、认证准入与规划器真实证据链

- 日期：2026-09-18；于同日审计意见后修订（脚本公开证据口径、开发暴露记录、写入路径根因与修复）。
- 授权范围：设计 §6 的有界数据库实跑（4 变体 = 2 个 A/B 对）、参考认证与准入、脚本化模型驱动规划器跑
  真实 DB/dbt 证据链，以及报告/归档/恢复/离线重评贯通检查。**不含**真实模型调用、能力比较、holdout
  用途与新 manifest 冻结。
- 结论先行：**4/4 变体认证并准入；4/4 规划器真实证据链端到端通过**（评测 `PASSED`、六件规范产物、
  会话恢复、评分输入归档、离线重评逐字段一致）。本记录如实包含执行偏差与其根因。

## 1. 变体生成参数（每个变体显式记录）

| 项 | 对 1：`required_null_payment_id_distractor_a/b` | 对 2：`type_change_payment_amount_drift_a/b` |
| --- | --- | --- |
| fault_family | `REQUIRED_FIELD_NULL` | `SCHEMA_TYPE_CHANGE` |
| 注入（A/B 完全一致） | `SET_FIELD_NULL` FAULT `raw_payments.id`（selector `order_id=1`，expected 1）+ `SET_FIELD_NULL` DISTRACTOR `raw_customers.last_name`（selector `id=7`，expected `"M."`） | `COLUMN_TYPE_CHANGE` `raw_payments.amount` integer→text + `ADD_NULLABLE_COLUMN` `raw_payments.source_batch_note` |
| 新机制组合 | payments 侧必需字段 NULL **首次进入 test 角色 A/B**（既有 test 对只有 orders 侧；payments 侧原为 DEV 无干扰），并带跨关系空值干扰 | payments 侧类型变更 **首次进入 test 角色 A/B**（原为 DEV 无干扰），并带同关系 schema 漂移干扰 |
| seed / fixture | `seed.v1`，fixture commit `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，`raw_customers/raw_orders/raw_payments` 全量刷新，A/B 共享 | 同左 |
| variant_role / answerability | A：`TEST_CONFIRMABLE`/`CONFIRMABLE`；B：`TEST_INSUFFICIENT`/`INSUFFICIENT` | 同左 |
| B 的决定性差异 | 扣留 `raw_payments` 的 `RELATION_DATA_PROFILE`（`RELATION_NOT_ALLOWED`），gap 矩阵含 `TRANSFORMATION_DEFINITION(stg_payments)/NOT_OBSERVABLE` | 扣留 `raw_payments` 的 `RELATION_SCHEMA`，同上第二 gap |
| A/B 伙伴 | 互为伙伴（登记于 `AB_SCENARIO_PAIRS`） | 同左 |
| **开发暴露记录（修订）** | **策略侧 false**：规划器 prompt/controller 的开发从未使用这四个变体。**测试侧 true**：integration 脚本随这些变体开发——早期版本曾按 `CASES[case]` 读取决定性工具、期望终态、根因分支与 B 的缺口/拒绝码（本已整改，见 §4），调试期也据此定位过脚本缺陷。**不得再表述为"未被任何开发过程参考"** | 同左 |
| 划分依据 | 重组既有冻结 mutation（`_M8_NULL_TARGETS`/类型变更校验器）→ 机制组合划分，非 seed 划分；登记为 dev，永不回标 holdout | 同左 |

mutation 载荷全部来自既有冻结校验器允许的原子组合，未新增任何注入代码路径；`ScenarioSpec` 家族
校验（required-null 恰 1 fault + 1 distractor、type-change 不得含 rename/`SET_FIELD_NULL`）逐条满足。

## 2. 前置与环境

- Docker Desktop 引擎 + compose `postgres`（镜像 `postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94`，与 compose 钉死值一致）。
- submodule `third_party/jaffle_shop` @ `36bde6c`（与 seed 契约一致）；`uv sync --frozen` 通过。
- 健康基线 `.dig/baseline-summary.json` 存在（本轮未重建）。
- 代码状态：HEAD `0261be4` + 本轮审计整改（脚本重写、写入路径修复），见 §6。

## 3. 认证与准入（命令与结果）

命令（逐对，无 `--overwrite`，输出互不覆盖）：

1. `uv run data-incident-gym certify --case required_null_payment_id_distractor_a --case required_null_payment_id_distractor_b --admit --output artifacts/admissions/t12-pair-1.json`
2. `uv run data-incident-gym certify --case type_change_payment_amount_drift_a --case type_change_payment_amount_drift_b --admit --output artifacts/admissions/t12-pair-2.json`

| 案例 | 认证 run | 工具调用 | 评测 | 诊断 | 准入 |
| --- | --- | --- | --- | --- | --- |
| `required_null_payment_id_distractor_a` | `74797b39`（重跑） | 5 | `PASSED` | `CONFIRMED` | ✅ |
| `required_null_payment_id_distractor_b` | `fcb50846` | 6 | `PASSED` | `INSUFFICIENT_EVIDENCE` | ✅ |
| `type_change_payment_amount_drift_a` | `193fd5e5` | 5 | `PASSED` | `CONFIRMED` | ✅ |
| `type_change_payment_amount_drift_b` | `a5448ca3` | 6 | `PASSED` | `INSUFFICIENT_EVIDENCE` | ✅ |

全部 findings（状态/根因/资产/gap 矩阵/收据/必需证据类型采集与引用/预算/无意外错误）满足，无
`SYMMETRY_MISMATCH`、`CARD_INCOMPLETE`。

### 执行偏差（保留记录）

- 设计规定"每对一次 certify、合计 2 次调用、4 次认证"。实际发生 **3 次 certify 调用、5 次场景认证**：
  对 1 首跑中 A 以 `ENVIRONMENT`/`SCORING_INPUTS_WRITE_FAILED` 失败（run `fa1cafd8`：诊断
  `CONFIRMED`、评测 `PASSED`、六件产物齐全，仅评分输入落盘失败），随后**未改动任何合同**对 A 单案例
  复跑一次通过。该重跑属环境类失败的处置，不是 seed/变体 shopping；B 与对 2 均为首跑通过。
- **根因已确证（2026-09-18 修订）**：不再是"瞬态假设"。对 `.dig/scoring-inputs/` 的 300 次真实
  目录 rename 探针复现 **3 次 `PermissionError [WinError 5]`**（1%），与每次失败后又成功的行为一致；
  修复见 §5。此前"复跑成功"确实不足以确认原因，本条以探针证据取代之。

## 4. 规划器真实证据链（脚本化模型，integration）

`tests/integration/test_planner_real_evidence.py`：**脚本不含任何案例配置**——无 case→答案映射，不读取
期望状态、根因、缺口或拒绝码；每一步（探哪条关系、收据说明什么、确认还是弃答、弃答须声明哪些缺口）
都由公开事实推出：run results 的失败节点、node error 的消息与 `resource_type`、lineage 图（最近
seed/source、staging 模型推导）、关系事实内容（null_count、列类型）、以及运行提示中的公开
`observable_relations` 白名单。**期望值只在断言侧**：测试直接对私有合同的 `expected_status`/
可接受根因/`affected_assets`/gap 矩阵断言，并复核被拒工具的轨迹与理由码一一对应。

审计整改前的版本曾由 `CASES[case]` 驱动上述决策；该版本证明的是"知道案例配置的脚本可以驱动真实
链路"，**不等于**"脚本仅从公开证据作出诊断"，本轮已重写并补充回归。

公开事实 → 结论的耦合由离线回归钉住（`tests/unit/test_planner_scripted_director.py`，合成收据、无
数据库）：同一历史下 profile 出现空值才确认 `SOURCE_REQUIRED_FIELD_NULL`，无空值不得确认；schema 列
类型为 text 且报错提及该列才确认 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，integer 不得确认；弃答声明的
缺口 `reason_code` 直接跟随真实拒绝收据（改动拒绝码，缺口随之改变）。这些回归在重写期间还抓住了一处
控制流缺陷（关闭批次早于补采步骤）。

最终一轮（`4 passed in 202.52s`）：

| 案例 | run | 工具尝试 | 诊断 | 评测 | 离线重评 |
| --- | --- | --- | --- | --- | --- |
| `required_null_payment_id_distractor_a` | `12504645` | 5 | `CONFIRMED` | `PASSED` | 逐字段一致 |
| `required_null_payment_id_distractor_b` | `8d055eea` | 6 | `INSUFFICIENT_EVIDENCE` | `PASSED` | 逐字段一致 |
| `type_change_payment_amount_drift_a` | `af468fae` | 5 | `CONFIRMED` | `PASSED` | 逐字段一致 |
| `type_change_payment_amount_drift_b` | `1115ac34` | 6 | `INSUFFICIENT_EVIDENCE` | `PASSED` | 逐字段一致 |

贯通检查：六件规范产物、`recovery.recovered=True`（含指纹）、`classify_scoring_inputs` 为
`RE_SCORABLE`、`score_run_offline` 与在线评测 `model_dump()` 相等且 `changed_check_codes=()`。
B 变体的被拒步骤在 `TOOL_CALL` 轨迹中保留**真实后端码**（`RELATION_NOT_ALLOWED`，非 `PLAN_*`）。

调试期失败（如实）：A2 曾因脚本取"最后一条 lineage"（downstream，无 seed）抛异常转
`MODEL_RUNTIME_ERROR`；B1 曾因脚本去重键误用缺失的 `arguments` 字段导致补采循环超预算；重写后的
零配置脚本另有一处补采/关闭次序缺陷被离线回归捕获。均为测试脚本缺陷，修正后通过。

## 5. 评分输入写入路径：根因与修复（审计整改）

- **证据**：300 次 `.dig/scoring-inputs/` 目录 rename 探针 → 3 次 `PermissionError [WinError 5]`
  （1%）；同形态探针加有界重试后 **0/300 失败**。这与四次 `SCORING_INPUTS_WRITE_FAILED`
  （认证 A1、integration B2 两次批次各一）"复跑即好"的行为完全一致。
- **修复**：`evaluation_inputs.py` 新增 `_rename_with_retry`，仅对瞬时 `PermissionError` 做有界重试
  （5 次、50ms 退避）；其他 `OSError` 立即失败，错误码不变（`SCORING_INPUTS_WRITE_FAILED`），
  临时目录清理逻辑不变。
- **回归**：`tests/unit/test_offline_scoring.py` 两条——瞬时一次锁定后写入成功；持续锁定仍
  fail-closed（重试次数等于上限、临时目录不残留）。

## 6. 边界与交付物

边界：无真实模型调用，只证明机制，不回答"规划器是否更聪明"；4 个变体均为 dev 扩展回归集，未进入
`P1_SCENARIO_IDS`，任何已冻结 manifest 的 scenario catalog 不变；未冻结新 manifest，未动 kernel/static
的任何合同。

交付物：

- 契约：`config/scenarios/{required_null_payment_id_distractor_a,b,type_change_payment_amount_drift_a,b}.json`
- 登记：`scenarios.py`（`P1_T12_DEV_EXTENSION_IDS`，仅入 `SUPPORTED_SCENARIO_IDS`）、
  `scenario_cards.py`（两对新 `AB_SCENARIO_PAIRS`）、`config/scenario-sets.json`。
- 回归：`tests/integration/test_planner_real_evidence.py`（零配置脚本 + 合同断言）、
  `tests/unit/test_planner_scripted_director.py`（公开事实 → 结论耦合）、
  `tests/unit/test_offline_scoring.py`（写入重试）。
- 产物（gitignored）：`artifacts/admissions/t12-pair-1.json`、`t12-pair-1-retry-a.json`、
  `t12-pair-2.json`；§3/§4 所列 run 的 `artifacts/<run_id>/` 与 `.dig/scoring-inputs/<run_id>/`。
