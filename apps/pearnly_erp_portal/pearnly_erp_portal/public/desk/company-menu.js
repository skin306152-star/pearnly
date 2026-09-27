/* 切换公司：挂在左上角那颗（原生侧栏标题）的下拉里。
 *
 * 用户 2026-09-23："我要做一租户多公司"。清单与"当前是哪家"由服务端给
 * （`boot.pearnly_companies`，见 `pearnly_erp_portal/boot.py`），切换写的是**用户默认值**
 * —— 就是官方那个 `company` 键（`frappe.defaults.set_user_default`）。所以这是"切我自己
 * 现在在哪家公司干活"，不是改租户配置，不需要管理员。
 *
 * 用的是原生 `SidebarHeader` 自己的 `dropdown_items`（原生菜单里的公司列表），不另画控件：
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

	// Native hover opens the submenu before the first click, which then toggles it closed.
	// Keep the native submenu, but make clicking this shortcut open it consistently.
	p.prepare_header_menu = menu => {
		const index = menu.menu_items_to_show.findIndex(item => item.name === "pearnly-company");
		if (index < 0) return;
		const item = menu.template.children().eq(index);
		item.off("click").on("click", event => {
			event.stopPropagation();
			const child = menu.nested_menus.find(nested => nested.parent.get(0) === item.get(0));
			if (!child) return;
			menu.nested_menus.filter(nested => nested !== child).forEach(nested => nested.hide());
			menu.current_menu = child;
			if (!child.visible) child.show(event);
		});
	};
	p.header_title = () => (frappe.boot.pearnly_companies || []).find(row => row.is_current)?.company_name;
	p.switch_company = name => switch_company(name, false);

	/** 建公司 / 改公司名之后重取「我能用哪些公司、当前是哪家」，左上角公司名当场换掉
	 *  （录制问题 #4：建第一家公司后要刷新页面才出现，公司列表「当前」一栏也先显示「—」）。 */
	p.refresh_company_context = async () => {
		const context = await p.call(api + "api_company_context");
		frappe.boot.pearnly_companies = context.companies || [];
		frappe.boot.pearnly_company = context.current || "";
		const title = p.header_title();
		if (!title) return;
		const header = $(".sidebar-header");
		header.find(".header-title").text(title).attr("title", title);
		const first = [...new Intl.Segmenter(frappe.boot.lang || "th", { granularity: "grapheme" })
			.segment(title)][0]?.segment.toLocaleUpperCase() || "";
		header.find(".pearnly-company-initial").text(first);
	};
	p.header_menu_items = () => {
		const rows = frappe.boot.pearnly_companies || [];
		if (rows.length <= 1) return [];
		return [{name: "pearnly-company", label: __("Company"), icon: "building-2",
			items: rows.map(row => ({name: "pearnly-company-" + row.name,
				label: p.escape(row.company_name) + (row.is_current ? " ✓" : ""),
				onClick: () => switch_company(row.name, row.is_current)}))}];
	};
})();
