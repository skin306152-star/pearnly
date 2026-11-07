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
- 统计做法（主会话批准）：一次 SSH，只在 cowork / erp 两个 web 容器里用 `frappe.get_all` 读 `Cowork Push Record`、`Pearnly CC Job`（status = success），按站点 × 工作空间 × 账套统计：单据数（按方向与单别）、带库存行的单数与行数、涉及的不同存货码个数、赊购 RR / 赊销 IV 张数、首末日期、电脑台数；只打印计数。
  第一次（sites_path 写错）8 个站点都在 `frappe.init` 报 FileNotFoundError、没读到数据；改成在 sites 目录下跑后第二次跑通。两次都删了 `/tmp` 下的脚本并回读确认。
- 结果（11-07，生产只读）：

  | 站点 | 工作空间 / 账套（账套键只取前 16 位） | CC 成功写的单 | 带库存行的 | 物料数 | 赊购 RR / 赊销 IV | 日期 |
  | --- | --- | --- | --- | --- | --- | --- |
  | **erpcowork**（正式 Cowork） | nb2t7uhhen / `s:\2569\exp69\69…` | IV 1 | IV 1（1 行） | 1 | IV 1 | 10-15 |
  | ccdemo | dn0tkh1dot / `\\pc-afa21e\c$\a…` | HP 5、RR 4 | 0 | 0 | RR 4 | 10-20 |
  | ccdemo | dn0tkh1dot / `\\accserver\d$\a…` | RR 5、HP 13、IV 8、HS 8 | 0 | 0 | RR 5、IV 8 | 10-20 ~ 10-21 |
  | testcowork（测试） | 3 本（C 盘 70EXP、C:\PearnlyExpress、TEST） | HP 72、RR 3、IV 7、HS 66 | 31 张、165 行 | 64 | RR 3、IV 7 | 10-13 ~ 11-05 |
  | testerp（测试） | 3 本 | HP 7、HS 2、JU 2 | 8 张、8 行 | 5 | 0 | 10-23 ~ 11-03 |
  | mrerp、gize、polymer、test01（正式 ERP） | — | **0** | 0 | 0 | 0 | — |

- 结论：**正式租户里只有 erpcowork 的一本账套**（`S:\2569\EXP69\69…`，S 盘办公室服务器上的真实账套）被 CC 写过 1 张带库存行的赊销 IV（10-15）：最多 1 件物料的总结存可能被改错（写前 TOTBAL = MREMBAL 就没错）、1 个客户余额少记这一张。
  ccdemo（演示站点；其中一本在 `\\pc-afa21e` 这台电脑的 C 盘）有 9 张赊购、8 张赊销没加对方余额，没有库存行。正式 ERP 站点 CC 一张都没写过。测试租户不算影响。
- 补查（主会话批准，一次 SSH，只读 erpcowork 站点的服务端记录）：那张单是 `IV69/09-001`，10-15 14:22 由站点唯一账号 `mrerp@outlook.co.th`
  推送，账套 `S:\2569\EXP69\69SINCER`（服务端登记名「69SINCER SINCERE ICE 2569」，位置 `\\192.168.0.212\pas212\2569\exp69\69sincer`），
  推送记录没记写单电脑（当时的版本不报）；这个工作空间登记的电脑是 KORN（1.12.0）和 mrerp07（1.3.5），都属这个账号。
  最近 30 天：登录 10-14、15、22、23 共 10 次（账号最后登录 10-23、最后活动 10-30）；推送只有这一张成功（另有 9-30 的 13 张转人工）；识别 1 次（10-15）。
