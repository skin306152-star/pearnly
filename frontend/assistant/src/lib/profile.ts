// 当前登录用户与本站产品（`pearnly_core.shell.describe`）。站点只属于一个产品：
// 开户给的是哪个产品，登录后就只看到这个产品的形象与工具。

import { useEffect, useState } from "react";

export type Profile = { product: string; product_label: string; full_name: string; user: string };

let cached: Promise<Profile> | null = null;

function load(): Promise<Profile> {
  cached ??= fetch("/api/method/pearnly_core.shell.describe", { credentials: "same-origin", cache: "no-store" })
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
    .then((body: { message: Profile }) => body.message);
  return cached;
}

export function useProfile(): Profile | null {
  const [profile, setProfile] = useState<Profile | null>(null);
  useEffect(() => {
    void load().then(setProfile, () => setProfile(null));
  }, []);
  return profile;
}

// 智能体自己的形象（scripts/build-assistant-brand.py 从 website/ 生成到 src/brand/）：欢迎处不分产品都用它。
// 经 Vite 打包带内容哈希，换了形象地址就变，不会被浏览器或 Cloudflare 缓存住旧图。
const BRANDS = import.meta.glob("../brand/*.svg", { query: "?url", import: "default", eager: true }) as Record<string, string>;

export function assistantMascot(): string | null {
  return BRANDS["../brand/ai.svg"] || null;
}
