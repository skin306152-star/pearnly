# 产品地图下一步：登录后端到端（B）与站外设置对比（G）——影响评估（待主会话审定）

> 窗口：product-map-next（主会话 local_3d36d798 派），分支 `pearnly/product-map-next-a4dba4`，基线 `56c1807ed`（本分支开工时的 main）。
> 只读调查：没改代码，没起回归实验室，没碰测试机，没连生产和任何站外后台。下文「待核实」的事实要在施工第一块只读核一次（见 §12 第 9 问）。
> 前情：[产品地图评估与施工记录](2026-11-04-product-map.md) 末尾「事故回放」与待补项 B、G；[CC 入口三项缺陷](2026-11-04-cc-entry-fixes.md)。

## 结论先说

1. **B 登录后端到端放在本机 `deploy.sh` 里，回读通过之后对测试租户（testerp / testcowork）跑一遍**，经真实的 fatcc.com 入口和生产 Keycloak 登录。
   回归实验室里没有身份服务和 fatcc.com 入口，测不到 11-03 的根因；free-arm-2 runner 留作备用执行地（同一脚本），第一批不用它。
   浏览器用 **Playwright 1.60.0（产品镜像里锁的同一版本）**：Chromium 跑电脑宽度和手机宽度，WebKit 加 iPhone 设备参数跑登录、切换、退出、对话这几条。
   不进 Development checks，**合入闸门一秒不增加**；部署多约 6–8 分钟（本机跑，零常驻、零月费；每次部署对话那一句约 1–2 泰铢模型费，估算）。
2. **G 站外设置对比并进部署前配置对比那一步**（`deploy.sh --dry-run` 和正式部署构建前，仍是一次 SSH）：Keycloak 两个账号池的 realm、
   每个站点的客户端、fatcc.com 的 pearnly-ai 客户端、LINE 登录身份提供者；每个站点 LINE 频道的 webhook 地址与开关、机器人模式、LIFF 设置、
   默认富菜单；fatcc.com 计费服务上的「租户站点」登记；Cloudflare 解析；证书有效期。分「应一致 / 形状规则 / 只核有没有」三类，
   沿用 11-04 定的口径：应一致和形状不对就拦部署，没有绕过开关；密钥只核有没有。
   读不到的两项如实写明：**LINE 的 redelivery 开关、Messaging 频道与登录频道的关联**，LINE 没有读取接口，留在开户清单和真机单。
3. **事故回放**：11-03 三起里，LIFF 404 从「部署后拦住」变成**部署前拦住**；切换账号从「部分」变成**能**（部署后几分钟内报出，Keycloak 与入口登记的配置差异部署前就报）；
   ERP 添加公司**网页一侧能、CC 桌面版一侧仍是部分**（Qt 窗口只有测试机能测，属于待补项 C）。13 起回放里另外三起「部分」（扫码相关）B、G 帮不上，见 §9。
4. 施工 9 块，机器耗时合计约 14.5 小时；B、G 两条线文件不重叠，可以两个窗口并行，只有地图格式（`product_map.py`、`config.toml`）那两块要一个写者顺序做。
5. 要主会话定 10 个问题（§12），最要紧的三个：**在生产测试租户建两个 e2e 测试账号、在生产 Keycloak 建一个只读客户端**（都是一次性生产写入，要授权）；
   B 部署后不通过是否自动回退（建议不自动，与 `deploy.sh` 现有口径一致）；`e2e:` 测试算不算绿（建议算）。

---

## 0. 需求原文与理解

- 来源：主会话 11-05 任务书；11-04 事故回放（[产品地图评估](2026-11-04-product-map.md) 末尾）的待补项 B「登录后的流程自动走一遍」、G「站外设置不在配置对比里」。
- 用户口径（记忆）：产品 100% 由 AI 做，不看技术产物，只看结果；工程设施要机器自动跑并证明有效；零常驻花费、不加固定月费；
  free-arm-2 的 runner 可以按事件跑；不循环 SSH 生产；登录页不动；生产测试不绑真实操作员、不碰真实 LINE 绑定。
- 我的理解：现在三道关（合入闸门、部署前配置对比、部署后不登录的探针）都碰不到两类东西——
  ① **登录之后**才有的行为（静默登录、跨站切换、退出回哪、在 CC 里点了会不会跳出去）；
  ② **不在 site_config 里的设置**（Keycloak、LINE 控制台、fatcc.com 登记、DNS）。11-03 三起事故的根因全在这两类里。
- 不在范围：CC 桌面版 Windows 窗口行为（待补项 C，测试机 runner）；真 LINE App 里的行为和相机（三星 adb，已有真机单做法）；
  DMS 和控制面入地图（第二期）；不改任何产品行为、不改登录页（（记忆「登录页不动」））。

## 1. 现有同类与复用

