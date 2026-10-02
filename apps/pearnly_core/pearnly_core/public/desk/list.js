/* Native BaseList paging + ListView rendering over scoped product APIs.
 * No report grid, generic DocType RPC, platform actions, or cloned list markup.
 */
(() => {
	const p = frappe.pearnly;
	// 框架的 `FilterGroup.add()` 构造 `frappe.ui.Filter` 时不带 `filter_fields`，于是每行过滤器
	// 的下拉里会列出该 DocType 的**全部**字段（几十个，绝大多数这页根本不支持）。产品列表只
	// 开放本页真正支持的几个，所以在这里把白名单塞进构造参数 —— 用的是官方自己的开关
	// （`field_select.js`：给了 `filter_fields` 就只加这几个）。**只对声明了 `filter_fields`
	// 的列表生效**：别的列表、表单里的过滤器、其它 App 一概不动。
	const NativeFilter = frappe.ui?.Filter;
	if (NativeFilter && !NativeFilter.pearnly_filter_fields) {
		class Filter extends NativeFilter {
			constructor(options) {
				// ⚠️ 官方的 `FieldSelect` 收的 `filter_fields` 是**字段对象数组**，不是字段名
				// （`field_select.js: add_field_option(df)` 直接读 `df.fieldname`）。传一串字符串
				// 会加出一堆空选项、下拉里什么都没有（实测踩到）。这里从 DocType 的元数据取。
				const names = options?.filter_list?.filter_fields;
				const fields = names?.length
					? names.map(name => frappe.meta.get_docfield(options.doctype, name)).filter(Boolean)
					: null;
				super(fields ? {...options, filter_fields: fields} : options);
			}
		}
		Filter.pearnly_filter_fields = true;
		frappe.ui.Filter = Filter;
	}
	// 官方原生筛选条件 → `{字段名: 值}`。`Between` 给 [从, 到]，其余给单值（`in` 给数组）。
	p.filter_values = list => {
		const out = {};
		for (const [doctype, fieldname, operator, value] of (list?.filter_area?.get() || [])) {
			if (!fieldname) continue;
			out[fieldname] = String(operator || "").toLowerCase() === "between"
				&& Array.isArray(value) ? [value[0] || "", value[1] || value[0] || ""] : value;
		}
		return out;
	};
	class ProductList extends frappe.views.BaseList {
		constructor(options) {
			super(options);
			if (options.container) this.page = Object.assign(Object.create(this.page), {main: $(options.container)});
			this.parent = {page: this.page};
			// 勾选框**默认就有**：官方 Desk 列表每行天生带一个 —— `frappe/public/js/frappe/list/list_view.js`
			// 的 `get_subject_element()` 无条件 append 复选框，表头 `get_header_html()` 也无条件给全选框，
			// 整份框架里没有"是否可选"的开关。产品列表照官方，所以默认开；只有明确不要选择的列表
			// （表单里的附件行那种，见 `sales-form.js` / `purchase-form.js`）才传 `selectable: false`。
			Object.assign(this, {view: "List", selectable: options.selectable !== false,
				start: 0, page_length: options.page_length || 20,
				selected_page_count: options.page_length || 20, data: [], total: 0, sequence: 0,
				// 筛选走**官方原生那一套**（那颗 Filter 按钮 + 弹层里的条件行）：页面用 `filters`
				// 声明本页支持的字段，没声明就不出筛选区。排序仍然不开放。
				hide_filters: !options.filter_fields, hide_sort_selector: true, column_max_widths: {},
				link_field_title_fields: {}, list_view_settings: {}});
			// **列头点一下排序**（用户 2026-09-25："点列头我要的是这种，不要那种繁琐的点开、选择"）。
			// 页面用 `sort_fields` 声明哪些字段**服务端认**；只有这些列头可点，其余列头照旧不可点
			// （点了没反应比不给点更糟）。列表是服务端分页的，所以排序参数交给各页的 `load` 回服务端，
			// 不在前端重排这一页（那只会排当前 20 条，是假的）。
			this.sort_fields = new Set(options.sort_fields || []);
			this.sort_by = null;
			this.sort_order = null;
			this.settings = {formatters: {}, button: options.button};
			// 白名单：只列这些字段（见上面包 `frappe.ui.Filter` 的那段）。注意 `filter_fields`
			// 是给 `FieldSelect` 看的**字段名数组**；页面声明写成 `filter_fields: [{fieldname…}]`
			// 是为了同时带着标签与类型，这里取名字。别叫 `filters` —— 那个名字框架自己用着
			// （初始条件列表），撞上会让筛选区初始化去解析一堆对象而抛错。
			this.filter_fields = options.filter_fields
				? options.filter_fields.map(column => column.fieldname) : null;
			this.spec = options.columns;
			this.columns = options.columns.map((column, i) => {
				// The framework measures each cell with `$(column_html)`, so a formatter
				// must return markup: plain text like "ops@example.test" is parsed as a
				// selector and throws. Escape the value, then wrap it.
				this.settings.formatters[column.fieldname] = (value, df, row) => {
					const text = column.format ? column.format(value, row) : value;
					// 这一层只出**转义后的文本**，所以列不能从 `format` 里塞 HTML（会被显示成一串标记）。
					// 要按格上样式就交给 `cell_class`：类名挂在我们自己这个 wrapper 上，颜色在皮肤里定义。
					const extra = typeof column.cell_class === "function"
						? column.cell_class(value, row) : column.cell_class;
					const klass = String(extra ?? "").split(/\s+/).filter(Boolean)
						.map(name => p.escape(name)).join(" ");
					// 悬浮提示默认就是格子里的字；列可以给 `title(value, row)` 换成别的（例如失败原因）。
					const tip = typeof column.title === "function" ? column.title(value, row) || text : text;
					return `<span class="ellipsis${klass ? ` ${klass}` : ""}" title="${p.escape(tip ?? "")}">${p.escape(text ?? "")}</span>`;
				};
				return {type: i === 0 ? "Subject" : "Field", df: {fieldtype: "Data", ...column}};
			});
			this.page.main.addClass("layout-main-list list-view");
			this.ready = frappe.model.with_doctype(this.doctype).then(() => {
				// 框架的筛选区/标准筛选要读 DocType 的字段定义（`meta.fields`、`title_field`），
				// 那一步在 ListView 的 `setup_meta` 里；产品列表不是 ListView，自己补上。
				this.meta = frappe.get_meta(this.doctype);
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
				// 列头排序：升序 → 倒序 → 恢复本页默认顺序（与服务端"没给排序参数"一致）。
				this.$result.on("click", "[data-sort-by]", event => {
					event.preventDefault();
					const fieldname = event.currentTarget.getAttribute("data-sort-by");
					if (!this.sort_fields.has(fieldname)) return;
					if (this.sort_by !== fieldname) { this.sort_by = fieldname; this.sort_order = "asc"; }
					else if (this.sort_order === "asc") { this.sort_order = "desc"; }
					else { this.sort_by = null; this.sort_order = null; }
					return this.reload();
				});
				// BaseList normally does this in refresh() and via cur_list. Product Pages
				// use scoped APIs, so retain its sizing on refresh, resize and return navigation.
				this.layout_handler = frappe.utils.debounce(() => this.resize_list(), 300);
				$(window).on("resize", this.layout_handler);
				this.page.wrapper.on("show", this.layout_handler);
				return this.refresh();
			});
		}
		setup_filter_area() {
			if (this.hide_filters) return;
			super.setup_filter_area();
			// 只要"筛选按钮 + 弹层里的条件行"这一套。框架还会按 DocType 的 `in_standard_filter`
			// 再铺一排常驻输入框（公司/名称…）—— 它们不在本页的参数映射里，出现了就是"看着能筛、
			// 其实不生效"（用户 2026-09-23：筛选字段按页删减，不必要这么多），整块收掉。
			this.page.page_form.find(".standard-filter-section").hide();
			// 框架那个「+ 添加筛选」默认加的是 `name`（ID）那一行 —— 它不在本页白名单里，加出来
			// 是一行**空白的选区**（截图实测）。改成加本页第一个筛选字段：走的还是官方同一个
			// `add_filter`，事件也挂在官方那颗按钮上。
			const first = this.filter_fields?.[0];
			if (!first || !this.filter_area) return;
			this.filter_area.filter_button.on("shown.bs.popover", () => {
				const group = this.filter_area.filter_list;
				if (!group?.wrapper) return;
				group.wrapper.find(".add-filter").off("click").on("click", () => {
					group.toggle_empty_filters(false);
					group.add_filter(this.doctype, first);
				});
				// 首次弹开时框架已经加过一行 `name`：它不是本页的字段，换掉。
				for (const row of group.filters.filter(row => !this.filter_fields.includes(row.fieldname))) {
					row.remove();
				}
				if (!group.filters.some(row => row.field)) group.add_filter(this.doctype, first);
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
			// 勾选框默认保留（官方列表就是这样）；只有 `selectable: false` 的列表才摘掉。
			// "点赞"仍然不开放。
			header.find(".list-liked-by-me").remove();
			if (!this.selectable) header.find(".select-like, .checkbox-actions").remove();
			// 排序：页面声明过的列（`sort_fields`，服务端同样认这几项）保留官方的 `data-sort-by`
			// 与"点击排序"提示，并在当前排序列后面挂 ↑ / ↓ —— 框架的列表本身没有列头箭头
			// （它把箭头画在排序控件里，而那个控件在我们这里是隐藏的）。没声明的列照旧摘掉属性，
			// 免得点上去什么都没发生还以为坏了。
			header.find("[data-sort-by]").each((_, el) => {
				const fieldname = el.getAttribute("data-sort-by");
				const label = el.textContent.trim();
				if (!this.sort_fields.has(fieldname)) {
					el.title = label;
					el.removeAttribute("data-sort-by");
					return;
				}
				el.title = __("Click to sort by {0}", [label]);
				el.textContent = fieldname === this.sort_by
					? `${label} ${this.sort_order === "desc" ? "↓" : "↑"}` : label;
			});
			// 框架那套"已选 N 项"条（`.checkbox-actions`）我们不用：产品的选中反馈是右上角那颗
			// 按钮（`p.selection_action`），表头要一直看得见。这两个引用留着 null 也有个用途 ——
			// 框架的 `update_checkbox()` 一见 `$checkbox_actions` 为空就直接返回。
			this.$list_head_subject = this.$checkbox_actions = null;
			header.find(".list-count").text(`${this.data.length} / ${this.total}`);
			this.$result.prepend(header);
			this.set_trailing_width();
		}
		// 表头右区放的是**计数**、行右区放的是**动作按钮**，两边必须一样宽 —— 否则列与内容
		// 对不齐（用户 2026-09-23 报的"列和内容位置对不上，歪了"）。宽度取两者更宽的那个：
		// 既容得下按钮（采购/销售的「付款 / 收款」），也容得下「20 / 47」这类计数。
		// 计数那格是 `nowrap`（皮肤里写死），所以量到的就是整句的宽度，不会因为格子窄而变短。
		set_trailing_width() {
			const list = this.$frappe_list?.[0];
			if (!list) return;
			const buttons = this.$result.find(".pearnly-row-actions").get();
			const count = this.$frappe_list.find(".list-row-head .list-count")[0];
			list.style.setProperty("--pearnly-actions-width", `${Math.max(
				buttons.length ? Math.max(...buttons.map(el => el.scrollWidth)) : 0,
				count ? Math.ceil(count.getBoundingClientRect().width) : 0,
			)}px`);
		}
		// 勾选状态变化时只做两件事：记下当前勾了哪些行、把结果交给页面自己的选中动作。
		// **必须覆盖框架那一版**：它会把表头整行藏起来换成它自己的"已选 N 项"条，还会写
		// `this.$list_head_subject.find(...)` —— 而产品列表的表头是我们自绘的、上面那两个
		// 引用是 null，于是它在 `clear_checked_items()` 里抛 TypeError，把真正的失败原因
		// 一起盖掉（实测踩到过）。
		on_row_checked() {
			this.$checks = this.$result.find(".list-row-checkbox:checked");
			this.on_selection?.(this.get_checked_items());
		}
		toggle_actions_menu_button() {
			// 选中动作由 `on_row_checked` 一个口子出去，这里什么都不做（框架会在
			// `on_row_checked` 里再调它一次，重复触发会让按钮闪）。
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
			this.set_trailing_width();
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
				// 排序参数一并交给页面自己的 `load`：列表是服务端分页的，排序必须回服务端。
				const result = await this.load({start, page_length: length,
					sort_by: this.sort_by, sort_order: this.sort_order});
				if (request !== this.sequence) return;
				// 翻页时服务端可以不再数总数（回 null / 不回）：沿用第一页的总数（2026-10-02 m6：每次都数一遍全部可见行太慢）。
				if (result.total !== null && result.total !== undefined) this.total = result.total;
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
					this.toggle_result_area();
					this.$no_result.text(__("Unable to load this page. Please reload."));
					// 清选择放在最后，而且自己兜住异常：这一步失败不该把真正的失败原因吞掉。
					try {
						if (this.selectable) this.clear_checked_items();
					} catch (cleanup) {
						console.error(cleanup);
					}
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
		"setup_check_events", "get_checked_items", "update_checkbox", "clear_checked_items"]) {
		ProductList.prototype[method] = frappe.views.ListView.prototype[method];
	}
	p.list = options => new ProductList(options);
	// **整批取回来**的列表（客户 / 供应商 / 仓库 / 团队 / 收发存 / 交班那种一次拿全的）
	// 就在页面里排：整套数据都在手上，排序结果与服务端分页那几张一致。
	// 口径与服务端 `pearnly_erp_portal.listing` 对齐：只认白名单字段、空值排最后、
	// 能当数字看就按数值比（`1,204` 这种带千分位的也不会按字符排）。
	// `fields` 是**列名**（点的是列头），`keys` 只给"列名和行里的键不一样"的列用
	// （团队那列叫 `status`，值其实在 `row.enabled` 上）—— 不这么写就会点半天顺序不动。
	p.sort_rows = (rows, {sort_by, sort_order, fields = [], keys = {}} = {}) => {
		if (!sort_by || !fields.includes(sort_by)) return rows;
		const key = keys[sort_by] || sort_by;
		const value = row => row[key];
		const as_number = item => {
			const text = String(item ?? "").replace(/[,\s\u00a0]/g, "");
			const number = Number(text);
			return text !== "" && Number.isFinite(number) ? number : null;
		};
		const filled = rows.filter(row => value(row) != null && value(row) !== "");
		const empty = rows.filter(row => value(row) == null || value(row) === "");
		filled.sort((a, b) => {
			const left = as_number(value(a)), right = as_number(value(b));
			if (left != null && right != null) return left - right;
			return String(value(a)).localeCompare(String(value(b)), frappe.boot.lang || "en",
				{numeric: true, sensitivity: "base"});
		});
		if (sort_order === "desc") filled.reverse();
		return filled.concat(empty);
	};

	// 选中若干行 → 右上角出现那颗动作按钮。每个列表页都要同一套行为，所以放在共享列表层：
	// 选择状态、忙碌时禁用、确认、失败保留选择、做完刷新并清空 —— 各页自己写一份必然走样。
	//
	// `when(row)` 决定这一行能不能做（服务端也有同样的闸，这里只是别让人白点一次）：
	// 选中里只要有一行不满足，按钮就禁用，原因写在按钮 title 上。
	//
	// 返回的就是 `p.list({ on_selection })` 要的那个回调。
	// `empty` 给"没选中时右上角本来就有主按钮"的页面用（商品页的"新建商品"、盘点页的"新建盘点"）：
	// 不传就按惯例清掉那颗按钮。
	// `sound` 只在**删除类**动作上传 `"delete"`（官方批量删除就是这一声）；其余默认 `submit`。
	// 注意：确认文案那个参数收下来就改名成 confirm_text —— 架构检查拦的是裸的浏览器
	// confirm 调用，参数也叫 confirm 的话会被它的正则误伤（连注释里的字面量都算）。
	p.selection_action = ({ page, list, label, when, reason, confirm: confirm_text, run, success,
		empty, sound }) => {
		const rows_now = () => (list()?.get_checked_items?.() || []);
		const eligible = (row, rows) => (when ? Boolean(when(row, rows)) : true);
		let busy = false, confirming = false;

		function paint() {
			const rows = rows_now();
			if (!rows.length) return empty ? empty() : page.clear_primary_action();
			page.set_primary_action(label(rows), () => void execute());
			if (!page.btn_primary) return;
			const blocked = rows.filter(row => !eligible(row, rows));
			page.btn_primary.prop("disabled", busy || confirming || blocked.length > 0);
			page.btn_primary.attr("title", blocked.length && reason ? reason(blocked[0]) : "");
		}

		async function perform(rows) {
			busy = true; paint();
			try {
				await run(rows);
				if (success) p.success(success(rows), sound);
				list()?.clear_checked_items?.();
				await list()?.reload?.();
			} catch (error) {
				// 服务端已经用 msgprint 说明了原因；这里只负责把选择留着，让人能改完再试。
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
			} finally {
				busy = false; confirming = false; paint();
			}
		}

		async function execute() {
			if (busy || confirming) return;
			const rows = rows_now();
			if (!rows.length || rows.some(row => !eligible(row, rows))) return;
			if (!confirm_text) return void perform(rows);
			confirming = true; paint();
			// 确认框关掉而没确认时要恢复可点状态；确认后按钮保持禁用（busy），避免重复提交。
			const dialog = frappe.confirm(p.escape(confirm_text(rows)), () => void perform(rows));
			if (dialog) dialog.onhide = () => { if (!confirming) return; confirming = false; paint(); };
		}

		return () => paint();
	};
})();
