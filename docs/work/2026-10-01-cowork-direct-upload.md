# Cowork 原件浏览器直传谷歌存储（ADR-016）

- 分支 / 工作树：`codex/cowork-direct-upload`，`/Users/skin/pearnly-wt-upload`；基线 `f4720964`（origin/main）。
- 授权：用户 2026-10-01「做」（压测窗口转达的方案，本窗口再确认）。生产部署走 Cloud Run 一次授权（夜间部署、开开关，出问题关开关）。

## 已做

- 原件桶 `gs://pearnly-cowork-originals-sg`（2026-10-01 建）：新加坡、标准存储、统一权限、禁止公开、无自动删除、默认 7 天软删除；
  CORS 任意来源 PUT / GET（签名地址才是凭据）；`pearnly-ocr-sa` 对象读写。标签 app=pearnly, role=originals。
- 代码：见 ADR-016「代码」。原件检查抽成纯函数 `ocr.originals.inspect`，`files.service.inspect_content` 改为包装它（提示文字不变）。
- 顺带修：原件在服务器上且超过 20 MiB 时，开着 Cloud Run 识别会整份报错（请求体放不下）→ 改为本机识别。
- 部署脚本：`PEARNLY_OCR_SERVICE=cowork-ocr-test` 部署单独的测试服务；Cloud Run 加 `PEARNLY_ORIGINALS_BUCKET`。

## 测试

- `tests/test_cowork_direct_upload.py`（11 条，生产镜像）：签名可验、上传锁大小与不可覆盖、下载限版本与附件、
  签发只给 PDF / 图片、拒绝提示与原来相同、只登记 Cloud Run 读出的信息、忙时让页面重试、读回按版本并核对 sha256、登记结果不再整份下载。
- `tests/test_cowork_remote_ocr.py` 新增 5 条：核对结果与服务器检查一致、坏原件删掉并给原代码、外站点 / 越界对象拒绝、
  按对象读与带字节发来结果和计费证据一字不差、内容被换 / 没有时不付费。
- `tests/test_cowork_remote_forward.py` 新增 3 条：核对不占名额、等待不占连接；忙 / 连不上让页面重试；按引用发不带字节。

## 待做

1. 测试 Cloud Run 服务 `cowork-ocr-test`（从本分支提交部署，最少实例 0）。
2. 测试环境（Windows）`lab-start cowork`，测试租户 testcowork 配 `pearnly_cowork_originals_bucket` 和指向测试服务的
   `pearnly_cowork_ocr_remote`；先自测（合成 PDF / 图片：直传 → 登记 → 识别 → 预览 → 下载），再请用户在浏览器试。
   切测试租户域名前先和压测窗口对好时间（压测可能在用 testcowork）。
3. 用户试用通过 → 生产镜像候选检查 → 合入 → 夜间部署（Cloud Run 线上服务 + 生产）→ 线上站点配桶名打开 → 回读 → 通知压测窗口。
