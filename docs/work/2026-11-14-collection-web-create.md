# 需求影响评估：ERP 网页端新建收款

> 2026-11-14 收款闭环窗口写，待主会话审。分支 `pearnly/collection-loop-5ac1a1`。

## 0. 需求原文与理解

- 原话（用户 11-13 晚，主会话交接第十节）：「收款 pearnly erp 网页端也可以新建收款，弹窗编辑相关字段，提交后就写入 exp，这个和明天的优先排一起」。
- 理解：能录收款的人（收款岗位、管理员——见本分支第 1 件「管理员默认有收款」）在 ERP 网页里点「新建」，在原生弹窗里填一笔收款，点提交后和 LINE / CC 一样排单，由 CC 桌面版写成 Express 的 RE；结果在列表里看。
- 不在范围：网页改已放行的收款、删除记录、作废已写进 Express 的 RE（仍由财务在 Express 原生作废）；付款单、退货；网页端识别转账凭证金额。

## 1. 现有同类与复用

- 规则与写账：`collection/service.py`（`book_options` / `choose_book` / `customers` / `open_invoices` / `methods` / `store_voucher` / `create_draft` / `release` / `withdraw`），LINE（`integrations/line_collection.py`）、CC（`collection/cc_tools.py`）都只调它；排单 `collection/push.py`。
- 网页：「销售 → 收款」`public/desk/collections.js`（只看、导出、凭证），「我的收款」`public/desk/my-collections.js`（本人记录、草稿原生弹窗改 `update_draft`）。
- 结论：**扩展现有**。服务层一行规则不改，只加网页入口与一个放行来源；弹窗沿用「我的收款」编辑弹窗的字段写法（同一个文件里抽成共用函数，新建与编辑一套）。

## 2. Frappe 原生能力

- `frappe.ui.Dialog`：Select（公司、账套、销售员、部门）、Autocomplete（客户，服务端 `customers` 搜索、按输入取，不一次取全）、Table（未收清发票：单号 / 日期 / 未收 / 本次收）、Currency（现金、各转账方式、折扣）、Attach（转账凭证，走 `store_voucher` 同一个上传口）。
- 页头主按钮 `page.set_primary_action`（「新建」）。不新增样式、不自定义控件。

## 3. 按类通用

- 同类：收款三个入口（LINE、CC、网页）。网页补上后三处同一服务、同一套规则码与文案（`collection/texts.py`）。
- 入口放两处：「销售 → 收款」（看收款的人：销售岗位、管理员）与「我的收款」（录收款的人）。按钮只给 `collection.write` 的人显示；只有销售岗位、没有收款的人看不到「新建」，服务端也拒绝。

## 4. 上下游数据

- ERP：建 `ERP Collection`（`source = "web"`，新增来源值），放行即预留发票余额（`RESERVING`），写成后回 RE 号、刷新应收快照——全部沿用。
- Express：同一载荷（`receipt`，版本 20），**CC 桌面版不改**；同 YOUREF 去重照旧。
- 「提交后就写入」：弹窗提交 = 建草稿 + 放行一步完成（同一事务）；放行的「同意」记为这次提交（`consent_ref = web:<会话>:<请求号>`，同一请求号重复提交只建一笔）。弹窗本身就是复核（满足「识别后必须复核再录」：内容全展示、可改、点提交才录）。
- Cowork / DMS：不涉及（收款只在 ERP）。

## 5. 隔离与权限

- 服务端：`create_draft` / `release` 本来就 `guard.require("collection.write")` + 本人可用公司（`_company`）+ 本人记录（`_own`）；网页只是又一个调用方。
- 跨公司：公司下拉只列 `companies_for()`（本人可用、接了 Express 的）；直接调接口带别家公司被拒。
- 管理员看全部、收款岗位只看自己的（`collection.access` 不变）。

## 6. 入口：网页、手机、LINE、拍照

- 网页桌面 / 窄屏：原生 Dialog 在手机宽度下单列显示，Table 字段原生横向滚动；验收要截 375 宽。
- LINE：不变（富菜单第 4 格）。CC：不变（收款工具）。
- 拍照：转账凭证用 Attach（手机浏览器原生选文件 / 拍照），不接共用相机件。

## 7. 共用件与连带回归

- 改到的登记共用件：`desk/list.js` 不改；`my-collections.js` / `collections.js` 是收款自有。服务层不改规则，只在 `contracts.SOURCES` 加 `web`、加一个网页提交函数。
- 新增流程 `scripts/flows/erp_web_collection.py`（实验栈：新建现金 / 转账带凭证 / 超收被拒 / 无权限看不到按钮且接口 403 / 撤回），产品地图登记两个入口（「新建」、「撤回」）。

## 8. 存储与成本

- 凭证图片同 LINE（统一原件层），无新增外部调用、无固定月费。

## 9. 删除、作废、追溯

- 网页「我的收款」给没被电脑领走的（草稿、待写入、失败）加「撤回」（`service.withdraw`，同 LINE【撤回】）；记录不删、凭证保留。已写进 Express 的不撤，照旧由财务在 Express 作废。
- 追溯：来源 `web`、`consent_ref`、`confirmed_at`、录入人都留在记录上，收款列表的来源筛选多一个「网页」。

## 10. 文案与官方 UI

- 新增：来源「网页」（`erp.collection.source.web`，四语）、Table 列「本次收」（若现有词条没有就新增）、「撤回」按钮（用 LINE 现有词条）。新增文案按官方风格，过 `check-copy.py`，交付附 `--report`。
- 改已有：`collections.js` 文件头说明（开发注释，不是界面文案）。没有改已有界面文案。

## 11. 测试与验收

- 离线：`tests/test_erp_collection.py` 补网页提交（成功、规则拒绝原样回、重复提交只一笔、无权限 403、来源 web）。
- 实验栈流程：上面的 `erp_web_collection`。
- 真机：部署后在测试机1 浏览器（或 testerp）新建一笔现金 + 一笔转账（69EXP\TEST），看 CC 写成 RE、Express 里有单。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 提交是否一步就排单（不再出确认页） | 一步：弹窗就是复核，点提交即排单 | 若要两步，要多一个「确认」弹窗 |
| 2 | 网页「我的收款」加「撤回」 | 加（LINE 有，网页没有就不一致） | 不加则网页录错只能去 LINE 撤 |

## 13. 工作量与排期

- 步骤：① 服务层网页提交函数 + 来源 `web` + 离线测试（约 1 小时）② 弹窗与两个入口、撤回（约 1.5 小时）③ 流程与产品地图（约 1 小时，实验栈跑约 20 分钟）④ 窄屏截图与文案清单。依赖：本分支第 1 件（管理员有收款）先合。
