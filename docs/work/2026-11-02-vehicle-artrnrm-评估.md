# 需求影响评估：按台号码写进 Express 单据附加说明（ARTRNRM）

分支 `pearnly/vehicle-artrnrm-410666`，基线 `f102c0fe`（= origin/main，11-02 11:10）。主会话派活：10-25 岳父需求里唯一没做完的一项；
用户 11-01 定按台管理照旧保留、这件照旧要做。本文只评估，审定后施工。

## 0. 需求原文与理解

- 原话：岳父 10-25（记忆 `pending-vehicle-dealer-fields` 第 5 条）——税票照标签红笔标注印：**车架号全 17 位、发动机号照原样（如 FUK575）、
  颜色码（如 ELG）**，挂在商品行下面。样张：`~/pearnly-devtools/found-images/岳父-Isuzu车辆标签A4-带二维码.jpeg`（MP1TFR41JTG032993 / FUK575 / ELG）。
- 现状：ERP 自己的打印已经在行下印号（`printing/_labels.html` 宏 `vehicle`：一台一行「เลขตัวถัง … เลขเครื่อง … สี …」）；
  **接了 Express 的公司，CC 写进 Express 的单不带号**，在 Express 里打税票看不到车号（复核报告 `2026-11-01-serial-tracking-review.md` 第 3 条）。
- 理解：开了按台管理的公司（汽车 / 手机电器类型），单据上选中 / 登记的每一台的号码，随 CC 写进 Express 的那张单，写在**那一行的附加说明**
  （Express 工具栏「Note」录的「ป้อนหมายเหตุเพิ่มเติม」，表 `ARTRNRM`），Express 打印时印在商品行正下方。
- 不在范围：Express 整单说明（SEQNUM「@n」）；从 Express 读回号码；改 ERP 按台逻辑 / 打印；Cowork（识别来的单没有按台号码）；
  代录（另见第 12 节第 4 问）。

## 1. 现有同类与复用

| 同类 | 位置 | 结论 |
| --- | --- | --- |
| 行上的台 | 各单据子表 `serials`（`ERP Line Serial`：`line_no` = 明细行 idx、`serial_no / secondary_no / color`）；读法 `inventory/serials.line_serials`、打印 `printing/context._units` | **复用**：载荷按同一口径取 |
| 行下印号的文字 | `printing/contracts.vehicle_lines` + 宏 `vehicle` | 同一内容，按 Express 50 字节一条拆（第 4 节） |
| 行号右对齐写法 | `companion/.../dbf_schema.append_native`（STCRD / GLJNLIT / ARTRN… 的行号按字段宽右对齐） | **扩展**：ARTRNRM.SEQNUM 是「行号右对齐 3 位、再补空格到 8 位」，不是按 8 位右对齐，要单独一种写法 |
| 写前备份 / 回滚 / 重建索引 / 读回 | `dbf_writer._backup / _restore / _reindex_or_defer / _verify_index` | **复用**：ARTRNRM 进备份表和重建索引表（只在这张单带号时） |
| 作废原单 | `dbf_void`（OU / JU / TK / RL） | **扩展**：照原生给行说明打删除标记（RL 除外，见第 9 节） |
| 版本协商 | `payload_contract.SUPPORTED_PAYLOAD_VERSIONS`（main 到 16） | 升一版（暂记 17，合入时取 main 最大值 + 1） |

结论：CC 新增一个小模块（`dbf_remarks.py`：拼行、写、作废、读回），各写入器调用；服务端在现有载荷行上加一键 `remarks`。不新建 DocType、不改 ERP 界面。

## 2. Frappe 原生能力

不涉及：这是 ERP → CC → Express DBF 的写账链路，没有界面；ERP 里的台仍是现有自建 DocType（站点不装 ERPNext，原生 Serial No 不可用，09-21 已定）。

## 3. 按类通用

- 一类 = **所有带按台号码、会写进 Express 的单据明细行**。按台管理只在汽车（vehicle：车架号 / 发动机号 / 颜色）和手机电器（device：S/N / 第二个号 / 颜色）
  两类公司生效，两类都覆盖。
- 逐单据：

| ERP 单据 | Express 单别 | 现在行上有没有台 | 本次 |
| --- | --- | --- | --- |
| 进货（赊 / 现） | RR / HP | 有（登记） | 写 |
| 销售单（赊 / 现） | IV / HS | 有（选台） | 写 |
| 入库「其他 / 退货」、出库「领用 / 报损 / 其他」 | JU / OU | 有 | 写；作废时照原生删 |
| 调拨 | RL | 有（按仓位选台） | 写；作废照原生留 |
| 收银日汇总 | HS（按品 × 仓 × 单位合并多张小票） | 小票上有 | 写（同一行汇总当天各小票的号，见第 12 节第 2 问） |
| 收银退货日汇总 | SR | 退货小票上有 | 写（同上） |
| 盘点 | TK | 不改台（10-26 定） | 不写 |
| 采购 / 销售单在 ERP 作废 | — | ERP 现在不作废 Express 里的采购 / 销售单（`push.void` 只开库存单） | 不涉及 |

