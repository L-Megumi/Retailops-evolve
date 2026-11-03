# RetailOps 受约束自改进实验报告

## 结论

本项目已经完成一个可复现的 **Harness 级受约束自改进闭环**：DeepSeek 根据 evolution 失败证据逐代提出一条策略规则，候选经过 scope critic、validation 增益门禁和 regression 防退化检查后才允许写入持久策略库。

最终实验结果：

- 4 代全部准入，每代只新增 1 条规则；
- evolution 从 32/48 提升到 48/48；
- validation 从 24/40 提升到 40/40；
- regression 始终保持 40/40；
- 不参与候选选择的 transfer 最终为 40/40；
- 冻结策略库后，在新随机种子的 post-freeze audit transfer 上为 40/40，模型调用为 0；
- DeepSeek proposer 共调用 4 次，使用 4,201 Tokens。

这可以作为“持久策略自改进”的证据，但不能表述成开放式代码 RSI：四个安全执行算子由系统预先实现，模型演化的是任务到算子的可审计路由规则，不会生成或执行任意代码。

## 数据与能力缺口

`rsi-v2` 共 168 项：

| 划分 | 现有策略任务 | 能力扩展任务 | 合计 | 用途 |
|---|---:|---:|---:|---|
| regression | 40 | 0 | 40 | 防止旧能力退化 |
| evolution | 32 | 16 | 48 | 允许读取失败证据并产生候选 |
| validation | 24 | 16 | 40 | 决定候选是否准入 |
| transfer | 24 | 16 | 40 | 搜索结束后评测迁移 |
| 总计 | 120 | 48 | 168 |  |

能力扩展包含四类，每类在 evolution、validation、transfer 各 4 项：

1. 跨品类退款按品类数等额分摊；
2. 跨品类退款按商品数量加权分摊；
3. 按 `refund_id` 对重复采集退款去重；
4. 根据退款与订单的间隔天数逐笔选择归属日期。

旧 `ExecutionPolicy` 只能表达三个二值口径，因此朴素策略、默认策略和旧反馈修复在 48 项能力扩展任务上均为 0/48；独立 Oracle 为 48/48。

## 防泄漏结构

候选输入位于 `benchmarks/retailops/rsi-v2/tasks/<split>/<case>/`，只包含自然语言任务、订单 CSV 和退款 CSV。公开任务 ID 与 metadata 不包含能力类型。

业务合同和预期结果位于独立 `oracle/` 目录。`manifest.json` 保存 504 个候选可见文件和 168 个 Oracle 文件的 SHA-256。完整性检查验证：

- 任务目录不存在 `metric_contract` 或 `expected.json`；
- 所有公开文件和 Oracle 文件哈希匹配；
- Oracle 能从原始 CSV 与私有合同独立重算；
- manifest、任务和 Oracle 身份一一对应。

候选 proposer 只接收一个失败任务的指令、工具、当前策略库、外部判定“未通过”以及上次拒绝原因，不接收 Oracle 数值或能力标签。

## 自改进对象与门禁

持久化对象是版本化 `StrategyLibrary`。每条规则只有：

```json
{
  "rule_id": "learned-g01",
  "cue": "同一退款订单涉及 N 个品类时",
  "action": "equal_category_allocation",
  "reason": "...",
  "source_task": "cap-evolution-001"
}
```

安全边界：

- 每代最多新增一条规则；
- `action` 必须来自四个预注册安全算子；
- `cue` 必须从任务原文的候选子串白名单中选择；
- cue 白名单排除包含具体数字的短语；
- scope critic 要求 cue 至少覆盖 4 个当前失败任务，且不能命中任何已通过的 evolution 任务；
- evolution 与 validation 必须各新增至少 4 个通过项；
- evolution、validation、regression 中原本通过的任务均不得退化；
- 未通过门禁的候选只保留审计记录，不更新 incumbent。

每代候选、评测明细、准入检查、策略哈希和回滚状态保存在独立 generation 目录与 `ledger.json` 中。

## 多代结果

