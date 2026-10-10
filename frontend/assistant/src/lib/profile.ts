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

const BRANDS = new Set(["cowork", "erp", "dms"]);

/** 宣传页的产品形象（scripts/build-assistant-brand.py 从 website/ 生成，见 public/brand/）。 */
export function brandIcon(product: string | undefined): string | null {
  return product && BRANDS.has(product) ? `${import.meta.env.BASE_URL}brand/${product}.svg` : null;
}
