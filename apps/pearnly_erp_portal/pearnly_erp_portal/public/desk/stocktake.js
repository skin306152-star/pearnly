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
		let list, table, ranking, moved, count, state, table_host, rank_host, rank_hint,
			moved_host, moved_hint, summary;
		let sequence = 0, refresh_sequence = 0, item_filter = "all", search, filter;
		const status_text = task => task.status === "Active" ? t("stock.count.statusActive") : t("stock.count.statusClosed");
		const open_detail = name => frappe.set_route(root, name);
		const qty = value => value == null ? "—" : p.number(value);
		// 有差异的地方标红：色值与差异看板、导出表同一个红（框架的 `--red-600`），
		// 桌面表格与 Excel 因此看到的是同一处红（用户 2026-09-22 口径）。
		const danger = value => `<span style="color: var(--red-600)">${value}</span>`;
		// 表格选项：列头菜单只留排序（"移除栏 / 冻结"由 core `p.table_menu` 去掉），
		// 所以这里不再需要给 Freeze/Unfreeze 补译文。
		const table_options = () => ({
			language: frappe.boot.lang,
			translations: frappe.utils.datatable.get_translations(),
		});
		const active = () => state?.task.status === "Active" && state.can_write;
		function clear() {
			list?.destroy(); table?.destroy(); ranking?.destroy(); moved?.destroy();
			list = table = ranking = moved = null;
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
			// 「有差异」清单的卡片（沿用框架 `.chart-wrapper` 那块留白，形态与之前一致）。
			rank_hint = $("<p class='text-muted'></p>").appendTo(page.main);
			rank_host = $("<div class='chart-wrapper'>").appendTo(page.main);
			moved_hint = $("<p class='text-muted'></p>").appendTo(page.main);
			moved_host = $("<div class='chart-wrapper'>").appendTo(page.main);
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
				// 删除这一声和"保存成功"不同（官方批量删除也是 `delete` 那一声）。
				sound: "delete",
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
					// 有差异的任务把这格标红（同一处红：差异看板 / 工作台 / 导出表）。
					// 列表这一层只出转义文本（塞 HTML 会被显示成标记），颜色走 `cell_class`。
					{ fieldname: "difference_total", width: 90, min_width: 72, label: t("stock.count.differenceRows"),
						cell_class: value => Number(value) ? "pearnly-difference" : "" },
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
			// 「有差异」清单：**所有**有差异的行都列出来（一条不滤），按差得多少排序，条越长差得越多，
			// 右边直接写"少货 62 提 / 多货 45 盒"。用的是原生 DataTable + desk 包自带的
			// Bootstrap `.progress` 条：没有坐标轴、没有正负号、没有图例，一线同事不用先懂"差异"
			// 这个词也能看懂（用户 2026-09-24 口径："现在这个太专业了，很多人看不懂的，
			// 我要的是直观易懂，且只要有差异的都在显示"）。
			// 数量差异为 0、只是放错货位的那几行也在这里（数字那格写"仓位差异"）。
			const accent = getComputedStyle(document.documentElement).getPropertyValue("--pearnly-accent").trim() || "#7c4dff";
			const counted_rows = state.items.filter(r => r.counted);
			const diff_rows = counted_rows.filter(r => Number(r.difference))
				.sort((a, b) => Math.abs(Number(b.difference)) - Math.abs(Number(a.difference)));
			const moved_rows = counted_rows.filter(r => r.location_difference)
				.sort((a, b) => String(a.product_name || "").localeCompare(String(b.product_name || "")));
			// 横条：灰＝账面数量（这一行的"满格"），彩＝实盘数量。整条轨道按**最大比例**归一化，
			// 所以超盘的彩条能压过灰条又不被裁掉；少盘的彩条短于灰条，一眼看出少了百分之多少。
			// 灰色与看板的账面柱同一个灰（`#a3a3a3`），条本身用 desk 包自带的 Bootstrap `.progress`。
			// 轨道最多画到账面的 1.5 倍：不让"账上 14 件却数出 61 件"这种极端行把别的条全压成一小截。
			// 超过 1.5 倍的按满格画（真值在右边那格照旧写出来）。
			const widest = Math.min(1.5, Math.max(1, ...diff_rows.filter(r => Number(r.book_qty) > 0)
				.map(r => Number(r.counted_qty || 0) / Number(r.book_qty))));
			const unit = 100 / widest;
			const bars = row => {
				const book = Number(row.book_qty) || 0, actual = Number(row.counted_qty) || 0;
				const book_width = book > 0 ? unit : 0;
				const actual_width = book > 0 ? Math.min(actual / book, widest) * unit : (actual > 0 ? 100 : 0);
				const colour = Number(row.difference) < 0 ? "var(--red-600)" : accent;
				return `<div class="progress" style="margin:0;position:relative">`
					+ `<div class="progress-bar" style="width:${book_width}%;background-color:#a3a3a3"></div>`
					+ `<div class="progress-bar" style="position:absolute;left:0;top:0;height:100%;`
					+ `width:${actual_width}%;background-color:${colour}"></div></div>`;
			};
			const amount_text = (row, diff) => {
				const amount = `${p.number(Math.abs(diff))} ${row.unit || ""}`.trim();
				return `${t(diff < 0 ? "stock.count.short" : "stock.count.over")} ${amount}`;
			};
			ranking?.destroy(); ranking = null;
			rank_hint.text(diff_rows.length ? t("stock.count.diffRankHint") : "");
			if (diff_rows.length) {
				ranking = await p.table(rank_host, [
					{ name: t("stock.col.name"), width: 190, format: value => value },
					{ name: w("bookPosition"), width: 160, format: value => value },
					{ name: `${t("stock.count.book")} / ${t("stock.count.counted")}`, width: 300, format: value => value },
					{ name: w("quantityDiff"), width: 170, format: value => value },
				], diff_rows.map(row => {
					const diff = Number(row.difference);
					return [
						p.escape(row.product_name),
						p.escape(`${row.warehouse_name} / ${row.location || t("stock.count.blank")}`),
						bars(row),
						`<span style="color: ${diff < 0 ? "var(--red-600)" : accent}">`
							+ `${p.escape(amount_text(row, diff))}</span>`,
					];
				}), table_options());
			} else {
				rank_host.empty();
			}
			// 仓库/仓位的差异是**另一类**问题：数量条只能表达数量（放错位置的行数量正好对上，
			// 在条上什么也看不出来）。所以单独列一小块"账面位置 → 实际位置"，条数正好等于
			// 汇总里的"仓位差异"。实际问题落点（哪个仓哪个位）在下面的明细表里也有。
			moved?.destroy(); moved = null;
			moved_hint.text(moved_rows.length ? t("stock.count.locationHint") : "");
			if (moved_rows.length) {
				moved = await p.table(moved_host, [
					{ name: t("stock.col.name"), width: 220, format: value => value },
					{ name: w("bookPosition"), width: 200, format: value => value },
					{ name: w("actualPositions"), width: 300, format: value => value },
				], moved_rows.map(row => [
					p.escape(row.product_name),
					p.escape(`${row.warehouse_name} / ${row.location || t("stock.count.blank")}`),
					danger(p.escape(row.actual_positions || "—")),
				]), table_options());
			} else {
				moved_host.empty();
			}
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
				{ name: w("bookPosition"), width: 160 },
				// 这几列会自己拼红字（差异那几格），所以不走默认的 `p.escape`。
				{ name: w("actualPositions"), width: 230, format: value => value },
				{ name: t("stock.col.unit"), width: 70 }, { name: t("stock.count.book"), width: 90 },
				{ name: t("stock.count.counted"), width: 90 },
				{ name: w("quantityDiff"), width: 90, format: value => value },
				...(state.can_view_costs ? [{ name: t("stock.count.differenceValue"), width: 110, format: value => value }] : []),
				{ name: t("stock.col.status"), width: 140, format: value => value },
			];
			table = await p.table(table_host, columns.map(c=>({ ...c, format: c.format || p.escape })), rows.map(r=>{
				// 有差异的三处：数量差异、差异金额、以及仓位对不上时的实际仓位 —— 标红；
				// 状态列在这一行有问题时一起标红（"未盘点"不算差异，保持原色）。
				const quantity_diff = Boolean(r.counted) && Number(r.difference) !== 0;
				const value_diff = quantity_diff && Boolean(Number(r.difference_value));
				const moved = Boolean(r.location_difference);
				const status = r.stock_changed ? w("needsRecount") : !r.counted ? t("stock.count.rowUncounted")
					: moved ? w("locationDiff") : quantity_diff ? t("stock.count.rowDifference") : t("stock.count.rowMatch");
				return [
					`<a href="#" data-count-row="${p.escape(r.name)}">${p.escape(r.product_name)}</a>`, r.code || "", r.barcode || "",
					`${p.escape(r.warehouse_name)} / ${p.escape(r.location || t("stock.count.blank"))}`,
					moved ? danger(p.escape(r.actual_positions || "—")) : p.escape(r.actual_positions || "—"),
					r.unit || "",
					qty(r.book_qty), r.counted ? qty(r.counted_qty) : "—",
					r.counted ? (quantity_diff ? danger(qty(r.difference)) : qty(r.difference)) : "—",
					...(state.can_view_costs ? [value_diff ? danger(qty(r.difference_value)) : qty(r.difference_value)] : []),
					quantity_diff || moved || r.stock_changed ? danger(p.escape(status)) : p.escape(status),
				];
			}), table_options());
		}
		async function records(row) {
			const entries = state.entries.filter(e=>e.item===row.name);
			const d = new frappe.ui.Dialog({ title: t("stock.count.entriesTitle", row.product_name), size: "extra-large", fields: [
				{ fieldtype: "HTML", fieldname: "hint", options: `<p class="text-muted">${p.escape(w("bookPosition"))}: ${p.escape(row.warehouse_name)} / ${p.escape(row.location || "—")} · ${qty(row.book_qty)} ${p.escape(row.unit || "")}</p>` },
				{ fieldtype: "HTML", fieldname: "entries" },
				// 表格下面单独一片看照片：结束盘点之后也在这里看（用户 2026-09-22 口径：
				// "除了导出能看图片，盘点结束的有没有什么办法查看图片"）。
				{ fieldtype: "HTML", fieldname: "photos" },
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
					// 实盘仓库 / 仓位与账面不同的那几格标红（与工作台、导出表同一条规矩）。
					{ name: w("actualWarehouse"), format: value => value },
					{ name: w("actualLocation"), format: value => value },
					t("stock.count.qty"), t("stock.count.note"),
					t("stock.count.countedBy"), t("stock.count.countedAt"),
					{ name: t("stock.col.status"), format: value=>value },
				], entries.map(e=>{
					// 哪一格对不上就标哪一格：仓库不同标仓库，仓位不同标仓位（和导出明细同一条规矩）。
					const position = (different, text) => different ? danger(text) : text;
					return [position(e.warehouse_name !== row.warehouse_name, p.escape(e.warehouse_name)),
						position((e.location || "") !== (row.location || ""), p.escape(e.location || "—")),
					// 时间一律走 `p.datetime_text`（库里带微秒，界面上只看到分钟）：
					// 产品里所有时间显示都用这一个入口，别再一处一处报。
					qty(e.qty), e.note || "", e.counted_by, p.datetime_text(e.counted_at),
					e.voided ? p.escape(t("stock.count.entryVoided")) : active()
						? `<button class="btn btn-default btn-xs" data-edit-count-entry="${p.escape(e.name)}">${p.escape(w("editEntry"))}</button> <button class="btn btn-default btn-xs" data-void-count-entry="${p.escape(e.name)}">${p.escape(t("stock.count.void"))}</button>`
						: p.escape(t("stock.count.entryLive"))];
				}), table_options());
				await render_photos();
			};
			// 该商品在本任务里的照片：点开看原图（私有文件，按当前登陆身份取）。
			async function render_photos() {
				const host = d.fields_dict.photos?.$wrapper;
				if (!host) return;
				const photos = await p.call(api + "api_stock_count_item_photos", { count, item: row.name });
				if (!host[0]?.isConnected) return;
				host.empty();
				if (!photos.length) return;
				$("<p class='text-muted'>").text(`${w("photos")} ${photos.length}`).appendTo(host);
				const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
				for (const photo of photos) {
					const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
					$("<a>").attr({ href: photo.file_url, target: "_blank", rel: "noopener" })
						.append($("<img>").attr({ src: photo.file_url, alt: photo.file_name || "" }))
						.appendTo(cell);
					$("<span class='text-muted'>").text([
						`${photo.warehouse_name || ""} / ${photo.location || t("stock.count.blank")}`,
						// 说明行只留"哪里、多少"，时间不放了（用户 2026-09-22：底部那排字太占位置、
						// 不雅观）。时间在弹窗上面的记录表里已经有。
						qty(photo.qty),
						photo.voided ? t("stock.count.entryVoided") : "",
					].filter(Boolean).join(" · ")).appendTo(cell);
				}
			}
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
					// 仓库：官方原生 Link（只列当前公司的仓库），不是自由填写。
					{ fieldtype: "Link", options: "ERP Warehouse", fieldname: "warehouse",
						label: t("stock.col.warehouse"), get_query: p.warehouse_query(), only_select: true },
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
					p.success(t("stock.count.created", created.rows), "click");
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
