# CC 开着不操作也耗电：首页小猫的无限循环动画

## 用户口径

「CC 桌面版必须要轻量设计并满足老机器能正常运行使用，架构不对就重构架构」。CC 网页（`frontend/assistant`）同时用于电脑浏览器、手机主屏应用、CC 桌面版窗口，三端一起改。
不改小猫的造型与动画内容本身，只改「循环多久、何时停」；不改文案。

## 任务信息

| 项 | 值 |
| :- | :- |
| 分支与工作树 | `pearnly/cc-idle-anim`，`.claude/worktrees/cc-idle-anim`，自己的 `node_modules`（`npm ci --prefix frontend/assistant`、`npm ci --prefix frontend`） |
| 基线 | `pearnly/cc-std-b1` 顶端 f1978271（H 线 → 第 13 轮 → B1 标准层，globals.css 已按 B1 标准层重排）；没有碰 cc-std-b1 工作树（测试机 lab 正挂着它） |
| 提交 | cea861cd `perf:` 形象动画播几轮就停；d5e3eb1d `test:` 测试与闸门；本记录 `docs:` |
| 状态 | 未推送、未合入 main、未部署；待测试窗口在测试机复测（见最后一节） |
| 实测工具 | `~/pearnly-devtools/cc-idle-anim/`：`measure.mjs`（静置耗电）、`frames.mjs`（画面逐像素对比与时间线）、`delta.mjs`（差值分布）；改前 / 改后产物 `dist-before`（index.BGIXqnLZ.js，与测试机同一个包）/ `dist-after` |

## 现象（测试窗口 10-18 在测试机实测）

- CC 桌面版窗口开着不操作仍占约 0.8 个核：窗口进程 48–51% + 网页内核 30–33%，Intel 显卡驱动两条线程每秒唤醒 650 / 517 次；锁屏后也不降。
- 同一页面（`/assistant` 首页，包 index.BGIXqnLZ.js）`document.getAnimations()` 查到首页小猫 7 个无限循环的 SVG 动画：cc-tail 2.6s、cc-calm 8s、hm-blink 5s、cc-laugh 8s、cc-bell 2.2s、cc-hold 5s、cc-tick 5s；rAF 每秒 121 次。手机和网页上一样一直在播。

**原因**：这 7 个动画都在形象图（`src/brand/cc.svg`）里一个带铅笔滤镜（`feTurbulence` + `feDisplacementMap`）的组里。SVG 的 `transform / opacity / stroke-dashoffset` 动画不走合成器，
每一帧都要重算样式、重排、重画整只带滤镜的猫；无限循环就是一直每秒 60 帧。本机无界面 Chrome 复现：静置时主线程每秒 60 帧、每帧两次绘制（每秒 120 次，与测试机「rAF 每秒 121 次」对得上——页面代码自己排的 requestAnimationFrame 是 0）。

## 常驻动画清单与处理

**装饰性（改成有限轮）**——都在 `globals.css`「产品形象小单」，挂在形象图的 `<g class>` 上：

| 动画 | 元素 | 时长（照搬宣传页） | 轮数（新） | 一次播多久 | 静止姿势（= keyframes 结尾） | 开头 |
| :- | :- | :- | :- | :- | :- | :- |
| hm-blink 眨眼 | `.hm-eyes` | 5s | 2 | 10s（4.85s、9.85s 各眨一次） | 不变形 | 同静止 |
| cc-calm 平常脸 | `.hm-calm` | 8s | 1 | 8s | 不透明 | 同静止 |
| cc-laugh 笑脸 | `.hm-laugh` | 8s | 1 | 8s（6.0–7.1s 笑一下） | 透明（`.hm-laugh { opacity: 0 }`） | 同静止 |
| cc-tail 尾巴 | `.cc-tail` | 2.6s | 3 | 7.8s | 不旋转 | 同静止 |
| cc-bell 铃铛 | `.cc-bell` | 2.2s | 4 | 8.8s | 不旋转 | 同静止 |
| cc-hold 举牌 | `.cc-hold` | 5s | 2 | 10s | 不旋转 | 同静止 |
| cc-tick 打勾 | `.cc-tick` | 5s | 2 | 10s（画两次） | 勾画好（`stroke-dashoffset: 0`） | **不同**：0% 是还没画出来的勾，18%–100% 是画好的勾 |

轮数取「不超过 10 秒的整轮数」：节奏与 keyframes 一字不改，播完正好停在每一轮的结尾，结尾就是元素不加动画时的样子，所以停下不跳。
打勾是唯一开头与静止不同的：它本来就是「从没有画到画好」，静止停在画好（18%–100% 这一段本来就占每轮的 82%）；若停在 0% 就等于把勾去掉，而且每一轮结尾会跳一下。

