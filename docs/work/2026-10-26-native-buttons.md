# 产品页原生按钮白名单（testcowork 个人页露出「权限」「创建用户电子邮件」）

基线 main `4561884f`，分支 `pearnly/native-buttons-whitelist`。主会话 10-26 派：查根因、按类修、防再犯；不部署、不合 main。

## 现象

用户在 testcowork.pearnly.com「个人信息」（`/desk/user/<本人>`）右上角看到框架原生的「权限」下拉和「创建用户电子邮件」按钮，产品没有这两个功能。

## 根因（证据）

1. **按钮出现的条件**（Frappe 16.33.0 原生 `frappe/core/doctype/user/user.js` 的 `refresh`）：
   - 「权限」下拉（设置用户权限限制 / 查看被授权单据 / 查看文档类型权限）：`has_access_to_edit_user()`，即当前用户有「对 User 表 permlevel ≥ 1 可写」的角色；
     站点上只有 System Manager 有（DocPerm 与 Custom DocPerm 都只有它）。
   - 「创建用户电子邮件」：`frappe.model.can_create("Email Account")`，站点上只有 System Manager 能建。
2. **这个账号有 System Manager**（生产只读查，镜像 `fb1037ad`，2026-10-26）：testcowork 的 `pearnlyai@gmail.com` 角色是 Cowork Admin、Cowork Intake、
   System Manager；六个生产站点里只有它一个业务账号带 System Manager。
3. **什么时候加上的**：User 的修改历史（`tabVersion`）——`2026-10-20 04:54:24` 由 Administrator **加了 System Manager**；此前（09-26 开户起）没有。
   这一刻正是 testcowork 换到 Cowork 号池（发布记录 `2026-10-20-release-23471f87.md`「泰国时间 04:50–05:00」）：搬池给管理员排了 `bind_user`，
   它调用 `provisioning.ensure_local_user`，参数 `role` 默认 `"System Manager"`，对**已存在**的账号也照加；之后没有再跑「设角色」把它拿走。
   换池流程来自 `26130d31`（10-19），生产 `08bfb419` 含它。开户时的「设角色」（`ensure_feature_roles`）本来会拿走 System Manager，所以别的租户没有。
4. **为什么页面藏不住**：我们的 `public/desk/user.js` 只拦了原生「重置密码」、`product.css` 只藏了原生「菜单」按钮，原生 `refresh` 加的其他自定义按钮
   没处理——账号一有 System Manager 就露出来。
5. **连带发现**：`2026-10-26 18:26:10` 用户本人保存个人页时，账号的 **Desk User 角色被移除**（`tabVersion` 记录 removed roles: Desk User）。
   有 System Manager 时原生「角色编辑器」在个人页上生效（虽然被我们隐藏），保存时按它的勾选重写了角色表。实验栈在 main 代码上复现：
   带 System Manager 的管理员改「静音」保存，角色从 [Desk User, ERP Admin, System Manager] 变成 [ERP Admin, System Manager]。
6. **回归为什么没拦住**：自动冒烟用普通岗位账号和不带 System Manager 的管理员，且没有「页头只许出现登记过的按钮」这一项；这个账号的状态是 10-20 换池后才变的。

## 修法

**按类：产品页页头按钮白名单**（`pearnly_core/public/desk/actions.js`，三产品共用）
- 产品站点上 `frappe.ui.Page` 加按钮的入口（`add_inner_button`、`add_button`、`add_menu_item`、`add_custom_menu_item`、`add_action_item`、
  `add_actions_menu_item`、`add_custom_button_group`、`add_action_icon`、`set_primary_action`、`set_secondary_action`）都先过白名单：
  - 我们自己注册的页面（`p.register` 建的，打 `page.pearnly_owned`）照常加；
  - 框架原生视图（表单、列表、报表、工作区）只放行产品声明里该路由列出的（键 = 当前路由前两段），
    没声明的根本不加。分组按钮按组名判（「权限」整组不出）。菜单项不加，它带的快捷键也就不注册。
  - 放行的都打 `data-pearnly-declared`，冒烟据此判。
