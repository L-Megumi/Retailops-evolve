# DeepSeek 真实模型改进实验

实验日期：2026-11-03  
运行环境：`ljh3`、Conda `retail`、Python 3.12.16  
模型：`deepseek-flash`  
改进预算：每个任务 `k=1`，Harness² 顺序模式

## 实验目标

验证模型能否读取经营指标校验器给出的失败证据，修改分析策略并通过重新执行确认修复有效。基础策略故意包含两处常见口径错误：

- 用订单明细行数而不是去重订单数计算退款率；
- 将跨品类订单退款全部归入第一个品类，而不是按订单行销售额比例分摊。

模型只修改两个枚举策略字段。指标计算、业务口径校验和最终验收均由确定性代码完成。

## 结果

| 任务 | 基础策略 | 基础校验 | 最终策略 | 最终校验 | API 请求 | Tokens |
| --- | --- | --- | --- | --- | ---: | ---: |
| `refund-metrics-1` | `order_lines + first_category` | 失败，2 个问题 | `distinct_orders + proportional` | 通过，0 个问题 | 4 | 14,489 |
| `refund-metrics-2` | `order_lines + first_category` | 失败，2 个问题 | `distinct_orders + proportional` | 通过，0 个问题 | 3 | 10,633 |
| 合计 | — | 0/2 通过 | — | 2/2 通过 | 7 | 25,122 |

表中调用量仅统计两次最终成功运行，不包含前期 API 连通测试和故障诊断请求。

两次运行均选择了非基础候选策略，并将修复经验写入 Harness² 经验库。第二个任务使用了不同订单结构和退款金额，修复后退款率与跨品类净销售额同时通过校验。

## 可审计证据

- 任务一：[运行汇总](task1/result.json)、[基础输出](task1/base-answer.json)、[最终输出](task1/final-answer.json)
- 任务二：[运行汇总](task2/result.json)、[基础输出](task2/base-answer.json)、[最终输出](task2/final-answer.json)

服务器保留完整运行目录，包括执行轨迹、候选策略、选择记录、模型审计文件和经验库：

- `runs/deepseek-smoke-20261103-task1-k1-retry3-nothinking`
- `runs/deepseek-validation-20261103-task2-k1`

## 运行中发现并修复的问题

DeepSeek 的 JSON Output 在无法识别模型输出为合法 JSON 时，会以空白字符替换内容，即使 `finish_reason=stop`。初始客户端因此得到空响应。最终实现增加了：

- 空白 JSON 检测与普通文本模式回退；
- JSON、Markdown 代码块和短前导文本的严格对象解析；
- 非思考模式，适配短策略分类任务；
- 枚举白名单，阻止未知策略写入执行脚本；
- 线程安全的请求计数，以及累计 token、终止原因和内容摘要审计。

## 结论边界

本实验说明真实模型能够在两个已知合成任务上，根据验证器反馈完成策略修复并通过确定性复验。它不能证明在开放域、真实企业数据或未见任务上的泛化能力。下一阶段应冻结实现，再生成独立测试集，并报告通过率、平均修复轮数、token 消耗和失败类型。
