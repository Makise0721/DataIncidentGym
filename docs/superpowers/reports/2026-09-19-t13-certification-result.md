# T13 认证执行结果：停机报告（2026-09-19）

- 依据：授权书 `docs/superpowers/plans/2026-09-19-t13-certification-authorization.md`（修订版，含 §4 修订说明）。
- 结论：**认证不成立——授权已消耗，未重跑；根因已定位并修复（修复未执行任何数据库/认证操作），重新认证需审计复核后再授权。**

## 1. 执行事实

- 执行 HEAD：`4c531f5a0150b5328aec55b3bd9a3a943879d225`（= 授权要求的 `4c531f5`）；工作树干净。
- 前置检查：`docker compose up -d --wait postgres` → Healthy；`pipeline build` → fingerprint
  `e5c7848e…cb18` == F0 逐字相等。未运行 `doctor`。
- 命令（逐字）：
  `uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a --case schema_type_change_raw_order_user_id_b --admit`
- 结果：**certified 0/4、admitted 0/4**；四个 case 的 failure_classes 均为 `ENVIRONMENT`，
  `REFERENCE_RUN_COMPLETED` finding detail = `BUILD_FAILED`（run=None，未进入诊断与评测）。
  `eval score` 未执行（无可评 run）。

## 2. 四轮归档（按执行顺序，与授权顺序一致）

| 顺序 | run_id | case（由 dbt 错误行方向判定） | 归档内容 | 缺失 |
| --- | --- | --- | --- | --- |
| 1 | `41f5a4891d7144aeb2408b1a1bf34537`（05:53） | 对 1 A（`text = integer`） | dbt 全套产物、schema.json、profile_snapshot.json | `baseline_evidence.json`、`incident_brief.json`、`runtime.json` |
| 2 | `6a394e3a087d42e2a144843afc3be299`（05:54） | 对 1 B（同故障） | 同上 | 同上 |
| 3 | `07920dcd7cea4b0ea2dd87f2875d4cd5`（05:55） | 对 2 A（`integer = text`） | 同上 | 同上 |
| 4 | `c3a352d0a6b7415694aa984612abc8b7`（05:56） | 对 2 B（同故障） | 同上 | 同上 |

四轮 dbt 构建本身**全部符合预期**：单一失败节点 `model.jaffle_shop.customers`、类型错误消息、
64 字符截断错误行（逐字形态与 dry run 一致）。异常发生在 dbt 之后的 lab 构建后处理。
评测运行器在 BUILD 阶段吞掉原始异常（`except Exception: primary_error_code = f"{stage}_FAILED"`），
归档中无 traceback。

## 3. 根因（离线复现，零数据库操作）

写盘顺序（`lab.build` v2 分支）：schema.json ✓ → profile_snapshot.json ✓ → **`_write_evidence_baseline`** ✗
→ …。`baseline_evidence.json` 缺失把异常唯一地定位在该函数内。其两个输入都是纯文件读，遂在**不触碰
数据库**的前提下复现：

```
trusted_baseline OK  （fingerprint e5c7848e…，8 关系）
relation_bridge FAILED: AttributeError: 'NoneType' object has no attribute 'strip'
```

- 根因：`lab._relation_bridge` 对 manifest `nodes` 里 **`relation_name = null` 的节点**（jaffle_shop 的
  20 个 dbt test 节点）直接调用 `relation_identity(None)` → `None.strip()` 崩溃。E2 事实路径
  （`evidence_batch.get_dbt_node_definition`）对同一取值有 `isinstance(relation_name, str)` 守卫，
  桥这里漏了。
- 为何三道既有防线都没拦住：桥的单测 manifest fixture 全部节点都带 `relation_name`（无 test 节点）；
  dry run 的两次归档早于身份桥 `279c678`（当时 `_write_evidence_baseline` 不调桥）；桥的纯文件读性质
  使其只在真实 v2 构建上首次暴露。属身份桥增量的实现缺陷，审计免责。
- 修复：`_relation_bridge` 增加与 E2 相同的守卫——非字符串 `relation_name` 的节点不入桥（与其 docstring
  "不在 manifest 描述内的关系 simply absent"语义一致）。回归
  `test_nodes_without_a_relation_are_absent_from_the_bridge` 以带 `relation_name: None` 的 test 节点复现
  真实形态；修复后对**失败归档里的真实 manifest** 离线验证：桥产出 8 条目（含 raw_customers/raw_orders
  两个 seed 及其身份）。修复未执行任何数据库操作。

## 4. C1–C8 状态

