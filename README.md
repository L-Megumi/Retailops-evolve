# RetailOps Agent

面向电商运营的经营数据分析与自动报表智能体。它把自然语言分析任务转换为受限的分析计划，执行数据工具，依据业务口径校验结果，并在失败时进行有限次数的修复。

## 2026-11-06 运行时修复

统一入口现在支持任务输出契约、必需转换和禁止操作；无法识别的任务返回 `unsupported_task`，不会再用三个旧指标冒充成功。规则提取覆盖已登记的退款能力，复杂表述可以启用模型规划；没有可信 Verifier 的未知指标会拒绝自动生成。

- 沙箱阻止私有属性链、方法别名、Builtin 重绑定、默认参数与嵌套函数；Linux Worker 清理继承环境，使用 `no_new_privs`、seccomp 系统调用白名单和 CPU/内存限制。限制安装失败时停止执行。插件执行需要 Linux；已在 ljh3 的 x86_64 环境验证。
- `PluginSpec.input_view` 区分 `raw` 和 `effective`，重放率读取原始退款。`provides` 描述退款去重与封顶能力，防止只执行复合需求中的一部分；输出 Schema 版本经过适配器校验。
- 指标插件必须通过开发者维护的字段、类型、独立语义 Verifier 与双种子准入测试；准入记录绑定源码、协议和 Verifier 摘要，在线执行仍逐次验证。当前可信定义包括重放率、唯一退款事件数和平均退款金额。
- `CapabilityEvolver` 接入 CLI 和 Agent 主链路，以有界错误反馈生成候选、冻结审计、持久注册并在后续任务复用。生成代码不能提供自己的答案或判分器；自动生成须显式开启，会产生模型调用费用。
- 注册表采用进程锁、原子持久写和版本冲突检测；路由缓存按完整任务契约及能力定义区分，并合并多进程增量写入。
- 可选 FastAPI 服务支持结构化请求、Bearer 鉴权、持久任务状态、租约心跳和可续读 SSE Trace。过期运行任务标记为 `interrupted` 并保留证据，不盲目重放可能已发布插件的任务。

安装服务与接口测试依赖：

```bash
python -m pip install -r requirements-service.lock -e '.[service,test]'
```

正常运行会自动推导已知任务的必需输出，也允许重复添加 `--required-result` 显式声明结果。在线演进使用同一入口：

```bash
PYTHONPATH=src python -m retailops_agent.cli run \
  --orders data/dedup-demo-orders.csv --refunds data/dedup-demo-refunds.csv \
  --task 'Calculate the average refund amount' \
  --plugin-registry plugins/registry.json --evolve
```

服务默认绑定本机，外部绑定必须设置 `RETAILOPS_SERVICE_TOKEN`。模型密钥沿用环境变量配置；在线演进默认关闭，启用时添加 `--allow-evolution`：

```bash
PYTHONPATH=src python -m retailops_agent.cli serve \
  --data-root data --plugin-registry plugins/registry.json --state state/tasks.sqlite3
```

接口包括 `GET /health`、`POST /tasks`、`GET /tasks/{task_id}`、`GET /tasks/{task_id}/events`。CSV 路径必须位于配置的数据目录，支持 `Last-Event-ID` 续读事件：

```json
{"task":"Calculate refund replay rate","orders":"dedup-demo-orders.csv","refunds":"dedup-demo-refunds.csv","planner":"rule","evolve":false}
```

提供非 root 用户的 Dockerfile；状态和动态插件分别持久化到数据卷。容器内仍要求内核允许安装更严格的 seccomp 过滤器：

```bash
docker build -t retailops-evolve:local .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e RETAILOPS_SERVICE_TOKEN \
  -v retailops-state:/app/state -v retailops-plugins:/app/plugins retailops-evolve:local
```

本轮只运行定向组件测试、替身 Agent 接口测试与组件级耗时测试，**未运行完整端到端回归、历史 Benchmark 重评分或真实模型修复试验**：

```bash
PYTHONPATH=src python experiments/run_component_checks.py \
  --output artifacts/hardening-20261106/component-tests.json
PYTHONPATH=src python experiments/benchmark_runtime_components.py \
  --output artifacts/hardening-20261106/component-timing.json
```

测试结果、性能边界和历史数据的准确口径见 [本轮修复报告](artifacts/hardening-20261106/REPORT.md)。下文已有报告保留原实验时的代码与数据结论，不代表新版本的端到端成绩。

