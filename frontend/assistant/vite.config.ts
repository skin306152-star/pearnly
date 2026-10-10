import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 智能体对话页（ADR-019）。与 frontend/ 一样构建成**静态文件**交给站点提供：
// 不做常驻 Node 服务；接口全部是本站 `pearnly_core.assistant.api` 的白名单方法，登录态就是站点会话。
//
// 产物放 `apps/pearnly_core/pearnly_core/public/assistant/`，所以资源前缀必须是
// `/assets/pearnly_core/assistant/`（Frappe 只把 `/assets/<app>/…` 映射到 App 的 public）。
// 页面入口是共用层的 www/assistant（Cowork / ERP / DMS 每个租户站点都有）（读这里产出的 index.html 搬标签，见那边的说明）。
export default defineConfig({
  base: "/assets/pearnly_core/assistant/",
  plugins: [react(), tailwindcss()],
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
