/* 收银 POS：界面照 ERPNext 的收银台**1:1** 复刻（结构、尺寸、交互都照它），业务仍走我们
 * 自己的商品 / 库存 / 收发存 / 销售域（本仓 2026-09-21 的决定＋用户 2026-09-23 的口径）。
 *
 * 对着官方那份实现（`apps/erpnext/erpnext/selling/page/point_of_sale/*.js` 与
 * `public/scss/point-of-sale.scss`，离线快照见工作记录 F22）逐块摆：
 *
 * · 一屏 = `.point-of-sale-app`（10 列网格、gap 15px、padding 1%）里六块，按 DOM 顺序自动排：
 *   商品区(`.items-selector`，6 列) / 商品明细(`.item-details-container`，6 列) /
 *   购物车(`.customer-cart-container`，4 列) / 收款(`.payment-container`，6 列) /
 *   最近订单(`.past-order-list`，4 列) / 订单汇总(`.past-order-summary`，6 列)。
 *   **哪两块显示就决定左右**：点收款时购物车留在最左边、收款面板占右边六列；开最近订单时
 *   订单列表在左四列、汇总在右六列 —— 这是网格自动排列的结果，不是我们挪的。
 * · 商品卡：库存色点（>10 绿 / ≤0 红 / 其余橙）＋图片或缩写＋名称＋`售价 / 单位`；4 列网格。
 * · 点购物车某一行 → 左半边换成「商品明细」面板（数量/单位/单价/折扣/仓库/数量（仓库）/标价），
 *   右半边**这时才出现**数字键盘（`1 2 3 数量 / 4 5 6 折扣 / 7 8 9 单价 / . 0 删除 移除`）。
 * · 键盘先选目标（数量/折扣/单价，选中的那颗高亮），再按数字；没选目标就按数字会提示。
 * · 「结账」→ 收款面板：左边各支付方式（点一下才展开金额格、键盘写它）、右边 3 列键盘，
 *   下面 总计 / 付款金额 / 剩余金额（多付变「找零」），最底一颗「完成订单」。
 * · 收款完成：藏起收银台，右半边显示这张小票的汇总（官方 `submit_invoice` 同一动作）。
 * · 页面动作：主按钮「新单」、次按钮「最近订单 / 隐藏最近订单」、菜单里「交班」。
 *
 * 我们**关掉**的官方功能（产品没有对应数据，位置留着不画）：
 * 客户选择、物料分组筛选、单位换算系数、行号/批次、忠诚度积分、发票附加信息、
 * 打印/邮件小票、Open Form View（产品不开放 DocType 表单页）。
 *
 * 数据全都走我们自己的接口：商品/售价/结存 `cashier/counter.py`，落单
 * `cashier.sales.create_sale`（一笔收银 = 一张单 + 一次出库 + 一条收发存），
 * 开班/交班 `cashier/shifts.py`（对账表按支付方式，见 `ERP Shift Balance`）。
 */
