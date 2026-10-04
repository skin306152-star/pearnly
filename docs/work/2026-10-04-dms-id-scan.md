# DMS 在 LINE 里「扫身份证」（2026-10-04）

用户：DMS 业务员拿手机在展厅、安卓和 iPhone 都用，不加硬件；"现在是拍身份证、上传、识别"，在提示发身份证的文字下
加一个「扫描身份证」，点了打开扫描页，不点照旧发照片。做完把 testdms 切到测试机，用户在 LINE 实测。
分支 `codex/dms-id-scan`，基线 `origin/main` c20cd463。

## 为什么

- 泰国身份证芯片是接触式，手机 NFC 读不了；不加硬件只能拍照识别。
- 现在一张约 7 秒，其中约 2.9 秒是服务器去 LINE 日本下载照片（线路绕美国，见 `2026-10-03-dms-ocr-speed.md`）。
  扫描页直传本站点，这一步没有了；识别本身（模型、提示词、扣费、复核）一字不改。

## 流程

1. 提示发身份证的文字（菜单选建档/订车后、先输了电话后、重拍、照片取不到、读不清要重拍、催料）带 LINE 快捷按钮
   「สแกนบัตรประชาชน」，打开 `https://liff.line.me/<liff>?editor=scan`。站点没配 LIFF 就不给按钮。
2. 交接页 `/liff/dms-booking` 照旧换票据（新模式 `scan`）→ SPA `#/dms/scan`。
3. 扫描页（`frontend/src/pages/dms/ScanPage.vue`）：后置摄像头 + 身份证比例取景框；每 150 毫秒看取景框缩小后的灰度图
   （`frontend/src/lib/id-scan.ts`），框四边至少三条有卡片边缘、清楚、亮度正常、无大片反光、连续三帧不动就自动拍；
   4 秒后给手动快门。原分辨率裁取景框（四周放宽 6%）、JPEG 0.92 上传。拍不了 → 提示回聊天直接发照片（不给相册）。
4. `browser_scan` → `intake.scan.store_upload`：只在收料态收，照片存 `private/files/dms-id-scan/<id>`（不进不淘汰 Redis，
   Redis 只记归属，1 小时过期，顺手清 1 小时前的残留），会话 `pending_image = scan:<id>`（与聊天发照片同一个"以最后一张为准"）。
5. 回交接页 `editor=scan_sent`：`liff.sendMessages` 替业务员发「📷 สแกนบัตรแล้ว #短码」→ webhook 文本路由**最先**认这句
   （它带数字，必须在"含数字→手机号"之前）→ 用这条消息的免费回覆令牌开始识别。LIFF 没有 `chat_message.write`
   （开户说明目前写的是不勾）或不在 LINE 里 → 页面调 `browser_scan_start` 直接开始，结果走推播。每张扫描图只开始一次
   （Redis `SET NX`），两条路都到也不重复提交。
6. 识别任务 `flow.process_image` 对 `scan:` 读站点里的照片（`intake.scan.read`，只给上传者、读一次即删），
   其余与聊天发照片完全相同（扣费引用 `idcard:line:scan:<id>`）。

## 规矩与检查

- LINE 票据页仍用 Frappe UI（Button / Spinner / ErrorMessage）；`router.ts` 与架构检查的白名单加 `/dms/scan`
  （三个都是 LINE 一次性票据页）。打包后扫描页 2.9 KB（gzip），整页首次约 90 KB。
- 文案进 `pearnly_dms/i18n/messages.tsv`（`dms.card.BTN_SCAN_CARD` / `TXT_SCAN_SENT`、`dms.edit.scan*`），四语。

## 验证

- `scripts/test-dms-line-runtime.py` 55 条（新增 `DmsIdScan` 9 条：有 LIFF 才给按钮且标签 ≤ 20 字；只收收料态的图片、
  拒非图片/超大；带数字的那句话不被当手机号、只开始一次且用它的令牌；过期短码回过期、不开始；回落无令牌且与那句话
  不重复；只有上传者能读、读一次；识别读扫描图不经 LINE 下载、重拍提示仍带按钮；上传接口只认 `scan` 票据、收 data URL）：
  生产镜像 f5283c70 挂载本分支、断网，通过。
- `tests/test_dms_recording_fixes.py`：菜单选建档后的提示改由 `send` 发出带按钮的同一句话（有意变化，断言随之更新）。
- 取景判断用合成图在本机核对：清楚的卡 → 拍；空桌面 → 放卡；模糊 → 对焦（清晰度阈值 80 时会误放行，改 300）；
  大片反光 → 反光；晃动 → 拿稳。真机阈值待测试机实测再调。
- `check-architecture.py` PASS；`check-dms-offline.py`（Python 3.14）20 项 PASS；`i18n-build --check` PASS；
  `check-i18n --strict` 与 main 输出逐行相同（main 上原本就不过）；`vue-tsc` 与 `vite build` 通过；
  生产镜像 f5283c70 挂载本分支跑完整 `ci-candidate-checks.sh offline`：54 项全过。

## 未验 / 待定

- 测试机真机：LINE 内置浏览器能否开摄像头（安卓 / iPhone）、自动拍阈值、拍到回聊天出结果的耗时。
- 金标：裁剪后的照片与原图读法是否持平（ris 50 张，flash-lite），识别预算暂停中，需用户同意。
- 省推播额度要在 LINE 后台给这个 LIFF 勾 `chat_message.write`（首次打开会多一个授权确认）；不勾功能照样能用，
  每次扫描多一条推播。若采用，开户说明 `line_guide_content` 那句"不勾"要一起改。
