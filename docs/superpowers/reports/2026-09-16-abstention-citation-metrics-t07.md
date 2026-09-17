# T07 实施报告：弃答与引用诊断指标

- 日期：2026-09-16。基线 `e664b8e` + 本轮未提交工作树（T01–T06 交付之上）。
- 交付：
  - `evaluation.py`：把单元级判定拆成可复用的逐 claim 规则（`_health_claim_supported`、
    `_asset_claim_supported`、`_alert_subjects`），新增公开 API `claim_support_verdicts`、
    `claim_supported_by_records`、`ClaimSupportVerdict` 及
    `ALL_CLAIM_KINDS` / `APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS`。单元级
    `_claim_evidence_compatible`、`_health_evidence_valid` 改为调用同一实现。
    **evaluator 身份升为 `p1.evaluator.v3`**：重构过程中暴露并修复了一处健康声明校验缺陷
    （见下节），这是合同变更而非等价重构。
  - `benchmark_manifest.py`：`result_inputs_for_project` 公开（原 `_result_inputs_for_project`），
    供报告校验"运行代码 = 冻结身份"。
  - `benchmark_report.py`：`summary.json` / `report.md` 新增四个指标块——
    `abstention`、`status_confusion`、`claim_support`、`citation_quality`；
    统一 `_ratio(numerator, denominator, applicable_set, zero_denominator_reason)` 口径；
    `_validate` 新增身份校验（见"身份影响"）。
  - `docs/requirements.md`：M15 修订行 + §13.2「诊断质量指标（弃答与引用）」。
- 验证：`ruff check .` 通过；单测 **699 passed / 4 skipped**（T07 收口时快照；T08 与审计整改后
  当前工作树为 717。T07 新增 18 项：15 项指标夹具 + 2 项报告身份拒绝 + 1 项多 claim 健康校验
  回归）；`git diff --check` 通过（仅 AGENTS.md 既有 CRLF 提示）。

## 固定口径

| 指标 | 分子 / 分母 | 零分母原因 |
| --- | --- | --- |
| 可确认过度弃答 | 期望 CONFIRMED 且实际弃答 / 有效 CONFIRMED 格 | no valid confirmable cells |
| 健康过度弃答 | 期望 NO_INCIDENT 且实际弃答 / 有效健康格 | no valid healthy-control cells |
| 合格弃答 | 期望弃答 ∧ 弃答 ∧ 整格 PASSED / 有效期望弃答格 | no valid expected-abstention cells |
| 弃答 precision | 应弃答 ∧ 弃答 / 有效实际弃答格 | no valid cell abstained |
| 弃答 recall | 应弃答 ∧ 弃答 / 有效期望弃答格 | no valid expected-abstention cells |
| 状态混淆表 | 期望三态 × 实际四态（含 MODEL_ERROR）计数 | — |
| claim 支撑覆盖 | 受支撑的适用 claim / 适用 claim | no applicable structured claims in valid cells |
| 引用存在性 | 存在于清单的引用 / 全部引用（按格去重） | no citations in valid cells |
| 单条引用支撑 | 单条即可支撑的引用 / 根因+资产 claim 的引用 | no single-record claim citations in valid cells |
| 冗余敏感度 | 移除后翻转判定的引用 / 可移除引用 | no citations on supported applicable claims |

