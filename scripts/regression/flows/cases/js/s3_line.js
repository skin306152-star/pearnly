// LINE 手机页（手机宽度、LINE 外回落到本账号绑定）：输系统没有的码 → 「新建物料」表单 → 建好接着出录入卡、有「仓位」→ 记 2；
// 再输停用商品的码 → 照样认出、记 1
const {count, codes, disabled} = JSON.parse(localStorage.getItem("flows_s3") || "{}");
await wait(() => document.querySelector("[data-task], #scan, .card.error"), 20000);
const open = document.querySelector('[data-task="' + count + '"]');
if (open) open.click();
if (!await wait(() => document.getElementById("code"), 15000)) return {ok: false, why: "打不开盘点任务", body: document.body.innerText.slice(0, 300)};
const find = code => { const input = document.getElementById("code"); input.value = code; document.getElementById("find").click(); };
find(codes.line);
const offer = await wait(() => document.getElementById("np_name"), 15000);
if (!offer) return {ok: false, why: "认不到的码没出新建表单", error: (document.getElementById("error") || {}).textContent};
offer.value = "แล็บมือถือ " + codes.line; document.getElementById("np_unit").value = "ชิ้น";
document.getElementById("np_save").click();
const qty = await wait(() => document.getElementById("qty"), 15000);
if (!qty) return {ok: false, why: "建好后录入卡没出来", error: (document.getElementById("error") || {}).textContent};
const card = qty.closest(".card").innerText;
const loc = !!document.getElementById("loc");
qty.value = "2"; document.getElementById("save").click();
const saved = await wait(() => document.querySelector("[data-undo]"), 15000);
find(disabled.code);
const qty2 = await wait(() => { const q = document.getElementById("qty"); return q && q.closest(".card").innerText.includes("หยุดขาย") ? q : null; }, 15000);
let saved2 = null;
if (qty2) { qty2.value = "1"; document.getElementById("save").click(); saved2 = await wait(() => document.querySelector("[data-undo]"), 15000); }
return {ok: !!saved && loc && !!saved2, card: card.slice(0, 160), loc, saved: !!saved, disabled_saved: !!saved2};
