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
	// 第 ③ 步的文案按所在环境给设置路径（读码器 `text_key`：LINE 安卓 / LINE iPhone / 手机浏览器 / iPhone Safari / 电脑）。
	const TEXT = {
		unsupported: () => __("common.scan.unsupported"), in_app: () => __("common.scan.in_app"),
		denied: () => __("common.scan.denied"), denied_android: () => __("common.scan.denied_android"),
		denied_ios: () => __("common.scan.denied_ios"), in_line_android: () => __("common.scan.in_line_android"),
		in_line_ios: () => __("common.scan.in_line_ios"), busy: () => __("common.scan.busy"),
		missing: () => __("common.scan.missing"), decoder: () => __("common.scan.decoder"),
		failed: () => __("common.scan.failed"),
	};
	const text_of = reason => (TEXT[scan().text_key(reason)] || TEXT.failed)();
	p.scan_failure_text = error => text_of(p.scan_failure(error));
	// 原因那一句放进 `$target`；`reason` 可以是错误对象，也可以是读码器 `on_waiting` 给的原因字符串。
	// 要去设置里开权限的（③）：人从设置切回来（页面重新可见）就调 `retry` 自动重开，只等这一次。
	p.scan_hint = ($target, reason, retry) => {
		const key = typeof reason === "string" ? reason : p.scan_failure(reason);
		$target.empty().append($("<p class='text-muted'>").text(text_of(key)));
		if (!retry || !scan().needs_settings(key)) return;
		const back = () => {
			if (document.visibilityState !== "visible") return;
			document.removeEventListener("visibilitychange", back);
			if ($target[0]?.isConnected) retry();
		};
		document.addEventListener("visibilitychange", back);
	};
	// 在 LINE 普通内置浏览器里打不开相机：转到 LIFF 打开同一页（LINE 会弹原生相机授权）。转了返回 true。
	// 只给没有未保存内容的页面用（盘点）；表单里的扫码转走会丢掉没保存的内容，那里只给原因。
	p.scan_to_liff = keep_scanning => scan().to_liff(frappe.boot.pearnly_liff_id, keep_scanning);
	// 安卓 LINE 普通内置浏览器：那里网页拿不到相机，要相机前就转（返回 true 表示已经转走）。
	p.scan_liff_first = keep_scanning => scan().plain_line() && p.scan_to_liff(keep_scanning);
	// 上传器里的「拍照」（框架 `frappe.ui.Capture`）：手机上它调手机自己的相机应用，授权由系统 / LINE 原生处理；
	// 电脑上它在网页里开相机，失败只弹一句笼统的话 —— 换成同一套第 ③ 步提示。其余行为与官方一致。
	if (frappe.ui?.Capture?.prototype?.show_for_desktop) {
		frappe.ui.Capture.prototype.show_for_desktop = function () {
			return this.render_stream().then(() => this.dialog.show())
				.catch(error => frappe.msgprint({ message: p.scan_failure_text(error), indicator: "orange" }));
		};
	}
	p.scan_reader = ({ host, kind = "all", on_result, on_error, on_waiting, on_started }) => {
		const stage = () => $(host).find(".pearnly-scan-stage");
		const reader = scan().reader({ video: $(host).find("video").get(0), kind, on_result, on_error, on_waiting, on_started,
			on_detected: () => p.scan_feedback(host),
			on_play_blocked: play => stage().one("click", play) });
		return { ...reader, resume() { reader.resume(); stage().removeClass("pearnly-scan-done"); } };
	};
})();
