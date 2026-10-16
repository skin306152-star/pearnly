# DMS 开单回执逐项复核 + 连点确认不再回「已过期」

- 状态：复核卡、连点修复、差异卡三个按钮实现完成，本地闸门通过，待测试机用户试用
- 分支 / 工作树：`pearnly/dms-booking-readback-card` / `.claude/worktrees/dms-booking-readback-card`
- 基线：`origin/main` `7c9bb445`（2026-10-16 开工时）
- 集成目标：`main`；用户试用通过后再合入、部署（部署按批次另行记录）
- 授权：用户 2026-10-16 原话"按这个范围开工：不改写入和结果未知逻辑，只做一致时加一行、不一致时差异卡，做完上测试环境给我试"；
  "连点回话修复和复核卡放同一批做：第二次点击认出正在提交就不回话，只有真过期才回已过期，做完一起上测试环境给我试"。

## 来由与证据

- ris（Rayong-Isuzu-Sales）使用方要求：Pearnly 把订车单存进 DMS 后，按单号把这张单读回来，与确认前的汇总卡逐项对比；
  有不一致就列出差异。理由原话："เพราะบางทีปรากฏว่าเงินจองไม่ได้บันทึก"（有时发现订金没存进去）。
- 生产只读核对（2026-10-16，系统读档账号，只读 `showdata` + `form.php`）：ris 经 Pearnly 开的全部 8 张订车单
  （BKRY26001165 … BKRY26001263），提交值与 DMS 现存值逐项一致，订金、各渠道金额、附件 2 份全部在。
  订金没存的情况在这 8 张里没有出现；已请使用方提供具体单号与截图，尚未收到。
- 连点两次：BKRY26001261（10-12 16:44）第一次点击认领确认号并排入后台开单，第二次点击时确认号已作废，
  `booking_flow.handle_postback` 一律回 `TXT_EXPIRED`，比第一次的开单回执早十几秒到达。生产只有一条开单记录，未重复建单。

## 范围

| 项 | 做法 |
|---|---|
| 复核来源 | 回读核实那一轮已读到的表单经 `submit_booking(readback_out=…)` 交出；不多读 DMS，不改写入、重试、"结果未知"与拒单判定 |
| 比对 | 新增纯函数 `booking/booking_review.py`：汇总卡上的各项按编号（顾问、地点、车、颜色、销售条件、上牌方式、省县区、邮编、称谓、银行）、金额（`Decimal`）、身份证号（去噪）、电话与账号（只比数字）、手填文字（折叠空白）比；不比 DMS 回显的名称文字 |
| 回执 | 全部一致且附件齐：原回执 + 「ผลการตรวจสอบกับ DMS: ถูกต้อง」；有不一致：差异卡（标题、单号、逐条「确认时的值 → DMS 里的值」、请在 DMS 中修改；无按钮）；拿不到表单或附件未齐且无其它差异：原回执不变 |
| 留痕 | 推送台账 `response_body.review_diff_items` 只记不一致的项目名，不记值 |
| 连点 | 确认 / 重试没认领到时，若这次点击的确认号就是会话里正在提交的那个（或刚在并发里输给同一张卡），不回任何话；其余照旧回「已过期」 |

关键项（订金、付款、车、客户、顾问、交车日期等）对不上时仍按原逻辑报"结果未知"，不会出差异卡（用户 2026-10-16 确认这次不动）。

新增文案（四语，`dms.card.TXT_REVIEW_OK / TXT_REVIEW_DIFF_HEAD / TXT_REVIEW_BOOKING_NO / TXT_REVIEW_FIX`），
泰文与发给使用方的说明一致。

## 改动文件

- `booking/booking_submit.py`：回读核实时把表单放进 `sink`，`submit_booking` 新增可选 `readback_out`
- `client/client_ops.py`：`create_booking_via_form` 透传 `readback_out`
- `booking/booking_review.py`（新）：逐项比对；`review()` 不抛异常
- `booking/service.py`：结果带 `review`
- `booking/recovery.py`：交给回执投递时保留 `review`
- `booking/delivery.py`：回执存整条消息（`receipt.message`），旧记录只有 `text` 时照旧发
- `booking/booking_ledger.py`：台账记 `review_diff_items`
- `line/cards.py`：`booking_receipt_message`
- `booking/conversation/booking_flow.py`：`_reply_unclaimed`
- 测试：新增 `tests/test_dms_booking_review.py`（接入 `check-dms-offline.py`）；`test_dms_booking_outcome.py` 增加回执复核与连点用例；
  `test_dms_booking_attach_repair.py`、`test_dms_booking_customer_gapfill.py` 按新接口调整替身

