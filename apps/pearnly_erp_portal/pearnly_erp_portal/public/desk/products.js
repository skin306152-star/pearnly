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
 * 文案全部来自 `scripts/i18n/messages.tsv` 的 `product.*` 键。
 */
(() => {
	const p = frappe.pearnly;

	p.products = async (page, t) => {
		const api = "pearnly_erp_portal.products.service.";
		let list;
		let busy = false;

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

		// 右上角那一颗主按钮：没选中任何行时是"新建商品"（与参考图一致），
		// 选中若干行时切换成批量启用/停用 —— 这是产品真有的动作，复选框列因此有实际去处。
		function update_action(rows) {
			if (busy) {
				page.btn_primary?.prop("disabled", true);
				return;
			}
			if (!rows.length) {
				if (can_create) page.set_primary_action(t("product.action.new"), () => open_create());
				else page.clear_primary_action();
				return;
			}
			const enabling = rows.every((row) => !row.is_active);
			page.set_primary_action(
				enabling
					? t("product.action.bulkEnable", rows.length)
					: t("product.action.bulkDisable", rows.length),
				() => void toggle_selected(rows, enabling),
			);
		}

		async function toggle_selected(rows, enabling) {
			if (busy) return;
			busy = true;
			update_action(rows);
			try {
				for (const row of rows) {
					await p.call(api + "api_set_active",
						{ name: row.name, active: enabling ? 1 : 0 }, "POST");
				}
				p.success(t("product.notice.toggledCount", rows.length));
				list.clear_checked_items();
				await list.reload();
			} catch (error) {
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
			} finally {
				busy = false;
				update_action(list.get_checked_items());
			}
		}

		list = p.list({
			page,
			doctype: "ERP Product",
			selectable: true,
			on_selection: update_action,
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
				{ fieldname: "barcode", label: t("product.col.barcode") },
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

		// 建与改共用一个表单：字段与旧线上 products 的列一一对应（不多不少）。
		function product_fields(existing = {}) {
			const value = (field, fallback = null) =>
				existing[field] === undefined ? fallback : existing[field];
			return [
				{ fieldtype: "Data", fieldname: "product_name", label: t("product.field.name"),
					reqd: 1, default: value("product_name") },
				// 编码由系统发（旧线上 ERP 门户里这一格也是只读的），留空即自动编号。
				{ fieldtype: "Data", fieldname: "code", label: t("product.field.code"),
					read_only: 1, description: t("product.field.codeAuto"),
					default: value("code") },
				{ fieldtype: "Data", fieldname: "barcode", label: t("product.field.barcode"),
					default: value("barcode") },
				{ fieldtype: "Data", fieldname: "qr_payload", label: t("product.field.qr"),
					default: value("qr_payload") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "unit", label: t("product.field.unit"),
					default: value("unit") },
				{ fieldtype: "Data", fieldname: "base_unit", label: t("product.field.baseUnit"),
					description: t("product.field.baseUnitHint"),
					default: value("base_unit") },
				{ fieldtype: "Data", fieldname: "default_warehouse", label: t("product.field.defaultWarehouse"),
					default: value("default_warehouse") },
				{ fieldtype: "Data", fieldname: "default_location", label: t("product.field.defaultLocation"),
					description: t("product.field.locationHint"), default: value("default_location") },
				{ fieldtype: "Int", fieldname: "category_id", label: t("product.field.category"),
					default: value("category_id") },
				// 空售价 = "从未设过"，与 ฿0 是两回事（服务端用 *_set 记这个区别）。
				{ fieldtype: "Currency", fieldname: "unit_price", label: t("product.field.price"),
					options: "THB", default: value("price_set", existing.price_set)
						? existing.unit_price : null },
				{ fieldtype: "Currency", fieldname: "default_cost", label: t("product.field.cost"),
					options: "THB", default: existing.cost_set ? existing.default_cost : null },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Float", fieldname: "min_stock", label: t("product.field.minStock"),
					default: value("min_stock") },
				{ fieldtype: "Check", fieldname: "track_batch", label: t("product.field.trackBatch"),
					default: value("track_batch", 0) },
				{ fieldtype: "Check", fieldname: "track_expiry",
					label: t("product.field.trackExpiry"), default: value("track_expiry", 0) },
				{ fieldtype: "Check", fieldname: "vat_applicable", label: t("product.field.vat"),
					default: value("vat_applicable", 1) },
				// 图片走原生附件控件（它自带上传按钮、只收图片、带缩略图预览），
				// 落库的还是那个 URL —— 与旧线上同一个字段。文件保持私有（框架默认，
				// 也符合租户隔离）：读权限靠服务端把它挂到这条商品上，见
				// `service._attach_image`（私有且无归属的文件只有上传者读得到）。
				{ fieldtype: "Attach Image", fieldname: "image_url", label: t("product.field.image"),
					default: value("image_url") },
			];
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

		// 条码/二维码两格旁边加一个扫码按钮：原生输入框 + 原生按钮，解码在 desk/scan.js。
		// 扫码枪不用按钮（它就是个键盘，打进输入框），但打完补的那个回车不能顺手把表单存了。
		async function add_scan_buttons(dialog, self_name) {
			await p.scan_ready();
			for (const [fieldname, kind] of [["barcode", "barcode"], ["qr_payload", "qr"]]) {
				const field = dialog.get_field(fieldname);
				if (!field || !p.scan_supported(kind)) continue;
				const button = $(`<button type="button" class="btn btn-default btn-sm"
					title="${p.escape(t("product.action.scan"))}">
					${frappe.utils.icon("es-line-camera", "sm")}</button>`);
				const row = $('<div class="flex align-center" style="gap:6px"></div>');
				field.$input.before(row);
				row.append(field.$input).append(button);
				button.on("click", () => p.scan({
					kind,
					on_result: async (code) => {
						field.set_value(code);
						await flag_taken(dialog, code, self_name);
					},
				}));
			}
			const barcode = dialog.get_field("barcode");
			barcode.$input.on("keydown", (event) => {
				if (event.key === "Enter") {
					event.preventDefault();
					event.stopPropagation();
				}
			});
		}

		// 空串要变 null：那是"从未设过"，让它落成 0 就把"没设价"和"真的 ฿0"混成一个值。
		function normalize(values) {
			const out = { ...values };
			for (const field of ["unit_price", "default_cost"]) {
				if (out[field] === "") out[field] = null;
			}
			return out;
		}

		async function open_create() {
			const dialog = p.dialog(
				t("product.action.new"),
				product_fields(),
				t("product.action.save"),
				async (values, d) => {
					await p.call(api + "api_create_product", normalize(values), "POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			await add_scan_buttons(dialog, null);
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
			const dialog = p.dialog(
				existing.product_name,
				product_fields(existing),
				t("product.action.save"),
				async (values, d) => {
					await p.call(api + "api_update_product",
						{ name: existing.name, ...normalize(values) }, "POST");
					d.hide();
					p.success(t("product.notice.saved", values.product_name));
					await list.reload();
				},
			);
			dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
			if (!can_write) dialog.get_primary_btn().prop("disabled", true);
			await add_scan_buttons(dialog, existing.name);
		}

		// 工具栏：刷新在导入左边，导入在新建左边（新建是页面主按钮，永远在最右）。
		page.add_inner_button(t("product.action.reload"), () => void list.reload());
		if (frappe.model.can_create("ERP Product")) {
			page.add_inner_button(t("product.action.import"), () => open_import());
		}
		await list.ready;
		update_action([]);
	};
})();
