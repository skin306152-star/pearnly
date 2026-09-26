/* 个人信息页（原生 User 表单，本人）里的「LINE」一块（Cowork）：开了 LINE 的账号在这里
 * 看二维码、Bot ID 与绑定码，绑上之后看 LINE 名称并可解绑。个人信息页本身归共用层，
 * 这一块由 Cowork 自己的 `doctype_js` 追加。开没开由服务端判（`boot.pearnly_line`）。
 */
frappe.ui.form.on("User", {
	refresh(frm) {
		frm.$wrapper.find(".pearnly-line-profile").remove();
		if (!frappe.boot.pearnly_product || !frappe.boot.pearnly_line) return;
		if (frm.doc.name !== frappe.session.user || frm.is_new()) return;
		// 块体不用原生的 `.section-body`：那是横向排列的容器，里面再放一组字段会被压成窄条。
		const block = $(`<div class="pearnly-line-profile form-section card-section">
			<div class="section-head">LINE</div><div class="pearnly-line-body"></div></div>`)
			.appendTo(frm.layout.wrapper);
		void frappe.pearnly.line_binding(block.find(".pearnly-line-body"));
	},
});
