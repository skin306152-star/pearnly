# 旧 Supabase 与 Cloud Run 退役

## 授权与边界

- 用户确认旧项目停用，并明确选择清理旧 Supabase、旧 Cloud Run 服务、定时任务、队列及专用存储。
- 保留新平台 SSD Nodes、OCR 所在 GCP 项目 `pearnly`、域名、迁移源码及备份。不删除整个 GCP 项目，不清理其他任务的工作树或数据卷。
- 本任务基线：新仓 `717d202af3cfcd06815e215ef088e07f4251e00e`；记录分支 `codex/legacy-cloud-retirement`。没有产品代码变更。

## 删除前证据

- Supabase 项目：`Pearnly / aydjsgmirjpkjaqknmlg`；Cloud Run Web 当前运行配置指向同一数据库，运行修订 `pearnly-web-ec2e5c957e6b-s3`，镜像 digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。
- 当前控制台 Security Advisor 显示 25 errors、24 warnings；只读目录查询确认 public 中 23 张表未启用 RLS，且 anon 有 SELECT/INSERT/UPDATE/DELETE 权限。
- 在强制只读事务中以 anon 角色执行存在性和 LIMIT 0 查询成功，涉及 users 的 password_hash/email、email_codes、password_reset_log；没有读取密码、邮箱、令牌的具体内容，也没有试写。
- Data API 无 API key 时返回 401；使用现有服务端凭据执行 LIMIT 0 返回 200/空数组。尚未完成公网匿名 key 端到端验证，未证明历史数据泄露。
- 新服务器 `107.155.65.74` 实读 12 个运行容器；testdms、ris、billing.internal 的数据库均为 MariaDB，容器环境及 Site 配置未发现旧 Supabase、Cloud Run URL 或本次旧文件桶引用。
- 新站点 OCR 项目及 `/opt/pearnly/secrets/ocr.json` 的 project_id 均为 `pearnly`，必须保留 GCP 项目、计费、OCR API 及其身份。
- 旧 Cowork/ERP 迁移仍有独立工作树；固定源码、历史线上证据及本地业务实验室必须保留。旧线上退役后，后续迁移需要以此次退役前冻结证据为来源，不能再要求不存在的服务持续提供在线回读。

## 资源清单与执行状态

| 对象 | 删除前状态 / 大小 | 处理状态 |
| --- | --- | --- |
| Supabase `aydjsgmirjpkjaqknmlg` | Healthy；213 个 public 关系对象 | 用户最终确认后永久删除；组织列表刷新为空，项目域名不再解析 |
| Cloud Run `pearnly-web` / `pearnly-worker` | ec2e5c957e6b-s3 | 已删除；服务列表为空，原生 URL 均返回 404 |
| Cloud Run Job `pearnly-schema` | 存在 | 已删除；Job 列表为空 |
| Scheduler `pearnly-dms-master-refresh` / `pearnly-background-recovery` | PAUSED | 已删除；Scheduler 列表为空 |
| Cloud Tasks `pearnly-background` | RUNNING | 先暂停后删除；队列列表为空 |
| `pearnly-app-build-112074003592` | 0 bytes | 已删除，bucket 列表不再包含 |
| `pearnly-app-files-112074003592` | 1,169,859,018 bytes | 所有对象版本及 bucket 已删除，列表不再包含 |
| `pearnly-app-installers-112074003592` | 62,905,188 bytes | 所有对象版本及 bucket 已删除，列表不再包含 |
| `pearnly-app-temp-112074003592` | 0 bytes | 已删除，bucket 列表不再包含 |
| `pearnly-app-backups-112074003592` | 6,530,348,209 bytes | 保留：用户确认保留备份，后续需独立决定保留周期 |

旧云资源定义保存在本机私有目录 `/Users/skin/.config/pearnly-archives/legacy-cloud-retirement-20260924/`，权限 0700/0600；不入仓。源代码归档仍为 `/Users/skin/.config/pearnly-archives/pearnly-app-20260921-cleanup/`。

## 删除后检查

- 2026-09-24 00:30–00:32 Asia/Bangkok：GCP `pearnly / asia-southeast1` 的 Run services、Run jobs、Scheduler jobs、Cloud Tasks queues 均为 `[]`；项目 bucket 列表只剩备份桶。
- Supabase 组织 `ktxmcjrjohlpzunrqori` 显示创建项目的空列表，刷新后不变；旧项目域名已 DNS 不存在。
- 新平台 testdms、ris 的 `/desk` 仍正常转到统一登录页并返回 200；实际 realm `pearnly-business` 的 OIDC discovery 200，issuer 正确。最初用错误的 realm `pearnly` 探测返回 404，已据实际登录跳转更正，没有把它作为平台故障。
- 新 DMS 容器内 OCR 服务账号刷新认证通过，令牌未打印；未调用模型、未发生付费 OCR，不能据此替代 OCR 业务验收。
- 四个已删 GCS bucket 原有 soft delete 为 604800 秒（7 天），此次没有绕过保留期。逻辑删除成功不等于云端立即物理擦除或所有旧费用清零。
- 原有源码 bundle 验证通过；两个仓库原工作目录没有业务代码修改。文档只运行 diff 检查和提交钩子，不跑业务全套测试或重部署新容器。

## 保留对象与后续条件

| 对象 | 用途 / 所属 | 保留原因与下次复核条件 |
| --- | --- | --- |
| SSD Nodes 新平台、GCP `pearnly`、OCR API/服务账号、域名及现有路由 | 当前新平台 | 用户明确保留；只有另行迁移/退役授权才清理 |
| 旧 GCS 备份桶及本机 `pearnly-archives` | 历史恢复与本次删除凭证 | 用户明确保留备份；后续单独确认期限 |
| 旧源码目录、固定来源副本、Cowork/ERP 工作树及实验室数据 | 进行中的迁移任务 | 仍有未集成成果；各任务完成迁移并验证保存后自行复核 |
| Artifact Registry `asia-southeast1/pearnly-app` | 旧镜像/迁移基准参考 | 本次具体删除清单未包括镜像库；迁移来源不再需要镜像后单独复核 |
| Secret Manager 五项旧运行配置与 LINE 凭据 | 迁移运行参考，可能包含新平台复用的外部身份 | 未轮换外部凭据；待迁移参考完成并逐项确认依赖后处理 |
| 已停止 `pearnly-work` VM 及磁盘 | 旧 WeKan 数据 | 用户本次选择范围未包括 VM/磁盘；明确补充范围及数据保留要求后复核 |

## 当前限制与收尾

- Supabase 删除不会自动撤销历史已泄露的外部凭据；本次未证明存在泄露，也未轮换新平台复用的 LINE/OCR 凭据。
- 域名和 Cloudflare 路由不在本次确认的删除清单。旧网站地址在旧服务删除后可能不可用；不擅自给新平台接管旧入口。
- 旧 Supabase 和 Cloud Run 专用运行/存储清理已完成；未宣称完成全 GCP 退役或历史泄露调查。
- 本记录合回本地 main；不推送主线原有的 58 个待推送提交。核验时远端 main 为 `ac33e0c7362915ae314295cb26f7748cb5589de0`，本任务远端分支不存在。收尾删除本任务专属工作树和本地分支，保留本机私有证据目录。
