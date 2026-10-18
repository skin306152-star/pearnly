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
| B2 组件库 | `pearnly/cc-std-b2`（基线 `pearnly/cc-longpress-race` 顶端 93a10475，开发窗口指定；工作树 `.claude/worktrees/cc-std-b2`），提交见下面「B2 做了什么」；纯替换 8bc08765 对基线全矩阵 773 个场景 0 样式差、0 像素差（报告里 1 个悬停残留经前后各复拍 2 次判为基线那一遍离群），画面变化 V5 / V7 / V9 / V11 / V18 / V38 逐个比对只差预期的项（V38 是 B2 按 4.1 补进第 7 节的）；本地全闸门全过。比对报告 `~/pearnly-devtools/cc-std-b2/reports/`，前后图 `~/pearnly-devtools/cc-std-b2/visual/`。10-18 晚变基到 origin/main 2a558d14 → 84c644f6，本地全闸门 100 步过、分支 CI 两条过；lab 挂 84c644f6 由测试窗口验收**六项全过、回归无变化**（见「B2 测试窗口验收」）；**待用户手机真手指走预览与长按后合入 main**；未部署 |
| B3–B8 | 待开工 |

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
- 没测：用户手机真手指（预览、长按）；平板竖屏、Windows 触屏。
- 测试窗口的观察（都不是 B2 引入，main 上同样）及去向：
  1. 「语言 ›」「了解更多 ›」`assistant.tsx` 写死 `sideOffset={8}`，量出离父菜单 4，不是 6——已在 V8（B3：子菜单 6）里，B3 照做；上面验收项第 5 条「子菜单离父菜单 6」指 `menu.tsx` 默认值，这两处调用方 B3 前仍是 4。
  2. `companies.tsx:150`、`scheduled-tasks.tsx:440` 的加载卡片仍是 `animate-pulse`——补登进 V5，B4 照做。
  3. 手机打开图片预览时关闭钮带 `:focus-visible` 蓝框（组件库打开弹窗自动聚焦关闭钮）——B2 没动焦点；等用户手机看过是否碍眼再定，要改归 B7（`attachment.tsx` 调用处）。
  4. adb `input swipe` 模拟长按不出菜单是工具问题（三星按下同一毫秒就发 contextmenu），调试接口触摸事件正常；真手指交用户确认。

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
| V3 | B1 / B4 / B7 | 三条 coarse 规则（`.cc-tap`、`.cc-card-btn`、`.cc-choice-*`）没有排除桌面窗口；`.cc-chip-btn` 没有手机 44 档；`thread.tsx:748` 用户消息「复制」条用 `pointer-coarse:hidden` | 触屏 Windows 上的 CC 桌面版会变 44、复制条被藏；手机上筛选胶囊只有 28 | 按 ccs.md「不改电脑值」排除桌面窗口，复制条改按 `data-cc-touch`；胶囊用伪元素把点按区补到 44，外观不变 | A |
| V4 | B1 | phone 与 coarse 成对规则中不一致的几对 | 只补了一边 | 以完整的一边为准 | A |
| V5 | B2 / B4 | 骨架脉动（B2：`ui/skeleton.tsx`；B4：`companies.tsx:150`、`scheduled-tasks.tsx:440` 直接写的 `animate-pulse` 加载卡片，B2 验收时测试窗口发现、补登） | 2s，Tailwind `pulse`：透明度 1↔0.5 | `--cc-pulse` 1.2s，透明度 0.35↔1（同运行中脉冲点） | B |
| V6 | B1 / B4 | 不可用透明度 | 0.35 / 0.4 / 0.5 | 统一 0.4（实测菜单不可用 40%） | B |
| V7 | B2 | 提示框层级 | 50，被菜单 60 盖住 | `--cc-z-tooltip` 90 | A |
| V8 | B3 / B4 / B6 | 菜单离触发器的距离 | 4 / 6 / 8 混用（主菜单约 9 处 4、`assistant.tsx` 有 6 和 8，`period-chip`、`projects` 用 6） | **主菜单 4、子菜单 6**。依据：ccs.md 菜单规格是一套，同类菜单不该三种距离；距离本身 Claude 没有实测，取现有多数（主菜单 4）和 `menu.tsx` 子菜单默认值 6，登记「现有值·CC 自定」 | 原 C·已定 |
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
| V28 | B6 | 「加载更多」行和骨架行的高度 | 固定 32 | `--cc-row-h`：桌面窗口 30、手机 44 | A |
| V29 | B6 | 会话行「···」与行过渡 | shadcn Button，字色 ink-2，150ms | `.cc-row-btn`，字色 ink-3，120ms | A |
| V30 | B7 | 回复下的「复制」和代码块的「复制」 | 圆角 8、深色悬停半透明、150ms、按下 0.9；代码块的没有 cc-tap | `.cc-row-btn`：圆角 6、120ms、按下 0.94，加 cc-tap | A |
| V31 | B7 | 发送中气泡的文件条 | 自写的一条，落地时会跳一下 | `.cc-attach-card` 加骨架 | A |
| V32 | B7 | MessageError 的 `mt-2` 盖掉了标准块间距 | 8 | `--cc-md-block`（0.75em） | A |
| V33 | B7 | 代码块 | 行高 1.625（`leading-relaxed`）；圆角 12（`rounded-xl`），对话里的表格外圈、步骤明细卡是 10 | **行高 20**（`--cc-code-lh`，实测正文 14/20）；**圆角统一 10**：按 ccs 现有档位，对话里的块级卡片（表格外圈、步骤明细卡 `--cc-radius-step-card`、用户气泡、附件卡）都是 `--code-radius-10`，代码块登记 `--cc-radius-code-block: var(--code-radius-10)` | 原 C·已定 |
| V34 | B7 | 复制图标切换的时长 | 150 / 200，Tailwind ease-out | `--cc-dur-fast` / `--cc-dur`，加 `ease-cc-out` | B |
| V35 | B7 | 附件：悬停效果叠了两层；上传遮罩有 1px 模糊 | 两种悬停叠加；有模糊 | 只留 `.cc-compose-tile` 的悬停；去掉模糊 | A |
| V36 | B2 / B3 / B4 / B7 | `sm:`（640）改 `md:`（768）：`dialog.tsx` 的 `sm:text-start / sm:flex-row`、图片预览的 `sm` 断点、`assistant.tsx:128` 形象 `sm:h-28`、`scheduled-tasks.tsx` 863 / 895 / 995 行的 `sm:grid-cols-2`、`attachment.tsx:115` 的 `sm:max-w-3xl` | 640–767 宽（iPad mini 竖屏 744、CC 桌面版窗口拉窄）按宽屏排 | 只有一条断点 768（与变体同一条），这一段按手机排；640 没有 Claude 依据。验证用 tablet-portrait、desktop-narrow 两种环境 | A |
| V37 | B1 | 手机 / 电脑分界的查询文本：`hooks/use-mobile.ts` 的 `MOBILE_QUERY` 是 `(max-width: 767px)`，`max-md:` 是 `(width < 48rem)` | 浏览器缩放出 767.5 这类小数宽度时变体判成 web、`max-md:` 的类却生效：侧栏已收起、却没有手机顶栏，设置等入口点不到（B0 比对 web-767.5 实测） | 三处同一段文本 `(width < 768px)` / `(width >= 768px)`（2.1「断点」），767.5 宽按手机外壳排；测试用项目自己的 Tailwind 现编 globals.css 核对 | A（B1 比对时补进本节） |
| V38 | B2 | 组件库弹窗（`dialog.tsx`，现在只有图片预览用）的遮罩与弹层进出场 | tw-animate：遮罩淡入 150ms、弹层淡入放大（0.95）200ms，`ease` | `.cc-overlay` / `.cc-pop`：快档 120ms、主缓动（同 V15 遮罩、V18 提示框）；组件库文件不再用 tw-animate，B8 才删得掉这个依赖 | B（B2 开工时按 4.1 补进本节：记录把 dialog 的动效漏了，不改就过不了 R8、也删不掉 tw-animate） |

## 8. 批次清单

| 批 | 名称 | 依赖 | 画面变化 |
| :- | :- | :- | :- |
| B0 | 闸门与比对工具骨架 | 无（现在开工） | 无 |
| B1 | 标准层（globals.css、@theme、touch 标记、断点、twMerge） | B0、cc-drag-perf 合入、cc-scroll-fix 合入 | V2、V3、V4、V6、V20（B1 部分无可见变化）、V37 |
| B2 | 组件库与共用 lib | B1 | V5、V7、V9、V11、V18、V36（无可见变化，见 B2 小节）、V38 |
| B3 | 外壳 | B2 | V1、V8、V16、V36 |
| B4 | 弹窗与列表页 | B2 | V1、V3、V6、V8、V10、V12、V14、V15、V17、V19、V20、V21、V22、V36 |
| B5 | 会话栏、结果、文档、工具卡 | B2 | V1、V12、V13、V23–V27 |
| B6 | 侧栏列表（拖动分支的文件） | B2、cc-drag-perf 合入 | V1、V8、V28、V29 |
| B7 | 对话区（滚动分支的文件） | B2、cc-scroll-fix 合入 | V1、V3、V12、V13、V30–V36 |
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
