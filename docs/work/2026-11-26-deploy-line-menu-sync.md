# 部署时同步站外表面（富菜单等）——盘点、设计与实现（11-26 盘点，11-28 实现）

分支 `pearnly/deploy-line-menu-sync`，11-28 变基到 origin/main 568f4b7f6（= 线上 release-20261127-1225）。11-26 只有盘点与设计；11-28 起有实现，见文末「11-28 实现」。

## 背景与用户原话

- 11-26 晚 DMS 库存停放报表上线后发现：部署不会重发富菜单。ris（正式客户，25 个绑定）的 DMS 六宫格从 9 月开户后**从没更新过**，
  10-02 加的「使用手册」格、11-08 缩短的泰文标签、11-26 的「报表查询」都没到客户手上；testdms 也停在 11-08 之前的泰文标签。
- 用户原话：「这真是一个傻逼的开发流程」；「三个产品以后不允许出现这样的情况」。
- 硬要求（主会话转达）：Cowork / ERP / DMS 三个产品、所有租户、所有「用户看得到但存在站外」的表面（LINE 富菜单、LIFF、回调、登录主题等），
  部署后都必须与代码一致——由部署自动同步并回读，有漂移检查，失败即部署失败。盘点表每项落到「已由部署覆盖 / 本分支补上 / 明确不适用（理由）」之一。
- 11-26 晚的手工补发（主会话执行，脚本由库存报表窗口写、审后跑）：
  - 只读计划 `MODE=plan`：`~/pearnly-devtools/prod-ro-1126/dms-menu-plan.out`——ris 8 套全要新建（现挂的是 09-20 / 09-27 第一版，
    基础版 4 格、查询版 5 格，图片散列与仓库第一版逐一对上），testdms 5 套（4 套 query + th:basic，后者是 11-08 d838ea3da 泰文第 4 格标签缩短）。
  - 发布 testdms：`dms-menu-publish-testdms.out`；发布 ris：`dms-menu-publish-ris.out`（BEFORE 行可回退），两站 readback_ok，再跑 plan 无变化。
  - ris 发布前核过三个教程视频键都配了、6 个地址 HTTP 200。
  - 脚本：`dms-menu-publish.sh/.py`（plan / publish / rollback）、`dms-menu-diff.sh/.py`（逐格比对）。

## 盘点表（只读勘察，origin/main 71c46b036）

结论：**平时部署（deploy.sh → deploy-run.sh → activate-product.sh → activate-foundation.sh → deploy-readback.sh）只重新生效 Keycloak 登录主题文件**；
富菜单、webhook、LIFF、Keycloak 身份提供方 / 客户端都只在开户或改渠道（`site-step.py` 的 `set_line` 等）时写一次。
其中 webhook、LIFF 落地地址、Keycloak 各项**部署前已有内容级漂移检查且会拦**；**富菜单没有任何内容级检查**（只看「有没有」）。

