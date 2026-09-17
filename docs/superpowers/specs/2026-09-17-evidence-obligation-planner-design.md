# T12 设计：公开证据义务规划器（候选策略）

- 日期：2026-09-17。状态：**设计待审**；本文只定义身份、对照、指标与离线验收集，实现随后进行。
- 依据：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T12；依赖 T05（回放库）、T06（场景准入与集合）、
  T07（弃答/引用指标）、T08（重复可靠性）、T09（策略协议）。
- 本文不改变现有 kernel / static 的 prompt、controller、账本或任何冻结身份；也不启动真实模型测量。

## 1. 候选策略身份

| 项 | 取值 |
| --- | --- |
| 枚举值 | `DiagnosticStrategy.EVIDENCE_PLANNER`（新增，`diagnosis.py`） |
| 策略名 | 公开证据义务规划器（evidence-obligation planner） |
| prompt 版本 | `p1.planner.v1`（新 prompt 文件，不改 kernel/static prompt） |
| controller 协议 | `p1.planner_controller.v1`（新的确定性计划校验层） |
| 政策身份 | `evidence_planner_policy_identity()`，与 `fixed_rule_policy_identity()` / kernel 身份并列 |
| 框架申报 | `StrategyDeclaration(framework="data-incident-gym", framework_version="p1.strategy_adapter.v1", deterministic=False)`，与 kernel 同模型同条件 |
| 工具 / 预算 / 可见上下文 | 与 kernel **完全相同**：六个只读工具、8/8/2/300、`incident_brief` + 关系白名单 |

**它与其他策略的差别**：模型不再直接输出"下一步调用哪个工具"，而是先提交一份**公开证据义务计划**——本步要
回答的问题、需要的证据类型与主体、以及该证据与既有证据的关系；确定性层只按公开规则校验该计划：

1. 工具与参数必须在申报的六工具白名单内（否则拒绝并把真实错误码写回计划回执）；
2. 计划引用的既有证据必须是本 run 已登记的 evidence ID（不得凭空引用）；
3. 剩余预算与截止时间由 harness 计数，计划不得自行扩张；
4. 计划中的义务只从**公开信号**推导：`incident_brief`（信号码、主体）、可观测关系白名单、以及此前工具收据的
   事实内容。`scenario_cards`、`ScenarioSpec`、期望状态与 `REQUIRED_EVIDENCE_TYPES_*` 均为管理/评分平面，
   **不得进入规划器**（`scenario_cards` 模块本身已声明诊断平面不得导入）。

模型仍负责：假设的判断、是否继续采证、拒绝后是否重规划、以及最终结论与引用。确定性层不替模型决定任何
根因或状态。

## 2. 对照（control）

对照必须是同条件对照，且不能把不同身份混成一个数字（T08 规则）：

| 角色 | 策略 | 说明 |
| --- | --- | --- |
| 处理组 | `EVIDENCE_PLANNER` | 本候选 |
| 主对照 | `DIAGNOSTIC_KERNEL`（`p1.kernel.v18` / `p1.controller.v19`） | 现任策略，prompt/controller/账本不变 |
| 第二对照 | `STATIC_SKILL` | 无 kernel 路径的基线 |
| 策略见证 | `FIXED_RULE`、`REFERENCE_ANALYST` | 确定性策略，给出"同一公开证据下可达结论"的上下界，不参与模型能力比较 |

- 同条件判据用 `StrategyDeclaration.comparison_identity`（框架、模型、工具、预算、可见上下文一致）——规划器与
  kernel 在这些字段上完全一致，差异只在于**政策身份**（prompt 与 controller 摘要）与代码路径；报告必须把这一
  点写成"政策身份不同"，不得表述为"同策略的两个版本"。
- 稳定性按 T08 分组：组 = 场景 × 策略 × 冻结身份；跨策略不混合；不完整组不出 `pass^k`。
- 历史 `p1-formal-v22` 及其之前的测量结果保持不变，规划器不复用其身份，也不回填。

## 3. 指标

**主指标**：整格通过率（evaluation `PASSED` 且无失败的适用 controller 门；与 `_evaluation_passed` 同口径）。

**次指标**（沿用既有口径，不另造第二套）：

1. T07 弃答与引用：过度弃答率、合格弃答率、状态混淆表、逐 claim 支撑覆盖、引用有效性；
2. T08 稳定性：按组的 `pass^k`，宏平均只在完整组上计算；
3. 成本：工具调用尝试、模型请求槽位、输出重试、耗时——全部取 harness 计数，自报用量仅作辅助。

