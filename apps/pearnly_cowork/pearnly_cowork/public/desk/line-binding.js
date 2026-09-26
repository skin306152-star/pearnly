/* LINE 绑定块（Cowork）：把**当前登录的账号**绑到本站点的 LINE 官方号。
 *
 * 照 ERP 的同名块（`pearnly_erp_portal/public/desk/line-binding.js`），只换接口与词条命名空间：
 * 只用在本人的「个人信息」页，绑定码只给本人；码 10 分钟有效、可换一个，每 4 秒问一次状态，
 * 绑上了自己切到"已绑定"；解绑先确认。二维码由服务端本地生成（`line/binding.py::_qr`）。
 *
 * `p.line_binding(parent)` 把这一块画进 `parent`（jQuery），返回 `{destroy}`。
 */
(() => {
	const p = frappe.pearnly;
	const api = "pearnly_cowork.line.api.";
	const POLL_MS = 4000;

	p.line_binding = async parent => {
		const t = await p.texts("cowork.line.");
		let form = null, bound = null, code_expires = 0, countdown = null, poll = null, requesting = null;
		const alive = () => parent[0]?.isConnected;
		const stop = () => {
			clearInterval(countdown); clearInterval(poll);
			countdown = poll = null;
		};

		function paint(fields) {
			parent.empty();
			form = p.layout(parent, fields);
		}

		function paint_unbound() {
			paint([
				{fieldtype: "HTML", fieldname: "qr"},
				{fieldtype: "Column Break"},
				{fieldtype: "Data", fieldname: "bot_id", label: "Bot ID", read_only: 1},
				{fieldtype: "Data", fieldname: "code", label: t("cowork.line.code"), read_only: 1},
				{fieldtype: "HTML", fieldname: "expires"},
				{fieldtype: "Button", fieldname: "new_code", label: t("cowork.line.newCode"),
					click: () => void new_code()},
			]);
		}

		function paint_bound(state) {
			paint([
				{fieldtype: "Data", fieldname: "line_name", label: t("cowork.line.lineName"), read_only: 1,
					default: state.display_name || "LINE User"},
				{fieldtype: "Column Break"},
				{fieldtype: "Datetime", fieldname: "bound_at", label: t("cowork.line.boundAt"), read_only: 1,
					default: state.bound_at || ""},
				{fieldtype: "Section Break"},
				{fieldtype: "Button", fieldname: "unbind", label: t("cowork.line.unbind"),
					click: () => frappe.confirm(p.escape(t("cowork.line.unbindConfirm")), async () => {
						await p.call(api + "api_line_unbind", {}, "POST");
						code_expires = 0;
						p.success(__("LINE unbound"), "cancel");
						await refresh();
					})},
			]);
		}

		function tick() {
			const target = form?.fields_dict.expires?.$wrapper;
			if (!alive() || !target) {stop(); return;}
			const left = code_expires - Date.now();
			if (left <= 0) {
				target.html(`<p class="text-danger">${p.escape(t("cowork.line.expired"))}</p>`);
				clearInterval(countdown); countdown = null;
				return;
			}
			const seconds = Math.floor(left / 1000);
			const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
			target.html(`<p class="text-muted">${p.escape(t("cowork.line.remaining", clock))}</p>`);
		}

		// 发码：同一时刻只发一个（连点「换一个码」不会并发出好几个码，服务端也只认最新那个）。
		async function new_code() {
			if (requesting) return requesting;
			requesting = (async () => {
				const data = await p.call(api + "api_line_binding_code", {}, "POST");
				if (bound !== false || !form || !alive()) return;
				form.set_value("code", data.code || "");
				form.set_value("bot_id", data.bot_basic_id || "");
				// 二维码是服务端生成的 data URI（不是外部地址）；名字是客户数据，一律转义。
				form.fields_dict.qr.$wrapper.html(data.bot_qr
					? `<img src="${p.escape(data.bot_qr)}" alt="${p.escape(data.bot_name || "LINE")}" width="160" height="160">`
					: "");
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
			if (!alive()) {stop(); return;}
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

		// 调用方的格子可能在我们被调用之后才挂到页面上（弹窗里实测如此）：
		// 先等它挂上再开始，否则 `alive()` 为假、整块什么都不画。
		for (let i = 0; i < 100 && !alive(); i++) await new Promise(resolve => setTimeout(resolve, 50));
		await refresh();
		return {destroy: stop};
	};
})();
