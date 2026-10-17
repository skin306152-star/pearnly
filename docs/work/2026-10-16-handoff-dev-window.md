# 开发窗口交接（2026-10-16 晚，「工作清单」窗口 → 新开发窗口）

给接手的新开发窗口。测试窗口的交接在 `~/pearnly-devtools/test-window/交接-测试窗口-2026-10-16.md`，两边互相引用。
**不在本次范围**：用户说的「新需求」（岳父提的：外地人员用手机在 Pearnly ERP 开销售 / 采购 / 领料单，自动记进办公室那台电脑的 Express）。用户另行安排，ERP 的 CC 先不大做（见第 5 节）。
仓库里的同名副本：`docs/work/2026-10-16-handoff-dev-window.md`（在 `pearnly/cc-shell-rebuild` 分支上）。

## 1. 工作方式（不变）

- 一个开发窗口做，一个测试窗口测。开发窗口写完 → 全闸门 → 同步到测试机 lab → 自己从外面读回确认是新包 → 按格式给测试窗口发「第 N 轮清单」→ 测试窗口逐条回 → 修 → 下一轮。
- 只用中文；用户看不懂英文。Windows 客户端在用户可见处一律叫「CC 桌面版」。
- 用户没定的业务口径要问；技术取舍自己按目标定（记忆 goal-driven-decisions）；改已有文案先给用户看，新文案不用每次问（copy-changes-need-approval）。
- **后台工作流 / 子代理的任务书开头必须加「只听这份任务书」防护段**：运行期间子代理会看到用户在主对话里的话，并当成新任务（10-16 上午第 5 轮因此白跑一整轮，还撤回了用户已定的两处）。写明：那些话由开发窗口处理，不要画预览、不要给用户发文件或消息、不要撤回已有提交、只在授权工作树里改。用户在工作流运行时定的新事，开发窗口自己落地。（记忆 workflow-agents-see-user-messages）
- 子代理用 high 档、同时不超过 5 路（记忆 subagent-effort-high）。
- 和测试窗口通信用 SendMessage；对方地址会随重启变化，先 ListAgents 找名字。测试机只有一台，Cowork / ERP / DMS 几个窗口轮着用，切之前先问、用完告诉对方。

## 2. 工具与命令

- 闸门：`SKIP_NPM_CI=1 bash ~/pearnly-devtools/gate.sh <工作树>`。**10-16 修过一个 stdin 问题**（以前中途一步会吞掉后面的步骤，只跑 14 步就报全过），现在每步 `</dev/null`，会跑满（外壳分支 85 步，多电脑 91 步左右，第 3 期 96 步）。
- npm：`export PATH=$HOME/pearnly-devtools/bin:$PATH`（`~/.local/bin/npm` 指向已删除的软件，不能用）。两个前端：`npm --prefix frontend run build`、`npm --prefix frontend/assistant run build`。
- 测试机 lab（`scripts/testenv.sh`，镜像用线上 digest；10-16 晚之前是 `ghcr.io/skin306152-star/pearnly/frappe@sha256:f9bf655157d2dbd046e08e306ee0e6af85fbc9e8bb2551fb902f3faf74276eb3`，10-16 晚 DMS 窗口部署后线上全部是 `…@sha256:b9da6b58c35ae05c55fa1fb455d9daecc77dba6e22c3d578b4d9a148682e1e97`，以后用这个，见第 3 节）：
  - 起：`PEARNLY_TESTENV_SRC=<工作树> PEARNLY_TESTENV_IMAGE=<digest> PEARNLY_TESTENV_LOCAL_SPA=1 bash scripts/testenv.sh lab-start cowork`（不带 REFRESH，保留测试数据；先构建两个前端）。
  - 改代码后同步：同样的环境变量，`... testenv.sh live cowork`。
  - 收：`bash scripts/testenv.sh lab-stop cowork`（域名切回线上、恢复线上测试租户的定时任务、栈停卷留）。一次只跑一个产品；起别的产品前先 lab-stop 当前的。
  - 同步后读回：`curl -s https://testcowork.pearnly.com/assets/pearnly_core/assistant/index.html | grep -oE 'index\.[A-Za-z0-9_-]+\.js'`，和本地 `apps/pearnly_core/pearnly_core/public/assistant/index.html` 对上，再比文件大小。
