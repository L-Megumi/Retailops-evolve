# RetailOps RSI Benchmark v2

## 结论

`rsi-v2` 已建立可用于下一阶段受约束自改进实验的能力缺口，但当前结果本身不构成 RSI 证明。

- 基准共 168 项：40 项回归、48 项演化、40 项验证、40 项迁移；
- 120 项属于现有三字段策略空间，48 项要求扩展可执行策略代码；
- 当前反馈修复在旧能力上通过 120/120，在能力扩展上通过 0/48；
- 独立 Oracle 重算通过 168/168，说明扩展任务有确定且可验证的解；
- DeepSeek 受限策略基线在 48 项能力扩展任务上通过 0/48，API 错误为 0。

因此，下一阶段可以测量“系统是否从演化集的失败中形成持久策略，并迁移到未见任务”，而不再被原有 40/40 的准确率天花板限制。

## 数据划分

| 划分 | 现有策略任务 | 能力扩展任务 | 合计 | 用途 |
|---|---:|---:|---:|---|
| regression | 40 | 0 | 40 | 防止旧能力退化 |
| evolution | 32 | 16 | 48 | 允许查看失败证据并产生候选修改 |
| validation | 24 | 16 | 40 | 候选准入，只返回评测结果 |
| transfer | 24 | 16 | 40 | 检查跨措辞与跨数据实例泛化 |
| 总计 | 120 | 48 | 168 |  |

能力扩展包含四类，每类 12 项：

1. 跨品类退款按品类数等额分摊；
2. 跨品类退款按商品数量加权分摊；
3. 按 `refund_id` 对重复采集退款去重；
4. 根据退款与订单的间隔天数逐笔选择归属日期。

这些规则不能由当前 `ExecutionPolicy` 的三个二值字段表达，需要候选系统修改并持久化执行逻辑。

## 防泄漏结构

候选输入位于 `benchmarks/retailops/rsi-v2/tasks/<split>/<case>/`，只包含：

- `task.json`：自然语言任务和允许调用的工具；
- `orders.csv`；
- `refunds.csv`。

合同与预期结果位于独立的 `benchmarks/retailops/rsi-v2/oracle/`。候选进程只能挂载单个任务目录，不应获得 suite 根目录或 Oracle 路径。`manifest.json` 保存 504 个候选可见文件和 168 个 Oracle 文件的 SHA-256。

完整性检查同时验证：

- 候选任务中不存在 `metric_contract` 和 `expected.json`；
- 所有文件哈希匹配；
- Oracle 能从原始 CSV 和私有合同重新计算；
- manifest、任务与 Oracle 的身份一一对应。

## 实际结果

| 策略 | 全部 168 项 | 现有能力 120 项 | 能力扩展 48 项 |
|---|---:|---:|---:|
| 朴素固定策略 | 44 | 44 | 0 |
| 默认固定策略 | 55 | 55 | 0 |
| 确定性反馈修复 | 120 | 120 | 0 |
| 外部 Oracle | 168 | 120 | 48 |

DeepSeek 无反馈结果：

| 子集 | 通过 | 请求 | Tokens | API 错误 |
|---|---:|---:|---:|---:|
| 旧回归任务 | 40/40 | 40 | 9,060 | 0 |
| 新措辞与复杂数据任务 | 80/80 | 80 | 21,276 | 0 |
| 能力扩展任务 | 0/48 | 48 | 13,151 | 0 |
| 合计 | 120/168 | 168 | 43,487 | 0 |

在能力扩展任务中，模型对现有三个字段的相关部分选择正确 31/48，但最终结果仍为 0/48。这支持“瓶颈在可执行策略空间，而不是普通枚举选择”的判断。

## 复现

```bash
export PYTHONPATH=src

python experiments/verify_rsi_benchmark.py \
  --suite benchmarks/retailops/rsi-v2

python experiments/evaluate_rsi_benchmark.py \
  --suite benchmarks/retailops/rsi-v2 \
  --output artifacts/rsi-v2/deterministic-baselines.json

python experiments/run_one_shot_suite.py \
  --suite benchmarks/retailops/rsi-v2 \
  --splits evolution validation transfer \
  --case-kind capability_expansion \
  --max-attempts 3 \
  --resume \
  --output artifacts/rsi-v2/deepseek-capability-one-shot.json
```

模型脚本支持逐任务原子检查点、瞬时网络错误重试和断点续跑。运行前需从安全位置加载 DeepSeek 环境变量，密钥不进入仓库或实验产物。

## 限制与下一步

本轮只建立了 RSI 所需的评测地基和明确能力缺口，尚未实现多代候选生成、准入与持久化。不能将 0/48 到未来更高分数的人工代码修改称为 RSI。

下一阶段需要：

1. 将可编辑对象限定为版本化的策略插件或规则库；
2. 只使用 evolution 失败证据提出候选修改；
3. 用 validation、回归和成本门禁决定是否采用候选；
4. 冻结 transfer，在多代结束后一次性评测；
5. 记录每代补丁、采用原因、准确率、调用次数、Tokens 和回滚事件。

只有持久化后的后代策略在未见 transfer 任务上提升，同时旧能力不退化，才能称为“受约束递归自改进”的实验证据。
