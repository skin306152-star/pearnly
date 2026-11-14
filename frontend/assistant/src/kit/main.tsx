// 标准组件库展示页入口（kit.html）。语言、深浅色从地址栏读（?lang=th|zh|en|ja&theme=light|dark），
// 先写到 <html> 上再加载组件：i18n.ts 在加载时读 <html lang>。电脑 / 手机宽度看窗口宽（< 768 是手机变体，同正式页面）。
import "../globals.css";

const params = new URLSearchParams(location.search);
const lang = ["th", "zh", "en", "ja"].includes(params.get("lang") ?? "") ? (params.get("lang") as string) : "th";
const dark = params.get("theme") === "dark";
document.documentElement.lang = lang;
document.documentElement.classList.toggle("dark", dark);

void Promise.all([import("react-dom/client"), import("@/lib/variant"), import("./gallery"), import("./guide"), import("./guide-inputs"), import("./allow-card")]).then(
  ([{ createRoot }, { installVariant }, { Gallery }, { GuidePreview }, { GuideInputsPreview }, { AllowCardPreview }]) => {
    installVariant();
    // ?page=guide：引导卡预览（kit/guide.tsx，&decided=open 时「已定 n 项」展开）；?page=guide-inputs：引导卡的几种输入；?page=allow：允许 / 拒绝卡
    const page = params.get("page") === "allow" ? <AllowCardPreview /> : params.get("page") === "guide-inputs" ? <GuideInputsPreview /> : params.get("page") === "guide" ? <GuidePreview decidedOpen={params.get("decided") === "open"} last={params.get("item") === "last"} /> : <Gallery lang={lang} dark={dark} />;
    createRoot(document.getElementById("root") as HTMLElement).render(page);
  },
);
