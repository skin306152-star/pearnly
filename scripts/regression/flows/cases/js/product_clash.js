// 商品撞码：新建商品，内部编码填一个别的商品正在用的编码「${codes.code}」→ 提交被拒（说出撞了哪件、哪种码），商品没建出来
const name = "撞码测试 " + Date.now();
click(await wait(() => byText(".page-head button", "新建"), 10000));
const d = await wait(() => window.cur_dialog && cur_dialog.display && cur_dialog.fields_dict.internal_code && cur_dialog, 10000);
if (!d) return {ok: false, why: "新建商品弹窗没出来"};
await d.set_value("product_name", name);
await d.set_value("internal_code", "${codes.code}");
await sleep(500);
click(d.get_primary_btn()[0]);
await sleep(3000);
const message = msg();
const made = await call("frappe.client.get_count", {doctype: "ERP Product", filters: {product_name: name}}, "GET");
return {ok: made === 0 && /${codes.code}|สินค้าตามรหัส/.test(message), message, made};
