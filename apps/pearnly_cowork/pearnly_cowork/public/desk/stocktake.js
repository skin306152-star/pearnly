/* 库存盘点（Cowork，`/desk/pearnly-stocktake`）。
 *
 * 布局照 ERP 盘点页（`pearnly_erp_portal/public/desk/stocktake.js`）：任务列表 / 任务详情两级路由，
 * 列表可排序、勾选删除，右上角新建；详情上方搜索 + 筛选，下面一张官方表格，点商品看逐笔记录。
 * 用户 2026-09-26：只去掉「盘点在库」——Cowork 没有库存系统，任务只能**从上传的表建**
 * （表里可带实盘，认不出的行跳过并逐条列出原因，见 `stocktake/upload.py`）；
 * 开始盘点 / 逐笔记录 / 重盘 / 照片都保留（`stocktake-entry.js`、`stocktake/counting.py`）。
 */
(() => {
	const p = frappe.pearnly;
	const root = "pearnly-stocktake";
	const api = "pearnly_cowork.stocktake.api.";

	p.coworkStocktake = async (page, _core_t, wrapper) => {
		const t = await p.texts("cowork.stock.");
		let list, table, task_id, state, table_host, search, filter;
		let sequence = 0, refresh_sequence = 0, item_filter = "all";
		const status_text = status => status === "active" ? t("cowork.stock.statusActive") : t("cowork.stock.statusClosed");
		const qty = value => value == null ? "—" : p.number(value);
		const danger = value => `<span style="color: var(--red-600)">${value}</span>`;
		const table_options = () => ({ language: frappe.boot.lang, translations: frappe.utils.datatable.get_translations() });
		// 刷新图标只挂一次：页面动作图标不随路由清掉，按当前是列表还是详情各自刷新。
		p.add_refresh(page, () => task_id ? refresh() : list?.reload());
		const breadcrumbs = (title) => p.breadcrumbs({ label: __("nav.stocktake"), route: p.subroute(root),
			trail: title ? [{ label: title }] : [] });

		function clear() {
			list?.destroy(); table?.destroy();
			list = table = null;
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
			task_id = route[1] || null;
			state = null; item_filter = "all";
			clear(); breadcrumbs();
			if (!task_id) { open_list(); return; }
			await frappe.require("report.bundle.js");
			if (request !== sequence) return;
			search = page.add_field({ fieldtype: "Data", fieldname: "search", label: __("Search"), change: () => render_data() });
			filter = page.add_field({ fieldtype: "Select", fieldname: "only", label: t("cowork.stock.filter"), default: "all",
				options: ["all", "uncounted", "differences", "locations"].map(value => ({ value,
					label: t(`cowork.stock.filter.${value}`) })),
				change: () => { item_filter = filter.get_value(); render_data(); } });
			table_host = $("<div class='report-wrapper'>").appendTo(page.main);
			table_host.on("click", ".dt-row", event => {
				if ($(event.target).closest(".dt-dropdown, input, select, button").length || window.getSelection()?.toString()) return;
				const id = $(event.currentTarget).find("[data-count-row]").attr("data-count-row");
				if (!id) return;
				event.preventDefault();
				const row = state.rows.find(r => r.id === id);
				if (row) void records(row);
			});
			await refresh();
		}

		// ── 列表 ──────────────────────────────────────────────────────────────
		function open_list() {
			const selection = p.selection_action({
				page, list: () => list,
				empty: () => {
					if (frappe.get_route().length !== 1) return;
					page.set_primary_action(t("cowork.stock.new"), open_new);
				},
				label: rows => t("cowork.stock.deleteSelected", rows.length),
				confirm: rows => t("cowork.stock.deleteConfirm", rows.length),
				run: async rows => {
					await p.call(api + "delete", { task_ids: JSON.stringify(rows.map(row => row.id)) }, "POST");
				},
				success: rows => t("cowork.stock.deleted", rows.length),
				sound: "delete",
			});
			list = p.list({ page, doctype: "Cowork Stocktake Task", selectable: true, on_selection: selection,
				compact_columns: true,
				on_open: row => frappe.set_route(root, row.id),
				empty_action: { label: t("cowork.stock.new"), action: open_new },
				sort_fields: ["title", "status", "total", "counted", "differences", "created_at"],
				load: async ({ start, page_length, sort_by, sort_order }) => {
					const result = await p.call(api + "tasks", { start, limit: page_length,
						sort_by, sort_order });
					// 列表框架按 `name` 去重识别行；任务的主键是 id，名称另放一格。
					return { items: result.tasks.map(row => ({ ...row, title: row.name, name: row.id })), total: result.total };
				}, columns: [
					{ fieldname: "title", width: 240, min_width: 160, label: t("cowork.stock.name") },
					{ fieldname: "status", width: 100, min_width: 80, label: t("cowork.stock.status"),
						format: (_v, row) => status_text(row.status) },
					{ fieldname: "total", width: 80, min_width: 64, label: t("cowork.stock.rows") },
					{ fieldname: "counted", width: 90, min_width: 72, label: t("cowork.stock.countedRows") },
					{ fieldname: "differences", width: 90, min_width: 72, label: t("cowork.stock.differenceRows"),
						cell_class: value => Number(value) ? "pearnly-difference" : "" },
					{ fieldname: "created_at", width: 160, min_width: 148, label: t("cowork.stock.createdAt"),
						format: value => p.datetime_text(value) },
				] });
			page.set_primary_action(t("cowork.stock.new"), open_new);
		}

		// ── 新建：名称 + 上传盘点表 ─────────────────────────────────────────────
		function open_new() {
			let file = null;
			// 一次弹窗一个请求号：连点或网络重试，服务端都认作同一次上传，不会建出两份任务。
			const request_id = crypto.randomUUID();
			const dialog = p.dialog(t("cowork.stock.new"), [
				{ fieldtype: "Data", fieldname: "title", label: t("cowork.stock.name"), reqd: 1 },
				{ fieldtype: "Data", fieldname: "file_name", label: t("cowork.stock.file"), read_only: 1, reqd: 1 },
				{ fieldtype: "Button", fieldname: "pick", label: t("cowork.stock.pick"), click: async () => {
					file = await p.pick_file(".xlsx") || file;
					dialog.set_value("file_name", file ? file.name : "");
				} },
				{ fieldtype: "Button", fieldname: "template", label: t("cowork.stock.template"),
					click: () => window.location.assign(`/api/method/${api}template`) },
			], t("cowork.stock.new"), async (values, d) => {
				if (!file) return;
				const created = await p.post_files(api + "upload_count", { file, title: values.title, request_id });
				d.hide();
				p.success(t("cowork.stock.created", created.imported, created.counted), "click");
				report_problems(created.problems);
				frappe.set_route(root, created.id);
			});
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		// 认不出的行逐条列出来（坏行只跳过自己，别的照收）——不悄悄吞掉。
		function report_problems(problems) {
			if (!problems?.length) return;
			frappe.msgprint({
				title: t("cowork.stock.importTitle"),
				indicator: "orange",
				message: t("cowork.stock.importSkipped", problems.length) + "<br>"
					+ problems.slice(0, 50).map(row => `${p.escape(row.row)}${row.code ? " · " + p.escape(row.code) : ""}: `
						+ p.escape(__(row.reason))).join("<br>"),
			});
		}

		// ── 详情：结果对照（只看、结束、导出）────────────────────────────────────
		async function refresh() {
			const request = ++refresh_sequence, current = task_id;
			const result = await p.call(api + "task", { task_id: current });
			if (request !== refresh_sequence || task_id !== current || frappe.get_route()[0] !== root) return;
			state = result;
			const summaries = Object.fromEntries((state.summary || []).map(row => [row.item_id, row]));
			state.rows = state.items.map(item => ({ ...item, summary: summaries[item.id] || {} }));
			breadcrumbs(state.name);
			page.clear_inner_toolbar(); page.clear_primary_action();
			const active = state.status === "active";
			page.set_indicator(t("cowork.stock.headline", status_text(state.status), state.counted, state.total,
				state.differences), active ? "orange" : "green");
			if (active) {
				page.set_primary_action(t("cowork.stock.start"), () => entry_form(null, true));
				page.add_inner_button(t("cowork.stock.close"), finish);
			} else {
				page.set_primary_action(t("cowork.stock.export"), () => window.location.assign(
					`/api/method/${api}export?task_id=${encodeURIComponent(current)}`));
			}
			await render_data();
		}

		const moved = row => ["mismatched"].includes(row.summary.warehouse_comparison)
			|| ["mismatched"].includes(row.summary.location_comparison);

		async function render_data() {
			if (!state || !table_host || !table_host[0].isConnected) return;
			const q = (search?.get_value() || "").trim().toLowerCase();
			const rows = state.rows.filter(r => (!q || [r.product_name, r.product_code, r.barcode, r.warehouse, r.location]
				.join(" ").toLowerCase().includes(q))
				&& (item_filter !== "uncounted" || r.actual_qty == null)
				&& (item_filter !== "differences" || (r.actual_qty != null && Number(r.difference)))
				&& (item_filter !== "locations" || moved(r)));
			table?.destroy(); table = null;
			if (!rows.length) {
				table_host.empty().append($(frappe.views.ListView.prototype.get_no_result_message.call({
					doctype: "Cowork Stocktake Item", meta: { description: __("Nothing to show") }, can_create: false,
					get_documentation_link: () => "" })));
				return;
			}
			const narrow = window.matchMedia("(max-width: 767px)").matches;
			const columns = [
				{ name: t("cowork.stock.col.name"), width: narrow ? 120 : 220, format: value => value, phone: true },
				{ name: t("cowork.stock.col.code"), width: 110 }, { name: t("cowork.stock.col.barcode"), width: 140 },
				{ name: t("cowork.stock.col.position"), width: 170, format: value => value },
				{ name: t("cowork.stock.col.unit"), width: 70 },
				{ name: t("cowork.stock.col.book"), width: narrow ? 64 : 90, phone: true },
				{ name: t("cowork.stock.col.actual"), width: narrow ? 64 : 90, phone: true },
				{ name: t("cowork.stock.col.difference"), width: narrow ? 64 : 90, format: value => value, phone: true },
				{ name: t("cowork.stock.col.result"), width: narrow ? 84 : 110, format: value => value, phone: true },
			];
			const shown = columns.map((column, index) => [column, index]).filter(([column]) => !narrow || column.phone);
			const pick = cells => shown.map(([_column, index]) => cells[index]);
			table = await p.table(table_host, shown.map(([c]) => ({ ...c, format: c.format || p.escape })), rows.map(r => {
				const counted = r.actual_qty != null;
				const different = counted && Number(r.difference) !== 0;
				const result = r.summary.count_result || (counted ? (different ? "short" : "matched") : "not_counted");
				const position = `${p.escape(r.warehouse || "—")} / ${p.escape(r.location || "—")}`;
				const result_text = p.escape(t(`cowork.stock.result.${result}`));
				return pick([
					`<a href="#" data-count-row="${p.escape(r.id)}">${p.escape(r.product_name)}</a>`,
					r.product_code || "", r.barcode || "",
					moved(r) ? danger(position) : position,
					r.unit || "", qty(r.book_qty), counted ? qty(r.actual_qty) : "—",
					counted ? (different ? danger(qty(r.difference)) : qty(r.difference)) : "—",
					different || moved(r) ? danger(result_text) : result_text,
				]);
			}), table_options());
		}

		function entry_form(item = null, continuous = false, entry = null) {
			return p.coworkStockEntry({ task_id, t, item, entry, continuous, on_saved: refresh,
				on_done: () => filter?.set_value("differences") });
		}

		// 一个商品的逐笔记录（含作废的，留痕）+ 照片；进行中可记录、改、作废、重盘。
		async function records(row) {
			const data = await p.call(api + "item_entries", { task_id, item_id: row.id });
			const active = data.active;
			const d = new frappe.ui.Dialog({ title: t("cowork.stock.entriesTitle", row.product_name), size: "extra-large", fields: [
				{ fieldtype: "HTML", fieldname: "hint", options: `<p class="text-muted">${p.escape(t("cowork.stock.col.position"))}: `
					+ `${p.escape(row.warehouse || "—")} / ${p.escape(row.location || "—")} · ${qty(row.book_qty)} ${p.escape(row.unit || "")}</p>` },
				{ fieldtype: "HTML", fieldname: "entries" },
				{ fieldtype: "HTML", fieldname: "photos" },
			] });
			let detail;
			d.onhide = () => detail?.destroy();
			const later = action => { d.$wrapper.one("hidden.bs.modal", action); d.hide(); };
			if (active) {
				d.set_primary_action(t("cowork.stock.record"), () => later(() => entry_form(row)));
				// 重盘不设确认步骤（用户口径）：旧记录全部作废留痕，这一行回到“未盘”。
				d.set_secondary_action_label(t("cowork.stock.recount"));
				d.set_secondary_action(async () => {
					await p.call(api + "recount", { task_id, item_id: row.id }, "POST");
					await refresh(); later(() => entry_form(row));
				});
			}
			d.on_page_show = async () => {
				if (detail) return;
				detail = await p.table(d.fields_dict.entries.$wrapper, [
					{ name: t("cowork.stock.actualWarehouse"), format: value => value },
					{ name: t("cowork.stock.actualLocation"), format: value => value },
					t("cowork.stock.entryQty"), t("cowork.stock.countedBy"), t("cowork.stock.countedAt"),
					{ name: t("cowork.stock.status"), format: value => value },
				], data.entries.map(e => {
					const place = (different, text) => different ? danger(text) : text;
					return [place(e.warehouse !== (row.warehouse || "-") && Boolean(row.warehouse), p.escape(e.warehouse)),
						place((e.location || "") !== (row.location || "") && Boolean(row.location), p.escape(e.location || "—")),
						qty(e.quantity), e.counted_by_name || e.counted_by || "", p.datetime_text(e.counted_at),
						e.voided ? p.escape(t("cowork.stock.entryVoided")) : active
							? `<button class="btn btn-default btn-xs" data-edit-entry="${p.escape(e.id)}">${p.escape(t("cowork.stock.editEntry"))}</button> `
								+ `<button class="btn btn-default btn-xs" data-void-entry="${p.escape(e.id)}">${p.escape(t("cowork.stock.void"))}</button>`
							: p.escape(t("cowork.stock.entryLive"))];
				}), table_options());
				render_photos();
			};
			function render_photos() {
				const host = d.fields_dict.photos?.$wrapper;
				if (!host || !data.photos.length) return;
				host.empty();
				$("<p class='text-muted'>").text(`${t("cowork.stock.photos")} ${data.photos.length}`).appendTo(host);
				const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
				for (const photo of data.photos) {
					const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
					$("<a>").attr({ href: photo.file_url, target: "_blank", rel: "noopener" })
						.append($("<img>").attr({ src: photo.file_url, alt: photo.file_name || "" })).appendTo(cell);
					$("<span class='text-muted'>").text([`${photo.warehouse || ""} / ${photo.location || "—"}`, qty(photo.quantity),
						photo.voided ? t("cowork.stock.entryVoided") : ""].filter(Boolean).join(" · ")).appendTo(cell);
				}
			}
			d.fields_dict.entries.$wrapper.on("click", "[data-edit-entry]", event => {
				const entry = data.entries.find(e => e.id === event.currentTarget.dataset.editEntry);
				later(() => entry_form(row, false, entry));
			}).on("click", "[data-void-entry]", async event => {
				const entry = data.entries.find(e => e.id === event.currentTarget.dataset.voidEntry);
				await p.call(api + "edit_entry", { task_id, entry_id: entry.id, request_id: crypto.randomUUID(),
					quantity: entry.quantity, warehouse: entry.warehouse, location: entry.location || "",
					version: entry.version, voided: 1 }, "POST");
				d.hide(); await refresh();
			});
			d.show();
		}

		// 不设确认步骤（用户口径）：点了就结束，结束后只能查看与导出。
		async function finish() {
			await p.call(api + "close", { task_id }, "POST");
			p.success(t("cowork.stock.closed")); await refresh();
		}

		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; ++refresh_sequence; table?.destroy(); table = null; });
		await show_route();
	};
})();
