# Kernel v18 / v19 小规模配对测量方案

日期：2026-09-27。状态：待批准；本轮授权仅为设计，不包含身份登记、冻结、数据库操作或真实请求。

策略候选完成点：`aac6873`。对照实现基线：`cb0437b`。设计依据：[schema 核对设计](../specs/2026-09-27-kernel-profile-schema-check-design.md)；离线交付见[实施报告](../reports/2026-09-27-kernel-profile-schema-check-implementation.md)。

## 1. 要回答的问题

在相同模型、场景、工具权限、预算、评分与控制器下，新增 schema 核对提示是否改善目标场景的**整格通过**，同时不造成可见的预算、弃答或健康判断退化？

这是一次 dev 场景上的小规模筛选，不是完整 benchmark、holdout 泛化评估或概率改善证明。五个历史失败仅用于选定研究对象，不把 v32 的旧结果充作本轮旧策略对照，不重写任何旧归档。

## 2. 两个新身份、36 个新样本

- A：`DIAGNOSTIC_KERNEL`，`p1.kernel.v18`，新采样对照。
- B：同一策略枚举，`p1.kernel.v19`，schema 核对候选。
- 每臂 6 场景 × 3 次 = 18 格；总计 36 格。按 `(case_id, repeat_index)` 对齐为 18 对。重复序号只是对齐标签，**不代表共享模型随机 seed**。
- 仅执行主 Kernel，不加入 Static 或消融；本轮不能形成跨策略结论。

| 分组 | 场景 | 每臂次数 | 选择理由 |
| --- | --- | --- | --- |
| 目标 | duplicate_payment_coupon_a | 3 | v32 两格 profile 已采但 schema 缺失 |
| 目标 | orphan_payment_coupon_a | 3 | v32 三格相同缺采形态 |
| 不足对照 | duplicate_payment_coupon_b | 3 | 检查额外核对是否诱导错误确认或多余探针 |
| 不足对照 | orphan_payment_coupon_b | 3 | 检查缺口与权限边界的副作用 |
| 预算压力对照 | silent_payment_drop_partition_a | 3 | 需要多种决定性证据，关注补采挤掉 history/lineage |
| 健康对照 | order_volume_within_sla | 3 | 检查无故弃答、误报和多余查询 |

目标组每臂分母 6；对照组每臂分母 12。所有场景已用于开发，明确标为 dev。不得看过结果再换场景、增加重复、改变分组或补跑失败格。

## 3. 使用现有 selector，不修改排程合同

现有 manifest 固定包含 106 cells；`benchmark preflight/run --only-sequence` 支持显式子集，两步 selector 必须完全一致。本计划沿用该能力，**不把 manifest 改成 18 格**，不放宽验证器，也不增加 scheduler。

从当前赛程读取的选择为：

```text
10,11,14,15,18,23,25,28,29,32,33,40,50,55,66,67,70,71
```

| repeat | case → sequence |
| --- | --- |
| 1 | duplicate_a→10；duplicate_b→11；orphan_a→14；orphan_b→15；silent_a→18；within_sla→23 |
| 2 | duplicate_a→25；duplicate_b→28；orphan_a→29；orphan_b→32；silent_a→33；within_sla→40 |
| 3 | duplicate_a→66；duplicate_b→67；orphan_a→70；orphan_b→71；silent_a→50；within_sla→55 |

冻结验收再次从每份清单程序化核对以上 case、strategy、repeat 和 sequence，任何差异先停，不按位置猜测。每臂按 manifest 顺序执行；完整 selector 固定，不能分批改变 selector 来绕过回执或暂停状态。

执行结果会写 `subset.json`，属于 **development smoke subset**。只运行 `benchmark partial` 和只读配对分析，不能调用 `benchmark report` 宣称正式完整报告。清单中未选择的 88 格是本计划未安排，不与所选 18 格中的执行缺失混计。

## 4. 身份构造与冻结验收

建议申请两个尚未登记的新 ID：A=`p1-formal-v33`，B=`p1-formal-v34`。这是预留建议，当前不登记；批准时复核未占用。两臂都要新身份，因为 v32 已有 ledger/subset 范围不能改写或扩展为本实验。