**规划器专属描述性诊断**（只描述机制，不参与任何门禁）：

- 计划步数：提出 / 被校验接受 / 被拒绝（按真实错误码分类）；
- 拒绝后的重规划次数，以及重规划是否改变了义务集合；
- 提交时仍开放的义务（模型自己声明未满足的证据类型与主体）；
- 最终引用是否覆盖其声明的义务。

**明确不作为成功信号**：拒绝次数减少、工具调用变少、弃答率单独下降。它们只在主指标与合格弃答口径下解释。

## 4. 离线验收集

| 集合 | 用途 | 诚实标注 |
| --- | --- | --- |
| T05 回放库（`tests/fixtures/diagnostic_replays/index.json`） | 规划器接入后必须复现既定失败分类（REFERENCE_IMPLEMENTATION / SCORING / HARNESS），作为回归 | 离线、无模型；只证明软件行为 |
| T06 dev 场景（18 个，`config/scenario-sets.json`） | 机制校验、`scenario_cards` 的 A/B 对称性检查、确定性对照（FIXED_RULE / REFERENCE_ANALYST） | **全部为 dev**：均已在 `p1-formal-v22` 及之前用于 prompt/规则开发，不得称为"未见变体"，不得作为泛化证据 |
| 新变体（待生成） | 规划器的离线泛化检查与后续冻结测量的输入 | 见下 |

**新变体的来源、暴露与划分（生成时必须逐条记录）**：

- 来源：沿用既有场景生成器与突变类型（`scenarios.py` 中的 mutation 记录），每个变体记录生成器版本、seed、
  突变参数与基线 case；不允许手工挑选"更容易"的变体。
- 开发暴露：变体只允许公开面信息（brief、关系白名单、可观测性）与准入结论被参考；**其故障机制不得用于
  编写规划器 prompt 或规则**。每条变体记录"是否被规则开发看过"（默认 false，若为 true 必须显式标注并降级
  为 dev）。
- 划分依据：按 `config/scenario-sets.json` 的 `partition_rule`——按故障机制与任务结构分配，绝不按 seed 单独
  分配；A/B 对成对进出同一划分；一个已用于开发或提示的 case 永远不能再标为 holdout。
- 准入：每个新变体必须通过 T06 准入（认证 + 参考解可解 + 对称性），未通过者不进集合。
- 物化成本：新变体的认证需要 lab（Docker/PostgreSQL + dbt）实跑，属一次性、有界的运行；**执行前单独请求
  授权**，并记录运行的镜像、场景、命令与结果。在此之前，离线结论只覆盖回放库与 dev 场景。

## 5. 边界与不做

- 不修改 kernel/static 的 prompt、controller、账本、evaluator 判定规则、工具权限、预算 8/8/2/300 与既有
  冻结 manifest；规划器以新身份并行存在。
- 规划器不得读取私有平面（`config/scenarios`、`.dig/scoring-inputs`、metadata/evaluation 产物），也不得
  自动补声明、自动执行探针或替模型裁定缺口；如要引入此类能力，须另立策略身份并更新行为合同。
- 离线完成不代表能力提升；真实收益只能由新冻结身份下的比较判定，且需另行批准测量。

## 6. 实施步骤与验收

1. 身份与注册：`DiagnosticStrategy.EVIDENCE_PLANNER`、`p1.planner.v1` prompt、`p1.planner_controller.v1`、
   `evidence_planner_policy_identity()`，并接入 `policy_identity_for_strategy` 与清单校验路径；
2. 计划校验层：公开规则校验（白名单、参数、已登记引用、预算、截止时间），拒绝保留真实错误码并写回计划回执；
3. 运行接线：`for_run` 建立 T09 会话，终态经 `session.submit`，异常终态经 `session.cancel`（与既有 runner 同规则）；
4. 轨迹与指标：轨迹记录计划与义务，供 §3 的描述性诊断使用；
5. 回归：T05 回放全部复现；规划器走过的每条拒绝路径有确定性回归（缺参、越权工具、未登记引用、超预算、
   截止时间、终态后调用）；`same_condition` 声明与比较身份有回归；
6. 文档：需求新增 M20（规划器身份与计划校验合同）+ §13.x 指标接入说明，实施报告列出离线结果与未验证项。

**验收（离线）**：`ruff` 通过；全量单测通过；回放库分类不变；规划器在合成公开证据上完成"计划 → 校验 →
取证 → 提交"闭环且拒绝路径全部生效。**不属于验收**：真实模型收益、未见变体泛化、新冻结排程——它们需要
新变体物化与新身份测量各自获得批准。
