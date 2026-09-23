frappe.provide("erpnext.PointOfSale");

frappe.pages["point-of-sale"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Point of Sale"),
		single_column: true,
		hide_sidebar: true,
	});

	frappe.require("point-of-sale.bundle.js", function () {
		wrapper.pos = new erpnext.PointOfSale.Controller(wrapper);
		window.cur_pos = wrapper.pos;
	});
};

frappe.pages["point-of-sale"].refresh = function (wrapper) {
	if (document.scannerDetectionData) {
		onScan.detachFrom(document);
		wrapper.pos.wrapper.html("");
		wrapper.pos.check_opening_entry();
	}
};
/* 官方收银台入口（原样搬入，2026-09-23）。
 * 来源：erpnext 16.34.1 `selling/page/point_of_sale/point_of_sale.js`。
 * 许可：ERPNext 为 GPLv3；本文件是它的副本，随本 App 分发时按 GPLv3 处理。
 * 约定：这棵 `pos/` 目录下的 `official-*` 文件**保持与上游一致**（便于以后比对/升级）；
 *       要改行为请在**对接层**（`desk/pos/pearnly-pos.js` 之类的适配文件）里改，或在本文件
 *       顶部以 `// [pearnly] …` 注明改动，方便下一轮对照上游。
 */
