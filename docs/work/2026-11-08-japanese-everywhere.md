# 全站全产品补日语（2026-11-08）

用户原话：「我们没日语吗……如果全站全产品补的话怎么搞」「有日本客户」。主会话派活；分支 `pearnly/nice-mcclintock-b4b5b4`，基线 `e608efefb`。

平台语言 th / zh / en / ja（`pearnly_core/account/preferences.py` `LANGUAGES`）。只加翻译、不改中文原文；用词照术语表已有日文。

## 1. 盘点（只读，基线 e608efefb）

数法：术语表按 `awk -F'\t'` 数第 4 列为空的行；CC 网页按 `scripts/copygate/sources.ts_lang_tables` 解析各语言键；CC 桌面版按 `_M` 键数。

| 来源 | 现有语言 | 缺多少 | 语言怎么判定 | 日语用户现在看到 |
|---|---|---|---|---|
| 五个产品术语表 `apps/*/*/i18n/messages.tsv`（服务端 `_()`：Desk、LINE 卡片、CC 工具问话 / 步骤名 / 卡片、邮件、导出） | th / zh / en / ja | 0（core 1017、cowork 2368、erp_portal 1922、dms 432、control 353 行） | 各自规则 | 日文，但 CC 对话里的服务端文案按页面语言，见下一行 |
| CC 网页 `frontend/assistant/src/i18n.ts`（9 段：MESSAGES / STEPS / SETTINGS / COMPANY / SIDEBAR / OPERATOR / RUN / ASK） | en / zh / th | 整块 ja：约 640 个键 | 页面 `<html lang>`，由 `pearnly_core/account/ui_language.py` 定：设备记住的 → 账号 → 浏览器 → 英文 | **英文** |
| 对话页语言门槛 `ui_language.UI_LANGUAGES` | th / zh / en | 列表缺 ja | 同上；还影响 `identity.py:111`、`preferences.py:97/110-115/123`（页面请求头） | 账号选了日语也回落**英文**；CC 回复、工具卡片、步骤名、侧栏标题跟着成英文（`toolrun.remember_lang` 记下 en） |
| CC 入口页 fatcc.com（`pearnly_control/assistant_entry/contracts.py:11-13`、`www/ai.py:13-15`） | th / zh / en | 列表缺 ja（文案走 `_()`，ja 齐） | cookie / Accept-Language | **泰文**（默认 th） |
| CC 桌面版 `companion/src/companion/i18n.py` `_M`（配对窗、托盘、通知、活动记录、报错） | zh / th | 143 条 × en、ja | `config.ui_lang` → Windows 界面语言（中→zh，其余→th）；网页切语言经 `tomo_bridge.py:108` 只收 zh / th | **泰文**（日文 Windows 也按 th）；网页选日语不跟 |
| CC 桌面版安装包 `companion/packaging/installer.iss` | english / thai / chinesesimplified | 缺 Japanese | Inno `LanguageDetectionMethod=uilanguage` | 英文 |
| CC 入口登录页 Keycloak 主题 `pearnly-ai`（`scripts/build-ai-entry.py` 生成） | th / zh-CN / en | 6 键 | Keycloak locale | 泰文（未实测）——登录页定过不改，只核对 |
| 主登录页 Keycloak 主题 `pearnly`（login / email） | 四种 | 0（th、zh_CN 多一个 `accountUpdatedTitle`，en、ja 都没有） | Keycloak locale | 日文 |
| LINE 消息 / 卡片 / 富菜单 / LIFF | 四种 | 0 | `pearnly_core/line.py:701` 按手机 LINE 语言 → 站点 → th | 日文 |
| 邮件（欢迎邮件） | 四种 | 0 | 开通单字段 | 日文 |
| 导出（对账、银行、盘点、ERP 输出） | 四种 | 0 | `_()` | 日文 |
| ERP 打印格式 `printing/templates/*.html` | 泰英双语（设计如此） | — | 写死 | 泰英双语 |
| 智能体系统提示（`definition.py`、`LANGUAGE_RULE`、`engines/base.py`、`direct_push.py`） | 不写死语言 | 0 | 「用界面语言回复」 | 跟界面语言（门槛修好后为日文） |
| 其他前端 `frontend/src`（LINE 编辑器，Vue） | 四种 | 0 | 回落 th | 日文 |
| PWA 清单（`frontend/assistant/vite.config.ts`） | 只有 "CC"，不分语言 | — | — | "CC" |
| 对账存档预生成 `recon_center/view_store.py:167` | th / zh / en | 列表缺 ja | 部署参数 | 正确，首次打开现算 |
| LINE 开通手册 `pearnly_control/onboarding/line_guide_content/`（管理员看） | 只有 zh | 整份 | 后台语言 | 中文 |
| 宣传页 pearnly.com `website/content.py:32` | th / en / zh | 约 23 组 + 正文 | URL 前缀 | 泰文首页 |