- **只读核对 SINCERE ICE 账套**（11-07，用户同意「可以」，只读）：测试机以 skin3 一次性任务把 `\\192.168.0.212\pas212\2569\exp69\69sincer`
  的 STMAS / STLOC / ARMAS / ARBAL / ARTRN / STCRD / ISPRD / ISINFO 只读复制到 `C:\PearnlyStress` 临时目录读副本，读完删掉；没写原账套、没开 Express。
  这本账套还在用（ARTRN 最后改动 11-05）。

  | | 现在 | 按原生应是 | 差 | 怎么算的 |
  | --- | --- | --- | --- | --- |
  | 物料「น้ำแข็งหลอดเล็ก」总结存 `TOTBAL` | −215,104（= 批次链 MREMBAL） | −265,287 | 少记约 50,183（账面负得少了） | 各仓合计 STLOC 01 = −265,287；期初 −204,459 + 882 行销货流水 −60,832 = −265,291，两者只差 4 |
  | 同一物料货值 `TOTVAL` | 0 | 0 | 0 | 这件品从没有成本（全是零成本销货） |
  | 客户 พ003 余额 `ARMAS.BALANCE` | 0.00 | 58.85 | 少记 58.85 | 月桶 期初 0 + 赊销 4,531.45 − 收款 4,472.60 = 58.85；未清单据 IV69/09-001（9-06、BIT5 录）58.85 |

  **CC 写的那张已不在这本账套里**：ARTRN 4,003 行的录入人只有 BIT5 / BIT9，没有一行是 PEARNLY（10-15 的 CC 1.12.0 写单一律记 PEARNLY）；
  同号 IV69/09-001 只剩人工录的那张（CC 写销货用票面号，账套里已有同号也照写，所以当时是第二张同号单）。
  **推断**（不是直接证据）：两处差额都和「CC 10-15 写了第二张 IV69/09-001，之后有人在 Express 里把它删掉、并整理过数据（PACK，删除标记行不在了）」吻合——
  ① 余额：人工那张原生加了 58.85，CC 那张没加，删 CC 那张时 Express 减了 58.85 → 现在 0、应是 58.85；
  ② 总结存：CC 写时把 TOTBAL 盖成「MREMBAL − 1」（写前两者差约 5 万，原生 99.96% 的品 TOTBAL = 各仓合计），删单时 Express 两栏各加回 1 → 现在 TOTBAL = MREMBAL、比各仓合计少记约 5 万。
  也可能这两处原本就偏（账套里看不到写前的值），所以修之前建议请会计在 Express 里看这件品的库存卡和这个客户的对账单确认。
- 修的话（要用户另授权写这本真实账套）：`TOTBAL` 改成 −265,287（= 各仓合计），`ARMAS.BALANCE` 改成 58.85；只动这一件、这一个客户两栏，写前备份、写后读回。
- 需要读客户电脑账套才能知道的（**不读**，由主会话请用户决定）：erpcowork 那本账套的完整路径（账套键只读了前 16 位，要再查一次推送记录或看工作空间设置）、那件物料写前 TOTBAL 是否等于 MREMBAL、客户余额现在差多少；ccdemo 两本账套是否算客户数据、要不要补余额。
- 需要读客户电脑账套才能知道的（**不读**，由主会话请用户决定）：每本账套里实际被改错的品数与差额、被少记的对方余额。

## 14. 工作量与排期

1. 写的一侧改成增减（5 处）+ 对方余额（RR / IV 两处）+ 单元测试：写代码约 1 小时，CC 全量测试约 10 分钟。
2. 读的一侧（第 12 节第 1 条，审定后）：约 30 分钟。
3. 离线回归（测试机 T 账套）约 20 分钟；TEST 实测约 30 分钟（要登记占用、测试机空闲时做）。
4. 闸门与 CI：约 30 分钟；合入前发主会话。
5. 修复工具（如用户同意）另立。

## 15. 主会话审定（11-07）

第 12 节：① 读的一侧同批改成 TOTBAL / TOTVAL；② MREMBAL / MREMVAL 维持现状（继续增减），批次链另议、记待办；③ 同批补 `UNITPR` 与 `APMAS.LASRCV`，
最后进价 / 售价那几栏另立待办；④ 已写错的数据先不动，影响面统计出来后写一页「受影响客户 × 账套 × 大约件数 + 修复方案 + 风险 + 要用户做什么」交主会话问用户，
读客户电脑上的账套也在这次要问的范围里，问之前不读；⑤ 并进 1.12.16（混行那批已在 main、未发正式版），服务端先部署、CC 再发，时间主会话定。

## 16. 施工记录（分支 `pearnly/cc-stock-balance-native`）

### 以后写对（改 CC）

