# CC 前端标准化（尺寸、间距、字号、层级、动效）

## 用户口径

- 用户 10-17：「所有前端框架都要标准开发和做法」，随后限定「只是cc」。
- 落到本任务：CC 前端（`frontend/assistant`，网页、CC 桌面版窗口、手机三端共用一份源码）一律标准开发；样式取值只走 `globals.css` 的标准定义，用闸门拦写死值。其他前端（ERP、DMS、Cowork 的 Desk 页、登录页）不在范围内。
- 一贯口径（用户记忆）：照 Claude 做；技术取舍由开发窗口自己定，不拿调参选项问用户；有法定或正确口径就照做；体验改进直接做，做完汇报；不做过渡版。

## 任务信息

| 项 | 值 |
| :- | :- |
| 基线 | 写方案时是 `pearnly/cc-shell-rebuild` 803c483b；cc-shell-rebuild 10-18 已全部并入 main 且删了远端分支，B0 变基到 main 669427ca 后合入 main |
| B0 分支与工作树 | `pearnly/cc-standards`，`.claude/worktrees/cc-standards`；`frontend/assistant/node_modules` 是指向 cc-shell-rebuild 工作树的符号链接 |
| B1 起 | 每批从最新 `origin/main` 开 `pearnly/cc-std-bN` 分支和工作树，按「主线集成」直接合入 main（变基 → 全闸门 → 推分支等 CI → 快进 main）；各批用自己的 `node_modules`，不再链到别的工作树 |
| 并行分支 | `cc-drag-perf`、`cc-scroll-fix`、`cc-ef-fixes` 正在改 `frontend/assistant/src`。10-17 写本记录时，已提交的改动是：drag-perf 改 `thread-list.tsx`、`drag.tsx`、`globals.css`、`lib/layers.ts`；scroll-fix 改 `thread.tsx`、`lib/follow-scroll.ts`；ef-fixes 还没有已提交的 src 改动。每批开工前重新核对 |
| 方案底稿 | 只读审计四片（外壳与侧栏、对话区、业务组件、组件库与 globals.css）+ 汇总 + 漏项审查 18 条，已全部并入本文 |
| 范围 | `frontend/assistant/src`。登录页（Keycloak 主题，用户 10-17 定不动）、`apps/pearnly_core/pearnly_core/www/assistant.html` 启动页、CC 桌面版的 Qt 原生部分不在范围内 |

## 进度

| 批 | 状态 |
| :- | :- |
| B0 闸门与比对工具骨架 | 10-18 变基到 main（669427ca，之后只多了交接文档的 249f5928），本地全闸门 87 步全过，推分支等 Development checks 通过后快进合入 main；不碰 `src`，无画面变化，不部署 |
| B1 标准层 | `pearnly/cc-std-b1`（基线 c4ab0c3e，工作树 `.claude/worktrees/cc-std-b1`），提交见下面「B1 做了什么」；纯替换 4 个提交逐个比对 0 样式差、0 像素差，画面变化 V2 / V3 / V4 / V6 / V37 逐个比对只差预期的项；本地全闸门 87 步全过。比对报告 `~/pearnly-devtools/cc-std-b1/reports/`，画面变化前后图 `~/pearnly-devtools/cc-std-b1/visual/`。**未推送、未合入 main、未部署；待测试窗口验收**（V3、V4、V37 的触屏与中间宽度要用户手机 / 平板走） |
| 插批：静置动效 | `pearnly/cc-idle-anim`（基线 cc-std-b1 顶端 f1978271）：首页小猫的 7 个装饰动画不再无限循环，出现时播几轮（`--cc-mascot-*-runs`，最多 10 秒）就停；闸门加「动画轮数只写 var()」；见 [静置动效记录](2026-10-18-cc-idle-animations.md)。未推送、未合入 main、待测试机复测 |
| B2 组件库 | `pearnly/cc-std-b2`（基线 `pearnly/cc-longpress-race` 顶端 93a10475，开发窗口指定；工作树 `.claude/worktrees/cc-std-b2`），提交见下面「B2 做了什么」；纯替换 8bc08765 对基线全矩阵 773 个场景 0 样式差、0 像素差（报告里 1 个悬停残留经前后各复拍 2 次判为基线那一遍离群），画面变化 V5 / V7 / V9 / V11 / V18 / V38 逐个比对只差预期的项（V38 是 B2 按 4.1 补进第 7 节的）；本地全闸门全过。比对报告 `~/pearnly-devtools/cc-std-b2/reports/`，前后图 `~/pearnly-devtools/cc-std-b2/visual/`。10-18 晚变基到 origin/main 2a558d14 → 84c644f6，本地全闸门 100 步过、分支 CI 两条过；lab 挂 84c644f6 由测试窗口验收**六项全过、回归无变化**（见「B2 测试窗口验收」）；用户 10-18 晚手机真手指走过预览与长按、通过，合入 main；未部署（随下一批） |
| B3 外壳 | `pearnly/cc-std-b3`（基线 `origin/main` 81bcd88a，工作树 `.claude/worktrees/cc-std-b3`），提交见下面「B3 做了什么」；纯替换 1a16d652 对基线全矩阵 0 样式差、0 像素差（1 个悬停残留经前后各复拍 2 次判为纯替换那一遍离群；新补的 `install-guide` 场景单独补拍 0 差），画面变化 V1 / V8 / V16 / V36（0 差）/ V39 逐个比对只差预期的项（V39 是 B3 按 4.1 补进第 7 节的）；本地全闸门 100 步全过。比对报告 `~/pearnly-devtools/cc-std-b3/reports/`，前后图 `~/pearnly-devtools/cc-std-b3/visual/`。**已推任务分支、未合入 main、未部署；待测试窗口验收**（V8 手机菜单、V39 要用户 iPhone 走） |
| B5 会话栏、结果、文档与工具卡 | `pearnly/cc-std-b5`（基线 origin/main 81bcd88a，工作树 `.claude/worktrees/cc-std-b5`），提交见下面「B5 做了什么」；纯替换 14045108 对基线全矩阵 773 个场景 0 样式差、0 像素差，画面变化 V1 / V12 / V13 / V23 / V24 / V26 / V27 逐个比对只差预期的项（主场景没渲染到的加载、出错、步骤明细由本地加场景补比）；V25 不变。比对报告 `~/pearnly-devtools/cc-std-b5/reports/`，前后图 `~/pearnly-devtools/cc-std-b5/visual/`。本地全闸门全过、分支推送等 CI；**未合入 main、未部署；待测试窗口验收**（V23 / V26 / V12 的手机项、V27 的分隔线要用户手机走） |
| B6 侧栏列表 | `pearnly/cc-std-b6`（基线 `origin/main` 81bcd88a，工作树 `.claude/worktrees/cc-std-b6`），提交见下面「B6 做了什么」；纯替换 e5efbdcb 对基线全矩阵 825 个场景 0 样式差、0 像素差，画面变化 V1 / V8 / V28 / V29 逐项比对只差预期的项（V28 补进「加载更多」转圈）；本地全闸门全过。比对报告 `~/pearnly-devtools/cc-std-b6/reports/`，前后图 `~/pearnly-devtools/cc-std-b6/visual/`。已推任务分支，**未合入 main、未部署；待测试窗口验收**（触屏上行的悬停底、拖动与长按回归要用户手机走） |
| B7 对话区 | `pearnly/cc-std-b7`（基线 origin/main 81bcd88a，工作树 `.claude/worktrees/cc-std-b7`），提交见下面「B7 做了什么」；纯替换 d9cb99bd 对基线全矩阵 801 个场景 0 样式差、0 像素差（只有类名标签与删掉的隐藏空容器，9 个渐变带抖动经复拍判定），画面变化 V1 / V3 / V12 / V30–V36 与补进第 7 节的 V70 / V71 / V72 逐个比对只差预期的项（V13 在 B7 无可见变化）。比对报告 `~/pearnly-devtools/cc-std-b7/reports/`，前后图 `~/pearnly-devtools/cc-std-b7/visual/`。**推了任务分支、未合入 main、未部署；待测试窗口验收**（V30、V36 要用户手机 / 平板走） |
| B4 弹窗与列表页 | `pearnly/cc-std-b4`（基线 origin/main 81bcd88a，工作树 `.claude/worktrees/cc-std-b4`），提交见下面「B4 做了什么」；纯替换两个提交合比基线全矩阵 955 张 **0 像素差**（样式差只有类名标签、转圈关键帧名等「值不同、画面相同」的五种，逐条见下），画面变化 V1 / V5 / V6 / V10 / V12 / V14 / V15 / V17 / V19 / V20 / V21 / V22 / V36 逐个比对只差预期的项（V3、V8 在 B4 无可见变化）；本地全闸门全过，分支已推、Development checks 通过。比对报告 `~/pearnly-devtools/cc-std-b4/reports/`，前后图 `~/pearnly-devtools/cc-std-b4/visual/`。**未合入 main、未部署；待测试窗口验收**（设置的手机点按区、平板竖屏的表单一列、手机卡片按钮转圈要用户手机 / 平板走） |
| B3–B7 合入 | `pearnly/cc-std-b3b7`（基线 origin/main 81bcd88a）：B3、B5、B6、B7、B4 依次 `--no-ff` 合入，见下面「B3–B7 合入」；测试窗口 10-19 早在 CC 桌面版深浅色验收通过，快进合入 main；手机 / 平板 / 触屏与网页端待用户部署前走 |
| B8 收口 | `pearnly/cc-std-b8`（基线 origin/main 4277e95c，工作树 `.claude/worktrees/cc-std-b8`），提交见下面「B8 做了什么」：闸门 R9 带轴变换修正、删 Button 与两处再导出、删 tw-animate-css / tw-shimmer / class-variance-authority 与 globals.css 里没人用的旧名、删收紧清单改全仓失败、改 NOTICE 删 components.json。基线对终点全矩阵 1001 个场景 **0 样式差、0 像素差**（10 个光栅抖动前后各单独复拍 2 次判定）；本地全闸门 105 步全过，分支 Development checks 通过。比对报告 `~/pearnly-devtools/cc-std-b8/reports/`。**无画面变化；未合入 main、未部署** |

每批完成后在这里补一行：分支、提交、合入 main 的 SHA、比对报告位置、测试窗口验收状态。

### B0 做了什么（10-17）

- **闸门**：`scripts/check-frontend-tokens.py` 原有 `scan()` 与 R1–R3 判据一字未改；新加 `scan_strict()` 判 R4–R15（3.1 表），
  允许的名字运行时从 globals.css 读（`@theme` 的 `--text-* / --ease-* / --animate-* / --font-weight-* / --font-* / --color-*` 与所有 `--cc-*` 定义）。
  组件源码先取出字符串与模板字符串（模板里 `${…}` 占位、运行时拼出来的片段不判；JSX 文字里的撇号不吞代码；正则字面量跳过），
  再按空白切类名、按顶层 `:` 拆变体、识别前置 / 后置 `!` 与负号；globals.css 按语句解析，跳过 `@keyframes` 与 `@font-face`，`@apply` 按组件规则判，
  `--cc-*` 写数要同行注释里有出处（实测 / 外壳实测 / 系统值 / 现有值·CC 自定 / 运行时写入 / 照搬宣传页）。另加两处 1.2 表里写了但 R 编号没点名的：
  不受 R4–R9 管的工具类（`grid-cols-[…]`、`aspect-[…]`）的任意值也按 R10 判；组件里整串是长度的字符串（`"3rem"`、`"200px"`）按 R12 判。
- **收紧范围**：`scripts/frontend-tokens-strict.txt`（已完成清单，初始为空，文件头写明每批加自己的文件、按字母序只增不减、B8 删文件改全仓失败）。
  默认模式：R1–R3 有违例或范围内有 R4–R15 违例才失败，范围外只打一行报数；`--report` 列各条命中数、示例和按文件 × 规则；`--strict-list` 供测试指定清单。
- **测试**：新文件 `tests/test_cc_frontend_gate.py` 20 项，R4–R15 每条都有拦和放的用例（含 3.3 点名的全部例子、多行 `cn()`、模板字符串、
  `[&_svg]:size-4`、运行时变量与模板拼接的 R14、globals.css 的 R13）、范围文件格式、只在范围内失败、旧 `scan()` 不受新判据影响、对真实 src 范围内 0 违例。
  `tests/test_cc_ccs.py` 不改，23 项照常通过。CI（`development.yml`）在闸门脚本后加跑新测试文件。
- **Tailwind 4.3.3 解析核对**（用 `node_modules/tailwindcss` 的 `compile()` 现编）：`-mt-(--x)` → `margin-top: calc(var(--x) * -1)`；
  `z-(--x)`、`duration-(--x)`（同时写 `--tw-duration`）、`text-(length:--x)` → `font-size: var(--x)` 都按预期；`in-data-[cc-touch]:p-0` →
  `:where([data-cc-touch]) .in-data-\[cc-touch\]\:p-0`（祖先匹配，`<html>` 上的属性可用）；`!px-0` 与 `p-0!` 都生成 `!important`。
  断点：不写时 `max-md / md` 生成 `(width < 48rem)` / `(width >= 48rem)`；`@theme` 写 `--breakpoint-md: 768px` 后生成 `(width < 768px)` / `(width >= 768px)`，
  与 2.1 定的那一段文本一致（B1 照此写、测试核对三处一致）。
- **范围外基线报数**（803c483b 的 src，`python3 scripts/check-frontend-tokens.py --report`）：共 1324 处 / 40 个文件——
  R4 尺寸 180、R5 间距 410、R6 字号行高 4、R7 层级 22、R8 动效 103、R9 其他数值类 27、R10 任意值与 `!` 58、R11 断点与输入设备 13、
  R12 JSX 与内联 25（`sideOffset` 等 17、svg 宽高描边 3、内联 style 2、长度字符串 3）、R13 globals.css 自身 423、R14 变量存在性 21、R15 字重 38。
  文件前几名：globals.css 431、thread.tsx 100、ui/sidebar.tsx 93、settings.tsx 84、scheduled-tasks.tsx 80、assistant.tsx 61。
  R14 的 21 处全是运行时变量与写入方自设的变量还没在 globals.css 登记（`--cc-menu-max` 6、`--cc-vvtop` 4、`--cc-marquee-x / -dur` 6、`--cc-modal-share` 3、`--cc-vvh` 2），
  即 3.2 预计的 B0 误报，B1 登记默认值后消失，不为它改规则；R15 的 38 处是 `font-medium / semibold / bold / normal`，B1 在 `@theme` 接上 `--font-weight-*` 后放行。
  逐条看过各规则的命中种类，没有发现误拦（唯一一处 `addEventListener("blur")` 被当成 `blur` 类，已改为单独一个词的字符串不算）。
- **画面比对工具** `scripts/cc-visual/`：`server.mjs`（假服务端，固定时刻，十个会话：待允许、富文本 Markdown 表格 / 代码块 / 提醒卡 / 结果文件、运行中、出错、长对话；
  `__boot=page` 照渲染 `www/assistant.html` 走动态插入资源，`__lang` 定 `<html lang>`，`/__hold` 挂起接口出加载骨架）、
  `scenes.mjs`（5.2 的 7 种环境 × 浅深色，场景 62 个、全矩阵每遍 681 张：外壳与对话、侧栏、手机、面板、页面、弹窗、加载、根节点状态、泰文浅色、生产页加载方式；另有 767.5 宽环境）、
  `shoot.mjs`（本机 Chrome 无界面 + CDP；每条跑道自己一个假服务端；时区、Date、Math.random 固定，光标每个场景先挪出画面；每个场景清存储、预置偏好、做交互、停时钟，
  采计算样式快照——每个元素与伪元素的外框到 0.01px 和 68 个受管属性，含 transition / animation 的时长与曲线、遮罩、合成提示——再截图）、
  `compare.mjs`（快照逐元素逐属性比、截图字节相同即 0 差否则在 Chrome 里用 canvas `getImageData` 逐像素比并出红色叠图，默认 0 容差；`--git-range` 做覆盖检查；出 report.md / report.json，有差异退出码 1）。
  与 5.2 原写法的一处不同：5.2 写「截图前注入样式把 animation / transition 设为 0」。实测只在截图前归零不够——过渡和入场动画跑没跑、元素截图时还在不在合成层上，
  取决于机器忙不忙，并行跑时会面板里转过角度的小箭头抗锯齿差 1（同一构建两遍 142 个场景有差）。现做法：过渡与动画在页面里**从加载起一直归零**，
  只在采快照那一刻放开、把动画停住（有限的结束、无限的停在 0），记下真实的时长与曲线后再归零截图；减少动效场景不归零，记下截图前仍在跑的动画（转圈要照常转）。
  另两处同类修正：输入框自动长高用的隐藏量尺（`visibility:hidden`，宽度跟最后量过的输入框走）只记样式不记外框；加载骨架场景挂起的接口按跑道分开（原来一个假服务端多条跑道共用，互相清掉）；
  子菜单改为点开（悬停打开有延时，触屏模拟下快照与截图之间才弹出）；快照 → 截图 → 再快照，两次快照不一样就整组重来（最多 3 次），一直不稳的写进报告「动作没做完」一栏（触屏点按后浏览器补发的鼠标事件会在快照之后才让某一行显示悬停）。
  不新增依赖，产物用 `vite build --outDir <临时目录>` 构建，不写进仓库。
- **自检**（产物 = 本分支 803c483b 的 src 用 `vite build --outDir` 构建到临时目录；本机 Chrome 无界面，7 条跑道并行，每遍约 11.5 分钟）：
  - 同一构建连跑两遍：681 个场景（14 组环境 × 深浅），**0 个有差异**——样式差异 0 条、差异像素 0（像素容差 0）。
    第一版工具同样的比法有 142 个场景差（过渡 / 动画时序、悬停残留、共用假服务端的挂起接口、隐藏量尺），按上面几处修正后归零。
  - 故意改 1px：复制一份产物，只把 CSS 里 `.cc-composer` 的 `padding-top` 从 `.875rem` 改成 `calc(.875rem + 1px)`，与第一遍比：
    12 个场景报出差异（这条规则生效的窄屏三环境 × 深浅 × 「输入了文字」「键盘弹出」两个场景），每个都是样式 2 条
    （`div.cc-composer` 的 `padding-top 14px → 15px` 与随之变的外框）加差异像素 878–7537 个；其余 669 个场景 0 差，没有误报。
  - 动作没做完（两遍一致，覆盖缺口，B1 前补选择器或人工点）：桌面版 `session-panel` 顶栏没有 `.cc-topbar .cc-row-btn`（4 组）、
    窄屏 `chin-month` 只有一个输入框下按钮（6 组）、触屏宽屏 `delete-confirm` 行尾「···」不显示找不到删除项（4 组）。
    假服务端没模拟的接口：`pearnly_core.assistant.api.service_worker`、`pearnly_cowork.companies.api.candidates`（返回空，不影响渲染）。
  - 减少动效场景：截图前在跑的动画记为 4 个 `cc-spin-dot`（四点转圈照常转）。
- **没做 / 留给后面**：3.3 的「断点三处文本一致」测试要等 B1 改 `MOBILE_QUERY` 与 `@theme` 后才能成立，B1 加；覆盖检查（`--git-range`）已实现，B0 没有 src 改动可测；
  现有 `TokenGateTests` 留在 `test_cc_ccs.py` 没有搬（B0 不改它）。

### B0 补修（10-17 晚，审查确认项）

**闸门**（`scripts/check-frontend-tokens.py`，原 `scan()` 与 R1–R3 仍一字未改；用例补进 `tests/test_cc_frontend_gate.py`，20 项 → 26 项）：

1. 变量写法只认 `--cc-*`：`-(--x)` / `-(length:--x)`、方括号里的 `var(--x)`、内联 style 里的 `var(--x)` 引用别的名字，按所在族记规则（1.2 补充第一条）；名字不对又写了数的另记 R10。
2. 收紧清单核对：条目对不上任何受检文件（`./`、`//`、`..`、大小写不同、扩展名不受管、文件不存在）闸门直接失败；测试另核对规范写法与扩展名。
3. 简写里的长度：globals.css 的 `transform` 平移参数（按间距）、`flex` 的 basis（按尺寸）、`font` 简写（字重 / 字号 / 行高）；内联 style 的 `transform` 同一判法（`scale()`、`rotate()` 与 `${x}px` 运行时插值放行，`-50%` 放行）。顺带补上内联 style 里的负长度（`marginTop: "-4px"` 原来漏过，变量名先剥掉再判）。
4. 任意值里的缓动：`animate-[…_ease-out]`、`[transition:…_linear]`、`[animation-timing-function:ease-in]` 记 R8（任意属性的时间与缓动同 R13 判法；关键帧名里带 linear / ease 不算）。
5. 容器查询变体 `@md:`、`@max-md:`、`@[480px]:`、`@lg/main:` 记 R11（工具类 `@container` 不算）。
6. 整串长度字符串：值为 0 的放行；`rootMargin` 这类非样式 JS 长度照拦，合规写法写进 2.6，口径写进 1.2 补充第二条。
7. 同一处只记一次：style 块里的值归 STYLE_PAIR、`.style.x =` / `setProperty` / svg 宽高归各自模式，它们的字面字符串不再另记「整串是长度」；style 块里的 `zIndex` 只记一条 R7，块外的 `const s = { zIndex: 50 }` 照记。

**新报数**（同一份 src，`--report`）：共 **1337** 处 / 40 个文件（原 1324，+13）——R4 尺寸 190（+10：`ui/sidebar.tsx` 的 `w-(--sidebar-width)` 等 9 处、`thread.tsx:137` 的 `--thread-max-width`）、
R5 间距 412（+2：`sidebar.tsx:232–233` 的 `start / end-[calc(var(--sidebar-width)*-1)]`）、R13 globals.css 424（+1：1229 行 `transform: translateX(1rem)`），其余各条不变
（R6 4、R7 22、R8 103、R9 27、R10 58、R11 13、R12 25、R14 21、R15 38）。去重这一条对真实 src 没有减数（原来没有重复命中的写法）。范围内 0；默认模式 PASS。

**比对工具**（`scripts/cc-visual/`，7 条审查发现逐条先复现再改，都成立）：

1. 「后」缺组 / 缺场景仍判 0 差——成立（按原代码，「只在前跑过」只写备注）。改：只在一边跑过的场景算差异；两份 `run.json` 要都在（缺 = 那一遍没跑完），环境、深浅色、场景筛选和每组场景清单要一致，否则退出码 1。实测用 4 组 × 8 场景对 2 组 × 1 场景：报出 6 条不可比、29 个只在一边跑过。
2. 767.5 宽没生效——成立（实测 CDP 回 `int32 value expected`，原 `send()` 不看报错）。改：`send()` 遇到 CDP 报错或 60 秒超时就失败；每个场景读回宽度（`matchMedia` 精确到小数）、高、像素比、`pointer: coarse`、UA，对不上整组失败。
   小数宽度改用「外层 1535 宽、像素比 1 的页面 + `zoom: 2`、宽 767.5px 的同源 iframe」，实测 iframe 里 `(width: 767.5px)` 成立、像素比 2（直接放 767.5px 的 iframe 会被取整成 768，不行）。
   **跑出来的结果**：767.5 宽时 `(width < 768px)` 成立而 `MOBILE_QUERY`（`max-width: 767px`）不成立，变体判成 web、侧栏按 `max-md` 收起、却没有手机顶栏，设置等入口点不到（场景 settings 记「动作没做完」）——就是风险 3，B1 统一查询文本时修，B0 不改 src。
3. 泰文欢迎页偶发字形光栅差——成立（同一构建两遍之间偶有只差像素的场景）。不调容差，改成：样式 0 差、只差像素的场景，按两份 `run.json` 里的产物目录前、后各复拍一次，复拍后仍差才算差异，一致的单列「光栅抖动」。
   本次两次同构建比对各有 6、7 个这类场景，都在窄屏长对话（`sesn_rich`）输入框那一条带（输入框盖在滚动内容上方），复拍后全部一致；th-welcome 这次没出现。
4. 触屏宽屏的行菜单没打开却没记下——成立（原 `find()` 只看外框，透明且不接指针的「···」也照点，点到的是行）。改：点按 / 悬停目标要看得见、接指针、中心落点就是它，否则记「点不到」；场景加「期望状态」（菜单、弹窗、PDF 页、编辑框……），动作做完看不见就记进「动作没做完」；
   触屏宽屏（web-coarse / desktop-coarse）的 sidebar-row-menu、delete-confirm、boot-page-menu 改走真实入口长按行。顺带发现原 attach-menu 场景的「＋」是直接调起系统选文件、没有菜单，场景名照旧、不设菜单期望。
5. 跑道异常留下孤儿 Chrome——成立（读代码：`lane()` 没有 try/finally，端口写死会接管旧 Chrome）。改：每条跑道 try/finally 关 Chrome 与假服务端；Chrome 用 `--remote-debugging-port=0`、从新清空的 profile 里读 `DevToolsActivePort`；假服务端端口由系统分配；
   一条跑道出错就不开新跑道、等其余收尾后整遍报错且不写 `run.json`；Ctrl+C / kill / 未捕获异常时关掉所有已起的 Chrome；关 Chrome 等主进程退出再删 profile（删目录带重试，复拍时曾 ENOTEMPTY）。比对也不再写死 19890 端口。
   实测：环境名写错的那条跑道失败、退出码 1、不留 Chrome；跑到一半 Ctrl+C，退出码 130，Chrome 0 个。
6. 场景没覆盖 5.2 的几类界面——成立。补 5 个场景：`results-pdf`（假数据加一个输出 PDF；浏览器自带的 PDF 查看器不是我们的样式且无界面下出图不稳，这个场景关掉它走页图）、`tasks-confirm`（假数据加一条定时任务，行菜单 → 删除 → 确认框）、
   `sidebar-search-empty`（宽屏侧栏 / 窄屏抽屉里搜一个查不到的词）、`notes-edit`（公司页知识库卡「+」出编辑框，窄屏先开公司栏）、`image-preview`（往输入框粘贴一张 canvas 画的 640×400 图，点附件出预览）。场景 62 → 67 个。
7. 覆盖检查把删掉的类报成没覆盖——成立。改：diff 里 `-` 行的类按「前」的快照核对、`+` 行按「后」，报告注明是删 / 加 / 改、哪一遍没渲染到。

**自检**（产物 = 本分支 src 用 `vite build --outDir` 构建到临时目录；全矩阵 = 7 种环境 × 深浅 + 767.5 宽浅深 = 16 组、757 张，7 条跑道并行，每遍约 12.5–14.5 分钟）：

- 同一构建连跑：第一遍 A 与第二遍 B（同一版工具）、A 与修完收尾后的第四遍 D（之间只改了关 Chrome 等进程退出、删目录重试、attach-menu 去掉期望，均不影响画面）都是 **0 个有差异**——样式差异 0 条、像素差 0、不可比 0；其中只差像素的 6 个 / 7 个场景经复拍判为光栅抖动（见上第 3 条）。不复拍时这几个场景会报差。
- 故意改 1px（复制产物，只把 `.cc-composer` 的 `padding-top` 改成 `calc(.875rem + 1px)`）：报出 **18 个场景**有差异——窄屏三环境 × 深浅 ×「输入了文字」「键盘弹出」「图片预览」（新场景，输入框里有附件）；每个都有 `div.cc-composer` 的 `padding-top 14px → 15px`，没有误报；另外 7 个只差像素的场景复拍后判为抖动。
- 动作没做完（覆盖缺口，与 B0 时一致，B1 前补选择器或人工点）：桌面版 `session-panel` 顶栏没有 `.cc-topbar .cc-row-btn`（4 组）、窄屏 `chin-month` 只有一个输入框下按钮（6 组）、767.5 宽 `settings`（2 组，见上第 2 条）。原来漏记的触屏宽屏行菜单 / 删除确认现在能打开。
- 假服务端没模拟的接口仍是 `service_worker`、`companies.api.candidates`（返回空，不影响渲染）。定时任务页的 Cowork 词条（`cowork.folder.*`）假服务端没给，显示词条键名，前后一致，不影响比对。

**补修复核**（10-18，另一名复核者按审查的 14 条逐条复跑、读代码，结论记在这里；代码没再改）：14 条全部成立已修（闸门 7 条、比对工具 7 条）。
复跑包括：把「后」那遍删掉一组 / 删掉 `run.json` / 改环境清单，三种都报不可比、退出码 1；web-767.5 读回 `(width: 767.5px)` 成立、像素比 2，settings 场景照预期记「动作没做完」；
新旧脚本对同一份 src 的命中逐条比对，`--report` 与上文报数一致（1337 处，R4 190、R5 412、R13 424）。两条不阻塞的建议留到 B1 前处理：
① 任意属性写法 `[width:var(--sidebar-width)]`、`[transition-duration:var(--foo)]`、`[--cc-x:var(--foo)]` 引用外来变量仍放行（src 现在没有这种写法），建议在 `check_class` 任意属性分支对不以 `--cc-` 开头的 `var(--…)` 记 R10，补 `[max-width:var(--thread-max-width)]`、`[--cc-x:var(--foo)]` 两条拦截用例；
② `shoot()` 开头校验环境名都在 ENVS / EXTRA_ENVS 里，SIGINT 处理时顺带删掉 `out/.profiles`。
另：第四遍 D 用的是收尾时又改过三处（关 Chrome 等进程退出、删目录重试、attach-menu 去掉菜单期望）的工具，修完后没有再从头连跑两遍新的全矩阵，以 A 对 D 的 0 差为准。

### B1 开工前（10-18，`pearnly/cc-std-b1` 第一个提交，不碰 `src`）

**补修复核留的两条加固**（都做了）：

1. 闸门：任意属性写法里 `var()` 引用的不是 `--cc-*`（`[max-width:var(--thread-max-width)]`、`[width:var(--sidebar-width)]`、`[transition-duration:var(--foo)]`、`[--cc-x:var(--foo)]`）记 R10；用例补进 `tests/test_cc_frontend_gate.py`。
2. 比对工具：`shoot()` 开头核对环境名、深浅色都在 `ENVS / EXTRA_ENVS / SCHEMES` 里，写错直接报错退出、不清输出目录；Ctrl+C / kill 时除了关 Chrome，还删掉还在用的 profile 目录（`<out>/.profiles`、比对的 `<report>/.profile`）。
   实测：`--env nope` 报错、退出码 1；全矩阵跑到第 8 秒 Ctrl+C，退出码 130，`.profiles` 已删、Chrome 0 个。

**闸门几种理论漏拦写法**（逐个用 Tailwind 4.3.3 的 `compile()` 现编看会不会生成样式，再定拦还是登记）：

