/* 商品：**原生列表**（框架装的 ListView 渲染器），只用我们自己的取数接口。
 *
 * 为什么走 `frappe.pearnly.list`：它继承的是框架装的 BaseList + ListView 渲染，
 * 于是复选框列、第 N 项/共 M 项、20/100/500/2500 分页、空状态、行高与移动端样式
 * 全部是原生那套；而"列表视图切换 / 已保存的筛选器 / 过滤器 / 排序"属于 ListView 的
 * 另外几条装配路径（`setup_view_menu` / `setup_standard_filters` / `setup_filter_area`），
 * 产品列表按用户 2026-09-21 的口径**不放这些**，走 BaseList 正好天然没有。
 *
 * 复选框列留着的理由：它后面挂着**真有的功能** —— 批量启用/停用选中的商品。
 * 没有去处的话就不该放出来。
 *
 * 新建两条路（用户 2026-09-22 口径："弹窗只是快捷添加方式……左下角是全屏编辑，
 * 点进去才是二级面包屑"）：
 *   /desk/pearnly-products       列表；点行 = 原生弹窗查看/编辑（查看是弹窗）
 *   「新建商品」= 弹窗，一次一个，那一格「条码/二维码」自带扫码图标
 *   弹窗左下角「全屏编辑」= 官方 `QuickEntryForm` 用的同一颗 custom action
 * （`add_custom_action(…, …)`，官方英文串是 "Edit Full Form"）
 *   /desk/pearnly-products/new   二级页面：原生 Grid，一次填多行、一次建完
 *
 * 文案全部来自 `pearnly_erp_portal/i18n/messages.tsv` 的 `product.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.products = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.products.service.";
		const root = "pearnly-products";
		// 二级页面的路由段。`frappe.get_route()` 是第一段之后的分段，所以列表是空数组。
		const SECOND = "new";
		const can_create = frappe.model.can_create("ERP Product");
		const url = (...parts) => p.subroute(root, ...parts);
		// 弹窗点「全屏编辑」时把已填的值带进二级页面：两边在同一个闭包里，不必写进 URL。
		let carry = null;
		let list, form;

		// "设过没有"要跟值一起看：Currency 列存不下 NULL，库里 0 与"从未设过"是同一个数字。
		// 没有成本权限的岗位，服务端把值与标记一起清空，于是这里自然显示"未设"。
		const marked = (row, flag, value) =>
			row[flag] ? p.money(value) : t("product.cell.notSet");

		// 扫码图标：取景框 + 码线。一个图标同时指条码与二维码，跟合并后的那一格同名同义。
		const SCAN_ICON = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
			<path d="M2 5.6V3.7c0-.9.8-1.7 1.7-1.7h1.9M10.4 2h1.9c.9 0 1.7.8 1.7 1.7v1.9M14 10.4v1.9c0 .9-.8 1.7-1.7 1.7h-1.9M5.6 14H3.7c-.9 0-1.7-.8-1.7-1.7v-1.9"
				stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
			<path d="M5.6 6.5v3M7.3 6.5v3M9 6.5v3M10.7 6.5v3" stroke="currentColor"
				stroke-width="1.4" stroke-linecap="round"/></svg>`;

		// 建与改共用一个表单：落库的字段与旧线上 products 的列一一对应（不多不少）。
		// 条码与二维码内容在界面上是**一格**（用户 2026-09-22 口径）：库里两列都留着 ——
		// Excel 导入导出仍按两列、扫码找品也仍旧三列一起查 —— 落哪一列由"怎么来的"决定。
		// 这一格里的值是不是条码形状：条码是短 ASCII（Code39/128 的字符集），其余（含中文、
		// 空格串、超过条码上限的长串）归二维码内容那一列 —— 服务端两列的长度上限不同，
		// 塞错了会被判成 "Invalid value"。
		const BARCODE_SHAPE = /^[A-Za-z0-9][A-Za-z0-9\-$\/+.% ]{0,99}$/;
		// 码型 → 服务端列名：界面上是一格，库里仍是旧线上那两列（导入导出的两列不动）。
		const CODE_FIELD = { barcode: "barcode", qr: "qr_payload" };
		function code_kind(value, scanned) {
			if (scanned === "qr" || scanned === "barcode") return scanned;
			const text = String(value ?? "").trim();
			if (!text) return "barcode";
			return BARCODE_SHAPE.test(text) ? "barcode" : "qr";
		}

		// 新建（弹窗与二级页面同一条）：界面上只有一格，所以两列一起写，另一列显式清空。
		function code_payload(value, scanned) {
			const text = String(value ?? "").trim();
			if (!text) return {};
			const kind = code_kind(text, scanned);
			return { [CODE_FIELD[kind]]: text,
				[kind === "barcode" ? "qr_payload" : "barcode"]: "" };
		}

		// 编辑已有商品：没动过这一格就写回原来那一列（另一列的历史值不因改价格被清掉），
		// 动过就按码型落一列、并把另一列清空 —— 界面上只有一格，留个看不见的旧值就是鬼数据。
		function code_changes(state, values) {
			const text = String(values.code_value ?? "").trim();
			if (!text || text === state.original) return { [CODE_FIELD[state.kind]]: text };
			return code_payload(text, state.scanned);
		}

		// `preview_code`：新建时把**系统将要发的编码**显示在只读那一格（用户 2026-09-22 口径：
		// 编码不给手输，但要看得见，跟入库/出库表单显示当前单据号同一个道理）。
		function product_fields(existing = {}, preview_code = "") {
			const value = (field, fallback = null) =>
				existing[field] === undefined ? fallback : existing[field];
			const original = String(existing.barcode || existing.qr_payload || "").trim();
			// 只有二维码那一列有值的商品，改的时候仍旧落二维码；其余默认条码。
			const state = { original, scanned: null,
				kind: existing.qr_payload && !existing.barcode ? "qr" : "barcode" };
			// **一个 Section 只分一次栏**：原生 `.form-section` 每条都带 12px 上内边距 +
			// 16px 上外边距，一行一个 Section 会把十行表单撑出三百多像素的空白（实测）。
			// 左列"这是什么商品"，右列"怎么卖、放哪"。
			return { state, fields: [
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "product_name", label: t("product.field.name"),
					reqd: 1, default: value("product_name") },
				{ fieldtype: "Data", fieldname: "code", label: t("product.field.code"),
					read_only: 1, description: t("product.field.codeAuto"),
					default: value("code") || preview_code },
				{ fieldtype: "Data", fieldname: "code_value", label: t("product.field.codeValue"),
					// 上限跟服务端二维码那一列（500）对齐：合并后这一格是录入二维码的唯一入口，
					// 用 Data 的默认 140 会让长二维码内容粘贴不进来。
					length: 500, default: original },
				{ fieldtype: "Data", fieldname: "unit", label: t("product.field.unit"),
					default: value("unit") },
				{ fieldtype: "Data", fieldname: "base_unit", label: t("product.field.baseUnit"),
					description: t("product.field.baseUnitHint"), default: value("base_unit") },
				{ fieldtype: "Int", fieldname: "category_id", label: t("product.field.category"),
					default: value("category_id") },
				{ fieldtype: "Column Break" },
				// 默认仓库：官方原生 Link（只列当前公司的仓库），与出入库/销售采购明细同一口径。
				{ fieldtype: "Link", options: "ERP Warehouse", only_select: true,
					get_query: p.warehouse_query(), fieldname: "default_warehouse",
					label: t("product.field.defaultWarehouse"),
					default: value("default_warehouse") },
				{ fieldtype: "Data", fieldname: "default_location", label: t("product.field.defaultLocation"),
					description: t("product.field.locationHint"), default: value("default_location") },
				// 空售价 = "从未设过"，与 ฿0 是两回事（服务端用 *_set 记这个区别）。
				{ fieldtype: "Currency", fieldname: "unit_price", label: t("product.field.price"),
					options: "THB", default: existing.price_set ? existing.unit_price : null },
				{ fieldtype: "Currency", fieldname: "default_cost", label: t("product.field.cost"),
					options: "THB", default: existing.cost_set ? existing.default_cost : null },
				{ fieldtype: "Float", fieldname: "min_stock", label: t("product.field.minStock"),
					default: value("min_stock") },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Check", fieldname: "track_batch", label: t("product.field.trackBatch"),
					default: value("track_batch", 0) },
				{ fieldtype: "Check", fieldname: "track_expiry",
					label: t("product.field.trackExpiry"), default: value("track_expiry", 0) },
				{ fieldtype: "Check", fieldname: "vat_applicable", label: t("product.field.vat"),
					default: value("vat_applicable", 1) },
				{ fieldtype: "Column Break" },
				// 图片走原生附件控件（自带上传按钮、只收图片、带缩略图预览），落库的还是那个 URL。
				{ fieldtype: "Attach Image", fieldname: "image_url", label: t("product.field.image"),
					default: value("image_url") },
			] };
		}

		// 扫到的码已经是别人的：**当场**说，并给一条"去改那一条"的出路。
		// 旧线上也是当场拦（撞码拖到保存才报，会让人白填一整张表）。
		async function flag_taken(code, self_name, on_open) {
			const taken = await p.call(api + "api_find_product",
				{ value: code, exclude: self_name || "" });
			if (!taken || !taken.name) return false;
			frappe.msgprint({
				title: t("product.scan.taken"),
				indicator: "orange",
				message: t("product.scan.takenBody", taken.product_name, taken.code || "—"),
				primary_action: {
					label: t("product.scan.open"),
					action: () => {
						on_open?.();
						void open_detail(taken);
					},
				},
			});
			return true;
		}

		// 扫码入口就在那一格里（原生输入框 + 右侧图标按钮），不再占对话框底部一颗按钮 ——
		// 那颗和"保存"并排，既抢眼又容易被当成主操作。
		function attach_scan_icon(dialog, state, self_name) {
			const field = dialog.get_field("code_value");
			const label = p.escape(t("product.action.scan"));
			const button = $(`<button type="button" class="pearnly-scan-icon"
				title="${label}" aria-label="${label}">${SCAN_ICON}</button>`);
			field.$wrapper.find(".control-input").addClass("pearnly-code-input").append(button);
			button.on("click", () => {
				if (!p.scan_supported()) return frappe.msgprint(t("product.scan.unsupported"));
				return p.scan({ kind: "all", on_result: async (code, details) => {
					state.scanned = details?.kind || null;
					await dialog.set_value("code_value", code);
					// 结果那一声（官方 POS 的口径：进了才算数）：写进表单是成功，码是别人的
					// 是要你处理，两种给不同的音。
					const taken = await flag_taken(code, self_name, () => dialog.hide());
					p.scan_result_sound(!taken);
				} });
			});
			// 扫码枪就是个键盘（它打进来的是文字，没有码型）：打完补的那个回车不能顺手把表单存了。
			field.$input.off("keypress").on("keydown keypress", event => {
				if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); }
			});
		}

		// 空串要变 null：那是"从未设过"，让它落成 0 就把"没设价"和"真的 ฿0"混成一个值。
		function normalize(values) {
			const out = { ...values };
			for (const field of ["unit_price", "default_cost"]) {
				if (out[field] === "") out[field] = null;
			}
			// `code_value` 只是界面上那一格的名字，服务端没有这个参数；
			// 落库的两列由 `code_changes` 拆好，这里把它摘掉。
			delete out.code_value;
			return out;
		}

		// ── 列表 ────────────────────────────────────────────────────────────────

		async function open_list() {
			// 右上角那一颗主按钮：没选中任何行时是"新建商品"（打开快捷弹窗），选中若干行时
			// 切换成批量启用/停用 —— 商品真有的动作。选中即出现操作这套行为由共享层的
			// `p.selection_action` 提供，其余列表页用的是同一套（确认、忙碌禁用、失败保留选择）。
			// 只在**列表那一级**挂这颗主按钮：列表的异步刷新可能在我们已经切进二级页面（新建）
			// 之后才落地，那一笔 paint 会把"新建商品"盖到二级页面右上角（同盘点工作台那个坑）。
			function restore_primary() {
				if (frappe.get_route().length !== 1) return;
				if (can_create) page.set_primary_action(t("product.action.new"), () => open_quick());
				else page.clear_primary_action();
			}
			const selection = p.selection_action({
				page, list: () => list, empty: restore_primary,
				label: rows => (rows.every(row => !row.is_active)
					? t("product.action.bulkEnable", rows.length)
					: t("product.action.bulkDisable", rows.length)),
				run: async rows => {
					const enabling = rows.every(row => !row.is_active);
					for (const row of rows) {
						await p.call(api + "api_set_active",
							{ name: row.name, active: enabling ? 1 : 0 }, "POST");
					}
				},
				success: rows => t("product.notice.toggledCount", rows.length),
			});

			const filters = {};
			filters.search = page.add_field({
				fieldtype: "Data",
				fieldname: "search",
				label: t("product.field.search"),
				change: () => void list?.reload(),
			});
			// 上面那个自研的"显示已停用"勾选撤掉了，改成**官方原生筛选**里的「上架」(is_active)
			// 条件（用户 2026-09-23："把我们自己设计的勾选筛选取消掉，做成官方原生的显示出来"）。
			const query = () => {
				const picked = p.filter_values(list);
				return {
					search: filters.search.get_value() || undefined,
					is_active: picked.is_active === undefined || picked.is_active === ""
						? undefined : picked.is_active,
				};
			};

			list = p.list({
				page,
				doctype: "ERP Product",
				selectable: true,
				// 本页支持的筛选条件（官方的字段下拉里只有这一个：上架 / 停用）。
				// 名称、物料号、条码、二维码仍然走搜索框（一次搜四样，比逐个字段筛更快）。
				filter_fields: [
					{ fieldname: "is_active", label: t("product.col.status"), fieldtype: "Check" },
				],
				on_selection: selection,
				on_open: (row) => open_detail(row),
				// 七列一屏放得下、不用横向滑（用户 2026-09-23："内容太多，要滑动才能看得到"）。
				// 宽度按下面的口径走，窗口更宽时等比放宽；`min_width` 是不能再压的底线。
				compact_columns: true,
				// 空态用原生那一页（图标 + 按钮），按钮就是这颗"新建商品"。
				empty_action: can_create ? { label: t("product.action.new"), action: open_quick } : null,
				load: async ({ start, page_length }) => {
					// 列表框架给的是 {start, page_length}；服务端的参数名是 start / limit。
					const [items, total] = await Promise.all([
						p.call(api + "api_list_products",
							{ start, limit: page_length, ...query() }),
						p.call(api + "api_count_products", query()),
					]);
					return { items, total };
				},
				columns: [
					{ fieldname: "product_name", width: 184, min_width: 160, label: t("product.col.name") },
					{ fieldname: "code", width: 88, min_width: 78, label: t("product.col.code") },
					// 条码与二维码在界面上是同一格：哪个有值就显示哪个，列名跟表单一致。
					{ fieldname: "barcode", width: 136, min_width: 104, label: t("product.col.codeValue"),
						format: (_value, row) => row.barcode || row.qr_payload || "" },
					{ fieldname: "unit", width: 60, min_width: 52, label: t("product.col.unit") },
					{ fieldname: "unit_price", width: 92, min_width: 82, label: t("product.col.price"),
						format: (_value, row) => marked(row, "price_set", row.unit_price) },
					{ fieldname: "default_cost", width: 92, min_width: 82, label: t("product.col.cost"),
						format: (_value, row) => marked(row, "cost_set", row.default_cost) },
					// 「位置」＝默认仓库 + 默认仓位合成一列（用户 2026-09-23："图二加位置，内容显示
					// 仓库和仓位，一屏显示完……长字段自动隐藏就好了"）。仓库显示**名字**（与库存/
					// 盘点/出入库同一个口径）；太长由列表自己截断 + tooltip（`list.js` 给每格挂了
					// `title`），所以它不会把整张表撑开。
					{ fieldname: "place", width: 188, min_width: 132, label: t("product.col.place"),
						format: (_value, row) => [row.default_warehouse_name || row.default_warehouse || "",
							row.default_location || ""].filter(Boolean).join(" / ") },
					{ fieldname: "is_active", width: 84, min_width: 72, label: t("product.col.status"),
						// 与供应商/客户两页同一口径：正常 / 已停用。原来用的是框架自己的
						// "Active"，中文界面里会译成「激活」，三张列表同一个概念三种说法。
						format: (value) => t(Number(value) ? "product.active" : "product.inactive") },
				],
			});

			// 工具栏：刷新在导入左边，导入在新建左边（新建是页面主按钮，永远在最右）。
			page.add_inner_button(t("product.action.reload"), () => void list.reload());
			if (can_create) page.add_inner_button(t("product.action.import"), () => open_import());
			restore_primary();
			await list.ready;
			// 别处的选择框点「新建」指过来时，开这一页自己的快捷弹窗（与右上角那颗同一个，
			// 用户 2026-09-23："点新建跳转到我们已有的对应位置"）。
			if (can_create) await p.take_create(root, "ERP Product", () => open_quick());
			// 箭头（打开这条记录）也回到这一页：在**已加载的行**里找到它就开详情弹窗
			// （商品列表是分页的，没在当页就只落到列表 —— 不为了一个箭头去拉全表）。
			await p.take_open(root, "ERP Product", name => {
				const row = (list.data || []).find(item => item.name === name);
				if (row) open_detail(row);
			});
		}

		// ── 新建弹窗（快捷）────────────────────────────────────────────────────

		// 快捷新建：编码由服务端发，这里先取一个**预览**填进只读那一格（不占号）。
		async function open_quick() {
			const preview_code = (await p.call(api + "api_next_product_code")).code;
			const { fields, state } = product_fields({}, preview_code);
			const dialog = p.dialog(
				t("product.action.new"),
				fields,
				t("product.action.submit"),
				async (values, d) => {
					await p.call(api + "api_create_product",
						normalize({ ...values, ...code_changes(state, values) }), "POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list?.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			attach_scan_icon(dialog, state, null);
			// 左下角那颗"全屏编辑"（官方插槽）：要批量建、或者字段在弹窗里挤，就进二级页面。
			p.edit_all(dialog, t("product.action.fullPage"), url(SECOND), values => {
				carry = { ...values, __scanned: state.scanned };
			});
		}

		// ── 二级页面：一次建多个商品 ─────────────────────────────────────────────

		// 与弹窗**同一套字段**（用户 2026-09-22 口径"字段不变"）：列表里露出来的那几列先
		// 横着排，其余字段在原生 Grid 的行展开里 —— 点行左边那个箭头就是完整字段。
		// Grid 自带的「添加行」和勾选列原样保留（那是框架的，勾选是"选中行→批量删除"）。
		// `code` 不放进来：编码由服务端发（弹窗里那格是只读预览；这一页把"本次从哪个号
		// 开始发"写在下面那行提示里）。
		//
		// **露出来的列加起来不能超过 10**：框架的 Grid 一旦 `total_colsize > 10` 就切成
		// "列超限"模式（横向滚动），右侧的列会被切在可视区外 —— 用户看到的就是"后面的字段
		// 都看不到了"。所以只留最常用的四列，其余（单位/库存单位/默认仓库/默认仓位/参考成本/
		// 分类/低库存/批次/有效期/VAT/图片）都在行展开里。
		const grid_fields = () => [
			{ fieldtype: "Data", fieldname: "product_name", label: t("product.field.name"),
				reqd: 1, in_list_view: 1, columns: 3 },
			{ fieldtype: "Data", fieldname: "code_value", label: t("product.field.codeValue"),
				length: 500, in_list_view: 1, columns: 3, description: t("product.field.codeAuto") },
			{ fieldtype: "Currency", fieldname: "unit_price", options: "THB",
				label: t("product.field.price"), in_list_view: 1, columns: 2 },
			{ fieldtype: "Data", fieldname: "unit", label: t("product.field.unit"),
				in_list_view: 1, columns: 1 },
			// 快录表里也是同一个官方 Link（只列当前公司的仓库）。
			{ fieldtype: "Link", options: "ERP Warehouse", only_select: true,
				get_query: p.warehouse_query(), fieldname: "default_warehouse",
				label: t("product.field.defaultWarehouse"), in_list_view: 1, columns: 1 },
			{ fieldtype: "Data", fieldname: "default_location",
				label: t("product.field.defaultLocation"), columns: 2 },
			{ fieldtype: "Currency", fieldname: "default_cost", options: "THB",
				label: t("product.field.cost"), columns: 2 },
			{ fieldtype: "Data", fieldname: "base_unit", label: t("product.field.baseUnit"),
				columns: 2 },
			{ fieldtype: "Int", fieldname: "category_id", label: t("product.field.category"),
				columns: 2 },
			{ fieldtype: "Float", fieldname: "min_stock", label: t("product.field.minStock"),
				columns: 2 },
			{ fieldtype: "Check", fieldname: "vat_applicable", label: t("product.field.vat"),
				default: 1, columns: 1 },
			{ fieldtype: "Check", fieldname: "track_batch", label: t("product.field.trackBatch"),
				columns: 1 },
			{ fieldtype: "Check", fieldname: "track_expiry", label: t("product.field.trackExpiry"),
				columns: 1 },
			{ fieldtype: "Attach Image", fieldname: "image_url", label: t("product.field.image"),
				columns: 2 },
		];

		async function open_new() {
			p.breadcrumbs({ label: __("nav.products"), route: url(),
				trail: [{ label: t("product.action.new"), route: "" }] });
			const rows = carry ? [carry] : [{}];
			carry = null;
			// 编码由服务端在入库那一刻发；这里只是把"本次从哪个号开始"写进提示给人看。
			const start_code = (await p.call(api + "api_next_product_code")).code;
			form = p.layout(page.main, [
				{ fieldtype: "Section Break" },
				{ fieldtype: "Table", fieldname: "products", label: t("product.new.lines"),
					in_place_edit: true, data: rows, fields: grid_fields() },
				{ fieldtype: "HTML", fieldname: "hint",
					options: `<p class="text-muted">${p.escape(t("product.new.hint"))}${
						start_code ? " " + p.escape(t("product.new.hintCode", start_code)) : ""}</p>` },
			]);
			const grid = form.fields_dict.products.grid;

			// 扫码入口：Grid 的单元格是框架按行即时建的（一行一控，`make_control` 在框架
			// 内部），塞不进"每格一颗图标"而不改框架代码；这里用 Grid 自己的
			// `.grid-custom-buttons` 官方插槽（DocType 的自定义按钮就挂这儿），
			// 扫码 = 新加一行并把码填进那一行的「条码/二维码」。
			const scan_label = p.escape(t("product.action.scan"));
			const scan_button = $(`<button type="button" class="btn btn-default btn-xs pearnly-scan-icon"
				title="${scan_label}" aria-label="${scan_label}">${SCAN_ICON}</button>`);
			grid.wrapper.find(".grid-custom-buttons").append(scan_button);
			scan_button.on("click", () => {
				if (!p.scan_supported()) return frappe.msgprint(t("product.scan.unsupported"));
				return p.scan({ kind: "all", on_result: async (code, details) => {
					grid.add_new_row();
					const row = grid.df.data[grid.df.data.length - 1];
					row.code_value = code;
					row.__scanned = details?.kind || null;
					grid.refresh();
					const taken = await flag_taken(code, "", null);
					p.scan_result_sound(!taken);
				} });
			});

			// Grid 的数字格读不出"空"（原生 Float/Currency 控件空值就是 0），所以这里按
			// "0 = 没设"处理：与弹窗里留空（null）落到同一个结果，免得快录出来的商品被记成
			// "设过价为 ฿0"（收银台的零元闸读的就是这个标记）。要一个真的 ฿0 就用弹窗填。
			const row_payload = row => ({
				product_name: row.product_name,
				unit: row.unit,
				base_unit: row.base_unit,
				category_id: row.category_id,
				default_warehouse: row.default_warehouse,
				default_location: row.default_location,
				unit_price: Number(row.unit_price) > 0 ? row.unit_price : null,
				default_cost: Number(row.default_cost) > 0 ? row.default_cost : null,
				min_stock: row.min_stock,
				track_batch: row.track_batch,
				track_expiry: row.track_expiry,
				vat_applicable: row.vat_applicable,
				image_url: row.image_url,
				...code_payload(row.code_value, row.__scanned),
			});

			async function submit_all() {
				const rows = (form.get_value("products") || []).filter(row =>
					String(row.product_name || "").trim() || String(row.code_value || "").trim());
				if (!rows.length) {
					return frappe.msgprint({ title: t("product.action.new"), indicator: "orange",
						message: t("product.notice.nothingToSave") });
				}
				let done = 0;
				const failed = [];
				for (const row of rows) {
					try {
						await p.call(api + "api_create_product", row_payload(row), "POST");
						done += 1;
					} catch (error) {
						// 服务端已经用 msgprint 说了原因；这里只把这一行留在表里让人改。
						if (!error?._server_messages && !error?.responseJSON) console.error(error);
						failed.push(row);
					}
				}
				// 建成功的从表里去掉，免得再点一次提交重复建。
				grid.df.data = failed;
				grid.refresh();
				if (!failed.length) {
					p.success(t("product.notice.savedCount", done));
					await list?.reload();
					p.redirect_up(root);
					return;
				}
				frappe.msgprint({ title: t("product.action.new"), indicator: "orange",
					message: t("product.notice.partial", done, failed.length) });
			}

			page.set_primary_action(t("product.action.submit"), () => void submit_all());
		}

		// Excel 导入：走原生上传组件（它负责落 File 记录、限制类型与大小），
		// 传完拿 file_url 交给服务端解析入库。只收本机文件 —— 导入读的是客户手里那张表，
		// 库里已有的文件、摄像头拍照都不是这条路的输入。
		function open_import() {
			let file_url = "";
			const dialog = p.dialog(
				t("product.action.import"),
				[
					{ fieldtype: "HTML",
						options: `<p class="text-muted">${p.escape(t("product.import.hint"))}</p>` },
					{ fieldtype: "Button", fieldname: "pick", label: t("product.import.pick"),
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
					{ fieldtype: "Data", fieldname: "file_name",
						label: t("product.field.importFile"), read_only: 1 },
					{ fieldtype: "Button", fieldname: "template",
						label: t("product.import.template"), click: () => {
							// 模板是服务端现生成的（表头跟着站点语言走），走下载口拿，
							// 不在租户里留 File 记录。
							window.location.assign(
								"/api/method/pearnly_erp_portal.products.service.api_import_template");
						} },
				],
				t("product.action.import"),
				async (_values, d) => {
					const result = await p.call(api + "api_import_products", { file_url }, "POST");
					d.hide();
					p.success(t("product.notice.imported", result.created));
					if (result.failed) {
						frappe.msgprint({
							title: t("product.action.import"),
							indicator: "orange",
							message: t("product.notice.importSkipped", result.failed) + "<br>"
								+ result.errors.map((row) =>
									`${p.escape(row.row)}: ${p.escape(row.error)}`).join("<br>"),
						});
					}
					await list?.reload();
				},
			);
		}

		// 查看/编辑：**原生弹窗**（用户 2026-09-22 口径：查看是弹窗）。
		async function open_detail(row) {
			const existing = await p.call(api + "api_get_product", { name: row.name });
			const can_write = frappe.model.can_write("ERP Product");
			const { fields, state } = product_fields(existing);
			const dialog = p.dialog(
				existing.product_name,
				fields,
				t("product.action.submit"),
				async (values, d) => {
					await p.call(api + "api_update_product",
						{ name: existing.name, ...normalize({ ...values, ...code_changes(state, values) }) },
						"POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list?.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			if (!can_write) dialog.get_primary_btn().prop("disabled", true);
			if (can_write) attach_scan_icon(dialog, state, existing.name);
		}

		// ── 路由 ────────────────────────────────────────────────────────────────

		async function render(rest) {
			p.clear_page(page);
			list = null; form = null;
			if (!rest.length) { await open_list(); return; }
			if (rest[0] !== SECOND || !can_create) { p.redirect_up(root); return; }
			await open_new();
		}

		const show = p.routed_page({ wrapper, root, render });
		await show();
	};
})();
