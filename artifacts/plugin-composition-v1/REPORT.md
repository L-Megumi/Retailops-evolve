# 通用插件协议与原子组合验证

## 通用协议

插件不再被硬编码为单一 `transform_refunds`。`PluginSpec` 统一声明：

- `kind`：`refund_transform` 或 `metric_tool`；
- `entrypoint` 与参数列表；
- 输入、输出 Schema 版本；
- 输出指标名称；
- 运行时不变量；
- `order_before`、`order_after` 和 `conflicts`；
- 单插件超时预算。

当前两个适配器分别支持：

```text
refund_transform: transform_refunds(order_totals_cents, refunds) → refund rows
metric_tool:      compute_metric(orders, refunds) → metric/data/evidence
```

通用沙箱根据协议校验函数名和参数，再通过同一个隔离 worker 执行。旧退款插件保持兼容；新指标插件可以向 Agent 注入新的 `ToolResult`，不修改退款数据。

## 组合执行

对于同时要求去重与累计封顶的任务，注册表声明：

```text
refund-event-dedup-v1 → cumulative-refund-cap-v1
```

`PluginManager` 对选中的插件执行冲突检查和拓扑排序。所有步骤在原始输入的本地副本上运行；只有整条流水线成功后才把有效退款和指标结果提交给 Agent。

若后续步骤失败：

1. 隔离失败版本；
2. 激活最近的 retired 稳定版本；
3. 丢弃此前步骤的中间结果；
4. 从原始输入重新执行完整流水线。

没有稳定版本时返回 `plugin_execution_failed`，不提交部分处理结果。

## 验证结果

真实 CLI 输入三条退款：70 元、同 ID 重复 70 元、另一 ID 60 元，订单成交额 100 元。最终执行轨迹为：

```text
refund-event-dedup-v1@1 → cumulative-refund-cap-v1@1
```

去重后为 70 + 60，封顶后为 70 + 30，最终有效退款 100 元。

独立随机 Oracle 使用 40 组不同订单金额、事件数、重复次数和超额退款，外部逻辑先按事件 ID 保留首条，再逐订单消费成交额余额：

| 指标 | 结果 |
|---|---:|
| 随机组合 Oracle | 40/40 |
| 真实 CLI | 通过 |
| 原子回滚故障注入 | 通过 |
| 最终完整测试 | 48 通过、1 跳过 |

## 边界

- 当前只有两个协议适配器，而不是任意 Python 函数接口；
- 组合顺序来自注册表声明，不由模型任意决定；
- 多插件事务是进程内原子提交，不是数据库级分布式事务；
- 资源隔离仍是 AST、隔离子进程和资源限制，不等同于容器或虚拟机。
