// 没有权限的人打开别人的单的详情页：显示框架原生的无权限页，不白屏、控制台无未捕获错误（验收 10-27：采购岗打开同事的
// 采购单 /desk/pearnly-purchase/<别人的单> 页面空白 + Uncaught (in promise)）。
// 用受限账号的会话跑（run.py 按 SMOKE_USER_<产品>_LIMITED 建），单号由 run.py 先在容器里只读挑一张别人建的（pick_doc.py）。
// 页面上的未捕获异常（含未处理的 Promise 拒绝）由驱动收进 logs（有就不通过）。
// args: {root, name}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
frappe.set_route(args.root, args.name);
const shown = () => {
  const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])");
  return page?.querySelector(".message-page") ? page.getAttribute("data-page-route") : "";
};
let route = "";
const started = Date.now();
while (Date.now() - started < 12000) {
  await sleep(500);
  route = shown();
  if (route) break;
}
const blank = !route && !(document.querySelector(".page-container[data-page-route]:not([style*='display: none']) .layout-main-section")
  ?.innerText || "").trim();
return { ok: Boolean(route), page: route, blank, at: frappe.get_route() };
