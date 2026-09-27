(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const cacheText = await p.texts("dms.connect.cache.");
		const acct = await p.texts("dms.connect.acct.");
		let accounts = { read: null, write: [] };
		let conn;
		// 四组都在：绑定成功时它们会被一起排队（缓存底座），这里是**进度显示**，
		// 不是"等操作员点按钮"。页面不再有手动触发。
		const datasets = ["vehicles", "customers", "company", "geo"];
		let statuses = [], timer, pollGeneration = null, visible = true, generation = 0;
		let frozen = false, announced = "";
		let startedAt = 0, peakDone = 0;
		const cacheMethod = "pearnly_dms.connections.snapshots.";
		const active = (row) => ["queued", "running", "waiting"].includes(row.state);
		// 还在读、但这一组已经能用（客户组：索引已发布，逐个详情在后台读）：不再遮挡页面，
		// 只继续轮询到读完。ris 的客户详情要一个多小时，不能让连接页白屏那么久。
		const blocking = (row) => active(row) && !row.progress?.usable;
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
			const running = statuses.filter((row) => blocking(row));
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
			if (statuses.some(active)) return; // 还有组在后台读：读完再说
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
			accounts = ep ? await p.call("pearnly_dms.connections.api.get_accounts") : { read: null, write: [] };
			page.main.empty();
			page.clear_inner_toolbar();
			// 右上角只留两个按钮（用户 2026-09-27）：改写权限账号（主）+ 读档账号（次）；
			// 换连接账号放进「…」菜单。还没连上 DMS 时只有「连接」一个按钮。
			page.clear_menu();
			if (ep) {
				page.add_menu_item(t("connect.action.edit"), wizard);
				page.set_primary_action(acct("dms.connect.acct.writeAdd"), editWriteAccounts);
				page.set_secondary_action(
					acct(accounts.read ? "dms.connect.acct.readChange" : "dms.connect.acct.readAdd"),
					editReadAccount,
				);
			} else {
				page.set_primary_action(t("connect.action.connect"), wizard);
				page.clear_secondary_action();
			}
			page.set_indicator(
				t(conn.connected ? "connect.state.connected" : "connect.state.notConnected"),
				conn.connected ? "green" : "orange",
			);
			// 不再有内联的「测试连接」「停用/启用」按钮（用户 2026-09-25）；服务端两个 RPC
			// （test_endpoint / set_connection_enabled）保留不动，只是不再从这个页面露出。
			// 只列管理员要看的四样（用户 2026-09-27："界面调整下"）：连没连上看标题旁的状态；
			// 连接账号只用来绑定，登记了读档 / 改写账号后系统读写都不再用它。
			const fields = [{ fieldtype: "Section Break" }];
			for (const [name, label, value] of [
				["url", t("connect.field.url"), ep?.system_url || "—"],
				["account", acct("dms.connect.acct.connectionField"), ep?.username || "—"],
				["read", acct("dms.connect.acct.readField"), accounts.read?.username || acct("dms.connect.acct.readNone")],
				["write", acct("dms.connect.acct.writeField"),
					accounts.write.map((row) => row.username).join(", ") || acct("dms.connect.acct.writeNone")],
			]) {
				fields.push({ fieldtype: "Data", fieldname: name, label, default: value, read_only: 1 });
			}
			p.layout(page.main, fields);
			statuses = [];
			shownPercent = 0;
			if (conn.connected) await pollStatus();
		}
		// 服务端的错误话术按四种语言一起回（`{th, zh, en, ja}`）：按当前界面语言取一句，取不到退回泰文。
		function friendlyText(value) {
			if (!value || typeof value !== "object") return value || "";
			const lang = (frappe.boot.lang || "th").slice(0, 2);
			return value[lang] || value.th || "";
		}
		function feedback(result) {
			if (!result.ok) {
				frappe.msgprint({
					message: p.escape(friendlyText(result.error_friendly) || t("connect.error.failed")),
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
		// 按用途试过但权限不够：列出缺的那几项（使用记录 / 客户列表 …），不笼统说"失败"。
		function accountFailure(result) {
			const missing = (result.missing || []).map((key) => acct(`dms.connect.acct.check.${key}`));
			const message = missing.length
				? acct("dms.connect.acct.missing", result.username, missing.join(", "))
				: friendlyText(result.error_friendly) || t("connect.error.failed");
			frappe.msgprint({ message: p.escape(message), indicator: "red" });
		}
		function editReadAccount() {
			p.dialog(
				acct(accounts.read ? "dms.connect.acct.readChange" : "dms.connect.acct.readAdd"),
				[
					{ fieldtype: "HTML", fieldname: "help", options: `<p class="text-muted">${p.escape(acct("dms.connect.acct.readHelp"))}</p>` },
					{ fieldtype: "Data", fieldname: "username", label: t("connect.field.account"),
						default: accounts.read?.username || "", reqd: 1 },
					{ fieldtype: "Password", fieldname: "password", label: __("Password"), reqd: 1 },
				],
				acct("dms.connect.acct.testAndSave"),
				async (values, d) => {
					const result = await p.call("pearnly_dms.connections.api.set_read_account", values, "POST");
					if (!result.ok) return accountFailure(result);
					d.set_value("password", "");
					d.hide();
					p.success(acct("dms.connect.acct.saved"));
					await refresh();
				},
			);
		}
		// 改写账号：默认一行「账号 · 密码」，下面「添加」再加一行，每行可删（用户 2026-09-27）。
		// 用官方控件逐行拼（make_control），不用 Grid：Grid 的密码列不遮挡、编辑框会撑出表格。
		// 已登记的行账号只读、密码留空 = 不改；删掉的行 = 移除。服务端整表试过才存。
		function editWriteAccounts() {
			const entries = [];
			const d = p.dialog(
				acct("dms.connect.acct.writeAdd"),
				[
					{ fieldtype: "HTML", fieldname: "help", options: `<p class="text-muted">${p.escape(acct("dms.connect.acct.writeHelp"))}</p>` },
					{ fieldtype: "HTML", fieldname: "rows" },
					{ fieldtype: "Button", fieldname: "add", label: acct("dms.connect.acct.addRow"), click: () => addRow({}) },
				],
				acct("dms.connect.acct.testAndSave"),
				async (values, dialog) => {
					const payload = entries.filter((entry) => !entry.removed).map((entry) => ({
						name: entry.name,
						username: String(entry.user.get_value() || "").trim(),
						password: entry.pass.get_value() || "",
					}));
					const result = await p.call(
						"pearnly_dms.connections.api.save_write_accounts",
						{ accounts: JSON.stringify(payload) },
						"POST",
					);
					if (!result.ok) return accountFailure(result);
					dialog.hide();
					p.success(acct("dms.connect.acct.saved"));
					await refresh();
				},
			);
			const holder = d.fields_dict.rows.$wrapper;
			function control(parent, df) {
				const field = frappe.ui.form.make_control({ parent, df, only_input: true, render_input: true });
				field.refresh();
				return field;
			}
			function addRow(row) {
				const $row = $('<div class="row form-group"></div>').appendTo(holder);
				const entry = { name: row.name || "", removed: false };
				entry.user = control($('<div class="col-xs-5"></div>').appendTo($row), {
					fieldtype: "Data", fieldname: `username_${entries.length}`,
					placeholder: t("connect.field.account"), read_only: row.name ? 1 : 0,
				});
				entry.user.set_value(row.username || "");
				entry.pass = control($('<div class="col-xs-5"></div>').appendTo($row), {
					fieldtype: "Password", fieldname: `password_${entries.length}`, placeholder: __("Password"),
				});
				control($('<div class="col-xs-2"></div>').appendTo($row), {
					fieldtype: "Button", fieldname: `remove_${entries.length}`, label: acct("dms.connect.acct.removeRow"),
					click: () => { entry.removed = true; $row.remove(); },
				});
				entries.push(entry);
			}
			const saved = accounts.write.map((row) => ({ name: row.name, username: row.username }));
			(saved.length ? saved : [{}]).forEach(addRow);
			return d;
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
					const saved = await p.call("pearnly_dms.connections.api.save_connection", values, "POST");
					d.set_value("password", "");
					d.hide();
					// 读全档要等读档账号（不占连接账号）：告诉管理员下一步做什么。
					if (saved?.reading === "awaiting_read_account") {
						frappe.msgprint(p.escape(acct("dms.connect.acct.readFirst")));
					}
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
