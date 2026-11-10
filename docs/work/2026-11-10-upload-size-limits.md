# 上传大小各层对齐（11-10 线上压测问题 2）

分支 `pearnly/upload-size-limits`，基线 origin/main 81ec70ae3。主会话 11-10 派（C 上传线）。

## 现象
线上压测 S3′：CC 对话附件放一份 27.7MB 的 50 页扫描 PDF，服务端回 413，附件变红，只显示「出错了，请再试一次」，话没发出去。
CC 给模型的工具说明写的是一份最多 30MB。

## 根因：一份文件要过五层，最紧的那层说了算
| 层 | 原来的上限 | 来源 |
|---|---|---|
| Cloudflare 免费版 | 100MB | 平台固定 |
| 宿主机租户站 nginx | 30m（整个请求体） | `infrastructure/production/provision-agent.py` 的 `nginx()` |
| 容器 nginx | 50m（没设变量，取镜像默认） | 镜像 `nginx-entrypoint.sh` |
| **Frappe** | **25MB（整个请求体）** | `frappe/app.py` `init_request`：除自带的 `upload_file` 外，都按 bench 配置 `max_file_size`，没配就是 25MB |
| CC 附件 / 代录资料区 | 30MB | `assistant.contracts.UPLOAD_MAX_BYTES`、`express_operator.api.UPLOAD_MAX` |
| ERP 对话收单（CC 交识别） | 20MB | `integrations/intake.py` |

Frappe 在调任何钩子之前（`before_request` 在第 244 行，解析表单在第 239 行）就解析表单，所以接口里放宽不了，只能写 bench 配置。
即使 Frappe 放开，正好 30MB 的文件加上 multipart 外壳也会撞上宿主机 nginx 的 30m；ERP 里 20–30MB 的文件进了对话，交识别时还会被拒。

## 改法
- **一个来源**：`pearnly_core/originals/rules.py` 新增 `UPLOAD_MAX_BYTES = 30MB`（一份文件）和 `UPLOAD_REQUEST_MAX_BYTES = 31MB`（请求体 = 文件 + 1MB 外壳余量）。
- Frappe：新模块 `originals/upload_limits.py` 把 bench 公共配置的 `max_file_size` 写成 31MB。
  - 生产：三个产品激活时都跑（`activate-product.sh`，在迁移和重启 web 之前，幂等）。
  - 实验栈：`lab_init.py`。测试机：`finish-start.py`。三处写的是同一个函数的结果。
- 页面：`www/assistant.py` 把共用常量写进页面（`window.pearnly_upload_max_bytes`），前端只读这个数、不另写。
  - 超过上限的文件在页面当场拦下，不白传 30 秒。
  - 回 413（网关或 Frappe 给的是网页、没有原文）时也照实说「文件超过 30 MB」。
- 接口：CC 附件、代录资料区超限时回同一句（四语词条 `upload.tooLarge`），不再是英文的「file too large」或「Invalid request」。
- 代录资料区与 ERP 对话收单直接取共用常量（ERP 由 20MB 提到 30MB；LINE 下载另有自己的 20MB 上限，不变）。
- 宿主机租户站 nginx：模板改成 31m。宿主机部署目录只有 `infrastructure/production`、`keycloak/themes`、`scripts/production`（frappe.Dockerfile 130–132），读不到应用代码，所以这里写同一个数，由测试核对。
- CC 契约（`assistant/contracts.py`）是零依赖文件，有 6 个测试单独加载它，同样写同一个数、由测试核对。

## 主会话审的四点
1. **同一个来源**：Frappe 与页面直接取 `originals.rules`；宿主机 nginx 和 CC 契约两处读不到它，各写一份同样的数，`tests/test_upload_limits.py` 在 CI 里逐个核对等于共用常量，改一处没改另一处就红。
2. **50 页扫描件多大、余量多少**：压测那份 50 / 53 页是 27.7MB，约 0.55MB / 页；另两份 50 页、53 页各 21MB。
   - 30MB 对 27.7MB 只有约 8% 余量；按 0.55MB / 页，30MB 约 54 页。识别本身最多 50 页，所以按这个扫描密度，大小不会先卡住。
   - 更密的扫描（彩色 300dpi，每页超过 0.6MB）50 页会超过 30MB，会在页面当场说「文件超过 30 MB」。
   - 要再放宽，下一个硬限是甲骨文原件服务：原件转 base64 装进 JSON，请求体上限 60MB（`claude_ocr/server.py`），文件最多约 44MB。提到 40MB 只需要改共用常量（外加 nginx 和契约里的两份数）；代价是网页进程每份要多占约 10MB 内存、传引擎多约 5 秒。这次没提，等真有超 30MB 的扫描件再定。
