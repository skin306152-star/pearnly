# 收款完整闭环（2026-11-14）

分支 `pearnly/collection-loop-5ac1a1`，基线 origin/main a838abe54。主会话任务书：收款 11-13 晚已对全部 ERP 租户上线，本任务把它做成闭环。
接手自 11-13 的收款窗口（交接见共享笔记「收款-真机与CC侧-交接-11-13晚.md」「收款-LINE侧-交接-11-13晚.md」）。

## 0. 旧分支核对（11-14 开工）

| 分支 | 结论 |
|---|---|
| `pearnly/collection-cc-chat` | 代码全部已在 main（按标题与内容核对 4 个未等价提交，main 都有同标题提交） |
| `pearnly/collection-line-side` | 同上，5 个都在 main |
| `pearnly/collection-on-ledger` | 7 个在 main；只差一条文档提交 6cf9786c3（银行流水抽样 5 行证据），已带进本分支 |

## 1. 管理员默认有收款

现象（11-13 晚用户自己撞到）：管理员在团队管理里「收款」显示打勾（管理员行一律画成全勾、不能改），LINE 菜单却没有收款格。
原因：`collection.write` 只认收款岗位角色。

改法（主会话定）：
- `access/rules.py`：新增 `ADMIN_FEATURE_CAPABILITIES = {"collection.write": "collection"}`，`required_roles` 给这些能力加上管理员角色。
- `access/guard.py`：这类能力仍跟着功能开通走——站点没开通「收款」时管理员也没有（功能角色本来就按开通摘，管理员角色不摘，所以单独判）。
- `hooks.py`：「我的收款」页加管理员角色（与 `collection.write` 同一判据）。
- 三处同时生效：网页（我的收款、以后的网页新建）、LINE（富菜单第 4 格与「เมนู」兜底，都按 `guard.has("collection.write")`）、CC（收款工具同一判据）。
- 补丁 `v1_28_admin_collection_menus`：部署迁移时给已绑定 LINE 的人各排一次菜单重挂（去重、只在该挂的那套不同才写 LINE），管理员不用等下次发消息。
- 测试 `tests/test_erp_admin_collection.py`（镜像里跑，已加进 `run-offline-tests.py` 的 IMAGE 清单）：管理员 × 录收款 / 菜单收款格 / 我的收款；功能没开通时谁都没有。

上线后逐正式租户回读（6 家 ERP：testerp、test01、mrerp、gize、klm、polymer），每家一次 web 容器 bench：
1. `pearnly_features` 含 `collection`；
2. 每个有 `ERP Admin` 的启用账号 `guard.has("collection.write")` 为真；
3. 其中已绑定 LINE 的，`line_menu.wanted_variant(user)` 含 `collection`，且 LINE `GET user/<id>/richmenu` 等于 `wanted_menu` 算出的那套（补丁排的重挂已跑完）；
4. 后台错误日志里没有 `ERP LINE menu` 相关新错误。

## 2. 岗位文案「收款员」→「收款」

用户 11-13 晚原话：「收款员的文案按照功能名称写：收款」（主会话交接第十节转）。四语取功能名「收款」现有词条（`erp.nav.collections`），不另造：

| 来源·键 | 中文 旧 → 新 | English | ไทย | 日本語 |
|---|---|---|---|---|
| pearnly_erp_portal·team.role.collector | 收款员 → 收款 | Collector → Collections | พนักงานเก็บเงิน → รับชำระหนี้ | 集金担当 → 入金 |

没改的：收款列表 / 导出里的「收款员」列（无键词条 `Collector`）指录这笔的人，不是岗位名，保持不变。

## 3. LINE 菜单发布不下载整张图

`_has_image` 用 GET 读整张图判断「有没有图」，生产到 api-data.line.me 慢时 20 秒读不完（11-13 testerp 首次发布超时）。
DMS、Cowork 的菜单发布是同一写法，按类一起改：共用层 `pearnly_core.line.rich_menu_has_image`（流式取，只看状态、不读正文、立刻关连接），三个产品的 `_has_image` 都调它。
断点续做：发布按内容散列命名复用已建菜单、只给没图的传图，中途失败后重跑就是接着做；测试 `tests/test_line_menu_publish_lock.py` 新增「只看状态」与「半途重跑只补剩下的」两条（三个产品各跑）。
