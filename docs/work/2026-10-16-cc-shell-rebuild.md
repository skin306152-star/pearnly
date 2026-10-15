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
  - 三个变体：`lib/variant.ts` 在 `main.tsx` 首帧前写 `<html data-cc-variant>`（桌面窗口按 `isDesktopApp()`，手机按 `hooks/use-mobile.ts` 同一条 768 分界，跨分界时跟手机外壳一起换）；桌面窗口侧栏浮成卡片（离边 8、卡片阴影）、行 30、组上间距 14；手机行与组标题带 44、组箭头与组操作常显、正文 16/24。
  - 共用样式：导航行（行高按变体、14/20 次要色、20 图标 / 28 槽、悬停不改字色）、组标题 13/16（鼠标在侧栏里变次要色，120ms）、搜索框 32（白 50% / 白 5%、0.5px 内环 + 轻投影、快捷键提示槽 `.cc-search-hint`）、行内按钮 `.cc-row-btn` 24 / 圆角 6、菜单按 3.6（含 `.cc-menu-value`、不可用 40%）、按钮 / 输入框焦点、弹窗 pop 阴影、提示框色、回复正文 14/20（手机 16/24，字体仍按设置）、Markdown 段落与列表去掉 leading-relaxed（列表上外边距 1.5em≈21px、缩进 28 + 7）、行内代码 0.9em / 圆角 5 / 代码色、链接色、用户气泡叠色 1 / 圆角 10 / 8·12。会话列表行：悬停与选中同一底色、选中靠主色字，行高按变体。
  - 菜单翻面（F11d）：`components/ui/menu.tsx` 的纯函数 `pickSide` + 包装 `Content` 在绘制前按 `scrollHeight`（`--cc-menu-max` 封顶）与触发按钮上下空间选方向，换子菜单页时重选；`.cc-menu` 限高改 `min(--cc-menu-max, 可用高度)`；三处 `max-h-80` 改 `[--cc-menu-max:20rem]`。
  - 底色同步：`www/assistant.html`（theme-color、启动底色）、`assistant/api.py` 离线页、`build-ios-splash.py` THEMES、`gui_tomo.py` APP_BG、`vite.config.ts` manifest → #fcfcfb / #151515。**iOS 启动图 PNG 未重新生成**（本机没有 Pillow），文件仍是旧底色，要在有 Pillow 的环境跑 `python3 scripts/build-ios-splash.py`。
  - 证据（本工作树）：`npm --prefix frontend/assistant run build` 通过；`tests/test_cc_ccs.py`（16，含 `--code-*` 浅深逐项、变体、底色副本）、`test_assistant_ui.py`（90）、`test_cc_mobile_overlays.py`（25，含 `pickSide` 纯函数）、`test_cc_run_indicator.py`、`test_cc_mobile_content.py`、`test_assistant.py`、`test_cc_desktop_name.py` 全过；`check-frontend-tokens.py`、`check-frontend-viewport-units.py`、`check-architecture.py` 通过。CC 桌面版 pytest 本机缺 pytest / PySide6 没跑（`gui_tomo.py` 只改一个常量，已 py_compile）。浅 / 深 × 网页 / 桌面窗口 / 手机的目测、测试机点验、`cand-run.sh`（动了 pearnly_core）都还没做。
