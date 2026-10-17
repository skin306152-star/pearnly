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
- 卡片顺序、折叠、开关记在本机（键带用户），不做跨设备同步。（第 7 轮改：卡片顺序与开合、侧栏手排顺序、分组和排序按人存到服务端跨设备同步，见进度第 7 轮；停靠时开着没有、侧栏组折叠仍按设备。）
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
  - 第 3 轮复审跟进（本地提交、未推送）：⑥ 公司先到、对话还没到（一条对话都还没归到公司、服务端也没说哪家还有）时照公司顺序展开前 5 家，不再全收着（`sidebar-groups.ts: companySections`）；① 对话里推送后等结果那条路（`outcomes`）的 `written` 也带单号（改用 `_entered`），与 `WRITTEN_NOTE` 一致；③ 单号、对方、金额、日期都没有的单，预填写文件名、不再静默丢掉（待处理条目与同号组每张带 `filename`，`cowork_list_documents` 的 search 也按文件名找）；② 按派单口径收紧：收到了单但一张没推的月份也不催回读、核对一段为空（有申报草稿时照旧催，已有回读发现照常报，收单 / 录账两段照常列这些单）。测试：`test_assistant_ui.py`、`test_cowork_assistant_tools.py`、`test_cc_month_ledger.py`、`test_cowork_companies.py`，两份契约 fixture 补 `filename`。⑥ 根因仍要真机重开 App 复验。
- 10-16 验收反馈 P / Q 线与第 2 期合入（本地提交、未推送）：`5c2cf06e` 合入 `pearnly/customer-rules`（数字会计员第 2 期：公司规则）；`340eecb8` Q 线（消息里的附件卡片、点开在右侧面板预览含 PDF、就地改名同 Claude Code）；`98a47aad` P 线（公司页改成对话骨架、会话栏卡头摘要、dnd-kit 排序）。设计见设计文档 3.9、3.13、4.4、4.5。
- 10-16 第 4 轮审查（只读审查，HEAD `98a47aad`，85 步闸门全过）跟进，本地提交、未推送：
  - ① 拿起的卡不再按会话栏看得见的框限制（会话栏本身滚动，长卡一拿起被拽上去、拖不下来）：去掉 `restrictToParentElement`，改成按整列卡片的版面位置（offsetTop，不含变换、不随滚动）限制在第一张顶边与最后一张底边之间（`session-panel.tsx: clampToList / withinList`），自动滚动照常。
  - ② 让位过渡只在 dnd-kit 给的过渡不是 0ms 时才加（`company-cards.tsx: moving`）：放下那一帧的 `transform 0ms linear` 不再套动效变量，让过位的卡不会先跳开一格再滑回。原来写的「放下有过渡」实际没有：补上放手处 → 新位置的过渡（记下放手时卡的中线，换位那次提交里用 `translate` 摆回放手处、去掉 `data-landing` 后走动效变量回到 0；`transform` 仍归 dnd-kit）。取消拖动同样有过渡。
  - ③ 附件的下载 / 预览先取本站留存的那份（只认这个对话、只读本地），没有再去引擎找可下载的结果（`files.py: _stored`），缩略图、PDF 预览不再每张先付每段两次引擎接口调用；本站没留存的附件在列表补大小时记一次「没有」（1 小时），不每次重扫目录。缓存头没加（Frappe 的二进制回应不好单独设，另议）。
  - ④ 公司页与它的会话栏共用一份公司详情（`company-panel.ts: usePanelDetail` 改成按「公司|月份」共享的一份）：先挂上的那一处跟着站点事件 / 主档推送重取，其余只读；刚挂上时已有同一份在取就不再取；用户操作后的 reload 照常单独取（不拼到操作前那次上）。公司页只留「删了回公司列表」的监听。
  - ⑤ 公司页输入框：回车改用 `isImeEnter`（Safari 选字的 keyCode 229 不发送）；随内容长高（上限同新对话）；补「＋」：选文件 = 在这家开新对话，打了的字与文件放进那边的输入框（同拖文件进输入框，上传、出错、发出都在那边，不替用户发出；`useStartIn` 加 files）。
  - ⑥ 公司名就地改名照对话标题的写法（`company-page.tsx: TitleRename`）：只结束一次、输入法回车 / Esc 不算、`cc-inline-edit` + 新的 `.cc-page-title-edit`（标题字号字重）。
  - ⑦ 会话列表时间改成带时区的 UTC、精确到秒（`sessions.py: _when`，`created_at` / `updated_at` / `archived_at`；页面各处都用 `new Date` 解析，翻页游标不用它）；公司页对话的相对时间每分钟跟着走（`useMinuteClock`）。
  - ⑧ 侧栏对话就地改名：输入法选字时的 Esc 只取消候选词，不取消改名。
  - ⑨ 公司页「让 CC 起草」用公司页当前的月份（`usePageMonth`），不再固定上个月。
  - ⑩ 拖卡片时的 Esc 不清侧栏多选：拖动标记改到 `lib/layers.ts`（`setCardDragging` / `isCardDragging`），会话栏和侧栏都认。
  - 证据：`test_cc_session_panel.py`（新增 clampToList、moving 的行为测试与样式断言）、`test_assistant.py`（附件不碰引擎、没留存记一次、会话时间 UTC）、`test_assistant_ui.py`（输入框、共用详情、多选 Esc）；`SKIP_NPM_CI=1 gate.sh` 85 步全过（lock 未变）。没在浏览器里逐帧看拖动与放下过渡（内置浏览器面板隐藏时动画帧不走），留给测试窗口第 4 轮。
- 10-15 夜 用户侧栏反馈（配图与录屏来自 DeepSeek 桌面端；「只改我提到的地方 其它不要动」），本地提交、未推送：A 公司组头最左 `Building2Icon`（`.cc-group-icon`）+ 公司名同会话行标题字号字色（`.cc-project-row .cc-group-toggle`），这家的对话行缩进到公司名起点（`[data-slot="cc-project-items"]` 的 `--cc-row-indent`，电脑 / 桌面窗口 / 手机抽屉、改名输入框同样）；B 单行截断文字悬停滚动（`lib/hover-scroll.ts` 的 `attachHoverScroll` / `useHoverScroll`、`components/ui/marquee.tsx` 的 `Marquee`，`--cc-marquee-*`；侧栏、会话栏、公司页、菜单、标题栏、列表页等原有 `truncate` / 渐隐的单行都换成 `Marquee`）；C 公司组「···」= 分组和排序菜单（`GroupSortMenu` 改成「···」触发，公司组受控，长按 / 菜单键同开），「打开公司」「在这家新建对话」去掉，`sb.newChatHere` 三种语言删除；D「＋」照旧；E 漏斗按钮去掉，已置顶 / 最近 / 月份组标题右侧同一个「···」。公司页入口核对：电脑顶栏「公司名 ›」、手机胶囊菜单 → 会话栏公司卡名称，侧栏「公司」→ 列表页卡片，两端都有。证据：`test_assistant_ui.py` 新增 A–E 源码断言与悬停滚动行为测试（等 150ms、超出 + 右端渐隐、40px/s、最短 0.3s、离开归位、触屏 / 按键拖入 / 改名中 / 减少动态效果不滚、键盘焦点也滚），静态页用构建出的样式在内置浏览器量过：公司名与对话标题文字起点同为 56px、最近组的行不缩进，悬停滚动每 0.5s 约 20px、到末尾停住、离开立即回到省略号。
- 10-16 第 5 轮 D 线（拖动与侧栏，`pearnly/cc-r5-drag`，本地提交、未推送）：
  - 共用拖动 `components/ui/drag.tsx`（设计文档 3.17）：`DragArea` / `DragList` / `useSortableItem` / `useDragItem` / `useDropTarget` / `altMove`，手机长按传感器 `HoldTouchSensor`（按住 250ms 就拿起，或 `touch="move"` 按住后再动才拿起）。拿起的那一项换成挂在 body 上的浮层（DOM 克隆），放下用 dnd-kit 的落位动画（动效变量），限位按「能放的各处 + 原位」的版面位置；拖动标记与 Esc 识别在 `lib/layers.ts`（`setDragging` / `isDragging` / `isDragEscape`，window 捕获阶段记下取消拖动的 Esc）。会话栏卡片改用它（`session-panel.tsx` 的 `SortableCards`、`company-cards.tsx` 的 `SortableCard`），第 4 轮的 `clampToList` 修饰器、`data-landing` / `data-moving` 过渡、`setCardDragging` 删掉。
  - 第 4 轮两处根因与修法：放手后停 0.35–0.5s（卡本身被拖、放下靠 React 提交后才开始的 translate 过渡）→ 浮层 + 放下当帧开始的 Web Animations；滚过最后一张（修饰器只限指针位移、库随后加滚动差把卡推出列表撑大滚动区）→ 浮层不进滚动区、按此刻版面位置限位。headless Chrome（真鼠标 / 触屏事件）实测：松手当帧浮层开始落位、约 200ms 落完；按在底边 2.5s 会话栏只滚到底（scrollHeight 不变），浮层底边 = 最后一张底边；拖动中 Esc 只取消拖动、window 上的 Esc 认得出；键盘空格 / ↓ / 空格调序；触屏按住不动不拿起、按住后再动拿起。
  - 侧栏拖动（用户 10-16 选「公司可拖 + 置顶可拖」）：`thread-list.tsx` 的 `CompanyGroups` 包一个 `DragArea`（`touch="move"`），每组包成 `.cc-group-section` 一块；对话行（`MovableRow`，10-17 已取代：对话不能换公司，见文末 10-17 记录）拖到别的公司组 / 「最近」= `fileSession` 移过去（月份、分店留空，不改置顶），多选整批逐条（`lib/managed-agents/sidebar-drag.ts` 的 `moveDragged`、`sidebarAccepts`），出错写侧栏出错行；没有「最近」组时拖动中临时出一个；公司组整块拖（组头开关是把手）、置顶行（`PinnedRow`）拖着排，顺序按人记在本机（`lib/managed-agents/sidebar-order.ts`，`cc.sidebar.order:<用户>`，新公司 / 新置顶排最上面，`sidebar-groups.ts` 的 `companyOrder` / `pinnedOrder`）；Alt+↑/↓ 在公司组头、置顶行上；搜索时不调序；长按菜单在已拖起时不出（`use-long-press.ts`），抽屉横滑在拖动中不接手（`mobile-shell.tsx`）；三种语言的播报 `sb.drag`。
  - 侧栏视觉审查遗留：抽屉里公司名同抽屉对话行色（`.cc-drawer .cc-project-row .cc-group-toggle`）；已置顶 / 最近 / 月份组标题改用 `SectionHeader`（受控「···」、菜单键打开、菜单开着 `data-open`），CSS 加 `:has(.cc-group-more[data-state="open"])`，「＋」不再消失；「显示更多」内边距改用 `var(--cc-row-pad)`（同 10px，效果不变，缩进对齐 80393ae0 保留）。
  - 第 4 轮第 9 项：多选后拿起卡片按 Esc 不再清多选（`sidebar-keys.ts` 用 `isDragEscape(event)`；键盘拖动中方向键不移侧栏焦点）。公司页「月结」卡头标题不再被挤（`.cc-panel-card-toggle` 不缩，胶囊、摘要让位；headless Chrome 量：标题 24/24，摘要 128→80 省略）。
  - 悬停滚动四处（`lib/hover-scroll.ts`）：只认真正键盘操作带来的焦点（`keyboardLast`）、鼠标关菜单焦点被还回不滚并立即复位；放不放得下按「框宽 - 右端渐隐」（`scrollDistance`）；滚动中文字变了复位重量（只在滚动中观察）；设置里的行、浮着的会话栏顶部补 `data-marquee-host`。
  - 新建对话光标进输入框：`projects.tsx` 的 `focusComposer`（点击当下聚焦一次、新对话换上来后再聚焦一次），`useStartIn`（组头「＋」、公司页、待处理「交给 CC」等）与顶部「新建」都用。
  - 证据：`tsc` 与 `npm run build` 通过；`test_assistant_ui.py` 新增 `SidebarDragTests`（手排顺序 / 新公司置顶、按人记住、能放到哪、拖进公司逐条移且服务端原文显示在出错行 / 被拒的退回、拖动中 Esc、触屏按住与长按菜单、接线、光标进输入框）与悬停滚动行为用例（渐隐内的也滚、文字变了重量、鼠标还回焦点不滚）；`test_cc_session_panel.py` 改成共用拖动的用例；完整闸门见提交说明。没在测试机与真机上点验（交测试窗口第 5 轮）。
  - 审查修正（9 条，含侧栏五处补审中归本线的部分；本地提交、未推送）：① 浮层克隆丢祖先样式（公司组对话行拿起左跳 22px、抽屉 24px，抽屉公司名变淡）→ `cloneOf` 外套一层层祖先空壳（`display: contents`，去标记；外壳去掉 role，不让返回键 / Esc 多数一层弹层）；② 公司组克隆带类上的组上间距、下移 14–16px 底部被裁 → 克隆根 `margin: 0`。静态页用构建出的样式在 headless Chrome 量（网页 / 桌面 / 手机 × 浅 / 深）：公司组里的行、组头、组最后一行、置顶行的文字偏移都为 0、裁切 0、字色同原位（改前复现出 -22 / -24 / -2px、16 / 14px 裁切、ink → ink-2）。③ 取消置顶不出手排顺序 → `sidebar-order.ts` 的 `forgetPinned`，`CompanyGroups` 按已加载的行收拾（单条、批量、别的设备都覆盖）。④「恢复默认设置」一并清手排顺序（`clearSidebarOrder`、`hasManualOrder`，可点条件同改）；改排序方式不清手排，待用户定。⑤ 整批拖进 / 「将 N 项移到…」跳过已在那家的（`lib/managed-agents/projects.ts` 的 `notIn`）。⑥ 长按菜单与拖动同一帧冲突 → `HoldTouchSensor` 按到 `LONG_PRESS_MS` 就不再拿起。⑦ 悬停抓手光标统一 → 共用模块给把手加 `data-drag-handle`，`[data-drag-handle] { cursor: grab; }`（headless Chrome 量：行开关、组头、卡片把手 grab，「···」pointer，输入框 text，不能拖的 pointer）。用例：`SidebarDragTests` 新增取消置顶 / 恢复默认、整批跳过已在那家、500ms 后再动不拿起、克隆带祖先空壳；`test_cc_session_panel.py` 光标断言改到共用标记。

