# Cowork 前端：录入工作台（接后端）

## 当前入口 · 换窗口交接（2026-09-27 第四次，新窗口从这里开始）

**状态：交接保留。** 用户：“现在你做交接，下个窗口准备做新功能，新功能待我通知”“我是在 cowork 基础上开发新功能”——
新窗口**先读本节，等用户说新功能再动手**，不要自己挑活。
> 2026-09-27 第五个窗口：新功能是**工作协调（团队看板）**，记录在 [2026-09-27-cowork-work-board.md](2026-09-27-cowork-work-board.md)。用户口径见下方「用户定下的口径」和用户记忆 `cowork-frontend-ui-rules`（中文、先说结果、不多加文案、没有确认步骤）。

### 代码与线上

| 位置 | 状态 |
| --- | --- |
| `origin/main` | **已含本任务全部代码**：第十四批验收修复（公司隔离拿掉、LINE 发单据 + 富菜单、识别配置、目标记在票上、ERP 式卡片、编辑页照 ERP 排版、含税小票导出）经用户同意 2026-09-27 合入（`8b26357`，CI 通过、远端回读一致）+ 共用票种表（ADR-0012）+ 测试环境实时源码规则 |
| 任务分支 `codex/cowork-frontend`（工作树 `/Users/skin/.codex/worktrees/cowork-frontend/pearnly`） | **已删除**（2026-09-27 第五个窗口）：测试机实时源码与本机样板改到新工作树后，删了工作树、本地分支与远端分支（删前回读均为 `af9aa34`，无独有提交） |
| 生产 | Cowork 2026-09-27 升到 `c4a04ca8`（任务管理、三个对账可单独授权与新页面，见 [工作协调任务记录](2026-09-27-cowork-work-board.md)），ERP 推送总闸已开；Cowork 账号池、`COWORK_OCR` 识别配置仍**没上生产**，每一步要用户同意 |

**新功能怎么开分支**：新功能在 Cowork 基础上做，从最新 `origin/main` 开自己的 `codex/<主题>` 分支与工作树（记录基线 SHA）。
然后把测试机实时源码切到新工作树：`PEARNLY_TESTENV_SRC=<新工作树> bash scripts/testenv.sh live cowork`；
本机样板要用时把 `pearnly-sample` 的 Cowork 容器改挂新工作树。两处都不再用旧工作树后，删掉 `codex/cowork-frontend` 工作树与本地 / 远端分支（已合入）。

### 测试环境（2026-09-27 起「实时源码」，三个产品同一规则，见 AGENTS.md「真实环境验证」）

- `https://testcowork.pearnly.com` **切在测试机上**（测试机不能关机），Cowork 栈运行中；底座镜像 `sha256:7eb33e678729efdb33f055128c71810c73b6dc81aba85071da2e8d8ff03aaea2`
  （本分支 `146faba` 构建），上面挂着工作树的实时源码（`/opt/pearnly-testenv/live-cowork.yml`）。
- **改完代码**：`PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh live cowork`（约 13 秒；表结构 / 补丁 / hooks 变了自动迁移，约 35 秒），
  用户刷新即见。不用推分支、不用构建镜像。依赖 / Dockerfile / 底座 / `frontend/` SPA 改动才要换镜像。
- **新功能在另一个工作树**：`PEARNLY_TESTENV_SRC` 指向新工作树再跑 `live cowork`，测试机就换成那份代码（同一时间只挂一份）。
- 测完：`bash scripts/testenv.sh lab-stop cowork`（切回线上、停栈、卷保留）。
- 登录走线上 `auth.pearnly.com/realms/pearnly-business`，测试账号 `pearnlyai@gmail.com` 由用户自己登，不替用户输线上密码。
- 识别在测试机是**真实付费调用**（配置 economy + vertex）；账本是测试副本：`TU-2026-00073` 1000 泰铢已按用户要求在测试账本审核通过
  （切在测试机时充值只进测试账本，本机管理后台看不到）。
- LINE：测试官方号 Cowork-TEST（`@771hffyh`）已由新平台发布富菜单并挂给已绑定的 `pearnlyai@gmail.com`；菜单配置
  `pearnly_cowork_line_menus` 只在测试机站点配置里。生产官方号没动。

### 测试机上的测试数据（均属本任务）

testcowork 上 3 张识别记录（`IV6906-00001` ×2 进项、`02000137` 销项，都是“识别后导出”，其中第一张的 `intake_target` 是手工补记的）、
自动建的站点内部归属行、LINE 绑定一条。删前与用户确认。

### 未验 / 待办

1. LINE 盘点格在 LINE 内置浏览器打开 Cowork 盘点页：Cowork 没接 LINE 登录，要用户自己登一次（`LINE_LOGIN_PRODUCTS` 只有 erp，属共用身份改动，随账号池做）。
2. Express 整链真机（小助手在线、推送）；非默认账套推送缺小助手缓存（旧待办）。
3. 生产待授权：`COWORK_OCR` 识别配置（`site-step.py`，每个 Cowork 租户都缺，否则识别报“请先配置”）、Cowork 独立账号池（第十一批顺序）、
   正式官方号富菜单发布、上线前按站点核对「公司资料」份数（多份时只用最早那份）。
4. ERP 窗口的 Cowork 识别提示词修正（7-Eleven 类小票单号取 R# 流水号、票面印了 VAT 照抄）已合入 main `5b8af27`（2026-09-27），
   ERP 已随 `76b6a6e` 上生产；Cowork 以后发布要用 `76b6a6e` 或更新的镜像。仍排给 Cowork 的后续：小票没印 VAT 时模型自算 VAT 并编造来源文字、
   一张图多张票只读出第一张；更早登记的：识别抄 `document_title` 并套 classify、对账按票种给配对键、ABB 未印 VAT 时模型自算 VAT、
   Tops 部分含税 VAT 读错（3.93 → 22.84）。
5. 工作协调（WeKan 替代）用户要重新设计，富菜单上是锁；旧菜单那格回“还没开放”。

### 本地环境与保留项（均属本任务）

| 保留项 | 用途 | 解除条件 |
| --- | --- | --- |
| ~~工作树 `/Users/skin/.codex/worktrees/cowork-frontend/pearnly` + 分支 `codex/cowork-frontend`~~ | 已删（2026-09-27） | — |
| 本机样板 `cowork-a.localhost:8101`（本机 `pearnly-cowork` 账号池，测试管理员密码在 `~/.config/pearnly/cowork-realm.env`） | 本机验证 | 样板切回或废弃时删 `/tmp/cowork-a-oidc-backup.json` |
| 测试机 Cowork 栈、`testcowork` 切在测试机、`/opt/pearnly-testenv/live/cowork` | 用户验收 | 测完 `lab-stop cowork`；不再需要实时源码时 `live-off cowork` |
| 演示数据（第 2~6 批）、test01 上真推的 8 张单据与自建主档 | 录入演示 | 删前与用户确认 |

## 当前入口 · 换窗口交接（2026-09-26 第三次，已被第四次取代，留作历史）


**状态：交接保留。** 用户：“做交接，发给新窗口，我测好问题发出来修复”——新窗口的工作是**等用户在测试环境测出的问题，逐个修复**。

### 代码与线上

| 位置 | 状态 |
| --- | --- |
| `origin/main` `02a3b22` | 已含本任务全部功能（合入提交 `c24e24c`，CI 通过）+ ERP 窗口的扫码器去重 |
| 任务分支 `codex/cowork-frontend`（已推送，工作树 `/Users/skin/.codex/worktrees/cowork-frontend/pearnly`） | 原 3 个待合提交已变基合入 main `77716e5`（第四个窗口，CI 通过、远端回读一致）；之后的验收修复见第十四批 |
| 生产 | Cowork 线上仍是 `15dea26`（ERP 窗口 2026-09-26 为补 LINE 登录核对升级）；本任务的 Cowork 新功能与账号池**都未部署** |

新窗口修问题：从最新 `origin/main` 开自己的 `codex/<主题>` 分支与工作树（或在本任务分支上继续，先 `git fetch` 变基），
改完走主线规则（本地同名闸门 → 推分支等 Development checks → 主目录快进 main → 推送 → 回读）。
构建检查脚本已改为 `scripts/ci-candidate-checks.sh` 三条并行线（ERP 窗口 `02a3b22` 前后），新测试加进该脚本，不再加 workflow。

### 测试环境（用户正在测）

- `https://testcowork.pearnly.com` 已**切到测试机**（Cloudflare CNAME → 隧道），测试机 Cowork 栈**运行中**，镜像 main `02a3b22`
  `ghcr.io/skin306152-star/pearnly/frappe@sha256:3a1d5f26d985ad423795434d319826656bd5d8d86d95f7226cc9bd573361f382`。
- 登录走线上 `auth.pearnly.com/realms/pearnly-business`（Cowork 独立账号池线上未建），测试账号由用户自己登录；不要替用户输入线上密码。
- 修好一个问题（2026-09-27 起实时源码）：`PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh live cowork`，约 13 秒生效，
  不用推分支、不用构建镜像；表结构 / 补丁 / hooks 变了自动迁移。用户试用通过后再合入 main、从 main 构建镜像上生产。
- 测完：`bash scripts/testenv.sh lab-stop cowork`（域名切回线上、停栈、卷保留）。**切在测试机时测试机不能关机。**
- OCR 在测试机是线上真凭据：上传单据识别、图片 / PDF 发票都会产生**真实付费调用**，用户授权后才做。
- 做法与坑见 [测试环境](../development/test-environment.md) 的 Cowork 一节、第十三批。

### 这一任务做完了什么（细节见下方各批）

- 录入工作台（MR.ERP 推送整条、Express、仅识别导出、复核、列表）——第一~五批；团队管理 + LINE 绑定——第六批；
  库存盘点（照搬 ERP，只去掉盘点在库）——第八批；对账中心三个入口（银行 / 收入 / 销项税报告核查），结果页照导出的 Excel 逐格画、
  可改可存、自动重算——第九、十批；图片 / PDF 发票走录入同一识别引擎（持久 Work `cowork.vat_check`）——第十批；
  分支号口径（发票没读到分公司就不比）——第十批；三种复核表统一外观（去 KPI 与说明文案，页面与导出同步）——第十二批。
- Cowork 独立账号池 `pearnly-cowork`（控制面按产品分池开户 / 开通 / 拆除；生产脚本已备好）——第十一批，**生产步骤未执行**。
  LINE 后台已由用户登记 Cowork 池回调（三条回读一致）。

### 用户定下的口径（继续有效，也记在用户记忆 `cowork-frontend-ui-rules`）

- 界面不照搬旧项目（重构），业务规则 / 第三方口径以旧线上 `ec2e5c95` 为准；不多加文案；没有「确认」步骤；手机端照 ERP。
- 推送：提交即推送整批；目标按第一次选择；结果不明不放开重新推送（一单推完再下一单）；状态三种，原因悬浮看，不弹窗。
- 不做：公司主档、推送日志页、旧项目附带功能。

### 未验 / 待办

1. 用户在 testcowork 的逐项业务验收（进行中）；真实识别（付费，需授权）；Express 整链与新小助手真机。
2. 非默认账套的 Express 推送不带小助手缓存，可能部分票转失败——真机验证时看；Express 小助手旧 URL 的 `companion.wsgi` 入口未挂出。
3. Cowork 独立账号池上生产（每步需授权，顺序见第十一批）：建 realm → nginx 放行 → 成员服务 cowork 三键 → 控制面接池 → 把 testcowork 迁到 Cowork 池。
4. 本机控制面站点（挂长期主目录，共享）需要 `bench migrate` 才有 `cowork_*` 新列；未迁移时本机 Cowork 开户 / 拆除会报缺列（未动，已告知 ERP 窗口）。
5. 已知既有问题：银行对账 `document_model_rows` 在识别任务外调模型（文字层读不出的 PDF 账单会报 `ocr.durable_work_required`）。

### 本地环境与保留项（均属本任务）

