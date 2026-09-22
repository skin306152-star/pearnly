/* Native BaseList paging + ListView rendering over scoped product APIs.
 * No report grid, generic DocType RPC, platform actions, or cloned list markup.
 */
(() => {
	const p = frappe.pearnly;
	class ProductList extends frappe.views.BaseList {
		constructor(options) {
			super(options);
			if (options.container) this.page = Object.assign(Object.create(this.page), {main: $(options.container)});
			this.parent = {page: this.page};
			Object.assign(this, {view: "List", start: 0, page_length: options.page_length || 20,
				selected_page_count: options.page_length || 20, data: [], total: 0, sequence: 0,
				hide_filters: true, hide_sort_selector: true, column_max_widths: {},
				link_field_title_fields: {}, list_view_settings: {}});
			this.settings = {formatters: {}, button: options.button};
			this.spec = options.columns;
			this.columns = options.columns.map((column, i) => {
				// The framework measures each cell with `$(column_html)`, so a formatter
				// must return markup: plain text like "ops@example.test" is parsed as a
				// selector and throws. Escape the value, then wrap it.
				this.settings.formatters[column.fieldname] = (value, df, row) => {
					const text = column.format ? column.format(value, row) : value;
					return `<span class="ellipsis" title="${p.escape(text ?? "")}">${p.escape(text ?? "")}</span>`;
				};
				return {type: i === 0 ? "Subject" : "Field", df: {fieldtype: "Data", ...column}};
			});
			this.page.main.addClass("layout-main-list list-view");
			this.ready = frappe.model.with_doctype(this.doctype).then(() => {
				if (!this.destroyed) return this.setup_main_section();
			}).then(() => {
				if (this.destroyed) return;
				this.$frappe_list.toggleClass("pearnly-list-with-actions", Boolean(this.row_actions));
				this.$frappe_list.toggleClass("pearnly-compact-list", Boolean(this.compact_columns));
				if (this.selectable) this.setup_check_events();
				this.$result.on("click keydown", ".list-row-container", event => {
					if (!this.on_open) return;
					if (event.type === "keydown" &&
						(event.key !== "Enter" || event.target !== event.currentTarget)) return;
					if (event.type === "click" && (event.button > 0 || window.getSelection()?.toString())) return;
					// Keep native selection and embedded controls independent from opening a row.
					if ($(event.target).closest(
						"button, input, select, textarea, label, .select-like, [contenteditable], a:not([data-product-row])"
					).length) return;
					const name = $(event.currentTarget).find("a[data-product-row]").attr("data-product-row");
					const row = this.data.find(item => item.name === name);
					if (!row) return;
					event.preventDefault();
					this.on_open(row);
				});
				this.$result.on("click", ".btn-action", event => {
					event.preventDefault();
					this.settings.button?.action(this.data[Number(event.currentTarget.dataset.idx)]);
				});
				this.$result.on("click", "[data-product-action]", async event => {
					event.preventDefault();
					event.stopPropagation();
					const button = event.currentTarget;
					const row = this.data.find(item => item.name === button.dataset.productRow);
					if (!row) return;
					const action = this.row_actions?.(row).find(item => item.id === button.dataset.productAction);
					if (!action || action.disabled || button.disabled) return;
					button.disabled = true;
					try { await action.action(row); }
					catch (error) {
						if (!error?._server_messages && !error?.responseJSON) console.error(error);
					} finally { button.disabled = false; }
				});
				// BaseList normally does this in refresh() and via cur_list. Product Pages
				// use scoped APIs, so retain its sizing on refresh, resize and return navigation.
				this.layout_handler = frappe.utils.debounce(() => this.resize_list(), 300);
				$(window).on("resize", this.layout_handler);
				this.page.wrapper.on("show", this.layout_handler);
				return this.refresh();
			});
		}
		get_subject_element(doc, value) {
			const wrapper = document.createElement("span");
			if (this.selectable) {
				// Clone the installed ListView's checkbox and spacing, then attach the
				// native row-selection hooks. No platform document link is introduced.
				const select = this.$result.find(".list-header-subject .select-like")[0].cloneNode(true);
				const checkbox = select.querySelector("input");
				checkbox.className = "list-row-checkbox";
				checkbox.dataset.name = doc.name;
				checkbox.dataset.doctype = this.doctype;
				checkbox.title = String(value ?? "");
				checkbox.setAttribute("aria-label", String(value ?? ""));
				wrapper.appendChild(select);
			}
			const element = document.createElement(this.on_open ? "a" : "span");
			element.textContent = value;
			element.title = String(value ?? "");
			element.className = "level-item ellipsis";
			if (this.on_open) {element.href = "#"; element.dataset.productRow = doc.name;}
			wrapper.appendChild(element);
			return wrapper;
		}
		render_header() {
			const header = $(this.get_header_html());
			// Only opt-in product lists expose selection; likes and generic sort stay absent.
			header.find(".list-liked-by-me").remove();
			if (!this.selectable) header.find(".select-like, .checkbox-actions").remove();
			header.find("[data-sort-by]").each((_, el) => {
				el.title = el.textContent.trim();
				el.removeAttribute("data-sort-by");
			});
			this.$list_head_subject = this.$checkbox_actions = null;
			header.find(".list-count").text(`${this.data.length} / ${this.total}`);
			this.$result.prepend(header);
		}
		toggle_actions_menu_button() {
			this.on_selection?.(this.get_checked_items());
		}
		toggle_workflow_actions() {
			// Product selection only exposes its explicitly supplied action.
		}
		apply_column_widths() {
			if (!this.compact_columns) {
				return frappe.views.ListView.prototype.apply_column_widths.call(this);
			}
			for (const col of this.spec) {
				this.$result.find(`[data-fieldname="${col.fieldname}"]`).css({
					width: col.width, flex: `1 1 ${col.width}px`, minWidth: col.min_width,
				});
			}
		}
		get_no_result_message() {
			// Keep upstream SVG/layout; expose only a caller-provided working action.
			return frappe.views.ListView.prototype.get_no_result_message.call({
				doctype: this.doctype, meta: {description: __("Nothing to show")},
				can_create: Boolean(this.empty_action), get_documentation_link: () => "",
			});
		}
		get_right_html(doc) {
			if (!this.row_actions) return "";
			const actions = document.createElement("span");
			actions.className = "pearnly-row-actions";
			for (const action of this.row_actions(doc)) {
				const button = document.createElement("button");
				button.type = "button";
				button.className = "btn btn-default btn-xs";
				button.textContent = action.label;
				button.disabled = Boolean(action.disabled);
				button.dataset.productAction = action.id;
				button.dataset.productRow = doc.name;
				actions.appendChild(button);
			}
			return actions.outerHTML;
		}
		resize_list() {
			if (this.destroyed || !this.page.main.is(":visible")) return;
			if (this.compact_columns) this.apply_column_widths();
			const actions = this.$result.find(".pearnly-row-actions").get();
			if (actions.length) {
				this.$frappe_list[0].style.setProperty("--pearnly-actions-width",
					`${Math.max(...actions.map(el => el.scrollWidth))}px`);
			}
			if (this.compact_columns && this.data.length) {
				const row = this.$result.find(".list-row")[0];
				const px = (el, property) => parseFloat(getComputedStyle(el)[property]) || 0;
				const columns = Array.from(row.querySelectorAll("[data-fieldname]"));
				const minColumns = columns.reduce((sum, el, i) =>
					sum + this.spec[i].min_width + px(el, "marginRight"), 0);
				const right = row.querySelector(".level-right");
				const minWidth = minColumns + right.getBoundingClientRect().width
					+ px(row.parentElement, "paddingLeft") + px(row.parentElement, "paddingRight");
				this.$result.children(".list-row-container").css({
					width: "100%", minWidth: frappe.is_mobile() ? "100%" : minWidth,
				});
			}
			if (this.container) {
				// Embedded home ledger shares native rendering/paging, but is not the
				// full-page list whose height Frappe derives from the viewport top.
				this.$frappe_list.find(".result-container").css({height: "auto", maxHeight: "60vh"});
			} else this.set_result_height();
		}
		destroy() {
			this.destroyed = true;
			++this.sequence;
			$(window).off("resize", this.layout_handler);
			this.page.wrapper.off("show", this.layout_handler);
			this.$result?.find("[data-original-title]").tooltip("dispose");
			this.$frappe_list?.remove();
		}
		async reload() {
			this.start = 0;
			this.page_length = this.selected_page_count;
			return this.refresh();
		}
		async refresh() {
			if (this.destroyed) return;
			const request = ++this.sequence;
			const start = this.start, length = this.page_length;
			this.freeze(true);
			try {
				const result = await this.load({start, page_length: length});
				if (request !== this.sequence) return;
				this.total = result.total;
				// The framework renders the first column as the row subject and skips its
				// formatter, so format that column here instead.
				const subject = this.columns[0];
				const subjectFormat = this.spec[0]?.format;
				const rows = subjectFormat
					? result.items.map(row => ({...row,
						[subject.df.fieldname]: subjectFormat(row[subject.df.fieldname], row)}))
					: result.items;
				const checked = this.selectable ? this.get_checked_items(true) : [];
				this.prepare_data({message: rows});
				this.$result.find("[data-original-title]").tooltip("dispose");
				this.render_list();
				this.$result.find("[data-fieldname] [title]:not(input)").tooltip({
					container: "body", trigger: "hover", html: false, delay: {show: 300, hide: 0},
				});
				if (this.selectable) {
					this.$result.find(".list-row-checkbox").each((_, el) => {
						el.checked = checked.includes(el.dataset.name);
					});
					this.on_row_checked();
				}
				// A working LINE action must also be reachable on a small display.
				this.$result.find(".btn-action").parent().removeClass("hidden-xs");
				this.toggle_result_area();
				this.$paging_area.find(".btn-more").toggle(this.data.length < this.total);
				this.$no_result.html(this.get_no_result_message());
				if (this.empty_action) this.$no_result.find(".btn-new-doc")
					.text(this.empty_action.label).on("click", this.empty_action.action);
				this.resize_list();
				this.reset_defaults();
			} catch (error) {
				if (request === this.sequence) {
					this.data = [];
					if (this.selectable) this.clear_checked_items();
					this.toggle_result_area();
					this.$no_result.text(__("Unable to load this page. Please reload."));
				}
				throw error;
			} finally {
				if (request === this.sequence) this.freeze(false);
			}
		}
	}
	// Call the installed Frappe renderer directly, so layout and future fixes stay upstream.
	for (const method of ["render_list", "get_header_html", "get_header_html_skeleton",
		"get_list_row_html", "get_list_row_html_skeleton", "get_left_html", "get_column_html",
		"generate_button_html", "generate_dropdown_html", "update_listview_classes",
		"setup_check_events", "on_row_checked", "get_checked_items", "update_checkbox", "clear_checked_items"]) {
		ProductList.prototype[method] = frappe.views.ListView.prototype[method];
	}
	p.list = options => new ProductList(options);
})();
