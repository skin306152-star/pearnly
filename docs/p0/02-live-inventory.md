# P0.02 现网只读盘点

日期：2026-09-17。方式：只读。没有读取任何 secret 的值，没有登录外部系统，没有改配置、没有重启、没有发布。

## 入口与域名

| 项 | 值 |
|---|---|
| 对外域名 | `pearnly.com`、`www.pearnly.com` |
| 边缘 | Cloudflare，实测两者均返回 HTTP/2 200，`server: cloudflare` |
| Cloudflare Worker | `pearnly-cloud-run`，account `7dc339bc60e52e97464b8eff4638b995`，compat date `2026-09-05` |
| Worker 路由 | `pearnly.com/*`、`www.pearnly.com/*`（zone `pearnly.com`） |
| 源文件 | `pearnly-app/deployment/cloudflare/wrangler.jsonc`、`worker.js` |

## Cloud Run（project `pearnly`，region `asia-southeast1`）

| 服务 | 镜像 | 最新就绪修订 | 创建时间 |
|---|---|---|---|
| `pearnly-web` | `…/pearnly-app/app@sha256:224f0f80…` | `pearnly-web-4f1cbab9f740-s3` | 2026-09-17T03:03:14Z |
| `pearnly-worker` | 同上（同一 digest） | `pearnly-worker-4f1cbab9f740-s3` | 2026-09-17T03:02:10Z |

web 与 worker 同镜像同修订后缀 `4f1cbab9f740`，是一次一致发布。最近三次修订都在 2026-09-16 至 09-17 之间。

环境变量**只记录名称**，未读取值：`PEARNLY_RUNTIME_ROLE`、`PEARNLY_RUNTIME_ENV_FILE`、`PEARNLY_OCR_CACHE_DIR`、`RECON_AI_MAPPING_CACHE_DIR`、`GOOGLE_CLOUD_PROJECT`、`GOOGLE_CLOUD_LOCATION`、`PEARNLY_WORKER_URL`、`LINE_DMS_A_CREDENTIALS`、`LINE_DMS_B_CREDENTIALS`。其中后两项指向 Secret Manager。

## 任务、队列与定时

| 类型 | 名称 | 关键参数 |
|---|---|---|
| Cloud Run Job | `pearnly-schema` | 数据库 schema 初始化（发布时串行执行） |
| Cloud Scheduler | `pearnly-dms-master-refresh` | `* * * * *`，ENABLED |
| Cloud Scheduler | `pearnly-background-recovery` | `*/5 * * * *`，ENABLED |
| Cloud Tasks 队列 | `pearnly-background` | RUNNING，max rate 1.0/s |

## 数据与存储

- **没有 Cloud SQL**（该 API 未启用）——数据库确实在外部，与本计划记录一致。
- 5 个 GCS 桶，均在 `ASIA-SOUTHEAST1`：`pearnly-app-backups-112074003592`、`pearnly-app-build-…`、`pearnly-app-files-…`、`pearnly-app-installers-…`、`pearnly-app-temp-…`。
- 镜像仓库：`asia-southeast1-docker.pkg.dev/pearnly/pearnly-app`（DOCKER）。
- Secret Manager 5 项（只记录名称）：`pearnly-web-env`、`pearnly-worker-env`、`pearnly-work-env`、`pearnly-line-dms-a`、`pearnly-line-dms-b`。

## 常驻计算

| 资源 | 状态 | 说明 |
|---|---|---|
| VM `pearnly-work` | **RUNNING** | `e2-medium`，`asia-southeast1-b`，公网 IP `136.85.65.80`，创建于 2026-09-11，label `component=collaboration` → 即计划所指的 WeKan 机器 |
| 盘 `pearnly-work` | READY | 20GB |
| 盘 `pearnly-work-data` | READY | 30GB |

计划把 `pearnly-work` 列为 P8 退役对象。**本轮不动**。

## 由启用 API 反映的外部依赖

`documentai`、`aiplatform`、`drive`、`sheets`、`bigquery`、`storage`、`cloudtasks`、`cloudscheduler`、`secretmanager`、`run`、`compute`、`logging`、`monitoring`、`iap`、`pubsub`、`artifactregistry`、`cloudbuild`、`containerregistry`。

说明 OCR 走 Document AI、AI 映射走 Vertex（`aiplatform`）、并存在 Drive/Sheets 集成。这些是逐项迁移与替换的候选范围，不是本次结论。

## 旧 ERP 云项目（`project-d0fbd530-1ee3-436d-b58`，名 "pearnly erp"）

确认仍是"关而未拆"：Cloud Run API 未启用（无服务）；VM `pearnly-erp-dev` 状态 **TERMINATED**（未删除）；桶 `pearnly-erp-dev-backups-185154502120` 与 `project-d0fbd530-1ee3-436d-b58_cloudbuild` 仍在。

注意：本机 `gcloud` 的**当前默认 project 就是这个 ERP 项目**。任何不带 `--project` 的命令都会打到它上面，后续脚本必须显式指定项目。

## 本轮未盘点到的（缺口）

- **Cloudflare 的实际状态**：本机没有 `wrangler`、没有 API token、环境里也没有 CF 凭据，无法核对已部署 Worker 版本、DNS 记录、TLS 与缓存规则。需要 token 或登录后才能关这项。
- **Supabase**：外部服务，连接串在 `pearnly-web-env` 里，本轮未读取、未登录，实例规格、库大小、角色、RLS 现状均未知。
- **外部 ERP 与 LINE 渠道**：只知道存在两个 LINE DMS 凭据项，渠道数量、webhook 指向、外部 ERP 目标账套未核对。
- **`pearnly-schema` Job 的最近执行结果**、备份实际最近成功时间未核对。
- 本机 `~/.cloudflared/` 存在隧道凭据（`c502d3aa-…json`、`cert.pem`）但无 `config.yml`，**用途不明**，未使用、未修改。
