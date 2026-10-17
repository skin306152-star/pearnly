# 开发窗口交接（2026-10-18，「工作清单窗口交接」→ 新开发窗口）

给接手的新开发窗口。测试窗口的交接在 `~/pearnly-devtools/test-window/交接-测试窗口-2026-10-18.md`，两份对照着看。
仓库副本：main 上的 `docs/work/2026-10-18-handoff-dev-window.md`（内容与本文件相同，以本文件为准；改一份就同步另一份）。
上一份是同目录的 `交接-开发窗口-2026-10-16.md`，其中第 6、7 节已逐条核对并并入本文件，**旧文件不用再看**。

**怎么防漏**：本文件第 4 节是全部未完成事项的总清单，每条有编号。各任务的细节和进度仍记在各自的任务记录里（`docs/work/…`，AGENTS.md 要求），
本文件只做索引和顺序。下次再交接时，从本文件的编号清单出发逐条核对状态，不要凭对话记忆重写。新冒出的待办直接加进对应任务线的编号后面。

## 0. 新窗口先做这几件事（按顺序）

1. 读本文件，再读测试窗口交接；`ListAgents` 找到新测试窗口（名字会变）。
2. 旧窗口的两个后台工作流都已跑完（第 1 节），不用接续。
3. 按第 5 节的顺序开工。第一批是 **A2（第 12 轮变基、同步 lab；先确认「消息含义理解」窗口借用的 lab 已还）、B2（标准化 B0 合入）、C1（文案闸门合入）**，三条互不冲突，可以并行（合入 main 时串行）。
4. 用户已定的事见第 2 节，**不要再问**；还要用户定的只剩第 6 节那几条。

## 1. 现状一览（10-18 00:45 核对）

- **main / 生产**：`origin/main` = 本地 main = `2574ef94`（ec90fdfc 外壳第 10、11 轮 + Claude 识别三批 + 发布记录）。生产 10-18 00:01（泰国时间）已部署 `ec90fdfc`，
  镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:ebba5a407bdc8e8d1a878bad05755e1b3443e7e57b05c7ef12090384f744d448`（以下简称 ebba5a40），回读干净。
  发布记录 `docs/work/2026-10-18-release-ec90fdfc.md`。**lab 底座以后用 ebba5a40**（10-16 交接里的 b9da6b58 作废）。
- **测试机 lab**：cowork lab 开着，testcowork 指向测试机（Cloudflare 104.21.28.241），挂的是 `.claude/worktrees/cc-shell-rebuild` 工作树的实时源码（ec90fdfc，包 `index.Dtpvp6Oq.js`）。
  没有 lab-stop。testerp、testdms 在线上（107.155.65.74）。测试数据状态见测试交接第 1 节。
  lab 开着意味着：线上 testcowork 的定时任务处于暂停（lab-stop 才恢复）；测试机要保持唤醒；「消息含义理解」窗口要在测试租户切 Claude 实测时，打到的是 lab 副本——
  所以 lab 由新开发窗口统一调度：第 12 轮直接换源码用（A2），用完或长时间不用就 lab-stop，别人要用 testcowork 前先说清现在指向哪里。
  **10-18 约 01:10 起 lab 借给「消息含义理解」窗口做统一原件层实测，已做完交回**（它的回报）：
  - cowork、erp 两个 lab 都已 lab-stop，WSL 里没有 testenv 容器；testcowork、testerp 都解析回线上 107.155.65.74，testcowork 的线上定时任务已恢复。**所以第 12 轮要用 `lab-start cowork`（不是 `live`）。**
  - 两个测试租户的识别路线恢复原样（`pearnly_ocr_engine` 键已删，原来就没有）；测试机 `/opt/pearnly-testenv/env` 里甲骨文的 6 个变量已删。
  - 更正：借用前 lab 实际跑的底座是 b9da6b58，不是上面写的 ebba5a40；它借用时换成了 ebba5a40，测试机上已拉好 ebba5a40 镜像，下次 lab-start 会按传入的镜像重写 env 文件。
  - 实测数据已全删（testcowork 测试发票 IV2610-0042、对账任务「原件层实测（可删）」、testerp 两条附件登记；甲骨文上两个测试租户的原件目录为空）；第 12 轮要用的侧栏、卡片、记住的选择、定时任务、测试对话都没动。
  - 保留：甲骨文 8443 对办公室 IP 124.120.107.105 放行（云端安全列表由用户点的 + 本机 iptables 已保存），以后测试机测 Claude 路线还要用；办公室 IP 一变就失效，不想留就删这两条规则。
  - 实测结果：Cowork 录入台、对账中心、ERP 采购 / 销售上传直传甲骨文、识别按引用读，预览下载走签名地址，删单后甲骨文原件跟着删；LINE 收单和 CC 工具没实机测（只有单元测试）。
- **生产服务器**：10-18 已删掉开户代理留下的 `pearnly-maintenance-cowork`（10-12 起、旧镜像 227ed4b2、只跑 sleep），删后读回没有任何非生产容器。修法见 F5。
- **交接时还在跑的**（旧窗口的后台工作流，旧窗口一关就停）：
  - 第 12 轮拖动 `wf_c007d6cb-649`：**已跑完**。替换引擎 `92733899`，四路审查确认 4 个问题、补修 `47d51d37`（见 A1）。
  - 标准化 B0 补修 `wf_694b03e6-ab2`：**已跑完**。补修 `41329a8b`，复核 14 条全部通过，结论已写进任务记录并提交 `a8c8ad27`。
  - 两个都已跑完，旧窗口关之前没有在跑的后台工作流。

## 2. 用户已定的（照做，不要再问）

10-18 凌晨用户答复（原话见记忆 `user-decisions-2026-10-18`）：
- **第 3 期补独立金标**：可以花钱补识别；**开跑前先找用户要测试用的识别 API，绝不用生产 API / 生产凭据**。仍按记忆 economize-test-spend：小样本、脚本按实际调用计费的硬上限、开跑前在任务记录写明页数和上限。
- **T4 简易税票「票面印 0 不拆」验证**（约 70–140 泰铢）：同意，同样只用测试 API。
- **两份同期账套「问一次」加重问**：另一份候选账套在上次回答之后有了更新录入，就再问一次（仍不自动挑）。
- **文案存量整改**：`pearnly/copy-gate` 的 `docs/work/2026-10-17-copy-stock-review.md` 第 3–14 节全部照清单建议改（含三个真缺陷）；第 1、2 节两套登录页不动（10-17 定，记忆 login-pages-frozen）。
  登记同意时在该记录里写明「用户 10-18 同意第 3–14 节按建议改」，再用 `--approve --source` 登记。
- **多电脑第 7 批真机**：要用户做的三件事（录制笔记本上输入测试机 skin3 密码、testcowork「团队管理」加第二个测试成员、同意在录制笔记本上另建本地测试账号装 CC 桌面版）**到那一步时再通知用户**，不要提前要。
- **现代待机电脑用电池时「保持唤醒」默认开还是关**：第 7 批实测后再定（实测结果出来后报给用户）。
- **手机深色导航条（A4）复验、正式租户真机业务验收**：用户 10-18 回复「没问题了」。
- 第二轮（CC 未完成项核对后，原话「以上不做了」「不用」「6，算」「其它的按期执行」）：
  - **教程不重录**：成片保留 10-16 改版前的旧界面和开头示意卡；教程里「下班后电脑常驻跑」也不重录、不加字幕。
  - **方案 A「办公室小主机当 CC 的电脑」作废**。
  - **「允许」后马上停止、下一轮 CC 可能再问一次：不补提示**，记为已知限制。
  - **10-15 夜间批及之后没有真机验收记录的那几项算已通过**（F9、H16–H18 列的：iPhone 状态行与断网、Safari 选字回车与往上翻、多设备侧栏同步、08:30 汇总、电脑选文件夹、智能应用控制拦更新、Chrome 106、安卓安装与 LINE 跳浏览器、回读 / 税表草稿 / POS 真环境对照），不再要求补测。
  - 其余照本文件顺序执行。

更早已定、仍有效的口径（不重复）：记忆 drag-unified-rule、cc-phone-layout-2026-10-17、delete-chat-confirm、frontend-standard-practice、da-phase3-goods-history-a、
multi-desktop-own-computer-rule、copy-changes-need-approval、login-pages-frozen、server-restructure-on-hold、background-memory-cap-deferred、claude-originals-on-oracle。

## 3. 工作方式、工具与命令

- 一个开发窗口做、一个测试窗口测。开发写完 → 全闸门 → 同步测试机 lab → 自己从外面读回确认是新包 → 给测试窗口发「第 N 轮清单」→ 测试窗口逐条回 → 修 → 下一轮。
  测试机只有一台（Cowork / ERP / DMS 三个 lab 轮着用），起 lab 前先和测试窗口说「开始占用」。
- 只用中文。Windows 客户端在用户可见处一律叫「CC 桌面版」。技术取舍自己定（goal-driven-decisions）；新文案按官方风格直接写，改已有文案先给用户看（已看过的见第 2 节）。
- 后台工作流 / 子代理：推理档 high，**路数不限**（10-17 起，原 5 路上限作废），同一工作树只能一个写者；**任务书开头必须加「只听这份任务书」防护段**（记忆 workflow-agents-see-user-messages）。
- 部署：攒批、过闸后一次构建激活（batch-deploys-not-piecemeal）；现在没客户，进维护立即部署（deploy-at-night-maintenance）；自主部署授权见 autonomous-batch-deploy；
  动生产、测试机、甲骨文前先和「消息含义理解」窗口约时间（它在做 `pearnly/originals-layer` 统一原件层，ERP 部分还在施工，见 G4）。
- 闸门：`export PATH=$HOME/pearnly-devtools/bin:$PATH` 后 `SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`（按 development.yml 全跑，外壳类约 86 步、多电脑约 91 步、第 3 期约 96 步）。
  **node_modules 是符号链接的工作树一定要带 SKIP_NPM_CI=1**，否则会碰坏共享依赖（见第 7 节）。推分支前全跑，推完看 Development checks（记忆 run-branch-gate-before-push）。
- 候选离线检查（改了 pearnly_core 合入前必跑，记忆 core-change-candidate-lane）：
  - 本机：`WT=<工作树绝对路径> IMAGE=<线上 digest> bash ~/pearnly-devtools/cand-local.sh offline`（10-18 新存，72 步；本机 Docker）。
  - 测试机：`~/pearnly-devtools/cand-run.sh`，**它默认的 IMG 还是旧的 b9da6b58，必须显式传 ebba5a40**。
- 测试机 lab：
  - 起：`PEARNLY_TESTENV_SRC=<工作树> PEARNLY_TESTENV_IMAGE=<ebba5a40 全名> PEARNLY_TESTENV_LOCAL_SPA=1 bash scripts/testenv.sh lab-start cowork`（不带 REFRESH，保留测试数据；先构建两个前端）。
  - 改代码后：同样的环境变量，`... testenv.sh live cowork`。前端改了必须带 `PEARNLY_TESTENV_LOCAL_SPA=1`。
  - 读回：`curl -s --resolve testcowork.pearnly.com:443:104.21.28.241 https://testcowork.pearnly.com/assets/pearnly_core/assistant/index.html | grep -oE 'index\.[A-Za-z0-9_-]+\.js'`，与本地 `apps/pearnly_core/pearnly_core/public/assistant/index.html` 对上。
  - 收：`bash scripts/testenv.sh lab-stop cowork`（域名切回线上、恢复线上测试租户定时任务）。
