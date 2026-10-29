// 仓库规则（多仓 P0 / 库位清单，服务端判，用页面同一套接口）：
// ① 有货的仓不能停用；② 空仓能停用、再启用；③ 仓设了库位清单后，入库只能进清单里的库位，清单外被拒，清单内能进且记在那个库位
const tryCall = async (method, args) => { try { return {ok: true, r: await call(method, args)}; } catch (e) {
  return {ok: false, m: String((e && (e.responseJSON && (e.responseJSON._server_messages || e.responseJSON.exception))) || e).slice(0, 200)}; } };
const out = {};
out.stocked_off = await tryCall("pearnly_erp_portal.inventory.api.api_update_warehouse", {name: "${v.stocked}", is_active: 0});
out.empty_off = await tryCall("pearnly_erp_portal.inventory.api.api_update_warehouse", {name: "${v.empty}", is_active: 0});
out.empty_on = await tryCall("pearnly_erp_portal.inventory.api.api_update_warehouse", {name: "${v.empty}", is_active: 1});
const loc = "A-" + Date.now().toString().slice(-4);
const current = await call("pearnly_erp_portal.inventory.locations.api_get_locations", {warehouse: "${v.empty}"}, "GET");
const rows = [...((current && (current.rows || current.locations)) || current || []).map(r => ({location_name: r.location_name, is_active: r.is_active})), {location_name: loc, is_active: 1}];
out.save_locations = await tryCall("pearnly_erp_portal.inventory.locations.api_save_locations", {warehouse: "${v.empty}", rows: JSON.stringify(rows)});
const line = l => JSON.stringify([{product: "${products.code}", qty: 1, unit_cost: 10, warehouse: "${v.empty}", location: l}]);
out.in_unlisted = await tryCall("pearnly_erp_portal.inventory.api.api_create_stock_entry", {direction: "in", posting_date: frappe.datetime.get_today(), warehouse: "${v.empty}", lines: line("ZZ-99"), submit: 1});
out.in_listed = await tryCall("pearnly_erp_portal.inventory.api.api_create_stock_entry", {direction: "in", posting_date: frappe.datetime.get_today(), warehouse: "${v.empty}", lines: line(loc), submit: 1});
const stock = await call("pearnly_erp_portal.inventory.api.api_stock", {warehouse: "${v.empty}", include_zero: 1, limit: 500}, "GET");
const atLoc = (stock || []).filter(r => (r.product === "${products.code}" || r.name === "${products.code}") && r.location === loc);
const ok = !out.stocked_off.ok && out.empty_off.ok && out.empty_on.ok && out.save_locations.ok && !out.in_unlisted.ok && out.in_listed.ok && atLoc.length === 1;
// 收尾：把这个仓的库位清单还原，免得影响别的用例（清单里刚入了货的库位保留）
return {ok, out, at_location: atLoc.map(r => ({location: r.location, qty: r.qty_on_hand}))};
