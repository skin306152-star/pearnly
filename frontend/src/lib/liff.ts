/**
 * LINE LIFF SDK 的装载与初始化。
 *
 * 为什么单独一份：编辑页和账号页都要用它 —— 编辑页要 `closeWindow`，
 * 账号页要 `openWindow({external: true})`（"拿票出站"）。两页各写一份必然漂移，
 * 而这里最容易踩的坑是**忘记 `init` 就直接调 API**（LIFF 只会在 init 之后才可用）。
 */

const SDK_SRC = "https://static.line-scdn.net/liff/edge/2/sdk.js";

type LiffLike = Record<string, unknown> & {
  init?: (options: { liffId: string }) => Promise<void>;
  isInClient?: () => boolean;
  openWindow?: (options: { url: string; external?: boolean }) => void;
  closeWindow?: () => void;
  getOS?: () => string;
  isLoggedIn?: () => boolean;
};

function current(): LiffLike | null {
  return (window as unknown as { liff?: LiffLike }).liff || null;
}

let loading: Promise<void> | null = null;

/** 注入 SDK（幂等）。加载失败也 resolve —— 调用方按"没有 LIFF"降级。 */
export function loadLiffSdk(): Promise<void> {
  if (current()) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise<void>((resolve) => {
    const script = document.createElement("script");
    script.src = SDK_SRC;
    script.onload = () => resolve();
    script.onerror = () => resolve();
    document.head.appendChild(script);
  });
  return loading;
}

/** 装载 + `init`；拿不到或初始化失败一律回 null（调用方走非 LIFF 分支）。 */
export async function initLiff(liffId: string): Promise<LiffLike | null> {
  if (!liffId) return null;
  await loadLiffSdk();
  const liff = current();
  if (!liff || typeof liff.init !== "function") return null;
  try {
    await liff.init({ liffId });
  } catch {
    return null;
  }
  return liff;
}

/** 在 LINE 内是否可用（能开窗口/能关窗口）。 */
export function inClient(liff: LiffLike | null): boolean {
  return Boolean(liff && typeof liff.isInClient === "function" && liff.isInClient());
}

/** 带票出站到系统浏览器；不在 LINE 内（或没有 SDK）就普通跳转。 */
export function openExternal(liff: LiffLike | null, url: string): boolean {
  const absolute = new URL(url, window.location.origin).toString();
  if (inClient(liff) && typeof liff?.openWindow === "function") {
    (liff.openWindow as (o: { url: string; external?: boolean }) => void)({
      url: absolute,
      external: true,
    });
    if (typeof liff.getOS === "function" && liff.getOS() === "ios") {
      (liff.closeWindow as () => void)?.();
    }
    return true;
  }
  window.location.assign(absolute);
  return false;
}
