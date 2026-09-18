# 订车逐问状态机本体：能跑起来了（差一个入口）

日期：2026-09-19。承接 [41-booking-qa-steps.md](41-booking-qa-steps.md)。线上基准 `501589f0`。

41 号落了四个分件，这批落**上游那台机器**和它的开局器。到这一步，订车逐问这条链
从"开局 → 逐问 → 收款资料 → 预览卡"整条都通了 —— 缺的只是**谁来开这一局**（菜单层）。

## 一、搬了什么

| 新平台 | 线上 | 行数 | 说明 |
|---|---|---|---|
| `dms_booking_qa.py`（新增） | `booking_qa.py` | 447 | 逐问状态机：文本 / 图片 / postback 三个入口 + 每步发问 |
| `dms_booking_qa_start.py`（新增） | `booking_qa_start.py` | 68 | 开一局：冻结实时主档 + 定死顾问归属 |

函数名与参数个数与线上**逐一对齐**（30 个 / 1 个，脚本校验过）。

## 二、这批守住了哪几条规矩

· **不抢上层**：全局命令（`เริ่มใหม่` / `เมนู`）与"当前不是 `booking_qa` 态"一律返 `False`，
  交上层路由。
· **串步按钮不跳步**：动作与当前步对不上（旧卡残留、翻历史卡点的按钮）→ 吃下、
  原步重问，状态一个字段都不推进。
· **翻页令牌永不写进 `answers`**：它只改"在看第几页"。
· **开局两件不能省的事**：实时抓一份完整主档（不走缓存）+ 定死顾问归属；抓不到主档
  或顾问认不出就**当场收场并清会话**，不带着残缺主档往下走。
· **收官轮换 nonce**：旧预览卡的按钮 postback 从此作废，确认/取消仍由既有订车流程
  处理（`consume_nonce` 守卫）。

新平台的接口差异与 41 号同源：async → 同步；会话按 **LINE 用户**存
（`line_session` 的键就是 `line_user_id`，一个站点一家租户）。

顺带发现：新平台的会话层**早就预留了 `booking_qa` 这个态**（TTL 120 分钟，
见 `pearnly_core/line_session.py` 的 `_STATE_TTL_MINUTES`）—— 也就是说这批不是
"临时加一个态"，而是把早就留好的位置填上。

## 三、一处**容易踩的接口形状**（已在代码里写明）

注入的 `masters` 读取器在同一个文件里有**两种调用形状**，老产品就是这样，照抄保留：

· `handle_text` 那条路：`masters_reader` 造的读取器 → `(line_user_id, qa, key)`；
· `pick_bank`（状态机直接调）：就是 `dms_booking_qa_sync.masters` 本身 →
  `(tenant_id, line_user_id, qa, key)`。

我第一遍按前一种形状写了 `pick_bank`，接线时会 `TypeError`；已改回线上形状，并在
`dms_booking_qa_transfer` 顶部把这两条路写清楚。**别"顺手统一"掉它**。

## 四、验证：`sample-verify-dms-booking-qa-machine.py`（33 项）

做法：只把「读会话 / 存会话 / 发卡 / 收尾」四处换成假的，**主档读取用真的**
（`dms_booking_qa_sync.masters` 走会话里那份快照），所以跑的是
状态机 → 分件 → 主档 的真实调用链；不连 DMS、不写库。

还加了一道**护栏**：把 `master_shared.get_session_masters` / `get_session_paints`
换成"一被调到就断言失败"。用例漏配快照时会立刻炸，而不是悄悄去登录一次真实 DMS
（写用例时就真被拦下两次 —— 一次是漏配颜色快照，一次是漏配车型）。

钉住的是：采集链路每一跳（取车点 → 车型搜索 → 颜色 → 交车日 → 条件 → 注册名义 →
付款渠道）、凭证图的三条分流、「现金声明只在开局那步能跳过凭证」、日期解析失败不写坏
答案、旧会话的 `pay_src*` 步骤归一、以及收官写 `booking_review` 时保留上层 payload。

## 五、还差什么

| 文件 | 行数 | 卡在哪 |
|---|---|---|
| 菜单层（`menu_cards` / `menu_flow` / `menu_sync`） | 251 / 225 / 114 | **入口**：决定建档后是否接着订车 |
| `rich_menu.py` / `login_tickets.py` | 243 / 190 | Rich Menu 与登录票据 |
| `booking_edit.py` / `booking_flow.py` | 415 / 463 | 逐问期编辑、建单执行体 |

`dms_flow.py` 顶部那句"订车逐问未搬"已改成实话：**搬完但未接线**，接线前这一层不会被
调用。`booking_attempt` 的四条契约仍待定（[34](34-booking-attempt-needs-design.md)），
菜单层会带出它的消费者，到时候一起定。

全套验证：**449 项**全绿（13 个脚本）。
