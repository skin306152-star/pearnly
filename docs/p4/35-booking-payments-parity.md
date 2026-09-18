# LINE 付款资料解析：唯一那个"文本 → 原生槽位"的转换点

日期：2026-09-18。承接 [34-booking-attempt-needs-design.md](34-booking-attempt-needs-design.md)。
线上基准 `501589f0`。订车逐问那批的**第一个切片**（也是能完整做完的一个）。

## 一、为什么先做它

[29](29-payments-company-banks-parity.md) 里我删掉了 `dms_payments` 里那个错位的
`_LEGACY_EXTRA`（它偷偷把 `extra["ref"]` 当票据号读），当时写下一句：

> 把 LINE 的自由文本拆成 DMS 原生槽位，是**上一层**的事。

这一批就把那一层补上 —— `dms_booking_payments.py`。补完之后，
"文本怎么拆"**只有一处**在管，`dms_payments` 只认原生槽位名。

## 二、搬了什么

| 新平台 | 线上 | 行数 | 干什么 |
|---|---|---|---|
| `dms_text_fields.py`（**新增**） | `services/line_dms/text_fields.py` | 98 | 一行资料按分隔符分段（零 IO 纯函数） |
| `dms_booking_payments.py`（**新增**） | `services/line_dms/booking_payments.py` | 201 | 解析成原生槽位 + 编辑校验 + 确认卡摘要 |

两份都与线上**逐行等价**（55→55、175→175；差异只有注释标点与 import 排序）。

## 三、这一层的立场：宁可多问一次，也不猜

`split_fields` 一次只用**一类**分隔符，切完必须**精确等于**期望段数，否则回 `None`
让调用方重问。刻意不拆的四类都是"猜错就把钱写错"：

| 输入 | 为什么必须保持原样 |
|---|---|
| `1,000.00` | 千位逗号不是分隔符（`\d,\d` 守卫） |
| `123/456` | 账号里的斜杠（`\d/\d` 守卫） |
| `19/09/2569` | 日期——斜杠切出来段数对不上就不认 |
| `14:36` / `Mr. Somchai` / `19.09` | 冒号不是分隔符；点号后跟空格不拆；"数字.数字"不拆 |
| `A \|  \| C` | 空段不算字段 |

## 四、两个必须分得开的东西

「银行目录**权威为空**（拿到空列表）」与「**取数失败**（缺席 / `None`）」：

- 前者 → 允许这一笔填**手工银行名**（挂 `MANUAL_BANK_FLAG`，只豁免 id 槽位）；
- 后者 → 只能报 `invalid_bank`（**拿不到名录 ≠ 名录里没有**）。

验收里这两条是并列的，防止哪天被"合并简化"成一条。

## 五、报错是"码"不是"话"

`PaymentValidationError.code` 是稳定机器码（`dms_booking.invalid_bank` 之类）。
**文案不在这一层**：老产品把它交给前端包按语义键渲染
（`static/dms-booking-edit/dms-booking-i18n.js`：`dms_booking.invalid_bank` → `masterChanged`
→ 各语言文案）。所以照原样保留码才是对齐 —— 这也是上一批"语言集只有四种"那条道理的
同一种用法：**先看谁负责渲染，再决定这一层该产出什么。**

## 六、验证（42/42）

```bash
scripts/sample-verify-dms-booking-payments.sh dms   # 合计: 通过 42 / 失败 0
```

| 组 | 钉住的规则 |
|---|---|
| 分段（18 条） | 五类分隔符能用；千位逗号/账号斜杠/日期/时间/缩写/小数/**空段**一律不拆；段数对不上就不认 |
| 解析（11 条） | 支票 = 号 + 簿号；刷卡/其它整行；`-` 与空行不算资料；手工银行多一段（支票在末段、卡在首段），`-` 段不认 |
| 校验（11 条） | 6 种错误码各就其位；金额 0 不算付款；转账指向不存在的账户 → `invalid_bank`；齐全时给的是**原生槽位**；**目录空 vs 取数失败**两条并列 |
| 摘要（3 条） | 转账显示"来源 → 到账"；支票含号/簿号/银行；与 DMS 字段同源 |

顺带钉了一条**边界**：规范化结果能直接喂给 `dms_payments.payment_form_fields`
并产出 `txtmoneycheque` —— 两层的接缝是接上的，不是各自为政。

全套 **319** 项：cards 34、advisor 25、intake 33、draft 21、client 77、
booking-foundation 45、booking-readback 42、booking-payments 42。

## 七、下一批

订车逐问剩下的：`booking_qa*`（起问 / 分页 / 付款 / 汇款 / 同步）、`qa_cards` /
`qa_payment_cards`（卡片渲染）、`booking_car_details` / `booking_preflight` /
`booking_ledger`（建单前置与台账）、`menu_flow` / `menu_cards` / `menu_sync` /
`rich_menu` / `login_tickets`（菜单层与登录票据）。
它们会把 `booking_attempt.on_attempt` 的**消费者**带进来 —— 那时再实现登记本体
（见 [34](34-booking-attempt-needs-design.md) 的四条待定契约）。