- 候选离线检查（改了 pearnly_core 合入前必跑）：`~/pearnly-devtools/cand-run.sh`（记忆 core-change-candidate-lane）。
- 生产部署原样命令见记忆 production-deploy-steps；现在没客户，进维护立即部署（deploy-at-night-maintenance）。

## 3. 分支与工作树（10-16 晚，都只在本地，除特别注明都没推送）

| 分支 / 工作树 | HEAD | 内容 | 下一步 |
|---|---|---|---|
| `pearnly/cc-shell-rebuild`（`.claude/worktrees/cc-shell-rebuild`） | 代码 `93c1ceda`，其后只加了这份交接文档的提交 | CC 外壳重构第 1–9 轮、第 1 期月结台账、第 2 期统一客户规则、F18、F19、侧栏五处、统一拖动、跨设备排列、CC 三处修复、发不出去显示原因（已并入，`pearnly/cc-send-error` 工作树与分支已删） | 第 9 轮测 → 候选检查 → 推分支等 CI → 快进 main → 部署 |
| `pearnly/multi-desktop`（`.claude/worktrees/multi-desktop`） | `b9dedf40` | 多电脑 T5 第 1–6 批 + T1 跨年度 + 「谁的活谁的电脑做」简化 + 保持唤醒两个开关；已并入外壳分支 f5c04929（之后外壳的改动还没并） | 外壳合入 main 后变基 / 合并 main → 第 7 批真机 |
| `pearnly/da-phase3`（`.claude/worktrees/da-phase3`） | `bfad4ca8` | 数字会计员第 3 期：分三类、档位、触发、推后核对（CC 桌面版 1.8.0）、界面、回放工具；基于 multi-desktop 5c11dc5b | 第四轮回放没达标，按第 6 节第 5 项按类再修 → 达标后试点 → 排在多电脑之后合入 |
| `pearnly/f19-push-summary` | `13bb3e69`（远端也有） | 已含在 cc-shell-rebuild | 外壳合入 main 后删本地与远端分支 |
| `pensive-blackwell-751d70` | main | 测试窗口的工作树 | 不动 |
| `pearnly/dms-booking-readback-card` | — | DMS 窗口的，不是本窗口的 | 不动 |

远端：`origin/main` = `372dd6c4`（10-16 晚 DMS 窗口合入 8 个提交，只动 `apps/pearnly_dms`、`frontend/src`、DMS 测试与检查脚本，和外壳 / 多电脑 / 第 3 期的文件不重叠）；远端 `pearnly/cc-shell-rebuild` 还停在旧的 `5db2c92b`，`pearnly/multi-desktop` 停在 `f87a0585`，`pearnly/f19-push-summary` 在 `13bb3e69`，`pearnly/dms-booking-readback-card` 是 DMS 窗口的。
**生产与 lab 底座（10-16 晚）**：DMS 窗口（「消息含义理解」）已用 main `372dd6c4` 的镜像 `sha256:b9da6b58c35ae05c55fa1fb455d9daecc77dba6e22c3d578b4d9a148682e1e97` 部署完生产并回读：ERP / Cowork / DMS / 基础层 billing-web 全部在这个镜像，六站退出维护，20:12 后无错误日志。部署中途 20:14–20:24 整机内存被挤满、生产约 10 分钟无响应，之后自行恢复（DMS 与基础层 20:31 补激活，全程可用内存不低于 22.9 GB）；发布记录 `docs/work/2026-10-16-release-372dd6c4.md`（DMS 窗口会快进进 main）。
- 外壳分支还没合 `372dd6c4`；合入 main 时按「主线集成」先把它带上再跑闸门。
- lab 底座以后用 `b9da6b58`（下次 lab-start 时）。
- 外壳部署前先看发布记录里内存被挤满有没有结论；部署顺序与容量检查照记忆 production-deploy-steps。

## 4. 测试机状态（10-16 晚）

