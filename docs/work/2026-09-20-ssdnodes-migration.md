# SSD Nodes 空环境切换

基线：`7415c1e`；工作树 `ssdnodes-migration/pearnly`，分支 `codex/ssdnodes-migration`。

## 用户决策

- 已购买 SSD Nodes；授权配置新服务器并更新所有相关项目配置。
- 用户明确选择 2：新环境重新开户，不迁移旧租户、账户、钱包及业务数据。旧服务器与本地历史不删除，不重放旧任务。
- 三产品共用身份、计费及 OCR 基础能力，各产品权限、数据、原生队列隔离。控制面继续只在本机运行。

## 当前目标

公开配置唯一入口：`infrastructure/production/target.json`。
SSD Nodes Singapore，服务 50948，IPv4 `107.155.65.74`，IPv6 `2602:ff16:5:115d::1`，Ubuntu 24.04.3 LTS，8 vCPU、32095 MiB、473 GiB 文件系统。
SSH 已使用现有 operator 密钥验证；只开放 22/80/443。Docker、Compose、Nginx、Certbot 已安装。

旧 Contabo `194.233.74.40` 的提供商回读为 `suspended=true, reason=Withdrawal`；保留退款流程及全部旧数据。

## 实现边界

- CI 镜像传送、Mac SSH 隧道、开户 runner、服务器域名验证统一读取公开目标配置。
- 本地桥在私有身份与计费服务验证成功后设新默认目标；旧目标标记 retired，保留历史，不自动转移旧开户单。
- 旧目标全部待办（含删除）不发给任一 runner；未配置或目标不匹配的生产 runner 不取任务。
- 凭据不入仓；新服务器生成独立数据库、身份管理与计费密钥。复用已有授权的邮件/OCR 服务凭据不涉及迁移业务数据。

## 交付检查（待追加实际结果）

- 架构检查、DMS 离线检查、开户隔离定向测试。
- 新 amd64 镜像 CI、摘要、运行回读。
- 私有身份/计费连通、匿名拒绝、空账本；公网 HTTPS 与管理面隔离。
- 本机控制面备份、目标切换、隧道/runner 重启、旧任务不重放。
- 不执行真实 LINE 消息、付费 OCR 或第三方建单；新租户需要重新开户后单独验收。
