/* 摄像头扫码：条码与二维码共用一份（商品页先用，收银台以后接同一个入口）。

   解码只用浏览器自带的 `BarcodeDetector` —— 桌面 Chrome/Edge、安卓 Chrome、Safari 17+
   都带这个能力，而引一个 wasm 解码器等于给一个"未必用得上"的功能常年付一份下载与一份
   安全面。没有这个能力的浏览器直说，扫码枪与手输照旧。

   能力探针是异步的（`getSupportedFormats()` 返回 Promise），所以页面先 `scan_ready()`
   再决定放不放那个按钮；探完之前按"能用"处理，免得按钮闪一下才出现。 */
frappe.provide("frappe.pearnly");
(() => {
	const p = frappe.pearnly;

	// 条码按零售常见的几档给；二维码那一路只认 qr_code。
	const WANTED = {
		barcode: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "codabar"],
		qr: ["qr_code"],
	};

	const known = {}; // kind → 这台机器/这个浏览器真支持的码制
	const probed = {}; // kind → Promise

	function probe(kind) {
		if (!probed[kind]) {
			const wanted = WANTED[kind] || [];
			const detect =
				typeof window.BarcodeDetector === "function" &&
				typeof window.BarcodeDetector.getSupportedFormats === "function";
			probed[kind] = detect
				? window.BarcodeDetector.getSupportedFormats()
					.then((formats) => {
						known[kind] = wanted.filter((format) => formats.includes(format));
						return known[kind];
					})
					.catch(() => (known[kind] = []))
				: Promise.resolve((known[kind] = []));
		}
		return probed[kind];
	}

	/** 探一次两种码制（页面加载时叫一次；以后再叫是读缓存）。 */
	p.scan_ready = () => Promise.all(Object.keys(WANTED).map(probe));

	/** 还没探完时按"能用"处理：探完发现不行，点下去会得到那句"这个浏览器不支持"。 */
	p.scan_supported = (kind) => (known[kind] || []).length > 0 || !(kind in known);

	const CAMERA_DENIED = "product.scan.denied";
	const CAMERA_MISSING = "product.scan.noCamera";
	const UNSUPPORTED = "product.scan.unsupported";

	/** 打开扫码窗，拿到一串码就回调（弹窗自己关）。 */
	p.scan = async ({ kind = "barcode", on_result } = {}) => {
		const t = await p.texts("product.");
		const formats = await probe(kind);
		if (!formats.length || !navigator.mediaDevices?.getUserMedia) {
			frappe.msgprint(t(navigator.mediaDevices?.getUserMedia ? UNSUPPORTED : CAMERA_MISSING));
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title: t("product.scan.title"),
			fields: [{
				fieldtype: "HTML",
				options: `<video class="pearnly-scan-video" playsinline muted
					style="width:100%;max-height:60vh;border-radius:10px;background:#000"></video>
					<p class="text-muted" style="margin-top:8px">${p.escape(t("product.scan.hint"))}</p>`,
			}],
		});
		dialog.show();

		const video = dialog.$wrapper.find("video").get(0);
		let stream = null;
		let timer = null;
		let busy = false;

		// 有的浏览器会静默地不开始播放（muted 属性在建元素时未必生效），
		// 于是每一帧都补一次 play() —— 自己会好，用户不用点第二次。
		async function ensure_playing() {
			if (!video.paused) return;
			try {
				await video.play();
			} catch (error) {
				// 这一帧起不来就下一帧再试。
			}
		}

		// 关窗、扫到、或中途出错都走这里 —— 相机不能留在后台亮着。
		function stop() {
			if (timer) clearInterval(timer);
			timer = null;
			if (stream) stream.getTracks().forEach((track) => track.stop());
			stream = null;
		}
		dialog.$wrapper.on("hidden.bs.modal", stop);

		try {
			stream = await navigator.mediaDevices.getUserMedia({
				video: { facingMode: "environment" },
			});
		} catch (error) {
			stop();
			dialog.hide();
			frappe.msgprint(t(CAMERA_DENIED));
			return;
		}
		video.srcObject = stream;
		video.muted = true;
		video.playsInline = true;
		await ensure_playing();

		const reader = new window.BarcodeDetector({ formats });
		const clean = (value) => String(value || "").trim().replace(/\s+/g, "");
		timer = setInterval(async () => {
			if (busy || !stream) return;
			busy = true;
			try {
				await ensure_playing();
				if (video.readyState < 2) return;
				const found = await reader.detect(video);
				const code = found.length ? clean(found[0].rawValue) : "";
				if (code) {
					stop();
					dialog.hide();
					on_result(code);
				}
			} catch (error) {
				// 单帧解不出来是常态（手抖、没对准），下一帧接着来。
			} finally {
				busy = false;
			}
		}, 250);
	};
})();