| 编号 | 状态 |
| --- | --- |
| C1 | **未达**（四轮均未写 runtime；无法评估 v2 记录完整性） |
| C2 | **未达**（E1 快照未写出；无法评估 relation_identity） |
| C3 | **不成立**：certified 0/4，failure_classes = ENVIRONMENT ×4（归因 BUILD_FAILED，见 §2–§3） |
| C4 | 未达（无诊断产物） |
| C5 | 未达（无证据清单） |
| C6 | 未达（无 metrics） |
| C7 | 未执行（无 run_id 可评） |
| C8 | **成立（只读核验）**：每轮 restore 在 finally 内执行；活库 `raw_customers.id`/`raw_orders.user_id` 均为 integer，只读指纹（`_inspect_relations` + `_fingerprint`，纯 SELECT）= `e5c7848e…cb18` == F0 逐字相等 |

## 5. 留档与环境状态

- 准入报告：`artifacts/admissions/all.json`（**已被本次失败运行覆盖**，四条 admitted=false 记录）；
  `artifacts/certifications/catalog.json` 为 2026-09-16 旧目录，本次失败条目未写入其中。
- 数据库：健康基线（指纹逐字等于 F0），无残留变异；Docker 容器保持运行。
- 四个失败 run 归档保留在 `.dig/lab/runs/`（gitignore 内，本地留证）。
- 代码状态：修复 + 回归已提交（988 passed / 5 skipped，ruff、`git diff --check` 干净）。

## 6. 请求裁定

1. 根因与修复方案（§3）的复核；修复本身未重跑任何认证/数据库操作。
2. 重新认证需**新的授权**（本次授权按"任一 C 项不成立即停"已消耗）；建议新授权沿用本次规格，前置
   HEAD 至少含本次修复提交，其余条款不变。

## 7. 第二次执行（2026-09-19，授权书 round2）

- 执行 HEAD：`469ef047bf57997173e7b5ac6fcd114748f1a9f3`（含身份桥修复）；工作树干净；
  `pipeline build` fingerprint == F0 逐字相等；未运行 `doctor`。
- 命令（逐字）：`uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a
  --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a
  --case schema_type_change_raw_order_user_id_b --admit --output artifacts/admissions/t13-pairs.json`
- stdout（逐字）：

```
[通过] schema_type_change_raw_customer_id_a failure_classes=-
[未通过] schema_type_change_raw_customer_id_b failure_classes=ENVIRONMENT
[通过] schema_type_change_raw_order_user_id_a failure_classes=-
[未通过] schema_type_change_raw_order_user_id_b failure_classes=ENVIRONMENT
[准入] schema_type_change_raw_customer_id_a reasons=-
[拒绝] schema_type_change_raw_customer_id_b reasons=CERTIFICATION_FAILED
[准入] schema_type_change_raw_order_user_id_a reasons=-
[拒绝] schema_type_change_raw_order_user_id_b reasons=CERTIFICATION_FAILED
certified: 2/4
admitted: 2/4
report: C:\Users\29913\codex_space\DataIncidentGym\artifacts\admissions\t13-pairs.json
```

- 四轮 run（按执行顺序）：对 1 A `d8cf6e8bb4d548d8aad34b30583a2da7`、对 1 B
  `6d02759ab6bd43669baba6b008b5a9e1`、对 2 A `5cc3d793f3e34ec98b33a8e821abeab3`、对 2 B
  `de0dd4c86ace447e93ebd0516411cf53`。按授权随附 4 次 `eval score`。

### C1–C8 实测

| 编号 | 实测 |
| --- | --- |
| C1 | **四轮全部成立**：`p1.runtime.v2`；`dbt_invocation_id` + `artifacts_sha256{manifest,run_results,compiled_tree}` 齐；`node_definitions` = 22 个编译节点、`redacted` 全 false；`evidence_baseline.fingerprint` == F0 |
| C2 | **按 A/B 分流成立**：两个 A 的 E1 快照含 `raw_customers`/`raw_orders` 且逐关系带 `relation_identity` + `resource_type`（对 dry run 旧归档的直接区别证据）；两个 B 的快照存在、fingerprint == F0、空白名单裁剪后 `relations` = `[]` |
| C3 | **A 半成立、B 半不成立**：A×2 `certified=True`（CONFIRMED / SOURCE_SCHEMA_COLUMN_TYPE_CHANGED / 资产 = `["model.jaffle_shop.customers"]` 与合同一致 / failure_classes 空）；B×2 `certified=False`，failure_classes = `ENVIRONMENT`，finding = `REFERENCE_RUN_COMPLETED` detail = **`ARTIFACT_WRITE_FAILED`**（非 BUILD_FAILED，升级规则不触发） |
| C4 | **未达**（B 的诊断在内存中完成后未落盘，无 `unresolved_evidence`/`target_refusals` 归档可核） |
| C5 | **A 半成立**：两个 A 的 collected 与 cited 均为六类型（`DBT_RUN_RESULTS`、`DBT_NODE_ERROR`、`DBT_LINEAGE`、`RELATION_SCHEMA`、`RELATION_SCHEMA_EXPECTATION`、`DBT_NODE_DEFINITION`）；B×2 未达（无产物） |
| C6 | **A 半成立**：`tool_call_attempts=8`、`successful=8`、`model_requests=0`（provider=reference-analyst，model=none）；B×2 未达 |
| C7 | **A 半成立**：两个 A 的 `eval score` 均 PASSED 且 `changed_checks: 无（与原归档评分逐项一致）`；两个 B = `NOT_RE_SCORABLE（ARTIFACT_RUN_MISSING）`，如实记录 |
| C8 | **成立**：每轮 restore 在 finally 内执行；执行后活库两个变异列均为 integer，只读指纹 == F0 逐字相等 |

