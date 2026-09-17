// 发号单前端的几处接线，都是原生能力的组合，没有自定义界面：
//   1. 填了域名前缀就即时显示完整域名（真值仍以服务端 validate 计算为准）
//   2. 各产品的功能候选按产品过滤，避免把 ERP 的功能勾进 Cowork 那一栏
//      （服务端也会挡一次，前端只是省得用户先选错再被打回）
//   3. 「套用常规项」：把该产品标了"常规开通"的功能一次带进来

const BASE = (frappe.boot && frappe.boot.pearnly_base_domain) || "pearnly.com";

// 功能目录里 default_on=1 的就是常规项（与 pearnly_control/features.py 的标记一致）
function default_features_of(product) {
	return frappe.db.get_list("Pearnly Feature", {
		filters: { product, default_on: 1 },
		fields: ["name"],
		order_by: "name asc",
		limit: 0,
	});
}

function fill_defaults(frm, product) {
	const fieldname = `${product}_features`;
	return default_features_of(product).then((rows) => {
		const have = new Set((frm.doc[fieldname] || []).map((r) => r.feature));
		let added = 0;
		rows.forEach((r) => {
			if (have.has(r.name)) return; // 只补缺，不动已勾的
			frm.add_child(fieldname, { feature: r.name });
			have.add(r.name);
			added += 1;
		});
		if (added) frm.refresh_field(fieldname);
		return added;
	});
}

function selected_products(frm) {
	return [...new Set((frm.doc.apps || []).map((r) => r.app).filter(Boolean))];
}

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

function apply_all_defaults(frm) {
	const products = selected_products(frm);
	if (!products.length) {
		frappe.msgprint(__("先在上面「产品与域名」里选好产品，再套用常规项"));
		return Promise.resolve();
	}
	return Promise.all(products.map((p) => fill_defaults(frm, p))).then((counts) => {
		const added = counts.reduce((a, b) => a + b, 0);
		frappe.show_alert(
			{
				message: added
					? __("已套用 {0} 项常规功能", [added])
					: __("常规功能都已经在了，没有需要补的"),
				indicator: added ? "green" : "blue",
			},
			4
		);
	});
}

frappe.ui.form.on("Pearnly Issue App", {
	app(frm, cdt, cdn) {
		// 新加的产品行如果功能表还空着，先把常规项带上，省得每次手点
		const row = locals[cdt][cdn];
		if (!row.app) return;
		const table = frm.doc[`${row.app}_features`] || [];
		if (table.length) return;
		fill_defaults(frm, row.app);
	},

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

		preview_domains(frm);

		// 不放进下拉组：这是个常用动作，直接显示在工具栏上（放进组里要多点一次）
		frm.add_custom_button(__("套用常规项"), () => apply_all_defaults(frm));
	},
});
