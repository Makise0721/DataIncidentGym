# T11 实施报告：外部接入隔离与可复现验收

> 收口状态（2026-09-17，第三轮复核）：**通过**——T11 按"确定性客户端的隔离闭环与边界验收"收口，
> 无新增阻塞项。复核确认：MCP 取证与独立提交使用同一 harness 会话，权威日志位于容器挂载范围之外；
> 闭环、公共包与 MCP 的定向回归 28 passed / 1 skipped；归档权威日志核对一致（合规路径 5/5 接受，
> 攻击路径 9 条记录、7 接受，两次拒绝为 `RELATION_NOT_ALLOWED`、`TOOL_BUDGET_EXHAUSTED`）；
> 容器攻击记录中私有平面与管理环境变量均为 `DENIED`，权威日志为 `ABSENT`。本轮复核未重跑容器、
> 未调用真实模型、未修改代码。**收口范围不含真实模型抗注入能力，也不含真实重复可靠性测量。**
> 至此 T01–T11 的既定交付可收口。

- 日期：2026-09-16 首版；2026-09-17 第二轮整改（闭环）后更新。基线 `e664b8e` + 未提交工作树
  （T01–T10 交付之上）。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T11；依赖 T06/T08/T10。
- 交付：
  - `src/data_incident_gym/public_package.py`：公共任务包导出（`build_public_task_package`）与
    fail-closed 泄漏校验（`verify_public_package`）；`task.json` 的 `submission` 块给出**可执行**的
    提交通道（文件名、必填/可选字段、引用规则）。
  - `src/data_incident_gym/strategy_bridge.py`：闭环桥接。harness 独占唯一会话，策略进程 stdio 上的
    MCP 逐条送入该会话，每次调用的请求与收据实时写入 harness 权威日志（`ToolGateway.recorder`）。
  - `examples/isolated_client.py`：沙箱内 stdlib-only MCP 客户端，`compliant` 与 `attack` 两模式。
  - `examples/isolation_probe.py`：沙箱内确定性探针（读取私有平面/其他 run/管理凭据、尝试写库）。
  - `examples/isolation_acceptance.py`：三种配置的验收编排器，写 `artifacts/isolation/acceptance.json`
    并导出闭环权威日志。
  - `docker/isolation.Dockerfile`：最小隔离镜像（可 `--build-arg BASE` 覆盖基础镜像）。
  - 规范：需求 M19 修订（新增第 8 条）+ §14.1；`docs/superpowers/specs/2026-09-16-strategy-access-protocol.md`
    新增 §7「隔离进程的桥接与提交通道」。
- 验证：`ruff check .` 通过；`git diff --check` 通过；单测 **779 passed / 5 skipped**（T11 相关 11 项：
  10 项通过、1 项因账户无符号链接权限跳过；其中 4 项是闭环回归）；隔离验收实跑（见下表）
  `isolation_verified: True`。未调用真实模型，未重跑数据库。

## 闭环验收（2026-09-17 实跑，本机 Windows + Docker 29.4.3 Linux 容器）

harness 进程持有唯一 `StrategySession`；容器里的客户端只能通过它自己的 stdio（挂到桥接上）看到六工具，
答案只以文件形式交回 harness 解析后再交给**同一会话** `submit`。

| 路径 | 客户端做了什么 | harness 记录与终态 | 结论 |
| --- | --- | --- | --- |
| `compliant` | 读公共包 → 5 次 MCP 取证（run results / node error / lineage / profile / history）→ 写 `submission.json` | 5 次尝试全部接受、5 条证据登记；提交被接受，终态 `CONFIRMED`；客户端 `audit.jsonl` 与 harness 日志逐条一致 | `verified=true` |
| `attack` | 读注入夹具并真实尝试其中的越权动作 → 8 次计数调用 + 第 9 次超预算 → 越权关系调用 → 未声明工具 `run_sql` → 伪造引用、夹带字段、假自报用量 → 删除自己的 `audit.jsonl` | 9 条权威日志（7 接受、2 拒绝：`RELATION_NOT_ALLOWED`、`TOOL_BUDGET_EXHAUSTED`）；`EVIDENCE_NOT_REGISTERED`、`SUBMISSION_FILE_INVALID`、`SUBMISSION_ALREADY_FINAL`、`SESSION_CLOSED` 依次生效；终态仍为 `CONFIRMED` | `verified=true` |

