/* 入库 / 出库（同一份代码，方向不同）。
 *
 * 两条路（用户 2026-09-22 口径）：
 *   /desk/pearnly-stock-in        列表；点行 = 弹窗查看（已提交的只读）/ 提交（草稿）
 *   「新建入库单」= 弹窗：单据编号（只读，显示当前要发的号）、日期、仓库、明细、备注
 *   弹窗左下角「全屏编辑」= 官方 `QuickEntryForm` 同一颗 custom action → 二级页面
 *   /desk/pearnly-stock-in/new    二级页面：同样的字段摊成整页，明细表用原生 Grid
 *
 * **没有"保存草稿"这一步**：两级都只有一颗「提交」，提交即过账（服务端 `submit=1`，
 * 建单与过账同一事务），提交后直接回列表 —— 不停在弹窗、也不停在编辑页。
 * 老数据里留下的草稿仍然可以打开，弹窗那颗按钮同样会把它提交掉。
 *
 * 仓库是**自由填写**（Data，不再是官方的 ERP Warehouse 选择器）：服务端按名字找，
 * 找不到就建一个（`warehouses.resolve`），与"仓位"同一个口径。
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
		// 出库不填单价：旧线上出库单是按**结存成本**计价的（`contracts.issue()`），
		// 单价那一格在界面上只读、由服务端在过账时算，所以这里不给人填 —— 但**只锁那一格**，
		// 商品行本身要能加能改。
		const priced = direction === "in";
		// 弹窗点「全屏编辑」时把已填的值带进二级页面（两边在同一个闭包里，不必写进 URL）。
		let carry = null;
		let list, form, lines = null, head = null, refresh_icon = null;

		// 刷新那颗图标只在列表那一级出现：二级页面里有没提交的输入，一颗刷新把它清掉不合适。
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
		// 明细：**能录入的时候不用表格** —— 一条一段的官方控件（用户 2026-09-23："出库和入库
		// 界面添加物料那个位置不要用表格，拆出来"），见 `p.trade.lines_editor`；已提交 / 已作废
		// 的单据只是看，仍旧用只读的那张表（那是"看"，不是"加物料"）。
		const entry_fields = ({ doc = {}, doc_no, editable, data }) => [
			{ fieldtype: "Data", fieldname: "doc_no", label: t("trade.docNo"), read_only: 1,
				default: doc_no, description: doc.doc_no ? "" : t("stock.field.docNoAuto") },
			{ fieldtype: "Date", fieldname: "posting_date", label: t("trade.date"),
				default: doc.posting_date || frappe.datetime.get_today(), reqd: 1 },
			// 仓库用**官方原生**的 Link 到 `ERP Warehouse`（用户 2026-09-23："我说用官方原生，
			// 这是第四遍了"）：下拉里只列**当前公司**的仓库；没找到时 Link 自己会带
			// 「Create a new ERP Warehouse」（框架原生行为）。服务端只认已有的仓库。
			{ fieldtype: "Link", options: "ERP Warehouse", fieldname: "warehouse",
				label: t("trade.warehouse"), default: doc.warehouse, reqd: 1,
				get_query: p.warehouse_query(), only_select: true,
				// 表头一改，每一行里那格镜像跟着改（行里的仓库是**同一个**仓库，只是也显示在行里）。
				onchange: () => sync_lines_warehouse() },
			...(editable
				// 明细的宿主（HTML 格）与"添加明细"那颗官方按钮：真正的控件由 `mount_lines` 建。
				? [{ fieldtype: "HTML", fieldname: "lines" },
					{ fieldtype: "Button", fieldname: "add_line", label: t("trade.action.addLine"),
						click: () => lines?.add() }]
				: [p.trade.lines(t, { stock: true, cost: true, readonly: true, locked_price: !priced,
					data: doc.lines || [] })]),
			// 用途（录制问题 #14）：入库是退库，出库是内部领用 / 报损；可以不填。
			{ fieldtype: "Select", fieldname: "purpose", label: __("stock.purpose.label"),
				options: [{ value: "", label: "" }, ...purpose_options()], default: doc.purpose || "" },
			{ fieldtype: "Small Text", fieldname: "reason", label: t("trade.note"),
				default: doc.reason },
		].map(field => ({ ...field, read_only: editable ? 0 : 1 }))
			// 单据号永远是只读的（上面那次 map 会把它放开）。
			.map(field => (field.fieldname === "doc_no" ? { ...field, read_only: 1 } : field));

		// 行里的「仓库」= 表头那一个（镜像，只读）。所以：
		//   · 表头变了 → 每一行跟着变（`sync_lines_warehouse`）；
		//   · 选物料时，**这家公司卖货的那个仓**建议给表头（只在表头还空着时填，不覆盖用户
		//     选的）。商品上那个"默认仓"已经删掉 —— 进货与卖货得是同一个仓，数量与金额
		//     才对得上（用户 2026-09-24）。
		function sync_lines_warehouse() {
			lines?.set_warehouse(head?.fields_dict?.warehouse?.get_value() || "");
		}
		function suggest_warehouse(name) {
			const control = head?.fields_dict?.warehouse;
			if (!name || !control) return;
			if (String(control.get_value() || "").trim()) return;
			void control.set_value(name);
			sync_lines_warehouse();
		}

		// 把逐条明细挂到某一处（弹窗 / 整页），并把实例记在闭包上（按钮、取值、搬家都要用）。
		function mount_lines(container, rows, target) {
			head = target;
			lines = p.trade.lines_editor(t, { cost: true, locked_price: !priced, data: rows || [],
				suggest_warehouse, warehouse: () => head?.fields_dict?.warehouse?.get_value() || "" });
			container.empty().append(lines.host);
			const initial = (rows && rows.length) ? rows : [{ qty: 1 }];
			initial.forEach(row => lines.add(row));
			sync_lines_warehouse();
			return lines;
		}

		const clean = values => ({ posting_date: values.posting_date,
			warehouse: values.warehouse, purpose: values.purpose || "", reason: values.reason,
			lines: p.trade.clean(values.lines) });
		const PURPOSES = { in: ["return", "other"], out: ["internal_use", "damage", "other"] };
		// 这页的 `t` 只加载 `trade.` 前缀；用途的词条走原生 `__()`（键名词条同样认得）。
		const purpose_options = () => (PURPOSES[direction] || []).map(value => ({ value,
			label: __(`stock.purpose.${value}`) }));
		const purpose_label = value => (value ? __(`stock.purpose.${value}`) : "");

		function entry_values() {
			const values = form.get_values();
			if (!values) return null;
			// 明细不在 `get_values()` 里（它们是这一段小表单），单独取；必填没填时它会展开那一段
			// 并返回 null —— 那就别提交，让用户看到是哪一条。
			const rows = lines ? lines.values() : values.lines;
			if (rows === null) return null;
			return clean({ ...values, lines: rows });
		}

		// ── 列表 ────────────────────────────────────────────────────────────────

		function open_list() {
			// 没选中行时右上角还是原来那颗"新建入库/出库"（打开快捷弹窗）。
			// 只在**列表那一级**挂这颗主按钮：列表的异步刷新可能在我们已经切进二级页面（新建）
			// 之后才落地，那一笔 paint 会把"新建入库/出库单"盖到二级页面右上角（同盘点那个坑）。
			function restore_new() {
				if (frappe.get_route().length !== 1) return;
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
				// 作废是"取消"类动作，官方放 `cancel`（不是"保存成功"那一声）。
				sound: "cancel",
			});
			// 五列一屏放得下、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到"）。
			list = p.list({ page, doctype: "ERP Stock Entry", selectable: true, compact_columns: true,
				on_selection: selection,
				on_open: row => editor(row.name),
				empty_action: canWrite ? { label: title_new(), action: () => editor() } : null,
				// 列头点一下排序（`sort_fields` 用页面上的列名，服务端白名单里一一对应）。
				// 「备注」列不在里面：那个字段（`note`）受字段级权限保护，服务端排不了（见
				// `inventory/documents.py` 的白名单注释），点了只会报错，所以干脆不开放。
				sort_fields: ["doc_no", "posting_date", "warehouse_name", "status", "creation"],
				load: p.trade.load(api, "api_list_stock_entries", "api_stock_entry_total", () => ({ direction })),
				columns: [
					// 首列是**单据编号**（系统发的 IR/IS 号）：旧线上列表也是拿它当行标题，
					// 内部单据名只在点开单据时用，不占一列。
					// 老单据（编号是后来才发的）没有号，落回内部单据名，别留一列空白。
					{ fieldname: "doc_no", width: 150, min_width: 128, label: t("trade.docNo"),
						format: (_value, row) => row.doc_no || row.name },
					{ fieldname: "posting_date", width: 110, min_width: 96, label: t("trade.date") },
					{ fieldname: "warehouse_name", width: 150, min_width: 120, label: t("trade.warehouse") },
					{ fieldname: "status", width: 90, min_width: 74, label: t("trade.status"), format: value => __(value) },
					// 备注前面带上用途（五列一屏放得下，不另占一列）。
					{ fieldname: "reason", width: 210, min_width: 150, label: t("trade.note"),
						format: (value, row) => [purpose_label(row?.purpose), value].filter(Boolean).join(" · ") },
				],
			});
			restore_new();
			set_refresh(true);
		}

		// ── 弹窗：新建 / 查看 / 提交 ─────────────────────────────────────────────

		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_stock_entry", { name }) : {};
			// 草稿（含老数据留下的）可以提交；已提交/已作废只能看。
			const editable = canWrite && (!name || doc.status === "draft");
			const posting_date = doc.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default(doc, posting_date);
			let d;
			const fields = entry_fields({ doc, doc_no, editable });
			if (doc.status === "submitted" && canWrite) fields.push({ fieldtype: "Button",
				fieldname: "void", label: t("trade.void"),
				click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				})) });
			d = p.dialog(name || title_new(), fields, editable ? t("trade.action.submit") : t("trade.close"),
				async (values, dialog) => {
					if (!editable) { dialog.hide(); return; }
					// 明细不在弹窗的 `get_values()` 里（它们是逐条的小表单），单独取；
					// 必填没填时它会展开那一段并返回 null —— 那就别提交。
					const rows = lines ? lines.values() : values.lines;
					if (rows === null) return;
					// 一次调用就建单/改单 + 过账；完了回列表，不停在这一页。
					const result = await p.call(api + (name ? "api_update_stock_entry" : "api_create_stock_entry"), {
						...(name ? { name } : { direction }), submit: 1, ...clean({ ...values, lines: rows }),
					}, "POST");
					dialog.hide();
					p.success(t("trade.notice.submitted", result.doc_no || ""));
					await list?.reload();
				});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
			// 明细：录入时是**逐条摊开**的（见 `mount_lines`）。
			if (editable) mount_lines(d.fields_dict.lines.$wrapper, doc.lines || null, d);
			// 只有新建（或还没提交的草稿）才摊成整页；已提交的单据点开就是看。
			if (editable) {
				// 搬到整页去改：明细也一起带走（这里**不校验**，过去了再改）。
				p.edit_all(d, t("trade.action.fullPage"), url(SECOND), values => {
					carry = { ...values, lines: lines ? lines.values({ check: false }) : values.lines };
				});
			}
		}

		// ── 二级页面：同一套字段摊成整页，提交即过账 ──────────────────────────────

		async function open_new() {
			set_refresh(false);
			p.breadcrumbs({ label: __(direction === "in" ? "nav.stockIn" : "nav.stockOut"),
				route: url(), trail: [{ label: title_new(), route: "" }] });
			const values = carry || {};
			carry = null;
			// 带过来的明细行（弹窗里已经填好的那些）原样铺成逐条的小表单。
			const data = (values.lines?.length ? values.lines : null) || [{ qty: 1 }];
			const posting_date = values.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default({}, posting_date);
			if (frappe.get_route()[0] !== root) return;
			form = p.layout(page.main, entry_fields({ doc: values, doc_no, editable: true, data }));
			mount_lines(form.fields_dict.lines.$wrapper, data, form);
			page.set_primary_action(t("trade.action.submit"), () => void submit_new());
		}

		async function submit_new() {
			const values = entry_values();
			if (!values) return;
			const result = await p.call(api + "api_create_stock_entry",
				{ direction, submit: 1, ...values }, "POST");
			await list?.reload();
			p.success(t("trade.notice.submitted", result.doc_no || ""));
			// 提交完回列表，不在编辑页停留。
			p.redirect_up(root);
		}

		// ── 路由 ────────────────────────────────────────────────────────────────

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null; lines = null; head = null;
			set_refresh(false);
			if (!rest.length) { open_list(); return; }
			if (rest[0] !== SECOND || !canWrite) { p.redirect_up(root); return; }
			await open_new();
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
