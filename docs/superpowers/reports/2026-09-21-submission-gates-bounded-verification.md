# I1/I2 提交门 · 有界真实验证结果报告（2026-09-21）

- 依据：[授权书](../plans/2026-09-21-submission-gates-bounded-verification-authorization.md)（签署区三项
  已填：所有者 夕沢 / 10 USD / 2026-09-21）。
- 执行范围：验收阶梯第 2 步「有界真实验证」，9 次 `eval run`，全部串行执行完毕，无重跑、无替换、
  无换模型或样本、无顺序调整。
- 修订（2026-09-21，审计 P2-1/P3-1）：§7 429 归因与 O6 表 run 3 计数更正。
- 结论摘要：**O4 零违例、`GATE_INTERNAL_ERROR` 零次**；但 9 格中出现 5 次 provider `HTTP_429`
  （run 3/5/6 于第 4 模型请求、run 8/9 于首次请求），kernel 格 4 中 3 命中；run 4 无协议异常并
  完整跑通 kernel 门链路。因此本批对 **门逻辑**给出了干净证据，对**模型任务表现**给出的证据
  **不完整**。

本批归档标记为**实验性观察产物**，不进入任何评估集合，不与 v29/v30 基线混算。

---

## 1. 执行基线

| 项 | 实测值 |
| --- | --- |
| HEAD（执行前） | `87edc59dc4db16bb57ed5769f5102672a39e45db` |
| HEAD 与授权基线关系 | 逐字等于 `87edc59`，无漂移 |
| 已跟踪文件（执行前） | 干净（`git status --porcelain` 仅列出未跟踪文件） |
| 只读指纹（执行前 = F0） | `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18` ✅ |
| 单测/静态检查 | HEAD 即 `87edc59` 且工作树干净，按 §2.3 引用终审记录（1089 passed / 5 skipped），未复跑 |
| 未运行项 | `pipeline build`、`doctor`、`benchmark`、`freeze` —— 均未运行 |

**时间线（UTC）**：`git status` 干净检查 → 07:00:04 授权书签署栏转录完成 → 07:01:06 run 1 启动。
即 §2 前置检查在签署栏转录**之前**完成。

**归档 `workspace_dirty: true` 说明**：9 格 `metadata.json` 均记录 `code_revision =
87edc59dc4db16bb57ed5769f5102672a39e45db`（与 HEAD 逐字一致）且 `workspace_dirty: true`。该脏标志
由本次签署栏转录产生（授权书为未跟踪文件，于 07:00:04 转录，早于 run 1 的 07:01:06；本报告在 run
期间尚未存在），**非代码改动**；`git status` 已跟踪文件在执行前后均为干净。

## 2. 模型配置与密钥处置

进程级环境映射（未写入 `.env.diagnostic`，未输出，未进入报告）：

```powershell
$env:DIG_DIAGNOSTIC_MODEL_BASE_URL = "https://api.commandcode.ai/provider/v1"
$env:DIG_DIAGNOSTIC_MODEL_NAME = "deepseek/deepseek-v4.1-flash"
$env:DIG_DIAGNOSTIC_MODEL_API_KEY = $env:COMMANDCODE_API_KEY
```

**偏离与处置（如实记录）**：`COMMANDCODE_API_KEY` 在本 harness 进程内**不存在**（该进程未继承），
但存在于 Windows **User 作用域**（长度 93，值未输出）。执行侧在每条命令内以
`[Environment]::GetEnvironmentVariable('COMMANDCODE_API_KEY','User')` 回退读取，再赋给
`DIG_DIAGNOSTIC_MODEL_API_KEY`。授权书 §3 规定的映射语义（进程内映射、不落盘、不输出）未变，
仅取值来源由「进程环境」变为「User 作用域回退」。

配置生效证据：9 格 `metadata.json` 全部记录
`provider = openai-compatible`、`model = deepseek/deepseek-v4.1-flash`、
`model_base_url = https://api.commandcode.ai/provider/v1`，与
`diagnostic_config.openai_compatibility_kwargs` 的 CommandCode/DeepSeek 兼容档案精确配对。