| 写法 | 现编结果 | 处理 |
| :- | :- | :- |
| `[z-index:50]`（及 `[line-height:2]`、`[font-weight:600]`） | 生成 `z-index: 50` | 拦：任意属性写不带单位的数，同 `z-[…]` / `leading-[…]` 方括号里必须有变量，按族记 R7 / R6 / R15；`[z-index:calc(var(--cc-z-menu)+1)]`、`[z-index:0]` 放行 |
| `[width:var(--foo)]`、`[transition-duration:var(--foo)]`、`[--cc-x:var(--foo)]` | 照常生成 | 拦，记 R10（上面第 1 条）；`[--cc-x:…]` 里的 `--cc-x` 没登记另记 R14（原有） |
| `group-@md:flex` | 不生成（不是合法变体） | 照样拦（R11），顺带查出真正会生成的组合：`not-sm:` → `@media not (width >= 40rem)`、`not-pointer-coarse:` → `@media not (pointer: coarse)`、`not-@md:` → `@container not (…)`。闸门先剥掉 `not- / group- / peer- / in- / has-` 再判断点、输入设备、容器查询，都记 R11；`not-max-md:`、`not-md:` 等于那一条断点，放行 |
| 常量拼长度 `` `${4}px` `` | — | 拦：模板里 `${…}` 只有数字字面量（`${4}`、`${2 * 100}`）再接单位记 R12（style 块、`.style.x =`、`setProperty` 里的照旧由各自模式记一次）。拼的是常量名（`` `${GAP}px` ``、`` `${n * 8}px` `` 写在 style 以外）机械上判不出，**登记为已知限制**，审查时按 2.6 看（常量也要来自 globals.css 的变量或测试核对过的回退值） |

对真实 src：这几条没有新增命中，`--report` 仍是 1340 处（main 上比 803c483b 多 3 处，都在 R13）。

**比对工具「动作没做完」三处**：

- 桌面版 `session-panel`：桌面版对话名旁的「···」在自画标题栏 `.cc-titlebar` 里，标题栏第一颗 `.cc-row-btn` 是侧栏收起钮；选择器改成 `:is(.cc-topbar, .cc-titlebar) .cc-row-btn[aria-haspopup="menu"]`，并设期望（菜单，或窄屏盖满屏的会话栏）。
- 窄屏 `chin-month`：手机没有输入框下巴（用户 10-17：基础信息放上面），已有对话的月份在右上「…」的「处理月份」里，窄屏子菜单在原菜单里换页（`data-slot="cc-menu-sub"`）；窄屏改走这条，期望菜单。
- 767.5 宽 `settings`：不是工具的问题，是风险 3 那条缺陷（767.5 宽时变体判成 web、没有手机顶栏）。B1 把 `MOBILE_QUERY` 改成 `(width < 768px)` 后打得开，作为画面变化 V37 单独提交（见第 7 节）；基点那一遍照旧记「动作没做完」。
- 试跑：desktop / desktop-coarse / desktop-narrow / phone / tablet-portrait 浅色这两个场景都做完、期望状态都在。

### B1 做了什么（10-18，`pearnly/cc-std-b1`）

基线 `origin/main` c4ab0c3e；分支自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`，真实目录）。动工前核对：`cc-drag-perf`、`cc-scroll-fix`、`cc-ef-fixes` 都已随 cc-shell-rebuild 进了 main；
正在测试机验收的 `pearnly/cc-drag-engine` 不改 globals.css，改 `drag.tsx`、`test_assistant_ui.py`、`test_cc_session_panel.py`——B1 没碰 `drag.tsx`，两份测试只改了断言 globals.css 写法的那几行。

| 提交 | 内容 | 类 |
| :- | :- | :- |
| 3bc9d7f8 | B1 开工前：闸门与比对工具加固（上一节） | 工具 |
| 95dbf34c | `lib/variant.ts` 在首帧前写 `<html data-cc-touch>`（手机变体，或粗指针且不是桌面窗口），监听主指针变化 | DOM 标记（比对只差根节点属性） |
| 9f8d7ffa | 标准层：刻度层一块、用途层、运行时层默认值；`@theme` 接实测字阶 9 档、字重 4 档、`--breakpoint-md: 768px`；`cn()` 登记字号、字重名 | 纯替换 |
| 4acd80ea | globals.css 内部替换：共用类里写死的间距、尺寸、字号、字重、层级、时长全换成同值变量并补出处；运行时变量去掉回退；`@media` 统一写法；13 对手机 / 粗指针成对规则合成 `:root[data-cc-touch]`；`.cc-results` 手机全屏；删无人用的同义变量、tw-shimmer、collapsible 残留；`--cc-bg-active` 并入 `--cc-bg-selected`、`--cc-titlebar-h` 改名 `--cc-win-titlebar-h`；H27 选择器 | 纯替换 |
| 649bf5aa | 新共用类（2.3 全部）、`.cc-switch` 40×24、`.cc-btn / .cc-new-dot / .cc-send[data-stop]` 的图标规则、减少动效选择器；「每个 `--cc-*` 都有人用或等某批接上」的测试；收紧清单登记 4 个文件 | 纯替换（新类暂无人用） |
| ee7e697d | V2 默认过渡 | 画面变化 B |
| 3e019925 | V3 触屏点按区不跟桌面窗口、月份分店胶囊补点按区 | 画面变化 A |
| d70ba4a9 | V4 图标按钮成对规则以完整的一边合并 | 画面变化 A（预期无可见差异） |
| 0e7b5bcc | V6 不可用透明度统一 0.4 | 画面变化 B |
| c0b1c7e8 | V37 `MOBILE_QUERY` 改 `(width < 768px)`，断点三处一致测试 | 画面变化 A（缺陷修复，新增） |
| 91e59229 | 比对工具：只差像素的场景前、后最多各复拍 3 轮；`--expect-root` 单列预期的根节点属性 | 工具 |
| （本记录） | ccs.md「尺寸、间距、字号、层级、动效」一节；本记录 | 文档 |

**与方案不同、按依据定的几处**（都在 globals.css 注释里写明）：

1. `--text-4xl` 这一档 **B1 不删**：现在删，Tailwind 会退回自带的 4xl（36/40），`assistant.tsx:129` 的 `md:text-4xl` 让宽屏问候行高 36 → 40，不是纯替换。B3 删掉 `md:text-4xl` 后由 B8 删这一档（属于 B8「删已无人引用的旧名」）。
2. 不登记 `--cc-skeleton-dur`：V5 把骨架改成 `--cc-pulse`，2s 不会再有人用；改为在 `@theme` 接 `--animate-cc-pulse`（B2 的 Skeleton 写 `animate-cc-pulse`）。
3. 2.2 的用途变量全部登记；新共用类里在刻度上的值直接用刻度（`--cc-space-8` 等），不另起同义的用途名。还没有组件接上的用途变量列在 `tests/test_cc_ccs.py` 的 `AWAITING` 表里，写明由哪一批接上：表里的变量必须已定义、且确实还没人引用，一接上测试就要求从表里删；B8 时表必须为空。这是待办表，不是豁免——表外的 `--cc-*` 没人引用就失败。
4. `.cc-dialog` 挪到 `.cc-card` 前面再补外观：添加公司的弹窗现在同时带 `cc-card cc-dialog`，按原位置补圆角 16 会让它在 B1 就变（V14 的一部分提前）；挪到前面后它照旧按 `cc-card` 的 8，B4 去掉 `cc-card` 后才是 16。
5. 状态点外层叫 `.cc-status-line`（`.cc-status` 已是连接提示条在用的名字）。`.cc-tabs` 这个名字原来是公司页旧页签、已删；B1 起是结果面板的页签（V24），`test_cc_session_panel.py` 那条「旧页签已删」的断言改成核对公司页不用它。
6. 新增 `--cc-z-drag-shield` 54（拖动挡板，原来写死 54）、`--cc-vw`（整屏宽，系统值）、`--cc-menu-max-none`（菜单默认限高，原来写在 `var()` 回退里的 100vh）。
7. `.cc-new-dot svg`、`.cc-send[data-stop] svg` 放在分层外，选择器盖得过抽屉导航图标 20、电脑输入框按钮图标 16 那两条分层外的规则——B6 / B7 删掉组件上的 `!size-4 / !size-3.5` 后照旧是 16 / 14。
8. 触屏合并：13 对规则在纯替换提交里合成；`.cc-tap`、`.cc-card-btn`、`.cc-choice-*` 三处原来只按粗指针、没排除桌面窗口，合到触屏标记就是 V3（画面变化）；图标按钮那一对两边不一样（粗指针那份排除了电脑顶栏、桌面标题栏），是 V4。
9. V20 在 B1 的部分只是登记 `--cc-card-btn-icon-phone`（22，外壳实测）并让 `.cc-card-btn svg` 用它，画面不变；变化在 B4 删掉 `pending.tsx` 的 `size-4` 时出现。
10. 比对工具两处补强（`scripts/cc-visual/compare.mjs`）：只差像素的场景前、后各复拍最多 3 轮，前、后出过的图里有一模一样的一对才判光栅抖动；`--expect-root data-cc-touch` 把预期的根节点属性变化单列（触屏标记那个提交用）。

**比对**（`scripts/cc-visual/`，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、757 张，7 条跑道，每遍约 12.6 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b1/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<前>-vs-<后>/report.md`）：

| 比的两遍 | 结果 | 说明 |
| :- | :- | :- |
| 基线 A / 基线 B（同一构建 c4ab0c3e 连跑两遍，新场景表） | 0 差 | 只差像素的 10 个场景复拍判为光栅抖动 |
| 基线 → 95dbf34c（触屏标记） | 样式 0、像素 0 | 406 个场景只多了根节点 `data-cc-touch`（`--expect-root` 单列）：手机、平板竖屏、触屏网页、桌面版拉窄四种环境；桌面版在触屏上没有，符合规则 |
| 95dbf34c → 9f8d7ffa（标准层） | 0 差 | 抖动 18 个 |
| 9f8d7ffa → 4acd80ea（内部替换） | 0 差 | 抖动 13 个 |
| 4acd80ea → 649bf5aa（新共用类） | 0 差 | 抖动 7 个 |
| 基线 → 649bf5aa（纯替换整段） | 0 差（预期根节点标记单列） | 抖动 7 个 |
| V2：649bf5aa → ee7e697d | 样式只有 `transition-duration 0.15s → 0.12s` 与 `transition-timing-function (.4,0,.2,1) → (.32,.72,0,1)` 两种（全量逐场景核对，32862 条） | 静止画面不变；5 个有像素差的场景都是下面那条抖动带，单独多拍几次，改前、改后两份构建都会拍出同样的两种结果 |
| V3：ee7e697d → 3e019925 | 只有 desktop-coarse（CC 桌面版在触屏 Windows 上）一种环境有差异，102 个场景：卡片按钮从 44 全圆胶囊回到电脑的 24 / 13px，`cc-tap` 的点按区与记住的选择回到电脑高度 | 网页深色 boot-page-menu 有一处悬停残留（侧栏箭头透明度），单独复拍前后 0 差；月份分店胶囊的触屏点按区场景没渲染到，合成节点核对：只多 `position: relative` 与 `::after`，外观不变 |
| V4：3e019925 → d70ba4a9 | 0 差 | 合成节点核对：只有手机变体里放进电脑顶栏 / 桌面标题栏的图标按钮变化，手机上不存在这两种栏 |
| V6：d70ba4a9 → 0e7b5bcc | 只有 `opacity 0.35 → 0.4`：不可用的 `.cc-icon-btn`（248 处）与发送钮（186 处），329 个场景 | 网页深色 boot-page-menu 同上那处悬停残留 |
| V37：0e7b5bcc → c0b1c7e8 | 只有 web-767.5 浅深两组、3 个场景有差异：767.5 宽从「网页布局 + 已收起的侧栏、没有手机顶栏」变成手机外壳；设置场景改前「动作没做完」，改后打得开 | 其余 751 个场景 0 差 |

- **光栅抖动**：窄屏长对话（`sesn_rich`）输入框那一条渐变带，机器忙时会光栅成两种之一；对同一个场景多拍几次，改前、改后两份构建都拍出同样的两张（md5 a0409e01 / 6143bb4e，文字放大那一张 206aa734 / 3f2d3937）。比对工具现在前、后最多各复拍 3 轮，出过一模一样的一对才判抖动。
- **场景没渲染到的类**（覆盖检查列出的 `.cc-chip-btn`、`.cc-chip-x`、`.cc-choice-detail / -seg / -error`、`.cc-company-empty`、`.cc-disclosure`、`.cc-field`、`.cc-group-dot`、`.cc-install-guide*`、`.cc-loading`、`.cc-month-set`、`.cc-row-edit`、`.cc-show-more`，以及只是关键帧名的 `cc-bob / cc-pulse / cc-tail …`）：没有改场景表（改了就得把 9 份构建全部重拍），改用**合成节点核对**补上——`~/pearnly-devtools/cc-std-b1/class-check.mjs`：两份构建的 CSS 装进同一个合成页面，globals.css 里的每个类、每个 data-slot 在 55 种上下文里各放普通 / 带齐状态属性两个节点，按 6 种组合（网页、桌面窗口、触屏网页、触屏上的桌面窗口、手机、桌面版拉窄）× 浅深色、开 CDP 触屏模拟，比每个节点与伪元素的计算样式（约 90 项）。同一构建自比 0 差。基线 → 4acd80ea 的差异全部是合成页面里才会有的组合：`.cc-results` 的手机全屏规则（实际页面调用处的 `max-md:` 类给的是同一组值，场景比对 0 差）；`.cc-mshell .cc-greeting` 放在非手机变体里（实际只有手机才有手机外壳）；H27 让触屏弹窗里 `.cc-btn / .cc-icon-btn` 的 `min-height / min-width` 成了 44（它们在触屏上本来就是 44 高 / 宽，外框不变）。4acd80ea → 649bf5aa 去掉新类后，差异只有 `.cc-btn svg`、`.cc-new-dot svg`、`.cc-send[data-stop] svg`、`.cc-section-label[data-flush]` 这几条新规则作用在合成节点上（实际组件里的图标都写了 `size-*`，还没有 `data-flush / data-stop`）。报告 `reports/class-check-*.json`。
- **没有比对的**：悬停、聚焦以外的交互状态，真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）。

**画面变化的前后图**（`~/pearnly-devtools/cc-std-b1/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告）：V2（网页、桌面窗口、手机 × 深浅，静止画面一致，差在过渡时长）、V3（触屏上的桌面窗口 102 个场景）、V4（无可见差异，留了对照）、V6（网页、桌面窗口、手机 × 深浅里有差异的 164 个场景）、V37（767.5 宽 6 个场景）。

**还缺什么**：测试窗口验收（三种变体 × 深浅色看 V2–V6、V37；V3 要触屏 Windows 上的 CC 桌面版，V4 / V37 要用户手机与平板、浏览器缩放到 767–768 之间走一遍）；推分支、等 Development checks、快进合入 main（按「主线集成」，本次没做）；B2 起各批按 `AWAITING` 表接上用途变量。B2–B7 迁移时如发现用途层缺档或语义不合，按 4.1 登记例外补到 globals.css。

**闸门报数**（`--report`）：范围外 1340 → 854 处（R13 427 → 0、R14 21 → 0、R15 38 → 0，其余各条不变：R4 190、R5 412、R6 4、R7 22、R8 103、R9 27、R10 58、R11 13、R12 25）；收紧清单 4 个文件（`globals.css`、`hooks/use-mobile.ts`、`lib/utils.ts`、`lib/variant.ts`）0 违例。


### B2 做了什么（10-18，`pearnly/cc-std-b2`）

基线是开发窗口指定的 `pearnly/cc-longpress-race` 顶端 **93a10475**（main afc615bb 之上依次叠 H 线、第 13 轮拖动、B1 标准层、静置动效、长按竞争修复，测试机 lab 正挂着它），不是 `origin/main`；工作树 `.claude/worktrees/cc-std-b2`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。
动工前核对：第 13 轮改过的 `components/ui/menu.tsx`（弹层 Portal 直接给 container）与 `drag.tsx`、长按修复改过的 `hooks/use-long-press.ts`、`lib/layers.ts`——B2 在 menu.tsx 里只换了离边 / 子菜单偏移的取值，Portal、上下翻面、点菜单外吞 click、窄屏换页原样，对应用例（`test_cc_mobile_overlays.py` 的 MenuEdgeTests、`test_assistant_ui.py` 的点菜单外只关菜单）照过；drag.tsx、use-long-press.ts、layers.ts 没碰。

| 提交 | 内容 | 类 |
| :- | :- | :- |
| 9f5ee25c | 比对工具补两个场景：`tooltip-copy`（悬停代码块「复制」出组件库提示框；原 `tooltip` 场景悬停的是只带 title 的按钮，浏览器自带提示不出图）、`sidebar-collapsed`（收起侧栏：offcanvas 偏移与占位宽 0）。先补场景再拍基线 | 工具 |
| 8bc08765 | 纯替换（见下） | 纯替换 |
| e5c77fab | V5 骨架呼吸同脉冲点；`cn()` 登记 `@theme` 的动画名 | 画面变化 B |
| 0330a501 | V7 提示框层级 `--cc-z-tooltip` 90 | 画面变化 A |
| bf8351cd | V9 图片预览圆角 16 | 画面变化 A |
| 14a83a8e | V11 侧栏收起 / 展开 200ms 主缓动 | 画面变化 B |
| 62d4069a | V18 提示框字号 caption 12/17、进出场 `.cc-pop` | 画面变化 B |
| 43046edc | V38（B2 补进第 7 节）组件库弹窗的遮罩与弹层进出场 `.cc-overlay` / `.cc-pop` | 画面变化 B |
| 88e0ce34 | 收紧清单登记 10 个文件 | 清单 |
| （本记录） | 本记录 B2 小节、4.1 例外、第 7 节 V38；ccs.md 两条 | 文档 |

**纯替换（8bc08765）做了什么**：

- **删没人用的**：`ui/avatar.tsx`、`ui/sheet.tsx`、`ui/input.tsx`、`ui/separator.tsx` 四个文件（全仓无引用；separator 唯一的引用是侧栏没人用的 SidebarSeparator）；`ui/sidebar.tsx` 17 个没人用的导出（菜单、分组、搜索框、分隔线、拖边条、收起钮、随机宽度骨架 SidebarMenuSkeleton …）、从来没渲染过的手机 Sheet 分支（`assistant.tsx` 在手机上挂 MobileShell，不挂 `<Sidebar>`）、图标档 / 浮起 / 内嵌三种外观与 `collapsible="none"`；`ui/dialog.tsx` 没人用的 DialogHeader / Footer / Description；`ui/button.tsx` 没人用的 4 种外观（default、destructive、secondary、link）与 4 档尺寸（xs、lg、icon-xs、icon-lg）——记录写「6 档」，实数 8 档；默认外观改成 ghost（所有调用方都写明了外观）。
- **写死值换成同值变量**：button 的间距、高、内边距、默认图标；dialog 的定位（`start-1/2`、`-translate-x/y-1/2`，计算值与原来的 `50%` / `-50%` 逐字一致）、离屏边 `calc(100%-var(--cc-dialog-gutter)*2)`、层级 `--cc-z-sheet`、间距与关闭钮位置；tooltip 内边距与小箭头（尺寸、角度新登记 `--cc-tooltip-arrow / -turn`，位移写成 `calc(100%/-2-var(--cc-space-2))`，计算值同原来的 `calc(-50% - 2px)`）；sidebar 层级 `--cc-z-float`、时长 `--cc-dur`、头 / 脚左右 `--cc-sidebar-pad`、间距；skeleton 默认条高 `--cc-skeleton-h`（现有两个调用方都写了尺寸，照旧盖掉它）。
- **侧栏宽一个名字**：`--sidebar-width` → `--cc-sidebar-w`，同一个提交把 `split-handle.tsx:75` 的 `cssVar` 改成它（4.1 登记的跨批例外）；本机记住的宽度仍按 `cc.sidebar.width` 存，旧值照常恢复（`split-resize` 场景：拖宽 60、刷新后网页 347 / 桌面版 348，前后一致）。收起时的偏移 `-start-(--cc-sidebar-w)` 跟着记住的宽度走（`sidebar-collapsed` 场景 -288，前后一致）。
- **JS 用到的值用时读（2.6）**：`menu.tsx` 导出 `menuEdge() / menuSubOffset() / menuOffset()`，读 `--cc-menu-edge / -sub-offset / -offset`，回退 8 / 6 / 4；`tooltip.tsx` 的延时读 `--cc-tooltip-delay`（回退 0）。都用 `lib/css-var.ts` 的 `fixedPx / fixedMs`：读到就记住、之后不再读样式（调用方在每次渲染里取也不逼整页重算样式），读到空（样式表还没生效）用回退值、下次再读；回退常量与 globals.css 同值由测试核对。`main.tsx` 与侧栏 Provider 里多包的两层 TooltipProvider 删掉（每个 Tooltip 自带一层，延时由最近的这层决定，外面的从来不起作用）。
- **`lib/css-var.ts`**：三份解析合成一份——`cssMs`（原 hover-scroll）、`cssPx`（原 company-panel，补上 split-handle `px()` 的 vw 换算）、`cssVar`、`rootFontPx`；`hover-scroll.ts`、`company-panel.ts` 改成再导出（B8 删）。`split-handle.tsx:42` 的 `px()` 与 `assistant.tsx:98` 的 `cssPx` 改导入在 B3，`drag.tsx:103` 的 `cssMs` 在 B6。`menu.tsx` 自己的 `lengthPx` 没并：它认不出就当「不封顶」，`--cc-menu-max` 的默认 `100vh` 要当不封顶，并进 `cssPx` 会变成 100px。
- **globals.css（4.1 新登记的例外）**：用途层补 `--cc-tooltip-arrow` 10 / `--cc-tooltip-arrow-turn` 45deg；删 `--cc-on-danger`（唯一的引用是删掉的 destructive 按钮）。

**与方案不同、按依据定的几处**：

1. **button.tsx 不进收紧清单**：在用的输出照记录保持原样，剩下的 `transition-all`、`disabled:opacity-50`、`has-[>svg]:px-2.5`（10，刻度上没有）一改就是 V10 / V29 / V30 提前出现。B4（设置页 → `.cc-btn`）、B6（会话行「···」→ `.cc-row-btn`）、B7（TooltipIconButton 不再包 Button、删 ThreadSuggestions）迁完后 Button 没有调用方，**由 B8 整个删掉**（纯删除），不再单独迁。
2. **dialog 的默认最宽删掉，不改成 `md:`**：唯一的调用方 `attachment.tsx` 写 `sm:max-w-3xl`，`cn()` 本来就把默认的 `sm:max-w-lg` 合并掉了，删掉是纯替换；改成 `md:max-w-*` 反而会在 ≥768 时压过调用方的 `sm:max-w-3xl`，把图片预览缩到 512。所以 **V36 的 dialog 部分没有可见变化**（`sm:text-start / sm:flex-row` 随没人用的头 / 脚删掉）；图片预览自己的 `sm:` 在 attachment.tsx（B7）。
3. **`--cc-preview-w / --cc-preview-share` 改由 B7 接**：用它们的是 attachment.tsx 调用处（B7 的文件），`AWAITING` 表改登记 B7。B2 接上并删掉的有：`--cc-menu-offset`、`--cc-menu-sub-offset`、`--cc-menu-edge`、`--cc-tooltip-delay`、`--cc-z-tooltip`、`--cc-sidebar-pad`、`--cc-skeleton-h`。`--cc-control-nested`（B2–B7）仍没人用。
4. **V38 补进第 7 节**：记录没列 dialog.tsx 的 tw-animate（遮罩、弹层进出场），不改过不了 R8，B8 也删不掉 tw-animate；同 V15 / V18 一类（B）。
5. separator.tsx 记录列在「迁」里，侧栏清掉没人用的导出后它没有引用了，一并删掉。
6. `--cc-sidebar-pad` 接在侧栏头 / 脚的默认左右（调用方现在写的 `px-2` 照样盖掉，B3 可以删）；侧栏主体原来没有默认内边距，不加。
7. `cn()` 登记 `@theme` 的动画名（`ANIMATIONS`，现在只有 `cc-pulse`）：不登记的话调用方写 `animate-none` 关不掉骨架的呼吸；测试核对与 `@theme --animate-*` 一致。

**比对**（`scripts/cc-visual/`，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、773 张，7 条跑道，每遍约 13–15.5 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b2/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<编号>/report.md`）：

| 比的两遍 | 结果 | 说明 |
| :- | :- | :- |
| 基线 93a10475 → 纯替换 8bc08765（`reports/base-vs-pure`） | **0 样式差、0 像素差**（报告列出 1 个，见右） | 列出的是 desktop-dark `boot-page-menu`：行尾「···」与组箭头的悬停态，基线那一遍没拍到悬停（B1 记过的同一种悬停残留）；这个场景前、后各单独复拍 2 次（`reports/recheck-bpm-1 / -2`）都 0 差，复拍的基线与纯替换一致、与基线那一遍不同。光栅抖动 16 个（窄屏长对话输入框那条渐变，复拍出现一模一样的一对）。覆盖检查：改到的 47 个类 / data-slot 里场景没渲染到的 38 个全是删掉的死代码（「前」那一遍同样没渲染到，证实没人用） |
| V5：纯替换 → e5c77fab | 只有 `loading` 场景 14 组：骨架 `animation-name pulse → cc-pulse`、`2s → 1.2s`、`cubic-bezier(.4,0,.6,1) → ease-in-out`、停在首帧时透明度 `1 → 0.35` | 静止截图 0 像素差（截图时动画归零）。另 1 个 web-light `company-panel` 菜单整体差 1px：v5 那一遍离群（同时在跑别的比对），单独前后各复拍 2 次 0 差，下一遍 v7 又回到原位 |
| V7：e5c77fab → 0330a501 | 只有 `tooltip-copy` 8 组（宽屏 4 种环境 × 深浅）：提示框外层、内容、小箭头 `z-index 50 → 90` | 0 像素差（场景里提示框没压着菜单）；`company-panel` 那 1px 是上一遍离群的回位 |
| V9：0330a501 → bf8351cd | 只有 `image-preview` 14 组：四角 `10px → 16px` | 差异像素 2274–13315（只在四个角） |
| V11：bf8351cd → 14a83a8e | 宽屏 8 组的每个场景（464 个）：侧栏本体与占位 `transition-timing-function linear → cubic-bezier(0.32, 0.72, 0, 1)` | 静止画面 0 像素差；时长仍 200ms |
| V18：14a83a8e → 62d4069a | 只有 `tooltip-copy` 8 组：行高 `16.8 → 17`、高 `28.8 → 29`；`animation enter 0.15s ease → cc-pop-in 0.12s 主缓动`；节点多了 `cc-pop` 类 | 差异像素 651（高多 0.2px 的那条边） |
| V38：62d4069a → 43046edc | 只有 `image-preview` 14 组：遮罩 `enter 0.15s ease → cc-fade-in 0.12s 主缓动`；弹层 `enter 0.2s ease → cc-pop-in 0.12s 主缓动`；弹层的 `transition-duration 0.2s → 0s`（原来的 `duration-200` 同时给了过渡时长，弹层本身没有会变的样式，看不出）；节点多了 `cc-overlay` / `cc-pop` 类 | 静止画面 0 像素差 |

- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b2/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告。V5（骨架，14 组；静止画面一样，差在呼吸）、V7 / V18（提示框，网页、桌面版及其触屏版 × 深浅；手机、平板没有悬停提示框）、V9 / V38（图片预览，14 组）、V11（`sidebar-collapsed`，宽屏 8 组；静止画面一样，差在缓动）。只导出编号对应的场景，上面写明的离群复拍没导出。
- **没有比对的**：悬停、聚焦以外的交互状态（提示框与菜单同时开、侧栏收起的过程），真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）。

**闸门报数**（`--report`）：93a10475 的范围外 851 处 → **623 处**（R4 190 → 128、R5 410 → 333、R7 22 → 13、R8 99 → 52、R9 27 → 14、R10 58 → 47、R11 13 → 6、R12 28 → 26，R6 4 不变）；收紧清单 4 → 14 个文件（加 `components/ui/` 的 dialog、marquee、menu、sidebar、skeleton、tooltip，`lib/` 的 company-panel、css-var、hover-scroll，`main.tsx`），范围内 0 违例。

**还缺什么**：测试窗口验收（下面几项）；推分支、等 Development checks、合入 main（本次没做，基线也不在 main 上，合入前要按「主线集成」变基）；后面各批顺带：B3 把 `split-handle.tsx` 的 `px()`、`assistant.tsx` 的 `cssPx` 改从 `lib/css-var.ts` 导入、删侧栏头 / 脚调用处多余的 `px-2`；B3 / B4 / B6 的菜单 `sideOffset` 用 `menuOffset()`（V8）；B6 的 `drag.tsx` 改导入 `cssMs`；B7 在 attachment.tsx 接 `--cc-preview-w / -share` 并把 `sm:` 改 `md:`（V36）、删 TooltipIconButton 外面那层不起作用的 `TooltipProvider delayDuration={0}`；B8 删 button.tsx（见上第 1 条）与两处再导出。

**给测试窗口的验收项**（三种变体 × 深浅色；本次没有同步到测试机）：

1. 提示框（网页、CC 桌面版，有鼠标的设备）：悬停代码块「复制」、回复下「复制」、附件上的按钮——字 12、行高 17，出现略快（120ms、主缓动，原来 150ms 淡入放大加滑入）；提示框不再被菜单盖住（V7、V18）。手机、平板没有悬停提示框，不看。
2. 侧栏（网页、CC 桌面版）：侧栏头按钮或 ⌘B 收起 / 展开，200ms 由匀速变成先快后慢（V11）；拖分隔条调宽、刷新后恢复记住的宽度；调宽后再收起、展开位置对。
3. 图片预览（输入框粘贴一张图、点附件）：圆角 16（V9），遮罩淡入与弹窗出现 120ms（V38）；**手机、平板竖屏也要看**（用户手机走一遍），关闭钮照旧 44。
4. 加载骨架（首次打开侧栏列表、发送中带附件的文件条）：呼吸 1.2s、最暗 35%（V5），减少动效时不动。
5. 回归（应无变化）：菜单离屏边 8、子菜单离父菜单 6（「语言 ›」「了解更多 ›」页面写的 8 照旧）、手机长按菜单（第 13 轮与长按竞争修复的 A14 复测项不受影响，**要用户手机走**）、设置弹窗里的按钮手机点按区 44（H27 仍认 `data-slot="button"`）。

