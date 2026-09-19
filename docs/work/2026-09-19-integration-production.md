# 合并主线并切换新平台生产

- 用户授权：2026-09-19 明确要求“合并并切换生产”。包含本地整合、主线推送、新平台发布和本地控制面统一；不切换旧 Cloud Run 的域名/LINE 通道，不删除租户或数据。
- 状态：代码合并与新平台生产发布已完成；外部 DMS 写入和 LINE 真机验收未执行。
- 整合工作树：`/Users/skin/.codex/worktrees/development-workflow/pearnly`；分支 `codex/development-workflow`。
- 目标：GitHub `main`、日常目录 `/Users/skin/pearnly`、本地控制面及 Contabo 新平台使用可追溯的统一候选。
- 来源：DMS `57bee0001e9c79dc258d641e24015b52d6ccf3cb`；云开通 `201cc4b263330f68ca2349809a66d8c7c2b2740d`；规范 `4fe24fe`。整合提交 `3b50ae6`，自动合并无冲突。
- 旧生产基准复查：旧 Cloud Run Web/Worker 仍为 `ec2e5c957e6b-s3`，各 100% 流量，与前次已映射源码和 digest 一致。本次不新增旧功能迁移。
- 新平台升级前镜像：源码 `97222477f9ba21248b2754eda9dbb2d10c8301c0`，digest `sha256:8ff3cbd12b8571f8533088f1042dbda6c7ce447935d7cf79350b42776746bab8`。

## 整合时必要修复

- 两种控制面 LINE 凭据表单的四个 Password 字段设置 `length: 200`，容纳此前已定位的 172 字符访问令牌。保持 Password 存储与服务端权限，不改真实渠道凭据。
- 本地生产开通执行器的 LaunchAgent 改指日常主线目录，解除对云开通工作树的长期依赖；运行挂载也在切换时统一。

## 验证与发布顺序

1. 定向离线测试、前端构建、翻译与结构检查。
2. 记录现有环境、备份与恢复路径；隔离环境验证相关 Frappe 迁移和控制面行为。
3. 固定候选 SHA，使用现有 amd64 工作流构建与验证镜像，以 digest 投递。
4. 暂停开通执行器与受影响写者，备份现有站点及配置；受控迁移并切换镜像和本地控制面。
5. 核对 GitHub main、本地 HEAD、运行镜像/脚本、服务健康和受影响行为，记录外部/真机未验项。

具体命令、备份引用、构建/投递运行和结果随进展补充。未执行项不标通过；失败保持数据与备份，按已知旧镜像及相应配置恢复。

## 已完成验证

- Node 22.14.0（官方分发校验 SHA-256），`npm ci`、类型检查及生产 build 通过。锁文件已有 2 项 moderate npm advisory，本次未变更依赖。
- DMS FakeTransport 77/77、登录路由 3/3、开通参数边界 3/3、会话失效重登通过；DMS 测试容器无网络，使用候选源码。
- 术语表与 Vue 翻译检查通过。严格 Python 文案检查失败的 90 条均为内部日志等中文，与云开通 `201cc4b` 原样一致；记录为存量缺口，不改检查器、不宣称全绿。
- 初次使用 macOS Python 3.9 执行类型注解不兼容；改用 3.12 跑纯逻辑，Frappe 相关验证在固定镜像 Python 3.14 中执行。
- 切换前只读盘点：生产 DMS volume 无租户 Site，billing.internal 存在；本地 control.localhost 无 Tenant、Issue、LINE Channel。

## 发布结果

