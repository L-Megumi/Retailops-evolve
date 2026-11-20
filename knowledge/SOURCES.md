# RetailOps knowledge sources

本批语料为人工核对后编写的条款摘要（`content_kind=curated_summary`），不是官方全文镜像。官方网页按一页一文档组织，覆盖京东、苏宁、淘宝、天猫、拼多多五个平台，合计 17 份官方文档、63 条摘要；另有 2 份内部说明、4 条说明，共 19 份文档、67 条条款。

## 来源与适用范围

| 文档 ID | 来源 | 本批使用范围 |
| --- | --- | --- |
| jd-marketplace-shipping | [第三方卖家商品售后服务运费规则](https://help.jd.com/user/issue/410-2968.html) | 签收后 7 天内，无理由及非买家原因两类运费规则 |
| jd-marketplace-office | [软件、电脑办公类商品售后服务细则（三方）](https://help.jd.com/user/issue/289-936.html) | 赠品发票、存储商品数据导出；保留品类及详情页优先条件 |
| jd-marketplace-disputes | [京东纠纷处理规则（买家版）](https://help.jd.com/user/issue/464-1501.html) | 审核后寄回与凭证提交；不含自营、虚拟、闪购及海外购 |
| jd-self-operated-shipping | [京东自营售后运费说明](https://help.jd.com/user/issue/131-496.html) | 京东发货自营商品的会员运费差异与快递垫付返还 |
| suning-freight-disputes | [运费争议处理规范](https://rule.suning.com/ruleInfo/ruleInfoDetail/GZ100006683.htm) | 平台介入的质量问题运费及运费争议举证 |

只保存与业务问题相关的精简摘要，不保存官方页面全文。条款保留平台、渠道、时间窗口、品类和例外，不能跨范围套用。

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

检索召回在可回答题上计算；澄清、历史有效期和答案充分性分别评估。问题、正确条款 ID 和预期状态仅存放在 benchmarks 目录，不加入检索正文。真实模型实验和离线检索对照分别报告，不借用其他项目测试数字。

## 五平台扩展批次

以下资料于 2026-11-20 核对公开页面；摘要版本为 `curated-2026-11-20-v2`。

| 文档 ID | 官方页面 | 收录 |
| --- | --- | --- |
| taobao-consumer | [淘宝消费者保障服务协议](https://terms.alicdn.com/legal-agreement/terms/suit_bu1_taobao/suit_bu1_taobao201709261344_28562.html) | 5 条摘要 |
| taobao-seller | [淘宝网卖家服务协议：关店与交易责任](https://terms.alicdn.com/legal-agreement/terms/suit_bu1_taobao/suit_bu1_taobao201908081502_44434.html) | 2 条摘要 |
| tmall-seven | [天猫七天无理由退换货公开服务说明](https://www.tmall.com/wow/seller/act/seven-day) | 5 条摘要 |
| tmall-warranty | [天猫保修服务卖家版订购协议](https://terms.alicdn.com/legal-agreement/terms/b_end_product_protocol/20230914123010322/20230914123010322.html) | 4 条摘要 |
| tmall-merchant | [天猫商户服务协议：保障、开票与退货费用](https://terms.alicdn.com/legal-agreement/terms/TD/TD201609271722_89275.html) | 4 条摘要 |
| pdd-protection | [拼多多消费者保障计划（2026年修订）](https://www.yangkeduo.com/home/customer/) | 7 条摘要 |
| pdd-help | [拼多多官方帮助中心：退换货流程与规则](https://www.yangkeduo.com/home/help/) | 5 条摘要 |
| jd-cancel | [京东取消订单及退货退款说明：自营部分](https://help.jd.com/user/issue/130-256.html) | 4 条摘要 |
| jd-coupon-return | [京东拒收后优惠券返还说明](https://help.jd.com/user/issue/130-481.html) | 4 条摘要 |
| jd-luxury | [京东奢侈品类自营售后服务细则](https://help.jd.com/user/issue/325-927.html) | 4 条摘要 |
| jd-building | [京东家装建材类第三方售后细则](https://help.jd.com/user/issue/330-1030.html) | 4 条摘要 |
| suning-seven | [苏宁易购主站七天无理由退货规范](https://rule.suning.com/ruleInfo/ruleInfoDetail/GZ100003631.htm) | 5 条摘要 |

淘宝消保协议明确生效日期 2024-09-12；拼多多消费者保障计划明确于 2026-01-18 修订施行。其余新增页面的生效日期未核实，保留为空，不以页面更新或采集日期替代生效日期。

天猫七天无理由公开页未标出生效版本，按公开服务说明提供摘录，历史日期查询不使用该页。天猫保修协议是特定服务的卖家版订购协议，不能解释成所有商品自带延保；天猫商户协议中的软件服务费属于商家费用，不是消费者退款手续费。

淘宝规则中心的七天无理由详情、天猫部分详细类目规范需登录或未取得正文，本批未导入二手转载。淘宝现有无理由退货条款来自消费者保障协议，未补写未核实的运费或精确时效。天猫国际、淘宝全球购、多多国际等跨境业务没有单独资料时返回证据不足。

`benchmarks/knowledge/expanded.json` 新增 132 道检索题，含 26 道口语化问题。按整份来源文档分组：96 题开发集、36 题测试集；同条款的两种表达不跨组。全部政策文档仍在检索库中，问题与标签不入库。这是本项目人工编写的评测集，不是外部公共榜单。

`live-v2.json` 是 24 道模型场景验收题，覆盖五平台回答、跨平台比较、日期澄清与证据不足。`challenge-015` 问具体截止日但缺确认收货日期，复核后预期为澄清，原始运行报告保留在 `model-public-initial.json`。
