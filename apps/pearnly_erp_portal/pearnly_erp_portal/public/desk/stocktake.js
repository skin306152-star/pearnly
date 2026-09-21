/* 盘点：任务（快照 + 追加式记录 + 差异过账）。
 *
 * 形状照旧项目 Cowork/LINE 那套盘点搬：开一份任务把账面冻结成快照行，员工扫一次记一条
 * （可改可撤销，实盘合计 = 未撤销之和），结束盘点锁住记录，最后过账把差异落成流水。
 * 手机那一侧（LINE）走 `/stocktake` 那个页面，提交进的是同一批接口。
 *
 * 一页两态：先看任务列表，点进去看这份盘点的行；工具栏跟着换（返回 / 导出 / 结束 / 过账）。
 */
(() => {
	const p = frappe.pearnly;

	p.stockCount = async (page, t) => {
		const api = "pearnly_erp_portal.inventory.api.";
		const item_filters = ["all", "uncounted", "differences"];
		let list;
		let chart;
		let count = null; // 打开的是哪一份盘点（null = 列表态）
		let item_filter = "all";
		let mode = "list";

		const qty = (value) => (value === null || value === undefined
			? t("stock.count.blank")
			: format_number(value, null, 3));
		const status_text = (row) => t({
			Active: "stock.count.statusActive",
			Closed: "stock.count.statusClosed",
			Posted: "stock.count.statusPosted",
		}[row.status] || "stock.count.statusActive");
		const row_state = (row) => {
			if (!row.counted) return t("stock.count.rowUncounted");
			return row.difference ? t("stock.count.rowDifference") : t("stock.count.rowMatch");
		};

		function clear() {
			list?.destroy();
			list = null;
			chart?.destroy();
			chart = null;
			page.main.find(".pearnly-stock-chart").remove();
			page.clear_inner_toolbar();
			page.clear_primary_action();
		}

		// 二级界面用**原生面包屑**（首页 / 盘点 / 任务名），不再放"返回"按钮。
		// 形状与框架自己给 Page 加的那条一致（`views/pageview.js`），只是多一层。
		function set_breadcrumb(task) {
			frappe.breadcrumbs.add({
				type: "Custom",
				label: __("nav.stockCount"),
				route: frappe.get_route_str(),
			});
			if (!task) return;
			frappe.breadcrumbs.$breadcrumbs.find("li a").last()
				.attr("href", "#")
				.on("click", (event) => {
					event.preventDefault();
					open_list();
				});
			frappe.breadcrumbs.append_breadcrumb_element("", p.escape(task.title));
		}

		// ── 列表态 ────────────────────────────────────────────────────────────
		function open_list() {
			mode = "list";
			count = null;
			clear();
			set_breadcrumb(null);
			const status = page.add_field({
				fieldtype: "Select",
				fieldname: "status",
				label: t("stock.col.status"),
				options: ["", "Active", "Closed", "Posted"].join("\n"),
				change: () => void list?.reload(),
			});
			list = p.list({
				page,
				doctype: "ERP Stock Count",
				selectable: true,
				on_selection: update_action,
				on_open: (row) => open_detail(row.name),
				load: async ({ start, page_length }) => {
					const query = { status: status.get_value() || undefined };
					const [items, total] = await Promise.all([
						p.call(api + "api_list_stock_counts",
							{ start, limit: page_length, ...query }),
						p.call(api + "api_stock_count_total", query),
					]);
					return { items, total };
				},
				columns: [
					{ fieldname: "title", label: t("stock.count.name") },
					{ fieldname: "status", label: t("stock.col.status"),
						format: (_value, row) => status_text(row) },
					{ fieldname: "warehouse", label: t("stock.col.warehouse") },
					{ fieldname: "item_total", label: t("stock.count.rows") },
					{ fieldname: "counted_total", label: t("stock.count.countedRows") },
					{ fieldname: "difference_total", label: t("stock.count.differenceRows") },
					{ fieldname: "creation", label: t("stock.count.createdAt") },
				],
			});
			update_action([]);
			page.add_inner_button(t("stock.action.reload"), () => void list?.reload());
			// 模版按**当前结存**给：先导表、离线填实盘数，再走"上传导入"开一份盘点。
			page.add_inner_button(t("stock.count.template"), () => {
				window.open(frappe.utils.get_url(
					`/api/method/${api}api_stock_count_template`));
			});
			void list.ready;
		}

		// 勾选后主按钮换成"删除所选"；已过账的在服务端也会被拒（只能留档）。
		function update_action(rows) {
			if (!rows.length) {
				page.set_primary_action(t("stock.count.new"), () => open_new());
				return;
			}
			page.set_primary_action(t("stock.count.deleteSelected", rows.length),
				() => void remove_selected(rows));
		}

		async function remove_selected(rows) {
			frappe.confirm(t("stock.count.deleteConfirm", rows.length), async () => {
				for (const row of rows) {
					await p.call(api + "api_stock_count_delete", { name: row.name }, "POST");
				}
				p.success(t("stock.count.deleted", rows.length));
				list.clear_checked_items();
				await list.reload();
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
					{ fieldtype: "Link", fieldname: "warehouse", label: t("stock.col.warehouse"),
						options: "ERP Warehouse",
						description: t("stock.count.warehouseHint") },
					{ fieldtype: "Small Text", fieldname: "notes", label: t("stock.count.notes") },
					{ fieldtype: "Button", fieldname: "pick", label: t("stock.count.importPick"),
						hidden: 1, click: pick("file_name") },
					{ fieldtype: "Data", fieldname: "file_name", label: t("stock.count.importFile"),
						read_only: 1, hidden: 1 },
					{ fieldtype: "Button", fieldname: "template",
						label: t("stock.count.template"), hidden: 1, click: () => {
							window.open(frappe.utils.get_url(
								`/api/method/${api}api_stock_count_template`));
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

		// ── 明细态 ────────────────────────────────────────────────────────────
		async function open_detail(name) {
			count = name;
			item_filter = "all";
			mode = "detail";
			clear();
			const task = await p.call(api + "api_get_stock_count", { name });
			const editable = task.status === "Active";
			page.add_inner_button(t("stock.action.export"), () => {
				window.open(frappe.utils.get_url(
					`/api/method/${api}api_stock_count_export?name=${encodeURIComponent(name)}`));
			});
			if (editable) {
				page.add_inner_button(t("stock.count.import"), () => open_import(name));
				page.add_inner_button(t("stock.count.close"), () => close_count(task));
				page.add_inner_button(t("stock.count.post"), () => post_count(task));
			}
			void draw_chart(name);
			const search = page.add_field({
				fieldtype: "Data",
				fieldname: "search",
				label: t("stock.field.search"),
				change: () => void list?.reload(),
			});
			const filter = page.add_field({
				fieldtype: "Select",
				fieldname: "item_filter",
				label: t("stock.count.filter"),
				options: item_filters.map((key) => t(`stock.count.filter.${key}`)).join("\n"),
				change: () => {
					const labels = item_filters.map((key) => t(`stock.count.filter.${key}`));
					const index = labels.indexOf(filter.get_value());
					item_filter = item_filters[index < 0 ? 0 : index];
					void list?.reload();
				},
			});
			const query = () => ({
				count: name,
				q: search.get_value() || undefined,
				filter: item_filter,
			});
			list = p.list({
				page,
				doctype: "ERP Stock Count Item",
				// 九列还要留出行上的动作按钮，按列给宽度让渲染器自己挤（原生机制），
				// 不给的话最后几列会被挤出去看不见。
				compact_columns: true,
				row_actions: (row) => [
					...(editable ? [{
						id: "add",
						label: t("stock.count.addEntry"),
						action: () => open_entry(row),
					}] : []),
					{
						id: "entries",
						label: t("stock.count.entries"),
						action: () => open_entries(row),
					},
				],
				load: async ({ start, page_length }) => {
					const [items, total] = await Promise.all([
						p.call(api + "api_stock_count_items",
							{ start, limit: page_length, ...query() }),
						p.call(api + "api_stock_count_item_total", query()),
					]);
					return { items, total };
				},
				columns: [
					{ fieldname: "product_name", label: t("stock.col.name"),
						width: 220, min_width: 140 },
					{ fieldname: "code", label: t("stock.col.code"),
						width: 100, min_width: 80 },
					{ fieldname: "barcode", label: t("stock.col.barcode"),
						width: 120, min_width: 90 },
					{ fieldname: "warehouse", label: t("stock.col.warehouse"),
						width: 100, min_width: 80 },
					{ fieldname: "unit", label: t("stock.col.unit"),
						width: 70, min_width: 55 },
					{ fieldname: "book_qty", label: t("stock.count.book"),
						width: 90, min_width: 75, format: (value) => qty(value) },
					{ fieldname: "counted_qty", label: t("stock.count.counted"),
						width: 90, min_width: 75,
						format: (value, row) => (row.counted ? qty(value) : t("stock.count.blank")) },
					{ fieldname: "difference", label: t("stock.count.difference"),
						width: 80, min_width: 65,
						format: (value, row) => (row.counted ? qty(value) : "—") },
					{ fieldname: "status", label: t("stock.col.status"),
						width: 100, min_width: 80,
						format: (_value, row) => row_state(row) },
				],
			});
			page.set_indicator(
				t("stock.count.headline", status_text(task), task.counted_total, task.item_total,
					task.difference_total),
				task.status === "Posted" ? "green" : (task.status === "Closed" ? "blue" : "orange"),
			);
			set_breadcrumb(task);
			void list.ready;
		}

		// 差异看板：这份盘点里"账面 vs 实盘"的条形，取差异最大的那几行（全量数拉不开看）。
		// 图表用框架自带的 frappe-charts，配色取产品自己的强调色令牌，不自绘。
		async function draw_chart(name) {
			const data = await p.call(api + "api_stock_count_chart", { name });
			if (mode !== "detail" || count !== name) return;
			const totals = data.totals || {};
			const card = $('<div class="pearnly-stock-chart"></div>').prependTo(page.main);
			card.append($("<div class='text-muted'></div>").text(
				t("stock.count.chartTotals", qty(totals.book), qty(totals.counted_qty),
					qty(totals.difference))));
			const holder = $('<div class="pearnly-stock-chart-body"></div>').appendTo(card);
			if (!data.labels.length) {
				holder.append($("<div class='text-muted'></div>").text(t("stock.count.chartEmpty")));
				return;
			}
			const accent = getComputedStyle(document.documentElement)
				.getPropertyValue("--pearnly-accent").trim() || "#7c4dff";
			chart = new frappe.Chart(holder[0], {
				type: "bar",
				height: 200,
				colors: ["#a3a3a3", accent],
				data: {
					labels: data.labels,
					datasets: [
						{ name: t("stock.count.book"), values: data.book },
						{ name: t("stock.count.counted"), values: data.counted },
					],
				},
				axisOptions: { shortenYAxisNumbers: 1 },
			});
		}

		// 往这份盘点里传表：同一份表重复上传是覆盖那一行，不是又记一笔。
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
							window.open(frappe.utils.get_url(
								`/api/method/${api}api_stock_count_template?name=${encodeURIComponent(name)}`));
						} },
				],
				t("stock.count.import"),
				async (_values, d) => {
					const result = await p.call(api + "api_stock_count_import",
						{ name, file_url }, "POST");
					d.hide();
					p.success(t("stock.count.imported", result.rows, result.counted));
					report_problems(result.problems);
					open_detail(name);
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		// 一条盘点记录 = 一次扫码/一次点数；同一商品可以记很多条，合计才是实盘数。
		function open_entry(row) {
			const dialog = p.dialog(
				t("stock.count.addEntryTitle", row.product_name),
				[
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("stock.count.addEntryHint",
							qty(row.book_qty), row.unit || ""))}</p>` },
					{ fieldtype: "Float", fieldname: "qty", label: t("stock.count.qty"), reqd: 1,
						precision: 3 },
					{ fieldtype: "Data", fieldname: "location", label: t("stock.count.location") },
					{ fieldtype: "Small Text", fieldname: "note", label: t("stock.count.note") },
				],
				t("stock.count.addEntry"),
				async (values, d) => {
					await p.call(api + "api_stock_count_add_entry", {
						count: count,
						item: row.name,
						qty: values.qty,
						location: values.location || undefined,
						note: values.note || undefined,
					}, "POST");
					d.hide();
					p.success(t("stock.count.entrySaved"));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		async function open_entries(row) {
			const entries = await p.call(api + "api_stock_count_entries",
				{ count, item: row.name, limit: 100 });
			const live = entries.filter((entry) => !Number(entry.voided));
			const labels = live.map((entry) => t("stock.count.entryOption",
				qty(entry.qty), entry.counted_by || "", entry.counted_at || ""));
			const dialog = p.dialog(
				t("stock.count.entriesTitle", row.product_name),
				[
					{ fieldtype: "HTML", fieldname: "entries" },
					...(live.length ? [
						{ fieldtype: "Select", fieldname: "entry",
							label: t("stock.count.voidPick"), options: labels.join("\n") },
						{ fieldtype: "Button", fieldname: "void", label: t("stock.count.void"),
							click: () => void_entry() },
					] : []),
				],
				__("Close"),
				(_values, d) => d.hide(),
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			const holder = dialog.get_field("entries").$wrapper;
			if (!entries.length) {
				holder.html(`<p class="text-muted">${p.escape(t("stock.count.noEntries"))}</p>`);
				return;
			}
			// 原生 DataTable（与采购列表同一个渲染器），不自绘表格
			void p.table(
				holder,
				[
					t("stock.count.qty"),
					t("stock.count.location"),
					t("stock.count.countedBy"),
					t("stock.count.countedAt"),
					t("stock.col.status"),
					t("stock.count.photos"),
				],
				entries.map((entry) => [
					qty(entry.qty),
					entry.location || "",
					entry.counted_by || "",
					entry.counted_at || "",
					entry.voided ? t("stock.count.entryVoided") : t("stock.count.entryLive"),
					entry.photo_total ? String(entry.photo_total) : "",
				]),
			);

			async function void_entry() {
				const picked = live[labels.indexOf(dialog.get_value("entry"))];
				if (!picked) return;
				await p.call(api + "api_stock_count_edit_entry",
					{ entry: picked.name, voided: 1 }, "POST");
				dialog.hide();
				p.success(t("stock.count.entryVoidedNotice"));
				await list.reload();
			}
		}

		function close_count(task) {
			frappe.confirm(t("stock.count.closeConfirm"),
				async () => {
					await p.call(api + "api_stock_count_close", { name: task.name }, "POST");
					p.success(t("stock.count.closed"));
					open_detail(task.name);
				});
		}

		function post_count(task) {
			frappe.confirm(t("stock.count.postConfirm"),
				async () => {
					const result = await p.call(api + "api_stock_count_post", { name: task.name },
						"POST");
					p.success(t("stock.count.posted", result.adjustments.length));
					open_detail(task.name);
				});
		}

		open_list();
	};
})();
