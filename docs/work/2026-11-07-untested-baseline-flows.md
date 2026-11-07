# 清产品地图「没测试」存量基线（11-07 晚）

接上一个窗口的 364 → 242。规则见 [验证 · 产品地图](../development/verification.md#产品地图)：只认真点了入口、断言了业务结果的绿测试；基线只许减少。

## 第一组：采购 / 销售动钱的动作（`pearnly/baseline-trade-actions`）

| 新流程 | 实点的入口（产品地图行） | 断言 |
|---|---|---|
| `erp.purchase.actions` | 采购列表「新建」、草稿勾选后「删除 (1)」、页头「付款」、「…」里「补开收据」「预扣税凭证」「修改」「作废」 | 新建进到新建页；草稿记录没了；付款记录多一行 50（转账）；两份生成的 PDF 附件文件在；修改后原单作废、付款冲回 −50、新单改自原单且已提交；作废原因是填的那句、入库那 1 件冲回 |
| `erp.sales.actions` | 销售列表「新建」、草稿「删除 (1)」、页头「收款」、「…」里「作废」「Express」 | 草稿没了；已收 50、方式转账、部分收；作废原因对、卖出的 1 件回到账面；接 Express 的新开户弹窗走 POST 且写账状态不变，老数据没有这颗按钮 |

挂 12 行，基线删 24 条（242 → 218）。两套数据 × 电脑 / 手机，16 格全过（本机实验栈 `pearnly-baseline-lab`，线上镜像 `sha256:fb1037ad…`）。

### 顺带修的缺陷：采购岗删不了采购草稿

- **现象**：采购记录勾选草稿 →「删除 (1)」→ 确认，草稿还在，页面报未捕获的错。新流程第一次跑（变基前 b5ec50102 之前的代码，基于 main 9540285d2）新老两套 × 电脑手机 4 格都红在
  「selecting the draft turns the head button into Delete and the row goes away」（`gone: false`）和「the deleted draft is gone」，页面错误 `Uncaught (in promise) Object`。
  在实验栈以 `purchase@erp.regression` 直接调 `purchasing.entry.delete_drafts([草稿])`：`PermissionError()`。
- **原因**：共享口径不给功能角色框架的 delete 位（业务单据只能作废）。销售 `sales.entry.delete_drafts` 早已 `frappe.delete_doc(..., ignore_permissions=True)` 并写明原因，采购这边漏了，`frappe.delete_doc` 按框架权限拒绝。
- **修法**：`purchasing/entry.py` 的 `delete_drafts` 同销售补 `ignore_permissions=True`。前面三道闸不变：写岗位 + 本人范围（`access.get_document(write=True)`）、整批行锁、全部是草稿才删。
- **证据**：修后同一流程 4 格全过（删除按钮后草稿行消失、库里没有这条记录）。

### 发现的问题（未在本组修）：同一进程两份 PDF 同时生成偶发 500

- **现象**：变基到 46190218e 后回写 `pearnly/flows` 7/8，`erp.purchase.actions [new / mobile]`「补开收据」请求 500、单上没有那份凭证。连跑 5 遍（`--jobs 2`，两个站点同时跑）约四分之一的格复现，
  补开收据、预扣税凭证轮流丢；`--jobs 1` 单线程连跑两遍 8/8 全过。一次点击只发一次请求（流程里计数），不是重复点击。
- **根因**：锁定版 Frappe 的 `www/printview.py`（`get_font` 的默认值，约第 628 行）字体串写坏了：`"InterVariable", "Inter", -apple-system", …`（`-apple-system` 前少一个引号）。
  `frappe.utils.pdf.get_pdf → prepare_options → read_options_from_html → get_print_format_styles` 用 `cssutils.parseString` 解析整页样式；cssutils「解析出错抛不抛」是**进程级全局开关**
  （`CSSParser.__parseSetting` 每次解析临时改 `cssutils.log.raiseExceptions`、解析完改回）。同一进程两个线程同时解析时，先完成的把开关改回「抛」，另一个正撞上这句坏 CSS 就抛
  `xml.dom.SyntaxErr: CSSStyleDeclaration: Syntax Error in Property: font-family …` → 500。
- **复现条件**：同一网页进程里两份 PDF 同时生成。生产网页是 gunicorn `gthread` 4 线程（`infrastructure/compose/01-bench.yml`），满足条件；实验室是开发服务器多线程，两个站点并行跑流程时满足。
- **影响**：所有走 `frappe.utils.pdf.get_pdf` 的打印（采购补开收据 / 预扣税凭证、各单打印、收货单等），概率随同时打印的人数上升。
- **证据**：错误日志是延迟写入（实验室没刷进表），原文从缓存队列 `insert_queue_for_Error Log` 取出，存在共享笔记「PDF并发500-错误原文-11-07.txt」
  （调用链 `purchasing/api.py:128 api_purchase_voucher → purchasing/output.py:117 voucher → frappe/utils/pdf.py:113 get_pdf → :228 prepare_options → :278 read_options_from_html`）。
- **处理**：主会话 11-07 定：本组这一格按 `--rerun-failed` 补跑通过后合入；修法另开一项（先评估打印设置给有效字体，所有打印格式改前改后截图对比；进程内串行生成作补充评估）。
- 流程这边补了：点完凭证等它真挂到单上再进下一步、记下页面上未处理的拒绝原文、断言一次点击只发一次请求。

## 第二组：收银台页头（`pearnly/baseline-pos-buttons`）

| 新流程 | 实点的入口 | 断言 |
|---|---|---|
| `erp.pos.shift-buttons` | 页头「开班」「新单」「最近订单」、「…」里「交班」 | 开班弹窗备用金 500 → 库里这一班开着、备用金 500；条码进车后「新单」清空购物车；「最近订单」列出本班小票；交班后库里已交、实点现金 = 500 + 小票金额 |

小票用收银接口卖（结账不是这四颗按钮的入口），卖完照页面成交后的做法 `cur_pos.refresh_shift_summary()`，交班弹窗的「应有」才是新的。挂 4 行，基线删 8 条（218 → 210）。

## 下一组

Cowork 对账（bank / income / vat）：一律走电子版解析（收入两边 CSV / Excel、银行带文字层 PDF），不碰付费识别；拿不准的先把实验栈识别服务指向不可达地址确认。进度与交接在共享笔记「交接-清欠测试基线-进度-11-07.md」。
