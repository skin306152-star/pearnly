// 页面 JS 报错回传（共用件连带回归闸门，2026-10-25）：Desk 页面上的报错与未处理的 Promise 拒绝发给
// `pearnly_core.desk.client_errors.report`，记进本站 Error Log，部署后回读与冒烟按它查。
// 最先加载（`hooks.app_include_js` 第一个），后面的共用组件出错也抓得到。每页最多发 10 条，同一条只发一次；
// 只发报错原文、文件、行列、堆栈和路由路径（服务端再去个人信息、限频）。
// 前端单页（LIFF 票据页 pearnly-app、CC 对话页 assistant、dms-booking、手机盘点 stocktake）也挂这一份（www 页面用 Jinja 的 `versioned_asset` 挂，地址带内容指纹）；
// Vue / React 自己接住、不再抛到 window 的报错由它们调 `window.pearnlyReportError(error, kind)` 交过来。
(() => {
	const sent = new Set();
	let budget = 10;
	const token = () => (window.frappe && window.frappe.csrf_token) || window.csrf_token || "";
	// 被 CSRF 拒掉的（登录会话、页面还没拿到令牌：LINE 盘点页、LIFF 票据页的令牌是页面自己问接口要的）先留着，
	// 页面拿到令牌后补发一次（每秒看一次，最多看 60 秒）。访客不校验 CSRF，第一次就收。
	const waiting = [];
	let watching = null;
	const post = (body, retry) => {
		try {
			fetch("/api/method/pearnly_core.desk.client_errors.report", {
				method: "POST",
				credentials: "same-origin",
				keepalive: true,
				headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": token() },
				body,
			}).then((response) => {
				if (retry && (response.status === 400 || response.status === 403) && !token()) wait(body);
			}).catch(() => {});
		} catch (e) {
			// 回传本身失败不影响页面
		}
	};
	const wait = (body) => {
		waiting.push(body);
		if (watching) return;
		let left = 60;
		watching = setInterval(() => {
			left -= 1;
			if (!token() && left > 0) return;
			clearInterval(watching);
			watching = null;
			if (token()) waiting.splice(0).forEach((item) => post(item, false));
			else waiting.length = 0;
		}, 1000);
	};
	const send = (payload) => {
		const key = [payload.message, payload.source, payload.line].join("|");
		if (!budget || sent.has(key)) return;
		sent.add(key);
		budget -= 1;
		post(JSON.stringify({ ...payload, route: location.pathname }), true);
	};
	window.pearnlyReportError = (error, kind) => {
		if (!(error instanceof Error)) return;
		send({ kind: String(kind || "error"), message: String(error.message || ""), source: "", line: 0, column: 0, stack: String(error.stack || "") });
	};
	// 统一出错回传（吞错闸门，主会话 10-28）：代码里接住的错误一律交这里。请求被站点拒收 / 网络失败（带状态码、`exc_type`、
	// 站点给的提示）服务端已经记过、页面也已提示，不再回传；用户取消（AbortError）不算错。其余是前端自己的异常，记进 Error Log。
	// `scripts/check-swallowed-errors.py` 要求每个 catch 重抛，或调这里（`pearnlyCaught` / `pearnlyReported`）。
	const request_failure = (error) => Boolean(error && (Number(error.status) || error.exc_type || error.excType
		|| error._server_messages || error.responseJSON || ["SiteError", "RequestError", "ApiError", "AbortError"].includes(error.name)));
	window.pearnlyCaught = (error, kind) => {
		if (!request_failure(error)) window.pearnlyReportError(error, kind);
	};
	// `.catch(window.pearnlyReported(原来的处理, "哪里"))`：先回传，再照原来的处理（函数就调用、其它值原样返回）
	window.pearnlyReported = (fallback, kind) => (error) => {
		window.pearnlyCaught(error, kind);
		return typeof fallback === "function" ? fallback(error) : fallback;
	};
	window.addEventListener("error", (event) => {
		if (!event || (!event.message && !event.error)) return; // 资源加载失败（图片等）不算
		send({
			kind: "error",
			message: String(event.message || (event.error && event.error.message) || ""),
			source: String(event.filename || ""),
			line: event.lineno || 0,
			column: event.colno || 0,
			stack: String((event.error && event.error.stack) || ""),
		});
	});
	window.addEventListener("unhandledrejection", (event) => {
		const reason = event && event.reason;
		// 只收脚本自己抛的 Error（带堆栈）：frappe.call 失败（框架已提示、服务端已记）、框架拿字符串当流程控制的拒绝
		// （如报表缺必填筛选时的 "Filter missing"）都不算
		if (!(reason instanceof Error) || !reason.stack) return;
		send({
			kind: "rejection",
			message: String((reason && reason.message) || reason || ""),
			source: "",
			line: 0,
			column: 0,
			stack: String((reason && reason.stack) || ""),
		});
	});
})();
