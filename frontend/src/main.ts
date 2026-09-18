import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
// 设计令牌必须**先**引入：app.css 里靠它覆盖（ADR-007：色值只有一份来源，
// 就是这份从旧产品整份搬来的设计系统）。
// 走打包器引入而不是 index.html 里挂 <link>：这样它会带 hash 参与构建，
// 不会被站点缓存成旧版本。
import "../design/pearnly-ui.css";
import "./styles/app.css";
// Tailwind 与"把官方组件配色接到我们令牌上"的覆盖放最后：它要盖住 Tailwind 的 base 层
import "./styles/tailwind.css";

createApp(App).use(router).mount("#app");
