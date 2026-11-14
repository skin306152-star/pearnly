// 旧地址「我的收款」（11-11～11-14）：已发出的 LINE 确认卡【编辑】指向 /desk/pearnly-my-collections/<记录>。11-14 起只有「收款」一页，
// 这里原样转过去（带记录号就打开那一笔），不留历史这一步。
frappe.pages["pearnly-my-collections"].on_page_show = () => {
	const rest = frappe.get_route().slice(1);
	frappe.set_route(["pearnly-collections", ...rest]);
};
frappe.pages["pearnly-my-collections"].on_page_load = () => {};
