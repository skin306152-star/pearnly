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

前端待办（等 `pearnly/cc-shell-rebuild` 合入后在外壳里做；Desk `intake-folders.js` 同理）：

- 任务表单 / 列表 / 主档页任务行：`company` 非空时在账套处显示「跟随公司当年账套」（账套名只作兜底），停下原因按三个原因码的词条显示；
  表单不传公司（服务端自己定），也不要加选项开关。
- 运行记录与任务结果显示 `results.account_sets`（实际推往的账套名与张数）。
- 待处理页对 `account_set_*` 停下的票：当场选账套（现在待处理页只按票上的去向选科目等，没有换账套的入口）。
- 主档页「定时任务」现在仍按 `account_set_key` 归到公司（`companies.service.tasks_of` / `task_overview` 未改）：任务存下的那份账套从目录里消失后
  会归不上，外壳重做时可改成优先认 `company`。

测试机验证计划（未执行；测试机 Express 账套写入要先列给用户确认）：

1. 起环境：`PEARNLY_TESTENV_SRC=<本工作树> PEARNLY_TESTENV_IMAGE=<线上 digest> bash scripts/testenv.sh lab-start cowork`（已起就用
   `testenv.sh live cowork`，表结构变了自动迁移）。迁移后只读回读：已有 Express 任务的 `company` / `company_branch` 回填数，对不上的留空。
2. 账套：`C:\PearnlyExpress\70EXP` 里同一公司的 `T7SINCER` / `T8SINCER` / `T9SINCER`（C 盘可直接写；局域网 Accserver / S 盘不写）。
   先在 testcowork 的公司页看三份的会计期间（T8、T9 都是 2025）；主档页「＋」用 T9 建一个任务（或用回填过的旧任务）。
3. 造跨年单据，省钱先用 POS 导出（销项任务收 Excel / CSV，不识别、不扣费，一天一张票）：同一份导出里放 2024-12-30、2024-12-31、2025-01-02 三天。
   - T8、T9 都在：2024 的两天进 T7SINCER；2025-01-02 停下「多份账套覆盖单据日期」。
   - 把 `C:\PearnlyExpress\70EXP\T8SINCER` 暂时改名（目录刷新后 T8 不在目录里），复跑：2025-01-02 进 T9SINCER；测完改回原名。
   - 一张 2026 年的单：停下「没有覆盖单据日期的账套」。
   - 停下的票在网页 / CC 手动选账套重推，任务默认值照样套用。
   照片 / PDF 收据各一两张走识别（会扣费）要先问用户。
4. 核对：Express 里各张单在对应年度的账套（经 CC 回读或 Express 界面，只读）；票上「推送去向」与 CC 说的账套名是实际那份；运行记录 /
   任务结果的 `account_sets`；完成简报写「待选账套」；结果表格的推送状态列。
5. 够不到：在第二台电脑或把这台 CC 桌面版的账套列表去掉 T9 后跑一张 2025 单，停下「没有电脑能访问单据日期所在的账套」；整台断开时是
   「电脑没连上」稍后重推，不是停下。
6. 收尾：`testenv.sh lab-stop cowork` 切回线上；T8SINCER 改回原名；测试写入的单据按用户意见留或删。

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

- 办公室测试机以前靠常驻模式保持在线（记忆「10-12 起保持 CC 常驻」）。升级 1.7.0 后屏幕一关就进待机，远程测试会断——要用户在 Windows 电源设置里改「从不睡眠」，
  这是系统设置，要用户自己点。
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
