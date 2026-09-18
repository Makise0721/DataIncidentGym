# T13 数据库 dry run 结果（T1′ 两次执行、T2′ 两次均未执行，按计划停止）

- 日期：2026-09-18。授权范围与判定规则见 `docs/superpowers/plans/2026-09-18-t13-dry-run-plan.md`（`d329a49`）。
- **结论：dry run 仍未完成，两次都在 T1′ 的 O7 上停下。** 第一次是构建自检按原始文本比较（Windows 上
  dbt 编译文件 CRLF）→ 已修复（`270d96a`）；第二次自检通过、v2 记录写出，但**校验器**仍按 v1 字段集合
  校验而拒绝 v2 运行 → 已修复（见 §7.3）。两次都按计划停止：T2′ 未执行、无第二次重跑、无认证、
  无 manifest 冻结。
- 另有一项已批准的设计变更待实施：错误行截断导致读器识别退化（§4）。
- 数据面未漂移：两次 `pipeline build` 的 `F0` = 历史基线指纹
  `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`。

## 1. 实际执行（逐步，命令与结果）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `docker compose up -d --wait postgres` | `postgres running healthy` |
| 2 | `uv run data-incident-gym pipeline build` | 成功，`relations: 8`，fingerprint **F0 = `e5c7848e…cb18`**（== 历史基线指纹 ✓） |
| 3 | `uv run data-incident-gym lab inject schema_type_change_raw_customer_id_a` | `state: INJECTED`，fingerprint `f15aecba311af71da1902dd7a670bf78c249ff93c1917581ad8b81aa87510f7c` |
| 4 | `uv run data-incident-gym lab build schema_type_change_raw_customer_id_a` | **失败**：`故障实验失败 [INCIDENT_EXECUTION_ERROR]：运行产物自相矛盾：model.jaffle_shop.customers、model.jaffle_shop.orders、model.jaffle_shop.stg_customers`；run_id `22c98522ca7e46f183a52254084e0f79` |
| 5 | `uv run data-incident-gym lab reset schema_type_change_raw_customer_id_a` | `state: HEALTHY`，fingerprint 回到 **F0** ✓ |
| 6 | （按计划停止） | T2′ 的 inject/build 未执行；未重跑 |

第 4 步的失败发生在 **dbt build 之后**的 v2 运行时写入阶段（`lab.py::_write_runtime_v2` →
`_definition_texts`）。**dbt 产物本身已完成且真实**，因此第 3 节的 O1–O6 直接从该次运行的归档读取；
只有"归档是否可作为合格运行"（O7）不成立。

## 2. O1–O8 实测

| # | 观察项 | 实测 | 与预期 |
| --- | --- | --- | --- |
| O1 | status=fail 的模型集合 | 恰为 `{model.jaffle_shop.customers}`（其余 4 个模型 success） | ✅ 一致 |
| O2 | `relationships_orders_customer_id…` 测试状态 | **skipped**（另有 `not_null_customers_customer_id`、`unique_customers_customer_id` skipped；其余测试 pass） | ✅ 一致 |
| O3 | `direct_failure` / `affected_assets` | 与场景文件一致（`model.jaffle_shop.customers`） | ✅ 一致 |
| O4 | 原始消息文本 | 见 §2.1（逐字节） | ⚠️ **形态部分不符**（见 §4） |
| O5 | 点名哪条比较点 | **`customer_orders` 侧**（风险 A） | ✅ 命中风险 A（不是风险 B） |
| O6 | 错误类别 | `operator does not exist: text = integer` + `HINT: No operator matches…` + `compiled code at …`，即类型类错误 | ✅ 一致 |
| O7 | v2 构建路径 | **失败**（构建自检拒绝归档） | ❌ **根因见 §3** |
| O8 | 恢复与基线一致性 | reset 后 fingerprint == F0，且 F0 == 历史指纹 | ✅ 一致 |

### 2.1 O4 原文（逐字节，来自 `22c98522…/dbt/target/run_results.json` 的 `message`，与 `dbt/logs/dbt.log` 三处渲染一致）

```text
Database Error in model customers (models\customers.sql)
  operator does not exist: text = integer
  LINE 73:         on customers.customer_id = customer_orders.customer_...
                                            ^
  HINT:  No operator matches the given name and argument types. You might need to add explicit type casts.
  compiled code at C:\Users\29913\codex_space\DataIncidentGym\.dig\lab\runs\22c98522ca7e46f183a52254084e0f79\dbt\target\run\jaffle_shop\models\customers.sql
```

注意 `LINE 73:` 后引用的编译 SQL 被**截断**（`customer_orders.customer_...`，snippet 长 64 字符、以 `...`
结尾）——这是本次 dry run 最重要的新事实，见 §4。

## 3. 偏差 1（已修复的切片 1 实施缺陷）：Windows 上编译文件为 CRLF，自检按原始文本比较

