// CC 画面比对的环境与场景表（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
// 环境 7 种 × 浅深色 = 14 组；场景按布局分「宽」（≥768，web / desktop 及其触屏版）与「窄」（<768，phone / 平板竖屏 / 桌面版拉窄，
// 变体判定窄屏优先成 phone）。每个场景：打开哪个地址、预置哪些本机偏好、做哪些交互；动作找不到元素时记进报告（「动作没做完」），
// 两次运行照样可比，覆盖缺口在报告里看得见。动作「成功」不等于做到了：带 expect 的场景在动作做完后核对那几个元素看得见，
// 没有同样记进「动作没做完」。动作可以按布局分（both(宽, 窄)），宽屏触屏环境（web-coarse / desktop-coarse）另给 touch 一份
// （行尾「···」在没有悬停的设备上不接点按，真实入口是长按行）。
//
// 动作：
//   ["hover", 选择器, 第几个]          鼠标移上去
//   ["click", 选择器, 第几个, 修饰键?] 触屏环境用触摸点按，其余用鼠标；第几个 = -1 表示最后一个
//   ["longpress", 选择器, 第几个]      按住 700ms（手机长按菜单）
//   ["press", 选择器, 第几个, dx, dy]  按下后挪动但不松开（拖动中、拖分隔条中的静态帧）
//   ["drag", 选择器, 第几个, dx, dy]   按下、挪动、松开
//   ["type", 选择器, 文字]             聚焦后输入
//   ["paste", 选择器, 文件名]          往输入框粘贴一张图（输入框粘贴即加附件；640×400 两色块，页面里用 canvas 画）
//   ["key", 键名]                      Escape / Enter …
//   ["attr", 名, 值]                   给 <html> 设属性（值为 null 删除）：桌面版拖文件进窗口、键盘弹出等
//   ["scroll", 选择器, 像素]           把某个滚动容器滚到指定位置（负数 = 离底部多少）
//   ["reload"]                         刷新（看记住的宽度能否恢复）
//   ["hold", 接口方法名, 毫秒]         让假服务端把这个接口挂起一段时间（加载骨架）
//   ["wait", 毫秒]

const PHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1";
const IPAD_UA = "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1";

export const ENVS = {
  web: { width: 1280, height: 800, dpr: 2, layout: "wide" },
  desktop: { width: 1280, height: 800, dpr: 2, layout: "wide", qt: true },
  phone: { width: 390, height: 844, dpr: 3, layout: "narrow", touch: true, mobile: true, ua: PHONE_UA },
  "web-coarse": { width: 1280, height: 800, dpr: 2, layout: "wide", touch: true },
  "desktop-coarse": { width: 1280, height: 800, dpr: 2, layout: "wide", touch: true, qt: true },
  "tablet-portrait": { width: 744, height: 1133, dpr: 2, layout: "narrow", touch: true, mobile: true, ua: IPAD_UA },
  "desktop-narrow": { width: 700, height: 800, dpr: 2, layout: "narrow", qt: true },
};

export const SCHEMES = ["light", "dark"];

// 选择器（与第 11 轮按钮排查用过的一致）
const ROW = '[data-slot="aui_thread-list-item"]';
const ROW_MORE = '[data-slot="aui_thread-list-item-more"]';
const SETTINGS_WIDE = [["click", '[data-slot="sidebar-footer"] .cc-icon-btn', 0]];
const DRAWER = [["click", ".cc-mtopbar .cc-round-btn", 0]];
const SETTINGS_NARROW = [...DRAWER, ["click", ".cc-avatar", 0], ["click", ".cc-menu-item", 0]];
const FILE_CARD = ".cc-card-hover > button";

const S = (name, path, actions = [], extra = {}) => ({ name, path, actions, ...extra });
const both = (wide, narrow, touch) => ({ wide, narrow, ...(touch ? { touch } : {}) });
const MENU = '[role="menu"]';
const DIALOG = '[role="dialog"], [role="alertdialog"]';
const SEARCH = '[data-slot="aui_thread-list-search"] input';
// 结果面板的 PDF：浏览器自带的 PDF 查看器（不是我们的样式）在无界面 Chrome 里出图不稳，统一走页图（触屏的那条路）
const NO_PDF_VIEWER = "Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled', { get: () => false });";

