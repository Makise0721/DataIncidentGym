# T12 对照实验 v2 准入身份冻结前审计（2026-09-29）

状态：第一道放行的代码与准入证据已核对，待以本报告所在提交为绑定修订冻结 `p1-planner-compare-v2`。本报告不放行真实模型 preflight 或测量。

## 准入原件与公开证明

正式赛程的 12 个场景经同一次确定性 `certify --admit` 完成：`certified=12/12`、`admitted=12/12`、命令退出码 0。原件保留在认证工作树的忽略目录 `artifacts/admissions/planner-compare-v2-formal12.json`，SHA-256 为 `aeced2c345432489eab98099d71d2dd97a61f622b8030c0b1e2c7d2506b34266`；认证后数据库指纹与健康基线相等。未调用真实模型。

独立重载原件后，正式 ID 及顺序、12 个唯一 run ID、证书与准入合同摘要、dev 登记、空拒绝原因均成立；用各证书重新执行 `build_admission`，12 条结果与原件逐项相等。跟踪的脱敏证明 `config/benchmark/p1-planner-compare-v2-admission.json` 只有 schema 版本、原件 SHA 和每条的 `(case_id, scenario_digest, admission_entry_sha256)`。另按原件规范 JSON 独立重算，12/12 条目摘要和原件 SHA 与证明一致；证明规范摘要为 `83e75ed0d1520a2b23cadd15bb7bb08a2d51701a549cec5c51a97661cb4a5d82`。私有卡片、答案、证据轨迹和原始报告均未进入提交。

## 代码审计结论

`e9974f7` 增加独立的 v2 manifest schema/ID 与证明摘要。原件读取拒绝重复 JSON 键、非有限常量、额外字段、不安全路径、非忽略文件、非正式场景、过期摘要和与当前合同重建不一致的准入；证明加载要求规范 JSON 且文件受 Git 跟踪。v2 freeze 强制提供原始报告并与证明逐项比较；clean checkout 中的 verify 只依赖已跟踪证明、当前场景合同和 dev 登记。直接构造的 runner 在 preflight/run 前同样复核 v2 证明，报告在派生指标前复核身份。v1 schema 与 v2 ID 的混搭由模型及 runner 双层拒绝。

独立在内存中构造 v2 并运行 `verify_experiment_manifest`：108 格成立；与原 v1 比较，模型配对、预算、六文件、场景目录、正式 12 场景、结果输入、三策略政策面和赛程 `(sequence, case, strategy, repeat, model_backed)` 逐项相同。v1 manifest 文件仍为原字节，SHA-256 `e4a09d16dd15b5b4433b5a25d1e5b8544a4b8c341cb97b793853fcd1d0378826`，`experiment verify` 通过。

## 验证与边界

v2 相关定向测试 50 项通过；完整单测为 1230 passed / 5 skipped / 3 failed。三个失败均位于 `tests/unit/test_experiment_runner_regression.py`，旧夹具未提供既有 planner compatibility probe factory；相同三个 node ID 与异常已在独立干净的 `a493e2c` 基线上逐项复现，也在主树该基准复现，不是 v2 变更引入。全仓 Ruff、`git diff --check` 和 v1 verify 通过。冻结前再检查锁文件与提交范围。

下一步：将本报告作为最终代码与合同修订 X 的一部分提交；在 X 的独立干净工作树中用官方 `experiment freeze` 生成 v2 清单，显式传入上述私有原件；仅清单文件形成 Y。Y 必须通过 v1/v2 verify、108 格与证明绑定、以及真实 `_verify_checkout`，且 `diff X..Y` 恰为 v2 清单路径。若任一检查失败，不进入第二道真实执行放行。
