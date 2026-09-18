// 发号单前端只做一件事：填了域名前缀就即时把完整域名显示出来。
// （真值仍以服务端 validate 计算为准，见 pearnly_control/domains.py）
//
// 功能模版那套接线（按产品过滤候选、套用常规项按钮）已经拆掉了 ——
// 现在选了产品就开通该产品的全部功能（用户 2026-09-18 定），没有可勾的东西。

const BASE = (frappe.boot && frappe.boot.pearnly_base_domain) || "pearnly.com";

// 只做展示用：打开表单时把已有前缀的完整域名补出来，免得"访问域名"一直空着。
// 真值仍由服务端 validate 计算（同一条规则，见 pearnly_control/domains.py）。
function preview_domains(frm) {
	let changed = false;
	(frm.doc.apps || []).forEach((row) => {
		const prefix = (row.domain_prefix || "").trim().toLowerCase();
		const want = prefix ? `${prefix}.${BASE}` : "";
		if (row.domain !== want) {
			row.domain = want;
			changed = true;
		}
	});
	if (changed) frm.refresh_field("apps");
}

frappe.ui.form.on("Pearnly Issue App", {
	domain_prefix(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const prefix = (row.domain_prefix || "").trim().toLowerCase();
		row.domain = prefix ? `${prefix}.${BASE}` : "";
		frm.refresh_field("apps");
	},
});

frappe.ui.form.on("Pearnly Issue", {
	refresh(frm) {
		preview_domains(frm);
	},
});
