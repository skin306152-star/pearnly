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
- **一个来源**：`pearnly_core/originals/rules.py` 新增 `UPLOAD_MAX_BYTES = 40MB`（一份文件；主会话 11-10 审定时由 30 提到 40，给密扫描留余量）和 `UPLOAD_REQUEST_MAX_BYTES = 41MB`（请求体 = 文件 + 1MB 外壳余量）。
- Frappe：新模块 `originals/upload_limits.py` 把 bench 公共配置的 `max_file_size` 写成 41MB。
  - 生产：三个产品激活时都跑（`activate-product.sh`，在迁移和重启 web 之前，幂等）。
  - 实验栈：`lab_init.py`。测试机：`finish-start.py`。三处写的是同一个函数的结果。
- 页面：`www/assistant.py` 把共用常量写进页面（`window.pearnly_upload_max_bytes`），前端只读这个数、不另写。
  - 超过上限的文件在页面当场拦下，不白传 30 秒。
  - 回 413（网关或 Frappe 给的是网页、没有原文）时也照实说「文件超过 40 MB」。
- 接口：CC 附件、代录资料区超限时回同一句（四语词条 `upload.tooLarge`），不再是英文的「file too large」或「Invalid request」。
- 代录资料区与 ERP 对话收单直接取共用常量（ERP 由 20MB 提到 40MB；LINE 下载另有自己的 20MB 上限，不变）。
- 宿主机租户站 nginx：模板改成 41m。宿主机部署目录只有 `infrastructure/production`、`keycloak/themes`、`scripts/production`（frappe.Dockerfile 130–132），读不到应用代码，所以这里写同一个数，由测试核对。
- CC 契约（`assistant/contracts.py`）是零依赖文件，有 6 个测试单独加载它，同样写同一个数、由测试核对。

## 主会话审的四点
1. **同一个来源**：Frappe 与页面直接取 `originals.rules`；宿主机 nginx 和 CC 契约两处读不到它，各写一份同样的数，`tests/test_upload_limits.py` 在 CI 里逐个核对等于共用常量，改一处没改另一处就红。
2. **50 页扫描件多大、余量多少**：压测那份 50 / 53 页是 27.7MB，约 0.55MB / 页；另两份 50 页、53 页各 21MB。
   - 30MB 对 27.7MB 只有约 8% 余量，主会话审定提到 40MB：按 0.55MB / 页约 72 页，每页 0.8MB 的密扫描 50 页也收得下。
   - 下一个硬限是甲骨文原件服务：原件转 base64 装进 JSON，请求体上限 60MB（`claude_ocr/server.py`），文件最多约 44MB；40MB 在它以内。
3. **以后新开的租户会不会自动带上**：会。`max_file_size` 写在 bench 公共配置里（一个产品组的所有站点共用），新站点建在同一个 bench 上就直接生效。开户的 `site-step.py` 写公共配置时是读出来再更新，不会把它冲掉。新租户的宿主机 nginx 由开户按同一个模板生成，就是 41m。新开一个产品组的话，第一次激活就写上。
4. **部署时要不要重载 nginx、怎么回读**：不用另外做。激活最后一步 `refresh-tenant-nginx.py` 会按模板重写内容有变的租户站配置，`nginx -t` 通过才 `systemctl reload nginx`，不通过就全部还原。部署回读 `deploy-readback.sh` 新增一段「upload limits」：
   - 三个产品的 bench `max_file_size` 等于镜像里的 `UPLOAD_REQUEST_MAX_BYTES`；
   - 每个租户站 nginx 的 `client_max_body_size` 不小于它；
   - 对宿主机上有配置的测试租户（站名以 test 开头），本机直连 nginx，不登录交一份正好 40MB 的文件：回 403 说明三层都收下了（表单解析完才轮到验登录），回 413 就报问题。这一步只读、什么都不写；正式租户不探。

## 40MB 的内存与超时（11-10 实测，主会话要求核）
- 生产镜像里一份 39.9MB、50 页的 PDF，每步单独一个进程量峰值：传引擎 multipart +75MB（SDK 自己复制，交文件对象反而 +114MB，维持现状）；
  读页数 +20MB；**存原件原来 +158MB**（base64 文本、json.dumps、编码各一份，真的经 requests 发出去量的）。