3. **以后新开的租户会不会自动带上**：会。`max_file_size` 写在 bench 公共配置里（一个产品组的所有站点共用），新站点建在同一个 bench 上就直接生效。开户的 `site-step.py` 写公共配置时是读出来再更新，不会把它冲掉。新租户的宿主机 nginx 由开户按同一个模板生成，就是 31m。新开一个产品组的话，第一次激活就写上。
4. **部署时要不要重载 nginx、怎么回读**：不用另外做。激活最后一步 `refresh-tenant-nginx.py` 会按模板重写内容有变的租户站配置，`nginx -t` 通过才 `systemctl reload nginx`，不通过就全部还原。部署回读 `deploy-readback.sh` 新增一段「upload limits」：
   - 三个产品的 bench `max_file_size` 等于镜像里的 `UPLOAD_REQUEST_MAX_BYTES`；
   - 每个租户站 nginx 的 `client_max_body_size` 不小于它；
   - 对宿主机上有配置的测试租户（站名以 test 开头），本机直连 nginx，不登录交一份正好 30MB 的文件：回 403 说明三层都收下了（表单解析完才轮到验登录），回 413 就报问题。这一步只读、什么都不写；正式租户不探。

## 验证
- 先红后绿：`tests/test_upload_limits.py` 10 条，在旧代码上 10 条全红（413 那条正是线上看到的「Something went wrong. Try again.」），改后全绿。
- 实验栈真 Frappe 对照（不登录 POST，回 403 = 请求体已收下）：
  | bench 配置 | 24MB | 28MB | 30MB | 33MB |
  |---|---|---|---|---|
  | 没配 `max_file_size`（修复前） | 403 | **413** | — | — |
  | `upload_limits.bench_values`（31MB） | — | 403 | 403 | 413 |
- 部署回读：`scripts/production/test-deploy-run.py` 38 条过，新加一条分别造 bench 没写、nginx 30m、上传回 413 三种情况，都会被抓出来。
- 本地闸门（gate.sh）：见提交前那次；`check-architecture`、`check-copy`（新增 2 条、拦截 0）、`i18n-build --check` 都过。
- 流程 `--changed`（7 个流程，new / old 两套数据 × 电脑 / 手机）：24 / 24 过。
- 候选离线（生产镜像 fb1037ad，`cand-local.sh offline`）：107 组，LANE EXIT 0（含要 Frappe 环境的 test_heic_uploads 等）。

## 共用件连带回归清单
命中共用件 `ocr`（原件层：`rules.py` 只新增两个常量，新增 `upload_limits.py`，原有函数没改）和 `assistant`（只改附件上传的大小检查，页面多写一个数）。

| 入口 | 方式 | 结果 |
|---|---|---|
| `cc.assistant` | manual | 通过：流程 cc.chat 4/4、cc.attach-delete 2/2、cowork.cc.attach-delete 2/2、cc.settings 4/4；实验栈真 Frappe 28 / 30MB 收下、33MB 回 413 |
| `cc.operator.page` | offline | 通过：test_cc_operator 12 条；流程 cc.operator 4/4、cc.operator.replay 4/4 |
| `cc.operator.thread` | manual | 离线通过（test_express_operator_feed 20、test_express_operator_tasks 21、test_cc_operator 12）；实机没测到（原因：这次没改任务对话，只改资料区上传的大小检查） |
| `erp.line.chat` / `erp.line.item-questions` / `erp.line.intake.submit` / `erp.line.binding` | manual | 流程 erp.line.card-lines 4/4 过；其余没测到（原因：只把 ERP 收单的大小上限由 20MB 改为共用的 30MB，LINE 下载自己的 20MB 上限不变，LINE 发来的文件走不到新上限） |
| `erp.ocr` / `cowork.ocr` / `dms.ocr.id-card` / `shared.ocr.pipeline` / `shared.originals.download` | manual | 没测到（原因：原件层只新增常量和一个写配置的模块，识别与原件的函数没改；付费识别不在本机跑）；候选离线见下 |
| `erp.stock-count.upload` / `erp.stock-in.attach` / `erp.stock-out.attach` | manual | 没测到（原因：这几处有自己的 20MB 上限（`recognition/originals.py`），这次没改；bench `max_file_size` 由 25 提到 31MB 只放宽请求体、不会收紧） |
| `erp.cc.troubleshooting` / `cowork.cc.troubleshooting` | manual | 没测到（原因：同识别；补传文件进原单走 CC 附件上传，已由 cc.assistant 覆盖） |
| `erp.cc.chat-push` / `erp.cc.stocktake` / `erp.cc.companies` / `cowork.cc-chat.push` / `cowork.assistant` / `control.assistant` / `shared.assistant` | offline / manual | 没测到（原因：因为命中 assistant 共用件被列出，这次 assistant 只改了附件上传的大小检查，这些入口的工具与页面没改） |
| `shared.page-errors` / `shared.login` / `shared.team.members` / `shared.execution` / `erp.execution` / `shared.exports` / `erp.entry.choices` | manual | 没测到（原因：只因同文件里的调用方被列出，本次改动不经过这些路径） |

上线后要人做的一项：在 testcowork 真登录，放一份 28MB 左右的 50 页扫描 PDF，看能收下、能识别；再放一份超过 30MB 的，看页面说「ไฟล์มีขนาดเกิน 30 MB」。部署回读的不登录探测只证明请求体能过，不代替这一项。

## 生产
- 随下一批部署，不单独发。激活时自动写 bench 配置、按模板刷新租户站 nginx 并重载，回读自动核对；不用手工改生产。
- 没动容器 nginx（默认 50m 已经够），没动 Cloudflare，没动甲骨文。
