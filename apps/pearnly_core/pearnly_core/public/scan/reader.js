/* 共用读码器：开相机 → 每 0.2 秒读一帧 → 连续两帧同一个码才算数。不依赖 Frappe。
 *
 * 用的地方（2026-10-15 用户："不都是一样的原理和使用方式吗"——一份逻辑，各页只留自己的界面）：
 *   · 后台扫码（ERP / Cowork 盘点录入、ERP 扫码弹窗）：`pearnly_core/public/desk/scan.js` 包一层 Frappe 界面；
 *   · LINE 盘点页（ERP `www/stocktake.html`，在 LINE 里打开）：直接用。
 * 改相机 / 读码 / 出错分类只改这里；提示文案在 core 术语表 `common.scan.*`，两边同一份。
 *
 * 读码：浏览器自带 BarcodeDetector 优先（主读的格式它都支持才用），否则同源打包的 ZXing WASM。
 * 码制（主会话 10-30，一次覆盖常见的全部）：一维 EAN / UPC / Code 128 / 39 / 93 / ITF / Codabar / GS1 DataBar（含扩展型），
 * 二维 QR / Data Matrix / PDF417 / Aztec。分几路读，原来那些码读得一样快（三星 A14 1080p 真实镜头实测，见 docs/work/2026-10-30-scan-symbologies.md）：
 *   · 主读：一维 + QR（原生能读的二维也并进来，原生多读三种二维只多约 20 ms）——每帧都读，耗时与原来一样；
 *   · 补读（WASM，只在主读这一帧什么都没读到、手上也没有待确认的码时，轮流跑一路）：二维（原生不支持时，缩到一半分辨率读，
 *     二维码在画面里都很大）、DataBar（原生都不支持，全分辨率）。读到的那一路下一帧先读，接着确认。
 * 相机：先要相机、读码器并行加载（授权弹窗立刻出来；读码器坏了不说成相机的事）。指定后置镜头
 * 起不来的机型（多摄像头、旧驱动）按名字挑后置再开，再不行要系统默认镜头；权限被拒不重试。
 */
