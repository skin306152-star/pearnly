/* LINE 集成（`/desk/pearnly-integrations`）：把**当前登录的账号**绑到本站点的 LINE 官方号。
 *
 * 迁自旧线上 `src/home/page-integrations.ts`（ec2e5c957e6b）的 ERP LINE 那一块，行为照旧：
 *   · 未绑定：① 扫码 / 搜 Bot ID 加好友 ② 把 6 位绑定码发给 Bot（10 分钟有效，可换一个）
 *     ③ 等待 —— 本页每 4 秒问一次状态，绑上了自己切到"已绑定"；
 *   · 已绑定：LINE 名称、绑定时间、怎么用，以及「解绑 LINE」（先确认）。
 * 有意的变化：二维码由服务端本地生成（`integrations/binding.py::_qr`），不再把加好友链接
 * 交给第三方画图网站；使用提示按新系统的真实流程写（发"ซื้อ/ขาย"切方向，单据在采购 /
 * 销售页的「LINE」里复核确认），不照抄旧线上"在 LINE 里确认"的说法。
 *
 * 页面壳在 core（`pearnly_integrations.js`），这里只给渲染；文案走本产品术语表的 `erp.line.*`。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_erp_portal.integrations.api.";
	const POLL_MS = 4000;

	p.integrations = async (page, _shell_t, wrapper) => {
		const t = await p.texts("erp.line.");
		let form = null, bound = null, code_expires = 0, countdown = null, poll = null, requesting = null;

		const stop = () => {
			clearInterval(countdown); clearInterval(poll);
			countdown = poll = null;
		};
		// 离开本页就停表，回来再问一次状态（框架切页时对页面容器触发 hide / show）。
		$(wrapper).off(".pearnly-integrations")
			.on("hide.pearnly-integrations", stop)
			.on("show.pearnly-integrations", () => void refresh());

		const html = (fieldname, options = "") => ({fieldtype: "HTML", fieldname, options});
		const indicator = state => page.set_indicator(
			state ? t("erp.line.status.bound") : t("erp.line.status.unbound"), state ? "green" : "gray");

		function paint_unbound() {
			p.clear_page(page);
			page.set_title(t("erp.line.title"));
			indicator(false);
			form = p.layout(page.main, [
				html("intro", `<p class="text-muted">${p.escape(t("erp.line.intro"))}</p>`),
				{fieldtype: "Section Break", label: "1. " + t("erp.line.step1.title"),
					description: t("erp.line.step1.desc")},
				html("qr"),
				{fieldtype: "Column Break"},
				{fieldtype: "Data", fieldname: "bot_id", label: "Bot ID", read_only: 1},
				{fieldtype: "Button", fieldname: "open_line", label: t("erp.line.openLine")},
				{fieldtype: "Section Break", label: "2. " + t("erp.line.step2.title"),
					description: t("erp.line.step2.desc")},
				{fieldtype: "Data", fieldname: "code", label: t("erp.line.code"), read_only: 1},
				html("expires"),
				{fieldtype: "Button", fieldname: "new_code", label: t("erp.line.newCode")},
				{fieldtype: "Section Break", label: "3. " + t("erp.line.step3.title")},
				html("wait", `<p class="text-muted">${p.escape(t("erp.line.step3.desc"))}</p>`),
			]);
			form.fields_dict.open_line.$input.prop("disabled", true);
			form.fields_dict.new_code.$input.on("click", () => void new_code());
		}

		function paint_bound(state) {
			p.clear_page(page);
			page.set_title(t("erp.line.title"));
			indicator(true);
			const tips = [t("erp.line.tip.direction"), t("erp.line.tip.inbox")]
				.map(text => `<li>${p.escape(text)}</li>`).join("");
			form = p.layout(page.main, [
				{fieldtype: "Data", fieldname: "line_name", label: t("erp.line.lineName"), read_only: 1,
					default: state.display_name || "LINE User"},
				{fieldtype: "Column Break"},
				{fieldtype: "Datetime", fieldname: "bound_at", label: t("erp.line.boundAt"), read_only: 1,
					default: state.bound_at || ""},
				{fieldtype: "Section Break", label: t("erp.line.readyTitle")},
				html("tips", `<ul class="text-muted">${tips}</ul>`),
				{fieldtype: "Section Break"},
				{fieldtype: "Button", fieldname: "unbind", label: t("erp.line.unbind")},
			]);
			form.fields_dict.unbind.$input.addClass("btn-danger").on("click", () =>
				frappe.confirm(p.escape(t("erp.line.unbindConfirm")), async () => {
					await p.call(api + "api_line_unbind", {}, "POST");
					code_expires = 0;
					p.success(__("LINE unbound"), "cancel");
					await refresh();
				}));
		}

		function tick() {
			const left = code_expires - Date.now();
			const target = form?.fields_dict.expires?.$wrapper;
			if (!target) return;
			if (left <= 0) {
				target.html(`<p class="text-danger">${p.escape(t("erp.line.expired"))}</p>`);
				clearInterval(countdown); countdown = null;
				return;
			}
			const seconds = Math.floor(left / 1000);
			const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
			target.html(`<p class="text-muted">${p.escape(t("erp.line.remaining", clock))}</p>`);
		}

		// 发码：同一时刻只发一个（连点「换一个码」不会并发出好几个码，服务端也只认最新那个）。
		async function new_code() {
			if (requesting) return requesting;
			requesting = (async () => {
				const data = await p.call(api + "api_line_binding_code", {}, "POST");
				if (bound !== false || !form) return;
				form.set_value("code", data.code || "");
				form.set_value("bot_id", data.bot_basic_id || "");
				// 二维码是服务端生成的 data URI（不是外部地址），名字是客户数据，一律转义。
				form.fields_dict.qr.$wrapper.html(data.bot_qr
					? `<img src="${p.escape(data.bot_qr)}" alt="${p.escape(data.bot_name || "LINE")}" width="160" height="160">`
					: "");
				const open = form.fields_dict.open_line.$input;
				open.prop("disabled", !data.bot_friend_url).off("click.pearnly")
					.on("click.pearnly", () => window.open(data.bot_friend_url, "_blank", "noopener,noreferrer"));
				code_expires = new Date(data.expires_at).getTime();
				clearInterval(countdown);
				tick();
				countdown = setInterval(tick, 1000);
			})();
			try {
				await requesting;
			} finally {
				requesting = null;
			}
		}

		async function refresh() {
			const state = await p.call(api + "api_line_binding");
			if (state.bound) {
				stop();
				if (bound !== true) paint_bound(state);
				bound = true;
				return;
			}
			if (bound !== false) {
				bound = false;
				paint_unbound();
				code_expires = 0;
			}
			if (!code_expires || code_expires <= Date.now()) await new_code();
			poll = poll || setInterval(() => void refresh().catch(stop), POLL_MS);
		}

		await refresh();
	};
})();