- 10-16 第 5 轮 C 线（内容与后端，工作树 `cc-r5-content`、分支 `pearnly/cc-r5-content`，本地提交、未推送）：第 4 轮测试窗口实测（lab `5db7cd1a`，网页 + 安卓）8 项。
  - ① PDF 附件没有缩略图：消息里的 PDF 卡片改为第一页缩略图——服务端 `files.page_image`（新接口 `pearnly_core.assistant.api.page_image`，GET，`size` 只收 thumb 240 / page 1240，页码 1–500）用镜像里已有的 PyMuPDF（`frappe.Dockerfile` 锁定 1.28.2，识别流水线同一个）把一页画成 PNG，回应带 `Cache-Control: private, max-age=604800`（文件编号对应的内容不变，浏览器同一张只取一次），取法、认法同 `download`（只认这个对话、先读本站留存的那份）；不是 PDF、页码不对、加密、画不出都当作不存在。卡片里从页顶显示，载不出退回文件图标。图片附件照旧显示图片本身。前端没有加 PDF 渲染库（pdf.js 要带 worker / wasm / 字体映射，老 iOS 兼容另算；服务端画图一次到位，安卓、iPhone 一样）。
  - ② 手机 PDF 预览没内容：右侧面板的 PDF 在不能内嵌的设备（`navigator.pdfViewerEnabled` 为假或触屏）改成 `PdfPages`——服务端逐页画的图一页一张往下排，`loading="lazy"` 看得见才取、取到前按 A4 比例占位（`--cc-pdf-page-ratio`），最多 100 页、其余给下载，第一页画不出给下载；页数先看列表（附件），没有问 `preview`（PDF 现在回 `{type: "pdf", pages}`）。电脑上原生查看器照用。用户看不到接口名。
  - ③ 公司页「··· → 重命名」焦点被菜单还给「···」：菜单 `onCloseAutoFocus` 在从菜单点了重命名时 `preventDefault`，焦点留在改名框（对话标题那条路径改名时菜单整个卸下，所以没这个问题）。公司页标题从 `Marquee as="button"` 改成按钮里面放 `Marquee`：遮罩只盖文字，按钮与 h1 不裁切，焦点环完整。
  - ④ 公司页「＋」选文件后侧栏多一条「新对话」——原因见下方「④ 查到的原因」，本线没改（改动点在 D 线的侧栏文件）。
  - ⑤ 删公司规则的「允许」卡：`preview_choice_remove` 原来把公司写成「改前 → 改后」两遍、这一条写在「改前」后面跟一个空「→」；改成与保存卡同一组三行（公司、范围、对象 → 值），问句已说明是删。
  - ⑥ 记住的选择里只记了税号的行（付款规则 0105565099871）补名称：`choices.listing` 先借同一税号别的行记的名称（对方档、历史行），再读一次覆盖这个月那份账套的供应商 / 客户主档（只读这两张；没权限、没读过就照旧写税号）；页面数据不带内部字段。
  - ⑦ 待处理当场选：行上第二行按展开后读到的要选项写（`choiceLine`），规则已定、没有要选的（按钮是「推送」）不再写「付款方式 · 费用科目」；没展开时仍按列表给的类别（服务端列表不逐张读主档，这一点没改）。
  - ⑧ CC 说法：`WRITTEN_NOTE` 与推送说明里的「ERP 单号」改为「推进去的那个程序的单号（Express，MR.ERP 账套是 MR.ERP）」，并要求程序按名字说、不说「ERP」；入账后报单号、泰铢、本地时间，已入账的单「不在待处理里、不指人去待处理页 / 复核页 / Express 检查或修改」；`GUIDANCE` 加同一条（全对话生效）；改单据说明里「只有人能在 ERP 里改」改成「在 Express 或 MR.ERP 里」。
  - 没加新的界面文案（PDF 页图的读屏名字用「文件名 · 页 / 共几页」），messages.tsv 不动。
  - 测试：`test_assistant.py`（page_image 宽度、缓存头、拒绝情形、没有 PyMuPDF 不报 500；preview 的 PDF 页数）、`test_assistant_ui.py`（PDF 卡片缩略图地址与 data-page、PdfPages 懒加载 / 上限 / 下载、nativePdf 判定、choiceLine）、`test_cc_mobile_content.py`（标题按钮结构、菜单焦点）、`test_cowork_companies.py`（删除卡三行、税号补名称两条来源）、`test_cowork_assistant_tools.py`（Express / MR.ERP 说法、已入账不指路）。本机用 PyMuPDF 1.28.2 实画过：A4 首页 240×340（1.7KB）、整页 1240×1755（15KB）、长条页高封顶 6 倍。没在手机上看过，测试机未点验。
  - ④ 查到的原因：输入框加附件时附件适配器先要会话（`attachments.ts` 的 `getSessionId` → `runtime-provider.tsx: ensureSession` → `aui.threadListItem().initialize()` → `sessions.create`），这是上传的前提（文件要放进引擎的那段会话）。会话一建，组件库就把这条新对话从「新对话」变成侧栏里的普通一行（标题空，显示「新对话」），挂在草稿里的公司下；服务端也已有这条记录（标题空），刷新、别的设备、公司页对话列表都会列出，用户不发就一直留着。不只公司页「＋」，任何新对话拖文件 / 粘贴截图都一样。小范围修法（归 D 线，`thread-list.tsx` 拼侧栏行的那一段）：标题为空、又不是当前打开的对话不进侧栏行（服务端标题空 ⇔ 还没发过消息：首条消息会先当临时标题写进本站）；当前打开那条（测试窗口看到的正是这种情况）要不要也藏到发出为止，请开发窗口对照 Claude 定。不能在服务端列表里直接滤掉：`list-sync.ts` 核对时会把「本该在这一页里却没有的」当成别处删了，从本页去掉，正在写的那条新对话会被删掉。
- 10-16 第 5 轮 C 线审查修正（同工作树、同分支，本地提交、未推送）：审查 10 条（含侧栏补审里归本线文件的）逐条核实，都成立，全改。
  - 名称补全只在「对象就是对方」的类别之间借（`choices.PARTY_KINDS`：对方档、付款、抵进项、费用科目）：存货行的归类键也带对方税号，但标签是品名，原来会把「น้ำมันดีเซล」借给只记了税号的付款行，删除 / 保存卡跟着显示错名；存货、部门、收款科目行不出借也不接收。
  - 规则没存名称时 `rule_rows` 不再回退成归类键「purchase|税号」，改用同 Express 行的 `subject_name`（得税号或名称），再按税号补名称。
  - 删除 / 保存卡按 key 找这一条改走 `choices.find`（不经页面每类 `SHOWN`=30 条的截断，名称同列表）；找不到（已删、不是这家的）时只写公司与「这条记住的选择已不在」（沿用 `cowork.company.choice_not_found`），不再写「全公司」加空值。
  - CC 读到的其余「the ERP」都改成按名字说（排队提示 `PUSH_QUEUED_NOTE`、推送 / 结果不明 / 列表 / 回读 / 推送结果 / 识别 / 入账选项 / 定时任务的说明）；改单说明不再写「只有人能在那个程序里改」，改成「不叫人去 Express 或 MR.ERP 改已入账的单，用户问到时只说这次修改到不了它」，与 `WRITTEN_NOTE` 一致；测试断言工具说明与提示里不再有单独的「the ERP」。
  - `page_image`：画好的页图存进这个对话私有目录的 `rendered/`（`_kept` 不扫子目录，不进附件列表；删对话随 `forget` 一起删），同一页再取直接给；引擎里的结果 PDF 第一次取来后在同一目录留一份原件，之后的页图、页数都读它（不再每页列引擎文件、整份下载）；PDF 页数记进缓存；列表记过「本站没留存」的老附件直接 404、不问引擎。前端只在本站确有留存时（本页刚发的，或列表带了大小）取 PDF 缩略图。含图的页编 JPEG（质量 80），纯文字 / 矢量页仍 PNG：本机用 PyMuPDF 1.24.10 实画，矢量页缩略图 1.7KB / 整页 15KB（PNG），满幅噪点照片页（JPEG 的最坏情况）整页 2.4MB，真实拍照会小得多。
  - 公司页对话列表不列标题空的会话（还没发过消息：选了文件就建了会话、没发）；这个列表自己取数，不经 `list-sync`，不会被当成别处删了。侧栏那边仍归 D 线。
  - 测试：`test_cowork_companies.py`（存货品名不出借、规则不露归类键、超过 30 条仍找得到、找不到时的卡片）、`test_cowork_assistant_tools.py`（工具说明不再有「the ERP」、改单说明口径）、`test_assistant.py`（页图落盘复用、JPEG / PNG、引擎结果只下载一次、老附件不问引擎、preview 留原件）、`test_assistant_ui.py`（没留存的老附件不取缩略图、刚发的取）、`test_cc_mobile_content.py`（公司页只列发过的对话）。
- 10-16 第 5 轮 C、D 两线合入本分支（本地、未推送）：基线 `cb1df1a1`（含 80393ae0 显示更多缩进、e5d0f4bf 悬停滚动覆盖面）；`de76bdf4` 合入 `pearnly/cc-r5-drag`（`d69ea1c5`），`6c650329` 合入 `pearnly/cc-r5-content`（`b23c979f`），两次都无文本冲突（`globals.css`、任务记录、设计文档、`test_assistant_ui.py` 两边改的段落不重叠，git 自动合并，两边内容都在）。`frontend/assistant` `npm ci` 后两个前端 `npm run build` 通过；完整闸门 85 步全过（`gate.sh` 的每步 stdin 接 `/dev/null` 跑的草稿副本：原脚本里 `check-dms-offline.py` 会读走剩余步骤列表，只跑前 14 步就返回 0）。仍未做：侧栏里「选了文件没发」的空标题对话仍会列出（C 线 ④ 查到的原因，修法待开发窗口定）、测试机第 5 轮点验、`cand-run.sh`、推送与合入 main。
- 10-16 用户三件（本分支，本地提交、未推送；只做这三件）：
  - ① 单行放不下统一末尾渐隐。用户原话：「一行放不下时，侧栏末尾是渐隐，右侧栏是省略号，统一渐隐」。CC 前端所有单行截断的文字末尾一律渐隐、不用省略号，只有真的放不下才渐隐（放得下的、按内容收缩宽度的末尾不淡）。`lib/overflow-fade.ts`：整页一个 ResizeObserver（`watchOverflow` / `attachOverflowFade` / `markOverflow` / `overflowOf`），观察截断的框与里层文字 `.cc-marquee-text`（改成一直 inline-block），文字宽 > 内容宽 0.5px 以上才设 `data-overflow`，先全读再全写、没变不写；`components/ui/marquee.tsx` 的 `Marquee` 挂上它；`globals.css` 分层外的 `.cc-marquee, .cc-fade-cell`（`text-overflow: clip`，`--cc-marquee-end` 默认 `--cc-row-fade` 24）与 `:is(.cc-marquee, .cc-fade-cell)[data-overflow]` 的 mask；侧栏标题原来的常驻 mask 去掉、并入这一种；各组件类里的 `text-overflow: ellipsis` 全删。悬停滚动不变（`lib/hover-scroll.ts` 的 `scrollDistance` 改为同一个量法：放得下不滚，放不下滚「多出的宽 + 末尾渐隐」；第 5 轮「渐隐里的也滚」随之不再需要）。原来没换成 Marquee 的单行换上：会话栏卡头摘要（月结卡头「需处理 2」等，`company-cards.tsx`）、公司列表 / 待处理 / 定时任务页标题、添加公司与定时任务对话框标题、分组和排序菜单的父项与选项（`sidebar-group.tsx`）、设置里 Express 文件夹路径、对话里工作状态行（`thread.tsx` 的 `typing-dots-label`）；结果文件表格格子（最多 500 × 40）用 `.cc-fade-cell`，整张表一处登记。保留原样：多行截断 `line-clamp-2`（对话出错提示）；结果面板选文件的原生下拉框（量不到文字宽，浏览器自己截）；没用到的官方侧栏组件 `ui/sidebar.tsx` 里 `SidebarMenuButton` / `SidebarMenuSubButton` 的 `[&>span:last-child]:truncate`。
  - ② 聚焦不要光晕。用户原话（截图是右侧栏知识库输入框聚焦）：「全局这种光晕取消掉，可以用边框，但是不能有光晕」。`--cc-shadow-focus` = 内描 1px 页面色 + 外 1px 蓝（`--cc-focus-ring`），去掉 6px 光晕；`--code-focus-halo`（浅、深）与 `--cc-focus-halo` 删除；组件里的 ring 写法换成同一个描边：`ui/dialog.tsx`、`ui/sheet.tsx` 关闭钮（原 `focus:ring-2 ring-offset-2`）、`ui/sidebar.tsx` 五处 `focus-visible:ring-2` → `focus-visible:shadow-focus`；`ui/button.tsx`、`ui/input.tsx` 的半透明 `ring-destructive/20·40`（光晕用的颜色）去掉；全局默认 `outline-ring/50` → `outline-ring`（浏览器自带聚焦框也是实色蓝）。描边粗细不变：统一 1px、就地改名与标题栏 / 侧栏搜索框里描边 1.5px。没动：输入框（composer）聚焦时环加深 + 灰色投影（不是蓝色光晕，属输入框浮起的样子）。
  - ③ 侧栏不列「选了文件但还没发」的空对话（C 线 ④ 查到的原因）：`sidebar-groups.ts` 的 `unsentDrafts`（标题空、又不是当前打开的那条或与它同一服务端编号的另一行），`thread-list.tsx` 的 `useUnsent` 在 Cowork 的 `CompanyGroups`（按公司 / 月份 / 不分组）与 ERP、DMS 的 `DayGroups` 拼行时滤掉；当前打开的照常显示。服务端列表、`list-sync.ts`、组件库数据都不动（多设备同步会把缺的当成别处删了）。公司页第 5 轮已修，没动。
  - 证据：`test_assistant_ui.py` 新增 `UserAsks20261016Tests`（渐隐只在放不下时出现：比框窄 / 等宽 / 差 0.4px / 按内容收缩 / 行内元素不淡，多 1px / 加内边距超出才淡，文字变短去掉、框变窄加上，放大 1.02 的浮层不误判，整页只一个观察器、卸下两层都不再观察、表格一处登记；`unsentDrafts` 藏未发的、保留当前打开的及其另一行；拼行两处接线、同步与服务端列表不滤；所有 `truncate` 只在 Marquee 上，样式表无省略号）；悬停滚动用例改为放得下不滚、多 4px 滚 24px；`test_cc_ccs.py` 焦点变量无光晕、全前端不再有 ring 光晕写法（`ring-[`、`ring-ring`、`ring-offset`、`focus:ring-`、半透明 ring、`outline-ring/50`）；`test_cc_session_panel.py`、`test_cc_mobile_content.py` 随摘要、页标题改动更新。headless Chrome 用构建出的样式量过：长标题 / 长摘要 / 长表格格子有渐隐、短的与按内容收缩的没有，`text-overflow` 都是 clip；聚焦描边浅色 `inset 1px #fcfcfb + 1px #2a78d6`、深色变量 `inset 1px #151515 + 1px #2a78d6`，没有 6px。`npm --prefix frontend/assistant run build` 通过；完整闸门 `SKIP_NPM_CI=1 gate.sh` 85 步全过。没在测试机与真机上点验（交测试窗口）。

