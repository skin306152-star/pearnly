# CC 外壳照 Claude Code 重构（第 1 批前端线，2026-10-15 夜起）

- 分支 `pearnly/cc-shell-rebuild`，工作树 `.claude/worktrees/cc-shell-rebuild`，基线 `origin/main` 7c9bb445（含 F18，未部署）。
- 规格：[Claude Code 研究](2026-10-15-claude-code-ui-study.md)（第 2 节数值、第 4 节交互清单、第 6 节方案、第 7 节用户四条）。
- 施工前的代码梳理（5 份，只读）：本机 scratchpad `map/{tokens,sidebar,rightpanel,thread,monthend}.md`，结论已并入本文。
- 证据层级：本文先写施工图；实现、本地闸门、测试机点验、部署分别在后面各节登记，缺哪层写哪层。

## 用户已定（2026-10-15）

1. 公司信息的日常位置：右侧栏卡片；主档页保留为「公司」导航下的管理页。
2. 电脑端密度照 Code（行字 14、正文 14/20、行高 28、组标题 13）。
3. 键盘这批做：⌘/Ctrl 1–9 跳转、方向键、多选。
4. 组标题纯 Code 行为：整行只折叠 / 展开，悬停才出箭头与操作；主档页从组「···」或顶部「公司」进入。

## 开发窗口按目标自定的（技术取舍，不另问）

- 只做舒适档：没有设置入口就不写紧凑档变量（不留死代码）。手机保留触屏尺寸。
- 回复字体沿用用户 10-12 选的设置，只改字号行高到 14/20（手机 16）。
- 输入框下巴放公司 / 月份 / 分店胶囊；**不做模型胶囊**（CC 没有选模型）；**不做回复中排队**（研究第 6 节方案没有，属新功能）。
- 批量操作在前端逐条顺序调用现有接口（rename / star / delete / move），不并发（F7 出现过 1213 死锁），不加服务端批量接口，不动 `pearnly_core`。
- 删除照旧不确认（用户口径：无确认步骤）。
- 右侧只有一个停靠位：会话栏（278px，不可拖宽）与文件预览（保留比例宽与分隔条）互相切换；只在 Cowork 公司对话出现，ERP / DMS 与未挂公司的对话保持原样（ADR-020）。
- 卡片顺序、折叠、开关记在本机（键带用户），不做跨设备同步。
- 菜单翻面：在包装组件里按内容完整高度选上 / 下，原方向放得下不动、反方向放得下就翻、两边都放不下取大的一边在菜单内滚动；`max-h-80` 改成 `--cc-menu-max`。
- 设计变量新增一层 `--code-*`（Claude Code 实测），`--cc-*` 结构色改指它；`--ccs-*` 官方表原样保留。深色底随之变暗（#0b0b0b / #151515），启动图、离线页、CC 桌面版窗口底色同步。

## 文案

- 新增词条（新文案，按口径不逐条问）：分组依据 / 无 / 排序 / 最近活动 / 名称、已选 N 项、公司（单数卡片标题）、本月、切换右侧栏、拖动调整顺序、月份组「未指定月份」、步骤按类摘要（已运行 N 个命令、已读取 N 个文件、已编辑 N 个文件、已搜索 N 次、已搜索网页 N 次、已打开 N 个网页、（N 个失败））。三种语言同批。
- **改已有文案（要用户先看）**：步骤组跑完的标题从「N 个步骤」改成按类摘要（例：「已运行 2 个命令（1 个失败）」）。现有的「失败」词条首次显示在失败步骤行尾，文字不变。用户不同意就保留「N 个步骤」。
- 位置变化（文字不变）：月份 / 分店胶囊从顶栏、问候语移到输入框下巴；点公司徽标改为打开右侧栏公司卡；主档页不再是日常入口。

## 施工分段与文件归属

阶段 1（一个代理，打底）：设计变量与密度、共用样式类改用变量、聚焦与阴影、菜单翻面机制（`components/ui/menu.tsx`、`.cc-menu`）、抄在别处的底色（`pearnly_core/www/assistant.html`、`pearnly_core/assistant/api.py` 离线页、`scripts/build-ios-splash.py` 与启动图、`companion/.../gui_tomo.py` 的 `APP_BG`）、`docs/development/ccs.md`、`tests/test_cc_ccs.py`。

阶段 2（三个代理并行，各自工作树，基于阶段 1 提交，合回本分支）：

| 线 | 归属文件 | 不碰 |
|---|---|---|
| A 侧栏 | `thread-list.tsx`、`projects.tsx` 的组头（`ProjectHeader`）与 `MoveToMenu`、新 `sidebar-group.tsx`、新 `lib/sidebar-keys.ts`、`lib/managed-agents/{projects,session-list-adapter,list-sync,sidebar-groups}.ts`、`assistant.tsx` 的 `SidebarNav` / `CompanyNav` / `MobileDrawer` / `Workspace` 收起状态 | 标题栏、Layout 右侧、Thread |
| B 右侧栏 | 新 `session-panel.tsx`、新 `company-cards.tsx`、新 `lib/company-panel.ts`、`company-page.tsx`（改管理页）、`results-panel.tsx`、`assistant.tsx` 的 `Layout` 右侧停靠 / `MainTopBar` 右上开关 / 标题栏（`ConversationTitle`、`ConversationMenu`、`ConversationCompany`）、`projects.tsx` 的 `ConversationCompany` / `useOpenCompany` 调用处 | 侧栏、步骤、输入框 |
| C 对话区 | `tool-uis/steps.tsx`、新 `lib/step-summary.ts`、`thread.tsx`（正文、栏宽、`Chin` 插槽）、`markdown-text.tsx`、`period-chip.tsx`、`projects.tsx` 的 `ComposerCompany` → 新 `ComposerChin` 与 `ConversationFiling` / `MobileFilingItems`、`assistant.tsx` 的问候语与 `<Thread components=`、顶栏去掉 `ConversationFiling` 那一处 | 侧栏、右侧栏 |

