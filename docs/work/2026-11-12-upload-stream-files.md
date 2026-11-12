# 上传边收边写盘 · 第 2 步：上传接口不再把文件读成 bytes（ADR-028 第 2 步）

分支 `pearnly/upload-stream-files`，叠在第 1 步 `pearnly/upload-parse-stream`（0602842ca）之上；第 1 步合入后变基到 main。主会话 11-12 批准。
LINE 收单（后台容器，不占网页内存）后做，见文末保留项。

## 改了什么

- 原件层新增 `originals/local_file.py`：`LocalFile`（磁盘上的一份：路径、大小、按块读、摘要按块算）、`from_upload`（把请求里的上传按块拷到临时文件，
  超限回 None，响应发出后删）、`as_bytes` / `head` / `digest`。
- 各层同时接受 bytes 与 `LocalFile`：
  - `checks.inspect`：PDF 按路径交 pymupdf、图片按路径交 PIL、sha256 按块算；结论与 bytes 完全相同（测试逐项比）。
  - `oracle.JsonUpload`：按 3MB 段从文件读、编码，请求体与 bytes 那份逐字节相同。
  - `registered.put`、Cowork `files.service.create`：HEIC 只看文件头，是照片才整份读出来转（照片小）；站点没配原件层存本机 File 的老路才整份读。
  - `store.put`：谷歌存储（生产不走）仍交整份字节。
  - ERP `integrations.intake.receive_chat`：文件摘要按块算。
  - 引擎适配器 `upload`：交打开的文件句柄（SDK 交路径会整份读进内存；httpx 每次发送前把指针拨回开头，重试也是整份）。
- 四个上传口：
  - CC 附件（`assistant/files._upload`，电脑 / 手机同一个口）：werkzeug 的临时文件里量大小、看文件头，按块拷进本站留存位置（原来留存本来就写盘），
    之后交引擎、交代录任务、读页数都按留存那份读；页数交 pypdf 文件句柄（交路径它会整份读进内存，实测 +38MB）。
  - 代录资料区（`express_operator/api.intake`）、Cowork 录入（`cowork/files/api.upload`）：`local_file.from_upload` 落盘后往下传。
  - CC 桌面版文件夹取文件：`request_service.result_file` 按块核对长度与摘要后回存放那份（提交时才删，放进对话在提交前做完），
    `add_to_conversation` 按磁盘那份交引擎、留存。
- 测试：`tests/test_upload_files_on_disk.py`（候选离线车道，生产镜像里跑）、`tests/test_upload_limits.py` 新增源码守护 `FilesStayOnDisk`，
  旧的源码检查随写法更新（仍核「超限照样说多大、先于文件名检查」）。

## 内存（生产镜像，同款 40 页约 40MB 合成 PDF）

一份走完落盘 → 原件检查 → 交甲骨文请求体 → 交引擎（真实 HTTP 连接）→ 读页数：匿名内存峰值 **+2MB**（逐步：留存 0、检查 +1、甲骨文 0、引擎 +1、页数 0）。
旧写法整条路上占着整份 40MB；第 1 步合入前还要加 Frappe 收请求的 +152MB 峰值。实验栈并发实测见下方「验证」。

## 验证（11-12，被测 30fb5ad62，叠在第 1 步 882bae59b 上）

- 先红后绿：`tests/test_upload_files_on_disk.py`（生产镜像，6 条）在第 1 步代码上导入即红（没有 `local_file`）；`test_upload_limits.FilesStayOnDisk` 3 条在第 1 步代码上全红。
  随写法更新的旧测试：上传流替身改成真的流（读到结尾回 b""、能 seek，原来的替身每次 read 都回整份）、源码字面核对按新写法（仍核超限照样说多大、
  HEIC 每处都转）、`pearnly_core` 替身挂上真的 `local_file`（只依赖标准库）。
- 本地闸门 224 步过；候选离线 110 组 LANE EXIT 0（生产镜像 5078ed47）；分支 Development checks 绿。
- **实验栈内存实测**（同第 1 步的做法与 39MB 合成 PDF，实验栈挂本分支源码、网页进程经新入口）：

  | | 1 份 | 15 份同时 |
  |---|---|---|
  | 原样（第 1 步记录） | +192MB | +2968MB，15/15 |
  | 只有第 1 步 | +40MB | +617MB，15/15 |
  | **第 1 + 2 步** | **+0MB** | **+49MB（每份约 3MB）**，15/15 |

