# CC 前端标准架构设计（2026-11-07）

- 用户原话（11-07，看过 [dsh 评估](2026-11-07-cc-to-dsh-评估.md) 后）：「那仓库的事等一下，你按我们的思路重新搭建框架，先给我把架构搭建好，做成一个标准的开发架构」。dsh 不搬。
- 本文是**第一步：设计**，交主会话审定。审定后才进行第二步：按设计建目录、基础层、组件目录页和检查规则，现有功能不受影响。
- 范围：CC 前端 `frontend/assistant`。电脑网页、手机、LINE 内置浏览器、CC 桌面版（QtWebEngine）共用这一份；Cowork、ERP 两种站点也共用。设计和最终放在哪个仓库无关，下文路径都写成相对前端根目录 `src/`。
- 基线：`origin/main` `8e5cb572`。现状数字来自本次只读勘察（123 个文件，约 3.46 万行）。
- 不改后端接口。唯一的后端改动是第 3.3 节的标题时机，单独列出。

## 0. 结论

现有前端已经有一部分标准件，这次不推倒：
- 设计变量只在 `globals.css`，闸门 R1–R15 拦写死的尺寸、颜色、字号（[ccs.md](../development/ccs.md)、`scripts/check-frontend-tokens.py`）；
- 截图比对，7 种环境 × 浅深色（`scripts/cc-visual/`）；
- 产品地图、共用件闸门、全套流程。

缺的是另外五样：
1. **分层和依赖方向**：组件里直接调接口（7 个文件），Cowork 的接口地址写死在组件里（8 个文件），按产品名分支（7 处）。
2. **业务模块边界**：定时任务、设置、公司卡片各自取数、各自画状态。
3. **「只此一份」的标准件**及其强制：对话行、分组规则、卡片接口、状态显示。
4. **组件目录页和组件测试**：现在前端没有一个单元测试。
5. **Claude 参考图清单**：「照 Claude 做」现在凭文字记录，参考图不在仓库里，没法逐项对照。

做法：在现有应用里逐块替换，新旧并存、功能不断。每块先做「画面不变」的搬移，用截图零差证明；再单独提交「照 Claude 改」的画面变化，每处附参考图。估算写代码约 55–75 小时（机器耗时），机器跑约 10–15 小时，另加两轮真机（第 5 节）。

## 1. 目标

1. **界面和交互照 Claude 做，凭实测依据**：每个界面元素对应一张 Claude 参考图（截图或录屏）。没有参考图的标「待用户提供」，不凭想象做（记忆「不知道 Claude 怎么做就说不知道」）。
2. **直写、代录同一套对话框架**：代录任务就是标准会话，两种模式只差执行方式（记忆「Code 版 CC」）。
3. **Cowork、ERP 同一套**：前端不认产品名，差别全部来自服务端声明（`projects` 的 `kind / api / sections`、`profile`）。
4. **四端都能用**：电脑网页、手机、LINE 内置浏览器、CC 桌面版。平台差别只在平台层。
5. **以后不再出现「两套」「写死」「各做各的」**：新功能只能接到标准件上，检查规则在提交时拦住。

不做：不换框架（React 19 + assistant-ui + Tailwind 4 不变），不改设计变量的值（取值规则照 ccs.md），不改后端接口（3.3 除外），不做过渡版。

## 2. 分层与目录

```
src/
  app/          入口与装配：main.tsx、路由与页内导航、把各业务模块登记进外壳（唯一知道「有哪些业务模块」的地方）
  platform/     L0 运行平台：CC 桌面版桥、视口与键盘、触屏与悬停、变体、返回键栈、网页推送、安装、LINE 内置浏览器判定
  design/       L1 设计：tokens/（globals.css 拆出的变量与 cc-* 类，值不变）、ui/（按钮、图标钮、菜单、弹窗、提示框、骨架、开关、
                页签、表格、出错框、提示条、转圈、拖动、分隔条）、i18n（文字入口）
  data/         L2 数据：api/（类型化接口客户端）、events/（Managed Agents 事件类型、折叠成消息的 reducer、本站扩展事件）、
                live/（实时推送 + 补拉 + 多设备同步）、stores/（缓存与界面状态）
  chat/         L3 对话核心：thread（消息列表）、message（单条消息）、composer（输入框）、attachments、status（状态行）、
                title、cards/（卡片接口 + 通用卡片：提问、允许、文件、提醒、步骤）
  shell/        L4 外壳：sidebar（分组规则 + 唯一的对话行）、topbar、side-panel（右侧栏框架与卡片插槽）、settings（设置框架与分区插槽）、
                mobile（抽屉、手机外壳）
  features/     L5 业务模块，每个一个目录：company、operator、precheck、scheduled-tasks、knowledge、folders、connections、
                month-ledger、mrerp-login、install-prompt
  catalog/      组件目录页（开发用，不进生产产物，见 4.1）
```

