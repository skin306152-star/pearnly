# CC 共用拖动换成自写指针排序（第 12 轮，2026-10-17 起）

- 分支 `pearnly/cc-drag-engine`，工作树 `.claude/worktrees/cc-drag-engine`，从 `pearnly/cc-shell-rebuild` `d70f7f00` 开出（本文所有「现行为」都指这个提交）。
- 范围：只换 `frontend/assistant/src/components/ui/drag.tsx` 的内部实现（需要时连带 `lib/layers.ts`、`globals.css` 的拖动规则），所有拖动处继续走这一个共用模块。`package.json` / `package-lock.json` 这一轮不动，`@dnd-kit/*` 依赖下一轮单独删。
- 本文先写背景与验收基准（「对照清单」），实现、本地检查、复刻页实测、测试机 / A14 复测分别在后面各节登记，缺哪层写哪层。

## 背景

### 用户要求

- 10-15：CC 里所有拖动（侧栏公司组头、已置顶、组里的对话行，右侧会话栏卡片）走同一个共用模块，效果照 Claude 桌面版右侧栏拖卡片的录屏（IMG_5192）：悬停出把手、按住浮起跟手、其他项拖动中带过渡滑开让位、松手立刻平滑落位、只在本列竖向拖；「全部参考视频，非常丝滑」。
- 10-16：「所有拖拽的地方要改统一规则和效果」「对话的拖动效果怎么没和其它拖动效果统一呢」。
- 10-17：侧栏对话只在本组内排序或拖进已置顶，不能跨公司（「移到」已删）。

### 第 10、11 轮在 dnd-kit 里做过的优化与结果

行与组头 memo、让位位移不经 React 直接写节点（`applyShift`）、鼠标拖动用挡板代替根节点标记、播报区与浮层挂到 body 下的常驻层、`sensorOptions` 记住、`FlowAreaContext` 只在侧栏关尺寸观察、整列滑动拿起后一帧才开始盯、`DragList` 的 items 按内容记住等（详见 `docs/work/2026-10-16-cc-shell-rebuild.md` 第 10 轮、第 10 轮 B 段、第 11 轮 B14、第 11 轮重测 B 段）。本机无界面 Chrome 6 倍节流下拿起、落定降了三到四成，但 A14 真机几乎没改善。

### A14 trace 对回源码的结论

分析脚本在本机 scratchpad `a14trace/`（不进仓库）。

- 拿起 122–168ms 主要是 dnd-kit 固有开销：
  1. 拿起时 `useRect` 的布局副作用同步引出第二遍渲染；
  2. `useDroppableMeasuring` 给每个可放区 `new Rect`，每个都走一遍 `getScrollableAncestors`（逐级 `getComputedStyle`），约 20–25ms；
  3. 所有 `useSortable` 都订阅拖动上下文，拿起、落定时全部重画；
  4. `DragOverlay` 挂载时强制测量；
  5. body 上的 `MutationObserver`：DOM 一有增删就同步量框。
- 落定 48–60ms：同样是全部排序项重画。
- 第 11 轮记录里「1 家公司 5 项时拿起 ≤33ms，项数是主因」，与上面一致：开销随同时挂着的排序项数线性增长，在 dnd-kit 里压不下去。

### 决定（开发窗口，按「不要拆东墙补西墙、不做过渡版」）

- 在 `drag.tsx` 里用自写的指针排序整体替换 dnd-kit。所有拖动处继续走同一个共用模块；对外接口（`DragArea`、`DragList`、`useSortableItem`、`useDropZone`、`altMove`、`chain`、`spotIn` 及类型）、调用方用法（`thread-list.tsx`、`projects.tsx`、`sidebar-group.tsx`、`company-cards.tsx`、`session-panel.tsx`）和读屏播报都逐句不变。
- 预期：A14 拿起约 30–45ms，落定约 15–25ms；拖动中每帧只写节点位移，不经过 React。
- 第 10、11 轮修过的边角都要保留，验收以下面的对照清单为准。

## 对照清单

验收基准：新引擎上线前，下列每一条要么行为一致，要么在本文写明有意改变的理由并经开发窗口确认。

**写法约定**

- 编号：P 拖动处，I 接口，A 静态 ARIA，S 播报，M 鼠标，T 触屏，K 键盘，E Esc 与其它层，B 多选，V 视觉与动效，X 牵连模块，Z 已知问题，R 测试。
- 「出处」中的 `drag.tsx:N` 是 `d70f7f00` 上 `frontend/assistant/src/components/ui/drag.tsx` 的行号，其余文件路径都相对 `frontend/assistant/src/`。测试写成 `类名.方法名`，`test_assistant_ui.py` 的类直接写类名，`test_cc_session_panel.py` 的前面加 `panel:`。
- 标【库】的是 dnd-kit 6.3.1 / sortable 10.0.0 自身的行为（`node_modules/@dnd-kit/core/dist/core.esm.js` 等）。换掉 dnd-kit 后，这些要由自写引擎自己做到。

### 一、拖动处总表（P）

- **P-01 会话栏卡片**：拖动项是整张卡 `<section>`（`sortable.node`），把手是卡头最右边的 6 点按钮 `.cc-panel-grip`（`sortable.handle`，`aria-label`/`title` 为「调整顺序 / Reorder / จัดลำดับ」）。`kind: "card"`，`list: "cards"`，`label` 是卡片标题。不禁用，也不传 `ownFocus`/`skip`。只在会话栏里（`PanelControlContext`）才有把手和拖动；公司页以外的独立卡片没有把手。出处：`components/company-cards.tsx:134-139, 167, 186-200`；`panel:PanelLogicTests.test_cards_toggle_summaries_and_grip_slot`。
- **P-02 侧栏公司组头**：拖动项是整组 `.cc-group-section`（组头加展开着的这组各行），同一节点也是这组的整块落点（`useDropZone`）。把手是组头开关按钮 `.cc-group-toggle`（`GroupHeader` 把 `{...handle}` 展开在这个按钮上）。`kind: "company"`，`list: "companies"`，`group: 公司编号`，`label` 是公司名。`ownFocus: true`，搜索时 `disabled`。出处：`components/assistant-ui/thread-list.tsx:729-780`、`components/assistant-ui/sidebar-group.tsx:64-90`、`components/assistant-ui/projects.tsx:117-165`；`SidebarDragTests.test_sidebar_drag_wiring`。
- **P-03 已置顶行**：`kind: "pin"`，`list: "pins"`，id 为 `pin:<本页编号>`，`label` 是对话标题（空时用「新对话 / New chat / แชทใหม่」）。`ownFocus: true`，`skip: ROW_SKIP`，搜索时 `disabled`。三种分组方式（按公司 / 按月份 / 不分组）下都能拖。出处：`thread-list.tsx:992-1001, 466-475`；`SidebarDragTests.test_sidebar_drag_wiring`、`Round6Tests.test_search_locks_every_sidebar_drag`。
- **P-04 组里的对话行（公司组 / 最近）**：只在按公司分组时能拖。`kind: "row"`，`list: rows:<公司编号>`（「最近」是 `rows:`），`group` 是公司编号（最近为 ""），`session` 是服务端编号，id 为 `row:<本页编号>`。`ownFocus: true`，`skip: ROW_SKIP`；搜索中或没存到服务端（没有 `remoteId`）时 `disabled`。只能在本组里排先后，或拖进已置顶。出处：`thread-list.tsx:1004-1019`、`lib/managed-agents/sidebar-drag.ts`；`SidebarDragTests.test_what_can_be_dropped_where`。
- **P-05 行的结构**：拖动项（node）是整行 `ThreadListItemPrimitive.Root`；把手（ref、`aria-*`、`data-drag-handle`）是行里打开对话的按钮 `ThreadListItemPrimitive.Trigger`。但鼠标按下、手指按下、`keyup`、`keydown` 的拖动处理挂在整行 Root 上（`chain(press.onTouchStart, dragTouch)`、`onMouseDown`、`onKeyUp`、`onKeyDown`），改名中都不接。从行尾「···」、菜单、输入框按下不拿起（`ROW_SKIP = '[data-slot="aui_thread-list-item-more"], [role="menu"], input'`）。出处：`thread-list.tsx:990, 1053-1100`；`SidebarDragTests.test_sidebar_drag_wiring`。
- **P-06 不能拖的情形**：搜索中，侧栏全部锁住（行、置顶、公司组）；按月份或不分组时，组里的行不能拖（不在 `RowScopeContext` 里，渲染成普通行），置顶照样能拖；没存到服务端的行不能拖；ERP / DMS 侧栏（`DayGroups`）没有拖动区。不能拖时没有 `data-drag-handle`，鼠标悬停不出抓手。出处：`thread-list.tsx:269, 455-475, 976-983, 1011`；`Round6Tests.test_search_locks_every_sidebar_drag`。
- **P-07 拖动区划分**：
  - 侧栏一个 `DragArea`（`CompanyGroups`），给了 `accepts`、`onDrop`、`onOver`、`onActive`、`onReturn`、`onPointer`、`flow={FLOW}`、`texts={sb.drag}`。
  - 侧栏里的列：`DragList id="pins"`（置顶）、`DragList id="companies"`（公司组）、每组 `DragList id={rows:<公司>}`、`DragList id="rows:"`（最近）。
  - 侧栏里的整块落点：已置顶 `zone:pins`（kind `pins`）、各公司组 `zone:rows:<公司>`、最近 `zone:rows:`；收着时 `shut = !open`。
  - 会话栏另一个 `DragArea`：`accepts={sameColumn}`，只有 `onDrop`、`texts={cardTexts()}`，没有 `flow`；一列 `DragList id="cards"`，items 是看得见的卡。
  - 出处：`thread-list.tsx:615-677, 706-727`、`components/session-panel.tsx:59-131`；`SidebarDragTests.test_sidebar_drag_wiring`、`panel:SortableSourceTests.test_the_panel_uses_the_shared_drag`。
- **P-08 能放到哪（accepts）**：
  - 对话行能放到：本组的行（多选而选中的不全在本组时不能）、本组整块（只算「回到本组」）、已置顶的行、已置顶整块。
  - 对话行不能放到：别的公司组的行或整块、公司组头、不属于自己的「最近」。
  - 置顶、公司组只能放到同一列的项上，不认整块。
  - 卡片只能放到同为 `cards` 列的卡上。
  - 拖动项自己总在候选里（`entry.id === activeId` 时只要有 `list` 就算）。`useSortable` 的 `disabled` 同时作用于拖和放，所以搜索中或没存到服务端的行也不是落点。
  - 出处：`lib/managed-agents/sidebar-drag.ts:sidebarAccepts`、`drag.tsx:1010-1015`、`session-panel.tsx:76`；`SidebarDragTests.test_what_can_be_dropped_where`。
- **P-09 放下以后（onDrop）各分支**：
  - 置顶：`moveTo(pinIds)` 后存置顶顺序（`setPinnedOrder`）。
  - 公司组：存公司顺序（`setCompanyOrder`）。
  - 对话行落在已置顶（行或整块）：执行 `pinDropped`。按落点算出置顶的新先后（`pinDrop`，多选按 `placeBlock` 整块排入），先存顺序，再从组里手排中去掉（`forgetInGroups`）；如果是临时补出来的已置顶又收着，就把它展开；然后逐条置顶（`runInOrder`，不并发，失败时出错行写原文）；服务端回来之前先按已置顶摆着（`pinning`）；多选完成后清空选中。
  - 对话行落在本组的行：执行 `groupDropped`，按 `moveTo` / `placeBlock` 存这一组的手排先后（`setGroupOrder`，`at` 用 `groupStamp` 取这组最新的时间），多选完成后清空选中。
  - 卡片：执行 `dropOrder`，只在看得见的卡片里挪，然后 `setCardOrder`（存在本机，按用户分开）。
  - 落回原位、没有落点：不调 `onDrop`。
  - 出处：`thread-list.tsx:526-570`、`session-panel.tsx:63-71, 117-120`；`SidebarDragTests.test_sidebar_drag_wiring`、`panel:PanelLogicTests.test_drag_drop_order_and_announcements`。
- **P-10 Alt+↑/↓ 挪一格**：公司组头、置顶行、组里的行、卡片（卡头开关和把手都能用）都支持。只在看得见的那一列里挪，挪完存顺序，与拖动放下走同一套存法（组里的行还要记 `[id]` 作为挪过的）。到头了不挪、不念。出处：`thread-list.tsx:452-458, 467-470, 640, 744-752, 997-1018`、`company-cards.tsx:158-163`、`session-panel.tsx:101-110`；`Round5SmallFixesTests.test_alt_arrow_moves_are_announced_like_dragging`。

### 二、对外接口（调用方一个字不改）（I）

- **I-01 `DragArea`**：参数为 `accepts(active, target)`、`onDrop(active, target)`（落位播完才调）、`onOver?(active, over|null, below)`（只在指针拖动时调，经过的是它自己时不调）、`onActive?(active|null)`（拿起 / 结束）、`onReturn?(active)`（跨列以后取消时请调用方挪回原列）、`onPointer?(active, y)`（只在指针拖动时，从拿起到结束之间，取消后不再调）、`flow?: string`（整列滑动的选择器）、`texts: DragTexts`。出处：`drag.tsx:837-857, 957`。
- **I-02 `DragList`**：参数为 `id`、`items: string[]`、`children`；items 按内容记住，内容不变就当同一份。出处：`drag.tsx:1406-1416`；`Round11DragFramesTests.test_whole_list_redraws_skip_unchanged_groups`。
- **I-03 `useSortableItem(data, { disabled, ownFocus, skip })`**：返回 `{ moved, node, handle, dragging }`，四样都按内容记住。
  - `node` 包含 `ref`、`data-drag-id`、`data-drag-list`（此刻真正所在的那一列；拖进已置顶后重挂的那一行是 `pins`）、`data-drag-source="gap"`（自己被拖时）。不带 `style`，位移由引擎直接写到节点上。
  - `handle` 包含 `ref`、ARIA 属性（见 A 组）、`data-drag-handle`（能拖时）、`onMouseDown`、`onTouchStart`、`onKeyDown`、`onKeyUp`。
  - `moved(spot)` 用来念 Alt 挪动后的位置。
  - 出处：`drag.tsx:1712-1762, 1656-1693`；`Round11DragFramesTests.test_rows_keep_the_same_props_when_sorting_starts`、`Round10BTests.test_lifting_does_not_redraw_the_whole_list`。
- **I-04 `useDropZone(data, shut)`**：返回 `{ ref, "data-drop-target" }`。指针此刻指着这块、且它收着时，`data-drop-target` 为 `""`，否则为 undefined。出处：`drag.tsx:1808-1814`。
- **I-05 其余导出**：
  - `altMove(event, move)`：是 Alt+↑/↓ 时返回 true；拖动中、带 Meta 或 Ctrl 时返回 false。
  - `chain(...handlers)`。
  - `spotIn(list, id)`：返回 `{at, total}`；没挪（新顺序为 null）或找不到时返回 null。
  - 类型 `DragData`、`DragTexts`、`DragHandle`、`MoveSpot`。
  - 出处：`drag.tsx:117-150, 869-875, 1818-1835`。
- **I-06 测试直接调用的导出**：`dragAnnouncements`、`narrowPool`、`keyboardAim`、`keyboardSlot`、`keyboardCollision`、`clampToList`、`clampIntoView`、`inView`、`layoutRect`、`flowLayout`、`landOverlay`、`settleBack`、`slideOverlay`、`dropAnimation`、`sortShift`、`ReleaseFirst`、`HoldTouchSensor`、`LandingMouseSensor`、`LandingKeyboardSensor`、`TOUCH_DRAG_SLOP`、`blockReleaseClick`、`cloneOf`、`dragLayer`、`wheelUnder`、`keepFocusOn`、`rememberHandle`、`applyShift`、`shiftStyle`、`OVERLAY_Z`。
  - 纯函数的语义按 R 组列的用例保持。
  - 依附 dnd-kit 形状的几样（`dataOf` 和 `listOf` 读 `data.current.sortable`；`dropAnimation` 的参数；传感器构造参数）可以随引擎改形状，但用例的期望值（结果、播报、调用先后）不变。
- **I-07 `lib/layers.ts` 给别处用的**：`isDragging`、`isDragEscape`、`isDragMoving`、`markDragMoved`、`setDragging(on, cursor)`、`releaseDrag("drop"|"keep")`、`setDragShield`、`dragShieldShown`，语义不变。出处：`lib/layers.ts:24-119`。

### 三、静态 ARIA（A）

- **A-01 卡片把手（不传 `ownFocus`）**：在 `<button type="button">` 上，依次是 `role="button" tabindex="0" aria-disabled="false" aria-roledescription="sortable" aria-describedby="DndDescribedBy-<n>" data-drag-handle=""`。自己被拖时多一个 `aria-pressed="true"`【库：`isDragging && role === "button"`】。实测 SSR 输出：`<button type="button" role="button" tabindex="0" aria-disabled="false" aria-roledescription="sortable" aria-describedby="DndDescribedBy-0" data-drag-handle="" class="cc-panel-btn cc-panel-grip" aria-label="Reorder" title="Reorder">`。出处：`drag.tsx:1670-1676`；`panel:PanelLogicTests.test_cards_toggle_summaries_and_grip_slot`（正则锁定属性先后）。
- **A-02 侧栏把手（行开关、置顶行开关、公司组头开关，`ownFocus: true`）**：只加 `aria-roledescription="sortable"`、`aria-describedby="DndDescribedBy-<n>"`，能拖时再加 `data-drag-handle=""`。不加 `role`、`tabIndex`、`aria-pressed`、`aria-disabled`，焦点仍由侧栏键盘（`data-roving`）管。实测：`<button type="button" aria-roledescription="sortable" aria-describedby="DndDescribedBy-0" data-drag-handle="">`。出处：`drag.tsx:1648-1650, 1671-1676`。
- **A-03 不能拖时**：
  - `ownFocus` 的把手照样带 `aria-roledescription="sortable"` 和 `aria-describedby`，只是没有 `data-drag-handle`，也没有事件。实测 `<button type="button" aria-roledescription="sortable" aria-describedby="DndDescribedBy-1">`。
  - 不带 `ownFocus` 的把手变成 `aria-disabled="true"`。
  - 出处：`drag.tsx:1670-1692`（本轮 SSR 实测）。
