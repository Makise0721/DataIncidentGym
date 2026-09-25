# v31 Kernel 合同拒绝只读可行性切片

日期：2026-09-25
范围：只读复核 v31 冻结前缀中 7 个带 rejected_decision 的 Kernel 拒绝；不改源代码、不运行模型、数据库或基准任务。

## 结论

按“完整重算第一条错误”的标准，判定为 **0 CORRECT / 0 FALSE_REFUSAL / 7 INDETERMINABLE**。七条投影都缺少被拒绝 decision 的 run_id，因此不能独立排除排在所有业务校验前的 DECISION_SCOPE_MISMATCH。seq36 还有第二个独立阻断：两条水位声明的原始 subject 被脱敏，无法重算更早的去重检查。

若下一步实现三态 review 分支并保留 INDETERMINABLE，现有合同足够，无需变更。若要从归档证明这些事件为 CORRECT/FALSE_REFUSAL，则需要增加隐私安全的审计投影：记录 scope 匹配结果；seq36 还需记录重复等价关系，而不是原始 subject。按现有 v31 归档，可条件性复算 scope 之后的规则：其余 6 条的已报拒绝码都与相应校验路径一致；seq36 的后续首错仍不可判。

## 身份与复核方法

- 冻结 manifest：p1-formal-v31，SHA-256 f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a；manifest 声明 106 个 cells。
- 按归档 loader 严格读取 manifest、doctor receipt 和 ledger：得到 79 个连续终态 ledger 项，最后为 seq79 FAILED。这是暂停前缀，不是完整 106-cell benchmark。
- 对 79 个终态项计算归档源聚合：SHA-256 41cd9261567dfdb7582f701ea45dca4e6fb459146fd7c6539889161391b867a，共 476 个文件。7 个拒绝事件所在 cell 均通过六件归档文件结构、身份、trace 连续性和 digest 校验。
- 对 6 个相关 run 使用 v31 的 load_evaluation_input_bundle 严格加载评分输入；每个输入索引与 bundle digest 校验通过。artifact metadata 的 code_revision 均为 91582e588e90156236040b87c18d0ecc2dccfbd2，与 doctor receipt 一致。Kernel、validator、拒绝投影、诊断 schema 和 evaluation-input loader 文件与 manifest 的 implementation_revision e5d81d99abbf6803cb3a4351b2f6471ebac57e77 比较无差异。
- 每个拒绝之前都没有已接受的 EVIDENCE_GATE；seq55 的第二个拒绝之前也只有一次失败的 finalize。因此 KERNEL_FINALIZED 可由 trace 前缀排除。每个事件拒绝之后均无 TOOL_CALL；拒绝时快照候选中的 evidence_inventory 与前缀成功收据的有序列表完全相同。terminal state 仅用于 hypotheses、gaps 和 evidence_inventory；后续成功 finalize 不修改这些字段。
- 全部 rejected_decision 投影未截断，保留项与 total 计数一致。但它没有 run_id 字段。KernelDecision 接收 submission.run_id，finalize 在任何状态/证据校验前检查 DECISION_SCOPE_MISMATCH；trace 的最终诊断 run_id 只能绑定归档 run，不能证明被拒绝 decision 的 run_id 相同。以下分支复算因此都以“scope 已匹配”为条件，不能升级为最终 CORRECT。

## 逐事件首错复算

A/C/U 分别为投影中的 assessments / claims / unresolved_evidence 数量。“状态”列的收据数仅计拒绝前成功工具调用。所有事件的最早不可排除项均为 decision scope；表中“条件性复算”仅描述 scope 匹配时后续规则的结果。

