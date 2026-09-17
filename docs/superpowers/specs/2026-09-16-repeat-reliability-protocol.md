# 重复可靠性协议 `p1.reliability.v1`

- 日期：2026-09-16。范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T08。
- 状态：离线实现与确定性样本验收完成；**真实测量需另行冻结 manifest 并经批准**。本协议不
  修改已冻结 manifest 的固定排程，也不使任何历史结果作废。
- 实现：`src/data_incident_gym/reliability.py`；报告接入 `benchmark_report.py`
  （`summary.json` 的 `strategies[*].reliability`）。

## 1. 比较单位与分组身份

比较单位是一个**组（group）**：单一场景 × 单一策略，落在一个冻结的实验身份内。身份由
`manifest_id` + `manifest_sha256` + `implementation_revision` + `evaluator` 身份 + 本协议版本
共同确定，随报告一起写出。

- trial 永不跨组移动：不从其它场景、其它策略或其它版本借 n。
- 同一组的全部 trial 必须共享场景 ID、策略与执行环境（重置、注入、构建、诊断、恢复、评测），
  每次 trial 独立 reset/inject/recover，无跨 trial 会话或记忆复用；`RUN_ID` 由
  (manifest, sequence, 场景, 策略, repeat_index) 派生，组内唯一。
- 排程侧的 `repeat_index` 必须为 1..n 连续、组内观测下标必须唯一且都在排程内；排程非法、观测重复或
  计划外下标即视为报告输入无效，拒绝出报告。**观测缺口不同**：它是合法的运行结果，按 §5 标记为不完整
  组并附完整子集覆盖量，不拒绝出报告。

## 2. 预定重复与执行顺序

- 预定重复次数 n **来自冻结排程本身**（manifest cells 的 `repeat_index`），不由观测数据决定。
  当前 P1 排程：`STATIC_SKILL` / `DIAGNOSTIC_KERNEL` 每场景 3 次；`NO_TOOL`、`FIXED_RULE`、
  `KERNEL_NO_LINEAGE`、`KERNEL_NO_SCHEMA` 每场景 1 次（消融/基线，不参与主比较）。
- 执行顺序即冻结的 `sequence` 升序：三次重复按轮转顺序分散（repeat 1/2/3 使用错开的场景顺序），
  同一组的重复不连续执行，降低时间漂移与缓存的相关性。
- 每次 trial 预算固定为 8 model 请求 / 8 工具调用 / 2 重试 / 300 秒（manifest `budget`）。
- 报告不得缩小 n、补跑、换格或排除失败 trial 来得到更好结果；n=3 是最小能力演示，不作为强
  统计结论的充分样本量；需要更稳估计时另立 n 更大的预定设计并冻结新身份。

## 3. 样本有效性与失败归类

