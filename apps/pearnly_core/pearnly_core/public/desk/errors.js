// 页面 JS 报错回传（共用件连带回归闸门，2026-10-25）：Desk 页面上的报错与未处理的 Promise 拒绝发给
// `pearnly_core.desk.client_errors.report`，记进本站 Error Log，部署后回读与冒烟按它查。
// 最先加载（`hooks.app_include_js` 第一个），后面的共用组件出错也抓得到。每页最多发 10 条，同一条只发一次；
// 只发报错原文、文件、行列、堆栈和路由路径（服务端再去个人信息、限频）。
(() => {
	const sent = new Set();
	let budget = 10;
	const send = (payload) => {
		const key = [payload.message, payload.source, payload.line].join("|");
		if (!budget || sent.has(key)) return;
		sent.add(key);
		budget -= 1;
		try {
			fetch("/api/method/pearnly_core.desk.client_errors.report", {
				method: "POST",
				credentials: "same-origin",
				keepalive: true,
				headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": (window.frappe && frappe.csrf_token) || "" },
				body: JSON.stringify({ ...payload, route: location.pathname }),
			}).catch(() => {});
		} catch (e) {
			// 回传本身失败不影响页面
		}
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
		// frappe.call 失败时框架已经弹原生提示、服务端已记日志；这里只收脚本自己的异常
		if (!reason || (reason.exc_type && reason.exc)) return;
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
