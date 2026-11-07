# CC 混行写入：Express 原生证据（11-07）

接 [按行选物料评估](https://github.com/skin306152-star/pearnly/blob/pearnly/line-item-stock-choice-770877/docs/work/2026-11-06-erp-line-item-stock-choice-评估.md)
末尾「CC 桌面版混行写入施工记录」的 5 条「等原生证据」。分支 `pearnly/cc-mixed-lines`（变基到 `origin/main` 08f32a3b1）。

## 取证做法

- **只读统计**：测试机本机的账套副本 `C:\PearnlyStress\S盘副本`（年度文件夹 2564–2569）与 `C:\PearnlyStress\accserver副本`，
  共 1136 本（含按月复制的重复账套）。只读文件，没碰任何真实账套。脚本与输出在共享笔记 `混行原生证据-11-07/`。
- 「混行」= 同一张单的 STCRD 里既有 STKTYP 0（库存品）又有 STKTYP 1/3/4/5（非库存品）。
- 全量计数（含重复账套）：RR 混行 37,775 / 149.7 万张，HP 243 / 26.8 万，IV 185,335 / 380 万，HS 14,366 / 364.6 万。混行很常见，不是边角。
- 写法归纳按账套分层：去重后 284 本有混行，均匀取 95 本，每本每种最多 5 张（RR 363、HP 17、IV 183、HS 75 张，全部是人录的单）；
  X 栏 / XSALVAL 另在同 95 本里统计全部人录的单（`USERID` 不是 `PEARNLY`）。
- **原生实录**（11-07 14:13，测试机 64 分钟无人操作后）：TEST（`\\accserver\d$\ACCOUNT\70EXP\TEST`）里以 skin3 用代录引擎（后台发消息，CC 存的
  自动登录，用户 BIT9）从服务器版 Express 原生界面录两张混行 RR：RR0000015（库存 500603 ×2 + 费用 53-03-02-00）、RR0000016（费用 53-03-04-00 +
  库存 500603 + 费用 53-01-06-00），录前录后只读复制整本逐表比（`native_diff.json`）；同一份录前快照上在本机用 CC 改后代码写同样两张比对（`ccdiff2.json`）。
- **原生作废**：以 skin3 用 CC 1.12.16 代码（main 108f49aca 源码 + 安装版 `cdx_reindex_runner.exe`）往 TEST 真写一张混行 RR691107-001，
  再用代录引擎在 Express 的 RR 窗口翻到它、按 Express 自己的「作废」（`void_diff.json`）。

## 5 条结论

| # | 问题 | 原生（证据） | CC 本分支 | 处理 |
|---|---|---|---|---|
| 1a | STCRD 行号 / 物理顺序 | 行号照单据行 1..n（328 / 363；另 35 张是改过单留下的空号），物理顺序 = 行号（全部）；单头 NXTSEQ = 行数 | 照载荷顺序、整张单连续编号，物理顺序同；NXTSEQ = 行数 | 一致，不改 |
| 1b | POSOPR | 进货两种行都是 0，销货两种行都是 9 | 同 | 一致 |
| 1c | 进货非库存行栏位 | X 栏（XUNITPR / XTRNVAL）与 XSALVAL 都是税前额（RR 24.5 万行、HP 5.5 万行，95 本全是），无批次 / 结存 | 同 | 一致 |
| 1d | 进货库存行 XSALVAL | **0**（RR 15.9 万行里 93%、87 本） | 写成本行金额 | **改：写 0**（`stock_purchase.py`） |
| 1e | 销货非库存行栏位 | **X 栏是 0**，售价只在 XSALVAL（IV 10.1 万行、69 本；HS 2,651 行；混行与纯服务单一样） | 照进货写成单价 / 金额（以前只按 HP580101 取证） | **改：销货非库存行 X 栏写 0**（`dbf_detail.py`） |
| 1f | 库存行批次链 | 带 MLOTNUM / LOTBAL / LOTVAL（先进先出批次） | 不写（既有做法，由 Express「计算成本」重排） | 不改；与混行无关，全库存单也如此 |
| 2 | 进货总账 | 每行按商品主档科目组（ISACC）直接记：库存行借 ACCNUM01（存货，232 / 363）或该账套设成记 ACCNUM02（购货，104 / 363）；非库存行借自己的 ACCNUM01（负数行贷记）；进项税 SEQIT 3、应付 6；没有重分类分录（SEQIT 7 / 8 只在 18 张里出现） | 借购货（`items_account`）+ 同凭证追加「借存货 / 贷购货」重分类（SEQIT 7 / 8），非库存行借自己的科目 | **不改**：与 CC 全库存单的现行写法一致，科目余额与「直接借存货」的账套相同；改成直接记要连全库存单一起改，另立 |
| 2b | 销货成本分录 | 每张单一对（借成本 SEQIT 8 / 贷存货 9），几行库存品合成一对 | `merge_gl_rows` 合成一对 | 一致 |
| 3a | 混行赊购的服务品进项税口径 | 有税的混行 RR 全部记 `1`（เกณฑ์สิทธิ，218 / 218）；没税的记 `-` | 有非库存行 = 有服务品，按本账套历史 / 人选定（`service_vat`） | 不改；历史读的就是这些单 |
| 3b | 非库存行的品码与科目 | 人选的是现成非库存商品（多为 STKTYP 3 / 5），科目取它的科目组 ACCNUM01；账套常见「商品编码 = 科目码」（如 TEST 的 53-03-02-00） | 行带科目 → 直接科目行，引用「编码 = 科目码」的非库存商品（没有就建）；混行单问费用科目在 `pearnly/cowork-line-item` 的 `choices.py` | 不改 |
| 4 | 原生作废 CC 写的混行单 | 只弹一次「确认作废」，作废干净：单头金额归 0、DOCSTAT C，总账金额清空，ISVAT 归 0，两行 STCRD 软删（行号 `@@@`），STLOC 退回，没有任何报错 | CC 1.12.16 写的 RR691107-001 | 通过 |
| 5 | 代录配方能否录混行 | 两张都录成、读回一致（每张 10–12 秒）；费用行在前那张 Express 问了进项税口径，配方自动答 เกณฑ์สิทธิ，读回 SRV_VATTYP `1` | 配方按行录品码、数量、单价，不分库存 / 非库存 | 通过，不改 |

### TEST 原生实录与 CC 逐栏比（改后代码）

- 非库存行：全部栏位一致（POSOPR 0、X 栏与 XSALVAL = 税前额、TQUCOD 取主档单位、REFNUM、无批次）。
- 库存行：XSALVAL 0 一致（本次改动之后）；只差先进先出批次栏 MLOTNUM / LOTBAL / LOTVAL / LUNITPR 和 MREMBAL / MREMVAL（既有做法，见下）。
- 单头：只差 CMPLAPP（原生 BIT9 记 N，CC 记 Y）、ISVAT 的 SELF_ADDED / HAD_MODIFY（原生 N，CC 空），既有写法。
- 总账：原生每行直接借自己的科目（存货行 11-04-04-00、费用行各自科目）+ 进项税 + 应付，另留一条已软删的购货工作行（SEQIT 1）；CC 是借购货 + 重分类，余额相同。
- STLOC：一致。

### 另发现（不是混行引入，影响 CC 现有的库存写法，已报主会话）

- **STMAS 总结存**：CC 进货 / 销货 / 库存单 / 退货把 TOTBAL / TOTVAL 写成「MREMBAL / MREMVAL ± 本单」。原生里 TOTBAL 才是总结存，MREMBAL 随成本批次变
  （TEST 原生录那两张后 TOTBAL 42→45、MREMBAL 42→3；原生作废时只减 TOTBAL）。95 本真账里 17 万件库存品有 18% 两者不等（90 本都有）。
  TEST 实测：CC 写一张把 500603 的 TOTBAL 从 45 改成 4。
- **APMAS.BALANCE**：原生进货加对方档余额（并记 LASRCV），CC 只加 APBAL；原生作废 CC 写的单时会把 APMAS.BALANCE 多减一次。
- 两处 TEST 数据已按作废前快照逐栏复原（500603 的 TOTBAL / TOTVAL / MREMBAL / MREMVAL / MLOTNUM，P001 的 BALANCE）并读回。

## 改动

- `companion/src/companion/stock_purchase.py`：进货库存行 `XSALVAL = 0`。
- `companion/src/companion/dbf_detail.py`：销货（POSOPR 9）的非库存行改用 `stcrd_detail_row`（X 栏 0、XSALVAL = 税前额）；进货照旧 `stcrd_nonstock_row`。
- 这两处不只混行单：所有 CC 写的进货库存行、销货非库存行都按原生改。Express 读这两栏做毛利 / 成本报表，以前销货服务行在报表上成本 = 售价。
- 测试：`test_purchase_lines.py` 新增进货混行栏位用例；`test_stock_sale.py` 混行销货补栏位断言；`test_dbf_writer_sales.py` 非库存销货改成 X 栏 0、XSALVAL 9000。
  还原两处源码改动，这 3 个用例都失败（本机核过）。

## 验证

- 本机 Mac：CC 全量 `pytest tests` 1521 通过、42 跳过（依赖按 `packaging/requirements-build.txt` 锁定版本装在临时虚拟环境）。
- GitHub：architecture-and-offline、测试机 companion-tests（Windows 全量）都通过；生产镜像 1560bb95 候选离线 LANE EXIT 0；11-07 合入 main a9348a5d6。
- 测试机：原生实录、原生作废、CC 1.12.16 代码真写 TEST 一张（上文）。
- 没做：CC 1.12.16 staging 安装版经服务端载荷 19 的实测（要换装测试机 CC、lab-start，等主会话安排）。