- 生产此刻 Cowork 网页容器匿名内存 1.51 / 1.5GiB（99%，撞上限 2684 次、在用 swap），ERP 92%。所以先减拷贝再上 40MB（主会话选 A）：
  存原件的请求体改成边读边编码的流（`originals.oracle.JsonUpload`，知道总长度、照常带 Content-Length，甲骨文收到的与原来逐字节相同），
  `claude_ocr.client.internal` 遇到可读的请求体就用 `data=` 发。实测发送时 **+158MB → +9MB**。
- 改后一份 40MB 的代录上传瞬时最多约 +104MB（原来 40MB 要 +273MB，30MB 时 +205MB）。网页容器上限本身另开分支调（主会话已批）。
- 超时：两层 nginx 收齐请求体才交后端，用户传得慢不占后端时间；gunicorn 是 gthread，单个请求受 `proxy_read_timeout 120s` 限；
  服务端估 20–40 秒（11-08 实测 10.6MB 串行 13.4 秒，现在传引擎与存原件并行），上线后用 U0 计时日志核。甲骨文容器 4GB。

## 验证
- 先红后绿：`tests/test_upload_limits.py` 10 条，在旧代码上 10 条全红（413 那条正是线上看到的「Something went wrong. Try again.」），改后全绿。
- 实验栈真 Frappe 对照（不登录 POST，回 403 = 请求体已收下）：
  | bench 配置 | 24MB | 28MB | 30MB | 33MB |
  |---|---|---|---|---|
  | 没配 `max_file_size`（修复前） | 403 | **413** | — | — |
  | `upload_limits.bench_values`（31MB） | — | 403 | 403 | 413 |
- 部署回读：`scripts/production/test-deploy-run.py` 38 条过，新加一条分别造 bench 没写、nginx 40m、上传回 413 三种情况，都会被抓出来。
- 存原件流式请求体：先红后绿 3 条（交出去的是流、各种边界长度下与原来的 JSON 相同、读完 40MB 峰值 < 16MB）。
- 本地闸门（gate.sh）：见提交前那次；`check-architecture`、`check-copy`（新增 2 条、拦截 0）、`i18n-build --check` 都过。
- 流程 `--changed`（7 个流程，new / old 两套数据 × 电脑 / 手机）：24 / 24 过。
- 候选离线（生产镜像 fb1037ad，`cand-local.sh offline`）：107 组，LANE EXIT 0（含要 Frappe 环境的 test_heic_uploads 等）。

## 11-11 补：CC 桌面版文件夹与 LINE 也是 40MB（C 线新窗口）

主会话定：用户能交文件的地方一律同一个上限（文件夹选 A、LINE 选 A）。

| 入口 | 上限 | 超限表现 | 落点 |
|---|---|---|---|
| 网页（Cowork / ERP 页面上传、代录资料区） | 40MB | 页面当场拦下；接口回「文件超过 40 MB」（`upload.tooLarge`） | 上面「改法」 |
| CC 对话（电脑、手机同一个页面） | 40MB | 同上，页面读服务端给的同一个数 | `www/assistant.py`、`assistant/files.py` |
| LINE 发单据（ERP、Cowork） | 40MB（原来是下载函数默认的 20MB） | 回同一句 `upload.tooLarge`（原来只说「没能保存这份文件」）；ERP 这条记 `too_large`、不记错误日志 | `erp_portal/integrations/intake.py` `_fetch`、`cowork/line/intake.py` `_admit` |
| CC 桌面版「从文件夹取文件」 | 40MB（原来 30MB），随 CC 桌面版 1.12.20 | 电脑回 `too_large`，CC 对模型说取不成、原因太大 | `cc_desktop/wire/request_contracts.py` 与 `companion/request_contract.py` 同值（`test_companion_contract` 绑定对话附件上限） |

