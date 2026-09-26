/* 工作协调：团队看板（用户 2026-09-27，照 Trello）。
 *
 * 一个团队一块板，三列固定（待办 / 进行中 / 已完成），拖到「已完成」就算完成，没有审核。
 * 卡片的样子与交互照 Trello（完成圆圈、悬停出归档 / 编辑、快捷菜单、封面、标签、徽标、头像），
 * 底色用我们自己的（用户 2026-09-27："底色还是用我们自己的"）：白底、原生灰列。
 * 拖放用原生 Sortable；详情、快捷编辑都是原生 Dialog / FieldGroup；筛选在页面原生筛选区；
 * 手机上三列纵排，顶上一条分区导航（照 ERP / 录入二级页）。
 * 状态、顺序、权限、提醒都在服务端（`pearnly_cowork.work`），页面只管交互。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.work.api.";
	const ROOT = "pearnly-work";
	const STATUSES = ["todo", "doing", "done"];
	const DOTS = {todo: "gray", doing: "orange", done: "green"};
	const COLORS = ["green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray"];
	const IMAGES = /\.(png|jpe?g|gif|webp)$/i;
	const EVENT = "cowork_work_changed";
	const NOTIFY = "cowork_work_notify";
	const COLLAPSED = "pearnly-work-collapsed";
	const mobile = () => window.matchMedia("(max-width: 767px)").matches;
	const icon = (name, size = "xs") => frappe.utils.icon(name, size);
	const card_url = name => `${window.location.origin}${p.subroute(ROOT, name)}`;
	// 放进 `style="background-image: url(...)"` 的地址：引号、括号都编码掉。
	const css_url = url => encodeURI(url || "").replace(/["'()]/g, char => `%${char.charCodeAt(0).toString(16)}`);

	// 浮在页面上的小菜单（卡片快捷菜单、列菜单）：列表会滚动、会裁切，所以挂在 body 上，
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
	window.addEventListener("scroll", close_popup, true);

	// 提醒的一句话：页面里的铃铛与在线时弹出的提示共用。`t` 是本页词条。
	const notice_text = (t, row) => {
		const who = row.actor_name || row.actor || "";
		const due = row.detail?.due ? frappe.datetime.str_to_user(row.detail.due) : "";
		const to = row.detail?.to ? t(`cowork.work.status.${row.detail.to}`) : "";
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
		const state = {cards: [], members: [], labels: [], column_colors: {}, me: frappe.session.user, admin: false,
			loaded_at: 0, unread: 0};
		const member = user => state.members.find(row => row.user === user);
		const member_name = user => member(user)?.name || user || "";
		const label_of = name => state.labels.find(row => row.name === name);
		const card_of = name => state.cards.find(row => row.name === name);
		const status_text = status => t(`cowork.work.status.${status}`);
		const color_text = color => t(`cowork.work.color.${color}`);

		const avatar = user => {
			const row = member(user);
			return frappe.avatar(null, "avatar-small", row?.name || user, row?.image || "");
		};
		const label_pill = label => `<span class="pearnly-work-label ${label.color}"
			title="${p.escape(label.title || color_text(label.color))}">${p.escape(label.title)}</span>`;
		// 到期：已完成绿、过期红、今明两天橙，其余不上色（照 Trello）。
		const due_state = card => {
			if (card.status === "done") return "done";
			const days = frappe.datetime.get_diff(card.due_date, frappe.datetime.get_today());
			return days < 0 ? "overdue" : (days <= 1 ? "soon" : "");
		};

		// ── 筛选（页面原生筛选区） ────────────────────────────────────────────
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
		filters.keyword.$input.on("input", frappe.utils.debounce(() => apply(), 150));
		const set_options = (field, options) => {
			const value = field.get_value();
			field.df.options = options;
			field.refresh();
			field.set_input(options.some(option => option.value === value) ? value : "");
		};
		const refresh_filter_options = () => {
			set_options(filters.member, [{value: "", label: t("cowork.work.filter.anyMember")},
				{value: "__me", label: t("cowork.work.filter.mine")},
				{value: "__none", label: t("cowork.work.filter.noMember")},
				...state.members.map(row => ({value: row.user, label: row.name}))]);
			set_options(filters.label, [{value: "", label: t("cowork.work.filter.anyLabel")},
				...state.labels.map(row => ({value: row.name, label: row.title || color_text(row.color)}))]);
		};
		const matches = card => {
			const keyword = (filters.keyword.get_value() || "").trim().toLowerCase();
			if (keyword && !card.title.toLowerCase().includes(keyword)) return false;
			const who = filters.member.get_value();
			if (who === "__me" && !card.assignees.includes(state.me)) return false;
			if (who === "__none" && card.assignees.length) return false;
			if (who && !who.startsWith("__") && !card.assignees.includes(who)) return false;
			const label = filters.label.get_value();
			if (label && !card.labels.includes(label)) return false;
			const due = filters.due.get_value();
			if (due === "none") return !card.due_date;
			if (due && (!card.due_date || card.status === "done")) return false;
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
		page.add_menu_item(t("cowork.work.labels.manage"), () => open_labels());
		page.add_menu_item(t("cowork.work.archived.title"), () => open_archived());

		// ── 看板骨架 ─────────────────────────────────────────────────────────────
		const root = $('<div class="pearnly-work">').appendTo(page.main);
		const nav = $('<nav class="form-tabs-list pearnly-work-nav">').appendTo(root);
		const nav_list = $('<ul class="nav form-tabs">').appendTo(nav);
		const board = $('<div class="kanban pearnly-work-board">').appendTo(root);
		const columns = {};
		const collapsed = new Set((() => {
			try { return JSON.parse(localStorage.getItem(COLLAPSED) || "[]"); } catch (error) { return []; }
		})());
		const save_collapsed = () => {
			try { localStorage.setItem(COLLAPSED, JSON.stringify([...collapsed])); } catch (error) { /* 无痕窗口 */ }
		};
		// 色块（照 Trello 直接点选）：列表颜色、标签颜色共用；当前选中的打勾。
		const swatches = (current, attrs = "") => `<div class="pearnly-work-swatches">${COLORS.map(color =>
			`<button type="button" class="pearnly-work-swatch ${color} ${color === current ? "selected" : ""}" ${attrs}
				data-color="${color}" title="${p.escape(color_text(color))}" aria-label="${p.escape(color_text(color))}"
				aria-pressed="${color === current}">${color === current ? icon("check") : ""}</button>`).join("")}</div>`;
		const column_menu = status => {
			const others = STATUSES.filter(other => other !== status);
			const item = (action, label, extra = "") =>
				`<button type="button" class="dropdown-item" data-column-action="${action}" ${extra}>${p.escape(label)}</button>`;
			return `${item("add", t("cowork.work.card.add"))}
				<div class="dropdown-divider"></div>
				<h6 class="dropdown-header">${p.escape(t("cowork.work.column.sort"))}</h6>
				${["created_desc", "created_asc", "due", "title"].map(key =>
					item("sort", t(`cowork.work.column.sort_${key}`), `data-key="${key}"`)).join("")}
				<div class="dropdown-divider"></div>
				<h6 class="dropdown-header">${p.escape(t("cowork.work.column.moveAll"))}</h6>
				${others.map(other => item("move-all", status_text(other), `data-target="${other}"`)).join("")}
				<div class="dropdown-divider"></div>
				<h6 class="dropdown-header">${p.escape(t("cowork.work.column.color"))}</h6>
				${swatches(state.column_colors[status], "data-column-action=\"color\"")}
				<div class="pearnly-work-popup-pad">
					<button type="button" class="btn btn-default btn-xs btn-block" data-column-action="color" data-color="">
						${icon("x")} ${p.escape(t("cowork.work.column.removeColor"))}</button></div>
				<div class="dropdown-divider"></div>
				${item("archive-all", t("cowork.work.column.archiveAll"))}`;
		};
		for (const status of STATUSES) {
			const column = $(`<div class="kanban-column pearnly-work-column" data-status="${status}">`).appendTo(board);
			const header = $('<div class="kanban-column-header pearnly-work-column-header">').appendTo(column);
			$(`<span class="kanban-column-title">
				<span class="kanban-col-dot indicator ${DOTS[status]}"></span>
				<span class="kanban-title ellipsis"></span></span>`).appendTo(header)
				.find(".kanban-title").text(status_text(status));
			const tools = $('<span class="pearnly-work-column-tools">').appendTo(header);
			const count = $('<span class="pearnly-work-count">').appendTo(tools);
			$('<button type="button" class="btn btn-xs btn-link pearnly-work-collapse">')
				.attr({title: t("cowork.work.column.collapse"), "aria-label": t("cowork.work.column.collapse")})
				.append(icon("chevrons-left-right")).appendTo(tools)
				.on("click", event => { event.stopPropagation(); toggle_collapse(status); });
			$(`<button type="button" class="btn btn-xs btn-link" data-popup aria-haspopup="menu"
				aria-label="${p.escape(t("cowork.work.column.menu"))}">${icon("ellipsis", "sm")}</button>`).appendTo(tools)
				.on("click", event => {
					event.stopPropagation();
					open_popup($(event.currentTarget), column_menu(status), button => void column_action(status, button));
				});
			const list = $(`<div class="kanban-cards" data-status="${status}">`).appendTo(column);
			const add = $('<button type="button" class="pearnly-work-add">')
				.append(icon("plus", "sm")).append($("<span>").text(t("cowork.work.card.add"))).appendTo(column);
			const compose = $('<div class="pearnly-work-compose hide">').appendTo(column);
			add.on("click", () => open_composer(status));
			// 收起后整列变成一条竖条，点它展开。
			column.on("click", () => { if (column.hasClass("collapsed")) toggle_collapse(status); });
			const link = $('<button type="button" class="nav-link">').text(status_text(status))
				.on("click", () => column[0].scrollIntoView({behavior: "smooth", block: "start"}));
			$('<li class="nav-item">').append(link).appendTo(nav_list);
			columns[status] = {column, list, count, add, compose, link};
			new Sortable(list[0], {
				group: "pearnly-work-cards", animation: 150, draggable: ".pearnly-work-card-wrap",
				filter: ".pearnly-work-card-tools, .pearnly-work-done", preventOnFilter: false,
				ghostClass: "pearnly-work-ghost", dragClass: "pearnly-work-dragging",
				// 手机上长按才拖，免得滑动翻页时误拖。
				delay: 200, delayOnTouchOnly: true,
				onEnd: event => void dropped(event),
			});
		}
		// 手机上不收列（三列纵排，分区导航已经够用）。
		const show_collapsed = () => {
			for (const status of STATUSES) columns[status].column.toggleClass("collapsed", collapsed.has(status) && !mobile());
		};
		const toggle_collapse = status => {
			if (collapsed.has(status)) collapsed.delete(status); else collapsed.add(status);
			show_collapsed();
			save_collapsed();
		};
		show_collapsed();
		$(window).on("resize", frappe.utils.debounce(show_collapsed, 200));
		// 手机分区导航：滚动时标出当前看到的那一列。
		const spy = () => {
			if (!mobile()) return;
			let active = STATUSES[0];
			for (const status of STATUSES) {
				if (columns[status].column[0].getBoundingClientRect().top <= window.innerHeight / 3) active = status;
			}
			for (const status of STATUSES) columns[status].link.toggleClass("active", status === active);
		};
		$(window).on("scroll resize", frappe.utils.throttle(spy, 100));

		// ── 画卡片 ───────────────────────────────────────────────────────────────
		const quick_items = card => [
			["open", "cowork.work.quick.open", "square-arrow-out-up-right"],
			["labels", "cowork.work.quick.labels", "tag"],
			["members", "cowork.work.quick.members", "user"],
			["cover", "cowork.work.quick.cover", "image"],
			["due", "cowork.work.quick.due", "clock"],
			["move", "cowork.work.quick.move", "move-right"],
			["copy", "cowork.work.quick.copy", "copy"],
			["link", "cowork.work.quick.link", "link"],
			["watch", card.watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch", "eye"],
			["archive", "cowork.work.card.archive", "archive"],
		].map(([action, key, name]) => `<button type="button" class="dropdown-item" data-quick="${action}">
			${icon(name)}<span>${p.escape(t(key))}</span></button>`).join("");
		const badge_html = (name, text, title, extra = "") => `<span class="pearnly-work-badge ${extra}"
			title="${p.escape(title)}">${icon(name)}${text === "" ? "" : `<span>${p.escape(text)}</span>`}</span>`;
		const card_html = card => {
			const done = card.status === "done";
			const cover = card.cover_url
				? `<div class="pearnly-work-cover image" style="background-image: url(&quot;${p.escape(css_url(card.cover_url))}&quot;)"></div>`
				: (card.cover_color ? `<div class="pearnly-work-cover ${card.cover_color}"></div>` : "");
			const labels = card.labels.map(label_of).filter(Boolean).map(label_pill).join("");
			const badges = [];
			if (card.watching) badges.push(badge_html("eye", "", t("cowork.work.quick.watching")));
			if (card.due_date) {
				badges.push(badge_html("clock", frappe.datetime.str_to_user(card.due_date), t("cowork.work.field.due"),
					`due ${due_state(card)}`));
			}
			if (card.has_description) badges.push(badge_html("align-left", "", t("cowork.work.field.description")));
			if (card.comments) badges.push(badge_html("message-square", card.comments, t("cowork.work.field.comments")));
			if (card.attachments) badges.push(badge_html("paperclip", card.attachments, t("cowork.work.field.attachments")));
			if (card.checklist_total) {
				badges.push(badge_html("square-check", `${card.checklist_done}/${card.checklist_total}`,
					t("cowork.work.field.checklist"), card.checklist_done === card.checklist_total ? "complete" : ""));
			}
			const people = card.assignees.map(avatar).join("");
			const meta = badges.length || people ? `<div class="pearnly-work-meta">
				<div class="pearnly-work-badges">${badges.join("")}</div>
				<div class="avatar-group overlap right">${people}</div></div>` : "";
			const done_label = t(done ? "cowork.work.card.undone" : "cowork.work.card.done");
			return `<div class="kanban-card-wrapper pearnly-work-card-wrap" data-name="${p.escape(card.name)}">
				<div class="pearnly-work-card ${done ? "is-done" : ""} ${cover ? "has-cover" : ""}">
					${cover}
					<div class="pearnly-work-card-body">
						${labels ? `<div class="pearnly-work-labels">${labels}</div>` : ""}
						<div class="pearnly-work-title-row">
							<button type="button" class="pearnly-work-done" data-action="done"
								title="${p.escape(done_label)}" aria-label="${p.escape(done_label)}">
								${icon(done ? "circle-check" : "circle", "sm")}</button>
							<span class="pearnly-work-title">${p.escape(card.title)}</span>
						</div>
						${meta}
					</div>
					<div class="pearnly-work-card-tools">
						<button type="button" class="btn btn-xs" data-action="archive"
							title="${p.escape(t("cowork.work.card.archive"))}">${icon("archive")}</button>
						<button type="button" class="btn btn-xs" data-action="menu" data-popup aria-haspopup="menu"
							title="${p.escape(t("cowork.work.quick.title"))}">${icon("pencil")}</button>
					</div>
				</div></div>`;
		};
		const render = () => {
			for (const status of STATUSES) {
				const column = columns[status].column;
				column.removeClass(COLORS.map(color => `tint-${color}`).join(" "));
				if (state.column_colors?.[status]) column.addClass(`tint-${state.column_colors[status]}`);
			}
			const sorted = [...state.cards].sort((a, b) => a.position - b.position);
			for (const status of STATUSES) {
				const cards = sorted.filter(card => card.status === status);
				columns[status].list.html(cards.map(card_html).join(""));
			}
			apply();
		};
		const apply = () => {
			for (const status of STATUSES) {
				let shown = 0;
				columns[status].list.children(".pearnly-work-card-wrap").each((_, node) => {
					const card = card_of(node.dataset.name);
					const visible = Boolean(card && matches(card));
					$(node).toggleClass("hide", !visible);
					shown += visible ? 1 : 0;
				});
				columns[status].count.text(shown);
			}
			spy();
		};
		const upsert = card => {
			const index = state.cards.findIndex(row => row.name === card.name);
			if (index >= 0) state.cards[index] = card;
			else state.cards.push(card);
		};

		let loading = null;
		const reload = async () => {
			if (loading) return loading;
			loading = (async () => {
				try {
					const data = await p.call(`${API}board`);
					Object.assign(state, {cards: data.cards, members: data.members, labels: data.labels,
						column_colors: data.column_colors || {}, me: data.me, admin: data.admin, unread: data.unread,
						loaded_at: Date.now()});
					refresh_filter_options();
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
			const name = item.attr("data-name");
			const status = $(event.to).attr("data-status");
			const before = item.prevAll(".pearnly-work-card-wrap").first().attr("data-name") || null;
			const after = item.nextAll(".pearnly-work-card-wrap").first().attr("data-name") || null;
			try {
				upsert(await p.call(`${API}move`, {name, status, before, after}, "POST"));
				render();
			} catch (error) {
				await reload();
			}
		};
		board.on("click", ".pearnly-work-card-wrap", event => {
			if ($(event.target).closest(".pearnly-work-card-tools, .pearnly-work-done").length) return;
			const name = $(event.currentTarget).attr("data-name");
			if (name) void open_card(name);
		});
		board.on("click", "[data-action]", event => {
			const button = $(event.currentTarget);
			const name = button.closest(".pearnly-work-card-wrap").attr("data-name");
			const card = card_of(name);
			if (!card) return;
			event.stopPropagation();
			if (button.attr("data-action") === "menu") {
				open_popup(button, quick_items(card), item => void card_action(item.attr("data-quick"), card_of(card.name) || card));
				return;
			}
			void card_action(button.attr("data-action"), card);
		});

		// ── 卡片动作（圆圈、归档、快捷菜单） ───────────────────────────────────
		const archive = async card => {
			await p.call(`${API}archive`, {name: card.name}, "POST");
			state.cards = state.cards.filter(row => row.name !== card.name);
			render();
			p.success(t("cowork.work.card.archived"));
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
			...attachments.filter(file => IMAGES.test(file.file_name || ""))
				.map(file => ({value: `file:${file.name}`, label: file.file_name})),
		];
		const cover_value = card => (card.cover_file ? `file:${card.cover_file}` : (card.cover_color ? `color:${card.cover_color}` : ""));
		const cover_values = value => ({
			cover_color: value.startsWith("color:") ? value.slice(6) : "",
			cover_file: value.startsWith("file:") ? value.slice(5) : "",
		});
		const card_action = async (action, card) => {
			if (action === "done") {
				upsert(await p.call(`${API}toggle_done`, {name: card.name}, "POST"));
				render();
				return;
			}
			if (action === "archive") return archive(card);
			if (action === "open") return open_card(card.name);
			if (action === "link") {
				frappe.utils.copy_to_clipboard(card_url(card.name));
				return;
			}
			if (action === "watch") {
				upsert(await p.call(`${API}watch`, {name: card.name, on: card.watching ? 0 : 1}, "POST"));
				render();
				return;
			}
			if (action === "labels") {
				small_dialog(t("cowork.work.quick.labels"), [{fieldname: "labels", fieldtype: "MultiCheck",
					columns: "10rem", sort_options: false, options: label_options(card.labels)}],
					(_, d) => quick_update(card, {labels: d.get_value("labels")}));
				return;
			}
			if (action === "members") {
				small_dialog(t("cowork.work.quick.members"), [{fieldname: "assignees", fieldtype: "MultiCheck",
					columns: "12rem", sort_options: false, options: member_options(card.assignees)}],
					(_, d) => quick_update(card, {assignees: d.get_value("assignees")}));
				return;
			}
			if (action === "due") {
				small_dialog(t("cowork.work.quick.due"), [{fieldname: "due_date", fieldtype: "Date",
					label: t("cowork.work.field.due"), default: card.due_date}],
					values => quick_update(card, {due_date: values.due_date || ""}));
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
				small_dialog(t("cowork.work.quick.move"), [{fieldname: "status", fieldtype: "Select", reqd: 1,
					label: t("cowork.work.field.status"), default: card.status,
					options: STATUSES.map(status => ({value: status, label: status_text(status)}))}],
					async values => {
						if (values.status === card.status) return;
						upsert(await p.call(`${API}move`, {name: card.name, status: values.status}, "POST"));
						render();
					}, t("cowork.work.quick.move"));
				return;
			}
			if (action === "copy") {
				small_dialog(t("cowork.work.quick.copy"), [
					{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.title"), reqd: 1, default: card.title},
					{fieldname: "keep", fieldtype: "MultiCheck", label: t("cowork.work.copy.keep"), columns: 3,
						sort_options: false, options: [
							{value: "checklist", label: p.escape(t("cowork.work.field.checklist")), checked: 1},
							{value: "labels", label: p.escape(t("cowork.work.field.labels")), checked: 1},
							{value: "members", label: p.escape(t("cowork.work.field.members")), checked: 1}]},
					{fieldname: "status", fieldtype: "Select", reqd: 1, label: t("cowork.work.copy.to"), default: card.status,
						options: STATUSES.map(status => ({value: status, label: status_text(status)}))},
				], async (values, d) => {
					upsert(await p.call(`${API}copy`, {name: card.name, title: values.title, status: values.status,
						keep: JSON.stringify(d.get_value("keep") || [])}, "POST"));
					render();
				}, t("cowork.work.copy.create"));
			}
		};

		const column_action = async (status, button) => {
			const action = button.attr("data-column-action");
			if (action === "add") return open_composer(status);
			if (action === "sort") {
				await p.call(`${API}sort_column`, {status, key: button.attr("data-key")}, "POST");
				return reload();
			}
			if (action === "move-all") {
				await p.call(`${API}move_column`, {source: status, target: button.attr("data-target")}, "POST");
				return reload();
			}
			if (action === "color") {
				const {color} = await p.call(`${API}column_color`, {status, color: button.attr("data-color") || ""}, "POST");
				state.column_colors[status] = color;
				return render();
			}
			if (action === "archive-all") {
				const {archived} = await p.call(`${API}archive_column`, {status}, "POST");
				if (archived) p.success(t("cowork.work.card.archived"));
				return reload();
			}
		};

		// ── 列尾快速新建（回车连续加，Esc 收起） ───────────────────────────────
		const close_composers = () => {
			for (const status of STATUSES) {
				columns[status].compose.addClass("hide").empty();
				columns[status].add.removeClass("hide");
			}
		};
		const open_composer = status => {
			close_composers();
			if (collapsed.has(status)) toggle_collapse(status);
			const {compose, add, list} = columns[status];
			add.addClass("hide");
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
					upsert(await p.call(`${API}create`, {title, status}, "POST"));
					render();
					control.set_input("");
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

		// ── 卡片详情（原生 Dialog） ─────────────────────────────────────────────
		const activity_line = row => {
			const detail = row.detail || {};
			const args = {
				move: () => [status_text(detail.from), status_text(detail.to)],
				assign: () => [(detail.users || []).map(member_name).join(", ")],
				due: () => [detail.due ? frappe.datetime.str_to_user(detail.due) : t("cowork.work.activity.noDue")],
				attach: () => [detail.file || ""],
				detach: () => [detail.file || ""],
				copy: () => [detail.from || ""],
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
			${IMAGES.test(row.file_name || "") ? `<img src="${p.escape(encodeURI(row.file_url))}" alt="">` : icon("paperclip")}
			<a href="${p.escape(encodeURI(row.file_url))}" target="_blank" rel="noopener">${p.escape(row.file_name)}</a>
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

		const open_card = async name => {
			const data = await p.call(`${API}card`, {name});
			let modified = data.modified;
			let watching = data.watching;
			let attachments = data.attachments;
			const checklist_label = rows => {
				const done = rows.filter(row => row.done).length;
				return rows.length ? `${t("cowork.work.field.checklist")} ${done}/${rows.length}` : t("cowork.work.field.checklist");
			};
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.card.title"),
				size: "large",
				fields: [
					{fieldname: "cover_preview", fieldtype: "HTML"},
					{fieldtype: "Section Break"},
					{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.title"), reqd: 1,
						default: data.title, length: 200},
					{fieldtype: "Column Break"},
					{fieldname: "status", fieldtype: "Select", label: t("cowork.work.field.status"), reqd: 1,
						options: STATUSES.map(status => ({value: status, label: status_text(status)})), default: data.status},
					{fieldname: "due_date", fieldtype: "Date", label: t("cowork.work.field.due"), default: data.due_date},
					{fieldtype: "Section Break"},
					{fieldname: "assignees", fieldtype: "MultiCheck", label: t("cowork.work.field.members"),
						columns: "12rem", sort_options: false, options: member_options(data.assignees)},
					{fieldtype: "Column Break"},
					{fieldname: "labels", fieldtype: "MultiCheck", label: t("cowork.work.field.labels"),
						columns: "10rem", sort_options: false, options: label_options(data.labels)},
					{fieldname: "cover", fieldtype: "Select", label: t("cowork.work.field.cover"),
						options: cover_options(attachments), default: cover_value(data)},
					{fieldtype: "Section Break"},
					{fieldname: "description", fieldtype: "Text Editor", label: t("cowork.work.field.description"),
						default: data.description},
					{fieldtype: "Section Break"},
					{fieldname: "checklist", fieldtype: "Table", label: checklist_label(data.checklist),
						in_place_edit: true, cannot_add_rows: false,
						data: data.checklist.map(row => ({title: row.title, done: row.done})),
						fields: [
							{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.item"), in_list_view: 1, columns: 8},
							{fieldname: "done", fieldtype: "Check", label: t("cowork.work.field.done"), in_list_view: 1, columns: 2},
						]},
					{fieldtype: "Section Break", label: t("cowork.work.field.attachments")},
					{fieldname: "files", fieldtype: "HTML"},
					{fieldname: "upload", fieldtype: "Button", label: t("cowork.work.file.upload"), click: () => void upload()},
					{fieldtype: "Section Break", label: t("cowork.work.field.activity")},
					{fieldname: "comment", fieldtype: "Small Text", label: t("cowork.work.comment.label")},
					{fieldname: "send", fieldtype: "Button", label: t("cowork.work.comment.send"), click: () => void send()},
					{fieldname: "activity", fieldtype: "HTML"},
				],
				primary_action_label: __("Save"),
				primary_action: () => void save(),
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-dialog");
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
			const draw_activity = rows => d.fields_dict.activity.$wrapper.html(activity_html(rows));
			draw_files(data.attachments);
			draw_activity(data.activity);
			const mentioned = new Map();
			mention_box(d.fields_dict.comment.$input[0], mentioned);

			// 「保存」只在有改动时出现（与录入复核页同一口径）。评论框不算改动。
			const current = () => {
				const values = d.get_values(true) || {};
				return {
					title: (values.title || "").trim(), status: values.status || "", due_date: values.due_date || "",
					assignees: [...(d.get_value("assignees") || [])].sort(),
					labels: [...(d.get_value("labels") || [])].sort(),
					cover: d.get_value("cover") || "",
					description: d.get_value("description") || "",
					checklist: (d.get_value("checklist") || []).filter(row => (row.title || "").trim())
						.map(row => ({title: row.title.trim(), done: row.done ? 1 : 0})),
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
				d.fields_dict.checklist.$wrapper.find(".control-label").first()
					.text(checklist_label(current().checklist));
				draw_cover();
			};
			const watch = frappe.utils.debounce(refresh_dirty, 120);
			d.$body.on("input change click keyup", watch);
			d.get_primary_btn().addClass("hide");
			if (!data.archived) {
				d.add_custom_action(t("cowork.work.card.archive"), async () => {
					await archive({name});
					d.hide();
				}, "btn-default");
				const watch_button = d.add_custom_action(t(watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch"),
					async () => {
						const result = await p.call(`${API}watch`, {name, on: watching ? 0 : 1}, "POST");
						watching = result.watching;
						upsert(result);
						render();
						$(watch_button || d.footer.find(".pearnly-work-watch"))
							.text(t(watching ? "cowork.work.quick.unwatch" : "cowork.work.quick.watch"));
					}, "btn-default pearnly-work-watch");
			}
			d.onhide = () => {
				if (frappe.get_route()[0] === ROOT && frappe.get_route()[1]) frappe.set_route(ROOT);
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
			const upload = async () => {
				const picked = await p.pick_files("", true);
				for (const file of picked) {
					draw_files(await p.post_files(`${API}attach`, {name, file}));
				}
				if (picked.length) {
					const fresh = await p.call(`${API}card`, {name});
					draw_activity(fresh.activity);
					void reload();
				}
			};
			d.fields_dict.files.$wrapper.on("click", "[data-file]", async event => {
				draw_files(await p.call(`${API}detach`, {name, file: $(event.currentTarget).attr("data-file")}, "POST"));
				const fresh = await p.call(`${API}card`, {name});
				draw_activity(fresh.activity);
				base && (base.cover = current().cover);
				void reload();
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

		// ── 标签（全板共用） ───────────────────────────────────────────────────
		// 标签编辑照 Trello：每个标签一行，名字 + 直接点选的色块；没有下拉。
		const open_labels = () => {
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
						state.labels = await p.call(`${API}save_labels`, {rows: JSON.stringify(rows.map(row => ({
							name: row.name, title: row.title.trim(), color: row.color})))}, "POST");
						d.hide();
						p.success(t("cowork.work.labels.saved"));
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
					const line = $('<div class="pearnly-work-label-edit">').appendTo(host);
					const head = $('<div class="pearnly-work-label-edit-head">').appendTo(line);
					const preview = $(label_pill(row)).appendTo(head);
					const name = frappe.ui.form.make_control({parent: $("<div class=\"pearnly-work-label-edit-name\">").appendTo(head),
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
			draw();
			d.show();
		};

		// ── 已归档 ───────────────────────────────────────────────────────────────
		const open_archived = async () => {
			const d = new frappe.ui.Dialog({title: t("cowork.work.archived.title"), size: "large",
				fields: [{fieldname: "list", fieldtype: "HTML"}]});
			d.$wrapper.addClass("pearnly-dialog");
			const host = d.fields_dict.list.$wrapper;
			let table = null;
			// 表格要在弹窗显示出来之后再画（宽度才算得出来），同盘点记录弹窗。
			const draw = async () => {
				const rows = await p.call(`${API}archived`);
				table?.destroy?.();
				table = await p.table(host, [
					{name: t("cowork.work.field.title"), format: p.escape},
					{name: t("cowork.work.field.status"), format: value => p.escape(status_text(value))},
					{name: t("cowork.work.archived.at"), format: value => p.escape(p.datetime_text(value))},
					{name: "", sortable: false, format: value => `<button type="button" class="btn btn-default btn-xs"
						data-restore="${p.escape(value)}">${p.escape(t("cowork.work.archived.restore"))}</button>
						${state.admin ? `<button type="button" class="btn btn-danger btn-xs" data-delete="${p.escape(value)}">
						${p.escape(__("Delete"))}</button>` : ""}`},
				], rows.map(row => [row.title, row.status, row.archived_at, row.name]));
			};
			host.on("click", "[data-restore]", async event => {
				await p.call(`${API}restore`, {name: $(event.currentTarget).attr("data-restore")}, "POST");
				p.success(t("cowork.work.archived.restored"));
				await Promise.all([draw(), reload()]);
			});
			// 彻底删除不可恢复，按原生确认框走一次。
			host.on("click", "[data-delete]", event => {
				const target = $(event.currentTarget).attr("data-delete");
				frappe.confirm(t("cowork.work.archived.confirmDelete"), async () => {
					await p.call(`${API}delete`, {name: target}, "POST");
					p.success(t("cowork.work.archived.deleted"), "delete");
					await draw();
				});
			});
			d.on_page_show = () => void draw();
			d.onhide = () => table?.destroy?.();
			d.show();
		};

		// ── 多人同时在看：别人改了就刷新（原生 socket.io；连不上时手动刷新照常可用） ──
		const on_change = frappe.utils.debounce(() => {
			if (frappe.get_route()[0] === ROOT) void reload();
		}, 500);
		frappe.realtime.off(EVENT);
		frappe.realtime.on(EVENT, on_change);
		// 卡片链接 `/desk/pearnly-work/<卡片>`（复制链接、提醒点进来）：打开那张卡。
		const open_from_route = () => {
			const [root, name] = frappe.get_route();
			if (root === ROOT && name && !$(".pearnly-work-dialog.show").length) void open_card(name);
		};
		const native_show = frappe.pages[ROOT].on_page_show;
		frappe.pages[ROOT].on_page_show = () => {
			native_show?.();
			if (state.loaded_at && Date.now() - state.loaded_at > 2000) void reload();
			open_from_route();
		};

		await reload();
		open_from_route();
	};
})();
