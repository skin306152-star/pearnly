/* Page-level purchase form. All controls, upload, gallery and item sections are native. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui;
	u.editor = async ({page, doc, defaults, editable, amended_from, intake, still_current, reload, go}) => {
		// Historical prototypes stored WHT only on the header. Keep its cents when
		// continuing a draft or amending it through the line-based legacy contract.
		if (editable && doc.calculation_version === 1 && Number(doc.wht_amount) > 0 && !doc.lines?.some(row => Number(row.wht_rate))) {
			doc = {...doc, lines: doc.lines.map(row => ({...row}))};
			const taxable = doc.lines.filter(row => Number(row.line_total) > 0);
			const subtotal = taxable.reduce((sum, row) => sum + Number(row.line_total), 0);
			let remaining = Math.round(Number(doc.wht_amount) * 100);
			taxable.forEach((row, index) => {
				const cents = index === taxable.length - 1 ? remaining : Math.round(Number(doc.wht_amount) * 100 * Number(row.line_total) / subtotal);
				row.wht_rate = cents / Number(row.line_total); remaining -= cents;
			});
		}
		const state = {page, doc, defaults, editable, intake, amended_from, can_write: frappe.model.can_write("Purchase Document"),
			attachments: (doc.attachments || []).map(row => ({...row})), request_id: frappe.utils.get_random(32),
			form: null, lines: [], ready: false, busy: false, disposed: false, revision: 0};
		const changes = frappe.utils.debounce(async () => {
			if (!state.ready || !editable || state.disposed) return;
			const form = state.form, values = state.values(true);
			u.set(form, "doc_no", {reqd: Boolean(values.has_vat)});
			u.set(form, "supplier_branch_no", {reqd: values.supplier_branch_type === "branch"});
			const revision = ++state.revision;
			const rows = state.lines.filter(line => line.form.get_value("description") || line.form.get_value("product"));
			const lines = u.clean_lines(values.lines);
			u.set(form, "merge_lines", {hidden: lines.length < 2 && !state.split_lines, label: state.split_lines ? __("Split") : __("Merge Items")});
			if (!lines.length) {await form.set_values({subtotal: 0, vat_amount: 0, wht_amount: 0, grand_total: 0, net_payable: 0}); return;}
			if (lines.some(row => !(Number(row.qty) > 0))) return;
			const result = await u.call("api_purchase_amounts", {payload: {...values, lines}}, true);
			if (!still_current() || state.disposed || revision !== state.revision) return;
			for (let i = 0; i < lines.length; i++) {
				await rows[i].form.set_values({line_total: result.lines[i].line_total,
					vat_amount: result.lines[i].vat_amount, wht_amount: result.lines[i].wht_amount});
			}
			await form.set_values(Object.fromEntries(["subtotal", "vat_amount", "wht_amount", "grand_total", "net_payable"]
				.map(key => [key, result[key]])));
		}, 400);
		state.form = p.layout(page.main, u.fields(state, changes));
		const form = state.form;
		state.party_choice = p.bind_entry_choice(form, {kind: "supplier", context: "purchase", reference: "supplier", title: "supplier_name", editable,
			on_select: row => form.set_values({supplier_tax_id: row.tax_id, supplier_address: row.address,
				supplier_branch_type: row.branch_type === "head" ? "head_office" : row.branch_type || "none",
				supplier_branch_no: row.branch_no, supplier_branch_name: row.branch_name}),
			on_clear: () => form.set_values({supplier_tax_id: "", supplier_address: "", supplier_branch_type: "none",
				supplier_branch_no: "", supplier_branch_name: ""})});
		const layout = u.continuous_layout(form, page);
		const cleanup_entry = u.entry_method(state, layout);
		state.sync_entry();
		// Internal provenance and automatic posting options remain in the contract,
		// not as merchant-facing switches or framework fields.
		state.values = (ignore_errors = false) => {
			const values = form.get_values(ignore_errors, !ignore_errors); if (!values) return null;
			const lines = [];
			for (const line of state.lines) {
				if (!line.form.get_value("description") && !line.form.get_value("product")) continue;
				const row = line.form.get_values(ignore_errors, !ignore_errors);
				if (!row) {line.section.collapse(false); layout.navigate("items"); return null;}
				lines.push(row);
			}
			return {...Object.fromEntries(["source", "requester", "requester_user", "external_ref", "image_sha256"]
				.filter(key => doc[key] !== undefined).map(key => [key, doc[key]])),
				auto_create_product: doc.auto_create_product ?? 1, update_stock: doc.update_stock ?? 1, source: state.source || doc.source || "web", ...values, lines};
		};
		const label_lines = () => state.lines.forEach((line, i) => {
			// Preserve the framework's collapse indicator/listeners while changing text.
			line.section.head.contents().filter((_, node) => node.nodeType === Node.TEXT_NODE).first().replaceWith(document.createTextNode(`${__("Item")} ${i + 1} `));
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
			line.choice = p.bind_entry_choice(line.form, {kind: "product", context: "purchase", reference: "product", title: "description", editable,
				on_select: async item => {
					await line.form.set_values({description: item.product_name, code: item.code, barcode: item.barcode,
					unit: item.base_unit || item.unit, unit_price: item.default_cost || 0,
					vat_rate: item.vat_applicable ? defaults.settings.default_vat_rate : 0,
					warehouse: frappe.boot.pearnly_warehouse || "", location: item.default_location || ""});
					line.form.set_df_property("batch_no", "hidden", !item.track_batch);
					line.form.set_df_property("expiry_date", "hidden", !item.track_expiry);
				}, on_clear: () => line.form.set_values({code: "", barcode: ""})});
			line.form.sections.forEach(section => section.wrapper.addClass("px-0"));
			line.form.sections.forEach(section => section.columns.forEach(column => column.wrapper.addClass("px-0 px-sm-3")));
			line.form.sections[0].body.addClass("mt-0 pt-0");
			state.lines.push(line);
			await line.form.set_values({item_type: "goods", qty: 1, unit_price: 0, vat_rate: defaults.settings.default_vat_rate, wht_rate: 0, ...values});
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
		const today = frappe.datetime.get_today();
		await form.set_values({system_no: defaults.system_no, doc_kind: "purchase_invoice",
			doc_date: today, due_date: frappe.datetime.add_days(today, defaults.settings.default_due_days || 0),
			supplier_branch_type: "none", currency: defaults.settings.base_currency || "THB", fx_rate: 1,
			has_vat: 1, price_mode: "exclusive", is_paid: 1, update_stock: 1, auto_create_product: 1,
			payment_method: "cash", discount_total: 0, rounding: 0, amount_override: 0, source: "web",
			...Object.fromEntries(Object.entries(doc).filter(([key]) => key !== "lines")), stock_posted_at: p.datetime_text(doc.stock_posted_at),
			outstanding: Number(doc.net_payable || 0) - Number(doc.paid_amount || 0)});
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
			gallery.$gallery.find("a.attachment-gallery-file").on("click.purchase", event => {
				event.preventDefault();
				show_preview(state.attachments.find(row => row.file_url === event.currentTarget.getAttribute("href")));
			});
			show_preview(state.attachments.find(row => row.file === state.preview_file) || state.attachments[0]);
			gallery.$wrapper.toggle(state.attachments.length > 1 || state.attachments.some(row => !frappe.utils.is_image_file(row.file_url)));
			attachment_list?.destroy();
			if (!state.attachments.length) {form.fields_dict.attachment_list.$wrapper.empty(); return;}
			attachment_list = p.list({page, container: form.fields_dict.attachment_list.$wrapper, compact_columns: true,
				// 这是表单里的附件行（每行自带预览/删除），不是可以批量操作的集合列表 —— 明确关掉勾选框。
				selectable: false,
				doctype: "File", load: async () => ({items: state.attachments.map(row => ({...row, name: row.file})), total: state.attachments.length}),
				on_open: row => show_preview(row),
				columns: [{fieldname: "file_name", label: __("File Name"), width: 150, min_width: 100},
					{fieldname: "kind", label: __("Attachment Type"), width: 80, min_width: 56, format: u.label}],
				row_actions: row => !state.can_write || doc.status === "void" || row.generated || row.intake || amended_from ? [] : [{id: "delete", label: __("Delete"),
					action: () => frappe.confirm(__("Are you sure you want to delete the attachment?"), async () => {
						if (doc.name) await u.call("api_remove_purchase_attachment", {name: doc.name, file: row.file}, true);
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
				values.attachments = state.attachments.filter(row => !row.intake && (!amended_from || !(doc.attachments || []).some(old => old.file === row.file)));
				const result = intake ? await p.call("pearnly_erp_portal.integrations.api.api_confirm_line_intake", {name: intake.name, ...values}, "POST")
					: await u.call("api_post_purchase", {payload: values, request_id: state.request_id,
					name: amended_from ? undefined : doc.name, amended_from}, true);
				p.success(__("Submitted")); await go(result.name);
			}));
			state.recognise = () => act(async () => {
				state.ocr_failed = false;
				frappe.dom.freeze(__("Recognizing..."));
				try {
					const result = await u.call("api_recognise_purchase", {attachments: state.attachments.filter(row => !row.generated), intake_name: intake?.name, document_name: doc.name}, true);
					if (!still_current() || state.disposed) return;
					const allowed = Object.keys(form.fields_dict).filter(key => !form.fields_dict[key].df.read_only
						&& !["HTML", "Button", "Section Break", "Column Break", "Tab Break"].includes(form.fields_dict[key].df.fieldtype));
					await form.set_values(Object.fromEntries(Object.entries(result).filter(([key]) => allowed.includes(key))));
					await form.set_value("supplier", "");
					await state.party_choice.show();
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
					await state.set_lines([{item_type: "goods", description: first.description, qty: 1, unit_price: Number(total.toFixed(2)),
						discount: 0, vat_rate: first.vat_rate, wht_rate: 0, category: first.category, subcategory: first.subcategory}]);
				}
				changes();
			};
		} else if (doc.status === "submitted" && state.can_write) {
			// 付清之后不再给付款入口（用户 2026-09-23：「已付不可更改」）。
			if (doc.payment_status !== "paid") page.set_primary_action(__("Payment"), () => u.payment(doc, reload));
			page.add_menu_item(__("Amend"), () => go("amend", doc.name));
			page.add_menu_item(__("Cancel"), () => p.dialog(__("Cancel"), [u.field("reason", "Reason for Cancellation", "Small Text", {reqd: 1})],
				__("Confirm"), async (values, dialog) => {
					await u.call("api_cancel_purchase", {name: doc.name, ...values}, true);
					dialog.hide(); p.success(__("Cancelled"), "cancel"); await reload();
				}));
			for (const kind of ["substitute_receipt", ...(Number(doc.wht_amount) > 0 ? ["wht_cert"] : [])]) {
				page.add_menu_item(u.label(kind), () => act(async () => {
					const file = await u.call("api_purchase_voucher", {name: doc.name, kind}, true);
					if (!state.attachments.some(row => row.file === file.file)) state.attachments.push({...file, kind, generated: 1,
						file_name: decodeURIComponent(file.file_url.split("/").pop())});
					state.preview_file = file.file; await refresh_files();
				}));
			}
		}
		await refresh_files();
		state.ready = true; state.sync_entry();
		if (editable) changes();
		return () => {state.disposed = true; cleanup_entry(); preview.destroy(); layout.destroy(); attachment_list?.destroy();};
	};
	u.payment = (doc, reload) => {
		// 与销售同一套弹窗（用户 2026-09-23）：只问这次付多少、什么方式、哪天（+备注）。
		// 不再给「未付 / 已付款」下拉 —— 那个下拉会挡在「提交」上面、也可能静默改状态。
		// 采购这边金额是**这次付出去的钱**（会累加进付款流水），默认值给还欠的数。
		const f = u.field, due = Number(doc.net_payable) - Number(doc.paid_amount), request_id = frappe.utils.get_random(32);
		p.dialog(__("Payment"), [
			f("amount", "Paid Amount", "Currency", {reqd: 1, default: due,
				description: `${__("Outstanding Amount")} ${p.money(due, doc.currency)}`}),
			f("method", "Mode of Payment", "Select", {options: u.options(["cash", "transfer", "promptpay", "bank", "cheque", "other"]), default: "cash"}),
			f("date", "Posting Date", "Date", {default: frappe.datetime.get_today(), reqd: 1}), f("note", "Remarks", "Small Text"),
		], __("Submit"), async (values, dialog) => {
			await u.call("api_purchase_payment", {name: doc.name, payload: {...values, request_id}}, true);
			dialog.hide();
			// 主按动作要有回音：提示 + 提示音（与其他页面同一套 `p.success`）。
			// 采购这边金额是**这次付出去的钱**（会累加进付款流水），与销售那边口径不同。
			p.success(__("Paid {0}", [p.money(values.amount)]));
			await reload();
		});
	};
})();
