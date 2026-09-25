/* 团队管理：本租户的用户账号与岗位（`/desk/pearnly-team`）。
 *
 * 账号与权限**都是框架原生的**：一个用户就是一条 `User`，权限就是 `Role` —— 我们不复刻
 * 一套用户表或权限表，也不另存一份"谁有什么权限"（那必然和框架的判据走偏）。
 *
 * 界面上只做三件事（用户 2026-09-23 口径："不做复杂的配置，只添加员工，基础信息、
 * 邮箱、初始用户和密码、勾选功能权限，做完闭环"）：
 *
 *   · 添加用户：姓名 + 邮箱（就是登录名）+ 初始密码 + 勾岗位 —— 这一步就是"发号"；
 *   · 改用户：**只有岗位能改**（用户名与邮箱开户时定死），另给「重置密码」与
 *     「停用 / 启用」两个动作（用户 2026-09-23："用户名和邮箱是绑死这个租户的，
 *     唯一关系，用户只可以自己改密码，员工也是一样"）；
 *   · 名单：姓名 / 邮箱 / 岗位 / 状态 / 最后登录，行内没有别的开关。
 *
 * 页面上不叫"员工"（用户 2026-09-23："团队管理里面不要出现员工，写添加用户"）。
 *
 * 能勾的岗位**不是我们写的清单**，是服务端 `team.role_catalog()` 给的 —— 那是
 * "本站点上真实存在的功能角色"，也就是这个租户买到的功能；没买的功能没有角色，
 * 勾选框里就不会出现。勾了之后这个用户看得到哪些菜单，由 `pearnly_navigation` 的
 * 声明 + 框架的角色判据决定，页面不自己算。
 *
 * 服务端判据在 `team.service`（谁能进：`team.manage`，也就是 ERP Admin）。
 * 页面上的显隐只是别让人白点；越权请求照样被拒。
 */
