// 页面能打开：路由到了、不是无权限页 / 404、主区有内容、页头只有登记过的按钮；页面上的未捕获异常由驱动收进 logs（有就不通过）。
// args: {expect: "content" | "not_permitted"}（默认 content）
const route = frappe.get_route ? frappe.get_route() : [];
const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
const text = (page && page.innerText || "").trim();
// 框架原生的无权限页 / 404 页（`frappe.show_not_permitted` / `show_not_found` → `show_message_page`）：两者同一张图，
// 按框架自己的提示语区分；提示语用页面的 `__()` 取当前界面语言的那一句，不写死某种语言（验收 10-25：中文界面认不出）
const say = typeof __ === "function" ? __ : (value) => value;
const messagePage = !!(page && page.querySelector(".message-page-image, .message-page-icon"));
const head = text.slice(0, 300);
const notPermitted = (messagePage && head.includes(say("Sorry! You are not permitted to view this page.")))
  || !!document.querySelector(".page-card .btn-primary[href='/app'], .not-permitted");
const notFound = (messagePage && head.includes(say("Sorry! I could not find what you were looking for.")))
  || /Page .* not found|Not Found/.test(head);
// 页头只有登记过的按钮（core `desk/actions.js` 白名单，10-26 testcowork 个人页露出原生「权限」「创建用户电子邮件」）：
// 页头里可点的按钮、菜单项、图标，没带 `data-pearnly-declared` 的就是产品没承接的原生入口。分组下拉、菜单、操作的折叠按钮本身不算。
const pageHead = page && page.querySelector(".page-head");
const shownInHead = (el) => !el.closest(".hide, .hidden, [hidden]") && getComputedStyle(el).display !== "none"
  && (el.closest(".dropdown-menu") ? getComputedStyle(el.closest("li") || el).display !== "none" : el.offsetParent !== null);
const toggle = (el) => el.matches(".menu-btn-group > button, .actions-btn-group > button, .inner-group-button > button");
const stray = pageHead ? [...pageHead.querySelectorAll(".page-actions button, .page-actions a.dropdown-item, .page-icon-group button")]
  .filter((el) => !toggle(el) && shownInHead(el) && !el.closest("[data-pearnly-declared]") && !el.querySelector("[data-pearnly-declared]"))
  .map((el) => (el.textContent || el.getAttribute("title") || el.getAttribute("data-label") || "").trim().slice(0, 40)) : [];
const want = args.expect || "content";
// 列表的原生空状态（「没有可显示的内容」）是打开成功的一种：冒烟账号当前公司没有这类数据时字很少（10-25 线上冒烟）
const empty = !!(page && [...page.querySelectorAll(".no-result")].some((el) => el.offsetParent !== null));
const ok = want === "not_permitted" ? notPermitted : (!notPermitted && !notFound && (text.length > 20 || empty) && !stray.length);
return { ok, route, not_permitted: notPermitted, not_found: notFound, empty, chars: text.length, stray_buttons: stray,
  user: frappe.session && frappe.session.user };
