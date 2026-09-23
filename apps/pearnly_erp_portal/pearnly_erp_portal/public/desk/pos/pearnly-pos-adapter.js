/* 收银台「对接层」：把搬进来的官方收银台（`official-*.js`）接到我们自己的数据与服务上。
 *
 * 为什么是"子类 + 垫片"而不是改官方代码：官方那 8 个文件要保持与上游一致（好比对、好升级），
 * 所有"我们这边不一样"的地方都收在这一个文件里：
 *
 *   1) 命名空间：官方每个文件都写 `erpnext.PointOfSale.X = class {...}`，先把这个命名空间建好；
 *   2) 开班/设置：官方读 POS Opening Entry + POS Profile；我们读**自己的班次**（`ERP Shift`）与终端；
 *   3) 商品：官方调 `erpnext…get_items`；我们调自己的 `api_counter_products`，映射成官方要的字段；
 *   4) 单据：官方操作 POS Invoice；我们给它一张**真实的 `ERP Sale` 客户端文档**，
 *      并把官方的 `doc.items` 别名到我们的 `doc.lines`（同一数组），子表名也做映射；
 *   5) 提交：官方的 `submit_invoice` 走 POS Invoice 的 submit；我们走 `api_create_sale`；
 *   6) 删减：忠诚度、序列号/批号、改客户资料、过期开班提示、关闭收银台监听 —— 空实现。
 *
 * 用法（临时路由，见 `desk/cashier.js` 里的 `?pearnly_pos_next=1` 分支）：
 *   frappe.require([…official-*.js, "官方 css", "本文件"], () => p.pos_next.boot(wrapper));
 */
frappe.provide("frappe.pearnly.pos_next");