**正在工作（保持无限，只在工作时挂载）**——已核对挂载条件，没有发现工作结束后还在跑的：

| 动画 | 在哪 | 什么时候在 |
| :- | :- | :- |
| cc-bob 上下浮 | `.cc-loading .pearnly-mascot-host`（加载页形象） | `ThreadLoading` 只在 `isLoading && Loading` 时挂（打开已有对话、消息取回前） |
| cc-pulse 脉冲点 | `.cc-activity[data-activity="running"]::before` | 对话行尾，这一轮在跑才有 `data-activity=running` |
| cc-spin 转圈 | `.cc-spinner` | 只在忙时挂（B2 起替换 `animate-spin`；现在还没有组件用） |
| cc-spin-dot 四点 | `.cc-spin i`（`TypingDots`） | 工作状态行 `RunIndicator`（回合结束即卸下）、步骤 `running &&` |
| cc-pulse 骨架 | `@theme --animate-cc-pulse` | 加载占位（B2 起 Skeleton 用） |
| Tailwind `animate-spin / animate-pulse` | 20 处转圈、骨架 | 每一处都在 `loading && / busy && / x === null ? / if (!x) return` 之下，逐处登记在测试里 |

**JS 定时与帧循环**（都不是常驻；逐处登记在测试里）：

| 位置 | 什么时候在跑 |
| :- | :- |
| `thread.tsx` `useClock` 每秒 | 工作状态行显示时（计时），回合结束即停 |
| `settings.tsx` 每 2 秒 / 每 10 秒 | 设置「此电脑」/ 网页连接列表开着时 |
| `scheduled-tasks.tsx` 每 3 秒 | 「连接 CC 桌面版」弹窗开着时 |
| `company-panel.ts` `useMinuteClock` 每分钟 | 公司页开着时刷新相对时间 |
| `drag.tsx` 自动滚动每 5ms | 拖动中贴着滚动区边缘时 |
| `drag.tsx` `watch`、`delete-chats.tsx` `check / wait`（自己反复排 rAF） | 键盘拖动放下后最多 5 秒；删除后最多 30 / 120 帧 |
| `mascot.tsx` 眼睛跟鼠标 | 只在指针移动时排一帧（事件驱动） |

**范围外、记下的**：启动页 `www/assistant.html` 的 `cc-boot-bob`（React 第一次渲染前的启动画面，属于「正在加载」，渲染后整块被替换）；宣传页（`website/`）与登录页（Keycloak 主题，用户 10-17 定不动）里的同一只猫仍是无限循环，不在 CC 网页里。

## 改法

- `globals.css`：7 个装饰动画的 `infinite` 换成 `var(--cc-mascot-*-runs)`；在用途层「产品形象的小动画」后登记 6 个轮数变量（注释「CC 自定」，写明轮数 × 时长）。
  新增两条规则：`.pearnly-mascot-host[data-cc-paused] :is(7 个类) { animation-play-state: paused }`、`.pearnly-mascot-host[data-cc-replay] :is(7 个类) { animation: none }`。
  减少动效设置那条 `animation-iteration-count: 1 !important` 改走 `--cc-reduced-runs`（现有值）。`prefers-reduced-motion` 那条照旧不播。
- `components/mascot.tsx`：外层按 `visibilitychange` 标 / 去 `data-cc-paused`（后台停住、回到前台接着播剩下的）；新参数 `play`：变了、而且形象里没有还没播完的动画（`getAnimations({ subtree: true })`，外层自己的上下浮不算）时，
  标 `data-cc-replay` → 读一次版面 → 去掉，浏览器按新动画从头播；还在播（含后台停住）不打断。第一次出现由浏览器自己从头播。
- `assistant.tsx` 欢迎页：问候语算成一个值，同时给 `<h1>` 和 `play`（名字取回、换了时段时再播一次；名字通常在开头 10 秒内取回，那时还在播，不打断）。
  形象只出现在欢迎页（每次回到新对话都会重新挂载、从头播）和加载页；回答中、回答完成时页面上没有形象，没有可挂的状态。
- 闸门 `scripts/check-frontend-tokens.py`：动画轮数同时长一样只能写 `var()`——`animation` 简写里不带单位的数、`animation-iteration-count`（globals.css 记 R13、任意属性记 R8、内联 style 记 R12）；`infinite` 是关键字，由测试按白名单查。
  对基线跑新闸门只多出 1 处（减少动效那条 `1 !important`），本分支已改成变量；范围外报数仍是 851 处，范围内 0。
