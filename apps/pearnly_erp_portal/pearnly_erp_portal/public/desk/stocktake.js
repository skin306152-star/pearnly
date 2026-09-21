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
			page.clear_inner_toolbar();
			page.clear_primary_action();
		}

		// ── 列表态 ────────────────────────────────────────────────────────────
		function open_list() {
			mode = "list";
			count = null;
			clear();
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
			page.set_primary_action(t("stock.count.new"), () => open_new());
			page.add_inner_button(t("stock.action.reload"), () => void list?.reload());
			void list.ready;
		}

		function open_new() {
			const dialog = p.dialog(
				t("stock.count.new"),
				[
					{ fieldtype: "Data", fieldname: "title", label: t("stock.count.name"), reqd: 1 },
					{ fieldtype: "Link", fieldname: "warehouse", label: t("stock.col.warehouse"),
						options: "ERP Warehouse",
						description: t("stock.count.warehouseHint") },
					{ fieldtype: "Small Text", fieldname: "notes", label: t("stock.count.notes") },
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("stock.count.newHint"))}</p>` },
				],
				t("stock.count.new"),
				async (values, d) => {
					const created = await p.call(api + "api_create_stock_count", {
						title: values.title,
						warehouse: values.warehouse || undefined,
						notes: values.notes || undefined,
					}, "POST");
					d.hide();
					p.success(t("stock.count.created", created.rows));
					await list.reload();
					open_detail(created.name);
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		// ── 明细态 ────────────────────────────────────────────────────────────
		async function open_detail(name) {
			count = name;
			item_filter = "all";
			mode = "detail";
			clear();
			const task = await p.call(api + "api_get_stock_count", { name });
			const editable = task.status === "Active";
			page.add_inner_button(t("stock.count.back"), () => open_list());
			page.add_inner_button(t("stock.action.export"), () => {
				window.open(frappe.utils.get_url(
					`/api/method/${api}api_stock_count_export?name=${encodeURIComponent(name)}`));
			});
			if (editable) {
				page.add_inner_button(t("stock.count.close"), () => close_count(task));
				page.add_inner_button(t("stock.count.post"), () => post_count(task));
			}
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
					{ fieldname: "product_name", label: t("stock.col.name") },
					{ fieldname: "code", label: t("stock.col.code") },
					{ fieldname: "barcode", label: t("stock.col.barcode") },
					{ fieldname: "warehouse", label: t("stock.col.warehouse") },
					{ fieldname: "unit", label: t("stock.col.unit") },
					{ fieldname: "book_qty", label: t("stock.count.book"),
						format: (value) => qty(value) },
					{ fieldname: "counted_qty", label: t("stock.count.counted"),
						format: (value, row) => (row.counted ? qty(value) : t("stock.count.blank")) },
					{ fieldname: "difference", label: t("stock.count.difference"),
						format: (value, row) => (row.counted ? qty(value) : "—") },
					{ fieldname: "status", label: t("stock.col.status"),
						format: (_value, row) => row_state(row) },
				],
			});
			page.set_indicator(
				t("stock.count.headline", status_text(task), task.counted_total, task.item_total,
					task.difference_total),
				task.status === "Posted" ? "green" : (task.status === "Closed" ? "blue" : "orange"),
			);
			void list.ready;
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
				t("stock.count.back"),
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
