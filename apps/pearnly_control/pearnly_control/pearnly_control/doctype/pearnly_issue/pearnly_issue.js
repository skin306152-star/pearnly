// 发号单前端的两个小便利，都是原生能力的接线，没有自定义界面：
//   1. 填了域名前缀就即时显示完整域名（真值仍以服务端 validate 计算为准）
//   2. 各产品的功能候选按产品过滤，避免把 ERP 的功能勾进 Cowork 那一栏
//      （服务端也会挡一次，前端只是省得用户先选错再被打回）

const BASE = (frappe.boot && frappe.boot.pearnly_base_domain) || "pearnly.com";

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
		const byProduct = (fieldname, product) => {
			const field = frm.fields_dict[fieldname];
			if (!field || !field.grid) return;
			field.grid.get_field("feature").get_query = () => ({ filters: { product } });
		};
		byProduct("cowork_features", "cowork");
		byProduct("erp_features", "erp");
		byProduct("dms_features", "dms");
	},
});
