# T05 实施报告：版本化失败回放库

- 日期：2026-09-16。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T05；依赖 T02–T04 已交付。
- 交付物：
  - `tests/fixtures/diagnostic_replays/index.json`：回放目录（`p1.diagnostic_replays.v1`），每条
    记录 id / group / source / protocol / trigger / expected / counterfactual，protocol 使用实现
    中的真实版本组合（见下方协议重签）。
  - `tests/unit/test_diagnostic_replays.py`：10 项测试 = 1 项守卫（索引与实现的 id 集合、schema、
    group、protocol 必须一致，防止目录与测试漂移）+ 9 个回放。

## 协议重签（2026-09-16，随 T07）

T07 修复健康声明校验缺陷并把 evaluator 升为 `p1.evaluator.v3`（见 T07 报告的合同变更章节）。
按 T05 约定"protocol 使用实现中的真实版本组合"，9 条目录项统一重签为
`p1.kernel.v18 / p1.controller.v19 / p1.evaluator.v3`（原 v2）；9 条回放的期望在 v3 下逐条重跑
通过，守卫测试继续强制目录与实现一致。本次只做 protocol 字段的定向替换（该 fixture 目录尚未纳入
git，无法用 diff 佐证改动范围，只能以当前内容与守卫一致为准）；entry 的
source/trigger/expected/counterfactual 与 `origin` 字段未被改写，条目仍是 `synthetic-mechanism`，
历史回放仍待补建。

## 回放清单（三组，均为合成机制回归）

**覆盖声明**：当前 9 条回放全部是 `synthetic-mechanism`——以合成公开证据驱动真实
kernel/evaluator/runner，钉住机制级的失败触发点与反事实恢复。它们**不是**对某一次历史运行的
回放；历史回放须待真实运行留存的 trace 与产物归档后补建（索引的 `pending_historical_replays`
已记录所需输入：原始 run_id、产物摘要、来源引用与脱敏说明）。每条目的 `origin` 字段标注来源
性质；`historical-replay` 类条目必须附原始 run_id、内容摘要与脱敏说明才能入列（守卫测试强制）。

| 组 | id | 层 | 原失败 | 反事实恢复 |
| --- | --- | --- | --- | --- |
| 本轮暴露 | reference-missing-required-schema | evaluator | `REQUIRED_EVIDENCE_TYPES_PRESENT` | 补采并引用 schema 后整格通过；并断言认证分类为 `REFERENCE_IMPLEMENTATION` 而非 `SCORING` |
| 本轮暴露 | reference-wrong-transformation-subject | evaluator | `INSUFFICIENCY_GAP_DECLARED` | 以 `model.jaffle_shop.stg_orders` 声明后通过 |
| 历史 | over-abstention-with-complete-evidence | kernel | `INSUFFICIENCY_GAP_REQUIRED`（证据采齐仍弃答且无声明） | 同一关闭缺口上提交 CONFIRMED 被接受 |
| 历史 | declared-gap-without-receipt | kernel | `UNRESOLVED_EVIDENCE_UNBOUND`（声明的缺口无真实拒绝） | 声明实际被拒的关系后 finalize 成功 |
| 历史 | incomplete-gap-matrix | evaluator | `INSUFFICIENCY_GAP_DECLARED` | 补全缺口矩阵后通过 |
| 引用/资产/预算 | cross-claim-citation-not-substitutable | kernel | `ASSET_CLAIM_EVIDENCE_INCOMPATIBLE` | 资产 claim 改引血缘记录后被接受 |
| 引用/资产/预算 | asset-id-must-be-canonical | kernel | `ASSET_CLAIM_NAME_NOT_IDENTIFIER` | 规范节点 ID 后被接受 |
| 引用/资产/预算 | schema-wrong-relation-accepted-by-kernel-rejected-by-evaluator | kernel + evaluator | kernel 接受错误关系 schema（仅要求"上游关系事实"），evaluator `CLAIM_EVIDENCE_COMPATIBLE` 拒绝 | 改引突变关系的数据画像后整格通过 |
| 引用/资产/预算 | budget-boundary-ninth-call-refused | runner | 第 9 次调用 `TOOL_CALL_LIMIT` | 边界断言：实际执行 8 次、拒绝调用不落证据 |

## 已知假设（T04 遗留，本轮补充反例）

参考解的"源关系名 token 匹配 stg 模型名"是已知假设：`raw_orders → 含 orders 的 stg 模型`。
反例测试钉住两处边界：改名后的 seed（无匹配 token 时回退到最近距离 1 模型）与歧义名称
（多个 stg 模型匹配时按血缘顺序确定性取第一个）。见
`tests/unit/test_reference_solver.py::test_transformation_subject_falls_back_on_unmatched_staging_name`
与 `…::test_transformation_subject_is_deterministic_under_ambiguous_names`。

## 分类口径修正（随本轮审计落地，两次修订）

- 第一次修订：缺采必需证据类型属于**参考实现未满足证据合同**
  （`REFERENCE_IMPLEMENTATION`），不再因根因与资产正确而归为 `SCORING`；认证新增
  `REQUIRED_EVIDENCE_TYPES_COLLECTED` 见证核对。
- 第二次修订（审计指出"采集不等于引用"）：evaluator 的
  `REQUIRED_EVIDENCE_TYPES_PRESENT` 检查的是**最终诊断引用**的证据类型，不是证据库存。
  认证因此新增 `REQUIRED_EVIDENCE_TYPES_CITED` 见证核对；"schema 已采集、最终诊断未引用"
  归为 `REFERENCE_IMPLEMENTATION`。`SCORING` 仅在合同要求的全部核对（状态、根因、资产、
  缺口矩阵、收据、必需证据类型已采集**且已引用**）均满足而 evaluator 仍拒绝时成立。
  回放 1 将两个口径（缺采、采而不引）都固化为回归。

## 验证

| 检查 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run pytest tests/unit/test_diagnostic_replays.py -q` | 10 passed |
| `uv run pytest tests/unit -q` | 640 passed, 4 skipped |
| `git diff --check` | 通过 |

未运行：integration/e2e（回放库为纯离线测试，无数据库依赖；未修改 src/ 与既有测试）。
真实模型行为仍未测量（归 T08）。

## 边界与后续

- 回放覆盖计划 T05 列出的机制（采齐后过度弃答、必要收据未采集、缺口矩阵不全、跨 claim 引用、
  规范资产 ID、schema 错关系被 kernel 接受但 evaluator 拒绝、预算边界）；协议拒绝/超时分类已由
  既有 `MODEL_PROTOCOL` 观测与单测覆盖，未重复建档。
- T06（场景卡片、准入命令、开发/保留集管理）以本目录与 `certify` 为基础。
