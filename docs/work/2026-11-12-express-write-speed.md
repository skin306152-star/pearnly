# 需求影响评估：CC 写 Express 按类提速

> 只评估，不改产品代码、不合入。基线 `32695c276`（= 开工时的 `origin/main`）。评估窗口 2026-11-12。
> 标注约定：**【实测】** = 收款窗口 11-12 在测试机量到的数；**【代码】** = 读代码得到、带位置；**【推算】** = 用实测数按字节量折算，没跑过；**【未查到】** 单列在 §14。

## 0. 需求原文与理解

- 起因：收款窗口 11-12 在测试机经局域网（SMB）往 `\\accserver\d$\ACCOUNT\69EXP\NLTTEST` 写 5 张收款单 RE，每张 202–235 秒【实测】。岳父的大米经销商客户一天约 150 张收款，同一本账套一张一张写约 9 小时；LINE「20 秒内回单号」做不到。用户 11-12 同意开本评估。
- 理解：把一张单的写入时间降下来，**不降低任何一项现有安全核对**（写前备份可恢复、写后读回逐字段核对、索引正确、并发锁）。用户口径「只有 100% 确定提速且对结果零影响才做」。
- 不在范围：改记账口径、改载荷、Express 开着时也能写（现状就不能，见 §5）、代录（走界面，不是这条路）、MR.ERP。

## 1. 现有同类与复用

### 1.1 共用写入路径（所有直写单据同一条）

入口 `companion/src/companion/worker.py:320`（`_handle_dbf_task`）：拿账套写者锁 `PEARNLY.LCK` 后按方向分派，七条写路结构相同——**检查 → 整表备份 → 写 → 重建索引 → 读回 → 索引回查 → 删备份**，共用 `dbf_writer.py` 的 `_backup`（:219）、`_restore`（:245）、`_reindex_or_defer`（:774）、`_verify_index`（:842）、`StepClock`（:251）。

| 单据 | 写路 | 备份的表（整表连 CDX / FPT 拷到本机） | 重建索引的表（每表一次整表重建） |
|---|---|---|---|
| 采购 RR / HP | `dbf_writer.py:285` | `PURCHASE_BACKUP_TABLES` 11 张（`dbf_schema.py:564`）+ STLOC / 备注表 | ISRUN、APTRN、ISVAT、GLJNL、GLJNLIT、APBAL；有明细再加 STCRD、STMAS、ISACC；新供应商加 APMAS |
| 销售 IV / HS | `dbf_sales.py:309` | `SALES_BACKUP_TABLES` 11 张（`dbf_schema.py:565`） | ISRUN、ARTRN、ISVAT、GLJNL、GLJNLIT、ARBAL + 同上主档 |
| 收款 RE | `dbf_receipt.py:426` | 8 张（`dbf_receipt.py:95`） | 7 张（`dbf_receipt.py:94`） |
| 退货 | `dbf_refund.py:443` | 同销售 | 同销售 |
| 库存单 / 调拨 / 作废 | `dbf_stock.py:328`、`dbf_transfer.py:171`、`dbf_void.py:214` | 各自 `TABLES` | 各自 `TABLES` |

主档索引已有一处「批末一次」：`cdx_reindex.IndexBatch`（`cdx_reindex.py:190`），只管 STMAS / STCRD / ISACC / 新建对手方，交易表仍逐张重建。

### 1.2 时间花在哪（收款 RE，对上实测）

【代码】Python `dbf` 库（0.99.11，`companion/packaging/requirements-build.txt`）打开表只读表头，逐行读 = 顺序把整个文件读一遍（8 KB 一块）；追加只写新行和表头，不扫表。所以**每出现一次「遍历整表」，就是经网络盘把这张表整个读一遍**。NLTTEST：ARTRN 9.9 MB、GLJNLIT 15.0 MB、ARBAL 8.9 MB、ARMAS 5.6 MB、GLJNL 4.8 MB【实测大小】。

| 段 | 实测秒 | 代码做了什么 | 经网络盘搬的量【推算】 |
|---|---|---|---|
| 检查 | 16–27 | `existing`（`dbf_receipt.py:274`）整表读 ARTRN；`_code_exists`、`receivable_account` 各扫 ARMAS；`check_invoices`（:316）再整表读 ARTRN | 约 25 MB |
| 备份 | 44–62 | `_backup`：8 张表 DBF + CDX 整文件拷到本机 | 53 MB（对照：纯拷贝 31 秒【实测】） |
| 写入 | 32–61 | `_next_docnum`（:388）整表读 ARTRN；改发票余额又整表读 ARTRN（:543）；摘要查 ARMAS；`_update_arbal` 扫 ARBAL；`move_party_balance` 扫 ARMAS。真正写的只有十几行 | 约 40 MB |
| 重建索引 | 28–52 | `cdx_reindex.reindex`（`cdx_reindex.py:145`）：7 张表各起一次 `cdx_reindex_runner.exe`，独占打开、整表 `REINDEX`（`packaging/native/cdx_reindex_runner.prg`） | 读 39 MB + 写 CDX 约 8 MB |
| 读回 | 45–66 | `_verify`（:651）：`appended` 逻辑上只取本次追加的行，但实现是整表遍历后再按行号筛（:663）→ ARTRN、GLJNLIT 各整表读；`invoice_state` 又整表读 ARTRN | 约 35 MB |
| 索引回查 | 0.2–0.6 | `SEEK` 走 Express 自己的索引，只读几页 | 很小 |

