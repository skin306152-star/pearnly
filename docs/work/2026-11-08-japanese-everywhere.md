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

- CC 入口登录页 Keycloak 主题 pearnly-ai、宣传页 pearnly.com：主会话问用户，定了再做；先不动。入口页（fatcc.com，不是 Keycloak 主题）已加日文，给 Keycloak 的 `ui_locales` 是「ja en」，主题没有日文时用英文。
- 术语不一致：统一（见上）。
- LINE 开通手册（管理员看）：不补。
- 对账存档预生成 `recon_center/view_store.backfill`：不加日文（每次部署多算三分之一；日文用户首次打开现算、结果正确）。
- LINE 单独当一大项（DMS / ERP / Cowork 回复、Flex 卡片、快捷按钮 ≤ 20 字、LIFF、出错提示、绑定 / 登录、富菜单图上的字），见第 4 节。

中文原文问题：CC 桌面版两条用了半角逗号「,」，用户 11-08 确认（主会话转）改全角；`lang_toggle` 没有地方用（只报）。

| 词条 | 原文 | 新文 |
|---|---|---|
| companion:pair_err_probe_fail | 未能读取 Express 账套,请检查所选文件夹。 | 未能读取 Express 账套，请检查所选文件夹。 |
| companion:pair_err_unexpected | 操作未完成,请重试。 | 操作未完成，请重试。 |
