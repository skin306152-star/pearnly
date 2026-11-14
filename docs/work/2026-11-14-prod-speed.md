# 线上访问速度 11-14：翻译词条、安装包走 R2、bbr 登记、逐页对照、全站边缘方案

接 `docs/work/2026-11-13-prod-speed.md`。分开报：实现 / 本地与实验室 / 部署 / 线上实测。测量脚本与原始数据在 `~/pearnly-devtools/prod-speed/`。

## 1. 翻译词条瘦身（分支 `pearnly/boot-messages-cache`）

做法见 `apps/pearnly_core/pearnly_core/desk/boot_messages.py` 文件头：页面里不再内联整份翻译表，只带语言、指纹和 Frappe 加载时就要用的
29 条译文；整表经 `messages?lang=&v=` 单独取，指纹对上 `public, max-age=31536000, immutable`。

实验室（本机 `pearnly-msgs-lab`，线上同版镜像 + 本分支源码）实测：

| 检查 | 改前（11-13 分支原样） | 改后 |
| --- | --- | --- |
| Frappe 加载时就翻译、却拿到原文的文字（ERP 各页） | 泰 18 / 中 17 / 日 2 / 英 0 | 0 |
| 同上，Cowork 8 页、DMS 2 页 × 四语言 | — | 0 |
| 整表条数（泰 / 中 / 英 / 日，ERP） | — | 10610 / 10583 / 4611 / 4819 |
| `__("Save")` | — | บันทึก / 保存 / Save / 保存（换语言即换表） |
| 词表请求 | — | 200，`public, max-age=31536000, immutable` |
| 60KB/s 慢网、禁缓存（整页 150 秒）下早期调用清单 | 29 条 | 同样 29 条 |

丢的那批：列表排序与筛选的「创建于 / 最后更新日期 / 最后更新人」、标准字段名（编号、创建人、标签、评论、单据状态、父级…）、「搜索」、
键盘快捷键说明、相机控件、数字缩写。原因：它们是 Frappe 打包脚本里的模块常量，在本 App 脚本（`messages.js`）之前求值。

守门：流程 `erp.desk.boot-messages`、`cowork.desk.boot-messages`（`scripts/flows/desk_boot_messages.py`）在 Frappe 脚本执行前挂钩 `__()`，
记下早期调用当时拿到的结果，有一条「整表有译文、当时拿到原文」就不通过。故意弄坏演练：把早期译文清空 → 流程判不通过并列出 17 条。

另：影响清单（`scripts/check-impact.py`，改到 hooks.py 命中 10 个入口）逐个记：

| 入口 | 结果 | 证据 |
| --- | --- | --- |
| shared.navigation 侧栏与菜单（三产品） | 通过 | 实验室 ERP 19 页 / Cowork 8 页 / DMS 2 页 × 四语言逐页打开，文字是当前语言、无新报错（ERP 库存卡页 2 个页面报错在 main 上同样有） |
| shared.browser-text 共用外壳 | 通过 | 同上；流程 erp/cowork.desk.boot-messages |
| shared.exports 导出 | 通过 | 流程 erp.exports、cowork.exports（两套数据 × 两种宽度） |
| shared.upload.shrink-photo 上传压缩 | 通过 | 流程 shared.upload.take-photo |
| shared.assistant 对话页外壳 | 通过 | 流程 cc.chat、cc.attach-delete（CC 网页不读 Desk 启动信息，不受影响） |
| shared.execution 登录会话等 | 通过（实验室会话） | 流程 erp.home.billing、erp.profile.save、erp.page-lists |
| shared.login OIDC 登录 / 退出 | 没测到 | 实验室不走 Keycloak；改动只在 Desk 页面加载之后，不碰登录链路；上线后登录 e2e 35 项照跑 |
| shared.line.login LINE 登录 | 没测到 | 同上，不碰 LINE 链路；上线后真机按发布清单 |
| erp.cc.connect CC 桌面版连接 | 没测到 | CC 桌面版只走机器接口，不打开 Desk 页面；测试机未动 |
| shared.originals.download 原件 | 没测到 | 不碰原件链路；上线后按发布清单 |

闸门：本地全闸门 242 步全过；生产镜像候选离线 111 组 `LANE EXIT 0`；流程 10 条 30/30 通过；故意弄坏演练拦住。

## 2. CC 桌面版安装包走 R2

- 更新器核对：旧仓库 `pearnly-companion`（唯一一次提交 updater.py，6-23）与本仓 `companion/src/companion/updater.py` 全部 6 个版本，
  `download_installer` 都是 `httpx.Client(follow_redirects=True)`，相对地址拼 `https://pearnly.com`、不限定域名；TLS 用 certifi 根证书。
  → 已发布的所有版本都跟随 302，不用改客户端。
- 改动：`companion-release.yml` 正式版先把安装包传 R2（版本路径 + 固定名），从 `media.pearnly.com` 读回比对 sha256，只留最近 5 版，
  再交 `receive-companion.py`（本机照旧存、改 latest.json）；`landing.conf` 对两类安装包地址 302 到 R2，latest.json 不动。
  本机 nginx 容器验证：latest.json 200、版本安装包与两个固定名 302 到 R2 同路径（带原查询串）、staging 不跳、旧 `/static/companion/latest.json` 照旧。
  验证时发现正则里的 `{40}` 不加引号 nginx 起不来，已加引号。
