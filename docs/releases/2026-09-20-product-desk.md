# 产品 Desk 与账户流程统一发布

用户授权：停止上游更新评估，先部署已合并产品。候选源码固定为 `14f0bb600cdc70edcbcb6a283ce19dc9e7ec41d4`，对应本轮本地验收与 Development checks 35472075261。Frappe 仍为 16.33.0，不包含上游升级。

本次范围：现有云端 DMS、私有计费服务、Keycloak 自有主题，以及本机控制面。保留租户和业务数据，不重跑开户、补发邮件、执行付费 OCR 或外部 DMS 写入。发布记录在 `codex/release-product-desk` 工作树维护，产品候选不加入文档变更。

## 发布前核对

- main/origin/main 为候选 SHA，工作区干净；生产 App 镜像与宿主回执仍为 `5686b474d7dbe8b742114530927b8784cdf7c985`。
- 真实服务器从生产投递/隧道定义确认是 Contabo；旧 SSH 别名指向另一旧主机，未更改其 host key 或绕过校验。使用现有 operator 密钥与已登记主机指纹访问正确目标。
- 现有产品 Site：`testdms.pearnly.com`，租户 `T-2026-0246`；另有 `billing.internal`。DMS Push Log 0，未发现待处理推送；保留当前调度开关。
- 生产身份配置为 Thai 默认，四语言开启，原生账户启用，登录/邮件主题 pearnly，发件人 hello@pearnly.com。
- [Build production image 35472404954](https://github.com/skin306152-star/pearnly/actions/runs/35472404954) 和 [Stage verified production image 35472602824](https://github.com/skin306152-star/pearnly/actions/runs/35472602824) 均成功。

## 备份与切换

私有材料：本机 `~/.local/state/pearnly-releases/20260920-product-desk/`；云端 `/opt/pearnly/state/releases/20260920-product-desk/`。备份仅供这次发布恢复，含敏感配置，不进入 Git，不清理其他历史备份。

暂停开户执行器、持有云端 provision 锁、确认任务静止；使用现有运行版本备份数据库、文件、配置及身份库。使用候选镜像执行原生 migrate，更新固定 SHA 的宿主脚本和自有认证主题，切换服务后回读版本、迁移结果、鉴权和站点入口。任何失败保留维护状态与备份，不能仅降级镜像掩盖 schema 不匹配。

## 结果

已部署：`ghcr.io/skin306152-star/pearnly/frappe@sha256:2ff73ae2ed5c873a7da6103bd6cec0ec34769ba07e0a9ce68c3d2e04789af530`。

- 云端 billing-web、DMS web/worker/scheduler/socketio/frontend 六个容器的源码 label 全部为候选 SHA，running、重启计数 0。active-image、source-revision 和本次 activated 回执一致。
- 云端 billing.internal、testdms.pearnly.com 与本机 control.localhost 原生 migrate 成功，没有使用 skip-failing。产品权限 patch 的 Patch Log.skipped=0；原生页面、推送记录审计字段、公司导航及默认 Pearnly favicon 均已落库。
- 229 个宿主文件与固定 SHA 归档逐个校验一致。Keycloak 自有主题已更新并重启；镜像和身份服务底座版本均未升级。原正式邮箱与默认泰语配置保留。
- 云端备份约 2.4 MiB，包括两个 Site 的原生数据库/文件备份及 Site volume、宿主配置、Keycloak SQL；四份归档 SHA256 全通过。本机控制面 Site 归档 1,462,106 字节，数据库/文件/配置已备份。没有新增定时备份或删除历史备份。
- 三个 Site 的关键业务表记录数量与指定字段摘要在升级前后相同；用户/语言、发号/部署/开通步骤、账务/充值、DMS 操作员/绑定/推送等本次核对项未变化。本地预览示例没有复制到生产。
- 使用现有 DMS Admin 在站内只读检查四个页面投影、操作员/推送列表及权限：列表均为 0 条，平台 Role/User Permission 写入和他人账户访问均拒绝。私有计费匿名拒绝、管理凭据只读访问成功；Tenant=1、Wallet=1、CreditTransaction=0、TopupRequest=0。
- 本机控制面重载后，真实已完成工单通过新的欢迎邮件就绪检查；仅检查条件，没有调用发信。官方 outgoing Email Account 开启；Email Queue 仍为原有 3 条 Sent，无新增。控制面登录 HTTP200，调度器/worker 恢复；开户执行器恢复运行前确认 pending/running 请求为 0。
- 公网 `/`、`/login` 和三个产品页面入口均经正常重定向到统一登录 HTTP200。浏览器实际显示 Pearnly 登录页及 hello@pearnly.com 联系入口；未持有已登录的生产浏览器会话，没有代替用户进行登录后的视觉验收。产品 JS/CSS、列表、首页与 favicon 共五份实际响应逐字节等于候选源码。

## 切换期间发现并处理

1. 发布探针初版把 Patch Log 的主键当作 patch 路径，导致检查中断。只读核对发现该框架使用随机 name，实际 patch 字段正确且 skipped=0；改为按 patch/skipped 查询，重新执行失败后的验证段通过。没有重跑迁移、绕过断言或重建备份，原失败日志保留。
2. 容器重建后公网一度返回“Site does not exist”，而直连 dms-web 正常，billing-web 返回同一404。产品代理重载后恢复302，符合代理保留旧容器地址的表现。nginx 配置检查通过，重载后完整公网入口及资源回读全部通过。不能只用容器 running 作为发布完成依据。

## 剩余验收边界

本次完成已授权发布，不触发真实开户、补发历史欢迎邮件、实际改密、LINE 发信、付费 OCR 或外部 DMS 写入。现有 DMS Site 的 scheduler disabled 状态发布前已存在，本次保留；worker 在线、无待处理业务任务。实际开户/收件、手机绑定及外部业务结果仍由对应场景验收，不以本次上线健康检查代签。

恢复须使用本次数据库/文件/配置与原镜像配套，不能仅降级镜像。私有执行脚本、迁移日志、前后摘要和回执保存在上述发布目录；Git 只保存脱敏记录。
