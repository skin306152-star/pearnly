// 收银相关三页打开：收银台、收银记录、班次记录 —— 页面出内容（新开户没有单据时是原生空状态「没有可显示的内容」，
// 也算打开）、不是无权限页、没有未捕获异常（异常由驱动收集在 page_errors）
const out = {};
for (const route of ["pearnly-cashier", "pearnly-pos-receipts", "pearnly-pos-shifts"]) {
  frappe.set_route(route);
  // 等到这一页显示出来并出了列表或空状态（实验栈慢，固定等几秒会误判）
  const settled = () => { const el = $$(".page-container").find(el => el.getAttribute("data-page-route") === route);
    const t = el ? el.innerText.trim() : ""; return el && (t.length > 20 || t.includes(__("Nothing to show"))) ? el : null; };
  const page = await wait(settled, 30000) || $$(".page-container").find(el => el.getAttribute("data-page-route") === route);
  const text = page ? page.innerText.trim() : "";
  const empty = text.includes(__("Nothing to show"));
  out[route] = {opened: !!page && (text.length > 20 || empty), empty, not_permitted: !!(page && page.querySelector(".message-page"))};
}
return {ok: Object.values(out).every(v => v.opened && !v.not_permitted), out};
