# 需求影响评估：DMS LINE 预定单带出车型赠品、车型选项显示「รหัสรุ่นรถ / ชื่อรถ」

> 状态：评估稿（2026-11-23），待主会话审定；第 12 节有 4 项要先拿原生证据（一次只读核对）再定施工细节。
> 分支 `pearnly/dms-line-gifts-model-name-534002`，基线 `b77bbb488`（= 开工时 origin/main）。

## 0. 需求原文与理解

- 原话（用户 11-22 提，主会话转述、用户已确认）：
  1. LINE 录「预定单」选车型后，带出该车型主档里的「รายการของแถม」赠品列表（名称 / 数量 / 单价 / 金额 / 单位 + 合计），LINE 预览卡和编辑器都显示、只读，与 DMS 网页手动录入预定单时带出的一致（例：车型 VUK60 有 4 项赠品，合计 20,634.13）。
  2. LINE 匹配车型后给的选项显示「รหัสรุ่นรถ / ชื่อรถ」（例 `VUK60 / Mu-X 2.2 Biturbo RS 4WD`），不是现在的「รหัสรุ่นรถ / ชื่อรุ่นรถ」。
  3. 附：LINE 提示「例如 dmax」但搜 dmax 没结果。
- 理解：销售在 LINE 订车逐问里搜车型 → 选项按钮/列表显示「代码 / ชื่อรถ」；选定后预览卡和 LIFF 编辑页里多一块只读的赠品表（来自该车型在 DMS 车型主档里的赠品明细）。
- 不在范围：车型活动（แคมเปญ）、贷款表、在 LINE 里增删改赠品、颜色/其他选项的标签、DMS 网页本身。

## 1. 现有同类与复用

- **DMS「网页」是第三方 DMS 系统**（`drfcbc/*.php` 原生订车单），Pearnly 不托管它；所以「复用网页的服务端逻辑」在本仓的对应物是：
  - 车型主档完整表单已在**车辆快照**里：`client/catalogue_schema.py` 的 `car` 表含 `txtmodelcode / txtmodelname / txtdescmodelname` 和赠品 6 列 `txtfbproduct[] txtfbunits[] txtfbquantity[] txtfbprice[] txtfbamount[] fbproductval[]`（09-24 DMS-TEST 只读核实，见 `docs/work/2026-09-24-dms-snapshot-protocol.md` 第 85 行）。由 `adapter/snapshot_sync.py` 按 `vehicles` 数据集发布到 `snapshots/store`，`data["catalogues"]["car"][车型id]["fields"]`。LINE 逐问和编辑页本来就读这份快照（`booking_qa_sync` / `editor/edit_snapshot`），**不需要新增任何 DMS 实时读取**。
  - 公司设置 `setcbc.cbhidefreebiesprice`（「隐藏赠品价格」勾选）已在公司快照里（`client/company_catalogue_schema.py`）。
- 车型标签与搜索：`booking/conversation/qa_util.py` 的 `car_label`（`row[1] + row[2]`）与 `norm_row`（搜 `row[1]`、`row[2]`）；调用方 `booking_qa.py`（搜索、选中存 label）、`qa_cards.car_results`（选项）、`master_contract._ANSWER_SPECS`（确认瞬间用**实时** bshsd 行重算 label 做变更比对）、`editor/booking_edit.py`（编辑页车型下拉）。
- 预览卡：`qa_cards.preview_card`（已有 `booking_car_details.rows(qa)` 一段「车型规格」，但它读的 `master_snapshot` 行被压成 3 列，实际一直为空——只记一句，不在本次处理）。
- 编辑页：`frontend/src/pages/dms/EditorPage.vue`（LINE 一次性票据编辑器，ADR 限定例外，Frappe UI），接口 `editor/browser_api.py`（`browser_draft` / `browser_paints` …）。
- 结论：**扩展现有**——快照里加一个只读取数函数（按车型 id 取赠品与 ชื่อรถ），预览卡、编辑页复用同一函数；不另写一套读取。

## 2. Frappe 原生能力

- 不涉及 DocType / Desk 页面。LINE 侧是 Flex 卡片（沿用 `_kv_row` 等现有构件）；编辑页沿用其 Frappe UI 现有表格/只读字段写法，不加自定义样式。

## 3. 按类通用

