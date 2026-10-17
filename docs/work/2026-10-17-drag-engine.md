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
