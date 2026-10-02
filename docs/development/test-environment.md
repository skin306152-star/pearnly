# 真实环境验证：Windows 测试环境

**结论先行**：需要"真实环境验证"的改动，先在这台隔离局域网里的 Windows 上由用户试用，通过了再合并、推送、部署。
底座是**线上正在用的同一份镜像**，产品代码是**被测工作树的实时源码**（用户 2026-09-27 定，三个产品同一规则：
改了马上能试，不再每改一次就构建镜像）；验证站点用**测试租户**，不占用正式服务器资源。

## 它是什么

- 一台隔离局域网里的 Windows 机器，跑 WSL2 + Ubuntu 24.04（x86_64），与正式服务器同架构。
  Mac 是 arm64，跑不了线上镜像，所以这台机器是唯一能"用线上镜像真跑一遍"的本地环境。
- 栈定义**只有一份**：[`production/compose.yml`](../../infrastructure/production/compose.yml)
  原样 + [`testenv/override.yml`](../../infrastructure/testenv/override.yml)（只换数据卷名、挂测试脚本）。
  线上 compose 改了，这里自动跟着改，不存在第二份需要同步的栈描述。
- 站点：三个产品的空白站点（`dms|erp|cowork.test.localhost`），以及三个**整站导入的测试租户**
  `testdms.pearnly.com`、`testerp.pearnly.com`、`testcowork.pearnly.com`（数据库、文件、`site_config.json` 与线上一致，
  包括 `encryption_key` 与各渠道凭据）。
- 操作入口：[`scripts/testenv.sh`](../../scripts/testenv.sh)（Mac 侧）、
  [`infrastructure/testenv/`](../../infrastructure/testenv/README.md)（设备侧）。

## 每次开发的流程（实时源码）

1. 第一次（或换了底座镜像）：用**线上正在跑的 digest** 起栈，并挂上被测工作树的代码（**一次只起一个产品**）：
   ```bash
   export PEARNLY_TESTENV_SRC=/Users/skin/.codex/worktrees/<被测分支工作树>
   export PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:…   # 线上 digest
   bash scripts/testenv.sh lab-start cowork     # 起栈 →（首次才导入租户）→ 挂实时源码 → 切域名
   ```
2. 之后每改一处：本地快循环（语法、单测、`scripts/check-architecture.py`）→
   `PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh live cowork`（约 13 秒；表结构 / 补丁 / hooks 变了自动迁移，约 35 秒）
   → 用户刷新页面就是新代码。**不用推分支、不用构建镜像。**
3. 用户试用没问题 → 按主线规则合入 main（本地同名闸门、分支 CI 通过、快进、推送）→ 从 main 触发 `build-production.yml`
   → 部署生产（每一步仍需用户同意）。生产只跑 main 构建出来的镜像。
4. 实时源码盖不到的改动，要构建镜像再用新 digest `lab-start`：依赖 / Dockerfile / Frappe 底座版本、`frontend/` 的前端 SPA
   （它是构建产物；实时同步时镜像里的那份原样保留）。要单独验"镜像本身"：`bash scripts/testenv.sh live-off <app>`。

实时源码怎么做到的：`live` 把工作树里 5 个自有 App（`git ls-files`，含未提交改动）打包送到测试机
`/opt/pearnly-testenv/live/<app>/`，原地同步后以只读挂载盖在镜像的 App 目录上（`/opt/pearnly-testenv/live-<app>.yml`，
挂到本产品全部应用容器），再重启这些容器、清缓存。镜像里仓库不跟踪的构建产物（前端 SPA）从镜像补回，不会被挂载盖没。

## 一个产品一套栈

DMS / ERP / Cowork 各有**自己的栈**：项目名 `pearnly-testenv-<app>`，各自独立占有一套
MariaDB、两个 Redis、站点卷、网络与容器。这样测一个产品不会碰到另一个——改坏 DMS 不影响
ERP，两边的库、队列、缓存都不共享。

