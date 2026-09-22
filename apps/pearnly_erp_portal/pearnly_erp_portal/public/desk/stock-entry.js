/* 入库 / 出库（同一份代码，方向不同）。
 *
 * 两条路（用户 2026-09-22 口径："弹窗只是快捷添加方式……左下角是全屏编辑，点进去才是
 * 二级面包屑"）：
 *   /desk/pearnly-stock-in        列表；点行 = 原生弹窗查看/编辑/过账（查看是弹窗）
 *   「新建入库单」= 弹窗：单据编号（只读，显示当前要发的号）、日期、仓库、明细、备注
 *   弹窗左下角「全屏编辑」= 官方 `QuickEntryForm` 同一颗 custom action → 二级页面
 *   /desk/pearnly-stock-in/new    二级页面：同样的字段摊成整页，明细表用原生 Grid
 *
 * 单据号由服务端发（入库 IR / 出库 IS，类型码 + 佛历年 + 六位流水），界面上只读 ——
 * 收发存报表"关联单据"那一列显示的就是它，口径见 `numbering.contracts`。
 */
(() => {
	const p = frappe.pearnly;

	p.stockEntry = async (page, t, direction, wrapper) => {
		const api = "pearnly_erp_portal.inventory.api.";
		// 页面名与注册名一致（`pearnly_stock_in.js` / `pearnly_stock_out.js`）。
		const root = direction === "in" ? "pearnly-stock-in" : "pearnly-stock-out";
		const SECOND = "new";
		const canWrite = frappe.model.can_write("ERP Stock Entry");
		const url = (...parts) => p.subroute(root, ...parts);
		const title_new = () => t(direction === "in" ? "trade.newIn" : "trade.newOut");
		// 出库不填单价：旧线上出库单是按**结存成本**计价的（`apply_issue_costs`），
		// 单价那一格在界面上只读、由服务端在过账时算，所以这里不给人填 —— 但**只锁那一格**，
		// 商品行本身要能加能改（早先整张表都锁了，出库单连商品都加不进去）。
		const priced = direction === "in";
		// 弹窗点「全屏编辑」时把已填的值带进二级页面（两边在同一个闭包里，不必写进 URL）。
		let carry = null;
		let list, form;
		let refresh_icon = null;

		// 刷新那颗图标只在列表那一级出现：二级页面里有没保存的输入，一颗刷新把它清掉不合适。
		function set_refresh(on) {
			if (on && !refresh_icon) refresh_icon = p.add_refresh(page, () => void list?.reload());
			if (!on && refresh_icon) { refresh_icon.remove(); refresh_icon = null; }
		}

		// 单据号的那一格：新建时把**当前要发的号**显示出来（只读预览，不占号）—— 号由系统发、
		// 不给改，但要看得见（旧系统 mrerp 的表单也是把号填进只读输入框）。
		async function doc_no_default(doc, posting_date) {
			return doc.doc_no
				|| (await p.call(api + "api_next_stock_entry_number",
					{ direction, posting_date })).doc_no;
		}

		// 字段顺序与旧线上入库/出库单据一致：单据号、日期、仓库，然后明细与说明。
		// `doc_no` 不进请求体：单据编号由服务端发，界面上只读。
		const entry_fields = ({ doc = {}, doc_no, editable, data }) => [
			{ fieldtype: "Data", fieldname: "doc_no", label: t("trade.docNo"), read_only: 1,
				default: doc_no, description: doc.doc_no ? "" : t("stock.field.docNoAuto") },
			{ fieldtype: "Date", fieldname: "posting_date", label: t("trade.date"),
				default: doc.posting_date || frappe.datetime.get_today(), reqd: 1 },
			{ fieldtype: "Link", fieldname: "warehouse", label: t("trade.warehouse"),
				options: "ERP Warehouse", default: doc.warehouse, reqd: 1 },
			p.trade.lines(t, { stock: true, cost: true, readonly: !editable, locked_price: !priced,
				data: data || doc.lines || [{ qty: 1 }] }),
			{ fieldtype: "Small Text", fieldname: "reason", label: t("trade.note"),
				default: doc.reason },
		].map(field => ({ ...field, read_only: editable ? 0 : 1 }))
			// 单据号永远是只读的（上面那次 map 会把它放开）。
			.map(field => (field.fieldname === "doc_no" ? { ...field, read_only: 1 } : field));

		const clean = values => ({ posting_date: values.posting_date,
			reason: values.reason, lines: p.trade.clean(values.lines) });

		function entry_values() {
			const values = form.get_values();
			if (!values) return null;
			return clean(values);
		}

		// ── 列表 ────────────────────────────────────────────────────────────────

		function open_list() {
			// 没选中行时右上角还是原来那颗"新建入库/出库"（打开快捷弹窗）。
			function restore_new() {
				if (canWrite) page.set_primary_action(title_new(), () => editor());
				else page.clear_primary_action();
			}
			// 选中若干张**已过账**的单据 → 右上角出现"作废选中"。库存流水不改不删，作废是追加
			// 反向流水（服务端 `api_void_stock_entry` 同一条路径），所以这里给的不是删除。
			const selection = p.selection_action({
				page, list: () => list, empty: restore_new,
				label: rows => t("trade.action.voidCount", rows.length),
				when: row => row.status === "submitted",
				reason: () => t("trade.voidBlocked"),
				confirm: rows => t("trade.voidConfirmCount", rows.length),
				run: async rows => {
					for (const row of rows) {
						await p.call(api + "api_void_stock_entry", { name: row.name }, "POST");
					}
				},
				success: rows => t("trade.voidNotice", rows.length),
			});
			list = p.list({ page, doctype: "ERP Stock Entry", selectable: true, on_selection: selection,
				on_open: row => editor(row.name),
				empty_action: canWrite ? { label: title_new(), action: () => editor() } : null,
				load: p.trade.load(api, "api_list_stock_entries", "api_stock_entry_total", () => ({ direction })),
				columns: [
					// 首列是**单据编号**（系统发的 IR/IS 号）：旧线上列表也是拿它当行标题，
					// 内部单据名只在点开单据时用，不占一列。
					// 老单据（编号是后来才发的）没有号，落回内部单据名，别留一列空白。
					{ fieldname: "doc_no", label: t("trade.docNo"),
						format: (_value, row) => row.doc_no || row.name },
					{ fieldname: "posting_date", label: t("trade.date") },
					{ fieldname: "warehouse_name", label: t("trade.warehouse") },
					{ fieldname: "status", label: t("trade.status"), format: value => __(value) },
					{ fieldname: "reason", label: t("trade.note") },
				],
			});
			restore_new();
			set_refresh(true);
		}

		// ── 弹窗（查看/编辑/过账；新建也是它）──────────────────────────────────

		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_stock_entry", { name }) : {};
			const editable = canWrite && (!name || doc.status === "draft");
			const posting_date = doc.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default(doc, posting_date);
			let d;
			const fields = entry_fields({ doc, doc_no, editable });
			if (name && editable) fields.push({ fieldtype: "Button", fieldname: "submit",
				label: t("trade.submit"), click: () => p.trade.act(d, async () => {
					const values = d.get_values(); if (!values) return;
					await p.call(api + "api_update_stock_entry", { name, ...clean(values) }, "POST");
					await p.call(api + "api_submit_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				}) });
			if (doc.status === "submitted" && canWrite) fields.push({ fieldtype: "Button",
				fieldname: "void", label: t("trade.void"),
				click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				})) });
			d = p.dialog(name || title_new(), fields, editable ? t("trade.save") : t("trade.close"),
				async (values, dialog) => {
					if (!editable) { dialog.hide(); return; }
					const result = await p.call(api + (name ? "api_update_stock_entry" : "api_create_stock_entry"), {
						...(name ? { name } : { direction }), ...clean(values),
					}, "POST");
					dialog.hide(); await list.reload(); await editor(result.name);
				});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
			// 新建那一支才有"全屏编辑"（查看/编辑已经有单据了，再摊成整页没有意义）。
			if (!name && editable) {
				p.edit_all(d, t("trade.action.fullPage"), url(SECOND), values => { carry = values; });
			}
		}

		// ── 二级页面：同一套字段摊成整页 ────────────────────────────────────────

		async function open_new() {
			set_refresh(false);
			p.breadcrumbs({ label: __(direction === "in" ? "nav.stockIn" : "nav.stockOut"),
				route: url(), trail: [{ label: title_new(), route: "" }] });
			const values = carry || {};
			carry = null;
			// 带过来的明细行（弹窗里已经勾好的那些）原样铺进 Grid。
			const data = (values.lines?.length ? values.lines : null) || [{ qty: 1 }];
			const posting_date = values.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default({}, posting_date);
			if (frappe.get_route()[0] !== root) return;
			form = p.layout(page.main, entry_fields({ doc: values, doc_no, editable: true, data }));
			page.set_primary_action(t("trade.save"), () => void save_new());
		}

		async function save_new() {
			const values = entry_values();
			if (!values) return;
			const result = await p.call(api + "api_create_stock_entry",
				{ direction, ...values }, "POST");
			await list?.reload();
			p.redirect_up(root);
			await editor(result.name);
		}

		// ── 路由 ────────────────────────────────────────────────────────────────

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null;
			if (!rest.length) { open_list(); return; }
			if (rest[0] !== SECOND || !canWrite) { p.redirect_up(root); return; }
			await open_new();
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