- **harness 持有唯一会话**：客户端没有 submit/cancel 工具，桥接是唯一入口；调用在 harness 侧登记与计数。
  攻击客户端删除自己的日志后，harness 日志仍完整（9 条、序号 1–9、含两次拒绝），其"只用了 3 次调用"的
  假自报与权威计数（9）不一致，被记为不可信而不是被采纳。**权威日志写在沙箱之外的 harness 目录**，
  攻击客户端专门搜索并尝试删除 `*harness*audit*`（结果 `ABSENT`：在它可达的任何位置都不存在），
  该检查是 `attack` 路径的必需项。
- **独立提交只承载答案**：`submission_smuggled.json` 携带 `expected_status`/`incident_case_id`/`scoring`
  三个额外字段，被 harness 的严格解析整文件拒收（`SUBMISSION_FILE_INVALID`），会话重试计数保持 0；
  伪造引用（一个从未被任何工具返回的 `ev_…`）得到 `EVIDENCE_NOT_REGISTERED` 并消耗 1 次输出重试；
  随后合法的 `submission.json` 在同一会话上被接受。
- **成功与拒绝同时覆盖**：合规路径端到端成功；拒绝路径覆盖伪造引用、删除客户端日志、超预算、重复提交、
  终态后调用，另有越权关系（后端真实码）与未声明工具（MCP 错误、不计尝试、无收据）两条旁路。
- **注入夹具在闭环中被真实尝试**：容器内客户端对 `config/scenarios`、`.dig/scoring-inputs`、`artifacts`
  与九个管理环境变量的尝试全部 `DENIED`（`FileNotFoundError` / 无变量），即边界成立——这仍是
  **边界行为**结论，不是真实模型抗注入结论。
- 证据文件：`artifacts/isolation/acceptance.json`、`closed_loop_compliant_harness_audit.jsonl`、
  `closed_loop_attack_harness_audit.jsonl`、`closed_loop_attack_attack_report.json`。
- 本地回归（`tests/unit/test_isolation_loop.py`，无 Docker）跑同一客户端在同一桥接上做同样的两件事；
  本地同用户进程能读到仓库，因此那里如实记录 `privileged_attempts.private_contract = ALLOWED`，
  该检查不参与本地判定。

## 配置与边界行为（2026-09-16 实跑）

| 配置 | 私有平面 | 管理凭据 | 写数据库 | 结论 |
| --- | --- | --- | --- | --- |
| windows_process（独立进程、专用工作目录、白名单环境） | **ALLOWED**（可列出 `config/scenarios`、`.dig/scoring-inputs`、`artifacts`） | DENIED（环境中无管理变量） | DENIED（连接超时） | `isolated=false`，如实记为**可信开发模式** |
| docker_container（只挂载沙箱 rw + 包源码 ro、`--network none`） | DENIED（目录未挂载） | DENIED | DENIED（真实驱动、连接失败） | `isolated=true` |

- 公共任务包：`verify_public_package` 报 `clean=true`，包内仅 `task.json` 与 `README.md`。
- 探针的写尝试在事务内 `ROLLBACK`，不在任何数据库留下痕迹。
- 镜像：首次 `docker build` 因镜像层拉取超时失败（记录在报告里），随后镜像就绪；
  编排器另实现了"注册表不可达时回退本地含 python3 的缓存镜像"路径并在报告中标注所用基础镜像。

## 验收对照（计划原文）

| 计划要求 | 落地 |
| --- | --- |
| 受限运行配置 | 容器：白名单挂载 + `--network none` + 环境过滤；本地配置如实标注未隔离 |
| 公共任务包 | `task.json`（公开 brief/关系白名单/工具/预算/协议 + 提交通道合同）+ `README.md`；无 case ID、无期望值、无私有副本 |
| 确定性外部策略示例 | T09 `examples/external_strategy_client.py`、T10 `examples/mcp_client.py`、T11 `examples/isolated_client.py`（沙箱内、经桥接） |
| 接入说明 | 包内 `README.md` + §14.1 + 本文档 |
| 读真值/其他 run/凭据/写库 → 失败且有审计 | `acceptance.json` 逐项记录尝试与结果；容器配置全 DENIED；闭环里的攻击客户端同样逐项执行并留痕 |
| 注入夹具边界仍成立 | 闭环中攻击客户端真实尝试夹具指令（含"报告期望状态"→ 伪造引用）后被拒；含诚实范围声明 |
| Windows 启动 + 至少一种隔离配置 | 两种配置 + 闭环均实跑；容器配置为隔离结论的唯一来源 |
| 演示数据可发布、保留集答案不随包发布 | 公共任务包不含任何私有文件字节副本（校验项 `PRIVATE_FILE_COPY`）；holdout 目录当前为空 |

