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

const app = createApp(App).use(router);
// 等**首个路由解析完**再挂载：`App.vue` 在 onMounted 里按路由判断"这是不是独立页"，
// 首路由还没解析时 `route.meta` 是空的，独立页会去调一次 `shell.describe`（403）。
// 那一声 403 不影响渲染，但会让人以为"没登录也能开这一页"是坏的。
router.isReady().then(() => app.mount("#app"));
