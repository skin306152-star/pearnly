// 开户单与租户表单上的智能体设置（ADR-019）：引擎下拉选项与默认模型都取自服务端的引擎目录，这里不写死厂商。
(() => {
	let options;

	function apply(frm) {
		const engine = (options || []).find((o) => o.value === frm.doc.assistant_engine);
		const model = frm.fields_dict.assistant_model;
		if (model && model.$input) model.$input.attr("placeholder", engine ? engine.default_model : "");
	}

	function load(frm) {
		const done = () => {
			frm.set_df_property("assistant_engine", "options", [{ value: "", label: "" }, ...options]);
			apply(frm);
		};
		if (options) return done();
		frappe
			.call({ method: "pearnly_control.onboarding.assistant.engine_options" })
			.then((r) => {
				options = r.message || [];
				done();
			});
	}

	const handlers = { refresh: load, assistant_engine: apply };
	frappe.ui.form.on("Pearnly Tenant", handlers);
	frappe.ui.form.on("Pearnly Issue", handlers);
})();