(() => {
	const LINEAR = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "code_93", "itf", "codabar"];
	// 主读用原生的条件：原来那些码（10-30 之前的九种）它都能读。少了新加的哪一种（如 Code 93）就交给补读，不因此整个退到 WASM
	const CORE = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "codabar", "qr_code"];
	const DATABAR = ["databar", "databar_expanded", "databar_limited"];
	const MATRIX = ["data_matrix", "pdf417", "aztec"];
	const WANTED = {
		barcode: [...LINEAR, ...DATABAR],
		qr: ["qr_code", ...MATRIX],
	};
	WANTED.all = [...WANTED.barcode, ...WANTED.qr];
	const ASSETS = "/assets/pearnly_core/vendor/barcode-detector/";
	// Classify by the decoder's symbology, never by the contents (QR may be numeric). 二维码都归「qr」一类（同一列存）；
	// DataBar 的各种写法（WASM 报 databar_omni 等）都是一维。
	const kind_of = format => WANTED.qr.includes(format) ? "qr"
		: WANTED.barcode.includes(format) || String(format).startsWith("databar") ? "barcode" : null;
	const IN_APP = /\bLine\/|FBAN|FBAV|Instagram|MicroMessenger|TikTok|; wv\)/i;
	const agent = () => (typeof navigator !== "undefined" && navigator.userAgent) || "";
	const in_app = () => IN_APP.test(agent());
	const in_line = () => /\bLine\//.test(agent());
	// LINE 的普通内置浏览器（聊天、菜单里点链接打开）：带 `Line/<版本>`、不带 `LIFF`（安卓结尾是 `/IAB`）；LIFF 是 `Line/<版本> LIFF`。
	// 安卓上网页在里面永远拿不到相机（LINE 有相机权限也黑屏，2026-10-15 三星 A14 实测）；iPhone 上 10-15 只是推定能用、没有真机验过，
	// 2026-10-23 用户 iPhone 实测也打不开。LIFF 里两边都会弹原生授权（iPhone：10-04 DMS 扫身份证真机；安卓：10-15）。
	// 所以 LINE 里不在 LIFF 就一律先转 LIFF，不先去要、也不等。LINE 链接的总入口（`line_login.enter`）同一判断、进来就转。
	const plain_line = () => /\bLine\/[\d.]+/.test(agent()) && !/\bLIFF\b/.test(agent());
	// App 内置浏览器（LINE 等）里要相机：请求可能既不弹窗也不报错，一直挂着
	// （2026-10-15 三星 A14：LINE 普通内置浏览器里点扫码没有任何反应）。等这么久还没开就交给页面处理。
	const SLOW_MS = 5000;
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
	// 半分辨率帧（二维补读用）：画到一块复用的画布上
	let half_canvas;
	function half(video) {
		half_canvas ||= document.createElement("canvas");
		half_canvas.width = video.videoWidth >> 1; half_canvas.height = video.videoHeight >> 1;
		half_canvas.getContext("2d").drawImage(video, 0, 0, half_canvas.width, half_canvas.height);
		return half_canvas;
	}
	// 读这一类码要跑的几路：`[{detect(video), formats}]`，第一路是主读、每帧都跑；其余是补读（WASM），主读没读到时轮流跑。
	async function decoder(kind) {
		const formats = WANTED[kind] || WANTED.all;
		const main = formats.filter(format => !MATRIX.includes(format) && !DATABAR.includes(format));
		let supported = [];
		if (typeof window.BarcodeDetector === "function") {
			try { supported = await window.BarcodeDetector.getSupportedFormats(); } catch (_error) { /* Use the same-origin decoder. */ }
		}
		const core = main.filter(format => CORE.includes(format));
		const native = core.length > 0 && core.every(format => supported.includes(format));
		const primary = native ? formats.filter(format => supported.includes(format)) : main;
		const rest = formats.filter(format => !primary.includes(format));
		const passes = [];
		const wasm = () => load_fallback();
		if (native) {
			const detector = new window.BarcodeDetector({ formats: primary });
			passes.push({ formats: primary, detect: video => detector.detect(video) });
		} else if (primary.length) {
			const Reader = await wasm();
			const detector = new Reader({ formats: primary });
			passes.push({ formats: primary, detect: video => detector.detect(video) });
		}
		// 补读：WASM 在后台加载，加载好之前不跑（主读照常）；加载不了也不影响主读
		// 二维缩一半读；DataBar 与原生读不了的一维（如 Code 93）全分辨率读
		for (const [group, scale] of [[MATRIX, 0.5], [[...DATABAR, ...LINEAR], 1]]) {
			const wanted = rest.filter(format => group.includes(format));
			if (!wanted.length) continue;
			const pass = { formats: wanted, detect: null };
			wasm().then(Reader => {
				const detector = new Reader({ formats: wanted });
				pass.detect = video => detector.detect(scale < 1 ? half(video) : video);
			}).catch((caughtError) => window.PearnlyScan.report(caughtError, "reader"));
			passes.push(pass);
		}
		if (!passes.length) throw new Error("decoder_unavailable");
		return passes;
	}

	// 相机打不开的原因：浏览器给的错误名归成几类，页面按类给对应那句提示。
	function reason(error) {
		if (error?.scan_reason) return error.scan_reason;
		const name = error?.name || "";
		if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
			return in_line() ? "in_line" : in_app() ? "in_app" : "denied";
		}
		if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") return "missing";
		if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") return "busy";
		return "failed";
	}

	// 上报只给真正的异常（11-05 主会话按类定）：权限被拒、LINE / App 里打不开、没有相机、相机被占用、浏览器不支持，
	// 都是使用环境的正常情况，页面已按原因给提示，不进站点 Error Log；读码器加载失败与没归类的异常照常上报。