术语表自身的不一致（盘点时）：账套 = 会計データ（cowork 55）/ 帳簿（erp 23、core 6、cowork 29）；增值税 = 消費税 / 付加価値税；电脑 = コンピューター（28）/ PC（10）/ パソコン（8）/ 端末（9）。

**已统一**（主会话 11-08 定，理由：日文用户看到同一个东西两三种叫法是质量问题）：账套 = 会計データ；增值税 = 付加価値税（泰国税种，不用日本的「消費税」；单独成词写「付加価値税（VAT）」）；电脑 = パソコン。锁定镜像里的 Frappe / ERPNext 没有日文译文，没有官方用词可跟。只改日文、不改中文；「帳簿」作「账面（数量）」的 19 条是另一个概念，不动。改动清单见 [日文统一用词改动清单](2026-11-08-japanese-terms-changes.md)（术语表 104 条、翻译包手工行 3 条、CC 网页 21 处、桌面版 7 处）。

## 2. 施工分批

1. 盘点表（本文件，docs）
2. CC 网页 ja：`i18n.ts` 全部段落 + `SUPPORTED` / `LANGUAGES` / 日期区域 + `ui_language.UI_LANGUAGES`
3. CC 桌面版 en + ja：`_M` 扩成四语、`normalize` / 默认语言按 Windows（日→ja、英→en）、桥接收四种、安装包加 Japanese
4. 其余：CC 入口页语言列表、对账存档预生成；`check-copy` 拦 CC 网页 / 桌面版缺语言；`interface-copy.md` 写四语硬性要求

## 3. 主会话的决定（11-08）

- CC 入口登录页 Keycloak 主题 pearnly-ai：用户「行」，**只加日文一种**，现有文字和布局一字不动。源头 `scripts/build-ai-entry.py`：`locales` 加 ja、6 个键的日文（用词同 pearnly_control 术语表）、语言切换条多「日本語」一项（与入口页 fatcc.com 一致）、日文点服务条款 / 隐私政策跳英文版宣传页；其余键继承 pearnly 主题已有的日文。**部署时主题随镜像生效**（Keycloak 主题在镜像里，不用控制台操作）。验收按身份页规矩：部署后日语浏览器打开 fatcc.com 截图。
- 入口页 fatcc.com（站点页面，不是 Keycloak 主题）：语言切换加「日本語」，给 Keycloak 的 `ui_locales` 为 ja。
- 宣传页 pearnly.com：这次不做日文；CC 网页、入口页、登录主题里日文用户点宣传页链接跳英文版。
- LINE 语言规则（用户选 C）：照旧跟手机 LINE 的语言；只改回落（手机语言不在四种里或问不到时，已绑定的用网页个人资料语言）。实现见第 4 节，在单独分支。
- 术语不一致：统一（见上）。
- LINE 开通手册（管理员看）：不补。
- 对账存档预生成 `recon_center/view_store.backfill`：不加日文（每次部署多算三分之一；日文用户首次打开现算、结果正确）。
- LINE 语言规则（用户选 C）：照旧跟手机 LINE 的语言（用户 09-26 / 09-27 口径）；只改回落——手机语言不在四种里（如韩文）或问不到时，已绑定的用网页个人资料语言，没绑定的才回落站点语言。改在共用层 `pearnly_core/line.py` `user_language` 一处，ERP / Cowork / DMS 回复与从 LINE 打开的编辑页同一规则；先写红的测试 `test_line_intake.Language.test_unsupported_phone_language_uses_the_bound_account_language`（韩文手机 + 资料日语 → 日文），改后变绿。
- LINE 单独当一大项（DMS / ERP / Cowork 回复、Flex 卡片、快捷按钮 ≤ 20 字、LIFF、出错提示、绑定 / 登录、富菜单图上的字），见第 4 节。

