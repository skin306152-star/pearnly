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
 * 文案全部来自 `pearnly_erp_portal/i18n/messages.tsv` 的 `product.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.products = async (page, t) => {
		const api = "pearnly_erp_portal.products.service.";
		let list;

		// "设过没有"要跟值一起看：Currency 列存不下 NULL，库里 0 与"从未设过"是同一个数字。
		// 没有成本权限的岗位，服务端把值与标记一起清空，于是这里自然显示"未设"。
		const marked = (row, flag, value) =>
			row[flag] ? p.money(value) : t("product.cell.notSet");

		const filters = {};
		filters.search = page.add_field({
			fieldtype: "Data",
			fieldname: "search",
			label: t("product.field.search"),
			change: () => void list?.reload(),
		});
		filters.include_inactive = page.add_field({
			fieldtype: "Check",
			fieldname: "include_inactive",
			label: t("product.action.toggleInactive"),
			change: () => void list?.reload(),
		});
		const query = () => ({
			search: filters.search.get_value() || undefined,
			include_inactive: filters.include_inactive.get_value() ? 1 : undefined,
		});

		// 右上角那一颗主按钮：没选中任何行时是"新建商品"（与参考图一致），选中若干行时
		// 切换成批量启用/停用 —— 商品真有的动作。选中即出现操作这套行为由共享层的
		// `p.selection_action` 提供，其余列表页用的是同一套（确认、忙碌禁用、失败保留选择）。
		function restore_primary() {
			if (can_create) page.set_primary_action(t("product.action.new"), () => open_create());
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

		list = p.list({
			page,
			doctype: "ERP Product",
			selectable: true,
			on_selection: selection,
			on_open: (row) => open_form(row),
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
				{ fieldname: "product_name", label: t("product.col.name") },
				{ fieldname: "code", label: t("product.col.code") },
				// 条码与二维码在界面上是同一格：哪个有值就显示哪个，列名跟表单一致。
				{ fieldname: "barcode", label: t("product.col.codeValue"),
					format: (_value, row) => row.barcode || row.qr_payload || "" },
				{ fieldname: "unit", label: t("product.col.unit") },
				{ fieldname: "unit_price", label: t("product.col.price"),
					format: (_value, row) => marked(row, "price_set", row.unit_price) },
				{ fieldname: "default_cost", label: t("product.col.cost"),
					format: (_value, row) => marked(row, "cost_set", row.default_cost) },
				{ fieldname: "is_active", label: t("product.col.status"),
					format: (value) => (value ? __("Active") : __("Disabled")) },
			],
		});

		const can_create = frappe.model.can_create("ERP Product");

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

		// 回写哪一列：没动过这一格就写回原来那一列（另一列的历史值不因改价格被清掉），
		// 动过就按码型落一列、并把另一列清空 —— 界面上只有一格，留个看不见的旧值就是鬼数据。
		function code_changes(state, values) {
			const text = String(values.code_value ?? "").trim();
			if (!text || text === state.original) return { [CODE_FIELD[state.kind]]: text };
			const kind = code_kind(text, state.scanned);
			return { [CODE_FIELD[kind]]: text,
				[kind === "barcode" ? "qr_payload" : "barcode"]: "" };
		}

		function product_fields(existing = {}) {
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
					read_only: 1, description: t("product.field.codeAuto"), default: value("code") },
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
				{ fieldtype: "Data", fieldname: "default_warehouse", label: t("product.field.defaultWarehouse"),
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
		async function flag_taken(dialog, code, self_name) {
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
						dialog.hide();
						void open_form(taken);
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
					await flag_taken(dialog, code, self_name);
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

		async function open_create() {
			const { fields, state } = product_fields();
			const dialog = p.dialog(
				t("product.action.new"),
				fields,
				t("product.action.save"),
				async (values, d) => {
					await p.call(api + "api_create_product",
						normalize({ ...values, ...code_changes(state, values) }), "POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			attach_scan_icon(dialog, state, null);
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
					await list.reload();
				},
			);
		}

		async function open_form(row) {
			const existing = await p.call(api + "api_get_product", { name: row.name });
			const can_write = frappe.model.can_write("ERP Product");
			const { fields, state } = product_fields(existing);
			const dialog = p.dialog(
				existing.product_name,
				fields,
				t("product.action.save"),
				async (values, d) => {
					await p.call(api + "api_update_product",
						{ name: existing.name, ...normalize({ ...values, ...code_changes(state, values) }) },
						"POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			if (!can_write) dialog.get_primary_btn().prop("disabled", true);
			if (can_write) attach_scan_icon(dialog, state, existing.name);
		}

		// 工具栏：刷新在导入左边，导入在新建左边（新建是页面主按钮，永远在最右）。
		page.add_inner_button(t("product.action.reload"), () => void list.reload());
		if (frappe.model.can_create("ERP Product")) {
			page.add_inner_button(t("product.action.import"), () => open_import());
		}
		await list.ready;
		restore_primary();
	};
})();
