# 事务所多台电脑各用 CC 桌面版写 Express · 定稿方案（2026-10-13，第二版：已按审查意见修订）

- 基线：`origin/main` 36487276，只读。没改仓库、没跑测试、没碰测试机和生产。复核时 `origin/main` 已到 7afdad0c：`S/companion/credentials.py` 第 240 行以后行号 +2（`_device_connection` 改为：装了 Express 的电脑不再退到 MR.ERP，本站没有 Express 连接就由 `register` 新建），`settings.tsx / i18n.ts / desktop.ts` 有小改动。本文行号仍按 36487276。
- 证据层级：本文只有设计。实现、本地测试、部署、外部业务结果、真机验收都还没有。`文件:行` 都在基线上逐处核过；标「待核实」的要真机或只读查生产才能定。审查意见逐条处理见附录。
- 来源：三份独立设计（`design-per-pc.md`、`design-per-person.md`、`design-capability-routing.md`）和五份勘察笔记（`map-*.md`），都在本目录；修订前的版本存为 `plan-r1-before-review.md`。
- 路径简写：`S/` = `apps/pearnly_cowork/pearnly_cowork/`，`C/` = `companion/src/companion/`，`F/` = `frontend/assistant/src/`。

## 结论

现在的 Express 连接保持一家所一条，把它当成一个「池」。每台装了 CC 桌面版的电脑（准确说是一台电脑上的一个 Windows 用户）是池里的一个成员，有自己的设备记录和密钥，自己上报够得到哪些账套、哪些文件夹，彼此不再顶替。账套按物理身份登记一次（目录文件号 → 网络路径 → 本机写法三层认定）：迁移来的键沿用今天存的路径字符串，所以推送记录、记住的选择、定时任务、回读、报税草稿都不用迁；新登记的、只在本机的账套，键写成带主机名的路径。登记后，规范键和规范根永远不变。准备推送时只冻结「哪份账套、谁录的」，不再冻结某把密钥。电脑来领任务时才决定由谁写，顺序是：这台电脑上登录 CC 的人就是录入人 → 常驻电脑 → 其他够得到的在线电脑。一份账套刚被某台写过、它的主档索引还没重建时，这份账套暂时只交给那台（按账套黏住）。下发给电脑的副本里换成这台电脑的本机路径。写者锁加在物理账套上，Express 不再锁整条连接，一张「结果不明」只挡它那一份账套。心跳不再改连接配置，池里也没有连接级的默认账套，两台电脑不会互相作废排队的单。新版桌面端去掉「改用这台电脑」，「断开连接」同时吊销服务端密钥。老客户一般不用重新设置：现有密钥原样有效，补丁只补设备记录和账套登记；例外是同一个配对码被手抄到两台电脑，第二台要在 CC 里登录一次（4.3）。这次发布不能直接回退（2.6）。MR.ERP 推送仍由服务器直连，连接名单不动；变化只有「读文件夹的电脑」改为（设备，成员连接）。全部改动在 Cowork 产品层，不新增域依赖方向。需要用户拍板 9 项（第 8 节）。

## 1 现状与缺口

### 1.1 今天处处按「一条连接 = 一把密钥 = 一台电脑」写

| 单机假设 | 代码 | 多台电脑时的后果 | 本方案 |
|---|---|---|---|
| 每条连接最多一把有效密钥：`active_key = sha256(站点, 连接)`，库里唯一 | `S/companion/contracts.py:70-71`；凭据 DocType json `:128-134`（unique）；`S/companion/credentials.py:102-106` | 第二台只能 `in_use` 或顶掉第一台 | 2.2 |
| 登录登记只取最早一条连接；有密钥就回 `in_use`，`replace` 吊销原密钥 | `credentials.py:239-250,253-279,213-224` | 换电脑整条让出 | 2.2、2.8 |
| 网页 / LINE / CC 每种 ERP 取第一条，但排序是 `modified DESC`，与登记的 `creation` 不一致 | `S/connections/service.py:181`；`S/public/desk/targets.js:37`；`S/line/flow.py:347-349` | 两条 Express 时网页和电脑可能各认一条 | 2.10 |
| 在线 = 连接行上的上报密钥 3 分钟内报过到 | `credentials.py:125-149` | 只有最后上报的那台算在线 | 2.3 |
| 心跳把所选账套和 6 个科目映射写进 `config_json`，一变就 `record_revision+1`、清快照头 | `S/connections/collector.py:57-64` | 两台默认账套不同，每分钟互改版本号 | 2.3 |
| 版本号进推送领取核对，变了就作废 | `S/push/native/service.py:368-370`；`S/connections/refresh.py:51-52` | 排队的单、目录刷新被成批作废 | 2.3 |
| 连接上的 `account_set / express_root` 被当成「默认账套」，选它不需要目录刷新证明 | `S/targets/selection.py:58-91`；`S/targets/choices.py:127-147`；`S/targets/freeze.py:18-35`；`S/connections/preparation.py:26-29` | 默认账套属于哪台电脑说不清；不在任何上报里的账套也显示「可写」 | 2.3 第 7 条 |
| 客户端把服务端回的 `account_set` 写回本机 | `C/worker.py:337-344` | 两台来回改对方的本机默认 | 2.3 |
| 上报缓存、上报凭据、`_preparation_binding` 是连接上的单槽，账套资料另存最多 10 份 | `collector.py:22,65-82`；`preparation.py:127-136` | 后报的覆盖先报的 | 2.3 |
| 目录刷新租约属主 = 密钥，别的密钥一来心跳就判失败 | `refresh.py:49-56` | 刷新永远完不成 | 2.7 |
| 准备证据冻结那把密钥 + 曼谷当天日期；确认、激活、领取、`_still_confirmable` 四处都整份重算比对 | `preparation.py:130-136,156-167`；`service.py:287,393-397,449,466,487` | 换电脑、跨午夜、电脑升级都把排队单转人工 | 2.5 |
| 证据里的 `credential` 另有 6 处在读 | `S/companion/transport.py:91-94`；`service.py:282-284,439-445`；`S/push/native/completion.py:23` → `S/connections/stock_effects.py:19-28`；`S/connections/service.py:559-565`；`S/push/application/service.py:56-58` → `S/folder_intake/destinations.py:28` | 不绑密钥后：未发出的单被作废、新建存货主档数不回写、推送记录丢存货科目组名 | 2.5 |
| 写者锁 = 每连接一把 endpoint + 一把「工作空间 + 数据根 + 账套路径」字符串 | `S/push/native/contracts.py:93-101`；注释自认没有物理证明 `service.py:196-197` | 同一份共享账套两种写法 = 两把锁，可能同时写；一张结果不明挡全所 | 2.6 |
| 领取按连接 FIFO 取 50 条，遇等锁直接 `break` | `transport.py:78-84,107-109` | 一份账套等锁，全所停 | 2.5 |
| 账套键 = 上报电脑的本机路径规整后的字符串；载荷写死准备那台的路径 | `S/targets/identity.py:16-18`；`S/push/express/preflight.py:305-314`；`C/worker.py:69-112` | 别的电脑认不出，或判「这台没有这份账套」转人工 | 2.4、2.5 |
| 定时任务读者存连接名；Express 任务只能由本连接那台读 | `S/folder_intake/rules.py:106-111,240-245`；`machine.py:73-80,142-143` | 各人电脑上的文件夹做不了 Express 任务 | 2.7 |
| 文件夹在场、按需请求在场与领取都按连接一份 | `S/folder_intake/agent.py:11-31`；`S/companion/request_service.py:206-253,282-310` | 互相覆盖；请求可能被不持有该根目录的电脑领走 | 2.7 |
| 选目标时 Express 必须「在线」才能选 | `targets.js:57-63`；`line/flow.py:353-368`；`S/assistant/tools.py:702-710`；`destinations.py:20-33,48-57` | 写入电脑关着，全所都选不了 Express | 2.5、2.10 |

### 1.2 多人事务所今天就卡住的硬伤和现存问题

- 只有 Cowork 管理员能登记电脑（`credentials.py:261`），机器每次认证还要求签发人有管理权限（`:334`）；403 一律显示「需要管理员账号」（`F/lib/desktop.ts:279`）。
- 连接使用名单 `allowed_users` 新建时只放创建人（`S/connections/service.py:73,115`），全仓没有任何入口改它（网页、CC 调 `configure` 都不传 `users`）。名单外的普通成员网页看不到这条连接、推不了。管理员在网页能看到全部连接（`service.py:133-134,186`），但不在名单里同样推不了（`S/connections/access.py:83-85`），也登记不了电脑（`credentials.py:92-99`）。
- 「改用这台电脑」不看对方在不在线（`F/components/settings.tsx:496-503`，`desktop.ts:263-266` 只留 `generation`），一点就吊销；原电脑已确认、还没写的单全部转人工（`transport.py:91-103`）。
- 没装 Express 的电脑登录、本站只有 Express 连接时也会拿到它的 `in_use`（`credentials.py:245-249`），点了会把写账电脑顶掉，自己又因没有数据根配对失败（`C/pairing_flow.py:61-62`）。
- 电脑上「断开连接」只删本机成员，服务端密钥仍然有效（`C/desktop_settings.py:208-211`）。
- 管理 RPC `issue / revoke / status`（`S/companion/api.py:26-39`）还在对外开放，全仓没有界面调用。`revoke` 和 `status` 取 `_active(doc) or _latest(doc)`（`credentials.py:292,305`），有了多台设备后会落到代数最高的那台，与调用方的意图无关。
- 现存的坏读法：回读用 `_known_version` 读 `config_json.companion_version`（`S/readback/service.py:106-112`），但这个键不在 `CONFIG_KEYS` 白名单里（`S/connections/model.py:9-14`），心跳也只往 `config_json` 写所选账套和 6 个科目（`collector.py:57-58`），所以它永远返回空。
- c47fbc07「多成员」只改了电脑一侧（`git show --stat c47fbc07` 只动 `companion/`）。

