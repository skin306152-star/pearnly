/* Shared local camera reader. Native decoder first, bundled production WASM fallback.
 * Dialogs remain Frappe native; the video surface is necessarily browser media API.
 *
 * 共用层的扫码器（浏览器能力，多产品共用）：2026-09-26 从 ERP `public/desk/scan.js` 原样复制过来给
 * Cowork 盘点用，只改了解码器资源路径，并去掉只属于 ERP 的部分（扫码弹窗 `p.scan` 与物料选择框扫码）。ERP 仍加载它自己那份（内容相同，后加载者生效），
 * 待与 ERP 窗口协调后由 ERP 删掉自己的副本、改用这里。
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
	p.scan_reader = ({ host, kind = "all", on_result, on_error }) => {
		const video = $(host).find("video").get(0);
		let stream, reader, timer, stopped = false, paused = false, detecting = false;
		let candidate = "", candidate_format = "", hits = 0;
		const stop = () => {
			stopped = true; clearTimeout(timer);
			stream?.getTracks().forEach(track => track.stop()); stream = null;
			video.srcObject = null;
		};
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
				try {
					reader = await decoder(kind);
					if (stopped) return;
					if (!navigator.mediaDevices?.getUserMedia) throw new Error("camera_unavailable");
					const acquired = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
					if (stopped) { acquired.getTracks().forEach(track => track.stop()); return; }
					stream = acquired; video.srcObject = stream; video.muted = true;
					await video.play(); void tick();
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
