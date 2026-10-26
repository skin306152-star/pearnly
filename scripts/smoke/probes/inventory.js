// 页面入口盘点（回归基线用）：记下这一页用户实际看得到、点得到的入口——页头按钮、「…」/ 下拉菜单项、侧栏与导航、
// 列表 / 表单侧栏、导航栏下拉、可点链接、已注册的快捷键。只读：不点任何会改数据的东西，下拉菜单只读 DOM（框架把菜单项
// 预先渲染在 DOM 里，隐藏的项带 hidden 标记一并记下，比对时只看 visible）。
// args: {}（无）
const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== "hidden";
const text = (el) => (el.innerText || el.textContent || el.getAttribute("title") || el.getAttribute("aria-label") || "")
  .replace(/\s+/g, " ").trim().slice(0, 80);
const hiddenByClass = (el) => !!el.closest(".hide, .hidden, [hidden], [style*='display: none']");
const items = (root, selector) => root ? [...root.querySelectorAll(selector)].map((el) => ({
  text: text(el), visible: visible(el) || (!hiddenByClass(el) && !!el.closest(".dropdown-menu")),
  href: el.getAttribute("href") || undefined,
  data: el.getAttribute("data-label") || el.getAttribute("data-name") || undefined,
})).filter((row) => row.text || row.data) : [];
const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
const head = page && page.querySelector(".page-head");
const route = frappe.get_route ? frappe.get_route() : [];
const out = {
  route, title: (head && text(head.querySelector(".title-text, .page-title"))) || document.title,
  user: frappe.session && frappe.session.user,
  roles: (frappe.user_roles || []).slice(0, 60),
  // 页头：主 / 次按钮、自定义按钮组、「…」菜单与各下拉
  head_buttons: head ? items(head, ".page-actions > .btn, .page-actions .standard-actions > .btn, .page-actions .custom-actions .btn, .page-actions .btn-group > .btn:not(.dropdown-toggle)").filter((row) => row.visible) : [],
  head_menu: head ? items(head, ".page-actions .dropdown-menu .dropdown-item, .page-actions .dropdown-menu a") : [],
  head_dropdowns: head ? [...head.querySelectorAll(".page-actions .btn-group, .page-actions .inner-group-button")].filter(visible).map((group) => ({
    label: text(group.querySelector(".dropdown-toggle, button")),
    items: items(group, ".dropdown-menu .dropdown-item, .dropdown-menu a").map((row) => row.text),
  })) : [],
  // 页面内的工具栏（列表筛选区、表单工具栏外的按钮）
  body_buttons: page ? items(page, ".page-form .btn, .standard-filter-section .btn, .filter-section .btn, .list-paging-area .btn, .form-toolbar .btn").filter((row) => row.visible).map((row) => row.text) : [],
  // 列表 / 表单 / 报表的左侧栏（框架默认的「指派给 / 创建人 / 标签 / 分享 / 关注 / 附件」都在这里）
  side_section: page ? items(page, ".layout-side-section .sidebar-label, .layout-side-section .list-sidebar-button, .layout-side-section .sidebar-item-container, .layout-side-section a, .layout-side-section button, .form-sidebar .sidebar-label, .form-sidebar a").filter((row) => row.visible).map((row) => row.text) : [],
  // 表单底部：评论框 / 动态（框架默认）
  form_footer: page ? {
    comment_box: visible(page.querySelector(".comment-box, .new-comment")),
    timeline: visible(page.querySelector(".new-timeline, .timeline-items")),
    assign: visible(page.querySelector(".form-assignments, .assign-to")),
    attachments: visible(page.querySelector(".form-attachments, .attachments-label")),
    share: visible(page.querySelector(".form-shared")),
    tags: visible(page.querySelector(".form-tags, .tags-input")),
  } : {},
  // 桌面侧栏（Workspace Sidebar / 产品导航）
  desk_sidebar: [...new Map([...document.querySelectorAll(".body-sidebar a[href], .body-sidebar .sidebar-item-container, .desk-sidebar a[href]")].filter(visible)
    .map((el) => { const a = el.matches("a") ? el : el.querySelector("a[href]"); return [text(el) + "|" + (a && a.getAttribute("href") || ""), { text: text(el), href: (a && a.getAttribute("href")) || undefined }]; })).values()],
  // 顶栏：搜索框、通知铃、帮助、用户头像下拉、应用切换
  navbar: {
    search: visible(document.querySelector(".navbar .search-bar, .navbar input#navbar-search, .awesomplete input")),
    notifications: visible(document.querySelector(".navbar .dropdown-notifications, .navbar .notifications-icon")),
    help: visible(document.querySelector(".navbar .dropdown-help")),
    items: items(document, ".navbar .dropdown-menu .dropdown-item, .navbar .dropdown-menu a, .sidebar-user-dropdown .dropdown-item, .body-sidebar-bottom .dropdown-item, .body-sidebar .dropdown-menu .dropdown-item").map((row) => ({ text: row.text, href: row.href, visible: row.visible })),
    buttons: items(document, ".navbar .nav-link, .navbar .btn, .body-sidebar-top .btn, .body-sidebar-bottom .btn, .body-sidebar-bottom a").filter((row) => row.visible).map((row) => row.text),
  },
  // 主区可点链接（站内）
  links: page ? [...new Map([...page.querySelectorAll("a[href]")].filter(visible).map((a) => [a.getAttribute("href"), text(a)])).entries()]
    .filter(([href]) => href && !href.startsWith("#") && !href.startsWith("javascript"))
    .slice(0, 120).map(([href, label]) => ({ href, text: label })) : [],
  // 已注册的快捷键（框架的 frappe.ui.keys.standard_shortcuts），记键与说明
  shortcuts: (() => {
    try {
      const list = (frappe.ui && frappe.ui.keys && frappe.ui.keys.standard_shortcuts) || [];
      return list.filter((row) => !row.page || row.page === (window.cur_page && cur_page.page))
        .map((row) => `${row.shortcut} · ${row.description || ""}`.trim()).slice(0, 80);
    } catch (error) { return [String(error)]; }
  })(),
  not_permitted: !!(page && page.querySelector(".message-page-image, .message-page-icon")),
};
// 点开才渲染的菜单（框架 frappe.ui.create_menu）：侧栏标题的应用切换、侧栏底部的用户菜单。只点开读项、再关掉，不点任何项
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const menuItems = () => new Set([...document.querySelectorAll(".dropdown-menu-item, .dropdown-item, [role=menuitem], .menu-item")].filter(visible));
const open = async (el) => {
  if (!el || !visible(el)) return null;
  // 链接不点（点了会跳页），只记它指向哪里
  const anchor = el.closest("a[href]");
  if (anchor && !/^(#|javascript)/.test(anchor.getAttribute("href"))) return [`→ ${anchor.getAttribute("href")}`];
  const before = menuItems();
  el.click();
  await sleep(500);
  const found = [...menuItems()].filter((item) => !before.has(item)).map((item) => text(item)).filter(Boolean);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  document.body.click();
  await sleep(250);
  return found;
};
out.app_menu = await open(document.querySelector(".body-sidebar .sidebar-header"));
const userEl = [...document.querySelectorAll(".body-sidebar-bottom a, .body-sidebar-bottom .btn, .body-sidebar-bottom [data-toggle], .body-sidebar .sidebar-user, .navbar .avatar")]
  .filter(visible).find((el) => (el.innerText || "").includes(frappe.session.user) || el.querySelector(".avatar"));
out.user_menu = await open(userEl);
return { ok: true, ...out };
