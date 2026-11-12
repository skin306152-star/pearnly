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

## 验证

（按做完的顺序补）

## 保留项

- LINE 收单（ERP `integrations.intake._fetch`、Cowork `line.intake._admit`）：在后台容器里下载成 bytes（11-11 已减一份，40MB 约 +41MB），
  不占网页内存，主会话定后做。做法：下载按块写临时文件，交 `registered.put` / `files.service.create` 的 `LocalFile`。
