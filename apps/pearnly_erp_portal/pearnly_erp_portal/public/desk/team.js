/* 团队管理：员工账号与岗位（`/desk/pearnly-team`）。
 *
 * 账号与权限**都是框架原生的**：员工就是一条 `User`，权限就是 `Role` —— 我们不复刻
 * 一套用户表或权限表，也不另存一份"谁有什么权限"（那必然和框架的判据走偏）。
 *
 * 界面上只做三件事（用户 2026-09-23 口径："不做复杂的配置，只添加员工，基础信息、
 * 邮箱、初始用户和密码、勾选功能权限，做完闭环"）：
 *
 *   · 添加员工：姓名 + 邮箱（就是登录名）+ 初始密码 + 勾岗位；
 *   · 改员工：姓名 / 岗位，另给「重置密码」与「停用 / 启用」两个动作；
 *   · 名单：姓名 / 邮箱 / 岗位 / 状态 / 最后登录，行内没有别的开关。
 *
 * 能勾的岗位**不是我们写的清单**，是服务端 `team.role_catalog()` 给的 —— 那是
 * "本站点上真实存在的功能角色"，也就是这个租户买到的功能；没买的功能没有角色，
 * 勾选框里就不会出现。勾了之后员工看得到哪些菜单，由 `pearnly_navigation` 的
 * 声明 + 框架的角色判据决定，页面不自己算。
 *
 * 服务端判据在 `team.service`（谁能进：`team.manage`，也就是 ERP Admin）。
 * 页面上的显隐只是别让人白点；越权请求照样被拒。
 */
(() => {
	const p = frappe.pearnly;
	p.team = async (page, t) => {
		const api = "pearnly_erp_portal.team.api.";
		const field = (fieldname, label, fieldtype = "Data", options = {}) =>
			({ fieldname, label: __(label), fieldtype, ...options });
		let data = { members: [], roles: [] }, list, term = "";

		page.set_title(__("nav.team"));
		p.breadcrumbs({ label: __("nav.team"), route: p.subroute("pearnly-team") });
		const search = page.add_field({ fieldname: "q", label: __("Search"), fieldtype: "Data",
			change: () => { term = String(search.get_value() || "").toLowerCase(); void list?.reload(); } });

		const role_label = name => (data.roles.find(role => role.name === name) || {}).label || name;
		const rows_now = () => data.members.filter(row => !term
			|| String(row.full_name || "").toLowerCase().includes(term)
			|| String(row.email || "").toLowerCase().includes(term));

		// ── 名单 ────────────────────────────────────────────────────────────────
		function columns() {
			return [
				{ fieldname: "full_name", width: 150, min_width: 110, label: t("team.name") },
				{ fieldname: "email", width: 210, min_width: 150, label: t("team.email") },
				{ fieldname: "roles", width: 200, min_width: 130, label: t("team.roles"),
					format: value => (value || []).map(role_label).join(" · ") || "—" },
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

		// 先把名单取回来再建列表：`p.list` 构造时就会 load 一次，先建列表的话第一屏是空的。
		await refresh_members();
		list = p.list({ page, doctype: "User", on_selection: selection,
			on_open: row => editor(row), columns: columns(),
			// 一个租户的员工就这么多人，整份名单一次给完（搜索在本地过滤，不再打接口）。
			load: async () => {
				const items = rows_now();
				return { items, total: items.length };
			},
			empty_action: { label: t("team.new"), action: () => editor(null) },
		});
		restore_new();
		p.add_refresh(page, () => list.reload());
		await list.ready;

		async function refresh_members() {
			data = await p.call(api + "api_list_members");
			data.members = data.members || [];
			data.roles = data.roles || [];
		}

		// ── 添加 / 编辑员工 ─────────────────────────────────────────────────────
		// 岗位用框架原生的 **MultiCheck**（多选勾选框，自带全选/清空），不是我们画的清单。
		function roles_field(default_roles = []) {
			return { fieldtype: "MultiCheck", fieldname: "roles", label: t("team.roles"),
				columns: 2, reqd: 1, description: t("team.rolesHint"),
				options: data.roles.map(role => ({ label: role.label, value: role.name,
					checked: default_roles.includes(role.name) })) };
		}

		function editor(row) {
			const is_new = !row;
			let dialog;
			const fields = [
				field("full_name", "team.name", "Data", { reqd: 1, default: row?.full_name || "" }),
				field("email", "team.email", "Data",
					{ options: "Email", reqd: 1, read_only: is_new ? 0 : 1, default: row?.email || "" }),
				...(is_new ? [
					field("password", "team.password", "Password",
						{ reqd: 1, description: t("team.passwordHint") }),
				] : []),
				roles_field(row?.roles || []),
			];
			dialog = p.dialog(is_new ? t("team.new") : row.email,
				fields.filter(Boolean), is_new ? t("team.new") : __("Save"),
				async (values, d) => {
					if (is_new) {
						await p.call(api + "api_add_member", {
							full_name: values.full_name, email: values.email,
							password: values.password, roles: values.roles,
						}, "POST");
						d.hide();
						p.success(t("team.notice.added", values.full_name));
					} else {
						await p.call(api + "api_update_member", {
							email: row.email, full_name: values.full_name, roles: values.roles,
						}, "POST");
						d.hide();
						p.success(t("team.notice.saved"));
					}
					await refresh_members();
					await list.reload();
					restore_new();
				});
			if (is_new) return;
			// 两个动作挂在原生弹窗的自定义动作槽里（与产品其它弹窗同一个位置）。
			dialog.add_custom_action(t("team.resetPassword"), () => password_dialog(row));
			dialog.add_custom_action(row.enabled ? t("team.disable") : t("team.enable"),
				async () => {
					await p.call(api + "api_set_member_active",
						{ email: row.email, enabled: row.enabled ? 0 : 1 }, "POST");
					dialog.hide();
					p.success(row.enabled ? t("team.disable") : t("team.enable"));
					await refresh_members();
					await list.reload();
					restore_new();
				});
		}

		function password_dialog(row) {
			p.dialog(t("team.resetPassword"), [
				field("password", "team.password", "Password",
					{ reqd: 1, description: t("team.passwordHint") }),
			], t("team.resetPassword"), async (values, d) => {
				await p.call(api + "api_set_member_password",
					{ email: row.email, password: values.password }, "POST");
				d.hide();
				p.success(t("team.notice.password"));
			});
		}
	};
})();
