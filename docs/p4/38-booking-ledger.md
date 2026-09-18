# 订车台账：落到新平台的推送流水表

日期：2026-09-19。承接 [37-booking-qa-batch-shape.md](37-booking-qa-batch-shape.md)。
线上基准 `501589f0`。**用户拍板"要"**，所以这批把台账做出来。

## 一、台账是什么（先说人话）

每往 DMS 推一次单就留一行："谁、什么时候、推了什么、结果如何"。
用途是**对账与排障**：销售说"我昨天那单呢"能查到；推失败了能看出是哪一步；
**顾问 id 与名字一起记**，月底核提成归属不用回查主档。

## 二、落点：新平台已经有这张表

老产品往 `erp_push_logs` 表里塞（`db.insert_push_log(...)`，13 个位置参数）。
新平台**早就有对应物**：`Pearnly Push Log`（`pearnly_core/push_log.py`），
字段是迁移时重新设计过的，而且 DMS 建档那条链已经在写它。

所以我上一条说的"要不要保留台账、是个产品决定"**把问题说大了** ——
表本来就在，要定的只是"订车这条也写进去吗"。你答"要"，那就照 `dms_flow._write_push_log`
的既有模式写一条。

## 三、搬了什么

| 新平台 | 线上 | 说明 |
|---|---|---|
| `dms_booking_ledger.py`（**新增**） | `services/line_dms/booking_ledger.py` | 内容照搬，落点改成 `Pearnly Push Log` |
| `dms_qa_util.deposit_total`（补） | `qa_cards.deposit_total` | 各渠道订金求和（`Decimal`，不用 float） |

分成三个函数，前两个是**纯函数**（可测、不碰库）：

```
booking_log_request_body(qa)      → 推了什么（摘要）
booking_log_response_body(result) → 落库结果
log_booking(endpoint, payload, result, actor)  → 真正写一行
```

## 四、两处**有意**与老产品不同

| | 老产品 | 新平台 | 为什么 |
|---|---|---|---|
| `trigger` 字段 | `line_dms` / `id_card` | **`line`** | 本表选项只有 `manual/line/auto`。照抄老值会**直接校验失败** —— 这个坑 `dms_flow._write_push_log` 里已经踩过并留了注释 |
| 正文里的 `trigger: "line_dms"` | — | **保留** | 它是"来源标记"，与建档那条台账同一口径，记录页才能按同一个口径筛 |

`business_key` 放 **BK 单号**（建档那条放的是客户号）。

## 五、两条必须保持的规矩

1. **结果如实记**：成功记 `success`/200，失败记 `failed`/0 **+ 错误码** —— 台账不是宣传栏。
2. **台账失败不能影响业务结果**：写台账的异常一律吞掉并留日志（用户那单已经推成功了）。

## 六、验证（17/17）

```bash
scripts/sample-verify-dms-booking-ledger.sh dms   # 合计: 通过 17 / 失败 0
```

这个脚本**不写真库**：给 `log_booking` 里那句函数内 `import frappe` 换上一个假模块，
既能验"写了哪些字段"，又不会往站点库插行（Frappe 的模块级 `Local` 不让直接打桩，
所以用替换 `sys.modules["frappe"]` 的办法，退出时换回）。

钉住的：

| 用例 | 锁住的规则 |
|---|---|
| 订金合计用 `Decimal`（0.1+0.2 正好 0.3） | 金额不用 float |
| 客户/车/颜色/店/付款方式/登记人都在 | 对账要的信息齐 |
| **顾问 id 与名字同层** | 核提成不用回查主档 |
| 订金按渠道列出 + 合计 | 摘要够用 |
| 只留摘要，不留明细 | 别把会话里的大块塞进台账 |
| 成功记 `success`/200/单号进业务键 | 如实 |
| 失败记 `failed`/0/错误码 | 如实 |
| `trigger` 取值在本表选项里 | 防那条照抄老值的老坑 |
| 台账写入失败 → 吞掉返回 `None` | 不影响业务结果 |

全套 **345** 项：cards 43、advisor 25、intake 33、draft 21、client 77、
booking-foundation 45、booking-readback 42、booking-payments 42、booking-ledger 17。

## 七、下一批

回 [37](37-booking-qa-batch-shape.md) 的路线：**`qa_cards`（489 行）** 是那一簇的入口
（小件都挂在它身上），构造器照搬、卡片文案进包、并把"防漏键"守卫扩到那一族。
`booking_qa_start` 要的 LINE 回复层接口，到那一步一并定。