## 验证

| 层级 | 状态 | 证据 |
|---|---|---|
| 实现 | 完成 | 本分支 |
| 本地离线检查 | 通过 | `check-architecture.py` 0 违规；`check-dms-offline.py` 32 项全过（Python 3.12）；`i18n-build.py --check` PASS；完整闸门见下 |
| 测试机（testdms 实时源码 + 真实 LINE） | 通过（见下方「真机对抗测试」） | 镜像 `sha256:104eb028…`（线上 DMS）+ 本分支实时源码 + 本地前端；三星 A14 真 LINE |
| 用户试用 | 待用户 | |
| 合入 / 部署 | 未执行 | 用户试用通过后 |

## 差异卡三个按钮：测试 DMS 勘察（2026-10-16 晚）

用户 2026-10-16："这批把差异卡三个按钮一起做：先在测试 DMS 上摸清修改订车单的接口和审批限制，再给我方案。"

- 测试租户 `testdms` 的端点与 ris 同一个 DMS 站点（mrerp4sme.com），但账号是测试公司的（sale02 / dmstest003，单号前缀 `BK00000…`），写入只落在测试公司。
- 修改接口就是附件补挂已在用的 `drfcbc/edit.php`：取新鲜的 `form.php?status=e` 整张表单，改字段后整张提交。
  在我们 10-12 复现时开的测试单 BK000002610000005（id 120）上实测：电话 0812340003 → 0812340004，返回非 `err::`，
  读回已改、附件保留、客户主档电话不变；再改回原值，整张表单与修改前逐字段一致（`diff_f0_f2 = []`）。
- DMS 自己的限制（读自其 `view.js` / `form.js`）：列表每行带 `data-allo`（该账号对这张单的权限）与 `data-cf`（已审核 r / 已核对 h / 已审批 a）；
  只有账号有 `e` 且 `data-cf` 为空才出「แก้ไข」按钮；`stcbc=2`（ใบจอง）打开修改页时所有文字栏只读。Pearnly 开出的单是 `stcbc=1`（แบบร่าง）。
- ris 只读核对（测试销售账号 00000）：自己刚开的草稿 BKRY26001261 权限 `pvecgu`（可改），其余已转 ใบจอง / 已取消的单只有 `vu` / `pvu`（不可改）。
  测试公司销售 sale02 对自己的草稿单同样是 `pvecgu`、`data-cf` 为空。

## 差异卡三个按钮：实现（2026-10-16 晚）

用户 2026-10-16："按这个方案开工做三个按钮，和复核卡、连点修复一起上测试环境给我试。"

| 按钮 | 做法 |
|---|---|
| อัปเดตข้อมูล | 后台用开单那个销售的 DMS 账号，把差异项的值改回确认时的值（同一张单），读回逐项核对：全一致回「已更新 + 单号 + 核对结果」，仍有差异发新的差异卡 |
| ใช้ข้อมูลเดิม | 不写入，回「已沿用 DMS 中的资料」 |
| แก้ไข | 打开编辑页（沿用 `/dms/editor` 地址，`editor=review`，路由表不加新页面）：每项二选一（确认时 / DMS），选确认时的文字栏可手改；保存后同上改单 |

- 改单写入 `booking/booking_amend.py`：取新鲜 `form.php?status=e`，只换差异项字段，整张提交 `edit.php`；
  写前检查 `stcbc=1`、列表行 `data-allo` 含 `e`、`data-cf` 为空，不满足回「已不能在此修改」；提交前登记"已发出"，
  之后超时 / 断连只读回、绝不重发；读回核对单号与附件未丢。
- 卡片状态 `booking/conversation/review_fix.py`：存不淘汰状态存储（23 小时），不写回执投递记录；
  一张卡只认领一次（`SET NX`），同卡再点不回话，换过 / 过期的卡回「已过期」；还没发出就失败（连不上等）卡还给销售可再点。
