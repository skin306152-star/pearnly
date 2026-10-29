/* 按台管理（车辆按台补充设计，主会话 10-26）：单据提交前，按台管理的商品逐行登记 / 选择台。服务端 `inventory.serials`。
 * - 登记（进货、入库「其他」）：原生 Dialog + Table，每台一行（车 = 车架号 / 发动机号 / 颜色）；扫码填进下一格空的号码（车身二维码 = 车架号全号）。
 * - 选择（销售、收银、出库、入库「退货」）：Table 里的原生 Link，下拉走服务端分页搜索（`search_units`）；扫码按号认台。
 * 台数必须等于这一行的数量，服务端同样核对。不开按台的商品不弹。 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_erp_portal.inventory.serials.";
	const products = {};
	const kind_of = item => (item?.serial_kind === "device" ? "device" : "vehicle");
	const LABELS = {
		vehicle: { primary: () => __("serial.vehicle.primary"), secondary: () => __("serial.vehicle.secondary"),
			register: () => __("serial.vehicle.register"), pick: () => __("serial.vehicle.pick") },
		device: { primary: () => __("serial.device.primary"), secondary: () => __("serial.device.secondary"),
			register: () => __("serial.device.register"), pick: () => __("serial.device.pick") },
	};
	const label = (kind, which) => LABELS[kind][which]();

	async function product(name) {
		if (!(name in products)) {
			products[name] = await p.call("pearnly_erp_portal.products.service.api_get_product", { name });
		}
		return products[name];
	}

	function count_of(line) {
		const qty = Number(line.qty || 0);
		return Number.isInteger(qty) && qty > 0 ? qty : 0;
	}

	// 一行的弹窗：`mode` = register（登记新台）/ pick（选在库的台）/ return（收回已售、已出库的台）
	function ask(line, item, mode, filters) {
		const kind = kind_of(item), wanted = count_of(line);
		const register = mode === "register";
		const status = mode === "return" ? "returnable" : "in_stock";
		const existing = (line.serials || []).slice(0, wanted);
		const rows = Array.from({ length: wanted }, (_v, index) => ({ ...(existing[index] || {}) }));
		return new Promise(resolve => {
			let done = false;
			const fields = register ? [
				{ fieldtype: "Data", fieldname: "serial_no", label: label(kind, "primary"), reqd: 1, in_list_view: 1, columns: 4 },
				{ fieldtype: "Data", fieldname: "secondary_no", label: label(kind, "secondary"), in_list_view: 1, columns: 3 },
				{ fieldtype: "Data", fieldname: "color", label: __("serial.color"), in_list_view: 1, columns: 3 },
			] : [
				{ fieldtype: "Link", fieldname: "serial", options: "ERP Serial No", label: label(kind, "primary"), reqd: 1,
					in_list_view: 1, columns: 6,
					get_query: () => ({ query: API + "search_units",
						filters: { product: line.product, status, ...(filters || {}) } }) },
			];
			const dialog = new frappe.ui.Dialog({
				title: `${label(kind, register ? "register" : "pick")} · ${item.product_name || line.product}`,
				size: "large",
				fields: [
					{ fieldtype: "Table", fieldname: "units", label: `${__("serial.units")} · ${wanted}`,
						cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true, data: rows, fields },
				],
				primary_action_label: __("Save"),
				primary_action(values) {
					const picked = (values.units || []).filter(row => String((register ? row.serial_no : row.serial) || "").trim());
					if (picked.length !== wanted) {
						frappe.msgprint(__("The number of units does not match the quantity"));
						return;
					}
					done = true;
					dialog.hide();
					resolve(picked.map(row => (register
						? { serial_no: row.serial_no.trim(), secondary_no: (row.secondary_no || "").trim(), color: (row.color || "").trim() }
						: { serial: row.serial })));
				},
			});
			// 扫码：车身标签二维码就是车架号全号——登记时填进下一格空的号码；选择时按号认台（认出唯一一台才填）
			if (p.scan_supported?.()) {
				dialog.set_secondary_action_label(__("product.action.scan"));
				dialog.set_secondary_action(() => p.scan({ kind: "all", on_result: async code => {
					const table = dialog.fields_dict.units;
					const data = table.grid.get_data();
					const next = data.find(row => !String((register ? row.serial_no : row.serial) || "").trim());
					if (!next) return p.scan_result_sound?.(false);
					if (register) {
						next.serial_no = String(code || "").trim();
					} else {
						const found = await p.call(API + "api_lookup", { code, product: line.product, status, ...(filters || {}) });
						if (found.length !== 1) return p.scan_result_sound?.(false);
						next.serial = found[0].name;
					}
					table.grid.refresh();
					p.scan_result_sound?.(true);
				} }));
			}
			dialog.onhide = () => { if (!done) resolve(null); };
			dialog.show();
		});
	}

	p.serials = {
		/** 提交前：`lines` 里按台管理的商品逐行弹出登记 / 选择，台记在行的 `serials` 上。取消回 null（调用方别提交）。
		 * `mode` 是 register / pick / return 或按行给的函数；`filters` 给选台下拉（比如 `{warehouse}`）。 */
		async collect(lines, { mode = "pick", filters } = {}) {
			// 普通公司不按台（用户 10-29）：不弹登记 / 选台，服务端同样不要台
			if (!p.unit_tracking()) return lines;
			for (const line of lines || []) {
				if (!line?.product) continue;
				const item = await product(line.product);
				if (!item?.track_serial) continue;
				const chosen = await ask(line, item, typeof mode === "function" ? mode(line) : mode, filters);
				if (!chosen) return null;
				line.serials = chosen;
			}
			return lines;
		},
		product,
		label: (item, which) => label(kind_of(item), which),
	};
})();