| 保留项 | 用途 | 解除条件 |
| --- | --- | --- |
| 工作树 `/Users/skin/.codex/worktrees/cowork-frontend/pearnly` + 分支 `codex/cowork-frontend` | 本机 Cowork 样板容器挂载它；3 个待合提交 | 待合提交进 main、样板改挂别处或废弃后删 |
| 本机 `cowork-a.localhost:8101` 已改签到本机 Keycloak 的 `pearnly-cowork`（测试管理员 `tenant-admin`，密码在 `~/.config/pearnly/cowork-realm.env`）；原 OIDC 配置备份在 cowork-web 容器 `/tmp/cowork-a-oidc-backup.json` | 本机验证 Cowork 独立账号池 | 样板切回或废弃时删备份 |
| 本机 Keycloak 新 realm `pearnly-cowork` 及其客户端 | 同上 | 随样板保留 |
| 测试机 Cowork 栈与卷、`testcowork` 切在测试机 | 用户验收 | 测完 `lab-stop cowork` |
| 演示数据（第 2~6 批）、test01 上真推的 8 张单据与自建主档 | 录入演示 | 删前与用户确认 |

本机样板：改后 `bash scripts/dev-reload.sh cowork --sites cowork-a.localhost`（改 DocType 加 `--migrate`）；
电脑重启后 `docker start` mariadb / 两个 redis，colima DNS 失效见第四批。

## 第十四批：用户验收修复（2026-09-26，第四个窗口）

用户在 testcowork 测出三处：LINE 菜单除工作协调外两格点了没反应；银行对账报“无法确定这条记录归属的公司资料”；
新建录入里两张 ERP 卡片不显示连接按钮与状态。用户口径：**新平台不做公司数据隔离，以前依赖公司的都删掉**。

**公司（修复 2、3，同一根因）**：testcowork 里 `Cowork Workspace` 为 0 行（只读查询），各域 `default_workspace()` 报错，
页面与连接列表全挂。后端迁移时照旧版做成“一站多公司、按公司授权”，28 张表都挂 `workspace`。

- 去掉按人分公司 / 客户：`access.scope`（`contains` / `_scope_query` / 公司与客户的 permission / query）、`recon_access`、
  `stocktake.access`、`connections.access.require_scope` 不再查 User Permission；团队管理添加用户不再分配公司。
- `workspace` 列留作站点内部归属键（**不删列**：推送防重、写者锁、幂等键、识别反馈范围键都以它为锚，换键等于关掉这些保护）：
  `scope.ensure_site_workspace()` 取最早一份启用行，没有就在 DocType 行锁下建一份（名 = 站点名，不经管理员校验）；
  `after_install` / `after_migrate` 先建好；连接域经 `masters.workspaces.site_workspace()` 取同一实现。
- 补丁 `v0_0_3_drop_company_grants`：删掉 `allow` 为 Cowork 公司 / 客户的 User Permission（否则框架自身的 User Permission
  过滤仍会按公司挡老账号）。作废词条 `cowork.workspace_unresolved` 已删。
- 没动：本方税号仍读归属行的 `tax_id`（自动建的为空 → 进 / 销项靠录入时的明确选择，MR.ERP 的“别家票”闸不生效，
  与没有公司资料时一致）；公司 / 客户主档的后台接口（无界面）；旧站点若有多份公司资料，新记录和列表只用最早那份——**上线前按站点核对份数**。
- 验证：新增 `tests/test_cowork_no_company_scope.py` 7 项；Cowork 离线全量 1455 项，失败集合与 main 基线相同（6 个既有）；
  CI 同名闸门全过（3.9 不支持的 4 项用 3.14）；本机 `cowork-a` 迁移后，在回滚事务里模拟“员工账号 + 一份公司都没有 + 无授权行”：
  银行 / 收入 / 销项税列表、连接列表、盘点列表都返回，自动建出一份归属，回滚后恢复原样。

**LINE（问题 1）**：旧线上 `ec2e5c95` 的富菜单（旧项目发布的全体默认菜单）三格：发单据进 ERP = postback
`action=cowork_erp_start` → 会话式（选目标 ERP → 进/销项 → 过账方式 → 发图片/PDF → 识别 → 复核卡 → LIFF 复核推送）；
盘点 = LIFF `flow=cowork-stocktake`；工作协调 = Wekan。新平台 `line.handlers` 原来只做绑定、postback 直接忽略，所以点了没回话；
盘点 LIFF 还指向已退役的旧站。用户选定：**照 ERP 新做法**（快捷回复逐步选，识别结果进网页录入列表，复核提交在网页）；
**富菜单由新平台发布，先只发 Cowork-TEST**（外部写入，已授权）。

| 入口 | 用途 | 实现 | 角色 / 配置 | 点击去向 | 验证 |
| --- | --- | --- | --- | --- | --- |
| 富菜单左上「发单据进 ERP」（旧菜单同一 postback） | LINE 里发单据 | `line/flow.py`：目标（MR.ERP 有连接 / Express 助手在线 / 识别后导出，不可选的在问句里列原因）→ 公司 → 年度（服务端已有的账套目录；没有就申请刷新并请稍后再点）→ 进/销项 → MR.ERP 付款（进项只赊账）/ Express 过账类型；按钮只带序号、只认当前一步、30 分钟会话 | 绑定 + LINE 开着 + `intake` 功能与录入角色 | 问完提示发 PDF / 图片 | 离线 15 项；本机样板真实连接与目录走完 MR.ERP 一轮 |
| 发来的图片 / PDF | 进网页录入同一条链 | `line/intake.py`：webhook 只排队；后台任务取原件 → `files.service.create` → `ocr…submit`（目标记在票上），幂等键 `line:<消息号>`；识别完经新钩子 `cowork_ocr_recognized` 推卡（每张票对方 · 金额，按钮进复核页 / 多张进列表），最终失败经 `pearnly_ocr_failure_handlers` 回一句；换绑不下载不扣费 | 同上 | 卡片按钮 → `/desk/pearnly-intake/new/<票>` 或 `/desk/pearnly-intake` | 本机：原件落本站归属、受理参数正确（识别受理被截下，未产生识别调用）、卡片成形；测试后删掉了假绑定与原件文件 |
| 富菜单中上「库存盘点」 | 在 LINE 内置浏览器打开网页端盘点页 | `line/menu.py` uri → `/desk/pearnly-stocktake` | 盘点功能 + 盘点角色 | 网页端盘点页（**Cowork 未接 LINE 登录**，内置浏览器里要自己登录一次） | 待真机 |
| 其余四格 | 锁 | 工作协调要重新设计；旧菜单的「ประสานงาน」回“还没开放” | — | — | 离线 |

- 菜单照 ERP：按权限 × 手机语言给绑定用户挂（`publish()` 幂等、内容散列命名，不删旧菜单、不改默认菜单）；每个事件核一次、绑定 / 解绑后重挂。
  格子图 `scripts/render-cowork-line-menu-tiles.py`（旧线上 Cowork 菜单的卡片与图标，文字按四语重画）。
- 架构策略：`line` 新增依赖 `companion / connections / files / ocr / stocktake`；`companion.credentials.online` 给录入入口判断助手在线（不要求管理权限）。
- 未做：Cowork 的 LINE 登录（`LINE_LOGIN_PRODUCTS` 只有 erp，属共用身份改动，随 Cowork 独立账号池一起做）；生产官方号的菜单发布（另行授权）。

**识别配置（2026-09-27 真机：LINE 发票回“请先配置 Cowork 发票识别参数”）**：Cowork 发票识别只认显式的
`pearnly_cowork_ocr_environment` / `pearnly_cowork_ocr_policy`，开户（`site-step.py set_ocr`）只写了共用 OCR 连接，
**所有 Cowork 租户（含生产）都缺**。改为按产品组写进 Cowork 栈的共用配置（`site-step.py` 的 `COWORK_OCR`，每次站点步骤都会带上；
测试环境 `finish-start.py` 同一份）：Vertex + 旧线上兜底档 `economy`。测试机已写入，`settings.snapshot()` 通过（economy / B 档 / vertex / 凭据在）。
**生产未改**（随下次上线、另行授权）。另：testcowork 的账本副本余额不足（`insufficient_balance`），识别会被拒，待用户决定。

**真机第二轮（2026-09-27）**：LINE 识别后导出的票从卡片打开是「提交」而不是「导出」——复核页只从新建弹窗的选择判断
“识别后导出”，从列表 / 卡片打开已有票时没有这份选择（网页同样有此问题）。改为识别时把目标卡片记在票上：
`Cowork OCR Batch` / `Cowork OCR History` 新增 `intake_target`（`mrerp` / `express` / `none`，不可改），`ocr.api.submit` 收 `target`
（只在带了时进指纹），落库时抄到每张票；复核页先读票再定「导出 / 提交」和是否摆过账类型。
用户要求上传提示与卡片参考 ERP：提示后加 ERP 同一句拍摄提示；卡片改为每张票一张（`line/cards.py`，版式照 ERP `line_cards.py`：
方向色页头、选择项、单据号 / 日期 / 票种 / 对方四项、全部明细、金额与付款、一个复核按钮；一次回复 5 张，其余推送）。
  再对齐 ERP main 上的卡片改动（`f01188a` / `840fb6e`）：「方向」改叫「业务类型」；日期标签只写「日期」、仍按佛历年份显示；
  票种名称用共用码表 `pearnly_core.tax_documents.codes.LABELS`（ADR-0012），不再自带一份。

**编辑页照 ERP 排版（2026-09-27，用户选「照 ERP 分组，补几项」）**：复核页改名「编辑」（页面标题与 LINE 卡片按钮）。
外框本来就照 ERP 采购页（左字段、右原件 3:2、手机原件在上 + 分区导航）；这次分组与顺序也照 ERP：
单据信息（单据号 / 票面日期 / 票种 ｜ 进项销项 / 付款方式 / 货品费用 / 过账类型）→ 对方（名称 / 税号 / 地址 ｜ 分店）→
另一方（名称 ｜ 税号，推送核对本方税号要用，ERP 页上没有）→ 明细 → 税费（折扣 / 币种 ｜ 未税 / 税额 / 预扣 / 合计）→ 备注；
税费与备注按 ERP `purchase-layout.js` 的办法定义在同一张表单里、渲染后挪到明细下面；分区导航改用 ERP 的「附件 / 详细信息 / 物料」。
新补字段（识别已读到、ERP 页也有）：对方地址、分店、票种（共用码表 ADR-0012）、折扣、币种、备注；ERP 记账专用的入账日期、到期日、
汇率、含税价、尾差、应付金额不加。验证：本机样板桌面与 375 宽手机截图核对；改备注保存后版本 +1、回读一致（之后已恢复原值）。

**识别后导出（2026-09-27 真机）**：①进项导出“模板没内容”——数据在第二页「ซื้อ」，工作簿打开停在空的第一页「ขาย」；改为打开时落在第一张有数据的页。
②简式税票（Sister Makeup，逐行含税价，相加＝合计 2768.00）导出被拦“回导会改变金额”：销项页协议是行金额未税、回导再加税，
含税价原样写会回导成 2949.08。改为：逐行相加＝合计且未税 + 税额＝合计时，按未税 / 合计比例把各行换成未税份额（末行吸收分位差，
单价＝行额 ÷ 数量，凑不回就只留行额），回导恰为 2586.92 + 181.08 = 2768.00；只改导出表、不改识别记录。拦截提示改写成看得懂的四语文案。
测试：`test_cowork_roundtrip_export.py` 新增 3 项（真实 6 行明细回导、未税票不动、打开页），源对照 35 项通过。

## 第六批：菜单收口 + 团队管理 + LINE 绑定（2026-09-26，第三个窗口）

用户：“按 ERP 的团队管理和 LINE 先把配置搭建好，前端画出来，团队管理的功能权限写 Cowork”；
菜单只要 录入工作台 / 对账中心 / 库存盘点 / 工作协调 + 个人信息 / LINE，其余旧菜单都不要；LINE 这次只做配置 + 绑定；
**工作协调不搬旧的（WeKan），用户要重新设计**。开工：本分支从 `a38d043` 快进到 `origin/main` `ee986f5`（无文件交集，未提交改动保留）。

| 入口 | 用途 | 实现 | 角色 / 配置 | 点击去向 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 侧栏：首页、录入工作台 | 已有 | — | 录入 = `Cowork Intake` | `/desk/pearnly-intake` | 可用 |
| 侧栏：对账中心、库存盘点、工作协调 | 待做页面 | 只在目录与团队管理里；**不在 `available`，侧栏不出现** | `Cowork Bank Reconciliation` / `Cowork Stocktake` / `Cowork Work` | — | 页面未做 |
| 左下角：团队管理 | 添加用户、改功能权限、重置密码、停用启用 | `team/`（service/api/setup）+ `public/desk/team.js`（页面文件在 core `pearnly-team`） | 只 `Cowork Admin`；服务端 `team.service.require_admin` | `/desk/pearnly-team` | 名单 / 弹窗已验；**添加用户受身份服务限制，见下** |
| 个人信息里的 LINE 块 | 本人绑 / 解绑本站点官方号 | `line/`（binding/api/handlers/boot）+ `line-binding.js`、`user-line.js` | `Cowork LINE` 或 `Cowork Admin`；站点需 `pearnly_line_*` 频道配置 | 个人信息页 | 块已出现；本地无频道配置，发码处提示未配置 |

