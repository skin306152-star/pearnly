/* 单据原件预览框：ERP 采购/销售与 Cowork 复核共用**这一份**实现。
 *
 * 交互契约来自生产版的 `purchase-form-bill.ts` / `image-viewer.ts`（ec2e5c957e6b）：
 * 拖拽平移、滚轮缩放、双指捏合、旋转、复位、翻页、全屏；按钮与图标都用官方控件，
 * 私有文件走当前站点会话。产品不再各写一套。
 *
 * **页图从哪来**由调用方交代：`p.document_viewer(wrapper, {load, on_page})`，
 * `load(row, page) => {image, pages}`（可以返回 Promise）；`on_page(row, page, pages)`
 * 在**翻到新的一页之后**回调（复核要按页对齐左侧内容），不传就只是看。
 *   · ERP 走自己的私有文件页图接口（PDF 才要页图，图片直接用 `file_url`）；
 *   · Cowork 走原始文件的页图接口（PDF 与图片都从它出）。
 *
 * 样式在共用皮肤里（`desk/product.css` 的 `.pearnly-document-*`），产品不各写一套。
 */
(() => {
	const p = frappe.pearnly;
	p.document_viewer = (wrapper, {load, on_page}) => {
		const host = $('<div class="pearnly-document-viewer">').appendTo(wrapper).hide();
		const viewport = $('<div class="pearnly-document-canvas">').appendTo(host);
		const image = $('<img draggable="false">').appendTo(viewport);
		const status = $('<div class="text-muted text-center pearnly-document-status" role="status">').appendTo(viewport);
		const toolbar = $('<div class="pearnly-document-tools">').appendTo(host);
		const paging = $('<div class="pearnly-document-tool-group">').appendTo(toolbar);
		const sizing = $('<div class="pearnly-document-tool-group">').appendTo(toolbar);
		const actions = $('<div class="pearnly-document-tool-group">').appendTo(toolbar);
		let scale = 1, tx = 0, ty = 0, rotation = 0, dragging = false, sx = 0, sy = 0;
		let alive = true, revision = 0, current, page = 1, pages = 1, loaded = false, dialog;
		const clamp = value => Math.max(0.4, Math.min(6, value));
		const zoom = $('<span class="text-muted small pearnly-document-value">');
		const page_number = $('<span class="text-muted small pearnly-document-value">');
		const controls = [];
		const button = (label, icon, click, parent = actions) => {
			const control = frappe.ui.form.make_control({parent, only_input: true, df: {fieldname: `viewer_${controls.length}`, fieldtype: "Button", label, click}, render_input: true});
			control.$wrapper.addClass("mb-0");
			control.$input.attr({title: label, "aria-label": label}).html(frappe.utils.icon(icon, "sm"));
			controls.push(control); return control;
		};
		const previous = button(__("Previous"), "left", () => show(current, page - 1), paging);
		page_number.appendTo(paging);
		const next = button(__("Next"), "right", () => show(current, page + 1), paging);
		button(__("Zoom Out"), "minus", () => {scale = clamp(scale / 1.2); apply();}, sizing);
		zoom.appendTo(sizing);
		button(__("Zoom In"), "add", () => {scale = clamp(scale * 1.2); apply();}, sizing);
		button(__("Rotate"), "rotate-cw", () => {rotation += 90; apply();});
		button(__("Reset"), "refresh", () => {reset();});
		button(__("Full Screen"), "expand", () => {
			if (dialog) {dialog.hide(); return;}
			dialog = new frappe.ui.Dialog({title: current.file_name, size: "extra-large", fields: [{fieldname: "document", fieldtype: "HTML"}],
				onhide: () => {host.appendTo(wrapper); dialog = null;}});
			dialog.$wrapper.addClass("pearnly-preview-dialog");
			dialog.$wrapper.one("hidden.bs.modal.pearnly-viewer", function() {$(this).remove();});
			host.appendTo(dialog.fields_dict.document.$wrapper); dialog.show();
		});
		const apply = () => {
			image[0].style.transform = `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale}) rotate(${rotation}deg)`;
			zoom.text(`${Math.round(scale * 100)}%`);
		};
		const reset = () => {scale = 1; tx = ty = rotation = 0; apply();};
		const update = () => {
			controls.forEach(control => control.$input.prop("disabled", !loaded));
			paging.toggle(pages > 1); page_number.text(`${page} / ${pages}`);
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
				const result = await load(row, number);
				if (!alive || version !== revision) return;
				pages = result.pages || 1;
				image.attr({src: result.image, alt: row.file_name});
				await image[0].decode();
				if (!alive || version !== revision) return;
				loaded = true; status.hide(); image.show(); update();
				on_page?.(row, page, pages);
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