- DMS 的 LINE 下载有自己的上限（身份证图片），不变；下载函数默认值仍是 20MB，只有 ERP、Cowork 两处显式传 40MB。
- 旧版 CC 桌面版（1.12.19 及以前）仍按 30MB 拦：服务端每件请求带 `max_bytes`，电脑取两者较小的，30–40MB 的文件回 `too_large`，不会半截；心跳带新版本号，CC 桌面版自动更新后就是 40MB。
- 测试（先红后绿）：ERP `test_line_intake`、Cowork `tests/test_cowork_line_intake.py` 各两条——正好 40MB 收下并交存原件；多 1 字节（带 / 不带 Content-Length 两种）不存、回 `upload.tooLarge`。旧代码上正好 40MB 那条红（回「没能保存这份文件」）。CC 桌面版 `test_folder_fetch` 与 `test_companion_contract` 跟着 40MB。

### 真机记录：`erp.line.postback.erp_item`（2026-11-11，被测代码 2db4b2a08）

改 `pearnly_core/line.py`（下载改 BytesIO）命中共用件 line，这一行只能真机。主会话 11-11 批借测试机与三星、识别费约 ฿1 以内。

- 环境：测试机 `testenv.sh lab-start erp`，线上镜像 5078ed47 + 本分支实时源码（工作树 HEAD 2db4b2a08）；追加甲骨文 6 个变量后 `live erp`，
  识别容器带上变量、`originals.store.where` 回 `oracle`、testerp 本来就是 Claude 引擎；testerp 解析切到测试机隧道。
  第一次 lab-start 在收尾导入 `upload_limits` 失败（镜像比源码旧），修收尾脚本（2db4b2a08，见下）后重跑通过，bench `max_file_size` 41MB。
- 设备：三星 SM-A146P，LINE「Skin」（绑 testerp aksarapak，只在 ERP-TEST 对话里操作，没解绑没重绑；LINE 显示语言是真机补测窗口临时设的日文，没动）。
- 测试票：本机生成的虚构税票（卖方「ห้างหุ้นส่วนจำกัด ทดสอบยางยนต์」、税号 0313599999991，两行新物料 + 运费，合计 16,371.00），
  加不可压缩的内嵌附件撑到正好 30MB（31,457,280 字节）与 42MB，页数 1。

| 步骤 | 结果 |
|---|---|
| 富菜单「采购录入」→ 上传识别 → 打字 mr.erp 选公司（没接 Express）→ 按票面识别 → 已付款 → 现金 | 每步按序回，最后「请上传 PDF 或图片」 |
| 11:30:48 经 Android 分享把 30MB PDF 发到 ERP-TEST | 11:31 回复核卡（单号 TS6911-0301、卖方与税号对、三行、合计 16,371.00、现金），随后按行追问「新物料：1、2」+ 全部新建并入库 / 全部只记账 / 逐项选择 / 取消（运费已预选只记账） |
| 点「全部只记账」 | 回更新后的卡片：三行都是「只记账」——`erp_item` 按钮照常 |
| 同样流程发 42MB PDF（11:34） | 回「文件超过 40 MB」（`upload.tooLarge`，资料语言中文） |
| 只读查测试机库 | LI-158414 `ready`、原件登记 352 字节（原件在甲骨文）；LI-158415 `failed` / `too_large`；Error Log 0 条；erp-ocr-worker 11:27 起内存峰值 316MiB |

- 收尾：两条收单与原件登记已删（连甲骨文原件）；env 恢复成追加前（甲骨文变量 0）；`lab-stop erp`，testerp 读回 107.155.65.74、线上调度恢复；
  手机上推的两份 PDF 已删（文件与媒体库都读回不存在）；测试机临时脚本与任务 0；占用表已登记释放。截图在共享笔记「C线-真机-11-11」。
- 没测到：Cowork 的 LINE（这行只要 ERP；Cowork 40MB 边界由 `tests/test_cowork_line_intake.py` 离线覆盖）；「逐项选择」「新建并入库」分支（11-11 上午真机补测已走过，这次没改那段）。

### 测试机收尾脚本（真机时发现，2db4b2a08）

