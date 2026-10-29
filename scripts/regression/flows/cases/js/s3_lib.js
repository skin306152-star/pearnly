// 公共：S3 本轮的码与盘点（存在本页 localStorage，同一个用例的各步共用）
const C = "pearnly_erp_portal.inventory.api.";
const s3 = () => { try { return JSON.parse(localStorage.getItem("flows_s3") || "{}"); } catch (e) { return {}; } };
const s3save = value => { try { localStorage.setItem("flows_s3", JSON.stringify({...s3(), ...value})); } catch (e) { /* 下一步取不到就判不通过 */ } };
const newCount = async label => (await call(C + "api_create_stock_count", {title: "flows s3 " + label + " " + Date.now(), warehouse: "${seed.count_warehouse}"})).name;
const quietCall = (method, args) => new Promise((resolve, reject) => frappe.call({method, args, type: "POST", error: reject,
  callback: r => resolve(r.message)}).fail(reject)).catch(e => { $$(".modal.show .btn-modal-close").forEach(b => b.click());
  return {error: JSON.stringify((e && (e.responseJSON || e)) || "error").slice(0, 300)}; });