- 生产部署：步骤见记忆 production-deploy-steps；服务器脚本模板 `~/pearnly-devtools/deploy/deploy-template.sh`（10-18 新存：同步部署工具 → 核对 → ERP / Cowork / DMS 依次激活、之间等可用内存 ≥ 8 GB → 基础层，全程每 10 秒记内存）；
  回读 `~/pearnly-devtools/deploy/readback.sh <digest> "<since>"`。控制面在本机（colima-pearnly，`pearnly-sample-control-web-1`，site control.localhost）。不 exec 进生产 mariadb，不循环 SSH 生产。
- 旧窗口临时目录里有用的脚本已转存：`~/pearnly-devtools/session-2026-10-17-scripts/`（A14 trace 分析 a14trace、失效追踪 a14inval、拖动审查复刻页 dragengine、标准化复核 ccstd-b0fix），
  第 3 期回放分析脚本和汇总在 `~/pearnly-devtools/da-phase3-replay-d5/`（`analysis-scratch/`、`out-summaries/`、`round5-notes.md`）与 `da-phase3-replay-d6/`。单据级明细没拷（不能进 devtools / 仓库）。

## 4. 未完成事项总清单（按任务线）

状态写法：没开始 / 进行中 / 待验证 / 待合入 / 已知限制。「用户」= 要用户做或定。共 8 条线：A 第 12 轮拖动、B 前端标准化、C 文案、D 多电脑、E 第 3 期、F 零碎、G 运维、H CC 零碎。

### A. CC 第 12 轮：自写拖动替换 dnd-kit

分支 `pearnly/cc-drag-engine`，工作树 `.claude/worktrees/cc-drag-engine`，HEAD `47d51d37`（`93d29383` 文档与 129 条对照清单、`92733899` 替换引擎、`47d51d37` 审查补修），基于 d70f7f00，**没推送**，相对 origin/main 领先 3、落后 8（含第 11 轮 E 段焦点补修，已上生产）。
任务记录 `docs/work/2026-10-17-drag-engine.md`。已完成：drag.tsx 用自写 DragEngine 整体替换 dnd-kit，对外接口、读屏播报不变，5 个调用方零改动；复刻页 6 倍节流下拿起手机 55–59→17–19ms、落定 44→35ms。

- A1 ✅ 已完成（10-18）：四路审查（行为对照、键盘读屏、性能、代码）→ 每条两名核实者 → 确认 4 条、意见不一 0、驳回 0 → 补修 `47d51d37`：
  拖进已置顶后取消（Esc / touchcancel / 转屏 / 切后台）置顶里让位的行当场滑回（顺带修掉修前就有的约 19px 下冲）；拖动区在拖动中卸下时摘掉传感器的页面监听（之前会吞下一次空格 / 回车 / Tab / Esc）；
  键盘拖动中推送重排挪了节点时焦点接回把手。补 4 条用例（改前失败、改后通过），310 条用例全过，记录追加「审查后修正」一节。复刻页 6 倍节流：手机拿起 58→17ms、鼠标拿起 57→40ms、落定 46→35ms。
  **「性能」那一路审查代理没返回结果**（空返回），性能结论以 A3 的 A14 真机帧测为准；新窗口觉得不够可以单独补一轮性能审查。
- A2 没开始（下一步就做这个；lab 已还且已 lab-stop，用 `lab-start cowork`）：变基到最新 origin/main（只读预演无冲突，两边都改的只有 tests/test_assistant_ui.py 且区段不重叠）→ 重跑测试 → vite build → 同步 lab（lab 开着用 `live cowork`；被「消息含义理解」窗口 lab-stop 过就用 `lab-start cowork`，都带 LOCAL_SPA 和 ebba5a40）→ 外网读回包名。**不变基就同步会让 lab 丢掉已上生产的焦点补修。**
- A3 没开始：写第 12 轮清单交测试窗口（放 `~/pearnly-devtools/test-window/第12轮清单-<日期>.md`）。要含：
  - A14 帧测四个时刻（拿起、出菜单、收菜单、落定），对比第 11 轮复核 58–60 / 72 / 35 / 35–39ms；两种口径（不开 trace 的帧间隔 `round11b/b14p.py`、trace 的 `round11b/trace.py`）分开比。目标拿起 30–45、落定 15–25ms。
  - 鼠标 / 键盘 / 手机长按 × 侧栏对话行、公司组头、置顶行、右侧栏卡片；组内排序、拖进置顶、Esc 后原处松开不误开、拖开再拖回、切走再回来、读屏播报、第一张卡键盘拖动。
  - **右侧栏卡片头 Esc 后原处松开仍开合（第 11 轮遗留）**：核对员查明根因多半是测法——第 11 轮 `round11c/b5.py` 按的是卡头开关 `[aria-expanded]`（只有 onClick 开合），
    拖动把手只在 6 点按钮 `.cc-panel-grip`（电脑上悬停或聚焦才显示）。先在记录里写明「卡头不是把手，按卡头松开就是普通点击」，清单里改按把手复测，再和卡头普通点击对照一次。
  - 读屏实听（TalkBack；VoiceOver 要 iOS 设备，可能没有）。