### 1.3 Express 写入本身的约束（决定锁怎么做）

- 桌面端写入不持有任何跨进程锁：单号取当日最大号 +1（`C/dbf_writer.py:132-148`），`ISRUN` 读改写（`:151-175`），失败时整表拷回（`:206-235`）；新建客户 / 供应商码扫全表 +1（`C/master_create.py:181-196`）。两台同时写一份账套 = 撞号或互相抹掉。
- `account_lock.is_account_busy` 打开就关、不持有（`C/account_lock.py:37-66`），而且探的是成员的默认账套，不是任务账套（`C/gui_tray_workers.py:90-96,134-142`）。
- 主档索引延到队列空时才重建（`C/cdx_reindex.py:141-171` `IndexBatch`）。这一步发生在回执之后、服务端锁已放开时；逐票重建正是「3 张间隔卡顿」的根因（`:142-144`）。
- 单台电脑内写入严格串行（`C/multi_profile_worker.py:1-9`）。

结论：跨电脑互斥只能靠服务端写者锁，锁键必须与路径写法无关；主档索引的可见性靠领取路由（2.6），不靠逐票重建。

### 1.4 可直接复用的

fence + attempt token（`service.py:479-511`）；被吊销的密钥只能回执自己那一件（`S/companion/receipt_auth.py:1-5`）；按需请求租约已绑密钥（`request_service.py:303,318-322`）；文件身份 `entry_key` 不含设备（`S/folder_intake/contracts.py:187-191`）；映射盘换 UNC 的 `share_path`（`C/folder_plan.py:143-183`，只认持久映射）；`waiting_lock` 回执把单放回排队、不消耗次数（`S/push/lifecycle.py:118-119`）；领取请求忽略多余的键（`S/companion/queue_contracts.py:85-91`）；心跳回复只加键的旧版兼容惯例（`S/companion/heartbeat.py:25-31`）；服务端不回 `account_sets_unchanged_ok` 就能让电脑下次整份上报（`heartbeat.py:27`、`C/worker.py:500-523`）。

## 2 方案

### 2.1 选型

骨架取 C 的「连接是池、设备是成员」结构，再嫁接两部分：B 的「领取时分派、证据只核用到的资料、待认定规则、问责记录」，A 的「写前核对文件号、迁移来的规范键 = 今天的键」。不选 A（每台电脑一条连接）的理由见 7.1。旧平台的 managed 共享代理不与本设计并存，先核一次旧源码再定（D9、7.3）。

### 2.2 连接模型与设备身份

| 实体 | 域 | 内容 |
|---|---|---|
| 池 = `Cowork ERP Connection` | connections | 每种 ERP 一条主连接；推送目标、记忆、`document_key`（`contracts.py:88-90`）照旧挂在它上面。Express 加布尔字段 `all_members`（全所可用，2.9） |
| 设备 `Cowork CC Desktop Device`（新，名字不带 Companion，免 en.csv 补行） | companion | `workspace`、`owner`（登记人 = 签发人，只管权限）、`signed_in_user`（这台电脑上最近登录 CC 的账号，只管路由）、`label`、`secret_digest`（sha256(站点, 设备密钥)，唯一）、`always_on`、`companion_version`、`max_payload_version`、`host_names`、`index_pending`、`retired_at/by` |
| 密钥 `Cowork Express Agent Credential`（改） | companion | 加 `device`。`active_identity(site, connection, device=None)`：迁移来的旧密钥**永远**保留原公式（旧槽位）；register v2 新发的密钥用 `fingerprint([site, connection, device])`。唯一约束不动 → 每个（连接，设备）最多一把；代数仍按连接递增（`credentials.py:109-112`） |
| 账套登记 `Cowork Express Account Set`（新） | connections | `connection`、`account_key`（规范键，连接内唯一，**登记后不改**）、`root_key`（规范根，**不改**）、`uid`、`location`、`content_key`、标签 / 公司 / 税号 / 分店 / 期间、`scope_device`、`last_writer_device`、`last_written_at` |
| 可达 `Cowork Express Reach`（新） | connections | `device`（不透明字符串）、`account_key`、本机路径 / 根 / SCCOMP 行号、`writable`、`state`（ok / pending_identity / stale）、`seen_at` |
| 账套资料 `Cowork Express Account Facts`（新） | connections | 按（连接，规范键）存科目、商品、客户、供应商、部门、存货科目组、目录指纹，记来源设备与 `seen_at`；每条连接保留 20 份，被未了结推送记录引用的不淘汰 |

- **设备密钥**：桌面端首次运行生成 32 字节随机数，与配对密钥一起用当前 Windows 用户的 DPAPI 加密（`C/token_protector.py:29-35`）。它只用来把同一台电脑的几把密钥归成一台、证明「同一台重连」；机器身份仍只来自 `exp_` 密钥（`S/companion/http_auth.py:1-6`）。现有 `companion_id` 只有 8 位十六进制、服务端不用（`C/config.py:62`，`S/companion/queue_contracts.py:85-91`），不复用。
- **认证**：按 `token_digest`（本就唯一）找到行，再核三件事：`row.connection == 密钥中段`、`active_key` 按本行的公式算、设备未退役。今天是按连接取唯一一把（`credentials.py:354-355`）。加锁顺序 Connection → Credential、每次重核签发人（`credentials.py:363-380`）都不变，只是失效按设备各自发生。
- **一台电脑多个 Windows 用户**（远程桌面服务器）= 多台设备：DPAPI 按用户隔离，单实例互斥量只在会话内唯一（`C/single_instance.py:52`）。同写一份账套由账套锁串行。
- **下线 `issue / revoke / status`**（`api.py:26-39`）：全仓没有界面调用，只有 `tests/test_cowork_companion_queue_http_contracts.py:130-131` 核它们只收 POST，测试同步改。内部的 `credentials.issue` 只留给 `register` 用。

**登记 `register` v2**（`S/companion/api.py:64-67` 加可选参数 `device_key`、`device_name`、`rekey`）：
- 权限改为 `require_actor()`（录入或管理）+ 池使用权。新发密钥要求管理员；D1 批准后，录入角色也能给自己的电脑发。建池仍只限管理员（`credentials.py:271` → `service.configure` 要求管理，`service.py:64`），非管理员遇到本站没有池时回 `no_connection`。
- 对话页在桌面端里**每次打开**都调一次（今天只在本机没有成员时调，`F/lib/desktop.ts:315`），用来记录 `signed_in_user`。

| 情况 | 结果 |
|---|---|
| 带设备密钥，新设备，本人有权发密钥 | 建设备（owner = 本人），在主连接上发密钥 → `created` |
| 带设备密钥，这台已有有效密钥 | `exists`：把 `signed_in_user` 记为本人，不改归属、不吊销（全所共用一台、各人用自己账号登录时就是这种） |
| 同上且带 `rekey`（本机已没有这家的成员，比如重装过），本人是 owner 或管理员 | 同一台重连：同一事务里吊销旧的、发新的 → `created`（已领的那件仍可回执） |
| 没装 Express | 有 MR.ERP 主连接就挂 MR.ERP；没有就挂 Express 池当只读文件夹成员（没有可达账套，永远领不到写入）。不再能顶掉写账电脑 |
| 不带设备密钥（≤1.5.0 原生端），池里还没有已认领的设备 | 走今天的旧槽位，`in_use` / `replace` 逐字不变（`tests/test_cowork_companion_register.py:73-82` 原样通过） |
| 不带设备密钥，池里已有已认领的设备 | 回 `update_needed`，对话页给「请更新 CC 桌面版」和下载地址；旧版因此不能再顶掉新版 |

**机器方法新表 `DEVICE_METHODS`**（自检 `http_auth.py:36-38` 扩到四张表互不重叠；`:287-289` MR.ERP 密钥的放行判断加上这张表，否则 MR.ERP 成员会被 403）：
- `device_hello {device_key, name, always_on, host_names}`：1.6.0 起每次启动、每个成员各发一次，把这把已认证的密钥挂到设备上。**不改 `active_key`**，服务端回退时旧版 `_active`（`credentials.py:102-106`）仍认得迁移来的密钥。同一台电脑的 Express 成员和 MR.ERP 成员带同一个设备密钥，合成一台（c47fbc07 的多成员）。如果这把密钥已被另一台设备认领（同一配对码抄到了两台电脑）→ 回 `claimed_elsewhere`，这台的对话页下次打开时自动走 register v2 拿自己的密钥（4.3）。认领成功后，服务端在下一次心跳回复里不带 `account_sets_unchanged_ok`，逼电脑整份重报，用来整份替换这台的可达行。
- `device_leave {}`：吊销这把密钥本身（「断开连接」用）。
- 桌面端遇到这两个方法返回 401 / 403 / 404，都按「旧服务端」处理，不当成配对失效（同 `C/request_client.py:41`、`C/folder_client.py:51`）。密钥被吊销仍由心跳发现。

### 2.3 能力上报（心跳与文件夹同步）

1.6.0 只加键。旧服务端只读认识的键（`S/connections/reports.py:487-596`）；新服务端要把两处白名单都扩上 `uid`、`share`：账套行清洗 `_ACCOUNT_SET_KEYS`（`reports.py:21-32,49-80`）和目录属性 `attribute_keys`（`:294-315`）。

- 每行账套加 `uid`（账套目录的卷序列号 + 文件号，只读打开取得，不写任何文件）和 `share`（网络路径：映射盘用 `WNetGetConnectionW` 换算，非持久映射也认；本机已共享的目录按 `LanmanServer\Shares` 换成 `\\本机名\共享名\…`）。
- 设备级加 `always_on`（补上 `docs/work/2026-10-12-cc-always-on.md:80` 列的未做项）和 `host_names`（本机名、DNS 名、IPv4，用来认出 `\\IP\…` 与 `\\主机名\…` 是同一处）。文件夹同步也带 `always_on`（MR.ERP 成员没有心跳）。

