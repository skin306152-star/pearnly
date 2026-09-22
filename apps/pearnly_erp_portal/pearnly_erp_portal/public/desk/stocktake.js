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

	// 原生 Custom 面包屑只支持一个节点。仅扩展盘点的多级数据，节点仍由原生 API 渲染；
	// 保存到 breadcrumbs.all，让框架重绘时保留整条路径，而不是临时追加 DOM 和点击闭包。
	const native_custom = frappe.breadcrumbs.set_custom_breadcrumbs;
	frappe.breadcrumbs.set_custom_breadcrumbs = function (crumb) {
		native_custom.call(this, crumb);
		for (const step of crumb.pearnly_stock_count_trail || []) {
			this.append_breadcrumb_element(step.route, p.escape(step.label));
		}
		if (crumb.pearnly_stock_count_trail && crumb.menu_items?.length) {
			// 窄屏的原生样式只展示最后一级；用框架已有的 menu_items 提供全部父级。
			// 菜单点击不能再冒泡给 Desk 路由器，否则它会重开当前页并关掉菜单。
			this.$breadcrumbs.children("li:last-child").on("click", (event) => {
				event.preventDefault();
				event.stopPropagation();
			}).find("a").attr("aria-haspopup", "menu")
				.append(frappe.utils.icon("chevron-down", "xs"));
		}
	};
	p.stockCountBreadcrumbs = ({ name, title, entries = false, report = false } = {}) => {
		const trail = [];
		if (name) trail.push({
			label: title || name,
			route: entries || report ? url(name) : "",
		});
		if (entries) trail.push({ label: __("stock.count.entries"), route: "" });
		if (report) trail.push({ label: __("stock.board.title"), route: "" });
		frappe.breadcrumbs.add({
			type: "Custom", label: __("nav.stockCount"), route: url(),
			pearnly_stock_count_trail: trail,
			menu_items: trail.length ? [
				{ label: __("nav.home"), onClick: () => frappe.set_route("pearnly-home") },
				{ label: __("nav.stockCount"), onClick: () => frappe.set_route(root) },
				...trail.filter((step) => step.route).map((step) => ({
					label: p.escape(step.label), onClick: () => frappe.set_route(step.route),
				})),
			] : [],
		});
	};

	p.stockCount = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.inventory.api.";
		const w = (key, ...args) => t(`stock.workbench.${key}`, ...args);
		let list, table, chart, count, state, table_host, chart_host, summary;
		let sequence = 0, refresh_sequence = 0, item_filter = "all", search, filter;
		const status_text = task => task.status === "Active" ? t("stock.count.statusActive") : t("stock.count.statusClosed");
		const open_detail = name => frappe.set_route(root, name);
		const qty = value => value == null ? "—" : p.number(value);
		const active = () => state?.task.status === "Active" && state.can_write;
		function clear() {
			list?.destroy(); table?.destroy(); chart?.parent?.remove?.();
			list = table = chart = null;
			page.main.empty().removeClass("layout-main-list list-view");
			page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
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
			list = p.list({ page, doctype: "ERP Stock Count", on_open: row => open_detail(row.name),
				empty_action: { label: t("stock.count.new"), action: open_new },
				load: async ({ start, page_length }) => {
					const [items, total] = await Promise.all([
						p.call(api + "api_list_stock_counts", { start, limit: page_length }),
						p.call(api + "api_stock_count_total"),
					]); return { items, total };
				}, columns: [
					{ fieldname: "title", label: t("stock.count.name") },
					{ fieldname: "status", label: t("stock.col.status"), format: (_v, row) => status_text(row) },
					{ fieldname: "warehouse_name", label: t("stock.col.warehouse") },
					{ fieldname: "item_total", label: t("stock.count.rows") },
					{ fieldname: "counted_total", label: t("stock.count.countedRows") },
					{ fieldname: "difference_total", label: t("stock.count.differenceRows") },
					{ fieldname: "creation", label: t("stock.count.createdAt") },
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
			page.add_inner_button(t("stock.action.export"), () => window.location.assign(
				`/api/method/${api}api_stock_count_export?name=${encodeURIComponent(name)}`));
			page.add_inner_button(t("stock.action.reload"), refresh);
			if (active()) {
				page.set_primary_action(w("continuous"), () => entry_form(null, true));
				page.add_inner_button(w("record"), () => entry_form());
				page.add_inner_button(t("stock.count.import"), () => open_import(name));
				page.add_inner_button(w("review"), () => filter.set_value("differences"));
				page.add_inner_button(t("stock.count.close"), finish);
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
				{ name: t("stock.col.status"), width: 140 },
			];
			table = await p.table(table_host, columns.map(c=>({ ...c, format: c.format || p.escape })), rows.map(r=>[
				`<a href="#" data-count-row="${p.escape(r.name)}">${p.escape(r.product_name)}</a>`, r.code || "", r.barcode || "",
				`${r.warehouse_name} / ${r.location || t("stock.count.blank")}`, r.actual_positions || "—", r.unit || "",
				qty(r.book_qty), r.counted ? qty(r.counted_qty) : "—", r.counted ? qty(r.difference) : "—",
				r.stock_changed ? w("needsRecount") : !r.counted ? t("stock.count.rowUncounted") : r.location_difference ? w("locationDiff")
					: Number(r.difference) ? t("stock.count.rowDifference") : t("stock.count.rowMatch"),
			]));
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
						: p.escape(t("stock.count.entryLive"))]));
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

		function open_import(name) {
			let file_url = "";
			const dialog = p.dialog(
				t("stock.count.import"),
				[
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("stock.count.importHint"))}</p>` },
					{ fieldtype: "Button", fieldname: "pick", label: t("stock.count.importPick"),
						click: () => {
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
									dialog.set_value("file_name", file.file_name);
								},
							});
						} },
					{ fieldtype: "Data", fieldname: "file_name", label: t("stock.count.importFile"),
						read_only: 1 },
					{ fieldtype: "Button", fieldname: "template",
						label: t("stock.count.template"), click: () => {
							window.location.assign(
								`/api/method/${api}api_stock_count_template?name=${encodeURIComponent(name)}`);
						} },
				],
				t("stock.count.import"),
				async (_values, d) => {
					const result = await p.call(api + "api_stock_count_import",
						{ name, file_url }, "POST");
					d.hide();
					p.success(t("stock.count.imported", result.rows, result.counted));
					report_problems(result.problems);
					void show_route();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}
		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; ++refresh_sequence; table?.destroy(); table = null; });
		await show_route();
	};
})();