合计约 200 MB，实测链路整文件拷贝约 1.7 MB/s、Python 小块读更慢，与 202–235 秒吻合。**同一张 ARTRN 在一张收款单里被整表读 8 遍**（检查 2、备份 1、写入 2、重建 1、读回 2）。

三种成本：
- **网络盘往返**：占绝大部分。同样的 8 表拷贝本机 0.08 秒、SMB 31 秒；4 表整读本机 0.02 秒、SMB 12–14 秒【实测】。
- **与表大小成正比**：上表每一段都是（备份、重建、所有「遍历」）。账越用越大越慢。
- **每张单固定开销**：起 7–8 次重建程序、开关文件，本机约 1–3 秒【推算】，不是主因。

采购、销售、退货、库存单同一模式（各自的 `_verify`、`_next_docnum`、`_update_apbal`、`find_supplier_code` 等都是整表遍历，如 `dbf_writer.py:154`、`:740`、`:873`、`:916`），只是表不同；带明细的还要多读 STCRD / STMAS。

- 结论：扩展现有共用路径，不新建第二条写路。

## 2. Frappe 原生能力

不涉及：全部在 CC 桌面版（Windows 端）本机代码里，不经 Frappe。

## 3. 按类通用

- 类：**所有 CC 直写 Express 的单据**（采购、销售、收款、退货、库存单、调拨、作废、主档新建）。
- 推荐方案放在共用层（会话 + 差异回写），七条写路的记账代码一行不改，一次覆盖全部；不做「只给收款提速」的补丁。

## 4. 上下游数据

- Express 映射：写进账套的 DBF 字节与现在**逐字节相同**是硬指标（§11 用回放证明）；CDX 仍由同一个重建程序产生。
- CC 桌面版：第 1、2 步不改载荷、不改回执。第 3 步（批量）一次要拿着多张未回报的任务，现在领取接口一次只给 1 张（`queue_client.py:212` `max: 1`），服务端是否允许同一台电脑同时持多张**【未查到】**；要改就是 `tests/test_companion_contract.py` 守的接口约定，需升版本、旧版照旧一张一张。
- Cowork / ERP：都走这条路，一起变快；服务端代码第 1、2 步不动。

### 4.1 可选做法逐项评估

| # | 做法 | 能省多少【推算】 | 对安全核对 | 风险 / 结论 |
|---|---|---|---|---|
| a | 批量：一把锁、备份一次、写 N 张、重建一次、读回一次 | 150 张从约 9 小时降到约 10–15 分钟（在 b–e 的基础上） | 不削弱，但回报要等整批提交后才发；一张失败只撤这一张、重建失败整批退回「等账套」 | 可行，放第 3 步；涉及领取接口 |
| b | 备份只备会改的部分（追加前的文件长度、表头、被改行的原值） | 备份 44–62 秒 → 1 秒内 | 恢复 = 截回原长度 + 写回原值 + 表头，对 DBF 是精确还原；但 CDX 是整体重建的，仍需整份 CDX 原件才能精确还原 | 单独做不够（还要读整表做检查）；并入推荐方案：本机留整表原件，回滚只回写改过的字节 |
| c | 读回只核本次写入的行 | 读回 45–66 秒 → 1–5 秒 | 核对内容不变（现状逻辑上已只核本次写的行，只是物理上整表读，`dbf_receipt.py:663`、`dbf_writer.py:873`、`write_verify.py:87`）；需保证「写前没有同号旧行」——由取号时的整表检查 + 写前比对表头行数保证 | 可行，并入推荐方案 |
| d | 索引增量维护，不整表重建 | 重建 28–52 秒 → 约 1 秒 | 原生证据：Express 静态链接 CodeBase 引擎、打开表时自动打开同名结构化 CDX、标签定义在 `DBINF.DBF`（`docs/work/2026-10-25-express-c-drive-fix.md:34`、`:47`）；Express 自己录单就是逐条增量维护。**但「我们用 Harbour 增量写出的 CDX 与 Express 逐字节 / 逐键一致」没有证据**【未查到】，泰文键排序是主要疑点 | **现在不建议**；先做 §11 的实验 E，过了才谈。推荐方案不依赖它 |
| e | 先拷到本地操作再整体写回 | — | **整文件写回不可行**：会把这期间别人写的内容盖掉，且写回量与表大小成正比 | 改成「本机算、只把改动的字节写回、写前逐段比对原值、写回期间独占」才可行——这就是推荐方案 |
| f | 重复工作 | — | — | ARTRN 一张单读 8 遍、ARMAS 4 遍；科目表 / ISINFO 每次查询各开一次；重建程序每表起一次进程。会话化后自然消除，不单独打补丁 |

### 4.2 有没有更小的改法能拿到大部分收益（主会话 11-12 要求的对照）