- 10-16 对话拖动与其他拖动统一（本分支，本地提交、未推送）。用户原话：「对话的拖动效果怎么没和其它拖动效果统一呢」（总规则仍是 10-15「全部参考视频，非常丝滑」、10-16「统一规则和效果」）。原来侧栏对话行（`MovableRow`）用的是「只拖不排序」：原位留淡影、别的行不让位、拖到别的公司没有落位空档、松手后行直接跳进那一组。
  - 共用模块扩展（`components/ui/drag.tsx`，不另写一套）：「在组之间移动」一类（`isMover`：没有 list、有 group）。拿起原位留空档（`useDragItem` 改 `data-drag-source="gap"`，加 `data-drag-id`）；`DragArea` 新增 `onPlace`（空档该在哪一组，`placeOf`：别的能放的组 / 原组 / 在组与组之间就不动）、`flow`（整列一起滑的各项）；拿起时的那份说明（`lifted`）一直用到底（换组重挂的行 group 会变）；碰撞含原组、横向按浮层；键盘 ↑↓ 一组一组跳（含原组）；放下按空档所在：落位动画（`dropAnimation`，同 dnd-kit 默认的落位，另能落到收着的组头并收起 `data-dropping="into"`）播完才交 `onDrop`（`Landing`，没播时 350ms 内自己交），请求做完前整列仍跟着动；整列滑动 `flowLayout`（版面一变，行、组头、「显示更多」从原位置带过渡滑到新位置，Web Animations 播 translate，不碰行内样式，滚动不算移动，正在滑的接着滑）；落位与滑动都 `startNow`（从松手那一刻起算）。收着的组：`shut` 选项 + `PlacedContext`，空档要去时组头高亮。读屏：去别的组念「将移到」，回原组念「已拿起」，放下念「已移到」或取消。
  - 侧栏（`thread-list.tsx`）：`CompanyGroups` 记 `place`（拖的行与去的组），开着的组才用 `liftRow` 把这一行按当前排序方式排进去（`sidebar-groups.ts`：「显示更多」之外的放在已显示的最后，名次、展开、「显示更多」不变；`withRecents` 补临时「最近」，与真的「最近」同一处渲染）；`onDrop` 返回 `moveDragged`（拖的这一条先移，多选其余逐条跟、不并发，跳过已在目标公司的）；放下时那组眼前的各行记进 `keep`（`companySections` 新选项），不因多了一条把最后一条挤进「显示更多」，落在「显示更多」之外的那条也留在眼前。搜索时仍可把对话拖到别的公司（同第 5 轮、设计文档原规则）；按月份、不分组时不能跨组拖（同现在）。
  - CSS：多选淡影规则不再盖住拖的那一条（`:not([data-drag-source], [data-drag-landing])`），去掉 ghost；`--cc-drag-fold` 落到收着的组时收起。
  - 实测（本机 headless Chrome，构建出的页面 + 本地假接口，真鼠标 / 键盘 / 触屏事件，每帧 rAF 取样，约 16.7ms 一帧，最长帧间隔 25ms）：对话行 A 组「Alpha two」拖到 B 组——按下后约 32ms 出浮层、原位空档，浮层透明度 1→0.9、放大 1→1.02 约 115ms；指针进 B 组那一帧空档移到 B 组按排序该在的位置（b1 之后），A 组 a3、B 组组头、b1 带过渡上移 34px（200ms，cubic-bezier(.32, .72, 0, 1)，每帧最多 10.6px），b2 以下不动；松手后第一帧（10–13ms）浮层就开始落，198–215ms 落进空档，移动请求在落完时发出（松手后 196–211ms，三次）。会话栏卡片同法拖三次：松手后第一帧（12–17ms）开始落，195–210ms 落完；落位是同一个函数、同为 200ms / cubic-bezier(.32, .72, 0, 1)，拿起参数相同（约 31ms 出浮层、约 115ms 到 1.02）。另测：原组内移动后松手（空档不动、滑回原位、不发请求）；拖到收着的「Gamma」（组头高亮、原位空档留着，松手浮层在组头淡掉收起，请求后原位合上、下面各行滑上去）；请求被拒（侧栏出错行写「ไม่มีสิทธิ์ในบริษัทนี้」，那一行从 B 滑回 A 原位）；拖到「最近」与临时「最近」；排在「显示更多」之外（放在已显示的最后，落位后仍在）；多选两条（浮层写 2，空档只给拖的那条，落位后先移它、另一条随后跟过去并滑入）；键盘（空格、↓ 到 B、↓ 到 C、↑ 回 B、空格放下）；键盘 Esc（空档回原位、浮层滑回、多选不清、不发请求）；手机抽屉触屏（按住再动拿起、不出长按菜单，跨组、落位后请求）。对照第 5 轮构建：对话行原来是淡影 + 组整块高亮，松手那一帧行直接跳进 B、别的行不滑；另外侧栏的落位比卡片晚一帧才动（同一份落位代码，侧栏放下那次提交要重画整列），这次 `startNow` 一并修了（卡片也走同一函数，结果不变）。
  - 测试：`test_assistant_ui.py` 的 `SidebarDragTests` 新增 5 个（空档按排序方式的位置 / 「显示更多」之外 / 名称排序 / 最近与临时最近 / 回原组 / 放下后留在眼前；空档去哪与读屏；落位后才交、没播自己交、收着的组落到组头、减少动态效果；整列滑动：换组重挂接着滑、空档不滑、滚动不算、正在滑的接着滑、失败退回；接线与样式），原有接线用例与 `test_cc_session_panel.py` 的共用拖动用例按新代码改。没在测试机与真机上点验（交测试窗口）。

- 10-16 对话拖动统一的审查修复（本分支，本地提交、未推送）。审查 7 条，逐条核实后都改了：
  - ① 滚动锚定（成立）：`flowLayout` 盯着的这段时间把滚动区 `overflow-anchor` 设为 none，停止时还原。复现审查场景（视口 520，Beta 展开显示更多、滚到组头在上面，拖 Gamma one 到 b6）：滚动区内容坐标逐帧没有超过 12px 的跳动。
  - ② 空档在可见范围外（成立）：`Flow` 在空档换组后若空档在滚动区可见范围外，平滑滚进来（`revealGap`）。同场景 scrollTop 348→310 约 100ms 滚完，空档停在可见顶边 88，浮层落到 88，不出列表。拖着时列表又被滚走、放下时空档在可见范围外：落点改为可见范围的边上并收起（`clampIntoView`，`data-dropping="into"`）；拖动库落位前对空档的 `scrollIntoView` 这一次不让滚（`holdStill`）。实测空档在窗口上方 186px 时 scrollTop 550 全程不变，浮层 205ms 落到顶边淡掉，请求在落完时发出（198ms）。
  - ③ 让位起点回拨（成立）：整列滑动去掉 `startNow`，从画出来的那一帧起算（同排序项的 CSS 过渡）；落位仍用 `startNow`，设计文档补了代价。CPU 6 倍节流实测：换组那次提交卡 58ms，之后对话行让位逐帧 21.8 / 52.6 / 77.9 / 87.6%，卡片 21.5 / 52.7 / 78.0 / 87.7%，前段没被吃掉。
  - ④ kept 永不清空、整组留（成立）：放下时不再留整组。新增 `keepInView`（`sidebar-groups.ts`）：移进去的各行（拖的这条与多选其余各条）排在眼前最后一条之前的，每条让那组多显示一条（`projects.ts` 的 `showOneMore`，同「显示更多」计数，被拒就撤，点过「显示更多」不撤）；排在「显示更多」之外的才单独留（kept 只记这几条）。搜索、排序、分组变了或点那组「显示更多」时清掉。实测多选两条进 B：b5 全程在眼前（原来第二条落位时 b5 直接消失）。
  - ⑤ liftRow 与 keep 顺序不一致（成立）：`liftRow` 把「显示更多」之外的那条按排序方式插进留着的那一段。实测先拖 r2、再拖 c2（47 分钟前）进 B：拖动中与落位后都是 [b1–b5, c2, r2]，不换位。
  - ⑥ 搜索时落位后消失（成立）：移过去就不在结果里的行（`matchesAfterMove`）不在那组排空档，同收着的组：组头高亮、落到组头收起。实测搜「alp」把靠公司名命中的那行拖进 Beta：Beta 组头高亮，空档留在 A，浮层落到组头淡掉，落位后 A 的空档合上。
  - ⑦ 行为测试不足（成立）：把放下逻辑抽成 `settleMove`（drag.tsx）与 `draggedSessions`、`moveDragged(failed)`（sidebar-drag.ts），新增行为用例。
  - 帧数据（本机 headless Chrome，构建出的页面 + 本地假接口，真鼠标事件，rAF 逐帧，约 16.7ms 一帧）：
    - 对话行跨组（两次）：拿起 34 / 35ms，到 1.02 约 110ms；松手 15ms 首帧开始落，逐帧 21.6 / 52.6 / 77.9 / 87.6%，218–221ms 落完，请求 215–217ms 发出。
    - 卡片（两次）：拿起 31–33ms，到 1.02 约 115ms；13–16ms 首帧开始落，逐帧 21.4 / 52.9 / 77.6 / 87.6%，197–213ms 落完。两者都是 200ms、`cubic-bezier(.32, .72, 0, 1)`。
    - 回归场景全过：原组松手、收着的组、请求被拒、最近 / 临时最近、显示更多之外、多选、键盘、Esc、手机触屏。逐帧没有整格跳动；大位移只出现在被隐藏的空档本身，以及长距离滑动的中间帧。
  - 测试：`test_assistant_ui.py` 的 `SidebarDragTests` 新增 4 个用例：落位后才交 / 原组不交 / 失败收尾；拖的先移、逐条、跳过已在目标、失败退回并回调；留在眼前（多显示、单独留、整批、撤销、换序、不换位）与搜索命中；落点不出可见范围。原有落位、整列滑动、接线用例按新代码改，没删断言意图。

## 暂停交接（10-16 凌晨，用户关机，次日接着做）

停在安全点：所有改动都已提交，各工作树干净，没有在跑的代理、构建或本机服务。都没推送、没合入 main、没部署。

**现在的状态**
- `pearnly/cc-shell-rebuild` = `e5d0f4bf`。含外壳重构第 1–4 轮、第 1 期月结、第 2 期客户规则、F19，以及用户 10-15 夜提的侧栏五处。
  - 侧栏五处：A 公司图标 + 对话缩进；B 超出就悬停滚动（`lib/hover-scroll.ts`、`components/ui/marquee.tsx`）；C 组「···」改成分组和排序菜单；D「＋」不变；E 去掉漏斗。
  - 闸门 85 步全过。用户已看过预览并同意（「预览可以，就按这个做」）。
- `pearnly/multi-desktop` = `2a1b25bf`：T5 第 1–5 批，各批都审查并修过；T1 跨年度换账套已并入（闸门 89 步全过）。
- 测试机：cowork lab 开着，跑第 4 轮 `5db7cd1a`（包 `index.C1w9Owru.js`），`testcowork.pearnly.com` 指向 lab。测试窗口第 4 轮结果已收到。
- 已清理：sub 分支 cc-shell-panel / sidebar / thread、customer-rules、month-ledger 都已含在 cc-shell-rebuild 里，工作树与本地分支已删，这几个都没有远端分支；t1-cross-year 已并入 multi-desktop 后删掉；happy-brahmagupta 已在 main 里，删掉。
- 仍保留：f19-push-summary（已含在 cc-shell-rebuild，有远端分支，等合入 main 后一起删）；pensive-blackwell（测试窗口在用）。

**次日接着做（按顺序）**
1. 侧栏五处的审查只跑完「视觉」一路，有 5 条发现，要修：
   - 中：手机抽屉里公司名颜色比对话行淡一档。
   - 低：公司页标题滚动时，焦点环被遮罩裁掉。
   - 低：「最近」组打开「···」后「＋」消失、「···」右移。
   - 低：已置顶、最近、月份组标题不能用键盘菜单键打开「···」。
   - 公司组里「显示更多」没缩进：先问用户要不要缩进。

   「范围」「正确性」两路审查被停掉了，要重跑。注意 B 的替换面很宽（设置、待处理、附件名、工具步骤行等都换成了 Marquee）。
