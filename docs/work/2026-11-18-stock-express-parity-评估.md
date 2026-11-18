# 需求影响评估：ERP 库存四入口写进 Express 与原生录入一致

> 依据：[`2026-11-18-stock-express-parity.md`](2026-11-18-stock-express-parity.md) 第一节对照表、第二节实录（差异 D1–D13）。
> 主会话 11-18 已定：D1 调整改 JU；D3 增加行照原生；D4 成本照原生批次链；D6–D10 细栏照原生（参考号仍要能追到 ERP 单号）；
> D12 出库按用途选簿；D11 一单一仓保持（11-06 用户定）。D2 入库口径、D5 单号样式待用户定，下面两种都写改动量。

## 0. 需求原文与理解

- 原话：用户 11-17 转述岳父，11-18 要求开工：操作员用手机在 Pearnly 录出库 / 调拨 / 调整 / 入库，自动进 Express；在 Pearnly 录一张同步过去后，
  Express 账套里的变化要和财务在 Express 对应原生菜单（4-1-1 / 4-2-1 / 4-3-1 / 4-3-2）亲手录一张**完全一样**。
- 理解：改的是「ERP 库存单 → 载荷 → CC 桌面版写 DBF」这条链的写法，让结果与原生同菜单录入逐字段一致；ERP 页面只补「用途」选项。
- 不在范围：采购 / 销售 / 收银写 Express 的成本取法（同样是均价，与原生批次链不同，另立一项）；Express 里「计算成本」的全范围行为；
  盘点流程本身（盘点生成的调整单仍写 TK，见 §3）；MR.ERP 写法。

## 1. 现有同类与复用

- ERP：`express/stock_entry.py`（单别、载荷）、`inventory/documents.py`（用途 `PURPOSES`）、`public/desk/stock-entry.js`（页面）。
- CC 桌面版：`dbf_stock.py`（OU / JU / TK）、`dbf_transfer.py`（RL）、`dbf_void.py`（作废）、`stock_sale.py`（`_cost` 均价、`move_stmas`）、
  `dbf_writer._bump_isrun`、`dbf_schema.stcrd_stock_entry_row`。
- 结论：全部在现有模块上改，不新建写入器。PI 与 JU 同属 ISRUN DOCTYP JU，写法共用 JU 那一支，只换簿与对方科目。

## 2. Frappe 原生能力

- 页面只多两个「用途」选项，沿用现有官方 Select；不涉及新 DocType。其余改动在 CC 桌面版（非 Frappe）。

## 3. 按类通用

- 这一类 =「ERP 库存单写 Express」：出库、入库、调拨、物料调整（人建）、**盘点生成的调整单**、历史盘点 AD 单。
- 盘点生成的调整单（`stock_count` 有值）与历史盘点：原生对应正是 4-1-3-3 TK「ปรับปรุงจากการตรวจนับ」，**继续写 TK**；只有人建的物料调整改 JU。
- 作废这一类（OU / JU / PI / TK / RL）随写法一起改，见 §9。
- 采购 / 销售行的成本取法同类不同口径，本次不动，列为后续项。

## 4. 上下游数据

### 4.1 逐项改法

| # | 改什么 | 改哪一层 | 做法 | 难度 |
|---|---|---|---|---|
| D1 | 人建物料调整 → JU | ERP 载荷 | `stock_entry.kind`：`direction=adjust` 且无 `stock_count` → JU；有 → TK。JU 的科目问法沿用 `stock:JU` | 小 |
| D3 | JU / PI 增加行 | CC | 增加行 STCRD POSOPR `1`，带 LOTBAL / LOTVAL / LUNITPR（成为一批），STMAS.MLOTNUM 指向这一行；减少行仍 `8`、NETVAL 0；单头 POSOPR 8、TRNVAL = 净值、ACCNUMCR | 中 |
| D4 | 成本照批次链 | CC | 见 4.2 | 大 |
| D6 | STCRD.STKDES | CC | 写单头备注（原生）；备注空时原生写什么**待实录**（先照 RL 现做法写品名） | 小 |
| D7 | STCRD.MLOTNUM / MREMBAL / MREMVAL | CC | 照 4.2 的批次链写 | 随 D4 |
| D8 | STMAS.LASUPD、进货类 MLOTNUM | CC | 所有库存单 LASUPD = 单据日；增加行把 MLOTNUM 改成本行 | 小 |
| D9 | 单头参考号 | CC | 见 4.3 | 小 |
| D10 | GLJNL.CREBY、GLJNLIT.CHGDAT / CHGTIM | CC | CREBY = 写账用的 Express 用户（与 USERID 同）；分录写 CHGDAT / CHGTIM | 小（共用 `_gl_line`，见 §7） |
| D12 | 出库按用途选簿 | ERP + 页面 + CC | 见 4.4 | 中 |
| D11 | 一单一仓 | — | 保持 | — |

