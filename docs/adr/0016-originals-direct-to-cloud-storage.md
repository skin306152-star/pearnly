# ADR-016 Cowork 原件由浏览器直传谷歌存储

- 状态：已采纳（2026-10-01）。用户：服务器先不升级；同意「浏览器直接把原件传到谷歌存储（新加坡），不经过我们服务器」
  （压测窗口转达，本窗口再次确认「做」）。
- 范围：Cowork 录入台网页上传的 PDF / 图片原件（`files.api.upload` 那条路）。不在范围：表格 / Word 原件（要在服务器上解析，
  照旧经服务器）、LINE 收到的原件（服务器本来就从 LINE 取字节，照旧存服务器）、对账中心的临时文件（任务结束即删，
  `recon_center.storage`）。
- 代码：`pearnly_cowork.files.cloud`（签名、读回）、`files.service.begin_direct / finish_direct / stored_original /
  verify_stored`、`files.api.begin_upload / finish_upload`；纯检查 `pearnly_cowork.ocr.originals`（服务器与 Cloud Run 共用）；
  Cloud Run `POST /v1/cowork/inspect`、识别请求可带 `original`（对象 + 版本号 + sha256）代替字节（`ocr.remote.contract`）。

## 问题

压测（2026-10-01）：几百人同时上传时，服务器要收下每一份原件（网络带宽、multipart 解析、PDF 打开数页、写盘），
再把整份 base64 发给 Cloud Run。每份发票服务器侧约 3.2 CPU 秒，其中上传 0.25、转发 TLS 0.17；原件越大，
上传越慢、转发越占带宽。另外 Cloud Run 一次请求最多 32 MiB，原件超过 20 MiB 时根本发不过去。

## 决定

1. **原件桶** `gs://pearnly-cowork-originals-sg`（新加坡 `asia-southeast1`，标准存储）：统一权限、禁止公开访问；
   **没有自动删除**（原件和存在服务器上时一样一直保留，不能比现在短）；误删可在 7 天内恢复（谷歌默认软删除）。
   只有识别服务账号 `pearnly-ocr-sa` 有对象读写（`roles/storage.objectUser`）。CORS 允许任意来源的 PUT / GET：
   没有签名地址什么也做不了，签名地址本身就是凭据；租户子域名会不断增加，按来源逐个登记做不到。
2. **上传三步**（`intake-upload.js`）：
   - `begin_upload`：服务器照旧查权限、幂等键、文件名 / 类型 / 声明的大小（同样的提示文字），签发 15 分钟有效的 PUT 地址。
     对象名 `<站点>/originals/<原件名><扩展名>`，原件名 = `CWS-` + sha256(工作空间, 上传人, 幂等键) 前 48 位，与原来的
     原件记录名相同。签名锁定 `x-goog-if-generation-match: 0`（写过一次就不能覆盖）和 `x-goog-content-length-range`
     （大小必须等于声明的字节数）。签名在服务器本地用服务账号私钥算，不调谷歌接口。
   - 浏览器把原件 PUT 到这个地址（谷歌存储），不经过我们服务器；重试时遇到 412 说明已经传上去了。
   - `finish_upload`：服务器请 Cloud Run 读一遍（`/v1/cowork/inspect`，不占识别名额）：与服务器收原件时**同一份检查**
     （`ocr.originals.inspect`：类型、PDF 能否打开 / 是否加密 / 页数 1–50、图片能否解码、大小），返回页数、sha256、
     版本号和 Frappe 用的 MD5。不通过就删掉那个版本、给原来的提示；通过才登记原件记录，`storage_url` 记
     `gs://桶/对象#版本号`，之后一律按版本号读，内容不会被换掉。Cloud Run 一时忙 / 连不上时回 `{"retry": 秒}`，页面过几秒再确认。
   没配桶、没开 Cloud Run 识别、表格 / Word 原件：`begin_upload` 回 `{"direct": 0}`，页面照旧走 `upload`。
3. **识别**：原件在桶里时，服务器只把对象、版本号、sha256 交给 Cloud Run，Cloud Run 自己读、核对 sha256（对不上 /
   没有就报错、不付费）。原件在服务器上且超过 20 MiB 时改在本机识别，不再整份报错。
4. **服务器仍然管**：权限、幂等、余额、名额、存结果、扣费、访问日志。预览（`files.api.page`）照旧由服务器渲染，按版本号从桶里
   读回原件、核对 sha256；**下载**（`files.api.original`）记访问日志后转到 5 分钟有效、只限这一版本、作为附件下载的签名地址。
   登记识别结果时不再整份下载核对（同一版本内容不变，登记原件时 Cloud Run 已核对 sha256）。
5. **开关**：站点配置 `pearnly_cowork_originals_bucket`（桶名）+ 已开的 `pearnly_cowork_ocr_remote`。关掉任一个立刻回到
   经服务器上传；已经存在桶里的原件照常能预览、下载、识别。

## 成本

存储约 0.02 美元 / GB / 月；上传免费；每份一次写入、一两次读（同区域 Cloud Run 读免费）。预览和下载从桶读到服务器 / 浏览器
按谷歌出网流量计（亚太约 0.12 美元 / GB，一份 2 MB 的发票看一次约 0.0003 美元）。没有常驻花费。

## 不变

- 每人每分钟份数：现在没有单独的每分钟限制，排队上限照旧由识别名额（全站 / 单租户待处理数）管，这次不加不减。
- 原件保护（不能改、不能删、只能经 Cowork 文件服务读、私有不缓存）、访问日志、权限判断位置不变。
- 读法、结果、计费不变：对照测试证明同一原件按对象读与带字节发来，结果和计费证据一字不差。