- 勾选目录 `access/menu.py`（纯数据）：业务四项直接用各域已有操作角色，不另造菜单角色；新建角色 `Cowork Work` / `Cowork Profile` / `Cowork LINE`
  由 `after_install` / `after_migrate`（`team.setup.sync_roles`）确保存在，不带单据权限。分组：「常用功能」「用户菜单」（同 ERP）。
- 与 ERP 的差别：没有多公司分配；新用户自动授权本站点启用中的 `Cowork Workspace`（官方 User Permission，各域范围判据要它）。
- LINE 通道侧：`follow` 回“把 Cowork 页面上的绑定码发过来”，收码绑定、解绑词解绑、`unfollow` 解绑；已绑定的人发别的回“通道还没开放”。
  团队管理取消 LINE = 立即解绑。前端块与 ERP 同款（复制后改接口与词条命名空间 `cowork.line.*`，未改 ERP / 共用层）。
- 菜单去掉：识别记录、推送日志、客户、公司资料、集成、使用教程（`tests/test_native_desk_contract.py` 菜单清单随之改为 6 项并注明依据）。
- 架构策略登记新域 `team → access`、`line → access`。

**阻塞：统一身份站点上“添加用户 / 重置密码”走控制面私有建号接口，现只认 ERP**
（`pearnly_core.account.member_client` 要求 `pearnly_product == "erp"`；`pearnly_control.account.member_service.PRODUCT = "erp"`，
身份台账键也含产品）。本地样板 `cowork-a` 接了 Keycloak，所以实测添加用户被拒 `Not permitted`；名单、目录、权限拒绝路径正常。
放开给 Cowork 属于身份 / 授权保护范围的共享改动，待用户决定。

**用户决定（2026-09-26）**：采用“按产品分账号池”——Cowork 用新 realm `pearnly-cowork`，ERP / DMS 留在 `pearnly-business`，
同一租户同时买 Cowork 和 ERP 时是两个独立账号（已确认可接受）。**顺序：ERP 窗口的「LINE 绑定即登录」先合入 main，分池暂停**
（两件事改同一批身份共用文件，已与 ERP 窗口协调，对方合入后发 SHA）。

- 分池工作树（交接保留）：`/Users/skin/.codex/worktrees/cowork-identity-realm/pearnly` · `codex/cowork-identity-realm`，基线 `19ade38`，
  已改未提交 3 个文件：`pearnly_control/account/member_service.py`（产品 erp/cowork，只从令牌头取；ERP 台账键与归属印记不变）、
  `account/member_provider.py`（Cowork 读 `pearnly_member_identity_cowork_*`）、`pearnly_core/account/member_client.py`（放开 cowork）。
  解除条件：ERP LINE 登录合入 main → 变基 → 按 `client_for_target(target, product)` 做完其余改动（开户按产品选池并各建号、删除按池、
  线上 bootstrap-auth / 成员身份配置 / site-step / auth-tls / connect-control）→ 本地验证 → 合入。
- 其余改动点清单见本窗口与 ERP 窗口的协调消息（开户 / 删除 / 线上脚本共约 14 处）。
- 更新：ERP 窗口改为扩展同一个建号服务（加 LINE 关联动作），会改这 3 个文件；已把本窗口的按产品改法导出为
  `/Users/skin/.codex/worktrees/cowork-identity-realm/member-by-product.patch` 交给它一并合入。对方合入 main 后，本工作树的未提交改动以 main 为准丢弃。

验证（本机，`ee986f5` + 本工作树未提交改动）：架构 PASS；`i18n-build --check` PASS；前端文案 PASS；
`tests/test_cowork_team_menu.py` 4 项、`tests/test_native_desk_contract.py` 22 项、`test_architecture_guard` 通过；
`test_product_feature_catalog` 在 bench Python 下通过（本机 3.9 语法不支持，非本改动）。
样板 `cowork-a`（迁移后）服务端实测：三个新角色已建、LINE 处理器已注册；管理员能看到 6 项勾选、侧栏只有首页 / 录入、团队管理进左下角；
管理员本人一行锁定。浏览器：团队管理名单与「添加用户」弹窗、个人信息 LINE 块已看。未验：添加用户成功路径、员工视角、真实 LINE 绑定。

## 第十三批：Cowork 真实测试环境（2026-09-26，测试机，镜像 main `02a3b22` `sha256:3a1d5f26…`）

用户：“开始搭测试环境”。线上已有 Cowork 测试租户 `testcowork.pearnly.com`（ERP 窗口开通并升到 `15dea26`），
所以照 DMS / ERP 的做法：整站导入 + 切域名（不再用本地密码的空白站点）。

- 脚本：`testenv.sh` / `testenv-cloudflare.sh` / `run.sh` 加上 `cowork → testcowork.pearnly.com`（隧道入口 → 8181）；
  `tunnel-create` 已把新入口写进线上隧道配置（原两条不变）。文档 `docs/development/test-environment.md` Cowork 一节改写。
- 执行：`lab-start cowork` → 拉镜像 → 起 Cowork 栈 → 线上导出 `testcowork` 与账本 → 导入 `testcowork`（`IMPORT_OK`，287 张表）；
  账本导入被拒（Cowork 栈账本卷里是第七批实验室自建的 `billing.internal`，无导入标记）→ 删掉该实验室账本站与其库
  `_f7b1e2dfec5cbff0` → 重新导入账本、迁移、收尾（两个 Cowork 站点 migrate / clear-cache ok，识别凭据可读）→ 入口冒烟
  （两站 ping 200、账本匿名 403 属正常）→ `switch-lab cowork`。本机导出副本已删。
- 回读：`testcowork.pearnly.com` CNAME → 隧道；经域名取 `recon.css`（只有新镜像有）200，确认流量到测试机（解析生效约几分钟）；
  登录跳线上 `auth.pearnly.com/realms/pearnly-business`（Cowork 独立账号池线上未建，仍是业务池）。
- **未做 / 待用户**：用测试账号登录后逐项业务验收（录入、三个对账、盘点、团队管理、LINE 绑定）；真实识别是付费调用，需授权。
- 现状：Cowork 栈在测试机上**运行中**，`testcowork` **已切到测试机**；测完用 `lab-stop cowork` 切回线上并停栈（卷保留）。

## 第十二批：三种对账复核表统一外观（2026-09-26）

用户：“三个对账的表格继续优化统一，不动逻辑，视觉对齐整齐，颜色统一，格式能统一的统一，KPI 卡片不要，说明文案删掉，统一框架，统一视觉效果”；
导出的 Excel 也一起改（用户选“页面和导出一起改”），所以改在工作簿本身，页面照画自动同步。

- 去掉的内容（只删展示，计算不动）：收入差异明细顶部 KPI 三行、销项税对账结果的标题 / 期间说明 / KPI 卡、
  银行汇总表的横幅与“恒等式”提示行和使用说明段、收入与销项税的「使用说明」页。各明细页第 1 行就是表头。
- 统一外观 `reconciliation/export/theme.py`（三本工作簿保存前都走一遍，只改外观；`年-月-日` 文字日期改写成 `日/月/年`）：
  Arial；表头深色底 `1F2937` 白字 10 号加粗；合计 / 期末 / 分区行浅灰 `F3F4F6` 加粗；状态三色同一口径
  （绿 `DCFCE7` 一致、黄 `FEF3C7` 有差异 / 待确认、红 `FEE2E2` 缺一边 / 未找到，各表原色按各自含义归类）；
  整行染色的页只染状态列；细灰边框、无斑马纹、无网格线、无标签页颜色；行高 24 / 22 / 18；列宽上限 48；金额 `#,##0.00;[Red]-#,##0.00`。
- 页面：`recon_center/sheets.py` 不再需要隐藏页；可改格子的对应关系不变（收入按冻结行找第一条数据，现为第 2 行）。
- 测试：收入导出测试按新版式改写（两页、表头在第 1 行、三色、统一金额格式、每行同一边框、列宽 48）；银行测试改为“无横幅 / 无使用说明”
  与统一金额格式，并加“状态色只在状态列、全表同一字体”；销项税测试改为三页、对账结果表头在第 1 行。Cowork 离线全量仍是 6 个既有错误。
- 样板浏览器验：银行 4 页、收入 2 页、销项税 3 页外观一致；改格子重算照常（收入第 2 行 = 第一条记录）。

## 第十一批：Cowork 独立账号池 `pearnly-cowork`（2026-09-26，代码 + 本机验；生产未动）

用户定方案 A：Cowork 账号与 ERP / DMS 分开（“账号分开可以”），ERP / DMS 仍在 `pearnly-business`。
基线：本分支已变基到 `origin/main` `ec79bbd`（含 ERP 窗口的全平台 LINE 登录 `c4bf731`、控制面按产品选池的成员服务）。

| 层 | 改动 | 验证 |
| --- | --- | --- |
| 开户目标 `Pearnly Target` | 新增「Cowork Account Pool」一组：`cowork_realm`、`cowork_admin_client_*`、`cowork_password_check_client_*`；原字段即业务池 | 迁移后生效（本机控制面挂的是长期主目录，未在本机控制面迁移） |
| 选管理客户端 `pearnly_core.keycloak_admin.client_for_target(target, product)` | 按产品取池（`POOL_PREFIX = {"cowork": "cowork_"}`）；Cowork 池没配就拒绝，不悄悄落进业务池 | 单测 |
| 开户 `issues` | 所选产品需要几个池就在几个池建号（同用户名、同初始密码）；任一池已有该邮箱就都不建；任一步失败删掉本次建的全部账号；身份分记 `identity_subject`（业务池）/ `cowork_identity_subject`（新字段） | 单测 3 条（两池各建、已存在都不建、第二池失败回滚第一池） |
| 开通 `provisioning` | OIDC 客户端建在产品所在池；`set_oidc` 的 issuer 与两个 realm 级凭据按产品；`bind_user`、计费登记开户管理员都用该产品池里的身份 | 单测 + 既有开通测试 |
| 拆除 `teardown` / 员工身份核对 / 开户欢迎信 | 按池删账号与客户端；两池身份都核对；“已开户”判据认两池 | 既有测试改为按池断言后通过 |
| 生产脚本 | `bootstrap-auth.py --realm pearnly-cowork`（同设置，控制凭据写 `control-identity-cowork.json`）；`connect-control.py` / `runner_bridge setup` 带上 Cowork 池凭据（没有就不配）；`configure-member-identity.py` 在 Cowork realm 存在时补 `pearnly_member_identity_cowork_*`（所有权密钥共用）；`bootstrap-billing.py` / `verify-foundation.py` 接受“三键全有或全无”；`auth-tls.conf` 放行 `/realms/pearnly-cowork/`；`site-step.py` 的 `set_oidc` 按组断言 issuer、LINE 入口按组选 realm；`configure-line-login.py --realm`；`verify-control.py` 加 Cowork 断言 | 语法 / CI 同名闸门；**未在生产执行** |
| 本机样板 | `scripts/sample-cowork-realm.py`（建池、两个客户端，密钥写 `~/.config/pearnly/cowork-realm.env` 0600）；`sample-keycloak-local.sh` / 主题 / 发信脚本加上 `pearnly-cowork`；`sample-configure-site.sh` 按组选 issuer；`sample-ensure-target.sh` 可带 Cowork 池两个密钥 | 本机实测：建池、本机 http / 忘记密码 / 空密码策略 / 管理角色、主题回读；`cowork-a` 站点改签到 `pearnly-cowork`（新客户端 `cowork-a`、测试管理员绑定），浏览器跳到 Cowork 池登录页、登录后进首页 `tenant-admin@sample.local` |

- LINE：Cowork 目前没有“LINE 绑定即登录”（`LINE_LOGIN_PRODUCTS` 仍只有 `erp`）；Cowork 池的 LINE 回调已由用户在 LINE 后台登记
  （`…/realms/pearnly-cowork/broker/line-2010411313/endpoint`，回读三条）。Cowork 接 LINE 登录时再把 `cowork` 加进去并对 Cowork 池跑 `configure-line-login.py --realm pearnly-cowork`。
