// 页内切换记录后，页头入口与新记录的状态一致（验收 10-27：盘点页从已过账切到进行中多出「导出」，反过来留着「结束盘点」；
// 直接打开是对的）。原生 `add_inner_button` 同时往「⋯」菜单里加一项，只清工具栏会把上一张的留在菜单里。
// 先打开 first 状态最新的一张，再在同一个页面里切到 second 状态最新的一张，然后看页头（按钮 + 「⋯」菜单项，没展开也在 DOM 里）：
// expect 都在、absent 一个都不在。两种状态缺一种记「没测到」。页面上的未捕获异常由驱动收进 logs（有就不通过）。
// args: {doctype, root, first, second, expect: [新记录该有的], absent: [只属于上一张的]}（标签是原文，按界面语言翻译后比对）
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const say = typeof __ === "function" ? __ : (value) => value;
const latest = async (status) => (await frappe.db.get_list(args.doctype, { filters: { status }, fields: ["name"],
  order_by: "creation desc", limit: 1 }))[0]?.name;
const [first, second] = [await latest(args.first), await latest(args.second)];
if (!first || !second) return { ok: false, skip: true, why: `no ${!first ? args.first : args.second} ${args.doctype}` };
const visibleText = (el) => (el.innerText || el.textContent || "").trim();
// 只算在用的入口：按钮要显示着（框架清主按钮只是加 hide，文字还在）；「⋯」菜单项没展开时不显示，看它没被 hide 收起
const live = (el) => (el.classList.contains("dropdown-item") ? !el.closest(".hide") : Boolean(el.offsetParent));
const head = () => {
  const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document;
  return [...new Set([...page.querySelectorAll(".page-actions .primary-action, .page-actions .btn, .page-actions .dropdown-item")]
    .filter(live).map(visibleText).filter(Boolean))];
};
const want = (args.expect || []).map((label) => say(label));
const gone = (args.absent || []).map((label) => say(label));
frappe.set_route(args.root, first);
await sleep(4000);
const before = head();
frappe.set_route(args.root, second);
let found = [];
const started = Date.now();
while (Date.now() - started < 12000) {
  await sleep(500);
  found = head();
  if (want.every((label) => found.includes(label)) && !gone.some((label) => found.includes(label))) break;
}
const missing = want.filter((label) => !found.includes(label));
const left = gone.filter((label) => found.includes(label));
return { ok: !missing.length && !left.length, first, second, missing, left_over: left, before: before.slice(0, 20), found: found.slice(0, 20) };
