/* Shared business line fields, composed from native Dialog/Grid controls. */
(() => {
	const p = frappe.pearnly;
	// 仓库字段一律是**官方原生**的 Link → `ERP Warehouse`，下拉里只列当前公司的仓库
	// （仓库属于公司，一租户多公司）。`df.get_query` 是框架 Link 控件自己认的写法：
	// 给一个普通对象它就当过滤条件用（`link.js`：`$.isPlainObject(get_query)`）。
	p.warehouse_query = () => ({
		filters: {company: frappe.boot.pearnly_company || "", is_active: 1},
	});
	// 收银台同理：属于公司、卖这家公司某个仓的货，所以下拉只列**当前公司**的收银台
	// （别家的柜台选了会把货记到别人账上；服务端也会拒）。
	p.terminal_query = () => ({
		filters: {company: frappe.boot.pearnly_company || "", is_active: 1},
	});
	// 单据写进 Express 的情况（CC 桌面版直连 ERP，ADR-025）：状态、Express 单号、停下的原因；要人拿主意的项在这里选一次
	// （服务端只认这份账套里有的值，选过记在这家公司名下），转人工的可以再写一次。
	const EXPRESS_REASONS = {
		"express.company_tax_missing": "express.reason.taxMissing", "express.account_set_missing": "express.reason.noBook",
		"express.no_writer": "express.reason.noWriter", "express.masters_reading": "express.reason.reading",
		"express.cc_desktop_not_connected": "CC Desktop not connected", "express.cc_desktop_offline": "express.reason.offline",
	};
	const EXPRESS_KINDS = {
		party: {sales: "Customer", purchase: "Supplier"}, item: "Item", account: "express.kind.account",
		department: "Department", payment: "Payment Method", vat: "express.kind.vat", account_set: "Account Set",
		stock_group: "express.kind.stockGroup",
	};
	const EXPRESS_OPTIONS = {cash: "Cash", credit: "Credit", claim: "express.option.claim", expense: "express.option.expense",
		__new__: "New"};
	p.express = async (doctype, name, reload) => {
		const api = "pearnly_erp_portal.express.api.";
		const info = await p.call(api + "document_express", {doctype, name});
		const direction = doctype === "Purchase Document" ? "purchase" : "sales";
		const reason = info.reason && !info.reason.startsWith("choice_needed")
			? __(EXPRESS_REASONS[info.reason] || info.reason) : "";
		const fields = [
			{fieldtype: "Data", fieldname: "state", label: __("Status"), read_only: 1,
				default: __({queued: "Queued", written: "express.state.written", mismatch: "Mismatch", held: "Pending",
					failed: "Failed"}[info.state] || info.state)},
			...(info.docnum ? [{fieldtype: "Data", fieldname: "docnum", label: __("Express No."), read_only: 1, default: info.docnum}] : []),
			...(reason ? [{fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), read_only: 1, default: reason}] : []),
			...info.needed.map((item, index) => {
				const kind = EXPRESS_KINDS[item.kind];
				return {fieldtype: "Select", fieldname: `pick_${index}`, reqd: 1, label: __(kind?.[direction] || kind || item.kind),
					description: item.label || "", default: item.current || "",
					options: [{value: "", label: ""}, ...item.options.map(option => ({value: option.code,
						label: EXPRESS_OPTIONS[option.code] ? __(EXPRESS_OPTIONS[option.code])
							: item.kind === "account_set" ? option.name  // 账套的编码是内部键，只显示名称与位置
							: [option.code, option.name].filter(Boolean).join(" · ")}))]};
			}),
		];
		const action = info.can_answer ? __("Save") : info.can_retry ? __("Retry") : __("Close");
		p.dialog(__("Express"), fields, action, async (values, dialog) => {
			if (info.can_answer) {
				await p.call(api + "answer", {doctype, name, answers: JSON.stringify(info.needed.map((item, index) =>
					({kind: item.kind, subject: item.subject, value: values[`pick_${index}`]})))}, "POST");
				p.success(__("Saved"), "click");
			} else if (info.can_retry) {
				await p.call(api + "retry", {doctype, name}, "POST");
				p.success(__("Queued"), "submit");
			}
			dialog.hide();
			if (info.can_answer || info.can_retry) await reload?.();
		});
	};
	p.trade = {
		// 采购 / 销售网页识别在后台跑（2026-10-02：同步请求读多页 PDF 会撞 120 秒超时）。推送优先、轮询兜底（同 Cowork 录入
		// `intake-upload.js`，几百人同时识别时不能一两秒问一次）：服务端每读完一页、读完都推「pearnly_erp_recognise」给发起人，
		// 收到页数直接更新等候层，收到结论立刻读结果；实时连接在线时 20 秒兜底问一次，不在线 5 秒。读完返回结果；失败按服务端
		// 给的原因用原生提示弹出（与原先同步调用时一样）。服务端说任务已经不在会回失败；30 秒仍问不到任何状态（号丢了）也停。
		async recognise(api, method, args, progress) {
			const key = args.progress_key, started = Date.now();
			let wake = null, pushed = false;
			const heard = data => {
				if (data?.token !== key) return;
				if (data.state) {pushed = true; wake?.();}
				else if (Number.isFinite(data.done)) progress?.({done: data.done, total: data.total});
			};
			frappe.realtime?.on?.("pearnly_erp_recognise", heard);
			try {
				await p.call(api + method, args, "POST");
				for (;;) {
					if (!pushed) await new Promise(resolve => {
						wake = resolve;
						setTimeout(resolve, frappe.realtime?.socket?.connected ? 20000 : 5000);
					});
					wake = null; pushed = false;
					let now;
					try {
						now = (await frappe.call({method: api + "api_recognise_progress", args: {progress_key: key},
							type: "GET", silent: true})).message || {};
					} catch (error) {
						continue; // 一时问不到就下次再问
					}
					if (now.total) progress?.(now);
					if (now.state === "done") return now.result;
					if (now.state === "failed" || (!now.state && Date.now() - started > 30000)) {
						frappe.msgprint({message: now.message || __("Recognition failed. Please try again."), indicator: "red"});
						throw {_server_messages: true};
					}
				}
			} finally {
				frappe.realtime?.off?.("pearnly_erp_recognise", heard);
			}
		},
		// 收银小票的只读查看器：销售记录里点到"来源=POS"的行，或收银页点小票，都开这一个。
		// `actions` 由调用方给（收银页给「退货退款 / 作废」，销售记录只读打开时不给）。
		async receipt_view(name, t, actions = {}) {
			const api = "pearnly_erp_portal.cashier.api.";
			const doc = await p.call(api + "api_get_sale", { name });
			const fields = [
				{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
				{ fieldtype: "Data", fieldname: "status", label: t("trade.status"), read_only: 1,
					default: t(`trade.state.${doc.status}`) },
				// 明细表整表只读：只给内层 `cannot_add_rows/cannot_delete_rows` 的话，弹窗里的
				// 原生表格照样会画出"添加行 / 删除 / 上传 / 批量添加"那一排（实测），点开一张
				// 已卖出的小票不该能改行，所以再加一层字段级 `read_only`。
				{ ...p.trade.lines(t, { readonly: true, data: doc.lines }), read_only: 1 },
				{ fieldtype: "Currency", options: "currency", formatter: value => p.money(value),
					fieldname: "total", label: t("trade.total"), read_only: 1, default: doc.grand_total },
			];
			let dialog;
			if (doc.sale_type === "sale" && actions.refund) {
				fields.push({ fieldtype: "Button", fieldname: "refund", label: t("trade.refund"),
					click: () => actions.refund(doc, dialog) });
			}
			if (actions.void) {
				fields.push({ fieldtype: "Button", fieldname: "void", label: t("trade.void"),
					click: () => frappe.confirm(t("trade.voidConfirm"),
						() => p.trade.act(dialog, () => actions.void(doc, dialog))) });
			}
			dialog = p.dialog(doc.receipt_no, fields, t("trade.close"), async (_values, d) => d.hide());
			dialog.$wrapper.find(".modal-dialog").addClass("modal-xl");
			return doc;
		},
		load(api, method, totalMethod, query = () => ({})) {
			// 列头点一下排序：`sort_by/sort_order` 一路交给服务端（列表是服务端分页的），
			// 具体认哪些字段由各接口自己的白名单决定（见 `pearnly_erp_portal.listing`）。
			return async ({ start, page_length, sort_by, sort_order }) => {
				const [items, total] = await Promise.all([
					p.call(api + method, { ...query(), start, limit: page_length, sort_by, sort_order }),
					p.call(api + totalMethod, query()),
				]); return { items, total };
			};
		},
		async texts() { return p.texts("trade."); },
		// `locked_price`：只锁单价那一格，其余照常能编 —— 出库就是这样（成本由服务端按
		// 当前结存算，界面上看得到、改不了）；整表 `readonly` 是另一回事（只读查看）。
		lines(t, { cost = false, stock = false, readonly = false, locked_price = false,
			data = [], onChange = () => {} } = {}) {
			const price = cost ? "unit_cost" : "unit_price";
			return {
				fieldtype: "Table", fieldname: "lines", label: t("trade.lines"),
				reqd: 1, in_place_edit: true, cannot_add_rows: readonly, cannot_delete_rows: readonly,
				data: data.map(row => ({ currency: "THB", ...row, warehouse: row.warehouse_name || row.warehouse,
					product: readonly ? row.description || row.product_name || row.product : row.product })),
				fields: [
					{ fieldtype: readonly ? "Data" : "Link", fieldname: "product", options: "ERP Product",
						label: t("trade.product"), reqd: stock, in_list_view: 1, columns: 3,
						get_query: () => ({ filters: { is_active: 1 } }),
						formatter: (value, _df, _options, row) => p.escape(row.description || row.product_name || value),
						async onchange() {
							const row = this.doc, selected = row.product;
							if (!selected || readonly) return;
							const item = await p.call("pearnly_erp_portal.products.service.api_get_product", { name: selected });
							if (row.product !== selected) return;
							Object.assign(row, { description: item.product_name, unit: item.unit || item.base_unit,
								// 仓库取**这家公司卖货的那个仓**（进货与卖货同一个仓，库存与金额才对得上）；
								// 商品上那个遗留的"默认仓"已经删掉，不再参与。
								warehouse: frappe.boot.pearnly_warehouse || "",
								location: item.default_location || "",
								[price]: cost ? item.default_cost : item.unit_price });
							this.grid_row.refresh();
						} },
					// 仓库：官方原生 Link（不是自由填写），只列当前公司的仓库。
					{ fieldtype: "Link", options: "ERP Warehouse", get_query: p.warehouse_query(), only_select: true,
						fieldname: "warehouse", label: t("trade.warehouse"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Data", fieldname: "location", label: t("trade.location"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1, in_list_view: 1, columns: 1, formatter: value => p.number(value) },
					{ fieldtype: "Currency", fieldname: price, options: "currency", formatter: value => p.money(value), label: t(cost ? "trade.cost" : "trade.price"), in_list_view: 1, columns: 2 },
					{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
					{ fieldtype: "Data", fieldname: "description", label: t("trade.description"), reqd: !stock },
					{ fieldtype: "Data", fieldname: "unit", label: t("trade.unit") },
					...(!stock ? [
						{ fieldtype: "Currency", fieldname: "discount", options: "currency", label: t("trade.discount"), default: 0 },
						{ fieldtype: "Percent", fieldname: "vat_rate", label: t("trade.vat"), default: 7 },
					] : []),
				 ].map(field => ({ ...field,
					read_only: readonly || (locked_price && field.fieldname === price) ? 1 : 0,
					onchange: async function () {
						await field.onchange?.call(this);
						onChange();
					},
				})),
			};
		},
		clean(lines) {
			// 空白行不发：明细表是可编辑的空表，框架会给它带一行空的（数量默认 1、没有商品、
			// 也没有文字说明）—— 那一行不是业务明细。以前它跟着请求发出去，入库/出库那边
			// 收到没有商品的明细直接抛 KeyError，用户看到的是服务器错误。采购/销售的自由行
			// （只有说明、没有商品）要留下，所以判空是"商品和说明都没有"。
			const kept = (lines || []).filter(row => row
				&& (String(row.product || "").trim() || String(row.description || "").trim()));
			return kept.map(row => Object.fromEntries([
				"product", "warehouse", "location", "description", "qty", "unit", "unit_price",
				"unit_cost", "discount", "vat_rate", "original_line",
			].filter(key => row[key] !== undefined).map(key => [key, row[key]])));
		},
		// ── 明细：逐条摊开（入库/出库的录入**不用表格**）───────────────────────────
		// 用户 2026-09-23："出库和入库界面添加物料那个位置不要用表格，拆出来"。
		// 版式与采购/销售的自由行同一套：**一条一段**（官方可折叠 Section Break）+ 官方控件，
		// 段里的每一格与老表格那一列一一对应；字段顺序照抄老表格（物料、仓库、仓位、数量、
		// 单价、说明、单位），所以换个版式、不换口径。
		// 值仍按 `p.trade.clean()` 认的形状交回去；行里原来带的、界面上没有的键（编辑草稿时
		// 的 `original_line` 这类）原样带回去 —— 老表格是整行对象，换成小表单别把字段丢了。
		// 行里的「仓库」是**表头那一个的镜像**，不是一个能各说各话的第二输入框：
		// 用户 2026-09-23 先指出"仓库重复了"，接着说"去掉了后把上面那个加进去。出入库同步"——
		// 也就是行里要看得到仓库（一眼知道这行进哪个仓），但它跟表头是**同一件事**：只读显示，
		// 表头一变所有行跟着变。服务端口径也是这样（`inventory/positions.py::prepare`：
		// `line.warehouse || doc.warehouse`，两个都没写才落"这家公司卖货的那个仓"）。
		lines_editor(t, { cost = false, locked_price = false, data = [],
			suggest_warehouse, warehouse } = {}) {
			const price = cost ? "unit_cost" : "unit_price";
			const host = $("<div class='pearnly-line-editor'></div>");
			const lines = [];
			const field_names = ["product", "warehouse", "location", "qty", price, "description", "unit", "currency"];
			const any = value => String(value ?? "").trim();
			// 表头那个仓库的现值（由调用方给；行里的镜像和新建的行都用它）。
			const head_warehouse = () => (typeof warehouse === "function" ? warehouse() || "" : "");
			function line_fields(line) {
				return [
					{ fieldtype: "Link", options: "ERP Product", fieldname: "product", label: t("trade.product"),
						reqd: 1, get_query: () => ({filters: {is_active: 1}}),
						async onchange() {
							const selected = line.form.get_value("product");
							if (!selected || !line.ready) return;
							const item = await p.call("pearnly_erp_portal.products.service.api_get_product",
								{name: selected});
							if (line.form.get_value("product") !== selected) return;
							// 售价取主数据里"设过的"那个价（没设过就别替商家宣布免费）；成本直接取主档成本。
							await line.form.set_values({ description: item.product_name,
								unit: item.unit || item.base_unit, location: item.default_location || "",
								[price]: cost ? item.default_cost : (item.price_set ? item.unit_price : 0) });
							// 仓库**建议**给表头那一个 = 这家公司卖货的那个仓（只在它还空着时
							// 填，不覆盖用户选的）。商品上那个遗留的"默认仓"已经删掉。
							suggest_warehouse?.(frappe.boot.pearnly_warehouse);
						} },
					// 仓库：表头的镜像（只读）。放在物料下面 —— 这一行进哪个仓，一眼看得到。
					{ fieldtype: "Link", options: "ERP Warehouse", only_select: true, read_only: 1,
						fieldname: "warehouse", label: t("trade.warehouse") },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Data", fieldname: "location", label: t("trade.location") },
					{ fieldtype: "Float", fieldname: "qty", label: t("trade.qty"), reqd: 1, default: 1,
						precision: 3, formatter: value => p.number(value) },
					{ fieldtype: "Data", fieldname: "unit", label: t("trade.unit") },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Currency", fieldname: price, options: "currency",
						label: t(cost ? "trade.cost" : "trade.price"),
						// 出库不给人填价：只锁这一格，行照样能加能改（结存成本由服务端算）。
						read_only: locked_price ? 1 : 0, formatter: value => p.money(value) },
					{ fieldtype: "Data", fieldname: "description", label: t("trade.description") },
					{ fieldtype: "Data", fieldname: "currency", default: "THB", hidden: 1 },
					{ fieldtype: "Button", fieldname: "remove", label: __("Delete"),
						click: () => drop(line) },
				];
			}
			// 段标题按当前顺序重排（删掉中间一条之后，后面的段号要接上）。
			function relabel() {
				lines.forEach((line, index) => {
					line.section.head.contents().filter((_, node) => node.nodeType === Node.TEXT_NODE)
						.first().replaceWith(document.createTextNode(`${t("trade.lines")} ${index + 1} `));
				});
			}
			function drop(line) {
				line.wrapper.remove();
				lines.splice(lines.indexOf(line), 1);
				relabel();
			}
			// 扫码枪：码打完立刻补一个回车。网慢时 Link 下拉的结果还没回来，回车什么也没选中，
			// 离开这一格时框架又把「不是商品编号的这串码」清掉，这一行就丢了（录制问题 #18）。
			// 所以回车时如果下拉里没有选中项，就按码直接查商品（编码 / 条码 / 二维码 / 单位条码，
			// 与扫码查商品同一个接口），查到就填上。
			function scan_by_code(line) {
				const control = line.form.fields_dict.product;
				control.$input.on("keydown", event => {
					if (event.key !== "Enter") return;
					const code = String(control.$input.val() || "").trim();
					const picked = control.awesomplete?.ul?.querySelector("[aria-selected='true']");
					if (!code || picked) return;
					event.preventDefault();
					void p.call("pearnly_erp_portal.products.service.api_find_product", {value: code}).then(found => {
						if (found?.name && line.form.get_value("product") !== found.name) {
							return line.form.set_value("product", found.name).then(() => control.awesomplete?.close());
						}
					});
				});
			}
			function add(values = {}, collapse_previous = true) {
				if (collapse_previous) lines.forEach(line => line.section.collapse(true));
				const wrapper = $("<div>").appendTo(host);
				const outer = p.layout(wrapper, [
					{ fieldtype: "Section Break", label: `${t("trade.lines")} ${lines.length + 1}`, collapsible: 1 },
					{ fieldtype: "HTML", fieldname: "fields" },
				]);
				const line = { wrapper, section: outer.sections[0], ready: false,
					stash: Object.fromEntries(Object.entries(values).filter(([key]) => !field_names.includes(key))) };
				line.form = p.layout(outer.fields_dict.fields.$wrapper, line_fields(line));
				scan_by_code(line);
				lines.push(line);
				relabel();
				// 先铺值再放开 onchange：初始化时别把"带出来的"值又算一遍。
				// 行里的仓库永远是**表头那一个**（旧行自带的仓库不覆盖它 —— 那是老表格的遗留）。
				void line.form.set_values({ qty: 1, currency: "THB", ...values, warehouse: head_warehouse() }).then(() => {
					line.ready = true;
					// 只有**最后那一条**默认展开：铺一叠草稿行时不会一串全开着（前面几条在
					// `add` 开头就收起来了；这里判"还是不是最后一条"，避免异步回来时又展开）。
					if (lines[lines.length - 1] === line) line.section.collapse(false);
				});
				return line;
			}
			return {
				host, add, lines,
				// 表头那个仓库变了：所有行跟着变（行里那格是它的镜像，不是第二个源）。
				set_warehouse(name) {
					lines.forEach(line => line.form.set_value("warehouse", name || ""));
				},
				// 交回明细：默认**校验**（必填没填会就地标红并展开那一段，同时返回 null 让调用方别提交）；
				// `check: false` 是"搬家"（弹窗→整页）用的，只取值不拦人。
				values({ check = true } = {}) {
					const out = [];
					for (const line of lines) {
						// 空行（没选物料、也没写字）等于没填：老表格就是把它丢掉的，不拿它拦提交。
						if (!any(line.form.get_value("product")) && !any(line.form.get_value("description"))) continue;
						const row = line.form.get_values(!check, check);
						if (!row) { line.section.collapse(false); return null; }
						out.push({ ...line.stash, ...row });
					}
					return out;
				},
			};
		},
		async act(dialog, callback) {
			if (dialog.pearnly_busy) return;
			dialog.pearnly_busy = true;
			dialog.$wrapper.find("button").prop("disabled", true);
			try { await callback(); }
			finally { dialog.pearnly_busy = false; dialog.$wrapper.find("button").prop("disabled", false); }
		},
	};
})();