**恢复（O8）**：三个变量在每个 pwsh 子进程中设置、随进程退出即消失；执行后复核三者均为
「未设置」。执行期另设 `PYTHONIOENCODING=utf-8`（仅输出编码，见 §3），未导出到持久环境。

## 3. 逐条命令与逐字 stdout 归档

9 条命令按授权书 §4 顺序串行执行，stdout/stderr 合并逐字落盘于 `.dig/`：

| # | 命令 | stdout 归档文件 |
| --- | --- | --- |
| 1 | `uv run data-incident-gym eval run required_null_order_customer_b --strategy static-skill` | `.dig/gate-verify-required_null_order_customer_b-static-skill.txt` |
| 2 | `uv run data-incident-gym eval run duplicate_payment_coupon_a --strategy static-skill` | `.dig/gate-verify-duplicate_payment_coupon_a-static-skill.txt` |
| 3 | `uv run data-incident-gym eval run orphan_payment_coupon_a --strategy static-skill` | `.dig/gate-verify-orphan_payment_coupon_a-static-skill.txt` |
| 4 | `uv run data-incident-gym eval run required_null_order_customer_b --strategy diagnostic-kernel` | `.dig/gate-verify-required_null_order_customer_b-diagnostic-kernel.txt` |
| 5 | `uv run data-incident-gym eval run duplicate_payment_coupon_a --strategy diagnostic-kernel` | `.dig/gate-verify-duplicate_payment_coupon_a-diagnostic-kernel.txt` |
| 6 | `uv run data-incident-gym eval run orphan_payment_coupon_a --strategy diagnostic-kernel` | `.dig/gate-verify-orphan_payment_coupon_a-diagnostic-kernel.txt` |
| 7 | `uv run data-incident-gym eval run required_null_order_customer_a --strategy static-skill` | `.dig/gate-verify-required_null_order_customer_a-static-skill.txt` |
| 8 | `uv run data-incident-gym eval run required_null_order_customer_a --strategy diagnostic-kernel` | `.dig/gate-verify-required_null_order_customer_a-diagnostic-kernel.txt` |
| 9 | `uv run data-incident-gym eval run silent_payment_drop_partition_a --strategy static-skill` | `.dig/gate-verify-silent_payment_drop_partition_a-static-skill.txt` |

### 3.1 逐字 stdout（全部 9 条，字节级）

文件 3–9（246 字节）内容逐字为：

```
评测未通过。
status: FAILED
run_id: <run_id>
artifacts: artifacts/<run_id>
scoring_inputs: C:\Users\29913\codex_space\DataIncidentGym\.dig\scoring-inputs\<run_id>
```

文件 1、2（256 字节）首行存在**编码塌陷**：捕获时控制台代码页为 GBK（`chcp` 936）而 Python 以
UTF-8 写出，非 ASCII 首行被替换为 4 个 U+FFFD（首 8 字节 `ef bf bd ef bf bd ef bf`），实际内容
即上表的「评测未通过。」。ASCII 各行完整无缺。自 run 3 起增设 `PYTHONIOENCODING=utf-8`，
run 3–9 首行均正确解出「评测未通过。」。该差异属**捕获层输出编码**，不改变 run 内容、模型行为或
任何被测指标；run 1/2 未重跑（§4「无重跑」）。

全部 9 条命令退出码均为 `1`（`eval run` 在 evaluator 未通过时以非零码返回，属预期语义）。

## 4. 9 个 run_id

