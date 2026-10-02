# Cowork 录入第一屏（2026-10-02，分支 codex/cowork-first-screen）

来源：提速批次第⑥项方案，用户经压测窗口同意（2026-10-02）；随后压测窗口调整为直接并进下一批，与 cowork-speed-next 一起集成、测试机统一验证（Cowork 5 种情况由本窗口点）、一次部署。
规矩：用原生 Desk 组件、不加主题色、不加多余文案；状态只用最短词；不显示假百分比；不加轮询。
基线：origin/main c5092f48。读法、调用、扣费不变。

## 改动

- **本机原图**：选完文件，这一批第一张图片直接在等候层的转圈下面显示（`URL.createObjectURL`），不等上传和识别。
  - PDF、表格不显示；浏览器显示不了的（如桌面浏览器的 HEIC）自动不显示。
  - 等候结束时收回地址。
  - 共用层 `p.waiting` 加了可选的 `image` 参数，样式在 `product.css`（边框、圆角取 Desk 变量）；ERP 不传这个参数，界面不变。
- **进度只写真实阶段**，替换原来的「正在整理文件内容 / 正在读取供应商… / 已识别 N/总 页 · 百分比」三行：
  - 单份：上传中 → 排队中 → 识别中；多页文件识别时写「第 N/总 页」。
  - 多份：每个阶段跟真实份数，例如「识别中 3 · 排队中 5 · 已识别 2」；失败的不计进去。
  - 「识别中」的依据：服务端开始识别时（`workflow.run` 标成 recognizing 之后）推一条 `pearnly_ocr_work {reading: true}`（`store.reading_started`）。
  - 批次详情多了 `reading` 字段（phase 是 recognizing / recognized），推送丢了时，原有的兜底轮询也能分出排队还是识别。没有新增轮询。
- **词条**：Cowork 术语表新增 `intake.wait.uploading / queued / page`。共用层不再使用的 `intake.proc.title / sub` 从术语表和翻译包删掉。
- `tests/test_cowork_intake_pages.py` 新增两条测试：开始识别会推给提交人；`reading` 按 phase 区分。这个文件登记进候选离线闸门。

## 验证（本地，未部署）

- 生产镜像 4e80514d 挂本分支源码，跑完整 `ci-candidate-checks.sh offline`：通过（47 项）。
- 架构检查、i18n 生成、JS 语法检查：通过。
- 用浏览器打开从 `product.js` / `product.css` 原样摘出的等候层：
  - 显示顺序是转圈、阶段文字、原图；
  - 等候期间页头按钮停用，结束后整块收起、按钮恢复。
- 缺：测试机（testcowork）上用真实上传点一遍；动测试机前先问其它窗口。还缺用户试用和随批部署。
