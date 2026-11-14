/* 网页「新建」收款（11-14 用户：「网页端也可以新建收款，弹窗编辑相关字段，提交后就写入 exp」）：「销售 → 收款」页头。
 * 原生 Dialog：公司（默认取页面当前公司；本人能用、接了账本的只有一家时不显示，多家才给下拉，只列本人能用的，服务端再判）→ （同期两本时）账套 → 客户（官方 Autocomplete，服务端按编号 / 名字搜、一次一页）→ 这位客户能收的发票
 * （原生表格，填本次收）→ 现金 / 各转账方式（附凭证，官方上传框）/ 折扣 → 提交。弹窗就是复核，提交即放行（`collection.service.submit_web`），
 * 规则与 LINE、CC 同一份：超收、缺凭证、同一张已被收、账套不对都由服务端照码拒，一句话原样显示，什么都不留。 */
(() => {
	const p = frappe.pearnly;
	const amount = value => Number(value || 0);
	// 服务端拒了（超收、缺凭证、没权限…）框架已经弹出那句话、弹窗不关；这里只接住，不让它变成页面上没接住的错误
	const settle = promise => promise.catch(window.pearnlyReported(null, "collection-new"));
	const new_request = () => (crypto?.randomUUID ? crypto.randomUUID()
		: `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 14)}`);

	async function upload_voucher(on_done) {
		const uploader = new frappe.ui.FileUploader({allow_multiple: false, allow_web_link: false, allow_google_drive: false,
			restrictions: {allowed_file_types: ["image/*", ".pdf"]}, make_attachments_public: false, dialog_title: __("Attachments")});
		uploader.upload_files = async () => {
			const item = uploader.uploader.files[0];
			if (!item?.file_obj) return;
			Object.assign(item, {uploading: true, failed: false, error_message: ""});
			try {
				const body = new FormData();
				body.append("file", item.file_obj, item.file_obj.name);
				const response = await fetch("/api/method/pearnly_erp_portal.collection.api.api_collection_voucher_upload", {method: "POST", body,
					credentials: "same-origin", headers: {"X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json"}});
				// 回的不是 JSON（网关错误页等）：记下，按上传失败说
				const payload = await response.json().catch((caughtError) => { globalThis.pearnlyCaught?.(caughtError, "collection-new"); return null; });
				if (!response.ok || !payload || !("message" in payload)) throw {...(payload || {}), status: response.status};
				item.request_succeeded = true;
				on_done({...payload.message, file_name: item.file_obj.name});
				uploader.dialog.hide();
			} catch (error) { globalThis.pearnlyCaught?.(error, "collection-new");
				Object.assign(item, {failed: true, error_message: p.erp_upload_failure(error)});
			} finally {
				item.uploading = false;
			}
		};
	}

	p.collection_new = async ({on_done} = {}) => {
		const request = new_request();  // 这一次打开的号：连点、重发只建一笔
		const first = await p.call("pearnly_erp_portal.collection.api.api_collection_new_options", {}, "POST");
		const companies = first.companies || [];
		let methods = null, vouchers = {}, open = [];
		const narrow = frappe.is_mobile();
		const current = companies.find(row => row.value === frappe.boot.pearnly_company)?.value
			|| (companies.length === 1 ? companies[0].value : "");
		const dialog = new frappe.ui.Dialog({title: __("New"), size: "large", fields: [
			{fieldtype: "Select", fieldname: "company", label: __("Company"), reqd: 1, hidden: companies.length <= 1 ? 1 : 0,
				options: [{value: "", label: ""}, ...companies], change: () => void settle(pick_company())},
			{fieldtype: "Select", fieldname: "book", label: __("Account Set"), hidden: 1, change: () => void settle(pick_book())},
			{fieldtype: "Column Break"},
			{fieldtype: "Autocomplete", fieldname: "customer", label: __("Customer"), reqd: 1, ignore_validation: true,
				get_query: () => ({query: "pearnly_erp_portal.collection.api.api_collection_customer_search",
					params: {company: dialog.get_value("company") || ""}}),
				change: () => void settle(pick_customer())},
			{fieldtype: "Section Break", label: __("Invoices")},
			{fieldtype: "Table", fieldname: "invoices", cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
				data: [], fields: [
					// 原生表格的列宽（`columns`，一行合计 10）：手机宽度不放日期，单号、未收、本次收各占得下（11-14 真机：四列都被截断）
					{fieldtype: "Data", fieldname: "docnum", label: __("Invoice No"), in_list_view: 1, read_only: 1,
						columns: narrow ? 4 : 3},
					{fieldtype: "Date", fieldname: "doc_date", label: __("Invoice Date"), in_list_view: narrow ? 0 : 1, read_only: 1,
						columns: 2},
					{fieldtype: "Currency", fieldname: "available", label: __("Outstanding Amount"), in_list_view: 1, read_only: 1,
						columns: 3},
					{fieldtype: "Currency", fieldname: "amount", label: __("Amount"), in_list_view: 1, columns: narrow ? 3 : 2},
				]},
			{fieldtype: "Section Break", fieldname: "methods_section", label: __("Collection Method")},
			{fieldtype: "HTML", fieldname: "methods_area"},
		], primary_action_label: __("Submit"), primary_action: values => void settle(submit(values))});
		dialog.$wrapper.addClass("pearnly-dialog");
		const extra = {};  // 方式那几格（随公司 / 账套换）：fieldname → control

		const clear_methods = () => {
			for (const control of Object.values(extra)) control.$wrapper.remove();
			for (const key of Object.keys(extra)) delete extra[key];
			methods = null; vouchers = {};
		};
		const show_methods = found => {
			clear_methods();
			methods = found;
			const parent = dialog.fields_dict.methods_area.$wrapper;
			const add = df => {
				const control = frappe.ui.form.make_control({df, parent, render_input: true});
				extra[df.fieldname] = control;
				return control;
			};
			if (found.cash) add({fieldtype: "Currency", fieldname: "cash", label: __("Cash")});
			(found.transfers || []).forEach((row, index) => {
				add({fieldtype: "Currency", fieldname: `transfer_${index}`, label: row.label || row.name});
				const button = add({fieldtype: "Button", fieldname: `voucher_${index}`, label: __("Attachments"),
					click: () => void settle(upload_voucher(stored => {
						vouchers[row.prefix] = stored.file;
						button.$input.text(stored.file_name || __("Attachments"));
					}))});
			});
			if (found.discount) add({fieldtype: "Currency", fieldname: "discount", label: __("Discount")});
		};
		const reset_customer = () => {
			open = [];
			dialog.fields_dict.invoices.df.data = [];
			dialog.fields_dict.invoices.grid.refresh();
		};
		async function pick_company() {
			const company = dialog.get_value("company");
			clear_methods(); reset_customer();
			dialog.set_value("customer", "");
			dialog.set_df_property("book", "hidden", 1);
			if (!company) return;
			const found = await p.call("pearnly_erp_portal.collection.api.api_collection_new_options", {company}, "POST");
			if (found.book?.options?.length) {
				dialog.set_df_property("book", "options", [{value: "", label: ""},
					...found.book.options.map(row => ({value: row.code, label: row.name}))]);
				dialog.set_df_property("book", "hidden", 0);
				return;
			}
			show_methods(found.methods || {});
		}
		async function pick_book() {
			const company = dialog.get_value("company"), key = dialog.get_value("book");
			if (!company || !key) return;
			await p.call("pearnly_erp_portal.collection.api.api_collection_choose_book", {company, key}, "POST");
			const found = await p.call("pearnly_erp_portal.collection.api.api_collection_new_options", {company}, "POST");
			dialog.set_df_property("book", "hidden", 1);
			show_methods(found.methods || {});
		}
		async function pick_customer() {
			const company = dialog.get_value("company"), customer = dialog.get_value("customer");
			reset_customer();
			if (!company || !customer) return;
			const found = await p.call("pearnly_erp_portal.collection.api.api_collection_open_invoices", {company, customer}, "POST");
			open = found.items || [];
			// 行要有 name：原生表格的勾选按行名记（`grid.js` setup_check），没名字的行勾了也不算选中
			dialog.fields_dict.invoices.df.data = open.map(row => ({name: row.docnum, docnum: row.docnum, doc_date: row.doc_date,
				available: amount(row.available), amount: 0}));
			dialog.fields_dict.invoices.grid.refresh();
		}
		// 勾选一张发票（原生表格的行勾选）= 收它：「本次收」还空着就填未收余额，可再改（11-14 真机：只勾不填提交报「请至少选择一张发票」）
		const fill_checked = () => {
			const grid = dialog.fields_dict.invoices.grid;
			let changed = false;
			for (const row of grid.get_selected_children()) {
				if (!amount(row.amount)) { row.amount = row.available; changed = true; }
			}
			if (changed) grid.refresh();
		};
		// 原生在 click 上记勾选；排在它之后再填
		dialog.fields_dict.invoices.grid.wrapper.on("click", ".grid-row-check", () => setTimeout(fill_checked));
		async function submit(values) {
			fill_checked();
			// 读表格自己的行（在格子里改的「本次收」在这里），不读打开时塞进去的那份
			const invoices = (dialog.fields_dict.invoices.grid.get_data() || [])
				.filter(row => amount(row.amount) > 0).map(row => ({docnum: row.docnum, amount: row.amount}));
			const chosen = [
				...(methods?.transfers || []).map((row, index) => ({prefix: row.prefix,
					amount: extra[`transfer_${index}`]?.get_value() || 0, voucher: vouchers[row.prefix] || ""})),
				...(methods?.discount ? [{prefix: methods.discount.prefix, amount: extra.discount?.get_value() || 0}] : []),
			].filter(row => amount(row.amount) > 0);
			dialog.disable_primary_action();
			try {
				await p.call("pearnly_erp_portal.collection.api.api_collection_submit", {company: values.company, customer: values.customer,
					invoices: JSON.stringify(invoices), cash: extra.cash?.get_value() || 0, methods: JSON.stringify(chosen),
					request}, "POST");
			} finally {
				dialog.enable_primary_action();
			}
			frappe.utils.play_sound("submit");
			frappe.show_alert({message: __("Recorded · pending write"), indicator: "green"});
			dialog.hide();
			on_done?.();
		}

		dialog.show();
		if (current) dialog.set_value("company", current);
	};
})();
