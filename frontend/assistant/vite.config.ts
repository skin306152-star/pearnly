import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 智能体对话页（ADR-019）。与 frontend/ 一样构建成**静态文件**交给站点提供：
// 不做常驻 Node 服务；接口全部是本站 `pearnly_core.assistant.api` 的白名单方法，登录态就是站点会话。
//
// 产物放 `apps/pearnly_core/pearnly_core/public/assistant/`，所以资源前缀必须是
// `/assets/pearnly_core/assistant/`（Frappe 只把 `/assets/<app>/…` 映射到 App 的 public）。
// 页面入口是共用层的 www/assistant（Cowork / ERP / DMS 每个租户站点都有）（读这里产出的 index.html 搬标签，见那边的说明）。
const BASE = "/assets/pearnly_core/assistant/";

// 图标与 manifest 的文件名是固定的，而站点给 /assets 一年缓存（Cloudflare 也照存）：
// 地址带内容指纹，换了图标就换地址。标签写进产物 index.html，由 www/assistant 搬过去。
function fingerprint(body: string | Buffer): string {
  return createHash("sha256").update(body).digest("hex").slice(0, 10);
}

function icon(name: string): string {
  return `${BASE}${name}?v=${fingerprint(readFileSync(new URL(`./public/${name}`, import.meta.url)))}`;
}

// 与 scripts/build-ios-splash.py 的 DEVICES 一致：(逻辑宽, 逻辑高, 倍率)
const SPLASH: [number, number, number][] = [
  [440, 956, 3], [430, 932, 3], [428, 926, 3], [414, 896, 3], [414, 896, 2], [402, 874, 3],
  [393, 852, 3], [390, 844, 3], [375, 812, 3], [375, 667, 2], [360, 780, 3],
];

function ccIcons(): Plugin {
  const manifest = () =>
    JSON.stringify(
      {
        id: "/assistant",
        name: "CC",
        short_name: "CC",
        start_url: "/assistant",
        scope: "/assistant",
        display: "standalone",
        // 启动画面与标题栏颜色 = 手机上的页面底色（globals.css 的 --cc-bg-app，手机 #f9f9f7）
        background_color: "#f9f9f7",
        theme_color: "#f9f9f7",
        icons: [
          { src: icon("icon-192.png"), sizes: "192x192", type: "image/png" },
          { src: icon("icon-512.png"), sizes: "512x512", type: "image/png" },
          // 安卓自适应图标：整块品牌橙、内容缩在中间 78%（由 icon-512.png 铺橙底后缩放生成，2026-10-12）
          { src: icon("icon-maskable-192.png"), sizes: "192x192", type: "image/png", purpose: "maskable" },
          { src: icon("icon-maskable-512.png"), sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      null,
      2,
    ) + "\n";
  return {
    name: "cc-icons",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "manifest.webmanifest", source: manifest() });
    },
    transformIndexHtml: () => [
      { tag: "link", attrs: { rel: "manifest", href: `${BASE}manifest.webmanifest?v=${fingerprint(manifest())}` }, injectTo: "head" },
      { tag: "link", attrs: { rel: "icon", href: icon("favicon.ico"), sizes: "16x16 32x32 48x48" }, injectTo: "head" },
      { tag: "link", attrs: { rel: "icon", type: "image/png", href: icon("icon-192.png"), sizes: "192x192" }, injectTo: "head" },
      { tag: "link", attrs: { rel: "apple-touch-icon", href: icon("apple-touch-icon.png") }, injectTo: "head" },
      // iPhone 主屏幕 app 的启动图（scripts/build-ios-splash.py 生成，按屏幕尺寸与深浅色各一张）：点开即显示，不再黑屏
      ...SPLASH.flatMap(([w, h, r]) =>
        (["light", "dark"] as const).map((theme) => ({
          tag: "link",
          attrs: {
            rel: "apple-touch-startup-image",
            href: icon(`splash/splash-${w}x${h}@${r}x-${theme}.png`),
            media: `screen and (device-width: ${w}px) and (device-height: ${h}px) and (-webkit-device-pixel-ratio: ${r}) and (orientation: portrait) and (prefers-color-scheme: ${theme})`,
          },
          injectTo: "head" as const,
        })),
      ),
    ],
  };
}

export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss(), ccIcons()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../../apps/pearnly_core/pearnly_core/public/assistant",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name].[hash].js",
        chunkFileNames: "assets/[name].[hash].js",
        assetFileNames: "assets/[name].[hash][extname]",
      },
    },
  },
});
