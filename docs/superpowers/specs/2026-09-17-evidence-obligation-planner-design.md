# T12 设计：公开证据义务规划器（候选策略）

- 日期：2026-09-17 首版；同日在审计意见后修订（计划校验层可执行定义、新变体 dev-only、T05 验收更正、实跑清单）。
- 状态：**设计待审**；本文只定义身份、计划校验层、对照、指标、离线验收集与实跑清单，实现随后进行。
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

模型侧新增的是**输出工具**（声明意图），不是新的证据工具：证据面仍是六工具。确定性层不替模型决定根因、
状态、是否继续采证或如何收尾。

## 2. 计划校验层：可执行定义

### 2.1 输出工具（模型可见）

| 输出工具 | 参数 | 作用 |
| --- | --- | --- |
| `plan_step` | `tool_name`、`arguments`、`intent` | 声明下一步要调用的只读工具、参数与公开理由（`intent` 只入轨迹，不参与判定） |
| `close_obligation` | `obligation_id`、`outcome`（`SATISFIED`/`REVOKED`）、`evidence_ids`、`note` | 关闭一条义务：满足（必须引用**本次调用真实返回**的证据）或撤销（必须给出公开理由，如"该关系不在可观测白名单"） |
| `submit_diagnosis` | 与 T09 `FinalSubmission` 同形 | 终态提交，规则不变（`EVIDENCE_NOT_REGISTERED`、输出重试预算等） |

**义务的身份是推导出来的，不是模型自由命名的**，而且必须覆盖全部有效参数：

```text
obligation_id = "<tool_name>:<canonical(arguments)>"      # 规范 JSON：键排序、无空白、转义值
```

因此同一次调用的不同参数是不同的义务：`get_dbt_lineage:{"direction":"upstream","node_id":"model.x"}` 与
`...{"direction":"downstream",...}` 是两条独立义务——关闭上游不会把下游判成已关闭。编码用**规范 JSON**
而不是 `name=value` 拼接：后者不是单射，`{"direction":"upstream,node_id=x","node_id":"y"}` 与
`{"direction":"upstream","node_id":"x,node_id=y"}` 会拼出同一串；JSON 会转义每个值，不同参数集合不可能
碰撞到同一 id。`evidence_kind` 与 `subject` 是**展示字段**（报告与诊断用），不参与身份。六工具映射：

| 工具 | 展示 kind | 展示 subject | 参与身份的规范化参数 |
| --- | --- | --- | --- |
| `get_dbt_run_results` | `DBT_RUN_RESULTS` | `run_id` | `run_id` |
| `get_dbt_node_error` | `DBT_NODE_ERROR` | `node_id` | `node_id`, `run_id` |
| `get_dbt_lineage` | `DBT_LINEAGE` | `node_id` | `direction`, `node_id` |
| `get_relation_schema` | `RELATION_SCHEMA` | `relation_name` | `relation_name` |
| `get_relation_data_profile` | `RELATION_DATA_PROFILE` | `relation_name` | `relation_name` |
| `get_relation_history` | `RELATION_HISTORY` | `relation_name` | `relation_name` |

- **创建**：`plan_step` 首次引用某个推导出的 `obligation_id` 即登记该义务（状态 `OPEN`）；重复相同
  `(tool_name, arguments)` 命中同一条义务，不新建。
- **满足**（四条同时成立，缺一即拒）：
  1. 该义务存在**最近一次真实调用**（已经执行过）；
  2. 该次调用**被后端接受**（`accepted=true`，没有后端错误码）；
  3. 该次调用**返回非空证据**；
  4. `evidence_ids` **非空**，且全部来自该次调用。
  违反 1–3 → `PLAN_NO_EVIDENCE_FROM_LAST_CALL`（后端拒绝或空结果都不能标为 `SATISFIED`）；违反 4 →
  `PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL`。
- **撤销**：`close_obligation(REVOKED)` 需要非空公开理由（否则 `PLAN_REVOKE_REASON_REQUIRED`）；后端拒绝
  或空结果之后可以撤销（这正是"用真实拒绝收据支撑的合格弃答"路径），但撤销不等于满足。
