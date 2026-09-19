# 首次开户邮件与官方邮箱统一

基线 909df6d；分支 codex/welcome-email-card；独立工作树。用户已确认中文卡片，要求移除“专属空间/租户/功能列表”和彩色边线；欢迎、忘记/修改密码、对外联系均用 hello@pearnly.com。电话沿用官网 +66 86-889-2228，不展示服务时间。

归属：pearnly_control.onboarding 负责开通完成事件、幂等通知与发信配置；原生 Email Queue 负责投递/重试/状态；模板在 templates/emails；云端 Keycloak 继续负责统一账号密码邮件。本机管理后台不迁云，新账户仍只开在云端。不会重试真实开户，也不会主动发送测试邮件。

固定事实：旧项目当前 100% 流量 revision pearnly-web-ec2e5c957e6b-s3，挂载 pearnly-web-env 第 3 版；只读取其中 SMTP 字段，发件人 hello@pearnly.com。配置保存在私有 0600 临时文件，不入库/日志/提交；复用渠道，不复制旧业务代码。

实现目标：首次开户全部产品已发布且 active 后，锁定发号单创建一条原生邮件队列，保留幂等回执。多产品展示各产品访问域名，不列功能。失败不影响开户状态；原生队列重试沿用同一记录，成功后正文脱敏。语言随发号单欢迎邮件语言，默认 th，支持 zh/en/ja。后台自身中文不作为邮件语言。

验证与发布：已在本机控制面启用欢迎邮件，并更新云端统一身份邮件配置。卡片的中泰预览已通过浏览器检查；实际收件及各邮件客户端兼容验收未执行，不以 SMTP 认证代替送达证据。

## 实际验证与启用

- 13 项离线服务契约通过：完成事件、幂等回执、失败隔离、云端限制、发布步骤、收件人、四语言、多产品、权限与无 BCC；四语言原生 MIME 检查通过（转义、CID 图片、官方 From/Reply-To、发送后脱敏）。
- 原生服务连接实际配置的 Email Account，在事务回滚/SMTP 阻断下用合成账号、密码和域名验证生成且仅生成一条队列。没有读取或发送真实账户密码；真实发号单未改写，回滚后队列数量为 0。
- 207 项 DMS 离线断言、4 项强制 SSH 入口契约、7 项 Desk 动作检查、架构和翻译检查通过。CI 35458457825（30e429f）成功。
- 本机 control.localhost 原生 migrate 成功；Email Account=Pearnly，From/Reply-To=hello@pearnly.com，无 BCC、关闭追踪；新建 control-worker，启用原生 scheduler 和 email.queue.flush。之前只有 scheduler 容器、System Settings.enable_scheduler=0，不足以实际投递。
- 云端 Keycloak pearnly-business的 SMTP 已最小 PUT 更新为 hello@pearnly.com、Reply-To 同址、显示名 Pearnly。复用旧项目正式发信渠道，TLS/认证/NOOP 成功，未发送测试邮件。忘记密码与执行改密邀请沿用同一 realm 通道。
- 本机开户执行器已恢复 running；原 PR-2026-0241 仍 failed。历史完成请求不会启动时补发；没有自动重跑真实开户。
- 欢迎邮件仅位于本机控制面，无需切换云端业务容器。云端业务镜像仍是已验收部署的 5686b47，SMTP 为本次独立配置变更；仓库其他 App 的联系元数据和宿主脚本官方邮箱默认值随下次对应代码发布生效，不把源代码提交冒称整套镜像发布。
- 真正送达、垃圾箱归类、客户端渲染及用户首次登录仍待实际开户验收。发号单“欢迎邮件语言”默认 th，zh/en/ja 可选；本次为首次账户通知，不发送后续增开功能邮件。

## 配置与恢复

本机使用原生 Email Account 密码字段存储 SMTP 密钥；临时 SMTP 导出在配置完成后删除。控制面迁移前的原生备份位于本机私有 `/Users/skin/.local/state/pearnly-releases/welcome-email/control-backup/`。云端旧 SMTP 配置备份位于 `/opt/pearnly/state/releases/20260920-welcome-email/smtp-before.json`（0600）。没有添加无限增长的自动备份任务。

配置入口 `python -m pearnly_control.onboarding.mail_setup` 仅用于 control.localhost，从标准输入接收 SMTP JSON（不传命令行参数、不打印凭据）；源配置必须为正式 hello@pearnly.com。`bash scripts/production/control-up.sh` 启动原生 worker/scheduler。后续需停发时可暂停原生邮件队列；不要通过重跑开户来重发邮件。队列成功后正文被原生脱敏，不应重新发送该已完成记录。
