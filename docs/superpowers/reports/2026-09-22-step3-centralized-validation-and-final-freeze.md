# 第 3 步集中验证与最终冻结执行(2026-09-22)

- 上游:`2026-09-21-step3-handoff.md` §7 顺序(P-1 收口 → 集中验证 → 最终冻结与检出门
  验证 → 真实模型测量单独放行);P-1 收口见 `2026-09-22-p1-reader-closure.md`
  (提交 `15ee6e4` / `b3ec341`)。
- 本文档作用:集中验证记录 + **冻结执行的判据基线**。冻结各步的预期输出在此预先写明,
  执行结果如实留档于会话输出;worktree 检出门的最终验证发生在清单提交之后,**不能**
  再以新提交记录(否则 `diff 绑定修订..HEAD` 超出清单文件、检出门自毁),其完整数字
  由下一份真实测量报告补记——这是结构性约束,如实声明,不是遗漏。
  **修订(2026-09-23,所有者更正)**:上一段的「清单提交之后不能再以新提交记录」
  **不成立,已撤回**——`_verify_checkout` 检查的是**运行 checkout 自身的 HEAD**
  (`project_root` 的 `rev-parse HEAD`),不是主分支 HEAD。正式运行固定在清单提交
  `91582e5` 的独立干净 worktree 即可;主分支后续提交不改变该历史提交,不破坏其
  检出门,验证结果亦不妨碍提交入库(即本补记)。
- **修订(2026-09-23,执行结果补记与所有者独立确认)**:F1–F6 全部按 §3 判据执行
  通过,详见 §3.1;所有者独立复核支持冻结身份收口(§3.2),真实模型测量仍未放行。
- 纪律:未 push;未改写历史;未重跑失败格;未发起任何模型/网络请求;真实模型测量
  仍等待单独放行。

## 1. 集中验证范围与实现修订语义

自批准提交 `ed04be9`(M24 身份升版)以来的**实现改动**(全部已提交):

| 提交 | 内容 | 性质 |
| --- | --- | --- |
| `fc9cb74` | ProtocolTools 门面补 `lineage_node_candidates` 只读转发 | 实现(已审计) |
| `3109db7` | P-1 归档核心(`RefusalAudit` + 双路径接线) | 实现 |
| `15ee6e4` | P-1 收口(读取端 + 构造端计数修复 + 共用推导函数) | 实现 |
| `b3ec341` 及更早 docs | 报告 | 文档 |

用户裁定(交接 §4):最终冻结须绑定**含全部最终代码与合同变更**的提交。本报告提交后
的 HEAD 即该提交,记为 **X**;冻结绑定 `implementation_revision = X`。
本轮改动均不触 `result_inputs` 面(`evaluator_sha256` 只钉 `evaluation.py` 文件;
`diagnosis_schema_sha256` 只钉 `Diagnosis.model_json_schema()`,P-1 加的纯函数与
trace 类 docstring 均不在该 schema 树内,已实测验证),故重生成清单的逐字段判据
仍是「相对已提交清单**唯一**差异为 `implementation_revision`」。

## 2. 集中验证结果(命令逐字)

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `uv run ruff check .` | `All checks passed!` |
| 2 | `uv run pytest tests/unit -q` | `1114 passed, 5 skipped in 135.26s` |
| 3 | `uv run pytest tests/integration -q` | `48 passed in 2758.76s (0:45:58)` |
| 4 | `uv run pytest tests/e2e -m 'not real_model' -q` | (见下方实测行) |
| 5 | `uv lock --check` | `Resolved 105 packages`(无变更) |
| 6 | `git diff --check` | clean |

注:#3 全绿(已知本机 dbt `0xC0000005` 间歇性不稳定本次未出现;若出现,按既定纪律
单例重跑复核、不据此改产品)。#4 含策略矩阵 38 例(此前已在 P-1 收口时单独跑过
`38 passed in 0:38:35`)。e2e 实测结果:`47 passed, 10 deselected in 4525.48s (1:15:25)`。

另做冻结干跑(只读、不落盘):以 `build_manifest` 在当前树重建清单对象,与工作树
现版比较,除 `implementation_revision` 外**零字段差异**(cells run_id 逐格相同、
policies、result_inputs 逐字相同)——F3 判据提前确认无风险。

环境:PG 容器 55432 运行中;submodule `36bde6c` 已初始化;Docker Desktop 可用。

## 3. 冻结执行(判据预先写明)

执行顺序与每步判据——任何一步不符即停,不静默调整:

**F1 绑定修订** = 本报告的 docs 提交(X)。此后到清单提交之间无其他改动。

**F2 重新生成清单**(官方工具,未手改 JSON):
```powershell
Remove-Item config/benchmark/p1-formal-v31.json   # 已提交内容在 git 内,可恢复
uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v31 --implementation-revision <X> --model deepseek/deepseek-v4.1-flash --output config/benchmark/p1-formal-v31.json
```
判据:exit 0;输出 `sha256: <新摘要>`;`cells: 106; model_backed: 94; fixed_rule: 12`。

**F3 逐字段比对**(相对两个基线):
- `git diff HEAD -- config/benchmark/p1-formal-v31.json`(HEAD = X,清单仍是 `bae3063`
  版):唯一差异行 `implementation_revision: 115299e… → X`。