(() => {
	const p = frappe.pearnly;
	p.team = async (page, t, wrapper) => {
		const api = "pearnly_erp_portal.team.api.";
		const field = (fieldname, label, fieldtype = "Data", options = {}) =>
			({ fieldname, label: __(label), fieldtype, ...options });
		const root = "pearnly-team";
		let data = { members: [], roles: [] }, list, term = "", carry = null;
		const lt = await p.texts("erp.line.");

		const role_label = name => (data.roles.find(role => role.name === name) || {}).label || name;
		const rows_now = () => data.members.filter(row => !term
			|| String(row.username || "").toLowerCase().includes(term)
			|| String(row.email || "").toLowerCase().includes(term));

		// ── 名单 ────────────────────────────────────────────────────────────────
		function columns() {
			return [
				// 显示的是**用户名**（不再有"姓名"这一栏）—— 用户 2026-09-24："全局可以涉及到
				// 姓名的地方，全部改为用户名"。没有用户名的老账号回落显示邮箱。
				{ fieldname: "display", width: 150, min_width: 110, label: __("Username") },
				{ fieldname: "email", width: 210, min_width: 150, label: t("team.email") },
				{ fieldname: "roles", width: 200, min_width: 130, label: t("team.roles"),
					format: value => (value || []).map(role_label).join(" · ") || "—" },
				// 一租户多公司：这个账号被指到哪几家（没指 = 不限制，看全部）。
				{ fieldname: "companies", width: 170, min_width: 120, label: t("team.companies"),
					format: value => (value || []).map(company_label).join(" · ") || t("team.allCompanies") },
				// LINE：开了才显示绑定状态（租户管理员本人默认开）。
				{ fieldname: "line_name", width: 100, min_width: 80, label: "LINE",
					format: (value, row) => row.line
						? (value ? lt("erp.line.status.bound") : lt("erp.line.status.unbound")) : "—" },
				{ fieldname: "status", width: 90, min_width: 72, label: t("team.status"),
					format: (_value, row) => row.enabled ? t("team.active") : t("team.inactive"),
					// 停用的账号标红：与其它页同一个红（`.pearnly-payment-due`）。
					cell_class: (_value, row) => row.enabled ? "" : "pearnly-payment-due" },
				{ fieldname: "last_login", width: 140, min_width: 108, label: t("team.lastLogin"),
					format: value => value ? p.datetime_text(value) : "—" },
			];
		}

		// 选中若干位员工 → 批量停用/启用（与商品、供应商、客户同一套 `p.selection_action`）。
		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			label: rows => (rows.every(row => !row.enabled)
				? t("team.action.bulkEnable", rows.length)
				: t("team.action.bulkDisable", rows.length)),
			run: async rows => {
				const enabling = rows.every(row => !row.enabled);
				for (const row of rows) {
					await p.call(api + "api_set_member_active",
						{ email: row.email, enabled: enabling ? 1 : 0 }, "POST");
				}
			},
			success: rows => t("team.notice.toggled", rows.length),
		});

		function restore_new() {
			page.set_primary_action(t("team.new"), () => editor(null));
		}

		async function show_list() {
			page.set_title(__("nav.team"));
			p.breadcrumbs({ label: __("nav.team"), route: p.subroute(root) });
			const search = page.add_field({ fieldname: "q", label: __("Search"), fieldtype: "Data",
				default: term, change: () => { term = String(search.get_value() || "").toLowerCase(); void list?.reload(); } });
			list = p.list({ page, doctype: "User", compact_columns: true, on_selection: selection,
				on_open: row => editor(row), columns: columns(),
				// 一个租户的员工就这么多人，整份名单一次给完（搜索在本地过滤，不再打接口）。
				// 列头点一下排序：名单在手上，直接排（同 core `p.sort_rows`）。
				sort_fields: ["display", "email", "status"],
				load: async ({ sort_by, sort_order }) => {
					const items = p.sort_rows(rows_now(), { sort_by, sort_order,
						fields: ["display", "email", "status"],
						// 状态那列显示的是"启用/停用"，值在 `enabled` 上（`status` 只是列名）。
						keys: { status: "enabled" } });
					return { items, total: items.length };
				},
				empty_action: { label: t("team.new"), action: () => editor(null) },
			});
			restore_new();
			p.add_refresh(page, () => list.reload());
			await list.ready;
		}

		async function refresh_members() {
			data = await p.call(api + "api_list_members");
			data.members = data.members || [];
			data.roles = data.roles || [];
			// 公司清单给编辑弹窗里的多选（含停用的：老单据还挂在上面）。
			const rows = await p.call("pearnly_erp_portal.masters.api.api_list_companies",
				{limit: 0});
			data.companies = rows.map(row => ({label: row.company_name, value: row.name}));
		}

		// **函数声明而不是 const**：列表那一列在构造时就会调它（`format` 在 `p.list()` 里立刻跑），
		// 写成 const 会撞上"暂时性死区"——整页直接 `Cannot access 'company_label' before
		// initialization`，页面只剩 "Unable to load this page."（2026-09-23 实测踩到）。
		function company_label(name) {
			return (data.companies || []).find(row => row.value === name)?.label || name;
		}

		// ── 添加用户 / 改岗位 ───────────────────────────────────────────────────
		// 姓名（就是登录用的用户名）与邮箱是**添加时填一次**的：之后谁都改不动
		// （用户 2026-09-23："可以用用户名登录，但是不能修改……用户只可以自己改密码"）。
		// 所以编辑这一屏里姓名/邮箱只是**看的**，能改的只有岗位，再加上「重置密码」
		// 「停用 / 启用」两个动作。**不在界面上写这类说明**（用户 2026-09-23：
		// "用户名和邮箱是开户时发的，之后不能修改。这类文案不要随意添加，删掉"）——
		// 字段只读本身就是那句话，成员也是租户自己加的。
		// 岗位用框架原生的 **MultiCheck**（多选勾选框，自带全选/清空），不是我们画的清单。
		const permission_groups = () => [...new Set(data.roles.map(role => role.group))];
		function roles_fields(default_roles = [], get_dialog) {
			return permission_groups().flatMap((group, index) => {
				const rows = data.roles.filter(role => role.group === group);
				const update_parent = () => {
					const fields = get_dialog()?.fields_dict;
					if (!fields) return;
					const parent = fields["group_" + index], count = fields["roles_" + index].get_value().length;
					parent._syncing = true;
					Promise.resolve(parent.set_value(count === rows.length ? 1 : 0)).finally(() => {
						parent.$input.prop("indeterminate", count > 0 && count < rows.length);
						parent._syncing = false;
					});
				};
				return [{fieldtype: index % 2 ? "Column Break" : "Section Break",
					...(index === 0 ? {label: t("team.roles")} : {})},
					field("group_" + index, group, "Check", {
					default: rows.every(row => default_roles.includes(row.name)) ? 1 : 0,
					onchange() {
						if (this._syncing || !get_dialog()) return;
						get_dialog().fields_dict["roles_" + index].select_all(!this.get_value());
					}}),
					{fieldname: "roles_" + index, fieldtype: "MultiCheck", columns: 1, sort_options: false,
						options: rows.map(row => ({label: row.label, value: row.name, checked: default_roles.includes(row.name)})),
						on_change: update_parent}];
			});
		}

		// 公司分配用同一颗原生 MultiCheck（多选勾选，自带全选/清空）；**一个都不勾 = 不限制**，
		// 也就是官方那套"没有 User Permission 就看全部"。
		function companies_field(default_companies = []) {
			return { fieldtype: "MultiCheck", fieldname: "companies", label: t("team.companies"),
				columns: 2, description: t("team.companiesHint"),
				options: (data.companies || []).map(row => ({ label: row.label, value: row.value,
					checked: default_companies.includes(row.value) })) };
		}

		function editor(row, full_page = false, initial = {}) {
			const is_new = !row;
			let dialog;
			const fields = [
				// 用户名：**英文和数字**（用户 2026-09-24 的口径），既是显示名也是登录名
				// （和邮箱一样能登）。长度不设限；服务端还会再按"只允许英文数字 + 不重名"挡一次。
				field("username", "Username", "Data",
					{ reqd: 1, read_only: is_new ? 0 : 1, default: row?.username || "" }),
				field("email", "team.email", "Data",
					{ options: "Email", reqd: 1, read_only: is_new ? 0 : 1, default: row?.email || "" }),
				...(is_new ? [
					// 密码不设任何规则（长度、字符都不限），所以这里没有说明句。
					{ fieldtype: "Column Break" },
					field("password", "team.password", "Password", { reqd: 1 }),
				] : []),
				...roles_fields(row?.roles || [], () => dialog),
				{fieldtype: "Section Break"},
				companies_field(row?.companies || []),
			];
			for (const item of fields) {
				if (!Object.hasOwn(initial, item.fieldname)) continue;
				if (item.fieldtype === "MultiCheck") item.options.forEach(option => {
					option.checked = (initial[item.fieldname] || []).includes(option.value);
				});
				else item.default = initial[item.fieldname];
			}
			const title = is_new ? t("team.new") : row.email;
			const label = is_new ? t("team.new") : __("Save");
			const save = async values => {
				const roles = permission_groups().flatMap((_group, i) => values["roles_" + i] || []);
				if (is_new) {
					await p.call(api + "api_add_member", {username: values.username, email: values.email,
						password: values.password, roles, companies: values.companies || []}, "POST");
					p.success(t("team.notice.added", values.username), "click");
				} else {
					await p.call(api + "api_set_member_roles", {email: row.email, roles,
						companies: values.companies || []}, "POST");
					p.success(t("team.notice.saved"), "click");
				}
				if (full_page) return frappe.set_route(root);
				dialog.hide();
				await refresh_members();
				await list.reload();
				restore_new();
			};
			if (full_page) {
				page.set_title(title);
				p.breadcrumbs({label: __("nav.team"), route: p.subroute(root), trail: [{label: title}]});
				dialog = p.layout(page.main, fields);
				let busy = false;
				const button = page.set_primary_action(label, async () => {
					const values = dialog.get_values();
					if (!values || busy) return;
					busy = true; button.prop("disabled", true);
					try { await save(values); }
					catch (error) { if (!error?._server_messages && !error?.responseJSON) console.error(error); }
					finally { busy = false; button.prop("disabled", false); }
				});
				page.set_secondary_action(__("Cancel"), () => frappe.set_route(root));
			} else {
				dialog = p.dialog(title, fields, label, save);
				dialog.$wrapper.find(".modal-dialog").addClass("modal-lg");
				const route = is_new ? [root, "new"] : [root, "edit", row.email];
				p.edit_all(dialog, __("Edit in Full Page"), route, values => {
					carry = {route: route.join("/"), values};
				});
			}

			permission_groups().forEach((group, index) => {
				const parent = dialog.fields_dict["group_" + index];
				parent.$wrapper.addClass("mb-0").find(".label-area").addClass("font-weight-bold");
				const children = dialog.fields_dict["roles_" + index];
				children.$wrapper.addClass("ml-3 mb-0");
				children.$checkbox_area.addClass("py-0");
				const count = dialog.fields_dict["roles_" + index].get_value().length;
				parent.$input.prop("indeterminate", count > 0 && count < data.roles.filter(role => role.group === group).length);
			});
			if (is_new || full_page) return;
			// 两个动作挂在原生弹窗的自定义动作槽里（与产品其它弹窗同一个位置）。
			dialog.add_custom_action(t("team.resetPassword"), () => password_dialog(row));
			dialog.add_custom_action(row.enabled ? t("team.disable") : t("team.enable"),
				async () => {
					await p.call(api + "api_set_member_active",
						{ email: row.email, enabled: row.enabled ? 0 : 1 }, "POST");
					dialog.hide();
					p.success(row.enabled ? t("team.disable") : t("team.enable"), "click");
					await refresh_members();
					await list.reload();
					restore_new();
				});
		}

		function password_dialog(row) {
			p.dialog(t("team.resetPassword"), [
				// 密码不设规则（长度、字符都不限），所以这一格没有说明句。
				field("password", "team.password", "Password", { reqd: 1 }),
			], t("team.resetPassword"), async (values, d) => {
				await p.call(api + "api_set_member_password",
					{ email: row.email, password: values.password }, "POST");
				d.hide();
				p.success(t("team.notice.password"), "click");
			});
		}
		const show = p.routed_page({wrapper, root, render: async (rest, current) => {
			p.clear_page(page);
			page.clear_secondary_action();
			list = null;
			await refresh_members();
			if (!current()) return;
			if (!rest.length) { carry = null; await show_list(); return; }
			const row = rest[0] === "edit" && rest.length === 2
				? data.members.find(member => member.email === rest[1]) : null;
			if (!(rest[0] === "new" && rest.length === 1) && !row) return p.redirect_up(root);
			const key = [root, ...rest].join("/");
			const initial = carry?.route === key ? carry.values : {};
			carry = null;
			editor(row, true, initial);
		}});
		await show();

	};
})();
