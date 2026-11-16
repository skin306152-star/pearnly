# 云端 Express：CC 合流分支记录（2026-11-16）

- 分支 `pearnly/express-cloud-cc`，基于 main `4879255e9`。按顺序含：步 B 统一联网客户端（`net.client`）、文件隧道、`--cloud` 云端模式、云端精简包、桥侧连接（连接窗口、非脚本设置、宿主、设置页一行）、代录凭据加密交接（cryptography 46.0.3）、设备密钥一次性配对、中转改成有请求才触发、云端侧接线与安装脚本。
- 不进 1.12.22；进 1.12.23 或更后，由发版时定。合入前与 CC 桌面版 1.12.23 窗口约定：它先合（`queue_client.py`、`tests/test_companion_contract.py`），本分支后合、负责变基。

## 检查（本机）
- companion 全套：2274 通过、63 跳过；`tests/test_companion_contract.py` 118 通过；`check-architecture`、`check-impact`、`check-copy`、`check-product-map` 通过。
- Windows 真机：**没测**。11-16 测试机1 预检（从源码跑，临时目录）：远程桌面控件能建出、高级设置第 9 版各属性可设可读；**阻塞**：PySide6 的 `QAxWidget` 不提供 `queryInterface`，拿不到控件接口，非脚本两项（只映射隧道盘、网络级身份验证）设不了，窗口按设计拒绝连接。改法待定（自建 ATL 宿主）。
- `check-impact` 列出的实测入口全部「没测到（需 Windows）」：步 B 的 `cc.desktop.requests`、`cc.desktop.places`、`cc.desktop.operator`、`cc.mrerp-login`、`cc.desktop.bridge`；桥侧的 `cc.desktop.cloud-express`；云端侧见 `2026-11-16-express-cloud-install-command.md`。原因：同一 Windows 用户下第二份 CC 会抢单实例锁，下次实验栈时换 Windows 用户测。

## 公开仓库扫描（推送前）
- 11-16 仓库改为公开。推送前用本机扫描脚本（只扫相对 main 新增的行，规则含内网 IP、私钥、令牌 / 密码赋值、.rdp 原文行、Bearer 令牌，另加不入库的本机字典：云端主机地址、账号名、主机名、目录名、内部机器名）。
- 本分支首次扫描命中 186 处（测试夹具与注释里的云端主机、账号、域、主机名、端口），已全部换成占位（`198.51.100.7`、`CLOUDUSER-01`、`example`、`CLOUD-HOST` 等）；测试里故意写的假密码 / 假令牌逐行标注 `scan: fake`；测试里的办公室服务器名换成通用名。
- 复扫结果：**拦截 0 · 提示 0**；提交说明命中 0。
- 已推送过的旧分支处理见主会话记录（三条远端分支已删、运行记录已删、清除请求草稿在本机）。
