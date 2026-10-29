// 同一个码别人再建（品名不同）→ 回同一件、不建第二件；停用商品的码 → 照样认出、记 1
const {count, codes, disabled} = s3();
const again = await quietCall(C + "api_stock_count_create_product", {count, code: codes.desk, product_name: "ชื่ออื่น " + Date.now(), unit: "ชิ้น"});
const look = await quietCall(C + "api_stock_count_lookup", {count, code: disabled.code});
const added = await quietCall(C + "api_stock_count_add_entry", {count, code: disabled.code, qty: 1, request_id: crypto.randomUUID()});
return {ok: again && again.created === false && !!again.name && !look.error && !added.error, again, look: look && (look.error || (look.product || {}).name), added};