- A4 待验证：落定没到 A14 目标 15–25ms（复刻页 23–35ms）。A14 数据出来还超，剩下的开销在 CompanyGroups 存新顺序后整列重排，要改 thread-list.tsx（超出「调用方零改动」，开工前在记录写明范围变化）。
- A5 已知限制：鼠标拿起后挡板盖上那一帧的悬停样式重算（6 倍节流约 13ms），看 A14 和真 Chrome 数据再定改不改。
- A6 没开始：Z-03 长按出菜单那一帧（A14 72ms）和组头菜单位置预算；Z-01 挪回原组时第一行先往下跳约 16px（老问题，复测顺手看）。
- A7 没开始：删 `@dnd-kit/*` 依赖（下一轮或合入前并入）。**先给本工作树装自己的 node_modules**（现在链到 cc-shell-rebuild），再删 package.json:14-17 与 lock 里 4 个包；
  `tests/test_cc_session_panel.py:599` 锁版本的断言按同一意图改写（改成断言不再依赖 dnd-kit，不能直接删）；session-panel.tsx:78、lib/company-panel.ts:5 注释里的 dnd-kit 改掉。
- A8 待合入：lab 与 A14 复测通过 → 全闸门（记录里只跑了 5 个测试文件，没跑全闸门和 CI 的 npm run build）→ 推分支等 CI → 快进 main → 攒批部署。
- 坑：无界面 Chrome 里连做几次鼠标 Esc 拖动后下一次不出帧（Z-02，两版一样，换新浏览器即可）；量帧只用 A14（内置浏览器窗格隐藏时每秒一帧）；文档提到的 behave.mjs、prof.py 在磁盘上找不到。

### B. CC 前端标准化（只用 globals.css 标准定义）

分支 `pearnly/cc-standards`，工作树 `.claude/worktrees/cc-standards`，HEAD `a8c8ad27`（b82aec3b 方案、6988d230 B0、41329a8b B0 审查补修、a8c8ad27 补修复核记录），**没推送**，领先 4、落后 28，试合并无冲突。任务记录 `docs/work/2026-10-17-cc-frontend-standards.md`。
已完成 B0：闸门 R4–R15（`scan_strict`，收紧清单 `scripts/frontend-tokens-strict.txt` 现为空）、比对工具 `scripts/cc-visual/`（67 场景 × 16 组环境，同构建两遍 0 差）、测试 26 项，审查 14 条已修。
**存量还没动**：CC 前端 102 个源码文件里写死的值 1,337 处（main 上 1,340，drag-engine 上 1,343），目前只报数不拦。

- B1 ✅ 已完成（10-18）：B0 补修复核 14 条全部通过，记录「B0 补修」下「补修复核」一段（`a8c8ad27`）。留了两条不阻塞的加固建议（任意属性写法引用外来变量记 R10；`shoot()` 校验环境名、SIGINT 删 `.profiles`），B1 开工前处理。比对工具全矩阵约 12.5–14.5 分钟，依赖本机 Chrome 与 Node 22。
- B2 待合入：B0 合入 main（不改 src、不部署）：变基 → 全闸门 → 推分支 → CI → 快进 main。顺带改记录里过时的说法（「合回 cc-shell-rebuild」、「B1 起从 cc-shell-rebuild 开」——它已全部并入 main、无远端分支，改为从 main 开）；
  合入后 main 上发布记录对本记录的链接才不断（现在是断的）。
- B3 已知限制：比对工具几个场景做不完（session-panel 桌面版选择器、chin-month 窄屏选择器、767.5 宽 settings 打不开），B1 开工前补齐；闸门有几种写法理论上漏拦（`[z-index:50]`、`[width:var(--foo)]`、`[transition-duration:var(--foo)]`、`[--cc-x:var(--foo)]`、`group-@md:flex`、常量拼 `${4}px`），现 src 里没用到，B1 前决定补用例还是登记为已知限制。
- B4 没开始：B1 标准层（globals.css 刻度层、用途层、运行时层、约 20 个共用类、@theme 字号字重断点、data-cc-touch、twMerge 登记新名、MOBILE_QUERY 改 `(width < 768px)` 修 767.5 宽现有缺陷、断点三处一致测试、ccs.md）。
  从最新 main 开 `pearnly/cc-std-b1`。**已核实 A 线不改 globals.css**（`git diff --name-only origin/main...pearnly/cc-drag-engine` 不含它），B1 可以和第 12 轮并行；A 线后续补修若碰 globals.css 再协调。行号要按 main 重新核对（globals.css 已 2308 行）。画面变化 V2、V3、V4、V6、V20。
- B5 没开始：B2 组件库（button / dialog / tooltip / skeleton / sidebar / menu / marquee / separator，删 avatar / sheet / input，lib/css-var.ts）。等 B1。
- B6 没开始：B3 外壳、B4 弹窗与列表页、B5 会话栏 / 结果 / 文档 / 工具卡，可并行开发、串行合入。**B4、B5 动的 settings、scheduled-tasks、pending、company-cards、month-ledger、i18n.ts 也被 D、E 两条线改过**，开工前先看它们合了没有。
- B7 没开始：B6 侧栏列表（thread-list、sidebar-group、projects、ui/drag.tsx）**必须等 A 线合入**；记录里 dnd-kit 相关说法与行号在新引擎上都过时；新引擎读屏区 LIVE_STYLE 写死 "1px" / "-1px" 要改。
- B8 没开始：B7 对话区（thread、attachment、markdown-text、tooltip-icon-button，删 follow-up-suggestions），等 B2。
- B9 没开始：B8 收口（删收紧清单全仓按新判据失败、删旧变量名、删 tw-animate-css / tw-shimmer、改 NOTICE.md、删 components.json）。等 B3–B7，并和 A7 删 dnd-kit 串行改 package.json；用自己的 node_modules。
- B10 用户（验收）：每批画面变化交测试窗口截图对比（三种变体 × 深浅色），触屏 / 中间宽度的 V3、V4、V24、V30、V36 请用户用手机走一遍；比较显眼建议让用户直接看的：V14（弹窗圆角 8→16、标题 24→22）、V21（「交给 CC」去图标）、V10 / V17（设置页）、V33（代码块）、V36（640–767 宽按手机排）。纯替换批次由工具证明 0 差即可。
- 坑：纯替换必须全部场景 0 像素差、0 样式差；画面变化与纯替换分开提交；B1 一定要用 extendTailwindMerge 登记新名；闸门看不出常量拼接（如 drag.tsx 的 `style.zIndex = String(OVERLAY_Z)`），靠审查；test_cc_ccs 的 TokenGateTests 不许改名成忽略清单、不许行内禁用。

### C. 文案闸门与存量整改

分支 `pearnly/copy-gate`，工作树 `.claude/worktrees/copy-gate`，本地 `cc0ec1f7`，远端 `5b429fd0`（CI 已过，run 39014535159），本地多 4 个只改存量清单的文档提交没推送；领先 6、落后 149。任务记录 `docs/work/2026-10-17-copy-gate.md`、`docs/work/2026-10-17-copy-stock-review.md`。
已完成：闸门 `scripts/check-copy.py` + `scripts/copygate/` + 词表 / 豁免 / 同意登记 + 测试，评审 38 条已处理；CI、共享钩子、AGENTS.md、验证矩阵的接入只在分支上。第 10 轮被拦文案已随 ec90fdfc 上线。

- C1 待合入：闸门合入 main（只是开发脚本、CI、文档，不部署）：变基（main 分叉后只改过 development.yml，不同处）→ 跑 test_check_copy.py、check-copy.py、check-architecture、check-i18n --strict、check-frontend-i18n --strict、全闸门 → 推分支 → CI → 快进 main。
  同时把 copy-gate.md 第 8–10 行「未推送、未合入」和第 129 行意见 19 改成实际状态。**合入后共享钩子立刻对所有工作树生效**（hooksPath 是绝对路径），没变基的分支会打印提示并跳过文案闸门；合入前先告诉「消息含义理解」窗口（originals-layer 也会被钩子管到）。
  **development.yml 被 copy-gate、cc-standards、multi-desktop、da-phase3 四条分支都改了**，这四条要串行变基合入，每条合完再变基下一条、各自跑全闸门。
- C2 没开始（用户已同意，见第 2 节）：存量整改第 3–14 节。先在最新 main 重跑 `python3 scripts/check-copy.py --audit --report --top 1000` 把清单行号对齐（基线 786c376d 已过时，main 上现在拦截 687 处，部分条目已不在），
  在记录里写「用户 10-18 同意」，按节改、用 `--approve --source docs/work/2026-10-17-copy-stock-review.md` 登记。换 msgid 的行要同步改调用处、删旧行；C 类内部断言（`pearnly_dms/adapter/session.py:600`、`pearnly_control/billing.py:176`）先改成不经 `_()` 的内部异常再删行；
  「未见引用」几条动手前再确认。建议按产品拆几批提交，**和 D、E 线都改翻译文件**，合入时 translations 取并集后用 `scripts/i18n-build.py` 重新生成，不手拼。
