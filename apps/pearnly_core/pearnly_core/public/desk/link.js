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
	// 跳走之前把**发起这次跳转的那张弹窗**收起来：框架的弹窗不跟着路由消失，
	// 不关的话用户到了我们的页面，屏上还压着原来那张（实测：箭头跳过去之后
	// 「新建物料」还盖在上面）。用的就是框架 `Dialog.hide()` 那一句 —— `$wrapper.modal("hide")`。
	p.dismiss_modal_of = $from => {
		const modal = $from && $from.closest && $from.closest(".modal");
		if (modal && modal.length) modal.modal("hide");
	};
	p.open_link_create = (doctype, $from) => {
		const route = p.link_target(doctype);
		if (!route) return false;
		p.dismiss_modal_of($from);
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

	// 选择框右边那颗**箭头**（官方叫 "Open Link"）本来指向官方表单
	// （`frappe.utils.get_form_link` → `/desk/erp-warehouse/WH-00101`）—— 用户
	// 2026-09-23："图二那个箭头按钮会带到别的地方……这个界面根本不是我们自己的"。
	// 所以箭头也回我们自己那一页，并由那一页打开这条记录（`p.take_open`，同 create 那套）。
	p.pending_open = null;
	p.open_link_record = (doctype, name, $from) => {
		const route = p.link_target(doctype);
		if (!route || !name) return false;
		p.dismiss_modal_of($from);
		p.pending_open = {route, doctype, name, at: Date.now()};
		frappe.set_route(route);
		return true;
	};
	p.take_open = async (route, doctype, open) => {
		const pending = p.pending_open;
		if (!pending || pending.route !== route || pending.doctype !== doctype) return false;
		p.pending_open = null;
		if (Date.now() - Number(pending.at || 0) > p.PENDING_CREATE_TTL_MS) return false;
		await open(pending.name);
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

	// ── 扫码：选择框右侧那颗「扫码」按钮 ─────────────────────────────────────
	// 用户 2026-09-23："把全局所有用到选择物料的选项的地方，全部加图二那个组件，扫码组件，
	// 扫到匹配后自动带出"。
	//
	// 共用层**只负责把按钮放上去**（形状照官方 `Data` + `options:"Barcode"` 那颗：
	// 输入框右侧一颗灰图标，见 `product.css`）；动作由产品给 —— 产品声明 `p.scan_doctypes`
	// （哪些 Link 要带扫码）并实现 `p.scan_link(control)`。共用层不知道"物料"是什么，
	// 也不掺产品逻辑（产品逻辑进产品 App）。
	p.scan_doctypes = null;
	p.scan_link = null;
	p.attach_scan_btn = control => {
		const doctype = control.get_options ? control.get_options() : "";
		if (!doctype || !Array.isArray(p.scan_doctypes) || !p.scan_doctypes.includes(doctype)) return;
		if (typeof p.scan_link !== "function" || control.pearnly_scan_btn) return;
		// 只读的格子（查看一张已提交的单据）没有"扫一个进来"这回事。
		if (control.df?.read_only) return;
		// 筛选弹层里那一格不上扫码：那不是"选物料"的动作，格子也太窄。
		if (control.$input.closest(".filter-area").length) return;
		// 表单/弹窗里是 `.control-input`；**网格单元里没有这一层**（`.link-field > .awesomplete > input`），
		// 那就挂在输入框的直接父元素上 —— 皮肤里的 `.pearnly-link-scan-input` 会给它 `position: relative`，
		// 两种情况按钮都在输入框右端。
		const input_holder = control.$input.closest(".control-input");
		const holder = input_holder.length ? input_holder : control.$input.parent();
		if (!holder.length) return;
		const label = __("Scan");
		const button = $(`<button type="button" class="pearnly-link-scan" title="${label}"
			aria-label="${label}">${frappe.utils.icon("scan-barcode", "sm")}</button>`);
		holder.addClass("pearnly-link-scan-input").append(button);
		button.on("click", event => {
			event.preventDefault();
			event.stopPropagation();
			void p.scan_link(control);
		});
		control.pearnly_scan_btn = button;
	};

	class ControlLink extends NativeLink {
		make_input() {
			// 默认联动要在**取数之前**挂上（控件的搜索读 `df.get_query`）。
			if (this.df && !this.df.get_query) {
				const query = p.link_query_default(this.df.options);
				if (query) this.df.get_query = query;
			}
			super.make_input();
			// 选择框右侧那颗扫码按钮（产品声明了才放）。
			p.attach_scan_btn(this);
		}

		// 官方那两项的兜底：万一入口还在（字段没设 `only_select`），动作也必须落在我们自己的页面上。
		new_doc() {
			if (p.open_link_create(this.get_options(), this.$input)) return false;
			return super.new_doc();
		}

		open_advanced_search() {
			const route = p.link_target(this.get_options());
			if (route) {
				p.dismiss_modal_of(this.$input);
				frappe.set_route(route);
				return false;
			}
			return super.open_advanced_search();
		}

		// 箭头那颗按钮：官方在这里写的是官方表单地址，我们改成自己那一页 + 打开这条记录。
		show_link_and_clear_buttons() {
			super.show_link_and_clear_buttons();
			// 明确"不要跳走"的格子（收银台的客户搜索框）：连官方那颗箭头也藏掉 —— 它指向
			// 官方表单，点了不但出了我们的界面，还会把正在开的这一单丢掉。
			if (this.df && this.df.pearnly_no_create) {
				this.$link_open && this.$link_open.hide();
				return;
			}
			const route = p.link_target(this.get_options());
			const name = this.get_input_value();
			if (!route || !name || !this.$link_open) return;
			this.$link_open
				.attr("href", "/desk/" + route)
				.off("click.pearnly")
				.on("click.pearnly", event => {
					event.preventDefault();
					p.open_link_record(this.get_options(), name, this.$input);
				});
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
					action: () => p.open_link_create(doctype, this.$input),
				});
			}
			aw.list = kept;
		}
	}
	ControlLink.pearnly_link = true;
	frappe.ui.form.ControlLink = ControlLink;
})();
