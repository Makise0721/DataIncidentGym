# p1-formal-v26 第二次预检失败中止报告（2026-09-20）

- 放行依据：所有者 2026-09-20"授权现在开始"。执行序列按 [v26 就绪报告](2026-09-20-v26-freeze.md) §5。
- **结论：第二次预检（v26）失败，按"预检失败即停止"中止；benchmark run 未启动；v26 回执原样保留。**

## 1. 环境复核（全部通过）

postgres healthy；worktree `cc157f9` 干净；v26 suite 目录在预检前不存在；基线重建
fingerprint == F0 逐字相等；v26 verify 通过（`7062a6ad…`）。
执行组装修正一处：launcher 除隔离 uv 0.11.24 外，将 worktree `.venv\Scripts` 前置于子进程 PATH
（裸 shell 不含 venv，doctor 的 `dbt debug` 子进程否则解析不到 dbt）——doctor 层实测解析：
`uv` = 0.11.24，`dbt` = Core（worktree 钉住版本）。

## 2. 第二次预检结果（唯一一次，未重试）

- **通过（10 项）**：PYTHON 3.12.10、**UV 0.11.24**（R1 修复生效）、DOCKER、COMPOSE_POSTGRES、
  **POSTGRES_CONNECTION CONNECTED**、**DBT_PROFILE_CONNECTION CONNECTED**（R2 修复生效）、
  PROFILE_SPEC、PROFILE_SNAPSHOT、**PROFILE_READ_ONLY READ_ONLY_AND_MATCHED**、PROFILE_BOUNDS。
  ——上一轮的环境面失败已全部消除。
- **失败（模型面 3 项）**：MODEL_ENDPOINT、MODEL_PRESENT、MODEL_TOOL_STRUCTURED_OUTPUT
  （级联；结构化探针未实际发出）。
- 回执：`<worktree>/artifacts/benchmarks/p1-formal-v26/doctor.json`（**保留，不清理不覆盖**）。

## 3. 模型面失败的诊断（只读，未重跑预检）

按"同一路径逐层复现"：

1. doctor 同款异步 SDK 调用（OpenAIProvider + `max_retries=0` + `timeout=5`）：**成功**，2.55 秒，
   71 个模型，deepseek 在列；
2. 从主工作区误置 `.env.diagnostic`（含 ollama 旧配置）的首个复现无效，已弃用；
3. **精确预检条件复现**（launcher 环境 + worktree src + v26 manifest 绑定 + `for_project` 接线 +
   `asyncio.run` 内 `await runner._models_list()`）：**成功**，71 个模型，deepseek 在列。

三个成功样本与预检内失败的差异只剩：预检进程内先执行了 10 项环境/profile 检查后再发目录 GET；
以及网络瞬时因素（`max_retries=0` 设计下，任何一次瞬时 Cloudflare 5xx / 超过 5 秒的延迟尖峰即失败，
无重试缓冲）。**当前证据不足以归因**：既不能证明代码缺陷（同条件独立复现稳定成功），
也不能证明瞬时（仅一次预检数据点）。不做无证据的归因表述。

## 4. 待所有者裁定

预检纪律与回执保留原则下，下一次尝试需要新授权，二选一：

1. **（推荐）授权移除 v26 的 FAILED 回执后重试一次预检**：manifest 身份经精确条件复现证明可工作，
   失败疑点指向瞬时因素；一次重试是最廉价的判别器——若复现，则获得稳定复现上下文，可离线二分。
   回执移除前先归档副本（移入 suite 目录内 `failed-attempts/` 或由所有者指定），保留审计链。
2. **另冻 v27**：与 v25→v26 先例一致，但今天两次冻结已证身份机制工作正常，此路径成本高而无额外收益。

可选加固（若所有者希望先消除 5 秒敏感度，需改代码 + 审计 + 冻结）：目录 GET 超时单独放宽
（如 15 秒；仍无重试，请求次数上界不变）。

## 5. 边界

- 本轮模型面请求：预检目录 GET 若干次（含诊断复现 3 次，均目录级）+ 结构化探针 0 次（未到达）；
  completion 请求 0 次；套餐扣量未知（按既定"目录扣量未验证"口径）。
- 数据库保持健康基线 F0；无 ledger、无 cell；未 push。