### 2.1 依赖规则（检查脚本照这张表判）

| 层 | 可以依赖 | 不能依赖 | 额外规则 |
|---|---|---|---|
| `platform` | 浏览器、`window.qt` | 其他所有层 | 全仓只有这里能碰 `window.qt`、`window.tomoDesktop`、`navigator.userAgent`、`matchMedia`；对外只给 `capabilities()`（有无悬停、是不是桌面版、是不是 LINE 内置浏览器、能否推送……）和桥的类型化方法 |
| `design` | `platform`（变体、触屏） | data / chat / shell / features | 不取数、不认业务词；外观只走变量与 `cc-*` 类（现有 R1–R15 不变） |
| `data` | `platform` | design / chat / shell / features | 不出现 JSX；全仓只有 `data/api` 能发请求（`frappe.call`、`fetch`）；产品接口前缀只读服务端声明，不写模块路径 |
| `chat` | design、data、platform | shell、features | 业务卡片经 `cards` 登记表接入，chat 不引用具体业务 |
| `shell` | design、data、platform、chat | features | 右侧栏卡片、设置分区、侧栏行状态都经插槽接入 |
| `features/*` | design、data、platform、chat 的卡片接口、shell 的插槽接口 | 别的 feature | 模块之间要通信就经 data 的 store 或事件，不互相 import |
| `app` | 全部 | — | 只装配，不写界面 |

全仓规则：
- 不写产品名分支（`product === "cowork"` 这类），要差别就让服务端声明一项能力；
- 不写 Cowork / ERP 的模块路径；
- 不在 platform 之外判断平台。

### 2.2 现有文件去哪（主要的）

| 现在 | 去 |
|---|---|
| `lib/desktop.ts`、`viewport.ts`、`touch.ts`、`variant.ts`、`back-stack.ts`、`push.ts`、`install.ts`、`notify.ts`、`hooks/use-mobile.ts` | `platform/` |
| `globals.css`、`components/ui/*`、`lib/css-var.ts`、`i18n.ts` | `design/` |
| `lib/frappe.ts`、`lib/managed-agents/api.ts`、`events.ts`、`reducer.ts`、`realtime.ts`、`poll-source.ts`、`list-sync.ts`、`prefs-sync.ts`、`companies.ts`、`month.ts` 里取数的部分 | `data/` |
| `components/assistant-ui/thread.tsx`、`attachment.tsx`、`markdown-text.tsx`、`components/tool-uis/steps.tsx`、`ask-*`、`approval-*`、`lib/step-*`、`turn.ts` | `chat/` |
| `assistant.tsx`（1,197 行，拆）、`thread-list.tsx`（1,581 行，拆）、`projects.tsx`、`sidebar-*`、`session-panel.tsx`、`settings.tsx` 的框架部分、`mobile-shell.tsx` | `shell/` |
| `company-cards.tsx`、`month-ledger.tsx`、`scheduled-tasks.tsx`、`operator-*`、`components/operator/`、`mrerp-login.tsx`、`settings.tsx` 里连接 / 文件夹那几段 | `features/<模块>/` |

## 3. 统一规则（只此一份的标准件）

新功能只能接到这些标准件上，不能另做一份。检查规则见 4.3。

### 3.1 一个对话行组件 `shell/sidebar/ThreadRow`

侧栏、搜索结果、手机抽屉、直写、代录都用它。
- 行的组成固定：前导图标、标题、尾部状态、行尾「···」。
- 状态只读 3.5 的运行状态，不能自带转圈或圆点。
- 行菜单只有一份登记表：置顶、改名、删除……业务要加菜单项就往登记表里加。
- 现有 `.cc-thread-row` 只允许出现在这个组件里。

