# T12 运行记录：4 个 dev 变体物化、认证准入与规划器真实证据链

- 日期：2026-09-18。授权范围：设计 §6 的有界数据库实跑（4 变体 = 2 个 A/B 对）、参考认证与准入、
  脚本化模型驱动规划器跑真实 DB/dbt 证据链，以及报告/归档/恢复/离线重评贯通检查。**不含**真实模型
  调用、能力比较、holdout 用途与新 manifest 冻结。
- 结论先行：**4/4 变体认证并准入；4/4 规划器真实证据链端到端通过**（评测 `PASSED`、六件规范产物、
  会话恢复、评分输入归档、离线重评逐字段一致）。本记录如实包含两次环境类瞬态失败与处置。

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
| 开发暴露记录 | **false**：两对的契约均在规划器 prompt/规则开发完成之后创建，未被任何开发过程参考 | 同左 |
| 划分依据 | 重组既有冻结 mutation（`_M8_NULL_TARGETS`/类型变更校验器）→ 机制组合划分，非 seed 划分；登记为 dev，永不回标 holdout | 同左 |

mutation 载荷全部来自既有冻结校验器允许的原子组合，未新增任何注入代码路径；`ScenarioSpec` 家族
校验（required-null 恰 1 fault + 1 distractor、type-change 不得含 rename/`SET_FIELD_NULL`）逐条满足。

## 2. 前置与环境

- Docker Desktop 引擎 + compose `postgres`（镜像 `postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94`，与 compose 钉死值一致）。
- submodule `third_party/jaffle_shop` @ `36bde6c`（与 seed 契约一致）；`uv sync --frozen` 通过。
- 健康基线 `.dig/baseline-summary.json` 存在（本轮未重建）。
- 代码状态：HEAD `9f99cca` + 本轮未提交的契约/登记/测试（提交见 §6）。

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

### 与设计清单的偏差（如实记录）

- 设计规定"每对一次 certify、合计 2 次调用、4 次认证"。实际发生了 **3 次 certify 调用、5 次场景认证**：
  对 1 首跑中 A 以 `ENVIRONMENT`/`SCORING_INPUTS_WRITE_FAILED` 失败——该次诊断与评测**已成功**
  （run `fa1cafd8`：`CONFIRMED`/`SOURCE_REQUIRED_FIELD_NULL`、评测 `PASSED`、六件产物齐全），仅评分
  输入落盘抛瞬态异常；随后**未改动任何合同**（摘要一致）对 A 单案例复跑一次通过。此重跑是环境类
  失败的归因处置，不是 seed/变体 shopping；B 与对 2 均为首跑通过。
- 该瞬态在本轮共出现两次（另见 §4 的 B2），均以"同合同单次复跑"关闭，建议后续单独评估写入路径的
  Windows 健壮性（`write_evaluation_input_bundle` 的临时目录 rename）。

## 4. 规划器真实证据链（脚本化模型，integration）

`tests/integration/test_planner_real_evidence.py`：脚本化 `FunctionModel` 只依据工具真实返回
（失败节点、上游 seed 关系、各收据的证据 ID、公开 `observable_relations` 白名单）驱动
`plan_step → 取证 → close_obligation → submit_diagnosis`；B 变体在决定性探针被真实后端拒绝
（`RELATION_NOT_ALLOWED`）后以 `REVOKED` 关闭该义务、补采仍可观测的证据并合格弃答。

最终一轮（`4 passed in 232.57s`）：

| 案例 | run | 工具尝试 | PLAN 轨迹 | 诊断 | 评测 | 离线重评 |
| --- | --- | --- | --- | --- | --- | --- |
| `required_null_payment_id_distractor_a` | `38b2fbc8` | 5 | STEP/CLOSE/STATE 齐全 | `CONFIRMED` | `PASSED` | 逐字段一致 |
| `required_null_payment_id_distractor_b` | `261e42b2` | 6 | 含 1 条真实拒绝收据 | `INSUFFICIENT_EVIDENCE` | `PASSED` | 逐字段一致 |
| `type_change_payment_amount_drift_a` | `4c2d3a16` | 5 | STEP/CLOSE/STATE 齐全 | `CONFIRMED` | `PASSED` | 逐字段一致 |
| `type_change_payment_amount_drift_b` | `e7be7bd8` | 6 | 含 1 条真实拒绝收据 | `INSUFFICIENT_EVIDENCE` | `PASSED` | 逐字段一致 |

贯通检查：六件规范产物、`recovery.recovered=True`（含指纹）、`classify_scoring_inputs` 为
`RE_SCORABLE`、`score_run_offline` 与在线评测 `model_dump()` 相等且 `changed_check_codes=()`。
B 变体的被拒步骤在 `TOOL_CALL` 轨迹中保留**真实后端码**（`RELATION_NOT_ALLOWED`，非 `PLAN_*`）。

调试期失败（如实）：A2 曾因脚本取"最后一条 lineage"（downstream，无 seed）抛异常转
`MODEL_RUNTIME_ERROR`；B1 曾因脚本去重键误用缺失的 `arguments` 字段导致补采循环超预算。均为测试
脚本缺陷，修正后通过；B2 一次评分输入写入瞬态（同 §3），复跑通过。

## 5. 边界

- 无真实模型调用：脚本化模型只证明机制，**不回答"规划器是否更聪明"**；能力表述仍需新冻结身份下的
  真实测量（另行授权）。
- 4 个变体均为 dev 扩展回归集（`scenario-sets.json` 有登记与理由），未进入 `P1_SCENARIO_IDS`，
  任何已冻结 manifest 的 scenario catalog 不变（已验证 v22 目录与当前源码逐项相等）。
- 未冻结新 manifest，未动 kernel/static 的任何合同。

## 6. 交付物

- 契约：`config/scenarios/{required_null_payment_id_distractor_a,b,type_change_payment_amount_drift_a,b}.json`
- 登记：`scenarios.py`（`P1_T12_DEV_EXTENSION_IDS`，仅入 `SUPPORTED_SCENARIO_IDS`）、
  `scenario_cards.py`（两对新 `AB_SCENARIO_PAIRS`）、`config/scenario-sets.json`（4 条 dev 记录）。
- 回归：`tests/integration/test_planner_real_evidence.py`（4 案例端到端）；
  单测计数更新（`SUPPORTED_SCENARIO_IDS`=22、7 对 A/B、dev 理由分档断言）。
- 产物（gitignored）：`artifacts/admissions/t12-pair-1.json`、`t12-pair-1-retry-a.json`、
  `t12-pair-2.json`；§3/§4 所列 run 的 `artifacts/<run_id>/` 与 `.dig/scoring-inputs/<run_id>/`。