当前 MVP 支持：

- 按日期统计净销售额；
- 按订单口径计算退款率；
- 生成品类净销售额排行；
- 检测缺失字段、非法金额、未知任务和结果不守恒；
- 输出包含执行轨迹、校验结果和指标表的 Markdown 报告。

## 快速运行

```powershell
uv run --project . retailops run `
  --orders data/orders.csv `
  --refunds data/refunds.csv `
  --task "分析每日净销售额、退款率和品类排行"
```

默认使用可复现的规则规划器。接入任意 OpenAI 兼容模型时，设置以下环境变量并添加 `--planner model`：

```powershell
$env:RETAILOPS_API_KEY="..."
$env:RETAILOPS_BASE_URL="https://api.openai.com/v1"
$env:RETAILOPS_MODEL="gpt-4.1-mini"
```

模型只负责选择白名单分析工具，实际计算和业务校验由确定性代码完成。Harness² 适配器进一步把校验反馈接入策略改进、重新执行、结果选择和经验沉淀循环。

要演示“错误口径 → 校验反馈 → 策略修复 → 重新执行”，运行：

```powershell
uv run --project . retailops run `
  --orders data/orders.csv `
  --refunds data/refunds.csv `
  --task "计算退款率并生成品类排行" `
  --initial-policy naive
```

朴素策略会按订单明细行计算退款率，并把跨品类订单的退款全部计入第一个品类。校验器分别检查去重订单口径和退款分摊规则，修复器据此切换执行策略。该故障注入用于验证闭环，不代表生产默认配置。

## Harness² 适配实验

`RetailOpsHarnessAdapter` 把两组经营分析任务接到 Harness² 的真实执行、证据、改进和经验沉淀流程。先运行确定性编辑器验证适配器，不需要模型凭据：

```powershell
$env:PYTHONPATH="src;../work/harness2-upstream"
py -3.12 experiments/run_harness2_demo.py --k 1
```

确定性编辑器只用于验证集成：它按照预设方式修正两个策略字段，不能作为模型自主改进的实验结果。接入真实模型后，保持相同任务、校验器和预算，比较基础策略、规则修复与 Harness² 改进策略。

生成基础策略与规则修复的对照结果：

```powershell
$env:PYTHONPATH="src"
py -3.12 experiments/evaluate_strategies.py
```

该对照集目前只有两项合成任务，只用于开发回归和验证评测链路。扩大任务集并接入真实模型后，才能将结果作为稳定的效果结论。

生成固定随机种子的 12 项开发评测集，并运行策略对照：

```powershell
$env:PYTHONPATH="src"
py -3.12 experiments/generate_benchmark.py --count 12 --seed 20261103
py -3.12 experiments/evaluate_strategies.py `
  --tasks benchmarks/retailops/generated `
  --output runs/evaluation/generated-comparison.json
```

生成集包含退款率、品类退款分摊、组合任务和无需策略修复的每日汇总四类场景。它用于开发和回归；最终效果报告还应保留一份独立、未参与规则开发的测试集。

生成并验证冻结独立集：

```powershell
$env:PYTHONPATH="src;../work/harness2-upstream"
py -3.12 experiments/generate_holdout.py `
  --output benchmarks/retailops/holdout-v4 `
  --count 40 `
  --seed 2026110306 `
  --dynamic-contracts
py -3.12 experiments/verify_holdout.py --tasks benchmarks/retailops/holdout-v4
py -3.12 experiments/evaluate_holdout.py `
  --tasks benchmarks/retailops/holdout-v4 `
  --output runs/holdout-v4/strategy-comparison.json
```

`manifest.json` 保存 160 个输入/真值文件与 15 个冻结源码文件的 SHA-256。每个任务还带有生成器独立计算的 `expected.json`，最终结果需要同时通过业务校验器和外部真值对比。动态集在退款率分母、跨品类退款分摊和退款日期归属三类口径间切换，避免所有任务收敛到同一固定策略。

真实 Harness² 编辑实验使用 `experiments/run_harness2_model.py`。首次运行从一个任务、`k=1` 和单一模式开始控制成本，例如：

```powershell
$env:PYTHONPATH="src;../work/harness2-upstream"
py -3.12 experiments/run_harness2_model.py `
  --channel opencode `
  --model "provider/model-name" `
  --mode parallel `
  --k 1
