# Express 开着账套时直写一个字节不动（2026-11-12）

分支 `pearnly/express-busy-probe`，基线 `32695c276`（开工时的 `origin/main`）。主会话 11-12 指定：从提速评估（`pearnly/express-write-speed-20761e` 分支的 `docs/work/2026-11-12-express-write-speed.md` §5.1）里单独拆出，先于提速处理。未合入，等主会话回复。

## 核实的结论

1. **无人值守的单以前确实不探占用**。探测只在托盘线程弹询问框之前（`companion/src/companion/gui_tray_workers.py:205`–`209`，条件里有 `not self._unattended(cfg, task)`）；共用写账入口 `worker._handle_dbf_task` 里没有探测，拿到 `PEARNLY.LCK` 就分派写入。无人值守 = 服务端给的 `attended` 为假：定时任务、自动推、录入人没在这台电脑登录过 CC（`apps/pearnly_core/pearnly_core/cc_desktop/wire/route_contracts.py:277`）。
2. **回滚是整表覆盖**。`dbf_writer._restore`（`companion/src/companion/dbf_writer.py:245`）把写前备份里的每个文件整份拷回账套。
3. **备份之后别人存的行会被抹掉——已复现**（合成账套，`companion/tests/test_express_open_no_touch.py::test_without_the_probe_the_rollback_wipes_what_express_saved_meanwhile`）：写前备份之后往 APTRN 追加一行（模拟 Express 里的人存了一张单），重建索引报占用，回滚后那一行不在了。
4. **线上是否已有这个风险：有**。已上线的无人值守写入（Cowork 定时任务 / 文件夹录入、自动推、录入人没在这台电脑登录过 CC 的单，ERP 同理）在 Express 开着这本账套时都会走到这一步。发生条件：Express 开着这本账套 **且** 在我们一轮「备份 → 回滚」之间（小账套几秒到十几秒，大账套经网络盘几分钟）有人在 Express 里保存了会动到同几张表（单头、分录、余额、流水号）的单。因为以前等账套是放回即领、连续重试，Express 开着的整段时间里这个窗口一轮接一轮地出现。
   - 没核实的一点：真机上 Express 持着文件句柄时，整文件覆盖是成功（抹掉别人的行）还是被系统拒绝（回滚不完整、留下半张单）——两种都是坏结果，本机（macOS）测不了，要测试机才能分清。
5. **一次写入超过任务租约（120 秒）**：不用修。领它的电脑还在线就续租约、不收回（`apps/pearnly_core/pearnly_core/cc_desktop/jobs.py:157` `_recover`，Cowork 同 `apps/pearnly_cowork/pearnly_cowork/companion/transport.py:87`）；不在线才转「结果不明」并钉回原来那台，那台再来领时按本机写入日记（`task_journal`）回上次单号、不写第二张；过期后的回执仍认原来那台（`jobs.py` `acknowledge` 的 `mine`）。

## 改了什么

| 提交 | 内容 |
|---|---|
| `485f2c281` | 复现测试（旧代码上 2 条红） |
| `3c164f8fa` | CC 桌面版：`worker._handle_dbf_task` 拿到 `PEARNLY.LCK` 之后、分派写入之前，一律 `account_lock.is_account_busy`；占用就回 `[ACCOUNT_BUSY_LOCKED]` + 等账套，不备份、不写、不重建、不回滚。采购、销售、收款、退货、库存单、调拨、作废全部经过这里；代录不经过（它要 Express 开着）；本机写过只差回执的单在更前面照旧直接回报 |
| `1981cbef9` | 服务端：等账套里「Express 开着」这一种，原因码 `ACCOUNT_BUSY_LOCKED` 留在记录上（`ack_rules.EXPRESS_OPEN`），这本账套歇 30 秒再派（`EXPRESS_OPEN_REST_SECONDS`），期间别的账套照常领。共用通道把「这之前不派」记在排队记录的租约到期栏（同一个时钟，不加字段）；Cowork 按记录的更新时间算。别的等待（别的电脑在写、账套够不到、文件号对不上）照旧不留原因、放回即领 |

没动的保护：`PEARNLY.LCK`、三重一致性闸、写前备份、读回、索引回查、任务日记、有人值守的询问框，全部原样。

