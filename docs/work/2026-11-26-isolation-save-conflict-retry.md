# 关快照隔离后保存前比对的冲突同样重来（11-26）

- 分支 `pearnly/isolation-timestamp-retry`，基线 `origin/main` = `f18fa1ec8`。主会话 local_93ac5c59 11-26 派（合入列车窗口施工）。用户口径：「虽然没有真实用户，但是并发还是为未来考虑，建议处理好」。
- 起因：train-1125 清卷全量里 `erp.purchase.voucher-concurrent`（8 张采购单 × 补开收据 / 预扣税凭证同时出、外加重复点，共 18 个请求）偶发一个 417 `TimestampMismatchError`。
- 前因：11-23 `4380c28ab` 给所有 MariaDB 加 `--innodb-snapshot-isolation=OFF`（[记录](2026-11-23-snapshot-isolation-off.md)），线上还没部署（生产仍是隔离 ON）。

## 证据：是关隔离带出的（同一回归实验室、同一份数据，每轮 2 格）

| 源码 | 隔离 | 结果 |
|---|---|---|
| 线上版 `d8510e61a`（实验室按它的定义重建 db，默认 ON） | ON | 10/10 过 |
| main `f18fa1ec8` | OFF | 3/10 过（7 格 417 TimestampMismatchError） |
| train-1125 `59b87b82e` | OFF | 3/10 过 |
| main `f18fa1ec8` 代码 + 覆盖文件把 db 改回 ON | ON | 10/10 过 |
| 本分支（`conflicts.retried` 接住保存前比对） | OFF | 40/40 过（20 轮） |

11-20 以前各次全量这条都过，11-25 起不过，与 `4380c28ab` 进 main 吻合。

## 机制

`purchasing/output._attach_voucher` 在 `conflicts.retried` 里：`access.get_document(lock=True)` 先 `guard.require`（读了角色——普通读，建立读视图），
再 `select … for update`，再 `frappe.get_doc` 普通读。

- ON：那次加锁读碰到读视图之后被别人改过的行，报 1020 → `retried` 整笔回滚重来 → 过。
- OFF：加锁读通过；随后普通读仍读读视图里的旧副本 → `doc.save()` 里 Frappe `check_if_latest`（`get_doc(for_update=True)` 加锁读出最新修改时间）
  对不上 → `TimestampMismatchError`，原来不在 `retried` 范围 → 417 给用户。数据没丢（比对挡住了）。

## 修法

`transaction.conflicts.retried` 把 **Frappe 保存前比对抛的** `TimestampMismatchError`（抛出位置是锁定版 `frappe/model/document.py` 的 `check_if_latest`，
回归实验室镜像核过）与 1020 / 1213 同样处理：整笔回滚、退避重来、次数与提示清理相同，用尽抛 `Exhausted`。重来时是新事务、新读视图，读到最新单。
业务自己用 `TimestampMismatchError` 表示「记录变了、请刷新」（Cowork 推送的版本号核对、`history.storage.conflict`）不认、原样抛出——重来没用，入口靠它给原来的提示。
约定写进模块说明：被包的函数按名字自己读单据，不收外面读好的 `Document` 去 `save`（11-26 核过现有调用方都按名字读）。

离线 `tests/test_transaction_conflicts_stale_save.py`（去掉修法 2 项失败），进 `development.yml`。

## 试过又撤回的：加锁后改成加锁读（`get_doc(..., for_update=True)`）

同日先试过「全仓加锁之后的普通 `get_doc` 一律改成 `for_update=…`」（20 来处，外加一条静态检查），实验室最终头上反而更差，已整段撤回（`6ba30ca5e` / `67e5cf679` / `54d38b514` 的 revert）：

- `erp.purchase.voucher-concurrent` 8 轮 16 格全不过：变成 417 `LinkValidationError`。原因：加锁读读到了最新单（含别人刚挂上的附件行），但这笔事务的读视图早于锁，
  保存时链接校验（普通读）看不到别人刚建的 `File` → 报链接不存在。同一事务里「加锁读是最新值、普通读是旧视图」本身不一致，改读法只是把报错换个地方。
- 同单并发（下节）付款请求死锁更多：Frappe 16 的 `for_update` 连子表行一起加 next-key 锁，锁提前拿、拿得更多。
- 结论：关隔离之后，写事务要么**先加锁、后做任何普通读**（读视图从锁之后开始，普通读与锁一致），要么经 `retried` 在冲突时换新事务重来；不要在旧读视图里混用加锁读。
  写进 `docs/development/architecture.md`（transaction 段）。

