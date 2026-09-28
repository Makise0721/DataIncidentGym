# v33/v34 配对测量执行报告：B 臂完整，A 臂 preflight 停止（2026-09-28）

- 授权：owner 2026-09-28「放行：B(v34)→A(v33)，各一次 preflight/run，共最多
  36 格，保留滚动暂停与失败即停规则」（方案
  `2026-09-27-kernel-v18-v19-paired-measurement-proposal.md` §8 第二道）。
- 执行序：B 整臂 → A 整臂；selector 两臂相同且固定
  （10,11,14,15,18,23,25,28,29,32,33,40,50,55,66,67,70,71）。
- **结论：B（v34，v19 候选）18/18 完整终态、无停止事件；A（v33，v18 对照）
  preflight FAILED（provider 探针 TIMEOUT），按 §6「任一臂 preflight 失败 →
  整个实验停止」停机。A 的 run 未启动。无重试、无补样本。**

## 1. B 臂（p1-formal-v34，v19）——完整执行

- 执行位置：`v34-verify-worktree` @ Y_B=`0dc1572`（porcelain 0，子模块
  `36bde6c`，uv 0.11.24）；前提链全绿（清单 sha256 `d57aa312…`、`pipeline
  build` 指纹 == F0、verify 通过）；preflight **PASSED**（回执 sha256
  `e899b119a5a3fecd8a7e2b7f83a8de5596a5aaee6d25190608126979bcd9fecd`）。
- 终态（逐字）：`status: FAILED / cells: 18/18 / subset: True /
  stop_reason: NONE / EXIT_CODE=1 / DURATION_SECONDS=2392`
  （11:19:52→11:59:19 +08:00，约 39.9 分钟）。subset=True 属 development
  smoke subset，按方案只作配对分析、不称正式完整报告。
- 结果：**11 COMPLETED / 7 FAILED**（7 个失败全部 `EVALUATION_FAILED`，
  零 RUN_SETUP/恢复/环境失败）；`GATE_INTERNAL_ERROR = 0`（ledger 全文扫描）。
  ledger sha256（前缀）`9f32a997859279d27726`。

## 2. A 臂（p1-formal-v33，v18 对照）——preflight 停止

- 执行位置：`v33-arm-worktree` @ Y_A=`3b97353`（porcelain 0，子模块
  `36bde6c`，uv 0.11.24）；臂间复核通过（F0 逐字相等、Docker healthy、清单
  sha256 `5428f8ef…`、verify EXIT_CODE=0）。
- preflight 一次执行 **FAILED**（12:03:11+08:00，EXIT_CODE=1）：唯一失败项
  `MODEL_TOOL_STRUCTURED_OUTPUT`，`kind=TIMEOUT`，60 秒上限，
  `elapsed_ms=60000`，**`model_requests=1`、`tool_called=1`、
  `validator_rejections=0`**——请求已发出、工具已被执行、探针在 60 秒窗口内
  未完成。其余检查全部 PASS。与 v32 时代的 401（装配错 key、零请求）不同：
  本次装配与 B 臂完全一致（同一 launcher、同一 key 注入、B 臂同日 40 分钟前
  全绿），失败形态指向 provider 侧延迟/超时，但按合同如实记录为
  TIMEOUT，不做根因断言。
- 停机动作：回执 sha256
  `1f92fd11320117b28331f6de8c84956780624a70c4498f18b966325338b85fe6`
  双侧一致后归档至 `codex_space/v33-preflight-failed-20260928T1203/`，活动
  路径已腾空（解除对下一次授权 preflight 的阻塞）。A 的 run 未启动，无任何
  cell 产生；A 臂 ledger 不存在。

## 3. 用量与状态

- 真实模型请求：B 臂 18 格（额度内，逐格 8 上限未超）；A 臂 preflight 探针
  1 次请求（已计费面），无其它。总额度 292 的口径下本阶段用量远未触及。
- B 臂 74→18 格的产物、ledger、scoring-inputs 全部保留在
  `v34-verify-worktree/artifacts/` 供分析；A 臂现场（归档回执 + 空路径）
  保留。

## 4. 待所有者裁定

按 §6 停止规则本实验停止，B 臂数据有效且完整（18/18），A 臂对照缺失。可选
路径（均需新的授权）：

1. **A 臂重新授权一次 preflight**（环境未变的干净路径已腾空；通过则按原
   授权跑 A 臂 run 一次，补全 18 对）；
2. 或接受 **B 单臂部分结果**，按 §7 只报 B 观察、不构成 A/B 配对结论；
3. 或整体放弃本轮筛选。

在获得新授权前，不重试、不补跑、不触碰 B 臂产物。
