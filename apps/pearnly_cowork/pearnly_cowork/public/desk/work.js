/* 任务管理：团队看板（用户 2026-09-27，照 Trello）。
 *
 * 多块看板（用户 2026-09-29）：页面筛选区第一格切换看板；每块看板有自己的成员、可见范围、标签、字段与自动化。
 * 能做什么由服务端按看板算好（`board.rights`：view / edit / comment / members / manage），页面只按它显示入口：
 * 只读（观察者）时不出现任何写入口，标签 / 字段 / 自动化 / 看板设置只给能管这块板的人。
 * 列表由大家自己增删、改名、拖动排序、复制（第四批）。拖进「完成列表」就算完成，没有审核；
 * 卡片上的完成圆圈照 Trello 只改完成与否、不挪列表。卡片的样子与交互照 Trello（完成圆圈、悬停出归档 / 编辑、
 * 快捷菜单、封面、标签、徽标、头像、模板、镜像），底色用我们自己的（"底色还是用我们自己的"）：白底、原生灰列，
 * 列表颜色由大家在列表菜单里点色块选。
 * 拖放用原生 Sortable；详情、快捷编辑都是原生 Dialog / FieldGroup / 控件；筛选在页面原生筛选区；
 * 手机上列表纵排，顶上一条分区导航（照 ERP / 录入二级页）。
 * 状态、顺序、权限、提醒都在服务端（`pearnly_cowork.work`），页面只管交互。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.work.api.";
	const ROOT = "pearnly-work";
	const LEGACY = ["todo", "doing", "done"];  // 第一、二批活动记录里的旧列代号
	const COLORS = ["green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray"];
	const REMINDERS = ["none", "at_due", "5m", "10m", "15m", "1h", "2h", "1d", "2d"];
	const REPEATS = ["never", "daily", "weekly", "monthly", "yearly"];
	const FIELD_TYPES = ["text", "number", "date", "checkbox", "select"];
	const IMAGES = /\.(png|jpe?g|gif|webp)$/i;
	const EVENT = "cowork_work_changed";
	const NOTIFY = "cowork_work_notify";
	const COLLAPSED = "pearnly-work-collapsed";
	const DETAILS = "pearnly-work-activity-details";
	const VIEWS = ["board", "mine", "calendar", "table"];
	const RULE_ACTIONS = ["assign", "label", "due_days", "complete", "watch"];
	const BOARD_KEY = "pearnly-work-board";  // 每人上次看的看板，记在浏览器本地
	const VISIBILITIES = ["team", "private"];
	const COMMENTING = ["members", "observers", "disabled"];
	const MEMBER_MANAGEMENT = ["admins", "members"];
	const BOARD_ROLES = ["admin", "member", "observer"];
	// 拖动的样子（照 Trello，用户 2026-09-27）：浏览器原生拖动只给一张半透明截图、样式改不了，改用 Sortable 的
	// 模拟拖动，拖着的是实物（不透明、微斜、带阴影，见 work.css）；移动 3 像素以上才算拖，单击照旧打开卡片。
	const DRAG_LOOK = {forceFallback: true, fallbackTolerance: 3};
	const mobile = () => window.matchMedia("(max-width: 767px)").matches;
	const icon = (name, size = "xs") => frappe.utils.icon(name, size);
	const card_url = name => `${window.location.origin}${p.subroute(ROOT, name)}`;
	// 放进 `style="background-image: url(...)"` 的地址：引号、括号都编码掉。
	const css_url = url => encodeURI(url || "").replace(/["'()]/g, char => `%${char.charCodeAt(0).toString(16)}`);
	const hhmm = value => (value ? String(value).slice(0, 5).padStart(5, "0") : "");

	// 浮在页面上的小菜单（卡片快捷菜单、列表菜单、模板）：列表会滚动、会裁切，所以挂在 body 上，
	// 用原生 `.dropdown-menu` 的样子；点外面、按 Esc、滚动都收起。
	let popup = null;
	const close_popup = () => { popup?.remove(); popup = null; };
	const open_popup = (anchor, html, onpick) => {
		const again = popup?.data("anchor") === anchor[0];
		close_popup();
		if (again) return;
		popup = $('<div class="dropdown-menu show pearnly-work-popup" role="menu">').html(html)
			.data("anchor", anchor[0]).appendTo(document.body);
		const box = anchor[0].getBoundingClientRect();
		const width = popup.outerWidth();
		const left = Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8));
		const below = box.bottom + 4;
		const top = below + popup.outerHeight() > window.innerHeight - 8
			? Math.max(8, box.top - popup.outerHeight() - 4) : below;
		popup.css({position: "fixed", left, top});
		popup.on("click", "button", event => {
			const button = $(event.currentTarget);
			close_popup();
			onpick(button);
		});
	};
	$(document).on("mousedown.pearnlyWork", event => {
		if (popup && !$(event.target).closest(".pearnly-work-popup, [data-popup]").length) close_popup();
	});
	$(document).on("keydown.pearnlyWork", event => { if (event.key === "Escape") close_popup(); });
	window.addEventListener("scroll", event => {
		if (popup && !$(event.target).closest(".pearnly-work-popup").length) close_popup();
	}, true);

	// 列表名：活动记录 / 提醒里旧的列代号翻成词条，新的就是列表名本身。
	const list_name = (t, value) => (LEGACY.includes(value) ? t(`cowork.work.status.${value}`) : (value || ""));
	// 提醒的一句话：页面里的铃铛与在线时弹出的提示共用。`t` 是本页词条。
	const notice_text = (t, row) => {
		const who = row.actor_name || row.actor || "";
		const due = row.detail?.due ? frappe.datetime.str_to_user(row.detail.due) : "";
		const to = row.detail?.to ? list_name(t, row.detail.to) : "";
		return t(`cowork.work.notify.${row.kind}`, who, row.title || "", to || due);
	};

	// 不在看板页时也要能收到"@了你 / 分给你"这类提示：登录后挂一次监听（只有能用看板的人）。
	let texts = null;
	const page_texts = () => (texts ||= p.texts("cowork.work."));
	const listen = () => {
		if (!frappe.boot.pearnly_pages?.includes(ROOT) || p.work_listening) return;
		p.work_listening = true;
		frappe.realtime.on(NOTIFY, async row => {
			const t = await page_texts();
			const alert = frappe.show_alert({message: p.escape(notice_text(t, row)), indicator: "blue"}, 8);
			alert?.css?.("cursor", "pointer").on("click", () => frappe.set_route(ROOT, row.card));
			p.work_unread?.(1);
		});
	};
	$(document).on("app_ready", listen);
	if (frappe.app) listen();

	p.coworkWork = async (page, t, wrapper) => {
		const state = {board: null, boards: [], lists: [], cards: [], foreign: [], mirrors: [], members: [], labels: [],
			fields: [], me: frappe.session.user, admin: false, loaded_at: 0, unread: 0};
		const can = right => Boolean(state.board?.rights?.includes(right));
		const member = user => state.members.find(row => row.user === user);
		const member_name = user => member(user)?.name || user || "";
		const label_of = name => state.labels.find(row => row.name === name);
		// 别的看板镜像过来的卡在 `foreign` 里：能画、能点开，但不算进本板的视图与表格。
		const card_of = name => state.cards.find(row => row.name === name) || state.foreign.find(row => row.name === name);
		const list_of = name => state.lists.find(row => row.name === name);
		const color_text = color => t(`cowork.work.color.${color}`);
		const list_options = () => state.lists.map(row => ({value: row.name, label: row.title}));

		const avatar = user => {
			const row = member(user);
			return frappe.avatar(null, "avatar-small", row?.name || user, row?.image || "");
		};
		const label_pill = label => `<span class="pearnly-work-label ${label.color}"
			title="${p.escape(label.title || color_text(label.color))}">${p.escape(label.title)}</span>`;
		// 到期：已完成绿、过期红、提醒时间到了 / 今明两天黄，其余不上色（照 Trello）。
		const due_state = card => {
			if (card.completed) return "done";
			const days = frappe.datetime.get_diff(card.due_date, frappe.datetime.get_today());
			return days < 0 ? "overdue" : (days <= 1 ? "soon" : "");
		};
		const due_text = card => {
			const parts = [];
			if (card.start_date) parts.push(frappe.datetime.str_to_user(card.start_date));
			if (card.due_date) parts.push(`${frappe.datetime.str_to_user(card.due_date)}${card.due_time ? ` ${hhmm(card.due_time)}` : ""}`);
			return parts.join(" - ");
		};

		// ── 看板切换（页面原生筛选区第一格）与筛选 ─────────────────────────────
		const stored_board = () => { try { return localStorage.getItem(BOARD_KEY) || ""; } catch (error) { return ""; } };
		const board_field = page.add_field({fieldname: "board", fieldtype: "Select", label: t("cowork.work.board.label"),
			change: () => void switch_board(board_field.get_value())});
		const filters = {
			keyword: page.add_field({fieldname: "keyword", fieldtype: "Data", label: t("cowork.work.filter.keyword"),
				change: () => apply()}),
			member: page.add_field({fieldname: "member", fieldtype: "Select", label: t("cowork.work.filter.member"),
				change: () => apply()}),
			label: page.add_field({fieldname: "label", fieldtype: "Select", label: t("cowork.work.filter.label"),
				change: () => apply()}),
			due: page.add_field({fieldname: "due", fieldtype: "Select", label: t("cowork.work.filter.due"),
				options: [{value: "", label: t("cowork.work.filter.anyDue")},
					{value: "overdue", label: t("cowork.work.filter.overdue")},
					{value: "soon", label: t("cowork.work.filter.soon")},
					{value: "none", label: t("cowork.work.filter.noDue")}],
				change: () => apply()}),
		};
		let searching = 0;
		const search = async () => {
			const keyword = (filters.keyword.get_value() || filters.keyword.get_input_value?.() || "").trim();
			const request = ++searching;
			if (keyword.length < 2) {
				state.search_hits = null;
			} else {
				const names = await p.call(`${API}search`, {keyword, board: state.board?.name});
				if (request !== searching) return;
				state.search_hits = new Set(names);
			}
			apply();
		};
		filters.keyword.df.change = () => void search();
		filters.keyword.$input.on("input", frappe.utils.debounce(() => {
			filters.keyword.set_value(filters.keyword.get_input_value() || "");
		}, 250));
		const set_options = (field, options) => {
			const value = field.get_value();
			field.df.options = options;
			field.refresh();
			field.set_input(options.some(option => option.value === value) ? value : "");
		};
		const refresh_filter_options = () => {
			const boards = state.boards.map(row => ({value: row.name, label: row.title}));
			if (state.board && !boards.some(row => row.value === state.board.name)) {
				boards.push({value: state.board.name, label: state.board.title});
			}
			board_field.df.options = boards;
			board_field.refresh();
			board_field.set_input(state.board?.name || "");
			set_options(filters.member, [{value: "", label: t("cowork.work.filter.anyMember")},
				{value: "__me", label: t("cowork.work.filter.mine")},
				{value: "__none", label: t("cowork.work.filter.noMember")},
				...state.members.map(row => ({value: row.user, label: row.name}))]);
			set_options(filters.label, [{value: "", label: t("cowork.work.filter.anyLabel")},
				...state.labels.map(row => ({value: row.name, label: row.title || color_text(row.color)}))]);
		};
		// 关键词：两个字以上时交给服务端全文搜（标题、描述、评论、清单项、字段值），一个字只比标题。
		const matches = card => {
			const keyword = (filters.keyword.get_value() || "").trim().toLowerCase();
			if (keyword && state.search_hits) {
				if (!state.search_hits.has(card.name)) return false;
			} else if (keyword && !card.title.toLowerCase().includes(keyword)) return false;
			const who = filters.member.get_value();
			if (who === "__me" && !card.assignees.includes(state.me)) return false;
			if (who === "__none" && card.assignees.length) return false;
			if (who && !who.startsWith("__") && !card.assignees.includes(who)) return false;
			const label = filters.label.get_value();
			if (label && !card.labels.includes(label)) return false;
			const due = filters.due.get_value();
			if (due === "none") return !card.due_date;
			if (due && (!card.due_date || card.completed)) return false;
			if (due) return due_state(card) === (due === "overdue" ? "overdue" : "soon");
			return true;
		};

		// ── 页面右上：刷新、提醒铃铛、菜单 ─────────────────────────────────────
		p.add_refresh(page, () => reload());
		const bell = page.add_action_icon("bell", () => void open_notifications(), "pearnly-work-bell",
			t("cowork.work.notify.title")).attr({type: "button", "aria-label": t("cowork.work.notify.title")});
		const badge = $('<span class="pearnly-work-bell-count hide">').appendTo(bell);
		const show_unread = () => badge.text(state.unread > 99 ? "99+" : state.unread).toggleClass("hide", !state.unread);
		p.work_unread = delta => { state.unread += delta; show_unread(); };
		// 手机上右上角图标收起来了，菜单里也放一个入口。
		page.add_menu_item(t("cowork.work.notify.title"), () => void open_notifications());
		page.add_menu_item(t("cowork.work.board.create"), () => create_board());
		// 只给能管这块板的人：看板设置、标签、自定义字段、自动化（服务端同样拒绝）。
		const managed = [
			page.add_menu_item(t("cowork.work.board.settings"), () => open_board_settings()),
			page.add_menu_item(t("cowork.work.labels.manage"), () => open_labels()),
			page.add_menu_item(t("cowork.work.fields.manage"), () => open_fields()),
			page.add_menu_item(t("cowork.work.rules.manage"), () => void open_rules()),
		];
		page.add_menu_item(t("cowork.work.board.members"), () => void open_members());
		page.add_menu_item(t("cowork.work.archived.title"), () => open_archived());
		const show_rights = () => {
			for (const link of managed) link.closest("li").toggleClass("hide", !can("manage"));
			root.toggleClass("pearnly-work-readonly", !can("edit"));
			for (const parts of Object.values(columns)) parts.sortable.option("disabled", !can("edit"));
			list_sortable.option("disabled", !can("edit"));
			calendar?.setOption("editable", can("edit"));
		};

		// ── 看板骨架 ─────────────────────────────────────────────────────────────
		const root = $('<div class="pearnly-work">').appendTo(page.main);
		// 视图（照 Trello 的视图切换）：看板 / 我的任务 / 日历 / 表格；路由 `pearnly-work/view/<视图>`，可收藏、可返回。
		const views_bar = $('<nav class="form-tabs-list pearnly-work-views">').appendTo(root);
		const views_list = $('<ul class="nav form-tabs">').appendTo(views_bar);
		const view_panes = {};
		for (const view of VIEWS) {
			$('<li class="nav-item">').append($('<button type="button" class="nav-link">').attr("data-view", view)
				.text(t(`cowork.work.view.${view}`))
				.on("click", () => frappe.set_route(...(view === "board" ? [ROOT] : [ROOT, "view", view]))))
				.appendTo(views_list);
			view_panes[view] = $(`<div class="pearnly-work-view" data-view="${view}">`).appendTo(root);
		}
		const nav = $('<nav class="form-tabs-list pearnly-work-nav">').appendTo(view_panes.board);
		const nav_list = $('<ul class="nav form-tabs">').appendTo(nav);
		const board = $('<div class="kanban pearnly-work-board">').appendTo(view_panes.board);
		// 左右移动（用户 2026-09-27："左右滑动看下怎么改比较灵敏点"）：看板撑到窗口底部，横向滚动条停在屏幕最下面；
		// 空白处（列表之间、列表下方）按住拖动或滚滚轮，整块看板左右走（照 Trello）。手机上列表纵排，不需要。
		const fit_board = () => {
			if (mobile() || !board.is(":visible")) { board.css("height", ""); return; }
			const top = board[0].getBoundingClientRect().top;
			board.css("height", `${Math.max(320, Math.floor(window.innerHeight - top - 12))}px`);
		};
		$(window).on("resize.pearnlyWorkBoard", frappe.utils.debounce(fit_board, 100));
		let pan = null;
		board.on("pointerdown", event => {
			if (event.target !== board[0] || event.button !== 0 || mobile()) return;
			pan = {x: event.clientX, left: board[0].scrollLeft, id: event.pointerId};
			board[0].setPointerCapture(event.pointerId);
			board.addClass("panning");
		});
		board.on("pointermove", event => {
			if (pan && event.pointerId === pan.id) board[0].scrollLeft = pan.left - (event.clientX - pan.x);
		});
		board.on("pointerup pointercancel", event => {
			if (!pan || event.pointerId !== pan.id) return;
			pan = null;
			board.removeClass("panning");
		});
		board[0].addEventListener("wheel", event => {
			if (event.target !== board[0] || mobile() || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
			board[0].scrollLeft += event.deltaY;
			event.preventDefault();
		}, {passive: false});
		const adder = $('<div class="kanban-column pearnly-work-column pearnly-work-add-list">').appendTo(board);
		const collapsed = new Set((() => {
			try { return JSON.parse(localStorage.getItem(COLLAPSED) || "[]"); } catch (error) { return []; }
		})());
		const save_collapsed = () => {
			try { localStorage.setItem(COLLAPSED, JSON.stringify([...collapsed])); } catch (error) { /* 无痕窗口 */ }
		};
		// 色块（照 Trello 直接点选）：列表颜色、标签颜色共用；当前选中的打勾。列表的色块用列表实际的底色
		// （`list-tints`，用户 2026-09-27：选的时候鲜艳、画出来是浅色，对不上）。
		const swatches = (current, attrs = "", kind = "") => `<div class="pearnly-work-swatches ${kind}">${COLORS.map(color =>
			`<button type="button" class="pearnly-work-swatch ${color} ${color === current ? "selected" : ""}" ${attrs}
				data-color="${color}" title="${p.escape(color_text(color))}" aria-label="${p.escape(color_text(color))}"
				aria-pressed="${color === current}">${color === current ? icon("check") : ""}</button>`).join("")}</div>`;
		const menu_item = (action, label, extra = "") =>
			`<button type="button" class="dropdown-item" data-action="${action}" ${extra}>${p.escape(label)}</button>`;
		const list_menu = row => {
			const others = state.lists.filter(other => other.name !== row.name);
			return `${menu_item("add", t("cowork.work.card.add"))}
				${menu_item("copy", t("cowork.work.list.copy"))}
				${menu_item("move", t("cowork.work.list.move"))}
				${others.length ? `<div class="dropdown-divider"></div>
					<h6 class="dropdown-header">${p.escape(t("cowork.work.column.moveAll"))}</h6>
					${others.map(other => menu_item("move-all", other.title, `data-target="${p.escape(other.name)}"`)).join("")}` : ""}
				<div class="dropdown-divider"></div>
				<h6 class="dropdown-header">${p.escape(t("cowork.work.column.sort"))}</h6>
				${["created_desc", "created_asc", "due", "title"].map(key =>
					menu_item("sort", t(`cowork.work.column.sort_${key}`), `data-key="${key}"`)).join("")}
				<div class="dropdown-divider"></div>
				<button type="button" class="dropdown-item" data-action="done-list">
					<span class="pearnly-work-check">${row.is_done_list ? icon("check") : ""}</span>
					${p.escape(t("cowork.work.list.doneList"))}</button>
				<div class="dropdown-divider"></div>
				<h6 class="dropdown-header">${p.escape(t("cowork.work.column.color"))}</h6>
				${swatches(row.color, 'data-action="color"', "list-tints")}
				<div class="pearnly-work-popup-pad">
					<button type="button" class="btn btn-default btn-xs btn-block" data-action="color" data-color="">
						${icon("x")} ${p.escape(t("cowork.work.column.removeColor"))}</button></div>
				<div class="dropdown-divider"></div>
				${menu_item("archive-all", t("cowork.work.column.archiveAll"))}
				${menu_item("archive-list", t("cowork.work.list.archive"))}`;
		};
		const columns = {};
		const build_column = row => {
			const column = $(`<div class="kanban-column pearnly-work-column" data-list="${p.escape(row.name)}">`);
			const header = $('<div class="kanban-column-header pearnly-work-column-header">').appendTo(column);
			const title = $('<span class="kanban-column-title"><span class="kanban-title ellipsis"></span></span>')
				.appendTo(header);
			const tools = $('<span class="pearnly-work-column-tools">').appendTo(header);
			const count = $('<span class="pearnly-work-count">').appendTo(tools);
			$('<button type="button" class="btn btn-xs btn-link pearnly-work-collapse">')
				.attr({title: t("cowork.work.column.collapse"), "aria-label": t("cowork.work.column.collapse")})
				.append(icon("chevrons-left-right")).appendTo(tools)
				.on("click", event => { event.stopPropagation(); toggle_collapse(row.name); });
			$(`<button type="button" class="btn btn-xs btn-link" data-popup aria-haspopup="menu"
				aria-label="${p.escape(t("cowork.work.column.menu"))}">${icon("ellipsis", "sm")}</button>`).appendTo(tools)
				.on("click", event => {
					event.stopPropagation();
					open_popup($(event.currentTarget), list_menu(list_of(row.name)),
						button => void list_action(list_of(row.name), button));
				});
			// 点列表名就地改名（照 Trello），回车 / 离开保存，Esc 放弃。
			title.on("click", event => {
				event.stopPropagation();
				if (column.hasClass("collapsed")) return toggle_collapse(row.name);
				if (can("edit")) rename_list(list_of(row.name), title);
			});
			const list = $(`<div class="kanban-cards" data-list="${p.escape(row.name)}">`).appendTo(column);
			const footer = $('<div class="pearnly-work-column-footer">').appendTo(column);
			const add = $('<button type="button" class="pearnly-work-add">')
				.append(icon("plus", "sm")).append($("<span>").text(t("cowork.work.card.add"))).appendTo(footer)
				.on("click", () => open_composer(row.name));
			$(`<button type="button" class="btn btn-xs btn-link pearnly-work-template-button" data-popup
				title="${p.escape(t("cowork.work.template.create"))}" aria-label="${p.escape(t("cowork.work.template.create"))}">
				${icon("copy-plus", "sm")}</button>`).appendTo(footer)
				.on("click", event => { event.stopPropagation(); open_templates($(event.currentTarget), row.name); });
			const compose = $('<div class="pearnly-work-compose hide">').appendTo(column);
			column.on("click", () => { if (column.hasClass("collapsed")) toggle_collapse(row.name); });
			const link = $('<button type="button" class="nav-link">')
				.on("click", () => column[0].scrollIntoView({behavior: "smooth", block: "start"}));
			const nav_item = $('<li class="nav-item">').append(link);
			const sortable = new Sortable(list[0], {
				group: "pearnly-work-cards", animation: 150, draggable: ".pearnly-work-card-wrap",
				// 拖到空列表上方附近就算放进去（照 Trello，整列都能接）。
				emptyInsertThreshold: 60,
				filter: ".pearnly-work-card-tools, .pearnly-work-done", preventOnFilter: false,
				ghostClass: "pearnly-work-ghost", dragClass: "pearnly-work-dragging", ...DRAG_LOOK,
				// 手机上长按才拖，免得滑动翻页时误拖。
				delay: 200, delayOnTouchOnly: true,
				onEnd: event => void dropped(event),
			});
			return {column, title, list, count, footer, add, compose, link, nav_item, sortable};
		};
		// 列表本身也能拖着排（拖列表头），照 Trello。
		// 鼠标在列表下方的空白处时，Sortable 会当成「放到最后」让空槽跳到末尾；照 Trello 改成只看左右位置。
		const LISTS = ".pearnly-work-column:not(.pearnly-work-add-list)";
		let dragged_list = null;
		const follow_list = event => {
			if (!dragged_list || mobile() || document.elementFromPoint(event.clientX, event.clientY) !== board[0]) return;
			const next = board.children(LISTS).not(dragged_list).get()
				.find(node => { const rect = node.getBoundingClientRect(); return event.clientX < rect.left + rect.width / 2; })
				|| adder[0];
			if (dragged_list.nextElementSibling === next) return;
			list_sortable.captureAnimationState();
			board[0].insertBefore(dragged_list, next);
			list_sortable.animateAll();
		};
		const list_sortable = new Sortable(board[0], {
			animation: 150, draggable: LISTS,
			handle: ".pearnly-work-column-header", filter: "button, input", preventOnFilter: false,
			delay: 200, delayOnTouchOnly: true, ghostClass: "pearnly-work-ghost", dragClass: "pearnly-work-dragging",
			...DRAG_LOOK,
			onStart: event => { dragged_list = event.item; document.addEventListener("pointermove", follow_list); },
			onMove: (event, pointer) => mobile() || pointer?.target !== board[0],
			onEnd: event => {
				dragged_list = null;
				document.removeEventListener("pointermove", follow_list);
				void list_dropped(event);
			},
		});
		const sync_columns = () => {
			const wanted = new Set(state.lists.map(row => row.name));
			for (const [name, parts] of Object.entries(columns)) {
				if (!wanted.has(name)) { parts.column.remove(); parts.nav_item.remove(); delete columns[name]; }
			}
			for (const row of state.lists) {
				const parts = columns[row.name] ||= build_column(row);
				parts.column.insertBefore(adder);
				parts.nav_item.appendTo(nav_list);
				parts.title.find(".kanban-title").text(row.title);
				parts.link.text(row.title);
				parts.column.removeClass(COLORS.map(color => `tint-${color}`).join(" "));
				if (row.color) parts.column.addClass(`tint-${row.color}`);
			}
			show_collapsed();
		};
		// 手机上不收列表（纵排，分区导航已经够用）。
		const show_collapsed = () => {
			for (const [name, parts] of Object.entries(columns)) {
				parts.column.toggleClass("collapsed", collapsed.has(name) && !mobile());
			}
		};
		const toggle_collapse = name => {
			if (collapsed.has(name)) collapsed.delete(name); else collapsed.add(name);
			show_collapsed();
			save_collapsed();
		};
		$(window).on("resize", frappe.utils.debounce(show_collapsed, 200));
		// 手机分区导航：滚动时标出当前看到的那一列。
		const spy = () => {
			if (!mobile()) return;
			let active = null;
			for (const row of state.lists) {
				const parts = columns[row.name];
				if (parts && (!active || parts.column[0].getBoundingClientRect().top <= window.innerHeight / 3)) active = row.name;
			}
			for (const [name, parts] of Object.entries(columns)) parts.link.toggleClass("active", name === active);
		};
		$(window).on("scroll resize", frappe.utils.throttle(spy, 100));

		// 列表最后一格「添加另一个列表」。
		const draw_adder = () => {
			adder.empty();
			$('<button type="button" class="pearnly-work-add">').append(icon("plus", "sm"))
				.append($("<span>").text(t("cowork.work.list.add"))).appendTo(adder)
				.on("click", () => {
					adder.empty();
					const control = frappe.ui.form.make_control({parent: adder, render_input: true, only_input: true,
						df: {fieldtype: "Data", fieldname: "list_title", placeholder: t("cowork.work.list.placeholder")}});
					const actions = $('<div class="pearnly-work-compose-actions">').appendTo(adder);
					const submit = async () => {
						const title = (control.get_input_value() || "").trim();
						if (!title) return;
						await p.call(`${API}create_list`, {title}, "POST");
						await reload();
						draw_adder();
						adder.find(".pearnly-work-add").trigger("click");
					};
					$('<button type="button" class="btn btn-primary btn-xs">').text(t("cowork.work.list.add"))
						.appendTo(actions).on("click", submit);
					$('<button type="button" class="btn btn-default btn-xs">').text(__("Cancel"))
						.appendTo(actions).on("click", draw_adder);
					control.$input.on("keydown", event => {
						if (event.key === "Enter") { event.preventDefault(); void submit(); }
						if (event.key === "Escape") { event.preventDefault(); draw_adder(); }
					}).trigger("focus");
				});
		};
		draw_adder();

		const rename_list = (row, title) => {
			if (title.find("input").length) return;
			const label = title.find(".kanban-title").addClass("hide");
			const control = frappe.ui.form.make_control({parent: $('<span class="pearnly-work-rename">').appendTo(title),
				render_input: true, only_input: true, df: {fieldtype: "Data", fieldname: "rename"}});
			control.set_input(row.title);
			let done = false;
			const finish = async save => {
				if (done) return;
				done = true;
				const value = (control.get_input_value() || "").trim();
				control.$wrapper.closest(".pearnly-work-rename").remove();
				label.removeClass("hide");
				if (save && value && value !== row.title) {
					label.text(value);
					await p.call(`${API}update_list`, {name: row.name, values: JSON.stringify({title: value})}, "POST");
					await reload();
				}
			};
			control.$input.on("keydown", event => {
				if (event.key === "Enter") { event.preventDefault(); void finish(true); }
				if (event.key === "Escape") { event.preventDefault(); void finish(false); }
			}).on("blur", () => void finish(true)).on("click", event => event.stopPropagation())
				.trigger("focus").trigger("select");
		};

		// ── 画卡片 ───────────────────────────────────────────────────────────────
		const quick_items = (card, mirror) => (mirror ? [
			["open", "cowork.work.quick.open", "square-arrow-out-up-right"],
			["unmirror", "cowork.work.mirror.remove", "x"],
		] : [
			["open", "cowork.work.quick.open", "square-arrow-out-up-right"],
			["labels", "cowork.work.quick.labels", "tag"],
			["members", "cowork.work.quick.members", "user"],
			["cover", "cowork.work.quick.cover", "image"],
			["due", "cowork.work.quick.due", "clock"],
			["move", "cowork.work.quick.move", "move-right"],
			["copy", "cowork.work.quick.copy", "copy"],
			["mirror", "cowork.work.mirror.add", "copy-plus"],
			["link", "cowork.work.quick.link", "link"],
			["template", card.is_template ? "cowork.work.template.unset" : "cowork.work.template.set", "layout-template"],
			["watch", card.watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch", "eye"],
			["archive", "cowork.work.card.archive", "archive"],
		]).map(([action, key, name]) => `<button type="button" class="dropdown-item" data-quick="${action}">
			${icon(name)}<span>${p.escape(t(key))}</span></button>`).join("");
		const badge_html = (name, text, title, extra = "") => `<span class="pearnly-work-badge ${extra}"
			title="${p.escape(title)}">${icon(name)}${text === "" ? "" : `<span>${p.escape(text)}</span>`}</span>`;
		const field_text = (field, value) => {
			if (field.fieldtype === "checkbox") return value ? field.title : "";
			if (field.fieldtype === "date") return `${field.title}: ${frappe.datetime.str_to_user(value)}`;
			return `${field.title}: ${value}`;
		};
		const card_html = (card, mirror = null, show_list = false) => {
			const done = Boolean(card.completed);
			const cover = card.cover_url
				? `<div class="pearnly-work-cover image" style="background-image: url(&quot;${p.escape(css_url(card.cover_url))}&quot;)"></div>`
				: (card.cover_color ? `<div class="pearnly-work-cover ${card.cover_color}"></div>` : "");
			const labels = card.labels.map(label_of).filter(Boolean).map(label_pill).join("");
			const badges = [];
			if (card.is_template) badges.push(badge_html("layout-template", t("cowork.work.template.badge"), "", "template"));
			if (mirror) badges.push(badge_html("copy-plus", t("cowork.work.mirror.badge"), list_of(card.board_list)?.title || ""));
			if (show_list) badges.push(badge_html("columns-3", list_of(card.board_list)?.title || "", t("cowork.work.field.list")));
			if (card.watching) badges.push(badge_html("eye", "", t("cowork.work.quick.watching")));
			if (card.due_date || card.start_date) {
				badges.push(badge_html("clock", due_text(card), t("cowork.work.field.due"),
					card.due_date ? `due ${due_state(card)}` : ""));
			}
			if (card.recurrence && card.recurrence !== "never") {
				badges.push(badge_html("repeat", "", t(`cowork.work.repeat.${card.recurrence}`)));
			}
			if (card.has_description) badges.push(badge_html("align-left", "", t("cowork.work.field.description")));
			if (card.comments) badges.push(badge_html("message-square", card.comments, t("cowork.work.field.comments")));
			if (card.attachments) badges.push(badge_html("paperclip", card.attachments, t("cowork.work.field.attachments")));
			if (card.checklist_total) {
				badges.push(badge_html("square-check", `${card.checklist_done}/${card.checklist_total}`,
					t("cowork.work.field.checklist"), card.checklist_done === card.checklist_total ? "complete" : ""));
			}
			const values = state.fields.filter(field => field.show_on_card && card.values?.[field.name])
				.map(field => field_text(field, card.values[field.name])).filter(Boolean)
				.map(text => `<span class="pearnly-work-field-chip">${p.escape(text)}</span>`).join("");
			const people = card.assignees.map(avatar).join("");
			const meta = badges.length || people ? `<div class="pearnly-work-meta">
				<div class="pearnly-work-badges">${badges.join("")}</div>
				<div class="avatar-group overlap right">${people}</div></div>` : "";
			const done_label = t(done ? "cowork.work.card.undone" : "cowork.work.card.done");
			return `<div class="kanban-card-wrapper pearnly-work-card-wrap" data-name="${p.escape(card.name)}"
				${mirror ? `data-mirror="${p.escape(mirror)}"` : ""}>
				<div class="pearnly-work-card ${done ? "is-done" : ""} ${cover ? "has-cover" : ""} ${mirror ? "is-mirror" : ""}">
					${cover}
					<div class="pearnly-work-card-body">
						${labels ? `<div class="pearnly-work-labels">${labels}</div>` : ""}
						<div class="pearnly-work-title-row">
							${mirror || card.is_template ? "" : `<button type="button" class="pearnly-work-done" data-action="done"
								title="${p.escape(done_label)}" aria-label="${p.escape(done_label)}">
								${icon(done ? "circle-check" : "circle", "sm")}</button>`}
							<span class="pearnly-work-title">${p.escape(card.title)}</span>
						</div>
						${values ? `<div class="pearnly-work-fields">${values}</div>` : ""}
						${meta}
					</div>
					<div class="pearnly-work-card-tools">
						${mirror ? "" : `<button type="button" class="btn btn-xs" data-action="archive"
							title="${p.escape(t("cowork.work.card.archive"))}">${icon("archive")}</button>`}
						<button type="button" class="btn btn-xs" data-action="menu" data-popup aria-haspopup="menu"
							title="${p.escape(t("cowork.work.quick.title"))}">${icon("pencil")}</button>
					</div>
				</div></div>`;
		};
		const render = () => {
			sync_columns();
			for (const row of state.lists) {
				const items = [
					...state.cards.filter(card => card.board_list === row.name).map(card => ({position: card.position, html: card_html(card)})),
					...state.mirrors.filter(mirror => mirror.board_list === row.name && card_of(mirror.card))
						.map(mirror => ({position: mirror.position, html: card_html(card_of(mirror.card), mirror.name)})),
				].sort((a, b) => a.position - b.position);
				columns[row.name].list.html(items.map(item => item.html).join(""));
			}
			apply();
		};
		const apply = () => {
			for (const [, parts] of Object.entries(columns)) {
				let shown = 0;
				parts.list.children(".pearnly-work-card-wrap").each((_, node) => {
					const card = card_of(node.dataset.name);
					const visible = Boolean(card && matches(card));
					$(node).toggleClass("hide", !visible);
					shown += visible && !node.dataset.mirror ? 1 : 0;
				});
				parts.count.text(shown);
			}
			spy();
			render_view();
		};
		const upsert = card => {
			const index = state.cards.findIndex(row => row.name === card.name);
			if (index >= 0) state.cards[index] = card;
			else state.cards.push(card);
		};
		// 新加 / 复制出来的卡被当前筛选挡住时清空筛选，让它直接出现（用户 2026-09-27：搜索框里留着字，
		// 加完卡什么都看不见，以为没加上）。不弹提示；筛选框空了本身就说明发生了什么。
		const reveal = card => {
			if (!card || matches(card)) return;
			searching++;
			state.search_hits = null;
			for (const field of Object.values(filters)) field.set_input("");
		};

		let loading = null;
		const reload = async () => {
			if (loading) return loading;
			loading = (async () => {
				try {
					// 上次看的看板没了 / 没权限了：服务端退回默认看板（`fallback`），不弹错。
					const data = await p.call(`${API}board`, {board: state.board?.name || stored_board(), fallback: 1});
					Object.assign(state, {board: data.board, boards: data.boards, lists: data.lists, cards: data.cards,
						foreign: data.foreign || [], mirrors: data.mirrors || [],
						members: data.members, labels: data.labels, fields: data.fields || [], me: data.me,
						admin: data.admin, unread: data.unread, loaded_at: Date.now()});
					try { localStorage.setItem(BOARD_KEY, data.board.name); } catch (error) { /* 无痕窗口 */ }
					refresh_filter_options();
					show_rights();
					render();
					show_unread();
				} finally {
					loading = null;
				}
			})();
			return loading;
		};

		// 拖放：按落点的上下邻居交给服务端算位置；失败就按服务端重画。
		const dropped = async event => {
			const item = $(event.item);
			if (event.from === event.to && event.oldIndex === event.newIndex) return;
			const board_list = $(event.to).attr("data-list");
			const mirror = item.attr("data-mirror");
			const neighbour = node => node.attr("data-mirror") || node.attr("data-name") || null;
			try {
				if (mirror) {
					await p.call(`${API}move_mirror`, {name: mirror, board_list,
						before: neighbour(item.prevAll(".pearnly-work-card-wrap").first()),
						after: neighbour(item.nextAll(".pearnly-work-card-wrap").first())}, "POST");
					await reload();
					return;
				}
				const cards = ".pearnly-work-card-wrap:not([data-mirror])";
				upsert(await p.call(`${API}move`, {name: item.attr("data-name"), board_list,
					before: item.prevAll(cards).first().attr("data-name") || null,
					after: item.nextAll(cards).first().attr("data-name") || null}, "POST"));
				render();
			} catch (error) {
				await reload();
			}
		};
		const list_dropped = async event => {
			if (event.oldIndex === event.newIndex) return;
			const item = $(event.item);
			const siblings = ".pearnly-work-column:not(.pearnly-work-add-list)";
			try {
				await p.call(`${API}move_list`, {name: item.attr("data-list"),
					before: item.prevAll(siblings).first().attr("data-list") || null,
					after: item.nextAll(siblings).first().attr("data-list") || null}, "POST");
			} finally {
				await reload();
			}
		};
		root.on("click", ".pearnly-work-card-wrap", event => {
			if ($(event.target).closest(".pearnly-work-card-tools, .pearnly-work-done").length) return;
			const name = $(event.currentTarget).attr("data-name");
			if (name) void open_card(name);
		});
		root.on("click", ".pearnly-work-card-wrap [data-action]", event => {
			const button = $(event.currentTarget);
			const wrap = button.closest(".pearnly-work-card-wrap");
			const card = card_of(wrap.attr("data-name"));
			if (!card) return;
			event.stopPropagation();
			const mirror = wrap.attr("data-mirror");
			if (button.attr("data-action") === "menu") {
				open_popup(button, quick_items(card, mirror),
					item => void card_action(item.attr("data-quick"), card_of(card.name) || card, mirror));
				return;
			}
			void card_action(button.attr("data-action"), card, mirror);
		});

		// ── 卡片动作（圆圈、归档、快捷菜单） ───────────────────────────────────
		const archive = async card => {
			await p.call(`${API}archive`, {name: card.name}, "POST");
			state.cards = state.cards.filter(row => row.name !== card.name);
			render();
			p.success(t("cowork.work.card.archived"));
		};
		// 修改标签（照 Trello 的标签弹窗）：勾选已有标签；「新建标签」建好后回到这里，已勾的不丢。
		const edit_labels = (card, chosen) => {
			const d = small_dialog(t("cowork.work.quick.labels"), [
				{fieldname: "labels", fieldtype: "MultiCheck", columns: "10rem", sort_options: false,
					options: label_options(chosen)},
				// 新建标签只给看板管理员（用户 2026-09-29）；成员照常勾选已有标签。
				...(can("manage") ? [{fieldname: "add_label", fieldtype: "Button", label: t("cowork.work.labels.add"), click: () => {
					const picked = d.get_value("labels");
					d.hide();
					open_labels({add: true, after: () => edit_labels(card_of(card.name) || card, picked)});
				}}] : [])],
				(_, dialog) => quick_update(card, {labels: dialog.get_value("labels")}));
		};
		const quick_update = async (card, values) => {
			await p.call(`${API}update`, {name: card.name, modified: card.modified, values: JSON.stringify(values)}, "POST");
			await reload();
		};
		const small_dialog = (title, fields, action, label = __("Save")) => {
			const d = new frappe.ui.Dialog({title, fields, primary_action_label: label,
				primary_action: async values => {
					d.disable_primary_action();
					try { await action(values, d); d.hide(); } finally { d.enable_primary_action(); }
				}});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			d.show();
			return d;
		};
		const member_options = (chosen = []) => {
			const people = [...state.members];
			for (const user of chosen) if (!member(user)) people.push({user, name: user});
			return people.map(row => ({label: p.escape(row.name), value: row.user, checked: chosen.includes(row.user)}));
		};
		const label_options = (chosen = []) => state.labels.map(row => ({label: label_pill(row) || "&nbsp;",
			value: row.name, checked: chosen.includes(row.name)}));
		// 封面选项：无 / 颜色 / 这张卡的图片附件（值写成 `color:red`、`file:<File>`）。
		const cover_options = attachments => [
			{value: "", label: t("cowork.work.cover.none")},
			...COLORS.map(color => ({value: `color:${color}`, label: color_text(color)})),
			...attachments.filter(file => !file.is_link && IMAGES.test(file.file_name || ""))
				.map(file => ({value: `file:${file.name}`, label: file.file_name})),
		];
		const cover_value = card => (card.cover_file ? `file:${card.cover_file}` : (card.cover_color ? `color:${card.cover_color}` : ""));
		const cover_values = value => ({
			cover_color: value.startsWith("color:") ? value.slice(6) : "",
			cover_file: value.startsWith("file:") ? value.slice(5) : "",
		});
		const date_fields = card => [
			{fieldname: "start_date", fieldtype: "Date", label: t("cowork.work.field.start"), default: card.start_date},
			{fieldname: "due_date", fieldtype: "Date", label: t("cowork.work.field.due"), default: card.due_date},
			{fieldname: "due_time", fieldtype: "Time", label: t("cowork.work.field.dueTime"), default: card.due_time || ""},
			{fieldtype: "Column Break"},
			{fieldname: "reminder", fieldtype: "Select", label: t("cowork.work.field.reminder"),
				options: REMINDERS.map(value => ({value, label: t(`cowork.work.reminder.${value}`)})),
				default: card.reminder || "1d"},
			{fieldname: "recurrence", fieldtype: "Select", label: t("cowork.work.field.repeat"),
				options: REPEATS.map(value => ({value, label: t(`cowork.work.repeat.${value}`)})),
				default: card.recurrence || "never"},
		];
		const date_values = values => ({start_date: values.start_date || "", due_date: values.due_date || "",
			due_time: values.due_time || "", reminder: values.reminder || "1d", recurrence: values.recurrence || "never"});
		// 移动 / 复制 / 镜像的去处（照 Trello）：先选看板（只列能改的）、再选列表；换看板时列表跟着换。
		const editable_boards = () => state.boards.filter(row => row.rights.includes("edit"))
			.map(row => ({value: row.name, label: row.title}));
		const target_fields = (list, lists = null, label = t("cowork.work.field.list")) => [
			{fieldname: "target_board", fieldtype: "Select", reqd: 1, label: t("cowork.work.board.label"),
				options: editable_boards(), default: state.board.name},
			{fieldname: "board_list", fieldtype: "Select", reqd: 1, label, default: list, options: lists || list_options()},
		];
		const wire_targets = (d, exclude = null) => {
			const field = d.fields_dict.target_board;
			field.df.onchange = async () => {
				const board = d.get_value("target_board");
				const rows = board === state.board.name ? state.lists
					: await p.call(`${API}board_lists`, {board});
				const options = rows.filter(row => board !== state.board.name || row.name !== exclude)
					.map(row => ({value: row.name, label: row.title}));
				const list = d.fields_dict.board_list;
				list.df.options = options;
				list.refresh();
				list.set_input(options[0]?.value || "");
			};
		};
		const card_action = async (action, card, mirror) => {
			if (action === "done") {
				upsert(await p.call(`${API}toggle_done`, {name: card.name}, "POST"));
				render();
				return;
			}
			if (action === "archive") return archive(card);
			if (action === "open") return open_card(card.name);
			if (action === "unmirror") {
				await p.call(`${API}unmirror`, {name: mirror}, "POST");
				return reload();
			}
			if (action === "link") {
				frappe.utils.copy_to_clipboard(card_url(card.name));
				return;
			}
			if (action === "watch") {
				upsert(await p.call(`${API}watch`, {name: card.name, on: card.watching ? 0 : 1}, "POST"));
				render();
				return;
			}
			if (action === "template") return quick_update(card, {is_template: card.is_template ? 0 : 1});
			if (action === "labels") return edit_labels(card, card.labels);
			if (action === "members") {
				small_dialog(t("cowork.work.quick.members"), [{fieldname: "assignees", fieldtype: "MultiCheck",
					columns: "12rem", sort_options: false, options: member_options(card.assignees)}],
					(_, d) => quick_update(card, {assignees: d.get_value("assignees")}));
				return;
			}
			if (action === "due") {
				small_dialog(t("cowork.work.quick.due"), date_fields(card), values => quick_update(card, date_values(values)));
				return;
			}
			if (action === "cover") {
				const detail = await p.call(`${API}card`, {name: card.name});
				small_dialog(t("cowork.work.quick.cover"), [{fieldname: "cover", fieldtype: "Select",
					label: t("cowork.work.field.cover"), options: cover_options(detail.attachments),
					default: cover_value(detail)}],
					values => quick_update(card, cover_values(values.cover || "")));
				return;
			}
			if (action === "move") {
				const d = small_dialog(t("cowork.work.quick.move"), target_fields(card.board_list),
					async values => {
						if (values.board_list === card.board_list) return;
						await p.call(`${API}move`, {name: card.name, board_list: values.board_list}, "POST");
						await reload();
					}, t("cowork.work.quick.move"));
				wire_targets(d);
				return;
			}
			if (action === "mirror") {
				const targets = list_options().filter(option => option.value !== card.board_list);
				const d = small_dialog(t("cowork.work.mirror.add"), target_fields(targets[0]?.value || "", targets),
					async values => {
						await p.call(`${API}mirror`, {name: card.name, board_list: values.board_list}, "POST");
						await reload();
					}, t("cowork.work.mirror.add"));
				wire_targets(d, card.board_list);
				return;
			}
			if (action === "copy") {
				const copy_dialog = small_dialog(t("cowork.work.quick.copy"), [
					{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.title"), reqd: 1, default: card.title},
					{fieldname: "keep", fieldtype: "MultiCheck", label: t("cowork.work.copy.keep"), columns: 2,
						sort_options: false, options: [
							{value: "checklist", label: p.escape(t("cowork.work.field.checklist")), checked: 1},
							{value: "labels", label: p.escape(t("cowork.work.field.labels")), checked: 1},
							{value: "members", label: p.escape(t("cowork.work.field.members")), checked: 1},
							{value: "fields", label: p.escape(t("cowork.work.fields.manage")), checked: 1}]},
					...target_fields(card.board_list, null, t("cowork.work.copy.to")),
				], async (values, d) => {
					const copied = await p.call(`${API}copy`, {name: card.name, title: values.title, board_list: values.board_list,
						keep: JSON.stringify(d.get_value("keep") || [])}, "POST");
					if (copied.board !== state.board.name) return;
					upsert(copied);
					reveal(copied);
					render();
				}, t("cowork.work.copy.create"));
				wire_targets(copy_dialog);
			}
		};

		const list_action = async (row, button) => {
			const action = button.attr("data-action");
			if (action === "add") return open_composer(row.name);
			if (action === "sort") {
				await p.call(`${API}sort_list`, {name: row.name, key: button.attr("data-key")}, "POST");
				return reload();
			}
			if (action === "move-all") {
				await p.call(`${API}move_all`, {name: row.name, target: button.attr("data-target")}, "POST");
				return reload();
			}
			if (action === "color" || action === "done-list") {
				const values = action === "color" ? {color: button.attr("data-color") || ""} : {is_done_list: row.is_done_list ? 0 : 1};
				await p.call(`${API}update_list`, {name: row.name, values: JSON.stringify(values)}, "POST");
				return reload();
			}
			if (action === "archive-all") {
				const {archived} = await p.call(`${API}archive_all`, {name: row.name}, "POST");
				if (archived) p.success(t("cowork.work.card.archived"));
				return reload();
			}
			if (action === "archive-list") {
				await p.call(`${API}archive_list`, {name: row.name}, "POST");
				p.success(t("cowork.work.list.archived"));
				return reload();
			}
			if (action === "copy") {
				small_dialog(t("cowork.work.list.copy"), [{fieldname: "title", fieldtype: "Data", reqd: 1,
					label: t("cowork.work.list.name"), default: row.title}],
					async values => {
						await p.call(`${API}copy_list`, {name: row.name, title: values.title}, "POST");
						await reload();
					}, t("cowork.work.list.copy"));
				return;
			}
			if (action === "move") {
				const index = state.lists.findIndex(other => other.name === row.name);
				small_dialog(t("cowork.work.list.move"), [{fieldname: "position", fieldtype: "Select", reqd: 1,
					label: t("cowork.work.list.position"), default: String(index + 1),
					options: state.lists.map((_, position) => ({value: String(position + 1), label: String(position + 1)}))}],
					async values => {
						const others = state.lists.filter(other => other.name !== row.name);
						const at = Number(values.position) - 1;
						await p.call(`${API}move_list`, {name: row.name, before: others[at - 1]?.name || null,
							after: others[at]?.name || null}, "POST");
						await reload();
					}, t("cowork.work.list.move"));
			}
		};

		// ── 列尾快速新建（回车连续加，Esc 收起）与从模板建卡 ────────────────────
		const close_composers = () => {
			for (const parts of Object.values(columns)) {
				parts.compose.addClass("hide").empty();
				parts.footer.removeClass("hide");
			}
		};
		const open_composer = name => {
			close_composers();
			if (collapsed.has(name)) toggle_collapse(name);
			const {compose, footer, list} = columns[name];
			footer.addClass("hide");
			compose.removeClass("hide");
			const control = frappe.ui.form.make_control({
				parent: compose, render_input: true, only_input: true,
				df: {fieldtype: "Data", fieldname: "title", placeholder: t("cowork.work.card.placeholder")},
			});
			const actions = $('<div class="pearnly-work-compose-actions">').appendTo(compose);
			const submit_button = $('<button type="button" class="btn btn-primary btn-xs">')
				.text(t("cowork.work.card.add")).appendTo(actions);
			$('<button type="button" class="btn btn-default btn-xs">').text(__("Cancel"))
				.appendTo(actions).on("click", close_composers);
			let busy = false;
			const submit = async () => {
				const title = (control.get_input_value() || "").trim();
				if (!title || busy) return;
				busy = true;
				try {
					const created = await p.call(`${API}create`, {title, board_list: name}, "POST");
					upsert(created);
					reveal(created);
					render();
					// 回车同时触发了输入框原生的 change，控件会把刚才的标题写回模型；要连模型一起清掉，
					// 否则下一张卡的标题会接在上一张后面（测试机实测）。
					await control.set_value("");
					control.$input.val("");
					list.scrollTop(list[0].scrollHeight);
				} finally {
					busy = false;
					control.$input.trigger("focus");
				}
			};
			submit_button.on("click", submit);
			control.$input.on("keydown", event => {
				if (event.key === "Enter") { event.preventDefault(); void submit(); }
				if (event.key === "Escape") { event.preventDefault(); close_composers(); }
			});
			control.$input.trigger("focus");
		};
		const open_templates = (anchor, name) => {
			const templates = state.cards.filter(card => card.is_template);
			open_popup(anchor, `<h6 class="dropdown-header">${p.escape(t("cowork.work.template.title"))}</h6>
				${templates.length ? templates.map(card => menu_item("template", card.title, `data-name="${p.escape(card.name)}"`)).join("")
					: `<div class="pearnly-work-popup-pad text-muted small">${p.escape(t("cowork.work.template.empty"))}</div>`}`,
			button => {
				const template = card_of(button.attr("data-name"));
				if (!template) return;
				small_dialog(t("cowork.work.template.create"), [{fieldname: "title", fieldtype: "Data", reqd: 1,
					label: t("cowork.work.field.title"), default: template.title}],
					async values => {
						const created = await p.call(`${API}create`, {title: values.title, board_list: name, template: template.name}, "POST");
						upsert(created);
						reveal(created);
						render();
					}, t("cowork.work.copy.create"));
			});
		};

		// ── 卡片详情（原生 Dialog） ─────────────────────────────────────────────
		const activity_line = row => {
			const detail = row.detail || {};
			const args = {
				move: () => [list_name(t, detail.from), list_name(t, detail.to)],
				assign: () => [(detail.users || []).map(member_name).join(", ")],
				due: () => [detail.due ? frappe.datetime.str_to_user(detail.due) : t("cowork.work.activity.noDue")],
				repeat: () => [detail.due ? frappe.datetime.str_to_user(detail.due) : ""],
				attach: () => [detail.file || ""],
				detach: () => [detail.file || ""],
				copy: () => [detail.from || ""],
				mirror: () => [list_name(t, detail.to)],
				rule: () => [t(`cowork.work.rules.action_${detail.action}`), detail.user ? member_name(detail.user)
					: (detail.label || (detail.due ? frappe.datetime.str_to_user(detail.due) : ""))],
			}[row.action]?.() || [];
			return t(`cowork.work.activity.${row.action}`, ...args.map(p.escape))
				.replace("{who}", `<b>${p.escape(member_name(row.actor))}</b>`);
		};
		const activity_html = rows => rows.map(row => {
			const when = `<span class="text-muted small">${comment_when(row.creation)}</span>`;
			if (row.action === "comment") {
				const remove = row.actor === state.me ? `<button type="button" class="btn btn-link btn-xs text-muted"
					data-comment="${p.escape(row.name)}">${p.escape(__("Delete"))}</button>` : "";
				return `<div class="pearnly-work-activity">${avatar(row.actor)}<div class="pearnly-work-activity-body">
					<div><b>${p.escape(member_name(row.actor))}</b> ${when} ${remove}</div>
					<div class="pearnly-work-comment">${p.escape(row.content).replace(/\n/g, "<br>")}</div></div></div>`;
			}
			return `<div class="pearnly-work-activity">${avatar(row.actor)}<div class="pearnly-work-activity-body">
				<div>${activity_line(row)}</div><div>${when}</div></div></div>`;
		}).join("");
		const files_html = rows => rows.map(row => `<div class="pearnly-work-file">
			${row.is_link ? icon("link") : (IMAGES.test(row.file_name || "")
				? `<img src="${p.escape(encodeURI(row.file_url))}" alt="">` : icon("paperclip"))}
			<a href="${p.escape(row.is_link ? row.file_url : encodeURI(row.file_url))}" target="_blank" rel="noopener noreferrer">
				${p.escape(row.file_name)}</a>
			<span class="text-muted small">${comment_when(row.creation)}</span>
			<button type="button" class="btn btn-link btn-xs text-muted" data-file="${p.escape(row.name)}">
				${p.escape(__("Delete"))}</button></div>`).join("");

		// 评论里的 @：敲 @ 出成员名单（原生 Awesomplete），选中的人发评论时一起交给服务端去提醒。
		const mention_box = (input, mentioned) => {
			const at = /(?:^|\s)@([^@\s]*)$/;
			const box = new Awesomplete(input, {
				minChars: 0, maxItems: 8, autoFirst: true,
				list: state.members.map(row => ({label: row.name, value: row.user})),
				filter: (item, text) => {
					const found = at.exec(text);
					return Boolean(found) && item.label.toLowerCase().includes(found[1].toLowerCase());
				},
				item: item => {
					const li = document.createElement("li");
					li.textContent = item.label;
					return li;
				},
				replace: function (item) {
					this.input.value = this.input.value.replace(/@([^@\s]*)$/, `@${item.label} `);
					mentioned.set(item.value, item.label);
				},
			});
			input.addEventListener("input", () => (at.test(input.value) ? box.evaluate() : box.close()));
			return box;
		};

		// 多个清单（照 Trello）：每个清单一个标题 + 进度 + 项目（勾选、文字、删除）+「添加项目」；最下面「添加清单」。
		// 全部用原生控件（Data / Check）；改动留在弹窗里，按「保存」一起交。
		const checklist_editor = (host, groups, changed) => {
			const draw = () => {
				host.empty();
				groups.forEach((group, gi) => {
					const box = $('<div class="pearnly-work-checklist">').appendTo(host);
					const head = $('<div class="pearnly-work-checklist-head">').appendTo(box);
					$('<span class="pearnly-work-checklist-icon">').append(icon("square-check", "sm")).appendTo(head);
					const name = frappe.ui.form.make_control({parent: $('<div class="pearnly-work-checklist-name">').appendTo(head),
						render_input: true, only_input: true,
						df: {fieldtype: "Data", fieldname: `checklist_${gi}`, placeholder: t("cowork.work.field.checklist")}});
					name.set_input(group.title);
					name.$input.on("input", () => { group.title = name.get_input_value() || ""; changed(); });
					$('<button type="button" class="btn btn-xs btn-default">').text(__("Delete")).appendTo(head)
						.on("click", () => { groups.splice(gi, 1); draw(); changed(); });
					const total = group.items.filter(item => item.title.trim()).length;
					const complete = group.items.filter(item => item.title.trim() && item.done).length;
					const percent = total ? Math.round(complete * 100 / total) : 0;
					$(`<div class="pearnly-work-progress"><span>${percent}%</span>
						<div class="progress"><div class="progress-bar ${percent === 100 ? "bg-success" : ""}"
							style="width: ${percent}%"></div></div></div>`).appendTo(box);
					group.items.forEach((item, ii) => {
						const line = $('<div class="pearnly-work-checklist-item">').appendTo(box);
						const check = frappe.ui.form.make_control({parent: $('<div class="pearnly-work-checklist-check">').appendTo(line),
							render_input: true, only_input: true, df: {fieldtype: "Check", fieldname: `item_done_${gi}_${ii}`}});
						check.set_input(item.done ? 1 : 0);
						check.$input.on("change", () => { item.done = check.get_input_value() ? 1 : 0; draw(); changed(); });
						const text = frappe.ui.form.make_control({parent: $('<div class="pearnly-work-checklist-text">').appendTo(line),
							render_input: true, only_input: true,
							df: {fieldtype: "Data", fieldname: `item_${gi}_${ii}`, placeholder: t("cowork.work.field.item")}});
						text.set_input(item.title);
						text.$input.toggleClass("is-done", Boolean(item.done))
							.on("input", () => { item.title = text.get_input_value() || ""; changed(); });
						$('<button type="button" class="btn btn-xs btn-link text-muted">').append(icon("x"))
							.attr("aria-label", __("Delete")).appendTo(line)
							.on("click", () => { group.items.splice(ii, 1); draw(); changed(); });
					});
					$('<button type="button" class="btn btn-xs btn-default pearnly-work-checklist-add">')
						.text(t("cowork.work.checklist.addItem")).appendTo(box)
						.on("click", () => {
							group.items.push({title: "", done: 0});
							draw();
							changed();
							host.find(`[data-fieldname="item_${gi}_${group.items.length - 1}"]`).trigger("focus");
						});
				});
				$('<button type="button" class="btn btn-xs btn-default">').text(t("cowork.work.checklist.add")).appendTo(host)
					.on("click", () => {
						groups.push({title: t("cowork.work.field.checklist"), items: []});
						draw();
						changed();
					});
			};
			draw();
		};

		const field_control = field => {
			const base = {fieldname: `cf_${field.name}`, label: field.title};
			if (field.fieldtype === "number") return {...base, fieldtype: "Data"};
			if (field.fieldtype === "date") return {...base, fieldtype: "Date"};
			if (field.fieldtype === "checkbox") return {...base, fieldtype: "Check"};
			if (field.fieldtype === "select") {
				return {...base, fieldtype: "Select", options: [{value: "", label: ""},
					...field.options.map(value => ({value, label: value}))]};
			}
			return {...base, fieldtype: "Data"};
		};

		const open_card = async name => {
			const data = await p.call(`${API}card`, {name});
			// 卡片在别的看板（提醒、卡片链接、别的看板镜像过来的卡）：先切到那块板（照 Trello），列表 / 标签 / 字段才对得上。
			if (data.board !== state.board?.name) await switch_board(data.board);
			const editable = data.rights.includes("edit");
			let details = (() => { try { return localStorage.getItem(DETAILS) !== "0"; } catch (error) { return true; } })();
			let modified = data.modified;
			let watching = data.watching;
			let attachments = data.attachments;
			const groups = data.checklists.map(group => ({title: group.title,
				items: group.items.map(item => ({title: item.title, done: item.done}))}));
			const custom = state.fields.map(field => ({...field_control(field),
				default: field.fieldtype === "checkbox" ? (data.values[field.name] ? 1 : 0) : (data.values[field.name] || "")}));
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.card.title"),
				size: "large",
				fields: [
					{fieldname: "cover_preview", fieldtype: "HTML"},
					{fieldtype: "Section Break"},
					{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.title"), reqd: 1,
						default: data.title, length: 200},
					{fieldname: "completed", fieldtype: "Check", label: t("cowork.work.field.completed"), default: data.completed},
					{fieldtype: "Column Break"},
					{fieldname: "board_list", fieldtype: "Select", label: t("cowork.work.field.list"), reqd: 1,
						options: list_options(), default: data.board_list},
					{fieldtype: "Section Break", label: t("cowork.work.field.dates")},
					...date_fields(data),
					{fieldtype: "Section Break"},
					{fieldname: "assignees", fieldtype: "MultiCheck", label: t("cowork.work.field.members"),
						columns: "12rem", sort_options: false, options: member_options(data.assignees)},
					{fieldtype: "Column Break"},
					{fieldname: "labels", fieldtype: "MultiCheck", label: t("cowork.work.field.labels"),
						columns: "10rem", sort_options: false, options: label_options(data.labels)},
					{fieldname: "cover", fieldtype: "Select", label: t("cowork.work.field.cover"),
						options: cover_options(attachments), default: cover_value(data)},
					...(custom.length ? [{fieldtype: "Section Break", label: t("cowork.work.fields.manage")},
						...custom.flatMap((field, index) => (index % 2 ? [{fieldtype: "Column Break"}, field] : [field]))] : []),
					{fieldtype: "Section Break"},
					{fieldname: "description", fieldtype: "Text Editor", label: t("cowork.work.field.description"),
						default: data.description},
					{fieldtype: "Section Break", label: t("cowork.work.field.checklist")},
					{fieldname: "checklists", fieldtype: "HTML"},
					{fieldtype: "Section Break", label: t("cowork.work.field.attachments")},
					{fieldname: "files", fieldtype: "HTML"},
					{fieldname: "upload", fieldtype: "Button", label: t("cowork.work.file.upload"), click: () => void upload()},
					{fieldname: "add_link", fieldtype: "Button", label: t("cowork.work.file.link"), click: () => add_link()},
					{fieldtype: "Section Break", label: t("cowork.work.field.activity")},
					{fieldname: "details", fieldtype: "Button", click: () => toggle_details(),
						label: t(details ? "cowork.work.activity.hideDetails" : "cowork.work.activity.showDetails")},
					{fieldname: "comment", fieldtype: "Small Text", label: t("cowork.work.comment.label")},
					{fieldname: "send", fieldtype: "Button", label: t("cowork.work.comment.send"), click: () => void send()},
					{fieldname: "activity", fieldtype: "HTML"},
				],
				primary_action_label: __("Save"),
				primary_action: () => void save(),
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			if (!data.rights.includes("comment")) {
				for (const fieldname of ["comment", "send"]) d.set_df_property(fieldname, "hidden", 1);
			}
			if (!editable) {
				// 只读（观察者）：字段都只读，没有保存、上传、加链接、归档；清单、附件也不能改。
				for (const field of d.fields) {
					if (!d.fields_dict[field.fieldname] || ["activity", "details", "comment", "send", "cover_preview",
						"files", "checklists"].includes(field.fieldname)) continue;
					if (["upload", "add_link"].includes(field.fieldname)) d.set_df_property(field.fieldname, "hidden", 1);
					// 下拉只读时框架显示的是代码（列表号、1d），改成禁用输入，照常显示名字。
					else if (field.fieldtype === "Select") d.fields_dict[field.fieldname].$input?.prop("disabled", true);
					else d.set_df_property(field.fieldname, "read_only", 1);
				}
				d.$wrapper.addClass("pearnly-work-readonly");
				d.$body.find('[data-fieldname="assignees"] input, [data-fieldname="labels"] input').prop("disabled", true);
			}
			const draw_cover = () => {
				const {cover_color, cover_file} = cover_values(d.get_value("cover") || "");
				const file = attachments.find(row => row.name === cover_file);
				const preview = d.fields_dict.cover_preview.$wrapper;
				preview.html(file
					? `<div class="pearnly-work-cover image large" style="background-image: url(&quot;${p.escape(css_url(file.file_url))}&quot;)"></div>`
					: (cover_color ? `<div class="pearnly-work-cover large ${cover_color}"></div>` : ""));
				// 封面照 Trello 占满弹窗顶部；没有封面时这一节整个收起来。
				preview.closest(".form-section").toggle(Boolean(file || cover_color));
			};
			const draw_files = rows => {
				attachments = rows;
				d.fields_dict.files.$wrapper.html(files_html(rows));
				const field = d.fields_dict.cover;
				const value = field.get_value();
				field.df.options = cover_options(rows);
				field.refresh();
				field.set_input(field.df.options.some(option => option.value === value) ? value : "");
				draw_cover();
			};
			// 「显示详情 / 隐藏详情」（照 Trello）：隐藏时只看评论；每人自己的偏好，记在浏览器本地。
			let activity_rows = data.activity;
			const draw_activity = rows => {
				activity_rows = rows;
				d.fields_dict.activity.$wrapper.html(activity_html(details ? rows : rows.filter(row => row.action === "comment")));
			};
			const toggle_details = () => {
				details = !details;
				try { localStorage.setItem(DETAILS, details ? "1" : "0"); } catch (error) { /* 无痕窗口 */ }
				d.fields_dict.details.$input.text(t(details ? "cowork.work.activity.hideDetails" : "cowork.work.activity.showDetails"));
				draw_activity(activity_rows);
			};
			draw_files(data.attachments);
			draw_activity(data.activity);
			const mentioned = new Map();
			mention_box(d.fields_dict.comment.$input[0], mentioned);

			// 「保存」只在有改动时出现（与录入复核页同一口径）。评论框不算改动。
			const current = () => {
				const values = d.get_values(true) || {};
				const custom_values = {};
				for (const field of state.fields) {
					const value = d.get_value(`cf_${field.name}`);
					custom_values[field.name] = field.fieldtype === "checkbox" ? (value ? 1 : "") : (value || "");
				}
				return {
					title: (values.title || "").trim(), board_list: values.board_list || "",
					completed: values.completed ? 1 : 0, ...date_values(values),
					assignees: [...(d.get_value("assignees") || [])].sort(),
					labels: [...(d.get_value("labels") || [])].sort(),
					cover: d.get_value("cover") || "",
					description: d.get_value("description") || "",
					checklists: groups.map(group => ({title: group.title.trim(),
						items: group.items.filter(item => item.title.trim()).map(item => ({title: item.title.trim(), done: item.done ? 1 : 0}))})),
					values: custom_values,
				};
			};
			let base = null;
			const dirty_keys = () => {
				if (!base) return [];
				const now = current();
				return Object.keys(now).filter(key => JSON.stringify(now[key]) !== JSON.stringify(base[key]));
			};
			const refresh_dirty = () => {
				d.get_primary_btn().toggleClass("hide", !dirty_keys().length);
				draw_cover();
			};
			const watch = frappe.utils.debounce(refresh_dirty, 120);
			checklist_editor(d.fields_dict.checklists.$wrapper, groups, watch);
			if (!editable) d.fields_dict.checklists.$wrapper.find("input, button").prop("disabled", true);
			d.$body.on("input change click keyup", watch);
			d.get_primary_btn().addClass("hide");
			if (!data.archived) {
				if (editable) {
					d.add_custom_action(t("cowork.work.card.archive"), async () => {
						await archive({name});
						d.hide();
					}, "btn-default");
				}
				d.add_custom_action(t(watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch"),
					async () => {
						const result = await p.call(`${API}watch`, {name, on: watching ? 0 : 1}, "POST");
						watching = result.watching;
						upsert(result);
						render();
						d.footer.find(".pearnly-work-watch")
							.text(t(watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch"));
					}, "btn-default pearnly-work-watch");
			}
			d.onhide = () => {
				// 从卡片链接打开的：关掉后回到看板；在视图里打开的留在视图。
				const [root_route, second] = frappe.get_route();
				if (root_route === ROOT && second && second !== "view") frappe.set_route(ROOT);
			};
			d.show();
			// 富文本编辑器是异步装上的；装好后再取基线，免得一打开就算"改过"。
			setTimeout(() => { base = current(); refresh_dirty(); }, 400);

			let saving = false;
			const save = async () => {
				const keys = dirty_keys();
				if (!keys.length || saving) return;
				const now = current();
				if (!now.title) {
					frappe.msgprint(t("cowork.work.error.title"));
					return;
				}
				saving = true;
				d.disable_primary_action();
				try {
					const values = {};
					for (const key of keys) {
						if (key === "cover") Object.assign(values, cover_values(now.cover));
						else values[key] = now[key];
					}
					const result = await p.call(`${API}update`, {name, modified, values: JSON.stringify(values)}, "POST");
					modified = result.modified;
					if (keys.includes("recurrence") || keys.includes("completed")) {
						// 重复到期的卡"完成"后到期日已经滚到下一次：把弹窗里的日期和完成勾对上服务端。
						d.set_value("due_date", result.due_date || "");
						d.set_value("completed", result.completed ? 1 : 0);
					}
					draw_activity(result.activity);
					base = current();
					refresh_dirty();
					p.success(t("cowork.work.card.saved"));
					await reload();
				} finally {
					saving = false;
					d.enable_primary_action();
				}
			};
			const send = async () => {
				const content = (d.get_value("comment") || "").trim();
				if (!content) return;
				const mentions = [...mentioned].filter(([, label]) => content.includes(`@${label}`)).map(([user]) => user);
				draw_activity(await p.call(`${API}comment`, {name, content, mentions: JSON.stringify(mentions)}, "POST"));
				d.set_value("comment", "");
				mentioned.clear();
				void reload();
			};
			const refresh_activity = async () => {
				const fresh = await p.call(`${API}card`, {name});
				draw_activity(fresh.activity);
				void reload();
			};
			const upload = async () => {
				const picked = await p.pick_files("", true);
				for (const file of picked) {
					draw_files(await p.post_files(`${API}attach`, {name, file}));
				}
				if (picked.length) await refresh_activity();
			};
			const add_link = () => {
				small_dialog(t("cowork.work.file.link"), [
					{fieldname: "url", fieldtype: "Data", options: "URL", reqd: 1, label: t("cowork.work.file.url")},
					{fieldname: "text", fieldtype: "Data", label: t("cowork.work.file.text")},
				], async values => {
					draw_files(await p.call(`${API}attach_link`, {name, url: values.url, text: values.text || ""}, "POST"));
					await refresh_activity();
				}, t("cowork.work.file.insert"));
			};
			d.fields_dict.files.$wrapper.on("click", "[data-file]", async event => {
				draw_files(await p.call(`${API}detach`, {name, file: $(event.currentTarget).attr("data-file")}, "POST"));
				if (base) base.cover = current().cover;
				await refresh_activity();
			});
			d.fields_dict.activity.$wrapper.on("click", "[data-comment]", async event => {
				draw_activity(await p.call(`${API}delete_comment`, {activity: $(event.currentTarget).attr("data-comment")}, "POST"));
				void reload();
			});
		};

		// ── 提醒（铃铛） ─────────────────────────────────────────────────────────
		const open_notifications = async () => {
			const d = new frappe.ui.Dialog({title: t("cowork.work.notify.title"), size: "large",
				fields: [{fieldname: "list", fieldtype: "HTML"}],
				primary_action_label: t("cowork.work.notify.readAll"),
				primary_action: async () => draw(await p.call(`${API}read_notifications`, {}, "POST"))});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			const host = d.fields_dict.list.$wrapper;
			const draw = data => {
				state.unread = data.unread;
				show_unread();
				host.html(data.rows.length ? data.rows.map(row => `<button type="button"
					class="pearnly-work-notice ${row.is_read ? "" : "unread"}" data-notice="${p.escape(row.name)}"
					data-card="${p.escape(row.card)}">
					${row.actor ? avatar(row.actor) : `<span class="pearnly-work-notice-icon">${icon("clock", "sm")}</span>`}
					<span class="pearnly-work-notice-body"><span>${p.escape(notice_text(t, row))}</span>
					${row.detail?.text ? `<span class="pearnly-work-notice-quote">${p.escape(row.detail.text)}</span>` : ""}
					<span class="text-muted small">${comment_when(row.creation)}</span></span></button>`).join("")
					: `<div class="text-muted">${p.escape(t("cowork.work.notify.empty"))}</div>`);
			};
			host.on("click", "[data-notice]", async event => {
				const button = $(event.currentTarget);
				d.hide();
				await p.call(`${API}read_notifications`, {names: JSON.stringify([button.attr("data-notice")])}, "POST");
				void reload();
				void open_card(button.attr("data-card"));
			});
			d.show();
			draw(await p.call(`${API}notifications`));
		};

		// ── 标签（全板共用）：照 Trello，一行一个标签，名字 + 直接点选的色块 ──────
		const open_labels = ({add = false, after = null} = {}) => {
			const rows = state.labels.map(row => ({name: row.name, title: row.title || "", color: row.color}));
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.labels.manage"),
				fields: [{fieldname: "rows", fieldtype: "HTML"},
					{fieldname: "add", fieldtype: "Button", label: t("cowork.work.labels.add"), click: () => {
						rows.push({name: null, title: "", color: "blue"});
						draw();
						host.find(".pearnly-work-label-edit").last().find("input").trigger("focus");
					}}],
				primary_action_label: __("Save"),
				primary_action: async () => {
					d.disable_primary_action();
					try {
						state.labels = await p.call(`${API}save_labels`, {board: state.board.name, rows: JSON.stringify(rows.map(row => ({
							name: row.name, title: row.title.trim(), color: row.color})))}, "POST");
						d.hide();
						p.success(t("cowork.work.labels.saved"));
						await reload();
						if (after) after();
					} finally {
						d.enable_primary_action();
					}
				},
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			const host = d.fields_dict.rows.$wrapper;
			const draw = () => {
				host.empty();
				rows.forEach((row, index) => {
					const line = $('<div class="pearnly-work-label-edit">').appendTo(host);
					const head = $('<div class="pearnly-work-label-edit-head">').appendTo(line);
					const preview = $(label_pill(row)).appendTo(head);
					const name = frappe.ui.form.make_control({parent: $('<div class="pearnly-work-label-edit-name">').appendTo(head),
						render_input: true, only_input: true,
						df: {fieldtype: "Data", fieldname: `label_${index}`, placeholder: t("cowork.work.labels.name")}});
					name.set_input(row.title);
					name.$input.on("input", () => {
						row.title = name.get_input_value() || "";
						preview.text(row.title);
					});
					$('<button type="button" class="btn btn-xs btn-link text-muted">').append(icon("trash-2", "sm"))
						.attr({title: __("Delete"), "aria-label": __("Delete")}).appendTo(head)
						.on("click", () => { rows.splice(index, 1); draw(); });
					$(swatches(row.color)).appendTo(line).on("click", "[data-color]", event => {
						row.color = $(event.currentTarget).attr("data-color");
						draw();
					});
				});
			};
			if (add) rows.push({name: null, title: "", color: "blue"});
			draw();
			d.show();
			// 弹窗动画结束时框架会把焦点放到第一个输入框；新建的那一行要等它之后再聚焦。
			if (add) d.$wrapper.one("shown.bs.modal", () =>
				host.find(".pearnly-work-label-edit").last().find("input").trigger("focus"));
		};

		// ── 自定义字段（全板共用，照 Trello）：名字、类型、下拉选项、是否显示在卡片正面 ──
		const open_fields = () => {
			const rows = state.fields.map(row => ({name: row.name, title: row.title, fieldtype: row.fieldtype,
				options: (row.options || []).join("\n"), show_on_card: row.show_on_card}));
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.fields.manage"), size: "large",
				fields: [{fieldname: "rows", fieldtype: "HTML"},
					{fieldname: "add", fieldtype: "Button", label: t("cowork.work.fields.add"), click: () => {
						rows.push({name: null, title: "", fieldtype: "text", options: "", show_on_card: 1});
						draw();
						host.find(".pearnly-work-field-edit").last().find("input").first().trigger("focus");
					}}],
				primary_action_label: __("Save"),
				primary_action: async () => {
					d.disable_primary_action();
					try {
						state.fields = await p.call(`${API}save_fields`, {board: state.board.name, rows: JSON.stringify(rows.map(row => ({
							name: row.name, title: row.title.trim(), fieldtype: row.fieldtype, show_on_card: row.show_on_card ? 1 : 0,
							options: row.options.split("\n").map(value => value.trim()).filter(Boolean)})))}, "POST");
						d.hide();
						p.success(t("cowork.work.fields.saved"));
						await reload();
					} finally {
						d.enable_primary_action();
					}
				},
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			const host = d.fields_dict.rows.$wrapper;
			const draw = () => {
				host.empty();
				rows.forEach((row, index) => {
					const box = $('<div class="pearnly-work-field-edit">').appendTo(host);
					const form = new frappe.ui.FieldGroup({body: box, fields: [
						{fieldname: "title", fieldtype: "Data", label: t("cowork.work.fields.name"), default: row.title},
						{fieldtype: "Column Break"},
						row.name ? {fieldname: "type_text", fieldtype: "Data", read_only: 1, label: t("cowork.work.fields.type"),
							default: t(`cowork.work.fieldtype.${row.fieldtype}`)}
							: {fieldname: "fieldtype", fieldtype: "Select", label: t("cowork.work.fields.type"), default: row.fieldtype,
								options: FIELD_TYPES.map(value => ({value, label: t(`cowork.work.fieldtype.${value}`)}))},
						{fieldtype: "Column Break"},
						{fieldname: "show_on_card", fieldtype: "Check", label: t("cowork.work.fields.showOnCard"), default: row.show_on_card},
						{fieldtype: "Section Break"},
						{fieldname: "options", fieldtype: "Small Text", label: t("cowork.work.fields.options"), default: row.options,
							depends_on: `eval:${JSON.stringify(row.fieldtype === "select")}`},
					]});
					form.make();
					const sync = () => {
						const values = form.get_values(true) || {};
						row.title = values.title || "";
						row.show_on_card = values.show_on_card ? 1 : 0;
						row.options = values.options || "";
						if (!row.name && values.fieldtype && values.fieldtype !== row.fieldtype) {
							row.fieldtype = values.fieldtype;
							draw();
						}
					};
					box.on("input change", sync);
					$('<button type="button" class="btn btn-xs btn-default">').text(__("Delete")).appendTo(box)
						.on("click", () => { rows.splice(index, 1); draw(); });
				});
			};
			draw();
			d.show();
		};

		// ── 已归档（卡片与列表） ───────────────────────────────────────────────
		const open_archived = async () => {
			const d = new frappe.ui.Dialog({title: t("cowork.work.archived.title"), size: "large",
				fields: [{fieldname: "cards", fieldtype: "HTML"},
					{fieldtype: "Section Break", label: t("cowork.work.archived.lists")},
					{fieldname: "lists", fieldtype: "HTML"},
					{fieldtype: "Section Break", label: t("cowork.work.archived.boards")},
					{fieldname: "boards", fieldtype: "HTML"}]});
			d.$wrapper.addClass("pearnly-dialog");
			const card_host = d.fields_dict.cards.$wrapper;
			const list_host = d.fields_dict.lists.$wrapper;
			const board_host = d.fields_dict.boards.$wrapper;
			const tables = [];
			// 恢复要能改这块板，彻底删除要能管这块板（看板管理员 / Cowork 管理员）。
			const buttons = (value, restore, remove) => `${can("edit") ? `<button type="button" class="btn btn-default btn-xs"
				${restore}="${p.escape(value)}">${p.escape(t("cowork.work.archived.restore"))}</button>` : ""}
				${can("manage") && remove ? `<button type="button" class="btn btn-danger btn-xs" ${remove}="${p.escape(value)}">
				${p.escape(__("Delete"))}</button>` : ""}`;
			// 表格要在弹窗显示出来之后再画（宽度才算得出来），同盘点记录弹窗。
			const draw = async () => {
				const data = await p.call(`${API}archived`, {board: state.board.name});
				board_host.closest(".form-section").toggle(Boolean(data.boards.length));
				while (tables.length) tables.pop()?.destroy?.();
				tables.push(await p.table(card_host, [
					{name: t("cowork.work.field.title"), format: p.escape},
					{name: t("cowork.work.field.list"), format: p.escape},
					{name: t("cowork.work.archived.at"), format: value => p.escape(p.datetime_text(value))},
					{name: "", sortable: false, format: value => buttons(value, "data-restore", "data-delete")},
				], data.cards.map(row => [row.title, row.list_title, row.archived_at, row.name])));
				tables.push(await p.table(list_host, [
					{name: t("cowork.work.list.name"), format: p.escape},
					{name: t("cowork.work.archived.at"), format: value => p.escape(p.datetime_text(value))},
					{name: "", sortable: false, format: value => buttons(value, "data-restore-list", "data-delete-list")},
				], data.lists.map(row => [row.title, row.archived_at, row.name])));
				if (data.boards.length) {
					tables.push(await p.table(board_host, [
						{name: t("cowork.work.board.name"), format: p.escape},
						{name: t("cowork.work.archived.at"), format: value => p.escape(p.datetime_text(value))},
						{name: "", sortable: false, format: value => `<button type="button" class="btn btn-default btn-xs"
							data-restore-board="${p.escape(value)}">${p.escape(t("cowork.work.archived.restore"))}</button>`},
					], data.boards.map(row => [row.title, row.archived_at, row.name])));
				}
			};
			const act = async (method, name, message, sound) => {
				await p.call(`${API}${method}`, {name}, "POST");
				p.success(t(message), sound);
				await Promise.all([draw(), reload()]);
			};
			d.$body.on("click", "[data-restore]", event => void act("restore", $(event.currentTarget).attr("data-restore"),
				"cowork.work.archived.restored"));
			d.$body.on("click", "[data-restore-list]", event => void act("restore_list",
				$(event.currentTarget).attr("data-restore-list"), "cowork.work.archived.restored"));
			d.$body.on("click", "[data-restore-board]", async event => {
				const board = $(event.currentTarget).attr("data-restore-board");
				await p.call(`${API}restore_board`, {board}, "POST");
				p.success(t("cowork.work.archived.restored"));
				d.hide();
				await switch_board(board);
			});
			// 彻底删除不可恢复，按原生确认框走一次。
			d.$body.on("click", "[data-delete]", event => {
				const target = $(event.currentTarget).attr("data-delete");
				frappe.confirm(t("cowork.work.archived.confirmDelete"),
					() => void act("delete", target, "cowork.work.archived.deleted", "delete"));
			});
			d.$body.on("click", "[data-delete-list]", event => {
				const target = $(event.currentTarget).attr("data-delete-list");
				frappe.confirm(t("cowork.work.list.confirmDelete"),
					() => void act("delete_list", target, "cowork.work.archived.deleted", "delete"));
			});
			d.on_page_show = () => void draw();
			d.onhide = () => { while (tables.length) tables.pop()?.destroy?.(); };
			d.show();
		};

		// ── 视图：我的任务 / 日历 / 表格（都用同一份数据和筛选） ────────────────
		const current_view = () => {
			const [root_route, second, third] = frappe.get_route();
			return root_route === ROOT && second === "view" && VIEWS.includes(third) ? third : "board";
		};
		const shown_cards = () => state.cards.filter(card => !card.is_template && matches(card));
		const show_view = () => {
			const view = current_view();
			// 视图的路由多了几段，框架的面包屑会丢掉页面名：每次切换都把标题补回来。
			if (view !== "board") p.breadcrumbs({label: __("cowork.nav.work"), route: p.subroute(ROOT)});
			for (const [name, pane] of Object.entries(view_panes)) pane.toggleClass("hide", name !== view);
			views_list.find("[data-view]").each((_, node) => $(node).toggleClass("active", node.dataset.view === view));
			render_view();
			requestAnimationFrame(fit_board);
		};
		const render_view = () => {
			const view = current_view();
			if (view === "mine") render_mine();
			if (view === "calendar") void render_calendar();
			if (view === "table") void render_table();
		};

		// 我的任务（照 Trello「我的卡片」）：分给我的卡，按到期分组；点卡片照常打开详情。
		const render_mine = () => {
			const pane = view_panes.mine.empty();
			const today = frappe.datetime.get_today();
			const groups = {overdue: [], today: [], week: [], later: [], none: [], done: []};
			for (const card of shown_cards().filter(card => card.assignees.includes(state.me))) {
				if (card.completed) groups.done.push(card);
				else if (!card.due_date) groups.none.push(card);
				else {
					const days = frappe.datetime.get_diff(card.due_date, today);
					groups[days < 0 ? "overdue" : (days === 0 ? "today" : (days <= 7 ? "week" : "later"))].push(card);
				}
			}
			const empty = Object.values(groups).every(list => !list.length);
			if (empty) {
				pane.append($('<div class="text-muted pearnly-work-empty">').text(t("cowork.work.mine.empty")));
				return;
			}
			for (const [key, cards] of Object.entries(groups)) {
				if (!cards.length) continue;
				cards.sort((a, b) => String(a.due_date || "").localeCompare(String(b.due_date || "")));
				const section = $('<section class="pearnly-work-mine-group">').appendTo(pane);
				$('<h6 class="pearnly-work-mine-title">').text(`${t(`cowork.work.mine.${key}`)} · ${cards.length}`).appendTo(section);
				$('<div class="pearnly-work-mine-cards">').html(cards.map(card => card_html(card, null, true)).join("")).appendTo(section);
			}
		};

		// 日历（Frappe 自带的 FullCalendar）：卡片按到期日排在月 / 周视图里，拖到别的日子就改到期日。
		let calendar = null;
		const calendar_events = () => shown_cards().filter(card => card.due_date).map(card => ({
			id: card.name, title: card.title, allDay: !card.due_time,
			start: card.due_time ? `${card.due_date}T${hhmm(card.due_time)}` : card.due_date,
			classNames: ["pearnly-work-event", card.completed ? "is-done" : `due-${due_state(card) || "later"}`,
				...(list_of(card.board_list)?.color ? [`tint-${list_of(card.board_list).color}`] : [])],
		}));
		const style_calendar = () => {
			// 照原生日历视图（`frappe.views.Calendar.set_css`）：工具栏按钮换成 Desk 原生按钮样式。
			view_panes.calendar.find("button.fc-button").removeClass("fc-button-primary").addClass("btn btn-default btn-sm");
		};
		const render_calendar = async () => {
			if (!calendar) {
				await frappe.require("calendar.bundle.js");
				if (calendar || current_view() !== "calendar") return;
				const host = $('<div class="pearnly-work-calendar">').appendTo(view_panes.calendar);
				calendar = new frappe.FullCalendar(host[0], {
					plugins: frappe.FullCalendar.Plugins, initialView: "dayGridMonth", locale: frappe.boot.lang,
					firstDay: frappe.datetime.get_first_day_of_the_week_index(), eventDisplay: "block",
					headerToolbar: {left: "prev,title,next", center: "", right: "today,dayGridMonth,timeGridWeek"},
					buttonText: {today: __("Today"), month: __("Month"), week: __("Week")},
					editable: true, height: "auto", dayMaxEvents: 4, nowIndicator: true,
					events: (info, success) => success(calendar_events()),
					datesSet: style_calendar,
					eventClick: info => { info.jsEvent.preventDefault(); void open_card(info.event.id); },
					eventDrop: async info => {
						const card = card_of(info.event.id);
						const start = info.event.start;
						const pad = value => String(value).padStart(2, "0");
						const values = {due_date: `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`,
							due_time: info.event.allDay ? (card.due_time || "") : `${pad(start.getHours())}:${pad(start.getMinutes())}`};
						try {
							await quick_update(card, values);
						} catch (error) {
							info.revert();
						}
					},
				});
				calendar.render();
				style_calendar();
				return;
			}
			calendar.refetchEvents();
			calendar.updateSize();
		};

		// 表格（照 Trello 表格视图）：一张卡一行，原生 DataTable（列头可排序）；可导出 Excel（就是表里这些卡）。
		let table = null;
		const render_table = async () => {
			const pane = view_panes.table;
			if (!pane.children().length) {
				const bar = $('<div class="pearnly-work-table-bar">').appendTo(pane);
				$('<button type="button" class="btn btn-default btn-sm">').append(icon("download", "sm"))
					.append($("<span>").text(t("cowork.work.table.export"))).appendTo(bar)
					.on("click", () => {
						const names = shown_cards().map(card => card.name).join(",");
						window.open(`/api/method/${API}export?board=${encodeURIComponent(state.board.name)}&names=${encodeURIComponent(names)}`);
					});
				$('<div class="pearnly-work-table">').appendTo(pane);
				pane.on("click", "[data-open]", event => {
					event.preventDefault();
					void open_card($(event.currentTarget).attr("data-open"));
				});
			}
			const host = pane.find(".pearnly-work-table");
			const lists = Object.fromEntries(state.lists.map((row, index) => [row.name, index]));
			const cards = shown_cards().sort((a, b) => (lists[a.board_list] - lists[b.board_list]) || (a.position - b.position));
			const field_cell = (field, value) => (value ? field_text({...field, title: ""}, value).replace(/^: /, "") || "✓" : "");
			table?.destroy?.();
			table = await p.table(host, [
				{name: t("cowork.work.field.title"), format: (value, row) => `<a href="#" data-open="${p.escape(row[row.length - 1])}">${p.escape(value)}</a>`},
				{name: t("cowork.work.field.list"), format: p.escape},
				{name: t("cowork.work.field.labels"), format: value => value},
				{name: t("cowork.work.field.members"), format: p.escape},
				{name: t("cowork.work.field.due"), format: (value, row) => p.escape(value)},
				{name: t("cowork.work.field.completed"), format: value => (value ? icon("check") : "")},
				{name: t("cowork.work.field.checklist"), format: p.escape},
				...state.fields.map(field => ({name: field.title, format: p.escape})),
				{name: "", format: () => "", width: 1},
			], cards.map(card => [
				card.title, list_of(card.board_list)?.title || "",
				card.labels.map(label_of).filter(Boolean).map(label_pill).join(" "),
				card.assignees.map(member_name).join(", "),
				card.due_date ? `${frappe.datetime.str_to_user(card.due_date)}${card.due_time ? ` ${hhmm(card.due_time)}` : ""}` : "",
				card.completed ? 1 : 0,
				card.checklist_total ? `${card.checklist_done}/${card.checklist_total}` : "",
				...state.fields.map(field => field_cell(field, card.values?.[field.name])),
				card.name,
			]));
		};

		// ── 自动化（照 Trello「卡片被添加到列表中时」）：进入某列表时自动分派 / 加标签 / 设到期日 / 完成 / 加关注 ──
		const open_rules = async () => {
			const rows = (await p.call(`${API}rules`, {board: state.board.name})).map(row => ({...row}));
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.rules.manage"), size: "large",
				fields: [{fieldname: "rows", fieldtype: "HTML"},
					{fieldname: "add", fieldtype: "Button", label: t("cowork.work.rules.add"), click: () => {
						rows.push({name: null, board_list: state.lists[0]?.name, action: "assign", user: state.me, label: null,
							days: 1, enabled: 1});
						draw();
					}}],
				primary_action_label: __("Save"),
				primary_action: async () => {
					d.disable_primary_action();
					try {
						await p.call(`${API}save_rules`, {board: state.board.name, rows: JSON.stringify(rows)}, "POST");
						d.hide();
						p.success(t("cowork.work.rules.saved"));
					} finally {
						d.enable_primary_action();
					}
				},
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			const host = d.fields_dict.rows.$wrapper;
			const draw = () => {
				host.empty();
				rows.forEach((row, index) => {
					const box = $('<div class="pearnly-work-field-edit">').appendTo(host);
					const target = {
						assign: {fieldname: "user", fieldtype: "Select", label: t("cowork.work.field.members"), default: row.user,
							options: state.members.map(item => ({value: item.user, label: item.name}))},
						watch: {fieldname: "user", fieldtype: "Select", label: t("cowork.work.field.members"), default: row.user,
							options: state.members.map(item => ({value: item.user, label: item.name}))},
						label: {fieldname: "label", fieldtype: "Select", label: t("cowork.work.field.labels"), default: row.label,
							options: state.labels.map(item => ({value: item.name, label: item.title || color_text(item.color)}))},
						due_days: {fieldname: "days", fieldtype: "Int", label: t("cowork.work.rules.days"), default: row.days},
					}[row.action];
					const form = new frappe.ui.FieldGroup({body: box, fields: [
						{fieldname: "board_list", fieldtype: "Select", label: t("cowork.work.rules.when"), default: row.board_list,
							options: list_options()},
						{fieldtype: "Column Break"},
						{fieldname: "action", fieldtype: "Select", label: t("cowork.work.rules.then"), default: row.action,
							options: RULE_ACTIONS.map(value => ({value, label: t(`cowork.work.rules.action_${value}`)}))},
						{fieldtype: "Column Break"},
						target || {fieldname: "spacer", fieldtype: "HTML"},
						{fieldtype: "Column Break"},
						{fieldname: "enabled", fieldtype: "Check", label: t("cowork.work.rules.enabled"), default: row.enabled},
					]});
					form.make();
					box.on("input change", () => {
						const values = form.get_values(true) || {};
						const action_changed = values.action && values.action !== row.action;
						Object.assign(row, {board_list: values.board_list, action: values.action || row.action,
							enabled: values.enabled ? 1 : 0});
						if (target) row[target.fieldname] = values[target.fieldname];
						if (action_changed) {
							if (["assign", "watch"].includes(row.action) && !row.user) row.user = state.me;
							if (row.action === "label" && !row.label) row.label = state.labels[0]?.name || null;
							draw();
						}
					});
					$('<button type="button" class="btn btn-xs btn-default">').text(__("Delete")).appendTo(box)
						.on("click", () => { rows.splice(index, 1); draw(); });
				});
			};
			draw();
			d.show();
		};

		// ── 看板：切换、新建、设置、成员（照 Trello；原生 Dialog） ───────────────
		const switch_board = async name => {
			if (!name || name === state.board?.name) return;
			state.board = {name, rights: []};
			try { localStorage.setItem(BOARD_KEY, name); } catch (error) { /* 无痕窗口 */ }
			// 换板时筛选清空（成员、标签都是那块板的）。
			searching++;
			state.search_hits = null;
			for (const field of Object.values(filters)) field.set_input("");
			await reload();
		};
		const board_fields = (row = {}) => [
			{fieldname: "title", fieldtype: "Data", reqd: 1, label: t("cowork.work.board.name"), default: row.title || ""},
			{fieldname: "visibility", fieldtype: "Select", reqd: 1, label: t("cowork.work.board.visibility"),
				default: row.visibility || "team",
				options: VISIBILITIES.map(value => ({value, label: t(`cowork.work.board.visibility_${value}`)}))},
		];
		const create_board = () => {
			small_dialog(t("cowork.work.board.create"), board_fields(), async values => {
				const created = await p.call(`${API}create_board`, {values: JSON.stringify(values)}, "POST");
				await switch_board(created.name);
			}, t("cowork.work.board.create"));
		};
		const open_board_settings = () => {
			const row = state.board;
			const d = small_dialog(t("cowork.work.board.settings"), [
				...board_fields(row),
				{fieldname: "commenting", fieldtype: "Select", reqd: 1, label: t("cowork.work.board.commenting"),
					default: row.commenting,
					options: COMMENTING.map(value => ({value, label: t(`cowork.work.board.commenting_${value}`)}))},
				{fieldname: "member_management", fieldtype: "Select", reqd: 1, label: t("cowork.work.board.memberManagement"),
					default: row.member_management,
					options: MEMBER_MANAGEMENT.map(value => ({value, label: t(`cowork.work.board.memberManagement_${value}`)}))},
			], async values => {
				await p.call(`${API}update_board`, {board: row.name, values: JSON.stringify(values)}, "POST");
				await reload();
			});
			// 默认看板不能归档；归档可以在「已归档」里恢复，所以不另外确认。
			if (!row.is_default) {
				d.add_custom_action(t("cowork.work.board.archive"), async () => {
					await p.call(`${API}archive_board`, {board: row.name}, "POST");
					d.hide();
					p.success(t("cowork.work.board.archived"));
					state.board = null;
					try { localStorage.removeItem(BOARD_KEY); } catch (error) { /* 无痕窗口 */ }
					await reload();
				}, "btn-default");
			}
		};
		// 成员（照 Trello「共享」）：谁在这块板上、什么角色；能加减成员的人才看得到添加和移除，
		// 改角色 / 动管理员只给看板管理员；自己随时可以离开。改了立即保存。
		const open_members = async () => {
			const d = new frappe.ui.Dialog({title: t("cowork.work.board.members"), size: "large",
				fields: [{fieldname: "rows", fieldtype: "HTML"},
					{fieldtype: "Section Break"},
					{fieldname: "user", fieldtype: "Select", label: t("cowork.work.board.addMember")},
					{fieldtype: "Column Break"},
					{fieldname: "role", fieldtype: "Select", label: t("cowork.work.board.role"), default: "member"},
					{fieldtype: "Column Break"},
					{fieldname: "add", fieldtype: "Button", label: t("cowork.work.board.add"), click: () => void add()}]});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
			let data = null;
			const host = d.fields_dict.rows.$wrapper;
			const role_options = manage => BOARD_ROLES.filter(role => manage || role !== "admin")
				.map(value => ({value, label: t(`cowork.work.board.role_${value}`)}));
			const save = async rows => {
				data = await p.call(`${API}save_board_members`, {board: state.board.name,
					rows: JSON.stringify(rows.map(row => ({user: row.user, role: row.role})))}, "POST");
				if (!data.board) {
					// 自己离开了一块看不到的私有板：回到默认看板。
					d.hide();
					state.board = null;
					try { localStorage.removeItem(BOARD_KEY); } catch (error) { /* 无痕窗口 */ }
				}
				await reload();
				if (data.board) draw();
			};
			const draw = () => {
				const rights = data.board.rights;
				const manage = rights.includes("manage");
				const members_right = rights.includes("members");
				host.empty();
				for (const row of data.members) {
					const line = $('<div class="pearnly-work-member">').appendTo(host);
					$(avatar(row.user)).appendTo(line);
					$('<span class="pearnly-work-member-name ellipsis">').text(row.name).appendTo(line);
					if (manage) {
						const control = frappe.ui.form.make_control({parent: $('<div class="pearnly-work-member-role">').appendTo(line),
							render_input: true, only_input: true,
							df: {fieldtype: "Select", fieldname: `role_${row.user}`, options: role_options(true)}});
						control.set_input(row.role);
						control.$input.on("change", () => void save(data.members.map(item =>
							(item.user === row.user ? {...item, role: control.get_input_value()} : item))));
					} else {
						$('<span class="text-muted">').text(t(`cowork.work.board.role_${row.role}`)).appendTo(line);
					}
					const removable = row.user === state.me || manage || (members_right && row.role !== "admin");
					if (removable) {
						$('<button type="button" class="btn btn-xs btn-default">')
							.text(t(row.user === state.me ? "cowork.work.board.leave" : "cowork.work.board.remove")).appendTo(line)
							.on("click", () => void save(data.members.filter(item => item.user !== row.user)));
					}
				}
				const can_add = members_right && data.candidates.length;
				for (const fieldname of ["user", "role", "add"]) d.set_df_property(fieldname, "hidden", can_add ? 0 : 1);
				d.fields_dict.add.$wrapper.closest(".form-section").toggle(Boolean(can_add));
				if (can_add) {
					d.set_df_property("user", "options", data.candidates.map(row => ({value: row.user, label: row.name})));
					d.set_df_property("role", "options", role_options(manage));
					d.set_value("user", "");
					d.set_value("role", "member");
				}
			};
			const add = async () => {
				const user = d.get_value("user");
				if (!user) return;
				await save([...data.members, {user, role: d.get_value("role") || "member"}]);
			};
			data = await p.call(`${API}board_members`, {board: state.board.name});
			d.show();
			draw();
		};

		// ── 多人同时在看：别人改了就刷新（原生 socket.io；连不上时手动刷新照常可用） ──
		// 服务端只推给能看那块板的人；这里再只认当前这块板（别的看板上的改动不用刷）。
		const on_change = frappe.utils.debounce(() => {
			if (frappe.get_route()[0] === ROOT) void reload();
		}, 500);
		frappe.realtime.off(EVENT);
		frappe.realtime.on(EVENT, data => {
			if (!data?.board || data.board === state.board?.name) on_change();
		});
		// 卡片链接 `/desk/pearnly-work/<卡片>`（复制链接、提醒点进来、LINE 菜单进来）：打开那张卡。
		const open_from_route = () => {
			const [root_route, name] = frappe.get_route();
			if (root_route === ROOT && name && name !== "view" && !$(".pearnly-work-dialog.show").length) void open_card(name);
		};
		const native_show = frappe.pages[ROOT].on_page_show;
		frappe.pages[ROOT].on_page_show = () => {
			native_show?.();
			show_view();
			if (state.loaded_at && Date.now() - state.loaded_at > 2000) void reload();
			open_from_route();
		};

		show_view();
		await reload();
		open_from_route();
	};
})();
