/* One native Autocomplete per transaction party/item, with the existing scanner. */
(() => {
	const p = frappe.pearnly;
	p.entry_choice_field = (label, editable) => ({fieldname: "entry_choice", label: __(label),
		fieldtype: "Autocomplete", ignore_validation: true, options: [], read_only: !editable});
	p.bind_entry_choice = (form, {kind, context, reference, title, editable, on_select, on_clear}) => {
		const control = form.fields_dict.entry_choice;
		let choices = [], revision = 0, applied = "", pending = Promise.resolve();
		const field = kind === "product" ? "product_name" : kind === "supplier" ? "supplier_name" : "customer_name";
		const label = row => `${row[field]} · ${row.code || row.tax_id || row.name}${row.branch_no ? " / " + row.branch_no : ""} · ${row.name}`;
		const sync = async () => {
			if (!editable || !control.$wrapper[0].isConnected) return;
			const value = String(control.$input?.val() || "").trim();
			if (value === applied) return pending;
			applied = value;
			const row = choices.find(item => label(item) === value);
			pending = pending.catch(() => {}).then(async () => {
				if (value !== applied) return;
				await form.set_values({[reference]: row?.name || "", [title]: row?.[field] || value});
				if (row) {
					await on_select(row);
					if (value === applied) {
						applied = row[field];
						await control.set_value(applied);
					}
				} else await on_clear?.();
			});
			return pending;
		};
		const set_rows = rows => {
			choices = rows;
			control.set_data(rows.map(row => ({label: label(row), value: label(row)})));
		};
		if (editable) {
			// Native autocomplete interaction and keyboard handling; customer text is textContent.
			control.awesomplete.filter = () => true;
			control.awesomplete.item = item => {
				const node = document.createElement("li");
				node.setAttribute("role", "option");
				node.setAttribute("aria-selected", "false"); node.textContent = item.label;
				return node;
			};
			const search = frappe.utils.debounce(async () => {
				const own = ++revision, text = control.$input.val();
				const selected = form.get_value(reference) && text === form.get_value(title);
				const rows = await p.call("pearnly_erp_portal.masters.selection.api_choices",
					{kind, context, txt: selected ? "" : text});
				if (own !== revision || !control.$wrapper[0].isConnected || !control.$input.is(":focus")) return;
				set_rows(rows); control.awesomplete.evaluate();
			}, 180);
			control.$input.on("input.entry-choice", () => {++revision; void sync(); search();});
			control.$input.on("focus.entry-choice click.entry-choice", search);
			control.$input.on("change.entry-choice awesomplete-selectcomplete.entry-choice", () => void sync());
			const button = $('<button type="button" class="btn btn-default btn-xs pearnly-link-scan"></button>')
				.attr({title: __("Scan"), "aria-label": __("Scan")}).html(frappe.utils.icon("scan", "sm"));
			$(control.input_area).addClass("position-relative").append(button);
			button.on("click", () => p.scan({kind: "all", on_result: async code => {
				++revision;
				const rows = await p.call("pearnly_erp_portal.masters.selection.api_choices", {kind, context, txt: code, scan: 1});
				set_rows(rows);
				if (rows.length === 1) {
					await control.set_value(label(rows[0])); await sync();
				} else {
					// Unknown scans remain search text, never a fabricated master reference.
					await control.set_value(code); await sync(); control.$input.trigger("focus");
					if (!rows.length) frappe.show_alert({message: __("No results found"), indicator: "orange"});
				}
			}}));
		}
		return {
			sync,
			async show() {
				++revision; choices = [];
				applied = form.get_value(title) || "";
				await control.set_value(applied);
			},
		};
	};
})();
