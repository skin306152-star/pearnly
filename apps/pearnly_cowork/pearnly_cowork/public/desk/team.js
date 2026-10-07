/* 团队管理（Cowork）：本租户的用户账号与功能权限（`/desk/pearnly-team`）。
 *
 * 照 ERP 团队管理（`pearnly_erp_portal/public/desk/team.js`）：账号就是框架的 `User`，
 * 权限就是 `Role`；添加用户 = 用户名 + 邮箱 + 初始密码 + 勾功能；编辑只改功能，另有
 * 「重置密码」「停用 / 启用」。Cowork 没有多公司分配，所以没有公司那一栏。
 *
 * 能勾的功能来自服务端 `team.service.role_catalog()`（本租户买了、站点上真有的角色），
 * 页面不自己维护清单。服务端判据在 `team.service`（只有 Cowork Admin 能进）。
 * 页面文件在 core（`pearnly-team`），渲染由产品提供 `frappe.pearnly.team`。
 */
(() => {
	const p = frappe.pearnly;
	p.team = async (page, _core_t, wrapper) => {
		const api = "pearnly_cowork.team.api.";
		const t = await p.texts("cowork.team.");
		const lt = await p.texts("cowork.line.");
		const field = (fieldname, label, fieldtype = "Data", options = {}) =>
			({ fieldname, label, fieldtype, ...options });
		const root = "pearnly-team";
		let data = { members: [], roles: [] }, list, term = "", carry = null;

		const role_label = name => (data.roles.find(role => role.name === name) || {}).label || name;
		const rows_now = () => data.members.filter(row => !term
			|| String(row.username || "").toLowerCase().includes(term)
			|| String(row.email || "").toLowerCase().includes(term));

		// ── 名单 ────────────────────────────────────────────────────────────────
		function columns() {
			return [
				{ fieldname: "display", width: 150, min_width: 110, label: __("Username") },
				{ fieldname: "email", width: 210, min_width: 150, label: t("cowork.team.email") },
				{ fieldname: "roles", width: 240, min_width: 140, label: t("cowork.team.roles"),
					format: value => (value || []).map(role_label).join(" · ") || "—" },
				{ fieldname: "line_name", width: 100, min_width: 80, label: "LINE",
					format: (value, row) => row.line
						? (value ? lt("cowork.line.status.bound") : lt("cowork.line.status.unbound")) : "—" },
				{ fieldname: "status", width: 90, min_width: 72, label: t("cowork.team.status"),
					format: (_value, row) => row.enabled ? t("cowork.team.active") : t("cowork.team.inactive"),
					cell_class: (_value, row) => row.enabled ? "" : "pearnly-payment-due" },
				{ fieldname: "last_login", width: 140, min_width: 108, label: t("cowork.team.lastLogin"),
					format: value => value ? p.datetime_text(value) : "—" },
			];
		}

		const selection = p.selection_action({
			page, list: () => list, empty: restore_new,
			label: rows => (rows.every(row => !row.enabled)
				? t("cowork.team.action.bulkEnable", rows.length)
				: t("cowork.team.action.bulkDisable", rows.length)),
			run: async rows => {
				const enabling = rows.every(row => !row.enabled);
				for (const row of rows.filter(row => !row.locked)) {
					await p.call(api + "api_set_member_active",
						{ email: row.email, enabled: enabling ? 1 : 0 }, "POST");
				}
			},
			success: rows => t("cowork.team.notice.toggled", rows.length),
		});

		function restore_new() {
			page.set_primary_action(t("cowork.team.new"), () => editor(null));
		}

		async function show_list() {
			page.set_title(__("nav.team"));
			p.breadcrumbs({ label: __("nav.team"), route: p.subroute(root) });
			const search = page.add_field({ fieldname: "q", label: __("Search"), fieldtype: "Data",
				default: term, change: () => { term = String(search.get_value() || "").toLowerCase(); void list?.reload(); } });
			list = p.list({ page, doctype: "User", compact_columns: true, on_selection: selection,
				on_open: row => editor(row), columns: columns(),
				sort_fields: ["display", "email", "status"],
				load: async ({ sort_by, sort_order }) => {
					const items = p.sort_rows(rows_now(), { sort_by, sort_order,
						fields: ["display", "email", "status"], keys: { status: "enabled" } });
					return { items, total: items.length };
				},
				empty_action: { label: t("cowork.team.new"), action: () => editor(null) },
			});
			restore_new();
			await list.ready;
		}

		async function refresh_members() {
			data = await p.call(api + "api_list_members");
			data.members = data.members || [];
			data.roles = data.roles || [];
		}

		// ── 添加用户 / 改功能权限 ────────────────────────────────────────────────
		// 功能按服务端给的分组画成原生 MultiCheck，组名那一格是全选 / 全不选（同 ERP）。
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
				return [{ fieldtype: index % 2 ? "Column Break" : "Section Break",
					...(index === 0 ? { label: t("cowork.team.roles") } : {}) },
				field("group_" + index, group, "Check", {
					default: rows.every(row => default_roles.includes(row.name)) ? 1 : 0,
					onchange() {
						if (this._syncing || !get_dialog()) return;
						get_dialog().fields_dict["roles_" + index].select_all(!this.get_value());
					} }),
				{ fieldname: "roles_" + index, fieldtype: "MultiCheck", columns: 1, sort_options: false,
					options: rows.map(row => ({ label: row.label, value: row.name, checked: default_roles.includes(row.name) })),
					on_change: update_parent }];
			});
		}

		function editor(row, full_page = false, initial = {}) {
			const is_new = !row;
			// 管理员本人：全部功能都有，只看不改（全勾、灰色）。
			const locked = !is_new && row.locked;
			const shown_roles = locked ? data.roles.map(role => role.name) : row?.roles || [];
			let dialog;
			const fields = [
				field("username", __("Username"), "Data",
					{ reqd: 1, read_only: is_new ? 0 : 1, default: row?.username || "" }),
				field("email", t("cowork.team.email"), "Data",
					{ options: "Email", reqd: 1, read_only: is_new ? 0 : 1, default: row?.email || "" }),
				...(is_new ? [
					{ fieldtype: "Column Break" },
					field("password", t("cowork.team.password"), "Password", { reqd: 1 }),
				] : []),
				...roles_fields(shown_roles, () => dialog),
			];
			for (const item of fields) {
				if (!Object.hasOwn(initial, item.fieldname)) continue;
				if (item.fieldtype === "MultiCheck") item.options.forEach(option => {
					option.checked = (initial[item.fieldname] || []).includes(option.value);
				});
				else item.default = initial[item.fieldname];
			}
			const title = is_new ? t("cowork.team.new") : row.email;
			const label = is_new ? t("cowork.team.new") : __("Save");
			const save = async values => {
				const roles = permission_groups().flatMap((_group, i) => values["roles_" + i] || []);
				if (is_new) {
					await p.call(api + "api_add_member", { username: values.username, email: values.email,
						password: values.password, roles }, "POST");
					p.success(t("cowork.team.notice.added", values.username), "click");
				} else {
					await p.call(api + "api_set_member_roles", { email: row.email, roles }, "POST");
					p.success(t("cowork.team.notice.saved"), "click");
				}
				if (full_page) return frappe.set_route(root);
				dialog.hide();
				await refresh_members();
				await list.reload();
				restore_new();
			};
			if (full_page) {
				page.set_title(title);
				p.breadcrumbs({ label: __("nav.team"), route: p.subroute(root), trail: [{ label: title }] });
				dialog = p.layout(page.main, fields);
				let busy = false;
				const button = locked ? null : page.set_primary_action(label, async () => {
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
					carry = { route: route.join("/"), values };
				});
			}

			permission_groups().forEach((group, index) => {
				const parent = dialog.fields_dict["group_" + index];
				parent.$wrapper.addClass("mb-0").find(".label-area").addClass("font-weight-bold");
				const children = dialog.fields_dict["roles_" + index];
				children.$wrapper.addClass("ml-3 mb-0");
				children.$checkbox_area.addClass("py-0");
				const count = children.get_value().length;
				parent.$input.prop("indeterminate", count > 0 && count < data.roles.filter(role => role.group === group).length);
			});
			if (locked) {
				const $root = dialog.$wrapper || $(dialog.wrapper);
				$root.find("input[type=checkbox]").prop("disabled", true);
				$root.find(".select-all, .deselect-all").addClass("hide");
				if (!full_page) dialog.get_primary_btn().addClass("hide");
				return;
			}
			if (is_new || full_page) return;
			dialog.add_custom_action(t("cowork.team.resetPassword"), () => password_dialog(row));
			dialog.add_custom_action(row.enabled ? t("cowork.team.disable") : t("cowork.team.enable"),
				async () => {
					await p.call(api + "api_set_member_active",
						{ email: row.email, enabled: row.enabled ? 0 : 1 }, "POST");
					dialog.hide();
					p.success(row.enabled ? t("cowork.team.disable") : t("cowork.team.enable"), "click");
					await refresh_members();
					await list.reload();
					restore_new();
				});
		}

		function password_dialog(row) {
			p.dialog(t("cowork.team.resetPassword"), [
				field("password", t("cowork.team.password"), "Password", { reqd: 1 }),
			], t("cowork.team.resetPassword"), async (values, d) => {
				await p.call(api + "api_set_member_password",
					{ email: row.email, password: values.password }, "POST");
				d.hide();
				p.success(t("cowork.team.notice.password"), "click");
			});
		}

		const show = p.routed_page({ wrapper, root, render: async (rest, current) => {
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
		} });
		await show();
	};
})();
