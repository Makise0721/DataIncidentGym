# Kernel 提交前 schema 核对——策略提示修订实施报告（2026-09-27）

- 设计：`docs/superpowers/specs/2026-09-27-kernel-profile-schema-check-design.md`
  （基线 `cb0437b`）。需求记录：`docs/requirements.md` M25 修订（策略提示修订，
  **偏好而非硬门**）。本报告只覆盖离线交付；冻结与真实测量不在本切片。
- 改动面：`src/data_incident_gym/prompts/diagnostic_kernel.md`（在既有
  "Before a final decision" 段前插入策略段，按设计稿 §3 英文文本逐句落地）、
  `KERNEL_PROMPT_VERSION` `p1.kernel.v18` → `p1.kernel.v19`
  （`diagnostic_agent.py`，实施前复核 v19 未被占用）。**无其它 src 改动**：
  controller/工具/输出 schema、账本、evaluator v5、预算 8/8/2/300、滚动暂停、
  两道提交门均不变；Static/NO_TOOL/PLANNER prompt 未触碰。

## 1. 身份差异核验

- 新增 `tests/unit/test_kernel_profile_schema_check.py` 身份面测试：三个 Kernel
  策略共享 `p1.kernel.v19` 且摘要 == prompt 文件 sha256；STATIC（p1.static.v5）、
  NO_TOOL（p1.no-tool.v1）prompt 版本与摘要逐字段不变；controller v22 与
  base prompt 摘要不变。
- T13 身份护栏 `test_frozen_v22_policy_surfaces_still_match_the_current_tree`
  按 M24 先例扩展：controller 身份键（M24 既有）与 **Kernel 家族 strategy prompt
  两键**（M25 新增）按预期漂移排除并断言——冻结侧 kernel 版本恰为
  `p1.kernel.v18`、当前恰为 `p1.kernel.v19`，漂移集合恰为三个 Kernel 策略；
  非 Kernel 策略 prompt 字段与冻结值逐字段相等；FIXED_RULE 整体不变。

## 2. 离线验收（设计 §5.1，真实 Kernel 路径）

`test_kernel_profile_schema_check.py`（16 项，全部入库）。设计表格逐行映射
（审计修订 2026-09-27：初版 §2 声称"覆盖行 1–6、行 7 由领域验证器承担"超出
实际测试，已按下列映射补齐并逐行更正；二次审计修订同日补充行 5 的两个预算
跳过用例与行 7 的同关系列不一致用例）：

| 设计行 | 覆盖测试与验证内容 |
| --- | --- |
| 1 补采与账本移除 | runner 级剧本（seq50 fixture + 假工具 + FunctionModel 走真实 `DiagnosisRunner.for_run`）：账本第 1–3 回合列出目标 schema 未采项 → 第 3 回合补采同关系 schema（一次、成功）→ 第 4 回合起账本移除 → 8 请求 / 7 工具 CONFIRMED；收据进入同一会话证据清单 |
| 2 复用与不冒充 | kernel 级：同关系 profile 与**其它关系**的 schema 都不清除目标未采项（跨类型/跨关系不替代）；runner 级剧本补充：已采 schema 的目标在决策回合不再列示 |
| 3 NO_SCHEMA／不可读 | runner 级 NO_SCHEMA 消融（`test_no_schema_ablation_skips_corroboration_without_new_gaps`）：无 schema 调用、无自动创建的 DISCRIMINATE_SCHEMA gap、CONFIRMED 终态规则不变；runner 级 seq59（`test_unreadable_target_is_not_probed_and_other_schemas_not_substituted`）：目标不可读时仅既有边界探针一次（真实收据），其它可读关系的 schema 不被当作替代调用 |
| 4 已有失败/阻断 | kernel 级：真实 RELATION_NOT_ALLOWED 收据保留为 BLOCKED gap、同参重调 `DUPLICATE_TOOL_CALL`、无伪造成功记录 |
| 5 预算只够决定性取证／最后请求 | runner 级两个专门用例（二次审计补充）：`test_open_opportunity_is_skipped_when_only_the_last_request_remains`——目标 schema 未采、工具启用且关系允许、机会真实开放，第 7 次业务调用后仅剩最后一个模型请求，脚本直接提交（账本断言 remaining==1 且 schema_uncollected 非空），全程零 schema 调用；`test_budget_reserved_for_decisive_history_skips_the_schema_check`——目标 profile 接受后机会即开放，但剩余预算恰好只够尚未采集的决定性 history 加最终提交（账本断言 remaining==2、两者均未采），脚本先采 history、跳过核对，终局仍 CONFIRMED。kernel 级补充：决定性取证耗尽工具预算后 schema 调用 `TOOL_CALL_LIMIT`、失败码不变 |
| 6 干扰关系不扫描 | runner 级（`test_schema_check_targets_only_the_root_cause_relation`）：两个关系都有 profile 且都可读，schema 调用恰一次、参数恰为目标（root-cause）关系；干扰关系从未被采集、决策回合仍留在未采清单 |
| 7 列不一致／不作变化证明 | runner 级同关系对（`test_same_relation_schema_profile_column_conflict_rejudges_to_abstention`，二次审计补充）：**同一关系（raw_customers）的 profile 与 schema 均被接受入库**，列集经断言确认互不一致（schema 声明 first_name/last_name 而 profile 覆盖 user_id/order_date/status）；脚本按约定重新判断——不把当前 schema 当变化证明或基线、不猜测修正，终态保持 INSUFFICIENT_EVIDENCE（零 claim、无 CONFIRMED gate）。预算边界如实记录：8 请求内容不下"该对 + 探针（探针拒绝占一次重试回合）+ b-variant 完整决定性集"，评估按合同给出 `REQUIRED_EVIDENCE_TYPES_PRESENT`——合同在正确起作用，测试只验证重判路径，不登记通过格。引用完整性由 runner 剧本补充断言：补采收据不进入任何 claim（采集完整性 ≠ 逐 claim 引用完整性） |

