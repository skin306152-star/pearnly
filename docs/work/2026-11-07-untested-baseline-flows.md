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

## 第三组：Cowork 对账三类（`pearnly/baseline-recon`，11-08）

| 新流程 | 实点的入口 | 断言 |
|---|---|---|
| `cowork.recon.run` | 收入 / 销项 / 银行三页：列表「新建」→ 新建页「取消」；「新建」→ 两个「选择文件」→「开始」→ 看板 → 结果页；结果页改一格 →「保存」；收入、银行「提问 (n)」答「是」；收入「对账预设」把答案改成「否」；列表「进行中（1）」→ 进行中一页 →「返回」；排队对账的看板 →「返回」；销项结果页「导出」 | 本次标记的任务每类正好一份（取消没建、开始建了一份），名称是填的；两边配上（收入、销项对上或有差异、没有落单，银行对上 ≥1）；保存后库里这份任务存着改后的字；收入预设 `income\|<税号>\|4201` 先记成「是」、弹窗改后库里是「否」；银行预设 `bank\|BBL\|<尾号>\|1112-01` 多笔对一笔「是」；进行中一页列着排队的那份、两颗「返回」都回列表；导出走 POST、复核表里有发票号；识别任务数不变 |

- 数据：两边都是 prepare 在站点里用 openpyxl 生成的 xlsx（表格直读，不经识别；实验室也没配识别）。税号、账户尾号每次随机，答过的预设才不会让以后不再问。
  「进行中」「返回」靠 prepare 给对账岗挂一份只有状态缓存、没有后台任务的排队对账（`jobs._state` + `progress.active_add`）。
- cleanup 删掉本次的三份任务（页面同一个删除接口 `recon_center.api.delete`）、预设（预设弹窗「删除」同一个 `presets.remove`，预设表不许直接删）和排队的假对账：
  `cowork.recon.presets` 新开户要「暂无预设」、`cowork.recon.exports` 新开户要没有任务，留着会让它们红。跑完新流程接着跑这三条老流程，各 4/4。
- 销项页「对账预设」「提问」：三页共用 `recon.js`，`check-entries` 按文件要求三页都声明，但销项 `KINDS.vat` 不挂预设、服务端只给银行 / 收入出问题，页面上不出现——
  登记进 `not_entries.toml`（同出入库页互相的新建按钮），地图删这两行。
- 挂 25 行（三页本身 + 23 颗按钮），基线删 52 条（207 → 155）。两套数据 × 电脑 / 手机 4 格全过（本机实验栈 `pearnly-reconflow-lab`）。

### 修的缺陷：手机宽度「进行中」不带件数、没有进行中也挂着（`pearnly/recon-running-mobile`，主会话 11-08 定修）

- **现象**：电脑宽度列表页头「进行中（n）」只在有进行中的对账时出、带件数（`recon.js` `show_list` 里 `running.text(...).toggle(...)`）。
  手机宽度框架把页头按钮另放进「…」菜单（`frappe/ui/page.js` `add_inner_button`「Add actions as menu item in Mobile View」），那一项是加按钮时的原字「进行中」，
  不跟着改字、不跟着隐藏：没有进行中的对账时手机上也有，点进去是空的进行中一页。框架的 `remove_inner_button` 不删这一项，自带的按名查找
  （`is_in_group_button_dropdown` 找 `span[data-label]`）也找不到它（菜单项的 span 没有 `data-label`）。
- **红**（提交 `test:`，修前代码）：`cowork.recon.tasks` 加「进行中入口与本人进行中的对账一致」、`cowork.recon.run` 加「看得见的进行中入口只有一个、带件数」，
  电脑宽度 4 格绿、手机宽度 4 格红：没有进行中时看到 `["进行中"]`，有一份时只有不带件数的菜单项（找不到「进行中（1）」）。日志在共享笔记「对账进行中手机入口-修前红-11-08.log」。
- **修法**（提交 `fix:`，只改 `recon.js`）：件数一变，用原生 `remove_inner_button` 去掉按钮，连同加按钮时框架顺带加的那份菜单项（加之前后比对 `page.menu` 的子节点拿到）一起去掉；
  有件数就按新文字 `add_inner_button` 重新加；菜单空了用原生 `clear_menu` 收起。不另做一套入口。