- **cowork lab 已收（10-16 深夜，用户要让三台设备睡眠）**：`lab-stop cowork` 已跑，testcowork 回到线上（读回 107.155.65.74、线上包 `index.BLUtTiba.js`），线上 testcowork 定时任务已恢复，测试机容器已停、数据卷保留、WSL 保活已停。收之前 lab 跑的是 `93c1ceda`、包 `index.DGYXsoIj.js`（第 9 轮还没开测）。DMS 窗口借用早已结束（testdms、testerp 都在线上）；快照工作树 `lab-snap` 已删。
- 测试机、录制笔记本、本机当晚都睡眠。**唤醒后两台 Windows 的 IP 可能变**（DHCP），SSH 连不上先按记忆 office-windows-test-pc / recording-laptop 找新地址再改 `~/.ssh/config`。
- cowork lab 的卷保留（测试数据都在，详见测试窗口交接第 4 节）；lab-start 不带 REFRESH 就接着用。
- 第 9 轮清单已写好：`~/pearnly-devtools/test-window/第9轮清单-2026-10-16.md`（19 条），新测试窗口照它测。
- 测试机 CC 桌面版以 skin3 在线（cowork lab 起来后看「单据文件夹」卡是否显示 DESKTOP-MN2BRKR 正常）。

## 5. 用户这两天定下的口径（照做，不要再问）

外壳与界面：
- 侧栏照 DeepSeek 截图：公司组头前面是公司图标（Building2），对话缩进到公司名起点；「显示更多」也缩进对齐；组头「···」打开分组和排序菜单（没有「打开公司」「在这家新建对话」）；「＋」在这家新建对话；漏斗图标全删。「只改我提到的地方，其它不要动」。
- **所有一行放不下的文字**：末尾渐隐（不用省略号），只在真放不下时淡；鼠标放上去约 0.15 秒后匀速 40px/s 滚到末尾停住，离开立即回到开头。同一行不能两处一起滚。
- **所有拖动统一一套效果**（照 Claude 视频 `/Users/skin/Downloads/IMG_5192.MOV`，「全部参考视频，非常丝滑」）：共用模块 `components/ui/drag.tsx`。侧栏：对话拖到别的公司 = 移过去（月份分店留空、置顶不变），拖到「最近」= 移出公司；公司组、置顶可拖成任意顺序；公司里其余对话按排序方式排。（记忆 drag-unified-rule）
- 侧栏悬停光标是手指，不是抓手（只改光标，照样能拖）；右侧栏卡片把手仍是抓手。
- **聚焦不要光晕**，只留蓝色描边；**菜单项、下拉选项不画蓝框**，只用底色（「这个边框颜色不要太泛滥」）。
- 个人排列（公司组顺序、置顶顺序、卡片顺序和开合、分组和排序）按人跨设备同步，后改的为准；组折叠按设备。
- 第一次发送：用户气泡不扫光，「思考中」在左边 CC 一侧。
- ERP：先不大做，岳父的新需求可能要重构 ERP 的 CC；ERP 只做最小回归（10-16 已做：没弄坏）。（记忆 erp-cc-hold）

数字会计员：
- 第 2 期：优先级 本票人工裁决 > 账套记忆 > 公司规则 > Express 历史已定 > 名称匹配；删公司规则后由 Express 历史接上算对；回读结果给能用这条连接的同事都看；CC 不叫人去 Express / 待处理改已入账的单。
- 第 3 期：升档阈值建议 A（一个完整期、合格回读覆盖的 A 类 ≥20 张、被改 ≤1%）；定时任务里设好的规则算人定规则，新客户第一个月照推 A 类；自动推遇重复票第二张留给人（人推照旧不查重不拦）；永续 / 混合库存账套的商品单按公司问一次入账方式；新年度账套没建时下一年的单记进旧账套下一年期间（T1 随之改）；识别闸层样本从生产机只读导出；S 盘真账可只读导出（不写）。开发窗口自定：抵进项按方向推断算 A；账套记忆要连续 3 张同科目才算 A。