- `stock_sale.move_stmas`：所有动库存的写法共用一处按增减改主档——`TOTBAL / TOTVAL` 在**现有值**上加本单（入正出负），总结存为正时
  `UNITPR = TOTVAL / TOTBAL`（4 位小数），不为正时不动（同原生 000004 −2 → −3）；`MREMBAL / MREMVAL` 照旧增减（补期初照旧设成期初数）；
  可带同时要写的栏（盘点的 `LASUPD / MLOTNUM`、期初的 `OPNBAL / OPNVAL`）。取代原来的 `write_stmas_fields` / `_reduce_stmas`（删掉）。
  调用方：进货 `stock_purchase`、销货 `stock_sale`（含收银日汇总）、补期初、库存单 `dbf_stock`、退货 `dbf_refund`、作废 `dbf_void`。
- 读：`stock_lookup.balances` 同时给总结存 `totbal / totval` 与批次链 `mrembal / mremval`。判有没有货、均价（销货、领用 / 减少调整、退货兜底成本）、
  补期初的「已有货跳过」、`stock_guard` 按全仓核负库存、`express_masters` 上报的 `balance` 一律用总结存；STCRD 行上的结存栏（批次链口径）不变。
  盘点 TK 在没有 STLOC 的账套里 `PHYBAL` 记写后总结存。
- 对方余额：`master_create.move_party_balance`——赊购 RR 加 `APMAS.BALANCE`、`LASRCV` 只往后记（S 盘 75% 的供应商 LASRCV = 最晚一张赊购的日期）；
  赊销 IV 加 `ARMAS.BALANCE`（原生 IV 不动 `LASIVC`：TEST 1b 客户第一张单也没记）；现购 HP、现销 HS 不动；退货 SR 改用同一个函数减余额（行为不变）。
- 索引：Express 的 APMAS / ARMAS / STMAS 索引只用代码、名称、类型（TEST 与 S 盘 20 本 CDX 里没有 BALANCE / TOTBAL / UNITPR），改这几栏不用额外重建索引。
- 合成测试表规格补上真表就有的 `APMAS.BALANCE / LASRCV`、`STMAS.UNITPR`（只给测试建表用）。

### 测试

- 新增 `companion/tests/test_stock_balance_native.py`（12 条）+ `test_dbf_refund.py` 1 条：夹具照 TEST 500603 做成 TOTBAL 45 / MREMBAL 3，
  进货、销货、领用 + 作废、调整、退货、补期初、全仓负库存核对、赊购 / 现购 / 赊销 / 现销的对方余额、LASRCV 不往回改、结存不为正时均价不动。
  **还原修复**（业务代码换回 main，测试表规格保留）：12 条里 11 条失败、退货那条失败；剩下的「旧日期赊购不把 LASRCV 往回改」旧代码本来就不碰这一栏。
- 两条旧断言按新口径改：`test_express_masters`（上报结存取 TOTBAL）、`test_stock_lookup`（同名多行挑总结存为正的）。
- CC 全量 `pytest`：1534 通过、42 跳过（本机 Python 3.11，依赖按 `packaging/requirements-build.txt` 锁定版本）。
- `scripts/express-stloc-regression.py` 的「STMAS = 各仓合计」改按 TOTBAL 核（原来核 MREMBAL）。
- 新增 `scripts/express-stock-balance-regression.py`：新旧两份 CC 源码在同一批账套副本上写同样 5 张单（RR +5、HP +1、IV −2、HS −1、OU −1），逐表逐行逐字段比，
  只许余额 / 均价栏和（均价不同时）成本栏不同，并核新写法的不变量。结果见下。

## 17. 一页：已经写错的数据（交主会话问用户）

**以后写对**靠这次改 CC（第 16 节），并进 1.12.16 发布后，新写的单就对了。**已经写错的**不会自己好，下面是现状和选项。

| 受影响 | 账套 | 大约多少 | 错在哪 |
| --- | --- | --- | --- |
| erpcowork 正式工作空间 nb2t7uhhen | `S:\2569\EXP69\69…`（S 盘办公室服务器，完整账套名要再查一次） | 1 张赊销 IV（10-15）、1 件物料、1 个客户 | 物料总结存可能被改成「批次链 − 1」（写前两者相等就没错）；客户余额少记这张单的含税额 |
| ccdemo 演示站点 | `\\pc-afa21e\c$\…`、`\\accserver\d$\…` 两本 | 赊购 9 张、赊销 8 张；没有库存行 | 9 个供应商 / 8 个客户（次数，人数可能更少）的余额各少记一张 |
| 正式 ERP（mrerp / gize / polymer / test01） | — | 0 | — |