- 类别：「订车单上由车型带出的主档明细」。同类入口只有两个：LINE 逐问预览卡（含编辑后重发的预览卡，同一个 `preview_card`）和 LIFF 编辑页。DMS 没有别的录预定单入口（CC 不碰 DMS）。
- 车型标签同一个 `car_label`，改一处即覆盖：逐问选项、颜色提问里的车名、预览卡车型行、编辑页下拉、确认复核。

## 4. 上下游数据

- **关键待证（E3）：DMS 原生预定单是「只显示」赠品，还是把赠品行随单提交（`drfcbc/new.php` 里带 `txtfb*[]`）。**
  - 若只显示 → 本需求只做显示，提交载荷不变（最小改动）。
  - 若随单提交 → 现在 LINE 建的预定单在 DMS 里**一直缺赠品行**，这是写入路径的真实差异；要与网页一致就得在 `_apply_booking_form_fields` 里按原生字段带上赠品行，并在回读比对里登记。这会动写单路径，风险与范围都更大，**单列一项由主会话/用户定**（第 12 节 Q1），不在未定前顺手做。
- Express / CC 桌面版 / Cowork / ERP：不涉及（DMS 独立产品，无 Express 映射，载荷不经 CC）。
- 快照：只读已发布数据，不改快照结构与采集。

## 5. 隔离与权限

- 赠品取自本租户本端点的车辆快照（`Directory(endpoint)`，已按端点与读取身份隔离），与车型/颜色同一范围；编辑页接口沿用现有票据 + nonce 校验（`_review`），不新开权限。
- `cbhidefreebiesprice` 若为勾选：网页是否隐藏单价/金额要按 E4 证据照做（同公司设置）。

## 6. 入口

