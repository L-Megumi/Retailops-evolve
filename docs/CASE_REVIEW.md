# 售后案例核查与辅助归因

RetailOps 的 `review` 入口关联售后案例、订单主记录、退款记录和核查凭证，输出异常类型、记录证据、政策引用、缺失信息及人工处理建议。适合售后运营对一批案例进行初步核查和整理。

## 核查范围

| 检查项 | 实现 | 边界 |
| --- | --- | --- |
| `records` 记录一致性 | 重复退款记录、同退款 ID 信息冲突、孤立退款、订单主记录冲突、退款流水重复引用 | 记录重复不直接等于实际重复付款；流水冲突需核对源账单 |
| `amount` 金额一致性 | 使用实付金额，对唯一退款 ID 的成功状态记录汇总并比较 | 请求中、审核通过但未成功、失败、取消状态不作为已退款；冲突记录不择一汇总 |
| `freight` 运费依据 | 按平台、渠道、退货原因、商品状态及凭证条件匹配已审核政策适配规则 | 给出核查参考及拟定承担方不一致提示，不执行退款或判定最终责任 |
| `materials` 材料检查 | 质量问题凭证/平台审核记录，以及特定商品的赠品、发票寄回要求 | 区分政策条件与内部核查证据要求；无赠品或无纸质发票时不要求不存在的材料 |

## 输入与数据关联

输入使用一个 JSON 案例包，包含 `schema_version: 1`、`dataset_kind` 和四张逻辑表：

- `cases`：`case_id`、`platform`、`shop_id`、`order_id`、`checks`、`business_scope`、`reason`、`request_type`、`application_date`、可选 `policy_as_of`、可选 `proposed_freight_party`。
- `orders`：唯一订单主记录及 `evidence_id`，包含平台、店铺、订单号、实付金额；按需补充签收日期、商品完好状态、七天无理由标识、包邮信息、会员及履约信息、是否含赠品和纸质发票。
- `refunds`：`evidence_id`、平台、店铺、订单号、退款 ID、金额、日期和状态；可选 `refund_transaction_id` 是退款出账流水号，不是原订单支付流水号。
- `evidence`：`evidence_id`、`case_id`、`kind`、`status` 和可选说明文字。`confirmed` 表示输入中已标记核实的记录状态，本版本不自动识别图片或验证真实资金流水。

所有关联使用 `(platform, shop_id, order_id)`，退款 ID 及退款流水在平台/店铺内核对，避免不同店铺的同名编号串单。`orders` 是每单一条的主记录，原有 CSV 订单明细及分析入口仍可独立使用；本版本不会把商品原价合计当成实付金额。

金额以十进制字符串表示，最多两位小数。日期为 `YYYY-MM-DD`。平台编码为 `jd`、`suning`、`taobao`、`tmall`、`pinduoduo`。京东需明确 `marketplace` 或 `self_operated`，其他当前适配规则使用 `platform`。

原因编码：`quality` 质量、`personal` 主观无理由、`description_mismatch` 描述不符、`wrong_item` 发错商品、`other` 其他。原始工单仍需先整理成案例字段；未提供的原因、时间或凭证不会由模型猜测补齐。

## 执行与证据

流程为：加载并校验 → 确定检查项和案例范围 → 关联记录 → 数据工具核查 → 检索政策与扩读适配条款 → 校验来源和适用条件 → 输出报告。请求可以覆盖检查项或选择部分案例；简单“只检查金额”“不查政策”等限制也会参与选择。

政策适配规则位于 `knowledge/review-rules.json`，已审核来源清单位于 `knowledge/review-rule-sources.json`。来源绑定包括正文哈希、版本、来源类型、URL、平台和渠道。条款文字或元数据变化后，旧适配规则停止使用；重新构建评测数据也不会自动刷新批准清单，需先人工核对政策与条件，再明确更新规则和来源清单。

检索复用现有 BM25/向量/RRF 与 Qdrant。固定规则需要的条款若未出现在候选中，可按已审核的条款 ID 扩读，仍受平台、渠道、状态和日期过滤。规则没有覆盖的业务留作政策缺口，不能只凭语义相似就套用。

未指定 `policy_as_of` 时提供当前收录快照下的核查参考；指定日期时使用有效期过滤，未知生效日期的条款不会用于历史核查。签收/申请时间用于检查已绑定规则的业务窗口，不用于反推政策生效日期。