**B2 测试窗口验收**（10-18 晚 21:20–21:45，lab，84c644f6，网页 1280 / 375 宽、CC 桌面版测试机真窗口、A14 主屏幕网页，各浅 / 深；记录 `~/pearnly-devtools/test-window/round-b2/B2-测试窗口结果.md`，截图同目录 `shots/`）：

- V7 / V18：字 12、行高 17、高 29、层级 90，`cc-pop-in` 0.12s 主缓动。V11：left 与 width 都是 200ms 主缓动，⌘B 可用，拖宽 341 刷新后保持，收起 -288 / 展开 0。V9 / V38：圆角 16，弹层 `cc-pop-in`、遮罩 `cc-fade-in` 0.12s，关闭钮电脑 32、手机 44。V5：`cc-pulse` 1.2s、0.35↔1，减少动效时停住（真实加载太快，用同类名元素核对；全矩阵 `loading` 场景已比对）。
- 回归：账户菜单离屏边 8；手机宽设置弹窗点按区 44（开关 `::before` 外扩到 44）；手机长按（调试接口按住 0.8 秒）出「置顶 / 重命名 / 删除」，松手不进对话。识别花费 0。
- 用户手机真手指（10-18 晚）：图片预览、长按菜单都正常，蓝色焦点框不碍事。没测：平板竖屏、Windows 触屏。
- 测试窗口的观察（都不是 B2 引入，main 上同样）及去向：
  1. 「语言 ›」「了解更多 ›」`assistant.tsx` 写死 `sideOffset={8}`，量出离父菜单 4，不是 6——已在 V8（B3：子菜单 6）里，B3 照做；上面验收项第 5 条「子菜单离父菜单 6」指 `menu.tsx` 默认值，这两处调用方 B3 前仍是 4。
  2. `companies.tsx:150`、`scheduled-tasks.tsx:440` 的加载卡片仍是 `animate-pulse`——补登进 V5，B4 照做。
  3. 手机打开图片预览时关闭钮带 `:focus-visible` 蓝框（组件库打开弹窗自动聚焦关闭钮）——B2 没动焦点；用户手机看过说不碍事，不改。
  4. adb `input swipe` 模拟长按不出菜单是工具问题（三星按下同一毫秒就发 contextmenu），调试接口触摸事件正常；用户真手指正常。

### B3 做了什么（10-18 夜—10-19，`pearnly/cc-std-b3`）

基线 `origin/main` **81bcd88a**（B2 已合入）；工作树 `.claude/worktrees/cc-std-b3`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。与 B4–B7 同时在各自工作树里做：本批只改 4.2「B3 外壳」的文件，`globals.css` 只按 4.1 补登（见下），共享测试只改断言本批文件的几行。

| 提交 | 内容 | 类 |
| :- | :- | :- |
| 1a16d652 | 纯替换（见下） | 纯替换 |
| d9b78361 | V1 外壳里的 `text-sm / xs` 换实测字阶 | 画面变化 B |
| 2cfb3e33 | V8 菜单离触发器 4、子菜单用包装默认 6 | 原 C·已定 |
| 9c72fca1 | V16 菜单里的快捷键提示用 `.cc-menu-key` | 画面变化 A |
| d1bd1067 | V36 欢迎页形象 `sm:` 改 `md:` | 画面变化 A（无可见变化） |
| fdc27655 | 比对工具补 `install-guide` 场景（iPhone / iPad 欢迎页点「添加」出「添加到主屏幕」步骤；场景脚本先吞掉无界面 Chrome 的 `beforeinstallprompt`，真 iPhone 不发这个事件） | 工具 |
| 686bf75d | V39（B3 补进第 7 节）「添加到主屏幕」步骤的遮罩用 `.cc-overlay` | 画面变化 B |
| 67175ffa | 收紧清单登记 9 个文件 | 清单 |
| （本记录） | 本记录 B3 小节、4.1 补登、第 7 节 V39、第 8 节；ccs.md 菜单写法一条 | 文档 |

**纯替换（1a16d652）做了什么**：

- **欢迎页**：`gap-5 / -mb-2 / gap-2 / px-4 / md:gap-3` 换 `--cc-welcome-gap`、`--cc-space-8 / 16 / 12`；形象 `h-24 / sm:h-28` 换 `--cc-mascot-h / -wide`（`sm:` 在 V36 改）；删掉不起作用的 `md:text-4xl`（与 `text-3xl` 同值，B8 删 `--text-4xl` 那一档）。
- **侧栏头 / 脚**（B2 留下的）：删掉调用处与默认值重复的 `gap-2 px-2`（头）、`px-2`（脚），头下 `pb-1` 换 `--cc-sidebar-head-pb`；标题区 `ps-1.5 pe-1 / gap-2`、logo 与托盘小头像 `size-6` 换 `--cc-space-6 / 4 / 8`、`--cc-logo`、`--cc-avatar-sm`；侧栏主体 `px-2` 换 `--cc-sidebar-pad`；导航几行之间 `gap-0.5` 换 `--cc-row-gap`。
- **标题栏 / 顶栏**：桌面版标题栏左侧四颗 `gap-0.5` 换 `--cc-titlebar-gap`、`-mx-0.5` 换 `--cc-space-2`；顶栏几处 `gap-1` 换 `--cc-topbar-gap`；对话名一组 `gap-0.5` 换 `--cc-space-2`；窗口按钮的 `!size-3.5` 去掉 `!` 换 `--cc-icon-xs`（`.cc-winbtn svg` 在 components 层，工具类本来就盖得过），「还原」图标的 `-scale-x-100` 改 `[scale:var(--cc-mirror)_1]`（见下「与方案不同」第 2 条）。
- **手机抽屉**：标题与搜索 `px-4` 换 `--cc-mdrawer-px`，列表 `px-3` 换 `--cc-mdrawer-list-px`、`pb-28` 换 `--cc-mdrawer-tail`（算出来同是 112），托盘 `px-5 pt-6 pb-[max(1.25rem,…)]` 换 `--cc-mdrawer-tray-px / -tray-pt / -bottom`，头像 `size-12` 换 `--cc-mavatar`；手机顶栏 DrawerIcon 删掉不生效的 `width / height="22"`（`.cc-round-btn svg` 定 20），`strokeWidth="1.5"` 改 `stroke-(length:--cc-icon-stroke)`。
- **菜单**：`!min-w-40` 换 `min-w-(--cc-menu-w-sm)`（`.cc-menu` 在 components 层，不用 `!`）；原来就是 4 的两处 `sideOffset` 换 `menuOffset()`。
- **其他**：ResultsPanel 调用处的 `max-md:fixed max-md:inset-0 max-md:z-50 max-md:w-full` 删掉（B1 已写进 `.cc-results` 的手机规则）；`split-handle.tsx` 的 `px()` 与 `assistant.tsx` 的 `cssPx` 改从 `lib/css-var.ts` 导入（`px()` 成了 `cssPx(cssVar(名, 栏), rootFontPx())` 一行）；整页出错的「重新加载」图标删掉 `size-4`（`.cc-btn svg` 同是 16）；安装步骤遮罩 `z-[70]` 换 `--cc-z-modal`。
- **globals.css（4.1 新登记的例外）**：加 `--cc-mirror` -1；删 `--cc-sidebar-head-pt`。`AWAITING` 表 B3 的 14 项：接上 10 项、删掉 `--cc-sidebar-head-pt` 1 项，都从表里删掉；剩下的三档菜单宽改登记给真正用它们的批次：`--cc-menu-w-md`（176，设置里的菜单）B4，`--cc-menu-w-lg / -max`（208 / 288，公司胶囊菜单 `projects.tsx`）B6。

**与方案不同、按依据定的几处**：

1. **`--cc-sidebar-head-pt` 删掉，不接**：2.2 写「侧栏头纵向 8 / 4」，实际侧栏头上一直是 0（网页的标题区 44、桌面版的标题栏 40 自带高度，调用处写 `pt-0`），接上 8 就是画面变化；没人用又过不了「每个 `--cc-*` 都有人用」。下 4 照接 `--cc-sidebar-head-pb`。
2. **「还原」图标的镜像写 `[scale:var(--cc-mirror)_1]`**：1.2 表说变换「只用变量」，但闸门的 R9 认不出 `scale-x-(--x)` / `rotate-y-(--x)`——`R9_FAMILIES` 里 `scale|scale-[xyz]` 的交替先配上 `scale`，把 `x-(--cc-mirror)` 当成值、判成违例（`scale-(--x)` 这种不带轴的才放行）。闸门脚本是 B0 的文件，不在本批范围，没改；任意属性写法计算值与原来的 `-scale-x-100` 一致（合成节点核对 0 差）。**B8 收口时修这条正则**（把带轴的写在前面），再把这里改回 `scale-x-(--cc-mirror)`。
3. **V36（形象）没有可见变化**：640–767 宽现在一律是手机变体（V37 后变体与 `max-md` 同一条分界），手机外壳里 globals.css 不分层的 `.cc-mshell .pearnly-mascot-host { height: var(--cc-mascot-h) }`（72）本来就压过 `sm:h-28`；`sm:` 改 `md:` 只是去掉 640 这条断点。全矩阵 0 差（tablet-portrait 744、desktop-narrow 700 的欢迎页都在里面）。
4. **V8 子菜单「6」是 `sideOffset`，看上去离父菜单外沿 2**：子菜单按父菜单里那一项定位，项离菜单外沿还有菜单内边距 4，所以原来写的 8 量出来是 4（测试窗口 B2 记的），改成包装默认的 6 后量出来是 2——与侧栏分组菜单、手机「处理月份 / 分店」这些一直用默认值的子菜单一样。V8 定稿的是同一套取值，没改 `--cc-menu-sub-offset`。
5. **V39 补进第 7 节**：记录没列 `install-prompt.tsx` 遮罩的 tw-animate，不改过不了 R8、B8 也删不掉 tw-animate；同 V15 / V38 一类（B）。
6. **快捷键格换 `.cc-kbd` 算在 V1**：设置「快捷提问」那一格原来是工具类拼的（`rounded-md border bg-app px-2 py-0.5 … text-xs`），换成 B1 定义的 `.cc-kbd` 后只差行高 16.8 → 17（格子高 22.8 → 23，合成节点核对，按设置行的 flex 上下文放）；场景里没渲染到它（假服务端的设置里没有快捷键）。

**比对**（`scripts/cc-visual/`，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、773 张（`install-guide` 补进后 777 张），本机同时有别的批在跑，用 3 条跑道，每遍约 30 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b3/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<编号>/report.md`）：

| 比的两遍 | 结果 | 说明 |
| :- | :- | :- |
| 基线 81bcd88a → 纯替换 1a16d652（`reports/base-vs-pure`） | **0 样式差、0 像素差**（报告列出 2 个 + 4 个只在一边，见右） | 列出的 2 个是 desktop 浅 / 深 `boot-page-menu` 的悬停残留（行尾「···」与组箭头，B1 / B2 记过的同一种）：前、后单独各复拍 2 次（`reports/recheck-bpm-1 / -2`）都 0 差，复拍的基线与基线那一遍一致，离群的是纯替换那一遍。只在一边的 4 个是 `install-guide`（基线开拍后才补的场景）：单独给基线补拍（`shots/ig-base`）后与纯替换比 0 差（`reports/ig-base-vs-pure`）。光栅抖动 12 个（窄屏长对话输入框那条渐变）。覆盖检查：改到的 9 个类 / data-slot 场景渲染到 7 个，没渲染到的 `.cc-status`（重连提示条）与场景外的「还原」图标、整页出错按钮图标用合成节点核对（`tools/synth-check.mjs`，两份构建的 CSS 各放进空白页、按变体放「前」「后」的标记，比计算样式与外框）0 差 |
| V1：1a16d652 → d9b78361 | 账户菜单（`account-menu / -submenu`，宽屏 6 种组合 × 深浅）与抽屉账户菜单（`drawer-account`）：顶上账号那一行 `line-height 19.6 → 20`、多了 `cc-menu-label` 类，菜单整体高 0.4、向上开所以上移 0.5；手机抽屉头像 `line-height 19.6 → 20`（窄屏 299 个场景都挂着抽屉，看不见的那份） | 另有 11 个窄屏长对话场景（`chat-rich / -bottom`、`font-sans`、`text-large`、`width-wide`、`th-chat`）同时有抽屉头像那条样式差和像素差：不是「只差像素」所以工具没复拍，单独前、后各拍一遍（`reports/v1-jitter`）像素差换了场景、数值仍是那几种（11075 / 13770 / 11790 …），是同一条光栅抖动带。重连提示条（`text-caption`：高 24.8 → 25）与快捷键格合成节点核对 |
| V8：d9b78361 → 2cfb3e33 | 48 个场景：用户菜单向上开、离触发器 6 → 4（整块下移 2）；「语言 ›」子菜单离父菜单外沿 4 → 2（左移 2）；手机「…」胶囊菜单（`pill-menu`、`boot-page-menu` 窄屏）、月份胶囊菜单（`chin-month` 14 组）离触发器 6 → 4 | 光栅抖动 9 个 |
| V16：2cfb3e33 → 9c72fca1 | 18 个场景：账户菜单、桌面版应用菜单（`titlebar-menu`）、桌面版拉窄的抽屉账户菜单里的快捷键：多了 `cc-menu-key` 类，`padding-left 0 → 12`、`line-height 16.8 → 20`；应用菜单宽 128 → 129.31（最长的一行加上 12 后超出最小宽 128） | 手机、平板、触屏网页不显示快捷键，不变 |
| V36：9c72fca1 → d1bd1067 | **0 差** | 见上第 3 条 |
| V39：d1bd1067 → 686bf75d（中间的 fdc27655 只改场景表） | 只有 `install-guide` 4 组（手机、平板竖屏 × 深浅）：遮罩 `animation enter 0.15s ease → cc-fade-in 0.12s 主缓动`，节点多了 `cc-overlay` 类；层级、底色不变 | 静止画面 0 像素差；光栅抖动 10 个 |

- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b3/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告。V1（账户菜单 22 个场景；抽屉头像那份看不见、没导出）、V8（48 个）、V16（18 个）、V39（`install-guide` 4 组）。V36 0 差，没有图。
- **没有比对的**：悬停、聚焦以外的交互状态（菜单与子菜单同时开时的过渡、遮罩关掉时的淡出），真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）。

**闸门报数**（`--report`）：81bcd88a 的范围外 641 处 → **569 处**（R4 134 → 122、R5 340 → 304、R7 14 → 12、R8 54 → 52、R9 14 → 13、R10 48 → 41、R11 6 → 5、R12 27 → 16，R6 4 不变）；收紧清单 14 → 23 个文件（加 `assistant.tsx` 与 `components/` 的 error-boundary、install-prompt、mascot、mobile-shell、period-chip、shortcut、split-handle、typing-dots），范围内 0 违例。本地全闸门（`~/pearnly-devtools/gate.sh`，照 development.yml）100 步全过。

**还缺什么**：测试窗口验收（下面几项）；合入 main（本次没做：只推了任务分支，按「主线集成」由开发窗口串行变基合入）；B8 修 R9 的带轴变换正则（上第 2 条）。

**给测试窗口的验收项**（三种变体 × 深浅色；本次没有同步到测试机）：

1. 用户菜单（网页、CC 桌面版：侧栏左下头像 / 名字；手机：抽屉左下头像）：离触发器 4（原来 6，整块往下靠近 2）；顶上账号一行 14/20；「语言 ›」「了解更多 ›」子菜单离父菜单外沿 2（原来 4），与侧栏分组菜单的「分组方式 ›」一样（V1、V8）。
2. 菜单里的快捷键（网页、CC 桌面版；桌面版左上「☰」应用菜单、用户菜单的「设置 Ctrl ,」）：灰字 12、离左边文字至少 12，行高跟着菜单项；应用菜单略宽 1px 多（V16）。手机、平板、触屏网页不显示快捷键，不看。
3. 手机对话页右上「…」菜单、输入框下巴 / 手机问候下的月份与分店胶囊菜单：离按钮 4（原来 6）（V8；**要用户手机走一遍**）。
4. 「添加到主屏幕」步骤（**iPhone / iPad 的 Safari 或 Chrome 里**打开网页版，欢迎页安装提示点「添加」）：遮罩淡入 120ms、点「关闭」时淡出（原来 150ms 淡入、直接消失）（V39；要用户 iPhone 走一遍）。
5. 设置「快捷提问」那一格（CC 桌面版）：字 12、高 23（原来 22.8），外观不变（V1）；重连时对话上方「正在重新连接…」一行 12/17（V1，难以复现可不看）。
6. 回归（应无变化）：网页侧栏头、桌面版标题栏（左侧四颗间距 2、窗口按钮图标 14；窗口最大化后「还原」图标是两个方框、前面那个在左下）、托盘、手机抽屉（标题 / 搜索 / 列表左右、底部托盘离底与安全区、列表末尾不被托盘挡住）、欢迎页形象与问候（**平板竖屏、CC 桌面版拉窄到 700 左右也看一眼**，V36）、手机上点结果文件全屏打开结果面板、拖分隔条调侧栏宽度并刷新。
### B5 做了什么（10-18 晚–10-19，`pearnly/cc-std-b5`）

基线 `origin/main` **81bcd88a**（B2 已合入）；工作树 `.claude/worktrees/cc-std-b5`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。与 B3 / B4 / B6 / B7 同时在各自工作树施工，本批只改 4.2 B5 列的 9 个源码文件，加登记文件、`globals.css` 两个补登变量（见 4.1）和断言本批文件的共享测试。

| 14045108 | 纯替换（见下） | 纯替换 |
| b35d1838 | V1（B5 部分）`text-sm / xs` → `text-body / caption` | 画面变化 B |
| 2ed0be00 | V12（B5 部分）允许卡「没送到」原因改用 `.cc-alert` | 原 C·已定 |
| 4332c799 | V13（B5 部分）结果面板工作表、Word 预览表格改用 `.cc-table` | 原 C·已定 |
| 2619b177 | V23 结果面板头部按钮 `.cc-icon-btn cc-tap`、「下载」`.cc-btn` | 画面变化 A |
| b0f7fd41 | V24 结果面板两套页签统一 `.cc-tabs` | 原 C·已定 |
| 9357207a | V26 Word 预览照对话 Markdown | 画面变化 A |
| 19f43eab | V27 会话栏卡片字号继承、加载骨架、步骤明细 | 画面变化 A |
| a113b8e9 | 收紧清单登记 9 个文件 | 清单 |
| （本记录） | 本记录 B5 小节、4.1 例外；ccs.md 一条 | 文档 |

V25（结果面板头高 48）定稿就是不变：纯替换里写成 `h-(--cc-results-head-h)`，没有单独的画面变化提交。

**纯替换（14045108）做了什么**：

- 写死的间距、尺寸换同值变量：结果面板头（高、间距、左右）、文本 / 源码预览内边距、读不出预览时的图标（`--cc-icon-2xl`）与文件名最宽（`max-w-[80%]` → 分数 `max-w-4/5`，计算值同为 80%）；知识库编辑框、文件夹列表、月结异常组标题、允许卡改动清单、手机允许浮层；Word 预览的正文最宽、内边距、列表标记间距、下划线偏移（`--cc-link-offset`）；步骤明细的上下。
- `.cc-btn` 里的「下载」「让 CC 起草」图标去掉 `size-4`（`.cc-btn svg` 本来就是 16）。
- 两处转圈改 `.cc-spinner`：结果面板加载（`size-(--cc-icon)` 保留 20，只用变量写尺寸）、文件夹「在电脑上选择」按钮里的忙状态。计算样式只差动画名 `spin → cc-spin`（同为 1s 匀速转一圈，减少动效时两者都照转），本地场景 `x-results-loading` 14 组核对：样式差只有这一项和节点多了类名，像素 0 差。
- 允许卡出错原因的两行截断读 `--cc-alert-lines`。
- `globals.css` 补登 `--cc-doc-w`、`--cc-alert-lines`（4.1）；`--cc-results-head-h`、`--cc-link-offset`、`--cc-icon-2xl` 接上，从 `AWAITING` 删掉。
- 共享测试断言改成新写法（`test_assistant_ui.py` 的出错原因与 Word 预览、`test_cc_month_ledger.py` 的组标题、`test_cc_idle_motion.py` 的「在忙」动画登记）。

**与第 7 节不同、按依据定的几处**：

1. **V1 在本批只有 4 处可见**：结果面板「读不出预览」一行、会话栏整栏出错一行、文件夹 / 账套 / 任务 / 草稿行里的 12px 淡色小字。结果面板顶上的文件下拉框也换了 `text-body`，但 Chrome 对 `<select>` 固定 `line-height: normal`，画面不变。页签、出错框、卡片里的字、Word 预览的 `text-sm / base` 分别随 V24、V12、V27、V26 换掉，不算在 V1。
2. **V12 在本批只有允许卡**：会话栏里的出错（整栏、月结卡、卡片里）是一行红字、不是框，不改成 `.cc-alert`；整栏那一行属 V1，卡片里的属 V27。
3. **V26 的标题字号**：`title / h1 / h2` 的 `text-2xl / xl / lg` 是 1.2 允许的文档标题档，不动；`h3 / h4` 的 `text-base` 改 `text-prose`（16/22.4 → 16/24），`h5 / h6` 的 `text-sm` 改 `text-body`（14/19.6 → 14/20），并在本条里。单元格里的段落与标题都不加块距（原来只有段落去掉了下边距，单元格里的标题还留着上边距）；表中表同样不加。块距常量在组件里写一处（`BLOCK_GAP / HEADING_GAP`）。
4. **V27 的范围**：「继承 13/19」包括知识库、文件夹、记住的选择的出错行和文件夹卡「在电脑上选择」那行；记住的选择**展开时**出错行在 12/17 的说明区里，继承下来是 12/17。加载骨架是两条字高的条（`PanelSkeleton`，`components/company-cards.tsx`，会话栏整栏与公司页「月结」卡共用）。步骤明细命令与输出之间的线 0.5 → 1px：本机 Chrome 把 0.5px 边线算成 1px（计算样式两边都是 1px，比对看不出），只有按物理像素画细线的浏览器（iPhone Safari）上才看得出，验收要看手机。
5. **结果面板的加载转圈不改成骨架**：第 7 节没列，面板里要预览的类型取到之前不知道，骨架画不出形状；保留转圈、只换成 `.cc-spinner`（纯替换，见上）。

**比对**（`scripts/cc-visual/`，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、773 张，3 条跑道，每遍约 30 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b5/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<编号>/report.md`）。主场景表没渲染到的状态（加载、出错、允许卡没送到、步骤明细、读不出预览、第二张工作表）另用**本地加场景**补：仓库的比对工具复制一份到 `~/pearnly-devtools/cc-std-b5/tool/`（不进仓库，免得和并行批次抢 `scripts/cc-visual`），加 9 个 `x-*` 场景，假服务端副本加一个读不出预览的 `.zip` 结果文件、`/__hold` 的毫秒给负数时该接口回 417 与一句原因；7 种环境 × 浅深 = 14 组、126 张，每遍约 10 分钟，截图 `…/shots-x/<名>`，报告 `…/reports/x-<编号>`。

| 比的两遍 | 主场景结果 | 本地加场景结果 |
| 基线 81bcd88a → 纯替换 14045108（`base-vs-pure`） | **0 样式差、0 像素差**；光栅抖动 12 个（窄屏长对话输入框那条带，复拍出现一模一样的一对）。覆盖检查列的 `.cc-note-editor` 是工具把 `data-slot="cc-note-editor"` 里的字也当成类名，节点 `div[cc-note-editor]` 在 notes-edit 场景前后都渲染到了；`.cc-spinner` 由本地场景补 | `x-results-loading` 14 组：动画名 `spin → cc-spin`、节点多了类名，像素 0 差；其余 112 个 0 差 |
| V1：纯替换 → b35d1838 | 346 个场景（宽屏 8 组的会话栏全部场景、公司页三场景的各组）：只有行高 `16.8 → 17`（文件夹、账套、任务、草稿行的小字）与随之下移的外框 | 另多 `x-results-nopreview`、`x-panel-error` 的 `19.6 → 20`，其余同 |
| V12：b35d1838 → 2ed0be00 | 0 差（主场景没有没送到的允许卡；光栅抖动 10 个） | `x-approval-failure` 12 组（桌面版拉窄两组前后都没打开，见下）：出错框 `display block → flex`、间距 8、行高 `19.6 → 20` |
| V13：2ed0be00 → 4332c799 | `results-sheet`、`results-doc` 14 组：单元格左右 `10 → 12`，别的不变 | `x-results-tab2` 同 |
| V23：4332c799 → 2619b177 | `results-*` 四场景 14 组：两颗头部按钮换 `.cc-icon-btn cc-tap`（字色 ink-2 → ink-3、图标 16 → 20、120ms 过渡；手机、平板竖屏、触屏网页、桌面版拉窄 32 → 44，触屏上的 CC 桌面版仍 32）。另 desktop-dark `boot-page-menu` 1 个悬停残留：v13、v23 两份构建各单独复拍 2 次（`reports/recheck-bpm-1 / -2`）都 0 差，判为 v23 那一遍离群（下一遍 v24 又回到原样） | `x-results-nopreview`：「下载」76×34 → 80×32（触屏 84×44）、字重 500、白底；`x-results-loading / -tab2` 同头部按钮 |
| V24：2619b177 → b0f7fd41 | `results-html`、`results-sheet` 14 组：工作表页签间距 `4 → 16`、整行左右 `8 → 16`、页签左右 `8 → 0`、上下 `6 → 8`、指示线位移 `-1 → -0.5px`；两套行高 `19.6 → 20`、过渡、聚焦描边；`boot-page-menu` 那处回到原样 | `x-results-tab2` 同 |
| V26：b0f7fd41 → 9357207a | `results-doc` 14 组：段落、标题去掉下边距、加上边距（0.75em / 1.25em，第一块 0）；正文电脑 `14/22.75 → 14/20`、手机与平板竖屏 `14/22.75 → 16/24`；二级列表缩进 `21 → 28`；表格上下 `12/12 → 0.75em/0` | 0 差 |
| V27：9357207a → 19f43eab | 0 差（主场景没有加载中、出错的会话栏，步骤明细没点开；光栅抖动 17 个） | `x-panel-loading`、`x-month-loading`：转圈换成两条骨架；`x-month-error`：出错行 `14/19.6 → 13/19`；`x-step-detail` 12 组：明细左右 `12 → 8`（分隔线见上第 4 条） |