| # | case_id | strategy | run_id | eval | recovery |
| --- | --- | --- | --- | --- | --- |
| 1 | required_null_order_customer_b | static-skill | `a3ddc77b2ec04f87b15acf4852ca4f41` | FAILED | HEALTHY |
| 2 | duplicate_payment_coupon_a | static-skill | `fc47fbad5e4d43d686dfaa94c8592901` | FAILED | HEALTHY |
| 3 | orphan_payment_coupon_a | static-skill | `b64cb13e233c4ae6a6738c1775ff5bec` | FAILED | HEALTHY |
| 4 | required_null_order_customer_b | diagnostic-kernel | `fab184b6d7e249f7982a4e14da853864` | FAILED | HEALTHY |
| 5 | duplicate_payment_coupon_a | diagnostic-kernel | `c71a531c58f747d997bf4d5a7e4a3f35` | FAILED | HEALTHY |
| 6 | orphan_payment_coupon_a | diagnostic-kernel | `df5558b419bb4003950c3a38c938008c` | FAILED | HEALTHY |
| 7 | required_null_order_customer_a | static-skill | `3145d711b9d34791895ce06788e8225c` | FAILED | HEALTHY |
| 8 | required_null_order_customer_a | diagnostic-kernel | `fba20a7bafd84b39b0fd0a65188b91e1` | FAILED | HEALTHY |
| 9 | silent_payment_drop_partition_a | static-skill | `255809006983438eaf4c913e1915584c` | FAILED | HEALTHY |

**9 格 evaluator 无一 PASSED。** 这是本批最重要的负面观察（详见 §7、§10）。

## 5. O1–O8 逐项实测

### O1 每格 `EVIDENCE_GATE` 事件（拒绝码 / 时序 / 是否最终接受）

共 16 个 `EVIDENCE_GATE` 事件。「时序位置」为 trace `sequence`（回合内序列）。

| # | 门事件（sequence 升序） | 最终结局 |
| --- | --- | --- |
| 1 | seq8 accepted=true `INSUFFICIENT_EVIDENCE` | 接受，无拒绝 |
| 2 | seq5 accepted=**false** `CLAIM_SUPPORT_REQUIRED` → seq7 accepted=true `MODEL_OUTPUT_RETRY_EXHAUSTED` | 拒绝 1 次后耗尽 |
| 3 | seq7 accepted=**false** `CLAIM_SUPPORT_REQUIRED` → seq9 accepted=true `MODEL_PROTOCOL_ERROR` | 拒绝 1 次后协议错误 |
| 4 | seq7 accepted=**false** `UNRESOLVED_EVIDENCE_UNBOUND`（带 `rejected_decision`）→ seq8 accepted=**false** 同码 → seq9 accepted=true `INSUFFICIENT_EVIDENCE` | **拒绝 2 次后模型修复成功，门最终接受** |
| 5 | seq3 accepted=**false** `CLAIM_SUPPORT_REQUIRED` → seq4 accepted=**false** 同码 → seq6 accepted=true `MODEL_PROTOCOL_ERROR` | 拒绝 2 次后协议错误（429） |
| 6 | seq5 accepted=**false** `CLAIMS_INCOMPLETE`（带 `rejected_decision`）→ seq7 accepted=true `MODEL_PROTOCOL_ERROR` | 拒绝 1 次后协议错误（429） |
| 7 | seq8 accepted=true `CONFIRMED` | 接受，**零拒绝事件** |
| 8 | seq2 accepted=true `MODEL_PROTOCOL_ERROR` | 首次请求即 429，无提交可判 |
| 9 | seq2 accepted=true `MODEL_PROTOCOL_ERROR` | 首次请求即 429，无提交可判 |

### O2 拒绝码分布（`GATE_INTERNAL_ERROR` 必须为零）

`accepted=false` 事件共 7 个，按来源分两层：

| 层 | 码 | 次数 | 出处 |
| --- | --- | --- | --- |
| harness 提交门（`SubmissionPolicy`，I1） | `CLAIM_SUPPORT_REQUIRED` | **4** | run 2 / 3 / 5（5 有 2 次） |
| harness 提交门（`SubmissionPolicy`，I2） | `GAP_RECEIPT_REQUIRED` | **0** | 未出现 |
| kernel 决策合同（`diagnostic_validation`） | `UNRESOLVED_EVIDENCE_UNBOUND` | 2 | run 4 |
| kernel 决策合同（`diagnostic_validation`） | `CLAIMS_INCOMPLETE` | 1 | run 6 |

**`GATE_INTERNAL_ERROR` = 0 次** ✅（O2 判定通过）。

