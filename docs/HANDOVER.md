# DataIncidentGym 交接文档

> 写作时间：2026-09-07。读者：下一个接手本项目的操作者/代理。
> 权威顺序：`docs/requirements.md`（需求，Windows 工作区材料）> 本文档（操作现状）> `decision.md`（逐轮决策日志）> `README.md`（面向外部的项目描述）。

## 1. 项目一句话与最终状态

DataIncidentGym 是本地单用户 CLI：在真实 PostgreSQL + dbt（Jaffle Shop）上确定性注入数据事故，让受限 Agent 只用只读工具调查，由确定性 evaluator 按冻结合同评分。P1 全部 17 场景已建成；正式基准批次已执行并结案。

**当前正式结论：`INVALID` / 模型质量 `NOT_ESTABLISHED`。** 不存在任何有效的准确率、Kernel 优势或生产可用性结论。措辞红线：不得把任何批次数字（包括 100%/87.5%）表述为准确率、普遍结论或策略优越性。

## 2. 位置地图

| 位置 | 内容 |
|---|---|
| `C:\Users\29913\codex_space\DataIncidentGym`（Windows） | 主工作区，`main`=`df22569`，与远端一致。未跟踪材料：`AGENTS.md`、`decision.md`、`docs/requirements.md`、`docs/superpowers/`（含全部计划文档） |
| `\\wsl.localhost\Ubuntu\home\makise\codex_space\DataIncidentGym`（WSL） | 执行主 checkout，已同步 `main`=`df22569`，干净。操作一律走 `wsl.exe -d Ubuntu -- bash -lc '...'` |
| WSL `…-smoke-v3/v4/v5/v6/v7/v8` | 六个 smoke 批次 worktree（各含证据与日志），分支 `codex/benchmark-smoke-v*` 已推送 |
| WSL `…-formal-v2` / `…-formal-v9` | 两次正式批次 worktree（含归档/结案记录），分支已推送 |
| WSL `…-relation-policy` | **另一会话创建**（`codex/kernel-relation-policy`@4ac9779），本项目主链未使用，勿动 |
| Windows 隔离 worktree `C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\benchmark-rerun-enablement` | 历史遗留（Task 1–4 初版），只读保留 |
| Windows `C:\Users\29913\codex_space\DataIncidentGym\decision.md` | 1579 行起为 Task 5–7 / Kernel 修订 / 正式批次逐轮记录 |

## 3. Git/CI 状态（2026-09-07 收尾时）

- `main` = `df22569`（`fix: validate kernel state before reporter access`），main CI `34120037221` 全绿（unit 373 / integration 34 / e2e 47）。
- 远端分支（全部保留，不删）：`codex/benchmark-smoke-v3/v5/v6/v7/v8`、`codex/kernel-contract-v6/v7/v8`、`codex/kernel-relation-policy`、`codex/model-mimo-v2-5-pro`、`codex/formal-v9-hardening`、`codex/benchmark-formal-v2`、`codex/benchmark-formal-v9`、`codex/formal-v9-final`、`codex/benchmark-rerun-enablement`。
- 本地分支（Windows 侧）：`codex/benchmark-smoke-v4`（未推送，按当时授权边界）、`codex/formal-v9-final` 等。

## 4. Manifest 身份台账（全部已消耗，冻结后永不复用）

| 身份 | 用途 | 结果 |
|---|---|---|
| `p1-formal-v1` | 第一次正式 106 格 | `INVALID_HARNESS`（历史封存） |
| `p1-formal-v2` | 第二次正式 106 格（修复后） | 32/106 fail-stop（seq32 diagnosis 逃逸）→ `INVALID`，已修复 |
| `p1-formal-v3` | 12 格 FIXED_RULE smoke | 11 PASSED / 1 质量失败；harness 干净 |
| `p1-formal-v4` | 8 格真实模型 smoke | Kernel 工具成功率 68.97%，0/4 终态 |
| `p1-formal-v5` | v6 绑定参数化验证 | 75.00%，1/4 |
| `p1-formal-v6` | v7 provenance 白名单验证 | 66.67%，2/4；**发现 kernel 门与工具层 allowlist 合同错位** |
| `p1-formal-v7` | v8 同源化验证 | 87.50%，0/4；关系纪律错误归零 |
| `p1-formal-v8` | mimo-v2.5-pro 判定门重测 | **100%，2/4 —— 判定门首次同时达标**，解锁 Task 7 |
| `p1-formal-v9` | 正式批次（第二Attempt + 用户豁免恢复） | 86/106 fail-stop → 恢复至 **106/106 终态**；结论 **`INVALID`**（seq86 环境硬门） |
| `p1-formal-v10+` | **未使用**——未来正式批次从这里开始（需先在 `APPROVED_MANIFEST_IDS` 追加批准并走 CI） |

