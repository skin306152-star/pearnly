/* 切换公司：挂在左上角那颗（原生侧栏标题）的下拉里。
 *
 * 用户 2026-09-23："我要做一租户多公司"。清单与"当前是哪家"由服务端给
 * （`boot.pearnly_companies`，见 `pearnly_erp_portal/boot.py`），切换写的是**用户默认值**
 * —— 就是官方那个 `company` 键（`frappe.defaults.set_user_default`）。所以这是"切我自己
 * 现在在哪家公司干活"，不是改租户配置，不需要管理员。
 *
 * 用的是原生 `SidebarHeader` 自己的 `dropdown_items`（原生菜单里那一组嵌套项），不另画控件：
 * 只有一家公司时整组不出现。切换后整页重载 —— 页面上的列表、仓库、单号都跟着这家公司走。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.masters.api.";

	async function switch_company(name, is_current) {
		if (is_current) return;
		frappe.dom.freeze(__("Switching company..."));
		try {
			await p.call(api + "api_set_active_company", {name}, "POST");
		} finally {
			frappe.dom.unfreeze();
		}
		window.location.reload();
	}

	const NativeHeader = frappe.ui.SidebarHeader;
	frappe.ui.SidebarHeader = class ProductCompanyHeader extends NativeHeader {
		// 原生构造函数第一句就调它，然后才建菜单 —— 插在这里，菜单与列表两处都会有。
		add_navbar_items(...args) {
			super.add_navbar_items(...args);
			const rows = frappe.boot.pearnly_companies || [];
			if (rows.length <= 1) return;
			this.dropdown_items.unshift({
				name: "pearnly-company",
				label: __("Company"),
				icon: "building-2",
				items: rows.map(row => ({
					name: "pearnly-company-" + row.name,
					label: row.is_current ? `${row.company_name} ✓` : row.company_name,
					onClick: () => switch_company(row.name, row.is_current),
				})),
			});
		}
	};
})();
