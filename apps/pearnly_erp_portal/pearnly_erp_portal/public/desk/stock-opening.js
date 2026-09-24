/* Opening inventory: native Page, FieldGroup, collapsible sections and FileUploader.
 * Decimal inputs use native Data fields to preserve empty versus explicit zero;
 * matching, Decimal validation and atomic posting belong to the server.
 */
(() => {
	const p = frappe.pearnly;
	p.stockOpening = (page, t, current) => {
		const root = "pearnly-inventory", api = "pearnly_erp_portal.inventory.api.";
		const w = (key, ...args) => t(`stock.opening.${key}`, ...args);
		const active = () => current() && frappe.get_route().join("/") === `${root}/opening`;
		let form, pagination, busy = false, reviewed = null, revision = 0, page_index = 0;
		const page_size = 20, lines = [], visible = [], review_rows = new Map();
		const back = () => p.redirect_up(root);
		const input_names = ["product", "product_code", "barcode", "unit", "location", "qty", "unit_cost"];
		const text_value = value => String(value ?? "");
		const header_values = { warehouse: frappe.boot.pearnly_warehouse || "",
			as_of_date: frappe.datetime.get_today(), note: "" };
		const header_change = key => () => {
			if (!form || !active()) return;
			const value = form.get_value(key);
			if (text_value(value) === text_value(header_values[key])) return;
			header_values[key] = value; changed();
		};
		const make_line = (initial = {}) => ({ data: {
			...Object.fromEntries(input_names.map(key => [key, initial[key] ?? ""])),
			product_name: initial.product_name || "", code: initial.code || "",
		}, selection: 0 });
		function save_visible() {
			if (reviewed) return;
			for (const view of visible) for (const key of input_names)
				view.line.data[key] = view.form.get_value(key);
		}
		function values() {
			save_visible();
			return { warehouse: form.get_value("warehouse"), as_of_date: form.get_value("as_of_date"),
				note: form.get_value("note"), rows: lines.map((line, index) => ({
					...Object.fromEntries(input_names.map(key => [key, line.data[key]])),
					// UI sequence is unique even when imported Excel row numbers start at 2.
					row_number: index + 1,
				})).filter(row => input_names.some(key => key !== "unit" && String(row[key] ?? "").trim())) };
		}
		function changed() {
			++revision;
			clear_review();
			if (!reviewed) return;
			reviewed = null;
			lock_inputs(false);
			page.set_primary_action(w("preview"), preview);
			page.set_secondary_action(__("Cancel"), back);
		}
		async function run(action) {
			if (busy || !active()) return;
			busy = true;
			page.btn_primary?.prop("disabled", true);
			update_pager();
			try { await action(); }
			finally {
				busy = false;
				if (active()) { page.btn_primary?.prop("disabled", false); update_pager(); }
			}
		}
		function lock_inputs(locked) {
			for (const field of ["warehouse", "as_of_date", "note", "pick_file", "add_line"])
				form.set_df_property(field, "read_only", locked ? 1 : 0);
			for (const view of visible) {
				for (const field of input_names.filter(key => key !== "unit"))
					view.form.set_df_property(field, "read_only", locked ? 1 : 0);
				view.form.set_df_property("remove", "hidden", locked ? 1 : 0);
				view.form.refresh_sections();
			}
		}
		function clear_review() {
			review_rows.clear();
			for (const view of visible) {
				view.form.set_df_property("status_text", "hidden", 1);
				view.form.set_df_property("amount", "hidden", 1);
				view.form.refresh_sections();
			}
			form.set_df_property("total_value", "hidden", 1);
			form.refresh_sections();
		}
		function edit() {
			if (busy) return;
			reviewed = null;
			lock_inputs(false);
			clear_review();
			page.set_primary_action(w("preview"), preview);
			page.set_secondary_action(__("Cancel"), back);
		}
		function relabel(view) {
			const data = view.line.data, index = lines.indexOf(view.line);
			const caption = [data.product_name || data.product, data.product_code || data.code || data.barcode]
				.filter((value, position, all) => value && all.indexOf(value) === position).join(" · ");
			view.section.head.contents()
				.filter((_, node) => node.nodeType === Node.TEXT_NODE).first()
				.replaceWith(document.createTextNode(`${w("item")} ${index + 1}${caption ? " · " + caption : ""} `));
		}
		function update_pager() {
			if (!form) return;
			if (pagination) {
				pagination.page_index = page_index + 1;
				pagination.update_page_numbers();
				pagination.last_page_button?.text(w("lastPage"));
				pagination.$page_number?.val(pagination.page_index);
				pagination.wrapper.find("button, input").prop("disabled", busy);
			}
			form.set_df_property("pagination", "hidden", !pagination || lines.length <= page_size);
			form.refresh_sections();
		}
		function render_page(focus) {
			save_visible();
			visible.forEach(view => view.wrapper.remove()); visible.length = 0;
			page_index = Math.max(0, Math.min(page_index, Math.ceil(lines.length / page_size) - 1));
			const start = page_index * page_size;
			for (let index = start; index < Math.min(start + page_size, lines.length); ++index) {
				const view = render_line(lines[index], index);
				view.section.collapse(focus ? view.line !== focus : index !== start);
			}
			lock_inputs(Boolean(reviewed)); update_pager();
		}
		function add_line(initial = {}) {
			if (busy || reviewed) return;
			save_visible();
			const line = make_line(initial); lines.push(line); changed();
			page_index = Math.floor((lines.length - 1) / page_size); render_page(line);
		}
		function render_line(line, index) {
			const initial = line.data, result = review_rows.get(line);
			const wrapper = $("<div>").appendTo(form.fields_dict.rows.$wrapper);
			const outer = p.layout(wrapper, [
				{ fieldtype: "Section Break", label: `${w("item")} ${index + 1}`, collapsible: 1 },
				{ fieldtype: "HTML", fieldname: "fields" },
			]);
			const view = { line, wrapper, section: outer.sections[0], ready: false,
				observed: Object.fromEntries(input_names.map(key => [key, text_value(initial[key])])) };
			view.section.wrapper.addClass("px-0");
			view.section.columns.forEach(column => column.wrapper.addClass("px-0"));
			// Native controls finish default validation asynchronously, after make() returns.
			// Remember each control's value separately from the saved page model: a late
			// same-value callback is initialization, while another field may already have
			// saved a genuine product edit before its own validation callback arrives.
			const change = key => {
				if (!view.ready || !active() || !visible.includes(view)) return false;
				const value = text_value(view.form.get_value(key));
				if (value === view.observed[key]) return false;
				view.observed[key] = value; changed(); save_visible(); relabel(view);
				return true;
			};
			view.form = p.layout(outer.fields_dict.fields.$wrapper, [
				{ fieldtype: "Link", options: "ERP Product", fieldname: "product", label: t("stock.col.name"), reqd: 1,
					get_query: () => ({ filters: { is_active: 1 } }), only_select: true,
					async onchange() {
						if (!view.ready || reviewed || !visible.includes(view)) return;
						if (!change("product")) return;
						const name = view.form.get_value("product"), selection = ++line.selection;
						Object.assign(line.data, { product_code: "", barcode: "", unit: "", product_name: "", code: "" });
						Object.assign(view.observed, { product_code: "", barcode: "", unit: "" });
						await view.form.set_values({ product_code: "", barcode: "", unit: "" });
						relabel(view);
						if (!name) return;
						const item = await p.call("pearnly_erp_portal.products.service.api_get_product", { name });
						if (!active() || reviewed || !lines.includes(line) || line.selection !== selection || line.data.product !== name) return;
						Object.assign(line.data, { unit: item.base_unit || item.unit, product_name: item.product_name || name, code: item.code || "" });
						const current_view = visible.find(entry => entry.line === line);
						if (current_view) {
							await current_view.form.set_value("unit", line.data.unit);
							relabel(current_view);
						}
					} },
				{ fieldtype: "Data", fieldname: "unit", label: t("stock.col.unit"), read_only: 1 },
				{ fieldtype: "Data", fieldname: "location", label: t("stock.count.location"), onchange: () => change("location") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "qty", label: w("qty"), reqd: 1, onchange: () => change("qty") },
				{ fieldtype: "Data", fieldname: "unit_cost", label: w("cost"), reqd: 1, onchange: () => change("unit_cost") },
				{ fieldtype: "Currency", fieldname: "amount", label: w("amount"), read_only: 1,
					hidden: !result || Boolean(result.error), default: result?.amount },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "product_code", label: t("stock.col.code"),
					hidden: !initial.product_code, onchange: () => change("product_code") },
				{ fieldtype: "Data", fieldname: "barcode", label: t("stock.col.barcode"),
					hidden: !initial.barcode, onchange: () => change("barcode") },
				{ fieldtype: "Small Text", fieldname: "status_text", label: t("stock.col.status"), read_only: 1,
					hidden: !result, default: result ? (result.error ? __(result.error) : w(result.status)) : "" },
				{ fieldtype: "Button", fieldname: "remove", label: __("Delete"), click: () => {
					if (busy || reviewed) return;
					save_visible(); lines.splice(lines.indexOf(line), 1); changed(); render_page();
				} },
			].map(field => ({ ...field, ...(initial[field.fieldname] !== undefined ? { default: initial[field.fieldname] } : {}) })));
			for (const key of ["qty", "unit_cost"]) view.form.fields_dict[key].$input?.attr("inputmode", "decimal");
			visible.push(view); view.ready = true; relabel(view);
			return view;
		}
		async function preview() {
			return run(async () => {
				const payload = values(), version = revision;
				if (!payload.warehouse || !payload.as_of_date) { frappe.msgprint(w("headerRequired")); return; }
				const result = await p.call(api + "api_preview_stock_opening", payload, "POST");
				if (!active() || version !== revision || JSON.stringify(payload) !== JSON.stringify(values())) return;
				clear_review();
				for (const row of result.rows) if (lines[row.row_number - 1])
					review_rows.set(lines[row.row_number - 1], row);
				const error = result.rows.find(row => row.error), error_line = error && lines[error.row_number - 1];
				if (error_line) page_index = Math.floor((error.row_number - 1) / page_size);
				if (result.valid) reviewed = { payload, token: result.review_token };
				render_page(error_line);
				form.set_value("total_value", result.total_value);
				form.set_df_property("total_value", "hidden", 0);
				form.refresh_sections();
				if (!result.valid) { frappe.msgprint(w("fixErrors")); return; }
				page.set_primary_action(w("submit"), submit);
				page.set_secondary_action(__("Edit"), edit);
			});
		}
		async function submit() {
			if (!reviewed) return;
			return run(async () => {
				const snapshot = reviewed;
				const result = await p.call(api + "api_submit_stock_opening",
					{ ...snapshot.payload, review_token: snapshot.token }, "POST");
				if (!active()) return;
				p.success(w("saved", result.created, result.skipped)); back();
			});
		}
		function download_template() {
			const warehouse = form.get_value("warehouse");
			if (!warehouse) { frappe.msgprint(w("headerRequired")); return; }
			window.location.assign(`/api/method/${api}api_stock_opening_template?warehouse=${encodeURIComponent(warehouse)}`);
		}
		function upload() {
			if (busy || reviewed) return;
			const original = JSON.stringify(values()), version = revision;
			const unchanged = () => active() && !reviewed && version === revision && JSON.stringify(values()) === original;
			const open = () => {
				if (busy || !unchanged()) return;
				return new frappe.ui.FileUploader({
					allow_multiple: false, allow_web_link: false, allow_google_drive: false,
					allow_take_photo: false, disable_file_browser: true, allow_toggle_private: false,
					make_attachments_public: false,
					restrictions: { allowed_file_types: [".xlsx"], max_file_size: 5 * 1024 * 1024 },
					on_success: async file => {
						if (!unchanged()) return;
						await run(async () => {
							const result = await p.call(api + "api_read_stock_opening_file", { file_url: file.file_url }, "POST");
							if (!unchanged()) return;
							let rows = result.rows;
							const header = values();
							if (header.warehouse && header.as_of_date) {
								const normalized = await p.call(api + "api_preview_stock_opening", { ...header, rows }, "POST");
								if (!unchanged()) return;
								const matched = new Map(normalized.rows.map(row => [row.row_number, row]));
								rows = rows.map((row, index) => {
									const resolved = matched.get(row.row_number ?? index + 1);
									return resolved && !resolved.error ? { ...row, product: resolved.product,
										product_name: resolved.product_name, code: resolved.code, unit: row.unit || resolved.unit } : row;
								});
							}
							visible.forEach(view => view.wrapper.remove()); visible.length = 0;
							lines.length = 0; rows.forEach(row => lines.push(make_line(row)));
							page_index = 0; changed(); render_page();
						});
					},
				});
			};
			if (values().rows.length) frappe.confirm(w("replaceRows"), open); else open();
		}
		const company = (frappe.boot.pearnly_companies || []).find(row => row.is_current);
		page.set_title(w("title"));
		p.breadcrumbs({ label: __("nav.inventory"), route: p.subroute(root), trail: [{ label: w("title"), route: "" }] });
		form = p.layout(page.main, [
			{ fieldtype: "Section Break" },
			{ fieldtype: "Data", fieldname: "company", label: __("Company"), read_only: 1,
				default: company?.company_name || frappe.boot.pearnly_company_name },
			{ fieldtype: "Link", fieldname: "warehouse", label: t("stock.col.warehouse"), options: "ERP Warehouse",
				get_query: p.warehouse_query(), only_select: true, reqd: 1,
				default: header_values.warehouse, onchange: header_change("warehouse") },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Date", fieldname: "as_of_date", label: w("date"), reqd: 1,
				default: header_values.as_of_date, onchange: header_change("as_of_date") },
			{ fieldtype: "Small Text", fieldname: "note", label: __("Note"), onchange: header_change("note") },
			{ fieldtype: "Section Break" },
			{ fieldtype: "Button", fieldname: "download_template", label: w("template"), click: download_template },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Button", fieldname: "pick_file", label: w("import"), click: upload },
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "rows" },
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "pagination", hidden: 1 },
			{ fieldtype: "Section Break" },
			{ fieldtype: "Button", fieldname: "add_line", label: w("add"), click: () => {
				add_line();
			} },
			{ fieldtype: "Section Break" },
			{ fieldtype: "Currency", fieldname: "total_value", label: w("total"), read_only: 1, hidden: 1 },
		]);
		add_line();
		page.set_primary_action(w("preview"), preview);
		page.set_secondary_action(__("Cancel"), back);
		// Reuse the installed Frappe paginator without constructing a tabular Grid.
		// Its native controls own First/Previous/page input/Next/Last and their bounds.
		return import("/assets/frappe/js/frappe/form/grid_pagination.js").then(({ default: GridPagination }) => {
			if (!active()) return;
			const wrapper = form.fields_dict.pagination.$wrapper;
			$("<div class='grid-pagination'>").appendTo(wrapper);
			const block_inactive = event => {
				if (!busy && active()) return;
				event.preventDefault(); event.stopImmediatePropagation(); event.stopPropagation();
			};
			for (const event of ["click", "keydown", "focusout"])
				wrapper[0].addEventListener(event, block_inactive, true);
			pagination = new GridPagination({ wrapper, grid: {
				data: lines, meta: { grid_page_length: page_size },
				render_result_rows() {
					const requested = Number(pagination.page_index);
					if (busy || !active() || !Number.isFinite(requested) || !Number.isInteger(requested)) {
						pagination.page_index = page_index + 1;
						pagination.$page_number?.val(pagination.page_index);
						// Native go_to_page writes its original input after this callback.
						Promise.resolve().then(() => {
							if (active()) pagination.$page_number?.val(pagination.page_index);
						});
						return;
					}
					page_index = requested - 1;
					render_page();
				},
				scroll_to_top() { if (!busy && active()) frappe.utils.scroll_to(form.fields_dict.rows.$wrapper); },
			} });
			update_pager();
		}).catch(error => { if (active()) throw error; });
	};
})();
