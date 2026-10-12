// CC 的后台脚本（ADR-019：手机 / 电脑上可以把 CC「安装」成应用）。由 `assistant.api.service_worker` 发出，只管 /assistant 页面：
// - 打开页面照常走网络：页面带登录信息，一律不缓存；接口、附件、下载都不经这里。
// - 断网打不开时给一个最简的离线页（文案在发出脚本时按语言填好）。
// - 网页推送（`assistant.push`）：收到就弹通知，每条都弹（不弹的话 iPhone 会撤销订阅，Chrome 会补一条通用通知）；
//   同一条提醒按 tag 覆盖。点通知打开那段对话：已有 CC 窗口就聚焦并交给页面切换（`pearnly-open`），没有就新开一个。
const OFFLINE = __OFFLINE_PAGE__;
const HOME = "/assistant";
const PAGE = /^\/assistant(?:[/?#]|$)/;

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

// 只打开对话页；别的地址一律改成对话页首页。
function target(url) {
  return typeof url === "string" && PAGE.test(url) ? url : HOME;
}

function text(value) {
  return typeof value === "string" ? value : "";
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = (event.data && event.data.json()) || {};
  } catch (err) {
    data = {};
  }
  if (typeof data !== "object") data = {};
  event.waitUntil(
    self.registration.showNotification(text(data.title) || "CC", {
      body: text(data.body),
      tag: text(data.tag) || undefined,
      data: { url: target(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = target(event.notification.data && event.notification.data.url);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => PAGE.test(new URL(client.url).pathname));
      if (open) {
        return open
          .focus()
          .catch(() => open)
          .then((client) => (client || open).postMessage({ type: "pearnly-open", url }));
      }
      return self.clients.openWindow(new URL(url, self.location.origin).href);
    }),
  );
});