服务端（`S/connections/collector.py` 改写，设备编号由 companion 当数据传入）：

1. **心跳不再写 `config_json`**：所选账套和 6 个科目映射记在设备上，连接 `record_revision` 只在管理员改配置时变（`service.py:97`）。回复里的 `account_set` 回本设备自己上次报的值，`C/worker.py:337-344` 不会被别的电脑改。
2. **账套名单只在变化时写**（沿用 `account_sets_unchanged`，`reports.py:559`）：按 2.4 的规则认定，差异更新本设备的可达行。某台报了空名单只清它自己的可达行，不再把整个目录标 `ERR_ACCOUNT_SET_EMPTY`（`reports.py:567-571`）。
3. **池目录 = 各设备可达行的并集**：每份登记一行，属性取登记里的规范写法（规范路径、规范根、SCCOMP 行号），`writable` 取任一设备可写；作为 endpoint 观测发布（`collector.py:94-97`），上限仍是 5000（`S/targets/projection.py:34`）。`freeze_target` / `_validate_target` 的目标比对照旧（`service.py:208-219`）；`_dispatch_target` 里的准备证据核对按 2.5 拆成两种模式。
4. **账套资料按账套存**：`REPORTED_KEYS` 去掉两个版本号（`model.py:15-17`），其余进 Account Facts；版本号进设备行。今天版本号混在账套资料里（`preparation.py:72-74`），电脑一升级就让准备证据对不上。资料不设年龄上限（今天也只要求出自当前密钥、不看时间，`preparation.py:69-70`）；领取时按当时最新一份核本单用到的行（2.5），网页「重新读取」可随时刷新。
5. **在线**：设备的任一密钥 3 分钟内报过到（`ONLINE_SECONDS`，`credentials.py:122`；`record_seen` 本来就按密钥行记，`:152-156`），不再要求它是连接行上的上报凭据。
6. **读单槽的地方改读新位置**：`stock_effects.apply`（`stock_effects.py:12-40`，经 `completion.py:23`）、`preparation.reported_stock_groups / reported_masters`（`preparation.py:78-114`）、`preparation.collect`（`:117-161`）、`stock_groups_for_log`（`S/connections/service.py:550-567`）、`folder_intake/rules.py:131` 的电脑名。回读的 `_known_version`（`readback/service.py:106-112`）今天永远是空的，改读设备上报的 `companion_version`。
7. **池里没有连接级默认账套**：Express 的 `config_json` 冻结后，`account_set / account_dir / express_root / account_company / account_set_row` 不再当默认账套。`selection.py:58-91`、`choices.py:127-147`、`freeze.py:18-35`、`preparation.py:26-29` 对 Express 一律按当前合并目录核（`S/targets/evidence.py:107-114` 的 `_validate_current_snapshot`），选默认账套也要它在目录里、可写。6 个科目取目录行里按账套读 ISINFO 得到的 `mapping`（`C/account_probe.py:283-288`）；各台读同一份文件结果相同，所以不按设备存。代码层面中和这些旧值，数据不清（不升 `record_revision`）。

### 2.4 账套物理身份（纯函数，`S/targets/identity.py`，今天只依赖 `ntpath`）

设备 D 上报的一行 r 按以下顺序认定：

1. r 有 `uid`，登记里有同 `uid` 的 → 就是它。
2. r 有 `share`，登记里有同位置（主机别名归一）**且内容键相同**的 → 就是它，补上 `uid`。
3. D 以前用同一本机路径够到过某份登记 → 就是它，只更新可达行和 `uid` / `location`（升级、从备份恢复到原处）。
4. r 没有 `uid` 也没有位置（旧版桌面端）：规范键与 r 的路径相同的 → 就是它（今天的口径）；否则新建一份只属于 D 的登记，键就是它报的路径。
5. r 有 `uid` / 位置但没认上，而登记里有一份「还没报过 `uid` / 位置」、内容键又相同的 → **待认定**：可达行记 `pending_identity`，不派写入；等那份的上报电脑升级、补上 `uid` 后按第 1 条认。这样过渡期不会拆出第二个键。退出条件：那份登记的上报电脑被移除或退役，或 7 天没报到 → 解除，按第 6 条新建（与旧登记内容键相同，company 锁照样让两者串行）。界面上把那台旧电脑标「需更新」。旧电脑日后升级、报上同一文件号时归入新登记，旧键只留给历史引用；旧键下还在排队的单显示「没有能写的电脑」。
6. 其余 → 新登记。有 `share` 的：规范键 = 网络路径。只在本机的：规范键 = `\\主机名\盘符$\…`，规范根同样写法（仍是路径形状，各台唯一，文件夹名不变；主机名重复时用 `\\主机名-设备短号\…`）。不再用「撞键加后缀」：键在 `choices.py:106-124`（取上一级目录当根，`:111,129`）、`S/targets/catalog_groups.py:49-52`（按根分组）、`preflight.py:305-314`、`readback/service.py:157` 都被当成路径用。

- **不变量**：规范键和规范根登记后永不改。账套锁 = `digest(['account', 工作空间, 根, 键])`（`S/push/native/contracts.py:99-100`），改了它们，结果不明的旧单还占着旧锁，新单取的是新锁，同一份物理账套就不再互斥。位置变化只改可达行和 `location`。
- **内容键** = 税号 + 分店 + 期初；没有税号时用公司名小写 + 账套代码 + 期初；都没有就为空。字段已在目录属性里（`reports.py:294-315`）。它只用于第 5 条的辅助判断和 2.6 的兜底锁，**不单独用来合并**：备份副本的特征完全相同，合并就会写进副本。
- 不往客户账套目录写标记文件：那属于外部写入，要授权，而且 Express 备份会把它一起复制。
- 备份副本、各人本机的拷贝：文件号不同 → 不同登记，各自只派给够得到的电脑。

### 2.5 写账分派

**谁有资格写**（纯函数 `S/companion/route_contracts.py`，输入单 J：账套 S、录入人 X、`queued_at`、载荷版本）。设备 D 要同时满足：
- 未退役、密钥有效，签发人此刻通过 `_issuer`（`credentials.py:327-335`）；X 此刻仍能用这个池（领取时 `_authorize` 已按录入人重核，`service.py:131-135`）；
- D 对 S 的可达行 `state = ok` 且可写；
- D 的 `max_payload_version` ≥ 这张单的载荷版本。

**优先顺序**：① 这台电脑上登录 CC 的人就是录入人（`signed_in_user = X`；定时任务看负责人 `rule_owner`，`S/folder_intake/machine.py:88-96`）→ ② 常驻电脑 → ③ 其他够得到的在线电脑。后一档只在两种情况下才能领：前面各档都没有在线设备，或这张单的 `queued_at` 已超过 120 秒（与租约同长，`queue_contracts.py:15`）。路由按「这台电脑上登录 CC 的人」，不按登记人，所以场景①②不依赖 D1（D7）。
- **按账套黏住**：S 在 D 最近一次领取请求的 `index_pending` 里（D 写过 S 的主档、还没重建索引），而且 D 在线 → S 只交给 D。D 队列空时照旧在批末重建，下一次领取请求里不再列 S，S 回到正常路由。D 离线超过 120 秒视为解除。旧版不报这个键，按「`last_written_at` 在 120 秒内」近似。只有一台电脑时零变化。
- **同一份账套严格按排队先后**：D 看到 S 的排头不归自己、在等锁、或需要更高载荷版本，就跳过 S，去看别的账套（今天是整条连接 `break`，`transport.py:107-109`）。

**何时决定**：电脑来领任务时（拉模式，桌面端每 3 秒一次，`C/config.py:57`）。不加定时任务、不加常驻进程。准备时不选电脑，所以电脑换了、关了、退役了，排队单都不受影响。

**`queued_at`**（`Cowork Push Record` 新字段）：在 `prepared → pending` 时设置（`service.py:450-452` 确认、`:474` 激活），回执把单退回排队时也设置（`:642`，含 `waiting_lock` 和可重试失败）。`created_at` 是准备时间（`:255-265`），`updated_at` 每次保存都变（`:158-163`），两者都不能用。旧行为空时按 `created_at` 算。

**载荷版本**：准备阶段不再拦（去掉 `preflight.py:221-235,302-304`），改在领取时判断。排头要的版本池里有电脑支持但不在线 → 等；池里没有任何一台支持 → 这张单和这份账套显示「需更新」。同一份账套不跳过排头，因为同账套的先后顺序是正确性要求。

**`transport.lease` v2**（`transport.py:69-112`）：
1. 重核密钥 → 设备 D；D 还有一件没到期的租约就直接回空（每台同时最多一件，替代 endpoint 锁的串行作用）。
2. 候选 = **每份账套的排头**：在 D 可达的账套里，每个 `account_key` 取 `queued_at` 最早的一条 `pending`，按 `queued_at` 排，最多 50 份。不能简单取前 50 条：如果前 50 条都是 S1 而排头归另一台，D 就永远看不到后面自己的 S2（今天只有一个写者，`transport.py:78-84` 没这个问题）。另外先做一次不加锁的预查「D 可达账套有没有排队单」，没有就不锁连接行。
3. 逐份按资格、顺序、黏住、版本判断。不能领就**跳过，不隔离**。只有确定失效才隔离：录入人失权（`companion.queue_access_revoked`，`transport.py:100-103`），或 v1 证据绑定的那把密钥已吊销。
4. 账套锁被占 → 看下一份账套。
5. 领到后，在下发副本里按 D 的可达行替换位置字段 `account_root / account_dir / account_set / account_set_row`（同 `wire_account_set` 的口径，`preflight.py:306-314`；纯函数放 `S/push/express/`）。账本里冻结的载荷一字不动（`service.py:385-388` 的原则）。另加 `account_uid`（登记里的 `uid`）和 `attended`。

