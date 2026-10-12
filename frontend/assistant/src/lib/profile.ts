// 当前登录用户与本站产品（`pearnly_core.shell.describe`）。站点只属于一个产品：
// 开户给的是哪个产品，登录后就只看到这个产品的形象与工具。

import { useEffect, useState } from "react";

/** 产品名（原 Tomo，用户 2026-10 定名 CC）：侧栏左上角、手机抽屉标题、标签页标题、桌面通知共用这一处。 */
export const APP_NAME = "CC";

export type Profile = { product: string; product_label: string; full_name: string; user: string };

let cached: Promise<Profile> | null = null;

/** 当前用户与产品（只取一次）；不在组件里的流程（深链接）直接等它。 */
export function loadProfile(): Promise<Profile> {
  cached ??= fetch("/api/method/pearnly_core.shell.describe", { credentials: "same-origin", cache: "no-store" })
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
    .then((body: { message: Profile }) => body.message);
  return cached;
}

export function useProfile(): Profile | null {
  const [profile, setProfile] = useState<Profile | null>(null);
  useEffect(() => {
    void loadProfile().then(setProfile, () => setProfile(null));
  }, []);
  return profile;
}

// 智能体自己的形象（scripts/build-assistant-brand.py 从 website/ 生成到 src/brand/）：欢迎处不分产品都用它。
// 经 Vite 打包带内容哈希，换了形象地址就变，不会被浏览器或 Cloudflare 缓存住旧图。
const BRANDS = import.meta.glob("../brand/*.svg", { query: "?url", import: "default", eager: true }) as Record<string, string>;

export function assistantMascot(): string | null {
  return BRANDS["../brand/cc.svg"] || null;
}

/** CC 的标志（橘色圆角方块，同登录页、任务栏图标）：只放在侧栏左上角「图标 + CC」（用户 2026-10-12）。 */
export function assistantLogo(): string | null {
  return BRANDS["../brand/cc-icon.svg"] || null;
}
