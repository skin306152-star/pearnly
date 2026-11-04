// 扫码读码器只上报真正的异常（11-05 主会话按类定）：权限被拒、LINE / App 里打不开、没有相机、相机被占用、浏览器不支持
// 都是使用环境的正常情况，只提示、不进站点 Error Log；读码器加载失败与没归类的异常照常上报。
// 用 Node 加载 public/scan/reader.js（不起浏览器），看 PearnlyScan.report 交给上报器的有哪些。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "apps/pearnly_core/pearnly_core/public/scan/reader.js"), "utf8");

function load(agent) {
  const reported = [];
  const context = {
    navigator: { userAgent: agent, mediaDevices: {} },
    location: { href: "https://erp.example.com/desk" },
    isSecureContext: true,
    pearnlyCaught: (error, kind) => reported.push([error.name || error.message, kind]),
    setTimeout, clearTimeout, URL, console,
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return { scan: context.PearnlyScan, reported };
}

const named = name => Object.assign(new Error(name), { name });
const desktop = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const lineApp = "Mozilla/5.0 (Linux; Android 13; SM-A145F) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36 Line/14.1.0/IAB";

for (const agent of [desktop, lineApp]) {
  const { scan, reported } = load(agent);
  // 正常情况：权限被拒（电脑 = denied，LINE 里 = in_line）、没有相机、相机被占用
  for (const name of ["NotAllowedError", "SecurityError", "PermissionDeniedError", "NotFoundError", "OverconstrainedError",
                      "NotReadableError", "AbortError"]) {
    scan.report(named(name), "scan");
  }
  scan.report(Object.assign(new Error("unsupported"), { scan_reason: "unsupported" }), "reader");
  assert.deepEqual(reported, [], `expected camera conditions must not be reported (${agent.slice(-20)})`);
  // 真正的异常照常上报：读码器加载失败、没归类的错误
  scan.report(Object.assign(new Error("decoder"), { scan_reason: "decoder" }), "reader");
  scan.report(new TypeError("cannot read properties of undefined"), "reader");
  assert.deepEqual(reported.map(row => row[1]), ["reader", "reader"]);
  assert.equal(reported.length, 2);
}

// 结构：扫码这两个文件里只剩 report 这一处直接调上报器，别的分支不许绕过它
for (const file of ["apps/pearnly_core/pearnly_core/public/scan/reader.js", "apps/pearnly_core/pearnly_core/public/desk/scan.js"]) {
  const text = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
  const calls = (text.match(/pearnlyCaught\?\.\(/g) || []).length;
  assert.equal(calls, file.endsWith("reader.js") ? 1 : 0, `${file}: report camera failures through PearnlyScan.report`);
}
console.log("scan report: expected camera conditions stay quiet, real errors are reported");
