# v32 preflight 失败报告：模型 key 注入名错误（2026-09-27）

- 授权与执行：按 `docs/superpowers/plans/2026-09-27-v32-measurement-authorization.md`
  （owner 2026-09-27「授权正式测量」，提交 `b7f8fef`）执行前提链与**一次** preflight。
  preflight **FAILED**（13:17:49+0800 回执），按「失败即停」停止；suite 未启动；
  **重新授权前不重试。**
- 唯一失败项：`MODEL_TOOL_STRUCTURED_OUTPUT`，`kind=HTTP_401`，390ms，
  `model_requests=0`（鉴权即拒，无计费消耗），`tool_called=0`。其余 12 项全 PASS
  （PYTHON/UV 0.11.24/DOCKER/COMPOSE_POSTGRES/POSTGRES/DBT_PROFILE/PROFILE 四项/
  MODEL_ENDPOINT/MODEL_PRESENT）。

## 1. 执行前核验链（全绿）

worktree `C:\Users\29913\codex_space\v32-exec-worktree` 固定 `cc1e181`（porcelain 0，
子模块 `36bde6c` 与 gitlink 一致）；清单 sha256 `898747c0…f992` 双侧一致；
`benchmark verify` 通过（17 catalog/12 formal/106 cells/94 model-backed）；
`pipeline build` 指纹 == F0 `e5c7848e…cb18`（relations: 8）；Docker postgres healthy。
装配排障两则（均环境侧、修复后继续）：worktree 需手动初始化子模块；
launcher 需把 worktree `.venv\Scripts` 前置 PATH（dbt 以裸命令解析）。

## 2. 401 归因（执行侧装配失误，如实归属）

launcher 把 **正确的值**（User 作用域 `COMMANDCODE_API_KEY`）注入到了 **错误的名字**
——原样写入环境变量 `COMMANDCODE_API_KEY`。而模型设置以 `env_prefix="DIG_DIAGNOSTIC_"`
读取字段 `model_api_key`（别名 `DIG_DIAGNOSTIC_MODEL_API_KEY` / `MIMO_API_KEY` /
`model_api_key`，默认占位符 `SecretStr("mimo-api-key-required")`）。父进程无任何
`DIG_*` 变量、worktree 无 `.env.diagnostic`（ignored 文件不入 worktree），于是探针以
**占位符默认 key** 访问 commandcode 端点 → 401。端点与模型名由冻结清单接线，均正确。

责任在执行侧环境装配，不在产品、清单或 provider。与 v31 第二次 preflight 的 401
（文件内 mimo key 覆盖 User key）同族不同位：那次是「错值占对位」，本次是
「对值占错名」。两次均 `model_requests=0`，无计费。

## 3. 回执归档与路径清理

失败回执（doctor.json）sha256
`3889965a9bb5956ae88063e9d10d06980ad74ef8ad833e98b8b9a3d3f07b37d3` 双侧一致后归档至
`C:\Users\29913\codex_space\v32-preflight-failed-20260927T1318\`，活动路径已清空
（解除既有「回执存在阻塞再 preflight」机制对下一次授权执行的阻塞）。

## 4. 已完成的离线修复（无产品/清单改动）

launcher（仓库外 `C:\Users\29913\codex_space\v32-launch.py`）已改为
`env["DIG_DIAGNOSTIC_MODEL_API_KEY"] = <User 作用域 COMMANDCODE_API_KEY 值>`，
值源不变、名字对齐设置读取名；六键注入与其余装配不变。

## 5. 所需授权

重新授权**一次** preflight；通过则按原授权执行**一次**完整 suite（滚动停止、恢复
失败即停、无子集 smoke、无补测——原计划其余条款不变）。
