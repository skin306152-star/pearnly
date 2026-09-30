/* 二级页面：左边是识别回填（复核），右边是上传。四组选择在弹窗里选
 *（见 `intake-choices.js`），这一页只管上传与复核 —— 新建录入是这一页，
 *从列表点「复核」进来也是这一页，只是标题分别叫「上传」与「复核」。
 *
 * 版面照 ERP 的录入页：左边字段区、右边原件区（窄屏自动上下排，见 `intake.css`）。
 *
 *   · 上传：原件的入口是 `pearnly_cowork.files.api.upload`（multipart + 幂等键），
 *     不经过框架的 `/api/method/upload_file`；归哪一份公司资料由服务端解析；
 *   · 回填：识别受理后拿到的记录读进左边字段（原生 FieldGroup）；
 *   · 复核：改完点「保存」，按记录版本写回（`history.api.save`，别人先改过就冲突回来）。
 *
 * 路由：`intake/new` 新建一批（弹窗口径在这一批里沿用，页面不跳路由，免得刚传完的
 * 清单和这一批的选择在跳转时丢掉）；`intake/new/<记录>`：复核已有记录（字段从记录读）。
 */
(() => {
	const p = frappe.pearnly;
	const FILES = "pearnly_cowork.files.api.";
	const OCR = "pearnly_cowork.ocr.api.";
	const HISTORY = "pearnly_cowork.history.api.";
	const PUSH = "pearnly_cowork.push.application.api.";
	// Express 的准备要再经小助手确认入口「确认发送」；MR.ERP 在服务端「提交」里一并确认。
	const EXPRESS_CONFIRM = "pearnly_cowork.companion.submission.confirm";
	const CONNECTIONS = "pearnly_cowork.connections.api.";
	const ROUNDTRIP = "pearnly_cowork.roundtrip.api.";
	const uuid = () => (crypto?.randomUUID ? crypto.randomUUID()
		: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);
	const STATES = {new: "Pending", uploading: "Uploading", uploaded: "Uploaded",
		submitting: "Submitting", recognizing: "intake.status.recognizing",
		recognized: "intake.status.recognized", not_invoice: "not_invoice", failed: "Failed"};
	// 识别是后台排队做的：受理后按旧线上 `ocr-job-poll.ts` 的节奏轮询批次，直到有结论。
	//   每 1.5 秒一次、最长 60 分钟（服务端排队 30 分钟没开始才过期、开始处理后不再按提交时间过期；
	//   原来 20 分钟就在页面上判超时，服务端其实还在做 · 2026-09-28 压测）；连续 10 次读不到状态（约 15 秒）才算失联。
	const POLL = {every_ms: 1500, max_ms: 60 * 60 * 1000, soft_fails: 10};
	// 批次状态：`saved` 有记录、`not_invoice` 没有发票；任务 `failed/uncertain/cancelled` 是失败；
	// 其余（受理、排队、运行、延后、识别中）都还在做。
	const FINISHED = new Set(["saved", "not_invoice"]);
	const BROKEN = new Set(["failed", "uncertain", "cancelled"]);
	// 旧线上同时识别 6 份（`dms-intake-invoice.ts` 的 worker 池）。
	const PARALLEL = 6;
	const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
	/* 复核字段照旧线上 `dms-intake-review-fields.ts`（ec2e5c95）：对方（进项看卖方、销项看买方）
	 * 名称与税号、单据号、票面日期、未税、税额、价税合计、另一方名称与税号、预扣税额；
	 * 分组与顺序照 ERP 采购页（用户 2026-09-27「排版做成一致」），并补上识别已读到、ERP 页也有的
	 * 对方地址 / 分店、票种、折扣、币种、备注（ERP 自己记账才用的入账日期、到期日、汇率等不加）。
	 * 表单字段名按「位置」取（party / other），存回记录时按进项 / 销项落到 seller_* / buyer_*。
	 * 日期改的是**票面日期** `date_raw`（佛历原样），公历 `date` 由服务端换算（改 `date` 会被覆盖）。 */
	const PARTY = direction => (direction === "sales"
		? {party: "buyer", other: "seller"} : {party: "seller", other: "buyer"});
	const FIELD_KEYS = direction => {
		const {party, other} = PARTY(direction);
		return {party_name: `${party}_name`, party_tax: `${party}_tax`, party_addr: `${party}_addr`,
			party_branch: `${party}_branch`, invoice_number: "invoice_number", date_raw: "date_raw",
			document_type: "document_type", subtotal: "subtotal", vat: "vat", discount: "discount",
			total_amount: "total_amount", currency: "currency", notes: "notes",
			other_name: `${other}_name`, other_tax: `${other}_tax`, wht_amount: "wht_amount",
			direction: "direction", posting_payment_manual: "posting_payment_manual", posting_kind: "posting_kind",
			posting_item_type_manual: "posting_item_type_manual"};
	};
	const EDITABLE = Object.keys(FIELD_KEYS(""));
	// 票种码表与名称照共用层 `pearnly_core.tax_documents.codes`（ADR-0012，ERP 卡片同一份）。
	const DOCUMENT_TYPES = [["tax_invoice", "Tax Invoice"], ["simplified_tax_invoice", "Abbreviated Tax Invoice"],
		["receipt", "Receipt"], ["credit_note", "Credit Note"], ["payment_evidence", "Payment Evidence"],
		["order_evidence", "Order Evidence"], ["other", "Other"]];
	// 空着要提醒的（旧版 `warnFields`）：单据号、价税合计、对方税号；销项的简易税票 / 收据可以没有买方税号。
	const ANONYMOUS_BUYER = new Set(["simplified_tax_invoice", "receipt"]);
	// 明细四列照旧版（名称 / 数量 / 单价 / 金额，键是 `price` / `subtotal`），能改、不能增删行；
	// 行级过账类型是新平台加的（Express 行级判据 `item_posting_kinds`），MR.ERP 批次不摆。
	const ITEM_COLUMNS = ["name", "qty", "price", "subtotal", "posting_kind"];
	// 过账类型是 `service` / `stock`（DocType 的 Select）：下拉的选项文案按术语表来。
	const KIND_OPTIONS = [{value: "", label: ""}, {value: "service", label: __("Service")},
		{value: "stock", label: __("Stock")}];
	const clone = value => JSON.parse(JSON.stringify(value ?? null));
	/** 主页面：服务端的合计、推送、学习都只认它（`contracts/history.py::extract_summary_fields` 同一判据）。 */
	const primary_index = pages => {
		const score = page => ["invoice_number", "total_amount", "seller_name", "date"]
			.filter(key => (page?.fields || {})[key]).length;
		const first = pages.findIndex(page => !page?.is_copy && !page?.is_duplicate
			&& ["invoice_number", "total_amount", "seller_name"].some(key => (page?.fields || {})[key]));
		if (first >= 0) return first;
		let best = -1;
		pages.forEach((page, index) => {if (score(page) > 0 && (best < 0 || score(page) > score(pages[best]))) best = index;});
		return best >= 0 ? best : (pages.length ? 0 : -1);
	};
	/** 这张票自己的原件页（实体页才能出图，去重、按页码），预览框按它串起来。 */
	const own_pages = record => {
		if (!record?.source_file || record.source_reference_kind !== "physical_page") return [];
		const indices = Array.isArray(record.source_page_indices) && record.source_page_indices.length
			? record.source_page_indices : (record.pages || []).map(page => page?.page_index);
		return [...new Set(indices.filter(Number.isInteger))].sort((a, b) => a - b);
	};
	/** 漏识别信号：流水线在页上留的 `possible_missed_invoice*`（旧版据此出「请复核」）。 */
	const maybe_missed = record => (record?.pages || []).some(page =>
		[...(page?._validation_warnings || []), ...(page?._trigger_reasons || [])]
			.some(text => String(text).startsWith("possible_missed_invoice")));
	/** 需复核原因的文案（`cowork.review.<码>`）；税号类带上是哪一格。 */
	const REVIEW_RESOLVED = "review_resolved";
	// 看过就可以点掉的提醒（识别时留下的、或写法类的）；税号 / 金额 / 明细这类改对了自然消失，不给点掉。
	const REVIEW_DISMISSABLE = new Set(["missed_invoice", "page_unread", "uncertain", "duplicate", "number_unseen",
		"voucher_number", "number_pattern"]);
	const TAX_LABELS = {seller_tax: "intake.field.sellerTax", buyer_tax: "intake.field.buyerTax"};
	p.intake_review_text = (reason, t) => {
		const label = TAX_LABELS[reason?.field] ? t(TAX_LABELS[reason.field]) : "";
		if (reason?.code === "missed_invoice") return t("intake.review.missed");
		if (reason?.code === "tax_length" && reason.suggest) return __("cowork.review.tax_suggest", [label]);
		if (reason?.code === "number_pattern") {
			return __("cowork.review.number_pattern", [String(reason.usual || "").replace(/9/g, "#")]);
		}
		return __(`cowork.review.${reason?.code}`, [label]);
	};
	/** 推送失败 / 退回原因 → 一句人话（`intake.error.<码>`）：小助手回报是 `[码] 原文`，服务端预检是 `码` 或 `码:细节`；
	 * 认不出码、或这个码没有词条的，原样显示（服务端直接给的已翻译句子就是这种）。 */
	p.intake_push_reason = reason => {
		const text = String(reason || "").trim();
		const code = text.match(/^\[([A-Z0-9_]+)\]/)?.[1] || text.match(/^EXPRESS_MANUAL:\s*([a-z_]+)/)?.[1]
			|| text.match(/^([A-Za-z][A-Za-z0-9_]*)(?=$|:)/)?.[1];
		if (!code) return text;
		const key = `intake.error.${code}`;
		const translated = __(key);
		return translated === key ? text : translated;
	};
	/** 结果不明（写入已发出、没拿到结果）的原因码：会计去 ERP 核对后在复核页解除。 */
	const RESULT_UNKNOWN = /^(ERP_CONFIRMED_PUSH_RESULT_UNKNOWN|EXPRESS_MANUAL:\s*confirmed_push_unacked|confirmed_push_unacked|ERR_MRERP_READBACK_NOT_FOUND|ERR_MRERP_READBACK_NOT_LINKED|ERR_MRERP_LEGACY_MENU_UNVERIFIED)/;
	/** 这两种退回是「Express 已有 / 上一版还在」：会计核对后可以「仍然推送」（小助手认 `duplicate_confirmed`）。 */
	const SEND_ANYWAY = /^\[(DUPLICATE_CONTENT_DIFFERS|PRIOR_DOC_STILL_IN_ERP)\]/;
	/** 失败原因：有对应文案就用（`intake.error.<码>`），否则按原码查通用词条。 */
	const code_text = code => {
		if (!code) return "";
		const key = `intake.error.${code}`;
		const text = __(key);
		return text === key ? __(code) : text;
	};
	// 原件页图：Cowork 自己的私有文件服务（PDF 与图片都从它出，见 `files.api.page`）。
	const page_url = (history, physical_page) => `/api/method/${FILES}page?`
		+ new URLSearchParams({history_name: history, physical_page: String(physical_page)});

	const size_text = bytes => {
		const value = Number(bytes) || 0;
		if (value < 1024) return `${value} B`;
		if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
		return `${(value / 1024 / 1024).toFixed(1)} MB`;
	};

	/** 服务端的拒绝原因：优先 `_server_messages` 里那句，读不出来才退回状态码。 */
	const failure = (payload, status) => {
		try {
			for (const item of JSON.parse(payload?._server_messages || "[]")) {
				const message = JSON.parse(item)?.message;
				if (message) return String(message).replace(/<[^>]*>/g, "").trim();
			}
		} catch (error) {
			// 不是标准错误体（网关 502 之类）—— 下面按状态码说。
		}
		return payload?.exception || `HTTP ${status}`;
	};

	/** 手机上的分区导航，照 ERP 采购页 `purchase-layout.js`：借 Desk 的 `form-tabs-list` 样式，
	 *  只负责滚到对应分区、滚动时标出当前分区，不切换 / 不隐藏内容，也不带 tab 角色；桌面不显示。 */
	const section_nav = (page, body, regions) => {
		const nav = $('<nav class="form-tabs-list pearnly-intake-nav">').insertBefore(body);
		const links = $('<ul class="nav form-tabs">').appendTo(nav);
		const scroller = body.closest(".main-section")[0] || document.scrollingElement;
		const head = $(page.wrapper).find(".page-head")[0];
		const mobile = window.matchMedia("(max-width: 767px)");
		let frame = 0, destroyed = false;
		const head_height = () => head?.getBoundingClientRect().height || 0;
		const top_offset = () => head_height() + (mobile.matches ? nav.outerHeight() : 0) + 8;
		const navigate = region => {
			const top = region.element[0].getBoundingClientRect().top - scroller.getBoundingClientRect().top - top_offset();
			scroller.scrollBy({top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
		};
		for (const region of regions) {
			const id = `pearnly-intake-${region.key}`;
			region.element.attr({id, "aria-label": region.label});
			region.item = $('<li class="nav-item">').appendTo(links);
			region.link = $('<button type="button" class="nav-link">').attr("aria-controls", id).text(region.label)
				.appendTo(region.item).on("click", event => {event.preventDefault(); navigate(region);});
		}
		const update = () => {
			frame = 0;
			if (destroyed) return;
			body[0].style.setProperty("--pearnly-intake-head-height", `${head_height()}px`);
			nav[0].style.setProperty("--pearnly-intake-head-height", `${head_height()}px`);
			for (const region of regions) region.item.toggle(region.element.is(":visible"));
			if (!mobile.matches) return;
			const threshold = scroller.getBoundingClientRect().top + top_offset() + 8;
			const shown = regions.filter(region => region.element.is(":visible"));
			let active = shown[0];
			for (const region of shown) {
				if (region.element[0].getBoundingClientRect().top <= threshold) active = region;
			}
			// 最后一个分区太短、顶不到判定线时，滚到底就算到了它。
			if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2) active = shown[shown.length - 1];
			for (const region of regions) {
				region.link.toggleClass("active", region === active);
				if (region === active) region.link.attr("aria-current", "location");
				else region.link.removeAttr("aria-current");
			}
		};
		const schedule = () => {if (!frame && !destroyed) frame = requestAnimationFrame(update);};
		scroller.addEventListener("scroll", schedule, {passive: true});
		window.addEventListener("resize", schedule);
		const observer = new ResizeObserver(schedule);
		observer.observe(body[0]);
		if (head) observer.observe(head);
		schedule();
		return {refresh: schedule, destroy: () => {
			destroyed = true;
			cancelAnimationFrame(frame); observer.disconnect();
			scroller.removeEventListener("scroll", schedule);
			window.removeEventListener("resize", schedule);
			nav.remove();
		}};
	};

	p.intake_new = async ({page, t, go, still_current, name}) => {
		let live = true;
		const rows = [];               // 本次进页面传的原件（识别状态就显示在这张表上）
		// 弹窗里选的那四组（`intake-choices.js`）：新建流程拿它当这一批的口径。
		const choice = name ? null : (p.intake_choice || null);
		let current = null;            // 正在复核的那张票（history detail）
		// 这一批识别出来的票（上传顺序；从列表点「复核」进来就只有那一张）与它们的详情缓存。
		let batch = [];
		let settled = Promise.resolve();  // 最近一次把票摆到表单上的赋值（`show_fields`）
		const details = new Map();
		// 预览框把这一批所有票的原件页串成一串：翻到哪张票的页，左边就切到那张票（旧版反向联动）。
		let sheet = [];
		// 左边这张票的编辑底稿：主页面字段的副本，与刚摆上去时的显示值（只写回改过的格子）。
		let working = null, original = null, shown = {}, shown_items = [], shown_direction = "";

		// 二级页面只做上传与复核：从「新建录入」弹窗进来的这一页是「上传」，
		// 从列表点「复核」进来的是「复核」。
		// 以第三方为准（用户 2026-09-26）：MR.ERP 不分服务 / 存货（旧线上 `mode_options`），
		// 这一批是 MR.ERP 就不摆「过账类型」（整票与明细行都不摆）；复核已有记录时不知道目标，照常显示。
		// 「仅识别」（用户 2026-09-26）：识别后导出复核工作簿，不推送；过账类型同样不摆，右上角是「导出」。
		// 从列表 / LINE 卡片打开已有的票：目标卡片记在票上（`intake_target`，新建时写入），没有弹窗里的选择可用。
		const review_text = reason => p.intake_review_text(reason, t);
		const opened = name ? await p.call(HISTORY + "detail", {name}).catch(() => null) : null;
		const target = choice?.target || opened?.intake_target || "";
		const recognize_only = target === p.intake_targets?.RECOGNIZE_ONLY;
		const no_kind = target === "mrerp" || recognize_only ? 1 : 0;
		// 从列表 / LINE 卡片打开已有的票叫「编辑」（用户 2026-09-27：复核改为编辑）。
		const stage = name ? __("Edit") : __("Upload");
		page.set_title(stage);
		p.breadcrumbs({label: t("intake.title"), route: p.subroute("pearnly-intake"),
			trail: [{label: stage, route: ""}]});

		// 版面照 ERP 采购页（`purchase-layout.js`）：DOM 里原件区在前、字段在后 —— 桌面用网格把
		// 原件摆到右栏，手机按顺序纵排（原件在上）；手机上另有一条分区导航（原件 / 识别结果 / 明细）。
		const body = $("<div class='pearnly-intake-body'>").appendTo(page.main);
		const right = $("<section class='pearnly-intake-evidence'>").appendTo(body);
		const left = $("<div class='pearnly-intake-data'>").appendTo(body);
		const result_region = $("<section>").appendTo(left);
		const items_region = $("<section>").appendTo(left);
		const amounts_region = $("<section>").appendTo(left);
		const form = p.layout(result_region, [
			// 一批多张票时（一份多票 / 一页多票）在这里切换，旧版的「发票 i/n」；切走前先把改动存好。
			{fieldtype: "Select", fieldname: "invoice", label: t("intake.field.invoice"), options: [], hidden: 1,
				onchange: () => void pick_invoice(form.get_value("invoice"))},
			// 旧版的漏识别提示（可能漏识别发票），用官方的表单提示条。
			{fieldtype: "HTML", fieldname: "notice"},
			// 单据信息（ERP「详情」第一段：左边单号 / 日期 / 类型，右边业务类型与付款）。
			{fieldtype: "Data", fieldname: "invoice_number", label: t("intake.field.docNo")},
			{fieldtype: "Data", fieldname: "date_raw", label: t("intake.field.printedDate")},
			// 票种按全平台共用码表（ADR-0012）：决定抵不抵进项，复核要能纠正。
			{fieldtype: "Select", fieldname: "document_type", label: __("Document type"),
				options: [{value: "", label: ""}, ...DOCUMENT_TYPES.map(([value, label]) => ({value, label: __(label)}))]},
			{fieldtype: "Column Break"},
			// 进项 / 销项换了，对方就从卖方换成买方（字段跟着换位，不丢已填的值）。
			{fieldtype: "Select", fieldname: "direction", label: t("intake.field.direction"),
				options: [{value: "", label: ""}, {value: "purchase", label: t("intake.dir.purchase")},
					{value: "sales", label: t("intake.dir.sales")}], onchange: () => remap()},
			{fieldtype: "Select", fieldname: "posting_payment_manual", label: __("Payment Method"),
				options: [{value: "", label: ""}, {value: "cash", label: __("Cash")},
					{value: "credit", label: __("Credit")}]},
			// 货品 / 费用（旧版复核屏的人工裁决，只对进项有意义）：空着＝按票面自动判断。
			{fieldtype: "Select", fieldname: "posting_item_type_manual", label: t("intake.field.itemType"),
				options: [{value: "", label: ""}, {value: "goods", label: t("intake.itemType.goods")},
					{value: "expense", label: t("intake.itemType.expense")}], hidden: 1},
			// 过账类型：这一张票自己的（默认＝入口预选 / 记录值），整票服务或整票存货。
			{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"),
				options: KIND_OPTIONS, hidden: no_kind},

			// 对方（ERP 这一段没有标题，名字只在字段标签上说一次）：左边名称 / 税号 / 地址，右边分店。
			{fieldtype: "Section Break"},
			{fieldtype: "Data", fieldname: "party_name", label: t("intake.field.seller")},
			{fieldtype: "Data", fieldname: "party_tax", label: t("intake.field.sellerTax")},
			{fieldtype: "Small Text", fieldname: "party_addr", label: __("Address")},
			{fieldtype: "Column Break"},
			{fieldtype: "Data", fieldname: "party_branch", label: __("Branch No")},
			// 另一方（本方）：推送核对本方税号要用，ERP 页面上没有这一段，放在对方下面。
			{fieldtype: "Section Break"},
			{fieldtype: "Data", fieldname: "other_name", label: t("intake.field.buyer")},
			{fieldtype: "Column Break"},
			{fieldtype: "Data", fieldname: "other_tax", label: t("intake.field.buyerTax")},
			// 税费金额与备注：排在明细下面（ERP 同位置），渲染后整段移到 `amounts_region`。
			{fieldtype: "Section Break", label: __("Taxes and Charges"), fieldname: "amounts_section"},
			{fieldtype: "Data", fieldname: "discount", label: __("Discount Amount")},
			{fieldtype: "Data", fieldname: "currency", label: __("Currency")},
			{fieldtype: "Column Break"},
			{fieldtype: "Data", fieldname: "subtotal", label: t("intake.field.netAmount")},
			{fieldtype: "Data", fieldname: "vat", label: t("intake.field.vatAmount")},
			{fieldtype: "Data", fieldname: "wht_amount", label: t("intake.field.whtAmount")},
			{fieldtype: "Data", fieldname: "total_amount", label: t("intake.col.grandTotal")},
			{fieldtype: "Section Break", fieldname: "notes_section"},
			{fieldtype: "Small Text", fieldname: "notes", label: __("Remarks")},
		]);
		// 明细照 ERP 采购页的物料行：每行一个可折叠的原生小节（第一行展开），手机上不挤表格。
		// 明细能增删行（2026-09-28 压测：漏读的行补不上，明细合计对不上的票只能退回）。
		const items_form = p.layout(items_region, [
			{fieldtype: "Section Break", label: t("intake.nav.items"), fieldname: "items_section"},
			{fieldtype: "HTML", fieldname: "lines"},
			{fieldtype: "Button", fieldname: "add_line", label: __("Add Row"), click: () => {
				lines.push(add_line({name: "", qty: "", price: "", subtotal: "", posting_kind: ""}, lines.length, null));
				sync_save();
			}},
		]);
		// 税费金额与备注放在明细下面（ERP `purchase-layout.js` 同法：定义在一张表单里，按分段挪到各自区域）。
		let moving = false;
		for (const section of form.sections) {
			if (section.df.fieldname === "amounts_section") moving = true;
			if (moving) section.wrapper.appendTo(amounts_region);
		}
		// 分区导航的名称照 ERP：附件 / 详情 / 明细。
		const nav = section_nav(page, body, [
			{key: "original", label: __("Attachments"), element: right},
			{key: "result", label: __("Details"), element: result_region},
			{key: "items", label: __("Items"), element: items_region},
		]);
		// 文件选择口：一个隐藏的原生 input（用 DOM 建，不写标签字符串 —— Desk 页面脚本不许手写
		// 通用控件标签，架构检查会拦），由紧挨着的那颗官方按钮（下面的 `pick`）点开。
		const file_input = $(document.createElement("input"))
			// 推到 ERP 的两张卡片还收我们导出的复核工作簿（.xlsx）：服务端认出往返表就按表解析，不走识别模型。
			.attr({type: "file", multiple: "multiple",
				accept: recognize_only ? "application/pdf,image/*" : "application/pdf,image/*,.xlsx"})
			.addClass("pearnly-upload-input");
		const uploader = p.layout(right, [
			{fieldtype: "Button", fieldname: "pick", label: __("Upload"),
				click: () => file_input.trigger("click")},
			{fieldtype: "HTML", fieldname: "picker"},
			// 原件预览框：共用层那一份（`pearnly_core/public/desk/viewer.js`，ERP 采购/销售
			// 用的是同一个），这里只交代页图从哪来。
			{fieldtype: "HTML", fieldname: "preview"},
			{fieldtype: "HTML", fieldname: "batch"},
		]);
		file_input.appendTo(uploader.fields_dict.picker.$wrapper);
		const preview = p.document_viewer(uploader.fields_dict.preview.$wrapper, {
			load: (row, number) => {
				const entry = row.sheet[number - 1];
				return {image: page_url(entry.record, entry.physical), pages: row.sheet.length};
			},
			// 翻页＝看原件；翻到另一张票的页，左边跟着切到那张票（旧版 `dms-intake-review.ts` 同一联动）。
			on_page: (row, number) => {
				const entry = row?.sheet?.[number - 1];
				if (entry && current && entry.record !== current.name) void open_record(entry.record, {from_viewer: true});
			},
		});

		// 没读到记录之前没有可复核的东西：字段一律只读（不给人改空气）。
		let reviewable = false, lines = [];
		const set_reviewable = editable => {
			reviewable = editable;
			for (const field of EDITABLE) form.set_df_property(field, "read_only", !editable);
			for (const line of lines) {
				for (const column of ITEM_COLUMNS) line.form.set_df_property(column, "read_only", !editable);
				line.form.set_df_property("remove", "hidden", !editable);
			}
			items_form.set_df_property("add_line", "hidden", !editable);
			items_region.toggle(Boolean(lines.length) || editable);
		};
		set_reviewable(false);
		items_region.hide();
		// 方向和付款方式在弹窗里已经选过，页面上仍然可改：复核要能纠正识别结果。
		for (const field of ["direction", "posting_payment_manual", "posting_item_type_manual"]) {
			form.set_df_property(field, "read_only", 0);
		}
		// 弹窗选的这一批口径先落在原生字段上（不另画一行提示），识别回填时再按记录对齐。
		if (choice) {
			// 目标 ERP 与账套不在页面上显示（用户 2026-09-26 / 09-27），推送按上传前的选择走。
			shown_direction = choice.direction || "";
			form.set_values({direction: choice.direction,
				posting_payment_manual: choice.payment, posting_kind: choice.posting_kind || ""});
		}

		const render_batch = () => {
			// 手上没有原件就不留一张空表：右侧那块地方是预览框的。
			uploader.fields_dict.batch.$wrapper.toggle(Boolean(rows.length));
			if (!rows.length) return;
			void p.table(uploader.fields_dict.batch.$wrapper, [__("Original Filename"),
				__("Original Size"), t("intake.col.status"), __("Note")], rows.map(row => [
				row.name,
				size_text(row.size),
				__(STATES[row.state] || row.state),
				row.message || "",
			]));
		};

		/* ── 复核：一张票一张票地看，字段写回这张票的主页面 ─────────────────────────
		 * 旧版一份文件里的几张票竖着排在同一块里、点哪张原件就跳到它的第一页；这里用原生
		 * 表单一次摆一张，上面的「发票」下拉与右边翻页都能换票。没有前端缓存层，所以换票前
		 * 先把改动存好（旧版是编辑留在内存、最后一步统一存）。 */
		/** 一行物料＝一个可折叠小节（ERP 采购页 `state.add_line` 同一组合：外层小节 + 里层字段组）。 */
		const add_line = (row, index, source = index) => {
			const wrapper = $("<div>").appendTo(items_form.fields_dict.lines.$wrapper);
			const outer = p.layout(wrapper, [
				{fieldtype: "Section Break", label: `${__("Item")} ${index + 1}`, collapsible: 1},
				{fieldtype: "HTML", fieldname: "item"},
			]);
			// 与 ERP 相同的原生间距类：手机上嵌套小节不叠三层边距，控件几何仍归框架。
			outer.sections[0].wrapper.addClass("px-0");
			outer.sections[0].columns.forEach(column => column.wrapper.addClass("px-0"));
			const line_form = p.layout(outer.fields_dict.item.$wrapper, [
				{fieldtype: "Data", fieldname: "name", label: t("intake.item.name"), read_only: !reviewable},
				{fieldtype: "Data", fieldname: "qty", label: __("Quantity"), read_only: !reviewable},
				{fieldtype: "Column Break"},
				{fieldtype: "Data", fieldname: "price", label: __("Unit Price"), read_only: !reviewable},
				{fieldtype: "Data", fieldname: "subtotal", label: __("Amount"), read_only: !reviewable},
				{fieldtype: "Select", fieldname: "posting_kind", label: __("Posting Kind"), options: KIND_OPTIONS,
					hidden: no_kind, read_only: !reviewable},
				{fieldtype: "Button", fieldname: "remove", label: __("Delete"), hidden: !reviewable, click: () => {
					line.removed = true;
					wrapper.hide();
					sync_save();
				}},
			]);
			line_form.sections.forEach(section => section.wrapper.addClass("px-0"));
			line_form.sections.forEach(section => section.columns.forEach(column => column.wrapper.addClass("px-0 px-sm-3")));
			line_form.sections[0].body.addClass("mt-0 pt-0");
			line_form.set_values(row);
			outer.sections[0].collapse(index > 0 && source !== null);
			const line = {wrapper, form: line_form, source, removed: false};
			return line;
		};
		const show_items = items => {
			const list = Array.isArray(items) ? items : [];
			shown_items = list.map(row => ({name: row?.name ?? "", qty: row?.qty ?? "", price: row?.price ?? "",
				// 旧版：金额没有 `subtotal` 时显示 `amount`，改过就写回 `subtotal`。
				subtotal: row?.subtotal ?? row?.amount ?? "", posting_kind: row?.posting_kind || ""}));
			items_form.fields_dict.lines.$wrapper.empty();
			lines = shown_items.map((row, index) => add_line({...row}, index));
			items_region.toggle(Boolean(lines.length) || reviewable);
			nav.refresh();
		};
		/** 把表单上**改过的**格子写进 `target`（没动的不写：显示用的回落值不能倒灌进记录）。 */
		const collect_into = (target, direction) => {
			const keys = FIELD_KEYS(direction);
			for (const field of EDITABLE) {
				const value = form.get_value(field) ?? "";
				if (String(value) === String(shown[field] ?? "")) continue;
				if (value === "") delete target[keys[field]];
				else target[keys[field]] = value;
			}
			// 明细：原有的行只写改过的格子；新加的行写填了的格子；删掉的行去掉。一行都没动就不碰 `items`。
			const original_items = Array.isArray(target.items) ? target.items : [];
			const merge = (item, line, shown_row) => {
				const next = {...item};
				for (const column of ITEM_COLUMNS) {
					const value = line.form.get_value(column) ?? "";
					if (String(value) === String(shown_row[column] ?? "")) continue;
					if (value === "" && column === "posting_kind") delete next[column];
					else next[column] = value;
				}
				return next;
			};
			const kept = lines.filter(line => !line.removed);
			const untouched = kept.length === original_items.length && kept.every((line, index) => line.source === index);
			if (untouched && (original_items.length || Array.isArray(target.items))) {
				target.items = original_items.map((item, index) => merge(item, kept[index], shown_items[index] || {}));
			} else if (!untouched) {
				target.items = kept.map(line => (line.source === null ? merge({}, line, {})
					: merge(original_items[line.source] || {}, line, shown_items[line.source] || {})))
					.filter(item => Object.keys(item).length);
			}
			return target;
		};
		const dirty = () => Boolean(working)
			&& JSON.stringify(collect_into(clone(working), shown_direction)) !== JSON.stringify(original);
		/** 空着要提醒的格子用官方的必填样式标出来（旧版标黄）；只提醒，不拦保存。 */
		const warn = (fields, direction) => {
			const exempt = direction === "sales" && ANONYMOUS_BUYER.has(fields.document_type);
			for (const field of ["invoice_number", "total_amount", "party_tax"]) {
				form.set_df_property(field, "reqd", field === "party_tax" && exempt ? 0 : 1);
			}
		};
		/** 按进项 / 销项摆字段：对方是卖方还是买方，标签与取值都跟着换。 */
		const show_fields = (fields, direction) => {
			const {party, other} = PARTY(direction);
			form.set_df_property("party_name", "label", t(`intake.field.${party}`));
			form.set_df_property("party_tax", "label", t(`intake.field.${party}Tax`));
			form.set_df_property("other_name", "label", t(`intake.field.${other}`));
			form.set_df_property("other_tax", "label", t(`intake.field.${other}Tax`));
			const keys = FIELD_KEYS(direction);
			const values = {};
			for (const field of EDITABLE) values[field] = fields[keys[field]] ?? "";
			// 票面日期没有时显示公历日期（旧版同）；识别没给出方向 / 付款 / 过账类型时退回弹窗那一组。
			values.date_raw = fields.date_raw || fields.date || current?.invoice_date || "";
			values.direction = direction;
			values.posting_payment_manual = fields.posting_payment_manual || choice?.payment || "";
			values.posting_kind = fields.posting_kind || current?.posting_kind || choice?.posting_kind || "";
			values.posting_item_type_manual = fields.posting_item_type_manual || "";
			form.set_df_property("posting_item_type_manual", "hidden", direction === "purchase" ? 0 : 1);
			shown = {...values};
			shown_direction = direction;  // 先记下，下面赋值触发的 onchange 就不会当成用户换了方向
			settled = Promise.resolve(form.set_values(values));
			warn(fields, direction);
		};
		/** 用户在复核页换了进项 / 销项：先把已填的按旧位置收进底稿，再按新位置摆出来。 */
		const remap = () => {
			const next = form.get_value("direction") || "";
			if (!working || next === shown_direction) return;
			collect_into(working, shown_direction);
			shown_items = [];
			show_fields({...working, direction: next}, next);
			show_items(working.items);
		};
		/** 需复核原因（服务端 `history/review.py` 算好存在票上）：一条一行，官方表单提示条。
		 * 单号两种读法、税号补 0 的建议点一下就填进去；识别时留下的提醒看过可以点掉（记进票面，保存后不再提）。 */
		const show_notice = record => {
			const wrapper = form.fields_dict.notice.$wrapper.empty();
			const reasons = Array.isArray(record?.review_reasons) ? record.review_reasons : [];
			const lines = reasons.length ? reasons : maybe_missed(record) ? [{code: "missed_invoice"}] : [];
			const keys = FIELD_KEYS(shown_direction);
			const control = field => Object.keys(keys).find(name => keys[name] === field);
			const resolve = (row, code) => {
				working[REVIEW_RESOLVED] = [...new Set([...(working[REVIEW_RESOLVED] || []), code])];
				row.remove();
				sync_save();
			};
			const pushed = record?.last_push_status;
			if (record?.last_push_reason && (pushed === "manual" || !pushed)) {
				const row = $("<div class='form-message red'>").appendTo(wrapper);
				$("<span>").text(p.intake_push_reason(record.last_push_reason)).appendTo(row);
				if (RESULT_UNKNOWN.test(String(record.last_push_reason)) && record.last_push_record) {
					$("<button type='button' class='btn btn-xs btn-default ml-2'>").text(__("cowork.unknown.check"))
						.on("click", () => void resolve_unknown(record)).appendTo(row);
				}
				if (/^choice_needed/.test(String(record.last_push_reason)) && !recognize_only) {
					$("<button type='button' class='btn btn-xs btn-default ml-2'>").text(__("cowork.choice.title"))
						.on("click", () => void submit({names: [record.name]})).appendTo(row);
				}
				if (SEND_ANYWAY.test(String(record.last_push_reason)) && !recognize_only) {
					$("<button type='button' class='btn btn-xs btn-default ml-2'>").text(__("cowork.review.send_anyway"))
						.on("click", () => void submit({names: [record.name], extra: {confirm_duplicate: 1}}))
						.appendTo(row);
				}
			}
			for (const reason of lines) {
				const row = $("<div class='form-message yellow'>").appendTo(wrapper);
				$("<span>").text(review_text(reason)).appendTo(row);
				const offer = (value, apply) => $("<button type='button' class='btn btn-xs btn-default ml-2'>")
					.text(value).on("click", apply).appendTo(row);
				if (reason.code === "number_choice") {
					for (const value of reason.choices || []) {
						offer(value, () => {form.set_value("invoice_number", value); resolve(row, reason.code);});
					}
				}
				if (reason.code === "tax_length" && reason.suggest && control(reason.field)) {
					offer(reason.suggest, () => {form.set_value(control(reason.field), reason.suggest); row.remove();});
				}
				if (REVIEW_DISMISSABLE.has(reason.code)) {
					$("<button type='button' class='close ml-2' aria-label='Close'>").html("&times;")
						.on("click", () => resolve(row, reason.code)).appendTo(row);
				}
			}
		};
		// 推到 ERP 的两张目标卡片（用户 2026-09-27：账套上传前由用户选定，编辑页不再选）。
		const PUSH_TARGETS = ["express", "mrerp"];

		/** 把一张票摆到左边（识别回填、换票、保存后重读都走这里）。 */
		const fill = record => {
			const pages = Array.isArray(record.pages) ? record.pages : [];
			const index = primary_index(pages);
			working = clone(index >= 0 ? pages[index]?.fields || {} : {});
			original = clone(working);
			const direction = working.direction || choice?.direction || "";
			show_items(working.items);
			show_fields(working, direction);
			show_notice(record);
			set_reviewable(index >= 0);
		};
		const detail = async record_name => {
			const record = await p.call(HISTORY + "detail", {name: record_name});
			details.set(record.name, record);
			return record;
		};
		const invoice_options = () => batch.map((record_name, index) => {
			const record = details.get(record_name) || {};
			return {value: record_name, label: [t("intake.review.invoice", index + 1, batch.length),
				record.invoice_no, record.seller_name].filter(Boolean).join(" · ")};
		});
		/** 这一批的票：读详情、把原件页串起来、摆出「发票」下拉（一张时不摆，旧版同）。 */
		const set_batch = async names => {
			batch = [...new Set(names)];
			const queue = batch.filter(record_name => !details.has(record_name));
			const worker = async () => {while (queue.length && live) await detail(queue.shift());};
			await Promise.all(Array.from({length: Math.min(PARALLEL, queue.length)}, worker));
			sheet = batch.flatMap(record_name => own_pages(details.get(record_name))
				.map(physical => ({record: record_name, physical})));
			form.set_df_property("invoice", "options", invoice_options());
			form.set_df_property("invoice", "hidden", batch.length > 1 ? 0 : 1);
		};
		const sheet_row = () => (sheet.length ? {file: `batch:${batch.join(",")}`, sheet,
			file_name: current?.filename || ""} : null);
		/** 换到某一张票：先存当前这张的改动，再摆新的；从下拉换的还要把原件翻到它的第一页。 */
		const open_record = async (record_name, {from_viewer = false} = {}) => {
			if (current && current.name !== record_name && dirty()) await save_review({quiet: true});
			current = details.get(record_name) || await detail(record_name);
			fill(current);
			if (form.get_value("invoice") !== record_name) form.set_value("invoice", record_name);
			// 右上角：提交＝把这一批推送出去（见 `submit`），仅识别的这一批是导出；保存只在改过时出现。
			sync_save();
			if (recognize_only) page.set_primary_action(__("Export"), () => void export_batch());
			else page.set_primary_action(__("Submit"), () => void submit());
			if (from_viewer) return;
			const first = sheet.findIndex(entry => entry.record === record_name);
			await preview.show(first >= 0 ? sheet_row() : null, first + 1);
		};
		const pick_invoice = record_name => {
			if (record_name && record_name !== current?.name) void open_record(record_name);
		};

		/** 「保存」只在这张票有没存的改动时出现（用户 2026-09-26）。回填是异步的：等值都落进控件再比。 */
		const sync_save = () => void settled.then(() => {
			if (current && dirty()) page.set_secondary_action(__("Save"), () => void save_review());
			else page.clear_secondary_action();
		});
		// 字段改动（输入、下拉选中、明细行）都冒泡到页面：每次改完重算一次。
		$(page.body).on("input change awesomplete-selectcomplete", ".frappe-control", () => setTimeout(sync_save));

		/** 复核保存：把改过的格子写回**这张票的主页面**（服务端只认它），按记录版本防覆盖。 */
		const save_review = async ({quiet = false} = {}) => {
			if (!current || !working) return;
			const pages = clone(current.pages || []);
			const index = primary_index(pages);
			if (index < 0) return;
			pages[index] = {...pages[index], fields: collect_into(clone(working), shown_direction)};
			const saved = await p.call(HISTORY + "save",
				{name: current.name, pages, expected_version: current.record_version}, "POST");
			details.set(saved.name, saved);
			form.set_df_property("invoice", "options", invoice_options());
			if (current.name !== saved.name) return;
			current = saved;
			if (quiet) return sync_save();
			p.success(__("Saved"));
			fill(current);
			sync_save();
		};

		/** 取一份复核工作簿（`roundtrip.api.export` 只收 POST）：成功给文件，失败给服务端原因。 */
		const request_workbook = async names => {
			const response = await fetch(`/api/method/${ROUNDTRIP}export`, {
				method: "POST", credentials: "same-origin",
				headers: {"X-Frappe-CSRF-Token": frappe.csrf_token},
				body: new URLSearchParams({names: JSON.stringify(names)}),
			});
			if (!response.ok) return {error: failure(await response.json().catch(() => null), response.status)};
			const disposition = response.headers.get("Content-Disposition") || "";
			return {blob: await response.blob(),
				file_name: disposition.match(/filename="?([^";]+)"?/)?.[1] || "cowork-review.xlsx"};
		};
		const save_file = ({blob, file_name}) => {
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url; link.download = file_name;
			document.body.appendChild(link); link.click(); link.remove();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		};
		/** 仅识别：把这一批识别出来的票导出（一次最多 100 张，多了分成几份下载）；先存好当前这张的改动。
		 *  工作簿要求每张票都能原样回导（缺金额等会被拒），一张不合格整份就出不来：这时逐张找出是哪几张，
		 *  其余照常导出，不合格的按「单号：原因」列出来（原因是服务端的原话）。 */
		const export_batch = async () => {
			if (!batch.length) return;
			if (dirty()) await save_review({quiet: true});
			const rejected = [];
			for (let index = 0; index < batch.length; index += 100) {
				const names = batch.slice(index, index + 100);
				let result = await request_workbook(names);
				if (result.error && names.length > 1) {
					const good = [];
					for (const record_name of names) {
						const single = await request_workbook([record_name]);
						if (single.error) rejected.push([record_name, single.error]);
						else good.push(record_name);
					}
					result = good.length ? await request_workbook(good) : null;
				} else if (result.error) {
					rejected.push([names[0], result.error]);
					result = null;
				}
				if (result?.blob) save_file(result);
			}
			if (rejected.length) {
				frappe.msgprint({title: __("Export"), indicator: "orange", message: rejected.map(([record_name, reason]) =>
					`${p.escape(details.get(record_name)?.invoice_no || record_name)}：${p.escape(reason)}`).join("<br>")});
			}
		};

		/** 推到第一次选的目标（用户 2026-09-26）：票上记的连接 / 账套（新建时写入）优先，当场这一批用弹窗里的；
		 *  老票没记过目标、站点只有一条连接时用它（按连接默认账套）。所选账套由服务端按当前目录核对，
		 *  不再为推送现扫一次（用户 2026-09-27）。 */
		const resolve_target = (record, rows) => {
			const name = record.push_connection || choice?.connection || (rows.length === 1 ? rows[0].name : "");
			const connection = rows.find(row => row.name === name) || null;
			const account = record.push_account_set || (choice?.connection === name ? choice?.account_set_key : "") || "";
			return {connection, args: connection && account ? {account_set_key: account} : {}};
		};

		/** 幂等键＝这件事本身的指纹：同一份内容、同一目标重复提交是同一件事（服务端回原结果），
		 *  改过复核或换了目标就是新的一件事。没有安全上下文的哈希时退回随机键（只失去重放合并）。 */
		const stable_key = async (...parts) => {
			if (!crypto?.subtle) return uuid();
			const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(parts)));
			return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
		};
		const push_call = async (method, args) => {
			try {
				return {ok: true, message: (await frappe.call({method, args, type: "POST", silent: true})).message};
			} catch (error) {
				return {ok: false, reason: failure(error, error?.status || "")};
			}
		};

		/** 推存货进 Express 前，这份账套的存货科目组得定下来（每个账套选一次，用户 2026-09-28）：新建录入时
		 *  小助手还没读过这份账套、或 LINE 上组太多没法点选的，在这里用原生弹窗选一次。关掉弹窗 = 这份账套的票
		 *  这次先不推。同一次提交里每份账套只问一次。 */
		const stock_group_ready = async (target, asked) => {
			const account = target.args.account_set_key;
			const key = `${target.connection.name}\n${account}`;
			if (!asked.has(key)) {
				asked.set(key, (async () => {
					const view = await p.intake_targets.stock_group(target.connection, account);
					if (!p.intake_targets.stock_group_needed(view)) return true;
					frappe.dom.unfreeze();
					try {
						return await p.intake_targets.ask_stock_group(target.connection, account, view);
					} finally {
						frappe.dom.freeze(t("intake.action.queue"));
					}
				})());
			}
			return asked.get(key);
		};

		/** 推 MR.ERP 前，这份账套这张票会用到的导入菜单得定下来（每个账套每类单据选一次，用户 2026-09-28）。
		 *  同一次提交里每份账套、每个方向只问一次；关掉弹窗 = 这份账套的票这次先不推。 */
		const mrerp_menu_ready = async (target, direction, asked) => {
			const key = `${target.connection.name}\n${target.args.account_set_key}\n${direction || ""}`;
			if (!asked.has(key)) {
				asked.set(key, (async () => {
					frappe.dom.unfreeze();
					try {
						return await p.intake_targets.mrerp_menu_ready(target.connection, target.args.account_set_key, direction);
					} finally {
						frappe.dom.freeze(t("intake.action.queue"));
					}
				})());
			}
			return asked.get(key);
		};

		/** 结果不明：会计在 ERP 里核对后标「已入账」（可填 ERP 单号）或「未入账」（之后可重推）。 */
		const resolve_unknown = record => new Promise(resolve => {
			const d = p.dialog(__("cowork.unknown.check"), [
				{fieldtype: "Select", fieldname: "outcome", label: __("cowork.unknown.outcome"), reqd: 1,
					options: [{value: "entered", label: __("cowork.unknown.entered")},
						{value: "not_entered", label: __("cowork.unknown.not_entered")}]},
				{fieldtype: "Data", fieldname: "docnum", label: __("cowork.unknown.docnum"),
					depends_on: "eval:doc.outcome=='entered'"},
			], __("Save"), async values => {
				await p.call(PUSH + "resolve_unknown", {name: record.last_push_record,
					entered: values.outcome === "entered" ? 1 : 0, docnum: values.docnum || ""}, "POST");
				d.hide();
				current = await detail(record.name);
				fill(current);
				sync_save();
				resolve(true);
			});
			d.onhide = () => resolve(false);
		});

		/** 推 Express 前，这张票在这份账套里要人选一次的（对方档 / 存货 / 费用科目 / 付款 / 抵进项 / 部门）先问齐。 */
		const express_choices_ready = async (target, record, posting_kind) => {
			frappe.dom.unfreeze();
			try {
				return await p.intake_targets.express_choices_ready(target.connection, target.args.account_set_key,
					record.name, posting_kind);
			} finally {
				frappe.dom.freeze(t("intake.action.queue"));
			}
		};

		/** 提交＝推送（用户 2026-09-26）：右上角点一次，把这一批的票按顺序全部推出去（一张推完再下一张），
		 *  没有「确认」这一步。推完回列表，状态在列表上看（录入中 / 成功 / 待人工核对）；推不了的原因由服务端
		 *  记在票上，列表状态图标悬浮可看，这里不弹窗。已经推过或正在推的跳过。 */
		const submit = async ({names = batch, extra = {}} = {}) => {
			if (!names.length) return;
			if (dirty()) await save_review({quiet: true});
			const rows = (await p.call(CONNECTIONS + "records", {limit: 100})) || [];
			const broken = [];  // 只有请求本身出错（网络 / 服务器异常）才会到这里；业务失败都记在票上
			const asked = new Map();  // 连接 + 账套 → 存货科目组是否已定
			frappe.dom.freeze(t("intake.action.queue"));
			try {
				for (const record_name of names) {
					const record = await detail(record_name);  // 最新版本（刚保存过的也要按新版本推）
					if (["success", "pending"].includes(record.last_push_status)) continue;
					const pages = Array.isArray(record.pages) ? record.pages : [];
					const fields = pages[primary_index(pages)]?.fields || {};
					const target = resolve_target(record, rows);
					// 推到 ERP 的票没记账套（上传前没选定的老票）：不按连接默认账套猜，拦下说明。
					if (PUSH_TARGETS.includes(record.intake_target) && !target.args.account_set_key) {
						broken.push([record, __("Choose the year and account set first.")]);
						continue;
					}
					const intent = {history: record.name, connection: target.connection?.name || "",
						expected_history_version: record.record_version,
						expected_connection_revision: target.connection?.revision ?? 0, ...target.args,
						// 推的是这一张票自己的过账类型（复核改过就用改过的），没定就不猜。
						posting_kind: fields.posting_kind || record.posting_kind || choice?.posting_kind || undefined};
					const stock = [intent.posting_kind, ...(Array.isArray(fields.items) ? fields.items : [])
						.map(item => item?.posting_kind)].includes("stock");
					if (target.connection?.adapter === "express" && target.args.account_set_key && stock
						&& !(await stock_group_ready(target, asked))) continue;
					if (target.connection?.adapter === "express" && target.args.account_set_key
						&& !(await express_choices_ready(target, record, intent.posting_kind))) continue;
					if (target.connection?.adapter === "mrerp" && target.args.account_set_key
						&& !(await mrerp_menu_ready(target, fields.direction, asked))) continue;
					// 键里带上一次推送记录：失败后再点提交是新的一次（会重推），连点两下仍是同一次。
					const submitted = await push_call(PUSH + "submit", {...intent, ...extra,
						request_key: await stable_key("prepare", {...intent, ...extra}, record.last_push_record || "")});
					if (!submitted.ok) {
						broken.push([record, submitted.reason]);
						continue;
					}
					const result = submitted.message || {};
					// 服务端没新建推送、回了已经成功的那条（同一张票别处已推成功）：告诉人，不再静默没反应。
					// 退回的原因照旧只记在票上（列表状态、复核页顶部），不弹窗（用户 2026-09-26）。
					if (result.status === "success" && result.name !== record.last_push_record) {
						broken.push([record, p.intake_push_reason("already_success")]);
					}
					if (result.status === "prepared" && target.connection?.adapter === "express") {
						const confirmed = await push_call(EXPRESS_CONFIRM, {name: result.name, expected_revision: result.revision,
							request_key: await stable_key("confirm", result.name, result.revision)});
						if (!confirmed.ok) broken.push([record, confirmed.reason]);
					}
				}
			} finally {
				frappe.dom.unfreeze();
			}
			await go();
			if (broken.length) {
				frappe.msgprint({title: t("intake.action.queue"), indicator: "red", message: broken.map(([record, reason]) =>
					`${p.escape(record.invoice_no || record.name)}：${p.escape(reason)}`).join("<br>")});
			}
		};

		/* ── 识别进度：白底 + 官方转圈（`p.waiting`，用户 2026-09-30「白屏加动画」，原来是整屏灰遮罩）。
		 * 按**页**算（用户 2026-09-30「按页分进度，不要按份」）：总页数是这一批上传后服务端数出的页数之和，
		 * 已识别页数是每份识别任务回报的真实页数（`pages_done`：引擎按页顺序读，读到第 N 页前 N−1 页已读完），
		 * 有结论的份按全部页数算。 */
		const progress = {rows: [], frozen: null};
		const row_pages = row => Math.max(1, Number(row.pages) || 1);
		const progress_lines = () => {
			const total = progress.rows.reduce((sum, row) => sum + row_pages(row), 0);
			const done = progress.rows.reduce((sum, row) => sum + (row.finished ? row_pages(row)
				: Math.min(row_pages(row), Number(row.pages_done) || 0)), 0);
			const percent = total ? Math.round((done / total) * 100) : 0;
			return [t("intake.proc.title"), t("intake.proc.sub"), __("shell.wait.pages", [done, total, percent])];
		};
		const paint_progress = () => {
			if (!live) return;
			if (!progress.frozen) progress.frozen = p.waiting(page, "");
			progress.frozen.update(progress_lines().join("\n"));
		};
		const end_progress = () => {
			progress.frozen?.done();
			progress.frozen = null;
		};

		/** 等一个识别批次出结论；读状态失败不弹窗（静默重试），连续读不到才算失联。 */
		const wait_batch = async (name, on_progress = null) => {
			const started = Date.now();
			let misses = 0;
			while (live) {
				let batch = null;
				try {
					batch = (await frappe.call({method: OCR + "detail", args: {name}, type: "GET", silent: true})).message;
					misses = 0;
				} catch (error) {
					if (++misses >= POLL.soft_fails) return {state: "failed", last_code: "poll_unreachable"};
				}
				if (batch && (FINISHED.has(batch.state) || BROKEN.has(batch.state))) return batch;
				if (batch && on_progress) on_progress(batch);
				if (Date.now() - started > POLL.max_ms) return {state: "failed", last_code: "timeout"};
				await sleep(POLL.every_ms);
			}
			return null;
		};

		const recognize = async row => {
			row.state = "submitting"; render_batch();
			const params = choice
				// 没选的就不传（空串在合同里是「非法值」，不是「没选」）。
				? {direction: choice.direction, posting_kind: choice.posting_kind || undefined,
					payment: choice.payment || undefined,
					// 选的是哪张目标卡片也记上：之后打开复核页才知道是「提交」还是「导出」。
					target: choice.target || undefined,
					// 第一次选的推送目标记在票上，之后从列表进来提交也按它推（用户 2026-09-26）。
					...(choice.connection && !recognize_only
						? {connection: choice.connection, account_set_key: choice.account_set_key || undefined} : {})}
				: {direction: form.get_value("direction") || undefined,
					posting_kind: current?.posting_kind || undefined,
					payment: form.get_value("posting_payment_manual") || undefined};
			let batch = null;
			try {
				const result = await p.call(OCR + "submit", {
					source_file: row.source,
					// 同一份原件重试识别沿用同一个键：服务端认得出"这不是新任务"。
					request_key: `${row.key}-ocr`,
					...params,
				}, "POST");
				if (!result?.accepted) {
					row.state = "failed";
					row.message = code_text(result?.code);
					return;
				}
				row.state = "recognizing"; render_batch();
				if (result.page_count) row.pages = result.page_count;
				batch = FINISHED.has(result.state) || BROKEN.has(result.state) ? result : await wait_batch(result.name,
					latest => {
						if ((Number(latest.pages_done) || 0) > (Number(row.pages_done) || 0)) {
							row.pages_done = latest.pages_done;
							paint_progress();
						}
					});
			} catch (error) {
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
				row.state = "failed";
				row.message = failure(error, error?.status || "");
				return;
			}
			if (!batch) return;  // 页面已切走
			row.records = batch.history_records || [];
			row.state = batch.state === "saved" ? "recognized" : batch.state === "not_invoice" ? "not_invoice" : "failed";
			// 结果不明＝中途断了、服务端不会自己重跑（不重复付费调用），也没扣费：直说重新上传，不叫人「稍后刷新」
			// （2026-09-28 复测：识别服务连接中断，15 页 PDF 整份停在这里）。
			row.message = batch.state === "uncertain" ? __("cowork.intake.interrupted") : code_text(batch.last_code);
		};

		const send = async row => {
			row.state = "uploading"; render_batch();
			try {
				// 不传归属：服务端按站点解析（见 `files.api.upload`）。
				const query = new URLSearchParams({request_key: row.key});
				const body = new FormData();
				body.append("file", row.file);
				const response = await fetch(`/api/method/${FILES}upload?${query}`, {
					method: "POST", body, credentials: "same-origin",
					headers: {"X-Frappe-CSRF-Token": frappe.csrf_token},
				});
				const payload = await response.json().catch(() => null);
				if (!response.ok || !payload?.message) {
					row.state = "failed"; row.message = failure(payload, response.status);
				} else {
					row.source = payload.message.name;
					row.pages = payload.message.page_count;
					row.state = "uploaded";
				}
			} catch (error) {
				row.state = "failed";
				row.message = String(error?.message || error);
			}
			if (!live) return;
			render_batch();
		};

		/** 一批原件：先全部上传（最多 6 份同时，上传后才知道每份几页），再最多 6 份同时识别；
		 * 等候页上走「已识别 N/总 页 · 百分比」，全部有结论才收起。 */
		const run_batch = async waiting => {
			if (!waiting.length) return;
			progress.rows = waiting;
			paint_progress();
			const pool = async (items, job) => {
				const queue = items.slice();
				const worker = async () => {
					while (queue.length && live) await job(queue.shift());
				};
				await Promise.all(Array.from({length: Math.min(PARALLEL, queue.length)}, worker));
			};
			try {
				await pool(waiting, async row => {
					await send(row);
					if (row.state !== "uploaded") row.finished = true;
					paint_progress();
				});
				await pool(waiting.filter(row => row.state === "uploaded"), async row => {
					await recognize(row);
					row.finished = true;
					paint_progress();
				});
			} finally {
				end_progress();
			}
			if (!live) return;
			render_batch();
			// 收尾提示照旧线上：一张都没识别出来就说「识别结果为空」；有失败就报成功/失败各几份。
			const records = waiting.flatMap(row => row.records || []);
			const failed = waiting.filter(row => row.state === "failed").length;
			if (!records.length) {
				frappe.msgprint({title: t("intake.title"), indicator: "red", message: p.escape(t("intake.proc.empty"))});
				return;
			}
			if (failed) frappe.show_alert({message: p.escape(t("intake.proc.batchFail", records.length, failed)),
				indicator: "orange"});
			// 这一批的票都摆进「发票」下拉；旧版识别完提示可能重复的张数（只提醒，不拦）。
			await set_batch(records);
			const duplicates = records.filter(record_name => details.get(record_name)?.duplicate_warning).length;
			if (duplicates) frappe.show_alert({message: p.escape(t("intake.review.dupWarn", duplicates)), indicator: "orange"});
			// 识别出来的第一张直接回填到左边，接着就能复核。
			await open_record(records[0]);
		};

		file_input.on("change", async () => {
			const chosen = Array.from(file_input[0].files || []);
			file_input.val("");
			// 按选文件的顺序识别与编号（「发票 i/n」跟着这个顺序）；表格里新的一批摆在最上面。
			const added = chosen.map(file => ({file, name: file.name, size: file.size, key: uuid(),
				state: "new", message: "", source: "", records: []}));
			rows.unshift(...added);
			render_batch();
			await run_batch(added);
		});

		p.add_refresh(page, async () => {
			if (!current) return render_batch();
			// 刷新＝丢掉没存的改动、按服务端最新版本重摆这张票。
			current = await detail(current.name);
			fill(current);
			sync_save();
		});
		render_batch();
		if (name) {
			// 从列表点「复核」进来：这份原件识别出了几张票，就把同一份的都摆进「发票」下拉。
			const record = await detail(name);
			const siblings = record.source_file && Number(record.source_total) > 1
				? ((await p.call(HISTORY + "workbench", {source_file: record.source_file, limit: 100}))?.items || [])
					.map(row => row.name)
				: [];
			await set_batch(siblings.includes(name) ? siblings : [name]);
			await open_record(name);
		}
		// 切走时收干净：预览框在 `window` 上挂过鼠标事件（见共用层 `document_viewer.destroy`）。
		const cleanup = () => {live = false; end_progress(); nav.destroy(); preview.destroy();};
		if (!still_current()) return cleanup;
		return cleanup;
	};
})();
