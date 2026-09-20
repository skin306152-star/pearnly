# DMS LINE 字段映射与验证清册

本表属于 [本轮任务](2026-09-20-dms-line-production-diagnosis.md) E22/E27/E28，候选基线 `507af265` 加当前未提交修改。旧线上源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；旧 `services/line_dms/booking_edit.py` 的 CUSTOMER_FIELDS 与当前范围相同。不是任意未来字段自动映射承诺。

入口：`editor/booking_edit.py`、`editor/customer_edit.py` → `contracts/models.py` / `booking/booking_payments.py` → `client/client_intake.py` / `client/client_ops.py` / `contracts/payments.py`。用户编辑先存草稿、轮换确认卡，再经本人销售身份提交；以外部原生 form/read_customer 回读为业务证据。

最新外部证据：第二笔测试单 `BK000002609000021`，模拟器完整人工录入 → 编辑 → 保存 → 重开 → 确认后，`scripts/dms-line-lab-field-readback.py` 独立核对 **104/104** 项通过。包括更改证号/称谓/姓名/生日、地址级联、七个可选地址字段清空并复制到三套地址、全部六种付款明细、身份证附件移除与合成转账凭证保留。未发生真实付款。车型派生属性变化、顾问组织全部槽位和目录非空时的银行选择尚不在这 104 项中。

新增只读证据：`scripts/dms-line-lab-derived-readback.py` 将订单21与原生车型详情、原生顾问组织接口逐值对照，**43/43**通过（车型14、顾问组织24、汇款方5）。使用原生来源而非自身mapper输出。证明这笔订单的派生值真实一致；原生为空的主管/汇款方槽仍为空，不等于那些槽位的非空修改已实测。

## 客户字段

客户地址原生有户籍、联系、寄送三套。现有 LINE 合同提供一套地址，映射三个后缀 `""`、`_ct`、`_sd`；不能把它描述成三套地址均可独立编辑。名称由有效 ID 解析，不接受伪造标签。

| 编辑字段 | 客户原生字段；订车原生字段相同则省略 | 类型/约束 | 已有外部证据及缺口 |
| --- | --- | --- | --- |
| people_id | txtpeopleid | 13 位证号校验 | 编辑合成证号、保存重开与外部回读通过 |
| prefix_id | selprefix；prefixval/txtprefix | 当前称谓 ID/派生名 | 改为คุณ，ID/名称回读通过 |
| name | txtcusname；txtcus | 必填姓名 | 编辑后回读通过 |
| birthday_be | txtbirthday | 佛历日期 | 改为29/02/2531，重开与回读通过 |
| phone | txttel | 必填 | 菜单 1 创建与菜单 2 修改回读通过 |
| house_no | txthousenum | 必填 | 修改后三套地址回读通过 |
| building | txtbuilding | 可清空 | 修改、清空、重开与三套地址回读通过 |
| floor | txtfloor | 可清空 | 修改、清空、重开与三套地址回读通过 |
| room | txtroom | 可清空 | 修改、清空、重开与三套地址回读通过 |
| village | txtvillage | 可清空 | 修改、清空、重开与三套地址回读通过 |
| moo | txtmoo | 可清空 | 修改、清空、重开与三套地址回读通过 |
| soi | txtsoi | 可清空 | 修改、清空、重开与三套地址回读通过 |
| road | txtroad | 可清空 | 修改、清空、重开与三套地址回读通过 |
| province_id / province_name | selprovinces；provincesval/txtprovinces | 省 ID/派生名 | 改为ระยอง/เมืองระยอง/ทับมา/21000，四级ID/名称回读通过 |
| district_id / district_name | seldistricts；districtsval/txtdistricts | 从属省 | 同上 |
| subdistrict_id / subdistrict_name | selsubdistricts；subdistrictsval/txtsubdistricts | 从属区 | 同上 |
| zipcode_id / zipcode | selzipcodes；zipcodesval/txtzipcodes | 从属街道 | 同上 |

客户适配器另支持 `tax_id/tel_work/tel_home/email/line_id/facebook/credit_day`，但不在旧 LINE 编辑合同中。本轮不凭适配器字段擅自增加界面。税号缺省沿用证号；其他原生未编辑字段应保持既有值。

## 订车与派生字段