**必须记录的结构性发现**：kernel 投影路径的拒绝分两类，且**都**以 `EVIDENCE_GATE` 事件落 trace——
(a) `SubmissionPolicy` 拒绝（`CLAIM_SUPPORT_REQUIRED` 等），`rejected_decision` 为 `null`；
(b) kernel `finalize` 抛出的 `KernelError` 决策合同拒绝，**带** `rejected_decision` 载荷
（`p1.rejected_decision.v1`，含 assessments/claims/unresolved 计数）。因此 O2 的「拒绝码分布」
若只按 `event_type=EVIDENCE_GATE` 聚合，会把 kernel 合同码与 harness 门码混为一谈。本报告按上述
两层分列，供审计侧复核口径。

另：run 5 的 2 次 `CLAIM_SUPPORT_REQUIRED` 证明 **kernel 投影路径确实装配并执行了
`SubmissionPolicy`**（`diagnostic_agent.py:2738`）。

### O3 拒绝后结局（修复成功 vs 耗尽）

| 结局 | 格 |
| --- | --- |
| 修复后被接受（门最终 `accepted=true` 且提交为真实诊断） | **run 4**（2 次 kernel 合同拒绝→`INSUFFICIENT_EVIDENCE` 被接受） |
| 耗尽为 `MODEL_OUTPUT_RETRY_EXHAUSTED` | **run 2**（`output_retry_used=2`，达合同上界 2） |
| 拒绝后遭 provider 中断，未走到修复或耗尽 | run 3、run 5、run 6 |
| 无拒绝事件 | run 1、run 7（另 run 8/9 无提交可判） |

**关键结论**：唯一一次「重试后修复成功」是 run 4 的 **kernel 合同层**拒绝。harness 门层
（`CLAIM_SUPPORT_REQUIRED`）的 4 次拒绝**没有一次**以修复成功收尾——run 2 耗尽，run 3/5 被 429 中断。
即 D2「拒绝→修复」通路在门层本批**未被观察到成功案例**。

### O4 误拒为零（精度定理线上版）

**未观察到任何违例** ✅。逐格核对：

- 三个 PASSED 对照格（run 7/8/9）**均零拒绝事件**。
- 全部 9 格 evaluator 均非 PASSED，故 O4 的触发条件（「PASSED 且出现拒绝事件」）在本批**不可能成立**。
- run 7 是最强单点证据：evaluator FAILED（仅 `REQUIRED_EVIDENCE_TYPES_PRESENT` 一项未过）而门
  对 `CONFIRMED` 提交**一次接受、零拒绝**——门没有因为后续质量检查失败而误拒。

**效力限定（必须如实声明）**：因 run 8/9 空转、run 7 质量未达标，本批**不存在任何一个 evaluator
PASSED 的格**。因此 O4 得到的是「无违例」的弱证据，**不是**「PASSED 格上的精度定理实证」。
本授权 §5 表格中「3 个 PASSED 对照格必须零拒绝事件」的**完整**验证未达成。

### O5 触发期望格是否实际触发预期门码

| 格 | 期望 | 实测 |
| --- | --- | --- |
| required_null_order_customer_b × static | I2 拒 `GAP_RECEIPT_REQUIRED` | **未触发**：模型直接以 `INSUFFICIENT_EVIDENCE` 提交，门接受；evaluator 仍 FAILED（`INSUFFICIENCY_GAP_DECLARED`） |
| duplicate_payment_coupon_a × static | I1 拒 `CLAIM_SUPPORT_REQUIRED` | **触发**（run 2 seq5）✅ |
| orphan_payment_coupon_a × static | I1 拒 | **触发**（run 3 seq7）✅ |
| 三个 kernel 对照格 | 线上行为首次观察 | 全部因 429 中断，见 §7 |

按 §5/O5 与 §6，「模型本次直接做对而不触发属正常结果」，不构成不符。

### O6 耗尽终态格的归档完整性

对「带拒绝事件」的 5 格（run 2/3/4/5/6）逐格核对归档完整性：

