(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const cacheText = await p.texts("dms.connect.cache.");
		let conn,
			busy = false;
		const datasets = ["vehicles", "customers", "geo", "company"];
		let statuses = [], timer, pollGeneration = null, visible = true, generation = 0;
		let pending = null, outcome = null, frozen = false, posting = false;
		const cacheMethod = "pearnly_dms.connections.snapshots.";
		const active = (row) => ["queued", "running"].includes(row.state);
		function stopPoll() {
			clearTimeout(timer);
			timer = null;
		}
		function schedulePoll() {
			stopPoll();
			if (visible && conn?.connected && pending?.size) timer = setTimeout(pollStatus, 3000);
		}
		function freezeRead() {
			if (frozen) return;
			frappe.dom.freeze(p.escape(cacheText("dms.connect.cache.processing")));
			frozen = true;
		}
		function finishRead(key, indicator = "orange") {
			stopPoll();
			pending = null;
			outcome = null;
			if (frozen) { frappe.dom.unfreeze(); frozen = false; }
			if (!key) return;
			if (key === "dms.connect.cache.state.succeeded") p.success(cacheText(key));
			else frappe.msgprint({ message: p.escape(cacheText(key)), indicator });
		}
		function checkRead() {
			if (!pending) {
				const running = statuses.filter((row) => active(row) && row.request_id);
				if (!running.length) return;
				pending = new Map(running.map((row) => [row.dataset, row.request_id]));
				freezeRead();
			}
			for (const [dataset, requestId] of pending) {
				const row = statuses.find((item) => item.dataset === dataset);
				// Never mistake an old successful generation for this read's completion.
				if (!row || row.request_id !== requestId || row.state === "idle") {
					finishRead("dms.connect.cache.changed");
					return;
				}
				if (active(row) && requestId) continue;
				if (row.state === "failed") outcome = "failed";
				else if (row.state === "waiting") { if (outcome !== "failed") outcome = "waiting"; }
				else if (row.state !== "succeeded" || !requestId) {
					finishRead("dms.connect.cache.changed");
					return;
				}
				pending.delete(dataset);
			}
			if (!pending.size) finishRead("dms.connect.cache.state." + (outcome || "succeeded"),
				outcome === "failed" ? "red" : "orange");
		}
		async function pollStatus() {
			if (!visible || !conn?.connected || pollGeneration === generation) return;
			const request = generation;
			pollGeneration = request;
			try {
				const result = await p.call(cacheMethod + "get_status");
				if (visible && request === generation) {
					statuses = result.datasets || [];
					checkRead();
				}
			} catch (error) {
				// A failed status request must not leave the native page frozen.
				if (visible && request === generation) finishRead("dms.connect.cache.unconfirmed");
				return;
			} finally {
				if (pollGeneration === request) pollGeneration = null;
			}
			if (visible && request === generation) schedulePoll();
		}
		function requestCache() {
			if (busy || posting || frozen) return;
			const firstRead = !statuses.some((item) => item.as_of > 0);
			p.dialog(cacheText("dms.connect.cache.read"), [
				{ fieldtype: "Section Break", description: cacheText("dms.connect.cache.help") },
				...datasets.map((dataset) => ({ fieldtype: "Check", fieldname: dataset,
					label: cacheText("dms.connect.cache.dataset." + dataset), default: firstRead ? 1 : 0 })),
			], cacheText("dms.connect.cache.read"), async (values, dialog) => {
				if (posting || frozen || !visible) return;
				const selected = datasets.filter((dataset) => values[dataset]);
				if (!selected.length) {
					frappe.msgprint(p.escape(cacheText("dms.connect.cache.select")));
					return;
				}
				dialog.hide();
				posting = true;
				const request = ++generation;
				stopPoll();
				freezeRead();
				try {
					const result = await p.call(cacheMethod + "request_refresh", { datasets: selected }, "POST");
					if (!visible || request !== generation) return;
					statuses = statuses.filter((row) => !selected.includes(row.dataset)).concat(result.datasets || []);
					pending = new Map(selected.map((dataset) => [dataset,
						statuses.find((row) => row.dataset === dataset)?.request_id || ""]));
					checkRead();
					schedulePoll();
				} catch (error) {
					if (visible && request === generation) finishRead("dms.connect.cache.unconfirmed");
				} finally {
					posting = false;
				}
			});
		}
		if (wrapper) {
			$(wrapper).on("show", () => {
				visible = true;
				void pollStatus();
			});
			$(wrapper).on("hide", () => { visible = false; generation += 1; finishRead(); });
		}
		async function refresh() {
			generation += 1;
			finishRead();
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
			statuses = [];
			if (conn.connected) await pollStatus();
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
