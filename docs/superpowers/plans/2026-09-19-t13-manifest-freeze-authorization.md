# p1-formal-v23 manifest 冻结授权（2026-09-19）

- 授权人：项目所有者（待确认）。审计依据：T13 认证收口（`bb8d5ca`，结果报告 §9）；冻结机制与漂移
  现状已由审计侧逐项核实（见"背景核验"）。
- 本文档是执行授权书。冻结是纯离线操作（不触数据库、不调模型），风险低，但仍按授权纪律执行：
  范围固定、观察项必录、不符即停。

## 背景核验（审计侧已完成的取证，执行侧复核即可）

- 现行冻结清单到 `p1-formal-v22`（`config/benchmark/`，v1 与 v9–v22 共 15 件）；`APPROVED_MANIFEST_IDS`
  止于 v22，冻结 v23 须先批准该身份（先例：`22d621d` 批准 + `40c7e82` 入库的两段式）。
- v22 对当前工作树 `benchmark verify` 失败于 "result-input hashes drifted"——**逐项比对确认漂移恰好
  是且仅是 T13 的有意改动**：`scenario_spec_schema_sha256`（v2 合同字段）、`evaluator_version`
  （v2→v3）与 `evaluator_sha256`（evaluation.py）；而 `profile_spec_sha256`、`diagnosis_schema_sha256`
  （v1 冻结面）、`scenario_catalog`（17 条，两对 T13 **不在**冻结目录）、`policies`（六策略 v1 身份）
  全部逐字不变。
- 本次冻结的语义：把 **T13 收口后的实现修订号**（评测器 v3 + v2 场景模式）钉进新清单，使后续真实
  模型测量绑定已认证的代码状态。**不**改变正式赛程（`FORMAL_SCENARIO_IDS` 仍为 12 条）、**不**把
  T13 两对纳入冻结目录、不删除或修改任何既有清单。

## 1. 授权范围

| 项 | 值 |
| --- | --- |
| 操作 | 两段式提交 + 一次冻结 + 一次验证，全部离线 |
| 步骤 1（批准提交） | 镜像 `22d621d` 的 diff：`benchmark_manifest.py` 的 `APPROVED_MANIFEST_IDS` 追加 `"p1-formal-v23"`（恰一行）+ 三处测试钉值同步（`test_benchmark_manifest.py` 的 v22 引用与 `test_cli.py`），提交信息沿用先例格式 `feat: approve p1-formal-v23 diagnostic measurement identity` |
| 步骤 2（冻结） | `uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v23 --implementation-revision <步骤1提交的完整SHA> --output config/benchmark/p1-formal-v23.json`（**必须显式 `--output`**：默认路径是 v1） |
| 步骤 3（验证） | `uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v23.json` 必须通过；stdout 的 sha256 逐字录入报告 |
| 步骤 4（入库提交） | 仅含 `config/benchmark/p1-formal-v23.json` 一个文件，提交信息沿用先例 `chore: freeze p1-formal-v23 diagnostic measurement manifest` |
| 不包含 | 数据库操作、真实模型调用、benchmark 执行、`FORMAL_SCENARIO_IDS`/`P1_SCENARIO_IDS` 变更、既有清单与场景文件的任何改动 |
| 重试 | 无。任何一步不符预期即停止并如实记录 |

## 2. 执行前置条件

1. 执行基线 HEAD = `bb8d5ca`（T13 收口提交）；工作树干净。
2. 全量单测绿（当前 991 passed / 5 skipped）、`ruff check .` 与 `git diff --check` 干净。
3. 无需 Docker / PostgreSQL / 模型端点；**不运行 `doctor`**。

## 3. 必录观察项 F1–F4

| 编号 | 观察项 | 预期 |
| --- | --- | --- |
| F1 | 漂移清单复核 | 冻结前重跑逐项比对：与 v22 的差异**仍恰好**是 `scenario_spec_schema_sha256`、`evaluator_version`(v3)、`evaluator_sha256` 三项；`diagnosis_schema_sha256`、`profile_spec_sha256`、catalog（17 条逐条等）、policies（六策略逐个等）不变——**任何第四项漂移都是冻结面破口，停止** |
| F2 | 冻结与验证 | `freeze` 成功且 stdout 报 `cells: 106; model_backed: 94; fixed_rule: 12`；`verify` 通过并报 `17 catalog scenarios; 12 formal scenarios; 106 cells`；sha256 录入报告 |
| F3 | 正式赛程不变 | v23 文件内 `formal_scenario_ids` 恰为既有 12 条、`is_formal` 分布与 v22 一致；T13 四条不出现在清单任何位置 |
| F4 | 改动面 | 两个提交的并集 = `benchmark_manifest.py` 一行 + 三处测试钉值 + 一个新清单文件；`git diff bb8d5ca..HEAD --stat` 除此之外为空 |

## 4. 判定规则

- F1–F4 全部成立 → 冻结成立，T13 的实现状态封口完成。
- 任一不符 → 停止、无重跑；F1 出现计划外漂移按冻结面事故处理（回滚步骤 1 提交，交审计裁定）。
- 冻结完成后 T13 全部收口；真实模型测量（`benchmark run`）需各自另行授权，不由本授权涵盖。

## 5. 产出

- 冻结报告追加至 `docs/superpowers/reports/2026-09-19-t13-certification-result.md` §10：两个提交 SHA、
  逐条命令与逐字 stdout、F1–F4 实测值、v23 sha256、结论。
