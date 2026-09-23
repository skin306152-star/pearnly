/* 选择框里的「新建 / 高级搜索」：**留在我们自己的产品里**。
 *
 * 官方 Link 控件的下拉默认带两项 ——「新建 X」进官方快速录入（或官方表单），「高级搜索」
 * 进官方选择器；两者都会把用户带到我们没有的功能上（用户 2026-09-23："不要把用户带到
 * 官方原生的地方去了……不应该带到我们没有的功能"）。这里把这两项接到**我们自己那一页**：
 *
 *   · 产品在 `hooks.py` 的 `pearnly_navigation.links` 里声明"这个主档是我们哪一页"，
 *     启动数据读回来（`frappe.boot.pearnly_links`）—— 前端不再各写一份映射（ADR-009）；
 *   · 没声明的 doctype 一切照官方；我们的表单对这类字段本来就用 `only_select` 不给入口，
 *     所以用户不会掉进官方表单；
 *   · 「新建」跳过去之后，由目标页**自动开它自己的新建弹窗**（`p.take_create`）：
 *     跳过去还要用户再点一次，不算"点新建"。
 *
 * 另外一条数据规矩也放在这里（一处规则、全站生效）：**仓库 / 收银台按当前公司联动**
 * （`p.link_query_default`）。仓库属于公司（一租户多公司），别家的仓选出来只会得到空列表。
 *
 * 下拉浮层的几何、白底、深色文字仍归官方组件，我们只换强调色（见 `product.css`）。
 */
(() => {
	const p = frappe.pearnly;
	const NativeLink = frappe.ui.form.ControlLink;
	if (!NativeLink || NativeLink.pearnly_link) return;

	// ── 我们自己的页面：声明在产品的 `pearnly_navigation.links` 里 ──────────────
	p.link_target = doctype => (frappe.boot.pearnly_links || {})[doctype] || "";

	// 「新建」的去处。跳过去之前先把"到了那边要开新建弹窗"记下（one-shot，带时限：
	// 目标页没渲染成功时不能留着，否则下次进那一页会莫名弹出新建）。
	p.pending_create = null;
	p.PENDING_CREATE_TTL_MS = 60000;
	p.open_link_create = doctype => {
		const route = p.link_target(doctype);
		if (!route) return false;
		p.pending_create = {route, doctype, at: Date.now()};
		frappe.set_route(route);
		return true;
	};
	// 目标页自己的收尾：对上了就开它自己的新建（`open()` 由那一页给），取走即清。
	p.take_create = async (route, doctype, open) => {
		const pending = p.pending_create;
		if (!pending || pending.route !== route || pending.doctype !== doctype) return false;
		p.pending_create = null;
		if (Date.now() - Number(pending.at || 0) > p.PENDING_CREATE_TTL_MS) return false;
		await open();
		return true;
	};

	// ── 数据联动：公司的仓库 / 收银台 ────────────────────────────────────────
	// 只在这一条关系上兜底（没写 `get_query` 的字段也吃得到）；调用方自己写了就以它为
	// 准 —— 各页要加别的条件（如 `is_active`）仍照写。筛选弹层里的 Link 控件是同一个
	// 控件类，所以"筛选里的仓库"也自动跟着当前公司，不用另写一份。
	p.link_query_default = doctype => {
		const company = frappe.boot.pearnly_company || "";
		if (!company) return null;
		return ["ERP Warehouse", "ERP POS Terminal"].includes(doctype)
			? {filters: {company}} : null;
	};

	class ControlLink extends NativeLink {
		make_input() {
			// 默认联动要在**取数之前**挂上（控件的搜索读 `df.get_query`）。
			if (this.df && !this.df.get_query) {
				const query = p.link_query_default(this.df.options);
				if (query) this.df.get_query = query;
			}
			super.make_input();
		}

		// 官方那两项的兜底：万一入口还在（字段没设 `only_select`），动作也必须落在我们自己的页面上。
		new_doc() {
			if (p.open_link_create(this.get_options())) return false;
			return super.new_doc();
		}

		open_advanced_search() {
			const route = p.link_target(this.get_options());
			if (route) {
				frappe.set_route(route);
				return false;
			}
			return super.open_advanced_search();
		}

		// 下拉里那两项是官方在 `on_input` 的回调里拼好的，拼完紧接着调 `toggle_href`
		// （`link.js`：`this.awesomplete.list = cache[doctype][term]; this.toggle_href(doctype);`）。
		// 官方没给别的插槽 —— `ControlLink.link_options` 钩子只在 `!only_select` 时才被调用，
		// 而我们的字段恰恰都用 `only_select`，所以借这个点把我们那一项换上去。
		toggle_href(doctype) {
			super.toggle_href(doctype);
			this.pearnly_link_options(doctype);
		}

		pearnly_link_options(doctype) {
			const route = p.link_target(doctype);
			const aw = this.awesomplete;
			// ⚠️ 这个 awesomplete 的 `list` 是**只写**属性：`set list(list) { this._list = list; … }`
			// （`awesomplete.js`），没有 getter —— 读 `aw.list` 只会拿到 `undefined`，真正的
			// 那份在 `_list` 里。所以这里读 `_list`、写仍走 `list`（写进去它会自己重画）。
			const list = aw && (Array.isArray(aw._list) ? aw._list : null);
			if (!route || !list) return;
			// 官方那两项（去官方表单 / 官方选择器）一律拿掉，换成我们自己的；
			// 文案与图标照官方那一条写，用户看到的东西不变，变的只是去处。
			const kept = list.filter(item => !["create_new__link_option",
				"advanced_search__link_option"].includes(item && item.value));
			// 少数格子明确不要这个入口（收银台的客户搜索框：跳走会把正在开的单丢掉）——
			// 那种格子上连官方那两项也一并去掉，用户不会被带到别处。
			const offers_create = !(this.df && this.df.pearnly_no_create);
			if (offers_create && frappe.model.can_create(doctype)) {
				kept.push({
					html: "<span class='link-option pearnly-link-option'>" +
						"<i class='fa fa-plus' style='margin-right: 5px;'></i> " +
						__("Create a new {0}", [__(doctype)]) + "</span>",
					label: __("Create a new {0}", [__(doctype)]),
					// 值以 `__link_option` 结尾：官方选中后会据此把输入框清空（`link.js`）。
					value: "pearnly_create__link_option",
					action: () => p.open_link_create(doctype),
				});
			}
			aw.list = kept;
		}
	}
	ControlLink.pearnly_link = true;
	frappe.ui.form.ControlLink = ControlLink;
})();
