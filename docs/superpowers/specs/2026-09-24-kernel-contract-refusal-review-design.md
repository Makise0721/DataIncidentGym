# Kernel 合同拒绝的离线复核：可判定性先行

状态：审计侧设计；不改变当前评分或运行合同。依据 v31 暂停前缀的 7 次 Kernel 合同拒绝，以及 `refusal_review` 目前对 `rejected_decision` 一律返回 `INDETERMINABLE/KERNEL_CONTRACT_PATHWAY` 的实现。

2026-09-25 可行性结论：见 [只读报告](../reports/2026-09-25-v31-kernel-refusal-readonly-feasibility.md)。v31 七次拒绝全部缺少被拒 decision 的 `run_id`，无法独立排除排在首位的 `DECISION_SCOPE_MISMATCH`；seq36 另缺两条脱敏 subject 的重复等价关系。按本文的完整首错标准，七次均为 `INDETERMINABLE`。在决定是否扩展**未来**归档的安全投影前，不把条件性复算写成七次已确认正确，也不以修改旧归档来补证。

## 目标与范围

逐次判断 Kernel `finalize` 拒绝是否符合**当时版本**的第一条适用判据，输出 `CORRECT`、`FALSE_REFUSAL` 或 `INDETERMINABLE` 与固定原因码。只审带 `rejected_decision` 的 `EVIDENCE_GATE(accepted=false)`；I1/I2 已有的 `refusal_audit` 复核路径保持原样。最终诊断是否通过、同格稍后是否被接受，都不能替代对这一次拒绝的判断。

第一轮只读验收以 v31 的 7 次样本为对象：`UNRESOLVED_EVIDENCE_UNBOUND` 六次（seq3/36/48/51/55/63），`EVIDENCE_GAP_OPEN` 一次（seq55）。这不是保证七次都可判定的预设结果。后续其他错误码按相同边界逐个扩展；没有实现判据的码返回 `INDETERMINABLE/UNSUPPORTED_KERNEL_REASON`，不得推断为正确。

## 身份与拒绝时状态

1. 严格加载运行归档、清单、评分输入和 trace，核对 run ID、case、策略、manifest 摘要与代码修订。复核所用 Kernel/validator 实现必须与清单绑定修订的相应源码一致；不一致时停止该次复核，不能用当前 `main` 的代码静默重释旧拒绝。任何输入缺失、摘要不符或版本交叉均记固定的不可判原因。
2. 取拒绝事件**之前**的工具轨迹和已登记证据。终态 `KERNEL_STATE` 仅在证明所需状态未于拒绝之后改变时可作代理：从拒绝至快照没有新的工具调用、快照 `evidence_inventory` 与前缀成功收据的证据 ID 按序完全相等，并逐字段核对本次判据依赖的 hypotheses/gaps。证明不了拒绝时状态，就返回 `INDETERMINABLE/STATE_AT_REFUSAL_UNAVAILABLE`；不得用事后补采证据倒推。
3. v31 七次样本的只读预查发现：每次拒绝之后工具调用数均为 0，终态证据清单与拒绝前成功收据的 ID 按序相等。此事实只为该批的可判定性检查提供前提，不自动证明任一拒绝正确。

## 判据与有损投影

- `EVIDENCE_GAP_OPEN`：按 Kernel `finalize` 的原始顺序先核对终态路径、run 作用域等前置条件，再看拒绝时是否有非 `CLOSED` gap。可证存在则 `CORRECT`；可证不存在且更早的规则均通过才可能为 `FALSE_REFUSAL`。任一必要状态不完整则不可判。
- `UNRESOLVED_EVIDENCE_UNBOUND`：保持原校验顺序（含替代假设、gap 必需条件、未解决声明去重），再用 `diagnostic_validation.validate_unresolved_declarations` 的同版本规则核对每条声明。已保留的 subject 可直接重算；投影为 `subject=null` 时，只有在可从投影构造规则与已核对的上下文严格证明该 subject **不可能**满足相应判据，且不会隐藏更早的拒绝原因时，才可判 `CORRECT`。
- `RejectedDecisionSummary` 是有损归档。截断、脱敏或重复关系使“第一条错误码”无法确定时，返回 `INDETERMINABLE` 并指出具体阻断项。尤其 v31 seq36 的两条 `INGESTION_WATERMARK` 声明都脱敏为 `subject=null`：不能把两个 null 当成原值相同，也不能仅凭事件自报的错误码排除更早的去重失败。不得为了得到零不可判而保存模型原始值或自由文本。
- 只有完整重算得到与归档相同的首个 Kernel 错误码时才记 `CORRECT`；完整重算证明该码不应拒绝时才记 `FALSE_REFUSAL`。仅证明“某个条件可能有问题”不足以判定本次拒绝正确。输出同时保留拒绝事件 trace 序号、代码身份、所用前缀边界和阻断原因。

## 实施与验收

先做**只读可行性切片**：对 v31 七次样本生成逐事件输入清单和可判性表，给每条列出状态来源、投影完整度、首错码能否复算及证据路径；如发现现有归档不足，报告不可判，不改写历史。确认复算路径后，才在 `refusal_review` 增加与 I1/I2 并列的 Kernel 分支，复用原 validator 或同版本冻结实现，不复制一套可漂移的规则。

合成回归至少覆盖：真实违例、误拒、拒绝后继续取证、缺失/冲突的快照、被截断声明、一个与两个脱敏 subject、错误码顺序、序列化重载，以及 I1/I2 路径不变。真实 v31 七次只作本地只读验收，不能以此声称所有 Kernel 码都可复核。报告分别列出 `CORRECT` / `FALSE_REFUSAL` / `INDETERMINABLE` 的分母和原因；不可判不得计入正确。归档、evaluator、历史评分、正式 suite、真实模型调用和 manifest 冻结均不在此设计的实施范围。