| 已有 | 能复用什么 | 为什么不够 |
|---|---|---|
| 流程用例 `scripts/flows/run.py` + `tests/flows/registry.toml` | 断言写法（页面结果 + 状态回读）、登记表格式、`cc_lib.py` 里 CC 对话页的帮手（`say`、`mode`、`toMode`、`pickCompany`、`rows`） | 跑在回归实验室，`session.py` 直接建 Desk 会话，**绕过 Keycloak**；实验室没有 fatcc.com 入口站点（[cc-entry-fixes §1](2026-11-04-cc-entry-fixes.md) 已写明「实验栈无 Keycloak，要部署后真机」） |
| 冒烟 `scripts/smoke/drive.mjs`（CDP、无头 Chrome，刻意不用 Playwright） | 无头 Chrome 的启动参数、页面异常收集 | 只有 Chrome；WebKit 没有 CDP，只能走 Playwright |
| `scripts/smoke/run.py --target production --readonly` | 线上测试租户只读冒烟的先例 | 同样靠容器里建会话，不走登录 |
| 部署后探针 `check-tenant-entries.py`（`assistant-gate`、`liff`、`http`） | 不登录那一段已经逐租户核了 | 走不到登录后 |
| `pearnly_core/browser.py` | 产品镜像锁定 `playwright==1.60.0`（`infrastructure/images/frappe.Dockerfile`），本机 `~/Library/Caches/ms-playwright` 已有 chromium、webkit | — |
| 部署前配置对比 `check-tenant-config.py` + `config.toml` | 一次 SSH、容器里读、密钥换成「有」、三类分级与拦部署口径 | 只读 site_config |
| `infrastructure/production/site-step.py` 的 `check_liff`、`pearnly_control/onboarding/line_liff.py` | 用 LINE Login 频道临时令牌只读 `liff/v1/apps`、落地地址规定（站点根 / `/liff/dms-booking`） | 只在开户时跑 |
| `infrastructure/production/configure-ai-entry.py` | pearnly-ai 客户端应有的配置（`representation`）和读回核对（`checks`） | 只在配置时跑 |
| `infrastructure/production/bootstrap-auth.py` | 两个 realm 应有的设置（记住我 30 天、主题、防暴力破解……） | 同上 |
| `scripts/production/configure-line-login.py` | LINE 身份提供者 `line-<频道号>`、首次登录拒绝流程 `pearnly-line-deny`、作用域 `pearnly-login-source` 的应有值 | 同上 |

结论：**B 新建一个跑真实登录的执行器**（复用流程用例的登记与断言写法、`cc_lib` 的页面帮手）；**G 不新写「应有值」**，
直接 import 上面这些开户 / 配置脚本里已有的期望和读回核对，让「怎么配」和「核什么」只有一份。

## 2. B：登录后端到端

### 2.1 在哪里跑

| 选项 | 好处 | 问题 | 结论 |
|---|---|---|---|
| 本机回归实验室 | 合入前就能拦代码回退 | 没有 Keycloak、没有 fatcc.com 入口站点和计费登记；要补一整套身份服务和控制面站点，而且测的不是真实配置 | 第一批不做；作为二期选项（§12 第 8 问） |
| free-arm-2 runner（按事件跑） | 不依赖本机醒着 | ARM Linux 上只有 Playwright 自带的 Chromium / WebKit 构建；测试账号密码要放进 GitHub 密钥；部署是在本机发起的，要么本机触发后轮询 GitHub（违反「别轮询 CI」），要么再开一条 SSH | 备用：同一脚本能在上面跑，第一批不接 |
| **本机 `deploy.sh` 回读通过后，对生产上的测试租户跑** | 部署本来就在本机跑、本机一定醒着；同步出结果，不轮询；凭据只在本机 `~/.config/`；macOS 上的 WebKit 比 Linux 的更接近 iPhone Safari；本机已装好 Playwright 浏览器 | 只能在部署后发现，生产已经激活（同一镜像同时激活所有站点，没法先激活测试租户） | **采用** |

另给一条单独命令 `python3 scripts/e2e/run.py --target production`，在**站外设置被手工改过之后**（Keycloak、LINE 控制台、DNS）随时跑一遍，不必等下次部署。

前置自检（每次跑之前，任何一项不过就整批记「没测到」并说原因，不算通过）：
1. testerp / testcowork 的解析此刻指向生产（Cloudflare 读回，与 G 同一读法）。测试租户被 `testenv.sh lab-start` 切到测试机时，跑的就不是这次部署的代码——记「没测到：测试租户现在在测试机」。
2. 两个 e2e 账号存在、没被锁（只看一次登录结果，不探测）。
3. 测试租户的 CC 能用：配了智能体密钥（`pearnly_assistant_api_key` 有没有，G 那一步已读）、有 e2e 专用测试公司。

### 2.2 浏览器

- Python Playwright **1.60.0**，与产品镜像同一版本（按兼容矩阵，不另选版本）；本机用独立虚拟环境 `~/pearnly-devtools/e2e-venv/`，不进仓库、不装进产品镜像。
- 三种跑法：

