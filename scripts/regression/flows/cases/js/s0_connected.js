// 连上之后（exec cc-connect）：有「断开」和「同步 Express」，没有下载，勾选框只读；直接取消被拒；点断开 → 不再接
await openProfile();
await sleep(1500);
const state = {download: !!fieldButton("cc_download"), disconnect: !!fieldButton("express_disconnect"), sync: !!pageButton("同步 Express"),
  locked: !checkbox("express_enabled") || checkbox("express_enabled").disabled || !!field("express_enabled").querySelector(".disabled, [disabled]")};
const before = await profileOf();
const refused = await errorOf(M + "api_update_company", {name: "${company}", express_enabled: 0});
click(fieldButton("express_disconnect"));
const yes = await wait(() => $$(".modal.show .btn-primary").find(b => b.textContent.trim().length), 10000);
click(yes);
const after = await wait(async () => { const v = await profileOf(); return !Number(v.express_enabled) ? v : null; }, 15000, 1000);
const ok = exec_out && exec_out.connected && before.express_connected === true && state.disconnect && state.sync && !state.download && state.locked
  && !!refused && /Express/.test(refused) && !!after;
return {ok, exec_out, state, refused: refused && refused.slice(0, 200), after_enabled: after && after.express_enabled};