**桌面端 1.6.0 的配合**：
- 写前用本机目录文件号核 `account_uid`。对不上、或本机找不到这份账套 → 回 `waiting_lock`，错误码 `ERR_ACCOUNT_UID_MISMATCH`（回到排队，`C/worker.py:183-194`）；服务端据此把 D 对 S 的可达行标 `stale`，直到下次整份账套名单。不再走「这台没有这份账套 → 转人工」。旧版照旧按路径找：可达行来自它自己的上报，路径必然对得上。
- `attended=false`（替别人写、或常驻电脑）：不弹「账套被占用」，按「等账套」回报（同常驻模式，`C/gui_tray_workers.py:134-142`）。`attended=true` 时，占用探测改探**任务账套**目录；遇到占用先回 `waiting_lock` 放掉租约再提示，不再拿着租约等人点（多台时别的电脑会顺手把过期租约回收成结果不明，`transport.py:56-66`）。
- 每次领取请求带可选键 `index_pending`（`IndexBatch` 里还没重建的账套 `uid`），旧服务端会忽略（`queue_contracts.py:85-91`）。

**准备证据 v2**（`S/connections/preparation.py:117-167`）：

| 项 | 今天 | v2 |
|---|---|---|
| 写者 | `credential = {credential_id, generation, issuer}` | `route = {connection, account_key}`，不绑电脑 |
| 主档 | 准备那台电脑当时的缓存，含两个版本号 | Account Facts 里这份账套的最新一份，不含版本号 |
| 银行流水窗口 | 按电脑签发人取（`:146-147`） | 改按录入人取；今天表里没有数据（`bank_evidence.record` 全仓没有调用方），不作为决策 |
| 确认、激活、`_still_confirmable` | 整份相等，含日期 | 不变（人确认的就是当天这份，`service.py:276-293`） |
| 领取 | 整份重算比对（含日期、密钥、版本） | 只核 `route` 仍然成立 + `basis_hash`：服务端事实（映射、供应商画像、银行窗口），加上主档里**本单载荷实际引用的行**（科目、对方档、商品、部门、存货科目组）。用到的变了照旧转人工 |

- `preparation.revalidate`（`preparation.py:164-167`）拆成 `mode='confirm' | 'lease'`。`_dispatch_target`（`service.py:385-411`）在确认（`:449`）、激活（`:466`）、`_still_confirmable`（`:287`）三处用 confirm，在领取（`:487`）用 lease。「本单引用的行」写成纯函数，放在载荷构建旁边，单测钉住。保护没有取消，只是收窄到与这张单有关的部分。
- 读 `credential` 的 6 处一起改，各配测试：
  - `transport.py:91-94`：v2 单不再比密钥。
  - `service.py:282-284` `_still_confirmable`：改判 `has_writer`。不改的话，v2 单永远判为「不能确认」，任何一次重新准备都会经 `_supersede_pristine`（`:295-319`）把它作废。
  - `service.py:439-445` `confirm`：见下一条。
  - `completion.py:23` → `stock_effects.py:19-28`：改按（连接，账套）在 Account Facts 上累加 `stock_master_count`。不改的话判 `target_changed`，新建存货主档数不回写。
  - `connections/service.py:559-565` `stock_groups_for_log`：改读 Facts。不改的话推送记录里的存货科目组名会消失。
  - `push/application/service.py:58` `has_reported_credential` → `has_writer`，读方 `folder_intake/destinations.py:28`。
- **确认不再要密钥**（`service.py:439-445`）：Express 改核三件事：录入人有权限、登记里有 S、至少一台设备对 S 可达（不要求在线）；一台都没有 → `push.no_writer`。`S/companion/submission.py:14`、`destinations.py:30-33` 不再调 `for_confirmation`。MR.ERP 不变。
- **电脑都不在线时**（D4）：仍可选 Express、确认、排队；有能写的电脑开机就写。状态从路由即时算出，不另存状态表；推送状态的唯一来源仍是 `Cowork Push Record`。

**问责**：写入尝试（`service.py:499-503`）加 `device`、`device_owner`、`route`（own / always_on / other / sticky）。推送记录的 `actor` 仍是录入人。Express 里 `USERID` / `CREBY` 仍写固定值，本次不改。

### 2.6 账套锁

**锁口径 v2**（纯函数 `S/push/native/contracts.py:93-101`），Express 新记录：

| 锁 | 键 | 作用 |
|---|---|---|
| account | 公式不变：`digest(['account', 工作空间, 规范根, 规范键])` | 规范键与写法无关，于是跨电脑同一份账套只有一把锁；与部署前在途的旧记录天然互斥（同一公式、同一字符串） |
| company | `digest(['company', 工作空间, 内容键])`（内容键为空不加） | 认定万一拆成两份时兜底；同公司同期的副本也串行，只会变慢，不会写坏 |

- **Express 不再取 endpoint 锁**（D6）。这属于 AGENTS.md「外部写者锁迁移期间不关闭」的范围，登记为有意改动：写者锁仍在，只是粒度从整条连接收窄到物理账套。每台同时只领一件由 2.5 第 1 步保证（桌面端本就单线程）。一张结果不明保留 account + company，只挡这份账套和同公司同期的副本。
- `_locks` 的字段映射（`service.py:198-201`）加 `company`：`workspace` = 工作空间，其余为空。`lock_kind` 选项加 `company`（DocType 今天只有 `endpoint\naccount`）。
- **口径随记录冻结**：冻结目标里写 `lock_scheme: 2`，`writer_identities` 按记录自己的口径算。原因是领取、回执、过期回收、隔离、人工核对每一步都从冻结目标重算锁（`service.py:232,425,467,488,542,620,663,691,721`），口径一改，旧记录就放不掉锁。没有 `lock_scheme` 的旧记录永远按旧口径算。
- **不能直接回退**：旧版 `ack` 的 `_release`（`service.py:578-583,638`）和 `recover_expired`（`:669-671`）要求按旧口径算出的 endpoint 锁也归这条记录，v2 记录不持有 endpoint 锁 → 锁冲突，回执和过期回收都会失败。要回退，必须先把 v2 的「领取中 / 结果不明」清零（人工核对放锁）。
- **主档索引**：按 2.5 的「按账套黏住」处理，不逐票重建。别的电脑接手前，前一台已在批末重建；前一台离线没重建的，按今天批末失败时的兜底处理：下一批或夜间 PACK 补上（`C/cdx_reindex.py:157-159`）。待真机核。
- **MR.ERP 不变**：endpoint + account，owner `server:mrerp`（`S/push/mrerp/runner.py:40,96-98`）。
- **不变的保护**：fence + attempt token 只存摘要、120 秒租约、过期转结果不明永不重试、迟到回执、人工核对放锁、回执权与派发权分开（`service.py:479-511,609-676,706-743`；`receipt_auth.py:1-5`）；`document_key` 查重（同号不同票照常推，`service.py:245-246`）。
- 本机探测（`account_lock`、REINDEX 独占失败回滚）仍是第二道防线，不当互斥用。

### 2.7 定时任务、按需请求、回读、目录刷新

**定时任务**
- 读者 =（设备，成员连接，根目录编号）。规则加 `reader_device`，原 `reader` 改存成员连接。补丁按读者连接当前有效密钥所在的设备回填（先例 `S/patches/v0_0_14_folder_task_reader.py`）。只按设备下发不够：同一台电脑上的 Express 成员和 MR.ERP 成员各自同步文件夹，同一个文件夹在两个成员下都授权过时，任务会被处理两遍。
- 机器同步按 `reader_device == 本设备 且 reader == 本成员连接` 下发（`machine.py:142-143`）；`_rule`（`machine.py:73-80`）同时核设备和连接；文件状态核对除连接外也核读者设备（`machine.py:161`）。旧版不用改：设备由密钥推出。
- Express 任务的读者可以是池里任一台设备，读与写分开：写入按 2.5 分派，以负责人为录入人。`_readers` 对 Express 只返回自己（`rules.py:110-111`），改为列出池内设备。
- 文件夹本机情况按（设备，成员连接）存（`agent.py:11-31`）。任务绑（设备，成员连接，`root_id`）；根目录编号仍是本机路径摘要（`C/folder_plan.py:54-56`），授权仍只在电脑上做。跨电脑重叠仍按网络路径比（`rules.py:381-410`），网络路径换算改用 2.3 的同一办法。
- 默认读者：在哪台电脑的 CC 里建任务就是哪台。网页从「电脑 · 文件夹」下拉选（下拉已是多读者形状，`F/components/scheduled-tasks.tsx:629-637`），值改为「设备|连接|根目录|子文件夹」。
- 「读者 = 连接」的其他调用方一起改：CC 工具的 `reader`（`S/assistant/tools.py:1822,1889-1910,1991-2077,2416-2505,2622,2917-2958`，改为不透明的「设备|连接」编号）、`S/folder_intake/fetch.py:31-38`。
- 不自动换电脑读：本机台账只在原电脑上（`C/folder_store.py:125-145`），换读者会把拍底前的旧文件当新文件。读者离线照旧提醒；读者被移除，任务显示「电脑已移除」，由负责人重选。
- 每台读者最多 50 个任务（`rules.py:28,252`）改按设备计；推送限流改按推送连接 + 账套计（今天按读者连接，`pump.py:359-366`）；提醒、简报、电脑通知按读者设备区分（`machine.py:169-172`）。
- Express 文件夹票的「在线」改用池的判据（`destinations.py:48-57`）；D4 批准后不在线也确认排队，不再 10 分钟重试、6 小时记失败（`S/folder_intake/push.py:106-108`）。