- **A-04 说明文字**：每个 `DragArea` 一份，`<div id="DndDescribedBy-<n>" style="display:none">{texts.instructions}</div>`，挂在 `document.body` 下（`accessibility.container`）。`n` 按页面里 `DragArea` 的创建先后全局递增，客户端挂上之后的第一个副作用里才出现（SSR 和首帧没有）。拖动区里每个把手的 `aria-describedby` 都指向它。【库：`Accessibility` / `HiddenText`】出处：`drag.tsx:1222-1229`。
- **A-05 拖动库的播报区**：每个 `DragArea` 一份，`<div id="DndLiveRegion-<n>" role="status" aria-live="assertive" aria-atomic="true">`，带行内看不见的样式（`position:fixed; top:0; left:0; width:1px; height:1px; margin:-1px; border:0; padding:0; overflow:hidden; clip:rect(0 0 0 0); clip-path:inset(100%); white-space:nowrap`）。挂在 `document.body` 下，常驻（不只在拖动时）。【库：`LiveRegion`】出处：`drag.tsx:1219-1229`；`Round11DragFramesTests.test_measuring_reads_the_layout_position_and_the_overlay_sits_in_its_own_layer`（锁定 `container` 为 body）。
- **A-06 Alt 挪动的播报区**：每个 `DragArea` 一份，`<div role="status" aria-live="assertive" aria-atomic="true" class="cc-move-live sr-only">`，通过 portal 挂到 body（没有 body 时就地渲染），不带 `data-slot`。出处：`drag.tsx:1343-1351`；`Round5SmallFixesTests.test_alt_arrow_moves_are_announced_like_dragging`、第 11 轮重测「body 下不用 data-slot」。
- **A-07 浮层**：外层 `.cc-drag-float` 带 `inert`、`aria-hidden`，读屏不念，也进不了 Tab。克隆时去掉这些属性：`id`、`data-card`、`data-row-id`、`data-drag-id`、`data-roving`、`data-drag-list`、`data-drag-handle`、`data-drag-source`、`data-drag-landing`、`data-drop-target`、`data-selected`、`data-marquee`、`aria-current`、`aria-describedby`、`aria-roledescription`。祖先空壳另外去掉 `role` 和 `aria-modal`。出处：`drag.tsx:1421-1465, 1512`；`SidebarDragTests.test_lifted_copy_keeps_the_ancestor_scoped_styles`。
- **A-08 名字（label）**：
  - 卡片用卡片标题。
  - 公司组用公司名。
  - 置顶行和组里的行用对话标题，空标题用「新对话 / New chat / แชทใหม่」。
  - 多选整批（自己被选中且选中多于 1 条）用 `sb.drag.items(n)`：「N 个对话」/「1 chat / N chats」/「N แชท」。
  - 整块落点的名字是「已置顶 / Pinned / ปักหมุดไว้」、「最近 / Recents / ล่าสุด」或公司名。现有的 `over` / `droppedIn` 文案不念它（参数是 `_group`）。
  - 出处：`thread-list.tsx:710-743, 993-1014`、`i18n.ts:13, 67, 111, 143, 165, 209, 241, 263, 730, 748, 766`。
- **A-09 播报文案（三种语言，逐字）**：
  - 侧栏（`sb.drag`）
    - 中文
      - instructions：「按空格键拿起，用上下方向键移动，再按空格键放下，按 Escape 键取消。」
      - pickedUp：`已拿起「${name}」。`
      - movedTo：`「${name}」移到第 ${at} 位，共 ${total} 项。`
      - droppedAt：`「${name}」放在第 ${at} 位，共 ${total} 项。`
      - cancelled：`已取消移动「${name}」。`
      - over：`「${name}」将置顶。`
      - droppedIn：`已置顶「${name}」。`
    - English
      - instructions："To pick up an item, press Space. Use the up and down arrow keys to move it, press Space again to drop it, or press Escape to cancel."
      - pickedUp：`Picked up ${name}.`
      - movedTo：`${name} moved to position ${at} of ${total}.`
      - droppedAt：`${name} dropped at position ${at} of ${total}.`
      - cancelled：`Moving ${name} was cancelled.`
      - over：`${name} will be pinned.`
      - droppedIn：`Pinned ${name}.`
    - ไทย
      - instructions：「กด Space เพื่อหยิบ ใช้ปุ่มลูกศรขึ้นลงเพื่อย้าย กด Space อีกครั้งเพื่อวาง หรือกด Escape เพื่อยกเลิก」
      - pickedUp：`หยิบ ${name} แล้ว`
      - movedTo：`ย้าย ${name} ไปตำแหน่งที่ ${at} จาก ${total}`
      - droppedAt：`วาง ${name} ที่ตำแหน่งที่ ${at} จาก ${total}`
      - cancelled：`ยกเลิกการย้าย ${name}`
      - over：`${name} จะถูกปักหมุด`
      - droppedIn：`ปักหมุด ${name} แล้ว`
  - 卡片（`co.card`）
    - 中文
      - instructions：「按空格键拿起卡片，用上下方向键移动，再按空格键放下，按 Escape 键取消。」
      - movedTo：`「${title}」移到第 ${at} 位，共 ${total} 张。`
      - droppedAt：`「${title}」放在第 ${at} 位，共 ${total} 张。`
      - pickedUp、cancelled 与侧栏同式。
    - English
      - instructions："To pick up a card, press Space. Use the up and down arrow keys to move it, press Space again to drop it, or press Escape to cancel."
      - 其余与侧栏同式。
    - ไทย
      - instructions：「กด Space เพื่อหยิบการ์ด ใช้ปุ่มลูกศรขึ้นลงเพื่อย้าย กด Space อีกครั้งเพื่อวาง หรือกด Escape เพื่อยกเลิก」
      - 其余与侧栏同式。
    - 卡片没有 over / droppedIn。
  - 出处：`i18n.ts:513-517, 589-593, 665-669, 721-766`；`SidebarDragTests.test_sidebar_drag_wiring`（三种语言的键齐全）、`panel:PanelLogicTests.test_drag_drop_order_and_announcements`。

### 四、读屏播报：每句念什么、什么时候念（S）

播报经 A-05 的播报区念出【库：`announce(value)` 遇到 null 或 undefined 不改播报区，保留上一句；同一句连续设两次时 React 状态不变，不会重念】。

- **S-01 拿起**：念 `pickedUp(label)`，与开始拖动同一次提交。鼠标是动够 3px 那一下，手机是按住满 250ms 那一下，键盘是按空格那一下。同时把「这次拖动挪开过没有」清零。出处：`drag.tsx:192-195`；`Round5SmallFixesTests.test_lifting_announces_picked_up_not_a_position`、`panel:PanelLogicTests.test_drag_drop_order_and_announcements`。
- **S-02 拿起后紧跟的那次「经过自己」**：不念，播报区留着「已拿起」。第 5 轮修过：公司组头空格拿起念成「移到第 3 位」。出处：`drag.tsx:161-162, 180`；`Round5SmallFixesTests.test_lifting_announces_picked_up_not_a_position`。
- **S-03 经过的项变了（排序）**：念 `movedTo(label, at, total)`。`at` 是经过那一项在它那一列 items 里的位置（从 1 起），`total` 是那一列 items 的条数，也就是 `DragList` 的 items：组里只数眼前显示的行（「显示更多」之外的不算，锁住的行照数），公司组只数眼前的公司，卡片只数看得见的卡。【库：`onDragOver` 在经过的编号变了的那次提交后的副作用里触发】出处：`drag.tsx:152-155, 184-187`。
- **S-04 挪开过以后又回到原位**：念 `movedTo(原位置)`。出处：`Round5SmallFixesTests.test_lifting_announces_picked_up_not_a_position`（`c to 3/4`）。
- **S-05 没有落点（经过的变成 null）**：拖动中不念，播报区保留上一句。出处：`drag.tsx:172`；`panel:PanelLogicTests.test_drag_drop_order_and_announcements`（`onDragOver` 经过 null 时为 undefined）。
- **S-06 对话行拖进已置顶**：经过置顶的某一行时，或者自己已经排进置顶、经过的是自己时，念 `over(label, …)`，即「「x」将置顶。」。之后在置顶里移动，句子不变，所以不重念（见上面的【库】）。出处：`drag.tsx:173-179`；`SidebarDragTests.test_drop_is_announced_by_what_really_happened`。
- **S-07 指着收着的已置顶整块**：同 S-06，念「将置顶」。出处：同上。
- **S-08 从已置顶拖回本组**：经过本组某一行，念 `movedTo`（本组的位置）。出处：`drag.tsx:184-187`。
- **S-09 放下（排序）**：念 `droppedAt(label, at, total)`，位置取落点那一项。时机是**落位动画播完、真正结束拖动的那一刻**，约在松手后 200ms：鼠标、手机、键盘一样；中途又按下就提前；最迟等 350ms（`landWait`）。出处：`drag.tsx:197, 1112-1122`、`ReleaseFirst`；`SidebarDragTests.test_release_starts_the_landing_and_ends_the_drag_after_it`。
- **S-10 放下进已置顶**：落点是置顶里的行、自己（已排进置顶），或收着的已置顶整块时，念 `droppedIn`，即「已置顶「x」。」。时机同 S-09。出处：`SidebarDragTests.test_drop_is_announced_by_what_really_happened`。
- **S-11 放下但没动**：落回原位（落点是自己、还在原来那一列）、落在本组整块上、没有落点，这三种都念 `cancelled`，即「已取消移动「x」。」。出处：`drag.tsx:182-183`；同上用例、`Round5SmallFixesTests.test_lifting_announces_picked_up_not_a_position`。
- **S-12 取消**：Esc、手机 touchcancel、转屏、切到后台都念 `cancelled`。时机是**滑回播完、真正取消的那一刻**（约 200ms 后）。跨列（拖进已置顶后再取消）时，要等原组重挂出来再起播滑回，挪回去最多等 600ms，滑回最多再等 350ms，然后才念。出处：`drag.tsx:198, 1126-1183`；`Round10DragTests.test_cancel_waits_for_the_slide_back_in_every_sensor`。
- **S-13 手机点按（拿起后没动，按下不到 500ms 就松手）**：先念「已拿起」（250ms 时），松手当场念「已取消移动」，同时补一次点击（会打开对话或折叠组）。出处：`drag.tsx:321-327`；`SidebarDragTests.test_touch_hold_lifts_at_250ms_and_moving_wins_over_the_long_press_menu`。
- **S-14 手机长按出菜单后原地松手**：结束拖动，经过的是自己，念 `cancelled`，菜单留着。出处：同上（`["start","end"]`）、`drag.tsx:182-183`。
- **S-15 键盘每按一下 ↑/↓**：瞄准换成下一项，经过的随之改变，念 `movedTo`。到头了瞄准不动，经过的没变，不念。出处：`drag.tsx:1039-1069`；`Round11RetestBDragTests.test_keyboard_moves_follow_the_list_order_whatever_the_heights`。
- **S-16 Alt+↑/↓（不在拖动中）**：挪完由 A-06 的播报区念 `movedTo(label, at, total)`，位置按挪完后看得见的那一列算。到头了没挪时不念。同一句连着出现两次时，末尾补一个空格，读屏才会再念。出处：`drag.tsx:1206-1215, 1756-1760`；`Round5SmallFixesTests.test_alt_arrow_moves_are_announced_like_dragging`。
- **S-17 说明文字**：焦点进入把手时，读屏经 `aria-describedby` 念 instructions；侧栏、卡片各自一套。出处：A-04。
- **S-18 两个拖动区各自独立**：侧栏、会话栏各有一份说明、一份拖动播报区、一份 Alt 播报区，互不干扰。出处：`drag.tsx:1222-1229, 1335`。

### 五、鼠标（M）

- **M-01 拿起条件**：在把手上按下（除右键以外的任何键【库：`MouseSensor` 只排除右键】），从按下处移动超过 3px（`MOUSE_START = { distance: 3 }`）才拿起；没动够就松手，照常算一次点击。拿起前移动多远都不会作废（没有 tolerance）。出处：`drag.tsx:204, 1195`；`panel:SortableSourceTests.test_shared_drag_rules`。
- **M-02 不拿起的按下**：
  - 行尾「···」、菜单、输入框（`skip`）。
  - 改名中的行。
  - Shift+按下对话行：行先 `preventDefault`（为了多选不选中文字），dnd-kit 见到已被处理的事件就不拿起【库：`nativeEvent.defaultPrevented` 不拿起】。
  - 别的拖动正在进行时【库：`activeRef` 不为空】。不过落位等待期间一按下，就先把上一次当场结束（见 M-14），所以接着的拿起是有效的。
  - 出处：`drag.tsx:1673, 1686`、`thread-list.tsx:1068-1073`、core `bindActivatorToSensorInstantiator`。
- **M-03 拿起前按 Esc**：作废这次按下（abort 加 cancel），之后松开照常算点击，不拦截。出处：`Round11RetestBDragTests.test_a_lifted_gesture_never_ends_in_a_click`（没拿起就 Esc）。
- **M-04 拿起那一刻**：
  - 原位留出同样大小的空档：`data-drag-source="gap"`，`opacity: 0`。
  - 浮层出现：克隆原来那一项，挂在 body 下常驻的 `.cc-drag-layer` 里，`position: fixed`，起始框等于原来那一项的框，z-index 55。
  - 调 `setDragging(true, true)`：鼠标才要挡板。
  - 调 `onActive(data)`。
  - 记下拿起时那一列（`lifted`），以及所在滚动区的 `scrollTop`。
  - 出处：`drag.tsx:1290-1308, 1492-1540`；`Round11DragFramesTests.test_measuring_reads_the_layout_position_and_the_overlay_sits_in_its_own_layer`。
- **M-05 挡板与光标**：
  - 拿起那一帧画完以后，才在拖动层里显示透明挡板 `.cc-drag-shield`：`position:fixed; inset:0; z-index:54; cursor:grabbing`。整页光标变成 grabbing，指针下面的行、组头、卡片、「···」都不出悬停效果。
  - 拿起就松手（还没盖上）时就不再盖。
  - 松手开始落位、取消开始滑回那一帧画完以后撤掉。
  - 跨列取消时先不撤（`releaseDrag("keep")`），起播滑回那一帧画完再撤。
  - 手机、键盘拖动都不盖。
  - 出处：`lib/layers.ts:37-110`、`drag.tsx:1545-1553`、`globals.css:2081`；`Round10BTests.test_the_drag_cursor_waits_for_the_lift_frame_to_paint`、`Round11DragFramesTests.test_drag_cursor_only_for_the_mouse_and_gone_at_release`、`Round11DragFramesTests.test_the_mouse_drag_shield_blocks_hover_and_passes_the_wheel_on`。
- **M-06 悬停光标（没在拖时）**：
  - 能拖的把手（`[data-drag-handle]`）显示 `cursor: grab`，里面的输入框是文字光标。
  - 侧栏和抽屉里能拖的把手显示 `cursor: pointer`，只改光标，照样能拖。
  - 会话栏卡片把手平时透明（`--cc-reveal-rest`），卡头悬停或聚焦时才出现；手机、触屏上一直显示。
  - 出处：`globals.css:1821-1829, 1854-1862`；`panel:SortableSourceTests.test_drag_lift_and_grip_slot_styles`。
- **M-07 跟手与范围**：
  - 浮层只能竖着动（x 恒为 0）。
  - 上下边不出「能放的各处与原位」合起来的上下边（`clampToList`，按版面位置量，不算让位位移和整列滑动的位移；视口坐标、每帧现量，所以滚动以后照样对）。不同拖动项的范围：
    - 卡片：第一张卡的顶边到最后一张卡的底边。
    - 公司组：第一家组的顶到最后一家组的底（含展开着的行）。
    - 置顶：置顶第一行到最后一行。
    - 对话行：已置顶整块、本组整块和本组各行合起来的上下边（两块之间的别的公司组也在范围里）。
  - 浮层是 fixed，不会撑大滚动区，列表滚动时它也不跟着走。
  - 出处：`drag.tsx:1071-1096, 462-490`；`panel:PanelLogicTests.test_lifted_card_stays_in_the_column_not_the_visible_box`、`panel:SortableSourceTests.test_shared_drag_rules`。
- **M-08 排序让位**：
  - 位于原位和落点之间的各项，整体移动「拖动项高度 + 两项间距」；空档移到落点：往后拖时底边对齐落点的底边，往前拖时顶边对齐落点的顶边【库：`verticalListSortingStrategy`】。
  - 位移用 `translate3d(x, y, 0px)`（取整）直接写到节点的行内样式，过渡是 `transform var(--cc-dur) var(--cc-ease)`，也就是 200ms 的 `cubic-bezier(0.32, 0.72, 0, 1)`。
  - 先写过渡、再写位移；不让位的项一次也不写；同样的值不写第二遍；结束时去掉留着的过渡。
  - 减少动态效果时没有过渡。
  - 出处：`drag.tsx:1727-1738, 1764-1804`；`Round11DragFramesTests.test_rows_keep_the_same_props_when_sorting_starts`、`Round11DragFramesTests.test_items_that_do_not_move_are_never_written`。
- **M-09 落点计算（指针）**：
  - 候选先按 `narrowPool` 收窄，再取离浮层框中心最近的中心（`closestCenter`）。
  - 有整块落点的拖动（侧栏对话行）：
    - 按指针竖向位置判断落在哪块里，只认那一列此刻的各项（它自己已排进去也算）。
    - 那一列没有别的项、或者收着时，认整块。
    - 不在任何一块里，就没有落点。
  - 没有整块的拖动（卡片、公司组、置顶）：认此刻所在那一列。
  - 出处：`drag.tsx:883-908, 1017-1034`；`SidebarDragTests.test_drag_pool_keeps_rows_in_their_group_or_the_pinned_section`。
- **M-10 对话行拖进已置顶（展开着）**：
  - 经过置顶的行或整块时，调用方把这一行挪进置顶（`pinGuest`），排在 `pinSpot` 给的位置：指在某行上半截时在它前面，下半截时在它后面；指在整块上时，上半截排最前，下半截排最后。
  - 原组的空档合上，置顶各行让开，这一行在置顶里重挂（拖动编号不变，浮层不重克隆）。
  - 指针回到本组时挪回去；到了没有落点的地方（别的公司组、两组之间）也挪回去，空档回原位。
  - 出处：`thread-list.tsx:572-588`、`lib/managed-agents/sidebar-drag.ts:pinSpot`、`lib/managed-agents/sidebar-groups.ts:pinGuest`；`SidebarDragTests.test_sidebar_drag_wiring`。
- **M-11 整列滑动（只在侧栏）**：
  - 版面一变（拖进置顶、挪回原组），侧栏的行、组头、「显示更多」凡是位置变了的，都带过渡从原位置滑到新位置。用 Web Animations 播 `translate`，时长和缓动同 M-08。
  - 换列重挂的同一行按 `data-drag-id` 认，接着滑。空档和正在落位的那一项不滑。滚动不算移动。正在滑的又变了，就从此刻看到的位置接着滑。起点不回拨。
  - 盯着期间，滚动区 `overflow-anchor: none`，停止时还原。
  - 拿起那一帧画完以后才开始盯。版面变了以后，到下一帧再重量各落点，几次变动合成一次。
  - 结束拖动那一次提交里停止，已经在滑的照样滑完。
  - 出处：`drag.tsx:506-569, 1353-1396`；`SidebarDragTests.test_the_column_slides_when_the_gap_changes_group`。
- **M-12 临时「已置顶」（侧栏没有置顶时）**：
  - 拿起那一刻不出。
  - 指针拖对话行到第一组组头的上半截以上时，临时补一个空的、展开的已置顶（点它不收）。
  - 出来以后还没排进去时，指针低于它的底边再往下超过它自己的高度，就收起。
  - 只在指针拖对话行、按公司分组时出现；键盘拖动不出。
  - 结束或取消时收起（`onActive(null)`、`onReturn`），取消以后不再因为最后停着的指针位置又出来。
  - 出处：`thread-list.tsx:337-338, 441-446, 589-614`、`drag.tsx:996-1008`；`Round11DragFramesTests.test_pointer_is_not_reported_after_the_drag_ends`。
