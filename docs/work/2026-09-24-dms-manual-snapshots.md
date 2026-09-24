# DMS 手动建缓存、交互读本地、提交查实时

## 换窗口总交接（2026-09-24，ERP 发布与测试环境更新后 · 新窗口从这里开始）

**当前是就地交接，DMS 新方案尚未上线，也尚未在新 Windows 环境跑完真实闭环。** 用户上一条要求按刚更新的测试环境/流程试验 DMS，随后要求先写交接、换窗口继续。本轮只核对本地 Git、文档、脚本和既有 Mac 容器，更新本节；没有启动新的测试、切换测试租户、调用 OCR、写第三方 DMS 或发布服务。下面历史批次仅在需要细节时查；与本节不一致的旧基线/预览存活状态不再当作当前事实。

### 1. 从哪里接、哪些版本不能混淆

| 对象 | 本次交接回读 / 已收到的发布证据 | 状态 |
| --- | --- | --- |
| 本任务工作树 | `/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly`，分支 `codex/dms-five-minute-snapshots` | 就在这里接；交接前 HEAD `3f5306fa764f76ff9ed10827198012c6ae0b883a`，本节提交后 HEAD 会前进，业务实现不变 |
| DMS 分支共同基线 | `57039ca15595641932f053fd34c49d4e45dba964` | 尚未合入后来的 ERP 发布与 Windows 测试环境改动 |
| 本地主线 | `/Users/skin/pearnly`，`main=90b5cb31bdda546466e44791772fb342688aaa7e`，工作区干净 | 已合入 Windows 测试环境/流程；比本次真实远端回读领先 7 个提交，不能把它说成已推送 |
| 真实远端 main | `3cee765cc89fe6c41434060248619be07743da94` | 本轮 `git ls-remote` 回读；DMS 新分支远端仍不存在 |
| ERP 当前发布 | 运行源码 `695325f18a02dc3294dc239352c00fba77c0993e`；镜像 `sha256:56f9011b6ab2ef633a92f9f37942d77ebca01121367c7084bfed97572545cd23` | ERP 窗口已交付；`3cee765` 只比运行源码多发布记录，本轮未再次连接生产复验 |
| DMS 既有发布 | 源码 `dd81a3f009e255c2facbb8da818cd92a98face30`；镜像 `sha256:a11eb9e36bae8ca16e3dc37cbda45915fd1ccf18d4b28714c21d235fbff4934f` | ERP 窗口回传 DMS 7 容器 ID/启动时间/镜像未变；本任务新方案没有进入该镜像 |

ERP 已解除本轮 main/共享部署文件协调冻结。ERP 发布记录在主线 `docs/work/2026-09-24-erp-purchase-menu-diagnosis.md` 第七轮；其证据为 7 个 ERP 服务一致、77 个 App 变更文件及 19 个公开资源逐一匹配、原生迁移成功、默认 POS 入口已显示官方组件。旧 POS 差异根因是新版仅通过 `?pearnly_pos_next=1` 开启，普通菜单仍走旧界面；昨日 POS 提交实际已经包含并部署，ERP 本次已修默认入口。不要重新把这件事当成 DMS 待修问题。

旧 DMS 工作树 `/Users/skin/.codex/worktrees/dms-line-full-flow-fix/pearnly` / `codex/dms-line-full-flow-fix` 仍在 `57039ca`，远端同名分支也在该 SHA。该树**不是干净树**：只有 `docs/work/2026-09-24-dms-line-full-flow-fix.md` 有未暂存修改（60 行新增、5 行删除），记录 sale02 续测及旧轮询/电话/OCR/编辑保存诊断，无未提交业务代码。原样保留，不能 reset、覆盖或把这份历史记录当作新方案已验收。

### 2. 用户已定下的方案与本地实现