| 跑法 | 引擎 | 视口 | 跑哪些场景 |
|---|---|---|---|
| 电脑 | Chromium（headless shell） | 1280×800 | 全部 |
| 手机宽度 | Chromium | 390×844、触屏、手机 UA | 全部 |
| iPhone 近似 | WebKit + Playwright 的 iPhone 设备参数 | 390×844 | 登录、切换站点、退出、对话一句 |

- **WebKit 只是近似**：Playwright 的 WebKit 不是 iOS Safari，没有 ITP、没有真软键盘、没有「添加到主屏幕」。真 iPhone 上的 Cookie 与 LINE 内置浏览器仍按真机单。
- 不开 Playwright 的 trace 和 HAR（会记下输入的密码和 Cookie）；只在失败时截图，截图前把密码框清空。

### 2.3 测试账号与凭据

- **两个专用账号**，只在测试租户上，不用任何真实客户账号、不用用户的 testdms 等日常账号、**永不绑 LINE**（（记忆「生产测试别绑真实操作员」））：

| 账号 | 账号池 | 能进的站点 | 用途 |
|---|---|---|---|
| `e2e-erp` | pearnly-business | testerp + 同池第二个测试站点（待核实：test01 是否仍在用，§12 第 5 问） | ERP 场景；同池切换（应不再输密码直接进） |
| `e2e-cowork` | pearnly-cowork | testcowork | Cowork 场景；跨池切换的落点 |

  角色：测试租户里给到能「添加公司」的权限（`company.manage`），不给系统管理员。
- 建法：用控制面现有的「加成员」流程在测试租户上建（同一个开户代码路径，不手工改库），密码由脚本随机生成 32 位。**这是一次性生产写入，要授权**（§12 第 4 问）。
- 存放：本机 `~/.config/pearnly-e2e/accounts.env`（权限 600，不进仓库）；脚本只从这里读，不打印、不写日志，传给页面用 `fill`，不出现在命令行参数里。
  走 free-arm-2 时才另放 GitHub 仓库密钥（第一批不放）。
- 防锁号：Keycloak 开了防暴力破解（10 次失败锁），脚本**登录失败一次就停**，绝不重试登录。

### 2.4 第一批场景（ERP、Cowork 各一套，共 14 个）

每个场景都断言业务结果（落在哪个站点、页面上出现了什么、服务端回读的状态），不只看能不能打开；同时收页面异常，主导航不许出现 4xx / 5xx。

| 场景 id | 怎么走 | 断言 | 对应地图行 |
|---|---|---|---|
| `e2e.<产品>.login` | 未登录打开 `https://test<产品>.pearnly.com/assistant` → fatcc.com 两步登录（用户名 → 继续 → auth.pearnly.com 密码页，勾「记住我」） | 落回本站 `/assistant`；CC 外壳出现；`frappe.session.user` 是 e2e 账号；密码页是 pearnly-ai 主题不是 pearnly 主题；中间不出现 Keycloak 默认页 | 登录相关行（共用件 `login-session`） |
| `e2e.<产品>.switch-site` | 已登录 A 站，左下角站点列表点 B 站：ERP 账号先点同池第二站，再点 testcowork（跨池） | 同池：直接进 B，不出密码页；跨池：出 **CC 登录页**（`fatcc.com/?fresh=1`），不是 Pearnly Keycloak 登录页；在 CC 登录页登 `e2e-cowork` → 落 testcowork `/assistant` | `*.cc.action.switch-account`（11-03 事故 2） |
| `e2e.<产品>.add-company` | 侧栏「公司」→ 添加公司 | **在本页弹窗**（Playwright 的新页面事件一次都不能出现）；候选列表由服务端搜索出来；点一家「已添加」的 → 打开那家公司页，仍在本站、没有登录页。只点已添加的，**不新建公司** | `*.cc.action.add-company`（11-03 事故 3 的网页一侧） |
| `e2e.<产品>.logout` | 点退出 | 不出 Keycloak「确定要注销吗」页（（记忆「退出不出确认页」））；回到 fatcc.com 登录页；再开本站 `/assistant` 要重新登录（会话真的没了，不是只换了页面） | `*.cc.action.logout` |
| `e2e.<产品>.liff-web` | 在普通浏览器里打开本站 LIFF 落地（同 LINE 拼出来的两种地址） | 本站一侧不 404；LIFF SDK 转去 `access.line.me` 时带的是本站 LINE 登录频道号。**LINE 登录之后那一半测不了**（要真 LINE 账号），仍留三星真机单 | LINE 进网页、卡片「แก้ไข」（11-03 事故 1） |
| `e2e.<产品>.chat-one` | 新对话发一句固定的话 | 90 秒内出现助手回复、没有出错卡片；侧栏出现这条对话；跑完删掉这条对话（走产品的删除，带确认） | CC 对话视图（Cowork 对话视图现在是红） |
| `e2e.<产品>.operator-new` | 切到「代录」，选 e2e 测试公司，新建任务 | 主栏顶上出现「公司 · 月份」（默认上个月）；侧栏出现准备中的任务；**不点开始**（不派给电脑）；跑完删掉这个任务。产品若没有删除准备中任务的入口，就固定复用同一个准备中任务，不每次新建（施工时核） | 代录新建任务、`*.cc.action.sidebar-modes` |