| # | 站外表面 | 写入点 | 谁触发 | 部署重跑 | 现有漂移检查 | 存在 / 内容 | 归属（三选一） |
|---|---|---|---|---|---|---|---|
| 1 | DMS 富菜单 8 套 + 官方号默认菜单 | `pearnly_dms/menu/rich_menu.py` `publish` | 开户 `set_line`；人手 | 否 | `verify_site`（只开户）；L4 只看有无默认菜单；`pearnly_dms_rich_menus` 不在产品地图 | 编号 / 存在 | **本分支补上** |
| 2 | ERP 富菜单 60 套 | `pearnly_erp_portal/integrations/line_menu.py` `publish` | 同上 | 否（v1_28 补丁只重挂） | `verify_site` 只看配置键齐不齐；check-tenant-config presence | 存在 | **本分支补上** |
| 3 | Cowork 富菜单 28 套 | `pearnly_cowork/line/menu.py` `publish` | 同上 | 否 | 同上 | 存在 | **本分支补上** |
| 4 | 个人菜单挂载（三产品） | 各 `sync` / `reconcile` | 绑定 / 权限变化；DMS 每 10 分钟对账 | 否 | DMS 对账读 LINE；ERP / Cowork 只比 Redis | 编号 | **本分支补上**（发布后 `reconcile()` 已排重挂；回读抽查绑定用户） |
| 5 | Messaging webhook 地址 | `site-step.py` `set_webhook` | `set_line` | 否 | 部署前 L1 内容相等 + active（拦）；L6 测试推送（报告） | 内容 | **已由部署覆盖**（检查）；地址由站点名算出、代码改不动它 → 不需重写 |
| 6 | Use webhook 开关 / chatMode | 无写接口（LINE 后台手动） | 人手 | — | L1 / L2（拦） | 内容 | **明确不适用**：LINE 无写接口，只能检查（已检查） |
| 7 | LIFF 落地地址 / 类型 / 作用域 | 控制面 `onboarding/line_liff.py` `ensure` | 开户、改渠道、拆租户 | 否 | `check-liff-endpoints.py` 部署前后各一次（拦）；尺寸 / 作用域读不到 | 落地地址内容 | 落地地址**已由部署覆盖**（检查）；尺寸 / 作用域要 LINE Login 密钥、只在控制面 → **待定**：要么部署时经控制面调 `ensure` 对齐，要么定为不适用（理由：代码里应有值 10 月后没变过） |
| 8 | Keycloak 账号池 LINE 设置 | `configure-line-login.py` `configure_realm` | `set_line`；人手 | 否 | K5 `verify_realm`（拦） | 内容 | **已由部署覆盖**（检查）；漂移时拦部署、人手重跑 |
| 9 | Keycloak LINE 身份提供方 | `configure-line-login.py` `configure_site` | `set_line` | 否 | K5 按 `idp_representation` 比（拦）；映射 `line-user-id` 内容没比 | 内容 | 已覆盖；**本分支补**映射内容回读 |
| 10 | 站点 OIDC 客户端 | `keycloak_admin.create_oidc_client` | 开户、repool | 否 | K2 / K3（拦） | 形状 + 密钥 | **已由部署覆盖**（检查） |
| 11 | 账号池设置（主题名、记住我等） | `bootstrap-auth.py` | 人手 | 否 | K1 逐键（拦；SMTP 不读） | 内容 | 已覆盖；SMTP **明确不适用**（含密码，读回不安全；发信失败另有告警链） |
| 12 | 登录主题文件 | `activate-foundation.sh` | **每次部署** | **是** | 只记摘要 | 摘要 | **已由部署覆盖** |
| 13 | `pearnly-ai` 客户端、租户退出跳转 | `configure-ai-entry.py` | 人手 | 否 | K4 / K2（拦） | 内容 | **已由部署覆盖**（检查） |
| 14 | 用户 LINE 关联（federated identity） | `pearnly_core/account/line_login.py` | 绑定钩子 + 每小时对账 | 否 | 只看站点 `login_link` | 状态 | **明确不适用**：按用户、随绑定变化，每小时对账已在跑，与代码版本无关 |
| 15 | LINE Login 频道回调地址 | 无写接口 | 人手 | — | L5（拦） | 登记与否 | **明确不适用**：无写接口，只能检查（已检查） |
| 16 | 测试租户 Cloudflare 解析 / 证书 | `testenv-cloudflare.sh` / certbot | 人手 / 开户 | 否 | C1、证书天数（拦） | 内容 | **明确不适用**：与代码版本无关 |
| 17 | fatcc.com 租户登记 | `assistant_entry/registry.py` | 开户 | 否 | F1（拦） | 内容 | **已由部署覆盖**（检查） |

另：`scripts/check-line-labels.py` 只管按钮 20 字，不管 `chatBarText` 14 字（代码里 `[:14]` 截断）；没有任何检查拦「图 / 格子 / 词条改了但部署不发」。

## 设计（第 1–4 项）