共用文件（`globals.css`、`i18n.ts`、`tests/test_assistant_ui.py` 等）各线只在自己的段落里加改，合并时由开发窗口解冲突。

阶段 3：合并、全闸门、浅 / 深 × 电脑 / 手机自查、测试机 lab 点验。

阶段 4（第 1 期）：月结台账 + 统一异常清单落在右侧栏「本月」卡，后端设计见 scratchpad `map/monthend.md`。

## 验证计划

- 每段：受影响的定向测试；合并后 `~/pearnly-devtools/gate.sh`（照 development.yml 全跑）、分支 CI。
- 改了 `pearnly_core`（`www/assistant.html`、`assistant/api.py`）→ 合入前在测试机跑 `cand-run.sh`。
- 测试机 lab：`lab-start cowork`（先通知测试窗口；10-16 15:30 前 `lab-stop`，测试窗口 V9 完成前不再起）、`PEARNLY_TESTENV_LOCAL_SPA=1 … live cowork`；交测试窗口按约定格式点验。

## 进度

- 10-15 夜：阶段 1 刚派出就按用户要求停下（「我希望你亲自摸下和策划下所有设计」），工作树未改动。开发窗口亲自在内置浏览器实测 Claude Code，写成 [CC 外壳设计](2026-10-16-cc-shell-design.md)，**取代本文上半部分与研究文档里的数值**（行高、悬停 / 选中色、右侧栏宽、组标题行为、多选方式等以设计文档为准）。用户回「可以」：设计与三处改动（步骤摘要文案、去掉公司组「定时任务 N」行、公司页改「概览 + 三个标签」）都已确认，按设计文档施工。
- 10-15 夜 阶段 1（打底）本地完成，未推送：
  - `globals.css` 新增 `--code-*` 一层（设计文档 2.1–2.4 浅 / 深全部取值），`--cc-*` 结构色、尺寸、阴影、动效改指它；`--ccs-*` 官方表未动。页面底所有变体（含手机）= 表面 1（#fcfcfb / #151515），删掉手机 #f9f9f7 覆盖与深色块里的旧底色。焦点统一 `--cc-shadow-focus`（内描 1px 页面色 + 外 1px #2a78d6 + 6px 光晕）。
  - 三个变体：`lib/variant.ts` 在 `main.tsx` 首帧前写 `<html data-cc-variant>`（窄屏优先：按 `hooks/use-mobile.ts` 同一条 768 分界判手机，CC 桌面版窗口拉到分界以下也换手机外壳与手机密度；其余在 CC 桌面版窗口里按 `isDesktopApp()` 判桌面窗口；跨分界时跟手机外壳一起换）；桌面窗口侧栏浮成卡片（离边 8、卡片阴影）、行 30、组上间距 14；手机行与组标题带 44、组箭头与组操作常显、正文 16/24。
  - 共用样式：导航行（行高按变体、14/20 次要色、20 图标 / 28 槽、悬停不改字色）、组标题 13/16（鼠标在组标题上变次要色，120ms；鼠标进侧栏只淡入箭头）、搜索框 32（白 50% / 白 5%、0.5px 内环 + 轻投影、快捷键提示槽 `.cc-search-hint`）、行内按钮 `.cc-row-btn` 24 / 圆角 6、菜单按 3.6（含 `.cc-menu-value`、不可用 40%）、按钮 / 输入框焦点、弹窗 pop 阴影、提示框色、回复正文 14/20（手机 16/24，字体仍按设置）、Markdown 段落与列表去掉 leading-relaxed（列表上外边距 1.5em≈21px、缩进 28 + 7）、行内代码 0.9em / 圆角 5 / 代码色、链接色、用户气泡叠色 1 / 圆角 10 / 8·12。会话列表行：悬停与选中同一底色、选中与键盘焦点靠主色字，行高按变体。
  - 菜单翻面（F11d）：`components/ui/menu.tsx` 的纯函数 `pickSide` + 包装 `Content` 在绘制前按 `scrollHeight`（`--cc-menu-max` 封顶）与触发按钮上下空间选方向，换子菜单页时重选；`.cc-menu` 限高改 `min(--cc-menu-max, 可用高度)`；三处 `max-h-80` 改 `[--cc-menu-max:20rem]`。
  - 底色同步：`www/assistant.html`（theme-color、启动底色）、`assistant/api.py` 离线页、`build-ios-splash.py` THEMES、`gui_tomo.py` APP_BG、`vite.config.ts` manifest → #fcfcfb / #151515。**iOS 启动图 PNG 未重新生成**（本机没有 Pillow），文件仍是旧底色，要在有 Pillow 的环境跑 `python3 scripts/build-ios-splash.py`。
  - 阶段 1 审查（10-15 夜）：无 blocker；3 项 should-fix 已改 —— ① 变体改窄屏优先（CC 桌面版窗口最窄 420，拉窄进抽屉时原来行高 30、正文 14/20），`useIsMobile` 也改用 `mql.matches`，与变体判得一样；② 组标题只在鼠标放在组标题上变次要色（去掉「鼠标进侧栏全部变色」）；③ 会话行键盘焦点也主色字（`has-focus-visible:text-ink`）。nit 已改：菜单打开期间内容变高（ResizeObserver + MutationObserver）、键盘弹出收起（visualViewport resize）时重选方向，离边距离按传进来的 `collisionPadding`，旁边展开的菜单不被旧的上 / 下盖掉；`[--cc-menu-max:20rem]` 改指 `--cc-menu-max-list`；`--cc-sidebar-w` 指 `--code-sidebar-w`；删掉没人用的 `--cc-list-selected`；`.cc-menu-item` / `.cc-menu-sep` 改走 `--cc-*`（加测试：只有变量定义能读 `--code-*`）；会话行「···」图标 16；CC 桌面版 `APP_INK` 同步 #0b0b0b / #f0efec。**留给编排窗口定**：回复段落仍 `my-3`（设计文档 3.10 写段落间无额外外边距，审查建议阶段 3 目测对照 Code 后再定）。**记到阶段 2**：输入框圆角 20→14 接 `--code-radius-input`、`.cc-project-name:hover` 改次要色、手机抽屉行字号（设计文档 3.16 写仍 14，现 17）、触屏平板 ≥768 组操作要悬停才出（原有行为）、`.cc-row-btn` / `.cc-menu-value` / `.cc-search-hint` / `--cc-row-gap` / `--cc-sidebar-head-h` / `-foot-h` 是阶段 2 预留。
  - 证据（本工作树）：`npm --prefix frontend/assistant run build` 通过；`tests/test_cc_ccs.py`（16，含 `--code-*` 浅深逐项、变体、底色副本）、`test_assistant_ui.py`（90）、`test_cc_mobile_overlays.py`（25，含 `pickSide` 纯函数）、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py` 全过；`check-frontend-tokens.py`、`check-frontend-viewport-units.py`、`check-architecture.py` 通过。CC 桌面版 pytest 本机缺 pytest / PySide6 没跑（`gui_tomo.py` 只改一个常量，已 py_compile）。浅 / 深 × 网页 / 桌面窗口 / 手机的目测、测试机点验、`cand-run.sh`（动了 pearnly_core）都还没做。
- 10-15 夜 阶段 2 · A 线（左侧栏，工作树 `.claude/worktrees/cc-shell-sidebar`，分支 `pearnly/cc-shell-sidebar`，基线 3042c929）本地完成，未推送：
  - 导航只选当前页：只看某家的定时任务 / 待处理、主档页都不再把顶部「定时任务」「待处理」「公司」选中；顺序按设计文档 4.2 改为「新建、待处理 N、定时任务、公司」（手机抽屉同序；补充乙原来在定时任务下面，见下方复审）。
  - 组标题统一新组件 `sidebar-group.tsx` 的 `GroupHeader`（设计文档 3.4）：整行开关（aria-expanded）、名字 13/16 淡色悬停变次要色、名字后 12px 箭头（鼠标进侧栏淡入）、右侧「分组和排序」常显、「···」「新建」悬停才出（新建在最右）；公司组另有淡色待处理数（点了只看这家）、「···」= 打开公司 / 在这家新建对话；名字不再打开主档页。手机抽屉与触屏平板（`@media (hover: none)`）箭头与操作常显。**公司组里「⏱ 定时任务 N」一行已去掉**（用户同意）。
  - 「分组和排序」（3.6）：分组依据 公司 / 月份 / 无，排序 上次更新 / 创建日期 / 名称 + 降序 / 升序，显示 元数据 / 空分组，恢复默认设置（改过才可点），父项右侧写当前值；本机按用户记（`lib/managed-agents/sidebar-view.ts`，键 `cc.sidebar.view:<用户>`）。按公司用分组接口，按月份 / 不分组用普通列表，两类之间换整表重拉（`list-sync.ts`）。**创建日期只排已加载的行**（服务端分页仍按上次更新，没动 `pearnly_core`）。
  - 会话行（3.5）：行高按变体、标题末尾渐隐、行尾 24 槽（在跑 6px 脉冲点 1.2s / 未读蓝点，悬停与键盘焦点换「···」），「元数据」开时第二行（按公司「9月 · 分店 00012」、按月份「公司 · 分店」、不分组「公司 · 月份 · 分店」），默认关（行高同 Code 的 32；见下方复审）。行菜单向下开、右对齐，单字母 P / R / D。
  - 多选（3.7）与键盘（3.8，`lib/sidebar-keys.ts`）：Shift 点一段（跨组）、⌘ / Ctrl 点单条、Shift + ↑↓、Esc 清空；没有批量栏，选两条以上时行「···」变「置顶 N / 移到… (N) / 删除 N」，逐条顺序执行；roving 焦点（整组一个 tabIndex=0）、↑↓ Home End、组标题 ← → 收起展开；⌘ / Ctrl + 1–9 只在桌面窗口，按住约 300ms 行尾换数字。
  - 侧栏收起记本机（`cc.sidebar.open`），收起后左上角展开钮为圆钮；标题区 44、收起钮 24；底部托盘 48（头像 + 名字 · 产品，右侧「设置」32）。抽屉行字 14（原 17）、行与组标题带 44。
  - 证据：`npm --prefix frontend/assistant run build` 通过；`test_assistant_ui.py`（新增侧栏外壳 8 条 + 按月份 / 不分组取列表 1 条）、`test_cc_ccs.py`、`test_cc_mobile_overlays.py`、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py` 全过；三个前端 / 架构闸门通过。本机用假接口起页面点过：网页宽屏浅 / 深色、手机抽屉深色、分组切换、Shift / ⌘ 多选与批量置顶、方向键与 ← 收起。桌面窗口变体（⌘1–9 数字提示）没在真窗口里看、测试机未点验。