- **同时只跑一个**：端口是固定分配的（前端 8183/8182/8181、账本 8184），所以 `up <app>`
  会先停掉别的测试栈（容器删掉、**卷保留**，数据不丢，下次 `up` 就回来）。
- **驱动与被测代码分开**：驱动、测试辅助脚本、这份文档取本分支；产品代码与
  `production/compose.yml` 取 `PEARNLY_TESTENV_SRC`（默认当前工作树）。所以在哪个窗口跑
  脚本都不会串，也不必先把工具改动合进被测分支。
- **Keycloak 不在每套栈里**：测试租户的身份走线上 Keycloak（`pearnly_oidc_issuer` 指向
  `auth.pearnly.com`）。只有要登那些空白 `<app>.test.localhost` 站点时才
  `--profile keycloak` 起一个。

## 开始测试前自动完成的事

换镜像之后有三件事过去靠人记，现在由 `up` / `finish` 自动做（实现见
[`finish-start.py`](../../infrastructure/testenv/finish-start.py)）；这三件漏掉任何一件，
用户看到的现象都不是"环境没配好"，而是像产品坏了：

| 动作 | 漏了会看到 |
| --- | --- |
| `bench migrate`（把站点库升到当前镜像的 DocType） | 点按钮报 `Table 'tab<新 DocType>' doesn't exist` |
| `bench clear-cache`（清 Frappe hooks/资源缓存） | "新功能看不见"——缓存里存着旧的资源版本号，页面一直加载旧 JS |
| 写 `pearnly_ocr_budget_redis` 与队列超时 | LINE 发照片在入队处就失败，回"识别队列繁忙，请稍后重发" |

`lab-start` 在导入数据之后会再跑一次收尾：导入是全新的库，起栈时那次升级没有覆盖到它。

## 一键切换（把测试租户整体切到测试环境）

```bash
bash scripts/testenv.sh lab-start dms   # 拉镜像（测试机没有才拉）→ 起 DMS 栈 →（首次或要求刷新才导出导入）→ 切域名
bash scripts/testenv.sh lab-stop  dms   # 域名切回线上 → 停 DMS 栈（卷保留）
bash scripts/testenv.sh lab-start erp   # 换成 ERP（会先停掉 DMS 栈）
```

**按产品切换**：`lab-start` / `lab-stop` 带要测的产品名（`dms`、`erp`、`cowork`；不带默认 `dms`）。
只切一个产品时，其它产品的域名完全不动：它们仍走通配记录、仍由线上服务。

**线上那份测试租户的定时任务**（2026-09-27 起）：`lab-start` 切完域名后把**线上**同名测试租户
（如 `testdms.pearnly.com`）的调度暂停，`lab-stop` 切回时恢复；正式租户不碰。原因：切域名只转走流量，
线上那份的定时任务照跑——DMS 实测它每 10 分钟按旧配置把 LINE 图文菜单挂回去，并用同一个第三方账号
登录做回读与补救，菜单、同步和并发的测试结果因此不是被测代码的。`lab-stop` 没跑完（中断、断网）时，
用 `bench --site <测试租户> scheduler status` 在线上核对，必要时手动 `scheduler resume`。

## 复用：同一套环境反复用（每次只需挑一条）

环境是**一次性配好、之后重复用**的：站点、测试租户数据、产品与镜像都落在各产品自己的
卷里（`down` 只停容器、**卷保留**），所以第二次开始不再做任何设备侧准备。