| 编辑/来源 | DMS 原生字段 | 判据与当前证据 |
| --- | --- | --- |
| answers.place_id | placebookval/txtplacebook | 有效 ID；编辑及回读通过 |
| answers.car_id | carval/txtcar | 有效车型 ID/编码；编辑及回读通过 |
| answers.paint_id | carpaintval/txtcarpaint/carpaintname | 必须属于选定车型；编辑及回读通过 |
| answers.delivery_date_be | txtcardeliverydate | 佛历日期；修改及回读通过 |
| answers.term_id | termsaleval/txttermsale | 当前销售条件；修改及回读通过 |
| answers.regis_id | regisbehalfval/txtregisbehalf | 当前登记类型；修改及回读通过 |
| answers.regis_name | txtregisname | 必填；修改及回读通过 |
| qa.advisor（员工/提成配置） | usersval/txtusers/txtuserstel | 编辑器只读；本人销售归属回读通过 |
| advisor 组织关系 | branch_bookval/txtbranch_book/team_bookval/txtteam_book/branch_sellval/txtbranch_sell/team_sellval/txtteam_sell 与主管槽位 | 原生组织24槽与订单回读一致；空主管槽的非空变更尚未逐个实测 |
| car.extras | carbrandval/txtcarbrand/typecarval/txttypecar/typecardescval/txttypecardesc/gradeval/txtgrade/cargearval/txtcargear/txtmanuyear/enginepowerval/txtenginepower/txtprice | 原生车型14槽与订单回读一致；属性逐个改动后另建单未实测 |
| customer.id | cusval | 实际客户主档；127 回读通过 |

车型/颜色的本轮自建对象已完成新增、改名、车型停用、删除，并在对话/编辑快照/提交前复核看见对应结果；不能据此代表全部主档类型已完成 CRUD。

## 收款与附件

本轮都是测试账套中的单据资料，没有转账、收款或支付动作。金额统一 Decimal；每种渠道至多一条，合计来自各槽位。

| 渠道/字段 | DMS 原生字段 | 当前外部证据 |
| --- | --- | --- |
| cash.amount | txtmoneycash | 25.75 回读通过 |
| transfer.amount | txtmoneytfmon | 0.02 回读通过 |
| transfer.extra.dst_id | 由公司账户解析：txtbusinessnametfmon/txtaccountnumtfmon/txtbanknametfmon/banktfmonval/txtbranchnametfmon | 选择公司账户后五个派生字段回读通过 |
| transfer 派生汇款方 | txtowneraccnametffrom/txtaccountnumtffrom/txtbanknametffrom/banktffromval/txtbranchnametffrom | 非自由编辑；5个原本为空的源字段在订单中仍为空，非空来源尚无外部样本 |
| cheque.amount | txtmoneycheque | 0.03 回读通过 |
| cheque.extra.cheque_no/cheque_book_no/bank_name/bank_id | txtchequeno/txtbooknocheque/txtbanknamecheque/bankchequeval | 合成票号/簿号/银行名回读通过；空目录时ID为空 |
| cashier_cheque.amount | txtmoneycashiercq | 0.04 回读通过 |
| cashier_cheque.extra.cashier_no/cashier_book_no/bank_name/bank_id | txtcashiercqno/txtbooknocashiercq/txtbanknamecashiercq/bankcashiercqval | 合成票号/簿号/银行名回读通过；空目录时ID为空 |
| card.amount | txtmoneycddbc | 0.05 回读通过 |
| card.extra.bank_name/bank_id/card_type | txtbanknamecddbc/bankcddbcval/txttypenamecddbc | 合成银行名/卡种回读通过；空目录时ID为空 |
| other.amount/detail | txtmoneyother/txtdetailother | 3.25 与标记说明回读通过 |
| 付款合计 | txtearnestmoney | 第一笔29.00、第二笔六渠道合计0.21均回读通过 |
| keep_files.id_card | 身份附件槽，名称 สำเนาบัตรประชาชน | 第一笔保留、第二笔移除，实际附件及台账通过 |
| keep_files.slip | 转账凭证附件槽 | 缺凭证拒绝且页面保留输入；合成图片实际上传及台账通过 |

支票/本票/卡的银行只有权威目录为空时允许手填名称；不是读取失败时退回手填。转账必须存在凭证，凭证也必须对应转账。新增字段、目录结构变动或解析缺行要明确报告，不能静默丢字段或选第一项补齐。