## 4. 上下游数据

### 4.1 Express 原生写法（依据）

1. **10-26 服务器测试账套实录**（`70EXP\test`，IV0000040、RR0000014，`docs/work/2026-10-25-express-native-rules.md` 4.9）：
   - 一条说明 = ARTRNRM 一行：`DOCNUM C12` = 单号、`SEQNUM C8` = 明细行号**右对齐 3 位再补空格到 8 位**（「  1     」）、`REMARK C50`；
     同一行多条按录入顺序；只写 ARTRNRM，ARTRN / STCRD 不多任何字段；赊购 RR 同一张表。
   - 界面一行最多 **10 条**；50 字符那条完整存下。
   - 打印印在所属商品行正下方，每条一行，不印行号；这张表单约 40 个显示列宽，超过压到「คลัง」栏竖线。
   - 作废 IV：这张单的行说明**全部打删除标记**（字段不改）。
2. **11-02 S 盘副本只读统计**（测试机 `C:\PearnlyStress\S盘副本`，脚本只读 DBF 字节，1,040 本账套、962 万条）：
   - 1,040 本表结构完全一致（DOCNUM C12 / SEQNUM C8 / REMARK C50），**每本都有 `ARTRNRM.CDX`，标签 `DOCNUM+SEQNUM`** → CC 写完要重建这张表的索引
     （同其他表，走 `cdx_reindex_runner`），否则 Express 查不到。
   - 行说明 SEQNUM 形状：「  9     」303 万、「 99     」16.5 万（右对齐 3 位补到 8），与实录一致；「@n」是整单说明。
   - **每行最多 10 条，962 万条里没有一行超过 10**（1 条 85 万行、3 条 26 万、10 条 4,227 行）。
   - 行说明出现在 IV / HS / RR / HP / SR / OU / JU / RL 等单别（例：GIS 的 RL「S/N:NPB2017390124U」、RR「S/N.NPB201850664S  NPB201850665S …」一条放几个号）。
   - 作废单（单头 DOCSTAT C）的行说明：IV 删 1,213 / 留 0、RR 删 73 / 留 0、SR 删 179 / 留 0、OU 删 17 / 留 0、JU 删 6 / 留 0；
     **RL 留 157 / 删 5**（调拨作废原生保留行说明，与 RL 作废「明细只打删除标记、字段不改」同一套）。
   - 泰文按 cp874 存（约 70% 的行带泰文字节）。
3. **待补实录**（要人在测试机登录 Express，已发主会话）：现购 HP 录行说明 + 作废；行说明超过 10 条时 Express 打开 / 打印的表现（第 12 节第 1 问）。

### 4.2 写法（CC）

- 载荷明细行加 `remarks: [str, …]`（已拼好的说明，每条 ≤ 50 字节 cp874）；CC 写完这张单的 STCRD 后，按行号逐条追加 ARTRNRM
  （DOCNUM = 写出的单号，SEQNUM = 那一行 STCRD 的行号照上面的写法，REMARK = 原文），同一事务、同一份写前备份、同一次重建索引；读回核条数。
- 行号对应：说明随载荷明细那一项走，CC 用写入器给这一项实际写的 STCRD 行号（`dbf_detail` / `dbf_stock` / `dbf_transfer` 是载荷顺序 1..n；
  退货 `dbf_refund` 只数库存行，取它自己记的行号）；RL 每件一对同一行号，说明挂一次。服务端在 ERP 行上就把号码挂到那一行（`contracts.document` 过滤数量 0 的行之前），不按位置对。
- 没带 `remarks` 的载荷写出来与现在**逐字节相同**（不碰 ARTRNRM、不进备份 / 重建清单）。

### 4.3 号码拼成说明（建议，第 12 节第 3 问定）

| 类 | 每台 | 例 |
| --- | --- | --- |
| 汽车 | 两条：「เลขตัวถัง {车架号}」「เลขเครื่อง {发动机号} สี {颜色}」（空的项不写） | เลขตัวถัง MP1TFR41JTG032993 / เลขเครื่อง FUK575 สี ELG |
| 手机电器 | 一台一个号段「{S/N}[/{第二个号}][ {颜色}]」，按原生习惯「S/N 」开头、两个空格隔开，**一条装满 50 字节再换下一条**（15 位 IMEI 一条 2 个，13 位 S/N 一条 3 个） | S/N 352948094653751  352948094652259 |