- 采用**手动建缓存、交互读本地、最终提交查实时**。五分钟全量轮询/每日地址轮询已被手动方案替代；分支名沿用旧名，不代表还在实施五分钟自动扫描。
- 连接页按钮为“更新数据”，四组选项为“车辆主档信息、客户资料、地址目录、公司资料”；执行时原生全屏等待，中文“数据更新中...”（三个 ASCII 句点）。不展示采集后的字段、数量或进度详情。
- 四组按原生完整目录、分页和详情读取，完整验证后独立发布；失败保留旧完整代际。精确表/字段/附表范围见 [协议记录](2026-09-24-dms-snapshot-protocol.md#四组主档范围与完整表单核对同日手动方案追加)。车辆含停用车型、所有颜色、活动/赠品/贷款附表；公司含组织、员工非凭据业务资料、银行/融资及订车选项；不把交易流水或员工密码混进主档。
- LINE 菜单 1/2 的查找、选项、编辑、预览均走本地资料；缺缓存不能偷偷回退现场全量读取。最终客户写入/订车仍实时复核，保留账号互斥、身份/顾问/权限、差异确认、幂等与未知结果恢复。成功客户写后只回填已确认的一条，不需要每次重新手动刷新全库。
- 菜单 3 是真实第三方登录，菜单 4 管本人保存的 DMS 凭据，菜单 5 显式刷新本人的当前查询；这些必要的实时动作不在“全部交互离线”承诺内。业务恢复调度仍保留，旧主档轮询入口不能重新派发扫描。
- 首次绑定不会自动建四组完整缓存。绑定连接仅校验有限主档；管理员仍需点“更新数据”确认。首次没有成功组时四组默认全选，打开弹窗本身不开始采集。
- “全国地址”指 DMS 原生的**泰国全国**省/区/镇/邮编，非全球地址。变更日志方案只做了只读可行性调查，尚未采用或实现增量/删除/游标；当前仍按所选组完整读取。

主要本地提交：`880fd04` 四组完整主档；`410b87b` 清理旧读取依赖、补顾问/提交校验及 OCR 分段计时；`c9ee279` 等待文案和单次 OCR 记录；`3f5306f` 原生使用记录调查。完整前序实现和验证保留在下方，不重新迁移或重写这些已完成工作。

### 3. 已有证据与尚缺的闭环

- 离线/原生 Site 层已有主档完整性、断点续读、独立发布、旧包保留、权限/换密码拒绝、交互零外呼、客户冲突与幂等、菜单 5 条件查询、真实 RQ 序列化及 UI 等待生命周期验证；本地架构检查通过。具体数量绑定下方各批版本，**不能直接算作合入新主线后的候选通过**。
- 第三方只读层：车辆与公司完整主档/附表已实际读全；客户 40 条、每条 65 字段及最终计数/ID 复核通过。地址只核了总数 15,729 条及各级完整表单样本，**全国详情整轮和四组首次真实缓存发布未做**。
- 一次真实 OCR 已用仓库合成附件完成：15/15 字段匹配，1 次模型调用，扣费 1.50 THB，总服务耗时约 8.276 秒，其中模型/容量段约 7.124 秒。它走的是既有线上 OCR，不是新 Windows 候选，更不是 LINE 入站到回卡的全链路延迟/P95；详细参考号与分段见下一节。单次授权已执行完，不能据此自动重跑多次收费脚本。
- DMS 原生使用记录可见新增/修改/删除，但没有已证实的完整覆盖、稳定增量游标或统一内部对象 ID，故不能承诺靠日志即可无遗漏增量更新。调查不等于增量功能上线。
- 尚缺：新版在实际测试租户上的安装/升级与后台专道、四组完整采集发布、LINE 菜单 1–5（含编辑保存回卡和订车付款附件）、同权限第三方回读与失败恢复。历史旧代码真 LINE 成功不能替代候选验证。
- 旧分支全仓 strict i18n 曾有 487 条发现（377 登记基线），这是历史存量检查结果；未扩大忽略、未宣称全绿。新主线可能已有变化，接手按受影响检查回读，不机械沿用数字或重复已无变化的全部套件。

### 4. 刚更新的 Windows 测试流程：下一窗口先读这里

新权威入口目前在**本地主线**，本 DMS 树尚未包含：

1. `/Users/skin/pearnly/docs/development/test-environment.md`
2. `/Users/skin/pearnly/docs/work/2026-09-24-windows-testenv.md`（优先最后“一键切换测试租户”节，前文早期阻塞已部分被后文取代）
3. `/Users/skin/pearnly/scripts/testenv.sh`、`scripts/testenv-cloudflare.sh` 与 `infrastructure/testenv/` 实际入口。

该环境为 `lenovo-office-lan` 上的 Windows/WSL2 Ubuntu 24.04 amd64，部署目录 `/opt/pearnly-testenv/`；已导入 `testdms.pearnly.com` 与 `testerp.pearnly.com` 整站测试数据，保留原域名/渠道/登录回调。命名隧道 `pearnly-testenv`，身份仍由线上 Keycloak 处理。Windows 任务记录报告域名切换/切回和 HTTP 冒烟通过，**未报告真实登录、真实 LINE 和手机拍照验收**；本交接没有登录设备或重新读回 DNS，当前路由/设备镜像需接手时确认。

本次阅读脚本确认的接手注意点：

- `lab-start` 固定导出、导入并切换 **DMS 和 ERP 两个测试租户**；不是只切 DMS 的开关。`lab-stop` 只切回两个域名，不把测试机数据库回写线上。下一窗口先核两测试租户当前使用情况和测试范围，不能把它当无副作用探测命令。
- `testenv.sh check/ps/logs/smoke` 等不少子命令也先调用 `sync`，会投递当前工作树的运行定义。先读调用链；需要纯只读状态时不要误认为这些包装命令零写入。`reset` 会删测试数据卷，不能作为普通排错步骤。
- 文档称切换后“线上容器照常在跑，只是没有流量”。DNS 切走不自动停止已入队任务、恢复发送或菜单协调；同一 OA、同一第三方 DMS 账号的双环境活动必须核对。独立 Redis 锁不覆盖另一个运行环境。本次只登记待核点，没有新增双写实测或修改旧环境。
- 测试文档同时记录“整站复制 site_config/渠道凭据”和“计费未接、OCR 占位空凭据”；不能由页面可打开推断收费/OCR 链已经可用，也不能由文档一句“不会收费”推断所有导入配置已隔离。真实 OCR 前核对凭据、额度/计费与共享容量策略；保持故障显式失败，不用假余额或镜像消息冒充真实结果。
- 当前脚本/文档的基础镜像证据为旧 `a11eb9e`；ERP 已有独立新镜像，DMS 新缓存代码仍只在本分支。用旧镜像测试只能证明旧逻辑可运行。应先形成对齐主线的新 DMS 候选并构建固定 digest，再按最新测试流程验证；不要把此处文档当作候选已经构建或切换完成。

### 5. 对齐主线时必须保留的共享改动

ERP 已采用产品独立镜像配置：`provision-agent.image(group)` 优先读取 `active-image-{group}.json`，缺失才回退旧 global；`activate-product.sh` 仅更新该产品 pin。ERP 回传 global 仍 `a11eb9e`，ERP pin 为 `56f9011...`，DMS 后续开户继续用自己的既有镜像。合并本 DMS 分支时保留该机制，并把本分支新增的 **`dms_sync` 队列/worker、容量和配置**兼容进去，不能用任一整文件覆盖另一侧。

重点重叠文件：`infrastructure/production/provision-agent.py`、`scripts/production/activate-product.sh`、对应 `scripts/production/test-provision-agent.py` / `test-activate-product.py`；另审查生产 Compose、`site-step.py`、CI 两工作流、容量检查和 dev 脚本。DMS 树无 tracked Core/ERP App 改动，但改了 DMS Vue 编辑器源码，且 Core 的 ignored `public/pearnly-app/` 内有其生成资源；候选必须按组合源码重建，不拷贝旧预览产物覆盖 ERP。

建议续接顺序：先回读 main/远端与环境实际状态 → 在本 DMS 工作树对齐已确认的新主线并解决共享文件 → 跑受影响的架构/DMS/镜像配置定向检查 → 构建固定候选 → 按 Windows 流程处理测试租户的单一消息/外部账号承接 → 手动四组采集与 LINE/第三方闭环。用户已要求试用新测试环境，普通本地实现与验证继续推进；正式 DMS 发布仍按用户届时指示，不从 ERP 发布授权推导。进入有费用或真实外部写入的步骤时沿用当前有效测试范围，不能照抄旧记录扩大对象。

### 6. 就地保留资源与解除条件

| 路径 / 资源 | 交接时状态、用途 | 归属与下次复核 |
| --- | --- | --- |
| 本 DMS 树、分支与四个 ignored 路径 | 未合并成果；`frontend/node_modules/`、`frontend/auto-imports.d.ts`、`frontend/components.d.ts`、`apps/pearnly_core/pearnly_core/public/pearnly-app/` 保留 | 本任务；下个窗口完成新候选构建/验证后再核用途，未集成前不删树 |
| `pearnly-dms-snapshot-lab-{bench,db,redis}-1` | `colima-pearnly` 下 3 容器运行，bench 原 serve 在，源码/scripts/tests RO 挂本树，无宿主端口；专属 sites/db/redis-data/logs 四卷 | 本任务 Mac 快循环实验室；换候选或退役前核队列、数据和挂载。历史记录 default=9/long=3 的旧任务未在本次重新计数，不能直接启动 worker 消费 |
| `/Users/skin/.codex/dms-manual-preview.uGyoJv`、`pearnly-dms-manual-preview` 及网络/两匿名卷 | sidecar 已自然退出，原 `127.0.0.1:18118` 不是当前可用预览；旧页面仅内存模拟，不是真 DMS | 本任务；用户需要再预览或确认退役时逐项处置，本轮未重启/删除 |
| `dms-line-full-flow-fix` 旧树及 `pearnly-dms-flow-lab-{bench,db,redis}-1` | 3 容器停止，仍引用旧树；旧文档有未提交续测记录 | 本任务旧业务验证；下个窗口先接纳必要记录/核旧测试数据再决定退役，不清队列或删除卷 |
| ERP 8157 预览树/6 容器；Windows 测试机/隧道/导入备份 | 其它任务交付资源，本任务本轮未操作 | ERP/Windows 对应任务；不能随 DMS 收尾清理 |

收尾状态为**交接保留**：本次仅提交本节文档，未合入 main、未推送任务分支、未部署 DMS；没有删除工作树/分支/运行资源。其它工作树、Windows 本地主线新增提交及旧 DMS 未暂存文档均不替其提交或清理。下个窗口从本节开始即可，不必重新阅读全部历史对话。

## 最新续接：测试环境"看不见新功能"是缓存不是代码（2026-09-24）

用户问：这次部署到测试环境的是否是那个窗口改的全部代码，为什么新增功能看不见。**结论：代码是全的，看不见是测试机 Frappe 的 hooks 缓存还停在旧镜像，页面因此把浏览器指到旧版本号的 JS，本地已命中的那个 URL 继续返回旧内容。** 本轮只做只读核对加一次标准 `clear-cache`，没有改业务代码、没有重建镜像、没有动线上、没有碰 LINE。

### 镜像与分支的等价证据

- 测试机 `pearnly-testenv-dms-web-1` 运行 `ghcr.io/skin306152-star/pearnly/frappe@sha256:0b37fe97…0969a9`，容器标签 `org.opencontainers.image.revision=0b3a90c`。
- `git diff --stat 0b3a90c ecbf4d1` 只有一个文件：`docs/work/2026-09-24-dms-manual-snapshots.md`。也就是说候选镜像之后两个提交全是文档，**业务代码等价于本任务分支 HEAD**；`0b3a90c` 之上还有 `0d76b6a`（测试）、`0b3a90c`（CI 套件）等非业务提交，均在镜像之内。
- 文件级逐一核对 sha256（镜像内 vs 本工作树）：`public/desk/connect.js` `9284fbeb109168c2…`、`adapter/geo_sync.py` `00db5eec…`、`snapshots/store.py` `8c114f92…`，三份全部一致。

### 根因（可复现）

- Frappe 把每个 App 的 hooks 缓存在 Redis（`cache_manager.global_cache_keys` 含 `app_hooks`），**无 TTL**。`pearnly_dms/hooks.py` 的 `app_include_js` 用 `versioned_asset()` 生成 `/assets/pearnly_dms/desk/connect.js?v=<文件内容 sha256 前 16 位>`，于是这份缓存决定了页面引用哪个版本的 JS。
- 测试机 Redis 里 `testdms.pearnly.com` 的 `app_hooks` 仍是 `desk/connect.js?v=b4eb40bb7f355411`——那正是**旧镜像** `dd81a3f` 里 `connect.js` 的内容哈希；新镜像应为 `9284fbeb109168c2`。这是替换镜像时复用了旧 `redis-cache` 卷造成的。
- 该 URL 现在取回的内容其实已经是新版（含四组 `datasets = ["vehicles","customers","geo","company"]` 与 `add_inner_button(cacheText("dms.connect.cache.read"), requestCache)`），所以服务端文件没问题；但响应的 `cache-control: max-age=14400` 让浏览器继续用本地那份旧 JS，连接页就只显示旧的"测试连接／停用／修改账号"，没有"更新数据"。
- 注意：`testdms.pearnly.com` 与 `dms.test.localhost` 的缓存状态不同——前者是换镜像前就存在的站点（旧值），后者是这次新建的（新值）。清缓存前后一次回读即为对照。

### 本轮处置与回读

- 在测试机对全部 5 个站点执行 `bench --site <site> clear-cache`：`testdms.pearnly.com`、`dms.test.localhost` 在 `pearnly-testenv-dms-web-1`；`testerp.pearnly.com`、`erp.test.localhost` 在 `pearnly-testenv-erp-web-1`；`cowork.test.localhost` 在 `pearnly-testenv-cowork-web-1`。这是换镜像后的标准操作，缓存会自行重建，不是业务数据修改。
- 回读 Redis：两个 DMS 站点的 `app_hooks` 均已变为 `desk/connect.js?v=9284fbeb109168c2`。
- 用户侧只需刷新浏览器标签页一次；因为版本号已变，不需要手工清浏览器缓存。
- 待办（与"测试环境 up 自动写 `dms_sync` 队列配置"同类）：把 `up` 之后对每个站点 `clear-cache` 并入 `scripts/testenv.sh`。该脚本目前由 `codex/testenv-per-app` 分支在改，动手前先定这个分支的去留，避免两个窗口改同一个文件。

### 仍未验收（不变）

LINE 菜单 1–5、四组完整真实采集与首次发布（尤其全国地址整轮）、编辑回卡与订车付款附件、真机与真实消息本轮都没有测。

### 第二个环境落差：导入的站点库没有新表（已 migrate）

清缓存后"更新数据"按钮出现，点击即暴露下一层问题：`MySQLdb.ProgrammingError (1146) "Table '_32ceeaae3b7cd7ba.tabDMS Read Snapshot' doesn't exist"`，另一张截图是前端友好提示"单据类型 DMS Read Snapshot未找到 … 暂时无法确认读取结果，请刷新页面查看；后台读取可能仍在继续。"（后者说明新版 JS 确实已在运行，只是服务端缺表，可反过来印证上一节的缓存结论）。

- 根因：`testdms.pearnly.com` 的库是**换镜像之前**从线上导入的，站点库里没有本分支新增的 DocType；`testenv.sh up` 只切换镜像，不做 `bench migrate`。同一镜像新建的空白站 `dms.test.localhost` 反而有该表，两者对照即可说明是"导入库 + 新镜像"的组合问题。
- 处置：在 `pearnly-testenv-dms-web-1` 对 `testdms.pearnly.com` 执行 `bench --site testdms.pearnly.com migrate`（约 10 秒：frappe/pearnly_core/pearnly_dms DocTypes、jobs、fixtures、dashboards 同步，结尾排队重建搜索索引）。
- 回读证据：`frappe.db.get_tables` 已含 `tabDMS Read Snapshot`（同批还有 `tabDMS Booking Delivery`、`tabDMS Customer Link`、`tabDMS Change Request` 等）；`pearnly_dms.connections.snapshots.get_status` 返回 `configured: true` 与四组 `state: idle`，即连接页读取链路的服务端部分已可用。
- 后台就绪：`sites/common_site_config.json` 仍有 `dms_sync` 队列配置，`pearnly-testenv-dms-sync-worker-1` 处于 Up。
- 待办：把 `bench --site <site> migrate` 与上面的 `clear-cache` 一起并入测试环境流程（`testenv.sh up` 或文档步骤），否则每次换镜像都要人工补。
- **未对** `testerp.pearnly.com` 与 `cowork.test.localhost` 执行 migrate：它们是同样的"导入库 + 新镜像"组合，很可能缺表，但属其它任务窗口，本轮不越界改动，等对应窗口测试前各自 migrate。
- 仍未验收：四组真实采集与首次发布（用户下一步动作）、LINE 菜单 1–5。

### 首次真实四组采集：2/4 成功发布，2/4 被线上自动刷新抢账号

用户在连接页点"更新数据"全选四组（测试机本地 22:15–22:18，容器日志 UTC 15:15–15:18）。**方案按设计工作**：分组入队、多轮复核、独立发布、区分错误码、不假成功都实测到；失败两组的根因是线上环境正在用同一个第三方 DMS 账号自动刷新。

- 成功：`vehicles` 发布 29 条（attempt 0/1/2 三轮，`as_of`/`completed_at` 写入）；`customers` 发布 46 条（多轮复核 40 → 46 后发布，attempt 0..3）。`get_status` 同时返回 `configured: true` 与四组 state/progress/count/as_of/error_code，前端"更新数据"按钮、全屏"数据更新中..."、失败提示均实测出现。
- 失败：`geo` = `ERR_DMS_CONCURRENT_LOGIN`（第三方判定该账号正在别处使用）；`company` = `ERR_DMS_UNREACHABLE`（连不上，对方主机新连接本就 6–16 秒起步）。
- 队列证据：`pearnly-testenv-dms-sync-worker-1` 上 `pearnly_dms.snapshots.manual.execute` 逐条执行，作业 ID 带 `-0/-1/-2` attempt 后缀，说明多轮复核确实由重新入队实现，不是单次长任务。
- **根因证据**：线上 `107.155.65.74` 的 `pearnly-production-dms-worker-1` 日志里，`pearnly_dms.adapter.warm.refresh` 针对 **testdms.pearnly.com / EP-00036** 每 **4 分钟** 自动跑一次（3 小时内 20 次，间隔全为 4 分钟），单次 44–62 秒；本次 15:16:10 的那一轮与测试环境 15:15:48–15:17:48 的采集完全重叠。测试环境的 DMS 账号来自整站导入的线上租户数据，两个环境共用同一账号 → 会话互顶。这正是总交接里"同一第三方 DMS 账号的双环境活动必须核对"首次被实测命中。线上跑的是旧镜像 `a11eb9e`，仍带这套自动刷新。
- 熔断回读（Redis `pearnly:dms:pacing:v1:EP-00001`）：`failures: 0`、`until: 0`，**当前无冷却可直接重试**；`failed_at` 与 company 失败时刻一致，其后一次成功已把计数清零。
- 下一步（待用户决定）：一次只勾一组重试；或测试期间暂停线上 4 分钟自动刷新；或给测试站配独立 DMS 账号。后两者要动线上/第三方账号，需明确授权。
- 另记：测试机 worker 容器初始化阶段（14:32–14:45）有若干 `validate_queue` → `RuntimeError: object is not bound` 回溯，发生在启动期、与本轮采集无关，留作测试环境噪音待查。

### LINE 身份证识别在测试环境为什么回"请再发一次"

用户在 DMS-TEST OA 发身份证照片（本地 22:19），机器人回泰文"คิวอ่านข้อมูลเต็ม กรุณาส่งรูปอีกครั้งในอีกสักครู่"。定位：**消息确实到了测试环境，OCR 任务在入队那一刻就被拒**——测试环境没有接 OCR 的容量/凭据/计费，属环境缺口，不是采集或识别代码缺陷。

- 到达证据：`pearnly-testenv-dms-frontend-1` 访问日志 15:18:55、15:19:06 两条 `POST /api/method/pearnly_core.line.webhook` 均返回 200（LINE 不会重试刷屏）。
- 失败证据：`pearnly-testenv-dms-web-1` 日志中的入队回溯 `pearnly_dms/intake/flow.py:_enqueue` → `pearnly_dms/line/out.py:enqueue` → `pearnly_core/execution/work.py:submit` → `pearnly_core/execution/budget.py:redis()` → `Deferred("capacity_not_configured")`。该回溯由 `flow.py:_enqueue` 明确记录（"入队失败不能静默"），不是被吞掉的错误。
- 直接原因：测试环境 `sites/common_site_config.json` 缺 `pearnly_ocr_budget_redis`。线上同一文件有该键（`redis://redis-queue:6379/6`，由 `infrastructure/production/site-step.py` 写入，口径见 `docs/operations/shared-ocr-capacity.md`）。容量存储缺失 → `submit()` 直接抛 Deferred，任务不入队。
- 同一环境还缺另两件（补了容量也仍跑不通）：测试机 `/opt/pearnly/secrets/ocr.json` 只有 **3 字节占位**，不是真实服务账号；站点 `pearnly_billing_api` 指向本机 `billing-web`，而该容器没有站点（与首页 `BillingUnavailable … 计费系统响应 HTTP 404` 同一根因）。
- 产品行为符合设计：`line/out.py` 捕获 `QueueFull/Deferred` 后回"识别队列繁忙，请稍后重新发送图片。"（泰文即用户看到那句）并重新抛出以便记日志；webhook 仍正常返回。
- 小观察（非缺陷）：容量"没配置"与"真的排满"共用同一句提示，排查时容易误判成业务繁忙；测试环境会一直命中前者。
- 待用户决定：① 给测试环境接本地假 OCR（仓库既有 `pearnly_ocr_endpoint` 的实验室做法）只验链路、不产生费用；② 接真实凭据与计费（会产生真实付费调用，与"测试环境不接付费 OCR"的约定冲突，需明确授权）；③ 先跳过拍照识别，测其余不依赖 OCR 的菜单路径。

## 最新续接：线上停掉自动重刷 + 测试环境改成"一个产品一套栈"（2026-09-24）

用户口径（原话摘要）：线上的自动刷新"全部关掉、不用这个机制"；测试环境要"再次搭建好、搭成理想状态、未来方便用"；**每个 app 一套单独的容器**（DMS 一套，将来 ERP 也一套），不要全挤在一起怕冲突。流程目标：改代码 → 一键把该 app 的测试租户整套切到测试机 → 真机验证 → 一键切回 → 正式部署。

### 线上自动重刷已停（含真租户）

- 机制是 `pearnly_dms.adapter.warm.sweep`，cron `* * * * *`：每分钟醒一次，判断"该刷了"就派发 `pearnly_dms.adapter.warm.refresh`（实测表现为每约 4 分钟真刷一次，单次 44–62 秒，且要登录同一个第三方 DMS 账号）。**这正是本任务四组采集被顶掉账号的根因。**
- 两个站点各自独立数据库与独立记录：`testdms.pearnly.com`（`_32ceeaae…`，行 `93joaufdck`）与 `ris.pearnly.com`（`_b8a0c781…`，行 `rnm47tp8f6`）。均已置 `stopped=1`。用户说明真租户当前无人使用，同意一并停。
- **"删掉"在旧代码上不成立**：`frappe.core.doctype.scheduled_job_type.scheduled_job_type.sync_jobs()` 在每次 `bench migrate` 时按代码清单 `insert_events()` 补建缺失行（新建即启用），同时 `clear_events()` 删除清单里没有的。旧镜像的 hooks 声明了这项，所以删了会被下一次升级建回来。置 `stopped=1` 会被 `insert_single_event` 保留（它只更新 frequency/cron_format）。**新代码的 hooks 不再声明它 → 新版部署 + migrate 时由 `clear_events()` 自动清掉**，那才是真正的删除。
- 效果回读：改后 2 分钟内生产 worker 日志中 `dms-master-warm` / `pearnly_dms.adapter.warm` 出现 **0 次**（改前每分钟一次）。
- 副作用（已告知）：`ris` 的预热没了，旧代码下真客户第一次操作会从"秒开"变成约 12.3 秒冷读；无人使用期间无影响，新方案上线后该机制本来就退役。

### 测试环境按 app 拆栈（本地已提交，未推送）

- 分支 `codex/testenv-per-app` 提交 `2d5cd60`：每个产品一套栈，项目名 `pearnly-testenv-<app>`，各自独立 MariaDB、两个 Redis、站点卷、网络与容器；`up <app>` 会先停掉别的测试栈（容器删、卷保留）。同一时间只跑一个（端口固定分配）。
- 起栈自动做三件以前靠人记的事（`infrastructure/testenv/finish-start.py`）：`bench migrate`、`bench clear-cache`、写 `pearnly_ocr_budget_redis` 与队列超时。这三件正是本轮"看不见新功能 / 表不存在 / 识别请重发"的直接原因。
- 工具与被测代码分开：驱动与测试辅助脚本取本分支，产品代码与 `production/compose.yml` 取 `PEARNLY_TESTENV_SRC`（默认当前工作树）；`lab-start <app>` / `lab-stop <app>` 已改成起栈/停栈 + 导出导入 + 收尾 + 切域名，一次只允许一个产品。
- 本地检查：`check-architecture.py` 0 违规、`bash -n`、`py_compile`、`git diff --check` 通过。**尚未推送、尚未在测试机实跑**。

### 下一步（接手直接从这里开始）

1. 推 `codex/testenv-per-app`（推前按 CI 同名闸门复核）→ 把它合进 DMS 候选分支（`codex/dms-five-minute-snapshots`），这样被测栈定义里既有 `dms-sync-worker` 又有新工具。
2. 在测试机执行 `PEARNLY_TESTENV_SRC=<DMS 工作树> PEARNLY_TESTENV_IMAGE=<候选 digest> bash scripts/testenv.sh lab-start dms`，验证：独立栈起来、站点库自动升级、缓存已清、四组采集不再撞账号。
3. 补 OCR 与计费（"理想状态"缺的两块）。用户已定：**OCR 凭据拷过去、计费要"做完整做真实"**。
   - ① OCR 凭据：**已完成**——测试机 `/opt/pearnly/secrets/ocr.json` 换成线上那份，回读 2343 字节、600、sha256 `7a5476486b1b7262…` 与线上逐字节一致（原来是 3 字节空占位）。
   - ② 计费（**未做**）：结论先记清楚——**DMS 识别在调模型前会先问账本**（`pearnly_dms/ocr/ocr_service.py` 先 `billing_client.status`，取不到就 `Deferred("billing_unavailable")` 一直推迟），所以"不接计费"等于"拍照识别永远不会开始"。做法：把线上 `billing.internal` 整站导入测试机自己的 billing 容器（`pearnly-testenv-<app>_sites-billing`，与生产物理隔离），带上测试租户真实余额与流水，识别走真实"查余额→放行→扣费→留记录"。**不能只建空站点**：租户 site_config 里的 `pearnly_billing_token` 是从线上导入的，必须与账本站点的密钥匹配，否则签名请求被拒。
   - 生产侧参考实现：`infrastructure/production/bootstrap-billing.py`（建 `billing.internal`、装 `pearnly_control`、写 `pearnly_billing_only/scoped_tokens/token/management_token`），它依赖 `/run/secrets/runtime.env` 与 `/run/secrets/member-identity.json`——测试机要照搬这两个挂载，或改为整站导入。
4. 验证通过后：按 `activate-product.sh` 部署新版 DMS 到线上（届时 `warm.sweep` 由 `clear_events()` 自动删除）。

### 地址目录：为什么它和另外三组不是一类东西（2026-09-24 调查）

### OCR 在测试环境一直重试：三道坎与处置（2026-09-24）

### 地址改为列表读取（代码已写、测试待补）+ LINE 卡点已解除（2026-09-24 深夜）

### 逆向 DMS：它自带**变更流水**，这是并发对齐的正解（2026-09-25 只读实测）

### 落地：绑定即建缓存底座 + 删除手动触发 + 地址改列表读取（2026-09-25，已上测试环境）

用户决定：只用"绑定读全量做底座 + 操作顺手读日志补增量"这一条路；**删除**之前的手动触发与定时方案；第二个账号暂不做；做好后发到测试环境，并把测试账号的数据清一下以便做首次绑定实测。

**已实现（分支 `codex/dms-five-minute-snapshots`，提交 `5952a50`，已推送）**：

- `connections/snapshots.py`：删掉 `request_refresh` RPC；`get_status` 仍只读本地元数据并覆盖**四组**；新增内部入口 `bootstrap(endpoint, actor)`（不是白名单方法），把四组依次排队。
- `connections/api.py`：`save_connection` 成功后调用 `bootstrap` → **绑定即建底座**（凭据一换 scope 就变，所以总是重读）。
- `public/desk/connect.js`：删掉"更新数据"按钮与选择弹窗；白屏覆盖改为**动态百分比**（`正在读取 DMS 主档… 24%`：四组各占四分之一，已知总数的组再按条数细分；只往上走，未全成功前最多 99%），读完只提示一次，**空闲不轮询**，失败不重试风暴。
- `adapter/geo_sync.py` + `client/geo_list_reader.py`：地址改**列表端点**读取（计数锚点 + 分页读满 + 父级唯一匹配 + 完整树校验）。首次读取从 15,729 次详情请求 / 约 33 分钟变成 **37 个请求 / 约 7 秒**。
- `hooks.py` / `snapshots/geo_daily.py`：**删除**每日 4 点那套定时任务（属"之前的方案"）。
- 词条：把"点击更新数据"这类指向已删除按钮的文案改成"稍后自动重试/继续"；`i18n-build.py --check` PASS。
- 测试：`tests/test_dms_geo_list_reader.py`（18 项）替代旧的 `test_dms_geo_catalogue.py` + `test_dms_geo_resume.py`；`tests/test_dms_connection_snapshots.py`（13 项）改为新契约（含"`request_refresh` 必须不存在""bootstrap 不是 RPC"）；`tests/test-dms-connect-overlay.cjs`（7 项）替代旧的 `test-dms-connect-snapshots.cjs`（那套的全部前提是被删除的手动选择流程）。旧断言针对的是已退役工作流，替换而非放宽。

**本地闸门（提交前全跑）**：`check-architecture.py` 0 违规；`check-dms-offline.py` 全 PASS（reader 54、catalogue 26、company schema 8、geo list 18、connection 13、advisors 15 及六个 sample-verify 套件）；`test-provision-agent.py`、`check-production-capacity.py`、`test-activate-product.py`、`i18n-build.py --check`、`test_welcome_email.py`、`git diff --check` 通过；CI 同名 node 套件（native desk / POS / roster / records / issue actions / editor snapshot）通过；新增 JS 套件 7/7。

**测试环境部署与验证（实测）**：

- CI `build-production.yml` run `36038917347` 成功；镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:25c0c3a3…f117`，revision 标签 `5952a5072ebe…`（＝本次提交）。用仓库既定 `receive-image.py`（凭据走 stdin）拉到测试机后 `testenv.sh up dms`，12 个容器全部 Up。
- **清缓存**：`DMS Read Snapshot` 的四行被**清空内容**（`payload/overlays/staging/refresh_request/source_started_at/completed_at/generation`），不删行——该 DocType 的 `on_trash` 明确保护（"managed by the synchronizer"）。业务数据未动：endpoint 4、operator 2、OCR Work 28 条保留。
- **绑定路径实跑成功**：调用与 `save_connection` 相同的 `bootstrap` → 四组全部 `succeeded`：vehicles 29、customers 46、company 27、**geo 15,729**（payload 约 1.04 MB）；四组 `as_of` 落在 15 秒之内。worker 日志显示单次作业 1.9–2.8 秒（含分片复核），队列随后为空、无残留锁。
- 教训（记下来避免误判）：我用一个长事务脚本轮询状态时看到的"车辆组卡住 4 分钟、其它排队"是**观察者自己的 REPEATABLE READ 快照**造成的假象，不是系统卡住。任何"进度显示"都不能在长事务里读——连接页每次轮询是独立 RPC/事务，所以不受影响。

**仍未做（下一步）**：

- **增量同步**（用户认定的"唯一方案"）：操作动作顺手读 `eventlog/component/showdata.php`（按模块 `ftd=<模块>||2`）、按游标取"上次之后的变化"、只补读被点名的记录。三条待验边界仍在：批量导入是否记明细、日志保留窗口、六个次要表无同名模块。
- 第二个共享账号（用户明确"先不做"）。
- 生产发布与正式验收。

用户把问题问准了：一个 DMS 被几十上百人共用，LINE 端拿缓存，**别人新增/修改的数据、以及写操作才产生的单号，我们怎么跟上**。全量重读和"每次手动刷新"都不是答案。于是去逆 DMS 自身，找到它原生的"使用记录"（`eventlog`）——**这正是一条可用的变更流水**。

**入口与参数（实测）**：页面 `eventlog/view.php`，列表端点 `eventlog/component/showdata.php`。参数是 `sd`（自由搜索）、`ftd`（过滤器，**不是** `selfilterdata`；该名字是页面 select 的 id）、`sdtamt`/`sdtpage`。过滤器格式 `<模块名>||<类别>`，例如 `ลูกค้า||2`（客户）、`ใบจอง||2`（订车）；类别 1＝登录类事件，2＝模块事件。**没有"所有模块"这一个过滤值**（`ftd=2` 直接 `err::`），所以必须按模块逐条查。

**每条记录给出什么**：`data-val="<序号>||<类别>"`、账号、日期（佛历 dd/mm/yyyy）、**时间精确到秒**、来源 IP、模块名、**动作（เพิ่ม 新增 / แก้ไข 修改 / ลบ 删除）**、以及**被改记录的唯一标识**。实测样本：

- 客户：`831||2 | dmstest | 24/09/2569 11:11:59 | … | ลูกค้า | แก้ไข | 1103702587460`（末列＝**身份证号**）
- 订车：`828||2 | sale02 | 24/09/2569 10:52:55 | … | ใบจอง | เพิ่ม | BK000002609000028`（末列＝**单号**）
- 地址：`797||2 | dmstest | 20/09/2569 15:10:47 | … | รหัสไปรษณีย์ | ลบ | 99997`
- 车型：`767||2 | dmstest | 20/09/2569 13:41:40 | … | รุ่นรถ | ลบ | E2E260920`

**可覆盖的模块共 55 类**，包含我们全部缓存对象：ลูกค้า（客户）、ใบจอง（订车）、ออเดอร์（订单）、ใบสั่งขาย（销售单）、เปิดสัญญา（开合同）、รับเงินจอง/รับเงินดาวน์（收订金/首付）、พนักงาน（员工）、สาขา（分店）、บริษัท（公司）、รุ่นรถ/สีรถ/ยี่ห้อรถยนต์/ประเภทรถ（车型/颜色/品牌/类型）、จังหวัด/อำเภอ/ตำบล/รหัสไปรษณีย์（省/区/镇/邮编）等。

**实测成本与规模（本站点真实数据）**：规模很小——客户 230 条事件、订车 263、员工 122、车型 17、分店 6、省/邮编各 3、公司 2。会话内**一页 100 条约 0.028 秒**；一次完整"登录 + 会话 + 十几个请求"**合计 3.06 秒**（登录约占 2.7 秒，是主要成本）。

**可以按记录反查**：`sd=<记录标识>` 配合对应模块过滤，用**1 个请求**就能拿到"这条记录发生过什么"——实测 `sd=1103702587460` 得 5 条客户事件、`sd=BK000002609000028` 得 3 条订车事件。这就是"操作员要动某条记录时，先花 1 个请求确认它最新状态"的依据。

**这解决了用户三个具体担心**：

1. **别人新建/修改的数据** → 靠流水发现"哪几条变了"，再**只补读那几条**，不必全量重扫，也不必等下一次全量刷新。
2. **写操作才产生的单号**（录入会改变 DMS 的单号序列） → 单号由 DMS 在提交时分配并记入流水；我们只展示 DMS 给的结果，并用流水核对，不自造、不缓存未来号。
3. **几十上百人并发导致缓存失真** → 失真被限制在"两次轮询之间"；提交仍始终实时校验，因此不会写错。

**待验证的洞（不能当已解决）**：

**覆盖度实测（2026-09-25）：我们缓存的表 33/33 都有对应模块，且都确实在记。** 逐表取该模块的事件总数（`ftd=<模块>||2` 的 `dt::<n>`），全部 >0：

| 表 | 模块 | 事件数 | 表 | 模块 | 事件数 |
| --- | --- | --- | --- | --- | --- |
| customers | ลูกค้า | 230 | company | บริษัท | 2 |
| booking | ใบจอง | 263 | branch | สาขา | 6 |
| users | พนักงาน | 122 | team | ทีมสาขา | 4 |
| car | รุ่นรถ | 17 | stores | คลัง | 2 |
| prefix | คำนำหน้านาม | 9 | orders | ออเดอร์ | 3 |
| carcampaign | แคมเปญรถ | 9 | works | งาน | 3 |
| carpaint | สีรถ | 5 | bank | ธนาคารของบริษัท | 8 |
| branchcus | สาขาของลูกค้า | 1 | finance | บริษัทสินเชื่อ | 1 |
| carbrand | ยี่ห้อรถยนต์ | 1 | provinces | จังหวัด | 3 |
| typecar | ประเภทรถ | 1 | districts | อำเภอ | 4 |
| subtypecar | ประเภทย่อย | 1 | subdistricts | ตำบล | 3 |
| ctgrcampaign | หมวดแคมเปญ | 1 | zipcodes | รหัสไปรษณีย์ | 3 |
| product | สินค้า | 1 | sales_order | ใบสั่งขาย | 9 |
| units | หน่วยสินค้า | 1 | payment_booking | รับเงินจอง | 11 |
| grade | เกรด | 1 | payment_down | รับเงินดาวน์ | 10 |
| enginepower | ขนาดกำลังเครื่องยนต์ | 1 | contract | เปิดสัญญา | 4 |
| engineno | รหัสเครื่องยนต์ | 1 | | | |

尚未逐一核对的次要表（不能算已覆盖）：`typecardesc`、`cargear`、`businessowner`、`setcbc`、`cusbank`、`numdayspullcarback`——过滤器 55 项里没有明显同名的模块，需要单独确认它们是否走别的模块记账。

**并发容量实测（同一天）**：

**操作员账号能否读日志（2026-09-25 实测，结论一半相反）**：

- **页面被拒**：用操作员自己的账号（EP-00036＝sale02，`authoritative_read=False`）打开 `eventlog/view.php`，返回 `alert("Error : ")`；该账号的首页菜单里也没有"使用记录"入口。用户"操作员一般没有查看日志权限"的判断成立。
- **但底层数据端点没有拦**：同一操作员会话 `POST eventlog/component/showdata.php`（`ftd=ลูกค้า||2`）**返回了 5 条真实客户事件**（`data-val="831||2"`，与共享账号读到的一致）。即：**页面级权限被拒，数据端点未设防**。
- 含义：**"顺手读日志"今天技术上用操作员会话就能做，不需要常驻账号**；但它是**对方的权限口子**，不是它开放的接口——随时可能被修，届时"顺手读"会静默失效。因此若走这条路，必须加**拒绝即视为"新鲜度未知"**的守卫并回退到共享账号路径，绝不能默认"读到空=没变化"。
- **更干净的出路（待确认）**：DMS 里有菜单设置模块 `setmnu`（ตั้งค่าเมนู，首页 80 个模块之一）。若它能把"使用记录"菜单按角色/用户组授权，则应让租户管理员**正式开通**该权限，再用操作员会话读日志——既不用常驻账号，也不是绕过权限。该页面是 JS 驱动的（HTML 里没有具名控件，只有 `frm`/`viewport`），**我无法只读确认它是否支持给 eventlog 授权**；这需要 DMS 管理员在界面上看，或问厂商。

- 我们的会话锁**按账号**互斥：`account_lock(account_key, timeout_sec=180)`，等满 180 秒即 `SessionLockUnavailable`；锁 TTL 默认 **600 秒**（`PEARNLY_SESSION_LOCK_TTL` 可调），即持锁进程被杀后该账号最多被堵 10 分钟。实测中确实出现过一次「等了 180 秒、锁还剩 420 秒」的残留（后来自然过期；两个 Redis 各库现均为 0）。
- **应用目前只支持一个共享主账号**：`connections/snapshots.py:_primary_endpoint` 在发现多个共享连接时直接 `frappe.throw`（"Multiple primary DMS connections exist"）。数据模型现状：每个操作员各有自己的端点（如 EP-00036/EP-00002 = sale02、EP-00003 = sale03），共享主连接只有一个（EP-00001 = dmstest）。
- 因此**排队只发生在"需要共享账号"的操作上**：四组权威读取、需要权限的借用写入，以及（拟议的）日志读取。操作员用自己账号的写入不受这条锁影响。
- 单次操作成本参考：登录 + 会话 + 十几个请求 = **3.06 秒**（登录约 2.7 秒）；四组主档采集是分钟级。也就是说：若两个操作员同时需要共享账号，后者最坏要等到前者做完；若前者是分钟级的采集，后者会直接吃 180 秒超时。**用户判断"多人并发会等很久"成立**，增加第二个共享账号是正解，但需要应用侧支持"凭据池"（缓存 scope 必须绑"逻辑连接"而不是某个凭据，否则会出现两份缓存或 scope 变更错误）。

- **批量导入是否被记成明细**：模块清单里另有"นำเข้าข้อมูลใบจอง（导入订车数据）""นำเข้าข้อมูลจัดสรรโควต้า"这类**导入模块**，说明批量导入可能只记一条"导入"事件、而不是每条记录的明细。若租户用批量导入，流水会漏掉具体记录——需要单独验证。
- **保留窗口**：本租户客户模块只能翻到 2 页（约 200 条，最早 13/06/2569），是"历史就这么多"还是"有截断/保留上限"未证实；断线很久后能否靠流水补齐，取决于这一点。
- **谁在轮询**：轮询要占用一个 DMS 会话（约 2.7 秒登录/次）。应使用**只有我们使用的共享管理员账号**（不是操作员账号），且**同一账号只能有一个轮询者**——这正是早些时候测试环境与线上抢账号的根因，ops 上必须保证唯一。
- 覆盖完整性（是否所有写路径都记）尚未穷举证明；因此流水只作**发现变化**的信号，**不作为唯一事实来源**：最终提交仍实时读取校验。

用户在 LINE 里发身份证后收到 `ERR_DMS_SNAPSHOT_PENDING`（"DMS 数据还没准备好…旧数据 24/09 23:01"）。**OCR 本身已成功**（照片被接收、机器人往下问电话），卡住的是**地址组**：`geo` 快照完全不存在（`store.read` 抛 `SnapshotUnavailable`，其默认码就是这个），`customers/vehicles/company` 都在（从线上导入）。

**代码改动（已提交本地，未推送）**：

- 新增 `client/geo_list_reader.py`：从**列表端点**读四张地址表并拼四级树。先取原生计数（`dt::<n>`）作锚点、再按页读满；行形状、ID 重复、计数与分页不一致、父级未知或重名歧义都判整代失败（**不猜**）。不使用详情页英文字段（实测全空、邮编表无该控件）。
- 改写 `adapter/geo_sync.py`：一次读完、整代成功才 `store.publish`；**删掉旧的分片断点**（详情协议的形状已不兼容，显式清掉暂存）；失败仍保留上一代。
- `hooks.py`：新增每日任务 `pearnly_dms.snapshots.geo_daily.run`，cron `0 4 * * *` 与 `30 4 * * *`（第二枪是失败重试；已成功则任务自身跳过）。**Frappe 的 cron 按站点时区求值**（本站 Asia/Bangkok，实测容器 UTC 17:01 时 `frappe.utils.now()` 为 00:01），所以就是泰国凌晨 4 点，不依赖宿主时区。
- 新增 `snapshots/geo_daily.py`：取本站启用的共享主连接；数据 20 小时内刷过就跳过；正在跑就跳过；否则 `manual.request(endpoint,"geo")`（复用既有队列与发布闸门）。**不在这里登录第三方。**
- `connections/snapshots.py` 与 `public/desk/connect.js`：地址**从"更新数据"按钮移除**（服务端也拒绝该组，前端不藏逻辑）。连接页现在只有车辆/客户/公司三组。

**验证（真实数据，实测）**：

- 新读取器在生产同源实例上读全：**15,729 行 / 37 个请求 / 约 7 秒**（省 77、区 928、镇 7,364、邮编 7,360），`complete_tree` 通过、零重名歧义、零未知父级；对照详情方式（15,729 次请求、约 33 分钟且一次超时全废）。
- 已在测试机**发布 geo 快照**（`as_of` 新、`stale=false`），四级目录可读：省 77、某府下区 8、某区下镇 7、某镇下邮编 1。
- 用公开行政区划名（กรุงเทพมหานคร/ป้อมปราบศัตรูพ่าย/ป้อมปราบ/10100）走 `Directory.resolve_address`：换出原生 ID 省 1 / 区 8 / 镇 39 / 邮编 5 —— **LINE 流程的卡点解除**。
- `tests/test_dms_connection_snapshots.py` 已更新为三组契约并新增"地址不可从按钮请求"断言：14 项通过。
- 注意：测试机上这次 geo 是由**注入的新读取器**直接 `store.publish` 发布的（现场解除用户阻塞），不是在跑镜像里的采集器；镜像里的采集器要等下次构建才带上。

**未完成（必须在推送前补齐）**：

- `tests/test_dms_geo_catalogue.py`（14 项）与 `tests/test_dms_geo_resume.py`（4 项）仍按**旧详情协议**写：前者引用 `CHECKPOINT_VERSION`、断言 `result["complete"]`；后者的"分片续跑"概念已被一次性读取取代。需要按新契约重写/替换（保留等价覆盖：整代成功才发布、失败/忙碌不覆盖上一代、旧 scope 不得发布、行形状与父级歧义拒绝、计数锚点），**不得靠删断言变绿**。
- 因此本批**未推送**：`scripts/check-dms-offline.py` 里那两套仍是红的。推送前还需跑完整闸门。
- 旧 `client/geo_catalogue.py` 与 `geo_aux` 注册表在本次改动后是否仍被引用，需在补测试时一并核对（避免留下无人使用的迁移残留）。

用户报告"测试环境 OCR 一直在重试"。只读排查后逐层解决，**现已可真实识别**。

**在重试的是什么（不是失控）**：`tabPearnly OCR Work` 共 27 条——26 条是从线上导入的历史任务，状态全是 `succeeded`（终态，不会被重跑，**没有重复扣费风险**）；只有 1 条 `deferred`，就是用户那张身份证照片（`176ad2ecc0dcf4a7e9f385136ac2459cc84e9593cfe1177b53d6886adf94c0de`），每约 31 秒重试一次。日志 `billing.status 查不到：… HTTP 404` 说明它**卡在计费那一步**（容量那道坎上一轮已由 `finish-start` 自动补好），而且重试期间**没有调用模型、没有扣费**。

**三道坎与处置**：

1. **OCR 容量存储**（上一轮已解决）：`pearnly_ocr_budget_redis` 写进 common_site_config，否则任务在**入队**那步就 `capacity_not_configured`。
2. **账本站点缺失**（本轮解决）：测试栈的 `billing-web` 里没有 `billing.internal`，计费请求 404。**必须整站导入**，不能只建空站——产品站点用的是**记账本数据库里的**分租户服务令牌（`pearnly_billing_scoped_tokens`），空站里没有那一行。
3. **OCR 凭据文件权限**（本轮解决）：线上 `/opt/pearnly/secrets/ocr.json` 是 `1000:1000 600`；我上轮拷过去的是 `root:root 600`，而应用以 uid 1000 运行 → `PermissionError: [Errno 13] Permission denied: '/run/secrets/ocr.json'`（表现成识别 failed / 一直重试）。已 `chown 1000:1000` + `chmod 600`，与线上一致。

**本轮实现（`codex/testenv-per-app`，已推送 `e1be310` 等，并合入本分支）**：

- `import-test-tenant.py`：加 `billing` 组分支（只接受 `billing.internal`，校验 `pearnly_billing_only`），产品校验对它跳过。
- `run.sh`：`import`/`migrate` 允许 `billing` 并路由到本栈的 `billing-web`；`override.yml` 给 `billing-web` 补挂 `/workspace/import`（否则导入找不到文件）。
- 新增 `billing-init.py`：账本站点是导入进**新卷**的，sites 卷上的 `common_site_config.json` 与 `apps.txt` 没人生成过 → `bench` 会报 "Service redis_cache is not running"（实测）。该脚本按产品栈同形补公共配置，并按镜像现有 App 补应用清单（结果与线上一致：`erpnext / frappe / pearnly_control`）。
- `finish-start.py`：新增**凭据可读性检查**（读 site_config 的 `pearnly_ocr_credentials` 并试读），读不了就明确提示 `chown 1000:1000`。防止第 3 类坑再静默发生。
- `testenv.sh`：`lab-start` 现在连带导出/导入账本站点（`billing:billing.internal`），`import billing billing.internal` 可用。

**验证证据（本轮实测）**：

- 产品站点 `billing_client.configured()` = True；`status()` 返回 `{"allowed": true, "balance_thb": 959.5, "estimate_thb": 1.5}`。
- 容器内 `ocr.access_token()` 成功（长度 1024）；配置 project `pearnly` / location `asia-southeast1` / backend `vertex`。
- **真实识别调用成功**（仓库合成样本，非客户数据）：两次分别 8.8 秒与 7.4 秒，字段齐全、`missing=[]`、`needs_review=False`。
- **真实扣费**：`OCR-2026-000043` / `OCR-2026-000044` 均 `ok`、各扣 1.5 THB（reference 分别为 `testenv:verify:20260924`、`testenv:verify3:20260924`，与本次验证调用一一对应）。本轮验证共花 3.0 THB（测试账本，与线上账本隔离）。
- 那张失败的照片（work `176ad2…`，`attempts=46`，`last_code=ocr.failed`，`charged=0`）已是终态，不会再重试。

**仍未验证**：LINE 端到端（webhook → 队列 → 识别 → 回卡/建档）。需要用户在 LINE 里重发照片，属于用户侧验收，不能由本轮只读/直连调用代替。

触发：首次真实四组采集中 `geo` 失败（`ERR_DMS_SNAPSHOT_INCOMPLETE`）；用户提出"身份证上本来就有地址，OCR 读出来填即可，地址为什么要读缓存"。以下全部为**只读实测**（走既有适配器与会话锁，未绕过、未写入任何第三方数据）。

**现状与失败机理**：当前按"逐条读详情页"采集，全国 15,729 条＝15,729 次 `{kind}/form.php`；实测约 8 请求/秒 → 一路顺利也需约 33 分钟；任何一次单页 30 秒超时即整轮判不完整并丢弃断点（本任务实测在第 31 轮被打断，即仅走完约 1/5）。结构上几乎不可能成功。

**DMS 侧新发现（只读）**：

- 列表接口 `{kind}/component/showdata.php` 支持 `sdtamt`（每页条数）与 `sdtpage`：实测一次可返回 1000 行；四张表全量 = **22 个请求 / 6.2 秒**（省 77、区 928、镇 7,364、邮编 7,360），行数与既有已知总数逐一吻合、分页无重叠。
- 列表行内含**本行及父级的可读名字**（镇行＝镇名+区名+府名；邮编行＝邮编+镇名+区名+府名），但**不含**父级原生 ID，也不含英文名。
- 四张表的英文名字段（`txtprovincesengname` / `txtdistrictsengname` / `txtsubdistrictsengname`）**抽查 12 条全为空**；邮编表无该字段。→ 逐条读详情换来的英文名实际不存在。
- **该租户对四张表有增删改权限**：列表页渲染 เพิ่ม/แก้ไข/ลบ（增/改/删）按钮，行上带 `data-allo="ved"`。所以"拉一次永久用"不成立。（未提交任何写入去验证服务端强制；行上的 `ผู้จัดทำ`+日期 是**制单人**戳，不是修改时间，不能当变更游标。）
- DMS **没有** CSV/Excel 导出：框架 JS（`mt.js`/`mtform.js`/`mtviewnc.js`/`mtview.js`）与全部 80 项菜单里只有单条打印（`btnctl-print` → `form.php`，`status=p`），无批量导出、无地址相关报表模块。

**地址在生产代码里的三个真实用途**（`intake/service.py`、`contracts/geo_matching.py`、`editor/edit_snapshot.py`、`snapshots/directory.py`）：

1. **名字→原生 ID 解析**：OCR 返回结构化 `province/district/subdistrict/zipcode`（`ocr_id_card.py` 的提示词确实要求这四个字段）；`resolve()` 做严格唯一匹配（去掉 จ./อ./ต. 前缀，多个匹配即视为歧义），匹配不到就留空并停止下沉——**不猜**。
2. **编辑器四级下拉**：`edit_snapshot.read_edit_snapshot` 只取"已解析那一支"的各级选项。
3. **差异卡可读名**：客户主档只有地址 ID，旧地址可能属于另一支，故按需补那一支的名字用来显示「旧→新」。

**关键结论：地址缓存不是安全的关键，也不是成本的必然**：

- 级联读取**代码已经存在**（`client_intake.list_geo`：省走 `cus/form.php`，区/镇/邮编走 `cus/component/listdistricts|listsubdistricts|listzipcodes.php`），并且**每次写客户都在用它做实时父链校验**（`intake/customer_geo.py:validate_customer_selects`，对不上即 `ERR_DMS_MASTER_UNMATCHED` 拒绝）。实测一条分支：4 个请求、4.22 秒，其中省那一次整页 4.16 秒、三个下级 fragments 各 **0.02 秒**。
- 也就是说：即使本地缓存陈旧，**写入也不会错**（提交前实时校验会拦）；缓存只影响"预览/下拉"。
- 全国 14,724/15,729 条（镇+邮编）只在"人真的要改到那一级"时才需要，而那时也只需当前分支。
   
**待用户选择的方案**（尚未实施）：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A（推荐） | 地址不再作为独立缓存组；解析/下拉/旧名显示走实时级联（每分支 4 请求，下级 0.02s） | 编辑/解析期需要 DMS 会话；DMS 连不上时地址只能只读显示现有值 |
| B | 只缓存省+区（77+928＝2 请求），镇/邮编实时 | 前两级秒开，第三四级要联机 |
| C | 保留全国缓存，改用列表接口刷新（22 请求 / 6.2 秒） | 保住"编辑零外呼"，仍有陈旧窗口 |
| 现状 | 15,729 次详情请求（约 33 分钟，一次超时全废） | 不推荐 |

### 本轮执行结果（2026-09-24，独立栈已在测试机跑通）

- `codex/testenv-per-app` 已推送 `951f80e`（含一个自己引入又修掉的 bug：`init_sites` 把产品名 `app` 覆盖成 Frappe 应用名，收尾步骤因此找不到服务），并合进本分支 `797b727`。
- 测试机跑 `PEARNLY_TESTENV_SRC=<本工作树> PEARNLY_TESTENV_IMAGE=<候选 digest> bash scripts/testenv.sh lab-start dms` 全流程成功：
  - 独立栈 12 容器全部健康，全为 `pearnly-testenv-dms-*`（自己的 MariaDB、两个 Redis、8 个 DMS 服务含 `dms-sync-worker`），无 keycloak；ERP/Cowork 栈不存在。
  - 自动收尾在真实流程生效：`testdms.pearnly.com migrate：ok`、`clear-cache：ok`；`dms-sync-worker` 因队列未配先重启、收尾写完配置后稳定（这条过去靠人工进容器补）。
  - 租户数据完整：已装 `frappe/pearnly_core/pearnly_dms`；`Pearnly ERP Endpoint=4`、`DMS Operator=2`、`Pearnly OCR Record=27`（历史识别记录随整站导入）、`DMS Read Snapshot=0`（缓存空，首次"更新数据"走完整读取）。
  - 域名按产品切换：`testdms.pearnly.com → <tunnel>.cfargotunnel.com proxied=True`，`*.pearnly.com → 107.155.65.74` 未动，`testerp` 未受影响。
  - 公开入口：经 Cloudflare 的 `/api/method/ping` 返回 200 且带 `cf-ray`，证明走隧道到测试机而不是线上。
- 剩余阻塞只有**账本站点**（OCR 付费路径需要）：导出侧 `export-test-tenant.sh` 已支持 `billing:billing.internal`；但 `import-test-tenant.py` 的 `PRODUCT_BY_GROUP` 没有 billing（第 51 行会 refusing），`run.sh` 的 app 校验与 `scripts/testenv.sh` 的 `lab_apps/tenant_spec/host_for` 也只认三个产品，需在这三处加 billing。必须整站导入以保持 `pearnly_billing_token` 与账本站点密钥一致（生产参考 `bootstrap-billing.py`）。DMS 其它验证不依赖账本，可先用。

## 最新续接：候选跑上 Windows 测试环境 + 按产品切租户（2026-09-24）

授权：用户"本机容器除了后台租户管理那个，其它的全部停掉删掉，不要占资源，然后搞到测试环境，测试环境不可以根据 app 切换对应的租户吗"。

### 本机容器清理（已完成）

- 保留 `pearnly-sample`（15 容器，含 `control-*` = 后台租户管理/控制面）及其网络 `pearnly-sample_sample`。
- 删除 8 组 46 个容器及对应项目网络：`pearnly-ci-business-sample`(9)、`pearnly-cowork-backend-lab`(3)、`pearnly-erp-entry-lab`(6)、`pearnly-erp-pos-lab`(3)、`pearnly-erp-pos-provision-lab`(4)、`pearnly-purchase-lab`(6)、`pearnly-stocktake-workbench-lab`(3)、`pearnly-tenant-lab`(12)；另删空的 `pearnly-auth-preview_default` 网络。
- 卷按"用户只说了容器"保留：仍剩 69 个卷（约 3.2 GB，2.8 GB 可回收，含上述 lab 的命名卷与 48 个匿名卷）；镜像 15.8 GB、构建缓存 4.5 GB 未动。**没有做全局 prune**；要清盘需用户再说一句。

### 候选镜像上测试机（已完成）

- 用仓库既定 `infrastructure/production/receive-image.py`（凭据只走 stdin、临时 `DOCKER_CONFIG`，机器上不留长期凭据）把候选拉到测试机：`ghcr.io/skin306152-star/pearnly/frappe@sha256:0b37fe9775c0b9f21da0d56e105144ff9ffa8e69e9811e827418c5e8400969a9`（revision `0b3a90c`）。测试机原先运行旧 `a11eb9e`。
- 从本 DMS 工作树跑 `bash scripts/testenv.sh up`（栈定义取候选那一份），24 个产品容器 + keycloak + mariadb + 双 Redis 全部 Up；站点读回：`dms/erp/cowork.test.localhost` 三空白站 + `testdms.pearnly.com`、`testerp.pearnly.com` 两个导入租户，均 frappe 16.33.0。

### 真实环境发现的两个问题（已处理）

1. **测试机数据库口令被重新生成**：`testenv.sh` 的 `.testenv-secrets` 取运行它的工作树根（`$root`），本树原先没有该文件 → 首次 `up` 生成了新随机口令 → keycloak 与既有卷里的口令不符、反复重启。处理：从原始工作树 `windows-testenv/pearnly/.testenv-secrets` 复制回来（SHA-256 一致），重写环境文件并重建 keycloak，现已 Up。**教训：`testenv.sh` 必须用持有原始口令的工作树运行，或先把该文件拷过去。**
2. **新队列在测试机没有生效**：`dms_sync` 队列靠生产发布脚本 `activate-product.sh` 调用 `configure-dms-runtime.py` 写进 bench 的 `common_site_config.json`；测试环境的 `up` 不做这一步，`dms-sync-worker` 因 `Queue should be one of …` 反复重启（新服务本身的定义没问题）。处理：在 `pearnly-testenv-dms-web-1` 内执行该脚本，`workers={'ocr': {'timeout': 600}, 'dms_sync': {'timeout': 420}}` 已写入，重启后稳定 Up。该配置落在 sites 卷里，只需做一次（`reset` 删卷后要重做）；把它并入测试环境 `up` 是待办。

### 按产品切租户（已实现、已使用）

- 原状：`lab-start`/`lab-stop` 与 `testenv-cloudflare.sh` 把 dms+erp 两个域名写死在一起切。
- 改动在分支 `codex/testenv-per-app`（从远端 main `3c5e4a3` 起，已推送，HEAD `4706323`）：`lab-start [dms|erp]`、`lab-stop [dms|erp]`、`switch-lab|switch-prod|status [dms|erp]`；不带参数与旧行为一致。隧道本身已同时带两个域名，所以只切一个产品不需要改隧道配置。推送前已本地跑同名闸门（架构、离线 DMS 27/27、provision/activate 套件、容量、词条与 diff 检查）。
- 本轮实际只切 DMS：`switch-lab dms` 后 `testdms.pearnly.com` 建了 CNAME → 隧道（Cloudflare 解析 104.21.28.241 / 172.67.147.231），`testerp.pearnly.com` 仍 `107.155.65.74`（线上，未动）。带唯一标记 `?codex-probe-dms=3` 的请求出现在测试机 `dms-frontend` 访问日志中（经隧道，302），证明 DMS 流量确实落在测试机。测试机侧 `smoke`：testdms 的 `/`、`/login` 302，`/api/method/ping` 200。
- 本机 DNS 有约 300 秒缓存：切完立刻用普通 `curl` 可能仍解析到线上 IP，用 `dig` 或 `curl --resolve` 可绕过。
- 边界：`lab-start dms` 的完整链路（线上导出 → 导入 → 切换）本轮**没有整条跑过**，只跑了 `switch-lab dms`；测试机上的 DMS 租户数据仍是上一次导入（约 19 小时前）。要最新数据需另跑 `lab-start dms`（会读线上导出并重建该测试站）。

### 仍未验收

- 真机/浏览器：LINE 菜单 1–5、编辑回卡、订车付款附件、拍照 OCR、真实 LINE 消息；四组完整真实采集（尤其全国地址整轮）；测试租户上的安装/升级。
- 边界提醒：DMS 测试租户现在由测试机服务，线上容器仍在跑但不再接到该域名的流量；LINE webhook 与 OIDC 仍走同一域名与线上 Keycloak。切回：`bash scripts/testenv.sh lab-stop dms`。

## 最新续接：候选镜像已产出 + 本机试验环境已清理（2026-09-24）

授权来自用户本轮原话“推，本机的试验环境删掉吧”。本轮只做推送、CI 构建与本机清理；**未部署、未切测试租户、未动测试机、未调用付费 OCR、未写第三方业务**。

### 推送与候选

- `codex/dms-five-minute-snapshots` **首次推送**（此前 31 个提交只在本地）。推送后本地与远端同为 `0b3a90ccd8768810ad5e2ff4f537598cd1f80d1b`。
- 推送期间远端 main 前进到 `e73e2d2a6cca7da5c3bbfde8469b9ba5bc4b8ce5`，只多两个文档提交（本机/基础设施健康检查清理），不碰代码，故未重新合并。
- **候选镜像**（`org.opencontainers.image.revision` = `0b3a90ccd8768810ad5e2ff4f537598cd1f80d1b`）：
  `ghcr.io/skin306152-star/pearnly/frappe@sha256:0b37fe9775c0b9f21da0d56e105144ff9ffa8e69e9811e827418c5e8400969a9`
  CI run `36011415839` 全绿：架构、离线契约、前端构建、候选运行时（含固定 Frappe 运行时的 36 项）、两个隔离 lab、运行时与前端资源、推送与 digest 上传。

### 首次推送暴露并修掉的三类存量缺口（本分支从未跑过 CI）

1. `scripts/test-dms-editor-snapshot.cjs` 仍在断言五分钟轮询/每日地址同步时代的文案。核对过：该测试与其输入文件都不在合并改动范围内，**合并前就是红的**。期望值改为已采纳的手动更新文案，现 5/5。
2. `scripts/test-dms-line-runtime.py` 两条：`booking_edit.load` 现在按票据把补齐后的复核资料写回（补打桩，测试本意是保留已清空字段）；建单成功后不再自动刷新员工销售全量（`c5f9c54` 已写进 `booking/service.py` 模块头第 4 条）→ 断言改为“不得被调用”。固定 Frappe 运行时 36/36。
3. `scripts/test-dms-snapshot-sync-native.py` 整套 5 条都在断言**已被收回的周期采集契约**：`adapter/snapshot_sync.refresh`、`adapter/geo_sync.refresh` 在没有手动 `request_id` 时返回 `{"ok": False, "retired": True}` 且不取 IO，`snapshots/schedule.sweep()` 返回 `{"queued": 0, "skipped": 0, "retired": True}`。该套件**已退休删除**；CI 的 lab 步骤改跑当前方案的 `scripts/test-dms-manual-native.py`。同等待遇的覆盖仍在 store / reads / manual / queue 原生套件与 `test-dms-snapshot-consumers.py`、`test-dms-query-snapshots.py` 中（后两者已显式断言 retired 入口不取 IO）。

### 本轮本地验证

- 一次性容器（固定 Frappe 运行时 + 挂载当前工作树源码）：`test-dms-line-runtime.py` 36/36；`test-dms-intake-queue.py`、`test-dms-query-output.py`、`tests/test_teardown_members.py`、`test-dms-query-snapshots.py`、`test-dms-snapshot-consumers.py` 全部通过（这些都是 CI 同一步骤/下一步骤里的命令）。
- 一次性临时 lab（本地 arm64 原生镜像 + 只读挂载当前源码，用完立即 `down --volumes`，无残留）：`test-dms-snapshot-store-native.py` 27、`test-dms-snapshot-reads-native.py` 17、`test-dms-manual-native.py` 12、`test-dms-snapshot-queue-native.py` 2，全通过。

### 本机试验环境清理（用户要求，本轮执行）

删除：`pearnly-dms-snapshot-lab`（3 容器 + 4 卷 + 网络）、`pearnly-dms-flow-lab`（3 容器 + 2 卷 + 网络）、`pearnly-dms-manual-preview`（容器 + 2 匿名卷 + 网络）；预览目录移入废纸篓 `~/.Trash/dms-manual-preview.uGyoJv`（可恢复）。复查：本任务 DMS 的容器/卷/网络/目录均为空。

未做全局 prune；未动其它任务的 lab（`erp-*`、`purchase-lab`、`tenant-lab`、`cowork-backend-lab`、`stocktake-workbench-lab`、`erp-pos-lab`）与共享镜像（`d5c8749…` 仍被 cowork 的 lab 引用，保留）。

### 保留项与下一步

- 保留：本任务树与分支（已推送、已构建候选）、四个 ignored 生成路径（`frontend/node_modules/`、`frontend/auto-imports.d.ts`、`frontend/components.d.ts`、`apps/pearnly_core/pearnly_core/public/pearnly-app/`）、旧 DMS 树 `dms-line-full-flow-fix`（仍有未提交文档；它引用的 `pearnly-dms-flow-lab` 运行资源已随本次清理删除，接手时按需重建）。
- 仍未验收：新版在测试租户上的安装/升级、四组完整真实采集与首次发布（尤其全国地址整轮）、LINE 菜单 1–5（含编辑回卡与订车付款附件）、同权限第三方回读与失败恢复、真机与真实消息。
- 下一步（在 Windows 侧）：先核对两个测试租户当前承接状态，再 `PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:0b37fe9775c0b9f21da0d56e105144ff9ffa8e69e9811e827418c5e8400969a9 bash scripts/testenv.sh up`。合并回 main 与正式发布仍需用户另行指示。

## 最新续接：对齐已确认新主线（2026-09-24，合并提交 ff6127f）

本轮只做“对齐主线 + 受影响本地检查”。**没有推送、没有构建镜像、没有部署、没有动测试环境、没有删资源。** 承接的是用户“把新主线对齐过来”的指示；正式 DMS 发布与推送仍需用户届时明确指示。

### 合并事实

- 方向：把本地主线（与真实远端 main 同为 `90b5cb31bdda546466e44791772fb342688aaa7e`）合进本 DMS 分支 `codex/dms-five-minute-snapshots`，合并提交 `ff6127f`（第一父提交 `f369435`）。
- **总交接里“真实远端 main = `3cee765`、本地主线领先远端 7 个提交、不能称作已推送”已过时**：本轮 `git ls-remote` 回读远端 main 已是 `90b5cb3`，Windows 测试环境那批已经推送并合入。本任务远端分支仍不存在（从未推送）。
- 合并前预演与结果一致：两侧共 125 / 134 个改动文件，真正重叠并冲突的只有交接点名的两个共享部署文件。

### 共享文件收口方式

- `scripts/production/activate-product.sh`：保留 DMS 的 `dms-sync-worker` 停止/重建与 `configure-dms-runtime.py` 前置步骤，同时采用主线的“只写本产品 `active-image-<product>.json`、旧 global `active-image.json` 保持不动”的固定记录方式。两侧改动合并后是同一段顺序：维护模式 → 备份 → 运行配置 → 迁移 → 重建 → 关维护 → 发布本产品 pin。
- `scripts/production/test-activate-product.py`：取主线的 `run_activation(failure, source_file, product, records)` 超集（含临时记录与失败不发布检查），并保留 DMS 的同步队列用例；主线的容器清单断言改为按产品计算，DMS 多一个 `dms-sync-worker`。
- `infrastructure/production/provision-agent.py` 自动合并后同时含主线的 `image(group)`（按产品 pin，缺失才回落 global）与 DMS 的 `services(group)`（DMS 追加同步 worker）；`scripts/production/test-provision-agent.py` 的 `restart_web` 断言同步按该规则更新。

### 顺手修掉的一处陈旧断言（与本合并无关）

`scripts/test-dms-editor-snapshot.cjs` 仍在断言五分钟轮询/每日地址同步时代的文案（`Address sync is delayed`、`5 minutes.*not draft revenue`），而这两句已被手动更新方案替换（`Older address data as of …`、`updated only when you click Read / refresh`）。用 `git diff` 核对：该测试及其输入文件（`frontend/src`、`apps/pearnly_dms`）都不在本次合并改动范围内，**合并前就是红的**，属本分支从未推送、CI 从未跑过的存量缺口。已把期望值改为当前已采纳文案（不是放宽或删除断言），现 5/5 通过。

### 本轮实际跑过并通过的检查（合并后的工作区）

- `scripts/check-architecture.py`：0 违规；`scripts/check-dms-offline.py`：27 套全通过（含 snapshot reader 54、catalogue 26、company schema 8、geo 14、geo resume 4、连接 13、顾问 15）。
- `tests/test_architecture_guard.py`、`tests/test_native_desk_contract.py`、`tests/test_product_feature_catalog.py`、`tests/test_queue_targets.py`、`scripts/test-dms-call-contracts.py`、`scripts/test-dms-customer-identity.py`、`tests/test_welcome_email.py` 通过。
- `scripts/production/test-activate-product.py` 12 项、`test-provision-agent.py` 16 项、`test-dms-runtime.py` 2 项通过；`scripts/check-production-capacity.py` 12 项通过。
- node 侧：`tests/test-native-desk.cjs`、`tests/test_pos_lifecycle.js`、`tests/test-roster-selection.cjs`、`tests/test-records-selection.cjs`、`scripts/production/test-issue-actions.cjs`、`scripts/test-dms-editor-snapshot.cjs` 通过。
- `scripts/i18n-build.py --check` PASS；`bash -n` / `py_compile` 语法通过；`git diff --check` 干净。
- 环境注意：本机默认 `python3` 是 **3.9.6**，`X | None` 语法会让 `tests/test_welcome_email.py`、`tests/test_product_feature_catalog.py` 抛 TypeError；改用 `python3.12` 即通过（**本地主线同样如此，非本次引入**）。`scripts/test-dms-query-snapshots.py`、`scripts/test-dms-snapshot-consumers.py` 需要镜像内 Frappe，本机不能直接跑。

### 下一步与授权边界

文档化的候选路径是：**推分支 → 手动触发 `build-production.yml` → 记下 digest → `PEARNLY_TESTENV_IMAGE=<digest> bash scripts/testenv.sh up`**（见 `docs/development/test-environment.md` 的运行流程）。推分支是外部写，**本分支至今从未推送，本轮没有该授权**，因此固定候选镜像尚未构建。本机原生 arm64 lab 仍以只读方式挂当前工作树源码，合并后的逻辑在 lab 里已生效，但那不是能交给测试机的候选 digest。

仍未验收（与上一节结论一致）：四组完整真实采集与首次发布（尤其全国地址整轮）、新版在测试租户上的安装/升级、LINE 菜单 1–5（含编辑回卡与订车付款附件）、同权限第三方回读与失败恢复。

## 最新续接：单次真实 OCR 与首次绑定说明（2026-09-24，基线 410b87b）

用户明确允许使用现有测试附件做一次真实识别，并要求等待文案改用三个英文句点。仅该次付费 OCR 获得新增授权；不合并、不推送、不部署，不发 LINE、不写第三方 DMS 业务。文字归 DMS 产品词条，没有修改绑定流程、识别模型或共享计费策略。

- 首次绑定不会自动建立四组完整缓存。连接向导登录并读取部分旧下拉主档作为连接校验，不发布车辆/客户/地址/公司完整快照；保存连接与操作员同步亦不派发完整采集。绑定后仍需管理员点“更新数据”，首次无成功资料时四组默认全选，确认提交才开始。LINE 绑定只处理会话及既有菜单同步，不触发全主档索引。已有任一成功组时选框默认不勾选，不能把“打开弹窗”当作完整读取。
- 中文等待文案改为“数据更新中...”；产品源词条、生成 zh.csv、原生 Dialog 回归与当前架构/ADR 引文同步，历史记录保留原样。

### 单次真实识别证据

- 输入为仓库已有合成证件 `samples/ocr-id-card/images/id_card_clean_002.jpg`（43,437 B，SHA-256 `0bd08780f6695a47ff88a086aef4a7ca81c43e0005ea62b11f58db16dfc24a4e`），不是聊天中的真实身份证照片。核对同目录 ground_truth 的 15 项字段，不输出身份内容。
- 执行环境是既有云端 `pearnly-production-dms-web-1` / `testdms.pearnly.com`，调用已部署的原生 `ocr_service.recognize_id_card`，不是在本地 lab 部署新版本。候选与线上 Core OCR、DMS 身份证提取模块文件 SHA-256 相同（分别为 `a518838251b387c9fc7d889af9dc3acacf9c90732be674b8f8b70829045063a4`、`780bb73831937bae390570d1a1fa9013dba03bacd6ddbf1306bb4c983c81f3bf`）；线上 service 为 `67f154d5b608da844fd67481c5920eca7b39700d34d754cba644f542cddc4378`，本地新版额外计时未部署。
- 一次性进程只接收 stdin 图片并作内存分段计时；原有 Vertex enterprise / `gemini-3.8-flash`、提示词、图片、容量准入与计费保持不变。固定网络出口仅 Google 认证、该模型请求和既有计费 status/charge，第二次模型请求及入队被拒绝，没有自动重试、LINE、DMS 或磁盘脚本/图片副本。
- 唯一参考号 `idcard:single-probe:20260924:405f1743a15b47bc95f23dfda9c9843a`。模型 HTTP 200，实际请求 1 次，15/15 字段与合成 gold 一致，缺字段 0、needs_review=false。计费回执 ok，扣费 1.50 THB；本 Site 按精确参考号回读 OCR Record 1 条、status=ok、pages=1、charged_thb=1.50、归属一致。此原生付费/识别审计保留，不因测试删除。
- 总服务耗时 8,275.88 ms；其中识别段 7,594.74 ms（包含认证 462.76 ms、模型及容量闸段 7,124.18 ms），余额闸 150.21 ms、扣费 219.39 ms、记录 268.18 ms、端点解析 39.79 ms、图片准备 0.01 ms。分段有嵌套，不能相加。模型返回 prompt=1,679 / candidates=228 / thoughts=855 / total=2,762 tokens。
- 本次主要等待在模型调用段（约 86%），不是 DMS 主档读取；但该段包含 HTTP 与容量检查，不能据此分离供应商纯推理时间。这是单张、冷认证进程的一次测量，不是平均/P95，也不包含 LINE 下载、队列等待和回复；不宣称 OCR 已提速或完整 LINE 链路已验收。

### 本批验证与交接

连接页 JS 20/20、词条生成一致性、前端词条严格检查、架构 0 违规及 diff 通过。全仓 strict i18n 仍为已登记的 487 条发现（377 条登记基线），本批没有改其扫描的 Python/JS/JSON 产品源码，不增加忽略、不宣称已修复全仓文案债。未改业务实现，不重跑上批已通过的全套 DMS/Site 测试。

本地原生预览实际显示“数据更新中...”和全屏浅色等待，取数仍为 sidecar 内存模拟，不连接 DMS。清除本任务 Site 缓存后仍被原 serve 进程词条缓存影响，因此重新核对 context/project/mount 后，仅重启 `pearnly-dms-snapshot-lab-bench-1` 内原页面 serve；未重启容器或 worker、未消费队列、未修改网络/生产配置。用户预览地址保持 `http://127.0.0.1:18118/desk/pearnly-connect`。

main 与真实远端 main 本批回读均为 `57039ca15595641932f053fd34c49d4e45dba964`，主目录干净，任务远端分支不存在。任务树与 `codex/dms-five-minute-snapshots` 继续仅本地保存、未合并/推送/部署；成果未入主线且仍被专属 lab/预览挂载，不具备删除条件。原实验室三容器/四卷、preview sidecar/网络/目录及既有生成资源继续按下节用途交接保留，解除条件为用户结束预览或授权本任务发布/收工后逐项核对。本次没有新增工作树、容器、卷或临时落盘原件；保留此次原生 OCR/计费审计。全国地址真实整轮、首次四组真实发布、候选 LINE 菜单 1–5 与第三方业务闭环仍未验收。

## 最新续接：旧依赖收口与 OCR 可观测性（2026-09-24，基线 880fd04）

用户在核对旧逻辑遗漏与 OCR 速度后要求“处理好”。本批修复归 DMS 的 editor/operators/masters/ocr/intake 域；保留此前仅本地实现、容器验证、不合并/推送/部署的边界，不发真实 LINE、不调用付费 OCR、不写第三方业务。不是开启新一轮生产迁移或扩大此前短时只读核对授权。

- 客户编辑 `load/save` 显式只取客户及地址，解除车辆/公司缓存的无谓前置依赖；订车编辑保留全部必需主档。缺缓存、当前身份、票据、nonce 与发卡失败恢复旧卡保护不变。
- 后台顾问下拉只读 `company` 快照；保存非空顾问才经原有管理员只读会话窄读顾问 selector 全部分页，按实时 ID 取名，失败不使用缓存旧名字。返回前当前读核对同一启用管理员、唯一主连接与凭据范围，不提交调用方事务；保留原生 Administrator 隐式角色语义。只登记实际 operators → snapshots/adapter 单向依赖，移除已无调用的 operators → masters。
- 订车顾问解析本来正常调用均显式传本地主档，但无参辅助分支还会 `get_masters(force_refresh=True)`；本批删除这一回退及导入，无主档明确返回不可匹配，不外呼、不采用旧配置。最终客户写入、订车提交及保存顾问分配的实时校验继续保留。
- DMS 身份证增加私有分段计时：工作单可执行后的等待、loading、下载/已缓存输入、端点、余额闸、图片准备、识别调用、扣费、记录、本地查重、LINE 回覆。仅固定阶段、毫秒、状态与内部工作摘要，**不记录账号、身份证、电话、文件名、图片、模型结果、错误正文或凭据**；不新建产品 UI/DocType，不改共享模型、计费幂等与重试策略。默认非开发模式过滤 INFO 的问题已在真实 Frappe 发现并修复，只启用此专用 logger 的 INFO。
- 耗时含义：`recognition` 包括认证、模型运输/检查点回放、解析，不等于供应商纯推理时间；`ready_wait_ms` 不含刻意的退避等待及 webhook 入队前耗时，`attempt_ms` 是本次 DMS handler 时间，不是整单总生命周期。查重阶段仅记录同一次图片处理中已带电话的自动查重，不覆盖另发一条电话的独立消息。`completed` 只表示 handler 返回，不代表第三方建档。重试按 attempt 分开，不能据 attempts 推断重复付费。没有通过缩图/换模型冒险改变识别质量，**本批尚无真实 OCR 提速证据**。

### 本批验证与未完成项

- 顾问旧无参回退新增回归：旧版 6 个断言失败，新版 34/34 通过。客户仅两组的实际 editor 回归旧版失败、新版 consumer 25/25 通过；额外 intake 23/23 通过，其中修正上一批遗留的缺 `completed_at`/独立分组测试夹具，未放宽生产读取校验。
- 后台顾问 15 项零 IO 检查通过：公司缓存唯一来源、缓存缺失/范围失效不外呼、仅原生顾问分页、删项/畸形/截页拒绝、非管理员/停用/中途撤权、主连接与凭据变化拒绝、原生 Administrator。
- OCR 新计时 9 项模拟通过；既有供应商退避/未知结果不重放、已完成检查点和持久任务恢复 5 项通过。计时或日志失败不改变结果、异常、账单引用或队列签名；模拟毫秒只证明归因，非性能基准。
- 全国规模合成地址 15,785 条、162 个有界批次，完整原生字段/JSON 断点重启、失败片重读、账号忙保留断点、最终 ID 漂移拒绝发布等 4 项通过。泰文/英文与父级标签样本的最大私有断点 5,737,877 B、最终包 6,373,251 B，均通过实际 8 MiB 序列化限制；未放宽限额。该证据不代表真实全国 15,729 条已完整采集或实际包一定不超限。
- 同一固定 lab 的真实 Site 定向验证 6 项通过：客户编辑 2 项（仅客户/地址、当前 binding/session/CAS、发卡失败恢复旧 nonce）；顾问 3 项（实际 roster 保存实时姓名、独立 MariaDB 连接撤权/轮换 Auth 加密凭据均拒绝）；OCR 1 项（原生 Work UTC/attempt、真实 bench 与 Site 两个日志实写且无原件/身份内容）。所有第三方出口替身阻断。旧 `check_roster_runtime.py` 只同步 fixture 并编译，不到其硬编码 auth-preview Site 运行。
- 最终架构 0 违规，离线业务 333 条及 reader 54/catalogue 26/company schema 8/geo 14/geo resume 4/连接 13/顾问 15 项通过，diff 检查通过；两个新增纯套件加入显式离线门禁。未改前端/词条，不重复全仓前端构建；上批全仓 strict i18n 存量失败仍未声称解决。

地址故障测试边界：读取器验证“失败片未污染上一断点、从该断点重放不漏”；真实采集编排遇完整性/协议错误仍失败、丢弃不可信半包、保留旧成功代际，不承诺所有网络中断都自动续跑。账号忙保留私有断点，需用户重新点更新，不恢复五分钟轮询。

仍需后续有授权的真实首次四组缓存建立（尤其全国地址）、新候选下的 OCR 分段实测和 LINE 菜单 1–5/第三方 DMS 闭环。此前线上总耗时快照只说明队列至处理完成约 8.8–23.6 秒，不能归因全部来自模型；本批未重跑付费识别。菜单 3 是真实 DMS 登录，菜单 4 修改本系统保存的 DMS 凭据，菜单 5 仅用户明确刷新的本人查询；不能把这些必要实时动作说成“所有网络请求均取消”。

本任务树/分支继续交接保留：独有本地成果及专用 lab/预览挂载仍在用，未获得合并/部署授权。main 与真实远端 main 本批再次回读均为 `57039ca15595641932f053fd34c49d4e45dba964`，任务远端分支不存在，主目录干净。未创建新工作树/分支，未动 ERP/Cowork；本地任务提交号以本节所在提交为准。原资源解除条件仍为用户结束预览、授权发布或本任务收工时逐项核实，不删除业务/审计数据。

本批资源回读：快照、主连接、活动合成 User/Operator/Endpoint、绑定、会话、合成 OCR Work 与指定拒绝 Error Log 均 0；队列每个 ID/顺序仍为 default=9、long=3、dms_sync=0 原基线，未消费历史任务。禁用合成身份审计由历史 86 套增至 94 套，保留在本任务实验室；安全计时日志也留于专属日志卷。三 lab 容器/卷与原预览资源保留用于本地复验，bench 仅原 serve，无 worker/scheduler/test 进程；未新建容器/卷、未启停其他运行组。唯一任务树和已有生成资源按上节用途保留，本批无额外工作树/分支/临时文件待删，属于交接保留而非全仓清理。

## 最新续接：四组完整主档（2026-09-24，基线 e4428ee）

用户最新明确文案为“更新数据”和“数据更新中。。。”；口述“三个”但实际列举四项，按四项实现：车辆主档信息、客户资料、地址目录、公司资料。用户另行批准 DMS-TEST 管理员只读目录/字段/分页核对；**依然不合并、不推送、不部署，不写第三方业务资料，不发 LINE、不做付费 OCR**。此节替代下方旧三组入口；历史记录不删。

- 范围不是旧 `masters` 改名。新增按原生完整目录/表单读取的车辆、公司、客户辅助和地址采集器；原始资料私有，前端不展示新增字段。完整范围/具体原生表/真实数量与字段差异见[协议记录](2026-09-24-dms-snapshot-protocol.md#四组主档范围与完整表单核对同日手动方案追加)。
- `client` 域含目录 schema、有界 count/page/form/final recheck、动态车型赠品/活动/贷款及促销适用车型；`adapter` 域分批编排与发布；`snapshots` 域存储/消费；`connections` 固定四组管理员 RPC；无新共享底座依赖或产品入口。
- 车辆包保留停用车型及所有颜色，LINE 仍只给可用车型。公司包含经营主体/公司/组织/员工非凭据业务字段/银行账户/融资及原生订车选项。客户包完整原生 51 控件/64 业务投影及称谓/分支/银行目录。地址读完整原生字段含英文名，校验全父子链后投影本地树；未知字段、断页、空错误页、超限均失败保旧，不静默少读。
- 老 `masters` 不再接受手动请求，带旧 token 也不能启动旧 collector；历史快照保留但不当作新组使用。新字段目录首次须手动更新四组。颜色只依赖车辆组、称谓只依赖客户组；订车与编辑组合各组真实代际，不把局部更新冒充全体刷新。
- 原生全屏等待及四组选项已在本地预览实见。实际取数仍为 preview 代理模拟；本地 native Desk 验证真实四任务注册/队列序列化，但终态模拟，不运行真实 collector、不代表 DMS 业务验收。

### 验证与状态

- 本地检查：旧离线业务 333 条、原快照读协议 54 项、新目录采集 26 项、公司 schema 8 项、完整地址 14 项、连接 RPC 13 项通过。新零 IO suite 已加入显式离线门禁。组合编排/车辆及公司 LINE 投影 9 项通过。
- 本任务固定容器与真实 Site：consumer 20、手动请求 12、原生读取/编辑/查询 15 项通过；另新增 store 两项（四组独立发布、完整地址字段保存且错误父级拒绝）通过。原生 Desk 四组选项/入队/冻结/离页/同 ID 返回/结束解冻通过。第三方出口由替身阻断，不将该层称为真 LINE 测试。
- 架构 0 违规；词条生成一致性、前端取词及 diff 通过。全仓 strict i18n 仍为之前记录的存量失败，不扩大忽略。
- 只读实测：早期候选拒绝尚未登记的活动适用车型、银行误列税号字段，按实际表单补齐后，车辆与公司全部主档/附表、客户辅助三目录均真实全量通过。车辆及公司 LINE 选项投影通过；公司读取含投影耗时 25.884 秒。过程不曾发布半包。全国地址 count 为 15,729 条且四种表单样本解析通过；**全国详情整轮及真实缓存发布尚未做**，不能声称全国完整采集验收。
- 复核修正：四类可手填银行目录的合法空值是 HTTP 200/零字节，仅对已核实四个原生 selector 兼容，空白/HTML/null/公司收款空体仍拒绝；不改变实时付款校验。客户续读断点新增完整 65 字段（含 customer_id）、字符串类型、身份及最终覆盖检查，残缺断点不能发布为“没有客户”；新增回归旧版失败、修后通过，未增加外部读取。
- 最后一轮客户全量只读：40 条列表、40 条完整详情、每条 65 字段，最终列表/计数复核通过，读取块 14.083 秒；仅内存解析，不发布生产缓存、不输出原始客户字段。

### 资源与交接保留

任务树/分支仍为 `/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly` / `codex/dms-five-minute-snapshots`，只保存本地；main 与真实远端 main 均回读 `57039ca15595641932f053fd34c49d4e45dba964`，任务远端分支不存在。ERP/Cowork 工作树未改。本批未创建额外工作树/分支；未合并、未推送、未部署。

本地预览 `http://127.0.0.1:18118/desk/pearnly-connect` 及其既有 sidecar/目录、专属 lab 三容器/四卷继续保留给用户看界面和复验；仅 task bench 的 serve 进程重启加载中文词条，未重启产品运行组/worker。用户结束预览或下一次本任务收尾时复核；预览侧车自动过期不等于 lab 卷可删除。原生测试队列最终回读 ID 与顺序均保持 default=9/long=3/dms_sync=0 原基线，未消费旧任务；fixture 快照/主连接/活动合成账户/会话均为 0，历史累计 86 套停用合成身份审计保留于独立 lab（非本批新增 86 套）。

补充收尾：一次性只读核对脚本目录 `/Users/skin/.codex/dms-master-audit.mtpxI9` 已确认无占用，移至 `/Users/skin/.Trash/dms-master-audit.mtpxI9`，可恢复；没有原始业务响应文件。预览 sidecar 的匿名 `logs/sites` 卷 `4726d3618fad58575de3f35b4e61217f7d164deb306acf62139dbd3baa58c825` / `40d8ec9ad6b1486e8bc936cd0ad025a2c4c631ef7bd06b86709625c245a1b1e2` 仍被该容器引用，随预览一起保留，下次结束预览时按容器归属复核。线上既有失败截图机制在本次时间窗内有 `testdms.pearnly.com/private/files/dms-shots/01-081028-company-bank-master-failed.png` 和 `01-081534-company-bank-master-failed.png`；没有下载或输出图片内容，不仅凭时间推断归属后自动删除运行审计，后续本任务发布前按日志保留要求复核。无生产源码覆盖、配置修改或缓存发布。

后续实际部署前须取得授权、对齐当时主线、重新构建候选和补真实首次四组采集（尤其全国地址整轮），再验收 LINE 交互及实时提交。当前实现/本地验证与真实全流程验收是不同层。

## 当前工作包

用户已明确采纳并要求实施。取代本日五分钟全量同步及每日地址同步的触发方案；旧记录保留为历史验证，不代表当前方案。仅本机实现和隔离验证，不合并主线、不推送、不部署、不登录真实 DMS、不发 LINE、不调用付费 OCR。

- 主任务树：`/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly`，`codex/dms-five-minute-snapshots`，续接基线 `658b8399fbf5d8cfe35842807241f448223bcfb5`。
- 目标 main：`57039ca15595641932f053fd34c49d4e45dba964`，开工干净；共享 ADR-009 已在基线。此次是用户要求的新设计，不是从未核实的旧生产版本移植功能。
- 独立子树 `dms-manual-connect`、`dms-manual-conflicts`、`dms-manual-query`（对应 `codex/` 同名分支）均由上述基线开始，归本任务；完成后由主任务整合并逐项清理。其他 ERP/Cowork 工作树不动。

## 合同与归属

- App：`pearnly_dms`。snapshots 管本地完整资料与显式读取请求；adapter/client 管第三方有界读取；connections 管管理员手动入口；query 管本人查询；intake/booking/approval 管实时写入及差异确认。
- 连接页原生“读取并更新资料”：仅 DMS Admin/System Manager；固定本站主连接；可选择基础资料、客户、地址。状态查询没有外部 IO。未配置连接/权限不足服务端拒绝。
- LINE 菜单 1/2 预览、编辑、下拉只读本地；菜单 3/4 仍真实登录/改凭据；菜单 5 明确一次刷新当前本人查询，不后台扫描所有员工。无数据不能伪装零结果。
- 取消资料周期采集和订车后的全量销售采集，保留业务恢复任务。旧持久化队列入口保留无副作用兼容，不能凭旧任务重新启动采集。
- 成功客户写后只回填独立验证过的一条，不能冒充全库刷新；失败不能导致外部写入重试。
- 新建时实时遇到已有客户进入差异确认；编辑比较基线、用户意图与实时值，同字段冲突重新确认，未修改字段保留当前值。幂等恢复与业务碰撞分开。
- 后台读不抢占实际提交；账号互斥保留，分块释放账号。第三方人工登录及最后复核与提交之间的竞态无法由我方完全消除。
- 无自动得知第三方修改的能力，不宣称 webhook/增量同步；完整分页校验也不等于第三方原子快照。旧好包显示真实时间，身份/权限/凭据变化仍拒绝。

## 本轮实现与证据

本地任务集成先后包含 `4fe39ce` 连接页、`647a9d6` 交互回归、`c5f9c54` 客户实时冲突/写入证据、`aaef3d8` 手动请求/分组快照/旧调度退役、`747e795` 单条件查询、`fbad447` 原生查询回归和 `fb9b7bd` 删除旧周期文案。后续补充验证/修复在本记录末尾登记。

- 核对并使用既有任务实验室：context `colima-pearnly`，project `pearnly-dms-snapshot-lab`，Site `dms-snapshot-lab.localhost`；四个专用卷、internal 网络、无宿主端口、五 App/脚本来自本任务只读挂载。未动共享样板。
- 镜像固定 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Frappe 16.33.0 / Python 3.14.7。真实 Site migrate 成功，新增私有 refresh_request；原生 scheduler 中旧读档任务不再启用，三个业务恢复任务保留。未启动 scheduler。
- 离线门禁：333 条业务断言 + 46 个纯采集测试通过；architecture 0 违规；翻译包一致性和前端词条覆盖通过。全仓 `check-i18n --strict` 仍失败：main 498 条、候选 487 条扫描发现，377 条已登记中文；未扩大忽略列表。属于已有全仓文案债，不伪报全绿。
- 原生存储 25 场景通过：完整发布、旧包保留、凭据换代、员工隔离、双连接并发、独立写穿不提交业务事务。旧测试从合并目录改为独立客户包，删必需 customers 字段仍拒绝；年龄改成软提示，显式 max_age 限制仍覆盖。
- 手动请求 11 场景通过：冷态不伪装空、GET 零派发、原生队列去重、RPC 回滚不丢已独立登记工作、派发失败可见、有限续读、前台忙需手动重试、旧终态任务重投无副作用、发布 token 行锁、认证拒绝跨组保护、完整性失败清半成品但保留成功代际。
- 原生队列 2 场景通过：真实 dms_sync burst 只消费 UUID 合成 now 任务，long 哨兵仍排队；旧入口无 token 不登录、不排队；恢复任务保留。
- 原生交互 15 场景通过：实际 Site lookup/editor/全部订车选项只读本地；菜单 5 经真实 manual.request、Redis job 序列化和 execute_job 后只发布当前条件；外部读使用假 transport，双连接撤权/换密码阻断发布，旧代际不变。
- 菜单 5 独立查询 40 场景、LINE 出口 11 场景、查询卡片 32 断言通过。连接 RPC 权限/元数据 12 场景、原生 JS 生命周期 4 组通过。
- 真实本地 Chromium/原生 Desk 实测：手动按钮、Dialog 选定基础资料而非全选、一次 POST 立即关闭且无长时间冻结、重复点击同请求 ID/一个队列任务、切换页面停止 GET 轮询。页面按原生 FieldGroup 两列显示状态/数量/实际时间，已截图检查。测试浏览器显式 locale=en-US，避免容器默认非 BCP47 locale 令上游 AltShortcutGroup 抛错；未改 Frappe/共享 UI。
- 一次浏览器测试 harness 在异常关闭浏览器后阻断 fixture cleanup，已按核实的 `EP-00130` / `snapshot-818d91eec48d4b50a3cb7787c567e1aa` 精确清主连接/快照，停用其两个人工测试账户；随后修复 harness 的 finally 并回读。无用户业务资料。

本轮没有真实第三方读写、LINE 消息、付费 OCR、生产构建/部署或真机业务验收。无需把旧真机测试当成本候选通过。

## 状态与下一步

本地实现和本轮验证完成，交接保留，未上线。最后业务补包 `8a3883f`、`e62b7e9` 已整合；业务内容固定在 `c2ca3f47dd1956c6d46be37274cc45c91596b323`（后续仅记录更正）。最终组合 consumer 19/19、客户流程 15/15 通过；纯客户 client 三方比较 11 场景、旧身份 31 场景、旧 intake 23 场景亦有对应包验证。

客户派发原生 7/7：真实 MariaDB/Redis 和两个独立进程，稳定同源/CID/旧新证号共同拦截未知重放；同请求已证实成功可恢复；失败前不留已派发证据。审批派发前重检当前绑定/管理员/认领，失去 claim 时不能写入或发送旧回执。旧 RR 与跨连接 claim 变更触发 MariaDB 1020 后安全拒绝，回滚后证实新认领未被覆盖，**不宣称旧 RR 一定成功锁读**。

原生主连接测试 fixture、CW marker、审批记录、快照均已精确清除；本轮 migrate/一次失败 harness 清理新增的 6 个原生 housekeeping job 按已核实 ID 删除，未消费历史队列。最终队列回到既有 default=8、long=3、dms_sync=0；合成账户停用审计保留于隔离实验室，无用户数据。

### Git 与资源收尾

- main 与远端实际 `refs/heads/main` 均为 `57039ca15595641932f053fd34c49d4e45dba964`，主目录干净且未改。当前仅本机授权，因此未合回 main、未推送、未部署，也未新建 PR。四个本任务远端分支均不存在。
- 三个子包先 cherry-pick 到本任务集成树，核对内容等价（原生查询脚本保留父树独立 seed 修改），再以 ancestry-only merge 登记已整合来源。已正常非强制删除 `dms-manual-connect`、`dms-manual-query`、`dms-manual-conflicts` 三个工作树及对应 `codex/` 分支；删除前核 tracked/untracked/ignored、符号链接、进程及所有容器挂载，均无残留或占用。
- 保留主任务树 `/Users/skin/.codex/worktrees/dms-five-minute-snapshots/pearnly` / `codex/dms-five-minute-snapshots`：用户尚未授权本方案合并/推送/部署，成果唯一整合位置；下一次开始本方案部署前复核 main 增量及授权。
- 保留该树旧批生成的 `frontend/node_modules/`、`frontend/auto-imports.d.ts`、`frontend/components.d.ts`、`apps/pearnly_core/pearnly_core/public/pearnly-app/`：本任务候选编辑器构建和后续复验所需，未沿链接删除共享依赖；正式候选构建/主任务合并时复核再生清理。
- 本轮测试专用的 browser serve 和三个 `pearnly-dms-snapshot-lab-{bench,db,redis}-1` 容器收尾停止；保留该任务 sites/db/redis-data/logs 四卷及原有固定镜像，供新方案后续本机复验。解除条件：用户确认不再复验或授权对应实验室收工；不得因停止容器删除数据卷。其他 ERP/Cowork/runtime 未动。
- 本轮一次性无网络容器已自动删除，三个临时子树无容器引用。已查看的宿主浏览器截图 `/tmp/pearnly-dms-manual-evidence.8vuTtH` 移入废纸篓，可恢复；复验脚本保留，容器内截图随测试容器生命周期保留。

后续若要上线：先取得授权并合并当前主线增量，制作固定候选与备份/恢复方案，再部署/migrate 和启用专用 worker；之后由管理员首次手动建缓存，做真实 LINE 菜单 1–5 与第三方 DMS 对账。此段是下一步，不代表已执行或已获生产授权。

## 连接页精简与中文预览（2026-09-24，续接 a3d4b2a）

用户看过快速预览后明确要求：不展示更新后的资料和状态字段，执行时用原生全屏白色等待效果，预览使用中文。本节替代上文“18 个常驻字段／入队立即解冻”的旧 UI 验证结论；采集、权限、缓存和实时提交协议不变。

- 归属仅 DMS `public/desk/connect.js` 与产品词条；保留原连接信息和原生选择 Dialog，移除资料状态、进度、数量、时间、错误码等 18 个字段。使用现有原生 `frappe.dom.freeze/unfreeze`，没有自建遮罩或修改共享底座。
- 入队后按 dataset + request_id 等待本次所选组的终态，全部成功才提示成功；失败优先于账号忙。旧成功、ID 变化、缺项、RPC 异常均不误报成功，并解除遮罩。离页解冻并停轮询，返回只查询本地活动状态恢复等待；空闲不持续轮询，不自动发起 DMS 采集。
- 定向真实 JS + 原生 Dialog helper 测试 18/18 通过（隔离镜像 Node 24.20.0）；包含失败/等待混合结果、空请求 ID 失败、重复提交与跨页面迟到响应。架构 0 违规；离线业务 333 条及采集器 46 测试通过；词条/前端翻译键检查通过。全仓 strict i18n 仍为既有 487 条发现（377 已登记），未增加忽略。
- 本地 Codex 浏览器实际打开原生 Desk，中文、无资料状态区、原生选择框、全屏浅色等待和结束解冻已验证，读取数据/队列由预览代理内存模拟，不是第三方业务验收。旧原生 Site 队列浏览器脚本已更新契约并做 AST 检查，本轮未运行该脚本，避免给快速预览额外建立 fixture。
- 预览为 `http://127.0.0.1:18118/desk/pearnly-connect`。精确临时目录 `/Users/skin/.codex/dms-manual-preview.uGyoJv`；sidecar `pearnly-dms-manual-preview`、转发网络 `pearnly-dms-manual-preview-host`，只向宿主回环发布端口。复用本任务三个实验室容器，bench 内部 8000，无 worker/scheduler。所有业务 API 都被拦截/模拟，真实外部 DMS/LINE 未连接。
- 为用户查看而交接保留：上述预览目录、sidecar/网络、三个 lab 容器在用；下次用户结束预览或本任务收工时复核并停止/清理，保留 lab 卷。sidecar 两小时自动退出不代表实验室容器自动停止。仅合成实验室 Administrator 语言设为 zh，developer_mode 设为 0；生产与其它样板设置未动。
- 仍只在原本地任务分支保存，未合回 main、未推送、未部署。本批不需要重建 Vue 编辑器或发布镜像；原任务树/构建资源继续按上节原因保留，待用户确认界面或授权后续阶段。

## 增量同步（2026-09-25）：操作动作顺手把使用记录带回来

用户把方案收敛成**唯一一条路**，并要求此前的手动触发、每日定时**全部删除**：

1. 绑定主账号时读一次全量，做缓存底座（已有，见上一节；白屏覆盖跟真实进度动）；
2. 之后**每次操作员真实使用 DMS**，顺手读原生"使用记录"（`eventlog`）的增量，
   只补读被点名的记录。

本节记录这条路的实现与实测。删除那部分已在 `5952a50` 完成，不重复。

### 实现落点

| 位置 | 做什么 |
| --- | --- |
| `client/eventlog_reader.py`（新） | 流水协议：`ftd=<模块>||2`、计数（不传分页）、分页；严格解析 7 列行，形状不对即失败 |
| `adapter/change_sync.py`（新） | 判断规则：模块水位（事件总数 + 已处理流水号）、按记录补读、整组重读登记、并发互斥、硬预算 |
| `snapshots/store.py` | `changes` 行（复用 `staging` 列）存水位；`change_state` / `merge_change_state`（按更靠前合并、独立短事务）/ `customer_index`（身份证号→客户号，一次解析） |
| `client/snapshot_reader.py` | `search_customer_by_identity`：身份证号 → 客户号（找不到、有歧义、被截断一律放弃） |
| `adapter/operations.py` | `run_logged_in(..., catch_up=True)`：同一个会话里跑完动作再跑同步；失败只记日志，绝不改动作结果 |
| `connections/snapshots.py` | 绑定成功先排一个**基线**任务（`adapter.change_sync.seed`），再排四组底座 |

`catch_up=True` 的入口：客户建档/改档（`intake/service`）、订车（`booking/service`）、
顾问列表（`operators/advisors`）、主档与颜色（`masters/masters_cache`）、菜单 5 查询
（`query/snapshot_sync`）。后台恢复类任务（`booking/recovery`、`booking/delivery`、
台账采集器）**不**带这个开关。

### 判断规则（写死在代码里）

- 读不到计数 → 整轮停手，**绝不把"读不到"当成"没变化"**（数据端点没有权限校验，
  随时可能被收紧）。
- 点不到具体记录（标识为空、动作不认识）、删除、积压超过页数上限、总数对不上账
  （可能被保留窗口截断）→ 不猜，登记一次**整组重读**（走既有队列，不阻塞操作员，
  以 `Administrator` 名义登记）。
- 整组重读没落地之前水位**不推进**，`dirty` 标记一直挂着，冷却 600 秒后再登记一次；
  数据集真的刷新过（`completed_at` 晚于登记时刻）才清标记并把水位推到最新。
- 一轮同步硬预算 6 秒 / 90 个请求，用不完下一轮接着来并从停下的模块轮着开始；
  同一连接同时只跑一轮（Redis 非阻塞互斥，拿不到就跳过）。

### 实测（测试租户，真实 DMS）

- **28 个模块全部可用**，事件数与 `2026-09-24` 那张覆盖度表逐项吻合（客户 230、员工 122、
  车型 17、公司 2、省 3 …），零 `err::`、零读不到。模块名写错会变成"永远没有变化"，
  所以这一条是必须实测的。
- **基线**：28 个模块 × (计数 + 最新一条) = 56 个请求，**3.94 秒**（一次性，绑定后跑）。
- **稳态**：没有变化时每个模块只花 1 个请求，28 个模块合计 **0.844 秒**；24 个请求会
  在变更月份里变成"再翻一页"。
- **端到端一次真实增量**：合成一条"客户被修改"的流水（只拦流水端点），其余全走真实请求 ——
  身份证号反查得到客户号 `59`、真读客户表单、校验身份证号一致、写回缓存，
  耗时 **0.729 秒**，水位 230/831 → 231/832，测试后已复原。
- 顺带发现：测试环境 `01:37` 有人重新绑定过连接，四组里**车辆组失败**
  （`ERR_DMS_SNAPSHOT_INCOMPLETE`），手动重跑一次即成功（29 条 / 10 秒）。是偶发
  （目录计数在读的过程中变了会整代替换），不是必现缺陷；但它暴露一个体验问题：
  偶发失败后没有手动重试入口，只能再次绑定（用户已明确要求删掉手动按钮）。
- 测试环境临时注入的探针与文件已全部清除，四个被临时覆盖的模块文件已从镜像原样恢复，
  容器已重启。水位行 `changes` 保留（这是正式状态，重新绑定会重建）。

### 边界与未验

- **批量导入**是否只记一条"导入"事件仍未验：若只记一条，明细会漏，靠"整组重读"兜底。
- **保留窗口**：本租户客户模块最早只到 `13/06/2569`；水位断档时同样是整组重读。
- 六张次要表（`typecardesc`/`cargear`/`businessowner`/`setcbc`/`cusbank`/`numdayspullcarback`）
  没有同名模块，它们的变化只能靠整组重读发现。
- **删除**不在这里猜："删了谁"交整组重读（按原生列表的真实成员收敛）。
- 既有隐患（本轮未改）：若某客户在 DMS 被删而我们这边留着 `overlays`（写穿或增量都会留），
  下一次整组客户重读会因为"读不到这条"而整代替换失败。属既有的完整性契约，单独处理。
- 本轮**没有**真实业务写入、LINE 消息、付费 OCR、生产部署或真机验收。

### 闸门

`check-architecture.py` 0 违规（新增的模块放 `adapter/`，域依赖 `adapter -> client/snapshots`
本就允许）；`check-dms-offline.py` 全 PASS（新增 `tests/test_dms_eventlog_reader.py` 8 项、
`tests/test_dms_change_sync.py` 17 项，旧 `test_dms_operator_advisors.py` 按新契约更新
"顺手同步"开关断言，未削弱任何严格性）；`i18n` 无新增文案，无需改词条。

补一提交（同日）：**删除事件必须先撤掉自己那条 overlay**。整组客户重读会把 `overlays`
里的客户当成必须读到的目标，读不到就整代替换失败——一条指向"已在 DMS 删掉"的 overlay
会让客户缓存**永久读不出来**（每轮重读都失败）。增量同步会给更多客户加 overlay，等于把
这个既有隐患的曝光面放大，所以在本链路里一并处理：删除事件按身份证号找到本地客户号后
调 `store.forget_customer` 撤掉本地这一条，**仍然**交整组重读（由原生列表决定这条客户
到底还在不在，软删除也能被带回来）。测试补到 19 项。

### 部署与部署后实测（2026-09-25）

镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:617c3ce4…d41e`（revision 标签
`224f7b7617d9…`）已发到测试机并 `testenv.sh up dms`，12 个容器全部 Up、站点 `migrate`
与 `clear-cache` 通过。**这次没有往容器里塞任何文件**——测的就是镜像里的代码。

在部署后的镜像上（`bench` 环境里跑一段只读脚本）：

- 代码确实在：`eventlog_reader.ENDPOINT`、`change_sync.VERSION=1`、28 个模块、
  `run_logged_in(...)` 带 `catch_up` 参数。
- 基线：28 个模块 × (计数 + 最新一条) = **2.59 秒**（真实 DMS）。
- 一次真实增量：合成"客户被修改"事件（只拦流水端点），其余全走真实请求 ——
  身份证号反查得客户号 `59`、真读客户表单、身份证号校验一致、写回缓存、
  水位 230/831 → 231/832，**0.677 秒**；测后已把水位复原。
- 最终状态：28 个模块水位齐、无 `dirty`、`next_index=0`；四组底座与业务数据未被改动。

中途一个**误判**值得记下来（我踩了、又爬出来）：`seed` 明明返回 `seeded: True`，
可我在**同一个进程里**读回水位却是旧值，一度以为写入没落地。原因是 `frappe.db`
那条长连接处于 REPEATABLE READ，同事务内看不到独立连接刚提交的写入——**读之前先
`commit()`**（或换新进程）就对了；独立连接上的写入与 `merge_change_state` 本身都正常。
这与 09-24 那条"进度显示不能在长事务里读"是同一个坑。

配套修的三个镜像内套件（`test-dms-line-runtime`、`test-dms-query-snapshots`、
`test-dms-snapshot-reads-native`）只是替身按精确 kwargs 断言调用形状，按新契约更新后
在本机隔离实验室（snapshot lab + flow lab）实跑全绿：store-native 27、reads-native 17、
manual-native 12、queue-native 2、native-flow 10、native-recovery 8、line-runtime 36、
intake-queue 23、query-output 11、query-snapshots 40、snapshot-consumers 25、
booking-flow 33/33。两个实验室容器收尾已停，四个卷按既有约定保留。

**未做**：生产部署、真实 LINE 消息与真机验收、批量导入明细/日志保留窗口/六张次要表
这三条边界的实测。

### 白屏改成动态百分比（2026-09-25）

用户要求把白屏上的分组明细换成**动态百分比**。原生遮罩（`frappe.dom.freeze`）保留，
只是正文变成 `正在读取 DMS 主档… 24%`：

- 四组各占四分之一；已经成功的组算满，正在读的组按"读到几条 / 共几条"再细分。
- **只往上走**：轮询之间偶发的状态回退不让数字倒退（那会让人以为出错了）。
- 四组没全成功之前最多 99%，不预告"读完了"；还在读就不会提前解遮罩。
- 分组标签词条（`dms.connect.cache.dataset.*`）保留在术语表里，只是不再写进遮罩。

`tests/test-dms-connect-overlay.cjs` 8 项通过（新增"数字不倒退、不预告 100%"一项，
并把旧的"必须写出车辆 12/29"换成"必须只有百分比、不能再出现组名"）。架构 0 违规、
`i18n-build --check` PASS。

### 测试环境：百分比版本上线 + 清空所有缓存（2026-09-25）

镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:1e492b0c…c942c`（revision `073f735`）
已发到测试机并 `testenv.sh up dms`，12 个容器全部 Up、站点迁移与清缓存通过。
浏览器实际拿到的资源已确认是新的：`/assets/pearnly_dms/desk/connect.js` HTTP 200 /
8503 字节、含 `progressPercent`。

**按用户要求清空全部 DMS 缓存**（清内容、不删行 —— 该 DocType 的 `on_trash` 明确保护
"由同步器管理"）：

- 五行全空：`vehicles` / `customers` / `company` / `geo`（四组底座）＋ `changes`（增量水位）。
  `payload/overlays/staging/refresh_request/last_error/request_json` 清空，
  `source_started_at/completed_at/requested_at` 归零，`generation` 清空。
- Redis 侧 `*dms*` 键为 0（重新查过，没有残留的主档缓存）。
- 业务数据未动。现在的状态就是"冷启动"：下次绑定会重新读四组底座并重建增量水位，
  在那之前 LINE 侧会照实说数据还没准备好。

**未做**：生产部署、真实 LINE 消息与真机验收。

## 操作员实测反馈与修复（2026-09-25 凌晨）

用户在测试环境跑了一轮 LINE 全流程，一次报了八个问题。逐条给结论（含"不是问题"的和
"还需要他复测"的），改了什么、证据在哪，都在这里。

### 1. 菜单 5 的「读取／看结果」必然被拒 —— **产品 bug，已修**

**症状**：操作员点「อ่าน / อัปเดตข้อมูลครั้งนี้」和「ดูผล / สถานะการอ่าน」，一律收到
「บัญชีนี้ยังไม่ได้รับสิทธิ์ค้นหาข้อมูลใน LINE」（未获授权搜索）。他先怀疑是权限设置。

**根因**（不是权限设置）：对话上下文 `handlers._binding()` 构造的 binding 字典**没有
`binding_revision`**，而 `query_flow._manual_action` 入队前的判据是"绑定版本非空且仍是
当前绑定"（`snapshot_sync._guard` 同样要求）。缺这个键 → 一律 `TXT_DENIED`。
后台任务那条路由 `line/out.py` 在入队时补了同一个版本，所以**只有"人点的"那条路断**。

**修**：`_binding` 用 `identity.current(line_user_id)` 取当前绑定版本一起带上（与
`line/out.py`、`matches()` 同一个来源）。操作员的 `DMS Operator.can_query_dms` 本来就是 1，
所以修完即通。新增回归 `test-dms-line-runtime.py::test_conversation_binding_carries_the_live_revision`。

### 2. 「地址没选全」被说成「所选项目已不在主档」 —— **已修话术**

**症状**：差异卡上点「更新ข้อมูล」→ 回一张卡："รายการที่เลือกไม่อยู่ในข้อมูลหลักของ DMS แล้ว"
（所选项目已不在 DMS 主档），看着像 DMS 把客户删了。

**根因**：那是 `intake/flow.py` 的守卫 —— 草稿的省/区/镇/**邮编**有任一为空就拒绝写入，
话术却借用了 `ERR_DMS_MASTER_UNMATCHED`。实测数据对得上：客户档里地址是完整的
（`zipcode_id=20`），真正空的是**编辑页交上来的草稿里的邮编**。

**修**：新增错误码 `ERR_DMS_ADDRESS_INCOMPLETE`（四语言，进 `messages.tsv`），
话术直说"地址还没选全，请点编辑把府/区/镇/邮编选满再保存"。拒绝行为本身一字未改
（缺项照旧不写入）。

### 3. 编辑页不自动填邮编 —— **已修**

**症状**：客户编辑页里邮编那格不填，操作员选完镇就保存 → 于是撞上第 2 条。

**修**：`EditorPage.vue` 里，镇（ตำบล）选完/首屏加载后，**该镇的邮编只有一个候选就自动
选中**；有多个候选仍然留空让人挑（不猜一个错的邮编写进客户档）。

### 4. 每条 LINE 回话都带「资料时间 / 地址资料时间」 —— **已去掉**

`line/out.py::_snapshot_notice` 以前把「ข้อมูล ณ …」「ข้อมูลที่อยู่ ณ … อัปเดตเมื่อกดอ่าน」
拼到**每一条**回话后面（文本拼一行、Flex 卡正文加一行小灰字）。现在**不再注入**：消息原样发出，
两个 flag 仍按"本消息消费一次"清掉。时间照旧记在快照元数据里，只是不进对方的消息。
`scripts/test-dms-snapshot-consumers.py` 两条断言按新契约更新（消息必须**原样**返回）。

### 5. 那张"编辑"卡的按钮和文案偏小 —— **已调大**

`edit_entry_card` 的提示正文原来 `size: "sm"`、按钮原来 `height: "sm"`；现在正文用默认字号、
按钮用标准高度。只动这一张卡，其它卡形状不变。

### 6. 连接页"旧提示和新界面冲突" ＋ "按钮只留一个" —— **已改**

- 绑定成功后不再弹「连接正常 · 车型 3 · 分店 1 · 顾问 5 · 称谓 2」那句 toast：它弹出的时刻
  正好和白屏读取进度叠在一起，像两件事同时在跑。绑定后的反馈交给读取本身（百分比 → 完成一句）。
  探测**失败**仍然照原样报错。
- 页面只留右上角那一个动作按钮（换账号 = `connect.action.edit/connect`）。原来的内联
  「测试连接」「停用/启用」拿掉了；服务端两个 RPC 保留，只是不再从这个页面露出。
  新增断言：内联按钮数必须是 0，且绑定成功后 `p.success` 一次都不该被调用。

### 7. 不确定来历的那个空白页 —— **不是 bug，是"用本人账号打开 DMS"的中转页**

用户截图里那个只有一行提示 + 一个蓝色按钮的页面是 `account/portal.py::render`：菜单 3/4
那一族里的**一次性票据**页面（`portal_ticket` → 60 秒有效的 `open_portal`），把操作员自己的
DMS 账号密码 POST 给 DMS 并在弹窗里打开。它**故意做得极简**（CSP `default-src 'none'`、
不带样式），因为它要把凭据直投对方站点。没有改动，等他确认要不要换个样子或去掉入口。

### 8. 日志大扫除与"方案是否真的生效" —— **生效，且当前完全跟上**

证据（测试环境、真实 DMS，只读）：

- **基线**：`changes` 行 28 个模块，`seeded_at` = 用户重新绑定的那一刻（02:28），
  actor 是他本人；`checked_at` = 02:35 → **顺手同步确实在他的会话里跑过**。
- **零漂移**：把 28 个模块的**存储水位**与**此刻真实的事件总数**逐个对比，
  `behind_total = 0`、`drift = {}` —— 我们现在**一条都不落后**。
- 四组底座快照都在（`vehicles`/`customers`/`company`/`geo`），无 `last_error`。
- 定时任务只剩该有的四个（`rich_menu.reconcile` + 三个订车恢复）；**被删掉的旧机制
  （每 4 分钟预热、每日地址读）确实没有回来**。
- 队列无积压（0 个待跑）。
- 错误日志（近 6 小时）里**没有 DMS 相关错误**；只有 01:39 一次 OCR 失败
  （`workflow_error`，历史上那条 46 次重试的也已成终态、不再重试，pending = 0）、
  23:47 一条 LINE reply token 失效、23:16 两条计费 404（既有问题，与本次改动无关）。

**还需要用户复测的**：第 1、2、3 条的端到端效果要他在真机 LINE 上再走一遍才算数
（我这里只能给到代码、离线断言和只读数据层证据）。

### 部署与部署后验证（2026-09-25 03:0x）

镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:cb69a9ef…c086e3`（revision
`98863eea1c91…`）已发到测试机并 `testenv.sh up dms`，12 个容器全部 Up、迁移与清缓存通过。

在**部署后的镜像**上验证（真实数据）：

- **菜单 5 的绑定版本**：对当前真实的 LINE 绑定取一次 —— `revision_present: true`、
  `revision_matches_row: true`、该操作员 `can_query: true`。也就是说那条"永远被拒"的路
  已经具备了走通的全部条件。
- **新话术**：`dms.err.ERR_DMS_ADDRESS_INCOMPLETE` 四种语言全部有译文；那张卡用默认字号
  正文 + `height: md` 的按钮，正文里就是这句新话术。
- **不再注入时间**：把两个快照 flag 塞进 `frappe.flags` 再调一次出口，消息**原样返回**、
  正文里没有时间、flag 仍被清掉。
- **旧提示**：服务端发出的 `connect.js`（HTTP 200，实际给浏览器的那个文件）里
  `add_inner_button` 出现 0 次、绑定成功那一支不再调 `feedback()`（只剩失败那一支）。
- **前端产物**：`assets/pearnly_core/pearnly-app/assets/EditorPage.*.js` 里能找到
  `zipcodes"&&de(...)`（即"镇选中/首屏加载后自动补唯一邮编"那段压缩后的代码）。
- **方案仍在跟上**：28 个模块水位齐、`dirty` 为空。

临时的只读脚本已从测试机与容器里移走（各自放到 `/tmp/*-scratch-trash/`，未删除）。
**仍未做**：生产部署、真机 LINE 复测。

## 操作员第二轮反馈（2026-09-25 夜里，**未部署**）

### 1. 邮编不自动填 —— **真根因：迁移时丢了老产品的一条补齐规则**

用户口径："为什么邮编没自动填，而是需要点进编辑页；之前我记得可以自动填，不需要进编辑页，
然后直接录入的。"

**查明**：老产品线上基准 `services/erp/mrerp_dms_client_forms.py::_prefill_address_selects`
在邮编那一级有一条**有意的例外**（原注释）：

> 泰国身份证正面常不印邮编。当前街道若在 DMS 只对应一个邮编，可确定性补齐；
> 多个候选仍保持空白，留给用户选择，绝不猜第一项。

新平台的 `client/client_forms._resolve_address_geo` 改成调用共用的
`contracts/geo_matching.resolve()`，而那个函数**只按显示名精确匹配**、没有这条例外 ——
于是"身份证没印邮编"时邮编为空 → 写入守卫（`intake/flow.py` 的地址完整性检查）把这一笔
拦下 → 操作员只能进编辑页手动挑邮编。这正是他这次的经历，也解释了为什么 DMS 里那条客户
的地址最后是**旧地址**（草稿地址不全，写回时沿用了基线）。

**修**（一处，两条路都受益）：在 `resolve()` 里恢复这条例外 —— 街道解析成功、邮编未命中时，
若该街道的邮编候选**只有一个**就补上；多个候选仍然留空；街道没解析出来时连邮编也不补。
候选**复用**这一轮已经取回的目录，不额外发请求（否则会多一次 HTTP，并有断言盯着）。
`client_forms`（实时写入路径）与 `snapshots/directory`（缓存预览路径）共用这个函数。

**验证**（真实数据）：从测试环境导出 ยานนาวา/บางโพงพาง 那一支真实目录，用**新**函数跑
身份证地址（只印到街道、无邮编）→ `province_id=1 / district_id=12 / subdistrict_id=54 /
zipcode_id=20`（邮编 10120 那一条）；两候选时留空；街道名对不上时留空。
`sample-verify-dms-client.py` 加三条断言（补齐、多候选不补、街道没命中不补），
118/118 通过；同时原有"缺名/空目录/重复名字不猜 ID、不查下级"的断言全部保持通过。

### 2. 编辑页的"资料时间／地址资料时间" —— 已从页面去掉

`EditorPage.vue` 不再渲染这两行（连同两个 computed 与 import）。时间仍然随草稿返回
（`snapshot.value`），只是页面不显示。`scripts/test-dms-editor-snapshot.cjs` 改成
"页面里不能再出现这两个绑定，但草稿仍带着时间"，6/6 通过。

### 3. 中转页（图 4）按钮太小、靠左 —— 已居中放大

`account/portal.py` 的一页式交接页：正文与按钮居中、按钮整宽 + 加高（`min-height: 3.25rem`）
+ 字号加大。样式走 **nonce 的 `<style>`**，CSP 只增加 `style-src 'nonce-…'`，
`default-src 'none'` / 不放 `unsafe-inline` / `form-action` 只许 DMS 源 —— 其余一律没放宽。
本地以替身渲染验证 12 项（含 CSP 仍严格、凭据表单原样、泰文文案与按钮标签都在）。

### 4. DMS 侧那次录入的实际结果（只读核对）

通过 `eventlog` 找到最近一次客户事件：**`1103702587460` 在 03:17:15 被修改**（账号 `dmstest`）。
直接读 DMS 上的这条记录：

| 字段 | DMS 现值 |
| --- | --- |
| 客户号 | 115 |
| 姓名 | อักษราภัค อักษรวรนารถ |
| 电话 | 178957964 |
| 门牌号 | 250/75 |
| 路 | โชคชัย 4 |
| 省 | 1 กรุงเทพมหานคร |
| 区 | **38 ลาดพร้าว** |
| 镇 | **127 ลาดพร้าว** |
| 邮编 | **94（＝10230）** |

结论：**这条记录本身是完整、自洽的**（省/区/镇/邮编互相匹配，邮编 94 确实是ลาดพร้าว的，
"镇只有一个邮编"那条规则也成立），门牌号与路名是身份证上的；**但区/镇/邮编是 DMS 里的
旧地址 ลาดพร้าว，不是身份证上的 ยานนาวา/บางโพงพาง** —— 与"草稿地址不全 → 写回沿用旧地址"
一致。也就是说：这一笔写得合法、不脏数据，但地址没按身份证更新，根因就是上面第 1 条。

**本次没有部署**（用户要求）。改动只在工作树里，等通知。

## 菜单 5：只留一个按钮，读取改成自动（2026-09-25 夜里，**未部署**）

用户口径（第二次说）：**菜单 5 只有一个功能按钮 —— 按类查记录**；其它按钮他不知道为什么被放出来。
他还让我去查老产品的业务逻辑。

### 查证：老产品菜单 5 到底长什么样

`services/line_dms/query_flow.py`（线上基准）：

```python
await _start_loading(line_user_id)                     # LINE 原生"正在输入"
cloud_dispatch.spawn("dms.records", _run_records, ...) # 后台去读，读完推卡
```

- 入口卡 `query_type_message` 当年摆过三个（销售记录 / 库存 / 其它），后两个是占位 ——
  用户说的"最早三个按钮、其它两个是占位、后来只要按钮 1"就是这三个。
- **从头到尾没有**"读取／刷新本次查询""查看结果／状态"这种按钮：选完条件 → 后台读 → 结果推过来。
- 结果卡上的导航是三个：上一页 / 重新读一次 / 下一页。

### 新平台多出来的东西（本次删掉）

后来为了让"缓存读取"可控，加了两个机械步骤按钮：

| 按钮 | 文案键 | 现在 |
| --- | --- | --- |
| อ่าน / อัปเดตข้อมูลครั้งนี้ | `NAV_FETCH` | **删除**（连同 4 种语言的词条） |
| ดูผล / สถานะการอ่าน | `NAV_RESULT` | **删除**（连同 `ACT_QUERY_RESULT` 动作码） |

同时把"读取"接成**自动**：

- 条件一凑齐就由服务端登记这次读取（`_read_failure` → `_request_read`，走既有 `manual` 队列与去重），
  回一句纯文本"正在读，读完把结果发过来"（`manual_status_message` 不再挂任何按钮）。
- 读完后由 `query/snapshot_sync._push_result` 把结果卡推给发起人（老产品就是推卡，不是等用户点）。
  推送前重查"这张卡还是不是当前绑定、条件还是不是当前条件"——换过绑定或改过条件的旧结果一律不推。
- 卡片导航回到老产品形状：上一页 / **重新读一次**（`NAV_REFRESH`＝老产品"อ่านใหม่"）/ 下一页。
- `MANUAL_*` 六条文案改成"正在读／会自动重读／读完在发"的说法，不再叫用户去点按钮。
- 入口卡只剩 `TYPE_SALES` 一个按钮；`TYPE_INVENTORY`/`TYPE_OTHER` 两个占位词条一并清掉
  （代码里那条"这一版还没开"的兜底留着，防旧卡）。

### 日志：菜单 5 这次为什么还是不能用

查了测试环境：最近一次使用菜单 5 的操作员**没有被登记的读取**（`query.*` 快照一行都没有），
说明拒答发生在**登记之前**——正是 `handlers._binding` 少 `binding_revision` 那条
（上一轮已修，见上文"菜单 5 的「读取／看结果」必然被拒"）。
**但那一版还没部署到测试环境**（用户上一轮要求"修好不要部署…等我通知"），
所以测试环境上仍是旧镜像，菜单 5 会继续拒答 —— 这不是新问题。

### 闸门（本地/隔离实验室）

架构 0 违规；`check-dms-offline` 14 项全 PASS；查询三套：`test-dms-query-snapshots` 40、
`sample-verify-dms-query-cards` 32/32、`sample-verify-dms-query-flow` 46/46；
`test-dms-query-output` 11、`test-dms-line-runtime` 37、`test-dms-intake-queue` 23、
`test-dms-snapshot-consumers` 25、`test-dms-snapshot-reads-native` 17；
flow 实验室 native-flow 10 / native-recovery 8 / booking-flow 33/33；node 三套 16 项；
`i18n-build --check` PASS。**未部署**（等通知）。

## 真机端到端实测：改 DMS → 日志 → 拿回来 → 覆盖旧的（2026-09-25 夜里）

用户授权（测试站，可增删改）。做法：绕开我们自己的写入通道，用**原生表单**直接提交
`cus/edit.php`，只改客户 115（证号 `1103702587460`）的电话 —— 这就是"别人在 DMS 里改了东西"
的真实场景。跑在**已部署的镜像**（`cb69a9ef`，含 change_sync）上。

| 轮次 | 缓存（改前） | DMS（改后） | 日志事件数 | 顺手同步 | 缓存（改后） | 水位 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `178957222` | `178957999` | 239 → 240（`แก้ไข` + 证号） | 0.75 秒 | **`178957999`** ✓ | 239/843 → 240/844 ✓ |
| 2 | `178957999` | `178957964`（改回原值） | 240 → 241 | 0.84 秒 | **`178957964`** ✓ | 240/844 → 241/845 ✓ |

结论（逐条对上用户的问题）：

- **能不能拿到日志**：能。每次保存后**0.05 秒内**事件总数就 +1（实测连看 12 次），
  行里带账号、佛历日期、精确到秒的时间、模块、动作（`แก้ไข`）和被改客户证号。
- **能不能拿回来**：能。同步按证号反查回客户 115、真读了一遍表单、身份证号校验一致。
- **拿回来能不能覆盖旧的**：能。两轮都把缓存里的旧值覆盖成 DMS 的新值，水位各推进一格，
  两轮之间没有 `dirty` 残留（说明没有偷偷降级成整组重读）。
- 一轮约 0.75–0.84 秒（含会话复用）。
- 测完 DMS 里的电话**已复原**成原值 `178957964`，缓存与 DMS 现在一致（净业务改动 = 0）。

**一个测量陷阱（第二次踩、这次写下来）**：第一次跑的时候看到"缓存慢一轮"（这一轮改的、
下一轮才进缓存）。查到底是**我的读法**问题 —— 同一个进程里那条长连接处于
REPEATABLE READ，独立连接刚提交的写入在它眼里看不见；`frappe.db.commit()` 之后再读就对了。
写入本身一直是好的（同一个坑 09-25 早上在 `seed` 的读回上踩过一次）。**任何"同步有没有生效"
的核对都必须先结束事务快照再读。**

**地址那条路**：geo 不做逐条补读，测的时候四个地址模块（省/区/镇/邮编）每轮都被读到、
计数与缓存一致（无漂移）；真有地址改动时会登记一次整组重读（那条路本次没有在 DMS 上
真做地址写入，只有测试覆盖）。
