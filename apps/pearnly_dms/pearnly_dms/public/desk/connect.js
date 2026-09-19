(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t) => {
		let conn,
			busy = false;
		async function refresh() {
			conn = await p.call("pearnly_dms.connections.api.get_connection");
			const ep = conn.endpoint;
			page.main.empty();
			page.clear_inner_toolbar();
			page.set_primary_action(
				t(ep ? "connect.action.edit" : "connect.action.connect"),
				wizard,
			);
			page.set_indicator(
				t(conn.connected ? "connect.state.connected" : "connect.state.notConnected"),
				conn.connected ? "green" : "orange",
			);
			if (ep) {
				page.add_inner_button(t("connect.action.test"), () =>
					run(async () => {
						const result = await p.call(
							"pearnly_dms.connections.api.test_endpoint",
							{ endpoint: ep.name },
							"POST",
						);
						feedback(result);
						await refresh();
					}),
				);
				page.add_inner_button(
					t(ep.enabled ? "connect.action.disable" : "connect.action.enable"),
					() => {
						frappe.confirm(
							p.escape(
								t(ep.enabled ? "connect.action.disable" : "connect.action.enable"),
							),
							() =>
								run(async () => {
									await p.call(
										"pearnly_dms.connections.api.set_connection_enabled",
										{ enabled: ep.enabled ? 0 : 1 },
										"POST",
									);
									await refresh();
								}),
						);
					},
				);
			}
			const fields = [{ fieldtype: "Section Break", label: t("connect.title") }];
			for (const [name, key, value] of [
				[
					"state",
					"status",
					t(conn.connected ? "connect.state.connected" : "connect.state.notConnected"),
				],
				["account", "account", ep?.username || "—"],
				["url", "url", ep?.system_url || "—"],
				["admin", "adminCreds", t(ep?.has_admin_creds ? "connect.yes" : "connect.no")],

			]) {
				fields.push({
					fieldtype: "Data",
					fieldname: name,
					label: t("connect.field." + key),
					default: value,
					read_only: 1,
				});
			}
			p.layout(page.main, fields);
		}
		function feedback(result) {
			if (!result.ok) {
				frappe.msgprint({
					message: p.escape(result.error_friendly || t("connect.error.failed")),
					indicator: "red",
				});
				return false;
			}
			const m = result.masters || {};
			p.success(
				t(
					"connect.notice.ok",
					[
						t("connect.master.carModels", m.car_models?.length || 0),
						t("connect.master.branches", m.branches?.length || 0),
						t("connect.master.advisors", m.advisors?.length || 0),
						t("connect.master.terms", m.term_sales?.length || 0),
					].join(" · "),
				),
			);
			return true;
		}
		async function run(fn) {
			if (busy) return;
			busy = true;
			frappe.dom.freeze(t("connect.processing"));
			try {
				await fn();
			} finally {
				busy = false;
				frappe.dom.unfreeze();
			}
		}
		function wizard() {
			p.dialog(
				t("connect.title"),
				[
					{
						fieldtype: "Data",
						fieldname: "username",
						label: t("connect.field.account"),
						default: conn.endpoint?.username || "",
						reqd: 1,
					},
					{
						fieldtype: "Password",
						fieldname: "password",
						label: __("Password"),
						reqd: 1,
					},
				],
				t("connect.action.connect"),
				async (values, d) => {
					const probe = await p.call(
						"pearnly_dms.connections.api.test_credentials",
						values,
						"POST",
					);
					if (!probe.ok) {
						feedback(probe);
						return;
					}
					await p.call("pearnly_dms.connections.api.save_connection", values, "POST");
					d.set_value("password", "");
					d.hide();
					feedback(probe);
					await refresh();
				},
			);
		}
		await refresh();
	};
})();