2. 第 5 轮：
   - 共用拖动模块：所有拖动统一一套规则和效果，照 Claude 视频 IMG_5192「全部参考视频，非常丝滑」。顺带修第 4 轮的两个问题：放手后停 0.35–0.5 秒才滑；拖到底边会滚过最后一张卡。
   - 侧栏拖动，用户选了「公司可拖 + 置顶可拖」：对话拖到别的公司 = 移过去，拖到「最近」= 移出公司；公司组可拖成任意顺序，按人记住，新公司排最上；置顶对话可拖着排先后；公司里其余对话照排序方式排。
   - 第 4 轮其余问题：
     - 公司页「月结」卡头被挤成「月...」。
     - PDF 没有缩略图；手机上 PDF 预览没内容。
     - 公司页「··· → 重命名」后焦点没进改名框。
     - 浮动会话栏里拖动时按 Esc 会清掉侧栏多选。
     - 删规则的确认卡显示乱。
     - 付款规则行只显示税号、没有供应商名。
     - 待处理行在不用选时仍写「付款方式 · 费用科目」。
     - CC 把 Express 说成「ERP」，还会让人去待处理页改已入账的单。
     - 公司页「＋」还没发送，侧栏就多出一条「新对话」：先查原因。
   - 第 4 轮已定的口径：删规则后由 Express 历史接上算对；公司卡「4 本 / 2 本」各数各列；公司页月份在公司之间沿用。
3. 第 5 轮审查、修复、闸门 → 同步到 lab → 测试窗口点验。
4. 然后：
   - 切到 ERP 的 lab，看一遍 CC；
   - 在测试机跑候选检查 `cand-run.sh`（这次改了 pearnly_core）；
   - 推分支，等 CI 变绿；
   - 快进合入 main；
   - 进维护部署，然后回读；
   - 更新交接与验收记录，清理工作树。
   部署后复验手机暗色导航条（A4）。
5. 多电脑线：
   - 外壳进 main 后，multi-desktop 变基，再做第 6 批（界面）；
   - 第 7 批真机联调要用户配合：录制笔记本上输入 skin3 的密码；在 testcowork「团队管理」加第二个成员；验共享回读；T1 用测试机 70EXP 里的 T7、T8、T9 验跨年；
   - 然后第 8 批。

补记（关机前）：测试机、录制笔记本、本机今晚都关机。已执行 `testenv.sh lab-stop cowork`：
- `testcowork.pearnly.com` 已切回线上（回读：ping 200，线上包 `index.DKAS5pGT.js`）；
- 线上测试租户的定时任务已恢复；
- lab 栈已停，站点和数据卷保留（第 4 轮写入的数据、规则都在）。

次日开机后：
1. 先用这条命令重新起 lab，不带 `PEARNLY_TESTENV_REFRESH`，保留测试数据：
   `PEARNLY_TESTENV_SRC=<cc-shell-rebuild 工作树> PEARNLY_TESTENV_IMAGE=<线上 digest> PEARNLY_TESTENV_LOCAL_SPA=1 bash scripts/testenv.sh lab-start cowork`
   起之前先构建两个前端。
2. 确认 testcowork 已指向 lab，CC 桌面版以 skin3 重新连上。
3. 再发第 5 轮清单。

测试窗口的交接在 `~/pearnly-devtools/test-window/handover-2026-10-16.md`。
- 10-16 用户截图两处（独立小分支 `pearnly/cc-composer-fix`，等对话拖动统一做完一起合入）：
  - 公司页输入框的「＋」跑到上一行，和光标、发送没对齐。原因是按钮少了 `aui-composer-add-attachment`，没落进 add 格。
  - 第一次发送时，用户气泡带「发送中」扫光，工作状态行跟着气泡靠右。用户原话：「发送时我方不应该出现气泡，cc 那边正常出现」（对照 Claude：用户气泡就是平常样子，转圈的点在左边 CC 一侧）。改成用户气泡不扫光（删掉 `data-sending` 和扫光样式、变量），状态行放到左边 CC 一侧（`px-2`，和正式回复下面的状态行同一列）。
  - 侧栏悬停光标：用户原话「侧栏鼠标放上去任意地方，应该是两个手指那个光标，不是 5 个手指的光标……只改变光标效果，不改行为」。
    侧栏与手机抽屉里的拖动把手（对话行、置顶行、公司组头）悬停改回手指，照样能拖；会话栏卡片把手仍是抓手，拖动进行中整页仍是 grabbing。
  - 第 5 轮测试窗口（lab 20b9f387，网页 + 安卓）没过的三处，同一小分支修（本地提交、未推送）：
    - ① PDF 缩略图刷新 / 换设备后没了，只剩「PDF」方块、不发 page_image。根因在引擎适配器的会话文件列表：附件挂进会话时引擎在会话范围另存一份（另一个编号、不可下载），`files.list(scope_id=会话)` 把它列成 `kind: output`，挂载记录的 `file_id` 也指向这份，所以列表里没有上传时的编号。三种编号：上传时引擎给的（`files.upload` 返回，`attach` 写进挂载位置 `/mnt/session/uploads/<编号>/<文件名>`，消息里记的就是这个位置，本站留存也按它存）＝ 消息卡片认的；引擎另存的副本编号（测试窗口看到的 `file_017i…` / `file_011N…`）只在引擎内部。改法：`anthropic_managed_agents.Adapter.files` 按挂载位置里的编号列附件（`kind: upload`），挂载记录指向的副本、或同名不可下载未认领的副本不再列成结果；`files.listing` 照旧按这个编号补本站留存的大小与页数。老数据：挂载位置都是这个写法，能对上；本站没留存的（10-10 以前）只给编号不给大小，卡片退回类型方块、不发请求。`test_assistant.py` 加「刷新后编号不同」用例（改前复现出两份 output、编号对不上）。headless Chrome 用构建出的整页（接口桩）实测：列表按修后的样子给时卡片是第一页缩略图 +「PDF · 2 หน้า」、发一次 page_image；按修前的样子给时只有「PDF」、没有 page_image 请求（同测试窗口现象）。
    - ② 公司页「··· → 重命名」焦点仍落在 body。根因：菜单开着时有焦点陷阱，选中那一下就挂上改名框，它的自动聚焦被陷阱拉回菜单项，菜单项随即卸下，焦点掉到 body（上一轮只拦了「还给 ···」，没管这一步）。改法：选「重命名」时只记一下，等组件库拆掉陷阱后问焦点去处（`onCloseAutoFocus`）时才挂改名框；改名框挂上时自己再拿一次焦点并全选。headless Chrome 真鼠标 / 键盘实测（单独渲染公司页顶部 + 整页各一遍）：鼠标连续三次、键盘回车一次，50ms 起到 2s `document.activeElement` 都是改名框（全选），直接打字进框；改前同样操作一直是 BODY。公司页「＋」选文件开出的新对话（不带字 / 带字各一次）：焦点在新对话输入框、字带过去、文件进了输入框。
    - ③ CC 对已入账的单给「自己去 Express 改，我再回读」。`tools.py` 加一段共用口径 `ENTERED_NOTE`（已入账的 CC 改不了；只说明改不到，给单号、泰铢、当地时间；不给去 Express / MR.ERP / 待处理 / 复核页改的选项，也不说改完我再查；回读照常可以提），接进 GUIDANCE、WRITTEN_NOTE、改单说明；推送说明删掉「只能由人在 Express 改、再回读」，回读说明改成「用户说自己在 Express 改过以后」；改单、查选项、存选项碰到已入账的单，结果带 `entered_note`。`test_cowork_assistant_tools.py` 加断言：全部说明与结果提示里不再有引导去 Express / MR.ERP 改已入账单的写法。
    - 证据：`npm --prefix frontend/assistant run build` 通过；完整闸门 `SKIP_NPM_CI=1 gate.sh` 85 步全过。没在测试机与真机上点验（交测试窗口）。
- 10-16 测试窗口第 5 轮（lab 20b9f387，网页 1024 / 820 + 安卓）查出的五处小问题（本分支，本地提交、未推送；只修这五处）：
  - ① 键盘聚焦没有蓝框。逐个核对：组头待处理数（`.cc-group-count`）、「记住的选择」的「设为全公司」（`.cc-link-btn`）、定时任务卡的任务行（`button.cc-list-row`）、侧栏宽度拖把（`.cc-split`）原来聚焦只换底色或加深线、确实没有描边；顺带同类的「显示更多」、组按钮、胶囊、标题栏公司名也只换底色。改法：这几条 `:focus-visible` 补同一个 `--cc-shadow-focus`；分隔条聚焦时那根线换成聚焦蓝 1px；`globals.css` base 层加兜底（按钮、链接、role=button / tab / switch / separator、tabIndex≥0 没写自己聚焦样式的也是这道描边，组件层的照样盖过它）；侧栏行尾「···」加 `focus-visible:opacity-100`（原来只靠整行 `:has(:focus-visible)` 露出）。消息下面的「复制」、知识库表单的「取消」、行尾「···」在本机无界面 Chrome 里聚焦都有描边（Button 的 `focus-visible:shadow-focus`、`.cc-btn:focus-visible`），没能复现，靠兜底与「···」自显再加一道。实测：构建出的整页（接口桩）逐个键盘聚焦全部可聚焦元素，对话 1024 / 820、深色、820 浮动会话栏、知识库表单、公司页、公司列表、待处理、定时任务、新对话、设置弹窗共 11 个状态，除对话输入框（设计如此：聚焦是外框环加深，3.11）外全部有蓝色描边、聚焦后不透明。
  - ② 侧栏公司组头 Alt+↑/↓ 没有播报。根因：Alt+↑/↓ 直接改顺序、不经过拖动库，原来只有键盘拖动有播报（卡片、置顶同样没有）。改法：共用拖动区（`DragArea`）加一个自己的播报区（同拖动库的 assertive、整段念），`useSortableItem` 给 `moved(位置)`，公司组头、置顶行、会话栏卡片挪完念「移到第 N 位，共 M 项」（键盘拖动同一套词条，没新增文案），到头了没挪不念。
  - ③ 组头空格拿起念成「移到第 3 位，共 4 项」。根因：拖动库拿起后紧跟着报一次「经过」，排序时经过的就是拿起的那一项自己（在原位、离自己最近），`dragAnnouncements` 按排序项照念位置，盖掉了「已拿起」；对话行是组之间移动、自己不是落点，所以没事；卡片、置顶也是排序项，同样会念错。改法：拿起后挪开之前经过自己不念；挪开过再回原位照常念位置。实测四种拖动的拿起 / 移动 / 放下 / 取消：公司组头「已拿起 → 移到第 2 位 → 放在第 2 位」「→ 已取消移动」，置顶「已拿起 → 移到第 2 位，共 2 项 → 放在第 2 位」，对话行「已拿起 → 将移到「…」 → 已取消移动」，卡片「已拿起 → 移到第 3 位，共 4 张 → 放在第 3 位」「→ 已取消移动」。
  - ④ Esc 一次只关一层。根因：浮着的会话栏（document 上）关了却没标记已处理，侧栏多选（window 上，随后收到同一个 Esc）照样清空。改法：会话栏关的时候 `preventDefault`，多选见到已处理的 Esc 不清（`lib/layers.ts` 头上写明顺序：弹窗 / 菜单 > 拖动 > 改名、编辑框 > 浮着的会话栏 > 侧栏多选）。实测 820：多选 2 条、开浮动会话栏、空格拿起卡片，Esc 三次依次：只取消拖动（多选 2、会话栏开）→ 只关会话栏（多选 2）→ 清多选；鼠标拖卡片中同样。
  - ⑤ 渐隐补量。观察器只在尺寸变了时回调；改法：回到前台、pageshow、字体载完（ready / loadingdone）、窗口 resize 时下一帧把登记过的框全部重量，同一个观察器多看根节点与侧栏（变了全部重量），仍只有整页一个观察器。实测（前台无界面 Chrome）：打开 1280、改窄 900、改宽 1600、侧栏改窄 / 改宽，每次逐个比对「真实放不下」与 `data-overflow` 全部一致；模拟后台（把标记清空、页面报 hidden）后回前台立即补齐 6 个；字体 loadingdone 同样补齐。真正的后台标签页无界面 Chrome 模拟不出来（两个标签都报 visible），只能这样模拟。
  - 测试：`test_assistant_ui.py` 新增 `Round5SmallFixesTests`（描边规则、拿起播报行为、Alt 挪动播报、Esc 逐层、渐隐补量行为）并改了两条连线断言；`test_cc_session_panel.py` 改一条导入断言。证据见下方提交说明；测试机点验未做（交测试窗口）。
  - 观察到但没改（不在这五处）：浮动会话栏用 Esc 关掉后焦点落在 body，没还给打开它的按钮。