(() => {
	const p = frappe.pearnly;
	// 支付方式：与 `ERP Sale Payment.method` 同一个集合，顺序照官方收款面板。
	const METHODS = ["cash", "card", "promptpay", "transfer"];
	const METHOD_KEYS = {cash: "trade.methodCash", card: "trade.methodCard",
		promptpay: "trade.methodPromptpay", transfer: "trade.methodTransfer"};
	// 官方购物车键盘（`pos_item_cart.js` 的 keys / css_classes 原样）：目标键各占两格，
	// 「移除」再挂 `.remove-btn`（红字），底部另有一颗占满行的「结账」。
	const CART_PAD = [
		[{v: "1"}, {v: "2"}, {v: "3"}, {v: "qty", key: "trade.qty", span: 2, hint: "⌘+Q"}],
		[{v: "4"}, {v: "5"}, {v: "6"}, {v: "discount", key: "trade.discount", span: 2, hint: "⌘+D"}],
		[{v: "7"}, {v: "8"}, {v: "9"}, {v: "unit_price", key: "trade.price", span: 2, hint: "⌘+R"}],
		[{v: "."}, {v: "0"}, {v: "delete", en: "Delete"},
			{v: "remove", key: "trade.remove", span: 2, cls: "remove-btn"}],
	];
	// 官方收款面板键盘（`pos_payment.js`）：3 列，最后一行 `+/- 0 删除`。
	const PAY_PAD = [
		[{v: "1"}, {v: "2"}, {v: "3"}],
		[{v: "4"}, {v: "5"}, {v: "6"}],
		[{v: "7"}, {v: "8"}, {v: "9"}],
		[{v: "sign", label: "+/-"}, {v: "0"}, {v: "delete", en: "Delete"}],
	];

	p.cashier = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.";
		const can_write = frappe.model.can_write("ERP Sale");
		// 钱一律这么显示（两位小数、带币种）；读数时把千分位剥掉再转数字。
		const money = value => p.money(value);
		const num = value => {
			const parsed = Number(String(value ?? "").replace(/,/g, ""));
			return isFinite(parsed) ? parsed : 0;
		};
		const round2 = value => Math.round(num(value) * 100) / 100;
		const kind_label = value => t(`trade.kind.${value}`);
		const state_label = value => t(`trade.state.${value}`);
		const method_label = method => t(METHOD_KEYS[method] || method);
		const state_pill = value => value === "completed" ? "green" : value === "void" ? "gray" : "orange";
		// 官方那条色点规则：>10 绿、≤0 红、其余橙（`pos_item_selector.js`）。
		const pill_class = qty => (qty > 10 ? "green" : qty <= 0 ? "red" : "orange");
		const clip = (text, count) => {
			const value = String(text ?? "");
			return value.length > count ? value.slice(0, count) + "…" : value;
		};

		let shift = null, items = [], items_total = 0, cart = [], selected = null,
			pad_target = null, pad_value = "", paying = false, pay_target = null,
			orders = [], active_order = null, orders_shown = false, view = "counter",
			detail_controls = {};

		// ── 骨架（类名与层级照官方那六块）────────────────────────────────────────
		function pad_html(keys) {
			return `<div class="numpad-container">` + keys.map(row => row.map(cell => {
				const label = cell.key ? t(cell.key) : cell.en ? __(cell.en) : cell.label || cell.v;
				const cls = ["numpad-btn", cell.span ? "col-span-2" : "", cell.cls || ""]
					.filter(Boolean).join(" ");
				return `<div class="${cls}" data-button-value="${p.escape(cell.v)}"${
					cell.hint ? ` title="${cell.hint}"` : ""}>${p.escape(label)}</div>`;
			}).join("")).join("") + `</div>`;
		}

		p.clear_page(page);
		page.main.append(`<div class="point-of-sale-app">
			<section class="items-selector">
				<div class="filter-section">
					<div class="label">${p.escape(t("trade.allItems"))}</div>
					<div class="search-field"></div>
					<div class="item-group-field"></div>
				</div>
				<div class="items-container show-item-image"></div>
			</section>
			<section class="item-details-container">
				<div class="item-details-header">
					<div class="label">${p.escape(t("trade.itemDetails"))}</div>
					<div class="close-btn" title="Esc">
						<svg width="32" height="32" viewBox="0 0 14 14" fill="none">
							<path d="M4.93764 4.93759L7.00003 6.99998M9.06243 9.06238L7.00003 6.99998M7.00003 6.99998L4.93764 9.06238L9.06243 4.93759" stroke="#8D99A6"/>
						</svg>
					</div>
				</div>
				<div class="item-display">
					<div class="item-name-desc-price">
						<div class="item-name"></div><div class="item-desc"></div><div class="item-price"></div>
					</div>
					<div class="item-image"></div>
				</div>
				<div class="discount-section"></div>
				<div class="form-container"></div>
			</section>
			<section class="customer-cart-container">
				<div class="cart-container">
					<div class="abs-cart-container">
						<div class="cart-label">${p.escape(t("trade.cart"))}</div>
						<div class="cart-header">
							<div class="name-header">${p.escape(t("trade.product"))}</div>
							<div class="qty-header">${p.escape(t("trade.qty"))}</div>
							<div class="rate-amount-header">${p.escape(t("trade.amount"))}</div>
						</div>
						<div class="cart-items-section"></div>
						<div class="cart-totals-section">
							<div class="item-qty-total-container">
								<div class="item-qty-total-label">${p.escape(t("trade.totalItems"))}</div>
								<div class="item-qty-total-value">0.00</div>
							</div>
							<div class="net-total-container">
								<div class="net-total-label">${p.escape(t("trade.net"))}</div>
								<div class="net-total-value">0.00</div>
							</div>
							<div class="grand-total-container">
								<div>${p.escape(t("trade.total"))}</div><div>0.00</div>
							</div>
							<div class="checkout-btn">${p.escape(t("trade.checkout"))}</div>
							<div class="edit-cart-btn">${p.escape(t("trade.editCart"))}</div>
						</div>
						<div class="numpad-section">
							<div class="numpad-totals">
								<span class="numpad-item-qty-total"></span>
								<span class="numpad-net-total"></span>
								<span class="numpad-grand-total"></span>
							</div>
							${pad_html(CART_PAD)}
							<div class="numpad-btn checkout-btn" data-button-value="checkout">${p.escape(t("trade.checkout"))}</div>
						</div>
					</div>
				</div>
			</section>
			<section class="payment-container">
				<div class="payment-split-container">
					<div class="payment-container-left">
						<div class="section-label payment-section">${p.escape(t("trade.method"))}</div>
						<div class="payment-modes"></div>
					</div>
					<div class="payment-container-right">
						<div class="fields-numpad-container">
							<div class="fields-section"><div class="invoice-fields"></div></div>
							<div class="number-pad">${pad_html(PAY_PAD)}</div>
						</div>
					</div>
				</div>
				<div class="totals-section"><div class="totals"></div></div>
				<div class="submit-order-btn">${p.escape(t("trade.completeOrder"))}</div>
			</section>
			<section class="past-order-list">
				<div class="filter-section">
					<div class="label">${p.escape(t("trade.recentOrders"))}</div>
					<div class="status-search-fields">
						<div class="status-field"></div>
						<div class="search-field"></div>
					</div>
				</div>
				<div class="list-container"></div>
			</section>
			<section class="past-order-summary">
				<div class="no-summary-placeholder">${p.escape(t("trade.selectOrderHint"))}</div>
				<div class="invoice-summary-wrapper">
					<div class="abs-container">
						<div class="upper-section"></div>
						<div class="label">${p.escape(t("trade.lines"))}</div>
						<div class="items-container summary-container order-summary-container"></div>
						<div class="label">${p.escape(t("trade.total"))}</div>
						<div class="totals-container summary-container"></div>
						<div class="label">${p.escape(t("trade.byMethod"))}</div>
						<div class="payments-container summary-container"></div>
						<div class="summary-btns"></div>
					</div>
				</div>
			</section>
		</div>`);
		const $app = page.main.find(".point-of-sale-app");
		const q = selector => $app.find(selector);

		// 商品区顶上那两颗控件是官方原生控件（Data + placeholder 与 Clear 那颗小叉）。
		const search_control = frappe.ui.form.make_control({
			df: { fieldtype: "Data", placeholder: t("trade.searchProducts") },
			parent: $app.find(".items-selector .search-field"), render_input: true,
		});
		search_control.$wrapper.find(".control-input").append(
			`<span class="link-btn"><a class="btn-open no-decoration" title="${__("Clear")}">${
				frappe.utils.icon("close", "sm")}</a></span>`);
		search_control.$wrapper.on("click", ".link-btn a", () => {
			search_control.set_value(""); void load_items(); search_control.set_focus();
		});
		search_control.$input.on("change", () => void load_items());
		const order_status_control = frappe.ui.form.make_control({
			df: { fieldtype: "Select", options: ["", "completed", "void", "refund"],
				label: t("trade.invoiceStatus") },
			parent: $app.find(".past-order-list .status-field"), render_input: true,
		});
		order_status_control.$input.on("change", () => paint_orders());
		const order_search_control = frappe.ui.form.make_control({
			df: { fieldtype: "Data", placeholder: t("trade.searchOrders") },
			parent: $app.find(".past-order-list .search-field"), render_input: true,
		});
		order_search_control.$input.on("change keyup", () => paint_orders());

		// ── 页面动作（照官方：主「新单」、次「最近订单」、菜单「交班」）───────────────
		function paint_actions() {
			if (!can_write) { page.clear_primary_action(); return; }
			page.clear_menu();
			page.add_menu_item(t("trade.closeShift"), () => void close_shift_dialog(), false);
			if (!shift) {
				page.set_indicator(t("trade.noShift"), "orange");
				page.set_primary_action(t("trade.openShift"), () => void open_shift_dialog());
				page.clear_secondary_action?.();
				return;
			}
			page.set_primary_action(t("trade.newInvoice"), () => void new_invoice());
			page.set_secondary_action(orders_shown ? t("trade.hideRecentOrders") : t("trade.recentOrders"),
				() => void toggle_orders());
			page.set_indicator(t("trade.shiftOpenedAt", shift.shift.shift_seq,
				p.datetime_text(shift.shift.opened_at), p.money(shift.expected_cash)),
				"green");
		}

		// 「新单」= 这一单清干净重新开始（官方 primary_action "New Invoice"）。
		async function new_invoice() {
			cart = []; selected = null; pad_target = null; pad_value = "";
			paying = false; pay_target = null; view = "counter";
			if (orders_shown) { orders_shown = false; }
			await paint();
		}

		// 「最近订单」= 订单列表与汇总那一屏（官方 secondary_action "Recent Orders"）。
		async function toggle_orders() {
			orders_shown = !orders_shown;
			view = orders_shown ? "orders" : "counter";
			if (orders_shown) { active_order = null; await load_orders(); }
			paint_actions(); paint_views();
		}

		// ── 视图切换：显示哪两块，官方就是哪两块（网格自己排左右）──────────────────
		function paint_views() {
			// 关键：收款时**购物车留在最左边**，收款面板占右边六列 —— 官方的网格就是
			// 这个排法（DOM 里购物车在收款面板之前），所以这里只切显示、不挪位置。
			const counter = view === "counter";
			q(".items-selector").toggle(counter && !paying && !selected);
			q(".item-details-container").css("display",
				counter && !paying && selected ? "flex" : "none");
			q(".customer-cart-container").toggle(counter);
			q(".payment-container").css("display", paying ? "flex" : "none");
			q(".past-order-list").css("display", orders_shown ? "flex" : "none");
			q(".past-order-summary").css("display",
				view === "summary" || orders_shown ? "flex" : "none");
			q(".cart-totals-section .checkout-btn").toggle(!paying && cart.length > 0 && !selected);
			q(".cart-totals-section .edit-cart-btn").toggle(paying);
		}

		// ── 商品区 ──────────────────────────────────────────────────────────────
		async function load_items() {
			if (!shift) return;
			const result = await p.call(api + "api_counter_products", {
				terminal: shift.shift.terminal, search: search_control.get_value() || undefined,
				start: 0, limit: 40 });
			items = result?.items || [];
			items_total = num(result?.total);
			paint_items();
		}

		function item_html(row) {
			const qty = num(row.qty_on_hand);
			const name = row.product_name || row.name;
			const image = row.image_url
				? `<div class="item-qty-pill"><span class="indicator-pill whitespace-nowrap ${pill_class(qty)}">${p.escape(p.number(qty))}</span></div>
					<div class="item-display"><img class="item-img" src="${p.escape(row.image_url)}"
						alt="${p.escape(name)}" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className: 'item-display abbr', textContent: this.alt}))"></div>`
				: `<div class="item-qty-pill"><span class="indicator-pill whitespace-nowrap ${pill_class(qty)}">${p.escape(p.number(qty))}</span></div>
					<div class="item-display abbr">${p.escape(frappe.get_abbr(name))}</div>`;
			return `<div class="item-wrapper" data-product="${p.escape(row.name)}" title="${p.escape(name)}">
				${image}
				<div class="item-detail">
					<div class="item-name">${p.escape(clip(name, 18))}</div>
					<div class="item-rate">${p.escape(money(row.unit_price))} / ${p.escape(row.unit || "")}</div>
				</div>
			</div>`;
		}

		function paint_items() {
			const $container = q(".items-container");
			if (!items.length) {
				$container.removeClass("show-item-image").addClass("items-not-found")
					.text(t("trade.itemsNotFound"));
				return;
			}
			$container.removeClass("items-not-found").addClass("show-item-image")
				.html(items.map(item_html).join(""));
		}

		// ── 购物车 ──────────────────────────────────────────────────────────────
		const line_total = row => round2(num(row.qty) * num(row.unit_price) - num(row.discount));
		const cart_qty = () => round2(cart.reduce((sum, row) => sum + num(row.qty), 0));
		const cart_net = () => round2(cart.reduce((sum, row) => sum + line_total(row), 0));

		function add_to_cart(row, qty = 1) {
			if (!row?.name) return;
			const line = cart.find(item => item.product === row.name);
			if (line) line.qty = round2(num(line.qty) + qty);
			else cart.push({
				product: row.name, product_name: row.product_name, description: row.product_name,
				code: row.code, unit: row.unit, warehouse: row.warehouse, location: row.location || "",
				unit_price: num(row.unit_price), list_price: num(row.unit_price),
				qty, discount: 0, qty_on_hand: num(row.qty_on_hand),
			});
			selected = null;
			paint_cart(); paint_views();
		}

		function cart_line_html(row) {
			const abbr = row.image_url
				? `<div class="item-image"><img src="${p.escape(row.image_url)}" alt=""></div>`
				: `<div class="item-image item-abbr">${p.escape(frappe.get_abbr(row.product_name || ""))}</div>`;
			return `<div class="cart-item-wrapper" data-product="${p.escape(row.product)}">
				${abbr}
				<div class="item-name-desc"><div class="item-name">${p.escape(row.product_name || "")}</div></div>
				<div class="item-qty-rate">
					<div class="item-qty"><span>${p.escape(p.number(row.qty))} ${p.escape(row.unit || "")}</span></div>
					<div class="item-rate-amount">
						<div class="item-rate">${p.escape(money(row.unit_price))}</div>
						<div class="item-amount">${p.escape(money(line_total(row)))}</div>
					</div>
				</div>
			</div>`;
		}

		function paint_cart() {
			const $items = q(".cart-items-section");
			const has = cart.length > 0;
			q(".cart-header").css("display", has ? "flex" : "none");
			$items.html(has
				? cart.map(cart_line_html).join("")
				: `<div class="no-item-wrapper">${p.escape(t("trade.cartEmpty"))}</div>`);
			if (has) {
				for (const row of cart) {
					if (row.product !== selected) continue;
					$items.find(`.cart-item-wrapper[data-product="${row.product}"]`)
						.css("background-color", "var(--control-bg)");
				}
			}
			// 金额列宽度对齐（官方 `set_columns_width` 同一做法：列头与行取同一个宽度）。
			const max = Math.max(0, ...Array.from(q(".cart-items-section .item-rate-amount"))
				.map(el => el.scrollWidth));
			q(".rate-amount-header").css("width", max ? `${max}px` : "");
			q(".cart-items-section .item-rate-amount").css("width", max ? `${max}px` : "");
			const net = cart_net();
			q(".cart-totals-section .item-qty-total-value").text(p.number(cart_qty()));
			q(".cart-totals-section .net-total-value").text(money(net));
			q(".cart-totals-section .grand-total-container > div:last-child").text(money(net));
			q(".numpad-totals .numpad-item-qty-total").html(`${p.escape(t("trade.totalItems"))}: <span>${p.escape(p.number(cart_qty()))}</span>`);
			q(".numpad-totals .numpad-net-total").html(`${p.escape(t("trade.net"))}: <span>${p.escape(money(net))}</span>`);
			q(".numpad-totals .numpad-grand-total").html(`${p.escape(t("trade.total"))}: <span>${p.escape(money(net))}</span>`);
			q(".cart-totals-section .checkout-btn").toggle(has && !selected);
			q(".numpad-section").css("display", has && selected ? "flex" : "none");
			q(".cart-totals-section").css("display", has && selected ? "none" : "flex");
		}

		// ── 商品明细面板（左半边，官方那一组字段）────────────────────────────────
		function details_fields() {
			return [
				{ fieldname: "qty", label: t("trade.qty"), fieldtype: "Float" },
				{ fieldname: "unit", label: t("trade.unit"), fieldtype: "Data", read_only: 1 },
				{ fieldname: "unit_price", label: t("trade.price"), fieldtype: "Currency",
					options: "currency", formatter: money },
				{ fieldname: "discount", label: t("trade.discount"), fieldtype: "Currency",
					options: "currency", formatter: money },
				{ fieldname: "warehouse", label: t("trade.warehouse"), fieldtype: "Data", read_only: 1 },
				{ fieldname: "qty_on_hand", label: t("trade.actualQty"), fieldtype: "Float", read_only: 1 },
				{ fieldname: "list_price", label: t("trade.listPrice"), fieldtype: "Currency",
					options: "currency", formatter: money, read_only: 1 },
			];
		}

		function paint_details() {
			const row = cart.find(item => item.product === selected);
			if (!row) return;
			q(".item-details-container .item-name").text(row.product_name || "");
			q(".item-details-container .item-desc").text(row.description || "");
			q(".item-details-container .item-price").text(money(row.unit_price));
			q(".item-details-container .item-image").html(row.image_url
				? `<img src="${p.escape(row.image_url)}" alt="">`
				: `<div class="item-abbr">${p.escape(frappe.get_abbr(row.product_name || ""))}</div>`);
			q(".item-details-container .discount-section").html(num(row.discount)
				? `<div class="item-rate">${p.escape(money(row.list_price))}</div>
					<div class="item-discount">−${p.escape(money(row.discount))}</div>`
				: "");
			const $form = q(".item-details-container .form-container").empty();
			detail_controls = {};
			for (const df of details_fields()) {
				$form.append(`<div class="${df.fieldname}-control" data-fieldname="${df.fieldname}"></div>`);
				const control = frappe.ui.form.make_control({
					df: { ...df, onchange: () => apply_field(df.fieldname, control.get_value()) },
					parent: $form.find(`.${df.fieldname}-control`), render_input: true,
				});
				control.set_value(row[df.fieldname]);
				detail_controls[df.fieldname] = control;
			}
		}

		function apply_field(fieldname, value) {
			const row = cart.find(item => item.product === selected);
			if (!row) return;
			if (fieldname === "qty") row.qty = Math.max(0, round2(value));
			else if (fieldname === "discount") row.discount = Math.max(0, round2(value));
			else if (fieldname === "unit_price") row.unit_price = Math.max(0, round2(value));
			paint_cart();
			q(".item-details-container .item-price").text(money(row.unit_price));
			q(".discount-section").html(num(row.discount)
				? `<div class="item-rate">${p.escape(money(row.list_price))}</div>
					<div class="item-discount">−${p.escape(money(row.discount))}</div>`
				: "");
		}

		function select_line(product) {
			selected = selected === product ? null : product;
			pad_target = null; pad_value = "";
			q(".numpad-container .numpad-btn").removeClass("highlighted-numpad-btn");
			if (selected) paint_details();
			paint_cart(); paint_views();
			if (!selected) return;
			// 选中行滚到看得见的位置（官方 `cart_item_clicked` 里同一手）。
			const $row = q(`.cart-item-wrapper[data-product="${selected}"]`);
			const $wrapper = q(".cart-items-section");
			if ($row.length && $wrapper.length) {
				$wrapper.animate({ scrollTop: $row.offset().top - $wrapper.offset().top }, 150);
			}
		}

		// ── 购物车键盘（官方 `on_numpad_event` 一套：先选目标，再按数字）────────────
		function cart_numpad(value) {
			if (value === "checkout") { void checkout(); return; }
			if (value === "remove") {
				cart = cart.filter(item => item.product !== selected);
				selected = null; pad_target = null; pad_value = "";
				paint_cart(); paint_views();
				return;
			}
			if (["qty", "discount", "unit_price"].includes(value)) {
				pad_target = pad_target === value ? null : value;
				pad_value = "";
				q(".numpad-container .numpad-btn").removeClass("highlighted-numpad-btn");
				if (pad_target) q(`.numpad-section .numpad-btn[data-button-value="${pad_target}"]`)
					.addClass("highlighted-numpad-btn");
				return;
			}
			if (!pad_target) {
				frappe.show_alert({ message: t("trade.pickPadTarget"), indicator: "red" });
				frappe.utils.play_sound("error");
				return;
			}
			if (value === "delete") pad_value = pad_value.slice(0, -1);
			else if (value === ".") { if (!pad_value.includes(".")) pad_value = `${pad_value || "0"}.`; }
			else pad_value = `${pad_value}${value}`.replace(/^0(?=\d)/, "");
			const amount = pad_value === "" || pad_value === "." ? 0 : round2(pad_value);
			paint_details_field(pad_target, amount);
			apply_field(pad_target, amount);
			frappe.utils.play_sound("numpad-touch");
		}

		// 键盘改的是明细面板里那一格：改完要把那一格上的值同步回去（不然面板还显示旧数）。
		function paint_details_field(fieldname, value) {
			detail_controls[fieldname]?.set_value(value);
		}

		// ── 收款（官方那套：支付方式 + 3 列键盘 + 总计/付款金额/剩余金额 + 完成订单）──
		function payments_now() {
			const map = {};
			for (const method of METHODS) {
				const control = q(`.payment-modes .mode-of-payment[data-mode="${method}"]`).data("pearnly_control");
				map[method] = round2(control?.get_value() || 0);
			}
			return map;
		}

		function paint_payment() {
			const total = cart_net();
			const amounts = payments_now();
			const paid = round2(METHODS.reduce((sum, method) => sum + num(amounts[method]), 0));
			const $modes = q(".payment-modes").empty();
			for (const method of METHODS) {
				$modes.append(`<div class="payment-mode-wrapper">
					<div class="mode-of-payment" data-mode="${method}" data-payment-type="${method}">
						${p.escape(method_label(method))}
						<div class="${method}-amount pay-amount">${p.escape(amounts[method] ? money(amounts[method]) : "")}</div>
						<div class="${method} mode-of-payment-control"></div>
					</div>
				</div>`);
			}
			for (const method of METHODS) {
				const control = frappe.ui.form.make_control({
					df: { fieldtype: "Currency", options: "currency", formatter: money,
						label: method_label(method), placeholder: t("trade.enterAmount", method_label(method)),
						onchange: () => { paint_payment_totals(); paint_payment_amounts(); } },
					parent: $modes.find(`.${method}.mode-of-payment-control`), render_input: true,
				});
				control.toggle_label(false);
				control.set_value(amounts[method] || 0);
				// 控件挂两处：金额格自己（键盘写它）与方式那一行（读总额时按方式取）。
				$modes.find(`.${method}.mode-of-payment-control`).data("pearnly_control", control);
				$modes.find(`.mode-of-payment[data-mode="${method}"]`).data("pearnly_control", control);
			}
			// 只有被点中的那个方式展开金额格（官方 `on_payment_mode_click` 同一手）。
			if (!pay_target) pay_target = METHODS[0];
			show_pay_mode(pay_target);
			paint_payment_amounts();
			paint_payment_totals();
		}

		function show_pay_mode(method) {
			q(".payment-modes .mode-of-payment").removeClass("border-primary")
				.find(".mode-of-payment-control").css("display", "none");
			const $mode = q(`.payment-modes .mode-of-payment[data-mode="${method}"]`);
			$mode.addClass("border-primary").find(".mode-of-payment-control").css("display", "flex");
		}

		function paint_payment_amounts() {
			for (const method of METHODS) {
				const control = q(`.payment-modes .${method}.mode-of-payment-control`).data("pearnly_control");
				const amount = round2(control?.get_value() || 0);
				q(`.payment-modes .${method}-amount`).text(amount ? money(amount) : "");
			}
		}

		function paint_payment_totals() {
			const total = cart_net();
			const amounts = payments_now();
			const paid = round2(METHODS.reduce((sum, method) => sum + num(amounts[method]), 0));
			const remaining = round2(total - paid);
			const over = paid > total;
			q(".payment-container .totals").html(`
				<div class="col"><div class="total-label">${p.escape(t("trade.total"))}</div>
					<div class="value">${p.escape(money(total))}</div></div>
				<div class="seperator-y"></div>
				<div class="col"><div class="total-label">${p.escape(t("trade.paid"))}</div>
					<div class="value">${p.escape(money(paid))}</div></div>
				<div class="seperator-y"></div>
				<div class="col"><div class="total-label">${p.escape(over ? t("trade.change") : t("trade.remaining"))}</div>
					<div class="value ${over ? "text-success" : remaining > 0 ? "text-danger" : ""}">${
						p.escape(money(over ? Math.abs(remaining) : remaining))}</div></div>`);
			q(".submit-order-btn").toggleClass("disabled", remaining > 0 || paid <= 0);
		}

		async function checkout() {
			if (!cart.length) return;
			paying = true;
			view = "counter";
			pay_target = METHODS[0];
			paint_views();
			// 现金那一格默认填满应收（官方也是第一个方式带出总额）。
			paint_payment();
			const total = cart_net();
			const cash = q(`.payment-modes .${METHODS[0]}.mode-of-payment-control`).data("pearnly_control");
			if (cash && !num(cash.get_value())) cash.set_value(total);
			paint_payment_amounts(); paint_payment_totals();
		}

		function pay_numpad(value) {
			const method = pay_target || METHODS[0];
			const control = q(`.payment-modes .${method}.mode-of-payment-control`).data("pearnly_control");
			if (!control) return;
			let buffer = control.pearnly_buffer || "";
			if (value === "delete") buffer = String(buffer).slice(0, -1);
			else if (value === "sign") buffer = String(buffer).startsWith("-") ? String(buffer).slice(1) : `-${buffer}`;
			else if (value === ".") { if (!String(buffer).includes(".")) buffer = `${buffer || "0"}.`; }
			else buffer = `${buffer}${value}`.replace(/^0(?=\d)/, "");
			control.pearnly_buffer = buffer;
			control.set_value(buffer === "" || buffer === "." || buffer === "-" ? 0 : round2(buffer));
			paint_payment_amounts(); paint_payment_totals();
			frappe.utils.play_sound("numpad-touch");
		}

		async function submit_order() {
			const total = cart_net();
			const amounts = payments_now();
			const payments = METHODS
				.filter(method => num(amounts[method]) > 0)
				.map(method => ({ method, amount: num(amounts[method]) }));
			const paid = round2(payments.reduce((sum, row) => sum + row.amount, 0));
			if (paid < total) {
				frappe.show_alert({ message: t("trade.underpaid"), indicator: "red" });
				frappe.utils.play_sound("error");
				return;
			}
			const request = crypto.randomUUID();
			const result = await p.call(api + "api_create_sale", {
				shift: shift.shift.name, terminal: shift.shift.terminal, has_vat: 0,
				lines: cart.map(row => ({ product: row.product, qty: num(row.qty),
					unit_price: num(row.unit_price), discount: num(row.discount),
					unit: row.unit, description: row.description,
					warehouse: row.warehouse, location: row.location })),
				payments, client_uuid: request,
			}, "POST");
			cart = []; selected = null; pad_target = null; pad_value = "";
			paying = false; pay_target = null;
			p.success(t("trade.paidNotice", result.receipt_no || result.name,
				p.money(result.change_amount)));
			await refresh();
			// 收款完成：藏起收银台，右半边显示这张小票的汇总（官方 `submit_invoice` 同一手）。
			orders_shown = false;
			view = "summary";
			await show_summary(result.name);
			await load_orders();
			paint_views();
		}

		// ── 最近订单 + 订单汇总（官方 PastOrderList / PastOrderSummary）────────────
		async function load_orders() {
			orders = await p.call(api + "api_list_sales", {
				shift: shift?.shift?.name, limit: 0 });
			paint_orders();
		}

		function paint_orders() {
			const status = order_status_control.get_value();
			const term = String(order_search_control.get_value() || "").toLowerCase();
			const rows = orders.filter(row => {
				if (status === "refund" && row.sale_type !== "refund") return false;
				if (status && status !== "refund" && row.status !== status) return false;
				if (term && !String(row.receipt_no || row.name).toLowerCase().includes(term)) return false;
				return true;
			});
			q(".past-order-list .list-container").html(rows.length ? rows.map(row => `
				<div class="invoice-wrapper" data-order="${p.escape(row.name)}">
					<div class="invoice-name-customer">
						<div class="invoice-customer">${p.escape(kind_label(row.sale_type))}</div>
						<div class="invoice-name">${p.escape(row.receipt_no || row.name)}</div>
					</div>
					<div class="invoice-name-date">
						<div class="invoice-name">${p.escape(frappe.user_info(row.cashier).fullname || row.cashier || "")}</div>
						<div class="invoice-date">${p.escape(p.datetime_text(row.sold_at))}</div>
					</div>
					<div class="invoice-total-date">
						<div class="invoice-total">${p.escape(money(row.grand_total))}</div>
						<div class="invoice-date">${p.escape(state_label(row.status))}</div>
					</div>
				</div>`).join("")
				: `<div class="no-item-wrapper">${p.escape(__("No Data"))}</div>`);
		}

		async function show_summary(name) {
			const doc = await p.call(api + "api_get_sale", { name });
			active_order = doc;
			q(".past-order-summary .no-summary-placeholder").css("display", "none");
			q(".past-order-summary .invoice-summary-wrapper").css("display", "block");
			q(".past-order-summary .upper-section").html(`
				<div class="left-section">
					<div class="customer-section">
						<div class="customer-name">${p.escape(doc.receipt_no || doc.name)}</div>
						<div class="customer-email">${p.escape(p.datetime_text(doc.sold_at))}</div>
					</div>
					<div class="cashier">${p.escape(t("trade.cashier"))}: ${
						p.escape(frappe.user_info(doc.cashier).fullname || doc.cashier || "")}</div>
				</div>
				<div class="right-section">
					<div class="paid-amount">${p.escape(money(doc.paid_total))}</div>
					<div class="invoice-name">${p.escape(doc.receipt_no || doc.name)}</div>
					<span class="indicator-pill whitespace-nowrap ${state_pill(doc.status)}"><span>${
						p.escape(state_label(doc.status))}</span></span>
				</div>`);
			q(".past-order-summary .order-summary-container").html((doc.lines || []).map(row => `
				<div class="item-row-wrapper">
					<div class="item-row-data">
						<div class="item-name">${p.escape(row.description || row.product)}</div>
						<div class="item-qty">${p.escape(p.number(row.qty))} ${p.escape(row.unit || "")}</div>
						<div class="item-rate-disc">
							${num(row.discount) ? `<span class="item-disc">−${p.escape(money(row.discount))}</span>` : ""}
							<div class="item-rate">${p.escape(money(row.unit_price))}</div>
							<div class="item-amount">${p.escape(money(row.line_total))}</div>
						</div>
					</div>
				</div>`).join(""));
			q(".past-order-summary .totals-container").html(`
				<div class="summary-row-wrapper"><span>${p.escape(t("trade.net"))}</span><span>${p.escape(money(doc.subtotal))}</span></div>
				<div class="summary-row-wrapper"><span>${p.escape(t("trade.discount"))}</span><span>${p.escape(money(doc.discount_total))}</span></div>
				<div class="summary-row-wrapper"><span>${p.escape(t("trade.vat"))}</span><span>${p.escape(money(doc.vat_amount))}</span></div>
				<div class="summary-row-wrapper grand-total"><span>${p.escape(t("trade.total"))}</span><span>${p.escape(money(doc.grand_total))}</span></div>`);
			q(".past-order-summary .payments-container").html((doc.payments || []).map(row => `
				<div class="summary-row-wrapper"><span>${p.escape(method_label(row.method))}</span><span>${p.escape(money(row.amount))}</span></div>`)
				.join("") + (num(doc.change_amount)
					? `<div class="summary-row-wrapper"><span>${p.escape(t("trade.change"))}</span><span>${p.escape(money(doc.change_amount))}</span></div>` : ""));
			// 官方这张汇总底下的按钮按单据状态给；我们有「退货退款」和「作废」两个真动作。
			const done = doc.status === "completed";
			q(".past-order-summary .summary-btns").html([
				can_write && done && doc.sale_type === "sale"
					? `<div class="btn btn-sm summary-btn return-btn">${p.escape(t("trade.refund"))}</div>` : "",
				can_write && done
					? `<div class="btn btn-sm summary-btn void-btn">${p.escape(t("trade.void"))}</div>` : "",
			].filter(Boolean).join(" "));
		}

		function show_summary_placeholder() {
			active_order = null;
			q(".past-order-summary .no-summary-placeholder").css("display", "flex");
			q(".past-order-summary .invoice-summary-wrapper").css("display", "none");
		}

		// ── 开班 / 交班（弹窗字段与对账表照官方的 Opening / Closing Entry）──────────
		// 币种：弹窗顶上那一格给非表格字段用；子表每一行也要带（网格里的 `options: "currency"`
		// 取的是**本行**的 currency，不带就按系统默认画成 `$`）。
		const CURRENCY = frappe.boot.sysdefaults.currency || "THB";
		const currency_field = () => ({ fieldtype: "Data", fieldname: "currency", hidden: 1,
			default: CURRENCY });
		// 官方那张对账表是整页表单，我们放在弹窗里 —— 给宽一点，五列才看得清。
		const widen = dialog => dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");

		function balance_table(fields_extra = [], rows = null) {
			return [
				{ fieldtype: "Table", fieldname: "balances", label: t("trade.balances"),
					cannot_add_rows: 1, in_place_edit: 1, reqd: 1,
					data: rows || METHODS.map(method => ({ method, opening_amount: 0,
						counted_amount: 0, currency: CURRENCY })),
					fields: [
						{ fieldname: "method", fieldtype: "Select", options: METHODS.join("\n"),
							label: t("trade.method"), in_list_view: 1, reqd: 1 },
						{ fieldname: "opening_amount", fieldtype: "Currency", options: "currency",
							label: t("trade.openingAmount"), in_list_view: 1 },
						...fields_extra,
					] },
			];
		}

		async function open_shift_dialog() {
			const terminals = await p.call(api + "api_list_terminals");
			const preferred = terminals.find(row => Number(row.is_default)) || terminals[0];
			let dialog;
			dialog = p.dialog(t("trade.openShift"), [
				currency_field(),
				{ fieldtype: "Link", fieldname: "terminal", options: "ERP POS Terminal", only_select: true,
					label: t("trade.terminal"), reqd: 1, default: preferred?.name || "",
					description: t("trade.terminalHint") },
				...balance_table(),
				{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
			], t("trade.openShift"), async (values, d) => {
				const openings = Object.fromEntries((values.balances || [])
					.filter(row => row.opening_amount)
					.map(row => [row.method, num(row.opening_amount)]));
				const result = await p.call(api + "api_open_shift",
					{ terminal: values.terminal, balances: openings, note: values.note }, "POST");
				d.hide();
				p.success(t("trade.shiftOpened", result.shift_seq));
				await refresh();
			});
			widen(dialog);
		}

		async function close_shift_dialog() {
			if (!shift) { frappe.msgprint(t("trade.noShift")); return; }
			const data = shift;
			const expected = Object.fromEntries((data.balances || [])
				.map(row => [row.method, num(row.expected_amount)]));
			const openings = Object.fromEntries((data.balances || [])
				.map(row => [row.method, num(row.opening_amount)]));
			let dialog;
			const paint = () => {
				const grid = dialog?.fields_dict?.balances?.grid;
				let cash_diff = 0;
				for (const row of grid?.data || []) {
					row.expected_amount = expected[row.method] || 0;
					row.difference = round2(num(row.counted_amount) - num(row.expected_amount));
					// 只重画动过的那一行，别整表刷新（正输着数字时整表重画会把焦点甩掉）。
					grid?.grid_rows_by_docname?.[row.name]?.refresh_row?.(row);
					if (row.method === "cash") cash_diff = row.difference;
				}
				dialog?.set_value("cash_diff", cash_diff);
				dialog?.fields_dict?.cash_diff?.$wrapper
					?.find("input, .control-value, .like-disabled-input")
					.toggleClass("pearnly-payment-due", Boolean(cash_diff));
			};
			// 表里先把期初与应有填好、默认实点=应有（现金那一行就是抽屉里应有的数）。
			const rows = METHODS.map(method => ({
				method, currency: CURRENCY,
				opening_amount: openings[method] || 0,
				expected_amount: expected[method] || 0,
				counted_amount: expected[method] || 0,
				difference: 0,
			}));
			dialog = p.dialog(t("trade.closeShift"), [
				currency_field(),
				{ fieldtype: "Int", fieldname: "sales_count", label: t("trade.salesCount"),
					read_only: 1, default: data.sales_count },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "net",
					label: t("trade.net"), read_only: 1, default: data.net },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Int", fieldname: "refund_count", label: t("trade.refundCount"),
					read_only: 1, default: data.refund_count },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "change_total",
					label: t("trade.changeTotal"), read_only: 1, default: data.change_total },
				...balance_table([
					{ fieldname: "expected_amount", fieldtype: "Currency", options: "currency",
						label: t("trade.expectedAmount"), in_list_view: 1, read_only: 1 },
					{ fieldname: "counted_amount", fieldtype: "Currency", options: "currency",
						label: t("trade.countedAmount"), in_list_view: 1, reqd: 1 },
					{ fieldname: "difference", fieldtype: "Currency", options: "currency",
						label: t("trade.diff"), in_list_view: 1, read_only: 1 },
				], rows),
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "cash_diff",
					label: t("trade.diff"), read_only: 1, default: 0 },
				{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
			], t("trade.closeShift"), async (values, d) => {
				const counted = Object.fromEntries((values.balances || [])
					.map(row => [row.method, num(row.counted_amount)]));
				const result = await p.call(api + "api_close_shift",
					{ name: shift.shift.name, counted, note: values.note }, "POST");
				d.hide();
				p.success(t("trade.shiftClosed", p.money(result.cash_diff)));
				await refresh();
			});
			widen(dialog);
			dialog.fields_dict.balances.grid.wrapper.on("change", "input", () => paint());
			dialog.set_value("cash_diff", 0);
			paint();
		}

		// ── 事件 ────────────────────────────────────────────────────────────────
		$app.on("click", ".item-wrapper", event => {
			const row = items.find(item => item.name === event.currentTarget.dataset.product);
			if (row) add_to_cart(row);
		});
		$app.on("click", ".cart-item-wrapper", event => {
			// 官方：收款面板开着时点购物车行 = 先退回购物车（Edit Cart）再看明细。
			if (paying) { paying = false; pay_target = null; paint_views(); }
			select_line(event.currentTarget.dataset.product);
		});
		$app.on("click", ".cart-totals-section .edit-cart-btn", () => {
			paying = false; pay_target = null;
			paint_views();
		});
		$app.on("click", ".item-details-container .close-btn", () => {
			selected = null; pad_target = null;
			paint_cart(); paint_views();
		});
		$app.on("click", ".numpad-section .numpad-btn", event => {
			cart_numpad(event.currentTarget.dataset.buttonValue);
		});
		$app.on("click", ".cart-totals-section .checkout-btn", () => void checkout());
		$app.on("click", ".payment-modes .mode-of-payment", event => {
			const method = event.currentTarget.dataset.mode;
			pay_target = method;
			show_pay_mode(method);
		});
		$app.on("click", ".payment-container .numpad-btn", event => {
			pay_numpad(event.currentTarget.dataset.buttonValue);
		});
		$app.on("click", ".submit-order-btn", () => {
			if (q(".submit-order-btn").hasClass("disabled")) return;
			void submit_order();
		});
		$app.on("click", ".past-order-list .invoice-wrapper", async event => {
			await show_summary(event.currentTarget.dataset.order);
		});
		$app.on("click", ".past-order-summary .void-btn", async () => {
			if (!active_order) return;
			await p.call(api + "api_void_sale", { name: active_order.name }, "POST");
			p.success(t("trade.voidNotice", 1));
			await refresh(); await load_orders(); await show_summary(active_order.name);
		});
		$app.on("click", ".past-order-summary .refund-btn", () => {
			if (active_order) refund(active_order);
		});

		function refund(doc) {
			const request = crypto.randomUUID();
			let d;
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
					options: (doc.lines || []).map(row => ({ value: row.name,
						label: `${row.description || row.product} · ${row.warehouse_name || ""} · ${p.number(row.qty)}` })) },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"),
					options: METHODS.join("\n"), default: METHODS[0], reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "amount",
					label: t("trade.refundAmount"), reqd: 1 },
			];
			const calculate = () => {
				if (!d) return;
				const line = (doc.lines || []).find(row => row.name === d.get_value("original_line"));
				if (line) d.set_value("amount", round2(num(line.line_total) * num(d.get_value("qty") || 0) / num(line.qty)));
			};
			fields.find(item => item.fieldname === "original_line").change = calculate;
			fields.find(item => item.fieldname === "qty").change = calculate;
			d = p.dialog(t("trade.refund"), fields, t("trade.refund"), async (values, dialog) => {
				await p.call(api + "api_refund_sale", { name: doc.name,
					lines: [{ original_line: values.original_line, qty: values.qty }],
					payments: [{ method: values.method, amount: values.amount }],
					client_uuid: request }, "POST");
				dialog.hide();
				p.success(t("trade.refund"));
				await refresh(); await load_orders();
			});
			calculate();
		}

		$(document).on("keydown.pearnly_pos", event => {
			if (event.key !== "Escape") return;
			if (!selected) return;
			selected = null; pad_target = null;
			paint_cart(); paint_views();
		});

		// ── 取数 ────────────────────────────────────────────────────────────────
		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			paint_actions();
			if (!shift) {
				view = "counter"; paying = false; selected = null;
				items = []; cart = []; orders = [];
				paint_cart(); paint_views();
				q(".items-selector .label").text(t("trade.noShift"));
				return;
			}
			await load_items();
			await load_orders();
			paint_cart(); paint_views();
		}

		async function paint() {
			if (shift) q(".items-selector .label").text(t("trade.allItems"));
			paint_actions(); paint_cart();
			if (selected) paint_details();
			if (paying) paint_payment();
			paint_views();
		}

		p.add_refresh(page, () => void refresh());
		await refresh();
		if (!shift && can_write) await open_shift_dialog();
		return () => {
			$(document).off("keydown.pearnly_pos");
			page.main.find(".point-of-sale-app").remove();
		};
	};
})();