1. **各产品声明自己的菜单表面**（ADR-009）：在 `hooks.py` 加 `pearnly_line_surfaces = ["<模块>"]`；模块提供
   - `wanted() -> {键: 菜单名}`：与 `publish()` **同一个**函数算（把 publish 里「body + 图 → 散列名」抽成 `_release()`，publish 与 wanted 都用它，避免两份算法）；
   - `drift() -> {behind, missing, default_ok}`：只读，GET `richmenu/list`（DMS 加默认菜单）对照站点配置编号；
   - `publish()`：现有函数不变（幂等、不删旧）。
2. **共用跑法** `pearnly_core/line/surfaces.py`（多产品共用层，文件头写明）：`check(site)` / `sync(site)`，按 hooks 汇总，单站失败抛错带站点与键。
3. **部署接入**：`deploy-run.sh` 在三个产品激活之后、foundation 之前加一步 `surfaces`：每个产品网页容器里对每个站点
   `sync`（有漂移才发布）→ 再 `check` 回读；发布前把原配置打进部署日志（同 `dms-menu-publish` 的 BEFORE 行，可回退）；任何一站失败 → 部署失败并打印停在哪一站、回退命令。
   `deploy.sh --dry-run` 只跑 `check` 报差异。`deploy-readback.sh` 再跑一遍 `check`，有落后即 PROBLEM。没有渠道凭据 / DMS 没 LIFF 的站点跳过并列出（同 `set_line` 条件）。
   ERP / Cowork 的图在发布时用 Pillow 拼，散列只在同一镜像里稳定——所以检查与发布都在生产网页容器里跑，不在本机算。
4. **闸门**：静态检查 `scripts/check-line-menu-sync.py`——改了 `public/images/menu|line-menu/**`、菜单 payload 模块或 `TXT_MENU_*` / 菜单词条，而产品没在 `pearnly_line_surfaces` 声明 → 拦；
   同时把 `pearnly_dms` 加进 `scripts/impact/product_map.py` 的 `CONFIG_SCAN`，`pearnly_dms_rich_menus` 进配置对比。部署说明写进 `docs/development/`（部署流程一节）。
5. **测试**：离线（替身 LINE）——有差异 → 发布 → 回读一致；无差异 → 不发布；发布失败 → `sync` 抛错、deploy-run 失败；无凭据 → 跳过并报告。候选离线、code-review medium。

## 11-28 实现

按上面设计 1–5 做完，和设计的差别：共用跑法是包 `pearnly_core/line_surfaces/`（`__init__` 是逐站函数，`__main__` 是容器里跑的命令），
不是 `pearnly_core/line/surfaces.py`（`pearnly_core.line` 是单文件模块）；第 4 项闸门改成按类的「站外写入点登记」，不只盯菜单图和词条。

- **声明**（ADR-009）：三个产品 `hooks.py` 各加 `pearnly_line_surfaces = [{"product": …, "module": …}]`。带 `product` 是因为一个站点可能装了别的产品的 App，
  只用和站点配置 `pearnly_product` 对上的那条，不会往本频道发别的产品的菜单。
- **三个菜单模块**：抽出 `_release(language, variant)`（body + 图 → 内容散列名），`publish()` 与新的 `wanted()` 都用它，散列只有一份算法；
  加 `SURFACE` / `ready()`（与开户 `set_line` 同条件：有渠道凭据，DMS 另要 LIFF）/ `drift()`（只读：behind / missing，DMS 加官方号默认菜单与旧键 `basic` / `query`）/
  `restore(before)`。ERP / Cowork 的 `_image` 加进程内缓存（图与站点无关；一次同步要算三遍，ERP 每站 60 张，本机量约 0.07 秒一张）。
- **部署**：`deploy-run.sh` 三个产品激活后、基础层前加 `surfaces` 步骤，每个产品网页容器里
  `python -m pearnly_core.line_surfaces sync --product <产品>`：逐站有漂移才 `publish()`，发布前打 `BEFORE` 行，发布后 `drift()` 回读，
  仍不一致或出错 → 状态 `failed surfaces`，`deploy.sh` 打印哪一站、怎么回退（`restore --before '<BEFORE 的 config>'`）。日志 `surfaces-<产品>-<短SHA>.log`。
  `deploy-readback.sh` 再 `check` 一遍，有漂移即 PROBLEM。`deploy.sh --dry-run`（及正式部署前）在租户配置对比**同一次 SSH** 里按线上正在跑的代码 `check`，
  只报「一致几项 / 会发布哪些 / 跳过哪些」，不拦；线上镜像还没有这个模块时写明「部署后才有」。
