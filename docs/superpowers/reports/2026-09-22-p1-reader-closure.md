# P-1 收口报告:读取端三分类复核器 + 构造端缺陷修复(2026-09-22)

- 交接基线:`docs/superpowers/reports/2026-09-21-step3-handoff.md` §5「尚未完成」清单。
- 本轮交付:读取端(离线复核器)、验收三分类、五类回归、历史兼容;过程中发现并修复
  构造端(`3109db7` 引入)两处缺陷。全程 TDD(每处先写失败测试再实现)。
- 纪律遵守:未 push、未改写历史归档、未重跑失败格、无数据库/模型/网络操作。

## 1. 交付一:构造端缺陷修复(`diagnostic_agent._refusal_audit`)

对 `3109db7` 的实现做复核时发现两处真实缺陷,均已先写失败测试(3 例 RED)再修复:

### 1.1 截断窗口错位 → 计数恒等式破坏

原实现先在全部 citations 上过滤 registered、再对 resolved 截断:

```python
resolved   = [c for c in citations if c in registered]
unregistered = len(citations) - len(resolved)      # 全量上的计数
truncated  = max(0, len(citations) - 32)
evidence_ids = resolved[:32]                        # cap 作用在 resolved 上
```

当 `len(citations) > 32` 且存在未登记引用时,被 cap 丢弃的未登记引用同时计入
`unregistered` 与 `truncated`,自述恒等式
`total = len(evidence_ids) + unregistered + truncated` 不成立
(反例:40 条引用、20 登记 20 未登记 → 20+20+8=48≠40)。
该恒等式正是「不能把缺失读为零」机制的核心,破坏它会让严格读取端把不自洽数据
误当代数成立。修复:cap 窗口移到 citations 本身,窗口内再分 resolved/unregistered,
恒等式对任意组合恒成立。回归测试
`test_audit_counts_survive_cap_with_unregistered_refs` 钉住 20/12/8 的精确分布。

### 1.2 死条件与 HEALTH_STATE 误标可复核

原判定 `if "UNREGISTERED" in refusal_reason and unregistered > 0` 中,
`refusal_reason` 是门的**固定通用文案**(不含 "UNREGISTERED"),该分支为死代码:
未登记引用的 claim 落入 `RECOMPUTABLE_PROJECTED_CLAIM`;且 HEALTH_STATE claim
(支撑规则需要 `history_name`/`bucket`/`current_value`,投影一律不保留)也被标记
为可复核——存储的参考值系统性失真。

修复:可复核性判定收拢为**单一事实函数** `diagnosis.derive_claim_recomputability`
(新增,纯函数、无场景知识,不触 P1 隔离约束),构造端填值、读取端再推导都经过它,
存储对与推导对由构造漂移为不可能。原因码集合从四种变五种:
`RECOMPUTABLE_UNREGISTERED_REF` / `RECOMPUTABLE_PROJECTED_CLAIM` /
`NOT_RECOMPUTABLE_TRUNCATED_REFS` / `NOT_RECOMPUTABLE_REDACTED_CLAIM_VALUE` /
**`NOT_RECOMPUTABLE_HEALTH_CLAIM_FIELDS`(新增)**。
`refusal_reason` 参数随之删除(两处调用点同步)。
另修正 `RejectedClaimSummary` 的误导 docstring(它描述了只属于
`AuditClaimSummary` 的计数字段);已验证该 docstring 不在任何冻结 schema 面
(`Diagnosis.model_json_schema()` 不含 trace 事件子树)。

## 2. 交付二:读取端 `refusal_review.py`(三分类复核器)

公共 API 一个:`review_refusal_events(run: DiagnosisRunResult, scenario: ScenarioSpec)
-> tuple[RefusalReviewVerdict, ...]`。输入即归档 bundle 的两个字段
(`.diagnosis_run` 与 `.scenario`,后者来自 `.dig/scoring-inputs/<run_id>/`,
digest 自校验),逐事件复核每个 `accepted=False` 的 `EVIDENCE_GATE` 事件,
产出 `p1.refusal_review.v1` verdict(status + 固定码 basis)。

**当时态重建**:证据集合与工具轨迹都取拒绝事件**之前**的 trace 前缀
(TOOL_CALL 的 `evidence_ids` 并集 ∩ 归档 `evidence_records`),
拒绝后补采的证据不可能进入判定;kernel 与 static 两路径的归档都保存全量
接收记录(`_result`:3017/3027),前缀语义对两路径一致。

**不信任存储布尔**:每条 claim 经 `derive_claim_recomputability` 再推导,
与存储对不一致的记入 `recomputability_disagreements`(claim 序号),
verdict 一律跟随再推导值;复核前先验证计数恒等式,不自洽 → 不可判定。

**三分类**(收口标准:分类准确;不可判定绝不计为正确):

