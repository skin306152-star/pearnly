// 公司页：列表点进这家公司 → 资料页出表单 → 在页面上改「简称」点保存 → 服务端读回是新值；没有页面报错
const M = "pearnly_erp_portal.masters.api.";
frappe.set_route("pearnly-company");
const row = await wait(() => $$(".page-container[data-page-route='pearnly-company'] .list-row, .page-container[data-page-route='pearnly-company'] .dt-row, .page-container[data-page-route='pearnly-company'] a")
  .find(el => el.innerText.includes("${company_name}")), 30000);
click(row && (row.querySelector("a, .list-subject, .dt-cell__content") || row));
const field = await wait(() => $$("[data-fieldname='short_name'] input, [data-fieldname='short_name'] textarea")[0], 30000);
const tag = "LAB" + Math.random().toString(36).slice(2, 6).toUpperCase();
let saved = null;
if (field) {
  field.focus(); field.value = tag; field.dispatchEvent(new Event("input", {bubbles: true})); field.dispatchEvent(new Event("change", {bubbles: true}));
  await sleep(800);
  click($$(".page-head .primary-action, .page-actions .primary-action")[0]);
  await wait(async () => (await call(M + "api_get_profile", {name: "${company}"}, "GET")).short_name === tag, 30000, 1000);
  saved = (await call(M + "api_get_profile", {name: "${company}"}, "GET")).short_name;
}
return {ok: !!row && !!field && saved === tag, row: !!row, field: !!field, tag, saved, alerts: alerts(), msg: msg()};