- **跳过**：没有渠道凭据的站点（如 ccdemo）、DMS 没 LIFF 的站点、**从没发布过**的站点（站点配置里没有菜单键：第一次发布归开户 `set_line`，DMS 的发布会换官方号默认菜单，开户没走完不能被部署抢先换），跳过原因进 `SUMMARY.skipped`，演练预览逐条打印。只缺几个键（加了语言 / 组合）照常发布。一个产品的站点上一条声明都没有 → 算失败（声明丢了不能静默通过）。
- **闸门** `scripts/check-external-writes.py`（CI 步骤与测试 `tests/test_check_external_writes.py`）：生产代码（apps 非测试、infrastructure/production、
  scripts/production）里，模块提到 LINE / Keycloak / Cloudflare 接口、函数里有写动词的，必须在 `scripts/external-writes.toml` 登记
  kind（deploy-sync / drift-check / runtime / token / probe / teardown / manual）与 note；新增不登记、登记了已不在、deploy-sync 没在 hooks 声明、
  声明的模块缺约定函数 → 拦。现登记 62 个函数（3 个 deploy-sync 发布 + DMS 回退、11 个 drift-check、其余运行时 / 令牌 / 校验 / 拆租户），没有 manual。
- **测试**：离线 `tests/test_line_surfaces.py`（候选离线车道；替身 LINE：无漂移不发布、有漂移发布 + BEFORE + 回读一致、从没发布跳过而缺几个键照发、
  发布没生效即失败、无凭据跳过、DMS 无 LIFF 跳过、DMS 默认菜单算漂移、只用本产品声明、restore 换回原配置、命令逐站汇总与退出码），
  `tests/test_line_menu_publish_lock.py` 照旧通过。

### 盘点表各项的落点（11-28）

| # | 落点 |
|---|---|
| 1–3 | **部署同步**（`surfaces` 步骤 + 回读 + 演练预览），登记 deploy-sync |
| 4 | 发布后 `reconcile()` 给全部绑定用户排重挂（原有）；登记 runtime。回读没有逐用户抽查：逐个 GET 用户菜单要用户 ID，测试与正式都牵涉真人，个人挂载按事件与 DMS 每 10 分钟对账另管 |
| 5、8–11、13、17 | **drift-check**：部署不重写，部署前内容级检查拦部署（原有），登记写明是哪一项检查 |
| 7 | **drift-check（部分）**：落地地址部署前后核；尺寸 / 作用域不纳入同步——LINE Login 密钥只在本机控制面、生产机没有；`desired()` 10-05 后没变过，改它要同批在控制面对全部租户重跑 `ensure`（写进登记 note） |
| 9 映射 | 映射 `line-user-id` 内容回读**没做**（不在本任务硬要求内，留作后续；现有 K5 已比身份提供方本体） |
| 6、14、15、16 | 不变：LINE 无写接口 / 按人 / 与代码版本无关 |
| 12 | 原本就每次部署重新生效 |

### 11-28 午加的（主会话追加的完成标准）

- **说清改了什么**：`check` 的结果和部署日志的 `PLAN` 行，对每个落后的键读 LINE 上现挂那套的 body 与图，逐项列出文字字段旧 → 新
  （`chatBarText`、尺寸、每格位置与 `label` / `displayText` / `data` / `uri`），图逐字节比（`same` / `changed`，读不到 `unknown`）。
  `deploy.sh` 的预览按站点打印「无变化 / 将发布 N 套（图 …）」及每套的变化。模块约定加 `release(key)`。
