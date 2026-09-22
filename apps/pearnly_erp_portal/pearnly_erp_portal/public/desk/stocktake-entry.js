/* Scan -> observe actual fields -> save -> next. One native Dialog, one camera stream. */
(() => {
	const p = frappe.pearnly, api = "pearnly_erp_portal.inventory.api.";
	p.stockCountEntry = async ({ count, t, row, entry, continuous = false, on_saved, on_done }) => {
		let selected, lookup, request_id, scanner, busy = false, closed = false, camera = false;
		let last_place = null;
		const w = key => t(`stock.workbench.${key}`);
		const fields = [
			{ fieldtype: "Section Break", fieldname: "scan_section" },
			{ fieldtype: "Data", fieldname: "code", label: w("code"), description: w("scanHint") },
			{ fieldtype: "Button", fieldname: "find", label: w("find"), click: () => match(d.get_value("code")) },
			{ fieldtype: "Button", fieldname: "camera", label: w("camera"), click: () => start_camera() },
			{ fieldtype: "HTML", fieldname: "video", options: p.scan_stage(), hidden: 1 },
			{ fieldtype: "Check", fieldname: "torch", label: __("product.scan.light"), hidden: 1,
				change: () => scanner?.torch(Boolean(d.get_value("torch"))) },
			{ fieldtype: "HTML", fieldname: "message" },
			{ fieldtype: "Section Break", fieldname: "entry_section", hidden: 1 },
			{ fieldtype: "Data", fieldname: "product_name", label: t("stock.col.name"), read_only: 1 },
			{ fieldtype: "Select", fieldname: "source_row", label: w("bookPosition"), options: [],
				change: () => choose_source() },
			{ fieldtype: "Data", fieldname: "unit", label: t("stock.col.unit"), read_only: 1 },
			{ fieldtype: "Data", fieldname: "warehouse", label: w("actualWarehouse"), reqd: 1 },
			{ fieldtype: "Data", fieldname: "location", label: w("actualLocation") },
			{ fieldtype: "Float", fieldname: "qty", label: w("entryQty"), precision: 3, reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "note", label: t("stock.count.note") },
		];
		const d = p.dialog(entry ? w("editEntry") : (continuous ? w("continuous") : w("record")), fields,
			continuous ? w("saveNext") : __("Save"), async values => {
				if (!selected || busy) return;
				busy = true;
				try {
					const payload = { qty: values.qty, warehouse: values.warehouse,
						location: values.location || "", note: values.note || "", stock_token: lookup.stock_token };
					if (entry) await p.call(api + "api_stock_count_edit_entry",
						{ ...payload, entry: entry.name, version: entry.version }, "POST");
					else await p.call(api + "api_stock_count_add_entry", { ...payload, count,
						item: values.source_row || undefined, product: selected.name, request_id }, "POST");
					last_place = { warehouse: values.warehouse, location: values.location || "" };
					p.success(t("stock.count.entrySaved"));
					await on_saved?.();
					if (closed) return;
					if (!continuous) d.hide();
					else await next();
				} finally { busy = false; }
			});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		d.set_secondary_action(() => { d.hide(); on_done?.(); });
		d.set_secondary_action_label(w("review"));
		d.onhide = () => { closed = true; scanner?.stop(); };
		d.fields_dict.code.$input.on("keydown", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); void match(d.get_value("code")); }
		});
		function message(text) { d.fields_dict.message.$wrapper.empty().append($("<p class='text-muted'>").text(text)); }
		function editor(show) {
			d.set_df_property("scan_section", "hidden", show);
			d.set_df_property("entry_section", "hidden", !show);
			d.get_primary_btn().toggle(show);
		}
		async function choose_source() {
			if (!lookup || !selected) return;
			const source = lookup.rows.find(r => r.name === d.get_value("source_row"));
			await d.set_value("warehouse", entry?.warehouse_name || source?.warehouse_name || lookup.warehouse_name);
			await d.set_value("location", entry?.location ?? source?.location ?? "");
		}
		async function match(code, product) {
			if (busy || selected || closed || (!code && !product)) return;
			busy = true; scanner?.pause();
			try {
				lookup = await p.call(api + "api_stock_count_lookup", { count, code: code || undefined, product });
				if (closed) return;
				selected = lookup.product;
				request_id = crypto.randomUUID();
				d.set_df_property("source_row", "options", lookup.rows.map(r => ({
					value: r.name, label: `${r.warehouse_name} / ${r.location || t("stock.count.blank")} · ${p.number(r.book_qty)} ${r.unit || ""}`,
				})));
				d.set_df_property("source_row", "hidden", !lookup.rows.length);
				await d.set_value("source_row", row?.name || lookup.rows[0]?.name || "");
				await d.set_value("product_name", selected.product_name);
				await d.set_value("unit", selected.base_unit || selected.unit || "");
				await choose_source();
				if (last_place) { await d.set_value("warehouse", last_place.warehouse); await d.set_value("location", last_place.location); }
				await d.set_value("qty", entry ? entry.qty : "");
				await d.set_value("note", entry?.note || "");
				editor(true);
				d.fields_dict.qty.$input.trigger("focus");
			} catch (error) {
				message(w("lookupFailed"));
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
				scanner?.resume();
			} finally { busy = false; }
		}
		async function start_camera() {
			if (scanner || closed) return;
			camera = true;
			d.set_df_property("video", "hidden", false);
			d.set_df_property("torch", "hidden", false);
			scanner = p.scan_reader({ host: d.fields_dict.video.$wrapper, kind: "all",
				on_result: code => match(code),
				on_error: () => { scanner?.stop(); scanner = null; camera = false; message(w("cameraFailed")); },
			});
			await scanner.start();
		}
		async function next() {
			selected = null; lookup = null; row = null; entry = null;
			await d.set_value("code", ""); await d.set_value("qty", "");
			editor(false); message(w("nextReady"));
			if (camera) scanner?.resume();
			else d.fields_dict.code.$input.trigger("focus");
		}
		editor(false);
		if (row) await match(null, row.product);
		else if (continuous) await start_camera();
	};
})();
