# 产品列表回来时保留筛选（评估）

11-23，主会话 local_0d36f9f2 定（A）。分支 `pearnly/routed-page-keep-filters`，基于 main b08a771ec。起因：左下角头像「再点回到上次界面」
（`pearnly/user-menu-return`）实测时，ERP 物料页回来搜索框清空——浏览器后退回去同样清空，是这类页面原有的问题。

## 0. 需求原文与理解

用户 11-13：「……应该跳转到上次界面」。回到一个列表页时，人刚才输的搜索、选的筛选、点的排序、选的每页条数都应该还在，
不管是左下角返回、浏览器后退、还是从侧栏 / 面包屑点回来。

## 1. 现有同类与复用

- 三产品的列表都用同一个共用列表 `p.list`（`pearnly_core/public/desk/list.js` 的 `ProductList extends frappe.views.BaseList`），27 个调用处。
- 丢的原因分两类：
  - ERP 用 `p.routed_page` 的页面（物料、入库、出库、调拨、调整、采购、销售、收款、库存、库存卡、公司、团队等）每次显示都 `clear_page`
    再 `render`，搜索框等页面字段和列表一起重建；
  - 其余页面（Cowork 任务、DMS 推送记录等）不重建，同一次打开里回来还在，但**刷新 / 下次登录**就没了。
- 只修 `routed_page` 不够通用；在 `ProductList` 这一处做，所有列表一个口径。

## 2. Frappe 原生能力（锁定 16.33，已读源码）

- 原生列表 `list_view.js`：每次渲染前 `before_render()` → `save_view_user_settings({filters: filter_area.get(), sort_by, sort_order})`，
  经 `frappe.model.user_settings.save(doctype, view_name, …)` 存成**这个人**的设置（服务端持久，跨刷新、跨设备）；
  建列表时 `view_user_settings.filters` 优先恢复（「Priority 1」），其次 list settings 默认；`frappe.route_options`（链接带来的条件）在刷新时覆盖。
- 复用：存取一律走 `frappe.model.user_settings`（原生 API，不自造存储、不进 localStorage），时机照原生（每次取数前存、建列表时恢复、route_options 优先）。
- 原生只存筛选区条件与排序；我们的页面另有页头字段（搜索框、日期、仓库等 `page.add_field`）——同一份设置里多存一项 `fields`，恢复时按字段名填回。

## 3. 按类通用

`ProductList` 一处：三产品全部页面级列表自动生效，各页不改代码。**不生效**：嵌在表单里的列表（`options.container`，如销售 / 采购表单的附件行）、
页面显式 `remember: false` 的（预留，当前没有）。

## 4. 上下游数据

只读写本人的 User Settings（原生表 `__UserSettings`，按 用户 × doctype 一行 JSON）；不改业务数据、不加接口。

## 5. 隔离与权限

- 设置按人存，别人看不到。
- **按公司分开**：view_name = `pearnly:<页面路由>:<当前公司>`（`frappe.boot.pearnly_company`，没有公司体系的产品为空）。仓库、客户这类条件是
  这家公司的值，换到另一家时不带过去，免得一进页面就是空列表。
- 恢复时条件不再合法（字段已不在白名单、选项已不存在）照原生 `validate_filters` 丢掉，页头 Select 值不在选项里不填。

## 6. 入口：网页、手机、LINE、拍照

电脑 / 手机网页同一份；LINE / LIFF / CC 桌面版 / 智能体页不用 `p.list`，不受影响。

## 7. 共用件与连带回归

改 `list.js`（共用件「产品列表」）：`check-impact` 出影响清单，各列表页自动冒烟照跑；手工实点 ERP 物料 / 入库 / 库存卡（有日期默认值）、
Cowork 任务、DMS 推送记录各一，「输条件 → 去别页 → 回来 / 刷新 / 换公司」。

## 8. 存储与成本

每次取数前一次 `user_settings.save`（原生本身就是这么做的，框架内部合并去抖后写服务端）；每页一份几百字节的 JSON。无新增费用。

## 9. 删除、作废、追溯

清空条件（筛选区的 ×、搜索框清空）即存成空，下次回来就是空的。不需要另做「清除记忆」。

## 10. 文案与官方 UI

不新增、不改文案；没有新控件。

## 11. 测试与验收

- Node 测试（真实 list.js + 假 BaseList / user_settings）：取数时存、建列表时恢复页头字段 / 筛选 / 排序 / 每页条数；带 route_options 时不恢复；
  换公司不串；表单内列表不存。
- 回归实验室（复用本窗口那套，挂本分支）：电脑 + 手机宽度，ERP 物料「搜 TEST → 左下角进出 / 浏览器后退 / 刷新」各截一张，库存卡日期、
  Cowork、DMS 各一张；截图自看后交主会话。
- 闸门：全闸门、候选离线、产品地图、文案。

## 12. 要问用户 / 主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 只在这次打开里记住（刷新就忘），还是照原生存到个人设置（刷新、下次登录、换电脑都在）？ | 照原生存（复用原生，行为同 Frappe 自己的列表） | 选「只这次」就不用 user_settings、改存内存，其余不变 |
| 2 | 日期类默认值（如库存卡默认本月）：回来时用上次选的，还是每次回到默认？ | 用上次选的（原生同样） | 若要每次默认，日期字段排除在记忆外 |

## 13. 工作量与排期（机器耗时）

改 `list.js` + Node 测试约 40 分钟；实验室截图约 30 分钟；闸门 + 候选离线约 40 分钟（后台）。不需要测试机、不部署。
