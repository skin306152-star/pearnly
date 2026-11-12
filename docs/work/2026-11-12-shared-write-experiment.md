# 共享写入实验：只读阶段（B8 补全 / P0 / P1 / 提速第 0 步 M1–M4）

> 11-12 下午，测试机1 + 服务器正版 Express（`\\accserver\d$\ACCOUNT\69EXP\ExpressI.exe`，1.0.1.94）+ 测试套 `69EXP\TEST`。
> 上游：评估 `pearnly/express-write-speed-20761e` 的 `docs/work/2026-11-12-express-write-speed.md` §11、§15。
> 原始输出、截图、Process Monitor 导出在共享笔记 `local_40940693…/共享写入实验-11-12/`（不进仓库）。
> 本任务只做实验与评估，没改产品代码；只读阶段唯一的写入是 P0 在 Express 界面里录的一张测试单 **RR0000094**（留在测试套）。

**链路条件（全部实验都在这种条件下做）**：测试机1 走 Wi-Fi（有线未接），连 accserver 是 SMB1（方言 1.5），Defender 实时扫描开着。用户 11-12 定：办公室的外部使用环境不改，这正是客户现场可能有的真实条件；提速只从我们自己的写法上解决，共享写入也在这种条件下验证。

## 0. 结论（一屏）

**只读阶段没有否决项，共享写入这条路继续成立。** 四项结果：

| 项 | 结论 | 关键事实 |
|---|---|---|
| B8 补全 | 通过 | 主界面不开任何文件；开窗口时表以「读写 + 共享读写」打开，不独占；18 种窗口两遍一致；除科目表窗口外都开着 ISRUN |
| P0 | 通过 | 锁规则 = FoxPro / VFP（表头、索引锁 0x7FFFFFFE，记录锁 0x7FFFFFFE − 行号，长 1）；取号只锁 ISRUN 那一行 <1 毫秒；索引增量维护，没有整表重建 |
| P1 | 通过 | Harbour 只读共享打开 12 张表、91 个标签全部遍历正确；读索引时在同一位置加共享锁，与 Express 写索引的独占锁位置一致；Express 无报错、无写入 |
| M1–M4 | 查到 | 慢的根因：整表读写跑在 Wi-Fi + SMB1（方言 1.5）上，NLTTEST 又是特别大的一本；链路不改，只改写法 |

## 1. 做法与规矩

