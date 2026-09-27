# p1-formal-v32 身份登记与冻结计划（2026-09-27）

所有者批准的范围是登记并冻结新正式身份，不包括 preflight、数据库运行、真实模型请求、正式 benchmark、旧归档改写或 push。测量另行放行。

## 冻结前只读核对

在 `57ada30` 上，用 `build_manifest` 在内存中按 v31 的 ID、绑定修订与模型配对重建清单。相对已封存的 v31，顶层仅 `result_inputs` 和 `policies` 漂移：evaluator `p1.evaluator.v3` → `p1.evaluator.v5` 及源码摘要改变；五个模型策略的 controller `p1.controller.v20` → `p1.controller.v22` 及政策摘要改变。FIXED_RULE 政策、模型/端点、预算 8/8/2/300、17 个场景目录、12 个正式场景及 106 格排程均不变。旧 v31 在当前实现上因 `result-input hashes drifted` 无法作为新测量身份；历史文件和归档不修改。

## 执行与验收

1. 仅登记 `p1-formal-v32`，把下一未批准身份测试移至 v33；完成受影响单测、Ruff、锁文件及差异检查。含批准记录和最终代码的提交为绑定修订 X。
2. 以官方 `benchmark freeze --manifest-id p1-formal-v32 --model deepseek/deepseek-v4.1-flash --implementation-revision X` 生成新文件，逐字段比较 v31：允许改变 manifest ID、绑定修订、106 个派生 run ID、上述 `result_inputs` 与五项模型政策身份；其余字段必须相同。任何额外漂移即停。
3. 仅提交新清单，得到 Y；验证 `git diff --name-only X..Y` 恰为 `config/benchmark/p1-formal-v32.json`。在 Y 的干净独立 worktree 调用真实 `_verify_checkout`，再运行 `benchmark verify`。记录新摘要与验证结果。
4. 冻结到此停止。正式执行不得在已有未跟踪历史文件的主工作树启动；新运行需另行批准，并从固定在 Y 的干净 worktree 出发。

本身份若执行完整赛程，不能先在同一 suite 上做 `--only-sequence` smoke：该选项会永久标记 subset，使该 suite 无法出正式完整报告。真实运行中的滚动停止、环境恢复停止和部分结果口径保持现有合同，不在本计划中更改。
