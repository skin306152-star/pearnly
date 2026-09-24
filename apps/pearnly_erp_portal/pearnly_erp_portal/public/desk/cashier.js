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
	const ALL_METHODS = ["cash", "card", "promptpay", "transfer"];
	// 页面上真正给收银员看的那几种 = **这家公司开了的**那几种（`apply_company_methods` 按
	// 服务端回的 `payment_methods` 收窄）。付款面板、落单校验、退款那一格看的都是它。
	let METHODS = ALL_METHODS;
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
	// 官方那颗折扣图标（`pos_item_cart.js` 的 `get_discount_icon()` **原文照抄**）。
	// 之前我们整个格子是自己写的、没有图标，看着就跟官方不是一个东西。
	const DISCOUNT_ICON = `<svg class="discount-icon" width="24" height="24" viewBox="0 0 24 24" stroke="currentColor" fill="none" xmlns="http://www.w3.org/2000/svg">
		<path d="M19 15.6213C19 15.2235 19.158 14.842 19.4393 14.5607L20.9393 13.0607C21.5251 12.4749 21.5251 11.5251 20.9393 10.9393L19.4393 9.43934C19.158 9.15804 19 8.7765 19 8.37868V6.5C19 5.67157 18.3284 5 17.5 5H15.6213C15.2235 5 14.842 4.84196 14.5607 4.56066L13.0607 3.06066C12.4749 2.47487 11.5251 2.47487 10.9393 3.06066L9.43934 4.56066C9.15804 4.84196 8.7765 5 8.37868 5H6.5C5.67157 5 5 5.67157 5 6.5V8.37868C5 8.7765 4.84196 9.15804 4.56066 9.43934L3.06066 10.9393C2.47487 11.5251 2.47487 12.4749 3.06066 13.0607L4.56066 14.5607C4.84196 14.842 5 15.2235 5 15.6213V17.5C5 18.3284 5.67157 19 6.5 19H8.37868C8.7765 19 9.15804 19.158 9.43934 19.4393L10.9393 20.9393C11.5251 21.5251 12.4749 21.5251 13.0607 20.9393L14.5607 19.4393C14.842 19.158 15.2235 19 15.6213 19H17.5C18.3284 19 19 18.3284 19 17.5V15.6213Z" stroke-miterlimit="10" stroke-linecap="round" stroke-linejoin="round"/>
		<path d="M15 9L9 15" stroke-miterlimit="10" stroke-linecap="round" stroke-linejoin="round"/>
		<path d="M10.5 9.5C10.5 10.0523 10.0523 10.5 9.5 10.5C8.94772 10.5 8.5 10.0523 8.5 9.5C8.5 8.94772 8.94772 8.5 9.5 8.5C10.0523 8.5 10.5 8.94772 10.5 9.5Z" fill="white" stroke-linecap="round" stroke-linejoin="round"/>
		<path d="M15.5 14.5C15.5 15.0523 15.0523 15.5 14.5 15.5C13.9477 15.5 13.5 15.0523 13.5 14.5C13.5 13.9477 13.9477 13.5 14.5 13.5C15.0523 13.5 15.5 13.9477 15.5 14.5Z" fill="white" stroke-linecap="round" stroke-linejoin="round"/>
	</svg>`;

	p.cashier = async (page, t) => {
		// **临时对照路由**：`/desk/pearnly-cashier?pearnly_pos_next=1` 走"官方原样搬过来"的那一套
		// （`desk/pos/official-*.js` + 适配层 `pearnly-pos-adapter.js`）。默认那条路（下面的手写实现）
		// 一行不动，方便两边同屏对、也保证演示站随时可用。对接完成后删掉这条分支与旧实现。
		if (String(window.location.search || "").includes("pearnly_pos_next")) {
			// 适配层随 `app_include_js` 一起来（按文件内容带版本，见 hooks.py 里那一段）——
			// 页面里**不要再 `frappe.require` 一次**：框架那个加载器从路径取扩展名，
			// 带 `?v=` 会解析错；不带版本的路径又会被浏览器缓存住（改了代码页面还是旧的，
			// 排查时看 `window.__pos_adapter_v`）。这里的兜底只为"hooks 改了、web 还没重启"。
			if (p.pos_next_route) {
				p.pos_next_route(page);
			} else {
				frappe.require("/assets/pearnly_erp_portal/desk/pos/pearnly-pos-adapter.js",
					() => p.pos_next_route(page));
			}
			return () => { page.main.find(".point-of-sale-app").remove(); };
		}
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
				${/* 客户那一格在**官方的位置**：`pos_item_cart.js` 里它是 `.customer-cart-container`
				     的第一个直接子级，与 `.cart-container` 平级（各自一张卡）。未选 = 官方原生
				     Link 搜索框；选了 = `.customer-details` 那张卡。 */""}
				<div class="customer-section"></div>
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
							${/* 官方 `make_cart_totals_section()` 第一格就是这颗虚线「折扣」：点一下换成
							     百分比输入（官方截图里那句「输入折扣百分比」），输入完原地变绿虚线
							     「Additional 7% discount applied」。我们照同一条（F61），用的是框架
							     原生控件 `frappe.ui.form.make_control`，不是自己写裸 input。 */""}
							<div class="add-discount-wrapper">${p.escape(t("trade.addDiscount"))}</div>
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

		// 商品区顶上那颗搜索框：官方原生 Data 控件，`options: "Barcode"` 时**框架自己**会在
		// `.control-input` 里挂一颗 `icon-scan-barcode` 的 `.link-btn`（点开的是官方
		// `frappe.ui.Scanner`，扫到的字回填进本框），Clear 那颗小叉照官方 `attach_clear_btn()`
		// 的写法补在后面 —— 两颗的位置、尺寸、层叠全由官方控件与框架样式决定，我们不再自己画。
		const search_control = frappe.ui.form.make_control({
			df: { fieldtype: "Data", options: "Barcode", placeholder: t("trade.searchProducts") },
			parent: $app.find(".items-selector .search-field"), render_input: true,
		});
		search_control.toggle_label(false);
		// Clear 那颗要放在**扫描前面**（官方页面上实测就是这个顺序：两颗 `.link-btn` 在同一处叠着，
		// 后进的画在上面，官方露出来的是条码那颗），所以用 `prepend`。
		search_control.$wrapper.find(".control-input").prepend(
			`<span class="link-btn" style="top: 2px;">
				<a class="btn-open no-decoration" title="${__("Clear")}">${
					frappe.utils.icon("close", "sm")}</a>
			</span>`);
		search_control.$wrapper.on("click", `a[title="${__("Clear")}"]`, () => {
			search_control.set_value(""); void load_items(); search_control.set_focus();
		});
		// 点了官方那颗扫码 = "这一次是从码来的"：搜到唯一一条就直接进购物车，一条都没有就
		// 照实说（官方 `onScan` 那两段行为）。见下面 `load_items()`。
		search_control.$wrapper.on("click", `a[title="${__("Scan")}"]`, () => { scanned = true; });
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
			// 官方"新单"= 一张全新的单子：客户跟着清空（回到散客），免得下一单悄悄挂在上一单的人名下。
			reset_customer();
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
			// 官方：汇总屏**单独显示**时占满十列（在页面里居中），与最近订单并排时才回到六列
			// （bundle 里那句 `this.$component.css("grid-column","span 10 / span 10")`）。
			q(".past-order-summary").css("grid-column",
				orders_shown ? "span 6 / span 6" : "span 10 / span 10");
			q(".cart-totals-section .edit-cart-btn").toggle(paying);
			// 「结账」那颗按钮**常驻**，空车也是亮的 —— 官方就是这样（实测：空购物车时
			// 它的 `.checkout-btn` 仍是 `cursor: pointer`、点下去直接进收款屏，没有灰态）。
			q(".cart-totals-section .checkout-btn").css("display", paying || selected ? "none" : "flex");
		}

		// ── 商品区 ──────────────────────────────────────────────────────────────
		// 官方 `pos_item_selector.js`：扫码枪/摄像头扫进来就一个字塞进搜索框，回车时
		// 「唯一一条 → 直接进购物车；一条都没有 → 橙色提示 + 出错音」。
		let scanned = false;
		async function load_items() {
			if (!shift) return;
			const result = await p.call(api + "api_counter_products", {
				terminal: shift.shift.terminal, search: search_control.get_value() || undefined,
				start: 0, limit: 40 });
			items = result?.items || [];
			items_total = num(result?.total);
			if (scanned) {
				scanned = false;
				const code = String(search_control.get_value() || "");
				if (items.length === 1) {
					add_to_cart(items[0]);
					frappe.utils.play_sound("submit");
					search_control.set_value("");
					await load_items();
					return;
				}
				if (!items.length && code) {
					frappe.show_alert({ message: t("trade.scanNotFound", code), indicator: "orange" });
					frappe.utils.play_sound("error");
				}
			}
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
		// 整单折扣（官方那颗「折扣」）：按百分比减在净额上，落单时带 `discount_total` 给服务端。
		let discount_pct = 0;
		// 折扣格正在编辑态：这时重画不能把输入框盖掉（官方 `discount_field` 同一手）。
		let discount_editing = false;
		const cart_gross = () => round2(cart.reduce((sum, row) => sum + line_total(row), 0));
		const cart_discount = () => round2(cart_gross() * (num(discount_pct) / 100));
		const cart_net = () => round2(cart_gross() - cart_discount());

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
			// 金额那两格照官方 `get_rate_discount_html()`：**应收金额 ≠ 单价时才出两行**，
			// 上面是应收金额、下面是单价（qty 1 且没折扣就一行）。原来我们**永远两行、
			// 而且上下颠倒**（上单价、下总额），行高也比官方高一截（56 vs 46）。
			const rate = num(row.unit_price);
			const amount = round2(line_total(row));
			const rates = rate && amount && rate !== amount
				? `<div class="item-rate">${p.escape(money(amount))}</div>
					<div class="item-amount">${p.escape(money(rate))}</div>`
				: `<div class="item-rate">${p.escape(money(rate || amount))}</div>`;
			return `<div class="cart-item-wrapper" data-product="${p.escape(row.product)}">
				${abbr}
				<div class="item-name-desc"><div class="item-name">${p.escape(row.product_name || "")}</div></div>
				<div class="item-qty-rate">
					<div class="item-qty"><span>${p.escape(p.number(row.qty))} ${p.escape(row.unit || "")}</span></div>
					<div class="item-rate-amount">
						${rates}
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
			// **官方的底色口径**（`highlight_checkout_btn`）：有货时给「结账」上主色底 + 反白字，
			// 空车时回到普通底（`--control-bg`）—— 而且**只有上过底才允许点**（官方点击处理里
			// 就是查 inline style 里有没有 `--btn-primary`）。折扣那一格也跟着有货才出现。
			q(".cart-totals-section .checkout-btn").css(has
				? {"background-color": "var(--btn-primary)", color: "var(--neutral)"}
				: {"background-color": "var(--control-bg)", color: ""});
			q(".cart-totals-section .add-discount-wrapper").css("display", has ? "flex" : "none");
			paint_discount();
			q(".cart-totals-section .item-qty-total-value").text(p.number(cart_qty()));
			q(".cart-totals-section .net-total-value").text(money(net));
			q(".cart-totals-section .grand-total-container > div:last-child").text(money(net));
			q(".numpad-totals .numpad-item-qty-total").html(`${p.escape(t("trade.totalItems"))}: <span>${p.escape(p.number(cart_qty()))}</span>`);
			q(".numpad-totals .numpad-net-total").html(`${p.escape(t("trade.net"))}: <span>${p.escape(money(net))}</span>`);
			q(".numpad-totals .numpad-grand-total").html(`${p.escape(t("trade.total"))}: <span>${p.escape(money(net))}</span>`);
			// 同上：结账按钮常驻（空车也亮），官方的空车点它就直接进收款屏。
			q(".cart-totals-section .checkout-btn").css("display", selected || paying ? "none" : "flex");
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
			// **不置灰**：官方那颗「完成订单」任何时候都是可点的，点下去才校验并给提示
			// （官方源码：`You cannot submit empty order.` / `You cannot submit the order
			// without payment.` + `play_sound("error")`）。这里照它，见下面的点击处理。
		}

		async function checkout() {
			if (!cart.length) return;
			// 点「结账」那一声 = 官方同一颗按钮的**存草稿音** `click`（音量 0.05），不是键盘音。
			// 官方链：`.checkout-btn` 的点击处理本身确实没有 `play_sound`，但它调用
			// `save_and_checkout()`（`pos_controller.js`），购物车有改动时先 `frm.save()`，
			// 存成功后由 frappe `form.js` 播 `click` —— 声音来自这一步保存。
			// 2026-09-23 在官方 POS 实测（erpnext 16.34.1 / frappe 16.33，站 `business.localhost`）：
			// 加购**不响**；点「结账」响 `sound-click`、音量 0.05，那一下真把 `new-sales-invoice-…`
			// 存成了 `ACC-SINV-2026-00032`。所以这里取 `click`，`numpad-touch` 只留给收款小键盘。
			// 如实记差别：官方这一声的由来是"存了草稿"，我们这一下不写库（完成订单才落单），
			// 只对齐声音；官方若购物车没有改动、不进保存，则那一下是静的。
			// 音量与静音不用在这里判：官方 `play_sound` 自己看 `User.mute_sounds`。
			frappe.utils.play_sound("click");
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

		// ── 「折扣」：官方那颗虚线格子点一下就**原地换成百分比输入**，不是弹窗 ─────────────
		// 官方 `show_discount_control()`（`pos_item_cart.js`）：把 `.add-discount-wrapper` 的内容
		// 换成原生输入框（`make_control`）并聚焦、去掉虚线框；输入完（回车/失焦）应用，>100% 先弹
		// 红提示再归零；有折扣时那格变**绿虚线**写着「Additional **7**% discount applied」，
		// 再点一次还能改（`hide_discount_control()`）。整单按它减（落单带 discount_total）。
		function paint_discount() {
			const $box = q(".cart-totals-section .add-discount-wrapper");
			if (!$box.length || discount_editing) return;
			const pct = num(discount_pct);
			// 边框与官方同一手：空闲 `1px dashed var(--gray-500)`，已折扣官方写的是
			// `var(--dark-green-500)` —— 那个变量在页面上不存在、整条 border 被丢弃，
			// 所以官方**已折扣时没有边框**。这里照**实际渲染**，不照它的意图。
			$box.css(pct
				? { border: "none", padding: "var(--padding-sm) var(--padding-md)" }
				: { border: "1px dashed var(--gray-500)", padding: "var(--padding-sm) var(--padding-md)" })
				.html(pct
					? `<div class="edit-discount-btn">${DISCOUNT_ICON} ${p.escape(t("trade.discountAdditional"))}&nbsp;<b>${
						p.escape(p.number(pct))}</b>% ${p.escape(t("trade.discountApplied"))}</div>`
					: `${DISCOUNT_ICON} ${p.escape(t("trade.addDiscount"))}`);
		}

		function discount_input() {
			const $box = q(".cart-totals-section .add-discount-wrapper");
			if (!$box.length || discount_editing) return;
			discount_editing = true;
			const pct = num(discount_pct);
			$box.css({ border: "none", padding: "0px" })
				.html(`<div class="add-discount-field"></div>`);
			const field = frappe.ui.form.make_control({
				df: { fieldtype: "Float", label: t("trade.discountPercent"), precision: 2,
					placeholder: pct ? `${p.number(pct)}%` : t("trade.discountPercentHint"),
					input_class: "input-xs" },
				parent: $box.find(".add-discount-field"), render_input: true,
			});
			p.tidy_number(field);
			field.toggle_label(false);
			field.set_value(pct || "");
			// **值先写进去、事件后挂**：`set_value` 自己会触发一次 change，
			// 先挂 onchange 就会"刚打开就应用并收起"，看着像没反应（实测踩过）。
			field.$input.on("change", () => { discount_pct = num(field.get_value()); apply_discount(); });
			// 没改动就点走，也算结束编辑、回到格子上（免得输入框一直杵在那）。
			field.$input.on("blur", () => {
				if (!discount_editing) return;
				discount_editing = false;
				paint_discount();
			});
			field.set_focus();
		}

		function apply_discount() {
			let pct = num(discount_pct);
			if (pct > 100) {
				frappe.msgprint({ title: t("trade.discountInvalid"), indicator: "red",
					message: t("trade.discountOver100") });
				pct = 0;
			}
			discount_pct = Math.max(0, pct);
			discount_editing = false;
			paint_cart();
			if (paying) { paint_payment_amounts(); paint_payment_totals(); }
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
				// 这一单挂哪个客户（空 = 散客）；服务端会核它确实存在且启用着。
				// 只认**查得到**的那个客户：客户框里还没落定的字不带进单子里。
				customer: customer_picked || undefined,
				// 整单折扣（官方那颗「折扣」按百分比给）：服务端 `sale_totals(discount_total=…)`。
				discount_total: cart_discount(),
				lines: cart.map(row => ({ product: row.product, qty: num(row.qty),
					unit_price: num(row.unit_price), discount: num(row.discount),
					unit: row.unit, description: row.description,
					warehouse: row.warehouse, location: row.location })),
				payments, client_uuid: request,
			}, "POST");
			cart = []; selected = null; pad_target = null; pad_value = ""; discount_pct = 0;
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
			orders = await p.call(api + "api_current_sales", {
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
				: `<div class="no-item-wrapper">${p.escape(t("trade.receiptsEmpty"))}</div>`);
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
					${doc.customer ? `<div class="customer-picked">${p.escape(t("trade.customer"))}: ${
						p.escape(doc.customer_name || doc.customer)}</div>` : ""}
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
			// 底部按钮照官方的条件表给（官方 `get_condition_btn_map`）：单子落定之后是
			// 「打印小票 / 邮件小票 / **新订单**」，已收款单还能「退货」，草稿才是编辑/删除。
			// 打印与邮件小票我们**没有做**（F22 的产品口径，位置上仍不画）；「新订单」就是
			// 用户说的"结束下一单"那颗，官方也在这一组里。
			q(".past-order-summary .summary-btns").html([
				// 「打印收据」走**框架原生**打印视图（`/printview`，官方也是用它出小票；
				// 我们自己的小票模板还没做，先开原生那份）。
				done ? `<div class="btn btn-sm summary-btn print-btn">${p.escape(t("trade.printReceipt"))}</div>` : "",
				can_write && done && doc.sale_type === "sale"
					// 类名必须是 `refund-btn`：下面 `$app.on("click", "… .refund-btn")` 绑的就是它。
					// 早先这里画成 `return-btn`，事件接不上 —— 这颗「退货退款」点了没反应。
					? `<div class="btn btn-sm summary-btn refund-btn">${p.escape(t("trade.refund"))}</div>` : "",
				can_write && done
					? `<div class="btn btn-sm summary-btn void-btn">${p.escape(t("trade.void"))}</div>` : "",
				// **主按钮永远排最右边**（用户 2026-09-23："次按钮是灰的，主按钮排最右边"）。
				`<div class="btn btn-sm summary-btn new-order-btn">${p.escape(t("trade.newOrder"))}</div>`,
			].filter(Boolean).join(" "));
		}

		function show_summary_placeholder() {
			active_order = null;
			q(".past-order-summary .no-summary-placeholder").css("display", "flex");
			q(".past-order-summary .invoice-summary-wrapper").css("display", "none");
		}

		// ── 开班 / 交班 ────────────────────────────────────────────────────────
		// 弹窗本体挪到共用模块 `desk/pos/pearnly-pos-shift.js`：搬过来的官方收银台也要
		// 「交班」这一项（用户 2026-09-24 口径：右上角那三个点里**之前是什么**就放什么），
		// 两条路调同一份，不各写一遍。这里只留转调。
		const open_shift_dialog = () => p.pos_shift.open({ t, on_done: () => refresh() });
		const close_shift_dialog = () => p.pos_shift.close({ t, shift, on_done: () => refresh() });

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
		// 与官方同一条闸：**没上底（空车）就不响应**（官方是查 inline style 里有没有
		// `--btn-primary`）。上了底才进收款屏，同时把收款屏那颗切出来。
		$app.on("click", ".cart-totals-section .checkout-btn", event => {
			if (!String($(event.currentTarget).attr("style") || "").includes("--btn-primary")) return;
			void checkout();
		});
		// 官方那颗虚线「折扣」：点一下原地变成输入框（有折扣时是绿虚线，再点还能改）。
		$app.on("click", ".cart-totals-section .add-discount-wrapper", () => discount_input());
		$app.on("click", ".payment-modes .mode-of-payment", event => {
			const method = event.currentTarget.dataset.mode;
			pay_target = method;
			show_pay_mode(method);
		});
		$app.on("click", ".payment-container .numpad-btn", event => {
			pay_numpad(event.currentTarget.dataset.buttonValue);
		});
		$app.on("click", ".submit-order-btn", () => {
			// 官方那两句原话（空单 / 没收钱）：先提示 + 错误音，再决定要不要落单。
			const total = cart_net();
			const paid = round2(METHODS.reduce((sum, m) => sum + num(payments_now()[m]), 0));
			if (!cart.length || total <= 0) {
				frappe.show_alert({ message: t("trade.submitEmpty"), indicator: "orange" });
				frappe.utils.play_sound("error");
				return;
			}
			if (paid <= 0) {
				frappe.show_alert({ message: t("trade.submitNoPayment"), indicator: "orange" });
				frappe.utils.play_sound("error");
				return;
			}
			void submit_order();
		});
		$app.on("click", ".past-order-list .invoice-wrapper", async event => {
			await show_summary(event.currentTarget.dataset.order);
		});
		$app.on("click", ".past-order-summary .void-btn", async () => {
			if (!active_order) return;
			await p.pos_receipt.void_sale({ t, doc: active_order,
				on_done: async () => { await refresh(); await load_orders(); await show_summary(active_order.name); } });
		});
		$app.on("click", ".past-order-summary .refund-btn", async () => {
			if (!active_order) return;
			await p.pos_receipt.refund({ t, doc: active_order, methods: METHODS,
				on_done: async () => { await refresh(); await load_orders(); } });
		});
		// 「新订单」= 清干净这一单回到收银台（官方汇总屏上的 New Order，同一颗按钮）。
		$app.on("click", ".past-order-summary .new-order-btn", () => void new_invoice());
		// 客户卡右边那颗叉 = 官方 `reset_customer_selector()`：清掉这一单挂的客户，
		// 回到散客卡。`last_customer` 一起清空，下一轮对值才会认"现在是空的"。
		// 点卡片本体（官方 `.customer-display` 的同一手）= 打开客户搜索框，焦点落在里面。
		$app.on("click", ".customer-section .customer-display", event => {
			if ($(event.target).closest(".reset-customer-btn").length) return;
			if (customer_mode === "selector") return;
			make_customer_selector();
			customer_control?.set_focus();
		});
		$app.on("click", ".customer-section .reset-customer-btn", () => {
			reset_customer();
		});
		// 「打印收据」= 框架原生打印视图（官方出小票也走这一条）。三段动作都在共用模块里。
		$app.on("click", ".past-order-summary .print-btn", () => {
			if (active_order) p.pos_receipt.print(active_order);
		});


		$(document).on("keydown.pearnly_pos", event => {
			if (event.key !== "Escape") return;
			if (!selected) return;
			selected = null; pad_target = null;
			paint_cart(); paint_views();
		});

		// ── 取数 ────────────────────────────────────────────────────────────────
		// 这家公司收哪几种钱：与付款面板、开班/交班对账表、退款那一格同一份来源 ——
		// `api_list_terminals` 上跟着收银台一起回的 `payment_methods`（建公司时勾的那几种）。
		// 勾掉的方式在这条路上也一并消失；服务端落单时还会再拦一次。
		async function apply_company_methods() {
			if (!shift) return;
			const rows = await p.call(api + "api_list_terminals");
			const list = Array.isArray(rows) ? rows : (rows?.terminals || []);
			const terminal = list.find(row => row.name === shift.shift.terminal) || list[0] || {};
			const allowed = (terminal.payment_methods || []).map(key => String(key).trim()).filter(Boolean);
			const next = ALL_METHODS.filter(key => allowed.includes(key));
			if (next.length) METHODS = next;
		}

		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			paint_actions(); paint_customer();
			if (!shift) {
				view = "counter"; paying = false; selected = null;
				items = []; cart = []; orders = [];
				paint_cart(); paint_views();
				q(".items-selector .label").text(t("trade.noShift"));
				return;
			}
			await apply_company_methods();
			await load_items();
			await load_orders();
			paint_cart(); paint_views();
		}

		async function paint() {
			if (shift) q(".items-selector .label").text(t("trade.allItems"));
			paint_customer();
			paint_actions(); paint_cart();
			if (selected) paint_details();
			if (paying) paint_payment();
			paint_views();
		}

		// 客户那一格：照官方 `pos_item_cart.js` 的**两态**来做 —— 未选是 `.customer-field` 里一个
		// **隐藏了标签的原生 Link 控件**（下拉按**名称 / 电话**搜，客户表的 `search_fields` 定的），
		// 选中后整段换成 `.customer-details`（缩写圆 + 名字 + 电话 + 清空那颗叉）。
		// 位置、留白、字号、圆角、阴影全是官方那套类名，不再自己画卡片。
		//
		// **值就放在控件自己身上**（下面 `customer_now()` 每次都从控件读）。早先把它另存一个变量、
		// 再在重画时写回控件，结果那个变量的更新依赖字段的 `change` 回调 —— 回调没来时它一直是空，
		// 于是每次重画都把用户刚选的客户**擦掉**（用户 2026-09-23："你那个客户为什么喜欢自己换"）。
		// 现在控件是唯一真相：重画只负责建它，不再往回写。
		let customer_control = null;
		let last_customer = "", customer_watch = null;
		// 客户那一格的两态（与官方 `pos_item_cart.js` 同一套结构）：
		//   `card` = 卡片（默认的散客卡 / 选了人之后的客户卡）；`selector` = 搜索框。
		// 官方一进去显示的是 **POS Profile 的默认客户卡**；我们没有"默认客户"这一位
		// （我们的口径是"客户留空 = 散客"，见 `ERP Sale.customer` 的说明），所以默认那张
		// 画成**散客卡**：形状/位置/那颗 ✕ 与官方一致，语义上也对得上我们的数据。
		let customer_mode = "card";
		// 真的**查得到**的那个客户 —— 落单只认它，输入框里还没落定的半截字不算（下面 `update_customer_section`）。
		let customer_picked = "";
		// 落单时取的就是"当前到底选了谁"：控件值优先，兜底认输入框里的内部号（理由同卡片那里）。
		const customer_now = () => {
			const from_control = String(customer_control?.get_value() || "").trim();
			const typed = String(customer_control?.$input?.val() || "").trim();
			return (from_control || (/^[A-Z]+-\d+$/.test(typed) ? typed : "")) || "";
		};

		// 官方 `make_customer_selector()`：清空那一格，重新放一个新的 Link 控件。
		function make_customer_selector() {
			const host = q(".customer-section");
			if (!host.length) return;
			customer_mode = "selector";
			host.empty().append(`<div class="customer-field"></div>`);
			// 官方 `make_customer_selector()` 用的就是 `frappe.ui.form.make_control`（不是 FieldGroup）：
			// 只有这样 DOM 才和官方一致（FieldGroup 会多包一层 form / section / column），
			// 标签也照官方 `toggle_label(false)` 藏起来。
			customer_control = frappe.ui.form.make_control({
				df: {
					label: __("Customer"), fieldtype: "Link", options: "ERP Customer", only_select: true,
					// **这一格故意不给「新建客户」入口**（`link.js` 的 `pearnly_no_create`）：
					// 收银台这一单的购物车只活在这个页面里（`cart` 在渲染闭包里），点"新建"跳去
					// 客户页会把用户正在开的这单丢掉 —— 主数据页那些入口可以跳，这一格不行。
					// 要做就做成**就地弹我们自己的客户弹窗**（把客户页那张弹窗抽到共用层），那是下一步。
					pearnly_no_create: 1,
					// 官方的提示是"名称/电话/邮箱"，我们的客户主档只按**名称与电话**两栏搜
					// （用户 2026-09-23：只按名称和电话搜，没有的不要做），所以提示里不写邮箱。
					placeholder: t("trade.customerHint"),
					onchange: () => void update_customer_section(),
				},
				parent: host.find(".customer-field"), render_input: true,
			});
			customer_control.toggle_label(false);
			// 选完（鼠标点、键盘回车、失焦）之后把客户卡画出来。框架的 Link 在选完那一刻派发的
			// 是 `awesomplete-select` / `awesomplete-selectcomplete`，**不一定**派发 `change`；
			// 三个都听，再加一条兜底的定时核对 —— 漏掉一次事件就会出现"选了客户但卡片不出来"。
			customer_control.$input.on(
				"change awesomplete-select awesomplete-selectcomplete blur",
				() => void update_customer_section());
			if (!customer_watch) {
				customer_watch = setInterval(() => void update_customer_section(), 500);
			}
		}

		function paint_customer() {
			const host = q(".customer-section");
			if (!host.length) return;
			// 搜索态且控件被页面重建弄丢了 → 补回来；其余交给 `update_customer_section()` 决定
			// 画卡片还是画搜索框。
			if (customer_mode === "selector" && !host.find(customer_control?.wrapper || []).length) {
				make_customer_selector();
				return;
			}
			void update_customer_section(true);
		}

		// 官方 `reset_customer_selector()` 的落点：清掉这一单挂的客户。官方清完显示**搜索框**
		// （它那份"没有默认客户"的状态就是一个裸字段）；我们的空客户 = 散客，所以清完回到
		// **散客卡**，与刚进页面时一致。
		function reset_customer() {
			last_customer = "";
			customer_picked = "";
			customer_control = null;
			show_customer_card();
		}

		// 客户卡（官方 `update_customer_section()` 的同一套标记）：圆头像 + 名字 + 一行小字 + 右边 ✕。
		// `value` 为空 = 散客卡（✕ 只是回到散客，不往单子上写客户）。
		function show_customer_card({ name, desc, value } = {}) {
			const host = q(".customer-section");
			if (!host.length) return;
			customer_mode = "card";
			const shown_name = name || t("trade.walkIn");
			host.html(`<div class="customer-details">
				<div class="customer-display">
					<div class="customer-image customer-abbr">${p.escape(frappe.get_abbr(shown_name))}</div>
					<div class="customer-name-desc">
						<div class="customer-name">${p.escape(shown_name)}</div>
						<div class="customer-desc">${p.escape(desc || t("trade.pickCustomer"))}</div>
					</div>
					<div class="reset-customer-btn"${value ? ` data-customer="${p.escape(value)}"` : ""}>
						<svg width="32" height="32" viewBox="0 0 14 14" fill="none">
							<path d="M4.93764 4.93759L7.00003 6.99998M9.06243 9.06238L7.00003 6.99998M7.00003 6.99998L4.93764 9.06238L9.06243 4.93759" stroke="#8D99A6"/>
						</svg>
					</div>
				</div>
			</div>`);
		}

		// 官方 `update_customer_section()`：选了人就把那一格换成客户卡；没选就回到搜索态。
		async function update_customer_section(force = false) {
			// 取值优先问控件；控件还没把值落下来时（现场几种选法不一定都派发 change），
			// 兜底看它输入框里的字 —— 看起来像内部号（`CUS-#####`）就认它。
			const value = customer_now();
			if (!force && value === last_customer) return;
			last_customer = value;
			// 没选人：搜索态就让它开着（不然刚点开就被重画掉），卡片态保持/回到散客卡。
			if (!value) {
				if (customer_mode === "selector") return;
				show_customer_card();
				return;
			}
			// 官方 `fetch_customer_details()` 取的就是这一句 `frappe.db.get_value(...)`：查得到才有卡，
			// 查不到返回空对象（不会像走接口那样弹一个"未找到"）。
			const info = (await frappe.db.get_value("ERP Customer", value,
				["customer_name", "contact_phone"])).message || {};
			if (customer_now() !== value) return;                          // 取的这段时间里又换了
			// 输进来的字还不是一个真客户（框架的 Link 还没落定值）：不画卡，也不把它当成这一单的客户。
			if (!info.customer_name) { customer_picked = ""; return; }
			customer_picked = value;
			const name = info.customer_name;
			// 官方 `.customer-desc` 是"邮箱 - 电话"，都没有时写"点击添加邮箱/电话"；我们只有电话
			// 一栏，有就写、没有就不写这一行（收银台不做在这里补资料，不写一句点了没反应的话）。
			const phone = info?.contact_phone || "";
			show_customer_card({ name, desc: phone, value });
		}

		p.add_refresh(page, () => void refresh());
		await refresh();
		if (!shift && can_write) await open_shift_dialog();
		return () => {
			$(document).off("keydown.pearnly_pos");
			if (customer_watch) { clearInterval(customer_watch); customer_watch = null; }
			page.main.find(".point-of-sale-app").remove();
		};
	};
})();