- 为什么汽车拆两条：标签那三项连同 ERP 打印的泰文标签放一条是 52 字节以上（车架号 17 位 + 发动机号 + 颜色 + 三个标签），超过 REMARK 50；
  拆成两条每条约 25–30 字节，打印也不压线。
- ERP 打印（一台一行）与 Express 打印（汽车一台两行）内容相同、行数不同；都在商品行正下方。

### 4.4 超过 10 条

汽车两条一台 → 一行最多 5 台；手机 15 位 IMEI 一条 2 个 → 一行最多 20 台。收银日汇总按品合并、进货一行多台时可能超过。处理办法见第 12 节第 1 问。

### 4.5 载荷与版本

- 服务端：`express/push._lines`（进货 / 销售）、`stock_entry.document`（出入库 / 调拨）、`pos_day.document`（收银日汇总 / 退货日汇总）给行带 `remarks`；
  `contracts.document / pages` 照带；共用写账规则 `express_rules/push/common.py` 规整明细时照带（同 `factor`、`vat_exempt` 的做法），
  `finalize_payload` 见到 `remarks` 盖新版本号（暂记 17）；库存单 / 调拨的版本在 `stock_entry._version` 同样加。
- 旧版 CC：不认 17，诚实拒收、派单只交给报过支持 17 的电脑（现成机制）。不带号的单版本号不变，旧版照常写。
- Cowork / DMS：Cowork 识别来的明细没有 `remarks`，载荷不变；共用规则只是多照带一个键，要按共用件闸门回归 Cowork 推送。DMS 不涉及。

## 5. 隔离与权限

- 号码来自单据自己的 `serials` 子表，跟着单据走现有写账队列（按公司、按账套），不新增读写入口、不跨公司。
- CC 只写派给它的那本账套（现有三重一致性闸），ARTRNRM 与单据同一本。

## 6. 入口：网页、手机、LINE、拍照

- 不新增入口。号码在 ERP 网页 / 手机登记、选台、扫码（现有）；写 Express 是后台。
- 受影响的写账入口：`erp.cc.write`（进货 / 销售）、`erp.cc.stock-entry`、`erp.cc.stock-void`、`erp.cc.stock-transfer`、`erp.cc.pos-day`、`erp.cc.pos-refund`。
- 代录（`cc.desktop.operator`，人开着 Express、CC 在界面里录）：见第 12 节第 4 问。

## 7. 共用件与连带回归

- 改到的登记共用件：`cc-dbf-write`（CC 写 Express），入口 9 个（`cc.desktop.write`、`cc.desktop.operator`、`cowork.cc.channel`、`erp.cc.write`、`erp.cc.stock-entry`、
  `erp.cc.stock-void`、`erp.cc.pos-day`、`erp.cc.pos-refund`、`erp.cc.stock-transfer`）；`express_rules`（共用写账规则，Cowork 推送）。施工前后跑
  `python3 scripts/check-impact.py --files …`，逐入口记「通过 / 不通过 / 没测到」。
- 新增 CC 单测：拼行、SEQNUM 字节、≤ 50 字节、条数、作废删 / 留、回滚、不带号逐字节不变；服务端单测：各单据带号、版本号、数量 0 的行不错位。
- 离线回归：测试机 C 盘 T 账套副本（`C:\PearnlyExpress`）写带号的 RR / HP / IV / HS / OU / JU / RL / SR 各一张，读回 ARTRNRM、核索引能查到；
  另跑全部 T 账套不带号回归，核与现在一致。

## 8. 存储与成本

一条说明 71 字节（12 + 8 + 50 + 删除标记），一台车两条；无外部调用、无月费。

## 9. 删除、作废、追溯

- ERP 作废库存单 → CC 照原生作废原单：OU / JU 把这张单的行说明打删除标记；**RL 留着不动**（原生如此，统计 157:5）；TK 没有说明。
- ERP 作废采购 / 销售单现在不动 Express（不涉及）；人在 Express 里作废时 Express 自己按原生处理说明。
- 写失败整体回滚（ARTRNRM 在同一份备份里）。

## 10. 文案与官方 UI

- 不新增界面文案。写进 Express 的说明用 ERP 打印已有的泰文标签「เลขตัวถัง」「เลขเครื่อง」「สี」（同宏 `vehicle`）和原生习惯「S/N」，不进词条表。
- 写账失败沿用现有错误码（说明超长 → `FIELD_TOO_LONG`），不加新提示。

## 11. 测试与验收

