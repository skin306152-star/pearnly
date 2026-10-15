# 多电脑方案施工记录（T5，2026-10-15 夜起）

- 方案：[多电脑方案](2026-10-13-cc-multi-desktop-plan.md)（D1–D9 用户 10-15 全部按建议拍板）。
- 分支 `pearnly/multi-desktop`，工作树 `.claude/worktrees/multi-desktop`，基线 `origin/main` 7c9bb445。
- 补丁编号用 `v0_0_19_*`（`v0_0_18_history_push_targets` 已占）；ADR 用 0021。
- 合入规矩（方案第 9 节）：批 1–4、6 攒成一次服务端发布，批 5（CC 桌面版）在服务端上线后发正式版；真机验证与用户试用通过前不合入 `main`。
- 证据层级：第 0 批只有只读核验；实现、本地闸门、部署、真机验收在后面各节登记。

## 最终验收场景（用户 2026-10-15 夜原话）

「我想要的使用场景：一个主 pearnly 号：添加成员，成员在自己的电脑自己的账号使用 cc 和 pearnly，每个人有每个人的独立账号，在自己的电脑使用，exp 还是依赖公司主体」。

对应方案：主账号在「团队管理」加成员（各自独立账号、角色）；成员在自己电脑用自己账号登录 CC 桌面版即加入事务所的 Express 连接（D1，不用配对码、不顶替别人的电脑）；
Express 仍是事务所一条连接（公司主体），账套按客户公司与物理位置认定，谁的电脑写都落同一本；谁录的单由谁的电脑写，关机时常驻或在线同事电脑代写（D3、D7）；
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
- 刷新改派（`S/connections/refresh.py`）：`lease_device`、`parent_request` 两个新字段；账套级按发起人自己的电脑 → 常驻 →
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
