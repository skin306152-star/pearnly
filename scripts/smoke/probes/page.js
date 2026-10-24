// 页面能打开：路由到了、不是无权限页 / 404、主区有内容；页面上的未捕获异常由驱动收进 logs（有就不通过）。
// args: {expect: "content" | "not_permitted"}（默认 content）
const route = frappe.get_route ? frappe.get_route() : [];
const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
const text = (page && page.innerText || "").trim();
const notPermitted = !!document.querySelector(".page-card .btn-primary[href='/app'], .not-permitted, [data-page-route='message']")
  || /Not Permitted|ไม่ได้รับอนุญาต|没有权限|无权限/.test(text.slice(0, 200));
const notFound = /Page .* not found|Not Found/.test(text.slice(0, 200));
const want = args.expect || "content";
const ok = want === "not_permitted" ? notPermitted : (!notPermitted && !notFound && text.length > 20);
return { ok, route, not_permitted: notPermitted, not_found: notFound, chars: text.length, user: frappe.session && frappe.session.user };