- LINE（手机）：逐问车型选项、颜色提问、预览卡、编辑后预览卡。
- LIFF 编辑页（手机浏览器）：车型下拉标签 + 赠品只读表（换车型时随之刷新）。
- 电脑网页 / 拍照 / CC：不涉及（DMS 网页是第三方；拍身份证入口不变）。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files`（qa_util / qa_cards / directory / booking_edit / browser_api / EditorPage.vue / messages.tsv）输出：

```
[manual] shared.keyboard.dms-editor  DMS · LINE 订车编辑页（LIFF #/dms/editor）
[manual] dms.line.chat  DMS · LINE DMS 测试号一对一（订车逐问各按钮、LIFF 编辑回传）
[manual] dms.line.binding  DMS · 人员页绑定码 / 解绑 / 停用
[manual] shared.shell-gate  共用 · 产品与功能开通闸
[manual] dms.browser-text  DMS · DMS 编辑页
```

- 实测覆盖 `dms.line.chat`（逐问全程到预览卡 → 编辑 → 回传预览卡）、`dms.browser-text` / `shared.keyboard.dms-editor`（编辑页赠品表与底部键盘/操作条不遮挡）；`dms.line.binding`、`shared.shell-gate` 本次不改其代码，按清单标「没测到（未改动）」。

## 8. 存储与成本

- 无新增存储、无新增 DMS 登录或外部调用（只读已发布快照）。LINE 消息条数不变（赠品并进同一张预览卡，不另发）。
- Flex 卡体积：每项赠品 1 行，常见 ≤10 项；LINE 单条 Flex 上限 30KB，预览卡现有约 6–8KB，余量足够；超过 N 项（建议 15）时只列前 N 项 + 「另 X 项」+ 合计，防止整条被拒。

## 9. 删除、作废、追溯

- 只读显示，不产生数据；不涉及作废/删除。若 Q1 定为随单提交，追加：回读核对赠品行是否写入。

## 10. 文案与官方 UI

- 改已有文案：无（车型选项只改拼接格式，不改词条）。
- 新增词条（四语齐，官方简短；泰文以 DMS 原生叫法为准）：
  - `dms.card.qa.LBL_FREEBIES`：รายการของแถม / 赠品 / Freebies / 景品
  - 表头沿用 DMS 原生列名：ชื่อสินค้า / จำนวน / ราคาต่อหน่วย / จำนวนเงิน / หน่วย（中英日对应：名称·数量·单价·金额·单位）
  - `LBL_FREEBIES_TOTAL`：รวม / 合计 / Total / 合計
  - （仅超长时）`TXT_FREEBIES_MORE`：อีก {n} รายการ / 另 {n} 项 / {n} more / 他 {n} 件
- 车型标签格式：`{รหัสรุ่นรถ} / {ชื่อรถ}`；ชื่อรถ 为空时回落现在的 ชื่อรุ่นรถ。LINE 快捷按钮标签 ≤20 字，超出按现有 `_pick_rows` 截断规则（完整标签在选项文字列表里）。
- 交付附 `python3 scripts/check-copy.py --report`。

## 11. 测试与验收

- 单元：赠品取数（有/无赠品、缺车型、金额 Decimal 合计、隐藏价格设置）、预览卡行、车型标签（有/无 ชื่อรถ）、搜索按 ชื่อรถ 命中（若 Q2 同意）、确认复核不因标签格式变化误报「主档已变」。
- 离线：`python3 scripts/check-dms-offline.py`、`check-architecture.py`、`check-copy.py`、闸门。
- 测试环境：`testenv.sh lab-start dms`，三星走 testdms LINE 入口真机：搜车型 → 选项文字/按钮 → 选 VUK60（或 DMS-TEST 里带赠品的车型）→ 预览卡赠品与合计 → 编辑页赠品表、换车型刷新 → 保存回传预览卡。截图（选项、预览卡、编辑页）发主会话。只用测试账号，不碰真实 LINE 绑定；不点确认建单（建单路径未改，若 Q1 定为写入另议）。
- 验收：主会话对照用户截图（VUK60 4 项、合计 20,634.13）。

## 12. 要先拿的证据 / 要定的点

### 原生证据（一次只读核对，需主会话排生产 SSH）

| # | 要核实 | 来源 | 为什么 |
| --- | --- | --- | --- |
| E1 | bshsd `txtcar` 返回行里哪一列是 ชื่อรถ（`txtdescmodelname`？），现 `row[2]` 是不是 ชื่อรุ่นรถ | 生产车辆快照 `masters.cars` 一行（只取 VUK60 那行列值） | 决定标签取行内列（确认复核用实时行，同源最稳）还是取快照主档表单字段 |
| E2 | VUK60 的赠品 6 列 + `txtmodelname / txtdescmodelname` | 生产车辆快照 `catalogues.car[VUK60]` | 对上 4 项、合计 20,634.13，确认合计 = Σ`txtfbamount[]` |
| E3 | 原生 `drfcbc/form.php` 选车后赠品从哪来、`new.php` 是否提交 `txtfb*[]` | DMS-TEST 只读登录读表单字段名与其引用的静态 JS（不点保存，不落 HTML） | 决定只显示还是要随单写入 |
| E4 | `setcbc.cbhidefreebiesprice` 各租户取值，勾选时网页隐藏哪些列 | 生产公司快照 + E3 同次 JS | 「与网页一致」 |

E1/E2/E4 只读快照表（web 容器里 bench console，一次 SSH，不登录 DMS、不输出客户字段）；E3 需走现有 `run_logged_in(authoritative_read=True)` 登录 DMS-TEST 一次（09-24 用户批过同类只读核对）。

### 要主会话 / 用户定

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| Q1 | 若 E3 证明网页随单提交赠品，LINE 建单是否也要写入赠品行 | 先只做显示（用户原话「只读」），写入另起一项、带回读核对，用户点头再做 | 不写：LINE 建的单在 DMS 里仍无赠品行（与网页建的单不同）|
| Q2 | 搜索是否加按 ชื่อรถ 匹配（解决「例如 dmax 搜不到」） | 同意：`norm_row` 加一列，成本极小，与标签同源 | 不做：提示里的例子继续搜不到，或改提示文案（属改已有文案，要给用户看）|
| Q3 | 赠品很多时卡片截断条数 | 15 项 + 「另 X 项」，合计始终是全部 | 不定就按 15 |

## 13. 工作量与排期

1. 主会话批 E1–E4 一次只读核对（约 10 分钟机器时间）。
2. 施工（证据定后）：取数函数 + 预览卡 + 标签/搜索 + 编辑页接口与表格 + 四语词条 + 单测，约 1–1.5 小时。
3. 离线检查 + 闸门约 20 分钟；testenv lab-start dms + 三星真机约 40 分钟（需排队测试机/三星）。
4. 变基、public-scan、推分支、等 CI 约 20 分钟；主会话批准后合入、lab-stop 回读、清理。

## 主会话批复（11-23）

- 方向批准：复用车辆快照、`qa_util` 一处改、不加实时读取。
- E1/E2/E4 生产只读由主会话执行，脚本 `~/pearnly-devtools/prod-ro-1123/dms-gifts/`；E3 DMS-TEST 只读登录一次，已批准，并入同一脚本。
- Q1：本次只做显示，不写入；E3 结果照记，写入另立一项等用户定。Q2：做。Q3：15 项 +「另 X 项」，新文案四语齐、过 check-copy。

## 另议

- 预览卡「车型规格」几行（`booking_car_details`）读被压成 3 列的快照，一直为空；已记进共享笔记「已知问题与结论.md」，主会话另排。

## 原生证据结果（11-23，主会话经甲骨文中转跑 `prod-ro-1123/dms-gifts`，输出 `out/dms-gifts.txt`）

- E1：DMS 车型下拉行（bshsd `txtcar`，17 列）第 1 列 = รหัสรุ่นรถ（`VUK60`）、第 2 列 = ชื่อรุ่นรถ（`TFS41GWRHRVU`）；**ชื่อรถ（`txtdescmodelname` = `Mu-X 2.2 Biturbo RS 4WD`）不在下拉行里**，只在车型主档表单。
  → 施工：选项与显示用「代码 / ชื่อรถ」，ชื่อรถ 取车辆快照的车型表单；确认复核仍比原来的 `car_label`（代码 + ชื่อรุ่นรถ），所以不会因显示名改变而误报「主档已变」。显示名另存 `answers.car.display`，旧会话没有它时回落原 label。
- E2：ris EP-00001 共 550 个车型，430 个有赠品；VUK60（id 558）4 项，逐行「数量×单价=金额」都对，合计 20,634.13，与用户所说一致。testdms EP-00057 有 6 个车型、2 个有赠品，没有 VUK60。
- E4：`setcbc.cbhidefreebiesprice` 在 ris、testdms 两站都开着（'1'）。用户截图里的网页录入界面仍显示单价、金额与合计，可见这个设置管的不是录入界面（推断管打印）。LINE 卡片与编辑页给销售看，照录入界面显示单价、金额与合计，不跟这个设置走。
- E3：DMS-TEST 新增预定单空表单里有「รายการของแถม」明细区（`frmfreebies` / `showfreebies`，带「加一行」`newfreebies()`），说明网页上的赠品是**随单录入的明细**；空表单里没有 `txtfb*` 输入框（选车后由脚本加进去）。引用的 5 个 JS 按相对路径没读到（0 字节），所以没拿到选车时自动带出的那段代码。按 Q1 批复本次只显示；「LINE 建单是否把赠品写进 DMS」另立一项，等用户定，开工前要补读那段 JS 和一张已有预定单的赠品行。

## 需求更正与「写入」评估（11-23 用户纠正，经主会话）

用户原话：要和在 DMS 网页人工录订车单一样**写入**；选车型自动带出赠品；编辑范围照 DMS 原生（原生能改的能改，原生不能改的只读）。此前「只显示」作废。

### 原生证据（`prod-ro-1123/dms-gifts2`，DMS-TEST 只读登录一次，读 `drfcbc/component/js/form.js` 全文与 3 张已有带赠品的订车单）

- **选车带出**：选车型时先 `clearfreebies()`，再 POST `drfcbc/component/cardffreebies.php`（`idcar=<车型 id>&hfbp=<h|s>`），返回 JSON `[项数, 合计数量, 合计金额, 赠品行 HTML]`，行 HTML 直接插进 `#showfreebies`。换车型 = 清空后重新带出。
- **提交字段**（每行一组，同名数组，按行顺序）：`txtfbproduct[]`（商品名）、`txtfbquantity[]`（数量，`1.00` 格式）、`txtfbunits[]`（单位名）、`txtfbprice[]`、`txtfbamount[]`（隐藏，`250.00` / 千分位格式）、`fbunitsval[]`（单位 id）、`fbproductval[]`（商品 id）；另有隐藏 `hfbp`（h=隐藏价格 / s=显示）。车辆快照的车型表单里**没有** `fbunitsval[]`，所以以 `cardffreebies.php` 的返回为准。
- **可改范围**：商品可从商品主档重选（同一商品不能出现两次）；数量可改（空或 ≤0 自动成 1.00）；单位只读；单价、金额隐藏，金额 = 数量 × 单价自动算；可加行、可删行。单子状态为「ใบจอง」（`stcbc=2`）后，赠品整块只读、不能加删。
- **必填**：没有赠品也能提交；某行填了商品或数量就两样都要有，否则原生提示「รายการของแถม รายการที่ N ยังไม่ได้กำหนด」。
- **隐藏价格**：`hfbp=h`（ris、testdms 都是）时，原生订车单的赠品行只显示商品、数量、单位，单价、金额和合计金额都隐藏（仍随单提交）。**这与上面 E4 的判断相反**：E4 依据的截图很可能是车型主档表单，不是订车单。照原生应跟这个设置走，待主会话定。
- **已有缺陷（属实）**：DMS 客户端提交表单用 Playwright `form=` 字典，每个字段只能发一个值；改单（`booking_amend` → `drfcbc/edit.php`）用 `_parse_form_defaults` 把表单读成字典，同名数组被压成最后一个值。所以带多行赠品的订车单（例如 VUK60 的 4 行）经我们改单后会只剩一行。