- 发布源码：`26158211ee4a3c6745537b10d08812eb70804f8c`。DMS、云开通和开发规范均已进入 GitHub `main`，日常目录 `/Users/skin/pearnly` 已切换到跟踪 `origin/main` 的 `main`。
- 生产镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:fc28c172fa36b3f286aa926a7edbefb983117a5e1f3b4244cda87b409ddda88a`。
- [构建与运行时检查](https://github.com/skin306152-star/pearnly/actions/runs/35454183636)及[受限镜像投递](https://github.com/skin306152-star/pearnly/actions/runs/35454447743)均成功，记录的 head SHA 与上述源码完全相同。
- Contabo 的 billing-web 与 DMS web/worker/scheduler/socketio/frontend 共 6 个容器均使用该镜像，运行中、重启计数 0、无 App 源码 bind mount。`active-image.json` 已指向该 digest，后续开通使用统一版本。
- 宿主机从固定源码归档更新的 205 个文件逐个校验一致；源码回执位于 `/opt/pearnly/deploy/source-revision.txt`。
- 本地 control-bench/web/scheduler 所有 App 源码挂载已统一为 `/Users/skin/pearnly/apps/`。控制面已迁移并恢复服务，前端使用 Docker DNS 动态解析模板，HTTP ping 返回 pong。
- 实际本地管理后台登录与原生 LINE 表单验证通过：`maxlength=200`，172 字符测试输入完整，未保存真实渠道或排入开通任务。浏览器探测使用独立 1GiB 容器，已退出。
- 本地生产开通 LaunchAgent 已改指 `/Users/skin/pearnly/scripts/production/provision-runner.py` 并恢复运行；复核单实例锁与空队列。恢复初期出现一次 `Local control bridge unavailable`，直接桥接回读正常，后续日志未增长；未把该瞬时错误隐去。
- 原 `/Users/skin/pearnly-cloud-provisioning` 保留为原任务工作树，保留其未跟踪 `frontend/node_modules`；已不承担控制面 App 挂载或执行器入口。它的所有提交已被主线包含，不再需要从该目录启动生产执行器。
- 旧 Cloud Run Web/Worker 交付前再次回读，仍为 `ec2e5c957e6b-s3`、各 100% 流量。没有切换旧域名、LINE Webhook 或用户数据。

本记录的收尾提交仅更新 Markdown；它可能让 main 的提交号领先运行镜像。运行代码仍固定为 `2615821`，用 `git diff 2615821 main -- apps frontend infrastructure scripts` 核对没有代码和运行配置差异，不能把记录提交误报为新的运行镜像。

## 迁移与恢复实证

- 测试项目的 4 个容器、独立测试卷与 internal 网络验证归属后已移除；生产和本地控制面的命名卷均保留。
- 测试项目 `pearnly-release-20260919` 使用独立数据库、Redis、sites volume 和禁止出网的 internal 网络，未接任何真实凭据或第三方业务系统。
- 先安装云开通 `201cc4b` 的旧 schema，写入一条测试记录和 172 字符假令牌；挂载候选源码后连续迁移两次，四个 Password 字段均为 200，原记录及加密凭据保持。
- 实际 Chromium/Frappe 表单输入并保存 172 字符假令牌成功，服务端解密回读一致。测试站点初始化及浏览器默认 locale 配置曾导致导航失败，完善测试环境后通过；未修改业务代码来绕过验证。
- 原生备份包含数据库、公有文件与私有文件；在同一隔离 Site 恢复备份后，原记录和备份时的加密令牌均恢复正确。拆户计划检查也通过，临时记录清理完毕。
- 生产切换在暂停本地执行器、持有服务端 provision 锁后进行；计费站点先开启维护模式，用旧镜像备份，再停止写者、迁移并切换新镜像，最后恢复原维护状态。
- 生产 DMS volume 经切换前再检查仍无租户 Site，所以本次没有可迁移或实测的线上 DMS 租户业务。计费站点四类业务记录切换前后均为 0。

## 留存与恢复路径

- 服务器私有发布目录：`/opt/pearnly/state/releases/20260919-integrated/`。包含执行脚本、候选归档、迁移/验证日志、旧镜像引用、原站点配置、站点备份与宿主机配置归档；归档 SHA-256 已校验。
- 本机私有发布目录：`/Users/skin/.local/state/pearnly-releases/20260919-integrated/`。包含本地控制面原生备份、原 LaunchAgent/挂载元数据、旧前端产物、测试与发布日志、固定版本回执。配置/备份含敏感信息，不提交到 Git。
- 需要恢复时先暂停执行器和相关写者，保持维护模式；使用已记录的旧镜像、匹配的宿主机配置及切换前数据库/文件/加密配置共同恢复。不能只回滚镜像后让旧代码读新 schema。计费恢复通过私有鉴权检查后再恢复执行器。

## 验收边界

| 层级 | 结果 |
| --- | --- |
| 整合与主线 | 已完成，源分支提交包含于 main |
| 本地定向验证 | 通过；严格内部中文扫描保留上文存量失败 |
| 隔离安装/重复迁移/浏览器保存/备份恢复 | 通过，测试资源独立 |
| CI 与镜像投递 | 成功，固定 SHA 与 digest |
| 新平台部署 | 完成，镜像、宿主脚本、挂载和执行器回读 |
| 服务与权限 | 计费私有接口匿名拒绝、授权只读成功；身份发现接口成功；本地控制面路由及元数据通过 |
| 外部 DMS 建单、LINE 消息、收费 OCR | 未执行；没有把部署授权扩大成外部业务测试 |
| 用户和 LINE 真机验收 | 未执行；用户尚未验收新版本 |
