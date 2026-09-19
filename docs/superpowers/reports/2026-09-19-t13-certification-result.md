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
