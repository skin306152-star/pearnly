/* 产品站点的路由、页头入口与全局快捷键白名单（三产品共用；回归方案 A 节，主会话 10-26 / 10-27 审定）。
 *
 * 声明只有一份：各产品 `hooks.py` 里 `pearnly_navigation` 的 `routes`（除我们自己的页面外允许打开的框架视图）、
 * `actions`（每页允许的页头入口，语义码）与 `shortcuts`（允许的全局快捷键），经启动数据下发为
 * `boot.pearnly_routes` / `boot.pearnly_page_actions` / `boot.pearnly_shortcuts`。三处读它：
 *   · 这里（运行时）：路由先过白名单——我们自己的页面（`pearnly-*`）、本人个人页、声明过的框架视图（按标准路由前两段，
 *     如 `query-report/ERP Stock Count Variance`）照常打开；其余框架路由（联系人、数据面板、文件、报表、工作区、
 *     各 DocType 原生列表 / 表单、别人的用户页……）不渲染，原地换成产品首页。
 *   · 这里（运行时）：产品站点上 `frappe.ui.Page` 加页头按钮 / 菜单项 / 下拉 / 图标的入口都先过白名单——
 *     我们自己注册的页面（`p.register` 建的，`page.pearnly_owned`）照常加，代码里的每个调用由 `scripts/check-entries.py` 核对已声明；
 *     框架原生视图（表单、列表、报表、工作区）只放行声明里该路由（当前路由前两段，如 `Form/User`）列出的，其余根本不加。
 *     全局快捷键：挂在我们自己页面上的照常，其余只留声明过的（搜索栏、全局搜索、快捷键表、清缓存重载、控制台、换主题……都不注册）。
 *   · `scripts/check-entries.py`（CI）、页面快照比对（回归方案 C 节）。
 * 放行的页头入口都带 `data-pearnly-declared`，冒烟据此判「页头只有登记过的按钮」；能对上声明的再带 `data-pearnly-action=<码>`，
 * 页面快照按码存、按码比对（三种界面语言共用一份基线，与验收窗口 10-26 对齐）。
 * 起因：10-26 testcowork 管理员账号带上了 System Manager，个人页露出原生「权限」「创建用户电子邮件」（原来只摘了「重置密码」）。
 */
(() => {
	if (!frappe.boot?.pearnly_product || !frappe.ui?.Page) return;
	const MARK = "data-pearnly-declared";
	const Page = frappe.ui.Page.prototype;

	// 路由：两道。`route()` 一进来先按原始地址的第一段判（不放行的连解析都不做——解析会先拉 DocType 定义，
	// 没权限的在那里就抛错，走不到渲染）；解析成标准路由后渲染前再按前两段精确判（`render`）。不放行的不画、
	// 换成首页（替换历史，不留回退）。Administrator 是框架超管（服务端同样放过它），照原生。
	const router = frappe.router;
	if (router?.route && router?.render) {
		const declared = () => frappe.boot.pearnly_routes || [];
		const profile_allowed = () => frappe.boot.pearnly_profile_allowed !== false;
		const own_profile = route => route[0] === "Form" && route[1] === "User" && route[2] === frappe.session.user;
		const allowed_route = (route = []) => {
			const head = String(route[0] || "");
			if (!head || head.startsWith("pearnly-")) return true;
			if (own_profile(route)) return profile_allowed();
			return declared().includes(route.slice(0, 2).join("/"));
		};
		// 原始地址第一段：`query-report/<报表>` 原样，`Form/<表>` / `List/<表>` 是表名的 slug（`/desk/user/<账号>`）
		const allowed_head = head => !head || head.startsWith("pearnly-") || (head === "user" && profile_allowed())
			|| declared().some(item => {
				const [view, name] = item.split("/");
				return head === (["Form", "List", "Tree", "Report"].includes(view) ? router.slug(name) : view);
			});
		const landing = () => String(frappe.boot.pearnly_landing_url || "/desk/pearnly-home");
		const exempt = () => frappe.session.user === "Administrator" || window.location.pathname === landing();
		const to_landing = () => {
			frappe.route_flags.replace_route = true;
			frappe.set_route(landing().replace(/^\/desk\/?/, ""));
		};
		const native_route = router.route;
		router.route = function (...args) {
			if (!exempt() && !allowed_head(String(this.get_sub_path() || "").split("/")[0])) return to_landing();
			return native_route.apply(this, args);
		};
		const native_render = router.render;
		router.render = function (...args) {
			if (exempt() || allowed_route(this.current_route)) return native_render.apply(this, args);
			to_landing();
		};
	}

	// 当前路由的前两段：`Form/User`、`query-report/ERP Stock Count Variance`
	// （原生表单的页容器只记 doctype，不能拿它判；切走之后才到的按钮按新路由判，宁可不加）
	const route = () => frappe.get_route?.() || [];
	const key_of = () => route().slice(0, 2).join("/");
	// 标签对上的声明码（没对上回空串）
	const code_of = label => {
		const actions = frappe.boot.pearnly_page_actions || {};
		// 原生视图按前两段（`Form/User`）；我们自己的详情页（`pearnly-sales/SD-1`）退到第一段找
		const allowed = [...(actions[key_of()] || actions[route()[0]] || []), ...(actions["*"] || [])];
		const text = String(label ?? "").trim();
		return allowed.find(code => code === text || __(code) === text) || "";
	};
	const mark = (element, code) => {
		if (!element?.jquery) return element;
		const targets = element.add(element.find("a, button")).attr(MARK, "1");
		if (code) targets.attr("data-pearnly-action", code);
		return element;
	};
	// 包一层：自己的页面、或声明过的入口照原生加并打标记；否则不加，回一个空的 jQuery（调用方照样能链式调用）
	const guard = (method, label_of) => {
		const native = Page[method];
		if (typeof native !== "function") return;
		Page[method] = function (...args) {
			const code = code_of(label_of(args));
			if (this.pearnly_owned || code) return mark(native.apply(this, args), code);
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