中文原文问题：CC 桌面版两条用了半角逗号「,」，用户 11-08 确认（主会话转）改全角；`lang_toggle` 没有地方用（只报）。

| 词条 | 原文 | 新文 |
|---|---|---|
| companion:pair_err_probe_fail | 未能读取 Express 账套,请检查所选文件夹。 | 未能读取 Express 账套，请检查所选文件夹。 |
| companion:pair_err_unexpected | 操作未完成,请重试。 | 操作未完成，请重试。 |

## 4. LINE（主会话 11-08：单独当一大项）

**合入安排（主会话 11-08 选 C）**：本节的 LINE 改动（回落规则、按钮改短与 check-line-labels、日文手打词）在单独分支 `pearnly/japanese-line`，不随本分支合；部署前主会话安排测试机 lab-start（testerp / testcowork 切到合好的 main 实时源码），用三星真测产品地图上只能真机的两行（`cowork.scan.line.stocktake.liff-camera`、`erp.line.postback.erp_item`），结果写进本记录后再合、随同一批部署。

数法：五份术语表 ja 列逐行查（空 0 条）；LINE 代码里实际用到的词条逐个对翻译包（ERP 122 处、Cowork 114 处、DMS 212 个 `dms.*` 键，日文全有）；按钮由 `scripts/check-line-labels.py` 静态认出（61 条）。

| 产品 | 来源 | 文件 | 现有语言 | 缺 ja | 语言判定 | 日文用户看到 |
|---|---|---|---|---|---|---|
| ERP | 文字回复 / 出错 | `integrations/handlers.py`、`line_flow.py`、`line_items.py`、`intake.py` | 四种（`_()`） | 0 | `line.user_language`；后台识别用单子上记的语言 | 日文 |
| ERP | Flex 卡片 | `integrations/line_cards.py` | 四种 | 0 | 同上 | 日文（币种缺省 `THB` 是代码） |
| Cowork | 文字回复 / 出错 | `line/handlers.py`、`flow.py`、`items.py`、`intake.py` | 四种 | 0 | 同上 | 日文 |
| Cowork | Flex 卡片 | `line/cards.py` | 四种 | 0 | 同上 | 日文（`THB`、MR.ERP、Express 是专名） |
| DMS | 文字回复 / 卡片 / 出错 | `line/*`、`booking/conversation/*`、`query/*`、`menu/*`、`intake/*`（`i18n.t`） | 四种 | 0 | `i18n.line_language` 包住入口与后台任务 | 日文 |
| DMS | 识别失败提示 | `line/out.py` `ocr_failed` | 四种 | 0 | 绑定账号的语言 | 账号语言 |
| 三产品 | 快捷按钮 / Flex 按钮 / 富菜单格子 label（≤ 20） | 见上 | 四种 | 0 | 同回复 | 日文最长 13 字，无超长 |
| 三产品 | 加好友欢迎语、绑定成功 / 失败、解绑 | 三个 handlers | 四种 | 0 | 跟手机 LINE 语言 | 日文（LINE 后台自配的欢迎语不在代码里） |
| core | LINE 登录 | `account/line_login.py` | 四种 | 0 | 访客按请求 / 系统语言 | 日文 |
| ERP / Cowork | 从 LINE 打开的 Desk 页（编辑、盘点、工作协调） | `line_login.enter` → `/desk` | Desk 四种 | 0 | Frappe 账号语言 | 账号语言 |
| DMS | LIFF 交接页 | `www/liff/dms-booking.html` | 四种 | 0 | 本地记住的 / `navigator.language`，换票后用服务端给的（手机语言 → 账号） | 日文（`<html lang="th">` 写死） |
| DMS | 编辑 / 复核 / 账号页 | `frontend/src/pages/dms/*.vue` | 四种 | 0 | 交接页写入的语言 | 日文 |
| DMS | 扫身份证页 | `ScanPage.vue` | 四种 | 0 | 只看缓存与浏览器语言 | 浏览器语言 |
| ERP | 手机盘点页（疑似旧页，菜单已去 /desk） | `www/stocktake.html` | 四种 | 0 | `navigator.language` | 日文 |
| 三产品 | 富菜单图 | ERP / Cowork `public/images/line-menu/tiles/*-ja.png`；DMS `rich-{basic,query}-ja.png` | 四种图 | 0 | 跟手机语言挂对应那套 | 日文图（字来自术语表） |
| 三产品 | 富菜单 chatBarText（≤ 14） | 各 `menu.py` / `rich_menu.py` | 四种 | 0 | 同上 | 「メニュー」「ERPメニュー」「Coworkメニュー」，都不超 |
| 三产品 | 还没绑定的人看到的默认菜单 | — | 只有泰文图 | — | — | 泰文 |