两套产品可以并行跑（不同账号、不同站点）；同一产品内按上表顺序串行。

### 2.5 接进产品地图

- 新登记表 `tests/e2e/registry.toml`：每个场景写 `id`、`product`、`runs`（电脑 / 手机宽度 / iPhone 近似）、`rows`（覆盖的地图行）、`asserts`（人话）。
- 地图测试引用新增前缀 **`e2e:<场景 id>[@desktop|@mobile]`**：
  - 规则 2（已过期）：引用的场景必须在登记表里；登记表里场景的 `rows` 必须是地图里存在的行。
  - 颜色：算**绿**（业务断言），但在影响清单里标「部署后跑」，让改到这些行的人知道合入前没有它兜底（§12 第 3 问）。
  - 规则 6（改到要补测试）：`e2e:` 满足要求。
- `check-product-map.py --batch`（本批真机单）里，命中有 `e2e:` 测试的行时打印「部署后 e2e 会跑：…」；**真机单不再列这些行的登录后部分**。
- 第一批让下面这些行变绿或补上手机那一端（以施工时的真实数字为准）：两个产品的切换站点、添加公司（Cowork 那行现在是红）、退出、
  CC 对话视图（Cowork 红）、代录新建任务、登录相关行；`untested_baseline.txt` 相应减少。

### 2.6 接进部署

- `deploy.sh` 在回读（`deploy-readback.sh`）通过之后加一步「登录后端到端（测试租户）」：本机跑 `scripts/e2e/run.py --target production --all`。
  **每次都跑全套**（约 6–8 分钟），不按改动挑——站外设置随时会变，按改动挑会漏。
- 不通过：`deploy.sh` 停下，打印哪个场景、哪一步、截图路径、「生产已激活，测试租户登录后流程不通过」，以及回退做法；**不自动回退**（`deploy.sh` 现有口径；§12 第 2 问）。
  「没测到」（测试租户在测试机、账号缺失）单独打印，不算通过也不算不通过，主会话按此决定。
- 网络抖动：只有页面导航超时才重跑那一个场景一次；登录失败从不重跑（防锁号）。
- 截图存 `~/pearnly-devtools/e2e-runs/<日期>/`（本机，不进仓库），每次跑时顺手删 14 天前的，不加定时任务。

### 2.7 耗时与成本

| 项 | 数 | 说明 |
|---|---|---|
| Development checks | **+0 秒** | e2e 不在合入闸门里；地图闸门认 `e2e:` 前缀只多一次查表，<0.1 秒（施工时实测写进记录） |
| 部署 | +约 6–8 分钟 | 估算：登录约 6 秒、切换约 15 秒、对话等回复约 20–60 秒；两个产品并行；电脑 + 手机宽度 + iPhone 近似 |
| 固定月费 | 0 | 本机跑，无常驻、无定时 |
| 每次部署的变动费 | 约 1–2 泰铢（估算，待施工时按账单核） | 对话场景每个产品发一句话的模型费；按「测试费用上限」口径计入 |

## 3. G：站外设置对比

### 3.1 哪些站外设置会让正式租户和测试租户行为不同

关键事实：**Keycloak 的 realm 是按账号池分的，正式租户和测试租户共用同一个 realm**（ERP / DMS 在 pearnly-business，Cowork 在 pearnly-cowork）。
所以 realm 级设置不会造成「正式与测试不同」，但会造成「两个池不同」（ERP 与 Cowork 行为不同）和「与代码期望不同」；
真正逐租户不同的是**每个站点自己的客户端、LINE 频道、LIFF、DNS、入口登记**。

