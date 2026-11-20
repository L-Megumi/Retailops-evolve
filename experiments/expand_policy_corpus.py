"""Build reviewed concise summaries and source-grouped retrieval questions.

No generated policy facts and no QA labels are ingested into the knowledge store.
Run once against the seed corpus; repeated runs replace the added document IDs.
"""
import hashlib
import json
from pathlib import Path

# Each tuple binds a manually reviewed public source to concise paraphrases.
# (clause suffix, heading, summary, two independently worded retrieval questions).
SOURCES = [
 ("taobao-consumer", "淘宝消费者保障服务协议", "taobao", "platform",
  "https://terms.alicdn.com/legal-agreement/terms/suit_bu1_taobao/suit_bu1_taobao201709261344_28562.html", "2024-09-12", [
   ("description", "如实描述", "淘宝卖家应在商品页、店铺页及阿里旺旺等渠道如实描述商品或服务。", "淘宝商家在旺旺里说的商品信息也要真实吗？", "淘宝只要求商品详情真实，聊天里随便承诺可以吗？"),
   ("return", "无理由退货", "淘宝卖家按法律和平台规则保障商品完好前提下的无理由退货权益。", "淘宝商品完好时，无理由退货的基础保障是什么？", "淘宝卖家有哪些基本的无理由退货义务？"),
   ("address", "退货地址", "淘宝卖家须准确更新退货地址、收件人与联系方式；信息错误导致的毁损、灭失风险及相关支出由卖家按规则承担。", "淘宝卖家给错退货地址，包裹丢了责任怎么处理？", "淘宝商家退货电话失效导致无法送达，谁负责？"),
   ("promise", "特色服务承诺", "淘宝卖家自愿承诺发货时间、破损补寄、免费换新等特色服务后，应按承诺履行。", "淘宝商家承诺免费换新后可以不兑现吗？", "淘宝店铺自愿承诺的破损补寄需要履行吗？"),
   ("evidence", "维权举证", "淘宝受理消保维权时，卖家应按要求及时提供真实合法证据；平台依证据、规则和流程判定赔付义务。", "淘宝处理消费者保障争议时卖家要提供什么材料？", "淘宝会根据什么判断卖家是否需要赔付？"),
  ]),
 ("taobao-seller", "淘宝网卖家服务协议：关店与交易责任", "taobao", "platform",
  "https://terms.alicdn.com/legal-agreement/terms/suit_bu1_taobao/suit_bu1_taobao201908081502_44434.html", None, [
   ("closure", "关店后的售后", "淘宝店铺关停后，卖家仍应承担关停前交易的发货、退换货、质保维修及维权投诉处理责任。", "淘宝店铺关了，之前买的商品售后责任还在吗？", "淘宝卖家注销店铺能免除老订单维修义务吗？"),
   ("orders", "协议终止后的订单", "淘宝卖家服务协议终止后，交易相对方要求继续履行存续期间的订单时，卖家仍应履约并承担相关损失或增加费用。", "淘宝卖家协议结束了，买家要求继续履行原订单怎么办？", "淘宝商家退店后能直接不履行已经产生的交易吗？"),
  ]),
 ("tmall-seven", "天猫七天无理由退换货公开服务说明", "tmall", "platform",
  "https://www.tmall.com/wow/seller/act/seven-day", None, [
   ("condition", "申请前提", "天猫该服务面向适用商品：支付宝担保交易签收后七天内因主观原因申请，退货须满足二次销售要求，部分商品及类目除外。", "天猫买来不喜欢，七天无理由申请有什么前提？", "天猫是不是所有类目的商品都能七天无理由退？"),
   ("clock", "七天起算", "天猫该说明按物流签收次日零时开始计时，满168小时为七天，须在该期限内提出申请。", "天猫七天退货从签收当天还是第二天开始算？", "天猫七天无理由的168小时怎么起算？"),
   ("freight", "主观退货运费", "天猫该服务下无理由退货或拒签通常由买家承担来回运费；包邮商品买家仅承担退回运费。", "天猫包邮商品不喜欢退回，哪一段运费归我？", "天猫无理由拒收一个非包邮商品，运费谁出？"),
   ("quality", "质量或描述不符", "天猫该说明中，商品质量问题或与网上描述不符导致退货，退货费用由卖家承担。", "天猫商品描述不符退货运费由谁支付？", "天猫买到有质量问题的货，退回费用谁承担？"),
   ("escalation", "申请平台处理", "符合该服务条件且期限内向卖家申请遭拒、无法联系或卖家停业时，可向天猫申请处理；申请金额限实际支付货款。", "天猫卖家不接电话又拒绝七天退货，可以找平台吗？", "天猫七天无理由赔付申请金额有没有上限依据？"),
  ]),
 ("tmall-warranty", "天猫保修服务卖家版订购协议", "tmall", "platform",
  "https://terms.alicdn.com/legal-agreement/terms/b_end_product_protocol/20230914123010322/20230914123010322.html", None, [
   ("extension", "延长保修适用", "购买含延长保修服务的商品并选择该服务后，保障期内制造质量故障经审核符合条件，可获额度内维修。", "天猫延长保修是买了商品就自动享有吗？", "天猫购买延长保修后什么故障可以申请维修？"),
   ("accident", "意外保修", "购买并选择意外保修服务的用户，保障期内符合约定的意外性能故障，经审核可获额度内一次维修。", "天猫意外保修审核通过提供几次维修？", "天猫意外保修和普通制造质量故障保修有什么区别？"),
   ("limit", "保修保障额度", "该协议中保修保障额度为消费者购买商品的实际支付金额，不含服务费及运费。", "天猫保修服务保障额度是否包含运费和服务费？", "天猫卖家版保修最高保障额度按标价还是实付算？"),
   ("service-fee", "卖家服务费退回", "含该保修服务的交易确认收货后收取卖家服务费；用户退货退款成功时，相应服务费自动退回卖家。", "天猫含保修服务的商品退货成功，卖家服务费退吗？", "天猫保修卖家版什么时候扣费，退货后如何处理？"),
  ]),
 ("tmall-merchant", "天猫商户服务协议：保障、开票与退货费用", "tmall", "platform",
  "https://terms.alicdn.com/legal-agreement/terms/TD/TD201609271722_89275.html", None, [
   ("responsibility", "消费者保障主体", "天猫商户是消费者保障责任主体，应按平台规则履行基础保障、天猫无忧购及行业特色服务义务。", "天猫消费者保障责任由商户还是平台承担？", "天猫商家除了基本消保还有哪些服务义务？"),
   ("invoice", "实际付款开票", "天猫商户应按买家在其店铺交易实际支付的金额开具合规发票。", "天猫商家开票金额应按商品原价还是实际付款？", "天猫消费者发票的金额口径是什么？"),
   ("category-fee", "退货软件服务费", "发生退货且商户无过错时，天猫核实线上退款记录后，退还对应交易实时划扣的类目软件服务费。", "天猫商家无过错退货后类目软件服务费能返还吗？", "天猫按什么证据核实商家退货后的软件服务费返还？"),
   ("exit", "退出期间处理", "天猫确认终止协议后商户进入退出流程，三十天内仅可处理交易纠纷及与天猫的账务、发票结算。", "天猫商家进入退出流程后还能处理哪些事项？", "天猫商户退店流程中有多久处理纠纷和发票结算？"),
  ]),
 ("pdd-protection", "拼多多消费者保障计划（2026年修订）", "pinduoduo", "platform",
  "https://www.yangkeduo.com/home/customer/", "2026-01-18", [
   ("authenticity", "正品承诺", "拼多多假冒品牌商品须商品页带有“假一赔十”标识，才可按该承诺向商家主张假一赔十。", "拼多多所有假货都能按假一赔十申请吗？", "拼多多假一赔十要先看商品页的什么标识？"),
   ("quality", "质量与描述保障", "拼多多商品质量不符描述或法定标准时，可主张换货、退货退款及赔付；商家应如实描述。", "拼多多商品不符合描述可以申请什么售后？", "拼多多商品质量不达标，消费者有哪些主张？"),
   ("delivery", "诚信发货", "拼多多商家须在规定时限真实发货，物流单号真实有效；违反承诺可申请售后及赔付。", "拼多多商家填了假物流单号，有什么保障？", "拼多多诚信发货对物流信息有什么要求？"),
   ("seven", "七天退换与介入", "拼多多提供七天无理由退换货保障，法律排除情形除外；商家拒绝或设置障碍时可申请平台介入。", "拼多多七天无理由卖家不提供地址，怎么办？", "拼多多不让无理由退换并设置障碍可以找谁处理？"),
   ("freight", "质量退货运费", "拼多多因质量问题退换货，申请页上传问题凭证且平台审核通过后，全部运费由商家承担。", "拼多多质量问题退货，谁出运费，要什么凭证？", "拼多多退货无忧的商家承担运费需要经过哪些步骤？"),
   ("deadline", "维权申请期限", "拼多多该计划要求在下单或拼单成功后至确认收货后15天内发起投诉或售后；逾期平台可不受理。", "拼多多消保计划的维权期限到确认收货后多久？", "拼多多确认收货后何时应申请投诉售后？"),
   ("purpose", "真实消费范围", "拼多多该计划面向真实消费需求，不包括知假买假、牟利性打假或恶意购买维权等扰乱秩序情形。", "拼多多消保计划适用于牟利性打假吗？", "拼多多消费者保障计划对购买目的有要求吗？"),
  ]),
 ("pdd-help", "拼多多官方帮助中心：退换货流程与规则", "pinduoduo", "platform",
  "https://www.yangkeduo.com/home/help/", None, [
   ("contact", "先联系再寄回", "拼多多帮助页建议先联系商家或客服获取退货地址与流程；未联系自行退货导致商家收不到商品，可能不予处理。", "拼多多能不联系卖家就直接寄回商品吗？", "拼多多售后退货应该先拿到哪些信息？"),
   ("cod", "退货到付", "拼多多帮助页不支持未经协商的退货到付；未协商就到付，应取回重寄，相关运费买家承担。", "拼多多没有和商家商量，能用到付退货吗？", "拼多多擅自到付被退回，重新寄的费用谁出？"),
   ("amount", "退款金额", "拼多多帮助页规定，申请退款金额不得高于实际付款金额。", "拼多多申请退款能超过订单实付款吗？", "拼多多退款金额上限按什么金额计算？"),
   ("personal", "个人原因运费", "拼多多七天无理由退货按该帮助页由买家承担退回运费；质量、发错或丢件导致退换货的运费由商家承担。", "拼多多没有质量问题，只是不喜欢，寄回运费谁付？", "拼多多发错货退换产生运费，商家要付吗？"),
   ("exchange", "换货处理时限", "拼多多帮助页规定，商家收回换货商品后应在三天内安排换货；签收次日零时起满72小时，逾期可申请平台介入。", "拼多多卖家收到换货商品后多久要重新发出？", "拼多多换货的三天时限从什么时候开始？"),
  ]),
 ("jd-cancel", "京东取消订单及退货退款说明：自营部分", "jd", "self_operated",
  "https://help.jd.com/user/issue/130-256.html", None, [
   ("before-outbound", "未出库取消审核", "京东自营在线支付订单未出库前提交取消申请，帮助页说明为30分钟后完成退款审核，并非承诺银行卡同时到账。", "京东自营未出库就取消，退款审核多久完成？", "京东取消订单30分钟是审核完成还是银行卡到账？"),
   ("warehouse", "退库后退款", "京东自营商品出库后取消或拒收，需待商品退库后系统退款；拒收后系统自动提交取消申请。", "京东自营已经出库再取消，什么时候退款？", "京东自营拒收后需要等退库才能退款吗？"),
   ("original", "退款路径", "京东在线支付及POS刷卡订单涉及银行信息时按原卡原退；企业转账需客服确认，退回原支付公司账户。", "京东自营公司转账支付的订单退款到哪里？", "京东自营银行卡付款退款可以改到别人的卡吗？"),
   ("ecard", "京东卡退款", "京东卡或京东E卡支付部分不直接兑换现金，帮助页说明约一个工作日返还原卡；周期可能受相关机构影响。", "京东自营用E卡买的商品退货能换成现金吗？", "京东E卡付款取消后钱退回哪里？"),
  ]),
 ("jd-coupon-return", "京东拒收后优惠券返还说明", "jd", "platform",
  "https://help.jd.com/user/issue/130-481.html", None, [
   ("unsplit", "未拆单退券", "京东使用京券或东券的未拆分订单，取消后系统自动返还相应优惠券。", "京东自营订单没拆单，取消后京券东券返还吗？", "京东第三方未拆分订单取消，普通优惠券怎么退？"),
   ("split-jing", "拆单京券", "京东京券订单拆分后须取消全部子单；限品类、店铺京券返还，平台专享京券不返，全品类京券由系统判断返等值京豆。", "京东自营拆单后全部取消，平台专享京券退吗？", "京东第三方拆单京券全部子单取消，全品类券怎么处理？"),
   ("split-dong", "拆单东券", "京东东券订单拆分后取消全部子单，系统自动返还相应东券。", "京东自营拆分订单的东券要全部子单取消才返吗？", "京东第三方拆单后全部取消，东券如何返还？"),
   ("promotion", "活动券例外", "京东促销活动领取的优惠券，取消后是否返还以具体活动规则为准，也可咨询客服。", "京东自营促销领的券取消后一定退吗？", "京东第三方活动优惠券是否退回要查哪里？"),
  ]),
 ("jd-luxury", "京东奢侈品类自营售后服务细则", "jd", "self_operated",
  "https://help.jd.com/user/issue/325-927.html", None, [
   ("condition", "适用品类与完好", "京东自营奢侈品无理由退货须在规定七日内、商品及附件完好且页面支持；例外及特殊规则以详情页为准。", "京东自营奢侈品七天无理由有什么限制？", "京东自营奢侈品页面不支持七天退货，还能直接无理由退吗？"),
   ("packaging", "原包装与附件", "京东该奢侈品细则要求未使用、品相全新，附件标识及外包装完整；缺原包装无法办理七天无理由退货。", "京东自营奢侈品的盒子丢了还能无理由退吗？", "京东自营奢侈品完好标准对包装和吊牌有什么要求？"),
   ("gifts", "赠品与发票", "京东该奢侈品退货应一并返回附件、发票、包装和赠品，电子发票无需返回；赠品缺失影响全额退款。", "京东自营奢侈品退货赠品丢了会影响退款吗？", "京东自营奢侈品退货电子发票也要寄回吗？"),
   ("underwear", "贴身衣物例外", "京东该奢侈品细则中的贴身衣物出于卫生考虑，签收后非质量问题不接受退换货。", "京东自营奢侈品内衣签收后不喜欢能退吗？", "京东自营奢侈品细则里贴身衣物非质量退换有什么限制？"),
  ]),
 ("jd-building", "京东家装建材类第三方售后细则", "jd", "marketplace",
  "https://help.jd.com/user/issue/330-1030.html", None, [
   ("custom", "定制与施工结余", "京东第三方家装建材定制商品不支持七日无理由；施工结余能否退货须看商品详情页。", "京东第三方建材施工剩下的材料都能退吗？", "京东第三方定做的建材能直接七天无理由退吗？"),
   ("quality", "质量举证与运费", "京东第三方建材质量问题须提供证据，经卖家确认后接受退换货，按审核的地址及方式妥善寄回，运费卖家承担。", "京东第三方建材质量问题退货要先做什么？", "京东第三方建材质量问题已确认，退货运费谁付？"),
   ("difference", "色差与尺寸误差", "京东第三方建材因拍摄、光线、显示器形成的色差，以及相关标准允许范围内的测量误差，不归入该细则的质量问题。", "京东第三方建材照片色差一定算质量问题吗？", "京东第三方建材尺寸在标准允许误差内算质量问题吗？"),
   ("description", "描述不符责任", "京东第三方建材因卖家原因尺寸错误或样式、材质、服务与页面不符，卖家应履约；因此退换货的运费卖家承担。", "京东第三方建材材质与详情不符，退货运费谁出？", "京东第三方建材卖家写错尺寸导致退货由谁付运费？"),
  ]),
 ("suning-seven", "苏宁易购主站七天无理由退货规范", "suning", "platform",
  "https://rule.suning.com/ruleInfo/ruleInfoDetail/GZ100003631.htm", None, [
   ("scope", "主站适用范围", "苏宁该规范适用于主站；特卖、苏宁拼购、苏宁国际另有规则，不直接适用本规范。", "苏宁主站七天无理由规则能直接套到拼购和国际吗？", "苏宁主站退货规范适用哪些业务范围？"),
   ("clock", "七天计时与完好", "苏宁带七天无理由标识的商品，签收次日零时起满168小时内、商品完好可申请；合理查验开包装不影响完好。", "苏宁为了检查商品打开包装就不能无理由退了吗？", "苏宁七天无理由的起算时间和完好要求是什么？"),
   ("freight", "包邮及满额包邮", "苏宁无理由退货，包邮商品买家仅付退回运费；未包邮或部分退货后不满足包邮条件时买家承担所有邮费，另约从约。", "苏宁凑单包邮后退掉一件不满足条件，运费怎么算？", "苏宁主站包邮商品无理由退回，要承担发货运费吗？"),
   ("exceptions", "不适用及确认", "苏宁该规范排除定制、鲜活易腐、数字化及报刊等商品；部分拆封、激活或试用后不宜退的商品须购买时确认。", "苏宁定制和鲜活易腐商品是否适用七天无理由？", "苏宁部分激活后贬值商品不适用无理由需要什么确认？"),
   ("promise", "更优承诺", "苏宁鼓励商家提供更长退货期限或更优完好标准；低于法律或平台要求、降低消费者权益的承诺无效。", "苏宁商家能自行把七天退货缩短到三天吗？", "苏宁商家承诺更长的无理由退货期限可以吗？"),
  ]),
]

