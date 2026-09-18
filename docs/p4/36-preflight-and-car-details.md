# 提交前权威复核 + 车型规格卡

日期：2026-09-19。承接 [35-booking-payments-parity.md](35-booking-payments-parity.md)。
线上基准 `501589f0`。订车逐问那批的第二个切片。

## 一、搬了什么

| 新平台 | 线上 | 行数 | 干什么 |
|---|---|---|---|
| `dms_booking_preflight.py`（**新增**） | `services/line_dms/booking_preflight.py` | 62 | 提交前按 DMS **当下**主档复核选择（只读） |
| `dms_booking_car_details.py`（**新增**） | `services/line_dms/booking_car_details.py` | 25 | 把映射好的车型规格摊在确认卡旁边 |

`booking_preflight` 与线上逐行等价（39→39，只差 import 与类名）；`booking_car_details`
是**故意不同**的（见第三节）。

## 二、为什么复核必须走权威只读

销售账号常看不到车型/颜色/收款银行的**全表**。拿这份不完整的视图去复核，会把
「DMS 里明明还在」的选择判成"主档变更"——用户被迫重选一个其实没变的东西。
所以复核走权威只读会话（`dms_admin_read`），整块只解析一次管理员登录。

本模块**只读、只出结论**，不写任何单据：建单/客户写入/附件挂载仍在**原销售 transport**
上执行且只提交一次。颜色读不到（回 `None`）按 `ERR_DMS_MASTER_UNAVAILABLE` 处理 ——
**不拿空表冒充"这车没颜色"**（这条与 `dms_masters_cache` 的一贯口径一致）。

## 三、车型规格卡：标签进包，不写死

线上把 8 个泰文标签直接写在代码里：

```python
_FIELDS = ((4, "ยี่ห้อ"), (6, "ประเภทรถ"), (8, "รายละเอียดรถ"), ...)
```

按本项目既定的边界（见 `dms_cards.py` 顶部"文案不进代码"），**卡片上用户看得见的字
一律进语言包**。所以这里改成 `_FIELD_NAMES = ((4, "brand"), ...)` + 从包取词，
8 条标签进 `translations/th.csv` 的 `dms.card.car.*`。

这样改的好处不是"好看"：改一个泰文错别字是改数据文件、不用发版；而"防漏键"守卫
（`sample-verify-dms-cards.py` 第 9 节）也把 `car.*` 这一族纳进来了，漏一条会自己报出来。

## 四、验证

`sample-verify-dms-cards.sh dms` 从 34 → **43**，新增：

| 用例 | 锁住的规则 |
|---|---|
| 标签取自语言包、正文不露裸 key | 文案在包里 |
| 取不到的列直接不出，不留空槽 | 空槽比缺行更让人误会 |
| 只有前几列时只出对应那几行 | 列号映射对得上 |
| 车不在快照里 → 空（不编） | 不猜 |
| 主档对不上 → **透传 reconcile 的结论**（非 ok + 字段 + 错误码） | 复核层不自己改判 |
| 顾问没了 → 报的是 `advisor`（不把根因说成别的） | 说清是哪个字段变了 |
| 颜色取数失败 → `ERR_DMS_MASTER_UNAVAILABLE` | 空表 ≠ 没颜色 |

写这几条时我自己踩了两个坑：列号数错（把"只出两行"写成三行）、以及忘了
`reconcile` 是**按自己的顺序**逐项查（先顾问、再店、…、最后才到车），
夹具缺店就会先报店。后者改成断言"透传结论"而不是"一定报车"——**断言要写它真正保证的事**。

全套 **328** 项：cards 43、advisor 25、intake 33、draft 21、client 77、
booking-foundation 45、booking-readback 42、booking-payments 42。

## 五、下一批

订车逐问剩下的：`booking_qa*`（起问 / 分页 / 付款 / 汇款 / 同步）、`qa_cards` /
`qa_payment_cards`、`booking_ledger` / `booking_edit` / `booking_flow`、
菜单层那组（`menu_flow` / `menu_cards` / `menu_sync` / `rich_menu` / `login_tickets`）。
菜单层会把 `booking_attempt.on_attempt` 的消费者带进来（见 [34](34-booking-attempt-needs-design.md)）。
