// 接了 Express 的公司（exec express-on）：盘点里就地建一件、记 2、过账 → 排进 Express
const {codes} = s3();
const count = await newCount("express");
const made = await quietCall(C + "api_stock_count_create_product", {count, code: codes.express, product_name: "แล็บ Express ใหม่ " + Date.now(), unit: "ชิ้น"});
const added = await quietCall(C + "api_stock_count_add_entry", {count, code: codes.express, qty: 2, request_id: crypto.randomUUID()});
const posted = await quietCall(C + "api_stock_count_post", {name: count});
s3save({express_count: count});
return {ok: exec_out && exec_out.linked && made && made.created && !added.error && !posted.error, exec_out, made, posted: posted && (posted.error || "ok"), count};