模型可生成带引用的辅助说明，输入为结构化事实、发现、缺口和已绑定政策。说明保留为待复核草稿；引用 ID、检查状态或付款动作表述校验不通过时回退到确定性报告。仅有政策或工具缺口、没有可解释发现的案例直接使用结构化说明，避免模型猜测新检查项的含义。模型不能修改异常标签、计算结果、源文件、政策适配规则或插件注册表。

## 结果与后续处理

每个案例有独立状态：

| 状态 | 含义 |
| --- | --- |
| `clear` | 本次指定检查未发现异常，不代表退款审批通过 |
| `needs_review` | 发现异常记录、承担方不一致或材料缺口，需人工复核 |
| `needs_information` | 缺少输入事实，当前无法完成检查 |
| `needs_policy` | 缺少已审核适用规则，或来源/版本/日期不匹配 |
| `needs_tool` | 未覆盖所请求的检查能力 |

报告执行成功与业务案例是否异常分别记录：任务 `completed` 表示核查报告已形成，案例仍可为 `needs_review`。输入无效或依赖失败时任务为 `failed`。

缺口按 `data`、`knowledge`、`tool` 分类：补信息、核验知识源、准备工具规格。启用 `--evolve` 后，已登记可信定义和适配器的工具缺口可自动生成指标插件，经过独立验证后重新规划执行。知识缺口及未定义的新检查仍保留，不由代码生成补写政策或验证标准。

## 命令行

```bash
# 无模型的确定性核查与中文报告
PYTHONPATH=src python -m retailops_agent.cli review \
  --casebook data/case-review/demo.json \
  --output runs/case-review.json --report runs/case-review.md

# 真实 Qdrant；DeepSeek 可选，最多 8 次说明调用
export RETAILOPS_EMBEDDING_CACHE=/home/ljh/.cache/retailops-embeddings
PYTHONPATH=src python -m retailops_agent.cli review \
  --casebook data/case-review/demo.json \
  --embedding-model BAAI/bge-small-zh-v1.5 --qdrant-url http://127.0.0.1:6333 \
  --planner model --max-model-calls 8 \
  --output runs/case-review-model.json --report runs/case-review-model.md

# 指定案例及检查项
PYTHONPATH=src python -m retailops_agent.cli review \
  --casebook data/case-review/demo.json --case-id CASE-06-0 --check amount
```

## 服务

复用原 `/tasks`、SQLite 持久任务及 SSE，不需要为案例核查上传 CSV：

```json
{
  "mode": "case_review",
  "task": "核查售后案例并整理证据",
  "casebook": "case-review/demo.json",
  "case_ids": ["CASE-06-0", "CASE-11-0"],
  "planner": "rule"
}
```

`casebook` 必须位于服务数据根目录内，大小最多 4 MiB。平台、渠道与政策日期放在每个案例中，服务不接受另外一组会被静默忽略的范围参数。`required_results` 在此模式下指定检查项。`evolve: true` 可请求案例派生指标演进，服务必须以 `--allow-evolution` 启用。插件保存到任务状态目录旁的 `case-plugins/registry.json`，后续任务可在 `evolve: false` 时复用。`planner: rule` 加 `evolve: true` 只调用代码生成，`planner: model` 才额外生成报告说明。

## 案例评测

`data/case-review/benchmark.json` 有 60 个明确标记为人工构造的案例，预期标签单独保存在 `benchmarks/case-review/oracle.json`，不进入模型输入或知识索引。40 个开发案例、20 个冻结参数边界案例，覆盖相同的 20 类机制；冻结部分用于边界回归，不表示未见过的新业务类型。

```bash
PYTHONPATH=src python experiments/evaluate_case_review.py \
  --qdrant-url http://127.0.0.1:6333 \
  --output artifacts/case-review-20261120/evaluation.json
```

对照分别启用数据工具、政策工具、组合流程；统计案例状态/异常类型/缺口类型/运费建议是否匹配，以及异常精确率、召回率、正常案例误报和引用有效性。缺失能力在对照组中保留为缺口，不能冒充正常完成。

当前合成集用于验证执行和证据约束。商家实际处理耗时、人工改写量及真实异常识别效果，需后续接入脱敏工单进行验证。

## 任务规划与受控 RSI