### 语言回落规则（用户选 C）

照旧跟手机 LINE 的语言（用户 09-26 / 09-27 口径）；只改回落——手机语言不在四种里（如韩文）或问不到时，已绑定的用网页个人资料语言，没绑定的才回落站点语言。改在共用层 `pearnly_core/line.py` `user_language` 一处，ERP / Cowork / DMS 回复与从 LINE 打开的编辑页同一规则；先写红的测试 `test_line_intake.Language.test_unsupported_phone_language_uses_the_bound_account_language`（韩文手机 + 资料日语 → 日文），改后变绿。

### 按钮超 20 字：改短（主会话：截断属缺陷）

日文没有超长的；其他语言 3 条，改成合规的正式说法：

| 词条 | 语言 | 旧 | 新 | 说明 |
|---|---|---|---|---|
| Cowork「发送单据至 ERP」按钮（`line/flow.py` 快捷按钮、`line/menu.py` 富菜单格子 label） | en | Send documents to ERP（21） | Send to ERP（11） | 新起一条英文原文 `Send to ERP`，中泰日照原样；菜单图上的全称与「请先点击菜单中的…」提示不变，点格子后聊天里显示的仍是全称 |
| `dms.card.query.ST_MANAGER_PENDING` | th | รอผู้จัดการฝ่ายขายอนุมัติ（25） | รอผจก.ฝ่ายขายอนุมัติ（20） | ผจก. 是泰文「经理」的通用缩写 |
| `dms.card.TXT_MENU_ITEM4` | th | เปลี่ยนบัญชีและรหัสผ่าน（23） | บัญชีและรหัสผ่าน（16） | 同英文 Account & password、日文 アカウントとパスワード；菜单图上的字（`dms.card.rich.credentials.title`）不变 |

防再犯：`scripts/check-line-labels.py`（Development checks）在 LINE 模块里认出按钮标签用到的词条，四种语言任一超 20 字就拦；由数据拼成的按钮（公司、供应商、仓库名）不归它管，运行时照旧截短。

### 其他发现（日文用户受影响，待主会话定是否做）

1. ERP 打字「メニュー」不认（Cowork、DMS 认）：`integrations/line_flow.py` 的菜单词。
2. ERP、Cowork 打字「連携解除」不认（DMS 认），日文用户只能打 `unbind`：两个 `handlers.py` 的解绑词。
3. ERP 打字选采购 / 销售不认「仕入」「売上」：`line_flow.py` 的方向词只有泰文、英文、数字。
4. DMS LIFF 交接页 `dms-booking.html` 预取的文案键里没有 `dms.edit.expired`，草稿过期时页面显示键名（所有语言都受影响，不是日文专属）。
5. DMS 卡片里 `_btn` / `_qr_item` 等没截断（词条已由新检查守住，动态数据目前没有用到这些函数）。

## 5. 验收（本机实验栈 pearnly-japanese-lab，2026-11-08）

截图与样例在共享笔记 `side-session-notes/…/日语验收-11-08/`（不进仓库）。账号：实验站 Administrator，网页个人资料语言设为日语（实验数据，用完随实验栈删除）。