判定门定义（对 8 格 subset smoke）：Kernel 工具成功率 ≥80% **且** ≥2/4 格非 `MODEL_ERROR` 终态。v8 已达标（100% + 2/4）。

## 5. 当前实现要点（main=`df22569`）

- 模型：`mimo-v2.5-pro` @ `https://api.xiaomimimo.com/v1`（M5.3 需求修订，2026-09-06）。密钥在 Windows 用户环境 `MIMO_API_KEY`，经 `WSLENV` 以 `DIG_DIAGNOSTIC_MODEL_API_KEY` 传入 WSL（不落盘）。
- Kernel 契约：`p1.kernel.v8` + `p1.controller.v7`。三轮修订已落地并实跑验证：绑定参数化传输（无并行意图信封）、单响应多业务调用、账本/工具描述注入 `provable_relations` 白名单（与工具层 allowlist 同源）、关系拒绝镜像工具层语义（`RELATION_NOT_ALLOWED` + BLOCKED gap 镜像）。
- `diagnose()` 顶层 fail-closed（`eb01a68`）：构造期/teardown 异常转化为安全 `MODEL_RUNTIME_ERROR` 终态（Kernel 策略合成零用量 `InvestigationState`）。经 v9 批次实跑验证（越过 v2 的失效点 54 格）。
- reporter 修复（`df22569`）：`KernelStateTraceEvent.state` 需 `InvestigationState.model_validate` 后访问（该字段是 `Any`）。
- 预算 8/8/2/300、evaluator `p1.evaluator.v2`、六文件产物合同、四态结论规则（`KERNEL_ADVANTAGE`/`TRADEOFF`/`NOT_PROVEN`/`INVALID`）——**从未改动**。

## 6. seq86 根因更正与当前 standing 约束（2026-09-07）

1. **seq86 根因已更正**：v9 批次唯一硬门失败格 sequence 86（`schema_type_change_order_customer_a` / KERNEL_NO_SCHEMA，`ENVIRONMENT_VERIFIED` expected=RUN_SETUP_COMPLETE / actual=BUILD_FAILED）的根因是**宿主硬件故障导致 dbt 子进程崩溃**，而非此前记录的「故障注入未生效」。当时观察到的「构建 PASS=25/ERROR=0 但注入类型变更未反映」是崩溃的现场表征，不是 inject 路径缺陷；此前「特定顺序上下文 inject 问题 / 低频瞬态、需 83→86 离线重放取证」的解读已推翻。该硬件故障**现已解决**。
2. **恢复段语义**：用户豁免一次性纪律后从 86 格恢复跑完剩余 20 格，**只补齐了终态数量（106/106），并未消除 seq86 这一格的硬门失败**。reporter 按冻结规则（环境硬门失败 → 整批无效）判为 **INVALID** 是正确行为，结论不变。
3. **standing 约束**：**后续不再运行任何真实模型，包括 doctor 模型探针**。因此不会再启动 v10+ 正式批次（正式批次含 94 个 model-backed 格，必然触发真实模型请求）；正式评测线封闭在 INVALID / NOT_ESTABLISHED，剩余工作为文档与留痕整理。
4. **宿主硬件偶发不稳定**：WHEA APIC 41 parity error 历史 + 纯 Python segfault 复现记录（见 `decision.md` 与 `.dig/crash-debug-20260904`）；两次正式批次约每 50–86 格一次的偶发异常即源于此，现已解决。

## 7. 环境已知坑（操作层，全部踩过）

1. **WSL 无法直连 GitHub**：`github.com` 被本机加速器（Steam++/Watt Toolkit，Windows 0.0.0.0:443）劫持解析到 127.0.0.1。push 用 bundle 通道：WSL `git bundle create /mnt/c/... <range>` → Windows `git fetch <bundle> 'refs/heads/X:refs/heads/X'` → `git push`。
2. **wsl.exe 传参陷阱**：分号链中内联 `$?` 会被外层 shell 提前展开为 0（假绿）；bash 变量在单命令形式下被吃掉；heredoc 里的反引号会被执行。对策：验证一律用日志 grep 或 Git Bash 外层退出码；复杂脚本用 `python3 - <<EOF` 且内容不含反引号/美元号，或用 Edit 工具直接改 UNC 路径文件。
3. **Docker Desktop 空闲回收**会在工具调用间隙干净关闭容器（exit 0）→ 长流程前 `setsid sleep 43200 &` keep-alive + 每阶段 `docker compose up -d --wait postgres`。
4. **doctor 需要 `.env.diagnostic`**（13 检查安全门）：worktree 必须从 Windows 复制（mode 600），否则连接族检查按设计固定 UNAVAILABLE。
5. **submodule 克隆**：WSL 内网络克隆失败，用本地路径 `git clone /home/makise/codex_space/DataIncidentGym/third_party/jaffle_shop` 再 checkout `36bde6c`。
6. **/tmp 跨 WSL 关机丢失**：日志放工作区 `.dig/`。
7. **detached HEAD 陷阱**：在 detached 状态 commit 会脱离分支（v9 最终证据曾落游离 HEAD，靠 rebase 挽救）。任何 worktree 提交前先 `git branch --show-current`。
8. **坏的 checkpoint ref**：`refs/codex/turn-diffs/...` 曾指向 tree 对象阻塞一切 fetch，两侧各删过一次；若再现，删除指向不可达对象的非法 ref 即可。
9. **geometric-repack 失败**：fetch 加 `-c gc.auto=0 -c maintenance.auto=false`。

