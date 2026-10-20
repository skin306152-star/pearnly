# 多电脑方案施工记录（T5，2026-10-15 夜起）

- 方案：[多电脑方案](2026-10-13-cc-multi-desktop-plan.md)（D1–D9 用户 10-15 全部按建议拍板）。
- 分支 `pearnly/multi-desktop`，工作树 `.claude/worktrees/multi-desktop`，基线 `origin/main` 7c9bb445。
- 补丁编号用 `v0_0_19_*`（`v0_0_18_history_push_targets` 已占）；ADR 用 0021。
- 合入规矩（方案第 9 节）：批 1–4、6 攒成一次服务端发布，批 5（CC 桌面版）在服务端上线后发正式版；真机验证与用户试用通过前不合入 `main`。
- 证据层级：第 0 批只有只读核验；实现、本地闸门、部署、真机验收在后面各节登记。

## 最终验收场景（用户 2026-10-15 夜原话）

「我想要的使用场景：一个主 pearnly 号：添加成员，成员在自己的电脑自己的账号使用 cc 和 pearnly，每个人有每个人的独立账号，在自己的电脑使用，exp 还是依赖公司主体」。

对应方案：主账号在「团队管理」加成员（各自独立账号、角色）；成员在自己电脑用自己账号登录 CC 桌面版即加入事务所的 Express 连接（D1，不用配对码、不顶替别人的电脑）；
Express 仍是事务所一条连接（公司主体），账套按客户公司与物理位置认定，谁的电脑写都落同一本；谁录的单由谁的电脑写，~~关机时常驻或在线同事电脑代写（D3、D7）~~ 关机时排队等他的电脑开机，不由同事代写〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕；
对话各人各自，公司主档、知识库、记住的选择挂在公司上全所共用。第 7 批联调按此场景验收：testcowork「团队管理」加第二个测试成员，录制笔记本 acct 账号用该成员登录 CC 桌面版。

## 第 0 批：真机与生产只读核验（2026-10-15 18:30–19:10，曼谷）

### 真机

探针以桌面用户身份（一次性计划任务，Interactive、Limited）运行，只用 `FILE_READ_ATTRIBUTES` 打开目录和 `ISINFO.DBF` 取卷序列号与文件号，不读内容、不写任何文件；任务、脚本、输出用完即删。
测试机为验证开了只读共享 `PearnlyExpressRO`（`C:\PearnlyExpress`，只给 skin3 读，用户本人双击执行）。

| 位置 | 访问方式 | 64 位文件号（`GetFileInformationByHandle`） | 128 位文件号（`FileIdInfo`） |
|---|---|---|---|
| 测试机 NTFS（6 份 T7 账套 × 目录 + ISINFO.DBF） | 本机 `C:\`、`\\localhost\共享`、`\\DESKTOP-MN2BRKR\共享`、`\\192.168.0.78\共享`、非持久映射 `Q:` | 五种方式完全一致，读两遍不变，卷号 `c8d5876c` | 一致 |
| 办公室 NAS `\\192.168.0.212\pas212`（`S:` 持久映射） | `S:\…` 与 UNC | 非零、两种方式一致、读两遍不变，卷号 `0c9a53f6`，高 32 位为 0 | 失败（错误 87） |
| Accserver `\\Accserver\d$` | UNC | 非零、读两遍不变，卷号 `9eb765a9` | 失败（错误 124） |

- `uid` 取 **卷序列号 + 64 位文件号**（`GetFileInformationByHandle`，`FILE_READ_ATTRIBUTES` + `FILE_FLAG_BACKUP_SEMANTICS`），**不能依赖 `FileIdInfo`**：NAS 与 Windows 服务器共享上都取不到。
- 共享表：`HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Shares` 对 `BUILTIN\Users` 有读权限，普通用户能换算 `\\本机名\共享名\…`（读到 `PearnlyExpressRO Path=C:\PearnlyExpress`）。
- 映射盘：`WNetGetConnectionW` 对非持久映射（`Q:` → `\\localhost\PearnlyExpressRO`）与持久映射（`S:` → `\\192.168.0.212\pas212`）都返回网络路径。
- `host_names`：测试机除局域网 `192.168.0.78` 外还有 WSL 虚拟网卡 `172.27.224.1`、Tailscale `100.67.246.0`。WSL / Hyper-V 网段在不同电脑上可能重复，第 5 批上报时只取有默认网关的物理网卡地址，排除 vEthernet / WSL / VPN 虚拟网卡。
- **未完成**：笔记本（DESKTOP-8BRD5AV，acct）经映射读测试机共享这一项，要用户在笔记本上输入测试机 skin3 密码；用户一时记不起，挪到第 7 批两台联调时再做。SMB 返回的是服务端的文件号，NAS / Accserver 已证明经网络读取非零且稳定。
- 跨电脑接手时主档索引的可见性要写入，按方案第 7.2 节放在第 7 批真机核。
- 测试机与笔记本桌面上的一键文件 `Pearnly-1-share.cmd`、`Pearnly-2-map.cmd` 留到第 7 批用完再删；共享 `PearnlyExpressRO` 同样保留到第 7 批，用完请用户删除（`Remove-SmbShare -Name PearnlyExpressRO`）。

### 生产只读（2026-10-15 18:50，经 cowork-web 容器的 bench mariadb 客户端，只 select）

| 站点 | Express 连接 | MR.ERP 连接 | binding_generation>0 | 名单最多人数 | 有效 Express 密钥 / 连接 | 凭据 branch | 推送记录 branch | 在途 |
|---|---|---|---|---|---|---|---|---|
| erpcowork | 2（启用） | 1 | 0 | 1 | 1 | legacy 2 条（1 有效） | legacy 13 | manual 13，无 pending / leased |
| testcowork | 1 | 1 | 0 | 1 | 1（MR.ERP 也 1） | legacy 3 条（2 有效） | legacy 157 | manual 17，无 pending / leased |

- 只有这两个 Cowork 站点；managed 分支在生产为 0 条。
- erpcowork 有两条启用的 Express 连接：方案 2.10「主连接统一解析（最早建、启用、client 为空）」要覆盖这种情况。
- 「同一配对码抄到两台」：`report_json` 里没有电脑名字段，历史无法回查；按方案 4.3 由 `device_hello` 的 `claimed_elsewhere` 兜底。

### 旧平台 managed（D9）

结论：**下线，不并入**（D9 按建议）。

- 线上身份本次无法在线确认：gcloud 只读查 `pearnly-web` / `pearnly-worker` 在三个区域都 `Cannot find service`，旧 Cloud Run、旧库、镜像库已于 9-24 退役删除（`docs/work/2026-09-24-legacy-cloud-retirement.md:25`）。只能沿用退役前冻结的 `501589f0`（映射源码 `ec2e5c95`，`docs/development/current-state.md` 80–90 行）。
- 旧源码（固定 SHA 只读克隆）：managed 是旧计划 F1「一个 Express Profile 多员工共享」，入口 `/shared/enroll` 等，按租户开关 `erp_shared_express_endpoint`、读不到即关（`services/erp/shared_express_flag.py:14-24`）；没有任何前端调用；B4 界面、B5 放量都没做（`docs/erp/ERP-LINE-COMPANION-CLOSED-LOOP-PO.md:483`「flag 全关」）。退役前最后一次只读采集（9-22 05:55 UTC）web / worker 各 60 个端点全为 legacy、shared / bound / managed 均为 0。
- 新仓：没有任何写入方能让 generation > 0（新建连接写死 0，`connections/service.py:125`；凭据只允许 legacy / 0，`companion/credentials.py:87,229,354-373`）；`companion/` 客户端没有 managed 协议。生产统计 managed 0 条（上表）。
- 下线做法（机械收窄，单独提交）：`push/lifecycle.py` 只认 legacy、删 managed 分支与 `payload_generation`；`push/native/contracts.py:74-77` 要求 legacy 且 generation 为 0；`push/application/service.py:46-47` 写死 legacy / 0；`targets/selection.py`、`targets/freeze.py`、`targets/contracts.py` 删掉按 bound 账套取默认值的分支。保留只读兼容字段：`Cowork Push Record.branch`（读历史）、各表 `binding_generation`（恒为 0 的一致性校验）、连接上的 `bound_account_set` / `bound_profile_key`。不需要补丁。测试删 managed 用例并补「managed 一律拒绝」断言。
- 对迁移保护无影响：外部写者锁、fence、attempt 令牌摘要、请求回执幂等、回执与派发分开都走 legacy 与原生账本。

## 进度

### D9 managed 下线（f3abd1d5，本地提交）

见上节「旧平台 managed（D9）」；单独提交。提交前缀是 `refactor:`，但含两处**行为收窄**（审查指出，提交说明没写成行为
变化，在此登记）：`targets.freeze` 拒绝代数不为 0 的连接；代数为 0 的连接不再退回用 `bound_account_set` 当默认账套。
生产 bound 为 0 条（第 0 批核过），实际影响为零。

### 第 1 批：设备身份层（服务端，2026-10-15 夜，本地提交，未推送）

提交（分支 `pearnly/multi-desktop`，基线 7c9bb445）：

- `8f69507d` refactor：下线 `companion.api.issue / revoke / status`（没有界面调用；内部 `credentials.issue` 只给
  `register` 旧路径，内部 `revoke` / `status` 仍只限管理员、只留给实验室与运维脚本）。
- `88e6e96c` feat：电脑记录、按电脑的密钥、按摘要认证、register v2、D1、`DEVICE_METHODS`、补丁第 1 步。
- 本条 docs：ADR-021、架构说明、本记录。

做了什么（对照方案第 9 节第 1 行）：

- 新 DocType `Cowork CC Desktop Device`（`S/companion/device_documents.py` 守写入，`S/companion/devices.py` 服务）；
  `Cowork Express Agent Credential` 加 `device`（Link）与 `full_report_due`（Check）。
- `contracts.active_identity(site, connection, device=None)` 两种公式、`active_matches` 按本行公式重算、`device_key` /
  `device_digest` / `device_issue_intent`；报文纯规则 `S/companion/device_contracts.py`。
- 认证：按 `token_digest` 找行 → 核连接 = 密钥中段、`active_key` 按本行公式、电脑没退役、签发人此刻「录入或管理 + 使用
  名单」；Connection → Credential 加锁顺序不变（register v2 里电脑行最后锁）。
- `online` / `for_confirmation` 改取「连接上报凭据那把（仍有效时），否则旧槽位」：单台电脑与今天同一把。
- register v2：结果按方案表格逐条；`tests/test_cowork_companion_register.py:73-82` 原样通过（只改了辅助函数：对
  `require_actor` 参数的断言——D1 后第一次调用不带 `management`——以及 `devices.pool_claimed` 的替身）。
- `DEVICE_METHODS`（`S/companion/device_api.py`）与 MR.ERP 放行集合 `http_auth.FOLDER_ONLY_ALLOWED`；心跳在
  `full_report_due` 时不回 `account_sets_unchanged_ok`，收到整份上报才清。
- 补丁 `pearnly_cowork.patches.v0_0_19_companion_devices`：只做 4.1 第 1 步，逻辑在 `devices.seed_legacy`。

证据（本机，`PATH=$HOME/pearnly-devtools/bin:$PATH`，python3 3.12；需要 Frappe 的单测在一次性离线容器
`docker run --rm --network none`、`pearnly/frappe-sample:16.34.1`、工作树只读挂载里跑，不碰共享样板栈）：

| 检查 | 结果 |
|---|---|
| `python3 tests/test_cowork_cc_desktop_devices.py`（新，进 CI） | 37 OK：纯契约、表结构、旧版槽位逐字照旧、加入不顶替、`exists` 记登录人、`rekey` 轮换与重放、非登记人不能 `rekey`、无 Express 不吊销、认领后旧版 `update_needed`、非管理员无池 `no_connection`、录入签发的密钥随权限失效、移除过的电脑重新加入、按摘要认证（旧槽位 / 按电脑 / 冒用别的连接 / 退役 / 吊销）、`hello` 认领 / 合成一台 / `claimed_elsewhere` / 从不改 `active_key` / 并发撞唯一摘要、`leave` 只吊销自己、整份重报、补丁幂等且不动 `active_key` |
| `PYTHONPATH=apps/pearnly_cowork python3 tests/test_cowork_companion_credentials.py`（新进 CI） | 17 OK（基线上 2 项旧失败：`last_seen_at` 断言过时、公开接口集合缺 `register`，本批一并改对） |
| `tests/test_cowork_companion_http_auth.py` / `test_companion_contract.py` / `test_cowork_companion_queue_http_contracts.py` / `test_cc_desktop_name.py` / `test_cowork_folder_intake.py` | 21 / 51 / 16 / 12 / 113 OK |
| 容器内：`test_cowork_companion_register.py`、`_liveness`、`test_cowork_folder_computers.py`、`test_cowork_line_intake.py`、`test_cowork_push_account.py`、`_legacy_wire`、`test_cowork_mrerp_push.py`、`test_cowork_readback_workbooks.py`、`test_assistant_push.py` | 全部 OK；新模块在 Frappe 16.34.1 下全部可导入 |
| `tests/test_cowork_companion_source_parity.py --source <草稿目录 legacy-ec2e5c95>` / `test_cowork_push_source_parity.py` | PASS 244 组 / PASS |
| 其余 companion / push / targets / connections / folder / readback 单测（30 个文件） | 全部 OK |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check`、`check-i18n.py --strict` | PASS / PASS / 退出码 0 |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（先 `npm ci --offline` 两个前端） | 85 步全过，退出码 0 |
| `scripts/cowork-companion-credentials-lab-verify.py` | 只编译通过；要真实 Site，没跑 |

没做 / 没验：真实 Site 迁移与补丁（第 7 批联调）、并发加锁、HTTP 链、真机；`test_cowork_companion_reports_source_parity.py`
要的旧源码目录布局与草稿目录不同，没跑（本批没改上报解析）。

与方案的出入及理由：

- 登记人字段叫 `device_owner`：`owner` 是 Frappe 保留字段（文档创建人）。
- 设备密钥摘要 = `sha256(['cc-desktop-device', 站点, 设备密钥])`，比方案多一个用途标签，免得与别的「站点 + 串」指纹同形。
- 旧槽位的 `replace`（顶掉另一台）对录入角色只允许顶自己签发的那把；管理员照旧。方案只写了「逐字不变」，录入角色以前
  根本登记不了，这是 D1 放开后补的边界。
- `rekey` 不是登记人也不是管理员时回 403（沿用「需要管理员账号」那条现有文案，不加新文案）。
- 被移除（退役）的电脑再登录：旧记录交出摘要、留作历史，这台作为新电脑加入（方案没写，第 6 批「移除」要用）。
- `device_hello` 碰到这台电脑在这条连接上已有自己的密钥（旧槽位密钥想并进来）也回 `claimed_elsewhere`：守「每个（连接，电脑）
  最多一把」。
- 内部 `credentials.revoke` / `status` 没删：7 个实验室脚本在用；只限管理员、没有 RPC，「当前那把」先取旧槽位。
- 「整份重报」记在密钥行 `full_report_due` 上（心跳按密钥走）。
- 新文案只有 DocType 名与字段标签（13 条，四语言进术语表）；`exists` / `update_needed` / `claimed_elsewhere` 是状态值，
  界面文案在第 6 批。

### 第 1 批对抗审查与修复（2026-10-15 夜，本地提交，未推送）

审查结论：无阻塞，5 个应修、7 个小问题。逐条核对代码后处理如下（复现脚本在审查方草稿目录，用的是单测里的内存版 Frappe）：

| # | 问题 | 处理 |
|---|---|---|
| 应修 1 | 旧版 `replace` 在锁连接前判断「池里没人认领」，并发认领的新版电脑会被顶掉 | 已修：判断挪进 `credentials.issue`，锁住连接和要动的密钥行之后、用加锁读做；回归测试模拟认领恰好在锁连接前提交 |
| 应修 2 | 新版先 `register`、后 `hello` 时同一台电脑变两台、旧密钥永远有效 | 已修（服务端，顺序无关）：`hello` 发现这台电脑在这条连接上已有自己的密钥 → 回 `superseded`，吊销发起请求的这把旧槽位密钥、退役没有密钥的旧电脑；不再回 `claimed_elsewhere`。真正的「配对码抄到两台」（旧电脑已被别的设备密钥认领）仍回 `claimed_elsewhere` |
| 应修 3 | 旧路径留下幽灵旧电脑、新发的旧槽位密钥不挂电脑 | 已修：`replace` 吊销 / `device_leave` / `superseded` 之后，没被认领、也没有其他有效密钥的旧电脑退役（`devices.retire_orphan`）；旧路径新发的旧槽位密钥同补丁一样建一台未认领的旧电脑（`devices.for_old_slot`） |
| 应修 4 | `hello` 与 register v2 先锁电脑行再锁密钥行，违背 Connection → Credential → Device | 已修：两处都先不加锁地读出计划、锁完密钥行（含 `_latest` 代数行）、最后锁电脑行并复核；复核不符或撞唯一摘要抛死锁，由 `api._local_transaction` / `device_api._guarded` 整段回滚重来。单测加了加锁顺序记录器，按事务断言「锁过电脑行后不再锁新的密钥行」（旧代码的 register 分支能被它抓到） |
| 应修 5 | 旧对话页把 `update_needed` 当 `created`，拿 `undefined` 去配对 | 已修：`frontend/assistant/src/lib/desktop.ts` 只在 `status === "created"` 且有 `token` 时配对，其余显示连接失败（不新增文案）。发布约束不变：批 1–4、6 同一次服务端发布，真机与用户试用前不合入 `main`，别的窗口带不出去 |
| 小 1 | `update_needed` 在检查连接使用权之前返回 | 已修（随应修 1）：先 `_use` 再判断；没有使用权的录入员拿不到连接名 |
| 小 2 | register v2 与 `hello` 并发建同一台电脑撞唯一约束直接报错 | 已修：`devices.create_claimed` 撞摘要即整段重来，重来时按已有那台处理；有单测 |
| 小 3 | `in_use` 把签发人邮箱给了录入角色 | 接受，ADR-021「有意变化」已登记 |
| 小 4 | `rekey` 用同一个 `request_key` 重放回 `exists`、不带密钥，而旧密钥已吊销 | 不改服务端（回执幂等就是这样）；写进下方第 5 批约定 |
| 小 5 | MR.ERP 密钥也被打上 `full_report_due`、永远清不掉 | 已修：只给 Express 密钥打 |
| 小 6 | `public/desk/targets.js` 注释还提已下线的 `status` | 已修 |
| 小 7 | f3abd1d5 前缀 `refactor:` 却含行为收窄 | 不改写历史；在上节「D9 managed 下线」登记为行为收窄 |

审查方顺带回答用户的两个问题（照录，本窗口没另查）：前端重构用的是标准的 `--code-*` 令牌（`frontend/assistant/src/globals.css`
一处），不是写死，`scripts/check-frontend-tokens.py` 与 `tests/test_cc_ccs.py` 会拦写死；智能体推理档位子会话看不到，需编排方自查。

修复后的证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；需要 Frappe 的在一次性离线容器里跑）：

| 检查 | 结果 |
|---|---|
| `python3 tests/test_cowork_cc_desktop_devices.py` | 46 OK（新增 9 项：并发认领被看见、先校使用权、`replace` 后旧电脑退役与新旧电脑、未认领旧电脑 `leave` 后退役、先 register 后 hello → `superseded`、迟到看见自己的密钥仍 `superseded`、register 并发建同一台、两处加锁顺序、只给 Express 打整份重报） |
| `test_cowork_companion_http_auth.py` / `test_companion_contract.py` / `test_cowork_companion_queue_http_contracts.py` / `test_cowork_companion_credentials.py` | 全部 OK |
| 容器内 `test_cowork_companion_register.py`（去掉已无用的 `pool_claimed` 替身，加 `update_needed` 转述）、`_liveness`、`_legacy_wire`、`test_cowork_folder_computers.py`、`test_cowork_line_intake.py`、`test_cowork_push_account.py`、`test_cowork_mrerp_push.py`、`test_cowork_readback_workbooks.py`、`test_assistant_push.py`；新模块在 Frappe 16.34.1 下可导入 | 全部 OK |
| `frontend/assistant` `tsc --noEmit` | 通过 |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 85 步全过，退出码 0 |

仍没验：真实 Site 上的加锁与快照（MariaDB 11.8 的 `innodb_snapshot_isolation` 下加锁读遇到快照后被改的行会报
`ER_CHECKREAD`，Frappe 当死锁处理，同样走整段重来）、HTTP 链、真机——都在第 7 批联调。

留给后面几批：

- 第 3 批路由必须让没有可达账套的电脑领不到写入：本批起没装 Express 的电脑可以在 Express 池里拿到按电脑的密钥，
  它在批 1–4 一起上线前不能单独部署（方案本来就是攒批发布）。
- 第 6 批对话页每次打开都带设备密钥调 `register`；`update_needed` 的界面与下载地址。
- 第 5 批 CC 桌面版：设备密钥、`hello` / `leave`、401 / 403 / 404 当旧服务端、`host_names` 只报物理网卡；
  `tests/test_companion_contract.py` 的 `DeviceMethods` 类在那时补客户端一侧。约定（审查补充）：手里有旧槽位密钥时
  先 `hello` 再让对话页 `register`（服务端两种顺序都收得住，先 hello 少一次吊销）；`hello` 回 `superseded` → 丢掉这把、
  用自己那把；回 `claimed_elsewhere` → 丢掉这把重新登记；`rekey` 没拿到新密钥（重放回 `exists`、不带 `token`）时必须换一个
  新的 `request_key` 再试，不能拿同一个重放。
- 第 6 批对话页：`update_needed` / `superseded` 的界面；本批已让旧对话页遇到非 `created` 一律不配对。

### 第 2 批：账套登记、可达与账套资料（服务端，2026-10-16，本地提交，未推送）

提交（分支 `pearnly/multi-desktop`）：`38773dba` feat（代码与测试）、本条 docs（ADR-021 第 2 批决定与有意变化、架构说明、
本记录）。补丁编号 `v0_0_20_express_account_sets`（接在 `v0_0_19` 之后，要先有电脑才有旧电脑的可达行）。

做了什么（对照方案第 9 节第 2 行、2.3、2.4、4.1 第 2–3 步）：

- 三张原生表都在 connections 域（`S/connections/pool.py` 读写，守卫同连接三张表，`access.POOL_TYPES`）：
  `Cowork Express Account Set`（规范键 / 规范根永不改，控制器拦，规范根只许从空补一次）、`Cowork Express Reach`
  （`ok` / `pending_identity` / `stale`，待认定的行带当初报的显示属性）、`Cowork Express Account Facts`
  （按账套、不含版本号、每条连接 20 份、未了结推送记录引用的不淘汰——引用由 companion 经 `push.native.service.open_account_keys`
  传入，connections 不依赖 push / companion）。设备编号在 connections 只是字符串（`connections/member.py` 的 `Member`）。
- 身份纯函数 `S/targets/identity.py` `resolve`（2.4 第 1–6 条，含主机别名归一、`localhost`、本机共享 / 映射盘的程序目录
  换算、两台本机同一路径不合并、待认定与 7 天 / 移除退出、带主机名的本机键、同一路径串即同一处）。
- 心跳（`S/companion/heartbeat.py` → `S/connections/collector.py`）：不写 `config_json`、不升版本；`previous` 取这台电脑
  自己的电脑行（所选、映射、版本，`devices.state` / `record_report`，电脑行在加锁顺序最后）；整份名单走认定、只替换
  自己的可达行，空名单只清自己；端点目录 = 并集；主档进账套资料；上报记录只剩绑定 + 版本 + 被拦的更新。
  名单没变的心跳一天补记一次可达行的报到时间；有待认定的行每次心跳重核，某份登记补上身份时也重核别的电脑的。
- 两处白名单收 `uid` / `share`（`S/connections/reports.py`），`ReportPlan.account_sets` 带整份名单的投影行。
- 中和默认账套：`model.pool_config` 在连接详情与推送读取两处去掉 Express 的所选与 6 个科目（数据不清、不升版本）。
- 刷新改派（`S/connections/refresh.py`）〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕：`lease_device`、`parent_request` 两个新字段；账套级按发起人自己的电脑 → 常驻 →
  其他（`my_turn` 纯函数，在线判据由 `credentials.pool_peers` 传入），120 秒后谁够得到谁读；交给电脑的是它自己的写法；
  整目录按 120 秒内来报到的电脑发子租约，`settle` 收尾；平时没有请求时领任务不查池。
- 单槽读者改读：`preparation.collect / reported_stock_groups / reported_masters`、`stock_effects.apply`、
  `service.stock_groups_for_log`、`account_masters.view`、`folder_intake/rules._reader_view`（电脑名，`devices.reporter_label`）、
  `readback._known_version`（`devices.newest_version`）。
- 补丁第 2–3 步：见 ADR-021 第 13 条；另给每把有效 Express 密钥打「整份重报」，上线后每台电脑整份报一次。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；需要 Frappe 的在一次性离线容器 `pearnly/frappe-sample:16.34.1`、
`--network none`、工作树只读挂载里跑）：

| 检查 | 结果 |
|---|---|
| `python3 tests/test_cowork_express_account_sets.py`（新，进 CI；替身 `tests/cowork_pool_fake.py`，存旧副本会像 Frappe 一样报 TimestampMismatch） | 48 OK：认定规则逐条表驱动（含别名 / IP / DNS / localhost、两份本机拷贝不合并、待认定与退出、同路径串）、不变量、白名单；两台映射不同盘符同一份、本机同路径两份、心跳不动配置与版本、空名单只清自己、可写取任一台、过渡期待认定 → 旧电脑升级后认上同一键、7 天与移除退出、规范键 / 根不改；资料合并 / 20 份 / 不淘汰被引用的 / 不每分钟重写 / 存货主档数；证据 v1 凭据按可达；刷新分派表、发起人优先、别的电脑不判失败、同台换密钥判失败、120 秒后换人读、交回换回规范键、整目录子租约 / 时间窗 / 单台立即完成 / 都失败才失败；无默认账套；补丁播种、幂等、已报过名单的电脑不覆盖、无旧电脑、旧版报回同一批键、补丁本体的查询与整份重报 |
| `tests/test_cowork_any_set_catalog.py`（改到新位置：资料在 `pool.facts`、换密钥仍算） | 11 OK |
| `tests/test_cowork_cc_desktop_devices.py`（新增心跳里的电脑 5 项：Member、所选与版本记在电脑行、补丁交接默认账套、最新版本 / 在线 / 电脑名、心跳不读写连接配置；退役删可达行） | 51 OK |
| `tests/test_companion_contract.py`（新增两处白名单收 `uid` / `share`、客户端今天报的账套行键都在白名单；刷新指令键名不变） | 53 OK |
| `tests/test_cowork_report_writes.py` / `_account_caches.py` / `_preparation_dynamic.py`（改成不依赖 Frappe，进 CI） | 6 / 4 / 2 OK |
| 变异核对 | 去掉移除前按连接加锁：设备测试 2 项失败；去掉页面 exists 后的 rekey 重试：页面测试失败 |
| 容器内 `tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含源码对照） | 182 个文件 OK；`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py` 两个基线上就失败（与本批无关，基线同样） |
| 新模块与补丁在 Frappe 16.34.1 下可导入 | 17 个模块 OK |
| `test_cowork_companion_source_parity.py --source legacy-ec2e5c95` / `test_cowork_push_source_parity.py` | PASS 244 组 / PASS |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check` | PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 87 步全过，退出码 0 |

没做 / 没验：真实 Site 迁移与补丁、MariaDB 上的加锁与快照、HTTP 链、真机（第 7 批）；
`test_cowork_companion_reports_source_parity.py` 要的旧源码目录布局与草稿目录不同，没跑（本批只给清洗加了两个键）。
实验室脚本 `scripts/cowork-companion-collector-lab-verify.py`、`-heartbeat-`、`cowork-ack-effects-lab-verify.py`、
`cowork-preparation-confirm-lab-verify.py`、`cowork-companion-transport-lab-verify.py` 还按旧的单槽上报记录断言（`ingest` 也
少了 `member`），要真实 Site 才能跑，本批没改，第 7 批联调时按新位置重写。

与方案的出入及理由：

- 「所选账套与 6 个科目记在设备上」落在电脑行新字段 `selection_json`；版本用第 1 批已有的两列。
- 账套资料没有年龄上限（同方案）；「这份是不是某台电脑按期上报的所选」记在资料行 `routine` 上，`account_masters` 据此
  不另排读（以前看连接的默认账套）。
- 认定第 4 条（按路径）只给没认领过的旧版电脑；新版报不出文件号又不在共享上的本机账套走第 6 条（带主机名），免得两台
  同一默认路径撞键。第 3 条加了一条护栏：那份已带别的文件号、又有别的电脑够得到时不认（两份本机拷贝）。
- 第 6 条算出的键与已有登记同一个路径串时认作同一处（方案「不撞键加后缀」的另一面）；第 2 条的内容键只在旧键（路径写法）
  与网络位置不同时起作用。
- 程序目录（规范根）的网络写法按数据目录那条网络路径的前缀换算（映射盘、本机共享都对），换不了就写成带主机名的。
- 退役电脑（`devices.retire`）随即删它的可达行（待认定的「移除」退出条件、并集目录都靠它）。删行在电脑行锁之后：
  同一条连接的心跳与认电脑都先锁连接行，所以不会互锁；第 6 批「移除」一台够得到多条连接的电脑时要先锁那些连接。
- 证据 v1 本批仍冻结「当前上报的那把密钥」（第 3 批换成按账套分派）；凭据成立改为「它的电脑够得到这份账套、同一规范根、
  可写」。**第 2 批不能单独部署**：两台电脑轮流上报时排队的单会被判 `push.native_target_changed`，第 3 批之后才成立。
- 在线、版本判断（`account_masters`、`credentials.online`）仍看当前上报的那台，第 3 批换成池的判据（`pool.state`）。
- 网页的「预选默认账套」（`targets.js` / `scheduled-tasks.tsx` 读 `config.account_set`）随中和自然拿不到 Express 的默认值，
  退回各自记住的选择；没改前端文案与代码。
- 补丁另给有效 Express 密钥打「整份重报」（方案 4.1 没写）：可达行以电脑此刻的名单为准，不只靠补丁时存下的目录。

### 第 2 批对抗审查与修复（2026-10-16，本地提交，未推送）

审查范围 `f87a0585..d99c1b5b`，审查者的复现脚本跑真 `plan_report` / `collector.ingest` / `pool`。逐条核实后：

| # | 结论 | 处理 |
|---|---|---|
| 1 高：旧槽位密钥改挂到同一台电脑已认领的记录后，同一份账套拆成两个规范键 | 复现属实 | `pool.move_device`：`devices.hello` 改挂时、`superseded` 吊销时，先把旧电脑在这条连接上的可达行与 `scope_device` 转给那台（那台在等旧电脑这份的待认定行由转来的取代），再退役旧电脑；`forget_device` 只用于真正移除。那台第一次整份上报按第 3 条（登记还没报过文件号）认回今天的键 |
| 2 高：第 3 条按本机路径把已登记的键改认成另一份物理账套 | 复现属实 | 第 3 条只在登记还没报过文件号 / 网络路径，或网络路径（别名归一，都没有也算）与内容键都相同时认；`_refresh_registration` 不覆盖已有的网络路径 |
| 3 中：克隆盘上两台的本机拷贝按文件号并成一份 | 复现属实 | `Registered` 带 `scope_device` / `foreign`（只属于另一台、那台还够得到）；没有网络路径的上报行第 1 条不认 `foreign` 的；第 4 条（旧版）同样不认，键带这台的主机名（网络路径写不成带主机名的照认） |
| 4 中：第 6 条同一路径串不看文件号冲突 | 复现属实 | 双方文件号不同、别的电脑 7 天内还按原文件号够到它（`others_uids`）→ 待认定（不派写入，不覆盖文件号），原文件号没人再报了才认。选「待认定」而不是「区分后的键」：网络路径写不出另一个合理的路径键 |
| 5 中：认不出的本机写法退回原路径，主档记到别人名下、刷新按「所选」领错 | 复现属实 | `pool.canonical` 只按这台状态正常的可达行换，认不出回空串；空串时不记主档、不发布按账套快照、不把按账套的读失败标到端点目录上；`refresh.lease` 的第 1 档自然不成立 |
| 6 中：并集超过 5000 行整个心跳失败 | 复现属实 | `pool.listing` 按「可写、有网络路径的在前，再按规范键」截到 `MAX_ACCOUNT_SETS`，截掉的份数记在端点目录指针 `account_sets_dropped`（`snapshots.publish` 的 `note`，不进快照内容） |
| 7 中：所选账套按电脑存，两条连接互改 | 属实（erpcowork 两条启用的 Express 连接） | `selection_json` 改成 `{连接: {所选与 6 个科目}}`；`devices.state / member / record_report / seed_state` 都带连接，补丁按连接「没有才补」。本批未上任何环境，没有旧格式数据要迁 |
| 8 低：规范根「从空补一次」与已冻结空根的未了结推送冲突 | 属实 | 被未了结推送记录引用的登记（`protected()`，心跳与补丁都传）不补空规范根，推送了结后的下一次整份上报再补 |
| 9a 低：密钥全吊销、电脑没退役时可达行还在 | 属实 | `devices.after_revoke`（离开、管理吊销、`superseded`、旧槽位重置）：这台在这条连接上一把有效密钥都不剩就删它在这条连接上的可达行（`forget_device(connection=…)`）；一把都不剩的（`retire_orphan`）在锁电脑行之前删各条连接的 |
| 9b 低：两台轮流上报时连接行与 `_preparation_binding` 来回翻转 | 属实，已知 | 归第 3 批（证据换成按账套分派时一并去掉），本批不改 |

与审查建议的出入：第 1、4 条（旧版）的 `scope_device` 判据放宽为「属于另一台、且那台还够得到」，不是「为空或等于本设备」：
旧槽位重置（同一台重新配对）会建新的旧电脑，旧的那台可达行已删，按严格判据今天的键会被拆开。认上这类登记时 `scope_device`
归这台。补丁（`seed_legacy`）里登记先按目录建、后补引用到的键，目录里的键若被冻结了空根的未了结推送引用，登记的根仍是
今天的根——第 3 批取锁时要按推送记录上冻结的根核这一类（记在第 3 批待办）。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；容器 `pearnly/frappe-sample:16.34.1`、`--network none`、工作树只读挂载）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_express_account_sets.py` | 62 OK（新增 14 项：认定第 1、3、4、6 条的纯函数用例，`ReviewBatch2` 逐条复现 1–6、8、按连接删可达行）；新增的 14 项在 `d99c1b5b` 的源码上全部失败 |
| `tests/test_cowork_cc_desktop_devices.py` | 55 OK（新增：改挂先转后退役、`superseded` 转给自己的密钥、离开只删这条连接的可达行且锁序不变、所选按连接） |
| 审查者复现脚本 `test_repro_b2.py` | 2、4–8 过；1 仍按旧流程只 `forget_device` 不转（修法在 `devices.hello`，由上面两处用例守）；3 的断言是「两份登记」，本修法是待认定、一份登记不被改写 |
| 容器内导入新改的 10 个模块；`tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含 parity） | 导入 OK；184 个文件 182 OK，`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py` 与基线相同的两个失败 |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 87 步全过，退出码 0 |

没做 / 没验：同第 2 批（真实 Site、MariaDB 加锁、HTTP 链、真机在第 7 批）。`move_device` 在真实 MariaDB 上是先删后插（可达行
名字带设备编号），并发只靠连接行锁串行，第 7 批联调时核。

### 第 3 批：写账分派、领取 v2、锁口径 v2、准备证据 v2（服务端，2026-10-16，本地提交，未推送）

提交：`177da234` feat（代码与测试）、本条 docs（ADR-021 第 3 批决定、有意变化与回退前提、架构说明、本记录）。没有新补丁：新字段都可为空，旧行按建立时间 / `actor` / 冻结载荷补位（`queued_at`、`entered_by`、
`payload_version`），锁种类加 `company` 是 DocType 选项，随原生迁移。

做了什么（对照方案第 9 节第 3 行、2.5、2.6、2.10、第 5 节第 4–8 条）：

- 分派纯函数 `S/companion/route_contracts.py`〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕：每份账套的排头、录入人自己的电脑 → 常驻 → 其他（120 秒换手）、按账套
  黏住（`index_pending` 按文件号；旧版按登记上的「最近 120 秒内写过」近似）、载荷版本（不支持的跳过、池里一台都不支持
  算「需更新」）、`attended`。
- `transport.lease` v2（`S/companion/transport.py`）：每台同时一件（`written_by_device`）；只看这台够得到、可写的账套的排头；
  不归它 / 黏在别处 / 版本不够 / 等锁都跳过看下一份，不再整条连接 `break`；确定失效才隔离；下发副本换成这台可达行的写法
  （`S/push/express/wire_location.py`）并带 `account_uid`、`attended`；电脑行最后记 `index_pending`。回执后：成功记在登记上
  （`pool.note_written`），`[ERR_ACCOUNT_UID_MISMATCH]` 的「等账套」把这台对这份账套的可达行标 stale 并要整份重报。
- 锁口径 v2（`S/push/native/contracts.py`）：`lock_scheme: 2` 只取账套锁（公式不变）+ 按内容键的公司锁，冻结在目标里；
  `frozen_values` 只许 Express 用 v2。账本领取时 v2 记录还让着同一连接同一账套键上已领走 / 结果不明的记录（第 2 批审查留的
  「空根旧单」一项）。
- 准备证据 v2（`S/connections/preparation.py`）：`route`，不绑电脑；主档取账套资料、不含版本；银行窗口按录入人；
  `revalidate(mode='confirm' | 'lease')`，领取只核登记、`route` 与 `basis_hash`（本单用到的主档行，`S/push/express/referenced.py`）。
  读 `credential` 的 6 处：`transport` 不再比密钥；`_still_confirmable` 改判 v2 + `has_writer`；`confirm` 不要密钥（无可写电脑回
  `push.no_writer`）；存货主档数回写与推送记录的存货科目组第 2 批已改读账套资料；`has_reported_credential` → `has_writer`（旧名
  同值保留），`folder_intake.destinations.ready` 读 `has_writer`。准备时不再按载荷版本拦（预检两处去掉）。
- 池的状态 `S/companion/pool.py`（`state` / `for_user`）替换 `credentials.online` 的全部调用（`api.online`、LINE、
  `destinations`、CC 工具、「电脑」列表）；`api.online` 保留 `online`、加 `writers_known` / `needs_update`。D4：Desk 录入入口
  （`targets.js`）、LINE、CC 工具按 `writers_known` 判能不能选（文案沿用现有「CC 桌面版未连接」，新文案在第 6 批）。
  连接域经钩子 `cowork_pool_presence` 取各台在线与版本（`account_masters` 判「电脑离线 / 要升级」不再看连接当前上报的那台）。
- 主连接统一解析 `connections.service.main_connections`，`records` 标 `main`；登记（`credentials._device_connection`）、
  Desk `targets.js`、LINE `_connections`、CC 工具 `intake_options` 都按它认。
- 问责：推送记录 `entered_by` / `payload_version`（不可改）/ `queued_at`（10-16 删）/ `written_by_device`（建索引）/ `write_route`；写入尝试
  `device` / `device_owner` / `route`（不可改）。
- 第 2 批审查 9b：连接的上报记录只留被拦的自动更新，`report_credential` 不再随上报改；两台轮流上报不再重写连接行。
- MR.ERP 服务端直连不变（v1 锁口径、确认不带电脑、执行器不填问责字段）。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；容器 `pearnly/frappe-sample:16.34.1`、`--network none`、工作树只读挂载）：

| 检查 | 结果 |
|---|---|
| `python3 tests/test_cowork_express_dispatch.py`（新，进 CI） | 42 OK：分派规则（三档、D7 按登录人、120 秒换手、只够得到的、版本 / 需更新、按文件号黏住与离线解除、旧版按最近写入近似、排头、`attended`）；位置替换（冻结的不变）；锁口径（v2 无 endpoint、账套锁与 v1 同一把、公司锁跨拷贝相同、只许 Express）；本单用到的主档行（没用到的变了不算、新建对方档按税号）；报文可选键；纯模块不导入框架；领取 v2 编排（跳过不归它的、等锁看下一份、确定失效才隔离、每台一件、120 秒后别的电脑写且不弹框、够不到的永远领不到、`index_pending` 最后记、文件号对不上标 stale 并要整份重报）；账本（证据 v1 两种模式都不成立、领取只核用到的行、`has_writer`、`_still_confirmable`、确认不要密钥 / `push.no_writer`、问责字段、v2 让着同账套旧单、退回排队重记 `queued_at`）；池的状态（关着也算能写、签发人失权不算、需更新、MR.ERP） |
| 变异核对 | 把 transport 里等锁改回 `break`、去掉「每台一件」：对应 2 项失败 |
| `tests/test_cowork_express_account_sets.py` | 65 OK（证据 v2 改写 1 项；新增 3 项：本机写法与可写电脑、写成记登记 / stale 到整份上报恢复、账套主档按池里任一台判在线与升级） |
| `tests/test_cowork_cc_desktop_devices.py` | 57 OK（每台看自己的密钥在线、`for_confirmation` / `online` 下线、`index_pending` 只在变了时写、文件号对不上要整份重报） |
| `tests/test_cowork_connection_read_locks.py` | 8 OK（新增主连接：最早建、启用优先、不挂客户、不加锁） |
| `tests/test_companion_contract.py` | 56 OK（新增 `WriteDispatch`：四键 + 登记的可选键、今天客户端只读三键、`index_pending`、`ERR_ACCOUNT_UID_MISMATCH` 同名并据此标 stale、替换的位置键覆盖 `_task_settings` 读的键） |
| 改过的旧测试 | `any_set_catalog` / `report_writes` / `account_caches`（上报记录不再存绑定与版本、证据 v2）、`assistant_tools`（主连接、D4）、`express_preflight`（准备时不拦版本）、`companion_credentials`、`folder_task_batch` / `folder_tasks_mrerp` 替身；容器内 `companion_liveness`（改测 `_fresh`）、`folder_computers` |
| 容器内导入改过的 26 个模块；`tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含源码对照） | 导入 OK；185 个文件 183 OK，`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py` 与基线相同的两个失败 |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check` | PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（新 CI 步骤含 dispatch / preflight / native 契约三个测试） | 88 步全过，退出码 0 |

与方案的出入及理由：

- 排头按**建立时间**先后取，不按 `queued_at`：回执把单退回排队（等账套、可重试失败）时会重记 `queued_at`，按它排会让同一份账套
  后来的单插到前面，违背「同一份账套严格按排队先后」。`queued_at` 只管 120 秒换手计时（10-16 随换手一起删）。
- 黏住的「离线」用现成的在线判据（任一把密钥 3 分钟内报过到），不是方案写的 120 秒：要精确到 120 秒得每 3 秒领取都写一次电脑行。
- 「一台都没有能写的电脑」时 LINE / CC / Desk 仍用现有文案「CC 桌面版未连接」，「电脑离线」（橙、仍可选）「需更新」等新文案在第 6 批；
  `scheduled-tasks.tsx` 等 React 端读 `online` 的照旧（第 6 批随前端构建改）。定时任务推送的 D4（不在线也确认排队）属第 4 批。
- 新建对方档的「用到的行」按税号（没有税号按名字）取：后来出现同税号的档时转人工，不会再新建一份重复的（方案只写了「引用的行」）。
- 方案 2.5 第 2 步「先做一次不加锁的预查再锁连接行」没意义：认证（`credentials.revalidate`）本来就先锁连接行（加锁顺序不变）；
  改为先看这台有没有可写的账套，没有就不查排队单。
- `credentials.online`、`for_confirmation`、`_reporter`、`_online` 删除（调用方都已换掉）；`refresh.desktop_version` 删除。

没做 / 没验：真实 Site 上的迁移、MariaDB 加锁（`_older_writer` 的加锁读、领取时逐份账套的锁等待）、HTTP 链、真机（第 7 批）。
实验室脚本 `cowork-preparation-confirm-lab-verify.py`、`cowork-companion-transport-lab-verify.py` 等还按证据 v1 / 单槽上报断言，要真实
Site，第 7 批按新口径重写。

留给后面几批：

- 第 4 批：定时任务读者三元组与补丁第 4 步、定时任务推送按池口径（D4：不在线也确认排队，不再 10 分钟重试 / 6 小时记失败）、
  CC 工具与 `fetch` 的读者、按设备在场、按需请求定向与回读的通用位置键、提醒 / 简报 / 限流按设备、`all_members` 与补丁第 5 步；
  「电脑」列表（`rules.computers`）改成每台电脑一行（本批只把在线换成池的判据，电脑名取最近报到的那台）。
- 第 5 批（CC 桌面版 1.6.0）：领取请求带 `index_pending`（`IndexBatch` 里还没重建的账套文件号）；任务读 `attended`（false 不弹框、
  按「等账套」回报）、写前用本机文件号核 `account_uid`，对不上回 `waiting_lock` + `[ERR_ACCOUNT_UID_MISMATCH]`；有人值守时探任务账套、
  先放租约再提示；`tests/test_companion_contract.py` 的 `WriteDispatch` 补客户端一侧（可选键、错误码同名）。
- 第 6 批：录入入口的状态与文案（「已连接 / 电脑离线（仍可选）/ 没有能写的电脑 / 需更新」，`needs_update` 按账套问
  `api.online(connection, account_set_key)`）、推送记录详情「由 {电脑} 写入」（`written_by_device`）、CC 工具多给 `writer`、
  `scheduled-tasks.tsx` 改读 `writers_known`；对话页每次打开带设备密钥调 `register`（第 5 节第 7 条的契约测试）。
- 第 7 批：真实 Site 迁移与回读（`config_json` / `record_revision` 未变、v1 排队 / 领取中为 0）、部署前 v1 单只读统计与回退演练或书面确认
  不可回退（ADR-021「兼容」节的回退前提）、两台电脑场景 ①–⑤、3 租户 × 10 台并发压测领取（每份账套只看排头的查询量、连接行热锁）。

### 第 3 批对抗审查与修复（2026-10-16，本地提交，未推送）

审查范围 `bf8aa969..3f412e29`，9 条（无高危）。提交：`53e8f68e` fix（服务端与测试）、`483cd2f2` fix（CC 定时任务页认主连接）、
本条 docs（ADR-021 第 16–18、20、21 条与有意变化「主连接」、架构说明、本记录）。没有新补丁、没有新词条、没有改已有文案。

| # | 审查结论 | 处理 |
|---|---|---|
| 1 中 | 领取核对把整张科目映射表（本站不分工作空间）与按录入人的银行流水窗口都算进 `basis_hash`，D4 让单子排得久，排队期间改一条无关映射就把整队转人工 | 已修：`push.express.referenced.used_facts` 只取本单用到的——映射只留 `erp_code` 是载荷里某个科目的行、客户映射（本就只有这条连接的客户）与本单卖方的供应商画像原样、主档行照旧；不含银行流水（方案第 8 节第 30 条）。`preparation.basis(used)` 只算指纹。代价写进 ADR 第 18 条：本单走兜底科目、后来才给它的类别加映射，领取时看不出（人确认仍整份比对） |
| 2 中 | 领取在锁着连接行时逐份账套查可达、登记、解析旧行载荷，可能再走整份 `ledger.lease` | 已修：可达（`pool.reachers_of`）、登记（`pool.registrations_of`）、旧行载荷版本（`queue_heads.payload_versions`）各一条查询；`ledger.lease` 前先 `ledger.lock_busy` 不加锁看写者锁与该让着的记录，占着的跳过、不做整份重核。连接行锁等待第 7 批压测量 |
| 3 中 | `MAX_PENDING_SCAN=5000` 跨账套截断，一份账套积压能饿住别的账套；v2 排头遇到全池只支持 1 时既不领也不隔离，堵住后面的 v1 单，且第 6 批前用户看不到 | 已修：`companion.queue_heads.heads` 按账套分组取最早建立时间再取那一刻的单（无 `LIMIT`；`route_contracts.heads` 按名字取第一张），取这台够得到的每一份账套（不再只看前 50 份）。全池够得到它的电脑都报过更低的最高版本（`NEEDS_UPDATE`）时隔离为人工，原因码 `payload_version_outdated`（第 3 批之前准备时就拦下的同一个码，网页 / 导出按现有词条 `intake.error.payload_version_outdated` 显示），同一账套后面的单照常写；有一台支持（`VERSION`）或旧版不报版本的照旧跳过不隔离 |
| 4 中 | `scheduled-tasks.tsx` 两处仍取「列表第一条」 | 已修：`mainOf` 只认 `main`（与 `targets.js` 同口径，不退到别的连接）；`HelperConnect` 同改。有意变化补登记进 ADR-021：没有主连接使用权、只有后建连接使用权的用户整个看不到 Express。第 7 批前只读核 erpcowork 那 1 把有效密钥、13 张人工单、现有规则各挂在哪条连接上，见下方第 7 批前提 |
| 5 低 | `has_writer` 不核签发人，`pool.state` 核 | 已修：一个判据 `connections.pool.writers(doc, key, peers)`：登记着、可达可写、在 `presence` 里（密钥没吊销、电脑没退役）、至少一把密钥的签发人此刻仍能用这条连接（新 `connections.access.usable_by`，不加锁；`credentials.issuer_valid` 改为转调它）。`has_writer` 与 `companion.pool.state` 都用它 |
| 6 低 | 公司锁取准备那一刻的内容键（不带详情上报建的登记为空）；部署前 v1 单没有公司锁，`_older_writer` 只看同账套键 | 已修：`_older_writer` 再按这份账套**现在**登记的内容键查同一工作空间里领走 / 结果不明的记录（联登记表）；可能在别的连接上，不加锁读（不抢别的连接的行锁、不在快照隔离下撞 1020）。登记一直没有内容键时仍挡不住，第 7 批前核 erpcowork 13 张人工单里结果不明的张数与它们账套的内容键 |
| 7 低 | 池状态的排头查询过滤与领取不同 | 已修：`pool.state` 与 `transport` 共用 `queue_heads.Q_HEADS` |
| 8 低 | 一台电脑挂两条连接时「每台一件」守不住 | 不改代码，写进 ADR 第 16 条与 `_busy` 注释：只在一条连接内严格成立。严格做要在加锁顺序（Connection → Credential → 池各行 → Device）里提前锁电脑行，与回执路径顺序相反，不值 |
| 9 低 | MariaDB 11.8 快照隔离下 `_locks` 的加锁读可能撞 1020（锁行跨连接共用） | 未改代码：`lock_busy` 的不加锁预看减少走到加锁读的次数，但撞上时整次领取仍失败。列入第 7 批并发压测清单（下方） |

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；容器 `pearnly/frappe-sample:16.34.1`、`--network none`、工作树只读挂载）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_express_dispatch.py` | 50 OK（42 → 50）：领取只核本单用到的映射 / 画像 / 主档、无关映射与银行流水变了 `basis_hash` 不变、本单那条映射改了会变；全池不支持 → `payload_version_outdated` 隔离且后面的单照领、有一台支持或旧版不报 → 跳过不隔离；批量读（排头、可达、登记各一次）与锁被占时不做整份领取；旧行载荷版本一条查询；`has_writer` 走 `pool.writers`；`_older_writer` 按现在登记的内容键不加锁再查；`lock_busy` 不加锁；`payload_version_outdated` 是隔离原因；`pool.state` 与领取同一条排头查询；`Q_HEADS` 在 SQLite 上实跑（3000 张积压不挡别的账套、过滤与领取一致、同一时刻按名字、旧行读回冻结载荷版本） |
| `tests/test_cowork_express_account_sets.py` | 67 OK（新增：`reachers_of` / `registrations_of` 一次取多份；`pool.writers` 关机也算、签发人失权 / 密钥吊销 / 没登记不算） |
| `tests/test_cowork_cc_desktop_devices.py` | 57 OK（`issuer_valid` 转调 `access.usable_by`） |
| 容器内导入改过的 13 个模块；`tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含源码对照） | 导入 OK；185 个文件 183 OK，`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py` 与基线相同的两个失败 |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 88 步全过，退出码 0（代码提交后与本条文档后各跑一次） |

没做 / 没验：同第 3 批（真实 Site、MariaDB 加锁与执行计划、HTTP 链、真机在第 7 批）。`Q_HEADS` 的分组子查询在推送记录上没有
（connection, status, account_key, created_at）组合索引，与以前 `_pending` 一样按连接扫；第 7 批压测量它，慢了再加索引（原生
`on_doctype_update`）。

第 7 批前提与压测清单（本条新增）：

- 只读核生产 erpcowork：两条启用的 Express 连接里哪条是主连接（最早建、启用、不挂客户）；那 1 把有效密钥、13 张人工单、现有
  定时任务规则各挂在哪条连接上——挂在后建那条的，切换后网页会失去 Express，要先定迁移办法再部署。
- 部署前 v1 记录里结果不明的张数（目标 0）与它们账套的内容键；不为 0 时要人先在 ERP 核对解除。
- 并发压测（3 租户 × 10 台）：连接行锁等待、`Q_HEADS` 执行计划与耗时、领取时 `_locks` 加锁读撞 1020 的次数（锁行跨连接共用，
  erpcowork 两条连接）、同一台电脑挂两条连接时「每台一件」。

留给后面几批（在第 3 批所列之上更新）：

- 第 4 批：照第 3 批所列（定时任务读者三元组与补丁第 4 步、定时任务推送按池口径 D4、读者 / 按需请求按设备、「电脑」列表每台一行等）。
  `folder_intake.destinations.ready` 读 `has_writer`，本批起与池的状态同一判据，第 4 批改推送口径时不用再对齐。
- 第 5 批（CC 桌面版 1.6.0）：照第 3 批所列。
- 第 6 批：照第 3 批所列；`scheduled-tasks.tsx` 已认主连接，第 6 批只改它读 `writers_known` 与新文案；推送记录上「需更新」现在
  是人工 + `payload_version_outdated`，第 6 批的「需更新」状态按账套问 `api.online` 时与它一致。
- 第 7 批：照第 3 批所列，加上上面的前提与压测清单；实验室脚本按证据 v2 / 新领取口径重写。
- 第 8 批：照方案。


### 第 4 批：读文件夹的电脑、按需请求按电脑、全所可用（服务端，2026-10-16，本地提交，未推送）

提交：`2ad8fab5` feat（代码与测试）、本条 docs（ADR-021 第 25–32 条与有意变化、架构说明、本记录）。补丁 `v0_0_21_reader_devices`（方案 4.1
第 4 步）、`v0_0_22_express_all_members`（第 5 步）。新词条只有 4 个字段标签（四种语言）；没改已有文案；前端（`frontend/assistant`、Desk 的
定时任务页）不动，属第 6 批。

做了什么（对照方案第 9 节第 4 行、2.7、2.9、4.1 第 4–5 步）：

- 读者三元组：`folder_intake.readers`（新）解析（电脑，成员连接）；任务、文件、运行、公司文件夹加 `reader_device`；同步只下发这台电脑这个成员的
  任务（`machine._sync` 按 `reader = 成员连接 AND reader_device = 电脑`），`_rule` / 文件状态 / 取结果表格同时核电脑；本机情况（`agent`）按
  （电脑，成员连接）存；表单选项、`computers()` 一台电脑一行；每台最多 50 个按电脑计；同一台电脑不分成员按根目录比重叠；读者被移除标
  `reader_gone`；电脑改挂（`devices._move`）经钩子 `cowork_reader_device_moved` 转移任务、文件、待发简报的运行、公司文件夹。
- 旧写法：只给连接时按根目录认电脑（改任务时原来那台还报着就仍是它）；任务列表 `reader` 仍是成员连接，另给 `reader_device` / `reader_id`，
  Desk 与 React 定时任务页、主档页的公司文件夹在第 6 批前照常可用（一条连接上只有一台电脑时与今天完全一样）。
- CC 工具：选项的 `reader` 给读者编号；改任务以现值为底时传回读者编号（不被认成换了文件夹）；卡片上的根目录名按编号取；任务列表带 `reader_gone`。
- 按需请求：`Cowork Companion Request` 加 `target_device` / `account_key` / `lease_device`；`fetch` 定向给持有根目录的电脑；回读与科目历史带
  规范键、按分派顺序交给够得到它的电脑、位置换成它的写法（`request_contracts.LOCATION_KEYS` / `localize`）；在场与租约按电脑；`waiting` 按
  电脑 / 按够得到账套的各台、版本取电脑行；领取只扫这台能领的、逐件加锁；过期不再锁整条连接。
- 提醒与简报按读者（电脑|连接）分组（部署前的提醒去重键仍认）；限流按推送连接 + 账套；定时任务推送按池的口径（D4，`destinations.queueable`）。
- 全所可用：连接加 `all_members`；`connections.access.listed` 一个判据替换 `access.require_connection`、`service.records`、`service.read_for_log`
  三处；新建 Express 池默认全所可用；连接详情带 `all_members`。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；需要 Frappe 的在一次性离线容器 `pearnly/frappe-sample:16.34.1`、`--network none`、
工作树只读挂载里跑）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_companion_requests.py` | 33 OK（新增 `PoolRequestTests` 7 项：定向只给持根目录的电脑、没定向的谁都行、在场与租约按电脑、读账套先给发起人的电脑且换成它的写法、120 秒或发起人电脑不在就换人、`waiting` 问够得到账套的各台、过期只锁到期的那件；契约 `target` / `localize`） |
| `tests/test_cowork_folder_intake.py` | 119 OK（同步按电脑下发与接手、运行记电脑且别的电脑报的不收、文件按电脑认、D4 排队、公司文件夹按根目录认电脑 / 两台各一行 / 只给连接不猜 / 接手老行、`readers.adopt` 只接手报了根目录的、`readers.moved` 四张表、读者编号、补丁 `v0_0_21` 回填与留空） |
| `tests/test_cowork_folder_task_batch.py`（用真 `readers`） | 28 OK（新增 `ReaderDevices` 6 项：池里每台一项、只给连接按根目录认 / 编号要持根目录 / 改任务保留原电脑、50 个按电脑、同一台电脑跨成员重叠、`reader_gone`、`waiting` 两种写法） |
| `tests/test_cowork_folder_reminders.py` | 42 OK（新增：同一个池两台电脑各一条提醒、部署前的去重键仍认、各一份简报） |
| `tests/test_cowork_connection_read_locks.py` | 11 OK（新增：`listed` 一个判据、MR.ERP 不看 `all_members`、列表对全所可用的池、补丁 `v0_0_22` 只设主连接、不改修改时间与版本） |
| `tests/test_companion_contract.py` | 58 OK（新增 `PoolRequests`：位置键 = 两种读账参数都有的键 = 客户端两个处理函数读的键；领取请求体不变；服务端按电脑与账套键定向） |
| 改过的旧测试 | `folder_tasks_mrerp`（真 `readers`）8、`assistant_tools` 175（新增：池里每台电脑一项、改任务保留读者编号）、`readback` 41、`account_suggest` 34、`companies` 54、`cc_desktop_devices` 57（改挂时钩子随可达行一起转）、`express_account_sets` 67、`delete_everything` 7 — 全部 OK |
| 变异核对 | 领取不按电脑过滤：契约测试失败；轮不到也领：请求测试 2 项失败；同步不按电脑：文件夹测试失败 |
| 容器内导入改过的 23 个模块与两个补丁；`tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含源码对照） | 导入 OK（钩子 `cowork_reader_device_moved` 解析到 `readers.moved`）；185 个文件 183 OK，`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py` 与基线相同的两个失败；`test_cowork_folder_computers.py` 改成按电脑一行（新增池里两台各一行） |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check` | PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 88 步全过，退出码 0 |

与方案的出入及理由：

- 读者对外编号「电脑|连接」只给 CC 工具与第 6 批；任务列表的 `reader` 与读者选项的 `connection` 仍是成员连接，第 6 批之前的网页 / Desk 页拼下拉
  值（`reader|root|folder`）不会被编号里的竖线拆错。只给连接时按根目录认电脑（方案没写旧写法怎么认）。
- 文件行不由补丁回填：没记电脑的文件按连接认（在途的照常走完）；电脑改挂时文件不论走到哪一步都转（之后还要按它取结果表格）。
- 补丁认不出电脑（读者连接上没有有效密钥）的任务，由这条连接上报了它那个根目录的电脑来同步时接手（`readers.adopt`，条件更新）；方案只写了回填。
- 〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕读账套请求的「在线」按这一种按需请求 90 秒内来领过算，不按心跳：心跳在、却从不来领按需请求的旧版不该让别的电脑等 120 秒。不黏住、不看载荷版本（只读）。
- 读账套请求的资格是可达行状态正常，不要求能写（读不写）。
- 公司文件夹（ADR-020，方案之后才有）一并按电脑：绑定按根目录认电脑，只给连接的解绑在几台电脑各有一行时不删。
- 定时任务推送 D4 用 `destinations.queueable`（`writers_known`）；`destinations.online` 保留给 CC 工具报「CC 桌面版在线」。
- 全所可用补丁只设每个归属的 Express **主连接**（同方案）；erpcowork 后建的那条 Express 连接仍按名单。

没做 / 没验：真实 Site 上的迁移与两个补丁、MariaDB 加锁（领取按需请求时单行加锁读在快照隔离下撞 1020 的情形与以前相同，列入第 7 批压测）、
HTTP 链、真机（第 7 批）。`test_cowork_companion_reports_source_parity.py` 等源码对照没跑（本批没改上报解析）。

留给后面几批（在第 3 批所列之上更新）：

- 第 5 批（CC 桌面版 1.6.0）：照第 3 批所列；桥交回的文件夹绑定可直接带 `reader_device`（服务端已收）；请求体不变。
- 第 6 批：定时任务页（React 与 Desk `intake-folders.js`）、主档页的公司文件夹改用 `reader_id` / `reader` 编号作下拉值与解绑参数，「电脑已移除」
  按 `reader_gone` 显示；「连接」列表一台电脑一行已由服务端给出；连接详情的 `all_members` 是否在设置页显示由第 6 批定。
- 第 7 批：照第 3 批所列，加上补丁 `v0_0_21` / `v0_0_22` 在真实 Site 上的回读（任务 `reader_device` 回填数、主连接 `all_members`、
  `record_revision` 未变）、两台电脑各读各的文件夹（场景 ②④）、按需请求在两台之间的分派。

### 第 4 批对抗审查与修复（2026-10-16，本地提交，未推送）

审查范围 `f1cb3c22..bf97a4df`，9 条（3 中 6 低，无阻塞）。提交：`f32bf8ab` fix（服务端与测试）、本条 docs（ADR-021 第 26–29、31、32 条与
有意变化「读者按电脑」、本记录）。没有新补丁、没有新词条、没有改已有文案；前端不动（第 6 批）。

| # | 审查结论 | 处理 |
|---|---|---|
| M1 | 公司文件夹主键就是 `binding_key`，补丁回填 / 电脑改挂后字段变了键没变；再绑一行认不出电脑的同一家公司时算出同一个键，撞主键报错 | 已修：`company_folders.bind` 新建时算出的键已被占着就换 `uuid4().hex`（同一时刻两次同样的新建仍撞唯一键，与以前一样只成一行）；测试 `test_a_drifted_key_does_not_block_binding_again`，替身写入撞键时报错 |
| M2 | 根目录编号不含电脑，几台都映射 `S:\` 时只给连接按「最近同步」猜读者，可能读到别人电脑的同名文件夹 | 已修：`readers.resolve` 几台都报过时只认 `keep`（改任务时原来那台）或 `name`（CC 桌面版绑定里带的电脑名，与同步报的 `device` 同出 `_device_name`）对上的唯一一台，否则 `root_invalid`；CC 卡片上的根目录名认不出时不显示（`assistant.tools._root_labels`）。ADR 第 26 条写明第 4 批与第 5、6 批一起发布；契约测试 `test_a_binding_names_the_computer_the_same_way_as_the_sync`。顺带修了测试替身：`test_cowork_folder_task_batch` 的 `get_value` 以前不回原任务的读者，「改任务保留原电脑」那项是靠最近同步碰巧过的 |
| M3 | 新建 Express 池默认全所可用，`configure` 却不收 `all_members`，现有界面改名单实际无效 | 已修：`configure` / `api.configure` 收 `all_members`（不给不变、给了照存、MR.ERP 给真值拒；请求指纹只在给了时带它，没给的与以前同一个指纹）。界面开关在第 6 批；ADR 第 31 条写明第 6 批之前名单编辑对全所可用的池不起作用、不单独部署 |
| L1 | 领取最多扫 50 件，只有别的电脑够得到的读账套请求能占满名额，挡住定向给这台的取文件 | 已修：`pool.readable_keys`（一条查询）+ SQL `AND (IFNULL(account_key,'')='' OR account_key IN …)`；测试：55 件 PC2 够不到的读账套请求排在前面，PC2 仍领到后面的取文件 |
| L2 | 认不出电脑时 `_candidates` 查 `presence(conn, "")`（部署前按连接的在场键）、`_busy` 算没记电脑的旧租约，可能建出不定向的取文件请求 | 已修：`fetch._reader` 电脑为空而连接上有电脑时 `root_invalid`；`request_service._candidates` 给了空电脑一台都不问（回 offline）。两项测试 |
| L3 | 补丁 `_device` 没排除退役电脑（退役不吊销密钥） | 已修：`JOIN Cowork CC Desktop Device … retired_at IS NULL`（并核该列存在）；补丁测试核查询 |
| L4 | 电脑改挂后目标电脑上可能两条一样的任务 / 两行一样的公司文件夹 | 已修：`readers.moved` 先把与目标电脑上启用任务根目录相同、文件夹相同或互相包含（`contracts.nested`）的转来任务停用（`enabled=0`、`rule_revision+1`，不删）；这家公司在目标电脑这条连接上已有绑定的，旧记录那行删掉（留目标的）。测试 `test_a_moved_computer_does_not_end_with_two_of_the_same` |
| L5 | 一个负责人的任务分在 N 台电脑上，提醒 / 简报变 N 份 | 不改代码：与 2026-10-13「按电脑分开」口径一致，写进 ADR 第 29 条；要按人合并时再改 |
| L6 | 没挂电脑的密钥（池里按密钥编号算一台）`hello` 挂上电脑时不转移按密钥编号接手的东西 | 已修：`devices.hello` 在 `key.device` 为空时，改挂到已认领的电脑前、或新建电脑后，`_move(连接, 密钥编号, 电脑)`（可达行 + 钩子 `cowork_reader_device_moved`）；两项测试（新建、改挂到已认领的），加锁顺序照核 |

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`；Frappe 依赖的在一次性离线容器 `pearnly/frappe-sample:16.34.1`、`--network none`、工作树只读挂载、
`PYTHONPATH=apps/pearnly_cowork:apps/pearnly_core` 照 CI 逐个文件跑）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_folder_intake.py` | 122 OK（新增 M1、M2 `resolve` 不猜、L4 两项；L3 补丁查询） |
| `tests/test_cowork_folder_task_batch.py` | 28 OK（`ReaderDevices` 加两台同根目录只给连接拒、编号照常、改任务保留原电脑真按 `keep`） |
| `tests/test_cowork_companion_requests.py` | 36 OK（新增 L1 扫描名额、L2 空电脑不问、取文件认不出电脑拒 / 没有电脑仍回离线） |
| `tests/test_cowork_connection_read_locks.py` | 12 OK（新增 `configure` 保留 / 改 `all_members`、MR.ERP 拒、重放同指纹） |
| `tests/test_cowork_cc_desktop_devices.py` | 58 OK（L6 新建与改挂两项，加锁顺序） |
| `tests/test_companion_contract.py` | 59 OK（新增绑定与同步报同一个电脑名、服务端都过 `contracts.device`） |
| 变异核对 | 去掉 M1 换键、M2 拒、L2 两处、L4 删旧行、L6 转移，各自的测试都失败 |
| 容器内 `tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（188 个文件，不含 `*source_parity*`） | 183 OK；`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py`（缺 `docx`）与基线相同的两个失败；三个 `*_parity.py` 是要 `--source` 的对照脚本，不是单元测试 |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 88 步全过，退出码 0 |

没做 / 没验：同审查所列——真实 Site 上的迁移与两个补丁、MariaDB 加锁（1020）、HTTP 链、真机留第 7 批；`readers.moved` 的停用与删除只在替身上验过
SQL 形状，真实库里同表子查询的 `SELECT … IN (SELECT …)` 留第 7 批迁移回读时一起看。

发布前提（在第 3、4 批所列之上）：第 4 批与第 5 批（CC 桌面版带电脑编号）、第 6 批（网页用读者编号、`all_members` 开关）一起发布，不单独部署。

### 第 5 批：CC 桌面版 1.7.0（2026-10-16，本地提交，未推送）

提交：`cff95cd4` feat（桌面端、契约测试、一条新词条、服务端注释里的版本号）、本条 docs（ADR-021 第 33–40 条、有意变化与兼容）。版本号
1.7.0（方案写的 1.6.0 已在 10-15 给科目历史 / 公司文件夹那一版用掉，`main` 上就是 1.6.0）；没有发布。没有新补丁；对话页（`frontend/assistant`）
不动，属第 6 批。

做了什么（对照方案第 9 节第 5 行、2.2、2.3、2.5「桌面端的配合」、2.8、第 5 节）：

- 设备密钥（`C/device_link.py`、`C/profile_store.py`）：第一次运行生成 32 字节随机数，DPAPI 加密存在 `companion.json` 的 `device.key`，一经写下
  不换（读得早的配置副本写回时沿用文件里的那把，本进程的写串行）；托盘启动时先生成；桥 `state()` 给 `device: {key, name}`，`features` 加 `devices`。
- 认电脑：Express 成员在取任务线程（`multi_profile_worker` 每轮先 `announce`）、MR.ERP 只读成员在文件夹线程里各发 `device_hello`（设备密钥、
  电脑名 = 文件夹同步与绑定里的同一个 `_device_name`、常驻（10-16 删）、`host_names`），内容变了马上再发；`superseded` 时本机同一条连接还有别的成员就删掉
  这个，否则与 `claimed_elsewhere` 一样给成员标 `device_state`（停用、设置里 `attention`、桥给页面 `device_state`），配对写入新密钥时清掉。
  401 / 403 / 404 当旧服务端（半小时后再问），网络 / 报文问题五分钟后再试，都不动成员。
- 断开：桥的 `remove` 改成后台任务 `disconnect`，先 `device_leave` 再删本机成员（旧服务端、连不上照样删）；改连别家公司时原来的成员也先各自 leave。
- 身份（`C/device_facts.py`，只读）：`uid` = `GetFileInformationByHandle`（`FILE_READ_ATTRIBUTES` + `FILE_FLAG_BACKUP_SEMANTICS`）的卷序列号 +
  64 位文件号，文件号 0 当取不到，不用 `FileIdInfo`；`share` = `WNetGetConnectionW`（持久、非持久映射）或 `LanmanServer\Shares`（取最深的共享）
  换成 `\\本机名\共享名\…`；`host_names` = 本机名、NetBIOS、DNS 全名 + 有默认网关、开着、非回环 / 隧道 / 拨号、名字不含 WSL / Tailscale /
  ZeroTier / WireGuard 的网卡 IPv4（不含 169.254），最多 32 个。心跳整份名单（`account_probe.account_sets_payload`，一次扫描共用一个换算器）与配对
  上报都走 `account_probe.with_identity`；文件夹规则的网络路径（`folder_plan.share_path`）用同一套换算。
- 领取：每次带 `index_pending`（全部成员 `IndexBatch` 还没重建的账套目录的文件号，十分钟缓存，空列表也报）；任务读 `attended`（只认布尔）。
- 写：任务记录（写过就照上次回执）挪到找账套之前；带 `account_uid` 时找不到账套或文件号确实不同 → `waiting_lock` +
  `[ERR_ACCOUNT_UID_MISMATCH]`，并 `master_refresh.rescan_soon()` 让下一次心跳整套重扫；读不出文件号照旧按路径写。
- 询问框（`C/gui_tray_workers.py`）：常驻（10-16 删，只看 `attended`）或 `attended=false` 不问；有人值守时探**这张单的**账套（`worker.task_account_dir`，只在授权根目录下），
  被占用先按「等账套」放掉租约再问，「终止」作用到之后领到的那张。
- 没有 Express 也能加入：Express 配对码、本机找不到 Express（没找过先找一次）→ `pair_folder(pool=True)` 当只读成员加入池；之后「更换… / 重新查找」
  找到 Express 时按原密钥重新配对成能写账的成员（`pairing.pair` 现在写 `kind="express"`）。
- 新词条 1 条：`intake.error.ERR_ACCOUNT_UID_MISMATCH`（四种语言，`tests/test_cowork_reason_language.py` 要求桌面端每个错误码都有词条），翻译包
  按 `i18n-build.py` 重新生成；桌面端界面没有新文案，没改已有文案。服务端只改了注释里的版本号（1.6.0 → 1.7.0）与 `device_contracts` 的说明。

证据（本机 macOS；桌面端测试用 Python 3.11 + 按 `packaging/requirements-build.txt` 装的非 Windows 依赖（PySide6 6.11.1、httpx、dbf、pytest），
虚拟环境在本会话草稿目录；仓库测试用 `PATH=$HOME/pearnly-devtools/bin:$PATH` 的 3.12）：

| 检查 | 结果 |
|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/） | 879 passed, 41 skipped（基线 809 passed, 39 skipped；新增 `test_device_facts.py` 20 项（2 项只在 Windows 跑）、`test_device_link.py` 27 项、`test_write_dispatch.py` 23 项，另在 `test_folder_member` / `test_multi_profile_integration` 各加 1 项；改了 6 个旧测试的替身或断言：`poll(index_pending=…)`、扫描传 `locator`、桥的 `features`、快照多 `device` / `device_state`、询问框先放租约） |
| 变异核对 | 去掉写前核文件号、领取不带 `index_pending`、401 不当旧服务端、忽略 `attended`、`device_state` 不停用、网卡不看默认网关：对应测试各自失败 |
| `python3 tests/test_companion_contract.py` | 66 OK（59 → 66：`DeviceMethods` 补客户端一侧——两条路径、报文键与状态同名、`MAX_HOST_NAMES`、设备密钥形状服务端收、401 / 403 / 404 = 旧服务端且断开照样删本机、桥交出设备密钥；`WriteDispatch`——任务读 `attended`、领取请求带 `index_pending` 且上限相同、`ERR_ACCOUNT_UID_MISMATCH` 两端同名并按 `waiting_lock` 回报、`account_uid` 是服务端下发的键；`AccountSetIdentity`——`IDENTITY_KEYS` 进两处白名单、心跳与配对同一个 `with_identity`、只用 `GetFileInformationByHandle`） |
| `python3 tests/test_cowork_reason_language.py` | OK（新词条） |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check` | PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 88 步全过，退出码 0（代码提交后先跑一次，缺一条词条失败，补上后在本条文档提交的 HEAD 上重跑全过） |

没做 / 没验（只有真 Windows 能验的）：

- CI 的 Companion checks（Windows 全量测试 + 打包）没跑：本地不推送。其中 `test_device_facts.py` 的两项真接口测试（目录文件号非零且稳定、主机名第一个
  是本机名、`GetAdaptersAddresses` / `GetComputerNameExW` / `WNetGetConnectionW` / 共享表不抛）、以及原有 39 项 Windows 专属测试只在 Windows 上跑。
  ctypes 结构体只在 macOS 上核了 x64 偏移（`FirstGatewayAddress` = 208、`BY_HANDLE_FILE_INFORMATION` = 52 字节）并拼假链表解析过，没调过真系统。
- 真 DPAPI 加解密设备密钥；`GetFileInformationByHandle` 经 `FILE_FLAG_BACKUP_SEMANTICS` 打开目录在 NAS / Accserver 上的结果（第 0 批探针用的
  同一种调用，但不是这份代码）；非持久映射与本机共享换算出的 `share` 与服务端主机别名归一能否对上；测试机 WSL / Tailscale 网卡确实被排除。
- 两台电脑的联调：认电脑后整份重报、同一份账套经两台分派、写前核文件号对不上转给另一台、`index_pending` 黏住、替别人写不弹框、有人值守时
  先放租约再问、断开吊销、没 Express 的电脑加入池、配对码抄到两台（`claimed_elsewhere`）——第 7 批。
- 服务端第 1–4 批 + 第 6 批（对话页带设备密钥调 register、`rekey`、`device_state` 的界面）上线前不能发 1.7.0；旧服务端上 1.7.0 照常工作（认电脑当旧服务端，
  多出的键被忽略）。

与方案的出入及理由：

- 版本号 1.7.0，不是 1.6.0（见上）。
- `always_on`（10-16 删）/ `host_names` 只经 `device_hello` 报，心跳与文件夹同步不加（服务端第 1 批只从 `hello` 收；常驻开关一变，各线程重启、马上再认一次）。
- `superseded` 而本机同一条连接没有别的成员时不删成员，标 `device_state` 等对话页 `rekey`：删了会丢掉这个成员的数据根与授权，重新登记时
  `pair` 按连接认回同一个成员、换上新密钥。
- 「终止」不再立刻把这张单回报为终止（租约已经放了），而是作用到之后领到的那张（多半就是它）：同一批里的其余单照旧按终止回报。
- 读不出文件号（非 NTFS、接口失败）不算对不上：否则这台电脑对带文件号的账套永远写不了；服务端交给它本来就因为它报过够得到。
- 新增词条 1 条（服务端的错误原因），桌面端界面无新文案。

留给后面几批：

- 第 6 批：对话页每次打开带 `state().device.key` 调 register v2（`device_name` 用 `state().device.name`）；成员 `device_state` 为 `superseded` 时
  `rekey`、为 `claimed_elsewhere` 时重新登记；「电脑」列表与 ⋯ 移除；`remove` 现在回 job（页面按现有 job 协议等结果）。
- 第 7 批：上面「没做 / 没验」各项；Companion checks 在 Windows 上全绿后再谈发布；发布顺序仍是服务端先、1.7.0 后。

### 第 5 批对抗审查与修复（2026-10-16，本地提交，未推送）

审查在 `248b6cbd` 上读代码（没在真 Windows 上复现），没有阻塞项：1 高、4 中、5 低，全部处理，修复提交 `34c7a682`（基线 `248b6cbd`）。
没有新补丁、没有服务端代码改动；改了第 5 批新加、尚未发布的 1 条词条（四种语言），没改已有文案；前端 / 对话页不动（第 6 批）。

| 级别 | 问题 | 处理 |
|---|---|---|
| 1 高 | 有人值守时先探占用、被占用就放回排队再弹框，绕过了「本机写过」的记录：写成了、回执没送到的单在会计开着 Express 时会被派给别的电脑再写一遍 | 已修：`PollWorker.handle_task`（原 `run` 里的闭包，挪成方法便于测）先查 `task_journal.written`，写过的不探占用、不放回、不按终止回报，直接交 `_handle_dbf_task` 照上次回执回报。单测（写过 + 占用 + 有人值守 + 这一批选过终止 → 只回 success、不弹框）与契约（`handle_task` 里写过记录先于占用探测与终止、`_handle_dbf_task` 里先于文件号核对）各守一处 |
| 2 中 | 同一把密钥重配（留空配对码、找到 Express 后升级）会清掉 `device_state`，而认电脑按密钥记着上次内容、不再发 | 已修：`pair` / `pair_folder` 只有换上新密钥才清（`_device_state_after`），清的同时让认电脑忘掉新旧两把的记录；`_upgrade_pool_members` 跳过带标记的成员；`Announcer` 上次结果是 `superseded` / `claimed_elsewhere` 而标记已清时不信记忆、马上再认 |
| 3 中 | 设备密钥会变或丢：降级 1.6 再升级丢 `device` 键、DPAPI 解不开永远拿不到、克隆机共用一把 | 已修：新 `device_key_backup.py`，同一份密文另存 `HKCU\Software\Pearnly\CC Desktop` 的 `Device`（旧版不碰），文件缺了从备份恢复、两边互补；密钥旁记 `MachineGuid`（`HKLM\…\Cryptography`，64 位视图），解不开或标识不是这台 → 生成新的并显式盖掉文件（`_write_locked(replace_device=True)`）与备份；读不出标识不比较。`schema_version` 不变，1.6 照样读。常驻电脑碰到 `claimed_elsewhere` 的提示 / 自动恢复列入第 6 批（下方） |
| 4 中 | 「终止」按这一批记，租约已放回，下一批再领到就又弹框 | 已修：按单号记一天（`_aborted_tasks`），这台再领到就按终止回报，`batch_done` 不清；这一批后面的单照旧按终止。别的电脑在此期间领到会照常写——服务端回执要拿着租约（`push.native.service.ack` 核 attempt），放掉的单这台已无权记失败；写进 ADR-021 第 38 条与「有意变化」 |
| 5 中 | 只读成员升级靠联网查连接类型，查不到就当 Express | 已修：`pair_folder(pool=True)` 且服务端说是 Express 时存 `ProfileRecord.pool` / `CompanionConfig.pool`；升级只看这个标记，不再调 `connection_kind`（用例里调到就报错） |
| 6 低 | 文件号对不上回排队后没有本机退避 | 已修：同一张单两分钟内第二次在这台对不上 → 回报（已放租约）后歇 `UID_MISMATCH_COOLDOWN_SECONDS`=20 秒、不再触发整套重扫；第一次照旧马上交出去 |
| 7 低 | 新词条「将由其他电脑自动推送」说满了 | 已改：`intake.error.ERR_ACCOUNT_UID_MISMATCH` 四种语言改成「……不是登记的那一份，等待其他电脑」；翻译包按 `i18n-build.py` 重生成 |
| 8 低 | 长路径取不到文件号 | 已修：`device_facts.extended_path`，≥248 个字符补回 `\\?\` / `\\?\UNC\`，只在真 `CreateFileW` 前用（替身接口不受影响） |
| 9 低 | 网卡只按名字排除 VPN；`WNetGetConnectionW` 回 1201 当本机盘 | 已修：类型 53（`IF_TYPE_PROP_VIRTUAL`）一并排除；1201 时缓冲区里是网络路径就用它 |
| 10 低 | `attached` 之后只有内容变了或进程重启才再认 | 已修：挂上了也每 12 小时（`REFRESH_SECONDS`）再认一次 |

证据（同第 5 批的环境）：

| 检查 | 结果 |
|---|---|
| `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/） | 891 passed, 41 skipped（879 → 891：`test_write_dispatch` +3、`test_device_link` +8、`test_device_facts` +1；改了 2 个旧测试：池成员升级改用 `pool` 标记且断言不联网、`test_always_on` 的源码对照跟着 `handle_task` 走并加「写过记录先于占用探测」） |
| 变异核对 | 去掉写过记录先查、按单号终止、重复对不上的冷却、同密钥不清标记、升级跳过带标记成员、清标记后不信记忆、备份恢复、类型 53、长路径前缀：对应测试各自失败（9 / 9） |
| `python3 tests/test_companion_contract.py` | 67 OK（66 → 67：`WriteDispatch` 加写过记录先于占用探测、终止与文件号核对） |
| `python3 tests/test_cowork_reason_language.py`、`check-architecture.py`、`i18n-build.py --check` | OK / PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 在修复提交 `34c7a682` 上 88 步全过，退出码 0；本条文档提交只改文档 |

没做 / 没验：同第 5 批（真 Windows：DPAPI、注册表备份读写与 `MachineGuid` 64 位视图、1201 时缓冲区确有网络路径、类型 53 的网卡、长路径
`\\?\` 打开目录、两台电脑联调，都在第 7 批）；降级 1.6 → 再升级 1.7.0 后设备密钥不变要在测试机上走一遍（第 7 批清单加这一条）。

留给后面几批（更新）：

- 第 6 批（**界面建在新的 CC 外壳上，等它合入 main 后再做**；本分支不做任何前端 / 对话页改动）：对话页每次打开带 `state().device.key` 调
  register v2；`device_state` 为 `superseded` 时 `rekey`、为 `claimed_elsewhere` 时重新登记；**常驻电脑碰到 `claimed_elsewhere` 要有看得到的提示**
  （托盘 / 设置「需要处理」已有，另需对话页或推送记录上让会计知道那台停了），或由服务端在心跳里带「请重新认电脑」信号让它自动恢复——二选一在
  第 6 批定；「电脑」列表与 ⋯ 移除（若只摘设备不吊销密钥，靠 12 小时再认恢复）；`remove` 回 job；录入入口状态文案、定时任务页读者编号、
  `all_members` 开关（第 3、4 批所列）。
- 第 7 批：第 5 批与本条「没做 / 没验」各项、降级再升级、克隆机（sysprep 前后）各一台；Companion checks 在 Windows 上全绿后再谈发布；真实 Site
  迁移回读与并发压测照第 3、4 批所列。
- 第 8 批：照方案。

## T1 跨年度换账套（定时任务跟随公司当年账套，2026-10-16，本地提交，未推送）

分支 `pearnly/t1-cross-year`，工作树 `.claude/worktrees/t1-cross-year`，基线 `248b6cbd`（`pearnly/multi-desktop` 第 5 批），已变基到第 5 批审查修复后的 `10f0ed0b`：建在第 4 批的读者三元组
与写账分派之上，随 multi-desktop 一起合入 `main`。需求原文：[交接 T1](2026-10-15-handoff-cc.md)。提交：`5df30d5d` feat（代码、测试、补丁）、本条 docs
（本节与架构说明两行）。补丁 `v0_0_23_folder_task_company`。新词条 4 条（三个原因码 + 1 个字段标签，en / zh / th / ja）；没改已有文案；
`frontend/assistant` 不动（`pearnly/cc-shell-rebuild` 在重做外壳），前端待办见下。

问题：`Cowork Folder Intake Rule` 绑死一份年度账套 `account_set_key`，跨年要手工改任务。

做了什么：

- 任务表加 `company`（公司主档编号，Data、带索引，不加外键）与 `company_branch`（5 位分店号，可空），原生 DocType 迁移（`modified` 升到 10-17）。
  保存任务时（网页、CC 一样，前端不改）服务端按目录里所选的那份 Express 账套反查：属于唯一一家主档就记下它和这份账套的分店，对不上、多于一家
  记空；报文里带的公司 / 分店一律不收。MR.ERP 任务不记（账套是公司 + 年度，目录里没有会计期间）。`account_set_key` 保留作兜底与显示。
- 判据归 companies（`companies/tasks.py`，新）：`company_of` 同 `identity.belongs`（身份相同；没有税号时与按名称建的主档同连接同名称键）；`books_of` 取
  这家公司在任务连接上的账套（`service.books` 全集），给了日期用 `identity.covering` 按月取、再由新纯函数 `identity.for_day` 核到日，给了分店用
  `identity.for_branch` 筛（这家公司只报过这一个分店号时，没报分店号的旧年度账套也算）。folder_intake 不导入 companies，经 hooks
  `cowork_task_company` / `cowork_task_books` 问（同 `cowork_rules_company` 的做法）。两个函数每次先清 companies 的请求内缓存：后台推进在同一个
  任务里换着负责人推（`pump._as` 不清它），不拿上一个人能用的连接来算。
- 执行（`folder_intake/follow.py`，新；`push.push_one`）：每张票按票上的单据日期（`invoice_date`）选这次推往的账套——
  - 正好一份、池里有电脑够得到（写账分派同一判据 `push.native.service.has_writer`，目录行 `writable=false` 也算够不到）→ 推往它，选到的经新的
    `history.push_state.retarget` 抄到票上（账本投影 `mirror` 不覆盖票上已有的去向，不换的话票上、CC、待处理页还显示任务存下的那一年），这张票的
    去向另记 `account_set` / `account_label`；
  - 一份都没有（含票上没有日期）`cowork.folder.account_set_none`、多于一份 `cowork.folder.account_set_ambiguous`、够不到
    `cowork.folder.account_set_unreachable` → 停下等人（`held` + 原因码），原因记在票上（够不到时连同选到的那份一起抄到票上）；池里一台能写的电脑
    都没有（电脑没连上）照旧按「电脑没连上」过一会儿再推，最后一次才停下；
  - 任务没记公司（旧任务对不上公司、MR.ERP）或主档已删 → 不跟随，照旧推往固定账套，一行都不多走。
- 推送仍是同一个推送服务（`choices_view` / `submit` / 确认），`account_match` 的期间、税号、分店硬闸不放宽、不绕过。
- 任务默认值（这一类都这样处理）：推往任务存下的那份原样用；跟到别的年度时科目、部门代码只在那份账套读到的主档里有才用，没读到也先不用
  （推送照现有规则请电脑读科目表、读到后再推），其余各类原样（`follow.fixed_in`）。人在复核页 / CC 重推停下的票，推往这家公司（这个分店）
  别的年度账套也套用任务默认值（`push.fixed_for` → `follow.member`）。
- 结果：`contracts.ticket_state` 把三种原因算「待处理」（类别 `account_set`），完成简报写「待选账套」（沿用 `cowork.folder.brief.why.choice` +
  `intake.target.accountSet`，不加新句子）；任务列表 / 运行记录的结果多一项 `account_sets`（这些票实际推往了哪几份账套、各几张，只有跟随的任务有），
  CC 的任务列表与运行记录随结果带出；任务列表另给 `company` / `company_branch`。
- 推送限流与存货先后：跟随的任务每张票的账套推的时候才定，按（推送连接，公司 + 分店）一条道（`pump._lane`），同一家公司存的年度不同的进项、
  销项任务归同一条道；固定账套的任务照旧按（推送连接，账套）。
- 补丁 `v0_0_23_folder_task_company`（post_model_sync）：Express 任务里 `company` 空着的，以任务负责人身份按 `account_set_key` 调同一个
  `company_of`，唯一对上一家才写公司与分店；对不上、多于一家、负责人用不了那条连接（读不到目录）的留空、照旧固定。不升 `rule_revision`
  （电脑不用重新拍底）、不改修改时间；已补的不再动，重跑同一结果。

有意变化（相对旧行为）：

1. 记了公司的任务不再把票推进任务存下的那一年：每张票按自己的日期换账套。原来新年度的单推往旧年度账套，`account_match` 允许落在旧账套的
   「次年」期间（`next_end`）；现在只认覆盖那天的本年期间（`identity.book` 的 `period_start` / `period_end`），新年度账套还没建时停下等人
   （用户定的用例「只有旧年度账套时新年度的单停下」）。
2. 同一家公司同一天有两份账套（Express 测试数据 T8 / T9 都是 2025 年）一律停下，不按名称、新旧或可达去挑。
3. 跟随的任务限流 / 存货先后按公司道算，不按任务存下的那份账套。
4. 跟到别的年度时，科目 / 部门默认值要在那份账套的主档里才用。
5. **2026-10-16 改（数字会计员第 3 期，用户定「新年度账套还没建时，下一年的单记进旧账套的下一年期间」）**：上面第 1 条「新年度账套还没建时
   停下等人」改为「先找本年期间覆盖这天的账套；没有，就用期间里含这一天的旧账套（次年段，到 `next_end`）」——照会计实际做法；新年度账套
   一建好就推往新账套。两份同期（本年或次年段）仍停下。判据只写一处（`targets.account_match.for_day`），`companies.identity.for_day`、
   `identity.book` 多给 `next_end`、自动推的「推往的须是按日期选出的那份」与 `account_match` 期间硬闸都按它：硬闸另拦「单据在所选账套次年段、
   而同一家公司（同税号同分店，没有税号按目录公司名）已有本年期间覆盖这天的账套」（`newer_book_covers`，原因同期间不符）。测试：
   `test_cowork_folder_follow_year`（新账套已建 / 未建 / 两份同期三种）、`test_cowork_push_direction` 的 `NextYearPeriod`。分支
   `pearnly/da-phase3`，见 [第 3 期设计](2026-10-16-da-phase3-design.md)「有意变化」。
   同日审查处理补：退到旧账套次年段前要核得了「新年度账套还没建」——同一家同一分店里有会计期间没报全的账套（CC 桌面版读 ISPRD 失败时
   目录里没有起止，可能正是新的那份）、或本年起始晚于旧账套本年止的账套，就选不出、照旧停下等人；按月取账套（`identity.covering`，
   月结台账、主档页、档位统计）用同一规则（`account_match.for_month`）。人推的期间硬闸照「读不出不拦」不变。
6. **2026-10-17 改（数字会计员第 3 期，用户批「两份同期按公司问一次哪份是现用的」）**：上面第 2 条与第 5 条的「两份同期（本年或次年段）
   一律停下」改为「没选过停下、选过照选」——这家公司、这个分店、本年或次年段、这一组候选账套选一次（待处理 / 复核页在停下的单上选一份、
   这张推成了才记住，CC 同一个工具同一条路），记在工作空间 `document_policy`（键 `current_book|…`）；新建账套、组合变了、跨到次年段再问；不按名称、新旧或
   最近有录入去挑。读在 `companies.tasks.books_of`（T1）与 `companies.automation.day_books`（自动推）。测试：`test_cowork_folder_follow_year`
   两份同期两条用例改成先停、选过照选，另加 `CurrentBookTests`（新建第三份再问、选的不在候选里不用、停下的单上选算出的键正是任务读的）。
   任务没记分店、候选跨了分店（各分店各有现用的账套）不问不记、照旧停下（第五轮后审查，`BranchTests`）。
   见 [第 3 期设计](2026-10-16-da-phase3-design.md) 第 18 条「②接线上」与「第五轮后审查」。

前端待办（等 `pearnly/cc-shell-rebuild` 合入后在外壳里做；Desk `intake-folders.js` 同理）：

- 任务表单 / 列表 / 主档页任务行：`company` 非空时在账套处显示「跟随公司当年账套」（账套名只作兜底），停下原因按三个原因码的词条显示；
  表单不传公司（服务端自己定），也不要加选项开关。
- 运行记录与任务结果显示 `results.account_sets`（实际推往的账套名与张数）。
- 待处理页对 `account_set_*` 停下的票：当场选账套（现在待处理页只按票上的去向选科目等，没有换账套的入口）。
- 主档页「定时任务」现在仍按 `account_set_key` 归到公司（`companies.service.tasks_of` / `task_overview` 未改）：任务存下的那份账套从目录里消失后
  会归不上，外壳重做时可改成优先认 `company`。

测试机验证（D10，10-19 按第 3 期口径重写；原 6 步是第 3 期合入前的「新年度账套没建时停下」，已作废）：

现在的口径（上面有意变化第 5、6 条）：每张票按单据日期选账套——先找本年期间覆盖这天的；没有，就用次年段（到 `next_end`）含这天的旧账套；
新年度账套已建且有会计期间才换过去。同一组有两份同期（本年或次年段）时，这家公司没选过就停下「该公司有多份账套覆盖单据日期」，
在待处理 / 复核页选一份、推成了就记住（主档页「现用账套」一行），之后同一组照选；本年和次年段是两组，各问一次。
另外第 3 期的自动推判档在选账套之后：单据日期早于上传前 60 天的票判 C（`date_stale`），停在「待复核」（结果里 `checked`），
不是账套原因。跨年那几天都是旧日期，所以下面「选到哪份」看票上的**推送去向**，不看有没有自动写进 Express；要写进去就在
待处理 / 复核页手动推一次（推往的就是票上那份）。

不调识别、不扣费：只用 POS 导出（销项任务收 Excel / CSV，一天一张票）。只写测试机 `C:\PearnlyExpress\70EXP` 下的
`T7SINCER` / `T8SINCER` / `T9SINCER`（可直接写）；局域网 Accserver、S 盘一律不写、不改名。

1. 起环境：`PEARNLY_TESTENV_SRC=<被测工作树> PEARNLY_TESTENV_IMAGE=<线上 digest> bash scripts/testenv.sh lab-start cowork`
   （已起就 `testenv.sh live cowork`）。只读回读：已有 Express 任务的 `company` / `company_branch` 回填数，对不上的留空。
2. 看账套与记住的选择（只读）：testcowork 主档页打开 SINCERE 这家公司，记下 T7 / T8 / T9 三份的本年期间与次年段
   （预期 T7 本年 2024；T8、T9 本年都是 2025、次年段 2026——与此不同就先停下，把实际期间回报开发窗口，下面的预期要按实际改）。
   再看主档页「记住的选择」卡的「现用账套」小节：有这家公司的行（10-19 lab 可能留下过）先截图记下，再删掉，让第 3 步从「没选过」开始。
   用 T9 建一个销项定时任务（或用回填过的旧任务），核对任务上记了公司（主档页任务行能归到这家）。
3. 造跨年单（第一份导出）：同一份 POS 导出里放 2024-12-30、2024-12-31、2025-01-02、2026-01-05、2023-06-15 五天，放进任务文件夹，
   CC 里「立即运行」。预期：
   - 2024-12-30、2024-12-31：推送去向 T7SINCER（本年覆盖），停在「待复核」（日期早于 60 天）；T7 第一次用、停在「要选…」（科目、对方）
     或「等电脑读科目表」也算对，账套判据看的是推送去向；
   - 2025-01-02：停下「该公司有多份账套覆盖单据日期」（T8、T9 本年同期，没选过）；
   - 2026-01-05：同样停下「该公司有多份账套覆盖单据日期」（本年没有覆盖 2026 的，T8、T9 次年段同期，是另一组）；
   - 2023-06-15：停下「该公司没有覆盖单据日期的账套」；
   - 结果 / 完成简报：两张「待选账套」、一张没有账套、两张待复核；运行记录的 `account_sets` 只数推往了账套的票（T7 两张）。
4. 选现用账套并核对记住：在待处理页 2025-01-02 那张上选 T9SINCER → 推往 T9（推成了才记住）；主档页「现用账套」多一行
   「本年 · 总公司 → T9SINCER」。2026-01-05 那张也选 T9 → 推进 T9 的次年段，「现用账套」再多一行「次年」。
   第二份导出放 2025-01-03、2026-01-06 两天再运行一次：两张都直接推往 T9（不再停下问），停在「待复核」；在待处理页推 2025-01-03 一张，
   Express 里落在 T9 本年。CC 里问这两张单推往哪份账套，回答与票上去向一致。
5. 新年度账套「没建」：把 `C:\PearnlyExpress\70EXP\T8SINCER` 暂时改名（如 `T8SINCER_off`），等 CC 刷新账套目录（或 CC 设置里重新读账套列表）
   后 T8 不在目录里；第三份导出放 2025-01-07、2026-01-08：2025-01-07 推往 T9 本年、2026-01-08 推往 T9 次年段，都不停下问
   （只剩一份，不用选；第 4 步记住的两行不起作用，主档页不再列）。测完把 T8 改回原名、刷新目录，两行「现用账套」重新列出。
6. 够不到：在这台 CC 的账套列表里去掉 T9（或让读文件夹的这台电脑访问不到 T9 所在目录）后跑一张 2025 单：停下「没有电脑能访问单据日期所在的账套。」。
   读文件夹的电脑整台关掉 / 退出 CC 时跑：票照常确认排队（「电脑离线」），电脑回来后自动写，不是停下（多电脑之后的口径，D4）。
7. 收尾：`testenv.sh lab-stop cowork` 切回线上；T8SINCER 已改回原名；主档页「现用账套」按第 2 步截图还原或留空（问用户）；
   登记写了什么：每张手动推成的单的 Express 单号、所在账套与期间（预期最多 T7 两张 2024-12、T9 本年 2025-01 两张、T9 次年段 2026-01
   一到两张），连同改名与恢复的时刻，写进本节下面；测试写入的单据按用户意见留或删。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`，python3 3.12；需要 Frappe 的在一次性离线容器 `docker run --rm --network none`、
`pearnly/frappe-sample:16.34.1`、工作树只读挂载里跑，不碰共享样板栈）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_folder_follow_year.py`（新，进 CI） | 29 OK：真实 `companies.tasks` / `service` / `identity` + `folder_intake.follow` / `push` 串起来跑。跨年两份账套各推一张进各自账套且抄到票上；只有旧年度时新年度的单停下；T8 / T9 同期停下（2026 的照推）；没日期停下；够不到停下并带账套名、整池没电脑时重试、最后一次停下；目录标不可写算够不到；负责人用不了连接时停下不猜；分店筛选（指定分店选到该分店，不指定而有两个分店停下）；旧任务（没公司）照旧推固定账套、不调钩子、不改票；主档已删照旧；默认科目 / 部门按新账套主档取舍；人工重推套任务默认；`retarget` 只写一次；`company_of` 唯一 / 多家 / 不认识 / 无权；换负责人不复用上一个人的目录；结果按账套计数；原因码四种语言；补丁只补唯一的、MR.ERP 与无权的留空、已有的不动、重跑无写入并还原身份 |
| 改过的旧测试 | `folder_intake` 123（新增公司道的存货先后；任务表 `modified` 改为 10-17；推送替身加 `follow`）、`folder_task_batch` 31（新增 `FollowCompanyYear` 3 项：记公司与分店、报文里的不收、改选对不上的账套清掉）、`folder_tasks_mrerp` 8（MR.ERP 不记、Express 记）、`folder_reminders` 42、`push_fixed` 18、`delete_everything` 7、`cc_desktop_devices` 58（补丁顺序改成核四条相连且在 post_model_sync 后，不再要求是最后四条）、`companies` 54 — 全部 OK |
| 变异核对 | 多于一份不停下、不核可达：新测试 3 项失败 |
| 容器内导入改过的 9 个模块与补丁、钩子 `cowork_task_company` / `cowork_task_books` / `cowork_push_fixed_choices` 解析；`tests/test_cowork_*.py` + `test_queue_targets.py`（不含源码对照） | 导入与解析 OK；全部 OK，只有与基线相同的 `test_cowork_invoice_checks.py`（2 个 `object is not bound`）、`test_cowork_table_input_runtime.py`（样板缺 `docx`） |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check` | PASS / PASS（不扩基线，没有新的域依赖方向） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 89 步全过，退出码 0（代码提交前的工作树，代码与 `5df30d5d` 相同；第一次跑缺三处测试替身的 `follow`、补丁顺序断言写死最后四条、词条行尾多一个制表符，修好后重跑全过；本条 docs 提交后在 HEAD 上再跑一次全过） |

没做 / 没验：真实 Site 上的迁移与补丁回读、MariaDB、HTTP、测试机与真 Express 写入（上面的计划）；`frontend/assistant` 与 Desk 页面（前端待办）。

风险：

- 写错账套代价大：选账套只按单据日期 + 公司 + 分店，取不到 / 多于一份 / 够不到都停下不猜，推送仍过 `account_match` 硬闸；但单据日期本身读错（识别把
  年份读错）会选到另一年度的账套，硬闸也拦不住（日期就在那份账套的期间里）——与人在网页按日期选账套是同一个风险。
- 公司主档是按身份认的：一家公司换了税号、或旧年度账套没报税号又不是按名称建的主档，旧年度认不出来，那一年的单会停下。
- 目录里没有会计期间的账套（读不到期间）一律不算覆盖：这样的公司每张单都停下（2026-09-27 起的 CC 桌面版都报期间）。
- 新年度账套刚在 Express 建好、电脑还没上报目录前到点的单会停下，要人重推（不再自动落进旧账套的次年期间，见有意变化 1）。
- 主档页 / 侧栏的「定时任务」仍按任务存下的账套归公司（前端待办第 4 条）。

### D10 第 5 步缺陷与修复（10-20）

测试窗口 10-20 在测试机 DESKTOP-MN2BRKR（CC 1.9.0，账套根 `C:\PearnlyExpress\70EXP`，另有 16 个 `\\Accserver\d$\ACCOUNT\…` 授权目录，
夜里离线）做第 5 步时发现三处，逐条按代码复现原因后按类修。分支 `pearnly/batch-1019b`，代码提交 `bd20c38c`；CC 桌面版不发版（仍是 1.9.1）、不上测试机。

**1. 账套目录改名后，目录里冒出 `\\desktop-mn2brkr\c$\…\t8sincer`（不可写），改回原名也回不去，重启 CC 才好**

原因（三环）：

- CC 照报登记表里的每一行：`T8SINCER` 改名后 `SCCOMP.DBF` 里仍有它，`account_sets_payload` 照报这一行，路径不存在，文件号（`uid`）、
  网络路径（`share`）都是空；心跳把它与上一份清单按路径合并（`projection_refresh.merge_account_sets`），公司名、税号、会计期间沿用上次的。
  所以心跳仍是「101 个 · 可写 100」。
- 服务端认定（`targets.identity.resolve`）：这份账套原登记是按文件号认上的，且带着旧的共享写法（`location`）；这一行没有文件号、
  网络路径为空，第 3 条（同一台电脑同一本机路径）要求网络路径相同而不认，落到第 6 条新登记，键 = `host_qualified` 写成
  `\\本机名\c$\…`（只在本机的新账套各台唯一的写法，**不是 CC 换算的管理共享路径**）。这一份没有电脑能写，进了目录。
- 「重新查找」不重扫：它只改授权数据根、重启取任务线程；账套清单扫描的结果缓存在进程里（`master_refresh._refreshes`，按数据根 +
  所选账套为键），取任务线程重启后头一拍拿到的仍是上一遍；每分钟那一眼只看登记表与科目表的大小 / 修改时间，目录改名不改它们，
  所以不重扫。第一次点（授权从 17 个变 1 个，键变了）扫到了 T8 不在；改回原名再点，键没变，交出的仍是那一遍，一直报旧行，直到重启 CC
  清掉进程缓存。

改法：

- CC：目录已经不在的账套不报（`account_probe.account_sets_payload`；ISINFO、ISPRD 都没 stat 到时才多看一眼目录，平常的账套不多一次
  读盘；找不到 / 不是目录才算不在，别的读盘错误当作在）。推送前核账套、配对照旧现读登记表全名单。所在服务器整台不在由关卡先跳过，不受影响。
- CC：「重新查找」之后 `master_refresh.rescan_soon()`；`rescan_soon` 之后之前扫的结果不再交出去（含取任务线程重启后的头一拍），
  新的一遍交出（含「进行中」快照）前谁都拿不到旧的。写前核文件号对不上时的整份重报同样受益。
- 服务端：第 3 条加一种「没有反证」：这一行文件号、网络路径都读不出（目录这会儿打不开 / 不在、本机共享撤了），内容键为空或与原登记
  相同，就仍是这台电脑一直用这个路径够到的那份，不另登记带主机名的一份（防 1.9.0 及更早版本在现场照报这种行）。内容键对不上、或这台
  电脑从没用这个路径够到过的，照旧第 6 条新登记。
- 已登记出来的那份 `\\…\c$\…\t8sincer` 不删：规范键登记后不改；改回原名后 T8 按文件号认回原登记，那份没有电脑再报到，不进目录。

**2. 之后两张票（2025-01-07、2026-01-08）停在「多份账套」，候选是写不进的 UNC T8 + 可写的 T9**

原因：按单据日期选账套（`companies.identity.for_day` → `targets.account_match.for_day`）只看期间，不看写不写得进；那一行 T8 沿用了上次的
会计期间，与 T9 本年 / 次年段同期，于是两张都判「多份」。定时任务（`tasks.books_of`）、自动推判档（`automation.day_scope` / `day_books`）、
待处理页可换的账套（`pending._stop_scope`）、记住的「现用账套」键（`current_book`）都经这一个函数。

改法：按期间选（`identity.for_day`，规则不动）之后、按「现用账套」筛之后，去掉目录标 `writable=false` 的（`identity.writable_first`：
池里没有一台电脑能写它，或目录已不在），一处 `current_book.pick` 给定时任务（`tasks.books_of`）与自动推（`automation.day_books`）共用；
待处理页可换的账套（`pending.account_set_options`）同样不列写不进的。

- 只剩一份就是它、不用选；一份写得进的都没有就原样回，定时任务停「没有电脑能访问单据日期所在的账套」（`follow.choose`：多份且全写不进 →
  `account_set_unreachable`，不是「多份」）。
- 先选期间再去掉：新年度账套已建但写不进时不退回旧账套的次年段（设计第 6 条「够不到」停下）。
- 先认「现用账套」再去掉：这家公司对这一组选过的那份写不进时停「没有电脑能访问」，**不改推没选的那份**（第 6 步「去掉 T9」正是这种：
  第 4 步记住了 T9）。「现用账套」的键照旧按整组候选算（`for_day` 不过滤），T8 恢复后记住的两行照用。10-20 那行 UNC T8 的键与原 T8 不同，
  它与 T9 是另一组、没选过，所以去掉它只剩 T9。
- 推送的期间硬闸（`account_match.conflict`）不改。目录里的 `writable` 是池里任一台够得到且可写（`connections.pool.listing`），单份账套
  「读文件夹的那台电脑够不够得到」仍由 `follow.choose` 的 `has_writer` 判。

**3. 「重新查找」把 17 个授权目录覆盖成只剩 `C:\PearnlyExpress\70EXP`（夜里 S 盘 / Accserver 不在线）**

原因：`express_machine.rescan_roots` 只用这次找得到的（`rank_roots` 要读得到登记表），`apply_roots` 把它整份写进每个成员的
`authorized_roots`。服务器关着时点一次就丢掉全部局域网授权；主根若也在关着的服务器上，还会把成员的默认账套换成别的。

改法：`rescan_roots` 回 `(要授权的, 其中暂时访问不到、保留的)`：原来授权、这次没找到的，**确实没了**（所在盘 / 共享此刻访问得到、
文件夹却不在，`root_gone`）才去掉，其余（服务器关着、网络盘没连上、盘符不在、文件夹在只是没读到登记表）保留授权，排在找到的后面；原主根
找得到或只是暂时访问不到都仍排第一。所在服务器经 `scan_pace.GATE` 限时问（同一次查找同一台只问一次）。`apply_roots` 不去读保留的那些的
登记表，主根在其中的成员账套、科目不动。一个都没找到（全在关着的服务器上）时什么都不改，仍整套重扫一遍账套清单。「标记离线」不另存
状态：心跳扫描本来就按服务器限时问、不在就跳过、退避重试（`scan_pace.HostGate`），回来就照常扫到；日志记「暂时访问不到、保留授权 N 个」。

「断开连接」清掉授权（C9 观察到断开清 17 个）：判断**不是设计**。方案 2.8 的断开是「吊销这把密钥 + 删本机成员」；Express 数据根是这台
电脑的设置（`express_machine` 文件头，用户 10-08「Express 账套是小助手的全局设置」），只因为存放在成员记录里才随成员一起删掉。重新连接时
自动检测找不到用户手动加的 `\\Accserver\d$\…`（要重新「更换…」）。改法：删最后一个 Express 成员前（断开、移除、改连别的公司）把数据根
记在设置对象里（`DesktopSettings._keep_roots`），下次连接时按「重新查找」同一规则检测（暂时访问不到的接在后面照旧授权；一个都找不到时不拿
它们去配对，先留着）。只在内存里：没有成员时 CC 重启就没了（照旧重新查找），不另加持久字段。断开仍先吊销密钥再删成员（接口约定测试不变）。

测试（CC 桌面版 `cd companion && QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`，Python 3.11 + PySide6 6.11.1）：
1015 passed、42 skipped（Windows 专属的跳过）。新增 / 改：

| 用例 | 钉住 |
|---|---|
| `test_account_probe::test_an_account_set_whose_folder_is_gone_is_not_reported` | 目录不在的不报、没 ISINFO 的目录照报、配对全名单不变、改回原名再报 |
| `test_master_refresh::test_after_rescan_soon_an_earlier_snapshot_is_never_handed_out_again` | 重启头一拍照旧交缓存；`rescan_soon` 后不交旧的、下一次是新扫的（改前这条失败：拿到旧那一遍） |
| `test_pairing_root_select` 三条 | 主根仍第一；暂时访问不到的保留、确实没了的去掉；`root_gone` 只在盘 / 共享访问得到且文件夹不在时为真、服务器不在不碰文件夹、同一台只问一次 |
| `test_desktop_settings::test_find_again_keeps_the_folders_it_cannot_reach_right_now` | 走真的 `apply_roots`：主根访问不到时主根与账套不换、确实没了的去掉、不读访问不到的登记表、全找不到时文件一个字节不改、都会整套重扫 |
| `test_desktop_settings::test_disconnecting_the_last_member_keeps_the_express_folders_for_the_next_connection` | 断开后设置页仍显示原数据根；重新连接按它们检测、找不到的接在后面授权；全找不到时不拿去配对 |
| 改：`test_find_again_authorizes_everything_found…`、`test_changing_the_express_folder…`、`test_find_again_keeps_the_root_in_use_first` | 新签名（`rescan_roots` 回两项、`apply_roots` 多 `offline`）；「重新查找」后要求整套重扫 |

服务端（`python3 -m unittest`，3.12）：`test_cowork_express_account_sets` 70 OK（新 `test_rule_3_a_row_without_identity_stays_the_same_local_account_set`：
读不出身份的行认回原登记、内容键对不上照旧新登记、没用这个路径够到过的照旧新登记）；`test_cowork_folder_follow_year` 50 OK（新
`test_books_nobody_can_write_are_not_candidates`：按期间选不看可写、去掉写不进的后本年 / 次年段只剩 T9、全写不进原样回、新年度写不进时
不退回旧账套次年段；新 `test_a_twin_book_nobody_can_write_is_not_asked_about`：另一个键的写不进 T8 + T9，两张都推往 T9、原来那组记住的
不起作用、停着的旧票可换账套只列 T9 且选它照样记住、记住的是写不进那份时停「没有电脑能访问」不改推 T9、全写不进也停「没有电脑能访问」）。
变异核对：去掉第 3 条的「没有反证」、`current_book.pick` 不过滤、待处理页不过滤，各自对应的新用例失败。

给测试窗口的复测（新包从本分支 `companion-build.yml` 打，附件 `companion-installer-test`；lab 照旧挂本分支实时源码，`testenv.sh live cowork`）：

- 第 5 步：装新包、普通权限重启 CC；把 `T8SINCER` 改名 `T8SINCER_off`，CC 设置里点「重新查找」。预期：日志「数据根 70EXP 里 1 份账套的目录不在，
  不报：…」、下一两次心跳「100 个 · 可写 100」，服务端目录里 T8 那行没了、**不出现** `\\desktop-mn2brkr\c$\…` 新行；夜里 Accserver 不在线时
  日志「暂时访问不到、保留授权 16 个」，`companion.json` 里各成员 `authorized_roots` 仍是 17 个（只看个数）。放 2025-01-07、2026-01-08 两天的导出
  「立即运行」：都推往 T9（本年 / 次年段），不停下问。把 T8 改回原名再点「重新查找」：一两次心跳内回到「101 个 · 可写 101」、目录里是原来的
  `c:\…\t8sincer`（可写），不用重启 CC；主档页「现用账套」两行重新列出。10-20 留下的那行 UNC T8（若还在目录里）改回原名后应消失。
  已经停在「多份账套」的那两张旧票：待处理页展开，可换的账套只剩 T9。
- 第 6 步前半：新版 CC 上「目录不在」的账套直接从目录里去掉，不再是「写不进」（Windows 上 CC 报的 `writable=false` 实际只来自目录不在：
  `os.access` 对目录不看权限）。所以把 `T9SINCER` 改名 `T9SINCER_off` 后「重新查找」：目录里只剩 T8，2025 单推往 T8（只剩一份），
  不是「没有电脑能访问」——这一条预期按此改；T8、T9 都改名时停「该公司没有覆盖单据日期的账套」。「够不到」（目录里还在、读文件夹的那台
  电脑写不了，或记住的那份写不进）要两台电脑才能在测试机上造（T9 只在另一台电脑的授权里），由离线用例钉住，这一轮不要求；第 6 步后半
  （读文件夹的电脑退出 CC 时排队、回来自动写）照原文。测完把 T8、T9 改回原名、「重新查找」。
- 断开连接（可选）：断开再在对话页登录自动连接，`authorized_roots` 仍是 17 个（夜里离线的也在）。
- 限制：断开后保留的数据根只在 CC 进程内存里，没有成员时重启 CC 就没了；`\\…\c$\…` 那份旧登记不删（规范键不改）。

## 并入 CC 外壳分支（2026-10-16，本地提交，未推送）

`git merge --no-ff pearnly/cc-shell-rebuild`（对方 `f5c04929`，67 个提交：CC 外壳重构、F19 推送摘要、数字会计员第 1 期月结台账、
第 2 期统一客户规则）进 `pearnly/multi-desktop`（`2a1b25bf`，T5 第 1–5 批 + T1）。两边都从 main `7c9bb445` 分出。第 2 期设计第 4 条
「推送接入在 T5 第 1–3 批合入后变基」由这次合并完成。

### 冲突怎么解

| 文件 | 解法 |
|---|---|
| `connections/preparation.py` `reported_masters` | 取本分支的来源（`pool.facts`：按物理账套存的账套资料，哪台电脑读的都一样），加上外壳分支的 `keys` 参数（默认科目表与部门，公司规则校验传 `ALL_MASTER_LISTS`）。外壳分支那版读连接 `report_json` 的凭据绑定 / 公共缓存，本分支第 2 批已不用这条路，不保留。`ALL_MASTER_LISTS` 五张都在 `pool.FACT_KEYS` 里 |
| `push/application/service.py` `prepare` | 冻结目标只建一次：用本分支那版（准备证据 v2 带 `basis_hash`、`ledger.writer_scope` 写者锁口径 v2），之后加外壳分支的 `decided_by`（有才加）；删掉外壳分支在载荷之前另建的那份 `frozen`（否则后面那份覆盖它、`decided_by` 丢了）。`decided_by` 只进冻结目标，不进载荷，不改 `basis_hash` |
| `translations/{en,ja,th,zh}.csv` | 不手拼：两边翻译包取并集（同一 msgid 两边译文不同的没有）作底，再 `python3 scripts/i18n-build.py` 按自动合并好的 `messages.tsv` 重新生成；`--check` PASS，合并后的四个包相对两边都没丢行 |
| `tests/test_cowork_folder_task_batch.py` 替身 `sql` | 两段都留：先把查询压成单行（本分支），再认月结台账的分组查询（外壳分支），再按电脑计数 / 列任务（本分支第 4 批） |
| `tests/test_cowork_readback.py` 替身 | 两边都留：`waited` / `targets`（本分支，按账套等电脑）与 `access_locks`（外壳分支，只读可见性检查不加锁）；`access.scope` 用外壳分支的 `require` / `allowed`，`companion.devices.newest_version` 与带 `account_key` 的 `waiting` 用本分支的，`connections.access` 用外壳分支带 `lock` 的替身 |

自动合并的 `hooks.py`（两边各加的权限表、`cowork_company_month_readback`、`cowork_push_company_choices`、`cowork_task_company` /
`cowork_task_books` / `cowork_pool_presence` / `cowork_reader_device_moved` 都在）、`patches.txt`（外壳分支没加补丁，本分支 v0_0_19–v0_0_23
顺序不变；外壳分支新 DocType `Cowork Company Choice` 走原生迁移）、`assistant/tools.py`、`companies/service.py`、`folder_intake/rules.py`、
`scheduled-tasks.tsx` 逐个看过，没有重复定义或互相覆盖。

### 合并后另改的一处（语义冲突）

`companies/ledger.py` 的 `_tasks`（月结台账「收单」段的任务与「文件夹不在了」）是外壳分支照合并前的 `service.tasks_of` 写的：
按 `row.reader or row.connection` 取 `agent.read(reader)`。本分支第 4 批起任务按电脑读（读者 =（电脑，成员连接）），本机情况按
`agent.read(连接, 电脑)` 存，老写法读到的是连接级那份，按电脑读的任务的「文件夹不在了」会漏报。改成与 `service.tasks_of` 一样走
`folder_intake.readers.ref / state / of`，并在 `tests/test_cowork_companies.py` 加一项（任务挂在电脑 D2 上，台账取 D2 报的情况）。

### 语义核对

1. **推送服务四件同时成立**：`prepare` 里依次是写账分派与账套物理身份（`freeze_target` / `_account_row` 按池目录）→ `account_match`
   硬闸 → `_decisions`（账套记忆 → 公司规则钩子 `cowork_push_company_choices` → Express 历史已定 → 任务默认，带 `decided_by`）→
   冻结目标（证据 v2 + 写者锁 v2 + `decided_by`）。F19 的 `written` 从冻结目标读 `decided_by`（`service.py` `_entered` 读推送记录的 `target_json`）；租约 v2
   领取只核 route 与 `basis_hash`，冻结目标里多一个 `decided_by` 不影响。T1 跟随当年账套是 `folder_intake.follow` 选好账套键再进
   同一个 `prepare`，公司规则按那份账套的目录行认公司，所以换年度后规则照样生效。
2. **公司规则按公司身份查、不依赖账套键**（第 2 期第 13 条）：`companies.rules.for_push` 丢掉 `account_set`，用目录这一行经
   `identity.company_identity` 认公司（有税号按 `tax:<税号>`，没有按建档连接 + 公司名），再按主档名查 `Cowork Company Choice`。本分支
   池目录（`pool.listing`）每行的 `attributes` 来自登记里的规范属性（税号、分店、公司名都在），所以带主机名的本机账套键换了也认得出同一家
   公司；账套记忆（`choice_store.remembered`，按账套键）跟不过去，与第 13 条说的一致。
3. **回读两条都成立**：发起时 `request_service.waiting / create` 带 `account_key`，只等、只交给够得到这份账套的电脑（本分支第 4 批）；
   看结果按「能用这条连接」放开（`_visible` / `_usable`，外壳分支），别人发起、还在读的只回现状，推进与删除仍只认发起人；
   `vat_records` 与 `month_status` 取「这本账套这个月最近一次完成的」，不分发起人。`require_connection(..., lock=False)` 本分支签名支持。
4. **公司规则存之前核主档**（`companies/choices.py` `_masters`、`_name_parties`）现在读 `pool.facts`：同一份物理账套哪台电脑读的都算，
   不再要求「出自当前凭据」（本分支第 2 批起账套资料就不绑电脑与密钥）。

### 闸门

完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`：91 步全过，退出码 0（在解完冲突、改完 `ledger._tasks` 的暂存树上跑，
代码与合并提交相同，之后只加了本节文档）。合并带进了外壳分支 `frontend/assistant` 的新依赖 `@dnd-kit/*`，先 `npm ci --prefix frontend/assistant`
（第一次单跑 `tests/test_cc_month_ledger.py` 因缺这些依赖失败 6 项，装好后 10 项 OK）。另单跑：`test_cowork_companies` 109、`readback` 44、
`folder_task_batch` 32、`any_set_catalog` 11、`push_fixed` 18、`account_suggest` 43、`assistant_tools` 186，全部 OK；`check-architecture.py`
0 违规；`i18n-build.py --check` PASS。

没做 / 没验：真实 Site 迁移（`Cowork Company Choice` 新表 + 本分支补丁同批跑）、测试机与真 Express；外壳分支前端在多电脑场景下的点验
（读者选择、电脑名显示）留给测试机一轮。

### 合并审查三条的处理（2026-10-16，本地提交，未推送）

1. **冻结目标只有字符串断言守着（low，成立）**：`tests/test_cowork_push_direction.py` 加 `FrozenTarget` 两项行为测试，用推送服务真实
   导入加替身跑整条 `prepare`，截获 `ledger.reserve` 收到的冻结目标：`decided_by`、准备证据 v2 的 `basis_hash`、`lock_scheme` / `company_key`
   同时在；「仍然推送」的 `duplicate_confirmed` 在算 `basis_hash` 之前写进载荷；`decided_by` 不进载荷；没有 `decided_by` 时冻结目标不多这一项。
   试过在 `reserve` 前再建一次 `frozen = asdict(target)`，两项都失败。原来的源码字符串断言保留。
2. **T1 跟到科目表没读过的新年度账套时公司规则照样套用（low，成立，按方案一改）**：`push/express/company_choices.py` 的 `overlay`
   对费用科目、收款科目（`WAIT_KINDS`，与推送准备的 `CHART_KINDS` 同一组，要选时会等电脑读科目表）在这份账套科目表没报上来时先不用
   公司规则；新增 `held_back` 列出这些先没用的项，`push/application/service.py` `_decisions` 据此不让 Express 历史已定顶替（公司规则比历史
   具体），这一项照常列为要选，`masters_wait` 请电脑读科目表、读到后再按规则推，与 `follow.fixed_in`「没读到也先不用」一致。账套自己记过的
   照用；对方档、存货、部门、付款、抵进项照旧放行（对方档要选时不会等读主档，改了反而变成没得选的人工项；部门不再问）。规则存时的校验不变。
   测试：`OverlayTests` 原「科目表没报上来照样套用」那条按新口径改写（科目、收款科目先不用，对方档仍放行，账套记忆照用，`config` 为 None
   不核）；`CompanyRuleDecisions` 加一项（科目表空 + 公司规则 → 要选、不问历史；读到后按规则；只有别家供应商的规则时本张照旧按历史已定）。
   没在 `test_cowork_folder_follow_year` 补：那里推送服务是替身，决策链在 `test_cowork_account_suggest` 里用真的 `_decisions` 测。
3. **月结台账漏掉 T1 跟随任务（medium，成立）**：`companies/ledger.py` `_tasks` 加 `_follows`：任务记着这家公司（`company`），且它的连接上
   有这家覆盖这个月的账套（按任务记的 `company_branch` 用 `identity.for_branch` 筛）也算，`month()` 传 `row.name`；任务文件计数随之带上。
   顺带按建议：`service._rule_company` 先认任务记着的公司（主档还在、连接本人能用），`task_overview` / `company_of_rules` 不再因旧年度
   账套从目录消失而落到「其他」；`service.tasks_of` 加 `company`，主档页也列这类任务。测试：`MonthLedgerTests` 加一项（任务存 68 年度键，
   2026-09 照样计数、`month_files` 收到它；分店不符、没记公司、别家的不算；2025-09 按存下的键照旧）；`TaskOverviewTests` 加一项
   （归属、提醒点开、主档已删回落、收件人用不了连接、主档页清单）。

## 第 6 批：界面（建在新的 CC 外壳上，2026-10-16，本地提交，未推送）

分支 `pearnly/multi-desktop`，基线 `53402f13`（并入 CC 外壳分支与合并审查修复之后）。提交：`decf1704` feat（代码与测试）、本条 docs（ADR-021 第 41–48 条
与有意变化、架构说明两处、本记录）。没有新补丁、没有新 DocType 字段；新词条服务端 5 条（en / zh / th / ja）、CC 页面 6 个词（en / zh / th）；
没改已有文案。界面只用 `globals.css` 变量与现有零件（设置的 Row / Heading / Switch / 菜单，任务页的 Field / Chip，待处理的当场选那一套），
没有新样式、没有拖动。

做了什么（对照本记录各批「留给第 6 批」与 T1「前端待办」）：

- **对话页每次打开带设备密钥登记**（`lib/desktop.ts`）：`thisComputer(state)` 只在桥 features 含 `devices` 且有 `device.key` 时成立；
  `register` 带 `device_key` / `device_name`（有设备密钥时不再带旧路的 `replace`）；`exists` 而本机没有能用的成员（`needsKey`：一个成员都
  没有，或有成员带 `device_state`）→ 换请求号带 `rekey` 再登记；`superseded` 第一次就带 `rekey`；`update_needed` → 新的 `Link`
  `update_needed`（设置「连接」写「需更新」+「下载新版」，桥的 `download_update`）。`useAutoConnect`：1.7.0 每次打开都登记，之后听桥的
  `changed`，成员新出现 `device_state` 马上再登记一次（每个成员每种状态自动试一次）。旧版桥照旧（没有成员才连、不带设备密钥）。
- **常驻电脑碰到 `claimed_elsewhere`**〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕：选「页面自动恢复」（ADR 第 42 条）。（审查第 3 条更正：停着的那台在池里没有自己的电脑行，别的
  电脑的「电脑」列表**不列它**；窗口没开时没有任何地方提示，见下方审查一节。）
- **「电脑」列表与移除**：服务端 `rules.computers` 每行多 `id` / `connection` / `always_on`（10-16 删）/ `can_remove`（`devices.owners` +
  `credentials.manages`）；`companion.api.remove_device` → `credentials.remove_device`（加锁顺序与幂等见 ADR 第 43 条；可达行的连接由
  `connections.pool.device_connections` 给）。页面 `PoolComputers`：浏览器的「连接」与桌面版「此电脑 → 连接」下的「电脑」同一个组件，
  常驻的写「常驻模式」，能移除的行尾「···」→「移除」（同侧栏行菜单，不加确认）。
- **全所可用开关**：`connections.service.set_members` / `api.set_members`（ADR 第 44 条）；`records` 每行带 `manageable`；页面 `AllMembers`
  只对管理员、Express 主连接出现，关掉后逐人开关名单（团队成员取 `team.api.api_list_members`）。
- **录入入口状态**：`companion.pool.state` 加 `computers`；Desk `targets.js` 的 `describe` 四种状态、新 `account_note`（按账套问
  `needs_update`）接到「新建录入」弹窗（`intake-choices.js` 账套下拉的说明）；CC 定时任务页的目标卡片同一套、选定 Express 账套后同样提示；
  LINE `_state` 有电脑却没有能写的说「没有能写的电脑」；CC 工具 `intake_options` 多给 `cc_desktop_online`。
- **读者编号**：CC 与 Desk 定时任务页文件夹下拉值 = `folderValue(读者编号, 根目录编号, 相对路径)`（各自编码后竖线连），保存传读者编号，
  文件夹计数按读者编号找；`reader_gone` → 状态「电脑已移除」（排在「CC 桌面版不在线」之前），原来的文件夹照样摆着并写明。公司文件夹
  `company_folders.view` 多给 `reader_gone`；页面解绑、刚移除先藏都按 `reader_id`，本机那一行按 `folderIsMine`（连接 + 电脑名），电脑已移除的行
  哪里都能「移除」；主档页「＋」预填任务文件夹用 `folderValue`、不拿电脑已移除的那一行。
- **T1 前端**：任务列表 / 表单 / 主档页与会话栏的任务行写「跟随公司当年账套」（`companies.service.tasks_of` 多给 `follows`）；列表与
  运行记录列 `results.account_sets`（「账套名 张数」，用 ` · ` 连）。待处理页与月结台账：`companies.pending._items` / `ledger._document_item`
  对 `account_set_stop` 的单给 `action = "account_set"`；新接口 `assistant.pending_api.account_sets` / `choose_account_set`；页面
  `AccountSetRow`（同当场选那一行的样子：点开、一个下拉、「确认」，没有可换的「交给 CC」）。
- **推送记录由哪台电脑写入**：`written.writer`（ADR 第 48 条，钩子 `cowork_device_labels`）。

新文案（都是新键，没有改已有的）：

| 键 | en | zh | th | ja |
|---|---|---|---|---|
| `intake.agent.state.offline` | Computer offline | 电脑离线 | คอมพิวเตอร์ออฟไลน์ | コンピューターがオフライン |
| `intake.agent.state.noWriter` | No computer can write | 没有能写的电脑 | ไม่มีคอมพิวเตอร์ที่บันทึกได้ | 書き込めるコンピューターがありません |
| `intake.agent.state.needsUpdate` | CC Desktop needs update | CC 桌面版需更新 | CC เดสก์ท็อปต้องอัปเดต | CC デスクトップの更新が必要 |
| `cowork.folder.readerGone` | Computer removed | 电脑已移除 | นำคอมพิวเตอร์ออกแล้ว | コンピューターは削除済み |
| `cowork.folder.followCompany` | Company's account set for the year | 跟随公司当年账套 | ชุดบัญชีของบริษัทตามปี | 会社のその年の会計データ |
| CC `st.computers` | Computers | 电脑 | คอมพิวเตอร์ | — |
| CC `st.needsUpdate` | Update required | 需更新 | ต้องอัปเดต | — |
| CC `st.allMembers` | Everyone in the firm | 全所可用 | ทุกคนในสำนักงานใช้ได้ | — |
| CC `st.members` | Members | 成员 | สมาชิก | — |
| CC `co.folderGone` | Computer removed | 电脑已移除 | นำคอมพิวเตอร์ออกแล้ว | — |
| CC `co.followCompany` | Company's account set for the year | 跟随公司当年账套 | ชุดบัญชีของบริษัทตามปี | — |

沿用的已有词：「常驻模式」（`st.alwaysOn`，用在「电脑」列表一行的说明里；10-16 删）、「移除」「重试」「下载新版」「需要管理员账号」「CC 桌面版未连接」、
`co.accountSets`「账套」、`co.confirm`、`co.handToCC`。

证据：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_cc_desktop_devices.py` | 66 OK（新增 `RemoveDevice` 8 项：登记人移除、两条连接按名字顺序先锁再锁密钥最后锁电脑行、只有可达行的连接也锁、别人 / 外人拒而管理员能、别的归属与不存在同样拒、移除两次只一次、移除后同一台作为新电脑回来、`owners` / `labels`、`manages`） |
| `tests/test_cowork_companion_credentials.py` | 17 OK（公开接口集合加 `remove_device`，只收 POST、走 `_local_transaction`） |
| `tests/test_cowork_connection_read_locks.py` | 13 OK（新增 `set_members`：只改两项不升版本、只核新加的人、相同不写、名单不能空 / 重复 / 坏 JSON、MR.ERP 拒、非管理员拒、`records` 带 `manageable`） |
| `tests/test_cowork_express_dispatch.py` | 51 OK（池的状态带 `computers`，分清没有能写的电脑与一台都没有） |
| `tests/test_cowork_companies.py` | 114 OK（新增：待选账套停下的单给 `account_set`、没去向的照旧交 CC；`account_sets` 只列覆盖单据日期的、`choose_account_set` 先抄到票上再按 CC 同一条路重推，别的年度 / 空 / 不在列的拒、非待选账套的单不给、没日期不给、没有录入权限拒；月结台账同样给 `account_set`） |
| `tests/test_cowork_folder_intake.py` | 124 OK（公司文件夹带 `reader_gone`，按读者编号解绑） |
| `tests/test_cowork_folder_computers.py`（容器内） | 7 OK（每行多 `id` / `connection` / `always_on` / `can_remove`；登记人与管理员才能移除，旧槽位密钥不给） |
| `tests/test_cowork_assistant_tools.py` | 187 OK（`written.writer` 一次取齐电脑名、没记电脑的不给，`outcomes` 同样） |
| `tests/test_companion_contract.py` | 68 OK（新增页面一侧：从桥的 `device` 取设备密钥、参数与服务端同名、`superseded` 带 `rekey`、每次打开都登记、听 `changed`） |
| `tests/test_assistant_ui.py` | 新增 `MultiDesktopBehaviourTests`（真跑 `lib/desktop.ts` 七种情形：新电脑配对、每次打开 exists 不配对、superseded rekey、本机没成员 exists 换请求号 rekey、claimed_elsewhere 先按自己的电脑登记、rekey 403 → 需要管理员账号、旧版不带设备密钥 → 需更新；读者编号编码拆得回原样、本机那一行按电脑名认、预填不拿电脑已移除的行）、「电脑」一行的常驻与「···」、第 6 批新词三种语言齐全；改了 3 处旧断言（`taskSeedOf` 多传电脑名、`setGone` 按读者编号，都是有意改的） |
| `tests/test_cc_mobile_content.py` | 改 1 处旧断言（同上 `setGone`） |
| 容器内 `tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（186 个文件，不含源码对照） | 184 OK；`test_cowork_invoice_checks.py`、`test_cowork_table_input_runtime.py`（缺 `docx`）与基线相同的两个失败 |
| `python3 scripts/check-architecture.py`、`i18n-build.py --check`、`check-i18n.py --strict` | PASS / PASS / 0 |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（含两个前端构建） | 91 步全过，退出码 0（第一次跑架构检查拦下 `assistant → targets` 新依赖：待选账套的比较挪进 `companies.pending`，不扩大基线；改后整套重跑全过） |

给测试窗口的点验（第 7 批真机前，测试机实时源码 + CC 桌面版 1.7.0）：

1. 对话页打开：服务端电脑行的 `signed_in_user` 是这次登录的人；再开一次不换密钥（`token_generation` 不变）。
2. 两台电脑同一个账号或不同账号各开 CC：「电脑」列表两行；一台关机后显示「连接中断」，Desk / CC 录入入口的 Express 写「电脑离线」且能选、能确认排队。
3. 「···」移除另一台（登记人或管理员）：那台的密钥失效、列表少一行；它读的定时任务状态「电脑已移除」、公司文件夹那一行「电脑已移除」可移除；那台重开 CC 后作为新电脑回来。非登记人非管理员看不到「···」。
4. 管理员在「电脑」下关掉「全所可用」：名单出现；关掉某个成员后那人在录入入口看不到 Express；再打开全所可用恢复；连接的 `record_revision` 不变、账套目录不需重读。
5. 两台都映射 S 盘时新建定时任务：文件夹下拉每项前面带电脑名，保存后任务的 `reader_device` 是选的那台。
6. T1 任务：列表写「跟随公司当年账套」，运行记录列出实际推往的账套与张数；T8 / T9 同期停下的票在待处理页点开选一份「确认」后推往那份，票上的去向换成它。
7. 1.6 的桌面版在已有 1.7.0 的池里打开对话页：设置「连接」写「需更新」+「下载新版」。
8. 配对码抄到两台（`claimed_elsewhere`）：对话页开着的那台自己重新登记、成员恢复；~~常驻电脑窗口开着时同样自愈~~（10-16 没有常驻了）。

没做 / 拿不准：

- 只在本机闸门与替身上验过；真实 Site、HTTP 链、测试机、真 Windows 都没跑（第 7 批）。`remove_device` 在 MariaDB 上逐条连接 `FOR UPDATE` 的锁等待没量。
- 网页没有推送记录详情页，「由 {电脑} 写入」只进了 CC 的 `written.writer`，没加网页界面与词条。
- 全所可用关掉后的名单只能从团队成员里勾（团队成员接口只限管理员，开关本来就只给管理员）；连接名单里已有、团队里已停用的人照样列出、可以勾掉。
- 〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕自动恢复靠对话页开着：常驻电脑窗口被关掉（托盘里没开窗口）时要等有人打开 CC；没加服务端心跳信号（心跳带不出登录会话，换不了密钥）。
- 「电脑离线」等状态在 Desk 只改了录入入口卡片与「新建录入」的账套说明；定时任务弹窗沿用同一个 `describe`，CC 工具只多给布尔值，没有新文案。

第 7 批真机要验：上面点验 1–8 各项在两台真机上走一遍；`remove_device` 与心跳 / 领取并发（连接行锁等待、快照隔离 1020）；1.7.0 收到
`remove` 后的表现（密钥 401 时成员状态、重开对话页作为新电脑回来）；`set_members` 收窄名单后排队单按录入人转人工的数量；对话页每次打开
都 `register` 的请求量（3 租户 × 10 台）。

### 第 6 批对抗审查与修复（2026-10-16，本地提交，未推送）

审查在 `723bffec` 上读代码（没在真 Site、真 Windows 上复现），没有阻塞项：6 中、8 低，共 14 条，全部成立；13 条已修，1 条（第 3 条）更正记录
与 ADR、本机一侧已修、窗口没开时的提示列为没做。修复提交 `15b41627`（基线 `723bffec`），本条 docs 另一个提交。没有新补丁、没有新 DocType 字段、
没有新词条；改了本批新加、尚未发布的 `cowork.folder.followCompany` 的 en / th / ja 与 CC `co.followCompany` 的 en / th（中文不变），没改已有文案。

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | 中 | 签发人失权（全所可用关掉、名单里去掉他）后那台电脑的密钥作废，有权的人打开 CC 时 register v2 只回 `exists`，电脑一直写不了 | 已修：`credentials._register_device` 的 `exists` 分支里那把密钥的签发人已用不了这条连接（`issuer_valid`，同认证 `_issuer`），按 rekey 吊销旧钥、发本人签发的新钥、回 `created`；不要求登记人或管理员（本人已过 `_use`），登记人不变。测试：签发人移出名单后名单里另一人打开 → 新钥、旧钥吊销、只剩一把，再开回 `exists`；签发人有效时别人打开不换 |
| 2 | 中 | 「···」移除不持久：对话页每次打开都 register，碰到退役电脑就交出摘要、建新电脑发钥，几秒内悄悄回来；ADR 写的是要有人重新登录 | 已修（服务端一侧）：退役电脑的自动登记回 `{"status": "removed"}`，不交摘要、不建电脑、不发钥（登记人、管理员、`rekey` 都一样）；只有带 `rejoin`（页面上人亲手点「连接」）才走「作为新电脑加入」。页面 `Link` 加 `removed`：设置「连接」写「电脑已移除」（现有 `co.folderGone`）+「连接」，点了带 `rejoin`。测试：移除后三种人自动登记都回 `removed` 且库不变、`rejoin` 才 `created`；页面行为测试第 8 步（自动登记不带 `rejoin` → `removed` 不配对；点「连接」带 `rejoin` → 配对） |
| 3 | 中 | 记录与 ADR 第 42 条说常驻电脑碰到 `claimed_elsewhere` 停着时别人在「电脑」列表看到它「连接中断」——不成立：它在池里没有自己的密钥与电脑行，列表不列它，录入入口照样「已连接」 | 成立。ADR 第 42 条与本记录更正；本机设置「连接」那一行现在写现有「已在另一台电脑连接」+「重试」（第 8 条）。**窗口没开时仍没有任何地方提示**（要服务端记下认领冲突、或桌面端托盘通知，都要新文案或新字段），列为没做，第 7 批真机后定 |
| 4 | 低 | 待处理页可换的账套按票上现算的公司、不传分店：会列出别的分店甚至别家公司的账套；选了以后任务默认不套用、可能被硬闸拒，而去向已先 commit、原因被换掉，再也选不了 | 已修：`folder_intake.push.source_rules`（`fixed_for` 同一条路，`fixed_for` 改用它）找回出这张票的任务，`companies.pending.account_sets_for` 按任务记的公司与分店调 `tasks.books_of`，找不到任务才退回现算归属；`choose_account_set` 改为先推、推成了（或停在要人选）才 `retarget`，推不成也没进账本就把原来的去向与「待选账套」原因放回，这次的原因只回给页面。测试：分店任务只列该分店两份、选总部那份回 `stale` 不推；任务删了退回现算（四份）；推不成放回原因与去向、不 `retarget`；推成先推后抄 |
| 5 | 低 | 1.6 升 1.7.0 首启时对话页的登记先于旧槽位密钥 `device_hello`：服务端建新电脑发新钥，配对换掉旧钥，旧槽位密钥留在没人认领的「旧电脑」上永不吊销，写账可能分派给它 | 已修（CC 桌面版 1.7.0 一侧，尚未发布）：`DesktopSettings.pair` 按端点换掉成员旧密钥前，旧钥没有 `device_state` 就先 `device_link.settle_old_key`（拿旧钥 `device_hello`）：服务端照「新版先 register、后 hello」回 `superseded`、吊销旧钥、把旧电脑的可达行与读文件夹任务转给这台再退役旧电脑。不用 `device_leave`（同一配对码抄到两台时会吊销另一台正在用的钥匙）；失败、旧服务端、旧钥已失效都不拦配对。ADR 第 49 条。测试：桌面端 3 项（先 hello 再配对、带标记 / 同一把 / 新成员不发、hello 抛错照常配对、`settle_old_key` 用旧钥发 hello 且吞掉 401）、契约 1 项（`pair` 里先 settle 后配对、用 hello 不用 leave） |
| 6 | 低 | 名单候选取 `team.api.api_list_members`（另一种管理员、列只有别的产品角色的人）；管理员关掉全所可用而自己不在名单里时「电脑」列表变空、移除不了 | 已修：新 `connections.api.member_choices`（只收 GET，`service.member_choices`：只限管理员、只给 Express，候选 = `access.assignees`，与 `require_assignee` 同一组角色、启用），页面改用它；`rules.computers` 对管理员不在名单上的连接按管理口径读（`_managed_connection`，同 `records`）。测试：`member_choices` 只给管理员、MR.ERP 拒、不加锁；`assignees` 的 SQL 与角色；管理员不在名单上照样列电脑且能移除、非管理员仍不列 |
| 7 | 中 | 桌面版「此电脑 → 连接」下的「电脑」一节没电脑时写「还没有电脑连接」+「下载 CC 桌面版」 | 已修：`PoolComputers` 加 `inDesktop`，桌面版里一台电脑都没有时整节（含「电脑」标题）不出现，只剩管理员的「全所可用」；浏览器照旧 |
| 8 | 中 | 成员带 `device_state` 时设置只写「未连接 / 连接」，看不出被另一台顶掉 | 已修：`LinkAction` 收 `deviceState`，`claimed_elsewhere` 写现有「已在另一台电脑连接」、`superseded` 写「未连接」，按钮都是现有「重试」；没加新词 |
| 9 | 低 | 任务表单的「跟随公司当年账套」看存下的旧任务、不看当前选的账套 | 已修：CC 与 Desk 的表单都去掉这句，只在列表与主档页 / 会话栏的任务行写；表单只按账套提示「CC 桌面版需更新」 |
| 10 | 低 | 推往的账套拼成「账套名 张数」、用「 · 」连，和账套名里的「 · 」混在一起 | 已修：每份用现有 `cowork.folder.pair`（「{0}：{1}」），几份之间用分号；运行记录里单独一行（CC 与 Desk 同一写法） |
| 11 | 低 | Desk 列表结果列不列推往的账套；Desk 定时任务弹窗不提示「CC 桌面版需更新」 | 已修：结果列在计数后用分号接上推往的账套；弹窗账套下拉按账套调 `p.intake_targets.account_note`，写在下拉说明里 |
| 12 | 低 | `followCompany` 的 en / th / ja 只是名词短语，看不出「跟随」 | 已改（本批新词，未发布）：en「Follows the company's account set for the year」、th「ตามชุดบัญชีของบริษัทในแต่ละปี」、ja「会社のその年の会計データに従う」；CC `co.followCompany` 的 en / th 同步；翻译包按 `i18n-build.py` 重生成，删掉旧英文原文的那一行别名 |
| 13 | 低 | 选的账套不在列时借用「请在新建录入中重新读取」 | 已修：`choose_account_set` 回 `{"status": "stale"}`，页面重取可换的账套再让人选，不弹错误，不借词 |
| 14 | 低 | 任务列表的文件夹一栏、编辑时补回的那一项不带读文件夹的电脑名 | 已修：`listing` 在池里不止一台电脑时给 `reader_label`（同步报的电脑名 → 电脑记录上的），CC 与 Desk 的列表文件夹一栏、补回的下拉项前面带「电脑名 · 」；一台时不给 |

文案（给用户看）：

- 没有新增词条。沿用的已有词：「电脑已移除」（`co.folderGone`，设置「连接」那一行）、「已在另一台电脑连接」（`st.inUse`）、「重试」「连接」、
  `cowork.folder.pair`「{0}：{1}」。
- 改了本批新加、尚未发布的一条（四种语言里中文不变）：

| 键 | en | zh | th | ja |
|---|---|---|---|---|
| `cowork.folder.followCompany` / CC `co.followCompany` | Follows the company's account set for the year（原 Company's account set for the year） | 跟随公司当年账套（不变） | ตามชุดบัญชีของบริษัทในแต่ละปี（原 ชุดบัญชีของบริษัทตามปี） | 会社のその年の会計データに従う（原 会社のその年の会計データ；CC 没有 ja） |

- 想改的已有文案（这次都没改）：同第 6 批——「CC 桌面版未连接」只在池里一台电脑都没有时出现（10-16 起改指「你没有电脑」），文字没动；「常驻模式」（`st.alwaysOn`，10-16 删）用在
  「电脑」列表每行的说明里，可考虑缩成「常驻」。

证据（本机 `PATH=$HOME/pearnly-devtools/bin:$PATH`，python3 3.12；需要 Frappe 的在一次性离线容器 `pearnly/frappe-sample:16.34.1`、
`--network none`、工作树只读挂载里跑）：

| 检查 | 结果 |
|---|---|
| `tests/test_cowork_cc_desktop_devices.py` | 68 OK（66 → 68：签发人失权换钥、签发人有效不换；「移除后同一台作为新电脑回来」改成「自动登记只回 removed、rejoin 才回来」；加锁顺序测试改走 `rejoin`） |
| `tests/test_cowork_companies.py` | 116 OK（114 → 116：推不成放回能再选、按任务的公司与分店列；原测试改为先推后抄、不在列回 `stale`） |
| `tests/test_cowork_connection_read_locks.py` | 15 OK（13 → 15：`member_choices`、`assignees`） |
| `tests/test_cowork_folder_task_batch.py` | 33 OK（32 → 33：`reader_label` 只在多台时给） |
| `tests/test_cowork_folder_computers.py`（容器内） | 8 OK（7 → 8：管理员不在名单上照样列、能移除） |
| `tests/test_companion_contract.py` | 69 OK（68 → 69：换钥前用 hello 不用 leave；`register` 参数与页面多 `rejoin`，两处旧断言有意改） |
| `tests/test_assistant_ui.py` | 127 OK（页面行为第 8 步 removed / rejoin；设置页与任务页审查修复的源码断言两项） |
| `tests/test_cowork_folder_intake.py`、`folder_follow_year`、`push_fixed`、`companion_credentials` | 124 / 29 / 18 / 17 OK（`fixed_for` 改用 `source_rules` 后照旧） |
| CC 桌面版 `companion/tests/test_device_link.py`、`test_desktop_settings.py` | 本机没有 PySide6 / httpx / dbf / pytest，用替身脚本逐个函数跑：改前 29 OK + 3 个替身导致的失败 / 20 OK，改后 32 OK + 同样 3 个 / 20 OK（新增 3 项全过）；完整 `pytest`（Companion checks，Windows）没跑，第 7 批 |
| 变异核对 | 去掉 `removed` 分支、去掉签发人失权判断、去掉推不成放回、改回现算公司、去掉 `member_choices` 的 Express 限制、`reader_label` 不看台数：对应测试各自失败（6 / 6） |
| 容器内 `tests/test_cowork_*.py`、`test_companion*.py`、`test_queue_targets.py`（不含源码对照） | 改前（`723bffec` 导出）与改后同一组结果：174 个文件 OK，其余 15 个在这个容器的跑法下改前改后同样失败（含基线的 `invoice_checks`、`table_input_runtime`；其余 13 个是这次在 `sites` 目录下跑的环境差异，与本次改动无关） |
| `check-architecture.py`、`i18n-build.py --check`、`check-i18n.py --strict`、`check-frontend-i18n.py --strict` | PASS / PASS / 0 / PASS（不扩基线） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（含两个前端构建） | 91 步全过，退出码 0（提交前的工作树，含本条文档；代码与 `15b41627` 相同） |

给测试窗口的点验（替换第 6 批清单对应项，测试机实时源码 + CC 桌面版 1.7.0）：

1. 对话页打开：电脑行的「最近登录的人」是这次登录的人；再开一次密钥代数不变。
2. 两台电脑各开 CC：「电脑」列表两行；关掉一台后 Desk 和 CC 的 Express 写「电脑离线」，仍能选、能确认排队。
3. 登记人或管理员用「···」移除另一台：那台密钥失效、列表少一行；它读的定时任务与公司文件夹标「电脑已移除」、公司文件夹那一行能移除；
   **那台重开 CC、刷新、重启都不回来**，设置「连接」写「电脑已移除」+「连接」，点「连接」后作为新电脑回来；非登记人非管理员看不到「···」。
4. 管理员关掉「全所可用」：名单只列有录入 / 推送记录 / 管理角色的人；关掉某人后他看不到 Express；**管理员自己不在名单里时「电脑」列表照样
   有行、能移除**；再打开恢复，连接版本不变。
5. 签发人失权：甲在一台全所共用的电脑上登记，管理员关掉全所可用、名单只留乙 → 乙在那台电脑上打开 CC，电脑换成乙签发的新钥、恢复写账
   （不需要管理员 rekey）。
6. 两台都映射了 S 盘：新建任务的下拉每项带电脑名；**任务列表文件夹一栏也带电脑名**；只有一台时不带。
7. T1：列表写「跟随公司当年账套」，表单不写；列表与运行记录的推往账套写成「ABC · 2569：2; ABC · 2568：3」，运行记录里单独一行；Desk
   列表结果列同样有。T8 / T9 同期停下的单在待处理页点开只列这家公司这个分店的账套；选一份推不成时那一行下面写原因、仍能再选。
8. 1.6 桌面版在已有 1.7.0 的池里：设置写「需更新」+「下载新版」。**1.6 升 1.7.0 首启**：「电脑」列表不多出一台永远离线的旧电脑。
9. 配对码抄到两台：对话页开着的那台自己恢复；没恢复时本机设置写「已在另一台电脑连接」+「重试」。
10. 在 CC 桌面版里、池里还没有电脑时打开设置「此电脑 → 连接」：没有「电脑」一节、没有「下载 CC 桌面版」。

没做 / 拿不准：

- 〔10-16 取代，见文末「按用户 10-16 简化：谁的活谁的电脑做」〕常驻电脑碰到 `claimed_elsewhere` 而窗口没开：任何地方都没有提示（第 3 条）。要么服务端记下认领冲突在「电脑」列表那台的行上说一句，要么
  桌面端托盘通知——都要新文案，先列给用户定。
- 第 5 条只在桌面端一侧修：升级首启时旧钥的 hello 要连得上服务端；连不上时旧电脑照旧残留（不拦配对）。真机时序没验。
- 名单候选按角色列，不看团队页的启用以外的状态；名单里已有、现在已停用或失去角色的人照样列出、能勾掉。
- 只在本机替身、离线容器与闸门上验过；真实 Site、HTTP 链、测试机、真 Windows 都没跑；CC 桌面版完整 `pytest` 没跑。

第 7 批真机要验（在第 6 批所列之上加）：上面点验 3、5、8 的升级首启时序；`removed` 之后点「连接」的 `rejoin` 在真 Site 上的锁顺序；
`member_choices` 在真库上的人数与排序；`choose_account_set` 推不成放回原因后早上汇总与月结台账的计数。

## 按用户 10-16 简化：谁的活谁的电脑做（2026-10-16，本地提交，未推送）

来源（用户 10-16 原话）：「我不太明白为什么会有什么常驻模式，不能像 claude 一样在那台电脑就处理那台电脑的工作吗？……我怎么听上去有很多阻碍」；
开发窗口给出简化方案后用户回「以上可以」。规则与取代了哪几条见 [ADR-021](../adr/0021-cc-desktop-multiple-computers.md) 第 50–56 条与
「有意变化」「兼容」里 10-16 的几条。基线 `254b2e28`（T5 第 1–6 批 + T1 + 已并入 CC 外壳分支 `f5c04929`）。
提交：`ecabec82` refactor（代码与测试）、本条 docs（ADR-021 第 50–56 条与有意变化 / 兼容、方案 D3 标注、架构说明、本记录）。没有新补丁：新字段可为空，空值有明确含义。

勘察时提给开发窗口的 6 点（D-a…D-f）按勘察推荐施工：

| 点 | 定成 |
|---|---|
| D-a 定时任务谁写 | 读那个文件夹的电脑：推送记录新字段 `route_device`（文件行的 `reader_device`，没有取任务上的；补丁前的旧任务都没有 → 按负责人自己的电脑）。只读成员（没装 Express）读的任务因此一律「访问不到」 |
| D-b 「他的电脑」 | 电脑行新字段 `signed_in_users`：凡在那台电脑登录过 CC 的人；共用电脑每人登录一次就都算，只能整台移除才去掉 |
| D-c 没有登录记录的电脑 | 过渡口径：谁的单都写（不弹框），一旦有人登录 CC 就只归登录过的人（ADR 第 50 条的有意例外） |
| D-d 同一份账套的排头卡死 | 排头只在这台能写的单里取（`queue_heads.scope`，内外两层同一套过滤） |
| D-e 科目历史与主档刷新 | 当账套的公共资料：够得到就读、先来先读；严格按发起人的只剩回读与写账 |
| D-f 询问框 | `attended` 只看是不是定时任务、录入人是否在这台电脑登录过 CC；桌面端不再看本机常驻设置 |

按新规则自己纠正的一处：任务书写 `attended(job) = not job.route_device`；改成「不是定时任务 **且** 录入人在这台电脑登录过 CC」——没有登录记录的电脑
（过渡期替谁都写）也不弹框，否则一台只认过电脑、没人打开过对话页的电脑会替别人的单弹询问框、卡住整台的写入线程（与以前「替别人写不弹框」一致）。

另做的一处减负：领取写账时这台电脑的登录集合用 `devices.signed_in_of`（一次按主键取电脑行），排头为空时不再读全池（`pool_peers` 仍在有排头时才读），
保持「平时没有排队的单，每 3 秒的领取不查池」。

### 改了什么

服务端（`S` = `apps/pearnly_cowork/pearnly_cowork`）：

- 归属判据：`S/connections/member.py` 新纯函数 `serves(users, actor)`；`S/connections/pool.py` 新 `owned(peers, actor, device=)`（定时任务只有那台、
  其余按人）；`Member.peers` 删掉（只有目录刷新的档位在用）。
- 分派纯函数 `S/companion/route_contracts.py` 整份重写：删 `tier`、`ALWAYS_ON` / `OTHER` / `BETTER`、`HANDOFF_SECONDS`、`queued()`、`Peer.user` /
  `Peer.always_on`、`Job.queued_at`；新 `Peer.users`、`Job.route_device`、`owns`、原因 `NOT_MINE`、问责 `READER`；判断顺序 够不到 → 不归它 → 黏在别处
  （只挡不接）→ 载荷版本（只在归这张单、又够得到的电脑里判）→ `own` / `reader` / `sticky`；`attended(job, peer)` 见上。`STICKY_SECONDS` 保留。
- 排头 `S/companion/queue_heads.py`：`Q_HEADS` 内外两层加 `_OWNED` 过滤（参数恒定，查询串不变）；`scope(device=, users=, user=)` 四种取法；
  `heads(…, device=, users=, user=)`；选列去掉 `queued_at`、加 `route_device`。
- 领取 `S/companion/transport.py`：排头按本机与它的登录集合取；`Job` 带 `route_device`；`attended` 用新函数；`_busy`、隔离、锁、位置替换不变。
- 池里各台电脑 `S/companion/credentials.py:pool_peers`：`users` = `signed_in_users` + `signed_in_user`（`devices.signed_in`），不再给 `always_on`。
- 电脑表 `cowork_cc_desktop_device.json`：删 `always_on`，加 `signed_in_users`（Small Text，只读，JSON）；`devices.note_signed_in` 同时并进登录集合，
  `create` / `create_claimed` 带上首个使用人；`hello` 不再写 `always_on`；`device_contracts.hello_request` 不再解析、不再返回它（多出的键照旧忽略）。
- 池的状态 `S/companion/pool.py`：`state(…, user=, device=)` 只看 `owned` 出来的电脑：`computers` = 他有没有电脑，`writers_known` / `online` 在他的电脑里判，
  `needs_update` 看他自己的排头（按电脑时看冻结给那台的）；`for_user(…, device=)` 传当前用户。`api.online`、Desk `targets.js`、LINE、CC 工具的
  `destinations.online / selectable` 随之按人，不用改。
- 能不能确认 `S/push/native/service.py:has_writer(connection, key, *, actor, route_device='')`：确认、「还能不能确认」、准备回执的 `has_writer`（`S/push/application/service.py`）
  都按录入人 / 读文件夹的电脑判；都不能写回现有 `push.no_writer`。`queued_at` 的三处写入删掉；`project` 多给 `route_device`。
- 推送记录 `cowork_push_record.json`：删 `queued_at`，加 `route_device`（标签沿用「Reader Device / 读文件夹的电脑」，只读、不可复制，
  进 `push/native/documents.py` 的不可改字段，不进请求指纹）；`ledger.reserve(…, route_device=)`、`application.prepare` / `submit(…, route_device=)`。
- 定时任务 `S/folder_intake/`：`push._reader(entry, rule)` → `submit(route_device=)`（只 Express）；`destinations.queueable(…, device=)` /
  `ExpressTarget.selectable(connection, device)` 按那台判；`follow.choose` 的 `has_writer` 带 `route_device=rule.reader_device`。
- 按需请求 `S/companion/request_service.py`：`_turns` 去掉 `decide` 与在线档位——回读只给发起人登录过 CC 的电脑，`SHARED_KINDS`（科目历史）够得到就给；
  `waiting(…, user=)` 只问他的电脑，有电脑却都够不到回 `reason: "unreachable"`；`readback/service.py` 两处传发起人，`readback/history.py` 不传（公共资料）；
  CC 工具回读的 `READBACK_WAITING` 与工具说明加 `unreachable`（给模型看的英文，不是界面文案）。
- 目录刷新 `S/connections/refresh.py`：删 `tier` / `my_turn` / `HANDOFF_SECONDS`，账套级读够得到它的电脑先来先读；`JOIN_SECONDS` 保留。
- 「电脑」列表 `S/folder_intake/rules.py:computers` 不再给 `always_on`。

CC 桌面版（`C` = `companion/src/companion`）：删 `C/always_on.py`（防睡眠、开机锁屏）；`gui_tray.py` 删 `KeepAwake` / `lock_screen` 与 `on_always_on`，
保留 `setQuitOnLastWindowClosed(False)`、关窗口进托盘、自启指向核对；`desktop_settings.py` 删回调、快照的 `always_on`、`set_always_on`，换钥前旧钥认电脑不再带它；
`tomo_bridge.py` 删 `always_on` 方法；`config.py` / `profile_store.py` 删字段（加载按字段白名单，旧文件里的读时忽略、写回丢掉）；`device_link.py` 的 `hello_body` /
`settle_old_key` / `Announcer` 不再报常驻；`gui_tray_workers._unattended` 只看 `attended is False`；`README.md` 记一条。开机自启仍是默认（`autostart=True`，配对时写入），
设置里「开机时自动启动 CC」保留。

前端（`frontend/assistant/src`）：`settings.tsx` 删常驻开关与「电脑」一行里的「常驻模式」、`Computer.always_on`；`lib/desktop.ts` 删 `DesktopState.always_on`、
改一句注释；`i18n.ts` 删 `alwaysOn`（三种语言）。

### 去掉的复杂度

档位（发起人 → 常驻 → 其他：`route_contracts.tier`、`refresh.tier` / `my_turn`、`request_service` 里的 `decide` 与在线档位）；两处 `HANDOFF_SECONDS`（120 秒谁都能领）
与 `queued_at` 计时（字段、三处写入、`queued()`）；「更好的电脑在线就先等」（`BETTER`）；「替别人写不弹框」靠常驻推导；`Member.peers` 回调；常驻电脑窗口没开就没地方
提示这个第 7 批待定项；桌面端 `always_on.py` 整个文件、设置开关、桥方法、`hello` 字段、`test_always_on.py`。

### 文案

- 新加界面文案：无。新加 DocType 字段标签一条：`Signed-in Users`（登录过的账号 / ผู้ใช้ที่เคยลงชื่อเข้าใช้ / サインインしたことのあるユーザー，术语表与翻译包同步）。
  `route_device` 沿用已有标签「Reader Device / 读文件夹的电脑」。
- 沿用的现有文案：「已连接」「电脑离线（仍可选）」「没有能写的电脑」「CC 桌面版未连接」（改指「你没有电脑」）「CC 桌面版需更新」、`push.no_writer`、
  `cowork.folder.account_set_unreachable`、`cowork.folder.companion_offline`、「开机时自动启动 CC」。
- 不再使用：「常驻模式」（`st.alwaysOn`，三种语言）；DocType 标签 `Always On`、`Queued At (UTC)`（术语表 `messages.tsv` 与 `translations/{en,zh,th,ja}.csv` 一并删）。

### 检查

| 命令 | 结果 |
|---|---|
| `python3 tests/test_cowork_express_dispatch.py` | 61 OK（分派规则整组重写：只由录入人自己的电脑写、同事电脑等多久都不领、共用电脑每个登录过的人都算、没有登录记录的电脑谁的都写、定时任务只由读文件夹那台写、载荷版本只在他的电脑里判、别人的电脑黏住只挡不接、`attended`；领取：同事的排头不挡我的单、同事的单永不领、定时任务按 `route_device`、无登录记录 `anyone`；排头查询在 SQLite 上按四种 `scope` 跑；账本：`has_writer` 按人 / 按读者电脑、只有同事电脑够得到时确认拒 `push.no_writer`、「还能不能确认」同口径、不再记 `queued_at`；池的状态：自己离线 → 离线仍可选、只有同事够得到 → 没有能写的电脑、自己一台都没有 → 未连接、同事在线不算自己在线、无登录记录的电脑过渡期算、按读者电脑、需更新只看自己的排头） |
| `python3 tests/test_cowork_companion_requests.py` | 38 OK（回读只由发起人的电脑领、等多久同事都不领；科目历史够得到就领；无登录记录的电脑过渡期领回读；`waiting(user=)` 回 `unreachable`、没有自己的电脑照旧 `offline`） |
| `python3 tests/test_cowork_express_account_sets.py` | 67 OK（目录刷新先到先读、租约归电脑；`pool.owned` 判据表） |
| `python3 tests/test_cowork_cc_desktop_devices.py` | 68 OK（登录集合累加、`signed_in_of`、`pool_peers.users`、`hello` 带旧 `always_on` 照收不存、字段表） |
| `test_cowork_folder_intake`（125）/ `folder_follow_year`（30）/ `readback`（44）/ `assistant_ui`（129）/ `companion_http_auth`（21） | 全 OK（定时任务冻结读者电脑、文件行优先于任务、旧任务回落负责人；跟随按读者电脑判够不够得到；回读按发起人问；「电脑」一行不再写常驻、前端源码里没有 `alwaysOn` / `always_on`） |
| `tests/test_cowork_folder_computers.py`（要 bench 的 frappe） | 改了断言（不再有 `always_on`），本机没有 frappe 没跑；候选检查（`ci-candidate-checks.sh`）里跑 |
| CC 桌面版 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1 / httpx / dbf / pytest 临时虚拟环境） | 891 passed, 41 skipped（基线 `254b2e28` 同一环境 892 passed, 43 skipped：删了 `test_always_on.py` 整个文件，其中「没人值守不弹框、先放租约」那条源码对照并入 `test_write_dispatch.py` 并改成只看 `attended`、断言不再防睡眠 / 锁屏；`test_desktop_settings` 两条常驻测试并成「旧配置里的 `always_on` 读时忽略、写回丢掉、开机自启仍默认」；`test_device_link` 三处不再带常驻、改测电脑改名时再认一次） |
| `check-architecture.py`、`i18n-build.py --check` | PASS / PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（含两个前端构建） | 91 步全过，退出码 0（含两个前端构建；提交前的工作树） |

### 给测试窗口的点验（测试机实时源码 + CC 桌面版 1.7.0；替换第 6 批清单里涉及常驻 / 代写的项）

1. 甲、乙各在自己的电脑登录 CC：甲在网页 / 手机 / LINE / CC 推一张 → 只有甲的电脑写（推送记录「由 甲的电脑 写入」）；乙的电脑开着也不接。
2. 甲的电脑关机或休眠后甲推一张：Desk、CC、LINE 写「电脑离线」、仍能选、确认排队；乙的电脑在线也不接；等两分钟以上仍在排队；甲开机后自动写进。
3. 同一份账套：甲的单排在前面、甲关机；乙推同一份账套 → 乙的电脑照样写乙的单，甲的那张继续等甲开机。
4. 账套只在甲电脑的 C 盘上：乙推这份账套 → 录入入口「没有能写的电脑」，确认回「没有能写这份账套的电脑。请在能打开这份账套的电脑上登录 CC 桌面版后重新推送」，
   不会由甲的电脑代写。乙在 CC 里回读这份账套 → 说明他自己的电脑够不到（`unreachable`），不建请求。
5. 新成员丙一台登录过 CC 的电脑都没有 → Express 写「CC 桌面版未连接」（同事有电脑也一样）。
6. 共用电脑：甲、乙先后在同一台电脑上打开 CC 对话页 → 两人的单那台都写；只认过电脑、没人打开过对话页的旧电脑（1.6 或补丁建的）照旧谁的单都写、不弹询问框。
7. 定时任务：乙的电脑读的文件夹出的票只由乙的电脑写（负责人是甲也一样）；乙的电脑关着 → 排队等它；乙的电脑够不到跟随到的账套 → 停在
   「这份账套没有电脑够得到」；定时任务的单碰到账套被占用不弹询问框。
8. CC 桌面版 1.7.0：设置「此电脑」里没有「常驻模式」开关、「电脑」列表没有「常驻模式」字样；「开机时自动启动 CC」仍在且默认开；关掉窗口进托盘照常写账；
   电脑睡眠后算离线，醒来自动接着写；旧配置里 `always_on` 打开过的升级后不再防睡眠、不再开机锁屏。
9. 科目建议历史、账套主档刷新：哪台够得到就由哪台先读（不必等发起人的电脑）。

### 第 7 批真机要验（加在第 6 批所列之上）

- 甲关机 → 开机后排队的单自动写完（真 Windows 睡眠 / 现代待机醒来后的心跳与领取恢复时延）；
- 1.6 升 1.7.0 首启：电脑行从「没有登录记录」变成只归登录过的人的那一刻，之前排着的别人的单是否按预期停下等各自的电脑（第 50 条过渡口径的边界）；
- 定时任务 `route_device` 在真 Site 上随文件行冻结、`device_hello` 改挂电脑后（`readers.moved`）已冻结的旧值不随之改——改挂前排着的定时任务单要看会不会停住；
- 真库上 `Q_HEADS` 加了 `_OWNED` 之后的执行计划与锁（压测时量）；
- 询问框：录入人本人的电脑、本人不在电脑前时碰到账套被占用，写入线程会一直等人点（现状，没加处理）。

### 没做 / 拿不准

- ~~办公室测试机以前靠常驻模式保持在线（记忆「10-12 起保持 CC 常驻」）。升级 1.7.0 后屏幕一关就进待机，远程测试会断——要用户在 Windows 电源设置里改「从不睡眠」，
  这是系统设置，要用户自己点。~~〔10-16 取代：防睡眠照 Claude 改回、默认开，见文末「CC 开着时保持电脑唤醒」，测试机不用改电源设置〕
- 共用电脑每个人都要在那台 CC 上用自己的账号登录一次，那台才写他的单；帮同事登录过一次，那台以后也写他的单，除了移除整台电脑没有入口去掉。
- 询问框仍可能卡住写入线程（第 7 批最后一条）。
- 改挂电脑（`readers.moved`、`credentials` 的旧槽位改挂）只转读者字段，不改已冻结在推送记录上的 `route_device`：改挂前排着的定时任务单冻结的是旧电脑编号，旧电脑退役后
  这些单不会被新编号的电脑领。真机遇到再定（可在 `readers.moved` 里顺带改排队中记录的 `route_device`），这次没做。
- 没挂电脑、按密钥编号算一台的旧槽位密钥当读者时，`route_device` 是密钥编号；它在推送连接上不是同一编号时那张单「访问不到」。补丁之后应已没有这种读者，没另做。
- `tests/test_cowork_folder_computers.py` 本机没跑（要 frappe）；真实 Site、HTTP 链、测试机、真 Windows 都没跑。

### 「谁的活谁的电脑做」对抗审查与修复（2026-10-16，本地提交，未推送）

审查在 `ecbb67a7` 上读代码（没在真 Site、真 Windows 上复现），没有阻塞项：9 条（4 中、5 低）逐条核实**全部成立**，其中第 1、6 条是同一处
（改挂 / 移除时冻结的 `route_device` 没人管）。6 处改代码、1 处只改注释（第 9 条）、1 处只改记录（第 7 条）。修复为本节之前的 `fix:` 提交，记录与 ADR 为随后的 `docs:` 提交；ADR-021 第 57 条，
第 30、51 条与「兼容」一节对应补注。没有新补丁、没有新 DocType 字段、没有新界面词条；新加一个 hooks 键 `cowork_device_retiring`。

| # | 级别 | 问题 | 核实 | 处理 |
|---|---|---|---|---|
| 1 / 6 | 中 | 定时任务单冻结的 `route_device` 在电脑改挂（`_move`）或移除（`retire`）时不跟着处理，已排队的单永远没人写、不报原因 | 成立：`queue_heads._OWNED` 只认 `route_device` 等于来领的那台；`readers.moved` 只转任务、文件、运行、公司文件夹；`remove_device` / `retire_orphan` 不碰推送记录；1.6 升 1.7.0 的补丁旧电脑就会走到 `_move` | 已修：推送域加 `push.native.service.reroute(connection, old, new)`，挂在同一个钩子 `cowork_reader_device_moved` 上（companion 仍不直接写推送表），把这条连接上冻结给旧记录、状态为已准备 / 排队 / 正在写的记录改由新记录写（`documents.reroute` 是唯一能改 `route_device` 的入口，带专用标记，其余身份字段照旧不可改；只换这一列、升一次版本）。新钩子 `cowork_device_retiring` → `release_route(device)`：电脑退役前（`credentials.remove_device` 吊销密钥后、锁电脑行前；`devices.retire_orphan` 对没人认领的旧电脑在锁电脑行前）把冻结给它、还在排队的单转人工，原因现有 `push.no_writer`，结果不明的不碰。两处都先不加锁挑（`route_device` 没有索引，加锁读会锁住扫过的每一行），再按主键逐张锁住复核。测试：账本 `reroute` 只改冻结给旧记录、未写完的两张，`release_route` 只转排队、不碰结果不明，挑选查询不加锁；身份字段守卫只放行 `reroute` 改 `route_device`；电脑移除时钩子在吊销密钥之后、锁电脑行之前；旧电脑退役前在改挂之后叫钩子；hooks 登记 |
| 2 | 中 | 读文件夹的电脑够不到账套（永久）被当成「CC 桌面版未连接」，每十分钟重试、约 6 小时才失败 | 成立：`queueable` 只回真假，`_push_to` 与 T1 的 `UNREACHABLE` 都按「没连上」重试 | 已修：`destinations.queueable` 换成三态 `writer_state`：`READY`（能写，关着也算）/ `UNREACHABLE`（给了读者电脑而它够不到能写的账套，或旧任务的负责人有电脑却都够不到）/ `MISSING`（负责人一台电脑都没有）。只有 `MISSING` 才回 `retry`；`UNREACHABLE` 照常准备、确认，由确认按现有 `push.no_writer` 第一次就记下（撤回那次准备、原因记在票上），跟随公司时第一次就停在 `cowork.folder.account_set_unreachable`。测试：读者电脑够不到时第一次推就记 `push.no_writer`、不重试、撤回准备；T1 跟随同样第一次就停下；负责人一台电脑都没有仍重试 |
| 3 | 中 | 待处理页当场补选重推定时任务的票时没带 `route_device`，改归点按钮的人自己的电脑写 | 成立：`pending_api.answer` / `choose_account_set` → `tools._push_one` → `application.submit` 不传 `route_device` | 已修（口径见下）：`folder_intake.push.reader_of(history, connection)` 按原件找回出这张票的文件行与任务（与 `source_rules` 同一条路），回读它所在文件夹的电脑（文件行优先，没有按任务上的；不是定时任务的票、任务删了、推往别的连接回空）；`pending_api` 两处把它传进 `_push_one(route_device=)`，Express 时交给 `submit`。CC 对话里主动推送仍按发起人。测试：`reader_of` 文件行优先 / 回落任务 / 别的连接与非定时任务为空；两处补选都带上读者电脑 |
| 4 | 低 | T1 跟随判够不够得到用 `rule.reader_device`，推送冻结的是文件行优先的 `_reader(entry, rule)` | 成立 | 已修：`follow.choose(rule, history, device=)`，`push_one` 先算 `_reader(entry, rule)` 再同一个值传给 `choose` 与推送。测试：任务改由新电脑读、在途文件行仍记旧电脑时，判据与冻结都是旧电脑 |
| 5 | 低 | CC 工具把「有电脑但都够不到能写的账套」也报成 `cc_desktop_offline` | 成立：`_target_state` 只看 `writers_known` | 已修：`destinations.intake_state`（与 LINE `flow._state` 同一口径）；`cowork_intake_options` 的 Express 状态分 `no_writer` 与 `cc_desktop_not_connected`，各带一句给模型的英文说明（`TARGET_NOTES`，不是界面文案），`cc_desktop_offline` 不再出现。账套清单按本人电脑筛**没做**（审查也说可留到第 7 批）：乙选了只有甲的电脑够得到的账套，确认时回 `push.no_writer`。测试：两种状态与说明、不列账套 |
| 7 | 低 | 网页去掉常驻开关后，1.6 用户在 1.7.0 推到之前关不掉本机常驻 | 成立（影响只在服务端部署到 1.7.0 推到之间） | 按审查建议第一条只改记录：ADR「兼容」与第 57 条写明，发布时 CC 桌面版紧跟服务端部署；不加只关不开的临时开关（用户要求界面不出现常驻） |
| 8 | 低 | 回读：发起人一台自己的电脑都没有时回 `offline`，CC 会叫他去把电脑开机 | 成立 | 已修：`request_service.waiting(…, user=)` 不定向、他一台登录过 CC 的电脑都没有时回新原因 `missing`（给不给账套都一样）；`READBACK_WAITING["missing"]` 一句给模型的英文说明。测试：`missing` 两种、定向读者电脑不按人判；CC 工具带上这句说明 |
| 9 | 低 | 两处注释仍按档位 / 常驻写 | 成立 | 已改：`queue_contracts` 的 `attended` 注释改为「不是定时任务、录入人在这台电脑上登录过 CC（第 55 条）」；桌面端 `device_key_backup` 去掉「常驻电脑更是没人去点」 |

第 3 条的口径：审查建议先请用户定「补选后算定时任务的活还是点按钮的人的活」。本窗口没法问用户，按 ADR 第 51 条「定时任务由读那个文件夹的电脑做」与
「T1 跟随不变」取**仍算定时任务**（审查推荐的一种，F18 / T1 的待处理闭环不受影响）；要改成按点按钮的人，只需 `pending_api` 不传 `route_device`。

文案：没有新增、没有改动界面词条。给模型看的英文说明加三句（`TARGET_NOTES` 两句、`READBACK_WAITING["missing"]` 一句），不进术语表。
不再使用：CC 工具状态码 `cc_desktop_offline`（给模型的，不是界面文案）。

检查（提交前的工作树）：

| 命令 | 结果 |
|---|---|
| `python3 tests/test_cowork_express_dispatch.py` | 63 OK（新：账本 `reroute` / `release_route`；身份字段守卫） |
| `python3 tests/test_cowork_cc_desktop_devices.py` | 68 OK（移除电脑时推送钩子的锁顺序；旧电脑退役前钩子在改挂之后） |
| `python3 tests/test_cowork_folder_intake.py` | 126 OK（新：读者电脑够不到第一次就记 `push.no_writer`；hooks 登记两处） |
| `python3 tests/test_cowork_folder_follow_year.py` | 30 OK（够不到第一次就停；文件行电脑优先、判据与冻结同一台） |
| `python3 tests/test_cowork_companies.py` | 117 OK（新：待处理补选带读者电脑） |
| `python3 tests/test_cowork_push_fixed.py` | 19 OK（新：`reader_of`） |
| `python3 tests/test_cowork_companion_requests.py` | 38 OK（`missing`） |
| `python3 tests/test_cowork_assistant_tools.py` | 187 OK（`no_writer` / `cc_desktop_not_connected` 与说明；回读 `missing` 说明） |
| `check-architecture.py` | PASS |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（含两个前端构建） | 91 步全过，退出码 0（含 `frontend/assistant` 构建）；另跑 `npm --prefix frontend run build` 通过（产物按 `.gitignore` 不入库） |

CC 桌面版只改了一行注释，没重跑它的 pytest；`tests/test_cowork_folder_computers.py`（要 frappe）本机仍没跑。

给测试窗口的点验（在上一节 1–9 之上加）：

10. 读文件夹的电脑在、却够不到这份账套（例如只装了 CC、没装 Express 的电脑读文件夹）：票第一次推就失败，原因「没有能写这份账套的电脑…」，不再显示
    「推送时 CC 桌面版未连接，将于十分钟后重新推送」；跟随公司当年账套的任务第一次就停在「这份账套没有电脑够得到」。
11. 定时任务的票停在「要人选」或「待选账套」：只用网页 / 手机、没有自己电脑的会计员在待处理页补选 → 能确认，由读文件夹的那台电脑写（推送记录「由 某电脑 写入」）。
12. 在「电脑」列表移除读文件夹的那台电脑：它名下还在排队的定时任务单转到待处理，原因「没有能写这份账套的电脑…」，能重推；不再永远停在「推送中」。
13. CC 里问「录到 Express」：自己有电脑但账套都在同事电脑上 → CC 说没有能写的电脑；一台电脑都没有 → CC 说还没连 CC 桌面版；回读时一台电脑都没有同样说没连。

第 7 批真机要验（加在上一节之上）：

- 1.6 升 1.7.0：补丁旧电脑改挂到新记录那一刻，排着的定时任务单是否随之改挂、由新记录领走（`reroute`，取代上一节「改挂前排着的单会不会停住」那条）；
- 真库上 `route_device` 无索引的不加锁挑选在推送记录多时的耗时（只在改挂、退役时跑一次）；
- 1.6 本机常驻在服务端部署后、1.7.0 推到前关不掉，发布时 CC 桌面版紧跟服务端。

没做 / 拿不准（替换上一节「改挂电脑只转读者字段」那条，其余照旧）：

- CC / LINE 识别时的账套清单仍是全池并集，不按本人电脑筛：选了只有同事电脑够得到的账套，先识别，确认时才回 `push.no_writer`。留到第 7 批。
- 电脑退役只转「排队」的单；「已准备」的确认时自会按 `push.no_writer` 被拒，「正在写」的照旧等回执或租约过期转「结果不明」。
- `retire_orphan` 判「没人认领」用的是锁电脑行之前的不加锁预看：极少数情况下那一刻电脑刚被认领，排着的单会被转人工（能重推，不丢）。
- 第 3 条口径按「仍算定时任务」施工，没问用户（见上）。

## CC 开着时保持电脑唤醒（用户 10-16，2026-10-16，本地提交，未推送）

来源（用户 10-16 原话，在上面删掉常驻之后）：「卧槽，这个模式需要啊，我之前以为是电脑常驻的意思，之前是我理解错了呀，改成和 claude 一样的功能」；
两个方案里选了「CC 开着就不睡」。原来的「常驻」拆成两件事：**替别人接活**（常驻电脑档位、开机锁屏、不弹询问框）按「谁的活谁的电脑做」删掉、不回来；
**防睡眠**照 Claude 桌面版（「Keep computer awake while Claude works」「Keep awake on battery power」）改成两个开关，默认都开。ADR-021 第 58 条，
第 56 条与「有意变化」「兼容」对应补注。

### 改了什么

- 新 `companion/src/companion/keep_awake.py`：`KeepAwake.apply(enabled, on_battery, members)` 两样都满足就起一条专用守护线程（`keep-awake`）拿住，
  否则马上放开；线程里每 15 秒查一次电源（开关、成员一变马上重算），用电池且「用电池时也保持」关着就放开、插上电再拿起；`stop()` 退出时放开。
  系统调用在 `WindowsPower`：`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED [| ES_DISPLAY_REQUIRED])`、`GetSystemPowerStatus`
  （`ACLineStatus == 0` 才算用电池）、`powrprof.GetPwrCapabilities`（`AoAc`，只查一次，查不出按现代待机）。非 Windows 什么都不做。
- 用的调用与依据：找回 `ecabec82` 之前 `always_on.py` 的做法——同样的 `SetThreadExecutionState`、同样挂在专用线程上、放开时同一条线程只留
  `ES_CONTINUOUS`。那套 2026-10-12 在办公室测试机（Windows、现代待机）实测：只保持系统 → 关屏立即待机；保持「系统 + 屏幕」再锁屏 → 4 分钟以上一直
  在线、系统日志无待机记录。所以现代待机的电脑仍用「系统 + 屏幕」（屏幕不会自动关）；一般电脑（S3）只用「系统」，屏幕照常关。开机锁屏
  （`LockWorkStation`）不带回来。没用 `PowerCreateRequest` / `PowerSetRequest`（没验证过）。
- `config.py` / `profile_store.py`：`keep_awake`、`keep_awake_on_battery`（默认 True）进 `CompanionConfig` 与 `GlobalSettings`；旧 `always_on=true`
  且没有 `keep_awake` 时迁成 `keep_awake=true`，旧键不在白名单里、下次写回时丢掉。
- `desktop_settings.py`：快照多 `keep_awake`、`keep_awake_on_battery`；`set_keep_awake(enabled=, on_battery=)` 存全局设置后回调托盘。
  `tomo_bridge.py`：桥方法 `keep_awake`、`keep_awake_on_battery`。
- `gui_tray.py`：启动时建 `KeepAwake` 并 `_apply_keep_awake()`（开关取自配置，成员 = `any(member_active(cfg))`）；`_reload_profiles`（配对、断开、
  暂停、移除、被顶掉都走这里）后重算；设置里改开关 → `_set_keep_awake` 先把各线程手上的配置一起改（它们写回配置时不把旧值盖回去）再重算；
  `_quit` 先放开。〔审查改：只改托盘的配置并没改到取任务线程的副本，见文末「审查（10-16）」第 2 条〕
- 前端（`frontend/assistant/src`）：`settings.tsx` 新 `KeepAwakeRows`，放在「此电脑 → 系统」的「开机时自动启动 CC」下面（有桥才有这一栏；
  状态里没有 `keep_awake` 的旧版桌面端不显示）；`lib/desktop.ts` 的 `DesktopState` 多两项可选键；`i18n.ts` 三种语言各两条。
- `companion/README.md` 改写 1.7.0 那一条。服务端没改（`hello` 不报、分派与询问框不看它）。

### 文案（只放开关和标签，不加说明文字）

| 键 | 中文 | English | ไทย |
|---|---|---|---|
| `keepAwake` | CC 开着时保持电脑唤醒 | Keep computer awake while CC is open | ไม่ให้คอมพิวเตอร์สลีปขณะเปิด CC |
| `keepAwakeOnBattery` | 用电池时也保持 | Keep awake on battery power | รวมถึงขณะใช้แบตเตอรี่ |

CC 页面只有三种语言，服务端 `messages.tsv` 不用加（没有新的服务端词条）。界面里不再出现「常驻」（测试守着 `i18n.ts`）。

### 检查

| 命令 | 结果 |
|---|---|
| CC 桌面版 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1 / httpx / dbf / pytest 临时虚拟环境） | 900 passed, 41 skipped（新 `test_keep_awake.py` 7 条：有成员才拿、成员都断开放开、关开关与退出马上放开、用电池按开关放开 / 插电再拿、现代待机加屏幕、非 Windows 不做事、`WindowsPower` 调的接口与查不出按现代待机、源码里没有锁屏调用与托盘接线；`test_desktop_settings` 3 条：旧 `always_on` 迁移与写回丢键、默认开与明写的关不被覆盖、两个开关保存并回调托盘） |
| `python3 -m unittest tests.test_assistant_ui.KeepAwakeTests tests.test_assistant_ui.NoAlwaysOnTests` | OK（真跑 `KeepAwakeRows`：中英两种语言的两行标签、开关状态、点了调 `keep_awake` / `keep_awake_on_battery`、旧版桌面端不显示；只在「此电脑 → 系统」、有桥才渲染；界面不再有「常驻」） |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 91 步全过，退出码 0（含 `frontend/assistant` 构建、`check-architecture.py` PASS、`check-i18n.py --strict`）；另跑 `npm --prefix frontend run build` 通过（产物按 `.gitignore` 不入库）；记录与 ADR 改完后另跑 `check-architecture.py` 与 `git diff --check` 通过 |

### 给测试窗口的点验（替换上面第 8 条里「不再防睡眠」那半句）

8'. CC 桌面版 1.7.0 设置「此电脑 → 系统」：「开机时自动启动 CC」下面有「CC 开着时保持电脑唤醒」「用电池时也保持」两个开关，默认都开；网页（浏览器里）
    设置没有这两行；任何地方没有「常驻」二字；开机后不锁屏。旧配置里打开过常驻的，升级后两个开关都是开。

### 第 7 批真机要验（办公室测试机，Windows 现代待机）

- 关屏后不睡：CC 连着、开关开着，等过电源设置的关屏 / 睡眠时间（或 `powercfg /requests` 看到 `SYSTEM` 与 `DISPLAY` 两项挂着 CC 的进程），
  远程一直连得上、`powercfg /sleepstudy` / 系统日志没有进待机；现代待机的电脑屏幕不会自动关（预期）。
- 拔电时按开关：「用电池时也保持」开着拔电 → 仍拿住；关掉后拔电 → 约 15 秒内放开（`powercfg /requests` 里 CC 那两项消失），插上电约 15 秒内又出现。
- 退出 CC（托盘「退出」）后能睡：`powercfg /requests` 里没有 CC，按电源设置正常关屏、待机；关掉「CC 开着时保持电脑唤醒」、断开全部成员同样马上放开。
- 合上笔记本盖子照样睡（录制笔记本上验）。

### 没做 / 拿不准

- 任务书说「屏幕照常可以关」，又说现代待机「以原来验证过的方式为准」：现代待机上两者冲突，取验证过的「系统 + 屏幕」，这种电脑屏幕不会自动关、
  也就不会因关屏而自动锁屏；以前常驻靠开机锁屏挡住，现在不锁屏（Claude 没有），桌面会一直亮着、不上锁（屏幕保护程序的「恢复时显示登录屏幕」照样起作用）。
  要不要在现代待机上另找只防睡眠、允许关屏的办法（例如 `PowerSetRequest` 的 `PowerRequestExecutionRequired`）没验证过，没做。
- 「至少一个成员连着」按配对有效算（没暂停、密钥没被说不能再用），不看此刻网络通不通：断网时也拿住，网络回来照常接活。
- ~~电源变化靠每 15 秒查一次，不是系统广播（`WM_POWERBROADCAST`）；拔插电后最多约 15 秒才跟上。~~〔10-18 D8 取代：订阅插电 / 电池切换通知，见下面「D8」〕
- `AoAc` 按 `winnt.h` 的 `SYSTEM_POWER_CAPABILITIES` 第 20 字节读，开发机上只用假的验证，真机要看日志「这台电脑是 / 不是现代待机」核一次。
- 托盘菜单里没加这两个开关（托盘菜单本来就没有设置项，设置都在对话页「此电脑」里）。

### 审查（10-16）

5 条发现（其中第 2、4 条是同一件事），逐条核实：

| # | 发现 | 核实 | 处理 |
|---|---|---|---|
| 1 | 现代待机的电脑默认拿着 `ES_DISPLAY_REQUIRED`：屏幕永远不自动关、不会因关屏自动上锁，用电池时同样（高） | 成立（行为本来就这样，上面「拿不准」写了屏幕不关，但没写「不自动上锁」和用电池耗电的后果） | 不改代码：只防睡眠、允许关屏的调用在现代待机上没验证过，任务书要求按验证过的做；要不要在现代待机上让「用电池时也保持」默认关、或用电池时只保持系统，是产品取舍，等用户拍板。后果写进 ADR-021 第 58 条，列为待用户确认 |
| 2 / 4 | `_set_keep_awake` 只改托盘的配置，取任务线程启动时 `copy()` 的副本还是旧值；心跳存盘（`_sync_connection_identity` / `_sync_account_set` / `_remember_roots`）经 `GlobalSettings.from_config(副本)` 把开关写回成开（中） | 成立：先在旧代码上复现（关开关后副本 `save()`，磁盘又成 `keep_awake=true`），新测试在旧 `profile_store.py` 上失败 | 按类修：`save_profile` 只写这份配置自己改过的全局项（读出时记 `_globals_seen`，与它相同的项以文件里最新的为准，读文件与写回在同一把锁里，存完副本跟上文件里的值）；界面语言、开机自启同样不再被旧副本盖回。直接构造、不是从文件读出的配置照旧整份写。托盘不再改各成员配置，开关单独记在 `_awake_settings`（启动 / `_reload_profiles` 取文件里的值，设置回调更新） |
| 3 | `GetPwrCapabilities` 返回 `BOOLEAN` 却没设 `restype`，失败时 EAX 高位残值会被当成功、读到全零、现代待机被当 S3（低） | 成立（概率低，但后果正是要防的关屏即待机） | 设 `restype = c_ubyte`；`AoAc` 偏移 20 按 `winnt.h` 再核一遍（15 个 BOOLEAN + 两个 BYTE + `FastSystemS4`、`Hiberboot`、`WakeAlarmPresent`）；第 7 批真机加 `powercfg /a` 对照 |
| 5 | 页面用变量 `run(method, method, …)` 调桥，契约检查的正则抓不到 `keep_awake` / `keep_awake_on_battery`，桥的分派也没测试（低） | 成立 | 页面改成字面量 `run("keep_awake", "keep_awake", …)` / `run("keep_awake_on_battery", …)`；`tests/test_companion_contract.py` 断言两个方法在页面调用里、桥分别调 `set_keep_awake(enabled=…)` 与 `set_keep_awake(on_battery=…)` |

另见（没改，不在这次范围）：取任务线程那份配置所在的 `ProfileStore` 手里的成员列表也是启动时读的，副本存盘时整份写回成员列表；成员增删、暂停都经
`_reload_profiles` 先停旧线程再起新线程，所以平常碰不到，只在停线程的那一小段里理论上可能盖掉别处刚改的成员。没有实际现象，按「只修实际问题」不动。

检查：

| 命令 | 结果 |
|---|---|
| CC 桌面版完整 pytest（companion/，Python 3.11 + PySide6 6.12.0 / httpx / dbf / pytest 临时虚拟环境，跑完删） | 901 passed, 41 skipped（新 `test_an_old_worker_copy_saving_on_a_heartbeat_does_not_put_the_switches_back`：关开关、关自启、换语言后，旧副本心跳存盘与托盘那份存盘都不盖回，副本自己改的账套名照样存下、持有者自己改的语言照样写；在旧 `profile_store.py` 上失败。`test_keep_awake` 补 `restype` 与偏移、托盘不改成员配置） |
| `tests/test_companion_contract.py` | 69 passed |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` | 91 步全过，退出码 0（含 `frontend/assistant` 构建、`tests.test_assistant_ui` 的 `KeepAwakeTests` 改成核对多行写法的 `<KeepAwakeRows`）；另跑 `npm --prefix frontend run build` 通过 |

第 7 批真机补一条：`powercfg /a` 列出「Standby (S0 Low Power Idle)」即现代待机，与日志「这台电脑是 / 不是现代待机」对照；现代待机上开着开关时
屏幕不自动关、不自动上锁（预期，待用户确认要不要改）。

### D8：用电池时默认不保持（用户 10-18 定，本分支提交）

**用户 10-18 定：「用电池时也保持」默认关**（原话「要我定的都按你建议」，建议即此项）。依据：办公室测试机 10-18 12:26 起用电池，约 3 小时电量
68%→21%，期间 `powercfg /requests` 里 CC 一直拿着 DISPLAY + SYSTEM，电脑不睡、屏幕不关（见上一节「1.7.0 空闲 CPU」与 test4 核查第 3 条）。
「CC 开着时保持电脑唤醒」（插电）默认仍开。界面文案、布局没动，没加说明。

- 默认值：`CompanionConfig` / `GlobalSettings` 的 `keep_awake_on_battery` 默认 False；`KeepAwake` 自己的初值、托盘取不到配置时的兜底、对话页
  `KeepAwakeRows` 状态缺这一项时的兜底一并改成关（后两处正常走不到，只为一致）。
- 「从没设过」与「用户设过」：新字段 `keep_awake_on_battery_set`，只有设置里点了这个开关（`DesktopSettings.set_keep_awake(on_battery=…)`）才写 True；
  读配置时没有这个标记就不看文件里的值、取默认（关）。**老配置按「没设过」处理的理由**：之前每次存盘都把整份全局设置写进 `companion.json`，
  文件里的 `keep_awake_on_battery: true` 分不出是用户亲手开的还是旧默认写进去的；这个开关只在没发布的 1.7.0 测试包里有过（测试机、录制机），
  旧默认是开，所以写着 False 的必是用户关的、本来就等于新默认，写着 True 的多半是默认，最多丢掉测试机上一次手动的「开」。存盘时副本只写自己改过的全局项
  （10-16 审查的 `_globals_seen`），用户打开后旧副本存盘不会把它盖回关（测试覆盖）。
- 拔电立即放开：守护线程起来时订阅 `powrprof.PowerSettingRegisterNotification(GUID_ACDC_POWER_SOURCE, DEVICE_NOTIFY_CALLBACK)`（回调形式，
  不要窗口；Windows 8 起），回调只叫醒守护线程，由它重新查 `GetSystemPowerStatus`：用电池且开关关着 → 同一条线程上只留 `ES_CONTINUOUS`（DISPLAY、
  SYSTEM 都放开）；插上电 → 按插电的设置拿回（现代待机「系统 + 屏幕」，一般电脑「系统」）。线程退出时取消订阅。15 秒轮询保留作兜底，订阅失败
  （返回错误码或抛异常）只记一条警告、照样靠轮询。

检查（Mac 上替身）：

| 命令 | 结果 |
|---|---|
| `test_keep_awake.py` 新 4 条 | 切换通知叫醒（轮询设 60 秒）：插电拿「系统 + 屏幕」→ 拔电马上全放开 → 插电拿回 → 打开开关用电池也拿；拿 / 放都在 `keep-awake` 线程、放开时取消订阅；一开始就在用电池什么都不拿；订阅抛错照样靠轮询放开；`WindowsPower.watch` 用的 GUID、`DEVICE_NOTIFY_CALLBACK`、回调叫醒、取消订阅、注册返回错误码回 None；真 Windows 上真注册 / 取消（Mac 跳过，windows-latest 跑） |
| `test_desktop_settings.py` | 新配置默认（开, 关）且文件里标记为 False；无标记的旧配置 True → 关、False → 关、插电开关照旧；有标记照用户的值；旧配置存盘后文件里也是新默认；设置里打开后重读仍开、标记 True；旧副本存盘不把打开盖回关 |
| 改代码前跑同样的测试 | 8 条失败（新默认、迁移、通知），改后全过 |

真机（第 7 批，测试机插电后）要验：默认装好后用电池 `powercfg /requests` 里没有 CC；插上电几秒内出现 DISPLAY + SYSTEM，再拔掉几秒内消失；
日志没有「订阅插电 / 电池切换失败」；设置里打开「用电池时也保持」后拔电仍拿住、重启 CC 后开关仍是开。

## 并入外壳（10-17，本地提交，未推送）

`git merge --no-ff pearnly/cc-shell-rebuild`（对方 `630ca3aa`，自上次合并 `f5c04929` 起 34 个提交，含 4 个子分支合并：侧栏对话只在本组排序、
删「移到」、服务端 `sessions.move` 拒换公司；审批卡片照 Claude；记住的选择一行一条；手机端照 Claude；第 10 轮文案；个人排列跨设备同步；
发送 / 停止失败说原因；公司规则按哪一条入账 `written.company_rules` 等）进 `pearnly/multi-desktop`（`b9dedf40`，第 6 批界面、谁的活谁的电脑做、
保持唤醒）。不变基、不改写历史。外壳分支之后的提交由开发窗口再合一次增量。

### 冲突怎么解

| 文件 | 解法 |
|---|---|
| `push/application/service.py` `prepare` | 同上次：冻结目标只建一次。外壳分支在载荷之前那份 `frozen` 又多了 `company_rules`（这张单用上的是哪几条公司规则）；整段不要，`company_rules` 加到本分支那一份上（证据 v2 的 `basis_hash`、`ledger.writer_scope` 写者锁 v2、`decided_by` 之后）。不进载荷，不改 `basis_hash` |
| `push/application/service.py` `_decisions` 文档串 | 两句都留：科目表没读到先没用的公司规则不拿 Express 历史顶替（本分支合并审查第 2 条）+ 结果带用上的公司规则 `company_rules`（外壳分支） |
| `push/application/service.py` `outcomes` / `written` | 两边都要：查询带 `written_by_device`、结果经 `_with_writers` 加「由哪台电脑写入」`writer`（本分支第 6 批），`_entered(row, now)` 带 `_rules_now` 核的公司规则现在还在不在（外壳分支）。`outcomes` 只拿入账成功的行查规则现状，同外壳分支 |
| `push/express/company_choices.py` `__all__` | 取并集：本分支的 `WAIT_KINDS`、`held_back`，外壳分支的 `USED_MAX`、`used` |
| `translations/zh.csv` | 同上次：两边取并集（本分支 T1 的「这家公司没有覆盖单据日期的账套。」+ 外壳分支第 10 轮改的「该公司或分店没有覆盖本月的账套」），再 `python3 scripts/i18n-build.py` 按自动合并好的 `messages.tsv` 重新生成；`--check` PASS；逐个核过改动的翻译包，相对两边都没丢 msgid |
| `frontend/assistant/src/components/settings.tsx` import | 并集：`postMethod` / `shownError`（本分支「电脑」列表移除、全所可用）+ `giveFocusBack` / `opener`（外壳分支设置关掉还焦点） |
| `tests/test_assistant_ui.py` 文件末尾 | 两边各在同一处加的测试类都留：本分支 `NoAlwaysOnTests` / `KeepAwakeTests`，外壳分支 `UserAsks20261016Tests` 起到第 8 轮那几类；外壳分支放在 `__main__` 之后的 `Round10DragTests` 合并时原样（脚本方式运行时 `unittest.main()` 先退出，这一类 2 项从来不跑；合并审查后已挪到 `__main__` 之前，见下文「合并审查」第 2 条）。核过各测试文件没有重名类 |

自动合并的 `hooks.py`（外壳分支的 `cowork_push_company_choices_saved`，本分支的 `cowork_device_labels` 等都在）、`assistant/tools.py`
（外壳分支的已入账单不叫人去 Express 改、`written.company_rules` 说明；本分支 `intake_options` 的 `cc_desktop_online`。`written.writer` 合并时
工具说明里其实没有，只在 `service.py` 的 `_with_writers` 里加进 `written`，合并审查后补上，见下文第 3 条）、
`companies/service.py`（外壳分支 `with_tax_id`、公司说明带主档编号；本分支 T1 的 `tasks_of` / `_rule_company`）、`company-cards.tsx`、
`month-ledger.tsx`、`pending.tsx`、`scheduled-tasks.tsx`、`i18n.ts`、`messages.tsv` 逐个看过，没有重复定义或互相覆盖。依赖没变（两边
`package*.json` 自上次合并都没改），两个工作树的 `node_modules` 都已在，没建符号链接、没装。

### 语义核对

1. **冻结目标四件同时成立**：`prepare` 的冻结目标里证据 v2（`basis_hash`）、写者锁 v2（`lock_scheme` / `company_key`）、`decided_by`、
   `company_rules` 都在同一份里；租约 v2 领取只核 route 与 `basis_hash`，多一项不影响。`tests/test_cowork_push_direction.py` `FrozenTarget`
   加一项：带 `company_rules` 时它与证据 v2、写者锁 v2、`decided_by` 同在，不进载荷、不改 `basis_hash`；没有时冻结目标不多这一项。去掉
   `company_rules` 那三行这项失败。
2. **先没用的公司规则不算「用上的」**：`company_choices.used` 只认来源记成 `company_rule` 的归类键；本分支 `held_back`（科目表没读到先不用）
   的那项来源不是 `company_rule`，不会冻结进 `company_rules`，CC 也不会说「按这条公司规则」。T1 跟到新年度账套、科目表读到后按规则推时照常记上。
3. **回读同时说电脑和规则**：`written` / `outcomes` 一条成功记录同时有 `writer`（电脑名）与 `company_rules`（带 `still_saved` / `now_value`），
   两个钩子各查一次，互不依赖。合并时只是读代码得出的，两边测试各断言一样；合并审查后加了同一条记录同时断言两样的测试（见下文第 6 条）。
4. **界面没有用外壳删掉的东西**：本分支第 6 批及之后的界面（「电脑」列表、全所可用、读者编号、`AccountSetRow`、跟随公司当年账套、保持唤醒）
   没用 `co.moveTo` / `sb.moveToN` / `MoveToMenu` / 跨组拖动 / 旧卡片的 `allowed` / `denied`（`grep` 与 `tsc --noEmit` 都核过）。「电脑」那一行的
   「···」→「移除」是移除电脑，不是移动对话；10-17 并入外壳第 11 轮后菜单改成同其他菜单的 `cc-menu` / `cc-menu-item`（第 11 轮删了旧的
   `menuItem`），触发钮加 `cc-tap`（电脑 28，手机点按区 44）。`AccountSetRow` 是待处理页 / 月结台账的一行（同当场选 `ChooseRow` 那一套），
   不是对话里的审批卡片；第 11 轮把 `ChooseRow` 的「确认 / 立即推送 / 交给 CC」改成卡片按钮（`cc-card-btn`，电脑 24、手机 44 胶囊）后，
   `AccountSetRow` 的「确认」「交给 CC」随之改用 `cc-card-btn`（10-17 合并审查）。没有用到外壳第 10 轮改掉的旧词（「等你处理」「还没推送」「要人选」等）。
5. **服务端 `sessions.move` 拒换公司**：本分支没有调用 `move` 的地方（删公司走 `projects.release`），不受影响。

### 闸门

完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>` 以第二遍为准：加 `FrozenTarget` 一项后，在最终代码树（与合并提交相同，
之后只加了本节文档）上整套跑，91 步全过、退出码 0；所有代码文件的修改都早于这一遍开跑。第一遍跑的时候 `service.py` 和测试文件还在改
（加测试、去掉三行再还原的变异实验），不算任何一棵固定代码树的证据。`cd frontend/assistant && npx tsc --noEmit -p .`
无输出通过；`check-architecture.py` 0 违规；`i18n-build.py --check` PASS；`test_cowork_push_direction.py` 17 OK。

没做 / 没验：真实 Site 迁移、测试机与真 Express；外壳分支新界面（侧栏本组排序、审批卡片、手机端）与多电脑界面同屏的点验留给测试机一轮。
本分支 T1 / 第 3 批新加的几条服务端中文带指示词，合并时没改；合并审查后已照第 10 轮口径改掉，连同 3 个 DocType 字段标签，见下文第 1 条。

### 合并审查（10-17，追加提交，不改合并提交 `b3aa1df4`）

1. **多电脑新文案按第 10 轮口径改（成立，已改）**。开发窗口的文案闸门（`pearnly/copy-gate` 的 `scripts/check-copy.py`，未进 main）只读跑
   `--base 630ca3aa` 拦下 9 处，全来自本分支；改后同一命令拦截 0；`--base 7c9bb445`（main 合并基线）只剩外壳分支待用户确认的 2 处 R-CHG
   （`MESSAGES.needsInput` / `MESSAGES.question`），本分支 0 处。这几条都不在 main 上，闸门判新增，不需要登记用户同意。

   | 词条 | 原中文 | 改为 |
   |---|---|---|
   | `cowork.folder.account_set_none` | 这家公司没有覆盖单据日期的账套。 | 该公司没有覆盖单据日期的账套 |
   | `cowork.folder.account_set_ambiguous` | 这家公司有多份账套覆盖单据日期。 | 该公司有多份账套覆盖单据日期 |
   | `intake.error.ERR_ACCOUNT_UID_MISMATCH` | 这台电脑上的账套不是登记的那一份，等待其他电脑 | 此电脑上的账套与登记的不一致，等待其他电脑 |
   | `push.no_writer` | 没有能写这份账套的电脑。请在能打开这份账套的电脑上登录 CC 桌面版后重新推送 | 没有可写入该账套的电脑（英 / 泰 / 日同去掉后半句操作提示） |
   | 标签 `Max Payload Version`（CC 桌面版电脑 `max_payload_version`） | 最高载荷版本 | `Max Push Data Version` 最高推送数据版本 |
   | 标签 `Payload Version`（推送记录 `payload_version`） | 载荷版本 | `Push Data Version` 推送数据版本 |
   | 标签 `Refresh Lease Computer`（ERP 刷新 `lease_device`） | 刷新租约电脑 | 领取刷新的电脑（照已有的 `Lease Computer` 领取的电脑；英文不在锁语境，闸门不拦，msgid 不变） |

   3 个 DocType 权限是 All / Guest 可读，算产品 Desk（强术语拦），所以没走豁免、换成业务说法；两张改了标签的 DocType `modified` 改到
   `2026-10-17 12:00`，迁移时标签会更新。翻译包按术语表重新生成，同时删掉本分支自己留下、术语表已没有的 4 条旧 msgid 行（两个旧标签、
   `push.no_writer` 的旧英文、第 5 批改掉的「将由其他电脑自动推送」那条），这几条 main 上都没有。仍标黄、不拦的：`ERR_ACCOUNT_UID_MISMATCH`
   英 / 泰 / 日是两句（R-SENT）、`push.no_writer` 中英长度差（R-ALIGN）等 9 处。
2. **`Round10DragTests` 在 `__main__` 之后（成立，已改）**：挪到文件末尾的 `if __name__ == "__main__"` 之前，`python3 tests/test_assistant_ui.py`
   由 167 项变 169 项，全过。外壳分支 `630ca3aa` 上同样如此，开发窗口下次在外壳分支改或合增量时注意（本次只在本分支追加提交）。
   全仓另有 `test_cowork_express_sales_mapper`、`test_cowork_recon_scanned_docs`、`test_desk_first_load`、`test_queue_targets` 四个文件 `__main__` 之后
   还有定义，都是早已存在的，不在本次范围。
3. **记录说 `tools.py` 有 `written.writer`（成立，已改记录并补说明）**：上文已更正；`WRITTEN_NOTE` 补一句 `written.writer` 是写入 Express
   的电脑名、用户问哪台电脑写的就给这个名字，`tests/test_cowork_assistant_tools.py` 加一项断言。
4. **同第 2 条（major）**：一并处理。
5. **第一遍闸门不算证据（成立，已改记录）**：上文「闸门」一节只以第二遍为准。
6. **电脑与规则现状没有同一条测试（成立，已补）**：`PushRuleReadbackTests` 加一项：一条带 `written_by_device` 与冻结 `company_rules` 的成功记录，
   `written()` 与 `outcomes()` 都同时有 `writer` 与 `company_rules[0].still_saved`。把 `written` 里的 `_with_writers` 去掉、或 `outcomes` 不再查规则现状，
   这项都报错（变异后已还原）。

闸门（追加提交的最终代码树，代码文件都在开跑前改完，跑完核过没有文件比日志新）：`SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`
91 步全过、退出码 0；`cd frontend/assistant && npx tsc --noEmit -p .` 通过；`check-architecture.py` 0 违规；`i18n-build.py --check` PASS；
`test_assistant_ui.py` 169 OK、`test_cowork_assistant_tools.py` 193 OK、`test_cowork_express_dispatch.py` 63 OK；文案闸门只读跑见第 1 条。

## 并入 main（10-18，本地提交，未推送）

`origin/main` 并进 `pearnly/multi-desktop`（`58d654f4`），用 merge、不变基、不改写历史（分支里有 4 个外壳合并提交）。合并基点
（merge-base）`84da6f81`（外壳分支第 11 轮合并审查，含 main `5edbd5e4`）；main 这段 43 个提交（39 个非合并），到 `19a26d22`
（开工时 `origin/main` 是 `8626eb39`，合并中途前进了一个提交：控制面 CC 设置改成全平台一份）。分两段合：

| 合并提交 | 合到 | 带进来的 |
|---|---|---|
| `0a228f45` | `c4ab0c3e`（文案闸门 `662cacd9` 进 main 之前那个提交，35 个） | 统一原件层 ADR-024、Claude 识别三批与原件存甲骨文（ADR-023）、第 11 轮重测修复、CC 前端标准化 B0 闸门、交接与发布记录 |
| `fd0cfd38` | `19a26d22`（其余 8 个） | 界面文案闸门与评审轮、存量文案清单、控制面 CC 设置全平台一份 |

为什么分两段：提交钩子的文案检查（`check-copy.py --staged`）只比 HEAD → 暂存快照。一次合并时，它把 main 自己在闸门进 main
之前（`a9f44116` ERP 原件层）加的词条当成本次提交新增，拦下合并提交（见下文「被拦的已有文案」）。这条是 main 上的已有文案，
改它要用户登记同意，本分支不改、不豁免、不绕钩子。先合到闸门之前（暂存树里还没有闸门，钩子按设计跳过文案检查，架构检查照跑），
再合余下的（钩子只看闸门那几个提交带来的：0 拦截，删除 5 条是控制面去掉的词条）。两段合完的树与一次 `git merge origin/main`
完全相同：`git merge-tree --write-tree 58d654f4 19a26d22` = `fd0cfd38^{tree}` = `27b6d8da`。

### 冲突怎么解

两段都没有文本冲突。两边都改过的 6 个文件逐个核过语义：

| 文件 | 解法 |
|---|---|
| `.github/workflows/development.yml` | 自动合并，两边的步骤都在：main 的 checkout `fetch-depth: 0`（文案闸门要读基线）、`test_cc_frontend_gate.py`（标准化闸门自测）、`test_check_copy.py`（文案闸门自测）、`check-copy.py --report`（带 `COPY_GATE_BASE`）、`check-frontend-i18n.py --strict`；本分支多电脑那几步（密钥值、多台电脑、账套登记、写账分派、心跳、跟随当年账套）原样。run 步骤 92 → 96 |
| `apps/pearnly_cowork/pearnly_cowork/assistant/tools.py` | main 只把 `document_original` 里的 `pearnly_cowork.ocr.originals` 换成 `pearnly_core.originals.checks`；本分支的 `_push_one(route_device)`、`TARGET_NOTES`、`folder_readers` 在别的段，自动合并 |
| `tests/test_cowork_assistant_tools.py` | main 只改 `OriginalTests` 的模块替身（原件层父包 `pearnly_core.originals` + `checks`）；本分支加的测试不受影响 |
| `tests/test_cowork_delete_everything.py` | main 改 `OriginalPurge` / `CloudPrefix` 的替身（原件层 `store.parse`、`gcs`、`rules`）并加 `OraclePurge`；本分支只改 `FolderRuleDelete` 的替身（`companion.devices`、`folder_intake.readers` / `follow`），不同段 |
| `tests/test_assistant_ui.py` | 两边各加的测试类都在，没有重名类，最后一个类在 `__main__` 之前；脚本方式运行 224 项全过 |
| `docs/development/architecture.md` | main 改「Claude 识别路线」一段、加「统一原件层」一节；本分支改的是域表 push / connections / companion / folder_intake / companies 几行与机器接口一段，不同段 |

补丁号与 ADR 号不撞：本分支 Cowork 补丁 `v0_0_19`–`v0_0_23`，main 这段只加了控制面 `v0_0_4_cc_settings_platform_wide`（另一个 App 的
`patches.txt`）；ADR 本分支 021，main 022–024（main 上 `docs/adr/README.md` 的索引没列 022–024，属 main 的现状，这次不改）。
依赖没变：`frontend/assistant`、`frontend` 的 `package*.json` 与 `companion/` 这段 main 都没改，没装、没重装 `node_modules`。

### 语义核对

1. **原件层搬走的旧模块本分支没有新调用**：`pearnly_cowork.ocr.originals` / `ocr.inputs` / `files.cloud` 搬进 `pearnly_core.originals`
   后全仓只剩 `tests/test_cowork_ocr_contracts.py`、`tests/test_cowork_ocr_source_parity.py` 两处 `from pearnly_cowork.ocr import inputs`，
   main 上同样如此、不在闸门里，不是这次带进来的。main 删掉的 `files.service._remote_ready` 本分支没有引用。
2. **文件夹录入的原件入口签名不变**：本分支 `folder_intake/pump.py` 调 `files.service.create` / `finish_direct`、`machine.py` 调
   `begin_direct`，参数与 main 一致；`begin_direct` 不直传时仍回 `{"direct": 0}`（`machine._upload_target` 认它改走经接口上传），
   直传时回原件层 `store.begin_upload` 的结果（main 自己的测试守着）。
3. **共用层对话底座**：main 第 11 轮重测改的 `pearnly_core.assistant` relay / sessions / toolrun 本分支没改过；前端 main 改的
   `assistant.tsx`、`delete-chats.tsx`、`sidebar.tsx`、`drag.tsx`、`globals.css` 与本分支改的 `settings.tsx`、`pending.tsx` 等不重叠，
   `tsc --noEmit` 通过。CC 前端标准化「已完成文件清单」（`scripts/frontend-tokens-strict.txt`）现在是空的，本分支文件不在里面。
4. **控制面 CC 设置全平台一份**（`19a26d22`）只动 `pearnly_control` 与 `tests/test_assistant.py`，本分支没碰。

### 闸门

| 检查 | 结果 |
|---|---|
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（HEAD `fd0cfd38`，工作树干净，跑的期间没改文件） | 96 步全过，退出码 0 |
| `cd frontend/assistant && npx tsc --noEmit -p .` | 无输出通过 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/i18n-build.py --check` | PASS（5260 条文案） |
| `python3 scripts/check-copy.py --report`（基线 = merge-base origin/main = `19a26d22`） | 新增 66、改动 0、删除 1（`SETTINGS.alwaysOn`「常驻模式」，10-16 去掉常驻时删的）、拦截 0、标黄 9（`Computer`、`intake.agent.state.offline`、`push.no_writer` 的 R-ALIGN，`Folder ID`、`Index Rebuild Pending Folder IDs` 的 R-DEV / R-LEN，`ERR_ACCOUNT_UID_MISMATCH` 的 R-SENT 等）；没有改已有文案，不需要登记 |
| 提交钩子（两段合并） | 都过：架构 PASS；第一段文案检查按设计跳过，第二段 0 拦截 |
| CC 桌面版 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1 / httpx 0.28.1 / dbf 0.99.11 / pytest 8.3.3 临时虚拟环境，跑完删） | 901 passed, 41 skipped（`companion/` 这次没变，与上次相同） |

被拦的已有文案（main 上的，交用户定，本分支没改、没登记）：ERP 词条 `The file could not be saved. Please try again shortly.`
（`apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv:1328`，中文「文件暂时保存不了，请稍后再试」，R-TALK「了，」进度口语）。
它由 `a9f44116`（ERP 原件层）带进 main，晚于存量文案清单的基线 `786c376d`，不在存量清单里；main 和各分支的全量闸门因为它在基线里
不判它，只有把 main 合进别的分支时提交钩子会碰到（下一个合 main 的分支同样会被拦）。

没做 / 没验：推送与分支 CI（远端仍停在 `f87a0585`）、Windows 上的 Companion checks、真实 Site 迁移、测试机与真 Express。

### 补登：10-17 两次并入外壳（`80ba3f8f`、`58d654f4`）

`80ba3f8f`（第一父 `3e895ef3`，对方外壳 `9798f54e`：第 10 轮 B 修复、第 11 轮停止后的卡片、滚动与按钮照 Claude、删除确认、「记费用」、
过期审批取消、当时的 main）：

| 文件 | 解法 |
|---|---|
| `frontend/assistant/src/i18n.ts` 中文 `COMPANY` | 文本冲突：两边都要——本分支加的 `folderGone`「电脑已移除」、`followCompany`「跟随公司当年账套」，加外壳把 `vatExpense`「计费用」改成「记费用」 |
| `tests/test_assistant_ui.py` | 文本冲突两处：① 外壳加的 `PREFS_STUB`（本分支那边是空），取外壳；② `Round10DragTests` 末尾：本分支那边是合并审查第 2 条挪到 `__main__` 之前时的旧断言（`save({ groups: … }, [groupField(group)], tidy)`），外壳那边同一类已改写（取消置顶 `unpinSettled` 先于 `forgetPinned`）并在后面加了第 10 轮 B、第 11 轮几类；取外壳那边，旧断言不留 |
| `frontend/assistant/src/components/settings.tsx` | 没有文本冲突，但合并结果与两边都不同（`git show --cc` 可见）：「电脑」行尾「···」菜单从旧 `menuItem` 改成 `cc-menu` / `cc-menu-item`（`data-danger`），因为外壳第 11 轮删了 `menuItem`；之后 `38a86fb0` 再给触发钮加 `cc-tap`、`AccountSetRow` 两颗按钮改 `cc-card-btn`（见上文「并入外壳（10-17）」语义核对第 4 条） |

`58d654f4`（第一父 `38a86fb0`，对方外壳 `84da6f81`：第 11 轮修复——卡片跟本站点的工具运行记录、经自己的中断停止、滚动、拖动帧、
删除确认的焦点、触屏平板顶栏；当时的 main 含 Claude 识别路线）：

| 文件 | 解法 |
|---|---|
| `tests/test_assistant_ui.py` `Round11ButtonsLikeClaudeTests` | 文本冲突一处，两边断言都留：待处理页 `cc-card-btn self-` 计数按两边相加改成 6（当场选 3 + 外壳 D32 列表底部「交给 CC」1 + 本分支 `AccountSetRow`「确认」「交给 CC」2）；本分支「电脑」行 `cc-tap` 断言与外壳 D26 触屏平板顶栏那组断言都在 |

两次合并当时的闸门结果在本记录、开发交接（`~/pearnly-devtools/dev-window/交接-开发窗口-2026-10-18.md` 只写「最后一次闸门 91 步全过」，
没绑定提交）和 `~/pearnly-devtools` 的日志里都查不到。以本节 `fd0cfd38` 上的 96 步全过为准，它包含这两次合并的全部内容。

## 用户 10-18 定（经测试窗口转达，AskUserQuestion 原选项）

- D2 第 7 批前对生产 erpcowork 只读核对（一次性只读客户端、不改数据）：「授权只读核对」。
- D12「第 8 批」：「可以，第 8 批=发布」（方案 4.4：服务端批 1–4、6 一次发，紧接着发 CC 桌面版 1.7.0 正式版）。
- D13 定时任务的票在待处理页补选后谁的电脑写：「读文件夹的电脑」（保持现做法，仍算定时任务，pending_api 照旧传 route_device）。
- D14 已有文案 `intake.agent.state.missing`（10-16 起实际意思是「你没有连上的电脑」）：「改成「未连接电脑」」，四语如下（用户 10-18 当晚另说「涉及到文案的今晚特批，但是不用有口语」）：
  - 中：未连接电脑
  - 英：No computer connected
  - 泰：ยังไม่ได้เชื่อมต่อคอมพิวเตอร์
  - 日：コンピューター未接続
  代码注释里引用旧说法「CC 桌面版未连接」的 5 处同步改成「未连接电脑」；给模型的说明（`TARGET_NOTES`）不是界面文案，不改。
- 测试机装 1.7.0（去掉常驻，改为「CC 开着时保持电脑唤醒」）：「同意装」。安装包由只构建不发布的 `companion-build.yml` 从本分支打。
- 第二个测试成员加在 lab 上的 testcowork（第 7 批在 lab 跑，lab-start 不带 REFRESH 保留）；多电脑上生产（D16）后再在生产 testcowork 补加同一个测试账号。

## D2 生产只读核对结果（10-18）

时间是 2026-10-17 20:00–20:03 UTC。做法：经 `pearnly-production-cowork-web-1` 里的 `bench --site erpcowork.pearnly.com mariadb`，会话设成只读事务，只跑 SELECT；没有 exec 进 mariadb 容器，什么都没写；SSH 2 次。
线上镜像 revision `ec90fdfc`（在 main 上），是旧结构：补丁到 `v0_0_18_history_push_targets` 为止，多电脑的表和列都还没有。只查了 erpcowork，工作空间 1 个。
完整结论（计数与编号，不含单据明细）见 `~/pearnly-devtools/dev-window/D2-生产只读核对-2026-10-18.md`。

| 问题 | 结果 |
|---|---|
| Express 连接、主连接 | 共 2 条，都启用、client 都为空、代数都为 0。**主连接是 `CWEC-9f8ad3b2-b89c-54aa-8221-afc06bf7dbe6`**（creation 2026-09-30 18:17:35，站点时间）。后建那条 `CWEC-79d8f2db-7fc6-50ae-8211-c11eb01c8a45` 晚 3.3 秒建。两条的使用名单、建的人、有效密钥签发人、显示名、所选账套都相同，名单都只有 1 人。MR.ERP 主连接是 `CWEC-14e095aa…` |
| 有效密钥 | 共 2 把，**主连接、后建连接各 1 把**，分属两台电脑。主连接那台是 1.6.0，10-15 才登记，最后报到 10-17；后建连接那台是 1.3.5，最后报到 10-16。两台上报的账套有 34 份重合。10-15 时只有后建那条上的 1 把 |
| 人工处理单 | `manual` 13 张，**全部挂在后建连接上**；`result_unknown` 都是 0，证据 v1，不占 `active_key`。原因：回读核对失败 12 张、`tax_id_invalid` 1 张；都是 9-30 产生的，只涉及 1 份账套，这份账套现在只有主连接那台在上报。另有 `success` 1 张在主连接上 |
| 定时任务 | **0 条**（运行、文件、公司文件夹也都是 0）；**挂在非主连接上的 0 条** |
| v1 结果不明 / 领取中 | **都是 0**；排队中、已准备也是 0。推送尝试 37 次全部 finished，写者锁没有持有者 |

判定：

- 主连接按规则是 `9f8ad3b2`。上线后固定认这条，和正在用的那台电脑（1.6.0，账套最多，唯一的成功单也在这条上）一致。现在的旧代码按 `modified DESC` 取第一条，此刻也是它，但这个结果会随心跳来回变。
- 没有挂在后建连接上的定时任务，不需要迁移办法。
- 后建连接上剩下的东西：1 把有效密钥（1.3.5 那台）、13 张人工处理单、7 条记住的选择、历史快照和尝试。没有在途单，没有锁，不挡部署。建议都不写补丁搬：
  - 13 张单：待处理页按录入记录列，部署后照旧显示。由用户决定作废，或者在主连接上重新录入。
  - 1.3.5 那台：如果还要用，装 1.7.0 后在那台电脑上登录 CC 一次，register v2 会在主连接上给它发自己的密钥。它一直没自己升级，能不能自动升级没核实，按手动装来准备。不用了就在部署后到「电脑」列表移除。要问用户这台还用不用。
  - 7 条记住的选择：在主连接上再问一次。
- 部署前进维护时仍按方案 4.4 第 1 步再统计一次 v1 单（主连接那台电脑还在用）。部署后回读预期：电脑 2 台；`reader_device` 回填 0；`all_members=1` 只设在 `9f8ad3b2`；两条 Express 连接的 `record_revision` 保持 3，`config_json` 不变。

## 1.7.0 空闲 CPU 与 03:15 重启（10-18）

来源：测试窗口 10-18 03:2x 实测 CC 桌面版 1.7.0（`9dd0992c`）两台 Windows 什么都没做时持续吃 1–1.5 个核、风扇一直响；之后用户（经开发窗口转达）定目标：
「CC 桌面版必须要轻量设计并满足老机器能正常运行使用，架构不对就重构架构」。基线 `e766caa0`，本节提交 `70b65896`、`e13c742a`、`08792e73`、`a9ed8a09`、
审查修复 `c25925ba`（本地，未推送）。

### 目标（以老办公机为准：双核 / 4 线程、4 GB、Windows 10、机械硬盘或低速 SSD）

- 后台空闲（托盘、窗口没开）：CPU 30 秒均值 < 1% 单核，只有心跳 / 扫描的瞬间短峰；常驻内存目标 < 150 MB，不常驻 Chromium 内核。
- 打开 CC 窗口时才加载网页内核，关窗后释放（不是只隐藏）；登录窗口、CC 窗口不开时没有 QtWebEngineProcess。

### 现状数据（只读采样，两台机器都没改任何东西）

采样做法：PowerShell 里 `NtQuerySystemInformation` 取每条线程的 CPU 与上下文切换次数，两次快照相减（30 / 20 / 125 秒窗口）；
线程起始模块用 `NtQueryInformationThread(ThreadQuerySetWin32StartAddress)` 对到 DLL；另每 2 秒看一次几条后台线程的累计 CPU 找节奏。
脚本留在 `~/pearnly-devtools/dev-window/cc-idle-probe.ps1`、`cc-thread-rhythm.ps1`（只读，用完删远端副本）。CPU 都按单核百分比。

| | 办公室测试机（i5-1135G7 8 线程 16 GB，skin3 已登录，CC 窗口 03:15:48 起开着） | 录制机（i5-1155G7 8 线程 8 GB，acct，CC 没登录：请求全 401，窗口 03:14:53 起开着） |
|---|---|---|
| companion.exe | 45–53%（30 / 125 秒），上下文切换 2 200–2 600 次/秒；WS 290 MB、私有 242 MB、42 线程 | 55–77%，2 800 次/秒；WS 77 MB、私有 262 MB |
| QtWebEngineProcess（渲染进程，1 个） | 35–38%：主线程 18%、合成线程 16%（约 300 次/秒） | 88–101%：主线程 69–80%、合成线程 16% |
| 渲染进程内存 | 私有 1 757 MB（03:25:51）→ 2 102 MB（03:27:53），约 +170 MB/分钟 | 私有 3 315 → 3 448 MB（33 秒内），约 +240 MB/分钟 |
| companion.exe 里谁在花 | 网页内核自己的线程（`Qt6WebEngineCore` 起始）17.8%（内核态 14.5%）+ 12.9% + 3.3%；主线程（Qt 与 Chromium 界面线程同一条）9.2%；Intel 显卡驱动线程 4.7% + 2.3%（600 / 500 次/秒）；`WINMM` 高精度定时线程 0.6%（181 次/秒）——都是网页内核在出帧 | 同左：网页内核线程 24.7% + 13.0% + 2.3% + 2.2%，主线程 6.5%，显卡驱动 4.5% + 1.3%（890 / 506 次/秒） |
| 纯后台的 Python 线程 | 按需请求线程 1.5%（**每 15 秒一次、每次约 250 ms CPU**）、文件夹线程 0.4%（每 60 秒一次约 280 ms）、取任务线程 0.1%、保持唤醒线程 0 | 401 退避中，量不出 |
| `powercfg /requests` | DISPLAY、SYSTEM 都挂着 companion.exe | 同左 |

### 根因

**CPU（主因）：窗口开着就一直按屏幕刷新率出帧，而且网页内核一旦起来就再不释放。**

- 两台的窗口都开着、没人看（办公室那台是安装程序完成页拉起的，见下面「03:15 重启」；录制机 03:14:53 有人打开过）。Qt 的网页内核只看窗口
  自己有没有藏起来，不像 Chrome 那样在锁屏、被遮住、屏幕关了时停画；页面上又有一直在动的东西：登录页整窗背景点阵是 `requestAnimationFrame`
  每帧重画约两千个点（`scripts/build-ai-entry.py` 生成的 `ai.*.js`），外加小猫的 SVG 无限动画；对话页欢迎页的 CC 小猫有眨眼 / 笑 / 尾巴 / 铃铛
  四组无限动画（`frontend/assistant/src/globals.css`）。于是渲染进程、companion.exe 里的合成 / GPU 线程、显卡驱动线程一直每秒几十帧地转。
  证据：合成线程、驱动线程每秒几百次唤醒；本机实验（PySide6 6.11.1，offscreen）窗口 `hide()` 后 `visibilityState` 变 `hidden`、
  `requestAnimationFrame` 计数停住（183 → 183），`page.setVisible(False)` 同样停住。
- 1.7.0 的设计是「关窗口 = 藏到托盘、网页内核一直留着」，打开过一次就常驻几百 MB；没打开过窗口时托盘启动也会经 `QuickAsk` 导入
  `gui_tomo` 把 QtWebEngine 模块载进来（本机对照：托盘进程最大常驻 154 MB，去掉后 103 MB）。
- 渲染进程内存每分钟涨 170–240 MB：只在页面一直出帧时出现，具体是网页内核哪一层在攒没查实（远程没有调试工具）；窗口关掉进程退出、锁屏 / 关屏停画
  之后这条路就没了，复测时看窗口开着的那段内存曲线。
- 录制机登录页还有一个页面缺陷（**登录页按用户 10-17「登录页不动」没改，要不要改请用户定**）：画点阵的循环只在页面看不见时停，`visibilitychange`
  变回看得见时又另起一个 `requestAnimationFrame(draw)`，而停住时排着的那一帧回来后照样续上——每最小化 / 还原一次就多一条画点阵的循环，越开越费。
  〔用户 10-18 授权只修这个循环缺陷（样子、布局、文案不动）：分支 `pearnly/login-dots-loop`（基于 `origin/main` `3a33e066`）改
  `scripts/build-ai-entry.py` 并重新生成入口第一页与 `pearnly-ai` 主题脚本；任何时候最多一条绘制循环，看不见时取消排着的那一帧、停画，
  改窗口大小只重画不另起（原来每次 resize 也多一条）。无界面 Chrome 实测切换 10 次 + 改 3 次大小：旧版排着的帧 15 个、450 次回调 / 秒，
  新版始终 1 个（看不见时 0）、30 次 / 秒，前后截图逐像素一致。提交 `748f15ac`，本地，未推送、未合入 main、未部署。〕
  录制机渲染主线程 69–80%（办公室对话页只有 18%）与此相符。

**纯后台（窗口关着也有）：每次新建 HTTPS 客户端约 250 ms CPU。** 按需请求线程每 15 秒、文件夹线程每分钟都新建一个 httpx 客户端，每次都新建 SSL
上下文、把 certifi 的一百多张根证书重新解析一遍（Windows 上 Python 3.11 自带 OpenSSL 3.0，这一步出名地慢），再重新握手。2 秒一次的采样里按需请求线程
每 15–16 秒一个 250–280 ms 的尖峰，文件夹线程每 60 秒一个约 280 ms 的尖峰，其余时间都是 0；按需请求一轮只做「新建客户端 + 发一个请求」，CPU 都在这里
（单次拆成「解析证书 / 握手 / 请求」各占多少是推断，复测看尖峰是否消失）。在快的 i5 上合计约 2% 单核，老电脑上成倍。文件夹计数本身（`survey`）在
办公室那台上可以忽略，没改。

**DISPLAY 请求：** `SetThreadExecutionState` 的 DISPLAY / SYSTEM 只是两个标志，保持唤醒线程每 15 秒醒一次，本身不占 CPU、不会让风扇转。但它让屏幕
永远不自动关（现代待机电脑上就是这样设计的，D8 待实测定默认值，这次不改口径〔10-18 用户定用电池默认不保持，见「D8」〕），屏幕不关、会话也就不会因为关屏自动锁上——CC 窗口开着时，原来页面会
一直出帧到天亮。改完以后窗口关着就没有网页内核；窗口开着但锁了屏会停画；**窗口开着、不锁屏、屏幕又被 DISPLAY 留着亮着** 时页面仍按它自己的动画出帧，
这一条取决于页面动画（上面登录页那条）与 D8 的默认值。

### 03:15:48 重启的原因（不是 CC 的缺陷）

- 应用程序日志：`Microsoft-Windows-RestartManager` 会话 10000 / 10001 在 03:15:08 开始、03:15:49 结束，用户 `skin3`（前一次 03:12:04–03:13:14，
  即测试窗口 README 记的 03:13:14 那次 1.6.0 → 1.7.0 安装）；安装目录 `unins000.exe` 03:15:14 新建、`unins000.dat` 03:15:38 改过——03:15 又跑了一次安装程序。
- 安装程序 `PrepareToInstall` 先 `taskkill /F /IM companion.exe`：旧进程（pid 13736）是被强杀的，所以 03:14:25 心跳之后日志里没有退出或报错。
- 新进程 pid 13548 的命令行是 `companion.exe`（**没有 `--background`**），父进程 2800 已不在，主线程建于 03:15:48.386，早于 RestartManager 会话结束
  （03:15:49）：是安装程序结束前的 `[Run]` 拉起的。`installer.iss` 里静默安装（`/SILENT`、`/VERYSILENT`）走 `--background` 那一行，带
  `skipifsilent` 的无参数那一行只在**非静默**安装（完成页「Launch CC」）时跑——所以 03:15 那次是一次非静默安装，以 skin3 身份。CC 自己的自动更新
  走 `/VERYSILENT`，装完是 `--background`；activity.log 也没有「发现新版 / 正在更新 / 自愈重启」的记录；任务计划程序操作日志在这台上没开。
- 结论：重启是 03:15 那次安装（多半是测试窗口自己的装包步骤又跑了一次，且没带静默参数），不是 1.7.0 的看门狗、更新检查、单实例或计划任务。
  它的后果正是上面的 CPU：非静默安装完直接打开 CC 窗口，窗口一直开着。录制机 03:10:13–03:10:38 那次是静默安装，起来的是 `--background`，对得上。
  请测试窗口核对自己 03:15 的装包命令。

### 改法（不改功能、协议、文案）

1. **托盘进程不加载网页内核，CC 窗口单独一个进程**（`70b65896`）：`companion.exe --window`（`window_main.py`）在要打开窗口时由托盘拉起
   （`window_link.WindowHost`），关窗口就退出，QtWebEngineProcess 随之退出；正在回复（页面 `window.tomo.busy()`，`frontend/assistant/src/lib/desktop.ts`
   新加）时先藏起来，每 3 秒问一次，回复完、通知弹出后再退（最多等 30 分钟；旧页面没有 `busy` 当没在回复）。托盘退出、管道断了窗口进程也退。
   两边经本用户的本地管道说话（`QLocalServer` 只本用户可连，拉起时经环境变量给一次性口令，`hello` 口令不对就断开；一行一条 JSON；收到的先排队、
   回到事件循环再逐条处理，处理时可以弹框、等对方回复）。对话页的桥照旧在托盘里的 `tomo_bridge.TomoBridge` 应答（只在 `*.pearnly.com/assistant`
   对话页、`pick_folder` 核对站点都不变），窗口进程的 `RemoteBridge` 转过去，窗口按钮在窗口进程本地做；选择文件夹框由托盘请窗口进程弹，以 CC 窗口为父、
   模态（同原来）。窗口里的通知交托盘弹（`notice:<编号>` 去重不变），点了转回窗口；窗口进程已退出就打开窗口，下载完成的「在文件夹中显示」托盘自己做。
   快捷输入条与 `reveal` 挪到 `gui_quick.py`，图标挪到 `gui_icon.py`；窗口进程日志写 `logs/window.log`（不和托盘抢着轮转 companion.log）；
   打包入口 `launcher_companion.py` 见到 `--window` 直接进窗口（不导入取任务 / 直写那一套），`main.py` 的 `--window` 在单实例锁之前分出去。
   自动更新「窗口开着且在最前面先不装」改看窗口进程报来的状态。
2. **后台空闲不白花 CPU**（`e13c742a`）：所有 httpx 客户端共用一份 SSL 上下文（`http_tls.py`，`httpx.create_ssl_context()`，与默认完全相同）；
   按需请求线程每条连接一直用同一个客户端（连接保持），出错、被拒、连接不再在用、线程停下时才关；托盘图标、悬停提示、菜单状态行只在变了时重画
   （取任务线程每 3 秒报一次「空闲」）。
3. **窗口开着但没人看得见时停画**（`08792e73`）：窗口进程登记锁屏 / 解锁（`WTSRegisterSessionNotification`）与屏幕开 / 关（`GUID_CONSOLE_DISPLAY_STATE`）
   通知，锁着或屏幕关着就 `page.setVisible(False)`（动画、重画停，回复与通知照常），解锁 / 亮屏且窗口开着没最小化再设回；窗口被激活时一律清掉这两个标志
   （`session_watch.py`）。
4. **从托盘打开窗口时拿到最前面**（`a9ed8a09`）：Windows 只许前台进程抢前台，托盘把权利交给窗口进程（`AllowSetForegroundWindow`：拉起时给新进程号，
   之后给 `hello` 报的进程号）；再点一次桌面图标的那个进程先放出前台权利再通知托盘。

### 审查（10-18，只读子代理，high）

没有死锁或丢数据的严重问题；1 条中、9 条低 / 存疑，逐条处理（`c25925ba`）：

| # | 发现 | 处理 |
|---|---|---|
| 1（中，两个进程实验复现） | 选择文件夹框开着超过 2 分钟：窗口进程的 `call` 先超时，页面报失败，托盘却已改了根目录 / 公司文件夹 | 会弹选择框的 `pick_roots` / `pick_folder` / `add_folder` 不设上限，其余 2 分钟照旧 |
| 2 | 第一次启动超过 30 秒后又点打开，第二个窗口进程要把网页内核建完才收到「退出」，两个进程同时开同一份网页存储 | 窗口进程连上后先等托盘的第一条命令（最多 5 秒），是 `quit` 就不建窗口直接退 |
| 3 | 窗口进程没连上就退了：30 秒内再点没反应，之后重拉会重放早先的快捷提问 | 托盘留着进程对象，发现已退出就马上重拉，旧命令丢掉、只做这一次点的 |
| 4 | 退出 CC 时迟到的 `hello` 仍被收下、窗口闪一下 | 正在退出时一律叫它退，也不再拉起 |
| 5 | 窗口进程刚退时点通知（毫秒级竞态）；页面实时收到的对话提醒，窗口进程退了以后再点只打开窗口、不打开那段对话 | 没改，记在下面「没做」 |
| 6 | 口令留在窗口进程环境里会被子进程继承；打包后自己拉起自己沿用 PyInstaller 环境 | 读完就从环境里删掉；拉起时设 `PYINSTALLER_RESET_ENVIRONMENT=1` |
| 7 | 每开一次窗口托盘里留一个连接对象 | 断开就释放 |
| 8 | 共用 SSL 上下文以后若有客户端开 HTTP/2 会跨线程改协议列表 | 文件头写明（现在全是 HTTP/1.1，无影响） |
| 9（存疑） | 回复刚完就退出，「回复完成」通知可能还没经网页内核另一条通道到托盘 | 等着的回复完了再留 1.5 秒才退 |
| 10 | `window.tomo.busy()` 要先随前端部署；旧页面上关窗即退，回复中关窗的完成通知会丢 | 发布顺序本来就是服务端（含前端）先、CC 桌面版后（D12），复测前确认测试租户前端已是本分支 |

### 取舍

- 每次打开窗口都要新起一个进程、初始化网页内核（本机 Mac 冒烟：拉起到连上 0.2 秒，老电脑要实测）；原来第一次之后再打开是瞬间的。
- 窗口开着时是两个 companion.exe（托盘 + 窗口），比原来单进程多一份 Python / Qt 运行时；窗口关着时只剩托盘一个。
- 窗口关着时，只经页面实时通道来的提醒（定时任务提醒、完成简报）等下次打开窗口才看得到——与现在开机自启后还没打开过窗口时一样；文件夹同步带来的提醒照常由托盘弹；
  关窗时正在进行的回复，完成通知照常弹（窗口进程等回复完再退）。页面实时收到的提醒弹出后窗口进程若已退出，再点它只打开窗口（回到上次的页面），
  不直接打开那段对话（审查第 5 条）。
- 窗口开着、有人在看时页面自己的动画照旧（登录页点阵、对话页小猫），属于页面设计，这次没动。

### 测试

| 命令 | 结果 |
|---|---|
| CC 桌面版全量 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1 / httpx 0.28.1 / dbf / pytest 临时虚拟环境） | 935 passed, 41 skipped（基线 901 / 41）。新 `test_window_process.py` 23 条（审查后加 6 条：选择框不限时、没连上就退的重拉、退出中迟到的 hello、打包后独立实例、断开释放、第二个窗口进程不建网页内核就退，另有口令不留给子进程）：托盘进程（含 `main`）一个 WebEngine / WebChannel 模块都不导入、`gui_tray.py` 里也不导入 `gui_tomo`；窗口进程命令与入口；按行收发与坏行 / 超长行；同一进程里真连本地管道：打开时只拉起一次、连上前排着的命令按顺序交过去、口令不对不收、对话页调桥在托盘应答、托盘请窗口弹选择框、状态与通知到托盘、断开后状态清空再打开重新拉起、后连上的第二个窗口进程被叫退出且它说的不算、`bye` 后马上当作已退出、托盘退出叫窗口退出、前台权利交给窗口进程；托盘接窗口转来的请求与通知（「在文件夹中显示」托盘自己做、其余转回、窗口没了就打开窗口）；窗口进程：没在回复关窗即退、在回复等回复完、等的时候又打开就不退、托盘叫退或管道断了就退、托盘命令驱动窗口、通知交托盘、状态只在变了时报、桥只在对话页转发且窗口按钮不绕托盘；页面有 `window.tomo.busy()`；真的 `TomoWindow` 关窗先藏再交出去、「仍在后台运行」只提示一次（子进程里跑）。新 `test_idle_cost.py` 7 条：一份 SSL 上下文跨线程共用且照样校验证书与主机名、源码里每个 `httpx.Client` 都用它、空闲五轮只建一个客户端、被拒 / 网络错丢掉重建、换密钥关旧的、线程停下关掉、托盘五次「空闲」只重画一次。新 `test_session_watch.py` 4 条：锁屏 / 解锁 / 屏幕开关变暗的消息解析、锁屏关屏藏页面解锁亮屏再显示、关着或最小化的窗口不会被解锁弄成看得见、`TomoWindow` 接线。改过的旧测试 4 条只换了观察点（`_tomo` → 窗口管家、`OPEN_WINDOW` → 窗口进程报来的状态、按需请求客户端「每轮关」→「停下时关」），断言没放宽 |
| 新测试先在改之前跑 | `test_idle_cost.py` 改之前 5 条失败（「线程停下时关掉」那条原来就成立，留作守护）；`test_window_process.py` 在基线源码上收集即失败（没有 `window_link`），其中「托盘不导入 gui_tomo」在基线上不成立 |
| 本机冒烟（真拉起 `python -m src.companion.main --window`，offscreen） | 拉起到连上 0.2 秒、报来看得见与地址、托盘叫退后窗口进程退出码 0；本机托盘进程最大常驻 103 MB（基线 154 MB，少了 QtWebEngine 模块） |
| `python3 tests/test_companion_contract.py`、`tests/test_cc_ccs.py`（3.11） | 92 tests OK |
| 完整本地闸门 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（在 `02ff963f` 上） | 96 步全过，退出码 0（含 `frontend/assistant` 构建与 `check-architecture.py`）；审查修复只动 companion/，之后重跑 CC 全量与架构检查 |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| `frontend/assistant` `tsc --noEmit` | 通过 |

### 要测试窗口在两台机器上复测（新包由开发窗口用 `companion-build.yml` 从本分支打）

装法照旧：核对安装包、`/VERYSILENT` 装（装完起来的是 `--background`），**别用非静默安装**（完成页会直接打开窗口）。每一步用同一个只读脚本采 30 秒
（`cc-idle-probe.ps1 -User <skin3|acct> -Seconds 30`），记 companion.exe 个数与各自 CPU%（单核）、WS / 私有 MB、QtWebEngineProcess 个数与 CPU%；
要看节奏时 `cc-thread-rhythm.ps1 -Tids <线程号…>`（每 2 秒一行，64 秒）。

1. 后台空闲（装完没开过窗口，等 2 分钟）：companion.exe 1 个，**30 秒均值 < 1%**、WS **< 150 MB**；QtWebEngineProcess 0 个；节奏上按需请求线程
   不再有每 15 秒约 250 ms 的尖峰，只剩心跳 / 文件夹每分钟的小峰。
2. 打开 CC 窗口（托盘左键）：记从点击到页面出来的秒数；companion.exe 2 个（一个带 `--window`）+ QtWebEngineProcess 1 个；窗口在最前面。
3. 关窗口（右上角 ×）：约 3 秒内 `--window` 进程与 QtWebEngineProcess 退出；再采 30 秒应回到第 1 步的数。
4. 发一句话、回复中关窗口：窗口进程留着，回复完弹「回复完成」通知后退出；点通知能打开窗口。
5. 窗口开着按 Win+L 锁屏，采 30 秒：QtWebEngineProcess 与 companion.exe（窗口进程）CPU 应降到接近 0、内存不再涨；解锁后页面照常。
6. 回归：托盘「连接与设置」打开设置「连接」一栏；设置里公司「选择文件夹…」弹在 CC 窗口前面、是模态的；Ctrl+Alt+Space 快捷提问能开新对话发出去；
   对话提醒点开那段对话；窗口开着且在最前面时自动更新等着、点「立即更新」照装；第二次双击桌面图标把已有窗口拿到前面（或打开窗口）。
7. `powercfg /requests` 照旧（有成员连着就是 DISPLAY + SYSTEM）。
8. 录制机（没登录）上第 1、3 步同样做；登录页开着时的 CPU 记下来，给「登录页要不要改」做依据。

### 没做 / 拿不准

- 登录页点阵循环每次「看不见 → 看得见」多一条（见根因）：登录页按 10-17 口径不动，要改请用户定（改法是只在没有排着的帧时才重新开始画，或干脆静态）。
- 对话页小猫的无限动画、登录页背景在「窗口开着、不锁屏、屏幕被 DISPLAY 留着亮着」时仍会出帧：取决于页面设计与 D8 默认值，这次不动。〔10-18：D8 定用电池默认不保持；登录页叠加循环另在 `pearnly/login-dots-loop` 修，见上〕
- 渲染进程内存为什么在一直出帧时每分钟涨上百 MB 没查实；复测第 2 步开着窗口的那段看内存曲线，若仍涨再单独查。
- Windows 专属的几处（真的本地管道权限、`AllowSetForegroundWindow`、锁屏 / 关屏消息、打包后 `--window` 入口）Mac 上只能用假的验证，以复测为准。
- 审查第 5 条：页面实时收到的对话提醒，窗口进程退了以后点它只打开窗口；要直接打开那段对话得让页面把提醒的去处交给桌面端（页面与桥的协议要加一项），这次没做。

## 锁屏 / 最小化停画（10-18 复测后）

来源：测试窗口 10-18 11:27–11:36 在办公室测试机复测 `CC-Setup-1.7.0-test2.exe`（`cbf1ddaf`，记录 `~/pearnly-devtools/test-window/round12/CC桌面版-1.7.0-test2-复测.md`，
原始输出 `round12/cc170b-*.txt`）：后台空闲达标（0.2%、80 MB、没有网页内核），但**窗口开着 Win+L 锁屏后窗口进程 59%、网页内核 35%**，比锁屏前还略高——
`08792e73` 的「锁屏停画」在真机上没生效。本节提交 `77df11f1`（本地，未推送）。对话页小猫的无限动画由另一个子代理在 `frontend/` 改，这里只管桌面版一侧。

### 为什么 08792e73 没生效

- 测试机只读核对（10-18 11:4x，`logs/window.log`）：窗口进程 11:32:30 起来，LogonUI（锁屏）11:32:55 起来；日志里**没有**「登记锁屏通知失败」——
  `WTSRegisterSessionNotification` / `RegisterPowerSettingNotification` 当时都成功了，失败在后面。
- 通知登记在 CC 窗口（`TomoWindow`）自己的系统句柄上：构造时 `winId()` 建出窗口、登记，然后才 `page.setUrl()`。对话页一开始载入，`QWebEngineView`
  把渲染控件（Qt Quick 控件，GPU 合成）挂进窗口；Qt 发现顶层窗口要从普通绘制换成 RHI 合成表面，就把窗口的系统句柄**销毁再新建**
  （Qt 6.11.1 `qwidget.cpp` `QWidget::setParent` 里「Recreating … with current type … to support …」那段 → `QWidgetPrivate::recreate()` =
  `destroy(); create()`）。登记在旧句柄上的 `WM_WTSSESSION_CHANGE` / `WM_POWERBROADCAST` 从此送到一个已经不存在的窗口，新窗口一条都收不到，
  页面一直当作看得见、一直按刷新率出帧。
- 本机证据：Mac 原生平台（cocoa，PySide6 6.11.1）照同样的顺序先 `winId()` 再载页面：载完 `windowHandle()` 换成了另一个对象、表面类型
  `RasterSurface → MetalSurface`（Windows 上是 Direct3D，测试机窗口进程确实载了 `d3d11.dll` / `dxgi.dll`）。改前对照（旧 `SessionWatch` 原样 +
  假 Windows 接口，offscreen）：登记在句柄 1，照 Qt 的做法重建后 CC 窗口是 2，活着的窗口里没有 1。
- 最小化本来就停：`QWebEngineView` 的 hide / show 事件里 Qt 自己 `page.setVisible(False / True)`，最小化时窗口会收到系统发的 hide（本机 offscreen 实测
  `requestAnimationFrame` 0 帧）。没补，只补记录。
- 被别的窗口完全挡住、只是失焦：原来都照画（Chrome 在 Windows 上有遮挡判断，Qt 的网页内核没有）。

### 改法（`77df11f1`，只动 companion/）

1. **通知登记到单独的消息窗口**（`visibility_watch._MessageWindow`）：一个从不显示、里面没有控件的 `QWindow`，系统句柄一直不变；它的 `nativeEvent`
   收锁屏 / 解锁、屏幕关 / 开 / 变暗（变暗仍算看得见）。CC 窗口自己不再接原生消息。Windows 接口用自己的一份 `ctypes.WinDLL`（设函数签名不影响别处）。
2. **被完全挡住时停**：从 CC 窗口往 Z 序上数（`GetWindow(GW_HWNDPREV)`），看得见、没最小化、不透明（分层窗口要 alpha 255 且不是色键）、不是工具窗 /
   弹出窗、没被系统藏起来（cloaked）、形状是矩形的窗口才算能挡（与 Chromium 的 Windows 遮挡判断同一套规则），边框用 DWM 看得见的边框（不含系统的隐形
   拉伸边），最大化的窗口截到它所在屏幕的工作区；CC 窗口在各屏幕工作区里的部分（任务栏下面那截不算）全被盖住才算挡住，拿不准的一律按看得见。
   CC 窗口在别的虚拟桌面上、整个在屏幕外也算看不见。**连续挡住 3 秒才停**（来回切窗口不闪）。CC 窗口在最前面（激活）时不算；**只是失焦、还露着一块照画**。
   什么时候重算：系统事件钩子（进程外、只在消息循环里回调）只听前台窗口换了、最小化 / 还原、拖动 / 拉伸结束、cloak / uncloak 这几种低频事件；挡住期间
   另按进程盯挡着它的那几个进程的窗口移动 / 关闭 / 藏起（光标、输入光标的事件在回调第一行就丢掉）；兜底再算：挡住时每 2 秒、不在最前面又没挡住时
   每 3 秒（例如前台窗口原地最大化）。CC 窗口两种边框坐标对不上（进程不是按每屏 DPI 算坐标）时不判断遮挡，日志记一次。
3. **怎么停**：`page.setVisible(False)`——`visibilityState` 变 hidden，`requestAnimationFrame`、CSS 动画、重画都停；**页面脚本照跑**（实时推送来的回复、
   回复完成的通知、关窗时问 `window.tomo.busy()` 都照常；计时器按浏览器规则降到每秒一次；前端补拉在页面后台时本来就停、回到前台马上补，见
   `poll-source.ts` 文件头，与浏览器后台标签页一样）。看得见时网页内核按最新内容重画。**不用 `Frozen`**：本机实测冻结后页面计时器 0 次，回复、通知、
   `busy` 都会卡住。关窗口、最小化照旧由 Qt 自己设，这里只记原因、不去改；被 Qt（视图的 show 事件）设回看得见时再对一次。
4. **有人要看马上恢复**：CC 窗口被激活、托盘 / 通知 / 快捷提问打开（`show` / `ask` / `open_conversation` / `open_settings` / `click`）时一律当作没锁屏、
   屏幕亮着、没挡住，当场设回看得见（快捷提问在页面里要等下一帧，不能卡到下一次重算）。
5. **日志**（不加界面文案）：窗口进程 `logs/window.log` 启动时一行「CC 窗口看不见时停画：锁屏通知已登记、屏幕通知已登记、窗口事件 4 个（登记在消息窗口
   0x… 上）」；每次停 / 恢复一行「CC 窗口看不见（锁屏 / 屏幕关了 / 最小化 / 被别的窗口完全挡住）：页面已暂停」「CC 窗口看得见：页面已恢复（停了 N 秒）」。
   原因随 `status` 的新字段 `paused` 报给托盘，托盘在 `logs/companion.log` 记同样两种行（不带秒数）；开 / 关窗口另有日志，不重复记。
6. `session_watch.py` 改名 `visibility_watch.py`（窗口进程 `WindowController` 里建、退出前撤掉登记与钩子）；`gui_tomo.py` 去掉 `nativeEvent` 与激活时清标志的旧接线。

### 取舍 / 限制

- 被挡住停画时网页那块是窗口底色；切回 CC 时网页内核要重画一帧（几十毫秒），挡住满 3 秒才停就是为了来回切窗口时不闪。挡着的窗口原地变小但没换前台、
  没拖动（例如点它的「还原」按钮）时，最多等一个兜底周期（2 秒）才画回来——那几秒露出来的部分是底色。
- 遮挡判断偏保守：工具窗、弹出窗（`WS_POPUP`）、半透明或取不到透明度的分层窗口、不规则窗口都不算挡；**Express 主窗口若是这几类之一，被它挡住就不会停**
  （复测看日志有没有「被别的窗口完全挡住」）。CC 窗口自己是激活状态时即使被置顶窗口盖住也照画。
- 不在最前面时每 3 秒兜底算一次：沿 Z 序往上逐个看窗口，一次约几百个系统调用（几毫秒），老电脑上约 0.1% 单核量级（本机没法量 Windows，复测看窗口进程
  在「失焦但露着」时的 CPU，主要还是页面动画）。
- 锁屏 / 关屏 / 遮挡都是 Windows 专属接口，Mac 上用假桌面（假的 user32 / dwmapi / gdi32 / wtsapi32）测，**以真机复测为准**。

### 测试

| 命令 | 结果 |
|---|---|
| CC 桌面版全量 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1） | 963 passed, 41 skipped（基线 935 / 41；旧 `test_session_watch.py` 4 条并进新 `test_visibility_watch.py` 30 条，`test_window_process.py` 加 2 条） |
| 新测试在改前源码（`cbf1ddaf`）上跑 | `test_visibility_watch.py` 收集即失败（没有 `visibility_watch`）；`test_window_process.py` 8 条失败（新的 2 条 + 构造窗口管家的 6 条）。根因那条另用旧 `SessionWatch` 原样对照：重建后登记的句柄不在 |
| `test_visibility_watch.py` 要点 | 根因：Qt 式重建 CC 窗口（`destroy(); create()`）后，登记通知的句柄仍在一个活着、从不显示的窗口上，经它的 `nativeEvent` 锁屏即停、解锁即画，退出撤掉登记与钩子；锁屏 / 关屏 / 变暗 / 关屏又锁屏的原因与日志；最小化交给 Qt 且解锁不弄醒最小化或关着的窗口；被 Qt 设回看得见时再停；有人用马上恢复；挡住满宽限才停、事件来了马上恢复且只算一次、来回切不停、失焦露着照画、在最前面不算、锁着不重算解锁再算；假桌面上的遮挡判断（最大化盖住、露 7 像素不算、隐形拉伸边不算、两个窗口合起来盖住、十种不算挡的窗口、不透明分层窗口算、只看工作区、最大化边框截到本屏、屏幕外 / 别的虚拟桌面算看不见、坐标对不上与窗口没了按拿不准）；事件钩子只听低频的、按进程盯挡着的、光标事件不管；矩形算术。**真网页内核**（子进程 offscreen，真 `TomoWindow`）：看得见 0.6 秒约 37 帧、失焦露着照样 37 帧、挡住 0 帧且 `visibilityState` hidden、隐藏时脚本照样改页面、点回来照画且是最新内容、锁屏 0 帧解锁照画、最小化 0 帧还原照画 |
| `test_window_process.py` 新 2 条 | 窗口进程报 `paused`、快捷提问一到马上照画、退出时撤掉；托盘只在开着的窗口停 / 恢复时记 companion.log（开关窗口不记、旧窗口进程不报 `paused` 当在画） |
| 四份并行重复跑 `test_visibility_watch.py`（模拟慢机器） | 4 × 30 passed |
| `python3 tests/test_companion_contract.py` | 69 tests OK |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |
| 提交钩子（文案闸门暂存快照） | 新增 0 · 拦截 0 |

### 复测口径（替换上一节第 5 条；新包由开发窗口从本分支打）

每个场景先等状态落定再用只读脚本采 30 秒：`~/pearnly-devtools/dev-window/cc-idle-probe.ps1 -User skin3 -Seconds 30`，记**窗口进程**（`--window`）与
**QtWebEngineProcess** 的 CPU%（单核）与内存；再看 `C:\Users\skin3\AppData\Roaming\Pearnly\logs\window.log` 与同目录 `companion.log` 的状态行。

1. 打开窗口后先看 window.log 第一行附近：「CC 窗口看不见时停画：锁屏通知已登记、屏幕通知已登记、窗口事件 4 个（登记在消息窗口 0x… 上）」。写「登记失败」
   或没有这一行就不用往下测，把日志带回来。
2. 对照（窗口开着、在最前面、不操作）：记一次数，应与上次相近（页面动画在画；另一个子代理改动画后会降）。
3. **锁屏**：Win+L，等 5 秒后采 30 秒——窗口进程与网页内核都应接近 0（单个百分点以内）、内存不再涨；日志「CC 窗口看不见（锁屏）：页面已暂停」。解锁后
   页面照常，日志「CC 窗口看得见：页面已恢复（停了 N 秒）」。
4. **最小化**：点最小化，采 30 秒——同样接近 0；日志「（最小化）：页面已暂停」。还原后照常。
5. **完全遮住**：CC 窗口不最小化，把 Express（或记事本等普通窗口）**最大化**盖住它，等 5 秒（停画要连续挡满 3 秒）后采 30 秒——接近 0；日志
   「（被别的窗口完全挡住）：页面已暂停」。没有这行而 CPU 没降：记下挡它的是哪个程序（Express 主窗口可能是弹出窗口类型，按规则不算挡）。
   再把那个窗口还原成小窗口、让 CC 露出一块：应在 2 秒内画回来（日志「已恢复」），露着时照画属于预期。
6. **只是失焦**：Express 小窗口放在 CC 旁边、点 Express 让 CC 失焦但大部分露着——照画（不该有「已暂停」）。
7. **关屏**（可选）：屏幕自动关或合盖关屏时同样「（屏幕关了）：页面已暂停」；有成员连着时 DISPLAY 请求让屏幕不自动关，这一条可以跳过。
8. 回复中验证：发一句较长的问题，回复中把 CC 盖住 / 锁屏 10 秒再回来——回复内容应是最新的、没有丢段；回复完成的通知照常弹。
9. 远程限制照旧：跨会话看不到窗口、点不到窗口内容，锁屏 / 最小化 / 盖住要人在机器前或用计划任务以 skin3 身份操作，看日志行核对状态。

## 账套清单增量扫描（10-18）

用户口径：「CC 桌面版必须要轻量设计并满足老机器能正常运行使用，架构不对就重构架构」。来源：测试窗口在办公室测试机实测 1.7.0 test3（`b48534d0`）。
只动 `companion/`；协议、上报格式、写入规则不变（写入照旧只认推送目标里明确选定、当场核过的账套，`dbf_schema.py` 第 419–423 行「目标 == 所选才放行」没动）。

### 现状数字

测试机（只读：`companion.log`、`companion.json` 只看数据根与所选账套、各成员的本机账套清单只数行数，没改任何东西）：

- 这个成员授权了 **17 个数据根**：本机 `C:\PearnlyExpress\70EXP`（101 份）+ `\\Accserver\d$\ACCOUNT\` 下 16 个年度文件夹（1252 份），共 **1353 份账套、可写 679**。
- 12:25:23 重启 → 12:25:24「先用上次扫到的账套清单 101 个」→ 12:25:24 到 12:29:29 五次心跳都是「上报账套 101 个 · 可写 101 · 科目 0」→ 12:30:31 才
  「1353 个 · 可写 679 · 科目 133」：**整套扫完约 5 分 7 秒**；测试窗口采样扫描线程约 17% 单核、累计 21.8 秒 CPU，扫完后后台降到 0.3%。
- 为什么本机清单是 101：10-17 20:59 到 10-18 12:25 心跳一直是 101 个（那段时间 16 个 Accserver 数据根都没读到，日志没有更细的原因），整套重扫把它们
  去掉，本机清单 `account_sets_<成员>.json` 也被 11:30 那次扫描写成了 101 行。重启后先报的就是这份，等整套重扫完才回到 1353。
- 科目 0：所选账套的科目表只在整套扫完时随快照带出来，本机不存，重启后到扫完前都没有（心跳不带科目表，服务端保留上一份；网页与日志看到的是 0）。

代码里每次启动做的事（`master_refresh` 第一次整套扫描）：

1. **整盘找新年度文件夹**（每次启动都做）：注册表、桌面 / 开始菜单快捷方式（起一个 PowerShell 子进程解析）、每个盘符与共享按 `*EXP*` 找、再在所有盘符、
   映射过的共享、「映射网络驱动器」用过的地址、个人文件夹下最多 3 层深层查找（每个起点 8 秒 / 5000 个文件夹，并行，总共 20 秒）。
2. 17 个数据根各读一次登记表 `secure\SCCOMP.DBF`；**每份账套 ISINFO 打开两次**（公司名一次、默认科目一次，这张表 249 列）、ISPRD 一次（72 列），
   约 18 次 stat / exists、1 次 `os.access`、1 次打开目录取文件号。公司名等只缓存在内存里，**每次启动都全读**：1353 份 ≈ 4060 次打开 DBF、约两万多次
   网络元数据调用。
3. 之后：每 60 秒看一眼 17 个登记表 + 所选账套 GLACC 的大小 / 修改时间，变了整套重扫（内存缓存还在，只 stat）；每 24 小时整套重扫并整盘找；网页「重新读取」
   整套重扫并整盘找。服务器断开时每 60 秒那一眼要对 16 个够不到的数据根逐个 stat（每个等系统超时）。
4. 所选账套的目录（商品 / 客户 / 供应商 / 部门 / 记账指纹 / 存货科目组）每 30 分钟读一次：只这一份账套，6–7 次打开 DBF（STMAS、ARMAS、APMAS、ISTAB、
   STCRD 最多 10 万行、ISACC、GLACC，每个存货科目组另 2 次）——不是这次的问题，没改。

本机量测（Mac，合成账套，列数照测试机本地测试账套的真表头：ISINFO 249 列、ISPRD 72、GLACC 16、SCCOMP 5；300 份账套一个数据根；
「加 2 毫秒」= 每次 stat / access 加 2 毫秒、每次打开 DBF 加 6 毫秒，模拟局域网共享盘；脚本在本会话临时目录，不进仓库）：

| 场景 | 打开 DBF | stat / exists | CPU | 墙钟 | 墙钟（加 2 毫秒） |
|---|---|---|---|---|---|
| 改前：每次启动（首扫、重启都一样） | 902（ISINFO 600、ISPRD 300） | 5408 | 1.6 秒（满一个核） | 1.6 秒 | 26.8 秒 |
| 改后：头一次（本机没有索引） | 602（ISINFO 300、ISPRD 300） | 2411 | 1.3 秒（限在 15% 个核） | 8.7 秒 | 14.4 秒 |
| 改后：重启（索引在、文件没变） | **0** | 604 + 300 次 access | 0.02 秒 | 0.09 秒 | 2.7 秒 |
| 改后：重启前改了一份 ISINFO | 2（就那一份的 ISINFO、ISPRD） | 612 | 0.03 秒 | 0.14 秒 | — |

按这个比例估测试机 1353 份：重启后不再打开账套表，只剩约 2700 次 stat、1353 次 access、1353 次取文件号，CPU 在 1 秒以内；头一次（装新版后第一次启动）
仍要全读一遍（每份 2 次打开，原来 3 次），CPU 限在 15% 个核。**真机数字以复测为准**。

### 改法（`54aeaa58`，只动 `companion/`；顺带的事件过滤器防护单独在 `afbf68db`）

1. **本机索引** `account_index.py`（新）：每个登记表的非删除行、每份账套 ISINFO / ISPRD 读出来的公司名 / 税号 / 分店号 / 默认科目 / 会计期间、用过的科目表，
   连同读它们时文件的（大小, 修改时间纳秒），存在 `%APPDATA%\Pearnly\account_index.json`（与 `companion.json` 同目录，不含密钥、令牌或单据内容，
   1353 份约 0.7 MB）。`account_probe._account_details` 原来的内存缓存换成它；**ISINFO 一份只开一次**（公司名与默认科目一起读，`_read_isinfo`）。
2. **按指纹只重读变了的**：每份账套两次 stat（ISINFO、ISPRD），与索引一样就不开 DBF；登记表（后台扫描时）、科目表同理。**「可写」「文件号」「网络路径」
   每一遍都现查**（多台电脑认同一份账套靠文件号，`rescan_soon` 之后报上去的必须是现在的）。推送前核账套（`worker._probe_task_account` →
   `probe_account_sets`）照旧每次现读登记表。
3. **启动时先用缓存上报**：账套清单照旧用本机清单；**科目表也先用索引里上次读到的**（`account_probe.cached_chart`，不碰账套文件）→ 第一次心跳就是
   「1353 个 · 科目 133」，不再先报「科目 0」。
4. **所选账套优先**：后台扫描先读所选账套的科目表，再读所选账套所在的数据根，其余按原顺序。扫得慢（开始读账套 3 秒后还没完）就先交一份「进行中」快照
   （`partial`、`scanned_roots`），之后每 10 秒一份：心跳把读完的数据根换成新读到的、其余沿用上次报的（`projection_refresh.merge_partial`，清单不先缩水
   再长回来）；「进行中」快照照常上报，但**不算网页「重新读取」已完成、不存本机清单、不记新年度文件夹**，等完整快照。扫得快就只有完整快照，与原来一样。
   每交一份「进行中」快照先存一次索引（扫到一半关机，下次不白读）。
5. **网络盘断开 / 慢**（`scan_pace.HostGate`，新）：按服务器（`\\服务器\共享`、盘符）每轮先限时 5 秒问一次（只读 stat 这一轮第一个数据根，放在守护线程里，
   卡住不等、卡着期间不再问第二次）；超时或 Windows 报网络类错误（53、64、67、121、1231……）→ 这台的数据根这一轮全跳过（照旧不报它们），按 1、2、4、8、
   15 分钟退避，退避期内一次网络读都不做，每 60 秒的登记表检查也不碰它（所以也不会因为它反复整套重扫）；只是文件夹不在 / 没权限不算服务器不在。
   读到一半某份账套光读文件就超过 15 秒 → 这个数据根这一轮报空、这台服务器进退避。网页「重新读取」不等退避到点，马上再问一次。
6. **限速、让出 CPU**（`scan_pace.Pace`，新）：每份账套读完按本线程累计 CPU 让出时间，平时不超过 **15% 个核**（网页「重新读取」有人在等时 50%），
   读网络盘等着的时间也算，所以以读盘为主的扫描几乎不额外变慢。**不改 Windows 线程优先级**（`cb1805d0` 起；`54aeaa58` 曾降一档）：CC 的托盘、拉取任务、
   扫描共用一个 Python 解释器锁，降了优先级的扫描线程拿着锁时被别的程序抢走 CPU、长时间轮不到，CC 自己的托盘与拉取任务那条线就跟着等锁卡住
   （优先级反转）；「低优先级」由扫描自己让出 CPU 做到，最多占 15% 个核，其余留给别的程序。
7. **整盘找新年度文件夹** 不再每次启动都做：距上次满 24 小时才做（时间记在索引里，重启不重找）；网页「重新读取」照旧马上找。

### 缓存位置与失效规则

- 位置：`%APPDATA%\Pearnly\account_index.json`（skin3 是 `C:\Users\skin3\AppData\Roaming\Pearnly\account_index.json`）；本机清单
  `account_sets_<成员>.json` 照旧（只在完整快照时写）。
- 键是文件 / 目录路径（不分大小写），值连着读它时的（大小, 修改时间纳秒）：对不上就当没有、重读后覆盖；读的过程中文件又变了，下一遍对不上会再读。
- 30 天没用到的条目存盘时删掉；科目表只留最近用过的 8 份；文件坏了 / 版本不对 / 读不了 → 当空索引（重读一遍补回来），写盘先写临时文件再整体替换。
- 删掉这个文件是安全的：下次启动当作头一次，全读一遍。

### 取舍 / 限制

- 头一次（装新版后第一次启动、或删了索引）仍要每份账套读一遍；CPU 限在 15% 个核后，以 CPU 为主的老电脑上墙钟会比原来长（以读网络盘为主时几乎不变），
  期间心跳按数据根逐步换成新的。
- 「进行中」快照里还没读到的数据根沿用上次报的那份（最多旧一轮）；完整快照照旧以这一轮读到的为准。
- 服务器断开时它的账套照旧不报（如实）；退避上限 15 分钟：服务器回来后最多等到下一次再问（第一次失败 1 分钟，连续失败才拉长），网页「重新读取」马上再问。
- 每遍仍逐份 stat 两个文件、查可写、取文件号：1353 份约 5400 次网络元数据调用（没有 CPU 负担，主要是往返时间），为了文件号与「可写」是现在的，没再省。
- 优先级、网络类错误码、SMB 超时都是 Windows 上的行为，Mac 上用假的服务器 / 时钟测，**以真机复测为准**。

### 测试

| 命令 | 结果 |
|---|---|
| CC 桌面版全量 `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests`（companion/，Python 3.11 + PySide6 6.11.1） | 976 passed, 41 skipped（基线 963 / 41；新 `test_account_index.py` 13 条） |
| 新测试在改前源码（`b48534d0`，临时工作树，用完已删）上跑 | 当时的 12 条里 11 条失败、1 条通过（通过的是「推送前照旧现读登记表」，本来就该不变）：重启后 ISINFO 8 次（一份两次）、改了一份账套重启后 ISINFO 6 次 + ISPRD 3 次 + 登记表 + 科目表全重读、重启后第一次心跳科目表为空（`None`）、「重新读取」被进行中快照提前回完成、重启后又整盘找一遍、其余新模块不存在 |
| `test_account_index.py` 要点 | 首扫写索引、重启后一份 DBF 都不开且结果一样；改一份 ISINFO 只重读那一份、登记表变了只重读登记表与新账套；推送前照旧现读登记表；重启后第一次心跳就带上次的账套清单与科目表；所选账套所在的数据根先读、进行中快照带科目表、心跳合并不缩水、最终顺序照旧；进行中快照不回「重新读取」已完成、不存本机清单；服务器卡住只等 0.2 秒（测试值）、同一台只问一次、退避期内下一遍不卡也不再问；网络类错误退避 60 / 120 秒、文件夹不在不算；读到一半服务器没响应停在第一份、同服务器其它数据根退避；「重新读取」马上再问退避中的服务器；CPU 不超过 duty；24 小时内重启不整盘找、「重新读取」马上找；索引坏文件当空、30 天清理、科目表留 8 份、不含密钥 |
| 顺带修的一处（单独提交） | 加了新测试文件后，`test_visibility_watch.py::test_locking_stops_the_cover_checks_and_unlocking_rechecks` 在全量里稳定失败（单独跑通过）：前面用例留下的 `VisibilityWatch` 被 Python 循环回收清到一半（属性已清、Qt 对象还挂着过滤器）时，正好在 `show()` 里收到事件，`eventFilter` 读 `_window` 报错。`eventFilter` 改为属性已清就什么都不做（正常时行为不变）；之后全量连跑 5 遍都是 976 passed |
| 已有测试按新接缝改了两处 | `test_master_refresh.py` 断言扫描时多传 `pace`、`reuse_registry`；`test_write_dispatch.py` 身份字段那条改为桩登记表行（扫描不再经 `probe_account_sets`） |
| `python3.11 tests/test_companion_contract.py` | 69 tests OK（本机系统 python3 是 3.9，另有 2 条 `type | None` 语法错误，基线同样有，CI 是 3.12） |
| `python3 scripts/check-architecture.py` | PASS（0 violations） |

### Windows 上测试不过与修正（10-18，`6732fd2e`、`bfd69eaa`、`cb1805d0`、`3f59d646`）

推上去的 `902e71f1` 在 windows-latest 上 5 failed（Companion checks / companion-build run 39103496493），Mac 上全过。原因与改法：

1. **本机盘也被当成了服务器**：Windows 上本机路径 `C:\…` 有盘符，关卡把 `C:` 也当一台「服务器」去问。两条关卡测试里假的「卡住 / 不通」落到了本机数据根上
   （本机的账套整个没报）；真机上同理，老硬盘一时读得慢就可能把本机（通常是所选的那份）账套当成服务器不在、一分钟不报。改为只对 UNC 共享与
   `GetDriveTypeW` 不是固定盘 / 移动盘 / 光驱 / 内存盘的盘符设关卡；本机盘读一份账套很慢也只是慢，不判「服务器卡住」。另把 1201（记住的网络盘没连上）、
   1203（网络路径不对或网络不通）算作网络类错误；查盘符类型用自己的一份 kernel32、签名写明。
2. **测试里的假 UNC 数据根被真的去问了**：`test_master_refresh.py` 两条用 `\\server\books` 这类假数据根（扫描读盘几步都是桩），新关卡在 Windows 上真去
   `stat`，得到「找不到网络路径」、整台跳过。测试默认的关卡换成不碰网络的（conftest），专测关卡的用例自己放关卡与假的 stat。
3. **`test_window_process.py::test_closed_connections_are_released_in_the_tray` 是改前就有的不稳**：测试管道名用 `time.monotonic_ns()`，Windows 上 Python 3.11 的
   monotonic 是 `GetTickCount64`、15.625 毫秒才跳一次（测试机 3.11.9 只读实测：47 毫秒里只有 4 个不同的值）；前一条用例很快，两个用例拿到同一个名字，
   客户端连到上一条用例还开着的管道（日志「CC 窗口管道：口令不对，断开」）。改为每个用例一个随机管道名；正式的管道名按 Windows 用户名，一个用户一个托盘，不受影响。
4. **第二轮（`2f501e3d`，1 failed）`test_worker_payload_gate.py::test_slow_catalog_scan_never_blocks_heartbeat_or_polling`**：测试只在 `with` 里替换目录扫描，
   心跳一返回就换回真的；后台线程什么时候真去调由解释器排。原来新线程一启动就能调到；`54aeaa58` 在目录线程开头加了降优先级（Windows 上第一次要载入
   `kernel32`），新线程晚了几毫秒，调到的已是真的扫描。处理：去掉降优先级（见改法第 6 条，`cb1805d0`）；测试改为等后台线程调到桩之后才离开 `with`
   （`3f59d646`，断言不变）。

### 复测口径（新包由开发窗口用 `companion-build.yml` 从本分支打；按记忆以 skin3 普通权限重启 CC）

只读看 `C:\Users\skin3\AppData\Roaming\Pearnly\logs\companion.log` 的 heartbeat 行；CPU 用 `~/pearnly-devtools/dev-window/cc-idle-probe.ps1 -User skin3 -Seconds 30`
（或 `cc-thread-rhythm.ps1`）采后台进程（`--background`）按线程的 CPU。

1. **装新版后第一次启动**（本机还没有索引）：第一次心跳应是「上报账套 1353 个 · 可写 679 · 科目 0」（本机清单是 12:30 那份 1353；科目表这一次还没进索引）。
   扫描期间采 30 秒：扫描线程应在 **15% 单核以内**（原来约 17%、连续 5 分钟）；记下从「托盘常驻」到第一条「科目 133」、到「账套清单读完：N 份」（若有让出 CPU）
   与心跳变成完整清单各用了多久。扫完后确认 `account_index.json` 在（只看大小，约 0.7 MB）。
2. **再重启一次**（重点）：日志应有「本机账套索引：578 个账套文件夹、17 个登记表」（测试机 test4 实测 578，见下节；test4 包里这行写作「578 份账套」）「先用上次扫到的账套清单 1353 个」「先用上次读到的科目表 133 个」，
   **第一次心跳就是「1353 个 · 可写 679 · 科目 133」**；完整快照预计在第一、二次心跳之间就到（几秒到几十秒，主要是 5400 次网络元数据往返）。
   重启后马上连采两次 30 秒：后台进程应只有短暂的小峰，之后回到约 0.3%。不应再出现连续几分钟的工作线程占用。
3. **网络盘断开**（可选，要人在机器前拔网线或断 Wi-Fi）：下一次心跳 Accserver 的 1252 份不再报（只剩 101 份），日志「服务器 … 读不到，60 秒后再试（连续第 1 次）」，
   之后不应每分钟都卡几十秒；接回网络后最多等到下一次再问（1–15 分钟）就回到 1353，或在网页点「重新读取」马上回来。
4. 不在 S: / Accserver 上写任何文件：本次只读（stat、只读打开 DBF），复测时也只看日志与本机文件。

### test4 真机两个疑问（10-18 14:07–14:12）的核查

测试窗口回报：test4（`8e094e6d`）上以 skin3 不带参数再启动一次 companion.exe，托盘没记「打开 CC 窗口（窗口进程）」（14:07、14:08、14:12）；
14:09 结束全部 CC 后不带参数启动，没有进程、没写日志；另问重启日志「本机账套索引：578 份账套」为什么不是 1353。只读核查（日志、进程、管道、事件日志、
电源，没改机器上任何东西）：

1. **578 是对的、存全了**：本机清单 1353 行里只有 679 行的账套文件夹在这台电脑上够得到（可写 679），这 679 行是 **578 个不同的账套文件夹**
   （几个年度文件夹的登记表登记了同一个文件夹，例如 `64EXP\63EXP` 与 `63EXP`）；索引里 578 条、登记表 17 个、够得到的账套没有一个不在索引里。
   其余 674 行的文件夹在这台电脑上不存在（没有 ISINFO / ISPRD 可读，原来报的详情也是空的），没有可存的。日志改为「本机账套索引：578 个账套文件夹、
   17 个登记表」，免得与清单的「账套 N 个」混淆。
2. **再启动一次打不开窗口：同一份程序在干净的 Windows 上复现不出来**。test3 → test4 的改动不碰启动路径（`main.py`、`single_instance.py`、
   `instance_channel.py`、`gui_tray.py`、`window_link.py`、打包都没变）；新加的打包启动冒烟（`packaging/smoke_launch.py`，Companion checks 与
   Companion test build 打包后都跑）在 windows-latest 上用打出来的 `companion.exe`、全新 %APPDATA% 照用户的用法走：`--background` 起来 →
   再不带参数启动一次（自己很快退出，已在跑的托盘记「打开 CC 窗口（窗口进程）」）→ 全部结束后不带参数启动（托盘起来并打开窗口），
   run 39107465423 两段都通过（产品代码与 test4 相同）。测试机上托盘这时也在正常听两条本机管道（`PearnlyTomo-skin3`、`PearnlyCCWindow-skin3` 都在），
   主线程状态与 test2 / test3 时一样。
3. **测试机那几次启动多半根本没跑起来**：测试机 **12:26:25 起一直用电池**（系统日志 Kernel-Power 105；现在 PowerLineStatus Offline、68%）；
   test3 那次成功（12:26:06）在换成电池前 19 秒。Windows 计划任务默认「用电池时不启动」（登记时没加 `-AllowStartIfOnBatteries` 就一直排队，删掉任务
   就什么都不留，见记忆 office-windows-test-pc）；同一批 14:07 以 skin3 计划任务做的 LockWorkStation 也没生效。测试机任务计划程序的运行日志没开、
   进程创建审计没开，没法直接证明，**请测试窗口复测时**：任务登记加 `-Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries
   -DontStopIfGoingOnBatteries)`（或插上电源），删任务前先看 `Get-ScheduledTaskInfo` 的 `LastTaskResult`（267011 = 从没跑过），再按 test4 的
   两步做；能请用户在机器前双击桌面图标一次最直接。若插电 / 任务确实跑了还复现，带上那一刻的 companion.log 与 `Get-Process companion` 回来。

## 第 7 批：lab 迁移与回读（10-18）

交接 D3「迁移后回读」。对象是测试机 lab（cowork 栈）上的 testcowork：`lab-start`（`PEARNLY_TESTENV_REFRESH=1`）从**线上 testcowork** 重新导出导入，
挂上本分支实时源码后迁移。lab 镜像 `49cbba82…`，与线上 cowork-web 同一份（线上 revision `afc615bb`，10-18 04:18 UTC 起运行）。
只读做法：`pearnly-testenv-cowork-cowork-web-1` 里 `bench --site testcowork.pearnly.com mariadb`，会话设成只读事务，只跑 SELECT；没有 exec 进 mariadb 容器。
唯一的写操作是第 4 项按交接要求重跑一次 `bench migrate`。线上对照值只取了一次：一次 SSH，在 `pearnly-production-cowork-web-1` 里对 testcowork 跑同样的只读 SELECT
（10:38 UTC）。线上 testcowork 是旧结构，补丁只到 `v0_0_18`。测试机上的临时脚本用完已删。时间口径：Patch Log、各表 `creation` / `modified` 是站点时间（UTC+7），
密钥 `last_seen_at`、登记 `registered_at` 是 UTC。

### 第一次迁移失败与 5db1462d

第一次 `migrate` 跑完了 `v0_0_19_companion_devices`（Patch Log 17:28:36），到 `v0_0_20_express_account_sets` 时报 AttributeError。原因是账套登记、可达、账套资料这三张表
共用连接的 `ConnectionDocument.before_validate`，而这个守卫无条件写 `self.client`，这三张表没有 `client` 字段。`5db1462d` 改成只在有 `client` 字段的 DocType 上补空值，
另加 `tests/test_cowork_connection_controller.py`。重跑后 `v0_0_20`–`v0_0_23` 在 17:31:28–29 执行完。`v0_0_20` 失败那次的写入随补丁事务回滚，重跑后没有残留：
可达行全部对得上登记，孤儿行 0。

### 回读数字（迁移刚完成时，以及重跑 migrate 前后）

| 项 | lab（迁移后） | 线上 testcowork（迁移前） | 判定 |
|---|---|---|---|
| Patch Log `v0_0_19`–`v0_0_23` | 5 个都在，每个只有 1 条 | 没有（到 `v0_0_18`） | 通过 |
| 工作空间 | 1 | 1 | — |
| 连接 `config_json` MD5 / `record_revision` | MR.ERP `CWEC-283edc03…` `b0df1f51…` / 2；Express `CWEC-0a40b958…` `c7f74141…` / 5 | 两条都相同 | **不变** |
| 使用名单 MD5、`creation` | 相同 | — | 不变 |
| 连接 `modified` | 迁移刚完成时与线上相同（Express 16:46:13）。17:37:30 起被 CC 桌面版 1.7.0 的心跳更新，`config_json` 仍不变 | — | 补丁没碰 |
| 有效密钥 | 2 把（Express 1、MR.ERP 1），都挂上了电脑；密钥行（名、`active_key`、吊销时间）的 MD5 与线上相同 | 2 把 | `active_key` 不变 |
| 电脑 | 2 台，都没退役：`CCD-63fbcf64…`「DESKTOP-MN2BRKR」（就是办公室测试机），以及 `CCD-3eae81e6…`「MR.ERP」 | 没有电脑表 | 见下面第 2 条发现 |
| 账套登记 | 1 073 份（Express 连接）。其中 1 070 份来自目录，3 份是定时任务引用、目录里已经没有的键 | 上报清单 `reported_account_sets` 1 353 行 | 合理（1 353 行里有重复路径） |
| 可达行 | 1 070 行，都在 `CCD-63fbcf64…`，状态全部 `ok`，可写 578 | — | 与 CC 桌面版「578 个账套文件夹」一致 |
| 账套资料 | 6 份（1 份 routine），都带来源电脑 | — | — |
| 推送记录（全是证据 v1） | 159 张：success 141、manual 17、cancelled 1；整表 MD5（名、状态、版本、账套键）与线上相同 | 159 张，相同 | 不变 |
| v1 排队中（prepared + pending）/ 领取中 / 结果不明 | **0 / 0 / 0** | 0 / 0 / 0 | 达到目标 0 |
| 推送尝试 / 写者锁 | 147 次全部 finished；锁 13 行（account 11、endpoint 2），没有持有者 | 相同 | 不变 |
| 定时任务 `reader_device` 回填 | **10 / 10**；`rule_revision` / `enabled` / `reader` 的 MD5 与线上相同 | 10 条（9 条启用） | 回填了，任务版本没升 |
| 定时任务 `company` 回填 | **1 / 10**（那 1 条是停用的任务） | 没有这一列 | 见下面第 3 条发现 |
| 运行 / 文件 / 公司文件夹 | 运行 19 条都是 sent，没有待发简报的，回填 0；文件 145 行按设计不补；公司文件夹 1 / 1 已回填 | 19 / 145 / 1 | 符合补丁口径 |
| 按需请求 | 1 张 done，`target_device` / `account_key` 仍为 NULL（补丁只处理 requested / leased） | 1 张 done | 符合 |
| `all_members` | Express 主连接 `CWEC-0a40b958…` = 1；MR.ERP = 0 | 没有这一列 | 通过。testcowork 只有 1 条 Express 连接，「后建连接不改」这一条在这里没有对象，要在生产 erpcowork 上核（D2 预期：只有 `9f8ad3b2` 设 1） |
| 记住的选择、公司主档、用户 | 90、4、3（启用 2） | 相同 | 不变 |

### 重跑 migrate（幂等）

10:41:38–10:41:47 UTC 重跑一次 `bench --site testcowork.pearnly.com migrate`，退出码 0，一个补丁都没执行，Patch Log 没有重复行。紧挨着重跑前后各回读一遍，
上表所有计数、连接 / 密钥 / 推送 / 定时任务的 MD5，以及另外 5 个 MD5（登记、可达、账套资料、任务的电脑与公司、电脑的选择与版本）都完全相同。
前后唯一的差别是 Express 密钥的 `last_seen_at`（心跳）。**结论：幂等。** 限制：Frappe 按 Patch Log 跳过已执行的补丁，所以这次重跑证明的是迁移整体幂等、
不会重复执行。补丁代码自身重复执行的幂等，仍以本机测试为准（各批测试里都有重跑断言）。

### Error Log 与报错

- Error Log 从 17:20 起 **0 条**。导入带来的旧记录 137 条，最后一条是 10-15 15:48，与线上相同。第一次迁移的 AttributeError 是命令行报错，不进 Error Log。
- web、worker、long-worker、scheduler 四个容器从这次启动（10:34 UTC）起的日志里 `Traceback` 都是 0；Scheduled Job Log 从 17:20 起没有行。

### 点名「迁移回读时一起看」的两项

- `readers.moved` 同表子查询（`Cowork Company Folder` 的 `SELECT name … AND company IN (SELECT company FROM 同一张表 …)`）：用不存在的值在 MariaDB 11.8 上
  照原样跑，返回空；执行计划是半连接 `FirstMatch`，外层走 `reader` 索引，内层走 `company` 索引，没有报 1093 之类的错误。删除是先 SELECT 出名字、再按名字删，
  不是同表子查询里删。`UPDATE` 的执行计划在只读事务里跑不了（1792），没看。
- `member_choices`（`access.assignees` 同一条 SQL）：2 人，按全名排序，没有空全名的。第二个测试成员还没加（用户 3 个，启用 2 个），加了之后人数会变成 3。

### 发现的问题与建议

1. **切域名后，测试机上旧的 CC 桌面版进程仍在给线上 testcowork 报到**：线上那把 Express 密钥的 `last_seen_at` 到 10:36:59 UTC 还在更新，lab 上停在导出那一刻
   10:25:34。测试机上 CC 在 17:37:28 重启之后，17:37:30 起才报到 lab：电脑被认领（有了设备密钥摘要），`full_report_due` 清掉，整份重报以后登记仍是 1 073 份、
   可达仍是 1 070 / 578。原因多半是 CC 的长连接还连着线上源站，没跟着 DNS 切过去（测试机现在解析到 Cloudflare，到线上 IP 的连接为 0）。线上 testcowork 这期间只多了
   报到时间，配置与版本都没变，不影响这次对照。**建议**：`lab-start` 切完域名后重启测试机和笔记本上的 CC 桌面版，再开始测，写进第 7 批的步骤里。
2. **给 MR.ERP 连接的密钥也补了一台旧电脑**：`v0_0_19` 不分连接种类，所有有效密钥都补电脑。MR.ERP 那把密钥 10-13 签发后从没报到，所以电脑名退到了连接名「MR.ERP」，
   设备密钥摘要为空。「电脑」列表不列没在线、没报过版本的电脑（`rules.computers`），用户看不到它，不算缺陷。等那台电脑用 1.7.0 在 MR.ERP 上 `device_hello` 认领，
   或者吊销这把密钥时由 `retire_orphan` 退役。生产 erpcowork 上 MR.ERP 没有密钥（D2），不会出现这种情况。
3. **9 条启用的定时任务没补上公司，而且指向没有电脑够得到的账套**：这 9 条只指向 3 份账套，都是 `C:\Account\70EXP` 下的 DEMO 账套。测试机现在上报的目录里没有它们，
   池里只作为「被引用的键」登记，没有可达行，没有属性，所以 `company_of` 认不出公司，按补丁口径留空（照旧推往固定账套）。这不是迁移缺陷。但这 9 条一旦到点运行，
   推送会因为没有电脑能写而变成 `push.no_writer`。其中 3 条的账套正是公司主档 `rsr95oj3p1` 建档时用的那份。**建议**：第 7 批做定时任务联调前，由测试窗口决定
   这 9 条测试任务是停用，还是改指测试机现在的账套（`C:\PearnlyExpress\70EXP`）。另外，只读回读看不出它们会不会在联调时被触发。
4. 没有发现会挡第 7 批或部署的问题。生产 erpcowork 部署后的回读仍按 D2 的预期做：电脑 2 台、`reader_device` 回填 0、`all_members` 只设在 `9f8ad3b2`、
   两条 Express 连接 `record_revision` = 3 且 `config_json` 不变、v1 排队中 / 领取中为 0。

没做 / 没验：并发加锁与 1020、HTTP 链、两台真机联调（第 7 批其余各项）；`readers.moved` 的 UPDATE / DELETE 执行计划；补丁代码自身的重跑（见上面的限制）。

## 第 7 批：测试中发现与修复（10-18 晚，lab，测试窗口实测）

| 条目 | 现象（lab 实测） | 处理 | 提交 |
|---|---|---|---|
| B6 排队回复 | 录制机关着、新成员的单排队，CC 回「已录入「นิภา…」的收据……目前状态是已排队」，前后矛盾 | 排队说明写明还没入账、回复开头也不说已录入；B8 复测：「已送入……目前状态是已排队，还没有确认写入 Express」 | dc9ff711 |
| B8 名称 | 中文排队回复写「CC Desktop」 | Windows 端正式名称这句从 cowork_companion_status 的说明挪进每轮都带的 GUIDANCE（仍只说一次） | 19a00519 |
| B8「已在线」 | 两台都退出了，CC 说「CC Desktop 已在线」 | 不改：测试用 Stop-Process 直接结束进程，没走托盘退出（托盘退出会发下线，gui_tray.py signal_offline），3 分钟内按在线算 | — |
| B7 待处理页死路 | 转账凭条停在「待选账套」，选账套后推送停在 document_policy_needed_payment_evidence，服务端放回「待选账套」，换哪份都一样；页面把码原样标红 | 被「录不录」挡下时选的账套照收、原因记成要选录不录，转成一般待处理（交给 CC / 复核页）；待处理页显示词条的话（reason_label）。复测通过 | 953a5a4c |
| B7 CC 选录不录存不上 | cowork_choose_document_policy 的 document 没说明，CC 交了「transfer slip」「payment_evidence」，两次存不上，卡片退回列英文参数 | document 写明是等这个选择的那张单的记录名、不是类别。复测通过：卡片中文「转账凭条 不录入」，保存成功 | c4efee5b |
| B7 选了以后单还挂着 | 选「不录入」保存后，等这个选择的凭条仍在待处理问同一句，要靠 CC 记得再推 | 选完把这个租户里停在「这类录不录」的单一并了结：不录入记 document_policy_skip_*，待处理不再列；录入清掉原因等人推。复核页同一处（history.document_policy.choose） | 本节提交 |
| B7 范围说法 | 录不录按租户（工作空间）存，文案与 CC 说「本公司」 | 范围按设计不变（用户 09-26 Cowork 不按公司隔离、10-07 按租户选一次）；文案去掉「本公司 / for this company」，CC 工具说明改成整个工作空间、所有公司 | 本节提交 |

验到的：B4 新成员的活由录制机写、B5 本人的活由测试机写、B6 录制机退出时排队不被抢、回来自动写完；B8 真并发服务端回读（泰国时间）：B706 录制机 18:11:18.109 领、18:11:28.963 回执，B707 测试机 18:11:32.821 才领（第一张回执后 3.9 秒）、18:11:33.704 回执，同一账套键、写者锁放开；D13 定时任务的票由新成员在手机补选，仍由读文件夹的测试机写（HP691018-009），任务结果仍计入定时任务；D12 插拔电保持唤醒切换正常。

### 改已有文案（用户 10-18 晚「涉及到文案的今晚特批，但是不用有口语」）

范围是整个租户，「本公司」会被读成某一家客户公司，所以去掉；问录不录的两条按闸门（出错最多 2 句）收成两句。

| 来源·键 | 角色 | 中文 旧 → 新 | English | ไทย | 日本語 | 标记 | 登记 |
|---|---|---|---|---|---|---|---|
| pearnly_cowork·intake.error.document_policy_needed_payment_evidence | 出错 | 此单据为转账凭条。本公司是否录入此类单据？选择后，同类单据… → 此单据为转账凭条。选择是否录入，所有同类单据将按此处理 | This is a transfer slip. Ente… → This is a transfer slip. Choose whether to enter it; every document of this kind follows the choice | เอกสารนี้เป็นสลิปโอนเงิน บริษ… → เอกสารนี้เป็นสลิปโอนเงิน เลือกว่าจะบันทึกหรือไม่ เอกสารประเภทเดียวกันทั้งหมดจะดำเนินการตามนี้ | これは振込明細です。この会社でこの種類を入力しますか。一度… → これは振込明細です。入力するかを選ぶと、同じ種類のすべてに適用されます |  | 未登记 |
| pearnly_cowork·intake.error.document_policy_needed_withholding_certificate | 出错 | 此单据为预扣税凭证（50 ทวิ）。本公司是否录入此类单据… → 此单据为预扣税凭证（50 ทวิ）。选择是否录入，所有同类单据将按此处理 | This is a withholding tax cer… → This is a withholding tax certificate (50 Tawi). Choose whether to enter it; every document of this kind follows the choice | เอกสารนี้เป็นหนังสือรับรองการ… → เอกสารนี้เป็นหนังสือรับรองการหักภาษี ณ ที่จ่าย (50 ทวิ) เลือกว่าจะบันทึกหรือไม่ เอกสารประเภทเดียวกันทั้งหมดจะดำเนินการตามนี้ | これは源泉徴収証明書（50 ทวิ）です。この会社でこの種… → これは源泉徴収証明書（50 ทวิ）です。入力するかを選ぶと、同じ種類のすべてに適用されます | R-LEN↑ R-PAREN | 未登记 |
| pearnly_cowork·intake.error.document_policy_skip_payment_evidence | 出错 | 本公司不录入转账凭条，此单据不推送 → 转账凭条不录入，此单据不推送 | This company does not enter t… → Transfer slips are not entered; this one is not sent | บริษัทนี้ไม่บันทึกสลิปโอนเงิน… → ไม่บันทึกสลิปโอนเงิน จึงไม่ส่งเอกสารนี้ | この会社では振込明細を入力しないため、送信しません → 振込明細は入力しないため、送信しません |  | 2026-10-18 docs/work/2026-10-15-multi-desktop-build.md |
| pearnly_cowork·intake.error.document_policy_skip_withholding_certificate | 出错 | 本公司不录入预扣税凭证（50 ทวิ），此单据不推送 → 预扣税凭证（50 ทวิ）不录入，此单据不推送 | This company does not enter w… → Withholding tax certificates (50 Tawi) are not entered; this one is not sent | บริษัทนี้ไม่บันทึกหนังสือรับร… → ไม่บันทึกหนังสือรับรองการหักภาษี ณ ที่จ่าย (50 ทวิ) จึงไม่ส่งเอกสารนี้ | この会社では源泉徴収証明書（50 ทวิ）を入力しないため… → 源泉徴収証明書（50 ทวิ）は入力しないため、送信しません | R-PAREN | 2026-10-18 docs/work/2026-10-15-multi-desktop-build.md |

### 改已有文案：CC 桌面版连接提示（C10 ② / C11，10-18 晚，同上特批）

1.7.0 起没有配对码，电脑被移除或成员被收窄后回设置点「连接」；池里的电脑不属于某一份账套，连上时不再写账套名。

| 来源·键 | 角色 | 中文 旧 → 新 | English | ไทย | 日本語 | 标记 | 登记 |
|---|---|---|---|---|---|---|---|
| companion·log_paired | 未知 | 配对成功，账套：{company}。 → 已连接 Pearnly。 | — | จับคู่สำเร็จ ชุดบัญชี: {compa… → เชื่อมต่อ Pearnly แล้ว | — |  | 未登记 |
| companion·off_auth | 未知 | 配对码已失效，请重新配对 → 连接已失效，请在设置中重新连接 | — | รหัสจับคู่ใช้งานไม่ได้แล้ว กร… → การเชื่อมต่อใช้งานไม่ได้แล้ว กรุณาเชื่อมต่อใหม่ในการตั้งค่า | — |  | 未登记 |

### 改已有文案：电脑对单据失去权限（C10 ③，10-18 晚，同上特批）

电脑被移除、成员被收窄后，排在那台电脑上的单转人工时待处理显示的原因；原文「队列的访问权限」是内部说法。

| 来源·键 | 角色 | 中文 旧 → 新 | English | ไทย | 日本語 | 标记 | 登记 |
|---|---|---|---|---|---|---|---|
| pearnly_cowork·companion.queue_access_revoked | 未知 | CC 桌面版队列的访问权限已撤销。 → 此电脑已不能处理该单据 | Access to the CC Desktop queu… → This computer can no longer handle this document | สิทธิ์เข้าถึงคิวงานของ CC เดส… → คอมพิวเตอร์เครื่องนี้ไม่สามารถดำเนินการเอกสารนี้ได้แล้ว | CC デスクトップのキューへのアクセス権が取り消されました。 → このコンピューターではこの書類を処理できなくなりました |  | 未登记 |

### 第 7 批结论（10-18 20:05，lab，multi-desktop 至 4d3a55e0 / 网页包 index.R9AeXN5K.js，桌面版 test6 = 7f89fdaf）

- **通过**：A1–A3（两台上线、经 P: 认成同一份账套，按身份键 101 份共有、0 不一致）；B4–B8（谁的活谁的电脑做、本人的活本机写、电脑退出排队不被抢且回来自动写完、写者锁按物理账套互斥：第二张在第一张回执 3.9 秒后才领）；B7 / D13（定时任务的票由别人补选仍由读文件夹的电脑写）；C10 ①⑤（读者换电脑时已排的单仍等原电脑、回来只写一次；离线时主档选项照常）；C10 ③（收窄名单后被收窄的人排在那台的单转人工 companion.queue_access_revoked，待处理计数 +1 对得上）；C10 ② 移除（密钥立即 401、列表少一行）；C11 rejoin（新电脑记录、心跳恢复）；D12（保持唤醒开关、拔插电、托盘退出放开）；D13（录制机合盖 19:59:37 进现代待机、20:01:26 醒，计划任务 RecKeepAwake / RecAgent 不在）；E 段多人共看 / 升规则 / 删规则；F（测试机首页 4.9%、被挡 / 最小化 / 锁屏暂停与恢复、关窗约 3 秒退、后台约 0.3% / 25–31 MB；录制机被动 1.5%）。
- **未测**：C9 与旧电脑改挂（两台都已不是 1.6）；C10 ⑥ 询问框卡住写入线程（要人在电脑前）；E 段「用不了的连接」一侧（只有一条连接，代码已核：只删本人可用连接上的记忆）、分类映射不问费用科目、忘掉历史写文件不跟事务。
- **当晚修的**：见上「测试中发现与修复」表，另加 0d896cd4 合入 main（首页小猫只播几轮的修复此前没进本分支）、4d3a55e0（桌面版连接提示去掉「配对」、定时任务写接口死锁重试）、本节提交（电脑对单据失去权限的原因文案）。登录页动画只播一次在 main 05ff9e28，随部署后量。
- **用户待定**：定时任务换读文件夹的电脑时，已排着的单要不要转给新电脑（现在不转）。
- 识别花费：第 7 批 7 张，全晚约 11.02 美元（上限 50）。

## 第 7 批后续 D9 / D10 / D11（10-19，分支 `pearnly/multi-desktop-followups`，起点 `pearnly/batch-1019b` 60184389）

提交：`38a8bcd8` fix（推送记录队列索引 + 补丁 `v0_0_25`、register 加锁顺序、测试）、`072b9663` test（五个实验室脚本）、`7944c73b` test（并发压测脚本）、本条 docs。

D10（T1 测试机验证按第 3 期口径重写）见上「T1 跨年度换账套」一节的「测试机验证（D10，10-19 按第 3 期口径重写…）」，没跑（要测试机）。

本机环境（D9 / D11 共用，只本机 docker，不连生产 / 测试机 / 甲骨文）：`infrastructure/compose/cowork-backend-lab.yml` 的 bench 镜像、内存、
CPU 与数据库连接上限改成可由环境变量指定（默认值不变），用线上同一份镜像
`ghcr.io/skin306152-star/pearnly/frappe@sha256:bcb5e240…`（MariaDB 11.8、Redis 6.2 同生产），本任务单独的 compose 项目
`pearnly-multidesk-lab`（不碰共享样板栈 `pearnly-sample`）：

```sh
export PEARNLY_COWORK_LAB_IMAGE=sha256:bcb5e240bb75b88dfaa584911fc0d6456f37cba0ade0421c59e770a38ad63b8e
export PEARNLY_COWORK_LAB_BENCH_MEM=2560m PEARNLY_COWORK_LAB_BENCH_CPUS=4 PEARNLY_COWORK_LAB_DB_CONNECTIONS=120
LAB="docker compose -p pearnly-multidesk-lab -f infrastructure/compose/cowork-backend-lab.yml"
$LAB up -d --wait db redis bench
$LAB exec -T -e PEARNLY_COWORK_LAB_SITES="cowork-backend-companion.localhost cowork-backend-load-1.localhost cowork-backend-load-2.localhost cowork-backend-load-3.localhost" \
  bench env/bin/python /workspace/scripts/cowork-backend-lab-init.py        # 空白站点，约 25 分钟（Mac 上 amd64 镜像走转译）
```

### D11 五个实验室脚本按证据 v2 重写

五个脚本都只写实验站点里带合成标签的数据，不连 Express / 测试机 / 任何外部服务——整份都是「离线部分」，没有要连真 Express 的段
（连测试机的是另外的 `cowork-windows-lab-relay.py` / `cowork-windows-test-client.py`，不在本次范围）。跑法：

```sh
for f in cowork-companion-collector cowork-companion-heartbeat cowork-ack-effects cowork-preparation-confirm cowork-companion-transport; do
  $LAB exec -T -e PEARNLY_COWORK_TEST_SITE=cowork-backend-companion.localhost bench env/bin/python /workspace/scripts/$f-lab-verify.py
done
```

结果（10-19，线上镜像 bcb5e240、空白站点 `cowork-backend-companion.localhost`，本分支源码只读挂载）：collector 18、heartbeat 26、ack-effects 13、
preparation-confirm 18、transport 40，**共 115 项全过**（改写前 0 / 18、22 / 26、0 / 13、0 / 18、0 / 40）。改服务端（下面 D9 两处）之后又全跑一遍，仍 115 全过。
`tests/test_cowork_folder_computers.py`（10-16 改断言后本机没跑过）在线上镜像的离线容器里跑：8 OK。

怎么改的（都是夹具与断言按现在的口径，产品代码不为脚本改）：

- 上报一律走真实心跳入口 `heartbeat._receive`（原来手搭 `plan_report` + `collector.ingest`，缺 `member`）；collector 只把「此刻」换成用例给的时间。
  账套行带 6 个科目（CC 桌面版 `account_probe` 也报在每一行上）；准备时明确给账套（连接配置不再存所选账套）；付款方式与科目像会计在复核页选过一样记进
  账套记忆（现在的推送要先定这两项）。
- 证据 v2 的断言：主档、存货主档数在账套资料（`pool.facts`）而不在连接的上报记录；上报不改连接配置、不升版本、不改 `report_credential`；
  换钥不再让排着的单转人工、换钥前的准备照常确认，新钥报到后照领；能不能确认看 `has_writer`（签发人停用即没有能写的电脑）；
  确认失败回 `failed`、整笔回滚（夹具退到保存点）；日期过了一天确认不了，但「这份账套正在重读主档」不再挡确认；银行窗口按录入人。
- 领取 v2：每份账套只看排头（失效的排头这一次转人工、同账套下一张下一次领到）、任务多 `attended`（旧槽位电脑没有登录记录，为 False）、锁口径 v2
  只有账套锁（合成登记没有内容键，所以没有公司锁，原来是 2 把）。
- 失权：Cowork 不按人分公司（用户 2026-09-26），删工作空间用户权限不再是失权；改成收回录入角色（改使用名单会连带改连接版本，那是「目标变了」）。
- 刷新租约 v2：整目录读是每台电脑一份子租约、每台同时只有一份租约；池里没有电脑够得到的账套的读请求失败（`ERR_ACCOUNT_SET_UNAVAILABLE`）；
  空名单只清这台电脑自己的可达行、不把目录标错；旧槽位换钥后新钥是另一台「旧电脑」，在同一轮里领自己的子租约，旧钥的租约它交不回、也不判失败。
- 复核工作簿跟着录入人的界面语言（合成用户英文），断言改成本语言的「成功」。
- 基础脚本 `cowork-push-application-lab-verify.py` 的工作空间税号固定，长期保留的实验站点第二次跑就撞「税号已被使用」：建之前把上一轮同名合成工作空间停用。
- 没在本次范围、仍按 v1 的：`cowork-push-application-lab-verify.py`（18 项里 12 项失败）、`cowork-push-native`、`cowork-companion-credentials`、
  `cowork-connections` 这几个基础脚本本身的用例（五个脚本只借它们的夹具）。

### D9 并发压测（10-19）

脚本 `scripts/cowork-companion-concurrency-lab-verify.py`（`setup` / `history` / `jobs` / `run` / `report`，说明在文件头）。3 个租户 = 3 个站点
（`cowork-backend-load-{1,2,3}.localhost`），每家 1 个管理员 + 10 个成员、一条 Express 主连接；每个成员用 register v2 登记自己的电脑、`device_hello`
认电脑、经真实心跳报 8 份共享盘账套（同文件号同网络路径 → 池里同一份登记，十台都够得到）+ 2 份本机账套；每人 30 张手工票经真实准备 + 确认成为排队单
（七成在共享盘账套上，互相抢写者锁）。租户 1 另有第二条 Express 连接，第 1 台电脑用旧槽位密钥 + `device_hello` 挂上去（`attached`，同一台电脑），
那条上另有 10 张它的单。每个租户另克隆 2 万张已成功的旧推送记录（只占行数）。

压的时候起与生产 Cowork 网页同形的 gunicorn（6 进程 × 4 线程 gthread、`--preload`），30 台电脑各一个线程走真实 HTTP，节奏照 CC 桌面版 1.7：
每 3 秒领一次（带 `index_pending`）、领到就「写」2 秒再回执成功、每 60 秒心跳（第一次整份、之后「没变」）、每 15 秒问一次按需请求；每个成员每 30 秒
打开一次对话页（register v2，比真实频繁）；压到一半管理员经网页接口移除租户 3 的两台电脑（它们照常在发心跳、领任务）。服务端计时与计数只加在本实验
gunicorn 的 `post_fork` 里（数据库错误码、几类加锁读、排头查询、领取分派、register / remove 耗时），产品代码不为它改。不调识别、不起真 CC 桌面版。

```sh
for n in 1 2 3; do $LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py setup cowork-backend-load-$n.localhost --history 20000; done
$LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py run --seconds 240
for n in 1 2 3; do $LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py report cowork-backend-load-$n.localhost; done
# 再压一轮前：jobs <站点>（被移除的电脑由人点「连接」重新加入、每台再排 30 张）
```

**环境限制**：Apple Silicon Mac 上跑 amd64 线上镜像（转译，bench 4 核 / 2.5 GB），MariaDB 11.8 原生但限 1 核 / 512 MB、缓冲池 128 MB；生产是 8 核、
MariaDB 不限核、缓冲池 4 GB。绝对值偏慢（客户端时延里还有 gunicorn 排队），看前后对比与服务端耗时。

发现两处问题，复现、定位、按类修了（下表「修前」是第 2 轮、「修后」是第 3 轮，两轮条件相同，各 240 秒；第 1 轮客户端没处理服务端关空闲连接、线程中途退出，作废）：

| 指标（3 租户 × 10 台，每家 2 万张旧记录） | 修前 | 修后 |
|---|---|---|
| 排头查询 `Q_HEADS` 执行计划 | 推送记录整表扫两遍（`type ALL`，估 1.4 万行，临时表 + 排序） | 走新索引 `connection_status_created_index`（`ref`，只读排队中的几百行） |
| `Q_HEADS` 耗时（服务端，压测中） | p50 395–430 ms，p95 500–574 ms | p50 1.3–1.7 ms，p95 2.9–3.1 ms |
| `Q_HEADS` 耗时（空闲，`report`） | 2 万行 19.4 ms；10 万行 1121 ms | 2 万行 1.66 ms；10 万行 1.14 ms |
| 领取（`transport.lease`，服务端） | p50 1.15–1.23 s，p95 1.32–1.38 s | p50 99–106 ms，p95 199–218 ms |
| 其中挑单（`_claim`，含排头与 `route_contracts.decide`） | p50 ≈1.0 s | p50 87–93 ms；`decide` 每次 p50 0.08 ms、p99 0.12 ms |
| 连接行加锁读等待（`Cowork ERP Connection` FOR UPDATE） | p95 2.57–3.72 s，p99 3.77–4.91 s，最长 7.1 s | p95 90–120 ms，p99 252–325 ms，最长 2.5 s |
| 归属行加锁读（`Cowork Workspace`） | p95 1.0–1.18 s | p95 ≈1 ms |
| register（对话页每 30 秒打开一次，每家 20 次 / 分钟） | 212 次里 205 次 500（死锁重试三次仍失败） | 239 次里 238 次 200（含被移除电脑的 8 次 `removed`），1 次 500（见下「剩下的 1020」） |
| InnoDB 死锁 | 618 | 0 |
| InnoDB 行锁等待 | 2853 次，合计 3778 s | 2601 次，合计 351 s |
| 写成（240 s） | 179 / 190 / 188 张 | 846 / 440 / 486 张（租户 2、3 队列已写空） |
| 移除电脑（压测中，客户端） | 8.8 s、10.1 s | 128 ms、230 ms |

第 4 轮（修后再跑、加锁读按表细分）：死锁 0；1020 共 8 次，全在 register 对 `Cowork Express Agent Credential` 的加锁读，都在重试里成功，register 239 / 239；
领取客户端 p50 38–75 ms、p95 82–251 ms；连接行加锁读 p95 17–48 ms；心跳服务端 p50 26 ms；按需请求每家 40 次 / 分钟，客户端 p50 21 ms；
移除 155 / 180 ms。

四轮压完 `report` 读回各租户全部排队单（三家合计 3513 张写成）：同一次尝试没有交付两次、没有一张交付过两次、没有一张成功两次、没有一张被别人的电脑写
（谁录的谁的电脑写）；租户 2 有 1 张「结果不明」转人工——第 1 轮那台模拟电脑领走后线程退出没回执（客户端缺陷，同真电脑写到一半断开），是预期处理。

问题 1：**排头查询整表扫推送记录、而且在锁着连接行的时候**。推送记录表只有 `name` 等索引，没有 `connection` / `status`，`Q_HEADS`（与领取前收回到期租约
`_recover`）每次都扫全表；它在领取锁住连接行之后跑，同一条连接上的电脑轮流等这把锁。10 万行时一次 1.1 秒，10 台每 3 秒领一次就超过这把锁的承载
（每秒要锁 3.3 秒）。修法：推送记录加组合索引（连接、状态、建立时间），排队中 / 领走的只是一小部分，按这两列开头只读它们；账套键 500 字符放不进
3072 字节的索引上限，不放（按账套分组在排队中的几百行里做）。原生 `on_doctype_update` 建（新站点），补丁 `v0_0_25_push_queue_index` 给已有站点补
（同 `v0_0_11` 的做法，已有同名索引跳过）；三个压测站点经 `bench migrate` 跑过补丁，删索引后再跑补丁会补回。

问题 2：**register 与领取 / 心跳的加锁顺序相反，互相死锁**。认证（`credentials.revalidate`）先锁连接行、再按当前读核归属行（`access.require_scope` 的
共享锁）；register 一进来先锁归属行（为了「两台同时登录不会各建一条 Express 连接」），再锁连接行——反序。对话页每打开一次就 register，十台电脑在领取，
九成 register 死锁三次后 500，等待中的领取与心跳也被拖慢（归属行的排它锁挡住所有人的共享锁）。修法（`companion.credentials.register`）：连接已在时
不锁归属行，顺序与别处一致（连接 → 密钥 → 电脑）；只有要新建本站 Express 连接（管理员、还没有）时才锁归属行，锁上后按当前读
（`connections.service.main_connections(current=True)`，共享锁）再核一次别人刚建好的，快照隔离下撞 1020 由 `api._local_transaction` 整段重来。
测试：`tests/test_cowork_cc_desktop_devices.py` 新增两项（连接已在时四种登记都不锁归属行、不做当前读；新建时先快照读、再锁归属行、再当前读，之后的登记不再锁），
70 OK；`test_cowork_companion_register.py` 在线上镜像里 10 OK。

剩下的、没改的：

- **1020（快照隔离下的加锁读）**：修后每轮 8–9 次，全在 register：它先不加锁地读主连接（建立了读视图），再加锁读这台电脑那把密钥行，而那一行正被同一台
  电脑的心跳改报到时间（`record_seen`）。三次重试里成功的占绝大多数，第 3 轮有 1 次三连撞、回 500（对话页下次打开再登记）。要根除得让心跳不每次改
  密钥行、或 register 在第一次一致性读之前就锁住密钥行，牵涉报到时间口径，没动，写在这里。领取时 `_locks`（写者锁）的加锁读：修后两轮 0 次 1020；
  修前那一轮的计数钩子没认出 Frappe 包了一层的错误码，没有分表的数，但领取函数一次都没抛过死锁 / 1020（被包着重试的也会经过计时钩子记下），那一轮的
  618 次死锁都在 register。
- **一台电脑挂两条连接**：服务端「每台一件」按整台电脑不加锁地看（ADR-021 第 16 条），两条连接各自锁自己的连接行。模拟成两个取任务线程同时领时，
  修后两轮各有 1 次同一台电脑同时拿着两条连接上各一件（第一条连接一直有活时，第二条上的单照样在写：两轮各写成 13、17 张）。真 CC 桌面版是单写者、各成员轮流
  领一次（`companion/src/companion/multi_profile_worker.py`），写完回执才轮到下一条，所以不会同时两件；服务端这一侧仍是已登记的已知口径，没改。
- **移除与心跳并发**：移除 128–230 ms 完成，那两台随后的领取 / 心跳回 401（各 1–2 次后停），对话页再登记回 `removed`（8 次），没有死锁或 1020；
  它们没写完的单留在排队（谁的活谁的电脑做，别人不代写），与设计一致。
- 生产推送记录的实际行数没查（不连生产）；修前的耗时随行数涨（2 万行 19 ms、10 万行 1.1 s），所以这个索引越早上越好。
- 这是服务端模拟：没有真 CC 桌面版、真 Express 写入时延、WSL / 办公网络；生产 8 核下的绝对值要部署后在测试租户压测时再量。

## 人推撞上写者锁 1020（10-20，测试窗口 lab 实测，分支 `pearnly/batch-1019b`）

现象：待处理页连着快速推单、测试机 CC 桌面版同时领单写账，5 张直接报 1020「Record has changed since last read in table 'tabCowork Push Writer Lock'」，
隔几秒再点都成；其中 1 张再点先报 `push.native_target_changed`。原因：人推那笔事务先做了一致性读（读视图），准备完到 `reserve` / `confirm` 的
`_locks` 加锁读写者锁行时，这一行已被 CC 桌面版的领单 / 回执改过并提交（快照隔离，上面「剩下的 1020」同一类）；Frappe 把 1020 与 1213 都抛成
`QueryDeadlockError`，`assistant.tools._push_one` 一路没接住。撞在确认那一步时准备已提交，再点时请求号随 `last_push_record` 换了，拿旧准备去确认
就是那一次 `native_target_changed`。修法（按类，一处）：`push.application.conflicts.retried`——整笔回滚（不是回保存点：1020 要新的读视图）、
退避 0.1 / 0.2 / 0.4 秒 × 0.5–1.5 随机后重来，一共 4 次（依据：D9 压测领单服务端 p50 约 100 ms、p95 约 200 ms，合计约 1 秒、短于 3 秒取单间隔；
原先 3 次不等、register 有过三连撞），用尽给已有正式原因 `push.native_lock_conflict`，不露 SQL 原文、不加新文案。复核页的推送 RPC
（`push.application.api._local_transaction`）、Express 确认发送（`companion.submission.confirm`）、CC 工具与待处理页共用的 `_push_one`
（准备到提交一笔、确认一笔，各自重来；确认只重来确认）都经它；被回滚的那次什么也不留，重来同一请求号，记录按请求号唯一、只排一次。
测试 `tests/test_cowork_push_conflicts.py`（9 项）与 `tests/test_cowork_assistant_tools.py` 新增 3 项；没在真 MariaDB 上复现、待测试窗口照原步骤再推。

## 电脑侧短事务撞锁整笔重来（10-20，分支 `pearnly/register-lock-retry`，起点 origin/main 1c98ffa0）

背景：上面「D9 并发压测」第 4 轮的 1020 共 8 次，全在 register 对 `Cowork Express Agent Credential` 的加锁读（register 先不加锁地读主连接、
建立了读视图，再加锁读这台电脑那把密钥行，而这一行刚被同一台电脑的心跳改过报到时间 `record_seen`）；第 3 轮有 1 次三连撞、回 500。
人推已经有 `push.application.conflicts.retried`（上一节）。电脑侧各入口原来各写一个「不等、一共三次」的循环，按需请求一个都没有。

提交：`4107fa20` refactor（纯搬移，行为不变）、`0a2590b4` fix（各入口改经同一处 + 测试）、本条 docs。

### 一个机制、放在哪

- 机制从 push 包搬到新的共用叶子 `pearnly_cowork.transaction.conflicts`（只依赖 Frappe 的数据库，不依赖任何业务域；架构文档域表加一行，
  `architecture-policy.json` 登记 `transaction: []`、`push → transaction`、`companion → transaction`）。内容不变：撞上 1020 / 1213
  整笔回滚（不回保存点：1020 要新的读视图）、退避 0.1 / 0.2 / 0.4 秒 × 0.5–1.5 随机、一共 4 次，用尽抛不带数据库原文的 `Exhausted`。
  fix 里加一条：撞锁后整笔回滚本身失败抛 `RollbackFailed`，立刻停、不再重来、不挂原异常（原来只有领单 / 回执这样做）。
- `push.application.conflicts` 保留原入口，只定人推用尽时给 `push.native_lock_conflict`；复核页、Express 确认发送、CC 工具的调用方不改。
- 为什么不两套：电脑侧和人推撞的是同一类锁（写者锁行、连接行、密钥行、电脑行），对面也是同一批事务（领单 / 回执 / 心跳 / 登记）；
  次数、退避、回滚、提示清理放一处，以后调一处，不会出现「人推 4 次带退避、register 3 次不等」这样各说各的。

### 入口与用尽时给什么（沿用各自原有的失败形状，不加文案）

| 入口 | 原来 | 现在 | 用尽时 |
|---|---|---|---|
| 电脑登记 / 移除电脑（`companion.api._local_transaction`：`register`、`remove_device`） | 3 次不等；用尽抛原来那个异常（带 1020 与表名原文） | 共用机制 | 照旧 `QueryDeadlockError`（500，对话页下次打开再登记），不带数据库原文 |
| 心跳（`heartbeat.report`） | 3 次不等 | 共用机制；每次尝试一个保存点，上报格式错照旧只退回保存点、不重来 | 照旧回「忙」（`http_auth.raise_busy`，503 `companion.report_busy`），CC 桌面版忽略、下一轮再报 |
| 领单 / 回执（`queue_api._run`） | 3 次不等；回滚失败回「忙」（阶段 `<操作>_rollback`） | 共用机制，每次照旧重验派发 / 回执能力，提交仍在重来之外 | 照旧回「忙」；回滚失败照旧 `lease_rollback` / `ack_rollback`、只领一次 |
| 认电脑（`device_api`：`hello`、`leave`） | 3 次不等 | 共用机制；`CredentialError` 照旧退回保存点、回 `{ok: false, code}`、不重来 | 照旧回「忙」 |
| 机器认证（`http_auth._authenticate_request`，心跳 / 领单 / 回执 / 文件夹 / 按需请求 / 认电脑共用） | 3 次不等 | 共用机制 | 照旧回「忙」（阶段 `authenticate`） |
| 按需请求（`request_api`：`next_request`、`complete_request`） | 没有重试：两台同时领同一件撞 1020 直接 500 | 共用机制（结果分块按 offset 覆盖写，重来写的是同一块） | 回「忙」，与别的机器方法一样（CC 桌面版对非 2xx 本来就这一轮不做、下一轮再来） |

被回滚的那次不留东西：整笔回滚丢掉这一笔里的写，Frappe 的 `db.rollback` 同时清掉排在提交后的回调与入队；被回滚那次弹过的提示也去掉。

没改的、原因：

- 文件夹机器方法（`folder_intake.machine` 的 `sync` / `begin` / `finish` / `workbook_file`）：鉴权后先提交、不锁连接行和写者锁；`begin` 要取原件
  直传地址、`finish` 落收件箱文件并叫后台推进，有外部 IO，整笔重来要逐项审，不在本次；压测里也没出现过。
- `credentials._atomic` / `transport._atomic` 里的 `except QueryDeadlockError: raise` 不是重试，是把冲突原样交给外层整笔重来，保留。
- 其他域（对账、识别、盘点、主档等）的人用接口各自的三次循环不是电脑侧，不在本次范围。
- 根因没动：register 先一致性读再加锁读密钥行、心跳每次改密钥行，仍然会撞，只是由重来吸收；根除牵涉报到时间口径（上面「剩下的 1020」）。

### 测试

- 新增 `tests/test_cowork_companion_lock_retry.py`（14 项，进 CI）：register 与 remove_device、心跳、领单 / 回执、认电脑、按需请求各自——
  1020 / 1213 前 1–3 次失败第 4 次成功（回滚次数、退避、只留成功那次的写）、4 次都失败（register 是不带原文、不挂原异常的
  `QueryDeadlockError`，机器方法回「忙」且记下的是 `Exhausted`）、非锁错误不重来（权限错、上报格式错退回保存点、`CredentialError`）、回滚失败
  立刻停；以及「同一处」：六个入口都经 `transaction.conflicts.retried`、companion 里不再有自己的重试循环、人推也经它。旧代码上跑这份测试 10 项失败。
- 本机离线：`test_cowork_push_conflicts` 9、`test_cowork_assistant_tools` 222、`test_cowork_cc_desktop_devices` 70（重来模拟改成 4 次）、
  `test_cowork_companion_requests` 38、`test_companion_contract` 76、`test_companion_receiver` 5、`test_cowork_companion_http_auth` 21、
  `-credentials` 17、`-queue_http_contracts` 16、`-queue_contracts` 18、`-reports` 23、`-release` 3，全 OK；架构检查 PASS。
- 生产镜像 `dd360538…` 的离线容器（`--network none`，本分支源码只读挂载）：`test_cowork_companion_register` 10、`-liveness` 4、`-reports` 23、
  `-legacy_wire` 3、`test_cowork_folder_computers` 8、`test_cowork_companion_lock_retry` 14，全 OK；改过的模块在镜像里全部能导入。
- 实验室脚本里按「3 次」断言的四处改成按共用的 `ATTEMPTS`：`cowork-companion-credentials`、`-heartbeat`、`-queue-http`、`-queue-ack-http`
  的 `-lab-verify.py`（只编译过，没在实验站点跑）。
- 没在真 MariaDB 上复现：需要测试窗口在 lab 照 D9 压测复测（下面）。

### 待测试窗口在 lab 复测

在本分支的工作树里跑（compose 按相对路径只读挂本工作树的 `apps/`、`scripts/`、`tests/`），环境同上「第 7 批后续」：

```sh
export PEARNLY_COWORK_LAB_IMAGE=<线上镜像 digest，部署前取当时的>
export PEARNLY_COWORK_LAB_BENCH_MEM=2560m PEARNLY_COWORK_LAB_BENCH_CPUS=4 PEARNLY_COWORK_LAB_DB_CONNECTIONS=120
LAB="docker compose -p pearnly-multidesk-lab -f infrastructure/compose/cowork-backend-lab.yml"
$LAB up -d --wait db redis bench
# 站点没有时先建（约 25 分钟）：见上「第 7 批后续」的 cowork-backend-lab-init.py 一行
for n in 1 2 3; do $LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py setup cowork-backend-load-$n.localhost --history 20000; done
# 已经压过一轮的站点改跑：jobs cowork-backend-load-$n.localhost
$LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py run --seconds 240
for n in 1 2 3; do $LAB exec -T bench env/bin/python /workspace/scripts/cowork-companion-concurrency-lab-verify.py report cowork-backend-load-$n.localhost; done
# 改了次数断言的四个实验室脚本
for f in cowork-companion-credentials cowork-companion-heartbeat cowork-companion-queue-http cowork-companion-queue-ack-http; do
  $LAB exec -T -e PEARNLY_COWORK_TEST_SITE=cowork-backend-companion.localhost bench env/bin/python /workspace/scripts/$f-lab-verify.py
done
```

看 `run` 打出的 JSON（也写在 `sites/cowork-backend-load-1.localhost/private/concurrency-run.json`）：

- `client.<站点>.register.status`：没有 `500:`（D9 第 3 轮有 1 次）；被移除的电脑仍是 `200:removed`。
- `server.<站点>.db_errors` 里 `1020:lock:tabcowork express agent credential` 可以照旧有几次（根因没动），`raised` 里
  `register:QueryDeadlockError:1020` 是被重来吸收的那几次；它们的和不该再变成 register 的 500。
- `client.<站点>` 的 `heartbeat`、`lease`、`ack`、`next` 没有 5xx；`innodb_delta` 死锁仍为 0；领单服务端 p50 / p95 与第 4 轮（约 100 / 200 ms）
  相比没有变慢（重来最多多等约 1 秒，只发生在撞锁的那几次）。
- `report` 照旧：同一次尝试没有交付两次、没有一张成功两次、没有被别人的电脑写。

验收：上面五条都满足、四个实验室脚本全过，才算这项在真 MariaDB 上通过；生产 8 核下的数要部署后在测试租户再看。

### D9 复测、对照与第二轮修复（10-20）

开发窗口在本机实验栈 `pearnly-multidesk-lab`（新建的四个站点，线上镜像 `dd360538…`，挂集成工作树 `batch-1020`）照上面的命令复测：load-2 的 register
有 1 次 500，死锁 6 次，领单客户端 p95 300–390 ms（第 4 轮是 82–251 ms），四个实验室脚本里三个失败。为分清是不是本改动引起的，同一个实验栈、同一台
机器上交替跑了改动前（origin/main `1c98ffa0`，临时工作树）与本分支，每轮之前 `jobs` 重排单，`--force-recreate bench` 改挂对应工作树：

| 轮次（各 240 秒，3 租户 × 10 台） | register 500 | register 撞 1020（次） | InnoDB 死锁 | 领单客户端 p95（三家，ms） | 领单服务端 p95（ms） |
|---|---|---|---|---|---|
| 对照 C1（改动前） | 2 | 9 | 6 | 327 / 357 / 353 | 184 / 188 / 184 |
| 对照 C2（改动前） | 2 | 15 | 4 | 326 / 297 / 297 | 177 / 174 / 175 |
| 开发窗口复测（`batch-1020`，含 `0a2590b4`） | 1 | 14 | 6 | 390 / 307 / 344 | 192 / 182 / 174 |
| M1（本分支 `ac997b6c`） | 0 | 14 | 7 | 366 / 361 / 319 | 190 / 175 / 181 |
| M2（加第二轮修复） | 0 | 1 | 0 | 309 / 360 / 416 | 182 / 181 / 195 |
| M3（同 M2，再跑一轮） | 0 | 0 | 3 | 281 / 244 / 318 | 170 / 162 / 188 |

结论：

- **p95 与第 4 轮的差距不是改动造成的**：改动前的代码在同一个栈上也是 297–357 ms（服务端 174–188 ms），第 4 轮是另一个实验栈（镜像 `bcb5e240`、
  站点压过几轮）。改后两轮 244–416 ms，与对照同一量级；服务端 p95 改后 162–195、对照 174–188。
- **死锁不是改动造成的**：对照两轮各 6、4 次，全是 load-1 的 `1213:ws_lock`、被领单的重试吸收（领单全 200）。来源：领单在 `credentials.revalidate`
  →`access.require_scope` 里对归属行（`Cowork Workspace`）加了共享锁，之后 `push.native.service._dispatch_target` 为核本公司税号又对同一行
  `for_update=True` 加排它锁——共享锁升排它锁。同一条连接上的领单先排在连接行锁后面，不会同时；只有 load-1 有第二条 Express 连接，两条连接上的
  领单各拿各的连接行锁、同时持有归属行共享锁、又都要升排它锁，就互相死锁。这是推送账本里已有的写法，本次没改（修法：那一句改成共享锁的当前读，
  核税号只要挡住改税号的人；涉及推送账本，单独评估）。
- **load-2 那次 500 是 4 次都用尽**：Redis 里待写的错误日志（实验栈没有调度器，`Error Log` 延迟写入还在队列里）只有这一条：`PC-07` 的
  `companion.api.register` → `_local_transaction` 末尾抛的 `QueryDeadlockError()`（不带原文），压测计数这个站点 register 撞 1020 共 6 次，其中 4 次是
  它。原因是加锁顺序以外的一个口子：register 的读视图在**等连接行锁之前**就建好了（请求自己的会话读、`_device_connection` 的快照读），等锁那几百
  毫秒里同一条连接上别的电脑心跳改了自己的密钥行（报到时间），拿到锁后 `_find_receipt` / `_latest` 扫这条连接的密钥行就撞 1020；重来时照样先建
  读视图、再等锁、再撞，所以退避也救不了。改动前也一样（对照每轮 2 次 500，只是 3 次就停）。
- **第二轮修复**（`60d9e999`）：`credentials.register` 在只读没写、马上要锁连接行时调调用方给的 `before_lock`，`api.register` 传的是提交（结束本请求的
  读视图，同 `ocr.execution.service` 结束读快照再锁；`credentials` 自己仍不提交，新建连接的那一次不提交），`_use` 改成先锁连接行再做签发人的当前读。
  之后的读视图建在拿到连接行锁之后，而这条连接的密钥行 / 电脑行只有拿着连接行锁的人改，register 撞 1020 从每轮 9–15 次降到 0–1 次，500 没有了。
- **load-3 的心跳 401、领单 401** 是压到一半被移除的两台电脑，对照轮同样有（领单 401 × 2），属预期（移除后它们的密钥被吊销，对话页再登记回 `removed`）。

实验室脚本（`cowork-backend-companion.localhost`，全量输出看过每个 traceback）：

| 脚本 | 对照（改动前） | 改后 |
|---|---|---|
| `cowork-companion-heartbeat` | 26 OK | 26 OK |
| `cowork-companion-queue-http` | 16 项全部出错 | 16 OK |
| `cowork-companion-queue-ack-http` | 24 项全部出错 | 24 OK |
| `cowork-companion-credentials` | 2 失败 1 出错 | 同样 2 失败 1 出错（同三项） |

- 两个队列脚本全部出错是夹具过期，与改动无关：D11 起 transport 夹具的 `setUp` 先经真实心跳报到、再记付款方式与科目（`report` / `choose`），这两个脚本借
  setUp 却没借这两个方法（第一项 `AttributeError`，之后各项在建合成用户时连锁报 `PermissionError`）。补上之后又露出三类断言：写者锁口径 v2 只有账套锁
  一把（原断言两把）、领取 v2 多给 `attended`——都照 D11 transport 脚本的口径改了；还有两项是**本改动的真问题**，已修（`d61b33f5`）：共用重来原来
  只去掉被回滚那次的 `message_log`，没还原 `frappe.flags.error_message`，原来各入口的 `clear_private_messages` 会清它——心跳认证重来后回复的
  `_error_message` 里带出了配对码。现在共用重来把两路原生提示都还原成调用之前的样子。另外回滚失败那项按调用帧找 `queue_api._run` 里的回滚，现在
  回滚在 `transaction.conflicts.retried` 里，改了找的帧（`801efbec`）。
- `credentials` 的三项是 v1 口径（D11 记录里已列为没改的基础脚本）：按 ADR-021 D1 / D2，录入角色也能给自己的电脑发密钥、Express 池默认全所可用，
  「把人从名单去掉就不能发 / 管理员以外不能发」的断言不再成立；对照同样这三项，不是本改动造成的，没改。
- 正确跑法：脚本跑的是 bench 当前挂的那份源码，复测哪个分支就 `--force-recreate bench` 挂哪个工作树，脚本从同一个工作树的 `/workspace/scripts` 跑；
  看全量输出（`| tail -4` 只看得到计数）。

实验栈跑完保持开着，bench 已改回挂 `batch-1020`。