- 与工作树旧中间版(ed04be9 绑定,sha256 `1f7b2067…`):同样唯一差异 `implementation_revision`。
- `model_configuration`、`budget`(8/8/2/300)、`result_inputs`、`policies`、`cells`
  (106,run_id 逐格相同)逐字不变。

**F4 仅提交清单**:`chore: freeze p1-formal-v31 …`(仅该文件)→ HEAD = Y;
`git diff --name-only X..Y` 恰为 `config/benchmark/p1-formal-v31.json`。

**F5 真实检出门验证**(Y 的独立干净 worktree;`git worktree add`,用毕 remove+prune,
不用 stash):
- worktree 中以真实 `BenchmarkRunner._verify_checkout`(经 `BenchmarkRunner.for_project`,
  `project_root=worktree`)→ 判据 **PASSED**(HEAD==Y 为绑定祖先 + diff 恰清单 + 树干净 +
  `verify_manifest` 过)。
- 因果性复核:同 checkout 把清单文件换回旧绑定版(`git show bae3063:…` 写入)复跑 →
  判据 **FAILED**(`formal checkout contains paths beyond the manifest`,因 `bae3063..Y`
  diff 含报告路径);复核后 `git checkout -- ` 还原。

**F6 主树 verify**(HEAD=Y):
```powershell
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v31.json
```
判据:exit 0,`verified: 17 catalog scenarios; 12 formal scenarios; 106 cells; 94 model-backed`,
sha256 与 F2 一致。

**F7 停点**:冻结收口。preflight/run 属真实模型测量,**等待单独放行**(密钥经 User 作用域
回退、`PYTHONIOENCODING=utf-8` 前置、429 容错规则按计划 §3.2——均不在本轮执行)。

### 3.1 执行结果(2026-09-22 实测,2026-09-23 补记入库)

每步均按上述判据执行,全部通过;任何一步未发生判据外的调整。

| 步 | 实测 |
| --- | --- |
| F1 | 绑定修订 **X = `e5d81d99abbf6803cb3a4351b2f6471ebac57e77`**(本报告提交)。 |
| F2 | 删除旧清单文件后官方 `benchmark freeze` 重新生成:exit 0,`sha256: f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a`,`cells: 106; model_backed: 94; fixed_rule: 12`。 |
| F3 | `git diff HEAD`(HEAD=X,清单仍为 `bae3063` 版)唯一差异行即 `implementation_revision: 115299e… → e5d81d9…`。与 ed04be9 中间版的文件级比对因 Git Bash 与 Windows Python 的 `/tmp` 路径不一致未能执行;该判据由传递性闭合(现版 vs `bae3063` 仅差绑定〔直接验证〕+ 上轮已证 ed04be9 版 vs `bae3063` 仅差绑定 ⇒ 现版 vs ed04be9 版仅差绑定)。 |
| F4 | 清单提交 **Y = `91582e588e90156236040b87c18d0ecc2dccfbd2`**;`git diff --name-only X..Y` 恰为 `config/benchmark/p1-formal-v31.json`。 |
| F5 | Y 的独立干净 worktree(`git worktree add --detach`,porcelain 为空)中以真实 `BenchmarkRunner._verify_checkout`(`for_project`, `project_root=worktree`)→ **`CHECKOUT GATE: PASSED`**。因果复核:同 checkout 置入 `bae3063` 版清单并提交(探测提交,不碰主分支)后复跑 → **`FAILED (formal checkout contains paths beyond the manifest)`**,与 §3.6 记录的旧绑定拒绝码一致。复核后 worktree 已 `remove --force` + `prune`(探测提交不可达,随清理消亡)。注:直接覆写不提交会先撞 `formal benchmark requires a clean checkout`,故按上轮先例以探测提交保持树干净再复跑。 |
| F6 | 主树(HEAD=Y)`benchmark verify`:exit 0,`verified: 17 catalog scenarios; 12 formal scenarios; 106 cells; 94 model-backed`,sha256 `f4196010…` 与 F2 一致。 |

### 3.2 所有者独立确认(2026-09-23)

所有者独立核对并确认:HEAD 为 `91582e5`、清单绑定 `e5d81d9`;两提交之间恰好只改清单
文件;相对 `bae3063` 清单唯一字段变化为 `implementation_revision`;`benchmark verify`
通过且摘要与 `f4196010…2b4c9a` 一致;P-1 离线复核测试 24 passed。全量单测与
integration/e2e 本轮仅核对报告、未重复运行;干净 worktree 检出门实跑采用上述执行记录。
裁定:冻结身份收口,之前的绑定阻塞已消除;真实模型测量仍未放行,后续运行固定
`91582e5` 与完整摘要,继续执行既定停止规则;历史缺陷暴露面检查与 kernel 合同拒绝
复核保留为独立待办。

## 4. 边界与登记待办(沿用,不外推)

1. v29/v30 历史归档的缺陷暴露面离线检查仍是登记待办(P-1 读取端已就绪,预计历史
   I1 事件多为 `AUDIT_ABSENT` 不可判定)。
2. `refusal_review` 无 CLI 入口(库层 API;如审计侧需要,另行裁定)。
3. kernel 合同层拒绝的离线重算属另一通路,未实现,如实记为能力缺口。
4. 阶段 2(429 基础率探针)可选,未执行;按计划 §2 所有者可跳过。
