// 生产补丁 v1_17_company_settings（exec patch-company-settings 建两家公司后跑）：记着 Express 账套的自动勾上「接 Express」、类型普通；没账套的不勾
const out = exec_out || {};
return {ok: !!out.linked && Number(out.linked.express_enabled) === 1 && out.linked.company_type === "general"
  && Number(out.plain.express_enabled) === 0 && out.plain.company_type === "general", out};
