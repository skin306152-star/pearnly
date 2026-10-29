// 打印：每类单据各取一张最新的（这一轮流程里建的 / 老数据夹具），打开原生打印页 → 200、出版面标题、没有模板报错；作废单带水印
const latest = async (doctype, filters) => ((await call("frappe.client.get_list", {doctype, filters, fields: ["name"], order_by: "creation desc", limit_page_length: 1}, "GET")) || [])[0];
const targets = [
  ["ERP Stock Entry", {direction: "in", status: "submitted"}, "Pearnly Goods Receipt Note", /ใบรับสินค้า|GOODS RECEIPT/],
  ["ERP Stock Entry", {direction: "out", status: "submitted"}, "Pearnly Goods Issue Slip", /ใบเบิกสินค้า|GOODS ISSUE/],
  ["Purchase Document", {status: "submitted", stock_posted_at: ["is", "set"]}, "Pearnly Purchase Goods Receipt", /ใบรับสินค้า|GOODS RECEIPT/],
  ["ERP Sales Document", {status: "submitted", doc_kind: "receipt", source: ["!=", "pos"]}, "Pearnly Sales Document", /ใบเสร็จรับเงิน|RECEIPT|ใบส่งของ|DELIVERY/],
  ["ERP Stock Count", {}, "Pearnly Stock Count", /ใบตรวจนับ|STOCK COUNT/],
  ["ERP Sale", {}, "Pearnly POS Receipt", /ใบเสร็จรับเงิน|RECEIPT|ใบรับคืน|RETURN/],
  ["ERP Shift", {status: "closed"}, "Pearnly Shift Summary", /สรุปการปิดกะ|SHIFT SUMMARY/],
];
const results = [];
for (const [doctype, filters, format, title] of targets) {
  const doc = await latest(doctype, filters);
  if (!doc) { results.push({doctype, format, skipped: "没有这类单"}); continue; }
  const url = "/printview?doctype=" + encodeURIComponent(doctype) + "&name=" + encodeURIComponent(doc.name) + "&format=" + encodeURIComponent(format) + "&no_letterhead=1";
  const response = await fetch(url, {credentials: "include"});
  const html = await response.text();
  results.push({doctype, name: doc.name, format, status: response.status, title: title.test(html),
    template_error: /Jinja|Traceback|Error/.test(html.replace(/console\.error|ErrorHandler/g, "")) && !title.test(html)});
}
const ok = results.every(r => r.skipped || (r.status === 200 && r.title && !r.template_error));
return {ok, results};
