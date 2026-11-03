# 从失败 Trace 到新指标插件：受约束 RSI 尝试

## RSI 闭环

本实验不预先把第三插件写入注册表。任务要求计算 `refund_replay_rate`，初始 Agent 只有三个固定分析工具和两个退款转换插件，因此 `required_results` 校验产生：

```text
unsupported_capability: 任务要求的结果 refund_replay_rate 当前没有可执行工具或插件
```

系统随后执行：

```text
失败 Trace
→ 能力缺口提案
→ PluginSpec(metric_tool)
→ 代码生成
→ AST critic
→ evolution / validation 功能门禁
→ 路由门禁
→ 冻结 audit
→ 激活与重跑
```

DeepSeek 提出的新能力是退款事件重放率：相同事件 ID 首次出现后的行记为 replay，`duplicate_rows / total_refund_rows` 以整数基点输出，同时保留两个计数证据。

## 基准

`metric-rsi-v1` 使用固定种子生成 36 项任务：evolution、validation、audit 各 12 项。每项包含不同退款事件数、非相邻重复、0–2 次重放和零重复案例；Oracle 与任务目录隔离。

初始 Agent：

| 划分 | 通过 |
|---|---:|
| evolution | 0/12 |
| validation | 0/12 |
| audit | 0/12 |

## 代码自修复

| 代次 | 静态门禁 | evolution | validation |
|---|---|---:|---:|
| attempt 1 | 拒绝：`disallowed syntax: Is` | 0/12 | 未打开 |
| attempt 2 | 通过 | 12/12 | 12/12 |

首代使用了安全 AST 未允许的 `is` 语法；critic 将精确错误反馈给模型。第二代改为允许的比较方式，未放宽沙箱，并生成不同接口的 `compute_metric(orders, refunds)`。

## 路由自修复

冻结代码首次接入三插件路由时，evolution 为 9/12、validation 为 12/12。三个英文指标任务被旧去重插件的关键词确定性抢先命中，导致缺少指标结果。

系统由此增加“结果覆盖感知路由”：若确定性候选不能产生任务声明的 required result，则重新进行完整能力选择。为降低 reasoner 逐题延迟，又加入批量路由缓存预热：一次响应严格返回一个 split 内 12 个任务的插件 ID，校验任务 ID、顺序、插件 ID 和重复项后写入注册表指纹缓存，再由真实 Agent 逐项执行。

最终结果：

| 指标 | 结果 |
|---|---:|
| 路由 evolution | 12/12 |
| 路由 validation | 12/12 |
| 首次冻结 audit | 12/12 |
| 批量续跑模型调用 | 3 |
| 批量续跑 Tokens | 7,325 |

包含最初能力提案、两代代码和第一次逐题路由尝试，成功实验链累计 27 次请求、36,690 Tokens。另有 DeepSeek flash/chat 的多次 503 和一次读取超时，这些失败没有产生候选或打开 audit。

## 激活前回归

模型最初提出的 cue 包含过宽的 `refund_id`。跨能力门禁发现其会碰撞原去重任务，因此正式注册前删除单字段 cue，只保留重放率和分子/分母组合短语。

| 回归项 | 结果 |
|---|---:|
| 原 `rsi-v2` 任务 | 168 项，确定性碰撞 0 |
| 多插件 v1–v3 盲测 | 碰撞 0 |
| 正式 CLI | 3 行中识别 1 行重放，3333 bps |
| 全量单元测试 | 49 运行，48 通过，1 可选依赖跳过 |

## 可以声称的结论

> 实现受约束的能力级自改进闭环：Agent 从缺失结果失败 Trace 提出新的 metric_tool 规格，首代代码经 AST critic 拒绝后自修复，第二代通过 24 项开发门禁及 12 项冻结审计；进一步根据路由失败引入结果覆盖感知和批量缓存预热，最终将新指标插件安全激活且原 168 项任务零确定性碰撞。

## 不能声称的结论

这不是开放式 RSI：

- `required_results` 和允许的两类协议由 Harness 预先限定；
- Oracle、预算、安全策略和激活门槛由开发者提供；
- 模型没有修改自身权重，也不能任意修改整个代码库；
- 新能力仍处于零售退款这一领域；
- 自动激活前仍经过确定性跨能力安全门禁。

准确表述应为“bounded harness-level capability RSI”或“受约束的能力级自改进”，而不是递归自我改进通用智能。
