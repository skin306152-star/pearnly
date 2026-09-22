/* 收银 POS：开班 / 收银台（商品 → 购物车 → 收款）/ 当班小票 / 交班 / 班次记录。
 *
 * 用户 2026-09-23 口径："收银台也要做，用官方原生的，数据关联商品，库存，收发存"、
 * "扫码可以用官方的"。所以这里三件事都按官方那条路走：
 *
 * · 扫码用框架自带的 `frappe.ui.Scanner`（走 html5-qrcode，一维码与二维码都认）；
 * · 商品与库存读我们自己的域（`cashier/counter.py`：在架商品 + 售价 + **这个收银台仓库**
 *   的结存，结存就是收发存那张 `ERP Stock Balance`）；
 * · 收款走 `cashier.sales.create_sale` —— 一笔收银落一张单、扣一次库存、进一次收发存，
 *   单号与销售记录同一个序列（见 `cashier/sales.py` 的 `_receipt_no`）。
 *
 * 全部界面构件都是官方的：Page / 原生列表 / Dialog / Button，没有自造控件。
 */
(() => {
	const p = frappe.pearnly;
	p.cashier = async (page, t) => {
		const api = "pearnly_erp_portal.cashier.api.";
		const canWrite = frappe.model.can_write("ERP Sale");
		// 钱一律这么显示（两位小数、带币种）；读数时把千分位剥掉再转数字。
		const money = value => p.money(value);
		const num = value => {
			const parsed = Number(String(value ?? "").replace(/,/g, ""));
			return isFinite(parsed) ? parsed : 0;
		};
		const round2 = value => Math.round(num(value) * 100) / 100;
		// 单据类型/状态用**本页自己的键**取词（`trade.kind.*` / `trade.state.*`）——
		// 直接 `__("sale")` 这种会被框架自带词典先接走，或者干脆露出英文原文。
		const kind_label = value => t(`trade.kind.${value}`);
		const state_label = value => t(`trade.state.${value}`);
		const amount = (fieldname, label, value) => ({ fieldtype: "Currency", options: "currency",
			formatter: money, fieldname, label, read_only: 1, default: value });
		const field = (fieldname, label, fieldtype = "Data", options = {}) =>
			({fieldname, label: __(label), fieldtype, ...options});

		let shift, form, products_list, cart_list, receipts_list;
		// 购物车只是这一屏的状态，不落库：收款那一下才由服务端成单（与旧线上一致）。
		let cart = [];
		// 收银台那一屏的骨架：左商品、右购物车，照框架 POS 的两栏比例（见 cashier.css）。
		// 用 HTML 字段当容器，原生列表挂进去 —— 控件本身还是官方的，没有自造。
		const COUNTER_HTML = `<div class="pearnly-counter">
			<div class="pearnly-counter-pane">
				<div class="pearnly-counter-filter"></div>
				<div class="pearnly-counter-items"></div>
			</div>
			<div class="pearnly-counter-pane pearnly-counter-cart">
				<div class="pearnly-counter-total"></div>
				<div class="pearnly-counter-lines"></div>
			</div>
		</div>`;

		const cart_total = () => round2(cart.reduce(
			(sum, row) => sum + num(row.qty) * num(row.unit_price) - num(row.discount), 0));

		// ── 右上角那颗主按钮 ────────────────────────────────────────────────────
		// 没班 → 「开班」；有班 → 「收款 ฿X」（购物车空着就禁用）。
		// 为什么单独一个函数：列表的选中状态一刷新就会去清主按钮（"没选中就没有列表动作"
		// 是共享层的规矩），不把"班次/收款"这个动作交回去，按钮会被列表刷掉、页面上点不到
		// （商品页当初踩的同一个坑）。
		function restore_primary() {
			if (!canWrite) { page.clear_primary_action(); return; }
			if (!shift) { page.set_primary_action(t("trade.openShift"), () => void open_shift()); return; }
			page.set_primary_action(`${t("trade.action.pay")} ${money(cart_total())}`, () => checkout());
			page.btn_primary?.prop("disabled", !cart.length);
		}

		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			page.set_indicator(shift
				? t("trade.shiftOpen", shift.shift.shift_seq, p.money(shift.expected_cash))
				: t("trade.noShift"), shift ? "green" : "orange");
			restore_primary();
			await Promise.all([products_list?.reload(), cart_list?.reload(), receipts_list?.reload()]);
		}

		// ── 开班 ────────────────────────────────────────────────────────────────
		// 收银台默认带出本店那个默认台（与服务端 `terminals.resolve` 同一口径），
		// 备用金默认 0。留空就用服务端的默认台 —— 新店还没建收银台时也不会卡住。
		async function open_shift() {
			const terminals = await p.call(api + "api_list_terminals");
			const preferred = terminals.find(row => Number(row.is_default)) || terminals[0];
			p.dialog(t("trade.openShift"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Link", fieldname: "terminal", options: "ERP POS Terminal", label: t("trade.terminal"),
					default: preferred?.name || "", description: t("trade.terminalHint") },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "opening_float",
					label: t("trade.float"), default: 0 },
			], t("trade.openShift"), async (values, d) => {
				const result = await p.call(api + "api_open_shift",
					{ terminal: values.terminal, opening_float: values.opening_float }, "POST");
				d.hide();
				p.success(t("trade.shiftOpened", result.shift_seq));
				await render();
			});
		}

		// ── 收银台：商品区 ──────────────────────────────────────────────────────
		// 商品区一次给全"商品 + 售价 + 该收银台的结存"（服务端 `counter.products_page`），
		// 点一行就是加一件；扫码/手输走同一个落点。
		function add_to_cart(row, qty = 1) {
			if (!row?.name) return;
			const line = cart.find(item => item.product === row.name);
			if (line) line.qty = round2(num(line.qty) + qty);
			else cart.push({
				product: row.name, product_name: row.product_name, description: row.product_name,
				code: row.code, unit: row.unit, warehouse: row.warehouse, location: row.location || "",
				unit_price: num(row.unit_price), qty, discount: 0, qty_on_hand: num(row.qty_on_hand),
			});
			paint_cart();
		}

		// 购物车一变就同步三处：主按钮上的金额、右栏顶上那行合计、明细列表。
		function paint_cart() {
			const text = `${t("trade.total")} ${money(cart_total())}`;
			form?.fields_dict?.counter?.$wrapper.find(".pearnly-counter-total").text(text);
			restore_primary();
			void cart_list?.reload();
		}

		// 扫码：走框架自带的 `frappe.ui.Scanner`（官方那条），扫到再落到 `api_counter_lookup`
		// 找品 —— 查四处（编码/条码/二维码/售卖单位条码）的规则在商品域里，收银台不另写一套。
		function start_scan() {
			if (typeof frappe.ui.Scanner !== "function") {
				frappe.msgprint(t("trade.scanUnsupported"));
				return;
			}
			new frappe.ui.Scanner({ dialog: true, multiple: false,
				on_scan: data => void on_scanned(data?.result?.text || data?.decodedText || "") });
		}

		async function on_scanned(code) {
			const wanted = String(code || "").trim();
			if (!wanted) return;
			const found = await p.call(api + "api_counter_lookup",
				{ value: wanted, terminal: shift?.shift?.terminal });
			if (!found?.name) { frappe.msgprint(t("trade.scanNotFound", wanted)); return; }
			add_to_cart(found);
			p.success(t("trade.addedToCart", found.product_name));
		}

		function edit_line(row) {
			const line = cart.find(item => item.product === row.product);
			if (!line) return;
			p.dialog(line.product_name, [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: line.qty },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "unit_price",
					label: t("trade.price"), reqd: 1, default: line.unit_price },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "discount",
					label: t("trade.discount"), default: line.discount },
			], t("trade.save"), async (values, dialog) => {
				line.qty = round2(values.qty); line.unit_price = num(values.unit_price);
				line.discount = num(values.discount);
				dialog.hide();
				paint_cart();
				await cart_list?.reload();
			});
		}

		function clear_cart() {
			if (!cart.length) return;
			frappe.confirm(t("trade.cartClearConfirm"), async () => {
				cart = [];
				paint_cart();
				await cart_list?.reload();
			});
		}

		// ── 收款 ────────────────────────────────────────────────────────────────
		// 一笔收银 = 一次 `api_create_sale`：单据、收款、出库三处一起成，幂等锚在请求号上。
		// 收银价是货架标价，所以 `has_vat: 0`（不在标价上再加 7%）。
		function checkout() {
			if (!cart.length) { frappe.msgprint(t("trade.cartEmpty")); return; }
			const total = cart_total();
			const request = crypto.randomUUID();
			let dialog;
			const paint_change = () => {
				if (!dialog) return;
				const change = round2(num(dialog.get_value("amount")) - total);
				dialog.set_value("change", change > 0 ? change : 0);
			};
			dialog = p.dialog(t("trade.checkout"), [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				amount("total", t("trade.total"), total),
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"),
					options: u_methods(), default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "amount",
					label: t("trade.paid"), reqd: 1, default: total, change: paint_change },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "change",
					label: t("trade.change"), read_only: 1, default: 0 },
				{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
			], t("trade.pay"), async (values, d) => {
				const result = await p.call(api + "api_create_sale", {
					shift: shift.shift.name, terminal: shift.shift.terminal, has_vat: 0,
					lines: cart.map(row => ({ product: row.product, qty: num(row.qty),
						unit_price: num(row.unit_price), discount: num(row.discount),
						unit: row.unit, description: row.description,
						warehouse: row.warehouse, location: row.location })),
					payments: [{ method: values.method, amount: num(values.amount) }],
					note: values.note || undefined, client_uuid: request,
				}, "POST");
				d.hide();
				p.success(t("trade.paidNotice", result.receipt_no || result.name,
					p.money(result.change_amount)));
				cart = [];
				paint_cart();
				// 卖出去就少货：商品区的结存要跟着变，所以两边都重取。
				await refresh();
				await receipt(result.name);
			});
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}

		// 付款方式照服务端认的那四种（`cashier/contracts.PAYMENT_METHODS`）。
		const u_methods = () => "cash\ncard\npromptpay\ntransfer";

		// ── 小票、退货、班次 ────────────────────────────────────────────────────
		async function receipt(name) {
			const doc = await p.call(api + "api_get_sale", { name });
			let d;
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Data", fieldname: "status", label: t("trade.status"), read_only: 1, default: state_label(doc.status) },
				p.trade.lines(t, { readonly: true, data: doc.lines }),
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "total", label: t("trade.total"), read_only: 1, default: doc.grand_total },
			];
			if (doc.status === "completed" && canWrite) {
				if (doc.sale_type === "sale") fields.push({ fieldtype: "Button", fieldname: "refund", label: t("trade.refund"), click: () => refund(doc, d) });
				fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"), click: () => frappe.confirm(t("trade.voidConfirm"), () => p.trade.act(d, async () => {
					await p.call(api + "api_void_sale", { name }, "POST"); d.hide(); await refresh(); await receipt(name);
				})) });
			}
			d = p.dialog(doc.receipt_no, fields, t("trade.close"), async (_values, dialog) => dialog.hide());
			d.$wrapper.find(".modal-dialog").addClass("modal-xl");
		}
		function refund(doc, parent) {
			const request = crypto.randomUUID();
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
					options: doc.lines.map(row => ({ value: row.name, label: `${row.description || row.product} · ${row.warehouse_name || ""} · ${row.location || ""} · ${p.number(row.qty)}` })) },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"), options: u_methods(), default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "amount", label: t("trade.refundAmount"), reqd: 1 },
			];
			let d;
			const calculate = () => {
				if (!d) return;
				const line = doc.lines.find(row => row.name === d.get_value("original_line"));
				if (line) d.set_value("amount", Math.round(Number(line.line_total) * Number(d.get_value("qty") || 0) / Number(line.qty) * 100) / 100);
			};
			fields.find(field => field.fieldname === "original_line").change = calculate;
			fields.find(field => field.fieldname === "qty").change = calculate;
			d = p.dialog(t("trade.refund"), fields, t("trade.refund"), async (values, dialog) => {
				await p.call(api + "api_refund_sale", { name: doc.name, lines: [{ original_line: values.original_line, qty: values.qty }],
					payments: [{ method: values.method, amount: values.amount }], client_uuid: request }, "POST");
				dialog.hide(); parent.hide(); await refresh(); await receipt(doc.name);
			});
			calculate();
		}

		// 交班：先把**当班汇总**摆出来（笔数、退款、各收款方式、找零、应有现金），再填实点
		// 现金；差额 = 实点 − 应有，边填边算、不为 0 就标红。
		function hand_over() {
			if (!shift) { frappe.msgprint(t("trade.noShift")); return; }
			const data = shift, by_method = data.by_method || {};
			let dialog;
			const paint = () => {
				if (!dialog) return;
				const diff = round2(num(dialog.get_value("counted_cash")) - num(data.expected_cash));
				dialog.set_value("cash_diff", diff);
				// 只读的金额格框架渲染成 `control-value` 而不是 `input`，所以按容器找。
				dialog.fields_dict.cash_diff?.$wrapper
					?.find("input, .control-value, .like-disabled-input")
					.toggleClass("pearnly-payment-due", Boolean(diff));
				dialog.set_df_property("cash_diff", "description",
					diff === 0 ? t("trade.diffEven") : diff > 0 ? t("trade.diffOver") : t("trade.diffShort"));
			};
			dialog = p.dialog(t("trade.closeShift"), [
				{ fieldtype: "Int", fieldname: "sales_count", label: t("trade.salesCount"), read_only: 1, default: data.sales_count },
				amount("gross", t("trade.gross"), data.gross),
				{ fieldtype: "Column Break" },
				{ fieldtype: "Int", fieldname: "refund_count", label: t("trade.refundCount"), read_only: 1, default: data.refund_count },
				amount("net", t("trade.net"), data.net),
				{ fieldtype: "Section Break", label: t("trade.byMethod") },
				amount("cash_total", t("trade.methodCash"), by_method.cash || 0),
				amount("card_total", t("trade.methodCard"), by_method.card || 0),
				{ fieldtype: "Column Break" },
				amount("promptpay_total", t("trade.methodPromptpay"), by_method.promptpay || 0),
				amount("transfer_total", t("trade.methodTransfer"), by_method.transfer || 0),
				{ fieldtype: "Section Break", label: t("trade.countCash") },
				amount("opening_float", t("trade.float"), data.shift.opening_float),
				amount("change_total", t("trade.changeTotal"), data.change_total),
				{ fieldtype: "Column Break" },
				amount("expected_cash", t("trade.expectedCash"), data.expected_cash),
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "counted_cash",
					label: t("trade.countedCash"), reqd: 1, default: data.expected_cash, change: paint },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "cash_diff",
					label: t("trade.diff"), read_only: 1, default: 0 },
			], t("trade.closeShift"), async (values, d) => {
				const result = await p.call(api + "api_close_shift",
					{ name: data.shift.name, counted_cash: values.counted_cash }, "POST");
				d.hide();
				p.success(t("trade.shiftClosed", p.money(result.cash_diff)));
				await render();
			});
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			paint();
		}

		// 班次记录：近 200 个班，谁开的、什么时候交的、备用金/应有/实点/差额各是多少。
		async function shift_history() {
			const dialog = p.dialog(t("trade.shiftHistory"), [
				{ fieldtype: "HTML", fieldname: "history" },
			], t("trade.close"), async (_values, d) => d.hide());
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			const blank = "—";
			const due = value => `<span class="pearnly-payment-due">${value}</span>`;
			const columns = [
				{ name: t("trade.shiftNo"), width: 140, format: p.escape },
				{ name: t("trade.terminal"), width: 130, format: p.escape },
				{ name: t("trade.cashier"), width: 150, format: p.escape },
				{ name: t("trade.status"), width: 90, format: p.escape },
				{ name: t("trade.openedAt"), width: 130, format: p.escape },
				{ name: t("trade.closedAt"), width: 130, format: p.escape },
				{ name: t("trade.float"), width: 90, format: p.escape },
				{ name: t("trade.expectedCash"), width: 105, format: p.escape },
				{ name: t("trade.countedCash"), width: 105, format: p.escape },
				// 这一列自己拼红字，所以不走默认转义（与盘点工作台的差异列同一做法）。
				{ name: t("trade.diff"), width: 100, format: value => value },
			];
			let table;
			// 表格要等弹窗**真的显示出来**再画：原生表格构建时会往容器里插一个 `<style>`，
			// 容器还没接上文档时拿不到 `sheet`，整张表就画不出来（盘点明细弹窗踩过同一个坑）。
			dialog.on_page_show = async () => {
				if (table) return;
				const rows = await p.call(api + "api_list_shifts", { limit: 200 });
				table = await p.table(dialog.fields_dict.history.$wrapper, columns, rows.map(row => {
					const closed = row.status === "closed";
					const diff = num(row.cash_diff);
					const shown = value => p.escape(money(value));
					return [
						p.escape(row.name), p.escape(row.terminal || blank),
						p.escape(frappe.user_info(row.cashier).fullname || row.cashier || blank),
						p.escape(t(`trade.state.${row.status}`)),
						p.escape(p.datetime_text(row.opened_at) || blank),
						p.escape(closed ? p.datetime_text(row.closed_at) || blank : blank),
						shown(row.opening_float),
						closed ? shown(row.expected_cash) : blank,
						closed ? shown(row.counted_cash) : blank,
						closed ? (diff ? due(shown(diff)) : shown(diff)) : blank,
					];
				}));
			};
		}

		// ── 页面 ────────────────────────────────────────────────────────────────
		// 小票不允许删除（退货要留痕），所以选中一条已完成的小票时给的是**对应那一步**：
		// 打开退货退款弹窗（与从小票弹窗里点"退货退款"是同一条路径）。
		const selection = p.selection_action({
			page, list: () => receipts_list, empty: restore_primary,
			label: () => t("trade.refund"),
			when: (row, rows) => rows.length === 1 && row.status === "completed"
				&& row.sale_type === "sale",
			reason: (_row, rows) => (rows.length === 1 ? t("trade.refundBlocked")
				: t("trade.refundSingle")),
			run: async rows => {
				const doc = await p.call(api + "api_get_sale", { name: rows[0].name });
				refund(doc, { hide() {} });
			},
		});

		async function render() {
			p.clear_page(page);
			form = null; products_list = null; cart_list = null; receipts_list = null;
			shift = await p.call(api + "api_current_shift");
			// 标题沿用侧栏那一项（`nav.cashier`，在 core 的语言包里），不另起一个名字。
			page.set_title(__("nav.cashier"));
			p.breadcrumbs({ label: __("nav.cashier"), route: p.subroute("pearnly-cashier") });
			page.set_indicator(shift
				? t("trade.shiftOpen", shift.shift.shift_seq, p.money(shift.expected_cash))
				: t("trade.noShift"), shift ? "green" : "orange");
			if (canWrite) page.add_inner_button(t("trade.closeShift"), hand_over);
			page.add_inner_button(t("trade.shiftHistory"), () => void shift_history());
			page.add_inner_button(t("trade.cartClear"), clear_cart);
			p.add_refresh(page, refresh);

			const counter = Boolean(shift) && canWrite;
			form = p.layout(page.main, [
				...(counter ? [
					{ fieldtype: "HTML", fieldname: "counter", options: COUNTER_HTML },
				] : []),
				{ fieldtype: "Section Break", label: t("trade.shiftReceipts") },
				{ fieldtype: "HTML", fieldname: "receipts" },
			]);

			if (counter) {
				const host = form.fields_dict.counter.$wrapper;
				const items_pane = host.find(".pearnly-counter-items");
				const lines_pane = host.find(".pearnly-counter-lines");
				// 搜索与扫码放在商品那一栏顶上（官方 POS 也是把筛选/search 放在商品区里）。
				// 两格并排一行：`p.field_rows` 就是官方那套 Column Break 排版。
				const filters = p.layout(host.find(".pearnly-counter-filter"), p.field_rows([
					field("q", "trade.searchProducts", "Data"),
					{ fieldtype: "Button", fieldname: "scan", label: t("trade.scan"),
						click: () => start_scan() },
				], 2));
				products_list = p.list({ page, container: items_pane,
					doctype: "ERP Product", compact_columns: true,
					on_open: row => add_to_cart(row),
					load: ({start, page_length}) => p.call(api + "api_counter_products", {
						terminal: shift.shift.terminal, search: filters.get_value("q") || undefined,
						start, limit: page_length }),
					columns: [
						// 五列要挤进左栏（约 576px）：宽度按"能缩但不挤掉最后一列"给，
						// 之前 240/130/70/100/90 加起来超过栏宽，库存那一列被顶出去了。
						{ fieldname: "product_name", width: 170, min_width: 110, label: t("trade.product") },
						{ fieldname: "code", width: 100, min_width: 72, label: t("trade.code") },
						{ fieldname: "unit", width: 56, min_width: 44, label: t("trade.unit") },
						{ fieldname: "unit_price", width: 84, min_width: 64, label: t("trade.price"),
							format: value => money(value) },
						// 库存不为正就不给"还能卖"的错觉：0 与负数标红（与列表里欠钱同一个红）。
						{ fieldname: "qty_on_hand", width: 70, min_width: 56, label: t("trade.onHand"),
							format: value => p.number(value),
							cell_class: value => Number(value) > 0 ? "" : "pearnly-payment-due" },
					],
				});
				cart_list = p.list({ page, container: lines_pane,
					doctype: "ERP Sale Line", compact_columns: true,
					on_open: row => edit_line(row),
					load: async () => ({ items: cart, total: cart.length }),
					columns: [
						{ fieldname: "product_name", width: 150, min_width: 96, label: t("trade.product") },
						{ fieldname: "qty", width: 62, min_width: 48, label: t("trade.qty"),
							format: value => p.number(value) },
						{ fieldname: "unit_price", width: 84, min_width: 64, label: t("trade.price"),
							format: value => money(value) },
						{ fieldname: "line_total", width: 90, min_width: 70, label: t("trade.amount"),
							format: (_value, row) => money(num(row.qty) * num(row.unit_price) - num(row.discount)) },
					],
					row_actions: row => [{ id: "remove", label: __("Delete"), action: async () => {
						cart = cart.filter(item => item.product !== row.product);
						paint_cart(); await cart_list.reload();
					}}],
				});
				// 商品区那颗搜索框：打完就重取商品（收银台的主路径是"搜/扫 → 加购物车"）。
				filters.fields_dict.q.$input?.on("keydown", event => {
					if (event.key === "Enter") { event.preventDefault(); void products_list.reload(); }
				});
			}

			receipts_list = p.list({ page, container: form.fields_dict.receipts.$wrapper,
				doctype: "ERP Sale", selectable: true, on_selection: selection,
				on_open: row => receipt(row.name),
				load: p.trade.load(api, "api_list_sales", "api_sale_total",
					() => (shift ? { shift: shift.shift.name } : {})),
				columns: [
					{ fieldname: "receipt_no", label: t("trade.docNo") },
					// 收银单据时间是 Datetime（库里带微秒），跟产品里其它时间一样显示到分钟。
					{ fieldname: "sold_at", label: t("trade.date"), format: value => p.datetime_text(value) },
					{ fieldname: "sale_type", label: t("trade.kind"), format: value => kind_label(value) },
					{ fieldname: "status", label: t("trade.status"), format: value => state_label(value) },
					{ fieldname: "grand_total", label: t("trade.total"), format: value => p.money(value) },
				],
			});
			await Promise.all([products_list?.ready, cart_list?.ready, receipts_list.ready]);
			paint_cart();
			if (counter && !cart.length) await cart_list.reload();
		}

		await render();
	};
})();
