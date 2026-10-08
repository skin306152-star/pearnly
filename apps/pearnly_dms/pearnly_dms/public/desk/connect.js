(() => {
	const p = frappe.pearnly;
	p.connect = async (page, t, wrapper) => {
		const acct = await p.texts("dms.connect.acct.");
		let accounts = { read: null, write: [] };
		let conn;
		// 读资料全在后台（用户 2026-09-27："那个白屏进度不要吧，首次读档让它在后台默认执行"）：
		// 这个页面只管账号，不遮挡、不轮询、不播报读取进度。
		async function refresh() {
			conn = await p.call("pearnly_dms.connections.api.get_connection");
			const ep = conn.endpoint;
			accounts = ep ? await p.call("pearnly_dms.connections.api.get_accounts") : { read: null, write: [] };
			page.main.empty();
			page.clear_inner_toolbar();
			// 右上角只有两个按钮（用户 2026-09-27："只要读档账号和权限账号就好了"）：
			// 改写权限账号（主）+ 读档账号（次）。读档账号就是本站连 DMS 用的账号，没有另外的"连接账号"；
			// 还没连上 DMS 时只有「添加读档账号」一个按钮。
			page.clear_menu();
			if (ep) {
				page.set_primary_action(acct("dms.connect.acct.writeAdd"), editWriteAccounts);
				page.set_secondary_action(
					acct(accounts.read ? "dms.connect.acct.readChange" : "dms.connect.acct.readAdd"),
					editReadAccount,
				);
			} else {
				page.set_primary_action(acct("dms.connect.acct.readAdd"), editReadAccount);
				page.clear_secondary_action();
			}
			page.set_indicator(
				t(conn.connected ? "connect.state.connected" : "connect.state.notConnected"),
				conn.connected ? "green" : "orange",
			);
			// 不再有内联的「测试连接」「停用/启用」按钮（用户 2026-09-25）；服务端两个 RPC
			// （test_endpoint / set_connection_enabled）保留不动，只是不再从这个页面露出。
			// 只列管理员要看的三样：连没连上看标题旁的状态。
			const fields = [{ fieldtype: "Section Break" }];
			for (const [name, label, value] of [
				["url", t("connect.field.url"), ep?.system_url || "—"],
				["read", acct("dms.connect.acct.readField"), accounts.read?.username || acct("dms.connect.acct.readNone")],
				["write", acct("dms.connect.acct.writeField"),
					accounts.write.map((row) => row.username).join(", ") || acct("dms.connect.acct.writeNone")],
			]) {
				fields.push({ fieldtype: "Data", fieldname: name, label, default: value, read_only: 1 });
			}
			p.layout(page.main, fields);
		}
		// 服务端的错误话术按四种语言一起回（`{th, zh, en, ja}`）：按当前界面语言取一句，取不到退回泰文。
		function friendlyText(value) {
			if (!value || typeof value !== "object") return value || "";
			const lang = (frappe.boot.lang || "th").slice(0, 2);
			return value[lang] || value.th || "";
		}
		// 「真试一次再存」在后台跑（2026-10-02：同步请求会撞 120 秒上限）：发起后先问一次，之后推送优先、轮询兜底
		// （实时连接在线 20 秒、不在线 5 秒一次，同 Cowork 录入）。读完返回原来的回复（`{ok, …}`）；后台报错按服务端给的
		// 原因用原生提示弹出；30 秒仍问不到任何状态（号丢了）也停。
		async function checkInBackground(method, args) {
			const check_key = frappe.utils?.get_random?.(24) || String(Date.now()) + Math.random().toString(36).slice(2, 12);
			const started = Date.now();
			let wake = null, pushed = false;
			const heard = (data) => { if (data?.token === check_key) { pushed = true; wake?.(); } };
			frappe.realtime?.on?.("pearnly_dms_account_check", heard);
			try {
				await p.call(method, { ...args, check_key }, "POST");
				for (let first = true; ; first = false) {
					if (!first && !pushed) await new Promise((resolve) => {
						wake = resolve;
						setTimeout(resolve, frappe.realtime?.socket?.connected ? 20000 : 5000);
					});
					wake = null; pushed = false;
					let now;
					try {
						now = (await p.call("pearnly_dms.connections.api.account_check", { check_key })) || {};
					} catch (error) {
						continue; // 一时问不到就下次再问
					}
					if (now.state === "done") return now.result || {};
					if (now.state === "failed" || (!now.state && Date.now() - started > 30000)) {
						frappe.msgprint({ message: p.escape(now.message || t("connect.error.failed")), indicator: "red" });
						return { ok: false, shown: true };
					}
				}
			} finally {
				frappe.realtime?.off?.("pearnly_dms_account_check", heard);
			}
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
					// 读档账号就是连接本身：第一次保存即连上 DMS，并开始读取缺的资料。
					const result = await checkInBackground("pearnly_dms.connections.api.set_read_account", values);
					if (result.shown) return;
					if (!result.ok) return accountFailure(result);
					d.set_value("password", "");
					d.hide();
					p.success(acct("dms.connect.acct.saved"), "click"); // 缺的资料组已在后台开始读，页面不等
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
					const result = await checkInBackground(
						"pearnly_dms.connections.api.save_write_accounts",
						{ accounts: JSON.stringify(payload) },
					);
					if (result.shown) return;
					if (!result.ok) return accountFailure(result);
					dialog.hide();
					p.success(acct("dms.connect.acct.saved"), "click");
					await refresh();
				},
			);
			const holder = d.fields_dict.rows.$wrapper;
			// 用完整的官方控件结构（不是 only_input）：密码框的"显示密码"眼睛靠 `.control-input` 定位在
			// 框内右侧，只取输入框时眼睛会掉到框下面（用户 2026-09-27 截图）。标签留空，靠占位文字说明。
			function control(parent, df) {
				const field = frappe.ui.form.make_control({ parent, df: { label: "", ...df }, render_input: true });
				field.refresh();
				field.disable_password_checks?.(); // 这里不是设密码，不要密码强度条
				// 空标签也占一行高度，输入框会比同一行的「删除」按钮低一截：藏掉让整行对齐。
				field.$wrapper?.find(".control-label").addClass("hide");
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
		await refresh();
	};
})();
