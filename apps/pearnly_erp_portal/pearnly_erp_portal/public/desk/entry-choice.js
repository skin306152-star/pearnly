/* One native Autocomplete per transaction party/item, with the existing scanner. */
(() => {
	const p = frappe.pearnly;
	p.entry_choice_field = (label, editable) => ({fieldname: "entry_choice", label: __(label),
		fieldtype: "Autocomplete", ignore_validation: true, options: [], read_only: !editable});
	p.bind_entry_choice = (form, {kind, context, reference, title, editable, on_select, on_clear, company}) => {
		const control = form.fields_dict.entry_choice;
		const doctype = {product: "ERP Product", supplier: "ERP Supplier", customer: "ERP Customer"}[kind];
		const create_value = "pearnly_create__link_option";
		let arrow;
		const update_arrow = () => {
			const available = Boolean(form.get_value(reference));
			arrow?.toggle(available);
			$(control.input_area).toggleClass("pearnly-has-link-actions", available);
		};
		let choices = [], revision = 0, value_revision = 0, sync_revision = 0, applied = "", pending = Promise.resolve();
		// Search/focus refreshes must not cancel a valid scan. Only a changed value,
		// a new scan, an OCR refill or a master refresh supersedes its result.
		const current = own => own === value_revision && control.$wrapper[0].isConnected;
		const field = kind === "product" ? "product_name" : kind === "supplier" ? "supplier_name" : "customer_name";
		const label = row => `${row[field]} · ${row.code || row.express_code || row.tax_id || row.name}${row.branch_no ? " / " + row.branch_no : ""} · ${row.name}`;
		const sync = async () => {
			if (!editable || !control.$wrapper[0].isConnected) return;
			const value = String(control.$input?.val() || "").trim();
			if (value === applied) return pending;
			const own = ++sync_revision;
			const valid = () => own === sync_revision && control.$wrapper[0].isConnected;
			++revision; ++value_revision;
			applied = value;
			const row = choices.find(item => label(item) === value);
			pending = pending.catch(() => {}).then(async () => {
				if (!valid()) return;
				const was_linked = Boolean(form.get_value(reference));
				await form.set_values({[reference]: row?.name || "", [title]: row?.[field] || value});
				if (!valid()) return;
				if (row) {
					await on_select(row);
					if (valid()) {
						applied = row[field];
						await control.set_value(applied);
					}
				} else if (!value || was_linked) {
					// OCR/free text has its own document details. Correcting its spelling
					// must not erase those details unless the field is actually cleared.
					await on_clear?.();
				}
				update_arrow();
			});
			return pending;
		};
		const set_rows = rows => {
			choices = rows;
			control.set_data([...rows.map(row => ({label: label(row), value: label(row)})),
				...(editable && frappe.model.can_create(doctype) ? [{value: create_value,
					label: __("Create a new {0}", [p.link_editors[doctype]?.label || __(doctype)])}] : [])]);
		};
		if (editable) {
			// Keep the Link's title/detail and plus-action presentation. Autocomplete
			// only permits unbound OCR text; it does not need a different visual style.
			// Master data stays textContent, never executable HTML.
			control.awesomplete.filter = () => true;
			control.awesomplete.item = item => {
				const node = document.createElement("li");
				node.setAttribute("role", "option");
				node.setAttribute("aria-selected", "false");
				const content = document.createElement("p");
				if (item.value === create_value) {
					const action = document.createElement("span");
					action.className = "link-option pearnly-link-option";
					const icon = document.createElement("i");
					icon.className = "fa fa-plus mr-1";
					icon.setAttribute("aria-hidden", "true");
					action.append(icon, document.createTextNode(" " + item.label));
					content.append(action);
				} else {
					const row = choices.find(choice => label(choice) === item.value);
					const name = document.createElement("strong");
					name.textContent = row?.[field] || item.label;
					content.append(name);
					if (row) {
						const detail = document.createElement("span");
						detail.className = "small";
						detail.textContent = (row.code || row.tax_id || row.name) +
							(row.branch_no ? " / " + row.branch_no : "");
						content.append(document.createElement("br"), detail);
					}
				}
				node.append(content);
				return node;
			};
			const search = frappe.utils.debounce(async () => {
				const own = ++revision, text = control.$input.val();
				const selected = form.get_value(reference) && text === form.get_value(title);
				const rows = await p.call("pearnly_erp_portal.masters.selection.api_choices",
					{kind, context, txt: selected ? "" : text, company: company?.() || undefined});
				if (own !== revision || !control.$wrapper[0].isConnected || !control.$input.is(":focus")) return;
				set_rows(rows); control.awesomplete.evaluate();
			}, 180);
			control.input.addEventListener("awesomplete-select", event => {
				if (event.text?.value !== create_value) return;
				event.preventDefault(); event.stopImmediatePropagation();
				control.awesomplete.close();
				p.open_link_create(doctype, control.$input, control);
			}, true);
			control.$input.on("input.entry-choice", () => {++revision; void sync(); search();});
			control.$input.on("focus.entry-choice click.entry-choice", search);
			control.$input.on("change.entry-choice awesomplete-selectcomplete.entry-choice", () => void sync());
			// Only product choices carry the scanner: suppliers and customers have no code to
			// scan. Same declaration as the shared Link fields (`p.scan_doctypes`, scan.js).
			const button = (p.scan_doctypes || []).includes(doctype)
				? $('<button type="button" class="btn btn-default btn-xs pearnly-link-scan"></button>')
					.attr({title: __("Scan"), "aria-label": __("Scan")}).html(frappe.utils.icon("scan-barcode", "sm"))
				: null;
			const holder = $(control.input_area).addClass("pearnly-link-scan-input").append(button || []);
			arrow = $('<span class="link-btn"><a class="btn-open" href="#"></a></span>').appendTo(holder);
			arrow.find("a").attr({title: __("Open Link"), "aria-label": __("Open Link")})
				.html(frappe.utils.icon("arrow-right", "xs")).on("click", event => {
					event.preventDefault(); event.stopPropagation();
					p.open_link_record(doctype, form.get_value(reference), control.$input, control);
				});
			update_arrow();
			p.watch_master_control(control, doctype, () => {++revision; ++value_revision; set_rows([]);});
			button?.on("click", () => {
				const own = ++value_revision;
				++revision;
				return p.scan({kind: "all", on_result: async code => {
					if (!current(own)) return;
					const rows = await p.call("pearnly_erp_portal.masters.selection.api_choices", {kind, context, txt: code, scan: 1, company: company?.() || undefined});
					if (!current(own)) return;
					++revision;
					set_rows(rows);
					if (rows.length === 1) {
						await control.set_value(label(rows[0])); await sync();
					} else {
						// Unknown scans remain search text, never a fabricated master reference.
						await control.set_value(code); await sync(); control.$input.trigger("focus");
						if (!rows.length) frappe.show_alert({message: __("No results found"), indicator: "orange"});
					}
				}});
			});
		}
		return {
			sync,
			async show() {
				++revision; ++value_revision; ++sync_revision; choices = [];
				applied = form.get_value(title) || "";
				await control.set_value(applied);
				update_arrow();
			},
		};
	};
})();