- **M-13 收着的已置顶**：对话行指着它时，整块（组头）带 `data-drop-target`，用悬停色高亮；不把这一行排进去（`over.shut` 时 `onOver` 直接返回）。浮层这时加 `data-peek`：底色淡到 0.15，字和条数隐去，离开后恢复。出处：`drag.tsx:1496-1502, 1309-1321`、`globals.css:2063-2077`；`SidebarDragTests.test_the_float_turns_see_through_over_a_folded_group_head`、`Round8Tests.test_the_float_steps_aside_over_a_folded_group_head`。
- **M-14 松手（有落点、挪过）**：
  - 松手那一下只做一件事：从这一刻起播浮层落位（`landOverlay`，Web Animations 只动 transform，`fill: forwards`，`startTime = performance.now()`）。
  - 落点在松手时量好：落点那一项的版面位置，加上它此刻的让位位移（`sortShift`）。
  - 浮层带 `data-dropping=""`：放大和阴影随落位一起收回。
  - 结束拖动（`onDrop`、新顺序、整列重排）等落位播完再做（`ReleaseFirst`）。等待期间：
    - 冻住自动滚动（`freezeScroll`）。
    - `releaseDrag()`：撤挡板，之后的 Esc 不算拖动的 Esc。
    - 窗口捕获阶段的 `pointerdown`、`touchstart`、`keydown` 一到就当场结束。
    - 最迟 `landWait` = 动效时长 + 150ms 后自己结束（页面在后台时动画不走）。
  - 出处：`drag.tsx:622-655, 785-834, 1113-1122`；`SidebarDragTests.test_release_starts_the_landing_and_ends_the_drag_after_it`、`SidebarDragTests.test_escape_after_release_is_not_a_drag_escape`。
- **M-15 松手（被限位停在落点）**：拖过首尾、浮层正停在落点时，照样播一段位移为 0 的落位，让放大和阴影收回，等它播完再结束。出处：`Round10DragTests.test_release_at_the_target_and_cancel_land_before_the_redraw`。
- **M-16 松手（真的没挪过）**：浮层位移为 0、落点就是原位、不用收进组头时，不播，当场结束。出处：`drag.tsx:649`；同上用例。
- **M-17 落到收着的整块**：落点是收着的那一列整块、而且拖动项不在它里面时，浮层落到那块（组头）的框上，并且收起：`data-dropping="into"`，缩到 0.96、淡到 0。出处：`drag.tsx:1100-1111, 604-606`、`globals.css:2067`；`SidebarDragTests.test_drop_lands_first_then_hands_over`。
- **M-18 落点在滚动区可见范围外**（拖着时又滚走了）：落到可见范围的边上（上下边贴边），并且收起（`into`）；这一次不让落点那一项 `scrollIntoView`（`holdStill`），列表不跳，浮层也不飞出列表。出处：`drag.tsx:572-597, 1098-1111`；`SidebarDragTests.test_the_landing_stays_inside_the_list`、`SidebarDragTests.test_dragged_chat_rows_use_the_shared_gap_and_landing`。
- **M-19 落定那一次提交**：
  - `onDrop` 存新顺序；各项去掉让位位移，不带过渡，当场归位（新顺序已同时排好）。
  - 落点那一项在落位播完之前带 `data-drag-landing`（`opacity: 0`），不会出现一份在落点、一份在路上。
  - 浮层卸下；`onActive(null)`；`setDragging(false)`。
  - 先量位置（`holdStill`），再 `finish()`。
  - 出处：`drag.tsx:746-783, 1245-1276`、`globals.css:2076`；`Round11DragFramesTests.test_rows_keep_the_same_props_when_sorting_starts`（结束时当场归位）。
- **M-20 松手以后的点击**：拿起过的松手，紧跟着的那次 click 在 document 捕获阶段被拦下（只停止传播），拦 50ms【库：`detach` 后 50ms 才摘】：不打开对话、不折叠组、不切换多选。出处：`Round11RetestBDragTests.test_a_lifted_gesture_never_ends_in_a_click`（照常放下由拖动库拦）。
- **M-21 拖动中**：
  - 不选中文字：`selectionchange` 时清选区。
  - 不起系统拖放：`dragstart` 被 preventDefault。
  - 不出右键菜单：`contextmenu` 被 preventDefault。
  - 【库：`AbstractPointerSensor.attach/handleStart`】
- **M-22 Esc 取消（拖动项还在原来那一列）**：
  - 先滑回（`settleBack`）：把这一列各项的让位位移直接清掉，它们靠行内过渡滑回原位；浮层从此刻滑回原位（没挪过也播一段，让放大和阴影收回）。原位在可见范围外时落到边上并收起（`homeInto`）。
  - 滑回途中，排序项不再写让位位移（`settle`）。
  - 已经取消、等滑回的这段时间（`cancelling`），碰撞不再算，不交指针位置，不重量落点。
  - 滑回播完才真正取消（`ReleaseFirst`）。
  - 出处：`drag.tsx:676-696, 1126-1163`；`Round10DragTests.test_release_at_the_target_and_cancel_land_before_the_redraw`、`Round10DragTests.test_cancel_waits_for_the_slide_back_in_every_sensor`。
- **M-23 Esc 取消（对话行已排进已置顶）**：
  - Esc 那一下只起播浮层滑回：目标是拿起时的位置，减去之后滚过的距离；在可见范围外就落到边上。同时停住碰撞和自动滚动，`releaseDrag("keep")`。
  - 这一帧画完以后，调 `onReturn`，侧栏把它挪回原组，并收起临时已置顶。
  - 原组重挂出来那次提交里核对落点；与 Esc 时的目标差超过 1px（拖着时列表增减了行）时，从此刻改滑到真正的原位。
  - 最多等 600ms（`HOME_WAIT`）原组重挂，等不到就当场取消。
  - 滑回播完才取消。
  - 滑回途中同一段对话只有一份：落点那一项按拖动编号在此刻的页面里找，它在动画播完前不显示。
  - 出处：`drag.tsx:657-744, 1126-1190`、`thread-list.tsx:611-614`；`Round10BTests.test_escape_after_dragging_into_pins_slides_back_before_the_redraw`、`SidebarDragTests.test_drop_lands_first_then_hands_over`。
- **M-24 拿起后、按键还没松开就取消了**（Esc、resize、切到后台）：
  - 从取消一直拦到松开（`mouseup` / `pointerup`）后再过 50ms：click（只拦 `detail ≠ 0` 的，键盘激活的照常）、选区（焦点在输入框或可编辑区时不清）、`dragstart`。
  - 又按下（`pointerdown`）时当场撤。
  - 拦着的时候切到后台，当场撤。
  - 已经在后台时不挂。
  - 鼠标再动、左键已经松开（`buttons & 1 == 0`）时，当作已经松开。
  - 没拿起就取消的不拦。
  - 出处：`drag.tsx:363-431, 436-456`；`Round11RetestBDragTests.test_a_lifted_gesture_never_ends_in_a_click`、`Round11RetestBDragTests.test_release_block_never_outlives_a_release_the_page_cannot_see`。
- **M-25 窗口 resize 或切到后台（visibilitychange）**：同 Esc，先滑回再取消。出处：【库：`windowListeners` 的 resize、visibilitychange】、`drag.tsx:436-456`。
- **M-26 自动滚动**（鼠标、手机拖动都有，键盘没有）：
  - 指针进入滚动区上下 20% 的边缘区时，朝指针移动的方向滚。速度按进入边缘区的深度线性增加，最快每 5ms 滚 10px【库：`useAutoScroller` 默认，activator 为 Pointer，`useScrollIntent`】。
  - 只滚到列表首尾：浮层是 fixed，不撑大滚动区。
  - 不做「拿起后版面挪了就滚回去」的补偿（`layoutShiftCompensation: false`）。
  - 落位、滑回等待期间冻住。
  - 出处：`drag.tsx:952-954, 828-834`；第 4 轮记录（浮层不撑大滚动区）。
- **M-27 拖动中转滚轮**：挡板把滚轮交给指针下面最近的、能往这个方向滚的滚动区（按行滚时 ×16，按页滚时 × 窗口高）。滚动后浮层不动，落点和让位按新位置重算。出处：`drag.tsx:1555-1570`；`Round11DragFramesTests.test_the_mouse_drag_shield_blocks_hover_and_passes_the_wheel_on`。
- **M-28 拖动中滚动以后**：落点与让位按滚动后的位置算。限位按此刻的视口坐标现量。跨列取消时，原位要减去拿起后滚过的距离。出处：`drag.tsx:1083-1092, 1136-1139`、【库：滚动祖先的滚动差】。
- **M-29 焦点**：鼠标按下把手时，浏览器照常把焦点放到按钮上；拖动中不另外移焦点；放下、取消后也不接回焦点（`keepFocusOn` 只管键盘拿起的）。出处：`drag.tsx:1599-1622`；`Round8Tests.test_keyboard_drag_keeps_focus_on_the_dragged_item`（鼠标、手指拿起的不管）。

### 六、触屏长按（T）

- **T-01 拿起**：
  - 把手上单指按下（多于 1 指不拿起），按住 250ms、没挪出 5px（从按下处量直线距离）就拿起，不需要再动。
  - 没到 250ms 就挪出 5px，算在滚动：作废，不拿起，页面照常滚。
  - 没到 250ms 就松手：普通点按，系统的 click 照常。
  - 出处：`drag.tsx:205, 242-351`；`SidebarDragTests.test_touch_hold_lifts_at_250ms_and_moving_wins_over_the_long_press_menu`。
- **T-02 iOS**：拖动区挂着期间，window 上一直有一个非 passive 的 `touchmove` 监听（`HoldTouchSensor.setup`），后挂的 `preventDefault` 才管用。出处：`drag.tsx:252-257`。
- **T-03 拿起以后**：
  - 清选区；拦 click，在 document 捕获阶段 `stopPropagation` 加 `preventDefault`；`selectionchange` 时清选区；`contextmenu` 被 preventDefault。
  - 之后的 `touchmove` 都 `preventDefault`，页面不滚。
  - 不盖挡板，不改光标。
  - 出处：`drag.tsx:279, 289-315`。
- **T-04 「在拖」的判断**：拿起以后，手指离按下处超过 10px（`TOUCH_DRAG_SLOP = LONG_PRESS_SLOP`），才记成在拖（`markDragMoved`）。8px 的一步、拿起前漂过 4px、拿起后抖 1–2px，都不算。出处：`drag.tsx:206-209, 310-313`；同 T-01 用例。
- **T-05 与长按菜单（行、公司组头）**：
  - 按住不动到 500ms（`LONG_PRESS_MS`），菜单照常出来（不按模态开），拿起的那一项留着。
  - 已经在拖，菜单就不出。
  - 菜单出来以后手指再动、记成在拖，就收起菜单，接着拖。
  - 卡片把手没有长按菜单。
  - 出处：`hooks/use-long-press.ts:19-88`；`SidebarDragTests.test_long_press_menu_opens_only_while_the_lifted_row_stays_still`、`Round11Tests.test_long_press_menus_open_without_the_modal_page_work`。
- **T-06 点按（拿起后没记成在拖，按下不到 500ms 就松手）**：
  - 拦下 `touchend` 的默认动作，不要系统的 click。
  - 当场取消（不滑回、不等待），当场摘掉拦点击的监听。
  - 在按下的那个元素上补发一次冒泡的 `click`，坐标是按下处（会打开对话或折叠组）。
  - 元素已经不在页面上时只取消，不补点击。
  - 280ms、450ms 都算点按，抖一下也算。
  - 出处：`drag.tsx:321-361`；同 T-01 用例。
- **T-07 按到菜单时间以后原地松手**：照常结束（浮层原地落下，没挪过就当场结束），菜单留着，不补点击，松手后的 click 照样拦 50ms；`useLongPress` 的 `touchend` 也会 `preventDefault`。出处：同 T-01 用例、`hooks/use-long-press.ts:55-58`。
- **T-08 拖动后松手**：同 M-14 到 M-19 先落位再结束；松手后的 click 拦 50ms。出处：`drag.tsx:327, 341, 348`；同 T-01 用例（`beforeEnd`）。
- **T-09 touchcancel（系统收回了这次触摸）**：先滑回再取消；手势已经结束，不挂 M-24 那种拦截。出处：`drag.tsx:276, 331, 344`；`Round10DragTests.test_cancel_waits_for_the_slide_back_in_every_sensor`、`Round11RetestBDragTests.test_a_lifted_gesture_never_ends_in_a_click`。
- **T-10 转屏（resize）、切到后台（visibilitychange）、外接键盘 Esc**：先滑回再取消，并挂 M-24 的拦截：拦到 `touchend` / `touchcancel` 后 50ms，已在后台时不挂。拿起前遇到这些，只作废这次按下。出处：`drag.tsx:277-278, 317-319, 344-347`；`Round11RetestBDragTests` 两条用例。
- **T-11 跟手、让位、跨列、临时已置顶、收着的组、自动滚动、落位、取消滑回**：同鼠标（M-07 到 M-28），只是没有挡板，也没有滚轮转交。出处：同上各条。
- **T-12 手机抽屉**：拖动中（`isDragging()`）手指横着偏一点，抽屉也不跟着推。出处：`components/mobile-shell.tsx:89-94`；`SidebarDragTests.test_sidebar_drag_wiring`。
- **T-13 层级**：浮层（55）在抽屉和手机会话栏（50）之上，在菜单（60）之下，所以按住不动出来的长按菜单不会被拿起的那一项盖住。出处：`drag.tsx:1480-1482`；`SidebarDragTests.test_lifted_overlay_sits_under_menus_and_over_drawers`。
- **T-14 触屏样式**：触屏上的对话行不能选字、不弹系统长按菜单（`-webkit-touch-callout: none; user-select: none`）；卡片把手 `touch-action: manipulation`，不能选字、不弹 callout。出处：`globals.css:1862, 2306`。

### 七、键盘（K）

- **K-01 拿起**：焦点在把手上时按空格（`code === "Space"`）就拿起；事件目标必须是把手本身，焦点在行里的「···」上按空格不拿起【库：`KeyboardSensor.activators`】。回车不拿起：在行、组头上回车是打开、折叠，在卡片把手上什么也不做。出处：`drag.tsx:210-215`。
- **K-02 拿起时滚动**：那一项完全在窗口之外时，`scrollIntoView({ block: "center" })`【库：`KeyboardSensor.handleStart`】。
- **K-03 焦点**：
  - 拖动中焦点一直在被拖那一项的把手上；它重挂过的，跟到新节点上（`useFollowFocus`）。
  - 拖动中，按在把手上的方向键、Home/End、PageUp/PageDown 标成已处理：组件库行自带的 ↑↓ 导航不接手，侧栏键盘（roving）也让开。
  - 出处：`drag.tsx:1572-1643, 1677-1681`、`lib/sidebar-keys.ts:217`；`Round8Tests.test_keyboard_drag_keeps_focus_on_the_dragged_item`。
- **K-04 ↑ ↓ 移动**：
  - 按这一列的先后一格一格换「瞄着的那一项」（`keyboardAim`）。这一列指拖动项所在那一列的 items，只取能放、没禁用的项和它自己；拿起时瞄着的是它自己；到头了不动。
  - 浮层挪到挪过去以后它该在的位置（`keyboardSlot`）：往后挪时底边对齐目标的底边，往前挪时顶边对齐目标的顶边，回到原位时就是它自己的版面位置。挪动带 `transform var(--cc-dur) var(--cc-ease)` 过渡。
  - 落点直接就是瞄着的那一项（`keyboardCollision`），不按几何远近算，所以高矮差很大的卡也不会跳格、不会误判成取消。
  - 瞄着的那一项不见了时，从它自己接着算；落点退回它自己。
  - 出处：`drag.tsx:910-947, 1036-1069, 1505-1507`；`Round11RetestBDragTests.test_keyboard_moves_follow_the_list_order_whatever_the_heights`。
- **K-05 只在本列**：键盘拖动只在原来那一列里挪：对话行不会拖进已置顶，不调 `onOver` / `onPointer`，不出临时已置顶，限位也只按本列算。多选而选中的不全在本组时，本组没有可挪的目标，按方向键不动。出处：`drag.tsx:898, 1053-1060, 1083-1086`；`SidebarDragTests.test_drag_pool_keeps_rows_in_their_group_or_the_pinned_section`。
- **K-06 ← → 键**：只吞掉默认动作，不动。Home、End、翻页键在把手上标成已处理，不动。出处：`drag.tsx:1044-1048`。
- **K-07 快按**：目标落在滚动区下半截（往上拖时是上半截）时改为滚动滚动区，滚动方式 `auto`（立即滚到位，不平滑），所以约 30ms 间隔连按，每一下都动。出处：`drag.tsx:216-220`；`SidebarDragTests.test_fast_arrow_keys_each_move_one_step`。
- **K-08 放下**：空格、回车、Tab 都放下，都 `preventDefault`（Tab 不移焦点）。之后同 M-14 到 M-19：挪过就先落位再结束，没挪过就当场结束。出处：`drag.tsx:211-215, 1197`。
- **K-09 取消**：Esc（`code === "Escape"`）先滑回再取消（`settleBack`）。键盘不会跨列，只走同列那一路。window resize、visibilitychange 也取消【库】。出处：`Round10DragTests.test_cancel_waits_for_the_slide_back_in_every_sensor`。
- **K-10 松开空格**：把手上的 `keyup` Space 被 `preventDefault`，不当成点击（不打开对话、不折叠组）。出处：`drag.tsx:1688-1689`、`thread-list.tsx:1062`、`sidebar-group.tsx:75`。
- **K-11 拖动中的其它键**：菜单键和 Shift+F10 不开「···」（行和组头的 `onKeyDown` 在 `isDragging()` 时只交给拖动）；Alt+方向键也归拖动（`altMove` 让开）。出处：`thread-list.tsx:1074-1085`、`sidebar-group.tsx:76-86`；`Round8Tests.test_keyboard_drag_keeps_focus_on_the_dragged_item`。
- **K-12 放下或取消后的焦点**：放下、取消后 5 秒内，每帧检查一次。焦点丢了（落到 body 或节点不在了）时，接回这一项此刻的把手（必须看得见，换组重挂时以新登记的为准）；焦点在别处（是人自己挪的）就不抢；鼠标或手指按下以后不再接回。出处：`drag.tsx:1578-1622`；`Round8Tests.test_keyboard_drag_keeps_focus_on_the_dragged_item`。
- **K-13 键盘拖动没有的东西**：挡板、自动滚动（只有 K-07 的滚动）、跨列、临时已置顶。出处：`drag.tsx:1299, 1005`、【库：`KeyboardSensor.autoScrollEnabled = false`】。
- **K-14 Alt+↑/↓（不在拖动中）**：
  - 按的那个按钮挪一格；Meta 或 Ctrl 同时按着时不算。
  - `preventDefault`，挪完在下一帧把焦点放回原来的按钮（React 挪动节点时可能失焦）。
  - 播报见 S-16。
  - 卡片：卡头开关、把手都能用，先挪、再念（没有排序上下文时照样挪）。
  - 组头、行：菜单键优先，其次 Alt，剩下的交给拖动。
  - 出处：`drag.tsx:1816-1828`、`company-cards.tsx:158-163, 197-199`、`sidebar-group.tsx:76-86`、`thread-list.tsx:1074-1085`。

### 八、Esc 与其它层（E）

- **E-01 认出拖动的 Esc**：拖动期间，在 window 捕获阶段（最先收到）记下每一个 Esc。`isDragEscape(e)` 的判断：这个 Esc 记过，或者正在拖、而且还没放手。拖动库处理完 Esc、已经结束拖动以后，同一个 Esc 在 window 上照样被认出来。出处：`lib/layers.ts:9-22, 112-119`；`SidebarDragTests.test_escape_while_dragging_only_cancels_the_drag`。
- **E-02 拖动中的 Esc 只取消拖动**：不关浮着的或全屏的会话栏（`session-panel.tsx` 遇到 `isDragEscape` 就返回），不清侧栏多选（`sidebar-keys.ts` 同样）。鼠标拖、键盘拖都一样。出处：`session-panel.tsx:155-165`、`lib/sidebar-keys.ts:272-283`；`Round5SmallFixesTests.test_escape_closes_one_layer_at_a_time`、`panel:TitleBarSourceTests.test_top_right_files_and_panel_toggle`。
- **E-03 放手以后的 Esc**：在等落位、等滑回（跨列取消已经 `releaseDrag("keep")`）期间按 Esc，不再算拖动的 Esc，照常关会话栏、清多选；同时窗口捕获阶段的 `keydown` 让等着的那次结束当场完成。出处：`lib/layers.ts:100-110`、`drag.tsx:794-801`；`SidebarDragTests.test_escape_after_release_is_not_a_drag_escape`。
- **E-04 层次**：从上往下依次是弹窗和菜单、拖动、改名和编辑框、浮着的会话栏、侧栏多选。每次按 Esc 只关一层：拖动取消一次，会话栏关一次，多选清一次。出处：`lib/layers.ts:1-5`；`Round5SmallFixesTests.test_escape_closes_one_layer_at_a_time`。

