/* Native Desk helpers: no cloned widgets, no Vue/Tailwind inside Desk. */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	p.call = async (method, args = {}, type = "GET") =>
		(await frappe.call({ method, args, type })).message;
	p.escape = (value) => frappe.utils.escape_html(String(value ?? ""));
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
	p.table = async (parent, columns, data) => {
		await frappe.require("report.bundle.js");
		parent.empty();
		if (!data.length) {
			parent.append(frappe.views.QueryReport.prototype.get_no_result_message());
			return { destroy: () => parent.empty() };
		}
		return new window.DataTable(parent[0], {
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
		});
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

		// 手机上的**导航菜单**不能"要点两下"（用户 2026-09-21 报障，本地 iPhone 视口复现）：
		// 原生 `Sidebar.set_height()` 会给 body 加滚动锁（`overflow: hidden`）但**从不释放**，
		// 而且手机上从菜单里选完页面后侧栏仍然盖在上面 —— 用户得再点一下（或点遮罩）才能
		// 看到刚打开的页面，用起来就是"点了两下"。这里只做两处原生组合：
		//   ① 关侧栏时释放那把滚动锁；② 手机点菜单项后自动收起侧栏。
		const is_mobile = () => Boolean(frappe.is_mobile && frappe.is_mobile());
		const native_sidebar = frappe.ui && frappe.ui.Sidebar;
		if (native_sidebar && native_sidebar.prototype && !native_sidebar.prototype.pearnly_mobile_close) {
			native_sidebar.prototype.pearnly_mobile_close = true;
			const native_close = native_sidebar.prototype.close;
			native_sidebar.prototype.close = function () {
				native_close && native_close.call(this);
				if (is_mobile() && document.body) {
					document.body.style.overflow = "";
				}
			};
		}
		const close_mobile_sidebar = () => {
			const sidebar = frappe.app && frappe.app.sidebar;
			if (is_mobile() && sidebar && sidebar.sidebar_expanded) sidebar.close();
		};
		// 路由变了（含返回键、程序化跳转）就把菜单收起来。
		frappe.router?.on?.("change", close_mobile_sidebar);
		if (typeof document.addEventListener === "function") {
			// 点菜单里的页面：**先让链接把路由走完**再收菜单，别抢在默认动作前面。
			document.addEventListener(
				"click",
				(event) => {
					if (!is_mobile()) return;
					const target = event.target;
					if (!target || typeof target.closest !== "function") return;
					if (!target.closest(".body-sidebar")) return;
					if (!target.closest(".standard-sidebar-item, .sidebar-item-container")) return;
					window.setTimeout(close_mobile_sidebar, 0);
				},
				true,
			);
			// iOS 会把第一次点击当成 hover 用掉（先出现灰底、第二下才跳），侧栏项因此
			// "要点两下"。触摸设备上让**第一下就跳**：自己拿走这一次触摸并阻止随后那次
			// 合成 click，避免跳两次。
			document.addEventListener(
				"touchend",
				(event) => {
					if (!is_mobile() || !window.matchMedia?.("(hover: none)")?.matches) return;
					const target = event.target;
					if (!target || typeof target.closest !== "function") return;
					const item = target.closest(".body-sidebar .standard-sidebar-item");
					const link = item && item.querySelector("a[href]");
					if (!link) return;
					const path = link.pathname || "";
					if (!path) return;
					event.preventDefault();
					close_mobile_sidebar();
					frappe.set_route(path);
				},
				{ passive: false },
			);
		}
		// 个人资料页的"先隐藏、修剪完再显示"（见 product.css）：路由进到 User 表单时
		// 重新上闸，`user.js` 修剪完会调 `mark_profile_ready`；JS 没跑就由看门狗兜底。
		let profile_guard = null;
		frappe.pearnly.mark_profile_ready = () => {
			if (profile_guard) {
				window.clearTimeout(profile_guard);
				profile_guard = null;
			}
			document.body.classList.add("pearnly-profile-ready");
		};
		const arm_profile_guard = () => {
			document.body.classList.remove("pearnly-profile-ready");
			if (profile_guard) window.clearTimeout(profile_guard);
			profile_guard = window.setTimeout(frappe.pearnly.mark_profile_ready, 5000);
		};
		frappe.router?.on?.("change", () => {
			if (frappe.get_route_str?.() === "user") arm_profile_guard();
		});
		if (frappe.get_route_str?.() === "user") arm_profile_guard();
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
