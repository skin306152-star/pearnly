# 需求影响评估：CC 写 Express 时总结存（STMAS.TOTBAL / TOTVAL）与对方余额（APMAS / ARMAS.BALANCE）照原生

> 分支 `pearnly/cc-stock-balance-native`（基线 main 63770d0fd）。只评估，交主会话审定后再施工。
> 证据脚本与原始输出：测试机 `C:\PearnlyStress\_stockbal1107\`（bal / bal2 / bal3 / bal4 / rec），混行窗口的取证在共享笔记 `混行原生证据-11-07/`。

## 0. 需求原文与理解

- 来源：11-07 混行原生证据窗口在 TEST 上查出（[混行证据记录](2026-11-07-cc-mixed-lines-native-evidence.md)「另发现」），主会话派单。线上已有，不是混行引入。
- 理解：CC 桌面版直写 Express 时有两处和原生不一样，会把客户真实账套的数改错：
  1. **物料总结存**：进货、销货、库存单、退货写 STMAS 时把 `TOTBAL / TOTVAL` 设成「`MREMBAL / MREMVAL` ± 本单」。原生里 `TOTBAL / TOTVAL` 才是总结存，`MREMBAL / MREMVAL` 是先进先出批次链用的，两者经常不等。
  2. **对方余额**：CC 写赊购 RR 不加 `APMAS.BALANCE`、写赊销 IV 不加 `ARMAS.BALANCE`；原生两者都加，原生作废时再减回去。
- 不在本范围：先进先出批次链（`MLOTNUM / LOTBAL / LOTVAL`、STCRD 行上的 `MREMBAL` 口径）仍按既有做法交 Express「计算成本」重排（[出入库剩余设计](2026-10-25-stock-b-remaining-design.md) 第 7 节第 5 条）；进货总账写法（借购货 + 重分类）不动；不改服务端载荷。

## 1. 原生证据

### 1.1 TOTBAL 是总结存，MREMBAL 不是（只读统计，95 本真账、17 万件库存品）

账套取自测试机本机副本 `C:\PearnlyStress\S盘副本 / accserver副本`，与混行窗口同一批 95 本（去重后均匀抽样），只读文件。

| 现象 | 件数 | 本数 |
| --- | --- | --- |
| 库存品（STKTYP 0） | 170,240 | 95 |
| `TOTBAL ≠ MREMBAL` | 30,593（18%） | 90 |
| 其中各仓结存合计 `Σ STLOC.LOCBAL = TOTBAL` | **30,580（99.96%）** | |
| 其中 `Σ STLOC.LOCBAL = MREMBAL` | 3 | |
| 其中「期初 + 全部 STCRD 进出」= TOTBAL / = MREMBAL | 22,208 / 868 | |
| 有货的库存品里 `UNITPR = TOTVAL / TOTBAL`（Express 维护的均价） | 55,486 / 59,036（94%）；= MREMVAL / MREMBAL 只有 30 件 | |

`TOTBAL ≠ MREMBAL` 时按 MREMBAL 判断会差多少（同 95 本）：TOTBAL > 0 但 MREMBAL ≤ 0（CC 会当没货）4,714 件、74 本；TOTBAL ≤ 0 但 MREMBAL > 0（CC 会当有货）6,482 件、68 本；两者都 > 0 且均价差 1% 以上 5,927 件、71 本。

### 1.2 TEST 原生实录（服务器测试账套 `70EXP\test`，前后快照逐栏比）

| 单 | STMAS | 对方档 |
| --- | --- | --- |
| 原生赊购 RR0000015 / 16（11-07，500603 共 +3） | `TOTBAL` 42→45、`TOTVAL` +600；`MREMBAL` 42→**3**、`MREMVAL` 8400→600（批次链从这两张起算，`MLOTNUM` 指向最新一批）；另改 `LPURPR / LPURDAT / LPURQU / LPURFAC / UNITPR / LASUPD` | `APMAS.BALANCE` +1,605（两张合计）、`LASRCV` = 单据日 |
| 原生赊购 RR0000014（s12） | `TOTBAL / MREMBAL` 24→25、`UNITPR` 47.5567→48（= TOTVAL / TOTBAL） | `APMAS.BALANCE` +64.20 |
| 原生现购 HP0000006（4a） | `TOTBAL` 0→1 等 | **不动** APMAS |
| 原生赊销 IV0000027 / 31 / 39 | `TOTBAL / TOTVAL` 减、`LSELLPR / LSELLQU / LSELDAT` 记最后售价 | `ARMAS.BALANCE` + 单据合计 |
| 原生现销 HS0000040（g1） | `TOTBAL` 1→0 | **不动** ARMAS |
| 原生退货 SR0000001 / 2（退现金） | `TOTBAL` +1 | `ARMAS.BALANCE` − 退款额（CC 已照做） |
| 原生作废 IV0000031 / IV0000040 | `TOTBAL / MREMBAL` 加回 | `ARMAS.BALANCE` 减回 |
| 原生作废 CC 1.12.16 写的混行 RR691107-001 | `TOTBAL` 4→3、`TOTVAL` −200、`MLOTNUM` 清空；**MREMBAL 不动** | `APMAS.BALANCE` −374.50（CC 当初没加，被多减一次） |
| CC 写一张 RR691107-001（写前 TOTBAL 45、MREMBAL 3） | **`TOTBAL` 45→4**、`TOTVAL` 9000→800（错）；`MREMBAL` 3→4 | 不动 APMAS（错） |

TEST 里被改错的 500603 与 P001 已由混行窗口按作废前快照复原并读回。

### 1.3 对方余额的口径（只读统计，同 95 本，按 APBAL / ARBAL 月桶暴力试正负号）

- `ARMAS.BALANCE = ARBAL.BEGBAL + Σ IV − Σ RE − Σ SR`（± DR）：75,721 / 75,792 个客户（**99.9%**，88 本）。`HS` 桶不在式子里 → 现销不动余额。
- `APMAS.BALANCE = APBAL.BEGBAL + Σ RR − Σ PS − Σ GR`（+ CP）：22,773 / 26,779 个供应商（85%，95 本）；`HP` 桶不在式子里 → 现购不动余额。其余 15% 没找到简单式子（可能是旧年度结转），不影响结论。
- CC 现在写 RR / IV 时 APBAL / ARBAL 月桶是加了的（`_update_apbal` / `_update_arbal`），只是没加 BALANCE。所以 CC 写过的账套里，月桶对、余额少了 CC 写的那部分。

## 2. 代码现状：所有写 / 读这些栏的地方

### 2.1 写 STMAS 结存

| 位置 | 单 | 现在写法 | 问题 |
| --- | --- | --- | --- |
| `stock_purchase.write_stock_in_lines`（`_stmas_state` 读 MREMBAL） | 进货 RR / HP 库存行 | `MREMBAL / TOTBAL = MREMBAL + 数量`，`MREMVAL / TOTVAL = MREMVAL + 金额` | TOTBAL / TOTVAL 被盖成 MREMBAL 系 |
| `stock_sale.write_stock_lines` → `_reduce_stmas` | 销货 IV / HS、收银日汇总 HS | 四栏 = MREMBAL − 数量 / MREMVAL − 成本 | 同上 |
| `stock_sale.set_opening_stock` → `_set_stmas_opening` | 补期初（MREMBAL > 0 时跳过） | 四栏与 OPNBAL / OPNVAL 直接设成期初数 | 判断「已有货」看 MREMBAL；TOTBAL 有货、MREMBAL ≤ 0 时会把 TOTBAL 盖成期初数 |
| `dbf_stock` 写 OU / JU / TK | 库存单 | 四栏 = MREMBAL ± 数量 | 同上 |
| `dbf_refund` 写 SR | 收银退货 | 四栏 = MREMBAL + 数量 | 同上 |
| `dbf_void._restore_masters` | 作废 CC 写的库存单 | 四栏各自 ± 原单变动 | TOTBAL / TOTVAL 是增减，**对**；MREMBAL 也增减（与其他写法一致） |
| `dbf_transfer` | 调拨 RL | STMAS 不动 | 同原生，对 |
| `product_master` 新建档 | — | 四栏 0 | 对 |

STLOC 各仓结存（`stock_location`）一直是按增减记的，没受影响 → 这也是事后能算回 TOTBAL 的依据（见第 9 节）。

### 2.2 读 MREMBAL 当总结存

| 位置 | 用途 | 影响 |
| --- | --- | --- |
| `stock_lookup.find_rows / find_rows_by_code` → `stock_sale.find_stock_item` | 销货判「有没有货」（> 0 走移动均价，否则标准成本 / 不结转）、均价 = MREMVAL / MREMBAL | 第 1.1 节那 1.1 万件会走错分支，近 6 千件均价不同 → 销货成本（COGS 分录、STCRD 成本）错 |
| `dbf_stock._cost`、`dbf_refund`、`dbf_transfer` | 领用 / 调整 / 退货的成本与写前结存 | 同上 |
| `stock_guard.short_of`（账套不分仓核的那条） | 不许负库存时按全仓结存拦 | 有货拦成没货、没货放成有货 |
| `express_masters._product` 的 `balance` | 上报服务端的全仓结存（ERP 对齐「STMAS 结存 = 各仓合计」） | 18% 的品对不上，进差异清单（误报） |
| `product_master` 校验「新库存品 MREMBAL ≠ 0」 | 建档自检 | 不影响 |

### 2.3 对方余额

| 位置 | 现在 | 原生 |
| --- | --- | --- |
| `dbf_writer` 写 RR / HP | 只加 APBAL 月桶 | RR 另加 `APMAS.BALANCE`、记 `LASRCV`；HP 不动 |
| `dbf_sales` 写 IV / HS | 只加 ARBAL 月桶 | IV 另加 `ARMAS.BALANCE`；HS 不动 |
| `dbf_refund` 写 SR | ARBAL SR 桶 + `ARMAS.BALANCE` 减 | 同（已对） |

## 3. 按类通用

- 一类：「CC 直写 Express 的所有动库存的单」= 进货 RR / HP、销货 IV / HS、收银日汇总、补期初、库存单 OU / JU / TK、退货 SR、作废、调拨。全部走 2.1 / 2.2 的同一套读写，一起改：写只做增减，读一律用 TOTBAL / TOTVAL。
- 二类：「CC 直写的赊购 / 赊销」= RR、IV（含混行单、POS 以外的所有入口）一起加对方余额；HP / HS 照原生不动。
- 代录（在 Express 界面里录）不经这些代码，Express 自己维护，不受影响。

## 4. 上下游数据

- 服务端载荷不变，不升载荷版本；只改 CC 桌面版写法，ERP / Cowork 代码不动。
- `express_masters` 上报的 `balance` 改成 TOTBAL 后，ERP 的 Express 结存对齐差异清单会少掉误报（行为变化，要在 ERP 侧复核一次对齐页）。
- 成本：销货 / 领用的均价改用 TOTVAL / TOTBAL（= Express 自己维护的 UNITPR 口径，第 1.1 节 94%），总账 COGS 分录金额会随之变（变成与 Express 均价一致）。先进先出账套照旧由 Express「计算成本」重排。

## 5. 隔离与权限

不涉及：只改 CC 在本机已选定账套里的写法；租户、公司、账套路由不变。

## 6. 入口

不涉及新入口。受影响的入口：Cowork 推送（LINE / 网页 / 定时）、ERP 采购 / 销售 / 库存单 / 盘点 / 收银日汇总 / 退货 / 作废，经 CC 直写的全部；代录不受影响。

## 7. 共用件与连带回归

- CC 桌面版直写是共用件（Cowork 与 ERP 共用）；施工时贴 `python3 scripts/check-impact.py --files <改动文件>` 输出，按影响清单逐入口记结果。
- 离线回归：`scripts/express-stock-entry-regression.py`、`scripts/express-refund-regression.py`、`scripts/express-new-item-regression.py` 在测试机 `C:\PearnlyExpress\70EXP` 的 T 账套副本上跑新旧对比（新增断言：写后 `TOTBAL / TOTVAL` = 写前 ± 本单、`Σ STLOC.LOCBAL` 不变量、RR / IV 后 `BALANCE` = 写前 + 合计、HP / HS 不动）。
- TEST 实测：在 TEST 先把一件品做成 `TOTBAL ≠ MREMBAL`（原生录一张进货即可，如 11-07 的 500603），CC 新代码各写一张 RR、IV、HS、OU，读回逐栏比原生；再在 Express 原生作废其中的 RR、IV，确认 BALANCE 回到写前、TOTBAL 回到写前。

## 8. 存储与成本

不涉及：没有新增外部调用或存储。

## 9. 已写错的数据：要不要修、能不能修

**先说清楚影响**：改代码只管以后，不会把已写错的数改回来；而且以后改成增减后，旧的偏差会一直留着。

- **总结存**：CC 每写一张，那件品的 TOTBAL 就被重置成「MREMBAL ± 数量」。原来 TOTBAL = MREMBAL 的品（82%）没写错；不等的品被写错，Express 界面、库存报表、按全仓的负库存控制都会看到错的总结存；各仓结存（STLOC）没错，所以会出现「全仓 ≠ 各仓合计」。
- **对方余额**：CC 写的每张 RR / IV，供应商 / 客户余额少记一张；Express 里再付款 / 收款或原生作废时，余额会再被减，可能变负。影响应付 / 应收余额显示、客户信用额度控制（CRLINE）。月桶和总账没错。

**修复方案（写客户真实账套，必须用户授权；这里只给方案）**：

| 栏 | 正确值从哪来 | 可靠度 | 做法 |
| --- | --- | --- | --- |
| `TOTBAL` | `Σ STLOC.LOCBAL`（原生 99.96% 成立，CC 一直按增减记 STLOC） | 高（有 STLOC 的账套） | 只修 CC 写过（STCRD 有 `PEARNLY` 行）且 `TOTBAL ≠ Σ LOCBAL` 的品；没有 STLOC 的账套用「期初 + 全部进出」交叉核对，两者不一致就不动、列给会计 |
| `TOTVAL` | 没有独立来源（STLOC.LOCVAL 不维护） | 低 | 先在 TEST 实测 Express 自带「计算成本」能否把 TOTVAL / UNITPR 算回；能就请会计在 Express 里跑，不能就按「修后 TOTBAL × 修前 UNITPR」近似并列清单给会计确认 |
| `APMAS / ARMAS.BALANCE` | 月桶式子（AR 99.9%、AP 85%） | 中高 | 只修有 CC 写的 RR / IV 的对方；两种算法交叉核对：①月桶式子 − 现余额，②CC 写的 RR / IV 合计（含已被原生作废的）；一致才改，不一致列清单 |

做成 CC 的一次性「核对 → 修」任务：先只读出报告（每本账套几件品、几个对方、差多少），用户看过同意后，CC 在写前备份里改、读回、留备份；按公司逐本授权。读客户电脑上的账套也算碰客户数据，**连只读报告也要用户先同意**。

## 10. 文案与官方 UI

不涉及：本次只改 CC 写入逻辑，没有界面文案。修复工具如果做，再单独列文案。

## 11. 测试与验收

- 单元：在 `companion/tests` 加用例——夹具账套里一件品 `TOTBAL 45 / MREMBAL 3`，CC 写 RR +1 后断言 `TOTBAL 46 / TOTVAL +成本`（还原改动必失败，旧代码会得 4）；销货、库存单、退货、作废同样；RR 后 `APMAS.BALANCE +合计`、IV 后 `ARMAS.BALANCE +合计`、HP / HS 不动；销货在 `TOTBAL > 0、MREMBAL ≤ 0` 时按有货走均价、均价 = TOTVAL / TOTBAL；`stock_guard`、`express_masters` 用 TOTBAL。
- 离线回归 + TEST 实测：见第 7 节。
- 闸门：CC 全量 `pytest`、`architecture-and-offline`、测试机 Companion checks；合入前发主会话。
- 验收：主会话复核；用户只看结果（TEST 里 Express 界面上那件品的总结存、供应商余额对）。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 读的一侧（判有货、均价、全仓负库存核对、上报结存）是否同批改成 TOTBAL / TOTVAL | 同批改：同一个根因，只改写不改读，下一张销货仍按批次链判有货、算均价 | 只改写的话 COGS 继续按 MREMBAL 算，11 千件品判断错 |
| 2 | MREMBAL / MREMVAL 还要不要跟着增减 | 维持现在的增减（行为不变），批次链与成本方案一起另定 | 不动它更贴近原生（原生作废 CC 单不动 MREMBAL），但改变既有行为，要另做 TEST 实测 |
| 3 | 是否同批补原生顺带改的栏：`UNITPR`（= TOTVAL / TOTBAL）、`APMAS.LASRCV`、`LPURPR / LPURDAT / LPURQU / LPURFAC / LASUPD`、`LSELLPR / LSELLQU / LSELDAT` | 同批补 `UNITPR` 与 `LASRCV`（有实录、影响均价显示和最后进货日）；最后进 / 售价那几栏另立 | 不补：Express 均价显示不随 CC 的单更新 |
| 4 | 已写错的数据修不修、怎么修 | 先跑线上影响面统计；有影响的公司由主会话请用户决定是否做第 9 节的一次性核对报告 | 不修：旧偏差一直留在客户账里 |
| 5 | CC 新版本号与发布时间 | 主会话定 | — |

## 13. 线上影响面（只读，待主会话批准后统计）

- 写法来源：TOTBAL 跟着 MREMBAL 写的做法在 CC 迁进本仓（1.1.77，09-27）之前就有，即 **CC 一开始写库存就是这样**；库存单 OU / JU / TK（10-25，载荷 6）、退货 SR（10-26，载荷 10）沿用同一写法。RR / IV 不加 BALANCE 同样从一开始就是。
- 统计做法（已发主会话申请）：一次 SSH，只在 cowork / erp 两个 web 容器里用 `frappe.get_all` 读 `Cowork Push Record`、`Pearnly CC Job`（status = success），按站点 × 工作空间 × 账套统计：单据数（按方向与单别）、带库存行的单数与行数、涉及的不同存货码个数、赊购 RR / 赊销 IV 张数、首末日期、电脑台数；只打印计数。
- 结果：（待补）
- 需要读客户电脑账套才能知道的（**不读**，由主会话请用户决定）：每本账套里实际被改错的品数与差额、被少记的对方余额。

## 14. 工作量与排期

1. 写的一侧改成增减（5 处）+ 对方余额（RR / IV 两处）+ 单元测试：写代码约 1 小时，CC 全量测试约 10 分钟。
2. 读的一侧（第 12 节第 1 条，审定后）：约 30 分钟。
3. 离线回归（测试机 T 账套）约 20 分钟；TEST 实测约 30 分钟（要登记占用、测试机空闲时做）。
4. 闸门与 CI：约 30 分钟；合入前发主会话。
5. 修复工具（如用户同意）另立。