- 费用依据（Cloudflare R2 定价页，2026-10-30 版）：免费额度存储 10 GB-月 / 月、A 类 100 万次、B 类 1000 万次，出流量免费；
  每版 154MB × 5 ≈ 0.8GB。零月费。`.exe` 在 Cloudflare 默认缓存扩展名里，免费版单文件缓存上限 512MB。
- 上线顺序（缺一步就会 404）：①用户在 Cloudflare 建 R2 令牌、在 GitHub 加三个密钥 → ②合入 main → ③下一次 CC 正式发布走新流水线（R2 上有了当前版）
  → ④再装 `landing.conf`（`publish-website.sh --nginx` 或手工 install + reload）。④之前一切照旧；回退 = 去掉两段 302。
- 客户端第二步（不在本窗口改 `companion/`，给 CC 桌面版窗口）：见下文「客户端改法与工作量」。

### 客户端改法与工作量（给 CC 桌面版窗口，经主会话；本窗口不改 `companion/`）

| 项 | 做法 | 机器时间 |
| --- | --- | --- |
| 带校验下发 | 服务端 `cc_desktop/release.py`（及 Cowork 同类入口）在 `companion_latest` 里加 `sha256`、`size`（latest.json 本来就有）；`tests/test_companion_contract.py` 同步；客户端下完比对，不符删掉重下 | 写约 30 分钟 + 契约测试 |
| 断点续传 | 临时文件改成 `%LOCALAPPDATA%\Pearnly\update\CC-Setup-<版本>-<sha256 前 12 位>.part`，有就带 `Range: bytes=<已有>-`；R2 与本机 nginx 都支持 Range（206）；下完改名再装 | 写约 1 小时 |
| 托盘进度与退避重试 | 下载线程回调已下字节 / 总大小，托盘菜单显示「正在下载新版 · 42%」；失败按 1 / 5 / 15 分钟重试，不靠重启 CC | 写约 1 小时 |
| 停滞检测 | 60 秒没有新字节就断开、按续传重连（今天的 180 秒 read timeout 只看两次收到数据的间隔） | 并入上一项 |
| 缩小安装包 | 先用 PyInstaller 产物量清 154MB 构成（预计 Qt / QtWebEngine 占大头；CC 窗口要网页内核，不能整个去掉），只去掉确实没用到的 Qt 模块；不用 UPX（杀软误报） | 量约 1 小时，改看结果 |
| 验证 | Companion checks（测试机 runner）+ 测试机1 / 2 从旧版自动升级计时、中途重启 CC 看续传、改 sha256 看拒装 | 约 1.5 小时（含等人开测试机） |

合计约半天机器时间。R2 那一步上线后办公室下载已是 1–3 分钟，这批是「中断不重来、看得到进度」，不再是速度瓶颈。

## 3. bbr 登记

`infrastructure/production/host/etc/sysctl.d/60-pearnly-bbr.conf`、`…/modules-load.d/pearnly-bbr.conf`：与 11-13 19:36 生产只读采集
（`~/pearnly-devtools/prod-speed/2026-11-13-evening/prod-1.txt` 17–19 行）逐字一致；`production-capacity.md`「本机固定」加一行（装法、回退、依据）。没有连生产。

## 4. 逐页对照（直连源站，办公室 True 线路）

脚本 `~/pearnly-devtools/prod-speed/pages2.py`（e2e 测试账号登录一次；首页清空缓存冷加载，之后每页「本次第一次打开」与「立刻再打开」；
记 load 事件秒数与本页传输量）。原始数据 `~/pearnly-devtools/prod-speed/pages-1114/`。注意：登录 11-14 起落在 CC 网页 fatcc.com。

白天 11-14 13:25（生产 6f58f0b48，翻译未瘦身）：

| 页面 | ERP 首次 / 再次 | ERP 每次传输 | Cowork 首次 / 再次 | Cowork 每次传输 |
| --- | --- | --- | --- | --- |
| 首页（缓存全空） | 0.94 s | 2716KB | 4.19 s | 2443KB |
| 首页 | 0.22 / 0.19 s | 282KB | 1.90 / 1.35 s | 320KB |
| 采购 / 录入 | 0.16 / 0.16 s | 286KB | 1.44 / 1.87 s | 320KB |
| 销售 / 工作 | 0.16 / 0.16 s | 276KB | 1.40 / 1.04 s | 314KB |
| 收款 / 银行对账 | 0.17 / 0.16 s | 280KB | 1.61 / 1.45 s | 314KB |
| 库存 / 盘点 | 0.20 / 0.17 s | 276KB | 1.34 / 1.20 s | 314KB |
| 物料、入库、盘点、收银台（ERP） | 0.16–0.23 s | 275–276KB | | |
| CC 网页（fatcc.com） | 2.59 / 3.40 s | 539KB / 12KB | 6.71 / 3.09 s | 540KB / 13KB |

结论：白天线路好时都在 2 秒内；每次整页都重下 275–313KB 的页面本身（九成是翻译表），晚上线路 30–90KB/s 时这就是 3–10 秒。
晚高峰数据见下一节。DMS（无 e2e 账号）与手机 LIFF 没测到。
