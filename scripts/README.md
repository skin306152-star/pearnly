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
| Cowork 录入演示数据 | `sample-cowork-intake-demo.py` | 只写本地样板 `cowork-a.localhost`：建原件与识别记录（固定识别结果，不调模型、不计费），请求键固定、重跑幂等 |
| 定向业务验证 | `sample-verify-*` | 有离线、本地写入、外部读取和外部真写，不能通配符全跑 |
| Windows 测试环境 | `testenv.sh` | `sync` / `config` / `check` / `smoke` / `urls` 只读；`up`、`sites`、`import`、`migrate`、`reset` 会改测试环境；`lab-start` / `lab-stop` 会**切换测试租户域名在线上与测试环境之间的指向**，见 [测试环境](../docs/development/test-environment.md) |
| 测试租户域名切换 | `testenv-cloudflare.sh` | 只改 Cloudflare 上 `testdms` / `testerp` 的解析记录；读取本机 `~/.config/pearnly-testenv/` 凭据，不碰其它记录 |
| 发票 CPU 测量（只在测试环境） | `testenv-invoice-cpu.py` | 在测试栈站点上真的建原件 / 识别任务单 / 票作压测数据；Cloud Run 换成已有结果、扣费直接回成功、不投递识别队列，**须先停识别进程**；不调谷歌、不扣费 |

Compose project 默认固定为 `pearnly-sample`。工作树独立不代表运行环境独立，执行前核对 context、project、Site、卷与源码挂载；不要接管其他任务正在使用的栈。

`sample-verify-dms-booking-real.sh` 会真实建 DMS 订车单，其他脚本也可能发送消息、调用付费服务或修改测试站点。历史执行记录不构成本次授权。

新脚本放在本目录或合适子目录，注明用途、参数、前提、副作用和失败退出条件。安装、升级、备份恢复围绕 Frappe 原生命令实现；不使用默认生产凭据，不把清空数据当作普通验证前置步骤。