- 2026-10-16 第 5 轮小修审查后修（本地提交，未推送）：审查 5 条逐条核实，4 条成立、1 条与另一条重复：
  - 分隔条聚焦成三条竖线（两条审查重复报）：base 层兜底匹配到 `role=separator tabindex=0` 的 `.cc-split`，12px 按住区外套了一圈描边、中间还有那根蓝线。改法：components 层 `.cc-split:focus-visible { box-shadow: none; }`，只剩那根 1px 蓝线。实测 1280 聚焦侧栏宽度拖把：box-shadow none、::before 蓝 1px，4 倍截图只一条线。
  - 兜底没去掉浏览器 outline:auto，待处理组头、定时任务页任务行、输入框「＋」等是约 2px 环叠 1px 描边。改法：兜底里一起写 `outline: none`（:where 零优先级，组件自己的聚焦样式照样盖过）。实测这三处 outline none、只剩描边。去掉 outline:auto 后重扫发现原来只靠它显示焦点、自带阴影盖过兜底的按钮会一点描边都没有：设置里分段选择的选中格（shadow-sm，6 处）、手机圆按钮、抽屉「新建」胶囊（组件层阴影），一并补同一个描边（手机顶栏 / 抽屉圆按钮的规则不分层，聚焦规则跟在后面）。重扫：对话 1024 / 820、公司页、公司列表、待处理、定时任务、新对话、820 浮动会话栏、知识库表单、深色、设置弹窗、手机 390 对话与抽屉，除对话输入框（3.11 外框环例外）外全部有蓝描边，无双道描边。
  - 渐隐补量窗口变化时量两遍：根节点观察已覆盖窗口宽，去掉 window resize 监听；测试改用根节点回调并断言不听 resize。
  - 卡片 Alt+↑/↓ 可选链把挪动连同播报一起短路：改成先 `control.move` 再 `sortable?.moved(spot)`。
- 2026-10-16 测试窗口第 6 轮（lab 1badde29，手机 CDP 精确触摸逐帧、电脑脚本鼠标；证据 `~/pearnly-devtools/test-window/round6-frames/`）查出的六处（本分支，本地提交、未推送；只修这六处）：
  - ① 对话行松手落位卡帧（松手后约 80ms 一帧不出，帧一出来浮层 10ms 内跳 75px）。本机无界面 Chrome 用构建出的整页（接口桩，侧栏 14 家公司、50 多行）复现：6 倍节流松手后 64–76ms 一帧不出，第一帧 22%、第二帧就 88–93%；卡片同样 62ms。性能追踪（Tracing + 函数采样，未压缩构建）松手那一个任务的耗时：对话行（触屏，6 倍）约 100ms = 结束拖动后 `holdStill` 量位置 24.6ms（`finish()` 先改了根节点的拖动标记 `:root[data-cc-drag] *`，量位置时整页样式与布局重算）+ React 同步提交结束拖动 68ms（整列重画 45ms：拖动库上下文一变，所有可拖的行、组头重算；提交 24ms：改 DOM 8、布局副作用里拖动库起播落位并量位置 7、被动副作用 8）；卡片（鼠标，6 倍）71ms = 同一处整页样式与布局 24ms + 提交 31ms。落位原来在这次提交里才起播、起点又回拨到松手前那一帧（`startNow`），主线程卡多久，帧一出来落位就走了多少。
    改法（结构性，共用模块一处，卡片、公司组、置顶、对话行同一条）：松手那一下只起播落位——`landOverlay` 在松手时量好落点（空档此刻的位置，排序时加上它让位的位移 `sortShift`；收着的组、可见范围外照旧 `into`），Web Animations 只动 transform（合成线程），从松手这一刻起算（`fromNow`，不回拨、也不等合成线程回报，后者头两帧原地不动）；结束拖动交给 `ReleaseFirst` 等落位播完才做（鼠标、键盘传感器包一层 `landFirst`，手机传感器自带，三种都先交给 `beforeEnd`）。等着的约 0.2s 里拖动库自动滚动不滚（`freezeScroll`），又按下了 / 动画不走（后台）时当场结束；拖动库随后要播它那一次落位时接着等这一次（`dropAnimation` 认 `early`）；落位已播完的在组之间移动在结束拖动那一次当场交给调用方（请求与结束拖动同一次提交）。`holdStill` 挪到改根节点标记之前（不再引起整页重算）。取消（Esc）照旧由拖动库在结束那次提交里滑回，不回拨。松手那一下现在约 6ms（6 倍节流：量位置、起播、冻结滚动），重活（约 90–106ms）在浮层停在落点之后。
    帧数据（rAF 逐帧，浮层位置换算成落位百分比；侧栏 50 多行；改前 → 改后）：
    - 对话行（鼠标）：正常 CPU 首帧 24ms/22%、落位中最大帧间隔 23.6ms → 首帧 13ms/13%、最大 18.9ms，逐帧 13 / 40 / 71 / 85 / 91 / 95 / 97 / 98 / 99 / 100%，196ms 到 100%；4 倍 首帧 48ms、下一帧已 78% → 首帧 14ms，逐帧 14 / 42 / 72 / 85 / 91 / 95%…，最大 18.4ms，198ms 到 100%；6 倍 首帧 76ms、下一帧 93% → 最大 18.6ms，201ms 到 100%。请求松手后 214 / 217 / 222ms（改前 198–201ms），都在落完之后。
    - 对话行（手机抽屉触屏）：4 倍 首帧 42ms、下一帧 88% → 最大 17.6ms，逐帧 0 / 9 / 35 / 67 / 83 / 90 / 94 / 96 / 98 / 99 / 100%，194ms；6 倍 首帧 64ms、下一帧 93% → 最大 18.0ms，194ms。请求 210–215ms。
    - 卡片（鼠标）：正常 首帧 18ms/22% → 13ms/12%，最大 17.0ms，180ms；4 倍 首帧 35ms → 14ms，最大 17.3ms，180ms；6 倍 首帧 62ms、下一帧 88% → 最大 17.5ms，183ms。
    - 落位到 100% 之后有一次长帧（结束拖动的重画：4 倍 50–63ms、6 倍 64–93ms），这时浮层已停在落点。
  - ② 手机长按拿起与长按菜单冲突：原来侧栏的行与组头按住 250ms 后要再动才拿起、按到 500ms 就归菜单。改成按住 250ms（5px 内）就拿起（`HoldTouchSensor` 去掉 `moveToLift`，`DragArea` 去掉 `touch` 参数，行、组头、置顶、卡片把手同一规则）；拿起后手指动出 3px（`TOUCH_DRAG_SLOP`）记到 `lib/layers.ts`（`markDragMoved` / `isDragMoving`，拿起、结束时清掉）；`useLongPress` 到 500ms 只在没动时出菜单，菜单出来后手指再动就调 `onDrag` 收起菜单（行、组头传 `() => setMenuOpen(false)`）。无界面 Chrome 手机抽屉实测（CDP 触摸）：按住 0.3s 后每 30ms 移 8px / 15px、按住 0.4s 后每步 8px、按住 0.7s 后每步 15px 都拖起来并移到 B（请求发出）；按住 0.8s 不动：0.28s 拿起、0.53s 出菜单、手指一动菜单收起、接着拖到 B；按住不动就松手：菜单留着、浮层原地落下、不发请求。改前：0.4s / 8px 没拿起、最后进了菜单；0.7s / 15px 进菜单、拖不起来；0.8s 不动后再动同样拖不起来。注：无界面 Chrome 对 8px 以内的第一下 touchmove 不派给页面（触摸 slop），真机同样只会更早认出在拖。
  - ③ CC 说法：`ENTERED_NOTE` 加一段：条件句、收尾句同样不许说「需要 / 可以在 Express、MR.ERP 或任何地方改」，不指引去任何地方、程序、页面或人改；三种语言各给反例（泰文「ต้องแก้ใน Express โดยตรง」「ไปแก้ใน Express」、中文「需要在 Express 里直接改」「去 Express 改」、英文条件句），反例登记在 `scripts/i18n/keep-cjk.tsv`；可以提议把以后的同类单记成规则（`cowork_company_choice_save`，要用户「允许」）。`ENTERED_RESULT` 同样补上。改单说明、GUIDANCE、WRITTEN_NOTE 仍共用这一段。只改给模型的说明，不做输出过滤。
  - ④ Esc 关浮动会话栏、设置弹窗后焦点落到 BODY。根因：设置弹窗没有 Trigger（齿轮、用户菜单、Ctrl+,、公司页按钮都是发事件打开），组件库关掉时还焦点给 Trigger，找不到，还拦下了默认去处；会话栏卸下时焦点在它里面，随它一起没了。改法：`lib/focus-return.ts`（`opener` 记谁打开的，菜单项打开的记菜单按钮；`giveFocusBack` 还回去，不在了回会话栏开关 / 齿轮 / 用户菜单按钮；`returnFocusAfterClose` 只在焦点原来在这一层里时还；键盘关的 `focusVisible: true`，鼠标关的 false）；设置用 `onCloseAutoFocus`，会话栏在卸下前量。实测（无界面 Chrome、构建出的整页，900 宽浮动会话栏）：Esc 关会话栏 → 会话栏开关（:focus-visible 是）；鼠标点关闭 → 会话栏开关（:focus-visible 否）；齿轮打开、Esc → 齿轮（是）；鼠标点 X → 齿轮（否）；用户菜单里的「设置」、Esc → 用户菜单按钮（是）；输入框里 Ctrl+,、Esc → 输入框。
  - ⑤ 搜索时拖向「最近」组头不高亮：测试机的「最近」收着（不搜索时收着 → 高亮），搜索时各组都展开显示，空档排进去、组头不亮。改法：搜索时算一遍不搜索时的分组（`companySections`，不带搜索词），不搜索时收着的组（用户收起的、排在前几家之后默认收着的，含「最近」）空档要来时组头同样高亮（`shut`）；空档照旧排进展开的组里、落进空档。实测：收起「最近」后搜「one」把 Alpha one 拖向「最近」：改前无高亮、改后「最近」高亮，空档在「最近」里，松手后移进最近；不收起时两者都不高亮（同不搜索）。
  - ⑥ 拖回原组念「已拿起」：`dragAnnouncements` 在组之间移动时，去过别的组再回原组念 `over(名字, 原组名)`（「将移到「原组」」，沿用词条）；拿起时那一次「经过」原组仍不念。实测：拿起「หยิบ Alpha two แล้ว」→ 到 B「…จะย้ายไปที่ Beta Co」→ 拖回「Alpha two จะย้ายไปที่ Alpha Co」（改前是「หยิบ Alpha two แล้ว」）→ 原组松手「ยกเลิกการย้าย…」。
  - 回归（同一套假接口与脚本，改前改后对比最后的顺序与请求）：键盘拖动、键盘 Esc（多选不清、不发请求）、多选整批、请求被拒退回、拖到最近、临时最近、搜索时落到组头、「显示更多」之外、连续两次、滚动后放下、拖着时滚走再放下：结果一致（连续两次与滚走那条的行序差别来自假接口按启动时间算的相对时间，不是代码差别）。
  - 测试：`test_assistant_ui.py`：`SidebarDragTests` 的触屏用例按新规则重写（250ms 拿起、2px 不算拖、慢慢动算拖、0.7s 照样拖、滚动、松手、beforeEnd）、新增长按菜单与拖动用例、松手先落位用例（`landOverlay` 落点与起点、收起、不播的几种、`dropAnimation` 接着等、`ReleaseFirst` 等落位 / 又按下 / 后台 / 新一次），读屏用例加拖回原组，接线断言按新代码改（`startNow` 不再出现、`holdStill` 在 `finish` 之前）；新增 `Round6Tests`（焦点还回去的行为与接线、搜索时收着的组高亮接线）。`test_cc_session_panel.py` 共用拖动规则断言改成新传感器。`test_cowork_assistant_tools.py` 新增已入账单三种语言说法的断言。
  - 没在测试机与真机上点验（交测试窗口第 7 轮）。
- 2026-10-16 第 6 轮修复审查后修（本地提交，未推送）：审查 6 条逐条核实，第 3、4 条是同一处（拿起后「在拖」的距离），合并修；6 条都成立：
  - 慢点按被吞（中）：250ms 拿起时就挂上拦点击，松手走放下，没区分「拿起后没动过」。无界面 Chrome 手机抽屉实测（构建出的整页、接口桩，`tap.mjs`）：对话行按住 120ms 松手打开、320 / 450ms 不打开（抽屉不收）；公司组头 320ms 不折叠。改法：`HoldTouchSensor` 松手时拿起后没动、离按下不到 `LONG_PRESS_MS` 就是点按——拦下 touchend（系统的 click 不用：拿起后的 touchmove 都拦了，各平台据此吞不吞 click 不一）、取消拖动（浮层落回）、当场摘掉拦点击的监听、在按下的元素上补一个冒泡的 click；按到菜单时间（菜单出来了）再松手、或动过再松手照旧。改后：120 / 320 / 450ms 都打开对话并收起抽屉，组头 320ms 折叠（182 → 44）。
  - 长按菜单被浮层盖住（中）：浮层用拖动库默认 z-index 999，菜单 60。实测按住 Alpha Co 组头 800ms：菜单 [206, 351]、浮层 [164, 346] 重叠 140px，浮层在上。改法：`<DragOverlay zIndex={OVERLAY_Z}>`，55（抽屉、手机会话栏 50 之上，菜单 60 之下；设置 70/71 里没有拖动）。改后同一场景菜单在上、字不叠。
  - 「在拖」的距离太近（低 / 中，两条合一）：3px、从按下处量，拿起前允许漂 5px，拿起后再来一个 touchmove 就算在拖，长按菜单就不出。无界面 Chrome 8px 以内不派 touchmove，复现不了，按代码成立。改法：`TOUCH_DRAG_SLOP = LONG_PRESS_SLOP`（10px，从按下处量），「在拖」与「菜单作废」同一个判断；常量取 `hooks/use-long-press.ts`，不另写一份。没改成从拿起处量：那样会出现「菜单已因 10px 作废、却还不算在拖」的一段，松手会被当成点按。回归（`touch2/3.mjs`）：按住 0.3 / 0.4 / 0.45 / 0.7 / 0.8s 后每步 8 或 15px 都拖到 B、发请求；按住 0.8s 不动：菜单出来，手指一动收起、接着拖；按住不动松手菜单留着、不发请求。按住 0.4s 后每步 8px 那条在这套脚本里页面收到第一次移动已在 520ms（CDP 延迟约 100ms，加上 8px 以内不派），菜单先出、随即收起接着拖；真机 8px 一步的第二步（16px，约 430ms）就算在拖、菜单不出。
  - 落位那 0.2 秒按 Esc 被吞（低）：等落位时仍在拖，window 捕获阶段先把 Esc 记成取消拖动的，会话栏、多选随后都不动。改法：`ReleaseFirst` 开始等落位时调 `lib/layers.ts` 的 `releaseDrag`（摘掉记 Esc 的监听，`isDragEscape` 不再因「还在拖」回真，下一次拖动时清掉）；Esc 照样当场结束拖动、不吞事件。实测（`escland.mjs`，选中两行后鼠标拖 Alpha Co 组头到 Gamma 下面）：松手后 40ms 按 Esc，改前多选还在、改后清掉；600ms 后按两者都清；组顺序两者一致。
  - 设置兜底去处按页面先后（低）：用户菜单按钮在齿轮前面。实测焦点在 BODY 时 Ctrl+,、Esc：改前回用户菜单按钮，改后回齿轮（都带描边）。改法：`giveFocusBack` / `returnFocusAfterClose` 的去处可以是几个选择器、按先后试（`Fallback`），设置给 `[齿轮, 用户菜单按钮]`。第 6 轮焦点场景（`focus.mjs`）回归一致。
  - 落位逐帧回归（`rel.mjs`，同第 6 轮那套）：对话行（鼠标）正常 / 4 倍 首帧 13 / 15ms、最大帧间隔 17.3 / 17.7ms、196 / 198ms 到 100%，请求 214 / 218ms；对话行（触屏）首帧 10 / 9ms、最大 17.0 / 17.6ms、194 / 192ms，请求 213ms；卡片 首帧 16 / 14ms、最大 17.6 / 17.8ms、182 / 180ms。同第 6 轮修后。
  - 测试：`test_assistant_ui.py` 触屏用例改成 10px（8px 一步不算、16px 算），加拿起前漂 4px + 拿起后抖动不算在拖、点按（280 / 450ms、抖一下也算、元素不在不补）、菜单时间后松手与动过后松手照旧；新增 `test_escape_after_release_is_not_a_drag_escape`、`test_lifted_overlay_sits_under_menus_and_over_drawers`；焦点用例加去处先后。`test_cc_session_panel.py` 两条接线断言按新代码改（浮层带 `zIndex`、`isDragEscape` 带 `released`）。
  - 没在测试机与真机上点验（交测试窗口第 7 轮；iPhone 按住时手指抖动、点按补 click 两条要真机看）。
