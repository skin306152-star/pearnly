// 没有盘点录入权的账号（销售岗位）：在进行中的盘点里就地建档被拒（权限错误，不是别的错）
const prepared = exec_out || {};
const response = await fetch("/api/method/" + C + "api_stock_count_create_product", {method: "POST", credentials: "include",
  headers: {"Content-Type": "application/json", "Accept": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token},
  body: JSON.stringify({count: prepared.noperm_count, code: prepared.codes.noperm, product_name: "ไม่มีสิทธิ์", unit: "ชิ้น"})});
const body = await response.json().catch(() => ({}));
return {ok: response.status === 403 || /Permission/.test(body.exc_type || ""), status: response.status, exc_type: body.exc_type};
