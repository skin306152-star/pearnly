// 标准组件库展示页入口（kit.html）。语言、深浅色从地址栏读（?lang=th|zh|en|ja&theme=light|dark），
// 先写到 <html> 上再加载组件：i18n.ts 在加载时读 <html lang>。电脑 / 手机宽度看窗口宽（< 768 是手机变体，同正式页面）。
import "../globals.css";

const params = new URLSearchParams(location.search);
const lang = ["th", "zh", "en", "ja"].includes(params.get("lang") ?? "") ? (params.get("lang") as string) : "th";
const dark = params.get("theme") === "dark";
document.documentElement.lang = lang;
document.documentElement.classList.toggle("dark", dark);

void Promise.all([import("react-dom/client"), import("@/lib/variant"), import("./gallery")]).then(
  ([{ createRoot }, { installVariant }, { Gallery }]) => {
    installVariant();
    createRoot(document.getElementById("root") as HTMLElement).render(<Gallery lang={lang} dark={dark} />);
  },
);