- **动作没做完**（前后一致）：本地场景 `x-approval-failure`、`x-step-detail` 在桌面版拉窄（desktop-narrow）两组没打开（那里是手机外壳，允许浮层的「允许」与步骤行的位置和场景动作对不上）；手机、平板竖屏的同一场景都打开了。
- **没有比对的**：知识库、文件夹、记住的选择的出错行（要点保存 / 移除 / 删除失败才出，假服务端没接；写法与 `x-month-error` 同一处改动，字号由 `.cc-panel-card-body` 决定）、文件夹「在电脑上选择」的忙状态（要 CC 桌面版桥），悬停、聚焦以外的交互，真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）。
- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b5/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`（主场景）与 `visual/<编号>-本地场景/…`，每个目录带一份比对报告。V1（346 + 72）、V12（12，只有本地场景）、V13（28）、V23（56 + 42）、V24（28 + 14）、V26（14）、V27（54，只有本地场景）。上面写明的离群复拍没导出。

**闸门报数**（`--report`）：81bcd88a 的范围外 641 处 → **552 处**（R4 134 → 116、R5 340 → 280、R6 4 → 3、R8 54 → 50、R9 14 → 10、R10 48 → 47、R12 27 → 26，R7 14、R11 6 不变）；收紧清单 14 → 23 个文件，范围内 0 违例。


1. 结果面板（对话里点结果文件卡）：头部「下载」「关闭」是 32 的图标钮、字色淡一档、图标 20，键盘 Tab 到上面有聚焦描边；**手机、平板竖屏 44**（要用户手机走），触屏 Windows 上的 CC 桌面版仍 32（V23）。读不出预览的文件（如 .zip）中间的「下载」是标准文字按钮（白底、细边、字重 500）。
2. 结果面板页签：HTML 文件的「预览 / 源码」与多张工作表的表名页签同一套——间距 16、上下 8、当前页签 2px 线（工作表页签的线在上边、贴表格）（V24）。工作表单元格左右 12（V13）。
3. Word 预览（.docx）：段落之间 0.75em、标题前 1.25em、没有段后空；正文电脑 14/20、**手机 16/24**；多级列表每级缩 28；表格单元格左右 12（V26、V13）。
4. 会话栏（挂了公司的对话、公司页）：打开或换公司时先出两条骨架、不是转圈（网速快时一闪而过，可用浏览器限速看）；月结卡、整栏出错时红字一行（卡片里 13/19、整栏 14/20）；文件夹 / 账套 / 任务 / 草稿行的小字行高 17（V27、V1）。
5. 允许卡：允许时确认没送到（断网后点「允许」）按钮上方是淡红底、细红边、圆角 8 的出错框，原因最多两行；**手机浮层里同样**（V12）。
6. 步骤明细：点开「已运行 1 个命令」再点那一步，命令与输出离左边 8（同步骤行），中间的分隔线 **iPhone Safari 上由细线变成 1px**（电脑 Chrome 上本来就画成 1px，看不出）（V27）。
7. 回归（应无变化）：结果面板头高 48（V25）、PDF 逐页、图片预览、文件下拉框；会话栏卡片拖动排序、折叠、知识库编辑「取消 / 保存」。

**还缺什么**：测试窗口验收（上面几项，手机项要用户手机走）；按「主线集成」变基到最新 `origin/main`、快进合入 main（本次没做，B3 / B4 / B6 / B7 同时在改共享测试与本记录，合入时串行变基）；部署随下一批。
### B6 做了什么（10-18 晚–10-19，`pearnly/cc-std-b6`）

基线 `origin/main` **81bcd88a**（`cc-drag-perf`、第 13 轮拖动、长按竞争修复都已在 main 里）；工作树 `.claude/worktrees/cc-std-b6`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。
拖动是共用模块，**drag.tsx、`hooks/use-long-press.ts`、手机长按菜单的行为不改**：drag.tsx 只换了 `cssMs` 的来源、浮层层级改成拿起时读（值仍是 55）、播报区换成 `sr-only` 加原来的定位与裁切（计算样式逐项相同）；`use-long-press.ts`、`lib/layers.ts` 没碰。第 13 轮拖动与长按竞争的用例（`test_cc_mobile_overlays.py` 全部、`test_assistant_ui.py` 的 SidebarDragTests 与长按项）照过，断言只改了写法（见下）。

| e5efbdcb | 纯替换（见下） | 纯替换 |
| 354e567f | V1 侧栏无结果行 `.cc-empty-line`、选公司菜单的无结果行 `.cc-menu-label` | 画面变化 B |
| eb99cfd3 | V8 新对话选公司菜单离按钮 4（原 6） | 原 C·已定 |
| 5535f73c | V28 「加载更多」行与加载骨架行高 `--cc-row-h` | 画面变化 A |
| 2210cdac | V28（B6 补进第 7 节）「加载更多」转圈 `.cc-spinner` | 画面变化 A |
| db55cc13 | V29 会话行 `.cc-thread-row` | 画面变化 A |
| c33773b3 | V29 行尾「···」`.cc-row-btn`（不再用组件库 Button） | 画面变化 A |
| 9e62b87d | 收紧清单登记 4 个文件 | 清单 |
| （本记录） | 本记录 B6 小节、第 7 节 V28 / V29；ccs.md 一条 | 文档 |

**纯替换（e5efbdcb）做了什么**：

- `thread-list.tsx`：列表、骨架的行间 `gap-(--cc-space-2)`；搜索框图标 `size-(--cc-icon-sm)`；「新建」加号去掉 `!size-4`（`.cc-new-dot svg` 已给 16，B1 第 7 条）；行菜单、多选菜单的图标去掉 `size-4`（`.cc-menu-item svg` 给 16）；`!min-w-44` → `min-w-(--cc-menu-w-md)`（`!` 原来就多余：`.cc-menu` 在 components 层，工具类本来就盖得过）；`sideOffset={menuOffset()}`；行尾「···」右距 `end-(--cc-space-4)`；「加载更多」行、骨架行左右 `px-(--cc-row-pad)`，骨架条去掉 `h-3.5`（`Skeleton` 默认 `--cc-skeleton-h` 14）。
- 列表预取：`IntersectionObserver` 的 `rootMargin` 建观察器时读 `--cc-observe-margin`（2.6），回退常量 `OBSERVE_MARGIN = 200` 与 globals.css 同值由测试核对。
- `sidebar-group.tsx`：分组和排序菜单 `sideOffset={menuOffset()}`、`min-w-(--cc-menu-w-lg)`，三个子菜单 `min-w-(--cc-menu-w-sm)`。
- `projects.tsx`：选公司菜单的搜索框外距 `--cc-space-4`、`min-w-(--cc-menu-w-lg) max-w-(--cc-menu-w-max)`；手机「处理月份」「分店」子菜单 `min-w-(--cc-menu-w-sm)`。
- `drag.tsx`：`cssMs` 从 `lib/css-var.ts` 导入；浮层层级 `overlayZ()` 拿起时读 `--cc-z-drag`（`fixedPx`，读到就记住），`OVERLAY_Z = 55` 留作回退并由测试核对；播报区原来内联写 `width / height / margin` 的 `1px / -1px`（R12），改成 `className="sr-only"` 加内联的 `position: fixed`、`top / left: 0`、`border: 0`、两种裁切——第一版漏了 `border: 0`，全矩阵报出播报区节点 `border-top-style none → solid`（`sr-only` 只把边宽归 0，预检样式给了 solid；边宽 0 看不见），补上后重拍为 0 差。
- `AWAITING` 表：`--cc-menu-w-sm / md / lg / max`（原登记 B3）、`--cc-observe-margin`（原登记 B4）、`--cc-z-drag`（B6）B6 接上，从表里删掉。B3 / B4 合入时 `test_cc_ccs.py` 这两行会冲突，照删即可（它们用到同一批变量时不用再动表）。
- 测试断言改成新写法、意思不变：`test_cc_mobile_overlays.py`（月份子菜单的类、组菜单 `sideOffset={menuOffset()}`）、`test_assistant_ui.py`（选公司菜单的类、浮层层级改读变量并核对 `--cc-z-drag: 55`、会话行与「···」见 V29）、`test_cc_idle_motion.py`（「加载更多」转圈的登记由 `animate-spin` 改 `cc-spinner`，仍只在 `loadingMore &&` 时挂）；`test_cc_ccs.py` 新增一项核对列表预取距离、浮层层级的回退值与 globals.css 一致、drag.tsx 不再从 hover-scroll 再导出拿 `cssMs`。
- globals.css **没有补登**（本批用到的档位 B1 都已登记）。


1. **V28 补进转圈**：记录没列「加载更多」行的 `size-3.5 animate-spin`，不改过不了 R8。换成 `.cc-spinner`（16、1s 匀速，同其他行内转圈）是可见变化（14 → 16），按 4.1 补进第 7 节 V28 那一行（同一行的东西，不另起编号，免得与并行的 B3–B7 撞号），单独一个提交。
2. **V29 分两个提交**（行、「···」），比对各自归因。实际差在：行从 `transition-colors`（8 种属性）变成只过渡底色与字色；**悬停底不再包在 `(hover: hover)` 里**——Tailwind 的 `hover:` 只在能悬停的设备生效，`.cc-thread-row:hover` 同侧栏其他 `cc-*` 行（导航项、组标题、显示更多）一样不分设备，触屏上点过 / 长按过的行可能留着悬停底，要用户手机看一眼（见验收项）。「···」字色从随行（ink-2，当前行 ink）变成 ink-3、深色悬停底从半透明变成整档；按钮自己的 `text-sm font-medium whitespace-nowrap gap` 随 Button 去掉（按钮里只有图标与读屏文字，看不出）。
3. **比对工具用了副本**：`scripts/cc-visual/` 不是本批文件，库里不改；在 `~/pearnly-devtools/cc-std-b6/shim/`（复制一份，`apps/` 链回工作树）加了 8 个场景与两样桩：`sidebar-more`（假服务端第一页带下一页游标、翻页请求挂起：「加载更多」行与转圈，宽屏滚侧栏、窄屏滚抽屉到底）、`sidebar-row-more-hover`（悬停到「···」上）、`sidebar-group-submenu`（分组和排序的子菜单）、`drawer-drag`（手机 / 平板抽屉里手指长按 350ms 后挪动、不松手：拖动中静态帧；新动作 `tpress`）、`chin-company` / `chin-company-search`（新对话选公司菜单与查不到的结果；公司搜索带 `zz` 时假服务端回空）；窄屏触屏点开选公司菜单会自己关上（前后一致），逐项比对时另加两个鼠标点开的 `chin-company-m` / `-search-m`（只在 `CCV_EXTRA=1` 时拍，不进全矩阵）。拖动中（宽屏 `sidebar-drag`、窄屏 `drawer-drag`）与长按菜单（`drawer-longpress`、触屏宽屏的 `sidebar-row-menu` / `delete-confirm` / `boot-page-menu` 走长按）都在全矩阵里。
4. 跑道 3 条（本机同时有 B3–B5、B7 在跑），全矩阵每遍约 50 分钟、825 张。

**比对**（B6 的工具副本，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、825 张；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b6/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<编号>/report.md`。画面变化逐项用「同一组场景、前后两份产物各拍一遍」的子集比对（16 组环境全拍，只拍相关场景，`subset.sh`），再用纯替换 → 最终版的全矩阵核对没有别处变化）：

| 基线 81bcd88a → 纯替换 e5efbdcb（`reports/base-vs-pure`） | **0 样式差、0 像素差** | 光栅抖动 13 个（窄屏长对话输入框那条渐变带，复拍出现一模一样的一对）。覆盖检查：diff 改到的 4 个 cc-* 类 / data-slot 场景全部渲染到（`reports/base-vs-pure-cov`）。第一版报 805 个场景差，全是播报区节点 `border-top-style / -color`（见上），补 `border: 0` 后重拍 |
| V1：e5efbdcb → 354e567f（`reports/V1`） | 只有无结果行 `line-height 19.6 → 20`：侧栏搜索查不到（14 组）、选公司菜单查不到（宽屏 8 组 + 手机 / 平板鼠标点开的 4 组）；向上开的选公司菜单因此高 0.4、整块上移 0.5 | 内边距、颜色不变 |
| V8：354e567f → eb99cfd3（`reports/V8`） | 只有选公司菜单的位置差 2：电脑下巴里向上开的往下 2、手机问候下面向下开的往上 2 | 其余 0 差 |
| V28 行高：eb99cfd3 → 5535f73c（`reports/V28`） | 骨架行（`loading`）与「加载更多」行（`sidebar-more`）的高：网页 32 不变（网页 4 组 0 差），CC 桌面版及其触屏版 32 → 30，窄屏（手机、平板竖屏、桌面版拉窄）32 → 44；随之下面的东西上下挪 | 骨架条仍是 14 高、左右 10 |
| V28 转圈：5535f73c → 2210cdac（`reports/V28s`） | 只有 `sidebar-more` 14 组：转圈 14 → 16、`animation-name spin → cc-spin`（同为 1s 匀速） | 差异像素 248–327（转圈本身） |
| V29 行：2210cdac → db55cc13（`reports/V29r`） | 每一行的 `transition-property` 由 Tailwind `transition-colors` 的 8 种变成 `background-color, color`（时长、曲线仍是 120ms 主缓动）；**触屏宽屏（web-coarse、desktop-coarse）`sidebar-row-hover` 鼠标悬停的行多了悬停底**（原来 `hover:` 只在能悬停的设备生效） | 其余环境静止画面 0 像素差；手机、平板竖屏的抽屉、长按、拖动场景 0 像素差 |
| V29「···」：db55cc13 → c33773b3（`reports/V29`） | 「···」字色 ink-3（原来随行：ink-2，当前行 ink）；悬停 / 菜单开着时浅色字从 accent 前景（82,81,78）变正文色、深色底从半透明（bg-accent/50）变整档悬停底 `rgba(255,255,255,.075)`；按钮的 `transition-property all → background-color, color, opacity`；随 Button 去掉的 `font-size 14 → 16 / font-weight 500 → 400 / line-height / white-space / gap / outline-color`（按钮里只有图标与读屏文字，看不出） | 差异像素每处 100 左右（图标本身）：悬停、菜单开着、多选、长按的行 |
| 纯替换 e5efbdcb → 最终 c33773b3 全矩阵（`reports/pure-vs-final`） | 821 / 825 个场景有样式差，全部是上面几项（逐条按元素与属性归类，没有别的属性或元素） | 像素差除上面几项外，窄屏长对话输入框带（`chat-rich*`、`font-sans`、`width-*`、`th-welcome`）与一次 `titlebar-menu`：这些场景带着行的过渡样式差、比对工具不自动复拍，另把两份产物在这 9 个场景上各复拍两轮（`reports/recheck-1 / -2`）——输入框带每轮落在不同场景（两种光栅之一，同 B1 / B2），`titlebar-menu` 两轮都 0 差，判为离群；稳定出现的只有 V29 的两种 |

- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b6/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告。V1（30 个场景）、V8（28）、V28（20）、V28s（14）、V29r（触屏宽屏的行悬停 8 个）、V29（悬停、悬停到「···」上、菜单开着、长按、生产页加载方式 40 个）。V29r / V29 只导出有像素差的场景（其余只差过渡样式，静止画面一样）。
- **没有比对的**：悬停、聚焦以外的交互状态，真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）；ERP / DMS 的按日子分组侧栏（假服务端是 Cowork，按日子分组的无结果行与公司分组同一个类）；桌面版拉窄（非触屏）的选公司菜单（点不开，前后一致）。

**闸门报数**（`--report`）：81bcd88a 的范围外 641 处 → **585 处**（R4 134 → 111、R5 340 → 326、R8 54 → 53、R10 48 → 38、R12 27 → 19，其余不变）；收紧清单 14 → 18 个文件（加 `components/assistant-ui/` 的 projects、sidebar-group、thread-list 与 `components/ui/drag.tsx`），范围内 0 违例。

**还缺什么**：测试窗口验收（下面几项）；合入 main（按「主线集成」，本次只推了任务分支）；与并行的 B3 / B4 合入时 `test_cc_ccs.py` 的 `AWAITING` 表按上面说明解冲突；B8 删 button.tsx 时会话行已经不是它的调用方。


1. 会话行「···」（网页、CC 桌面版，有鼠标）：悬停一行出「···」，字色淡（ink-3），鼠标移到「···」上变正文色、带悬停底（深色也是整档，原来很淡）；点开菜单时同样；当前对话那一行的「···」也是淡色（原来跟行字一样深）（V29）。
2. 会话行的悬停 / 当前 / 键盘焦点 / 菜单开着的底色与字色照旧，过渡 120ms（V29）。**触屏**：手机、平板、Windows 触屏上点过或长按过的行，松手后悬停底会不会留着（原来不会，现在同侧栏导航项、组标题）——**要用户手机 / 平板走一遍**，留着且碍眼就告诉开发窗口。
3. 侧栏搜索查不到、新对话选公司菜单搜查不到：「没有结果」一行高 20（原 19.6，看不出）（V1）；电脑下巴的选公司菜单离按钮 4（原 6）、手机问候下面的同样（V8）。
4. 首次打开侧栏的加载骨架：CC 桌面版每行 30、手机抽屉每行 44（原来都是 32），网页不变；对话很多时滚到侧栏底的「加载更多」转圈 16（原 14）（V28）。
5. 回归（应无变化，**拖动与长按要用户手机走**）：电脑上拖对话行排序 / 拖进已置顶、浮层在菜单下面、拖动中整页不出悬停；手机抽屉长按 250ms 后拖、长按不动出「置顶 / 重命名 / 删除」、松手不进对话；读屏念拖动播报；侧栏分组和排序菜单与子菜单的宽度与位置；手机对话页「…」里的处理月份、分店。
### B7 做了什么（10-18 夜 – 10-19，`pearnly/cc-std-b7`）

基线 `origin/main` **81bcd88a**（B2 已合入，前置的 `cc-scroll-fix` 早已在 main）；工作树 `.claude/worktrees/cc-std-b7`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。B3–B6 同时在各自工作树里做，B7 只改本批 5 个源码文件、`globals.css` 补缺档（4.1 例外）、登记文件与共享测试里断言本批文件的那几行。
对话区的滚动（`lib/follow-scroll.ts`、`useSettleOnOpen`、`turnAnchor="top"`）和输入法回车（`holdImeEnter`）一行没动，`test_cc_run_indicator.py`（含 follow-scroll 的 node 用例）、`test_assistant_ui.py` 的输入法用例照过。图片预览打开时关闭钮自动聚焦的蓝框（B2 验收用户看过不碍事）没动焦点行为。

| 9bb8f4ed | 比对工具先补覆盖再拍基线：富文本对话加 1–4 级标题、分隔线、脚注和一张用户附件卡；`compose-uploading`（挂起上传接口：方块的上传中转圈与遮罩）、`send-pending`（上传没完成就发送：发送中气泡与附件骨架）两个场景。场景 70 → 72，全矩阵每遍 801 张 | 工具 |
| d9cb99bd | 纯替换（见下） | 纯替换 |
| f9fdf7ed | V1 字号换实测字阶 | 画面变化 B |
| c0c8d45c | V3 用户消息「复制」条改按触屏标记 | 画面变化 A |
| 6864f9af | V72（B7 补进第 7 节）用户消息改用 `.cc-user-row`，「复制」条回到气泡下方右侧 | 画面变化 A（缺陷修复） |
| b187e690 | V12 出错框 `.cc-alert` | 画面变化（原 C·已定） |
| 0b6851e2 | V32 出错块去掉 `mt-2` | 画面变化 A |
| 2666b128 | V30 回复与代码块「复制」`.cc-row-btn cc-tap`；`TooltipIconButton` 不再包 shadcn Button | 画面变化 A |
| f5579212 | V70（B7 补进第 7 节）滚到底 `.cc-scroll-btn` | 画面变化 A |
| 5365f730 | V71（B7 补进第 7 节）附件移除钮自己写标准写法 | 画面变化 B |
| 818ad90c | V31 发送中气泡的附件 = 同款 `.cc-attach-card` + 骨架 | 画面变化 A |
| 71932df1 | V33 代码块 `.cc-code-block-head` / `.cc-code-block` | 画面变化（原 C·已定） |
| f6ceadf4 | V34 复制图标切换的时长与缓动 | 画面变化 B |
| 52294f91 | V35 附件只留一层悬停、去掉 1px 模糊、转圈 `.cc-spinner` | 画面变化 A |
| a97486f6 | V36 图片预览 `sm:` 改 `md:` | 画面变化 A |
| 9f524e6f | 收紧清单登记 4 个文件 | 清单 |
| （本记录） | 本记录 B7 小节、4.1 例外、4.2 / 第 7 / 8 节 B7 的行；ccs.md 一条 | 文档 |

每个提交都单独跑过 `npm run typecheck`、本批相关的 9 个共享测试文件、`check-frontend-tokens.py`（`git rebase --exec` 逐个提交跑，全过）。

**纯替换（d9cb99bd）做了什么**：

- **写死值换成同值变量**：栏宽 `max-w-[calc(var(--cc-thread-w)+var(--cc-thread-gutter)*2)] px-(--cc-thread-gutter)`，删 `thread.tsx` 自设的 `--thread-max-width`（2.4 第 3 条）；一轮之间 `--cc-turn-gap`、末尾让位 `--cc-thread-tail`、工作状态行 `--cc-run-gap / -reply`、回复操作行 `--cc-reply-actions-h`（`-mb / pb / min-h` 三处）、`content-visibility` 占位 `--cc-msg-intrinsic`、输入框最高 `--cc-composer-input-max`、四点与计时之间 `--cc-dots-gap`；Markdown 标题 `--cc-md-h1 / -h2 / -heading-lh / -heading-scroll`、列表项 `--cc-md-li-gap`、引用竖线 `--cc-md-quote-w`、链接下划线 `--cc-link-offset`、行内代码 `--cc-code-inline-size`；图片预览 `--cc-preview-w / -share`；其余间距、尺寸换刻度。
- **换成已有的共用类（计算值逐项相同）**：提醒卡与结果文件卡 `.cc-file-card` / `.cc-file-card-icon`（图标格里的 `size-4.5` 删掉，由类给 18）、回复表格 `.cc-table`（单元格的 `min-w-24 px-3 py-1.5 text-start font-medium` 删掉，V13 在 B7 这一处本来就是这套）、发送中气泡 `.cc-user-row`、停止钮 `data-stop`（去掉 `!size-3.5`，B1 的 `.cc-send[data-stop] svg` 给 14）。
- **删死代码**：组件库默认的欢迎区 `ThreadWelcome`（CC 总是传自己的 Welcome）、`ThreadSuggestions`（运行时从不给建议，只剩一个 `empty:hidden` 的空容器）、`follow-up-suggestions.tsx` 整个文件；`TooltipIconButton` 外面那层 `TooltipProvider delayDuration={0}`（每个 Tooltip 自带一层 Provider，外层从不起作用，B2 点名）。
- **globals.css（4.1 新登记的例外）**：用途层「对话」补 `--cc-file-card-py` 10（卡片上下、提醒卡内正文与按钮之间）、`--cc-alert-lines` 2（出错行最多两行，`line-clamp-(--cc-alert-lines)`）、`--cc-compose-x-stroke` 2.5px（深色下附件「×」加粗）；V30 再补 `--cc-press-scale` 0.94（图标按钮按下，同 `.cc-icon-btn:active`）。都是「现有值·CC 自定」。
- **共享测试**：断言改成新写法、意思不变（`test_cc_ccs.py` 的回复内边距、表格、链接、行内代码、栏宽；`test_cc_mobile_overlays.py` 的预览占高、「新对话输入框下不多出空容器」改成断言建议容器已删；`test_cc_run_indicator.py` 的 `ThreadWelcome` 锚点改 `const Composer: FC`、发送失败、状态行；`test_assistant_ui.py` 的计时 Marquee、`line-clamp`）。`AWAITING` 表删掉 B7 一节（全部接上），另删 `--cc-composer-input-max`（登记 B4，`thread.tsx` 先用上了）、`--cc-link-offset`（登记 B5，`markdown-text.tsx` 先用上了）——B4 / B5 合入时这两行会有小冲突，删掉即可。


1. **V13 在 B7 没有可见变化**：对话里的表格本来就是 12 / 6，换 `.cc-table` 是纯替换；10 → 12 的变化在 B5（结果面板、文档预览）。
2. **补进第 7 节三项，编号从 V70 起**（B3–B6 同时在补，避免撞号）：V70 滚到底、V71 附件移除钮——都是「`TooltipIconButton` 不再包 Button」带出来的（B2 点名要做，第 7 节只列了 V30 的复制钮）；V72 用户消息的 `[&:where(>*)]:col-start-2` 从来没生成规则（构建产物里没有这条），复制条一直落在左边那一大列、在气泡左下外侧，换成 B1 的 `.cc-user-row` 后在气泡下方右侧（同 claude.ai，代码注释原本就写「气泡下方右侧」）。
3. **V30 的「按下 0.94」**：`.cc-row-btn` 本身没有按下缩放（会话行「···」不缩），回复与代码块的复制按钮在组件上加 `active:scale-(--cc-press-scale)`，补登 `--cc-press-scale`（同 `.cc-icon-btn:active` 的 0.94）。
4. **V31 的做法**：发送中的附件不再是一个小胶囊，而是和发出后同一种 `.cc-attach-card`、同一个位置（附件那一排在气泡上面、靠右），缩略图位置放骨架；组件放在 `attachment.tsx` 的 `PendingAttachments`（`AttachmentCard` 加 `loading`）。
5. **V33 比记录多出的两样**：`.cc-code-block-head / .cc-code-block` 是 B1 按标准定的外观，换上后除了圆角 12 → 10、行高 1.625 → 20，还有边线从半透明的 `border/50` 变成正常线色、头部底色从 `muted/50` 变成叠色底（同对话里别的块）、代码区不再垫 `muted/30` 的底、语言名从正文色变淡色。同属「代码块统一成对话里的块级卡片」，不另起编号。
6. **V35 顺带**：上传中的转圈从 `animate-spin` 换成 `.cc-spinner`（同为 1 秒一圈匀速，图标 20 用 `size-(--cc-icon)` 保持），`test_cc_idle_motion.py` 的登记跟着改。
7. **V3 的范围**：按 `data-cc-touch` 后，触屏 Windows 上的 CC 桌面版（desktop-coarse）显示复制条；CC 桌面版拉窄成手机外壳（desktop-narrow，手机变体也带触屏标记）不再显示——与手机一致，规则就是「手机变体，或粗指针且不是桌面窗口」。
8. **V32 实测**：出错块不再自带 `mt-2`，间距归 `.cc-reply` 的块间距；假数据里的出错回复只有这一块（是第一块），所以从 8 变 0，排在正文后面时是 0.75em。

