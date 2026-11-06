# 录入工作台编辑页「明细 / 税费为空」（2026-11-06）

分支 `pearnly/intake-edit-empty-lines-6661e5`，基线 `5b1bb3688`。证据与前后截图在共享笔记「录入工作台明细为空-11-06/」。

## 现象

用户 11-06 晚在 CC 直写对话里上传 12 页 BJC PDF（识别拆成 11 张单），打开
`/desk/pearnly-intake/new/CWH-ea82ff5dbc55f69e0c35dfa94c4469c3ca35e094b3419260`（第 8 张，221067100017），
截图里明细只有「添加行」、税费各格全空，备注「Page 1/2; page 2 not included. Running subtotal …」。

## 结论

1. **编辑页没丢数据。** 直接打开 ea82：发票 8/11、原件 8/12，5 行明细与未税 / 税额 / 价税合计都在。
   截图里原件停在 1/12、备注与第 1 张 `CWH-a63387bc…`（211067100410）库里的 notes 一字不差：用户看的是第 1 张。
2. **卡点：地址栏不跟着换单。** 编辑页翻原件页时左边换成那页的单（照旧版联动），下拉选发票也换单，
   但地址一直停在最初打开的那张，截图、刷新、复制链接都对不上屏上的单。
3. **线上同样存在。** 56c1807e 与本基线在 `intake-upload.js`、`viewer.js`、`claude_ocr/*`、
   `ocr/execution/claude.py`、`history/storage.py`、`history/api.py` 上 `git diff` 为空，不是 11-06 的提交改坏的。

## 本次修改（A）

- `apps/pearnly_cowork/pearnly_cowork/public/desk/intake-upload.js`：`p.intake_follow_route`；
  从地址打开的编辑页（`new/<票>`）每次换单（`open_record`：翻原件页、下拉选发票）用 `history.replaceState`
  把地址换成 `new/<当前票>`，框架 `current_route` / `current_sub_path` 一起改；不重走路由、不多后退记录、不改数据。
  新建上传那一批（地址 `new`，不指哪张票）不动。
- `tests/test-intake-follow-route.cjs`（进 `development.yml`）：跑真实文件；还原修复 3/3 失败。
- 共用件闸门命中「Cowork 录入上传」（`cowork-intake-upload`）：
  - `cowork.intake.viewer`：通过（testcowork 真实 12 页原件，翻页预览、换单正常；地址跟着换，见截图 2）。
  - `cowork.page.intake`：本机实验栈流程通过（见下「验证」）。
  - `cowork.intake.upload`、`shared.upload.shrink-photo`、`cowork.tax-documents`：没测到（上传、压缩、识别推送路径不经过本改动：新建上传时 `name` 为空，不调用；付费识别不跑）。
- 与 `pearnly/cowork-line-item`（按行选物料）：该分支没改 `intake-upload.js`，无冲突。

## 验证

- 本地：`node --test tests/test-intake-follow-route.cjs` 3/3；`development.yml` 里 38 个 node / 检查脚本步骤全过；架构、文案、产品地图、吞错闸门通过。
- 本机回归实验栈（本分支源码）：`cowork.intake.review` 新 / 旧数据 × 电脑 / 手机 4/4、`cowork.field-help` 4/4、`cowork.keyboard.intake` 1/1 通过。
- testcowork（测试机实验栈，挂 main）只读：取站点上的 `intake-upload.js`（sha 与基线一致），页内套上本改动执行，
  从 ea82 打开 → 翻到 1/12 → 地址与 `frappe.get_route()` 变成 a633、`history.length` 不增；按新地址刷新落在「发票 1/11」。未保存任何数据。

## 待办 B（未做，主会话排期）

- **现象：** 票面印「Page 1/2」、第 2 页不在文件里的多页票，识别只给表头，`lines` 为空（第 1 页明明印了约 12 行明细）。
  复现单：同一份原件（`CWS-e1dca0af…`，本机 `~/Downloads/ราคาซื้อ สินค้า BJC.pdf`）的第 1 张 211067100410（`CWH-a63387bc…`，
  明细、未税、税额、合计全空）、第 7 张 221067100014（`CWH-f71b2d24…`，明细空，读出未税 38869.00、税 2720.83，合计空）。
  合计印在缺的第 2 页上，合计空是对的；明细空不对。
- **怀疑环节：** 12 页按段读（`claude_ocr/segments.py`）；第一段 Haiku 读后自检不过（`documents[i].total missing` 等）→
  `engine.run` 交 Opus 整段重读、`read = second` 整份采用 → Opus 给了空 `lines`。`ocr/execution/claude.py` 的 lines→items 原样映射、
  `segments.merge` 不删行，本端没丢。
- **修法方向：** 提示词写明「多页票只看到部分页时，照样逐行转录本页印出的明细，合计没印就留空」；
  自检加一条「票面有明细表但 lines 为空」交复读；或 Opus 结果 lines 为空而 Haiku 有行时按字段合并保留 Haiku 的明细。
- **闸门：** 识别提示词 / 自检改动，先用 549 份回放基准比新旧，再过金标闸门与并发压测，一次一个变量，不在生产试。