1. A 从 `cb0437b` 构建，B 从 `aac6873` 构建；各自只增加同样的 ID 登记与必要身份测试/授权记录。A 保留原始 v18 文本，不能把 v19 改个版本标签冒充 v18。
2. 对两臂运行代码逐文件比较：除 `diagnostic_kernel.md` 的新增段和 `KERNEL_PROMPT_VERSION` 外，产品源码必须相同；ID 白名单也必须相同。测试/文档不同逐项列出，不作为运行行为相同的替代证据。
3. 清单模型配置必须逐字段相同：`openai-compatible`、`deepseek/deepseek-v4.1-flash`、`https://api.commandcode.ai/provider/v1`，包括 model settings。
4. 两臂 evaluator v5、controller v22、result_inputs 五项摘要、catalog、场景摘要、工具/输出 schema、六策略排程与 8/8/2/300 预算全部一致。政策差异仅允许 Kernel 家族 prompt 版本/摘要；虽有三种 Kernel 身份变化，本轮实际只排主 Kernel。
5. manifest 差异白名单：manifest_id、implementation_revision、派生 run_id，以及上述 Kernel prompt 身份字段。其余任何差异都阻断冻结收口。
6. 每臂先得到包含最终代码与授权的绑定提交 X，再用官方 `benchmark freeze` 生成清单、仅提交该清单得到 Y；在 Y 的独立干净 checkout 上运行真实 `_verify_checkout` 并核对 `diff X..Y` 恰为清单路径。
7. 记录两臂 X/Y、完整 manifest sha256、selector JSON 与摘要后，才申请真实执行放行。历史 v32 文件与 74 格归档保持原样。

本阶段不能仅凭 `benchmark verify` 替代检出门验收。A 的测试按其旧策略版本执行，不把 B 专用的 v19 断言套在 A 上；另外运行跨臂身份差异检查。

## 5. 执行顺序与环境

固定执行顺序 **B 整臂 → A 整臂**，先观察新提示的运行风险；不是依据结果选顺序。两臂串行，共享数据库，禁止并发 dbt/lab 操作。

这不是随机化交叉实验：既有 runner 的子集/回执约束不支持任意逐格切换。本方案接受分块顺序的时间混杂；即使观察到差值，也只能报告本次配对样本差异，不能排除服务端随时间变化。真正交叉验证留给筛选通过后的独立方案，不为本轮修改执行框架。

每臂执行前：

- 固定到自己的 Y，检出干净，子模块与冻结依赖齐备；核对 `uv 0.11.24`，worktree venv 的 Scripts 在 PATH 中。
- 诊断数据库六键与 User 作用域模型 key 经进程注入，不落盘、不回显；模型/端点取 manifest，不被 dotenv 覆盖。
- PostgreSQL 健康，`pipeline build` 恢复后核对 F0。F0 取已归档健康基线并逐字比较，不因本轮运行重新定义。
- `benchmark verify`；然后同 selector 的 preflight 一次。通过后同 selector 的 run 一次。两臂之间再次核对环境与 F0。

命令模板（执行时替换为验收后的完整值）：

```powershell
$selectorArgs = @()
foreach ($seq in @(10,11,14,15,18,23,25,28,29,32,33,40,50,55,66,67,70,71)) {
    $selectorArgs += @('--only-sequence', [string]$seq)
}
uv run data-incident-gym benchmark preflight --manifest $manifestPath --confirm-sha256 $manifestSha @selectorArgs
# 只有回执 PASSED 才执行下一条；不能仅凭 shell 继续运行。
uv run data-incident-gym benchmark run --manifest $manifestPath --confirm-sha256 $manifestSha @selectorArgs
```

launcher 记录开始/结束/退出码、HEAD、清单/回执摘要与 selector；不能依据 CLI 退出码单独判断是普通质量失败还是必须停止，须读取 stop_reason 与逐格终态。

## 6. 停止与用量边界

**保留现有自动机制：每臂最近 12 个 model-backed 终态中 ≥10 未通过即暂停。** 两臂各自独立窗口，不把不同 manifest 的 ledger 混在一起。尚不足 12 格时该窗口不触发，不能声称它从第一格起就能防大面积失败。