**现象**：`_definition_texts` 对 28 个节点中的 **22 个**判为"自相矛盾"（消息只列前 3 个：customers、orders、
stg_customers）。这些节点的 `run_results`/`manifest` 的 `compiled_code` 是 **LF**，而 dbt 在 Windows 上把
编译**文件**写成 **CRLF**（同一段 SQL，仅行尾不同）；逐节点字节数差==该节点的换行数（例：customers
1257 vs 1325 = 68 个换行）。

**影响面**：一处缺陷、两个位置——构建期 `lab._definition_texts` 与读取期
`evidence_batch._verify_definition_integrity` 都按**原始字符串**比较。后者意味着：即使构建通过，Windows 上
任何一次 E2 读取都会因同样的原因抛 `EVIDENCE_INTEGRITY_ERROR`。设计 §2.4 原文要求的是"**规范化**文本互不
相同"，所以这是实现未落实设计，而非设计问题。

**修复**（本轮提交）：新增共享 `run_context.canonical_compiled_text`（仅规范行尾：`\r\n`/`\r` → `\n`），
构建期与读取期都用它做**一致性判定**；摘要与上报文本仍按归档原始字节，保证"记录的摘要"与"重算的摘要"
不会漂移（`node_definitions.sha256`、`artifact_sha256` 均不变）。

**回归**（`tests/unit/test_t13_batch_evidence.py` 新增 3 条）：

- CRLF 编译文件 + LF JSON 副本 → 不再报冲突，且按来源优先级取 `run_results` 文本；
- 编译文件与 JSON 副本**实质不同**（追加注释）→ 仍报"运行产物自相矛盾"（防止修复弱化校验）；
- 完整 write→load：CRLF 运行可写出 v2 runtime 并通过加载，`get_dbt_node_definition` 返回
  `complete=true` 且文本为规范化来源文本。

这 3 条在未修复的代码上 2 条失败（`test_a_crlf_compiled_file_agrees_with_its_lf_json_copies`、
`test_a_crlf_run_loads_and_serves_its_definition`），修复后全部通过；第 3 条（实质差异）在两种情况下都必须
通过。夹具同时改为**按字节写文件**（原先文本模式写入会隐式改写行尾，这也是该缺陷此前未被单测发现的原因）。

## 4. 偏差 2（需设计变更裁定）：错误行被截断 → 读器识别退化为单侧投影映射

**事实**：PostgreSQL/dbt 报告的错误行只保留约 64 字符并以 `...` 结尾，因此 **join 条件全文不出现**在消息中
（`customers.customer_id = customer_orders.customer_id` 不在消息里）。把真实消息 + 真实归档编译文本喂给
切片 2 读器（用它自己的解析/归因，`upstream` 按 manifest `relation_name`，终止关系为三个 seed），实测：

```text
status: RESOLVED
expression: 'customers.customer_id'          ← 命中的是投影（边界命中），不是连接条件
origin: data_incident_gym.analytics.raw_customers id
chain: ('customers', 'data_incident_gym.analytics.stg_customers', 'renamed', 'source')
```

**后果（按对区分）**：

- **对 1（T1′，偏差在 `raw_customers.id`）**：映射到的单一起源正是偏差列，"唯一偏差且关联到失败表达式"
  仍可成立 → 验收路径大概率成立。
- **对 2（T2′ 镜像，偏差在 `raw_orders.user_id`）**：映射到的是 `raw_customers.id`（无偏差），偏差列
  （右侧起源）**没有被映射到** → A 变体的确认条件不成立，会在"应确认"场景上弃答。设计 §4.1 的
  "两侧起源"前提在真实消息形态下不成立——这正是镜像对设计要捕捉的方向性反例，它生效了。

**处置选项（按建议次序）**：

1. **截断感知的识别（建议采用）**：消息中的片段若**以 `...` 结尾且是某候选表达式的真前缀**，可把该候选
   视为被命中（仍是"唯一最大命中才继续"，歧义 → UNKNOWN）。**映射始终用完整 SQL 文本**，不引入任何
   相似度匹配——与设计"识别只用于选定表达式"一致。此方案同时恢复左右两侧起源的映射（含 T2′）。
   需要独立回归：前缀命中唯一/歧义两例、截断点在标识符中间（本次实测就是 `customer_`）、
   非截断的短片段不得被当作前缀命中（防止误命中）。
2. 按 `LINE n:` 定位编译文本中的表达式：确定性强，但消息中的行号指向 `target/run/…` 的副本，与 E2 提供的
   `target/compiled/…` 文本是否逐行对应需要另行验证，风险高于方案 1。
3. 放宽验收为"单侧映射 + E1 偏差"：**不解决 T2′**（偏差恰好在未被映射的一侧），因此不作为方案。

按计划 §4 的约定，这一项**不在现场改写**：先走设计变更流程（本报告即提案），裁定后再实施。

## 5. 结论与下一步