| 项 | 结果 | 证据 |
|---|---|---|
| CC 网页 ERP 直写 / 代录，电脑 + 手机 | 通过：`<html lang="ja">`，问候「こんにちは、Administrator」、侧栏、输入框、公司胶囊都是日文，页面无报错 | `CC直写-erp-电脑.png` 等 8 张 |
| CC 网页 Cowork 直写 / 代录，电脑 + 手机 | 通过：同上 | 同上 |
| Desk 首页 ERP / Cowork / DMS | 通过：`frappe.boot.lang = ja`，菜单与首页日文 | `Desk首页-*.png` |
| LINE 消息样例（ERP，打「メニュー」「仕入」） | 通过：日文提问与按钮；日文手打词被认出 | `LINE样例与导出.md` |
| 导出（ERP 库存导出） | 通过：日语账号表头日文，泰语对照泰文 | `LINE样例与导出.md` |
| CC 桌面版 Qt 窗口（托盘菜单、MR.ERP 登录小窗、账套占用提示） | 通过：本机临时 venv（PySide6 6.11.1，同锁定版本）用真实代码出窗截图，截完删 venv；发现并修了登录小窗 Save / Cancel 不跟语言 | `托盘菜单-ja.png` 等 6 张 |
| CC 桌面版通知、真机效果 | 没测到：主会话定放进 1.12.18 发版后测试机验收清单（装正式版时用日本語看一遍） | — |
| LINE 真机（三星） | 没测到：主会话定部署后线上验收时做 | — |
| fatcc.com 登录主题 pearnly-ai 日文 | 没测到：实验栈没有 Keycloak；主题随镜像生效，部署后用日语浏览器打开 fatcc.com 截图 | — |

受影响流程（`scripts/flows/run.py --changed --base origin/main --migrate`，本机实验栈）：cc.chat、cc.companies、cc.person-names、cc.sidebar-modes、cowork.cc.sidebar-modes、cowork.mrerp-login、cowork.pending.operator，19/19 格通过（base e608efefb，源码为 ae5e95f67 之前的工作树）。

### 共用件连带闸门（第一批 2bce6d357 命中 login-session、cc-desktop-bridge 等 8 个入口）

| 入口 | 结果 | 说明 |
|---|---|---|
| cc.assistant | 通过 | cc.chat 等流程 19/19；日文截图无报错 |
| cc.settings.connection | 通过 | cc.sidebar-modes 流程打开设置；日文截图无报错 |
| shared.login | 没测到 | 实验栈没有 Keycloak。改动只让语言规则多认 ja，登录回调里语言的取舍由离线 `tests/test_assistant.py` InterfaceLanguageTests（9 条，含日文）守住 |
| control.issue-and-entry | 没测到 | 实验栈没有控制面站点；入口约定由 `tests/test_assistant_entry.py`（33 条，含日文 ui_locales）守住 |
| shared.shell-gate | 没测到 | 改动不碰开通闸；Desk 三产品首页日文打开正常 |
| shared.team.members | 没测到 | 改动不碰团队页；团队页改语言写的是同一个 User.language，对话页按它显示 |
| shared.line.login | 没测到 | 实验栈无 LINE 通道 |
| cc.desktop.bridge | 没测到 | 要 Windows 测试机；第一批不含桌面版改动 |

## 6. 11-09 部署后真机与语言表缺陷

部署 release-20261109-0845（45701eabd）后用三星（LINE 账号 Skin，绑 testerp / testcowork 的用户本人账号，只读操作）实测。
LINE 原设置「自动（默认）」= 系统中文；测试时改日本語，测完改回。

- **网页语言下拉缺日本語（缺陷，已修）**：testcowork「个人信息 → 语言」搜 th 有 ไทย，搜 ja 无结果。
  原因：锁定 Frappe 的 `frappe/geo/languages.csv` 把 `ja,日本語` 标为停用（en / th / zh 为 1）；下拉与
  `frappe.translate.get_all_languages` 只列启用的。四种语言在 Frappe 里的代码是 th / zh / en / ja（中文是 `zh`，`zh-CN` 只是身份服务的写法），名字都对，只有 ja 停用。
  修法（分支 `pearnly/language-records-enabled`）：`pearnly_core.account.languages.ensure_product_languages` 挂 `after_migrate`
  （已有站点随部署迁移，新开户收尾回跑 `after_migrate`，补丁在新站点不执行所以不用补丁），四种缺记录先按框架表补、补完仍缺就让迁移失败；
  停用的用原生保存启用（清启用语言缓存）。测试 `pearnly_core.account.test_languages`（先红：钩子没挂时失败）进候选离线；
  流程 `erp.profile.save` / `cowork.profile.save` 加「语言下拉四种都能选、只看不存」与库内四种启用的断言。