- **绿**：对账四条流程（tasks / run / presets / exports）× 新老 × 电脑手机 16/16；`cowork.recon.run` 走收入、销项、银行三页。候选离线检查 102 组、LANE EXIT 0（生产镜像 `sha256:26a2b0e8…`）。
- **共用件连带回归**（`check-impact.py`，对账页脚本）：

| 入口 | 结果 | 依据 |
|---|---|---|
| `cowork.page.recon-bank` | 通过 | `cowork.recon.run` / `tasks` / `presets` / `exports` 在银行页点遍页头按钮，控制台无报错 |
| `cowork.page.recon-income` | 通过 | `cowork.recon.run` 收入页点遍页头按钮、`exports` 收入导出 |
| `cowork.page.recon-vat` | 通过 | `cowork.recon.run` 销项页点遍页头按钮与导出 |
| `cowork.recon.upload`（manual） | 通过 | `cowork.recon.run` 三页各选两份文件 → 开始 → 结果页，库里多一份任务 |

## 第四组：团队页、任务看板菜单（`pearnly/baseline-team`，11-08）

| 新流程 | 实点的入口 | 断言 |
|---|---|---|
| `erp.team.actions` / `cowork.team.actions`（同一模块 `team_actions.py`，按站点认产品） | 列表页头「新建」→ 弹窗填名姓用户名密码、勾一个功能 → 弹窗「新建」；整页新建页头「新建」；整页编辑页头「保存」「取消」 | 库里弹窗与整页各建出一位用户、有勾的角色；保存后名是改后的；取消后名不变（建的两位跑完停用） |
| `erp.work.board-settings` / `cowork.work.board-settings`（同一模块 `work_board_settings.py`） | 看板「…」菜单：提醒、已归档、新建看板、看板设置、标签、自定义字段、自动化、成员 | 提醒「全部已读」后管理员未读 0（prepare 让同事提了他一句）；已归档里「恢复」后那张卡不再归档；新建（私有）看板、设置改名后库里只有改后的名称；新板上有加的标签、字段、规则；成员里多了加进来的同事 |

- 两产品各 4 格，团队 8/8、看板 8/8，跟着跑老的 `erp.work.board` / `cowork.work.board` 各 4/4。
- 写法要点：外壳把单行文字格画成自动长高的多行框（`textarea`），选择框画成可搜索的输入框（不是 `<select>`）——选值用 `dialog.set_value`，候选取字段的 `df.options`。
  团队看板人人都算成员，「成员」里没有可加的人：新建看板选私有。cleanup 归档新建的看板与 prepare 的卡。
- 挂 ERP / Cowork 团队页各 3 颗按钮、Cowork 团队页手机端、两产品看板各 8 项菜单；基线删 45 条（155 → 110），对照组缺口存量删 `cowork.desk.pearnly-team@mobile`。

## 第五组：首页订阅与充值（`pearnly/baseline-home`，11-08）

| 新流程 | 实点的入口 | 断言 |
|---|---|---|
| `erp.home.billing` / `cowork.home.billing`（同一模块 `home_billing.py`，页面是共用的 `home.js`） | 明细页头「订阅与充值」；二级页「选择套餐」「充值」；明细类型切「充值」勾选后「删除」 | 二级页余额 = 计费服务余额；订阅一个与当前不同的套餐后计费服务里当前套餐是它、余额少它的月费；充值（填金额、付款人、经原生上传框交一张 PNG 凭证）后明细里有这条、金额是填的；删除后计费服务的充值记录里没有它 |

- 计费是实验室本机控制面（`lab_billing.py`）。cleanup 在控制面用正式审核函数 `billing_api.approve_local` 补回扣掉的月费，余额回到跑前（核过：跑完 1000）。
- 充值服务端必收凭证（`payments.proof.payload`）；余额显示不带小数（`p.money`），按数值比；明细金额列按页面同一个 `p.money` 找行。
- 两产品各 4 格 8/8。挂两产品首页 4 颗按钮，基线删 16 条（110 → 94）。

## 下一组

个人资料保存、列表多选、只开页面的几页、Cowork 录入与盘点（第一份交接第 4 节）。