- C3 没开始（随 C2 先做）：三个真缺陷。
  - 改密码页 `apps/pearnly_core/pearnly_core/www/pearnly-password.html` 整页写死中文、出错时显示服务端原始 JSON（第 41 行）→ 改用 Frappe 自带 msgid、解析 `_server_messages`、补 Current Password；同时按第 15 节补 www 页内联脚本不扫描的盲区。
  - CC 工具步骤展开后显示给模型的英文指令（`assistant/toolrun.py:145`、`tools.py:195/201`）→ 新建 `assistant.tool.declined / unavailable / refused` 三个术语键，给模型的指令不经 `_()`、只进 tool_result；补 `No file was received.`、`Conversation not found`。部署前在 CC 网页展开一次被拒绝的步骤核对。
  - DMS LINE 快捷按钮 `dms.card.qa.BTN_REMOVE_SLIP` 泰文 22 码位（`i18n/messages.tsv:234`，`qa_cards.py:366` 发出、不截断）→ 改 `ลบสลิปแล้วทำต่อ`；删没人用的 `BTN_RETRY_BOOKING`。随 DMS 批次部署后请用户用手机走一次「凭证与付款方式矛盾」卡片（真 LINE 实测由用户手机走，我们在服务器核对）。
    DMS 是「消息含义理解」窗口常动的域，改之前和它说一声。
  - 三个真缺陷改的文件（pearnly-password.html、assistant/toolrun.py 与 tools.py、pearnly_dms/i18n）其它分支都没改，可以最先做；其余各节涉及 i18n.ts、Cowork 翻译、settings.tsx 的，等 D、E 合入后做或合入前逐个变基。
- C4 没开始：存量清单第 15 节「闸门收窄与盲区」单独提交（放宽规则不能和文案改动同一份差异，否则被 R-RULES 拦；坏例测试仍要被拦；hidden 字段收窄前先逐条登记豁免）。
- C5 没开始：第 3 节 B 类 `scheduled-tasks.tsx`「Delete {0} item permanently?」，到锁定镜像 `frappe/locale/ja.po` 查日文官方译文，没有就只补日文。
- C6 待合入：da-phase3 变基到含闸门的 main 后会被拦（10-18 只读跑 9 处：Max Payload Version、Payload Version、Refresh Lease Computer、account_set_ambiguous / none 等；10-17 另一份基线不同的报告是 22 处，以变基后复跑为准）——multi-desktop 的 `3e895ef3` 已改过这些，da-phase3 合入 multi-desktop 后复跑 check-copy 应为 0。
- 坑：改豁免过的文案（CC 输入框占位、首页问候、DMS 加好友欢迎语、两条拍摄提示）要在同一提交更新 `scripts/i18n/copy-exempt.tsv`；CI checkout 必须 `fetch-depth: 0`；`upstream-msgids.txt` 随底座升级重导；本机没 pytest，用 `python3 tests/test_check_copy.py`；
  工作树 `frontend/assistant/node_modules` 链到 f19-push-summary 工作树、`frontend/node_modules` 链到主目录。

### D. 多电脑（T5）与 T1 跨年度换账套

分支 `pearnly/multi-desktop`，工作树 `.claude/worktrees/multi-desktop`，HEAD `58d654f4`，**分支领先 origin/main 44、落后 16**（落后的是 Claude 识别三批、第 11 轮重测修复、发布记录）；远端还停在 `f87a0585`（本地领先 179，一直没推，从没跑过分支 CI 和 Companion checks）。
任务记录 `docs/work/2026-10-15-multi-desktop-build.md`（到第 1340 行），方案 `docs/work/2026-10-13-cc-multi-desktop-plan.md`，ADR-021。node_modules 是真实目录（da-phase3 链到这里）。
已完成（只在本机替身和离线容器验证过）：第 1–6 批、「谁的活谁的电脑做」、保持唤醒两个开关、T1（补丁 v0_0_19–23）、CC 桌面版 1.7.0；最后一次闸门 91 步全过、桌面版 pytest 901 passed / 41 skipped。

- D1 没开始：并入 origin/main 的 16 个提交（**用 merge，不变基**，分支里有 4 个外壳合并提交）。两边都改的只有 architecture.md、test_assistant_ui.py、test_cowork_delete_everything.py；补丁号、ADR 号不冲突。
  合完跑全闸门、`npx tsc --noEmit -p .`、check-architecture、`i18n-build.py --check`；在记录补一节「并入外壳第 11 轮与 main」（80ba3f8f、58d654f4 两次合并的冲突解法和闸门结果也没登记，一起补）。
- D2 没开始：第 7 批前对生产只读核对（数字要重取，10-15 之后部署过两次）：erpcowork 两条 Express 连接哪条是主连接，有效密钥、13 张人工单、定时任务规则各挂哪条（挂后建那条的要先定迁移办法）；v1 结果不明张数（目标 0）。一次性只读客户端，不 exec mariadb、不循环 SSH。方案 7.3 写着生产只读要授权，拿不准先问用户。
- D3 没开始：第 7 批服务端——测试机实时源码起 cowork lab（命令见第 3 节，换成本工作树），迁移后回读电脑数、登记数、config_json / record_revision 不变、v1 排队 / 领取中为 0、reader_device 与 company 回填数，重复 migrate 一次；
  看 MariaDB 1020、readers.moved 同表子查询、move_device 并发、removed 后 rejoin 锁顺序、member_choices 排序。
- D4 没开始：1.7.0 怎么装到两台真机——`companion-release.yml` 只发 main 上的提交，而 ADR-021 要求真机验证通过前不合入 main。可选：测试机 `C:\PearnlyPython311` 从源码跑，或在 Windows 上 `companion/packaging/build.ps1` 自己打包（依赖是否齐全未核实）。装好后按记忆 office-windows-test-pc 用 skin3 普通权限重启。
  注意：**测试机 CC 桌面版现在是 1.4.x 常驻模式，是用户 10-12 要求保持的**；1.7.0 已经没有「常驻」，装上去等于改掉用户定过的状态，通知用户（D5）时一并说明。点验里「1.6 升 1.7.0 首启」要先把这台装到 1.6.0 才测得到。
- D5 用户：第 7 批开工前**通知用户做三件事**（见第 2 节，到时再叫）；第二个成员用专门测试账号，不绑真实操作员或真实 LINE。
- D6 没开始：第 7 批两台真机点验——记录第 945–960 行（第 6 批审查后 1–10）、第 1066–1077 与 1140–1144 行（谁的活谁的电脑做 1–13）、第 1206 行（保持唤醒 8'），
  加追加项：关机后开机自动写完、1.6→1.7.0 首启切换、旧电脑改挂时排着的定时任务单、询问框卡住写入线程、收到 remove 后表现、set_members 收窄后转人工数、choose_account_set 放回原因后的计数、跨电脑接手时主档索引可见性、笔记本经映射读测试机共享。
  **还要并入数字会计员第 1、2 期原定挪到第 7 批的多人实测**：第 1 期「多人共看回读」（`docs/work/2026-10-15-digital-accountant-plan.md:140-141`）；第 2 期「第二个测试用户的多人场景」，以及第 2 期复审记下的 3 条原有行为
  （分类映射时不问费用科目、`_drop_memories` 只删本人可用连接上的记忆、忘掉历史写文件不跟事务，`docs/work/2026-10-16-customer-rules-phase2.md:91、108-109、122`）。
- D7 没开始：只有真 Windows 能验的（真 DPAPI、注册表备份、WNetGetConnectionW 1201、类型 53 网卡、长路径、NAS / Accserver 上算 uid、share 主机别名归一、WSL / Tailscale 网卡排除、降级再升级密钥不变）；推分支后让 Companion checks（windows-latest）跑绿。
- D8 没开始（结果报给用户定默认值）：保持唤醒真机实测——测试机 `powercfg /requests`、拔电行为、退出后能睡、`powercfg /a` 对照；录制笔记本「合盖照样睡」（排除 RecKeepAwake、RecAgent 两个计划任务干扰）；
  实测 PowerSetRequest 能否关屏不待机，做不到就把现代待机电脑「用电池时也保持」默认改关。
