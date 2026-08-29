# DataIncidentGym 决策记录

本文件记录已经作出、且会影响实现或验收的项目决策。它解释“为什么这样做”，不替代 [`docs/requirements.md`](docs/requirements.md) 中的需求合同，也不承担实施计划或逐命令日志的职责。

## 2026-08-24：M1 Ubuntu CI 推送

### 背景

M1 Task 1–9 已在本地 Windows、PowerShell、真实 PostgreSQL 和 dbt 环境中验证，但最终门槛还要求 Ubuntu CI。

### 决策

用户授权把当时的 `master` 推送到 `https://github.com/Makise0721/DataIncidentGym.git`，等待 Ubuntu CI 后再继续后续阶段。

### 理由与边界

- 用第二平台验证固定基线，而不是在未验证时宣称 M1 完成。
- 不强推，不扩大代码范围，CI 成功前不开始 M2。
- 当时的关键阶段提交包括 `6327a03`、`4fc7029b`、`47d582c` 和 `b69c79b`。

## 2026-08-29：M6 Diagnostic Kernel v1 收口

### 决策

- 保留 `schema_rename_payment_amount`，新增 `schema_type_change_payment_amount`，M6 只完成第一个 P1 故障类型纵切。
- Diagnostic Kernel 显式维护候选假设、EvidenceGap、hypothesis assessment、claim-evidence binding 和 8/8 预算；模型负责提出结论，Kernel 负责验证、拒绝和投影。
- Agent 固定暴露四个只读证据工具，不增加自由 SQL、自由路径、写工具或自动修复。
- 默认模型使用 OpenAI-compatible `mimo-v2.5`；诊断和模型探针是唯一获批的外部网络例外。
- evaluator 独立读取 Ground Truth；Kernel、Agent、工具包装器、trace 与报告生成都不能读取 Ground Truth。
- artifact 合同固定为 `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md` 六个文件。

### 验收事实

- 最终确定性验证：`441 passed, 4 skipped, 2 deselected`，Ruff、CLI help、doctor help 与 diff check 通过。
- 固定真实模型窗口共六个样本；两个案例分别 `3/3 PASSED`，没有补样本、替换样本或额外 diagnosis probe。
- M6 功能 HEAD `3907edf33452efbfec2c21582b66c85cef84f7e0` 的 Ubuntu CI run `33255524612` 成功，unit、integration 和普通 e2e 均通过。
- 历史 schema-rename Ground Truth blob、语义 digest 与 Jaffle Shop submodule 指针保持不变。

### 报告边界

最终模板不再渲染 evaluator 的 `expected`/`actual` 字段。固定六样本是在修复前的 `a54e4a54aa5d117400f62e2272a564f483e19e83` 生成；为保持观测不可伪造且不突破授权窗口，旧 artifact 未重跑或覆盖，其 `code_revision` 继续如实指向该提交。

## 2026-08-29：项目文档收敛

### 决策

- `docs/` 只保留 `requirements.md`，已完成阶段的实施计划不继续作为长期项目文档维护。
- 根目录决策记录由 `mistake.md` 更名为 `DECISIONS.md`。
- README 只描述当前能力、运行入口和安全边界；需求合同放在 `docs/requirements.md`，重要取舍与验收事实放在本文件。

### 理由

实施计划用于阶段执行，阶段完成后继续保留会造成入口分散和过时信息。需求、决策和使用说明分开维护，能让项目状态更清楚，也避免把历史执行步骤误当成当前合同。