// 只在这个场景里换掉某几个接口的回复（页面里包一层 fetch，假服务端不变）：[方法名, 回复（null = 失败 500）, 查询串里要带的一段（可选）]
const FAKE = (...rules) => `(() => { const rules = ${JSON.stringify(rules)}; const real = window.fetch.bind(window);
  window.fetch = (input, init) => { const url = String(typeof input === "string" ? input : input.url);
    for (const [m, body, when] of rules) {
      const at = url.indexOf("/api/method/" + m);
      if (at < 0 || !/^($|\\?)/.test(url.slice(at + 12 + m.length)) || (when && !url.includes(when))) continue;
      return Promise.resolve(body === null ? new Response("{}", { status: 500, headers: { "content-type": "application/json" } })
        : new Response(JSON.stringify({ message: body }), { headers: { "content-type": "application/json" } }));
    }
    return real(input, init); }; })();`;
// CC 桌面版的本机状态（设置「此电脑」两页有东西可看：公司一行、Express 文件夹、以前授权的文件夹、开关、版本、日志）。
// 只在有桌面版桥（qt 桩）的环境里换；浏览器环境不碰（不能凭空造出 tomoDesktop，否则变体会变成桌面版）
const DESKTOP_STATE = `if (window.tomoDesktop) { const s = ${JSON.stringify({
  window: { frameless: true, maximized: false }, connected: true, version: "1.7.0", site: "", express_roots: ["D:\\Express"],
  members: [{ id: "M1", name: "Express", kind: "express", paused: false, status: "online", folders: null }],
  autostart: true, keep_awake: true, keep_awake_on_battery: false, hotkey: true, update: { state: "found", version: "1.7.1" },
  features: ["company_folders"], legacy_roots: [{ member: "M1", id: "R1", path: "D:\\Old" }],
})}; window.tomoDesktop.state = (cb) => cb(JSON.stringify(s));
  // 「此电脑 · 连接」公司一行写的是站点（没连时 = location.host）：假服务端的端口每遍不同，字宽跟着变；页面里把它换成固定的 cc.test
  const pin = (root) => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) if (n.data.includes(location.host)) n.data = n.data.split(location.host).join("cc.test"); };
  new MutationObserver(() => pin(document.body)).observe(document, { childList: true, subtree: true, characterData: true }); }`;
// 池里的两台电脑（设置「连接」/「此电脑 → 连接」下面的电脑列表）：一台在线可移除，一台离线、自动更新被 Windows 拦下
const COMPUTERS = FAKE(["pearnly_cowork.folder_intake.api.computers", [
  { adapter: "express", device: "OFFICE-PC", online: true, version: "1.7.0", folders: [], id: "D1", can_remove: true },
  { adapter: "mrerp", device: "LAPTOP", online: false, version: "1.6.0", folders: [], update_blocked: { version: "1.7.0", at: "2026-10-17T11:18:00Z" } },
]]);
const RUNS = FAKE(["pearnly_cowork.folder_intake.api.runs", { runs: [
  { started_at: "2026-10-17 08:00:00", trigger: "schedule", found: 5, new_files: 3, skipped: 2, upload_failed: 0, results: { entered: 3, attention: 1 }, states: { pushing: 1 } },
  { started_at: "2026-10-16 08:00:00", trigger: "manual", note: "folder_missing", found: 0, new_files: 0, skipped: 0, upload_failed: 0 },
] }]);
const CHOICES = FAKE(["pearnly_cowork.push.application.api.choices", {
  needed: [{ kind: "account", subject: "MEA", label: "MEA", suggested: [{ code: "5100" }], options: [{ code: "5100", name: "ค่าไฟฟ้า" }, { code: "5200", name: "ค่าน้ำ" }] }],
  options: {},
}]);
const TASK_ROW = "[data-task]";

