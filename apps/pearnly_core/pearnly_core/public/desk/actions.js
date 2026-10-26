/* 产品页右上角 / 菜单里的按钮白名单（三产品共用）。
 *
 * 框架原生视图（表单、列表、报表、工作区）在页头加的按钮、菜单项、下拉、图标，产品没有承接的一律不显示：
 * 产品站点上 `frappe.ui.Page` 加按钮的入口都先过这里——我们自己注册的页面（`p.register` 建的，`page.pearnly_owned`）
 * 照常放行；框架原生视图只放行各产品在 `hooks.py` 的 `pearnly_native_actions` 里声明过的（`boot.pearnly_native_actions`，
 * 键是页面路由的前两段，如 `Form/User`）。放行的按钮都带 `data-pearnly-declared`，冒烟据此判「页头只有登记过的按钮」。
 * 起因：10-26 testcowork 管理员账号带上了 System Manager，个人页露出原生「权限」「创建用户电子邮件」（原来只摘了「重置密码」）。
 */
(() => {
	if (!frappe.boot?.pearnly_product || !frappe.ui?.Page) return;
	const MARK = "data-pearnly-declared";
	const Page = frappe.ui.Page.prototype;

	// 当前路由的前两段：`Form/User`、`List/ERP Serial No`、`query-report/ERP Stock Count Variance`
	// （原生表单的页容器只记 doctype，不能拿它判；切走之后才到的按钮按新路由判，宁可不加）
	const key_of = () => (frappe.get_route?.() || []).slice(0, 2).join("/");
	const declared = (page, label) => {
		const allowed = (frappe.boot.pearnly_native_actions || {})[key_of()] || [];
		const text = String(label ?? "").trim();
		return allowed.some(item => item === text || __(item) === text);
	};
	const mark = element => {
		if (element?.jquery) element.add(element.find("a, button")).attr(MARK, "1");
		return element;
	};
	// 包一层：自己的页面、或声明过的按钮照原生加并打标记；否则不加，回一个空的 jQuery（调用方照样能链式调用）
	const guard = (method, label_of) => {
		const native = Page[method];
		if (typeof native !== "function") return;
		Page[method] = function (...args) {
			if (this.pearnly_owned || declared(this, label_of(args))) return mark(native.apply(this, args));
			return $();
		};
	};
	guard("add_inner_button", args => args[2] || args[0]);  // 分组按钮按组名判（「权限」整组不出）
	guard("add_button", args => args[0]);
	guard("add_menu_item", args => args[0]);
	guard("add_custom_menu_item", args => args[1]);
	guard("add_action_item", args => args[0]);
	guard("add_actions_menu_item", args => args[0]);
	guard("add_custom_button_group", args => args[0]);
	guard("add_action_icon", args => `icon:${args[0]}`);
	guard("set_primary_action", args => args[0]);
	guard("set_secondary_action", args => args[0]);
})();
