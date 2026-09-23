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
window.__pos_adapter_v = 28;   // 排查用：确认页面到底加载到哪一版（下一轮可删）

(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.cashier.api.";
	const NS = (window.erpnext = window.erpnext || {});
	const O = (NS.PointOfSale = NS.PointOfSale || {});

	// 官方代码里的子表名/父字段 → 我们的（`add_child(doc, "POS Invoice Item", "items")` 那一套）。
	// 散客哨兵：只在"借过官方那道'必须先选客户'的闸"时用一下，提交前会被丢掉。
	const WALK_IN = "__pearnly_walk_in__";
	// 商品名 → 可用量（来自我们商品区那一页的回参；见 `to_official_item` 与库存那条映射）。
	const available_map = {};
	// 商品编码 → 名字 / 图片 / 单位。**为什么要单独存一份**：官方点击商品时传给
	// `on_cart_update` 的 `item` 只有 `item_code / uom / rate / stock_uom / batch_no / serial_no`
	// （见官方 `item_selector` 里那次 `item_selected`），**没有名字与图片**；而购物车行与明细面板
	// 读的正是行上的 `item_name / image`。这两样只有我们商品卡那份数据里有，所以在这里留一份。
	const item_meta = {};

	// 支付方式：与 `ERP Sale Payment.method` **同一个集合**（`cashier/contracts.py` 的
	// `PAYMENT_METHODS`），顺序照官方收款面板（现金 / 刷卡 / 扫码 / 转账）。
	// 界面上的字走**我们自己的词条**（泰语为主，`p.texts` 那张表）；落单时再还原成服务端的 key。
	const PAY_METHODS = [
		{ key: "cash", term: "trade.methodCash", type: "Cash", default: 1 },
		{ key: "card", term: "trade.methodCard", type: "Bank" },
		{ key: "promptpay", term: "trade.methodPromptpay", type: "Bank" },
		{ key: "transfer", term: "trade.methodTransfer", type: "Bank" },
	];
	// 本地化之后的这一份（`our_settings()` 里解析一次；界面、还原 key、落单都用它）。
	let payment_methods = [];

	function method_key(label) {
		const row = payment_methods.find(m => m.label === label);
		return row ? row.key : undefined;
	}

	// 官方的单据子表叫 `items`，我们叫 `lines` —— 要的是**同一个数组**，不是"赋一次值"。
	// 赋一次值会被框架刷新单据时换掉（实测：行落在 `lines` 里，官方读 `items` 读到 0，
	// 表现就是"点商品不加货"）。用属性别名，永远指向同一个数组。
	function alias_items(doc) {
		if (!doc || doc.__pearnly_items_alias) return doc;
		Object.defineProperty(doc, "items", {
			configurable: true,
			get() { return this.lines || (this.lines = []); },
			set(value) { this.lines = value; },
		});
		doc.__pearnly_items_alias = true;
		return doc;
	}
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

		seed_meta();

		// 排查开关：`?pearnly_pos_debug=1` 时，把"客户端读了哪张 DocType 的元数据/记录"记在
		// `window.__pos_dt_log` 里。上一轮就是因为读不出"哪一个 doctype 被权限挡下"卡住的。
		if (String(window.location.search || "").includes("pearnly_pos_debug")) {
			window.__pos_dt_log = [];
			window.__pos_route_log = [];
			window.__pos_call_log = [];
			// 把每一个 `frappe.call` 的方法名与失败原因都记下来（403 那种会顺带把页面路由重置）。
			const call_probe = frappe.call.bind(frappe);
			frappe.call = function (opts) {
				const method = typeof opts === "string" ? opts : (opts && opts.method);
				const out = call_probe(opts);
				if (out && out.fail) {
					out.fail(err => window.__pos_call_log.push({
						method,
						args: JSON.stringify((opts && opts.args) || {}).slice(0, 160),
						status: err && err.status,
						msg: String((err && (err.statusText || err.message)) || "").slice(0, 80),
						server: String(((err && err.responseJSON) || {})._server_messages || "").slice(0, 200),
					}));
				}
				return out;
			};
			const set_route = frappe.set_route.bind(frappe);
			frappe.set_route = function (...args) {
				window.__pos_route_log.push({
					args: JSON.stringify(args).slice(0, 120),
					stack: String(new Error().stack || "").split("\n").slice(1, 7).join(" | "),
				});
				return set_route(...args);
			};
			const router = frappe.router && frappe.router.set_route;
			if (router) {
				frappe.router.set_route = function (...args) {
					window.__pos_route_log.push({
						via: "router.set_route",
						args: JSON.stringify(args).slice(0, 120),
						stack: String(new Error().stack || "").split("\n").slice(1, 7).join(" | "),
					});
					return router.apply(frappe.router, args);
				};
			}
			const note = (kind) => (dt) => window.__pos_dt_log.push(kind + ":" + dt);
			const with_dt = frappe.model.with_doctype.bind(frappe.model);
			frappe.model.with_doctype = function (dt, cb) { note("with_doctype")(dt); return with_dt(dt, cb); };
			for (const fn of ["get_doc", "get_value", "get_single_value"]) {
				if (!frappe.db[fn]) continue;
				const orig = frappe.db[fn].bind(frappe.db);
				frappe.db[fn] = function (dt, ...rest) { note("db." + fn)(dt); return orig(dt, ...rest); };
			}
		}

		const add_child = frappe.model.add_child.bind(frappe.model);
		frappe.model.add_child = (doc, doctype, parentfield, idx) =>
			add_child(doc, CHILD_DOCTYPE[doctype] || doctype, PARENTFIELD[parentfield] || parentfield, idx);

		// `Item` 是我们**没有**的表（不装 erpnext）。官方明细面板会问
		// `frappe.db.get_value("Item", item_code, ["has_serial_no", "has_batch_no"])`，
		// 一次未授权的读 → 403 弹窗（它的提示还带 `re_route`）。我们的商品没有序列号/批号，
		// 所以这里直接答"都没有"，不再发那次请求。
		const db_get_value = frappe.db.get_value.bind(frappe.db);
		frappe.db.get_value = function (doctype, ...rest) {
			if (doctype === "Item") {
				return Promise.resolve({ message: { has_serial_no: 0, has_batch_no: 0 } });
			}
			// 官方那张**单据汇总**会问客户邮箱：`get_value("Customer", <客户>, "email_id")`。
			// 我们没有 `Customer` 这张表，所以翻成我们的 `ERP Customer`、字段名也翻
			// （`contact_email`），结果再按官方那个名字回 —— 官方那句读法一个字没改。
			// 散客（客户是空）直接答空：不能让 `message` 是 undefined，官方那句读法会当场炸。
			if (doctype === "Customer") {
				const name = rest[0];
				if (!name) return Promise.resolve({ message: { email_id: "" } });
				return db_get_value("ERP Customer", name, "contact_email")
					.then(out => ({ message: { email_id: (out && out.message) || "" } }))
					.catch(() => ({ message: { email_id: "" } }));
			}
			return db_get_value(doctype, ...rest);
		};

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
			// 库存可用量：我们没有单独那个接口，也不需要 —— 商品区那一页已经带了每个商品的
			// `qty_on_hand`，装货时顺手存进 `available_map`，这里直接答；落单时服务端仍会再校验。
			//
			// **必须是三元数组**：官方 `check_stock_availability()` 读的是
			// `resp[0]` 可用量 / `resp[1]` 是否库存商品 / `resp[2]` 是否允许负库存，
			// 明细面板那格仓库的 onchange 也读 `[0]` 与 `[1]`。
			// 早先我们只回了**一个数字** → 官方第 807 行 `available_qty.toString()` 直接抛
			// `Cannot read properties of undefined`，而这个错被官方 `on_cart_update` 里那个
			// 空 `catch` 吞掉，所以"货已经加进单据了、购物车 DOM 却一次都不画"。
			//
			// 第三位给**真**是我们既有的口径：`inventory/stock.py` 的 `apply_movement(allow_negative=True)`
			// —— 允许超卖、告警在上层，收银台不因库存拦单。给了真，官方
			// `if (is_negative_stock_allowed) return;` 就会提前返回，不会弹"库存不足"。
			// 可用量只用于界面显示（`Available Qty` 那一格）。
			"erpnext.accounts.doctype.pos_invoice.pos_invoice.get_stock_availability": (args) => ({
				message: [available_map[(args && args.item_code) || ""] ?? 0, 0, 1],
			}),
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

	// ── 客户端元数据：**不申请权限**，自带一份字段定义给搬来的官方界面用 ────────────────
	// 官方收银台要 `frappe.get_meta(子表).fields` 来画明细面板的输入框，而我们的 DocType 权限
	// 只给了 System Manager（我们的页面平时**不读元数据**，所以以前没撞上）。这里按框架自己的
	// 机制把一份"我们支持的字段"种进客户端缓存：`locals.DocType[...]` + `frappe.meta.sync()`，
	// 于是 `with_doctype` 直接短路、不发那次会被挡下的请求，权限模型一个字没动。
	// **要加字段就在这里加**（这也是"我们给收银台支持哪些字段"的唯一清单）。
	function df(parent, fieldname, label, fieldtype, extra = {}) {
		return Object.assign({ parent, fieldname, label, fieldtype }, extra);
	}
	function seed_meta() {
		if (frappe.model.pearnly_meta_seeded) return;
		frappe.model.pearnly_meta_seeded = true;

		const line = "ERP Sale Line";
		const payment = "ERP Sale Payment";
		const sale = "ERP Sale";
		const fields = {
			[sale]: [
				df(sale, "customer", "Customer", "Link", { options: "ERP Customer" }),
				df(sale, "customer_name", "Customer Name", "Data"),
				// 这几格用 **Data** 而不是 Link：我们的 DocType 权限只给 System Manager，
				// Link 控件一渲染就会去读目标表的元数据/记录 → 403 弹窗（实测"单位"那格就是这么炸的）。
				// 界面上它们只需要显示/带值，真正校验在服务端（`api_create_sale`）。
				df(sale, "company", "Company", "Data"),
				df(sale, "terminal", "Terminal", "Data"),
				df(sale, "warehouse", "Warehouse", "Data"),
				df(sale, "currency", "Currency", "Link", { options: "Currency" }),
				df(sale, "posting_date", "Date", "Date"),
				df(sale, "posting_time", "Time", "Time"),
				df(sale, "items", "Items", "Table", { options: line }),
				df(sale, "lines", "Lines", "Table", { options: line }),
				df(sale, "payments", "Payments", "Table", { options: payment }),
				df(sale, "taxes", "Taxes", "Table", { options: "ERP Sale Tax" }),
				df(sale, "net_total", "Net Total", "Currency"),
				df(sale, "total_taxes_and_charges", "Total Taxes and Charges", "Currency"),
				df(sale, "grand_total", "Grand Total", "Currency"),
				df(sale, "rounded_total", "Rounded Total", "Currency"),
				df(sale, "total_qty", "Total Quantity", "Float"),
				df(sale, "discount_amount", "Discount Amount", "Currency"),
				df(sale, "additional_discount_percentage", "Additional Discount Percentage", "Percent"),
				df(sale, "paid_amount", "Paid Amount", "Currency"),
				df(sale, "outstanding_amount", "Outstanding Amount", "Currency"),
				df(sale, "change_amount", "Change Amount", "Currency"),
				df(sale, "status", "Status", "Data"),
				df(sale, "receipt_no", "Receipt No", "Data"),
				df(sale, "client_uuid", "Client Reference", "Data"),
				df(sale, "remarks", "Remarks", "Small Text"),
			],
			[line]: [
				df(line, "item_code", "Item Code", "Data"),
				df(line, "item_name", "Item Name", "Data"),
				df(line, "qty", "Quantity", "Float"),
				df(line, "uom", "UOM", "Data"),
				df(line, "stock_uom", "Stock UOM", "Data"),
				df(line, "rate", "Rate", "Currency"),
				df(line, "amount", "Amount", "Currency"),
				df(line, "price_list_rate", "Price List Rate", "Currency"),
				df(line, "discount_percentage", "Discount (%)", "Percent"),
				df(line, "discount_amount", "Discount Amount", "Currency"),
				df(line, "conversion_factor", "Conversion Factor", "Float"),
				df(line, "warehouse", "Warehouse", "Data"),
				df(line, "actual_qty", "Available Qty", "Float"),
			],
			[payment]: [
				df(payment, "mode_of_payment", "Mode of Payment", "Data"),
				df(payment, "amount", "Amount", "Currency"),
				df(payment, "type", "Type", "Data"),
				df(payment, "default", "Default", "Check"),
			],
		};
		locals.DocType = locals.DocType || {};
		for (const [name, dfs] of Object.entries(fields)) {
			// 框架要的那几个标志位给全：能读、能写、不是单页、不是子表（子表由 parent 表达）。
			const meta = {
				name, doctype: "DocType", istable: 0, issingle: 0, is_submittable: 0,
				quick_entry: 0, track_changes: 0, fields: dfs,
				permissions: [{ read: 1, write: 1, create: 1, role: "All" }],
			};
			locals.DocType[name] = meta;
			frappe.meta.sync(meta);
			// 子表还要把 df 挂到父表的 `__ doctype` 里一份，框架画 Table 控件时读它。
		}
	}

	// 「一条我们的商品」→「官方商品卡要的字段」（`get_item_html` 读这几个）。
	function to_official_item(row) {
		// 我们那一页的可用量字段叫 `qty_on_hand`（`cashier.counter.products_page`），官方叫 `actual_qty`。
		// 顺手存一份到 `available_map`：官方后面问"这个商品还有多少"（`get_stock_availability`）时用它答。
		const on_hand = Number(row.qty_on_hand ?? 0);
		available_map[row.name] = on_hand;
		item_meta[row.name] = {
			item_name: row.product_name || row.name,
			item_image: row.image_url || "",
			uom: row.unit || "",
			stock_uom: row.base_unit || row.unit || "",
		};
		return {
			item_code: row.name,
			item_name: row.product_name || row.name,
			item_image: row.image_url || "",
			stock_uom: row.unit || "",
			uom: row.unit || "",
			price_list_rate: Number(row.unit_price || 0),
			actual_qty: on_hand,
			has_batch_no: 0,
			has_serial_no: 0,
			barcode: row.barcode || "",
			// 官方商品卡的价签是 `format_currency(rate, item.currency, …)`。官方那边的
			// `item.currency` 由服务端 `get_items` 带出来；我们不带，`undefined` 就会让
			// 框架去缓存里抓**第一条**货币当默认（本机实测抓成 `د.إ`）。
			currency: site_currency(),
			// 官方的库存角标只在 `is_stock_item` 为真时才画（>10 绿 / ≤0 红 / 其余橙）。
			// 我们的商品都是要盘点的实物，照官方语义就是真。
			is_stock_item: 1,
		};
	}

	// 站点货币：优先框架 boot 里那个（补齐见 `pearnly_core` 的 v0_0_4 补丁），否则用我们自己的。
	function site_currency() {
		return (frappe.boot.sysdefaults || {}).currency || "THB";
	}

	// 一行的实收（照 ERPNext 的口径）：折% 优先，没有百分比才用折额。
	function line_amount(row) {
		const gross = flt(row.qty) * flt(row.rate);
		const disc = flt(row.discount_percentage)
			? gross * flt(row.discount_percentage) / 100
			: flt(row.discount_amount);
		return gross - disc;
	}

	// 付款屏右下角那三个数（总计 / 付款金额 / 剩余或找零）。
	// 官方的 POS Invoice 是**服务端**算好 `paid_amount` 的；我们这张客户端单据没有那一步，
	// 所以按我们自己的口径算：实收 = Σ 各方式的金额，找零只可能从现金里出来
	// （与 `cashier/contracts.py` 的 `settle()` 同一口径，最终也由它在服务端再核一遍）。
	function recalc_payment_amounts(doc) {
		const paid = (doc.payments || []).reduce((sum, row) => sum + flt(row.amount), 0);
		const grand = flt(doc.rounded_total != null ? doc.rounded_total : doc.grand_total);
		doc.paid_amount = paid;
		doc.outstanding_amount = Math.max(grand - paid, 0);
		doc.change_amount = paid > grand ? paid - grand : 0;
		return doc;
	}

	// 付款方式那几行（`doc.payments`）：官方的它们是**服务端** `set_pos_data` 从 POS Profile 带出来的。
	// 我们按自己的方式表给上，界面读的就是这几行；金额一律从 0 起，由收银员敲。
	function seed_payments(frm) {
		const doc = frm.doc;
		if ((doc.payments || []).length) return;
		payment_methods.forEach((m) => {
			frm.add_child("payments", {
				mode_of_payment: m.label, type: m.type, amount: 0, default: m.default || 0,
			});
		});
		recalc_payment_amounts(doc);
	}

	// 我们的收银台设置 → 官方 `settings` 的形状（官方读到的那些开关）。
	async function our_settings(shift) {
		const terminals = await p.call(api + "api_list_terminals");
		const terminal = (terminals?.terminals || []).find(t => t.name === shift.terminal)
			|| (terminals?.terminals || [])[0] || {};
		// 方式名要本地化（泰语为主）：`p.texts` 拿的就是站点那份词条表，与页面同一来源。
		const t = await p.texts("trade.");
		payment_methods = PAY_METHODS.map(m => Object.assign({}, m, { label: t(m.term) }));
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
			frappe.msgprint(__("The cashier assets did not load (official-*.js)."));
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
							frappe.utils.escape_html(__("Open a shift on the cashier page first."))}</div>`);
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
			// 删减（**不暴露没承接的入口**）：官方这两项菜单在我们这边都没有落点 ——
			// 「打开表单视图」要打开这张客户端单据的服务端表单（我们没有那种表单），
			// 「关闭收银台」要开一张 `POS Closing Entry`（我们的交班是自己的 `ERP Shift` 页面，
			// 走侧栏那条路）。它们各自还绑着快捷键（⌘+F / ⇧+⌘+C），所以整段菜单只清空、不加那两项。
			prepare_menu() {
				this.page.clear_menu();
			}
			// 单据：官方用 POS Invoice 的 `items`；我们是 `ERP Sale` 的 `lines` —— 同一个数组两个名字。
			get_new_frm(_frm, doctype = this.settings.frm_doctype) {
				const frm = super.get_new_frm(_frm, doctype);
				alias_items(frm.doc);
				// 官方 `checkout()` 里有一句 `frm.cscript.calculate_outstanding_amount()`
				// （POS Invoice 的做法）。我们这张客户端单据没有那个 cscript，就给它一个同名实现 ——
				// 官方那句调用一个字没改，付款屏那三个数由我们自己的口径算。
				frm.cscript = Object.assign(frm.cscript || {}, {
					calculate_outstanding_amount: () => recalc_payment_amounts(frm.doc),
				});
				return frm;
			}
			// 官方每次"新建/重开单据"都会再摆一遍设置：**货币要钉在这一拍上** ——
			// 不然框架给 `doc.currency` 填的默认值会把我们钉的顶掉（实测出过 `د.إ`）。
			set_pos_profile_data() {
				const out = super.set_pos_profile_data();
				if (this.frm && this.frm.doc) {
					this.frm.doc.currency = (frappe.boot.sysdefaults || {}).currency || "THB";
					// 单据汇总右上角那个状态徽章读的是 `status`（官方 POS Invoice 是 Paid/Consolidated…）。
					// 我们的收银单一律"当场付清"，对应的就是官方那个 `Paid`（绿）—— 也是我们销售记录里
					// 收款状态那一列的同一个词。不写它，徽章会渲染成 `__("undefined")`。
					this.frm.doc.status = "Paid";
					alias_items(this.frm.doc);
					seed_payments(this.frm);
				}
				return out;
			}

			// **结账不落任何东西**（用户 2026-09-24 口径："不存草稿，过账发生在最后一环节"）。
			// 官方这一步会先 `frm.save()` 存一张**草稿单**，存成功才切付款屏；存的又是我们这张
			// `ERP Sale`，而客户端文档上带的是官方字段名（行上是 `rate`、整单没有 `sold_at`），
			// 于是被框架自己的必填校验挡下 —— 点结账弹的就是那句"请填写必填字段：销售时间"。
			// 我们产品里本来也没有"草稿单"这种状态：单据由 `api_create_sale` 在**完成订单**时
			// 一次落定（写单 + 出库存 + 提交），所以这里只切屏。
			save_and_checkout() {
				this.payment.checkout();
			}

			// 落单：官方在 `init_payments()` 里把"提交"写成 `frm.savesubmit()`（POS Invoice 的路），
			// 我们换成自己的接口 —— 官方 payment 里那句 `this.events.submit_invoice()` 一个字没动，
			// 换掉的是**我们这一侧的事件实现**。
			init_payments() {
				super.init_payments();
				this.payment.events.submit_invoice = () => this.submit_our_sale();
			}

			async submit_our_sale() {
				const doc = this.frm.doc;
				const lines = (doc.items || []).map(row => ({
					product: row.item_code, qty: flt(row.qty), unit: row.uom,
					// 行折扣按**金额**给（`contracts.line_total` 收的就是金额），
					// 与 `line_amount()` 是同一套算法，两边不会打架。
					unit_price: flt(row.rate),
					discount: flt(flt(row.qty) * flt(row.rate)) - line_amount(row),
				}));
				const payments = (doc.payments || [])
					.filter(row => flt(row.amount))
					.map(row => ({ method: method_key(row.mode_of_payment), amount: flt(row.amount) }));
				frappe.dom.freeze();
				try {
					const result = await p.call(api + "api_create_sale", {
						terminal: this.pos_profile,
						// 哨兵客户 = 散客 → 传空（我们的口径：客户留空就是散客）。
						customer: (doc.customer && doc.customer !== WALK_IN) ? doc.customer : undefined,
						lines, payments,
						// 整单折扣按**金额**给（界面那一格是百分比，我们已经算成了金额）。
						discount_total: flt(doc.discount_amount),
						client_uuid: doc.client_uuid,
					}, "POST");
					doc.name = result.name;
					doc.receipt_no = result.receipt_no;
					this.toggle_components(false);
					this.toggle_submitted_invoice_summary(true);
					frappe.show_alert({
						indicator: "green",
						message: __("POS invoice {0} created successfully", [result.receipt_no || result.name]),
					});
				} catch (error) {
					frappe.utils.play_sound("error");
				} finally {
					frappe.dom.unfreeze();
				}
			}
			// 产品口径差异（**按功能放行的第一处**）：官方要求"先选客户才能加货"，
			// 我们这边**客户为空 = 散客**。官方那句判断在 `on_cart_update()` 里，无法只用子类改一行，
			// 所以调用前后**临时借一个哨兵客户**过闸，返回后还原成空；提交时会把哨兵丢掉。
			async on_cart_update(args) {
				// 官方这几格（换算系数 / 仓库 / 可用量 / 标价）是**服务端**在明细行上带出来的
				// （`set_item_details` 那一套 + POS Profile 的仓库）。我们这边带上：
				// 换算系数 = 1（我们的商品没有多单位换算这一步）、仓库 = 终端默认仓库、
				// 可用量与标价来自商品卡那份数据。否则明细面板那四格是空的（实测如此）。
				const item = args && args.item;
				if (item && item.item_code) {
					item.price_list_rate = item.price_list_rate ?? flt(item.rate);
					item.actual_qty = item.actual_qty ?? (available_map[item.item_code] ?? 0);
					item.stock_uom = item.stock_uom || item.uom;
				}
				const row = await (async () => {
					if (this.frm.doc.customer) return super.on_cart_update(args);
					this.frm.doc.customer = WALK_IN;
					try {
						return await super.on_cart_update(args);
					} finally {
						if (this.frm.doc.customer === WALK_IN) this.frm.doc.customer = "";
					}
				})();
				if (row && row.item_code) {
					// 名字 / 图片 / 单位：官方这几格是**服务端** `set_item_details` 补在行上的
					// （商品名、商品图 `image`、单位），我们这张客户端单据没有那一步 ——
					// 不补的话购物车行只有数量与金额、**没有物料名**，明细面板那格是个空缩写框，
					// 商品图也不会出现（实测 `.item-abbr` 是空的）。商品卡那份数据里就有这几样。
					const picked = (args && args.item) || {};
					const meta = item_meta[row.item_code] || {};
					row.item_name = row.item_name || picked.item_name || meta.item_name || row.item_code;
					row.image = row.image || picked.item_image || meta.item_image || "";
					row.uom = row.uom || picked.uom || meta.uom || "";
					row.stock_uom = row.stock_uom || picked.stock_uom || meta.stock_uom || row.uom;
					row.conversion_factor = flt(row.conversion_factor) || 1;
					row.warehouse = row.warehouse || (this.settings && this.settings.warehouse) || "";
					// 价格要**数字**：商品卡上的 `data-rate` 是 DOM 字符串，官方汇总屏那句
					// `item_data.rate !== item_data.price_list_rate` 是严格比较，
					// 字符串 "100" 与数字 100 于是被判成"这行有折扣"，画出 `(undefined% off)`。
					row.rate = flt(row.rate);
					row.price_list_rate = flt(row.price_list_rate ?? row.rate);
					row.discount_percentage = flt(row.discount_percentage || 0);
					row.discount_amount = flt(row.discount_amount || 0);
					row.actual_qty = row.actual_qty ?? (available_map[row.item_code] ?? 0);
					if (this.is_current_item_being_edited(row)) this.edit_item_details_of(row);
					// 官方在 `super.on_cart_update()` 里**已经画过一次**购物车 —— 那一画笔的是
					// 还没补名字/图片/单位的那一行（实测行上只有数量与金额、名字是空的）。
					// 补完再画一次；官方那套渲染一个字没改，只是让它拿到完整的行。
					this.update_cart_html(row);
				}
				return row;
			}
		}

		class PearnlyItemSelector extends O.ItemSelector {
			// 官方这一段会去读 `POS Profile.selling_price_list`（`frappe.db.get_value("POS Profile", …)`）。
			// 我们没有 POS Profile（刻意不装 erpnext 的那套主数据），那次读取会报"未授权访问单据类型"。
			// 我们的价格来自商品自己的 `unit_price`、**不走价目表**，所以这里先把 `price_list` 填上一个非空值，
			// 官方的 `if (!this.price_list)` 分支就不会进，其余流程原样。
			async load_items_data() {
				if (!this.price_list) this.price_list = "Standard Selling";
				return super.load_items_data();
			}
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
			// 客户那一格：官方"没有客户"就重画一个**输入框并聚焦**（所以一进页面下拉就张着、盖住购物车），
			// 它默认客户是 POS Profile 上配的。我们产品**没有默认客户这一位**，口径是"客户留空 = 散客"，
			// 所以空态画**散客卡**（样式与官方那张客户卡同一套标记），点卡片才打开搜索框。
			init_customer_selector() {
				this.$component.append(`<div class="customer-section"></div>`);
				this.$customer_section = this.$component.find(".customer-section");
				this.customer_info = this.customer_info || {};
				this.update_customer_section();
			}
			update_customer_section() {
				const info = this.customer_info || {};
				if (info.customer) return super.update_customer_section();
				const walk_in = __("Walk-in customer");
				this.$customer_section.html(`<div class="customer-details">
					<div class="customer-display">
						<div class="customer-image customer-abbr">${frappe.utils.escape_html(frappe.get_abbr(walk_in))}</div>
						<div class="customer-name-desc">
							<div class="customer-name">${frappe.utils.escape_html(walk_in)}</div>
							<div class="customer-desc">${frappe.utils.escape_html(__("Click to choose a customer"))}</div>
						</div>
						<div class="reset-customer-btn">
							<svg width="32" height="32" viewBox="0 0 14 14" fill="none">
								<path d="M4.93764 4.93759L7.00003 6.99998M9.06243 9.06238L7.00003 6.99998M7.00003 6.99998L4.93764 9.06238L9.06243 4.93759" stroke="#8D99A6"/>
							</svg>
						</div>
					</div>
				</div>`);
			}
			// 点卡片（官方 `.customer-display` 那条路）→ 散客态就打开搜索框；有客户走官方那套。
			toggle_customer_info(show) {
				if ((this.customer_info || {}).customer) return super.toggle_customer_info(show);
				if (show) {
					this.make_customer_selector();
					this.customer_field && this.customer_field.set_focus();
				}
			}
			// 叉掉客户 → 我们的口径是回到散客卡（官方是回到输入框）。
			reset_customer_selector() {
				const frm = this.events.get_frm();
				frm.set_value("customer", "");
				this.customer_info = {};
				this.update_customer_section();
			}

			// 官方的"净总计/总计/物料数"是**服务端**在 POS Invoice 上算好、写进 doc 的
			// （`calculate_taxes_and_totals`）。我们这张客户端 `ERP Sale` 没有那一步，
			// 所以在这里按**我们的口径**先算出来（数量、净额 = Σ 数量×单价、整单折扣、总计），
			// 再交给官方那套渲染 —— 这样官方那几行数字就有值，而不是一直 0。
			// 税费行（D1）暂时空着：我们的收银价是含税价，逐条税明细等做到那一步再接。
			update_totals_section(frm) {
				// 官方允许不带参数调（它自己去 `events.get_frm()`），我们的重算也要跟上，
				// 否则不带参数时这一段被跳过：合计不重算，界面还显示原价。
				if (!frm) frm = this.events.get_frm();
				if (frm && frm.doc) {
					const lines = frm.doc.items || [];
					// 行金额照 ERPNext 的口径：折% 优先，没有百分比才用折额。
					const net = lines.reduce((sum, row) => sum + line_amount(row), 0);
					const pct = flt(frm.doc.additional_discount_percentage);
					const discount = net * pct / 100;
					frm.doc.total_qty = lines.reduce((sum, row) => sum + flt(row.qty), 0);
					frm.doc.net_total = net;
					frm.doc.discount_amount = discount;
					frm.doc.grand_total = net - discount;
					frm.doc.rounded_total = net - discount;
					frm.doc.taxes = frm.doc.taxes || [];
				}
				return super.update_totals_section(frm);
			}

			// 订单折扣那一格改完，官方**不自己调**重算 —— 它等 `refresh-fields` 事件
			// （`official-pos_item_cart.js` 的 `attach_refresh_field_event`），而那个事件由
			// 表单 Layout 的刷新发出来（`frappe/form/layout.js:373`）。我们这张客户端
			// `ERP Sale` 没有 Layout，事件永远不会响 —— 表现就是"填了 12%，合计还是原价"
			// （实测 `doc.additional_discount_percentage = 12`，而 `grand_total` 仍是 100）。
			// 这里在官方那条路上补一脚：改完按我们自己的口径重算一次合计。
			hide_discount_control(discount) {
				const out = super.hide_discount_control(discount);
				this.update_totals_section();
				return out;
			}

			// 客户选择框：官方那份指向 **ERPNext 的 `Customer`**，我们没有这张表 —— 它一渲染就会
			// 发一次 `search_link(doctype="Customer")`，被 403 挡下，而框架的 403 提示带 `re_route: true`，
			// 于是**关掉提示框会把页面路由重置**（实测就是"点商品跳到 /desk"的真凶）。
			// 这里整段照官方抄一遍，只把 `options` 换成我们的 `ERP Customer`（我们的客户表对这角色可搜）。
			make_customer_selector() {
				this.$customer_section.html(`<div class="customer-field"></div>`);
				const me = this;
				this.customer_field = frappe.ui.form.make_control({
					df: {
						label: __("Customer"),
						fieldtype: "Link",
						options: "ERP Customer",
						placeholder: __("Search by customer name, phone, email."),
						onchange: function () {
							if (!this.value) return;
							const frm = me.events.get_frm();
							frappe.dom.freeze();
							frappe.model.set_value(frm.doc.doctype, frm.doc.name, "customer", this.value);
							frm.script_manager.trigger("customer", frm.doc.doctype, frm.doc.name).then(() => {
								frappe.run_serially([
									() => me.fetch_customer_details(this.value),
									() => me.events.customer_details_updated(me.customer_info),
									() => me.update_customer_section(),
									() => me.update_totals_section(),
									() => frappe.dom.unfreeze(),
								]);
							});
						},
					},
					parent: this.$customer_section.find(".customer-field"),
					render_input: true,
				});
				this.customer_field.toggle_label(false);
			}

			// 客户资料：官方走 erpnext…set_customer_info（我们删减）；这里只给"读得到"的那点信息。
			// 字段名按**我们的** `ERP Customer`：`contact_phone` / `contact_email`
			// （官方那边叫 `mobile_no` / `email_id` —— 用过官方名会被 417 挡下，实测踩过）。
			fetch_customer_details(customer) {
				if (!customer) return Promise.resolve({});
				return frappe.db.get_value("ERP Customer", customer,
					["customer_name", "contact_phone", "contact_email"]).then(r => ({
						customer, customer_name: r.message?.customer_name || customer,
						mobile_no: r.message?.contact_phone || "",
						email_id: r.message?.contact_email || "",
					}));
			}
		}

		class PearnlyPayment extends O.Payment {
			// 官方拿「方式名」当 CSS 类名与控件键：`sanitize_mode_of_payment()` 会把名字里
			// 非字母数字的字符**剔掉**再小写。我们的方式名是本地化标签（泰语为主），照那样剔会
			// 全变成空串，几种方式于是撞成同一个类名、`this[`${mode}_control`]` 也互相覆盖。
			// 所以这里按方式表还原成 ASCII key；界面上的字仍是本地化标签。
			sanitize_mode_of_payment(mode_of_payment) {
				const key = method_key(mode_of_payment);
				return key || super.sanitize_mode_of_payment(mode_of_payment);
			}

			// 付款屏右下角那三格读的是 `doc.paid_amount / grand_total / change_amount`。
			// 官方在 POS Invoice 上有服务端算好的 `paid_amount`；我们每次渲染前按自己口径先算一遍。
			update_totals_section(doc) {
				if (!doc) doc = this.events.get_frm().doc;
				recalc_payment_amounts(doc);
				return super.update_totals_section(doc);
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