### 第二次停机根因（离线复现，未触碰真实 artifacts/）

失败阶段在**评测之后的产物写出**（非 BUILD_FAILED）：`ArtifactWriter._validate_complete_bundle` 把
写出的 `diagnosis.json` 用**冻结的 v1 `Diagnosis`** 模型重新校验；B 变体的 `DiagnosisV2` 缺口词表
（`RELATION_SCHEMA_EXPECTATION` / `NODE_NOT_ALLOWED`）在 v1 `Literal` 上必然 ValidationError →
`ARTIFACT_WRITE_FAILED`，写入器清理临时目录故盘上无痕。这是归档层的 v2 接缝缺失（第三增量报告曾
断言"序列化同形无碍"——该断言对此重验路径不成立，属执行侧判断错误，如实记录）。离线复现（纯内存
构造 v2 缺口 payload）：v1 `Diagnosis.model_validate` 拒绝（3 个校验错误）、`DiagnosisV2` 接受；
A 变体不受影响（其诊断无未决缺口）。

修复（`469ef04` 之后的提交）：`_validate_complete_bundle` 按产生诊断的**实际合同类**
（`DiagnosisV2`/`Diagnosis`）重验持久化文件；回归
`test_a_v2_diagnosis_round_trips_through_the_writer` 以含两条 v2 缺口的诊断过完整写入并回读。
全量 **989 passed / 5 skipped**，ruff、`git diff --check` 干净；修复未执行任何数据库/认证操作。

### 结论与请求裁定

- **认证不成立（B 半边）**：两对必须成对认证；A×2 已认证并准入（内嵌卡片 `certified=True`，tool calls
  8/8），B×2 因归档层缺陷未完成。本次授权消耗，未重跑。
- 修复后 B 路径的已知链路（构建 → 诊断 → 评测 → 归档）各环节均已有实测或回归覆盖；是否授权第三次
  执行（规格沿用，前置 HEAD 含本次修复提交）由审计裁定。若第三次仍在构建后处理阶段停机，升级规则
  （v2 链路整体审计）继续有效。
- 两份授权书随本报告一并入库。

## 8. 第三次执行（2026-09-19，授权书 round3）

- 执行 HEAD：`6ede22311f7400fc32658f86227236adc3a67b49`；工作树干净；`pipeline build`
  fingerprint == F0 逐字相等；未运行 `doctor`。
- 命令（逐字）：`uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a
  --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a
  --case schema_type_change_raw_order_user_id_b --admit --output artifacts/admissions/t13-pairs.json
  --overwrite`
- stdout（逐字）：

```
认证失败 [CERTIFICATION_SETUP_FAILED]。
```

- 产生的 run（仅前两个 case，循环在第二对启动前中断）：对 1 A `8602db2917df4670bcd2b5f9b598edc0`、
  对 1 B `3f65c5e63088497cae33b0b93ddd50e1`——**两轮产物完整落盘**（含 `diagnosis.json`），归档校验
  修复（`6ede223`）在真实链路上得到验证。

### 关键实测：B 链路首次完整走通，C4 达成

对 1 B 的归档（`3f65c5e6`）逐字核验：

- `diagnosis.json`：`INSUFFICIENT_EVIDENCE`，`unresolved_evidence` **恰 2 条**（与合同精确相等）：
  `(RELATION_SCHEMA_EXPECTATION, raw_customers, RELATION_NOT_ALLOWED)`、
  `(DBT_NODE_DEFINITION, model.jaffle_shop.customers, NODE_NOT_ALLOWED)`；