**按需请求、回读**
- 请求加 `target_device`、`account_key`。列文件夹、取文件定向给持有该根目录的设备。回读不定向，按 2.5 的资格和顺序、以发起人为录入人分派；领取时把参数里的账套位置换成领取设备的写法（桌面端只认本机数据根或授权根，`C/express_read.py:131`）。
- companion 不能 import readback（`docs/development/architecture-policy.json:177` 起，companion 只能依赖 connections / push / targets）。位置替换只认 `request_contracts` 定义的通用位置键：`account_set` / `account_root`（就是回读参数的键，`S/readback/contracts.py:96-102`）加 `account_key`。
- `lease_next` 只扫本设备能领的（今天每次 FOR UPDATE 扫整条连接，`request_service.py:262-269,288-294`）；在场按设备记（`:206-253`）；`waiting_for_cc` 改判「有没有能做这件事的设备来过」，版本取设备行（1.2 的坏读法一并修掉）。最低版本 1.5.0 不变（`request_contracts.py:51`）。回读查重键不变（`readback/service.py:68-69`）。
- 取文件的权限仍是录入权限 + 连接使用授权（`fetch.py:31-38`）。

**目录刷新**（`refresh.py`）
- 账套级（`account_set`）：只租给一台够得到这份账套的设备，顺序同 2.5（发起人自己的电脑 → 常驻 → 其他）。这台交回即完成。
- 整目录（`@endpoint`，`refresh.py:120-125`）：按请求时在线的设备各租一份子租约，每台交回自己的整份名单就更新它的可达行，并重发并集目录。全部交回、或租约到期（900 秒，`:14`），即算完成；到期没交回的设备不算失败，它那部分保持旧数据。
- 刷新行加 `lease_device`；别的设备来心跳不再判失败，只有同一台设备换了密钥才判 `ERR_AGENT_CREDENTIAL_CHANGED`（`refresh.py:53-56`）。连接版本不再随心跳变，`ERR_CONNECTION_CHANGED` 只在管理员改配置时出现。

### 2.8 「改用这台电脑」与「断开连接」

- 1.6.0 起没有「改用这台电脑」：第二台登录就是加入池；同一台重连是静默轮换本机密钥（2.2）。旧原生端只在全是旧槽位的池里保留旧语义，池里一有已认领的设备就回 `update_needed`，所以不会出现旧版吊销新版。
- 「断开连接」= 先 `device_leave` 吊销本机这把密钥，再删本机成员（今天只删本机，`C/desktop_settings.py:208-211`）。旧服务端回 401 / 403 / 404 时退回只删本机。
- 「移除」离线电脑：电脑的 owner 或管理员在桌面端「电脑」列表的 ⋯ 里操作，吊销它全部密钥、标退役。正在写的那件仍能回执；排队单本来就没绑它，下次领取自动由别的电脑写，**不转人工**。网页仍只读（`docs/work/2026-10-10-assistant-foundation.md:189`）。

### 2.9 权限

- **登记电脑（D1）**：要改四处：`register` 的 `require_actor(management=True)`（`credentials.py:261`）；`issue` 的 `_management(use=True)`（`:208`）；`_issuer` 每次认证要求签发人有管理权限（`:334`，不改的话，录入角色签发的密钥第一次心跳就失效）；建池仍只限管理员（`service.py:64`）。改后：录入角色能给**自己的**电脑发密钥，签发人 = 本人。机器执行身份 = 签发人（`http_auth.py:295`），每次请求按 `require_actor`（录入或管理，`access.py:52-63`）+ 池使用权重核，失权即失效，这一点不变。这是迁移保护「身份、授权」里的有意改动，在 D1 登记。
- **池使用名单（D2，只针对 Express）**：Express 连接加布尔字段 `all_members`，不往 `allowed_users` 里放 `*`（`configure` 没传 `users` 时沿用原名单，`service.py:73`，并逐个查用户，`:82-83`，查不到 `*` 就拒绝；网页 MR.ERP 弹窗 `targets.js:87-93` 和 register 自动建池 `credentials.py:271` 都走这里）。判定集中成 connections 的一个函数，替换三处：`access.py:84`、`service.py:186`（`records`）、`:524`（`read_for_log`，看推送记录的人仍要有推送记录角色）。新建 Express 池默认 `all_members = 1`；补丁把现有 Express 主连接设为 1，直接写字段、不升 `record_revision`（放宽名单不该作废任何排队单）。MR.ERP 连接名单本次不动。
- 写入名单按人按账套：本次不做（2026-09-26「不做公司隔离」，`access.py:75`；没有实测需求支撑）。
- 不改：迁移保护（密钥只存摘要与末四位、加锁顺序、回执与派发分开、fence、写者锁、请求回执幂等）；`binding_generation` 仍只能为 0（`credentials.py:87`）。

### 2.10 统一入口，消掉零散判据

- `companion.pool.state(connection, account_key=None)` → `{online, writers_known, needs_update}`，替换 `credentials.online(connection)` 的全部调用：`S/companion/api.py:42-47`、`S/line/flow.py:353-368`、`destinations.py:20-57`、`S/assistant/tools.py:52-66,702-710`（经 `destinations`）、`rules.py:160`。
- `companion.api.online` 的形状保持兼容：`online` 仍表示「池里有能写的电脑在线」，另加 `writers_known`、`needs_update`。调用方 `F/components/scheduled-tasks.tsx:101,1065`、`targets.js:41,57-63,131` 改用 `writers_known` 判断能不能选（D4），旧缓存的页面读 `online` 也不出错。
- 「每种 ERP 的主连接」统一由 connections 的一个函数解析（最早建的、启用的、client 为空），登记、`records` 的使用方、Desk、LINE、CC 都用它。
- 落点与依赖：设备、密钥、领取派发、`pool.state` 在 companion；登记、可达、账套资料、证据 v2、刷新改派在 connections（设备编号只当不透明字符串，不反向依赖 companion，`refresh.py:1-5` 的约定）；锁口径在 push；身份、路由、位置替换是纯函数，不导入框架。对照 `architecture-policy.json:177` 起的 `cowork_domain_dependencies`：companion→connections/push/targets、push→connections/targets、folder_intake/line/readback→companion 都已登记，assistant 经 folder_intake，**不改 policy**。全部在 `pearnly_cowork`，不进 `pearnly_core`（共用层的 `pearnly_core.endpoints` 是另一套）。
- 新写 ADR「CC 桌面版多台电脑」：`docs/adr` 下目前没有配对或桌面版的 ADR。

## 3 六个场景

**① 全所共用一台电脑（照旧工作）**
迁移后它是池里唯一的设备，所有账套只有它够得到，路由永远选它。别人在自己账号下登录这台的 CC → `exists`，记为当前登录人，不改归属。差别只有三点：它关机时仍能选 Express 排队（D4）；跨午夜、电脑升级不再把排队单转人工；一张结果不明只挡那份账套（D6）。零设置。

**② 各用自己的电脑，数据在服务器共享、映射成盘符（泰国最常见）**
每人在自己电脑上登录 CC 就加入池。A 映射 `S:`，B 映射 `Z:`，有人用非持久映射或 `\\192.168.1.5\…`：同一目录文件号相同 → 同一份登记；拿不到文件号时按网络路径 + 主机别名 + 内容键认。A 录的单由「当前登录 A 的那台」写，B 的同理，这不依赖 D1（管理员先在每台登记一次也行）。不同账套并行；同一份账套由 account 锁串行，主档刚被改过的账套按账套黏住，不逐票重建索引。A 下班关机后补录的单：先找常驻电脑，没有就 2 分钟后由在线的同事电脑代写，不弹框（D3）。定时任务各绑各的电脑。

**③ 各用自己的电脑，数据在本机**
各台文件号不同 → 各是各的登记。各台 Express 默认路径即使完全相同也不会撞：本机账套的键带主机名（`\\ACC-01\C$\…`）。网页目录是并集，同名的账套标签后加电脑名。A 本机的账套只派给 A 的电脑；A 关机时，这些单排队「等电脑上线」（D4），B 录的 A 那边客户的单也一样。同一家公司在两台本机各有一份拷贝：两份登记、内容键相同 → company 锁让它们串行，不会写错份。

**④ 混合 + 一台常开电脑跑夜间任务**
常开电脑开常驻模式，心跳报 `always_on`。数据放在它本机 `D:` 并共享：它报本机路径 + 共享表换算出的 `\\主机\共享`，别人报映射盘；文件号相同（本机 NTFS 与经 SMB 看到的是否一致，待真机核实），否则按位置 + 内容键认。白天各写各的；晚上个人电脑都关了，共享账套的单全由常开电脑写。夜间定时任务的文件夹选常开电脑上的；读个人电脑文件夹的任务晚上不跑，照旧提醒。只在某人本机的账套，等他开机再写。

**⑤ 只用网页 / 手机的人**
他没有电脑，跳过第 ① 档：常驻电脑 → 其他够得到的在线电脑；都不在线就排队。前提是他能用这个池（D2）。每张单记录谁录的、哪台电脑写的。LINE 和 CC 不再因「电脑不在线」拒绝选 Express（D4）。回读也按这个顺序找电脑。

**⑥ MR.ERP 连接**
推送仍是服务器直连，owner `server:mrerp`，锁口径不变，确认不带凭据（`S/push/mrerp/runner.py:40,96-98`；`destinations.py:36-42`；`service.py:446-448`）。连接名单不动（D2 只针对 Express）。只读文件夹的成员照旧只能调文件夹和按需请求，另放行 `DEVICE_METHODS`（`http_auth.py:285-289`），这样同一台电脑上的 Express 成员和 MR.ERP 成员能合成一台，只有 MR.ERP 的电脑也能「断开连接」。变化只在读者：从连接改为（设备，成员连接）（今天已能借任一台 Express 电脑读，`rules.py:112-120`）。只用 MR.ERP 的事务所不会冒出 Express 池。

## 4 老客户迁移

### 4.1 补丁 `v0_0_18_companion_devices`（原生补丁、幂等；机械搬移与行为变化分开记录）