- 本机样板的变更（属本任务）：Keycloak 新 realm `pearnly-cowork` 及其 `pearnly-admin` / `pearnly-password-check` / `cowork-a` 客户端、测试管理员
  `tenant-admin`（密码只在 `~/.config/pearnly/cowork-realm.env`）；`cowork-a` 站点 OIDC 配置改到 Cowork 池（原配置备份在 cowork-web 容器 `/tmp/cowork-a-oidc-backup.json`），
  站点多一条 `Pearnly Identity`（Cowork 池 issuer）。ERP / DMS 样板站与业务池未动。
- **生产上线顺序（每步需授权，均未做）**：① 合入 main → 构建镜像；② 生产主机 `bootstrap-auth.py --realm pearnly-cowork --smtp`（发信配置经标准输入）；
  ③ 替换 `auth-tls.conf` 并重载 nginx；④ `configure-member-identity.py` → 重跑计费站点初始化（带上 Cowork 三键）→ `verify-foundation.py`；
  ⑤ 本机 `connect-control.py`（带 Cowork 池凭据）→ `verify-control.py`；⑥ 已有 Cowork 站点（ERP 窗口 2026-09-26 把 `testcowork` 升到 `15dea26`，仍签到业务池）
  要迁到 Cowork 池：在 Cowork 池建该租户账号与站点客户端、改站点 OIDC、按新 issuer 绑定——这是生产数据变更，单独授权、单独记录。

## 第十批：复核表 1:1 照画、分支号口径、图片 / PDF 发票识别（2026-09-26）

用户：“前端显示表格按导出表格 1:1 画，可以不用官方 UI 组件，使用说明不用出现，不要过多文案备注”；
“分支号按你建议的”；“旧项目能用，这里就要能用”（图片 / PDF 发票）。

| 项 | 实现 | 状态 |
| --- | --- | --- |
| 结果页 = 导出的 Excel 逐格照画：文字按单元格数字格式排（千分位、`[Red]`、括号负数、SUM 合计当场算）、底色 / 字色 / 字体 / 字号 / 粗斜 / 对齐 / 换行 / 边框 / 合并 / 列宽 / 行高 / 冻结窗格 / 网格线，底部 Excel 式工作表标签；不再有四个汇总数和说明文字 | `recon_center/sheets.py`（从导出同一函数生成的工作簿读样式，按页输出）、`public/desk/recon.js`（`document.createElement` 画表）、`public/desk/recon.css` | 样板浏览器验：银行 4 页、收入 2 页、销项税 3 页均显示；不换行的字伸到右侧空格（冻结行里也对） |
| 「使用说明」页不出现（收入第 3 页、销项税第 4 页）；导出的 Excel 照旧带着 | `sheets.HIDDEN` | 同上 |
| 直接在格子里改：银行对账单明细（日期 / 摘要 / 提款 / 存款 / 余额）、总账明细（日期 / 凭证号 / 科目 / 摘要 / 借 / 贷 / 余额）、收入差异明细（单据号 / 日期 / 客户 / 税表金额 / 总账金额 / 科目）、销项税发票明细与报告明细的字段；回车或离开格子即重算重画；打错（日期、金额）退回上一次算得通的输入 | 每格带输入位置；银行两页导出时重新排过序，按内容认回输入行；服务端把 `1,080.00`、`(500)`、`日/月/年`（佛历自动减 543）换回任务里存的格式 | 样板验：银行把 IV001 存款改成 1,080 → 对账结果变成“仅对账单存款 + 仅 GL 借方”，出现「保存」；未点保存 |
| 分支号（有意改动，偏离旧线上）：发票一侧没读到分公司（空）就**不比**分公司；读到了照旧比（空 / 总部词仍归 00000） | `vat_check_core._diff_dims` 一行 + 文件头注明 | 离线测试 2 条 |
| 图片 / PDF 发票：每份文件一个持久识别任务 `cowork.vat_check`（Pearnly OCR Work），用录入同一个识别引擎 `run_invoice`、同一套冻结配置 / 限流 / 超时让出 / 检查点；按发票分组（一份 PDF 多张票逐张出）；识别出发票按页扣费（参考号＝Work 名，重试只补记账），整份不是发票不扣；对账已放弃时未识别的任务自行取消；余额不足直接报 | `recon_center/invoice_ocr.py`、`hooks.pearnly_ocr_handlers`、`recon_center/vat_check.py` | 样板冒烟（识别与扣费打桩）：建 Work → 跑 → 结果存检查点 → 扣费 1 次（`cowork`/`pdf`/1 页）→ 对账取回；等待超时路径报“识别超时”。**真实识别未验**（本机无识别配置；付费 OCR 需用户授权，在测试环境做） |

- 自画例外的原因与范围（ADR-007 要求记录）：用户明确要求复核表与导出的 Excel 一模一样；官方 DataTable / Grid 不能表达单元格级的底色、合并、冻结与
  Excel 数字格式。例外只限结果页那张表和底部工作表标签；列表、新建、遮罩、按钮、提示仍是官方控件。表格保持 Excel 的白底，不随深色主题变。
- 识别结果不进录入的识别记录（旧线上销项税核查也不进）；识别读出的买方分公司原样带上（引擎目前多为空，按上面的口径不比）。
- 发票记录的“期间”留空，由核查工作簿按日期推（与旧线上 `_derive_period` 相同）。
- 依赖：`recon_center` 新增依赖 `files`（上传文件检查与页数），已登记在 `architecture-policy.json`。
- 验证：架构 PASS；四语 PASS（删掉作废的 `vat_check.scanned_not_ready`）；CI 同名 Python 闸门（3.12）与 `test-native-desk.cjs` PASS；
  Cowork 离线全量（容器内）仍是 6 个既有错误，无新增。

## 第九批：对账中心（银行 / 收入；销项税报告核查进行中，2026-09-26）

用户：“三个对账中心拆出来放在侧栏单独放，每个首页统一列表（排序、复选框），右上角新建任务，新建进来上方两个上传口……
对账中白屏覆盖显示核对中……对账完成显示结果，所有前端界面优先采用官方原生界面”；进度只显示阶段文字（照旧线上）；
随后：“结果预览做成对账完后导出的那个复核表，可以在线修改，修改字段后自动在线计算，修改后可以保存。”

| 入口 | 实现 | 状态 |
| --- | --- | --- |
| 侧栏「银行对账」「收入对账」（`pearnly-recon-bank / -income`，Cowork 自己的 Page 文档） | `public/desk/recon.js`（列表 / 新建 / 结果三段路由） | 样板验 |
| 侧栏「销项税报告核查」`pearnly-recon-vat` | `recon_center/vat_check.py` + `reconciliation/vat_check_*`（新任务类型 `Cowork VAT Check`）；每个上传口可多份 | 样板验：报告 + Excel 发票 → 匹配 2 / 差异 1 / 缺失 2；改 IV002 金额重算 → 匹配 3，保存第 2 版、导出 200、列表同步。**图片 / PDF 发票未接**（见下） |
| 新建：左右两个上传口 →「开始对账」→ 官方整屏遮罩写阶段文字 | `recon_center/jobs.py`（文件先存本人私有 File，后台 `long` 队列跑，阶段进缓存 24 小时，只本人可查，读完删临时文件） | 样板验（样例一秒内完成，只见到“完成”一阶段） |
| 结果页 = 导出的复核表：四个汇总数 + 工作簿各页（官方分区）；银行两张明细 / 收入差异明细可改，改后自动重算；有改动才出现「保存」 | `recon_center/sheets.py`（页面各页由导出同一函数在内存生成）、`reconciliation/revision.py`（从已存结果还原两边输入、同一比对重算、整份替换 + 修订记录 `edit_json`，按修改时间防并发） | 样板验：收入改 IV002 总账金额 → 匹配率 75%、汇总 4,500，保存后第 2 版、列表同步、过期保存被拒；银行加一笔存款重算 → 已匹配 4 |

- 收入对账补齐“从上传文件建任务”（`recon_center/sources.py`，照旧线上分派：Excel / CSV 直读；总账 PDF 先识别管线 general_ledger、再文字行；
  税表 PDF 先表后文字行）。差异：新平台只有 pypdf（无 pdfplumber 表格抽取）；税表扫描件 / 图片的模型读取未迁（识别层无 vat_report 文档模型），明确报读不出。
- 银行 `create_from_documents` 加可选阶段回调（不传即原行为）；对账任务列表加白名单排序；对账角色的用户经 `recon_center.api.start` 直接上传，不再要求录入权限。
- Tab 在本镜像的 Frappe 里只能用于表单（需表单 doctype），所以工作簿各页用官方分区按顺序排列，使用说明默认折叠。
- 销项税报告核查（用户定迁）：旧线上 `vat_recon_core`、`vat_excel_build / sheet3 / i18n / styles`、`vat_report_merge` 的合并校验、
  `report_row_to_invoice` 逐字移植到 `reconciliation/vat_check_core.py` 与 `export/vat_check_*.py`（只改导入路径）；任务存两边输入 + 汇总，
  核查工作簿随时由输入重建，所以复核表可改可存、导出即当前版本。报告读取同收入对账税表（Excel / CSV / 文字层 PDF）。
  **未接：图片 / PDF 发票识别。** 用户定“复用录入的发票识别”，但识别引擎只能在持久识别任务（Pearnly OCR Work）里跑
  （防重复计费、限流），需要新增 `cowork.vat_check` 任务类型并把入库的识别步骤抽成共用函数；在此之前这类发票明确报“暂不支持”。
  另一个口径问题：识别引擎不抽买方分支号，旧线上逐项比对会比分支（空当 00000），接识别前需定处理方式。
- 发现的既有问题（未改）：银行对账的 `document_model_rows`（`pearnly_cowork/doctype/cowork_bank_reconciliation`）在识别任务之外调模型，
  真走到模型那一步会报 `ocr.durable_work_required`；现在只有文字层读不出的 PDF 账单会触发。
- 团队管理原是一个「对账中心」勾选（角色 `Cowork Bank Reconciliation`）；2026-09-27 拆成银行 / 收入 / 销项税三个角色，见 [工作协调任务记录](2026-09-27-cowork-work-board.md) 第六批。
- 验证：架构 / 四语 / 前端文案 / 空白 PASS；原生 Desk 合同 22 项；Cowork 离线全量 6 个既有错误无新增；样板浏览器与接口实测如上。
  未验：页面上真选文件（测试浏览器不能驱动系统文件框，改为同一接口直传）、慢文件的中间阶段文字、需复核（PDF 低置信）任务的在线修改保存。

## 第八批：库存盘点页（2026-09-26）

**更正（同日，用户：“只去掉盘点在库就好了，其它照搬，你可以不要自己画吗，画出来两边都不像，扫码器也没有了”）**：
页面改为 **ERP 两个页面文件的原样副本**（`public/desk/stocktake.js`、`stocktake-entry.js`，文件头注明来源），只改接口前缀、
词条前缀（`cowork.` + ERP 原词条，共 93 条照抄；两条提到库存账的文案改写）、路由 / DocType 名、去掉「盘点在库」与仓库下拉 /
仓库列，实际仓库改为填写。Cowork 后端出一层**同名同形**接口 `stocktake/desk_api.py`（列表、总数、删除、新建（官方上传器的私有
文件，读完即删）、模板、复核快照、结束、导出、查商品、记录、修改、作废、重盘、照片），底层仍是 `service` / `upload` / `counting`。
任务加「备注」、记录加「说明」两个字段。扫码器：ERP `scan.js` 通用部分原样复制到共用层 `pearnly_core/public/desk/scan.js`
（+ `scan.css` + `vendor/barcode-detector`），core 统一加载；ERP 仍加载自己那份（内容相同，后加载者生效），ERP 文件未改，
待与 ERP 窗口协调后由 ERP 删掉副本。下表是第一版（自己画的）的记录，页面部分已被替换。
样板复验：列表 / 新建弹窗（无盘点在库）/ 上传导入（2 行、1 行带实盘、1 个错行）/ 工作台 / 连续盘点（摄像头按钮在，测试浏览器禁用摄像头，
改输编码）/ 记录弹窗（说明已存）/ 重新盘点该物料（确认后作废留痕）均按 ERP 行为走通。


用户：“库存盘点采用 ERP 前端布局……所有前端界面优先采用官方原生界面”；随后更正：“开始盘点、逐笔记录、重盘、照片这些也要，
只去掉盘点在库就好了，因为 Cowork 这边没库存系统”；上传表“带实盘，跳过错行并列出”。