### 4.2 D4 成本照原生批次链

实录的原生规则（70EXP\test，平均成本账套）：
- 出库 / 调整减少：取 STMAS 的批次链结存 MREMBAL / MREMVAL（当前那一批剩下的），单价 = MREMVAL / MREMBAL。
  数量超过批次结存：只按现有批次摊（10-25 实录 4.2：XTRNVAL = 批次剩余金额）。**批次结存 ≤ 0：按 0 成本出，不写凭证**，MREMBAL 继续减成负数，
  MREMVAL 不动，TOTVAL 不动（UNITPR 随 TOTBAL 重算）。STCRD.MLOTNUM 指当前批次，MREMBAL / MREMVAL 记写后批次链结存。
- 增加（JU / PI 调增、进货）：成为新的一批，批次链结存 = 本行数量 / 金额。
- **之后在 Express 里存任何一张增加库存的单**，Express 会把同日（及之后）原来 0 成本的出库行回填成本，**并改那张单已写的凭证金额**
  （实录：OU0000002 的凭证从 0 → 96 → 204.27 → 201.96，跟着后面每张进货变）。

实现：`dbf_stock._cost` 换成批次链取法（同 `_lots` 读 STCRD，不另建状态）；0 成本行不过账（现有「零金额不过账」规则已覆盖）。
难点与风险：
1. **CC 自己写增加库存的单时，不会像 Express 那样回头重算前面 0 成本的单**。要做就得改动别的单（包括财务原生录的单）的明细与凭证，
   风险大，**建议不做**：交给 Express 自己——下次有人在 Express 存一张库存类单、或跑 4-7-4「คำนวณยอดสะสม/คงเหลือ/ลงบัญชี」时它会重算。
   第三步实测一次：CC 写 0 成本出库 → CC 写进货 → Express 跑 4-7-4，核对结果与全程原生录入一致（这是「与原生一致」的最终判据）。
2. **对我们写的单的影响**：我们写的 0 成本出库，之后会被 Express 改成本和凭证金额 → 现在作废时「核原单还是写入时的样子，对不上转人工」
   （`VOID_DOCUMENT_CHANGED`）会误拦。作废核对要改成只核品码 / 仓 / 数量 / 单号，不核成本与凭证金额（原生作废也不看这些）。
3. 先进先出账套（COSMTD = F）本来就按批次；平均成本账套原生也按批次链（实录如此），两种统一一套取法。批次链从未建立的旧物料（MREMBAL 空）
   按 0 处理同原生。
4. 采购 / 销售 / 收银写 Express 用的是同一个 `stock_sale` 均价，本次只改库存单这一支；同一件物料两种口径并存，账面会与原生不同 → 列后续项。

### 4.3 D9 参考号

- 原生这几张单的单头只有「部门、备注」两栏可填，STTRN.REFNUM 原生留空；界面上没有别的可放 ERP 单号的栏。
- 方案甲（建议）：**仍写 REFNUM**。Express 界面不显示、不影响记账与报表口径；ERP 侧本来就按 Express 单号（`ledger_docnum`）追溯，REFNUM 只是双保险。
  这是唯一有意保留的不同，需主会话认可。
- 方案乙：REFNUM 留空，把 ERP 单号拼进备注（会占用户 50 字的备注、也会改变凭证摘要与 STKDES），不建议。

### 4.4 D12 出库按用途选簿

- 用途 → 簿：内部领用 → OU、报损 → ZZ、样品 → XS、生产领料 → PD；其他 / 不填 → OU。
- Pearnly 用途现在只有 内部领用 / 报损 / 其他，要补「样品」「生产领料」两项（新文案，四语）。
- 各账套的簿名与前缀不一定相同（NLTTEST 另有 XA 等）。ERP 先按默认前缀找；这本账套没有这个前缀的簿 → 提交时弹窗问一次（选这本账套里
  DOCTYP OU 的哪一簿，按公司 + 用途记住，同现有「科目问一次」的弹窗），记住后不再问。CC 桌面版主档读回时已报 `stock_books`，
  要扩成「每个前缀的名称与默认科目」供弹窗列出。
