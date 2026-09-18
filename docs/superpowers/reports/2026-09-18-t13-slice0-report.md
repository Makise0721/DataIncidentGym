# T13 切片 0 报告：目标登记、合同 v2、拒绝见证与离线构造佐证

- 日期：2026-09-18。范围：设计 §6 切片 0 的离线部分（发起于设计通过 `c189ba1`）。
- **未包含**：数据库 dry run（构造的实跑验证）——按审计意见属独立授权范围，本报告只给出离线佐证与
  实跑清单。
- 验证：`ruff check .`、`git diff --check` 通过；全量单测 **861 passed / 5 skipped**（新增 19 条）。

## 1. 目标登记（T1′ 新增、T2′ 复用）

- `scenarios.py`：新增冻结映射 `_TYPE_CHANGE_TARGETS`，登记 `(raw_customers, id) → (integer, text)`；
  既有两个目标（`raw_payments.amount`、`raw_orders.user_id`）语义不变。校验器改为查表，未登记组合
  一律拒绝（含 `raw_payments.id`、`raw_orders.id`、`text→integer` 方向）。
- T2′ 复用确认：`raw_orders.user_id` 本就在冻结集合内，**不新增登记**。
- **实现发现（必须记录）**：staging 模型是 **view**（`dbt_project.yml` 中 `staging: +materialized: view`），
  PostgreSQL 拒绝在视图依赖列上改类型。既有 `_drop_dependency` 只映射了 `raw_orders`/`raw_payments`；
  本次把映射提为 `_DEPENDENT_VIEWS` 并补 `raw_customers → stg_customers`，注入与恢复两条路径
  （`_apply_mutations` 与 `_restore_mutations`）都会经过它。回归钉住"每个可类型变更的关系都有依赖
  视图映射"。

## 2. 合同 v2

- `scenarios.py`：`ObservableEvidenceContractV2`（`observable_evidence.v2`）＝ v1 字段 +
  `expectation_relations`（必须 ⊆ `schema_relations`）、`definition_nodes`（去重、非空），二者**默认空**。
  `ScenarioSpec.observable_evidence_contract` 改为按 `schema_version` 判别的联合类型。
- **v1 序列化逐字节稳定**：回归对全部 22 个场景断言 v1 dump 的键集恰为原五键，且
  `ScenarioSpec.model_validate(dump).digest()` 与原对象相等——旧场景 digest、旧证书绑定不受影响。
- **如实记录的漂移轴**：`scenario_spec_schema_sha256` 由 `fbf974…dd60` 变为 `4c9821…0a2b`
  （联合类型使总体 JSON schema 变化）。同一次核对确认：`diagnosis_schema_sha256` 不变，
  **v22 的六策略身份仍逐项相等**（新证据没有渗入 v1 工具面）。

## 3. 拒绝见证（逐目标、v1/v2 分流）

- `diagnosis.py`：新增 `TargetRefusal{target, code}`；`ToolTraceEvent` 新增可选
  `target_refusals`（成功调用为空、拒绝目标不得重复）；调用级码 `TARGETS_REFUSED` 只作概括。
- 共享判据 `refusal_witnessed(trace, tool_name, target, code)`，**两个归档匹配器**
  （certification 的 `_receipt_proved`、evaluator 的 `_insufficiency_matches`）都改用它：
  - v2 批量工具（该工具存在带 `target_refusals` 的事件）：只认逐目标条目，`(target, code)` 必须
    **精确命中恰好一条**；调用级码永不构成见证；
  - v1 工具（无该字段）：**原规则逐字保留**——主题出现在调用参数值中、调用被拒，且这类事件恰好一条
    且其码等于缺口码（"先计数、再比码"的既有语义未被放宽）。
- 回归（`tests/unit/test_t13_slice0_contracts.py`）：混合权限（请求 `[可读 raw_orders, 禁止 raw_customers]`
  只支撑被禁的那条）、混合错误码（同批 B/C 两码并存时各自只支撑自己的 `(target, code)`，交叉与
  调用级码均不成立）、v1 原规则（错码/错目标/未拒/重复调用）、两个匹配器对 v1 与 v2 trace 同判。

## 4. 离线构造佐证（不是实跑结论）

用已记录的上一次运行 manifest（`.dig/lab/runs/1250464…/dbt/target/manifest.json`）核对 `child_map`：

| 节点 | 模型消费者 | 结论 |
| --- | --- | --- |
| `stg_customers` | 仅 `customers` | T1′（raw_customers.id）在模型层面**只能**使 `customers` 失败 → 单一失败前提成立 |
| `stg_orders` | `customers` + `orders` | 原候选 T2（raw_orders.id）必然双失败 → 排除依据确认 |
| `stg_payments` | `customers` + `orders` | 原候选 T1（raw_payments.order_id）必然双失败 → 排除依据确认 |

这是**离线依赖图佐证**；它不替代 dry run——实跑仍须确认：注入成功、单一 `failed_nodes`、失败消息形态、
失败表达式在编译 SQL 中的唯一识别、以及恢复。

## 5. 待授权：数据库 dry run 清单（构造验证）

逐对执行、失败即停、不重试：
1. 落盘 T1′/T2′ 的**探测契约**（最小 ScenarioSpec，仅用于 dry run；非正式场景文件）；
2. `lab reset <case_id>`（如需要）→ `prepare` → `build`；
3. 断言 `run_results.failed_nodes` **恰好一个**（T1′/T2′ 均为 `model.jaffle_shop.customers`）；
4. 记录失败消息全文，核对：消息点名 `customer_id`、**不出现** raw 列名（`id`/`user_id`）、
   行片段在 `customers.sql` 编译产物中**唯一可定位**（该 SQL 有两处 `customer_id =` 比较）；
5. 断言注入确实生效（观测 schema 中目标列变为 text）且 `_drop_dependency` 使 ALTER 成功；
6. `restore` 后校验健康（列类型回到 integer）；
7. 记录 run id、消息文本、失败节点集合；若任一断言不成立，如实重设计该对或按负面结论处理。