### 写入方案

1. 传输层：`client/transport.post` 支持同名数组（值为 list 时按原生顺序重复键、urlencoded 正文发送），只影响传了 list 的调用；`_parse_form_defaults` 对 `[]` 结尾的名字保留成 list。建单、改单共用。
2. 建单：提交前（权威预检那一次会话内）调 `cardffreebies.php` 取所选车型的原生默认赠品行，按会话里用户的修改（改数量、删行、加行）合成最终行，写进 `drfcbc/new.php`；没有赠品就不发这几个字段（同原生空表单）。
3. 会话与显示：LINE 选车 / 编辑页换车时，用车辆快照的赠品（名称、数量、单位、单价、金额）预填并显示；`hfbp` 照公司设置（按主会话定）。编辑页按原生可改范围：改数量、删行、从商品主档加行（商品快照已有 `txtproductcode/txtproductname/txtprice/unitsval/txtunits`），单位只读，金额自动算；同一商品不能重复。
4. 回读：建单后回读 `drfcbc/form.php?status=e`，逐行核对赠品商品 id、数量、金额与提交一致，不一致按现有回读规则报「结果需核对」。
5. 改单（回执差异修正）：数组字段原样保留并提交；改单不改赠品（原生状态为「ใบจอง」时赠品只读）。
6. 作废 / 删除：赠品是订车单的子行，原生作废 / 删除订车单时随单处理，我们不另做。实测建的测试单按原生方式作废。