export const SCENES = [
  // ── 外壳与对话 ──
  S("welcome", "/assistant"),
  S("chat-pending", "/assistant?c=sesn_a1"),
  S("chat-answered", "/assistant?c=sesn_a1", [["click", ".cc-card-btn[data-primary]", 0], ["wait", 600]]),
  S("chat-steps-open", "/assistant?c=sesn_a1", [["click", ".cc-steps-summary", 0]]),
  S("chat-rich", "/assistant?c=sesn_rich"),
  S("chat-rich-bottom", "/assistant?c=sesn_rich", [["scroll", ".aui-thread-viewport, [data-slot=aui_thread-viewport]", -1]]),
  S("chat-error", "/assistant?c=sesn_err"),
  S("chat-running", "/assistant?c=sesn_a2"),
  S("chat-scroll-btn", "/assistant?c=sesn_rich", [["scroll", ".aui-thread-viewport, [data-slot=aui_thread-viewport]", 0], ["wait", 400]]),
  S("chat-reply-hover", "/assistant?c=sesn_a3", [["hover", ".aui-assistant-message-root, [data-role=assistant]", 0]], { layouts: ["wide"] }),
  S("chat-user-hover", "/assistant?c=sesn_a3", [["hover", ".aui-user-message-root, [data-role=user]", 0]], { layouts: ["wide"] }),
  S("composer-typed", "/assistant?c=sesn_r1", [["type", "textarea", "你好，帮我看一下九月"]]),
  // 「＋」直接调起系统选文件（没有菜单）：这个场景看的是按下后的按钮状态
  S("attach-menu", "/assistant?c=sesn_r1", [["click", ".cc-attach", 0]]),
  // 月份胶囊：电脑在输入框下巴里；手机没有下巴（用户 10-17：基础信息放上面），已有对话的月份在右上「…」的「处理月份」里
  // （窄屏的子菜单在原菜单里换页：components/ui/menu.tsx 的 data-slot="cc-menu-sub"）
  S("chin-month", "/assistant?c=sesn_a1", both([["click", ".cc-chin-btn", 1]],
    [["click", ".cc-mpill button", 1], ["click", '[data-slot="cc-menu-sub"]', 0], ["wait", 500]]), { expect: MENU }),
  S("tooltip", "/assistant?c=sesn_a1", [["hover", ".cc-titlebar .cc-icon-btn, .cc-topbar .cc-icon-btn, .cc-topbar .cc-row-btn", 0], ["wait", 700]], { layouts: ["wide"] }),
  // 组件库的提示框（components/ui/tooltip.tsx）：上面那个场景悬停的是带 title 的按钮（浏览器自带提示，不出图）；代码块「复制」是组件库提示框
  S("tooltip-copy", "/assistant?c=sesn_rich", [["hover", ".aui-code-header-root button", 0], ["wait", 500]],
    { layouts: ["wide"], expect: '[data-slot="tooltip-content"]' }),

  // ── 侧栏 ──
  S("sidebar-row-hover", "/assistant?c=sesn_a1", [["hover", ROW, 1]], { layouts: ["wide"] }),
  S("sidebar-row-menu", "/assistant?c=sesn_a1", both([["hover", ROW, 1], ["click", ROW_MORE, 1]], [], [["longpress", ROW, 1]]), { layouts: ["wide"], expect: MENU }),
  S("account-submenu", "/assistant?c=sesn_a1", [["click", '[data-slot="sidebar-footer"] .cc-nav-item', 0], ["click", '[role="menuitem"][aria-haspopup="menu"]', 0], ["wait", 500]], { layouts: ["wide"], expect: MENU }),
  S("sidebar-group-menu", "/assistant?c=sesn_a1", [["hover", ".cc-project-row", 0], ["click", ".cc-group-more", 0]], { layouts: ["wide"], expect: MENU }),
  S("sidebar-search-empty", "/assistant?c=sesn_a1", both([["type", SEARCH, "查不到的词zz"]],
    [...DRAWER, ["click", ".cc-drawer-title ~ .cc-round-btn", 0], ["type", SEARCH, "查不到的词zz"]])),
  S("sidebar-multiselect", "/assistant?c=sesn_a1", [["click", ROW, 1, "meta"], ["click", ROW, 2, "meta"]], { layouts: ["wide"] }),
  S("sidebar-drag", "/assistant?c=sesn_a1", [["press", ROW, 2, 0, 60]], { layouts: ["wide"] }),
  S("split-resize", "/assistant?c=sesn_a1", [["drag", ".cc-split", 0, 60, 0], ["reload"]], { layouts: ["wide"] }),
  S("split-resizing", "/assistant?c=sesn_a1", [["attr", "data-cc-resizing", ""]], { layouts: ["wide"] }),
  // 收起侧栏（components/ui/sidebar.tsx 的 offcanvas：侧栏移到屏外、占位宽 0）：网页点侧栏头的收起钮，CC 桌面版点侧栏标题栏里的开关；
  // 收起后对话区左上角出展开钮（cc-nav-toggle）
  S("sidebar-collapsed", "/assistant?c=sesn_a1", [["click", '[data-slot="sidebar"] [data-cc-nav-fallback]', 0], ["wait", 300]],
    { layouts: ["wide"], expect: '[data-slot="cc-nav-toggle"]' }),
  S("account-menu", "/assistant?c=sesn_a1", [["click", '[data-slot="sidebar-footer"] .cc-nav-item', 0]], { layouts: ["wide"], expect: MENU }),
  S("titlebar-menu", "/assistant?c=sesn_a1", [["click", ".cc-titlebar .cc-icon-btn", 0]], { layouts: ["wide"], envs: ["desktop", "desktop-coarse"], expect: MENU }),

  // ── 手机 ──
  S("drawer", "/assistant?c=sesn_a1", DRAWER, { layouts: ["narrow"] }),
  S("drawer-group-menu", "/assistant?c=sesn_a1", [...DRAWER, ["click", ".cc-group-more", 1]], { layouts: ["narrow"], expect: MENU }),
  S("drawer-account", "/assistant?c=sesn_a1", [...DRAWER, ["click", ".cc-avatar", 0]], { layouts: ["narrow"], expect: MENU }),
  S("drawer-longpress", "/assistant?c=sesn_a1", [...DRAWER, ["longpress", ROW, 1]], { layouts: ["narrow"], expect: MENU }),
  S("pill-menu", "/assistant?c=sesn_a1", [["click", ".cc-mpill button", 1]], { layouts: ["narrow"], expect: MENU }),
  S("keyboard", "/assistant?c=sesn_r1", [["type", "textarea", "你好"], ["attr", "data-cc-keyboard", ""]], { layouts: ["narrow"] }),

  // ── 面板 ──
  S("results-sheet", "/assistant?c=sesn_rich", [["click", FILE_CARD, 0], ["wait", 600]]),
  S("results-doc", "/assistant?c=sesn_rich", [["click", FILE_CARD, 1], ["wait", 600]]),
  S("results-html", "/assistant?c=sesn_rich", [["click", FILE_CARD, 2], ["wait", 600], ["click", '[role="tab"]', 1]]),
  S("results-pdf", "/assistant?c=sesn_rich", [["click", FILE_CARD, 3], ["wait", 600]], { init: NO_PDF_VIEWER, expect: '[data-slot="cc-pdf-pages"]' }),
  // 宽屏：会话栏停靠时默认开着，这里点对话名旁的「···」（网页在 .cc-topbar、CC 桌面版在自画标题栏 .cc-titlebar 里；
  // 标题栏里第一颗 .cc-row-btn 是侧栏收起钮，按 aria-haspopup 找）；窄屏：右上「…」→ 公司名，会话栏盖满屏
  S("session-panel", "/assistant?c=sesn_a1", both([["click", ':is(.cc-topbar, .cc-titlebar) .cc-row-btn[aria-haspopup="menu"]', 0]],
    [["click", ".cc-mpill button", 1], ["click", ".cc-menu-item", 0]]), { expect: `${MENU}, .cc-panel[data-mode="phone"]` }),
  S("company-panel", "/assistant?company=C-0001", [["click", ".cc-page-head .cc-icon-btn", 0]]),

  // ── 页面 ──
  S("pending", "/assistant?view=pending"),
  S("tasks", "/assistant?view=tasks"),
  S("tasks-new", "/assistant?view=tasks", [["click", ".cc-page-head .cc-btn-sm[data-primary]", 0], ["wait", 500]]),
  S("tasks-confirm", "/assistant?view=tasks", [["click", '[data-task] [aria-haspopup="menu"]', 0], ["click", '[role="menuitem"][data-danger]', 0], ["wait", 400]], { expect: DIALOG }),
  S("tasks-runs", "/assistant?view=tasks", [["click", `${TASK_ROW} [aria-haspopup="menu"]`, 0], ["click", '[role="menuitem"]', 1], ["wait", 400]], { init: RUNS, expect: DIALOG }),
  // 「立即运行」后底部的提示条：它 2.6 秒后自己消失，比拍一张的时间短，这个场景里不让 2600ms 的定时器走（只这一个时长）
  S("tasks-toast", "/assistant?view=tasks", [["click", `${TASK_ROW} button[title]`, 0], ["wait", 300]],
    { init: "{ const real = window.setTimeout; window.setTimeout = (fn, ms, ...a) => (ms === 2600 ? 0 : real(fn, ms, ...a)); }" }),
  S("tasks-loading", "/assistant?view=tasks", [], { before: [["hold", "pearnly_cowork.folder_intake.api.listing", 20000]] }),
  // 取列表失败：出错框 + 空状态卡
  S("tasks-error", "/assistant?view=tasks", [], { init: FAKE(["pearnly_cowork.folder_intake.api.listing", null]) }),
  S("companies", "/assistant?view=companies"),
  S("companies-loading", "/assistant?view=companies", [], { before: [["hold", "pearnly_cowork.companies.api.companies", 20000]] }),
  S("companies-error", "/assistant?view=companies", [], { init: FAKE(["pearnly_cowork.companies.api.companies", null]) }),
  S("companies-search-empty", "/assistant?view=companies", [["type", ".cc-page-search input", "zz"], ["wait", 600]],
    { init: FAKE(["pearnly_cowork.companies.api.companies", [], "search="]) }),
  S("pending-loading", "/assistant?view=pending", [], { before: [["hold", "pearnly_cowork.companies.api.pending", 20000]] }),
  S("pending-error", "/assistant?view=pending", [], { init: FAKE(["pearnly_cowork.companies.api.pending", null]) }),
  S("pending-search-empty", "/assistant?view=pending", [["type", ".cc-page-search input", "zz"]]),
  // 能当场选的那一张点开：一个下拉（建议项预选）+「确认」
  S("pending-open", "/assistant?view=pending", [["click", '[data-slot="cc-pending-choose"] .cc-pending-toggle', 0], ["wait", 400]],
    { init: CHOICES, expect: '[data-slot="cc-pending-choose"] select' }),
  S("company", "/assistant?company=C-0001"),
  S("add-company", "/assistant?view=companies", [["click", ".cc-page-head .cc-btn-sm[data-primary]", 0], ["wait", 500]]),
  // 知识库编辑：公司页的知识库卡点「+」出编辑框（卡头的按钮在卡里第一个；能悬停的设备上悬停卡头才显示，窄屏先打开公司栏）
  S("notes-edit", "/assistant?company=C-0001", both(
    [["hover", '[data-slot="cc-company-notes"] .cc-panel-card-head', 0], ["click", '[data-slot="cc-company-notes"] .cc-panel-btn', 0], ["wait", 400]],
    [["click", ".cc-page-head .cc-icon-btn", 0], ["click", '[data-slot="cc-company-notes"] .cc-panel-btn', 0], ["wait", 400]]),
  { expect: '[data-slot="cc-company-notes"] textarea' }),

  // ── 弹窗 ──
  S("delete-confirm", "/assistant?c=sesn_a1", both(
    [["hover", ROW, 1], ["click", ROW_MORE, 1], ["click", '[role="menuitem"]', -1], ["wait", 400]],
    [...DRAWER, ["longpress", ROW, 1], ["click", '[role="menuitem"]', -1], ["wait", 400]],
    [["longpress", ROW, 1], ["click", '[role="menuitem"]', -1], ["wait", 400]]), { expect: DIALOG }),
  S("settings", "/assistant?c=sesn_a1", both(SETTINGS_WIDE, SETTINGS_NARROW), { expect: ".cc-modal" }),
  // 图片预览：输入框选一张图，点附件出预览
  S("image-preview", "/assistant?c=sesn_r1", [["paste", "textarea", "photo.png"], ["click", ".aui-attachment-preview-trigger", 0], ["wait", 500]],
    { expect: ".aui-attachment-preview-dialog-content" }),
  // 「连接」（浏览器里：池里没有电脑时「下载 CC 桌面版」）/「此电脑 → 连接」（桌面版）：手机设置弹窗里按钮的点按区 44（按钮排查 H27）
  S("settings-tab2", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 1]], [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 1]]),
    { init: DESKTOP_STATE, expect: '.cc-modal main :is([data-slot="button"], .cc-btn)' }),
  S("settings-tab3", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 2]], [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 2]]),
    { envs: ["desktop", "desktop-coarse", "desktop-narrow"], init: DESKTOP_STATE, expect: '.cc-modal main [role="switch"]' }),
  // 池里有电脑：一台一行（状态点、「···」），再打开第一个「···」菜单（桌面版是本机公司那一行的，浏览器是第一台电脑的）
  S("settings-computers", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 1], ["wait", 300]],
    [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 1], ["wait", 300]]), { init: DESKTOP_STATE + COMPUTERS }),
  S("settings-computer-menu", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 1], ["wait", 300], ["click", '.cc-modal main [aria-haspopup="menu"]', 0]],
    [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 1], ["wait", 300], ["click", '.cc-modal main [aria-haspopup="menu"]', 0]]),
    { init: DESKTOP_STATE + COMPUTERS, expect: MENU }),

  // ── 其他 ──
  S("loading", "/assistant?c=sesn_a1", [], { before: [["hold", "pearnly_core.assistant.api.listing", 20000], ["hold", "pearnly_core.assistant.api.events", 20000]] }),

  // ── 根节点状态 ──
  S("width-medium", "/assistant?c=sesn_rich", [], { storage: { "cc.width": "medium" } }),
  S("width-wide", "/assistant?c=sesn_rich", [], { storage: { "cc.width": "wide" } }),
  S("font-sans", "/assistant?c=sesn_rich", [], { storage: { "cc.font": "sans" } }),
  S("text-small", "/assistant?c=sesn_rich", [], { storage: { "tomo-text-size": "small" } }),
  S("text-large", "/assistant?c=sesn_rich", [], { storage: { "tomo-text-size": "large" } }),
  // 减少动效：不做动画归零，报告里记下截图前还在跑的动画（转圈要照常转）
  S("motion-reduce", "/assistant?c=sesn_a2", [], { storage: { "cc.motion": "reduce" }, motion: "reduce" }),
  S("dropping", "/assistant?c=sesn_a1", [["attr", "data-cc-dropping", ""]], { envs: ["desktop", "desktop-coarse", "desktop-narrow"] }),
  S("drag-bulk", "/assistant?c=sesn_a1", [["attr", "data-cc-drag-bulk", ""]], { layouts: ["wide"] }),

  // ── 泰文（界面主语言），浅色 ──
  S("th-welcome", "/assistant", [], { lang: "th", schemes: ["light"] }),
  S("th-chat", "/assistant?c=sesn_rich", [], { lang: "th", schemes: ["light"] }),
  S("th-sidebar", "/assistant?c=sesn_a1", both([["hover", ROW, 1]], DRAWER), { lang: "th", schemes: ["light"] }),
  S("th-settings", "/assistant?c=sesn_a1", both(SETTINGS_WIDE, SETTINGS_NARROW), { lang: "th", schemes: ["light"] }),
  S("th-tasks", "/assistant?view=tasks", [], { lang: "th", schemes: ["light"] }),

  // ── 生产页的加载方式（assistant.html 动态插入 <link> 与 module <script>） ──
  S("boot-page-chat", "/assistant?c=sesn_a1", [], { boot: "page" }),
  S("boot-page-menu", "/assistant?c=sesn_a1", both([["hover", ROW, 1], ["click", ROW_MORE, 1]], [["click", ".cc-mpill button", 1]], [["longpress", ROW, 1]]),
    { boot: "page", expect: MENU }),
];

