# v33/v34 配对测量的离线机制复盘（2026-09-28，只读）

- 依据：两臂 36 格严格加载 bundle + 各 run 的公开 `runtime.json`（observable
  relations 白名单）+ trace 重演，在**决策点**重算控制器可见账本面（每工具
  白名单 − 已采、剩余请求/工具额度）。方法与逐格原始输出：
  `codex_space/v33v34-mechanism-review.py`（仓库外，全程只读、零模型调用）。

## 1. B seq70 为什么没采 schema——遗漏，不是取舍

决策点账本面：schema 白名单 `[raw_payments]`、已采 `[]`（未采=目标开放）、
两个假设已注册、剩余 **6 次模型请求 / 4 个工具额度**。提示的全部前置条件
（已接受同关系 profile、schema 未采、工具启用且关系允许、预算充足）都成立，
模型仍直接提交 CONFIRMED，随后 `REQUIRED_EVIDENCE_TYPES_PRESENT` 失败。
**判定：提示未被执行（遗漏），非预算取舍**；B 目标机会格执行率 5/6。与设计
一致（§3 规则是规划指令，控制器不强制）——不构成产品缺陷，作为真实测量的
执行率观察记录。

## 2. 对照组失败机制——1 个改变、1 个同形加核实、1 个完全相同

| 格 | A(v18) 机制 | B(v19) 机制 | 判定 |
| --- | --- | --- | --- |
| seq15 orphan_b | run→**schema 采**→profile→lineage；只声明 WATERMARK → `GAP_DECLARED` | run→profile→**两次 history 边界探针均拒**（收据）→声明两条 RELATION_HISTORY 收据 + WATERMARK，**未采 schema** → `GAP_DECLARED`+`REQUIRED` | **机制改变**：B 走了 history 边界探针路线但漏掉 schema 核对，失败构成不同 |
| seq28 duplicate_b | run→lineage→profile 拒→声明 profile 收据+IDENTITY → `REQUIRED` | run→**schema 采**→profile 拒→同样声明 → `REQUIRED` | 同形失败，B 按新提示多采 schema、无害；均正确弃答 |
| seq32 orphan_b | run→schema→profile→lineage；WATERMARK → `GAP_DECLARED` | run→profile→schema→lineage；**同一声明、同一失败** | **完全相同**（仅 schema/profile 调用顺序互换） |

对照组的 3 个 MODEL_ERROR（seq40/67/71）全部为 `transport=CONNECTION_ERROR`
provider 侧中断，不属于质量机制变化。**关键：B 的额外 schema 采集在对照格
没有产生任何错误确认**（0/0）。

## 3. schema 是否挤占决定性取证——未见硬预算挤占；取证替换不能排除

11 个 B 采 schema 的格逐一与其 A 配对格对齐：**history 计数全部 B≥A**（唯一
例外 seq67 是 transport 中断格，req=1 即死），请求数 B ≤ A+2，**两臂 36 格
无一触及 8/8 预算上限**——这只支持"未见硬预算挤占"。**不能据此否定取证
选择替换**：seq28 的 A 采了 lineage 而 B 没有，B 的 `REQUIRED_EVIDENCE_
TYPES_PRESENT` 恰包含这一缺采；且该格 B 的 profile 调用被拒（schema 调用
发生在任何 profile 被接受之前），新规则"已接受 profile 后核对 schema"的
触发前提在该格并不成立，原稿"按新提示先采 schema"的归因**撤回**。结论
收紧为：无硬预算挤占证据；是否存在取证替换，需要更大样本或过程级观察，
本轮数据不足。

## 4. 结论

Q1（seq70）：提示未执行（遗漏），非预算取舍——执行率 5/6，非产品缺陷。
Q2（对照机制）：seq15 机制改变（B 走 history 探针收据路线、漏 schema）、
seq28 同形失败但采集集不同、seq32 完全相同；B 的额外 schema 采集在对照格
未产生错误确认（0/0）。
Q3（挤占）：**未见硬预算挤占（零触顶、history/lineage 计数未见减少，唯
transport 中断格例外）；取证选择替换不能被排除**（seq28 为待解释样本）。
三个问题均未指向需要修复的产品机制；按 owner 指令提出确认性测量方案 v2
（分块可执行版），暂不改 prompt。
