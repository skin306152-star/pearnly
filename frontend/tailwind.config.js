import preset from "frappe-ui/tailwind";

// 侧栏等外壳组件用官方 Frappe UI（用户 2026-09-18 定："侧栏的前端组件用官方的"）。
//
// Frappe UI 的组件是 Tailwind 写的，所以必须带上 Tailwind 与它的 preset ——
// 不带 preset 的话 `bg-surface-menu-bar` / `text-ink-gray-6` 这些类根本不存在，
// 组件会长成没有样式的裸块。
//
// ★ 颜色仍然是我们的：Frappe UI 把颜色做成 CSS 变量（`--surface-menu-bar` 等），
//   我们在 src/styles/tailwind.css 里把这些变量指向 design/pearnly-ui.css 的令牌
//   （ADR-007）。这样"组件用官方的、皮肤用我们的"，两边都不让步。
export default {
  presets: [preset],
  content: [
    "./index.html",
    "./src/**/*.{vue,ts}",
    // 必须扫官方组件的源码：它们是按需编译的（没有预构建产物），
    // 不扫的话里面的 Tailwind 类会被摇掉，组件看着就"没样式"。
    "./node_modules/frappe-ui/src/**/*.{vue,js,ts}",
  ],
  theme: {
    extend: {},
  },
};