| 观测 | 归类 | 计入 |
| --- | --- | --- |
| 评测器 `PASSED` **且**无失败的适用 controller（kernel）门 | 成功 trial | s |
| 评测器 `FAILED`（含判错、缺引用、弃答但期望确认等） | 失败 trial | n |
| `MODEL_ERROR`（`MODEL_TIMEOUT`、请求/工具预算耗尽、协议或运行时错误） | 失败 trial | n |
| 适用**环境门**失败（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`） | **无效样本** | 仅计入无效计数 |

- `EvaluationResult.status` 只由证据检查推导，**不含** `controller_checks`：kernel 门（`KERNEL_STATE_VALID`、
  `KERNEL_HYPOTHESIS_GATE`、`KERNEL_EVIDENCE_GAP_GATE`）失败必须单独判定为失败 trial，否则违反 kernel 契约的
  run 会被算成成功（报告 `_evaluation_passed` 即为此实现，`benchmark partial` 同样口径）。
- 正常策略超时按失败处理；provider/环境故障若没有独立证据（环境门），不被单独豁免，按
  失败 trial 计入——归类只看预定规则，不看输出质量。
- **代理侧违规不算无效样本**：伪造引用（`EVIDENCE_IDS_EXIST`）、越权工具（`TOOL_ALLOWLIST_EXACT`）、
  写尝试（`TRACE_READ_ONLY_SAFE`）与 kernel 状态门都计为失败 trial 并留在分母；否则模型可以用违规
  把最严重的失败洗成"环境问题"。环境门限定为场景未验证、证据越出本 run、环境未能恢复三类。
- 环境门与失败同时出现在同一 trial 时**以环境门为准**：样本不可解释，不产数字，组标记不完整。
- 无效样本既不是成功也不是失败：它使所在组**不完整**，绝不进入 s 或 n 的分子分母。

## 4. `pass^k` 定义

对同一组 n 次预定重复中的 s 次成功：

```
pass^k = C(s, k) / C(n, k)，  n >= k
s < k  → 0
n < k  → null
```

含义是"k 次全部成功"的无偏估计，**不是**"尝试 k 次至少一次成功"的 `pass@k`。例：n=3、s=2 时
`pass^1 = 2/3`、`pass^2 = 1/3`、`pass^3 = 0`。报告主比较输出 k=1/2/3（受 n 限制）。

## 5. 缺失与无效样本处理

- 组内出现缺失（预定 `repeat_index` 无有效产物）或无效样本时，**主组标记为不完整**：主组
  `pass^k` 全部为 null 并列出缺失/无效的 `repeat_index`，不缩小 n 计算。
- 完整子集分析单列：在剩余有效 trial 上计算 `pass^k`，并强制携带覆盖量
  （有效 trial 数 / 预定 n）与被排除的下标；它只能作为附注，不能替代主组数字。
- 缺失与无效的原因随组输出（缺产物 / 安全硬门失败代码），便于定位，不做静默补齐。

### 5.1 部分运行入口

正式报告（`BenchmarkReporter.write`）要求 ledger 每格两条、六产物齐全；协议的不完整组规则由只读入口
`analyze_partial_suite`（CLI：`benchmark partial`）提供：缺失格与无效样本按本文档标记，主组不产数字、
完整子集分析附带覆盖量，缺失格绝不进入分母、也绝不补齐。该入口只读归档状态（不重算 evaluator 规则），
每个被计分的 bundle 必须同时绑定到**目标试次**与传入 manifest：ledger 条目、`metadata`、`evaluation`、`diagnosis`
的 run_id / 场景 / 策略 / 序号必须与目标格一致，`metadata.benchmark_manifest_sha256` 必须等于 manifest 摘要；
身份不符（含换成其它场景或其它 run 的产物）记为 `IDENTITY_MISMATCH`、跨版本记为 `MANIFEST_IDENTITY_MISMATCH`、
合法 JSON 但形状非法记为 `ARTIFACTS_INVALID`，三者都只让该格退出计分，不中断其余格的分析。

## 6. 跨场景汇总

- 组是场景内相关的重复集合，**不得**把不同场景的 trial 合并成一个 `pass^k`；任何汇总只做
  **组级宏平均**：只纳入"完整组"，并同时写出纳入组数与 trial 数。
- 没有完整组时宏平均为 null 并附原因，不写 0 或 100%。
- 置信区间/重采样（当前实现不输出跨场景区间）必须先按场景聚类（如场景级 bootstrap），不得把
  重复 trial 当作独立场景直接算二项区间；`pass^1` 的组内 Wilson 区间沿用报告既有口径。

## 7. 边界

- 历史单次（n=1）运行只输出 `pass^1`；`pass^2`/`pass^3` 因 n < k 为 null，不得用跨场景或跨版本
  的 trial 拼出更大的 n。
- 本协议不修改诊断平面、工具权限、预算、evaluator 判定规则、scenario 定义与既有冻结 manifest。
- 无模型预算时本任务只达到"实现验证完成"；在另行冻结新 manifest 并完成真实测量之前，不得
  表述为"真实可靠性已验证"。

## 8. 版本与升级

更大 n、外部策略（T09/T11）、新场景或新任务结构都使用新的协议版本与新 manifest 身份；旧身份
与其历史结果保持不变。代码强制的是 manifest/排程/场景结构（cells 必须等于冻结排程、`repeat_index`
上限、组身份校验）；"换协议版本"本身是约定：需要人工升级 `RELIABILITY_PROTOCOL_VERSION` 并冻结新
manifest，报告不会自动阻止沿用旧版本号。
