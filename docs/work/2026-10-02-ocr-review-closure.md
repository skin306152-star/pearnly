# Cowork 录入识别：确定性错误定向复核 + 录入口不再显示识别后提示（2026-10-02）

分支 `codex/ocr-review-closure`，基线 `origin/main` = `10a4f07d`。范围按用户 2026-10-02 口径（经协调窗口转达）两次调整：
录入口不新增、也不再显示识别后的错误提示 / 复核原因，只提升识别质量、把结果显示出来；推送错误的文案保留。

## 1. 现象（测试机）

27 页扫描销项第 3 页走了复核链 `ID-RC-L1-L2`：日期 3/6/2569 读成 2559、买方税号末位读错（校验位不过），两处都没改对。

本地按当前 main 复现（替身模型，不调付费接口）：税号校验位不过本来就会触发直读闸 → 带提示复核 → 原图 + 原文再读，
最后标 `needs_review`；**年份差 10 年不触发任何复核**（日期只在结果里加一条提示），复核提示里也没有日期；
复核链里前几次读到过的对的值，最后一次读错就丢了。

## 2. 改动

识别（`ocr/`，纯计算，不改提示词模板、不改 Cloud Run 调用）：

- `validation/dates.year_shift`：票面年份与识别当年差 9–11 年（佛历一位读错的特征）→ 内部信号。
- `engine/runner.image_page`：年份信号与原有闸的原因一起交给**已有的带提示复核（RC）**，同一次复核说明两处；
  只有年份不对时只复核一次、只取复核读到的日期（月日相同、年份正常），其余字段照直读，不升级到 Vision 原文再读。
- `validation/accounting.adopt_from_reads`：同一页前几次读数里有过校验的值、最后一次没过 → 取过校验的那个
  （税号：只差两位以内且唯一、不取对方的号；日期：月日相同、只有一个）。复核（RC）与原图 + 原文再读之后都用。
- 没读对就保留最佳值照常出结果：不新增标红、不新增提示（原有的税号校验位 / 超 5 年日期的判定不变）。

入库与录入口（`history/`、`public/desk/`）：

- `history/review.repair_tax_ids`：12 位税号补开头 0 校验位就对 → 入库时直接补（原来只给建议）。
- `history/review.settle_number`：单号两种读法 → 按这家以前人核过的票号写法定，没有历史保留识别主值；
  进项单号像 Express 凭证号、另有候选号 → 换成候选号。不确定的不改。
- `history/storage.settle`：在 `_create` 请求指纹之后、查重之前调用（重试时历史可能变，指纹只认调用方交来的内容）。
- `review_reasons` 照样算、照样存（内部质量信号），DocType、字段、补丁都不删。
- 列表（`intake.js`）：去掉「没推送但有原因 → 橙色待复核」；状态列名改用已有词条 `intake.col.status`（状态）；
  仅识别的票（`intake_target` = `none`）显示蓝色「已识别」（已有词条 `intake.status.recognized`）。推送三态与失败悬浮原因不变。
  列表接口多回 `intake_target` 一列。
- 复核页（`intake-upload.js` 的 `show_notice`）：去掉黄色需复核提示条与漏识别那条；红色推送失败原因与按钮原样保留。
  识别收尾的橙色「可能重复 N 张」提示（`intake.review.dupWarn`）也去掉；「识别结果为空」「N 份识别失败」、导出被拒 / 提交队列出错的提示保留（属于失败，不是质量提示）。
- 翻译：删掉不再使用的 `cowork.review.*`（`send_anyway` 保留）、`cowork.quality.pending`、`intake.review.missed`、
  `intake.col.entryStatus`、`intake.review.dupWarn` 及其英文别名行（`Needs review` 对账任务仍用，保留），`i18n-build` 重建。

有意变化已登记在 `tests/test_cowork_engine_source_parity.py`（年份差约 10 年先复核一次；复核读回同值时判定与字段同旧线上）。

## 3. 触发率（离线统计，已存读数，不调接口）

`OCR本地评测资料/google-enterprise-invoice-race-2026-09-03` 下已存的看图读数，每套 153 张发票，识别当年按 2026：

| 读数 | 年份差约 10 年（新触发） | 税号校验位不过（原有触发） |
| --- | --- | --- |
| imageonly31（flash-lite 看图，线上直读同款） | 0 | 71 |
| imageonly38 | 0 | 72 |
| ent31 | 0 | 71 |
| ent35lite | 1 | 71 |

新触发 1/612（0.16%）；税号那一列偏高是 v2 合成语料的卖方税号本身校验位不合法（见 2026-09-27 语料记录），
原有行为不变、不是这次新增的调用。已经因为别的原因要复核的页，年份原因并进同一次复核，不多调用。

## 4. 验证（本机，`--network none`，Cowork 识别镜像 `cowork-ocr:10a4f07dc3fd` 的 Python 3.14 + 本树源码）

- 新增 `tests/test_cowork_reread_checks.py` 8 条：第 3 页两处错一次复核改对；只错年份复核一次改对 / 复核失败不升级；
  前几次读对的值被保留；全错保留最佳值；正确的页不多一次调用（单页与 5 页）。
- `tests/test_cowork_review_reasons.py` 16 条（新增入库纠正 5 条，含 `storage.settle` 编译替身）。
- `tests/test-intake-status.cjs` 7 种状态（已加进 Development checks）。
- 引擎相关 102 条除 `PagesTogether.test_pages_are_read_at_the_same_time`（计时用例，基线同样失败：本机 amd64 镜像在 arm 上模拟）外全过；
  旧线上对照 `test_cowork_engine_source_parity` 对 `ec2e5c95` PASS。
- 与识别 / 入库相关的 37 个 Cowork 测试文件，本树与基线 `10a4f07d` 在同一镜像里逐个跑、结果逐行对比：除新增文件外完全相同（其余失败是这个镜像没有 Frappe / PDF 库，基线同样失败）。
- `check-architecture` PASS；`i18n-build --check` PASS；`check-frontend-i18n --strict` PASS；Development checks 同名闸门本地通过（本机 python3 是 3.9，4 个用到新语法的脚本改用镜像里的 Python 跑；`npm ci` + DMS 编辑器快照那一步本机 npm 坏了没跑，与本改动无关，交 CI）。

未做：浏览器里点列表 / 复核页（本机共享样板不能接管、不用测试租户）；Site 内 `_create` 写入路径；真实识别调用。
Cloud Run 识别镜像要随部署重建，引擎改动才在 Cloud Run 上生效。

## 5. 与其他窗口可能冲突的文件

`ocr/engine/runner.py`（`image_page` / `pipeline_page`）、`ocr/validation/accounting.py`、`ocr/validation/dates.py`、
`history/review.py`、`history/storage.py`、`public/desk/intake.js`、`public/desk/intake-upload.js`、两个 `messages.tsv` 与翻译包、
`tests/test_cowork_engine.py`（一处日期用例）、`tests/test_cowork_engine_source_parity.py`、`.github/workflows/development.yml`（一步）。