| 这次想干什么 | 一条命令 | 说明 |
| --- | --- | --- |
| 同一镜像、同一个产品，再测一遍 | `lab-start dms` | 最快：镜像已在不再拉，栈起来时自动收尾，租户数据复用，然后切域名 |
| 同一个产品，**换成新镜像**（发版后复验） | `PEARNLY_TESTENV_IMAGE=<新 digest> lab-start dms` | 拉新镜像 → 起栈 → 自动 `migrate`/`clear-cache`/写运行配置 → 切域名；已导入的租户数据原样复用并升级 |
| 要**线上最新的租户数据** | `PEARNLY_TESTENV_REFRESH=1 lab-start dms` | 线上导出到本次专用目录 → 只取这一组 → 导入租户与账本 → 收尾 → 切域名；测试机上原来的该租户数据被覆盖 |
| 换成**另一个产品** | `lab-start erp` | 会先停掉 dms 栈（容器删、卷保留），再起 erp；两个产品的域名互不影响 |
| 测完**切回线上** | `lab-stop dms` | 域名回落通配记录；测试环境数据保留，下次接着用 |
| 想**推倒重来**（数据也清掉） | 先 `lab-stop <产品>`，再 `reset` | `reset` 删的是 `pearnly-testenv*` 的卷；**会连导入的测试租户数据一起删掉**，之后要重新 `lab-start` 导入 |
| 只想知道现在什么状态 | `ps` / `sites` / `check` / `urls` | 只读，不动任何东西 |

几个重复用时最容易忘的点：

- **驱动与被测代码是两个来源**：`scripts/testenv.sh`、`infrastructure/testenv/*`、这份文档取
  **当前工作树**（按产品分栈的驱动已随 `a1f6488` 合进 `main`，任何基于 main 的工作树都能跑）；
  产品代码与 `production/compose.yml` 取 `PEARNLY_TESTENV_SRC`（默认当前工作树）。在哪个窗口跑都不会串。
- **一次只跑一个产品**：端口是固定分配的（前端 8183/8182/8181、账本 8184），所以换产品必然
  要停掉上一个；这是有意的，避免两个产品抢同一批端口与宿主 nginx 规则。
- **镜像必须钉 digest**（`…@sha256:…`）：`pull` 只接受钉死的 digest，避免"以为测的是这版、
  其实拉到的是别的标签"。
- 产品栈与设备侧的准备是分开的：`setup` 只在**换设备/重装 WSL** 时才跑；日常复用不需要它。
- **dms** 与 **erp** 两套都真跑过（erp 于 2026-09-25 第一次起，见
  [ERP 测试环境记录](../work/2026-09-25-erp-line-intake.md)）；第一次 `lab-start <产品>` 会建卷并导入，
  遇到问题按"出问题怎么查"一节处理，
  不要当成"流程坏了"。

底层是 [`testenv-cloudflare.sh`](../../scripts/testenv-cloudflare.sh) 的 `switch-lab` / `switch-prod`
（同样接受产品名，并可 `status [dms] [erp] [cowork]` 只读看某一个）。隧道本身同时带三个域名，
所以"切哪个产品"只是"给哪个域名建/删自己的 CNAME"，不需要改隧道配置。
注意三个产品共用同一个测试租户数据导出通道，只切一个产品时不会顺带刷新另一个产品的数据。

- 切换改的是 Cloudflare 解析：这三个名字平时走通配记录 `*.pearnly.com → 线上 IP`；
  切过去时给被切的产品建 `CNAME → 隧道`，切回来时删除，自动回落到通配记录。
- **域名不变**，所以 LINE webhook、OIDC 登录、第三方 ERP 推送地址都不需要改：
  登录仍走线上 Keycloak 与同一批测试账号。
- 影响范围只有这几个测试租户；其它租户完全不受影响。
  切换期间被切的租户由测试机提供服务，线上容器照常在跑，只是没有流量。
- 明细开关（排查时用）：`bash scripts/testenv-cloudflare.sh status|switch-lab|switch-prod`。

## 当前状态与交接（2026-09-26）

基建（WSL、docker、隧道、工具目录、镜像）一直在；两个产品的栈都**停着**，域名都**在线上**。下一个窗口不需要任何设备侧准备：

