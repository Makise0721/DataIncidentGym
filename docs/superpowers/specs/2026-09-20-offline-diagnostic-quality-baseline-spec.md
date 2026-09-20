# 离线诊断质量基线 spec（v1，待所有者审核）

- 日期：2026-09-20。状态：**草案，待审核；审核通过前不实施。**
- 依据：所有者对 v29/v30 部分结果的裁定——证据与缺口处理缺陷重复出现，优先离线策略回归；
  本轮**不改策略、提示、评分或历史产物**。
- 定位：这是"离线诊断质量分析基线"（对既有归档的质量轴度量），**不是**策略回归本身——
  重算已有答案定位缺陷，不证明新策略会产生更好的答案。策略改进的验证仍需后续独立授权的测量。

## 1. 输入与前置（先验证，后分析）

1. 输入仅限归档：`artifacts/benchmarks/<id>/ledger.jsonl` + 各格六文件 + 场景/验证附件，经
   `load_evaluation_input_bundle` 严格加载。私有场景合同（required_evidence_types、
   unresolved_gaps、ground truth）只在分析器进程内使用，**绝不进入任何策略上下文或提示**。
2. **评分身份前置（不满足即整体报错，不产出基线）**：
   - 当前 `EVALUATOR_VERSION` 与 `sha256(evaluation.py)` 逐字等于该批 manifest `result_inputs`
     的 `evaluator_version` / `evaluator_sha256`；
   - 逐格以冻结 evaluator 纯内存重算 `EvaluationResult`，与归档 `evaluation.json` **完全相等**
     （模型级相等）。任一格不一致：报错列出格 ID 与差异，该批不进入基线。
3. 数据损坏（文件缺失、JSON/schema 解析失败、ledger 与归档不一致）：该格标 `CORRUPT` 剔除并
   显式列出；**绝不静默跳过**。`CORRUPT` 格不进任何分母。

## 2. 分母与单元格分类（先于三轴）

每个终态格先分类，互斥：

| 类别 | 判定 | 进入三轴？ |
| --- | --- | --- |
| `MODEL_ERROR` | `diagnosis.status == MODEL_ERROR` | 否，单列计数（其衍生检查失败——如空引用导致的 REQUIRED_EVIDENCE_TYPES_PRESENT——**不计为质量缺陷**） |
| `CORRUPT` | 见 §1.3 | 否，显式列出 |
| `PASSED` | 重算评测 PASSED | 计入分母基线（作为对照） |
| `QUALITY_FAILED` | 重算评测 FAILED 且非 MODEL_ERROR | 是 |

某轴分母 = 该轴适用的 `QUALITY_FAILED` + `PASSED` 格数；**分母为零时该轴输出 `n/a`，不输出 0%**；
不适用（如轴 3 对 CONFIRMED 场景格）按 evaluator 既有 applicability 排除，不计入分母。

## 3. 三轴定义（全部复用 evaluator 规则，不定义第二套）

三轴**允许重叠**：同一格可同时被多轴标记；报告中给出重叠矩阵。

### 轴 1 采集完整性

- 判定素材：`scenario.required_evidence_types`（私有，分析侧）、归档已采集证据类型集合
  （`evidence_records`）、最终被引用证据类型集合（`cited_types`，evaluator 同源）。
- 每个失败格的每个必需类型分为：`未采集`（不在已采集集合）或
  `已采但未进入引用集合`（在已采集集合、不在 cited_types）。
- 该格若 REQUIRED_EVIDENCE_TYPES_PRESENT 通过，则该轴记 0 缺陷（可通过格不产生轴缺陷）。

### 轴 2 逐 claim 引用绑定

- **复用 evaluator 规则**：使用 `evaluation.py` 既有的逐 claim 支撑判定
  （`ClaimSupportVerdict` / `_claim_evidence_compatible` 同源逻辑），不另行定义支撑规则。
- 对失败格的每条 claim 输出：`支撑成立` / `当前引用不足`（全部已采证据按 evaluator 规则
  足以支撑、但 claim 的 evidence_ids 未绑定）/ `已采证据也不足以支撑`。
- 适用范围：evaluator 既有 applicability（CONFIRMED / NO_INCIDENT 的 claim 类别）。

### 轴 3 弃答缺口声明（仅 INSUFFICIENT_EVIDENCE 终态且该检查 applicable 的格）

- 期望集合 = 场景合同 `unresolved_gaps` 的 `(evidence_kind, subject, reason_code)` 三元组
  （私有，分析侧）；实际集合 = `diagnosis.unresolved_evidence` 同三元组。
- **缺失集合与多余集合分别记录，允许同时存在**：`missing = expected − actual`、
  `extra = actual − expected`（与 evaluator 的精确相等判定一致，但不止于布尔）。
- 带工具的缺口核对**真实拒绝收据**：按 evaluator 的 `refusal_witnessed` 逻辑，对每个带
  `tool_name` 的期望缺口检查 trace 中是否有对应拒绝；记录"已见证 / 未见证"。

## 4. 报告形态

- **两批分别报告**（v29 13 格、v30 18 格），保留批次、场景、策略与重复关系（两批赛程前缀
  重叠、非独立样本）；合计仅作描述性并列表，不作统计推断。
- 每格一行：批次、seq、场景、策略、终态类别、轴 1 缺陷（按类型细分）、轴 2 缺陷、轴 3
  缺失/多余集合与收据见证。聚合为直方图与重叠矩阵。
- 预期计数（验收参照，**仅来自 v29、仅作参照**）：分析应复现所有者人工分析的 v29 形态
  （如第 3/6/11 格未采集、13 格采而未引、8/9/12/13 反事实可修复形态）；**v30 结果独立计算，
  不预填、不以 v29 形态为验收答案**。反事实结果单列，绝不替换原始通过率。

## 5. 实现与测试边界

- 交付：只读分析器（脚本或 CLI 子命令）+ 单元测试 + 两批报告。无评分/策略/提示/归档变更，
  无模型调用，无数据库（附件与归档均为本地文件）。
- **测试不依赖本机忽略目录**：机制测试用可入库的脱敏最小合成夹具（构造
  `DiagnosisRunResult`/`EvaluationResult`/场景合同的内存或 repo 内 fixture，覆盖分母、零分母、
  不适用、损坏、多缺陷重叠五类规则）；v29/v30 真实归档仅作本地验收运行，不入库。
- 不一致与损坏的报错路径本身有测试钉住。

## 6. 明确不做

不改策略/提示/评分/历史产物；不重跑；不把私有合同灌进模型上下文；不把 31 格当独立样本；
不产出"新策略更优"的结论（那是后续获准测量的任务）。
