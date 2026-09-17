# T08 实施报告：重复可靠性协议

- 日期：2026-09-16。基线 `e664b8e` + 本轮未提交工作树（T01–T07 交付之上）。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T08；依赖 T06/T07。
- 交付：
  - `docs/superpowers/specs/2026-09-16-repeat-reliability-protocol.md`：版本化通用实验规范
    `p1.reliability.v1`（比较单位、预定重复、执行顺序、失败归类、缺失/无效处理、跨场景汇总）。
  - `src/data_incident_gym/reliability.py`：`pass_hat_k`（`C(s,k)/C(n,k)`）、`build_group`、
    `unscheduled_group`、`macro_pass_hat`、`reliability_for`、`scheduled_repeats`。
  - `benchmark_report.py`：`strategies[*].reliability` 稳定性块（全部策略）+ report.md
    「Repeat reliability」章节含不完整组清单。
  - `docs/requirements.md`：M16 修订行 + §13.3「重复可靠性协议」。
- 验证：`ruff check .` 通过；单测 **725 passed / 4 skipped**（T08 新增 26 项：13 项协议单测 +
  2 项报告级回归 + 11 项审计整改回归）；`git diff --check` 通过（仅 AGENTS.md 既有 CRLF 提示）。

## 固定规则（与 §13.3 一致）

| 规则 | 口径 |
| --- | --- |
| 分组身份 | 单一场景 × 单一策略 × 单一冻结身份（manifest id/摘要/实现修订/evaluator 身份/协议版本） |
| 预定重复 n | 冻结排程的 `repeat_index`：主策略每场景 3 次，`NO_TOOL`/`FIXED_RULE`/两个消融每场景 1 次 |
| 执行顺序 | 冻结 `sequence` 升序；三次重复按轮转顺序错开，同组重复不连续执行 |
| 成功 / 失败 | 评测 `PASSED` 且无失败 controller 门 / 评测 `FAILED` 与 `MODEL_ERROR`（含超时、预算耗尽） |
| 无效样本 | 适用**环境门**失败：`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY` |
| 代理违规 | 伪造引用、越权工具、写尝试、kernel 状态门 → 失败 trial，绝不隔离 |
| 优先级 | 同一 trial 同时命中环境门与失败时，以环境门为准（样本不产数字） |
| `pass^k` | `C(s,k)/C(n,k)`；`s<k` → 0；`n<k` → null；报告 k=1/2/3（受 n 限制） |
| 缺失/无效 | 主组全 null + 列出下标；完整子集分析单列，强制携带覆盖量 |
| 跨场景汇总 | 只对完整组做宏平均，写出纳入组数与 trial 数；零分母 null + 原因 |
| 区间 | 跨场景区间必须按场景聚类；本实现不输出跨场景区间 |

`pass^k` 是"k 次全部成功"的无偏估计，**不是** `pass@k`；报告 markdown 直接写明这一点，避免读者
误读成"尝试 k 次至少成功一次"。

## 手工可验算验收（协议自带示例）

`test_pass_hat_follows_the_frozen_formula` 等 13 项单测逐项核算：

- **n=3、s=2**：`pass^1 = 2/3`、`pass^2 = 1/3`、`pass^3 = 0`（计划里的示例原样复现）。
- **n=1**：只输出 `pass^1`；`pass^2`/`pass^3` 因 `n < k` 为 null——历史单次 R3 不生成 pass^2/3，
  也不能用其它场景/策略/版本的 trial 拼出更大的 n（宏平均只纳入完整组，且按组计数）。
- **缺失**：预定 1..3 只观测到 1、2（都成功）→ 主组 `complete=false`、`pass^1..3` 全 null、
  `missing_repeat_indices=(3,)`；完整子集分析 `coverage=2/3`、`pass^1=1.0`、
  `pass^3=null`（按自身规模计算，绝不缩小 n 后当主数字）。
- **无效样本**：两条成功 + 一条安全硬门失败 → 主组 null、`invalid_gate_codes` 记录失败门；
  不会被当成 `pass^3 = 0`，也不算成功。
- **模型失败**：三条 `MODEL_ERROR` → 组完整、`successes=0`、`pass^1..3 = 0`。
- **身份校验**：组内重复 `repeat_index`、计划外下标、非连续计划分别以固定码拒绝。

## 报告级回归（真实 106 格冻结排程）

报告级回归在 `_write_fixture` 生成的完整套件上断言（以下为各断言的实际范围）：

- `test_reliability_block_follows_the_frozen_repeat_schedule`：`STATIC_SKILL` 12 组且
  `planned_repetitions` 全为 3、宏平均 `groups=12`/`trials=36`；`FIXED_RULE` 的
  `planned_repetitions` 全为 1 且 `pass^2` 为 null（原因 `no complete group with n >= 2`）。
  其余消融组（`NO_TOOL` 12、`KERNEL_NO_LINEAGE` 5、`KERNEL_NO_SCHEMA` 5）的计划形状由
  `test_frozen_schedule_plans_three_repeats_for_main_strategies_only` 在排程层钉住，
  不在本报告级测试的断言范围内。
- `test_reliability_marks_an_invalid_environment_sample_incomplete`：制造一条
  `RECOVERY_HEALTHY` 失败 → 该组 `groups_incomplete=1`、`invalid_repeat_indices=[1]`、
  主数字全 null、子集覆盖量 `2/3`，宏平均只覆盖其余 11 组。
- `test_agent_rule_violation_stays_a_failed_trial`：同样一格改成 `TRACE_READ_ONLY_SAFE`
  失败 → 组仍完整、`successes=2`、`pass^1..3 = 2/3, 1/3, 0`；该门仍出现在
  `invalid_gates`/INVALID 结论里（两种用途不混）。