- 两对的构造前提中，O1/O2/O3/O5/O6/O8 均已实测成立（失败节点单节点、关系测试 skipped、消息点名
  `customer_orders` 侧、类型类错误、基线未漂移）；**O7 因切片 1 的实现缺陷失败，已修复**；
  **O4 形态偏差需设计变更裁定**。
- 因 O7 失败且 O4 需裁定，按计划 **dry run 结论不成立**：`certify --admit` 不启动、切片 4 不开始接线。
- 需要：①对"截断感知识别"方案的设计变更裁定；②修复后的 **dry run 重跑授权**（T1′ + T2′，范围同原计划；
  重跑同样需要独立授权）。
- 环境现状：数据库为健康基线（fingerprint == F0），无活动运行指针；失败运行
  `22c98522ca7e46f183a52254084e0f79` 的归档保留在 `.dig/lab/runs/`（`runtime.json` 未写出，符合失败语义）。

## 6. 边界

- 本报告不宣称任何场景已认证、可答或可诊断；两对不得计入任何评估集合。
- 未执行：T2′ 注入与构建、重跑、`certify --admit`、benchmark、manifest 冻结、真实模型调用。
- 报告中的 dbt 事实来自真实的数据库运行（授权范围内的一次 inject + build + reset），但那次运行**不是**
  合格的 v2 运行产物（自检未通过），其用途仅限于上表的观察项。

## 7. 第二次执行（重跑授权下，2026-09-18）

授权条件：范围与 `d329a49` 计划一致；执行 HEAD 必须含 `270d96a`；T2′ 同样逐字节记录 O4；O7 再次失败即
停止且不允许在同一授权内再跑。**执行 HEAD = `3e1e931`（含 `270d96a` ✓），工作树干净。**

### 7.1 逐步结果

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `docker compose ps` | `postgres running healthy` |
| 2 | `uv run data-incident-gym pipeline build` | 成功，fingerprint == **F0** ✓ |
| 3 | `uv run data-incident-gym lab inject schema_type_change_raw_customer_id_a` | `state: INJECTED`，fingerprint `f15aecba…`（与第一次相同） |
| 4 | `uv run data-incident-gym lab build schema_type_change_raw_customer_id_a` | **失败**：`[FAULT_VERIFICATION_ERROR]：runtime 字段集合无效`；run_id `65626c9a9a564c32b3b8bf27f3502eb5` |
| 5 | `uv run data-incident-gym lab reset schema_type_change_raw_customer_id_a` | `state: HEALTHY`，fingerprint == **F0** ✓ |
| 6 | （按授权条件停止） | T2′ 未执行；未在同一授权内再跑 |

### 7.2 O1–O6、O8（第二次，实测与第一次一致）

- O1：status=error 仍**只有** `model.jaffle_shop.customers`；O2：`skipped` 恰为三条（relationships、
  not_null_customers_customer_id、unique_customers_customer_id）；O3/O6 同第一次；
- O4/O5：消息上下文四行与第一次**逐行相同**（仅末行 `compiled code at <本次 run 路径>` 随 run_id 变化），
  仍是 64 字符截断、仍点名 `customer_orders` 侧；
- O8：reset 后 fingerprint == F0 ✓。

### 7.3 O7：自检已通过，卡在**校验器的 v1 字段集合**

CRLF 修复生效：22 个编译节点在 `_definition_texts` 中全部一致（不再报"运行产物自相矛盾"），v2 运行记录
写出且内容完整——`evidence_baseline`（fingerprint == F0）、`dbt_invocation_id` + 三个产物摘要、
`node_definitions` **22/22 覆盖全部编译节点**、`redacted` 全为 false、`observable_nodes.definition` 与合同
三项白名单一致。**共享记录校验器（读取期用）接受该记录**（`resolve_run_context` 实测通过）。

失败点：`lab_verifier._validate_runtime` 仍写死 v1 字段集合与 v1 schema_version，任何 v2 运行都会在
构建的最后一步被自己的校验器拒绝。**修复**（本报告同批提交）：新增公开的
`run_context.validate_runtime_record`（v1/v2 的单一权威，读取期已用它），校验器对 v2 记录委派给它，
v1 分支逐字节不变；并新增 3 条回归——v2 记录被接受、删掉 `artifact_sha256` 子键被拒、
`expectation` 越出可见 schema 被拒（未修复代码上 3 条全失败）。

### 7.4 结论

- 第二次执行把 O7 的阻塞从"写入期自检"推进到"校验器版本识别"，两处缺陷均已修复并带回归；**O7 仍需一次
  完整执行才能宣告成立**（需要第三次授权，范围同计划）。
- T2′ 的 O1–O6 依旧未实测；按审计口径不得从 T1′ 推断。
- 环境：数据库健康（F0），无活动运行指针；运行归档 `22c98522…`（第一次）与 `65626c9a…`（第二次）保留在
  `.dig/lab/runs/`。
