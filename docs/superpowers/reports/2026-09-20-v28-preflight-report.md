# p1-formal-v28 第四次预检报告（2026-09-20）

- 授权依据：所有者 2026-09-20"授权"（v28 一次预检）。
- **结论：预检失败于 MODEL_TOOL_STRUCTURED_OUTPUT；目录平面首次全部通过（大写放行生效）；
  结构化输出探针首次实际执行。run 未启动；v28 回执保留；本次预检实际发出了结构化探针的
  completion 请求（≤2 次，`UsageLimits request_limit=2`，均未成功）。**

## 1. 预检结果

环境面 10 项全部通过。模型面：

- **MODEL_ENDPOINT: PASS（REACHABLE）**——大写放行生效，26 个大写条目不再阻断；
- **MODEL_PRESENT: PASS（deepseek/deepseek-v4.1-flash）**——绑定模型在列；
- **MODEL_TOOL_STRUCTURED_OUTPUT: FAIL（无 diagnostic）**——探针真实执行（真实 completion
  POST，≤2 次，60 秒 asyncio 超时，输出重试 1 次）但未通过。

## 2. 失败原因不可区分（当前无诊断记录）

M21 的 diagnostic 字段按当时"最小"范围仅覆盖目录探针；结构化探针的 `except Exception` 仍静默。
以下假说目前无法区分：Cloudflare 对 POST/completion 端点的不同 WAF 规则、网络/超时、
模型未按要求调用工具、输出 schema 校验失败（重试 1 次后耗尽）。**不做无证据归因。**

## 3. 建议（沿用已建立的诊断优先模式，待所有者裁定）

把 M21 的同一脱敏 diagnostic 机制**延伸到结构化探针检查**（失败时记录：阶段
`stage=model_probe`、异常类型、HTTP 状态、耗时、60 秒超时配置；同样禁止密钥/鉴权头/响应正文；
模型可见的失败原因如 `MODEL_RETRY`/输出校验失败类型可计数化记录）。改动离线可验证（MockTransport
夹具），随后按流程审计、提交、冻结 v29、申请新预检。不加重试、不放宽超时。

备选：接受当前不可区分性、直接申请另一次预检——不推荐：若 POST 面存在 WAF 差异，重试同样失败
且仍无归因。

## 4. 边界

- 额度消耗：本次预检 ≤2 次 completion 请求 + 1 次目录 GET；历史目录 GET 共约 6 次。
  completion 是否扣量仍按"未验证"口径。
- v25–v28 四份失败回执全部保留；数据库保持 F0；未 push。
