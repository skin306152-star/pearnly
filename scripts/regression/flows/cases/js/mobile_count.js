// 手机盘点页（手机宽度、LINE 外回落到本账号绑定）：打开刚建的盘点 → 用${v.how}输入「${v.code}」→ 应出录入卡（商品名、数量框）
// → 填数量保存 → 盘点里多一条记录（polymer 10-28 现象：认出商品、录入卡没出来、误报找不到）
const name = localStorage.getItem("flows_count");
const app = await wait(() => document.querySelector("[data-task], #scan, .card.error, .card.muted"), 20000);
if (!app) return {ok: false, why: "手机盘点页没出内容", body: document.body.innerText.slice(0, 300)};
if (document.querySelector(".card.error")) return {ok: false, why: "手机盘点页报错", body: document.querySelector(".card.error").innerText.slice(0, 300)};
const open = document.querySelector('[data-task="' + name + '"]');
if (open) { open.click(); }
const ready = await wait(() => document.getElementById("scan") && document.getElementById("code"), 15000);
if (!ready) return {ok: false, why: "打不开盘点任务", body: document.body.innerText.slice(0, 300), tasks: [...document.querySelectorAll("[data-task]")].map(b => b.dataset.task)};
if ("${v.how}" === "camera") {
  document.getElementById("scan").click();
} else {
  const input = document.getElementById("code"); input.value = "${v.code}"; document.getElementById("find").click();
}
const shown = await wait(() => document.getElementById("qty") || (document.getElementById("error") || {}).textContent, 25000);
const card = document.getElementById("qty") ? document.getElementById("qty").closest(".card") : null;
const state = {entry_shown: !!document.getElementById("qty"), error: (document.getElementById("error") || {}).textContent || "",
  card: card ? card.innerText.slice(0, 160) : "", choices: [...document.querySelectorAll("[data-choice]")].map(b => b.textContent)};
if (!state.entry_shown) return {ok: false, why: "认码后录入卡没出来", ...state};
if (!state.card.includes("${v.name}")) return {ok: false, why: "认出的商品不对", ...state};
document.getElementById("qty").value = "${v.qty}";
// 抬头「已盘点 x / y」（13dad827 起保存、撤销后随重查刷新）
const counted_of = () => { const m = ((document.querySelector(".card .muted") || {}).textContent || "").match(/(\d+)\s*\/\s*(\d+)/); return m ? Number(m[1]) : null; };
const before = counted_of();
document.getElementById("save").click();
// 这一页没有 frappe 对象：保存后页面按同一个码重查、重画——出现本条记录（带撤销按钮）、「实盘数量」变成刚填的数就算存上了
const saved = await wait(() => document.querySelector("[data-undo]") && counted_of() !== before ? true : null, 15000)
  || await wait(() => document.querySelector("[data-undo]"), 5000);
const card2 = document.getElementById("qty") ? document.getElementById("qty").closest(".card") : null;
const counted = card2 ? [...card2.querySelectorAll(".big")].map(e => e.textContent.trim()) : [];
const after_save = counted_of();
// 撤销这一条：页面再查一次码，抬头回到保存前
const undo = document.querySelector("[data-undo]");
if (undo) undo.click();
await wait(() => !document.querySelector("[data-undo]") && counted_of() === before, 15000);
const after_undo = counted_of();
// 查码会登记行，只收 POST：GET 直接调应被拒
const get = await fetch("/api/method/pearnly_erp_portal.inventory.line_mobile.api_line_stocktake_lookup?name=" + encodeURIComponent(name)
  + "&code=" + encodeURIComponent("${v.code}"), {credentials: "include"});
const header = {before, after_save, after_undo, get_status: get.status};
return {ok: !!saved && counted[1] === "${v.qty}" && before !== null && after_save === before + 1 && after_undo === before && get.status >= 400,
  ...state, counted, header, after: (document.getElementById("error") || {}).textContent || ""};
