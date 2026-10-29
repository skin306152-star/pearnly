// 在资料页勾「接 Express」保存（还没连上 CC 桌面版）
await openProfile();
await sleep(1500);
const box = checkbox("express_enabled");
if (box && !box.checked) { box.click(); await sleep(400); }
const saved = await saveCompany();
const profile = await wait(async () => { const v = await profileOf(); return Number(v.express_enabled) ? v : null; }, 15000, 1000);
return {ok: !!profile && profile.express_connected === false, saved, express_enabled: profile && profile.express_enabled};