- 流程：`--changed` 5 条 16/16（cc.attach-delete、cc.chat、cc.operator、cowork.cc.attach-delete、erp.line.card-lines，状态回写 30fb5ad62）；
  登记表的覆盖没含这次改的上传接口，另手工补跑录入、代录、对账 10 条（cc.operator.replay、cowork.recon.run、cowork.intake.review / bulk-kind / actions / submit、
  cowork.keyboard.intake、cowork.operator.unknown、cowork.pending.operator、cowork.operator.send-while-waiting）31 格：首轮 30/31，
  cowork.intake.bulk-kind [new / desktop]「page steps timed out after 900s」无断言失败，补跑 20.6 秒通过，合计 31/31。
- 浏览器（内置浏览器，实验栈本分支）：Cowork 录入页 `frappe.pearnly.upload_original` 传 1.69MB 图片，来源文件大小、页数、sha256 对；
  CC 附件 1.5MB 上传后按编号下载逐字节一致；同一对话传 40MB + 1 字节，回 417「文件超过 40 MB」（超限提示照旧）。

## 共用件连带回归（`check-impact.py`：web-entry、ocr、assistant 三个共用件，39 个入口）

| 入口 | 结果 |
|---|---|
| `cc.assistant`、`shared.assistant`、`cowork.assistant` | 通过：流程 cc.chat、cc.attach-delete、cowork.cc.attach-delete；浏览器 CC 附件上传 / 下载 / 超限 |
| `cowork.intake.upload`、`cowork.ocr`、`cowork.execution` | 通过：流程 cowork.intake.submit / actions / review / bulk-kind、cowork.keyboard.intake；浏览器录入页上传 |
| `cc.operator.page`、`cc.operator.thread`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting` | 通过：流程 cc.operator、cc.operator.replay、cowork.operator.unknown、cowork.pending.operator、cowork.operator.send-while-waiting（代录资料区与输入框交任务都经磁盘那份） |
| `cowork.recon.upload` | 通过：流程 cowork.recon.run（对账上传本步没改，第 1 步入口下实测） |
| `erp.purchase.original-upload`、`erp.sales.original-upload`、`erp.stock-in.attach`、`erp.ocr`、`erp.execution` | 通过：第 1 步浏览器实测三处上传；本步只把 ERP 对话收单的摘要改成按块算，流程 erp.line.card-lines 通过；`test_line_intake` / `test_cc_intake`（候选离线）通过 |
| `shared.login`、`shared.execution` | 通过：第 1 步流程全套 361/361 与本步所有流程都先登录 |
| `cc.desktop.requests`、`cowork.cc.channel` | 离线通过：`test_cowork_companion_requests`（取文件交回磁盘那份）、`test_upload_files_on_disk`；实机没测到（原因：要 CC 桌面版连着，实验栈没有） |
| `erp.stock-out.attach`、`erp.stock-count.upload`、`erp.products.image-upload` | 没测到（原因：与已实测的入库附件 / Desk 原生上传同一个接口，本步没改它们） |
| `shared.ocr.pipeline`、`dms.ocr.id-card` | 没测到（原因：付费识别不在本机跑；原件检查对磁盘那份与 bytes 结论逐项相同已在镜像里比对；DMS 没改） |
| `shared.originals.download` | 没测到（原因：下载、预览没改；甲骨文收到的请求体与原来逐字节相同已比对） |
| `cowork.line.chat`、`erp.line.chat`、`erp.line.item-questions`、`erp.line.binding`、`erp.line.intake.submit` | 没测到（原因：LINE 收单仍交 bytes，本步没改它的路；只因同在原件层被列出） |
| `control.assistant` | 没测到（原因：实验栈没有 fatcc.com 站点；它的 CC 附件与产品站点同一个接口，已由 CC 附件那一行覆盖） |
| `erp.cc.troubleshooting`、`erp.cc.companies`、`erp.cc.chat-push`、`erp.cc.stocktake`、`shared.exports`、`cowork.exports`、`erp.entry.choices` | 没测到（原因：只因同在 assistant / 原件层被列出，这些工具与导出没改） |

## 保留项

- LINE 收单（ERP `integrations.intake._fetch`、Cowork `line.intake._admit`）：在后台容器里下载成 bytes（11-11 已减一份，40MB 约 +41MB），
  不占网页内存，主会话定后做。做法：下载按块写临时文件，交 `registered.put` / `files.service.create` 的 `LocalFile`。

## 变基到 702580300 后重新验证（11-12 晚，接手窗口 c-line-2）

与 main 第 14 车（cc-composer-send：选文件时拒收超限 / 空 / 不支持类型、上传失败不写 Error Log、页面变量 `pearnly_upload_extensions`）功能相邻，逐处核对：
- `assistant/files._upload` 合完仍是服务端自己的两道：`size > contracts.UPLOAD_MAX_BYTES` 回「文件超过 40 MB」、`sessions._guard(contracts.upload_name, …)` 拒空文件与不收的类型，
  都在留存与开远端会话之前；页面那份上限 / 类型清单仍从同一处来（`originals.rules.UPLOAD_MAX_BYTES`、`contracts.UPLOAD_EXTENSIONS`），本支没碰。
- 「上传失败不写 Error Log」是页面 `SiteError` 那一侧；服务端拒收走 `frappe.throw`，本支的落盘路径没有新的 `log_error`。
- **跨两边的新测试**（流程 `cowork.upload.server-refuses`、`cc.upload.server-refuses`，`scripts/flows/upload_server_refuses.py`）：绕过页面直接按 multipart 打接口——
  CC 附件 41MB+ 请求体 413；比 40MB 大 256KB（请求体没超 Frappe 上限）服务端回「文件超过 40 MB」（417）；空文件、.bmp 服务端拒收；
  Cowork 录入 41MB+ 413、.bmp 在落到临时文件之后由原件检查拒收；之后这段对话留存目录为空、`remote_ids` 为空（没开远端会话）、
  本流程开始后的 `pearnly-upload-*` 临时文件隔 15 秒两次都看不到；Cowork 没建出原件记录。两条在实验栈都过。
  顺带看到：空文件 / 不收类型服务端回的是英文原句（`file is empty`、`unsupported file type`，`ContractError` 没翻译），main 上就如此，不在本支改。
- 本地闸门 231 步过；候选离线 111 组 LANE EXIT 0（生产镜像 b3adf474）；Development checks 绿（bf86e4b4e）。
- 实验栈（第 1 步新镜像 1cf98bf7 + 本支源码）：`--changed --base origin/main` 7 条 18/18（含上面两条新流程），`pearnly/flows` 回写到 bf86e4b4e；
  补充 12 条（交接里的 10 条 + composer 的 `cowork.cc.send-unblocked`、`cc.send-unblocked`）35/35。
- **内存复测**（同款 40 页 39MB 合成 PDF，CC 附件接口，新入口网页进程匿名内存峰值增量）：1 份 **+3MB**；15 份同时 **+51MB（每份约 3.4MB）**，15/15 成功——
  与变基前 +0 / +49MB 同一量级。原始输出 `~/pearnly-devtools/c-line2-mem/rebased-new-1.txt`、`rebased-new-15.txt`。
  （假引擎容器默认 128MB，收 40MB 文件会被 OOM 杀，量内存时照前一窗口用 `agents-mem.yml` 放宽到 3GB；它是测量用的假引擎，不是被测对象。）
- **浏览器逐产品点**（内置浏览器，实验栈，1.3MB 文件、超过 werkzeug 500KB 落盘阈值）：

  | 入口 | 结果 |
  |---|---|
  | ERP Desk 原生附件 `upload_file` | 通过：200，读回 1331866 字节一致 |
  | ERP 采购 / 销售识别原件、入库单附件（`frappe.pearnly.erp_upload_original`） | 通过：三处都回 File 记录 |
  | ERP CC：选 41MB 文件 / .bmp | 通过：当场拒收，「文件超过 40 MB」「不支持此文件类型」，没有附件卡 |
  | ERP CC：选 1.3MB 文件上传、发出、下载回来 | 通过：逐字节一致，假引擎回附件已收 |
  | Cowork 录入（`frappe.pearnly.upload_original`） | 通过：大小、页数、sha256 一致 |
  | Cowork CC：空文件；一次选 41MB + 1.3MB | 通过：「文件为空」；超限的不进、正常的加入并发出，下载回来一致 |
  | 控制面 `upload_file` | 通过：200，读回 sha256 一致（实验栈控制面没走完安装向导、Desk 进不去，用会话 + CSRF 直接按 multipart 打同一接口） |
  | fatcc.com | 没测到：实验栈没有这个站点，放进部署后核对 |

  这一轮各站新增 Error Log 只有实验栈固有的实时推送超时（`kind: push`）和 ERP 实验账号没有公司的 boot 项报错，与上传无关。
