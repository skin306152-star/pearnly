# 11-06 部署后补验（2026-11-07）

主会话 local_3d36d798 派，补 `~/pearnly-devtools/main-handoff-2026-11-06.md` 第六节剩下的上线后验证。
被测版本：生产发布标记 release-20261106-1542 → main 08f32a3b1；CC 桌面版 1.12.15 正式版（测试机 skin3，11-06 23:40 起运行）。
只用测试租户与测试账号，没碰正式租户、没碰真实 LINE 绑定。截图在本机 `/Users/skin/pearnly-devtools/device-1107-verify/`（不进仓库）。

## 1. 三星 LINE 盘点 LIFF 相机 + 物料调整新流程

已完成，结果与步骤补进 [真机记录](2026-11-06-device-erp-stocktake-liff-camera.md)「11-07 线上补全」：
LINE 原生相机授权弹出、读码认出物料、结束盘点不动库存、「按盘点结果调整物料」生成草稿 IA2569-000001（未提交）。

## 2. CC 桌面版 1.12.15 正式版观感（测试机，只看不改设置）

做法：skin3 一次性计划任务启动 `companion.exe`（后台进程按需拉起 `--window` 子进程，和用户点桌面图标一样），截屏后正常关窗（`CloseMainWindow`）。
后台进程 6080（`--background`，11-06 23:40 起）没动、没重启、没开调试口；关窗后心跳 12:43 正常（上报 2 本、可写 2）。位置、所选账套、testerp 暂停都没动。

| 项 | 结果 | 截图 |
|---|---|---|
| 左侧对话列表滚动条平时隐藏、鼠标移上去才出现 | **通过**：鼠标在右侧时列表无滚动条；移到列表上出现滚动条，文字位置不动 | `cmp-sidebar-hover.png`（左 = 鼠标不在，右 = 鼠标在列表上）；全屏 `pc-c02-loaded` / `pc-c03-hover` |
| 对话区往上滚时内容停在顶栏下面、有淡出 | **通过**：打开已有对话「PDF代录结果讨论」上滚，顶栏下第一行文字变淡、下一行正常，没有硬切 | `crop-c06-edge.png`（顶栏交界放大）、`crop-c05-top.png`；全屏 `pc-c05-scrollup` / `pc-c06-scrollup2` |
| 状态行显示「执行中」 | **界面上没看到**：这行只在 CC 正在跑任务时出现，本次不发消息（会建对话、花模型费用）。替代证据：线上 testcowork 下发的 CC 前端 `assets/index.CaTX0cUV.js` 里 `thinkingNow` 为「执行中」/ 泰文 / 英文三处，「思考中」0 处 | 无 |

打开对话只浏览、没发消息；看完点「新建」回到首页再关窗，最终桌面与开始时一致（`pc-c00-now` / `pc-c08-final`）。

## 3. testai.pearnly.com 现状（只读，没改 DNS）

| 项 | 读回 |
|---|---|
| Cloudflare 记录 | `CNAME testai.pearnly.com → 875e14d3-….cfargotunnel.com`（测试机隧道 pearnly-testenv），代理开，备注「pearnly testenv lab」，2026-10-10 04:14 UTC 建，之后没改过 |
| 现在访问 | 经 Cloudflare 502（测试机实验栈没起） |
| 生产上有没有 testai 站点 | **没有**：直连生产 107.155.65.74 的 https 握手报「unrecognized name」（没有这个名字的证书 / 站点），http 404 |
| 用途 | ADR-019 智能体入口（CC 登录入口）的**测试版**，正式版是 fatcc.com。站点只在测试环境实验栈的账本容器里由 `infrastructure/testenv/ai-entry-init.py` 建（`testenv.sh ai-entry`），隧道把它转到实验栈（`scripts/testenv-cloudflare.sh`）。生产登录服务的 `pearnly-ai` 客户端回调与 test 开头租户的退出跳转都登记了 testai |
| 平时谁在用 | 基本没人：10 个站点都没配 `pearnly_assistant_entry_url`，测试租户也走 fatcc.com（11-05 站外设置对比）；只有设了 `PEARNLY_TOMO_ENTRY=https://testai.pearnly.com` 的 CC 测试包会去它。测试机 skin3 现在没有这个变量；录制笔记本 aksar 10-26 有过，本次没查 |
| 有意还是忘了切回 | **有意**：`testenv.sh` 的 lab-start / lab-stop 只切 erp / dms / cowork，从不切 ai；用户 10-12：「删掉 ai.pearnly.com，testai 留着」（[发布记录](2026-10-12-release-f518964c.md)） |

建议：**保持现状**。它本来就只在实验栈起来时才有内容；“切回线上”= 删掉这条 CNAME 落到通配记录，而生产没有 testai 站点，结果是证书错误而不是 502，没有好处。
要是以后觉得 502 碍眼，可选做法是实验栈不在时也不处理（现状），或干脆按需在 lab-start 时才建这条记录——都要用户定，本次不动。