- 10-15 夜 A 线复审修复（同一工作树）：
  - B1（阻塞）行菜单单字母 P / R / D 挪到 `lib/sidebar-keys.ts` 的 `menuLetterKeys`，只认按在这一层菜单本身的键（`ownMenuKey`：最近的 `role="menu"` 就是这一层、且不在窄屏换上的子菜单页 `cc-menu-subpage` 里）；「移到 ▸」「移到… (N)」里按 D / P 不再删除 / 置顶。行为测试 `test_menu_letters_only_fire_on_the_menu_itself`。
  - S1 组标题右侧操作（待处理数、···、分组和排序、新建，仍 tabIndex -1）键盘可达：组开关已展开时 → 进操作区，操作之间 ← →，最左再 ← 回组开关，↑ ↓ 回 roving 列表；已置顶 / 最近 / 月份组同样可到「分组和排序」「新建」。
  - S2 顶部导航按设计文档 4.2：新建、待处理 N、定时任务、公司（`PendingNav` / `CompanyNav` 拆开，电脑与手机抽屉同序）。2026-10-15 主档验收记录写的「定时任务下面」以本次用户批准的设计为准。
  - S3「元数据」默认关（`DEFAULT_VIEW.meta = false`）：行高同 Code 的 32；需要月份 / 分店的在「分组和排序 → 显示 → 元数据」打开，本机按用户记。
  - S4 多选、批量菜单、Shift + ↑↓、⌘ / Ctrl + 1–9 只在有公司分组的产品（`useSidebarKeys(companies)`、行上 `companies` 判断），ERP / DMS 不变；ERP / DMS 的日期组开关加入 roving（↑↓ 不跳过、Tab 不单独停）。roving 焦点本身三个产品共用（外壳统一，有意带上）。
  - 小项：第一次 Shift 点从正开着的那一行选起（`selectRange(…, currentRow(…))`）；抽屉导航图标 20（`--cc-icon`）；ERP / DMS 手机抽屉日期组标题带 44（`--cc-group-h`），字号按组标题 13；「分组依据」的月份项用新词条「月份」（`sb.month`，三语）；悬停时行尾装饰 120ms 淡出（`--cc-dur-fast`）；资料到之前改的「分组和排序」不再被读回的覆盖，知道是谁时存给这个人。
  - 未改（原因）：收起后圆形展开钮只覆盖网页版顶栏——CC 桌面版窗口收起后标题栏照 Code 保留菜单 / 侧栏 / 前进后退一排，不是圆钮；它依赖 `MainTopBar` 结构，合并时与 B 线核对。触屏平板行「···」只在悬停时显示是原有问题，不在本线。「创建日期」只排已加载的行，已知。