- **有效格**：适用**环境门**（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`）
  全部通过的格。代理侧违规（`EVIDENCE_IDS_EXIST` 伪造引用、`TOOL_ALLOWLIST_EXACT` 越权工具、
  `TRACE_READ_ONLY_SAFE` 写尝试、kernel 状态门）**不是**环境失效：它们计为失败试验并留在分母，
  否则模型可以用违规把最严重的失败洗成"环境问题"。隔离计数在
  `abstention.cells.invalid_excluded`、`status_confusion.invalid_cells` 单列。
- **MODEL_ERROR**：计入所在集合分母，绝不进入弃答分子；另有
  `status_confusion.model_error_rate`（分母为有效格）。既有 `_efficiency.model_error_rate`
  口径未动（分母为全部格），两者并列，标签区分。
- **零分母**：`rate`/`lower`/`upper` 为 null 且给出固定原因，不写 0 或 100%。
- **适用性**：跟随 evaluator 的 `CLAIM_EVIDENCE_COMPATIBLE` 门控（CONFIRMED→根因+资产；
  NO_INCIDENT→健康；`INSUFFICIENT_EVIDENCE`→不适用）。其余 claim 类型进
  `inapplicable_claim_types`，未知类型进 `unsupported_claim_types`，均不进入分母、不计"未支撑"。
- **引用指标**：只覆盖确定性可判定的结构化 claim；健康声明由多条记录联合证明，其引用从
  "单条引用支撑"分母中排除并单列 `health_claim_citations`；冗余是报告事实
  （`redundancy.note` 明示"not an error"），不是扣分项。

## 手工可验算夹具（`tests/unit/test_benchmark_quality_metrics.py`）

覆盖审计要求的六类场景，全部按格手工核算：

| 场景 | 断言要点 |
| --- | --- |
| 全弃答 | 过度弃答 2/2 = 1.0；健康/合格弃答分母为空 → null + 原因；precision 0/2（弃答全为过度弃答） |
| 全确认 | 过度弃答 0/2 = 0.0；recall 分母为空 → null |
| 健康误报 | 健康过度弃答 0/1；混淆表 NO_INCIDENT×CONFIRMED = 1 |
| 空集合 | 五项弃答指标全为 null + 原因；混淆表全 0；model_error_rate null |
| 模型失败 | MODEL_ERROR 计入分母（过度弃答 1/2）、不计入弃答分子；混淆表 CONFIRMED×MODEL_ERROR = 1；model_error_rate 1/2 |
| 无效环境 | 无效格（若计入本会是过度弃答）被隔离：有效 1、隔离 1，指标分母只用有效格 |

另含 claim/引用夹具：
`test_citation_metrics_hand_checked_for_joint_root_and_direct_asset`（存在性 3/3、单条支撑 1/3、
载荷 3/3——根因需失败 test + null profile 联合）、
`test_redundant_citations_are_reported_not_penalized`（资产 claim 双支撑引用 → 冗余 2、载荷 2/4）、
`test_health_claim_citations_are_excluded_from_single_record_support`、
`test_missing_citation_is_not_existence_and_not_support`、
`test_meaningless_citations_never_raise_coverage` 与单元级
`test_unrelated_citations_never_flip_the_cell_level_claim_gate`
（未支撑 claim 追加无关引用后 `CLAIM_EVIDENCE_COMPATIBLE` 仍为 False；追加**相关**引用则合法地
转为 True——两者在夹具中并列，避免把"补齐正确引用"误判为刷分）。

夹具过程中修正过一次自己的错误设计：最初把失败 test 的 node error 当作"无关引用"追加到根因
claim，门确实翻转为 True——因为那条记录本来就是根因规则消费的证据。改为追加规则不消费的记录
（血缘）后，见证成立。

## 报告身份校验（新失败路径）

`_validate` 现在先校验 `result_inputs_for_project(project_root) == manifest.result_inputs`
（evaluator 源码摘要、profile spec、scenario/diagnosis schema），再逐格校验
`load_scenario_spec(case_id).digest() == scenario_catalog[case_id].scenario_spec_sha256`。
两者任一不符即拒绝出报告：claim/引用指标用运行中的 evaluator 规则重算，身份不一致会混合两套
规则。回归：`test_reporter_fails_closed_when_result_inputs_drift`、
`test_reporter_fails_closed_when_a_scenario_contract_drifted`。

## 合同变更：健康声明校验缺陷修复（v2 → v3）

审计复核指出：`v2` 的 `_health_evidence_valid` 在循环体内 `return True`，**第一条健康 claim
通过即返回**，多 claim 诊断的后续 claim 从未被校验。抽成逐 claim 函数后行为变为"每条 claim
都必须成立"，更严格且符合合同本意，但属于 **evaluator 缺陷修复**，不能称为"判定语义未变"。

复现（旧实现从 `git show HEAD:src/data_incident_gym/evaluation.py` 载入，同一输入）：

| 输入 | v2 | v3 |
| --- | --- | --- |
| 单条有效健康 claim | True | True |
| 有效 claim + 指向无关关系（`raw_customers`）的第二条 claim | **True** | **False** |

处置：

- 显式列为合同变更：`evaluator` 升为 `p1.evaluator.v3`（`EVALUATOR_VERSION`），
  v3 语义写入 `_health_evidence_valid` docstring 与需求 M15（7）。
- `KNOWN_EVALUATOR_VERSIONS` 保留 `p1.evaluator.v2`，既有附件继续可加载；
  **旧证据（原始六文件、附件、归档评分、冻结 manifest 文件）不改写**。
- 回归：`test_health_validation_covers_every_claim_not_only_the_first`——两种 claim 顺序都必须
  拒绝，单元级 `CLAIM_EVIDENCE_COMPATIBLE` 与 `POSITIVE_HEALTH_EVIDENCE` 同时失败，且逐 claim
  判定为 `[True, False]`；单条有效 claim 仍通过（防过度收紧）。

## 归档一致性的正确解读（离线）

重构后，用 T02/T03 的离线重评机制对**全部 75 条归档运行**重新评分（无模型、无数据库、无 dbt），
逐项 diff 结果为 `same=75 bad=0`——75/75 报告"changed_checks: 无（与原归档评分逐项一致）"，
即 v2 附件在 v3 身份下仍可加载且判定逐项相同。这**只证明归档样本未触发上述健康声明修复**
（归档中不存在多 claim 健康诊断），不证明"判定语义未变"；语义变化的边界以上表为准。
期间共 16 次 `OFFLINE_SCORE_WRITE_FAILED` 记为**瞬时写入失败，原因未确认**：重试即成功，但未
捕获底层异常证据（重试成功不足以推出"Windows 写锁"等具体根因）；工具本身 fail-closed，未写出
半成品，与本轮改动无关。

派生评分目录状态：本轮先用过渡身份（v2 标签 + 新源码摘要）生成的 75 个目录已删除并按
`p1.evaluator.v3` 重新生成（避免留下正式身份之外的派生结果）；T03 时期的 3 个旧派生目录
（`f4cebf2a…`、`c0dbe554…`、`96e10f36…`）保持原样未动。

## 身份影响（必须知晓）

本轮 `evaluation.py` 的改动同时改变两样身份信息：源码摘要（进入 manifest 的 `result_inputs`）
与 evaluator 版本（`p1.evaluator.v2` → `v3`）。因此：

- 已冻结的 `config/benchmark/p1-formal-v22.json` 现在实测报 `result-input hashes drifted`
  （`verify_manifest`）：v22 **与当前实现不兼容**（源码摘要与 evaluator 版本双变），新正式测量
  必须先冻结新的 manifest 身份并经批准。这只影响 v22 能否在当前代码上重新校验/重跑，**不使其
  历史实验结果作废**——那些结果是在 v22 身份对应的实现上产生的，仍然有效。本轮未冻结新身份，
  因为冻结是独立的批准动作，且 T08 仍可能改动代码。
- 旧证据仍可读：`KNOWN_EVALUATOR_VERSIONS` 同时保留 `v2` 与 `v3`，既有附件（含 75 条归档运行）
  继续可加载与重评；原始六文件、附件、归档评分与冻结 manifest 文件均未改写。
- 派生评分按新的 scorer 身份（版本 + 源码摘要）生成新 `score_id`；既有派生目录不覆盖、
  不原地改分（见上节的目录处理说明）。
- 连带重签：`tests/fixtures/diagnostic_replays/index.json` 的 9 条 `protocol` 字段按 T05 约定
  "使用实现中的真实版本组合"重签为 v3，9 条回放期望在 v3 下逐条重跑通过；见 T05 报告的
  "协议重签（2026-09-16，随 T07）"。

## 审计整改（子代理审计，2026-09-16）

- **环境门与代理违规分离（P2，语义修正）**：初版把 `_SAFETY_GATES` 整体当作"无效环境样本"，
  其中 `EVIDENCE_IDS_EXIST`/`TOOL_ALLOWLIST_EXACT`/`TRACE_READ_ONLY_SAFE` 是代理违规。现改为
  环境门三码（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`）才隔离，
  代理违规留在失败分母；`_SAFETY_GATES` 仅继续服务既有 `invalid_gates`/INVALID 结论。
  回归：报告级 `test_agent_rule_violation_stays_a_failed_trial` 与
  `test_environment_gate_isolation_shows_in_the_abstention_sets`。