多电脑：
- **谁的活谁的电脑做**：发起人自己登录过 CC 桌面版的电脑写；定时任务由读文件夹的电脑写；关机就排队、开机接着做；同事的电脑不代写；都够不到就直说。界面没有「常驻」。（记忆 multi-desktop-own-computer-rule）
- 保持唤醒照 Claude 两个开关「CC 开着时保持电脑唤醒」「用电池时也保持」，默认都开；合盖照样睡；不做开机锁屏。现代待机电脑上现在会连屏幕一起保持亮（验证过的做法），第 7 批在测试机实测 PowerSetRequest 能否做到关屏不待机，不行就把这类电脑的「用电池时也保持」默认关。
- Cowork 的 LINE 是成员绑定自己的 LINE 后自己发单，没有「客户从 LINE 发单」。

## 6. 下一步（按顺序）

（10-16 晚定稿）

1. ~~外壳：把 `pearnly/cc-send-error` 并进 `pearnly/cc-shell-rebuild`，跑全闸门~~：已做，合并提交 `93c1ceda`，全闸门 85 步全过（exit 0），两个前端构建通过。
2. **新开发窗口第一件事：重起 cowork lab 并发第 9 轮清单。** 先确认测试机醒着、SSH 通、没别的窗口在用；在 `pearnly/cc-shell-rebuild` 工作树构建两个前端，然后 `PEARNLY_TESTENV_SRC=/Users/skin/pearnly/.claude/worktrees/cc-shell-rebuild PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:b9da6b58c35ae05c55fa1fb455d9daecc77dba6e22c3d578b4d9a148682e1e97 PEARNLY_TESTENV_LOCAL_SPA=1 bash scripts/testenv.sh lab-start cowork`（不带 REFRESH）→ 从外网读回包名与大小（代码同 93c1ceda，应仍是 `index.DGYXsoIj.js`，以实际构建为准）→ ListAgents 找到新测试窗口，把 `~/pearnly-devtools/test-window/第9轮清单-2026-10-16.md` 发给它并告诉它实际包名；提醒手机先强制关闭 Chrome / CC 应用再打开（记忆 testenv-remote-pitfalls）。清单第 17 条（没配 CC 的站点）要起 ERP lab 才测得到，和测试窗口商量，可跳过。
3. 第 9 轮通过后：测试机跑 `cand-run.sh`（这批改了 pearnly_core）→ 推 `pearnly/cc-shell-rebuild` 等 Development checks 绿 → 主目录快进 main 到同一提交并立刻推送 → 进维护部署 ERP / Cowork / DMS → 回读 → 写发布记录、更新验收记录 → 清理工作树分支（cc-shell-rebuild 本地与远端、f19-push-summary 本地与远端；cc-send-error 与 lab-snap 已删）。部署后请用户复验手机深色导航条（A4，必要时重装主屏应用）。
4. 多电脑：main 更新后合并进 `pearnly/multi-desktop` → 第 7 批真机联调（要用户：录制笔记本输入 skin3 密码；testcowork「团队管理」加第二个成员）；第 7 批前对生产做只读核对（erpcowork 两条 Express 连接映射、结果不明数为 0、并发压测）；第 8 批；部署服务端后发 CC 桌面版正式版（记忆 companion-staging-from-main、companion-update-via-heartbeat）。
5. 第 3 期：**还没到 99.5%，不上线自动推。** 10-16 晚第三轮回放后做了一次审查（15 条，13 条成立已修），修完跑了第四轮；以第四轮为准，第三轮的闸层 98.13% / 合并 94.8% 已撤回。结果在 `docs/work/2026-10-16-da-phase3-replay.md`「第四轮」，明细在测试机 `C:\PearnlyStress\_回放\2026-10-16-d4\`，脚本备份在 `~/pearnly-devtools/da-phase3-replay-d4/`。
   - 判定层 A 类 **95.91%**（下限 95.53%，11,511 张，占 2.92%）；A+B 占 20.87%。只有 Paisarn 888 一家单独过线（99.78%）。
   - 识别闸层只算独立金标：27 张，92.59%（下限 76.63%）；合并估计约 88.8%。Express 答案那组单列、不进合并、不算进 381 张（会计写法和票面不同，例如 BakeLab 7 处是答案记 00001、票上没印）。
   - 阈值 A：6 家升 L1、降档 7 次，自动推 8,873 张、错 137 张（98.46%）。
   - 审查后的有意变化：按公司选的商品单入账方式改为 B（各家仍有 0–15% 的单和会计记法不同）；存货每一行要有着落才算 A；抵 / 不抵进项要原文标题（与识别票种）对得上才算 A；账套记忆的科目要「合格回读覆盖、没被改」的连续 3 张（`memory_evidence`）；新年度账套没建的判断更严（有期间没报全或本年起始更晚的账套就停下）；月结台账、主档页等按月取账套也认次年段（`account_match.for_month`）。三条新词条已收短（还没上线）。
   - 剩下的错主要在 ASIA-FA：账套记忆定的现 / 赊 260 张、对方档 65 张。
   - **下一步（按类，都没施工）**：① 把 `memory_evidence`「最近连续 3 张有证据」推广到现 / 赊和对方档，重跑判定层；② 两份同期账套：先把回放里估的期间换成真期间，分清回放偏差与真问题，真有两份的处理办法要用户定（第 7 节）；③ 商品单要进 A 得有逐张可靠判据（如这家供应商在 Express 的存货历史），回放按答案（STCRD）造明细行才量得到；④ 「录入晚 60 天」改用接近真实上传日再报一行。
   - 达标后测试租户一家整月试点；合入排在多电脑之后。
6. 之后：第 4 期（从纠正中学、月底「要不要记住」、冲突提示）→ 第 5 期（本月缺单，要用户定提醒怎么发）→ 第 6 期（对账 / 报税草稿接进月结、签字锁月、月报，要用户定锁月严格程度）。计划在 `docs/work/2026-10-15-digital-accountant-plan.md`。

## 7. 还要用户拍板的

- 第 3 期：识别闸层能算数的独立金标只有 27 张（生产机上的人工金标文件已随旧目录删掉，回放库里的识别结果大多没有独立金标），离 381 张差很远；是补独立金标（人工核票），还是花钱补识别（10-02 起识别预算暂停），要用户定。
- 第 3 期：语料里真有两份同期账套时，认「最近有录入或有合格回读的那份」，还是「按公司问一次哪份是现用的」，要用户定（先做第 6 节第 5 项 ② 分清回放偏差再问）。
- 保持唤醒：现代待机电脑用电池时默认开还是关（看第 7 批实测）。
- 零碎：T2 MR.ERP 推送方向按账套税号判；T3 CC 桌面版托盘提醒按公司打开（要发桌面版）；T4 简易税票「票面印 0 不拆」金标验证（约 70–140 泰铢，要用户同意）；T6 公司主档后续（不急）。见 `docs/work/2026-10-15-handoff-cc.md` 第 2 节。
- 运维：生产 `pearnly-maintenance-cowork` 容器来源（删前问）；testcowork 那张 03/0102 教程票要不要更正；**11 月 1 日 Actions 额度重置后提醒用户把仓库改回私有**；用户说要改 dmstest 等密码，确认改了没有。

## 8. 本窗口（「工作清单」）收工状态

- 没有在跑的工作流、子代理或后台命令；第 3 期工作流 `wf_26d26f90-025` 已结束（6 个代理全部完成）。
- 已删：工作树 `lab-snap`（快照，已含在外壳分支）、工作树与本地分支 `pearnly/cc-send-error`（`f26ba242` 已并入 `93c1ceda`，远端没有）。删前核对过没有未提交内容；两处 `node_modules` 是指向外壳工作树的符号链接，只删了链接。
- 保留（都属于本窗口的任务，交给新开发窗口）：
  - `pearnly/cc-shell-rebuild`：第 9 轮待测，lab 正挂着它的实时源码；合入 main 并部署后删本地与远端。
  - `pearnly/multi-desktop`、`pearnly/da-phase3`：未合入，按第 6 节第 4、5 项继续；合入后删。
  - `pearnly/f19-push-summary`（本地与远端 `13bb3e69`）：已含在外壳分支，外壳合入 main 后删。
  - cowork lab 的数据卷（测试机）：已 lab-stop，卷留着给第 9 轮继续用。
- 记忆：`test-window-handover-2026-10-16`（两个窗口的交接指针，两边接上后删）。

