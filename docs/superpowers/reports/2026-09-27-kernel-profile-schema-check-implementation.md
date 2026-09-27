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

`test_kernel_profile_schema_check.py`（10 项，全部入库）：

- **机制护栏**（kernel 级）：profile 或其它关系的 schema 不清除目标关系的
  schema 未采项（账本按工具×类型相减，跨类型/跨关系不替代）；已记录的 schema
  拒绝收据（RELATION_NOT_ALLOWED）保留为 BLOCKED gap、同参重调
  `DUPLICATE_TOOL_CALL`、无伪造成功记录；工具预算耗尽后 `TOOL_CALL_LIMIT`
  原码不变，model_requests_used=8 时 remaining=0。
- **runner 级剧本**（真实 `DiagnosisRunner.for_run` + FunctionModel + 假工具，
  seq50 公开 fixture）：账本第 1–3 回合列出目标 schema 未采项 → 第 3 回合按新
  规则补采同关系 schema（一次，成功）→ 第 4 回合起账本移除该项 → 提交
  CONFIRMED 终态；全程 8 请求 / 7 工具调用，schema 收据进入同一会话证据清单，
  唯一且晚于 profile。
- 预算与权限断言覆盖设计表格行 1–6；行 7（schema/profile 列不一致）由既有
  领域验证器（root-cause 证据校验）承担，本切片无新逻辑。**这些脚本只证明
  约定路径可执行、权限/计数/归档正确，不证明真实模型会作这些选择。**

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

- 定向回归：新用例 10 项 + 身份/提示/Kernel runner/重放/投影批次共 **190 passed**。
- 收口：**完整单测 1159 passed / 5 skipped**（exit 0，2:15）。
- `uv run ruff check .` 通过；`uv lock --check` 通过；`git diff --check` 干净。
- 按设计未重跑 integration/E2E（纯 prompt+版本变更，不触数据库与归档写入）。

## 5. 边界与后续

- 「脚本跑通 ≠ 模型改善」：本切片只交付一个可测策略候选；"修复五格""提升模型
  能力""可以继续 v32"均不是本切片结论。
- 后续（另行授权）：以旧/新 Kernel prompt 为对照的配对真实测量计划（固定模型、
  端点、场景、预算、重复次数、停止条件），主要结果为整格通过；若仅证据类型
  检查变好而整格通过未改善或预算失败增加，记录负面/混合结论并停止扩大该规则。