- **outcome 运行时校验**：`close_obligation` 的 `outcome` 只接受 `SATISFIED`/`REVOKED`，其余任何值
  （含大小写变体与空串）一律 `PLAN_OUTCOME_INVALID`，不得落到"默认当撤销"的分支——静态类型注解是文档，
  不是校验。
- **已关闭义务固定行为**：对 `SATISFIED`/`REVOKED` 义务再次 `plan_step`（同 id）或再次 `close_obligation`
  一律拒绝，码固定为 `PLAN_OBLIGATION_CLOSED`，不改变任何状态、不消耗工具尝试；不支持重新打开——若模型
  想再验证同一 `(tool, arguments)`，那是设计上不允许的重复，模型应转而推进其它义务。
- **重规划**：任何一次拒绝之后，模型可以继续用 `plan_step` 指向另一条义务（或先撤销）。重规划不是独立操作，
  就是"下一次 `plan_step`"。

### 2.2 状态机

```text
PLANNING ──plan_step(校验通过)──▶ EXECUTING ──▶ 真实 ToolReceipt ──▶ PLANNING
    │                                  （harness 同步执行一次工具调用，不流水线）
    ├── plan_step/close_obligation 校验拒绝（未触达后端）──▶ PLANNING（+1 输出重试）
    ├── submit_diagnosis 被拒（T09 规则）──▶ PLANNING（+1 输出重试）
    ├── submit_diagnosis 接受 ──▶ FINAL
    └── cancel ──▶ CANCELLED
       输出重试耗尽 ──▶ OUTPUT_RETRY_EXHAUSTED；超时 ──▶ DEADLINE_EXCEEDED；工具预算耗尽后 plan_step 校验拒绝
```

一次 `plan_step` 恰好对应**一个**工具请求，且在同一个模型回合内同步执行：模型看不到"已提交但未执行"的
中间状态，也就不存在一个计划里连续多步、或中途改主意的语义空洞。没有"计划文件"落盘，计划只存在于会话
状态与轨迹里。

### 2.3 预算与计数

| 事件 | 工具调用尝试 | 计划拒绝计数 | 模型请求槽位 | 产生对象 |
| --- | --- | --- | --- | --- |
| `plan_step` 校验通过并执行（后端接受或拒绝都算） | **+1** | 0 | 该回合已领取 | 真实 `ToolReceipt`（后端错误码、证据记录） |
| `plan_step` 校验拒绝（缺参、多参、越权工具、外来 run、义务已关闭、预算已空） | **0** | **+1** | 该回合已领取 | `PlanVerdict`（校验码） |
| `close_obligation` 被拒（未知/已关闭义务、非本次证据、空证据、缺撤销理由） | 0 | +1 | 同上 | `PlanVerdict` |
| `submit_diagnosis` 被拒 | 0 | 0 | 同上 | `SubmissionReceipt`（**T09 既有规则不变**） |
| 计划拒绝计数达到上限 | — | — | — | 后续 `plan_step`/`close_obligation` 一律 `PLAN_OUTPUT_RETRY_EXHAUSTED` |

计数口径说明（避免与 T09 重复记账）：计划拒绝计数上限取同一个数值常量 `output_retry_limit`（2），但它是
**规划器自己的计数器**，与 T09 会话里"提交被拒"的计数各自独立、在快照中分别报告；`submit_diagnosis` 仍然
只消耗 T09 的输出重试预算。`close_obligation` 与 `submit_diagnosis` 不消耗工具预算；`plan_step` 只要执行就
消耗，与被接受的次数无关。

### 2.4 校验拒绝 ≠ 真实拒绝收据