- 声明见下节（对齐 A 节后放在各产品 `pearnly_navigation`）。盘点差异报表原来用黑名单删了编辑、打印、PDF 等几项，改成白名单只留导出，删掉黑名单那段。
- `user.js` 里专门摘「重置密码」的两段删掉（白名单已覆盖）。

**服务端**（`pearnly_core/desk/permissions.guard_user_roles`，User `validate`，只在产品站点）
- 业务账号不许被加上 System Manager：只有开户**新建**账号那一刻（`ensure_local_user` 举旗 `pearnly_grant_system_manager`）放行，随后「设角色」拿走；
  其他任何路径（含 Administrator 重跑开户步骤）加都报「Not permitted」。用户权限、文档类型权限报表、建邮件账户这些原生接口本来就只给 System Manager，
  业务账号拿不到它，服务端就拦住了。
- 本人保存自己的个人页不能改动自己的角色（隐藏的原生角色编辑器不能再重写角色表）。团队管理改别人的角色照常；管理员本来就不能在团队管理里改自己。

**开户**（`pearnly_core/provisioning.ensure_local_user`）：`role` 只在新建账号时给；已有账号只绑身份、不加角色（堵住 10-20 那条路）。

## 对齐回归方案 A 节（主会话 10-26 审定，`pearnly/regression-baseline` 5ac67715 评估 13.2）

- **声明只有一份**：各产品 `hooks.py` 的 `pearnly_navigation` 里加 `actions`（每页页头允许的入口，语义码）与 `shortcuts`（允许的全局快捷键）。
  原先单独的 `pearnly_native_actions` 撤掉。启动数据下发 `boot.pearnly_page_actions` / `boot.pearnly_shortcuts`（`navigation.head_entries`）。
  - 我们自己的页面：代码里加的每个页头入口都要在所属产品的 `actions` 里（`"*"` = 各页共用，如勾选后的批量操作 `selection`）；
  - 框架原生视图：`"Form/User": ["Save"]`（三产品）、`"query-report/ERP Stock Count Variance": ["Export"]`（ERP）；没列的一律不加；
  - `shortcuts: ['ctrl+s']`：其余原生全局快捷键（Ctrl+K 搜索栏、Ctrl+G 全局搜索、Shift+/ 快捷键表、Shift+Ctrl+R 清缓存重载、Shift+T 控制台、
    Shift+Ctrl+G 换主题、Ctrl+/ 侧栏、原生表单的 Ctrl+P 打印等）加载时注销、之后也不注册；挂在我们自己页面上的（收银台官方组件）照常。
- **静态检查** `scripts/check-entries.py`（进 Development checks）：扫 JS 里页头接收者（`page`、`this.page`、`frm`…）上的
  add_custom_button / add_menu_item / set_primary_action / set_secondary_action / add_inner_button / add_action_item / add_actions_menu_item /
  add_button，取标签的语义码（包装函数里的字面量），标签是变量 / 模板串的在同一行或上一行写 `// entry: 码`；码不在所属产品声明里就失败。
  存量用 `--generate` 一次生成（能从登记表对上页面的放那页，对不上的放 `"*"`），现在 107 个调用点、136 个声明码对齐。
- 「…」菜单（打印 / 邮件 / 改名 / 删除 / 复制 / 新建用户）是原生表单往页头菜单加的项，走同一白名单，个人页一个都不加。
- **待主会话定**：应用切换（公司标题下拉）里的「网站」「显示」「刷新」是用户 09-20 明确要求保留的（`2026-09-20-ssdnodes-migration.md`
  第 197、291 行），本分支没动；若要去掉需用户推翻原口径。

## 防再犯

- 冒烟页面探针 `scripts/smoke/probes/page.js` 加一项：页头里可点的按钮、菜单项、图标，没带 `data-pearnly-declared` 的就判不通过
  （所有 auto 页面入口每次都核）。
- 登记共用件 `desk-head-actions` 与入口 `shared.page-head-declared`（`scripts/impact/shared-parts.toml`）。
- 离线测试：`tests/test-native-actions.cjs`（白名单行为）、`tests/test_tenant_framework_roles.py`（开户只给新建账号、拒绝加 System Manager、
  本人保存不改角色、白名单合并），已进 Development checks。