- **重挂回读**：发布后当场给每个有效绑定的人重挂（产品自己的 `sync`，约定加 `remount`），再逐人 GET：发布前挂着个人菜单的人一个不少、
  每个人挂的都在这一版的编号里，否则部署失败。发布前没挂、这次挂上的（以前漏挂）只计数，SUMMARY 里有 `mounted_before / mounted_after`。
  没用「人数完全相等」：以前漏挂的人这次补上是对的，不能因此让部署失败。
- **ERP / Cowork 的对照放进独立模块** `line_menu_surface.py` / `menu_surface.py`，原菜单模块与 main 一字不差：原菜单模块是 LINE 盘点相机两行
  只能真机的地图入口文件，改它就要真机记录；发布逻辑没变，没理由动它。两边同一算法由「发布后再对照必须无漂移」的测试守住。
- **预览出自要上线的镜像**（主会话 11-28 定）：`scripts/production/surfaces-plan.sh`，部署时在激活前跑（`plan` 步骤，没跑成 = `failed plan`，线上没动），
  演练经一次 SSH 跑同一份（默认最近一次放上生产的镜像，`--digest` 指定已放上去的新镜像；镜像早于本功能时写「没有可比的」，不算失败）。
  一次性容器：`--rm`、`--memory 2g`、`--volumes-from` 网页容器只读、同网络与 DNS、`FRAPPE_STREAM_LOGGING=1`（只读挂载下 Frappe 写日志文件会直接报错退出，
  测试机实测），`common_site_config.json` 换成所有 `redis://` 指向连不上地址的副本（缓存连不上 Frappe 只用进程内缓存，新代码的 hooks / 译文写不进线上缓存）。
  测试机实测（对退货窗口在跑的 erp 栈只读）：testerp 60 套一致，ERP 一轮 19 秒；对方 erp-web 日志前后一致无报错；一次性容器与临时文件删净。

### Cowork 12 套落后菜单（erpcowork 正式、testcowork 测试）

11-26 线上只读：两站各 28 套里落后 12 套——en / zh 的录入系 4 套（intake、intake+stocktake、intake+work、intake+stocktake+work）、
th 的盘点系 4 套（stocktake、stocktake+work、intake+stocktake、intake+stocktake+work）。代码历史：图块 9 月后没改；11-08 d838ea3da 把录入格按钮
label 从「Send documents to ERP」截 20 字（英文成了「Send documents to ER」）改成「Send to ERP」。新旧逐格对照要读 LINE 上现挂菜单的 body 与图
（只读脚本 `~/pearnly-devtools/prod-ro-1128-menu/cowork-menu-compare.sh`，交主会话排队）；补发由主会话批准后做，先 testcowork 后 erpcowork。
11-28 线上只读对照（主会话跑）：两站 12 套的**图逐字节相同、格子与去向相同**，只有按钮文字字段不同——中文录入格点下去发出的那句
（`displayText`）与标签「发单据进 ERP」→「发送单据至 ERP」（用户看得到）；英文录入格标签「Send documents to ER」→「Send to ERP」、泰文盘点格
标签「ตรวจนับสต็อก」→「ตรวจนับสินค้า」（只读屏用）。主会话定：不手工补发，**由这个分支的第一次部署经 `surfaces` 步骤发出**，当作它第一次真实验证。

泰文那条的原因（11-28 核对，更正当天先报给主会话的「取到了 ERP 的词条」）：9 月发布时 Cowork **自己的**译文就是「ตรวจนับสต็อก」
（37895fe7b），10-08 泰文文案整改（56f5bc16e）改成「ตรวจนับสินค้า」、同一提交把中文「发单据进 ERP」改成「发送单据至 ERP」，之后菜单没重发；
英文那条是 11-08 d838ea3da 改按钮标签。三条都是「词条改了、部署不发」——与 ris 是同一个病，不是跨产品串词条。

### 测试环境实测（11-28，测试机1 WSL，线上镜像 4cf9c586…= 568f4b7f6 + 本分支实时源码）