## 另外发现的老问题（不是本次引入，单列）：同一张单并发写会死锁

新写的实验室流程（同一张采购单同时付款 / 作废 / 出凭证，4 张单 × 3 笔付款 + 2 张单各作废 + 付款 + 凭证）：线上版（ON）、main（OFF）、本分支，每版 3 轮 × 2 格**全部不过**，
都是付款请求 500 `QueryDeadlockError`。线上本来就有，与关隔离无关；第一次部署不会让它更差。修法要把采购 / 销售等写接口经「先结束读快照 + `retried`」的共用入口，
逐入口核对重来安全（幂等、提交前无外部 IO），属于改进项，另开分支做（流程文件留在 `~/pearnly-devtools/handoff-1126/erp_purchase_same_doc_concurrent.py`）。

## 并发下数据库 / 框架会抛的冲突，各自怎么处理

| 冲突 | 什么时候 | Frappe 抛成 | 处理 | 现在落在哪 |
|---|---|---|---|---|
| 1020 `ER_CHECKREAD` | 隔离 ON：读视图之后加锁读 / 改到别人改过的行 | `QueryDeadlockError` | 整笔回滚、退避重来（最多 4 次），用尽 `Exhausted` | `conflicts.retried`；OFF 后基本不再出现 |
| 1213 `ER_LOCK_DEADLOCK` | 两笔事务互相等锁（含子表 parent 索引的间隙锁） | `QueryDeadlockError` | 同上（InnoDB 已回滚整笔） | `conflicts.retried`；各入口原有的捕获照旧；没经 `retried` 的写入口直接报 500（同单并发老问题，见上节） |
| Frappe 保存前比对 `TimestampMismatchError`（抛出位置 `check_if_latest`） | 隔离 OFF：旧读视图里读出单据、别人提交后再保存（与 ON 时的 1020 同一种冲突） | `TimestampMismatchError` | **本次起**同 1020 | `conflicts.retried._stale_save`；没经 `retried` 的入口报 417（与 ON 时报 1020 一样是报错，没有变差） |
| 业务自己抛的 `TimestampMismatchError`（「记录变了、请刷新」） | 页面拿着旧版本号提交 | `TimestampMismatchError` | 如实报给用户 | 原样抛出，不重来 |
| 1205 等锁超时 | 等锁超过 `innodb_lock_wait_timeout` | `QueryTimeoutError` | 如实报给用户（已等满整个等锁时间） | 不重来；`assistant.sessions` 等入口按原有形状接住 |
| `LinkValidationError` | 旧读视图里看不到别人刚提交的被链接记录 | `LinkValidationError` | 不是冲突类型，不重来；靠「先锁后读 / 经 retried」不让它出现 | 撤回加锁读之后实验室 40/40 不再出现 |
| `DocumentLockedError` | Frappe 文档锁（`doc.lock()`，后台任务占着单据） | `DocumentLockedError` | 如实报给用户 | 产品代码没用 `doc.lock()` |
| 1062 重复键 | 两边同时插同一个唯一键 | `DuplicateEntryError` / `UniqueValidationError` | 各入口按业务处理（回到保存点改更新、视为已存在、报重复） | 各入口原有处理（`line_memory.learn`、`notices`、`push`、`companies.service`、`companion.devices` 等） |

## 经 `retried` 的入口（OFF 下撞上同一种冲突时，与 ON 时同样重来）

ERP 出凭证（`purchasing/output.voucher`）；CC 对话与执行记录（`assistant/sessions` 的 `_fresh_retried`、`_mark_after_send`、停止、标题写回，`toolrun`）；
CC 桌面版机器方法（`cc_desktop/machine._run` / `_guarded`、`api.register`、MR.ERP 登录）；Cowork 推送与 companion 接口（`push/application/api`、`card_save`、
`companion/*_api`、`submission`、`heartbeat`、`http_auth`、`assistant/tools` 交推送）。其余写入口不在 `retried` 内：并发冲突时 ON 报 1020、OFF 报 417 / 死锁 500，都是报错，
本次不变；统一接上重试见上节改进项。

## 验证

本机（分支 9425cb702，代码 = f7fd77bcf + 说明）：

