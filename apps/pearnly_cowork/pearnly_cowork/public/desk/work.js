/* 工作协调：团队看板（用户 2026-09-27，参照 Trello）。
 *
 * 一个团队一块板，三列固定（待办 / 进行中 / 已完成），拖到「已完成」就算完成，没有审核。
 * 版式借 Frappe 原生看板的样式（`.kanban*`）与原生 Sortable；卡片详情是原生 Dialog / FieldGroup，
 * 筛选放在页面原生筛选区；手机上三列纵排，顶上一条分区导航（照 ERP / 录入二级页）。
 * 状态、顺序、权限都在服务端（`pearnly_cowork.work`），页面只管交互。
 */
(() => {
	const p = frappe.pearnly;
	const API = "pearnly_cowork.work.api.";
	const STATUSES = ["todo", "doing", "done"];
	const DOTS = {todo: "gray", doing: "orange", done: "green"};
	const COLORS = ["green", "yellow", "orange", "red", "purple", "blue", "cyan", "pink", "gray"];
	const EVENT = "cowork_work_changed";
	const mobile = () => window.matchMedia("(max-width: 767px)").matches;
	const icon = name => frappe.utils.icon(name, "xs");

	p.coworkWork = async (page, t, wrapper) => {
		const state = {cards: [], members: [], labels: [], me: frappe.session.user, admin: false, loaded_at: 0};
		const member = user => state.members.find(row => row.user === user);
		const label_of = name => state.labels.find(row => row.name === name);
		const status_text = status => t(`cowork.work.status.${status}`);
		const color_text = color => t(`cowork.work.color.${color}`);

		const avatar = user => {
			const row = member(user);
			return frappe.avatar(null, "avatar-small", row?.name || user, row?.image || "");
		};
		const label_pill = label => `<span class="indicator-pill no-indicator-dot ${label.color} pearnly-work-label"
			title="${p.escape(label.title || color_text(label.color))}">${p.escape(label.title) || "&nbsp;"}</span>`;
		// 到期：已完成绿、过期红、今明两天橙，其余灰（原生指示色）。
		const due_color = card => {
			if (card.status === "done") return "green";
			const days = frappe.datetime.get_diff(card.due_date, frappe.datetime.get_today());
			return days < 0 ? "red" : (days <= 1 ? "orange" : "gray");
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
			if (due) {
				const days = frappe.datetime.get_diff(card.due_date, frappe.datetime.get_today());
				return due === "overdue" ? days < 0 : days >= 0 && days <= 1;
			}
			return true;
		};

		// ── 看板骨架 ─────────────────────────────────────────────────────────────
		p.add_refresh(page, () => reload());
		page.add_menu_item(t("cowork.work.labels.manage"), () => open_labels());
		page.add_menu_item(t("cowork.work.archived.title"), () => open_archived());

		const root = $('<div class="pearnly-work">').appendTo(page.main);
		const nav = $('<nav class="form-tabs-list pearnly-work-nav">').appendTo(root);
		const nav_list = $('<ul class="nav form-tabs">').appendTo(nav);
		const board = $('<div class="kanban pearnly-work-board">').appendTo(root);
		const columns = {};
		for (const status of STATUSES) {
			const column = $(`<div class="kanban-column pearnly-work-column" data-status="${status}">`).appendTo(board);
			const header = $('<div class="kanban-column-header">').appendTo(column);
			$(`<span class="kanban-column-title">
				<span class="kanban-col-dot indicator ${DOTS[status]}"></span>
				<span class="kanban-title ellipsis"></span></span>`).appendTo(header)
				.find(".kanban-title").text(status_text(status));
			const count = $('<span class="pearnly-work-count text-muted">').appendTo(header);
			const list = $(`<div class="kanban-cards" data-status="${status}">`).appendTo(column);
			const add = $('<button type="button" class="add-card btn btn-link">')
				.append(icon("plus")).append($("<span>").text(t("cowork.work.card.add"))).appendTo(column);
			const compose = $('<div class="pearnly-work-compose hide">').appendTo(column);
			add.on("click", () => open_composer(status));
			const link = $('<button type="button" class="nav-link">').text(status_text(status))
				.on("click", () => column[0].scrollIntoView({behavior: "smooth", block: "start"}));
			$('<li class="nav-item">').append(link).appendTo(nav_list);
			columns[status] = {column, list, count, add, compose, link};
			new Sortable(list[0], {
				group: "pearnly-work-cards", animation: 150, draggable: ".kanban-card-wrapper",
				// 手机上长按才拖，免得滑动翻页时误拖。
				delay: 200, delayOnTouchOnly: true,
				onEnd: event => void dropped(event),
			});
		}
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
		const card_html = card => {
			const labels = card.labels.map(label_of).filter(Boolean).map(label_pill).join("");
			const badges = [];
			if (card.due_date) {
				badges.push(`<span class="indicator-pill no-indicator-dot ${due_color(card)} pearnly-work-badge"
					title="${p.escape(t("cowork.work.field.due"))}">${icon("calendar")}
					${p.escape(frappe.datetime.str_to_user(card.due_date))}</span>`);
			}
			if (card.has_description) {
				badges.push(`<span class="pearnly-work-badge text-muted" title="${p.escape(t("cowork.work.field.description"))}">
					${icon("align-left")}</span>`);
			}
			if (card.checklist_total) {
				const complete = card.checklist_done === card.checklist_total;
				badges.push(`<span class="pearnly-work-badge ${complete ? "text-success" : "text-muted"}"
					title="${p.escape(t("cowork.work.field.checklist"))}">${icon("square-check")}
					${card.checklist_done}/${card.checklist_total}</span>`);
			}
			if (card.comments) {
				badges.push(`<span class="pearnly-work-badge text-muted" title="${p.escape(t("cowork.work.field.comments"))}">
					${icon("message-square")} ${card.comments}</span>`);
			}
			if (card.attachments) {
				badges.push(`<span class="pearnly-work-badge text-muted" title="${p.escape(t("cowork.work.field.attachments"))}">
					${icon("paperclip")} ${card.attachments}</span>`);
			}
			const people = card.assignees.map(avatar).join("");
			const meta = badges.length || people ? `<div class="pearnly-work-meta">
				<div class="pearnly-work-badges">${badges.join("")}</div>
				<div class="avatar-group overlap right">${people}</div></div>` : "";
			return `<div class="kanban-card-wrapper" data-name="${p.escape(card.name)}">
				<div class="kanban-card content"><div class="kanban-card-body">
					${labels ? `<div class="pearnly-work-labels">${labels}</div>` : ""}
					<div class="kanban-card-title">${p.escape(card.title)}</div>
					${meta}
				</div></div></div>`;
		};
		const render = () => {
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
				columns[status].list.children(".kanban-card-wrapper").each((_, node) => {
					const card = state.cards.find(row => row.name === node.dataset.name);
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
						me: data.me, admin: data.admin, loaded_at: Date.now()});
					refresh_filter_options();
					render();
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
			const before = item.prevAll(".kanban-card-wrapper").first().attr("data-name") || null;
			const after = item.nextAll(".kanban-card-wrapper").first().attr("data-name") || null;
			try {
				upsert(await p.call(`${API}move`, {name, status, before, after}, "POST"));
				apply();
			} catch (error) {
				await reload();
			}
		};
		board.on("click", ".kanban-card-wrapper", event => {
			const name = $(event.currentTarget).attr("data-name");
			if (name) void open_card(name);
		});

		// ── 列尾快速新建（回车连续加，Esc 收起） ───────────────────────────────
		const close_composers = () => {
			for (const status of STATUSES) {
				columns[status].compose.addClass("hide").empty();
				columns[status].add.removeClass("hide");
			}
		};
		const open_composer = status => {
			close_composers();
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
			const who = p.escape(member(row.actor)?.name || row.actor || "");
			const detail = row.detail || {};
			const args = {
				move: () => [status_text(detail.from), status_text(detail.to)],
				assign: () => [(detail.users || []).map(user => member(user)?.name || user).join(", ")],
				due: () => [detail.due ? frappe.datetime.str_to_user(detail.due) : t("cowork.work.activity.noDue")],
				attach: () => [detail.file || ""],
				detach: () => [detail.file || ""],
			}[row.action]?.() || [];
			return t(`cowork.work.activity.${row.action}`, ...args.map(p.escape)).replace("{who}", `<b>${who}</b>`);
		};
		const activity_html = rows => rows.map(row => {
			const when = `<span class="text-muted small">${comment_when(row.creation)}</span>`;
			if (row.action === "comment") {
				const remove = row.actor === state.me ? `<button type="button" class="btn btn-link btn-xs text-muted"
					data-comment="${p.escape(row.name)}">${p.escape(__("Delete"))}</button>` : "";
				return `<div class="pearnly-work-activity">${avatar(row.actor)}<div class="pearnly-work-activity-body">
					<div><b>${p.escape(member(row.actor)?.name || row.actor)}</b> ${when} ${remove}</div>
					<div class="pearnly-work-comment">${p.escape(row.content).replace(/\n/g, "<br>")}</div></div></div>`;
			}
			return `<div class="pearnly-work-activity">${avatar(row.actor)}<div class="pearnly-work-activity-body">
				<div>${activity_line(row)}</div><div>${when}</div></div></div>`;
		}).join("");
		const files_html = rows => rows.map(row => `<div class="pearnly-work-file">
			${icon("paperclip")}
			<a href="${p.escape(row.file_url)}" target="_blank" rel="noopener">${p.escape(row.file_name)}</a>
			<span class="text-muted small">${comment_when(row.creation)}</span>
			<button type="button" class="btn btn-link btn-xs text-muted" data-file="${p.escape(row.name)}">
				${p.escape(__("Delete"))}</button></div>`).join("");

		const open_card = async name => {
			const data = await p.call(`${API}card`, {name});
			let modified = data.modified;
			const people = [...state.members];
			for (const user of data.assignees) {
				if (!member(user)) people.push({user, name: user});
			}
			const checklist_label = rows => {
				const done = rows.filter(row => row.done).length;
				return rows.length ? `${t("cowork.work.field.checklist")} ${done}/${rows.length}` : t("cowork.work.field.checklist");
			};
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.card.title"),
				size: "large",
				fields: [
					{fieldname: "title", fieldtype: "Data", label: t("cowork.work.field.title"), reqd: 1,
						default: data.title, length: 200},
					{fieldtype: "Column Break"},
					{fieldname: "status", fieldtype: "Select", label: t("cowork.work.field.status"), reqd: 1,
						options: STATUSES.map(status => ({value: status, label: status_text(status)})), default: data.status},
					{fieldname: "due_date", fieldtype: "Date", label: t("cowork.work.field.due"), default: data.due_date},
					{fieldtype: "Section Break"},
					{fieldname: "assignees", fieldtype: "MultiCheck", label: t("cowork.work.field.members"),
						columns: "12rem", sort_options: false,
						options: people.map(row => ({label: p.escape(row.name), value: row.user,
							checked: data.assignees.includes(row.user)}))},
					{fieldtype: "Column Break"},
					{fieldname: "labels", fieldtype: "MultiCheck", label: t("cowork.work.field.labels"),
						columns: "10rem", sort_options: false,
						options: state.labels.map(row => ({label: label_pill(row), value: row.name,
							checked: data.labels.includes(row.name)}))},
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
			const draw_files = rows => d.fields_dict.files.$wrapper.html(files_html(rows));
			const draw_activity = rows => d.fields_dict.activity.$wrapper.html(activity_html(rows));
			draw_files(data.attachments);
			draw_activity(data.activity);

			// 「保存」只在有改动时出现（与录入复核页同一口径）。评论框不算改动。
			const current = () => {
				const values = d.get_values(true) || {};
				return {
					title: (values.title || "").trim(), status: values.status || "", due_date: values.due_date || "",
					assignees: [...(d.get_value("assignees") || [])].sort(),
					labels: [...(d.get_value("labels") || [])].sort(),
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
			};
			const watch = frappe.utils.debounce(refresh_dirty, 120);
			d.$body.on("input change click keyup", watch);
			d.get_primary_btn().addClass("hide");
			if (!data.archived) {
				d.add_custom_action(t("cowork.work.card.archive"), async () => {
					await p.call(`${API}archive`, {name}, "POST");
					state.cards = state.cards.filter(row => row.name !== name);
					render();
					d.hide();
					p.success(t("cowork.work.card.archived"));
				}, "btn-default");
			}
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
					const values = Object.fromEntries(keys.map(key => [key, now[key]]));
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
				draw_activity(await p.call(`${API}comment`, {name, content}, "POST"));
				d.set_value("comment", "");
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
				void reload();
			});
			d.fields_dict.activity.$wrapper.on("click", "[data-comment]", async event => {
				draw_activity(await p.call(`${API}delete_comment`, {activity: $(event.currentTarget).attr("data-comment")}, "POST"));
				void reload();
			});
		};

		// ── 标签（全板共用） ───────────────────────────────────────────────────
		const open_labels = () => {
			const d = new frappe.ui.Dialog({
				title: t("cowork.work.labels.manage"),
				fields: [{fieldname: "rows", fieldtype: "Table", in_place_edit: true, cannot_add_rows: false,
					data: state.labels.map(row => ({label_id: row.name, title: row.title, color: row.color})),
					fields: [
						{fieldname: "title", fieldtype: "Data", label: t("cowork.work.labels.name"), in_list_view: 1, columns: 6},
						{fieldname: "color", fieldtype: "Select", label: t("cowork.work.labels.color"), in_list_view: 1,
							columns: 4, default: "blue", options: COLORS.map(color => ({value: color, label: color_text(color)}))},
					]}],
				primary_action_label: __("Save"),
				primary_action: async () => {
					const rows = (d.get_value("rows") || []).map(row => ({
						name: row.label_id || null, title: (row.title || "").trim(), color: row.color || "blue"}));
					d.disable_primary_action();
					try {
						state.labels = await p.call(`${API}save_labels`, {rows: JSON.stringify(rows)}, "POST");
						d.hide();
						p.success(t("cowork.work.labels.saved"));
						await reload();
					} finally {
						d.enable_primary_action();
					}
				},
			});
			d.$wrapper.addClass("pearnly-dialog pearnly-work-labels-dialog");
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
			if (frappe.get_route()[0] === "pearnly-work") void reload();
		}, 500);
		frappe.realtime.off(EVENT);
		frappe.realtime.on(EVENT, on_change);
		const native_show = frappe.pages["pearnly-work"].on_page_show;
		frappe.pages["pearnly-work"].on_page_show = () => {
			native_show?.();
			if (state.loaded_at && Date.now() - state.loaded_at > 2000) void reload();
		};

		await reload();
	};
})();