## 验证

本机一次性实验栈 `pearnly-native-lab`（`~/pearnly-devtools/native-lab/`，线上同一镜像 `fb1037ad`，挂本分支，三站点 erp-nb / cowork-nb / dms-nb）。
「修之前」对照用挂 main 代码的 `pearnly-print-lab`。

| 场景 | 结果 |
| --- | --- |
| 修之前（main）：带 System Manager 的 ERP 管理员打开个人页 | 右上角出现「权限」（设置用户权限限制 / 查看被授权单据 / 查看文档类型权限）、「创建用户电子邮件」、「保存」，与用户看到的一致（`2026-10-26-native-buttons/before-profile-system-manager.png`） |
| 修之后：三产品 × 租户管理员 / 普通员工 / 带 System Manager 的管理员，逐页打开该账号能进的全部产品页 + 个人页（ERP 加盘点差异报表） | 9 组共 77 页，页头没有一个未声明的按钮；每个账号的个人页只有带标记的「保存」（显示、可用）；带 System Manager 的管理员也看不到「权限」「创建用户电子邮件」（`2026-10-26-native-buttons/after-profile-system-manager.png`） |
| 页面异常 | ERP 几页的页面异常在 main 代码上同样出现（实验站点缺业务数据），与本改动无关 |
| 以 Administrator 给业务账号加 System Manager | 被拒（PermissionError） |
| 带 System Manager 的管理员改「静音」后保存个人页 | 新代码：角色不变；main：丢了 Desk User（复现 10-26 生产记录） |
| 开户：新建账号 → 设角色 → 重绑已有账号 | 新建后临时有 System Manager；设角色后变成 Cowork Admin / Intake / Tax Filing / Desk User；重绑不再加回 |
| 对齐 A 节后重跑（声明改读 `pearnly_navigation`、加快捷键白名单） | 9 组 77 页页头 0 个未声明入口，个人页只有「保存」；三产品每组注册的全局快捷键只有 Ctrl+S（另有框架基础导航 Esc / 回车 / Ctrl+↑↓，不走快捷键注册）。注：改了启动数据后要清缓存（部署时迁移会清），实验栈手动 `bench clear-cache` |
| 离线测试 | `test_tenant_framework_roles.py` 5 项、`test-native-actions.cjs`（含快捷键）、`check-entries.py`、`test-native-desk.cjs`、`test_native_desk_contract.py` 通过 |

连带回归（`check-impact`）：`desk-shell`、`desk-head-actions`、`desk-user`、`desk-navigation` 的页面入口由上面逐页巡检覆盖；
`provisioning` 三个入口由开户三步实测覆盖；`upload-box` 15 个入口**没测到**——上传框代码也在 `product.js`，本次对 `product.js` 只改了
`p.register` 一行（给自有页面打标记），上传走弹窗不走页头。

## 生产数据修复（10-27 已做，用户同意，经主会话；只限 testcowork `pearnlyai@gmail.com`）

- 入口：本机控制面用换池同一个 `pearnly_control.account.repool._queue` 把开户单 `PR-2026-0288` 的 `DEP-2026-0289:set_roles` 排回待执行
  （载荷按当前目录 `api_reapply_payloads` 重算，与开户时逐字相同：管理员 Cowork Admin、基础 Desk User、功能 Cowork Intake），
  本机常驻开通执行器 11:58 跑完（「Cloud step verified」），站点上走 `provisioning.ensure_feature_roles`。不手改库、不进数据库容器。
  执行前控制面没有任何别的待执行步骤。
- 执行前预核：这次要写的三条表权限（Cowork Admin × User / Contact、Cowork Intake × Intake Record）与站点现有的逐字段相同，重跑只改角色。
- 回读：执行前 Cowork Admin、Cowork Intake、System Manager → 执行后 Cowork Admin、Cowork Intake、Desk User；修改历史 10-27 13:28（站点时间）
  Administrator 拿走 System Manager、补回 Desk User；三条表权限不变；两个原生按钮的条件（User 表 permlevel ≥ 1 可写、能建 Email Account）
  对这个账号都是 0，生产旧代码下个人页也不会再出这两个按钮。账号启用、System User。没有它的登录凭据、也不给真实账号建会话，
  「能正常登录使用」请用户刷新 / 重登确认。