| 代次 | 新规则 | evolution | validation | regression |
|---:|---|---:|---:|---:|
| 0 | 空策略库 | 32/48 | 24/40 | 40/40 |
| 1 | 等额品类分摊 | 36/48 | 28/40 | 40/40 |
| 2 | 按商品数量分摊 | 40/48 | 32/40 | 40/40 |
| 3 | 重复退款去重 | 44/48 | 36/40 | 40/40 |
| 4 | 按退款时差动态归属 | 48/48 | 40/40 | 40/40 |

最终策略库：

- 版本：4；
- 准入候选：4/4；
- proposer 请求：4；
- proposer Tokens：4,201；
- 规范化库 SHA-256：`0baf1055ddf0ca05591ace2df10a66282f51479b40b2bdfe71518f1efb09c303`；
- transfer：40/40；
- 新种子 post-freeze audit transfer：40/40，0 模型调用，策略库未修改。

## 公平基线

仅允许选择旧三个字段的 one-shot 在能力扩展集为 0/48，这只能说明旧动作空间不足，不能单独证明自改进优于逐题调用模型。

因此增加了相同动作空间的无记忆路由基线：DeepSeek 每个任务都可以从与策略库完全相同的四个安全算子中选择，但任务间不保存经验。

| 方法 | 通过 | 模型调用 | Tokens | 持久记忆 |
|---|---:|---:|---:|---|
| 旧三字段 one-shot | 0/48 | 48 | 13,152 | 否 |
| 同动作空间、无记忆路由 | 48/48 | 48 | 13,891 | 否 |
| 4 代持久策略演化 | 48/48 | 4 | 4,201 | 是 |

相对于公平的无记忆路由器，持久策略演化在保持结果一致的情况下：

- 模型调用减少 91.7%；
- Tokens 减少约 69.8%；
- 训练完成后的任务执行不再调用模型；
- 每条规则都可以追溯到来源任务、准入证据和库版本。

## 复现

```bash
export PYTHONPATH=src

python experiments/verify_rsi_benchmark.py \
  --suite benchmarks/retailops/rsi-v2

python experiments/run_rsi_evolution.py \
  --suite benchmarks/retailops/rsi-v2 \
  --output artifacts/rsi-v2/evolution-deepseek-scope-critic \
  --proposer deepseek \
  --max-generations 8 \
  --candidates-per-generation 3 \
  --minimum-split-gain 4

python experiments/run_capability_router_baseline.py \
  --suite benchmarks/retailops/rsi-v2 \
  --splits evolution validation transfer \
  --output artifacts/rsi-v2/capability-router-one-shot.json

python experiments/evaluate_strategy_library.py \
  --suite benchmarks/retailops/rsi-audit-v1 \
  --library artifacts/rsi-v2/evolution-deepseek-scope-critic/incumbent/library.json \
  --split transfer \
  --output artifacts/rsi-v2/post-freeze-audit.json
```

模型脚本从安全位置读取 DeepSeek 环境变量，密钥不进入仓库或实验产物。

## 结论边界

当前可以准确表述为：

> 构建受约束的 Agent 策略自改进框架，基于失败证据生成并持久化业务规则，通过 evolution/validation 增益门禁与 regression 防退化检查完成 4 代策略演化；在 168 项合成任务上保持旧能力 40/40，并将能力扩展任务从 0 提升至 48/48，相比逐题模型路由减少 91.7% 调用和约 69.8% Tokens。

仍需明确以下限制：

- 这是策略库/Harness 级自改进，不是模型权重自训练，也不是任意代码自修改；
- 安全算子由开发者预先实现，模型学习的是何时复用这些算子；
- 数据均为合成经营数据；
- post-freeze audit 使用新随机种子，但仍属于同一生成器和任务模板家族；
- transfer 不参与候选准入，但在 Harness 开发期间曾被查看，因此不能称为完全外部盲测；
- 评测器使用隐藏合同确定旧三字段口径，本实验主要隔离并测量新增策略路由能力。

下一阶段若要接近更强的 RSI 结论，需要允许模型在沙箱中提出新的安全算子实现，并使用独立维护、不同模板来源的外部审计集进行准入外评测。
