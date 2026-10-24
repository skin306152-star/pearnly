# 自动冒烟（共用件连带回归闸门）

规则见 [共用件连带回归闸门](../../docs/development/verification.md#共用件连带回归闸门)；入口登记在 [`scripts/impact/shared-parts.toml`](../impact/shared-parts.toml)。

| 文件 | 做什么 |
|---|---|
| `run.py` | 按登记表选 auto 入口（`--diff` / `--part` / `--entry` / `--all`），在实验栈（或 `--target production --readonly` 的线上测试租户）跑，出「通过 / 不通过 / 没测到」表 |
| `drive.mjs` | 本机无头 Chrome（CDP，Node 22，不要 Playwright）：带 sid 打开一页、跑探针、收页面异常；假摄像头 `--use-file-for-fake-video-capture` |
| `probes/page.js` | 页面打开：不是无权限页 / 404、主区有内容；`args.expect = "not_permitted"` 反过来核 |
| `probes/scan.js` | 扫码入口：按 `args.steps` 点到扫码，核「见过有画面」（`<video>` 有尺寸、可见、轨道在播）和读出测试码 |
| `probes/selftest.js`、`selftest.py` | 不需要站点：当前共用读码器读出假摄像头里的测试条码（CI 跑） |
| `make_barcode_video.py` | 生成测试条码画面（EAN-13 `2000000000008`，y4m，运行时生成不进仓库） |
| `session.py` | 在站点网页容器里给测试账号建 / 删 Desk 会话（不碰 Keycloak 密码） |
| `line_flow.py` | 容器里发模拟 LINE 事件，走真实分发与处理器，出站全部截住；核每条都回、三个点不在回复之后 |
| `express_flow.py` | 容器里开一张 ERP 单 → 测试机 CC 写 C 盘 T 账套 → 等回执与 CC 读回核对 |

前提：实验栈已 `lab-start` 并 `live` 上候选源码（动测试机的域名 / 实验栈 / CC 先问主会话）；本机有 Chrome（`SMOKE_CHROME`）；
`ssh lenovo-office-lan` 通（线上只读子集用 `ssh pearnly`）。环境变量见 `run.py` 开头。

不跑的：付费识别（上传识别、LINE 发图）、真 LINE 消息与真手机 LIFF、Windows 上 CC 桌面版的原生界面——登记表里标 manual，交验收窗口按清单实测。