```

该命令需要对应模型渠道和凭据。模型实验的成功率、调用次数和成本必须以实际运行产物为准；确定性适配演示的数据不能写成模型自主改进结果。

### DeepSeek 直连

DeepSeek 提供 OpenAI 兼容的 Chat Completions 和 JSON Output。复制 `.env.example` 中的配置到环境变量后，可以直接让 DeepSeek 编辑策略，无需安装 OpenCode：

```powershell
$env:DEEPSEEK_API_KEY="你的 API Key"
$env:RETAILOPS_BASE_URL="https://api.deepseek.com"
$env:RETAILOPS_MODEL="deepseek-flash"
$env:RETAILOPS_MAX_TOKENS="4096"
$env:RETAILOPS_THINKING="disabled"
$env:PYTHONPATH="src;../work/harness2-upstream"

py -3.12 experiments/run_harness2_model.py `
  --channel deepseek `
  --mode sequential `
  --k 1 `
  --only-task refund-metrics-1
```

也可以将模型改为 `deepseek-v4-pro`。DeepSeek 返回的策略字段经过枚举白名单校验；未知值不会写入执行脚本。每次模型响应和 token 用量保存在运行目录的 `audit` 文件夹中。

客户端会识别 DeepSeek JSON Output 以空白字符替换无效 JSON 的情况，并在第二次请求中降级为普通文本模式后严格解析 JSON。经营策略选择默认关闭思考模式，以减少此类短分类任务的延迟和 token 消耗。`result.json` 同时记录真实 API 请求数、累计 token、终止原因和内容摘要。

模型运行默认先执行基础策略：已通过校验的任务立即早停，不发送模型请求；失败任务只向模型传递当前策略、问题代码和期望口径。模型只允许修改被校验器点名的字段，省略或改写未报错字段时仍保留当前策略。

运行测试：

```powershell
uv run --project . python -m unittest discover -s tests -v
```

## RSI 分层评测基准

`benchmarks/retailops/rsi-v2` 将 168 项任务划分为 regression、evolution、validation 和 transfer。候选 Agent 只能访问单个 `tasks/<split>/<case>` 目录；业务合同与标准答案保存在独立 `oracle/` 目录，由外部评测器使用。完整性检查会拒绝任务目录中的合同泄漏，并重新计算所有 Oracle 结果。

```powershell
$env:PYTHONPATH="src"
py -3.12 experiments/verify_rsi_benchmark.py `
  --suite benchmarks/retailops/rsi-v2
py -3.12 experiments/evaluate_rsi_benchmark.py `
  --suite benchmarks/retailops/rsi-v2 `
  --output artifacts/rsi-v2/deterministic-baselines.json
```

120 项任务仍属于现有三字段策略空间；另外 48 项要求等额退款分摊、按数量加权分摊、重复退款去重或按退款时差逐笔归属日期。当前反馈修复在前者达到 120/120，在后者为 0/48，而独立 Oracle 为 168/168。能力扩展集为后续策略代码演化提供了明确且可验证的提升空间，但这一步只建立基准，尚不能单独证明 RSI。

模型基线脚本支持网络重试、逐任务原子检查点和断点续跑：

```powershell
py -3.12 experiments/run_one_shot_suite.py `
  --suite benchmarks/retailops/rsi-v2 `
  --splits evolution validation transfer `
  --case-kind capability_expansion `
  --max-attempts 3 `
  --resume `
  --output artifacts/rsi-v2/deepseek-capability-one-shot.json
```

数据设计、逐项结果和结论边界见 [RSI v2 实验报告](artifacts/rsi-v2/REPORT.md)。

在 `rsi-v2` 上运行受约束的策略库演化：

```powershell
py -3.12 experiments/run_rsi_evolution.py `
  --suite benchmarks/retailops/rsi-v2 `
  --output artifacts/rsi-v2/evolution-deepseek-scope-critic `
  --proposer deepseek `
  --minimum-split-gain 4
```

最终 DeepSeek 实验用 4 次请求、4,201 Tokens 完成 4 代规则准入，evolution 从 32/48 提升至 48/48，validation 从 24/40 提升至 40/40，regression 保持 40/40，transfer 为 40/40。相同四算子但无持久记忆的逐题路由基线也达到 48/48，但需要 48 次请求和 13,891 Tokens；持久策略库减少 91.7% 模型调用和约 69.8% Tokens。该实验属于受约束的 Harness/策略自改进，不代表模型权重训练或开放式代码 RSI。

