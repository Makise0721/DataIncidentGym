# T13 两对认证与准入授权（2026-09-19）

- 授权人：项目所有者（待确认）。审计依据：设计第 8 版 `4c531f5`、dry run 收口报告 `857e6fb`（§8）、
  切片 4 四个增量（`279c678`/`27e13b5`/`40da2c4`/`8ae7f7a`）及各轮审计结论。
- 本文档是执行授权书：认证与准入是设计 §4.3 的第一项端到端验收，也是两对场景从"已登记、未认证"
  转为"已认证、已准入"的唯一关口。执行后由审计侧逐项复核，复核通过才计入任何评估集合。

## 1. 授权范围

| 项 | 值 |
| --- | --- |
| 数据库 | 本地 Docker Desktop / PostgreSQL（既有 dev profile） |
| 操作 | 1 次 `certify --admit` 覆盖四个 case（内部各 1 轮 inject → build → reset，共 4 轮）+ 4 次 `eval score` 离线重评 |
| 命令 | `uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a --case schema_type_change_raw_order_user_id_b --admit`；随后对四个 run_id 各执行 `uv run data-incident-gym eval score <run_id>` |
| 顺序 | 对 1 A → 对 1 B → 对 2 A → 对 2 B（一条 catalog 命令内的登记顺序即此顺序） |
| 不包含 | 真实模型调用、benchmark、manifest 冻结、任何写入 `config/` 的改动、对既有冻结场景（`P1_SCENARIO_IDS` 18 条）的认证重跑 |
| 前置检查 | **纯环境检查**：`docker compose up -d --wait postgres` + 步骤 1 的 `pipeline build`；**不运行 `doctor`**（模型探针不可关闭） |
| 重试 | 无。任何一步不符预期即停止并如实记录 |

## 2. 执行前置条件（逐条核对后写入报告）

1. **执行 HEAD 必须含 `4c531f5`**（设计第 8 版：四个合同的 `required_evidence_types` 已含 `DBT_LINEAGE`）；
   记录实际 HEAD 的完整 SHA。认证不得在任何早于身份桥 `279c678` 的代码上执行。
2. 工作树干净（`git status --short` 无已跟踪文件改动）。
3. `pipeline build` 的 fingerprint 等于历史基线指纹
   `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；不等即停止（数据面漂移）。
4. **dry run 旧归档不得顶替**：`6c2c5a03…` 与 `0c20920a…`（及更早的 `22c98522…`/`65626c9a…`）的 E1 快照
   早于身份桥，无 `relation_identity`。认证必须使用本次新产生的四轮运行归档。

## 3. 必录观察项 C1–C8（全部实测，取自本次运行归档与认证报告）

| 编号 | 观察项 | 预期 |
| --- | --- | --- |
| C1 | 四轮运行的 v2 记录完整性 | `evidence_baseline` fingerprint == F0；`dbt_invocation_id` + `artifacts_sha256` 齐；`node_definitions` 覆盖全部 22 个编译节点；`redacted` 全 false |
| C2 | 身份桥生效 | 两个 **A** 变体的 E1 快照均含 `relation_identity` 键（区别于 dry run 旧归档的直接证据）；两个 **B** 变体的 E1 快照存在、fingerprint == F0、按空白名单裁剪后 `relations` 为空（无关系条目可查身份） |
| C3 | 认证结论 | 两个 A 变体 `certified=True`：status `CONFIRMED`、root_cause `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`、affected_assets 与合同一致；两个 B 变体 `certified=True`：status `INSUFFICIENT_EVIDENCE`、failure_classes 为空 |
| C4 | B 缺口归档复现 | 两个 B 变体的 `unresolved_evidence` **恰为 2 条缺口**（代表缺口规则：E1 请求序首目标 `raw_customers` / `RELATION_NOT_ALLOWED`，E2 失败节点 `model.jaffle_shop.customers` / `NODE_NOT_ALLOWED`——与合同 `unresolved_gaps` 精确相等）；归档轨迹的 `target_refusals` 共 **6 条**逐目标条目（E1×2 `RELATION_NOT_ALLOWED`：raw_customers、raw_orders；E2×4 `NODE_NOT_ALLOWED`：customers、stg_customers、stg_orders、stg_payments——后者经 payments CTE 在上游闭包内），两条缺口三元组各自精确命中其 (target, code)——**调用级 `TARGETS_REFUSED` 与"出现在请求列表"不算见证** |
| C5 | 必需证据类型 | 四轮收集证据类型覆盖合同 required（A 六项、B 四项，均含 `DBT_LINEAGE`）；`types_ok` / `cited_types_ok` 满足；B 变体不把被拒事实计入 collected/cited |
| C6 | 预算实测 | 工具调用 A = 8、B = 7（设计 §4.1 第 8 版实测路径）；模型请求 = 0（确定性参考解） |
| C7 | 离线重评一致 | 四个 run_id 的 `eval score` 结果与在线评测**逐字段一致**（status、failed_check_codes、计数值） |
| C8 | 环境恢复 | 每轮 reset 后 fingerprint == F0；四轮结束后数据库处于健康基线 |

## 4. 判定规则

- **修订说明（2026-09-19，执行前）**：C4 初稿按"每个被拒目标一条缺口"写成 5 条，与三处权威来源
  冲突——B 合同 `unresolved_gaps` 恰 2 条、认证 `gaps_ok` 要求精确相等、已裁定的代表缺口规则
  （subject = 请求序首目标）。经执行侧在设计层面提出、审计侧逐条核实后修订为"缺口 2 条 +
  `target_refusals` 6 条"（C2 同步收窄 B 变体范围）。修订发生在任何执行之前，初稿未消耗授权。
- C1–C8 全部成立 → 认证成立，准入报告生效，两对进入"已认证"状态。
- 任一 C 项不成立 → **停止，无重跑**，如实记录后交审计裁定；不得在修复后直接再跑。
- C4 的见证精确性按设计 §4.3 匹配回归执行：混合权限只支撑被拒 subject 的缺口；缺口三元组
  （evidence_kind, subject, reason_code）须与 `target_refusals` 逐条一致。
- C3 中任一 finding 不满足 → 按认证报告的 `failure_classes` 如实归因（ENVIRONMENT / POLICY / SCENARIO /
  REFERENCE），不得以既有 dry run 结论顶替认证结论。
- 准入报告写入 `artifacts/admissions/`、认证报告写入 `artifacts/certifications/`（默认路径，不 `--output`
  改道）；四张场景卡片的 `solvability.certified` 在准入后应为 True——若卡片仍显示未认证，说明准入
  记录未被卡片读取，视为缺陷停止。
- 认证通过后：两对方可计入评估集合；manifest 冻结与真实模型测量仍需各自另行授权，不在本次范围。

## 5. 产出

- 结果报告 `docs/superpowers/reports/2026-09-19-t13-certification-result.md`：实际 HEAD、逐条命令、
  四个 run_id、C1–C8 实测值（C4 附 `target_refusals` 与 `unresolved_evidence` 原文）、与预期的差异、
  结论（认证成立 / 不成立及原因）。
- 认证报告与准入报告按其默认路径留档并在结果报告中给出路径；任何场景文件或设计文档的必要修订
  单独提交，不在结果报告里夹带。
