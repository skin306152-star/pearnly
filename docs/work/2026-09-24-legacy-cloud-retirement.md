# 旧 Supabase 与 Cloud Run 退役

## 授权与边界

- 用户确认旧项目停用，并明确选择清理旧 Supabase、旧 Cloud Run 服务、定时任务、队列及专用存储。
- 后续用户扩大范围为旧 Google 资源全部删除、保留本地及 Git 源码，并再次明确“新平台的 OCR 不要动，新平台依赖的任何东西都不要动”。因此继续删除旧云备份、镜像、VM/磁盘/快照和旧部署身份；保留新平台 SSD Nodes、GCP 项目 `pearnly` 及计费、OCR 身份与 API、域名和新平台依赖。未执行项目删除或关闭计费。
- 本任务基线：新仓 `717d202af3cfcd06815e215ef088e07f4251e00e`；记录分支 `codex/legacy-cloud-retirement`。没有产品代码变更。
- 第二轮记录基线 `8a9cd3b8e8348f92cb450904e82e1a9cceeafdfb`，分支 `codex/legacy-google-cleanup`；仍只有任务记录修改。

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
| `pearnly-app-backups-112074003592` | 6,530,348,209 bytes | 第二轮依补充授权删除所有版本及 bucket |
| `pearnly-work` VM / 两块同名前缀磁盘 | TERMINATED；20 GiB 系统盘、30 GiB 数据盘 | 第二轮删除 VM 及两块磁盘 |
| 旧 WeKan 快照与日程 | 9 个快照，均来自 `pearnly-work-data`；`pearnly-work-consistent-daily` | 第二轮删除 |
| 旧 WeKan 专用网络 | 保留 IP `136.85.65.80`、两个防火墙规则、子网/VPC 均为 `pearnly-work` 专用 | 第二轮删除；共享 default 网络保留 |
| Artifact Registry `asia-southeast1/pearnly-app` | 32,140,705,105 bytes；DOCKER 格式 | 第二轮删除；不是 Git 源码仓库 |
| 五个旧 Secret Manager 配置 | `pearnly-line-dms-a`、`pearnly-line-dms-b`、`pearnly-web-env`、`pearnly-work-env`、`pearnly-worker-env` | 第二轮删除存储副本；未撤销 LINE 外部身份或新平台凭据 |
| 六个旧服务账号 | `pearnly-tasks`、`pearnly-worker`、`pearnly-web`、`pearnly-companion-publisher`、`pearnly-work-vm`、`pearnly-deploy` | 第二轮删除；保留 `pearnly-ocr-sa` 和默认 Compute 身份 |
| 旧 GitHub 身份联合池 `pearnly-github` | 两个 provider 仅允许旧 app/companion 仓库，repo ID 为 1241204785 / 1301765903；新仓 ID 为 1377078911 | 第二轮删除，未修改 Git 仓库或新平台 GHCR 发布身份 |
| 更早的 ERP 项目 `project-d0fbd530-1ee3-436d-b58` | 已处于 `DELETE_REQUESTED` | 本轮只读确认计费关闭；处于 Google 删除流程，不是已物理擦除 |

旧云资源定义保存在本机私有目录 `/Users/skin/.config/pearnly-archives/legacy-cloud-retirement-20260924/`，权限 0700/0600；不入仓。源代码归档仍为 `/Users/skin/.config/pearnly-archives/pearnly-app-20260921-cleanup/`。

## 删除后检查

- 2026-09-24 00:30–00:32 Asia/Bangkok：GCP `pearnly / asia-southeast1` 的 Run services、Run jobs、Scheduler jobs、Cloud Tasks queues 均为 `[]`；项目 bucket 列表只剩备份桶。
- Supabase 组织 `ktxmcjrjohlpzunrqori` 显示创建项目的空列表，刷新后不变；旧项目域名已 DNS 不存在。
- 新平台 testdms、ris 的 `/desk` 仍正常转到统一登录页并返回 200；实际 realm `pearnly-business` 的 OIDC discovery 200，issuer 正确。最初用错误的 realm `pearnly` 探测返回 404，已据实际登录跳转更正，没有把它作为平台故障。
- 新 DMS 容器内 OCR 服务账号刷新认证通过，令牌未打印；未调用模型、未发生付费 OCR，不能据此替代 OCR 业务验收。
- 五个已删 GCS bucket 原有 soft delete 为 604800 秒（7 天），此次没有绕过保留期。逻辑删除成功不等于云端立即物理擦除或所有旧费用清零。
- 原有源码 bundle 验证通过；两个仓库原工作目录没有业务代码修改。文档只运行 diff 检查和提交钩子，不跑业务全套测试或重部署新容器。