折算用的速率都来自 §1.2 的实测：整文件拷贝约 1.7 MB/s（53 MB / 31 秒）；Python 逐行读约 1.2 MB/s（检查段约 25 MB / 16–27 秒）。下面的秒数全是【推算】，收款 RE、NLTTEST、测试机到 accserver。

**a. 备份改成服务器端复制（数据不过网络）**

- 现状【代码】：`_backup` 用 `shutil.copy2`（`dbf_writer.py:241`）拷到本机（`_local_backup_root`，`dbf_writer.py:201`）。CC 用 Python 3.11（`companion/packaging/build.ps1:6`），3.11 的 `shutil` 在 Windows 上是「读进本进程再写出」（`_copyfileobj_readinto`，本机 3.11 源码核对过，没有调用系统的 CopyFile）。所以 9-28「备份放账套旁 5.6–7.4 秒、放本机更快」（`dbf_writer.py:195`）量到的是数据过两次网络的拷法，**服务器端复制从来没试过**。
- 要成立需要：改用系统复制接口、备份放回客户服务器的共享目录里、服务器与这条连接支持服务器端复制。accserver 的 SMB 版本没查到，代码注释里写的是「老旧 SMB1」（`account_lock.py:6`）。**能省多少没有实测**，成立时备份 44–62 秒 → 估计几秒，不成立时不变。第 0 步加一项实测（M6）才能下结论。
- 代价：备份文件回到客户服务器上（9-28 特意挪走的）；回滚的整文件还原同样依赖它。

**b. 检查与读回按记录定位读，不整表取回**

ARTRN 的 8 遍逐遍看【代码】：

| # | 位置 | 干什么 | 能否不整表读 |
|---|---|---|---|
| 1 | `dbf_receipt.py:274` `existing` | 按客户 + 收款记录号找已写过的 RE | 要看全表；可与 2、4 合成一遍 |
| 2 | `dbf_receipt.py:287` `invoice_state` | 找要收的发票、当前余额 | 同上，合并 |
| 3 | `dbf_writer.py:219` `_backup` | 整表备份 | 见 a |
| 4 | `dbf_receipt.py:388` `_next_docnum` | 收集用过的单号 | 同上，合并 |
| 5 | `dbf_receipt.py:543` | 改发票余额时再找一遍发票行 | **可直接消掉**：行号第 2 遍已知，按行号读写 |
| 6 | `cdx_reindex.py:145` | 整表重建索引 | 不能（见 c） |
| 7 | `dbf_receipt.py:663` `appended` | 读回本次追加的单头 | **可直接消掉**：只读追加的尾部 |
| 8 | `dbf_receipt.py:689` | 读回发票余额 | **可直接消掉**：按行号读 |

- 直接可消 3 遍（5、7、8），1、2、4 合并再省 2 遍，ARTRN 从 8 遍降到 3 遍（合并扫描、备份、重建）。ARMAS 现在读 5 遍（`dbf_receipt.py:460`、`:375`、`:615`、`:484` + 备份）可降到 2 遍；GLJNLIT 读回（`:679`）改只读尾部，3 遍降到 2 遍；ARBAL 找客户行仍要扫一遍。
- 省下约 75–80 MB 的 Python 读取，约 60–70 秒。**只做 b：单张约 145–170 秒。**
- 想连那一遍合并扫描也省掉，就得改用 Express 的索引定位（现有重建程序的 `SEEK` 能回行号）。但这改变了查找的语义（软删行、同号多行、键补空格），每一处都要单独证明与现在等价，不属于「零影响」的改法。

**a + b 合计**：a 成立时约 60–85 秒（检查约 15 + 备份约 5 + 写入约 10 + 重建 28–52 + 读回约 2）；a 不成立时约 100–140 秒。

**c. 只剩「重建索引必须整表读」时**：约 30–55 秒（重建 28–52 + 其余几秒）。要到这个数，除了重建以外的每一次查找都得走索引、备份只备改动部分——等于把 b 的语义改动做到底。

**与「会话 + 差异回写」对比**

| | a + b | 会话 + 差异回写 |
|---|---|---|
| 收款单张 | 约 60–85 秒（a 没实测，不成立则 100–140） | 约 75–95 秒（第 1a 步，索引仍在远端重建）→ 约 45–50 秒（第 1b 步） |
| 150 张 | 约 2.5–3.5 小时，批量仍要另做 | 第 3 步后约 10–15 分钟 |
| 改到哪 | 只做收款：`dbf_receipt.py` 的检查、写入、读回重排 + 备份函数。**按类全做：七条写路及其辅助模块里约 97 处整表遍历**（`dbf_*.py`、`master_create.py`、`account_rules.py`、`product_master.py`、`stock_*.py` 等）逐处改记账代码的查找 | 记账代码 0 处；新增一层共用设施（取原件、回写、回写日志） |
| 怎么证明没改结果 | 每处查找单独证明等价 + 回放 | 构造上同一份代码同一份输入，回放逐字节比对一次证明全部写路 |
| 新增的风险 | 逐处改查找时漏掉软删行 / 同号行这类边角；备份回到客户服务器 | 回写中断（靠回写日志还原）、独占句柄在网络盘上的行为、本机磁盘空间 |
| 会不会被后一步覆盖 | 会：批量（第 3 步）建立在会话上，b 改过的查找届时都改在本机跑，a 的服务器端备份不再需要 | 不会 |