### 九、多选（B）

- **B-01 整批拖动**：对话行被选中、而且选中多于 1 条时，拖动它就是整批拖动。`count` 等于选中条数，label 是「N 个对话」。浮层右侧显示条数徽标（`.cc-drag-count`）。出处：`thread-list.tsx:1007-1014`、`drag.tsx:1515`、`globals.css:2068-2072`；`Round8Tests.test_the_float_steps_aside_over_a_folded_group_head`。
- **B-02 其余选中的行变淡**：拿起时 `onActive` 在根元素上加 `data-cc-drag-bulk`，其余选中的行透明度 0.4（拖的那一条自己是空档，不归这条规则管），结束时去掉；侧栏卸下时也去掉。出处：`thread-list.tsx:397, 599-609`、`globals.css:2075`。
- **B-03 选中的不全在这一组**：本组的行不是落点，只能拖进已置顶（`wholeBatch`）。出处：`thread-list.tsx:605`、`sidebar-drag.ts:sidebarAccepts`；`SidebarDragTests.test_what_can_be_dropped_where`。
- **B-04 拖进已置顶**：整批按侧栏里眼前的先后排进落点，逐条置顶，然后清空选中。出处：P-09。
- **B-05 落在本组**：选中的全在本组时，整批作为一块排到落点（其余各条从原处拿掉，紧挨着排进来），然后清空选中。出处：P-09、`sidebar-drag.ts:placeBlock`。
- **B-06 播报**：用整批的 label，如「已拿起「3 个对话」。」。出处：A-08。

### 十、视觉与动效（V）

- **V-01 浮层的样子**：
  - 弹层底（`--cc-bg-popover`）、弹出阴影（`--cc-shadow-pop`）、圆角同行（卡片用面板圆角）。
  - 透明度 0.9（`--cc-drag-opacity`），放大 1.02（`--cc-drag-scale`）。
  - 拿起时从原样浮起：`cc-drag-lift` 动画，`--cc-dur-fast` 120ms。
  - 浮层不接指针（`pointer-events: none`）。
  - 出处：`globals.css:703-707, 2053-2060, 2082`；`panel:SortableSourceTests.test_drag_lift_and_grip_slot_styles`。
- **V-02 浮层的克隆**：拿起时深克隆一次原来那一项，去掉 A-07 列的属性，以及行内的 transform、transition、opacity，外边距清零。外面套一层层祖先的空壳：只抄元素本身、不带兄弟，`display: contents !important`，这样后代选择器、变量、继承都和原来一样（公司组里对话行的缩进、抽屉里的字色）。重挂到另一列时不重新克隆。出处：`drag.tsx:1439-1478`；`SidebarDragTests.test_lifted_copy_keeps_the_ancestor_scoped_styles`。
- **V-03 浮层的位置和大小**：拿起时那一项的框；fixed 定位；外层大小由唯一的子元素撑开（量落点时量它）。出处：`drag.tsx:643-644`、【库：`DragOverlay`】。
- **V-04 落位和滑回中**：`data-dropping=""` 时放大、阴影、透明度收回（`scale 1; opacity 1; box-shadow none`）；`"into"` 时缩到 0.96、淡到 0。让开（peek）的规则只在拖动中生效：取消时字和条数回来；落进收着的组时字一直隐着，底色随落位淡掉。出处：`globals.css:2061-2067`；`Round8Tests.test_the_float_steps_aside_over_a_folded_group_head`。
- **V-05 空档与落点**：空档 `[data-drag-source="gap"]` 透明度 0；落点那一项在落位播完前 `[data-drag-landing]` 透明度 0；收着的整块被指着时 `[data-drop-target]` 用悬停色。出处：`globals.css:2073-2077`。
- **V-06 动效变量**：时长取 `--cc-dur`（200ms），缓动取 `--cc-ease`（`cubic-bezier(0.32, 0.72, 0, 1)`）。系统要求减少动态效果，或设置里 `data-cc-motion="reduce"` 时：不播让位、落位、滑回、整列滑动，浮起动画和过渡也关掉，放下、取消当场完成。这个判断在 `DragArea` 挂上时读一次。出处：`drag.tsx:492-502, 960`、`globals.css:2083, 2273-2275`；`panel:SortableSourceTests.test_shared_drag_rules`。
- **V-07 落位、滑回都从这一刻起算**：不回拨到上一帧，也不等合成线程回报开始；整列滑动同样不回拨。出处：`drag.tsx:698-702`；`SidebarDragTests.test_release_starts_the_landing_and_ends_the_drag_after_it`、`SidebarDragTests.test_the_column_slides_when_the_gap_changes_group`。
- **V-08 拖动层**：body 下有一个常驻的 `div.cc-drag-layer`（第一次用时建，被移走了就重建），里面放挡板和浮层，不带 `data-slot`。出处：`drag.tsx:1526-1540`；`Round11DragFramesTests.test_measuring_reads_the_layout_position_and_the_overlay_sits_in_its_own_layer`。

### 十一、牵连模块（X）

- **X-01 `isDragging()` 的时间窗**：从拿起到结束拖动那次提交为止，包括落位和滑回的等待期（约 0.2s）。期间：
  - 侧栏键盘 roving 不移焦点。
  - 悬停滚动（Marquee）不滚。
  - 抽屉横滑不跟手。
  - 行和组头的 `onKeyDown` 只交给拖动。
  - 出处：`lib/sidebar-keys.ts:217`、`lib/hover-scroll.ts:47-50`、`components/mobile-shell.tsx:91`。
- **X-02 卸下**：`DragArea` 卸下时，还在等挪回原组的取消、还在等落位的放下，都当场结束，`setDragging(false)`。出处：`drag.tsx:1231-1244`。
- **X-03 拖动区没有拖动时**：各把手照常；行的点击、Shift 或 ⌘ 多选、长按菜单、菜单键都照旧。出处：`thread-list.tsx:1046-1090`。
- **X-04 侧栏不把「拖的是什么」存成状态**：拿起、放下不重画整列，只有 `reveal` 和 `guest` 变时才重画。行、组头、行里的字、菜单、Marquee、`Items` 都记住（memo），组头的开关和对话列表给稳定的一份。出处：`thread-list.tsx:333-346, 744-765, 1022-1026`；`Round10BTests.test_lifting_does_not_redraw_the_whole_list`、`Round11DragFramesTests.test_whole_list_redraws_skip_unchanged_groups`。

### 十二、已知问题（不要求新引擎保留，记下来对照）（Z）

- **Z-01** 挪回原组时，第一行先往下跳约 16px（第 10 轮 B 段审查，minor，原有问题，没查）。来源：`2026-10-16-cc-shell-rebuild.md:416`。
- **Z-02** 无界面 Chrome 里连做几次鼠标 Esc 拖动以后，下一次按下移动不出帧（两版一样，判为无界面模式的毛病）。来源：同文件 `:654`。
- **Z-03** 第 11 轮重测里「拖动中菜单先藏后卸、组头菜单位置预算」两项，推到本轮和自写拖动一起做：前者省掉的主要是拖动库 body 观察器的同步量框，换掉拖动库以后就不存在了。来源：同文件 `:663`。

### 十三、现有测试（验收用）（R）

- **R-01 跑代码的行为用例，期望值就是验收基准**。构造参数里依附 dnd-kit 形状的部分，可以随新引擎改写；期望的结果、播报、调用先后不改。
  - 共用拖动：
    - `SidebarDragTests`：`test_drop_is_announced_by_what_really_happened`、`test_touch_hold_lifts_at_250ms_and_moving_wins_over_the_long_press_menu`、`test_long_press_menu_opens_only_while_the_lifted_row_stays_still`、`test_lifted_copy_keeps_the_ancestor_scoped_styles`、`test_drag_pool_keeps_rows_in_their_group_or_the_pinned_section`、`test_drop_lands_first_then_hands_over`、`test_release_starts_the_landing_and_ends_the_drag_after_it`、`test_the_column_slides_when_the_gap_changes_group`、`test_the_landing_stays_inside_the_list`、`test_escape_while_dragging_only_cancels_the_drag`、`test_escape_after_release_is_not_a_drag_escape`
    - `Round5SmallFixesTests`：`test_lifting_announces_picked_up_not_a_position`、`test_alt_arrow_moves_are_announced_like_dragging`
    - `Round8Tests.test_keyboard_drag_keeps_focus_on_the_dragged_item`（行为段）
    - `Round10DragTests`：`test_release_at_the_target_and_cancel_land_before_the_redraw`（行为段）、`test_cancel_waits_for_the_slide_back_in_every_sensor`
    - `Round10BTests`：`test_the_drag_cursor_waits_for_the_lift_frame_to_paint`、`test_escape_after_dragging_into_pins_slides_back_before_the_redraw`（行为段）
    - `Round11DragFramesTests`：`test_rows_keep_the_same_props_when_sorting_starts`、`test_items_that_do_not_move_are_never_written`、`test_drag_cursor_only_for_the_mouse_and_gone_at_release`、`test_the_mouse_drag_shield_blocks_hover_and_passes_the_wheel_on`、`test_measuring_reads_the_layout_position_and_the_overlay_sits_in_its_own_layer`（行为段）
    - `Round11RetestBDragTests`（3 条全部）
  - 调用方纯函数：`SidebarDragTests.test_what_can_be_dropped_where`、`test_manual_order_inside_a_group`、`test_unpinned_chats_leave_the_manual_order_and_reset_clears_it`，`Round10DragTests.test_an_unpinned_chat_returns_to_the_top_of_its_group`。
  - 会话栏：`panel:PanelLogicTests` 的 `test_card_order_moves_only_between_visible_cards`、`test_cards_toggle_summaries_and_grip_slot`（SSR 标记与把手属性先后）、`test_drag_drop_order_and_announcements`、`test_lifted_card_stays_in_the_column_not_the_visible_box`。
- **R-02 只查源码字符串的用例**，换引擎时会随写法失效，要按同一意图改写成新引擎上的断言或行为用例，不能删掉意图：
  - `SidebarDragTests`：`test_sidebar_drag_wiring`、`test_dragged_chat_rows_use_the_shared_gap_and_landing`、`test_fast_arrow_keys_each_move_one_step`（含读 dnd-kit 源码的一段）、`test_the_float_turns_see_through_over_a_folded_group_head`、`test_lifted_overlay_sits_under_menus_and_over_drawers`
  - `Round6Tests.test_search_locks_every_sidebar_drag`
  - `Round8Tests` 的源码段
  - `Round10BTests.test_lifting_does_not_redraw_the_whole_list`
  - `Round11Tests`：`test_long_press_menus_open_without_the_modal_page_work`、`test_review_small_fixes`（`useSensor` 那段）
  - `Round11DragFramesTests`：`test_only_flow_areas_skip_size_watching`、`test_whole_list_redraws_skip_unchanged_groups`、`test_pointer_is_not_reported_after_the_drag_ends`
  - `panel:SortableSourceTests` 3 条：`test_the_panel_uses_the_shared_drag` 锁了 `@dnd-kit/*` 的版本，按任务书这一轮不动 `package.json`，留到删依赖那一轮再改。
- **R-03 第 11 轮的帧数口径**（性能追踪里「这一帧第一个任务开始 → 绘制结束」，6 倍节流，复刻页：vite build 整页 + 假接口，3 条置顶、14 家公司 × 4 条、最近 8 条）。当时的基线：
  - 电脑鼠标拖对话行：拿起 65–75、落定 52–61。
  - 键盘空格拖组头：拿起 75–94、空格放下 55–61。
  - 手机长按对话行：拿起 56–67、落定 50–60。
  - 手机长按公司组头：拿起 68–82、落定 20–24。

  新引擎沿用同一口径对照，并另外交给测试窗口在 A14 上复测（目标：拿起 30–45ms、落定 15–25ms）。来源：`2026-10-16-cc-shell-rebuild.md:522-528`。

## 施工（第 12 轮，开发窗口）

状态：实现、本地检查、复刻页实测已做；**测试机复测、A14 真机复测还没做**（本轮不同步测试环境，交测试窗口）。分支 `pearnly/cc-drag-engine`，未推送、未合并。

### 改法

只改 `frontend/assistant/src/components/ui/drag.tsx`（`lib/layers.ts`、`globals.css` 不用改），`drag.tsx` 不再 import `@dnd-kit/*`；调用方（`thread-list.tsx`、`projects.tsx`、`sidebar-group.tsx`、`company-cards.tsx`、`session-panel.tsx`）一个字没动。`package.json` / `package-lock.json` 本轮不动，依赖下一轮删。

- **拖动本身搬出 React**：每个 `DragArea` 一个 `DragEngine`（`useMemo` 建一次）。登记的各项、各列的先后、量好的位置、一次拖动的全部状态都在它里面；`DragArea` 每次重画只把最新的回调交给它（`engine.props`），拿起、放下都不重画 `DragArea`。
- **各项只订阅自己**：`useSortableItem` / `useDropZone` 用 `useSyncExternalStore` 按拖动编号订阅「我是不是被拖的那一项」「我是不是被指着的收着的整块」。拿起、落定只叫醒被拖的那一项（重画成空档、键盘拿起时焦点跟着它），其余各项不重画。`DragList` 只把列的编号放进上下文，先后交给拖动区（`setList`），先后变了各项也不重画。
- **一次量好**：拿起那一刻先只读（滚动祖先走一遍、拿起的那一项、能放的各处的框：`getBoundingClientRect` 加行内让位位移，不读计算样式），再写（浮层、播报、空档）。拖动中跟手、限位、落点、让位都按这份位置算，滚动只加滚动差（监听主滚动区的 scroll）。版面真变了才重量：侧栏由整列滑动（`flowLayout`）的观察器触发、下一帧重量一次；会话栏在拿起那一帧画完后给能放的卡片挂一个尺寸观察器（卡片拖动中会变高），变了下一帧重量。
- **拖动中不经 React**：指针每动一下只写浮层的行内 `transform`；经过的项变了才算让位（同原来的竖排让位：原位与落点之间的各项挪「拖动项高 + 间距」，空档挪到落点），位移直接写到节点上（`applyShift`，先写过渡再写位移，不让位的不写）。
- **浮层与播报区是自己建的节点**：浮层在拿起时克隆一次（`cloneOf` 不变），挂进 body 下常驻的拖动层（拖动区挂上时就建好）；说明（`DndDescribedBy-n`）与 assertive 播报区（`DndLiveRegion-n`）在拖动区挂上时建好常驻，播报直接改文字（同一句不重写）。拖动中 body 下不增删节点。
- **结束拖动一次同步提交**：落位播完（`ReleaseFirst` 不变）以后，调用方存顺序（`onDrop`）、`onActive(null)`、被拖的那一项去掉空档放在同一个 `flushSync` 里提交；提交后各项去掉让位（不带过渡，新顺序已排好）、浮层卸下（落位没播完就等它播完，落点那一项先 `data-drag-landing`）。在 React 提交里结束的（挪回原列重挂出来、滑回没起播）不再嵌套同步提交。
- **三种输入**：鼠标（`MouseSensor`：按下处移动超过 3px 拿起、松手后 50ms 拦点击、拖动中不选字不起系统拖放不出右键菜单、Esc / resize / 切后台取消）、键盘（`KeyboardSensor`：空格拿起、下一个任务起收键，放下 / 取消 / 其余交给 `step`）自己写，手机长按（`HoldTouchSensor`）、`landFirst`、`blockReleaseClick` 原样保留。拿起的判断同原来：右键、已处理过的按下（Shift 点对话行）、另一次拖动在进行、键盘焦点不在把手本身上都不拿起。
- **键盘、落点、动效**：`narrowPool`、`keyboardAim` / `keyboardSlot` / `keyboardCollision`、`landOverlay` / `settleBack` / `slideOverlay`、跨列取消的 homing、`flowLayout`、`clampToList` 等纯函数原样保留；碰撞的「最近中心」改成自写的 `closestCenter`（同算法）。键盘目标落在滚动区下半截（往上时上半截）时改为立即滚动滚动区的算法照原来拖动库那一段写进 `step`。自动滚动照原来拖动库的默认（滚动区上下 20%、越深越快最快每 5ms 10px、只朝指针动过的方向、落位与滑回期间冻住）。

### 复刻页实测（修前 `d70f7f00` / 修后本提交）

做法：vite build 整页 + 假接口（scratchpad，3 条置顶、14 家公司 × 4 条、最近 8 条），无界面 Chrome，CPU 6 倍节流；电脑 1024×768 真鼠标、真键盘（CDP Input），手机 412×915 触屏。帧 = trace 里的 AnimationFrame（这一帧第一个任务开始 → 绘制结束），每个动作取时间窗里最长的一帧；各跑 3 遍取中位数（括号里是 3 遍原值）。打标记的任务与操作之间隔 40ms，免得标记那一下算进操作那一帧。单位 ms。

| 场景 | 动作 | 修前 | 修后 |
|---|---|---|---|
| 电脑 鼠标拖组里的对话行 | 拿起（含之后挡板那一帧） | 53.6（50.3 / 57.1 / 53.6） | 36.8（36.8 / 37.2 / 36.2） |
| | 拖动中（窗口内中位帧 / 最长帧） | 14.7 / 29.7 | 8.9 / 26.3 |
| | 松手 | 32.2 | 31.2 |
| | 落定 | 44.5（43.0 / 44.5 / 48.7） | 34.9（35.5 / 34.9 / 34.1） |
| 电脑 鼠标拖对话进已置顶再 Esc | 拖进置顶 | 42.4 | 25.9 |
| | Esc 那一帧 | 9.3 | 9.3 |
| | Esc 下一帧（挪回原组） | 34.4 | 25.8 |
| 电脑 键盘拖公司组头 | 空格拿起 | 79.3（58.9 / 79.3 / 94.2） | 32.4（30.2 / 33.0 / 32.4） |
| | ↓（中位帧 / 最长帧） | 12.9 / 24.7 | 14.8 / 27.6 |
| | 空格放下（这一下目标已滚到浮层下面，两版都当场结束） | 43.4 | 27.7 |
| 手机 长按对话行 | 拿起（250ms） | 59.1（59.1 / 54.8 / 60.3） | 17.4（18.9 / 17.4 / 17.4） |
| | 出长按菜单（500ms，组件库菜单） | 46.3 | 54.5 |
| | 手指再动收菜单 | 31.6 | 29.7 |
| | 拖动中（中位帧 / 最长帧） | 15.5 / 25.9 | 16.2 / 24.9 |
| | 松手 | 16.4 | 18.5 |
| | 落定 | 43.6（43.6 / 47.4 / 14.7） | 35.0（35.0 / 33.8 / 35.4） |
| 手机 长按公司组头 | 拿起 | 55.4 | 18.8 |
| | 出长按菜单 | 53.8 | 53.6 |
| | 收菜单 | 19.2 | 29.8 |
| | 拖动中（中位帧 / 最长帧） | 15.6 / 18.2 | 18.0 / 24.8 |
| | 松手 | 17.0 | 19.8 |
| | 落定 | 18.9 | 22.8 |