// 小数宽度：变体判定与 max-md 要在同一处分界。CDP 的视口宽度只收整数：frame = 2 表示外层 1535 宽、像素比 1 的页面里放一个
// zoom: 2、宽 767.5px 的同源 iframe，iframe 里的布局宽度正好 767.5、像素比 2（shoot.mjs 读回 matchMedia 核对）
export const EXTRA_ENVS = {
  "web-767.5": { width: 767.5, height: 800, dpr: 2, layout: "narrow", frame: 2 },
};

/** 某环境 × 深浅色要跑的场景（动作已按布局展开）。 */
export function scenesFor(envName, scheme) {
  const env = ENVS[envName] ?? EXTRA_ENVS[envName];
  if (!env) throw new Error(`没有这个环境：${envName}`);
  const out = [];
  for (const scene of SCENES) {
    if (scene.layouts && !scene.layouts.includes(env.layout)) continue;
    if (scene.envs && !scene.envs.includes(envName)) continue;
    if (scene.schemes && !scene.schemes.includes(scheme)) continue;
    const key = env.layout === "wide" && env.touch && scene.actions.touch ? "touch" : env.layout;
    const actions = Array.isArray(scene.actions) ? scene.actions : scene.actions[key];
    out.push({ ...scene, actions });
  }
  if (EXTRA_ENVS[envName]) return out.filter((s) => ["welcome", "chat-pending", "settings"].includes(s.name));
  return out;
}
