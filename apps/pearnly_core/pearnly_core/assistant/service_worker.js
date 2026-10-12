// CC 的后台脚本（ADR-019：手机 / 电脑上可以把 CC「安装」成应用）。由 `assistant.api.service_worker` 发出，只管 /assistant 页面：
// - 打开页面照常走网络：页面带登录信息，一律不缓存；接口、附件、下载都不经这里。
// - 断网打不开时给一个最简的离线页（文案在发出脚本时按语言填好）。
// 以后接推送通知也在这里。
const OFFLINE = __OFFLINE_PAGE__;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(
      () => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } }),
    ),
  );
});