- `trace.jsonl`：恰好两次批量拒绝，`target_refusals` 共 **6 条逐目标条目**——
  `get_relation_schema_expectation`（请求 `raw_customers,raw_orders`）→
  `[(raw_customers, RELATION_NOT_ALLOWED), (raw_orders, RELATION_NOT_ALLOWED)]`；
  `get_dbt_node_definition`（请求四节点）→ `[(customers, NODE_NOT_ALLOWED), (stg_customers,
  NODE_NOT_ALLOWED), (stg_orders, NODE_NOT_ALLOWED), (stg_payments, NODE_NOT_ALLOWED)]`；
  两条缺口三元组各自精确命中其 `(target, code)`，调用级 `TARGETS_REFUSED` 未作任何见证；
- 预算：`tool_call_attempts=7`（成功 5 + 拒绝 2）、`model_requests=0`；
- 评测：`evaluation.json` status `PASSED`、failed_check_codes 空。

C1（两 run：v2 记录齐、22 节点、redacted 全 false、baseline==F0）、C2（A 快照带 `relation_identity`；
B 快照存在、==F0、`relations: []`）、C6（A=8/0、B=7/0）、C7（对 1 A rescore PASSED 逐项一致）、
C8（只读活库指纹 == F0）同轮实测成立。对 1 A 与第二轮实测逐点一致（结论、8/8、rescore 无差异）。

### 第三次停机根因（认证侧装载器，离线复现）

循环死于**对 1 B 的认证侧后处理**：`certify_scenario` 在评测成功后调用
`load_evaluation_input_bundle` 重载评分输入，`_finalize_bundle` 的
`EvaluationInputBundle.model_validate` 再次以 **v1 `Diagnosis`** 重验持久化诊断 → v2 缺口词表
ValidationError → `SCORING_INPUTS_INVALID`（`EvaluationInputsError`，`RuntimeError` 子类）→
不被 `certify_scenario` 的 `EvaluationWorkflowError` 捕获 → 直穿 CLI 裸 `except Exception` →
`CERTIFICATION_SETUP_FAILED`。在真实落盘的 B 评分输入上离线复现
（`SCORING_INPUTS_INVALID: bundle failed schema validation`），与归档修复（6ede223）相互独立——
同一条链路上第 4 处 v1 重验点。

修复（本次提交，无数据库操作）：`DiagnosisV2.schema_version` 覆写为 `"p1.diagnosis.v2"`（v2 合同
自身的显式标记；v1 模型与全部冻结面不动），`_finalize_bundle` 按该标记选择合同类重验——
**按合同声明分流，非内容猜测**。验证：真实 B payload（带 v2 标记，即下次执行写入的形态）装载为
`DiagnosisV2`、恰两条缺口、摘要自洽；回归两条（v2 写-读往返 + v1 冻结合约不变）。全量
**991 passed / 5 skipped**、ruff、`git diff --check` 干净。

**如实边界**：磁盘上第三次执行的 B 评分输入带旧标记（覆写前写入），按新装载器仍不可载——它属于
已消耗的授权；第四次执行将从内存 v2 对象写出 v2 标记的 bundle，自然绕开。剩余
`Diagnosis.model_validate` 扫描结果：`benchmark_archive`/`benchmark_report`（benchmark，v1 场景、
不在 T13 范围）、`diagnostic_agent` 静态技能路径、`evaluation_inputs` 内核状态路径——均属 v1
作用域，留待整体审计裁定是否统一扫除。

### 结论与请求裁定

- **认证仍未成立**：`certify_catalog` 未返回，无认证条目、无准入文件；两对需成对认证。本次授权
  消耗，未重跑。
- 升级规则的裁定请求：本次停机码是 `CERTIFICATION_SETUP_FAILED`（认证侧装载器），按字面不在
  "BUILD_FAILED / ARTIFACT_WRITE_FAILED" 之列；但按其意图——同一链路（构建 → 桥 → 归档校验 →
  评分输入装载）已连曝 **4 处** v1 重验/接缝缺陷——执行侧认为逐点修复的收益已尽，**建议直接进入
  v2 链路整体审计**（含上述 4 处剩余 v1 作用域点的扫除裁定），审计通过后再授权第四次执行。
  两个 A 变体已连续两轮全绿（结论、预算、离线重评逐点一致），B 链路的全部环节至此均有归档实测
  或回归覆盖；第三次执行未见任何新的诊断内容缺陷。
- 四个 `eval score` 命令按授权执行：对 1 A PASSED（逐项一致）；对 1 B `SCORING_INPUTS_INVALID`
  （旧标记边界，见上）；对 2 无 run_id 存在（循环未达），两条命令无可执行对象。

