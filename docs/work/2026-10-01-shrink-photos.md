# 上传前在浏览器里缩照片（2026-10-01，测试分支，不上生产）

用户经压测窗口同意：上传前把照片缩到识别用得上的大小，降低上传量与服务器解码的内存和 CPU，不改变识别读到的图。
分支 `codex/shrink-photos`，基线 main `8bc2d3c5`。

- 站点开关 `pearnly_shrink_photos`（默认关）；开时启动数据带 `pearnly_shrink_photos = {max_long_edge: 2400, quality: 0.92}`
  （`pearnly_core/desk/upload.py`）。
- 共用 `p.shrink_photo(file)`（`pearnly_core/public/desk/product.js`）：JPEG / PNG / WebP / HEIC（浏览器解得开时）按 EXIF 转正，
  长边大于 2400 才缩到 2400（canvas 高质量缩放），存成 JPEG 0.92、文件名改 `.jpg`；长边不超过 2400、PDF、表格、文档、
  解不开的图原样返回。
- 用在：Cowork 录入工作台上传（`intake-upload.js`）、ERP 采购 / 销售录入的原生上传器（`purchase-entry.js` / `sales-entry.js`，
  上传前替换待传文件）。对账不改。
- 依据：录入识别本来就把照片缩到长边 `OCR_IMG_MAX_LONG_EDGE` 2400（LANCZOS）再读（`ocr/inputs_runtime/source.py`），预览 2048，
  PDF 按 200 DPI 渲染。浏览器缩放算法与服务端 LANCZOS 不同、多一次 JPEG 编码，由压测窗口用 150 张真实照片逐字段对比验收。
- 对账这条线（结论给压测窗口）：银行 / 总账 / 税报照片送 Document AI 读字用的是**原图字节**（`document_ocr.read_file` 图片走
  `[(content, mime, 1)]`，不缩）；只有 3.8 抽取用的页面图缩到 2400。对账若跟进，Document AI 读到的图会变，要单独验证。
- 代价：开关打开后「下载原件」拿到的是缩小后的版本（用户已知）。
- 验证：内置浏览器实测 4000×3000 JPEG → 2400×1800、3000×4000 PNG → 1800×2400 JPEG；1600×1200 与 2400×1800 原样；PDF 原样；
  开关关原样。原生界面契约、架构、前端 i18n、Cowork 离线测试通过（除两条镜像缺 `docx`）。