| # | 设置 | 读法（只读） | 在哪读 | 分类 | 「应有」以谁为准 |
|---|---|---|---|---|---|
| K1 | realm 设置 ×2 池：记住我开关、记住我时长（30 天）、普通会话时长、登录主题 `pearnly`、找回密码、邮箱登录、防暴力破解参数、语言 | Keycloak 管理接口 `GET /admin/realms/<池>` | 生产机上（Keycloak 只听 127.0.0.1:8180） | **形状**：等于 `bootstrap-auth.py` 的设置；**应一致**：两个池之间（池名、显示名除外） | `bootstrap-auth.py` |
| K2 | 每个站点的 OIDC 客户端（clientId = 站点名）：启用、授权码登录、回调地址 = `https://<站点>/api/method/pearnly_core.identity.callback`、webOrigins、退出跳转含本站根与 `https://fatcc.com/*`、后台退出地址、所在的池与站点配置里的签发方一致 | `GET /admin/realms/<池>/clients?clientId=<站点>` | 生产机上 | **形状**（站点名代入后逐项核）；**应一致**：把站点名换成 `{site}` 后，正式租户与同产品测试租户的结构相同 | `provisioning._ensure_oidc_client`、`configure-ai-entry.allow_logout_back` |
| K3 | 客户端密钥两端是否一致（Keycloak 里的 vs 站点配置里的） | 两边各取摘要在机器里比，只输出「一致 / 不一致」 | 生产机上 | **只核有没有 + 一致与否**（不出值，§12 第 7 问） | — |
| K4 | pearnly-ai 客户端 ×2 池：公开客户端、PKCE S256、回调含 `https://fatcc.com/api/method/pearnly_control.assistant_entry.api.callback`、登录主题 `pearnly-ai`、不开密码直传 | `GET …/clients?clientId=pearnly-ai` | 生产机上 | **形状**：直接复用 `configure-ai-entry.py` 的 `checks` | `configure-ai-entry.representation` |
| K5 | LINE 身份提供者 `line-<登录频道号>`：启用、登录页隐藏、首次登录流程 `pearnly-line-deny`、clientId = 频道号；作用域 `pearnly-login-source` 存在 | `GET /admin/realms/<池>/identity-provider/instances`、`…/client-scopes` | 生产机上 | **形状**；每个配了 LINE 的站点，它的登录频道在对应池里**有没有**这个提供者 | `configure-line-login.py` |
| L1 | Messaging 频道 webhook：地址 = `https://<站点>/api/method/pearnly_core.line.webhook`、**「Use webhook」开着** | `GET https://api.line.me/v2/bot/channel/webhook/endpoint`（回 `endpoint` 和 `active`） | 各产品 web 容器里，用站点自己的频道凭据；只输出地址与开关 | **形状**（地址）+ **应一致**（开关必须开） | `pearnly_line_channel.webhook_url` |
| L2 | 机器人模式（聊天模式会让自动回复失效） | `GET /v2/bot/info` 的 `chatMode`、`markAsReadMode` | 同上 | **应一致**：正式与同产品测试租户相同，且 `chatMode = bot` | — |
| L3 | LIFF：落地地址 = 站点根（ERP / Cowork）或 `/liff/dms-booking`（DMS）、尺寸 Full、作用域含 openid、加好友提示关 | `GET https://api.line.me/liff/v1/apps`（LINE Login 频道临时令牌） | 同上 | **形状**：复用 `line_liff._usable_url` 与 `site-step.check_liff` 同一口径 | `line_liff.desired` |
| L4 | 默认富菜单是否设了 | `GET /v2/bot/user/all/richmenu` | 同上 | **只核有没有**（与同产品测试租户比，只报告） | — |
| L5 | LINE 登录频道的回调地址是否登记了 Keycloak 的接收地址 | LINE 没有读取接口；本机不登录请求一次 `access.line.me/oauth2/v2.1/authorize?client_id=<频道>&redirect_uri=https://auth.pearnly.com/realms/<池>/broker/line-<频道>/endpoint…`，LINE 对没登记的回调直接回错误页 | 本机（公开地址，不要凭据） | **形状**（待施工时实测 LINE 的这个行为，不成立就降为真机单） | `configure-line-login.py` |
| L6 | webhook 连通（LINE → 服务器） | `POST /v2/bot/channel/webhook/test`：LINE 向我们的 webhook 发一个测试事件，回成功与状态码；不改设置 | 各产品 web 容器里 | **只报告**（§12 第 6 问） | （记忆「LINE 补发已全开」） 记忆里的「先测连通」 |
| L7 | redelivery 开关；Messaging 频道与登录频道的关联（加好友） | **LINE 没有读取接口**，读不到 | — | 不进对比；留在开户清单与三星真机单，如实写「读不到」 | — |
| F1 | fatcc.com 计费服务上的「租户站点」登记（`Pearnly Tenant Site`）：每个生产站点都有一条，签发方等于站点实际账号池、产品对得上 | 计费服务容器里只读一段脚本（同 `check-tenant-config.py` 的读法，不进数据库容器） | 生产机上 | **只核有没有**（缺登记 = 那家进不了 CC，拦部署）+ **形状**（签发方） | 生产各产品 web 容器里的站点列表 |
| F2 | fatcc.com 入口站点自己的配置：签名密钥、各池签发方 | 同上读站点配置，密钥换成「有」 | 生产机上 | **只核有没有** | — |
| C1 | 每个租户域名、`fatcc.com`、`auth.pearnly.com` 的解析：指向哪、是否经 Cloudflare 代理 | Cloudflare 接口 `GET /zones/<区>/dns_records` | 本机（现有令牌，只发 GET） | **形状**：正式租户指向生产、fatcc.com 只解析不代理（（记忆「智能体入口」））；**应一致**：正式与测试租户的代理开关相同。测试租户被切到测试机时只记 NOTE，并告诉 B 跳过 | 记忆与 `fatcc.conf` |
| C2 | 证书剩余有效期（租户域名、fatcc.com、auth.pearnly.com） | 本机 TLS 握手读证书 | 本机 | **形状**：剩余少于 7 天拦部署，少于 21 天只报告 | — |