- 2026-10-16 测试窗口第 7 轮（lab c401da96，手机 CDP 触摸 + 电脑真实鼠标逐帧；证据 `~/pearnly-devtools/test-window/round7/`）查出的六处，本分支本地提交、未推送（只改前端与偏好存储，不碰 `assistant/tools.py` 与标题生成）。本机无界面 Chrome 用构建出的整页（接口桩：4 家公司、10 多行）改前（c401da96）改后对比：
  - ① 真的移过去了却念「已取消移动」。根因不是「新空位在指针下被当成放回原处」：拖动库松手时先调放下的处理（`onDragEnd`）、后发播报；落位已播完（`ReleaseFirst` 等落位，`landed`）时放下的处理当场把这一项交给调用方（`settleMove` → `landing.land()`），随即把空档清回 null（`place(from, null)`），播报再读 `placed()` 就成了「原组」。改法（共用模块一处）：放下的处理在交出去之前先记下去处（`dropped`：能放的别的组，或 null = 没移），播报按它念；排序的一列（卡片、公司组、置顶）落回原位（经过的是它自己）也念取消，挪了念放到第几位，同 `onDragEnd` 动没动。实测（泰文）：鼠标拖到 Beta 组头、拖到 Beta 的行、键盘 ↓ + 空格，改前三条都发了 `move sesn_a2→comp_B` 却念「ยกเลิกการย้าย Alpha two」，改后念「ย้าย Alpha two ไปที่ Beta Co แล้ว」；先拖到 Beta 再拖回 Alpha 放下：两版都不发请求、念取消。公司组头键盘空格 ↓ 空格两版都念「วาง Alpha Co ที่ตำแหน่งที่ 2 จาก 4」；空格拿起再空格放回、↓↑ 回原位放下：改前念「วาง…ที่ตำแหน่งที่ 2 จาก 4」，改后念「ยกเลิกการย้าย Alpha Co」。
  - ③ 手机上浮层盖住收着的组头、看不到高亮：`Lifted` 在空档换组那次提交后看有没有只能高亮组头的落点（`[data-drop-target]`），有就给浮层 `data-peek`，`.cc-drag-lift` 透明度换成 `--cc-drag-peek`（0.5），沿用原有 opacity 过渡（--cc-dur / --cc-ease）；落到组头收起的淡出规则写在后面照样盖过。实测（390 宽抽屉，CDP 触摸）：拖到收起的 Gamma 组头上停住，改前 `opacity 0.9`、改后 `0.5`（组头高亮透出来）；再挪到展开的 Beta 行上，恢复 0.9。
  - ④ Mac 上快捷键写成「Ctrl ,」：`components/shortcut.tsx` 按设备换字（苹果设备 Ctrl → ⌘、Alt → ⌥；看 `userAgentData.platform` / `platform` / UA），源码仍按 Windows 写法给，全仓快捷键提示本来就只经这里（`ShortcutTests` 守住），各快捷键的处理本来就 ctrlKey || metaKey。实测（无界面 Chrome，platform MacIntel）：用户菜单「การตั้งค่า」后改前「Ctrl ,」、改后「⌘ ,」。
  - ⑤ 「分组和排序」子菜单选项读屏听不出选中：单选（分组依据、排序方式、升降序）改成组件库 `RadioGroup` / `RadioItem`（menuitemradio + aria-checked），显示里的两项改成 `CheckboxItem`（menuitemcheckbox + aria-checked，点了不关菜单），✓ 用 `ItemIndicator` 同一个图标，样子不变。实测（手机抽屉换页子菜单）：改前全是 `menuitem` 无 aria-checked；改后「บริษัท」`menuitemradio:true`、其余 false，「มากไปน้อย」true，「กลุ่มที่ว่าง」`menuitemcheckbox:true`。
  - ⑥ 公司组头长按菜单左边露出各行首字：菜单按「···」右对齐，左边缘（54）正好落在组里各行文字起点（46）右边几像素。手机上（`alignHead={isMobile}`）公司组头的菜单改为左边贴着组头开关的左边（`align="start"` + 打开时量出的 `alignOffset`），盖住各行起点；电脑照旧右对齐。实测（390 宽、长按 Alpha 组头 0.8s）：菜单左边 54 → 22（组头图标那一列），各行文字起点 46 在菜单下；截图 `scratchpad/r7/after-longpress-menu.png`。
  - 测试：`test_assistant_ui.py` 新增 `test_drop_is_announced_by_what_really_happened`、`test_the_float_turns_see_through_over_a_folded_group_head`、`test_group_and_sort_choices_say_which_one_is_picked`、`test_phone_company_menu_lines_up_with_the_head`、`test_shortcuts_use_command_on_mac_and_ctrl_elsewhere`；排序落回原位的断言由「放到第 3 位」改为念取消（第 7 轮定的口径），iPhone 接键盘那条按 ⌘ 断言；`ShortcutTests` 允许 shortcut.tsx 里那一行换字表；组头菜单接线断言按新属性改。
  - 没在测试机与真机上点验（交测试窗口第 8 轮：读屏真念、手机半透明的观感、菜单位置）。
- 2026-10-16 第 7 轮第 2 项：个人排列按人跨设备同步（单独一个提交，本地、未推送）。用户 10-16 选「公司可拖 + 置顶可拖」时说的是「按人记住」，原来侧栏手排顺序、右侧栏卡片顺序与开合、侧栏「分组和排序」都只存 localStorage。
  - 服务端：`pearnly_core/assistant/prefs.py`（读 `read()`、存 `save(prefs)`）+ `assistant/api.py` 两个接口：`GET pearnly_core.assistant.api.layout` → `{prefs: {...} | null}`，`POST pearnly_core.assistant.api.save_layout(prefs)` → `{prefs}`。存在当前会话用户自己的用户默认值（Frappe User Default，`DefaultValue` 表 parent = 用户），键 `pearnly_cc_layout`，一份 JSON；只按会话用户读写（接口不收用户名），Guest 拒（Not Permitted）；只收 JSON 对象、顶层只留 `order` / `view` / `cards`（各自是对象），序列化后上限 32 KB，不合规拒（Invalid value）。同侧栏页面顺序 `desk/navigation.py` 的 `pearnly_sidebar_order_*` 一样的存法，不加 DocType、不加迁移。
  - 前端：`lib/prefs-sync.ts`（各份由自己的模块 `registerPref` 登记，不反向导入，不绕循环）：知道是谁了（`assistant.tsx` 的 `syncPrefs(profile.user)`）、回到前台（隔 10 秒以上）时取；服务端有就换成它（后写的为准），各份照样写进本机缓存；改了就存（400ms 内连着的改动合成一次，三份合成整份覆盖）；存不上（离线）记 `cc.prefs.unsaved:<用户>`，回到前台、联网、下次打开时把本机的补存上去，不被服务端旧的盖掉；取的时候本机又改了，同样本机为准；服务端还没有时把本机原来记下的（不是默认的）整份迁上去。`sidebar-order.ts`（order：公司组、置顶，各最多 300 条）、`sidebar-view.ts`（view）、`company-panel.ts`（cards：卡片顺序与开合）各自登记；按数据自动收拾的改动（取消置顶了从手排置顶顺序里去掉，`forgetPinned`）在取服务端那份期间不算人改的，不因此把本机的当成后写的、盖掉别的设备改的（换成服务端的以后照新的那份再收拾一遍）；会话栏停靠时开着没有、侧栏组折叠（`sidebar-fold.ts`）仍按设备，不同步。本机缓存保留：打开时先按它显示，离线照样能用。
  - 实测（无界面 Chrome、构建出的整页、接口桩带 layout / save_layout）：设备 1 本机有旧版记下的公司顺序 Gamma, Alpha, Beta, Delta 与按名称排 → 打开后服务端收到同一份（迁上去）；设备 2 清空本机 → 打开后侧栏就是 Gamma, Alpha, Beta, Delta、本机缓存写上；设备 2 上 Alt+↓ 挪 Gamma → 服务端变成 comp_A, comp_C, comp_B, comp_D。改前（c401da96）设备 2 是默认顺序、服务端没有请求。
  - 测试：`test_assistant.py` 新增 `LayoutPrefsTests`（只读写自己的、Guest 与坏值拒收、32 KB、接口读写分开）；`test_assistant_ui.py` 新增 `test_personal_layout_follows_the_user_across_devices`（迁移、整份覆盖、折叠与停靠开关不触发、换成服务端的并写本机缓存、离线时改的补存、取的时候又改了本机为准、取的时候自动收拾不算）。
  - 没在测试机与真机上点验（交测试窗口第 8 轮：两台设备真拖一遍，看手机主屏幕 app 回到前台后是否换成另一台改的）。
- 2026-10-16 测试窗口查出的 CC 三处（lab 对话 `bji1m432s1`，调查结论：都不是跨对话串数据，没存下错规则；本分支本地提交、未推送；改了 pearnly_core，合入前要在测试机跑候选检查）：
  - (a) 规则对象说成ซ่อมไว。根因：推送只记了「公司规则定的」，没记是哪一条；`WRITTEN_NOTE` 叫模型去 `<client_choices>` 找；SD-6909-1010 当时用的สะอาดดี那条后来不在了，说明里只剩ซ่อมไว那条费用科目规则，模型就当成了这张单的来源。改法：
    - 推送准备把用上的公司规则冻结进目标（`company_choices.used` → `finish` 放进 `decision["company_rules"]` → 目标 `company_rules`：编号、对象名、归类键、值、范围；分店的比全公司的具体，同 `pick`）；`rules.for_push` 带上编号与对象名。
    - 回读 `written.company_rules`：`[{kind, for, tax_id, value, value_name?, branch, still_saved, now_value?}]`；还在不在经新钩子 `cowork_push_company_choices_saved`（`companies.rules.values_now`）一次查完，查不到只是不说，不挡回读；编号不交给模型。
    - 说明：`WRITTEN_NOTE` 的 company_rule 改成看 `written.company_rules`、没有就只说是公司规则，不从 `<client_choices>` 或知识库里挑；`<client_choices>` 每行带对象税号，并写明是现在的规则、不是入账记录；公司工具的总说明同口径。
    - 存规则的对象只按传进来的那张单算（`_party`），并由服务端核对：新增 `expect_party`（CC 说给用户的对方税号或名称），没给或与这张单的对方对不上就回 `not_saved`、不写库，原因里给这张单真正的对方。卡片前就核：共用层工具声明新增 `refuse`（`pearnly_core.assistant.tools.refusal`，`toolrun.execute` 出卡片前按这段对话现算，要拒的不出「允许」卡、不执行，按出错交回模型、留记录；核对本身出错照常出卡片）；点了允许后处理函数再核一遍。`ENTERED_NOTE`、`ENTERED_RESULT` 与工具说明加 `RULE_SUBJECT_NOTE`：规则对象就是这张单自己的卖方（销项买方），按单上名称说，document 只传这一张。
  - (b) 「查看了单据 ×2（1 个失败）」。根因：公司说明只给了税号，模型把税号当 company 传，`default_scope` 按主档编号找不到就失败（判本对话公司之前）。改法：`company_tools.resolve`：13 位数字按税号认——本对话公司的税号等于没给，本所唯一一家就用那家，几家同税号失败并列出编号；`default_scope` 与 `_company`（公司工具）同一认法；`service.with_tax_id`。公司说明第一行带主档编号，并写明本公司不用传 company、别传税号。
  - (c) 标题成了「ภาษีเดือน 9 สามบริษัท」。根因：标题模型把提示里的示例照抄回来；另外模型默认开着思考，60 token 常被空思考块用完、标题回空。改法：`summarize` 明确 `thinking: disabled`、`max_tokens` 200，只收 `end_turn` 的回答、只取文字块；这个模型不接受关思考（400）时不带 thinking、给足 token 再问一次。`contracts.copies_example`：回答里出现示例、而这条对话自己的内容撑不起这个话题（示例相邻两字出现不到 60%）就算照抄；`make_title` 照抄的重问一次（不带 too_long），仍抄或没给保留临时标题，总共最多问两次；对话本身就在说这件事（「เมื่อคืนเป็นยังไงบ้าง」→「ผลงานเมื่อคืน」）照收。示例从 `engines.base.TITLE_EXAMPLES` 传入，不另存。素材只读本行对话编号的事件、只推给本行主人（原来就是，这次补了测试）。
  - 影响面：说错规则对象——凡由公司规则定、规则后来删改或同类规则不止一条的已入账单（今后推送的都带冻结；之前推的没有 `company_rules`，只说是公司规则）；查单失败——所有走 `_company_scope` 的读工具与公司工具；标题——所有新对话的自动标题。
  - 测试：`test_cowork_companies.py`（相近单号、几家供应商已有规则时存的仍是这张单的对方；说成别家 / 没说不写库、卡片前就拒；按税号认公司、本公司税号同没给、几家同税号列出编号；说明第一行带编号；`<client_choices>` 带税号）、`test_cowork_account_suggest.py`（冻结用上的规则、别家规则与账套记忆不算、分店优先；written 带对象与还在不在、删了 / 改了照说）、`test_cowork_assistant_tools.py`（回读一次查完、查不到不挡；说明不再叫去 `<client_choices>` 找、带规则对象口径）、`test_assistant.py`（卡片前拒绝不出卡、不执行、留记录；关思考与 token 上限、非 end_turn 不收、400 退一步；照抄示例重问一次、示例 + 解释、对话本身就是这个话题照收、本次素材回归；标题只读本对话、只推本人）。改了旧断言 6 处（按新行为更新字面，没删）：`<client_choices>` 两行带税号、`for_push` 多回编号与对象名、三处 `choice_save` 加 `expect_party`、标题模型的假回答带 `stop_reason`。完整闸门 85 步全过。
  - 点验清单（交测试窗口）：① 在 p258ek6vak 的对话里问「SD-6909-1010 ช่วยเปลี่ยนบัญชีเป็น 53-05-28-00」：CC 说改不了、给单号，说按公司规则入账时不提ซ่อมไว（这张是旧推送、没有冻结记录，应只说是公司规则）；提议存规则时说的是สะอาดดี，卡片上「值」一行是สะอาดดี → 53-05-28-00。② 让 CC 拿 RP-6909-0015 去存「สะอาดดี」的规则：不出卡片，CC 改口说明对方。③ 存好后重推一张สะอาดดี的新单，再问它怎么入账：说出按「สะอาดดี」那条公司规则；删掉规则再问，说那条已不在。④ 公司对话里查单、查推送结果，步骤里不再有「1 个失败」。⑤ 新开几段对话（泰 / 中 / 英），标题不出现提示示例原文、不再停在原句。