- **码表分离**：校验层只使用 `PLAN_*` 码（`PLAN_SESSION_CLOSED`、`PLAN_TOOL_NOT_ALLOWLISTED`、
  `PLAN_ARGUMENTS_INVALID`、`PLAN_OUTCOME_INVALID`、`PLAN_RUN_SCOPE_MISMATCH`、`PLAN_UNKNOWN_OBLIGATION`、`PLAN_OBLIGATION_CLOSED`、
  `PLAN_NO_EVIDENCE_FROM_LAST_CALL`、`PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL`、`PLAN_REVOKE_REASON_REQUIRED`、
  `PLAN_OUTPUT_RETRY_EXHAUSTED`、`PLAN_TOOL_BUDGET_EXHAUSTED`、`PLAN_DEADLINE_EXCEEDED`）；
  后端拒绝保留真实错误码（如 `RELATION_NOT_ALLOWED`），两类码不共用命名空间。
- **校验顺序固定**（同一请求同时命中多条时按此顺序给出第一个码）：会话已终态 → 截止时间 →
  **计划拒绝预算是否已耗尽** → 工具名/outcome → 参数键集合与类型 → run 作用域 → 义务是否已关闭 →
  工具预算。预算检查刻意排在具体规则之前：一旦耗尽，后续每次操作都返回
  `PLAN_OUTPUT_RETRY_EXHAUSTED`，而不会继续产出新的具体拒绝码、也不会继续抬高拒绝计数（只增加
  `plan_operations_blocked`）。
- **对象分离**：校验拒绝产生 `PlanVerdict`，**绝不**产生 `ToolReceipt`；`ToolReceipt` 的
  `evidence`/`evidence_ids`/`error` 只在真实调用之后出现。没有后端调用就没有任何形式的"拒绝收据"。
- **轨迹分离**：轨迹事件类型区分 `PLAN_*` 与 `TOOL_*`，报告与评估读取时不可能把校验拒绝当成后端拒绝，
  也不可能把"计划被拒"计成一次工具尝试或一次 backend refusal。
- **反作弊**：校验层不写证据、不登记证据、不改计数；它只回答"这一请求能否交给后端执行"。

## 3. 对照（control）

| 角色 | 策略 | 说明 |
| --- | --- | --- |
| 处理组 | `EVIDENCE_PLANNER` | 本候选 |
| 主对照 | `DIAGNOSTIC_KERNEL`（`p1.kernel.v18` / `p1.controller.v19`） | 现任策略，prompt/controller/账本不变 |
| 第二对照 | `STATIC_SKILL` | 无 kernel 路径的基线 |
| 策略见证 | `FIXED_RULE`、`REFERENCE_ANALYST` | 确定性策略，给出"同一公开证据下可达结论"的上下界，不参与模型能力比较 |

- 同条件判据用 `StrategyDeclaration.comparison_identity`（框架、模型、工具、预算、可见上下文一致）：规划器与
  kernel 在这些字段上完全一致。但**这不足以宣称完整预算条件相同**——规划器多了一道 kernel/static 没有的
  策略门：计划拒绝预算（上限 2，与 T09 的 `output_retry_limit` 数值相同但是**独立计数器**）。因此报告必须
  同时写明两点：政策身份不同（prompt 与 controller 摘要），且规划器附加了这项策略规则与计数；不得表述为
  "同策略的两个版本"，也不得表述为"预算条件逐项相同"。两个计数器（计划拒绝 / 提交被拒）在任何表格中分别
  列出，不合并、不相加。
- 稳定性按 T08 分组：组 = 场景 × 策略 × 冻结身份；跨策略不混合；不完整组不出 `pass^k`。
- 历史 `p1-formal-v22` 及其之前的测量结果保持不变，规划器不复用其身份，也不回填。

## 4. 指标

**主指标**：整格通过率（evaluation `PASSED` 且无失败的适用 controller 门；与 `_evaluation_passed` 同口径）。

**次指标**（沿用既有口径，不另造第二套）：T07 弃答与引用、T08 按组的 `pass^k`、成本（工具尝试、模型请求、
输出重试、耗时，全部取 harness 计数）。

**规划器专属描述性诊断**（只描述机制，不参与任何门禁，且必须分别统计 `PlanVerdict` 与 `ToolReceipt`）：

