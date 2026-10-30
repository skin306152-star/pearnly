/* 入库 / 出库 / 调拨（同一份代码，方向不同；调拨 = 多仓闭环 P2，同公司两仓之间，单头多一组「调入仓库 + 调入库位」）。
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
		// 页面名与注册名一致（`pearnly_stock_in.js` / `pearnly_stock_out.js` / `pearnly_stock_transfer.js`）。
		const transfer = direction === "transfer";
		const root = { in: "pearnly-stock-in", out: "pearnly-stock-out", transfer: "pearnly-stock-transfer" }[direction];
		const nav_label = { in: "nav.stockIn", out: "nav.stockOut", transfer: "nav.stockTransfer" }[direction];
		// 预填本人上次选的仓（`warehouses.prefill` 的单据类型）
		const prefill_kind = transfer ? "transfer" : `stock_${direction}`;
		const SECOND = "new";
		const canWrite = frappe.model.can_write("ERP Stock Entry");
		const url = (...parts) => p.subroute(root, ...parts);
		const title_new = () => t({ in: "trade.newIn", out: "trade.newOut", transfer: "trade.newTransfer" }[direction]);
		const express_label = state => state ? __({ queued: "Queued", written: "express.state.written", mismatch: "Mismatch",
			held: "Pending", failed: "Failed" }[state] || state) : "";
		// 出库不填单价：旧线上出库单是按**结存成本**计价的（`contracts.issue()`），
		// 单价那一格在界面上只读、由服务端在过账时算，所以这里不给人填 —— 但**只锁那一格**，
		// 商品行本身要能加能改。
		const priced = direction === "in";
		// 弹窗点「全屏编辑」时把已填的值带进二级页面（两边在同一个闭包里，不必写进 URL）。
		let carry = null;
		let list, form, lines = null, head = null, page_files = null;

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
			// 单头「仓库 + 库位」（多仓闭环 P1，采购 / 销售 / 期初 / 盘点同一套 `p.place`）：表头一改，每一行里那格仓库镜像跟着改，
			// 库位还跟着表头的行一起换。
			...p.place.fields({ labels: transfer ? { warehouse: t("trade.fromWarehouse"), location: t("trade.fromLocation") }
				: { warehouse: t("trade.warehouse"), location: t("trade.location") }, editable, reqd: true,
				warehouse: doc.warehouse, location: doc.location, current: () => head?.fields_dict?.warehouse?.get_value(),
				onchange: key => place?.changed(key) }),
			// 调拨：调入仓库 + 调入库位（同一家公司的另一个仓；服务端核同公司、不同仓、调入库位在调入仓的清单里）
			...(transfer ? p.place.fields({ labels: { warehouse: t("trade.toWarehouse"), location: t("trade.toLocation") }, editable,
				reqd: true, warehouse: doc.to_warehouse, location: doc.to_location,
				keys: { warehouse: "to_warehouse", location: "to_location" },
				current: () => head?.fields_dict?.to_warehouse?.get_value(), onchange: key => to_place?.changed(key) }) : []),
			...(editable
				// 明细的宿主（HTML 格）与"添加明细"那颗官方按钮：真正的控件由 `mount_lines` 建。
				? [{ fieldtype: "HTML", fieldname: "lines" },
					{ fieldtype: "Button", fieldname: "add_line", label: t("trade.action.addLine"),
						click: () => lines?.add() }]
				: [p.trade.lines(t, { stock: true, cost: true, readonly: true, locked_price: !priced, to_location: transfer,
					data: doc.lines || [] })]),
			// 用途（录制问题 #14）：入库是退库，出库是内部领用 / 报损；可以不填。调拨没有用途。
			// 只读时官方 Select 显示的是原值（other / internal_use），已提交的单改用文字框显示译文
			...(transfer ? [] : [editable ? { fieldtype: "Select", fieldname: "purpose", label: __("stock.purpose.label"),
				options: [{ value: "", label: "" }, ...purpose_options()], default: doc.purpose || "" }
				: { fieldtype: "Data", fieldname: "purpose", label: __("stock.purpose.label"), default: purpose_label(doc.purpose) }]),
			{ fieldtype: "Small Text", fieldname: "reason", label: t("trade.note"),
				default: doc.reason },
		].map(field => ({ ...field, read_only: editable ? 0 : 1 }))
			// 单据号永远是只读的（上面那次 map 会把它放开）。
			.map(field => (field.fieldname === "doc_no" ? { ...field, read_only: 1 } : field));

		// 行里的「仓库」= 表头那一个（镜像，只读）；表头的仓库 / 库位一变，行跟着变（`p.place.track` → `lines.follow`）。
		// 新单的仓按服务端 `warehouses.prefill` 预填（本人上次 → 卖货仓（只给入库）→ 唯一仓 → 空 = 必须选），选物料不再改它。
		let place = null, to_place = null;
		// 把逐条明细挂到某一处（弹窗 / 整页），并把实例记在闭包上（按钮、取值、搬家都要用）。
		function mount_lines(container, rows, target) {
			head = target;
			const value = key => head?.fields_dict?.[key]?.get_value() || "";
			lines = p.trade.lines_editor(t, { cost: true, locked_price: !priced, data: rows || [],
				warehouse: () => value("warehouse"), location: () => value("location"),
				...(transfer ? { to_warehouse: () => value("to_warehouse"), to_location: () => value("to_location") } : {}) });
			place = p.place.track(head, () => lines.rows());
			to_place = transfer ? p.place.track(head, () => lines.to_rows(),
				{ keys: { warehouse: "to_warehouse", location: "to_location" } }) : null;
			container.empty().append(lines.host);
			const initial = (rows && rows.length) ? rows : [{ qty: 1 }];
			initial.forEach(row => lines.add(row));
			return lines;
		}

		// 按台管理的商品提交前逐行登记 / 选台（`p.serials`）：出库、调拨选这个仓（这一行库位）在库的台，入库「退货」收回已售的台，
		// 其余入库登记新台
		const units = (lines_, values, way) => p.serials.collect(lines_, way === "out" || way === "transfer"
			? { mode: "pick", filters: { warehouse: values.warehouse, location: values.location || "" } }
			: { mode: values.purpose === "return" ? "return" : "register" });
		const clean = values => ({ posting_date: values.posting_date,
			warehouse: values.warehouse, location: values.location || "", purpose: values.purpose || "", reason: values.reason,
			...(transfer ? { to_warehouse: values.to_warehouse, to_location: values.to_location || "" } : {}),
			lines: p.trade.clean(values.lines) });
		const PURPOSES = { in: ["return", "other"], out: ["internal_use", "damage", "other"] };
		// 这页的 `t` 只加载 `trade.` 前缀；用途的词条走原生 `__()`（键名词条同样认得）。
		const purpose_options = () => (PURPOSES[direction] || []).map(value => ({ value,
			label: __(`stock.purpose.${value}`) }));
		const purpose_label = value => (value ? __(`stock.purpose.${value}`) : "");

		// ── 附件：入库 / 出库附图片、PDF 或当场拍照（岳父 10-25，`inventory.attachments`）─────────────────────
		// 上传框是官方 FileUploader（拍照、多选），照片先压缩，传到统一原件层（`erp-originals.js`）。新建 / 草稿时选的先挂在
		// 页面上、提交时一起挂到单；已提交的单选了就挂上，只能加不能删；作废的单不再加。每张单最多 10 个（服务端同样拦）。
		const ATTACH_MAX = 10;
		const attachment_fields = can_attach => [
			{ fieldtype: "Section Break", label: __("Attachments") },
			{ fieldtype: "HTML", fieldname: "attachment_list" },
			...(can_attach ? [{ fieldtype: "Button", fieldname: "attach", label: __("Attach") }] : []),
		];
		function attachments_area(target, { name = null, status = "draft", can_attach = false } = {}) {
			let kept = [], pending = [], file_list = null;
			const live = Boolean(name) && status !== "draft";
			const render = async () => {
				file_list?.destroy?.();
				const host = target.fields_dict.attachment_list?.$wrapper;
				if (!host) return;
				const rows = [...kept.map(row => ({ ...row, kept: 1 })), ...pending];
				if (!rows.length) { host.empty(); return; }
				file_list = p.list({ page, container: host, compact_columns: true, selectable: false, doctype: "File",
					load: async () => ({ items: rows, total: rows.length }),
					on_open: row => window.open(row.file_url, "_blank", "noopener,noreferrer"),
					columns: [{ fieldname: "file_name", label: __("File Name"), width: 260, min_width: 120 }],
					row_actions: row => (!can_attach || (row.kept && live)) ? [] : [{ id: "delete", label: __("Delete"),
						action: () => frappe.confirm(__("Are you sure you want to delete the attachment?"), async () => {
							if (row.kept) kept = await p.call(api + "api_remove_stock_entry_file", { name, file: row.name }, "POST");
							else {
								await frappe.call({ method: "frappe.client.delete", args: { doctype: "File", name: row.name }, type: "POST" });
								pending = pending.filter(item => item.name !== row.name);
							}
							frappe.utils.play_sound("delete");
							await render();
						}) }] });
				await file_list.ready;
				file_list.$paging_area.hide();
			};
			const on_success = async file => {
				if (!file?.name) return;
				if (live) kept = await p.call(api + "api_add_stock_entry_files", { name, files: [file.name] }, "POST");
				else if (!pending.some(row => row.name === file.name)) {
					pending.push({ name: file.name, file_url: file.file_url, file_name: file.file_name });
				}
				await render();
			};
			const pick = () => {
				if (kept.length + pending.length >= ATTACH_MAX) { frappe.msgprint(__("stock.attachmentLimit", [ATTACH_MAX])); return; }
				const uploader = new frappe.ui.FileUploader({ allow_multiple: true, allow_web_link: false, allow_google_drive: false,
					disable_file_browser: true, allow_take_photo: true, allow_toggle_private: false, make_attachments_public: false,
					allow_toggle_optimize: false, on_success,
					restrictions: { allowed_file_types: ["image/*", ".pdf"], max_file_size: 20 * 1024 * 1024 } });
				p.erp_upload_with(uploader, `stock_${direction}`, on_success, async () => {
					for (const item of uploader.uploader.files) {
						if (!item.file_obj || item.shrunk) continue;
						const smaller = await p.shrink_photo(item.file_obj);
						if (smaller !== item.file_obj) Object.assign(item, { file_obj: smaller, name: smaller.name });
						item.shrunk = true;
					}
				});
			};
			if (target.fields_dict.attach) target.fields_dict.attach.df.click = pick;
			return {
				async load() {
					if (name) kept = await p.call(api + "api_stock_entry_files", { name });
					await render();
				},
				names: () => pending.map(row => row.name),
				carry: () => pending.slice(),
				restore(rows) { pending = (rows || []).slice(); },
			};
		}

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
				if (canWrite) page.set_primary_action(title_new(), () => editor()); // entry: trade.newIn, trade.newOut, trade.newTransfer
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
					{ fieldname: "warehouse_name", width: 150, min_width: 120, label: t(transfer ? "trade.fromWarehouse" : "trade.warehouse") },
					...(transfer ? [{ fieldname: "to_warehouse_name", width: 150, min_width: 120, label: t("trade.toWarehouse") }] : []),
					{ fieldname: "status", width: 90, min_width: 74, label: t("trade.status"), format: value => __(value) },
					// 备注前面带上用途（五列一屏放得下，不另占一列）。
					{ fieldname: "reason", width: 210, min_width: 150, label: t("trade.note"),
						format: (value, row) => [purpose_label(row?.purpose), value].filter(Boolean).join(" · ") },
					// 接了事务所 Express 账本的站点：写进去了显示 Express 单号，否则显示状态（同采购 / 销售列表）
					...(frappe.boot.pearnly_express ? [{ fieldname: "express_docnum", width: 110, min_width: 76,
						label: __("Express"), format: (value, row) => value || express_label(row?.express_state) }] : []),
				],
			});
			restore_new();
		}

		// ── 弹窗：新建 / 查看 / 提交 ─────────────────────────────────────────────

		async function editor(name) {
			const doc = name ? await p.call(api + "api_get_stock_entry", { name }) : {};
			// 草稿（含老数据留下的）可以提交；已提交/已作废只能看。
			const editable = canWrite && (!name || doc.status === "draft");
			if (editable && !doc.warehouse) doc.warehouse = await p.place.prefill(prefill_kind);
			const posting_date = doc.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default(doc, posting_date);
			let d;
			const can_attach = canWrite && doc.status !== "void";
			const fields = [...entry_fields({ doc, doc_no, editable }), ...attachment_fields(can_attach)];
			// 写进 Express 的情况（状态、单号、原因、再写一次）：采购 / 销售同一个弹窗
			// 已写进 Express 后又作废的：同一个弹窗里看 Express 作废原单的结果（转人工的能重试）
			if ((doc.status === "submitted" || doc.express_void_state) && doc.express_state && frappe.boot.pearnly_express) fields.push({
				fieldtype: "Button", fieldname: "express", label: __("Express"),
				click: () => p.express("ERP Stock Entry", name, () => list?.reload()) });
			// 已提交 / 已作废的单能打印：出库 → 领料单、入库 → 收货单（作废单带「已作废」水印）
			// 调拨还没有打印格式（没有样板，多仓闭环 P2），不出「打印」
			if (name && doc.status !== "draft" && !transfer) fields.push({ fieldtype: "Button", fieldname: "print", label: __("Print"),
				click: () => p.trade.print("ERP Stock Entry", name,
					doc.direction === "out" ? "Pearnly Goods Issue Slip" : "Pearnly Goods Receipt Note") });
			if (doc.status === "submitted" && canWrite) fields.push({ fieldtype: "Button",
				fieldname: "void", label: t("trade.void"),
				click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_stock_entry", { name }, "POST");
					d.hide(); await list.reload(); await editor(name);
				})) });
			d = p.dialog(name ? (doc.doc_no || name) : title_new(), fields, editable ? t("trade.action.submit") : t("trade.close"),
				async (values, dialog) => {
					if (!editable) { dialog.hide(); return; }
					// 明细不在弹窗的 `get_values()` 里（它们是逐条的小表单），单独取；
					// 必填没填时它会展开那一段并返回 null —— 那就别提交。
					const rows = lines ? lines.values() : values.lines;
					if (rows === null) return;
					if (!await units(rows, values, doc.direction || direction)) return;
					// 接了 Express：这家公司这个单别的科目要人选一次时当场弹窗（同采购 / 销售提交前）；关掉弹窗不提交
					if (!await p.express_preflight("ERP Stock Entry", { direction: doc.direction || direction, ...clean({ ...values, lines: rows }) })) return;
					// 一次调用就建单/改单 + 过账；完了回列表，不停在这一页。
					const result = await p.call(api + (name ? "api_update_stock_entry" : "api_create_stock_entry"), {
						...(name ? { name } : { direction }), submit: 1, ...clean({ ...values, lines: rows }),
						attachments: files.names(),
					}, "POST");
					dialog.hide();
					p.success(t("trade.notice.submitted", result.doc_no || ""));
					await list?.reload();
				});
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
			const files = attachments_area(d, { name, status: doc.status || "draft", can_attach });
			await files.load();
			// 明细：录入时是**逐条摊开**的（见 `mount_lines`）。
			if (editable) mount_lines(d.fields_dict.lines.$wrapper, doc.lines || null, d);
			// 只有新建（或还没提交的草稿）才摊成整页；已提交的单据点开就是看。
			if (editable) {
				// 搬到整页去改：明细也一起带走（这里**不校验**，过去了再改）。
				p.edit_all(d, t("trade.action.fullPage"), url(SECOND), values => {
					carry = { ...values, lines: lines ? lines.values({ check: false }) : values.lines, attachments: files.carry() };
				});
			}
		}

		// ── 二级页面：同一套字段摊成整页，提交即过账 ──────────────────────────────

		async function open_new() {
			p.breadcrumbs({ label: __(nav_label),
				route: url(), trail: [{ label: title_new(), route: "" }] });
			const values = carry || {};
			carry = null;
			if (!values.warehouse) values.warehouse = await p.place.prefill(prefill_kind);
			// 带过来的明细行（弹窗里已经填好的那些）原样铺成逐条的小表单。
			const data = (values.lines?.length ? values.lines : null) || [{ qty: 1 }];
			const posting_date = values.posting_date || frappe.datetime.get_today();
			const doc_no = await doc_no_default({}, posting_date);
			if (frappe.get_route()[0] !== root) return;
			form = p.layout(page.main, [...entry_fields({ doc: values, doc_no, editable: true, data }), ...attachment_fields(true)]);
			mount_lines(form.fields_dict.lines.$wrapper, data, form);
			page_files = attachments_area(form, { can_attach: true });
			page_files.restore(values.attachments);
			await page_files.load();
			page.set_primary_action(t("trade.action.submit"), () => void submit_new());
		}

		async function submit_new() {
			const values = entry_values();
			if (!values) return;
			if (!await units(values.lines, values, direction)) return;
			if (!await p.express_preflight("ERP Stock Entry", { direction, ...values })) return;
			const result = await p.call(api + "api_create_stock_entry",
				{ direction, submit: 1, ...values, attachments: page_files?.names() || [] }, "POST");
			await list?.reload();
			p.success(t("trade.notice.submitted", result.doc_no || ""));
			// 提交完回列表，不在编辑页停留。
			p.redirect_up(root);
		}

		// ── 路由 ────────────────────────────────────────────────────────────────

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null; lines = null; head = null; page_files = null;
			if (!rest.length) { open_list(); return; }
			if (rest[0] !== SECOND || !canWrite) { p.redirect_up(root); return; }
			await open_new();
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
