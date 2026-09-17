# 项目状态汇总：kernel 诊断与修复周期收口

日期：2026-09-12。本文件收口本轮 kernel 诊断与修复周期，记录各工作流的最终状态、证据边界与批次记录纪律。**周期到此结束**：不再围绕已测格子连续追测；更广的能力测量应由新的覆盖目标与预算另行决定。工作树停在 `23c29ad`，无未提交改动，无待办。

## 1. 已收口的修复：规范资产标识符合同

**状态：已实施、离线恢复验证通过、一次真实直接成功。**

- 实施：`6501130 feat: require canonical asset node identifiers in confirmed claims`。`AFFECTED_ASSET.value` 仅接受所引证据中的完整 `node_id`；短名以新码 `ASSET_CLAIM_NAME_NOT_IDENTIFIER` 拒绝，反馈仅在引用证据能唯一合法映射时给出替代值；不静默转换提交值。身份随之升为 `p1.kernel.v18` / `p1.controller.v19`。
- 离线验证（`tests/unit/test_asset_identifier_contract.py`，7 项）：完整 ID 接受、短名拒绝（唯一映射档反馈含值、歧义档不含）、不受支持的同名节点不给建议（反例）、**同一 kernel 仅替换标识符即恢复**，且恢复后实际 outcome 通过 evaluator 资产检查。全量 unit 579 passed、wire 7 passed。
- 真实验证（p1-formal-v21，协议运行前冻结）：seq14 首次确认提交即使用完整 ID，**零拒绝、evaluator 全部检查通过**（判据归类 #1）。
- 证据边界：真实模型收到新拒绝反馈后的恢复行为**未验证**（该格无拒绝发生）；本轮不证明成功率或提示效果提升。

## 2. 已知限制（三项，分别保留）

1. **过度弃答（seq2，模型选择问题）**：离线重放证明当轮证据足以构造合格确认——kernel 接受、evaluator 13 项全过；真实运行中模型采齐证据后直接弃答。同一格在 v13 旧契约下曾确认通过，确认行为跨身份不稳定。单格样本，不外推；本轮不追加提示。
2. **弃答规划（收据规划能力限制）**：收据候选投影已实施并离线验证（`43ce507`），v18 测量未观察到目标收据动作变化，接口实验已按 `2026-09-12-receipt-planning-experiment-closure.md` 收口；后续不追加提示补丁。v20 seq7 出现首次真实探针且声明正确绑定收据，但缺口矩阵仍不完整——归入本限制，不重新展开。
3. **schema 内容校验边界（接口边界）**：kernel 仅绑定"声明关系↔引用的 schema 事实"，不校验 schema 内容与错误/变更的对应；evaluator 要求引用实际变更关系的 schema。两者之间的差异已被最小重放钉死，且**本次未找到公开依据充分、不会误拒正确引用的更严规则**（真实数据中的列改名与 CTE 别名使文本锚定规则误拒正确引用）。保留为已知限制，不新增门禁。

三项互不捆绑；各自的详细证据见对应报告（§5）。

## 3. 批次与证据记录纪律

- 历史定向子集（v9–v12 八格、v13/v14 四格、v15–v18 三格、v20 四格、v21 单格）**逐批分开记录，不合并成总体通过率**；子集运行留下 `subset.json`、不出正式报告；失败不补跑。
- **环境无效批次单独标注**：p1-formal-v19（四格全部 `MODEL_ERROR`，provider 配额耗尽）作废，不作为模型行为证据。
- 提交链（本周期）：`7057ccd`（OPEN 派生修复）→ `4fd5029`/`8985ba8`（三输出工具）→ `881ca34`/`6c12e1e`（v17 冻结）→ `43ce507`（收据候选投影）→ `6e8c51e`/`c72f47e`（v18 冻结）→ `b242504`/`a8b2585`（v19，作废）→ `6556809`/`c4318c5`（v20 冻结）→ `6501130`（规范标识符合同）→ `af90f3b`/`23c29ad`（v21 冻结）。
- 当前身份：kernel `p1.kernel.v18` / controller `p1.controller.v19`，由 p1-formal-v21 清单记录；无其他待决身份。

## 4. 周期结束声明

本轮诊断与修复到此结束。已达成：三输出工具接口迁移及其真实运行检验、OPEN 派生修复、收据候选投影（含实验收口）、规范资产标识符合同（含一次真实直接成功）；已归档：两项模型能力限制与一项接口边界。**是否开展更广的能力测量，由新的覆盖目标与预算决定**——例如覆盖从未测量的场景与决策负担组合、或为反馈恢复路径设计针对性检验——不再围绕本周期已测格子连续追测，也不以零散小批重跑替代立项决策。

## 5. 报告索引

- 修复线：`2026-09-11-kernel-rejection-feedback-validation.md`、`2026-09-12-three-output-tools-design.md`/`-implementation.md`、`2026-09-12-seq14-canonical-asset-identifier-contract-design.md`/`-implementation.md`
- 测量线：v15–v21 各批协议与结果报告（含 `2026-09-12-p1-formal-v19-four-cell-diagnostic-protocol.md`、`2026-09-12-p1-formal-v19-v20-four-cell-diagnostic-review.md`、`2026-09-12-p1-formal-v21-single-cell-protocol.md`/`-result.md`）
- 限制线：`2026-09-12-receipt-planning-assessment.md`、`2026-09-12-receipt-planning-experiment-closure.md`、`2026-09-12-seq2-confirmation-path-replay-verification.md`、`2026-09-12-schema-citation-boundary-verification.md`
- 运行证据：`artifacts/benchmarks/p1-formal-v15` 至 `p1-formal-v21/` 及各 run bundle（v19 为作废批次）
