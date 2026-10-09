/* Cowork 页面共用：选文件与带文件的 POST（multipart）。盘点上传、三个对账中心的两个上传口都走这里；
 * 录入工作台的原件上传（可直传谷歌存储，ADR-016）也在这里（`upload_original`）。
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
		// 没有标准报错：按状态说现成的话（不把服务端异常原文 / 英文给人看，2026-10-09）。
		if (Number(status) === 413) return __("The uploaded file exceeds the 100 MiB limit.");
		return Number(status) ? __("intake.error.http", [status]) : __("intake.error.poll_unreachable");
	};

	// 选一个文件：系统文件框由官方按钮点开。选择框用 DOM 建、用完即删（与录入工作台同一做法，
	// 页面脚本不手写通用控件标签）。取消选择时返回 null。
	p.pick_files = (accept, multiple = false) => new Promise(resolve => {
		const input = $(document.createElement("input")).attr({ type: "file", accept }).addClass("hide")
			.prop("multiple", multiple).appendTo(document.body);
		input.on("change", () => { resolve(Array.from(input[0].files || [])); input.remove(); });
		input.on("cancel", () => { resolve([]); input.remove(); });
		input.trigger("click");
	});
	p.pick_file = async accept => (await p.pick_files(accept, false))[0] || null;

	p.post_files = async (method, fields) => {
		const body = new FormData();
		for (const [key, value] of Object.entries(fields)) {
			for (const item of Array.isArray(value) ? value : [value]) {
				if (item instanceof File) body.append(key, item, item.name);
				else if (item !== undefined && item !== null) body.append(key, item);
			}
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

	/** 录入工作台上传一份原件（ADR-016），返回原件信息（与 `files.api.upload` 的回复相同）。
	 * 开了直传：`begin_upload` 要一个签名地址 → 原件直接 PUT 到谷歌存储（新加坡），不经过我们服务器 → `finish_upload`
	 * 让服务端核对后登记（Cloud Run 一时忙会回 `retry`，隔几秒再确认）。PUT 回 412 = 这个键之前已经传上去了，直接去确认。
	 * 没开直传或表格类原件（服务端回 `direct: 0`）：照旧 multipart 交给 `files.api.upload`。
	 * 失败时抛出 `{status, _server_messages?, exception?}`（不弹框：调用方把原因写在这一行上）。
	 * `alive()` 为假（页面已切走）时不再继续确认，返回 null。 */
	const FILES = "pearnly_cowork.files.api.";
	p.upload_original = async (file, request_key, {alive = () => true, wait} = {}) => {
		const pause = wait || (ms => new Promise(resolve => setTimeout(resolve, ms)));
		const post = async (method, body, query = "") => {
			const response = await fetch(`/api/method/${FILES}${method}${query}`, {
				method: "POST", body, credentials: "same-origin",
				headers: {"X-Frappe-CSRF-Token": frappe.csrf_token},
			});
			const payload = await response.json().catch(() => null);
			if (!response.ok || !payload?.message) throw {...(payload || {}), status: response.status};
			return payload.message;
		};
		const meta = {filename: file.name, byte_size: String(file.size), request_key};
		const begun = await post("begin_upload", new URLSearchParams(meta));
		if (!begun.direct) {
			// 不传归属：服务端按站点解析（见 `files.api.upload`）。
			const body = new FormData();
			body.append("file", file, file.name);
			return post("upload", body, "?" + new URLSearchParams({request_key}));
		}
		if (begun.done) return begun.done;
		const put = await fetch(begun.url, {method: "PUT", body: file, headers: begun.headers});
		if (!put.ok && put.status !== 412) throw {status: put.status};
		for (let attempt = 0; attempt < 6; attempt++) {
			const finished = await post("finish_upload", new URLSearchParams(meta));
			if (!finished.retry) return finished;
			await pause(finished.retry * 1000);
			if (!alive()) return null;
		}
		throw {status: 503};
	};
})();