| 入口 | 实现 | 角色 / 配置 | 状态 |
| --- | --- | --- | --- |
| 侧栏「库存盘点」`/desk/pearnly-stocktake` | `public/desk/stocktake.js`（列表 / 详情两级，照 ERP）；core 页面文件改为到用时取 `frappe.pearnly.coworkStocktake` | `Cowork Stocktake` 或 `Cowork Admin`，功能 `stocktake` | 已加进 `available` |
| 新建：名称 + 上传盘点表 + 下载模板 | `stocktake/upload.py`、`upload_sheet.py`（纯）；`api.upload_count / template` | 同上 | 样板验：4 行导入、3 行带实盘、3 个错行列出原因 |
| 列表：排序、勾选删除 | `api.tasks / delete` | 同上 | 样板验 |
| 详情：搜索、筛选（全部 / 未盘 / 有差异 / 仓位不符）、结果（一致 / 盘盈 / 盘亏 / 未盘）、结束、导出 | `api.task`（= report）、`close`、`export` | 同上 | 样板验 |
| 开始盘点（连续）/ 记录 / 修改 / 作废 / 重盘 / 照片 | `public/desk/stocktake-entry.js`；`stocktake/counting.py`（lookup、recount、照片挂记录）；`add_entry / edit_entry` 收照片 | 同上 | 样板验：编码查商品、保存下一条、重盘作废留痕、照片私有可读、别处已挂的照片拒收且整笔回滚 |

- 与 ERP 的差别：无「盘点在库」、无仓库主档（实际仓库自由填写，默认账面仓位 / 上一条位置）、一商品一行不选账面行、无备注列、
  无差异金额（没有成本）。**摄像头扫码未接**：扫码组件在 ERP 应用（含 WASM），Cowork 不跨产品引用；要挪进共用层需与 ERP 窗口协调。
  目前是编码框（扫码枪 = 输入 + 回车）。导出表的“查看照片”列仍为空（导出沿用原实现）。
- 上传导入的实盘落成一笔逐笔记录（仓位 = 表里的仓位，空则 `-`），所以重盘、导出、结果对照都复用原实现；任务仍是 `count_mode = scan`。
- 不设确认步骤（用户口径）：结束盘点、重盘、作废直接执行；删除沿用录入工作台的确认。
- 验证：架构 / 四语 / 前端文案 / 空白 PASS；`test_cowork_stocktake_upload.py` 5 项；Cowork 离线全量 1443 项，6 个既有错误无新增；
  样板 `cowork-a` 浏览器与接口实测如上表。

## 第七批：Cowork 测试环境（2026-09-26，已起过一次，按用户要求暂停）

