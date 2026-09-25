/* Entry choice and native uploader lifecycle. Upload bytes finishing is not a
 * successful File response: wait for every server response before one OCR call. */
(() => {
	const p = frappe.pearnly, u = p.purchase_ui;
	u.entry_method = (state, layout) => {
		const choose = state.editable && !state.doc.name && !state.intake && !state.amended_from;
		state.entry_mode = choose ? "ocr" : "manual";
		const choices = [];
		const show_fields = () => !choose || state.entry_mode === "manual" || (state.entry_mode === "ocr" && Boolean(state.ocr_complete));
		state.can_submit = () => !state.busy && !state.uploading && show_fields();
		state.sync_entry = () => {
			layout.set_data_visible(show_fields());
			state.page.btn_primary.prop("disabled", !state.can_submit());
			for (const {mode, control} of choices) {
				control.set_input(state.entry_mode === mode ? 1 : 0);
				control.$input.prop("disabled", state.busy || state.uploading);
			}
			u.set(state.form, "upload", {hidden: state.doc.status === "void" || !state.can_write || (choose && !state.entry_mode)});
			u.set(state.form, "attachment_kind", {hidden: state.doc.status === "void" || !state.can_write || (choose && (!state.entry_mode || (state.entry_mode === "ocr" && !state.ocr_complete)))});
			u.set(state.form, "recognise", {hidden: !state.editable || !state.defaults.capabilities.ocr || (choose && !(state.entry_mode === "ocr" && state.ocr_failed)),
				label: state.ocr_failed ? __("Retry") : __("OCR")});
			state.form.fields_dict.recognise.$input?.prop("disabled", state.busy || state.uploading);
		};
		if (choose) {
			const select = mode => {
				if (state.busy || state.uploading) {state.sync_entry(); return;}
				// A mode change never runs paid OCR or discards already entered values.
				state.entry_mode = mode;
				state.sync_entry();
			};
			const modes = [
				{mode: "ocr", label: __("Upload and Recognize"), description: __("Upload an invoice, then review the recognized details."),
					tip: __("For the best reading, photograph the document upright and straight, with the text the right way up and the whole page in frame.")},
				{mode: "manual", label: __("Manual Entry"), description: __("Enter purchase details. Files are added as attachments only.")},
			];
			const fields = [u.section("Entry Method")];
			for (const [index, option] of modes.entries()) {
				if (index) fields.push(u.column());
				fields.push(u.field(`entry_${option.mode}`, option.label, "Check", {onchange: () => select(option.mode)}));
			}
			const selector = p.layout(layout.entry, fields);
			for (const option of modes) {
				const control = selector.fields_dict[`entry_${option.mode}`];
				control.$wrapper.addClass("purchase-entry-choice");
				control.$wrapper.find(".checkbox > label").addClass("px-3 py-2");
				$(control.label_area).empty().append($('<span class="d-block font-weight-bold">').text(option.label))
					.append($('<small class="d-block mt-1 text-muted font-weight-normal">').text(option.description))
					// 上传识别下面加一句拍摄提示（用户 2026-09-26：侧着的照片会读错，不另花识别费转正）。
					.append(option.tip ? $('<small class="d-block mt-1 text-muted font-weight-normal">').text(option.tip) : null);
				choices.push({mode: option.mode, control});
			}
		} else layout.entry.hide();
		return () => state.upload_cleanup?.();
	};
	u.upload_evidence = (state, refresh_files) => {
		if (state.uploading || state.busy) return;
		if (state.entry_mode === "ocr" && !state.defaults.capabilities.ocr) {
			frappe.msgprint(__("Recognition is not configured for this account"));
			return;
		}
		const kind = state.entry_mode === "ocr" && !state.ocr_complete ? "bill" : state.form.get_value("attachment_kind") || "bill";
		state.uploading = true; state.sync_entry();
		const pending = [];
		let started = false, finished = false, closed = false;
		const finish = async success => {
			if (finished) return;
			finished = true; clearInterval(timer);
			try {
				await Promise.all(pending);
				if (state.disposed) return;
				await refresh_files();
				state.uploading = false;
				if (success && state.entry_mode === "ocr" && kind === "bill") {
					state.ocr_complete = false;
					await state.recognise();
				}
			} finally {state.uploading = false; if (!state.disposed) state.sync_entry();}
		};
		const uploader = new frappe.ui.FileUploader({allow_multiple: true, allow_web_link: false, allow_google_drive: false,
			disable_file_browser: true, allow_take_photo: true, allow_toggle_private: false,
			make_attachments_public: false, allow_toggle_optimize: true,
			restrictions: {allowed_file_types: ["image/*", ".pdf"], max_file_size: 20 * 1024 * 1024},
			on_success: file => {
				if (!file || state.disposed) return;
				pending.push((async () => {
					const row = {file: file.name, file_url: file.file_url, file_name: file.file_name, kind};
					if (state.doc.name && !state.amended_from) await u.call("api_purchase_attachments", {name: state.doc.name, attachments: [row]}, true);
					if (!state.disposed && !state.attachments.some(old => old.file === row.file)) state.attachments.push(row);
				})());
			},
		});
		const upload = uploader.upload_files.bind(uploader);
		uploader.upload_files = () => {started = true; return upload();};
		const check = () => {
			const files = uploader.uploader.files;
			if (started && files.length && files.every(file => file.request_succeeded || file.failed)) {
				void finish(files.every(file => file.request_succeeded) && !closed);
			} else if (closed && (!started || !files.length)) void finish(false);
		};
		const timer = setInterval(check, 150);
		uploader.dialog.$wrapper.on("hide.bs.modal.purchase", () => {
			// Upstream closes after all successful responses; user cancellation must
			// not trigger recognition of only the files that happened to finish first.
			closed = !uploader.uploader.files.length || !uploader.uploader.files.every(file => file.request_succeeded);
			check();
		});
		state.upload_cleanup = () => {finished = true; clearInterval(timer); uploader.dialog.hide();};
	};
})();
