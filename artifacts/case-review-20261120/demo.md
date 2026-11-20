# 售后案例核查报告

任务：核查售后案例并整理证据及待补信息

案例数：8

## CASE-02-0 · 需人工复核

平台：天猫；店铺：demo-shop-A；订单：O-2-0

- 同一退款ID的记录重复。退款ID：CASE-02-0-R1；证据：CASE-02-0-refund-row-1, CASE-02-0-refund-row-2

## CASE-06-0 · 需人工复核

平台：天猫；店铺：demo-shop-A；订单：O-6-0

- 成功状态退款记录的合计超过订单实付金额。订单实付金额：100；成功退款记录合计：101；差额：1；证据：CASE-06-0-order, CASE-06-0-refund-row-1, CASE-06-0-refund-row-2

## CASE-10-0 · 本次检查未见异常

平台：苏宁；店铺：demo-shop-A；订单：O-10-0

政策核查范围：当前收录的政策快照。

- 运费核查参考：商家承担发出与退回运费；依据：suning-freight-disputes::suning_quality_shipping
- 本次指定检查未发现异常。
- [suning-freight-disputes::suning_quality_shipping](https://rule.suning.com/ruleInfo/ruleInfoDetail/GZ100006683.htm)：苏宁易购介入运费争议时，商品确有质量问题的退换货运费由商家承担；双方另有约定时依约处理。

## CASE-11-0 · 需人工复核

平台：拼多多；店铺：demo-shop-A；订单：O-11-0

政策核查范围：当前收录的政策快照。

- 拟定运费承担方与已匹配政策条件不一致。原拟承担方：买家；政策对应承担方：商家；证据：CASE-11-0-order, CASE-11-0-quality_proof, CASE-11-0-platform_audit, pdd-protection::pdd-protection-freight
- 运费核查参考：商家承担发出与退回运费；依据：pdd-protection::pdd-protection-freight
- [pdd-protection::pdd-protection-freight](https://www.yangkeduo.com/home/customer/)：拼多多因质量问题退换货，申请页上传问题凭证且平台审核通过后，全部运费由商家承担。

## CASE-13-0 · 本次检查未见异常

平台：天猫；店铺：demo-shop-A；订单：O-13-0

政策核查范围：当前收录的政策快照。

- 运费核查参考：买家承担退回运费；依据：tmall-seven::tmall-seven-freight, tmall-seven::tmall-seven-condition, tmall-seven::tmall-seven-clock
- 本次指定检查未发现异常。
- [tmall-seven::tmall-seven-freight](https://www.tmall.com/wow/seller/act/seven-day)：天猫该服务下无理由退货或拒签通常由买家承担来回运费；包邮商品买家仅承担退回运费。
- [tmall-seven::tmall-seven-condition](https://www.tmall.com/wow/seller/act/seven-day)：天猫该服务面向适用商品：支付宝担保交易签收后七天内因主观原因申请，退货须满足二次销售要求，部分商品及类目除外。
- [tmall-seven::tmall-seven-clock](https://www.tmall.com/wow/seller/act/seven-day)：天猫该说明按物流签收次日零时开始计时，满168小时为七天，须在该期限内提出申请。

## CASE-17-0 · 需人工复核

平台：京东；店铺：demo-shop-A；订单：O-17-0

政策核查范围：当前收录的政策快照。

- 本次核查所需材料或适用信息尚未齐全。待补材料：已有赠品的寄回凭证、已有纸质发票的寄回凭证；证据：CASE-17-0-order, CASE-17-0-detail_policy_review, jd-marketplace-office::jd_office_return_materials
- 待补充（业务信息）：需要补充或核对已有赠品的寄回凭证
- 待补充（业务信息）：需要补充或核对已有纸质发票的寄回凭证
- [jd-marketplace-office::jd_office_return_materials](https://help.jd.com/user/issue/289-936.html)：京东第三方软件、电脑办公类商品退货需寄回已有赠品和发票，缺失可能影响全额退款；质量问题换货无需寄回这些材料。与商品详情页不一致时按详情页。

## CASE-19-0 · 待补政策依据

平台：淘宝；店铺：demo-shop-A；订单：O-19-0

- 待补充（政策依据）：当前没有已核对适用条件的该类核查规则

## CASE-20-0 · 待补核查能力

平台：天猫；店铺：demo-shop-A；订单：O-20-0

- 待补充（核查能力）：现有核查工具未覆盖 refund_velocity，需明确该检查项的定义与验证标准
