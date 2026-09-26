/* 盘点记录弹窗（Cowork）：查商品 → 填实际仓库 / 仓位 / 数量（可带照片）→ 保存 → 下一条。
 *
 * 照 ERP `stocktake-entry.js`：一个官方 Dialog，编码框回车即查（扫码枪就是“输入 + 回车”）。
 * 与 ERP 的差别：Cowork 没有仓库主档，实际仓库是自由填写（默认账面仓位 / 上一条的位置）；
 * 一个商品在任务里只有一行，不用选账面行。摄像头扫码组件目前在 ERP 应用里，
 * 共用层有 `p.scan_reader` 时才出现“摄像头”按钮。
 */
(() => {
	const p = frappe.pearnly, api = "pearnly_cowork.stocktake.api.";
	const PHOTO_MAX = 5;  // 服务端 `counting.PHOTO_LIMIT` 才是权威，这里只先拦一下

	p.coworkStockEntry = async ({ task_id, t, item = null, entry = null, continuous = false, on_saved, on_done }) => {
		let selected = null, request_id, scanner, busy = false, closed = false, camera = false, last_place = null;
		const photos = [];
		let existing = [];
		const can_scan = typeof p.scan_reader === "function";
		const fields = [
			{ fieldtype: "Section Break", fieldname: "scan_section" },
			{ fieldtype: "Data", fieldname: "code", label: t("cowork.stock.code"), description: t("cowork.stock.scanHint") },
			{ fieldtype: "Button", fieldname: "find", label: t("cowork.stock.find"), click: () => match(d.get_value("code")) },
			...(can_scan ? [
				{ fieldtype: "Button", fieldname: "camera", label: t("cowork.stock.camera"), click: () => start_camera() },
				{ fieldtype: "HTML", fieldname: "video", options: p.scan_stage(), hidden: 1 },
			] : []),
			{ fieldtype: "HTML", fieldname: "message" },
			{ fieldtype: "Section Break", fieldname: "entry_section", hidden: 1 },
			{ fieldtype: "Data", fieldname: "product_name", label: t("cowork.stock.col.name"), read_only: 1 },
			{ fieldtype: "Data", fieldname: "book_position", label: t("cowork.stock.col.position"), read_only: 1 },
			{ fieldtype: "Data", fieldname: "unit", label: t("cowork.stock.col.unit"), read_only: 1 },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Data", fieldname: "warehouse", label: t("cowork.stock.actualWarehouse"), reqd: 1 },
			{ fieldtype: "Data", fieldname: "location", label: t("cowork.stock.actualLocation") },
			{ fieldtype: "Float", fieldname: "qty", label: t("cowork.stock.entryQty"), precision: 3, reqd: 1 },
			{ fieldtype: "Section Break", fieldname: "photo_section", hidden: 1 },
			{ fieldtype: "HTML", fieldname: "photos" },
		];
		const d = p.dialog(entry ? t("cowork.stock.editEntry") : (continuous ? t("cowork.stock.continuous") : t("cowork.stock.record")),
			fields, continuous ? t("cowork.stock.saveNext") : __("Save"), async values => {
				if (!selected || busy) return;
				// 官方 Float 会把空输入当 0；盘点数必须明确填。
				if (!String(d.fields_dict.qty.$input.val()).trim()) {
					frappe.msgprint(__("Enter a quantity"));
					d.fields_dict.qty.$input.trigger("focus");
					return;
				}
				busy = true;
				try {
					const payload = { task_id, request_id, quantity: String(values.qty), warehouse: values.warehouse,
						location: values.location || "", photos: JSON.stringify(photos.map(photo => photo.url)) };
					if (entry) await p.call(api + "edit_entry", { ...payload, entry_id: entry.id, version: entry.version }, "POST");
					else await p.call(api + "add_entry", { ...payload, item_id: selected.id }, "POST");
					last_place = { warehouse: values.warehouse, location: values.location || "" };
					p.success(t("cowork.stock.entrySaved"), "click");
					photos.length = 0;
					await on_saved?.();
					if (closed) return;
					if (!continuous) d.hide();
					else await next();
				} finally { busy = false; }
			});
		d.$wrapper.find(".modal-dialog").addClass("modal-lg");
		d.set_secondary_action(() => { d.hide(); on_done?.(); });
		d.set_secondary_action_label(continuous ? t("cowork.stock.done") : t("cowork.stock.review"));
		d.onhide = () => { closed = true; scanner?.stop(); };
		d.fields_dict.code.$input.on("keydown", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); void match(d.get_value("code")); }
		});
		// 扫码枪的回车只用来查商品，不能把记录提交出去。
		d.fields_dict.code.$input.off("keypress").on("keypress", event => {
			if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); }
		});
		const message = text => d.fields_dict.message.$wrapper.empty().append($("<p class='text-muted'>").text(text));
		function editor(show) {
			d.set_df_property("scan_section", "hidden", show);
			d.set_df_property("entry_section", "hidden", !show);
			d.set_df_property("photo_section", "hidden", !show);
			d.get_primary_btn().toggle(show);
		}

		// ── 照片：官方上传器存成私有文件，提交时服务端挂到这条记录上 ─────────────
		function pick_photos() {
			if (existing.length + photos.length >= PHOTO_MAX) { frappe.msgprint(t("cowork.stock.photoLimit")); return; }
			new frappe.ui.FileUploader({
				allow_multiple: true, allow_take_photo: true, allow_toggle_private: false,
				make_attachments_public: false, disable_file_browser: true,
				restrictions: { allowed_file_types: ["image/*"] },
				on_success: file => {
					if (!file?.file_url) return;
					if (existing.length + photos.length >= PHOTO_MAX) { frappe.msgprint(t("cowork.stock.photoLimit")); return; }
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
			$("<p class='text-muted'>").text(`${t("cowork.stock.photos")} ${total} / ${PHOTO_MAX}`).appendTo(host);
			const strip = $("<div class='pearnly-count-photos'>").appendTo(host);
			const thumb = (url, name, removable) => {
				const cell = $("<div class='pearnly-count-photo'>").appendTo(strip);
				$("<img>").attr({ src: url, alt: name }).appendTo(cell);
				if (!removable) return;
				$("<button type='button' class='btn btn-default btn-xs'>").text(t("cowork.stock.removePhoto"))
					.on("click", () => {
						const index = photos.findIndex(photo => photo.url === url);
						if (index >= 0) photos.splice(index, 1);
						render_photos();
					}).appendTo(cell);
			};
			existing.forEach(photo => thumb(photo.file_url, photo.file_name || "", false));
			photos.forEach(photo => thumb(photo.url, photo.name, true));
			if (total < PHOTO_MAX) {
				$("<button type='button' class='btn btn-default btn-sm'>").text(t("cowork.stock.addPhoto"))
					.on("click", () => pick_photos()).appendTo(host);
			}
		}

		async function match(code, item_id) {
			if (busy || selected || closed || (!code && !item_id)) return;
			busy = true; scanner?.pause();
			try {
				const found = await p.call(api + "lookup", { task_id, code: code || undefined, item_id });
				if (closed) return;
				selected = found.item;
				request_id = crypto.randomUUID();
				await d.set_value("product_name", `${selected.product_name} · ${selected.product_code}`);
				await d.set_value("book_position", `${selected.warehouse || "—"} / ${selected.location || "—"} · `
					+ `${p.number(selected.book_qty)} ${selected.unit || ""}`.trim());
				await d.set_value("unit", selected.unit || "");
				const place = entry || last_place || { warehouse: selected.warehouse, location: selected.location };
				await d.set_value("warehouse", place.warehouse || "");
				await d.set_value("location", place.location || "");
				await d.set_value("qty", entry ? entry.quantity : "");
				if (!entry) d.fields_dict.qty.$input.val("");
				editor(true);
				existing = entry ? await p.call(api + "entry_photos", { task_id, entry_id: entry.id }) : [];
				render_photos();
				d.fields_dict.qty.$input.trigger("focus");
				if (code) p.scan_result_sound?.(true);
			} catch (error) {
				selected = null;
				message(t("cowork.stock.lookupFailed"));
				if (!error?._server_messages && !error?.responseJSON) console.error(error);
				scanner?.resume();
				if (code) p.scan_result_sound?.(false);
			} finally { busy = false; }
		}
		async function start_camera() {
			if (scanner || closed || !can_scan) return;
			camera = true;
			d.set_df_property("video", "hidden", false);
			scanner = p.scan_reader({ host: d.fields_dict.video.$wrapper, kind: "all",
				on_result: code => match(code),
				on_error: () => { scanner?.stop(); scanner = null; camera = false; message(t("cowork.stock.cameraFailed")); },
			});
			await scanner.start();
		}
		async function next() {
			selected = null; item = null; entry = null; existing = [];
			photos.length = 0;
			await d.set_value("code", ""); await d.set_value("qty", "");
			editor(false); render_photos(); message(t("cowork.stock.nextReady"));
			if (camera) scanner?.resume();
			else d.fields_dict.code.$input.trigger("focus");
		}

		editor(false);
		if (item) await match(null, item.id);
		else d.fields_dict.code.$input.trigger("focus");
	};
})();
