// 按码找商品（products.service.api_find_product，扫码 / 新建物料 / 出入库扫码都走它）：取当前公司能看到的一件在架商品，
// 用它的编码（有条码再用条码）去找，必须找回同一件。数据不写死，任何有商品的测试租户都能跑。
const rows = await frappe.db.get_list("ERP Product", { fields: ["name", "code", "barcode"], filters: { is_active: 1 }, limit: 20 });
const pick = rows.find((row) => row.code);
if (!pick) return { ok: false, why: "这个测试租户没有在架商品" };
const tries = [pick.code, pick.barcode].filter(Boolean);
const found = [];
for (const value of tries) {
  const r = await frappe.call({ method: "pearnly_erp_portal.products.service.api_find_product", args: { value }, freeze: false });
  found.push({ value, name: r && r.message && (r.message.name || r.message.product) });
}
return { ok: found.every((f) => f.name === pick.name), product: pick.name, found };
