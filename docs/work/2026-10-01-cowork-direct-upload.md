# Cowork 原件浏览器直传谷歌存储（ADR-016）

- 分支 / 工作树：`codex/cowork-direct-upload`，`/Users/skin/pearnly-wt-upload`；基线 `f4720964`（origin/main）。
- 授权：用户 2026-10-01「做」（压测窗口转达的方案，本窗口再确认）。生产部署走 Cloud Run 一次授权（夜间部署、开开关，出问题关开关）。

## 已做

- 原件桶 `gs://pearnly-cowork-originals-sg`（2026-10-01 建）：新加坡、标准存储、统一权限、禁止公开、无自动删除、默认 7 天软删除；
  CORS 任意来源 PUT / GET（签名地址才是凭据）；`pearnly-ocr-sa` 对象读写。标签 app=pearnly, role=originals。
- 代码：见 ADR-016「代码」。原件检查抽成纯函数 `ocr.originals.inspect`，`files.service.inspect_content` 改为包装它（提示文字不变）。
- 顺带修：原件在服务器上且超过 20 MiB 时，开着 Cloud Run 识别会整份报错（请求体放不下）→ 改为本机识别。
- 部署脚本：`PEARNLY_OCR_SERVICE=cowork-ocr-test` 部署单独的测试服务；Cloud Run 加 `PEARNLY_ORIGINALS_BUCKET`。

- 预览：原件在桶里时由 Cloud Run 生成（`/v1/cowork/preview`），服务器只转回图；渲染抽成 `ocr.originals.render_page`。

## 测试

- `tests/test_cowork_direct_upload.py`（12 条，含预览在 Cloud Run 生成、不下载整份原件，生产镜像）：签名可验、上传锁大小与不可覆盖、下载限版本与附件、
  签发只给 PDF / 图片、拒绝提示与原来相同、只登记 Cloud Run 读出的信息、忙时让页面重试、读回按版本并核对 sha256、登记结果不再整份下载。
- `tests/test_cowork_remote_ocr.py` 新增 6 条（含预览与服务器渲染逐字节相同）：核对结果与服务器检查一致、坏原件删掉并给原代码、外站点 / 越界对象拒绝、
  按对象读与带字节发来结果和计费证据一字不差、内容被换 / 没有时不付费。
- `tests/test_cowork_remote_forward.py` 新增 4 条（含预览只传回图）：核对不占名额、等待不占连接；忙 / 连不上让页面重试；按引用发不带字节。

## 实测（测试机 testcowork，实时源码 + 测试服务 cowork-ocr-test，2026-10-01）

- 服务层：签发 → PUT（浏览器预检 200、允许任意来源）→ 再 PUT 412（不能覆盖）→ 大小不符 400 → 登记（2 页、sha256 对）
  → 同键再签发直接给出已登记 → 读回字节相同 → 下载链接 200、字节相同、附件 → Cloud Run 预览与服务器渲染逐字节相同 → 坏 PDF 拒绝（原提示）。
- HTTP 层（压测账号 API 密钥，按页面步骤）：语料照片 / 3 页 PDF / Excel 各走直传与原来的上传各一次：
  页数、sha256、字节数相同；照片识别两边都 saved、各扣 0.75 泰铢（档位 B、1 页）；识别结果只差耗时毫秒、互为疑似重复、
  请求指纹（预期）；3 页 PDF 两边都 not_invoice、不扣费；Excel 直传回 `direct: 0` 照旧经服务器、扣 0.33；
  下载 302 到谷歌、字节相同、附件；预览两边逐字节相同（照片 PNG 2.9 MB → 之后改成超过 400 KB 用 JPEG）。
- 办公室网络断开，第二轮（单页 PDF 与失败路径的 HTTP 版）没跑完；改为部署后在生产 testcowork 上跑完再全站打开。
- 页面：`tests/test-upload-original.cjs`（node 6 条）；生产镜像（46645eee 底座 + 本分支）完整离线检查 28 组通过。

## 上线

1. 合入 main，与 46645eee 一起构建、部署（Cloud Run 线上服务带 `PEARNLY_ORIGINALS_BUCKET`）。
2. 先只给 testcowork 配 `pearnly_cowork_originals_bucket`，在生产 testcowork 跑 HTTP 实测（含失败路径），通过后给全部 Cowork 站点打开。
3. 测试服务 `cowork-ocr-test` 与其镜像在收尾时删除（`deploy-cowork-ocr.sh` 可随时重建）。