**比对**（`scripts/cc-visual/`；全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、801 张；对话区子集 = 7 种环境 × 浅深 × 12 个对话场景 = 143 张；并行跑道 3 条（本机同时还有 4 个批次在跑），全矩阵每遍约 54 分钟、子集约 7 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b7/dist-<名>`，截图 `…/shots/<名>`、`…/shots/sub-<名>`，报告 `…/reports/<前>-vs-<后>/report.md`。工具的报告每个场景只列前 30 条差异，下表的种类用 `~/pearnly-devtools/cc-std-b7/allkinds.mjs` 拿工具自己的 `diffSnapshots` 对全部快照逐条汇总）：

| 基线 81bcd88a（含工具提交）→ 纯替换 d9cb99bd，全矩阵（`reports/base-vs-pure`） | **0 样式差、0 像素差**（DOM 标签与死代码见右） | 「样式差异」2712 条全是类名标签：`span → span.cc-file-card-icon` 1221、`div.cc-card(.cc-card-hover) → div.cc-file-card(.cc-card-hover)` 1221、`table.aui-md-table → +.cc-table` 183、`div → div.cc-user-row` 14；另 73 个「只在前」都是删掉的 `display:none` 空容器 `aui-thread-welcome-suggestions`。9 个场景有像素差，全在窄屏长对话输入框那一条渐变带（B1 记过的光栅抖动带）；因为同场景有标签变化，工具不自动复拍，手工前、后各复拍 2 遍（`shots/re-pure-*`），9 个场景都出现一模一样的一对图，判为抖动。覆盖检查：改到的类与 data-slot 都有场景渲染到（列出的 `.cc-notice` 是 `data-slot="cc-notice"` 被当成类名，`[cc-notice]` 渲染到了） |
| V1：纯替换 → f9fdf7ed（子集，`reports/pure-vs-v1`） | 85 个场景：只有行高 `19.6 → 20`（提醒卡、文件卡名、出错框、发送中附件名）与 `16.8 → 17`（代码块头、脚注、时间、类型行），随之下面的内容挪 0.4 / 0.2px | 没有别的属性变化 |
| V3：f9fdf7ed → c0c8d45c | 只有 desktop-coarse（显示复制条）与 desktop-narrow（不再显示）两种环境、28 个场景 | 其余 5 种环境 0 差 |
| V72：c0c8d45c → 6864f9af | 样式只有标签 `div[aui_user-message-root] → +.cc-user-row`，外框是复制条换到气泡那一列；像素差只在宽屏的 `chat-user-hover`（复制钮从气泡左下外侧挪到气泡右下，742–748 像素） | 窄屏、触屏上复制条本来就不显示 |
| V12：6864f9af → b187e690 | 只有 `chat-error` 14 组：出错框 `display block → flex`、`gap 8`，外框是里面那行字的宽 | 0 像素差（行高已在 V1 变）；发送失败、通知没打开两处没有场景，与回复出错同一个常量 |
| V32：b187e690 → 0b6851e2 | 只有 `chat-error` 14 组：`margin-top 8px → 0px`，下面整体上移 8 | 见上第 8 条 |
| V30：0b6851e2 → 2666b128 | 复制钮（回复 435 处、代码块 57 处）：圆角 `8 → 6`、内边距 `4 → 0`、`transition all → background-color, color, opacity`、继承的字号字重变化（按钮里只有图标，看不见）；代码块「复制」图标从继承的正文色变成 ink-3；触屏环境代码块头随 `cc-tap` 变高 | 像素差：宽屏是代码块复制图标的颜色（约 380 像素），手机 / 平板 / 拉窄是代码块头变高带动下面挪位 |
| V70：2666b128 → f5579212 | 滚到底（143 个场景都有这颗按钮，多数时候在底部被藏起来）：箭头 `ink → ink-2`；深色底 `input/30（白 3%） → 表面色`；**阴影从无到有**——原来的 `shadow-[var(--cc-shadow-composer)]` 被 Tailwind 当成阴影颜色、从来没生效，现在是 `.cc-scroll-btn` 的输入框同款 1px 环 + 投影；`transition all → background-color`；到底时 `opacity 0.5`（本来就 `invisible`）→ 1 | 看得见的只有 `chat-scroll-btn`：环与投影、深色底色 |
| V71：f5579212 → 5365f730 | 只有 `compose-uploading`、`image-preview`：`transition all → 颜色类`，其余是按钮里看不见的继承字号 | 0 像素差 |
| V31：5365f730 → 818ad90c | 只有 `send-pending` 14 组：小胶囊（`span.cc-card` + 骨架）换成 `div.cc-attach-card`（骨架缩略图 + 文件名 + 类型） | 落地后的正式气泡同一个样子 |
| V33：818ad90c → 71932df1 | 代码块头与代码（57 处）：圆角 `12 → 10`、行高 `22.75 → 20`、头部底 `黑 2.5% → 叠色 5%`、边线 `黑 5% → 线色 10%`、代码区底 `黑 1.5% → 透明`、语言名 `ink → ink-3` | 深色同理（白） |
| V34：71932df1 → f6ceadf4 | 复制图标 492 处：`animation enter 0.15s ease → cc-pop-in 0.12s 淡出缓动`（已复制那颗 0.2s） | 静止画面 0 差；2 个场景的像素差在那条渐变带，同纯替换 |
| V35：f6ceadf4 → 52294f91 | `compose-uploading`：遮罩 `backdrop-filter blur(1px) → none`、转圈 `spin → cc-spin`（加 `.cc-spinner`）；`image-preview` 等：方块 `transition-property 颜色类 → opacity`（回到 `.cc-compose-tile` 自己的） | 像素差在上传中那块方块（去掉模糊） |
| V36：52294f91 → a97486f6 | 只有 tablet-portrait、desktop-narrow 的 `image-preview` 4 组：预览弹窗从贴满屏宽变成离屏边 16 | 宽屏、手机 0 差 |
| 纯替换 d9cb99bd → 终点 a97486f6，全矩阵（`reports/pure-vs-v36`，代码与 9f524e6f 相同） | 668 / 801 个场景有差异（滚到底按钮在所有对话场景里都有，V70 的样式差到处都是）；用 `~/pearnly-devtools/cc-std-b7/novel.mjs` 扣掉上面各提交子集里出现过的差异种类后，剩 21 种，逐个看过：出错块 `margin-top` 与滚到底圆角只是标签 / 像素比不同的同一项（V32、V70，767.5 宽那组子集没有），`chat-user-hover` 触屏桌面版多出复制钮（V3 + V30）；web-light `sidebar-row-menu` 的组箭头透明度与「···」颜色是悬停残留（前、后各复拍 2 遍 0 差，`shots/re-fin-*`）；desktop-narrow 的 `chat-steps-open` 改前（基线同）点步骤摘要没展开、动作没记失败，改后展开，内容变长、滚到底按钮出现——前、后复拍各 2 遍结果稳定，改的是这个环境里 V3 收起复制条后的布局，步骤卡本身的样式与其他环境一致 | 子集以外的场景没有发现第 7 节以外的变化 |

- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b7/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告（V1、V3、V12、V30–V36、V70–V72）。
- **没有比对的**：悬停在按钮本身上的状态（复制钮、滚到底、附件移除的悬停色与按下缩放）、提示框里的复制钮聚焦、发送失败与「打开通知」没打开时的出错框（同一个常量，没有场景）、真机字体、Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）。

**闸门报数**（`--report`）：81bcd88a 的范围外 641 处 → **493 处**（R4 134 → 109、R5 340 → 267、R6 4 → 1、R7 14 → 13、R8 54 → 30、R9 14 → 8、R10 48 → 34、R11 6 → 4，R12 27 不变）；收紧清单 14 → 18 个文件（加 `components/assistant-ui/` 的 attachment、markdown-text、thread、tooltip-icon-button；follow-up-suggestions 已删），范围内 0 违例。


1. 对话正文里的小字（提醒卡、文件卡、出错框、代码块头）行高 20 / 17（V1）。
2. 你发的消息：电脑上悬停出的「复制」在气泡下方右侧（V72）；手机、平板、CC 桌面版拉窄不显示，**触屏 Windows 上的 CC 桌面版要显示**（V3）。
3. 出错的回复（断网或服务端出错时）：淡红框同侧栏外的出错框一套，紧贴在回复第一块时上面不留 8（V12、V32）。
4. 回复下与代码块的「复制」：圆角 6、悬停底色同侧栏、按下缩一点；**手机上代码块「复制」点按区 44**（V30，要用户手机走）。
5. 往上翻出 ↓：圆钮带一圈细线与投影，深色下是表面色（V70）。
6. 输入框里的附件：右上 × 悬停变红（V71）；悬停方块只变淡、不再叠一层底色；上传中的遮罩不模糊（V35）。
7. 带附件、上传没完成就发送：发送中的气泡里附件是和发出后一样的卡片、缩略图位置在闪，落地时不跳（V31）。
8. 代码块：圆角 10、行高 20、头部浅底与正常线色（V33）；复制后打勾图标轻弹出（V34）。
9. 图片预览：**iPad mini 竖屏与 CC 桌面版拉窄（640–767 宽）离屏边 16**，不再贴满（V36，要平板）；关闭钮的蓝色焦点框照旧（不改）。
10. 回归（应无变化）：打开正在回复的对话停在本轮开头、出 ↓；已结束的停在最新一条；中文 / 泰文输入法选字回车不发送；停止钮图标 14；表格、标题、列表、引用、链接、行内代码样子不变。

**还缺什么**：测试窗口验收（上面几项）；用户手机 / 平板走 V30、V36 与回归第 10 条；合入 main（按「主线集成」变基，本次没做）；不部署。
### B4 做了什么（10-18，`pearnly/cc-std-b4`）

基线 `origin/main` **81bcd88a**（B2 已在 main）；工作树 `.claude/worktrees/cc-std-b4`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。B3、B5、B6、B7 同时在各自工作树里做；本批只改 4.2 列的 7 个源码文件、`globals.css` 的补登变量（4.1 例外）、登记文件和本批涉及的共享测试断言。`tests/test_cc_ccs.py` 的 `AWAITING` 表里，本批用上并删掉的除了 B4 那一组，还有 B3 名下的 `--cc-menu-w-md`（设置、公司页菜单最小宽 176）和 B5 名下的 `--cc-link-offset`（设置里「添加到主屏幕」的下划线离字）——合入时与 B3 / B5 删同一行，按「删掉」合并。

| 50669477 | 比对工具补场景（见下「比对」）：桌面版桥的本机状态（「此电脑 · 连接 / 系统」两页有内容）、池里的电脑与它的「···」菜单、运行记录弹窗、「立即运行」提示条、定时任务 / 公司 / 待处理的加载、出错、空、无结果、待处理当场选展开；`settings-tab2` 期望弹窗里有按钮（H27 手机 44） | 工具 |
| 9fd2fecf | 纯替换一：同值变量（见下） | 纯替换 |
| f03a8b82 | 纯替换二：换共用类、外观相同（见下） | 纯替换 |
| 4d476c4f | V1 字阶；提示条 `.cc-toast`、无结果行 `.cc-empty-line`（同是 14/20） | 画面变化 B |
| 98e6074c | V5（B4 部分）加载卡片 `animate-cc-pulse` | 画面变化 B |
| 9d90a979 | V6 不可用 0.4（开关、选不了的目标卡） | 画面变化 B |
| a3957317 | V10 设置按钮 `.cc-btn`（日志按钮 `.cc-icon-btn`） | 画面变化 A |
| 274a5c31 | V12 出错框 `.cc-alert` | 画面变化 原 C·已定 |
| 0527ebe4 | V14 三个确认类弹窗只用 `.cc-dialog`：圆角 16、标题 22/28、删除确认脚部统一 | 画面变化 原 C·已定 |
| ed103374 | V15 遮罩 `.cc-overlay` | 画面变化 B |
| b95168cb | V17 设置左栏 `.cc-nav-item` / `.cc-section-label` | 画面变化 A |
| 5290ba23 | V19 设置开关 `.cc-switch` | 画面变化 B |
| 09351091 | V20 待处理卡片按钮里的图标不写尺寸 | 画面变化 A |
| 7e286c01 | V21 「交给 CC」去掉图标 | 画面变化 A |
| 0006ac48 | V22 设置里手写的 28 图标钮 → `.cc-row-btn` 24 | 画面变化 A |
| 52e29972 | V36（B4 部分）定时任务表单两列 `sm:` → `md:` | 画面变化 A |
| 7f5d2433 | 收紧清单登记 7 个文件 | 清单 |
| 115cf78e | 比对工具：「此电脑 · 连接」场景把站点文字钉成 `cc.test`（见下「比对」） | 工具 |
| 311b4dff | 比对工具补 `tasks-edit`（打开已有任务的表单；原 `tasks-new` 停在选公司那一步，表单没被拍到） | 工具 |
| （本记录） | 本记录 B4 小节、4.1 例外、第 7 / 8 节 B4 的行；ccs.md 一条 | 文档 |

**纯替换一（9fd2fecf）：同值变量**——间距（`--cc-space-*`、`--cc-dialog-px`）、图标与控件尺寸、层级（普通弹窗 `--cc-z-sheet`、设置遮罩 `--cc-z-modal`、设置弹窗 `--cc-z-modal-top`、删除确认与设置里的菜单 `--cc-z-confirm`）；设置弹窗的宽高与占高（`--cc-settings-w / -w-max / -h / -share`）、左栏宽、内容区内边距、分节上下、值列最宽、关闭钮位置；添加公司的占高 `[--cc-modal-share:var(--cc-add-company-share)]`；公司页输入框最高 `--cc-composer-input-max`、最矮 `--cc-control-xs`、「＋」细线 `stroke-(length:--cc-icon-stroke)`（同 B7 的写法）；定时任务目标卡状态字的三元内联色改成颜色类；组标题 `!px-0 !pt-0` 改 `data-flush`；菜单 `sideOffset={4}` 改 `menuOffset()`、`!min-w-44` 改 `min-w-(--cc-menu-w-md)`（同 B3 的做法：值不变的放纯替换）；公司列表滚到底预取的 `rootMargin` 建观察器时读 `--cc-observe-margin`（2.6，回退常量 `OBSERVE_MARGIN` 与 globals.css 同值由测试核对）。

**纯替换二（f03a8b82）：换共用类、外观相同**——定时任务、添加公司、删除确认三份手抄的弹窗外壳改 `.cc-dialog` + `data-size`（sm 448 / md 512 / lg 672，原来的 `max-w-md / lg / 2xl` 同值）与 `.cc-dialog-head / -body / -foot`（删除确认的脚部值不同，留给 V14）；这一步仍带 `cc-card`，圆角照旧 8（B1 第 4 条的安排），V14 去掉。空状态卡改 `.cc-empty`（图标、按钮里的图标改由类定尺寸）；转圈改 `.cc-spinner`（同 1 秒匀速一圈；卡片按钮里的暂留 `size-(--cc-icon-sm)` 给 V20）；设置里连接状态的内联色改 `.cc-status-line[data-status]` + `.cc-status-dot`，删掉 `STATUS_COLOR`。

**globals.css（4.1 新登记的例外）**：用途层补四个同值变量 `--cc-add-company-share` 0.8、`--cc-run-row-py` 10（运行记录一行上下）、`--cc-target-card-py` 10（表单目标卡上下）、`--cc-task-skeleton-h` 80（定时任务加载卡高），都是现有值·CC 自定。


1. **V3 在 B4 没有要改的**：本批文件里没有 `pointer-coarse:` 一类写法，点按区都走 `cc-tap` 与 B1 的 `:root[data-cc-touch]` 规则（B1 已排除桌面窗口）。V22 给「此电脑 · 连接」公司一行的「···」补了 `cc-tap`，它只在 CC 桌面版里，桌面窗口没有触屏标记，不生效。
2. **V8 在 B4 没有可见变化**：本批 5 处菜单（设置两处、定时任务行、公司列表排序、公司页「···」）原来都是 4，就是主菜单的值，`menuOffset()` 放进纯替换一。
3. **V1 带上提示条与无结果行**：`.cc-toast`、`.cc-empty-line` 的字是 14/20，原来写的 `text-sm` 是 14/19.6，换类就是 V1 这一种变化，不另编号。
4. **设置的日志按钮换 `.cc-icon-btn` 不是 `.cc-btn`**：只有图标，`.cc-btn` 会成 44 宽 32 高的扁按钮；`.cc-icon-btn` 32 方、图标 20（原来 16）、没有外框，手机 44 由 H27 的 `.cc-icon-btn` 一半保住。
5. **设置左栏第一个组标题「设置」照 `.cc-section-label` 带组上间距**（网页 16、桌面版 14；原来 4），不用工具类压标准类（1.3）；第二个「此电脑」原来就是 16。窄屏左栏横排，导航行写 `w-auto`（`.cc-nav-item` 默认 `width: 100%`，横排时每颗会撑满一行）。
6. **V20 实际只剩转圈**：「交给 CC」的 Sparkles 在 V20 先去掉 `size-4`、V21 整个去掉；按「一项一个提交」照编号顺序做。
7. **定时任务的确认框（`Confirm`）主体仍是空的 `<span />`**，`.cc-dialog-body` 照旧留 8 / 8 的空，V14 只统一圆角、标题、脚部，不改内容结构。
8. 不改 `components/ui/button.tsx`：设置页迁完后 Button 只剩 B6、B7 的调用方（B2 第 1 条，B8 删）。

**比对**（`scripts/cc-visual/`；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b4/dist-<名>`，每份都从对应提交 `git archive` 导出构建、不碰工作树；3 条跑道；截图 `…/shots/<名>`，报告 `…/reports/<编号>/report.md`）。机器同时有另外 4 个代理在跑全矩阵，全矩阵 955 张一遍约 49 分钟，所以：基线、纯替换、画面变化做完的终点各拍一遍**全矩阵**；画面变化逐项拍 **B4 子集**（本批组件出现的 30 个场景 × 16 组 = 402 张，一遍约 26 分钟：设置五个页签、待处理 / 定时任务 / 公司列表各自的列表与加载、出错、空、无结果、公司页与会话栏、添加公司、删除确认、知识库编辑、`session-panel`、`chat-pending`），再用全矩阵「纯替换 → 终点」核对子集以外没有别的变化；`tasks-edit`（表单）是做完以后补的场景，用工具副本对每份构建单拍这一个场景（14 组）补比。

| 基线 81bcd88a → 纯替换二 f03a8b82（全矩阵 955 张，`reports/base-vs-pure`；两个纯替换提交合在一起比，纯替换一没有单独拍） | **0 像素差**（只差像素的 7 个场景复拍判为光栅抖动，都是窄屏长对话那条渐变带）；样式差只有下面几种「算出来的值不同、画面相同」 | ① 节点标签：类名换成 `cc-dialog-head / -body / -foot`、`cc-empty`、`cc-spinner`、`cc-status-line / -dot`（标签只认 `cc-*` 类，换类就记一条）；② 转圈 `animation-name spin → cc-spin`（两份关键帧都是转 360°、1 秒匀速）；③ 状态点四角 `3.35544e+07px → 9999px`（`rounded-full` 与 `--cc-radius-full` 的写法不同，8 的圆点都是正圆）；④ 待处理空状态卡 `text-align start → center`、本身的字色 `ink-3 → 继承`（卡里只有一个图标，图标颜色由 `.cc-empty > svg` 给，照旧 ink-3）；⑤ 三个弹窗的 `box-shadow` 序列化不同（原来 `shadow-[var(--cc-shadow-pop)]` 前面带两层透明的 ring，现在直接是同一个弹出阴影）。报告里 18 个有像素差的场景（桌面版三种环境的「此电脑 · 连接」）是工具问题：公司一行写的是 `location.host`，假服务端端口每遍不同、字宽跟着变——115cf78e 把它钉成 `cc.test`，这几个场景前后各重拍（`reports/base-vs-pure-fix`，24 张）：**0 像素差**，样式差只有上面 ① ③ |
| 纯替换二 → V1（子集） | 282 个场景；样式只有 `line-height 19.6 → 20 / 16.8 → 17` 与随之移动的外框，节点标签多了 `cc-toast`、`cc-empty-line`（提示条的 `box-shadow` 序列化同上 ⑤） | 表单（`te-v1`）同样只有行高 |
| V1 → V5（子集） | 只有 `companies-loading`、`tasks-loading` 14 组：`pulse → cc-pulse`、`2s → 1.2s`、缓动、停在首帧透明度 `1 → 0.35` | 静止截图 0 像素差（截图时动画归零），同 B2 的 V5 |
| V5 → V6（子集 + 表单） | 设置「常规」里不可用的通知开关、表单里选不了的目标卡：`opacity 0.5 → 0.4`，没有别的 | 浏览器无界面没有网页推送，通知开关灰着，所以 `settings` 场景照得到 |
| V6 → V10（子集） | 只在设置「连接 / 此电脑 · 连接 / 系统」：按钮左右 12 → 14（触屏 16）、没有投影、底色、过渡、节点标签 `cc-btn`；日志按钮换 `.cc-icon-btn`（无外框、图标 20）；按钮的 `white-space nowrap → normal`、`line-height 19.6 → 21`（`.cc-btn` 不设行高，高度固定 32 / 44，字照样居中） | 手机「下载 CC 桌面版」高 44、左右 16（H27 与 `.cc-btn` 触屏规则），见 `visual/V10/phone-*` |
| V10 → V12（子集） | 只有三个 `*-error` 场景 14 组：出错框多 1 的危险线、上下 8 → 12、`display flex`、间距 8 | |
| V12 → V14（子集 + 表单） | 添加公司、删除确认、定时任务确认框 / 运行记录 / 选公司 / 表单：四角 8 → 16、标题 24/30 → 22/28（字距 `-0.24 → -0.22`）、删除确认脚部上 8 下 16 → 上下 12 带上边线 | |
| V14 → V15（子集 + 表单） | 四个弹窗的遮罩：`enter 0.15s ease → cc-fade-in 0.12s 主缓动`，节点标签 `cc-overlay` | 静止画面 0 像素差 |
| V15 → V17（子集） | 只在设置：导航行左右 10 → 6、图标与字间距 10 → 6、图标 16 → 20（多一层 28 的 `cc-nav-icon`）、选中项不加粗、选中底色换侧栏的；行间 4 → 2；「设置」「此电脑」标题 12px → 13/16、字重 500、上间距照组标题 | 窄屏横排照旧，见 `visual/V17/phone-*` |
| V17 → V19（子集 + 表单） | 设置与表单里的开关：`transition-duration 0.12 → 0.2s`、只管底色，圆点从 `<span>` 换成 `::after`（回弹缓动）、四角写法同上 ③ | **0 像素差**：静止外观一模一样 |
| V19 → V20（子集） | 只在触屏（手机、平板竖屏、桌面版拉窄、触屏网页）：「交给 CC」里的图标 16 → 22、按钮随之宽 6 | 电脑上不变 |
| V20 → V21（子集） | 「交给 CC」少了图标（节点「只在前」svg）、按钮变窄（电脑 86 → 64、手机 134 → 106）：待处理页、公司页与会话栏的待处理卡 | 报告里另有 1 个 `phone-light settings-computers`：那一遍点开的是抽屉账户菜单、设置没打开（动作离群）。v20、v21、v22 各单独重拍 2 次（`reports/recheck-*`）：同一构建两次 0 差、v20 与 v21 0 差，判为 v21 那一遍离群，没导出 |
| V21 → V22（子集） | 只在设置「连接 / 此电脑 · 连接」：「···」「×」28 → 24、四角 8 → 6、颜色与过渡换 `.cc-row-btn`，节点标签 `cc-row-btn cc-tap` | 触屏上 `cc-tap` 照旧 44 |
| V22 → V36（子集 + 表单） | 子集 0 差（表单不在子集里）；表单只有平板竖屏、桌面版拉窄 4 组：两列 → 一列 | 宽屏、手机不变 |
| 纯替换二 → 终点 52e29972（全矩阵 955 张，`reports/pure-vs-v36`） | B4 子集以外有差异的只有 32 个：宽屏 8 组的 `chat-running`、`chat-reply-hover`、`chat-user-hover`、`motion-reduce`——这几段对话没有月份，会话栏的待处理卡全列，差的正是 V1 的行高和 V21 去掉的图标，没有别的样式种类 | 光栅抖动 9 个（同上那条渐变带） |

- **画面变化的前后图**：`~/pearnly-devtools/cc-std-b4/visual/<编号>/<环境>-<深浅>/<场景>-前 / -后 / -差异.png`，每个目录带一份比对报告；表单另放 `V1-表单`、`V6-表单`、`V14-表单`、`V19-表单`、`V36`。V8、V3 在 B4 没有画面变化（见上第 1、2 条），没有目录。
- **H27（手机设置弹窗按钮点按区 44）**：`settings-tab2` 期望弹窗里有按钮，手机、平板竖屏、桌面版拉窄与触屏网页四种环境的「下载 CC 桌面版」在 V10 前（`[data-slot="button"]`）、后（`.cc-btn`）都是 44 高；「此电脑 · 连接」的「重新查找」「更换…」在桌面版里 32（桌面窗口没有触屏标记），桌面版拉窄到手机变体时 44。
- **没有比对的**：悬停、聚焦以外的交互状态（开关拨动的回弹、遮罩淡出），真机字体，Qt WebEngine 与 WSL2 的渲染（5.2 已知局限）；定时任务的「连接 MR.ERP / CC 桌面版」弹窗、MR.ERP 菜单弹窗、添加公司有候选行时（假服务端没有这几个接口的数据），只由代码审查与同一套类保证。

**闸门报数**（`--report`）：范围外 641 → **368** 处（R4 134 → 78、R5 340 → 184、R7 14 → 3、R8 54 → 32、R9 14 → 12、R10 48 → 32、R11 6 → 3、R12 27 → 20，R6 4 不变）；收紧清单 14 → 21 个文件，范围内 0 违例。


1. 设置（网页、CC 桌面版、手机）：左栏同侧栏导航行（图标 20、选中色同侧栏、不加粗）、「设置」「此电脑」组标题 13/16（V17）；「连接」「此电脑 · 连接」「系统」里的按钮（V10）、「···」「×」24（V22）、状态点；开关拨动是回弹（V19）；不可用的开关更淡（V6）。**手机、平板竖屏要用户手机走**：「连接」「下载」按钮点按区 44（H27），左栏横排能左右滑、选中项看得出。
2. 定时任务（网页、CC 桌面版）：列表行的字 14/20、12/17（V1），加载卡片呼吸 1.2s（V5）、出错框（V12，可断网后刷新），「立即运行」后的提示条；点任务打开表单：圆角 16、标题 22/28（V14）、遮罩 120ms（V15），MR.ERP 那张选不了的卡更淡（V6）；**iPad mini 竖屏或把 CC 桌面版拉窄到 640–767**：表单一列（V36）。运行记录、删除确认框同上。
3. 添加公司、删除对话确认（三种变体）：圆角 16、标题 22/28；删除确认的「取消 / 删除」上面一条线、上下 12（V14）；遮罩（V15）。
4. 待处理（三种变体）：「交给 CC」没有图标（V21）；手机上「确认」「立即推送」忙时的转圈 22（V20，**要用户手机**）；出错框（V12）。
5. 公司列表、公司页（三种变体）：加载卡片（V5）、出错框（V12）、空状态、无结果行。
6. 回归（应无变化）：菜单离触发器 4（本批所有菜单，V8）、设置弹窗在键盘弹出时照旧不超出可见区、手机长按菜单、Esc / × / 点遮罩关弹窗后焦点回原处、减少动效时遮罩不播。

**还缺什么**：测试窗口验收（上面几项）；按「主线集成」变基到最新 `origin/main`（B3、B5 合入后 `AWAITING` 与 `test_cc_*` 会有同一行的冲突，见本节开头）、推分支、等 Development checks、快进合入 main（本次只推了分支）；设置弹窗本身（`.cc-modal` 外层）没有改成 `.cc-dialog`：它是整窗两栏、尺寸自定（`--cc-settings-*`），外观值已与 `.cc-dialog` 相同，B8 收口时如要合并再看。

**B3–B7 合入**（10-19 凌晨，开发窗口）：五批由五个代理在各自工作树并行施工，各自全闸门与分支 CI 通过。先在 `pearnly/batch-1019` 连同数字会计员第 3 期一起集成、挂测试机（8caf84b7）；测试窗口验收 B3–B7 在 CC 桌面版深浅色全部通过（记录 `~/pearnly-devtools/test-window/round-b3b7/B3-B7-第3期-测试窗口结果.md`），第 3 期场景 1–3 没测到，所以另从 main 串 `pearnly/cc-std-b3b7` 只合 B3–B7（前端与测试同 8caf84b7，差的只是第 3 期的内容），第 3 期留在自己的分支等验收。

- 登记文件（本记录、`ccs.md`、`frontend-tokens-strict.txt`）两边都留；收紧清单取并集按字母序；第 8 节表去掉各批改前的旧行。
- 共享测试：每条断言按它读的文件所属批次取写法；`AWAITING` 表各批接上的都删，只剩 `--cc-control-nested`。
- `globals.css`：B5、B7 各补登了同值的 `--cc-alert-lines`，只留一份，注释写两批共用。
- 第 3 期合入时要做的（`pearnly/batch-1019` 已做过，照搬）：月结台账「全部推送」组标题（B5 的文件）与待处理推送行 `ProposalRow`（B4 的文件）改成所在文件的标准写法、补回 `LoaderIcon` 导入、第 3 期测试断言改新写法、待处理「推送中」转圈计数 3 → 4；这两处验收时一起看。
- 合完范围外报数 3 处 / 1 个文件（`ui/button.tsx`，留 B8）。
- 没验到、部署前请用户走：网页端深浅色；手机 / 平板 / 触屏各项（各批「给测试窗口的验收项」里标了手机的）；触屏 Windows 上用户消息的复制条。

### B8 做了什么（10-19，`pearnly/cc-std-b8`）

