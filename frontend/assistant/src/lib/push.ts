// 手机与浏览器的推送通知（Web Push，用户 2026-10-12）：定时任务提醒、完成简报在 CC 没打开时也送到。
// 订阅交给本站（pearnly_core.assistant.api 的 push_key / push_subscribe / push_unsubscribe），由后台脚本
// （service_worker.js）弹通知；点通知打开那段对话（没有对话就定时任务页，见 lib/nav.ts）。
//
//   桌面端窗口里不用（系统通知由桌面端自己弹）；iPhone / iPad 只有添加到主屏幕后打开才收得到推送（状态 install）。
//   每次打开 CC 同步一次（syncPush）：浏览器里已有订阅就重新交给本站，同一台设备换人登录时归到现在登录的人；
//   本站公钥换过就退掉旧订阅，开关显示为关。退出登录前只让本站删掉这台设备（forgetDevice），浏览器里的订阅留着，
//   下一个人登录时再认领。
//   打开（enablePush）只能在点击里直接调：iPhone 要求订阅发生在用户手势里，所以公钥和后台脚本都先取好，中间不等网络。

import { useSyncExternalStore } from "react";
import { isDesktopApp } from "@/lib/desktop";
import { get, post } from "@/lib/frappe";
import { isIOS, standalone } from "@/lib/install";

/** unknown：还没同步完（不显示开关）；install：iPhone 在浏览器页签里，要先添加到主屏幕。 */
export type PushState = "unknown" | "unsupported" | "install" | "denied" | "off" | "on";

let state: PushState = "unknown";
let registration: ServiceWorkerRegistration | null = null;
let publicKey: Uint8Array<ArrayBuffer> | null = null;
let working = false;
let synced: Promise<void> | null = null;
const listeners = new Set<() => void>();

function setState(next: PushState) {
  if (state === next) return;
  state = next;
  listeners.forEach((listener) => listener());
}

function capable(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** base64url（服务端 VAPID 公钥）→ 字节。 */
function bytesOf(text: string): Uint8Array<ArrayBuffer> {
  const raw = atob(text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "="));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function sameKey(subscription: PushSubscription, key: Uint8Array): boolean {
  const own = subscription.options.applicationServerKey;
  if (!own) return false;
  const bytes = new Uint8Array(own);
  return bytes.length === key.length && bytes.every((b, i) => b === key[i]);
}

function payload(subscription: PushSubscription) {
  const json = subscription.toJSON();
  return { endpoint: subscription.endpoint, p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" };
}

function within<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([promise, new Promise<T>((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);
}

async function sync(): Promise<void> {
  if (isDesktopApp()) return setState("unsupported");
  if (isIOS() && !standalone()) return setState("install");
  if (!capable()) return setState("unsupported");
  // 后台脚本没注册上 ready 永远不来、本站还没有推送接口都算不支持：不显示开关
  const found = await within(
    Promise.all([navigator.serviceWorker.ready, get<{ key: string }>("push_key").then((r) => r?.key)]),
    15_000,
  ).catch(() => null);
  if (!found?.[1]) return setState("unsupported");
  const [reg, text] = found;
  const key = bytesOf(text);
  registration = reg;
  publicKey = key;
  if (Notification.permission === "denied") return setState("denied");
  const subscription = await reg.pushManager.getSubscription().catch(() => null);
  if (!subscription || Notification.permission !== "granted") return setState("off");
  if (!sameKey(subscription, key)) {
    await subscription.unsubscribe().catch(() => false);
    return setState("off");
  }
  try {
    await post("push_subscribe", payload(subscription));
    setState("on");
  } catch {
    setState("off");
  }
}

/** 每次打开 CC 调一次（重复调用只做一次）。 */
export function syncPush(): Promise<void> {
  synced ??= sync().catch(() => setState("unsupported"));
  return synced;
}

/** 打开通知：必须在点击回调里直接调（见文件头）。 */
export function enablePush(): Promise<void> {
  if (!registration || !publicKey || working) return Promise.resolve();
  working = true;
  return registration.pushManager
    .subscribe({ userVisibleOnly: true, applicationServerKey: publicKey })
    .then((subscription) => post("push_subscribe", payload(subscription)))
    .then(() => setState("on"))
    .catch(() => setState(Notification.permission === "denied" ? "denied" : "off"))
    .finally(() => {
      working = false;
    });
}

/** 关掉通知：本站删掉这台设备，浏览器退订。 */
export async function disablePush(): Promise<void> {
  if (!registration || working) return;
  working = true;
  try {
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await post("push_unsubscribe", { endpoint: subscription.endpoint }).catch(() => undefined);
      await subscription.unsubscribe().catch(() => false);
    }
    setState("off");
  } finally {
    working = false;
  }
}

/** 退出登录前：本站不再往这台设备推这个人的提醒（浏览器订阅留着，下一个人登录时认领）。最多等 3 秒，不挡退出。 */
export async function forgetDevice(): Promise<void> {
  if (state !== "on" || !registration) return;
  const reg = registration;
  await within(
    (async () => {
      const subscription = await reg.pushManager.getSubscription();
      if (subscription) await post("push_unsubscribe", { endpoint: subscription.endpoint });
    })(),
    3000,
  ).catch(() => undefined);
}

export function usePush(): { state: PushState; enable: () => Promise<void>; disable: () => Promise<void> } {
  const current = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
  );
  return { state: current, enable: enablePush, disable: disablePush };
}