重试间隔的取舍：固定 30 秒，不做递增。Express 关掉后最迟 30 秒被再次派出，加一次正常写入；Express 开着期间每本账套每 30 秒一次「领取 + 探测（约 0.1 秒）+ 回报」，不再搬表。有人值守时点「重试」后也可能要等到这 30 秒过完才写。

连带发现并一起解决的：以前同一台电脑连着领同一张等账套的单，排在它后面的**别的账套**的单领不到（排头按建立时间取、放回即领）；现在歇着的账套被跳过。

## 界面显示：只做到一处，其余没做

- **Cowork 的 CC 对话：已能显示**。推送记录上现在有原因码，现有路径（`assistant/tools.py` `_concluded` → `export_facts.reason_text`）会把它转成现成那句「Express 正在使用此账套，关闭后将自动推送」随「已排队」交给模型。没有新增文案。
- **Cowork 网页列表、ERP 网页、ERP 的 CC 对话：没做**，原因是不能照搬「把原因抄过去」：
  - Cowork 票上的 `last_push_reason` 在 CC 工具里被当成「失败 / 停下」的标志（`assistant/tools.py:595`、`:664`、`pending_api.py`、`operator_source.py` 等十来处），给「录入中」的票写上原因会让它们被列成失败。现在只有转人工才抄（`history/push_state.py:61`）。
  - ERP 单据上的 `ledger_reason` 在排队中被清空（`express/push.py:620`），读它的地方没逐个核过。
  - 正确做法是给「在等 Express 关闭」单独一个读得到的标志（或列表读的时候从推送记录 / 任务上取），不复用失败原因栏。这要逐个核读取方、改三处界面、过文案闸与产品地图，另起一步做，方案待主会话定。
- 收款分支（未合入）的 `collection/push.py:129` 照抄任务的原因：合入后收款记录会带上这个码，它的显示要在那边核一次。

## 验证

- CC 测试（本机 macOS，临时环境 pytest 8.3.3 + dbf 0.99.11）：新文件 4 条全过；全套 1595 过、74 跳过、9 失败——9 条都是本机没有 PySide6 的界面用例（`test_write_dispatch.py` 8 条、`test_mrerp_login.py` 1 条），改动前同样失败，**要等 Windows CI（companion.yml）确认**。
- `tests/test_cc_desktop_channel.py` 36 过（新增 2 条）、`tests/test_cowork_express_dispatch.py` 70 过（新增 1 条）、`tests/test_cowork_push_lifecycle.py` 过（新增 1 条）、`tests/test_companion_contract.py` 93 过。
- `scripts/run-offline-tests.py` 63 / 65：没过的 `test_cowork_vat_check.py`、`test_cowork_vat_void.py` 是本机没有 frappe，与本改动无关。
- `check-architecture.py` 通过；提交钩子的文案闸门 0 新增；`check-product-map.py`、`check-unbounded-queries.py` 通过。
- 服务端 3 条新测试与实现同一提交，没有单独先提交红的版本。
- **没做**：候选离线（要生产镜像）、实验栈流程、Development checks 与 companion.yml 的结果（推送后才有）、测试机真机。

### 共用件影响清单（`check-impact.py`）

命中 `cc-desktop`；要实测 14 个入口：`erp.cc.connect`、`erp.cc.write`、`erp.cc.masters`、`erp.cc.stock-void`、`cowork.cc.channel`、`cc.operator.page`、`erp.cc.companies`、`erp.cc.chat-push`、`cc.mrerp-login`、`erp.mrerp.direct`、`cc.desktop.bridge`、`cc.desktop.places`、`cc.desktop.write`、`erp.cc.stock-transfer`。
**全部「没测到」**：都要测试机或实验栈，测试机今天不能碰。合入前要补，至少：
1. 测试机开着 Express 的账套 → Cowork、ERP 各推一张无人值守的单：账套文件修改时间不变、托盘「正在等待 Express 释放账套…」、约 30 秒探一次；关掉 Express 后一分钟内写进去并读回一致。
2. 同上有人值守：弹框照旧，点「重试」后写入。
3. Express 没开：采购、销售、库存单、调拨、作废、收款各写一张，与以前一致。
4. 一本账套在等、另一本账套的单照常写。
5. 实机确认 Express 打开账套期间始终持着 `ISRUN.DBF`（探测的前提；代录 10-27 起已在用同一判断）。