- 分支提交 `cfcc66d` 已推 `origin/codex/cowork-frontend`（用户授权“只推任务分支”）；Development checks 通过；
  `build-production.yml` 出镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:1a25a8e8f9a9884a5b8877d679b9c3af5d329ff9ff1772de9ae5a1fd2a892b67`（revision = `cfcc66d`）。
  本地闸门：CI 同名 Python 检查（3.12）全过、Node 检查除需 `npm ci` 的 DMS 编辑器快照外全过（本机 npm 快捷方式损坏，交 CI），
  Cowork 离线全量 1438 项，6 个既有错误（缺 docx 2、原件清单桩 4），无新增。
- 测试机 IP 由 `192.168.0.94` 变为 `192.168.0.65`（主机指纹核对为同一台），本机 `~/.ssh/config` 的 `lenovo-office-lan` 已改
  `HostName 192.168.0.65` + `HostKeyAlias 192.168.0.94`（改前备份在会话临时目录）。建议路由器固定该机 IP。
- Cowork 线上无租户可导入，新增 `scripts/testenv.sh lab-init cowork`（`infrastructure/testenv/cowork-lab.py`）：本栈账本
  `billing.internal`、测试租户 `T-TESTCOWORK`（1000 THB）、全部 Cowork 功能、管理员 `labadmin@testcowork.local`（密码在
  `~/.config/pearnly-testenv/testenv-secrets`）、公司资料、MR.ERP 推送开关。与线上不同：本地密码登录、自建账本、无 OCR / LINE。
- 实测（测试机，上述镜像）：`up cowork` + `lab-init` 成功；站点侧查余额 1000 THB、功能全开、侧栏首页 + 录入工作台；
  Mac 端口转发 ping 200。**浏览器录入流程未验**。
- 发现：WSL 在没有会话时会自动关停，容器随之重启（本次 VM 在初始化后重启过一次）；测试期间需从 Mac 挂一条
  `ssh lenovo-office-lan "wsl -d Ubuntu-24.04 -u root -- sleep infinity"` 保活（未改 Windows 配置）。
- 用户：“测试先不搞，等那边搞完你再做独立号池，然后再搞真实测试环境。” 已 `down cowork`（容器删、4 个卷
  `pearnly-testenv-cowork_{database,queue,sites-billing,sites-cowork}` 保留），端口转发与保活会话已关，域名未动过。
  解除条件：分号池完成后按真实登录重建或复用这 4 个卷；不再需要时 `reset cowork`。

## 工作包：推送依赖的后端（2026-09-26 起，进行中）

用户 2026-09-26 定的范围：**只要能顺利推送到目标 ERP**——按票面、按用户的选择（弹窗）、按用户的修改（复核）、按识别结果。
**不要**：公司主档（Cowork 不做）、推送日志页（只显示状态）、其它旧项目附带功能；先不改前端逻辑（推送顺序后面要重排）。

盘点结论（两路只读核对，旧 `ec2e5c95` ↔ 本工作树）：录入前半段（上传/识别/复核/删除/目录）已有；**MR.ERP 发票推送整条没有**
（`push.application.service.prepare` 只接 Express）；Express 链路后端在，但结果不明会锁死连接、前端只调了准备没调确认（前端待重排时一起改）。

### MR.ERP 推送：来源 → 落点（旧线上 `ec2e5c95`，只读 `git show`）

| 旧来源 | 新落点 | 说明 |
| --- | --- | --- |
| `services/erp/mrerp_http/modules.py` | `push/mrerp/modules.py` | 导入模块表（只保留推送用到的销项赊账/现金、采购货品/费用、三类主档） |
| `mrerp_adapter_models.py`、`exceptions.py` | `push/mrerp/models.py` | 结果类型、回执单号 |
| `mrerp_http/routing.py` | `push/mrerp/routing.py` | 按方向 + 付款 + 货/费分流、单据防呆、散客现金、匹配闸；复用已迁 `contracts.push` / `push.express` 判定 |
| `mrerp_http/account_valve.py`、`preview_parse.py`、`report_parse.py`、`mrerp_report_parser.py` | `push/mrerp/` 同名纯函数 | 科目阀、预览表单、主档/发票回执解析 |
| `mrerp_xlsx_*`（generator/sales_credit/sales_cash/purchase/customer/supplier/product/lookups/fmt/schemas/sharedstrings）+ 7 个模板 | `push/mrerp/xlsx/` | 导入表生成 |
| `mrerp_http/autocreate.py` | `push/mrerp/autocreate.py` | 缺客户/供应商/商品自建（确定性码、幂等） |
| `mrerp_http/session.py`、`adapter.py` | `connections/mrerp_session.py`（会话，与只读采集共用）+ `push/mrerp/transport.py`（导入 5 步 + 逐行回读） | 联网；会话锁用 `pearnly_core.session_lock` |
| `erp_push.py::push_mrerp_history`、`erp_push_adapters.py` | `push/application` MR.ERP 分支 + 服务端执行 | 接进原生账本（准备 → 确认 → 领取 → 回执） |

不搬：四语友好文案目录（`mrerp_business_friendly*`，展示用）、库存进出单、Playwright 旧适配器、DMS 的 `mrerp_dms_*`、推送日志/统计/批量重推。

### 第一批进展（2026-09-26，MR.ERP 推送后端已接通，未真推）

实现（`apps/pearnly_cowork/pearnly_cowork/`）：
- `push/mrerp/`：`modules`、`models`、`routing`、`account_valve`、`preview_parse`、`report_parse`、`report_parser`、`autocreate`、
  `xlsx/`（11 个生成模块 + 6 个模板）——纯逻辑，按旧文件逐字移植，只改导入路径 / 模板位置；`transport.py`（导入 5 步 + 逐行回读，
  `import_sent` 标记导入确认是否已发出）；`runner.py`（确认后排 `short` 队列任务：领取 → 导入 → 回执，同连接串行，一条结束接下一条）。
- `connections/mrerp_session.py`：MR.ERP 会话从只读采集器抽出来共用（登录 / 进账套抓 idus / 业务请求；公网校验、不许跳出原站点）。
- 推送应用层：`prepare` 增加 MR.ERP 分支（冻结目标账套 + 整理后的票面 + 已配置映射 + 本方税号）；新 RPC
  `push.application.api.confirm`（MR.ERP 确认发送并排执行任务；Express 仍走 `companion.submission.confirm`）。
- 账本：目标类型放开到 express / mrerp；MR.ERP 确认不要求小助手凭据；回执带 `mrerp_bill_no`；新增 `mark_unknown`
  （导入确认发出后异常：转人工 + 结果不明，保留写者锁，同 `recover_expired`）。`push_inputs.mappings` 增 `erp_type` 参数（默认 express）。

有意差异（需求依据：用户 2026-09-26「按用户的选择 / 票面推」）：
1. **销项现金按用户选择推**：旧 `route_and_upload` 只把采购切出默认赊销，用户选的「现金」算出 `sales_cash` 却被丢掉（旧缺陷）；现在采用 `choose_doc_type` 的结果。
2. 失败细分：导入确认没发出前的失败（登录 / 上传被拒 / 预览无可导入行）确定没写入 → 转人工并释放锁；发出之后的异常才算结果不明。旧版技术失败一律按不明。
3. 「发票号已存在」记为成功（旧 `skipped_dup`：单号由票据 id 确定性派生，同票同号）。不自动重试（旧 MR.ERP 同样不重试）。
4. 客户编号按字符串比较（新平台 Cowork Client 主键是名称，旧版整数 id）。
5. 不搬：四语失败文案、库存进出单、银行流水佐证（付款仍按人工选择 → 票面判定）。

验证（本机，基线 `a38d043` + 本工作树未提交改动）：
- 导入表来源对照（`tests/test_cowork_mrerp_xlsx_source_parity.py`，旧 `ec2e5c95` 经 `git show` 导出、经 `PEARNLY_MRERP_LEGACY_ROOT` 传入）：
  7 种表型逐成员一致，票号派生与推送前校验一致。
- 离线（`tests/test_cowork_mrerp_push.py`，假 MR.ERP 站点）：分流（用户付款 / 货费选择）、客户 / 供应商 / 商品先自建再导入、
  赊销 118 / 现金 125 / 采购 67 / 费用 453、逐行回执、非数字回复整批失败、上传被拒在发送前、别家票不进导入；
  结果落定：成功带单号、已存在 = 成功、登录错 / 上传被拒 = 失败、确认后断线 = 不明。
- 本地样板全链路（准备 → 确认 → 执行 → 回执，联网一步换假结果，全程回滚不留数据）：成功 → 成功 + 单号 + 识别记录「成功」+ 锁释放；
  拒收 → 转人工 + 原因 + 锁释放；确认后断线 → 转人工 + 不明 + **锁保留**。执行器拿到账套 15:1、test01、解密后的密码、本方税号、进项。
- Cowork 离线全量 1420 项，失败集合与 `origin/main` 同结构基线相同；架构 / 四语 / 前端文案 PASS。

未做 / 待定：真推 test01（外部写入，需授权）；结果不明后锁不释放（Express 同样存在），需要处理入口；前端提交流程未改（等推送顺序重排）；
Express 小助手旧地址入口（`companion.wsgi`）未挂。
- 已补：只读目录采集与推送共用同一 MR.ERP 账号锁（`mrerp_session.lock_key` + `pearnly_core.session_lock.account_lock`；采集等锁 120 秒，
  拿不到按 `ERR_TECHNICAL` 走原有重试）——否则推送中打开弹窗触发刷新会把导入会话踢掉。真实环境刷新（test01 只读）2 秒成功。

### 第二批：test01 真推验证（2026-09-26，用户授权“test01 可以真推验证”）

用户同时定：**不放开结果不明后的重新推送**（实际使用是完成一单再下一单）。

- 数据：新注入第 3、4 批演示票（不动用户在点的第 2 批）；账套 test01 / TEST2019（6:1，旧版真机验证用的同一账套）；一单推完再推下一单。
- 真推结果（按 MR.ERP 导入回执逐行判定 + 列表独立只读回读 `allview` / `showdata.php` 按单号搜）：

| 单据 | 票 | 人工选择 | 结果 / MR.ERP 单号 | 回读模块 | 金额 | 客户 / 供应商 |
| --- | --- | --- | --- | --- | --- | --- |
| 销项赊账 | SV6909-0307 | 赊账 | 成功 SI690923-841904 | artran/118 | 28,890.00 | **เงินสด** |
| 销项现金 | SV6909-0407 | 现金 | 成功 SI690924-093665 | arse/125 | 28,890.00 | **เงินสด** |
| 采购货品 | IV6909-0312 | 货品 | 成功 690915-963946 | aptran/67 | 13,375.00 | บริษัท สยามซัพพลาย จำกัด |
| 采购费用 | TC-2569/0615 | 费用 | 成功 690908-536693 | aptran/453 | 5,350.00 | บริษัท ไทยคลีนนิ่ง เซอร์วิส จำกัด |

  交叉检查：现金单不在赊销列表、赊销单不在现金列表、货品单不在费用菜单。识别记录状态均回写「成功」，每单约 3 秒。
- test01 上的外部写入（测试账套，已授权）：上表 4 张单据，以及推送时自动建的供应商（两家，按税号）、商品（按名称派生码）、通用费用物料、现金客户（幂等确保）。未删除。
- 问题：**销项客户都落在现金客户 เงินสด，不是票面买方**。旧逻辑销项客户只取识别记录关联的客户（`client_id`），没关联就当散客；
  新平台录入流程不关联客户（改挂/解绑已取消），所以所有销项都会挂现金客户。待用户确认改为“无关联客户时按票面买方（税号）建 / 取客户”。
- 复核页没有「货品 / 费用」选项，本次按复核保存接口写入 `posting_item_type_manual` 模拟人工选择；界面上目前只有自动判断。

### 第三批：销项客户按票面买方 + 复核页货品 / 费用（2026-09-26，用户：“按票面买方改，复核页加上货品费用选项”）

- 销项客户：`xlsx/lookups.resolve_customer_code` 关联客户映射 > **票面买方税号**（`invoice_buyer_code`，13 位）> 现金客户 เงินสด；
  `autocreate.provision_customers` 无关联客户但有买方税号时按买方自建（码 = 税号，与生成器同源取码，不注入）；
  买方 / 卖方取值同时认新平台键名 `buyer_tax` / `buyer_addr` / `seller_addr`（旧 OCR 是 `*_tax_id` / `*_address`）。有意差异见代码注释。
- 复核页：`posting_item_type_manual` 下拉（空＝自动判断 / 货品 / 费用），只在进项显示、换方向跟着显隐，保存走原复核接口（空值删键）；
  新词条 `intake.field.itemType`、`intake.itemType.goods/expense`（Core 术语表，四语）。
- 验证：新增客户解析 / 买方自建测试 2 项；Cowork 离线全量 1424 项，失败集合同 `origin/main` 基线，导入表来源对照仍一致；
  **test01 真推 SV6909-0507（第 5 批，无关联客户）→ 成功 SI690925-749804**，只读回读：赊销单客户 = บริษัท กรีนฟาร์ม จำกัด，
  客户档 0105562055501（ลูกหนี้การค้า）已建。页面：进项票显示「货品 / 费用」、销项票隐藏；经复核保存接口写「费用」后重开回显「费用」，随后恢复留空
  （IV6909-0212 版本号 +2，内容不变）。界面点选未做（内置浏览器面板隐藏时点击不落页面）。
- test01 新增外部写入：上述 1 张赊销单 + 客户 บริษัท กรีนฟาร์ม จำกัด（0105562055501）。

### 第四批：提交即推送、目标按第一次选择、失败原因上状态图标（2026-09-26）

用户定：复核页右上角「提交」= 把这一批全部推送；「保存」只在字段有改动时出现；提交后回列表看状态；
推送目标按第一次的选择；失败只把原因放在状态图标里（悬浮看）；状态只有 录入中 / 成功 / 失败；
第三张卡片改「识别后导出」（我们的复核工作簿模板），MR.ERP / Express 两张卡片能导入这份模板解析后推送。

实现：
- 目标跟票走：`ocr.api.submit` 增 `connection` / `account_set_key`（只在带了时进请求指纹，老请求重放不受影响）→ `Cowork OCR Batch.target_*`
  → 识别落库时抄到 `Cowork OCR History.push_connection` / `push_account_set`（不可改，`storage.IMMUTABLE`）。
- 失败原因：`Cowork OCR History.last_push_reason`；`push_state.mirror` 转人工时写账本原因、其它状态清空；准备阶段被拦（reserve manual）也回写；
  还没进账本就失败的走新 RPC `push.application.api.submit`（准备 + MR.ERP 确认一次做完，失败整笔回滚后 `record_failure` 只记原因、不改状态、正常返回）；
  Express 的 `companion.submission.confirm` 同样失败不抛、记原因。
- 前端：提交逐张（一张推完再下一张）按票上目标推，弹窗当场这一批用弹窗目录证据、从列表进来的现刷一次目录；幂等键 = 内容 + 上一次推送记录
  （失败后再提交会重推，连点仍是一次）；已成功 / 录入中的跳过；推完回列表，不弹业务失败窗。「保存」按改动显隐（等回填完成再比）。
  列表状态：manual 显示「失败」、只有原因没有状态也显示「失败」，悬浮 = 原因（共用 `list.js` 增可选 `title(value,row)`）；有录入中的每 4 秒自动重拉。
  上传框：MR.ERP / Express 收 `.xlsx`（复核工作簿，服务端认出往返表走确定性解析）。卡片三改名「识别后导出」；删 `intake.push.manual`。
- 兜底巡检 `push.mrerp.runner.sweep`（hooks cron 每 5 分钟）：丢失的待发送重新排队；过期租约转结果不明（不重推）。回执原因不再重复前缀。

验证：
- 浏览器：打开第 6 批 3 张票的复核页，「保存」初始不出现、改字段出现、改回消失；点「提交」→ 回列表 → test01 真推 3 张全部成功
  （第一次提交因本机 Docker 虚拟机 DNS 故障 3 张失败、原因记在票上；修好 DNS 后再提交 → 3 张成功、原因清空）。
- 本地样板（全程回滚）：连接版本不符 → `submit` 返回 failed、原因记票、状态不动；执行失败 → 状态 manual + 原因；巡检无事可做时不报错。
- Cowork 离线全量 1424 项，失败集合仍是既有 7 项；架构 / 四语 / 前端文案 PASS。
- 未验：从 MR.ERP / Express 卡片导入复核工作簿——本地 Cowork 站点**没配计费**（导回按字符计费，要先查额度，本地 `lookup_error`），需在测试环境验；
  Express 整链（本机无小助手）。

本地环境（本批，属本任务）：cowork-a 加了最小识别配置 `pearnly_cowork_ocr_environment={"OCR_LLM_BACKEND":"vertex"}`、`pearnly_cowork_ocr_policy={"mode":"economy"}`
（只让确定性导回可用；无模型凭据，付费识别路径在凭据检查处被拦）；第 6 批演示票带目标 MR.ERP / 6:1（`sample-cowork-intake-demo.py 6 mrerp 6:1`）。
电脑重启后：mariadb / redis 需手动 `docker start`；控制面容器需重启重连数据库；colima 虚拟机 DNS（192.168.5.4）失效，临时把虚拟机
`/etc/resolv.conf` 指向 8.8.8.8 / 1.1.1.1（备份在 `/etc/resolv.conf.colima-bak`，colima 重启会恢复原状）。test01 新增：第 6 批 3 张单据（及可能自建的主档）。

### 第五批：小助手精简 + 账套以网页为准（2026-09-26）

用户（附 v1.1.77 截图）：小助手去掉多余文案、只保留必要标题；不显示账套 / 年度、只显示连接人的基础账号信息；去掉账套和年度选择，
目标账套 / 年度以网页第一步选择为准；托盘右下角那个框太大撑破了。

- **小助手**（来源仓库 `pearnly-companion`，用户本次明确要求改；单独工作树 `/Users/skin/.codex/worktrees/companion-simplify/pearnly-companion`，
  分支 `codex/companion-simplify`，基线 `origin/master`＝`ca7f267`，**未提交、未推送、未出安装包**——该仓 master 的 CI 会把安装包发布到 Cloud Storage，不能随手推）：
  - 连接列表每行只显示「Pearnly 账号 · 状态」，去掉公司 / 账套 / 端点 / 密钥尾号与顶部说明；托盘连接名用 Pearnly 账号（原来用 Express 公司名，
    正是截图里的泰文乱码），菜单状态行超长截断（48 字，完整在悬停提示）。
  - 编辑窗：介绍、「选择 Express 账套」「年度 / 数据根」「选择账套」说明、「允许全部年度与账套」不再显示（控件隐藏保留，配对仍要用检测结果）；
    检测到的数据根**全部授权**；内部默认账套自动取（沿用已配的，否则第一个），只用于心跳身份。保留：配对码、开机自启、选择 Express 文件夹、状态行。
    文案：标题改「添加 / 编辑 Express 连接」、检测结果「已找到 Express 数据」、配对成功只说「小助手将在后台运行」。
  - 测试：新增 `tests/test_gui_pairing_simplified.py`，改 `test_gui_profiles` / `test_i18n` 两处旧口径断言；PySide6 6.11.1 环境 30 项界面相关通过；
    仓库 `.venv` 跑 `tests/` 504 通过、5 失败——与 master 同为既有失败（`test_dbf_writer` 等），无新增。
- **服务端**：`connections.preparation.collect` 以前只认小助手默认账套才给推送凭据证明；现在助手在线、且它上报的可写账套里有网页选的这一个也成立
  （`reported_writable`），但默认账套的缓存（科目表等）不挪给别的账套（这种情况不带小助手缓存）。新增 `tests/test_cowork_preparation_dynamic.py`。
  Cowork 全量 1426 项，失败集合仍是既有 7 项。
- 未验：真机（Windows + Express）上新小助手与新平台的配对、心跳、领取、写入；非默认账套推送时缺小助手缓存会不会让部分票转失败（needs_mapping）。

## 开工核对

- 同步前备份：未提交补丁与未跟踪文件存放在本会话临时目录（不入仓）；快进后逐文件比对 31 处改动全部保留。
- 与 ERP 的文件冲突：`codex/erp-testenv`（领先 main 19 个提交，未合）与 `.claude/worktrees/strange-swartz-a8b667` 的未提交改动，和本工作树改动**文件交集为 0**；ERP 对 `pearnly_core/ocr.py`、`line.py` 只做了新增，Cowork 调用面不变。
- 前一窗口遗留的两个契约失败已处理（见下方“决定”第 4、5 条）。

## 本批目标与范围

用户 2026-09-26：新建录入弹窗「补付款方式，加公司选项、年度选项；MR.ERP 这个选项是第三方 ERP 映射过的实时选项，旧项目已有」；
追问付款选项时的口径：“优先录入，优先第三方为准”。

不做：MR.ERP 推送（新平台未迁，`push.application.service.prepare` 仍只接 Express）；选定账套层的商品/客户目录（旧版走 Playwright）；
真实付费 OCR；Express 目录的本地验证（本地无 Express 连接与助手）。

## 迁移线上基准

- 旧线上最后承接流量的版本：Cloud Run `pearnly-web-ec2e5c957e6b-s3`，digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`，
  源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（映射见 [Cowork 后端迁移](2026-09-20-cowork-backend-migration.md)）。
- 旧云已于 2026-09-24 退役（[退役记录](2026-09-24-legacy-cloud-retirement.md)），无法再在线回读；按该记录“以退役前冻结证据为来源”，首尾基准同为上述版本，无增量。
- 只读来源：本机 `/Users/skin/Developer/Pearnly/pearnly-app` 的 `git show ec2e5c95:<path>`，未 checkout/fetch/修改。

