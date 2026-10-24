// 扫码入口：照页面真实的点法打开扫码（args.steps：依次点的选择器 / 执行的表达式），然后核
// ① 弹窗 / 区域里挂着 <video>，画面可见（有尺寸、不是 display:none、轨道在播）；② 读出测试码（args.code）。
// 抓的就是 10-23 那类事故：相机灯亮着、画面被框架重画换掉成黑屏。args.read_target：读出的码应出现在哪个输入框（可选）。
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = { route: frappe.get_route(), steps: [] };
for (const step of args.steps || []) {
  try {
    if (step.click) {
      let el = null;
      for (let i = 0; i < 20 && !el; i++) { el = document.querySelector(step.click); if (!el) await sleep(250); }
      if (!el) { out.steps.push({ click: step.click, found: false }); return { ...out, ok: false, why: `no element ${step.click}` }; }
      el.click();
      out.steps.push({ click: step.click, found: true });
    } else if (step.eval) {
      out.steps.push({ eval: step.eval, value: String(await (0, eval)(step.eval)).slice(0, 200) });
    }
    await sleep(step.wait || 800);
  } catch (e) {
    return { ...out, ok: false, why: `step failed: ${e && e.message}` };
  }
}
let code = null, seen = null;
const host = () => (window.cur_dialog && cur_dialog.$wrapper && cur_dialog.$wrapper.get(0)) || document;
const look = () => {
  const video = host().querySelector("video");
  if (!video) return null;
  const rect = video.getBoundingClientRect();
  return { readyState: video.readyState, w: video.videoWidth, h: video.videoHeight,
    shown: rect.width > 0 && rect.height > 0 && getComputedStyle(video).display !== "none",
    live: !!(video.srcObject && video.srcObject.getTracks().length && video.srcObject.getTracks().every((t) => t.readyState === "live")) };
};
// 读到码后弹窗会关、画面跟着没了：边等边记「见过有画面」，读码结果看目标输入框
for (let i = 0; i < 48 && !(seen && (code || !args.read_target)); i++) {
  await sleep(250);
  const now = look();
  if (now && now.w > 0 && now.shown && now.live) seen = seen || now;
  out.last_video = now || out.last_video || null;
  if (args.read_target) {
    const input = document.querySelector(args.read_target);
    if (input && String(input.value || "").includes(args.code)) code = input.value;
  }
}
out.picture = seen;
out.code = code;
out.ok = !!seen && (!args.read_target || !!code);
return out;
