/* Scan -> observe actual fields -> save -> next. One native Dialog, one camera stream. */
(() => {
	const p = frappe.pearnly, api = "pearnly_erp_portal.inventory.api.";
	// 一条记录最多 5 张照片（服务端 `counts.PHOTO_LIMIT` 同值，它才是权威，这里只是先拦一下）。
	const PHOTO_MAX = 5;
	// `scan`：一打开就开扫码器（从 LINE 普通内置浏览器转到 LIFF 回来时，接着扫）
	p.stockCountEntry = async ({ count, t, row, entry, continuous = false, scan = false, on_saved, on_done }) => {
		let selected, lookup, request_id, scanner, busy = false, closed = false, camera = false;
		// 查不到的那个码（相机扫到的不在编码栏里，「新建物料」用它）；`scanned`：这一件是扫码来的，点「继续」后自动再开扫码器，
		// 打字选的回到框里（用户 11-02）
		let missing = "", scanned = false, last_code = "";
		let last_place = null;
		// `photos` 是本次要随记录提交的（桌面端上传后拿到 file_url，服务端再挂到这条记录上）；
		// `existing` 是这条记录**已经有**的照片，只做只读预览（旧项目也没有删照片的入口）。
		const photos = [];
		let existing = [];
		const w = key => t(`stock.workbench.${key}`);
		const fields = [
			{ fieldtype: "Section Break", fieldname: "scan_section" },
			// 一个框（用户 11-02）：打字出本次盘点 + 本盘点仓在库的物料（原生选择框，服务端按页查），框内右侧扫码图标开扫码器；
			// 扫码枪 / 手打编码回车按码认。扫码器平时收起，读到物料就关。
			{ fieldtype: "Link", fieldname: "code", label: w("code"), description: w("scanHint"), options: "ERP Product",
				only_select: true, ignore_link_validation: 1,
				get_query: () => ({ query: api + "api_stock_count_product_search", filters: { count } }),
				// 扫码器开着时再点一下图标就是关掉
				scan: () => void (camera ? close_camera() : start_camera()) },
			// 「关闭」在画面上面：手机上画面很高，放下面会落到底栏后面看不到
			{ fieldtype: "Button", fieldname: "close_camera", label: __("Close"), hidden: 1, click: () => close_camera() },
			{ fieldtype: "HTML", fieldname: "video", options: p.scan_stage(), hidden: 1 },
			{ fieldtype: "Check", fieldname: "torch", label: __("product.scan.light"), hidden: 1,
				change: () => scanner?.torch(Boolean(d.get_value("torch"))) },
			{ fieldtype: "HTML", fieldname: "message" },
			// 扫到系统没有的码：当场建商品（有盘点录入权即可，服务端 `count.create_product_here`），建好接着记数
			{ fieldtype: "Button", fieldname: "new_product", label: __("product.action.new"), hidden: 1,
				click: () => create_here(missing) },
			{ fieldtype: "Section Break", fieldname: "entry_section", hidden: 1 },
			// 认出的物料，先后照岳父 11-01 那张单：条码、名称、分类、特性/颜色，再接编码（条码、分类、特性有值才出；
			// 条码是这件物料存的条码 / 二维码，没存时是这次扫到的码——扫的是编码就不出，见服务端 `lookup_count`）
			{ fieldtype: "Data", fieldname: "product_barcode", label: t("stock.col.barcode"), read_only: 1, hidden: 1 },
			{ fieldtype: "Data", fieldname: "product_name", label: t("stock.col.name"), read_only: 1 },
			{ fieldtype: "Data", fieldname: "product_category", label: __("product.field.category"), read_only: 1, hidden: 1 },
			{ fieldtype: "Data", fieldname: "product_attribute", label: __("product.field.attribute"), read_only: 1, hidden: 1 },
			// 扫到的那件商品的编码（内部编码，没有就系统编码；服务端 `count_code`，与 LINE 手机页同一个）；标签跟着显示的是哪一种
			// （用户 11-01 批准：内部编码叫「内部编码」，系统编码叫「编码」）
			{ fieldtype: "Data", fieldname: "product_code", label: t("stock.col.code"), read_only: 1 },
			// 这一屏只记数、不改物料；有物料修改权的人在这里开物料页的编辑弹窗（主会话 11-01 批复 ④，服务端 `update_product` 仍判权）
			{ fieldtype: "Button", fieldname: "edit_product", label: __("product.action.edit"), hidden: 1,
				click: () => edit_product() },
			{ fieldtype: "Select", fieldname: "source_row", label: w("bookPosition"), options: [],
				change: () => choose_source() },
			{ fieldtype: "Data", fieldname: "unit", label: t("stock.col.unit"), read_only: 1 },
			// 实际仓库：官方原生 Link（只列当前公司的仓库），不是自由填写。
			{ fieldtype: "Link", options: "ERP Warehouse", fieldname: "warehouse",
				label: w("actualWarehouse"), reqd: 1, get_query: p.warehouse_query(), only_select: true },
			// 「这件货还在别处」：货的账可能在另一个仓/仓位（用户 2026-09-23 那次盘点就是这样，
			// 数出来的货落成了新位置）。摆在"实际仓库"下面，当场就能改（见 `render_elsewhere`）。
			{ fieldtype: "HTML", fieldname: "elsewhere", hidden: 1 },
			// 多人分区盘点：这件货在本次盘点里别人（或自己）已经记过的 —— 谁、在哪、多少，
			// 免得同一架货被两个人各数一遍（用户 2026-10-15）。没有就不占地方。
			{ fieldtype: "HTML", fieldname: "recorded", hidden: 1 },
			// 按台管理的商品：这个仓在库的每一台、盘到了没有（一台一条，数量固定 1）
			{ fieldtype: "HTML", fieldname: "units", hidden: 1 },
			{ fieldtype: "Autocomplete", fieldname: "location", label: w("actualLocation"),
				get_query: p.location_query(() => d?.get_value("warehouse")) },
			{ fieldtype: "Float", fieldname: "qty", label: w("entryQty"), precision: 3, reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "note", label: t("stock.count.note") },
			{ fieldtype: "Section Break", fieldname: "photo_section", hidden: 1 },
			{ fieldtype: "HTML", fieldname: "photos", hidden: 1 },
		];
		const d = p.dialog(entry ? w("editEntry") : (continuous ? w("continuous") : w("record")), fields,
			continuous ? w("saveNext") : __("Save"), async values => {
				if (!selected || busy) return;
				// Native Float normalizes an empty input to 0; an observation must be explicit.
				if (!String(d.fields_dict.qty.$input.val()).trim()) {
					frappe.msgprint(__("Enter a quantity"));
					d.fields_dict.qty.$input.trigger("focus");
					return;
				}
				busy = true;
				try {
					const payload = { qty: values.qty, warehouse: values.warehouse,
						location: values.location || "", note: values.note || "", stock_token: lookup.stock_token,
						photos: photos.map(photo => photo.url) };
					if (entry) await p.call(api + "api_stock_count_edit_entry",
						{ ...payload, entry: entry.name, version: entry.version }, "POST");
					else await p.call(api + "api_stock_count_add_entry", { ...payload, count,
						item: values.source_row || undefined, product: selected.name, request_id,
						serial: lookup.unit?.name || undefined }, "POST");
					last_place = { warehouse: values.warehouse, location: values.location || "" };
					p.success(t("stock.count.entrySaved"), "click");
					photos.length = 0;
					await on_saved?.();
					if (closed) return;
					if (!continuous) d.hide();
					else await next();
				} finally { busy = false; }
			});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		// 触屏上打开不自动聚焦框（框架默认聚焦第一格：键盘弹起、下拉展开，挡住扫码），要打字时点框；电脑照框架默认
		if (!window.matchMedia?.("(pointer: fine)").matches) d.no_focus = true;
		d.set_secondary_action(() => { d.hide(); on_done?.(); });
		// 连续盘点时第二颗是"完成"：收工离开扫码，落到差异那一屏（用户 2026-09-22 口径：
		// "…下一项或者完成"）。单条录入/修改时它仍是"查看差异"。
		d.set_secondary_action_label(continuous ? w("done") : w("review"));
		d.onhide = () => { closed = true; scanner?.stop(); };
		const code = d.fields_dict.code;
		// 框里带着盘点号查，原生下拉会多一行「已应用筛选 …」把盘点号露出来：这一格不要那一行
		code.get_filter_description = async () => null;
		// 下拉第二行只留「编码 · 条码」：框架对显示名称的选择框会把内部编号拼在最前面（Cowork 的行号是一串随机号）
		const merge = code.merge_duplicates.bind(code);
		code.merge_duplicates = rows => merge(rows).map(row => ({ ...row,
			description: String(row.description || "").split(", ").filter(part => part !== row.value).join(", ") }));
		// 回车：扫码枪 / 手打的码按码认（捕获阶段先于原生下拉，免得它把第一项当成选中）；用方向键挑了一项才交给原生选中
		let navigated = false;
		code.input.addEventListener("keydown", event => {
			if (event.key === "ArrowDown" || event.key === "ArrowUp") { navigated = true; return; }
			if (event.key !== "Enter" || event.isComposing) return;
			if (navigated && code.awesomplete?.isOpened) return;
			event.preventDefault(); event.stopImmediatePropagation();
			code.awesomplete?.close();
			scanned = false;
			void match(String(code.$input.val() || "").trim());
		}, true);
		code.input.addEventListener("input", () => { navigated = false; });
		// 从下拉里选了一件：直接开这件的录入
		code.input.addEventListener("awesomplete-selectcomplete", event => {
			const value = event.text?.value;
			if (!value || String(value).endsWith("__link_option")) return;
			scanned = false;
			void match(null, value);
		});
		// A scanner's Enter finds the product; it must not submit the observation.
		code.$input.off("keypress").on("keypress", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); }
		});
		function message(text) { d.fields_dict.message.$wrapper.empty().append($("<p class='text-muted'>").text(text)); }
		// 没认出来：除了写在弹窗里，再用原生浮动提示说一遍——相机开着时弹窗里那一行在画面下面，手机上看不到
		// （10-28 polymer 的人只看到"扫了没反应"）。
		function miss(text) { message(text); frappe.show_alert({ message: text, indicator: "orange" }); }
		// 「这件货还在别处」：`lookup.elsewhere` 是服务端给的"别的仓/仓位还有多少"。
		// 有就摆出来，没有就不占地方（用户 2026-09-23："盘点时不提示'这件货的账在别处'"）。
		function render_elsewhere() {
			const host = d.fields_dict.elsewhere;
			if (!host) return;
			const places = lookup?.elsewhere || [];
			host.$wrapper.empty();
			d.set_df_property("elsewhere", "hidden", places.length ? 0 : 1);
			if (!places.length) return;
			const text = places.slice(0, 3)
				.map(place => `${place.warehouse_name} / ${place.location || t("stock.count.blank")} · `
					+ `${p.number(place.qty)} ${place.unit || ""}`.trim())
				.join("；");
			host.$wrapper.append($("<p class='text-muted small mb-0'>").text(`${w("elsewhere")} ${text}`));
		}
		function render_recorded() {
			const host = d.fields_dict.recorded;
			const records = entry ? [] : (lookup?.recorded || []);
			host.$wrapper.empty();
			d.set_df_property("recorded", "hidden", records.length ? 0 : 1);
			if (!records.length) return;
			const text = records.slice(0, 5)
				.map(record => `${record.counted_by} · ${record.warehouse_name} / ${record.location || t("stock.count.blank")} · `
					+ p.number(record.qty))
				.join("；");
			host.$wrapper.append($("<p class='text-muted small'>").text(`${w("recorded")} ${text}`));
		}
		function editor(show) {
			d.set_df_property("scan_section", "hidden", show);
			d.set_df_property("entry_section", "hidden", !show);
			d.set_df_property("photo_section", "hidden", !show);
			d.set_df_property("photos", "hidden", !show);
			d.get_primary_btn().toggle(show);
		}
		// ── 照片 ────────────────────────────────────────────────────────────────
		// 原生上传器（`frappe.ui.FileUploader`）：多选、可选拍照。与库存单附件同一套（主会话 10-26）：照片先压缩，
		// 传到统一原件层（`erp-originals.js`，方向 stock_count），本机只留登记；这里只收集 `file_url`，提交时由服务端
		// 把本人刚上传的 File 挂到这条记录上（`counts._save_photos`）。
		function pick_photos() {
			const left = PHOTO_MAX - existing.length - photos.length;
			if (left <= 0) { frappe.msgprint(w("photoLimit")); return; }
			const on_success = file => {
				if (!file?.file_url) return;
				if (existing.length + photos.length >= PHOTO_MAX) { frappe.msgprint(w("photoLimit")); return; }
				photos.push({ url: file.file_url, name: file.file_name || String(file.file_url).split("/").pop() });
				render_photos();
			};
			const uploader = new frappe.ui.FileUploader({
				allow_multiple: true,
				allow_take_photo: true,
				allow_web_link: false,
				allow_google_drive: false,
				disable_file_browser: true,
				allow_toggle_private: false,
				make_attachments_public: false,
				restrictions: { allowed_file_types: ["image/*"] },
				on_success,
			});
			p.erp_upload_with(uploader, "stock_count", on_success, async () => {
				for (const item of uploader.uploader.files) {
					if (!item.file_obj || item.shrunk) continue;
					const smaller = await p.shrink_photo(item.file_obj);
					if (smaller !== item.file_obj) Object.assign(item, { file_obj: smaller, name: smaller.name });
					item.shrunk = true;
				}
			});
		}
		function render_photos() {
			const host = d.fields_dict.photos?.$wrapper;
			if (!host) return;
			const total = existing.length + photos.length;
			host.empty();
			$("<p class='text-muted'>").text(`${w("photos")} ${total} / ${PHOTO_MAX} · ${w("photoHint")}`).appendTo(host);
			const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
			const thumb = (url, name, removable) => {
				const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
				$("<img>").attr({ src: url, alt: name }).appendTo(cell);
				if (!removable) return;
				$("<button type='button' class='btn btn-default btn-xs'>").text(w("removePhoto"))
					.on("click", () => {
						const index = photos.findIndex(photo => photo.url === url);
						if (index >= 0) photos.splice(index, 1);
						render_photos();
					}).appendTo(cell);
			};
			existing.forEach(photo => thumb(photo.file_url, photo.file_name || "", false));
			photos.forEach(photo => thumb(photo.url, photo.name, true));
			if (total < PHOTO_MAX) {
				$("<button type='button' class='btn btn-default btn-sm'>").text(w("addPhoto"))
					.on("click", () => pick_photos()).appendTo(host);
			}
		}
		async function load_photos() {
			if (!entry?.name) return;
			existing = await p.call(api + "api_stock_count_photos", { entry: entry.name });
			render_photos();
		}
		function render_units() {
			const rows = lookup?.units || [];
			d.set_df_property("units", "hidden", !rows.length);
			if (!rows.length) return;
			const kind = lookup.product?.serial_kind === "device" ? "device" : "vehicle";
			const counted = rows.filter(row => row.counted || row.name === lookup.unit?.name).length;
			const wrapper = d.fields_dict.units.$wrapper.empty();
			$(`<div class="text-muted small"></div>`).text(__("serial.progress", [counted, rows.length])).appendTo(wrapper);
			// 原生 DataTable：这个仓在库的每一台，盘到的标出来
			new frappe.DataTable($("<div>").appendTo(wrapper).get(0), { layout: "fluid", serialNoColumn: false,
				columns: [kind === "device" ? __("serial.device.primary") : __("serial.vehicle.primary"),
					kind === "device" ? __("serial.device.secondary") : __("serial.vehicle.secondary"), __("serial.color"), __("Status")]
					.map(name => ({ name, editable: false })),
				data: rows.map(row => [row.serial_no, row.secondary_no || "", row.color || "",
					row.counted || row.name === lookup.unit?.name ? __("serial.counted") : __("serial.notCounted")]) });
		}
		async function choose_source() {
			if (!lookup || !selected) return;
			const source = lookup.rows.find(r => r.name === d.get_value("source_row"));
			// 实际仓库是官方 Link，值是仓库编号（显示名由框架按标题取）；写成名称时实际仓位按它取选项会报「未找到仓库」（11-02 真机）
			await d.set_value("warehouse", entry?.warehouse || source?.warehouse || lookup.warehouse);
			await d.set_value("location", entry?.location ?? source?.location ?? "");
		}
		// 物料页同一个新建弹窗（`products.js` 的 `open_quick`，用户 11-01「原地弹出新建物料的弹窗」）：码预填进「条码/二维码」；
		// 编码预览与保存走盘点录入权的接口（来由记这份盘点，服务端 `count.create_product_here`），建好按存下的码接着记数。
		async function create_here(code) {
			code = String(code || "").trim();
			if (!code) return;
			await p.link_editors["ERP Product"]({ code,
				preview: () => p.call(api + "api_stock_count_next_product_code", { count }),
				submit: (payload, values) => {
					const { barcode, qr_payload, ...rest } = payload;
					return p.call(api + "api_stock_count_create_product", { ...rest, count, code: barcode || qr_payload || "",
						track_serial: values.track_serial ? 1 : 0 }, "POST");
				},
				on_saved: async saved => {
					d.set_df_property("new_product", "hidden", 1);
					const used = String(saved.code_value || "").trim();
					await match(used || null, used ? undefined : saved.name);
				},
			});
		}
		async function match(code, product) {
			if (busy || selected || closed || (!code && !product)) return;
			busy = true; scanner?.pause();
			d.set_df_property("new_product", "hidden", 1); missing = "";
			d.fields_dict.message.$wrapper.empty();  // 上一件的提示（已保存 / 查不到）查下一件时清掉，查不到再写上（验收 10-29）
			try {
				lookup = await p.call(api + "api_stock_count_lookup", { count, code: code || undefined, product });
				if (closed) return;
				if (lookup.candidates) {
					// 同一个码命中几件商品（存量里撞了的码）：让人选一件再查，不默认挑一个
					busy = false;
					const picked = await p.pick_product(lookup.candidates);
					if (picked) return match(null, picked);
					scanner?.resume(code);
					return;
				}
				if (lookup.not_product_code) {
					// 扫到的是网址（TIS 认证码等，岳父 11-01）：不是商品码，说一句、不出「新建物料」，相机接着扫、先跳过这个码
					miss(lookup.not_product_code);
					lookup = null;
					scanner?.resume(code);
					if (code) p.scan_result_sound(false);
					return;
				}
				if (lookup.not_found) {
					// 系统里没有这个码（服务端照官方 POS 不弹提示框）：停相机、收起画面，「新建物料」就在查找框下面，
					// 焦点放上去（岳父 11-01 手机上：提示框挡着、按钮在相机画面下面看不到）。换个码就再点相机或手动查找。
					lookup = null; missing = code;
					stop_camera();
					miss(__("No product matches this code"));
					d.set_df_property("new_product", "hidden", 0);
					d.fields_dict.new_product.$input.trigger("focus");
					p.scan_result_sound(false);
					return;
				}
				if (lookup.track_serial && !lookup.unit && !entry) {
					// 按台管理的商品要扫这一台自己的号（车架号 / 设备号），扫商品码认不出是哪一台。说清是哪件商品；
					// 这个仓一台都没登记时也照实说（10-28 polymer：商品勾了按台管理、一台没登记，人只看到扫不出东西）。
					miss([lookup.product?.product_name, __("Count products tracked by unit one unit at a time"),
						lookup.units?.length ? "" : __("No units of this product are registered in this warehouse")].filter(Boolean).join(" · "));
					scanner?.resume(code);
					if (code) p.scan_result_sound(false);
					return;
				}
				selected = lookup.product;
				request_id = crypto.randomUUID();
				stop_camera();  // 认出来了：扫码器关掉，直接出这件的字段
				d.set_df_property("source_row", "options", lookup.rows.map(r => ({
					value: r.name, label: `${r.warehouse_name} / ${r.location || t("stock.count.blank")} · ${p.number(r.book_qty)} ${r.unit || ""}`,
				})));
				d.set_df_property("source_row", "hidden", !lookup.rows.length);
				await d.set_value("source_row", row?.name || lookup.rows[0]?.name || "");
				await show_product(selected);
				await d.set_value("unit", selected.base_unit || selected.unit || "");
				await choose_source();
				// 沿用上一件的位置只给**账上没有位置**的新品（同一个货架连续盘时省得重填）；这件在账上有位置
				// 就用账面位置，否则会盖掉它、凭空多出一条位置差异，结束盘点时真的调库存（录制问题 #21）。
				// 修改已录的记录也不沿用：那条记录自己的位置才是对的。
				if (last_place && !entry && !lookup.rows.length) {
					await d.set_value("warehouse", last_place.warehouse);
					await d.set_value("location", last_place.location);
				}
				await d.set_value("qty", entry ? entry.qty : (lookup.unit ? 1 : ""));
				if (!entry && !lookup.unit) d.fields_dict.qty.$input.val("");
				d.set_df_property("qty", "read_only", lookup.track_serial ? 1 : 0);
				render_units();
				await d.set_value("note", entry?.note || "");
				editor(true);
				await load_photos();
				render_photos();
				render_elsewhere();
				render_recorded();
				d.fields_dict.qty.$input.trigger("focus");
				// 结果那一声：查到了才响（从码来的那一次才算 —— 打开已录那一行不算"查了"）。
				if (code) p.scan_result_sound(true);
			} catch (error) {
				selected = null;
				// 服务端明确不认（417：查不到这个码）才说"找不到"；网络断、服务端出错、这页自己的脚本错都只说查找失败，
				// 错误照记，不冒充"找不到"。读码器跳过这个码：镜头还对着它就不再反复查（换个码或手动点查找再试）。
				const refused = error?.status === 417;
				// 417 时服务端那句已经弹成原生提示框，这里只写进弹窗；查找失败没人提示过，再浮动提示一次。
				if (refused) message(w("lookupFailed")); else miss(w("lookupError"));
				// 这页自己的脚本错交给统一的前端报错（服务端的错服务端自己记）。
				if (!refused) console.error(error);
				if (!error?.status) window.pearnlyReportError?.(error, "stocktake-lookup");
				scanner?.resume(code);
				if (code) p.scan_result_sound(false);
			} finally { busy = false; }
		}
		// 认出的这件物料：名称、编码（标签按内部 / 系统编码换）、分类、特性/颜色、「编辑」入口
		async function show_product(product) {
			await d.set_value("product_name", product.product_name);
			d.set_df_property("product_code", "label", product.code_internal ? __("product.col.internalCode") : t("stock.col.code"));
			await d.set_value("product_code", product.code || "");
			for (const [field, value] of [["product_barcode", product.barcode_shown], ["product_category", product.category_name],
				["product_attribute", product.attribute]]) {
				await d.set_value(field, value || "");
				d.set_df_property(field, "hidden", !value);
			}
			d.set_df_property("edit_product", "hidden", !product.can_edit);
		}
		async function edit_product() {
			if (!selected?.name || !p.link_editors["ERP Product"]) return;
			const name = selected.name;
			await p.link_editors["ERP Product"]({ name, on_saved: async () => {
				const fresh = await p.call(api + "api_stock_count_lookup", { count, product: name });
				if (closed || selected?.name !== name || !fresh?.product) return;
				selected = fresh.product;
				await show_product(selected);
			} });
		}
		// `skip`：先跳过这个码，直到镜头里出现别的码（「继续」重开时镜头往往还对着刚盘的那件，免得马上又记一次）
		async function start_camera(skip = "") {
			if (scanner || closed) return;
			// 安卓 LINE 普通内置浏览器里网页拿不到相机：直接转 LIFF 打开这份盘点并接着扫（LINE 弹原生授权），
			// 这一页的扫码窗口随即关掉（LIFF 盖在它上面打开，关掉 LIFF 回来不留一个黑着的相机框）。
			if (p.scan_liff_first(true)) { d.hide(); return; }
			camera = true;
			d.set_df_property("video", "hidden", false);
			d.set_df_property("close_camera", "hidden", false);
			scanner = p.scan_reader({ host: d.fields_dict.video.$wrapper, kind: "all",
				on_result: value => { scanned = true; last_code = value; return match(value); },
				on_error: error => {
					scanner?.stop(); scanner = null; camera = false;
					// 在 LINE 普通内置浏览器里：转 LIFF 打开这份盘点并接着扫（LINE 会弹原生相机授权）。
					if (p.scan_failure(error) === "in_line" && p.scan_to_liff(true)) { d.hide(); return; }
					// 打不开就把黑框收起来，只留原因那一句（手动输入编码照常可用）。
					d.set_df_property("video", "hidden", true); d.set_df_property("torch", "hidden", true);
					d.set_df_property("close_camera", "hidden", true);
					p.scan_hint(d.fields_dict.message.$wrapper, error, restart_camera);
				},
				// App 内置浏览器里 5 秒还没开相机（请求被挂着）：LINE 里转 LIFF，别的给原因、继续等。
				on_waiting: reason => {
					if (reason === "in_line" && p.scan_to_liff(true)) { scanner?.stop(); d.hide(); return; }
					p.scan_hint(d.fields_dict.message.$wrapper, reason, restart_camera);
				},
				on_started: () => d.fields_dict.message.$wrapper.empty(),
			});
			await scanner.start();
			if (skip) scanner?.resume(skip);
			d.set_df_property("torch", "hidden", !scanner?.has_torch());
		}
		function stop_camera() {
			if (!camera && !scanner) return;
			scanner?.stop(); scanner = null; camera = false;
			d.set_df_property("video", "hidden", true); d.set_df_property("torch", "hidden", true);
			d.set_df_property("close_camera", "hidden", true);
		}
		// 手动关掉扫码器：回到框里
		function close_camera() {
			stop_camera();
			code.$input.trigger("focus");
		}
		// 去设置里开了权限再切回来：重开相机（`p.scan_hint` 只在需要去设置时调它）。
		function restart_camera() {
			if (closed) return;
			scanner?.stop(); scanner = null;
			void start_camera();
		}
		async function next() {
			selected = null; lookup = null; row = null; entry = null;
			existing = [];
			photos.length = 0;
			await d.set_value("code", ""); await d.set_value("qty", "");
			editor(false); render_photos(); message(w("nextReady"));
			render_elsewhere();   // 换下一条：别处那份提示也收起来
			render_recorded();
			// 上一件是扫码来的：接着开扫码器；打字选的：回到框里
			if (scanned) void start_camera(last_code);
			else code.$input.trigger("focus");
		}
		editor(false);
		if (row) await match(null, row.product);
		else if (scan) await start_camera();
	};
})();
