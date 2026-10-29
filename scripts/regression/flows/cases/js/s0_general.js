// 普通公司：资料页有「公司类型」= 普通、「接 Express」不勾，没有断开 / 下载 / 同步；新建物料没有按台两格和批次 / 有效期；
// 按台商品按数量入库不要台
const shown = await openProfile();
await sleep(1500);
const state = {type: comboText("company_type"), express: checkbox("express_enabled") && checkbox("express_enabled").checked,
  download: !!fieldButton("cc_download"), disconnect: !!fieldButton("express_disconnect"), sync: !!pageButton("同步 Express")};
const fields = await newProductFields();
const stock_error = await stockInVehicle();
const ok = !!shown && state.type === "普通" && state.express === false && !state.download && !state.disconnect && !state.sync
  && Array.isArray(fields) && fields.includes("product_name") && !["track_serial", "serial_kind", "track_batch", "track_expiry"].some(f => fields.includes(f))
  && stock_error === null;
return {ok, state, fields, stock_error};