- 测试账号的账号语言（testcowork 用户 pearnlyai@gmail.com）为「中文」，下拉只看未保存，未改。
- 验证（实验栈 pearnly-language-lab，源码 = 本分支）：`erp.profile.save`、`cowork.profile.save` 各 4/4 通过（new/old × 电脑/手机）。
  会红：把 erp-old 站点 Language ja 临时停用后同一格不通过（`ja` 搜不到、库内 ja=0，与三星上的现象一致），
  跑 `ensure_product_languages` 后恢复启用、同格通过。gate 过；候选离线 105 组 LANE EXIT 0（镜像 sha256:9fef288f…，11-09 部署那份）。
- 连带入口（login-session 共用件，只动迁移时的语言记录，不碰登录 / 会话代码）：
  shared.team.members 团队页改语言 —— 同一个 Language 下拉，个人信息流程已覆盖下拉能选四种，团队页本身没测到（实验栈无团队页流程，部署后在 testerp 团队页看下拉）；
  shared.login、control.issue-and-entry、shared.shell-gate、shared.line.login —— 没测到（不经语言表，本次改动不触及；部署后 e2e 登录 / 退出照常跑）。

### 11-09 三星 LINE 日语实测结果

| 项 | 结果 | 证据 / 原因 |
|---|---|---|
| LINE App 切日语 | 通过 | 设置 → 言語 → 日本語，LINE 自身界面变日文（截图 07） |
| testerp 官方号回复（采购录入的提问与快捷按钮） | 不通过（仍中文） | 17:30 与缓存过期后 18:35 两次都是「请选择上传识别或手动录入 / 上传识别 / 手动录入 / 取消」（截图 11、44） |
| testcowork 官方号回复（发单据进 ERP） | 不通过（仍中文） | 17:32 与 18:36 两次都是「选择单据推送目标 / MR.ERP / Express / 识别后导出 / 取消」（截图 18、52） |
| 富菜单图与菜单栏字 | 不通过（仍中文） | 「ERP 菜单」「Cowork 菜单」中文图块；菜单随服务端判定的语言重挂，判定仍是中文 |
| 网页端（LINE 进 testerp 盘点 / testcowork 库存盘点） | 按设计中文 | 网页跟账号语言（中文）走，不跟 LINE；改账号语言的下拉缺日本語（上面的缺陷，已修待部署） |
| 全角逗号（CC 桌面版 pair_err_probe_fail / pair_err_unexpected） | 通过（源码层） | 线上 latest.json = 1.12.19，构建自 45701eabd，两条为全角逗号；界面只在配对出错时出现，没触发到 |

判断：服务端按 LINE 资料（profile）的 `language` 出回复（`pearnly_core.line.user_language`，缓存 1 小时）。缓存过期后仍是中文，
说明 LINE 资料里的语言没有跟着 App 内「言語」设置变（手机系统是中文，原设置是「自动」）。
11-10 只读核实（两个官方号各 GET 一次资料，令牌与用户号不打印）：testerp、testcowork 对这位测试用户的资料语言都是 `zh-Hans`，缓存为空 —— 回复用中文符合设计，不是缺陷。
之后用户把三星系统语言切成 ja-JP（LINE「言語」为「端末の言語」、界面已日文），重启 LINE 后 12:28、12:43 两次、手机重启后 12:52 再一次只读查，两个官方号的资料语言仍是 `zh-Hans`：LINE 资料语言不随手机或 LINE 设置及时变（官方文档未写更新时机）。真机日语回复记「没测到」；资料语言为 ja 时回日语、挂日语富菜单由 `test_line_intake` 覆盖。期间没给官方号发消息（资料仍是 zh 时发消息会把 zh 缓存 1 小时）。
测完 LINE 语言已改回「自动（默认）」（截图 57），两边对话各点了一次「取消」，没留下待办。截图在共享笔记 `日语真机-11-09/`。
