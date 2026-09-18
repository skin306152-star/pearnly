# 订车逐问那一批的形状：它是一簇，不能切小片

日期：2026-09-19。承接 [36-preflight-and-car-details.md](36-preflight-and-car-details.md)。
本文是**调查结论**：订车逐问这批与前几批不同，得整簇一起做，不能像前面那样挑小片。

## 一、先量规模

`services/line_dms/` 里还没搬的：

| 文件 | 行数 | 依赖落在哪 |
|---|---|---|
| `qa_cards.py` | **489** | 卡片与文案的**中枢**（`_msg` / `_option_text` / `_pick_rows` / 一堆 `TXT_ASK_*`） |
| `booking_qa.py` | 447 | 逐问状态机 |
| `booking_flow.py` | 463 | 建单执行体 |
| `booking_edit.py` | 415 | 逐问期编辑 |
| `menu_cards.py` / `menu_flow.py` / `menu_sync.py` | 251 / 225 / 114 | 菜单层 |
| `rich_menu.py` / `login_tickets.py` | 243 / 190 | Rich Menu 与登录票据 |
| `booking_qa_transfer.py` / `booking_qa_payment.py` / `booking_qa_pages.py` | 218 / 143 / 118 | 逐问分页 |
| `booking_ledger.py` | 78 | ⚠️ **直接用 `core.db`**（老平台那套 Postgres） |
| `booking_qa_start.py` | 68 | ⚠️ 要 LINE 回复层（`_out._send` / `_thr`） |
| `qa_payment_cards.py` | 50 | ⚠️ 全部取自 `qa_cards` |
| `booking_qa_sync.py` | 50 | `masters_cache` + `master_contract` 的快照函数 |
| `booking_car_details.py` / `booking_preflight.py` | 25 / 62 | ✅ 已搬（36 号） |
| `booking_payments.py` / `text_fields.py` | 201 / 98 | ✅ 已搬（35 号） |

## 二、为什么这次切不出小片

我本来想挑最小的那个（`qa_payment_cards.py`，50 行）先做。查完发现：

```python
from services.line_dms.qa_cards import _msg, TXT_ASK_CARD_REF, TXT_ASK_CHEQUE_REF, ...
from services.line_dms.qa_cards import _msg, _option_text, _pick_rows, ask_pay_ref
```

**它一行代码都离不开 `qa_cards`。** 而 `qa_cards` 是 489 行的中枢：卡片构造器
（`_msg` / `_option_text` / `_pick_rows`）+ 各渠道的问法文案。
剩下的小文件也都挂在这簇里（`booking_qa_start` 挂 LINE 回复层、`booking_qa_sync` 挂
`masters_cache`、`booking_ledger` 直接 SQL）。

所以这一批的**入口是 `qa_cards` 自己**，而它 489 行里绝大部分是**卡片文案** ——
按本项目规矩要全部进语言包（`dms.card.*`）。这不是尾巴上加一笔的量。

## 三、两个"不能照抄、要先定设计"的（与 [34](34-booking-attempt-needs-design.md) 同类）

### `booking_ledger.py` 直接用老平台的 Postgres

```python
from core import db

def log_booking(user_id: str, ep: dict, payload: dict, result: dict) -> None:
```

它把订车结果写进老平台的表。新平台没有那套 `core.db`（会话/台账都在 Frappe 侧）。
**照抄会得到一段连不上的代码**；要落地得先定"这条台账在新平台住哪张表、用哪个键"。

### `booking_qa_start.py` 要 LINE 回复层

`_out._send` / `_thr` 是 LINE 的发送与线程包装；新平台的对应物在
`pearnly_core/line.py` + `dms_transport.py`，但不是同一个形状。同样要先定接口。

## 四、建议的下一步（一次一批，别切）

1. **`qa_cards`（489 行）**：卡片构造器照搬，文案进包（`dms.card.qa.*`）；
   同时把"防漏键"守卫扩到这一族。做完它，`qa_payment_cards` / `booking_qa_start` /
   `booking_qa_pages` / `booking_qa_payment` 才是能逐个落地的小件。
2. 再定 `booking_ledger` 的落点（新表 or 复用现有台账）——**这需要一句产品决定**：
   台账要不要保留、给谁看。
3. 菜单层（`menu_cards` / `menu_flow` / `menu_sync` / `rich_menu` / `login_tickets`）——
   它会带出 `booking_attempt.on_attempt` 的消费者。

## 五、当前状态

未提交代码改动（本轮只做调查）。上一轮成果仍在：全套 **328** 项验证全绿。