主会话批：不切域名、不连生产，只 `testenv.sh up` + `live`，三个测试站全程指向线上；每个产品做完 `down`。副本对 LINE 的读写打的是真实测试官方号。

| 站点 | 做了什么 | 结果 |
|---|---|---|
| testdms 副本 | check | 落后 5 套（en/ja/th/zh query + th basic）= 副本导入时的旧编号（生产 11-26 已重发）；默认菜单 ok；`dms.test.localhost` 无凭据跳过并列出；退出码 1 |
| testdms 副本 | sync（主会话批一次，副本调度暂停） | PLAN 前的版本：BEFORE 打出 → 5 套全部复用 LINE 上已有菜单（没新建）→ 副本配置与生产 11-26 编号逐键相同 → 回读 ok；再 check 退出码 0 |
| testerp 副本 | check + sync | 60 套全部一致，sync 不发布，退出码 0；两个空白站点无凭据跳过 |
| testcowork 副本 | check | 副本配置已是新版（之前有窗口在副本里发布过），一致；**另在进程内存里把 12 个键换成生产的旧编号再 check（只读）**：12 套逐项列出与线上对照表一致（图都 same，中文录入格 label + displayText、英文录入格 label、泰文盘点格 label） |

副作用（已处理、已登记共享笔记）：DMS 副本当时调度没暂停，`up` 后 1 分钟内菜单对账按副本旧编号给测试官方号两位用户重挂；线上 11:30 对账已挂回，11:42 回读两人与线上绑定一致。
修法单独一个提交：`run.sh` 起栈 / 同步源码 / 导入前给 `*.pearnly.com` 副本写 `pause_scheduler`（`PEARNLY_TESTENV_SCHEDULER=on` 才开），测试机上已实测打出「定时任务：暂停」。
没测到：真实发布 + 重挂回读的完整一遍（三个副本都没有能真发布的漂移，主会话定 testcowork 不做真 sync）——离线测试覆盖，第一次真实发布是下一次部署（erpcowork / testcowork 的 12 套）。

### code-review（medium）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 3 | 3（1 重要：从没发布的站点会被部署抢先发布、DMS 换默认菜单；2 小：声明丢了回读静默通过、测试租户切在测试机时生产照样同步） | 2（第 3 条只记限制，见下） | 约 5 分钟 |
| 第二轮（8554cca54..HEAD）4 | 4（2 重要：本功能上线前的镜像做预览会让演练失败、权限收回后旧菜单被摘会误判「丢了」让部署失败；2 小：副本调度默认暂停改变了别的窗口的测试习惯、ID 按前 8 位比） | 3（调度默认暂停是有意的，已写进文档与共享笔记） | 约 10 分钟 |

另外自查修掉：部署回读留下的假身份 LINE 回 400 userId 会让整站失败（改为跳过计数）；预览容器补上网页容器的 DNS 设置；预览两个函数原本被定义在 `config_gate` 里面（没地图的提交会找不到函数）。

### 没做 / 限制

- 演练预览按**线上正在跑**的代码算；本批改了菜单时，真部署按新代码算，可能比预览多（预览里写明）。
- 测试租户 lab-start 切在测试机时，生产那份站点仍会按生产的绑定排重挂（同一个频道）；菜单内容相同，只是挂载按生产的绑定。切在测试机时部署要知道这一点。
- 三个 publish 仍不删旧菜单（LINE 每频道菜单数上限未核；erpcowork 现 52 个、testerp 80 个）。

## 做到哪 / 下一步

- 11-26：盘点、设计、线上只读现状脚本（11-26 主会话跑过：ERP 7 站、DMS 2 站全部一致；Cowork erpcowork / testcowork 各落后 12 套；ccdemo 无凭据跳过）。
- 11-28：设计 1–5 的代码、离线测试、闸门、部署说明（`docs/development/verification.md` 部署接线第 10 项）。
- 待：Cowork 12 套对照表与补发（主会话批准）；三个测试站实测（check 与 sync 各一次，脚本在 `~/pearnly-devtools/prod-ro-1128-menu/`，主会话排队）；第 9 项映射回读（后续）。
