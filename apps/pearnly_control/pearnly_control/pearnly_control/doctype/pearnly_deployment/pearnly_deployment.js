// 部署单上的「配 LINE 渠道」入口。
//
// 为什么放在这里：用户 2026-09-19 问的就是"我怎么给它分配 LINE" —— 渠道是
// **按部署一条**（一个租户一个产品一条），单据名由部署算出来。从部署单直接
// 进去，就不会有人去列表里挑一个不知道对应谁的渠道。
//
// 文案一律 `__()` + 英文 msgid（翻译在 pearnly_control/translations/*.csv），
// 这里不写死任何语言。

frappe.ui.form.on("Pearnly Deployment", {
	refresh(frm) {
		if (frm.is_new()) return;
		frappe.db
			.get_value("Pearnly LINE Channel", { deployment: frm.doc.name }, "name")
			.then((r) => {
				const existing = r && r.message && r.message.name;
				frm.add_custom_button(
					__(existing ? "Open the LINE channel" : "Set up the LINE channel"),
					() => {
						if (existing) {
							frappe.set_route("Form", "Pearnly LINE Channel", existing);
						} else {
							frappe.new_doc("Pearnly LINE Channel", { deployment: frm.doc.name });
						}
					}
				);
			});
	},
});