**结论：不先做 a + b，照会话方案，但把会话方案自己拆成风险最小的先上。** 依据：
1. a + b 要到「1 分钟上下」取决于 a，而 a 没有任何实测、且很可能受 accserver 的 SMB 版本限制；只有 b 是 145–170 秒，差得远。
2. 改动面并不更小：只做收款违反按类口径，按类做是 97 处记账代码的手工修改，出错点比会话方案多，且无法用一次逐字节回放整体证明。
3. 做完仍解决不了 150 张（仍要批量），而批量会把 a + b 的改动盖掉，属于「过渡版」。
4. 会话方案里风险最大的一块是「本机重建索引再回写 CDX」，所以拆开：**第 1a 步只回写 DBF 的差异，索引照现在的做法在远端整表重建**（索引处理与今天完全相同，单张约 75–95 秒）；回放证明本机重建与远端重建的 CDX 逐字节相同之后，才上**第 1b 步**（约 45–50 秒）。
5. 若 M6 实测服务器端复制有效，它可以直接用在会话方案的「取原件」上吗——不能，取原件要把数据拿到本机；所以 a 的结果只影响「要不要 a + b」，不改变上面的结论，除非 M1 发现链路本身有毛病、修好后现状就够快（见 §14-1）。

## 5. 隔离与权限（并发与正确性）

现有保护【代码】：
1. **电脑之间**：`account_write_lock.py:58` 对账套目录里的 `PEARNLY.LCK` 加字节锁，锁不到回「等账套」。
2. **Express 开着时不写**：`account_lock.py:37` 写前独占试开 `ISRUN.DBF`（有人值守的单，`gui_tray_workers.py:208`）；重建索引必须独占打开每张表（`cdx_reindex_runner.prg` `USE … EXCLUSIVE`），Express 开着这张表就拿不到 → 整单回滚、回「等账套」（`dbf_writer.py:757`）。**所以现状是：任何人开着这本账套的 Express，直写就写不进去，只能等。**
3. 三重一致性闸、任务日记防重写、读回、索引回查。

现状的薄弱处（不是本次引入）：写 DBF 那几十秒到几分钟里没有任何独占，只在开头探一次、在重建时才真正独占；Express 若在中途打开账套并改了数据，回滚时 `_restore` 整文件覆盖会把那些改动一并盖掉。窗口现在是 3–4 分钟。

推荐方案下：
- `PEARNLY.LCK` 照旧全程持有；三重闸、任务日记、读回、索引回查照旧。
- 回写阶段（几秒）对本单涉及的每张表 DBF + CDX **同时独占打开**（不允许共享），拿不到 = 有人开着 → 回「等账套」，与现在同一结果；比现在「逐表先后独占」更严。
- 回写前逐段比对：远端文件长度、表头、每一段要改的字节必须等于本机原件，不等 = 这期间被别人动过 → 一个字节不写、重新取原件再来。现状没有这一层。
- 暴露窗口从 3–4 分钟缩到几秒。
- 批量：整批在一把 `PEARNLY.LCK` 内，别的电脑等；Express 中途打开 → 回写时独占拿不到 → 整批退回「等账套」，没有半批落盘。
- 只备份改动部分（b）：在「回写期间独占 + 逐段比对」之下恢复是精确的；没有这两条时不安全（别人追加过行，截回原长度会删掉别人的行），所以不单独做 b。

### 5.1 现状：有人开着这本账套的 Express 时，直写只能等（主会话 11-12 要求写准）

以下全部【代码】，没有实测；收款部分看的是分支 `pearnly/collection-cc-chat`（未合入）。

**怎么判定「开着」**
- 有人值守的单：写前独占试开 `ISRUN.DBF`，打不开就不写（`companion/src/companion/gui_tray_workers.py:208`、`account_lock.py:37`）。
- 无人值守的单（定时任务、自动推、录入人没在这台电脑登录过 CC——**LINE 上收款员录的收款属于这一类**；判定 `gui_tray_workers.py:116`，服务端算法 `apps/pearnly_core/pearnly_core/cc_desktop/wire/route_contracts.py:277`）：不探，直接照常「备份 → 写 → 重建索引」，到重建索引拿不到独占才发现（`dbf_writer.py:757`；重建程序单表最多等 90 秒，`cdx_reindex.py:30`），然后整单回滚、回报「等账套」。

**等待期间单子是什么状态**
- 服务端规则：回到待领，尝试次数不加（`apps/pearnly_core/pearnly_core/cc_desktop/wire/ack_rules.py:100`）。Cowork 推送记录 `status = pending`、原因清空、失败次数不加（`apps/pearnly_cowork/pearnly_cowork/push/native/service.py:906`）；ERP 任务 `status = queued`、原因清空、不再钉在原来那台电脑（`apps/pearnly_core/pearnly_core/cc_desktop/jobs.py:370`）。
- **没有次数上限，也没有时间上限**：Express 开一整天，单子就等一整天，不会转失败、不会转人工（上限只管普通失败，3 次，`ack_rules.py:12`、`:109`）。

