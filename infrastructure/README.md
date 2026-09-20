# 运行定义

- 正式服务器目标：[`production/target.json`](production/target.json)，SSH、CI 镜像传送及租户域名发布共用此配置。
- 正式运行组：[`production/compose.yml`](production/compose.yml)，固定兼容矩阵及镜像摘要，数据库/Redis/Keycloak/计费和三产品分服务运行。
- 空主机准备：`scripts/production/bootstrap-host.sh`；正式镜像通过 CI 后使用 `scripts/production/activate-foundation.sh`。
- 开户由本机 `scripts/production/provision-runner.py` 经受限 SSH 协议执行；管理连接通过只允许两个私有端口的 SSH 隧道。
- 控制后台只在 Mac，云端 `billing.internal` 仅提供受鉴权的计费 API，不提供控制台。
- 秘密值保存在服务器 `/opt/pearnly/secrets`，本机专用密钥保存在 `~/.config/pearnly-production`，不提交源码。

本次 SSD Nodes 是全新身份库和账本，旧服务器、旧本地目标和历史记录保留但不重放任务。配置、验证和部署的实际进度见 [切换记录](../docs/work/2026-09-20-ssdnodes-migration.md)。

本机为 arm64、正式服务器为 amd64，本地测试数据不代表正式服务器吞吐上限。OCR 容量规则见 [共享 OCR 容量](../docs/operations/shared-ocr-capacity.md)。
