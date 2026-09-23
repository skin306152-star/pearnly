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
	// 数量/百分数按**实际位数**显示：2.5 就是 "2.5"、2 就是 "2"，不留 `precision` 那些尾零。
	//
	// 为什么这么做：框架的 Float 控件用 `format_for_input` = `format_number(value, null, precision)`
	// 渲染输入框，precision 是字段定的（明细数量给 6），所以 2.5 会显示成 2.500000。
	// 这里只改**显示**这一层（包一层 format_for_input 去尾零），值不动 —— 输入解析
	// （`parse` → `flt(value, precision)`）走的是另一条路，所以"2.5"照样能输入、不会被四舍五入。
	// 金额（Currency）不在这里改：钱固定两位。
	p.tidy_number = (field) => {
		const type = field?.df?.fieldtype;
		if (!field || !["Float", "Percent"].includes(type) || field.pearnly_tidy) return field;
		field.pearnly_tidy = true;
		const tidy = text => (typeof text === "string" && text.includes(".")
			? text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : text);
		const format = field.format_for_input?.bind(field);
		if (format) field.format_for_input = value => tidy(format(value));
		// 只读时框架走的不是 `format_for_input`，而是 `frappe.format(value, df)`；它的
		// Float 分支把 precision 0 当"没设"退回系统默认（2 → 2.000、2.5 → 2.500）。
		// 数量按**实际位数**显示，所以只读的 Float 直接出我们自己那份文本。
		const display = field.set_disp_area?.bind(field);
		if (display) field.set_disp_area = value => {
			const shown = value ?? field.get_value?.();
			if (field.df.read_only && type === "Float" && field.disp_area
					&& shown !== null && shown !== undefined && shown !== "") {
				$(field.disp_area).html(p.escape(p.number(shown)));
				return;
			}
			return display(value);
		};
		// 已经渲染过的控件（默认值先画了一遍）按新口径重画一次显示。
		if (field.$input?.length) field.set_formatted_input(field.get_value?.());
		return field;
	};
	p.money = (value, currency = "THB", max = 2) => {
		if (value === null || value === undefined || value === "") return "";
		const decimals = decimals_of(value, max);
		return decimals === null ? String(value) : format_currency(value, currency, decimals);
	};
	// 产品里的时间一律这么显示：`2026-09-22 20:10`（到分钟）。所有界面（列表、弹窗、明细）
	// 都走这一个入口 —— 用户 2026-09-22 口径："我真的不希望说一处就该一处"。
	//
	// 为什么这里**不做** `str_to_user` 的时区换算：业务时间在库里就是**站点时区的裸时间**
	// （盘点记录 `counted_at`、收银单据 `sold_at` 都是站点时区的 now() 写进去的），再按
	// "UTC → 观看者时区"换算只会整体偏 —— 本机实测偏 1.5 小时（站点默认时区残留成
	// Frappe 装的 Asia/Kolkata）。旧项目同样按业务时区（UTC+7）直接显示。
	// 所以只做三件事：去掉 `T`、去掉微秒尾巴、秒收到分钟。
	p.datetime_text = (value) => {
		if (!value) return "";
		return String(value).replace("T", " ").replace(/\.\d+$/, "")
			.replace(/(\d{1,2}:\d{2}):\d{2}$/, "$1");
	};
	// 纯数字金额（不带货币符号、固定两位小数）：收发存那种"整列同一个币种"的表用 ——
	// 旧项目那张表的金额列就是 `20,000.00` 这个形状（`fmtMoney`），不带 THB。
	p.amount = (value) => {
		if (value === null || value === undefined || value === "") return "";
		const number = Number(value);
		return isFinite(number) ? format_number(number, null, 2) : String(value);
	};
	// 数量**不加千分位**（`2000`，不是 `2,000`）：旧项目那张收发存表的数量列就是
	// `fmtQty` 的 `String(n)`。站点的数字格式里分组符号可能是逗号也可能是点，所以从
	// 框架的格式信息里取出来再去掉，不写死分隔符。
	p.plain_number = (value, max = 3) => {
		const text = p.number(value, max);
		if (!text) return text;
		const info = typeof get_number_format_info === "function" && typeof get_number_format === "function"
			? get_number_format_info(get_number_format())
			: { group_sep: ",", decimal_str: "." };
		const parts = String(text).split(info.decimal_str);
		const integer = parts[0].split(info.group_sep).join("");
		return parts.length > 1 ? `${integer}${info.decimal_str}${parts[1]}` : integer;
	};
	// 佛历日期 `08/04/2569`：旧项目 `format-date.ts` 的 `DD/MM/YYYY`（年份 +543），
	// 收发存那张报表按它显示（用户 2026-09-22 给的参考图就是这个形状）。
	p.buddhist_date = (value) => {
		const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
		return parts ? `${parts[3]}/${parts[2]}/${Number(parts[1]) + 543}` : String(value || "");
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
	// 「静音」是**一位管全站**：官方 `frappe.utils.play_sound` 自己看 `User.mute_sounds`，
	// 而我们自己的提示（音效之外的震动这类）没有那条管道，判断收到这里来 ——
	// 用户 2026-09-23 口径："同时关联静音和不静音，全站"。个人信息页保存后会当场回写这一位。
	p.sounds_muted = () => Boolean(frappe.boot?.user?.mute_sounds);
	// 多行框（备注 / 地址）**默认矮、写不下才长**：高度按内容走，长到上限就自己在框里滚。
	// 一处规则全站生效 —— 皮肤里把默认高度压到两行（`product.css`），这里负责"依次变大"，
	// 所以每个页面、每个弹窗都不用自己接一遍（用户 2026-09-23："地址和备注框默认同比例和
	// 当前在的位置缩小，当字段超出默认尺寸时才自动依次变大"）。
	p.TEXTAREA_MAX_PX = 240;
	p.grow_textarea = element => {
		const node = element?.target ?? element;
		if (!node || node.tagName !== "TEXTAREA") return;
		node.style.height = "auto";
		node.style.height = `${Math.min(node.scrollHeight, p.TEXTAREA_MAX_PX)}px`;
	};
	p.fit_textareas = root => {
		$(root || document).find("textarea.form-control").each((_, node) => p.grow_textarea(node));
	};
	// 敲字、点进去、粘贴之后都过一遍（已有内容的长框，点一下也会长到该有的大小）；
	// 弹窗打开时也过一遍 —— 查看/编辑一张已有单据时，长备注不能只露两行。
	$(document).on("input focus change", "textarea.form-control", event => p.grow_textarea(event.target));
	$(document).on("shown.bs.modal", event => p.fit_textareas(event.target));
	// 官方 `ControlSmallText.make_input()` 里写死了**内联** `height: 150px` ——
	// 皮肤里的高度被它压住，而我们的"内容超出才长"又必须能改这一格。所以照接管
	// `ControlLink` 的同一套做法：继承它、只把那个内联高度去掉，其余（官方的
	// `max_height`、只读呈现、事件）原样留着，交回给皮肤与 `p.grow_textarea`。
	const NativeSmallText = frappe.ui?.form?.ControlSmallText;
	if (NativeSmallText && !NativeSmallText.pearnly_small_text) {
		class SmallText extends NativeSmallText {
			make_input() {
				super.make_input();
				this.$input.css({height: ""});
			}
		}
		SmallText.pearnly_small_text = true;
		frappe.ui.form.ControlSmallText = SmallText;
	}
	// 成功回音。官方那一声是**按动作分**的：保存/提交 → `submit`，删除 → `delete`
	// （框架自己的批量动作就是 `play_sound(action)`，`bulk_operations.js` 里 submit/cancel/delete 各一声），
	// 所以第二个参数留给"这不是提交、是删除"这类场合；默认仍是 submit。
	p.success = (message, sound = "submit") => {
		frappe.show_alert({ message: p.escape(message), indicator: "green" });
		frappe.utils.play_sound(sound);
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
		// 标记是我们自己的弹窗：框架给下拉浮层的 z-index（2）比 footer（1）高，
		// 短弹窗里最后一行选项正好压在「提交」上 —— 点提交其实点在选项上。
		// 皮肤里把 footer 抬到浮层之上（见 product.css），所以这里只要挂个类。
		d.$wrapper.addClass("pearnly-dialog");
		// 先给控件挂上"数量不留尾零"的显示口径，再 show —— 否则第一遍画出来还是 2.500000。
		Object.values(d.fields_dict || {}).forEach(field => p.tidy_number(field));
		d.show();
		return d;
	};
	p.layout = (parent, fields) => {
		const form = new frappe.ui.FieldGroup({ body: parent, fields });
		form.make();
		Object.values(form.fields_dict).forEach(field => p.tidy_number(field));
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
	// 表单分几栏：默认两栏。**Frappe 的"栏"是竖着堆字段的**（Column Break 换到下一栏），
	// 所以 `columns=2` 的含义是"两栏、每栏从上往下排"，视觉上就是每排两个字段。
	//
	// 整行控件（HTML / Table / Button / Section）必须落在**新的一节**里，不能只换栏：
	// 只换栏的话它会跟字段并排挤在右边（实测：流水弹窗、收发存二级页的表格都被挤成一条），
	// 所以这里用 Section Break 收口，并且**看下一个字段**决定用哪种换行 ——
	// 恰好填满一栏时如果后面跟着整行控件，就用 Section Break 而不是 Column Break。
	p.field_rows = (fields, columns = 2) => {
		const rows = [];
		let filled = 0;
		const full_width = (field) => ["Section Break", "HTML", "Table", "Button"]
			.includes(field?.fieldtype);
		fields.forEach((field, index) => {
			const type = field.fieldtype;
			if (full_width(field)) {
				if (filled) { rows.push({ fieldtype: "Section Break" }); filled = 0; }
				rows.push(field);
				return;
			}
			if (type === "Column Break") { rows.push(field); filled = 0; return; }
			rows.push(field); filled += 1;
			if (filled === columns) {
				rows.push({ fieldtype: full_width(fields[index + 1]) ? "Section Break" : "Column Break" });
				filled = 0;
			}
		});
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

	// ── 二级页面（新建 / 明细）与多级面包屑 ────────────────────────────────────
	// 用户 2026-09-22 口径：**新建是二级面包屑页面，查看是弹窗**；商品、入库、出库统一
	// 这一套。做法与盘点工作台一致 —— 一个 Desk Page 下按路由分段切换内容（第一段是
	// 列表，第二段是二级页面），页面只在首次进入时建一次，之后每次 `show` 按路由重绘。
	p.subroute = (root, ...parts) =>
		"/desk/" + [root, ...parts]
			.filter(part => part !== null && part !== undefined && part !== "")
			.map(encodeURIComponent).join("/");

	// `render(rest, still_current)`：`rest` 是路由第一段之后的全部段（列表时是空数组），
	// `still_current()` 在切走之后返回 false，用来丢掉慢请求的响应。
	p.routed_page = ({ wrapper, root, render }) => {
		let sequence = 0;
		const show = async () => {
			const route = frappe.get_route();
			if (route[0] !== root) return;
			const request = ++sequence;
			await render(rest_of(route, root), () => request === sequence);
		};
		wrapper.on_page_show = () => { p.restore_sidebar(); void show(); };
		$(wrapper).on("hide", () => { ++sequence; });
		return show;
	};
	const rest_of = (route, root) => (route[0] === root ? route.slice(1) : []);
	// 路由段超出这个页面认得的层级（手输的、旧书签）时收敛回上一级，别停在空白页。
	p.redirect_up = (root, ...parts) => frappe.set_route(root, ...parts);

	// 弹窗左下角那颗「全部编辑」：官方 `QuickEntryForm` 用的就是同一个插槽
	// （`dialog.add_custom_action(__("Edit Full Form"), …)`）。用户 2026-09-22 口径：
	// 新建默认走弹窗（快），字段多、要批量或要改细节时点它进二级页面。`carry` 是交给
	// 二级页面的初值；两边在同一个闭包里，不走 `route_options`（不必把值写进 URL）。
	p.edit_all = (dialog, label, route, carry) => {
		dialog.add_custom_action(label, () => {
			dialog.hide();
			carry?.(dialog.get_values(true) || {});
			frappe.set_route(route);
		});
	};
	// 路由切走时把页面恢复成"只剩原生筛选区"的空壳：列表与二级页面共用 page.main，
	// 上一态残留的字段、按钮、指示器都会跟过来（盘点工作台踩过同样的坑）。
	p.clear_page = (page) => {
		page.page_form.detach();
		page.main.empty().append(page.page_form).removeClass("layout-main-list list-view");
		page.clear_fields(); page.fields_dict = {}; page.clear_inner_toolbar();
		// 右上角那排图标也要清：`add_refresh` 每进一次列表就加一颗，不清就会越堆越多
		// （实测收发存页面进了几次之后右上角挂了一排刷新）。用原生 `clear_icons()`。
		page.clear_icons?.();
		page.hide_form();
		page.clear_primary_action(); page.clear_indicator();
	};

	// 多级面包屑：原生 Custom crumb 只画一个节点。这里把整条路径交给框架自己渲染
	// （存在 `breadcrumbs.all` 里，重绘、返回、刷新都能复原），而不是临时插 DOM；窄屏
	// 原生只显示最后一级，所以同时给出 `menu_items`。
	const native_custom = frappe.breadcrumbs.set_custom_breadcrumbs;
	frappe.breadcrumbs.set_custom_breadcrumbs = function (crumb) {
		native_custom.call(this, crumb);
		for (const step of crumb.pearnly_trail || []) {
			this.append_breadcrumb_element(step.route, p.escape(step.label));
		}
		if (crumb.pearnly_trail?.length && crumb.menu_items?.length) {
			// 菜单点击不能再冒泡给 Desk 路由器，否则它会重开当前页并关掉菜单。
			this.$breadcrumbs.children("li:last-child").on("click", (event) => {
				event.preventDefault();
				event.stopPropagation();
			}).find("a").attr("aria-haspopup", "menu")
				.append(frappe.utils.icon("chevron-down", "xs"));
		}
	};
	// `trail` 里带 `route` 的节点可点（父级），最后一个通常不带（就是当前页）。
	p.breadcrumbs = ({ label, route = "", trail = [] }) => {
		const parents = trail.filter(step => step.route);
		frappe.breadcrumbs.add({
			type: "Custom", label, route,
			pearnly_trail: trail,
			menu_items: trail.length ? [
				// 语义键（`nav.home`）而不是英文原句 —— 传 "Home" 会被框架自带译文抢走。
				{ label: __("nav.home"), onClick: () => frappe.set_route("pearnly-home") },
				{ label, onClick: () => frappe.set_route(route || frappe.get_route_str()) },
				...parents.map(step => ({
					label: p.escape(step.label), onClick: () => frappe.set_route(step.route),
				})),
			] : [],
		});
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

	// ── 左下角那颗用户按钮：点开时**左侧那栏整个换成一份两项菜单** ───────────────
	//
	// 原生那颗按钮只有一件事：点了直接进个人信息（`sidebar.html` 里写死
	// `onclick="return frappe.ui.toolbar.route_to_user()"`）。用户 2026-09-23 口径：
	// "我点左下角，左侧菜单就只有个人信息和团队管理" —— 所以点开时把侧栏**条目区**
	// 换成那两项，再点一次（或换路由）用原生 `make_sidebar()` 画回原来的菜单。
	// 条目用框架自己的 `sidebar_item` 模板渲染，图标、hover、折叠态都跟侧栏一致。
	//
	// 项由各产品在 `pearnly_navigation` 的 `user_menu` 里声明，服务端按与侧栏同一套
	// 判据（页面已实现 + 当前用户能进）筛过再进 boot（`desk/navigation.py`）；别人
	// 的菜单里连这一项都没有。
	p.user_menu = (sidebar = frappe.app?.sidebar) => {
		const button = sidebar?.$sidebar?.find(".sidebar-user-button");
		if (!sidebar || !button?.length) return;
		// 产品声明的那几项按声明顺序插在「个人信息」周围：带 `before_profile` 的在它**上面**
		// （ERP 的公司），其余的（团队管理）在它下面。用户 2026-09-23："公司……做在个人信息上方"。
		const declared = frappe.boot.pearnly_user_menu || [];
		const elsewhere = entry => ({ label: __(entry.label), icon: entry.icon || "users",
			route: entry.route, go: () => frappe.set_route(entry.route) });
		const profile = { label: __("My Profile"), icon: "user",
			go: () => frappe.ui.toolbar.route_to_user() };
		const entries = [
			...declared.filter(entry => entry.before_profile).map(elsewhere),
			profile,
			...declared.filter(entry => !entry.before_profile).map(elsewhere),
		];
		// 「个人信息 / 团队管理」这两个地方算一个**账号区**：停在里面时左侧就保持这两项，
		// 回到业务页面（首页、销售记录…）才画回业务菜单。用户 2026-09-23 口径："点了
		// 其他菜单（团队管理），左侧菜单发生了变化" —— 进去之后不该变回去。
		const in_account = () => {
			const route = frappe.get_route() || [];
			return entries.some(entry => entry.route && entry.route === route[0])
				|| (route[0] === "Form" && route[1] === "User" && route[2] === frappe.session.user);
		};
		const state = sidebar.pearnly_user_menu = sidebar.pearnly_user_menu
			|| {open: in_account(), bound: false};
		const cards = () => sidebar.$sidebar.find(".body-sidebar-cards");
		// 画成"账号那两项"（原生 `sidebar_item` 模板，图标/hover 与侧栏一致），
		// 每次都重绑一次点击 —— 侧栏重画会把节点换掉。
		const paint_account = () => {
			state.open = true;
			// 当前停在哪一项就给它原生的选中底色（`.active-sidebar`，业务侧栏那套同一个
			// 类与样式）—— 少这个的话点进来两项长得一样，看不出自己在哪。
			const route = frappe.get_route() || [];
			const on_profile = route[0] === "Form" && route[1] === "User"
				&& route[2] === frappe.session.user;
			sidebar.$items_container.empty().append(entries.map(entry => {
				const item = $(frappe.render_template("sidebar_item", {
					item: {label: entry.label, icon: entry.icon, type: "Link", standard: 1},
					path: "", edit_mode: false,
				}));
				if (entry.route ? route[0] === entry.route : on_profile) {
					item.find(".standard-sidebar-item").addClass("active-sidebar");
				}
				return item;
			}));
			sidebar.$items_container.find(".item-anchor").each((index, element) =>
				$(element).on("click", (event) => {
					event.preventDefault();
					// 点进去的这两页本身就在账号区里，菜单保持两项；`in_account()` 守得住。
					entries[index].go();
				}));
			cards().addClass("hidden");
		};
		// 收起 = 让原生重新画一遍业务菜单（我们不自己拼回原来的条目）。
		const show_business = () => {
			state.open = false;
			cards().removeClass("hidden");
			sidebar.make_sidebar();
		};
		// 原生那行 onclick 要摘掉：不然点一下既换菜单又跳走。绑定记号挂在按钮自己身上，
		// 万一那颗按钮被换掉还能重绑（路由监听挂在侧栏实例上，只挂一次）。
		if (!button.data("pearnlyMenu")) {
			button.removeAttr("onclick").data("pearnlyMenu", true).on("click", (event) => {
				event.preventDefault();
				if (state.open) return show_business();
				// 点开时右侧默认落在「个人信息」；已经在账号区里（例如团队管理）就只换菜单。
				if (!in_account()) profile.go();
				paint_account();
			});
		}
		if (!state.bound) {
			state.bound = true;
			frappe.router.on("change", () => {
				if (in_account()) return paint_account();
				if (state.open) show_business();
			});
		}
		// 侧栏每次重画（含框架主动换侧栏、我们自己的 `make_sidebar()`）之后按当前状态画对。
		if (state.open) paint_account();
	};
	// 挂在原生侧栏的 `make_sidebar()` 之后：那颗按钮和条目区都在同一张模板里，而
	// `make_sidebar()` 开头会把条目区清空（挂早了会被清掉，实测）。`p.user_menu()`
	// 自己是幂等的，侧栏每次重建都会重画，这里每次调用都安全。
	const NativeSidebar = frappe.ui.Sidebar;
	frappe.ui.Sidebar = class ProductSidebar extends NativeSidebar {
		make_sidebar(...args) {
			super.make_sidebar(...args);
			p.user_menu(this);
		}
		// 产品站点只认自己那份侧栏：框架会按"这个页面属于哪个模块 / 哪个工作区"来换侧栏
		// （我们的页面都挂在 `Pearnly Core` 模块下，`pearnly-team` 这种**被移出侧栏**的
		// 页面就会中招 —— 2026-09-23 实测：点进去之后左侧换成一份二十来项的平铺原生清单，
		// 上面还带着 `团队管理`，跟"菜单只放在侧栏里"正好相反）。所以在 `setup()` 这一层
		// 把目标钉成我们这一份：模块/工作区侧栏留给没有外壳的裸 Desk。
		setup(workspace_title) {
			super.setup(frappe.boot?.pearnly_sidebar || workspace_title);
		}
	};

	// ── 上传框只留「我的设备 / 摄像头」（用户 2026-09-23 口径）───────────────────
	//
	// 框架的上传器默认给四颗按钮：我的设备 / 库（本站已有的文件）/ 链接（外部网址）/
	// 摄像头。我们收的是**当场拍的照片或自己选的文件**："库"会让人把别的单据的附件
	// 再挂一次（一串单据挂同一份图，对不上是哪张），"链接"存的是别人服务器上的地址
	// （对方删了就断、我们也没有留底）。所以这两颗全站去掉，只留另外两颗。
	//
	// 为什么挂 setter 而不是直接替换：`frappe.ui.FileUploader` 是**懒加载**的
	// （`frappe/upload.js` 里那句 `frappe.require("file_uploader.bundle.js")` 是异步的），
	// 我们这段脚本可能跑在它前面、也可能跑在它后面。挂上存取器之后，框架**什么时候**
	// 赋值都会被换成"固定关掉这两项"的子类；直接替换的话，框架那次赋值会把补丁盖掉。
	// 覆盖范围是**全站**：不但我们的六处调用，框架自己的附件侧栏、文件列表、表单附件、
	// 沟通记录里的上传框也一起收紧（那些地方我们改不到）。
	(() => {
		const minimal = Native => {
			if (typeof Native !== "function" || Native.pearnly_minimal_uploader) return Native;
			const Patched = class extends Native {
				constructor(options = {}) {
					super({...options, allow_web_link: false, disable_file_browser: true});
				}
			};
			Object.defineProperty(Patched, "pearnly_minimal_uploader", {value: true});
			return Patched;
		};
		let current = minimal(frappe.ui.FileUploader);
		try {
			Object.defineProperty(frappe.ui, "FileUploader", {
				configurable: true, enumerable: true,
				get: () => current,
				set: value => {current = minimal(value);},
			});
		} catch (error) {
			// 理论上不会走到这里（`frappe.ui` 是我们自己的对象）。真发生了也把当前那一个
			// 换掉，并留一条日志 —— 宁可少一层保险，也不要静默地什么都没做。
			frappe.ui.FileUploader = current;
			console.warn("pearnly: uploader options not hooked", error);
		}
	})();

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