- 10-16 阶段 2 B 线（标题栏、右侧会话栏、公司页、列表页），分支 `pearnly/cc-shell-panel`（基于 3042c929），本地提交、未推送：
  - 标题栏（3.9）：高 32；「公司名 ›」点了打开右侧会话栏并展开公司卡（预览开着时换回会话栏）；对话名按钮点了就地改名；「···」单独是会话菜单（置顶 / 改名 / 移到 / 删除）；月份分店胶囊从标题栏拿掉（C 线放进下巴）。右上 26 的「文件 N」（有文件才有）与会话栏开关（`aria-pressed`）。手机在胶囊菜单最上面加公司名，点了全屏打开会话栏。
  - 右侧会话栏（3.13 / 4.4，新 `session-panel.tsx`、`company-cards.tsx`、`lib/company-panel.ts`）：宽 280、卡片间距 10、透明底 + 1px 内环、圆角 10；卡头 26 整行折叠、右侧淡色摘要、悬停出 6 点把手（拖动、Alt+↑/↓ 只在看得见的卡片间挪）；卡内行 26、13/19；空卡不显示，「本月」卡留位不显示。顺序 / 折叠 / 开关记本机（键 `cc.panel.<用户>`）。只在 Cowork 挂了公司的对话里；与文件预览共用一个停靠位；对话区放不下「对话最窄 + 280」时浮在对话上（Esc、×、返回键），手机全屏。公司详情按「公司|月份」缓存，只在会话栏挂着时跟站点事件重取。
  - 公司页（3.15）：顶栏名称点了改名，「···」= 改名 / 刷新账套 / 删除公司；左边「在这家新建对话」输入框、概览（法定名、税号 · 分店、合并申报）、账套、待处理；右边大面板标签「对话 / 资料 / 定时任务」。对话分组只用现有信号：服务端读回的运行状态里有等你点「允许」的、或有没看的 CC 提醒 → 等你处理；实时通道或读回说在跑 → 进行中（读回已结束的不算）；其余已完成。读回状态用同侧栏的 `check`（只核对侧栏以为在跑的，最多 10 个）。待处理单据按公司记、不挂在对话上，不参与分组。
  - 列表页（3.14）：公司列表改卡片网格（名称、税号 · 分店 · 年度、待处理数、我的最近活动），标题 24/500、「排序方式」（最近活动 = 服务端顺序 / 名称 = 已加载的按名称排）、小主按钮「添加公司」、搜索 36；待处理页、定时任务页同样的标题与搜索（前端就地筛选），行改成同侧栏行的样式，F18 当场选规则与「新建任务」两处按钮不变。
  - 新词条（三语同批）：`sessionPanel`、`reorder`、`co.company / thisMonth / library / sortBy / sortRecent / sortName / chatRunning / chatWaiting / chatDone`。已有文案未改。
  - 证据（本工作树）：`npm --prefix frontend/assistant run build` 通过；`test_assistant_ui.py`（90）、`test_cc_ccs.py`、`test_cc_mobile_overlays.py`、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py`、新 `test_cc_session_panel.py`（11）全过；三个检查脚本通过。卡片外框改成整行按钮后，文件夹卡的按钮断言按新结构逐条改写（第一个按钮是卡头开关），没有放宽。静态渲染卡片与列表页在浅 / 深色看过一遍；真实页面（网页 / 桌面窗口 / 手机）目测与测试机点验没做。
  - B 线审查（10-16）：无阻塞；应修与小问题已改 —— ① 公司页「对话」分组只看实时状态：取回的运行状态先交给 `noteRun` 校正（同侧栏 `absorb`），「等你处理」只认还在跑、且读回里有等你点「允许」的；页面开着时才开始跑的对话隔 1 秒补读一次运行状态（只读状态、不换列表），不在跑了丢掉读回的；② 公司页卡头的「＋」一直显示，会话栏里才悬停出现（手机变体、不能悬停的触屏常显）；④ 会话栏挂不挂改看对话有没有服务端编号（不看消息条数），换对话时不再卸下再挂上；⑤ 开关文案改「显示 / 隐藏右侧栏」（`showPanel` / `hidePanel`，按开着没有选），把手改「调整顺序」，三语同批。小问题：「名称」排序时把剩下的页一次取完；窄屏首帧前就量停靠位（`useLayoutEffect`）；网页标题栏去掉上下外边距，实高 32；公司列表卡片网格样式移进「公司主档（ADR-020」区块；标题栏改名时 Esc 标记已处理，不再顺带关掉浮着的会话栏；「对话」标签加载中转圈、没有对话时显示「还没有对话。」（新词条 `co.noChats`）；登录资料读到前不读写偏好（不写进 `cc.panel.-`）。
  - **合并顺序（审查 ③）**：标题栏已拿掉月份分店胶囊，C 线的下巴胶囊落地前网页与桌面窗口没有地方改对话的月份与分店（手机仍有 `MobileFilingItems`）。**B 线不能先于 C 线单独合并或部署**，两线一起合；C 线胶囊点击调 `openCompanyCard()`。
  - **待用户确认**：会话栏并排 / 浮层分界（对话最窄 24rem + 280，实现者自定，非实测）；公司页左边保留「待处理」（F18 当场选不受影响）；CC 桌面版自画标题栏仍 40（`--cc-titlebar-h`，放窗口按钮，B 线未动），设计文档 3.9 的 32 现只落在网页标题栏。
- 10-15 夜 阶段 2 · C 线（对话区、步骤、输入框与下巴；分支 `pearnly/cc-shell-thread`，基线 3042c929，提交 0f3fa2be，未推送）：
  - 正文按设计文档 3.10：块之间只用上外边距（段落 / 列表 / 表格 / 代码 / 引用 / 分隔线 `--cc-md-block` 0.75em，标题前 `--cc-md-heading` 1.25em，第一块 0，取代阶段 1 的 `my-3` 与 `--cc-md-list-top`）；标题按 em 缩小；表格外圈 1px 环 + 圆角 10、里面细线、表头不再灰底；回复里步骤、正文、文件卡之间同样只用上外边距；回复下的操作行（复制）悬停淡入，手机与无悬停设备常显（原来电脑上最后一条常显，按设计文档改）；栏宽内容区补回 2rem，正文与输入框正好 768。
  - 步骤按 3.11：新 `lib/step-summary.ts`（纯函数：分类、失败判断、合并计数、拼文案）；跑完的组标题从「N 个步骤」改成按类摘要（用户已批，例「已运行 2 个命令（1 个失败）」；本站工具用服务端「做完」说法，多次「× n」；zh 用「，」连接）；进行中仍是「正在运行」，工作状态行未动。展开是细框卡片（圆角 10、1px 环、行 6/8、行间 1px 线），每步一句说明、不再有图标，失败行尾红字「失败」（沿用已有词条），再点开看命令与输出（等宽，`Clipped` 不变）。
  - 输入框按 3.12（电脑）：一行「＋ / 输入 / 发送」，单行高 44、内边距 8、按钮 28、圆角 14（`--cc-radius-composer` 改指 `--code-radius-input`，主档页输入框同步变 14）；手机外壳保留原两行。`thread.tsx` 加通用 `Chin` 插槽（不认识公司）；`ComposerChin`（projects.tsx，取代 `ComposerCompany`，问候语里的公司条去掉）：新对话「公司 ▾ / 月份 / 分店」（草稿），已有对话「公司 / 月份 / 分店」（改了就 move），菜单向上开（阶段 1 翻面机制）；ERP / DMS 不出现。已有对话点公司名派发 `cc:open-company-card`（`OPEN_COMPANY_CARD`，detail.company，cancelable），**B 线右侧栏监听并 preventDefault**；没人接时打开主档页。手机胶囊菜单去掉「处理月份」，只留「移到…」。`ConversationFiling` 仍导出（顶栏那处由 B 线删）。
  - 证据：`npm --prefix frontend/assistant run build` 通过；`test_assistant_ui.py`（90）、`test_cc_ccs.py`（19，含按类摘要三语 node 用例）、`test_cc_mobile_overlays.py`（25）、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py` 全过；`check-frontend-tokens.py`、`check-frontend-viewport-units.py`、`check-architecture.py` 通过。浅 / 深色用构建出的样式表做了静态页截图自查（步骤卡、表格、输入框一行、下巴）；真页面、手机、测试机点验未做。
  - C 线审查（10-15 夜）：无 blocker。已改 —— ① 摘要的分类表与「做完」说法只认对象自己的键（`Object.hasOwn`），工具名叫 `constructor` / `toString` 不再拿到原型函数（加 node 用例）；② 下巴里新对话选了公司后公司名用正文色（`.cc-chin-btn[data-chosen]`），和「公司」占位分开；③ 电脑输入框内边距扣掉 `.cc-composer` 的细线边框，border-box 下单行正好 44（原 45）；④ 等批准、不进组的那一步也放进带环的 `.cc-steps-card`，不再单独浮在正文里。核实不改 —— iPad / 横屏大手机点输入框放大：`globals.css` 已有 `@media (hover: none) and (pointer: coarse)` 把所有 input / textarea 抬到 ≥16px（!important、不在 layer 里，压过 14px），触屏平板不会放大；正文 `cc-reply px-2` 比输入框两侧各缩 8：原有、有测试锁着，设计文档 3.10 没写正文与输入框边缘对齐，阶段 3 目测对照 Code 再定；手机下巴按钮 24 高没加 `cc-tap`：加了下巴会从 48 撑到 60，旧胶囊也只有 28，阶段 3 目测再定。记一笔 —— 服务端说法异步取回前，本站工具的摘要先显示「已使用 N 个工具」再变成本站说法；用户中途停止（`incomplete`）的那一步算「失败」（旧版同样标红，区分「已取消」要新文案，本批不做）。前面「静态页截图自查」不是真组件渲染的，不能当界面证据。
  - **合并清单（B、C 一起合入再验收）**：① 标题栏的 `<ConversationFiling />`（`assistant.tsx`）由 B 线删；C 线单独合入或先合入时，已有对话的标题栏和下巴会各显示一份月份 / 分店。② 点公司名派发的 `cc:open-company-card` 要由 B 线右侧栏监听、`preventDefault()`，且右侧栏没打开时先打开再显示公司卡；没人接时退回打开主档页（与原标题栏行为一致）。
  - **要用户定**：电脑上最后一条回复的操作行也悬停才出现可否；中文摘要连接符用「，」还是照 Code 的「, 」；已有对话没挂公司时下巴要不要给「选公司」。