**修复办法**（都只改上面这几本里 CC 写过的对象；先只读出报告给人看，同意后再改；在写前备份里改、改完读回）：

1. 总结存 `TOTBAL`：改成各仓结存合计（Express 原生 99.96% 如此，CC 一直按增减记各仓结存，所以各仓那边是对的）。没有各仓表的账套，用「期初 + 全部进出」交叉核，对不上就不动、列给会计。
2. 货值 `TOTVAL`：没有独立来源。先在 TEST 试 Express 自带的「计算成本（คำนวณต้นทุน）」能不能把它和均价算回；能就请会计在 Express 里跑这一件，不能就列清单请会计核。
3. 对方余额 `BALANCE`：按 Express 的月桶式子（期初 + 赊购 / 赊销 − 付款 / 收款 − 退货）重算，与「CC 写的赊购 / 赊销合计」交叉核，一致才改。

**风险**：改的是客户真实账套；改错会让 Express 报表、信用额度、库存控制看到错数。所以只改这几本里 CC 写过的那几件 / 那几个对方，每一处先出报告、留备份；不一致的不碰。erpcowork 那本只有 1 件物料、1 个客户，人工在 Express 里核对改正也行，比做工具省。

**要用户定的**：
1. erpcowork 那本账套（S 盘）要不要修？建议：先只读核对那 1 件物料、1 个客户（要读客户账套，需用户同意），差了再由会计在 Express 里改或由我们按上面办法改。
2. ccdemo 两本算不算客户数据、要不要补余额？建议：演示用就不修，以后演示前用 Express 自带功能或重建演示账套。
3. 以上都要读客户电脑 / 服务器上的账套；用户同意前不读。

### TEST 实测（11-07，`\\accserver\d$\ACCOUNT\70EXP\TEST`，skin3，本分支 CC 源码 + 安装版 cdx_reindex_runner）

写前（只读快照）：500603 总结存 46 / 货值 9,200、批次链 4 / 800（TEST 里两者本就不等）、各仓合计 4；P001 余额 2,840.85；客户 5001 余额 2,682.00。

| 步骤 | 500603 TOTBAL / TOTVAL / UNITPR | P001 余额 | 5001 余额 | 说明 |
| --- | --- | --- | --- | --- |
| CC 写赊购 RR691107-003（+1 @200，含税 214） | 47 / 9,400 / 200 | 3,054.85（+214） | 2,682.00 | 旧写法会把 TOTBAL 写成 5 |
| CC 写赊销 BAL1107B（−1 @300，含税 321） | 46 / 9,200 / 200 | 3,054.85 | 3,003.00（+321） | 成本按均价 200 结转（XUNITPR 200） |
| Express 原生作废 RR691107-003 | 45 / 9,200 / 204.4444 | **2,840.85（= 写前）** | 3,003.00 | 单头 C；旧写法这里会被多减 214。TOTBAL 45 = 写前 46 − 赊销 1。作废时 Express 自己重排了这件品的批次链（MREMBAL −1、MREMVAL 0、MLOTNUM `-/F`）并重算 TOTVAL / UNITPR，是原生行为 |

- 作废时 Express 先弹「确认作废」，确定后又弹一个只有「确定」的提示 Message#43（大意：ISRUN(BA) 里找不到单号那一行，请看「日记账」设置），按确定后作废照常完成；混行窗口那次作废没出现，像是 TEST 的单据簿设置，与本次改动无关，记着。
- 赊销 BAL1107B 没能原生作废：单号是票面号，代录引擎在 IV 窗口里按上一张 / 下一张翻不到它（翻到 IV69/00473 就停）。它仍是一张有效单据，所以 TEST 没有按写前快照复原主档（复原了反而和这张单对不上）；现在 500603 / 5001 的值与「这张单有效」一致。要清掉的话请人在 Express 里打开这张单点作废（作废后应回到 TOTBAL 46、5001 余额 2,682.00）。
- Express 已正常退出，计划任务已删，CC 没碰。