- 顺手修掉一处接线缺陷：可靠性计算最初把整份排程传给单策略的组计算，导致其它策略的空组被
  计入（`groups_total` 出现在 58）；现按被报告策略过滤排程，并对混入的异策略格 fail-closed
  （`test_reliability_rejects_a_foreign_strategy_cell`）。

## 审计整改（第三轮复核，2026-09-16）

- **部分分析的试次绑定缺失（P1）**：`analyze_partial_suite` 原先只核对 `metadata` 的 manifest 摘要，
  因此换入其它场景的 `evaluation.json`（保留本格 metadata）仍会得到 SCORED / passed=True，把别的场景结果
  计入本重复组。现逐格校验 ledger 条目、`metadata`、`evaluation`、`diagnosis` 各自的 run_id / 场景 / 策略 /
  序号与目标格一致，并交叉核对 ledger 终态与评测状态；身份不符记 `IDENTITY_MISMATCH`、跨版本记
  `MANIFEST_IDENTITY_MISMATCH`。回归 2 项：`test_partial_analysis_rejects_a_substituted_bundle`
  （跨场景 evaluation、同场景跨 run evaluation、跨场景 diagnosis 三种替换）、
  `test_partial_analysis_rejects_a_ledger_identity_mismatch`。
- **合法 JSON、错误结构会中断整批分析（P2）**：原先直接 `metadata.get(...)`，`metadata.json` 为 `[]` 时抛
  `AttributeError` 而非标记该格。现三份产物一律经 `RunMetadata`/`EvaluationResult`/`Diagnosis` 模型校验，
  形状非法记 `ARTIFACTS_INVALID` 并只让该格退出计分；回归
  `test_partial_analysis_survives_a_structurally_broken_file`（同套件其余 57 组仍完整）。

## 审计整改（第二轮复核，2026-09-16）

- **controller 门失败计为成功（P1）**：`_passed` 原先只读 `EvaluationResult.status`，而该状态由证据检查
  推导、不含 `controller_checks`；夹具中加入失败的 `KERNEL_HYPOTHESIS_GATE` 后可靠性仍输出
  `successes=3`、`pass^1..3` 全 1。现统一为 `_evaluation_passed`（证据检查通过 **且** 无失败的适用
  controller 门），`_efficiency` 的通过格选择同步改用同一判定。回归：
  `test_kernel_gate_failure_is_a_failed_trial_not_a_success`——该组 `successes=2`、
  `pass^1=2/3`、`pass^3=0`、`invalid_repeat_indices=[]`，同时 `completed=36`（ledger 口径）与
  `passed_cells=35`（成功口径）分离可见；report.md 的覆盖表列头改为
  "Completed (ledger)"/"Failed (ledger)" 以免误读。语料影响：附件覆盖的 75 条运行中 controller
  门失败为 0，数值不变。
- **缺失重复的报告路径未接通（P2）**：正式报告要求 ledger 每格两条且六产物齐全，因此协议承诺的
  "不完整组 + null + 覆盖量"在正式入口上不可达。现新增只读入口 `analyze_partial_suite`
  （CLI `benchmark partial`）：只读归档状态、不重算 evaluator 规则、不写正式报告，缺失格按
  `NO_TERMINAL_LEDGER_ENTRY`/`ARTIFACTS_UNREADABLE`/`MANIFEST_IDENTITY_MISMATCH`/
  `LEDGER_STATE_MISMATCH`/`LEDGER_TERMINAL_CONFLICT` 标记且不进入分母；每个被计分 bundle 仍必须
  与传入 manifest 摘要一致（跨版本样本一律记为缺失）。回归 4 项：缺格、不可读产物 + 身份不符、
  ledger 终态冲突、CLI 输出。

## 审计整改（第一轮，子代理审计，2026-09-16）

- **环境门与代理违规分离（P2，语义修正）**：初版沿用 `_SAFETY_GATES` 判定"无效样本"，会把伪造
  引用/越权工具/写尝试洗成环境问题并隔离出分母。现改为环境门三码才隔离（见上表），代理违规一律
  计失败 trial。复核数据：附件覆盖的 75 条运行中没有任何一格命中安全硬门失败，因此该修正对现有
  语料影响为零；审计代理引用的"66 条 MODEL_ERROR / 33 条 EVIDENCE_IDS_EXIST"来自 `artifacts/`
  下更早代次的 run 目录（不同 evaluator 版本、不在语料内），不能用于本协议样本。
- **空计划拒绝（说明）**：`build_group(planned=())` 曾返回 `complete=True` 的空组（正式路径不可达），
  现以 `RELIABILITY_SCHEDULE_INVALID` 拒绝。
- **报告块路径（P1，纯文字）**：§13.3/spec/本报告写的 `strategy_metrics[*].reliability` 与实际
  `summary.json` 键不符，已统一改为 `strategies[*].reliability`。
- **`summary.json` 内联 evaluator 版本（说明）**：顶层新增 `evaluator_version`，使报告身份更易
  直接读取（此前只能经 manifest 摘要间接获得）。

## 边界与未做

- 真实测量另行冻结 manifest 并经批准；本轮无真实模型预算，因此**只达到"实现验证完成"**，
  不写"真实可靠性已验证"。
- 既有 `p1-formal-*` manifest 与历史结果不变；v22 与当前实现不兼容
  （见 T07 报告身份影响一节），其历史实验结果仍然有效。
- 跨场景置信区间未实现；§13.3 已写明若实现必须按场景聚类，不得把重复 trial 当作独立场景。
- 外部策略（T09/T11）与新场景的可靠性必须使用新的协议版本与新的冻结身份。
- 改动未提交 git。
