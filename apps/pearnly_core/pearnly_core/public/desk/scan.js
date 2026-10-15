/* 后台扫码的 Frappe 那一层：扫码画面、读到时的反馈与结果音、按原因的提示。
 * 开相机与读码本身在共用读码器 `pearnly_core/public/scan/reader.js`（`window.PearnlyScan`，不依赖 Frappe），
 * LINE 盘点页用的也是那一份（2026-10-15 合并成一份）；这里只把它接到 Desk。
 *
 * 共用层的扫码器（浏览器能力，多产品共用）：2026-09-26 从 ERP 搬到这里，ERP、Cowork 都用这一份。
 * 只属于 ERP 的部分（扫码弹窗 `p.scan` 与物料选择框扫码）留在 ERP 的 `public/desk/scan.js`。
 */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	const scan = () => window.PearnlyScan;
	p.scan_kind = format => scan().kind(format);
	p.scan_ready = () => Promise.resolve();
	p.scan_supported = () => Boolean(navigator.mediaDevices?.getUserMedia);
	p.scan_stage = () => `<div class="pearnly-scan-stage">
		<video class="pearnly-scan-video" playsinline muted autoplay></video>
		<span class="pearnly-scan-flash" aria-hidden="true"></span>
		<span class="pearnly-scan-check" aria-hidden="true">${frappe.utils.icon("es-line-success", "md")}</span>
		</div>`;
	// "读到了"这一刻的反馈：**只做不像结果的那一部分** —— 闪一下、对勾、震一下。
	// 音效**不在这里**：官方 POS 是"真的进单"才响一声（`pos_item_selector.js` 里只有命中
	// 那一条路径 `play_sound("submit")`），读到就响会变成"一件事两声"（用户 2026-09-23 的
	// 口径）。结果那一声由调用方在**成了 / 没成**的时刻放，见下面的 `p.scan_result_sound`。
	p.scan_feedback = host => {
		// 震动也算提示：账户静音就一起停。判断跟音效同一个开关（`p.sounds_muted`）。
		if (!p.sounds_muted?.()) navigator.vibrate?.(60);
		$(host).find(".pearnly-scan-stage").addClass("pearnly-scan-done");
	};
	// 结果那一声：和官方 POS 完全一致的两个音（命中 `submit`、没命中 `error`），走官方的
	// `play_sound` —— 所以账户静音时它自己就不响了，不用在这儿再判一次。
	p.scan_result_sound = ok => {
		try { frappe.utils.play_sound(ok ? "submit" : "error"); } catch (_error) { /* Muted browser. */ }
	};
	// 相机打不开的原因（共用读码器分类），提示文案在 core 术语表 `common.scan.*`（LINE 盘点页同一份）。
	p.scan_failure = error => scan().reason(error);
	const FAILURE = {
		unsupported: () => __("common.scan.unsupported"), in_app: () => __("common.scan.in_app"),
		in_line: () => __("common.scan.in_line"),
		denied: () => __("common.scan.denied"), busy: () => __("common.scan.busy"),
		missing: () => __("common.scan.missing"), decoder: () => __("common.scan.decoder"),
		failed: () => __("common.scan.failed"),
	};
	p.scan_failure_text = error => (FAILURE[p.scan_failure(error)] || FAILURE.failed)();
	// 提示那一句放进 `$target`；在 LINE 里再给一颗「用浏览器打开」（LINE 自己的参数，跳到手机默认浏览器，那里相机正常）。
	// `reason` 可以是错误对象，也可以是读码器 `on_waiting` 给的原因字符串。
	p.scan_hint = ($target, reason) => {
		const key = typeof reason === "string" ? reason : p.scan_failure(reason);
		$target.empty().append($("<p class='text-muted'>").text((FAILURE[key] || FAILURE.failed)()));
		if (key === "in_line") {
			$("<button type='button' class='btn btn-primary btn-sm'>").text(__("common.scan.open_browser"))
				.on("click", () => scan().open_in_browser()).appendTo($target);
		}
	};
	p.scan_reader = ({ host, kind = "all", on_result, on_error, on_waiting, on_started }) => {
		const stage = () => $(host).find(".pearnly-scan-stage");
		const reader = scan().reader({ video: $(host).find("video").get(0), kind, on_result, on_error, on_waiting, on_started,
			on_detected: () => p.scan_feedback(host),
			on_play_blocked: play => stage().one("click", play) });
		return { ...reader, resume() { reader.resume(); stage().removeClass("pearnly-scan-done"); } };
	};
})();
