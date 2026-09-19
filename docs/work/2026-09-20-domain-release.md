# 架构整理与开户入口修复发布

用户明确授权：先恢复执行权限，再部署本次改动；开户流程由用户自己运行。不重试发号或开通工单，不创建新测试账号、不发 LINE、不写外部 DMS。

候选源码：5686b474d7dbe8b742114530927b8784cdf7c985。日常目录 main 干净且已推送；发布记录在 codex/release-domain-architecture 独立工作树维护。

## 已完成

- 云端固定入口 `/opt/pearnly/deploy/infrastructure/production/provision-agent.py` 权限已恢复为 755。
- 使用受限 provisioner SSH 密钥发送 `{}`，得到 JSON `Unsupported action or product`，退出码 1 是无效任务的预期拒绝。这证明固定入口可以执行，未进入建站逻辑。
- 最终构建 35457267514 已通过；原构建 35457092115 因补入重试按钮而取消，未部署。投递 35457498732 已通过。
- 失败/部分失败的发号单增加“重试开通”，只调用原请求重试接口；7 项离线交互测试通过。本机控制面清缓存并重启 web，未点击真实重试。
- 镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:e066f0c7461897fb28ce3a7763a59f8686b68a7d120d927f40a522c58a96f4b5`。
- 现有代码验证：73 个模块 AST 等价审计、207 项 DMS 离线断言、55 项卡片/队列断言、15 项云端开户边界、34 个旧入口解析、架构检查和前端构建已通过；本次权限回归 4 项通过。严格内部中文日志扫描仍有已记录的 90 条存量问题。

## 切换计划与恢复

暂停本地开户执行器并持有云端 provision 锁；再次确认 DMS 无租户 Site；备份计费数据库/文件/配置、DMS Site volume、宿主脚本配置和旧镜像引用。备份使用现有旧镜像，计费维护状态按原配置恢复。

固定 SHA 的宿主文件归档包含可执行位；落盘后直接执行开户入口回归。候选镜像原生 migrate 计费 Site，切换 6 个 App 容器，完成私有计费只读鉴权检查后更新 active-image/source-revision 回执并恢复执行器。

私有发布材料：本机 `/Users/skin/.local/state/pearnly-releases/20260920-domain-architecture/`；服务器 `/opt/pearnly/state/releases/20260920-domain-architecture/`。备份包含敏感配置，禁止提交。发生失败保持执行器暂停，结合旧镜像和本次备份恢复，不做无依据的数据库降级。

## 发布完成证据

- 云端 activated/source-revision 与 6 个 App 容器 label 均为 5686b474d7dbe8b742114530927b8784cdf7c985，容器 running。
- 计费原生 migrate 成功；鉴权只读检查通过；Tenant=1、Wallet=1、CreditTransaction=0、TopupRequest=0，与发布前一致。
- 3 个备份归档 SHA256 校验通过；服务器本次 backup 约 2.3 MiB。没有清理历史备份，也未新增定时备份策略。
- 宿主入口解包后实际权限 775（Git 100755），可直接执行，4 项契约检查通过。
- 本地 LaunchAgent 已恢复 running；原 ISS-2026-0239 仍 issued，PR-2026-0241 仍 failed，running steps=0；未执行真实重试。
- 本地控制面登录页 HTTP 200。按钮真实浏览器显示、真实开户成功及手机验收仍待用户操作，不能由这些基础检查代替。


## 后续邮件需求（文案评审，未包含于本次镜像）

用户希望正式开通成功后使用配置好的官方邮件，套用官方邮件结构并替换 Pearnly logo、产品名称，发送账号、初始密码与修改提醒。首次开户与后续增开功能必须区分，后者不发送旧密码；邮件失败不能重跑开户。

只读核验：云端 Keycloak SMTP 已配置，发件人 `pearnlyai@gmail.com`，显示名 Pearnly；默认语言 th，支持 th/zh-CN/en/ja。本机 Frappe 16.33 原生 new_user 模板为设置密码链接，不能直接用于本系统统一身份密码重置。邮件语言拟优先用户偏好，其次租户默认，沿用现有泰语兜底。尚未实现、未发送真实邮件，待用户审阅文案。
