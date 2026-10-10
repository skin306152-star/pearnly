// 开户单与租户表单上的智能体设置（ADR-019）：引擎与模型的下拉选项都取自服务端的引擎目录，这里不写死厂商或模型。
// 模型列表的第一个是该引擎的默认模型；选了引擎而模型为空或不在列表里时，自动填默认模型。
(() => {
	let options;

	function engine_of(frm) {
		return (options || []).find((o) => o.value === frm.doc.assistant_engine);
	}

	function apply_models(frm) {
		const engine = engine_of(frm);
		const models = engine ? engine.models : [];
		frm.set_df_property("assistant_model", "options", ["", ...models]);
		if (engine && !models.includes(frm.doc.assistant_model)) {
			frm.set_value("assistant_model", models[0]);
		}
	}

	function load(frm) {
		const done = () => {
			frm.set_df_property("assistant_engine", "options", [
				{ value: "", label: "" },
				...options.map((o) => ({ value: o.value, label: o.label })),
			]);
			apply_models(frm);
		};
		if (options) return done();
		frappe
			.call({ method: "pearnly_control.onboarding.assistant.engine_options" })
			.then((r) => {
				options = r.message || [];
				done();
			});
	}

	const handlers = { refresh: load, assistant_engine: apply_models };
	frappe.ui.form.on("Pearnly Tenant", handlers);
	frappe.ui.form.on("Pearnly Issue", handlers);
})();