**多久重试一次**
- 服务端没有冷却，放回即可再领。CC 这边只有「一整轮什么都没领到」才睡 3 秒；领到过任务的轮次不睡，立刻再领（`companion/src/companion/multi_profile_worker.py:361`、`config.py:57`）。
- 所以**无人值守的单在 Express 开着期间是连续重试**：每一轮都完整做一遍 备份 → 写 → 重建失败 → 整表回滚 → 回报 → 立刻再领。按现在的速度，一轮几分钟，整天不停地经网络盘搬整表，并且每轮都在别人开着 Express 时把整张表写回去一次（§5「现状的薄弱处」）。这是比慢更需要先处理的一点，见 §12-6。
- 有人值守的单：弹框后一直等人点，没有超时（`gui_tray_workers.py:122`）；等待期间这条取任务线程不处理别的单。

**Express 关了之后多久写进去**
- 无人值守：正在进行的那一轮失败尝试做完（最坏含 90 秒超时 + 回滚）→ 立刻再领 → 一次正常写入。按现状约几分钟【推算】；提速后相应缩短。
- 有人值守：要有人在那台电脑上点「重试」才写；没人点就一直不写。点了之后立刻再领、探测通过即写。

**用户各处看到什么**

| 位置 | 看到的 | 依据 |
|---|---|---|
| 装 CC 的电脑（有人值守） | 弹框「账套正被占用」：「账套正被 Express 占用。关闭正在使用该账套的 Express 后点击「重试」，或点击「终止推送」。」点「终止」= 这张按失败回报 | `companion/src/companion/i18n.py:295`–`303`、`gui_tray_workers.py:150` |
| 装 CC 的电脑托盘 | 橙黄色「正在等待 Express 释放账套…」 | `i18n.py:288`、`gui_tray.py:47` |
| Cowork 网页 | 「录入中」，没有任何「等账套」字样 | `apps/pearnly_cowork/pearnly_cowork/history/push_state.py:61` |
| ERP 网页（采购、销售、库存） | 「排队中」，同样看不出原因 | `apps/pearnly_erp_portal/pearnly_erp_portal/express/push.py:585` |
| LINE 收款 | 放行后等 20 秒，等不到回「已记录 · 待写入」+【撤回】；之后只有写成 / 失败才推送，**等账套期间没有任何消息** | 收款分支 `integrations/line_collection.py:41`、`:804`，`collection/push.py:114` |
| 「我的收款」页 / 收款记录 | 「待写入」，原因为空 | 收款分支 `collection/push.py:84` |
| CC 对话（直写） | 最多等 25 秒，回「已排队」类说明，说明里只提 CC 桌面版可能离线，没提 Express 开着；普通单据写成后会补一句，收款这条现在不会补 | `apps/pearnly_core/pearnly_core/assistant/direct_push.py:27`、收款分支 `collection/cc_tools.py:47` |

- 「Express 正在使用此账套，关闭后将自动推送」这条文案两边都有（`apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv:1467`、`apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv:1388`），但等账套时原因被清空，**按代码它显示不出来**。也就是说：除了装 CC 那台电脑的弹框和托盘，网页、LINE、CC 对话上的人都不知道单子是在等 Express 关闭。

## 6. 入口：网页、手机、LINE、拍照

- 入口不变。LINE 收款现有口径：放行后等最多 20 秒回 RE 号，等不到回「已记录 · 待写入」+ 之后推送结果。
- 提速后：
  - 第 1 步后单张约 45–50 秒【推算】，仍超过 20 秒 → **「先回已记录、写完补结果」必须保留**。
  - 第 2 步（本机镜像）后单张约 8–20 秒【推算】，多数能当场回单号，但电脑关机 / Express 开着 / 排队时仍然等不到 → **这条兜底永远保留**，不删。
  - 账套就在 CC 这台电脑本机盘时（很多小客户如此），现状多快**【未查到】**，可能本来就在 20 秒内。

## 7. 共用件与连带回归

- 改到的共用件：`dbf_writer.py`（备份、回滚、重建、读回的共用函数）、`cdx_reindex.py`、`worker.py` 写账分派。施工时跑 `python3 scripts/check-impact.py --files …` 贴清单；预计命中「CC 写 Express」全部入口（Cowork 推送、ERP 采购 / 销售 / 库存 / 收款、定时任务）。
- 新增回归：新旧写法逐字节比对的离线回放（§11）。

## 8. 存储与成本

- 第 1 步：与现在一样，本机临时放一份涉及表的原件，写完即删。
- 第 2 步：本机常驻一份镜像（只含写入涉及的表，NLTTEST 约 53 MB / 账套；62 本测试账套量级约数 GB 以内【推算】），放 `%LOCALAPPDATA%\Pearnly`；需要上限与清理规则。
- 无云端成本、无识别 / 模型费用、无固定月费。

## 9. 删除、作废、追溯