(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.cashier.api.";
	const NS = (window.erpnext = window.erpnext || {});
	const O = (NS.PointOfSale = NS.PointOfSale || {});

	// 官方代码里的子表名/父字段 → 我们的（`add_child(doc, "POS Invoice Item", "items")` 那一套）。
	const CHILD_DOCTYPE = {
		"POS Invoice Item": "ERP Sale Line",
		"Sales Invoice Item": "ERP Sale Line",
		"POS Invoice Payment": "ERP Sale Payment",
		"Sales Invoice Payment": "ERP Sale Payment",
	};
	const PARENTFIELD = { items: "lines" };

	function install_shims() {
		if (frappe.model.pearnly_pos_shim) return;
		frappe.model.pearnly_pos_shim = true;
		const add_child = frappe.model.add_child.bind(frappe.model);
		frappe.model.add_child = (doc, doctype, parentfield, idx) =>
			add_child(doc, CHILD_DOCTYPE[doctype] || doctype, PARENTFIELD[parentfield] || parentfield, idx);

		// **方法映射垫片**：官方那些 `erpnext.*` 的方法名，一律在这里翻到我们这边（或按"删减"给安全值）。
		// 放在一处的好处：官方代码一个字不改，我们也不用满地图找调用点；要接一个新方法就加一行。
		const MAP = {
			// —— 删减（我们不做）：给能让界面继续跑的安全值 ——
			"erpnext.selling.page.point_of_sale.point_of_sale.get_parent_item_group": () => ({ message: [] }),
			"erpnext.selling.page.point_of_sale.point_of_sale.set_customer_info": () => ({ message: {} }),
			"erpnext.selling.page.point_of_sale.point_of_sale.get_customer_recent_transactions": () => ({ message: [] }),
			"erpnext.stock.doctype.serial_no.serial_no.auto_fetch_serial_number": () => ({ message: [] }),
			"erpnext.accounts.doctype.loyalty_program.loyalty_program.get_loyalty_program_details_with_points":
				() => ({ message: {} }),
			"erpnext.controllers.sales_and_purchase_return.is_invoice_returnable": () => ({ message: false }),
			"erpnext.controllers.sales_and_purchase_return.get_invoice_item_returned_qty": () => ({ message: 0 }),
			// —— 接到我们自己的服务 ——
			"erpnext.accounts.doctype.pos_invoice.pos_invoice.get_stock_availability": (args) =>
				frappe.call({
					method: "pearnly_erp_portal.cashier.api.api_product_stock",
					args: { product: args && args.item_code, warehouse: args && args.warehouse },
				}).then(r => ({ message: r.message })),
		};
		const call = frappe.call.bind(frappe);
		frappe.call = function (opts) {
			const method = typeof opts === "string" ? opts : (opts && opts.method);
			const handler = MAP[method];
			if (!handler) return call(opts);
			const args = typeof opts === "string" ? {} : (opts && opts.args) || {};
			const deferred = $.Deferred();
			Promise.resolve(handler(args, opts)).then(
				message => deferred.resolve(message),
				() => deferred.reject());
			return deferred.promise();
		};
	}

	// 「一条我们的商品」→「官方商品卡要的字段」（`get_item_html` 读这几个）。
	function to_official_item(row) {
		return {
			item_code: row.name,
			item_name: row.product_name || row.name,
			item_image: row.image_url || "",
			stock_uom: row.unit || "",
			uom: row.unit || "",
			price_list_rate: Number(row.unit_price || 0),
			actual_qty: Number(row.available_qty ?? row.available ?? 0),
			has_batch_no: 0,
			has_serial_no: 0,
			barcode: row.barcode || "",
		};
	}

	// 我们的收银台设置 → 官方 `settings` 的形状（官方读到的那些开关）。
	async function our_settings(shift) {
		const terminals = await p.call(api + "api_list_terminals");
		const terminal = (terminals?.terminals || []).find(t => t.name === shift.terminal)
			|| (terminals?.terminals || [])[0] || {};
		return {
			warehouse: terminal.default_warehouse || "",
			company: shift.company || terminal.company || "",
			currency: terminal.currency || "THB",
			// 官方的开关：我们产品没有这些配置位 → 用"我们已有的行为"对应的固定值；
			// 将来要在终端上加一位就改这里（不是改官方代码）。
			hide_images: 0,
			allow_rate_change: 1,
			allow_discount_change: 1,
			allow_partial_payment: 0,
			auto_add_item_to_cart: 1,
			action_on_new_invoice: "Always Ask",
			print_receipt_on_order_complete: 0,
			set_grand_total_to_default_mop: 1,
			selling_price_list: "",
			terminal: shift.terminal || "",
		};
	}

	function boot(wrapper) {
		install_shims();
		if (!O.Controller) {
			frappe.msgprint(__("收银台资源没加载完（official-*.js）。"));
			return;
		}
		return new (O.PearnlyController || O.Controller)(wrapper);
	}

	// 等官方文件都挂上命名空间之后再装子类（`frappe.require` 的回调里调 install()）。
	function install() {
		if (O.pearnly_installed) return O.PearnlyController;
		O.pearnly_installed = true;
		install_shims();

		class PearnlyController extends O.Controller {
			// 开班：官方读 POS Opening Entry；我们读自己的班次（`api_current_shift`）。
			fetch_opening_entry() {
				return p.call(api + "api_current_shift");
			}
			check_opening_entry() {
				this.fetch_opening_entry().then(shift => {
					if (!shift) {
						this.wrapper.html(`<div class="text-muted p-4">${
							frappe.utils.escape_html(__("请先在收银台开班（旧页面那条路）。"))}</div>`);
						return;
					}
					this.prepare_app_defaults(shift);
				});
			}
			// 设置：官方 = 开班凭证 + POS Profile；我们 = 班次 + 终端。
			async prepare_app_defaults(shift) {
				this.pos_opening = shift.name;
				this.company = shift.company;
				this.pos_profile = shift.terminal;
				this.pos_opening_time = shift.opened_at;
				this.item_stock_map = {};
				this.allow_negative_stock = false;
				this.settings = Object.assign(await our_settings(shift), {
					frm_doctype: "ERP Sale",
					invoice_fields: [],
					customer_groups: [],
				});
				this.make_app();
			}
			// 删减：我们不做"过期开班提示""交班实时推送""附加信息字段配置"。
			setup_listener_for_pos_closing() {}
			check_outdated_pos_opening_entry() {}
			fetch_invoice_fields() { this.settings.invoice_fields = []; }
			// 单据：官方用 POS Invoice 的 `items`；我们是 `ERP Sale` 的 `lines` —— 同一个数组两个名字。
			get_new_frm(_frm, doctype = this.settings.frm_doctype) {
				const frm = super.get_new_frm(_frm, doctype);
				if (frm.doc) {
					if (!frm.doc.items) frm.doc.items = frm.doc.lines || (frm.doc.lines = []);
					// 官方整块界面按 `doc.currency` 出符号；我们的 `ERP Sale` 没有这个栏位，
					// 不给就会掉到框架默认货币（实测出过 `د.إ`）→ 这里钉成站点货币。
					frm.doc.currency = frm.doc.currency || (this.settings && this.settings.currency) || "THB";
				}
				return frm;
			}
		}

		class PearnlyItemSelector extends O.ItemSelector {
			// 商品：官方 get_items；我们 api_counter_products（字段映射见 to_official_item）。
			// **注意**：官方这里返回的是 `frappe.call(...)` 那个 **jQuery promise**（调用方用了 `.always`），
			// 所以要给 jQuery 的 deferred，不能给原生 Promise —— 给错了一次，页面直接"没找到物料"。
			get_items({ start = 0, page_length = 40, search_term = "" }) {
				const deferred = $.Deferred();
				frappe.call({
					method: api + "api_counter_products",
					args: { terminal: this.pos_profile, search: search_term || undefined,
						start, limit: page_length },
					// 调用方要的是 `message.items`（对象里的数组，不是数组本身）：
					//   `.then(({ message }) => { const { items } = message; render_item_list(items) })`
					callback: (r) => {
						const raw = ((r && r.message) || {}).items || [];
						const items = raw.map(to_official_item);
						deferred.resolve({ message: { items, filtered_items: items } });
					},
					error: () => deferred.reject(),
				});
				return deferred.promise();
			}
		}

		class PearnlyItemCart extends O.ItemCart {
			// 客户资料：官方走 erpnext…set_customer_info（我们删减）；这里只给"读得到"的那点信息。
			fetch_customer_details(customer) {
				if (!customer) return Promise.resolve({});
				return frappe.db.get_value("ERP Customer", customer,
					["customer_name", "contact_phone", "email_id"]).then(r => ({
						customer, customer_name: r.message?.customer_name || customer,
						mobile_no: r.message?.contact_phone || "", email_id: r.message?.email_id || "",
					}));
			}
		}

		class PearnlyPayment extends O.Payment {
			// 提交：官方走 POS Invoice 的 submit；我们走 api_create_sale（把客户端文档翻成我们的入参）。
			submit_invoice() {
				const doc = this.events.get_frm().doc;
				const items = (doc.items || []).map(row => ({
					product: row.item_code, qty: row.qty, unit: row.uom,
					unit_price: row.rate, discount_percent: row.discount_percentage || 0,
				}));
				return p.call(api + "api_create_sale", {
					terminal: this.pos_profile, customer: doc.customer || undefined,
					items, payments: (doc.payments || []).map(x => ({ method: x.mode_of_payment, amount: x.amount })),
					discount_percent: doc.additional_discount_percentage || 0,
					client_uuid: doc.client_uuid,
				}, "POST");
			}
		}

		O.PearnlyController = PearnlyController;
		O.PearnlyItemSelector = PearnlyItemSelector;
		O.PearnlyItemCart = PearnlyItemCart;
		O.PearnlyPayment = PearnlyPayment;
		// 让官方 Controller 里 `new erpnext.PointOfSale.ItemSelector/ItemCart/Payment(...)` 拿到我们的子类。
		O.ItemSelector = PearnlyItemSelector;
		O.ItemCart = PearnlyItemCart;
		O.Payment = PearnlyPayment;
		return PearnlyController;
	}

	p.pos_next = { boot, install };

	// 临时对照路由的加载器：`/desk/pearnly-cashier?pearnly_pos_next=1` 时由 `cashier.js` 调它。
	// 官方 9 个文件 → 官方样式 → 本适配层（顺序不能反：适配层要在官方文件之后装子类）。
	const FILES = [
		"onscan.js",
		"official-pos_number_pad.js",
		"official-pos_item_selector.js",
		"official-pos_item_details.js",
		"official-pos_item_cart.js",
		"official-pos_payment.js",
		"official-pos_past_order_list.js",
		"official-pos_past_order_summary.js",
		"official-pos_controller.js",
	];
	// 注：`official-point_of_sale.js`（官方那条 `/desk/point-of-sale` 路由的入口）**不加载** ——
	// 它只做"官方站点的页面注册 + 启动控制器"，而启动由我们这边负责（见 `pos_next_route`）。
	p.pos_next_route = (page) => {
		const base = "/assets/pearnly_erp_portal/desk/pos/";
		frappe.require([base + "official-pos.css", ...FILES.map(f => base + f)], () => {
			O.pearnly_installed = false;           // 让 install() 这一轮真的装一遍
			p.pos_next.install();
			const wrapper = page.wrapper;
			wrapper.page = page;                   // 官方 Controller 读 `wrapper.page`
			wrapper.pos = p.pos_next.boot(wrapper);
			window.cur_pos = wrapper.pos;
		});
	};
})();
