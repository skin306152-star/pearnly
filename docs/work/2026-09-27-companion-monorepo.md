# Windows 小助手并入主仓、改连新平台、安装包放我们自己的服务器（2026-09-27）

用户："小助手的改动是不是没部署上去？为什么没生效，我也无法下载"→ 排查后决定："按你的方案做，并进主仓，放我们自己服务器"。

## 排查结论（改之前）

1. 安装包原在旧云私有桶 `gs://pearnly-app-installers-112074003592`，2026-09-24 旧云下线（有授权）时连同发布身份一起删了；
   新平台的下载地址配置 `pearnly_cowork_agent_installer` 从没设过 → 网页「下载助手」只会说未配置。
2. 已发布的 v1.1.77 配对时写死连 `https://pearnly.com`（旧平台域名，现为官网静态页，接口 404），新平台的密钥它连不上。
3. 2026-09-26 用户要的「小助手精简」只存成补丁 `~/Backups/pearnly-cleanup-2026-09-26/companion-simplify/`，从未提交、构建、发布。
4. 服务端（`apps/pearnly_cowork/pearnly_cowork/companion/`）是齐的；旧地址兼容入口 `companion.wsgi` 从未挂出（新版不再需要）。

## 做了什么

| 提交 | 内容 |
| --- | --- |
| `f1b7ff39` | 原样搬入 `pearnly-companion` master `ca7f267`（v1.1.77）的 `src/companion`、测试、打包脚本、图标到 `companion/`；旧 POC、模拟服务器、bridge、旧云发布脚本与冻结整机环境不搬（去掉 1 条与 bridge 逐字比对的测试） |
| `f12bff8f` | 打上 2026-09-26 的精简补丁（账套由网页选、只显示连接人与状态、托盘状态行截断） |
| `2280978e` | 1.2.0：配对码 `exp_…@<租户域名>`，小助手连 @ 后的站点；三个机器接口改调原生 `/api/method/pearnly_cowork.companion.{heartbeat.report,queue_api.lease,queue_api.acknowledge}` 并取 `message`；查更新改到 `pearnly.com/downloads/companion/latest.json`。网页「复制」给整段配对码、字段名改「配对码」，「下载助手」默认 `https://pearnly.com/downloads/companion/PearnlyCompanion-Setup.exe`（站点配置仍可覆盖）。发布：`Companion checks`（Windows 全量测试）、`Companion release`（Windows 打包 → 受限 SSH `receive-companion.py` → 公网读回比对）、nginx `landing.conf`（新增 `/downloads/companion/`、旧版查更新的 `/static/companion/latest.json`）。约定测试 `tests/test_companion_contract.py`、接收脚本测试 `tests/test_companion_receiver.py` 进 Development checks。构建依赖精简为 `companion/packaging/requirements-build.txt`（25 个精确版本） |
| `0e52582c` | 首次在 Windows CI 上跑出的两个问题：全新账户没有 HKCU Run 键（改 `CreateKeyEx`）；Windows 时钟约 15ms 一跳，同一刻度完成的目录快照看起来不更新而被重扫（完成时间严格递增） |

5 个 Windows 专属测试（UNC 路径、Win32 文件锁）在非 Windows 上标记跳过，Windows 上照跑。

## 验证

| 层级 | 结果 |
| --- | --- |
| 本机 | 小助手测试 505 通过（36 跳过，macOS）；CI 同名闸门、`test_companion_contract`（6）、`test_companion_receiver`（5）；服务端小助手相关 8 组测试（镜像内）通过 |
| 本机端到端 | 本机样板 `cowork-a.localhost` 重新生成本地测试密钥（第 7 代，只在样板），新版客户端按配对码拆出站点 → 原生心跳 ok（`message` 正确取出）→ 领取空队列 → 错误密钥 401 |
| CI | Development checks `36279627101`、Companion checks `36279626986`（Windows 512 通过） |
| 合入 | `main` 快进到 `0e52582c`，远端回读一致 |
| 生产镜像 | Build `36279746309` → `sha256:b9e6fd11fbab7f910f9ccc2792b5d6f1fc93ad05a415229d2bdf2561c5878905`；Stage `36280031700` |
| 生产部署目录 | 差异仅新增的 `landing.conf`、`receive-companion.py`；先备份到 `/opt/pearnly/state/predeploy-0e52582c-20260926233823` 再从镜像同步，同源检查通过 |
| 生产 Cowork | `activate-product.sh … cowork` 成功（日志 `/opt/pearnly/state/activate-cowork-0e52582c.log`，激活前备份 `20260927_063853-testcowork_pearnly_com-*`，上一版 `c4a04ca8` / `sha256:38b58ff9…`）；7 容器 `0e52582c`；ping 200（切换后数秒内一次 502，随即恢复）；线上 `installer()` 回新地址，字段名「配对码」 |
| nginx | 旧配置备份 `/opt/pearnly/state/pearnly-landing.conf.before-companion-20260926233222`；`nginx -t` 通过后重载；官网 200、下载目录不列表（403） |
| 发布密钥 | 专用 ed25519，`authorized_keys` 只允许 `receive-companion.py`（原文件备份 `/opt/pearnly/state/authorized_keys.before-companion-*`）；私钥只在 GitHub 密钥 `COMPANION_PUBLISH_SSH_KEY`，本机副本已删 |
| 测试发布 | Companion release `36280195232`（staging）：Windows 测试 + 打包 1.2.0（51,660,521 字节）→ 上传 → 公网读回一致 |
| 正式发布 | 标签 `companion-v1.2.0`（`0e52582c`）→ `36280391658`：`latest.json` 1.2.0，sha256 `b60125354c46…`；固定名下载 51,660,066 字节、sha256 一致、`MZ`、`Cache-Control: no-cache`；旧地址 `/static/companion/latest.json` 同一份 |
| 未验 | Windows + Express 真机：安装、粘贴配对码配对、心跳、领取、写账套、回报；已装 v1.1.77 的电脑自动升级到 1.2.0（升级后需重新粘贴新配对码）；安装包无代码签名，下载可能提示「未知发布者」 |

## 保留与后续

- 旧仓库 `skin306152-star/pearnly-companion`：停用、不再发布，未归档（归档是对 GitHub 的外部改动，等用户决定）；
  本机克隆 `/Users/skin/Developer/Pearnly/pearnly-companion` 未动。
- `~/Backups/pearnly-cleanup-2026-09-26/companion-simplify/`：内容已进 `f12bff8f`，可由用户决定删除。
- 本机样板 `cowork-a` 的本地 Express 连接：本次重新生成了第 7 代测试密钥并有一次探针心跳（只在样板）。