## 同一缺陷的影响面（10-27 只读核对）

- 生产全部 7 个业务站点（testcowork、erpcowork、ccdemo、testerp、mrerp、testdms、ris）：现在没有业务账号带 System Manager，没有启用账号缺 Desk User。
- 10-19 以来 User 角色增删（`tabVersion`）：testcowork 三条（10-20 加 System Manager、10-26 丢 Desk User、10-27 修复）；ccdemo 10-20 新开户
  两条（建号临时加 System Manager，14 秒后「设角色」拿走，正常开户流程）；其余站点没有。
- 控制面 10-19 以来跑过的 `bind_user` / `set_roles`：只有 testcowork 换池（`bind_user`，没跟 `set_roles`——就是这次的缺口）与 ccdemo 开户
  （`bind_user` 后紧跟 `set_roles`）。

## 生产数据（原记录：未做，需用户授权——已于 10-27 完成，见上）

testcowork 的 `pearnlyai@gmail.com` 现在仍带 System Manager、少了 Desk User。部署本分支后按钮会被白名单藏住，但角色本身要改回：
拿走 System Manager、补回 Desk User（生产写操作，等用户同意；做法：web 容器里 `bench --site testcowork.pearnly.com execute` 调
`pearnly_core.provisioning.ensure_feature_roles`，按开户单重跑「设角色」，它会拿走 System Manager 并补齐基础角色）。

## 余项

- 原生表单级快捷键（例如新建）不经页头接口，不在白名单里；业务账号没有 System Manager 后，对应服务端权限照样拦。
- 不部署、不合 main；合入前跑生产镜像完整候选离线检查（见下方闸门）。

## 暂停点（10-26 晚，用户指示全部窗口就地暂停）

- 分支 `pearnly/native-buttons-whitelist`：`de4c9cbd`（生产缺陷修复：开户只给新建账号临时 System Manager、业务账号加 System Manager 一律拒、
  本人保存个人页不改角色、页头白名单）+ `9c2e2a7b`（A 节声明化：`pearnly_navigation` 的 actions / shortcuts、`check-entries.py`、快捷键白名单）
  都已推送。闸门：`de4c9cbd` 本地全闸门 130 步、候选离线 72 步都过；`9c2e2a7b` 本地全闸门 131 步过，**候选离线没跑完**（暂停时停掉）。
  基线仍是 `4561884f`，**没变基**到新 main `1dabbd06`。
- 主会话已定：应用切换的「网站」「显示」「刷新」照用户 09-20 口径保留（不算漏出），要告诉验收窗口把这三项移进基线——**还没发**。
- 生产 testcowork `pearnlyai@gmail.com` 角色修复（用户已同意，只限这一个账号）：**没开始**。
- 其他租户是否受同一缺陷影响：六站点 System Manager 已只读查过（只有 pearnlyai 一个）；六站点 Desk User、10-20 之后其他换池 / 绑定是否加过角色，**还没查**。
- 本机实验栈 `pearnly-native-lab` 容器已收（`docker compose down`，数据卷保留，`~/pearnly-devtools/native-lab/` 里 `docker compose up -d` 后
  重起 `bench serve` 即可续用）。

明天第一步（按顺序）：
1. 变基到最新 `origin/main`（≥ `1dabbd06`），重跑本地全闸门 + 候选离线，推送，看分支 CI；
2. 告诉验收窗口「网站 / 显示 / 刷新」移进基线；
3. 生产 pearnlyai 角色修复：执行前只读回读角色表 → 按开户单用正式入口重跑「设角色」（不手改库、不进 mariadb 容器）→ 回读角色表、
   确认个人页没有那两个按钮、账号能登录；
4. 六站点只读查 Desk User 缺失与 10-20 之后的换池 / 绑定记录，结论报主会话（不顺手改）；
5. 交验收复核差异，过了按主线集成先合 native-buttons。