- `plan_step` 提出 / 校验拒绝（按 `PLAN_*` 码分类）/ 执行次数；预算耗尽后被挡下的尝试次数
  （`plan_operations_blocked`，与 `plan_refusals_used` 分开记，被挡下的尝试不抬高拒绝计数）；
- 义务：登记数、满足数、撤销数、提交时仍 `OPEN` 的义务；
- 后端拒绝（真实码）后是否重规划、重规划是否换了义务；
- 最终引用是否覆盖其声明为 `SATISFIED` 的义务。

**明确不作为成功信号**：拒绝次数减少、工具调用变少、弃答率单独下降。

## 5. 离线验收集

| 集合 | 用途 | 诚实标注 |
| --- | --- | --- |
| T05 回放库（9 条 `synthetic-mechanism` 条目） | 回归：模块与 runner 改动后必须**全部保持通过** | 各条目断言的是**各自的**机制——kernel/evaluator/runner 的原失败码（如 `INSUFFICIENCY_GAP_REQUIRED`、`ASSET_CLAIM_NAME_NOT_IDENTIFIER`、`CLAIM_EVIDENCE_COMPATIBLE`）、反事实恢复与第 9 次调用预算边界；**不是**统一的 `REFERENCE_IMPLEMENTATION/SCORING/HARNESS` 分类（那是认证失败归类）。`historical-replay` 条目在索引里明确为待补 |
| 新增 planner 专属回归 | 用合成公开证据驱动规划器路径，逐条复现该路径下可验证的机制 | 实施报告必须给出"机制 → 路径 → 断言"的映射表；**不预先声称覆盖全部** |
| T06 dev 场景（18 个） | 机制校验、A/B 对称性、确定性对照（FIXED_RULE / REFERENCE_ANALYST） | **全部 dev**：均已在 `p1-formal-v22` 及之前用于 prompt/规则开发，不得称为"未见变体" |
| dev 扩展回归集（新变体，见 §6） | 新机制组合下的规划器离线回归 | 本轮**只作为 dev 扩展回归集**；不进入 holdout，不用于泛化结论 |

**回放通过 ≠ 规划器能力已验证**：T05 现有条目走的是 kernel/evaluator/runner 路径，规划器接入后它们只证明
"没有被破坏"。规划器自身的"计划 → 校验 → 取证 → 提交"闭环与全部拒绝路径，由 §7 的新增回归单独证明。

## 6. 新变体：本轮限定 dev 扩展回归集

**规则冲突（已核对）**：`scenario_admission.build_admission` 对 holdout 成员直接给出 `HOLDOUT_SCENARIO`
拒绝，因此当前准入流程**无法**为 holdout 出具准入结论。由此确定本轮口径：

- 新变体一律登记为 **dev 扩展回归集**，可以走 T06 准入；
- **禁止**"先按 dev 通过准入，再回标 holdout"；
- 若将来需要未见评估用途，必须**单独定义受控认证与准入流程**（独立的登记与报告通道、准入规则不含
  `HOLDOUT_SCENARIO` 却有等价的隔离要求、变体在提交前不得被规则开发看到、划分由机制与任务结构决定），
  并在批准后另行实施。本轮不实现该通道。

**变体生成参数（每个变体必须显式记录）**：`incident_case_id`（新，不占用既有 id）、`fault_family`（沿用
既有注入机制，不新增注入代码路径）、`variant_role`、`answerability`、`seed`（固定 fixture commit 与
`seed_names`，A/B 对共享同一个 seed）、`incident_brief`（A/B 对表面症状一致，由 `ab_symmetry_findings` 强制）、
期望与可接受答案（私有，不进公开面）、A/B 伙伴 id、**开发暴露记录**（是否被规划器 prompt/规则开发参考过；
默认 false，若为 true 必须显式标注）。

**数据库实跑清单（有界）**：