- 科目随簿：选了簿就用那本簿的默认科目，原来按公司记住的 `stock:OU` 科目选择**只在默认科目不可记账时**再问。

### 4.5 D2 入库口径（待用户定，两种都列）

| 方案 | 做法 | 改动量 |
|---|---|---|
| A 按来源分 | 入库单加「来源」：生产完工 → PI，其他（退库 / 找到货 / 其他）→ JU；用现有「用途」字段扩选项（入库：退库 / 生产完工 / 其他） | ERP：用途选项 +1、`kind` 按用途选 JU / PI；CC：PI 走 JU 那支换簿与对方科目；GLBAL 首次用到科目时补行（D13）。约 0.5 天机器时 |
| B 一律 PI | 入库都写 PI | ERP：`kind` 改 PI；CC 同上。改动最小，但没有生产的公司会在在制品 11-04-03 挂负数（第二步业务答复） |
| （现状 C）一律 JU | 不改单别，只照 D3 改增加行写法 | 最小 |

### 4.6 D5 单号样式（待用户定，两种都列）

| 方案 | 做法 | 改动量 / 影响 |
|---|---|---|
| A 照原生流水 | 单号 = 前缀 + ISRUN 下一个号（OU0000002），用完 +1 | CC：`_next_docnum` 改读 ISRUN；直写本来就等 Express 关账套才写，不与界面抢号。只改库存单则与 CC 写的采购 / 销售（HP691118-001）不同样；要统一就连采购 / 销售一起改（另一批、共用件大回归） |
| B 保留自编号 | OU691118-001，但**不再把 ISRUN +1**（修掉原生下一张跳号） | CC：库存单 `_bump_isrun` 去掉一行。采购 / 销售同样白涨 ISRUN，要不要一起修另问 |

### 4.7 载荷与 CC 桌面版

- 新载荷版本 22（取合入时 main 上最大 +1）：新增 `book`（出库 / 入库的簿前缀）、`purpose`；ERP 发 JU / PI / 选簿的单一律盖 22，旧版 CC 诚实拒收
  （`ERR_PAYLOAD_VERSION`），网页显示「需要更新 CC 桌面版」现有提示。CC 的写法改动（D3 / D4 / D6–D10）对旧载荷也生效（版本 ≥ 6 的库存单都按新写法）。
- 发布顺序：服务端先上（旧 CC 收到 22 拒收、单子留在队列等更新）→ CC 正式版发布、心跳带 `companion_latest` 自动更新。

### 4.8 已上线租户与已写进账套的旧单

- 主会话 11-18 用只读脚本（`~/pearnly-devtools/prod-ro-1118/stock-ledger-count.sh`）核了生产 7 个 ERP 站点：写进过 Express 的库存单只有测试租户
  的 1 张入库；其余站点的库存单都没写过 Express，或者没有库存单。**线上没有真实客户的库存单进过 Express**，改单别、选簿、单号都没有存量包袱。
- 旧单不回改：已写的 OU / JU / TK 留原样；作废旧单照旧能作废（作废按单头 POSOPR 认单别，D1 之后 JU 作废增加行认 `1`、减少行认 `8`，
  旧 TK 单仍按 `:` 作废）。
- 改完后同一件物料在账套里会出现「旧单均价、新单批次链」两种成本，Express 跑 4-7-4 计算成本会按它自己的规则重排，不需要我们迁移。

## 5. 隔离与权限

- 不涉及新权限。用途选项沿用 `stock.out` / `stock.in`；选簿弹窗沿用现有提交时弹窗（服务端按公司记住，`choices`）。账套仍按 `target.pick` 选，
  CC 只写对话 / 选中的那本。

## 6. 入口：网页、手机、LINE、拍照

