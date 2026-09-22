/* 盘点：任务（快照 + 追加式记录 + 差异过账）。
 *
 * 形状照旧项目 Cowork/LINE 那套盘点搬：开一份任务把账面冻结成快照行，员工扫一次记一条
 * （可改可撤销，实盘合计 = 未撤销之和），结束盘点锁住记录，最后过账把差异落成流水。
 * 手机那一侧（LINE）走 `/stocktake` 那个页面，提交进的是同一批接口。
 *
 * 仅任务列表/盘点工作台两级路由；记录用原生弹窗。结束与库存调整在同一事务完成。
 */
(() => {
	const p = frappe.pearnly;
	const root = "pearnly-stock-count";
	const url = (name) => "/desk/" + [root, name]
		.filter(Boolean).map(encodeURIComponent).join("/");

	// 多级面包屑那套（含窄屏 `menu_items`）在共享层 `p.breadcrumbs`，商品/出入库/收发存
	// 用的是同一份；这里只把盘点自己的路径拼出来。
	p.stockCountBreadcrumbs = ({ name, title, entries = false, report = false } = {}) => {
		const trail = [];
		if (name) trail.push({
			label: title || name,
			route: entries || report ? url(name) : "",
		});
		if (entries) trail.push({ label: __("stock.count.entries"), route: "" });
		if (report) trail.push({ label: __("stock.board.title"), route: "" });
		p.breadcrumbs({ label: __("nav.stockCount"), route: url(), trail });
	};

	p.stockCount = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.inventory.api.";
		const w = (key, ...args) => t(`stock.workbench.${key}`, ...args);
		let list, table, chart, count, state, table_host, chart_host, summary;
		let sequence = 0, refresh_sequence = 0, item_filter = "all", search, filter;
		const status_text = task => task.status === "Active" ? t("stock.count.statusActive") : t("stock.count.statusClosed");
		const open_detail = name => frappe.set_route(root, name);
		const qty = value => value == null ? "—" : p.number(value);
		// 表格选项：列头菜单只留排序（"移除栏 / 冻结"由 core `p.table_menu` 去掉），
		// 所以这里不再需要给 Freeze/Unfreeze 补译文。
		const table_options = () => ({
			language: frappe.boot.lang,
			translations: frappe.utils.datatable.get_translations(),
		});
		const active = () => state?.task.status === "Active" && state.can_write;
		function clear() {
			list?.destroy(); table?.destroy(); chart?.parent?.remove?.();
			list = table = chart = null;
			// Page owns the native filter row inside main; retain it across routes.
			page.page_form.detach();
			page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
			page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
			page.hide_form();
			page.clear_primary_action(); page.clear_indicator();
		}
		async function show_route() {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			if (route.length > 2) { frappe.set_route(root, route[1]); return; }
			const request = ++sequence; ++refresh_sequence;
			count = route[1] || null;
			state = null; item_filter = "all";
			clear(); p.stockCountBreadcrumbs({ name: count });
			if (!count) { open_list(); return; }
			await frappe.require("report.bundle.js");
			if (request !== sequence) return;
			search = page.add_field({ fieldtype: "Data", fieldname: "search", label: t("stock.field.search"),
				change: () => render_data() });
			filter = page.add_field({ fieldtype: "Select", fieldname: "only", label: t("stock.count.filter"),
				options: ["all", "uncounted", "differences", "locations"].map(value => ({ value,
					label: value === "locations" ? w("locationDiff") : t(`stock.count.filter.${value}`) })),
				change: () => { item_filter = filter.get_value(); render_data(); } });
			summary = $("<div class='report-summary'>").appendTo(page.main);
			chart_host = $("<div class='chart-wrapper'>").appendTo(page.main);
			table_host = $("<div class='report-wrapper'>").appendTo(page.main);
			table_host.on("click", ".dt-row", event => {
				if ($(event.target).closest(".dt-dropdown, input, select, button").length || window.getSelection()?.toString()) return;
				const name = $(event.currentTarget).find("[data-count-row]").attr("data-count-row");
				if (!name) return;
				event.preventDefault();
				const row = state.items.find(r => r.name === name);
				if (row) void records(row);
			});
			await refresh();
		}
		function open_list() {
			// 选中若干个**进行中**的盘点 → 右上角出现"删除选中"。已结束/已过账的盘点只能查看
			// （服务端 `delete_count` 也只在 Active 时放行），所以那几行会让按钮禁用并说明原因。
			const selection = p.selection_action({
				page, list: () => list,
				// 只在**列表那一级**挂这颗主按钮：列表的异步刷新有可能在我们已经切进任务页之后
				// 才落地，那一笔 paint 会把"新建盘点"挂到只读的任务页右上角（实测抓到过）。
				empty: () => {
					if (frappe.get_route().length !== 1) return;
					page.set_primary_action(t("stock.count.new"), open_new);
				},
				label: rows => t("stock.count.deleteSelected", rows.length),
				when: row => row.status === "Active",
				reason: () => t("stock.count.deleteBlocked"),
				confirm: rows => t("stock.count.deleteConfirm", rows.length),
				run: async rows => {
					for (const row of rows) {
						await p.call(api + "api_stock_count_delete", { name: row.name }, "POST");
					}
				},
				success: rows => t("stock.count.deleted", rows.length),
			});
			list = p.list({ page, doctype: "ERP Stock Count", selectable: true, on_selection: selection,
				compact_columns: true,
				on_open: row => open_detail(row.name),
				empty_action: { label: t("stock.count.new"), action: open_new },
				load: async ({ start, page_length }) => {
					const [items, total] = await Promise.all([
						p.call(api + "api_list_stock_counts", { start, limit: page_length }),
						p.call(api + "api_stock_count_total"),
					]); return { items, total };
				}, columns: [
					// `compact_columns` + 每列 width/min_width：框架默认给每列 `min-width: 150px`
					// 且不收缩，七列就要 1050px —— 窗口窄一点，最右边那列（创建时间）就被挤出
					// 可视区、只剩一串省略号（用户 2026-09-22："那个时间被截断了"）。这里的写法
					// 与花名册那份列表一致：给出宽度与下限，让列能收缩到都看得见。
					{ fieldname: "title", width: 220, min_width: 160, label: t("stock.count.name") },
					{ fieldname: "status", width: 100, min_width: 80, label: t("stock.col.status"),
						format: (_v, row) => status_text(row) },
					{ fieldname: "warehouse_name", width: 130, min_width: 100, label: t("stock.col.warehouse") },
					{ fieldname: "item_total", width: 80, min_width: 64, label: t("stock.count.rows") },
					{ fieldname: "counted_total", width: 90, min_width: 72, label: t("stock.count.countedRows") },
					{ fieldname: "difference_total", width: 90, min_width: 72, label: t("stock.count.differenceRows") },
					// 创建时间是库里的 datetime（带秒与微秒），直接显示会被截成一串省略号 ——
					// 按"日期 时:分"显示（用户 2026-09-22 口径："那个时间格式不对"）。
					{ fieldname: "creation", width: 160, min_width: 148, label: t("stock.count.createdAt"),
						format: value => p.datetime_text(value) },
				] });
			page.set_primary_action(t("stock.count.new"), open_new);
			page.add_inner_button(t("stock.action.reload"), () => list.reload());
		}
		async function refresh() {
			const request = ++refresh_sequence, name = count;
			const result = await p.call(api + "api_stock_count_review", { name });
			if (request !== refresh_sequence || count !== name || frappe.get_route()[0] !== root) return;
			state = result;
			p.stockCountBreadcrumbs({ name, title: state.task.title });
			page.clear_inner_toolbar(); page.clear_primary_action();
			page.set_indicator(t("stock.count.headline", status_text(state.task), state.task.counted_total,
				state.task.item_total, state.task.difference_total), active() ? "orange" : "green");
			// 进行中的任务**只留三颗**（用户 2026-09-22 口径："只有刷新、结束盘点、开始盘点按钮，
			// 其它全部不要"）：刷新、结束盘点，加右边那颗开始盘点。
			// 已结束的是只读态 —— 之前定过"结束后只可查看和导出"，所以那一档保留导出 + 刷新。
			if (active()) {
				page.set_primary_action(t("stock.count.start"), () => entry_form(null, true));
				page.add_inner_button(t("stock.action.reload"), refresh);
				page.add_inner_button(t("stock.count.close"), finish);
			} else {
				page.add_inner_button(t("stock.action.export"), () => window.location.assign(
					`/api/method/${api}api_stock_count_export?name=${encodeURIComponent(name)}`));
				page.add_inner_button(t("stock.action.reload"), refresh);
			}
			await render_data();
		}
		function entry_form(row = null, continuous = false, entry = null) {
			return p.stockCountEntry({ count, t, row, entry, continuous, on_saved: refresh,
				on_done: () => filter?.set_value("differences") });
		}
		async function render_data() {
			if (!state || !table_host || !table_host[0].isConnected) return;
			summary.empty();
			for (const [label, value] of [[t("stock.count.countedRows"), state.task.counted_total],
				[t("stock.count.rowUncounted"), state.task.item_total - state.task.counted_total],
				[w("quantityDiff"), state.items.filter(r => r.counted && Number(r.difference)).length],
				[w("locationDiff"), state.location_total]]) {
				const item = $("<div class='summary-item'>").appendTo(summary);
				$("<div class='summary-label'>").text(label).appendTo(item);
				$("<div class='summary-value'>").text(value).appendTo(item);
			}
			if (state.conflicts.length) $("<p class='text-danger'>").text(w("stockChanged")).appendTo(summary);
			chart_host.empty();
			const groups = new Map();
			const group = (warehouse, location, unit) => {
				const key = `${warehouse} / ${location || t("stock.count.blank")} · ${unit || ""}`;
				if (!groups.has(key)) groups.set(key, { book: 0, actual: 0 });
				return groups.get(key);
			};
			for (const row of state.items) if (row.counted) {
				group(row.warehouse_name, row.location, row.unit).book += Number(row.book_qty);
				for (const e of state.entries.filter(e => e.item === row.name && !e.voided)) {
					group(e.warehouse_name, e.location, row.unit).actual += Number(e.qty);
				}
			}
			const ranked = [...groups].sort((a,b) => Math.abs(b[1].actual-b[1].book)-Math.abs(a[1].actual-a[1].book)).slice(0,12);
			if (ranked.length) chart = new frappe.Chart(chart_host[0], { type: "bar", height: 260,
				colors: ["#a3a3a3", getComputedStyle(document.documentElement).getPropertyValue("--pearnly-accent").trim() || "#7c4dff"],
				data: { labels: ranked.map(([key])=>key), datasets: [
					{ name: t("stock.count.book"), values: ranked.map(([,v])=>v.book) },
					{ name: t("stock.count.counted"), values: ranked.map(([,v])=>v.actual) },
				] } });
			const q = (search?.get_value() || "").trim().toLowerCase();
			const rows = state.items.filter(r => (!q || [r.product_name,r.code,r.barcode,r.location,r.actual_positions].join(" ").toLowerCase().includes(q))
				&& (item_filter !== "uncounted" || !r.counted)
				&& (item_filter !== "differences" || r.counted && (Number(r.difference) || r.location_difference))
				&& (item_filter !== "locations" || r.location_difference));
			table?.destroy(); table = null;
			if (!rows.length) {
				const empty = $(frappe.views.ListView.prototype.get_no_result_message.call({ doctype: "ERP Stock Count Item",
					meta: { description: __("Nothing to show") }, can_create: active(), get_documentation_link: () => "" }));
				empty.find(".btn-new-doc").text(w("record")).on("click", () => entry_form());
				table_host.empty().append(empty); return;
			}
			const columns = [
				{ name: t("stock.col.name"), width: 210, format: value => value },
				{ name: t("stock.col.code"), width: 100 }, { name: t("stock.col.barcode"), width: 140 },
				{ name: w("bookPosition"), width: 160 }, { name: w("actualPositions"), width: 230 },
				{ name: t("stock.col.unit"), width: 70 }, { name: t("stock.count.book"), width: 90 },
				{ name: t("stock.count.counted"), width: 90 }, { name: w("quantityDiff"), width: 90 },
				...(state.can_view_costs ? [{ name: t("stock.count.differenceValue"), width: 110 }] : []),
				{ name: t("stock.col.status"), width: 140 },
			];
			table = await p.table(table_host, columns.map(c=>({ ...c, format: c.format || p.escape })), rows.map(r=>[
				`<a href="#" data-count-row="${p.escape(r.name)}">${p.escape(r.product_name)}</a>`, r.code || "", r.barcode || "",
				`${r.warehouse_name} / ${r.location || t("stock.count.blank")}`, r.actual_positions || "—", r.unit || "",
				qty(r.book_qty), r.counted ? qty(r.counted_qty) : "—", r.counted ? qty(r.difference) : "—",
				...(state.can_view_costs ? [qty(r.difference_value)] : []),
				r.stock_changed ? w("needsRecount") : !r.counted ? t("stock.count.rowUncounted") : r.location_difference ? w("locationDiff")
					: Number(r.difference) ? t("stock.count.rowDifference") : t("stock.count.rowMatch"),
			]), table_options());
		}
		async function records(row) {
			const entries = state.entries.filter(e=>e.item===row.name);
			const d = new frappe.ui.Dialog({ title: t("stock.count.entriesTitle", row.product_name), size: "extra-large", fields: [
				{ fieldtype: "HTML", fieldname: "hint", options: `<p class="text-muted">${p.escape(w("bookPosition"))}: ${p.escape(row.warehouse_name)} / ${p.escape(row.location || "—")} · ${qty(row.book_qty)} ${p.escape(row.unit || "")}</p>` },
				{ fieldtype: "HTML", fieldname: "entries" },
			] });
			let detail;
			d.onhide = () => detail?.destroy();
			const later = action => { d.$wrapper.one("hidden.bs.modal", action); d.hide(); };
			if (active()) {
				d.set_primary_action(w("record"), () => later(()=>entry_form(row)));
				d.set_secondary_action_label(w("recount"));
				d.set_secondary_action(() => frappe.confirm(w("recountConfirm"), async () => {
					await p.call(api + "api_stock_count_recount", { count, product: row.product }, "POST");
					await refresh(); later(()=>entry_form(row));
				}));
			}
			d.on_page_show = async () => {
				if (detail) return;
				detail = await p.table(d.fields_dict.entries.$wrapper, [
					w("actualWarehouse"), w("actualLocation"), t("stock.count.qty"), t("stock.count.note"),
					t("stock.count.countedBy"), t("stock.count.countedAt"),
					{ name: t("stock.col.status"), format: value=>value },
				], entries.map(e=>[e.warehouse_name, e.location || "—", qty(e.qty), e.note || "", e.counted_by, e.counted_at,
					e.voided ? p.escape(t("stock.count.entryVoided")) : active()
						? `<button class="btn btn-default btn-xs" data-edit-count-entry="${p.escape(e.name)}">${p.escape(w("editEntry"))}</button> <button class="btn btn-default btn-xs" data-void-count-entry="${p.escape(e.name)}">${p.escape(t("stock.count.void"))}</button>`
						: p.escape(t("stock.count.entryLive"))]), table_options());
			};
			d.fields_dict.entries.$wrapper.on("click", "[data-edit-count-entry]", event => {
				const entry = entries.find(e=>e.name===event.currentTarget.dataset.editCountEntry);
				later(()=>entry_form(row, false, entry));
			}).on("click", "[data-void-count-entry]", event => {
				const entry = entries.find(e=>e.name===event.currentTarget.dataset.voidCountEntry);
				frappe.confirm(t("stock.count.voidConfirm", qty(entry.qty)), async () => {
					await p.call(api + "api_stock_count_edit_entry", { entry: entry.name, version: entry.version, voided: 1 }, "POST");
					d.hide(); await refresh();
				});
			});
			d.show();
		}
		async function finish() {
			await refresh();
			if (!active()) return;
			if (state.conflicts.length) { frappe.msgprint(w("stockChanged")); return; }
			const reviewed = state.review_token;
			frappe.confirm(w("finishConfirm", state.task.counted_total, state.task.item_total-state.task.counted_total), async () => {
				await p.call(api + "api_stock_count_close", { name: count, review_token: reviewed }, "POST");
				p.success(t("stock.count.closed")); await refresh();
			});
		}
		function open_new() {
			let file_url = "";
			let dialog;
			const pick = (field) => () => {
				new frappe.ui.FileUploader({
					allow_multiple: false,
					allow_web_link: false,
					allow_google_drive: false,
					allow_take_photo: false,
					disable_file_browser: true,
					allow_toggle_private: false,
					make_attachments_public: false,
					restrictions: {
						allowed_file_types: [".xlsx"],
						max_file_size: 5 * 1024 * 1024,
					},
					on_success: (file) => {
						file_url = file.file_url;
						dialog.set_value(field, file.file_name);
					},
				});
			};
			dialog = p.dialog(
				t("stock.count.new"),
				[
					{ fieldtype: "Data", fieldname: "title", label: t("stock.count.name"), reqd: 1 },
					{ fieldtype: "Select", fieldname: "mode", label: t("stock.count.mode"),
						options: [t("stock.count.modeStock"),
							t("stock.count.modeImport")].join("\n"),
						default: t("stock.count.modeStock"),
						change: () => toggle_import() },
					{ fieldtype: "Data", fieldname: "warehouse", label: t("stock.col.warehouse"),
						description: t("stock.count.warehouseHint") },
					{ fieldtype: "Small Text", fieldname: "notes", label: t("stock.count.notes") },
					{ fieldtype: "Button", fieldname: "pick", label: t("stock.count.importPick"),
						hidden: 1, click: pick("file_name") },
					{ fieldtype: "Data", fieldname: "file_name", label: t("stock.count.importFile"),
						read_only: 1, hidden: 1 },
					{ fieldtype: "Button", fieldname: "template",
						label: t("stock.count.template"), hidden: 1, click: () => {
							window.location.assign(
								`/api/method/${api}api_stock_count_template?warehouse=${encodeURIComponent(dialog.get_value("warehouse") || "")}`);
						} },
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("stock.count.newHint"))}</p>` },
				],
				t("stock.count.new"),
				async (values, d) => {
					const importing = values.mode === t("stock.count.modeImport");
					if (importing && !file_url) {
						frappe.msgprint({
							title: t("stock.count.modeImport"),
							indicator: "orange",
							message: t("stock.count.importMissing"),
						});
						return;
					}
					const created = await p.call(api + "api_create_stock_count", {
						title: values.title,
						warehouse: values.warehouse || undefined,
						notes: values.notes || undefined,
						file_url: importing ? file_url : undefined,
					}, "POST");
					d.hide();
					p.success(t("stock.count.created", created.rows));
					report_problems(created.problems);
					await list.reload();
					open_detail(created.name);
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");

			// 只有"上传导入"才需要文件与模版两件事，选"盘点在库"时收起来。
			function toggle_import() {
				const importing = dialog.get_value("mode") === t("stock.count.modeImport");
				for (const name of ["pick", "file_name", "template"]) {
					const field = dialog.get_field(name);
					field.df.hidden = importing ? 0 : 1;
					field.refresh();
				}
			}
			toggle_import();
		}

		// 表里认不出的行逐条列出来（坏行只跳过自己，别的照进）—— 不悄悄吞掉。
		function report_problems(problems) {
			if (!problems || !problems.length) return;
			const reasons = {
				code_not_found: t("stock.count.problemCode"),
				no_identifier: t("stock.count.problemIdentifier"),
				warehouse_not_found: t("stock.count.problemWarehouse"),
				"book_qty:not_a_number": t("stock.count.problemNumber"),
				"book_qty:invalid": t("stock.count.problemNumber"),
				"counted_qty:not_a_number": t("stock.count.problemNumber"),
				"counted_qty:invalid": t("stock.count.problemNumber"),
			};
			frappe.msgprint({
				title: t("stock.count.importTitle"),
				indicator: "orange",
				message: t("stock.count.importSkipped", problems.length) + "<br>"
					+ problems.slice(0, 20).map((row) =>
						`${p.escape(row.row)}: ${p.escape(reasons[row.reason] || row.reason)}`
					).join("<br>"),
			});
		}

		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; ++refresh_sequence; table?.destroy(); table = null; });
		await show_route();
	};
})();