同一时间窗里主线程的总忙碌时间（顶层任务合计，中位数）：鼠标拖行 拿起 146→101、拖动中 139→90、落定 115→110；键盘公司组 拿起 119→69、↓ 70→52、放下 50→35；手机对话行 拿起 87→43、拖动中 71→34、落定 81→69；手机公司组 拿起 84→45、收菜单 32→29、拖动中 61→26、落定 74→57；拖进置顶 255→193。

读法与带 CPU 采样的拆解（scratchpad 的 `prof.py`）：

- 拿起：手机 55–59 → 17–19，键盘 79 → 32。鼠标 37 里约一半是拿起后那一帧挡板盖上、指针下面的行换悬停引起的整块样式重算（约 690 个元素、6 倍节流约 13ms），修前也有，归第 11 轮的挡板设计，不在拖动库里；拿起那一下本身（量、克隆、播报、被拖的那一项重画）约 9ms。
- 拖动中：每一下只写浮层位移，采样里拖动那一帧主线程只有约 1.5ms（触摸事件）。手机公司组的「拖动中 / 收菜单」最长帧比修前长 6–10ms，但同一窗口的总忙碌时间是 61→26、32→29：多出来的是帧里等下一次绘制的空闲（输入事件来得早、绘制在下一个刷新点），不是活多了；收菜单那一帧是组件库菜单卸下（Radix Presence / 焦点归还），两版同一份。
- 落定：鼠标、手机拖对话行 44–45 → 35。剩下的主要是侧栏存了组里新顺序以后整列重排的那一次同步提交（调用方 `CompanyGroups` 按新顺序重算各组、14 个组头、这一组的行换位置，6 倍节流约 18ms），这是调用方的重画，本轮按任务书不改调用方；A14 目标 15–25 在复刻页上还没达到，要等真机复测再定要不要动侧栏那一侧。
- 出长按菜单 46–54 两版相当（带采样那次修前 60.5、修后 57.3），全是组件库菜单与浮动定位；Z-03 的「拖动库 body 观察器同步量框」随拖动库一起没了，但菜单那一帧本身没有因此变短。
- 「滑回后」那一段（Esc 之后 300–900ms）两版都只有应用自己的后台重画（输入框自动高度、会话轮询），与拖动无关，不列。

### 对照清单逐条结果

行为对照：复刻页上同一套脚本（scratchpad 的 `behave.mjs`，真鼠标、真键盘、触屏，不节流，每条新开一个无界面 Chrome）在修前、修后各跑一遍，逐项比较页面状态（各组先后、置顶、卡片顺序、两个播报区的字、正开着的对话、浮层 / 空档 / 让位位移是否清干净、焦点、`data-drop-target` / `data-peek`、读屏属性）。23 项检查修前修后**全部一致**：鼠标组内排序、拖进置顶放下、拖进置顶后 Esc 再回原行松开、组内拖开 Esc 回原行松开、点一下照常打开、键盘公司组排序 / Esc / 快按 4 下、Alt+↓、会话栏卡片鼠标与键盘排序及把手属性、手机点按、长按出菜单再拖、长按出菜单原地松手、touchcancel、拖动中改窗口大小、已置顶收着时拖到组头（高亮、半透明、放下置顶）、多选两条拖进置顶、多选两条组内整批、自动滚动与拖动中滚轮、置顶内排序、拖进置顶再拖回本组、没有置顶时临时出空的已置顶（放下置顶 / Esc 不留下）、读屏属性。

- **P 拖动处**：P-01 至 P-10 通过（卡片、公司组头、置顶行、组里的行、多选、Alt 都在复刻页过了；P-06 搜索锁住、没存到服务端的行禁用走调用方原样的 `disabled`，引擎里禁用项不出把手、不拿起、不当落点，`Round6Tests` 与引擎用例覆盖，复刻页未单独操作搜索）。
- **I 接口**：I-01 至 I-05、I-07 通过（调用方零改动，`tsc` 通过）。I-06：纯函数与 `ReleaseFirst`、`HoldTouchSensor`、`LandingMouseSensor`、`LandingKeyboardSensor`、`blockReleaseClick`、`cloneOf`、`dragLayer`、`wheelUnder`、`keepFocusOn`、`rememberHandle`、`applyShift`、`shiftStyle`、`OVERLAY_Z` 等照旧导出、用例期望值不变；有意变化见下。
- **A 静态 ARIA**：A-01 至 A-09 通过（卡片把手 `role="button" tabindex="0" aria-disabled="false" aria-roledescription="sortable" aria-describedby="DndDescribedBy-1" data-drag-handle=""`，侧栏把手只有 `aria-roledescription` / `aria-describedby` / `data-drag-handle`，说明与播报区编号、位置、样式同原来；SSR 输出被 `panel:PanelLogicTests.test_cards_toggle_summaries_and_grip_slot` 锁住）。
- **S 播报**：S-01 至 S-18 通过（拿起、经过、拿起时经过自己不念、拖进置顶「将置顶」、拖回本组念位置、放下按实际结果、取消、点按先「已拿起」再「已取消」、长按菜单原地松手念取消、Alt 另一个播报区，复刻页逐字一致）。时机：原来是拖动库在提交后的副作用里设，现在在同一时刻直接写播报区，念的字与先后不变。
- **M 鼠标**：M-01 至 M-29 通过。M-18 见有意变化（`holdStill` 去掉）。
- **T 触屏**：T-01 至 T-14 通过（T-10 切后台、外接键盘 Esc 由 `Round11RetestBDragTests` 两条用例覆盖，复刻页做了转屏同类的 resize 与 touchcancel）。
- **K 键盘**：K-01 至 K-14 通过（K-02 键盘拿起时那一项在窗口外就滚到中间：同原来的条件写进引擎，复刻页未构造这种位置）。
- **E Esc 与其它层**：E-01 至 E-04 通过（`lib/layers.ts`、`ReleaseFirst` 未改）。
- **B 多选**：B-01 至 B-06 通过（条数徽标 2、其余选中变淡、拖进置顶整批、组内整批、「2 个对话」播报）。
- **V 视觉与动效**：V-01 至 V-08 通过（浮层结构与类名、`data-kind` / `data-peek` / `data-dropping`、z-index 55、拖动层、动效变量与减少动态效果都同原来；样式表未改）。
- **X 牵连模块**：X-01 至 X-04 通过（`isDragging()` 时间窗同原来，卸下当场结束）。
- **Z 已知问题**：Z-01（挪回原组时第一行先往下跳约 16px）未查、未改；Z-02 无界面模式毛病不涉及；Z-03 拖动库 body 观察器没了，菜单那一帧本身未变短（见上）。
- **R 测试**：R-01 的行为用例期望值都没改、全过。R-02 的源码字符串断言按同一意图改写到新引擎上（`SidebarDragTests` 6 条、`Round5SmallFixesTests`、`Round8Tests` 2 条、`Round10DragTests`、`Round10BTests`、`Round11Tests`、`Round11DragFramesTests` 7 条、`Round11RetestBDragTests`、`CompanyMasterSourceTests`、`panel:SortableSourceTests.test_shared_drag_rules`）；`test_fast_arrow_keys_each_move_one_step` 原来读拖动库源码的那段改成读自写的 `step`；`test_keyboard_moves_follow_the_list_order_whatever_the_heights` 的 `closestCenter` 改从 `drag.tsx` 导入。`panel:SortableSourceTests.test_the_panel_uses_the_shared_drag` 锁 `@dnd-kit/*` 版本那段留到删依赖那一轮。新增 `DragEngineTests` 3 条（直接驱动引擎：键盘排序写让位、拿起 / 落定只叫醒被拖的那一项、拖动中不再读框、body 下不增删节点、鼠标跟手与限位、Esc 先滑回且松开不当点击、有动效时先落位再在同一下交调用方并清让位）。

### 有意变化（行为不变的实现差异）

1. `holdStill` 去掉：它只为挡住拖动库落位前对落点那一项的 `scrollIntoView`；自写引擎落位前不滚动那一项，列表照样不跳（M-18 复刻页、`SidebarDragTests.test_the_landing_stays_inside_the_list` 通过）。
2. 传感器是自写的类：构造参数仍是 `{ active, event, options, onStart, onMove, onEnd, onCancel, onAbort }`；键盘选项的 `coordinateGetter` 换成 `step`（方向键交给拖动区算）。`dropAnimation` 的参数仍是 `{ active, dragOverlay, transform }`。
3. 新增导出 `DragEngine`（行为用例直接驱动）、`closestCenter`、`sortOffset`、`MouseSensor`、`KeyboardSensor`、`KeyboardCode`（Esc 键名写作 `Escape`，避开快捷键提示检查）及几个类型。
4. 会话栏卡片的尺寸观察只在拖动中挂（拿起那一帧画完以后，结束时摘），原来拖动库挂着就一直观察；侧栏仍不观察尺寸。
5. 拖进另一列重挂、还没重量（下一帧）的那一下不算那一列的让位：那一列的空档已经由版面让出来，原来按旧位置算会给空档写一段错位移（看不见，但松手那一下落点会偏），重量后照常。
6. 播报、经过（`onOver`）、指针位置（`onPointer`）在算出落点的同一刻交，不再等拖动库提交后的副作用；先后与内容不变。

### 本地检查

- `tsc --noEmit` 通过；`check-frontend-tokens`、`check-frontend-viewport-units`、`check-frontend-i18n`、`check-architecture`（0 violations）通过。
- `tests.test_assistant_ui`、`tests.test_cc_session_panel`、`tests.test_cc_ccs`、`tests.test_cc_mobile_overlays`、`tests.test_cc_mobile_content` 全过（306 条）。

### 还缺什么

- 测试机（实时源码）与 A14 真机复测：拿起、拖动中、拖进置顶、Esc 下一帧、落定各项，按本节同一口径；读屏（TalkBack / VoiceOver）实听。
- 落定在复刻页 23–35ms，没到 A14 目标 15–25：剩下的是侧栏存顺序后整列重排的那一次同步提交（调用方），要不要改侧栏那一侧等真机数据再定。
- 鼠标拿起后挡板盖上那一帧的悬停样式重算（约 13ms，6 倍节流）未动（第 11 轮的挡板设计）。
- `@dnd-kit/*` 依赖、`package.json` 与 `panel:SortableSourceTests.test_the_panel_uses_the_shared_drag` 里锁版本那段，下一轮一起删；`session-panel.tsx`、`lib/company-panel.ts` 注释里还写着 dnd-kit，按本轮范围没动。
- Z-01 未处理。

## 审查后修正（第 12 轮，开发窗口）

状态：实现、本地检查、复刻页复量已做；**测试机复测、A14 真机复测还没做**。分支 `pearnly/cc-drag-engine`，未推送、未合并。两名核实者都确认的 4 条全部修了，只改 `drag.tsx` 与 `tests/test_assistant_ui.py`，调用方零改动。

### 改了什么

1. **拖进已置顶后取消，已置顶里让过位的行不再停在让位处、最后一下跳回**（M-22、M-23、T-09、T-10、V 组）。`beforeCancel` 跨列那一支（挪回原组、`onReturn`）在起播浮层滑回的同时，把 `d.touched` 里各项的位移带过渡清回原位；`d.touched` 留着，结束时照旧用 `NO_SHIFT` 去掉留下的过渡。
   - 只改这一处时，复刻页逐帧量到 a1 先滑回、再被挪回原组那次整列重挂的整列滑动（`flowLayout`）反向推一下：314 → 273 → 262 → 261 → 268 → … → 280，下冲约 19px。原因：`flowLayout` 记位置时只去掉整列滑动的 `translate`，没去掉排序让位此刻的 `transform`（正走着 CSS 过渡），于是把正在滑回的让位当成版面挪了，又用 `translate` 反向滑一遍，两段叠起来过冲。
   - 所以 `flowLayout` 一起改：记的是版面位置，同时去掉计算样式里 `translate` 和 `transform` 的竖向位移（同一份计算样式，不多算一次样式）。让位有自己的 CSS 过渡，版面挪动由 `translate` 滑，两段各管各的，叠起来连续。这也修掉了修前就有的同类下冲：拖进已置顶、再拖回本组时，a1 在修前与只改第 1 处的版本里都是 314 → 307 → 269 → 267 → 272 → … → 280，现在是 314 → 307 → 296 → 288 → 284 → … → 280，一路单调。拖进已置顶（鼠标、手机）各行的轨迹，修前、只改第 1 处、修后三版逐帧一致。
2. **拖动区在拖动中卸下时，摘掉传感器挂在页面上的监听**（键盘拖动中关掉会话栏、换分组方式，之后第一下空格、回车、Tab、Esc 被吞）。修前用 dnd-kit 时也有这个问题。
   - `SensorInstance` 加可选的 `abort()`：只摘监听、清计时器，不回调。
   - `KeyboardSensor` 调 `detach`（`detached` 挡住还没挂上的 keydown）。`MouseSensor` 全摘；已经松开或取消过的不动，松手后拦点击的那 50ms 照旧；拿起后按键还按着时，补一道 `blockReleaseClick("mouse")`。`HoldTouchSensor` 设 `done`、清长按计时、摘全部监听；拿起过的补 `blockReleaseClick("touch")`。
   - `mount()` 的卸下函数在结束拖动之前，先取出 `pending` 调 `abort()`。
3. **键盘拖动中被拖那一行被 React 挪位、焦点掉到 body 时接回把手**（K-03）。`setList` 存下新先后后调 `regain()`：DragList 的布局副作用就在 React 挪节点的那次提交里，当帧接回。`step()` 开头再调一次作保险。`regain` 只在键盘拖动中、还没放下或取消、焦点掉在 body（或没有）时接回；焦点被人点到别处的不抢。

### 测试

- `DragEngineTests` 新增 3 条：
  - `test_cancel_after_dragging_into_another_list_slides_that_list_back_at_once`：拖进另一列写出让位，Esc 当场清成带过渡的 `""`；之后不再写回；结束后过渡也清掉。
  - `test_unmounting_mid_drag_leaves_no_listeners_behind`：分三种情况卸下拖动区，之后的 Space、Enter、Tab、Esc 都不被 `preventDefault`。键盘拿起后卸下；拿起前（下一个任务才挂收键）卸下；鼠标拖动中卸下，此时松开那一下的点击照拦、之后照常。
  - `test_keyboard_focus_comes_back_after_the_row_is_moved_by_a_redraw`：节点挪位、焦点掉到 body 后，`setList` 当场接回，按方向键也接回；焦点在别处的不抢。
- `SidebarDragTests` 新增 `test_the_column_slide_ignores_a_sort_shift_sliding_back`：让位正在滑回的那一行不另起整列滑动，版面真挪了的照常滑。
- 4 条新用例在改前的代码上都失败，修后都通过。
- `test_keyboard_sort_writes_shifts_and_only_wakes_the_lifted_item` 改成按真实情况先把焦点放在把手上再按空格（真实页面里空格就是在把手上按的），末尾的断言从「焦点在 body」改为「焦点一直在被拖那一项的把手上」。这是本轮自己的引擎用例，R-01 的行为用例期望值都没改。

### 本地检查

- `tsc --noEmit` 通过；`check-frontend-tokens`、`check-frontend-viewport-units`、`check-frontend-i18n` 通过，`check-architecture` 0 violations。
- `tests.test_assistant_ui`、`tests.test_cc_session_panel`、`tests.test_cc_ccs`、`tests.test_cc_mobile_overlays`、`tests.test_cc_mobile_content` 全过，共 310 条（原 306 条加新增 4 条）。

### 复刻页复量（修前 `d70f7f00` / 修后本提交）

口径同上一节：vite build 整页 + 假接口，无界面 Chrome，CPU 6 倍节流，每帧取 AnimationFrame，每个动作取时间窗里最长的一帧；修前、修后交替各跑 3 遍取中位数（括号里是 3 遍原值），单位 ms。

| 场景 | 动作 | 修前 | 修后 |
|---|---|---|---|
| 电脑 鼠标拖组里的对话行 | 拿起 | 57.0（60.1 / 52.8 / 57.0） | 39.8（40.1 / 38.7 / 39.8） |
| | 拖动中（中位帧 / 最长帧） | 14.7 / 27.2 | 14.3 / 24.6 |
| | 松手 | 33.4 | 33.7 |
| | 落定 | 46.2（49.1 / 46.2 / 46.2） | 35.0（35.0 / 34.5 / 35.5） |
| 电脑 鼠标拖进已置顶再 Esc | 拿起 | 56.2 | 37.3 |
| | 拖进置顶 | 42.0（42.0 / 43.9 / 36.6） | 26.5（27.0 / 26.5 / 21.3） |
| | Esc 那一帧 | 10.0 | 10.9 |
| | Esc 后（含挪回原组那一帧，窗口内最长） | 45.7（45.7 / 47.0 / 41.4） | 23.4（24.0 / 23.4 / 23.2） |
| 电脑 键盘拖公司组头 | 空格拿起 | 68.9 | 29.4 |
| | ↓（中位帧 / 最长帧） | 12.4 / 26.8 | 11.7 / 25.2 |
| | 空格放下 | 54.6 | 40.6 |
| 手机 长按对话行（组内） | 拿起 | 58.4 | 17.4 |
| | 出长按菜单 | 45.0 | 45.4 |
| | 手指再动收菜单 | 17.1 | 16.1 |
| | 拖动中（中位帧 / 最长帧） | 15.9 / 33.1 | 16.5 / 30.0 |
| | 落定 | 46.7 | 36.3 |
| 手机 长按对话行拖进置顶放下 | 拿起 | 56.8 | 17.5 |
| | 出长按菜单 | 50.7 | 45.1 |
| | 收菜单 | 33.2 | 23.9 |
| | 拖动中（中位帧 / 最长帧） | 15.7 / 44.7 | 16.6 / 33.2 |
| | 落定 | 47.8 | 39.0 |
| 手机 长按公司组头 | 拿起 | 53.9 | 17.4 |
| | 出长按菜单 | 56.5 | 53.4 |
| | 收菜单 | 16.3 | 16.0 |
| | 拖动中（中位帧 / 最长帧） | 15.6 / 32.6 | 17.8 / 31.5 |
| | 落定 | 17.4 | 21.2 |

与上一节同量级：本轮的修正只在取消、卸下、列表重排时才走到，帧数不变。`flowLayout` 每次多读一项计算样式，用的是同一份计算样式，拖进置顶那一段的帧数与主线程总忙碌时间（265 → 160）没有变差。

取消滑回逐帧（在页面里派发 Esc；手机用 touchcancel。每帧读置顶 a1 的位置，相同的连续帧只记一次）：

- 鼠标，修前：314 → 280（下一帧直接跳过去）。
- 鼠标，修后：314 → 307 → 296 → 288 → 284 → 283 → 282 → 281 → 280，带过渡单调滑回，浮层滑回播完后再结束。
- 手机，修前：300 → 254（跳）。
- 手机，修后：300 → 290 → 276 → 264 → 260 → 257 → 256 → 255 → 254。

播报、各组先后、最后的顺序，修前、修后逐项一致（`_state`：「已取消移动…」「…放在第 4 位，共 4 项。」「已置顶…」「「曼谷物流有限公司」放在第 5 位，共 14 项。」）。

### 对照清单

- M-22、M-23、T-09、T-10「取消先滑回」：通过。已置顶里让过位的行在取消那一刻带过渡滑回，不再停在让位处，也不再最后一下跳回。
- V 组「其余各项带过渡滑开 / 滑回」：通过。另修掉修前就有的拖出已置顶时约 19px 下冲，属有意变化：只是轨迹变平滑，落点不变。
- K-03「拖动中焦点一直在被拖那一项的把手上」：通过。推送重排挪了节点也当帧接回。
- E 组与「切后台不留拦截」：通过。拖动区卸下时传感器监听全摘，之后的按键不被吞。
- 其余条目不受影响，用例全过。