- 台账：每次真正尝试改单记一行 `operation=booking_amend`（只有项目名、字段名，无值）。
- 不改开单写入、结果未知、附件补挂；不改客户主档；不开新单。
- 新文案：`dms.card.TXT_REVIEW_KEPT / UPDATED / LOCKED / FAILED / UNVERIFIED`、`dms.edit.reviewTitle / reviewConfirmed / reviewInDms / reviewSaved / reviewExpired`（四语）。
- 测试：新增 `tests/test_dms_booking_review_fix.py`（17 项，接入 `check-dms-offline.py`）；`scripts/test-dms-editor-snapshot.cjs` 补 `./ReviewPage.vue` 替身。
- 本地闸门：development.yml 全部 83 步通过（Python 3.12，本机 macOS）。

## 真机对抗测试（2026-10-16 19:12–19:43）

用户 2026-10-16："line测试你自行在安卓真机测，制造对抗测试，错误发生和正确，看是否改动生效"。

- 环境：`lab-start dms`（`PEARNLY_TESTENV_REFRESH=1` 重新导入 testdms，`PEARNLY_TESTENV_LOCAL_SPA=1`），镜像 = 线上 DMS `sha256:104eb028…`；
  手机 = 接在本机的三星 A14（用户本人 LINE，只操作 DMS-TEST 对话）；客户 = 测试公司合成客户 128（กมลวรรณ ศรีสุข），身份证图为本机合成。
- 差异由**不入库的临时故障开关**制造（临时工作树 `pearnly/dms-review-lab-tmp`，站点配置 `dms_lab_review_faults`）：
  `phone` = 开单成功后真的在测试 DMS 里把电话改成 0899999999；`locked` = 模拟 DMS 不许改；`unreachable` = 模拟提交前连不上。
  测完已删配置键、删临时工作树与分支（从未推送）。

| # | 场景 | 结果 |
|---|---|---|
| 1 | 正常开单（无故障） | 回执末尾「DMS 核对结果：一致」；台账 `review_diff_items: []` |
| 2 | 点确认后 3 秒再点一次（两个「确认」气泡都发出） | 不回「已过期」，只出一次结果，只开一张单（BK000002610000012） |
| 3 | DMS 电话被改 → 差异卡 | 标题、单号、「电话 0812340031 → 0899999999」、三个按钮 |
| 4 | 「使用原资料」连点两下 | 只回一次「已沿用 DMS 中的资料。」；DMS 不写；再点同卡「更新资料」不回话、不写 |
| 5 | 「更新资料」连点两下 | 只回一次「已更新 DMS 中的订车单 / 单号 / DMS 核对结果：一致」；DMS 电话改回；台账一行 `booking_amend updated` |
| 6 | 「编辑」页：切换两个选项、手改电话为 0812340099、保存 | 页面自动关闭，LINE 回已更新一致；DMS 电话 = 0812340099 |
| 7 | 已处理的卡再点「编辑」 | 页面显示「此卡片已失效，请使用 LINE 中最新的卡片。」 |
| 8 | `unreachable` 下点「更新资料」 | 首测回「连不上…请联系管理员」——**发现问题**，已改为与开单同一重试话术（`775f1eea`），复测回「连接 DMS 超时或网络异常 · 请稍后重试」；卡片保留，关掉故障后同卡重试成功改单 |
| 9 | `locked` 下点「更新资料」 | 回「这张订车单已不能在此修改，请在 DMS 中修改。」；DMS 不写；台账一行 `locked`；连不上那次不记台账 |
| 10 | 被新卡替换掉的旧卡点按钮 | 回「此项已过期，请重新开始。」 |

- 服务器核对（测试机 testdms 推送台账 + 测试 DMS 只读回读）与手机上看到的一致。
- 留在测试 DMS 测试公司的单：BK000002610000010–014（id 125–129），客户 128 未改。
- **操作失误**：第二次分享测试图时按旧坐标点联系人，LINE 把「最近分享」排在前面，合成测试身份证图（假人）被发到了 Pearnly 官方号对话（19:22）。已告知用户，是否撤回由用户决定，未动该对话。之后每次分享先截图核对再发送。
- 本地闸门：修复后 development.yml 全部 83 步通过。
- 设计口径（已向用户说明）：一张卡被处理后，同卡任何按钮都不再回话（避免「已沿用」后紧跟一句「已过期」）；被新卡替换掉的旧卡才回「已过期」。