// 扫码的 catch 一律写 `window.PearnlyScan.report(...)`：吞错闸门只认这个名字，并核对下面的定义仍是「预期原因跳过、其余交 pearnlyCaught」。
	const EXPECTED = new Set(["denied", "in_line", "in_app", "missing", "busy", "unsupported"]);
	function report(error, kind) {
		if (EXPECTED.has(reason(error))) return;
		globalThis.pearnlyCaught?.(error, kind);
	}

	// 画面要清楚：只要「后置镜头」时很多手机给 640×480（2026-10-23 双摄 iPhone 离远了条码只剩几十个像素读不出，
	// 离近了又对不上焦），所以要 1080p（ideal，给不了就给能给的，不会因此打不开）。能连续自动对焦的（安卓 Chrome）打开它。
	const SHARP = { width: { ideal: 1920 }, height: { ideal: 1080 } };
	async function focus(stream) {
		const track = stream?.getVideoTracks?.()[0];
		const modes = track?.getCapabilities?.().focusMode || [];
		if (modes.includes("continuous")) await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(() => {});
		return stream;
	}
	async function camera() {
		const media = navigator.mediaDevices;
		if (!window.isSecureContext || !media?.getUserMedia) throw failure(in_line() ? "in_line" : in_app() ? "in_app" : "unsupported");
		try {
			return await focus(await media.getUserMedia({ video: { facingMode: { ideal: "environment" }, ...SHARP } }));
		} catch (error) {
			if (!["OverconstrainedError", "NotReadableError", "AbortError", "NotFoundError"].includes(error?.name)) throw error;
			const cameras = (await media.enumerateDevices().catch(() => [])).filter(device => device.kind === "videoinput");
			const back = cameras.find(device => /back|rear|environment|后|後|หลัง/i.test(device.label || ""));
			for (const video of [back && { deviceId: { exact: back.deviceId }, ...SHARP }, true].filter(Boolean)) {
				try { return await focus(await media.getUserMedia({ video })); } catch (_retry) { /* Next fallback. */ }
			}
			throw error;
		}
	}

	// `video`：页面自己的 <video>。`on_result(code, {format, kind})` 读到一个确认过的码（读码随即暂停，
	// 页面要么 `resume()` 接着读，要么 `stop()`；`resume(code)` 接着读但先**跳过这个码**——页面刚说它不行，
	// 镜头还对着它就不再交回去，读到别的码才放开，免得同一个码被反复读、反复查）；`on_detected()` 是"读到了"那一刻（给页面闪一下）；
	// `on_play_blocked()` 浏览器拦了自动播放（页面让人点一下画面再 `play()`）；`on_error(error)` 打不开。
	// `on_waiting(reason)`：App 内置浏览器里相机 5 秒还没开（请求被挂着），读码器继续等，页面决定怎么办
	// （盘点页在 LINE 里转 LIFF）；`on_started()`：相机真的开了。
	function reader({ video, kind = "all", on_result, on_error, on_detected, on_play_blocked, on_waiting, on_started }) {
		let stream, passes, timer, stopped = false, paused = false, detecting = false;
		let candidate = "", candidate_format = "", hits = 0, skip = "";
		// focus：手上那个待确认的码是哪一路读出来的，下一帧先跑那一路；extra：补读轮到哪一路
		let focus = null, extra = 0;
		// 这一帧跑哪几路：有待确认的码只跑读出它的那一路；没有就主读，主读读不到再补读一路（轮流）
		async function read_frame() {
			const order = focus !== null ? [focus] : [0];
			if (focus === null && passes.length > 1) {
				const ready = passes.map((_pass, index) => index).filter(index => index > 0 && passes[index].detect);
				if (ready.length) order.push(ready[extra++ % ready.length]);
			}
			for (const index of order) {
				if (!passes[index]?.detect) continue;
				const result = await passes[index].detect(video);
				if (result[0]?.rawValue) return { result, index };
			}
			return { result: [], index: null };
		}
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
					const { result, index } = await read_frame();
					if (stopped || paused) return;
					// QR content is exact text: internal whitespace must not be stripped.
					let code = String(result[0]?.rawValue || "").trim();
					const format = result[0]?.format || "";
					if (code && code === skip) code = "";
					else if (code) skip = "";
					hits = code && code === candidate && format === candidate_format ? hits + 1 : (code ? 1 : 0);
					candidate = code; candidate_format = format; focus = code ? index : null;
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
				reading.catch((caughtError) => window.PearnlyScan.report(caughtError, "reader"));
				// LIFF 里请求会弹原生授权（网页的、再到系统给 LINE 的），人还在看授权窗时不能抢先提示，所以不计时。
				const slow = in_app() && !/\bLIFF\b/.test(agent()) ? window.setTimeout(() => {
					if (!stopped && !stream) on_waiting?.(in_line() ? "in_line" : "in_app");
				}, SLOW_MS) : null;
				try {
					const acquired = await camera();
					window.clearTimeout(slow);
					if (stopped) { acquired.getTracks().forEach(track => track.stop()); return; }
					stream = acquired; video.srcObject = stream; video.muted = true;
					on_started?.();
					await start_playing();
					passes = await reading;
					if (stopped) return;
					void tick();
				} catch (error) { window.PearnlyScan.report(error, "reader"); window.clearTimeout(slow); stop(); on_error?.(error); }
			},
			pause() { paused = true; },
			resume(skip_code = "") { candidate = ""; candidate_format = ""; hits = 0; focus = null; skip = String(skip_code || "").trim(); paused = false; },
			has_torch() { return Boolean(stream?.getVideoTracks?.()[0]?.getCapabilities?.().torch); },
			async torch(on) {
				const track = stream?.getVideoTracks?.()[0];
				if (track?.getCapabilities?.().torch) await track.applyConstraints({ advanced: [{ torch: on }] });
			},
			stop,
		};
	}

	// LINE 普通内置浏览器（聊天里点链接打开的那种）不把相机请求交给手机系统：不弹授权也不报错。
	// 同一页用 LIFF 打开时 LINE 会弹原生授权（2026-10-15 三星 A14 实测）。`to_liff` 把当前页换成 LIFF 打开，
	// 链接带 `pearnly_liff`：到了那边不再转第二次（即便判断不出自己是不是在 LIFF 里，也不会来回跳）。
	// 只能放一个参数：LINE 把深链交给 LIFF 时只保留第一个查询参数（2026-10-15 测试机访问日志实测）。
	// `scan=true` 时值是 `scan`，页面据此接着开扫码，开了就改回 `1`。
	const LIFF_MARK = "pearnly_liff";
	const to_liff = (liff_id, scan = false) => {
		const here = new URL(window.location.href);
		if (!liff_id || !(plain_line() || in_line()) || /\bLIFF\b/.test(agent()) || here.searchParams.get(LIFF_MARK)) return false;
		here.searchParams.delete(LIFF_MARK);
		const rest = here.searchParams.toString();
		here.search = `?${LIFF_MARK}=${scan ? "scan" : "1"}${rest ? `&${rest}` : ""}`;
		window.location.href = `https://liff.line.me/${liff_id}${here.pathname}${here.search}`;
		return true;
	};
	// 相机权限三步（用户 2026-10-15，标准做法）：① 有权限直接开；② 没有就请求，由浏览器 / 系统 / LINE 弹原生授权
	// （LINE 普通内置浏览器弹不出，页面转 LIFF）；③ 弹不出来了（以前拒绝过、App 没有相机权限）才引导去设置，
	// 人从设置切回来页面自动重开。③ 的文案按所在环境给路径：`text_key` 把原因换成术语表里的那一条。
	const platform = () => /iPhone|iPad|iPod/i.test(agent()) ? "ios" : /Android/i.test(agent()) ? "android" : "desktop";
	const SETTINGS = new Set(["denied", "in_line"]);
	const text_key = reason => {
		const where = platform();
		if (reason === "in_line") return `in_line_${where === "ios" ? "ios" : "android"}`;
		if (reason === "denied") return where === "desktop" ? "denied" : `denied_${where}`;
		return reason;
	};
	window.PearnlyScan = { formats: WANTED, kind: kind_of, reason, report, reader, in_line, plain_line, to_liff, text_key,
		needs_settings: reason => SETTINGS.has(reason) };
})();