- D9 没开始：第 7 批并发压测 3 租户 × 10 台，不调付费识别（连接行锁等待、Q_HEADS 执行计划、_locks 撞 1020 次数、一台挂两条连接、register 请求量、route_device 挑选耗时、remove 与心跳并发）。
- D10 没开始：T1 测试机验证 6 步（70EXP 里 T7 / T8 / T9SINCER，POS 导出造跨年单，不识别不扣费；C 盘可直接写，做完登记写了什么；Accserver / S 盘不写）。**预期是第 3 期合入前的口径**（新年度账套没建时停下；第 3 期合入后改为记进旧账套次年段，要重写复验）。
  **T1 有硬期限：必须在第一次跨年月结（2027 年 1 月）之前上生产**（计划 `digital-accountant-plan.md:51`）。T1 挂在 D 线上、要等第 7 批真机和用户操作，排期有风险；到 12 月初 D 线还合不进去，就把 T1 单独拆出来先上。
- D11 没开始：5 个实验室脚本还按证据 v1 断言（collector、heartbeat、ack-effects、preparation-confirm、transport 的 `scripts/cowork-*-lab-verify.py`），第 7 批用真 Site 时按证据 v2 重写；`test_cowork_folder_computers.py` 10-16 改断言后本机没跑过。
- D12 用户：「第 8 批」没有定义（方案分批表只到第 7 批）——建议定义为「发布」（方案 4.4），写进记录后报给用户。
- D13 用户：定时任务的票在待处理页补选后算谁的活——现在按「仍算定时任务，由读文件夹的电脑写」做的，没问过用户；要改成「点按钮的人自己的电脑写」只需 pending_api 不传 route_device。报给用户确认。
- D14 用户：已有文案「CC 桌面版未连接」10-16 起实际意思是「你没有电脑」，文字没动；要改先给用户看。
- D15 待合入：第 7 批真机和用户试用通过后才合入（ADR-021）：并入最新 main → 全闸门 → 候选离线检查 → 推分支等 Development checks 与 Companion checks → 快进 main → 推送 → 回读。
- D16 待合入（部署）：服务端批 1–4、6 **必须一次发**；部署前只读统计 v1 单，排队中和领取中都为 0 再进维护；整次发布含锁口径 v2 不能直接回退，要回退演练或请用户确认不可回退；部署后回读；
  **紧接着发 CC 桌面版 1.7.0 正式版**（`gh workflow run companion-release.yml --ref main -f target=prod`，读回 `https://pearnly.com/downloads/companion/latest.json`；1.6 客户端的本机常驻在服务端上线后网页上关不掉）。发布前现读线上 latest.json 与 source_sha（线上应是 1.6.0，未重新读回）。
- D17 已知限制（真机遇到再处理）：账套清单是全池并集、不按本人电脑筛；网页没有推送记录详情页；询问框卡住写入线程；共用电脑帮同事登录过一次就一直写他的单；退役只转「排队」的单；名单候选列出已停用的人；「每台一件」只在一条连接内严格；T1 读错日期选错年度等风险；第 2 批前的登记没有内容键。
- 资源：测试机只读共享 `PearnlyExpressRO` 和桌面 `Pearnly-1-share.cmd`、`Pearnly-2-map.cmd`，录制笔记本桌面映射文件——第 7 批用完删（共享由用户本人 `Remove-SmbShare -Name PearnlyExpressRO`）；是否还在未核实。
  测试机共享是只读的，验「经共享两台电脑写同一份账套」要请用户开可写共享。

### E. 数字会计员第 3 期（及第 4–6 期）

分支 `pearnly/da-phase3`，工作树 `.claude/worktrees/da-phase3`，HEAD `a93447de`，基于 multi-desktop 的 `5c11dc5b`，**没推送、没跑过分支 CI**；相对 origin/main 领先 80（36 个是多电脑线的）、落后 91。node_modules 链到 multi-desktop 工作树。
任务记录 `docs/work/2026-10-16-da-phase3-replay.md`、`docs/work/2026-10-16-da-phase3-design.md`，计划 `docs/work/2026-10-15-digital-accountant-plan.md`。
已完成：A / B / C 分类与推前核对、L0 / L1 / L2 档位、定时任务与网页 / LINE 每 5 分钟扫描自动推、CC 桌面版推后写入核对（1.8.0）与 account_history v3（1.9.0）、商品单凭供应商历史升 A、两份同期按公司问一次、界面与四语词条、回放工具。
回放第 6 次（10-17 改证据窗口取法后，甲骨文、源码 8edd94b8）：strict A 11,182 张、准确率 98.43%（下限 98.18%），合并估计 91.14%。**还没到 99.5%，不上线自动推。**

- E1 进行中：判定层 A 类 176 张错全部来自账套记忆定的项（ASIA-FA 现 / 赊 57、科目 10、对方档 2，集中在 69ASIAFC 的 2026 年；ASIA-HA 科目 47；NEWLINK 科目 19；Paisarn 888 科目 17）。要到 99.5% 最多错约 56 张（自算）。修法数据还没指出来，按类找，改完在甲骨文按第五轮跑法重跑 strict / estimated。
- E2 没开始（用户已同意，见第 2 节）：识别闸层补独立金标（现只有 27 张，要约 381 张）。**先找用户要测试 API**；样本按设计「要用户定」第 4 项（一家 × 2 个月约 60–100 页、脚本硬上限），开跑前在记录写明页数和上限。
- E3 没开始（用户已同意）：「问一次」加重问信号——另一份候选账套在上次回答之后有更新录入就再问。改 ask_once 的记住条件（复用工作空间 document_policy 存储），补测试；回放里加对应模拟后量一次。
- E4 没开始：用 HEAD 版 `da-replay.py` 在甲骨文重跑 answer-stkdes / answer-opaque，替换第五轮的旧口径上界（4,158 / 3,748），给出升出来的 A 里推错几张。
- E5 没开始：走库存车道的科目判据（潜力最多约 844 张，推断）；「录入晚 60 天」按「录入日与 ภ.พ.30 截止日取早」单独复核（93% 落在成批录入日）；7 份没有 ISPRD 的账套补真期间后重跑 strict（book_none_unknown 89,233 张，测试机或 S 盘只读导出）。
- E6 没开始：回放工具两处修正——`scripts/da-replay.py:2649` conflict_caught 的说法错了；明细.tsv 补逐张 memory_cap_lost、book_pick、posting_history 和候选账套列。
- E7 没开始：写入层回归——测试机 62 套账套（C:\PearnlyExpress，可随便写）用实时源码 + 1.8.0 以上桌面版写一轮，核对 verify 全为 ok。
- E8 待验证：CC 桌面版 1.9.0 读大账套 STCRD 的耗时（NEWLINK 这类，看 diagnostics 的 posting_copy_seconds / stcrd_seconds，整件租约 900 − 120 秒够不够），测试机实测后才能发版。
- E9 待验证：10-17 的 11 个代码提交（c812126d 至 8edd94b8）之后没有全闸门记录，合并 main 前后各跑一次。
- E10 用户：几处已有文案 / 产品用词要先给用户看：CC 审批步骤名「Choosing how a kind of document is handled」措辞；三个档名「只提议 / 按规则 / 整月」和「自动推送」「全部推送」。
  （「计费用」→「记费用」10-17 已定并上线；「这家公司有多份账套覆盖单据日期」已在 multi-desktop 3e895ef3 按文案闸门改成「该公司有多份账套覆盖单据日期」。）
- E11 没开始：两层达标后，测试机实时源码真机点验界面 + testcowork 一家试点（压缩时间连跑 2–3 个会计期；导入的测试租户不跑定时任务，手动调 `sweep.run(now=…)`、`companies.automation.on_readback`；试点任务固定写 T 账套）。
- E12 待合入：**等 D 线合入 main 后**并最新 main（试算约 10 个文件冲突：push/application/service.py、hooks.py、companies/rules.py、company_choices.py、translations/zh.csv、i18n.ts、settings.tsx、keep-cjk.tsv、test_assistant_ui.py、test_cowork_companies.py；行样式以外壳为准）→ 全闸门 → 推分支 → CI → 用户试用 → 快进 main。
- E13 待合入（部署）：部署服务端（要 bench migrate：新 DocType Cowork Company Automation，OCR History / Push Record 加列；新增每 5 分钟定时扫描）→ **紧接着发 CC 桌面版 1.9.0**。
  **服务端上线后、客户电脑装上 ≥1.8.0 之前，所有自动推都会退成只出提议**（含生产里定时任务现在直接推的 A 类），所以 1.9.0 要紧跟；反过来先发 1.9.0 会被线上 main 当不合规（只收 v1），没有技术闸，靠顺序。