- 任一臂 preflight 失败、RUN_SETUP_ERROR、恢复失败、滚动暂停、身份/归档异常，整个实验停止，不启动另一臂、不自动续跑、不补样本；保留现场请所有者裁定。
- 任一 `GATE_INTERNAL_ERROR` 或发现隐私/权限越界，人工停止后续执行；这不是新加的自动门。若中断发生在一格中途，如实记录中断，不将其伪装成普通模型失败。
- 单个 provider 错误与普通诊断失败按原合同计入结果，不自动替换；原实现的传输重试保持不变，不新增手工重试、preflight 重试或第二次 run。
- B 完整结束且没有上述停止事件，才可启动 A。普通未通过但未达到停止条件不按结果择机中断。

样本诊断请求合同上界为 36×8=288；两次结构化 preflight 各最多 2 次，合计 **292 个逻辑请求额度**。目录 GET 单列；底层重试/失败尝试未必等于 RunUsage 完成计数，因此 292 **不是实际 HTTP 尝试的硬上界**。记录 SDK 配置、transport 事件和可得的实际请求计数，不以 token 费率虚构 Goat 套餐账单。

支付边界：现有 Goat 套餐内、额外付费 0 USD；额度不可用或出现额外付费要求即停，不充值、不切模型或端点。此项需随执行授权确认。

时间计划：v32 74 格墙钟约 131 分钟，按吞吐粗估 36 格约 64 分钟；加两次预检、环境准备、恢复和核验，建议预留 **2–3 小时**。这只是排班估计，单格诊断 300 秒不约束全部 dbt/lab 耗时，不能作为总时长保证。

## 7. 分析口径与预先固定的决策规则

先严格核对 manifest、ledger、六文件、scoring-inputs、模型身份、恢复与 evaluator 重算一致性，再计算结果。任何损坏停止效果判读，不用宽松读取填充分母。

主要指标：每格证据检查 PASSED 且适用 controller 门无失败，才算成功。MODEL_ERROR/传输错误保留在端到端结果中；环境失败单列，不能归因于策略质量。未执行记缺失，绝不按失败或成功补齐。

分开呈现目标组（6 对）和对照组（12 对）：A/B 各自成功数/分母；双方成功、仅 A 成功、仅 B 成功、双方失败四格表。总 18 对仅作补充，不用总体平均掩盖目标或健康退化。不输出显著性、泛化或“可靠性已经提升”的结论。

次指标：目标关系 schema 是否接受、原完整性失败码是否消失、决定性 history/lineage 是否缺失、错误弃答/误确认、工具与请求耗尽、传输错误、token/时长、P-1 各层拒绝及复核。额外 schema 存在但整格仍败必须单列；工具不调用也可能是合规预算取舍，按公开前缀复核，不能自动判成策略未执行。

预先约定的筛选条件（不是统计显著性标准）：

1. 36 格全部终态、18 对完整，且环境、身份、严格归档验证通过，才评判候选是否晋级。否则结论为“不完整/不可判”，仅报已观察事实。
2. 目标组 B 比 A **净增至少 2 个成功格（分母各 6）**，且两个目标场景分别不下降。
3. 对照组 B 成功数不低于 A；B 的预算耗尽及错误确认数均不高于 A。若任一对照场景下降，即使总数抵消，也标记为需解释的退化，不自动晋级。
4. 满足以上条件，只建议进入更广的确认性测量；不直接将 v19 宣称为更优生产策略。不满足则保留负面/混合结果，不追加样本直到通过。

历史 v32 只列背景，不混入任何 A/B 分母。停止造成的非对称配对单列，不能只挑完整配对计算后隐去未完成样本。

## 8. 待批准的具体范围与交付

建议分两道放行：

1. **离线身份登记与冻结**：批准上述两臂、六场景×三重复和暂定 v33/v34；执行者完成两个冻结、跨臂差异与检出门验收，报告完整摘要。此授权不包含真实请求。
2. **真实执行**：在第一步证据齐备后，批准上述 B→A 顺序、各一次 preflight/run、36 格上限、套餐与停止边界。未批准不发目录探针或 completion。

最终交付一份结果报告，含两臂身份证据、18 对逐格表、目标/对照分组、失败与预算分解、P-1 复核、停止与缺失说明、成本计数及上述筛选规则逐项判定。不改评分、不改旧产物、不 push。