`lab-start` 先只用线上镜像起栈收尾、再挂实时源码收尾一次。镜像比本分支旧时，第一次收尾导入 `pearnly_core.originals.upload_limits` 失败，整个
lab-start 停下、域名没切——合入 main 后、新镜像上线前，谁用线上镜像 + main 源码起测试机都会卡在这里。改成镜像里没有这个模块就跳过并记一行，
挂上源码那次再写（与线上没换镜像时一样是 25MB）。测试 `test_upload_limits` 两条（有 / 没有这个模块），旧代码上「没有」那条红（同一个 ImportError）。

### 内存（主会话要求按减拷贝口径核）

- LINE 下载原来 `bytearray` 再转 `bytes`，两份；改成 `BytesIO` 取值不再复制。生产镜像实测 40MB：**峰值 +81MB → +41MB**。下游（ERP 存原件 `registered.put`、Cowork `files.service.create`）都只收字节，没法直接流式写盘；存原件那一步已是流式（+9MB）。
- 文件夹取文件在服务端：电脑按 4MB 一块交回、逐块写站点私有目录；取进对话时整份读一次（40MB），再交引擎（SDK 复制，约 +75MB，同对话附件），留存直接写盘。没有 base64 / JSON 再拷贝，一份约 +115MB，与对话附件同量级。
- 并发最坏情况写进容器内存那份记录（`docs/work/2026-11-11-web-container-memory.md` 第 7 节）。

## 共用件连带回归清单
命中共用件 `ocr`（原件层：`rules.py` 只新增两个常量，新增 `upload_limits.py`，原有函数没改）和 `assistant`（只改附件上传的大小检查，页面多写一个数）。

| 入口 | 方式 | 结果 |
|---|---|---|
| `cc.assistant` | manual | 通过：流程 cc.chat 4/4、cc.attach-delete 2/2、cowork.cc.attach-delete 2/2、cc.settings 4/4；实验栈真 Frappe 28 / 30MB 收下、33MB 回 413 |
| `cc.operator.page` | offline | 通过：test_cc_operator 12 条；流程 cc.operator 4/4、cc.operator.replay 4/4 |
| `cc.operator.thread` | manual | 离线通过（test_express_operator_feed 20、test_express_operator_tasks 21、test_cc_operator 12）；实机没测到（原因：这次没改任务对话，只改资料区上传的大小检查） |
| `erp.line.chat` / `erp.line.item-questions` / `erp.line.intake.submit` / `erp.line.binding` | manual | 流程 erp.line.card-lines 4/4 过；其余没测到（原因：只把 ERP 收单的大小上限由 20MB 改为共用的 30MB，LINE 下载自己的 20MB 上限不变，LINE 发来的文件走不到新上限） |
| `erp.ocr` / `cowork.ocr` / `dms.ocr.id-card` / `shared.ocr.pipeline` / `shared.originals.download` | manual | 没测到（原因：原件层只新增常量和一个写配置的模块，识别与原件的函数没改；付费识别不在本机跑）；候选离线见下 |
| `erp.stock-count.upload` / `erp.stock-in.attach` / `erp.stock-out.attach` | manual | 没测到（原因：这几处有自己的 20MB 上限（`recognition/originals.py`），这次没改；bench `max_file_size` 由 25 提到 41MB 只放宽请求体、不会收紧） |
| `erp.cc.troubleshooting` / `cowork.cc.troubleshooting` | manual | 没测到（原因：同识别；补传文件进原单走 CC 附件上传，已由 cc.assistant 覆盖） |
| `erp.cc.chat-push` / `erp.cc.stocktake` / `erp.cc.companies` / `cowork.cc-chat.push` / `cowork.assistant` / `control.assistant` / `shared.assistant` | offline / manual | 没测到（原因：因为命中 assistant 共用件被列出，这次 assistant 只改了附件上传的大小检查，这些入口的工具与页面没改） |
| `shared.page-errors` / `shared.login` / `shared.team.members` / `shared.execution` / `erp.execution` / `shared.exports` / `erp.entry.choices` | manual | 没测到（原因：只因同文件里的调用方被列出，本次改动不经过这些路径） |