### 还缺什么

- 测试机（实时源码）与 A14 真机复测；读屏实听。
- 上一节「还缺什么」里的其余各项不变（落定 35ms 没到 A14 目标、挡板悬停重算、删 `@dnd-kit/*` 依赖、Z-01）。

## 第 12 轮测试机复测修正（10-18）

状态：实现、本地检查、前端构建已做（`index.Ce3uxhzz.js`，原 `index.CfOCaTaq.js`）；**测试机复测、复刻页实测没做**（本轮不同步测试环境，开发窗口同步 lab 后交测试窗口）。基线 `22df383e`，分支 `pearnly/cc-drag-engine`，未推送、未合并。只改 `drag.tsx` 与两份测试，调用方零改动。

### 现象（测试窗口第 12 轮 D15，2/2，内置浏览器 1180×820 真按键）

1. 组头键盘拖动中换分组：Tab 到公司组头「1010」、空格拿起，鼠标打开「分组和排序」点「分组依据 → 月份」。侧栏换成按月份后，拿起的 1010 整组还浮在月份列表上（y≈369）；焦点落在 body；下一下 Tab 被吞、焦点不动，同时念「已取消移动「」。」（名字空），念完残留才消失。第一次复现按的是 Esc，同样念空名字。
2. 组头键盘拿起后，鼠标打开同一个菜单、在菜单里用键盘：→ 开子菜单正常；↓ 时被拖的组头跟着动、念「…移到第 3 位，共 4 项。」；回车选「月份」时拖动也被放下，念「已置顶「」。」，焦点到 body。服务端只收到 `view.by=month`，置顶与组顺序没写。

### 根因（按源码逐步对上两份播报）

- **换分组不卸拖动区**：Cowork 侧栏不论按什么分组都渲染同一个 `CompanyGroups`（`thread-list.tsx` 的 `ThreadListItemGroups` 只看 `kind`），里面一个 `DragArea`；`view.by` 一变只换 `sections`：`DragList id="companies"` 还挂着、items 变空，各 `CompanyGroup` 卸下（各项 `unregister`）。引擎对「被拖那一项没了」没有任何处理：`this.drag` 留着，`KeyboardSensor` 挂在 document 上收键的监听留着，浮层留在 body 下的拖动层里。审查第 2 条只处理了拖动区卸下（`mount()` 的卸下函数），这条路没走到。
- **之后那一下 Tab / Esc**：传感器照常收到 → `beforeEnd`：被拖那一项已注销、量不到节点，不播落位、当场 `dropped()` → `view(id)` 查不到说明，`dragAnnouncements` 的名字取的是「此刻登记的说明」→ 空；落点是它自己 → 念「已取消移动「」。」；随后 `release()` 卸下浮层（「念完残留才消失」）。测试窗口记的「没有 `.cc-drag-overlay`」：浮层在 `body > .cc-drag-layer` 下、不在侧栏里，复测时请在那里看。
- **菜单里的按键推着拖动走**：键盘拖动收键在 document 上（dnd-kit 时代同样如此），按在别处也算。菜单项的 ↓ 先由组件库菜单处理（React 根节点上的监听），再冒泡到 document 由拖动 `step` 挪一格 → 「移到第 3 位」。回车：组件库菜单项选中走 `dispatchDiscreteCustomEvent` → `ReactDOM.flushSync`（`@radix-ui/react-primitive`），按月份的重画在这一下按键里同步提交完，被拖那一组和瞄着的那一组都已注销；冒泡到 document 时拖动当它是「放下」→ `view(overId)` 也查不到说明，`say()` 落到最后一行念 `droppedIn` → 「已置顶「」。」；`onDrop` 按此刻（按月份）的 `companyIds = []` 算，`moveTo` 回 null，什么也没存——与「服务端只收到 view.by=month」一致。

### 改法（`components/ui/drag.tsx`）

- **拖动中各项、各列、焦点一变就核对（`verifySoon` → `verify`）**：`unregister`、`attach(null)`、`setList`、`dropList`，以及键盘拖动中的 `focusin`，都排一次核对，放在这一轮同步的事（React 这次提交）做完以后的微任务里，几处变动合成一次。同一次提交里先卸后挂的（拖进已置顶重挂、挪回原组、推送重挂）到那时已经挂好，不算消失。
  1. **被拖那一项不在任何一列里了**（`present`：登记着、节点挂着、它那一列的先后里有它）：当场取消（`vanished`）——先 `abort()` 摘掉传感器挂在页面上的监听（同拖动区卸下：鼠标、手指按着时补 `blockReleaseClick`，松开那一下不当点击），再走 `cancelled()`：`keepFocusOn`（焦点掉了才接回、别人拿到的不抢）、`finish`（`onActive(null)`、其余各项的让位与过渡清掉）、浮层当场卸下（没有原位可滑回）、念「已取消移动「名字」。」。已经放手 / 取消、在等落位或滑回的不再等（`letGo.flush()`；挪回原组那一段除外，等它重挂，`HOME_WAIT` 兜底）；`dropped()` 开头再核一次，那一项没了就按取消收尾、不交调用方。
  2. **键盘拖动只认把手上的按键**：焦点被人移到把手以外能聚焦的地方（`strayed`：不是被拖那一项此刻的把手、不是 body、节点还挂着）就当作取消，同 Esc 先滑回再取消（`stray`：`abort()` 后 `beforeCancel`），之后的按键归新焦点。两道：`focusin` 当场核对（鼠标点开菜单时组件库把焦点放进菜单内容，`@radix-ui/react-menu` 的 `onMountAutoFocus` → `contentRef.current.focus()`；侧栏各菜单都没拦这一下）；`KeyboardSensor` 收键时再问一次 `owns()`，不归拖动的这一下不 `preventDefault`、不挪、不放下，拖动取消。焦点只是掉到 body（React 挪节点）照旧按 K-03 接回把手，不算取消。鼠标、手指拖动不挂 `focusin`、不看焦点。
  3. **瞄着的、经过的那一项没了**：键盘瞄准退回它自己（K-04），浮层回到它自己的位置，当场重算落点与让位；放下时落点那一项已不在（落位途中被删）同没有落点，不交调用方。
- **播报的名字一律用拿起时记下的**（`dragAnnouncements` 的 `name` 先取 `lookup.lifted`）：拿起、经过、放下、置顶、取消各句都是；落点那一项查不到说明时同没有落点（拖动中不念、放下念取消），不再落到「已置顶」那一行。

### 有意变化

1. **键盘拖动中焦点被人移走就取消**（对照 K-03、K-08、K-09、E-02）：原来（dnd-kit 时代同样）键盘拖动在 document 上收所有按键，焦点在菜单、输入框里按的 ↑↓ 也挪拖动，空格 / 回车 / Tab 也放下，Esc 也只取消拖动。现在焦点移到把手以外能聚焦的地方那一刻就取消（同 Esc 先滑回、播报同 S-12），之后的键归新焦点（菜单里 Esc 关菜单、输入框里打字）。理由：这些键是人对新焦点按的，交给拖动会出现 D15 那种「选菜单项把拖动放下」。焦点在把手上（含掉到 body 后接回）时 K-03 至 K-12 不变。
2. **被拖那一项在拖动中消失就当场取消**：原来没有处理（浮层、监听留着，下一键才结束且念空名字）。没有原位可滑回，所以不播滑回、浮层当场卸下，与 Esc 取消（S-12 先滑回）的时机不同。
3. **瞄着的那一项消失时当场退回它自己**（K-04 原来在下一次按方向键时才从它自己接着算）：落点、让位、浮层当场回原位，念它此刻的位置（「移到第 1 位，共 N 项」）；放下不交给已经不在的落点。
4. **播报名字取拿起时的**：拖动中改了名（推送）念的是拿起时的名字；正常拖动里两者相同，各句文案与时机不变。
5. 源码断言两处随写法改：`SidebarDragTests.test_dragged_chat_rows_use_the_shared_gap_and_landing`、`panel:SortableSourceTests.test_shared_drag_rules` 锁的键盘选项那一行多了 `owns`，意图（放下、取消先交 `beforeEnd` / `beforeCancel`）不变。R-01 的行为用例期望值都没改。

### 测试

- `DragEngineTests.test_the_lifted_item_leaving_every_list_cancels_at_once`（直接驱动引擎，7 段）：① 键盘拿起公司组、↓ 一格后整列换掉（各组注销、节点卸下、列还在、先后变空，焦点掉到 body）→ 下一个微任务就取消：不在拖、`isDragging()` 为 false、浮层没了、念「cancel company:1010」、`onActive(null)`、不交调用方，之后 Tab / Esc / 空格 / ↓ / 回车都不被 `preventDefault`；② 只有被拖那一行没了 → 其余各行的让位与过渡当场清掉；③ 同一轮里先卸后挂 → 不算消失、接着拖；④ 瞄着的那一项没了 → 瞄准退回自己、让位清掉、浮层回原位、念「moved q1 1/2」，放下不交、念取消；⑤ 鼠标拖动中那一项没了（列的先后没跟着变）→ 当场取消，之后移动不拦、松开那一下不当点击、80ms 后照常；⑥ 松手落位途中落点没了 → 不交、念取消；⑦ 松手落位途中被拖那一项没了 → 不等落位播完当场收尾。
- `DragEngineTests.test_a_keyboard_drag_only_takes_keys_on_its_handle`：① 焦点移到列表外的按钮（`focusin`）→ 当场取消、让位清掉、浮层没了、念「cancel a」，之后 ↓ / 回车 / 空格 / Tab / Esc 都不被吞、不放下，焦点留在按钮上不抢；② 焦点掉到 body 不算取消、按键时接回把手（K-03），焦点在别处时按 ↓（没等到 `focusin`）→ 不吞、不挪、当场取消，再按回车不放下；③ 有动效时同 Esc：让位带过渡清掉、浮层起播滑回，滑回途中的按键也归新焦点，滑回播完才取消、过渡清掉。
- `SidebarDragTests.test_announcements_name_the_lifted_item_even_after_it_is_gone`：被拖那一项查不到说明时拿起 / 放下 / 取消都念拿起时的名字；落点也查不到时拖动中不念、放下念取消（不念「已置顶」）；拖动中改了名念拿起时的名字。
- 改前（`22df383e` 的 `drag.tsx`）3 条都失败：前两条在第一段就停在「还在拖」（`stateOf` 为 2，期望 0），第三条念「up 」（期望「up 1010 Co」）；改后 3 条都过。另逐项回退新逻辑各跑一次（变异检查），去掉 `owns` 守卫、`focusin` 监听、消失取消、瞄准退回、名字取拿起时的、落点查不到同没有落点、落点没了不交、`unregister` / `attach(null)` 的核对、`dropped()` 开头的核对，每一项都有用例失败。
- K-03 原用例 `DragEngineTests.test_keyboard_focus_comes_back_after_the_row_is_moved_by_a_redraw` 原样通过（最后一段焦点被人点到别处后按的 Esc 现在不再被吞、拖动按取消收尾，原用例没有断言这一下）。

### 本地检查

- `tsc --noEmit` 通过；`check-frontend-tokens`、`check-frontend-i18n`、`check-frontend-viewport-units` 通过；`check-architecture` 0 violations。
- `tests/test_assistant_ui.py` 225 条、`test_cc_session_panel.py` 25 条、`test_cc_ccs.py` 23 条、`test_cc_mobile_overlays.py` 26 条、`test_cc_mobile_content.py` 16 条全过。
- `npm --prefix frontend/assistant run build` 通过，产物 `apps/pearnly_core/pearnly_core/public/assistant/assets/index.Ce3uxhzz.js`（不进仓库，开发窗口同步 lab 用）。

### 对照清单

- P、I、A、B、V、X、Z：调用方零改动，接口只多了可选的 `KeyboardSensorOptions.owns`；ARIA、浮层、多选、动效、卸下（X-02，`mount()` 卸下函数原样）都不变。新增的核对只在拖动中、各项或各列真变了时排一次微任务，拖动中每一下指针移动不多做事。
- S：S-01 至 S-18 文案与时机不变，名字改取拿起时的（正常拖动里与原来同一个值）；新增「落点查不到同没有落点」。
- M、T：鼠标、手机不看焦点，M-29、T-05（长按菜单出来时焦点进菜单，拖动照旧）不受影响；只多了「被拖那一项消失就当场取消」，按着时补拦松开那一下的点击（同 M-24、T-10）。
- K：K-01、K-02、K-04 至 K-07、K-10 至 K-14 不变；K-03 焦点掉到 body 照旧接回；K-08、K-09 在焦点还在把手上时不变，焦点被人移走见有意变化 1；K-04 瞄准消失见有意变化 3。「第一张卡键盘拖动」（K-04 高矮差很大的卡）落点照旧按瞄准、不按几何；Alt+↑/↓（K-14、S-16）不在拖动中，不受影响。
- E：E-01 至 E-04 不变；焦点移走后的取消走 `ReleaseFirst.hold`（`releaseDrag()`），之后的 Esc 同 E-03 是普通 Esc（关菜单、关浮着的会话栏、清多选）。
- 拖动中切走再回来：切到后台（`visibilitychange`）照旧取消；只是窗口失焦再回来时焦点回到把手，`focusin` 的目标就是把手，不算移走。

### 还缺什么

- 测试机复测（开发窗口同步 lab 后）：D15 两条原样复现步骤；Tab 到组头空格拿起后鼠标打开「分组和排序」——打开那一下就应念「已取消移动「1010-…」。」、浮层滑回、之后菜单里 ↓ / 回车只管菜单；换成按月份后没有残留浮层（`body > .cc-drag-layer` 下没有 `.cc-drag-overlay`）、Tab 照常走焦点。另请顺手过一遍：键盘拿起对话行后点输入框（取消、打字正常）、键盘拖动中推送重排（焦点接回、接着拖）、鼠标拖进已置顶再拖回本组、拖进已置顶后 Esc、会话栏第一张卡键盘拖动、Alt+↑/↓、读屏实听。
- 复刻页实测没做（上一轮的复刻页脚本不在本机临时目录里了）。
- 上一节「还缺什么」里的其余各项不变（A14 真机复测、落定 35ms 没到 A14 目标、挡板悬停重算、Z-01）。

## 第 12 轮测试机与 A14 结果（10-18，测试窗口）

全文 `~/pearnly-devtools/test-window/round12/第12轮回报.md`（含 A 段帧测原始数据、trace、截图）。被测：lab 实时源码，CC 包 `index.CfOCaTaq.js`（9eac8080 变基后）与 D15 修正后的 `index.Ce3uxhzz.js`（bca151fc），底座 ebba5a40。

- **A14 帧测**（不开 trace 的帧间隔，3 次中位数，ms，拿起 / 出菜单 / 收菜单 / 落定）：组内对话行 11.1 / 54.4 / 23.9 / 34.0；公司组头 10.7 / 56.3 / 22.0 / 32.3；拖进已置顶 10.9 / 54.7 / 21.2 / 29.6（第 11 轮复核 58–60 / 72 / 35 / 35–39）。拿起达标（目标 30–45）；**落定仍高于目标 15–25**，出菜单是下一大头。trace 口径：拿起最长任务 16 / 19ms，出菜单 42 / 51ms（其中 JS 38 / 44ms 在菜单组件），收菜单 24 / 22ms。
- **行为**：B、C、D 段与回归全部通过——三种拖法 × 对话行 / 组头 / 置顶 / 卡片，Esc 原处松开不误开（卡片改按 6 点把手复测，卡头开关普通点击照常开合，第 11 轮遗留确认是测法问题），拖进置顶后取消平滑滑回、拖回本组无下冲，第一张卡键盘拖动，D16 推送重排焦点留在把手，第 9 条手机切后台取消，搜索中不能拖，Z-01 这次没再看到。
- **D15**（键盘拖动组头中换分组残留、菜单按键推着拖动走）：查出后修正（bca151fc，见上节），在本机复刻页用 lab 上同一份 `index.Ce3uxhzz.js`（sha 前缀 a8ee3600）真按键复测通过；真页面因 lab 停过一次、登录失效，待用户醒后补。
- **没测**：读屏实听（TalkBack 要改手机无障碍设置，用户睡了不能授权；VoiceOver 没有设备）；电脑上拖动中切标签页（内置浏览器窗格不发 visibilitychange，要真 Chrome）。
- 测试中另查出的多设备置顶顺序被旧列表覆盖不是本轮引起的（第 10、11 轮就有），另开 `pearnly/cc-pin-order-race` 修，不进本轮。
- **第 13 轮**（落定与出菜单）根因分析与改法：`~/pearnly-devtools/cc-drag-r13/分析-落定与出菜单.md`（落定慢主要是长按菜单把焦点还到被拖那一项里、往后挪时 React 重插节点引起的失焦 / 获焦与强制样式；画面落定与提交同一帧；出菜单全在菜单组件）。零行为变化的四项下一轮做，两项会改行为的（菜单晚 1–2 帧出、拖后焦点不回「···」）按「提速不能影响质量」不做。
- 合入：10-18 叠在文案整改与 F7 之后随下一批部署（A8）。

## 第 13 轮（10-18，开发窗口施工子代理）

状态：实现、本地检查、复刻页实测已做；**测试机与 A14 复测没做**（交测试窗口，量法见本节末）。分支 `pearnly/cc-drag-r13`，从 `pearnly/cc-drag-engine` `2bf3bcff` 开出，未推送、未合并。依据：`~/pearnly-devtools/cc-drag-r13/分析-落定与出菜单.md`（根因 R1–R4、M1–M2，改法 1–5）。只做报告里零行为变化的四项（1、2、3a / 3b、4），会改行为的 5a、5b 不做。调用方（`thread-list.tsx`、`projects.tsx`、`company-cards.tsx`、`session-panel.tsx`）一个字没动。

### 改法

1. **落定那次提交前把焦点拿出来、提交后原样放回**（R1，`drag.tsx` 的 `finish` / `reinserts` / `letFocusGo` / `takeFocusBack`）。
   - 条件全满足才处理：是放下（不是取消）、只拖一项、落点是同一列里的另一项且调用方会收到 `onDrop`、按这一列的先后是**往后挪**、焦点在被拖那一项的节点里、焦点不在输入框 / 文本框 / 下拉框 / 可编辑区。
   - 做法：在交给 React 的那次同步提交里，`setDragging(false)`、`onActive(null)`、`onDrop`、各项通知都排好以后、React 动页面之前 `blur()`；提交完焦点还空着、那个元素还在页面上，就 `focus({ preventScroll: true })`。React 提交时看到前后焦点都是 body，不再读祖先滚动、不再 `focus()`。
   - **选的是按方向的版本**，不是「只要焦点在里面就处理」的保守版本。理由：React 按 key 重排时，单项往后挪挪的是它自己（它的节点被重插、浏览器同步失焦，原来就有一对失焦 / 获焦），往前挪挪的是被越过的那几项、它不动（原来一个焦点事件都没有）。保守版本会给往前挪凭空多出一对 blur / focus 事件，违反「事件先后不变」；按方向的版本在两种方向上都与原来的事件一一对应。整批多选、拖进另一列（那一行提交时整行重挂）照旧交给 React。这条规则在复刻页用真的 React 核对过（见「测试」）。