### 改动面与验证

- 共用件：`client/transport.py`（DMS 全部写路径）、`client/client_forms._parse_form_defaults`（建单、改单、回读）。需跑 DMS 离线检查、`sample-verify-dms-*`（候选车道）、受影响流程，并在 testdms 真建一张单后回读、作废。
- 机器耗时估计：实现约 2–3 小时（传输数组、建单合成、编辑页可改、回读、单测），离线检查和闸门约 40 分钟，测试环境实测约 40 分钟。

### 主会话定案（11-23）与施工结果

- **隐藏价格照原生**：读 DMS 公司设置 `setcbc.cbhidefreebiesprice`（车辆 / 公司快照），开着时 LINE 卡片和编辑页只显示名称、数量、单位，不显示单价、金额、合计；价格照原生随单提交。设置读不到按隐藏处理。上面 E4「照录入界面显示」作废。
- **这是修现有缺陷**：改动前，带多行赠品（或多行加装件）的订车单，经我们改单（回执差异修正）或建单后挂身份证附件，提交时同名数组只剩最后一行，DMS 上只剩一行赠品。现在 `client/transport` 在值为 list 时自己编码正文，三条整单回写路径（建单 `new.php`、改单 / 挂附件 `edit.php`）都按原样带回全部 `[]` 行；其余请求不变。
- **写入**：建单时同一会话调原生 `cardffreebies.php`，用户没动过赠品就原样写入；改过则按（商品 id，数量）合成，新加商品取原生商品选择器的单位与单价。
- **编辑页**：改数量、删行、从商品主档加行（不列停用商品、同一商品不重复），单位只读，金额 = 数量 × 单价；服务端重算并拒绝不认识的商品。
- **回读**：开单回执复核加「赠品」一项（逐行比商品 id 与数量），差异走现有差异卡，「更新」写回提交时的整组数组。
- **测试**：`tests/test_dms_booking_freebies_write.py`（传输编码、数组解析、原生返回解析、合成规则、多行建单 / 改单 / 挂附件 / 差异写回、编辑意图）、`tests/test_dms_car_freebies.py`（显示、隐藏价格、编辑规则、快照取数），均进 `check-dms-offline.py`。
