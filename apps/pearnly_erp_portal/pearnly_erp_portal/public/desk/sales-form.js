/* Page-level sales form. All controls, upload, gallery and item sections are native. */
(() => {
	const p = frappe.pearnly, u = p.sales_ui;
	u.editor = async ({page, doc, defaults, editable, intake, still_current, reload, go}) => {
		const state = {page, doc, defaults, editable, intake, can_write: frappe.model.can_write("ERP Sales Document"),
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
				source: state.source || doc.source || "web", ...values, lines};
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
			line.form.sections.forEach(section => section.wrapper.addClass("px-0"));
			line.form.sections.forEach(section => section.columns.forEach(column => column.wrapper.addClass("px-0 px-sm-3")));
			line.form.sections[0].body.addClass("mt-0 pt-0");
			state.lines.push(line);
			await line.form.set_values({qty: 1, unit_price: 0, discount: 0, vat_applicable: 1, ...values});
			line.form.sections.filter(section => section.df.collapsed).forEach(section => section.collapse(true));
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
			party_type: "person", has_vat: 1, vat_rate: defaults.default_vat_rate || 7, is_paid: 1,
			payment_method: "cash", payment_date: frappe.datetime.get_today(), header_discount: 0,
			source: "web", ...Object.fromEntries(Object.entries(doc).filter(([key]) => key !== "lines")),
			stock_posted_at: p.datetime_text(doc.stock_posted_at)});
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
			gallery.$wrapper.toggle(state.attachments.length > 1 || state.attachments.some(row => !frappe.utils.is_image_file(row.file_url)));
			attachment_list?.destroy();
			if (!state.attachments.length) {form.fields_dict.attachment_list.$wrapper.empty(); return;}
			attachment_list = p.list({page, container: form.fields_dict.attachment_list.$wrapper, compact_columns: true,
				doctype: "File", load: async () => ({items: state.attachments.map(row => ({...row, name: row.file})), total: state.attachments.length}),
				on_open: row => show_preview(row),
				columns: [{fieldname: "file_name", label: __("File Name"), width: 150, min_width: 100},
					{fieldname: "kind", label: __("Attachment Type"), width: 80, min_width: 56, format: u.label}],
				row_actions: row => !state.can_write || doc.status === "void" || row.intake ? [] : [{id: "delete", label: __("Delete"),
					action: () => frappe.confirm(__("Are you sure you want to delete the attachment?"), async () => {
						if (doc.name) await u.call("api_remove_sale_attachment", {name: doc.name, file: row.file}, true);
						else await frappe.call({method: "frappe.client.delete", args: {doctype: "File", name: row.file}, type: "POST"});
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
				const values = state.values(); if (!values) return;
				values.lines = u.clean_lines(values.lines);
				if (state.ocr_hash) values.image_sha256 = state.ocr_hash;
				values.attachments = state.attachments.filter(row => !row.intake);
				const result = intake ? await p.call("pearnly_erp_portal.integrations.api.api_confirm_line_intake", {name: intake.name, ...values}, "POST")
					: await u.call("api_post_sale", {payload: values, request_id: state.request_id, name: doc.name || undefined}, true);
				p.success(__("Submitted")); await go(result.name);
			}));
			state.recognise = () => act(async () => {
				state.ocr_failed = false;
				frappe.dom.freeze(__("Recognizing..."));
				try {
					const result = await u.call("api_recognise_sales", {attachments: state.attachments, intake_name: intake?.name, document_name: doc.name}, true);
					if (!still_current() || state.disposed) return;
					const allowed = Object.keys(form.fields_dict).filter(key => !form.fields_dict[key].df.read_only
						&& !["HTML", "Button", "Section Break", "Column Break", "Tab Break", "Table"].includes(form.fields_dict[key].df.fieldtype));
					await form.set_values(Object.fromEntries(Object.entries(result).filter(([key]) => allowed.includes(key))));
					state.source = result.source;
					state.ocr_hash = result.image_sha256;
					if (result.lines) await state.set_lines(result.lines);
					state.ocr_complete = true;
					changes(); state.sync_entry();
					layout.navigate("information");
				} catch (error) {
					state.ocr_failed = true;
					// frappe.call already presents server failures in the native message dialog.
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				}
				finally {frappe.dom.unfreeze();}
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
			// 收满之后不再给收款入口（用户 2026-09-23：「已收不可更改」）。
			if (doc.doc_kind === "receipt" && doc.payment_status !== "paid")
				page.set_primary_action(__("Collection"), () => u.payment(doc, reload));
			page.add_menu_item(__("Cancel"), () => p.dialog(__("Cancel"), [u.field("reason", "Reason for Cancellation", "Small Text", {reqd: 1})],
				__("Confirm"), async (values, dialog) => {
					await u.call("api_cancel_sale", {name: doc.name, ...values}, true);
					dialog.hide(); p.success(__("Cancelled")); await reload();
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
