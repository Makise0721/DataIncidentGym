# 金丝雀格复测结果报告（2026-09-21）

- 依据：[实施计划](../plans/2026-09-21-canary-retest-plan.md)（3 格原位复测 run 7/8/9）。
- 执行范围：3 次 `eval run`，串行执行完毕，无重跑、无替换失败格、无更换模型或样本。
- **前置 0.1 判定**：配额口径**无法确认**（本环境外部抓取被禁，全部文档主机解析到非公网地址，
  [CommandCode 限流文档](https://commandcode.ai/docs/troubleshooting/errors/rate_limit)与
  [配额说明](https://commandcode.ai/docs/resources/pricing-limits)均不可读）。按计划硬性规定，
  **退到降级方案**：格间间隔 ≥ 5 分钟串行执行并记录实际间隔。
- 定位：验收阶梯第 2 步的补充观察，不是第 3 步；归档仍为**实验性观察产物**，不进评估集合。
- 所有者放行：本批经所有者书面确认放行（按计划执行 3 格 + 出报告 + docs 提交）。

## 1. 前置条件实测（计划 §0）

| 项 | 要求 | 实测 |
| --- | --- | --- |
| 0.1 配额缓解确认 | 先与 CommandCode 确认 | ❌ **无法确认** → 已启用降级方案（≥5 min 间隔） |
| 0.2 HEAD | `d5b30b1` 或直系后代 | `d5b30b1add3cd8245d45b8cbc594dc02bd3dae7e`（逐字相等） |
| 0.2 工作树 | 已跟踪文件干净 | 干净（执行前后均干净） |
| 0.3 指纹 | 逐字 == F0 | `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18` ✅ |
| 0.4 密钥 | 进程无则按 User 作用域回退并记录 | 进程内**仍无** `COMMANDCODE_API_KEY`（未继承）；已按上批已验证的 User 作用域回退执行（长度 93，值未输出） |
| 0.5 编码 | 首条命令即带 `PYTHONIOENCODING=utf-8` | ✅ 3 条命令**全部**前置该变量；3 个 stdout 首行均正确解出（上批 run 1/2 的塌陷未复现） |

**建议仍未落实**：计划 §0.4 建议重启 DSH 服务使进程继承 `COMMANDCODE_API_KEY`——本批进程仍无该变量，
故仍依赖 User 作用域回退。此建议**待执行**。

## 2. 实际间隔（降级方案要求的记录）

| 边界 | 起点（UTC） | 终点（UTC） | 实际间隔 |
| --- | --- | --- | --- |
| T1 → T2 | 07:58:08（run 1 启动） | 08:03:08（run 2 启动） | **300.4 s** |
| T2 → T3 | 08:03:08（run 2 启动） | 08:08:08（run 3 启动） | **301.0 s** |

两处间隔均 ≥ 5 分钟（300 s），由命令内前置 `Start-Sleep` 强制并在归档中回显校验（内含
`interval < 300s` 即 `exit 9` 的自检，未触发）。单格执行时长 86.6–111.8 s。

## 3. 逐条命令与逐字 stdout 归档

| # | 命令 | stdout 归档 | 起止（UTC） | exit |
| --- | --- | --- | --- | --- |
| 1 | `uv run data-incident-gym eval run required_null_order_customer_a --strategy static-skill` | `.dig/canary-retest-required_null_order_customer_a-static-skill.txt` | 07:58:08 → 07:59:41 | 0 |
| 2 | `uv run data-incident-gym eval run required_null_order_customer_a --strategy diagnostic-kernel` | `.dig/canary-retest-required_null_order_customer_a-diagnostic-kernel.txt` | 08:03:08 → 08:04:40 | 1 |
| 3 | `uv run data-incident-gym eval run silent_payment_drop_partition_a --strategy static-skill` | `.dig/canary-retest-silent_payment_drop_partition_a-static-skill.txt` | 08:08:08 → 08:10:06 | 0 |

逐字 stdout（三份格式一致，首行编码正确）：

```
评测通过。                                  # run 2 为「评测未通过。」
status: PASSED                              # run 2 为 FAILED
run_id: <run_id>
artifacts: artifacts/<run_id>
scoring_inputs: C:\Users\29913\codex_space\DataIncidentGym\.dig\scoring-inputs\<run_id>
```

环境映射同上一批（`DIG_DIAGNOSTIC_MODEL_BASE_URL` / `_NAME` / `_API_KEY`），进程级、不落盘、
执行后恢复（见 §6/C5）。

## 4. 3 个 run_id 与结果

| # | case_id | strategy | run_id | eval | recovery |
| --- | --- | --- | --- | --- | --- |
| 1 | required_null_order_customer_a | static-skill | `970042d0e44540028de567c544e0f7af` | **PASSED** | HEALTHY |
| 2 | required_null_order_customer_a | diagnostic-kernel | `9bbafbcf0f434aff90aba9a590b6cfdb` | FAILED（**429**） | HEALTHY |
| 3 | silent_payment_drop_partition_a | static-skill | `8a69125a84104f8d8adad1d58d982e17` | **PASSED** | HEALTHY |

**2/3 PASSED，1/3 因 provider `HTTP_429` 作废。**

## 5. C1–C5 逐项实测

| 编号 | 观察项 | 实测 | 判定 |
| --- | --- | --- | --- |
| C1 | 3 格 evaluator 结果 | run 1 PASSED、run 2 FAILED（429）、run 3 PASSED | **部分 PASSED**（2/3） |
| C2 | 每格 `EVIDENCE_GATE` 事件；PASSED 格出现拒绝事件即停 | **两个 PASSED 格均零拒绝事件**，各仅 1 个 `accepted=true` 事件（`reason_code=CONFIRMED`） | ✅ **无违例** |
| C3 | 429/协议异常 | **run 2 命中 429**（`model_request_index=4`，`transport=HTTP_429`）；run 1/3 无协议异常 | ⚠️ 触发，该格证据作废，未自行重试 |
| C4 | `GATE_INTERNAL_ERROR` | **0 次** | ✅ 通过 |
| C5 | 环境恢复 | `recovery_status` 3/3 HEALTHY；执行后三个 `DIG_DIAGNOSTIC_*` 均未设置；指纹逐字 == F0；HEAD 未变、已跟踪树干净 | ✅ 通过 |

### 逐格门事件明细（C2/C4 证据）

| run | `EVIDENCE_GATE` 事件 | 拒绝事件 |
| --- | --- | --- |
| 1 | seq9 `accepted=true` `CONFIRMED` | **无** |
| 2 | seq6 `accepted=true` `MODEL_PROTOCOL_ERROR`（429 终止登记，非提交判定） | **无** |
| 3 | seq8 `accepted=true` `CONFIRMED` | **无** |

即本批 3 格的 `accepted=false` 事件总数为 **0**，`GATE_INTERNAL_ERROR` 为 **0**。

### 预算与用量

| 格 | 模型请求 | 工具调用尝试 | 诊断耗时 | 总耗时 |
| --- | --- | --- | --- | --- |
| run 1 | 4 / 8 | **8 / 8（达上界）** | 21.7 s | 86.6 s |
| run 2 | 3 / 8 | 4 / 8 | 19.6 s | 85.4 s |
| run 3 | 4 / 8 | 7 / 8 | 48.1 s | 111.8 s |
| **合计** | **11 / 24** | **19 / 24** | 89.4 s | 283.9 s |

token 实测：**85,499 输入 / 16,690 输出**。按上批采用的第三方量级单价（0.10 USD/M 输入、
0.30 USD/M 输出）估算 **约 0.0136 USD**，与计划「约 0.01–0.02 USD」一致。同上一批，**精确账单须以
provider 账目为准**（本环境无法读取计价端点）。

## 6. 对照 §3 解释矩阵（预先约定）

| 矩阵结局 | 本批是否命中 | 说明 |
| --- | --- | --- |
| 3/3 PASSED 且无 429 | ❌ 否 | 仅 2/3 PASSED |
| 有 PASSED 有 FAILED，**无 429** | ❌ 否（形态相似但成因不同） | 本批的 FAILED **是** 429 造成，不是质量失败 |
| **仍有 429** | ✅ **是（主判定）** | run 2 于 `model_request_index=4` 再命中 429，**降级方案（≥5 min 间隔）未能缓解** |
| 任何 C2/C4 违例 | ❌ 否 | 零拒绝、零 `GATE_INTERNAL_ERROR` |

**按矩阵取「仍有 429」分支 → 限流未解决 → 先解决配额，第 3 步冻结等待。** 本批为该分支提供了
新的、更强的证据：**间隔 300 s 的错峰不足以规避**，且 429 仍精确落在 `model_request_index=4`
（与上批 run 3/5/6 同一位置），指向**单次 run 内的请求深度/配额**而非请求速率。

**须同时记录的正面观察（不改变上述分支结论）**：在本批**唯一有效的 2 个格上**
（run 1、run 3，均为 static 路径），结论为 **2/2 PASSED 且门零拒绝**。即：一旦不被 429 截断，
PASSED 金丝雀格可以复现，且未出现误拒。这补齐了上批 O4 只拿到「无违例弱证据」的缺口——
现在有了 **2 个真实 PASSED 格 + 零拒绝事件** 的直接证据（上批一个 PASSED 格都没有）。

## 7. 结论

1. **门逻辑再次通过，且这次拿到强证据。** 两个 PASSED 格（run 1/3）门各只发 1 个 `accepted=true`
   事件、**零拒绝**，`GATE_INTERNAL_ERROR` 为 0。O4 的「PASSED 格零拒绝事件」在**真实 PASSED 格上
   被验证 2 次**，不再只是「无违例」的弱证据。
2. **降级方案无效，限流仍未解决。** ≥5 分钟格间间隔下 run 2 仍在 `model_request_index=4` 命中 429。
   结论：**第 3 步按矩阵冻结等待配额解决**；错峰方案可判定为**不足以规避**。
3. **3 格中 2 格达成原位复测目标，1 格证据作废。** 上批 run 8/9 的「首请求即 429 空转」本批未复现
   （本批 429 发生在第 4 请求），说明首请求命中可能是瞬时状态；但第 4 请求处的命中高度稳定复现。
4. **kernel 路径仍未有干净的 PASSED 观察。** 本批唯一 kernel 格（run 2）再次被 429 截断，
   kernel 路径的 PASSED 复现**仍未观察到**。
5. **两处上批教训已固化生效**：User 作用域密钥回退（0.4）与 `PYTHONIOENCODING=utf-8`（0.5）
   本批均生效，3 份 stdout 无编码塌陷。

**交由审计侧复核的两点**：
- (a) 本批「仍有 429」主判定 + 「2 个有效 PASSED 格零拒绝」的**并存**读法是否正确（即：O4 缺口
  是否可按「有效格 2/2 PASSED 且零拒绝」部分关闭，还是必须等 kernel 格也拿到 PASSED 才算完整）；
- (b) 429 是否应按「单 run 请求深度配额」方向与 CommandCode 交涉（本批与上批合计 6 次 429 中，
  5 次落在 `model_request_index=4`、1 次落在 `index=1`）。

## 8. 附带观察（非本批目标，如实记录)

- `.dig/baseline-summary.json` 的 mtime 在本批执行前后多次发生推进（07:20、07:21、07:23、08:10），
  但 **fingerprint 值每次读取均逐字等于 F0**，无一次变化。执行侧未运行 `pipeline build`/`doctor`，
  亦未写入该文件（仅只读读取）。该 mtime 推进**成因不明，疑为外部进程**（审计侧或环境侧）所致，
  建议审计侧确认；**不影响 O8/C5 判定**（判定依据是指纹值本身，而非 mtime）。
- run 1 的工具调用尝试达上界 **8/8**（evaluator 仍 PASSED）。单点，不足以判定预算紧张，
  但若后续批次再现，建议评估上界余量。
- 本批 3 格历史归档中，同类格在 08-31～09-15 期间多次 PASSED（如
  `required_null_order_customer_a × STATIC_SKILL` 与 `× DIAGNOSTIC_KERNEL` 均有多条 PASSED 记录），
  佐证上批 FAILED 属异常而非格本身失效。

## 9. 红线遵守声明

- 未改动任何代码或配置；未运行 `benchmark` / `doctor` / `freeze` / `pipeline build`。
- 未触碰 T13 场景与 planner 策略；未 push。
- 密钥值未出现在任何输出、文件或本报告中；未写入 `.env.diagnostic`。
- 无重跑、无替换失败格、无更换模型或样本、无顺序调整；3 条命令按计划 §1 原序串行执行。
- 本批归档为实验性观察产物，不进入任何评估集合，不与 v29/v30 基线混算。