- 规模与运行上限：**4 个变体 = 2 个 A/B 对**，每个 A/B 对**只跑一次 `certify --admit`**（该命令先跑
  `certify_catalog` 一次，再用同一份认证报告构建准入报告，不会二次认证）→ 合计 **4 次场景认证 / 2 次
  certify 调用**。失败不追加、不换 seed 重跑（避免 seed shopping）。
- 前置：Docker Desktop + PostgreSQL 可用、submodule 已初始化、`uv sync --frozen` 成功；记录镜像与
  fixture commit。变体契约文件先落盘，并登记进 `config/scenario-sets.json` 的 `dev_scenarios`
  （未登记会在准入阶段得 `UNREGISTERED_SCENARIO`）。
- 命令（逐对执行，输出到独立目录，不覆盖既有产物）：
  1. 仅在需要清理遗留环境时：`uv run data-incident-gym lab reset <case_id>`（**必须带 case_id**；认证自身的
     pipeline 会 reset→inject→build→diagnose→evaluate→recover，正常情况下不需要手工 reset）；
  2. `uv run data-incident-gym certify --case <a> --case <b> --admit --output artifacts/admissions/t12-pair-<n>.json`
     —— 认证与准入一次完成，`--admit` 报告内嵌该次认证结果。
- **失败停止条件**（任一命中即停止整批并如实报告，不重试）：认证未通过（`CERTIFICATION_FAILED` 或参考解
  不可解）、准入任一 `reason` 非空（含 `SYMMETRY_MISMATCH`、`CARD_INCOMPLETE`）、A/B 对称性不满足、
  lab 环境无法恢复。
- 产物：认证/准入报告、变体契约文件、`config/scenario-sets.json` 的 dev 条目、以及一份运行记录（命令、
  镜像、fixture commit、结果、停止原因）。

## 7. 边界与不做

- 不修改 kernel/static 的 prompt、controller、账本、evaluator 判定规则、工具权限、预算 8/8/2/300 与既有
  冻结 manifest；规划器以新身份并行存在。
- 规划器不得读取私有平面（`config/scenarios`、`.dig/scoring-inputs`、metadata/evaluation 产物）；`scenario_cards`
  仍是管理平面，诊断平面不得导入。
- 不发生"无后端调用的拒绝收据"（§2.4）；不自动补声明、不自动执行探针、不替模型裁定缺口。
- 离线完成不代表能力提升；真实收益只能由新冻结身份下的比较判定，且需另行批准测量。

## 8. 实施步骤与验收

1. 身份与注册：枚举、`p1.planner.v1` prompt、`p1.planner_controller.v1`、`evidence_planner_policy_identity()`，
   接入 `policy_identity_for_strategy` 与清单校验路径；
2. 计划校验层：按 §2 实现数据结构、状态机、`PLAN_*` 码表、`PlanVerdict` 与 `ToolReceipt` 的分离；
3. 运行接线：`for_run` 建立 T09 会话，终态经 `session.submit`，异常终态经 `session.cancel`；
4. 轨迹与指标：轨迹区分 `PLAN_*` / `TOOL_*`，供 §4 描述性诊断使用；
5. 回归：T05 九条回放全部保持通过；新增规划器路径回归（计划校验通过并执行、六类 `PLAN_*` 校验拒绝、
   后端真实拒绝码保留、未登记引用、输出重试耗尽、预算与截止时间、终态后调用）；`PlanVerdict` 不计入
   工具尝试、且从不携带证据记录；
6. 文档：需求新增 M20（规划器身份与计划校验合同，含 §2 的表）+ 指标接入说明；实施报告给出
   "机制 → 路径 → 断言"映射表与未验证项。

**验收（离线）**：`ruff` 通过；全量单测通过；T05 九条回放全部通过；规划器在合成公开证据上完成
"计划 → 校验 → 取证 → 提交"闭环，且全部校验拒绝路径生效、未产生任何伪收据。**不属于验收**：真实模型收益、
未见变体泛化、新冻结排程——它们需要 §6 的新变体物化与新身份测量各自获得批准。