基线 `origin/main` **4277e95c**（B2–B7 都已在 main 并上线）；工作树 `.claude/worktrees/cc-std-b8`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`）。按 4.2「B8 收口」做，纯删除与写法收口，画面不变。

| 提交 | 内容 | 类 |
| :- | :- | :- |
| 63903061 | 闸门 R9：`R9_FAMILIES` 里带轴的 `scale-[xyz] / rotate-[xyz] / skew-[xy]` 写在不带轴的前面，`scale-x-(--x)` / `rotate-y-(--x)` 不再把轴当成值误判（B3 记的）；测试补带轴的拦 / 放用例（修前那条用例确实失败） | 闸门 |
| e271aba3 | CC 桌面版「还原」图标改回 `scale-x-(--cc-mirror)`（B3 临时写的 `[scale:var(--cc-mirror)_1]`） | 纯替换 |
| bdb89934 | 删 `components/ui/button.tsx`：B4 / B6 / B7 迁完后全仓没有调用方（B2 第 1 条）；H27 手机点按区选择器去掉它的 `data-slot="button"`，只认 `.cc-btn` / `.cc-icon-btn` | 纯删除 |
| bb5a808b | 删 `lib/hover-scroll.ts` 的 `cssMs`、`lib/company-panel.ts` 的 `cssPx` 两处再导出；`test_cc_session_panel.py`、`test_cc_ccs.py` 改从 `lib/css-var.ts` 导入，并断言两处已不再导出 | 纯删除 |
| e5d07a4e | 依赖与 globals.css 的旧名（见下） | 纯删除 |
| 0fff7494 | 删 `scripts/frontend-tokens-strict.txt`，R4–R15 全仓失败（见下） | 闸门 |
| 59098ee0 | `components/NOTICE.md` 改写（保留 MIT 声明）；删 `frontend/assistant/components.json` | 文档 / 删除 |
| （本记录） | 本记录 B8 小节与进度表；`ccs.md` 收口（`scripts/README.md` 的闸门一行随 0fff7494 改） | 文档 |

**删了什么（e5d07a4e）**，都先 grep 与构建确认没人用，删后由测试守住：

- 依赖：`tw-animate-css`（最后的进出场在 B2 / B3 / B4 换成 `.cc-overlay` / `.cc-pop`）、`tw-shimmer`（从没用过），以及 **`class-variance-authority`**——记录没列，它只给删掉的 Button 用，Button 删了它就成了孤儿依赖，一并删（`npm uninstall`，锁文件只少这三个包，`tailwindcss` 重新标回 dev）。`globals.css` 删 `@import "tw-animate-css"`。
- `@theme` 与 `:root, .dark` 里组件改名后没人用的 shadcn 别名 16 个：`card / popover / primary / secondary`（各带 `-foreground`）、`input`、`accent-foreground`、`sidebar-primary / -accent / -border / -ring`（带 `-foreground` 的一并）；留下的 `background / foreground / muted / muted-foreground / accent / destructive / border / ring / sidebar / sidebar-foreground` 都还有组件类或 `@apply` 在用。
- `@theme` 的 `--text-xs / sm / base`（V1 迁完后组件里一处都没有）与 `--text-4xl`（与 3xl 同值，B3 删掉了唯一的 `md:text-4xl`）：不删的话闸门 R6 照样放行 `text-sm`，1.2 表「不允许」的这几档就拦不住。
- `--cc-control-nested`（B2–B7 都没人用），`AWAITING` 表随之为空并删掉，测试改成「每个 `--cc-*` 都有人用」。
- 减少动效例外里的 `.animate-spin`（转圈都是 `.cc-spinner`；`:not()` 的权重由 `.cc-spin i` 决定，不变）。
- **构建核对**：清理前后两份构建的 CSS 逐条规则比，只少了上面这些名字的定义、tw-animate 的 `@property --tw-enter-* / --tw-exit-*` 等、Button 独有的工具类（`transition-all`、`disabled:opacity-50`、`has-[>svg]:px-2.5` …）和 tw-animate 带来的 `.running / .paused`、Button 文本带出的 `.outline`（组件里没有元素用这几个类），H27 两条换成新选择器，再没有别的变化（脚本 `~/pearnly-devtools/cc-std-b8/tools/css-rules.py`）。
- 新测试（`test_cc_ccs.py`）：`test_shadcn_aliases_left_are_all_in_use`（留下的每个别名都有人用、`:root` 里的都有 `@theme` 接，删掉的不再出现）、`test_dependencies_left_by_the_migration_are_gone`（三个依赖不在 package.json 与锁文件、globals.css 不再引用、没有 `animate-spin`）、`test_tailwind_utilities_resolve_to_ccs` 补「xs / sm / base / 4xl 不在 `@theme`、组件里不用」。反例核对过：往 `@theme` 加回一个没人用的别名，测试当场失败。

**闸门全仓失败（0fff7494）**：`check-frontend-tokens.py` 去掉 `STRICT_LIST`、`strict_list()`、`unmatched_entries()`、`--strict-list` 与报数里的「范围内 / 范围外」两栏，`scan_strict(src)` 只返回 `(路径, 行号, 规则, 片段)`，R4–R15 任何一个受检文件有违例就退出码 1；`--report` 改成按规则、按文件的全仓报数。`test_cc_frontend_gate.py` 的收紧范围测试改成：违例在哪个文件都失败、不再有「范围外」、传 `--strict-list` 报错；清单文件不存在、脚本里没有清单入口、仍没有 `IGNORE` 与行内禁用；对真实 `src` 全仓 0 违例。合入前范围外是 3 处（都在 `ui/button.tsx`），删掉后全仓 0 处。

**与方案不同、按依据定的几处**：

1. **Button 直接删，不按标准改**：全仓没有任何调用方（`grep` 只剩 globals.css 的一句注释与测试），按 B2 第 1 条纯删除；测试里读 button.tsx 的三处改成断言它已删、没有文件再导入它。
2. **H27 选择器去掉 `data-slot="button"`**：没有元素再带这个属性（比对的覆盖检查：「前」那一遍也没渲染到）。第二条从 `:is([data-slot][data-size^="icon"], .cc-icon-btn)` 改成 `.cc-icon-btn` 后权重低了一档，核对过 globals.css 里没有夹在中间、会改写弹窗里图标钮 `min-width` 的规则，全矩阵 0 差。
3. 记录没列的另删了 `class-variance-authority` 与 shadcn 别名、`text-xs / sm / base`（上面「删了什么」）：都是本批删掉 Button 或前面各批改名后剩下的没人用的旧名，属于 4.2「删掉组件改名后已无人引用的旧变量名」。Claude 自己的 `@theme` 颜色名（`brand-*`、`hover`、`overlay` 等）里有几个组件暂时没用，它们是标准层可用的名字、不是旧名，不删。
4. 设置弹窗外层（`.cc-modal`）没有改成 `.cc-dialog`（B4 记的「B8 如要合并再看」）：它是整窗两栏、尺寸自定，合并会动布局，不是纯删除，不做。
5. 选做的「重复工具类组合报警」不做（开发窗口定）。

**比对**（`scripts/cc-visual/`，全矩阵 = 7 种环境 + 767.5 宽 × 浅深 = 16 组、1001 张，本机只有本批在跑，7 条跑道，每遍约 28 分钟；产物 `vite build --outDir ~/pearnly-devtools/cc-std-b8/dist-<名>`，截图 `…/shots/<名>`，报告 `…/reports/<名>/report.md`）：

| 比的两遍 | 结果 | 说明 |
| :- | :- | :- |
| 基线 4277e95c → 终点 59098ee0（`reports/base-vs-after`） | **0 样式差、0 像素差** | 报告列出 10 个「只差像素、样式 0 差」的场景，工具复拍判为光栅抖动（窄屏 / 平板长对话输入框那条渐变 7 个，平板 `width-wide`、`text-small`、泰文欢迎页各 1 个）。照 B2 的办法另把这 10 个场景前、后各单独复拍 2 次（`shots/re-base-1 / -2`、`re-after-1 / -2`，`reports/recheck-1 / -2`）：样式 0 差，每个场景前、后都拍出一模一样的图（md5 一致的一对），出现的另一种图在前、后两边都有，判为抖动。`web-coarse` 的 `tooltip-copy` 两边都是「动作没做完」（触屏网页顶栏盖住代码块「复制」，基线就这样），不是本批引入。覆盖检查：改到的 7 个类 / data-slot 渲染到 6 个，没渲染到的是删掉的 `data-slot="button"`（「前」那一遍同样没有，证实没人用） |
| 「还原」图标（场景外：CC 桌面版窗口最大化） | 0 差 | 合成节点核对（`tools/synth-check.mjs`，两份构建的 CSS 各放进空白页，桌面版 / 网页 / 手机 × 深浅）：计算出的 `scale` 都是 `-1 1`、外框一致；反例（去掉镜像类）能核出差异 |

**闸门报数**：范围外 3 处 / 1 个文件 → **全仓 0 处**（清单删掉，不再分范围内外）。本地全闸门（`~/pearnly-devtools/gate.sh`，照 development.yml）105 步全过；`check-architecture.py` 0 违例；`check-copy.py --report` 新增 / 改动 / 删除文案都是 0。

**还缺什么**：合入 main（本次只推了任务分支，按「主线集成」由开发窗口变基、快进合入）；部署随下一批。B8 无画面变化，测试窗口只需回归：设置弹窗里的按钮手机点按区仍是 44（H27）、减少动效时按钮里的转圈照常转、CC 桌面版最大化后「还原」图标仍是两个方框、前面那个在左下。

## 0. 结论

1. **标准只有一个来源：`globals.css`**。`ccs.md` 只写规则和变量名，不抄数值。组件 className 只写布局，外加变量写法（`p-(--cc-space-12)`、`size-(--cc-icon-sm)`）。同一组外观出现两处以上，就收成一个 `cc-*` 类。
2. **Tailwind 自带的 4px 数字档（`p-3`、`h-8`、`gap-1.5`）和容器档（`max-w-md`）不算标准**。原因：它们与设计系统无关，10、14、30、56 这类不在实测里的值就是这样混进来的。全局 `--spacing` 不改，否则所有数字类会一起变。
3. **要补三样东西**：
   - 刻度层：间距、控件、图标、层级、时长、透明度、字重；
   - 用途层：弹窗、对话、菜单、设置、抽屉等；
   - 约 20 个共用类。
   字号在 `@theme` 补上 Claude 实测的字阶（caption 12/17 … page 24/31）。
4. **闸门新增 12 条判据（R4–R15）**，R1–R3 现行判据不变。上线时先用「收紧范围」：已迁移完的文件按新判据算失败，范围外只报数不失败。每批把自己的文件加进范围，最后一批删掉范围文件，全仓按新判据失败。全程没有忽略清单。
5. **共 9 批（B0–B8），同一个源码文件只出现在一批里**（登记过的例外见 4.1）：
   - B0 现在就能开工，不碰 `src`；
   - B1 起要等 `cc-drag-perf`、`cc-scroll-fix` 合入；
   - B3、B4、B5 可以并行；
   - B6 跟在 `cc-drag-perf` 后面，B7 跟在 `cc-scroll-fix` 后面。
6. **纯替换的提交要证明画面不变**：构建前后各跑一遍，对比「计算样式快照」和「逐像素」两样，范围是 7 种环境 × 浅深色 × 约 40 个场景（含根节点状态和泰文）。有画面变化的改动单独提交，列进第 7 节，经测试窗口验收。
7. **第 7 节不再有待用户拍板的项**。原方案的 C 类由开发窗口按依据定稿：有 Claude 实测或 ccs 依据的照标准做；没有参照的保留现值，登记为「现有值·CC 自定」。B 类（亚像素、时长）照标准直接做，验收时一起看，不单独请用户确认。

## 1. 标准是什么

### 1.1 分层（都在 `globals.css`）

| 层 | 名字 | 内容 | 能不能改 |
| :- | :- | :- | :- |
| 官方 | `--ccs-*` | Anthropic 公开的样式变量 | 原样照录，不改 |
| 实测 | `--code-*` | Claude 设计系统实测（[设计文档](2026-10-16-cc-shell-design.md)第 2 节） | 由 `tests/test_cc_ccs.py` 逐项比对，不改 |
| 刻度 | `--cc-space-*`、`--cc-control-*`、`--cc-icon-*`、`--cc-z-*`、`--cc-dur-*`、`--cc-radius-*`、`--cc-text-*`、`--cc-weight-*` | 组件可用的全部档位 | 只引用上面两层或注明出处 |
| 用途 | `--cc-row-*`、`--cc-menu-*`、`--cc-dialog-*`、`--cc-thread-*` … | 有业务含义、可能按变体切换的值 | 优先引用刻度层；不在刻度上的值必须注明出处 |
| 运行时 | `--cc-vvh`、`--cc-vvtop`、`--cc-marquee-x`、`--cc-marquee-dur`、`--cc-modal-share` … | 由脚本或组件在运行时写入 | 在 globals.css 登记默认值，注释写「运行时写入」和写入方 |
| 变体 | `:root[data-cc-variant=web\|desktop\|phone]`、`:root[data-cc-touch]` | 只覆盖用途层变量 | — |

**出处标注只有四种**（写在变量同一行的注释里，闸门检查）：

- 实测：引用 `--code-*`；
- 外壳实测：截图量出来的，例如抽屉、手机圆钮；
- 系统值：Windows 窗口按钮 46、iOS 安全区；
- 现有值·CC 自定：没有 Claude 参照，照现值登记，等有参照再改。

另有两类注释不算出处、但同样要写：「运行时写入」（运行时层）、「照搬宣传页」（形象动画，见 2.1）。

### 1.2 组件里怎么写（闸门按这张表判）

| 类别 | 允许 | 不允许 |
| :- | :- | :- |
| 布局 | `flex`、`grid`、`items-*`、`justify-*`、`overflow-*`、`relative/absolute/fixed`、`shrink/grow`、`truncate`、`order-*`、`hidden`；尺寸关键字 `0 / full / auto / fit / min / max / none`；分数 `1/2`；`min-w-0` | — |
| 尺寸 `size/w/h/min-*/max-*` | `-(--cc-*)`；合规的任意值 | `h-8`、`size-4.5`、`max-w-md / 2xl / 3xl`、`w-56` |
| 间距 `p*/m*/gap*/space-*/inset*/top…/start/end/translate-*/scroll-m*` | `-(--cc-*)`（可带负号）、`0`、`auto`、分数 | 所有 Tailwind 数字档（含 `px`）、`-mt-1`、`!px-0` |
| 字号 | `@theme` 接过的名字：官方 `text-lg/xl/2xl/3xl`（文章与文档标题），实测 `text-caption / footnote / group / panel / body / subhead / prose / title / page`；按变体切换的用 `text-(length:--cc-*)` | `text-xs / sm / base`（迁完后退出组件，见 V1）、`text-4xl`（和 3xl 重复，删掉）、`text-[0.9em]` |
| 文字排版关键字 | 对齐 `text-center / left / right / justify / start / end`；换行与溢出 `text-wrap / nowrap / balance / pretty / ellipsis / clip` | — |
| 行高 | 跟字号成对给出；单独写只能 `leading-(--cc-*)`；`leading-none` | `leading-snug / relaxed / tight / normal` 和数字 |
| 字重 | `@theme` 接过的 `--font-weight-*` 名字（B1 接到 `--cc-weight-*`）；`font-(--cc-*)` | 没接过的字重名、`font-[550]` |
| 颜色、圆角、阴影 | 现行判据不变 | 现行判据不变 |
| 层级 | `z-(--cc-z-*)`、`z-0`、`z-auto` | `z-10`、`z-50`、`z-[80]` |
| 动效 | `transition`、`transition-colors / opacity / transform / shadow`（默认时长和缓动接到变量）；`transition-[属性名,…]`（方括号里只有属性名，不带时长）；`duration-(--cc-dur*)`、`delay-(--cc-*)`；`ease-cc / ease-cc-fast / ease-cc-out`；`@theme` 接过的 `animate-*` | `transition-all`、`duration-200`、`ease-in / out / linear / in-out`；tw-animate 的 `animate-in / out / fade-* / zoom-* / slide-*`，改用 `.cc-overlay` 和 `.cc-pop`；`animate-spin / pulse`，改用 `.cc-spinner` 和 `Skeleton` |
| 透明度、描边、模糊、边线宽、下划线偏移、变换、环与分隔 | `opacity-0 / 100`；其余只用变量 | `opacity-50`、`stroke-[1.5px]`、`backdrop-blur-[1px]`、`border-t-2`、`underline-offset-2`、`scale-90`、`active:scale-90`、`-scale-x-100`、`rotate-45`、`ring-1 / 2`、`outline-2`、`divide-y-2`、`line-clamp-2` |
| 任意值 `[…]` | 只能由这些组成：`var(--…)`、`calc / min / max / clamp / minmax`、`env(safe-area-inset-*)`、`0`、`100%`、不带单位的整数（如 `*2`、`*-1`）、`fr` 单位（如 `1fr`）；任意属性 `[--cc-x:var(--cc-y)]` | 带单位的数（px / rem / em / % / vw / ms，`100%` 除外）、小数（`0.8`、`0.92`）、`2rem`、`minmax(20%,1fr)` 里的 `20%`（改用变量） |
| `!` 强制 | — | 尺寸、间距、字号、层级、动效类上的 `!`，前置（`!px-0`）和 Tailwind v4 后置（`p-0!`）都拦（说明缺档位，应先补档） |
| 断点与输入设备变体 | `md:`、`max-md:`（= 768，与 `hooks/use-mobile.ts` 同一条查询文本）；触屏用 `in-data-[cc-touch]:` 或 CSS 类 | `sm:`、`lg:`、`xl:`、`2xl:`；`pointer-fine / pointer-coarse / any-pointer-*`、`hover-none` 这类变体；容器查询变体（`@md:`、`@max-md:`、`@[480px]:`、`@lg/main:`，`@md` 是 28rem 的容器档、不是 768；工具类 `@container` 本身不算）；能用 `data-cc-variant` 的优先写在 CSS 里 |
| JSX 属性 | 从 `ui/menu.tsx`、`ui/tooltip.tsx`、`ui/drag.tsx` 导出的值（使用时从 CSS 变量读，见 2.6） | `sideOffset / alignOffset / collisionPadding / delayDuration` 写非 0 的数；svg 的 `width / height / strokeWidth` 写数；`size={16}`；`zIndex` 写数 |
| 内联 style | `var(--…)`；运行时量出来或来自数据的值（`${w}px`、文档图片宽高、拖动宽度） | 字面数值的长度、透明度、层级、时长（`opacity: 0.5`、`${level * 1.5}em` 里的 1.5em） |

补充三条：

- 变量写法只能引用 `--cc-*`，而且这个名字必须在 `globals.css` 里定义过（运行时层登记的默认值算定义）。组件里不直接用 `--code-*` 和 `--ccs-*`。
  「变量写法」包括 `-(--x)` / `-(length:--x)`、方括号任意值里的 `var(--x)`、内联 style 里的 `var(--x)`：引用别的名字（`w-(--sidebar-width)`、
  `max-w-[var(--thread-max-width)]`、`style={{ width: "var(--sidebar-width)" }}`）按所在的族记（尺寸 R4、间距 R5、字号行高 R6、层级 R7、动效 R8、
  其他数值类 R9、字重 R15，内联 style R12）；同时写了数（`calc(var(--thread-max-width)+2rem)`）的另记 R10。目前组件里没有受管工具类引用 `--radix-*`，
  不设具名例外；以后确需放行 Radix 这类运行时变量，先在这里写明再在脚本里列具名前缀，不整体放开。
- **整串是一个长度的字符串**（`"3rem"`、`"200px"`、`"300ms"`、`"calc(… 2rem …)"`）按 R12 拦，不论用在哪里；值为 0 的（`"0px"`、`"0ms"`）放行，与内联 style 的 0 同一口径。
  `IntersectionObserver` 的 `rootMargin` 这类**不是样式的 JS 长度也照拦**，合规写法见 2.6。同一个字面值已经按内联 style（`style={{…}}`、`.style.x =`、
  `setProperty`）或 svg 宽高记过的，不再重复记。
- **`globals.css` 本身也要守规矩**：
  - `--*` 变量的定义可以写数，但 `--cc-*` 直接写数时，同一行要有出处注释；
  - 普通声明里，下列属性只能写 `var()` 或关键字（`0 / auto / none / inherit / 100% / 50%`）：尺寸、间距、字号、行高、字重、层级，以及 `calc()` 里的长度；
    简写同样管：`transform` 里 `translate / translateX / translateY / translateZ / translate3d` 的参数按间距判（`scale()`、`rotate()` 不判），`flex` 带单位的那一项（flex-basis）按尺寸判，`font` 里的字重、字号、`/行高` 都按各自的规则判；
  - 动效：`transition`、`animation` 的简写和长写（`*-duration`、`*-delay`、`*-timing-function`）里的时间只能写 `var()`，缓动只能写 `var()`，不许 `ease-in-out`、`linear` 这类关键字；
  - `@media` 的宽度条件只许 `(width < 768px)` / `(width >= 768px)` 这一条（见 2.1「断点」）；
  - `@apply` 的内容按组件规则（R4–R12、R15）判；
  - `@keyframes` 的内部和 `@font-face` 不查。

### 1.3 写法原则（写进 ccs.md）

- 外观放在 `cc-*` 类里，布局放在组件里。组件里的工具类不用来压标准类：需要用 `!` 或覆盖类去纠正时，先补档位或补变体。
- 刻度层以外的值，只能经用途层变量进入组件，并注明出处。没有 Claude 参照的值照现值登记，不为凑整挪到相近的刻度。
- 只做舒适档，不加紧凑档。
- 拷进来的第三方组件（assistant-ui、shadcn）要先改写到标准（`cc-*` 类和变量）才能进 `src`，不用命令整份重新生成（B8 同步改 `components/NOTICE.md`）。

## 2. 要补的标准

### 2.1 刻度层（B1 新增，只引用已有来源）

**间距** `--cc-space-*`（按像素命名，迁移时机械替换、审查时一眼能看懂）：

| 名 | 值 | 来源 |
| :- | :- | :- |
| `--cc-space-2` | 2 | `--code-row-gap`，实测 |
| `--cc-space-4` | 4 | `--code-menu-pad` / `--code-menu-sep-gap`，实测 |
| `--cc-space-6` | 6 | `--code-pad-1` |
| `--cc-space-8` | 8 | `--code-gap-1` / `--code-pad-2` |
| `--cc-space-12` | 12 | `--code-gap-2` / `--code-pad-3` |
| `--cc-space-16` | 16 | `--code-gap-3` / `--code-pad-4` |
| `--cc-space-24` | 24 | `--code-pad-5` |
| `--cc-space-28` | 28 | `--code-gap-4` |
| `--cc-space-40` | 40 | `--code-gap-5` |

- 10、14、20、30、36、56、112 这些不在刻度上的值，只能经用途层变量使用，见 2.2。
- 已有的 `--cc-list-gap`、`--cc-attach-pad`、`--cc-row-fade`、`--cc-marquee-fade` 保留用途名，改成引用刻度层。

**控件高**：

| 名 | 值 | 来源 |
| :- | :- | :- |
| `--cc-control-2xs` | 20 | `--code-panel-btn`，实测 |
| `--cc-control-nested` | 22 | `--code-control-nested`，实测 |
| `--cc-control-xs` | 24 | `--code-control-xs` |
| `--cc-control-sm` | 28 | 已有 |
| `--cc-control` | 32 | 已有 |
| `--cc-control-md` | 36 | `--code-list-search-h` |
| `--cc-control-lg` | 40 | `--code-control-lg` |
| `--cc-tap` | 44 | 已有 |

`--cc-row-ctl`、`--cc-chin-btn`、`--cc-list-btn`、`--cc-card-btn-h`、`--cc-step-h` 保留用途名，改成引用 `--cc-control-xs`；`--cc-mbtn-size`、`--cc-card-btn-h-phone` 改成引用 `--cc-tap`。

**图标**：

| 名 | 值 | 来源 |
| :- | :- | :- |
| `--cc-icon-2xs` | 12 | `--code-group-chevron` / `--code-step-chevron`，实测 |
| `--cc-icon-xs` | 14 | 现有值·CC 自定（组件 4 处、globals 6 处） |
| `--cc-icon-sm` | 16 | 已有 |
| `--cc-icon-md` | 18 | 现有值·CC 自定（输入框「＋」、发送、胶囊、文件卡） |
| `--cc-icon` | 20 | 已有 |
| `--cc-icon-xl` | 32 | 现有值（空状态） |
| `--cc-icon-2xl` | 40 | 引用 `--cc-control-lg`（文件预览） |
| `--cc-icon-stroke` | 1.5 | 现有值 |

手机卡片按钮图标 22 登记为用途变量 `--cc-card-btn-icon-phone`，出处是外壳实测。

**字号**（`@theme` 补上，全部引用 `--code-text-*` 实测值）：

| Tailwind 名 | 字号 / 行高 | 引用 |
| :- | :- | :- |
| `text-caption` | 12 / 17 | `--cc-text-caption` |
| `text-footnote` | 13 / 17 | `--cc-text-footnote` |
| `text-group` | 13 / 16 | `--cc-group-font` / `--cc-group-lh` |
| `text-panel` | 13 / 19 | `--cc-panel-font` / `--cc-panel-lh` |
| `text-body` | 14 / 20 | `--cc-row-font` / `--cc-row-lh` |
| `text-subhead` | 15 / 20 | 新增 `--cc-text-subhead`，引用 `--code-text-heading` |
| `text-prose` | 16 / 24 | 新增 `--cc-text-prose`，引用 `--code-text-prose` |
| `text-title` | 22 / 28 | 新增 `--cc-text-title`，引用 `--code-text-title`（弹窗标题，见 V14） |
| `text-page` | 24 / 31 / 500 | `--cc-text-page*` |

- 官方 `text-lg / xl / 2xl / 3xl` 保留，用于文章和文档里的标题。
- 删掉 `--text-4xl` 这一档：它与 3xl 同值（都接 `--ccs-heading-3xl-*`），`assistant.tsx:129` 的 `md:text-4xl` 因此不起作用。
- `--cc-text-heading` 名字与 `text-subhead` 容易混，在注释里写明它是官方 heading-md 16、用于设置小标题。

**字重**：新增 `--cc-weight-regular / medium / semibold / bold`（400 / 500 / 600 / 700），在 `@theme` 接成 `--font-weight-*`。实测出处：页面标题 500（设计文档 2.2）、卡片按钮 500（ccs.md）；600 / 700 是弹窗与 Markdown 现有值·CC 自定。组件里现有约 49 处 `font-medium / semibold / bold` 写法不变，只是名字从「Tailwind 默认」变成「接过的档」。

**层级** `--cc-z-*`（按现值定档，替换后画面不变；提示框那一档除外）：

| 名 | 值 | 现在谁在用 |
| :- | :- | :- |
| `--cc-z-raise` | 1 | 预览关闭钮 |
| `--cc-z-float` | 10 | 滚到底按钮、侧栏容器 |
| `--cc-z-bar` | 20 | 分隔条、手机顶栏 |
| `--cc-z-scrim` | 30 | 浮动会话栏、抽屉遮罩 |
| `--cc-z-sheet` | 50 | 手机会话栏、手机结果面板、普通弹窗和遮罩 |
| `--cc-z-drag` | 55 | 拖动浮层（`drag.tsx` 的 `OVERLAY_Z`） |
| `--cc-z-menu` | 60 | 菜单、拖文件提示 |
| `--cc-z-modal` | 70 | 设置遮罩、toast |
| `--cc-z-modal-top` | 71 | 设置弹窗、安装引导 |
| `--cc-z-confirm` | 80 | 删除确认、设置里的菜单 |
| `--cc-z-tooltip` | 90 | 新增：提示框要盖过菜单（现在是 50，见 V7） |

**时长与缓动**：

- 已有 `--cc-dur-instant / fast / dur / slow / pop`，即 60 / 120 / 200 / 300 / 450（引用 `--code-dur-1…5`）。
- 新增，都照现值登记：`--cc-spin-dur` 1s（按钮里转圈）、`--cc-skeleton-dur`（现在 2s，见 V5）、`--cc-bob-dur` 1.4s（加载页形象上下浮）、`--cc-dots-dur` 1.2s 与 `--cc-dots-step` 0.3s（`.cc-spin` 四点转圈和它的错开延时）、`--cc-tooltip-delay` 0（Claude 未测）。
- 形象动画（`globals.css` 2229–2241 行，尾巴 2.6s、铃铛 2.2s、举牌与打勾 5s、笑 8s、眨眼 5s，`ease-in-out` / `ease-out`）登记成 `--cc-mascot-*-dur` 和对应缓动变量，注释「照搬宣传页」，节奏不改。10-18 起不再无限循环：轮数登记成 `--cc-mascot-*-runs`（CC 自定，最多 10 秒），见[静置动效记录](2026-10-18-cc-idle-animations.md)。
- `--cc-results-in` 240ms 和 `--cc-dur-drawer` 380ms 不在时长阶上，照现值保留，补出处注释：前者为现有值·CC 自定，后者为外壳实测。不挪到阶上。
- `@theme` 补上 `--default-transition-duration: var(--cc-dur-fast)` 和 `--default-transition-timing-function: var(--cc-ease)`（V2）。
- **不登记** `--cc-shimmer-dur`：src 里没有任何 shimmer 用法，B1 删掉 `@import "tw-shimmer"`（globals.css:3）、`--animate-shimmer`、`shimmer-sweep` 关键帧和没人用的 `collapsible-down / up` 关键帧。

**透明度**：新增 `--cc-disabled-opacity` 0.4（实测：菜单不可用项为 40%，ccs.md 菜单一条）。现在 0.35、0.4、0.5 三档并存，见 V6。

**断点**：只有一条，写法统一成 `(width < 768px)` / `(width >= 768px)`：

- `@theme` 显式写 `--breakpoint-md: 768px`（用 px 而不是 48rem：媒体查询里的 rem 按浏览器默认字号算，用户改了默认字号时会与 768 不一致）；
- `hooks/use-mobile.ts` 的 `MOBILE_QUERY` 现在是 `(max-width: 767px)`，改成同一段文本 `(width < 768px)`，否则浏览器缩放出 767.5 这类小数宽度时，变体判成 web、`max-md:` 的类却生效；
- globals.css 第 1534、1931 行的 `@media (min-width: 768px)` 改成 `(width >= 768px)`；
- 测试核对三处文本一致：`MOBILE_QUERY`、构建产物里 `max-md` 生成的查询、globals.css 里的 `@media`。

### 2.2 用途层（B1 新增，取现值，替换后画面不变）

**外壳**：
- `--cc-sidebar-pad` 8（引用 space-8）；侧栏头纵向 8 / 4。
- 标题栏控件间距 2 / 4，同时供 `.cc-titlebar` 和 `.cc-topbar` 用。
- 导航行左内边距 6，与 `.cc-nav-item` 共用。
- `--cc-mascot-h`：电脑 96 / 112；手机和加载页 72。
- `--cc-greeting-font`：电脑 36；手机 20，原来写在 `.cc-mshell .cc-greeting`（globals.css:2168）。
- `--cc-welcome-gap` 20，现有值·CC 自定。
- `--cc-avatar-sm` 24、`--cc-logo` 24、`--cc-mavatar` 48（外壳实测）。
- `--cc-winbtn-w` 46（系统值）；窗口按钮图标用 `--cc-icon-xs`。
- 手机抽屉内边距 12 / 16 / 20 / 24（外壳实测）。
- 抽屉底栏让位 `--cc-mdrawer-tail`：用 calc 由托盘尺寸算出，替代写死的 `pb-28`。
- 抽屉底部留白 20。
- 手机键盘弹出时输入框离屏边 8（`:root[data-cc-keyboard]` 一段现在写 `0.625rem`、`calc(100% + 1rem …)`），登记为 `--cc-keyboard-edge`，外壳实测（设计文档 G15）。

**菜单**：
- `--cc-menu-offset` 4、`--cc-menu-sub-offset` 6、`--cc-menu-edge` 8（替代 `menu.tsx` 的 JS 常量 `MENU_EDGE`）。统一取值见 V8。
- 菜单宽三档 `--cc-menu-w-sm` 160 / `-md` 176 / `-lg` 208，最宽 `--cc-menu-w-max` 288，都取现值。

**弹窗**：
- 宽 `--cc-dialog-w-sm / md / lg` 448 / 512 / 672；屏边 `--cc-dialog-gutter` 16。
- 内边距 `--cc-dialog-px` 20、头部上 16、底部上下 12。
- 设置弹窗：`--cc-settings-w` 980、`--cc-settings-h` 680、`--cc-settings-share` 0.92、左栏 224、内容区 20/24 与 36/32、关闭钮 14、分节上 32 / 下 4、值列最大宽 22rem。
- 图片预览 `--cc-preview-w` 48rem，占高 0.8。
- `--cc-modal-share` 登记默认 0.88（现在只在 `.cc-modal` 里写成 `var(--cc-modal-share, 0.88)` 的回退），注释「运行时写入：add-company.tsx、settings.tsx 用任意属性覆盖」。

**对话**：
- `--cc-thread-gutter` 16，替代 `thread.tsx` 里设的 `--thread-max-width` 和 `+2rem`。
- `--cc-turn-gap` 24、`--cc-thread-tail` 56、`--cc-run-gap`（16 / 8 两处）、`--cc-reply-actions-h` 30。
- 滚到底按钮的偏移由 calc 算出，不再写 36。
- `--cc-composer-input-max` 288，`thread.tsx` 和 `company-page.tsx` 共用。
- `--cc-card-max-w` 28rem、`--cc-file-icon` 36、`--cc-msg-intrinsic` 200px（性能提示）。
- `--cc-bubble-min` 20%（用户气泡 `grid-cols-[minmax(20%,1fr)_auto]` 里的 20%），现有值·CC 自定。

**Markdown 与代码**：
- `--cc-md-h1` 1.25em、`--cc-md-h2` 1.125em、`--cc-md-heading-lh` 1.375。
- `--cc-code-inline-size` 0.9em（设计文档 3.10）。
- `--cc-md-li-gap` 4、`--cc-md-quote-w` 2、`--cc-link-offset` 2。
- `--cc-code-pad` 14、`--cc-code-lh` 20、`--cc-radius-code-block`（见 V33）。

**表格、页签、面板**：
- 单元格 `--cc-cell-px` 12 / `--cc-cell-py` 6 / `--cc-cell-min-w` / `--cc-cell-max-w`（对话不限、结果面板 288、文档预览 320，现有值·CC 自定），见 V13。
- 页签 `--cc-tab-gap`、`--cc-tab-py`、`--cc-tab-indicator` 2，见 V24。
- `--cc-results-head-h` 48，现有值·CC 自定（V25）。

**其他**：
- toast：底距 24、内边距 16 / 10。
- 状态点 8；`kbd` 内边距 8 / 2；骨架条高 14。
- 设置开关 `--cc-switch-w` 40、`--cc-switch-h` 24：第 11 轮已定电脑上不改；由 `.cc-switch` 使用（见 2.4）。

**运行时层**（登记默认值，算作已定义）：`--cc-vvh`、`--cc-vvtop`（`lib/viewport.ts` 写入）、`--cc-marquee-x`、`--cc-marquee-dur`（`lib/hover-scroll.ts` 写入）、`--cc-modal-share`（见上）。

### 2.3 新共用类（B1 定义；迁移批次只改用法）

- **弹窗与浮层**：
  - `.cc-dialog` 已存在（globals.css:1739，只有弹出阴影），在它上面补外观，新增 `.cc-dialog-head`、`.cc-dialog-title`、`.cc-dialog-body`、`.cc-dialog-foot`；居中定位继续用现有 `.cc-modal`；
  - `.cc-overlay`：遮罩淡入淡出，用 `--cc-dur-fast` 和 `--cc-ease`，替代 tw-animate；
  - `.cc-pop`：弹层与提示框进出场。
- **提示与状态**：
  - `.cc-alert[data-tone]`（含紧凑变体）：统一三套出错框，见 V12；
  - `.cc-toast`；
  - `.cc-empty`：空状态卡；
  - `.cc-empty-line`：无结果和加载占位行，侧栏空状态也用它；
  - `.cc-spinner`：按钮或行内转圈，尺寸 `--cc-icon-sm`，时长 `--cc-spin-dur`（与现有四点转圈 `.cc-spin` 不同，不合并）；
  - `.cc-status-dot[data-status]`。
- **菜单与侧栏**：
  - `.cc-menu-label`：菜单里不能点的说明行；
  - `.cc-kbd`：快捷键格；
  - `.cc-section-label[data-flush]`：贴边的组标题，替代 `!px-0 !pt-0`；
  - `.cc-thread-row`：会话行本体，替代 `thread-list.tsx:1074` 那一长串工具类。
- **对话**：
  - `.cc-user-row`：用户消息网格，原来两处一样；
  - `.cc-file-card`：提醒卡和文件卡共用；
  - `.cc-scroll-btn`：40 的圆形滚到底按钮；
  - `.cc-code-block`：代码块头部和 pre；
  - `.cc-table`：单元格。
- **结果面板**：`.cc-tabs`（`data-edge` 决定指示线贴哪一边）。
- **开关**：`.cc-switch` 保留并改成设置用的 40×24（见 2.4），V19 复用它的过渡。
- **补规则**：
  - `.cc-btn svg { size: var(--cc-icon-sm) }`：现在各组件自己写 `size-4`；
  - `.cc-new-dot svg`：去掉 `!size-4`；
  - `.cc-send[data-stop] svg`：去掉 `!size-3.5`。
- **减少动效**：
  - `:root[data-cc-motion="reduce"] *:not(.animate-spin, .cc-spin i)`（globals.css:2263）改为 `:not(.animate-spin, .cc-spinner, .cc-spin i)`，转圈在减少动效时照常转、不播一次就停；B8 再删掉已无人用的 `.animate-spin`；
  - `@media (prefers-reduced-motion: reduce)` 一段（2220 行起）登记 `.cc-overlay`、`.cc-pop`、`.cc-spinner`、骨架的处理，与 `data-cc-motion="reduce"` 一致。
- **H27 手机弹窗按钮 44**（globals.css:1233–1238，`.cc-modal [data-slot="button"]` 与 `[data-size^="icon"]`）：B1 把选择器改成同时覆盖两种写法，`:is([data-slot="button"], .cc-btn)` 和 `:is([data-slot="button"][data-size^="icon"], .cc-icon-btn)`。`.cc-btn`、`.cc-icon-btn` 在手机上本来就是 44（设计文档 G2、G3），由比对证明 B1 不变；B4 把设置按钮换成 `.cc-btn` 后点按区不回落。

### 2.4 同义变量合并规则

1. **刻度只有一份**。用途变量必须引用刻度层（或 `--code-*` / `--ccs-*`），不另写数。用途变量存在的理由只有两条：在某个变体里取值不同，或语义上可能单独调整。
2. **没人用的纯同义变量删掉**：`--cc-fill-hover`、`--cc-fill-selected`、`--cc-dur-instant`、`--cc-line-stronger`。删之前用 grep 和构建确认确实无人引用（含模板拼接，见第 4 条）。`.cc-switch` **不删**：它现在 36×20、无人使用（设置用的是自己写的 40×24），B1 改成设置用的 40×24 开关类（`--cc-switch-w / h`，保留它的 `--cc-ease-spring` 回弹过渡），B4 设置开关改用它。
3. **有人用的同义变量**：
   - `--cc-bg-active` 并到 `--cc-bg-selected`（globals.css 内 5 处引用，B1 内完成）；
   - `--sidebar-width`、`--sidebar-width-icon`：在 `components/ui/sidebar.tsx` 里设、`split-handle.tsx:75` 的 `sidebarSize.cssVar` 写入。B2 改 sidebar.tsx 用 `--cc-sidebar-w`，**同一个提交**把 `split-handle.tsx:75` 这一行改成 `--cc-sidebar-w`（登记过的跨批例外，见 4.1）。`assistant.tsx:1046` 的 `useStoredSize` 按 `sidebarSize.cssVar` 写，不用改；本机记住的宽度按 `sidebarSize.key` 存，不受影响；
   - `--thread-max-width`：只在 `thread.tsx:124` 设、137 行用，B7 删掉；
   - 只在 B1 里改引用就能完成的，在 B1 做完；
   - 要等组件改名的，在 B8 删掉旧名（见 4.1 的例外）。
4. **引用怎么算**：模板拼接 `var(--cc-status-${k})`（`settings.tsx:52`）算引用整组 `--cc-status-*`；任意属性 `[--cc-x:…]` 算写入，但变量仍须在 globals.css 登记默认值。闸门 R14 和下面的「至少被引用一次」用同一套识别。
5. **容易混的名字改名**：`--cc-titlebar-h`（40，桌面自画标题栏）改为 `--cc-win-titlebar-h`，并补出处。
6. **`test_cc_ccs.py` 增加两项检查**：
   - 每个 `--cc-*` 至少被引用一次（官方 `--ccs-*` 不查；按第 4 条识别拼接）；
   - 刻度层内不出现两个定义完全相同的变量。

### 2.5 twMerge 必须同时做

`lib/utils.ts` 的 `cn()` 用的是 `twMerge`，它不认识 `text-caption` 这类自定义字号名，会当成颜色处理。于是 `cn("text-ink-3", "text-caption")` 会丢掉其中一个类。B1 必须用 `extendTailwindMerge` 登记新字号名（和新字重名），并补测试。

### 2.6 JS 里用到的值怎么读

菜单偏移与离边（`menu.tsx`）、提示框延时（`tooltip.tsx`）、拖动浮层层级（`drag.tsx` 的 `OVERLAY_Z`）只在 globals.css 定值，JS 在**使用时**惰性读：打开菜单时、拿起拖动时、提示框打开时读 `getComputedStyle`。每处都有回退常量，与 globals.css 同值，由测试核对两边一致。

不是样式、但也是长度的 JS 值同样这样做：`IntersectionObserver` 的 `rootMargin`（`companies.tsx`、`thread-list.tsx` 现在写 `"200px"`）在 globals.css 登记带出处注释的 `--cc-*` 变量（例如「现有值·CC 自定」），建观察器时读、留回退常量，由测试核对两边一致；B4 / B6 迁这两个文件时照此处理。闸门只能机械地拦字面长度：用 `` `${常量}px` `` 把一个写死的数字常量拼成长度能绕过它，审查时按同一条规则看（常量也要来自 globals.css 的变量或测试核对过的回退值）。

原因：生产页 `apps/pearnly_core/pearnly_core/www/assistant.html` 用脚本动态插入 `<link>` 和 module `<script>`，脚本不等样式表生效；在导入或首次渲染时读，会拿到空串（`main.tsx` 的 `themeColor` 注释记录过这一点），Radix 的 `sideOffset / collisionPadding / delayDuration` 和 dnd-kit 的 `zIndex` 会收到 NaN。`drag.tsx` 现在读 `--cc-dur` 就是使用时读，照这个做法。比对工具加一种按 assistant.html 方式注入资源的启动方式（5.2）。

## 3. 闸门

### 3.1 判据

`scripts/check-frontend-tokens.py` 的扩法是在现有扫描框架上加规则，沿用它的左右边界正则和注释剥离。**现有 `scan()` 保持只判 R1–R3**，新判据走新接口（例如 `scan_strict()`）。`tests/test_cc_ccs.py` 第 495–560 行的 `TokenGateTests` 原样保留、必须继续通过：它的夹具 globals.css 只有圆角、阴影两项，`test_variables_and_mapped_utilities_pass` 断言 `scan()==[]`，若让 `scan()` 返回 R14 的结果，B0 当场变红；`test_only_node_modules_and_build_output_are_excluded` 断言脚本没有 `IGNORE` 属性，收紧范围用 `STRICT` 一类的名字，它是完成清单，不是忽略清单。

| 编号 | 规则 | 判法 |
| :- | :- | :- |
| R1–R3 | 颜色、圆角、阴影 | 现行判据，**全仓失败，不变** |
| R4 | 尺寸 | 1.2 表「尺寸」一行；允许的名字只有布局关键字和分数 |
| R5 | 间距 | 1.2 表「间距」一行 |
| R6 | 字号与行高 | `text-<名>` 只许 `@theme --text-*` 里接过的名字，颜色名和文字排版关键字（对齐、换行、溢出）另外放行；`leading-*` 只许变量写法和 `none` |
| R7 | 层级 | `z-*`；JSX 的 `zIndex={数}`、style 里 `zIndex` 写数 |
| R8 | 动效 | `duration / delay / ease / animate / transition-all`；tw-animate 类名；`transition-[…]` 方括号里只许属性名；`animate-[…]` 里的缓动关键字，任意属性 `[transition:…]` / `[animation:…]` / `[*-timing-function:…]` 里的时间与缓动（同 R13 的判法） |
| R9 | 其他数值类 | `opacity / stroke / blur / border-<n> / underline-offset / scale / rotate / ring-<n> / outline-<n> / divide-<n> / line-clamp` |
| R10 | 任意值与 `!` | 任意值内部按 1.2 表拆词判断（`fr`、由变量组成的 `minmax` 放行）；被管的工具类带 `!` 一律拦，前置后置都拦 |
| R11 | 断点与输入设备变体 | `sm: lg: xl: 2xl:`；`pointer-* / any-pointer-* / hover-none` 这类变体；容器查询变体 `@…:` 一律拦 |
| R12 | JSX 与内联 | 数值的 `sideOffset / alignOffset / collisionPadding / delayDuration / size / width / height / strokeWidth`；style 里字面长度（模板字符串里有「数字 + 单位」也算），运行时插值放行 |
| R13 | globals.css 自身 | 普通声明里的受管属性只能写 `var()` 或关键字（含 `transform` 的平移、`flex` 的 basis、`font` 简写）；`transition / animation` 简写与长写里的时间、延时、缓动只能写 `var()`；`@media` 宽度只许那一条断点；`@apply` 的内容按 R4–R12、R15 判；`--cc-*` 写数的必须带出处注释；跳过 `@keyframes` 和 `@font-face` |
| R14 | 变量存在性 | `-(--cc-x)` / `var(--cc-x)` 引用的名字必须在 globals.css 里定义过（运行时层登记的默认值算；模板拼接按前缀算）；组件里不许直接写 `--code-*` / `--ccs-*` |
| R15 | 字重 | `font-<字重>` 只许 `@theme` 接过的 `--font-weight-*` 名字或变量写法；`font-[数]` 拦 |

**允许清单从哪里来**：脚本运行时从 `globals.css` 读取：

- `@theme inline` 里的 `--text-*`、`--radius-*`、`--shadow-*`、`--ease-*`、`--animate-*`、`--color-*`、`--font-weight-*`、`--breakpoint-*` 名字；
- 所有 `--cc-*` 定义。

脚本里不维护数值清单。要新增档位，只能先在 globals.css 里登记。

### 3.2 不设忽略清单，怎么分批上线

- 新增 `scripts/frontend-tokens-strict.txt`，一行一个已迁移完的文件。它是**「已完成清单」，不是「豁免清单」**：
  - 里面的文件，R4–R15 违例就失败；
  - 范围外的文件，按文件和规则汇总报数，退出码不受影响，进度看得见；
  - R1–R3 照旧全仓失败。
- 每批在自己的提交里，把本批文件加进清单（按字母序，只增不减）。
- `tests/test_cc_frontend_gate.py` 核对：清单里的路径都存在、是规范写法（不带 `./`、没有 `//` 或 `..`）、扩展名受管、已排序、没有重复。闸门本身也核对：清单条目对不上任何受检文件就失败（否则这个文件会被悄悄跳过）。审查时只看两点：本批有没有删行，有没有加了别批的文件。
- 不支持行内的禁用注释，也不按目录或文件跳过规则。
- **B8 删掉清单文件，新规则全仓失败**，并把测试改成「清单文件不存在」。
- 本地提交钩子和 CI（`.github/workflows/development.yml` 第 138 行已经在跑这个脚本）用同一条命令。CI 加跑新的测试文件。
- B0 时 globals.css 还没登记运行时变量，R14 在范围外报数里会有误报；B1 登记后消失。B0 报数照实记录，不为它改规则。