| run | 终态码 | trace 事件数（门事件） | 终态码可见 | evidence_inventory | 归档 6 件套 |
| --- | --- | --- | --- | --- | --- |
| 2 | `MODEL_OUTPUT_RETRY_EXHAUSTED` | 有（2 门事件 + `MODEL_PROTOCOL` seq6，`output_retry_used=2`） | ✅ diagnosis.summary 可见 | 4 条 | 完整 |
| 3 | `MODEL_PROTOCOL_ERROR` | 有（2 门事件 + `MODEL_PROTOCOL` seq8，`transport=HTTP_429`） | ✅ | 5 条 | 完整 |
| 4 | `INSUFFICIENT_EVIDENCE`（正常终态） | 有（3 门事件） | ✅ | 5 条 | 完整 |
| 5 | `MODEL_PROTOCOL_ERROR` | 有（3 门事件 + 429 事件） | ✅ | 2 条 | 完整 |
| 6 | `MODEL_PROTOCOL_ERROR` | 有（2 门事件 + 429 事件） | ✅ | 3 条 | 完整 |

**结论**：D2 设计初衷成立——耗尽/中断格**未出现静默接受**，trace 与证据均保留，终态码在
`trace.jsonl` 与 `diagnosis.json` 两处可见。9 格归档均为完整 6 件套
（`metadata.json` / `trace.jsonl` / `evidence.json` / `diagnosis.json` / `evaluation.json` / `report.md`）。
run 8/9 归档同样完整（`evidence_inventory: []`，如实反映零请求）。

### O7 每格预算实测（合同上界 模型请求 8 / 工具调用 8 / 输出重试 2 / 时长 300s）

| 格 | 模型请求 | 工具调用尝试 | 输出重试 | 诊断耗时 | elapsed_ms |
| --- | --- | --- | --- | --- | --- |
| required_null_order_customer_b × static | 6 / 8 | 7 / 8 | ≤2 | 39.4s | 114.5s |
| duplicate_payment_coupon_a × static | 5 / 8 | 4 / 8 | **2 / 2（达上界）** | 23.3s | 91.2s |
| orphan_payment_coupon_a × static | 3 / 8 | 6 / 8 | ≤2 | 26.0s | 90.9s |
| required_null_order_customer_b × kernel | 6 / 8 | 6 / 8 | ≤2 | 102.3s | 166.8s |
| duplicate_payment_coupon_a × kernel | 3 / 8 | 2 / 8 | ≤2 | 125.5s | 191.6s |
| orphan_payment_coupon_a × kernel | 3 / 8 | 4 / 8 | ≤2 | 91.6s | 155.8s |
| required_null_order_customer_a × static | 3 / 8 | 7 / 8 | ≤2 | 18.1s | 79.8s |
| required_null_order_customer_a × kernel | 0 / 8 | 0 / 8 | — | 2.6s | 64.1s |
| silent_payment_drop_partition_a × static | 0 / 8 | 0 / 8 | — | 2.1s | 61.9s |
| **合计** | **29 / 72** | **36 / 72** | — | ~431s | **1016.8s（16.9 min）** |

- 模型请求：**29 / 72**，合同上界内，余量 43。
- 工具调用尝试：**36 / 72**，上界内（逐格均 ≤7/8）。
- 输出重试：**1 格达上界**（run 2 `output_retry_used=2`），即合同上界 2 被精确触及一次。
- 单格墙钟最长 191.6s，**均未触及 300s 上限**。
- 空转格（run 8/9）在 429 于首次请求即失败，诊断耗时仅 2 秒级——runner 未在 provider 异常上
  做静默重试放大。

### O8 环境恢复指纹

| 项 | 实测 |
| --- | --- |
| 活库恢复（每格 `recovery_status`） | **9/9 = HEALTHY** |
| `DIG_DIAGNOSTIC_MODEL_BASE_URL` | 执行后未设置 ✅ |
| `DIG_DIAGNOSTIC_MODEL_NAME` | 执行后未设置 ✅ |
| `DIG_DIAGNOSTIC_MODEL_API_KEY` | 执行后未设置 ✅ |
| HEAD（执行后） | `87edc59dc4db16bb57ed5769f5102672a39e45db`（未变） |
| 只读指纹（执行后） | `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18` **逐字等于 F0** ✅ |
| 已跟踪文件（执行后） | 干净 |

