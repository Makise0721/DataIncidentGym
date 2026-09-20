# p1-formal-v25 阶段 B worktree 准备演练报告（2026-09-20，纯离线）

- 演练依据：所有者 2026-09-20 指令"先做一次纯离线的 worktree 准备演练"。
- 演练目标：证明阶段 B 的执行环境可以在不联网、不动数据库、不跑 preflight 的前提下完全就绪。
- 执行环境：`C:\Users\29913\codex_space\DataIncidentGym-v25-exec`（detached worktree，
  HEAD = `775756e940cfd714f49401231cd59376776f5ef9`，即 v25 冻结提交）。演练后**保留**该 worktree
  作为阶段 B 执行环境；如需重建，按 §2 命令重放即可。

## 1. 演练结果（全部通过）

| # | 项目 | 命令/方式 | 结果 |
| --- | --- | --- | --- |
| D1 | 建立干净检出 | `git worktree add --detach ../DataIncidentGym-v25-exec 775756e…` | HEAD=`775756e`；`git status --porcelain` 为空 |
| D2 | 子模块离线初始化 | 以主工作区本地克隆为源（见 §2 注），pinned 提交比对 | worktree 子模块 HEAD = `36bde6c…` = `775756e` 的 gitlink = 主工作区本地子模块；初始化后 status 仍为空 |
| D3 | Python 环境离线装配 | `uv sync --offline --frozen`（worktree 内） | 全部 105 包自本地缓存装入，无网络；`.venv` 被忽略，status 仍为空 |
| D4 | v25 verify（冻结代码自验） | worktree 内 `uv run --offline data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v25.json` | 通过；sha256 `78ed8319c3310db35865cf3e91d70c9facae726ba8d892c00f0c19c2a6762ff2`；17/12/106/94/12 |
| D5 | 检出门正对照 | worktree 内以冻结代码直接调用 `BenchmarkRunner._verify_checkout(v25 manifest)` | **PASS**（verify 通过 + 干净检出 + HEAD 为 implementation_revision `a798758b…` 后代且 diff 恰为清单路径） |
| D6 | 检出门负对照 | 主工作区同样调用 | **REJECTED**："formal benchmark requires a clean checkout"（未跟踪历史文档所致）——检出门对执行环境判别有效 |

## 2. 复现命令与注记

```powershell
git worktree add --detach C:\Users\29913\codex_space\DataIncidentGym-v25-exec 775756e940cfd714f49401231cd59376776f5ef9
# 子模块（URL 为远程；离线演练改用本地克隆为源，并对该命令显式放行 file 协议）：
git -C <worktree> -c protocol.file.allow=always `
  -c submodule.third_party/jaffle_shop.url=C:\Users\29913\codex_space\DataIncidentGym\third_party\jaffle_shop `
  submodule update --init third_party/jaffle_shop
uv sync --offline --frozen   # 在 worktree 内
```

- 子模块注记：git 的安全默认（`protocol.file.allow`）拒绝子模块走本地 file 传输，首次尝试失败；
  本次按文档化做法对该条命令显式放行，源为本仓库自己的本地克隆，初始化后逐项核对 pinned 提交
  一致。阶段 B 窗口内如重建 worktree，同样走本地克隆即可保持离线；若接受联网，直接
  `git submodule update --init` 亦可达同一提交。
- 检出门注记：主工作区因未跟踪历史文档被拒属预期行为（D6）；阶段 B 一切正式命令
  （preflight/run）只在 worktree 内执行。

## 3. 阶段 B 剩余步骤（均在放行后的执行窗口内）

1. 启动本地隔离实验 PostgreSQL/dbt 环境，`pipeline build` 基线 + F0 指纹核对；
2. worktree 内 v25 verify 复核（同 D4）；
3. 密钥经 PowerShell 进程环境映射（`finally` 恢复原值）；
4. 一次 `benchmark preflight --manifest config/benchmark/p1-formal-v25.json --confirm-sha256 78ed8319…`；
5. 通过后一次 `benchmark run`（同参数）；滚动窗口与恢复失败停止机制在场；
6. 触发停止即保存现场并报告部分结果；M1–M4 照录。

本轮演练未联网、未启动数据库、未创建 cell/ledger、未调用模型。