机制补充说明（来自行 3/7 用例的实现）：seq59 所在 coupon_b 场景的评估合同要求
schema 缺口声明，而该声明只能由真实边界探针收据派生——新提示的"不要求边界
探针"不解除评估对声明的要求，两者并存（探针走既有边界规则，核对走新策略段）。
**所有脚本只证明约定路径可执行、权限/计数/归档正确，不证明真实模型会作这些
选择；目标选择的"正确性"由脚本按公开账本执行来演示，不是模型泛化验证。**

## 3. 五格本地归档分析（设计 §5.2，只读严格加载）

v32 worktree（`cc1e181`）`load_evaluation_input_bundle` 五格全部严格加载成功：

| seq | 场景 | 已采类型 | schema | 工具/请求用量 | 失败码 |
| --- | --- | --- | --- | --- | --- |
| 14 | orphan_payment_coupon_a | run_results+profile(raw_payments)+history(raw_orders)+lineage | **无** | 4/8, 3/8 | REQUIRED_EVIDENCE_TYPES_PRESENT |
| 25 | duplicate_payment_coupon_a | run_results+profile(raw_payments)+lineage | **无** | 3/8, 2/8 | REQUIRED_EVIDENCE_TYPES_PRESENT |
| 29 | orphan_payment_coupon_a | 同 14 形态 | **无** | 4/8, 2/8 | REQUIRED_EVIDENCE_TYPES_PRESENT |
| 66 | duplicate_payment_coupon_a | 同 25 形态 | **无** | 3/8, 2/8 | REQUIRED_EVIDENCE_TYPES_PRESENT |
| 70 | orphan_payment_coupon_a | 同 14 形态 | **无** | 4/8, 2/8 | REQUIRED_EVIDENCE_TYPES_PRESENT |

run_id（14/25/29/66/70）：`6ff8c6aa…`、`61bf8e27…`、`e102710c…`、
`8e2f473f…`、`3f7fdbd3…`。五格均存在「同关系 profile 已接受、schema 未采、
预算充足」的形态；按设计，**未发生的新取证不能凭空重放**，反事实结论一律记
「未验证」，不登记为五格修复。

## 4. 验证记录（设计 §5.3）

- 初版定向回归（审计前）：10 项新用例 + 身份/提示/Kernel runner/重放/投影批次
  共 190 passed；完整单测 1159 passed / 5 skipped（exit 0）。
- 一次审计修订（2026-09-27，两项 P2）：设计稿单独入库（提交 `6166a6f`）；报告
  §2 改为逐行映射；新增 3 个 runner 边界用例（13 项全绿）；完整单测
  1162 passed / 5 skipped。
- 二次审计修订（2026-09-27，预算与列不一致两处 P2）：新增 3 个 runner 用例
  （最后请求跳过、预算留给决定性 history、同关系列不一致重判），新用例文件
  16 项全绿；完整单测 **1165 passed / 5 skipped**（exit 0，1:41）。
- `uv run ruff check .` 通过；`uv lock --check` 通过；`git diff --check` 干净。
- 按设计未重跑 integration/E2E（纯 prompt+版本变更，不触数据库与归档写入）。

## 5. 边界与后续

- 「脚本跑通 ≠ 模型改善」：本切片只交付一个可测策略候选；"修复五格""提升模型
  能力""可以继续 v32"均不是本切片结论。
- 后续（另行授权）：以旧/新 Kernel prompt 为对照的配对真实测量计划（固定模型、
  端点、场景、预算、重复次数、停止条件），主要结果为整格通过；若仅证据类型
  检查变好而整格通过未改善或预算失败增加，记录负面/混合结论并停止扩大该规则。
