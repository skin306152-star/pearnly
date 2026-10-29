// 在资料页把类型改成「汽车」保存 → 不刷新，新建物料就有「按台管理」「号码种类」；按台商品入库不给台被拒；改回普通又收
await openProfile();
await sleep(1500);
const picked = await comboPick("company_type", "汽车");
const saved = await saveCompany();
const profile = await wait(async () => { const v = await profileOf(); return v.company_type === "vehicle" ? v : null; }, 15000, 1000);
const fields = await newProductFields();
const refused = await stockInVehicle();
await call(M + "api_update_company", {name: "${company}", company_type: "general"});
const back = await stockInVehicle();
const ok = !!profile && Array.isArray(fields) && fields.includes("track_serial") && !!refused && back === null;
return {ok, picked, saved, type: profile && profile.company_type, fields, refused: refused && refused.slice(0, 200), back};