## 限制与没解决的

- **探测之后 Express 才打开**的情况仍在：写入窗口内（现在几秒到几分钟）有人打开并保存，重建索引拿不到独占，回滚仍是整表覆盖。窗口从「Express 开着的全程」缩到「恰好在我们写的那一小段里打开」。彻底解决靠提速评估里的「回写期间独占 + 逐段比对 + 只回写改动」（第 1a 步）。
- 旧版 CC 桌面版不带这次的探测：服务端的 30 秒间隔对它同样生效（它回的也是 `[ACCOUNT_BUSY_LOCKED]`），空转和别的账套被挡住得到缓解，但每 30 秒仍会「备份 → 写 → 回滚」一轮，要升级到新版才根治。
- 要发 CC 桌面版新版；服务端先部署不影响旧版。

## 收尾状态

- 工作树 `.claude/worktrees/express-write-speed-20761e` 现在检出的是本分支；评估文档那一支 `pearnly/express-write-speed-20761e` 已在远端 `094f28e29`。
- 本机临时测试环境在会话临时目录，随会话清。

## 测试机真机实测清单（发版前必须全过；主会话排时间后执行）

只用测试套：`\\accserver\d$\ACCOUNT\70EXP\test`（或 `69EXP\TEST`），下文记作 `<套>`；真客户账套一律不碰。先登记测试机占用表；accserver 21:00 前；测试机上以 skin3 跑脚本用隐藏窗口。

**准备**
1. 构建：`gh workflow run companion-build.yml --ref pearnly/express-busy-probe`，跑完 `gh run download <运行编号>` 取 `CC-Setup.exe`（版本 1.12.21，产物留 1 天）。先**不装**，第 A 组用现在装着的旧版。
2. 服务端：`PEARNLY_TESTENV_SRC=<本分支工作树> PEARNLY_TESTENV_IMAGE=<线上 digest> bash scripts/testenv.sh lab-start cowork`（ERP 同样起一套），测试租户 testcowork / testerp；确认 `<套>` 在 CC 的可写账套里，且这家公司只对应这一本。
3. 无人值守的单怎么来：Cowork 定时任务 / 文件夹录入往 `<套>` 推一张（服务端给的 `attended` 为假）；CC 日志里这张单不出现询问框即是。准备 3 张小额采购票备用。
4. 哈希脚本（前后各跑一次，存两份对比）：
   ```powershell
   $d='\\accserver\d$\ACCOUNT\70EXP\test'
   Get-ChildItem $d -File | Where-Object { $_.Name -ne 'PEARNLY.LCK' } | Get-FileHash -Algorithm SHA256 | Select-Object Hash,@{n='Name';e={Split-Path $_.Path -Leaf}} | Sort-Object Name | Export-Csv C:\PearnlyStress\_open1112\hash_<标签>.csv -NoTypeInformation
   ```
   比较：`Compare-Object (Import-Csv hash_a.csv) (Import-Csv hash_b.csv) -Property Name,Hash`，没有输出 = 一个字节没变。

**A 组：旧行为定性（旧版 CC + 旧服务端或新服务端均可；只做一次）**
- A1 用服务器版 Express 打开 `<套>`，停在主界面。触发一张无人值守的单。看 `companion.log` 的 `[timing]`：应出现 `rolled_back`。
- A2 在 A1 那一轮进行中（日志出现 backup 之后），在 Express 里保存一张赊购单 X。等 CC 这一轮结束。
- A3 判定并如实记录三选一：①Express 里 X 还在、且数据完整；②X 不见了（整文件覆盖成功，抹掉了别人的单）；③CC 回滚报错 / 表里留下半张我们的单（覆盖被系统拒绝）。在 Express 里重新打开 X、跑一次应付明细表核对。记下 `companion.log` 对应片段。
- A 组做完，在 Express 里作废或保留 X 都行（测试套），记单号。