| 旧来源（`ec2e5c95`） | 新落点 | 保持的契约 |
| --- | --- | --- |
| `services/erp/mrerp_http/session.py`（login / list_account_sets / select_company / idus） | `connections/mrerp_collector.py::_Session` | 先 GET 登录页建会话；checklogin 后以进账套是否被踢回判真伪；抓 idus；30 秒超时 |
| `services/erp/erp_mrerp_listing.py::test_mrerp_endpoint` | `mrerp_collector.collect` | 列账套后用第一个试选；永不抛；`ERR_AUTH/ERR_TECHNICAL/ERR_NO_CREDS/ERR_UNEXPECTED` |
| `mrerp_target_projection.py::_run_live/_account_sets/_observation` | `mrerp_collector.collect`、`connections/mrerp_catalog.py` | 网络类失败隔 2 秒重试一次；键 `comidyear:seldb`；采集器 `cloud / mrerp-live-v1`；整站目录不带主档/表单；空目录 `ERR_ACCOUNT_SET_EMPTY` |
| `target_refresh.py::process_mrerp_request` + `mrerp_refresh_worker.py` | `mrerp_collector.run/_lease/_complete` | 租约 180 秒、最多领 3 次；完成时核租约、配置版本；期间又被申请就重排、不发布旧观察 |
| `services/erp/ssrf_guard.py` | `mrerp_collector.public_host` | 系统地址只许解析到公网（另加：跳转不许离开原站点） |
| `line_target_projection.py:435 mode_options`、`cowork_line/document_preflight.py:178` | `public/desk/intake-choices.js` | MR.ERP 入口选现金/赊账、进项现金拦截；Express 入口选服务/存货 |
| `src/home/dms-intake-erp-catalog-refresh.ts`、`dms-intake-erp-accounts.ts` | `public/desk/targets.js::catalog/companies` | 0.8 秒/2.5 秒轮询；快照版本须等于刷新结果版本；Express 年度＝程序目录、不可写账套不给选、年度按数字倒序 |

有意差异（均为用户本次要求或新平台约束）：

1. **公司、年度分两级**：旧版只抓链接文字，平铺成一个「账套」下拉。实读 test01 的选公司页（2026-09-26）是 `<p>` 行 / `<span>` 格（Comname / Comfullname / Remark / Database name），按行读出公司、链接文字仍是年度；认不出行时退回旧版平铺读法。
2. **选择时点**：旧版 Web 在第 4 步（推送前）选账套；这里在新建录入弹窗里选，并把账套键与目录证据（刷新单号 + 快照版本）一路带到推送准备；证明规则不变（非默认账套必须带最新成功刷新的证据）。用户 2026-09-26 看过逐项对照后确认：**公司/年度仍在上传前先选**。
3. **对外请求期间不持行锁**：租约一笔提交、采集、完成一笔提交（旧版同为租约围栏，事务划分按本仓 Frappe 任务实现）。
4. 只做整站目录；按账套范围申请明确失败 `ERR_ACCOUNT_SCOPE_UNSUPPORTED`。

## 变更

后端（`apps/pearnly_cowork/pearnly_cowork/connections/`）：
- 新增 `mrerp_catalog.py`（纯函数：选公司页解析、目录快照）、`mrerp_collector.py`（后台任务：租约、公网校验、登录读取、发布快照）。
- `api.request_refresh` 落库后调 `mrerp_collector.dispatch`（`frappe.enqueue`，`job_id` 按申请去重、提交后入队、short 队列）。
- `service`：`collector_available` 对 MR.ERP 为真（服务端采集）；`targets` 放行 `company_name` 属性。

前端（`public/desk/`）：
- `targets.js`：公司/年度目录（先给已有快照，再刷新并轮询），默认账套预选，错误码文案。
- `intake-choices.js`：弹窗字段改为 目标 → 公司 → 年度 → 进项/销项 → 付款方式（MR.ERP）或过账类型（Express）；失败时显示原因与「重试」；下拉变化改用字段 `onchange`（产品皮肤的组合框上原生 change 只在失焦触发，实测会漏）。
- `intake-upload.js`：目标显示为「系统 · 公司 · 年度」；推送准备带 `account_set_key` 与目录证据；识别提交不传空值；MR.ERP 批次不显示过账类型（整票与明细行）。

其它：新增 10 条词条（年度、目录加载、MR.ERP 进项只能赊账、7 个目录错误码），四语包已重新生成。原判断放 Cowork 术语表 → `intake.` 前缀按 `browser_text` 约定归 core → 已全部移到 pearnly_core 术语表（见第二批）。

## 决定

1. 付款方式以第三方为准：MR.ERP 只有现金/赊账，进项只剩赊账（旧版拦截“进项+现金”）；Express 入口不问付款（票面六级漏斗判定，复核页可逐张改），改问过账类型。
2. 每次打开弹窗选中 MR.ERP 都实时刷新一次（约 7 个请求，本地实测 0.76 秒）；旧版同样在首次选择时刷新，且同一连接同范围只有一个进行中的申请。
3. 本地样板 cowork 组的 Redis 队列改到库 2（见“环境变更”）。
4. `installer`（小助手安装包地址，登录用户 GET）加入 companion 允许的 RPC 集合，测试同时断言它只接受 GET 且先过 `require_actor`。
5. 付款方式合同恢复严格：`None` 是没选，空串/False/0 仍是 `payment_invalid`（前一窗口曾放宽，与既有测试冲突）；识别工作流从库里读回的空值在调用处归一为 None。

## 2026-09-26 第三批（本窗口）

- 列表卖方税号 / 买方 / 买方税号可排序：`Cowork OCR History` 新增 `seller_tax`、`buyer_name`、`buyer_tax` 三个摘要列
  （`contracts/history.py::extract_party_fields`，与摘要同一主页面），`storage.validate_document` 每次写入重算；
  补丁 `v0_0_2_backfill_history_parties` 回填旧行（直接写列、不改修改时间）；`history/api.py` 去掉逐页现算的 `_with_parties`，排序白名单加这三列。
- 录入状态「推送中」→「录入中」。
- Express 连接弹窗（`targets.js::express_dialog`），新增词条 `intake.agent.key/copy/reset/resetConfirm`。
- 修 `companion.api.issue`：表单把 `reset` 送成字符串，合同要求布尔，网页上的「重新生成」此前必然 `companion.request_invalid`；
  入口用 `contracts.wire_flag` 只认 `1/true/0/false` 四种写法，合同层严格性不变。
- 本地环境：cowork-a 打开 `pearnly_cowork_erp_push_enabled=1`（生成密钥需要；回退：`bench --site cowork-a.localhost set-config -p pearnly_cowork_erp_push_enabled 0`）；
  建了一条本地 Express 连接并生成过 2 代密钥（第 1 代已因重新生成失效）。本机未装助手，没有外部写入。
- 演示脚本加轮次参数；轮次 2 已注入。
- 验证：架构 PASS；四语与前端文案 PASS；bench Python 下 `test_cowork_*` 1401 项，与 `origin/main` 同结构基线（1382 项）逐项对比失败集合相同、无新增
  （既有失败：缺 `docx`、manifest 测试桩缺 `check_duplicates` 参数、缺 `scripts/cowork-migration-baseline.py` 以外的同项）；
  浏览器：三列升/降序接口核对正确，Express 弹窗首次生成、再开显示末 4 位、重新生成、复制按钮均走通（内置浏览器禁止写剪贴板，复制提示需在 Chrome 里看）。
- ~~已知问题：Express 以“有有效密钥”判已绑定~~ → 已改，见第四批。

## 2026-09-26 第四批（本窗口）

- Express 状态按助手**实际连上**判断：`companion/credentials._online`（旧线上 `erp-agent-liveness.ts` 同一判据：
  当前这把有效密钥 3 分钟内报过到；只生成密钥、旧密钥报的、时间戳缺失/无时区/1970 下线标记一律不算），`status` 回 `online`；
  卡片「已连接」（与 MR.ERP 同词）/「助手未连接」，Express 只有在线才可选；弹窗开着时每 30 秒重读一次（旧版同节奏），
  状态变了才重画、就绪变了才重读目录。删掉不再使用的 `intake.agent.state.bound/revoked`。
- ~~Express 改「年度 → 账套」~~ → 用户 2026-09-26 定：两家的账套都是「一家公司的一个年度」（MR.ERP 公司下挂年度库，Express 年度目录下
  每家公司一个账套文件夹，只是嵌套相反），**界面统一「公司 → 年度」**。Express 按公司名归组、年度取目录名倒序（旧版界面先年度后账套，是照
  Express 目录结构；这是有意差异）。未新增词条。浏览器用本机虚构目录核对：ACME → 2568/2567，BETA → 2568。
- 目标卡片：三张一排等宽等高（窄屏一列），名称一行、状态下一行、「连接」贴底；不可选时只压暗名称与状态，「连接」不变灰。
- 验证：新增 `tests/test_cowork_companion_liveness.py` 3 项通过；bench Python `test_cowork_*` 1404 项，失败集合与 `origin/main` 基线相同；
  架构 / 四语 / 前端文案 PASS。浏览器：无助手时 Express「助手未连接」不可选；本机用测试密钥模拟助手报到一次（第 4 代密钥，
  虚构 2 个年度 3 个账套，只打本机）后 Express「已连接」可选，年度 2568/2567 倒序，选 2568 后账套为该目录下两家；桌面与 375 宽手机排版核对。
- 本地残留：该 Express 连接带着模拟报到写入的虚构目录与心跳（3 分钟后自然转离线）；Express 目录刷新申请没有真实助手领取，会在 240 秒后显示超时。

## 验证（均为本机，代码＝本工作树未提交状态，基线 `a38d043`）

| 场景 | 环境 / 命令 | 结果 |
| --- | --- | --- |
| 架构 | `python3 scripts/check-architecture.py` | PASS，0 violations |
| 四语 | `i18n-build.py --check`、`check-frontend-i18n.py --strict` | PASS / 0 问题；ERP、DMS 翻译包未被连带改动 |
| JS 语法 | `node --check public/desk/*.js` | 通过 |
| 选公司页解析与目录 | `tests/test_cowork_mrerp_catalog.py`（宿主 Python 3.14） | 8 项通过（含实测 `<p>/<span>` 形状、平铺回退、去重、选择层兼容） |
| 采集会话 | `tests/test_cowork_mrerp_collector.py`（cowork bench Python，假 HTTP） | 9 项通过：登录顺序、账号错误不重试、缺凭据不外呼、网络失败重试一次、5xx、跳出原站、空目录、异常收敛、公网/语法校验 |
| Cowork 离线全量 | cowork bench Python，`unittest discover -p 'test_cowork_*.py'` | 1408 项，6 error；与 `origin/main` 同环境基线（1390 项，同样 6 个 error：缺 `docx` 等依赖）逐项对比**无新增失败** |
| 真实 MR.ERP 读取 | 本地样板 + test01（用户授权，只读） | 刷新成功，快照 revision 2：`TEST · 1010-01-000006 บริษัท ทดสอบการใช้ จำกัด` → `TEST2020 (15:1)`、`TEST2019 (6:1)` |
| 推送侧证明关卡 | bench 内 `read_for_push` + `freeze_target` | 带证据选 15:1 通过；无证据 `catalog_refresh_required`；旧版本、未知账套 `catalog_refresh_invalid` |
| 浏览器 | 内置浏览器真实点击 | 公司自动选中、年度倒序；进项→只剩赊账并提示；销项→现金/赊账；「继续」按规则禁用/启用；上传页显示「MR.ERP · TEST · … · TEST2019」、MR.ERP 批次无过账类型 |
| 失败显示 | 浏览器内替换目录函数模拟 `ERR_AUTH` | 显示原因、出现「重试」、「继续」禁用；点「重试」重新读取 |

未覆盖：MR.ERP/Express 实际推送；Express 目录（本地无连接）；真实识别（付费 OCR）；用户验收。HTTP 200 与页面可打开不算业务验收。

## 环境变更（本地样板，均属本任务）

- `pearnly-sample_sites-cowork` 卷的 `common_site_config.json`：`redis_queue` 由 `redis://redis-queue:6379` 改为 `redis://redis-queue:6379/2`。原因：cowork 与 control 两组共用库 0，cowork 的后台任务被 control worker 领走并报 `cowork-a.localhost does not exist`（ERP 组此前已在库 1，未见记录）。根因在 `scripts/sample-init-group.sh` 给所有组写同一个库，本次未改该共用脚本。回退：`bench set-config -g redis_queue redis://redis-queue:6379` 后重启 cowork 组。
- 本地 MR.ERP 连接 `CWEC-6229892c-…`：凭据由演示账号改为 test01（经 `connections.service.configure`，revision 1 → 2）。
- 容器 `/tmp` 下的测试副本已清除。

## 识别结果轮询（2026-09-26 第二批）

