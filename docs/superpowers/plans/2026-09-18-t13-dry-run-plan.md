# T13 数据库 dry run 计划（切片 4 前置授权项）

- 日期：2026-09-18。状态：**待授权，未执行**。
- 目的：在进入切片 4（v2 工具面接线）之前，用一次**有界**的数据库操作定论两对场景的构造前提；
  设计 §6 第 6 版已把 dry run 记为切片 4 的前置授权项。
- 边界：**只做 inject + build + reset**；不调用真实模型、不跑基准、不冻结 manifest、不改任何场景文件或
  契约、不写入 `artifacts/` 之外的产物。任何一步不符预期即**停止并如实记录**，不做重试、不做同合同复跑。

## 1. 授权范围（逐条）

| 项 | 值 |
| --- | --- |
| 数据库 | 本地 Docker Desktop / PostgreSQL（既有 dev profile） |
| 变更操作 | `lab inject`（对 `raw_customers.id`、`raw_orders.user_id` 各一次 `ALTER ... TYPE text`，经依赖视图重建路径）+ `lab build` 的 `dbt build --exclude-resource-type seed` |
| 恢复操作 | 每次 build 后 `lab reset`（`FULL_REFRESH_BASELINE`），并以基线 fingerprint 核对 |
| 运行次数 | 2 次 inject、2 次 build、2 次 reset（每个 mutation 目标一次）；不做额外复跑 |
| 不包含 | 真实模型调用、`certify --admit`、benchmark 运行、manifest 冻结、任何写入 `config/` 的改动 |
| 前置检查 | `uv run data-incident-gym doctor`（确认 Docker/PostgreSQL 与基线可用） |

## 2. 逐步执行清单（两条目标各一遍，先 T1′ 后 T2′）

1. `uv run data-incident-gym pipeline build` —— 保证基线健康；记录输出的 `fingerprint`（记为 `F0`）。
2. `uv run data-incident-gym lab inject schema_type_change_raw_customer_id_a` —— 注入
   `COLUMN_TYPE_CHANGE(raw_customers.id: integer→text)`；记录输出的 `state` 与 `fingerprint`。
3. `uv run data-incident-gym lab build schema_type_change_raw_customer_id_a` —— 运行场景 dbt build；
   记录 `run_id`、`dbt_exit_code`、`verification_status`、`artifacts` 目录。
4. 读归档（只读）：`dbt/target/run_results.json`、`dbt/logs/dbt.log`（或 `dbt/stderr.log`）中的
   **原始失败消息文本**、`runtime.json`（v2 字段是否写出）、`dbt/target/manifest.json`。
5. `uv run data-incident-gym lab reset schema_type_change_raw_customer_id_a` —— 恢复基线；核对
   `fingerprint == F0`。
6. 对 `schema_type_change_raw_order_user_id_a`（T2′）重复第 2–5 步。

> 说明：dry run 用 A 变体的合同（E1/E2 白名单非空）驱动 `lab build`，同时顺带验证切片 1 的 v2 构建路径
> （`evidence_baseline.json`、`_redact_compiled_tree`、runtime v2 摘要）。B 变体与 A 变体的注入完全相同，
> 不需要各跑一遍。

## 3. 每步必须记录的观察值（dry run 的输出就是这些事实）

| # | 观察项 | 预期（静态预检，见切片 3 报告 §2） | 记录方式 |
| --- | --- | --- | --- |
| O1 | `run_results` 中 **status=fail 的模型节点集合** | 恰为 `{model.jaffle_shop.customers}` | 从 run_results 打印每个节点的 `unique_id`/`status` |
| O2 | 关系测试 `test.jaffle_shop.relationships_orders_customer_id__customer_id__ref_customers_.*` 的状态 | **skipped**（失败模型的下游） | 同上 |
| O3 | `direct_failure` / `affected_assets` 取值是否与场景文件一致 | 两者都应为 `model.jaffle_shop.customers` | 场景文件已声明；dry run 只核对 |
| O4 | dbt 报出的**原始消息文本**（逐字节，含 `LINE n:`） | `operator does not exist: text = integer` + 点名一条连接条件 | 从 `dbt.log`/`stderr.log` 复制原文进报告 |
| O5 | 消息点名的是哪一条比较点 | 待定：`... = customer_orders.customer_id`（风险 A）或 `... = customer_payments.customer_id`（风险 B） | 由 O4 判定 |
| O6 | `failure_class`/错误码在节点错误事实中的形态（供切片 4 参考解判别） | 类型类错误 | 从 run_results 的失败消息与 node_error 事实比对 |
| O7 | v2 构建路径是否成立：`evidence_baseline.json` 存在、`runtime.json` 为 `p1.runtime.v2`、`build_provenance.node_definitions` 覆盖全部编译节点 | 应成立 | 读归档文件字段 |
| O8 | 恢复后 fingerprint == `F0` | 应成立 | `lab reset` 输出 + 基线摘要比对 |

## 4. 预先约定的判定规则（结果出来当天即可定，不再二次讨论）

- **O5 = 风险 A（消息点名 `customer_orders` 侧）**：验收路径按设计 §4.1 原样成立，切片 4 按计划接线。
- **O5 = 风险 B（消息点名 `customer_payments` 侧）**：该侧经**无别名限定投影**（`customer_payments` 内的
  `orders.customer_id`）向下追溯，切片 2 读器按已记录的窄点返回 `UNKNOWN_COLUMN`，A 变体将在"应确认"
  场景上弃答。处置按审计建议执行——**走设计变更流程给读器新增"无别名限定投影追溯"这一个形状**
  （唯一匹配才解析、歧义即 UNKNOWN，独立审计 + 回归），**不放宽 A 变体的期望**，也不重设计已登记的 T1′
  目标。
- **O1 不是单节点，或 O2 显示测试 failed（而非 skipped）**：立即停止，如实记录，按设计 §4.3 的
  "构造 dry run 若不成立，如实重设计或按负面结论处理"执行；不得以既有场景数据顶替。
- **O7 不成立（v2 构建路径失败）**：停止并先修切片 1 的构建路径（视为切片 1 的实施缺陷），dry run 结论
  不成立、需重跑（重跑同样需要授权）。
- **O8 不成立（恢复失败）**：停止，先恢复数据库并如实记录，不继续第二条目标。

## 5. 产出

1. 报告 `docs/superpowers/reports/2026-09-18-t13-dry-run-result.md`：逐步命令、O1–O8 的实测值（O4 原文）、
   与预期的差异、结论（两对前提成立/不成立/需设计变更）。
2. 若 O5 = 风险 B：设计变更提案（读器新增形状）与预定的独立回归清单。
3. 场景文件或设计的任何必要修订都单独提交，不在 dry run 报告里夹带。
