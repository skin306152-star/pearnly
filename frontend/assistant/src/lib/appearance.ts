// 外观偏好（设置 → 常规，同 Claude）：主题、对话字体、对话记录宽度、动效。只存在本机浏览器里，写成 <html> 上的属性，
// 样式在 globals.css（data-cc-font / data-cc-width / data-cc-motion），主题由 main.tsx 换 .dark。对话文字大小在 lib/desktop.ts。

export type Theme = "system" | "light" | "dark";
export type ChatFont = "serif" | "sans";
export type ChatWidth = "narrow" | "medium" | "wide";
export type Motion = "system" | "reduce";

type Prefs = { theme: Theme; font: ChatFont; width: ChatWidth; motion: Motion };

const SPEC: { [K in keyof Prefs]: { key: string; values: readonly Prefs[K][]; fallback: Prefs[K] } } = {
  theme: { key: "cc.theme", values: ["system", "light", "dark"], fallback: "system" },
  font: { key: "cc.font", values: ["serif", "sans"], fallback: "serif" },
  width: { key: "cc.width", values: ["narrow", "medium", "wide"], fallback: "narrow" },
  motion: { key: "cc.motion", values: ["system", "reduce"], fallback: "system" },
};

export function pref<K extends keyof Prefs>(name: K): Prefs[K] {
  const spec = SPEC[name];
  try {
    const value = localStorage.getItem(spec.key) as Prefs[K] | null;
    return value && spec.values.includes(value) ? value : spec.fallback;
  } catch {
    return spec.fallback;
  }
}

export function setPref<K extends keyof Prefs>(name: K, value: Prefs[K]) {
  try {
    localStorage.setItem(SPEC[name].key, value);
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
  apply();
}

/** 把偏好写到 <html> 上；主题变了通知 main.tsx 重新决定深浅色。 */
export function apply() {
  const root = document.documentElement;
  root.dataset.ccFont = pref("font");
  root.dataset.ccWidth = pref("width");
  root.dataset.ccMotion = pref("motion");
  window.dispatchEvent(new Event("cc-theme"));
}

apply();
