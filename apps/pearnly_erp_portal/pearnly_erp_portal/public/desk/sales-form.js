/* Page-level sales form. All controls, upload, gallery and item sections are native. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui;
	u.editor = async ({page, doc, defaults, editable, intake, still_current, reload, go}) => {
		// `intake`：从 LINE 卡片进来、已经识别完 —— 不再问「上传识别 / 手动录入」，直接显示字段。
		const state = {page, doc, defaults, editable, intake, can_write: frappe.model.can_write("ERP Sales Document"),
			// 票面读出的买方税号 / 分店（LINE 卡片打开时随单据带来，网页识别时更新）：提交时服务端按它认客户
			ocr_buyer: doc.buyer_tax_id ? {buyer_tax_id: doc.buyer_tax_id, buyer_branch_no: doc.buyer_branch_no || ""} : {},
			attachments: (doc.attachments || []).map(row => ({...row})), request_id: frappe.utils.get_random(32),
			form: null, lines: [], ready: false, busy: false, disposed: false, revision: 0};
		const changes = frappe.utils.debounce(async () => {
			if (!state.ready || !editable || state.disposed) return;
			const form = state.form, values = state.values(true);
			const revision = ++state.revision;
			const rows = state.lines.filter(line => line.form.get_value("description") || line.form.get_value("product"));
			const lines = u.clean_lines(values.lines);
			u.set(form, "merge_lines", {hidden: lines.length < 2 && !state.split_lines, label: state.split_lines ? __("Split") : __("Merge Items")});
			if (!lines.length) {await form.set_values({subtotal: 0, discount_total: 0, vat_amount: 0, grand_total: 0}); return;}
			if (lines.some(row => !(Number(row.qty) > 0))) return;
			const result = await u.call("api_sales_amounts", {payload: {...values, lines}}, true);
			if (!still_current() || state.disposed || revision !== state.revision) return;
			for (let i = 0; i < lines.length; i++) {
				await rows[i].form.set_values({line_total: result.lines[i].line_total});
			}
			await form.set_values(Object.fromEntries(["subtotal", "discount_total", "vat_amount", "grand_total"]
				.map(key => [key, result[key]])));
		}, 400);
		state.form = p.layout(page.main, u.fields(state, changes));
		const form = state.form;
		state.party_choice = p.bind_entry_choice(form, {kind: "customer", context: "sales", reference: "customer", title: "buyer_name", editable,
			company: () => doc.company || frappe.boot.pearnly_company,
			on_select: row => form.set_values({party_type: row.party_type, address: row.address}),
			on_clear: () => form.set_values({party_type: "anonymous", address: ""})});
		const layout = u.continuous_layout(form, page);
		const cleanup_entry = u.entry_method(state, layout);
		state.sync_entry();
		state.values = (ignore_errors = false) => {
			const values = form.get_values(ignore_errors, !ignore_errors); if (!values) return null;
			const lines = [];
			for (const line of state.lines) {
				if (!line.form.get_value("description") && !line.form.get_value("product")) continue;
				const row = line.form.get_values(ignore_errors, !ignore_errors);
				if (!row) {line.section.collapse(false); layout.navigate("items"); return null;}
				lines.push(row);
			}
			// 内部来源与留底字段留在契约里，不作为商家可见的开关。
			return {...Object.fromEntries(["source", "source_file", "image_sha256"]
				.filter(key => doc[key] !== undefined).map(key => [key, doc[key]])),
				source: state.source || doc.source || "web", ...state.ocr_buyer, ...values, lines};
		};
		const label_lines = () => state.lines.forEach((line, i) => {
			// Preserve the framework's collapse indicator/listeners while changing text.
			line.section.head.contents().filter((_, node) => node.nodeType === Node.TEXT_NODE).first()
				.replaceWith(document.createTextNode(`${__("Item")} ${i + 1} `));
		});
		state.add_line = async (values = {}, collapse_previous = true) => {
			if (collapse_previous) state.lines.forEach(line => line.section.collapse(true));
			const wrapper = $("<div>").appendTo(form.fields_dict.lines.$wrapper);
			const outer = p.layout(wrapper, [u.section(`${__("Item")} ${state.lines.length + 1}`, {collapsible: 1}), u.field("item", "Item", "HTML")]);
			// Native spacing utilities prevent nested business sections from consuming
			// three sets of gutters on a phone; controls keep their upstream geometry.
			outer.sections[0].wrapper.addClass("px-0");
			outer.sections[0].columns.forEach(column => column.wrapper.addClass("px-0"));
			const line = {wrapper, section: outer.sections[0], ready: false};
			line.form = p.layout(outer.fields_dict.item.$wrapper, u.line_fields(state, line, changes));
			line.choice = p.bind_entry_choice(line.form, {kind: "product", context: "sales", reference: "product", title: "description", editable,
				company: () => doc.company || frappe.boot.pearnly_company,
				on_select: async item => {
					// 单价只补空：识别读出 / 已填的单价不被商品默认价盖掉
					await line.form.set_values({description: item.product_name, unit: item.base_unit || item.unit,
					unit_price: Number(line.form.get_value("unit_price")) || (item.price_set ? item.unit_price : 0),
					vat_applicable: item.vat_applicable ? 1 : 0,
					warehouse: frappe.boot.pearnly_warehouse || "", location: item.default_location || ""});
				}, on_clear: () => Promise.resolve()});
			line.form.sections.forEach(section => section.wrapper.addClass("px-0"));
			line.form.sections.forEach(section => section.columns.forEach(column => column.wrapper.addClass("px-0 px-sm-3")));
			line.form.sections[0].body.addClass("mt-0 pt-0");
			state.lines.push(line);
			await line.form.set_values({qty: 1, unit_price: 0, discount: 0, vat_applicable: 1, ...values});
			line.form.sections.filter(section => section.df.collapsed).forEach(section => section.collapse(true));
			await line.choice.show();
			line.ready = true; line.section.collapse(false); changes();
			return line;
		};
		state.remove_line = line => {
			++state.revision;
			line.wrapper.remove(); state.lines = state.lines.filter(item => item !== line); label_lines(); changes();
		};
		state.set_lines = async values => {
			++state.revision;
			form.fields_dict.lines.$wrapper.empty(); state.lines = [];
			for (const row of values) await state.add_line(row, false);
			state.lines.forEach((line, i) => line.section.collapse(i > 0));
		};
		await form.set_values({system_no: defaults.system_no, doc_kind: "receipt", doc_date: frappe.datetime.get_today(),
			party_type: "person", has_vat: 1, price_includes_vat: 0, vat_rate: defaults.default_vat_rate || 7, is_paid: 1,
			payment_method: "cash", payment_date: frappe.datetime.get_today(), header_discount: 0,
			source: "web", ...Object.fromEntries(Object.entries(doc).filter(([key]) => key !== "lines")),
			stock_posted_at: p.datetime_text(doc.stock_posted_at)});
		await state.party_choice.show();
		await state.set_lines(doc.lines?.length ? doc.lines : [{}]);
		form.sections.filter(section => section.df.collapsed).forEach(section => section.collapse(true));
		let attachment_list;
		const gallery = form.fields_dict.gallery;
		// Supply the native gallery's documented form context with the pending/posted
		// evidence collection. The gallery itself remains upstream, including thumbnails.
		gallery.frm = {doc: {__islocal: false}, get_files: () => state.attachments.map(row => ({...row, name: row.file})),
			attachments: {get_file_url: row => row.file_url, can_delete_attachment: () => false}, has_perm: () => false};
		const preview = u.document_viewer(form.fields_dict.document_preview.$wrapper);
		const show_preview = row => {
			state.preview_file = row?.file;
			void preview.show(row);
		};
		gallery.open_lightbox = (images, index) => show_preview(state.attachments.find(row => row.file === images[index]?.name));
		const refresh_files = async () => {
			if (state.disposed) return;
			await gallery.refresh_input();
			gallery.$gallery.find("a.attachment-gallery-file").on("click.sales", event => {
				event.preventDefault();
				show_preview(state.attachments.find(row => row.file_url === event.currentTarget.getAttribute("href")));
			});
			show_preview(state.attachments.find(row => row.file === state.preview_file) || state.attachments[0]);
			gallery.$wrapper.toggle(state.attachments.length > 1 || state.attachments.some(row => !frappe.utils.is_image_file(row.file_name || row.file_url)));
			attachment_list?.destroy();
			if (!state.attachments.length) {form.fields_dict.attachment_list.$wrapper.empty(); return;}
			attachment_list = p.list({page, container: form.fields_dict.attachment_list.$wrapper, compact_columns: true,
				// 这是表单里的附件行（每行自带预览/删除），不是可以批量操作的集合列表 —— 明确关掉勾选框。
				selectable: false,
				doctype: "File", load: async () => ({items: state.attachments.map(row => ({...row, name: row.file})), total: state.attachments.length}),
				on_open: row => show_preview(row),
				columns: [{fieldname: "file_name", label: __("File Name"), width: 150, min_width: 100},
					{fieldname: "kind", label: __("Attachment Type"), width: 80, min_width: 56, format: u.label}],
				row_actions: row => !state.can_write || doc.status === "void" || row.intake ? [] : [{id: "delete", label: __("Delete"),
					action: () => frappe.confirm(__("Are you sure you want to delete the attachment?"), async () => {
						if (doc.name) await u.call("api_remove_sale_attachment", {name: doc.name, file: row.file}, true);
						else await frappe.call({method: "frappe.client.delete", args: {doctype: "File", name: row.file}, type: "POST"});
						// 删掉的是附件，给的是官方那声 `delete`（不是"保存成功"那声）。
						frappe.utils.play_sound("delete");
						state.attachments = state.attachments.filter(item => item.file !== row.file);
						await refresh_files();
					})}],
			});
			await attachment_list.ready;
			// The evidence collection is already loaded in full; no page-size chooser.
			attachment_list.$paging_area.hide();
		};
		state.upload = () => u.upload_evidence(state, refresh_files);
		const act = async callback => {
			if (state.busy) return;
			state.busy = true; state.sync_entry();
			try { await callback(); } finally { state.busy = false; if (!state.disposed) state.sync_entry(); }
		};
		if (editable) {
			page.set_primary_action(__("Submit"), () => state.can_submit() && act(async () => {
				await state.party_choice.sync();
				for (const line of state.lines) await line.choice.sync();
				const values = state.values(); if (!values) return;
				values.lines = u.clean_lines(values.lines);
				if (state.ocr_hash) values.image_sha256 = state.ocr_hash;
				// LINE 那张源文件由服务端按卡片补上，这里只交用户另加的附件。
				values.attachments = state.attachments.filter(row => !row.intake);
				// 写进 Express 缺人要定的（同期账套、费用科目、同税号几个档…）提交前就问；关掉就不提交
				if (!(await p.express_preflight("ERP Sales Document", {...values, company: doc.company || undefined}))) return;
				const result = intake ? await p.call("pearnly_erp_portal.integrations.api.api_submit_line_intake", {name: intake, payload: values}, "POST")
					: await u.call("api_post_sale", {payload: values, request_id: state.request_id, name: doc.name || undefined}, true);
				p.success(__("Submitted")); await go(result.name);
			}));
			state.recognise = () => act(async () => {
				state.ocr_failed = false;
				// 白底 + 官方转圈 + 按页进度 + 已用秒数（用户 2026-09-30「白屏加动画」「按页分进度，不要按份」）：
				// 带一个随机进度号发起，识别每读完一页服务端记一次，等候期间按这个号问（`recognition.progress`）。
				const progress_key = frappe.utils.get_random(24);
				let latest = {}; // 后台识别的进度：`p.trade.recognise` 问到就更新，等候层直接读
				const waiting = p.waiting(state.page, __("Recognizing..."), {elapsed: true,
					pages: async () => latest});
				try {
					const result = await p.trade.recognise(u.api, "api_recognise_sales", {attachments: state.attachments, document_name: doc.name, progress_key}, now => {latest = now;});
					if (!still_current() || state.disposed) return;
					const allowed = Object.keys(form.fields_dict).filter(key => !form.fields_dict[key].df.read_only
						&& !["HTML", "Button", "Section Break", "Column Break", "Tab Break", "Table"].includes(form.fields_dict[key].df.fieldtype));
					await form.set_values(Object.fromEntries(Object.entries(result).filter(([key]) => allowed.includes(key))));
					// 客户按读出的买方税号由服务端预选（认不出就空着给人选）；税号 / 分店随提交带上，提交时再按它认或记进新客户
					await form.set_value("customer", result.customer || "");
					await state.party_choice.show();
					state.ocr_buyer = {buyer_tax_id: result.buyer_tax_id || "", buyer_branch_no: result.buyer_branch_no || ""};
					state.source = result.source;
					state.ocr_hash = result.image_sha256;
					if (result.lines) await state.set_lines(result.lines);
					state.ocr_complete = true;
					// 同一文件里还有别的单据：网页一次只填第一张，照实告诉用户（LINE 那边会各出一张卡）。
					if (result.additional?.length) frappe.msgprint(__("This file holds {0} documents; only the first is filled in. Upload the others separately.", [result.additional.length + 1]));
					changes(); state.sync_entry();
					layout.navigate("information");
				} catch (error) {
					state.ocr_failed = true;
					// frappe.call already presents server failures in the native message dialog.
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				}
				finally {waiting.done();}
			});
			state.merge = async () => {
				const rows = u.clean_lines(state.values(true).lines); if (rows.length < 2 && !state.split_lines) return;
				if (state.split_lines) {await state.set_lines(state.split_lines); state.split_lines = null;}
				else {
					state.split_lines = structuredClone(rows);
					const first = rows[0], total = rows.reduce((sum, row) => sum + Math.max(0, Number(row.qty) * Number(row.unit_price) - Number(row.discount || 0)), 0);
					await state.set_lines([{description: first.description, qty: 1, unit_price: Number(total.toFixed(2)),
						discount: 0, vat_applicable: first.vat_applicable, warehouse: first.warehouse, location: first.location}]);
				}
				changes();
			};
		} else if (doc.status === "submitted" && state.can_write) {
			if (frappe.boot.pearnly_express && doc.express_state)
				page.add_inner_button(__("Express"), () => p.express("ERP Sales Document", doc.name, reload));
			// 收满之后不再给收款入口（用户 2026-09-23：「已收不可更改」）。
			if (doc.doc_kind === "receipt" && doc.payment_status !== "paid")
				page.set_primary_action(__("Collection"), () => u.payment(doc, reload));
			page.add_menu_item(__("Cancel"), () => p.dialog(__("Cancel"), [u.field("reason", "Reason for Cancellation", "Small Text", {reqd: 1})],
				__("Confirm"), async (values, dialog) => {
					await u.call("api_cancel_sale", {name: doc.name, ...values}, true);
					dialog.hide(); p.success(__("Cancelled"), "cancel"); await reload();
				}));
		}
		await refresh_files();
		state.ready = true; state.sync_entry();
		if (editable) changes();
		return () => {state.disposed = true; cleanup_entry(); preview.destroy(); layout.destroy(); attachment_list?.destroy();};
	};
	u.payment = (doc, reload) => {
		// 表头口径：钱记在这张单上（`paid_amount` 是**累计已收**，不是这次追加）。
		// 所以弹窗只问三件事，不再给「未付 / 已付款」那种下拉 —— 那个下拉既会让人把
		// 已经收到的钱静默清零，又会挡在「提交」上面（见 docs/work 的 F3）。
		const f = u.field;
		p.dialog(__("Collection"), [
			f("amount", "Received Amount", "Currency", {reqd: 1, default: doc.grand_total,
				description: `${__("Grand Total")} ${p.money(doc.grand_total)}`}),
			f("method", "Collection Method", "Select", {options: u.options(["cash", "transfer", "promptpay", "other"]),
				default: doc.payment_method || "cash"}),
			f("date", "Collection Date", "Date", {default: doc.payment_date || frappe.datetime.get_today(), reqd: 1}),
			// 与采购付款弹窗同一套字段（金额/方式/日期/备注），用户 2026-09-23 口径。
			f("note", "Remarks", "Small Text", {default: doc.payment_note || ""}),
		], __("Submit"), async (values, dialog) => {
			await u.call("api_sale_payment", {name: doc.name, payload: values}, true);
			dialog.hide();
			// 主按动作要有回音：提示 + 提示音（与其他页面同一套 `p.success`）。
			// 金额说的是这张单**累计已收**（表头口径），不是这次追加的部分。
			p.success(__("Collected {0}", [p.money(values.amount)]));
			await reload();
		});
	};
})();
