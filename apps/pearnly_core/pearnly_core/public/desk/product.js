/* Native Desk helpers: no cloned widgets, no Vue/Tailwind inside Desk. */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	p.call = async (method, args = {}, type = "GET") =>
		(await frappe.call({ method, args, type })).message;
	p.escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	// 数量与金额**按实际值**显示：不留固定的尾随 0（8.000 → 8，3.500 → 3.5，฿190.00 → ฿190）。
	// 用户 2026-09-22 定："那个数量后面留两个 0 就好了或者不要固定，自动填……按实际显示"。
	// 上限仍然守着（数量 3 位、金额 2 位），免得浮点尾巴漏到界面上。
	// 分组与小数分隔符交给框架自己的 `format_number` / `format_currency`，这里只算"要几位"。
	const decimals_of = (value, max) => {
		const number = Number(value);
		if (!isFinite(number)) return null;
		const fraction = Math.abs(number).toFixed(max).split(".")[1] || "";
		return fraction.replace(/0+$/, "").length;
	};
	p.number = (value, max = 3) => {
		if (value === null || value === undefined || value === "") return "";
		const decimals = decimals_of(value, max);
		return decimals === null ? String(value) : format_number(value, null, decimals);
	};
	p.money = (value, currency = "THB", max = 2) => {
		if (value === null || value === undefined || value === "") return "";
		const decimals = decimals_of(value, max);
		return decimals === null ? String(value) : format_currency(value, currency, decimals);
	};
	p.add_refresh = (page, refresh, label = __("Reload")) =>
		page.add_action_icon("es-line-reload", refresh, "", label)
			.removeClass("btn-default")
			.attr({type: "button", "aria-label": label});
	p.texts = async (prefix) => {
		const pack = await p.call("pearnly_core.browser_text.browser_text", { prefix });
		const lang = (frappe.boot.lang || frappe.boot.user.language || "th").slice(0, 2);
		return (key, ...args) => {
			const text = pack[key]?.[lang] || pack[key]?.th || key;
			return text.replace(/\{(\d+)\}/g, (_, i) => String(args[Number(i)] ?? ""));
		};
	};
	p.success = (message) => {
		frappe.show_alert({ message: p.escape(message), indicator: "green" });
		frappe.utils.play_sound("submit");
	};
	p.dialog = (title, fields, label, action) => {
		const d = new frappe.ui.Dialog({
			title,
			fields,
			primary_action_label: label,
			async primary_action(values) {
				if (d.pearnly_busy) return;
				d.pearnly_busy = true;
				d.disable_primary_action();
				try {
					await action(values, d);
				} catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally {
					d.pearnly_busy = false;
					d.enable_primary_action();
				}
			},
		});
		d.show();
		return d;
	};
	p.layout = (parent, fields) => {
		const form = new frappe.ui.FieldGroup({ body: parent, fields });
		form.make();
		return form;
	};
	p.table = async (parent, columns, data, options = {}) => {
		await frappe.require("report.bundle.js");
		parent.empty();
		if (!data.length) {
			parent.append(frappe.views.QueryReport.prototype.get_no_result_message());
			return { destroy: () => parent.empty() };
		}
		const table = new window.DataTable(parent[0], {
			columns: columns.map((column) =>
				typeof column === "string"
					? { name: column, editable: false, format: p.escape }
					: { editable: false, ...column },
			),
			data,
			layout: "fluid",
			noDataMessage: __("No Data"),
			serialNoColumn: false,
			checkboxColumn: false,
			language: frappe.boot.lang,
			translations: frappe.utils.datatable.get_translations(),
			...options,
		});
		return p.table_menu(table);
	};
	// 列头菜单只留排序三项。官方 datatable 默认还挂"移除栏"和"冻结/解冻"，本项目表格是
	// **只读展示、列由代码定义**：少一列 / 冻一列都不是业务动作，留着只是误导（用户
	// 2026-09-22 口径：没用就删）。做法是沿用官方自己的 `display` 通道 + 关掉粘性列开关，
	// 不改插件源码、不动排序逻辑。
	p.table_menu = table => {
		const items = table?.options?.headerDropdown || [];
		const nodes = table?.dropdownContainer?.firstElementChild?.children || [];
		items.forEach((item, index) => {
			const removable = item.stickyAction === "stick" || item.stickyAction === "unstick"
				|| /\bremoveColumn\b/.test(String(item.action || ""));
			if (!removable) return;
			item.display = "hidden";
			nodes[index]?.classList.add("dt-hidden");
		});
		// 冻结/解冻两项的显隐由列管理组件在"打开菜单"时按列状态切换（`updateStickyDropdownItems`），
		// 所以要把**它**上面这两个索引置 -1（放在 DataTable 上没用：那里没有这两个字段，
		// 实测会白写，菜单里 Freeze 照样冒出来）。
		const columns = table.columnmanager;
		if (columns) columns.stickDropdownIndex = columns.unstickDropdownIndex = -1;
		return table;
	};
	// 弹窗里一排摆几个字段：默认两个。字段少就一排，多了自动换到第二排 —— 一列到底的
	// 竖排会把"上面那几行"拉得又长又空（用户 2026-09-22 口径）。整行控件（HTML/Table/
	// Button、Section）自己占一行，不参与并排。
	p.field_rows = (fields, columns = 2) => {
		const rows = [];
		let filled = 0;
		const end_row = () => { if (filled) { rows.push({ fieldtype: "Column Break" }); filled = 0; } };
		for (const field of fields) {
			const type = field.fieldtype;
			if (type === "Section Break" || type === "HTML" || type === "Table" || type === "Button") {
				end_row(); rows.push(field); continue;
			}
			if (type === "Column Break") { rows.push(field); filled = 0; continue; }
			rows.push(field); filled += 1;
			if (filled === columns) end_row();
		}
		return rows;
	};
	p.restore_sidebar = () => {
		if (frappe.boot.pearnly_sidebar && frappe.app?.sidebar?.sidebar_title !== frappe.boot.pearnly_sidebar) {
			return frappe.app.sidebar.setup(frappe.boot.pearnly_sidebar);
		}
	};
	p.register = (name, prefix, title, render) => {
		frappe.pages[name].on_page_show = () => p.restore_sidebar();
		frappe.pages[name].on_page_load = async (wrapper) => {
			const page = frappe.ui.make_app_page({
				parent: wrapper,
				title: __(title),
				single_column: true,
			});
			if (!frappe.boot.pearnly_product || !frappe.boot.pearnly_pages?.includes(name)) {
				page.set_title_sub(__("Not Permitted"), "red");
				return;
			}
			try {
				const t = await p.texts(prefix);
				await render(page, t, wrapper);
			} catch (error) {
				console.error(error);
				page.set_title_sub(__("Unable to load this page. Please reload."), "red");
			}
		};
	};
	p.notBuilt = async (page, t) => {
		page.set_title_sub(p.escape(t("shell.notBuilt")));
	};

	if (frappe.boot.pearnly_product) {
		frappe.re_route[""] = "/desk/pearnly-home";
		// Use the framework's initialization switches: no search/notification widgets or
		// their keyboard targets. A hidden icon alone would leave the global routes active.
		Object.assign(frappe.boot.desk_settings, {
			search_bar: 0, notifications: 0, form_navigation_buttons: 0,
		});
		const NativeHeader = frappe.ui.SidebarHeader;
		frappe.ui.SidebarHeader = class ProductHeader extends NativeHeader {
			make() {
				super.make();
				// Company names are customer data, never translation keys or HTML.
				const company = frappe.boot.pearnly_company_name || "Pearnly";
				this.wrapper.find(".header-title").text(company).attr("title", company);
				const first = [...new Intl.Segmenter(frappe.boot.lang || "th", { granularity: "grapheme" })
					.segment(company)][0].segment.toLocaleUpperCase();
				this.wrapper.find(".header-logo").empty().append(
					$('<div class="icon-container pearnly-company-initial" aria-hidden="true"></div>').text(first),
				);
			}
			add_navbar_items() {
				// 原生顺序：首页、网站、分隔线、侧栏排序、显示、刷新、分隔线、注销。
				// 上游把"显示"和"注销"挂在通知开关上（产品有意关掉全局搜索/通知），
				// 所以这几项在这里显式给出；行为仍交给原生 SidebarHeader、主题切换、
				// clear_cache 与侧栏编辑态，不自建主题或控件。
				// "侧栏排序"按原生放"编辑侧栏"的位置（第一条分隔线之后、显示之前）。
				const is_dark = frappe.ui.get_current_theme() === "dark";
				this.dropdown_items = [
					{ name: "home", label: __("Home"), icon: "home",
						onClick: () => frappe.set_route("/desk/pearnly-home") },
					{ name: "website", label: __("Website"), icon: "web",
						onClick: () => window.open("https://pearnly.com") },
					{ is_divider: true },
					{ name: "reorder-sidebar", label: __("account.sidebarOrder"), icon: "list",
						condition: () => frappe.boot.pearnly_pages.length > 1,
						onClick: () => p.reorder_sidebar() },
					{ name: "display", label: __("Display"), icon: "monitor",
						items: this.get_display_siblings(is_dark) },
					{ name: "reload", label: __("Reload"), icon: "rotate-ccw",
						onClick: () => frappe.ui.toolbar.clear_cache() },
					{ is_divider: true },
					{ name: "logout", label: __("Logout"), icon: "logout",
						onClick: () => frappe.app.logout() },
				];
			}
			populate_dropdown_menu() {
				// 标题里那份菜单是逐项渲染的，原生没有"分隔线行"，照搬会多出一行
				// `undefined`。分隔线交给原生 context menu（同一次点击里的另一份）画，
				// 这里只列真正可点的项。
				const items = this.dropdown_items;
				this.dropdown_items = items.filter((item) => !item.is_divider);
				try {
					super.populate_dropdown_menu();
				} finally {
					this.dropdown_items = items;
				}
			}
		};
	}

	$(document).on("app_ready", () => {
		if (!frappe.boot.pearnly_product) return;
		document.body.classList.add("pearnly-product-desk");
		// app_ready fires inside Application construction, before frappe.app is assigned.
		frappe.Application.prototype.logout = async () => {
			const t = await p.texts("account.");
			frappe.confirm(
				p.escape(t("account.signOutConfirm")),
				async () => {
					const result = await p.call("pearnly_core.identity.sign_out", {}, "POST");
					location.assign(result.redirect_url);
				},
				null,
				t("account.signOut"),
				__("Cancel"),
			);
		};
	});
})();
