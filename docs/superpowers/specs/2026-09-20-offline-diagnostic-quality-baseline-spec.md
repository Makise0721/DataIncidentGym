# 离线诊断质量基线 spec（v2，含可执行定义；已经所有者补齐审核）

- 日期：2026-09-20。状态：**v2 定义补齐版；进入实施（分析器、合成测试、真实归档验收）。**
- 依据：所有者对 v29/v30 部分结果的裁定——证据与缺口处理缺陷重复出现，优先离线策略回归；
  本轮**不改策略、提示、评分或历史产物**。
- 定位：这是"离线诊断质量分析基线"（对既有归档的质量轴度量），**不是**策略回归本身——
  重算已有答案定位缺陷，不证明新策略会产生更好的答案。策略改进的验证仍需后续独立授权的测量。
- v2 变更：按所有者审核意见补齐四项可执行定义——轴 1/2 判定算法、轴 3 适用范围、分母与
  重叠单位、身份完整性 expansions 与反事实边界。

## 1. 输入与前置（先验证，后分析）

1. 输入仅限归档：`artifacts/benchmarks/<id>/ledger.jsonl` + 各格六文件 + 场景/验证附件，经
   `load_evaluation_input_bundle` 严格加载。私有场景合同（required_evidence_types、
   unresolved_gaps、ground truth）只在分析器进程内使用，**绝不进入任何策略上下文或提示**。
2. **评分身份前置（不满足即整体报错，不产出基线）**：
   - 当前 `EVALUATOR_VERSION` 与 `sha256(evaluation.py)` 逐字等于该批 manifest `result_inputs`
     的 `evaluator_version` / `evaluator_sha256`；
   - `result_inputs` 记录的**其余评分依赖摘要**同样逐字核对：`profile_spec_sha256`、
     `scenario_spec_schema_sha256`、`diagnosis_schema_sha256`；
   - **批次—manifest—run 身份绑定**：批次名 ↔ manifest_id ↔ manifest 文件 sha256（与冻结值
     比对）；每格 ledger 条目的 manifest_id/sequence/run_id/case/strategy 与 manifest cells
     一致；归档 `metadata.json` 的 `benchmark_manifest_sha256` == manifest digest、`run_id` 一致；
   - 逐格以冻结 evaluator 纯内存重算 `EvaluationResult`，与归档 `evaluation.json` **完全相等**
     （模型级相等）。任一格不一致：报错列出格 ID 与差异，该批不进入基线。
3. **选取范围**：两批的**全部终态格**（v29 13、v30 18）。正常提前停止留下的未执行格
   （106 − 终态数）标记 `NOT_EXECUTED` 剔除——这是设计内边界，**不算数据损坏**，不进任何分母。
4. 数据损坏（文件缺失、JSON/schema 解析失败、ledger 与归档不一致）：该格标 `CORRUPT` 剔除并
   显式列出；**绝不静默跳过**。`CORRUPT` 格不进任何分母。

## 2. 分母与单元格分类（先于三轴）

每个终态格先分类（互斥）：

| 类别 | 判定 | 进入三轴？ |
| --- | --- | --- |
| `RUN_ERROR`（MODEL_ERROR） | `diagnosis.status == MODEL_ERROR` | 否，单列计数（其衍生检查失败——如空引用导致的 REQUIRED_EVIDENCE_TYPES_PRESENT——**不计为质量缺陷**） |
| `CORRUPT` | 见 §1.4 | 否，显式列出 |
| `NOT_EXECUTED` | 终态集之外的格 | 否，仅计数（§1.3） |
| `PASSED` | 重算评测 PASSED | 计入分母基线（作为对照） |
| `STATUS_ERROR` | 状态维度错误（见下） | 不进三轴，但**必须出现在总体结果中**，按方向细分：`错误弃答`（期望 CONFIRMED/NO_INCIDENT，实际 INSUFFICIENT_EVIDENCE）、`应弃答却确认`（期望 INSUFFICIENT_EVIDENCE，实际 CONFIRMED）、`其他状态错误`（其余组合） |
| `QUALITY_FAILED` | 重算评测 FAILED、状态正确（含弃答正确但证据/缺口/claim 检查失败）、非 MODEL_ERROR | 是 |

**轴 3 正式缺口比较仅对"合同期望不足、实际也弃答"的格**（期望 = 实际 =
INSUFFICIENT_EVIDENCE）。其余状态错误与运行错误不因三轴不适用而消失——它们在总体结果中
按 `STATUS_ERROR`（含方向）与 `RUN_ERROR` 单独列示。

## 3. 三轴定义（全部复用 evaluator 规则，不定义第二套）

三轴**允许重叠**；**重叠矩阵统一按 run（格）为单位**——一格在任一轴有缺陷即在该轴计 1，
绝不与类型级/claim 级/缺口级单位混用。

### 轴 1 采集完整性 —— 按证据类型计

- 固定算法（集合运算，类型 = evidence type）：
  - `未采集 = required − collected`；
  - `采而未引 = (required ∩ collected) − evaluator认可的引用类型`，其中引用类型集合与
    evaluator 的 `cited_types` 同源计算。