- 单元 + 离线回归见第 7 节。
- 服务器测试账套（69EXP\TEST，用户登录 Express 后）：CC 测试包写一张带两台车的 IV、一张带号的 RL，Express 打开看 Note、屏幕预览（不打纸）看行下印号；
  作废一张 OU 看说明。
- CC 新版本先发 staging，测试机实机升级；正式版由主会话安排。
- 用户 / 岳父要看的只有一件：Express 税票预览上号码的样子（第 4.3 节格式）。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 一行超过 10 条（汽车 > 5 台、手机 > 20 台）怎么办 | 先在 69EXP\TEST 实测：超过 10 条 Express 能正常打开、打印全部 → 照写全部；不行 → CC 不写、这张单转人工（错误码 `REMARKS_TOO_MANY`，ERP 显示原因），不拆行 | 拆行会改 Express 明细行数和金额拆分，影响退货挂原行，不建议 |
| 2 | 收银日汇总 / 退货日汇总也带号吗（一行是当天这个品所有小票合并） | 带（按类通用；手机店最常见）；超过按第 1 问 | 不带则收银卖出的手机 Express 里没有号 |
| 3 | 说明格式（4.3 节：汽车一台两条、手机按 S/N 装满） | 照 4.3 | 决定打印样子；改格式不用改结构 |
| 4 | 代录（人开着 Express、CC 在界面录）遇到带号的单 | 这批直写先做；代录点 Note 录说明另做一步（要实机调界面）；在那之前带号的单不走代录、照直写 | 不定则代录录的单没有号 |
| 5 | 载荷版本号 | 合入时取 main 最大值 + 1（现在会是 17），合前问 | — |

## 13. 工作量与排期

1. 补实录（等人登录 Express）：HP 行说明 + 作废、超 10 条表现 —— 机器约 30 分钟（含拷副本比对）。
2. CC：`dbf_remarks` + 接入 6 个写入器 + 作废 + 版本 + 单测 —— 写代码约 2 小时，单测约 0.5 小时。
3. 服务端：进货 / 销售 / 出入库 / 调拨 / 收银日汇总带 `remarks`、共用规则照带、版本 —— 约 1.5 小时，单测与闸门约 0.5 小时。
4. 离线回归（测试机 C 盘 T 账套）+ 69EXP\TEST 实机看打印 + staging 升级 —— 机器约 1.5 小时。
依赖：2、3 可并行；4 在 2、3 之后；1 的结论决定第 1 问的实现。

## 14. 施工记录（审定前先做的、不影响不带号的单）

- `77138958`（CC）：新模块 `companion/src/companion/dbf_remarks.py`（拼行、按原生字节写 SEQNUM、≤ 50 字节 / ≤ 10 条写前查、读回、作废打删除标记）；
  接入进货 `dbf_writer`、销货 `dbf_sales`、退货 `dbf_refund`（行号只数库存行）、库存单 `dbf_stock`、调拨 `dbf_transfer`（一对明细挂一次）、
  作废 `dbf_void`（OU / JU 删、RL 留）；带号的单才把 ARTRNRM 放进写前备份和重建索引；载荷 17。合成表 `table_specs` 加 ARTRNRM（真表 3 列）。
- `4cee11c0`（服务端）：`express/contracts.unit_remarks`（4.3 节格式；号码再长折行不截断）；进货 / 销售 `push._lines`、出入库 / 调拨
  `stock_entry.document` 按明细行号取单据 `serials` 子表拼说明；`contracts.document / pages` 与共用写账规则 `express_rules/push/common.py` 照带；
  `finalize_payload` 与库存单 `_version` 见到 `remarks` 盖 17；带号的库存单作废也盖 17（旧版不会删说明）。
- 未做（等第 12 节审定）：收银日汇总 / 退货日汇总带号（第 2 问）、超过 10 条的处理（第 1 问，现在 CC 拒写 `REMARKS_TOO_MANY`）、代录（第 4 问）。

### 检查

| 层 | 结果 |
| --- | --- |
| CC 单测（本机 macOS，Python 3.11 + 锁定 dbf） | 1,320 通过；8 条 `test_write_dispatch` 本机缺 PySide6 失败，与改动无关（Windows CI 跑全量） |
| 服务端 | `test_erp_express_direct` 153、`test_companion_contract` 86、Cowork 写账规则 13 个文件全过；`check-architecture` 0 违规；`check-copy` 新增 0 |
| 共用件影响面 | `check-impact`：express-rules 12 个入口、cc-dbf-write 9 个入口，逐入口结果见下节（施工完补） |
| 测试机 C 盘 T 账套离线回归 | `scripts/express-remarks-regression.py`（每本副本：带号进货 + 销货两行、带号领用再作废，核字节、删除标记、写入集合外逐字节不变，正式 CC 的 `cdx_reindex_runner` 重建并核 CDX） |
