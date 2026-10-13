// 手机与浏览器的推送通知（Web Push，用户 2026-10-12）：定时任务提醒、完成简报在 CC 没打开时也送到。
// 订阅交给本站（pearnly_core.assistant.api 的 push_key / push_subscribe / push_unsubscribe），由后台脚本
// （service_worker.js）弹通知；点通知打开那段对话（没有对话就定时任务页，见 lib/nav.ts）。
//
//   桌面端窗口里不用（状态 desktop：系统通知由桌面端自己弹，设置里没有这一行）；iPhone / iPad 只有添加到主屏幕后打开
//   才收得到推送（状态 install）；浏览器没有网页推送是 unsupported。设置里这三种和拒绝过（denied）开关都灰着，各给一句原因。
//   后台脚本没就绪、本站公钥没取到（网络、站点一时出错）不算不支持：回到 unknown（开关灰着、不给原因），下次 syncPush 再试
//   （打开设置时会再调一次）。
//   每次打开 CC 同步一次（syncPush）：浏览器里已有订阅就重新交给本站，同一台设备换人登录时归到现在登录的人；
//   本站公钥换过就退掉旧订阅，开关显示为关。退出登录前只让本站删掉这台设备（forgetDevice，不看开关状态），
//   浏览器里的订阅留着，下一个人登录时再认领。
//   打开（enablePush）只能在点击里直接调：iPhone 要求订阅发生在用户手势里，所以公钥和后台脚本都先取好，中间不等网络。
//   打开失败时开关回到关，并给一句原因（failure：本站拒收的原文，或「此设备无法接收通知」）；浏览器订阅上了、
//   本站没收下就把浏览器这边也退掉，两边都是关，下次打开 CC 不会自己变成开。

import { useSyncExternalStore } from "react";
import { st } from "@/i18n";
import { isDesktopApp } from "@/lib/desktop";
import { get, post, SiteError } from "@/lib/frappe";
import { isIOS, standalone } from "@/lib/install";

/** unknown：还没同步完（开关灰着）；install：iPhone 在浏览器页签里，要先添加到主屏幕；desktop：CC 桌面端窗口。 */
export type PushState = "unknown" | "desktop" | "unsupported" | "install" | "denied" | "off" | "on";

let state: PushState = "unknown";
// 上一次打开失败的原因（空 = 没失败）
let failure = "";
let registration: ServiceWorkerRegistration | null = null;
let publicKey: Uint8Array<ArrayBuffer> | null = null;
let working = false;
let synced: Promise<void> | null = null;
// 正在退出登录：同步不再把这台设备认领给本人；claiming 是同步发出、还在路上的认领
let leaving = false;
let claiming: Promise<unknown> | null = null;
const listeners = new Set<() => void>();

function setState(next: PushState) {
  if (state === next) return;
  state = next;
  listeners.forEach((listener) => listener());
}

function setFailure(next: string) {
  if (failure === next) return;
  failure = next;
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
  if (isDesktopApp()) return setState("desktop");
  if (isIOS() && !standalone()) return setState("install");
  if (!capable()) return setState("unsupported");
  // 后台脚本 15 秒没就绪、本站公钥没取到：这台设备本身支持，只是这次没准备好 —— 回到 unknown，下次 syncPush 再试
  const found = await within(
    Promise.all([navigator.serviceWorker.ready, get<{ key: string }>("push_key").then((r) => r?.key)]),
    15_000,
  ).catch(() => null);
  if (!found?.[1]) {
    synced = null;
    return setState("unknown");
  }
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
  if (leaving) return setState("off");
  try {
    claiming = post("push_subscribe", payload(subscription));
    await claiming;
    setState("on");
  } catch {
    setState("off");
  } finally {
    claiming = null;
  }
}

/** 每次打开 CC 调一次（重复调用只做一次；上次没准备好的再试一次）。 */
export function syncPush(): Promise<void> {
  synced ??= sync().catch(() => setState("unsupported"));
  return synced;
}

/** 打开失败：浏览器订阅上了、本站没收下就退掉浏览器这边；拒绝过通知就显示「已在浏览器设置中关闭」；
 *  只是关掉了询问不算失败；其余给原因。 */
async function enableFailed(error: unknown, subscription: PushSubscription | null): Promise<void> {
  if (subscription) await subscription.unsubscribe().catch(() => false);
  if (Notification.permission === "denied") return setState("denied");
  setState("off");
  if (Notification.permission !== "granted") return;
  setFailure(error instanceof SiteError && error.message ? error.message : st.notificationsFailed);
}

/** 打开通知：必须在点击回调里直接调（见文件头）。 */
export function enablePush(): Promise<void> {
  if (!registration || !publicKey || working) return Promise.resolve();
  working = true;
  setFailure("");
  let subscription: PushSubscription | null = null;
  return registration.pushManager
    .subscribe({ userVisibleOnly: true, applicationServerKey: publicKey })
    .then((created) => {
      subscription = created;
      return post("push_subscribe", payload(created));
    })
    .then(() => setState("on"))
    .catch((error: unknown) => enableFailed(error, subscription))
    .finally(() => {
      working = false;
    });
}

/** 关掉通知：本站删掉这台设备，浏览器退订。 */
export async function disablePush(): Promise<void> {
  if (!registration || working) return;
  working = true;
  setFailure("");
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

/** 退出登录前：本站不再往这台设备推这个人的提醒（浏览器订阅留着，下一个人登录时认领）。不看开关状态：同步还没走完、
 *  上次认领失败时，本站也可能还记着这台设备。还在路上的认领先等它落地（不然它晚到又把设备认领回来）。最多等 3 秒，不挡退出。 */
export async function forgetDevice(): Promise<void> {
  leaving = true;
  if (isDesktopApp() || !capable()) return;
  await within(
    (async () => {
      if (claiming) await within(claiming, 2000).catch(() => undefined);
      const reg = registration ?? (await navigator.serviceWorker.getRegistration("/assistant"));
      const subscription = await reg?.pushManager.getSubscription();
      if (subscription) await post("push_unsubscribe", { endpoint: subscription.endpoint });
    })(),
    3000,
  ).catch(() => undefined);
}

/** 现在的开关状态与上一次打开失败的原因（usePush 读的就是它们）。 */
export function currentPush(): { state: PushState; failure: string } {
  return { state, failure };
}

function subscribeStore(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function usePush(): { state: PushState; failure: string; enable: () => Promise<void>; disable: () => Promise<void> } {
  const current = useSyncExternalStore(subscribeStore, () => state, () => state);
  const reason = useSyncExternalStore(subscribeStore, () => failure, () => failure);
  return { state: current, failure: reason, enable: enablePush, disable: disablePush };
}
