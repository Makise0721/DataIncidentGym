# p1-formal-v25 阶段 B 测量执行报告（2026-09-20，预检失败中止）

- 放行依据：所有者 2026-09-20 指令"现在开始"（阶段 B 最终放行；开始时间即本轮）。
- 执行环境：worktree `C:\Users\29913\codex_space\DataIncidentGym-v25-exec`（detached @ `775756e`，
  见 [worktree 演练报告](2026-09-20-v25-worktree-drill.md)）。
- **结论：预检失败，按授权 §3.2"预检失败即停止"中止；benchmark run 未启动，无模型 completion 请求、
  无 cell、无 ledger。** 本报告记录现场与根因诊断（诊断均为只读或目录级 GET，未消耗模型额度）。

## 1. 已完成步骤（全部成功）

| 步骤 | 结果 |
| --- | --- |
| PostgreSQL 环境 | `data-incident-gym-postgres-1` 已运行，`docker compose up -d --wait` → Healthy |
| 健康基线 | worktree 内 `pipeline build` 成功；8 关系；fingerprint `e5c7848e…cb18` == F0 逐字相等 |
| v25 verify 复核 | 通过；sha256 `78ed8319…2ff2`；17/12/106/94/12 |
| 密钥存在性 | `COMMANDCODE_API_KEY` 在进程环境中（值未输出、未写入任何记录）；`DIG_DIAGNOSTIC_MODEL_API_KEY` 按进程前缀映射，会话不残留 |

## 2. 预检结果（唯一一次，未重试）

```
status: FAILED / doctor_status: FAILED / model_probe_required: True / started_cells: 0
receipt: <worktree>/artifacts/benchmarks/p1-formal-v25/doctor.json（保留现场）
```

通过项：PYTHON、DOCKER、COMPOSE_POSTGRES、PROFILE_SPEC、PROFILE_SNAPSHOT、PROFILE_BOUNDS。
失败项：UV、POSTGRES_CONNECTION、DBT_PROFILE_CONNECTION、PROFILE_READ_ONLY、MODEL_ENDPOINT、
MODEL_PRESENT、MODEL_TOOL_STRUCTURED_OUTPUT（doctor 将失败项 observed 统一清洗为 UNAVAILABLE，
doctor.py:200；以下根因来自逐项只读诊断）。

## 3. 三项独立根因（证据充分）

### R1 工具链漂移：uv 0.12.9 ≠ 冻结期望 0.11.24

doctor 以 `_EXPECTED_UV = "0.11.24"` 逐字比对 `uv --version`；本机当前为 `uv 0.12.9`。这是冻结时点
之后的环境漂移，与 v25 身份无关。**环境级可修**（将本机 uv 降回 0.11.24，恢复冻结时工具链；无需
改代码），但属机器级工具链变更，需所有者确认。

### R2 worktree 缺显式诊断库配置（设计内拒绝）

doctor 在 `DIG_DIAGNOSTIC_POSTGRES_*` 环境变量组不全且无 `.env.diagnostic` 时，按设计直接判定
POSTGRES_CONNECTION 失败（doctor.py:622-626），DBT/PROFILE 检查级联失败。主工作区存在未跟踪的
`.env.diagnostic`（含全部键），worktree 没有该文件；直接连接测试证明数据库与 `dig_reader` 均正常。
**环境级可修**：运行 preflight/run 时从主工作区 `.env.diagnostic` 就地 source 到进程环境（值不回显、
不入命令记录），与密钥"进程环境映射"机制一致；授权书禁止的是"写 `.env.diagnostic` 文件"，不冲突，
但需所有者确认此用法。

### R3 doctor 的 urllib 目录探针被 Cloudflare 按 UA 封锁（结构性）

- `_endpoint_check`（doctor.py:521-551）用 urllib 默认 UA（`Python-urllib/3.12`）GET
  `{base_url}/models`；`api.commandcode.ai` 的 Cloudflare WAF 对该 UA 返回 **403 + error 1010**
  （基于浏览器签名的封锁）。
- 对照实验（同一 URL、同一密钥）：UA `Python-urllib/3.12` → 403；UA `OpenAI/Python 1.0.0` → **200**；
  `Mozilla/5.0` → **200**。封锁纯按 User-Agent。
- **OpenAI SDK 真实路径正常**：`client.models.list()` 成功，目录 71 个模型，
  `deepseek/deepseek-v4.1-flash` **在列**；密钥有效、模型可用、额度端点可达。
- 因此 MODEL_ENDPOINT/MODEL_PRESENT 在冻结代码下对本端点**恒失败**（MODEL_TOOL_STRUCTURED_OUTPUT
  为级联失败，探针实际未发出）。这不是环境问题，无法在不改代码的前提下让 preflight 通过。

## 4. 现场与边界

- worktree suite root 仅含 `doctor.json`（FAILED 回执，**按所有者更正永久保留**）；无 ledger、无 cell、
  无 subset 标记；后续测量使用 v26 的独立 suite 目录与新回执，下一次预检属另行授权的新尝试。
- 数据库保持健康基线（F0）；诊断全程只读。
- 模型侧仅发生目录级 GET（urllib×3 + SDK×1），无 completion 请求，不消耗套餐调用额度。

## 5. 裁定与更正（2026-09-20 所有者复核后）

**表述更正（两处）**：

1. **v25 的 FAILED 回执保留原样，不清理、不覆盖。** v26 使用独立 suite 目录
   （`artifacts/benchmarks/p1-formal-v26/`）与新回执；下一次预检属于**另行授权的新尝试**，
   不存在"清理后重试"的路径。本文 §4 与早先 §5 中"清理该回执"的表述作废，以此处为准。
2. **`models.list()` 成功只证明目录访问、鉴权有效与模型在列**；不证明 completion 可用、
   剩余额度充足或目录请求不扣量。这三项应标为**尚未验证**，待获准的 preflight 与测量给出证据。

**所有者裁定（三项修复）**：R3 目录探针改用 OpenAI SDK `models.list()`（同端点/鉴权/网络路径、
保留超时、禁用自动重试、保留模型在列检查）→ 已实现、审计通过并提交，冻结 v26（见
[v26 冻结报告](2026-09-20-v26-freeze.md)与 [doctor 改动审计](2026-09-20-doctor-catalog-probe-audit.md)）；
R1 隔离使用 uv 0.11.24（不降级全机）；R2 dotenv 进程级注入。检出门未修改。
