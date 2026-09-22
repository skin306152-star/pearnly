/* Shared local camera reader. Native decoder first, bundled production WASM fallback.
 * Dialogs remain Frappe native; the video surface is necessarily browser media API.
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
			await frappe.require("/assets/pearnly_erp_portal/vendor/barcode-detector/ponyfill.js");
			const api = window.BarcodeDetectionAPI;
			await api.prepareZXingModule({ overrides: {
				locateFile: name => name.endsWith(".wasm")
					? "/assets/pearnly_erp_portal/vendor/barcode-detector/zxing_reader.wasm" : name,
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
	p.scan_feedback = host => {
		try { frappe.utils.play_sound("submit"); } catch (_error) { /* Muted browser. */ }
		navigator.vibrate?.(60);
		$(host).find(".pearnly-scan-stage").addClass("pearnly-scan-done");
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
	p.scan = async ({ kind = "barcode", on_result } = {}) => {
		const t = await p.texts("product.");
		const d = new frappe.ui.Dialog({ title: t("product.scan.title"), fields: [
			{ fieldtype: "HTML", fieldname: "reader", options: p.scan_stage() },
			{ fieldtype: "Check", fieldname: "torch", label: t("product.scan.light"), hidden: 1,
				change: () => scanner?.torch(Boolean(d.get_value("torch"))) },
		] });
		let scanner;
		d.onhide = () => scanner?.stop();
		d.show();
		scanner = p.scan_reader({ host: d.fields_dict.reader.$wrapper, kind,
			on_result: async (code, details) => { scanner.stop(); d.hide(); await on_result(code, details); },
			on_error: () => { d.hide(); frappe.msgprint(t("product.scan.denied")); },
		});
		await scanner.start();
		d.set_df_property("torch", "hidden", !scanner.has_torch());
	};
})();
