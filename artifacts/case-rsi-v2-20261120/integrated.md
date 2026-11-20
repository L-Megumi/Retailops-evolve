# 售后案例核查报告

任务：核查售后案例并整理证据及待补信息

案例数：2

## COMBO-01-0 · 需人工复核

平台：拼多多；店铺：demo-shop-A；订单：COMBO-01-0-ORDER

政策核查范围：当前收录的政策快照。

- 成功状态退款记录的合计超过订单实付金额。订单实付金额：100；成功退款记录合计：100.01；差额：0.01；证据：COMBO-01-0-order, COMBO-01-0-RROW1
- 拟定运费承担方与已匹配政策条件不一致。原拟承担方：买家；政策对应承担方：商家；证据：COMBO-01-0-order, COMBO-01-0-quality_proof, COMBO-01-0-platform_audit, pdd-protection::pdd-protection-freight
- 成功退款平均金额超过指定核查阈值。平均金额（分）：10001；核查阈值（分）：4000；证据：COMBO-01-0-order, COMBO-01-0-RROW1
- 运费核查参考：商家承担发出与退回运费；依据：pdd-protection::pdd-protection-freight
- 派生核查：窗口内成功退款事件数：1；使用本次指定的分析阈值。
- 派生核查：成功退款记录数：1；平均退款金额：100.01 元；使用本次指定的分析阈值。
- 派生核查：原始记录数：1；重放行数：0；记录重放比例：0%；使用本次指定的分析阈值。
- [pdd-protection::pdd-protection-freight](https://www.yangkeduo.com/home/customer/)：拼多多因质量问题退换货，申请页上传问题凭证且平台审核通过后，全部运费由商家承担。

## COMBO-02-0 · 需人工复核

平台：天猫；店铺：demo-shop-A；订单：COMBO-02-0-ORDER

政策核查范围：当前收录的政策快照。

- 拟定运费承担方与已匹配政策条件不一致。原拟承担方：商家；政策对应承担方：买家；证据：COMBO-02-0-order, COMBO-02-0-alipay_guaranteed_transaction, tmall-seven::tmall-seven-freight, tmall-seven::tmall-seven-condition, tmall-seven::tmall-seven-clock
- 成功退款平均金额超过指定核查阈值。平均金额（分）：5000；核查阈值（分）：4000；证据：COMBO-02-0-order, COMBO-02-0-RROW1, COMBO-02-0-RROW2
- 运费核查参考：买家承担退回运费；依据：tmall-seven::tmall-seven-freight, tmall-seven::tmall-seven-condition, tmall-seven::tmall-seven-clock
- 派生核查：窗口内成功退款事件数：2；使用本次指定的分析阈值。
- 派生核查：成功退款记录数：2；平均退款金额：50 元；使用本次指定的分析阈值。
- 派生核查：原始记录数：2；重放行数：0；记录重放比例：0%；使用本次指定的分析阈值。
- [tmall-seven::tmall-seven-freight](https://www.tmall.com/wow/seller/act/seven-day)：天猫该服务下无理由退货或拒签通常由买家承担来回运费；包邮商品买家仅承担退回运费。
- [tmall-seven::tmall-seven-condition](https://www.tmall.com/wow/seller/act/seven-day)：天猫该服务面向适用商品：支付宝担保交易签收后七天内因主观原因申请，退货须满足二次销售要求，部分商品及类目除外。
- [tmall-seven::tmall-seven-clock](https://www.tmall.com/wow/seller/act/seven-day)：天猫该说明按物流签收次日零时开始计时，满168小时为七天，须在该期限内提出申请。