**B 组：新版（装 1.12.21，以 skin3 普通权限重启 CC；服务端为本分支）**
- B1 Express 开着 `<套>` → 跑哈希 a → 触发无人值守的单 → 等 2 分钟 → 跑哈希 b。**断言**：哈希无差异；`companion.log` 有「the account set is open in Express, nothing touched」、没有 `[timing]`；本机备份目录 `%LOCALAPPDATA%\Pearnly\write-backups` 没有新目录；托盘「正在等待 Express 释放账套…」；日志里这张单约每 30 秒出现一次。
- B2 B1 等待期间在 Express 里保存一张单 Y → 断言 Y 在、可重新打开；之后 CC 写成的那张也在。
- B3 关闭 Express → **断言**：60 秒内写进去（记从关闭到 `✓ dbf task` 的秒数），Express 重新打开能看到这张单，读回一致（回执 verify ok）。
- B4 另一本测试套同时有一张单排着：`<套>` 在等时它照常写进去（不被挡）。
- B5 有人值守：Express 开着 → 网页手动推一张 → 弹框照旧；关 Express 点「重试」→ 写入（记点击到写成的秒数，最长约 30 秒 + 写入时间）。
- B6 Express 没开：采购、销售、库存单、调拨、作废、收款各写一张，与以前一致（单号、读回、Express 里打开）。
- B7 「探测后 Express 才打开」：Express 关着触发一张单，日志出现 backup 后立刻用 Express 打开 `<套>`。如实记录结果（写成 / 等账套并回滚）与文件状态；这是已知未解决的窗口，不作为通过条件，只留证据。
- B8 探测前提：Express 打开 `<套>` 停在主界面、打开单据窗口、最小化三种状态下，各用 `is_account_busy` 等价的方式试一次（PowerShell：`[IO.File]::Open("$d\ISRUN.DBF",'Open','Read','None')` 应抛共享冲突）。任何一种状态能独占打开 = 探测不可靠，**不发版**并报主会话。
- B9 ERP 通道重复 B1、B3 各一次（testerp，往 `<套>` 推一张无人值守的单）。

**收尾**：`testenv.sh lab-stop`、flushdns、CC 以 skin3 普通权限重启、释放占用表；记录每张写进去的单号。结果逐条填「通过 / 不通过 / 没测到（原因）」写回本文件。

## 发版顺序与部署记录要写的

1. 合入 main 后先部署服务端（`scripts/production/deploy.sh`，先 `--dry-run`）。部署后旧版 CC 也按 30 秒间隔重试，别的账套不再被挡。
2. 紧接着从 main 发 CC 桌面版 1.12.21（`companion/README.md`「发布」：先 staging 再推标签 `companion-v1.12.21`）。1.12.20 已于 11-11 晚正式发布（main 6275e4999，服务端先、再 CC；主会话 11-12 更正），1.12.21 相对线上只多这次的改动和届时 main 上已合入的 companion 改动，发版前由主会话核 `companion/` 相对 6275e4999 的差异。
3. 部署记录写明：**旧版 CC 在升级前，Express 开着账套时每 30 秒仍会「备份 → 写 → 回滚」一轮**。发版后回读各租户电脑的 CC 版本（控制面 / 心跳里的 `companion_version`），低于 1.12.21 的逐台列出，直到清零。
4. 回退：服务端回上一个发布标记；CC 旧版仍可用（接口没变）。

## 给用户的风险说明（大白话）

现在线上，凡是「没人守在电脑前」的写入——定时任务、文件夹自动录入、自动推送，以及录单的人没在那台电脑上登录过 CC 的单——如果正赶上有人开着同一本账套的 Express，CC 会先备份、写入，写到最后一步才发现 Express 占着，于是把刚才备份的整张表盖回去。**如果这几秒到几分钟里，Express 里的人正好保存了一张单，这张单可能被一起盖掉（消失）**，而且以前 CC 会一轮接一轮地重试，Express 开着多久就重复多久。有人守着电脑手动推的单不受影响（会先弹框问）。修复上线前的避开办法：定时任务和自动推送排在没人用 Express 的时段（下班后），或者推送前先关掉这本账套的 Express；白天要推就在装 CC 的那台电脑上手动推。这是读代码并在模拟账套上复现出来的，真机上的实际表现要等测试机验证；有没有客户已经遇到过，我手上没有数据。