### 3.2 一个侧栏分组规则 `shell/sidebar/grouping.ts`

输入是服务端的会话列表 + `projects` 声明 + 本机的视图偏好，输出是分组树。
- 公司分组、置顶、排序、折叠、拖动都由这一个纯函数决定。
- Cowork、ERP、DMS 的差别只来自声明（DMS 没有 `kind`，就不分组）。
- 有单元测试。
- 现在分散在 `projects.ts`、`sidebar-groups.ts`、`sidebar-order.ts`、`sidebar-view.ts`、`sidebar-fold.ts` 五个文件，要收拢。
- 「分组和排序」菜单现在只在 Cowork 出现，以后改成由声明决定。

### 3.3 一个标题规则

- **服务端定标题，前端只显示。** 前端唯一的本地逻辑：服务端标题还没到时，按服务端同一规则显示首句（现 `session-list-adapter.ts` 的 `generateTitle`）。
- 时机改为**第一句发出后马上总结**（主会话所列规则；dsh 默认也是这样，见评估第 4 节）。
  - 现在是第一轮结束后才总结（`relay.py` `_turn_ended` → `titles.turn_ended`），还参考 CC 的回复。
  - 要改成：`sessions.py` 建会话写临时标题时直接排 `titles.request`，总结只用首句。
  - **这是后端改动**：约 1–2 小时，加回归测试，单独一个提交。
- 改名后不再自动覆盖（现状不变）。

### 3.4 一套卡片接口 `chat/cards`

```
defineCard({ tool, placement: "step" | "card", render, answer? })
```

- 每张卡片都有同一套阶段：等待、已答（所选那一项）、已取消、失败。这套阶段现在在 `card-phase.ts`，以后升为标准。
- 外框用同一个 `CardShell`：按钮最多 2 个、底部靠右、`.cc-card-btn`。
- 回答都经同一个接口送出（`data/api` 的 confirm / answer），以服务端结果为准。
- 提问卡片 ask_user、允许卡片、代录的开始前 / 中途选 / 继续、预选卡、上传卡、结果文件卡都按这个接口实现。
- 现在的 `tool-uis/index.tsx`（toolkit 登记）就是这张登记表的雏形，以后 features 往里登记，chat 不再 import 代录组件。
- 卡片排版规矩照 ccs.md 第 2 节。

### 3.5 一套状态显示 `data/stores/run-status` + `chat/status`

- 运行状态只有一个数据源（现 `turn.ts`、`progress.ts`、`stop-run.ts` 合并）：空闲、运行中（当前动作、计时）、等你输入、出错、连接中断。
- 对话里的状态行、侧栏行尾、标题栏、手机浮层都只读它。
- 业务模块不自己写「正在……」，要显示进度就往运行状态里写当前动作。
- 显示规则照 ccs.md 第 3 节「工作状态行」，代录复用同一套（11-04 用户口径）。

### 3.6 插槽：右侧栏、设置、侧栏行

- 右侧栏 `shell/side-panel`：服务端 `sections` 声明有哪些卡片，features 按 section 键登记渲染器；没登记的不显示；空卡隐藏。
- 设置 `shell/settings`：同样按分区登记。
- 侧栏行尾：只能放 3.5 的状态。

### 3.7 一个接口客户端 `data/api`

- 每个后端方法一个有类型的函数，现在 `lib/managed-agents/api.ts` 的写法推广到全部接口。
- 组件不直接 `call`。
- 产品接口（公司、月份、定时任务、连接）用服务端声明的前缀：`projects.api` 现在已经这样做，`scheduled-tasks.tsx`、`settings.tsx` 等写死的 `pearnly_cowork.*` 迁过来；ERP 没有的那几项由声明决定不显示。

## 4. 开发规范

### 4.1 组件目录页

- 地址：开发环境 `/assistant/catalog`。生产构建不打进去（Vite 单独入口，`vite build` 默认不含）。
- 每个标准件旁边放一个 `*.catalog.tsx`，列出它的各种状态：空、加载、正常、长文字、出错、禁用、触屏、深色，卡片的四个阶段，对话行的各种状态。
- 用现有的假服务端（`scripts/cc-visual/server.mjs`）喂数据，不连真站点。
- `scripts/cc-visual` 加「目录页」场景：每个组件的每个状态在 7 种环境 × 浅深色下各截一张，和基准比对。
- 做法：**自建，不引入 Storybook**。理由：
  - 不加一整套新依赖和第二套构建；
  - 直接复用现有的变量、变体判定和截图环境；
  - 目录页本身就是一个普通页面，桌面版和手机也能打开看。
  - 代价：没有 Storybook 的插件生态。如果主会话更想要 Storybook，第二步改用它，工作量多约 2–3 小时。

