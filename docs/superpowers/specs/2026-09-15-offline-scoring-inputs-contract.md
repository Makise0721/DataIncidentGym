# 评分输入归档与离线重评合同（T01 设计说明与 ADR）

- 日期：2026-09-15。
- 状态：设计说明与决策记录，供审阅。权威合同 `docs/requirements.md` 的 M14 章节已按本文件同步
  （2026-09-15 审计整改）；两者冲突时以 requirements 的合同语义与本文件的字段级细节共同为准。
- 上游：[2026-09-15 改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T01–T03；
  依据：[开源调研 R1](../reports/2026-09-15-open-source-landscape-research.md)、
  [调研驱动改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) 第 2 节代码核对表。
- 对应实现（同日落地）：`src/data_incident_gym/evaluation_inputs.py`（T02）、
  `src/data_incident_gym/evaluation_rescore.py`（T03）、`evaluation_runner.py` 归档接入、
  `cli.py` 的 `eval score` / `eval compare-scores`。

## 1. 目标与范围

把"运行"与"评分"解耦：新产生的完整诊断可以在不接触模型、数据库与 dbt 的条件下重新评分；
评测器的修复或升级不再要求重跑模型，历史批次的结论不被评测器缺陷污染，且版本差异可以逐项
追溯。

本文件覆盖 T02/T03 的新增字段、路径与版本策略；不覆盖场景参考解（T04）、失败回放库（T05）、
通用重复实验协议（T08）与第三方接入（T09–T11）。

## 2. 分层与不变量

- **公开面（策略可见）**：`IncidentBrief`、runtime 白名单、六个只读工具与工具 schema、对话历史。
- **私有面（管理平面）**：`ScenarioSpec`、`ScenarioVerification`、评分附件、evaluator、
  含 expected 字段的 `metadata.json`/`evaluation.json`/`report.md`。
- 不变量：
  1. 原始六文件、旧 manifest、ledger、旧报告以及旧派生评分目录一律不覆写；不原地改分。
  2. 评分附件不进入策略输入；`metadata.json` 含期望信息，不得整体公开给第三方 Agent。
  3. 离线重评只读运行产物；不自动重跑模型、不自动修复、不自动补造缺失输入。
  4. 时间等记录字段不进入语义比较：`created_at` 只出现在索引与派生 provenance 中，不参与
     `inputs_digest`、`score_id` 或任何检查判定。
  5. 历史产物按"完整可复评 / 仅可部分分析 / 不可复评"分类；不得从当前 `config/scenarios`
     或旧 `PASSED` 布尔值补造私有验证事实。

## 3. 字段合同

### 3.1 附件 `evaluation_inputs.json`（`.dig/scoring-inputs/<run_id>/`，私有）

| 字段 | 消费者 | 来源 | 公开 | 版本 | 缺失/不一致处理 |
| --- | --- | --- | --- | --- | --- |
| `schema_version` | 加载器 | 常量 | 否 | `p1.evaluation_inputs.v1` | 不支持版本 → 拒绝 |
| `run_id` | 加载器、评分器 | 运行身份 | 否 | 同 `RUN_ID_PATTERN` | 目录名、索引与 bundle 三方必须一致；任一不符 → 拒绝 |
| `incident_case_id` | 加载器 | 场景合同 | 否 | 非空字符串 | 与场景/验证不一致 → 拒绝 |
| `strategy` | 评分器 | 运行身份 | 否 | `DiagnosticStrategy` | 与 run result 不一致 → 拒绝 |
| `scenario` | 评分器 | 私有 `ScenarioSpec` 快照 | 否 | `scenario.v1` | 结构校验失败 → 拒绝 |
| `scenario_digest` | 加载器 | 写入时按 `ScenarioSpec.digest()` 计算 | 否 | 64-hex | 重算不符（含场景被替换/编辑）→ 拒绝 |
| `verification` | 评分器 | `IncidentVerifier.verify` 的冻结结果 | 否 | 9 字段镜像（status、case、run、exit code、failed/skipped nodes、assets、schema fingerprint、profile spec hash） | 结构校验失败 → 拒绝 |
| `verification_digest` | 加载器 | 写入时计算（payload 规范化 JSON） | 否 | 64-hex | 重算不符 → 拒绝 |
| `diagnosis_run` | 评分器 | `DiagnosisRunner` 的完整结果（含 kernel 终态） | 否 | `p1.diagnosis.v1` | 结构或一致性校验失败 → 拒绝 |
| `diagnosis_run_digest` | 加载器 | 写入时按 `DiagnosisRunResult.digest()` 计算 | 否 | 64-hex | 重算不符 → 拒绝 |
| `recovery` | 评分器 | `EvaluationRunner` finally 段 `lab.restore` 的实际返回 | 否 | `source=LAB_RESTORE`、`incident_case_id`、`state=HEALTHY/FAILED`、`fingerprint`（64-hex，可缺失） | 缺失 → 拒绝；case 与 bundle 不一致 → 拒绝；指纹格式不符 → 拒绝；不得伪称来源 |
| `budget` | 报告、审计 | 运行常量（8/8/2/300） | 否 | 与 `metadata.json` 同源 | 缺失 → 拒绝 |
| `original_evaluator` | 版本策略 | 写入时计算的 evaluator 身份（name、version、source_digest、dependencies_digest） | 否 | `version` + `source_digest` + `dependencies_digest` | 未知版本 → 拒绝 |
| `artifact_digests` | 差异报告 | `artifacts/<run_id>/` 六个文件 | 否 | 六个固定文件名 | 缺项 → 拒绝；与磁盘不符 → 不产出对照 diff |

### 3.2 索引 `index.json`（同目录）

| 字段 | 消费者 | 来源 | 公开 | 版本 | 缺失/不一致处理 |
| --- | --- | --- | --- | --- | --- |
| `schema_version` | 加载器 | 常量 | 否 | `p1.scoring_inputs_index.v1` | 不支持版本 → 拒绝 |
| `run_id` | 加载器 | 目录身份 | 否 | 同 run 模式 | 与目录不一致 → 拒绝 |
| `created_at` | 人读审计 | 写入时钟 | 否 | ISO-8601 带时区 | 不进语义比较；缺失时视为历史附件 |
| `inputs_digest` | 评分器 | 写入时按附件规范化 JSON 计算 | 否 | 64-hex | 重算不符 → 拒绝 |
| `files` | 加载器 | 附件文件摘要表（原始字节 sha256） | 否 | 恰好 `{evaluation_inputs.json: sha256}` | 多余键、路径分隔符、`..`、摘要不符 → 拒绝 |

### 3.3 派生评分 `artifacts/rescores/<run_id>/<score_id>/`（只读发布）

| 文件 | 内容 | 备注 |
| --- | --- | --- |
| `provenance.json` | `score_id`、`inputs_digest`、scorer 身份（name/version/source_digest/dependencies_digest）、原 evaluator 身份、原 `evaluation.json` 状态、三个派生产物的原始字节摘要表、`created_at` | 时间不参与语义比较；摘要表覆盖 `evaluation.json`/`diff.json`/`report.md` |
| `evaluation.json` | 重算的 `EvaluationResult` | 与产物同 schema |
| `diff.json` | 与归档 `evaluation.json` 的逐项差异：适用性/通过状态、`details_changed` 与 before/after 的 expected/actual；不可对照时 `available=false` + 固定 reason | 不与旧报告混用 |
| `report.md` | 人读摘要：状态、变更检查、provenance 与边界声明；不可对照时明确写"无法与归档评分比较（原因）" | 明确"派生评分不改变原批次结论" |

### 3.4 导出包（迁移/发布时才需要）

单文件 JSON：`schema_version=p1.scoring_inputs_export.v1`、`run_id`、`created_at`、`index`
与 `inputs` 原文，并携带文件摘要；导入侧按 3.1/3.2 的规则重算校验。公开策略视图永远不含
该附件。

## 4. 身份与派生规则

- `score_id = sha256(canonical{schema_version, inputs_digest, scorer{name, version,
  source_digest, dependencies_digest}, config})`。
- evaluator 源码摘要 = 以下文件的 sha256 字典：`evaluation.py`、`diagnosis.py`、
  `diagnostic_kernel.py`、`evidence.py`、`lab_verifier.py`、`profiles.py`、`scenarios.py`。
  选择偏保守：宁可让无关改动产生新的 `score_id`，也不放过会改变评分语义的改动。
- 依赖摘要 = `{python: major.minor.patch, pydantic: version}`。
- 自定义 scorer 的源码摘要额外纳入 `inspect.getsource(scorer)`（统一换行后）。身份必须对应
  实际执行代码，禁止只替换标签。
- 版本策略：只支持当前 `EVALUATOR_VERSION`（`p1.evaluator.v3`；`KNOWN_EVALUATOR_VERSIONS` 另保留
  `p1.evaluator.v2` 以继续加载健康声明修复前写出的附件）及其已保留的可执行版本；
  未知版本显式失败；历史版本不作为可复现承诺。

## 5. 历史产物分类

| 状态 | 判定 | 允许的操作 |
| --- | --- | --- |
| `RE_SCORABLE` | 附件与索引齐全、全部摘要与交叉校验通过 | 离线重评、版本比较 |
| `PARTIAL_ANALYSIS` | 六文件可读，但缺附件或附件不完整 | 只读分析；不产出评分、不产出 `PASSED` |
| `NOT_RE_SCORABLE` | 缺失、损坏、篡改、跨 run、未知版本、路径逃逸 | 拒绝并给出固定原因码 |

## 6. ADR（决策记录）

- **D1** 保留旧六文件目录布局；新增独立受限附件 `.dig/scoring-inputs/<run_id>/` 与派生评分
  `artifacts/rescores/<run_id>/<score_id>/`。旧读者继续读取旧产物。
- **D2** 不放宽旧 P1 固定排程验证器；新的通用实验协议独立版本化（T08），不修改旧身份。
- **D3** 离线评分只读、幂等、目录不覆写：同一输入重复评分返回既有派生结果；原始 bundle、
  manifest、ledger 与报告保持不变。
- **D4** 未知 evaluator 版本显式失败；历史版本齐全前不承诺旧版本可复现。
- **D5** `docs/requirements.md` 的 M14 章节记录本合同（2026-09-15 同步）：新增命令、附件与
  派生目录的合同语义进入权威需求；实现细节仍以本文件为准。
- **D6** 派生评分目录自带完整性证据：命中已有 `score_id` 时先校验 `provenance.files` 记录的
  `evaluation.json`/`diff.json`/`report.md` 原始字节摘要，再核对跨文件一致性（run_id、score_id、
  `diff.after_status` 与 evaluation 状态）；任一不符即拒绝，不返回被修改过的缓存。
- **D7** 逐项差异必须区分"判定变化"与"依据变化"：适用性/通过状态变化记入 `change`，
  expected/actual 变化记入 `details_changed` 并保留前后取值；`changed_check_codes` 两类都算，
  不允许把评分依据变化报成"无变化"。无法与归档评分对照时，报告、CLI 与产物都必须明说
  "无法比较（原因）"，不得表述为"与原评分逐项一致"。
- **D8** 附件与目录、索引、bundle 三方绑定：`run_id` 必须同时等于目录名、索引 `run_id` 与
  bundle `run_id`；三种检查分别存在，任何抄改其中一个的复制都不会被当成另一个 run 的输入。

## 7. 未决项

- 导出包的发布流程（公共任务包与保留集边界）在 T11 决定。
- 通用重复实验协议、参考解身份与新场景准入作为 T04–T08 依赖，本文件不预设其字段。