- 全闸门 `gate.sh` 292 项过；候选离线（生产镜像 `fdf20d98`，在第一次部署合并头上跑）LANE EXIT 0；GitHub 9425cb702：architecture-and-offline / candidate / flows 全绿；逐提交公开扫描拦截 0。
- 离线 `tests/test_transaction_conflicts_stale_save.py` 5 项过；去掉修法 2 项失败。相关老测试（push conflicts、companion lock retry、assistant、cowork tools、devices、requests、operator tasks、snapshot isolation）全过。
- 回归实验室（隔离 OFF，回读 `--innodb-snapshot-isolation=OFF`）：
  - `erp.purchase.voucher-concurrent`：f7fd77bcf 20 轮 40/40 过；9425cb702 5 轮 10/10 过（main 同条件 3/10）。
  - 取号并发 conc30（30 张 LINE 卡片同一瞬间保存，11-23 关隔离要解决的原问题）：回归实验室 5 轮 + 本机另一套实验栈补跑到 20 轮，回归实验室 5 轮 + 自己的栈第 6–18 轮全部 confirmed 30、errors_3min 0（共 18 轮；补到 20 轮的最后几轮结果写在 ~/pearnly-devtools/handoff-1126/合入列车-train-1126.md）（每轮 confirmed 30、errors_3min 0）。主会话 11-26 定：回归实验室只跑 5 轮（每轮约 11 分钟、A 没碰取号路径），余下在自己的栈上补，不挡合入。
  - 条件批量改（并发删 12 个用户，User.on_trash 按条件改 ToDo 等）：3 轮，每轮 12/12、errors 0、清理后残留 0。
  - 两台电脑同时领同一件（`erp.writer-at-claim`，经机器通道 `retried`）：回归实验室 9 轮 9/9 过（每轮约 4 分钟，为让出实验室跑总合并头全量在第 10 轮停下；余下在自己的栈上补到 20 轮，结果写在交接）。

### 共用件影响清单（`check-impact.py --files …/transaction/conflicts.py`）

| 入口 | 结果 |
|---|---|
| `erp.purchase.voucher`（采购单补开收据 / 预扣税凭证，几张单同时点、同一张点两次） | 通过：实验室流程 erp.purchase.voucher-concurrent（见上）；第一次部署合并头清卷全量另跑 |
| `cc.assistant`（CC 对话页：新建、发消息、附件、切会话） | 通过：真机第 2 项（发消息、工具执行、改标题）；对话页流程在第一次部署合并头清卷全量里跑。**直写没测到**（测试环境没接付费识别、可用的待处理单卡在原有的存货科目组追问缺陷，见下节） |
| `shared.transaction`（CC 登记电脑、回执，并发时自动重试） | 通过：真机第 1 项（写账回执只写一次）、第 4 项（重新登记不多建）；实验室领单并发（见上） |
| `cowork.transaction`（Cowork CC 推送回执、心跳并发） | 通过：真机第 3 项（推送、按需请求、回读）；心跳并发没单独压测（经同一 `retried`，离线 test_cowork_companion_lock_retry 覆盖） |
| `cc.mrerp-login`（MR.ERP 连接登录授权） | **没测到**：授权弹窗要输 MR.ERP 测试账号密码，规则上不代输；用户回来后补测（步骤见真机第 6 项）。本分支只改重试范围，正常路径与 main 相同 |

代录停止 / 继续（真机第 5 项）**没测到**：一批两张约 70 秒写完，点停止时任务已结束；开始到完成、回执、结算通过。

## 真机（11-26 15:10–16:25，测试机1 + 测试环境，库存报表窗口执行）

环境：测试机1 WSL 测试环境挂本分支 9425cb702 源码，镜像为线上 `frappe@sha256:fdf20d98…`；testerp 与 testcowork 依次 `lab-start`，不同时开。
两套库的 `@@innodb_snapshot_isolation` 回读都是 **0（OFF）**，版本 11.8.9-MariaDB。测试机1 的 CC 用线上正式 1.12.24（以 skin3 普通权限运行）。
写账只写办公室服务器上的 69EXP\TEST 测试账套（测试公司 CO-140489「บริษัท Pearnly จำกัด」现用账套；不涉及第 15 条那 3 家真客户公司）：
写前整本快照（167 个文件），测后整本还原，MD5 回读差异 0。

