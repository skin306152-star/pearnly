(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const cacheText = await p.texts("dms.connect.cache.");
		let conn;
		// 四组都在：绑定成功时它们会被一起排队（缓存底座），这里是**进度显示**，
		// 不是"等操作员点按钮"。页面不再有手动触发。
		const datasets = ["vehicles", "customers", "company", "geo"];
		let statuses = [], timer, pollGeneration = null, visible = true, generation = 0;
		let frozen = false, announced = "";
		let startedAt = 0, peakDone = 0;
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
		// 「正在读取 DMS 主档… 2/4 组 · 12,480 条 · 00:48」
		// 为什么不是百分比：一次全量的大部分时间花在排队和等第三方（2026-09-25 实测
		// 四组一轮约 65 秒，前面还要排 53 秒），而百分比只按"正在读那一组"的条数比例
		// 算，排队那段时间恒定 0%，管理员会以为页面卡死。换成两样一直在动的东西：
		// 已完成的组数、读到的条数，以及从开始到现在的耗时。这三个都会随轮询往上走，
		// 所以每隔两秒至少有一个数字在变。
		function elapsedText() {
			const total = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
			const minutes = String(Math.floor(total / 60)).padStart(2, "0");
			return `${minutes}:${String(total % 60).padStart(2, "0")}`;
		}
		function progressSuffix() {
			const rows = datasets
				.map((dataset) => statuses.find((item) => item.dataset === dataset))
				.filter(Boolean);
			// 已完成组数只往上走：轮询之间偶发的状态回退不该让数字倒退，那看着像出错。
			peakDone = Math.max(peakDone, rows.filter((row) => row.state === "succeeded").length);
			// 占位符交给 `p.texts` 的取值函数填 —— 它**总是**会替换 `{n}`，自己先 replace
			// 等于白填（不带参数时那些占位符会被换成空串，页面上只剩「/ 组」）。
			const parts = [cacheText("dms.connect.cache.groups", peakDone, datasets.length)];
			const records = rows.reduce((sum, row) => sum + (Number(row.progress?.processed) || 0), 0);
			if (records > 0) {
				parts.push(cacheText("dms.connect.cache.records", records.toLocaleString("en-US")));
			}
			if (startedAt) parts.push(elapsedText());
			return parts.join(" · ");
		}
		function progressText() {
			const head = cacheText("dms.connect.cache.processing");
			const suffix = progressSuffix();
			return suffix ? `${head} ${suffix}` : head;
		}
		function updateOverlay() {
			const running = statuses.filter((row) => active(row));
			if (running.length) {
				sawActive = true;
				if (!startedAt) startedAt = Date.now();
				// Already frozen: keep the mask but let its text follow the progress.
				const text = p.escape(progressText());
				if (!frozen) { frappe.dom.freeze(text); frozen = true; }
				else setOverlayText(text);
				announced = "";
				return;
			}
			// 这一轮读完了：计时和组数峰值都归零，下一轮从头算。
			startedAt = 0;
			peakDone = 0;
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
			// **页面上只留一个动作按钮**（用户 2026-09-25："功能按钮只保留一个"）。
			// 原来的「测试连接」「停用/启用」是内联按钮，和右上角的主按钮挤在一块，
			// 操作员看不出该按哪个。这个页面只有一件事要做：换账号（右上角那个）。
			// 服务端两个 RPC（test_endpoint / set_connection_enabled）保留不动，
			// 只是不再从这个页面露出。
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
					// **绑定成功不再弹"连接正常 · 车型 3 · 分店 1 …"**（用户 2026-09-25：
					// "旧的提示和界面存在冲突"）。那句话是"快速探测"的产物，弹出时正好和
					// 白屏上的读取进度叠在一起，看着像两件事同时在跑。
					// 绑定后的反馈交给读取本身：白屏百分比 → 读完一句「数据更新完成」。
					// 探测失败仍然照原样报错（那时没有读取在跑）。
					await refresh();
				},
			);
		}
		await refresh();
	};
})();