- 2026-10-16 第 7 轮两次提交（`528e0e48` 偏好同步、`547b43e9` CC 三处）的审查，7 条逐条核实，都成立，都修了（本地提交、未推送；改了 pearnly_core 的 `assistant/prefs.py`、`contracts.py`、`titles.py`，合入前要在测试机跑候选检查）：
  - 偏好同步整份覆盖（审查 1、5、6、7，同一根因）：一直开在前台、没重取过的设备，改任何一项（或按数据自动收拾取消置顶的）都把本机三份整份存上去，盖掉别的设备改过的；资料没到之前的自动收拾被当成「知道是谁之前就改过」，整份旧缓存存上去、不再取服务端的；取失败也记成取过；同时可能发两个 GET。改法（结构上一次改到位）：
    - 按项记、按项存：`prefChanged(份, 项, tidy)`，只记改了的项（`order.companies`、`order.pinned`、`view.by`… `cards.order`、`cards.collapsed`），存时只交这几项；没存上的项记在 `cc.prefs.unsaved:<用户>`（JSON 列表；旧版的 "1" 当作全部）。服务端 `prefs.save` 按项并进已有那份（`{**旧[份], **交来的}`），回合并后的整份，合并后再卡 32 KB。
    - 取：先取后存。服务端各项换进来，本机没存上的项留本机的、随后存上去（原来是有没存上的就只存不取）；服务端某份没有的项照本机。取成功才记取过；同一个人同时只取一次（共用进行中的那次）；回到前台、窗口拿到焦点（电脑上一直开着的窗口不触发 visibilitychange）、联网时都按同一条件再取（有没存上的，或隔 10 秒以上）。
    - 自动收拾（`forgetPinned`）不是人改的：还不知道是谁、正在取时只改本机；刚取过（2 秒内，`TIDY_FRESH`）才当改了置顶这一项存上去；否则先取一次，换成服务端的以后侧栏照新的那份再收拾、那时再存。不拿本机可能过时的值盖服务端，也不丢「取消置顶就从手排顺序去掉」（第 5 轮）的跨设备效果。
    - 迁移不变：服务端还没有、本机有一份不是默认的，就三份整份存上去。
    - 剩下的限制：两台设备几乎同时改同一项（例如都在拖公司组顺序），后存的为准。
  - 标题照抄示例的判断按回复算（审查 2）：复现成立，事故那条回复里有「เดือน 9」「บริษัท」「ภาษีซื้อ」，示例 14 组相邻两字命中 11 组，判成「对话撑得起」照收。改法：只看用户首句（`titles.summarize` 传 `source=message`）；首句撑得起 80%（`EXAMPLE_SUPPORT`）才第一次就收，否则重问一次；重问仍是同一句示例时首句撑得起一半（`EXAMPLE_REPEAT_SUPPORT`）才收，否则保留临时标题。「เมื่อคืนเป็นยังไงบ้าง」起名「ผลงานเมื่อคืน」（首句命中 5/9）现在要问两次才收，多一次标题模型调用，只在回答正是示例时发生。
  - 写工具把别家税号当 company（审查 3）：成立。`resolve(..., write=True)`：存 / 删团队笔记、公司规则只认本对话公司的税号，别家的税号回失败、原因里给那家的主档编号并说明对方税号不放 company；`_no_company` 带上这条原因。新的 `company_refusal` 声明在笔记存 / 删、规则删的 `refuse` 上，规则存的 `choice_refusal` 先过它：出「允许」卡片前就拒、不写库。读工具照旧按税号认别家（结果写明是哪家）。
  - 名称包含就算对上（审查 4）：成立。`_same_party`：单据带 13 位税号时，给名称要与单据名称归一后完全相同（不然原因里给税号，模型改传税号）；单据没有税号才允许一方包含另一方，且短的至少占长的 60%（`NAME_COVER`）。
  - 测试：`test_assistant.py` 新增 `test_saves_merge_by_field_so_another_device_is_not_rolled_back`，标题用例加回复里带记账词的回归（`copies_example` 只看首句、两次都抄保留临时标题）；`test_assistant_ui.py` 偏好同步用例改为按项断言（只交改了的项、离线补存先取后存、取的时候改了别的项照样换成服务端的），新增久没取过时自动收拾先取后存只交置顶一项，新增 `test_layout_sync_never_pushes_a_stale_layout_and_retries_failed_fetches`（资料没到前收拾不存、同时叫两次只取一次、取失败联网再取、刚取过拿到焦点不再取）；`test_cowork_companies.py` 写工具传别家税号失败、卡片前拒、不写库，三个写工具声明 `refuse`，名称部分相同在有税号时拒、`_same_party` 各情况。按新行为改了旧断言 6 处（没删）：偏好用例「取的时候本机又改了」改为没改的项换成服务端的、离线补存的调用顺序与没存上标记的格式；标题用例「เมื่อคืน」按重问一次断言（两处）、示例来源用首句；规则对象「สะอาดดี คลีนนิ่ง」（部分名称）从「对上」挪到「有税号时拒」。
  - 没在测试机与真机上点验（交测试窗口第 8 轮）。
- 10-16 用户截图问「已经回读的滚动状态原本就是这样设计的吗」（月结卡「核对」的账套行：右边「testcowork 10月15日 21:55 回读」被截成「0月15日…」，悬停时左边账套名和右边状态两处一起滚）。悬停滚动本身按设计（「所有超出的内容……鼠标放上去自动会滚动」），但右边状态不该被截：段行与账套行右侧状态改为完整显示（`.cc-panel-extra.cc-month-status`，上限放宽到「整行减 4rem」），左边名字让位，放不下时只有它渐隐、悬停滚动。独立小分支 `pearnly/cc-ledger-fix`，等第 8 轮做完一起合入。
- 10-16 用户截图（分组和排序菜单里「排序方式」那一项外面一圈蓝框）：「这个边框颜色不要太泛滥吧」。菜单项、子菜单项、单选 / 勾选项、下拉选项只用底色表示当前项（同 Claude 的菜单），聚焦时去掉浏览器自带的 outline 与兜底描边；蓝色描边只留给按 Tab 走到的独立控件（按钮、输入框、行等）。下一次同步 lab 时一起带上。
- 2026-10-16 测试窗口第 8 轮（lab 49de1497，手机 CDP + 电脑；证据 `~/pearnly-devtools/test-window/round8/`）查出的七处，本分支本地提交、未推送（改了 pearnly_core 的 `assistant/tools.py`（`steps()` 带 `ask`）、`assistant/toolrun.py`（拒绝的结果带 `status: denied`）、`assistant/engines/base.py`（标题提示的年份写法），合入前要在测试机跑候选检查）。本机无界面 Chrome 用构建出的整页（接口桩：4 家公司 + 最近，另加月结接口）改前（10920e5b）改后对比：
  - ① 键盘拖动跨一次组后焦点掉到邻行。根因：组件库对话行（assistant-ui `ThreadListItemPrimitive.Root`）自带 ↑ ↓ 导航，拖动中按在行上的方向键先被它拿去移焦点（侧栏自己的 roving 已在拖动中让开，这一层没有）；拖动库在 document 上照样收到同一个键，于是焦点和空档各走各的；拖的那一行换组时节点卸下重挂，焦点更没了着落，之后键按在别的行上，拖动看着卡住。改法（共用模块一处，`components/ui/drag.tsx`）：拖动中按在把手上的方向键（含 Home / End / 翻页）标成已处理（`DRAG_KEYS`，组件库的导航见到已处理就不接手），Alt+方向键在拖动中也不挪（`altMove`）；`DragArea` 记下键盘拿起的那一项（`KeyedContext`），两种项的把手 ref 走 `useFollowFocus`，换组重挂后焦点跟到新节点；放下 / 取消后几秒内焦点丢了就接回移动后的那一行（请求做完又重挂也接回；落进收着的组给组头开关；鼠标点了别处不再接回；`keepFocusOn`）。侧栏行的键盘处理在拖动中只交给把手（`thread-list.tsx`）。实测：「最近」的 Loose one 空格拿起、↑×3：改前焦点 Delta one → Gamma two → BODY；改后一直在 Loose one，连跨三组进 Beta、空格放下后焦点仍在它（Beta 组里）。Alpha two ↓×3：改前焦点 Alpha three → Beta 1 → Beta 2；改后一直在 Alpha two，进 Delta。Esc 取消：焦点回原组的 Alpha two。长列表里拖进收着的 Extra 6：放下后焦点在 Extra 6 组头。公司组头空格拿起 ↑↑ 放下：顺序与焦点同改前。
  - ② 月结卡「核对」的账套行：改成两行（`month-ledger.tsx`，`.cc-month-set`）：第一行账套名、第二行状态（12px 淡色），各自全宽，各自是一行悬停滚动的范围（`data-marquee-host`），同一行不会两处一起滚；上一轮的 `.cc-panel-extra.cc-month-status` 规则只留给段行（`[data-slot="cc-month-stage"] > …`）。实测（1024×800、右侧栏 280、曼谷时区、中文）：改前账套名 60px、状态框 156px 装 178px 的「testcowork 10月15日 21:55 回读」；改后两行各 220px，状态 178px 放得下不渐隐，长账套名「69NIEXCP NIPA EXCHANGE 2569-2570」243px 只在名字那行渐隐。
  - ③ 规则卡没写对方。根因：CC 把票面单号「SD-6909-1010」当 `document` 传（单据编号是 `CWH-…`），服务端读不到这张单，卡片算不出对方、只写了值；出卡片前的核对也因读不到而放行。改法（`pearnly_cowork/assistant/company_tools.py`）：`_document` 先按单据编号读，读不到时按票面单号找本人看得到的（`history.storage.by_invoice_no`，权限同列表），只有一张就用它；几张、没有、读不了就抛 `UnknownDocument`，出卡片前就拒（原因里列出看得到的几张），点了允许也再核一遍、不写库；卡片的对方仍按服务端从单据算出的（不是模型说的），单据只有税号时写税号；规则来源记认出的那张单；工具参数说明写明传单据编号。删除卡按 key 找，本来就写「对方 → 值」，补了用例。
  - ④ 拒绝后步骤仍写「记下了公司选择」。根因：步骤行与摘要只按工具名取做完的说法，不看是否被拒；重开对话后连本页的「已拒绝」结论都没有（拒绝的结果是一句随语言变的话）。改法：拒绝时交回模型的结果带 `status: denied`（`toolrun.decide`，话照旧在 `message` 里）；`tool_steps` 多给各「允许」工具卡片上的问话（`ask`）；被拒的一步行与摘要写问话（去掉问号）加「已拒绝」（摘要「把这个选择记到公司 · 已拒绝」，重开对话时行尾淡色「已拒绝」），结果写明 `not_saved` / `failed` 的算失败（`lib/step-summary.ts`、`tool-uis/steps.tsx`）。「การ」残句：拿不到那段对话的事件记录（lab 栈已停、容器已删），按代码只找到一条会留下前几个字的路：同一条回复的预览开头（`event_start`）再来一次（推送线程断了重接，事件流把没写完的这一条从头再推）时，对话页另开一段，先到的「การ」留在原来那段、没有正式事件收口；另外出错重试（`session.status_rescheduled`）时没写完的预览也不会再收口。两处都改了（`reducer.ts`：同一条再来就用原来那段、清空重写；重试时清掉没写完的预览）。是不是模型自己输出的残句没法证实，重开那段对话（预览不入记录）若仍有「การ」就是模型输出。
  - ⑤ 手机第一次打开公司信息报「出了点问题，请重试。」。这句只在不是本站拒收时出现（网络断开、网关错误、服务端原始异常，10-15 生产公司页出现过数据库死锁 1213）；截图里月结卡取到了、公司详情没取到，同时发出的还有月结、待处理与排列同步（展开公司卡时存卡片开合）。lab 栈已停、拿不到错误日志与网关记录，根因没法定到是哪一种；前端取详情、偏好同步、对话归属的先后里没有找到会让详情请求失败的路径。改法：公司详情碰到一时的失败（`lib/frappe.ts` 的 `transientError`：fetch 抛 TypeError、5xx / 408 / 429、1213 / 1205）隔 0.6s、1.2s 再取，最多 3 次（`withRetry`）；再取时先收起上一次的报错、转圈（`company-panel.ts`）；本站拒收照常马上显示。站点回错误码又没有原文时带上状态（`RequestError`）。
  - ⑥ 手机拖到收起的组头：浮层让开——里面的字与条数隐去，只剩 0.15 的一层底（`--cc-drag-peek`、`.cc-drag-float[data-peek] .cc-drag-lift > * { opacity: 0 }`），组头的字与高亮清楚可读；共用拖动一条规则，减少动态效果时不过渡。实测（390 宽抽屉，CDP 触摸拖 Alpha two 停在收着的 Gamma 组头上）：改前浮层 0.5、字 1；改后浮层 0.15、字 0，截图里「Gamma Co」清楚。
  - ⑦ 英文标题写成佛历：标题提示加一条（`engines/base.py`）：泰文标题可留佛历，其余语言用公历（减 543），「Sep 2026」不是「Sep 2569」。
  - 测试：`test_assistant_ui.py` 新增 `Round8Tests`（键盘拖动焦点跟随与放下后接回、浮层让开、预览重来与重试、详情一时失败再取）；`test_cc_ccs.py` 新增 `test_a_denied_step_reads_as_not_done`，摘要与步骤行断言按新写法；`test_cc_month_ledger.py` 账套行两行断言；`test_cowork_companies.py` 新增 `test_a_card_always_names_the_party_worked_out_from_the_document`；`test_assistant.py` 拒绝结果带 `status: denied`、`tool_steps` 的 `ask`、标题年份写法；拖动相关旧断言随新签名改（不删断言）。
  - 没在测试机与真机上点验（交测试窗口第 9 轮）；④ 的「การ」与 ⑤ 的根因都只按代码与现象推断。
