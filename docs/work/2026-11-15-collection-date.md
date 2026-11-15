# 收款日期可选（2026-11-15）

## 需求

线上 testerp 用 LINE 收款，选公司「นิว ลิงค์」后回「2026-11-15 不在已连接账套的期间内：9.… 2024-01-01–2025-12-31」，流程到此为止。

用户原话：「如果我要去收老账本的帐无法收了？这个时间是人工可以改的，我只要改到账本期间，就可以进行写帐，为何我们不可以？」

在 Express 里，人可以把工作日期（วันที่ทำการ）改进期间再录 RE。我们的收款把日期写死成放行当天。按已定口径（Express 不拦我们不拦；人在 Express 能做的 CC 都要能做；要人定的事问用户、不拒绝了事），这是产品缺口。

## 做法（主会话 11-15 审定）

日期是唯一的输入，账套跟着日期走：

- **当天在期间内**：流程不变，不多问一步。
- **当天不在这家任何一本已连接账套的期间里**：不拒绝，改问「收款日期」。
  - 可选范围是各本「本年起 ~ 次年止」，与选账套、期间判据同一条 `account_match`。
  - 选定后按这个日期重新挑账套；同期多本照旧问一次。
- **日期定下后不再变**：
  - 草稿记 `date_chosen`，放行不再改回当天。
  - 没选过日期的照旧按放行当天。
- **改日期**：只能在草稿冻结的那本账套的期间里（`service._redate`）。要收别的账套的，重开一笔。
- **CC 对话里本对话账套固定**：日期不在这本期间时回 `date_needed`，带这一本的期间，由模型问日期。换账套走对话的账套选择器，这里不另给入口。

| 入口 | 改法 |
|---|---|
| LINE | 选公司后多一步 `day`：期间用佛历写（同卡片），快捷回复是各本本年段最后一天，也可以打 `D/M/YYYY`（佛历）或 `YYYY-MM-DD`；确认卡加「收款日期」一行。改日期在【编辑】打开的网页弹窗里改，不占快捷回复位。 |
| 网页新建弹窗 | 加原生「收款日期」Date（默认今天）。不在期间时清空、用 `min_date` / `max_date` 限定到各段期间，下面写出会计期间；改日期重新取账套、方式和发票。 |
| 网页改草稿弹窗 | 加「收款日期」Date，范围是这笔冻结的那本账套；只在改了日期时才提交。 |
| CC 工具 | `erp_collection_invoices` / `methods` / `draft` 都收 `date`；`date_needed` 回期间和定式说明。 |

服务端：
- `collection.service`：`day_options` / `periods` / `parse_day` / `DAY_NEEDED`；
- `book_for` 遇到无账套或期间不符时，有账套就问日期；
- `create_draft` / `update_draft` / `submit_web` 收日期，`release` 保留选定的日期；
- `_view` 带 `reason_text`：电脑回 `PERIOD_LOCKED` 时说「会计期间已在 Express 关账」（沿用已有词条，不加选项）。

ERP Collection 新增隐藏字段 `date_chosen`（Check）。

## 与 CC 桌面版的契约

不变，不用发新版。载荷本来就带 `doc_date`，`companion/dbf_receipt.py` 按它写以下字段：
- ARTRN 的 DOCDAT / DUEDAT / CMPLDAT；
- ARRCPIT、ARRCPCQ；
- GLJNL / GLJNLIT；
- ARBAL 期别。

另外：
- CHGDAT 记实际操作那天，同原生；
- 期间锁按这个日期查（`respect_period_lock`）；
- 服务品现金制（SRV_VATTYP 2）的发票本来就不收，没有税期问题。

## 文案

- **新增**（四语）：`Accounting period: {0}`，对应 会计期间：{0} / งวดบัญชี: {0} / 会計期間：{0}。
- **沿用已有**：`Collection Date`（收款日期）、`express.reason.periodLocked`（会计期间已在 Express 关账）。
- **改已有**：无。原来那句「{0} 不在已连接账套的期间内：{1}」在收款里不再出现，ERP 其它入口照旧用。

## 测试机1 原生对照（待做，主会话排测试机窗口；通过前不合 main）

账套：NLTTEST（可写测试收款单）。步骤：

1. Express 登录 NLTTEST，把工作日期（วันที่ทำการ）改到上一会计年度期间内的某天（例如上一年 12-31）。
2. 原生录一张收款单 RE：一位有未收发票的客户、一张发票、现金 + 一种转账，记下 RE 号。
3. 用 CC 桌面版（同一本、同一位客户、另一张未收发票），按服务端载荷 `doc_date` = 同一天写一张 RE。
4. 两张逐字段对比：
   - ARTRN：DOCDAT、DUEDAT、CMPLDAT、CHGDAT、USERID、DOCNUM 前缀与号段；
   - ARRCPIT：DOCDAT 或对应日期列；
   - ARRCPCQ：CHQDAT 等日期列；
   - GLJNL / GLJNLIT：凭证日期、期别；
   - ARBAL：落在哪个 `RE{期}` 桶；
   - ISRUN：RE 那一行的 DOCNUM 是否按期间 / 年度分段（老期间录单号是接着当前号，还是另一段）。
5. 如果老期间在 Express 里已关账：原生应拒；CC 应回 `PERIOD_LOCKED`，网页 / LINE 显示「会计期间已在 Express 关账」。

## 验证

- 离线：`tests/test_erp_collection.py`（`CollectionDate` 7 条，含无账套时仍说没有账套），`tests/test_erp_collection_line.py`（`DayStep` 2 条），`tests/test_erp_collection_cc.py`（对话账套期间外问日期）。
- 流程：新增 `erp.collection.date`；`erp.line.collection` / `erp.web.collection` 回归。
- 真机（线上核对窗口）：LINE 选 นิว ลิงค์ → 选 31/12/2568 → 列出 9.… 的发票 → 写入 → Express 里看 DOCDAT 与凭证日期；网页弹窗改日期再收一次。

## 按类：其它入口里「日期不在期间 → 一句话拒绝」（下一车，本次不修）

1. CC 工具建的出入库 / 调整单默认当天：要核实。网页单据的日期本来就能改，不算死路。
2. POS 日汇总：日期就是营业日，不该改。期间不符是真冲突，维持拒绝，只把文案换成可选期间。
3. CC 对话固定账套时，采购 / 销售单日期不在期间（`PERIOD_OUTSIDE`）：应问「换日期」。
4. 采购 / 销售单：日期取票面，不受影响。