规划器将案例检查项、显式 `--check` 和自然语言需求整理为检查契约与依赖步骤：关联记录、政策检索、各检查节点、报告。支持“只统计退款频次”“不要检查运费”“不要生成插件”等限制，显式检查与禁止项冲突时拒绝执行。当前规划器使用检查目录及规则匹配，不让模型重写已核对的业务事实。

| 检查项 | 可信指标插件 | 必需参数 | 范围 |
| --- | --- | --- | --- |
| `refund_velocity` | `unique_refund_events` | `window_days`、`event_count_threshold`，以及案例 `application_date` | 同平台/店铺/订单，参考日及此前 N-1 个自然日内，成功且唯一退款 ID 的事件数；超过阈值才提示 |
| `refund_average` | `average_refund_amount` | `average_amount_threshold`，金额字符串 | 同订单成功且唯一的退款记录，平均金额按分向下取整；超过指定金额才提示 |
| `refund_replay` | `refund_replay_rate` | `replay_rate_threshold_bps`，0–10000 | 同订单原始导出记录中重复退款 ID 的行数比例；不代表真实重复付款 |

参数放在案例的 `review_parameters` 中，是用户提供的分析阈值，不是平台政策。记录或流水关联冲突时先补信息，不生成工具来掩盖冲突。此前演示中的 `refund_velocity` 占位任务未带参数，新运行时会将其从工具缺口细化为参数缺失。

执行顺序：检查输入 → 查已安装能力 → 若缺失且已授权，生成候选 → AST 静态检查与 Linux 隔离执行 → 两组独立验证 → 冻结第三组审计 → 原子写入插件注册表 → 发出 `case_replan` 事件并执行 → 后续任务复用。每次运行仍检查源码及验证器绑定，并验证实际结果；故障版本隔离，存在稳定版本时回滚。

复用现有 `CapabilityEvolver`、`PluginManager` 和 `PluginSpec.metric_tool`，新增加的是案例数据适配器与任务规划连接。模型不提供验证器、修改政策规则或修改模型权重。默认每任务最多 6 次代码生成调用，每能力最多 2 次尝试；同任务失败能力不逐案例重复生成。缺事实、缺知识或缺可信定义时不进入生成。每个受理指标在 5 个开发样例、5 个验证样例及冻结后的 5 个审计样例上核对，真实输入结果仍逐次验证。

```json
{
  "checks": ["records", "amount", "freight", "refund_velocity"],
  "application_date": "2026-11-20",
  "review_parameters": {"window_days": 7, "event_count_threshold": 2}
}
```

```bash
# 缺工具时生成并重规划，之后可去掉 --evolve 复用
PYTHONPATH=src python -m retailops_agent.cli review \
  --casebook data/case-review/rsi-v2.json --evolve \
  --case-plugin-registry state/case-plugins/registry.json \
  --output runs/rsi-review.json --report runs/rsi-review.md

# 六类检查共同运行的案例
PYTHONPATH=src python -m retailops_agent.cli review \
  --casebook data/case-review/integrated-v2.json --evolve \
  --output runs/integrated-review.json --report runs/integrated-review.md
```

## 第二版评测资料

- `public-v2.json`：6 个公开案例改编。原始事实、未披露信息、人工添加的核查问题分别标记；不推测平台，不将判决结果写入运行输入。当前均为政策覆盖缺口，不能将缺口分类正确率解释为案件解决率。
- `combination-v2.json`：24 个同时包含数据和政策条件的新组合。
- `rsi-v2.json`：24 个派生指标边界案例，其中 21 个具备输入条件、3 个需补数据。
- `integrated-v2.json`：记录、金额、政策、频次、均额、重放率共同执行的案例。

标签分别保存在 `benchmarks/case-review/*-oracle.json`；公开来源清单在 `public-v2-sources.json`。阈值只用于显式定义的分析，不据此宣称发现欺诈或真实资金重复支付。

```bash
PYTHONPATH=src python experiments/evaluate_case_review.py \
  --casebook data/case-review/combination-v2.json \
  --oracle benchmarks/case-review/combination-v2-oracle.json \
  --output runs/combination-eval.json

# 使用全新状态目录，比较演进前、演进后及重新加载复用
PYTHONPATH=src python experiments/evaluate_case_rsi.py \
  --state runs/new-case-rsi-state --output runs/case-rsi-eval.json
```