- 网页桌面与手机是同一个页面同一个接口（第一步已核），只多两个用途选项；窄屏下 Select 照常。
- LINE 只做盘点，不建这四种单；拍照不涉及；CC 对话（引导式）建库存单的入口若有，同一个 `create_entry`，载荷同一处拼，自动覆盖。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files companion/src/companion/dbf_stock.py companion/src/companion/stock_sale.py apps/pearnly_erp_portal/pearnly_erp_portal/express/stock_entry.py apps/pearnly_erp_portal/pearnly_erp_portal/inventory/documents.py`
命中共用件 `cc-dbf-write`，9 个入口：cc.desktop.write、cc.desktop.operator、cowork.cc.channel、erp.cc.write、**erp.cc.stock-entry**、**erp.cc.stock-void**、
erp.cc.pos-day、erp.cc.pos-refund、**erp.cc.stock-transfer**，另有 erp.stock-count.adjust。

- 控制回归面：`_cost` 不改 `stock_sale` 里的共用函数，只在 `dbf_stock` 里新加批次链取法；`_gl_line` 的 CHGDAT / CHGTIM 若在共用处改会波及采购 / 销售 /
  收银凭证 → 只在库存单调用处补，或单独一批做全部（需主会话定）。
- 入口说明要更新：erp.cc.stock-entry（入库 / 调整的单别）、erp.cc.stock-void；冒烟随之更新。

## 8. 存储与成本

- 不涉及：不加外部调用、不加存储，测试只用测试账套与实验栈。

## 9. 删除、作废、追溯

- 作废照原生：JU / PI 单头 C、明细删除标记、凭证 C 金额空、结存回滚、ISRUN 不回退（同现有 JU 作废）。
- 作废核对改为不核成本与凭证金额（§4.2 第 2 条），否则 Express 回填成本后作废全转人工。
- 先实录一次原生作废 JU（增加 + 减少两行）与 PI，确认增加行 `1` 作废后的字段，再改 `dbf_void`。

## 10. 文案与官方 UI

- 新增两条用途：样品、生产领料（入库方案 A 另加「生产完工」），四语，过 `scripts/check-copy.py`。不改已有文案。
- 选簿弹窗沿用现有「Express 选项」弹窗，不加说明文字。

## 11. 测试与验收

- **先红后绿（离线）**：把第二步的原生实录做成合成金样本（编造的物料与金额，按实录字段结构），`companion/tests/test_dbf_stock.py` 加：
  JU 增加 / 减少行、PI、批次链取价（干净 / 超过批次 / 批次 ≤ 0 → 0 成本不过账）、STKDES = 备注、LASUPD、CREBY / CHGDAT、出库四簿；
  `dbf_void` 加 JU 增加行作废、成本被 Express 改过仍能作废。ERP `tests/` 加 `kind` 的人建调整 → JU、盘点调整 → TK、用途 → 簿与问一次。
- **离线回归**：`scripts/express-stock-entry-regression.py` 101 本跑一遍（每本只写副本）。
- **流程**：推分支自动跑受影响流程（erp.cc.stock-entry 等），推完等 15 分钟以上查一次。
- **真实环境**（测试账套 70EXP\test，同第二步做法，每张对原生逐字段比，用 dbfdiff）：
  1. 四种单 + 出库四簿各一张；
  2. 0 成本出库 → CC 写进货 → Express 跑 4-7-4，结果与全程原生录入一致；
  3. 作废 JU / PI / 出库各一张；
  4. **第二步留下要补的两项**：Pearnly 侧逐格点界面（网页桌面一遍、三星手机网页一遍，不再只调接口）；原生明细附加页（Ctrl+F8）里有哪些栏、
     Pearnly 要不要传。
- 真机：三星手机经 adb 在 ERP 手机网页录一张出库（报损）→ Express 读回与原生一致，截图与 dbfdiff 留证。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 入库写哪一簿（D2） | 方案 A 按来源分（自产 → PI，其他 → JU） | 不定就保持 JU，只改写法（D3） |
| 2 | 单号样式（D5） | 方案 B 先修 ISRUN 跳号；要不要改成原生流水连采购 / 销售一起另定 | 不定就保持自编号并修跳号 |
| 3 | 参考号保留 REFNUM（D9） | 保留（唯一有意不同） | 不保留就要占备注 |
| 4 | 采购 / 销售 / 收银的成本也照批次链 | 另立一批 | 不做则同一物料两种成本口径，靠 Express 计算成本收口 |

## 13. 工作量与排期（机器耗时）

依赖：用户定 #1、#2 不阻塞第 1–3 步。

1. 原生补录（作废 JU / PI、备注空时的 STKDES、Ctrl+F8 附加页）：测试机约 40 分钟（含还原）。
2. CC：D3 / D4 / D6–D10 + 作废核对（一类一提交），离线测试先红后绿：写码约 1.5 小时 + 测试 20 分钟 + 101 本回归约 30 分钟。
3. ERP：D1、D12（用途、选簿弹窗、主档簿清单）、载荷 22：写码约 1 小时 + 测试 20 分钟 + 流程 30 分钟。
4. D2 / D5 按用户结论：各约 30 分钟。
5. 真实环境逐字段比对 + 三星真机：约 1.5 小时（测试机两台、实验栈）。
6. 合入队列、发 CC 正式版（服务端先上）。