### 4.2 类型检查

- 保持 `strict`（现状），`npm run build` 前跑 `tsc --noEmit`（现状）。
- `data/api` 的返回类型和后端契约对齐，改接口时两边一起改。

### 4.3 代码检查（提交钩子与 CI 同名一条）

新增 `scripts/check-frontend-layers.py`，与现有 `check-frontend-tokens.py` 并列。用 Python 写，不加 npm 依赖，照仓库现有闸门风格。规则：
- L1：跨层 import 违反 2.1 的表；
- L2：`data/api` 之外发请求；
- L3：`platform` 之外碰 `window.qt`、UA、`matchMedia`；
- L4：产品名分支、写死产品模块路径；
- L5：标准件的标志类（`.cc-thread-row`、`.cc-card-btn` 的卡片外框、状态行的类）出现在标准件之外，也就是另做了一份；
- L6：新建的标准件没有目录页文件；
- L7：feature 之间互相 import。

上线方式照仓库惯例：
- 先出报告；
- 存量登记进基线，**基线只许减少**；
- 迁到哪块，哪块的基线就清零；
- 不设行内禁用。

现有 R1–R15（尺寸、颜色）、`check-frontend-i18n.py`、`check-frontend-viewport-units.py`、`check-copy.py` 照旧。

### 4.4 测试

- **纯逻辑单元测试**：加 Vitest，新开发依赖，版本锁定进 `package-lock.json`，CI 加一步。
  - 首批覆盖分组规则（3.2）、事件折叠 `reducer.ts`、运行状态（3.5）、步骤摘要 `step-summary.ts`、标题显示规则。
  - 现在前端零单测，这几块最容易回归。
- **组件测试**：靠目录页截图比对（4.1），不在 jsdom 里测画面。jsdom 不画 CSS，测不出用户看到的差别。
- **端到端**：现有 `scripts/flows/cc_*.py`、实验栈流程、`tests/test_cc_*.py`、`tests/test_assistant_*.py` 照旧；搬文件时同一提交改它们读的路径。

### 4.5 Claude 对照清单

- 存放：`docs/reference/claude-ui/`，清单文件 `claude-ui.toml`，图片放在同目录。
- 每行一个界面元素，字段：
  - `id`（如 `sidebar.row.hover`）
  - 参考图路径
  - 来源（截图 / 录屏、日期、Claude 哪一端、版本）
  - 对应的 CC 标准件
  - 目录页里的状态名
  - 状态：已对照一致 / 有差异（写差在哪）/ **待用户提供**
- 没有参考图的写「待用户提供」，**不能写成「照 Claude」**。照 Claude 改画面的提交必须引用清单里已有图的那一行（4.3 加一条检查：改动说明里引用的 id 必须有图）。
- 首批：把 [CC 外壳设计](2026-10-16-cc-shell-design.md) 和 ccs.md 里已经引用过的参考（1.webp–8.webp、IMG_5192、10-27 实录 `~/Downloads/Claude操作程序演示/`）找回来登记。这些图现在不在仓库里，找不到的标「待用户提供」。外壳设计第 93 行写明，有几张图来自 DeepSeek 桌面端，不是 Claude。这几张要单独标出，不算 Claude 依据。

## 5. 过渡：逐块替换

规则：
- 每块两个提交：先「纯搬移、画面不变」，截图零差 + 流程绿；再「照 Claude 改」的画面变化，附清单 id。
- 新旧并存：旧文件搬走后，原路径不留壳；同一提交改完所有调用方和测试读的路径。
- 每块单独分支，合入时机由主会话定；不做一次性重写。
- 先清零该块的层检查基线，再接下一块。