| 产品 | 数据卷 | 测试租户 | 下次 `lab-start` |
| --- | --- | --- | --- |
| DMS | 09-25 按用户要求清零（见 [Windows 测试环境记录](../work/2026-09-24-windows-testenv.md)） | `testdms.pearnly.com` | 建卷并从线上重新导出导入（首次较慢） |
| ERP | **保留**（用户 09-26 定）：`pearnly-testenv-erp_{database,queue,sites-erp,sites-billing}` | `testerp.pearnly.com`（09-25 导入 + 09-26 测试数据） | 复用，约 2 分钟；要线上最新数据加 `PEARNLY_TESTENV_REFRESH=1` |
| Cowork | `pearnly-testenv-cowork_{database,queue,sites-cowork,sites-billing}`（09-26 导入） | `testcowork.pearnly.com`（09-26 导入，镜像 `02a3b22` `sha256:3a1d5f26…`） | 复用；要线上最新数据加 `PEARNLY_TESTENV_REFRESH=1` |

ERP 上次跑的是已上线的 `a1f6488`（`sha256:b7c07a7f…`）。

ERP 这套的外部状态（下次切过去就生效，不用再配）：

- LINE 官方号 ERP-TEST（`@063eadty`）Webhook 指向 `https://testerp.pearnly.com/api/method/pearnly_core.line.webhook`，
  域名跟着切：切到测试机时 LINE 打到测试机，切回时打到线上。
- 富菜单 28 套（4 语言 × 7 权限组合）线上与测试机两边站点配置指向同一批（内容散列命名，重复发布复用，不新建）。
- 测试机账本是整站导入的副本（测试租户余额与线上不同步）；识别扣费扣的是这份副本。
- OCR 是线上真凭据：经 LINE 发单据会产生真实付费调用，只用于人工验收。

复用一条命令（在任意基于 main 的工作树里跑）：

```bash
PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:<被测镜像> \
PEARNLY_TESTENV_SRC=<被测分支工作树> bash scripts/testenv.sh lab-start erp   # 约 2 分钟：起栈 → 升级库/清缓存 → 切域名
bash scripts/testenv.sh lab-stop erp                                          # 测完切回线上、停栈、卷保留
```

**测试机可以关机**，前提是三个测试域名都已切回线上（`bash scripts/testenv-cloudflare.sh status` 没有 CNAME 记录）：
关机不影响线上任何租户；数据在卷里，开机后 Windows SSH（自动启动）、WSL 的 docker / 隧道 / nginx（systemd 开机自启）
都会在下一次 `lab-start` 远程拉起 WSL 时自己起来。**切在测试机上时关机**，被切过去的那个测试租户会整体打不开，
所以关机前先 `lab-stop`。

## Cowork：测试租户 `testcowork.pearnly.com`（2026-09-26 起与 DMS / ERP 同一套做法）

线上已有 Cowork 测试租户 `testcowork.pearnly.com`（ERP 窗口 2026-09-26 开通并升到 `15dea26`），所以 Cowork 与
`testdms` / `testerp` 一样：整站导入 + 切域名，登录走线上 Keycloak 与同一批测试账号。

```bash
export PEARNLY_TESTENV_SRC=/Users/skin/.codex/worktrees/<被测分支工作树>
export PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:…
bash scripts/testenv.sh lab-start cowork   # 起 Cowork 栈 →（首次或要求刷新）导入 testcowork + 账本 → 收尾 → 切 testcowork 域名
bash scripts/testenv.sh lab-stop cowork    # testcowork 切回线上 → 停栈（卷保留）
```

- 隧道入口加了 `testcowork.pearnly.com → 127.0.0.1:8181`（`testenv-cloudflare.sh tunnel-create` 已更新线上隧道配置）；
  切换只动 `testcowork` 一条 CNAME，DMS / ERP 域名不受影响。改完解析后几分钟内才全部生效，期间请求可能仍落到线上。
- 第一次（2026-09-26）遇到的坑：Cowork 栈的账本卷里原来有一个实验室自建的 `billing.internal`（下面旧做法留下的），没有
  导入标记，导入脚本拒绝覆盖。已删掉该实验室账本站与其数据库后重新导入。今后 Cowork 栈的账本都是线上账本的副本。
- 空白站点 `cowork.test.localhost` 仍在栈里（`lab-init cowork` 的旧做法：本地密码登录、本栈自建账本），
  只在需要不接统一身份的空白站点时用；它现在与导入的账本共用一个账本站，`lab-init` 会往里登记 `T-TESTCOWORK`。
