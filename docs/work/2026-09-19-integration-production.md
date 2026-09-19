# 合并主线并切换新平台生产

- 用户授权：2026-09-19 明确要求“合并并切换生产”。包含本地整合、主线推送、新平台发布和本地控制面统一；不切换旧 Cloud Run 的域名/LINE 通道，不删除租户或数据。
- 状态：进行中。
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
