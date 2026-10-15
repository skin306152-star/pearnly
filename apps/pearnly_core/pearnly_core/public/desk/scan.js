/* Shared local camera reader. Native decoder first, bundled production WASM fallback.
 * Dialogs remain Frappe native; the video surface is necessarily browser media API.
 *
 * 共用层的扫码器（浏览器能力，多产品共用）：2026-09-26 从 ERP 搬到这里，ERP、Cowork 都用这一份。
 * 只属于 ERP 的部分（扫码弹窗 `p.scan` 与物料选择框扫码）留在 ERP 的 `public/desk/scan.js`。
 */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;
	const WANTED = {
		barcode: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "codabar"],
		qr: ["qr_code"],
	};
	WANTED.all = [...WANTED.barcode, ...WANTED.qr];
	// Classify by the decoder's symbology, never by the contents (QR may be numeric).
	p.scan_kind = format => format === "qr_code" ? "qr"
		: WANTED.barcode.includes(format) ? "barcode" : null;
	let fallback;
	async function decoder(kind) {
		const formats = WANTED[kind] || WANTED.all;
		if (typeof window.BarcodeDetector === "function") {
			try {
				const supported = await window.BarcodeDetector.getSupportedFormats();
				if (formats.every(format => supported.includes(format))) {
					return new window.BarcodeDetector({ formats });
				}
			} catch (_error) { /* Use the same-origin decoder. */ }
		}
		if (!fallback) fallback = (async () => {
			await frappe.require("/assets/pearnly_core/vendor/barcode-detector/ponyfill.js");
			const api = window.BarcodeDetectionAPI;
			await api.prepareZXingModule({ overrides: {
				locateFile: name => name.endsWith(".wasm")
					? "/assets/pearnly_core/vendor/barcode-detector/zxing_reader.wasm" : name,
			} });
			return api.BarcodeDetector;
		})().catch(error => { fallback = null; throw error; });
		const Reader = await fallback;
		return new Reader({ formats });
	}
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
	// 相机打不开的原因：浏览器给的错误名归成几类，调用方用 `p.scan_failure_text` 给对应那句提示
	// （以前一律说"请在 HTTPS 页面允许"，安卓上被拒过权限、被别的应用占着都看不出该去哪儿改）。
	const failure = (reason, cause) => Object.assign(new Error(reason), { scan_reason: reason, cause });
	const IN_APP = /\bLine\/|FBAN|FBAV|Instagram|MicroMessenger|TikTok|; wv\)/i;
	p.scan_failure = error => {
		if (error?.scan_reason) return error.scan_reason;
		const name = error?.name || "";
		if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
			return IN_APP.test(navigator.userAgent || "") ? "in_app" : "denied";
		}
		if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") return "missing";
		if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") return "busy";
		return "failed";
	};
	p.scan_failure_text = error => ({
		unsupported: __("This browser cannot use the camera. Open this page in Chrome or Safari, or enter the code."),
		in_app: __("This app's built-in browser cannot use the camera. Open this page in Chrome or Safari, or enter the code."),
		denied: __("Camera access is blocked. Allow the camera for this site in the browser settings and in the phone settings, then try again."),
		busy: __("The camera is being used by another app. Close that app and try again."),
		missing: __("No camera was found on this device. Enter the code instead."),
		decoder: __("The barcode reader could not load. Refresh the page and try again."),
		failed: __("The camera could not start. Try again, or enter the code."),
	})[p.scan_failure(error)];
	// 后置镜头优先。有的安卓机型"指定后置"本身就起不来（多摄像头、旧驱动）：
	// 换成按名字挑后置那一个，再不行就让系统给默认镜头；权限被拒之类的错误不重试。
	async function camera() {
		const media = navigator.mediaDevices;
		if (!window.isSecureContext || !media?.getUserMedia) {
			throw failure(IN_APP.test(navigator.userAgent || "") ? "in_app" : "unsupported");
		}
		try {
			return await media.getUserMedia({ video: { facingMode: { ideal: "environment" } } });
		} catch (error) {
			if (!["OverconstrainedError", "NotReadableError", "AbortError", "NotFoundError"].includes(error?.name)) throw error;
			const cameras = (await media.enumerateDevices().catch(() => [])).filter(device => device.kind === "videoinput");
			const back = cameras.find(device => /back|rear|environment|后|後|หลัง/i.test(device.label || ""));
			for (const video of [back && { deviceId: { exact: back.deviceId } }, true].filter(Boolean)) {
				try { return await media.getUserMedia({ video }); } catch (_retry) { /* Next fallback. */ }
			}
			throw error;
		}
	}
	p.scan_reader = ({ host, kind = "all", on_result, on_error }) => {
		const video = $(host).find("video").get(0);
		let stream, reader, timer, stopped = false, paused = false, detecting = false;
		let candidate = "", candidate_format = "", hits = 0;
		const stop = () => {
			stopped = true; clearTimeout(timer);
			stream?.getTracks().forEach(track => track.stop()); stream = null;
			video.srcObject = null;
		};
		// 画面要真的播起来才读得到帧。被新的加载打断（AbortError）就再播一次；
		// 被浏览器拦了自动播放就等人点一下画面再播，相机本身已经开着，不算失败。
		async function play() {
			try { await video.play(); } catch (error) {
				if (error?.name === "AbortError") { await video.play().catch(() => {}); return; }
				$(host).find(".pearnly-scan-stage").one("click", () => void video.play().catch(() => {}));
			}
		}
		async function tick() {
			if (stopped) return;
			try {
				if (!paused && !detecting && video.readyState >= 2) {
					detecting = true;
					const result = await reader.detect(video);
					if (stopped || paused) return;
					// QR content is exact text: internal whitespace must not be stripped.
					const code = String(result[0]?.rawValue || "").trim();
					const format = result[0]?.format || "";
					hits = code && code === candidate && format === candidate_format ? hits + 1 : (code ? 1 : 0);
					candidate = code; candidate_format = format;
					if (code && hits >= 2) {
						paused = true;
						p.scan_feedback(host);
						await on_result(code, { format, kind: p.scan_kind(format) });
					}
				}
			} catch (error) { if (!stopped) on_error?.(error); }
			finally {
				detecting = false;
				if (!stopped) timer = setTimeout(tick, 200);
			}
		}
		return {
			async start() {
				// 先要相机、同时加载识别器：授权弹窗立刻出来，识别器慢或坏也不会被当成"相机打不开"。
				const reading = decoder(kind).catch(error => { throw failure("decoder", error); });
				reading.catch(() => {});
				try {
					const acquired = await camera();
					if (stopped) { acquired.getTracks().forEach(track => track.stop()); return; }
					stream = acquired; video.srcObject = stream; video.muted = true;
					await play();
					reader = await reading;
					if (stopped) return;
					void tick();
				} catch (error) { stop(); on_error?.(error); }
			},
			pause() { paused = true; },
			has_torch() { return Boolean(stream?.getVideoTracks()[0]?.getCapabilities?.().torch); },
			resume() {
				candidate = ""; candidate_format = ""; hits = 0; paused = false;
				$(host).find(".pearnly-scan-stage").removeClass("pearnly-scan-done");
			},
			async torch(on) {
				const track = stream?.getVideoTracks()[0];
				if (track?.getCapabilities?.().torch) await track.applyConstraints({ advanced: [{ torch: on }] });
			},
			stop,
		};
	};
})();