- 与线上不同 / 边界：OCR 读的是测试机 `/run/secrets/ocr.json`（线上真凭据，识别是**真实付费调用**，只在人工验收时用）；
  账本是导入时的副本，扣费不回写线上；Cowork 独立账号池 `pearnly-cowork` 还没在线上建，`testcowork` 目前仍签到业务账号池。
- **Cowork 识别的 Cloud Run 地址（2026-10-02 发现）**：测试机这份 `testcowork` 的 `pearnly_cowork_ocr_remote.url` 仍是
  `https://cowork-ocr-test-…a.run.app`，这个 Cloud Run 服务已经不存在（404），所以上传在转发 Cloud Run 那一步就失败
  （`RemoteError: remote_http_404`）。要在测试机上真实上传 / 识别，先补一个测试用 Cloud Run 服务，或临时把 url 指向生产
  `cowork-ocr` 服务并**同时**设 `pearnly_ocr_paused=1`（只验界面、服务端受理即拒、不调谷歌），验完改回、关暂停。
  2026-10-02 统一点页就是这样做的。
- **充值也只进测试账本**：切在测试机时，租户页面上的充值申请落在测试机账本副本（`billing.internal` 的 `Pearnly Topup Request`），
  本机管理后台读的是线上账本、看不到它。只为测试时在测试账本里审核（`billing_api.approve_local`，备注写明测试）；
  真实转账要切回线上后在线上再提交。2026-09-27：`TU-2026-00073`（testcowork，1000 泰铢）按用户要求在测试账本审核通过。

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `bash scripts/testenv.sh setup` | 设备侧一次性准备（WSL 内装 docker / nginx / cloudflared） |
| `bash scripts/testenv.sh up [dms\|erp\|cowork]` | 拉起该产品的独立栈（先停别的栈），并自动升级库 / 清缓存 / 写运行配置 |
| `bash scripts/testenv.sh finish [产品]` | 只跑那三个收尾动作（导入数据后、换镜像后单独用） |
| `bash scripts/testenv.sh sites [产品]` | 只建/迁移本栈的空白站点 |
| `bash scripts/testenv.sh lab-init cowork` | Cowork 空白站点配成可用（本栈账本、功能、管理员），见上节 |
| `bash scripts/testenv.sh import <组> <站点>` | 导入某个测试租户（数据来源于线上导出） |
| `bash scripts/testenv.sh migrate <组> <站点>` | 发版后升级某个站点 |
| `bash scripts/testenv.sh check` | 读回每个站点的产品、已装 App、版本、数据量 |
| `bash scripts/testenv.sh smoke` | 入口冒烟（按 Host 头访问各站点） |
| `bash scripts/testenv.sh urls` | 打印 Mac 侧端口转发与可访问地址 |
| `bash scripts/testenv.sh logs <服务>` / `down` / `reset` | 看日志 / 停栈（保留数据）/ 删测试环境数据卷 |
| `bash scripts/testenv.sh lab-start [dms\|erp\|cowork]` / `lab-stop [dms\|erp\|cowork]` | 见上"一键切换"；可只切一个产品 |
| `PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh live [dms\|erp\|cowork]` | 把工作树当前代码同步到测试机并生效（实时源码，约 13 秒） |
| `bash scripts/testenv.sh live-off [dms\|erp\|cowork]` | 摘掉实时源码，回到纯镜像 |
| `bash scripts/testenv-cloudflare.sh status` | 只读：这两个测试域名现在指向线上还是测试机 |

## 凭据与边界

- Cloudflare API token 与隧道运行令牌只存在**本机** `~/.config/pearnly-testenv/`（权限 600），
  设备侧令牌在测试机 `/opt/pearnly-testenv/`；都不进仓库、不写进文档。
