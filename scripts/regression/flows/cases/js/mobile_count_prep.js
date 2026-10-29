// 手机盘点准备：给 ${v.wh_label} 建一张盘点并开始（桌面接口），记下名字给下一步
const made = await call("pearnly_erp_portal.inventory.api.api_create_stock_count", {title: "flows mobile ${v.how} " + Date.now(), warehouse: "${v.warehouse}"});
localStorage.setItem("flows_count", made.name);
return {ok: !!made.name, made};