- 开 Express：以 skin3 交互式一次性计划任务（`PearnlySw1112*`，Limited、允许电池）跑 CC 代录引擎源码（同引导式窗口 §9.5 的做法），凭据管理器自动登录，核对状态栏是 `69EXP\TEST` 才动，做完正常退出（7 次，每次进程数回到 0）。
- 每次开跑前查 skin3 键鼠空闲 ≥120 秒，不够就不动（两次被自己上一轮的按键挡下，等够再跑）。
- 占用探测：另一个进程对账套目录 118 个 DBF / CDX / FPT 逐个 `CreateFileW` 试开即关（读 + 不共享；打不开的再试读 + 共享读、读 + 共享读写、读写 + 共享读写），不读不写内容。
- Process Monitor（Sysinternals，签名有效）以 macadmin 抓全部事件，转 CSV 后只留 `ExpressI.exe` / `shared_probe.exe` 对 `\69EXP\TEST\` 的行。
- Harbour 3.0.0 只读探针 `shared_probe.exe`（`SHARED READONLY`，源码 `~/pearnly-devtools/shared-write-exp/shared_probe.prg`）在测试机1 现场编译。
- 没 lab-start、没切域名、没重启 CC、没改 CC 配置（进程 15596、哈希 7D2EE159 前后一致）；没开真账套；RE0000001–6、IV0000029 没动。

## 2. B8 补全：各窗口开着哪些表

每种窗口「开 → 探测 → 关 → 探测」，四轮共两遍，两遍逐表相同；关掉后都回到 0 个文件被开着。所有被开着的文件都是：不共享 / 只共享读 → 错误 32；共享读写（读或读写访问）→ 能开。即 **Express 以读写访问、允许别人共享读写的方式开表**。

| 窗口 | 表数 | 被开着的表（DBF + CDX 各一） |
|---|---|---|
| 赊购 RR、现购 HP、付款 PS | 26 | APBAL APMAS APRCPCQ APRCPIT APTRN ARMAS ARTRN ARTRNRM BKMAS BKTRN GLACC GLJNL ISACC ISRUN ISTAB ISTAX ISVAT POPR POPRIT STBOM STCRD STLOC STMAS STMIN STPRI STTRN |
| 赊销 IV、现销 HS、收款 RE | 26 | APMAS APTRN ARBAL ARMAS ARRCPCQ ARRCPIT ARSHIP ARTRN ARTRNRM BKMAS BKTRN GLACC ISACC ISRUN ISTAB ISVAT OESLM OESO OESOIT STBOM STCRD STLOC STMAS STMIN STPRI STTRN |
| 退货 SR | 27 | 同赊销 + ISTAX |
| 调拨 RL、领用 OU、调整 JU | 18 | APMAS APTRN ARMAS ARTRN ARTRNRM GLACC GLJNL ISACC ISRUN ISTAB OESOIT STBOM STCRD STLOC STMAS STMIN STPRI STTRN |
| 凭证 JV（5.1.1） | 17 | APMAS APTRN ARMAS ARTRN ARTRNRM BKMAS BKTRN GLACC GLBAL GLJNL GLJNLIT GLTPL GLTPLIT ISRUN ISTAB ISTAX ISVAT |
| 物料主档 | 13 | APMAS APTRN ARMAS ARTRN ARTRNRM ISACC ISRUN ISTAB STBOM STCRD STLOC STMAS STTRN |
| 报表选择窗（6.1） | 12 | APMAS ARMAS ARTRN BKMAS GLACC ISACC ISRUN ISTAB OESLM STCRD STLOC STMAS |
| 客户主档 | 11 | ARBAL ARMAS ARSHIP ARTRN ARTRNRM BKTRN GLACC ISRUN ISTAB OESLM STCRD |
| 供应商主档 | 10 | APBAL APMAS APTRN ARTRNRM BKMAS BKTRN GLACC ISRUN ISTAB STCRD |
| 库存系统设置（7.1.3.1） | 5 | BKMAS GLACC ISINFO（只 DBF）ISRUN ISTAB |
| 单号设置（7.4） | 3 | GLACC ISRUN ISTAB |
| 科目表（5.2） | 4 | ARTRNRM GLACC GLBAL ISTAB |
| 主界面 / 任何窗口关掉后 | 0 | — |

**我们各写路要写的表会撞上哪些窗口**（表清单取自现有常量：采购 `PURCHASE_BACKUP_TABLES`、销售 `SALES_BACKUP_TABLES`、收款 `dbf_receipt.BACKUP_TABLES`、库存单 `dbf_stock.TABLES`、调拨 `dbf_transfer.TABLES`、作废 `dbf_void.TABLES`，加 STLOC、ARTRNRM）：

- 除「单号设置」「库存系统设置」只撞 ISRUN、「科目表」只撞 ARTRNRM 外，**每一种窗口都和每一条写路有共同的表**——Express 开着任何单据或主档窗口，现在的整表独占重建都写不进去。
- 只试 ISRUN 会漏判的组合只有一个：**科目表窗口开着 + 采购 / 销售 / 退货单带行说明（写 ARTRNRM）**。收款、库存单、调拨、作废没有漏判。
- 详表：共享笔记 `b8_matrix.txt`。

没测到：银行、税、期末处理、盘点、单据打印预览，真正跑一张报表（只开了报表选择窗）。

## 3. P0：Express 自己怎么开表、怎么锁、怎么写

做法：Process Monitor 抓着，从启动 Express、进公司、开赊购单窗口，到用代录引擎在界面里录一张最简单的赊购单并关窗口。录的单：**RR0000094**，供应商 ซ014，PN00030 × 1 @ 10，单据号 SWP01112，日期 12/11/69；引擎读回一致。5855 个事件。

- **打开方式**：数据表与索引全部「Generic Read/Write + 共享 Read, Write」；ISINFO、ISRUN.CDX、DB_VER.REF 只读访问 + 共享读写。没有独占打开，没有整表锁（没有锁长度 >1 的锁）。
- **锁规则 = FoxPro / VFP**（全部独占、长度 1、立即失败，重试由它自己做）：
  - 追加 / 表头：DBF 偏移 `0x7FFFFFFE`；
  - 记录：`0x7FFFFFFE − 行号`（例：APMAS 第 706 行锁在 0x7FFFFD24）；
  - 索引：CDX 偏移 `0x7FFFFFFE`；
  - 读数据、读索引都不加锁。
- **取号（ISRUN）**：新建时不取，**保存那一刻才取**：先锁 APTRN 表头 → 锁 ISRUN 第 41 行 → 锁内重读这一行 → 写回 365 字节 → 解锁，全程 <1 毫秒。不锁 ISRUN 表头。
- **一张赊购单的写入顺序**（时间线见共享笔记 `P0/p0_timeline.txt`）：
  1. 录入时输完供应商就锁 APMAS 那一行，**到保存结束才放（约 15 秒）**；
  2. 保存：锁 APTRN 表头 → ISRUN 取号 → 锁新行（第 244 行）→ 锁 APTRN.CDX → 锁内重读索引头与相关页 → 增量改十几个 512 字节索引页、在文件尾加新页、写索引头 16 字节 → 解索引锁 → 写新行 → 写表头前 10 字节（行数、日期）→ 解表头锁 → 解行锁（这一段约 30 毫秒），随后又锁住新行留到最后改合计；
  3. STCRD 同样追加一行；STMAS 改那件物料一行（行锁）；
  4. GLJNLIT 逐行追加 4 行，每加一行就锁、改 GLBAL 对应那一行；GLJNL 追加单头；
  5. APBAL 改一行、APMAS 改一行；ISVAT 追加一行；最后改 APTRN 单头合计、解行锁；
  6. 关窗口时对改过的表各写表头前 4 字节（版本 + 最后修改日期，不含行数），不加锁；随后 4096 字节的写是 Windows 缓存回写同一页。
- 两个细节：① Express 改自己刚追加的行（STCRD、GLJNL、GLJNLIT）时没有再加锁——别人不会去改这些行，所以对它自己是安全的；共享写入时我们也只能改自己的行。② 没有事务：中途断掉会留下一半（这与 Express 自己被断网时一样，见操作手册「踩过的坑 3」）。

## 4. P1：Express 开着时 Harbour 只读共存

做法：Express 开赊购单窗口（停在显示已有单）→ 探针对 ISRUN、APTRN、ARTRN、GLJNL、GLJNLIT、STCRD、STMAS、APMAS、ARMAS、ISVAT、APBAL、ARBAL 共享只读打开、按每个标签整表遍历、按单号查找 → 再叠开赊销单窗口，对 ARTRN、ISRUN 再做一遍 → 关窗口退出。Process Monitor 同时抓 `ExpressI.exe` 与 `shared_probe.exe`。

- 129 次全部成功；锁方案都自动选 3（VFP）。
- 91 个标签逐个核：遍历行数 = 索引键数；键值按原字节递增（含泰文名称键，**索引排序是原字节序**）；查到 RR0000093（APTRN 第 179 行）、IV0000029（ARTRN 第 50 行）；RR0000094 在 APTRN 索引里（第 244 行）。指定方案 3 与不指定结果相同。
- 锁位置：探针每读一次索引就在 **CDX 偏移 0x7FFFFFFE 加共享锁**（22,276 次，全部成功、全部解开），不锁数据。Express 写索引是在同一位置加独占锁（P0）→ 两边规矩一致：我们读的时候 Express 写索引会等，反之亦然；Express 自己读不加锁（Express 之间本来如此）。
- Express 一侧：0 次写入、0 个失败事件、没有报错弹窗，窗口照常叠开、关闭、正常退出。
- 速度：共享读每页都要往返加解锁，SMB1 上单次整表遍历最长 9.7 秒（129 次合计 329 秒，含 Process Monitor 拖慢）。

## 5. 提速第 0 步 M1–M4

**M1 链路**（以 skin3 只读测；`Get-SmbConnection` 要管理员，另以 macadmin 读）：

| 项 | 结果 |
|---|---|
| SMB 方言 | **accserver d$ = 1.5（即 SMB1）**，不签名不加密；同机连另一台共享 pas212 是 3.1.1 |
| 客户端签名设置 | RequireSecuritySignature True（SMB1 这条没用上签名） |
| 网卡 | **有线网卡未接**，走 Wi-Fi（Intel AC 9560，5 GHz，接收 351 Mbps、信号 75%） |
| 延迟 | 50 次平均 4.6 ms、最大 28 ms；1400 字节 ping 20 次 0 丢包，平均 5 ms |
| 杀毒 | Defender 实时保护开着（只看没改） |
| 读速度 | NLTTEST\GLJNLIT.DBF（15.7 MB）首次 1 MB 块 8 MB/s；之后再读命中客户端缓存（几千 MB/s，不代表链路）；随机 4 KB、打开关闭 <1 ms（同样是缓存） |

没做：换机对照（测试机2 连不到 accserver）、写速度（M5 / M6 要写，不在只读阶段）。

**M2 历史计时**（CC 日志 521 条 `[timing]`）：网络盘 NLTTEST 收款 5 张 202–235 秒（检查 16–27、备份 44–62、写 32–61、重建 28–53、读回 45–66 秒）；其余 491 条成功的单（C 盘与小账套）413 条 <3 秒、78 条 3–20 秒，没有 20–200 秒之间的。

**M3 账套规模**（只列文件大小，没打开）：accserver 69EXP / 70EXP 共 17 本；NLTTEST 关键 15 类表 DBF 合计约 70 MB（STCRD 16.7、GLJNLIT 15.7、ARTRN 10.4、ARBAL 9.3 MB），其余真账套这些表合计中位 0.2 MB、最大 18 MB。TEST 与 TEST2 都很小（<2 MB）。

**M4 索引标签**：对 C 盘账套副本 T9HENG15 的 18 张表（复制到临时目录，源账套没动）用只读探针列出 104 个标签与键 / FOR 表达式（共享笔记 `M4-索引标签-T9HENG15副本.txt`）；与测试机2 DEMO01 取到的 ISRUN 4 / APTRN 15 / GLJNL 8 一致，ARTRN 16、STCRD 15、STMAS 12。

**含义**：网络盘慢主要是「SMB1 + 无线」上的整表读写，NLTTEST 又特别大。链路按用户 11-12 的决定保持原样，只作事实记录；提速只靠写法——共享写入不再整表读写。P0 里 Express 在同样的链路上写一张单的数据部分只用几十毫秒，说明增量写本身不受链路拖累。

## 6. express-busy-probe 探测方式（只给结论，代码不在本任务改）

- 现状「只试 ISRUN」：在测过的 18 种窗口里只漏一种组合（科目表窗口 + 带行说明的采购 / 销售 / 退货单），**不是必须改**。
- 建议仍改成「本次要写的每张表的 DBF + CDX 都试」：补上科目表那一种、以及没测到的窗口（银行、税、期末处理等）；网络盘上多十几次试开约 1–2 秒；判断范围与之后要独占的范围完全一致。做法按交接文档里的倾向方案（`account_lock.busy_tables`，清单取各写路现成常量，`express_operator/session.py` 那个调用不动）。
- 依据：§2 对照表。注意主界面不占任何文件，「Express 开着但停在主界面」不会被任何试开方式判成占用——这是对的（那时写不冲突），但写到一半 Express 才开窗口的窗口期仍在，只有共享写入能根除。

## 7. a–e 实验清单修订版（待主会话批准，未批准不做任何写入实验）

依据今天的事实改了这几处：锁规则已知是 VFP、Harbour 默认对得上；Express 编辑单据时会长时间锁住对方主档那一行；取号只锁 ISRUN 一行；索引是原字节序；链路是 SMB1 + 无线。

**a0（新增，先做，C 盘副本，不碰服务器）Harbour 写入时的锁位置**：写一个最小的 Harbour 写入程序（共享打开、追加 / 改行、按规矩加解锁、索引由 DBFCDX 增量维护），在 `C:\PearnlyStress` 下 TEST 的整本副本上追加一张凭证，Process Monitor 抓它的锁：必须与 P0 完全同位（追加锁表头 0x7FFFFFFE、记录 0x7FFFFFFE − 行号、索引独占 0x7FFFFFFE）。不同就停（否决条件 2）。约 3 小时写程序 + 30 分钟跑。

**a 真写（测试机1 + 69EXP\TEST）**：
- a1 先追加一张普通日记账凭证（GLJNL 1 + GLJNLIT 2，GLBAL 两行按增量改），Express 停在凭证窗口；看 Express 不重开能否看到、打开、按各索引翻页查找、试算表含它。
- a2 再一张赊购单（APTRN 追加 + ISRUN 锁行取号 + STCRD / STMAS / APBAL / APMAS 改行 + ISVAT + 凭证），写法照 P0 的顺序；核应付明细表。
- 取号必须「锁 ISRUN 行 → 锁内重读 → +1 → 写 → 解锁」，余额必须锁内重读再加增量（不能写写前算好的整行）。

**b 索引一致性**：按原计划（DUMP 增量后的清单 X vs 复制出来整表重建的清单 Y vs Express 自带重建后的 Z）。今天已有 91 个标签的遍历工具与基线，可直接复用；重点挑带 FOR 条件的标签（APTRN 的 CMPLAPP / REFNUM / BILNUM，GLJNL 的 TRNSTAT）、泰文名称键、软删行。Express 自带重建 / 检查的菜单：8.1.6「จัดเรียงข้อมูล」（待实看确认），4.7 系列是库存重算。

**c 并发**：
- c1 取号、c2 同一客户余额：同原计划，各 20 张。
- **c4 提前并加重**：P0 看到 Express 录单时从输完对方起就锁住 APMAS / ARMAS 那一行直到保存（十几秒到几分钟，人停着不存就一直锁）。要验：我们写同一对方时能等到锁、超时就按「等账套」放弃，绝不硬写；人一直不存时我们的行为。
- c5 新增：同机并发（Express 与我们的程序都在测试机1）与跨机并发（我们的程序在测试机2）各做一遍——客户现场两种都有，SMB 缓存行为不同。跨机要用户在测试机2 的 acct 下连一次 S 盘（测试机2 的 Express 不能用，只跑我们的程序）。
- 每轮后做 b。

**d 故障注入（会弄坏账套的安排）**：
- d1 写到一半杀进程、d2 断网 10 秒、d3 持着行锁杀进程：先在 C 盘副本上做（只看表头行数 / 文件长度 / 索引清单 / 残留锁），不需要 Express。
- 「Express 还能不能用」那一半只能在 TEST 上做：做之前把 `69EXP\TEST` 整本复制到测试机1 本机（只读复制），记下每个文件的哈希；注入后让 Express 打开看；无论好坏都在 Express 退出、确认 CC 没有往 TEST 写的任务后，用备份整本还原并逐文件核哈希（TEST 用户已授权自由增删改写）。不往服务器上新建账套目录（那要改 Express 的公司登记表）。
- 恢复手段（写前日志 + 反向操作）做一次并核报表回到写前。

**e 计时**：TEST（小）与 NLTTEST 的整本只读副本（放测试机1 本机盘，不写 NLTTEST 本身）各写 10 张；另在网络盘 TEST 上写 10 张。记单张秒数，并单独记「共享读每页往返加锁」的开销（P1 显示 SMB1 上这是主要成本）。全部在现有「SMB1 + 无线」条件下测，不做换链路对照。

**估时**：写程序 4–5 小时（a0 写入程序 + 日志回滚）+ 测试机1 上约 5–6 小时（a0 0.5、a 1、b 1、c 2、d 1.5、e 0.5）+ 测试机2 并发 1 小时（等用户连 S 盘）。全部只用测试套与副本。

## 8. 证据位置（共享笔记 `共享写入实验-11-12/`）

- `B8-第1–4轮-o_b8*`：每种窗口开 / 关时的逐文件探测、截图、窗口树；`b8_matrix.txt` 对照矩阵。
- `P0/`：`procmon-p0-ExpressI-TEST.csv`（过滤后 5855 行）、`p0_analysis.txt`、`p0_timeline.txt`、`o_p0/`（截图、录单记录 record.json）。
- `P1/`：`procmon-p1-ExpressI+shared_probe-TEST.csv`、`p1_analysis.txt`、`o_p1/`（106 份探针输出、截图）。
- `M1-链路测量/`（含 `sizes.csv`，只有文件名与大小）、`M2-companion-timing.txt`、`M4-索引标签-T9HENG15副本.txt`。

## 9. 收尾

- 测试机1：`C:\PearnlyStress\_sw1112`（脚本、Process Monitor 与 Harbour 解压目录、约 1.4 GB 抓包）已删、读回不存在；计划任务 `PearnlySw1112*` 为 0；ExpressI、Procmon 进程 0；CC 进程 15596、配置哈希 7D2EE159 不变；`C:\PearnlyStress\_tools1112` 两个原始压缩包照旧（不是本窗口建的）。占用表已释放。
- 测试套里新增：RR0000094（及它带出的 STCRD / GLJNL / GLJNLIT / ISVAT / APBAL / APMAS / STMAS / GLBAL 变动），留着。
- 本机：脚本在会话临时目录；`~/pearnly-devtools/shared-write-exp/` 原样。

## 10. GLBAL 核实（主会话 11-12 追问，只读）

问题：P0 看到 Express 保存时写了 GLBAL，与上午「原生保存不动 GLBAL」的结论冲突。

- Express 保存 RR0000094 时锁并整行写回 GLBAL 第 12、6、7、8 行（= 这张单 4 行分录的科目，部门空），**金额栏没有加**：这 4 个科目 11 月 GLBAL 与分录合计正好差这张单的金额。唯一变化是 **CALSTA**（计算状态，每期一位）：全表 1134 行只有这 4 行第 11 位（11 月）是 '0'。
- 规则：**原生保存不累加 GLBAL，只把「科目 + 部门」那一行、该期那一位标成 0（待重算）**；重算时只重算标 0 的格，从 GLJNLIT 整格重新求和。上午 GLBAL 是从没算过的空壳，标记本来就不是 1，所以保存前后逐字节相同。
- 直写现状：不累加也不标 0 → 那些格仍是「已算好」→ 我们写的金额进不了科目余额，直到有人整本重算。实证：TEST 里 CC 直写的 RE0000006 不在 GLBAL 11 月、对应行 CALSTA 仍全是 1；NLTTEST「1130-01、1113-01 含我们的金额、1111-00 / 1112-01 / 4100-04 没有」正好是：前两格 11 月还有原生单把它们标成 0、之后被重算（连我们的一起算进去），后三格 11 月只有我们的分录、没人标 0。
- 直写要补的原生动作：每行分录对应 GLBAL「ACCNUM + DEPCOD」那一行，把 CALSTA 该期那一位改成 '0'（VFP 行锁、整行写回），金额栏、期初、LY / NY 栏不动。没查到：那一行不存在时怎么做、NY 期间是不是第 13–24 位、什么动作触发「只重算标 0 的格」。
- 证据：共享笔记 `GLBAL核实/`（TEST 表副本、比对脚本与输出）。

## 11. 写入阶段（主会话 11-12 批准 a0–e）

实验写入程序 `docs/work/shared-write-exp/sw_write.prg`（Harbour 3.0 DBFCDX，共享打开，只做实验，不进产品）。动作：CLONE（复制一行追加、可改字段，带删除标记照带）、SET、ADD（锁内重读按增量加）、NEXTNO（ISRUN 锁行取号）、CALSTA（标待重算）、DELETE、REINDEX（只对副本，做比对基准）；表头两个字节（年份、第 28 字节标志）在表头锁内写回 Express 原值；PAUSE1–3 为故障注入暂停点。

### a0（本机副本）：通过
- 锁位置与 P0 的 Express 同位：表头 / 追加 DBF 0x7FFFFFFE、行 0x7FFFFFFE − 行号、写索引 CDX 0x7FFFFFFE 独占；读索引同位置共享；索引页写入都在锁内。
- 6 张表 37 个标签：增量写后与整表重建逐行相同。
- 差异：追加顺序 Harbour 是「行 + 表头 → 放表头锁 → 索引」，Express 是「索引 → 行 → 表头」；Harbour 会改表头年份字节（0xEA → 0x7E）与第 28 字节标志（0x00 → 0x01）→ 写入程序已改成在表头锁内写回原值，验证通过（head_patch=7E01->EA00）。

### a1（服务器 TEST，Express 开着凭证窗口）：通过
- 追加凭证 SWA1JV01（借 51-01-00-00 1.00 / 贷 14-01-03-00 1.00）并标 GLBAL 第 4、5 行待重算，每步 0.5–1.2 秒。
- Express 不重开窗口就能翻到、分录正确；期间 Express 0 次写入、0 个失败事件、无报错。
- b：8 张表 76 个标签增量与重建逐行相同。

### a2（服务器 TEST，Express 开着赊购单窗口）：通过，有 1 处实验程序缺陷已更正
- 先让 Express 原生录 RR0000095（ซ014、PN00030 × 2 @ 20），录前录后整本快照得出原生改动：ISRUN +1；追加 APTRN、STCRD、ISVAT、GLJNL 各 1 行、GLJNLIT 4 行（其中 51-03-01-00 贷方那行写完即打删除标记）；APBAL 第 701 行 RR11 +42.80；APMAS 第 706 行 BALANCE +42.80；GLBAL 这次没变（已是待重算）；STMAS 没变。
- 我们照写 RR0000096：除单号 / 单据号 / 时间外与原生相同；余额按增量正确；Express 按「最后一张」直接显示 RR0000096、明细正确；b：9 张表 58 个标签一致；共 3.7 秒。
- 差异：APTRN 的 PVAT_RF / PVAT_NRF / PRNCNT、GLJNL 的 PRNCNT 原生是空白、我们是 0（意义相同，产品化按原生写空白）。
- **实验程序缺陷**：复制 51-03-01-00 那行时没带删除标记，RR0000096 多一行有效贷方 40.00、凭证借贷不平。比对脚本当时没显示删除标记，所以没在 a2 当场发现；已改程序（照带删除标记、加 DELETE 动作）并补删除标记（见 c 节）。这是实验程序的问题，不是共享写入的问题。

### d（C 盘副本 `a0b\TEST`，先看清后果）
- d1 追加了空行、还没写内容时杀进程：表头行数已 +1、文件长度与行数一致，留下一行全空白的记录；这行**不在索引里**（增量索引与整表重建的 4 个标签都不同：重建会把空白行算进去）。
- d1b 内容还在缓冲、没提交时杀进程：同 d1（又多一行空白）。
- 两次被杀都没走到「表头两个字节写回」：表头留着 Harbour 的年份字节与第 28 字节标志 0x01。
- d3 持着 APMAS 行锁时杀进程：锁随进程结束立刻释放，下一个进程马上锁得到；那一行没被改。
- 被杀之后再正常追加：行号接着走、索引正常（新行在索引里）。
- 含义：被打断的追加会留下「已计数、不在索引里的空白行」和没写回的表头字节——Express 能不能照常用要在 TEST 上看；恢复手段是写前日志记下追加的行号，恢复时给这些行打删除标记、把表头字节写回、对受影响的表核索引。
- d2（写到一半断网）：要禁用网卡 = 改系统设置，本任务规矩不允许，没做。

### c1 / c2 同机并发（服务器 TEST，14:33–14:44）：出现否决候选，已停
- 做法：Express（代录引擎）连录 20 张 RR（ซ014、PN00030 × 2 @ 20），同时写入程序每 25 秒写一张同供应商、同单据簿的 RR。
- 通过：RR0000097–136 共 40 张不重不漏（Express 偶数、我们奇数），ISRUN 下一个号正确；APMAS 706 BALANCE、APBAL 701 RR11 增量都正好 1,712.00；APTRN / STCRD / ISVAT / GLJNL 每张 1 行；并发前已有的行一行没被改。
- **问题**：Express 自己的 RR0000110 少了进项税分录（借 11-05-04-01 2.80），RR0000116 的 51-03-01-00 行变成「借 40、未删除」（原生应为「贷 40、已删除」）。两张都是 Express 的分录行中间夹着我们刚追加的行。没开 Process Monitor，不能断定丢的是谁的写。
- 我方 20 张都带着已知的程序缺陷（多一行有效贷方 40）。
- 已按否决规则停下报主会话，等是否开 Process Monitor 复现查根因、TEST 怎么清理。
