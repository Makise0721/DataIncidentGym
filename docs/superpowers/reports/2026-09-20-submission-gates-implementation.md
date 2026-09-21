# I1/I2 提交门实施报告（2026-09-20）

- 依据：[实施 spec](2026-09-20-submission-gates-implementation-spec.md)（v2.1，含所有者 D2 裁决
  与三条约束、两轮审计的整改与复审记录）与[回归方案](2026-09-20-strategy-regression-proposal.md)。
- 状态：**离线实施与验收完成；两轮独立审计通过（第二轮 PASS WITH FINDINGS，全部处置）；未经
  授权的真实验证与测量未做。**

## 1. 交付物与提交

| 提交 | 内容 |
| --- | --- |
| `7726351` | `SubmissionPolicy` 模块（I1/I2 语义、固定拒绝码、内部错误 fail-closed） |
| `3c5639f` | validator 接线（static/kernel 输出回合）、线程传递（evaluation_runner 早加载 → 工厂 → for_run；benchmark/certification/集成测试兼容）、planner 门与 target_refusals 数据流修复 |
| `525b1b1` | 门致重试耗尽的可见终态 `MODEL_OUTPUT_RETRY_EXHAUSTED`（两处码表、门标记、kernel 安全码表） |
| `53a4445` | planner 钉住测试 |
| `c24f650` | **首轮审计整改**：kernel 决策投影（`_ProjectedKernelSubmission`）+ 门前移至 `finalize` 之前 + 三个工厂 + planner 留痕/映射 + F5/F7 测试 |
| `c195ffd` / `24e44ea` | spec 整改与复审记录 |
| `2ed40d0` | 复审处置：e2e 工厂透传 policy（M1）、planner 门事件时序（N1） |

## 2. 两轮独立审计

- **首轮 FAIL**：两 BLOCKER——I1 无法评估 `KernelDecision` 的 `ClaimEvidence`（形状不匹配，合法
  CONFIRMED 决策必被拒为内部错误）；门在 `finalize` 之后（内核锁定，重试不可能，实测无会话时
  **静默接受**、有会话时**证据清空**）。另有 3 MAJOR（工厂未更新、planner 留痕/映射缺失、e2e
  静默无门）、2 MINOR/NOTE。全部在 `c24f650` 整改。
- **第二轮 PASS WITH FINDINGS**：两 BLOCKER 以 A/B 复现证明修复（旧失效模式消失、证据保留、
  内核开放重试）；服务型集成测试**实跑通过**（`test_evaluation_runner.py` 1 passed；
  `test_planner_real_evidence.py` 4 passed，真实 PostgreSQL/dbt）；M1/N1 已修复，N2/N3 记录
  （方向 fail-closed / 归因经拒绝码可见）。

## 3. 离线验收证据

- 单测：**1089 passed / 5 skipped**；`ruff check .` 全绿。
- **语料回归（31 格终态 + 4 反事实变体）**：拒绝集逐格精确——I1 = {v29 seq 9, 13；v30 seq 9}、
  I2 = {v30 seq 8}；其余 27 格全接受（含 8 个 PASSED）；4 个变体（按所有者完整配方重建）
  evaluator PASSED 且门 ACCEPT；**KernelDecision 形状重放**：15 个 kernel 格经同一投影与
  Diagnosis 重放逐格一致、零 `GATE_INTERNAL_ERROR`。
- 精度定理成立：任何 evaluator PASSED 的提交必然通过两门（语料零误拒）。
- 零冻结 schema 漂移：`Diagnosis.model_json_schema()` 哈希 `1093da7b…170b` 逐字不变（运行时
  校验器扩展，非 schema 变更）。

## 4. 身份面变更清单（下一身份冻结/发布时随附，本轮不动）

1. `STRATEGY_PROTOCOL_VERSION` 需要升版（控制器行为变更：新增两门与耗尽终态码）。
2. `controller_protocol_version` 载荷（`_build_policy_surface`）随之变化——即使仅升版本号，
   新清单将绑定新载荷摘要。
3. `docs/requirements.md`：M17/M18 相关拒绝码与重试语义文本需修订（新增
   `CLAIM_SUPPORT_REQUIRED`/`GAP_RECEIPT_REQUIRED`/`GATE_INTERNAL_ERROR` 与
   `MODEL_OUTPUT_RETRY_EXHAUSTED` 终态；总预算 8/8/2/300 不变）。
4. 协议 spec §4 拒绝词汇与 `examples/isolation_acceptance.py` 固定码断言（随 F6 推迟项一并）。
5. `Diagnosis` 合同码表已扩展（运行时校验器），manifest `result_inputs` 不受影响（见 §3）。

## 5. 明确未做（按验收阶梯）

- **有界真实验证**与**新身份测量**均未进行，属阶梯第 2/3 步，须另行授权；届时以拒绝码出现率与
  后续修复行为为观察指标，与两批基线分离报告。
- 外部 harness 会话装门（F6）与 N2（`SCENARIO_LOAD_FAILED` 组合）推迟，登记于 spec。
- 未 push、未动归档、未重冻、未重跑任何测量。