- 10-16 阶段 2 三线合入 `pearnly/cc-shell-rebuild`（本地，未推送）：依次 `--no-ff` 合 A（侧栏）→ B（标题栏、右侧会话栏、公司页）→ C（对话区、步骤、输入框下巴）。
  - 冲突处理：`globals.css` 公司主档区块保留 A 的说明（组头样式走 `.cc-group-*`、删掉 `.cc-project-row` / `.cc-project-toggle`）并收进 B 的卡片网格；`--code-*` 同时保留 B 的标题栏 / 会话栏 / 列表页与 C 的对话区取值；`test_cc_ccs.py` 取值表合并 A 的 `pulse` 与 C 的对话区各项；`assistant.tsx` 去掉两条都没人用的图标导入（A 删了托盘的 `ChevronsUpDownIcon`，B 删了标题栏的 `ChevronDownIcon`）；`projects.tsx` 文件头按三线现状合写（`BulkMoveMenu`、`ComposerChin`、`ConversationCompany` 打开公司卡）；任务记录三段都保留。
  - 合并清单落实：① 标题栏已无 `ConversationFiling`（B 删），月份、分店只在下巴（C 的 `FilingChips`），没人用的 `ConversationFiling` 导出一并删掉；② 下巴公司名不再派发 `cc:open-company-card` 事件（没有监听，且与 B 的导入同名编译不过），改为直接调 B 的 `openCompanyCard()`（`lib/company-panel.ts` 的 `showCard("company")`：打开会话栏、展开并滚到公司卡、预览开着时换回会话栏），与标题栏「公司名 ›」、手机菜单同一入口；会话栏只在已建好（有服务端编号）的挂了公司的对话里挂，下巴已有对话的公司名同条件出现。③ 公司组里「⏱ 定时任务 N」一行已去掉（A），`CompanyTasks` 在源码里不再出现（测试守住）。
  - 证据：`npm --prefix frontend/assistant run build`（含 tsc）通过；`test_assistant_ui.py`（100）、`test_cc_ccs.py`（19）、`test_cc_mobile_overlays.py`（25）、`test_cc_run_indicator.py`（11）、`test_cc_mobile_content.py`（7）、`test_assistant.py`（123）、`test_cc_desktop_name.py`（12）、`test_cc_session_panel.py`（12）全过；三个检查脚本通过；`gate.sh` 结果见下一条提交说明。三线合在一起后的真实页面（网页 / 桌面窗口 / 手机，浅 / 深）还没在浏览器里点过，测试机未点验（阶段 3）。