2. **落定分两拍：画面先落定，提交放到这一帧画完以后**（R2，`dropped` / `settled` / `reveal`、`ReleaseFirst.next`）。
   - 第一拍（落位播完那一下，同原来结束拖动的时刻）：卸浮层；在节点上去掉 `data-drag-source`（空档亮出来，它此刻正由让位位移摆在落点上，浮层落的就是这个位置）；撤「正在拖」（`setDragging(false)`，与原来同一时刻）；念放下。
   - 第二拍（这一帧画完以后的下一个任务）：原来 `finish` 的其余部分——`onActive(null)`、`onDrop`（存顺序、整列重排）、被拖那一项重画、各项去掉让位（不带过渡）——仍是同一次同步提交，加上改法 1。
   - 只在第一拍画面就等于最终画面时分拍：有动效、落位确实播完了、落到的是空档本身（不是收着的整块、可见范围的边上）、只拖一项、还在拿起时那一列、拖动中版面没变过（`relaid`：整列滑动看到了增删、卡片变高、登记的各项变了——例如临时补出的已置顶，放下那次提交还要把它收起）、拖的不是装着别的拖动项的一整块（`holdsOthers`：侧栏公司组，它同时是组里那一列的整块落点）。其余（整批多选、落进收着的组、减少动态效果、没挪过、落位途中又按下或等太久、拖进已置顶、公司组头）照旧一拍。
   - **公司组头为什么不分拍**（报告没预见到，复刻页逐帧位置查出来的）：侧栏前 5 家公司默认展开（`lib/managed-agents/projects.ts` 的 `OPEN_FIRST`），公司组拖过第 5 家时，放下那次提交里被拖的那一家收起、补进前 5 的那一家展开，整列跳 138–184px（复刻页 P-gback-menu 两次量；修前也跳，只是与浮层卸下在同一帧）。分两拍的话中间会多出一帧「还没收起」的画面，属可见差异，所以不做。拖动引擎认不出调用方这条规则，调用方的写法又被源码用例锁住（`SidebarDragTests.test_sidebar_drag_wiring`），所以按结构认：被拖那一项里面还有别的拖动项（整块落点、组里的行）就照旧一拍。行、置顶、卡片里没有别的拖动项，提交只是换先后（逐帧量过，差 0px）。公司组头仍有改法 1、3b、4。
   - 两拍之间又按下、按键（窗口捕获阶段的 pointerdown / touchstart / keydown）当场做第二拍，再交给这一下；拖动区卸下、被拖那一项消失（`verify`）同样当场做完；最迟 `landWait` 后自己做（页面在后台）。
   - 第二拍用 `setTimeout(0)`，不用 `afterPaint`：落位的 `finished` 在浏览器画这一帧的同一个任务里兑现，第一拍就在那个任务里，排一个任务就是「这一帧画完以后」；`afterPaint` 的 requestAnimationFrame 已赶不上这一帧，复刻页实测提交会晚到再下一帧。
3. **两处引擎小开销**。
   - 3a：`dropAnimation` 不在参数里解构 `transform`，只在「没起播过、要从此刻起播」那一支才读（落定时松手那一下已经起播过，原来白读一次浮层计算样式）。
   - 3b：`ownFocus` 的把手（侧栏行、置顶行、公司组头开关）另记一份不依赖「在不在拖」的（`still`），拿起、落定时 `handle` 不换一份，被拖那一组的 `ProjectHeader`（memo）不再跟着重画。属性一个不变；卡片把手（不带 `ownFocus`）照旧被拖时多 `aria-pressed`。原来的依赖写法 `[engine, drag.id, handleRef, disabled, ownFocus, skip, isDragging]` 保留（`Round11Tests.test_review_small_fixes` 锁着它）。
4. **长按出菜单收成一遍渲染**（M1，`components/ui/menu.tsx`、`sidebar-group.tsx`；两步一起做）。
   - 4a：`menu.tsx` 给下拉菜单的弹层统一传 `container`（`document.body`，同组件库默认），菜单内容在打开那一遍就渲染，不再「先空、布局副作用 setMounted、再同步重画」。服务端渲染（测试）不给，同组件库。各处菜单都从这里取，所以整个 CC 的下拉菜单都一样。导出那一行 `export const DropdownMenu = { ...Radix, Content, SubContent };` 不变（`MenuEdgeTests` 锁着），`Radix` 换成「组件库加上这个弹层」。
   - 4b：`GroupSortMenu` 的组头偏移在打开的那一遍渲染里量（React 还没提交，版面是上一帧画好的），量到的同一遍就用上；不再在提交后的布局副作用里量完 `setOffset`、让整份菜单再渲染一遍。收起时偏移留着，同原来。`use-long-press.ts`、`projects.tsx` 不用改（锁它们写法的用例照旧）。
   - 4c（`menu.tsx` 按内容高度选上下）按报告保留。

### 没做的

- 5a（长按开菜单用可中断渲染）、5b（拖起来收菜单时不把焦点还给「···」）：会改时序或焦点终点，按任务书不做。

### 有意变化（对用户不可见、结果不变）

1. **M-19 / I-01：分两拍时浮层卸下比提交早一个任务**。卸浮层、亮出空档、撤「正在拖」、念放下仍在落位播完那一刻；`onActive(null)`、`onDrop`、各项去掉让位晚到这一帧画完以后（复刻页上就是紧接的下一帧）。为什么看不到、不影响结果：
   - 第一拍那一帧已经是最终画面：复刻页逐帧量了全部拖动项的位置，第一拍那一帧与下一帧（提交）、与 1 秒后的最终位置差都 ≤0.5px（见下表）；
   - 两拍之间的按下、按键先做完第二拍再处理，所以点按、键盘、手机抽屉、侧栏多选看到的都是提交以后的状态，同原来；
   - 「正在拖」（`isDragging()`）与原来同一时刻撤：X-01 时间窗不变（悬停滚动这类按计时器问它的不受影响）；
   - 存顺序（本机存储、服务端同步本来就去抖几秒）晚一帧，结果不变；
   - 第一拍到第二拍之间 React 若因推送重画被拖那一项，属性没变就不写，空档不会藏回去（用例锁住）。
2. **改法 1：失焦在 React 提交前、获焦在提交后**（原来：React 挪节点时浏览器同步失焦，React 在提交的变更阶段后、布局副作用前 `focus()`）。事件先后（blur → focusout → focus → focusin）、目标、焦点终点都同原来；`:focus-visible` 同原来（两条路都是先落到 body 再由脚本聚焦；复刻页 11 个场景修前修后逐一核对一致）。原来 `focus()` 不带 preventScroll，会把抽屉 / 侧栏滚到按钮可见再写回滚动（同一个任务里，画面看不到，但会多派一次 scroll 事件），现在不滚，也就没有那次 scroll 事件；本仓没有在侧栏、抽屉上监听 scroll 的（只有 `lib/viewport.ts` 的 visualViewport，拖动区自己的 scroll 监听在提交前已摘）。提交里的布局副作用、被动副作用没有读焦点的（核对过全部 `activeElement` 读取处）。
3. **4a：所有下拉菜单的内容早一遍渲染**（同一串提交里、绘制之前），DOM、位置、焦点进菜单的时机都同原来。
4. **4b：组头偏移在渲染时读**（原来在布局副作用里读）。手机外壳下「···」常显、`data-open` 不改横向版面，读到的值相同。
5. 测试：`ENGINE_DOM` 假页面补了 `blur()`（引擎现在会调它，同浏览器）；`DragEngineTests.test_release_lands_first_then_hands_over_in_one_go` 按报告改写成「卸浮层在前一拍，交调用方与清让位仍在同一下」，意图不变。其余现有用例一字没改。

### 对照清单逐条（129 条）

逐条按本轮四处改动核对：会走到改动的只有「放下」（改法 1、2、3a）、侧栏把手的记住（3b）和下拉菜单打开（4）；取消、拿起、拖动中、Alt 挪动的代码路径没动。

- **P 拖动处（P-01 至 P-10）**：不变。哪些能拖、拖到哪、`onDrop` 各分支一样；交给调用方的还是同一个闭包，分两拍时晚一帧执行（见有意变化 1）。
- **I 接口（I-01 至 I-07）**：签名、导出不变（`ReleaseFirst` 多一个 `next`，`hold` 原样）。I-01「`onDrop` 落位播完才调」照旧，分两拍时晚一帧（有意变化 1）；I-03「四样按内容记住」：`ownFocus` 的 `handle` 现在拿起、落定时也是同一份（3b），内容不变；I-06 `dropAnimation` 的参数形状不变，只是 `transform` 按需才读（3a）；I-07 `isDragging()` 的语义、撤的时刻不变。
- **A 静态 ARIA（A-01 至 A-09）**：不变。`handleOf` 的输出一样，卡片把手被拖时照旧 `aria-pressed="true"`（用例锁住），`ownFocus` 的把手照旧没有。
- **S 播报（S-01 至 S-18）**：文案、先后不变。S-09 / S-10 / S-11 放下的播报仍在「落位播完那一刻」念（分两拍时在第一拍念，提交在后，念的字在提交前就算好，同原来）；同一句不重念。
- **M 鼠标（M-01 至 M-29）**：M-14「等待期间按下就当场结束」同样覆盖两拍之间；M-15（被限位停在落点）、正常放下：行、置顶、卡片走两拍，公司组照旧一拍；M-16（没挪过）、M-17（落进收着的组）、M-18（可见范围外）照旧一拍；M-19 见有意变化 1；M-20（松手后拦点击 50ms）不涉及（第二拍在松手 200ms 以后）；M-22 至 M-25 取消路径没动（不分拍、不碰焦点）；M-29「放下后不接回焦点」：改法 1 放回的是原来就有焦点的那个元素，React 原来也是还给它，`keepFocusOn` 的规矩没变。
- **T 触屏（T-01 至 T-14）**：T-05 长按菜单出不出、何时收不变，菜单内容早一遍渲染、位置相同（4，复刻页手机组头菜单框修前修后一致）；T-07（到菜单时间后原地松手）没挪过，照旧当场结束；T-08 同 M-14 至 M-19；T-13 层级不变。
- **K 键盘（K-01 至 K-14）**：K-03 拖动中焦点不变；K-08 放下：行、置顶、卡片有落位时走两拍，公司组与「目标已滚到浮层下面、没有落位可播」的照旧一拍；K-12 放下后 5 秒的焦点接回在第一拍起算，同原来在结束拖动那一刻起算，改法 1 的失焦 / 放回在同一个任务里做完，接回的那一帧检查看不到 body；K-14 Alt 挪动不是拖动，不受影响。
- **E Esc 与其它层（E-01 至 E-04）**：E-03 两拍之间按 Esc：捕获阶段先做完第二拍，Esc 照常关会话栏、清多选（`isDragEscape` 为否，同落位期间）；其余不变。
- **B 多选（B-01 至 B-06）**：整批不分拍、不碰焦点，照旧。
- **V 视觉与动效（V-01 至 V-08）**：浮层样子、落位动画、`data-dropping` 不变；V-05「落点那一项落位播完前不显示」：两拍时它就在落位播完那一刻亮出（原来也是那一刻随提交亮出）；V-06 减少动态效果照旧一拍。
- **X 牵连模块（X-01 至 X-04）**：X-01 `isDragging()` 时间窗不变（第一拍撤）；X-02 两拍之间卸下拖动区当场做完第二拍（不碰焦点）；X-04 不变。
- **Z 已知问题（Z-01 至 Z-03）**：不涉及。
- **R 测试（R-01 至 R-03）**：R-01 行为用例期望值都没改、全过；R-02 源码断言全过（为此 3b 保留原依赖写法、4a 保留导出写法、4b 不改 `use-long-press.ts` / `projects.tsx`）；R-03 帧数口径同第 12 轮，见下。

### 测试

- 新增 `REACT_DOM`：在 `ENGINE_DOM` 假页面上跑真的 React（`react-dom/client`）——补了文本节点、`insertBefore`、节点名、祖先滚动位置、按类名找；挪动 / 卸下装着焦点的节点时同 Chrome 同步失焦。这样落定那次提交里 React 自己的焦点还原（失焦 → 不带参数的 `focus()`）能直接看到、拿来对照。`ENGINE_DOM` 补了 `blur()`（引擎现在会调，同浏览器）。
- 新增 `Round13DropTests` 7 条：
  - `test_focus_leaves_before_the_drop_commit_and_comes_back_without_react_restoring_it`（真 React）：往后挪时焦点记录是 `drop → blur → focus({preventScroll:true})`，没有挪节点引起的失焦、没有 React 的 `focus()`，终点同一个按钮；往前挪一个焦点事件都没有；焦点在输入框里照旧交给 React（失焦 → React `focus()`）；焦点不在被拖那一项里、取消，都不碰焦点。
  - `test_the_drop_paints_first_and_commits_on_the_next_frame`（真 React）：落位播完那一下浮层卸下、`data-drag-source` 去掉、念放下、`isDragging()` 为否，调用方没收到、DOM 先后与让位没变；期间 React 重画不把空档写回；下一帧才交调用方、换先后、去让位，且不再另播一段落位；两拍之间按下当场提交；落位途中按下照旧一拍。
  - `test_drops_that_change_more_than_the_gap_stay_in_one_go`（真 React）：拖动中这一列多了一项（版面变过）→ 一拍；源码核对整批、落进收着的、落位没播完、换列、整块、减少动态效果都在 `settled` 里排除。
  - `test_dragging_a_whole_block_stays_in_one_go`：被拖那一项同时是整块落点（侧栏公司组的结构）→ 一拍；同样的拖法换普通的一项 → 两拍。
  - `test_own_focus_handles_stay_the_same_while_the_drag_starts_and_ends`（真 React）：`ownFocus` 的把手拿起、落定都是同一份，memo 的组头一次也不重画；卡片把手被拖时照旧换一份、带 `aria-pressed="true"`。
  - `test_the_landing_does_not_read_the_overlay_again_once_it_has_played`：落位已起播并播完时 `dropAnimation` 不读 `transform`；没起播过的才读一次。
  - `test_long_press_menus_render_their_content_in_the_opening_pass`（真 React，组件库换成记下 props 的空壳）：手机公司组头打开菜单，菜单内容只渲染一遍、第一遍就是贴组头的 `align="start"` 与偏移 −224；弹层都直接给 `container = document.body`；收起时偏移留着；服务端渲染不给 container。
- 改写 `DragEngineTests.test_release_lands_first_then_hands_over_in_one_go`（报告点名，本轮自己的引擎用例）：「卸浮层在前一拍，交调用方与清让位仍在同一下」，意图不变。
- 修前（`2bf3bcff` 的 `drag.tsx` / `menu.tsx` / `sidebar-group.tsx`）新增 7 条与改写的那一条全部不过（6 条失败，`test_drops_that_change_more_than_the_gap_stay_in_one_go` 因源码里没有 `settled` 而报错）；修后全过。逐项回退新逻辑跑一次（变异检查）13 项都有用例失败：`relaid` 排除、整块排除、第一拍撤「正在拖」、第二拍不再卸浮层、方向判断、输入框不碰、`preventScroll`、失焦在交调用方之后、`ownFocus` 那一份、`transform` 按需读、弹层 container、渲染时量偏移、两拍整体。

### 复刻页实测（修前 `2bf3bcff` / 修后 `0b7b3385`）

- 做法：口径同第 12 轮——vite build 整页 + 假接口（3 条置顶、14 家公司 × 4 条、最近 8 条），无界面 Chrome 155，CPU 6 倍节流，帧取 trace 的 AnimationFrame（这一帧第一个任务开始 → 绘制结束），每个动作取时间窗里最长的一帧；修前、修后交替各 3 遍取中位数（括号里是 3 遍原值），单位 ms。
- 修前包 `index.-3sU3Wfh.js`（`2bf3bcff` 构建），修后包 `index.C1iXsJxj.js`。脚本放在 `~/pearnly-devtools/cc-drag-r13/replica/`：`server.mjs`（假接口）、`drive.mjs`（各场景）、`drive-card.mjs`（卡片）、`drive-tap.mjs`（松手后点按）、`inject.js`（页面里记 React 提交、焦点与滚动事件、浮层卸下的时刻、逐帧位置）、`analyze.py` / `summary.py`（出数）。
- 口径：
  - 「浮层消失那一帧」：页面里记下的浮层卸下时刻落在哪一帧。修前它就是提交那一帧。
  - 「下一帧」：修后是第二拍的提交；修前提交已在前一帧，这一帧几乎没活。
  - 「时间窗最长」：第 12 轮的「落定」口径（松手后 150ms 到 1.5 秒里最长的一帧）。
  - 「位置差」：另跑一遍，每帧读全部拖动项的位置（读版面会拖慢帧，不与帧数混跑）；浮层消失那一帧与下一帧、与 1 秒后最终位置之差的最大值。

分两拍的（对话行、会话栏卡片）：

| 场景 | 修前 浮层消失那一帧（= 提交） | 修后 浮层消失那一帧 | 修后 下一帧（提交） | 时间窗最长 修前 → 修后 | 位置差 |
|---|---|---|---|---|---|
| 电脑 鼠标拖组里的对话行 往后 | 35.3（38.1 / 35.3 / 35.3） | 17.4（14.3 / 18.4 / 17.4） | 25.8（30.6 / 25.8 / 21.1） | 35.3 → 30.1 | 0 |
| 电脑 鼠标拖组里的对话行 往前 | 32.5（32.5 / 32.8 / 30.0） | 15.9（15.9 / 17.4 / 13.9） | 17.2（21.1 / 17.2 / 16.6） | 32.5 → 20.3 | 0 |
| 手机 长按对话行出菜单再拖 往后 | 35.0（34.5 / 35.9 / 35.0） | 19.2（18.5 / 19.2 / 19.6） | 17.5（16.5 / 22.9 / 17.5） | 35.0 → 19.6 | 0 |
| 手机 长按对话行出菜单再拖 往前 | 31.5（29.9 / 33.5 / 31.5） | 19.3（17.7 / 19.3 / 19.3） | 17.8（17.8 / 18.7 / 16.0） | 31.5 → 19.3 | 0 |
| 手机 拿起就挪（不出菜单） 往后 | 31.7（37.5 / 30.1 / 31.7） | 19.3（19.3 / 19.3 / 17.7） | 15.0（20.7 / 14.3 / 15.0） | 31.7 → 22.0 | 0 |
| 手机 拿起就挪（不出菜单） 往前 | 39.9（43.0 / 39.9 / 32.6） | 27.9（27.9 / 19.4 / 28.6）（注） | 16.5（23.1 / 16.5 / 15.4） | 39.9 → 27.9 | 0 |
| 电脑 鼠标拖会话栏卡片 往后 | 28.8（28.8 / 29.2 / 28.6） | 18.4（18.4 / 19.0 / 18.0） | 14.4（16.9 / 13.1 / 14.4） | 29.2 → 20.1 | 0.2（让位取整；修前提交那一帧同样 0.2） |

注：这一格有两遍，浮层消失那一帧里还压着一个两版都有、与拖动无关的约 11ms React 调度任务（松手后约 265ms；修前它落在提交的前一帧）。没压上的那一遍是 19.4。

照旧一拍的（修后只有改法 1、3a、3b）：

| 场景 | 修前 | 修后 | 说明 |
|---|---|---|---|
| 手机 长按公司组头再拖 往后（越过第 5 家） | 48.9（48.9 / 49.4 / 46.4） | 43.3（45.9 / 43.3 / 42.9） | 焦点那一段没了；整列 184px 的跳动两版都在浮层消失那一帧（位置逐帧一致） |
| 手机 长按公司组头再拖 往前 | 30.9（30.9 / 32.1 / 30.6） | 31.1（31.1 / 31.3 / 30.6） | 往前挪本来不碰焦点 |
| 手机 拖进已置顶放下 | 37.7（39.5 / 37.7 / 37.4） | 36.5（40.1 / 36.5 / 36.4） | 换列 |
| 电脑 键盘拖公司组头（↓×3、空格放下） | 41.0（39.9 / 41.0 / 41.6） | 39.8（39.8 / 40.8 / 39.4） | 目标已滚到浮层下面、没有落位可播，两版都当场结束；焦点在开关上且看得见，原来 React 的 `focus()` 也不滚 |

