/* Scan -> observe actual fields -> save -> next. One native Dialog, one camera stream. */
(() => {
	const p = frappe.pearnly, api = "pearnly_erp_portal.inventory.api.";
	// 一条记录最多 5 张照片（服务端 `counts.PHOTO_LIMIT` 同值，它才是权威，这里只是先拦一下）。
	const PHOTO_MAX = 5;
	p.stockCountEntry = async ({ count, t, row, entry, continuous = false, on_saved, on_done }) => {
		let selected, lookup, request_id, scanner, busy = false, closed = false, camera = false;
		let last_place = null;
		// `photos` 是本次要随记录提交的（桌面端上传后拿到 file_url，服务端再挂到这条记录上）；
		// `existing` 是这条记录**已经有**的照片，只做只读预览（旧项目也没有删照片的入口）。
		const photos = [];
		let existing = [];
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
			{ fieldtype: "Section Break", fieldname: "photo_section", hidden: 1 },
			{ fieldtype: "HTML", fieldname: "photos", hidden: 1 },
		];
		const d = p.dialog(entry ? w("editEntry") : (continuous ? w("continuous") : w("record")), fields,
			continuous ? w("saveNext") : __("Save"), async values => {
				if (!selected || busy) return;
				// Native Float normalizes an empty input to 0; an observation must be explicit.
				if (!String(d.fields_dict.qty.$input.val()).trim()) {
					frappe.msgprint(__("Enter a quantity"));
					d.fields_dict.qty.$input.trigger("focus");
					return;
				}
				busy = true;
				try {
					const payload = { qty: values.qty, warehouse: values.warehouse,
						location: values.location || "", note: values.note || "", stock_token: lookup.stock_token,
						photos: photos.map(photo => photo.url) };
					if (entry) await p.call(api + "api_stock_count_edit_entry",
						{ ...payload, entry: entry.name, version: entry.version }, "POST");
					else await p.call(api + "api_stock_count_add_entry", { ...payload, count,
						item: values.source_row || undefined, product: selected.name, request_id }, "POST");
					last_place = { warehouse: values.warehouse, location: values.location || "" };
					p.success(t("stock.count.entrySaved"));
					photos.length = 0;
					await on_saved?.();
					if (closed) return;
					if (!continuous) d.hide();
					else await next();
				} finally { busy = false; }
			});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		d.set_secondary_action(() => { d.hide(); on_done?.(); });
		// 连续盘点时第二颗是"完成"：收工离开扫码，落到差异那一屏（用户 2026-09-22 口径：
		// "…下一项或者完成"）。单条录入/修改时它仍是"查看差异"。
		d.set_secondary_action_label(continuous ? w("done") : w("review"));
		d.onhide = () => { closed = true; scanner?.stop(); };
		d.fields_dict.code.$input.on("keydown", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); void match(d.get_value("code")); }
		});
		// A scanner's Enter finds the product; it must not submit the observation.
		d.fields_dict.code.$input.off("keypress").on("keypress", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); }
		});
		function message(text) { d.fields_dict.message.$wrapper.empty().append($("<p class='text-muted'>").text(text)); }
		function editor(show) {
			d.set_df_property("scan_section", "hidden", show);
			d.set_df_property("entry_section", "hidden", !show);
			d.set_df_property("photo_section", "hidden", !show);
			d.set_df_property("photos", "hidden", !show);
			d.get_primary_btn().toggle(show);
		}
		// ── 照片 ────────────────────────────────────────────────────────────────
		// 原生上传器（`frappe.ui.FileUploader`）：多选、可选拍照，默认存成私有文件；
		// 这里只收集 `file_url`，提交时由服务端把 File 挂到这条记录上（`counts._save_photos`）。
		function pick_photos() {
			const left = PHOTO_MAX - existing.length - photos.length;
			if (left <= 0) { frappe.msgprint(w("photoLimit")); return; }
			new frappe.ui.FileUploader({
				allow_multiple: true,
				allow_take_photo: true,
				allow_toggle_private: false,
				restrictions: { allowed_file_types: ["image/*"] },
				on_success: file => {
					if (!file?.file_url) return;
					if (existing.length + photos.length >= PHOTO_MAX) { frappe.msgprint(w("photoLimit")); return; }
					photos.push({ url: file.file_url, name: file.file_name || String(file.file_url).split("/").pop() });
					render_photos();
				},
			});
		}
		function render_photos() {
			const host = d.fields_dict.photos?.$wrapper;
			if (!host) return;
			const total = existing.length + photos.length;
			host.empty();
			$("<p class='text-muted'>").text(`${w("photos")} ${total} / ${PHOTO_MAX} · ${w("photoHint")}`).appendTo(host);
			const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
			const thumb = (url, name, removable) => {
				const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
				$("<img>").attr({ src: url, alt: name }).appendTo(cell);
				if (!removable) return;
				$("<button type='button' class='btn btn-default btn-xs'>").text(w("removePhoto"))
					.on("click", () => {
						const index = photos.findIndex(photo => photo.url === url);
						if (index >= 0) photos.splice(index, 1);
						render_photos();
					}).appendTo(cell);
			};
			existing.forEach(photo => thumb(photo.file_url, photo.file_name || "", false));
			photos.forEach(photo => thumb(photo.url, photo.name, true));
			if (total < PHOTO_MAX) {
				$("<button type='button' class='btn btn-default btn-sm'>").text(w("addPhoto"))
					.on("click", () => pick_photos()).appendTo(host);
			}
		}
		async function load_photos() {
			if (!entry?.name) return;
			existing = await p.call(api + "api_stock_count_photos", { entry: entry.name });
			render_photos();
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
				if (!entry) d.fields_dict.qty.$input.val("");
				await d.set_value("note", entry?.note || "");
				editor(true);
				await load_photos();
				render_photos();
				d.fields_dict.qty.$input.trigger("focus");
			} catch (error) {
				selected = null;
				message(w("lookupFailed"));
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
				scanner?.resume();
			} finally { busy = false; }
		}
		async function start_camera() {
			if (scanner || closed) return;
			camera = true;
			d.set_df_property("video", "hidden", false);
			scanner = p.scan_reader({ host: d.fields_dict.video.$wrapper, kind: "all",
				on_result: code => match(code),
				on_error: () => { scanner?.stop(); scanner = null; camera = false; message(w("cameraFailed")); },
			});
			await scanner.start();
			d.set_df_property("torch", "hidden", !scanner?.has_torch());
		}
		async function next() {
			selected = null; lookup = null; row = null; entry = null;
			existing = [];
			photos.length = 0;
			await d.set_value("code", ""); await d.set_value("qty", "");
			editor(false); render_photos(); message(w("nextReady"));
			if (camera) scanner?.resume();
			else d.fields_dict.code.$input.trigger("focus");
		}
		editor(false);
		if (row) await match(null, row.product);
		else if (continuous) await start_camera();
	};
})();
