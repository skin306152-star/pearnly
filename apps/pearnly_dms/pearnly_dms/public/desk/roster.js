(() => {
	const p = frappe.pearnly;
	p.roster = async (page, t) => {
		const api = "pearnly_dms.operators.roster.";
		let list, deleting = false;
		const role = value => t(value === "admin" ? "roster.role.admin" : "roster.role.sales");
		const status = value => __(value === "active" ? "Active" : "Inactive");
		async function refresh() { await list.reload(); }
		page.set_primary_action(t("roster.add"), create);
		p.add_refresh(page, refresh, __("Reload List"));
		page.set_title_sub(p.escape(t("roster.subtitle")));
		list = p.list({page, doctype: "DMS Operator", selectable: true, compact_columns: true,
			on_open: editor,
			on_selection: rows => {
				page.set_primary_action(rows.length ? t("roster.delete") : t("roster.add"),
					rows.length ? () => removeRows(list.get_checked_items()) : create);
				page.btn_primary.prop("disabled", deleting);
			},
			load: args => p.call(api + "api_list_operators", args),
			row_actions: row => [
				{id: "line", label: t(row.line_bound ? "roster.unbindLine" : "roster.bindLine"),
					disabled: !row.line_bound && row.status !== "active", action: row.line_bound ? unbind : bind},
				{id: "edit", label: t("roster.edit"), action: editor},
				{id: "delete", label: t("roster.delete"), action: remove},
			],
			columns: [
				{fieldname: "display_name", width: 100, min_width: 80, label: t("roster.col.operator")},
				{fieldname: "dms_username", width: 110, min_width: 90, label: t("roster.field.dmsUsername")},
				{fieldname: "role", width: 60, min_width: 48, label: t("roster.field.role"), format: role},
				{fieldname: "status", width: 60, min_width: 48, label: __("Status"), format: status},
				{fieldname: "can_query_dms", width: 80, min_width: 72, label: t("roster.col.query"), format: value => t(value ? "roster.allowQuery" : "roster.noQuery")},
				{fieldname: "advisor_name", width: 150, min_width: 110, label: t("roster.field.advisor"), format: value => value || t("roster.advisor.auto")},
				{fieldname: "line_bound", width: 85, min_width: 70, label: t("roster.col.line"), format: (value, row) => value ? row.line_display_name || t("roster.bound") : t("roster.unbound")},
			],
		});
		function create() { editor(); }
		function editor(row) {
			const adding = !row;
			const fields = [
				// Renaming is a create-time decision; the old roster's edit modal never changed it.
				...(adding ? [{fieldtype: "Data", fieldname: "display_name", label: t("roster.field.displayName"), reqd: 1}] : []),
				// DMS 用户名也是**发号时定死**（用户 2026-09-23："可以用用户名登录，但是不能
				// 修改"）：新建时填一次，之后只给看不给改，编辑这一屏能改的只有密码。
				{fieldtype: "Data", fieldname: "dms_username", label: t("roster.field.dmsUsername"),
					reqd: adding ? 1 : 0, read_only: adding ? 0 : 1, default: row?.dms_username || ""},
				{fieldtype: "Password", fieldname: "dms_password", label: t("roster.field.dmsPassword"), reqd: adding ? 1 : 0,
					description: adding ? "" : t("roster.edit.passwordHint")},
				{fieldtype: "Select", fieldname: "dms_role", label: t("roster.field.role"), default: row?.role || "sales",
					options: ["sales", "admin"].map(value => ({value, label: role(value)}))},
				{fieldtype: "Check", fieldname: "can_query_dms", label: t("roster.field.canQuery"), default: row?.can_query_dms ? 1 : 0},
				{fieldtype: "Select", fieldname: "advisor_id", label: t("roster.field.advisor"), read_only: 1,
					default: row?.advisor_id || "", options: [{value: row?.advisor_id || "", label: row?.advisor_name || t("roster.advisor.auto")}],
					description: t("roster.advisor.loading")},
				{fieldtype: "Button", fieldname: "reload_advisors", label: t("roster.advisor.reload"), click: () => loadAdvisors()},
			];
			let loaded = false;
			const d = p.dialog(adding ? t("roster.add") : row.display_name, fields, adding ? t("roster.add") : __("Save"), async (values, dialog) => {
				if (!loaded) delete values.advisor_id; // Failed loads must never clear an existing assignment.
				await p.call(api + (adding ? "api_create_operator" : "api_update_operator"), adding ? values : {name: row.name, ...values}, "POST");
				dialog.set_value("dms_password", ""); dialog.hide();
				p.success(t(adding ? "roster.notice.created" : "roster.notice.saved"));
				await refresh();
			});
			async function loadAdvisors() {
				try {
					// Read failures come back as a code, not a server error toast: the
					// operator dialog must stay usable while the master list is unreadable.
					const {items, error} = await p.call(api + "api_advisors");
					const current = d.get_value("advisor_id");
					if (error) {
						d.set_df_property("advisor_id", "description", t(error));
						return;
					}
					const options = [{value: "", label: t("roster.advisor.auto")},
						...items.map(item => ({value: item.id, label: item.code ? `${item.name} · ${item.code}` : item.name}))];
					d.set_df_property("advisor_id", "options", options);
					d.set_df_property("advisor_id", "read_only", 0);
					d.set_df_property("advisor_id", "description", items.length ? "" : t("roster.advisor.empty"));
					d.set_value("advisor_id", current);
					loaded = true;
				} catch (error) {
					d.set_df_property("advisor_id", "description", t("roster.advisor.loadError"));
				}
			}
			void loadAdvisors();
		}
		function unbind(row) {
			frappe.confirm(p.escape(t("roster.confirm.unbind", row.display_name)), async () => {
				await p.call(api + "api_unbind_operator", {name: row.name}, "POST");
				await refresh();
			});
		}
		function remove(row) { removeRows([row]); }
		function removeRows(rows) {
			if (deleting || !rows.length) return;
			// Snapshot record IDs before confirmation; duplicate display names are valid.
			const selected = [...new Map(rows.map(row => [row.name, row])).values()];
			const question = selected.length === 1
				? t("roster.confirm.delete", selected[0].display_name)
				: t("roster.confirm.deleteSelected", selected.length);
			frappe.confirm(p.escape(question), async () => {
				if (deleting) return;
				deleting = true;
				page.btn_primary.prop("disabled", true);
				let removed = 0;
				try {
					for (const row of selected) {
						// Same audited cleanup as the row's Delete action, one at a time.
						await p.call(api + "api_delete_operator", {name: row.name}, "POST");
						removed++;
					}
				} catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally {
					if (removed) p.success(t("roster.notice.deletedCount", removed));
					// Reload reconciles selected IDs: failed/unprocessed rows stay selected.
					try { await refresh(); }
					finally { deleting = false; page.btn_primary.prop("disabled", false); }
				}
			});
		}
		async function bind(row) {
			const requestCode = () => p.call(api + "api_operator_bind_code", {name: row.name}, "POST");
			// Verify the assigned OA before opening the form. A failed lookup must not
			// leave an empty code dialog behind the framework's translated error.
			const initial = await requestCode();
			let timer, closed = false, generating = false;
			const d = new frappe.ui.Dialog({
				title: row.display_name,
				fields: [
					{fieldtype: "HTML", fieldname: "line_account"},
					{fieldtype: "Data", fieldname: "code", label: t("roster.code.title"), read_only: 1},
					{fieldtype: "HTML", options: `<p>${p.escape(t("roster.code.instruction"))}</p>`},
				],
				primary_action_label: t("roster.regenerate"),
				primary_action: generate,
			});
			d.onhide = () => { closed = true; clearInterval(timer); };
			function render(result) {
				if (closed) return;
				const account = result.line;
				const box = d.fields_dict.line_account.$wrapper.empty();
				const content = $("<div>").addClass("d-flex flex-wrap align-items-center mb-3").appendTo(box);
				$("<img>").addClass("mr-4 mb-3 flex-shrink-0").attr({
					src: account.qr, alt: account.basic_id, width: 160, height: 160,
				}).appendTo(content);
				const details = $("<div>").addClass("text-break mw-100").appendTo(content);
				$("<p>").addClass("font-weight-bold").text(account.name).appendTo(details);
				$("<p>").text(account.basic_id).appendTo(details);
				$("<a>").addClass("btn btn-default btn-sm").attr({
					href: account.url, target: "_blank", rel: "noopener noreferrer",
				}).text(t("roster.line.addFriend")).appendTo(details);
				d.set_value("code", result.code);
				clearInterval(timer);
				const expires = Date.now() + result.expires_in * 1000;
				const tick = () => {
					const seconds = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
					const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
					d.set_df_property("code", "description", seconds
						? t("roster.code.validFor", remaining) : t("roster.code.expiredHint"));
					if (!seconds) clearInterval(timer);
				};
				timer = setInterval(tick, 1000);
				tick();
			}
			async function generate() {
				if (generating) return;
				generating = true;
				d.disable_primary_action();
				try { render(await requestCode()); }
				catch (error) {
					if (!error?._server_messages && !error?.responseJSON) console.error(error);
				} finally {
					generating = false;
					if (!closed) d.enable_primary_action();
				}
			}
			d.show();
			render(initial);
		}
		await list.ready;
	};
})();
