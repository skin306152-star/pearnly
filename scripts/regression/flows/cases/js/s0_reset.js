// 起点：没连 CC（exec cc-unplug）、普通、不接 Express
await call(M + "api_update_company", {name: "${company}", company_type: "general", express_enabled: 0});
const profile = await profileOf();
return {ok: profile.company_type === "general" && !Number(profile.express_enabled) && exec_out && exec_out.connected === false, exec_out};
