# T13 两对认证与准入授权 · 第三次执行（2026-09-19）

- 授权人：项目所有者（待确认）。审计依据：第二次执行停机报告 §7 与修复 `6ede223`（
  `docs/superpowers/reports/2026-09-19-t13-certification-result.md`）；审计侧独立复核：A 半边
  C1/C2/C3/C5/C6/C7/C8 全部对真实归档取证通过，B 停机根因离线复现（v1 冻结类拒 v2 缺口词表，
  恰 3 个校验错误），修复回归真实 v2 缺口写入-回读。
- 第二次授权按"任一 C 项不成立即停"已消耗，未重跑。本文档是第三次执行授权书：观察项 C1–C8 与
  判定规则沿用第一次授权书（含 §4 修订说明），此处只列差异与修正。

## 1. 授权范围（相对第二次的差异）

| 项 | 值 |
| --- | --- |
| 数据库 / 顺序 / 不包含 / 重试 | 与前两次授权书完全一致 |
| 命令 | `uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a --case schema_type_change_raw_order_user_id_b --admit --output artifacts/admissions/t13-pairs.json --overwrite`；随后对四个 run_id 各执行 `uv run data-incident-gym eval score <run_id>` |
| 四案全跑理由 | 两对必须成对认证，且 `t13-pairs.json` 必须保持为单一权威的四条目文件——只跑 B 会把该文件覆盖成两条目、丢失 A 的准入记录。`--overwrite` 是本授权的显式组成部分，覆盖对象仅限该文件 |
| A 侧口径 | A×2 是**在新代码上的复认证**：结论、预算、离线重评须与第二轮实测一致（8/8 工具调用、CONFIRMED、逐字段一致）；任何漂移都按不符处理 |
| 标准输出 | stdout/stderr 逐字捕获进结果报告（同第二次） |

## 2. 执行前置条件

1. **执行 HEAD 必须含 `6ede223`**（归档校验按产生合同类重验）；记录实际 HEAD 的完整 SHA。
2. 工作树干净（`git status --short` 无已跟踪文件改动）。
3. `pipeline build` fingerprint == `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；
   不等即停止。
4. 既有全部归档（dry run、认证第一轮的四个失败归档、第二轮的四个归档）均不得顶替；认证必须使用
   本次新产生的四轮运行归档。
5. 前置检查仍为纯环境检查；**不运行 `doctor`**。

## 3. 判定规则修正与新增

- **修正（审计侧错误，两处分）**：第一次授权书 §4 的"四张场景卡片的 `solvability.certified` 在准入后
  应为 True"按字面不可执行——`build_scenario_card` 不读准入文件，`certification` 是入参。修正为：
  **准入文件四条目的内嵌卡片 `card.solvability.certified` 均为 True**，且 B 条目的
  `certification.findings` 全 satisfied、`failure_classes` 为空。
- **C4 本次必须达成**：两个 B 归档的 `diagnosis.json` 落盘成功后可核——`unresolved_evidence` 恰 2 条
  （代表缺口），trace 的 `target_refusals` 共 6 条逐目标条目（E1×2 `RELATION_NOT_ALLOWED`：
  raw_customers、raw_orders；E2×4 `NODE_NOT_ALLOWED`：customers、stg_customers、stg_orders、
  stg_payments）；C6 的 B 半边的 7 次工具调用同步可核。
- **升级规则继续有效**：若本次仍在构建或构建后处理阶段停机（`BUILD_FAILED` / `ARTIFACT_WRITE_FAILED`
  等产物链路错误码），停止并触发 v2 链路整体审计，不再直接授权第四次执行。
- 若 B 的诊断内容本身不符（缺口集合、见证精确性、状态），那不是环境失败——按第一次授权书 §4 的
  failure_classes 归因规则处理，同样停止。

## 4. 产出

- 结果报告 `docs/superpowers/reports/2026-09-19-t13-certification-result.md` 追加 §8：第三次执行的
  实际 HEAD、逐条命令与逐字 stdout、四个 run_id、C1–C8 全量实测值（C4 附 `unresolved_evidence` 与
  `target_refusals` 原文）、A 侧与第二轮实测的比对、结论（两对认证成立 / 不成立及原因）。
- 认证成立即 T13 收口：两对进入"已认证、已准入"，可计入评估集合；manifest 冻结与真实模型测量仍需
  各自另行授权。