## 6. 费用与 token 用量（对照 §7 上限 10 USD）

**实测 token 用量（provider 自报 `usage`，9 格合计）**：

| 项 | 实测值 |
| --- | --- |
| 输出侧输入 token | **282,859** |
| 输出侧输出 token | **84,801** |
| 模型请求 | 29 |

**对照上限**：本工作区**无计费端点可读**，且本环境对外部定价页面的抓取被拒绝
（`commandcode.ai` / `openrouter.ai` / `llm-stats.com` 均解析到非公网地址），因此**无法取得
CommandCode 网关的权威单价，本报告不给出精确账单金额**。可给出的界：

- 若按第三方公布的 DeepSeek-V4.1-Flash 量级单价（约 0.10 USD/百万输入 token、
  0.30 USD/百万输出 token，[Vercel AI Gateway 模型页](https://vercel-docs.vercel.sh/ai-gateway/models/deepseek-v4.1-flash)、
  [Artificial Analysis](https://artificialanalysis.ai/models/deepseek-v4-1-flash)；另有报道称
  [缓存输入非高峰价 0.003 USD/百万 token](https://venturebeat.com/technology/deepseek-v4-1-flash-debuts-with-0-003-1m-off-peak-cached-input-rate-and-benchmarks-eclipsing-gpt-5-6-sol-claude-opus-5)），
  其**裸模型成本量级约 0.03–0.06 USD**。
- 该数字**不构成**账单结论：网关可能加价、可能按订阅计划计费、可能另计思考 token 与重试请求。
  **10 USD 上限的最终核销须以 provider 侧账单为准**（审计侧如可读取账目，请以账目覆盖本节估算）。

**另有两点用量事实须记录**：
1. 授权书 §1「模型请求合同上界 9×8 = 72 次」实测消耗 **29 次**，未超。
2. 存在 **5 次 provider 传输层异常（`HTTP_429`）**，另有 run 2 的 1 次输出校验失败（非 provider
   异常，详见 §7）；429 部分可能对应网关侧被计费但无产出的请求，本批无法从归档中区分其计费状态。

## 7. Provider 异常（停止规则执行情况，如实归因）

授权书 §6 规定：provider 异常按既有 runner 行为停机并如实归因，**不自行换端点重试**。执行侧遵守：
未更换 base_url、未更换模型、未自行重试任何失败格。runner 自身行为为「单格失败并正常终态归档」。

**传输层异常共 6 起，须分列**：其中 **5 起为 `transport=HTTP_429`**
（`category=PROVIDER_PROTOCOL_FAILURE`、`error_type=MODEL_API_ERROR`、`error_origin=PROVIDER`），
另 1 起为 run 2 的 **输出校验失败**（`stage=OUTPUT_SCHEMA_VALIDATION`、
`category=OUTPUT_SCHEMA_REJECTED`、`error_type=UNEXPECTED_MODEL_BEHAVIOR`、无 `transport` 字段），
**不属 provider 异常**，如实列于下表末行：

| run | 429 发生位置 | 后果 |
| --- | --- | --- |
| 3 | `model_request_index=4` | 拒绝 1 次后被中断，门终止为 `MODEL_PROTOCOL_ERROR` |
| 5 | `model_request_index=4` | 拒绝 2 次后被中断 |
| 6 | `model_request_index=4` | 拒绝 1 次后被中断 |
| 8 | `model_request_index=1` | **首次请求即失败，整格空转** |
| 9 | `model_request_index=1` | **首次请求即失败，整格空转** |
| 2 | `model_request_index=5`（`OUTPUT_SCHEMA_VALIDATION`，**非 429**） | 该格为输出重试耗尽，**非 provider 异常** |

**形态特征**：kernel 格 4 个中 **3 个命中 429**（run 5/6 于 `index=4`、run 8 于 `index=1`）；
**run 4 无任何协议异常**（trace 无 `MODEL_PROTOCOL`/429 事件），正常终态 `INSUFFICIENT_EVIDENCE`。
2 个 static 格命中（run 3 于 `index=4`、run 9 于 `index=1`）。即命中形态为「重负载格在**第 4 模型
请求**处命中（run 3/5/6 共 3 例）、轻负载格在**首次请求**即命中（run 8/9 共 2 例）」。

**run 4 正名（本批最强正面单点证据）**：run 4 是**唯一完整跑通 kernel 门链路**的格——
2 次 kernel 合同拒绝（`UNRESOLVED_EVIDENCE_UNBOUND`）→ 模型修复 → 门最终 `accepted=true`
（`INSUFFICIENT_EVIDENCE`），全程无协议异常。它证明 **D2「拒绝→修复→接受」通路在 kernel 投影
路径上实际可用**，也说明本批限流并非必然阻断。因此授权书 §1「kernel 投影路径的首次线上观察」
目标**已部分达成**：kernel 完整门链路在 run 4 上被观察到，但其余 3 个 kernel 格未跑完，
**样本量不足以支撑 kernel 路径的稳定性结论**。

## 8. 停止规则逐条判定（§6）

| 停止条件 | 判定 |
| --- | --- |
| 任何最终 evaluator PASSED 的格出现门拒绝事件（O4 违例） | **未触发** —— 本批无 PASSED 格，且 run 7 零拒绝 |
| 任何归档出现 `GATE_INTERNAL_ERROR` | **未触发** —— 0 次 |
| O8 指纹不符 | **未触发** —— 逐字等于 F0 |
| provider 异常 | **已触发 5 次（`HTTP_429`：run 3/5/6/8/9）**，按 runner 行为停机、如实归因，未换端点重试；另 run 2 的 1 次输出校验失败属输出重试耗尽，非 provider 异常 |

因此 §6 的「立即停止」类条件**均未触发**，9 格按序执行完毕。

## 9. 基线对照（v29/v30 预期 vs 本批实测）

| 格 | 基线预期 | 本批实测 | 一致性 |
| --- | --- | --- | --- |
| required_null_order_customer_b × static | QUALITY_FAILED，I2 拒 `GAP_RECEIPT_REQUIRED` | FAILED；**未触发门**（模型直接弃权） | 结果一致，门码不同 |
| duplicate_payment_coupon_a × static | QUALITY_FAILED，I1 拒 | FAILED；I1 拒 `CLAIM_SUPPORT_REQUIRED` **触发** | ✅ 与预期门码一致 |
| orphan_payment_coupon_a × static | QUALITY_FAILED，I1 拒 | FAILED；I1 拒 **触发** | ✅ 与预期门码一致 |
| required_null_order_customer_b × kernel | RUN_ERROR（provider） | FAILED（无协议异常，完整跑通 kernel 门链路：2 次合同拒绝→修复→门接受 `INSUFFICIENT_EVIDENCE`） | 更优：门路径完整跑通 |
| duplicate_payment_coupon_a × kernel | RUN_ERROR（provider） | FAILED（I1 拒 2 次后于 index 4 遇 429） | 同类 provider 失败 |
| orphan_payment_coupon_a × kernel | STATUS_ERROR（wrong_abstention） | FAILED（kernel 合同拒 1 次后于 index 4 遇 429） | —— |
| required_null_order_customer_a × static | **PASSED，门接受** | **FAILED**（仅 `REQUIRED_EVIDENCE_TYPES_PRESENT` 未过）；门接受 ✅ | ❌ 结果不符 |
| required_null_order_customer_a × kernel | **PASSED，门接受** | **FAILED**（首请求 429 空转） | ❌ 不可评 |
| silent_payment_drop_partition_a × static | **PASSED，门接受** | **FAILED**（首请求 429 空转） | ❌ 不可评 |

**三个 PASSED 对照格全部未复现 PASSED**。其中 run 7 为真实质量失败（非门所致，非 provider 所致），
run 8/9 为 provider 空转。**本批与 v29/v30 基线的 PASSED 率不具可比性**，原因需在设计层复盘时
分离「模型侧波动」与「provider 限流」两个因素。

## 10. 结论

1. **门逻辑本批给出干净证据，零告警级缺陷。**
   - `GATE_INTERNAL_ERROR` = **0**（O2 通过，约束 1 未见失效）。
   - 误拒 = **0**（O4 无违例；run 7 在质量失败情形下门仍零拒绝）。
   - I1 门码 `CLAIM_SUPPORT_REQUIRED` 实际触发 4 次；I2 门码 `GAP_RECEIPT_REQUIRED` 本批 0 次。
   - 拒绝后未出现静默接受：所有中断/耗尽格均带明确终态码且 trace/证据完整（O6 通过）。
2. **「拒绝→修复」通路在 harness 门层未被观察到成功**；唯一的修复成功案例在 kernel 合同层
   （run 4）——该格亦为**本批唯一完整跑通 kernel 门链路**的格（2 次合同拒绝→修复成功→门最终接受），
   是 D2 通路的最强正面单点证据（详见 §7「run 4 正名」）。
3. **本批对模型任务表现的证据不完整**：9 格 evaluator 全部 FAILED，3 个 PASSED 对照格无一 PASSED，
   其中 2 格因首次请求即 429 完全空转。故 **O4 的「PASSED 格零拒绝」完整验证未达成**，仅有
   「无违例」的弱证据。授权书 §1「kernel 投影路径首次线上观察」**部分达成**（kernel 格 4 中 3 命中
   429，run 4 完整跑通）。
4. **provider 限流是本批的主要混淆因素**（5 次 429；kernel 格 4 中 3 命中），形态高度规则
   （重负载格命中于 `index=4` 共 3 例、轻负载格命中于 `index=1` 共 2 例），更像网关侧配额/节流而非
   随机抖动；但 run 4 完整跑通证明限流**非必然阻断**。任何基于本批的模型能力结论都需先剥离该因素。
5. **环境与成本受控**：O8 全部通过（活库 9/9 HEALTHY、指纹逐字等于 F0、环境变量已恢复、HEAD 未变）；
   token 用量 282,859 输入 / 84,801 输出、模型请求 29/72，量级上远离 10 USD 上限，
   但**精确账单须以 provider 账目为准**（本环境无法读取计价端点）。
6. **本批归档为实验性观察产物**，不进入任何评估集合、不与 v29/v30 混算；归档身份仍为
   `p1.controller.v19` / `p1.strategy_adapter.v1`（§0.3 已知缺口，身份升版属阶梯第 3 步）。

**建议交由审计侧裁定的两项**（审计报告 §3 已给出意见，此处按修订后口径同步）：
- (a) 429 限流是否需要在阶梯第 3 步前先解决：修订后的形态为「重负载格命中于第 4 模型请求（3 例）、
  轻负载格首请求命中（2 例）」，仍支持「先解决配额/节流再进第 3 步」；但 run 4 完整跑通证明限流
  **非必然阻断**，第 3 步可在降并发/错峰下先行试预检；
- (b) 三个 PASSED 金丝雀格在真实模型上的复现问题——本批无一 PASSED，其中 run 7 为真实质量失败
  （与门无关、与 provider 无关），说明模型波动独立存在；建议 429 缓解后先做一次仅含 3 个金丝雀格的
  小样本复测授权，分离「模型波动」与「provider 限流」两因素后再定第 3 步样本量。

## 11. 红线遵守声明

- 未改动任何代码或配置（`git status` 已跟踪文件执行前后均干净；`code_revision` 9 格均为 `87edc59`）。
- 未运行 `benchmark` / `doctor` / `freeze`；未运行 `pipeline build`。
- 未触碰 T13 场景与 planner 策略；未 push。
- 密钥值未出现在任何输出、文件或本报告中；未写入 `.env.diagnostic`。
- 无重跑、无替换失败格、无更换模型或样本、无顺序调整；9 条命令按授权书 §4 原序串行执行。