不在 G 里的（不按租户、或已有别的检查）：Cloud Run / 甲骨文上的识别服务（全平台一份，部署回读另有）、镜像与容器（`deploy-readback.sh`）、site_config（已有）。

### 3.2 接进 `deploy.sh`

- 新脚本 `scripts/production/check-external-config.py`，两段：
  - **生产机段**（Keycloak、LINE、fatcc.com 登记）：并进现有 `config_gate` 的**同一次 SSH**，和 `check-tenant-config.py` 一起跑；
    LINE 读取在各产品 web 容器里执行，频道凭据不出容器，只输出地址、开关、模式这些推导出来的事实。
  - **本机段**（Cloudflare、证书、L5 的公开请求）：接在 `liff_gate` 后面，本机直接跑。
- 时机：`deploy.sh --dry-run` 一次、正式部署构建前一次（站外设置不随镜像变，激活前不必再跑）。
- 分类登记：`config.toml` 加 `[ext.<键>]` 一节，`class` 同现有（same / shape / presence），地图行的 `config` 里 `kc:…`、`line:…`、`liff:…`、`ext:fatcc.com`、`dns:…`
  都要能对上一个 `[ext.…]`，规则 2 照样核（引用不存在算过期）。这样 11-04 已经手写在行里的 `kc:pearnly-business:remember-me` 等就真的有人读了。
- 拦与不拦：沿用审定结论 3——应一致、形状不对就拦，没有绕过开关；要放行就去改站外设置，或改 `config.toml` 的分类（走评审）。只核有没有的只报告，**F1 缺登记例外，要拦**（缺登记那家直接用不了 CC）。
- 次数与频率：每次约 60 个 LINE 请求、30 个 Keycloak 本机请求、2 个 Cloudflare 请求，二三十秒；不循环、不轮询，生产只多用一次已有的 SSH（（记忆「别循环 SSH 生产」））。

### 3.3 凭据

| 读什么 | 用什么凭据 | 在哪 | 怎么不泄露 |
|---|---|---|---|
| Keycloak | **新建一个只读客户端** `pearnly-config-reader`（client_credentials，只给 view-realm / view-clients / view-identity-providers），密钥放生产机 `runtime.env` | 生产机 | 只在生产机上用，脚本不打印；备选是用现有 `pearnly-bootstrap` 管理员但只发 GET（权限过大，不建议）。**建客户端是一次性生产写入，要授权**（§12 第 4 问） |
| LINE | 站点自己 site_config 里的频道凭据（现有），临时令牌现取现用 | 生产 web 容器内 | 令牌、密钥都不出容器，输出只有推导事实 |
| fatcc.com 登记 | 不需要（容器里读本地库） | 生产机 | — |
| Cloudflare | 现有 `~/.config/pearnly-testenv/cloudflare.token`（含 DNS 编辑权限，脚本只发 GET） | 本机 | 不打印；更稳妥是用户在 Cloudflare 后台另建只读令牌（§12 第 10 问） |
| 证书、L5 | 不需要 | 本机 | — |

## 4. 上下游数据

- 不改业务单据、Express、CC 桌面版载荷（`payload_contract.py` 不动）。
- e2e 在测试租户上会产生：一条对话（跑完删）、一个准备中的代录任务（跑完删或固定复用）、登录会话记录。不派任务给电脑、不推 Express、不调识别。
- G 全程只读；L6（webhook 测试）会让 LINE 往我们 webhook 发一个测试事件，我们的处理器按 LINE 规定忽略这类事件（施工时核处理器确实不回复、不落库）。

## 5. 隔离与权限

- e2e 账号只在测试租户、只给 `company.manage`，不进任何正式租户；不绑 LINE；不用于其他用途。
- 正式租户上 B 一个都不登录（沿用 11-04 口径：正式租户只做不登录的入口探针）。
- G 读的是平台配置，不读任何客户业务数据；Keycloak 只读客户端连用户列表都看不到（不给 view-users）。

## 6. 入口：网页、手机、LINE、拍照

- 电脑宽度、手机宽度、iPhone 近似三种跑法见 §2.2。
- LINE：网页一侧（落地、拼路径、登录频道号）由 B 和 G 覆盖；LINE App 内、真 LINE 登录之后仍是三星真机单。拍照 / 相机不在本次范围（已有假摄像头流程）。
- CC 桌面版窗口行为不在范围（待补项 C）。

## 7. 共用件与连带回归

- 不改产品代码，不改登记的共用件；改的是 `scripts/impact/product_map.py`、`scripts/check-product-map.py`、`config.toml`、`scripts/production/deploy.sh` 与新脚本。
- `scripts/production/` 下新脚本要被 D 块的「调用方」认法看见（它会读站点上的接口吗？不读，只读 Keycloak / LINE / 本地文件），不影响白名单接口分诊。
- `deploy.sh` 改动按现有做法给离线测试（假 SSH、假 HTTP），照 `test-check-tenant-config.py`、`test-check-liff-endpoints.py`。

## 8. 存储与成本