1. 每把有效密钥建一台「旧电脑」设备：owner = 签发人；名字先取 `report_json.agent_device_name`（`reports.py:501-504`），再取文件夹同步报的名字（`agent.py:22-24`），都没有就用连接名。`secret_digest` 留空，等首次 `device_hello` 认领。密钥行补上 `device`，**`active_key` 永远不改**（旧槽位，现有密钥原样有效，回退也认）。
2. 账套登记：从当前 endpoint 快照和 `report_json.reported_account_sets` 播种，**规范键 = 今天的 `source_id`，规范根 = 今天的根**；同时建旧电脑的可达行。未了结的推送记录、启用的定时任务引用到、但目录里已经没有的键，也补成登记（没有可达行，保证引用不悬空）。
3. 账套资料：默认账套那份和 `_account_caches` 按账套搬进 Account Facts。
4. 定时任务回填 `reader_device`，`reader` 保持原连接；未了结的按需请求 `target_device = ''`。
5. D2 批准时，Express 主连接 `all_members = 1`，不升版本。MR.ERP 不动。
6. **不动**：`config_json`（Express 的默认账套由代码中和，2.3 第 7 条）、`record_revision`、快照、推送记录的各种键、Express Choice（键 = 连接 + 路径，`S/push/choices.py:38-47`）、存货科目组 / POS 版式 / 回读（`S/connections/service.py:278-299`、`S/pos_import/service.py:33-46`、`S/push/application/service.py:537-581`）、对账预设 / 预扣税 / 供应商画像（按工作空间存，与电脑无关）。Redis 里的在场与文件夹情况不迁，下一次领取（3 秒）/ 同步（60 秒）就补上。

### 4.2 旧桌面版兼容

- ≤1.5.0 不报设备密钥：补丁建的旧电脑代表它。心跳没有 `uid` / 位置 → 按路径认账套（2.4 第 4 条，就是今天的口径）。服务端在下发副本里填它自己的路径，它照常写 v2 单。回复多出的键、任务里多出的 `attended` / `account_uid` 都会被忽略：客户端只读 `log_id`、`invoice_no`、`payload` 并按键取值（`C/queue_client.py:159-177`），`C/` 下没有按键白名单校验载荷。
- 旧版只缺三件事：不弹框的代写（照旧弹框）、写前文件号核对、`index_pending`（按 120 秒近似）。它随心跳拿到 `companion_latest` 自动更新（`heartbeat.py:28-31`）。安装包不签名，自动更新可能被拦；所以 2.4 第 5 条给了 7 天的退出条件，界面上标「需更新」。

### 4.3 旧配对码与多成员

- 手抄的旧配对码就是 `exp_` 密钥，形状不变（`contracts.py:88-95`），照常有效；`legacy_wire` 旧地址保留。
- 多成员（一台电脑 Express + MR.ERP 两把密钥）：补丁先建两台旧电脑；升级后两个成员 `device_hello` 带同一设备密钥，自动合成一台。只是改挂密钥，没有业务数据要搬。
- **同一配对码抄到两台电脑**：补丁只会为这把密钥建一台旧电脑，两台的本机路径会混进同一台的可达行。`device_hello` 先到的那台认领；另一台收到 `claimed_elsewhere`，在它的 CC 对话页下次打开时自动走 register v2 拿自己的密钥（需要有人在那台电脑上登录 CC，**这种情况不是零重配**）。认领后整份重报，替换混进来的可达行（2.2）。开工前只读查生产：同一把密钥上报的电脑名是否来回变（7.3）。

### 4.4 发布顺序与在途单

不能「先停新推送入口，等旧单写完」：发布脚本第一步就进维护并停掉 web（`scripts/production/activate-product.sh:26-30`），维护期间电脑领不了任务；唯一能停推送的开关 `pearnly_cowork_erp_push_enabled` 连领取一起停（`service.py:362-363`），而且功能不做租户开关。也不保留 v1 证据的核对分支：要重算 v1 证据，就得保留单槽上报，与本方案相反。做法：

1. 部署前（服务照常运行）只读统计 v1 单：已准备 / 排队中 / 领取中 / 结果不明。排队中 + 领取中为 0 时再进维护部署（现在没有真实客户，等几分钟即可）。统计之后到进维护之前新确认的单，部署后第一次领取时按 `push.native_target_changed` 转人工，交付时报实际数量。
2. 已准备的 v1 单，下次点推送时由 `_supersede_pristine` 换成新的准备（`service.py:295-319`）。结果不明的 v1 单仍占 v1 锁（endpoint + account），由人工核对放锁（`resolve_unknown` 只放自己持有的锁，`service.py:726`）；v2 单取同一把 account 锁，同一份账套照样互斥。
3. 部署后回读：设备数、登记数、`config_json` 与 `record_revision` 未变、v1 排队中 / 领取中为 0。本次发布不可回退（2.6）；万一要回退，先把 v2 的领取中 / 结果不明清零。
4. 同一晚发 1.6.0 正式版，桌面端随心跳更新。先服务端后桌面端（用户既定规矩）。

## 5 接口约定与版本兼容

| 接口 | 变化 | 旧桌面版 + 新服务端 | 新桌面版 + 旧服务端 |
|---|---|---|---|
| `companion.api.register` | 加 `device_key`、`device_name`、`rekey`；回复加 `device`；新状态 `exists`、`update_needed` | 不带设备密钥 → 旧槽位逐字不变，或 `update_needed` | 对话页随服务端发布，不会出现新参数；Frappe 16.33.0 的 `frappe.call` 本就丢弃签名外的参数 |
| `companion.api.issue / revoke / status` | 下线 | 不调用 | — |
| `DEVICE_METHODS`（新表） | `device_hello`、`device_leave`；MR.ERP 密钥放行 | 不调用 | 带 `Bearer exp_…` 的未知路径一律按机器请求处理，在信封校验处拒绝（`http_auth.py:47-51,58`）→ **401**；桌面端把 401 / 403 / 404 都当旧服务端；断开退回只删本机 |
| 心跳 | 加 `always_on`、`host_names`；账套行加 `uid`、`share`；服务端两处白名单扩键 | 不报 → 按路径认 | 新键被白名单丢弃（`reports.py:49-80,294-315`） |
| 文件夹同步 | 加 `always_on` | 不报 | 忽略 |
| 领取请求 | 加可选 `index_pending` | 不报 → 120 秒近似 | 忽略（`queue_contracts.py:85-91`） |
| 领取回复 job | 四键照旧，加可选 `attended`；载荷加 `account_uid`，位置字段换成领取设备的写法 | 只读四键里的三个 | 没有就按「有人值守、不核文件号」处理 |
| 回执 | 新错误码 `ERR_ACCOUNT_UID_MISMATCH`，按 `waiting_lock` 回报 | 不会出现 | 旧服务端按 `waiting_lock` 退回排队 |
| 按需请求领取 | 请求体仍是 `{version, kinds, device}`（`tests/test_companion_contract.py:292-295`，**不改**）；身份由密钥推出 | 不变 | 不变 |
| `companion.api.online` | 保留 `online`，加 `writers_known`、`needs_update` | — | — |
| 密钥 / 配对码形状 | `exp_<连接>_<secret>`、`密钥@域名` 不变 | — | — |

`tests/test_companion_contract.py` 要加的：
1. `DEVICE_METHODS` 两端路径一致，与另外三张不重叠，并在 MR.ERP 密钥的放行集合里；
2. 桌面端对这张表的 401 / 403 / 404 都按旧服务端处理；
3. 心跳新增键两端一致：服务端 `_ACCOUNT_SET_KEYS` 与 `attribute_keys` 都 ⊇ 客户端上报的账套行键；
4. job 必有四键 + 可选键集合两端一致；`lease_response` 的 `set(row) != _JOB_KEYS`（`queue_contracts.py:18,137`）改为「必有四键、可选键在登记集合内」；领取请求可选键 `index_pending` 两端一致；
5. `ERR_ACCOUNT_UID_MISMATCH` 两端同名，服务端据此标 `stale`；
6. 服务端替换的位置字段 = 客户端 `_task_settings` 读的字段（`C/worker.py:76-77`）；回读替换的参数键 = `request_contracts` 的通用位置键 ⊆ 客户端读的键；
7. 对话页每次打开都带设备密钥调 register，桥的状态里有设备密钥；
8. 身份认定、路由（含排头、黏住、`queued_at`、载荷版本）、锁口径、位置替换、`active_identity` 两种公式、「本单引用的行」各有纯函数单测；纯契约模块沿用「不导入框架」检查（`tests/test_cowork_companion_credentials.py:142`）。

`tests/test_cowork_companion_register.py:73-82` 两条旧用例原样保留（旧槽位）；新增：加入不顶替、`exists` 记登录人、`rekey` 轮换、无 Express 不吊销任何人、有已认领设备后旧版回 `update_needed`、非管理员无池回 `no_connection`。

## 6 界面

CC 设置页是 React（`frontend/assistant`），沿用现有的列表行、右侧状态与 ⋯；录入入口里的 Desk 部分（`targets.js`）用原生 Desk。不加说明文字、不加确认步骤；状态只用真实数据。

**CC 桌面版 · 设置 · 此电脑**（`F/components/settings.tsx:355-489`）
```
pearnly-abc.pearnly.com                       ● 已连接   ⋯      ⋯ = 暂停 / 断开连接
Express 数据文件夹   S:\2569\EXP69          [重新查找] [更换…]
账套                12                                  ›       点开：公司 · 年度，只读
网页可读取的文件夹                                   [添加文件夹…]
```
去掉「已在另一台电脑连接 / 改用这台电脑」（`settings.tsx:496-503`），只在旧服务端回 `in_use` 时仍出现。

