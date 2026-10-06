/* Cowork 录入工作台：待办队列（原生列表）+ 新建录入入口。
 *
 * 列表这一层照 ERP 各列表页同一套：原生 Page / BaseList / ListView，
 *   · 勾选框照官方保留；没有「确认」这一步：识别后人工自然复核、直接提交推送（用户 2026-09-26）；
 *   · 列头点一下排序（`sort_fields` 声明服务端认的字段，排序回服务端算）；
 *   · 筛选是官方那颗 Filter 按钮 + 弹层里的条件行（`filter_fields` 声明本页支持哪几个
 *     字段，名称与选项都取自 DocType 自己）；
 *   · 点一行进复核页（用户 2026-09-26：不要「复核」按钮与详情弹窗）；状态列按推送结果着色。
 * 文案不另起一套：列名、状态、动作都取产品已有的术语（`intake.*`、DocType 标签、
 * 与产品已有的术语）。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.history.api.";
	p.intake = async (page, t, wrapper) => {
		const root = "pearnly-intake";
		const can_write = frappe.model.can_write("Cowork OCR History");
		const go = (...parts) => frappe.set_route(root, ...parts);
		let cleanup = () => {};

		const create = () => {
			// 四组选择都在弹窗里选完（见 `intake-choices.js`），二级页面只做上传与复核。
			void p.intake_create(t, choice => {
				p.intake_choice = choice;
				void go("new");
			});
		};

		// 状态＝最近一次推送的结果，颜色照旧线上 `erp-log-card.ts`：录入中（推送未完成）黄、成功绿、失败红
		// （悬浮看原因）。用官方的状态药丸。
		const PUSH_STATUS = {
			pending: ["yellow", () => t("intake.push.pending")],
			success: ["green", () => __("connect.records.success")],
			// 推送只有三种：录入中 / 成功 / 失败（用户 2026-09-26）；转人工就是失败，原因悬浮看。
			manual: ["red", () => __("connect.records.failed")],
		};
		// 共用列表的 `format` 只出文字，颜色交给 `cell_class`（官方 indicator-pill 的颜色类）。
		// 还没进推送账本就失败（目标不可用等）的票没有推送状态、只有原因：显示「失败」（用户 2026-09-26）。
		// 「仅识别」的票（`intake_target` 为 `none`，识别后导出、不推送）：蓝色「已识别」，表示识别完成、可以导出；
		// 导出不另记状态。识别后的需复核原因不在列表显示（用户 2026-10-02），服务端照样算、作内部质量信号。
		const push_status = (status, row) => (status ? PUSH_STATUS[status] || ["red", () => __("connect.records.failed")]
			: row?.last_push_reason ? ["red", () => __("connect.records.failed")]
			: row?.intake_target === p.intake_targets?.RECOGNIZE_ONLY ? ["blue", () => t("intake.status.recognized")]
			: null);
		const status_title = (value, row) => (value || row?.last_push_reason
			? (p.intake_push_reason ? p.intake_push_reason(row?.last_push_reason) : row?.last_push_reason || "")
			: "");

		async function listing(still_current) {
			page.set_title(t("intake.title"));
			p.breadcrumbs({label: t("intake.title"), route: p.subroute(root)});
			let list;
			// 新平台以批量识别和推送为主，识别后人工自然复核（用户 2026-09-26）：列表不再有「确认」
			// （把草稿标成已完成）这一步，也就没有「草稿」筛选。勾选后右上角换成「删除」（ERP 列表同一套
			// `p.selection_action`）：能删的删，已推送的服务端跳过；没勾选时是新建录入。
			const restore_new = () => (can_write ? page.set_primary_action(t("intake.action.new"), create)
				: page.clear_primary_action());
			const selection = can_write ? p.selection_action({
				page, list: () => list, empty: restore_new,
				label: () => __("Delete"),
				confirm: rows => t("intake.confirm.delete", rows.length),
				run: rows => p.call(API + "delete", {records: rows.map(row => row.name)}, "POST"),
				success: () => __("Deleted"),
			}) : restore_new;
			// 官方筛选条件 → 接口参数。条件全在筛选区里（见下面 `add_filters`），
			// 这里只做翻译，不再偷偷默认什么。
			// 推送状态（2026-10-07）：页面表单里的一个原生下拉，与状态列同一口径；从自动录入规则页「查看待处理」
			// 进来时预选「失败」（`frappe.route_options`，用一次就清）。
			const preset = frappe.route_options?.push_state || "";
			frappe.route_options = null;
			const push_filter = page.add_field({fieldtype: "Select", fieldname: "push_state", label: t("intake.col.status"),
				options: [{value: "", label: __("All")}, {value: "failed", label: __("connect.records.failed")},
					{value: "pending", label: t("intake.push.pending")}, {value: "success", label: __("connect.records.success")}],
				default: preset, change: () => void list?.reload()});
			if (preset) push_filter.set_value(preset);
			const parameters = () => {
				const picked = p.filter_values(list);
				const [from, to] = picked.invoice_date || [];
				return {
					posting_kind: picked.posting_kind || undefined,
					date_from: from || undefined,
					date_to: to || undefined,
					push_state: push_filter.get_value() || undefined,
				};
			};
			list = p.list({
				// 勾选框照官方列表保留（用户 2026-09-26）。
				page, doctype: "Cowork OCR History", compact_columns: true,
				on_selection: () => selection(),
				// 本页支持的筛选条件：名称与选项都取自 DocType（不再自己造词）。
				filter_fields: [
					{fieldname: "posting_kind", label: __("Posting Kind"), fieldtype: "Select"},
					{fieldname: "invoice_date", label: __("Invoice Date"), fieldtype: "Date"},
				],
				// 列照用户 2026-09-26：单据号、日期、卖方与税号、买方与税号、价税合计、状态（不要系统单号）。
				columns: [
					{fieldname: "invoice_no", width: 130, min_width: 100, label: t("intake.field.docNo"),
						format: value => p.escape(value || "")},
					{fieldname: "invoice_date", width: 100, min_width: 88, label: t("intake.col.date")},
					{fieldname: "seller_name", width: 170, min_width: 110, label: t("intake.field.seller"),
						format: value => p.escape(value || "")},
					{fieldname: "seller_tax", width: 130, min_width: 110, label: t("intake.field.sellerTax"),
						format: value => p.escape(value || "")},
					{fieldname: "buyer_name", width: 170, min_width: 110, label: t("intake.field.buyer"),
						format: value => p.escape(value || "")},
					{fieldname: "buyer_tax", width: 130, min_width: 110, label: t("intake.field.buyerTax"),
						format: value => p.escape(value || "")},
					{fieldname: "total_amount", width: 110, min_width: 90, label: t("intake.col.grandTotal"),
						format: value => p.money(value)},
					{fieldname: "last_push_status", width: 110, min_width: 90, label: t("intake.col.status"),
						format: (value, row) => push_status(value, row)?.[1]() || "",
						cell_class: (value, row) => (push_status(value, row) ? `indicator-pill ${push_status(value, row)[0]}` : ""),
						// 失败原因只放在状态图标上，鼠标悬浮看（用户 2026-09-26）。
						title: status_title},
				],
				// 列头点一下排序：服务端分页，所以排序参数回服务端（见 `workbench`）。
				// 单号是系统随机号，按它排看不出顺序，不给排。
				sort_fields: ["invoice_no", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
					"total_amount", "modified"],
				load: ({start, page_length, sort_by, sort_order}) => p.call(API + "workbench",
					{...parameters(), start, limit: page_length, sort_by, sort_order}),
				// 点一行就进复核页（看原件、改字段、提交 / 导出）；不另摆「复核」按钮。
				on_open: row => void go("new", row.name),
				empty_action: can_write ? {label: t("intake.action.new"), action: create} : null,
			});
			selection();
			p.add_refresh(page, () => list.reload());
			await list.ready;
			// 提交后票是「录入中」，推送在后台进行。推送状态一变、本人的识别一读完，服务端都推实时事件（`cowork_intake_changed`、
			// `pearnly_ocr_work`），收到就重拉；列表里还有录入中的每 5 秒兜底一次，没有就 60 秒（`p.live`；原来有录入中的就每 4 秒
			// 问一次，2026-10-01 压测窗口）。勾着行、开着弹窗时不打断，下一次再刷。
			const live = p.live({
				refresh: async () => {
					if (cur_dialog || list.get_checked_items?.().length) return;
					await list.reload();
				},
				events: ["cowork_intake_changed", "pearnly_ocr_work"],
				// 推送状态变了（带 history）、识别有了结论（带 status）才重拉；每读完一页的进度推送（只带 pages_done）不刷
				accept: data => data?.history !== undefined || (data?.kind === "cowork.invoice" && Boolean(data?.status)),
				// 快刷（5 秒）只为本人刚推的：同事的、等离线小助手挂了很久的「录入中」不让所有打开列表的人每 5 秒刷一次
				// （独立复查 2026-10-01），它们照样随实时事件 / 60 秒兜底更新。
				busy: () => (list.data || []).some(row => row.last_push_status === "pending" && row.owner === frappe.session.user
					&& (!row.last_pushed_at || Date.now() - new Date(String(row.last_pushed_at).replace(" ", "T")).getTime() < 15 * 60e3)),
				still_current,
			});
			cleanup = () => {live.stop(); list.destroy(); cleanup = () => {};};
		}

		const show = p.routed_page({wrapper, root, render: async (rest, still_current) => {
			cleanup();
			p.clear_page(page);
			if (!rest.length) {await listing(still_current); return;}
			if (rest[0] === "new") {
				cleanup = await p.intake_new({page, t, go, still_current, name: rest[1] || ""});
				return;
			}
			// 自动录入规则（`intake-folders.js`，2026-10-07）：规则页出错只回列表，不影响录入工作台。
			if (rest[0] === "folders" && p.intake_folders) {
				try {
					cleanup = (await p.intake_folders({page, t, go, still_current})) || (() => {});
				} catch (error) {
					console.error(error);
					cleanup = () => {};
					await go();
				}
				return;
			}
			// 认不出的层级（旧书签、手输）收敛回列表，不停在空白页。
			await go();
		}});
		await show();
	};
})();
