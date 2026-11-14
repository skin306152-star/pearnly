// 旧地址「我的收款」（11-11～11-14）：已发出的 LINE 确认卡【编辑】指向 /desk/pearnly-my-collections/<记录>。11-14 起只有「收款」一页，
// 能进「收款」的人原样转过去（带记录号就打开那一笔）；进不了的给框架原生的无权限页（同 `p.register`），不转。
frappe.pages["pearnly-my-collections"].on_page_load = () => {};
frappe.pages["pearnly-my-collections"].on_page_show = () => {
	if (!frappe.boot.pearnly_pages?.includes("pearnly-collections")) {
		frappe.show_not_permitted("pearnly-my-collections");
		return;
	}
	frappe.set_route(["pearnly-collections", ...frappe.get_route().slice(1)]);
};
