import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建成**静态文件**交给站点的 web 提供（ADR-005：不做常驻 Node 服务，不增服务器）。
//
// 产物放哪：`apps/pearnly_core/pearnly_core/public/pearnly-app/`。
// 放在 core 而不是各产品 App 里，是因为四个入口共用同一份前端 —— 四个产品各拷一份
// 就成了四份要同步维护的东西，与 ADR-005「复用同一份前端程序」相反。
export default defineConfig({
  // 必须写成这个前缀：Frappe 只把 `/assets/<app>/…` 映射到
  // `apps/<app>/<app>/public/…`（见 infrastructure/images/frappe.Dockerfile 里建的软链）。
  // 用默认的 base `/` 时，产物里会写成 `/assets/index.<hash>.js`，
  // 那个 URL 会被当成"某个叫 index.<hash>.js 的 App"，直接 404（这是必踩的坑）。
  base: "/assets/pearnly_core/pearnly-app/",
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../apps/pearnly_core/pearnly_core/public/pearnly-app",
    emptyOutDir: true,
    // 文件名带 hash：站点是按 URL 缓存的，不带 hash 改完不生效（旧系统为此专门写了
    // 一个 check_cachebust.py 来防漏改版本号，这里用 hash 从根上免掉那类事故）。
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name].[hash].js",
        chunkFileNames: "assets/[name].[hash].js",
        assetFileNames: "assets/[name].[hash][extname]",
      },
    },
  },
  server: {
    // 刻意**不做** dev server 代理。
    //
    // 为什么：业务接口靠会话 cookie 认证，而 cookie 绑定在站点域名上。代理到另一个
    // 源之后，登录态、CSRF、跨域规则全都和线上不是一回事 —— 在那种环境里"验证通过"
    // 说明不了任何问题（旧系统为此吃过亏）。
    //
    // 所以本地开发的形态与线上一致：**产出静态文件 → 站点自己提供 → 直接打开站点域名**。
    // 迭代用 `npm run build -- --watch`，改完刷新页面即可（文件名带 hash，不会吃旧缓存）。
  },
});
