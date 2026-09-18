# 订车回读与提交：写出去的是业务凭证，所以只许一种情况重写

日期：2026-09-18。承接 [29-payments-company-banks-parity.md](29-payments-company-banks-parity.md)。
线上基准 `501589f0`。第 3 批的第四件，订车链的核心两件。

## 一、搬了什么

| 新平台 | 线上 | 行数 | 干什么 |
|---|---|---|---|
| `dms_booking_readback.py`（**新增**） | `mrerp_dms_booking_readback.py` | 358 | 回读的字段契约 + 定因阶段（零 IO 纯函数叶子） |
| `dms_booking_submit.py`（**新增**） | `mrerp_dms_booking_submit.py` | 344 | 只提交一次，核对落库那张单才敢报成功 |

两份都与线上**逐行等价**（做完 `cmp_dms.py` 比对：readback **0 行差异**，
submit 只差 import 路径、类名约定与日志文案）。

## 二、`readback`：三层判据（2026-09-12 生产事故的直接产物）

事故：单号 `BK000002609000006` **写入成功**、管理员按单号确实查得到，系统却报
`ERR_DMS_BOOKING_OUTCOME_UNKNOWN`。原因是上一版把表单里**上百个字段逐个相等**当
"单据存在"的判据 —— 任何一个显示镜像字段（名称、格式化文本、DMS 自行规范化的
非关键字段）不一致就得到假阴性。

改成三层，**顺序即优先级**：

| 层 | 内容 | 不一致时 |
|---|---|---|
| 1 身份 | `idsel` + 单号/客户/身份/顾问/车/颜色 | `identity_mismatch`，换候选 |
| 2 关键 | 影响归属/金额/付款/交车的 **ID 与金额**（含组织两侧、审批经理链） | `critical_mismatch` → 宁可 UNKNOWN |
| 3 展示镜像 | 名称、格式化文本、地址文本 | **只记脱敏告警（字段名，不含值）**，不推翻结论 |

单号等价单独一条：DMS 把 `BK000002609000007` 存成 `txtprefixautonum='BK'` +
`txtdocno='000002609000007'`，**只比一格必得假阴性**；但也不能反过来用正则猜前缀
（前缀不必是 ASCII 字母、不必两位）。所以只认"逐字相同"与"前缀 + 主体精确拼接"
两条，其余一律判不是这张单。

## 三、`submit`：两条铁律

```python
1. 只有"明确的单号重复"才允许再写一次（顺号 bump，数字主体与 natn 同步前进）
2. 结果没核出来 → 抛 DmsBookingOutcomeUnknown(submitted=True, retry_safe=False)
   —— 调用方保留草稿并禁止新的 POST
```

配套的两个细节：

- **`on_attempt` 必须在跨过写边界之前调用**：POST 之后崩溃，不能让下一个 worker 的
  投递变成第二张业务单据。
- **回读全程只读**（列表搜索 + 详情表单），绝不重发 `drfcbc/new.php`；超时/断连
  可能发生在提交**之后**，此时回读安全、再 POST 不安全。
- 超时/断连/无法核实时，`response_body` 只留 `sha256` 当"对方回过话"的证据，
  **不落原始 HTML/PII**。

## 四、验证（42/42）

```bash
scripts/sample-verify-dms-booking-readback.sh dms   # 合计: 通过 42 / 失败 0
```

关键断言：

| 用例 | 锁住的规则 |
|---|---|
| 前缀 + 主体精确拼接 → 同一张单；错前缀/错主体/缺格 → 不是 | 单号等价 |
| 前缀不是 ASCII 也能拼 | 不做字母假设 |
| 顾问被换 → `identity_mismatch` | 提成归属变了就是另一张单 |
| 金额 `1,000.00` == `1000` | 金额按 `Decimal` |
| 只有展示差异 → 仍 `verified` + 告警**只有字段名** | 不许假阴性，也不许落值 |
| `idsel` / 未知字段不参与比对 | 不放宽 |
| `on_attempt` 在 POST **之前** | POST 后崩溃不能变成第二张单 |
| 明确重复号 → 顺号重写一次 | 唯一允许重写的情形 |
| 非重复 `err::` → 直接报错，**只 POST 一次** | 不许重发 |
| 超时/断连 → `OUTCOME_UNKNOWN` + `retry_safe=False`，且只 POST 一次 | 不谎报成功、不重发 |
| 两张完全匹配 → 报**歧义**，不随便挑一张 | 多命中一律弃权 |
| 证据里只有 `sha256`，没有原始 HTML | 不落 PII |

回归：cards 34、advisor 25、intake 26、draft 21、client 54、booking-foundation 45。

## 五、当前状态：四个件都还是"没有调用方"的地基

`dms_booking_org` / `dms_booking_payload` / `dms_booking_readback` /
`dms_booking_submit` 现在都**还没有调用方** —— 要等 `dms_client_ops` 的订车段改完
才接上。库里跑的还是旧订车路径，没受影响。

## 六、下一批

1. 改 `dms_client_ops` 订车段：接上这四件（`create_booking_via_form` 改成
   原生 autonum 状态 + `submit_booking(... on_attempt=...)` +
   `_booking_write_payload` / `_booking_readback_payload`）；
   顺带修 `txtuserstel` 缺了要抛 `ERR_DMS_ADVISOR_UNMATCHED`（新平台现在填 `"."`）。
2. `dms_docno` 换成线上的严格 autonum 状态机（261 行）。
3. 加 `run_logged_in(authoritative_read=True)`，再把银行目录的"抓取 + 完整度判据"
   一次并进去（见 [29](29-payments-company-banks-parity.md) 第四节）。
4. 订车逐问（线上 9 个文件 ~2800 行）—— 其中 `booking_payments.py` 负责把 LINE 的
   自由文本拆成 DMS 原生槽位（`cheque_no` / `card_type` / `dst_bank_id`…），
   是上一批删掉 `_LEGACY_EXTRA` 之后**唯一的**那个转换点。
