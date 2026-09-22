/* Purchase viewer adapted from the production legacy purchase-form-bill.ts and
 * image-viewer.ts at ec2e5c957e6b. Same transform/pan/pinch/rotate/reset contract;
 * controls and fullscreen fallback use Desk, private files use current Site auth. */
(() => {
	const u = frappe.pearnly.purchase_ui;
	u.document_viewer = wrapper => {
		const host = $('<div class="purchase-document-viewer">').appendTo(wrapper).hide();
		const viewport = $('<div class="purchase-document-canvas">').appendTo(host);
		const image = $('<img draggable="false">').appendTo(viewport);
		const status = $('<div class="text-muted text-center purchase-document-status" role="status">').appendTo(viewport);
		const toolbar = $('<div class="purchase-document-tools">').appendTo(host);
		let scale = 1, tx = 0, ty = 0, rotation = 0, dragging = false, sx = 0, sy = 0;
		let alive = true, revision = 0, current, page = 1, pages = 1, loaded = false, dialog;
		const clamp = value => Math.max(0.4, Math.min(6, value));
		const zoom = $('<span class="text-muted small">');
		const page_number = $('<span class="text-muted small">');
		const controls = [];
		const button = (label, icon, click) => {
			const control = frappe.ui.form.make_control({parent: toolbar, df: {fieldname: `viewer_${controls.length}`, fieldtype: "Button", label, click}, render_input: true});
			control.$wrapper.addClass("mb-0");
			control.$input.attr({title: label, "aria-label": label}).html(frappe.utils.icon(icon, "sm"));
			controls.push(control); return control;
		};
		const previous = button(__("Previous"), "left", () => show(current, page - 1));
		page_number.appendTo(toolbar);
		const next = button(__("Next"), "right", () => show(current, page + 1));
		button(__("Zoom Out"), "minus", () => {scale = clamp(scale / 1.2); apply();});
		zoom.appendTo(toolbar);
		button(__("Zoom In"), "add", () => {scale = clamp(scale * 1.2); apply();});
		button(__("Rotate"), "rotate-cw", () => {rotation += 90; apply();});
		button(__("Reset"), "refresh", () => {reset();});
		button(__("Full Screen"), "expand", () => {
			if (dialog) {dialog.hide(); return;}
			dialog = new frappe.ui.Dialog({title: current.file_name, size: "extra-large", fields: [{fieldname: "document", fieldtype: "HTML"}],
				onhide: () => {host.appendTo(wrapper); dialog = null;}});
			dialog.$wrapper.addClass("purchase-preview-dialog");
			dialog.$wrapper.one("hidden.bs.modal.purchase", function() {$(this).remove();});
			host.appendTo(dialog.fields_dict.document.$wrapper); dialog.show();
		});
		const apply = () => {
			image[0].style.transform = `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale}) rotate(${rotation}deg)`;
			zoom.text(`${Math.round(scale * 100)}%`);
		};
		const reset = () => {scale = 1; tx = ty = rotation = 0; apply();};
		const update = () => {
			controls.forEach(control => control.$input.prop("disabled", !loaded));
			previous.$wrapper.toggle(pages > 1); next.$wrapper.toggle(pages > 1); page_number.toggle(pages > 1).text(`${page} / ${pages}`);
			previous.$input.prop("disabled", !loaded || page <= 1); next.$input.prop("disabled", !loaded || page >= pages);
		};
		const show = async (row, number = 1) => {
			if (!alive || (current?.file === row?.file && page === number && loaded)) return;
			const version = ++revision;
			current = row; page = number; loaded = false; pages = 1;
			host.toggle(Boolean(row)); image.hide(); reset(); update();
			if (!row) return;
			status.text(__("Loading...")).show();
			try {
				let src = row.file_url;
				if (/\.pdf$/i.test(row.file_name || row.file_url)) {
					const result = await u.call("api_purchase_file_page", {file: row.file, page: number});
					if (!alive || version !== revision) return;
					src = result.image; pages = result.pages;
				}
				image.attr({src, alt: row.file_name});
				await image[0].decode();
				if (!alive || version !== revision) return;
				loaded = true; status.hide(); image.show(); update();
			} catch (_) {if (alive && version === revision) status.text(__("Unable to load preview"));}
		};
		const wheel = event => {if (!loaded) return; event.preventDefault(); scale = clamp(scale * (event.deltaY < 0 ? 1.12 : 0.89)); apply();};
		const down = event => {if (!loaded || event.button !== 0) return; event.preventDefault(); dragging = true; sx = event.clientX - tx; sy = event.clientY - ty;};
		const move = event => {if (!dragging) return; tx = event.clientX - sx; ty = event.clientY - sy; apply();};
		const up = () => {dragging = false;};
		let pinch_distance = 0, pinch_scale = 1;
		const distance = touches => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
		const touch_start = event => {
			if (event.touches.length === 2) {pinch_distance = distance(event.touches); pinch_scale = scale; dragging = false;}
			else if (event.touches.length === 1) {dragging = true; sx = event.touches[0].clientX - tx; sy = event.touches[0].clientY - ty;}
		};
		const touch_move = event => {
			if (!loaded) return;
			if (event.touches.length === 2) {event.preventDefault(); scale = clamp(pinch_scale * distance(event.touches) / (pinch_distance || 1)); apply();}
			else if (dragging && event.touches.length === 1) {event.preventDefault(); move(event.touches[0]);}
		};
		viewport[0].addEventListener("wheel", wheel, {passive: false});
		viewport[0].addEventListener("mousedown", down);
		viewport[0].addEventListener("touchstart", touch_start, {passive: true});
		viewport[0].addEventListener("touchmove", touch_move, {passive: false});
		viewport[0].addEventListener("touchend", up);
		window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
		return {show, destroy: () => {
			alive = false; ++revision; dialog?.hide();
			window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
			host.remove();
		}};
	};
})();
