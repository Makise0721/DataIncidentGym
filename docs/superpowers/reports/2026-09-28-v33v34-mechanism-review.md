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

## 3. schema 是否挤占决定性取证——未观察到挤占

11 个 B 采 schema 的格逐一与其 A 配对格对齐：**history 计数全部 B≥A**（唯一
例外 seq67 是 transport 中断格，req=1 即死；seq28 B 少一次 lineage 但失败
方式与 A 相同、均为正确弃答，非决定性损失）；lineage 计数除该两格外全部
B=A；请求数 B ≤ A+2；**两臂 36 格无一触及 8/8 预算上限**。B 的 schema 是
"增量"而非"置换"。

## 4. 结论

三个问题均未发现需要修复的产品机制：seq70 是执行率问题（83%），对照失败
机制的改变不产生错误确认，挤占假设被数据否定。按 owner 指令，下一步提出
控制执行顺序的独立确认性测量方案（见同日 plan 文档），暂不改 prompt。