# Colloquial queries omit the exact clause headings and add distracting facts.
CHALLENGES = [
 ("taobao-consumer", "address", "淘宝店给我的收件人电话是空号，包裹送不到又坏了，这笔损失算谁的？"),
 ("taobao-consumer", "promise", "淘宝店下单前说碎了就再寄一份，真碎了却说那只是宣传，能这样吗？"),
 ("taobao-consumer", "description", "淘宝店客服聊天说是真皮，收到却是合成革，聊天内容是否也属于应如实描述的渠道？"),
 ("taobao-seller", "closure", "淘宝店已经不营业了，我半年前买的机器还在质保，商家能直接不管吗？"),
 ("taobao-seller", "orders", "淘宝卖家退出平台了，但我仍要求履行退店前订单，协议怎么规定？"),
 ("tmall-seven", "clock", "天猫周一下午收的包裹，无理由退货的倒计时从周一下午就开始吗？"),
 ("tmall-seven", "freight", "天猫那件衣服本来免邮，我收到觉得不好看要寄还，商家说连寄来的钱也要扣，可以吗？"),
 ("tmall-seven", "quality", "天猫页面说是纯棉，寄来的成分不是，我寄还的快递钱应该找谁？"),
 ("tmall-warranty", "limit", "天猫给这个商品买的保修，算能修多少钱时，另外付的服务费和快递钱也计入吗？"),
 ("tmall-warranty", "service-fee", "我是天猫卖家，顾客已经把带保修的订单退掉，平台扣的那笔保障服务钱还返给店铺吗？"),
 ("tmall-merchant", "invoice", "天猫标价500，优惠后只付了400，店铺票上应该写哪个数？"),
 ("tmall-merchant", "category-fee", "天猫店铺没有过错，客户退款退货办完后，按类目扣给平台的钱还回来吗？"),
 ("pdd-protection", "freight", "拼多多买的东西确实坏了，我拍照片在申请页上传并通过审核，来回寄的钱谁掏？"),
 ("pdd-protection", "authenticity", "拼多多买到了假的品牌货，我想要十倍补偿，是否还得看页面有没有对应承诺？"),
 ("pdd-protection", "deadline", "拼多多拼成后我手动点了收货，现在还能向平台投诉到哪一天？"),
 ("pdd-help", "cod", "拼多多我没和店里打招呼就让快递到那边收钱，被拒绝了，这次还得自己取回重寄吗？"),
 ("pdd-help", "exchange", "拼多多我寄回去换另一件，对面签收后一直没有重发，什么时候能让平台处理？"),
 ("jd-cancel", "warehouse", "京东自营包裹到了我没接，订单是不是要等东西回到仓库才退钱？"),
 ("jd-cancel", "ecard", "京东自营用礼品E卡抵的部分，退掉订单后能不能直接提现到储蓄卡？"),
 ("jd-coupon-return", "split-jing", "京东自营那单分成三个包裹，全部取消了，为什么平台专享京券没给我？"),
 ("jd-luxury", "packaging", "京东自营买的奢侈品牌包没用过，但原来的盒子扔了，还能按无理由退吗？"),
 ("jd-luxury", "gifts", "京东自营奢侈品主商品要退，随单送的小礼物找不到了，还能拿全款吗？"),
 ("jd-building", "custom", "京东第三方家里装修，工人只用了八成材料，剩余的店铺都必须收回退款吗？"),
 ("jd-building", "difference", "京东第三方建材尺寸差一点，但仍在标准容许范围内，能按品质缺陷处理吗？"),
 ("suning-seven", "freight", "苏宁原本凑够金额不用付快递钱，退掉一部分后凑不够了，寄来那趟也要我出吗？"),
 ("suning-seven", "clock", "苏宁我只把盒子打开检查，没有不合理使用，商家能一概说不完整不给退吗？"),
]