- 10-16 阶段 2 合并后两份审查（设计还原、回归）的修复（同一分支，本地提交、未推送）：
  - 改了 —— ① 手机抽屉组标题的开关占满 44 的带（原来能点的只有 24），待处理数也放到 32；② 收起的公司组名字后的点按实际活动显示（有在跑的是脉冲点，否则未读蓝点；`useGroupActivity`）；③ 标题栏「公司名 ›」高 26（同旁边的标题按钮），对话还没存到服务端时不显示，下巴里的公司名同期不可点；④ 菜单危险项悬停不再有红底，只红字；⑤ 批量移动文案改「将 N 项移到…」（英、泰同批）；⑥ 用户气泡最宽 580（新变量 `--cc-bubble-max` → `--code-bubble-max`）；⑦ 停靠位改用回调 ref 量宽度，窄屏拉回宽屏后会话栏能重新并排（原来盯着已卸下的旧节点，一直浮着）；⑧ 公司页换公司时整页按公司重新挂上（`key`），会话栏各卡按公司重新挂上，输入框的分店、所在标签、知识库新增框不再串到另一家；⑨ 多选只留眼前还排着的行（收起分组、搜索、换分组、别处删掉后自动去掉），批量菜单只做列表里还在的；侧栏增删合到一帧处理一次；⑩ 输入法选字时按 Esc 不关会话栏、不清多选；弹窗或菜单开着时 Esc 不清多选、⌘ / Ctrl + 数字不切后面的对话；⑪ 公司页「对话」标签只在移进这家、或从这一页移走时重取，与侧栏条数变化合成一次；⑫ 点公司名在浮层 / 手机里只开这一次，不改宽屏记下的开关；⑬ ERP / DMS、手机上带修饰键点对话照常切回对话页（多选只有电脑上的 Cowork）；⑭ 定时任务总览改由定时任务页自己监听（公司组「定时任务 N」一行已删，侧栏不再常驻读取），三处旧注释改掉；⑮ 弹层选择器 `OPEN_LAYERS` 挪到不引任何模块的 `lib/layers.ts`（返回键栈照旧导出，侧栏键盘也用）。
  - 核实不改 —— ERP / DMS 侧栏也有 roving 焦点：A 线复审 S4 定的外壳统一，有意带上；只把 `sidebar-keys.ts` 的说明改准确。
  - 有意不做（登记）—— 搜索框右侧快捷键提示：设计 4.2 不做 ⌘K，没有可提示的键，`.cc-search-hint` 留空不渲染；导航行行尾悬停操作钮（如「公司」行尾「添加公司」）：4.2 没要求；回复操作行的相对时间：没做，要不要做等目测对照；CC 桌面版标题栏仍 40（放窗口按钮），3.9 的 32 只落在网页；手机外壳（抽屉 80% 宽、页面推开、无左上「×」、顶栏 44 圆钮）是已验收的手机外壳，3.16 的测量值不照改；公司页左边输入框仍是旧的两行样式。
  - 待定（要在浏览器里对照 Code 再定）—— 组标题「展开状态开关字为主色口径」（文档同节又写平时淡色、悬停变次要色，两句冲突，暂按淡色）；输入框下巴要不要自己的底色与圆角（3.12「左右各宽 8」现在只体现在按钮位置）；以及前面各线留的：会话栏并排 / 浮层分界、电脑上最后一条回复的操作行悬停才出、中文摘要连接符、已有对话没挂公司时下巴要不要「选公司」。
  - 阶段 2 小结：A 侧栏（组标题、分组和排序、会话行、多选、键盘）、B 标题栏 / 右侧会话栏 / 公司页 / 列表页、C 对话区 / 步骤摘要 / 输入框与下巴已合在本分支并按两份审查修完；用户同意的三处改动（步骤摘要按类、去掉公司组「定时任务 N」、公司页概览 + 三标签且对话里点公司名开右侧栏）都已落地。没做：浅 / 深 × 网页 / 桌面窗口 / 手机的真实页面目测、测试机 lab 点验、`cand-run.sh`（阶段 1 动了 `pearnly_core`）、iOS 启动图重生成、推送与合入 main。
  - 证据（提交 659b2b32）：`npm --prefix frontend/assistant run build`（含 tsc）通过；`test_assistant_ui.py`（100）、`test_cc_ccs.py`、`test_cc_mobile_overlays.py`（25）、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py`、`test_cc_session_panel.py`（12）全过，新增用例：选中只留眼前的行（`keepRendered`）、浮层里点公司名不改记下的开关、停靠位跟新节点重量、各卡 / 公司页按公司重新挂上、输入法 Esc、「对话」标签只认这家的移动；三个检查脚本通过；`SKIP_NPM_CI=1 gate.sh` 84 项全部 ok。修复都是读代码改的，复现步骤与效果没在浏览器里跑过。
- 10-16 待定项由开发窗口按 Claude Code 实测定下（提交 9663f2de）：组标题展开后仍淡色（Code 的组开关展开时实测仍是淡色，主色只用于展开中的操作按钮）；下巴不另加底色与圆角（Code 下巴实测透明底，只有按钮）；回复操作行电脑上一律悬停才出、包括最后一条（Code 实测最后一条也是 opacity 0）；中文摘要连接符照 Code 用「, 」；已有对话没挂公司时下巴给「公司 ▾」，选了就归到那家（月份取上个月、分店空）。会话栏并排 / 浮层分界保持「对话最窄 24rem + 280」；公司页左边保留「待处理」；CC 桌面版标题栏保持 40（窗口按钮）。回复操作行的相对时间不做。
- 10-16 展开后的步骤行照 Claude Code 实测再改（测试窗口转来的截图对照）：行字改淡色（`--cc-ink-3`）、一行省略，能展开的行尾 12px 箭头（展开转 90°，失败的红字「失败」在箭头前，没详情的行没有箭头）；本站工具的行后面带「 · 对象」，由新纯函数 `lib/step-subject.ts` 从这一步的参数 / 结果（加同一条回复里别的结果认出的记录名字）取：点名的单据 / 任务 / 盘点等用单号 > 标题 > 卖方 > 文件名，一串时「第一张 +其余张数」，其次标题 / 搜索词 / 月份，再次账套文件夹名（如 T9NIEXCP），最后结果里的公司范围；内部记录名不显示，最多 40 字，认不出就只写原说法；摘要行与工作状态行不变。没新增文案，没在浏览器里目测。
- 10-15 夜 测试窗口第 2 轮（21:00–21:18）五条，本地提交、未推送：
  - ① 推送那一行写单号：记录名字改从整段对话的结果里认（推送结果里没有单号，单号在前几条回复列过 / 识别过的结果里），不再落到账套键；只能写账套时照原写法（结果里见过的原写法，没见过的全小写名按 Express 大写，如 T9NIEXCP），不显示归一过的小写键（`lib/step-subject.ts` 的 `folderSpellings` / `knownEntries`）。
  - ② 点开的本站工具步骤只显示给人看的：新纯函数 `lib/step-detail.ts` 拿掉连接、账套键 / 路径、会话、推送记录等内部键，以及像内部名字的值（CWEC-…、CWH-…、随机名、file_…、sesn_…、本地路径），留单号、名称、金额、日期、状态、说明和搜索词 / 月份；内置工具（命令、文件）照原样，不是 JSON 的原样。
  - ③ F19 跟进：推送 / 查结果 / 单据详情的 `written` 加科目来源 `account_from`（chosen / remembered / task_default / express_history / matched / default，旧记录不给）；模型说明要求入账必报 ERP 单号、现赊与科目都说来源、不叫用户去 Express 核对（详见验收记录 F19）。
  - ④ 手机深色：启动页与离线页加 `<meta name="color-scheme" content="light dark">`（theme-color 浅 / 深各一个已有，离线页补上）；设置里固定浅 / 深色时运行时把 color-scheme 改成那一种，theme-color 照旧跟当前底色；manifest 加 `user_preferences.color_scheme_dark`（#151515，认它的 Chromium 才用）。安卓真机导航条变没变要测试窗口在手机上看。
  - ⑤ 右侧栏公司卡：对话没指定分店时各分店覆盖这个月的账套都列、一份一行、总店在前（分店没有覆盖的取它最新可写的一份）；指定了分店只列那个分店的（`lib/companies.ts` 的 `setsForScope`）。没新增文案。
  - 审查后修正（本地提交、未推送）：① 详情里 UUID（盘点任务名）与 33–64 位十六进制名（sha256）也认作内部名字拿掉；② `file_` / `sesn_` 这类编号要带数字才拿掉，`agent_failed`、`file_unreadable`、`user_permissions` 这类原因码照留；③ 查「这张票后有人选的」用与记选择同一个税号键，说明里 chosen 补上公司页改过的；⑤ 有任一账套覆盖这个月就只列覆盖的，都不覆盖才每个分店各取最新一份，对话分店不在名单里时照旧只显示一份；步骤行的记录名字表各步共用一份、流式只来文字时不重算不重画，展开的结果按文字缓存。都有用例，没在浏览器里目测。
- 10-16 合入数字会计员第 1 期（`pearnly/month-ledger`，合并提交 b05d12b9，本地、未推送）：月结台账后端（`companies/ledger.py`、`exceptions.py`、`api.month`、回读按连接共看）与右侧栏「本月」卡、公司页概览第一节随外壳一起上。合并无文本冲突；两边同改的 6 个文件（`assistant/tools.py` 回读等待 / F19 `WRITTEN_NOTE`、`push/application/service.py` 冲突文案挪到 `account_match` / F19 `account_from`、`session-panel.tsx` 本月卡 / 第 2 轮 `setsForScope`、`globals.css`、`lib/companies.ts`、`test_cowork_assistant_tools.py`）逐段核对，两边改动都在、互不覆盖。按开发窗口 10-16 定的 ②：会话栏「待处理」卡只列对话月份以外的单，对话那个月的只在「本月」卡的异常清单里，一张不剩时整卡不显示（同别的空卡）；对话没有月份时全列；公司页与待处理页不变。单据记在哪个月与台账同一口径：服务端待处理项加 `month`（单据日期的月份，没有日期的按上传月份，`pending.month_of`），页面 `otherMonths` / `pendingMonth`（旧服务端没给 `month` 时取单据日期）。用例：`test_cowork_companies.py` 的 `test_month_follows_the_ledger`、契约 `cc-pending-contract.json` 加 `month`、`test_cc_session_panel.py` 两条。没在浏览器里目测，测试机未点验。
- 10-15 夜 测试窗口第 3 轮（21:50–22:06）六条，本地提交、未推送（开发窗口定的修法）：
  - ① F19：`push/application/service.py: written` 一律带推送记录的 ERP 单号 `docnum`（对话推、待处理页当场选重推、定时任务推都一样；MR.ERP 只给单号），单据详情 / 推送结果 / 已入账重推都有；`WRITTEN_NOTE` 加「来源用用户的话说、不念码、说完不叫人去 Express 再看」，`GUIDANCE` 加「金额是泰铢（บาท / 泰铢 / THB），不说元」和「按单号找交来的单」；`cowork_company_profile` 的时刻（记住选择的 set_at、知识库 / 草稿 updated_at、建档时间）换成站点时区带偏移（`2026-10-15T21:08:00+07:00`），说明里写明。
  - ② 什么都没有的月份（没收到单、没推过、没有申报草稿）不再给 `readback_needed`，核对一段按推过的单算、没推过时是 empty，四段都空时「本月」卡隐藏（`ledger.month`、`exceptions.readback_items(needed)`）。
  - ③「交给 CC」预填不写内部记录名：每张写「单号 · 对方 · 金额 · 日期」（`lib/companies.ts: docLine`），一张用新词条 `handToCCOne`（处理这张：…），几张每张一行；重复单号服务端合成一条（`count`、`documents` 每张明细，含不在本月的被匹配那张），行写「IV6909-0005 · 2 张」（新词条 `ledger.sameNumber`），核对预填逐张列；工具说明加按单号找。契约 `cc-month-contract.json` 随之改（两条同号合一，total 13 → 12）。
  - ④ 核对一段的回读时刻写到时分（`lib/month.ts: dayTime`，按设备时区，如「10月15日 21:08」）。
  - ⑤ 报税一段：这个月的截止日都过了就不写截止日（也不写逾期），否则仍写最早未到的那个（`taxDue`）。
  - ⑥ 手机重开后侧栏各组都收着：折叠改成按用户记在本机的一份全局 store（`lib/managed-agents/sidebar-fold.ts`，键 `cc.sidebar.collapsed:<用户>`，首屏按这台设备上次登录的人读，没记过沿用设备上原来那份），电脑 / 手机同一套；默认展开的「前 5 家」只数有对话的公司，空公司组不再占位（之前空公司活动更近时把有对话的公司挤到第 6 家以后，一重开就全收着）。没在真机上复现，按代码路径修；要测试窗口在手机上重开 App 复验。
  - 测试：`test_cowork_assistant_tools.py`、`test_cowork_companies.py`（档案时刻、空月、没推过、同号合组）、`test_cc_month_ledger.py`（预填、同号一行、时分、截止日）、`test_assistant_ui.py`（handText、折叠按用户、空公司不占位）。没在浏览器里目测，测试机未点验。