### 3.3 测试

`tests/test_cc_frontend_gate.py` 是新文件，不往 `test_cc_ccs.py` 里堆，以减少并行批次的冲突。内容：

- 每条规则至少一个「拦」的用例和一个「放」的用例，例如 `p-3` 拦、`p-(--cc-space-12)` 放；`z-50` 拦、`z-(--cc-z-menu)` 放；`max-w-[calc(var(--cc-thread-w)+2rem)]` 拦、`max-w-[calc(var(--cc-thread-w)+var(--cc-thread-gutter)*2)]` 放；`text-center` 放；`grid-cols-[minmax(var(--cc-bubble-min),1fr)_auto]` 放；`!px-0` 与 `p-0!` 都拦；`active:scale-90`、`ring-2`、`pointer-coarse:hidden`、`font-[550]` 拦；
- 运行时变量（`--cc-vvtop`）和模板拼接（`--cc-status-${k}`）的 R14 用例；
- 多行 className、`cn()` 拼接、模板字符串、`[&_svg]:size-4` 这类变体前缀；
- globals.css 的 R13 用例：`animation: x 1.4s ease-in-out` 拦、`@apply p-3` 拦、`@media (min-width: 768px)` 拦；
- 范围文件的格式；
- 断点三处文本一致（2.1）；
- 对真实 `src` 跑一遍：范围内 0 违例。

### 3.4 ccs.md 怎么写（B1 改，B8 收口）

第 1 节加一小节「尺寸、间距、字号、层级、动效」，写五样：

- 1.1 的分层表；
- 1.2 的写法表；
- 刻度层的**名字**列表，数值不抄，注明「数值看 globals.css」；
- 新增档位的流程：先在 globals.css 登记并注明出处 → 需要时接到 `@theme` → 组件使用 → 不许在组件里用 `!` 压标准类；
- 触屏的三种判定各管什么：`data-cc-touch`（尺寸与点按区，排除桌面窗口）、`data-cc-variant`（外壳变体）、`lib/touch.ts` 的 `touchOnly()`（`(hover: none)`，只管「没有悬停」时的行为，例如长按，不管尺寸）。

原来的「闸门」一段（ccs.md:39）改写成 R1–R15 的一行摘要。B8 删掉「收紧范围」的说明，并写明拷入组件要先改写到标准。

## 4. 迁移批次

### 4.1 规则

- **分支**：B0 在 `pearnly/cc-standards`；B1 起每批从最新 `origin/main` 开一个 `pearnly/cc-std-bN` 分支和工作树，直接合入 main（cc-shell-rebuild 10-18 已并入 main、远端分支已删）。同一工作树只有一个写者。
- **同一个源码文件只在一批**。登记过的例外：
  - **`globals.css`** 在 B1 补标准，在 B8 只删已经没人引用的旧名（含 reduce-motion 里的 `.animate-spin`、`tw-animate-css` 的 import）。B8 是纯删除，不改画面；它必须等所有组件改完名才能删。
  - **`split-handle.tsx:75`** 这一行（`cssVar` 改成 `--cc-sidebar-w`）随 B2 的 sidebar.tsx 一起改，文件其余部分归 B3。
  - **`lib/hover-scroll.ts`、`lib/company-panel.ts`** 在 B2 把 `cssMs`、`cssPx` 改成从 `lib/css-var.ts` 再导出，在 B8 删掉再导出（纯删除，测试证明已无人导入）。
  - **`globals.css` 的 B2 补登**（B1「还缺什么」允许 B2–B7 补缺档）：用途层加提示框小箭头 `--cc-tooltip-arrow` 10 / `--cc-tooltip-arrow-turn` 45deg（现有值·CC 自定，组件库模板带的值）；删掉 `--cc-on-danger`（唯一的引用是 B2 删掉的危险按钮外观，删后没人引用，「每个 `--cc-*` 都有人用」的测试当场要求删）。
  - **`globals.css` 的 B3 补登**：用途层加 `--cc-mirror` -1（现有值·CC 自定：CC 桌面版「还原」按钮的两个方框水平镜像，原来写 `-scale-x-100`）；删掉 `--cc-sidebar-head-pt`（2.2 写「侧栏头纵向 8 / 4」，实际侧栏头上是 0：网页的标题区、桌面版的标题栏自带高度，调用处一直写 `pt-0`；接上就是画面变化，没人用又过不了「每个 `--cc-*` 都有人用」）。
  - **`globals.css` 的 B5 补登**：用途层加 `--cc-doc-w` 48rem（结果面板 Word 预览正文最宽，原 `max-w-3xl`；不借 `--cc-thread-w`，它随「对话记录宽度」设置变）、`--cc-alert-lines` 2（出错框原因最多两行，原 `line-clamp-2`），都是现有值·CC 自定、同值。**B7 注意**：`thread.tsx` 的 ERROR_BOX 里同样有 `line-clamp-2`，改成 `.cc-alert` 时直接用 `line-clamp-(--cc-alert-lines)`，不要再登记一个同义变量。
  - **`tests/test_cc_idle_motion.py`**（4.1 共享测试表没列，B5 起按同一规则改）：`BUSY_SITES / BUSY_MOUNTS` 登记的「在忙」动画位置随组件换写法改成新的标记（`cc-spinner`、`<Skeleton`、`PanelSkeleton` 的挂载条件），意思不变。
  - **`globals.css` 的 B7 补登**：用途层「对话」加 `--cc-file-card-py` 10（提醒卡、文件卡上下）、`--cc-alert-lines` 2（出错行最多两行）、`--cc-compose-x-stroke` 2.5px（深色下附件「×」加粗）、`--cc-press-scale` 0.94（回复与代码块「复制」按下，同 `.cc-icon-btn:active`），都是现有值·CC 自定。
  - **`globals.css` 的 B4 补登**：用途层加四个同值变量 `--cc-add-company-share` 0.8、`--cc-run-row-py` 10、`--cc-target-card-py` 10、`--cc-task-skeleton-h` 80（现有值·CC 自定，原来写在 `[--cc-modal-share:0.8]`、`py-2.5`、`h-20`）。
  - **`docs/development/ccs.md`、本任务记录、`scripts/frontend-tokens-strict.txt`** 是登记文件，每批顺序追加。
  - **共享测试**：`tests/test_cc_ccs.py`、`test_assistant_ui.py`、`test_cc_mobile_overlays.py`、`test_cc_mobile_content.py`、`test_cc_run_indicator.py`、`test_cc_month_ledger.py`、`test_cc_session_panel.py` 直接断言写死的类名和渲染出的 HTML（例：Shortcut 断言 `text-ink-3 text-xs`；document-view 断言 `text-2xl / font-bold / mb-2 / padding-inline-start:1.5em`；弹窗断言 `px-5 pt-4 pb-2`、`z-50`；菜单断言 `!min-w-40`、`sideOffset 6`）。断言按它所测组件所在的批次改，各批文件清单列出要改的测试；并行批次合入集成分支时串行、变基。不靠删断言过关：断言改成新写法（类名或变量），意思不变。
- **每批的提交顺序**：
  1. 纯替换：值不变，只换写法、删死代码、删冗余覆盖类；
  2. 画面变化：一项一个提交，对应第 7 节的编号；
  3. 把本批文件加进收紧范围。
- **画面变化提交的条件**：A、B 两类都照第 7 节直接做，验收时一起看；原 C 类已在第 7 节按依据定稿，照定稿做，不再交用户拍板。比对发现第 7 节没列的变化，先判断是改错了还是本就该变；本就该变的补进第 7 节、按同一规则分类后再做。

### 4.2 批次

**B0 闸门与比对工具骨架**（现在开工，不碰 `src`）
- 文件：`scripts/check-frontend-tokens.py`、`scripts/frontend-tokens-strict.txt`（新，先为空）、`tests/test_cc_frontend_gate.py`（新）、`scripts/cc-visual/{server,scenes,shoot,compare}.mjs`（新）、`scripts/README.md`、`.github/workflows/development.yml`、本记录。
- 测试：`tests/test_cc_ccs.py` 不改，现有 `TokenGateTests` 必须原样通过（新判据另开接口，见 3.1）。
- 要做的：
  - 扩 `check-frontend-tokens.py`，加 R4–R15 和收紧范围；CI 加跑新测试；
  - 把第 11 轮的假服务端和无界面 Chrome 探针整理进 `scripts/cc-visual/`：`server.mjs`（假数据）、`scenes.mjs`（场景表，含 5.2 的环境、根节点状态与泰文）、`shoot.mjs`（截图和样式快照；两种启动方式：直接开 `dist/index.html`、按 `assistant.html` 动态注入资源）、`compare.mjs`（比对与报告）；
  - 先自检：同一个构建连跑两遍，必须 0 差异；
  - 验证 Tailwind 4.3.3 对 `-mt-(--x)`、`z-(--x)`、`duration-(--x)`、`text-(length:--x)`、`in-data-[cc-touch]:`、`--breakpoint-md: 768px` 生成的查询文本的解析，给出范围外的基线报数（按文件 × 规则）。
- 画面变化：无。机器时间约 1.5 小时。

**B1 标准层**（依赖：B0；`cc-drag-perf` 合入，它改了 globals.css；`cc-scroll-fix` 合入；开工前再核对两条分支与 `cc-ef-fixes` 是否改到本批文件）
- 文件：
  - `globals.css`：刻度层、用途层、运行时层默认值、新类、`@theme` 映射（字号、字重、断点、默认过渡）；cc-* 类内部约 90 处间距、约 25 处尺寸与字号、z-index、时长都换成变量（同值）；删掉无人用的变量与 tw-shimmer / collapsible 残留；`.cc-switch` 改 40×24；reduce-motion 与 H27 选择器（2.3）；`@media` 宽度统一写法；
  - `lib/variant.ts`：写出 `data-cc-touch`，规则是「手机变体，或粗指针且不是桌面窗口」；在首帧前写入，并监听 `matchMedia` 变化（外接鼠标、平板）；
  - `lib/utils.ts`：登记 twMerge 字号名与字重名；
  - `hooks/use-mobile.ts`：`MOBILE_QUERY` 改成 `(width < 768px)`，注明断点的唯一来源；
  - `docs/development/ccs.md`、`scripts/frontend-tokens-strict.txt`。
- 测试：`test_cc_ccs.py`（新增 2.4 第 6 条两项检查、断点一致检查）；断言 globals.css 写法的 `test_assistant_ui.py`、`test_cc_mobile_overlays.py`、`test_cc_mobile_content.py`、`test_cc_month_ledger.py`、`test_cc_run_indicator.py`、`test_cc_session_panel.py` 里涉及 globals.css 的断言。
- 约 14 对重复规则（phone 变体一份、coarse 且非桌面一份）合成一份 `:root[data-cc-touch]`。`hover: none` 的语义不同，保留。
- 结果面板的手机全屏规则写进 `.cc-results`，与调用方现在的 `max-md:` 类同值叠加，不改画面。变体判定（窄屏优先成 phone，桌面窗口也一样）与 `max-md` 本来就是同一条分界，统一查询文本后小数宽度也一致，不另写 `@media`。
- 画面变化：V2、V3、V4、V6、V20（见第 7 节）。机器时间约 2.5 小时，拆成多个提交。

**B2 组件库**（依赖：B1）
- 文件：
  - `components/ui/` 下的 button、dialog、tooltip、skeleton、sidebar、menu、marquee、separator；
  - 删除 avatar、sheet、input；
  - `main.tsx`；
  - 新文件 `lib/css-var.ts`：把三份「CSS 长度或时长转数字」的解析合成一份（`hover-scroll.ts` 的 `cssMs`、`company-panel.ts` 的 `cssPx`、`split-handle.tsx:42` 的 `px()`）；
  - `lib/hover-scroll.ts`、`lib/company-panel.ts`：改成从 `css-var.ts` 再导出，调用方不动；
  - `components/split-handle.tsx` 第 75 行（登记例外，见 4.1）。
- 测试：`test_cc_ccs.py`（第 400 行读 `ui/input.tsx`、`ui/sheet.tsx` 的断言随删除改写；button、dialog、tooltip、sidebar 的断言）、`test_assistant_ui.py`（marquee、menu、button、sidebar、main、hover-scroll、company-panel）、`test_cc_mobile_overlays.py`（menu、sidebar、dialog、main）。
- 各组件的处理：
  - **Button**：只删没人用的 6 档。`variant="ghost" size="icon"` 等在用的输出**保持原样**，映射到 `.cc-row-btn / .cc-icon-btn / .cc-btn` 随调用方所在批次做（会话行「···」`thread-list.tsx:1221` 在 B6，回复与代码块「复制」`tooltip-icon-button.tsx:27`、`thread.tsx:360` 在 B7，设置页按钮在 B4），否则 V29、V30、V10 的变化会提前出现在 B2，打乱后面批次的「纯替换 0 差」基线。
  - **sidebar.tsx**：删掉 18 个没人用的导出和随机宽度骨架；`--sidebar-width` 别名改成 `--cc-sidebar-w`（同提交改 split-handle.tsx:75）。
  - **dialog**：`sm:` 改 `md:`（V36）。
  - **menu**：偏移、离边按 2.6 使用时从 `--cc-menu-offset / sub-offset / edge` 读，导出给调用方用。
  - **tooltip**：延时按 2.6 从 `--cc-tooltip-delay` 读。
- 场景：拖分隔条调侧栏宽度后宽度生效、刷新后恢复记住的宽度。
- 画面变化：V5、V7、V9、V11、V18、V36（dialog 部分；实际随死代码删除、无可见变化）、V38（B2 补进第 7 节）。机器时间约 1.5 小时（实际 8 遍全矩阵约 2 小时）。

**B3 外壳**（依赖：B2；可与 B4、B5 并行）
- 文件：`assistant.tsx`、`components/mobile-shell.tsx`、`shortcut.tsx`、`period-chip.tsx`、`mascot.tsx`、`split-handle.tsx`（第 75 行已在 B2）、`install-prompt.tsx`、`error-boundary.tsx`、`typing-dots.tsx`。
- 测试：`test_assistant_ui.py`、`test_cc_mobile_overlays.py`、`test_cc_session_panel.py`、`test_cc_run_indicator.py` 里涉及这些文件的断言。
- 要做的：
  - 欢迎页、侧栏头、手机抽屉、托盘的写死值换成变量；
  - 删掉不起作用的 `md:text-4xl`（`assistant.tsx:129`）；
  - 删掉 DrawerIcon 不生效的 `width="22"`；
  - 删掉 ResultsPanel 调用处的 `max-md:` 类（B1 已经接手）；
  - 菜单的 `!min-w-*` 换成菜单宽档；
  - `sideOffset` 改用 menu 导出的值；
  - `split-handle.tsx:42` 的 `px()` 和 `assistant.tsx:98` 的 `cssPx` 改从 `lib/css-var.ts` 导入。
- 画面变化：V1、V8、V16、V36（`assistant.tsx:128` 形象 `sm:h-28`）。机器时间约 1.5 小时。

**B4 弹窗与列表页**（依赖：B2）
- 文件：`settings.tsx`、`scheduled-tasks.tsx`、`add-company.tsx`、`delete-chats.tsx`、`companies.tsx`、`company-page.tsx`、`pending.tsx`。
- 测试：`test_assistant_ui.py`、`test_cc_mobile_overlays.py`、`test_cc_mobile_content.py`、`test_cc_month_ledger.py`、`test_cc_session_panel.py` 里涉及这些文件的断言。
- 要做的：
  - 三份手抄的弹窗外壳改为 `.cc-dialog-*`；
  - toast、空状态、无结果行、错误框、骨架换成共用类；
  - 设置页的 NavItem、组标题、按钮（`<Button>` 换 `.cc-btn`）、图标钮换成标准类；设置开关改用 `.cc-switch`；
  - 内联的透明度和三元选色改成类；
  - `sideOffset` 改用 menu 导出的值。
- 场景：手机设置弹窗里「连接」「下载」等按钮点按区 44（H27）。
- 画面变化：V1、V3、V6、V8、V10、V12、V14、V15、V17、V19、V21、V22、V36（`scheduled-tasks.tsx` 863 / 895 / 995 行的 `sm:grid-cols-2`）。机器时间约 2.5 小时。

**B5 会话栏、结果、文档与工具卡**（依赖：B2）
- 文件：`company-cards.tsx`、`session-panel.tsx`、`month-ledger.tsx`、`results-panel.tsx`、`document-view.tsx`，以及 `tool-uis/` 下的 steps、approval-bar、approval-sheet、index。
- 测试：`test_assistant_ui.py`、`test_cc_mobile_content.py`、`test_cc_month_ledger.py`、`test_cc_session_panel.py`、`test_cc_ccs.py`（steps）、`test_cc_run_indicator.py`（steps）里涉及这些文件的断言。
- 画面变化：V1、V12、V13、V23、V24、V25、V26、V27。机器时间约 2 小时。

**B6 侧栏列表**（依赖：`cc-drag-perf` 合入、B2）
- 文件：`assistant-ui/thread-list.tsx`、`sidebar-group.tsx`、`projects.tsx`、`components/ui/drag.tsx`。
- 测试：`test_assistant_ui.py`、`test_cc_mobile_overlays.py`、`test_cc_session_panel.py` 里涉及这些文件的断言。
- 要做的：
  - 会话行改为 `.cc-thread-row`；
  - 行尾「···」改为 `.cc-row-btn`（B2 留下的 Button 映射在这里做）；
  - 去掉菜单图标上的 `size-4`；
  - 空状态改为 `.cc-empty-line`；
  - `OVERLAY_Z`（`drag.tsx:1272`）按 2.6 拿起时读 `--cc-z-drag`；
  - `drag.tsx:103` 的 `cssMs` 改从 `lib/css-var.ts` 导入；
  - `sideOffset` 改用 menu 导出的值。
- 画面变化：V1、V8、V28、V29。机器时间约 1 小时。

**B7 对话区**（依赖：`cc-scroll-fix` 合入、B2）
- 文件：`assistant-ui/thread.tsx`、`attachment.tsx`、`markdown-text.tsx`、`tooltip-icon-button.tsx`、`follow-up-suggestions.tsx`。最后一个会被删除。
- 测试：`test_assistant_ui.py`、`test_cc_ccs.py`（thread、markdown-text）、`test_cc_mobile_content.py`、`test_cc_mobile_overlays.py`（第 170 行断言 `aui-thread-welcome-suggestions`，随 ThreadSuggestions 删除改写）、`test_cc_run_indicator.py`（第 433 行用 `thread.index("const ThreadWelcome")` 切片，删 ThreadWelcome 后会抛异常，改用别的锚点）。
- 要做的：
  - 删掉死代码：ThreadWelcome、ThreadSuggestions、ThreadFollowupSuggestions；
  - 对话栏间距、提醒卡、文件卡、出错框、滚到底、代码块、表格、Markdown 标题换成标准；
  - TooltipIconButton 不再包 shadcn Button（B2 留下的映射在这里做）；
  - `thread.tsx:748` 用户消息「复制」条的 `pointer-coarse:hidden` 改按 `data-cc-touch`（V3）。
- `lib/follow-scroll.ts` 没有样式，不进任何批次。
- 画面变化：V1、V3、V12、V13、V30–V35、V36（`attachment.tsx:115` 的 `sm:max-w-3xl`）。机器时间约 2 小时。
- 实际（见「B7 做了什么」）：V13 在对话里无可见变化；补进第 7 节 V70（滚到底）、V71（附件移除钮）、V72（用户消息「复制」条位置）；全矩阵 3 遍 + 子集 14 遍，约 5 小时（3 条跑道）。

**B8 收口**（依赖：B3–B7 全部合入）
- 文件：
  - 删除 `scripts/frontend-tokens-strict.txt`；改 `check-frontend-tokens.py` 和 `test_cc_frontend_gate.py`；
  - `ccs.md`；
  - `globals.css`：删掉组件改名后已无人引用的旧变量名、reduce-motion 里的 `.animate-spin`、`@import "tw-animate-css"`，由测试证明无人引用；
  - `lib/hover-scroll.ts`、`lib/company-panel.ts`：删掉再导出；`test_cc_session_panel.py:344`、`test_assistant_ui.py` 里从旧位置导入的地方改到 `css-var.ts`；
  - `frontend/assistant/package.json` 与锁文件：删 `tw-animate-css`、`tw-shimmer` 两个依赖，删后构建确认（B8 有自己的 node_modules，不改符号链接指向的共享目录）；
  - `components/NOTICE.md`：保留 MIT 声明，把「Regenerate or extend them with that command」改成「拷入的组件先改写到标准（cc-* 类和变量）才能进 src」；
  - `components.json`：只给 shadcn 命令用、构建不读，删前 grep 确认没有脚本引用，然后删除。
- 选做：加一项检查，长度 5 个以上的受管工具类组合在两处以上一字不差出现时报警。
- 画面变化：无。机器时间约 0.5 小时。
- 实际（见「B8 做了什么」）：另删了只给 Button 用的 `class-variance-authority`、没人用的 shadcn 别名与 `text-xs / sm / base / 4xl`；全矩阵 2 遍 + 抖动场景复拍 4 遍，约 1.2 小时（7 条跑道）。

## 5. 验证

### 5.1 每批都做

