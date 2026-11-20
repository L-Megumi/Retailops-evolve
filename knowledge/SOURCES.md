# RetailOps knowledge sources

本批语料为人工核对后编写的条款摘要（`content_kind=curated_summary`），不是官方全文镜像。官方网页按一页一文档组织，合计 5 份官方文档、10 条摘要；另有 2 份内部说明、4 条说明，共 7 份文档、14 条条款。

## 来源与适用范围

| 文档 ID | 来源 | 本批使用范围 |
| --- | --- | --- |
| jd-marketplace-shipping | [第三方卖家商品售后服务运费规则](https://help.jd.com/user/issue/410-2968.html) | 签收后 7 天内，无理由及非买家原因两类运费规则 |
| jd-marketplace-office | [软件、电脑办公类商品售后服务细则（三方）](https://help.jd.com/user/issue/289-936.html) | 赠品发票、存储商品数据导出；保留品类及详情页优先条件 |
| jd-marketplace-disputes | [京东纠纷处理规则（买家版）](https://help.jd.com/user/issue/464-1501.html) | 审核后寄回与凭证提交；不含自营、虚拟、闪购及海外购 |
| jd-self-operated-shipping | [京东自营售后运费说明](https://help.jd.com/user/issue/131-496.html) | 京东发货自营商品的会员运费差异与快递垫付返还 |
| suning-freight-disputes | [运费争议处理规范](https://rule.suning.com/ruleInfo/ruleInfoDetail/GZ100006683.htm) | 平台介入的质量问题运费及运费争议举证 |

只保存与本次任务相关的短摘要，每个页面的两条摘要合计均控制在 200 个汉字以内。条款保留平台、渠道、时间窗口、品类和例外，不能跨范围套用。

## 时间、版本与状态

- `retrieved_at` 是本次检索核对时间，时区为 Asia/Shanghai；它不是政策发布或生效时间。
- `version=curated-2026-11-20-v1` 是本项目摘要版本，不是平台官方规则版本。
- 京东买家版纠纷规则正文明确写明 2023-09-19 生效，保存在 `effective_from`。
- 苏宁正文写明公示无修改时于 2023-10-14 生效，另有修改则重新公示；本次未独立核验该条件，`effective_from` 留空。
- 其他页面未核实生效日期，保持空值；`effective_to` 均为空，表示未核实终止日期。
- `status=active` 表示当前知识库允许检索该摘要，不表示已证明该条款适用于任意历史时间。涉及指定日期时，应核验生效范围，不能用抓取日期反推。
- 小米页面本批没有入库：帮助页正文提取不完整，另一个页面与新检索摘要的报销额度存在差别，没有用旧额度填补。

## 内部说明的证据与性质

`retailops-analysis-spec` 按当前开发副本以下文件核对：

- `src/retailops_agent/models.py`：OrderLine、Refund、ExecutionPolicy、ToolResult。
- `src/retailops_agent/task_contract.py`：内置结果、required_results，以及仅报告任务禁止数据变换的约束。
- `src/retailops_agent/plugin_protocol.py`：raw/effective 输入视图与两个插件接口。
- `src/retailops_agent/plugin_manager.py`：变换链使用 effective，指标按 input_view 读取原始或有效退款。

`retailops-review-sop` 是根据现有字段编写的项目演示核查流程，`source_type=demo_sop`。其 source_url 指向说明所依据的数据模型，核查流程由本项目编写，不属于商家官方政策，也不是实际支付系统已实现的处理功能。订单模型不含签收、商品状态和申请时间，所以演示 SOP 要求补齐这些事实。

重复事件、去重、退款封顶属于分析记录的处理与核查；实际退款资格、交易退款金额和资金是否重复支付，应根据源业务记录及适用政策判断。

## 评测数据约定

`benchmarks/knowledge/questions.json` 包含 30 条人工核对问题：

- 16 条检索题，覆盖全部 14 条条款；另外覆盖自营会员身份与质量原因的不同分支。
- 6 条范围澄清题，覆盖平台、渠道、品类及签收等事实缺失。
- 3 条比较题，覆盖跨平台与同平台自营/第三方对照。
- 5 条证据不足题，覆盖未知平台、历史政策和缺少精确答案。

`expected_clause_ids` 代表能够支持所问结论的条款，不是所有可能相关的候选。比较题可以有多个正确条款。`expected_platforms` 代表正确答案引用应涉及的平台，不等同于输入已识别的平台。澄清或证据不足题通常为空。

`kb-027` 与 `kb-028` 标记 `evaluation_layer=answer_support`：现有语料不能回答精确运费上限或银行卡到账小时数。只返回相关运费摘要的摘录模式，应单独报告检索结果，不计为完整答案已得到证据支持。

检索召回在可回答题上计算；澄清、历史有效期和答案充分性分别评估。问题、正确条款 ID 和预期状态仅存放在 benchmarks 目录，不加入检索正文。没有执行模型调用，没有借用其他项目测试数字。
