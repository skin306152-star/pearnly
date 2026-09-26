/* Cowork 页面共用：选文件与带文件的 POST（multipart）。盘点上传、三个对账中心的两个上传口都走这里。
 *
 * `frappe.call` 发不了文件，所以用 fetch + CSRF 头；服务端报错照官方弹窗显示
 * （取 `_server_messages` 里的那句，与录入工作台 `intake-upload.js` 的 `failure` 同一口径），
 * 抛出后调用方停在原处，不往下走。
 */
(() => {
	const p = frappe.pearnly;

	const server_message = (payload, status) => {
		try {
			for (const item of JSON.parse(payload?._server_messages || "[]")) {
				const message = JSON.parse(item)?.message;
				if (message) return String(message);
			}
		} catch (error) {
			// 不是标准错误体（网关 502 之类）——下面按状态码说。
		}
		return payload?.exception || `HTTP ${status}`;
	};

	// 选一个文件：系统文件框由官方按钮点开。选择框用 DOM 建、用完即删（与录入工作台同一做法，
	// 页面脚本不手写通用控件标签）。取消选择时返回 null。
	p.pick_file = (accept) => new Promise(resolve => {
		const input = $(document.createElement("input")).attr({ type: "file", accept }).addClass("hide")
			.appendTo(document.body);
		input.on("change", () => { resolve(input[0].files?.[0] || null); input.remove(); });
		input.on("cancel", () => { resolve(null); input.remove(); });
		input.trigger("click");
	});

	p.post_files = async (method, fields) => {
		const body = new FormData();
		for (const [key, value] of Object.entries(fields)) {
			if (value instanceof File) body.append(key, value, value.name);
			else if (value !== undefined && value !== null) body.append(key, value);
		}
		const response = await fetch(`/api/method/${method}`, {
			method: "POST", body, credentials: "same-origin",
			headers: { "X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json" },
		});
		const payload = await response.json().catch(() => null);
		if (!response.ok || !payload || !("message" in payload)) {
			frappe.msgprint({ message: server_message(payload, response.status), indicator: "red" });
			throw new Error("request failed");
		}
		return payload.message;
	};
})();