## 8. 标准操作卡

**新 smoke/正式批次（从零）**：
```bash
# 1. worktree（从 main CI 绿的 SHA）
git worktree add -b codex/benchmark-<name> /home/makise/codex_space/DataIncidentGym-<name> <SHA>
cd …-<name> && git clone -q …/DataIncidentGym/third_party/jaffle_shop third_party/jaffle_shop
git -C third_party/jaffle_shop checkout -q 36bde6cba69d962b83be1d52fc65a0dce1cb4ebb
uv sync --frozen
cp /home/makise/codex_space/DataIncidentGym-smoke-v5/.env.diagnostic .env.diagnostic && chmod 600 .env.diagnostic
mkdir -p .dig/<name>
# 2. 基线（fingerprint 必须为 e5c7848e…b18 才与历史可比）
docker compose up -d --wait postgres && uv run data-incident-gym pipeline build
# 3. freeze → verify → 包装提交（仅 Manifest 文件，checkout 必须干净）→ preflight → run
# 4. 完成后顺序：report（必须在 archive 前）→ archive（独占一次）→ 审计 → 证据提交
```

**关键命令**（SHA/manifest 按实际替换；`--only-strategy fixed-rule` / `--only-sequence N` 为 subset smoke；正式批次不带 selector）：
```bash
uv run data-incident-gym benchmark freeze --manifest-id p1-formal-vN --implementation-revision <40hex> --output config/benchmark/p1-formal-vN.json
uv run data-incident-gym benchmark verify   --manifest config/benchmark/p1-formal-vN.json
uv run data-incident-gym benchmark preflight --manifest … --confirm-sha256 <64hex>
uv run data-incident-gym benchmark run      --manifest … --confirm-sha256 <64hex>
uv run data-incident-gym benchmark report   --manifest … --confirm-sha256 <64hex>   # subset 会拒绝
uv run data-incident-gym benchmark archive  --manifest … --confirm-sha256 <64hex>   # 独占一次
```

**恢复语义（已实跑验证）**：fail-stop/中断后，同目录同 Manifest 同命令重跑即跳过已终态格继续；但 checkout 相对实现修订**只允许含 Manifest 一个文件**——证据提交后需先 `git checkout <包装提交>`（detached）再恢复；归档命令目标已存在会 fail-closed，不能重跑。

**判定门审计**：独立审计代理复算 `_source_aggregate`（字节正确 NUL 分隔、按 repo 相对 posix 路径排序、终态 run 去重），核对 ledger 对、六文件、receipt 五元绑定、文件位置合同。

## 9. 纪律与红线（继承自需求与历轮决策）

- 措辞红线：INVALID/NOT_ESTABLISHED 状态下禁止任何"成功/优势/准确率"表述；subset 数字禁止外推。
- 预算 8/8/2/300、evaluator、finalize 门、场景合同从未改动；改动需先修订 `docs/requirements.md` 并获批准。
- 密钥不落盘、不打印；`.env.diagnostic` mode 600；`.dig/`、`artifacts/`、`reports/`（部分已按授权转为跟踪证据）不入库的边界见 `.gitignore`。
- 真实模型请求 = 花费；每次批次/探针数量在执行前报数、执行后对账（历轮均如此记录于 decision.md）。

## 10. 快速背景阅读顺序（新会话 15 分钟入门）

1. 本文档全文。
2. `decision.md` 末 6 节（2026-09-05 起的 Kernel 修订与正式批次记录）。
3. `docs/superpowers/plans/2026-09-03-benchmark-rerun-enablement.md` 的 Task 5–7 与"checkout 组织修正"节。
4. `docs/superpowers/plans/2026-09-05-kernel-contract-revision.md` 与 `2026-09-06-kernel-v7-provenance.md` 的"执行状态"节。
5. `…-formal-v9/reports/benchmark/p1-formal-v9/formal-run-record.md` 与根 `RESULTS.md`（在 formal-v9 worktree）。