- 固定月费 0；无常驻服务、无定时任务。
- 本机：Playwright 虚拟环境约 100 MB（浏览器已装）；截图每次几 MB，14 天自动删。
- 变动费：每次部署对话场景约 1–2 泰铢（估算）；LINE / Keycloak / Cloudflare 读取不收费。
- free-arm-2：第一批不用，不占它的资源。

## 9. 事故回放复核

先说口径（同 11-04）：「能」= 机器报出来并停下；「部分」= 只报一部分或只提醒；「不能」= 三道关都看不到。本次多了第四道关「部署后登录 e2e」。

### 11-03 三起

| # | 事故 | 11-04 时 | 11-04 已做的补项之后 | 加上 B、G 之后 | 还差什么 |
|---|---|---|---|---|---|
| 1 | mrerp / erpcowork LINE 卡片「แก้ไข」404（LIFF 落地带子路径） | 部分（只在部署后拦住） | **能，部署前**：A 块在部署前用 HTTP 走 LIFF 落地 | **能，部署前，而且直接读到设置本身**：G 的 L3 读 LINE 里的 LIFF 落地地址，不靠 HTTP 推断；B 的 `liff-web` 在测试租户再走一遍 | LINE App 里点卡片仍是三星真机（只剩「LINE 本身的行为」，不再有配置盲区） |
| 2 | CC 切换账号第一次落到 Keycloak 登录页 | 部分 | 部分（单测 `SiteSwitchTests` 拦代码回退；探针只到不登录那一步） | **能**：B 的 `switch-site` 在真实 fatcc.com + Keycloak 上走同池、跨池两种切换，落到 Pearnly 登录页就报；G 的 K1（记住我）、K2（退出跳转、回调）、F1（入口登记）差异部署前报 | 代码引起的回退要到**部署后几分钟**才报（生产受影响到回退为止）；想在合入前拦，要做二期「实验室带身份服务」（§12 第 8 问） |
| 3 | ERP 版 CC「添加公司」跳系统浏览器、要重新登录 | 部分 | 部分（流程 `cc.companies` 拦网页一侧） | **网页一侧能**（B 的 `add-company` 在真实登录会话里断言不开新窗口、不出登录页，两个产品都跑，Cowork 那行由红变绿）；**CC 桌面版一侧仍部分** | Qt 窗口把新窗口交给系统浏览器，只有在 Windows 上跑 CC 桌面版才测得到——待补项 C（测试机 runner），B、G 管不到 |

### 13 起回放里其余「部分」与「不能」

| # | 事故 | B、G 能否改变 | 说明 |
|---|---|---|---|
| 5 | 10-23 扫码弹窗全黑 | 不能 | 相机问题，与登录、站外设置无关；11-04 E 块已把扫码入口逐个成行、规则 6 要求补测试，那条线的复核不在本方案 |
| 6 | 10-15 改盘点扫码其它入口黑屏 | 不能 | 同上，属规则 6 |
| 12 | 11-01 LINE 盘点相机扫到未知码点「新建物料」没反应 | 不能 | 同上 |
| 7 | 10-22 手工重建 web 漏挂 frontend，全站 502 | 部分（新增） | 手工操作仍不经过闸门；但有了单独命令 `scripts/e2e/run.py --target production`，F 块规矩「手工动过生产就跑一次回读」可以加上「再跑一次 e2e」——仍靠规矩，不是机器强制 |
| 11 | 10-03 剖析脚本顶掉 testdms 的 LINE 绑定 | 不能 | 运维临时脚本、DMS；e2e 账号永不绑 LINE 只保证 B 自己不再犯同类错 |

**小结**：11-03 三起由「部分 / 部分 / 部分」变成「能（部署前）/ 能（部署后几分钟）/ 网页一侧能、CC 桌面版一侧部分」；
其余三起「部分」都是扫码类，B、G 帮不上，归规则 6 那条线。

## 10. 文案与官方 UI

不涉及：不改任何界面与文案；脚本输出是给开发看的中文，不进产品词典。e2e 只读页面上的固定 `data-slot` 和地址，不依赖文案（文案改动不会弄红 e2e）。

## 11. 测试与验收

- 离线：e2e 执行器的凭据读取、前置自检、场景登记校验、失败处理（假浏览器）；G 的三类比较用固定样例（含 11-03 之前的 LIFF 子路径、记住我没开、缺入口登记、webhook 关着、证书将过期）各一条成功与失败；
  `check-product-map.py` 认 `e2e:` 前缀与 `[ext.…]` 的规则 2 用例。全部进 `tests/`，CI 跑。
- 真实验证（各一次，绑定 SHA 记进本记录）：
  - B：在当前生产版本上手动跑一遍全套（测试租户在生产上时），全过；再**故意**用一个会失败的场景参数（例如期望跨池切换直接进站）确认它会红——证明不是空跑。
  - G：一次 `deploy.sh --dry-run`，逐项打印读到的事实；拿 11-03 之前的 LIFF 形状、关掉的 webhook 做离线复现，证明会拦。
