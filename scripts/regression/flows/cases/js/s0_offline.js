// 勾了没连上：只显示「CC 桌面版未连接」和「下载 CC 桌面版」，没有断开、没有同步
await openProfile();
await sleep(1500);
const status = field("express_connection");
const state = {status: status ? (status.querySelector("input, textarea") || {}).value || status.innerText.trim() : "",
  download: !!fieldButton("cc_download"), disconnect: !!fieldButton("express_disconnect"), sync: !!pageButton("同步 Express"),
  checkbox_enabled: !!checkbox("express_enabled") && !checkbox("express_enabled").disabled};
return {ok: state.status.includes("CC 桌面版未连接") && state.download && !state.disconnect && !state.sync, state};
