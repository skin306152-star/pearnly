// CC 对话页（/assistant，React 应用，不是 Desk）：页面渲染出来、点设置齿轮后设置窗口出现、能切到「连接」一栏。
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
for (let i = 0; i < 60 && !document.querySelector("[data-slot='cc-settings-button']"); i++) await sleep(500);
const button = document.querySelector("[data-slot='cc-settings-button']");
if (!button) return { ok: false, why: "对话页没渲染出设置按钮", text: document.body.innerText.slice(0, 160) };
button.click();
let dialog = null;
for (let i = 0; i < 20 && !dialog; i++) { await sleep(250); dialog = document.querySelector("[role='dialog']"); }
if (!dialog) return { ok: false, why: "点设置后没出现设置窗口" };
const tabs = [...dialog.querySelectorAll("[role='tab'], button")].map((b) => b.innerText.trim()).filter(Boolean).slice(0, 12);
return { ok: dialog.innerText.trim().length > 0, tabs };
