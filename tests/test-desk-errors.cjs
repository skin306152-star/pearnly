// 页面 JS 报错回传（public/desk/errors.js）：报错与脚本自己的 Promise 拒绝都发给服务端；同一条只发一次、每页最多 10 条；
// 资源加载失败和 frappe.call 的失败（框架已提示、服务端已记）不发。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/errors.js', 'utf8');
const listeners = {}, posts = [];
const window = { addEventListener: (name, fn) => { listeners[name] = fn; } };
vm.runInNewContext(source, { window, location: { pathname: '/desk/pearnly-stock-count/STC-1' },
  frappe: { csrf_token: 'tok' },
  fetch: (url, options) => { posts.push({ url, options, body: JSON.parse(options.body) }); return Promise.resolve(); } });
window.frappe = { csrf_token: 'tok' };
listeners.error({ message: 'p.scan is not a function', filename: '/assets/pearnly_core/desk/scan.js', lineno: 9, colno: 2,
  error: { stack: 'TypeError: p.scan is not a function' } });
listeners.error({ message: 'p.scan is not a function', filename: '/assets/pearnly_core/desk/scan.js', lineno: 9, colno: 2 });
listeners.error({});  // 图片等资源加载失败
listeners.unhandledrejection({ reason: { exc_type: 'ValidationError', exc: '[]', message: 'x' } });
listeners.unhandledrejection({ reason: new Error('decode failed') });
assert.equal(posts.length, 2);
assert.equal(posts[0].url, '/api/method/pearnly_core.desk.client_errors.report');
assert.equal(posts[0].options.headers['X-Frappe-CSRF-Token'], 'tok');
assert.deepEqual([posts[0].body.kind, posts[0].body.line, posts[0].body.route], ['error', 9, '/desk/pearnly-stock-count/STC-1']);
assert.equal(posts[1].body.kind, 'rejection');
for (let n = 0; n < 20; n++) listeners.error({ message: `boom ${n}`, filename: 'a.js', lineno: n });
assert.equal(posts.length, 10);
console.log('desk errors: ok');