- 不变。`[timing]` 日志保留并加新分段，便于对照。
- 断电恢复：回写前先在本机落一份「回写日志」（要写的每段的位置、原值、新值）。下次启动发现未完成的日志 → 逐段比对远端：等于新值或原值的段写回原值，全部还原后删日志；CDX 用本机原件整份还原。现状断电后只能靠人发现（本机留着整表备份，但没有自动恢复）——这是加强。

## 10. 文案与官方 UI

不涉及：无新增 / 改动界面文案。

## 11. 测试与验收

### 推荐方案（一次到位的结构）：账套写入会话 + 差异回写

1. 拿 `PEARNLY.LCK`（不变）。
2. **取原件**：本单涉及的表（DBF + CDX + FPT）整文件读到本机两份——「原件」（只读，等同现在的写前备份）和「工作副本」。
3. **在工作副本上跑现有写路**：检查、取号、写入、重建索引、读回、索引回查，七条写路的记账代码原样执行，只是目录指向本机。三重一致性闸仍对真实账套路径判。
4. **回写**：独占打开远端这些文件 → 逐段比对远端 = 原件 → 落回写日志 → 只写「工作副本与原件不同的字节段」（追加的行、改过的行、表头、CDX 里变了的页）→ 读回这些段与工作副本逐字节相同 → 远端 `SEEK` 回查单头。任何一步不过 → 按日志还原、回「等账套」或失败，与现在的回滚等价。
5. 回报、删本机文件。

为什么零影响：远端最终字节 = 现有代码在同一份输入上算出的字节；差的只是「在哪块盘上算」。这一点由回放逐字节证明，不靠推理。

分步与估时（写代码 + 机器跑 + 等人）：

| 步 | 内容 | 写代码 | 机器跑 | 等人 | 单张【推算】 | 150 张【推算】 |
|---|---|---|---|---|---|---|
| 0 | 测试机只读测量（M1–M4）；之后另排 M5、M6、实验 E | 1 小时 | 1 小时 | 等主会话排测试机 | — | — |
| 1a | 会话 + 只回写 DBF 差异 + 回写日志 + 断电恢复（全部写路）；索引照现在在远端整表重建 | 5–7 小时 | 回放 2–3 小时、测试机实写 2 小时 | 排测试机 | 约 75–95 秒（取原件 31 + 本机 3–6 + 回写 2 + 远端重建 28–52 + 读回 2） | 约 3–4 小时 |
| 1b | 索引在本机重建、只回写 CDX 变了的页（先由回放证明本机与远端重建逐字节相同） | 2–3 小时 | 回放 1 小时、测试机 1 小时 | 排测试机 | 约 45–50 秒 | 约 2 小时 |
| 2 | 本机镜像：原件常驻，写前只核对指纹（长度、修改时间、变更时间、表头）+ 补读新增的尾部，不再整表取 | 4–6 小时 | 故障注入 3 小时 | 排测试机 | 约 8–20 秒 | 约 20–50 分钟 |
| 3 | 批量：同一本账套连续的单共用一次会话，一次回写 | 3–4 小时（另加服务端领取接口，若要改） | 2 小时 | 接口约定要主会话审 | 不变 | 约 10–15 分钟 |

第 2 步的诚实说明：镜像靠「独占打开下的文件指纹」判断远端没变，工程上可靠但不是数学上的 100%；被改的行有回写前逐段比对兜底，只读不改的行（查重、取号）靠指纹。是否满足「100% 确定」口径，要看 §11 故障注入 F4 的结果，**由主会话 / 用户定**（§12-2）。不做第 2 步，第 1 + 3 步仍成立。

### 验证计划

**离线回放（本机 / WSL，不碰测试机在用的东西，不花钱）**
- R1：62 套（T8 / T9 副本）+ NLTTEST 只读副本，每套用旧写法与新写法各写同一组合成单（采购赊 / 现、销售、收款 5 种场景、退货、库存单、调拨、作废、新建对手方、带明细 / 库存行），比对：**每张表 DBF 全文件逐字节相同**；CDX 用 `cdx_reindex.tags` 核标签与键表达式相同、并逐字节比。
- R2：回执（单号、line_modes、verify、notes、receipt）逐字段相同；失败用例（读回不符、发票不够收、借贷不平）错误码与证据相同，账套文件与写前逐字节相同。
- R3：`companion/tests` 全套 + `tests/test_companion_contract.py`。
- 注意：重建程序是 Windows exe，本机 Mac 跑不了真重建；R1 的 CDX 比对要在 Windows（测试机或 CI 的 Windows runner）上跑，本机只能比 DBF。

**第 0 步：测试机只读测量清单（交主会话排在收款窗口之后；先登记占用表；accserver 21:00 前；不启动 / 不重启 CC，不开 Express，不写任何账套）**

