# 原生取号状态机 + `client_ops` 订车段接上四件

日期：2026-09-18。承接 [30-booking-readback-submit-parity.md](30-booking-readback-submit-parity.md)。
线上基准 `501589f0`。第 3 批的第五件 —— 到这一步，**订车链才算接上**。

## 一、为什么这两件必须同批

`dms_docno` 换成线上的严格 autonum 状态机后，`next_unoccupied_docno` 就不存在了 ——
而那正是 `client_ops` 旧订车路径在用的函数。所以只能一起改：**换 docno + 改订车段**，
分开做必然留下一段跑不起来的中间态。

## 二、`dms_docno`：从"返回一个字符串"换成原生取号状态机

旧版把 `autonum.php` / `autonumdetail.php` **只读了一半**：拿前缀和位宽，丢掉隐藏字段
`idatndt` / `natn`，异常还吞成 `""`。后果是**计数器永不推进**（线上 2026-09-13 修复的
根因）。新版（158 行，与线上逐行等价）：

| 东西 | 作用 |
|---|---|
| `DMSBookingAutonumState` | 前缀 / 完整号 / 位宽 / `idatndt` / `natn` 五件套 |
| `.body` | 数字主体 = 完整号去掉**精确**前缀（提交到 `txtdocno` 那一栏） |
| `.with_docno()` | 换号的**唯一入口**：把 `natn` 前移"新尾号 − 原尾号"的 delta；会变负数就抛 |
| `scan_unoccupied_docno()` | 只读扫列表，从实际最大流水之后取号（绕过失步的计数器） |
| `autonum_state()` | **任一必需字段缺失/无效即抛** `ValueError`，由调用方 fail closed |

⚠️ 校验刻意**不假定**前缀是 ASCII 字母、不假定它两位 —— 前缀来自配置。

## 三、`client_ops` 订车段接上四件

| 改动 | 为什么 |
|---|---|
| `create_booking_via_form(..., on_attempt=None)` → 走 `scan_unoccupied_docno` + `state.with_docno` + `submit_booking(...)` | 顺号重试与"结果不明不重发"都交给那一层，不再自己搓一个 POST 循环 |
| `_next_booking_docno` 返回状态对象；协议不符 → `ERR_DMS_IMPORT` | 旧版吞异常返回 `""`，是计数器不推进的根因 |
| 新增 `_booking_write_payload` / `_booking_readback_payload` | 提交体带前缀栏 + 数字主体 + 两个隐藏字段；**回读体带完整号**（拿纯数字主体去比对会把正确的单判成不一致） |
| `_apply_booking_form_fields`：顾问电话缺 → `ERR_DMS_ADVISOR_UNMATCHED` | 旧版填一个 `"."`，等于**编一个不存在的电话**推进业务单据 |
| `resolve_booking_payload` 改用 `resolve_booking_org` + `payload_from_refs` | 组织只能按顾问 id 问；旧版拿"分店列表首行"当这个顾问的分店 |
| 表单字段合并 `booking.organization_fields` | 顾问归属带出来的原生组织（两侧分店/组 + 四级主管） |

顺带删掉 `client_ops` 里已经没人用的 `_is_duplicate_docno_error` / `_bump_docno` /
`_BOOKING_DOCNO_MAX_TRIES`（权威源在 `dms_docno`）。

## 四、验证

`scripts/sample-verify-dms-client.sh dms` 从 54 → **77** 项，新增的都是这批的：

| 用例 | 锁住的规则 |
|---|---|
| 取号状态五件套解析正确 | 前缀/完整号/位宽/`idatndt`/`natn` |
| 数字主体 = 完整号去掉精确前缀 | 提交到哪一栏 |
| 换号把 `natn` **同量**前移 | 计数器不发散 |
| `natn` 会变负 → 直接拒 | 不写出坏计数器 |
| 协议不合（字段不足/未启用/前缀空/位宽非正/非整数/号不以前缀开头/尾号非数字/缺 idatndt/natn 负）→ fail closed | 10 种坏形态逐个拦 |
| 未占号扫描：从现有最大流水之后取；候选号本就靠后则不动它 | 绕过失步计数器 |
| 列表取数失败 → 原样返回候选号（delta=0） | 不为"顺号"去猜 |
| 提交体：前缀栏 + 数字主体 + 两个隐藏字段，且**不**把完整号塞进 `txtdocno` | 计数器才推进 |
| 回读体用完整号 | 不误判 |
| 顾问没有电话 → 拦；有电话 → 进 `txtuserstel` | 不编电话 |
| 组织字段整包进表单 | 归属正确 |

全套 **270** 项：cards 34、advisor 25、intake 26、draft 21、client 77、
booking-foundation 45、booking-readback 42。

## 五、现在订车链是**接通**的

`client_ops.create_booking_via_form` → `dms_docno`（原生取号）→
`dms_booking_payload` / `dms_booking_org` → `dms_booking_submit` →
`dms_booking_readback`。四件地基不再是"没有调用方"。

**但还没做真机**：`sample-verify-dms-booking-real.sh` 会往真 DMS 写单，对外部系统的
写入要用户明确授权，本批没跑。这一批的全部证据都在**纯逻辑层**（假管道）。

## 六、下一批

1. 加 `run_logged_in(authoritative_read=True)`，再把银行目录的"抓取 + 完整度判据"
   一次并进去（见 [29](29-payments-company-banks-parity.md) 第四节）。
2. `booking_attempt`（线上：原子登记提交意图 + 草稿归属核验，防并发重复）——
   `create_booking_via_form` 的 `on_attempt` 现在还没有人传。
3. 订车逐问（线上 9 个文件约 2800 行）：`booking_qa*` / `qa_cards` / `qa_payment_cards`
   / `booking_payments`（LINE 文本 → 原生槽位的那一层）/ `text_fields`。
4. 查询流 / 菜单层 + Rich Menu + 登录票据 / 字段修正（浏览器编辑页）/ 改写审批。