用户：“先把识别结果轮询补上，用官方原生的白屏覆盖和旧项目的百分比”。

- 缺口：识别提交是后台排队（批次 `admitted` → 任务 queued/running/deferred → 批次 `saved`/`not_invoice`，或任务 `failed/uncertain/cancelled`），
  前端原来只看提交那一刻的返回，正常情况下左边不会回填。
- 来源（`ec2e5c95`）：`src/home/dms-intake-invoice.ts` `startRecognize/renderProcessing`（6 份并发；「已完成/总数 · 百分比」按份数；
  结束后「完成 · 成功 {ok} · 失败 {fail}」、全空报「识别结果为空，请重新上传」）；`src/home/ocr-job-poll.ts`（每 1.5 秒、最长 20 分钟、
  连续 10 次读不到才算失联）。四语文案取自旧 `static/i18n-data.js` 的 `dxi-proc-title/-sub`、`dxi-batch-fail`、`dxi-rev-empty`。
- 新落点：`public/desk/intake-upload.js` `run_batch / wait_batch / paint_progress`。遮罩用官方 `frappe.dom.freeze`，正文改 `#freeze .freeze-message p.lead`
  （与 DMS 连接页同一写法）；状态查询用 `frappe.call(..., silent: true)`，失败静默重试。离开页面时收起遮罩。
- 有意差异：官方遮罩里没有「停止识别」按钮（旧版的停止只是放弃前端等待，后台任务不会停）；批次表新增「识别中」「已识别」「非发票」三种状态。
- 词条：`intake.` 前缀按 `browser_text` 约定归 pearnly_core，本批新增的 `intake.*` 词条（含上一批的年度/目录/付款说明）全部放在 core 术语表；
  年度英文原文用 “Fiscal year”，避免按英文补的别名行把 Frappe 自带的 “Year” 全站改译。Cowork 语言包里上一批残留的行已删。
- 验证：浏览器里替换上传/提交/状态三个调用做模拟批次（不产生真实识别与费用）：遮罩依次显示 1/3 · 33%、2/3 · 67%，全部有结论后收起；
  状态查询全部静默；批次表 a 已识别 / b 非发票 / c 失败；第一张回填（PV-2026-0001）。真实识别未跑（付费）。
- 余项：失败原因目前显示原始码（如 `queue_full`），错误码文案随识别字段对照一起补。

## 复核页对齐旧线上（2026-09-26 第三批）

用户：核实识别字段、分页、拆页、一份多页、一页多票是否按旧项目；目标 ERP、原始文件名、页数不显示在识别结果区（随后“干脆不要显示目标 ERP”）；
界面兼容手机，明细与预览照 ERP 页的成熟做法。

对照结论（来源 `ec2e5c95`：`src/home/dms-intake-review*.ts`、`image-viewer.ts`、`services/ocr/invoice_grouper.py`、`routes/history_routes.py`）：

| 项目 | 旧线上 | 新后端 | 本批前端 |
| --- | --- | --- | --- |
| 识别字段表 | `schemas_invoice.py` 30 个抬头 + 明细 4 键 | 原样已迁 | — |
| 复核字段 | 对方（进项卖方 / 销项买方）名称与税号、单据号、票面日期、未税、税额；价税合计、另一方名称与税号、预扣税额 | — | 照旧版补齐并按进/销项换位 |
| 日期 | 改 `date_raw`，服务端换算 `date` | 同规则 | 原前端改 `date` 会被覆盖 → 改为票面日期 |
| 明细 | 名称/数量/单价/金额（`price`/`subtotal`）可改，不能增删 | — | 原前端读 `unit_price`/`amount` 导致单价、金额为空 → 改正；行级过账类型保留（新平台增项，MR.ERP 不摆） |
| 空值提醒 | 单据号、合计、对方税号标黄；销项简易票/收据免买方税号 | — | 用官方必填样式标出，只提醒不拦 |
| 分页 | 整份原件翻页、点票跳到它第一页、翻页反向联动 | 页图接口只给本票自己的页（有意收紧） | 把这一批所有票的页串起来翻；翻到别的票就切到那张票 |
| 编辑落点 | 每张票的合并字段（主页面） | 合计/推送/学习只认主页面 | 原前端“按页编辑”第 2 页以后无效 → 改为写回主页面 |
| 拆页 / 合页 / 移页 | 没有 | 没有且锁死 | 一致，不做 |
| 一份多页 / 一页多票 | 自动分组，一张票一条记录；「发票 i/n」；漏识别横幅 | 已迁 | 「发票」下拉切换全部票（切走前自动保存）；漏识别提示 |
| 重复发票 | 识别后提示 N 张可能重复 | 每条记录有 `duplicate_warning` | 识别后提示 + 本票提示条 |

原判断 → 新证据 → 当前结论：上个窗口注释写“复核逐页做、保存只写当前这一页（用户 2026-09-25）” → 服务端只认主页面，第 2 页以后的改动不影响合计、推送、学习 →
保留“翻页换左侧内容”，改为按票切换、写回主页面（旧线上同一做法）。

手机（照 ERP 采购页 `purchase-layout.js` / `purchase-form.js`）：DOM 里原件区在前、字段在后，桌面网格右栏吸顶，手机纵排；手机上有分区导航（原件 / 识别结果 / 明细，
Desk `form-tabs-list` 样式，只滚动不切换，滚到底高亮最后一区）；明细每行一个可折叠原生小节（第一行展开）。分区导航是录入页自己的小函数，
与 ERP 同写法；若要抽到 core 共用，需与 ERP 窗口协调后再做。目标 ERP、原始文件名、页数已从页面去掉（推送仍按弹窗选择）。

验证（本地样板，内置浏览器）：
- 桌面：字段按旧版排布，票面日期显示佛历原样，空卖方税号标必填；明细金额有值。
- 手机 375×812 与面板窄屏 706：整页宽度＝视口（无横向滚动），原件区在上、工具条换行，分区导航跳转与高亮正常，明细折叠小节正常。
- 保存（演示记录 `CWH-9ea9…`）：改预扣税额 10、票面日期 26/09/2569 → 主页面写入、`date` 由服务端换算为 2026-09-26、其它页不变、未改的格子不写；随后已改回原值（版本 12 → 16，`edit_count` 随之增加）。
- 切换：第 1 张有未存改动时切到第 2 张，第 1 张先自动保存；进/销项改为销项后对方换成买方、卖方挪到另一方（未保存，已丢弃）。
- 漏识别、可能重复两条提示用替换的详情数据验证显示；真实重复/漏识别数据未跑。

## 演示数据与口径收敛（2026-09-26 第四批）

用户：“不要多加文案，新平台以推送和批量识别为主，识别后人工会自然复核”；复核页不加「确认」按钮。

- 撤掉本任务自加、旧版没有的两句：本票“可能重复”提示条、“MR.ERP 的进项只能按赊账入账”；旧版原有的漏识别提示、重复张数提示、批量成功/失败提示保留。
- `history.api.workbench` 增加可选 `source_file`（同一份原件的票，按 `source_index` 排）：从列表进复核时，同一份原件识别出的几张票也能用「发票 i/n」与翻页切换。
- 本地演示数据（`cowork-a.localhost`，用户 tenant-admin@sample.local，工作区唯一那一份）：会话临时目录的造数脚本走真实 `files.service.create` →
  `prepare_history_batch`（分组）→ `persist_batch`（落库、原件绑定、重复检查），只把模型识别换成固定结果（不调模型、不计费、不外连；请求键固定，重跑幂等）。
  原件用容器内泰文字体 Loma 画成票据，公司与税号为虚构测试值。
  共 6 份原件、9 张票：两页一票（IV6909-0012，明细跨页 6 行）、一页两票（RC-0441 / BN2569-88213）、三页三票（TC-2569/0315、OM-66-10421、AE-0927，含 3% 预扣）、
  销项（SV6909-0007）、F3 首张的重复件（服务端判为 exact 重复）、带漏识别信号的收据（0005821）。
- 浏览器抽查：一页两票从列表进来显示「发票 1/2」，翻页切到第二张；销项对方为买方；两页一票明细 6 行、原件 2 页；漏识别提示出现。

- 列表去掉「确认」（行内与勾选后的「确认选中的 N 张」，即 `history.api.complete` 把草稿标成已完成）与「草稿」筛选（用户：“外面那个确认复核按钮不要了”）；勾选框按用户要求保留。
  推送、对账、导出都不看 `staged`，去掉不影响推送；后端 `complete` 接口保留未动。词条 `intake.action.confirmCount` 随之删除。

## 仅识别、勾选删除、列表（2026-09-26 第五批）

用户：新建录入加「仅识别」（识别后导出、不推送，用已有模板）；勾选后右上角出现删除（照 ERP）；列头点了排序看着乱；
列表列改为不要系统单号、加买卖方与税号、价税合计、录入状态（黄推送中 / 红失败 / 绿成功，旧项目有）；不要「复核」按钮。

- **仅识别**：目标卡片加第三张「仅识别」（无需连接）；只选进项 / 销项；上传页右上角「导出」代替「提交」，导出这一批全部票的
  复核工作簿（新后端 `roundtrip.api.export`，旧线上复核工作簿协议，一次 ≤100 张，多了分批）。工作簿要求每张票能原样回导，
  缺金额等会被拒；整份失败时逐张找出不合格的，其余照常导出，不合格的列「单号：服务端原话」。不新增文案。
- **删除**：新增 `history.api.delete(records)`（`history/removal.py`，来源旧线上 `history_batch_delete`：≤500 条，回
  `{deleted, skipped}`）。有意差异：已推送（推送记录 / 推送通知 / `last_push_status`）跳过；原件不删（计费批次引用）；纠错样本
  按设计不可删、原样保留（取样按内连接，记录删后不再参与），删除时 `force` 跳过关联检查；Frappe 仍留「已删除文档」备份；
  DocType `on_trash` 只对删除服务放行，通用 REST / `frappe.client.delete` 仍 403。已知窄窗口：已受理未执行的识别若冻结了被删
  记录的纠错样本，该次识别会在调用模型前校验失败（不计费，重传即可）。列表勾选后右上角「删除」+ 确认「删除选中的 {0} 条记录？已推送的删不掉。」。
- **排序**：价税合计在库里是 Decimal 文本，数据库按文字排（1200 < 13375 < 2140 < 450）；Frappe 16 的 `order_by` 不许表达式，
  改为与计数同一条权限查询取全部行、服务端按数值排序再分页。「单号」是随机号，去掉其列头排序。
- **列表列**：单据号、日期、卖方、卖方税号、买方、买方税号、价税合计、录入状态（`workbench` 按主页面补出卖方税号 / 买方 / 买方税号，
  主页面判据抽成 `contracts.history.primary_page_fields`，摘要与列表共用）。录入状态＝最近一次推送结果，颜色照旧线上
  `erp-log-card.ts`：`pending` 黄「推送中」、`success` 绿「成功」、`manual` 红「待人工核对」、其余红「失败」，未推送不标；
  用 `cell_class` 挂官方 `indicator-pill` 颜色类（共用列表 `format` 只出文字）。去掉行内「复核」与详情弹窗，点一行进复核页。
- 验证：删除（API：删 1 / 跳过 1，同页另一张页图仍 200；UI 勾选→删除→确认→已删除）；通用删除接口 403；价税合计升 / 降 / 翻页按数值；
  「仅识别」弹窗只剩进项 / 销项，上传页「导出」下载 1 份工作簿、RC-0441 列为不合格；列表新列与三种颜色；点行进复核。
  Cowork 离线全量 1408 项，与 main 基线同样 6 个环境 error，无新增失败。
- 本地样板数据：另建并已删除两张 `DEL-TEST-*` 测试票（原件保留）；为看颜色给 IV6909-0012 / AE-0927 / OM-66-10421 直接写了
  演示用 `last_push_status`（success / pending / manual，非真实推送，这三张因此删不掉）。

## 下一步

1. 用真实识别结果（需付费 OCR 授权）走一遍上传 → 识别 → 复核 → 提交；复核后的「确认」（`history.api.complete`）入口是否放在复核页待定。
2. 本批与前一窗口遗留改动提交到任务分支（待用户同意），推送前跑 `build-production.yml` 同名闸门。
3. 可另开小任务修 `scripts/sample-init-group.sh` 的按组分库。

## 收尾状态

交接保留：工作树与本地分支承载全部未提交改动，不能删除；解除条件＝录入完成、提交并按主线规则合入 main 后清理。无远端分支。
