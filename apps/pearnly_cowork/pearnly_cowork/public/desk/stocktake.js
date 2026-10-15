/* 库存盘点（Cowork，`/desk/pearnly-stocktake`）：**ERP 盘点页的原样副本**。
 *
 * 用户 2026-09-26：“只去掉盘点在库就好了，其它照搬。” 源文件 `pearnly_erp_portal/public/desk/stocktake.js`；
 * 这里只做了：接口前缀换成 Cowork（同名同形的接口在 `stocktake/desk_api.py`）、词条前缀加 `cowork.`、
 * 路由与 DocType 名换成 Cowork 的、新建弹窗去掉「盘点在库」与仓库下拉，列表去掉仓库列
 * （Cowork 没有库存系统和仓库主档，任务只能从上传的表建）。改 ERP 那份时请同步这份。
 */
(() => {
	const p = frappe.pearnly;
	const root = "pearnly-stocktake";
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
		if (entries) trail.push({ label: __("cowork.stock.count.entries"), route: "" });
		if (report) trail.push({ label: __("cowork.stock.board.title"), route: "" });
		p.breadcrumbs({ label: __("nav.stocktake"), route: url(), trail });
	};

	p.coworkStocktake = async (page, _shell_t, wrapper) => {
		const t = await p.texts("cowork.stock.");
		const api = "pearnly_cowork.stocktake.desk_api.";
		const w = (key, ...args) => t(`cowork.stock.workbench.${key}`, ...args);
		let list, table, count, state, table_host, conflict;
		let sequence = 0, refresh_sequence = 0, item_filter = "all", search, filter;
		// 多人分区盘点（用户 2026-10-15："a 在 a 仓库盘某类商品，b 在某仓库盘某类商品"）：
		// 每人选自己负责的「仓库 / 类别」，表格与进度只看这一片；没选时上面列出各区进度和谁在盘。
		let scope_warehouse, scope_category, area_host, area_table, scoping = false, shown_screen;
		// 自己刚保存过：服务端也会推给本人（另一台设备要刷新），这一页 3 秒内就不再重取一遍。
		let saved_at = 0;
		const own_refresh = () => { saved_at = Date.now(); return refresh(); };
		const BLANK = "\u0000";
		const area_of = row => row.warehouse_name || BLANK;
		// ERP 的类别是商品主档上的编号（纯数字），单独显示一个数看不出是什么，前面带上「分类」。
		const category_label = value => value === BLANK ? t("cowork.stock.count.blank")
			: /^\d+$/.test(value) ? `${t("cowork.stock.col.category")} ${value}` : value;
		const scope = () => ({ warehouse: scope_warehouse?.get_value() || "", category: scope_category?.get_value() || "" });
		const in_scope = row => {
			const chosen = scope();
			return (!chosen.warehouse || area_of(row) === chosen.warehouse)
				&& (!chosen.category || (row.category || BLANK) === chosen.category);
		};
		// 记录上的盘点人：Cowork 给的是名字，ERP 给的是账号 —— 都按名字显示。
		const who = user => frappe.user_info?.(user)?.fullname || user || "";
		// 手机竖屏只留四列；横屏 / 平板收成六列按比例铺满；电脑上全列。转屏跨档就重画。
		const screen = () => window.matchMedia("(max-width: 575px)").matches ? "phone"
			: window.matchMedia("(max-width: 991px)").matches ? "compact" : "full";
		const status_text = task => task.status === "Active" ? t("cowork.stock.count.statusActive") : t("cowork.stock.count.statusClosed");
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
			list?.destroy(); table?.destroy(); area_table?.destroy();
			list = table = area_table = null;
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
			search = page.add_field({ fieldtype: "Data", fieldname: "search", label: t("cowork.stock.field.search"),
				change: () => render_data() });
			filter = page.add_field({ fieldtype: "Select", fieldname: "only", label: t("cowork.stock.count.filter"),
				options: ["all", "uncounted", "differences", "locations"].map(value => ({ value,
					label: value === "locations" ? w("locationDiff") : t(`cowork.stock.count.filter.${value}`) })),
				change: () => { item_filter = filter.get_value(); render_data(); } });
			scope_warehouse = page.add_field({ fieldtype: "Select", fieldname: "scope_warehouse",
				label: t("cowork.stock.col.warehouse"), change: () => choose_scope() });
			scope_category = page.add_field({ fieldtype: "Select", fieldname: "scope_category",
				label: t("cowork.stock.col.category"), change: () => choose_scope() });
			// 库存被别的业务改过时要重盘：这条警告原先挂在汇总卡片里，卡片删掉后直接排在表格上方
			// （空字符串时不留任何痕迹）。
			conflict = $("<p class='text-danger'></p>").appendTo(page.main);
			// 手机上框架把表格滚动区强制成一整屏高，这张小表在 product.css 里按行数定高。
			area_host = $("<div class='report-wrapper pearnly-stock-areas mb-4'>").hide().appendTo(page.main);
			area_host.on("click", ".dt-row", event => {
				const key = $(event.currentTarget).find("[data-count-area]").attr("data-count-area");
				if (key == null) return;
				event.preventDefault();
				const [warehouse, category] = JSON.parse(key);
				void pick_scope(warehouse, category);
			});
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
			// 从 LINE 普通内置浏览器转到 LIFF 时带着 `pearnly_scan=1`：接着开扫码（LINE 会弹原生相机授权）。
			const params = new URLSearchParams(window.location.search);
			if (params.get("pearnly_scan") && request === sequence && active()) {
				params.delete("pearnly_scan");
				window.history.replaceState(null, "", window.location.pathname + (params.size ? `?${params}` : ""));
				void entry_form(null, true);
			}
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
					page.set_primary_action(t("cowork.stock.count.new"), open_new);
				},
				label: rows => t("cowork.stock.count.deleteSelected", rows.length),
				when: row => row.status === "Active",
				reason: () => t("cowork.stock.count.deleteBlocked"),
				confirm: rows => t("cowork.stock.count.deleteConfirm", rows.length),
				run: async rows => {
					for (const row of rows) {
						await p.call(api + "api_stock_count_delete", { name: row.name }, "POST");
					}
				},
				success: rows => t("cowork.stock.count.deleted", rows.length),
				// 删除这一声和"保存成功"不同（官方批量删除也是 `delete` 那一声）。
				sound: "delete",
			});
			list = p.list({ page, doctype: "Cowork Stocktake Task", selectable: true, on_selection: selection,
				compact_columns: true,
				on_open: row => open_detail(row.name),
				empty_action: { label: t("cowork.stock.count.new"), action: open_new },
				// 列头点一下排序（`sort_fields` 声明服务端认的字段，见 core `p.list`）。
				// 仓库列显示的是仓库名（由 link 名翻出来），排 link 名没意义，所以它不在这里。
				sort_fields: ["title", "status", "item_total", "counted_total",
					"difference_total", "creation"],
				load: async ({ start, page_length, sort_by, sort_order }) => {
					const [items, total] = await Promise.all([
						p.call(api + "api_list_stock_counts",
							{ start, limit: page_length, sort_by, sort_order }),
						p.call(api + "api_stock_count_total"),
					]); return { items, total };
				}, columns: [
					// `compact_columns` + 每列 width/min_width：框架默认给每列 `min-width: 150px`
					// 且不收缩，七列就要 1050px —— 窗口窄一点，最右边那列（创建时间）就被挤出
					// 可视区、只剩一串省略号（用户 2026-09-22："那个时间被截断了"）。这里的写法
					// 与花名册那份列表一致：给出宽度与下限，让列能收缩到都看得见。
					{ fieldname: "title", width: 220, min_width: 160, label: t("cowork.stock.count.name") },
					{ fieldname: "status", width: 100, min_width: 80, label: t("cowork.stock.col.status"),
						format: (_v, row) => status_text(row) },
					{ fieldname: "item_total", width: 80, min_width: 64, label: t("cowork.stock.count.rows") },
					{ fieldname: "counted_total", width: 90, min_width: 72, label: t("cowork.stock.count.countedRows") },
					// 有差异的任务把这格标红（同一处红：差异看板 / 工作台 / 导出表）。
					// 列表这一层只出转义文本（塞 HTML 会被显示成标记），颜色走 `cell_class`。
					{ fieldname: "difference_total", width: 90, min_width: 72, label: t("cowork.stock.count.differenceRows"),
						cell_class: value => Number(value) ? "pearnly-difference" : "" },
					// 创建时间是库里的 datetime（带秒与微秒），直接显示会被截成一串省略号 ——
					// 按"日期 时:分"显示（用户 2026-09-22 口径："那个时间格式不对"）。
					{ fieldname: "creation", width: 160, min_width: 148, label: t("cowork.stock.count.createdAt"),
						format: value => p.datetime_text(value) },
				] });
			page.set_primary_action(t("cowork.stock.count.new"), open_new);
		}
		async function refresh() {
			const request = ++refresh_sequence, name = count;
			const result = await p.call(api + "api_stock_count_review", { name });
			if (request !== refresh_sequence || count !== name || frappe.get_route()[0] !== root) return;
			state = result;
			p.stockCountBreadcrumbs({ name, title: state.task.title });
			page.clear_inner_toolbar(); page.clear_primary_action();
			await scope_options();
			headline();
			// 进行中的任务只留结束盘点 + 右边那颗开始盘点（用户 2026-09-22 口径；10-07 起所有界面不放刷新按钮）。
			// 已结束的是只读态 —— 之前定过"结束后只可查看和导出"，所以那一档只留导出。
			if (active()) {
				page.set_primary_action(t("cowork.stock.count.start"), () => entry_form(null, true));
				page.add_inner_button(t("cowork.stock.count.close"), finish);
			} else {
				page.add_inner_button(t("cowork.stock.action.export"), () => void p.export_file(
					`${api}api_stock_count_export`, {name}));
			}
			await render_data();
		}
		// 进度那一行：选了范围就只算这一片（数法与服务端的任务合计相同：有实盘且数量不等才算差异）。
		function headline() {
			const rows = state.items.filter(in_scope);
			const chosen = scope().warehouse || scope().category;
			const counted = chosen ? rows.filter(r => r.counted).length : state.task.counted_total;
			const total = chosen ? rows.length : state.task.item_total;
			const differences = chosen ? rows.filter(r => r.counted && Number(r.difference) !== 0).length
				: state.task.difference_total;
			page.set_indicator(t("cowork.stock.count.headline", status_text(state.task), counted, total, differences),
				active() ? "orange" : "green");
		}
		// 范围选项来自这份盘点自己的行；只有一个值时不出现。上次在这份盘点上选的范围记在本机。
		const scope_key = () => `pearnly-stocktake-scope:${count}`;
		async function scope_options() {
			const label = value => value === BLANK ? t("cowork.stock.count.blank") : value;
			const fill = async (control, values, chosen, name = label) => {
				const options = [...new Set(values)].sort((a, b) => name(a).localeCompare(name(b), undefined, { numeric: true }));
				// 「不限」那一项就显示字段名（仓库 / 类别），一眼看得出这一格在选什么。
				control.df.options = [{ value: "", label: control.df.label },
					...options.map(value => ({ value, label: name(value) }))];
				control.df.hidden = options.length > 1 ? 0 : 1;
				control.refresh();
				await control.set_value(options.length > 1 && options.includes(chosen) ? chosen : "");
			};
			let saved = {};
			try { saved = JSON.parse(localStorage.getItem(scope_key()) || "{}") || {}; } catch (_error) { /* Private mode. */ }
			const chosen = scope().warehouse || scope().category ? scope() : saved;
			scoping = true;
			try {
				await fill(scope_warehouse, state.items.map(area_of), chosen.warehouse);
				await fill(scope_category, state.items.map(r => r.category || BLANK), chosen.category, category_label);
			} finally { scoping = false; }
		}
		function choose_scope() {
			if (scoping || !state) return;
			try { localStorage.setItem(scope_key(), JSON.stringify(scope())); } catch (_error) { /* Private mode. */ }
			headline();
			void render_data();
		}
		async function pick_scope(warehouse, category) {
			scoping = true;
			try {
				if (!scope_warehouse.df.hidden) await scope_warehouse.set_value(warehouse);
				if (!scope_category.df.hidden) await scope_category.set_value(category);
			} finally { scoping = false; }
			choose_scope();
		}
		// 各区进度：按「仓库 · 类别」分组，已盘 / 行数、差异、谁在盘（从有效记录推出，不另设指派）。
		// 只在没选范围、而且不止一个区时出现；点一行就把范围切到那个区。
		async function render_areas() {
			area_table?.destroy(); area_table = null;
			area_host.empty().hide();
			const chosen = scope();
			const by_category = !scope_category.df.hidden;
			const by_warehouse = !scope_warehouse.df.hidden;
			if (chosen.warehouse || chosen.category) return;
			const owner = new Map(state.items.map(r => [r.name, r]));
			const areas = new Map();
			for (const row of state.items) {
				const key = JSON.stringify([area_of(row), by_category ? row.category || BLANK : ""]);
				if (!areas.has(key)) areas.set(key, { rows: 0, counted: 0, differences: 0, people: new Set() });
				const area = areas.get(key);
				area.rows += 1;
				if (row.counted) area.counted += 1;
				if (row.counted && Number(row.difference) !== 0) area.differences += 1;
			}
			for (const entry of state.entries) {
				const row = owner.get(entry.item);
				if (!row || entry.voided) continue;
				areas.get(JSON.stringify([area_of(row), by_category ? row.category || BLANK : ""]))?.people.add(who(entry.counted_by));
			}
			if (areas.size < 2) return;
			const label = value => value === BLANK ? t("cowork.stock.count.blank") : value;
			const size = screen();
			// 按行数 × 行高给这张小表定高：手机上框架会用 `!important` 把它盖成一整屏（见 product.css）。
			// 先按常见行高预设（表格按容器高度决定画几行，0 高就一行都不画），画完再按实际行高校正。
			const fit = height => area_host[0].style.setProperty("--pearnly-areas-height", `${Math.ceil(height * areas.size) + 2}px`);
			fit(40);
			area_host.show();
			area_table = await p.table(area_host, [
				{ name: w("area"), width: size === "full" ? 260 : 4, format: value => value },
				{ name: t("cowork.stock.count.countedRows"), width: size === "full" ? 110 : 2 },
				{ name: t("cowork.stock.count.differenceRows"), width: size === "full" ? 110 : 2, format: value => value },
				...(size === "phone" ? [] : [{ name: t("cowork.stock.count.countedBy"), width: size === "full" ? 260 : 3 }]),
			], [...areas].map(([key, area]) => {
				const [warehouse, category] = JSON.parse(key);
				// 只有一个仓时区名只写类别，只有一种类别时只写仓库（手机上不被截断）。
				const name = [by_warehouse || !category ? label(warehouse) : "", category ? category_label(category) : ""]
					.filter(Boolean).join(" · ");
				const cells = [`<a href="#" data-count-area="${p.escape(key)}">${p.escape(name)}</a>`,
					`${area.counted} / ${area.rows}`, area.differences ? danger(area.differences) : "0"];
				if (size !== "phone") cells.push([...area.people].join(", "));
				return cells;
			}), { ...table_options(), ...(size === "full" ? {} : { layout: "ratio" }) });
			const row = area_host.find(".dt-body .dt-row")[0]?.getBoundingClientRect().height;
			if (row) fit(row);
		}
		function entry_form(row = null, continuous = false, entry = null) {
			return p.stockCountEntry({ count, t, row, entry, continuous, on_saved: own_refresh,
				on_done: () => filter?.set_value("differences") });
		}
		async function render_data() {
			if (!state || !table_host || !table_host[0].isConnected) return;
			// 最上面那张四格汇总卡片已按用户口径删掉；任务进度仍在页面指示器上
			// （`进行中 · 已盘点 x / y · 差异 z`），每个数字与差异明细在明细表里。
			conflict.text(state.conflicts.length ? w("stockChanged") : "");
			await render_areas();
			if (!table_host[0].isConnected) return;
			const q = (search?.get_value() || "").trim().toLowerCase();
			const rows = state.items.filter(r => in_scope(r) && (!q || [r.product_name,r.code,r.barcode,r.location,r.category,r.actual_positions].join(" ").toLowerCase().includes(q))
				&& (item_filter !== "uncounted" || !r.counted)
				&& (item_filter !== "differences" || r.counted && (Number(r.difference) || r.location_difference))
				&& (item_filter !== "locations" || r.location_difference));
			table?.destroy(); table = null;
			if (!rows.length) {
				const empty = $(frappe.views.ListView.prototype.get_no_result_message.call({ doctype: "Cowork Stocktake Item",
					meta: { description: __("Nothing to show") }, can_create: active(), get_documentation_link: () => "" }));
				empty.find(".btn-new-doc").text(w("record")).on("click", () => entry_form());
				table_host.empty().append(empty); return;
			}
			// 手机上（用户 2026-09-26：11 列挤成一堆看不了）只留盘点时要看的四列：物料、账面、实际、差异；
			// 编码 / 条码 / 仓位 / 单位 / 金额 / 状态点物料名进去看。仍是同一张官方表格，只是列少了，
			// 并按比例铺满屏宽（ERP 录制问题 #22：五列定宽加起来比手机屏宽，物料名被挤得只剩两个字）。
			// 横屏 / 平板（用户 2026-10-15："旋转横屏后表格也没显示完整"）以前直接给电脑版 11 列定宽，
			// 现在收成六列（物料、账面位置、账面、实际、差异、状态）按比例铺满；转屏跨档时重画。
			// 状态并进物料名：这一行有问题（差异、位置不对、要重盘）物料名标红。
			const size = screen();
			shown_screen = size;
			const narrow = size === "phone";
			// `fit`：[电脑定宽, 横屏比例, 竖屏比例]，null 表示这一档不显示。
			const columns = [
				{ name: t("cowork.stock.col.name"), fit: [210, 4, 5], format: value => value },
				{ name: t("cowork.stock.col.code"), fit: [100, null, null] }, { name: t("cowork.stock.col.barcode"), fit: [140, null, null] },
				{ name: w("bookPosition"), fit: [160, 3, null] },
				// 这几列会自己拼红字（差异那几格），所以不走默认的 `p.escape`。
				{ name: w("actualPositions"), fit: [230, null, null], format: value => value },
				{ name: t("cowork.stock.col.unit"), fit: [70, null, null] }, { name: t("cowork.stock.count.book"), short: t("cowork.stock.count.bookShort"), fit: [90, 2, 2] },
				{ name: t("cowork.stock.count.counted"), short: t("cowork.stock.count.countedShort"), fit: [90, 2, 2] },
				{ name: w("quantityDiff"), short: t("cowork.stock.count.diffShort"), fit: [90, 2, 2], format: value => value },
				...(state.can_view_costs ? [{ name: t("cowork.stock.count.differenceValue"), fit: [110, null, null], format: value => value }] : []),
				{ name: t("cowork.stock.col.status"), fit: [140, 3, null], format: value => value },
			];
			const slot = { full: 0, compact: 1, phone: 2 }[size];
			// 手机竖屏列窄，数量三列用短列名（账面 / 实盘 / 差异），不被截成「账…」。
			const shown = columns.map((column, index) => [{ ...column, width: column.fit[slot],
				name: size === "phone" && column.short ? column.short : column.name }, index])
				.filter(([column]) => column.width != null);
			const pick = cells => shown.map(([_column, index]) => cells[index]);
			table = await p.table(table_host, shown.map(([c])=>({ ...c, format: c.format || p.escape })), rows.map(r=>{
				// 有差异的三处：数量差异、差异金额、以及仓位对不上时的实际仓位 —— 标红；
				// 状态列在这一行有问题时一起标红（"未盘点"不算差异，保持原色）。
				const quantity_diff = Boolean(r.counted) && Number(r.difference) !== 0;
				const value_diff = quantity_diff && Boolean(Number(r.difference_value));
				const moved = Boolean(r.location_difference);
				const status = r.stock_changed ? w("needsRecount") : !r.counted ? t("cowork.stock.count.rowUncounted")
					: moved ? w("locationDiff") : quantity_diff ? t("cowork.stock.count.rowDifference") : t("cowork.stock.count.rowMatch");
				const flagged = quantity_diff || moved || r.stock_changed;
				// 红色要写在链接自己身上：包在外面会被链接的颜色盖掉。
				const red = narrow && flagged ? ' style="color: var(--red-600)"' : "";
				const name = `<a href="#" data-count-row="${p.escape(r.name)}"${red}>${p.escape(r.product_name)}</a>`;
				return pick([
					name, r.code || "", r.barcode || "",
					`${p.escape(r.warehouse_name)} / ${p.escape(r.location || t("cowork.stock.count.blank"))}`,
					moved ? danger(p.escape(r.actual_positions || "—")) : p.escape(r.actual_positions || "—"),
					r.unit || "",
					qty(r.book_qty), r.counted ? qty(r.counted_qty) : "—",
					r.counted ? (quantity_diff ? danger(qty(r.difference)) : qty(r.difference)) : "—",
					...(state.can_view_costs ? [value_diff ? danger(qty(r.difference_value)) : qty(r.difference_value)] : []),
					quantity_diff || moved || r.stock_changed ? danger(p.escape(status)) : p.escape(status),
				]);
			}), { ...table_options(), ...(size === "full" ? {} : { layout: "ratio" }) });
		}
		async function records(row) {
			const entries = state.entries.filter(e=>e.item===row.name);
			const d = new frappe.ui.Dialog({ title: t("cowork.stock.count.entriesTitle", row.product_name), size: "extra-large", fields: [
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
					await own_refresh(); later(()=>entry_form(row));
				}));
			}
			d.on_page_show = async () => {
				if (detail) return;
				detail = await p.table(d.fields_dict.entries.$wrapper, [
					// 实盘仓库 / 仓位与账面不同的那几格标红（与工作台、导出表同一条规矩）。
					{ name: w("actualWarehouse"), format: value => value },
					{ name: w("actualLocation"), format: value => value },
					t("cowork.stock.count.qty"), t("cowork.stock.count.note"),
					t("cowork.stock.count.countedBy"), t("cowork.stock.count.countedAt"),
					{ name: t("cowork.stock.col.status"), format: value=>value },
				], entries.map(e=>{
					// 哪一格对不上就标哪一格：仓库不同标仓库，仓位不同标仓位（和导出明细同一条规矩）。
					const position = (different, text) => different ? danger(text) : text;
					return [position(e.warehouse_name !== row.warehouse_name, p.escape(e.warehouse_name)),
						position((e.location || "") !== (row.location || ""), p.escape(e.location || "—")),
					// 时间一律走 `p.datetime_text`（库里带微秒，界面上只看到分钟）：
					// 产品里所有时间显示都用这一个入口，别再一处一处报。
					qty(e.qty), e.note || "", e.counted_by, p.datetime_text(e.counted_at),
					e.voided ? p.escape(t("cowork.stock.count.entryVoided")) : active()
						? `<button class="btn btn-default btn-xs" data-edit-count-entry="${p.escape(e.name)}">${p.escape(w("editEntry"))}</button> <button class="btn btn-default btn-xs" data-void-count-entry="${p.escape(e.name)}">${p.escape(t("cowork.stock.count.void"))}</button>`
						: p.escape(t("cowork.stock.count.entryLive"))];
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
						`${photo.warehouse_name || ""} / ${photo.location || t("cowork.stock.count.blank")}`,
						// 说明行只留"哪里、多少"，时间不放了（用户 2026-09-22：底部那排字太占位置、
						// 不雅观）。时间在弹窗上面的记录表里已经有。
						qty(photo.qty),
						photo.voided ? t("cowork.stock.count.entryVoided") : "",
					].filter(Boolean).join(" · ")).appendTo(cell);
				}
			}
			d.fields_dict.entries.$wrapper.on("click", "[data-edit-count-entry]", event => {
				const entry = entries.find(e=>e.name===event.currentTarget.dataset.editCountEntry);
				later(()=>entry_form(row, false, entry));
			}).on("click", "[data-void-count-entry]", event => {
				const entry = entries.find(e=>e.name===event.currentTarget.dataset.voidCountEntry);
				frappe.confirm(t("cowork.stock.count.voidConfirm", qty(entry.qty)), async () => {
					await p.call(api + "api_stock_count_edit_entry", { entry: entry.name, version: entry.version, voided: 1 }, "POST");
					d.hide(); await own_refresh();
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
				p.success(t("cowork.stock.count.closed")); await refresh();
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
				t("cowork.stock.count.new"),
				[
					{ fieldtype: "Data", fieldname: "title", label: t("cowork.stock.count.name"), reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "notes", label: t("cowork.stock.count.notes") },
					{ fieldtype: "Button", fieldname: "pick", label: t("cowork.stock.count.importPick"),
						click: pick("file_name") },
					{ fieldtype: "Data", fieldname: "file_name", label: t("cowork.stock.count.importFile"),
						read_only: 1 },
					{ fieldtype: "Button", fieldname: "template",
						label: t("cowork.stock.count.template"), click: () => {
							window.location.assign(`/api/method/${api}api_stock_count_template`);
						} },
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("cowork.stock.count.newHint"))}</p>` },
				],
				t("cowork.stock.count.new"),
				async (values, d) => {
					if (!file_url) {
						frappe.msgprint({
							title: t("cowork.stock.count.modeImport"),
							indicator: "orange",
							message: t("cowork.stock.count.importMissing"),
						});
						return;
					}
					const created = await p.call(api + "api_create_stock_count", {
						title: values.title,
						notes: values.notes || undefined,
						file_url,
					}, "POST");
					d.hide();
					p.success(t("cowork.stock.count.created", created.rows), "click");
					report_problems(created.problems);
					await list.reload();
					open_detail(created.name);
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
		}

		// 表里认不出的行逐条列出来（坏行只跳过自己，别的照进）—— 不悄悄吞掉。
		function report_problems(problems) {
			if (!problems || !problems.length) return;
			const reasons = {
				code_not_found: t("cowork.stock.count.problemCode"),
				no_identifier: t("cowork.stock.count.problemIdentifier"),
				warehouse_not_found: t("cowork.stock.count.problemWarehouse"),
				"book_qty:not_a_number": t("cowork.stock.count.problemNumber"),
				"book_qty:invalid": t("cowork.stock.count.problemNumber"),
				"counted_qty:not_a_number": t("cowork.stock.count.problemNumber"),
				"counted_qty:invalid": t("cowork.stock.count.problemNumber"),
			};
			frappe.msgprint({
				title: t("cowork.stock.count.importTitle"),
				indicator: "orange",
				message: t("cowork.stock.count.importSkipped", problems.length) + "<br>"
					+ problems.slice(0, 20).map((row) =>
						`${p.escape(row.row)}: ${p.escape(reasons[row.reason] || __(row.reason))}`
					).join("<br>"),
			});
		}

		// 多人同时盘点：别人记了 / 改了 / 结束了，服务端只推盘点号，这里按本人权限重新取
		// （原生 socket.io；连不上时照常手动刷新）。列表页上就刷新列表里的进度数。
		const live = frappe.utils.debounce(data => {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			if (!route[1]) void list?.reload?.();
			else if (data?.count === count && Date.now() - saved_at > 3000) void refresh();
		}, 800);
		frappe.realtime.off("pearnly_stocktake_changed");
		frappe.realtime.on("pearnly_stocktake_changed", data => live(data));
		$(window).off("resize.pearnlyStocktake").on("resize.pearnlyStocktake", frappe.utils.debounce(() => {
			if (state && count && frappe.get_route()[0] === root && screen() !== shown_screen) void render_data();
		}, 200));
		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; ++refresh_sequence; table?.destroy(); table = null; });
		await show_route();
	};
})();
