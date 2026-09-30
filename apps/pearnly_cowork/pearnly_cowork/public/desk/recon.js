/* 对账中心（Cowork）：银行对账 / 收入对账 各一个侧栏入口，同一套页面（用户 2026-09-26）。
 *
 *   列表：官方列表，列头排序、勾选删除，右上角「新建任务」；
 *   新建：上方两个上传口（各传一份对应的文件）→「开始对账」；
 *   核对中：官方整屏遮罩（`frappe.dom.freeze`），只显示阶段文字（照旧线上，不显示百分比）；
 *   完成：结果页就是导出的复核表，按导出的 Excel 逐格照画（用户 2026-09-26）；输入落在表里的格子可在线改，
 *         改了自动重算（服务端用同一套比对与同一个导出函数），有改动才出现「保存」。
 *
 * 列表、新建、遮罩、提示用官方控件；复核表是用户明确要求的自画例外（照 Excel 画，见任务记录）。
 * 服务端：`recon_center/api.py`（任务、阶段、列表、结果、删除），导出沿用各对账域的下载接口。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_cowork.recon_center.api.";
	// 页面等到后台任务的上限之后（任务 30 分钟、其中识别最多等 25 分钟）：原来页面 20 分钟先报超时，后台其实还在做。
	// 销项核查的发票可能上百张、按识别名额分批读，后台任务给到 60 分钟（jobs.TIMEOUTS）：页面等 62 分钟。
	// 识别排队的时间另算（`document_ocr.QUEUE_LIMIT`），后台任务给到三小时；销项核查五小时（发票读得慢，
	// `invoice_ocr.READ_LIMIT`）：页面等到后台任务上限之后再 2 分钟。
	// 进行中的看板每 5 秒读一次进度（原 1.5 秒；2026-10-01 生产压测：页面轮询占了网页 CPU 的一大块），对账一结束服务端推的
	// 「cowork_recon_finished」会立刻叫醒它去读结果。
	const POLL_MS = 5000, POLL_LIMITS_MS = { bank: 3 * 3600e3, income: 3 * 3600e3, vat: 5 * 3600e3 };
	const POLL_LIMIT_MS = kind => (POLL_LIMITS_MS[kind] || 3 * 3600e3) + 2 * 60e3;

	// 对账结束的原生通知（用户 2026-09-30：「结果完成时用原生带声音的通知」）：服务端结束时推给发起人，
	// 不管人在哪个页面都弹原生提示并响原生提示音（`p.success` / `play_sound`，跟账户静音走）；点一下打开结果。
	const ROOTS = { bank: "pearnly-recon-bank", income: "pearnly-recon-income", vat: "pearnly-recon-vat" };
	// 框架启动完（`app_ready`，实时连接此时才建好）再挂监听：脚本加载时 `frappe.realtime.on` 还没有连接，调用会被
	// 悄悄丢掉，提示从来没挂上（2026-09-30 测试机上推了三次都没弹）。看板页的「@了你」提示同一做法（work.js）。
	const listen = () => {
		if (p.recon_listening || !frappe.realtime?.socket) return;
		p.recon_listening = true;
		frappe.realtime.on("cowork_recon_finished", async data => {
			p.recon_wake?.(data?.job);
			const t = await p.texts("cowork.recon.");
			const root = ROOTS[data?.kind];
			if (!root) return;
			if (data.task) {
				const alert = frappe.show_alert({ message: `${p.escape(t("cowork.recon.finished", data.title || ""))}`, indicator: "green" }, 8);
				alert?.css?.("cursor", "pointer").on?.("click", () => frappe.set_route(root, data.task));
				frappe.utils.play_sound("submit");
			} else {
				// 人正看着这份的看板：看板自己弹失败原因，这里只响一声，不重复提示。
				const route = frappe.get_route();
				if (!(route[0] === root && route[1] === "run" && route[2] === data.job)) {
					frappe.show_alert({ message: p.escape(t("cowork.recon.finishedFailed", data.title || "")), indicator: "red" }, 8);
				}
				frappe.utils.play_sound("error");
			}
		});
	};
	$(document).on("app_ready", listen);
	if (frappe.app) listen();

	// 每种对账的差异：上传口与列表列。结果页（复核表）的数据见服务端 `recon_center/sheets.py`。
	// 三个对账功能的上传口都收：表格、PDF（含扫描件）、照片（识别任务支持的图片类型）。
	const DOCUMENTS = ".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.xlsx,.xls,.xlsm,.csv,.tsv";
	const KINDS = {
		bank: {
			root: "pearnly-recon-bank", title: "cowork.nav.reconBank", doctype: "Cowork Bank Reconciliation",
			export: "pearnly_cowork.reconciliation.bank_recon_api.export",
			// 两边都可多份：存折一页一张照片、总账分几段（旧线上同样逐份读再合并）。
			multiple: true,
			left: "cowork.recon.bank.left", right: "cowork.recon.bank.right",
			// 电子版优先（用户 2026-09-30）：上传口下一句轻提示，照片 / 扫描件只作后备。
			hint: { left: "cowork.recon.bank.leftHint" },
			// 对账预设能定的规则与可选的答案（服务端 `reconciliation/presets.py` 的 RULES）。
			presets: { grouped: ["yes", "no"], gl_account: null, card_fee: null },
			list: t => [
				{ fieldname: "title", width: 240, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "bank_code", width: 90, min_width: 70, label: t("cowork.recon.col.bank") },
				{ fieldname: "status", width: 100, min_width: 80, label: t("cowork.recon.col.status"),
					format: value => t(`cowork.recon.task.${value}`) },
				{ fieldname: "row_count", width: 80, min_width: 64, label: t("cowork.recon.col.rows") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
			],
			sort: ["title", "bank_code", "status", "row_count", "matched_count", "created_at"],
		},
		income: {
			root: "pearnly-recon-income", title: "cowork.nav.reconIncome", doctype: "Cowork Income Reconciliation",
			export: "pearnly_cowork.reconciliation.income_recon_api.export",
			// 税报可多份（一页一张照片、分几段的 PDF；用户 2026-09-30 定），总账一份。
			multiple: { right: true },
			left: "cowork.recon.income.left", right: "cowork.recon.income.right",
			presets: { excluded: ["yes", "no"] },
			list: t => [
				{ fieldname: "title", width: 240, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "row_count", width: 80, min_width: 64, label: t("cowork.recon.col.rows") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
				{ fieldname: "diff_count", width: 90, min_width: 72, label: t("cowork.recon.col.differences"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
				{ fieldname: "unmatched_count", width: 90, min_width: 72, label: t("cowork.recon.col.unmatched"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
			],
			sort: ["title", "row_count", "matched_count", "diff_count", "unmatched_count", "created_at"],
		},
		vat: {
			root: "pearnly-recon-vat", title: "cowork.nav.reconVat", doctype: "Cowork VAT Check",
			export: "pearnly_cowork.recon_center.api.export_vat", multiple: true,
			// 报告与发票都收表格 / PDF / 照片（2026-09-29 起扫描件与照片由识别任务读，旧线上同样收图片）。
			accept: { left: DOCUMENTS, right: DOCUMENTS },
			left: "cowork.recon.vat.left", right: "cowork.recon.vat.right",
			list: t => [
				{ fieldname: "title", width: 220, min_width: 160, label: t("cowork.recon.col.title") },
				{ fieldname: "period", width: 90, min_width: 72, label: t("cowork.recon.col.period") },
				{ fieldname: "invoice_count", width: 80, min_width: 64, label: t("cowork.recon.col.invoices") },
				{ fieldname: "matched_count", width: 90, min_width: 72, label: t("cowork.recon.col.matched") },
				{ fieldname: "diff_count", width: 90, min_width: 72, label: t("cowork.recon.col.differences"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
				{ fieldname: "unmatched_count", width: 90, min_width: 72, label: t("cowork.recon.col.unmatched"),
					cell_class: value => Number(value) ? "pearnly-difference" : "" },
			],
			sort: ["title", "period", "invoice_count", "matched_count", "diff_count", "unmatched_count", "created_at"],
		},
	};

	p.coworkRecon = kind => async (page, _shell_t, wrapper) => {
		const spec = KINDS[kind];
		const t = await p.texts("cowork.recon.");
		const root = spec.root;
		let list, current, sequence = 0;
		const title = () => __(spec.title);
		const breadcrumbs = trail => p.breadcrumbs({ label: title(), route: p.subroute(root), trail: trail || [] });
		p.add_refresh(page, () => current ? show_result(current) : list?.reload());

		function clear() {
			list?.destroy();
			list = null;
			page.page_form.detach();
			page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
			page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
			page.hide_form(); page.clear_primary_action(); page.clear_secondary_action(); page.clear_indicator();
		}

		// ── 列表 ──────────────────────────────────────────────────────────────
		function show_list() {
			page.set_title(title()); breadcrumbs();
			const selection = p.selection_action({
				page, list: () => list,
				empty: () => { if (frappe.get_route().length === 1) page.set_primary_action(t("cowork.recon.new"), open_new); },
				label: picked => t("cowork.recon.deleteSelected", picked.length),
				confirm: picked => t("cowork.recon.deleteConfirm", picked.length),
				run: async picked => {
					await p.call(api + "delete", { kind, task_ids: JSON.stringify(picked.map(row => row.name)) }, "POST");
				},
				success: picked => t("cowork.recon.deleted", picked.length),
				sound: "delete",
			});
			list = p.list({ page, doctype: spec.doctype, selectable: true, compact_columns: true, on_selection: selection,
				on_open: row => frappe.set_route(root, row.name),
				empty_action: { label: t("cowork.recon.new"), action: open_new },
				sort_fields: spec.sort,
				load: async ({ start, page_length, sort_by, sort_order }) => {
					const result = await p.call(api + "tasks", { kind, start, limit: page_length, sort_by, sort_order });
					return { items: result.tasks, total: result.total };
				},
				columns: [...spec.list(t),
					{ fieldname: "created_at", width: 160, min_width: 148, label: t("cowork.recon.col.createdAt"),
						format: value => p.datetime_text(value) }],
			});
			page.set_primary_action(t("cowork.recon.new"), open_new);
			if (spec.presets) page.add_inner_button(t("cowork.recon.presets"), open_presets);
			// 进行中的对账不再摆在列表上方（用户 2026-09-30）：一颗官方按钮「进行中 · N」通往单独一页；
			// 有对账做完（件数变少）就刷新列表，结果自动落进来。
			const running = page.add_inner_button(t("cowork.recon.running"), () => frappe.set_route(root, "running")).hide();
			let before = null;
			watch(async () => {
				const items = await p.call(api + "active", { kind });
				running.text(t("cowork.recon.runningCount", items.length)).toggle(items.length > 0);
				if (before !== null && items.length < before) await list?.reload();
				before = items.length;
			});
		}
		// 列表 / 进行中两页的定时刷新：切走（换路由）就停。
		let ticker = 0;
		function watch(tick, every = 5000) {
			const request = sequence;
			const run = async () => {
				if (request !== sequence) return;
				try { await tick(); } catch (error) { /* 下一轮再试 */ }
				if (request === sequence) ticker = setTimeout(run, every);
			};
			void run();
		}
		const open_new = () => frappe.set_route(root, "new");

		// ── 对账预设：看、改、删（结果页问过一次、按账户记住的答案）─────────────────────
		// 改了只影响以后的对账和重新打开的结果；删了以后同类情况重新问。
		async function open_presets() {
			const dialog = new frappe.ui.Dialog({ title: t("cowork.recon.presets"), fields: [{ fieldtype: "HTML", fieldname: "body" }] });
			const body = dialog.fields_dict.body.$wrapper[0];
			const call = async (method, args) => draw(await p.call(presets_api + method, { kind, ...args }, "POST"));
			const draw = items => {
				if (!items.length) {
					const empty = document.createElement("p");
					empty.className = "text-muted";
					empty.textContent = t("cowork.recon.presets.empty");
					body.replaceChildren(empty);
					return;
				}
				body.replaceChildren(...items.map(item => {
					const row = document.createElement("div");
					row.className = "pearnly-recon-preset";
					const head = document.createElement("div");
					head.className = "pearnly-recon-preset-head";
					const name = document.createElement("span");
					name.textContent = `${item.scope_label} · ${t(`cowork.recon.rule.${item.rule}`)}`;
					const remove = document.createElement("button");
					remove.type = "button";
					remove.className = "btn btn-xs btn-default";
					remove.textContent = t("cowork.recon.preset.remove");
					remove.addEventListener("click", () => call("remove", { scope_key: item.scope, rule: item.rule }));
					head.append(name, remove);
					// 固定选项的规则可直接改；答案是科目号的（对哪个总账科目）只显示，要改就删了让它下次重新问。
					const values = spec.presets[item.rule];
					let select;
					if (values) {
						select = document.createElement("select");
						select.className = "form-control input-sm";
						for (const value of values) {
							const option = new Option(t(`cowork.recon.rule.${item.rule}.${value}`), value, false, value === item.value);
							select.append(option);
						}
						select.addEventListener("change", () => call("change", { scope_key: item.scope, rule: item.rule, value: select.value }));
					} else {
						// 答案是文字的：科目号照原样；刷卡手续费率写成「1.95%」，「不是」写成文字。要改就删了让它下次重新问。
						select = document.createElement("div");
						select.textContent = item.rule !== "card_fee" ? item.value
							: item.value === "no" ? t("cowork.recon.rule.card_fee.no") : `${item.value}%`;
					}
					const by = document.createElement("div");
					by.className = "text-muted small";
					by.textContent = t("cowork.recon.preset.by", item.set_by_name || item.set_by || "", p.datetime_text(item.set_at));
					row.append(head, select, by);
					return row;
				}));
			};
			draw(await p.call(presets_api + "listing", { kind }));
			dialog.show();
		}

		// ── 新建：两个上传口 → 开始对账 ────────────────────────────────────────
		function show_new() {
			page.set_title(t("cowork.recon.new")); breadcrumbs([{ label: t("cowork.recon.new") }]);
			const chosen = { left: null, right: null };
			// 两个上传口左右并排：官方 FieldGroup 的两栏，每栏一个标题 + 选择文件 + 文件名。
			const slot = side => [
				{ fieldtype: "Heading", fieldname: `${side}_heading`, label: t(spec[side]) },
				{ fieldtype: "Button", fieldname: `${side}_pick`, label: t("cowork.recon.pick"), click: async () => {
					const many = spec.multiple === true || Boolean(spec.multiple?.[side]);
					const files = await p.pick_files(spec.accept?.[side] || DOCUMENTS, many);
					if (!files.length) return;
					chosen[side] = many ? files : files[0];
					await form.set_value(`${side}_file`, files.length > 1
						? t("cowork.recon.files", files.length) : files[0].name);
				} },
				{ fieldtype: "Data", fieldname: `${side}_file`, label: t("cowork.recon.file"), read_only: 1,
					description: spec.hint?.[side] ? t(spec.hint[side]) : "" },
			];
			const form = p.layout(page.main, [
				...slot("left"),
				{ fieldtype: "Column Break" },
				...slot("right"),
				// 任务名称放回来（用户 2026-09-30 晚）：不填就用上传的前两个文件名。
				{ fieldtype: "Section Break" },
				{ fieldtype: "Data", fieldname: "title", label: t("cowork.recon.col.title") },
			]);
			page.set_primary_action(t("cowork.recon.start"), async () => {
				if (!chosen.left || !chosen.right) { frappe.msgprint(t("cowork.recon.bothFiles")); return; }
				await run(chosen, (form.get_value("title") || "").trim());
			});
			page.set_secondary_action(__("Cancel"), () => frappe.set_route(root));
		}

		// 开始对账：上传时短暂遮罩（文件还在路上），拿到任务号就进识别看板（不再整屏白屏等）。
		async function run(chosen, task_title) {
			await follow(() => p.post_files(api + "start", { kind, title: task_title, left: chosen.left,
				right: chosen.right, lang: (frappe.boot.lang || "th").slice(0, 2) }));
		}

		// 新建对账、答了要重跑的预设问题（用原文件重跑）：拿到后台任务号就进看板；看板的网址带着任务号，离开再回来能接上。
		async function follow(begin) {
			frappe.dom.freeze(p.escape(t("cowork.recon.stage.upload")));
			let started;
			try { started = await begin(); } finally { frappe.dom.unfreeze(); }
			if (started?.job) frappe.set_route(root, "run", started.job);
		}

		// ── 识别看板（用户 2026-09-30：「不想看这个大白屏，起码要知道他在干嘛，有点动态好玩的东西展示」）────────
		// 只画真实发生的事：每份文件一排格子、一格一页（读字整块变蓝、整理完变绿、没读出变红，电子版一下全绿）；
		// 排队时写前面还有几份；「刚读出」滚出每页真读出的最后一笔。数字都来自服务端（recon_center/progress.py），不估算。
		const element = (tag, className, text) => {
			const node = document.createElement(tag);
			if (className) node.className = className;
			if (text !== undefined) node.textContent = text;
			return node;
		};
		function tiles(file) {
			const grid = element("div", "pearnly-recon-tiles");
			const pages = Math.max(file.pages, 1);
			const unread = new Set(file.unread || []);
			let done = file.done;
			for (let page = 1; page <= pages; page++) {
				const tile = element("span", "pearnly-recon-tile");
				if (unread.has(page)) tile.classList.add("is-unread");
				else if (file.state === "done" || done > 0) { tile.classList.add("is-done"); if (file.state !== "done") done -= 1; }
				else if (page <= file.read) tile.classList.add("is-read");
				grid.append(tile);
			}
			const next = [...grid.children].find(tile => !tile.classList.length || tile.classList.length === 1
				|| tile.classList.contains("is-read"));
			if (next && ["reading", "queued", "waiting"].includes(file.state)) next.classList.add("is-current");
			return grid;
		}
		function file_row(file) {
			const row = element("div", "pearnly-recon-file");
			const head = element("div", "pearnly-recon-file-head");
			const name = element("span", "", file.name || "");
			if (file.via === "text") name.append(element("span", "indicator-pill green pearnly-recon-via", t("cowork.recon.live.electronic")));
			const count = element("span", "text-muted", file.state === "queued" && file.ahead
				? t("cowork.recon.live.ahead", file.ahead)
				: file.pages ? t("cowork.recon.live.pages", Math.min(file.done + (file.unread || []).length, file.pages), file.pages)
				: t(`cowork.recon.live.${file.state === "done" ? "read" : "waiting"}`));
			head.append(name, count);
			row.append(head, tiles(file));
			return row;
		}
		function many_files(files) {
			// 发票几十张：一份一格，放在一排里（一格一页太长）。
			const row = element("div", "pearnly-recon-file");
			const finished = files.filter(file => ["done", "failed"].includes(file.state)).length;
			row.append(element("div", "pearnly-recon-file-head text-muted", t("cowork.recon.live.files", finished, files.length)));
			const grid = element("div", "pearnly-recon-tiles");
			for (const file of files) {
				const tile = element("span", "pearnly-recon-tile");
				tile.title = file.name || "";
				tile.classList.add(file.state === "done" ? "is-done" : file.state === "failed" ? "is-unread"
					: file.state === "reading" ? "is-read" : "is-waiting");
				grid.append(tile);
			}
			const next = grid.querySelector(".is-read, .is-waiting");
			if (next) next.classList.add("is-current");
			row.append(grid);
			return row;
		}
		function ticker_line(item) {
			const kind = item.kind ? t(`cowork.recon.live.kind.${item.kind}`) : "";
			const parts = [item.name, item.page ? t("cowork.recon.live.page", item.page) : "",
				item.rows ? t("cowork.recon.live.rows", item.rows) : "",
				[item.date, kind, item.amount].filter(Boolean).join(" ")];
			return parts.filter(Boolean).join(" · ");
		}
		async function show_progress(job) {
			const request = ++sequence;
			// 面包屑：对账 / 进行中 / 这一份（点「进行中」回到进行中那一页；地址要写全，写成相对的会接在当前地址后面）
			const trail = name => breadcrumbs([{ label: t("cowork.recon.running"), route: p.subroute(root, "running") },
				{ label: name || t("cowork.recon.checking") }]);
			let titled = "";
			page.set_title(t("cowork.recon.checking")); trail();
			const board = element("div", "pearnly-recon-board");
			// 看板顶上那排阶段标签去掉（用户 2026-09-30 晚：「上面这排状态字不要」）：进行中列表里的状态、下面的文件格子保留。
			const sides = element("div", "pearnly-recon-sides");
			const live = element("div", "pearnly-recon-live");
			board.append(sides, live);
			page.main.append(board);
			const seen = new Set();
			const began = Date.now();
			const draw = state => {
				if (state.title && state.title !== titled) { titled = state.title; page.set_title(titled); trail(titled); }
				const bySide = { left: [], right: [] };
				for (const file of state.files || []) (bySide[file.side] || bySide.left).push(file);
				sides.replaceChildren(...["left", "right"].filter(side => bySide[side].length).map(side => {
					const box = element("div", "pearnly-recon-side");
					box.append(element("div", "pearnly-recon-side-title", t(spec[side])));
					if (bySide[side].length > 6) box.append(many_files(bySide[side]));
					else box.append(...bySide[side].map(file_row));
					return box;
				}));
				const items = state.latest || [];
				live.replaceChildren(...(items.length ? [element("div", "pearnly-recon-live-title", t("cowork.recon.live.latest"))] : []),
					...items.map(item => {
						const key = JSON.stringify(item);
						const line = element("div", `pearnly-recon-live-item${seen.has(key) ? "" : " is-new"}`, ticker_line(item));
						seen.add(key);
						return line;
					}));
			};
			page.set_secondary_action(t("cowork.recon.live.back"), () => frappe.set_route(root));
			for (;;) {
				let state;
				try { state = await frappe.xcall(api + "status", { job }); } catch (error) { state = null; }
				if (request !== sequence) return;
				if (state) {
					if (state.state === "done" && state.task) { frappe.set_route(root, state.task); return; }
					if (state.state === "failed") {
						draw(state);
						frappe.msgprint({ title: t("cowork.recon.failed"), message: p.escape(state.error || ""), indicator: "red" });
						return;
					}
					draw(state);
				}
				if (Date.now() - began > POLL_LIMIT_MS(kind)) { frappe.msgprint(t("cowork.recon.timeout")); return; }
				await new Promise(resolve => {
					const timer = setTimeout(resolve, POLL_MS);
					p.recon_wake = finished => { if (finished === job) { clearTimeout(timer); resolve(); } };
				});
				if (request !== sequence) return;
			}
		}

		// 「进行中」一页：本人还在做的对账，与列表同一个官方列表（任务名称 · 进度 · 开始时间 · 状态靠右）。
		// 点一行进看板；做完的自动从这页消失、落进列表，全部做完就回到列表。
		function show_running() {
			const label = t("cowork.recon.running");
			page.set_title(label); breadcrumbs([{ label }]);
			list = p.list({ page, doctype: spec.doctype, selectable: false, compact_columns: true,
				on_open: row => frappe.set_route(root, "run", row.name),
				load: async () => {
					const items = await p.call(api + "active", { kind });
					return { items: items.map(item => ({ ...item, name: item.job })), total: items.length };
				},
				columns: [
					{ fieldname: "title", width: 320, min_width: 180, label: t("cowork.recon.col.title") },
					{ fieldname: "done", width: 110, min_width: 90, label: t("cowork.recon.col.progress"),
						format: (value, row) => row.pages ? t("cowork.recon.live.pages", value, row.pages) : "" },
					{ fieldname: "started", width: 160, min_width: 148, label: t("cowork.recon.col.startedAt"),
						format: value => value ? p.datetime_text(value) : "" },
					// 状态用原生彩色标签：排队灰、进行中黄（与看板同一套颜色）。
					{ fieldname: "stage", width: 170, min_width: 140, label: t("cowork.recon.col.status"),
						format: value => t(`cowork.recon.stage.${value || "queued"}`),
						cell_class: value => `indicator-pill ${!value || value === "queued" ? "gray" : "yellow"}` },
				],
			});
			page.set_secondary_action(t("cowork.recon.live.back"), () => frappe.set_route(root));
			let shown = null;
			watch(async () => {
				if (shown === null) {
					await list?.ready;  // 第一次由列表自己加载
					list?.$frappe_list?.addClass("pearnly-recon-running");
					shown = 0;
				} else await list?.refresh();
				if (shown && !list?.total) { frappe.set_route(root); return; }
				shown = list?.total || shown;
			}, 3000);
		}

		// ── 结果页 ────────────────────────────────────────────────────────────────
		// 数据仍是导出同一个函数生成的工作簿（`recon_center/sheets.py`：每格文字、样式、合并、列宽、冻结），计算与导出不变。
		// 页面外观 2026-09-27 按用户改成原生风格（"改为图二那种，可以点击排序……整屏看起来视觉上协调能对齐"）：
		//   明细表 = 原生表格外观（浅灰表头、点列头排序、行线与悬停色），状态格用原生彩色标签，合计行固定在底部；
		//   汇总表 = 卡片：调节过程一张主卡，账户信息 / 结果 / 文件信息放右侧，一屏看全；
		//   工作表切换放在顶部原生页签。排序只改显示顺序；可改的格子带着自己的输入位置，排序后照样能改。
		const sheets_api = "pearnly_cowork.recon_center.sheets.";
		const presets_api = "pearnly_cowork.recon_center.presets.";
		const lang = () => (frappe.boot.lang || "th").slice(0, 2);
		const clone = value => JSON.parse(JSON.stringify(value));
		// 工作簿里的底色 / 字色 → 原生色名。
		const TONES = { "#DCFCE7": "green", "#FEE2E2": "red", "#FEF3C7": "orange", "#DBEAFE": "blue" };
		const RED_TEXT = new Set(["#FF0000", "#DC2626", "#B91C1C", "#991B1B"]);
		const GREEN_TEXT = new Set(["#166534", "#16A34A", "#065F46"]);
		// 金额 / 数量 / 百分比算数字（右对齐、按数值排）；税号、单号这类长串数字是编号，按文字处理。
		const is_number = text => {
			const value = text.trim();
			return /^[-+]?\(?[\d,]*\.?\d+\)?%?$/.test(value) && (/[.,%]/.test(value) || value.replace(/\D/g, "").length <= 5);
		};
		// 合计行的公式值在工作簿里没带数字格式：照这一列的金额写法补上千分位与两位小数（只改显示）。
		const money = value => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
		const is_header = (row, styles) => row.c.some(item => item && styles[item[1]].bg === "#1F2937");
		const is_blank = row => row.c.every(item => !item || item[0] === "");

		function tone_of(style) {
			return TONES[(style.bg || "").toUpperCase()] || null;
		}

		function text_tone(style) {
			const fg = (style.fg || "").toUpperCase();
			return RED_TEXT.has(fg) ? "red" : GREEN_TEXT.has(fg) ? "green" : null;
		}

		// 一格的内容：状态字（有底色、不是数字）画成原生彩色标签，数字右对齐等宽，红绿字保留。
		function fill_cell(node, text, style) {
			const tone = tone_of(style), numeric = is_number(text);
			node.classList.toggle("is-number", numeric || style.h === "right");
			if (style.h === "center" && !tone) node.classList.add("is-center");
			if (style.b) node.classList.add("is-bold");
			const color = text_tone(style) || (numeric ? tone : null);
			if (color) node.classList.add(`text-${color}`);
			if (tone && !numeric && text) {
				const pill = document.createElement("span");
				pill.className = `indicator-pill no-indicator-dot ${tone} ellipsis`;
				// 标签颜色已表明状态：去掉开头的任何符号 / 表情（原来只去 ✓❗⚠✗✔，🔍🟡 留着）。
				pill.textContent = text.replace(/^(?:[\p{Extended_Pictographic}\p{So}]\uFE0F?\s*)+/u, "");
				pill.title = text;
				node.append(pill);
			} else {
				node.textContent = text;
				if (text) node.title = text;
			}
		}

		function make_editable(node, text, target, on_edit) {
			node.contentEditable = "true";
			node.classList.add("editable");
			node.addEventListener("keydown", event => {
				if (event.key === "Enter") { event.preventDefault(); node.blur(); }
				if (event.key === "Escape") { node.textContent = text; node.blur(); }
			});
			node.addEventListener("blur", () => {
				const typed = node.innerText.replace(/\n/g, " ").trim();
				on_edit(target, typed === text.trim() ? null : typed);
			});
		}

		// 排序用的键：金额 / 百分比按数值，dd/mm/yyyy 按日期，其余按文字；空格子永远排在最后。
		function sort_key(text) {
			const value = text.trim();
			if (!value || value === "—") return null;
			if (is_number(value)) {
				const negative = value.startsWith("-") || value.startsWith("(");
				const number = parseFloat(value.replace(/[^\d.]/g, ""));
				return negative ? -number : number;
			}
			const date = value.match(/^(\d{2})\/(\d{2})\/(\d{4})(?: (\d{2}):(\d{2}))?$/);
			if (date) return Number(`${date[3]}${date[2]}${date[1]}${date[4] || "00"}${date[5] || "00"}`);
			return value;
		}

		function compare(a, b) {
			if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
			if (typeof a === "number" && typeof b === "number") return a - b;
			return String(a).localeCompare(String(b), frappe.boot.lang || undefined, { numeric: true });
		}

		// 列宽按页面字号量内容（工作簿里的列宽是 Excel 字号的，页面上会截断日期和金额）：表头与每格取最宽，夹在上下限之间。
		const WIDTH_MIN = 56, WIDTH_MAX = 360, CELL_PAD = 24, PILL_PAD = 28, SORT_PAD = 18;
		let measurer;
		function column_widths(host, head, rows, styles) {
			measurer = measurer || document.createElement("canvas").getContext("2d");
			const font = getComputedStyle(host);
			measurer.font = `${font.fontSize} ${font.fontFamily}`;
			return head.c.map((item, c) => {
				let width = item ? measurer.measureText(item[0]).width + SORT_PAD : 0;
				rows.forEach(row => {
					const cell = row.c[c];
					if (!cell || cell[2]?.cs > 1) return;
					const pill = tone_of(styles[cell[1]]) && !is_number(cell[0]);
					width = Math.max(width, measurer.measureText(cell[0]).width + (pill ? PILL_PAD : 0));
				});
				// 泰文等由备用字体画出来比量的略宽：留 8% 余量。
				return Math.round(Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, width * 1.08 + CELL_PAD)));
			});
		}

		// 明细表：表头一行 + 数据行 + 末尾的合计行（加粗灰底，固定在底部、不参与排序）。
		function draw_list(host, sheet, styles, sort, on_sort, on_edit) {
			const head_index = sheet.rows.findIndex(row => is_header(row, styles));
			const head = sheet.rows[head_index];
			let body = sheet.rows.slice(head_index + 1).filter(row => !is_blank(row));
			let footer = [];
			while (body.length) {
				const last = body[body.length - 1], first = last.c.find(Boolean);
				if (!first || !styles[first[1]].b || (styles[first[1]].bg || "").toUpperCase() !== "#F3F4F6") break;
				footer.unshift(body.pop());
			}
			const money_columns = head.c.map((_item, c) => body.some(row => /\.\d{2}$/.test(row.c[c]?.[0] || "")));
			footer = footer.map(row => ({ ...row, c: row.c.map((item, c) => item && money_columns[c] && /^-?\d+(\.\d+)?$/.test(item[0])
				? [money(item[0]), ...item.slice(1)] : item) }));
			const merged = body.some(row => row.c.some(item => item?.[2]?.cs > 1 || item?.[2]?.rs > 1));
			if (sort && !merged) {
				const keyed = body.map((row, index) => ({ row, index, key: sort_key(row.c[sort.column]?.[0] || "") }));
				keyed.sort((a, b) => {
					if (a.key === null || b.key === null) return compare(a.key, b.key) || a.index - b.index;
					return (sort.order === "desc" ? compare(b.key, a.key) : compare(a.key, b.key)) || a.index - b.index;
				});
				body = keyed.map(item => item.row);
			}
			const frozen = sheet.freeze[1] || 0;
			const widths = column_widths(host, head, [...body, ...footer], styles);
			const lefts = widths.reduce((out, width, i) => [...out, i ? out[i - 1] + widths[i - 1] : 0], []);
			const table = document.createElement("table");
			table.className = "pearnly-recon-table";
			table.style.minWidth = `${widths.reduce((sum, width) => sum + width, 0)}px`;
			const columns = document.createElement("colgroup");
			widths.forEach(width => {
				const column = document.createElement("col");
				column.style.width = `${width}px`;
				columns.append(column);
			});
			const pin = (node, c) => {
				if (c >= frozen) return;
				node.classList.add("is-pinned");
				node.style.setProperty("--pin-left", `${lefts[c]}px`);
				if (c === frozen - 1) node.classList.add("is-pinned-last");
			};
			const thead = document.createElement("thead");
			const head_line = document.createElement("tr");
			head.c.forEach((item, c) => {
				if (!item) return;
				const th = document.createElement("th");
				const label = document.createElement("span");
				label.className = "ellipsis";
				label.textContent = item[0];
				th.title = item[0];
				th.append(label);
				// 表头的对齐跟着这一列的数据走（金额列右对齐）。
				const sample = body.map(row => row.c[c]?.[0]).find(value => value);
				if (sample && is_number(sample)) th.classList.add("is-number");
				if (!merged) {
					th.classList.add("sortable");
					const arrow = document.createElement("span");
					arrow.className = "pearnly-recon-sort";
					arrow.textContent = sort?.column === c ? (sort.order === "asc" ? "↑" : "↓") : "";
					th.append(arrow);
					th.addEventListener("click", () => on_sort(c));
				}
				pin(th, c);
				head_line.append(th);
			});
			thead.append(head_line);
			const line_of = (row, footer_row) => {
				const line = document.createElement("tr");
				row.c.forEach((item, c) => {
					if (!item) return;
					const [text, style_id, extra = {}] = item;
					const node = document.createElement("td");
					if (extra.cs > 1) node.colSpan = extra.cs;
					if (extra.rs > 1) node.rowSpan = extra.rs;
					fill_cell(node, text, footer_row ? { ...styles[style_id], bg: null } : styles[style_id]);
					if (extra.e) make_editable(node, text, extra.e, on_edit);
					pin(node, c);
					line.append(node);
				});
				return line;
			};
			const tbody = document.createElement("tbody");
			body.forEach(row => tbody.append(line_of(row, false)));
			table.append(columns, thead, tbody);
			if (footer.length) {
				const tfoot = document.createElement("tfoot");
				footer.forEach(row => tfoot.append(line_of(row, true)));
				table.append(tfoot);
			}
			host.replaceChildren(table);
		}

		// 汇总表（两列：项目 / 金额）：按空行分块，带表头的那块是调节过程（主卡），其余块是右侧的小卡。
		function draw_summary(host, sheet, styles) {
			const blocks = [[]];
			sheet.rows.forEach(row => {
				if (is_blank(row)) { if (blocks[blocks.length - 1].length) blocks.push([]); return; }
				blocks[blocks.length - 1].push(row);
			});
			if (!blocks[blocks.length - 1].length) blocks.pop();
			const span_all = row => row.c[0]?.[2]?.cs >= sheet.widths.length;
			const card_of = rows => {
				const card = document.createElement("div");
				card.className = "pearnly-recon-card";
				let rest = rows;
				// 块的第一行是整行合并的标题（如"银行对账 · KBANK""文件信息"）→ 卡片标题。
				if (rows.length > 1 && span_all(rows[0])) {
					const heading = document.createElement("div");
					heading.className = "pearnly-recon-card-title";
					heading.textContent = rows[0].c[0][0];
					card.append(heading);
					rest = rows.slice(1);
				}
				const table = document.createElement("table");
				table.className = "pearnly-recon-table pearnly-recon-summary";
				const columns = document.createElement("colgroup");
				columns.innerHTML = '<col><col class="amount">';
				table.append(columns);
				const tbody = document.createElement("tbody");
				rest.forEach(row => {
					const line = document.createElement("tr");
					if (is_header(row, styles)) {
						row.c.forEach(item => {
							if (!item) return;
							const th = document.createElement("th");
							th.textContent = item[0];
							if (styles[item[1]].h === "center" && line.children.length) th.classList.add("is-number");
							line.append(th);
						});
						table.createTHead().append(line);
						return;
					}
					const first = styles[row.c[0]?.[1]] || {};
					if (span_all(row)) line.classList.add("is-section");
					else if (first.bg && (first.bg || "").toUpperCase() === "#F3F4F6") line.classList.add("is-subtotal");
					const tone = tone_of(first);
					if (tone) line.classList.add(`tone-${tone}`);
					row.c.forEach((item, c) => {
						if (!item) return;
						const [text, style_id, extra = {}] = item;
						const style = styles[style_id];
						const node = document.createElement("td");
						if (extra.cs > 1) node.colSpan = extra.cs;
						const indent = c === 0 ? (style.in || 0) : 0;
						if (indent > 1 || /^\s{2,}·/.test(text)) node.classList.add("is-detail");
						fill_cell(node, c === 0 ? text.trim() : text, tone && c === 0 ? { ...style, bg: null } : style);
						line.append(node);
					});
					tbody.append(line);
				});
				table.append(tbody);
				card.append(table);
				return card;
			};
			const main = blocks.findIndex(rows => rows.some(row => is_header(row, styles)));
			const layout = document.createElement("div");
			layout.className = "pearnly-recon-summary-layout";
			const side = document.createElement("div");
			side.className = "pearnly-recon-summary-side";
			blocks.forEach((rows, index) => {
				if (index === main) return;
				side.append(card_of(rows));
			});
			if (main >= 0) layout.append(card_of(blocks[main]));
			if (side.children.length) layout.append(side);
			else layout.classList.add("single");
			host.replaceChildren(layout);
		}

		function draw_sheet(host, sheet, styles, sort, on_sort, on_edit) {
			const list = sheet.widths.length > 2 && sheet.rows.some(row => is_header(row, styles));
			host.classList.toggle("is-summary", !list);
			if (list) draw_list(host, sheet, styles, sort, on_sort, on_edit);
			else draw_summary(host, sheet, styles);
		}

		async function show_result(name, loaded, ask_next) {
			const request = ++sequence;
			if (!loaded) {
				// 数据还没回来先摆官方列表打开时那套占位骨架（用户 2026-09-30：点进结果 / 复核页整屏白着）。
				page.main.children(".list-skeleton").remove();
				// 结果页没有侧栏，只摆内容那一块。
				page.main.append($(`<div class="row list-skeleton"><div class="col"><div class="list-skeleton-box"></div></div></div>`));
			}
			const data = loaded || await p.call(sheets_api + "load", { kind, task_id: name, lang: lang() })
				.finally(() => page.main.children(".list-skeleton").remove());
			if (request !== sequence || frappe.get_route()[1] !== name) return;
			clear();
			let modified = data.task.modified, dirty = false, pending = null, active = 0, view = data, waiting = null;
			let status = data.task.status;
			let inputs = clone(data.inputs), good = clone(data.inputs);
			page.set_title(data.task.title || name); breadcrumbs([{ label: data.task.title || name }]);
			// 为什么要人看一眼：跟在标题旁的原生指示器里（用户 2026-09-28：只在页面写，导出的 Excel 不加）。
			// 这一版 Desk 的页面模板没有副标题位（set_title_sub 等于白写），与 ERP 库存页同一做法；悬停看全文。
			const notice = [data.task.status !== "completed" ? t(`cowork.recon.task.${data.task.status}`) : "",
				...(data.task.notes || [])].filter(Boolean).join(" · ");
			if (notice) {
				page.set_indicator(notice, "orange");
				page.indicator?.attr("title", notice);
			}
			// 导出取服务端存着的版本：有没保存的改动就先保存再导出，导出的与页面上看到的一致（2026-09-28 录制发现）。
			const export_now = async () => {
				if (dirty) await save();
				window.location.assign(`/api/method/${spec.export}?task_id=${encodeURIComponent(name)}&lang=${lang()}`);
			};
			const buttons = () => {
				page.clear_primary_action(); page.clear_secondary_action();
				// 需复核的银行对账没改也要能保存（确认），否则只有导出（用户 2026-09-29 选 B：有断点逐个确认后保存）。
				if (dirty || (kind === "bank" && status === "needs_review")) {
					page.set_primary_action(__("Save"), save);
					page.set_secondary_action(t("cowork.recon.export"), export_now);
				} else {
					page.set_primary_action(t("cowork.recon.export"), export_now);
				}
			};
			// 顶部原生页签切换工作表；表格区按窗口高度铺满，只在表内滚动（表头、合计行、冻结列固定）。
			const book = document.createElement("div");
			book.className = "pearnly-recon-book";
			const tabs = document.createElement("nav");
			tabs.className = "form-tabs-list pearnly-recon-tabs";
			const tab_list = document.createElement("ul");
			tab_list.className = "nav form-tabs";
			tabs.append(tab_list);
			const scroller = document.createElement("div");
			scroller.className = "pearnly-recon-scroll";
			book.append(tabs, scroller);
			page.main.append(book);
			const sorts = {};
			const fit = () => {
				if (!scroller.isConnected) { window.removeEventListener("resize", fit); return; }
				// 汇总是卡片，跟整页一起滚；明细表铺满窗口余下的高度，只在表内滚。
				const top = scroller.getBoundingClientRect().top + window.scrollY;
				scroller.style.maxHeight = scroller.classList.contains("is-summary") ? ""
					: `${Math.max(320, window.innerHeight - top - 24)}px`;
			};
			window.addEventListener("resize", fit);

			const paint = () => {
				// 正在改某一格时先不重画，等这一格改完（失焦）再画最新的。
				if (book.contains(document.activeElement) && document.activeElement.isContentEditable) { waiting = view; return; }
				waiting = null;
				active = Math.min(active, view.sheets.length - 1);
				const sheet = view.sheets[active];
				const scroll = [scroller.scrollLeft, scroller.scrollTop];
				draw_sheet(scroller, sheet, view.styles, sorts[active], on_sort, edit);
				[scroller.scrollLeft, scroller.scrollTop] = scroll;
				tab_list.replaceChildren(...view.sheets.map((item, index) => {
					const tab = document.createElement("li");
					tab.className = "nav-item";
					const link = document.createElement("button");
					link.type = "button";
					link.className = `nav-link${index === active ? " active" : ""}`;
					link.textContent = item.name;
					link.addEventListener("click", () => {
						if (index === active) return;
						active = index; scroller.scrollLeft = 0; scroller.scrollTop = 0; paint();
					});
					tab.append(link);
					return tab;
				}));
				tabs.classList.toggle("hide", view.sheets.length < 2);
				fit();
			};
			// 点列头：升序 → 降序 → 恢复原顺序（与原生表格相同）。
			function on_sort(column) {
				const sort = sorts[active];
				sorts[active] = sort?.column !== column ? { column, order: "asc" }
					: sort.order === "asc" ? { column, order: "desc" } : undefined;
				paint();
			}
			const recalc = async () => {
				const payload = clone(inputs);
				pending = payload;
				try {
					const result = await p.call(sheets_api + "recalc", { kind, task_id: name, inputs: JSON.stringify(payload), lang: lang() }, "POST");
					if (pending !== payload || frappe.get_route()[1] !== name) return;
					view = result; inputs = clone(result.inputs); good = clone(result.inputs);
				} catch (error) {
					// 打进去的字不合格（日期、金额）：退回上一次算得通的输入，表也画回去。
					if (pending !== payload) return;
					inputs = clone(good);
				}
				paint();
			};
			function edit(target, typed) {
				if (typed === null) { if (waiting) paint(); return; }
				const [key, index, field] = target;
				inputs[key][index][field] = typed;
				if (!dirty) { dirty = true; buttons(); }
				void recalc();
			}
			// 余额链还连不上的每一处：原生对话框逐个选「此处缺页 / 已知差异」，都选了才保存（用户 2026-09-29 选 B）。
			const confirm_breaks = found => new Promise(resolve => {
				const choices = ["missing_page", "known_difference"];
				const labels = choices.map(choice => t(`cowork.recon.break.${choice}`));
				const dialog = new frappe.ui.Dialog({
					title: t("cowork.recon.breaks"),
					fields: found.map(item => ({ fieldtype: "Select", fieldname: `b${item.idx}`, reqd: 1,
						// 多读 / 少读一个 0 的只提示「疑似应为 X」，不自动改（用户 2026-09-30 定）。
						label: [item.idx + 1, item.date, item.diff, item.suggest && t("cowork.recon.break.suggest", item.suggest)]
							.filter(Boolean).join(" · "), options: ["", ...labels] })),
					primary_action_label: __("Save"),
					primary_action(values) {
						resolve(Object.fromEntries(found.map(item => [item.idx, choices[labels.indexOf(values[`b${item.idx}`])]])));
						dialog.hide();
					},
				});
				dialog.onhide = () => resolve(null);
				dialog.show();
			});
			async function save() {
				let acknowledged;
				if (kind === "bank" && status === "needs_review") {
					const found = await p.call(sheets_api + "breaks", { kind, task_id: name, inputs: JSON.stringify(inputs) }, "POST");
					if (found.length) {
						acknowledged = await confirm_breaks(found);
						if (!acknowledged) return;
					}
				}
				const saved = await p.call(sheets_api + "save", { kind, task_id: name, inputs: JSON.stringify(inputs),
					modified, lang: lang(), acknowledged: acknowledged ? JSON.stringify(acknowledged) : undefined }, "POST");
				modified = saved.task.modified; dirty = false; status = saved.task.status;
				view = saved; inputs = clone(saved.inputs); good = clone(saved.inputs);
				buttons(); paint();
				p.success(t("cowork.recon.saved"));
			}
			// 对账预设：系统定不了、但定一次规矩就能定的地方（如这个账户的多笔对一笔是不是汇总入账），一次问一个，
			// 选了即记住并按它重算（已识别的数据，不再识别）；以后同一账户不再问（用户 2026-09-30 批准）。
			const questions = data.task.questions || [];
			async function answer(item, option, dialog) {
				if (dirty) {
					await save();
					if (dirty) return;
				}
				if (item.rerun) {
					// 答案要用原文件重跑（读总账时就按科目筛了）：跟着后台任务走，完成后打开新结果（识别结果复用，不再扣费）。
					dialog.hide();
					await follow(() => p.call(presets_api + "answer", { kind, task_id: name, rule: item.rule, value: option.value,
						lang: lang() }, "POST"));
					return;
				}
				const fresh = await p.call(presets_api + "answer", { kind, task_id: name, rule: item.rule, value: option.value,
					subject: item.subject || "", lang: lang() }, "POST");
				dialog.hide();
				if (frappe.get_route()[1] === name) await show_result(name, fresh, true);
			}
			function ask() {
				const item = questions[0];
				const dialog = new frappe.ui.Dialog({ title: item.title, fields: [{ fieldtype: "HTML", fieldname: "body" }] });
				const body = dialog.fields_dict.body.$wrapper[0];
				const evidence = document.createElement("table");
				evidence.className = "table table-sm pearnly-recon-evidence";
				for (const cells of item.evidence) {
					const line = evidence.insertRow();
					for (const text of cells) line.insertCell().textContent = text;
				}
				const count = document.createElement("p");
				count.className = "text-muted small";
				count.textContent = item.count || "";
				const choices = document.createElement("div");
				choices.className = "pearnly-recon-choices";
				for (const option of item.options) {
					const button = document.createElement("button");
					button.type = "button";
					button.className = "btn btn-default btn-block";
					button.textContent = option.label;
					button.addEventListener("click", async () => {
						choices.querySelectorAll("button").forEach(node => { node.disabled = true; });
						try { await answer(item, option, dialog); }
						finally { choices.querySelectorAll("button").forEach(node => { node.disabled = false; }); }
					});
					choices.append(button);
				}
				body.replaceChildren(...(item.evidence.length ? [evidence] : []), count, choices);
				dialog.show();
			}
			if (questions.length) {
				page.add_inner_button(t("cowork.recon.questions", questions.length), ask);
				// 刚答完一个、还有下一个：接着问，不用再点。
				if (ask_next) ask();
			}
			buttons();
			paint();
		}

		async function show_route() {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			++sequence;
			clearTimeout(ticker);
			clear();
			current = null;
			if (!route[1]) show_list();
			else if (route[1] === "new") show_new();
			else if (route[1] === "running") show_running();
			else if (route[1] === "run" && route[2]) await show_progress(route[2]);
			else { current = route[1]; await show_result(current); }
		}

		wrapper.on_page_show = () => { p.restore_sidebar(); void show_route(); };
		$(wrapper).on("hide", () => { ++sequence; clearTimeout(ticker); });
		await show_route();
	};
})();
