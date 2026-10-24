// 自检（不需要站点）：假摄像头有画面、共用读码器（public/scan/reader.js）读得出测试条码。
const video = document.createElement("video");
video.muted = true; video.playsInline = true;
document.body.appendChild(video);
let code = null, error = null;
const reader = window.PearnlyScan.reader({ video, on_result: (value) => { code = value; }, on_error: (e) => { error = String(e && (e.scan_reason || e.message || e)); } });
// 读码器坏了时 start() 可能永远不返回：最多等 8 秒，按「没画面」报
await Promise.race([reader.start().catch((e) => { error = String(e && (e.scan_reason || e.message || e)); }), new Promise((r) => setTimeout(r, 8000))]);
for (let i = 0; i < 40 && !code; i++) await new Promise((r) => setTimeout(r, 250));
const picture = { readyState: video.readyState, w: video.videoWidth, h: video.videoHeight };
reader.stop();
return { ok: !!code && picture.w > 0 && code.replace(/\s/g, "") === args.code, code, picture, error };