### 11-11 补：新命中的共用件 line、cc-desktop、cc-request-worker（`check-impact.py`，被测 c2c03e97b）

本轮改动：`pearnly_core/line.py` 只改 `download_message_content` 的缓冲（BytesIO，返回值仍是 bytes，上限判断不变）；
`cc_desktop/wire/request_contracts.py` 与 CC 桌面版 `request_contract.py` 只把 `folder_fetch` 的 `MAX_BYTES` 由 30 改 40 MiB；
ERP / Cowork LINE 收单显式传 40MB、超限回 `upload.tooLarge`。流程 `--changed` 7 条 24/24（实验栈 pearnly-cline-lab，含 cc.chat 四格）。

| 入口 | 方式 | 结果 |
|---|---|---|
| `erp.line.chat`、`erp.line.item-questions`、`erp.line.intake.submit` | manual | 通过：测试机 + 三星真机（见上「真机记录」）：30MB 出卡片、按行追问点选生效，42MB 回「文件超过 40 MB」；卡片提交那一步没点（没改） |
| `cowork.line.chat`、`cowork.line.item-questions` | manual | 离线通过（`tests/test_cowork_line_intake.py` 32 条：正好 40MB 存原件、多 1 字节带 / 不带 Content-Length 回 `upload.tooLarge`）；真机没测到（原因：这次只借了 ERP 栈，产品地图只要求 ERP 那一行，主会话按此批的） |
| `erp.line.webhook-sim`、`cowork.line.webhook-sim`、`dms.line.webhook-sim` | auto | 没测到（原因：回复、推送、三个点、webhook 验签都没改；下载由上面两组测试经同一个 `download_message_content` 实跑） |
| `dms.line.chat`、`dms.line.scheduled`、`dms.roster.line-oa` | manual | 离线通过：候选离线 107 组（生产镜像 5078ed47）里的 DMS LINE 运行时、首读、身份证读法各组；DMS 的上限照旧由调用方传（`out.download_image`），默认 20MB 不变 |
| `erp.line.stocktake`、`erp.line.stocktake.liff-camera`、`cowork.line.stocktake.liff-camera`、`shared.desk.liff-boot`、`control.line.channel`、`erp.line.binding`、`cowork.line.binding` | manual / offline | 没测到（原因：盘点 LIFF、登录、频道保存、绑定都不经过下载函数；只因同在共用件 line 被列出） |
| `cc.desktop.requests`、`cc.desktop.bridge` | manual | 离线通过：`test_companion_contract` 90 条（两边常量相等、等于对话附件上限）、`test_cowork_companion_requests`、CC 桌面版 `test_folder_fetch` / `test_request_worker` 28 条；实机没测到（原因：要 CC 桌面版 1.12.20 安装包，随下一版发；旧版按 30MB 回 `too_large`，服务端每件带 `max_bytes`） |
| `cc.desktop.places`、`cc.desktop.operator`、`erp.cc.connect`、`erp.cc.write`、`erp.cc.masters`、`erp.cc.stock-void`、`cowork.cc.channel`、`erp.cc.companies`、`erp.cc.chat-push`、`cc.mrerp-login`、`erp.mrerp.direct`、`cowork.express.push`、`cowork.cc.mrerp-setup`、`cowork.execution`、`cowork.tax-documents` | manual / offline / auto | 没测到（原因：只因同文件 `request_contracts.py` 被列出；只动了 `folder_fetch` 一个大小常量，其余种类的上限、租约、通道没改；闸门与候选离线全过） |


上线后要人做的一项（主会话 11-10 交给发布窗口）：在 testcowork 真登录，放一份约 38MB 的扫描 PDF，看能收下、能识别；再放一份约 42MB 的，看页面说「ไฟล์มีขนาดเกิน 40 MB」。部署回读里的不登录探测只证明请求体能过，不代替这一项。

## 生产
- 随下一批部署，不单独发。激活时自动写 bench 配置、按模板刷新租户站 nginx 并重载，回读自动核对；不用手工改生产。
- 没动容器 nginx（默认 50m 已经够），没动 Cloudflare，没动甲骨文。