- 测试机数据库与本地 Keycloak 的口令在 `~/.config/pearnly-testenv/testenv-secrets`（600）。
  驱动只认这一份（工作树根的 `.testenv-secrets` 是旧位置，仍兼容），两处都没有才生成；
  已有数据卷时换一份口令会让数据库对不上，所以不要删它。
- 测试环境不接正式账本（账本站是整站导入的副本）、LINE 用测试租户自己的频道。
- **OCR 是真凭据**：测试机 `/opt/pearnly/secrets/ocr.json` 自 2026-09-24 起是线上那份
  （DMS 验证识别时放入），所以在测试环境经 LINE 发单据会产生**真实付费调用**。只用于人工验收，
  不跑会自动调用 OCR 的脚本；要回到零费用就换回空占位。
- 测试机上另有一个**空的** Keycloak（仅本地），不代表线上身份；身份以线上为准。
- **DMS 切到测试机后先在测试机发布一次图文菜单**（`rich_menu.publish`）。测试机与生产共用同一个 LINE 官方号，测试机的库是旧副本、
  记的是旧菜单 ID：不先发布，用户一发消息测试机就把旧菜单挂回他手机上（2026-10-03 少了第 6 格）。发布按内容命名，和生产内容相同时
  直接复用同一套菜单，切回生产后不会再变。
- **切完 DNS 先确认请求真的到了测试机**再开始点：浏览器可能沿用到生产 IP 的旧连接（2026-10-02 ERP 导出点到了生产旧版）。
  看测试机前端日志有这次请求、生产计时日志没有，或用只在测试机生效的配置（例如导出门槛）判断。
- **生产上（含 testdms / testerp / testcowork 这类测试租户）的剖析、冒烟脚本，不得绑定、解绑或改写任何真实操作员、
  真实 LINE 身份**。要用身份就用专门建的测试操作员账号，并事先告诉压测窗口。原因：`line_binding.bind` 规定
  一个账号只能有一个有效 LINE，新绑会顶掉旧的并触发图文菜单同步——2026-10-03 02:47（曼谷）一次剖析把临时
  LINE 身份绑到用户自己的操作员账号上，用户的 LINE 被解绑、菜单退成默认 3+2 版，随后按原账号重新绑定恢复。
  动手前先读要调用的共享写函数（绑定、会话、菜单、票据）的副作用，测完回读真实身份没有被改。

## 它证明不了什么

- WSL2 的内核/网络栈与正式服务器不同；宿主 TLS 终结、生产并发与容量不能在这里验。
- 真机验收必须在切换状态下由人执行：手机 HTTPS 摄像头、真实 LINE 消息送达、
  真实第三方 ERP 落单。健康检查、HTTP 200、页面能打开都不算业务验收。
- 切换期间这两个域名的 certbot 自动续期会跳过（切回后恢复）；证书本身是免费的自动续期。

## 出问题怎么查

```bash
# 隧道服务与日志（设备侧）
ssh lenovo-office-lan 'wsl -d Ubuntu-24.04 -u root -- systemctl status pearnly-tunnel'
bash scripts/testenv.sh logs dms-frontend        # 看请求是否真的落在测试机
bash scripts/testenv-cloudflare.sh status        # 看解析当前指向谁
```

- 页面打不开但端口能通：确认 WSL 是 `networkingMode=mirrored`（`C:\Users\codexops\.wslconfig`）。
- 隧道 530/502：多半是隧道刚起或后端未就绪，重试并看 `dms-frontend` 访问日志。
- Mac 想绕过域名直连：`bash scripts/testenv.sh urls`。

## 归属与清理

- Cloudflare：命名隧道 `pearnly-testenv`（id `875e14d3-ac1b-4ff5-860e-2533f09dbd5a`），
  DNS 记录由脚本建删，不手工改。
- 设备：systemd 单元 `pearnly-tunnel`、目录 `/opt/pearnly-testenv/`、WSL 发行版 `Ubuntu-24.04`。
- 不再需要这套环境时：`lab-stop` → 删隧道与记录 → 停用并删除设备单元与目录，并在
  [本任务记录](../work/2026-09-24-windows-testenv.md) 里登记清理结果。