- 第 8 轮提交（09613747）的审查十条，逐条核实后本地再提交一次（改了 pearnly_cowork 两个文件、前端与测试；这次没改 pearnly_core，上一提交改的三个文件合入前仍要在测试机跑候选检查）：
  - 部门规则（`kind=department`）点「允许」后失败：成立。上一提交把规则来源改成认出的单据编号，对所有类别都认单据，部门没带单据（或带的票面单号对上几张）时出卡片前放行、点了允许才抛 `UnknownDocument`。改法：来源只在按单据算对象的类别记认出的那张单，部门照以前原样记参数（`_evidence_document`）；补用例部门不带单据、带对不上的单号都能存，且不读、不找单据。
  - 出错重试只在 rescheduled 早于 span 结束时清半句：成立（按代码）。改法：span 结束（出错）时补「 …」收口的预览记进 `cutPreviews`，随后的 rescheduled 一起清空；本轮结束、会话终止时照旧留「 …」。两种先后都有用例。服务端（托管智能体事件流）实际先后没在测试机核实，现在哪种先到都一样。
  - 被拒的一步只去问号、泰文日文读着仍在问：成立，中文「要…吗」同样。改法：`askStem` 去掉问号、中文「要…吗」、泰文句末 ไหม / มั้ย / หรือไม่ / หรือเปล่า、日文句末「か」；用例从各产品工具声明与服务端词条表取全部问话（四种语言 56 句）逐句核对去掉后不再是问句。没加新词条。
  - 旧对话重开仍写「记下了公司选择」：成立。拒绝的旧结果是一句没翻译的英文原文，`stepDenied` 同时认这句原文；第 8 轮那段旧对话重开也显示「已拒绝」。
  - 票面单号没限定到规则所属公司：成立。改法：`storage.by_invoice_no` 多收 `party_tax`（规则所属公司的 13 位税号），只要这家是买方或卖方的单（税号带横线也认，最多看 50 张），候选也只列这家的；认不出的原因写明「不是这家公司的单据编号」。补了函数级用例（只编译这一个函数、列表查询换成替身）和工具用例（别家客户的同号单不算、只属于别家的单号认不出）。文档里说按 `client` 筛：单据的 `client` 不是公司主档（单据归哪家按推送账套、买卖方税号认，`companies.pending.company_of`），所以按税号筛。
  - 公司组头拖动中菜单键仍开「···」：成立。`GroupHeader` 的键处理开头同对话行：拖动中只交给把手。Alt+方向键原来就由 `altMove` 在拖动中让开。
  - 在收着的组头上取消拖动时滑回去的是空白的底：成立。改法：让开的两条规则只在拖动中生效（底：`.cc-drag-overlay:not([data-dropping])`；字：`:not([data-dropping=""])`，放进收着的组时字仍隐着、底随落位淡掉）。本机无界面 Chrome 实测：收起 Gamma，键盘拿起 Delta one、↑ 停在 Gamma 组头（底 0.15、字 0），Esc 后 30ms 字 0.41、90ms 0.97、150ms 1，浮层带字滑回；空格放进 Gamma：字一直 0、底 0.12 → 0。
  - 结果写明没存上的仍写「记下了…」只加「失败」：成立。改法：要点「允许」的工具结果是 `not_saved` / `failed` 时，行写问话、行尾「失败」，摘要单算一类写「把这个选择记到公司 · 失败」（沿用步骤行的「失败」词条）；不要允许的工具照做完的说法加失败数。
  - 段行两个 Marquee 共用整行做悬停范围：成立（窄栏、长泰文时）。段名、状态各自标 `data-marquee-host`。
  - 账套名改成 13px 深色：成立，属于没登记的变化。改回原来子行的 12px 淡色（`.cc-month-set-name`），设计文档 4.4 写明。实测 1024 宽、右侧栏 280：两行各 220px，「testcowork 10月15日 21:55 回读」放得下，长账套名只在名字那行渐隐。
  - 测试：`test_cowork_companies.py`（部门规则、按公司筛单号）、`test_cc_ccs.py`（没存上的写法、问话去疑问、旧拒绝原文）、`test_assistant_ui.py`（span 先结束再 rescheduled、组头拖动中键、让开只在拖动中）、`test_cc_month_ledger.py`（账套名样式、段行各自滚动）；旧断言只按新写法改，没删。
- 10-16 测试窗口在 lab testerp（站点没配 CC，site_config 没有智能体密钥）发现：新对话发第一条，`create` 回 417，界面不报错——用户气泡和「思考中」约 100ms 就没了，回到空白新对话，侧栏没有这条，打的字也没了，控制台只有未捕获的「本站点尚未设置 CC。」。根因：组件库按下发送先清空输入框，再调 `onNew` 而不等它、不接错（`ExternalStoreThreadRuntimeCore.append` 由输入框 `handleSend` 直接调用）；`onNew` 建会话或发出失败只是抛出（已有对话发出被拒同样：乐观气泡撤回、字没了、未捕获）。改法：`lib/managed-agents/send-failure.ts` 的 `attemptSend` 包住建会话与发出，从不抛出；没成功就把原因（服务端原文，没有原文用现有词条 `somethingWrong`）记在这段对话上，没发出去的字与附件放回输入框（传进会话的原样放回、编号不变；没传上的放回原文件重新传；等的时候又打了字，原来那条在前），临时气泡与「思考中」照旧撤掉；已发出、只是之后的打断没成功不放回；没发出去不取标题。原因显示在输入框上方（`thread.tsx` 的 `SendFailure`，同对话里出错那一行的样子 `ERROR_BOX`，`role="alert"`，两行截断），只在这段对话、再发送时清掉；放回的图片按会话位置取本页预览（`attachment.tsx`）。打开新对话页就提示没配 CC：`shell.describe` 与现有接口都没有「CC 是否已配置」的信号，按任务书不新加接口，没做。测试：`test_cc_run_indicator.py` 新增 `test_a_failed_send_says_why_and_keeps_what_was_typed`（用组件库真实的输入框与对话运行时：417 原文、断网通用句、字与附件放回、重试清掉、已有对话被拒、打断失败不放回、没传上的附件重新传、没有未捕获的错误）与 `test_send_failure_wiring`。实测（无界面 Chrome、构建出的整页、假接口 create / send 回 417）：改前字没了、未捕获错误一条；改后原因出现在输入框上方、字和图片（缩略图在）都在、无异常，配好后再发原因清掉、照常发出。独立小分支 `pearnly/cc-send-error`（基于 `10920e5b`），等第 9 轮做完一起合入。
- 10-17 侧栏对话行只在本组排序（用户 10-17：「侧栏对话行改为仅可以在当前公司环境拖拽排序，不可以跨公司，把之前的对话可以移到其它公司的逻辑删掉」），`pearnly/cc-shell-rebuild` 本地提交、未推送：
  - 口径：对话行（按公司分组时）只在它自己那一组（某家公司，或「最近」）里排先后，或拖进「已置顶」= 置顶、按落点排进置顶顺序；不能落到别的公司组、别的组头。键盘拖动、Alt+↑/↓ 只在本组里挪；按月份 / 不分组、搜索时对话行不能拖。多选拖进置顶 = 整批置顶；选中的全在本组时整块排到落点。设计文档 3.17 与侧栏表「拖动」行已改。
  - 删掉的入口：行「···」的「移到 ▸」、多选的「移到… (N)」、电脑标题栏「···」的「移到」、没挂公司的已有对话下巴里的「公司 ▾」（新对话开之前照样选公司；手机对话页「…」的月份 / 分店子菜单与问候下面的公司条保留）；`MoveToMenu`、`BulkMoveMenu`、`UnfiledChin` 与跨组拖动的整套逻辑；词条 `co.moveTo`、`sb.moveToN`。
  - 服务端：`sessions.move` 只改月份、分店，请求里的公司与对话现在的不同一律拒绝（"A conversation cannot be moved to another company."，四种语言）；删公司走 `projects.release`，不经 `move`，不动。
  - 组里手排：存在个人排列 order 里，每一组一项 `group:<公司编号>`（最近 `group:`），各组后写的为准；存对话编号的先后与存下时这组最新的那个时间（服务端给的时间）；新对话排最上面，分页后才取到的旧对话接在后面；选排序方式清掉；服务端分组列表第一页一并给手排列表里的对话（`sessions._ordered`，回 `ordered`），组里各组合起来按 16 KB 截（最早存下的先去掉）。
  - 提交：`f11cdf5d`（实现）；审查后的修正见下一次提交（拿起时不再临时插已置顶把整列推下去、没有落点时松手滑回原位、按组同步、第一页带回手排的对话、`at` 不用本机时钟、落点不因条数上限丢、键盘拖动滚动改 `auto`）。
  - 还缺：真实侧栏没在浏览器或测试机上点过（只有复刻页的无界面 Chrome 实测与源码、纯函数、服务端桩测试）；手机长按拖进置顶、多选整批拖动没有浏览器实测；服务端只跑了 frappe 桩测试。两处行为待用户定：手排过的组里收到提醒的对话不浮到组顶；取消置顶的旧对话回到手排列表后面（组里手排超过 5 条时要点「显示更多」才看得到）。
- 10-17 第 10 轮文案（用户 10-17 同意：卡片「照 Claude」时同意点完后不再写「已允许 / 已拒绝」、改写所选那一项；文案闸门拦下的外壳新文案「照你的建议改」）。
  - 卡片：删除 `allowed`「已允许」、`denied`「已拒绝」，点完那一行改写所选那一项（复用 `allow`「允许」/ `deny`「拒绝」），没做成时「允许 · 失败」；新增 `needsInput`「需要您的输入」（en Needs your input / th ต้องการข้อมูลจากคุณ）、手机点完的卡片标签 `question`「问题」。
  - 公司页与月结台账（`frontend/assistant/src/i18n.ts`）：`chatWaiting`「等你处理」→「需要您的输入」（en Waiting for you → Needs your input，th รอคุณดำเนินการ → ต้องการข้อมูลจากคุณ）；`noChats`「还没有对话。」→「暂无对话」（en No chats yet. → No chats，th ยังไม่มีแชท → ไม่มีแชท）；`choice_needed`「要人选」→「待选择」；`not_pushed`「还没推送」→「未推送」（en Not pushed yet → Not pushed）；`readback_needed`「还没回读」→「未回读」；`partial`「单据较多，只统计了一部分」→「仅统计部分单据」（en Only some documents counted，th นับเพียงบางส่วน）；`severity.info` 英文 FYI → Info。
  - Cowork 术语表（`apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv`，只改中文）：「把这个选择记到公司？」→「保存为公司选择？」（步骤行随之写「保存为公司选择」）；「删除这条公司选择？」→「删除公司选择？」；「这个值不在本月账套的主档里」→「该值不在本月账套主档中」；「这家公司（或这个分店）没有覆盖本月的账套」→「该公司或分店没有覆盖本月的账套」；「无法保存这条选择」→「无法保存此选择」。
  - `Cowork Company Choice` 的说明收短为 "Push choices set for a client company or one of its branches"，与字段名 "Conversation" 一起进术语表（按客户公司或分店设的推送选择 / 对话）。
