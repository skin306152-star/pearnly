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

## 实测第二轮（办公室网络恢复后，测试机地址 .65 → .70，镜像 0ed85f4d + 实时源码）

- 4 份语料（照片 1 页、3 页 PDF、1 页 PDF、Excel）：两路页数 / sha256 / 字节数相同；照片两路各扣 0.75、识别结果只差耗时；
  PDF 两路都 not_invoice 不扣费；Excel 照旧经服务器；下载 302 → 谷歌、字节相同、附件；照片预览 JPEG 433 KB（原 PNG 2.9 MB），两路逐字节相同。
- 单页 PDF 直传第一次 833 秒：另一个窗口同时在测试机上重建了 ocr-worker、关了 Cloud Run 开关（已确认、已恢复）；
  重跑直传 6.0 秒、经服务器 6.1 秒。
- 失败路径：超大 417、类型不支持 417（原提示）、大小不符 PUT 400、同键再传给出已登记、**同键换文件**（第一轮发现直接给出旧记录，
  已修 114f7221，改为 417 原提示）、外人下载 / 上传 403、过期链接 400、不带签名 403。

## 上线

1. 合入 main，与 46645eee 一起构建、部署（Cloud Run 线上服务带 `PEARNLY_ORIGINALS_BUCKET`）。
2. 先只给 testcowork 配 `pearnly_cowork_originals_bucket`，在生产 testcowork 跑 HTTP 实测（含失败路径），通过后给全部 Cowork 站点打开。
3. 测试服务 `cowork-ocr-test` 与其镜像在收尾时删除（`deploy-cowork-ocr.sh` 可随时重建）。

## 上线记录（2026-10-01）

- 生产：main `5c7b2afa`（46645eee 发票号索引 / 扣费计数 + 直传）镜像 `sha256:1a452ff1…`，10:02–10:16 UTC 部署 ERP / Cowork / DMS
  + 单独更新计费（migrate，钱包加 `usage_month` / `usage_checked`）。回读：23 个容器同一镜像、六站点不在维护、四域名 200、
  发票号 / 日期索引已建、`_payg_pages_used` 在、`LOWER(invoice_no)` 已去掉、部署后错误日志 0（按站点时区核对）、
  mariadb 1.84 / 4 GiB。Cloud Run `cowork-ocr-00010`（带 `PEARNLY_ORIGINALS_BUCKET`）。
- 直传：先只开 testcowork，在生产 testcowork 上服务层自测全过后，`pearnly_cowork_originals_bucket` 写入 Cowork 组
  common_site_config（全部 Cowork 站点）。
- 随后 main `2bbcb4ee`（另一窗口的银行账单短页重读 + 本仓测试替身）：只发 Cowork（`sha256:d149a8cf…`，10:25–10:29）
  与 Cloud Run `cowork-ocr-00011`；回读 `rows_short` / `repair_single_break` / 新词条都在、错误日志 0。
- 临时压测租户（用户同意）：testcowork2（ISS-2026-0298 / T-2026-0299）、testcowork3（ISS-2026-0302 / T-2026-0303），
  正式开户流程；各充 1500 泰铢（`admin_adjust`，备注压测、内部记账）；压测账号 stress-t2 / stress-t3@pearnly.local
  （录入、识别记录、银行 / 收入对账、销项税核查、个人资料），密钥在生产 `/root/pearnly-stress/api-t2.env`、`api-t3.env`
  （root、600）；识别配置与 testcowork 一致（补了 `pearnly_shrink_photos`）。欢迎邮件发往 skin306152+testcowork2/3@gmail.com。
  压测后由压测窗口删账号与密钥，租户与站点按下线流程删除（删除前再问用户）。
- 容量：开户时容量检查拦下（维护容器 1 GiB + 现有额度超 1.3 倍上限 269 MiB）。用户同意把 Cowork 识别进程上限 8g → 7g
  （runtime.env，备份 `runtime.env.bak-20261001-cowork-ocr-8g`；实际用 347 MiB，识别在 Cloud Run）。
- 事故：重试开通时 `restart_web` 的 `compose up -d` 没带 `--no-deps`，把 mariadb（与 Cowork 识别进程）重建了
  （10:36:52–53，数据库断开约数秒；六站点之后错误日志 0）。已修 `provision-agent.py`（`codex/provision-no-deps`），
  服务器上的开户代理随下一次部署同步后生效；在此之前不要再开户。
- 清理：测试服务 `cowork-ocr-test` 与镜像包已删；测试机栈已停（卷保留），testcowork 已切回线上。