| verdict | 判据(全部由读取端重算) |
| --- | --- |
| `CORRECT` | 重算确立违规:I1 存在 applicable claim 带未登记引用(计数即足,不重建 claim),或已登记引用重算 `claim_supported_by_records` 不支撑(ROOT_CAUSE 用 `known_value` 重建 claim;AFFECTED_ASSET 同理);I2 存在命中合同 triple 的声明 gap 且前缀无 `refusal_witnessed` |
| `FALSE_REFUSAL` | 无违规且无阻断:I1 所有 applicable claim 合规(或场景无 applicable kinds 而 I1 却拒绝了——`I1_NOT_APPLICABLE`);I2 所有命中合同的 gap 都有恰好一条 witness |
| `INDETERMINABLE` | 固定码:AUDIT_ABSENT(v1 历史)、KERNEL_CONTRACT_PATHWAY(带 `rejected_decision` 的 kernel 合同拒绝,判据属另一通路)、GATE_INTERNAL_ERROR_NOT_REVIEWABLE、COUNT_IDENTITY_VIOLATED、CLAIMS_TRUNCATED / UNRESOLVED_TRUNCATED、CLAIM_NOT_RECOMPUTABLE:*(截断/脱敏/HEALTH 字段缺失)、AUDIT_EVIDENCE_OUTSIDE_PREFIX、SUBJECT_UNAVAILABLE、REASON_CODE_MISMATCH、APPLICABLE_KINDS_MISMATCH(audit 记录与 scenario 重算不一致,阻断)、UNSUPPORTED_REASON_CODE |

复核与门(`submission_policy`)判据逐字对应:applicable 先于未登记检查
(非 applicable claim 的未登记引用不构成违规);I2 的合同 triple 查找与
`tool_name is None` 跳过行为与门实现一致。

**设计边界(如实声明)**:audit 的 claims/unresolved 投影被信任为被拒提交自身的
记录(除 cap 丢弃外)——被拒提交原文按所有者裁定不入档(方案 b 已否决);
kernel 合同层拒绝(带 `rejected_decision`)不在本复核器范围,如报不可判定。

## 3. 验收对照

| 交接要求 | 落点 |
| --- | --- |
| 读取端对 `recomputable` 再验证,不信任布尔 | `_claim_disagreements` + verdict 跟随再推导;`test_reader_records_recomputability_disagreement_without_trusting_it` |
| 历史记录缺字段 → 不可判定 | `AUDIT_ABSENT`;`test_reader_indeterminable_when_audit_absent`(v1 JSON 无键加载 OK) |
| 缺字段不得默认成零 | `AuditClaimSummary` 三计数必填无默认;`test_audit_claim_missing_count_fails_strict_load`(缺键严格拒载) |
| 序列化重载后正确复核 | `test_reader_verdict_survives_serialization_round_trip`(audit JSON round-trip,verdict 全等) |
| 五类回归:未登记 / 已登记不支撑 / 混合 / 合法 / 序列化重载 | `test_reader_correct_for_unregistered_ref` / `..._registered_but_unsupported_claim` / `..._mixed_citations`(双 basis 并存)/ `..._false_refusal_for_compliant_submission`(I1)+ I2 witnessed / round-trip |
| 三分类且禁止把不可判定算作正确 | 12 个 INDETERMINABLE 用例逐一断言 status;CORRECT 仅在违规重算确立时给出 |
| 双路径完整留痕 | 构造端 kernel(`diagnostic_agent.py:2855` 区域)与 static(`:2948` 区域)共用同一 `_refusal_audit`;e2e 矩阵 38 例覆盖 kernel 门拒绝真实通路 |
| 拒绝发生时的证据集合与轨迹前缀 | 前缀重建(§2);`test_reader_indeterminable_when_audit_evidence_outside_prefix` 钉住「引用不在前缀 → 不可判定」 |
| ScenarioSpec 取自归档 inputs,非 trace | API 收 `ScenarioSpec`(调用方从 bundle 取);复核器不从 trace 推断场景 |

新增测试 24 例(构造端 3 + 读取端 21),全部通过。

## 4. 验证

```powershell
uv run pytest tests/unit -q                        # 1114 passed, 5 skipped(基线 1090 + 24)
uv run ruff check .                                # All checks passed!
uv run pytest tests/e2e/test_p1_policy_matrix.py -q  # 38 passed in 0:38:35
uv lock --check                                    # 通过
git diff --check                                   # 通过
```

e2e 矩阵 38 passed 与交接基线一致:本轮改动(构造端计数修复 + 共用推导函数)
不影响门通路行为,kernel 门拒绝真实通路保持修复后状态。

## 5. 效力边界与登记待办(不外推)

1. 构造端修复改变 audit 内容 → **必须纳入最终冻结**(与门面修复同一批重绑,
   顺序按交接 §7,不做额外动作)。
2. `refusal_review` 是库层 API,本轮未加 CLI 入口;v29/v30 历史归档的缺陷暴露面
   离线检查仍是**登记待办**(预计多数 I1 事件因 `AUDIT_ABSENT` 不可判定,
   run 4 的 2 次 kernel 合同拒绝带 `rejected_decision`,属另一通路,同样待办)。
3. kernel 合同层拒绝的离线重算(判据属 kernel 合同,非本复核器)未实现,
   报告如实记为能力边界,不是「零误拒」的证据。
4. HEALTH_STATE claim 的支撑成因在归档侧结构性不可复核(投影不含所需字段);
   如未来需要,须所有者裁定扩大投影面(当前裁定:不扩)。

## 6. 改动清单

| 文件 | 改动 |
| --- | --- |
| `src/data_incident_gym/diagnosis.py` | 新增 `derive_claim_recomputability`(双端共用纯函数);修正 `RejectedClaimSummary` docstring |
| `src/data_incident_gym/diagnostic_agent.py` | `_refusal_audit`:cap 窗口移至 citations、改用共用推导函数、删 `refusal_reason` 死参数(两处调用点同步) |
| `src/data_incident_gym/refusal_review.py` | 新增:三分类离线复核器(`p1.refusal_review.v1`) |
| `tests/unit/test_refusal_review.py` | 新增 24 例(构造端 3 + 读取端 21) |
