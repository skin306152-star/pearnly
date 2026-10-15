/* 共用读码器：开相机 → 每 0.2 秒读一帧 → 连续两帧同一个码才算数。不依赖 Frappe。
 *
 * 用的地方（2026-10-15 用户："不都是一样的原理和使用方式吗"——一份逻辑，各页只留自己的界面）：
 *   · 后台扫码（ERP / Cowork 盘点录入、ERP 扫码弹窗）：`pearnly_core/public/desk/scan.js` 包一层 Frappe 界面；
 *   · LINE 盘点页（ERP `www/stocktake.html`，在 LINE 里打开）：直接用。
 * 改相机 / 读码 / 出错分类只改这里；提示文案在 core 术语表 `common.scan.*`，两边同一份。
 *
 * 读码：浏览器自带 BarcodeDetector 优先（要的格式它都支持才用），否则同源打包的 ZXing WASM。
 * 相机：先要相机、读码器并行加载（授权弹窗立刻出来；读码器坏了不说成相机的事）。指定后置镜头
 * 起不来的机型（多摄像头、旧驱动）按名字挑后置再开，再不行要系统默认镜头；权限被拒不重试。
 */
(() => {
	const WANTED = {
		barcode: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "codabar"],
		qr: ["qr_code"],
	};
	WANTED.all = [...WANTED.barcode, ...WANTED.qr];
	const ASSETS = "/assets/pearnly_core/vendor/barcode-detector/";
	// Classify by the decoder's symbology, never by the contents (QR may be numeric).
	const kind_of = format => format === "qr_code" ? "qr" : WANTED.barcode.includes(format) ? "barcode" : null;
	const IN_APP = /\bLine\/|FBAN|FBAV|Instagram|MicroMessenger|TikTok|; wv\)/i;
	const in_app = () => IN_APP.test((typeof navigator !== "undefined" && navigator.userAgent) || "");
	const failure = (reason, cause) => Object.assign(new Error(reason), { scan_reason: reason, cause });

	let fallback;
	function load_fallback() {
		if (!fallback) fallback = (async () => {
			if (!window.BarcodeDetectionAPI) await new Promise((resolve, reject) => {
				const script = document.createElement("script");
				script.src = ASSETS + "ponyfill.js";
				script.onload = resolve;
				script.onerror = () => { script.parentNode?.removeChild(script); reject(new Error("decoder_unavailable")); };
				document.head.appendChild(script);
			});
			const api = window.BarcodeDetectionAPI;
			await api.prepareZXingModule({ overrides: {
				locateFile: name => name.endsWith(".wasm") ? ASSETS + "zxing_reader.wasm" : name,
			} });
			return api.BarcodeDetector;
		})().catch(error => { fallback = null; throw error; });
		return fallback;
	}
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
		const Reader = await load_fallback();
		return new Reader({ formats });
	}

	// 相机打不开的原因：浏览器给的错误名归成几类，页面按类给对应那句提示。
	function reason(error) {
		if (error?.scan_reason) return error.scan_reason;
		const name = error?.name || "";
		if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
			return in_app() ? "in_app" : "denied";
		}
		if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") return "missing";
		if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") return "busy";
		return "failed";
	}

	async function camera() {
		const media = navigator.mediaDevices;
		if (!window.isSecureContext || !media?.getUserMedia) throw failure(in_app() ? "in_app" : "unsupported");
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

	// `video`：页面自己的 <video>。`on_result(code, {format, kind})` 读到一个确认过的码（读码随即暂停，
	// 页面要么 `resume()` 接着读，要么 `stop()`）；`on_detected()` 是"读到了"那一刻（给页面闪一下）；
	// `on_play_blocked()` 浏览器拦了自动播放（页面让人点一下画面再 `play()`）；`on_error(error)` 打不开。
	function reader({ video, kind = "all", on_result, on_error, on_detected, on_play_blocked }) {
		let stream, decode, timer, stopped = false, paused = false, detecting = false;
		let candidate = "", candidate_format = "", hits = 0;
		const stop = () => {
			stopped = true; window.clearTimeout(timer);
			stream?.getTracks().forEach(track => track.stop()); stream = null;
			video.srcObject = null;
		};
		const play = () => video.play();
		async function start_playing() {
			try { await play(); } catch (error) {
				// 被新的加载打断就再播一次；被拦了自动播放就等人点一下，相机本身已经开着，不算失败。
				if (error?.name === "AbortError") { await play().catch(() => {}); return; }
				on_play_blocked?.(() => play().catch(() => {}));
			}
		}
		async function tick() {
			if (stopped) return;
			try {
				if (!paused && !detecting && video.readyState >= 2) {
					detecting = true;
					const result = await decode.detect(video);
					if (stopped || paused) return;
					// QR content is exact text: internal whitespace must not be stripped.
					const code = String(result[0]?.rawValue || "").trim();
					const format = result[0]?.format || "";
					hits = code && code === candidate && format === candidate_format ? hits + 1 : (code ? 1 : 0);
					candidate = code; candidate_format = format;
					if (code && hits >= 2) {
						paused = true;
						on_detected?.();
						await on_result(code, { format, kind: kind_of(format) });
					}
				}
			} catch (_error) { /* 单帧读不出来是常态，下一帧再读。 */ }
			finally {
				detecting = false;
				if (!stopped) timer = window.setTimeout(tick, 200);
			}
		}
		return {
			async start() {
				// 让同一轮里紧跟着的 stop()（连点两次扫码、刚开就关）先生效：没开相机就不去要权限。
				await Promise.resolve();
				if (stopped) return;
				const reading = decoder(kind).catch(error => { throw failure("decoder", error); });
				reading.catch(() => {});
				try {
					const acquired = await camera();
					if (stopped) { acquired.getTracks().forEach(track => track.stop()); return; }
					stream = acquired; video.srcObject = stream; video.muted = true;
					await start_playing();
					decode = await reading;
					if (stopped) return;
					void tick();
				} catch (error) { stop(); on_error?.(error); }
			},
			pause() { paused = true; },
			resume() { candidate = ""; candidate_format = ""; hits = 0; paused = false; },
			has_torch() { return Boolean(stream?.getVideoTracks?.()[0]?.getCapabilities?.().torch); },
			async torch(on) {
				const track = stream?.getVideoTracks?.()[0];
				if (track?.getCapabilities?.().torch) await track.applyConstraints({ advanced: [{ torch: on }] });
			},
			stop,
		};
	}

	window.PearnlyScan = { formats: WANTED, kind: kind_of, reason, reader };
})();