在此基础上还完成了第一次受限代码算子实验：DeepSeek 首代生成了正确的累计退款封顶算法，但因调用安全白名单之外的 `type()` 被静态 critic 拒绝；第二代根据精确错误自修复，在不放宽沙箱的情况下通过 evolution、validation、transfer 和新种子 audit 共 24 项评测。两代累计 2 次调用、1,215 Tokens。详细过程见 [代码算子实验报告](artifacts/code-evolution-v1/REPORT.md)。

代码能力随后进入独立路由准入。纯字面/关键词路由虽然在 12 项开发任务与原 168 项回归任务上通过，却在 4 条未见改写上 0/4；失败产物被保留，没有用审计句继续堆关键词。升级后的混合路由采用“冻结规则优先、DeepSeek 批量语义判别兜底”，通过 184/184 开发与回归门槛，并在新冻结的 24 条正负混合盲测上达到 24/24，共 9 次请求、19,476 Tokens。该结果证明的是受限插件路由原型，不等同于生产在线路由或开放式 RSI。

## 版本化代码插件运行时

通过准入的代码插件现已接入 `RetailOpsAgent.run()` 主链路。`PluginManager` 负责源码摘要和 AST 复验、确定性/语义路由、隔离执行、领域不变量检查、状态迁移和稳定版本回滚。插件不能新增、重复或放大退款，不能改变事件顺序和身份字段，也不能让订单累计退款超过成交额。

使用冻结规则直接运行，无需模型：

```powershell
$env:PYTHONPATH="src"
py -3.12 -m retailops_agent.cli run `
  --orders data/orders.csv `
  --refunds data/refunds.csv `
  --task "按累计退款上限生成每日净销售额和品类排行" `
  --plugin-registry plugins/registry.json
```

对未登记的新措辞启用 DeepSeek 语义兜底和持久缓存：

```powershell
$env:DEEPSEEK_API_KEY="你的 API Key"
$env:RETAILOPS_BASE_URL="https://api.deepseek.com"
$env:RETAILOPS_MODEL="deepseek-flash"
$env:PYTHONPATH="src"

py -3.12 -m retailops_agent.cli run `
  --orders data/orders.csv `
  --refunds data/refunds.csv `
  --task "同一交易的多次售后合计不能超过原始货款，按顺序裁短越界记录" `
  --plugin-registry plugins/registry.json `
  --plugin-semantic-router `
  --plugin-route-cache artifacts/runtime/route-cache.json
```

真实 CLI 重复实验中，第一次路由来源为 `semantic`，第二次为 `semantic_cache`，第二次不再调用模型。新版本运行时不变量失败时会进入 `quarantined`，并回滚最近的稳定版本；没有稳定版本时安全停止。实现与测试证据见 [插件运行时报告](artifacts/plugin-runtime-v1/REPORT.md)。

第二个插件能力 `refund-event-dedup-v1` 已完成复用验证。DeepSeek 首代生成的事件标识去重算子通过 20/20 开发随机案例和 20/20 新种子审计，只使用 1 次请求、373 Tokens。双插件路由采用单次模型请求从所有 active 插件中选择一个或拒绝；在保留 22/24、23/24 两轮失败证据并修复语义边界后，最终通过旧门槛 48/48、新冻结盲测 24/24，缓存复跑 24/24 且模型调用为 0。完整过程见 [多插件复用报告](artifacts/multi-plugin-v1/REPORT.md)。

## 通用协议、插件组合与能力 RSI

`PluginSpec` 将运行时扩展为两类受限接口：退款转换 `transform_refunds` 与指标工具 `compute_metric`，统一声明 Schema、不变量、依赖顺序、冲突和超时。组合任务按拓扑顺序执行；“事件去重 → 累计封顶”通过 40/40 随机 Oracle。任一步失败都会丢弃中间结果，从原始输入重新执行稳定版本。详见 [组合与协议报告](artifacts/plugin-composition-v1/REPORT.md)。