出长按菜单那一帧（按住后 450ms 到开始挪之间最长的一帧）：

| 场景 | 修前 | 修后 | React 提交次数 / 重画的组件数（按住 450ms 到开始挪） |
|---|---|---|---|
| 手机 对话行（往后） | 43.1（42.7 / 43.1 / 55.1） | 42.7（47.7 / 42.5 / 42.7） | 10 / 486 → 9 / 488 |
| 手机 对话行（往前） | 43.4（46.1 / 41.9 / 43.4） | 46.3（46.3 / 50.3 / 46.0） | 同上 |
| 手机 对话行（拖进置顶） | 44.0（47.4 / 44.0 / 42.7） | 44.2（48.4 / 44.2 / 42.5） | 同上 |
| 手机 公司组头（往后） | 50.9（54.5 / 50.9 / 48.2） | 50.3（54.2 / 50.3 / 46.5） | 10 / 524 → 9 / 506 |
| 手机 公司组头（往前） | 51.2（51.2 / 55.3 / 48.4） | 50.0（50.0 / 51.8 / 45.7） | 同上 |

- 出菜单：少一遍提交（弹层不再先空一遍），组头菜单少重画 18 个组件（不再 `setOffset` 再渲染一遍）；帧数在复刻页的噪声（±4ms）以内，看不出差别。报告估的 A14 省 2–10ms 主要是强制布局与样式，复刻页的桌面 CPU 上这几样本来就便宜，要以 A14 的 M3 为准。菜单出来的位置两版逐像素相同：对话行 `[137.5, 537.9, 176×140]`，组头 `[21.7, 702.1, 208×193]`。
- 行为：11 个场景、每个修前修后各 3 遍，结束时的状态逐项一致——各组 / 各行 / 各卡先后、两个播报区的字、没有留下空档或让位位移、焦点停在哪个元素上及 `:focus-visible`、各滚动区的位置。松手以后的焦点 / 滚动事件序列，修前修后逐条相同（都是 blur → focusout → focus → focusin，目标同一个；往前挪、不出菜单的都没有）。
- 松手后紧跟着点另一行（`drive-tap.mjs`，event timing 以 8ms 为单位）：0.25 秒时 `pointerdown` 修前 120（144 / 120 / 120）、修后 136（120 / 136 / 144）；0.4 秒时修前 128（128 / 128 / 120）、修后 128（136 / 128 / 128）。两版同一档（主要是打开对话本身），看不出第二拍拖慢紧跟着的点按。
- 时间窗里主线程的总忙碌（顶层任务合计）修前修后同一档、噪声大（如电脑往后挪 94 → 116、往前 89 → 103、手机出菜单往后 59 → 61）：分两拍是把提交挪到看不见的一帧，活本身没少；省下的只有改法 1 的焦点那一段与 3a、3b。

### 本地检查

- `tsc --noEmit` 通过；`check-frontend-tokens` 通过（收紧范围内 0 违例）；`check-architecture` 0 violations；`check-copy` 新增 / 改动 / 删除都是 0（只改了注释）。
- `tests/test_assistant_ui.py` 232 条（原 225 条加新增 7 条）、`test_cc_session_panel.py` 25 条、`test_cc_ccs.py` 23 条、`test_cc_mobile_overlays.py` 26 条、`test_cc_mobile_content.py` 16 条全过。
- 全闸门 `SKIP_NPM_CI=1 gate.sh`（照 `development.yml` 逐步跑）91 步全过。
- 构建产物留在工作树：`apps/pearnly_core/pearnly_core/public/assistant/assets/index.C1iXsJxj.js`（sha256 前缀 `a6c11912`，原 `index.-3sU3Wfh.js`；不进仓库，开发窗口同步 lab 用）。复刻页量的就是这一份。

### 给测试窗口（A14）的量法

同步本轮包（见「本地检查」里的包名）到 lab 以后，按报告第六节 M1–M4，其中两处按本轮改法改口径。

- **M1（不开 trace，最要紧）**：`round12/fr.py` 原样，按方向和出不出菜单分开跑，每格 3 次。
  - 对话行：往后 `384 440`、往前 `430 374`；公司组头：往后 `676 765`、往前 `738 650`；已置顶：`384 282`。
  - 每种跑 `hold=0.9`（出菜单）和 `hold=0.35`（不出菜单；A14 上 0.35 秒开挪若菜单仍出，改成 0.28）。
  - 在 `MARK('up')` 前、松手后 1.8 秒各记一次 `document.activeElement`（`dataset.slot || className.slice(0,40) || tagName`）。
  - **口径改动**：本轮以后 fr.py 现在的 `drop`（浮层消失那一帧的帧间隔）只量到第一拍，对话行、置顶的提交在它的下一帧。请加记 `dropNext`（浮层消失那一帧的下一帧的帧间隔），两项都报。
  - 预期：对话行（往后、往前、出不出菜单）`drop` 接近一个刷新周期（11–15ms），`dropNext` 往后 ≈ 往前、都低于第 12 轮的 32–37；公司组头不分拍（见改法 2），`drop` 往后挪应比第 12 轮少焦点那一段（报告估 6–10ms）、与往前挪拉到同一档；拖进已置顶不分拍，与第 12 轮持平或略好。松手前后焦点是同一个元素（出菜单的是 `aui_thread-list-item-more` / `cc-group-more`）。
- **M4（逐帧位置，另跑一遍：读版面会拖慢帧，不与 M1 混）**：fr.py 的逐帧记录里每帧加记所有 `[data-drag-id]` 的 `getBoundingClientRect().top`（1 位小数，只记高度大于 0 的）。
  - 对话行组内排序、置顶里排序、会话栏卡片：核对「浮层消失那一帧」与「下一帧」、与松手 1 秒后各项位置的差都 ≤0.5px。拖进已置顶不分拍，不用核对这一条。
  - 公司组头：往后挪越过第 5 家时会整列跳（前 5 家默认展开，修前就有），这一跳应与浮层消失在同一帧（不分拍），不应出现「浮层没了、还没跳」的一帧。
  - 另记松手后 0.25 秒、0.4 秒点另一行的 event timing（`pointerdown` / `click` 的 duration），与第 12 轮包对照。
- **M2 / M3（trace，可选）**：用 `round11b/trace-inval.py` 的分类，确认 ① 往后挪的落定里不再有挪节点引起的失焦、`restoreSelection` 逐层读滚动、不带 preventScroll 的 `focus()` 与抽屉滚动写回；② 出长按菜单少一遍渲染与提交（弹层首遍不再空、`GroupSortMenu` 不再在布局副作用里 `getBoundingClientRect` + `setOffset`）。
- **行为复测**：第 12 轮 B、C、D 段与 D15 原样过一遍；另看手机长按公司组头出的菜单左边仍贴组头开关；拖完马上点另一行照常打开；键盘拖组头 / 行放下后焦点留在原来的把手、Tab 正常；电脑上拖动的行标题悬停滚动照常。读屏实听（能授权时）。

### 还缺什么

- 测试机（实时源码 + 本轮包）与 A14 复测：M1、M4，能做的话加 M2、M3；读屏实听。
- 公司组头的落定不分拍（见改法 2）：往后挪只省焦点那一段（复刻页 48.9 → 43.3）。要再快只能动调用方（侧栏整列重画、前 5 家默认展开那一跳），不在本轮「调用方零改动」范围内，等 A14 数据再定。
- 出长按菜单那一帧在复刻页上看不出变化（结构上少一遍提交），以 A14 的 M3 为准；仍高的话剩下的就是 5a（会改时序，要拍板）。
- 5a、5b 没做（会改行为）。
- 第 12 轮「还缺什么」其余各项不变（鼠标拿起后挡板悬停重算、Z-01）。

## 第 13 轮测试机：按住 0.35 秒拖动偶发出菜单（10-18）

状态：根因已定、修好、本地检查与复刻页对照已做；**A14 复测没做**（交测试窗口，口径见本节末）。分支 `pearnly/cc-longpress-race`，从 `pearnly/cc-std-b1` `f1978271`（lab 上那条叠线的顶端）开出，未推送、未合并、未部署。修复提交 `49e9f522`。

### 现象

测试窗口 10-18 在三星 A14 上量第 13 轮（lab 包 `index.BGIXqnLZ.js` = `f1978271`）：`round13/fr13.py` 按下 → 停 0.35 秒 → 每步 6px、步间 30ms（每步等 CDP 回执）拖动，8 次里有 3 次（`row-back-h0.35-1`、`-2`，`pin-h0.35-0`）长按菜单仍在约 0.57 秒弹出、挂约 0.22 秒才收起。按住 0.9 秒（菜单先出、手指再动收菜单）是设计行为，不在本题。

### 根因（证据）

**到点的计时器先于「到点前就已经动出去」的那一下 touchmove 跑。** Chrome 把 touchmove 攒到下一帧开头才发给页面（帧开头先发排着的输入、再调动画帧回调），手指离开它自己的触摸防抖范围以前的几下干脆不发；长按的 500ms 计时器是另外的任务，到点就跑。到点时问 `isDragMoving()`，那一下还排在浏览器里，问到的是「没动」，菜单出来；紧跟着这一下发进来，共用拖动记成在拖，`onTouchMove` 里 `fired && isDragMoving()` 又把菜单收起——真机上就是「弹出、挂一会儿（菜单退场动画）、收起」。

1. **复刻页实测**（本机无界面 Chrome，同一份 `index.BGIXqnLZ.js`，A14 视口 412×915、触屏、安卓 UA；脚本同 `fr13.py`：按住 350ms、每步 6px、步间 30ms 等回执）。页面里记了触摸事件（含事件自带的产生时刻 `timeStamp`）、长按 500ms / 拿起 250ms 计时器的设 / 清 / 到点、菜单节点出现的时刻（`~/pearnly-devtools/cc-longpress-race/`，原始记录在 `results/`）。不节流 12 次，**3 次出了菜单，3 次都是同一个样子**：

   | 次 | 过 10px 那一下产生（timeStamp，按下起算） | 发给页面 | 计时器到点 | 菜单节点出现 |
   |---|---|---|---|---|
   | k=2 | 488.2ms | 516.7ms | 500.6ms | 511.5ms |
   | k=6 | 485.4ms | 515.9ms | 501.1ms | 511.2ms |
   | k=9 | 492.5ms | 532.3ms | 500.6ms | 511.5ms |

   - 6px、12px 两步页面根本没收到（第一下收到的就是 18px）：Chrome 的防抖范围以内不发。
   - 没出菜单的 9 次，这一下在 481–500ms 发进来，`onTouchMove` 当场清了计时器（记录里 `t500-clear` 紧跟在这一下后面）。
2. **测试窗口的原始数据对得上**（`round13/fr-raw.jsonl`，以按下标记为 0）：拿起那一帧 280–292ms，所以 touchstart 约在 +30–40ms 发出；开始挪的标记在 +390–410ms，按 `fr13.py` 的节奏第一步（6px，在 10px 里）约在按下后 375–410ms，第二步（12px，过 10px）再晚一个回执加 30ms，约 440–510ms，正落在 500ms 前后。出了菜单的 3 次，菜单出现那一帧在 566 / 595 / 570ms（到点 + 出菜单那一帧 42–75ms），即计时器准时到点、当时还不知道已经动了。
3. **排除的两种猜法**：
   - 「引擎在 rAF 或下一个任务里才置位」：不是。`HoldTouchSensor.move` 收到 touchmove 当场 `markDragMoved()`（同一个事件里，挂在按下的节点上，比冒泡到 React 那一份早）。
   - 「拿起后 React 那一层的 `onTouchMove` 收不到」：对话行不是。复刻页没出菜单的 9 次，计时器正是被 React 那一份 `onTouchMove`（离按下处超过 10px）清掉的。但按下的节点被挪走以后它确实收不到（触摸事件照样发给节点本身、共用拖动照样收到），原来「出菜单后再动收菜单」只靠它，也一并改掉（见改法 2）。

**生产第 12 轮（`afc615bb`）同样受影响**：`hooks/use-long-press.ts`、`lib/layers.ts` 两版逐字相同；`components/ui/drag.tsx` 第 13 轮只改了落定与菜单渲染，`HoldTouchSensor` 收 touchmove、`markDragMoved` 一行没动（`git diff afc615bb f1978271`）。第 12 轮没量过 0.35 秒这一档；第 13 轮复刻页脚本 `drive.mjs` 里已经写着「6 倍节流下 350ms 再慢慢挪，长按菜单的计时器会先到，菜单照样出来」，当时为量帧绕开了（拿起后马上挪出 12px）。

### 改法（`hooks/use-long-press.ts`、`lib/layers.ts`）

1. **到点只排到下一帧开头再定**（`nextFrame`，`requestAnimationFrame`）：那时浏览器已经先把排着的 touchmove 发进来，动过的当场作废了长按，这一帧里没人作废才出菜单。按住不动的菜单最多晚一帧（见「有意变化」）。到点后、这一帧之前松手或系统收回触摸（`touchend` / `touchcancel`）：照原来到点就出菜单处理（触摸事件按先后发，松手之前的移动都已经发进来了），松手不算点击；共用拖动这时按「到了菜单时间」原地结束，不当点按，与原来一致。
2. **「拿起后动了」由共用拖动当场告诉长按**，不在到点时回头问：`markDragMoved()` 调长按按下时挂上的 `watchDragMoved(owner, run)`——还没出菜单就作废计时器和等着的那一帧，已经出来就 `onDrag` 收起、接着拖。长按不再引 `isDragMoving()`，收菜单也不再靠 React 那一份 `onTouchMove`。松手、系统收回触摸时摘掉，之后别处的拖动（例如会话栏卡片）记下的移动不算到这一次头上；一次只有一根手指在按，只留一个。
3. 不变：拿起 250ms、菜单 500ms、10px（在滚列表 / 在拖）三个数；手指动出 10px 作废长按照旧由 `onTouchMove` 判断（不能拖的行也一样）；点按、按住不动出菜单、出菜单后再动收菜单接着拖、到菜单时间后原地松手（T-01 至 T-07）；菜单不按模态开。调用方（`thread-list.tsx`、`projects.tsx`）一个字没动。

### 有意变化

**按住不动的长按菜单晚约一帧出来。** 复刻页按住 0.9 秒各 12 次，菜单画出来的时刻（中位数，按下起算）：不节流 518.2 → 533.4ms，4 倍节流 521.0 → 538.5ms（+15–18ms，一帧左右）；出菜单 12/12、再动收起 12/12 两版一样。也量了「下一帧里 `flushSync` 同步渲染」的变体：527.4 / 533.3ms，只省约 5ms，要改菜单的渲染方式（同步提交、被动副作用提前到这一帧里），没采用。

### 测试

- 新增 `Round13LongPressRaceTests`（`tests/test_assistant_ui.py`）：真的 `useLongPress` + 真的 `HoldTouchSensor`，按浏览器的先后手动发事件、走帧（帧开头先发排着的 touchmove，再调动画帧回调）。
  - `test_a_move_made_before_the_menu_time_cancels_it_even_when_it_reaches_the_page_after_the_timer`：拿起后 6px 一步，到点前动出 12px 的那一下排着、500ms 计时器先跑、下一帧才发进来——菜单不出也不用收（`[0, 0]`）；不能拖的行到点前已经在滚，同样不出。**改前失败**：`[1, 1]`（出菜单又收起，就是真机现象）。
  - `test_holding_still_still_opens_the_menu_and_moving_afterwards_still_closes_it`：按住不动（只抖 4px）到点后的下一帧出菜单、再动收起（只收一次）、接着拖、照常放下；React 那一份 `onTouchMove` / `onTouchEnd` 收不到时同样收起；到点后、下一帧之前松手照旧出菜单、松手不算点击、拖动原地结束，那一帧不再出第二次；松手后别处拖动记下的移动不收留着的菜单；没到点松手是点按。**改前失败**：React 收不到时菜单不收（`closed` 为 0）。
- 改了一处锁写法的断言：`SidebarDragTests.test_sidebar_drag_wiring` 原来锁 `if (isDragMoving()) return;`，换成锁新的两处（`watchDragMoved(owner, () => {`、`frame.current = nextFrame(open);`、长按里不再有 `isDragMoving`、`markDragMoved` 里调 `watcher?.run()`），意图同一条（拿起后动了就不出、已经出来的收起）。其余现有用例一字没改，`test_long_press_menu_opens_only_while_the_lifted_row_stays_still`、`test_touch_hold_lifts_at_250ms_and_moving_wins_over_the_long_press_menu`、`LongPressTests.test_long_press_blocks_the_system_menu_only_for_fingers` 照旧通过。

### 复刻页对照（改前 `f1978271` / 改后 `49e9f522`，同一驱动，各 12 次）

| 场景 | 改前 | 改后 |
|---|---|---|
| 不节流，按住 0.35 秒每步 6px | 出菜单 3 次（3 次都是过 10px 那一下到点前产生、到点后才发进来） | 0 次（碰上 1 次同样的情况：496.9ms 产生、514.5ms 发进来、计时器 501ms 到点，没出） |
| 4 倍节流，同上 | 0 次（这一组没碰上到点前产生、到点后发进来的） | 0 次（碰上 1 次：488.2ms 产生、515.3ms 发进来，没出） |
| 按住 0.9 秒再拖（各 5 次 × 2 种节流，另 12 次量画出来的时刻） | 都出菜单、再动都收起 | 同左（晚约一帧，见上） |

### 本地检查

- `tsc --noEmit` 通过；`check-frontend-tokens` 通过（收紧范围内 0 违例）；`check-architecture` 0 violations；`check-copy` 新增 / 改动 / 删除都是 0（只改了注释）。
- `tests/test_assistant_ui.py` 239 条（含新增 2 条）、`test_cc_session_panel.py` 25 条、`test_cc_mobile_overlays.py` 26 条、`test_cc_mobile_content.py` 16 条全过。
- 全闸门 `SKIP_NPM_CI=1 gate.sh`（照 `development.yml` 逐步跑）92 步全过。
- 构建产物留在工作树：`apps/pearnly_core/pearnly_core/public/assistant/assets/index.CPFrZKVu.js`（sha256 前缀 `2f0a905b`，原 `index.BGIXqnLZ.js`；不进仓库，开发窗口同步 lab 用）。复刻页「改后」量的就是这一份（逐字节相同）。

### 给测试窗口（A14）的复测口径

lab 同步本分支的包以后（包名见「本地检查」）：

- `round13/fr13.py` 原样，`hold=0.35`，对话行往后 `384 440` / 往前 `430 374` 各 5 次（共 10 次）：**菜单 0 次出现**（逐帧记录里 `menu` 一直是 0）。能多跑的话 `hold=0.4`、`0.45` 各 3 次，同样 0 次。
- `hold=0.9` 对话行往后、往前各 3 次：照旧出菜单（约 0.55–0.6 秒，比第 13 轮晚一帧左右属预期），手指动起来收菜单、接着拖、照常放下。
- 手动：按住不动约 0.6 秒松手——菜单出来、留着，对话不打开；按住不到 0.5 秒松手——当点按打开对话；按住 0.35 秒开始拖进已置顶——菜单不出。
- 不能代替：真手指的起手速度各人不同，量的是脚本节奏；读屏不涉及。

### 还缺什么

- A14 复测（上面的口径）；通过后随这条线一起按主线集成合入。
- 本节只修这一处时序；第 13 轮「还缺什么」其余各项不变。