- E14 已知限制：发版顺序没有技术闸（read_params 带服务端版本、桌面版不认 3 时退回 v2，没做，自行定要不要补）；设计第 19 条缺口（法定不得抵扣进项、MR.ERP 定时任务不进档位、没建主档的公司定时任务 A 类一直照推、小票抵进项等「记一次就 A」、销项凭记忆的对方档不查等）；第 1 期留下的「回读完成的站点事件」没做（月结台账靠有上限的定时重取）。
- E15 没开始：第 4 期（从纠正中学、月底「要不要记住」、冲突提示 rule_conflict）→ 第 5 期（本月缺单，**到时问用户提醒怎么发**，建议 CC 写草稿、会计员自己发）→ 第 6 期（对账 / 报税接月结、签字锁月、月报，**到时问用户锁月严格程度**，建议软锁；
  第 1 期留下的「逾期」提示也在这里加——报税状态接进来以前只写截止日）。第 6 期在第 1–5 期之后。
- E16 资源：甲骨文 `/opt/pearnly-replay`（10-18 读回 9.4G：da5、da6 源码与跑法，inputs 含各家 Express 答案原表与真账只读导出，out/2026-10-17-d5、d6）——第 3 期上线、不再重跑后整目录删，并在 `~/.claude/oracle-free-arm.md` 记一笔；
  测试机 `C:\PearnlyStress\_回放\`（d1–d4、prod-export、isprd-2026-10-17）同样第 3 期结束后处理（未核实是否还在）。
- 坑：回放数字都偏乐观（假定每期都按时合格回读）；要和第四轮比只能用 d4compat；`~/pearnly-devtools/da-phase3-replay-d5/run-d5-oracle.sh` 的 OUT 写死 2026-10-17-d5，重跑前改 OUT 并把新源码包传到 `/opt/pearnly-replay/`
  （第 6 次跑法与 8edd94b8 源码在甲骨文 `/opt/pearnly-replay/da6`，开发机没存；分析脚本已转存到 `~/pearnly-devtools/da-phase3-replay-d5/analysis-scratch/`）；
  甲骨文重跑吃满 4 核约 2–3 小时，用 `nice -n 19`，先和「消息含义理解」窗口约时间（甲骨文跑着生产外部守望和 Claude 租户原件）；da-phase3 的前端改动合 main 后要过 main 新加的样式检查（development.yml「CC colors, radii and shadows」）。

### F. 零碎（main 上已有记录的遗留）

- F1 没开始：**T2 MR.ERP 推送方向按所选账套税号判**。main 上 `push/application/service.py` 的 `_prepare_mrerp`（约 578 行起）只取 `Cowork Workspace.tax_id`；要把 MR.ERP 目标公司税号作 `account_tax_id` 交给 `resolve_account_direction`（卖方税号须有效且与账套税号差 ≥3 位，同 Express 2b825a00），
  **匹配闸锚点 `mappings["_own_tax_id"]`（service.py:601）和 `routing.py:52 / :142 / :215-237` 要一起改**，否则替客户记账的票会被匹配闸拦；仿 `tests/test_cowork_push_direction.py` 补测试；testerp 实测一张无买方票。
  **排在 D、E 两条线合入之后**在新 main 上做（两条线都大改了 service.py）。MR.ERP 目录行 attributes 里有没有 tax_id 未核实。
  （旧窗口 10-18 曾给 T2 开过任务卡 task_4da17eb9，因为这个冲突已撤回，不要再按那张卡另开会话。）
- F2 没开始：**T3 CC 桌面版托盘提醒按公司打开**。比旧交接写的多一层：服务端 `folder_intake/contracts.py:486` 和桌面版 `folder_worker.py:63` 的 NOTICE_FIELDS 都没有 view（`tests/test_companion_contract.py` 守两边一致），要一起加；
  `tomo_links.conversation_url` 把 `company:<编号>` 映射成 `?company=<编号>`；`window.tomo.open`（frontend/assistant/src/lib/desktop.ts）也要能接公司目标。版本号排在 1.7.0、1.9.0 之后，建议并进 1.9.x 同一次发布；先部署服务端再发桌面版。
- F3 没开始（用户已同意，见第 2 节）：**T4 简易税票「票面印 0 不拆」金标验证**。规则在 `pearnly_core/tax_documents/rules.py: included_vat` 的 `printed_zero`；先找用户要测试 API，在测试机 `C:\PearnlyStress\语料\金标-2026-10\` 用 `工具/gold_run.py` 跑一批带来源的结果，再用 cowork-vat-included-replay.py 对照。和其它线无依赖。
- F4 没开始（不急）：T6 公司主档后续（合并申报草稿汇总算法、同税号 Express 与 MR.ERP 合成一家、公司级参考文件、批量添加、按公司分权限、网络盘掉线区分、子文件夹建公司），以及 pending_counts / task_overview 在公司多时的耗时（测试机公司多时量一次）。
- F5 没开始：**开户代理维护容器不收**。`infrastructure/production/provision-agent.py` 的 `ensure_maintenance` 建的 `pearnly-maintenance-<产品组>` 只在 `verify_site` / `drop_site` 收；单独改已有站点配置的工单不收，之后没工单就一直留着（文档字符串说的「镜像变了或超过 12 小时重建」只在下一个工单进来时才生效）。
  修法：`activate-product.sh` 激活时收掉该产品组的维护容器；带 `restart` 的改已有站点工单做完即收；补测试（`scripts/production/test-provision-agent.py`）。随下一批部署（要同步部署工具）。
  部署工具和服务器结构归「消息含义理解」窗口在改，动 `infrastructure/production` 前先和它说一声（它 10-18 回复：目前没在改这个目录，约好就可以动）。
- F6 ✅ 用户已定不补（10-18）：「允许」后马上停止，下一轮模型可能以为没做而再提一次，记为已知限制（`docs/work/2026-10-16-cc-shell-rebuild.md:445`）。
- F7 已知限制（外壳第 10、11 轮，不主动修，出现真实现象再按证据处理）：缺口开着时引擎一直读不到会一直压推送、每 6 秒补拉；首次握手按重连多一次补拉；手指挪动超过 10px 就交还、不分方向；只带附件断线后缩略图晚约 97ms 放回；
  交回引擎失败（非停止）时不自动重交；sessions.send 之后 context.active 理论上可能 1020；进 before_request 前抛的错照 Frappe 默认带堆栈（要关得改系统设置 allow_error_traceback，**改配置要用户同意**）；
  回前台点回已在后台答完的对话，先显示旧样子 2–6 秒再定位（以后再看）；联网 33 秒后「正在重新连接」又短暂出现过一次（只见过一次，再看到要带时间线报）。
- F8 待验证（测试窗口有条件时补测，不阻塞）：B11（页面关 15 分钟以上后引擎回调更新 updated_at）、B12（ERP / DMS 侧栏）、C18（晚到的文件卡）、G33 后一半（引擎慢）、「设为全公司」、Safari / iPhone、输入法选字回车等（测试交接第 2 节表格）。
- F9 ✅ 用户 10-18 定「算已通过」，不再补测：10-15 夜间批 2bc4aad1 留下的真机验收一直没有完成记录——电脑上为公司选文件夹（1.6.0）、iPhone 工作状态行与断网重连、Safari 中文输入法回车、多设备侧栏同步、早上 08:30 待处理汇总，
  以及 V12「智能应用控制」拦更新、F11 甲包在 Chrome 106 与新版 Chrome 上的复验（`docs/work/2026-10-15-release-2bc4aad1.md:35-37`、`2026-10-15-acceptance-2bc4aad1.md:386-388`、`2026-10-15-cc-night-batch.md:76`）。
  （原文保留备查。）

### G. 运维与提醒

- G1 用户：testcowork 里 03/0102 那张手写简易税票（727.00，教程数据，当时按税额 0 推进了 Express DEMO02）要不要更正——问用户，没同意不写 Express。
- G2 用户：**11 月 1 日 GitHub Actions 额度重置后，提醒用户把仓库改回私有**（改可见性是账号设置，由用户操作；仓库现在是否公开未核实）。
- G3 用户：用户说过要自己改 dmstest 等账号的密码，确认改了没有。
- G4：「消息含义理解」窗口（会话 local_49e1dd3a…，ListAgents 里叫「消息含义理解」）在做 `pearnly/originals-layer`（ADR-024 统一原件层：Cowork 录入、对账中心已完成，接着做 ERP；做完在测试租户切 Claude 实测），**要动生产时会先来约时间**；
  服务器结构改造（甲骨文取图、内存超配、数据库副本）也归它（记忆 server-restructure-on-hold）。它的工作树、分支不动。和本线有关的：
  - originals-layer（10-18 01:00 它回复）：HEAD `eb7ac4b2`，Cowork 录入、对账中心、ERP（采购 / 销售附件、LINE 收单、预览、下载、删除）都已完成（15953187 / c138c9f9 / 98e6319c / eb7ac4b2）；甲骨文服务已重新部署（按页读、多附件读），目前没有 Claude 租户、不影响线上；本地 Development checks 全过，候选离线检查在重跑。
    还没做：Cloud Run 新版（认 ERO-、对账文档只交引用）、合入 main、生产部署——它说这几步都会先约时间；合入前它会变基到最新 main、跑全闸门。
    **和 D、E 线有 3 个共同文件**（`pearnly_cowork/assistant/tools.py`、`tests/test_cowork_assistant_tools.py`、`tests/test_cowork_delete_everything.py`），它说那边只改了导入路径和测试桩，冲突应该很小；合入先后到时再约。
  - 它还有几件会用到测试租户 / 甲骨文的事：选 Claude 的测试租户实测上传到出结果的速度（三个产品各一份，会撞上 lab，见第 1 节）；生产先开一个内部租户切 Claude；甲骨文手动跑一次备份演练确认能回放到最近 5 分钟（`docs/work/2026-10-17-claude-ocr-route.md:92-96`、`2026-10-17-server-architecture.md:60-63`）。只为协调登记，不归本线。
  - 已告诉它 swap 与容量等待在 10-17 外壳批部署时实际执行过，它会改自己的记录。它确认在测试租户切 Claude 实测前先和新开发窗口约（testcowork 现指向 lab）。
- G5：生产 `/opt/pearnly/state/deploy-ec90fdfc.*`、`mem-ec90fdfc.log` 和「消息含义理解」窗口的 `runtime.env.bak-*` 是部署 / 回退证据，不归本线清理。
- G6 已完成（10-18 更正）：零成本扩容基础 1–3 项 10-02 已做并上线（`b4b02d54`：`infrastructure/production/sizing.py` 按机器核数与内存算参数、`docs/development/production-capacity.md` 配置对照表与可拆分自检清单，记录 `docs/work/2026-10-02-scaling-base.md`）；备份由异地备份与 10-17 恢复演练覆盖；扩容后 100 / 300 人压测按规则只在真扩容时跑。只剩记录里有意留到真扩容时才做的四项（sites 共享文件系统、多机部署工具、Redis 认证、数据库跨机访问）。旧窗口当天一度误报为「没人做」，已向用户和「消息含义理解」窗口更正。

### H. CC 零碎（旧记录里登记、上面 A–G 没列的；10-18 逐条对代码核实过）

来源：`docs/work/2026-10-12-cc-closed-loop.md`、`2026-10-13-tutorial-issues.md`、`2026-10-15-handoff-cc.md`、`2026-10-15-cc-night-batch.md`、`2026-10-15-acceptance-2bc4aad1.md`、`2026-10-16-cc-shell-rebuild.md`、
`~/Downloads/pearnly-cc-video/`。核对工作流 `wf_c5e3984c-267`（109 条：已做并在 main 45 条，其余见各线与下表）。

功能 / 缺陷（都没做，按影响大小）：
- H1 `assistant/toolrun.py` 的 execute / decide / `_finish` 执行工具前不查会话主人是否已停用（复核确认）→ 停用就不执行、记 error，补测试。
- H2 定时任务换文件夹时，相对路径同名、且大小和修改时间也一致的文件会被当成已处理而跳过（复核确认）→ 台账键带上文件夹，或换文件夹时清该任务台账，补测试。
- H3 识别失败后重调，录入工作台多出一份新原件（旧的失败那份还在）→ 用户说「其它按期执行」，由开发窗口按证据自定算不算缺陷；算就在重调成功后处理旧原件。
- H4 点 Windows 正式通知时 CC 桌面版没在运行，打不开对话（没有系统级激活，复核确认）→ 要做就给通知注册系统级激活。
- H5 CC 桌面版按 F5 不刷新，只有 Ctrl+R（复核确认）→ 加 F5，随下次桌面版发布。
- H6 「立即运行」约 6 分钟后才执行 → 要缩短得改通知电脑的方式。
- H7 分店拦截时 CC 没说出是按对话里指定的分店判的 → 工具结果带上判定依据。
- H8 打开新对话页时，本站没配 CC 不会先提示 → 要做需加一个配置状态信号。
- H9 打开长对话「正在加载消息」慢（服务端按页取完全部事件）→ 可选：加大每页条数或缓存已取历史。
- H10 新绑定的文件夹要等重取详情才出现（多一次请求，影响小，可不做）。
- H11 CC 对话推送路径 `choices_view` 写入可能撞 1020 → 出现用户可见失败时再按入口重试统一处理。
- H12 CC 回复偶尔夹英文过程句（提示词问题，出现再按证据处理）。
- H13 iOS 启动图 PNG 没按新底色重新生成 → 在有 Pillow 的环境跑 `python3 scripts/build-ios-splash.py` 并提交。
- H14 侧栏「悬停 + 任意后代」规则（含 group-hover/note）让进出侧栏整块重算样式 → 可并进 B 线或 A 线顺手改。
- H15 已知限制（不主动修）：侧栏「创建日期」排序只排已加载的行；打开已结束的对话后文件列表晚到时文件卡不再贴底；第一批登记的三条（删任务后重建会重新处理已有文件、同一负责人任务分在多台电脑时提醒 / 简报各发一份、Desk 新建表单接续停用任务时仍显示「现有 N 份」）。

真机 / 真环境验收没有完成记录——**用户 10-18 定 H16–H18 算已通过、不再补测**（下面只留作备查；以后真遇到相关现象再按证据修）：
- H16 V1 / V2 iPhone 主屏幕 App 工作状态行、断网与卡死区分；V3 往上翻不拉回、Mac / iPhone Safari 拼音选字回车不误发；V4 同账号电脑 + 手机互相增删改的侧栏同步；V5 公司主档全流程、V6 公司对话查询范围（「这个月」「所有公司」）；
  V7 待处理当场选与每天 08:30 汇总（推出、不重复、0 张不推）；V8 电脑上为公司选文件夹（1.6.0）；V12 智能应用控制拦更新的提示（排第 7 批录制笔记本）；V13 浅 / 深色 × 电脑 / 手机整体视觉；F11 手机弹窗、返回键、长按、子菜单在 Chrome 106 与新版 Chrome；F13 刚打开页面后第一条消息静默丢失（用户前台真键盘确认一次）。
- H17 安装入口：安卓「安装 CC」真机、LINE 内置浏览器里打开 CC 链接是否跳到默认浏览器（要用户手机）。
- H18 闭环工作包的真环境项（`cc-closed-loop.md:387-397、466-467`）：L1 回读 Express（开着 / 录单 / 整理时各读一次、总账逐科目对打印件、大账套耗时、三种对账端到端）；L2 从文件夹取文件的边界（2000 份以上、25 MB PDF、联接点 / 符号链接被拒、映射盘、MR.ERP 只读成员）；
  L4 ภ.พ.30 草稿对 Express 税报逐项对照；L5 ภ.ง.ด.3 / 53 一张凭证付几张票、分期、跨月配对；L8 BBL 误认要先跑零费用对照；L9 POS 按天录销售的 Express 打开 / 打印 / 修改、存货 POS、真实 Ocha 导出、0 元赠品、现金净额为负；F7 存货账套进货先写、销售后写端到端；
  提醒时间控件没在浏览器实点过；CC 桌面版 `folder_intake.machine.sync` 出过一次 500（查生产 Error Log 是否再现）；新建定时任务表单是否显示现有份数。
- H19 真机：手机让 CC 录一张票、电脑锁屏状态下完成写入，没有单独证据 → 1.7.0 上线后按「保持唤醒」新口径在第 7 批验（D6）。

用户 10-18 已定：
- H20 ✅ 不重录。CC 泰语新手教程：成片开头「收到客户单据、按公司分文件夹」没补拍（用示意卡代替），画面是 10-16 外壳重构前的旧界面；保留原样。
- H21 ✅ 保留原样：教程口径「下班后电脑常驻跑」和 1.7.0 去掉常驻冲突，用户定不重录、不加字幕。
- H22 ✅ 作废：方案 A「办公室小主机当 CC 的电脑」。

## 5. 推荐执行顺序（跨任务线）

1. **马上可并行**（互不冲突，不需部署）：A2→A3（第 12 轮交测试窗口；A1 已完成）；B2（B0 合入 main，B1 已完成）；C1（文案闸门合入 main）。
   B2、C1 和后面的 D、E 都改 development.yml，**一条一条串行合入**（合一条、下一条再变基、各跑全闸门）；C1 合入前先告诉各分支和「消息含义理解」窗口共享钩子要生效。
2. **C 线接着**：C3 三个真缺陷先做（文件不和别的分支重叠），再 C2 其余各节（涉及 i18n.ts、Cowork 翻译、settings.tsx 的等 D、E 合入或逐个变基），C4、C5。改动按产品分批，跟下一次部署走。
3. **D 线**：D1（并 main）→ D2（生产只读核对）→ D3 / D4（lab 与装 1.7.0）→ 通知用户 D5 → D6–D11 真机与压测 → D15 合入 → D16 部署 + 发 1.7.0。
4. **E 线**：E1、E3、E4–E6 可在 D 线进行时在 da-phase3 分支上继续（回放在甲骨文）；E2 找用户要测试 API 后跑；D 合入后 E12 合入 → E11 试点 → E13 部署 + 发 1.9.0（可带上 F2 的 T3）。
5. **B 线后续**：B4（B1 标准层，B0 合入后即可，和 A 并行）→ B5 → B6（先看 D、E 合了没有）→ B7（A 合入后）→ B8 → B9（和 A7 串行）。
6. **F、H 线**：H1、H2 是真缺陷，可在任何一批顺手修（H1 改 pearnly_core 要跑候选检查）；H5、F2 随下一版桌面版；H16–H18 用户已定算通过、不补测；F5 和「消息含义理解」窗口约好后随最近一批部署；F3（T4）找用户要测试 API 后随时跑；F1（T2）在 D、E 合入后；F4 不急。
7. **测试机 lab 依次占用**：第 12 轮（lab 已开，只换源码）→ 标准化各批验收 → D 第 7 批 → E 第 3 期试点；中间「消息含义理解」窗口要切 Claude 实测测试租户、要合 originals-layer 时事先约。用完 lab-stop，恢复线上 testcowork 定时任务。
8. **部署**：攒批。第一批建议 = A（第 12 轮）+ C3 三个真缺陷 + F5，过闸后一次构建激活；之后按 D、E 各自的发布要求单独成批（它们都要紧跟发桌面版）。生产部署都先和「消息含义理解」窗口约时间。
9. **T1 期限**：D 线到 12 月初还合不进 main，就把 T1 单独拆出来先上（2027-01 第一次跨年月结前必须在生产）。
10. **清理依赖链接的顺序**：cc-shell-rebuild 要等 A、B 两个工作树合入或换成自己的 node_modules、且 lab 换了源；f19-push-summary 要等 C1；multi-desktop 要等 da-phase3 处理完它的两个链接。

## 6. 还要用户定的（只剩这些）

- D12：第 8 批定义成「发布」可不可以（报给用户确认）。
- D13：定时任务的票在待处理页补选后，算定时任务（读文件夹的电脑写）还是算点按钮的人。
- D14、E10：几处已有文案 / 档名（「CC 桌面版未连接」；审批步骤名；「只提议 / 按规则 / 整月」「自动推送」「全部推送」）。
- D8 实测后：现代待机电脑用电池时「保持唤醒」默认值。
- D2：第 7 批前的生产只读核对是否要再授权（拿不准就问）。
- E15：第 5 期缺单提醒怎么发、第 6 期锁月严格程度（做到那一期再问）。
- G1、G3：03/0102 教程票要不要更正；dmstest 等密码改了没有。
- F7 最后一条：要不要改系统设置 allow_error_traceback。
- D4 / D5：第 7 批装 1.7.0 会让测试机离开用户 10-12 要求保持的常驻模式，通知第 7 批三件事时一并说明。

## 7. 工作树、分支与保留资源（10-18 交接时）

| 路径 / 分支 | 状态 | 为什么留 | 什么时候删 |
|---|---|---|---|
| `/Users/skin/pearnly`（main） | `2574ef94`，干净 | 长期主目录，只做拉取、快进、推送 | 不删 |
| `cc-drag-engine` / `pearnly/cc-drag-engine` | `47d51d37`，没推送 | A 线 | A8 合入并回读后 |
| `cc-standards` / `pearnly/cc-standards` | `a8c8ad27`，没推送 | B 线（B0） | B2 合入后（B1 起每批另开分支） |
| `copy-gate` / `pearnly/copy-gate`（远端 5b429fd0） | `cc0ec1f7` | C 线 | C1 合入后删本地与远端 |
| `multi-desktop` / `pearnly/multi-desktop`（远端 f87a0585 已过时） | `58d654f4` | D 线；node_modules 是真实目录，da-phase3 链到这里 | D15 合入后；删前先处理 da-phase3 的链接 |
| `da-phase3` / `pearnly/da-phase3` | `a93447de`，没推送 | E 线 | E12 合入并部署后 |
| `cc-shell-rebuild` / `pearnly/cc-shell-rebuild`（远端已删） | `ec90fdfc`，已在 main，干净 | 10-18 01:10 前测试机 lab 挂着它的实时源码（现已 lab-stop，不再挂它）；cc-drag-engine、cc-standards 的 node_modules 链到这里 | A、B 两个工作树换成自己的 node_modules 或合入后（并确认 lab 已不挂它） |
| `f19-push-summary` / `pearnly/f19-push-summary`（远端已删） | `13bb3e69`，已在 main，干净 | copy-gate 的 `frontend/assistant/node_modules` 链到这里（锁文件不同，不能直接改指） | C1 合入或 copy-gate 换成自己的 node_modules 后 |
| `handoff-2026-10-18` / `pearnly/handoff-2026-10-18` | 交接文档进 main 用 | 本交接 | 快进 main 后旧窗口当场删 |
| `originals-layer` | 「消息含义理解」窗口的 | 不归本线 | 不动 |
| `pensive-blackwell-751d70`（游离 7c9bb445，无分支） | 10-15 建、内容已在 main、干净；两份 node_modules 10-18 经用户同意已删（腾出约 0.5G），只剩约 71M | 测试窗口 10-18 确认不用、同意删；**但手机调试的 adb 服务（10-18 时 pid 72399）当前目录在这里**，测试窗口连 A14 的端口转发靠它 | adb 服务重启后（`adb kill-server` 或重启电脑）用 `git worktree remove` 删，不用强制 |
| 测试机 cowork / erp lab 与数据卷 | 10-18 借给原件层实测后已还：两个都 lab-stop、配置恢复、testcowork 指向线上 | 第 12 轮、标准化、多电脑第 7 批继续用 | 不删；不用时 lab-stop |
| 甲骨文 `/opt/pearnly-replay`（9.4G） | 回放数据 | E 线还要重跑 | 第 3 期上线后（E16） |
| 测试机 `PearnlyExpressRO` 共享、两台桌面映射文件 | 未核实是否还在 | D 线第 7 批 | 第 7 批用完（共享由用户删） |

符号链接一览（删工作树前必查，不能顺着链接删共享目标）：cc-drag-engine、cc-standards → cc-shell-rebuild；copy-gate → f19-push-summary（assistant）与主目录（frontend）；da-phase3 → multi-desktop。

## 8. 旧窗口（「工作清单窗口交接」）收工状态

- 10-17 至 10-18 已删：工作树与本地分支 cc-round11、cc-cards、cc-scroll-fix、cc-ef-fixes、cc-drag-perf、cc-drag-b11b、cc-focus-fixes、release-ec90fdfc，本地分支 cc-d-fixes，
  远端分支 pearnly/cc-shell-rebuild（lease ec90fdfc）、pearnly/release-ec90fdfc（lease 2574ef94）、pearnly/f19-push-summary（lease 13bb3e69），临时工作树 scratchpad/a14inval/build/src；
  生产 `pearnly-maintenance-cowork` 容器（用户同意后删，读回无非生产容器）。删前都核对过已在 main、没有未提交内容；node_modules 链接只删链接。
- 记忆：新增 `user-decisions-2026-10-18`；更新 `next-batch-items`、`ocr-budget-pause-2026-10`、`da-phase3-goods-history-a`、`copy-changes-need-approval`；删掉过时的 `test-window-handover-2026-10-16`；
  测试窗口建的 `test-window-handover-2026-10-18` 已补上本文件路径。
- 第 12 轮与标准化补修两个后台工作流都已跑完并写进记录；本机临时目录里旧窗口起的 4 个 node 服务（a14inval 复刻页）已停。旧窗口关之前没有在跑的后台进程。
