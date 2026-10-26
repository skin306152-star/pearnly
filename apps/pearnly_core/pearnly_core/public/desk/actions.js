/* 产品页页头入口与全局快捷键白名单（三产品共用；回归方案 A 节，主会话 10-26 审定）。
 *
 * 声明只有一份：各产品 `hooks.py` 里 `pearnly_navigation` 的 `actions`（每页允许的页头入口，语义码）与 `shortcuts`
 * （允许的全局快捷键），经启动数据下发为 `boot.pearnly_page_actions` / `boot.pearnly_shortcuts`。三处读它：
 *   · 这里（运行时）：产品站点上 `frappe.ui.Page` 加页头按钮 / 菜单项 / 下拉 / 图标的入口都先过白名单——
 *     我们自己注册的页面（`p.register` 建的，`page.pearnly_owned`）照常加，代码里的每个调用由 `scripts/check-entries.py` 核对已声明；
 *     框架原生视图（表单、列表、报表、工作区）只放行声明里该路由（当前路由前两段，如 `Form/User`）列出的，其余根本不加。
 *     全局快捷键：挂在我们自己页面上的照常，其余只留声明过的（搜索栏、全局搜索、快捷键表、清缓存重载、控制台、换主题……都不注册）。
 *   · `scripts/check-entries.py`（CI）、页面快照比对（回归方案 C 节）。
 * 放行的页头入口都带 `data-pearnly-declared`，冒烟据此判「页头只有登记过的按钮」。
 * 起因：10-26 testcowork 管理员账号带上了 System Manager，个人页露出原生「权限」「创建用户电子邮件」（原来只摘了「重置密码」）。
 */
(() => {
	if (!frappe.boot?.pearnly_product || !frappe.ui?.Page) return;
	const MARK = "data-pearnly-declared";
	const Page = frappe.ui.Page.prototype;

	// 当前路由的前两段：`Form/User`、`query-report/ERP Stock Count Variance`
	// （原生表单的页容器只记 doctype，不能拿它判；切走之后才到的按钮按新路由判，宁可不加）
	const key_of = () => (frappe.get_route?.() || []).slice(0, 2).join("/");
	const declared = label => {
		const actions = frappe.boot.pearnly_page_actions || {};
		const allowed = [...(actions[key_of()] || []), ...(actions["*"] || [])];
		const text = String(label ?? "").trim();
		return allowed.some(code => code === text || __(code) === text);
	};
	const mark = element => {
		if (element?.jquery) element.add(element.find("a, button")).attr(MARK, "1");
		return element;
	};
	// 包一层：自己的页面、或声明过的入口照原生加并打标记；否则不加，回一个空的 jQuery（调用方照样能链式调用）
	const guard = (method, label_of) => {
		const native = Page[method];
		if (typeof native !== "function") return;
		Page[method] = function (...args) {
			if (this.pearnly_owned || declared(label_of(args))) return mark(native.apply(this, args));
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

	// 全局快捷键：框架在加载时已注册了一批（`ui/keyboard.js`），先把没声明的注销；之后注册的同样过白名单
	const keys = frappe.ui.keys;
	if (!keys?.add_shortcut) return;
	const allowed_key = (shortcut, page) => Boolean(page?.pearnly_owned)
		|| (frappe.boot.pearnly_shortcuts || []).includes(String(shortcut || "").toLowerCase());
	for (const item of [...(keys.standard_shortcuts || [])]) {
		if (allowed_key(item.shortcut, item.page)) continue;
		keys.off?.(item.shortcut, item.page);
		keys.standard_shortcuts.splice(keys.standard_shortcuts.indexOf(item), 1);
	}
	const add_shortcut = keys.add_shortcut;
	keys.add_shortcut = (options = {}) => allowed_key(options.shortcut, options.page) ? add_shortcut(options) : undefined;
})();
