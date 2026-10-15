/* ERP 的扫码动作：扫码弹窗 `p.scan` 与物料选择框里的扫码带出（`p.scan_link`）。
 * 读码器本体（原生 BarcodeDetector 优先、同源 WASM 兜底、`p.scan_reader` / `p.scan_stage` 等）在共用层
 * `pearnly_core/public/desk/scan.js`，那边先加载；这里只放属于 ERP 的部分（2026-09-26 去重）。
 */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	p.scan = async ({ kind = "barcode", on_result } = {}) => {
		const t = await p.texts("product.");
		const d = new frappe.ui.Dialog({ title: t("product.scan.title"), fields: [
			{ fieldtype: "HTML", fieldname: "reader", options: p.scan_stage() },
			{ fieldtype: "Check", fieldname: "torch", label: t("product.scan.light"), hidden: 1,
				change: () => scanner?.torch(Boolean(d.get_value("torch"))) },
			{ fieldtype: "HTML", fieldname: "hint" },
		] });
		let scanner, closed = false;
		d.onhide = () => { closed = true; scanner?.stop(); };
		d.show();
		const open = async () => {
			scanner = p.scan_reader({ host: d.fields_dict.reader.$wrapper, kind,
				on_result: async (code, details) => { scanner.stop(); d.hide(); await on_result(code, details); },
				// 打不开：画面收起，原因留在弹窗里（在 LINE 里还会弹授权引导，开好权限回来自动重开）。
				on_error: error => { d.set_df_property("reader", "hidden", true); p.scan_hint(d.fields_dict.hint.$wrapper, error, restart); },
				on_waiting: reason => p.scan_hint(d.fields_dict.hint.$wrapper, reason, restart),
				on_started: () => { d.set_df_property("reader", "hidden", false); d.fields_dict.hint.$wrapper.empty(); p.scan_guide_close(); },
			});
			await scanner.start();
			d.set_df_property("torch", "hidden", !scanner.has_torch());
		};
		// 授权引导里点「重试」或从设置切回来：关掉还在等的那次，重开相机。
		const restart = () => { if (closed) return; scanner?.stop(); void open(); };
		await open();
	};

	// ── 选择框里的扫码：「扫到匹配后自动带出」──────────────────────────────────
	// 共用层 `link.js` 把按钮放在选择框右侧（`p.attach_scan_btn`），这里给动作：
	// 扫到一个码 → 用**主数据那条查接口**（编码/条码/二维码/售卖单位条码四路）查它是哪条物料
	// → 填进选择框 → 该字段自己的 `onchange` 把说明/单位/单价/仓库/地点带出来。
	// 查不到就照实说（橙色提示 + 出错音），不静默。
	p.scan_doctypes = ["ERP Product"];
	p.scan_link = async control => {
		const t = await p.texts("product.");
		if (!p.scan_supported()) return frappe.msgprint(t("product.scan.unsupported"));
		return p.scan({ kind: "all", on_result: async code => {
			const found = await p.call("pearnly_erp_portal.products.service.api_find_product",
				{ value: code });
			if (!found?.name) {
				frappe.show_alert({ message: t("product.scan.notFound", code), indicator: "orange" });
				p.scan_result_sound(false);
				return;
			}
			// 填进选择框；后面"带出"交给字段自己的 onchange（各页自己那份），不在这里重复一遍。
			await control.set_value(found.name);
			p.scan_result_sound(true);
		} });
	};
})();
