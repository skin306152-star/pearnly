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
window.__pos_adapter_v = 41;   // 排查用：确认页面到底加载到哪一版（下一轮可删）

(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.cashier.api.";
	const NS = (window.erpnext = window.erpnext || {});
	const O = (NS.PointOfSale = NS.PointOfSale || {});

	// 官方代码里的子表名/父字段 → 我们的（`add_child(doc, "POS Invoice Item", "items")` 那一套）。
	// 散客哨兵：只在"借过官方那道'必须先选客户'的闸"时用一下，提交前会被丢掉。
	const WALK_IN = "__pearnly_walk_in__";
	// 没开班时那张空态图上的图标：**官方 POS 自己那枚**（erpnext 16.34.1
	// `public/images/pos.svg`，与 `official-*.js` 同一份 GPLv3 素材）。
	// 照原样内联、只把尺寸交给 CSS（`fill="currentColor"` 让它跟着文字色走），不进构建。
	const POS_ICON = `<svg class="pearnly-pos-empty-icon" viewBox="0 0 100 100" fill="currentColor"
		xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M73.617,42.921H53.671c-0.788,0-1.423,0.636-1.423,1.423v7.238c0,0.788,0.635,1.424,1.423,1.424h19.946 c0.788,0,1.429-0.636,1.429-1.424v-7.238C75.046,43.557,74.405,42.921,73.617,42.921z M72.194,50.16H55.099v-4.392h17.096V50.16z M58.494,57.78c0,0.788-0.635,1.423-1.423,1.423h-3.4c-0.788,0-1.423-0.635-1.423-1.423s0.635-1.429,1.423-1.429h3.4 C57.859,56.352,58.494,56.992,58.494,57.78z M66.771,57.78c0,0.788-0.636,1.423-1.424,1.423h-3.4c-0.788,0-1.429-0.635-1.429-1.423 s0.641-1.429,1.429-1.429h3.4C66.135,56.352,66.771,56.992,66.771,57.78z M75.046,57.78c0,0.788-0.641,1.423-1.429,1.423h-3.396 c-0.788,0-1.423-0.635-1.423-1.423s0.635-1.429,1.423-1.429h3.396C74.405,56.352,75.046,56.992,75.046,57.78z M58.494,64.058 c0,0.788-0.635,1.429-1.423,1.429h-3.4c-0.788,0-1.423-0.641-1.423-1.429s0.635-1.423,1.423-1.423h3.4 C57.859,62.635,58.494,63.27,58.494,64.058z M66.771,64.058c0,0.788-0.636,1.429-1.424,1.429h-3.4c-0.788,0-1.429-0.641-1.429-1.429 s0.641-1.423,1.429-1.423h3.4C66.135,62.635,66.771,63.27,66.771,64.058z M75.046,64.058c0,0.788-0.641,1.429-1.429,1.429h-3.396 c-0.788,0-1.423-0.641-1.423-1.429s0.635-1.423,1.423-1.423h3.396C74.405,62.635,75.046,63.27,75.046,64.058z M87.271,72.227 c-0.239-13.364-2.135-26.347-5.562-37.713c-0.366-1.2-1.474-2.028-2.729-2.028H66.496V22.334h11.636c1.57,0,2.847-1.276,2.847-2.852 V7.847C80.979,6.276,79.702,5,78.132,5H49.163c-1.576,0-2.852,1.276-2.852,2.847v11.636c0,1.576,1.276,2.852,2.852,2.852h11.634 v10.151H44.255c0.902-1.006,1.896-1.789,2.977-2.257c0.623-0.264,0.971-0.93,0.833-1.591c-0.137-0.666-0.722-1.139-1.398-1.139 H33.028c-0.193,0-0.386,0.041-0.564,0.117c-2.104,0.91-3.896,2.587-5.315,4.87h-6.133c-1.253,0-2.364,0.829-2.725,2.028 c-3.431,11.366-5.327,24.349-5.566,37.713c-1.515,0.066-2.732,1.297-2.732,2.826v17.096c0,1.575,1.276,2.852,2.849,2.852h74.312 c1.576,0,2.852-1.276,2.852-2.852V75.053C90.006,73.523,88.791,72.293,87.271,72.227z M52.012,10.698h23.268v5.938H52.012V10.698z M33.338,30.351h8.998c-2.229,2.389-3.922,5.901-4.85,10.182c-1.281,5.922-0.854,11.94,0.976,15.819H29 c-2.336-3.151-3.134-10.07-1.807-16.201C28.248,35.276,30.482,31.723,33.338,30.351z M23.157,38.189h1.604 c-0.122,0.447-0.251,0.884-0.353,1.357c-1.332,6.161-0.732,12.663,1.332,16.805h-1.271c-0.788,0-1.426,0.641-1.426,1.429 s0.638,1.423,1.426,1.423h3.858H41.06h3.116c0.788,0,1.423-0.635,1.423-1.423s-0.636-1.429-1.423-1.429h-2.389 c-2.14-2.897-2.809-9.241-1.515-15.214c0.227-1.052,0.516-2.018,0.834-2.948H76.84c2.903,10.339,4.529,22.005,4.753,34.012H18.404 C18.627,60.194,20.257,48.528,23.157,38.189z M84.308,89.302H15.694V77.899h68.613V89.302z M45.45,83.598 c0-1.57,1.276-2.846,2.849-2.846h3.4c1.575,0,2.851,1.275,2.851,2.846c0,1.576-1.275,2.853-2.851,2.853h-3.4 C46.726,86.45,45.45,85.174,45.45,83.598z"/></svg>`;
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
	// 我们自己的词条解析器（`p.texts("trade.")` 那一份，泰语为主）。页面动作、开班交班弹窗、
	// 付款方式名都用它 —— 开机时解析一次，之后不再重复请求。
	let term = key => key;
	let term_ready = false;
	async function load_term() {
		if (!term_ready) { term = await p.texts("trade."); term_ready = true; }
		return term;
	}

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
		// 接口回的是**数组**（`frappe.whitelist` 直接把列表回出来），两种形状都认。
		const rows = Array.isArray(terminals) ? terminals : (terminals?.terminals || []);
		// 班次里那个终端在 `shift.shift.terminal`：`api_current_shift` 回的是当班汇总
		// （`{shift: {...}, balances: [...], …}`）。以前读成 `shift.terminal` 一直是 undefined，
		// 靠服务端的默认值兜着 —— 现在按真值取，仓库/公司/终端都对得上这一个柜台。
		const terminal = rows.find(t => t.name === shift.shift.terminal) || rows[0] || {};
		// 方式名要本地化（泰语为主）：`p.texts` 拿的就是站点那份词条表，与页面同一来源。
		const t = await load_term();
		// **这家公司收哪几种钱**由服务端给（建公司时勾的那几种，见 `terminals.list_terminals`）——
		// 付款面板与交班对账都按它来，不把"四种全有"硬写在这儿（用户 2026-09-24 的收银配置）。
		const allowed = (terminal.payment_methods || []).map(key => String(key).trim()).filter(Boolean);
		payment_methods = (allowed.length ? PAY_METHODS.filter(m => allowed.includes(m.key))
			: PAY_METHODS).map(m => Object.assign({}, m, { label: t(m.term) }));
		return {
			// 「这台收银台从哪个仓卖货」：自己配了用自己；没配、而这家公司只有一个仓时，
			// 那个唯一仓就是答案（`api_list_terminals` 现算的 `effective_warehouse`，只读不建）。
			// "默认仓"这个概念已经删掉了，多个仓又没配就是明确报错。
			// 之前读的 `default_warehouse` 是**不存在的字段名** —— 一直是空，靠服务端兜着。
			warehouse: terminal.effective_warehouse || terminal.warehouse || "",
			warehouse_name: terminal.effective_warehouse_name || terminal.warehouse_name || "",
			company: terminal.company || "",
			currency: terminal.currency || "THB",
			// 官方的开关：我们产品没有这些配置位 → 用"我们已有的行为"对应的固定值；
			// 将来要在终端上加一位就改这里（不是改官方代码）。
			hide_images: 0,
			allow_rate_change: 1,
			allow_discount_change: 1,
			allow_partial_payment: 0,
			auto_add_item_to_cart: 1,
			// 「新单」= 这一单清干净重新开始（我们旧页面的行为）。
			// 官方的 `Always Ask` 会弹"有未保存的改动，要保存这张单据吗？"，点"是"就走
			// `frm.save()` —— 那条路在我们这边必然撞必填校验（客户端文档带的是官方字段名），
			// 而且我们根本没有"草稿单"这种状态。所以给一个非那两个值的档位：官方直接开新单、不存。
			action_on_new_invoice: "Create New Invoice",
			print_receipt_on_order_complete: 0,
			set_grand_total_to_default_mop: 1,
			selling_price_list: "",
			terminal: shift.shift.terminal || "",
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
				return this.fetch_opening_entry().then(async shift => {
					this.pearnly_shift = shift || null;
					await load_term();
					if (!shift) {
						// 没开班：**不建收银台界面**，把「开班」放成主按钮（我们之前的做法 ——
						// 收银员点一下就开班，而不是看一句"请先去别处开班"）。
						this.wrapper.html("");
						this.show_no_shift_placeholder();
						this.prepare_page_actions();
						return;
					}
					return this.prepare_app_defaults(shift);
				});
			}
			// 没开班时那块地方**不留一片白**（用户 2026-09-24："pos空态未开班时加官方文案和
			// svg 图标展示"，随后又补："空态下方也加一个按钮，像官方原生那个空态一样，
			// 不要那个添加支付方式啥的"）。所以形状照 Frappe 官方原生空态：
			// **图标 + 一行灰字 + 一颗按钮**；图标仍是官方 POS 自己那枚。
			show_no_shift_placeholder() {
				this.wrapper.html(`<div class="pearnly-pos-empty">
					${POS_ICON}
					<div class="pearnly-pos-empty-hint">${frappe.utils.escape_html(term("trade.noShift"))}</div>
					<button class="btn btn-primary btn-sm pearnly-pos-empty-btn">${
						frappe.utils.escape_html(term("trade.openShift"))}</button>
				</div>`);
				this.wrapper.find(".pearnly-pos-empty-btn").on("click", () => void this.open_shift_here());
			}
			// 设置：官方 = 开班凭证 + POS Profile；我们 = 班次 + 终端。
			async prepare_app_defaults(shift) {
				await load_term();
				this.pearnly_shift = shift;
				// 当班汇总的形状是 `{shift: {...}, balances, sales_count, …}` —— 这几格都在 `shift.shift` 里。
				this.pos_opening = shift.shift.name;
				this.pos_profile = shift.shift.terminal;
				this.pos_opening_time = shift.shift.opened_at;
				this.item_stock_map = {};
				this.allow_negative_stock = false;
				this.settings = Object.assign(await our_settings(shift), {
					frm_doctype: "ERP Sale",
					invoice_fields: [],
					customer_groups: [],
				});
				// 公司取终端那家（当班汇总里没有公司这一位）。
				this.company = this.settings.company;
				this.make_app();
			}
			// 删减：我们不做"过期开班提示""交班实时推送""附加信息字段配置"。
			setup_listener_for_pos_closing() {}
			check_outdated_pos_opening_entry() {}
			fetch_invoice_fields() { this.settings.invoice_fields = []; }
			// **页面动作照我们之前那页来**（用户 2026-09-24："右上角那三个点里之前是什么"）：
			//
			//   菜单（那三个点） = 「交班」
			//   主按钮           = 有班次「新单」／没班次「开班」
			//   次按钮           = 「最近订单 / 隐藏最近订单」
			//   指示器           = 有班次：开班时间 · 应有现金（绿）／没班次：「尚未开班」（橙）
			//
			// 官方的菜单是「打开表单视图 / 关闭收银台」，两项在我们这边都没有落点（一个要打开这张
			// 客户端单据的服务端表单、一个要开 `POS Closing Entry`），所以整段不摆。**主按钮的事项
			// 不放进那三个点里**：开班是没班次时唯一该点的东西，它就得是主按钮。
			prepare_menu() {
				this.page.clear_menu();
			}
			prepare_btns() {
				this.prepare_page_actions();
			}
			// 官方的指示器显示的是**收银台名**（蓝），而且它在 `make_new_invoice()` 里会再盖一次。
			// 我们之前那页显示的是**班次**状态（开班时间 · 应有现金 / 尚未开班）——
			// 这里整段交给 `prepare_page_actions()`，动作与指示器一处定义、每次都用同一份。
			set_pos_profile_status() {
				this.prepare_page_actions();
			}
			prepare_page_actions() {
				const shift = this.pearnly_shift;
				this.page.clear_menu();
				this.page.clear_secondary_action();
				this.page.add_menu_item(term("trade.closeShift"),
					() => void this.close_shift_here(), false);
				if (!shift) {
					this.page.set_indicator(term("trade.noShift"), "orange");
					this.page.set_primary_action(term("trade.openShift"),
						() => void this.open_shift_here());
					return;
				}
				this.page.set_primary_action(term("trade.newInvoice"),
					() => this.new_invoice_event());
				this.page.set_secondary_action(term("trade.recentOrders"),
					() => this.toggle_recent_order());
				this.page.set_indicator(term("trade.shiftOpenedAt", shift.shift.shift_seq,
					p.datetime_text(shift.shift.opened_at), p.money(shift.expected_cash)), "green");
			}
			// 开班 / 交班弹窗与我们旧页面是**同一份**（`desk/pos/pearnly-pos-shift.js`）。
			open_shift_here() {
				return p.pos_shift.open({ t: term, on_done: () => this.after_shift_change() });
			}
			close_shift_here() {
				return p.pos_shift.close({ t: term, shift: this.pearnly_shift,
					on_done: () => this.after_shift_change() });
			}
			// 班次变了（开班 / 交班）之后整页重来一遍：界面与页面动作都跟着新班次走。
			async after_shift_change() {
				this.wrapper.html("");
				await this.check_opening_entry();
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
				// **那一声「结账」是"存草稿"带来的**：官方 `.checkout-btn` → `save_and_checkout()`
				// → `frm.save()` → frappe `form.js` 存成功后播 `click`（音量 0.05；官方 POS 上实测
				// 就是这一声，加购本身不响）。我们把保存去掉了，这一声就得自己补 —— 同一时刻、同一声、
				// 同样只在"车里有东西"时响（官方 `is_dirty()` 那条件、我们旧页面 `if (!cart.length) return`）。
				// 音量与静音不用在这里判：官方 `play_sound` 自己看 `User.mute_sounds`。
				const doc = this.frm && this.frm.doc;
				if (doc && (doc.items || []).length) frappe.utils.play_sound("click");
				this.payment.checkout();
			}

			// 落单：官方在 `init_payments()` 里把"提交"写成 `frm.savesubmit()`（POS Invoice 的路），
			// 我们换成自己的接口 —— 官方 payment 里那句 `this.events.submit_invoice()` 一个字没动，
			// 换掉的是**我们这一侧的事件实现**。
			init_payments() {
				super.init_payments();
				this.payment.events.submit_invoice = () => this.submit_our_sale();
			}

			// 「最近订单」点一行 → 右边那张汇总。官方那句只认它自己那两种单据
			// （`if (!["POS Invoice", "Sales Invoice"].includes(doctype)) return;`），
			// 我们的单是 `ERP Sale` —— 所以那一句永远提前返回，点一行什么都不发生。
			// 这里**只换事件里那一次读**：官方 `load_summary_of()` 与它下面那套渲染一个字没改，
			// 我们喂进去的是摆成官方形状的**我们的单据**（见 `summary_doc_of`）。
			init_recent_order_list() {
				super.init_recent_order_list();
				this.recent_order_list.events.open_invoice_data = (doctype, name) => {
					if (doctype !== "ERP Sale") return;
					p.call(api + "api_get_sale", { name }).then(doc => {
						if (doc && doc.name) this.order_summary.load_summary_of(summary_doc_of(doc));
					});
				};
			}

			// 「完成订单」之后那张汇总屏（底下就是我们那三颗按钮）。官方直接把**客户端那张单据**
			// 交给汇总屏渲染（`this.frm.doc`，字段名是官方 POS Invoice 那一套），我们手上这张是
			// `ERP Sale`，所以同一处摆成官方形状再喂（与点最近订单那条路同一个 `summary_doc_of`）。
			toggle_submitted_invoice_summary(show) {
				this.order_summary.toggle_component(show);
				this.order_summary.load_summary_of(summary_doc_of(this.frm.doc), true);
			}

			async submit_our_sale() {
				const doc = this.frm.doc;
				const lines = (doc.items || []).map(row => ({
					product: row.item_code, qty: flt(row.qty), unit: row.uom,
					// 商品名一起落库（官方 POS Invoice 行上也有 `item_name`）：小票、销售记录、
					// 汇总屏都读它；不带的话单据行上只有编码，金额旁边就是一行 `PRD-00597`。
					description: row.item_name || undefined,
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
					// **那一声「完成订单」是"提交成功"带来的**：官方 `frm.savesubmit()` 成功后由
					// frappe `form.js` 播 `submit`。我们换成了自己的落单接口，这一步同样自己补 ——
					// 成功之后、绿条之前，与官方那是同一时刻。
					frappe.utils.play_sound("submit");
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
				// 换算系数 = 1（我们的商品没有多单位换算这一步）、仓库 = **这台收银台卖货的仓**、
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
			// 扫码：**用我们现成那条路**（旧页面商品区顶上那颗相机的做法）——
			// 官方这个搜索框只认 `onscan.js` 那种键盘楔式扫码枪；`options: "Barcode"` 是**框架自己的开关**，
			// 打开后 `ControlData.setup_barcode_field()` 会给控件挂上那颗 `icon-scan-barcode` 的
			// `.link-btn`（点开官方相机对话框，扫到的字回填进本框）。我们不改官方建控件的代码，
			// 只在它建好之后按框架自己那条路补上，再接管"扫到之后干什么"。
			make_search_bar() {
				const out = super.make_search_bar();
				const field = this.search_field;
				if (field && field.setup_barcode_field && field.df.options !== "Barcode") {
					field.df.options = "Barcode";
					field.setup_barcode_field();
					field.$wrapper.on("click", `a[title="${__("Scan")}"]`, () => {
						this.pearnly_scanned = true;
					});
				}
				// 「物料组」我们产品**没有**（用户 2026-09-24："物料组我们是没有的，删掉"）。
				// 官方那一格就在这个方法里建（`item_group_field`，一个 `Item Group` 的 Link，
				// 另配 `get_parent_item_group` / `item_group_query` 两个 erpnext 方法）。
				// **控件照官方那套建、只把那一格连同容器从筛选行里拿掉**：控件对象仍在，
				// 官方 `attach_clear_btn()` / `attach_shortcuts()` 里对它的引用（`.$wrapper`、`.parent`）
				// 落在已移除的节点上，是空操作、不会抛错；筛选行只剩标题与搜索框。
				this.$component.find(".item-group-field").remove();
				return out;
			}
			// **"唯一命中直接进车"官方自己就有**，不要自己再点一次卡：
			// `filter_items()` 里那两处 `auto_add_item && 搜索框有值 && this.items.length == 1
			// → add_filtered_item_to_cart()`，而我们的设置在 `auto_add_item_to_cart: 1`。
			// 早先那版我自己又点了一次卡 —— 于是**同一次扫码进两瓶**（实测：一次输入从 1 瓶变 2 瓶）。
			// 现在加货一律走官方那条路，我们只补官方没有的两件：相机按钮（`make_search_bar`）
			// 与**声音**（官方那两声只在"回车"那条路上，而相机是 `set_value()` 回填、没有回车）。
			add_filtered_item_to_cart() {
				const out = super.add_filtered_item_to_cart();
				// 相机扫到的字回填进搜索框 → 官方画出一条并自动进车 —— 这一路官方是**静音**的
				// （它只在回车那条路上播 `submit`）。按我们旧页面的口径补同一颗音：
				// **扫码命中**才响，手输搜索不响。
				if (this.pearnly_scanned) {
					this.pearnly_scanned = false;
					frappe.utils.play_sound("submit");
				}
				return out;
			}
			// 相机扫到**没有**对应物料：官方那句提示也在回车那条路上（`items.length == 0 &&
			// this.barcode_scanned`），相机这条路没有回车 —— 照我们旧页面的口径给一声提示 + `error`。
			async filter_items(args) {
				const out = await super.filter_items(args);
				if (!this.pearnly_scanned) return out;
				if (this.$component.find(".items-container .item-wrapper").length) return out;
				this.pearnly_scanned = false;
				frappe.show_alert({
					// 与我们旧页面同一句（术语表 `trade.scanNotFound`）。
					message: __("No product for this code: {0}", [
						String((args && args.search_term) || "").trim(),
					]),
					indicator: "orange",
				});
				frappe.utils.play_sound("error");
				return out;
			}
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
					["customer_name", "contact_phone", "contact_email"]).then(r => {
						const name = r.message?.customer_name || customer;
						// 顺手把抬头写到**单据上**：完成订单后那张汇总屏读的是 `doc.customer_name`
						// （官方的名字是服务端 `set_customer_info` 补的，我们删减了那一步），
						// 不写的话买了单的客户在汇总屏上仍显示"散客"。
						const frm = this.events.get_frm();
						if (frm && frm.doc && frm.doc.customer === customer) {
							frappe.model.set_value(frm.doc.doctype, frm.doc.name, "customer_name", name);
						}
						return {
							customer, customer_name: name,
							mobile_no: r.message?.contact_phone || "",
							email_id: r.message?.contact_email || "",
						};
					});
			}
		}

		// ── 「最近订单」：面板是官方那个，数据是**我们自己的销售记录** ─────────────────
		// 我们产品**支持**这件事（销售记录 / 收银记录两页就是它），官方那个面板只是去调
		// `erpnext…get_past_order_list`（那个 app 我们没装）——所以以前点开是"未安装应用程序"。
		// 做法与加货、明细面板同一套：**官方那两个文件的渲染代码一个字不改**，
		// 我们把数据摆成它们要的形状喂进去（`invoice_of` / `summary_doc_of`）。
		// 官方面板里"草稿"那一档我们**没有**（产品口径不存草稿），所以状态只给全部/已完成/已作废。
		const ORDER_STATUS = [
			{ value: "", label: __("All") },
			{ value: "completed", label: __("Completed") },
			{ value: "void", label: __("Voided") },
		];
		const status_value_of = label =>
			(ORDER_STATUS.find(row => row.label === label) || ORDER_STATUS[0]).value;

		function invoice_of(row) {
			const at = String(row.sold_at || "");
			return {
				doctype: "ERP Sale",
				// **显示用小票号**（收银员认的那个，如 `SI2569-000060`）。
				// 取数要的是**内部单号**（`SALE-2026-00673`）—— 官方这个渲染函数两处都用
				// `invoice.name`（显示 + `data-invoice-name`），所以在 `refresh_list` 里
				// 追加之前把属性改回内部单号，官方那 20 行标记一个字没动。
				name: row.receipt_no || row.name,
				pearnly_name: row.name,
				customer_name: row.customer_name || __("Walk-in customer"),
				grand_total: flt(row.grand_total),
				currency: row.currency || frappe.boot.sysdefaults.currency || "THB",
				posting_date: at.slice(0, 10),
				posting_time: at.slice(11, 19),
			};
		}

		// 我们这张 `ERP Sale` → 官方那张 `POS Invoice` 的形状（官方汇总屏整屏读的是后者的字段名）。
		// 状态**只借用官方的词**（`Paid` 绿点、`Voided` 灰点），文案仍由术语表翻；
		// 底下那几颗按钮是我们自己那版（见 `PearnlyPastOrderSummary`），判据也看这个状态。
		function summary_doc_of(doc) {
			// ⚠️ 这份映射要同时认**两种来源**：点最近订单时是服务端那份 `api_get_sale`
			// （行上是我们的字段名 `product / unit_price / line_total / discount`），
			// 刚完成订单时是手上那张**客户端单据**（行上是官方那套 `item_code / rate / amount`）。
			// 两边都用 `??` 兜，不靠"哪一路进来"分叉。
			const currency = doc.currency || frappe.boot.sysdefaults.currency || "THB";
			const vat = flt(doc.vat_amount ?? doc.total_taxes_and_charges);
			const customer = doc.customer || "";
			const pay_label = row => row.mode_of_payment
				|| (payment_methods.find(m => m.key === row.method) || {}).label || row.method;
			return Object.assign({}, doc, {
				doctype: "ERP Sale",
				docstatus: 1,
				// 汇总屏右上角那个号显示**小票号**；取数（退货/作废/重读汇总）用内部单号。
				name: doc.receipt_no || doc.name,
				pearnly_name: doc.name,
				// 两种来源：服务端那一份 `status=completed`；刚提交时手上是客户端那张
				// （收银台自己把它置成 `Paid`）。都算已完成。
				// `status` 是给官方那套渲染用的词（`Paid` 绿点 / `Return` 灰点 / `Voided` 灰点）；
				// 我们自己那几个按钮看的是 `pearnly_status`（我们自己的词：completed / void）——
				// 两套别混着判，否则"已作废的单"底下还会画着「作废」那颗按钮（实测）。
				status: doc.status === "void" ? "Voided"
					: (doc.sale_type === "refund" ? "Return"
						: (["completed", "Paid"].includes(doc.status) ? "Paid" : "Draft")),
				pearnly_status: ["completed", "Paid"].includes(doc.status) ? "completed" : "void",
				is_return: doc.sale_type === "refund",
				currency,
				// 散客：不给客户编号（官方那句"客户编码"在散客时会画出一个空行）。
				customer,
				customer_name: doc.customer_name || __("Walk-in customer"),
				owner: doc.owner || doc.cashier || frappe.session.user_fullname || frappe.session.user,
				items: (doc.lines || []).map(line => {
					const rate = flt(line.unit_price ?? line.rate);
					return {
						name: line.name,
						item_code: line.product || line.item_code,
						// 服务端那份里商品名是行上的 `description`（可能为空），编码是 `product`；
						// 客户端那张单据上则是官方那套 `item_name / item_code`。两边都兜住 ——
						// 不兜的话汇总屏那一行的名字是空的（实测：只剩"2 瓶 ฿ 100.00"）。
						// `product_name` 是主档里的名字（`positions.public` 带的），比编码好看。
						item_name: line.description || line.item_name || line.product_name
							|| line.product || line.item_code,
						qty: flt(line.qty),
						uom: line.unit || line.uom,
						rate,
						// 标价 = 成交价 + 行折扣：官方那句严格比较 `rate !== price_list_rate`
						// 只在真打过折的行上画 `(x% off)`。
						price_list_rate: rate + flt(line.discount ?? line.discount_amount),
						amount: flt(line.line_total ?? line.amount),
					};
				}),
				// 我们收银默认不开票（含税拆分在开票侧），所以这里通常没有税行。
				taxes: vat ? [{ description: __("VAT"), tax_amount_after_discount_amount: vat }] : [],
				net_total: flt(doc.subtotal ?? doc.net_total),
				total_taxes_and_charges: vat,
				discount_amount: flt(doc.discount_total ?? doc.discount_amount),
				grand_total: flt(doc.grand_total),
				rounded_total: flt(doc.grand_total),
				paid_amount: flt(doc.paid_total ?? doc.paid_amount),
				outstanding_amount: 0,
				change_amount: flt(doc.change_amount),
				payments: (doc.payments || []).map(row => ({
					// 付款方式显示本地化的名字（泰语为主），不是服务端的 key。
					mode_of_payment: pay_label(row),
					amount: flt(row.amount),
				})),
			});
		}

		class PearnlyPastOrderList extends O.PastOrderList {
			// 状态那一格：官方默认筛「草稿」、选项是它自己那套（草稿/已付款/合并/退货/部分付款）。
			// 我们**不存草稿**（那是产品口径），也没有合并/部分付款，所以换成我们这三档，
			// 默认「全部」——否则一打开面板就是空的。
			make_filter_section() {
				const out = super.make_filter_section();
				if (!this.status_field) return out;
				this.status_field.df.options = ORDER_STATUS.map(row => row.label).join("\n");
				this.status_field.refresh();
				this.status_field.set_value(ORDER_STATUS[0].label);
				return out;
			}
			// 列表数据：`api_list_sales`（销售记录那张表用的**同一个接口**）。
			// 非管理员在服务端只回本人的单 —— 与"成员只看自己录入的记录"一致。
			refresh_list() {
				// 遮罩**两个出口都要收**：官方的写法是 `freeze()` 之后只在成功回调里
				// `unfreeze()` —— 请求一失败（以前这里就是"未安装 erpnext"那个 500），
				// 遮罩就永远留在屏幕上：整页发灰、点什么都没反应（实测遇到过）。
				frappe.dom.freeze();
				this.events.reset_summary();
				const search_term = this.search_field.get_value();
				const status = status_value_of(this.status_field.get_value());
				this.$invoices_container.html("");
				return frappe.call({
					method: api + "api_list_sales",
					args: { q: search_term || undefined, status: status || undefined, limit: 20 },
					callback: (response) => {
						frappe.dom.unfreeze();
						(response.message || []).forEach(row => {
							const $row = $(this.get_invoice_html(invoice_of(row)));
							// 官方那行同时拿 `invoice.name` 当**显示号**与**取数键**；
							// 我们两个号不是一回事（显示 `SI…`、取数 `SALE-…`），所以这里
							// 只把取数那个属性换回内部单号，HTML 一个字没改。
							$row.attr("data-invoice-name", row.name);
							this.$invoices_container.append($row);
						});
					},
					error: () => frappe.dom.unfreeze(),
				});
			}
		}

		// 单据汇总底下那几颗按钮照**我们之前那页**来（用户 2026-09-24："这按钮留我们之前的"）：
		// 打印收据 / 退货退款 / 作废 / 新订单。官方那组是 Print Receipt / **Email Receipt** /
		// Return / New Order —— 邮件小票我们**没有做**（F22 的产品口径），所以不画。
		// 三个动作都在共用模块 `desk/pos/pearnly-pos-receipt.js` 里（旧页面调同一份）。
		class PearnlyPastOrderSummary extends O.PastOrderSummary {
			constructor(opts) {
				super(opts);
				// 官方是"按标签首词生成类名、再在 `bind_events()` 里各自绑动作"（`.print-btn`…
				// 那串）。我们的动作用自己的接口，所以在**渲染那一处**整段换成我们这一版，
				// 标记沿用官方那套（`summary-btn btn btn-default <x>-btn`）。
				// 官方那些动作绑在**外层容器**上（`$summary_container` 的委托），我们这些绑在
				// 按钮区自己身上 —— 不摘掉官方那一条，点一次「打印收据」会走两遍：
				// 我们那份开 `/printview`（用内部单号），官方那份再 `frappe.utils.print()`
				// 开一次（它拿 `doc.name` = 我们给小票号的那个显示名 → 那一张打不开）。
				// 实测点一次弹出两个打印标签页。这里只摘掉与我们对位的那一个。
				this.$summary_container.off("click", ".print-btn");
				this.$summary_btns.on("click", ".print-btn", () =>
					p.pos_receipt.print(this.summary_doc()));
				this.$summary_btns.on("click", ".refund-btn", () => void this.with_server_doc(doc =>
					p.pos_receipt.refund({ t: term, doc, methods: payment_methods.map(m => m.key),
						on_done: () => this.after_receipt_action() })));
				this.$summary_btns.on("click", ".void-btn", () => void this.with_server_doc(doc =>
					p.pos_receipt.void_sale({ t: term, doc, on_done: () => this.after_receipt_action() })));
				this.$summary_btns.on("click", ".new-order-btn", () => {
					this.events.new_order();
					this.toggle_component(false);
					this.$component.find(".no-summary-placeholder").css("display", "flex");
					this.$summary_wrapper.css("display", "none");
				});
			}
			// 汇总屏手上这份可能是**客户端**那张（刚提交完那张）。打印只要名字够用；
			// 退货 / 作废要服务端那一份（它们按 `lines[].name / line_total / warehouse_name` 取数）。
			// 「打印收据」走的是框架打印视图（`/printview?doctype=…&name=…`），它要的是**内部单号**
			// —— 屏上那一版为了让收银员看到小票号，`name` 摆的是 `SI…`，所以这里换回来。
			summary_doc() {
				const doc = this.doc || {};
				return doc.pearnly_name ? Object.assign({}, doc, { name: doc.pearnly_name }) : doc;
			}
			async with_server_doc(run) {
				// 汇总屏上那个号是**小票号**（给人看的）；取服务端那一份要用**内部单号**。
				const doc = this.doc || {};
				const name = doc.pearnly_name || doc.name;
				if (!name) return;
				const full = await p.call(api + "api_get_sale", { name });
				if (!full || !full.name) return;
				await run(full);
			}
			// 退货 / 作废之后这张单已经变了：重新取一遍汇总，别让界面停在做完之前的样子。
			after_receipt_action() {
				return this.with_server_doc(doc => this.load_summary_of(summary_doc_of(doc), true));
			}
			add_summary_btns() {
				const doc = this.summary_doc();
				// 判据用**我们自己那个状态**（旧页面同一句：`doc.status === "completed"`）：
				// 作废过的单只剩「新订单」。`pearnly_status` 是 `summary_doc_of` 归一化过的
				// （服务端 `completed` 与刚提交时客户端那份 `Paid` 都归 `completed`）。
				const state = doc.pearnly_status
					|| (["completed", "Paid"].includes(doc.status) ? "completed" : "void");
				const done = state === "completed";
				const can_write = frappe.model.can_write("ERP Sale");
				const is_refund = doc.sale_type === "refund" || doc.is_return;
				// 文案走**我们自己的词条键**（与旧页面那三颗按钮同一份：`trade.printReceipt` /
				// `trade.refund` / `trade.void` / `trade.newOrder`）—— 不写官方那串英文。
				// 踩过一次：`__("Void")` 在术语表里有两条带 key 的词条（库存那套"撤销"与销售
				// 这套"作废"），译文不一致 → 生成脚本**不补英文别名行** → 运行时那句直出
				// 英文"Void"（实测就是那颗按钮）。
				const buttons = [
					done ? { cls: "print-btn", label: term("trade.printReceipt") } : null,
					can_write && done && !is_refund ? { cls: "refund-btn", label: term("trade.refund") } : null,
					can_write && done ? { cls: "void-btn", label: term("trade.void") } : null,
					// **主按钮永远排最右边**（用户 2026-09-23："次按钮是灰的，主按钮排最右边"）。
					{ cls: "new-order-btn btn-primary", label: term("trade.newOrder") },
				].filter(Boolean);
				this.$summary_btns.html(buttons.map(row =>
					`<div class="summary-btn btn btn-default ${row.cls}">${
						frappe.utils.escape_html(row.label)}</div>`).join(" "));
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
		O.PearnlyPastOrderList = PearnlyPastOrderList;
		O.PearnlyPastOrderSummary = PearnlyPastOrderSummary;
		// 让官方 Controller 里 `new erpnext.PointOfSale.ItemSelector/ItemCart/Payment(...)` 拿到我们的子类。
		O.ItemSelector = PearnlyItemSelector;
		O.ItemCart = PearnlyItemCart;
		O.Payment = PearnlyPayment;
		O.PastOrderList = PearnlyPastOrderList;
		O.PastOrderSummary = PearnlyPastOrderSummary;
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