**设置 · 电脑**（浏览器只读，替换现有「连接」页 `settings.tsx:515-581`；桌面端是同一个列表，owner / 管理员多一个 ⋯ 移除）
```
电脑
OFFICE-NUC        常驻              ● 在线    12 个账套
ACC-01            这台电脑          ● 在线     8 个账套
ACC-02                              ○ 离线
ACC-03                              需更新
```
点一行看这台能写的账套和网页可读取的文件夹。数据源 `folder_intake.api.computers`（`S/folder_intake/rules.py:142-165`）从「每条连接一行」改为「每台设备一行」。

**录入入口的「在线电脑与可写账套」**（Desk `targets.js`、LINE、CC 工具）
- Express 状态：有在线电脑能写 →「已连接」（绿）；能写的电脑都离线 →「电脑离线」（橙，仍可选，D4）；一台能写的都没有 →「没有能写的电脑」（不可选）；排头要的版本没有电脑支持 →「需更新」。
- 账套下拉照旧；同名账套（各台本机各一份）标签后加电脑名区分。
- 推送记录详情显示「由 ACC-01 写入」；CC 工具的状态与推送结果多给 `writer`，由模型自己表述。

**词条**（`F/i18n.ts` 与 `S/i18n/messages.tsv`，四列 en / zh / th / ja 同步，泰文按术语表）：新增 电脑、这台电脑、在线、离线、{n} 个账套、需更新、移除、电脑离线、没有能写的电脑、请更新 CC 桌面版、由 {电脑} 写入；错误码 `push.no_writer`、`companion.device_claimed_elsewhere`、`ERR_ACCOUNT_UID_MISMATCH` 的术语。删除 已在另一台电脑连接、改用这台电脑（`i18n.ts:364-365`）。「需要管理员账号」改为「此账号不能连接电脑」。「常驻」沿用 `alwaysOn`（`i18n.ts:369`）。新 DocType 名都不带 Companion；如果带了，就照 `translations/en.csv:410-411` 补一行 en.csv，受 `tests/test_cc_desktop_name.py` 约束。改已有文案先给用户过目（D8）。用户可见的名字只叫 CC 桌面版。

## 7 风险与取舍

### 7.1 被否掉的做法

| 做法 | 否掉的原因 |
|---|---|
| 只放开 `in_use`，同一连接挂多把密钥，其余不改 | 心跳互改 `config_json` → `record_revision` 每分钟变 → 排队单全被隔离（`collector.py:57-64`、`service.py:368-370`）；上报单槽互相覆盖；在线只认最后一台；刷新永远被判失败 |
| A：每台电脑一条连接 | 设备行混在连接表里，所有列连接的地方都要过滤；推送记录、`document_key`、记忆都按连接存，要再加一层「家 / 账套」间接；准备时就要选电脑，电脑离线时要服务器撤单、续备、转交，状态机变复杂 |
| 准备时选定写入电脑（A） | 电脑离线、换机、退役都要搬单；领取时决定是自然的拉模式，排队单不绑任何电脑 |
| 服务器替人重新准备排队单（A、C） | 改变「人确认的就是这份」的语义，还可能需要重新选；证据只核用到的资料后这种情况已很少，照旧转人工 |
| 路由按登记人（owner） | D1 不过时所有电脑都归管理员，录入员的第 ① 档永远不成立；改按「这台电脑上登录 CC 的人」 |
| 撞键加短后缀 | 加了后缀的键不是合法路径，而键在多处被当路径用（2.4 第 6 条） |
| 池里不止一台够得到就逐票重建主档索引 | 场景②每份共享账套都满足条件，等于逐票重建，正是「3 张间隔卡顿」的根因（`cdx_reindex.py:142-144`）；改为按账套黏住 |
| 名单值 `*` 表示全所 | `configure` 逐个查用户会拒绝（`service.py:82-83`），改 MR.ERP 账号密码都会失败；改用布尔字段，且只针对 Express |
| 保留 v1 证据核对分支 | 要保留单槽上报才能重算，与本方案相反；改为部署前只读统计、排空再部署 |
| B 的「个人 / 共用电脑」类型和按人按账套写入名单 | 多一套管理员配置；与「不做公司隔离」的口径冲突；没有实测需求。常驻标记已能表达「所里那台」 |
| 每台电脑一把写入通道锁、结果不明时一直保留（B） | 不对应任何外部资源，只会把那台电脑整个卡住；单线程桌面端 + 「每台同时一件」已经足够 |
| 新旧混用时的「单机模式闸」（A）/「池启用条件」（C） | 由三条结构规则替代：「有已认领设备后旧版不能新登记」「v2 锁沿用 v1 的 account 公式」「待认定（带退出条件）」，不需要全局开关 |
| 只靠 UNC 换算认账套 | 认不出非持久映射、本机共享、IP 与主机名别名、DFS |
| 只靠税号 + 期间等内容特征合并 | 备份副本特征相同，会写进副本；内容键只用于兜底锁 |
| 往账套目录写标记文件当身份 | 外部写入要授权；Express 备份会一起复制 |
| 用本机 `account_lock` 做跨电脑互斥 | 只探不持；探的是默认账套 |
| 常驻电脑做成 Windows 服务 | 2026-10-12 已否（DPAPI、映射盘按会话、第二写入者，`docs/work/2026-10-12-cc-always-on.md:15-17`） |
| 网页上管理电脑 / 连接 | 2026-10-10 定「客户端才能变更、网页只能看」 |
| managed 共享代理与本设计并存 | 不能有两套多电脑机制；按 D9 核完旧源码后二选一 |
| 一台电脑同时连多家站点 | 不在本次范围，规则不变（`C/desktop_settings.py:159-172`） |

### 7.2 剩余风险

| 风险 | 处理 |
|---|---|
| 文件号经 SMB 与服务器本机看到的不一致（NAS / Samba / DFS） | 批 0 真机核，作为批 2、3 开工的过关条件；不一致就自动退到位置 + 内容键认；company 锁兜底；最多少一台能写，不会写错 |
| 认定失败出现重复登记，同一张票可能经两行各推一次 | company 锁保证不同时写；与「推送不查重」的口径一致，验收时说明 |
| 内容键为空的账套没有兜底锁 | 界面不另提示；这类账套多为空账套，残余风险靠 REINDEX 独占做第二道 |
| 连接行热锁：认证、心跳、领取都 FOR UPDATE 连接行（`credentials.py:351,370`、`heartbeat.py:18`、`transport.py:110`），10 台约每秒 4 次 | 加锁顺序是迁移保护，不改；领取先做不加锁的预查；按 3 租户 × 10 台压测实测 |
| 黏住期间同一账套只有一台在写，吞吐不如并行 | 同一份账套本来就只能串行；黏住只省掉索引重建，不减少可并行的账套 |
| 前一台离线、主档索引没重建，下一台查不到它新建的主档 | 按今天批末失败的兜底：下批或夜间 PACK 补上（`cdx_reindex.py:157-159`）；批 7 真机核 |
| 同一配对码在两台电脑上 | 第二台要在 CC 里登录一次；开工前只读查生产有没有这种情况 |
| 本次发布不能直接回退 | 写明；回退前先清零 v2 的领取中 / 结果不明；部署前做一次回退演练或接受不可回退 |
| 代写在同事电脑本机留下写入备份（`dbf_writer.py:206-229`） | 共享盘场景同事本来就能打开；本机账套场景是主人自己的数据；按现有规则清理 |
| 跨人的写入先后：A 的进货等开机，B 的销售先写 | 同一份账套按排队先后；不够时走现有库存检查与人工 |
| 会计离职、账号停用 | 他签发的电脑立即失效；他排队中的单按录入人失权转人工（与今天一致） |
| Express 程序本身开着账套时写入失败、整表还原，会覆盖期间 Express 里录的内容 | 现有风险，本方案不放大（同一份仍只有一个 CC 写者），单列 |

### 7.3 开工前必须核实

- **真机（批 0）**：同一账套目录在服务器本机与两台经 SMB 映射的客户端上，文件号是否一致；NAS 上是否非零且稳定；普通用户能否读 `LanmanServer\Shares`；`WNetGetConnectionW` 对非持久映射的结果；跨电脑接手时主档索引的可见性。第二台物理电脑属于采购或借用，需要用户授权；录制笔记本上有别人的账号，不用。
- **生产只读**（需授权，用一次性客户端容器，不 exec 进数据库容器）：各站点 Express / MR.ERP 连接各几条、`allowed_users` 几人、v1 在途与结果不明记录数、同一把密钥上报的电脑名是否来回变（同一配对码抄到两台）。补丁按「一条 Express、名单只有创建人」也能正确运行，但开工前按迁移基准核一次。
- **旧平台 managed 共享代理**：仓里还留着 `branch='managed'` 的代码（`S/push/lifecycle.py:104-169`、`S/push/native/contracts.py:74-77`、`S/push/application/service.py:45`），旧设计见 `docs/work/cowork-backend/companion-identity-contract.md:61-66`；上一批采集记录「60 个端点全部 generation=0、managed 为 0」（同文件 `:11`，不是本轮确认的）。本机没有旧源码副本（`pearnly-app` 不在本机，固定只读目录 `/tmp/pearnly-cowork-migration-20260920` 已不存在）。开工前按迁移线上基准取线上 revision，只读核一次旧源码的 managed 语义，再由 D9 定下线还是合并。

## 8 需要用户拍板

**2026-10-15 用户拍板：D1–D9 全部按建议。** D8 文案按第 6 节照用；D9 先按线上基准核旧源码，倾向下线。
开工时间：等 2026-10-15 夜间这批上线、用户验收后再排；第 0 批的第二台物理电脑（借或买）仍待用户决定。