- 单位：**(格, 证据类型) 对**。numerator = 该格两类缺陷的类型数；denominator =
  `|required|`（该格必需类型数）。
- **零分母**：场景 `required_evidence_types` 为空（或该检查不适用）→ 该格轴 1 记 `n/a`，
  原因记 `no_required_types`，不产出 0%。
- 格级 rollup：两类任一非空即该格轴 1 有缺陷；报告中同时给出类型级明细。

### 轴 2 逐 claim 引用绑定 —— 按 claim 计

- **复用 evaluator 规则**：适用性沿用 evaluator（CONFIRMED / NO_INCIDENT 的 claim 类别与
  `APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS`）；支撑判定使用 `evaluation.py` 既有机制
  （`ClaimSupportVerdict` / `_claim_evidence_compatible` 同源），不定义第二套支撑规则。
- **仅对"适用且当前不被支撑"的 claim** 做"全部已采证据足够性"检查：以全部已采证据
  （而非仅当前引用）按同一 evaluator 规则重判支撑，输出
  `当前引用不足（已采证据足以支撑）` 或 `已采证据也不足以支撑`。
- 该结果是**事后诊断**：不自动修改引用、不改写归档、不宣称答案已修复。
- 单位：**claim**。numerator = 当前不被支撑的适用 claim 数（按上述二分细分）；
  denominator = 适用 claim 总数。
- **零分母**：范围内无适用 claim → `n/a`，原因记 `no_applicable_claims`。

### 轴 3 弃答缺口声明 —— 按缺口计，格级 rollup

- **仅对轴 3 适用格**（期望 = 实际 = INSUFFICIENT_EVIDENCE，见 §2）做正式缺口比较：
  - 期望集合 = 场景合同 `unresolved_gaps` 的 `(evidence_kind, subject, reason_code)` 三元组
    （私有，分析侧）；实际集合 = `diagnosis.unresolved_evidence` 同三元组。
  - **缺失集合与多余集合分别记录，允许同时存在**：`missing = expected − actual`、
    `extra = actual − expected`。
  - 带工具的期望缺口核对**真实拒绝收据**：按 evaluator 的 `refusal_witnessed` 对 trace
    校验，逐缺口记录"已见证 / 未见证"。
  - **缺口正确 = 集合相等 且 全部必需拒绝收据成立**（与 `_insufficiency_matches` 同判）。
- 单位：**缺口**。numerator = `|missing| + |extra|`（按缺口列出）+ 未见证收据数；
  denominator = `|expected| + |extra|`。
- **零分母**：期望与实际皆空 → 该格轴 3 记 `n/a`，原因记 `no_gaps_declared_or_required`。
- 格级 rollup：轴 3 有缺陷 = 集合不等或任一必需收据未见证。

## 4. 报告形态

- **两批分别报告**（v29 13 格、v30 18 格），保留批次、场景、策略与重复关系（两批赛程前缀
  重叠、非独立样本）；合计仅作描述性并列表，不作统计推断。
- 每格一行：批次、seq、场景、策略、终态类别（含 STATUS_ERROR 方向 / RUN_ERROR）、
  轴 1 缺陷（按类型细分）、轴 2 缺陷（按 claim 细分）、轴 3 缺失/多余集合与收据见证。
  聚合为直方图与**按 run 的重叠矩阵**。
- 预期计数（验收参照，**仅来自 v29、仅作参照**）：分析应复现所有者人工分析的 v29 形态
  （如第 3/6/11 格未采集、13 格采而未引、8/9/12/13 反事实可修复形态）；**v30 结果独立计算，
  不预填、不以 v29 形态为验收答案**。反事实结果单列，绝不替换原始通过率。

## 5. 反事实边界

- **本轮不实施反事实执行**。分析器只输出"可修复形态"描述（轴 2 的"当前引用不足"、
  轴 3 的缺失/多余集合等），**不得写"修复后通过"**。
- 若未来需要反事实执行，须另行 spec 明确：允许修改的字段白名单、重算验证方式、私有合同
  参与程度，以及结果与原始通过率的隔离。

## 6. 实现与测试边界

- 交付：只读分析器（模块 + 直接运行入口）+ 单元测试 + 两批报告。无评分/策略/提示/归档变更，
  无模型调用，无数据库（附件与归档均为本地文件）。
- **测试不依赖本机忽略目录**：机制测试用可入库的脱敏最小合成夹具（构造
  `DiagnosisRunResult`/`EvaluationResult`/场景合同的内存或 repo 内 fixture，覆盖分母、零分母、
  不适用、损坏、多缺陷重叠五类规则，以及身份/摘要不一致的报错路径）；v29/v30 真实归档仅作
  本地验收运行，不入库。

## 7. 明确不做

不改策略/提示/评分/历史产物；不重跑；不把私有合同灌进模型上下文；不把 31 格当独立样本；
不产出"新策略更优"的结论（那是后续获准测量的任务）。
