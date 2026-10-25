// 页面能打开：路由到了、不是无权限页 / 404、主区有内容；页面上的未捕获异常由驱动收进 logs（有就不通过）。
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
const want = args.expect || "content";
const ok = want === "not_permitted" ? notPermitted : (!notPermitted && !notFound && text.length > 20);
return { ok, route, not_permitted: notPermitted, not_found: notFound, chars: text.length, user: frappe.session && frappe.session.user };
