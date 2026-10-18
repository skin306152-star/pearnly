# scripts/

本目录已有样板运行、开发重载、翻译与业务验证等脚本。先读选中脚本及其调用入口，再按 [验证规范](../docs/development/verification.md) 使用。

| 用途 | 入口 | 边界 |
| --- | --- | --- |
| 样板栈 | `stack.sh` | `ps` / `config --quiet` 用于检查；`up`、`down`、`restart` 会改变运行环境 |
| 本地代码重载 | `dev-reload.sh` | 选择运行组；`--sites` 不会隔离同组服务重启，`--migrate` 会修改数据库 |
| ERP 前端快速迭代 | `stocktake-preview.sh` | `up`/`down`/`reload`/`assets` 只动本任务的预览与验收 lane；`faithful` 会重建镜像并升级验收 lane |
| 术语表校验 | `i18n-build.py --check` | 不写文件；不带 `--check` 会生成语言包 |
| Frappe 文案检查 | `check-i18n.py --strict` | 静态检查，有发现则失败 |
| 前端翻译键检查 | `check-frontend-i18n.py --strict` | 静态检查，不代替界面语言验收 |
| CC 前端标准闸门 | `check-frontend-tokens.py [--report]` | 静态检查：`frontend/assistant` 组件不写死颜色 / 圆角 / 阴影（R1–R3，全仓失败）；尺寸、间距、字号、层级、动效等 R4–R15 只在 `frontend-tokens-strict.txt`（已完成清单，不是豁免清单）里的文件失败，范围外只报数（[标准化记录](../docs/work/2026-10-17-cc-frontend-standards.md) 第 3 节）；不代替截图核对 |
| CC 画面比对 | `cc-visual/shoot.mjs` → `cc-visual/compare.mjs` | 离线：本机假服务端（`cc-visual/server.mjs`，假数据、不连真实服务）+ 本机无界面 Chrome，按 `cc-visual/scenes.mjs` 的 7 种环境 × 浅深色 × 场景对两个构建产物各取计算样式快照与截图，逐项 / 逐像素比；产物用 `vite build --outDir <临时目录>` 构建，不写进仓库；有差异退出码 1。做法见记录 5.2 |
| Cowork 录入演示数据 | `sample-cowork-intake-demo.py` | 只写本地样板 `cowork-a.localhost`：建原件与识别记录（固定识别结果，不调模型、不计费），请求键固定、重跑幂等 |
| 定向业务验证 | `sample-verify-*` | 有离线、本地写入、外部读取和外部真写，不能通配符全跑 |
| Windows 测试环境 | `testenv.sh` | `sync` / `config` / `check` / `smoke` / `urls` 只读；`up`、`sites`、`import`、`migrate`、`reset`、`live` / `live-off` 会改测试环境；`lab-start` / `lab-stop` 会**切换测试租户域名在线上与测试环境之间的指向**，见 [测试环境](../docs/development/test-environment.md) |
| 测试租户域名切换 | `testenv-cloudflare.sh` | 只改 Cloudflare 上 `testdms` / `testerp` / `testcowork` 的解析记录；读取本机 `~/.config/pearnly-testenv/` 凭据，不碰其它记录 |
| 发票 CPU 测量（只在测试环境） | `testenv-invoice-cpu.py` | 在测试栈站点上真的建原件 / 识别任务单 / 票作压测数据；Cloud Run 换成已有结果、扣费直接回成功、不投递识别队列，**须先停识别进程**；不调谷歌、不扣费 |
| 回放压测开关（只给测试租户） | `ocr-replay.py` | 在站点所在 bench 里 `--site test<app>.pearnly.com on|off|status`，改站点配置里的回放开关：开着时识别交回以前的真结果、不调谷歌、识别费 0；站点名不是 `testcowork` / `testerp` / `testdms` 系列直接拒绝，`check-architecture.py` 拦误开；在该租户没有在途识别时切 |
| 简式税票拆税回放（离线、只读） | `cowork-vat-included-replay.py <目录> [--all] [--json]` | 递归读目录里已存的识别结果 JSON / JSONL（`results.jsonl`、识别记录 `pages`、`PipelineResult`），列出含税价拆税规则会改的票与改前改后数；不连站点、不调模型、不写文件，输入格式见文件头 |
| 识别金标评测（只在生产机一次性容器） | `eval-invoices.py` | `run` 按真实入口提交、真实识别、按页计费；`gold` / `subset` / `l1` 在已有结果上零费用打分；用法与闸门见 [识别改动上线闸门](../docs/development/verification.md#识别改动上线闸门) |
| 数字会计员两层回放（离线、只读、零费用） | `da-replay.py inventory / judge / gate / report` | 只读语料答案（Express 原表副本）、测试机本地测试账套的 ISPRD 与金标已有识别结果，导入推送同一套纯函数判 A / B / C、计分、比三套阈值；不连站点、不调识别与模型、不写账套，拒绝 `\\` / `S:` 开头的路径；语料只在测试机，源码复制到 WSL `/tmp` 跑、跑完删；用法见文件头 |
| DMS LINE 图文菜单画图 | `draw-dms-line-menu-originals.py` → `render-dms-line-menus.py` | 离线、幂等：前者重画泰文原版 `rich-{basic,query}.png` 与菜单 6 图标，后者据此生成 en / zh / ja 版；改完 `dms.card.rich.*` 词条后按此顺序重跑，再重新发布菜单 |

Compose project 默认固定为 `pearnly-sample`。工作树独立不代表运行环境独立，执行前核对 context、project、Site、卷与源码挂载；不要接管其他任务正在使用的栈。

`sample-verify-dms-booking-real.sh` 会真实建 DMS 订车单，其他脚本也可能发送消息、调用付费服务或修改测试站点。历史执行记录不构成本次授权。

新脚本放在本目录或合适子目录，注明用途、参数、前提、副作用和失败退出条件。安装、升级、备份恢复围绕 Frappe 原生命令实现；不使用默认生产凭据，不把清空数据当作普通验证前置步骤。