- 测试 `tests/test_cc_idle_motion.py`（17 项，加进 Development checks）：见提交说明；变异检查 10 种（结尾不是静止姿势、尾巴 5 轮 13 秒、静止透明度改了、改回 infinite、轮数变量里藏 infinite、暂停规则漏一个、常驻转圈、没登记的定时器、自己反复排的 rAF、组件内联 infinite）都被对应的测试拦下；改前源码跑这份测试 6 项失败。

## 实测（本机 Mac，无界面 Chrome，`vite build` 整页 + `scripts/cc-visual/server.mjs` 假接口）

做法（`measure.mjs`）：打开 `/assistant` 首页，静置 15 秒后读 `getAnimations()` 里 running 的个数，再量 30 秒：页面代码自己排的 rAF / 定时器回调、`Performance.getMetrics` 的 TaskDuration 等增量、`SystemInfo.getProcessInfo` 各进程 CPU 时间；最后录 5 秒 trace 数主线程帧与绘制。
桌面版环境 = 1280×800 + 模拟桥（`window.qt`，变体 desktop）。

| 环境 | 版本 | running 动画 | 主线程帧 / 5 秒 | 绘制 / 5 秒 | 样式重算 / 秒 | 主线程忙（TaskDuration） | 网页内核 CPU | GPU 进程 CPU |
| :- | :- | -: | -: | -: | -: | -: | -: | -: |
| 桌面版 | 改前 | 7 | 300 | 600 | 60 | 54.2 ms/秒（30 秒共 1625 ms） | 94.0 ms/秒 | 134.4 ms/秒 |
| 桌面版 | 改后 | **0** | **0** | **0** | **0** | **0.7 ms/秒（30 秒共 21 ms）** | **3.5 ms/秒** | **3.8 ms/秒** |
| 网页 | 改前 | 7 | 300 | 600 | 60 | 57.6 ms/秒 | 99.4 ms/秒 | 141.8 ms/秒 |
| 网页 | 改后 | 0 | 0 | 0 | 0 | 0.5 ms/秒 | 1.4 ms/秒 | 3.4 ms/秒 |
| 手机（390×844×3） | 改前 | 7 | 300 | 600 | 60 | 52.2 ms/秒 | 93.6 ms/秒 | 132.4 ms/秒 |
| 手机 | 改后 | 0 | 0 | 0 | 0 | 0.6 ms/秒 | 2.7 ms/秒 | 3.1 ms/秒 |

- 页面代码自己排的 requestAnimationFrame：改前、改后都是 0 次 / 秒（测试机的「121 次」是浏览器每秒画的帧，见上面原因）。
- 改后剩下的约 0.4 次 / 秒 setTimeout 是 socket.io 重连（假服务端不接 socket），线上是连着的，不在静置开销里。
- 从打开起算 30 秒（含开头 10 秒播放与页面启动）：主线程 1699 ms → 519 ms，网页内核 95 → 30 ms/秒，GPU 进程 143 → 48 ms/秒。
- 时间线（`frames.mjs`，每 0.5 秒读 running）：0–7.5 秒 7 个 → 8 秒尾巴停（桌面 4 个、手机 6 个）→ 8.5–10 秒 3–4 个 → 10.5 秒起 0 个。
- 后台暂停：伪造 `visibilityState=hidden` 发 `visibilitychange`，外层带上 `data-cc-paused`、7 个动画都是 paused，3 秒后尾巴的 currentTime 不动（1083 ms → 1083 ms）；切回 visible 去掉标记、7 个接着跑。
- 回放：全部播完后照 `replay()` 的做法标 / 去 `data-cc-replay`，7 个动画重新在跑。
- 这台 Mac 比测试机快得多，绝对值只能看比例：改前每秒约 23% 个核（内核 + GPU），改后约 0.7%，不再随时间累积。

## 画面对比

逐像素、0 容差（`frames.mjs`，桌面版 1280×800×2 与手机 390×844×3，浅色；两份构建把形象的 7 个动画停在同一时刻 t，其余入场动画停在结束）：

| 比的 | 桌面版 | 手机 |
| :- | :- | :- |
| 播放中 t = 0 / 0.3 / 0.55 / 1.1 / 1.3 / 1.95 / 2.6 / 3.3 / 4.85 / 6.0 / 6.5 / 7.0 / 7.7 秒（都在最短的尾巴 7.8 秒内），改前对改后 | 13 个时刻都是 0 像素差 | 13 个时刻都是 0 像素差 |
| 改后播完的静止帧 对 改前「摘掉动画」（不加动画的样子） | **0** | **0** |
| 改后静止帧 对 改前「7 个停在 0%」 | 6321 像素差（只在猫的轮廓线上，外加勾） | 4429 |
| 改后静止帧 对 改前「6 个停在 0%、勾画好」 | 6274 | 4328 |
| 同一份改前构建自比：「6 个停在 0%、勾画好」对「摘掉动画」 | **6274**（与上一行逐像素同一批） | **4328** |
| 自检：改后 t=0 对 t=1.3 秒 | 3474（证明截图拍到了动画） | 3199 |

