/* 收银 POS：开班 / 收银台 / 当班小票 / 交班 / 班次记录。
 *
 * 收银台照 **ERPNext POS 那套交互**做（本仓 2026-09-21 的决定："POS 借 ERPNext 的前端
 * 体验与交互，但业务后端仍走 Pearnly 的商品、库存、收发存与销售"；用户 2026-09-23 又
 * 明确按 ERPNext 对）。骨架对着框架那份实现摆：
 *
 * · 商品区是**卡片网格**，不是列表 —— 卡上：库存色点（>10 绿 / ≤0 红 / 其余橙，
 *   与 `pos_item_selector.js` 同一条规则）、商品图或缩写（`frappe.get_abbr`）、
 *   名称、`售价 / 单位`；
 * · 购物车带**合计区**：总件数 / 净额 / 合计；
 * · 购物车下面挂**常驻数字键盘**：`1 2 3 数量 / 4 5 6 折扣 / 7 8 9 单价 / . 0 ⌫ 移除`
 *   + 底部「收款」；收款弹窗里另有一副 3 列键盘（照官方收款面板）。
 *
 * 后端仍然是我们自己的：商品/售价/结存读 `cashier/counter.py`（结存就是收发存那张
 * `ERP Stock Balance`），落单走 `cashier.sales.create_sale` —— 一笔收银落一张单、
 * 扣一次库存、进一次收发存，单号与销售记录同一个序列。
 *
 * 数字键盘在官方也是个自造件（Frappe 没有这个控件，ERPNext 自己写了
 * `pos_number_pad.js`），所以这里同样自造，交互照它；其余控件一律官方原生。
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

		let shift, form, receipts_list, active = null, active_field = "qty", buffer = "";
		// 当班小票那一节默认收起、按钮切换（官方 POS 的 "Recent Orders" 就是这样一个切换项）。
		let receipts_shown = false;
		// 购物车只是这一屏的状态，不落库：收款那一下才由服务端成单（与旧线上一致）。
		let cart = [];
		// 商品区是"分页 + 加载更多"（官方 POS 也是先给一页）。
		let items = { rows: [], total: 0, start: 0 };
		const PAGE_SIZE = 24;

		const COUNTER_HTML = `<div class="pearnly-pos">
			<div class="pearnly-pos-left">
				<div class="pearnly-pos-filter"></div>
				<div class="pearnly-pos-grid"></div>
				<button type="button" class="btn btn-default btn-sm pearnly-pos-more"></button>
			</div>
			<div class="pearnly-pos-right">
				<div class="pearnly-pos-lines"></div>
				<div class="pearnly-pos-totals"></div>
				<div class="pearnly-pos-pad"></div>
			</div>
		</div>`;

		// 官方那条色点规则：>10 绿、≤0 红、其余橙（`pos_item_selector.js`）。
		const pill_class = qty => (qty > 10 ? "green" : qty <= 0 ? "red" : "orange");
		const clip = (text, count) => {
			const value = String(text ?? "");
			return value.length > count ? value.slice(0, count) + "…" : value;
		};
		const cart_total = () => round2(cart.reduce(
			(sum, row) => sum + num(row.qty) * num(row.unit_price) - num(row.discount), 0));
		const pane = selector => form?.fields_dict?.counter?.$wrapper.find(selector);

		// ── 页面动作 ────────────────────────────────────────────────────────────
		// 右上角照官方那三处摆：主按钮「新单」、次按钮「当班小票」（切换列表显隐）、
		// 菜单里放「交班 / 班次记录 / 清空购物车」（官方是 Open Form View / Close the POS）。
		// 收款不在这里 —— 它在购物车面板的键盘下面（官方也是把 Checkout 放那儿）。
		// 没开班时主按钮换成「开班」（对应官方那句 "Create POS Opening Entry"）。
		//
		// 为什么要一个函数：列表的选中状态一刷新就会去清主按钮，不把页面动作交回去，
		// 「开班 / 新单」会被列表刷掉（商品页当初踩的同一个坑）。
		function restore_primary() {
			if (!canWrite) { page.clear_primary_action(); return; }
			if (!shift) page.set_primary_action(t("trade.openShift"), () => void open_shift());
			else page.set_primary_action(t("trade.newInvoice"), new_invoice);
		}

		// 「新单」= 把这一单清干净重新开始（官方 primary_action "New Invoice"）。
		function new_invoice() {
			cart = []; active = null; buffer = ""; active_field = "qty";
			paint_cart();
		}

		// 「当班小票」= 那一节的显隐开关（官方 secondary_action "Recent Orders"）。
		function toggle_receipts() {
			const wrapper = form?.fields_dict?.receipts?.$wrapper;
			if (!wrapper?.length) return;
			receipts_shown = !receipts_shown;
			wrapper.closest(".form-section").toggle(receipts_shown);
		}

		async function refresh() {
			shift = await p.call(api + "api_current_shift");
			paint_shift_indicator();
			restore_primary();
			if (shift) await load_items(true);
			paint_cart();
			await receipts_list?.reload();
		}

		// 开班信息摆在标题旁那颗原生指示器里：班次号、开班时间、应有现金。
		//
		// 官方 ERPNext 用的是 `page.set_title_sub("Opened at …")`，但**我们这套页面壳里没有
		// 那个副标题位** —— 框架的 `Page.$sub_title_area` 取的是 `wrapper.find("h6")`，
		// 而产品页面里 h6 数量是 0（实测），那句调用是空的。这里不往 DOM 里塞补丁，
		// 改用同样原生、且一定渲染得出来的指示器，信息一样（三个事实都在）。
		function paint_shift_indicator() {
			page.set_indicator(shift
				? t("trade.shiftOpenedAt", shift.shift.shift_seq,
					p.datetime_text(shift.shift.opened_at), p.money(shift.expected_cash))
				: t("trade.noShift"), shift ? "green" : "orange");
		}

		// ── 开班 ────────────────────────────────────────────────────────────────
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

		// ── 商品卡网格 ──────────────────────────────────────────────────────────
		function item_card(row) {
			const qty = num(row.qty_on_hand);
			const name = p.escape(row.product_name || row.name);
			const image = row.image_url
				? `<img class="pearnly-pos-img" src="${p.escape(row.image_url)}" alt="">`
				: p.escape(frappe.get_abbr(row.product_name || ""));
			return `<div class="pearnly-pos-card" data-product="${p.escape(row.name)}" title="${name}">
				<div class="pearnly-pos-pill"><span class="indicator-pill whitespace-nowrap ${pill_class(qty)}">${p.escape(p.number(qty))}</span></div>
				<div class="pearnly-pos-display${row.image_url ? "" : " abbr"}">${image}</div>
				<div class="pearnly-pos-detail">
					<div class="pearnly-pos-name">${p.escape(clip(row.product_name, 18))}</div>
					<div class="pearnly-pos-rate">${p.escape(money(row.unit_price))} / ${p.escape(row.unit || "")}</div>
				</div>
			</div>`;
		}

		async function load_items(reset) {
			if (!shift) return;
			if (reset) items = { rows: [], total: 0, start: 0 };
			const result = await p.call(api + "api_counter_products", {
				terminal: shift.shift.terminal, search: form?.get_value("q") || undefined,
				start: items.start, limit: PAGE_SIZE });
			const rows = result?.items || [];
			items = { rows: reset ? rows : items.rows.concat(rows),
				total: num(result?.total), start: items.start + rows.length };
			const grid = pane(".pearnly-pos-grid");
			grid.html(items.rows.length ? items.rows.map(item_card).join("")
				: `<div class="text-muted pearnly-pos-empty">${p.escape(__("No Data"))}</div>`);
			const more = pane(".pearnly-pos-more");
			more.text(`${__("Load More")} (${items.rows.length} / ${items.total})`);
			more.toggle(items.rows.length < items.total);
		}

		// ── 购物车（行 + 合计区 + 键盘）─────────────────────────────────────────
		function add_to_cart(row, qty = 1) {
			if (!row?.name) return;
			const line = cart.find(item => item.product === row.name);
			if (line) line.qty = round2(num(line.qty) + qty);
			else cart.push({
				product: row.name, product_name: row.product_name, description: row.product_name,
				code: row.code, unit: row.unit, warehouse: row.warehouse, location: row.location || "",
				unit_price: num(row.unit_price), qty, discount: 0, qty_on_hand: num(row.qty_on_hand),
			});
			active = row.name;
			buffer = "";
			paint_cart();
		}

		function line_html(row) {
			const line_total = money(num(row.qty) * num(row.unit_price) - num(row.discount));
			return `<div class="pearnly-pos-line${active === row.product ? " active" : ""}" data-product="${p.escape(row.product)}">
				<div class="pearnly-pos-cell name">${p.escape(clip(row.product_name, 16))}</div>
				<div class="pearnly-pos-cell">${p.escape(p.number(row.qty))}</div>
				<div class="pearnly-pos-cell">${p.escape(money(row.unit_price))}</div>
				<div class="pearnly-pos-cell">${p.escape(line_total)}</div>
			</div>`;
		}

		function pad_html() {
			const rows = [["1", "2", "3", t("trade.qty"), "qty"],
				["4", "5", "6", t("trade.discount"), "discount"],
				["7", "8", "9", t("trade.price"), "unit_price"],
				["decimal", "0", "backspace", t("trade.remove"), "remove"]];
			const key_html = key => `<button type="button" class="btn btn-default pearnly-pos-key" data-key="${key}">${
				key === "decimal" ? "." : key === "backspace" ? "⌫" : key}</button>`;
			return rows.map(row => {
				const [a, b, c, label, action] = row;
				return `<div class="pearnly-pos-pad-row">${[a, b, c].map(key_html).join("")}
					<button type="button" class="btn btn-default pearnly-pos-key pearnly-pos-target${active_field === action ? " active" : ""}" data-target="${action}">${p.escape(label)}</button>
				</div>`;
			}).join("")
				+ `<button type="button" class="btn btn-primary pearnly-pos-pay">${p.escape(t("trade.action.pay"))}</button>`;
		}

		function paint_cart() {
			const lines = pane(".pearnly-pos-lines");
			if (!lines.length) return;
			const count = cart.reduce((sum, row) => sum + num(row.qty), 0);
			const subtotal = round2(cart.reduce((sum, row) => sum + num(row.qty) * num(row.unit_price), 0));
			lines.html(`<div class="pearnly-pos-line head">
					<div class="pearnly-pos-cell name">${p.escape(t("trade.product"))}</div>
					<div class="pearnly-pos-cell">${p.escape(t("trade.qty"))}</div>
					<div class="pearnly-pos-cell">${p.escape(t("trade.price"))}</div>
					<div class="pearnly-pos-cell">${p.escape(t("trade.amount"))}</div>
				</div>`
				+ (cart.length ? cart.map(line_html).join("")
					: `<div class="text-muted pearnly-pos-empty">${p.escape(t("trade.cartEmpty"))}</div>`));
			pane(".pearnly-pos-totals").html(
				`<div class="pearnly-pos-total-row"><span>${p.escape(t("trade.totalItems"))}</span><span>${p.escape(p.number(count))}</span></div>
				 <div class="pearnly-pos-total-row"><span>${p.escape(t("trade.net"))}</span><span>${p.escape(money(subtotal))}</span></div>
				 <div class="pearnly-pos-total-row grand"><span>${p.escape(t("trade.total"))}</span><span>${p.escape(money(cart_total()))}</span></div>`);
			pane(".pearnly-pos-pad").html(pad_html());
		}

		// 键盘：点数字就写进"当前选中行"的"当前字段"（官方就是这套：先选行、再选
		// 数量/折扣/单价，然后敲数字）。`buffer` 是这一串输入，第一次敲数字会替换原值。
		function pad_key(key) {
			const line = cart.find(row => row.product === active);
			if (!line) { frappe.msgprint(t("trade.pickLine")); return; }
			if (key === "backspace") buffer = "";
			else if (key === "decimal") { if (!buffer.includes(".")) buffer += buffer ? "." : "0."; }
			else buffer = (buffer + key).replace(/^0(?=\d)/, "");
			const value = buffer === "" ? 0 : Math.max(0, num(buffer));
			line[active_field] = round2(value);
			paint_cart();
		}

		// ── 扫码（框架自带的 `frappe.ui.Scanner`）───────────────────────────────
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

		function pay_pad_html() {
			const rows = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["sign", "0", "backspace"]];
			return `<div class="pearnly-pos-paypad">` + rows.map(row =>
				`<div class="pearnly-pos-pad-row">${row.map(key =>
					`<button type="button" class="btn btn-default pearnly-pos-key" data-pay-key="${key}">${
						key === "backspace" ? "⌫" : key === "sign" ? "±" : key}</button>`).join("")}</div>`).join("")
				+ `</div>`;
		}

		// ── 收款（弹窗里也带一副 3 列键盘，照官方收款面板）──────────────────────
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
					options: "cash\ncard\npromptpay\ntransfer", default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "amount",
					label: t("trade.paid"), reqd: 1, default: total, change: paint_change },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Currency", options: "currency", formatter: money, fieldname: "change",
					label: t("trade.change"), read_only: 1, default: 0 },
				{ fieldtype: "Small Text", fieldname: "note", label: t("trade.note") },
				{ fieldtype: "HTML", fieldname: "pad", options: pay_pad_html() },
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
				cart = []; active = null; buffer = "";
				// 卖出去就少货：商品卡的结存要跟着变，所以商品区也重取。
				await refresh();
				await receipt(result.name);
			});
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			// 键盘直接写"实收"那一格；找零跟着变。
			let pay_buffer = "";
			const write_amount = () => {
				const value = pay_buffer === "" ? 0 : Math.max(0, num(pay_buffer));
				dialog.set_value("amount", round2(value));
				paint_change();
			};
			dialog.fields_dict.pad.$wrapper.on("click", "[data-pay-key]", event => {
				const key = event.currentTarget.dataset.payKey;
				if (key === "backspace") pay_buffer = "";
				else if (key === "sign") pay_buffer = pay_buffer.startsWith("-")
					? pay_buffer.slice(1) : "-" + pay_buffer;
				else if (key === "decimal") { if (!pay_buffer.includes(".")) pay_buffer += pay_buffer ? "." : "0."; }
				else pay_buffer = (pay_buffer + key).replace(/^0(?=\d)/, "");
				write_amount();
			});
		}

		// ── 小票、退货、班次 ────────────────────────────────────────────────────
		async function receipt(name) {
			// 小票查看器是共享的（`p.trade.receipt_view`）：收银页、销售记录点"来源=POS"的行、
			// 收银记录页三处用同一个，避免各写一份以后走样。收银页多给两个动作。
			await p.trade.receipt_view(name, t, canWrite ? {
				refund: (doc, parent) => refund(doc, parent),
				void: async (doc, parent) => {
					await p.call(api + "api_void_sale", { name: doc.name }, "POST");
					parent?.hide?.();
					await refresh();
				},
			} : {});
		}

		function refund(doc, parent) {
			const request = crypto.randomUUID();
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Select", fieldname: "original_line", label: t("trade.originalLine"), reqd: 1,
					options: doc.lines.map(row => ({ value: row.name, label: `${row.description || row.product} · ${row.warehouse_name || ""} · ${row.location || ""} · ${p.number(row.qty)}` })) },
				{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1 },
				{ fieldtype: "Select", fieldname: "method", label: t("trade.method"), options: "cash\ncard\npromptpay\ntransfer", default: "cash", reqd: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value), fieldname: "amount", label: t("trade.refundAmount"), reqd: 1 },
			];
			let d;
			const calculate = () => {
				if (!d) return;
				const line = doc.lines.find(row => row.name === d.get_value("original_line"));
				if (line) d.set_value("amount", Math.round(Number(line.line_total) * Number(d.get_value("qty") || 0) / Number(line.qty) * 100) / 100);
			};
			fields.find(item => item.fieldname === "original_line").change = calculate;
			fields.find(item => item.fieldname === "qty").change = calculate;
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

		function clear_cart() {
			if (!cart.length) return;
			frappe.confirm(t("trade.cartClearConfirm"), () => {
				cart = []; active = null; buffer = "";
				paint_cart();
			});
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
			// `p.clear_page` 不清菜单与次按钮，而这个函数每次开班/交班都会重跑一遍 ——
			// 不自己清，「交班 / 班次记录 / 清空购物车」会一轮一轮往上叠。
			page.clear_menu?.(); page.clear_secondary_action?.();
			form = null; receipts_list = null; active = null; buffer = "";
			shift = await p.call(api + "api_current_shift");
			// 标题沿用侧栏那一项（`nav.cashier`，在 core 的语言包里），不另起一个名字。
			page.set_title(__("nav.cashier"));
			p.breadcrumbs({ label: __("nav.cashier"), route: p.subroute("pearnly-cashier") });
			paint_shift_indicator();
			// 页面动作：主按钮「新单/开班」、次按钮「当班小票」、菜单里三件（照官方那三处）。
			if (canWrite) page.set_secondary_action(t("trade.shiftReceipts"), toggle_receipts);
			if (canWrite) page.add_menu_item(t("trade.closeShift"), hand_over);
			page.add_menu_item(t("trade.shiftHistory"), () => void shift_history());
			page.add_menu_item(t("trade.cartClear"), clear_cart);
			p.add_refresh(page, refresh);

			const counter = Boolean(shift) && canWrite;
			form = p.layout(page.main, [
				...(counter ? [{ fieldtype: "HTML", fieldname: "counter", options: COUNTER_HTML }] : []),
				{ fieldtype: "Section Break", label: t("trade.shiftReceipts") },
				{ fieldtype: "HTML", fieldname: "receipts" },
			]);
			// 当班小票默认收起，按钮切换（与官方 "Recent Orders" 一个用法）。
			receipts_shown = false;
			form.fields_dict.receipts.$wrapper.closest(".form-section").hide();

			if (counter) {
				// 搜索与扫码放在商品区顶上（官方 POS 也把筛选放在商品区里）。
				const filters = p.layout(pane(".pearnly-pos-filter"), p.field_rows([
					field("q", "trade.searchProducts", "Data"),
					{ fieldtype: "Button", fieldname: "scan", label: t("trade.scan"),
						click: () => start_scan() },
				], 2));
				// 搜索框那一格同时也是"商品区取数"的口径（`load_items` 读它）。
				form.fields_dict.q = filters.fields_dict.q;
				filters.fields_dict.q.$input?.on("keydown", event => {
					if (event.key === "Enter") { event.preventDefault(); void load_items(true); }
				});
				await load_items(true);
				paint_cart();
				// 商品卡 / 购物车行 / 键盘：一块代理接住（卡片会随搜索重画）。
				const counter_host = pane(".pearnly-pos");
				counter_host.on("click", ".pearnly-pos-card", event => {
					const name = event.currentTarget.dataset.product;
					const row = items.rows.find(item => item.name === name);
					if (row) add_to_cart(row);
				});
				pane(".pearnly-pos-more").on("click", () => void load_items(false));
				counter_host.on("click", ".pearnly-pos-line:not(.head)", event => {
					active = event.currentTarget.dataset.product;
					buffer = ""; paint_cart();
				});
				counter_host.on("click", ".pearnly-pos-key", event => {
					const target = event.currentTarget.dataset.target;
					if (target === "remove") {
						cart = cart.filter(row => row.product !== active);
						active = cart.length ? cart[cart.length - 1].product : null;
						buffer = ""; paint_cart();
						return;
					}
					if (target) { active_field = target; buffer = ""; paint_cart(); return; }
					pad_key(event.currentTarget.dataset.key);
				});
				counter_host.on("click", ".pearnly-pos-pay", checkout);
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
			await receipts_list.ready;
			paint_cart();
			restore_primary();
		}

		await render();
	};
})();