def main():
    path = Path("knowledge/retailops-policies.json")
    corpus = json.loads(path.read_text(encoding="utf-8-sig"))
    added = {row[0] for row in SOURCES}
    corpus["documents"] = [d for d in corpus["documents"] if d["id"] not in added]
    questions = []
    for index, (doc_id, title, platform, scope, url, effective, clauses) in enumerate(SOURCES):
        corpus["documents"].append({
            "id": doc_id, "title": title, "platform": platform, "business_scope": scope,
            "source_type": "official_public_policy", "source_url": url,
            "version": "curated-2026-11-20-v2", "retrieved_at": "2026-11-20",
            "effective_from": effective, "effective_to": None, "status": "active",
            "content_kind": "curated_summary",
            "clauses": [{"id": doc_id + "-" + c[0], "title": c[1], "text": c[2], "topics": []} for c in clauses],
        })
        # Whole source documents are assigned to one question split. Both wording
        # variants stay together; corpus documents remain available to retrieval.
        split = "test" if index % 3 == 1 else "dev"
        for suffix, heading, text, *queries in clauses:
            for variant, query in enumerate(queries):
                qscope = scope if platform == "jd" else "platform"
                if platform == "jd" and scope == "platform":
                    qscope = "self_operated" if "自营" in query else "marketplace"
                questions.append({"id": f"expanded-{doc_id}-{suffix}-{variant}",
                                  "query": query, "platforms": [platform], "business_scope": qscope,
                                  "category": "retrieval", "split": split,
                                  "source_group": doc_id, "expected_status": "completed",
                                  "expected_platforms": [platform],
                                  "expected_chunk_ids": [f"{doc_id}::{doc_id}-{suffix}"],
                                  "expected_clause_ids": [doc_id + "-" + suffix]})
    path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_doc = {d["id"]: d for d in corpus["documents"]}
    group_splits = {q["source_group"]: q["split"] for q in questions}
    for n, (doc_id, suffix, query) in enumerate(CHALLENGES, 1):
        d = by_doc[doc_id]
        scope = d["business_scope"] if d["platform"] == "jd" else "platform"
        if scope == "platform" and d["platform"] == "jd":
            scope = "self_operated" if "自营" in query else "marketplace"
        questions.append({"id": f"challenge-{n:03}", "query": query,
                          "platforms": [d["platform"]], "business_scope": scope,
                          "category": "colloquial_retrieval", "split": group_splits[doc_id],
                          "source_group": doc_id, "expected_status": "completed",
                          "expected_platforms": [d["platform"]],
                          "expected_chunk_ids": [f"{doc_id}::{doc_id}-{suffix}"],
                          "expected_clause_ids": [doc_id + "-" + suffix]})
    qpath = Path("benchmarks/knowledge/expanded.json")
    qpath.write_text(json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(corpus["documents"]),
                      "clauses": sum(len(d["clauses"]) for d in corpus["documents"]),
                      "new_questions": len(questions),
                      "questions_sha256": hashlib.sha256(qpath.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