### 第二轮回读（2026-09-24 00:47 Asia/Bangkok）

- GCP `pearnly`：VM、磁盘、快照、快照策略、保留 IP、bucket、Artifact Registry、Secret Manager、有效 WIF pool、Run services/jobs、Scheduler 与 Cloud Tasks 均为空。Cloud Build 两处 trigger、Pub/Sub topic/subscription、转发规则、路由器和实例组也为空。
- 新平台服务器的环境、部署配置、systemd/cron 与 Site 配置没有引用此次旧资源；仅测试文件包含 `work.pearnly.com` 示例。新平台镜像来自 GHCR，不依赖已删 Google 镜像库。
- 新平台仍运行 12 个容器；testdms、ris 的 `/desk` 正常跳转统一登录并返回 200，OIDC discovery 返回 200。
- 新平台 OCR 凭据文件 SHA-256 与删除前相同，容器内服务账号认证通过；`pearnly-ocr-sa` 仍启用，三个项目角色与删除前一致，Vertex AI / Document AI / Vision / IAM Credentials API 和计费仍启用。没有执行付费模型调用，未代签 OCR 业务验收。
- 删除凭证与前后资源清单位于本机私有证据目录的 `remaining-google/`。旧账号删除后遗留的项目 IAM 条目已清理，仅移除已核验旧身份：前两项单独移除成功，后续单项请求遇到 Google 对已删除主体的校验错误，改为从最新策略精确删除剩余八项并保留 etag 提交。回读与预期策略完全一致，其他成员及 OCR 三个角色不变。
- 旧仓本地与 GitHub master 均为 `9aa7a6b2cb7ea629868d5cbcb28cba5260c37a7d`，源码 bundle 验证通过。本轮没有删除或修改本地/Git 源码，没有部署或重启新平台。

## 保留对象与后续条件

| 对象 | 用途 / 所属 | 保留原因与下次复核条件 |
| --- | --- | --- |
| SSD Nodes 新平台、GCP `pearnly`、OCR API/服务账号、域名及现有路由 | 当前新平台 | 用户明确保留；只有另行迁移/退役授权才清理 |
| 本机 `pearnly-archives` | 源码归档、历史证据与本次删除凭证 | 本次清理 Google 旧资源，未扩大到本地归档；迁移任务结束后复核 |
| 旧源码目录、固定来源副本、Cowork/ERP 工作树及实验室数据 | 进行中的迁移任务 | 仍有未集成成果；各任务完成迁移并验证保存后自行复核 |
| 项目共享默认网络、默认/托管服务身份、已启用 API 与计费 | OCR 及项目基础设施 | 不将旧业务退役扩大为共享基础设施清理；只有新平台依赖迁移后另行复核 |
| 其他 Google 项目 `project-3933c6ec-c1cd-498c-b86`、`project-40c21e2c-4975-4027-944`、`gen-lang-client-0397119443` | 两个 My First Project 与 Default Gemini Project | 未证明属于本次旧 Pearnly 平台；保留，需明确用途后才能纳入其他清理 |

## 当前限制与收尾

- Supabase 删除不会自动撤销历史已泄露的外部凭据；本次未证明存在泄露，也未轮换新平台复用的 LINE/OCR 凭据。
- 域名和 Cloudflare 路由不在本次确认的删除清单。旧网站地址在旧服务删除后可能不可用；不擅自给新平台接管旧入口。
- 旧 Supabase 和 Cloud Run 专用运行/存储清理已完成；第二轮进一步清理旧 Google 服务器、备份和部署资源。未宣称完成全 GCP 账户退役或历史泄露调查。
- 本记录合回本地 main；不推送主线原有的 58 个待推送提交。核验时远端 main 为 `ac33e0c7362915ae314295cb26f7748cb5589de0`，本任务远端分支不存在。收尾删除本任务专属工作树和本地分支，保留本机私有证据目录。
- 第二轮记录同样合回本地 main；不代推原有 59 个待推送提交。远端 main 再次回读仍为上述 SHA，`codex/legacy-google-cleanup` 远端分支不存在；本轮专属工作树和本地分支在确认合入、无未保存文件及运行占用后删除，私有删除凭证保留。
