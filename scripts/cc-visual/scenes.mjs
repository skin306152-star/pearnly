// CC 画面比对的环境与场景表（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
// 环境 7 种 × 浅深色 = 14 组；场景按布局分「宽」（≥768，web / desktop 及其触屏版）与「窄」（<768，phone / 平板竖屏 / 桌面版拉窄，
// 变体判定窄屏优先成 phone）。每个场景：打开哪个地址、预置哪些本机偏好、做哪些交互；动作找不到元素时记进报告（「动作没做完」），
// 两次运行照样可比，覆盖缺口在报告里看得见。
//
// 动作：
//   ["hover", 选择器, 第几个]          鼠标移上去
//   ["click", 选择器, 第几个, 修饰键?] 触屏环境用触摸点按，其余用鼠标；第几个 = -1 表示最后一个
//   ["longpress", 选择器, 第几个]      按住 700ms（手机长按菜单）
//   ["press", 选择器, 第几个, dx, dy]  按下后挪动但不松开（拖动中、拖分隔条中的静态帧）
//   ["drag", 选择器, 第几个, dx, dy]   按下、挪动、松开
//   ["type", 选择器, 文字]             聚焦后输入
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
const both = (wide, narrow) => ({ wide, narrow });

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
  S("attach-menu", "/assistant?c=sesn_r1", [["click", ".cc-attach", 0]]),
  S("chin-month", "/assistant?c=sesn_a1", [["click", ".cc-chin-btn", 1]]),
  S("tooltip", "/assistant?c=sesn_a1", [["hover", ".cc-titlebar .cc-icon-btn, .cc-topbar .cc-icon-btn, .cc-topbar .cc-row-btn", 0], ["wait", 700]], { layouts: ["wide"] }),

  // ── 侧栏 ──
  S("sidebar-row-hover", "/assistant?c=sesn_a1", [["hover", ROW, 1]], { layouts: ["wide"] }),
  S("sidebar-row-menu", "/assistant?c=sesn_a1", [["hover", ROW, 1], ["click", ROW_MORE, 1]], { layouts: ["wide"] }),
  S("account-submenu", "/assistant?c=sesn_a1", [["click", '[data-slot="sidebar-footer"] .cc-nav-item', 0], ["click", '[role="menuitem"][aria-haspopup="menu"]', 0], ["wait", 500]], { layouts: ["wide"] }),
  S("sidebar-group-menu", "/assistant?c=sesn_a1", [["hover", ".cc-project-row", 0], ["click", ".cc-group-more", 0]], { layouts: ["wide"] }),
  S("sidebar-multiselect", "/assistant?c=sesn_a1", [["click", ROW, 1, "meta"], ["click", ROW, 2, "meta"]], { layouts: ["wide"] }),
  S("sidebar-drag", "/assistant?c=sesn_a1", [["press", ROW, 2, 0, 60]], { layouts: ["wide"] }),
  S("split-resize", "/assistant?c=sesn_a1", [["drag", ".cc-split", 0, 60, 0], ["reload"]], { layouts: ["wide"] }),
  S("split-resizing", "/assistant?c=sesn_a1", [["attr", "data-cc-resizing", ""]], { layouts: ["wide"] }),
  S("account-menu", "/assistant?c=sesn_a1", [["click", '[data-slot="sidebar-footer"] .cc-nav-item', 0]], { layouts: ["wide"] }),
  S("titlebar-menu", "/assistant?c=sesn_a1", [["click", ".cc-titlebar .cc-icon-btn", 0]], { layouts: ["wide"], envs: ["desktop", "desktop-coarse"] }),

  // ── 手机 ──
  S("drawer", "/assistant?c=sesn_a1", DRAWER, { layouts: ["narrow"] }),
  S("drawer-group-menu", "/assistant?c=sesn_a1", [...DRAWER, ["click", ".cc-group-more", 1]], { layouts: ["narrow"] }),
  S("drawer-account", "/assistant?c=sesn_a1", [...DRAWER, ["click", ".cc-avatar", 0]], { layouts: ["narrow"] }),
  S("drawer-longpress", "/assistant?c=sesn_a1", [...DRAWER, ["longpress", ROW, 1]], { layouts: ["narrow"] }),
  S("pill-menu", "/assistant?c=sesn_a1", [["click", ".cc-mpill button", 1]], { layouts: ["narrow"] }),
  S("keyboard", "/assistant?c=sesn_r1", [["type", "textarea", "你好"], ["attr", "data-cc-keyboard", ""]], { layouts: ["narrow"] }),

  // ── 面板 ──
  S("results-sheet", "/assistant?c=sesn_rich", [["click", FILE_CARD, 0], ["wait", 600]]),
  S("results-doc", "/assistant?c=sesn_rich", [["click", FILE_CARD, 1], ["wait", 600]]),
  S("results-html", "/assistant?c=sesn_rich", [["click", FILE_CARD, 2], ["wait", 600], ["click", '[role="tab"]', 1]]),
  S("session-panel", "/assistant?c=sesn_a1", both([["click", ".cc-topbar .cc-row-btn", 0]], [["click", ".cc-mpill button", 1], ["click", ".cc-menu-item", 0]])),
  S("company-panel", "/assistant?company=C-0001", [["click", ".cc-page-head .cc-icon-btn", 0]]),

  // ── 页面 ──
  S("pending", "/assistant?view=pending"),
  S("tasks", "/assistant?view=tasks"),
  S("tasks-new", "/assistant?view=tasks", [["click", ".cc-page-head .cc-btn-sm[data-primary]", 0], ["wait", 500]]),
  S("companies", "/assistant?view=companies"),
  S("company", "/assistant?company=C-0001"),
  S("add-company", "/assistant?view=companies", [["click", ".cc-page-head .cc-btn-sm[data-primary]", 0], ["wait", 500]]),

  // ── 弹窗 ──
  S("delete-confirm", "/assistant?c=sesn_a1", both(
    [["hover", ROW, 1], ["click", ROW_MORE, 1], ["click", '[role="menuitem"]', -1], ["wait", 400]],
    [...DRAWER, ["longpress", ROW, 1], ["click", '[role="menuitem"]', -1], ["wait", 400]])),
  S("settings", "/assistant?c=sesn_a1", both(SETTINGS_WIDE, SETTINGS_NARROW)),
  S("settings-tab2", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 1]], [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 1]])),
  S("settings-tab3", "/assistant?c=sesn_a1", both([...SETTINGS_WIDE, ["click", ".cc-modal nav button", 2]], [...SETTINGS_NARROW, ["click", ".cc-modal nav button", 2]]), { envs: ["desktop", "desktop-coarse", "desktop-narrow"] }),

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
  S("boot-page-menu", "/assistant?c=sesn_a1", both([["hover", ROW, 1], ["click", ROW_MORE, 1]], [["click", ".cc-mpill button", 1]]), { boot: "page" }),
];

// 小数宽度：变体判定与 max-md 要在同一处分界
export const EXTRA_ENVS = {
  "web-767.5": { width: 767.5, height: 800, dpr: 2, layout: "narrow" },
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
    const actions = Array.isArray(scene.actions) ? scene.actions : scene.actions[env.layout];
    out.push({ ...scene, actions });
  }
  if (EXTRA_ENVS[envName]) return out.filter((s) => ["welcome", "chat-pending", "settings"].includes(s.name));
  return out;
}