- `npm run typecheck`、`npm run build`；
- `python3 scripts/check-frontend-tokens.py`、`check-frontend-viewport-units.py`、`check-architecture.py`；
- `tests/test_cc_*.py`、`tests/test_assistant_ui.py`、`tests/test_cc_frontend_gate.py`；
- 推分支前按 `development.yml` 本地全跑一遍，推完确认 Development checks 是绿的。

### 5.2 纯替换：证明画面不变

**工具**：B0 入库的 `scripts/cc-visual/`。
- 本机 Google Chrome 无界面运行，直接走 CDP，参数 `--force-color-profile=srgb --hide-scrollbars`。
- 假服务端只返回假数据，不连任何真实服务。
- 不引入新依赖：像素比对放在 Chrome 页面里，用 canvas 的 `getImageData` 做，不装 PIL 或 pixelmatch。
- 两种启动方式：直接开 `dist/index.html`；按 `assistant.html` 的方式动态插入 `<link>` 和 module `<script>`（复现 2.6 的加载时序）。

**环境**（7 种 × 浅深色 = 14 组）：

| 环境 | 尺寸 | 用途 |
| :- | :- | :- |
| web | 1280×800，像素比 2 | 网页变体 |
| desktop | 1280×800，加 qt 桩 | CC 桌面版变体 |
| phone | 390×844，像素比 3，触屏，iPhone UA | 手机变体 |
| web-coarse | web 加触屏模拟 | 验证 `data-cc-touch` 合并 |
| desktop-coarse | desktop 加触屏模拟 | 验证桌面窗口在触屏上的排除规则 |
| tablet-portrait | 744×1133，触屏 | 平板竖屏（iPad mini），落在 640–767 段，验证 `sm:` 改 `md:`（V36） |
| desktop-narrow | 700×800，加 qt 桩 | CC 桌面版窗口拉窄，同上 |

另加一个 767.5 宽的环境（`web-767.5`），核对小数宽度下变体与 `max-md` 一致。CDP 的视口宽度只收整数，做法是外层 1535 宽、像素比 1 的页面里放一个 `zoom: 2`、宽 `767.5px` 的同源 iframe：iframe 里布局宽度正好 767.5、像素比 2，每个场景读回 `matchMedia("(width: 767.5px)")` 核对。

**场景**（约 40 个，每个含必要的交互状态）：

- 外壳：欢迎页；对话页（用户消息、Markdown 表格和代码块、步骤卡、允许卡待答和已答、文件卡、提醒卡、出错框、工作状态行、滚到底）；
- 侧栏：组展开、已置顶、行悬停、行菜单和子菜单、多选、空搜索、拖分隔条调宽与刷新后恢复；
- 手机：抽屉打开、长按菜单、等回答的浮层、键盘弹出时的输入框（`data-cc-keyboard`）；
- 面板：结果面板（表格预览、PDF、HTML 页签）、文档预览、会话栏（公司卡、月份账、知识库编辑）；
- 页面：待处理、定时任务（列表、新建表单、确认框）、公司列表、公司页、添加公司；
- 弹窗：删除确认、设置各页签（含手机设置弹窗的按钮点按区）、图片预览；
- 其他：提示框、拖动中浮层（静态帧）、加载骨架、空状态、安装提示；
- 根节点状态：对话宽度 medium / wide（`data-cc-width`）、无衬线回复字体（`data-cc-font=sans`）、文字大小 small / large（`html[data-text-size]`）、减少动效（`data-cc-motion=reduce`，这一场景**不注入**动画归零，核对转圈仍在转）、桌面版拖文件进窗口（`data-cc-dropping`）、多选拖动（`data-cc-drag-bulk`）、拖分隔条中（`data-cc-resizing`）；
- 语言：界面主语言是泰文（`html lang=th`），关键场景（欢迎页、对话页、侧栏、设置、定时任务）用泰文浅色各跑一遍，看上下标在固定高度里有没有被裁；
- 悬停、聚焦、按下这些状态用 CDP 的鼠标和键盘事件制造。

**做法**：
1. 批次基点和分支各构建一次，得到 `dist-before` 和 `dist-after`，用同一个假服务端各跑一遍。
2. **先采样式快照**：对每个元素和它的 `::before / ::after`，记录外框（取到 0.01px）和受管属性的计算值，包括字号、行高、字重、内外边距、gap、圆角、z-index、透明度、`transition` 与 `animation` 的时长和曲线、颜色。动效差异靠这一步发现。
3. **再截图**：注入样式把 animation 和 transition 都设为 0（减少动效场景除外）、隐藏光标；固定 `Date.now`；等 `document.fonts.ready` 和两帧之后截图。
4. 逐场景比对两份快照、两张截图，输出差异表和红色叠图。

**判定**：
- 纯替换提交必须全部场景 0 像素差、0 样式差。
- 有差异时：要么是改错了，修掉；要么本来就该算画面变化，移到画面变化提交并补进第 7 节。

**覆盖检查**：
- 从本批 diff 取出改动涉及的 `cc-*` 类和 `data-slot`，核对至少有一个场景渲染到了它们。
- 没覆盖到的，补场景或人工点，并在报告里写明是哪一种。

**已知局限**：
- 只在浏览器默认 16px 字号下证明等价。刻度层用 px，原来写 rem 的地方在用户改了浏览器默认字号时不再跟着缩放；现状本来就是两种混用，记录为已知差异。
- 测不到 WSL2 和 Qt WebEngine 的渲染差异、真机字体。

### 5.3 有画面变化：交测试窗口

1. 比对工具出「前 / 后」对照图和样式差异表，按第 7 节编号归档。
2. 本地构建，用 `PEARNLY_TESTENV_LOCAL_SPA=1` 同步到测试机，从外部读回资源哈希，确认测试机上是新版本，再交给测试窗口。
3. 测试窗口按批次清单验收：三种变体 × 浅深色。涉及触屏和中间宽度的项（V3、V4、V24、V30、V36），用用户手机走一遍，我在服务器侧核对。
4. 证据分层报告：实现、本地检查、比对、测试机、用户验收，缺哪一层写哪一层。用户确认之前不写「已验收」。

## 6. 风险与不做的事

**不做**：
- 登录页（Keycloak 主题）的文案和布局不动。
- `--ccs-*` 官方表、`--code-*` 实测值不改。
- 不改 Tailwind 全局 `--spacing`。
- 不加紧凑档，不加新功能，不加说明文案。
- 不引入新依赖（B8 只删依赖）。
- **不改用户已定的口径**：
  - 卡片按钮电脑 24、手机 44（10-17）；
  - 手机输入框按钮 34；
  - 设置开关电脑 40×24（第 11 轮）；
  - 侧栏行高 32 / 30 / 44、组上间距 16 / 14；
  - 菜单规格（圆角 12、项高 32、内边距 4，以及 6/10）；
  - 焦点只描边、没有光晕；
  - 跟随滚动规则；
  - 拖动参数（开发窗口定）；
  - 抽屉尺寸与 380ms（外壳实测）；
  - 对话宽度、对话字体这两个设置项；
  - 形象动画（照搬宣传页，只登记变量，不改节奏）。
- 没有参照的值照现值登记，不为凑整挪到刻度上。

**风险与对策**：
1. **twMerge 吞掉自定义字号类**。对策：B1 登记新字号名与字重名，并补测试（2.5）。
2. **组件里的覆盖类一删，cc-* 类的值就生效了**。原因是 utilities 层优先于 components 层。看起来是「纯替换」，画面可能会变；由逐像素比对兜住，发现了就移到画面变化提交。
3. **断点写法不一致、`data-cc-touch` 需要运行时切换**。变体判定本身是「窄屏优先成 phone」，与 `max-md` 是同一条分界；差别只在查询文本不同导致的小数宽度。对策：B1 统一成同一段查询文本并由测试核对（2.1），比对加 767.5 宽场景；`variant.ts` 监听媒体查询（外接鼠标、平板）。
4. **phone 和 coarse 成对规则本身可能不一致**（第 11 轮 D26 就只补了一边）。合并时以完整的一边为准，作为 A 类画面变化单列（V4）。
5. **并行冲突**：范围文件、共享测试、任务记录会被多批改到。对策：批次合入 main 时串行、变基。B6 和 B7 必须等对应分支合入后，从新的基点开工。
6. **比对工具不稳定**（字体、时间、随机宽度骨架）。对策：B0 先自检连跑两遍 0 差；sidebar 的随机宽度骨架在 B2 删掉。
7. **假服务端覆盖不到的分支**：CC 桌面版专属的「此电脑 · 连接」、安装提示的平台分支。由覆盖报告列出，人工点。
8. **B1 工作量集中**（globals.css 2295 行）。对策：按「刻度 → 用途 → 新类 → 内部替换 → touch 合并 → 删变量」拆提交，每个提交都跑一遍比对。
9. **JS 读 CSS 变量的加载时序**：生产页样式表可能晚于脚本生效。对策：使用时惰性读、回退常量由测试核对（2.6），比对工具按 assistant.html 方式启动。
10. **减少动效被新类绕过**：比对默认注入动画归零，测不出转圈停住。对策：2.3 的选择器改动，加不归零的减少动效场景。
11. **共享测试断言写死类名**：纯替换一做就红。对策：断言随组件所在批次改成新写法，意思不变（4.1）。

## 7. 画面会变的项

类别说明：

- **A**：照现行标准改，用于修违例或缺陷，验收时一起看；
- **B**：亚像素级或时长级的变化，照标准直接做，验收时一起看，不单独请用户确认；
- **原 C·已定**：原方案列为「要用户拍板」的项。开发窗口 10-17 定：不交用户。有 Claude 实测或 ccs 依据的照标准做；没有参照的保留现值并登记「现有值·CC 自定」。每项的依据写在「标准 / 定稿」一列。

| 编号 | 批次 | 项 | 现在 | 标准 / 定稿 | 类 |
| :- | :- | :- | :- | :- | :- |
| V1 | B3–B7 | 组件里的 `text-sm / xs` 换成实测字阶 `text-body / caption` | 14/19.6、12/16.8 | 14/20、12/17，每行行高 +0.4 / +0.2px | B |
| V2 | B1 | Tailwind 默认过渡 | 150ms，(.4,0,.2,1) | `--cc-dur-fast` 120ms，主缓动 | B |
| V3 | B1 / B4（无改动，见 B4 小节第 1 条） / B7 | 三条 coarse 规则（`.cc-tap`、`.cc-card-btn`、`.cc-choice-*`）没有排除桌面窗口；`.cc-chip-btn` 没有手机 44 档；`thread.tsx:748` 用户消息「复制」条用 `pointer-coarse:hidden` | 触屏 Windows 上的 CC 桌面版会变 44、复制条被藏；手机上筛选胶囊只有 28 | 按 ccs.md「不改电脑值」排除桌面窗口，复制条改按 `data-cc-touch`；胶囊用伪元素把点按区补到 44，外观不变 | A |
| V4 | B1 | phone 与 coarse 成对规则中不一致的几对 | 只补了一边 | 以完整的一边为准 | A |
| V5 | B2 / B4 | 骨架脉动（B2：`ui/skeleton.tsx`；B4：`companies.tsx:150`、`scheduled-tasks.tsx:440` 直接写的 `animate-pulse` 加载卡片，B2 验收时测试窗口发现、补登） | 2s，Tailwind `pulse`：透明度 1↔0.5 | `--cc-pulse` 1.2s，透明度 0.35↔1（同运行中脉冲点） | B |
| V6 | B1 / B4 | 不可用透明度 | 0.35 / 0.4 / 0.5 | 统一 0.4（实测菜单不可用 40%） | B |
| V7 | B2 | 提示框层级 | 50，被菜单 60 盖住 | `--cc-z-tooltip` 90 | A |
| V8 | B3 / B4（本批菜单原来都是 4，无可见变化） / B6 | 菜单离触发器的距离 | 4 / 6 / 8 混用（主菜单约 9 处 4、`assistant.tsx` 有 6 和 8，`period-chip`、`projects` 用 6） | **主菜单 4、子菜单 6**。依据：ccs.md 菜单规格是一套，同类菜单不该三种距离；距离本身 Claude 没有实测，取现有多数（主菜单 4）和 `menu.tsx` 子菜单默认值 6，登记「现有值·CC 自定」 | 原 C·已定 |
| V9 | B2 | 图片预览弹窗圆角 | 10 | `--cc-radius-dialog` 16（globals.css:562，外壳实测） | A |
| V10 | B4 | 设置页 Button 换成 `.cc-btn` | 左右 12、带投影、悬停 `bg-accent` | 左右 14、无投影、悬停 `--cc-bg-hover`、120ms；手机 44 由 2.3 的 H27 选择器保住 | A |
| V11 | B2 | 侧栏收起和展开的缓动 | 200ms 匀速 | 200ms 主缓动 | B |
| V12 | B4 / B5 / B7 | 出错框 | 三套：无边 12/8、有边 12/12、侧栏 13px 6/10 | **统一一套 `.cc-alert`**：危险浅底、危险线、内边距 12（`--code-pad-3`）、圆角 8（`--cc-radius-md`，ccs.md「对话里的卡片 8」）；侧栏那套作为紧凑变体（13px、6/10）保留。依据：ccs 状态色与卡片规格 | 原 C·已定 |
| V13 | B5 / B7 | 表格单元格 | 左右 12 / 10 / 10，最宽不限 / 320 / 288 | **统一对话 Markdown 那套**：左右 12、上下 6（设计文档 3.10）；最宽按场景保留现值，登记为 `--cc-cell-max-w`（对话不限、文档预览 320、结果面板 288，现有值·CC 自定）。结果面板、文档预览左右 10→12 | 原 C·已定 |
| V14 | B4 | 确认类弹窗（定时任务、添加公司、删除确认）的圆角、标题、脚部、层级 | 圆角 8；标题 `.cc-greeting` 衬线、`text-xl` 24/30；脚部两种（定时任务上下 12 带上边线，删除确认上 8 下 16 无边线）；层级 50 与 80 | **圆角 16**（`--cc-radius-dialog`，外壳实测，设置弹窗已用）；**标题字号 22/28**（实测 title 档 `text-title`）；**标题字体保留衬线**：查了设计文档（2.2 只有 title 档字号、没写用途）、研究文档、ccs.md 和它们引用的截图说明，没有 Claude 确认框标题字体的参照，按现值保留；**脚部统一**为上下 12、带上边线；**层级**：遮罩与内容同档，普通弹窗 `--cc-z-sheet`，删除确认 `--cc-z-confirm`（要盖过设置里的菜单），值不变 | 原 C·已定 |
| V15 | B4 | 遮罩淡入 | 150ms，tw-animate | `.cc-overlay`：120ms 主缓动 | B |
| V16 | B3 | 菜单里的快捷键 | `text-xs` | `.cc-menu-key`，左内边距多 12 | A |
| V17 | B4 | 设置左栏 | 图标 16、间距 10、行间 4、组标题 12px、选中色不同 | `.cc-nav-item` 与 `.cc-section-label`：图标 20、间距 6、行间 2、组标题 13/16 | A |
| V18 | B2 | 提示框字号与进出场 | 12/16.8、tw-animate 默认 | 12/17、`--cc-dur-fast` | B |
| V19 | B4 | 设置开关的动效 | 150ms 默认缓动 | 改用 `.cc-switch`（B1 改成 40×24）：`--cc-dur` 加回弹，尺寸不变 | B |
| V20 | B1 / B4 | 卡片按钮里的图标 | `pending.tsx` 写 `size-4` 压掉类规则，手机上停在 16 | 由 `.cc-card-btn svg` 决定，手机 22（`size-4` 在 B4 删） | A |
| V21 | B4 | 待处理「交给 CC」卡片按钮带 Sparkles 图标（`pending.tsx:254`、`391`） | 带图标 | **去掉图标**。依据：ccs.md:51（用户 10-17）卡片按钮「不带图标」，并点名待处理「交给 CC」同用这一类。属修违例 | A（原 C） |
| V22 | B4 | 设置「此电脑 · 连接」的手写图标钮 | 28 | `.cc-row-btn` 24 | A |
| V23 | B5 | 结果面板头部按钮、「下载」链接 | 手写 32，无聚焦描边，手机不到 44 | `.cc-icon-btn cc-tap`、`.cc-btn` | A |
| V24 | B5 | 结果面板两套页签 | HTML 页签：间距 16、上下 8、指示线在下；工作表页签：间距 4、上下 6、指示线在上 | **统一一套 `.cc-tabs`**，取 HTML 页签的间距与上下内边距，指示线 2（`--cc-tab-indicator`）贴内容一侧：工作表页签在表格下方，指示线在上（`data-edge`），是位置不同，不是两套样式。Claude 没有页签实测，数值登记「现有值·CC 自定」 | 原 C·已定 |
| V25 | B5 | 结果面板头高 | 48 | **保留 48**，登记 `--cc-results-head-h`「现有值·CC 自定」。Claude 面板头没有可对照的实测（右侧栏卡头 26 是卡片，不是面板头），不改 | 原 C·已定（无变化） |
| V26 | B5 | 文档预览 | 标题段距按固定像素、上下都加；正文行高 1.625；缩进 1.5em；表格上下 12 | 照对话 Markdown：只加上间距、按 em；正文 `--cc-body-lh`（手机 16/24）；缩进 `--cc-md-list-indent` | A（注释本来就写「同对话里 Markdown」） |
| V27 | B5 | 会话栏卡片里的文字；卡片加载状态；步骤明细 | 被强压成 14；转圈；明细左右 12，分隔 0.5px | 继承 13/19；骨架；明细 8，分隔 1px | A |
| V28 | B6 | 「加载更多」行和骨架行的高度；「加载更多」行的转圈（B6 开工时按 4.1 补进：记录没列，`animate-spin` 不改过不了 R8） | 固定 32；转圈 14、Tailwind `animate-spin` | `--cc-row-h`：桌面窗口 30、手机 44；转圈 `.cc-spinner`（16、`--cc-spin-dur` 1s 匀速，同其他行内转圈，减少动效时照常转） | A |
| V29 | B6 | 会话行「···」与行过渡 | shadcn Button，字色 ink-2，150ms（V2 后已是 120ms 主缓动）；行是一长串工具类，`transition-colors` | `.cc-row-btn`，字色 ink-3，120ms；B6 实际差在：「···」悬停底深色也是整档悬停底（原来半透明）、过渡只管底色 / 字色 / 透明度（原来 `transition-all`）；行换 `.cc-thread-row`，过渡只管底色与字色，悬停底不再包在 `(hover: hover)` 里（与侧栏其他 `cc-*` 行一致） | A |
| V30 | B7 | 回复下的「复制」和代码块的「复制」 | 圆角 8、深色悬停半透明、150ms、按下 0.9；代码块的没有 cc-tap | `.cc-row-btn`：圆角 6、120ms、按下 0.94，加 cc-tap | A |
| V31 | B7 | 发送中气泡的文件条 | 自写的一条，落地时会跳一下 | `.cc-attach-card` 加骨架 | A |
| V32 | B7 | MessageError 的 `mt-2` 盖掉了标准块间距 | 8 | `--cc-md-block`（0.75em） | A |
| V33 | B7 | 代码块 | 行高 1.625（`leading-relaxed`）；圆角 12（`rounded-xl`），对话里的表格外圈、步骤明细卡是 10 | **行高 20**（`--cc-code-lh`，实测正文 14/20）；**圆角统一 10**：按 ccs 现有档位，对话里的块级卡片（表格外圈、步骤明细卡 `--cc-radius-step-card`、用户气泡、附件卡）都是 `--code-radius-10`，代码块登记 `--cc-radius-code-block: var(--code-radius-10)` | 原 C·已定 |
| V34 | B7 | 复制图标切换的时长 | 150 / 200，Tailwind ease-out | `--cc-dur-fast` / `--cc-dur`，加 `ease-cc-out` | B |
| V35 | B7 | 附件：悬停效果叠了两层；上传遮罩有 1px 模糊 | 两种悬停叠加；有模糊 | 只留 `.cc-compose-tile` 的悬停；去掉模糊 | A |
| V36 | B2 / B3 / B4 / B7 | `sm:`（640）改 `md:`（768）：`dialog.tsx` 的 `sm:text-start / sm:flex-row`、图片预览的 `sm` 断点、`assistant.tsx:128` 形象 `sm:h-28`、`scheduled-tasks.tsx` 863 / 895 / 995 行的 `sm:grid-cols-2`、`attachment.tsx:115` 的 `sm:max-w-3xl` | 640–767 宽（iPad mini 竖屏 744、CC 桌面版窗口拉窄）按宽屏排 | 只有一条断点 768（与变体同一条），这一段按手机排；640 没有 Claude 依据。验证用 tablet-portrait、desktop-narrow 两种环境 | A |
| V37 | B1 | 手机 / 电脑分界的查询文本：`hooks/use-mobile.ts` 的 `MOBILE_QUERY` 是 `(max-width: 767px)`，`max-md:` 是 `(width < 48rem)` | 浏览器缩放出 767.5 这类小数宽度时变体判成 web、`max-md:` 的类却生效：侧栏已收起、却没有手机顶栏，设置等入口点不到（B0 比对 web-767.5 实测） | 三处同一段文本 `(width < 768px)` / `(width >= 768px)`（2.1「断点」），767.5 宽按手机外壳排；测试用项目自己的 Tailwind 现编 globals.css 核对 | A（B1 比对时补进本节） |
| V38 | B2 | 组件库弹窗（`dialog.tsx`，现在只有图片预览用）的遮罩与弹层进出场 | tw-animate：遮罩淡入 150ms、弹层淡入放大（0.95）200ms，`ease` | `.cc-overlay` / `.cc-pop`：快档 120ms、主缓动（同 V15 遮罩、V18 提示框）；组件库文件不再用 tw-animate，B8 才删得掉这个依赖 | B（B2 开工时按 4.1 补进本节：记录把 dialog 的动效漏了，不改就过不了 R8、也删不掉 tw-animate） |
| V39 | B3 | 「添加到主屏幕」步骤（`install-prompt.tsx`，iPhone / iPad 浏览器里点安装提示）的遮罩进出场 | tw-animate 淡入 150ms、`ease`，关掉时不淡出 | `.cc-overlay`：淡入淡出快档 120ms、主缓动（同 V15、V38）；层级 `--cc-z-modal` 70 不变 | B（B3 开工时按 4.1 补进本节：记录把这个文件的 tw-animate 漏了，不改过不了 R8） |
| V70 | B7 | 滚到底 ↓（`thread.tsx` 的 ThreadScrollToBottom）走 shadcn 的 outline 图标 Button | 箭头正文色；深色底是 `input/30`（白 3%）；写的 `shadow-[var(--cc-shadow-composer)]` 被 Tailwind 当成阴影颜色，从没生效；`transition-all` | 共用的 `.cc-scroll-btn`（B1 定义）：箭头 ink-2、表面色底、输入框同款 1px 环 + 投影、只过渡底色 120ms，位置与大小不变 | A（B7 补进本节：`TooltipIconButton` 不再包 Button 带出来的，第 7 节只列了复制钮；编号从 70 起，避开 B3–B6 同时补的号） |
| V71 | B7 | 输入框附件右上的移除 ×（`attachment.tsx`）走 shadcn 的 ghost 图标 Button，类几乎都被覆盖 | `transition-all`、按下 0.9 | 自己的标准写法：20 圆钮、表面色、轻投影、× 悬停变红（同原样），只过渡颜色、按下 0.94（`--cc-press-scale`） | B（同 V70） |
| V72 | B7 | 你发的消息里「复制」条的位置：`[&:where(>*)]:col-start-2` 不生成任何规则 | 复制条落在左边那一大列，在气泡左下外侧 | 用 B1 的 `.cc-user-row`（发送中气泡已在用）：子元素都在气泡那一列，复制条在气泡下方右侧（同 claude.ai，代码注释原本就写「气泡下方右侧」） | A（缺陷修复，B7 比对前补进本节） |

## 8. 批次清单

| 批 | 名称 | 依赖 | 画面变化 |
| :- | :- | :- | :- |
| B0 | 闸门与比对工具骨架 | 无（现在开工） | 无 |
| B1 | 标准层（globals.css、@theme、touch 标记、断点、twMerge） | B0、cc-drag-perf 合入、cc-scroll-fix 合入 | V2、V3、V4、V6、V20（B1 部分无可见变化）、V37 |
| B2 | 组件库与共用 lib | B1 | V5、V7、V9、V11、V18、V36（无可见变化，见 B2 小节）、V38 |
| B3 | 外壳 | B2 | V1、V8、V16、V36（无可见变化，见 B3 小节）、V39 |
| B4 | 弹窗与列表页 | B2 | V1、V3（无改动）、V5（补登）、V6、V8（无可见变化）、V10、V12、V14、V15、V17、V19、V20、V21、V22、V36 |
| B5 | 会话栏、结果、文档、工具卡 | B2 | V1、V12、V13、V23–V27 |
| B6 | 侧栏列表（拖动分支的文件） | B2、cc-drag-perf 合入 | V1、V8、V28、V29 |
| B7 | 对话区（滚动分支的文件） | B2、cc-scroll-fix 合入 | V1、V3、V12、V13（无可见变化）、V30–V36、V70–V72 |
| B8 | 收口：全仓失败、删旧名、删 tw-animate / tw-shimmer 依赖、改 NOTICE | B3–B7 | 无 |

## 9. 漏项审查 18 条的处理

方案初稿经一轮漏项审查，18 条全部并入上文：

| # | 漏项 | 并入位置 |
| :- | :- | :- |
| 1 | 多个测试文件直接断言写死类名，删死代码会让切片断言抛异常 | 4.1 共享测试例外；各批「测试」一行；B7 列出第 170、433 行 |
| 2 | B0 改闸门会让 `test_cc_ccs.py` 现有闸门测试变红 | 3.1 新判据另开接口、`scan()` 不变、不加 `IGNORE`；B0「测试」一行 |
| 3 | B2 删 `--sidebar-width` 别名，写入方在 B3 | 2.4 第 3 条；4.1 例外；B2 同提交改 `split-handle.tsx:75`，加拖宽场景 |
| 4 | `css-var.ts` 的调用方在别的批次 | B2 只新增并再导出；调用方随批改；B8 删再导出 |
| 5 | B2 改 Button 映射会提前改变 B6 / B7 画面，并让 H27 规则失效 | B2 只删未用档；映射随调用方；V10 移到 B4；2.3 H27 选择器 |
| 6 | 减少动效的例外写死 `.animate-spin` | 2.3 减少动效；5.2 不归零的减少动效场景；风险 10 |
| 7 | R14 误拦运行时变量、引用检查误判模板拼接 | 1.1 运行时层；2.2 运行时层；2.4 第 4 条；R14 |
| 8 | JS 读 CSS 变量的加载时序 | 2.6；5.2 第二种启动方式；风险 9 |
| 9 | R4–R12 误拦与漏拦 | 1.2 表（排版关键字、字重、变换与环、`fr`/`minmax`、后置 `!`）；R6、R8、R9、R10、R15；3.3 用例 |
| 10 | R13 只管长写属性，简写和 `@apply` 漏掉 | 1.2 补充；R13；2.1 形象动画与 `.cc-spin` 时长登记 |
| 11 | 断点写法三四种、判定不等价 | 2.1「断点」；B1 `use-mobile.ts`；3.3；风险 3；767.5 场景 |
| 12 | `sm:` 改 `md:` 改变 640–767 段画面 | V36；5.2 加 tablet-portrait、desktop-narrow |
| 13 | 场景漏了根节点状态和主语言 | 5.2 根节点状态与泰文 |
| 14 | tw-shimmer、collapsible、tw-animate 收尾；V5 描述不全 | 2.1 不登记 `--cc-shimmer-dur`、B1 删残留；B8 删依赖；V5 补透明度区间 |
| 15 | 组件里的 `pointer-coarse:` 与第三套触屏判定 | R11；V3 补 `thread.tsx:748`；3.4 三种判定各管什么（`touch.ts` 保留 `hover: none`） |
| 16 | C 类交用户拍板与用户口径冲突；V21 分类不对 | 0 第 7 条；4.1；第 7 节按依据定稿；V21 改 A |
| 17 | 删 `.cc-switch` 与 V19 复用冲突 | 2.4 第 2 条；2.3 开关；V19 |
| 18 | `NOTICE.md`、`components.json` 仍要求命令重新生成 | 1.3；3.4；B8 |

## 10. 写本记录时核对过的引用

以 803c483b 为准，回仓库逐项核对了方案引用的文件、行号和变量名，修正了这几处：

- H27 手机弹窗按钮规则在 globals.css 第 1233–1238 行（phone 变体两行 + coarse 两行），不是 1236–1240；
- `.cc-dialog` 已存在（globals.css:1739，只有弹出阴影），新类在它上面补，不是新建同名类；`.cc-modal` 继续负责居中；
- `--sidebar-width` 定义在 `components/ui/sidebar.tsx`、`--thread-max-width` 定义在 `thread.tsx:124`，都不在 globals.css；
- 「整窗弹窗 16」的出处是 globals.css:562 `--cc-radius-dialog` 的注释（外壳实测），ccs.md 里没有这句；
- `use-mobile.ts` 的 `MOBILE_QUERY` 现为 `(max-width: 767px)`；变体判定窄屏优先成 phone（桌面窗口也一样），所以与 `max-md` 只差查询文本，原方案「桌面窗口窄于 768 时可能不等价」的担心改为风险 3 的写法；
- 原方案说 `cc-scroll-fix` 改了 ccs.md；它已提交的改动只有 `thread.tsx`、`lib/follow-scroll.ts`。B7 必须等它；B1 是否还要等，开工前按它最终改到的文件定；
- 菜单 `sideOffset` 不只在 B3 / B6：`companies.tsx`、`company-page.tsx`、`scheduled-tasks.tsx`、`settings.tsx`（B4）也有，V8 批次补上 B4；
- 其余引用（`assistant.tsx:98 / 128 / 129 / 1046 / 1058`、`thread-list.tsx:1074 / 1221`、`tooltip-icon-button.tsx:27`、`thread.tsx:360 / 748`、`split-handle.tsx:42 / 75`、`drag.tsx:103`、`settings.tsx:52`、`scheduled-tasks.tsx:863 / 895 / 995`、`attachment.tsx:115`、`sidebar.tsx:486`、globals.css 3 / 838 / 867 / 1368 / 1534 / 1931 / 2220 / 2229–2241 / 2263、`test_cc_ccs.py:400 / 495–560`、`test_cc_run_indicator.py:433`、`test_cc_mobile_overlays.py:170`、`test_cc_session_panel.py:344`、`ccs.md:51`、Tailwind 4.3.3、无人引用的 `--cc-fill-hover / -selected / --cc-dur-instant / --cc-line-stronger`）与仓库一致。