| cell / 拒绝 trace 序号 | run_id | 输入投影 | 拒绝前状态 | scope 之后的条件性复算 | 判定 |
|---|---|---|---|---|---|
| seq3 / 7 | ae41a73051ac749405a50c3b57f948dd | INSUFFICIENT_EVIDENCE；A/C/U=3/0/1；1 个 subject 被脱敏；未截断 | 3 个 hypotheses，1 个 BLOCKED gap，5 个成功收据 | 替代假设数满足；单条 unresolved 使 gap-required 条件满足，单条不可能造成重复。投影 builder 只会在 subject 不属于公开 node/relation 集时脱敏，而 validator 的 known_subjects 是该 node 集的子集；故 TRANSFORMATION_DEFINITION 不可能绑定，后续首错为 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 未归档 |
| seq36 / 8 | 4d7647e10b5534022379439a53ccf1cf | INSUFFICIENT_EVIDENCE；A/C/U=0/0/2；2 个 subject 被脱敏；未截断 | 2 个 hypotheses，2 个 BLOCKED gaps，5 个成功收据 | scope 匹配时，替代假设和 gap 条件通过。validator 先按原始 (kind, subject, reason) 去重，再逐条检查 watermark subject。两个 subject 都不可见，不能知道原始 key 是否相等：若相等，去重先触发；若不同，两条声明都不属于 incident_subjects，之后才会报 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 与重复关系均不可复算 |
| seq48 / 6 | f8e3262a42691cdd798bce286b0a9132 | INSUFFICIENT_EVIDENCE；A/C/U=2/0/1；1 个 subject 被脱敏；未截断 | 2 个 hypotheses，1 个 BLOCKED gap，4 个成功收据 | scope 匹配时，替代假设通过；单条声明令 gap-required 条件通过且去重不会失败。脱敏证明 subject 不属于公开 node/relation 集，因此不可能属于 validator 的 known_subjects；后续首错为 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 未归档 |
| seq51 / 7 | 67a04ca3c8f0c8ae82822696cd9f41e7 | INSUFFICIENT_EVIDENCE；A/C/U=0/0/1；1 个 subject 被脱敏；未截断 | 2 个 hypotheses，2 个 BLOCKED gaps，4 个成功收据 | scope 匹配时，替代假设通过；单条声明令 gap-required 条件通过且去重不会失败。incident_subjects 是字符串集合；脱敏表明该 subject 不在公开 subject 集中，因此不可能属于 incident_subjects；后续首错为 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 未归档 |
| seq55 / 6 | 1506494114980221915bacd89295d009 | NO_INCIDENT；A/C/U=3/1/0；未截断；health claim 引用 3 个前缀收据 | 3 个 hypotheses，raw_orders 的 DISCRIMINATE_SCHEMA gap 为 BLOCKED，3 个成功收据 | scope 匹配时，NO_INCIDENT 进入 health 路径后，最先检查是否存在非 CLOSED gap。BLOCKED gap 可证，故后续首错为 EVIDENCE_GAP_OPEN，在 claim 形状和 evidence 检查之前。 | INDETERMINABLE：decision.run_id 未归档 |
| seq55 / 7 | 1506494114980221915bacd89295d009 | INSUFFICIENT_EVIDENCE；A/C/U=3/0/1；subject=raw_orders；未截断 | 3 个 hypotheses，同一 BLOCKED gap，3 个成功收据 | scope 匹配时，替代假设和 gap 条件通过；单条声明不会触发重复检查。前缀 evidence 中 run-results 的 failed/skipped nodes 为空，profile/history 记录提供 relation_name 而非 node_id；按 validator 的 known_subjects 构造，raw_orders 不在集合中，后续首错为 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 未归档 |
| seq63 / 5 | 36efeb83d3ad1660cf0ceb471e910690 | INSUFFICIENT_EVIDENCE；A/C/U=2/0/1；1 个 subject 被脱敏；未截断 | 2 个 hypotheses，1 个 BLOCKED gap，3 个成功收据 | scope 匹配时，替代假设通过；单条声明令 gap-required 条件通过且去重不会失败。脱敏 subject 不可能属于 validator 的 known_subjects；后续首错为 UNRESOLVED_EVIDENCE_UNBOUND。 | INDETERMINABLE：decision.run_id 未归档 |

## 证据路径与边界

- cell 映射和冻结身份：v31 artifacts/benchmarks/p1-formal-v31/ledger.jsonl、config/benchmark/p1-formal-v31.json、doctor.json。
- 每个 run 的拒绝输入：.dig/scoring-inputs/<run_id>/evaluation_inputs.json 及其 index；拒绝事件位于 artifacts/<run_id>/trace.jsonl 的上述序号；拒绝前 receipts 位于相应 artifacts/<run_id>/evidence.json。
- scope 与第一错误顺序：冻结代码 src/data_incident_gym/diagnostic_contracts.py:325,358-406、src/data_incident_gym/diagnostic_kernel.py:690-705。未解决声明顺序见 src/data_incident_gym/diagnostic_validation.py:377-421；subject 投影规则见 src/data_incident_gym/diagnostic_agent.py:1489,1560-1565；RejectedDecisionSummary 字段见 src/data_incident_gym/diagnosis.py:581-616。
- 本切片结论只适用于 v31 这 7 个拒绝事件；不外推为其他错误码或整个 106-cell suite 的覆盖结论。
