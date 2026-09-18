import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
// 设计令牌必须**先**引入：app.css 里靠它覆盖（ADR-007：色值只有一份来源，
// 就是这份从旧产品整份搬来的设计系统）。
// 走打包器引入而不是 index.html 里挂 <link>：这样它会带 hash 参与构建，
// 不会被站点缓存成旧版本。
import "../design/pearnly-ui.css";
import "./styles/tailwind.css";
// app.css 放最后：我们的样式要能盖住 Tailwind 的工具类
// （例如给官方按钮指定主色，靠的就是"我们的类在后"）。
// Tailwind 自己带 base 层，所以 tailwind.css 必须排在 design 之后。
import "./styles/app.css";

createApp(App).use(router).mount("#app");