在测试机以 skin3 开 PowerShell（隐藏窗口 / 远程执行均可），结果存 `C:\PearnlyStress\_speed1112\`（本机目录，测完可删）。先建目录：`New-Item -ItemType Directory -Force C:\PearnlyStress\_speed1112 | Out-Null`。

M1 链路为什么慢（全部只读）：
1. SMB 版本与签名：`Get-SmbConnection | Format-List ServerName,ShareName,Dialect,Signed,Encrypted,UserName`（没有 accserver 那行就先 `Get-ChildItem \\accserver\d$\ACCOUNT\70EXP\test\ISRUN.DBF` 再查）。`Dialect` 1.x = 老协议，本身就慢且没有服务器端复制。
2. 客户端签名设置：`Get-SmbClientConfiguration | Format-List RequireSecuritySignature,EnableSecuritySignature,EnableLargeMtu,EnableBandwidthThrottling`。
3. 网卡：`Get-NetAdapter | Format-Table Name,InterfaceDescription,Status,LinkSpeed,MediaType,PhysicalMediaType`（看走有线还是无线、协商到 100 Mbps 还是 1 Gbps）；`Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Format-Table InterfaceAlias,NextHop,RouteMetric`。
4. 延迟与丢包：`Test-Connection accserver -Count 50 | Measure-Object ResponseTime -Average -Maximum`；`ping -n 20 -l 1400 accserver`。
5. 杀毒实时扫描：`Get-MpComputerStatus | Format-List AntivirusEnabled,RealTimeProtectionEnabled,OnAccessProtectionEnabled`；`Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Format-Table displayName,productState`。只看不改。
6. 读速度，大块与小块各三次（只读打开源文件，写到本机）：
   ```powershell
   $src='\\accserver\d$\ACCOUNT\69EXP\NLTTEST\GLJNLIT.DBF'
   foreach($buf in 1MB,8KB){ 1..3 | % { $sw=[Diagnostics.Stopwatch]::StartNew(); $f=[IO.File]::Open($src,'Open','Read','ReadWrite'); $b=New-Object byte[] $buf; $n=0; while(($r=$f.Read($b,0,$buf)) -gt 0){$n+=$r}; $f.Close(); "{0} bytes buf={1} {2:n2}s {3:n2} MB/s" -f $n,$buf,$sw.Elapsed.TotalSeconds,($n/1MB/$sw.Elapsed.TotalSeconds) } }
   ```
   （`FileStream` 自带缓冲，8 KB 那组近似 Python 的读法。）
7. 对照另一条线：同一段脚本在录制用笔记本（rec-laptop）上对 accserver 跑一次；测试机上对任意另一台电脑的共享文件跑一次。两台都慢 = accserver 或它的网络慢；只有测试机慢 = 测试机这条线的问题。

M2 现成日志里的基线（只读）：`Select-String -Path "$env:LOCALAPPDATA\Pearnly\logs\companion*.log","$env:APPDATA\Pearnly\*.log" -Pattern '\[timing\]' | Select-Object -Last 400 | Out-File C:\PearnlyStress\_speed1112\timing.txt`（日志实际位置以测试机为准）。里面有历史上采购 / 销售 / 收款写 C 盘账套与网络账套的分段计时，不用新写一张单就能得到「本机盘现状几秒」「采购销售在网络盘几秒」。

M3 账套规模（只读列目录）：
```powershell
Get-ChildItem '\\accserver\d$\ACCOUNT\70EXP','\\accserver\d$\ACCOUNT\69EXP' -Directory | % { $d=$_; Get-ChildItem "$($d.FullName)\*" -File -Include ARTRN.*,APTRN.*,GLJNL.*,GLJNLIT.*,ARBAL.*,APBAL.*,ARMAS.*,APMAS.*,STCRD.*,STMAS.*,ISVAT.* | Select @{n='set';e={$d.Name}},Name,Length } | Export-Csv C:\PearnlyStress\_speed1112\sizes.csv -NoTypeInformation
```
用来知道真实账套的表一般多大（NLTTEST 是否偏大）。

M4 索引标签（只读，但重建程序是独占打开，**只对 `C:\PearnlyExpress` 下的副本跑**，不碰 accserver）：对一本 C 盘账套的 ARTRN、APTRN、GLJNL、GLJNLIT、ARBAL、APBAL、ARMAS、APMAS、ISRUN、ARRCPIT、ARRCPCQ 各跑
`& "$env:LOCALAPPDATA\Programs\CC\cdx_reindex_runner.exe" TAGS <账套目录> <表> x x C:\PearnlyStress\_speed1112\tags_<表>.log`，存下标签名与键表达式。

**需要写入的步骤（第 0 步之后另排；只限 `C:\PearnlyExpress` 副本与 `\\accserver\d$\ACCOUNT\70EXP\test`）**
- M5 写速度：在 `70EXP\test` 里建一个 50 MB 的 `_m5_probe.bin`、读回、删除，各计时。
- M6 服务器端复制：`cmd /c copy \\accserver\d$\ACCOUNT\70EXP\test\GLJNLIT.DBF \\accserver\d$\ACCOUNT\70EXP\test\_m6_probe.tmp` 计时三次后删除；明显快于 M1-6 的读速度 = 服务器端复制有效。
- 实验 E（仅 C 盘副本）：同一份账套复制两份，一份 Express 界面录一张单，一份用 Harbour 共享打开追加同样的行，比两份 CDX；只为回答做法 d，不过就永久搁置 d。
- 基线实写（M2 的日志不够用时才做）：正式 CC 往 C 盘副本与 `70EXP\\test` 各写采购、销售、收款 3 张，取 `[timing]`。
- 施工后 V1：新 CC（测试版，不发正式）在 C 盘副本与 `70EXP\\test` 各写同样 9 张 + 一批 30 张收款，分段计时对比；Express 界面逐张打开、跑应收明细表与试算表截图。
- 故障注入（仅 C 盘副本与 `70EXP\test`）：
  - F1 回写到一半杀掉 CC 进程 → 重启后自动还原，账套文件与写前逐字节相同。
  - F2 回写到一半断网（禁用网卡）→ 恢复网络后同上。
  - F3 取原件之后、回写之前，用 Express 打开这本账套并录一张单 → CC 必须一个字节不写、回「等账套」；Express 关掉后重试成功。
  - F4（第 2 步才做）镜像建好后，用 Express 分别做：新录、改已有单、作废、删单、PACK → 每种之后 CC 的指纹都必须判「变了」并重取；各 20 次，一次漏判即否决第 2 步。
  - F5 本机 CDX 重建中断（杀重建程序）→ 远端零改动。
- 验收：主会话复核回放报告与分段计时；用户只看「一张多少秒、150 张多少分钟、Express 里打开正常」。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 是否按「第 0 → 1 → 3 步」开工 | 主会话 11-12 已同意 | — |
| 2 | 第 2 步（本机镜像，单张进 20 秒）做不做 | 等第 1 步上线 + F4 结果再定；不过 F4 不做 | 不做则 LINE 多数走「已记录、稍后推送」，推送条数多（官方号额度） |
| 3 | 批量时回单号要等整批回写完（每批几张到几十张、等十几秒到几分钟），可否 | 可以；每批封顶 20 张或 30 秒 | 不接受则只能逐张，150 张约 2 小时（第 1 步）或 20–50 分钟（第 2 步） |
| 4 | 大米客户的账套放在哪（CC 本机盘 / 局域网服务器）、ARTRN 与 GLJNLIT 多大 | 请主会话转问；本机盘的话第 1 步收益小、第 3 步仍有用 | 推算数字都按 NLTTEST + accserver 链路，换环境会变 |
| 5 | 这家客户白天是否一直开着 Express | 主会话转用户。开着就写不进去（§5.1），提速解决不了 | 白天收款全部排到 Express 关闭后才写 |
| 6 | 无人值守的单在 Express 开着时连续「备份 → 写 → 回滚」不停重试（§5.1），要不要先加「写前先探、占用就不动账套」+ 重试间隔 | 建议并入第 1a 步：会话方案在取原件前先探、回写时才独占，占用时一个字节不写；重试间隔另定 | 不处理则 LINE 收款上线后，Express 开着的每个白天 CC 都在整天搬表并反复整表回写 |
| 7 | 等账套时网页 / LINE / CC 对话都看不出原因（§5.1） | 属文案与状态显示，不在本评估范围，单列给主会话排 | 用户以为卡住 |

## 13. 工作量与排期

主会话 11-12 已定：第 0 步 → 第 1 步 → 第 3 步；第 2 步等第 1 步上线和故障注入结果再定；批量每批封顶 20 张或 30 秒、单号等整批回写完才回；LINE「先回已记录、写完补结果」保留。

顺序：第 0 步 → 第 1a 步 → 回放与测试机验证 → 第 1b 步 → 第 3 步 →（定了再做）第 2 步。合计写代码 15–20 小时、机器跑 9–11 小时，另加排测试机的等待。每一步通过 R1 逐字节比对才进下一步。

## 14. 没查到的（不下结论）

1. 测试机到 accserver 为什么只有约 1.7 MB/s（SMB 版本、网卡速率）；别的客户局域网是否同样慢（M1）。
2. 网络盘**写**的速度（现有计时里没有单独的写入量；回滚没实测过；M5）；服务器端复制是否有效（M6）。
3. 账套在本机盘时现状一张要几秒（M2 的现成日志）；采购 / 销售在 SMB 上的分段计时（收款窗口只量了收款，9-28 压测只有小账套「约 10 秒、每第三张 30–35 秒」，`docs/work/2026-09-28-express-intake-stress.md:144`）。
4. 重建后 CDX 与重建前相比变了多少页（决定第 1 步回写量，推算按最坏整份 8 MB）。
5. 本机重建与远端重建的 CDX 是否逐字节相同（R1 在 Windows 上验证；不同则第 1 步改为「回写 DBF 差异 + 远端重建」，单张约 75–95 秒【推算】）。
6. Harbour 增量维护的 CDX 与 Express 是否一致（实验 E）。
7. 各表标签的键表达式（M4）；本评估没有逐表列出。
8. 服务端领取接口是否允许同一台电脑同时持多张任务（第 3 步前读 `apps/pearnly_cowork/pearnly_cowork/companion/queue_api.py`）。
9. 大米客户的环境（§12-4、5）。
10. 一次写入超过任务租约（旧通道 120 秒，`ack_rules.py:11`）时服务端怎么处理——现在一张收款 200 多秒已经超过，没核对后果。
11. §5.1 全部来自读代码，没有在测试机上实际开着 Express 观察过一轮。