- 静止姿势与 0% 一致：「停在 0%、勾画好」与静止帧的 6274 / 4328 个差异像素，在改前构建里「停住的动画」对「摘掉动画」**一模一样地出现**——是浏览器给「身上挂着动画（哪怕停住）」的滤镜组换了一种光栅画法（轮廓线抗锯齿不同，差值多在 3–32、放大 4 倍看不出区别），不是姿势变了；
  姿势层面只差打勾：0% 时勾还没画出来（6321 − 6274 的那部分）。改前的猫从页面打开起一直挂着动画，所以一直是前一种画法；改后播完那一刻换成后一种画法——与改前从启动画面换成动画时同一类变化。
- 比对工具（`scripts/cc-visual/`，含形象的 welcome、loading、th-welcome 与 motion-reduce 四个场景 × 7 种环境 × 深浅 = 49 张）：改前 / 改后 **0 个有差异**（样式 0 条、像素 0、运行中的动画列表一致——减少动效场景里转圈照常转）。
  报告 `~/pearnly-devtools/cc-idle-anim/reports/before-vs-after/report.md`；逐像素对比的图与报告 `~/pearnly-devtools/cc-idle-anim/frames-desktop/`、`frames-phone/`。

## 检查

`npx tsc --noEmit -p .`、`python3 scripts/check-frontend-tokens.py`（范围外 851 处、范围内 0）、`python3 scripts/check-architecture.py`（0）、`python3 scripts/check-copy.py`（文案新增 / 改动 0）、
`tests/test_cc_idle_motion.py` 17 项、`test_cc_frontend_gate.py`、`test_cc_ccs.py` 通过；本地全闸门（`gate.sh`，照 development.yml）93 步全过。
构建产物留在工作树 `apps/pearnly_core/pearnly_core/public/assistant/`：新包 `index.2bg2ys7E.js`、`index.D0yUZnl8.css`（改前 `index.BGIXqnLZ.js`、`index.CWlxrXk3.css`）。

## 已知限制

- 实测在本机 Mac 的无界面 Chrome；测试机是 Windows + Qt WebEngine + Intel 显卡，驱动唤醒次数、锁屏后的表现要测试窗口在测试机上量。
- 鼠标在首页上移动时，眼睛跟随会让带滤镜的猫重画（事件驱动，不动鼠标就没有）；滤镜本身是造型的一部分，没有改。
- 鼠标移到猫上会笑，移开后笑脸那一组动画重新挂上、再播一轮 8 秒（改前就是这样，现在有限）。
- 回放只在问候语变化、且上一次已播完时触发；问候语在页面停着不动时很少变（名字通常在开头 10 秒内就取回了），实际多半靠「回到新对话重新挂载」再播。

## 给测试窗口的复测口径

1. 把 `pearnly/cc-idle-anim` 的工作树挂上测试机 lab（前端 SPA 构建产物要用本分支构建：`npm --prefix frontend/assistant run build`，新包 `index.2bg2ys7E.js`；或照测试环境文档带 `PEARNLY_TESTENV_LOCAL_SPA=1` 同步），确认页面里加载的是新包（不是 index.BGIXqnLZ.js）。
2. CC 桌面版打开首页（新对话，有小猫），**等 15 秒**后：控制台 `document.getAnimations().filter(a => a.playState === "running").length` 应为 **0**（打开的头 10 秒内会是 7 → 3 → 0）；`document.getAnimations().map(a => a.animationName)` 里不再有 cc-tail / hm-blink 等。
3. 窗口开着不操作静置 30 秒，任务管理器看 CC 桌面版窗口进程与网页内核（QtWebEngineProcess）CPU：改前约 48–51% + 30–33%，应降到接近 0（个位数以下）；Intel 显卡驱动线程的每秒唤醒次数应大幅下降；锁屏后同样。
4. 画面：打开首页头 10 秒猫照旧摆尾、晃铃、举牌、画勾、眨眼、笑一下；之后停住，停住时不跳；最小化再还原，没播完的接着播；点「新对话」回到首页会再播一次。
5. 手机主屏应用、电脑浏览器上同样：首页静置 15 秒后 running 为 0。正在回答时四点转圈、对话行尾的脉冲点照常动，回答结束就停。
