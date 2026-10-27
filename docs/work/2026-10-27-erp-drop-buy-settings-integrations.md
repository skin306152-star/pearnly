# 删掉 ERP「采购设置」「集成」两页（10-27）

> 分支 `pearnly/erp-drop-buy-settings-integrations`，基线 main `6c8860b9`。主会话转用户原话：「『采购设置』『集成』是不要的」。
> 合入时点：今晚 00:30 生产部署完成、主会话通知后再合。

## 影响评估（按 `templates/impact-assessment.md`，删减型需求只列有内容的项）

**0. 需求与理解**：两页彻底删掉——页面文件、`pearnly_navigation` 声明、只被它们用的接口 / 词条 / 测试；被别处用的设置值不连带删，列出来。

**1. 现状**：两页都是 core 里的占位页（`frappe.pearnly.register(..., frappe.pearnly.notBuilt)`，只显示「未建」），ERP 声明了但不在 `available` 里，
任何岗位侧栏都没有；Cowork、DMS 不声明它们。页面本身没有业务代码、没有接口、没有数据。

**4. 上下游 / 设置值**（全仓查过调用方）：

| 东西 | 谁在用 | 处理 |
| --- | --- | --- |
| `ERP Purchase Settings`（单例）与 `purchasing/settings.py` 的 `read_settings` | 采购建单读默认值：`dedupe_block`（`service.py`、`entry.py` 拦重复对方单号）、`auto_stock_in`（`service.py` 自动入库）、`base_currency`（`entry.py` 外币要汇率）、默认税率 / 账期等（`service.py`），采购页启动数据（`api.py` 第 45 行）；core 补丁 `v0_0_4_site_default_currency` 读 `base_currency`；`access/menu.py` 把它列进采购岗位能读的主档 | **保留**（采购流程在用） |
| `purchasing/api.py` 的 `api_get_settings` / `api_update_settings`（兼容入口 `purchase.py` 同名转出） | 全仓没有前端或其它代码调用（两页从没实现，也没调过它们）；`update_settings` 服务函数被实验脚本 `scripts/erp-pos-lab-*-verify.py` 用来切自动入库 | **保留，列给主会话定**：删了以后改采购默认值就只剩后台 |
| 词条 `nav.purchaseSettings`（「采购设置」） | 只有这页和旧 Vue 对照代码的侧栏清单（`frontend/src/lib/nav.ts`，路由不进这些页面） | 删：术语表一行 + 四种语言翻译包里的键行与英文原文行（生成脚本不删已有行，手工删） |
| 词条 `nav.integrations`（「集成」） | 旧 Vue 对照代码 Cowork 那组仍引用 | 保留 |
| 词条 `nav.group.master`（「主数据」） | ERP 侧栏「主数据」组只装集成这一页，组随之删；但同一行的英文原文「Master Data」被 Cowork `features.json` 的功能标签用 | 保留 |
| 控制面功能目录、`features.json` | 没有这两页的项 | 不动 |
| 页面快照基线（验收分支 `pearnly/regression-baseline`） | 验收那边的基线可能记着这两页「无权限页」 | 不在本分支，交验收复核时更新 |

**6. 入口**：网页（直接输地址 → 外壳换成首页）；旧书签 `/pearnly-app#/integrations`、`#/purchase-settings` 的映射去掉（回首页）；手机 / LINE / 拍照不涉及。

**7. 共用件**：改到 core（删 Page、补丁、旧书签映射）与 ERP `hooks.py`。`check-impact` 命中的入口见下方「验证」。

**9. 删除**：站点上已同步的 Page 记录（连同角色行）由 core 补丁 `v0_0_8_drop_buy_settings_integrations_pages` 删除；两页没有业务数据。

**10. 文案**：只删词条，不新增、不改；`check-copy` 报删除 1 条，无拦截。

**12. 要问的**：

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 采购默认值（默认税率、自动入库、拦重复单号、默认账期、本位币）以后在哪改 | 保留两个接口；需要时在「公司」页加一块，不单独设页 | 删接口则只能后台改 |

## 改了什么

- 删 core 页面目录 `page/pearnly_buy_settings`、`page/pearnly_integrations`；core 补丁删站点上的两条 Page 记录。
- ERP `pearnly_navigation`：`pages` 去掉两项；采购组只剩采购记录、供应商；「主数据」组（只有集成）整组删。
- 旧书签映射（`www/pearnly-app.html`）去掉两项；旧 Vue 对照侧栏清单（`frontend/src/lib/nav.ts`）去掉 ERP 的两项；一次性实验校验脚本
  `scripts/cowork-preparation-upgrade-verify.py` 的页面名单去掉两项。
- 词条 `nav.purchaseSettings` 从术语表与四种语言翻译包删除。
- 共用件登记表删 `erp.page.never.buy-settings` / `erp.page.never.integrations` 两个入口。
- **顺带修（上一批 native-routes 的连带）**：登记表里另外 4 个「从不开放」的 ERP 入口（推送记录、对账、指南、历史）原来预期「原生无权限页」，
  native-routes（6c8860b9）之后外壳把它们换成首页，自动冒烟会判不通过。冒烟 page 探针加 `expect = "home"`（停在本账号的产品首页），
  4 个入口改用它。实验站点用正式冒烟驱动实测：旧预期 `not_permitted` 不通过，新预期 `home` 通过。

## 验证（本机一次性实验栈 `pearnly-drop-lab`，线上同版镜像 fb1037ad）

先用 main `6c8860b9` 的源码建 ERP 站点（两条 Page 记录在、各 1 条角色行），再挂本分支迁移：

| 场景 | 结果 |
| --- | --- |
| 补丁 | 迁移执行 `v0_0_8`；两条 Page 记录与角色行都删掉，`pearnly-purchase` 等其余页面不动 |
| ERP 管理员侧栏 | 采购系统：采购记录、供应商；没有「主数据」组；其余分组与项不变（两页从来不在任何岗位侧栏，验收 10-27 同样记录） |
| 直接输 `/desk/pearnly-buy-settings`、`/desk/pearnly-integrations` | 都换成产品首页 |
| 旧书签 `/pearnly-app#/integrations`、`#/purchase-settings` | 回 `/desk/pearnly-home`；`#/purchase` 照旧到采购页 |
| 冒烟 page 探针 `expect = "home"`（推送记录、对账、指南、历史、采购设置） | 全部通过（`at_home`，路由 `pearnly-home`）；同页旧预期 `not_permitted` 不通过 |

`check-impact` 命中入口（共用件本身无命中，只改到调用方）：

| 入口 | 结果 |
| --- | --- |
| `shared.navigation` 侧栏与菜单 | 通过（ERP 管理员，见上表） |
| `shared.page-head-declared` 页头与框架路由 | 通过（两页地址换首页；页头声明 `check-entries` 110 个调用点无漏登记） |
| `erp.provisioning` 开户后初始化 | 部分：实验站点按 install-app + migrate 建，菜单与页面齐；没走控制面正式开户（本批不改开户步骤） |
| `erp.line.chat`、`erp.cc.connect`、`erp.masters.scope`、`erp.execution`、`dms.liff.id-scan`、`shared.page-errors` | 没测到：只因改到 `hooks.py` / core 被列出，本批只动 `pearnly_navigation` 的页面声明与两张占位页，不涉及这些链路 |