| 块 | 内容 | 写代码（机器耗时） |
|---|---|---|
| M0 骨架（第二步） | 建目录；`check-frontend-layers.py`（报告 + 基线）；目录页框架和 3–5 个基础件的目录文件；Vitest 接入；`claude-ui.toml` 格式和首批登记；现有功能不动 | 6–8 小时 |
| M1 平台层 | desktop / viewport / touch / variant / back-stack / push / install 收进 `platform`，加 `capabilities()` 和 LINE 内置浏览器判定；19 处平台判断改用它 | 4–6 小时 |
| M2 数据层 | 全部接口进 `data/api`；7 个直接调接口的组件、8 个写死 Cowork 地址的文件改用声明前缀；7 处产品名分支改成服务端能力声明（可能要服务端多声明一两项能力，只加声明、不改接口） | 6–8 小时 |
| M3 设计层 | `components/ui` → `design/ui`；`globals.css` 拆成变量和类两份（值不变，截图零差）；每个基础件一个目录文件 | 5–7 小时 |
| M4 状态与卡片 | 运行状态合一；`CardShell` + `defineCard`；提问、允许、代录、预选、上传卡迁过去 | 6–8 小时 |
| M5 侧栏 | `ThreadRow` 唯一；`grouping.ts` 收拢五个文件 + 单测；`thread-list.tsx` 拆分；标题时机的后端改动（3.3） | 8–10 小时 |
| M6 对话核心 | `thread.tsx` 拆成消息 / 输入框 / 状态行 / 附件 | 6–8 小时 |
| M7 插槽与业务模块 | 右侧栏、设置插槽；公司卡片、月结台账、定时任务、连接、文件夹、知识库、MR.ERP 登录迁进 `features/` | 10–14 小时 |
| M8 外壳 | `assistant.tsx` 拆成 `app/` + `shell/` | 4–6 小时 |
| 合计 | | **约 55–75 小时** |

- 机器跑：每块截图比对（14 组）约 15–20 分钟，加受影响流程；M4 以后每块跑一次全套（本机约 30 分钟）。合计约 10–15 小时。
- 真机：M1（平台层）和 M8（外壳）之后各一轮，覆盖三星手机、LINE 内置浏览器、iPhone、测试机 CC 桌面版。动测试机先问主会话。
- 顺序理由：先下层后上层，每块只依赖已经整理好的层。M1–M3 是纯搬移，用户看不出变化；从 M4 起逐步出现照 Claude 的改动，每处都有参考图。

## 6. 和产品地图、共用件闸门、全套流程的配合

- **产品地图**：CC 视图行（`ccview:*`）和 `// map:` 注释跟着代码走。搬文件的同一提交里 `check-product-map.py` 必须绿：锚点路径更新、页面跳转注释不丢。新增或删除用户能碰到的入口，照规矩同一提交更新地图。
- **共用件闸门**：
  - `scripts/impact/shared-parts.toml` 里 CC 的共用件（`cc-sidebar-list`、`cc-composer`、`cc-drag`、`cc-desktop-bridge`、`cc-prefs-sync`、`cc-assistant-settings`、`cc-split-handle`……）路径在搬移的同一提交改；
  - 新标准件 `ThreadRow`、`CardShell`、运行状态、`data/api`、`platform` 桥登记为共用件，写全入口；
  - 改到它们时 `check-impact.py` 出影响清单，逐入口实测记「通过 / 不通过 / 没测到（原因）」。
- **全套流程**：
  - 每块合入前跑受影响流程；M4 以后每块跑全套（本机约 30 分钟，记忆「流程测试留本机」）；
  - 回归要比对应有状态（记忆「回归要比对应有状态」）：流程断言业务结果，不只是页面能打开。
- **文案**：纯搬移不改文案；照 Claude 改动若涉及已有文案，照规矩先给用户看清单。
- **共用层改动**：3.3 的标题时机改的是 `pearnly_core`，合入前按「共用层候选检查」在生产镜像跑完整离线检查。

## 7. 待主会话定

1. 目录页自建，还是用 Storybook（建议自建，4.1）。
2. 加 Vitest 这个新开发依赖（建议加，4.4）。
3. 标题时机改为「第一句发出后马上总结、只用首句」，包含一处后端改动（3.3）。主会话的任务书已列为规则，这里再确认一次它算后端改动。
4. Claude 参考图：已引用过的图请用户帮忙找回或重拍；新元素的图由用户提供。没有图的元素不改画面。
5. 第二步（M0 骨架）是否就在本分支做，还是另开分支。
