// 单据详情页能打开、按钮齐全（10-26 打印窗口发现：已提交 / 作废单据的详情页里，只读的库位格赋值报错，打断整页，
// Express 按钮、收款、取消菜单都出不来；以前的冒烟只开列表页，没开过详情页）。
// 在列表页上找这一类最新的一张某状态的单 → 点开（列表行 / 路由）→ 等页面或弹窗里出现预期的按钮 / 菜单项。
// 页面上的未捕获异常由驱动收进 logs（有就不通过）。找不到这种状态的单记「没测到」。
// args: {doctype, status, filters, root, open: "route" | "row", expect: [按钮或菜单项的原文（按界面语言翻译后比对）]}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const say = typeof __ === "function" ? __ : (value) => value;
// 点列表行打开的（出入库单：详情是列表上的弹窗，没有路由）只能挑列表第一页上看得到的单
const listed = args.open === "row" ? [...document.querySelectorAll("a[data-product-row]")].map((a) => a.dataset.productRow) : null;
if (listed && !listed.length) return { ok: false, skip: true, why: "empty list" };
const rows = await frappe.db.get_list(args.doctype, { filters: { ...(args.filters || {}), status: args.status,
  ...(listed ? { name: ["in", listed] } : {}) }, fields: ["name"], order_by: "creation desc", limit: 1 });
if (!rows.length) return { ok: false, skip: true, why: `no ${args.status} ${args.doctype}${listed ? " on the first list page" : ""}` };
const name = rows[0].name;
if (args.open === "row") {
  document.querySelector(`a[data-product-row="${CSS.escape(name)}"]`).click();
} else {
  frappe.set_route(args.root, name);
}
const want = (args.expect || []).map((label) => say(label));
const visibleText = (el) => (el.innerText || el.textContent || "").trim();
let found = [];
const started = Date.now();
while (Date.now() - started < 12000) {
  await sleep(500);
  const scope = document.querySelector(".modal.show") || document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document;
  const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document;
  // 页面动作（主按钮、行内按钮、「…」菜单里的项，菜单没展开也在 DOM 里）与弹窗里的按钮
  found = [...new Set([
    ...[...page.querySelectorAll(".page-actions .primary-action, .page-actions .inner-group-button, .page-actions .btn, .page-actions .dropdown-item")],
    ...[...scope.querySelectorAll(".modal.show .btn, .modal.show .dropdown-item")],
  ].map(visibleText).filter(Boolean))];
  if (want.every((label) => found.includes(label))) break;
}
const missing = want.filter((label) => !found.includes(label));
return { ok: missing.length === 0, doc: name, status: args.status, route: frappe.get_route(), missing, found: found.slice(0, 20) };