| # | 决定 | 推荐 |
|---|---|---|
| D1 | 录入角色能否在自己电脑上登录 CC 就连上（签发人 = 本人，失权即失效；改 `register`、`issue`、`_issuer` 三处，建池仍只限管理员） | 能。场景①②不依赖它（D7），不批就由管理员在每台电脑登记一次 |
| D2 | Express 池对全所成员开放（布尔字段 `all_members`；新建池默认开，迁移时现有 Express 主连接设为开）；MR.ERP 名单本次不动 | 是（与 2026-09-26「不做公司隔离」一致）；先只读查生产名单 |
| D3 | 写账交给谁：登录这台电脑的人就是录入人 → 常驻电脑 → 其他够得到的在线电脑（排队超过 2 分钟后接手，代写不弹框）；主档刚改过的账套按账套黏住 | 照此；备选是「只有录入人没有自己的电脑时才找同事电脑」 |
| D4 | 能写的电脑都不在线时，网页 / LINE / CC / 定时任务仍可选 Express，排队等开机后写 | 可以 |
| D5 | 排队单领取时只核本单用到的资料，不再比日期、电脑、桌面端版本 | 是 |
| D6 | 一张结果不明只挡那一份账套（及同公司同期的副本），Express 不再锁整条连接（登记为迁移保护「外部写者锁」的有意改动）；本次发布不可直接回退 | 是 |
| D7 | 写账路由按「这台电脑上登录 CC 的人」，不按登记人 | 是 |
| D8 | 第 6 节文案的新增、删除，以及「需要管理员账号」的改写 | 请过目 |
| D9 | 旧平台 managed 共享代理分支：下线，或并入本设计 | 先按线上基准核旧源码再定；倾向下线，不并存两套 |

## 9 工作量（机器耗时）与分批

| 批 | 内容 | 写代码 | 机器跑 | 等人 |
|---|---|---|---|---|
| 0 | 真机核文件号 / 共享表 / 非持久映射 / 主档索引；只读查生产；核旧源码 managed。**批 2、3 的过关条件** | 0.5 h | 1 h | 第二台物理电脑（采购或借用，要授权）；生产只读授权；D9 |
| 1 | 设备 DocType、密钥按设备、按摘要认证、register v2（`exists` 记登录人、`rekey`）、`DEVICE_METHODS` 与 MR.ERP 放行、下线 `issue / revoke / status`、D1 三处、补丁第 1 步、ADR | 4.5 h | 1.5 h | D1 |
| 2 | 登记 / 可达 / 账套资料表、身份纯函数（主机名路径键、不变量、待认定退出）、心跳停写配置、两处白名单、并集目录、中和默认账套、资料淘汰规则、刷新改派（子租约）、单槽读者改读（含回读版本）、补丁第 2–3 步 | 6 h | 1.5 h | 批 0 |
| 3 | 路由纯函数（排头、黏住、`queued_at`、载荷版本）、`transport.lease` v2、位置替换、每台一件、锁口径 v2 与 company 映射、证据 v2 两种模式与 6 处读方、确认不带密钥、`pool.state` / `online` 兼容形状、主连接统一解析、问责字段 | 7.5 h | 2 h | 批 0；D3–D7 |
| 4 | 读者三元组与补丁第 4 步、CC 工具与 `fetch` 的读者、按设备在场、请求定向、回读通用位置键、提醒 / 简报 / 限流口径、`all_members` 与补丁第 5 步 | 4 h | 1 h | D2 |
| 5 | 桌面端 1.6.0：设备密钥、hello / leave（401 / 403 / 404 = 旧服务端）、`uid` / `share` / `host_names` / `always_on`、无 Express 也能加入、`attended`、先放租约再提示、探任务账套、写前文件号核对与 `ERR_ACCOUNT_UID_MISMATCH`、`index_pending` | 4.5 h | 1 h（Windows 构建与单测） | — |
| 6 | 界面与词条（此电脑、电脑、录入入口状态；四列术语；对话页每次打开调 register） | 3 h | 0.5 h | D8 |
| 7 | 测试环境联调：测试机两个 Windows 账号当两台 + 本机共享映射 + 第二台物理机；场景 ①–⑤；3 租户 × 10 台并发压测，用已识别的单据和模拟设备客户端，不调用任何付费识别 | 1 h | 3 h | 用户试用 |

- 合计写代码约 31 小时，机器约 11.5 小时，另加等人。
- 每批跑 `python3 scripts/check-architecture.py` 和与 CI 同名的完整分支闸门；不改 `pearnly_core`，不涉及 DMS。
- 部署前检查加两项：v1 单只读统计（4.4 第 1 步）；回退演练，或书面确认本次不可回退（2.6）。
- 攒批部署：批 1–4、6 一次服务端发布（进维护立即部署，按 4.4 回读），批 5 在服务端上线后发正式版。真机验证和用户试用通过前不合入 `main`。
- 测试机只有一台，两个 Windows 账号能模拟两台设备和 `\\localhost` 共享；跨物理机的 SMB / NAS 只有批 0 的第二台机器能测，交付时如实说明。

## 附录 审查意见处理

逐条在基线 36487276 上核过代码。「采纳」后面写改在哪；有出入的写一行理由。

| # | 处理 |
|---|---|
| 1 | 采纳，改 1.2：管理员能看到全部连接（`service.py:133-134,186`），只是推不了、登记不了电脑 |
| 2 | 采纳，改 2.3 第 6 条：两个函数在 `preparation.py:78-114`；`_known_version` 是现存坏读法，改读设备版本，并列入 1.2 |
| 3 | 采纳，改 2.3 第 3 条和 2.5：`revalidate` 拆成 confirm / lease 两种模式，并标出四个调用点 |
| 4 | 采纳，改 1.1 和 2.5：读 `credential` 的 6 处逐条列出并配测试；另补一个读方 `destinations.py:28` |
| 5 | 采纳，改 2.2、2.8、第 5 节：旧服务端回 401（`http_auth.py:47-51,58`），401 / 403 / 404 都按旧服务端处理 |
| 6 | 采纳，改 2.2 和场景⑥：`http_auth.py:287-289` 放行 `DEVICE_METHODS` |
| 7 | 采纳，改 1.2 和 2.2：下线三个 RPC。小更正：`issue` 只取 `_active`（`credentials.py:213`），取 `_active or _latest` 的是 `revoke`、`status`（`:292,305`） |
| 8 | 采纳，改 2.2 和 2.9：`register`、`issue`、`_issuer` 三处一起改，建池仍只限管理员，非管理员无池回 `no_connection` |
| 9 | 采纳，改 2.5 和 D7：路由按 `signed_in_user`。补充：今天对话页只在本机没有成员时才调 register（`desktop.ts:315`），v2 改为每次打开都调 |
| 10 | 采纳，改 2.5：候选改成每份账套的排头 |
| 11 | 采纳，改 2.5：新增 `queued_at`；另补 `ack` 退回排队处（`service.py:642`） |
| 12 | 采纳，改 2.5 和 2.6：按账套黏住。服务端用领取请求的可选键 `index_pending` 知道主档索引是否重建完，旧版按 120 秒近似 |
| 13 | 采纳，改 2.5：版本拦截挪到领取；池里没有一台支持时显示「需更新」；不跳过排头 |
| 14 | 采纳，改 2.4 第 6 条：本机账套键带主机名，取消加后缀。小更正：`same_company_account_sets` 只把键当查记忆的标识（`push/application/service.py:263-282`），不当路径用，不受影响 |
| 15 | 采纳，改 2.4 不变量与第 3 条 |
| 16 | 采纳，改 2.4 第 5 条和 4.2：7 天或移除 / 退役即解除，旧电脑标「需更新」 |
| 17 | 采纳，改 4.3 和 7.3：如实写明这种情况不是零重配；认领后整份重报替换可达行；开工前只读查生产 |
| 18 | 采纳，改 2.6 和 D6：登记为有意改动；`company` 字段映射；不可回退及回退前提（另补 `recover_expired` `:669-671` 同样会冲突） |
| 19 | 采纳，改 2.2 和 4.1：迁移来的密钥 `active_key` 永远不改 |
| 20 | 采纳第二种做法，改 4.4：部署前只读统计、排空再部署。不保留 v1 核对分支：要保留单槽上报才能重算，与本方案相反 |
| 21 | 采纳，改 2.9、4.1 和 D2：用布尔字段代替 `*`，只针对 Express，新建池默认开，MR.ERP 不动 |
| 22 | 采纳「一律按合并目录核」，改 2.3 第 7 条：代码中和，数据不清（不升版本）；6 个科目来自目录行的 `mapping` |
| 23 | 采纳，改 2.2 和 2.3 第 4 条：被引用的那份不淘汰；资料不设年龄上限（与今天一致），靠领取时核本单引用的行 |
| 24 | 采纳，改 2.7：读者 =（设备，成员连接，根目录），`_rule` 同时核设备和连接 |
| 25 | 采纳，改 2.7 和 2.10：补列 CC 工具、`fetch.py` 和 `online` 的调用方，`online` 保持兼容形状 |
| 26 | 采纳，改 2.7：只认 `request_contracts` 的通用位置键。小更正：回读参数的实际键是 `account_set` / `account_root`（`readback/contracts.py:96-102`），不是 `account_dir` |
| 27 | 采纳，改 2.7：账套级租给一台，整目录按在线设备分子租约，并定义何时算完成 |
| 28 | 采纳，改 2.5 和第 5 节：新错误码、两处白名单、job 可选键都进契约测试 |
| 29 | 采纳，改第 6 节：四列术语、DocType 不带 Companion、错误码补术语、删去「官方 Desk 组件」、改为「电脑离线」 |
| 30 | 采纳，改第 8 节：D5 删去银行流水（`bank_evidence.record` 全仓没有调用方）；原 D7 删除；D2 限定 Express；新 D7 = 按登录人路由 |
| 31 | 采纳，改 7.3 和 D9：本机没有旧源码副本，列为开工前按线上基准核实的事项，核完再定下线还是合并 |
| 32 | 采纳，改 7.3 和第 9 节：批 0 是批 2、3 的过关条件，第二台物理机需授权；压测不用付费识别；部署前检查加两项 |
