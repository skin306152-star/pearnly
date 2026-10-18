/* 采购 / 销售附件原件的上传（ADR-024 统一原件层；服务端 `pearnly_erp_portal.recognition.originals`）。
 * 上传框仍是官方 `frappe.ui.FileUploader`（拍照、多选、进度都照旧），只把「传到哪」换掉：按租户路线存原件层——
 * 能直传就由浏览器直接 PUT 到存放处（甲骨文 / 谷歌存储），不经过我们服务器；iPhone 照片、没配原件层的站点经服务器传。
 * 回复与原生上传的 File 一样有 `name / file_url / file_name`，之后挂附件、识别照旧。 */
(() => {
	const p = frappe.pearnly;
	const API = {purchase: "pearnly_erp_portal.purchasing.api.api_purchase_upload",
		sales: "pearnly_erp_portal.sales.api.api_sale_upload"};
	const new_key = () => (crypto?.randomUUID ? crypto.randomUUID()
		: `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 14)}`);

	const post = async (method, body) => {
		const response = await fetch(`/api/method/${method}`, {method: "POST", body, credentials: "same-origin",
			headers: {"X-Frappe-CSRF-Token": frappe.csrf_token, Accept: "application/json"}});
		const payload = await response.json().catch(() => null);
		if (!response.ok || !payload || !("message" in payload)) throw {...(payload || {}), status: response.status};
		return payload.message;
	};

	p.erp_upload_original = async (file, direction) => {
		const method = API[direction], request_key = new_key();
		const meta = () => new URLSearchParams({filename: file.name, byte_size: String(file.size), request_key});
		const begun = await post(`${method}_begin`, meta());
		if (begun.done) return begun.done;
		if (!begun.direct) {
			const body = new FormData();
			body.append("file", file, file.name);
			body.append("request_key", request_key);
			return post(method, body);
		}
		const put = await fetch(begun.url, {method: "PUT", body: file, headers: begun.headers});
		if (!put.ok && put.status !== 412) throw {status: put.status};  // 412：这个键之前已经传上去了，直接去确认
		for (let attempt = 0; attempt < 6; attempt++) {
			const finished = await post(`${method}_finish`, meta());
			if (!finished.retry) return finished;
			await new Promise(resolve => setTimeout(resolve, finished.retry * 1000));
		}
		throw {status: 503};
	};

	// 上传失败的原因：服务端给的那句（官方报错体 `_server_messages`），没有就说「这份没存上」。
	p.erp_upload_failure = error => {
		try {
			for (const item of JSON.parse(error?._server_messages || "[]")) {
				const message = JSON.parse(item)?.message;
				if (message) return frappe.utils.strip_html(String(message));
			}
		} catch (parse_error) {
			// 不是标准错误体：用下面的通用说法
		}
		return __("Could not store this document");
	};

	/** 把官方上传框的「上传」换成按原件层传：每份传完照原生上传成功一样交给 `on_success`，全部成功后关框。 */
	p.erp_upload_with = (uploader, direction, on_success, before) => {
		uploader.upload_files = async () => {
			if (before) await before();
			const files = uploader.uploader.files;
			await Promise.all(files.map(async item => {
				if (item.request_succeeded || item.uploading || !item.file_obj) return;
				Object.assign(item, {uploading: true, failed: false, error_message: ""});
				try {
					on_success(await p.erp_upload_original(item.file_obj, direction));
					item.request_succeeded = true;
				} catch (error) {
					Object.assign(item, {failed: true, error_message: p.erp_upload_failure(error)});
				} finally {
					item.uploading = false;
				}
			}));
			if (files.length && files.every(item => item.request_succeeded)) uploader.dialog.hide();
		};
	};
})();