*事实澄清*：审计代理称"66 条 MODEL_ERROR 中 33 条命中安全门失败"，经复核该统计来自
  `artifacts/` 下**更早代次**的 run 目录（不同 evaluator 版本，且不在附件语料内）；附件覆盖的
  75 条运行里**没有任何一格**命中安全硬门失败，所以该定义漏洞在当前语料上影响为零，但真实测量
  前必须修正。
- **引用存在性指标的结构恒等（说明）**：能通过 `_validate` 的套件必然满足 `referenced ⊆ known`，
  故 `citation_quality.existence` 在正式报告里恒为覆盖率 1.0；实现保留它作为不变式与单条支撑的
  分母语境，并在 report.md 与 §13.2 注明。
- **两处 model error 列名区分（说明）**：`status_confusion.model_error_rate`（分母为有效格）与
  `efficiency.model_error_rate`（分母为全部格）在 report.md 列头改为
  "Model errors (valid cells)"/"Model errors (all cells)"。
- **文档路径修正（P1，纯文字）**：§13.3/spec/T08 报告写的 `strategy_metrics[*].reliability` 与
  `summary.json` 实际键不符，已改为 `strategies[*].reliability`。

## 审计整改（第二轮，2026-09-16）

- **controller 门不计入成功（P1）**：`_passed` 原先只看 `EvaluationResult.status`，而该状态只由证据检查
  推导、不含 `controller_checks`——kernel 门失败的 run 在合格弃答里会被当作"整格 PASSED"。现统一为
  `_evaluation_passed`：证据检查通过**且**无失败的适用 controller 门；`_efficiency` 的通过格选择同步改用
  同一判定（`passed_cells` 因此不再把 kernel 违规格算作通过）。
  语料影响：附件覆盖的 75 条运行中 controller 门失败为 0，数值不变，但口径漏洞已堵。
  回归：`test_kernel_gate_failure_is_a_failed_trial_not_a_success`（报告入口，含 `completed=36` 与
  `passed_cells=35` 的对照）。

## 边界与未做

- 未重跑数据库认证与真实模型；本轮所有验证为单测 + 离线重评。
- 引用指标不宣称自然语言蕴含准确率，不冠名 ALCE；未引入任何新硬门。
- T08（重复可靠性协议）未开始。改动未提交 git。
