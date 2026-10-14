// 页面自己弹的系统通知（回复完成、本站提醒，lib/desktop.ts 调）：
//
//   浏览器      — 交给后台脚本弹（registration.showNotification）。安卓 Chrome（含装成应用）不让页面直接
//                 new Notification（抛 Illegal constructor）；点通知由后台脚本聚焦 CC 窗口并打开 url
//                 （pearnly_core/assistant/service_worker.js 的 notificationclick；url 是 lib/nav.ts 认的深链接）。
//   桌面端窗口  — 不注册后台脚本（lib/install.ts），用 new Notification，由桌面端弹系统通知（托盘按 tag 去重），点了调 onClick。
//
// 弹不出来就算了：任何一步出错都不往外抛，一条通知不能拖垮整页。

const HOME = "/assistant";

/** 打开某段对话的深链接；没有对话按 view 开那一页：company:<公司> 是主档页，pending 待处理，其余定时任务页
 *  （同服务端 assistant/contracts.py 的 deep_link）。 */
export function deepLink(session: string, view = ""): string {
  if (session) return `${HOME}?c=${encodeURIComponent(session)}`;
  if (view.startsWith("company:") && view.length > 8) return `${HOME}?company=${encodeURIComponent(view.slice(8))}`;
  return `${HOME}?view=${view === "pending" ? "pending" : "tasks"}`;
}

async function workerRegistration(): Promise<ServiceWorkerRegistration | undefined> {
  if (window.qt?.webChannelTransport || !("serviceWorker" in navigator)) return undefined;
  return navigator.serviceWorker.getRegistration(HOME).catch(() => undefined);
}

/** 弹一条系统通知（要用户已允许通知）；弹出了回 true。url：点通知打开的 /assistant 地址；onClick：桌面端窗口里点了做什么。 */
export async function showNote(title: string, body: string, tag: string, url: string, onClick?: () => void): Promise<boolean> {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return false;
    const registration = await workerRegistration();
    if (registration) {
      await registration.showNotification(title, { body, tag, data: { url } });
      return true;
    }
    const note = new Notification(title, { body, tag });
    note.onclick = () => {
      window.focus();
      onClick?.();
    };
    return true;
  } catch (error) {
    console.warn("[cc] notification not shown", error);
    return false;
  }
}