| # | 入口 | 结果 | 证据 |
|---|---|---|---|
| 1 | ERP 网页提交 → CC 写账回执（erp.cc.write） | **通过** | testerp 网页手动录入采购单 PE2569-000041（PUR-2026-212656）并提交 → CC 领单写成 HP691126-001（写账 8.48 s），Pearnly CC Job 状态 success、只写一次（written_by_device = 测试机1）；69EXP\TEST 的 APTRN / GLJNL / GLJNLIT / ISVAT / STCRD 能读到单号；Error Log 0；CC 日志除心跳外没有 401 / 冲突 |
| 2 | ERP 版 CC 对话（erp.cc.chat-push、cc.assistant） | **部分通过**：发消息、工具执行、改标题通过；**直写没测到** | 同一段对话发了 4 句，11 次工具调用全部 done；改标题「隔离冒烟 直写」后回读 title_source = user；Error Log 0。直写没走到的原因：① 测试环境没接付费识别，对话里新上传的票不会被识别（pearnly_guide 返回 unknown_document）；② 已识别的待处理单 PE2569-000021 / 000019 在「存货科目组」上反复追问，属于原有缺陷，见下文；CO-140489 没有不涉及存货的 ERP 待处理单 |
| 3 | Cowork 推送 + 按需请求（cowork.cc-chat.push、cowork.cc.channel） | **通过** | testcowork 的 CC 对话把已识别的 AL-6906-11131227（清洁服务费 107.00）写进 9.บริษัท Pearnly จำกัด（69EXP\TEST）→ 单号 RR690615-001，Cowork Push Attempt outcome = success；按需请求 account_history 状态 done（2754 字节）；对话「回读」2026-06 的 Cowork Express Readback 状态 ready；69EXP\TEST 读回到 RR690615-001；Error Log 0 |
| 4 | CC 桌面版登记 / 连接（erp.cc.connect） | **通过** | CC 重启后 `cc_desktop.api.register` 返回 200；测试机1 在 Pearnly CC Device 里仍只有一条记录（PCD-41bd…，令牌尾号不变）；之后心跳、领单都正常 |
| 5 | 代录开始 / 停止（cc.operator.page） | **部分通过**：开始到完成、回执、结算通过；**停止 / 继续没测到** | ERP：代录 PE2569-000031 / 000029 → 写成 RR0000318 / RR0000319，machine.acknowledge 2 次（各 200），task_settle 1 次，任务 finished，Error Log 0。Cowork：代录 AL-6906-11121627 / 11121620 → 写成 HP0000160 / HP0000161，任务 finished，Error Log 0。停止 / 继续：两次都是一批两张约 70 秒写完，远程截图点「停止」时任务已经结束，服务端没有收到停止请求；两边都没有别的不涉及存货的已识别单可以再起一批 |
| 6 | MR.ERP 连接登录授权（cc.mrerp-login） | **没测到（需人工输密码）** | 授权弹窗要输 MR.ERP 专属测试账号的密码，规则上不能代输。界面上没有不用输密码的重新授权入口：Cowork「定时任务 → 新建」里的 MR.ERP 标签只有公司列表；`mrerp_login` 是 ERP 站点的机器通道。用户回来后补测（约 2 分钟）：先 `lab-start erp`（挂本分支）→ 测试机1 CC 切到 testerp 账号 → 定时任务（或设置 → 连接）→ 连接 MR.ERP → 弹窗输该测试账号的密码 →「连接」；之后由我们回读界面显示「已授权：测试账号 · 测试机1 · 时间」、Pearnly CC Device 的 mrerp_logins 连接号不变（现为 lsnr7mj310）、只有一条连接、frontend 日志里 `machine.mrerp_login` 返回 200、Error Log 0 |

收尾：testerp 15:3x–16:0x、testcowork 16:0x–16:2x 先后 `lab-stop`，三个域名回读都指回线上，两站定时任务 resumed / enabled；
69EXP\TEST 已整本还原（diff 0）；CC 在 16:22 以 skin3 普通权限重启，两个账号心跳正常。测试环境里建的对话、单据和代录任务只留在测试环境的库里，线上没有写入。

另外发现（**不是本分支带出来的**：本分支只改了 `conflicts.py`，过程中没有任何异常）：ERP 的待处理单 PE2569-000021 选「存货科目组」= วัตถุดิบ（DM）后，
ERP Express Choice 已经存下（kind = stock_group、subject = 账套 be4d2124…、value = DM），但下一次 `pearnly_precheck` 读回的 value 仍为空，`pearnly_write_plan` 报 `undecided_items`，CC 反复追问。直写、代录两种模式都一样。归因另查，结论记在共享笔记。