## 9. 第四次执行（2026-09-19，授权书 round4）——认证成立，T13 收口

- 执行 HEAD：`d00885631e9771a4ba0011b772d1030af89a669b`；工作树干净；`pipeline build`
  fingerprint == F0 逐字相等；未运行 `doctor`。
- 命令（逐字）：`uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a
  --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a
  --case schema_type_change_raw_order_user_id_b --admit --output artifacts/admissions/t13-pairs.json
  --overwrite`
- stdout（逐字）：

```
[通过] schema_type_change_raw_customer_id_a failure_classes=-
[通过] schema_type_change_raw_customer_id_b failure_classes=-
[通过] schema_type_change_raw_order_user_id_a failure_classes=-
[通过] schema_type_change_raw_order_user_id_b failure_classes=-
[准入] schema_type_change_raw_customer_id_a reasons=-
[准入] schema_type_change_raw_customer_id_b reasons=-
[准入] schema_type_change_raw_order_user_id_a reasons=-
[准入] schema_type_change_raw_order_user_id_b reasons=-
certified: 4/4
admitted: 4/4
report: C:\Users\29913\codex_space\DataIncidentGym\artifacts\admissions\t13-pairs.json
```

- 四个 run（按执行顺序）：对 1 A `b6d134d7dc6a4e549a82cb4818f06e31`、对 1 B
  `7e2082a14de549348168e812bdc3c638`、对 2 A `977561b3318f4ccf8108d8d3568f2063`、对 2 B
  `63a33aee180f45d9a6b742a6d895129d`。

### C1–C8 全量实测（全部成立）

| 编号 | 实测 |
| --- | --- |
| C1 | 四轮全部成立：`p1.runtime.v2`；`dbt_invocation_id`（04d27e5e / 29faba63 / 52199c53 / e8b37960）+ `artifacts_sha256` 三键齐；`node_definitions` = 22、`redacted` 全 false；`evidence_baseline.fingerprint` == F0 |
| C2 | A×2 快照含两关系且逐关系带 `relation_identity` + `resource_type`；B×2 快照存在、fingerprint == F0、`relations` = `[]` |
| C3 | 四条 `certified=True`、`failure_classes` 空、findings 全 satisfied：A×2 `CONFIRMED / SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`、资产 = `["model.jaffle_shop.customers"]`；B×2 `INSUFFICIENT_EVIDENCE` |
| C4 | 两个 B 逐字核验：`unresolved_evidence` 恰 2 条（同合同）；`trace.jsonl` 恰 2 次批量拒绝、`target_refusals` 共 6 条（E1×2 `RELATION_NOT_ALLOWED`：raw_customers、raw_orders；E2×4 `NODE_NOT_ALLOWED`：customers、stg_customers、stg_orders、stg_payments）；缺口三元组各自精确命中，`TARGETS_REFUSED` 未作见证；B 的 `diagnosis.json` 标记 = `p1.diagnosis.v2` |
| C5 | A×2 collected = cited = 六类型（含 `DBT_LINEAGE`）；B×2 collected = cited = 四类型（含 `DBT_LINEAGE`，不含被拒的两个 v2 事实） |
| C6 | A×2 = 8/8 调用、B×2 = 7 调用（5 成功 + 2 拒绝）、四轮 `model_requests = 0` |
| C7 | 四个 run_id 的 `eval score` 全部 PASSED 且 `changed_checks: 无（与原归档评分逐项一致）`——**B 半边首次可执行且逐项一致** |
| C8 | 每轮 restore 在 finally 内执行；活库两个变异列 integer、只读指纹 == F0 逐字相等 |

### A 侧跨轮比对与结论

- A×2 与第二/三轮实测逐点一致：结论（CONFIRMED / 同根因 / 同资产）、预算（8/8 调用、0 模型请求）、
  离线重评（逐项一致）——第三/四轮代码上的复认证通过。
- 内嵌卡片四条 `solvability.certified` 均为 True（A "tool calls 8/8"、B "tool calls 7/8"）。
- **结论：两对认证成立、已准入，T13 认证收口**。准入报告 `artifacts/admissions/t13-pairs.json`
  为单一权威四条目文件。两对自此可计入评估集合；manifest 冻结与真实模型测量仍需各自另行授权。
- 后续留档（审计裁定，非阻塞）：CLI 裸 `except Exception` 吞错误码（`CERTIFICATION_SETUP_FAILED`
  掩盖 `SCORING_INPUTS_INVALID`）待后续修；5 处认证路径外的 v1 重验点记为已知接缝，待 v2 场景
  进 benchmark 时统一处理。