- 验收：主会话看「事故回放」两张表是否属实；用户不看技术产物，只听结果（拦住了什么）。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | B 在哪跑 | 本机 `deploy.sh` 回读后跑；free-arm-2 只做备用（同一脚本，第一批不接、不放 GitHub 密钥） | B1 开不了工 |
| 2 | B 部署后不通过怎么办 | 停下报告，**不自动回退**（与 `deploy.sh` 现有口径一致），由主会话按自主部署的回退规则处理 | `deploy.sh` 的退出处理 |
| 3 | `e2e:` 测试算不算绿 | 算绿，影响清单标「部署后跑」；规则 6 认它 | 不算的话这些登录行只能进真机单，基线减不下来 |
| 4 | 一次性生产写入：① 在 testerp / testcowork 建 `e2e-erp`、`e2e-cowork` 两个账号（控制面加成员流程）；② 在生产 Keycloak 两个池建只读客户端 `pearnly-config-reader`，密钥进 `runtime.env` | 同意两项；由施工窗口按脚本做、读回核对、记进本记录 | 没有 ① B 跑不了；没有 ② K1–K5 只能用管理员账号读或不做 |
| 5 | 同池切换要第二个 ERP 测试站点 | 先只读核 test01 是否还在用；在用就给 `e2e-erp` 加进去，不在用就改用 testdms（同在业务池，只测切换落点，不测 DMS 功能） | 同池切换那一半测不了 |
| 6 | L6 webhook 连通测试（LINE 往我们 webhook 发测试事件） | 做，只报告；只在 `--dry-run` 和正式部署构建前各一次 | 不做就少一项「LINE → 服务器」连通证据 |
| 7 | OIDC 客户端密钥「两端一致」：在生产机里比两边摘要，只输出一致与否 | 做（不一致 = 那家登录全挂），算对 11-04「密钥只核有没有」的一处细化 | 不做就只能核有没有 |
| 8 | 二期「回归实验室带身份服务 + 入口站点」，让 B 的登录类场景在合入前跑 | 先不做；第一批上线后，若 B 部署后报出代码回退超过一次再做 | 代码引起的登录回退只能部署后发现 |
| 9 | 施工前只读核一次生产（一次 SSH）：测试租户的 `pearnly_assistant_entry_url` 与智能体密钥、test01 是否在用、各站点 LINE 频道凭据键名、Keycloak 站点客户端的实际形状 | 同意，B0 块做一次，只读、不循环 | 若干形状规则要等施工中再猜 |
| 10 | Cloudflare 令牌 | 先用现有令牌只发 GET；用户方便时在 Cloudflare 后台另建只读令牌替换 | 不影响施工 |

## 13. 工作量与排期（机器耗时）

| 块 | 内容 | 依赖 | 机器耗时 |
|---|---|---|---|
| B0 | 只读核生产一次（§12 第 9 问）；按授权建两个 e2e 账号与测试公司、建 Keycloak 只读客户端，读回核对 | 授权 | 约 0.5 小时 |
| B1 | `scripts/e2e/run.py` 执行器：Playwright 1.60.0 本机虚拟环境、凭据读取、前置自检、三种跑法、截图与清理、离线测试 | — | 约 2 小时 |
| B2 | 14 个场景（ERP、Cowork 各 7 个），对着生产测试租户调通；含一次故意失败的验证 | B0、B1 | 约 3 小时（每轮全套约 6–8 分钟，预计调 8–10 轮） |
| M | 地图接线：`e2e:` 前缀与 `[ext.…]` 分类进 `product_map.py`、规则 2 / 6、`--batch` 文案、`tests/e2e/registry.toml`、行引用更新与基线减少、闸门自测；推分支跑 CI | B1（格式）、G1（键名） | 约 1.5 小时 + CI 约 15 分钟 |
| B3 | `deploy.sh` 回读后接 e2e、停下文案、离线测试 | B2、M | 约 1 小时 |
| G1 | 生产机段：Keycloak 读取（K1–K5，复用三个配置脚本的期望）、fatcc.com 登记读取（F1–F2）、离线样例 | B0（只读客户端） | 约 2 小时 |
| G2 | 容器段：LINE 读取（L1–L4、L6），凭据不出容器；本机段：L5 公开请求（先实测 LINE 行为） | — | 约 2 小时 |
| G3 | 本机段：Cloudflare（C1）、证书（C2）；与 B 前置自检共用 DNS 读法 | — | 约 1 小时 |
| G4 | `deploy.sh` 的 `config_gate` 接线（同一次 SSH）、一次真实 `--dry-run`、事故复现用例 | G1–G3、M | 约 1.5 小时 |

合计约 **14.5 小时**机器耗时（不含等授权、等 CI）。B1–B3 与 G1–G4 文件不重叠，可以两个窗口并行；M 块碰地图格式，由一个写者在两条线之间做。
每块按仓库流程：全闸门 → 推分支 → Development checks 绿 → 快进合入 main。本方案不涉及生产部署本身，接线随下一批部署生效。
