(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const cacheText = await p.texts("dms.connect.cache.");
		let conn,
			busy = false;
		const datasets = ["masters", "customers", "geo"];
		let cacheForm, statuses = [], timer, pollBusy = false, visible = true, generation = 0;
		const cacheMethod = "pearnly_dms.connections.snapshots.";
		function stopPoll() {
			clearTimeout(timer);
			timer = null;
		}
		function schedulePoll() {
			stopPoll();
			if (visible && conn?.connected) timer = setTimeout(pollStatus, 3000);
		}
		function timestamp(value) {
			const epoch = Number(value);
			return epoch > 0 && Number.isFinite(epoch)
				? new Date(epoch * 1000).toLocaleString(frappe.boot.lang || undefined)
				: "—";
		}
		function paintStatus() {
			if (!cacheForm) return;
			for (const dataset of datasets) {
				const row = statuses.find((item) => item.dataset === dataset) || { state: "idle" };
				const progress = row.progress || {};
				const values = {
					state: cacheText("dms.connect.cache.state." + row.state),
					progress: progress.total == null
						? cacheText("dms.connect.cache.processed", progress.processed || 0)
						: `${progress.processed || 0} / ${progress.total}`,
					count: row.count == null ? "—" : String(row.count),
					as_of: row.stale && row.as_of
						? cacheText("dms.connect.cache.stale", timestamp(row.as_of)) : timestamp(row.as_of),
					completed_at: timestamp(row.completed_at),
					error_code: row.error_code || "—",
				};
				for (const [key, value] of Object.entries(values)) {
					cacheForm.set_value(`${dataset}_${key}`, value);
				}
			}
		}
		async function pollStatus() {
			if (!visible || !conn?.connected || pollBusy) return;
			const request = generation;
			pollBusy = true;
			try {
				const result = await p.call(cacheMethod + "get_status");
				if (visible && request === generation) {
					statuses = result.datasets || [];
					paintStatus();
				}
			} catch (error) {
				// Frappe reports RPC errors. Stop rather than repeatedly retrying a denied page.
				stopPoll();
				return;
			} finally {
				pollBusy = false;
			}
			schedulePoll();
		}
		function requestCache() {
			const firstRead = !statuses.some((item) => item.as_of > 0);
			p.dialog(cacheText("dms.connect.cache.read"), [
				{ fieldtype: "Section Break", description: cacheText("dms.connect.cache.help") },
				...datasets.map((dataset) => ({ fieldtype: "Check", fieldname: dataset,
					label: cacheText("dms.connect.cache.dataset." + dataset), default: firstRead ? 1 : 0 })),
			], cacheText("dms.connect.cache.read"), async (values, dialog) => {
				const selected = datasets.filter((dataset) => values[dataset]);
				if (!selected.length) {
					frappe.msgprint(p.escape(cacheText("dms.connect.cache.select")));
					return;
				}
				const result = await p.call(cacheMethod + "request_refresh", { datasets: selected }, "POST");
				// Only wait for queue acceptance, never for the external reading job.
				for (const row of result.datasets || []) {
					statuses = statuses.filter((old) => old.dataset !== row.dataset).concat(row);
				}
				dialog.hide();
				paintStatus();
				schedulePoll();
			});
		}
		if (wrapper) {
			$(wrapper).on("show", () => {
				visible = true;
				void pollStatus();
			});
			$(wrapper).on("hide", () => { visible = false; generation += 1; stopPoll(); });
		}
		async function refresh() {
			generation += 1;
			stopPoll();
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
				if (conn.connected) page.add_inner_button(cacheText("dms.connect.cache.read"), requestCache);
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
			cacheForm = null;
			statuses = [];
			if (ep) {
				const cacheFields = [{ fieldtype: "Section Break", label: cacheText("dms.connect.cache.title"),
					description: cacheText("dms.connect.cache.help") }];
				for (const dataset of datasets) {
					cacheFields.push({ fieldtype: "Section Break", label: cacheText("dms.connect.cache.dataset." + dataset) });
					for (const key of ["state", "progress", "count", "as_of", "completed_at", "error_code"]) {
						if (key === "as_of") cacheFields.push({ fieldtype: "Column Break" });
						cacheFields.push({ fieldtype: "Data", fieldname: `${dataset}_${key}`,
							label: cacheText("dms.connect.cache.field." + key), read_only: 1, default: "—" });
					}
				}
				cacheForm = p.layout(page.main, cacheFields);
				await pollStatus();
			}
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
