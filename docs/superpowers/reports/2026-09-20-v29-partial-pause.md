# p1-formal-v29 部分结果报告：滚动窗口暂停触发（2026-09-20）

- 放行与执行：所有者"授权"（v29 预检 + 既定放行的完整 run）。**预检首次通过**（13 项零失败，
  含结构化探针首次真实通过）；完整 run 启动后，在 13/106 格终态时
  **`ROLLING_WINDOW_UNPASSED_PAUSE` 按批准规则正确触发**：窗口（最近 12 个 model-backed 格，
  seq 2–13）内 10 未通过 / 2 通过，第 13 格终态落账后、第 14 格启动前暂停。
- **本报告是部分结果，按批准规则不作为完整 benchmark 结论。** 现场（ledger、13 份归档、恢复结果）
  原样保留，无第 14 格 ledger 条目。

## 1. 预检（首次通过）

v29 预检 13 项检查全部 PASSED：环境面（uv 0.11.24、postgres、dbt profile、profile 四检）+
目录平面（REACHABLE、deepseek 在列）+ **MODEL_TOOL_STRUCTURED_OUTPUT 首次真实通过**（模型真实
调用工具并返回结构化输出）。`tool_choice=auto` 能力声明生效。

## 2. 部分运行事实

| 项 | 值 |
| --- | --- |
| 终态格 | 13（全部 model-backed；3 COMPLETED / 10 FAILED，失败均为 EVALUATION_FAILED 质量失败，无 RUN_SETUP_ERROR、无环境/恢复失败） |
| 停止触发 | 窗口 12 格中 10 未通过（阈值恰好达到；seq 1 已滑出窗口） |
| 模型身份（M2 部分） | 13/13 归档 provider=openai-compatible、model=deepseek/deepseek-v4.1-flash |
| 用量 | 55 次模型请求；输入 504,647 / 输出 123,137 tokens；工具调用 71 次（成功 64）；墙钟约 10 分钟（约 47 秒/格，远快于历史 mimo 的 ~167 秒/格估算） |
| 套餐占比 | 55 次 ≈ 所述 150,000 次月额度的 0.037%（数量级对照；实际扣量规则仍未验证） |

## 3. 失败归因（只读，来自归档）

诊断状态：CONFIRMED 6、INSUFFICIENT_EVIDENCE 4、MODEL_ERROR 3（`MODEL_PROTOCOL_ERROR` ×2、
`MODEL_TOOL_CALL_LIMIT` ×1）。评测失败检查码直方图：REQUIRED_EVIDENCE_TYPES_PRESENT ×7、
INSUFFICIENCY_GAP_DECLARED ×5、STATUS_EXACT ×3、CLAIM_EVIDENCE_COMPATIBLE ×3、
ROOT_CAUSE_ACCEPTED ×1、AFFECTED_ASSETS_EXACT ×1。

定性：**10 次未通过中 3 次为模型协议/工具预算错误，7 次为真实诊断质量失败**（根因错误、
必要证据类型缺失、不足声明缺失等）——是模型在该正式赛程上的能力表现问题，不是基础设施、
环境或端点问题（基础设施面全程健康）。

## 4. 续跑的机制事实

按批准的停止规则与实现，**暂停是粘性的**：窗口状态（12 格中 10 未通过）由已完成历史重建，
直接 `benchmark run` 续跑会立即再次暂停、执行 0 个新格。继续测量需要所有者**新的裁定**
（例如：接受本部分结果为 v29 的最终结果；或授权改变停止规则的后续身份/规则变更——按流程需
另行审计与冻结）。失败格不得重跑或替换。

## 5. 状态

- ledger、13 份归档、doctor 回执原样保留；数据库保持 F0；未 push。
- 与历史 mimo 结果的比较未做（分母、场景交集与 evaluator 身份差异核对未满足 M4 前置）。
- 待所有者裁定：接受部分结果、离线深挖失败原因，或其他处置。