在此协议上完成一次受约束能力 RSI：初始 Agent 对 `refund_replay_rate` 产生 `unsupported_capability`，DeepSeek 据失败 Trace 提出 `metric_tool` 规格并生成代码；首代因 AST `Is` 被拒绝，第二代在不放宽沙箱的情况下通过 evolution 12/12、validation 12/12。结果覆盖感知路由修复后，首次冻结 audit 为 12/12；正式激活前检查原 168 项和多插件盲测，确定性碰撞均为 0。完整证据和声明边界见 [能力 RSI 报告](artifacts/capability-rsi-v3/REPORT.md)。

## 业务口径

- 销售额：订单明细 `quantity * unit_price` 的合计；
- 退款额：退款表中 `refund_amount` 的合计；
- 净销售额：销售额减退款额；
- 退款率：发生退款的去重订单数 / 全部去重订单数；
- 品类净销售额：品类销售额减该品类关联订单明细对应的退款额。

项目使用的订单和退款数据为演示用合成数据。README 中只记录实际运行后得到的结果，不预填效果数字。

## 当前验证结果

- 当前测试集在本地与服务器 `retail` 环境通过；Harness² 未安装时，其可选集成测试按预期跳过；
- 固定种子生成的 12 项开发集上，朴素策略通过 3 项；
- 校验反馈驱动的规则修复通过 12 项，其中 3 项无需修复并保持单次执行；
- Harness² 确定性适配演示完成 2/2 项任务，共产生 14 次执行和 16 次编辑/选择调用。
- 冻结的 `holdout-v4` 包含 40 项动态业务口径任务：朴素基础策略通过 17/40，确定性校验修复通过 40/40，外部真值通过 40/40。
- 真实 DeepSeek/Harness² 最终通过 40/40：修复错误基础策略 23 项、早停并保留正确基础策略 17 项、回归 0 项；共 132 次策略执行、69 次 API 请求和 24,690 tokens。
- 证据压缩与早停将平均 token 从旧版 13,356.5/项降至 617.25/项，下降约 95.4%；正确基础任务保持 0 模型调用。
- 单次 DeepSeek、无执行反馈的对照同样达到策略、业务校验和外部真值 40/40，仅使用 40 次请求和 9,060 tokens。因此当前清晰任务集不能证明反馈循环提升准确率；生产建议采用“单次策略选择 + 确定性验证”，仅在验证失败时启用 Harness² 修复，不把闭环准确率提升作为简历结论。
- `rsi-v2` 共 168 项：当前反馈修复在现有策略空间通过 120/120，在四类能力扩展任务通过 0/48；外部 Oracle 通过 168/168。DeepSeek 受限策略基线在能力扩展任务为 0/48，确认后续实验需要演化可执行策略，而不是继续优化三字段枚举提示词。
- 受约束策略库完成 4 代 DeepSeek 演化：evolution 48/48、validation 40/40、regression 40/40、transfer 40/40；新种子 post-freeze audit transfer 为 40/40 且执行阶段 0 模型调用。
- AST 受限代码算子完成一次生成与一次 critic 自修复：第一次安全拒绝，第二次在原白名单下通过原基准 12/12 和新种子 audit 12/12。
- 代码插件路由保留了字面规则在未见改写 0/4 的失败对照；混合语义路由随后通过开发/回归 184/184 和新冻结正负盲测 24/24，原 168 项任务误路由为 0。
- 服务器 `retail` 环境完整测试为 49 项运行、48 项通过、1 项 Harness² 可选依赖跳过。
- 版本化插件运行时已接入 Agent 主链路；真实语义任务首次请求模型、重复执行命中持久缓存并保持 0 次模型调用，坏版本可隔离并自动回滚稳定版本。
- 第二能力家族验证完成：事件标识去重插件功能 Oracle 40/40；双插件最终开发门槛 48/48、冻结正负盲测 24/24，正式 CLI 将重复退款样例从 70 元正确去重为 50 元。
- 原子插件组合完成：去重→累计封顶随机 Oracle 40/40，真实 CLI 将 200 元申报处理为 100 元有效退款，后置步骤失败时从原输入回滚重跑。
- 受约束能力 RSI 完成：初始新指标任务 0/36，代码自修复后功能门槛 24/24，结果覆盖感知路由后冻结 audit 12/12；第三插件正式激活且旧任务确定性碰撞为 0。

最终冻结集方法、逐任务结果、调用明细与限制见 [holdout-v4 实验报告](artifacts/deepseek-holdout-v4/REPORT.md)。所有任务仍是合成经营数据，不代表真实企业数据或开放域任务上的泛化效果。