## 审计整改（第一轮，2026-09-16）

- **公共包检查未阻断验收（P1，已修）**：`task.json` 现做严格结构校验（字段集合必须恰好等于
  `TASK_CONTEXT_FIELDS`，多一个 `incident_case_id` 即 `UNDECLARED_FIELD`）并扫描私有 case id
  （取自 `config/scenarios/*.json` 文件名，任何位置出现即 `PRIVATE_CASE_ID`）；
  `isolation_verified` 现在同时要求公共包 `clean=true`。回归：两条新测试复现审计的两种绕过。
- **NO_DRIVER 算作拒写成功（P2，已修）**：`database_write_denied` 只在驱动存在且真实拒绝时为真；
  `NO_DRIVER` 记为 `database_write_attempted=false` 并阻断 `boundary_held`
  （回归 `test_acceptance_does_not_count_a_missing_driver_as_a_denied_write`）。
- **注入夹具未被读取（P2，已修）**：探针现在读取夹具并记录 `outcome=READ` 与内容摘要，
  `boundary_held` 只在"夹具被真实读取 + 私有平面被拒 + 写尝试真实发生"时成立。

## 审计整改（第二轮，2026-09-17：隔离配置的策略闭环）

审计指出"重放客户端日志后再提交，不能替代取证时的同一 harness 会话"，并给出三条要求；落地如下：

| 审计要求 | 实现 | 回归/证据 |
| --- | --- | --- |
| harness 持有唯一会话，客户端 MCP 请求经传输桥接进入该会话，实时登记证据与计数并保存权威日志 | `strategy_bridge.serve_child_session`：`build_mcp_server` 建在同一会话上，子进程 stdio 与 `Server.run` 之间用消息流对接；`ToolGateway(recorder=…)` 在每次调用后落盘（含被拒调用）；日志目录是沙箱的兄弟目录，客户端不可达 | `tests/unit/test_isolation_loop.py`；`closed_loop_*_harness_audit.jsonl` |
| 独立提交文件只承载答案，harness 严格解析后交给原会话 `submit`；客户端 `audit.jsonl` 仅作辅助比对 | `_load_submission` 用 `FinalSubmission` 严格解析（额外字段整文件拒收），随后 `session.submit`；合规路径逐条比对客户端日志与 harness 日志 | 闭环两条路径；`submission_file`、`client_audit_matches` 检查项 |
| 验收同时覆盖成功与拒绝：伪造引用、删除客户端日志、超预算、重复提交、终态后调用都不能绕过会话约束 | 攻击客户端与 harness 接力执行五类动作，逐项检查 | `test_attack_client_is_refused_at_every_point`、`test_deleting_the_client_log_does_not_hide_what_the_harness_counted` |
| 注入夹具应被真实尝试，而不只是读取后算摘要 | 攻击客户端按夹具指令实际读取私有平面/其他 run/管理凭据，并把"报告期望状态"落成一个伪造引用的提交文件 | `closed_loop_attack_attack_report.json` 的 `privileged_attempts`；容器内四项全 `DENIED` |

## 边界与未做

- 容器内运行的是**确定性客户端**（stdlib，无模型、无外部依赖），只证明边界与会话约束行为；真实模型
  接入的隔离验收需要"在容器内复跑同一桥接客户端"的命令，见包内 README 与 §14.1，但真实模型抗注入
  能力仍未验证。
- 本机 Windows 配置无法提供文件系统边界（同用户进程），报告如实标注；`isolation_verified` 只由
  容器配置与闭环给出。
- 真实 run 的公共任务包需要已存在的归档运行；本轮用合成运行验证导出与校验链路，闭环证据后端同样是
  确定性公开夹具（不含任何私有真值）。
- 改动未提交 git。
