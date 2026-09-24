(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const cacheText = await p.texts("dms.connect.cache.");
		let conn,
			busy = false;
		// 四组都在：绑定成功时它们会被一起排队（缓存底座），这里是**进度显示**，
		// 不是"等操作员点按钮"。页面不再有手动触发。
		const datasets = ["vehicles", "customers", "company", "geo"];
		let statuses = [], timer, pollGeneration = null, visible = true, generation = 0;
		let frozen = false, announced = "";
		let shownPercent = 0;
		const cacheMethod = "pearnly_dms.connections.snapshots.";
		const active = (row) => ["queued", "running", "waiting"].includes(row.state);
		let sawActive = false;
		function stopPoll() {
			clearTimeout(timer);
			timer = null;
		}
		function schedulePoll() {
			stopPoll();
			// 只在真的有人在读的时候轮询：空闲页面读一次状态就停，不做无谓的定时敲门。
			if (visible && conn?.connected && statuses.some(active)) timer = setTimeout(pollStatus, 2000);
		}
		// 「正在读取 DMS 主档… 58%」：四组各占四分之一，已知总数的组再按"读到几条/共几条"
		// 细分，所以数字读的过程中一直在动，管理员不用干等一张空白页。
		// **只往上走**：轮询之间偶发的状态回退不该让进度倒退，那会让人以为出错了。
		// 四组没全成功之前最多显示 99% —— 不预告"读完了"。
		function progressPercent() {
			const share = 1 / datasets.length;
			let done = 0,
				reading = false;
			for (const dataset of datasets) {
				const row = statuses.find((item) => item.dataset === dataset);
				if (!row) continue;
				if (row.state === "succeeded") {
					done += share;
					continue;
				}
				if (!active(row)) continue;
				reading = true;
				const processed = row.progress?.processed || 0;
				const total = row.progress?.total;
				if (typeof total === "number" && total > 0) {
					done += share * Math.min(1, processed / total);
				}
			}
			if (!reading) return null;
			shownPercent = Math.max(shownPercent, done * 100);
			return Math.min(99, Math.floor(shownPercent));
		}
		function progressText() {
			const head = cacheText("dms.connect.cache.processing");
			const percent = progressPercent();
			return percent === null ? head : `${head} ${percent}%`;
		}
		function updateOverlay() {
			const running = statuses.filter((row) => active(row));
			if (running.length) {
				sawActive = true;
				// Already frozen: keep the mask but let its text follow the progress.
				const text = p.escape(progressText());
				if (!frozen) { frappe.dom.freeze(text); frozen = true; }
				else setOverlayText(text);
				announced = "";
				return;
			}
			if (frozen) { frappe.dom.unfreeze(); frozen = false; }
			announceResult();
		}
		// 原生覆盖的正文是 `#freeze .freeze-message p.lead`。重复调 freeze 只会加计数、
		// 不换文字，所以冻结之后直接改这一段；没有 DOM（离线断言）时静默跳过。
		function setOverlayText(text) {
			if (typeof document === "undefined") return;
			const lead = document.querySelector("#freeze .freeze-message p.lead");
			if (lead) lead.textContent = text;
		}
		// 只在"从读取中变成读取完"那一次提示，不在每次轮询里重复弹。
		function announceResult() {
			if (!sawActive) return;
			const key = statuses.map((row) => `${row.dataset}:${row.state}:${row.count}`).join("|");
			if (!key || key === announced) return;
			announced = key;
			const failed = statuses.some((row) => row.state === "failed");
			const succeeded = statuses.length && statuses.every((row) => row.state === "succeeded");
			if (failed) frappe.msgprint({ message: p.escape(cacheText("dms.connect.cache.state.failed")), indicator: "red" });
			else if (succeeded) p.success(cacheText("dms.connect.cache.state.succeeded"));
		}
		async function pollStatus() {
			if (!visible || !conn?.connected || pollGeneration === generation) return;
			const request = generation;
			pollGeneration = request;
			try {
				const result = await p.call(cacheMethod + "get_status");
				if (visible && request === generation) {
					statuses = result.datasets || [];
					updateOverlay();
				}
			} catch (error) {
				// A failed status request must not leave the native page frozen.
				if (visible && request === generation) {
					unfreeze();
					frappe.msgprint(p.escape(cacheText("dms.connect.cache.unconfirmed")));
				}
				return;
			} finally {
				if (pollGeneration === request) pollGeneration = null;
			}
			if (visible && request === generation) schedulePoll();
		}
		function unfreeze() {
			if (frozen) { frappe.dom.unfreeze(); frozen = false; }
		}
		if (wrapper) {
			$(wrapper).on("show", () => {
				visible = true;
				void pollStatus();
			});
			$(wrapper).on("hide", () => { visible = false; generation += 1; unfreeze(); });
		}
		async function refresh() {
			generation += 1;
			unfreeze();
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
			statuses = [];
			shownPercent = 0;
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
