// 自检（不需要站点）：假摄像头有画面、共用读码器（public/scan/reader.js）读得出测试条码。
const video = document.createElement("video");
video.muted = true; video.playsInline = true;
document.body.appendChild(video);
let code = null, error = null;
const reader = window.PearnlyScan.reader({ video, on_result: (value) => { code = value; }, on_error: (e) => { error = String(e && (e.scan_reason || e.message || e)); } });
await reader.start();
for (let i = 0; i < 40 && !code; i++) await new Promise((r) => setTimeout(r, 250));
const picture = { readyState: video.readyState, w: video.videoWidth, h: video.videoHeight };
reader.stop();
return { ok: !!code && picture.w > 0 && code.replace(/\s/g, "") === args.code, code, picture, error };
