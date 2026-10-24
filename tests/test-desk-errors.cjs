// 页面 JS 报错回传（public/desk/errors.js）：报错与脚本自己的 Promise 拒绝都发给服务端；同一条只发一次、每页最多 10 条；
// 资源加载失败和 frappe.call 的失败（框架已提示、服务端已记）不发。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/pearnly_core/pearnly_core/public/desk/errors.js', 'utf8');
const listeners = {}, posts = [];
const window = { addEventListener: (name, fn) => { listeners[name] = fn; } };
vm.runInNewContext(source, { window, Error, location: { pathname: '/desk/pearnly-stock-count/STC-1' },
  frappe: { csrf_token: 'tok' },
  fetch: (url, options) => { posts.push({ url, options, body: JSON.parse(options.body) }); return Promise.resolve(); } });
window.frappe = { csrf_token: 'tok' };
listeners.error({ message: 'p.scan is not a function', filename: '/assets/pearnly_core/desk/scan.js', lineno: 9, colno: 2,
  error: { stack: 'TypeError: p.scan is not a function' } });
listeners.error({ message: 'p.scan is not a function', filename: '/assets/pearnly_core/desk/scan.js', lineno: 9, colno: 2 });
listeners.error({});  // 图片等资源加载失败
listeners.unhandledrejection({ reason: { exc_type: 'ValidationError', exc: '[]', message: 'x' } });
listeners.unhandledrejection({ reason: 'Filter missing' });  // 框架的字符串拒绝（报表缺筛选）
listeners.unhandledrejection({ reason: new Error('decode failed') });
assert.equal(posts.length, 2);
assert.equal(posts[0].url, '/api/method/pearnly_core.desk.client_errors.report');
assert.equal(posts[0].options.headers['X-Frappe-CSRF-Token'], 'tok');
assert.deepEqual([posts[0].body.kind, posts[0].body.line, posts[0].body.route], ['error', 9, '/desk/pearnly-stock-count/STC-1']);
assert.equal(posts[1].body.kind, 'rejection');
for (let n = 0; n < 20; n++) listeners.error({ message: `boom ${n}`, filename: 'a.js', lineno: n });
assert.equal(posts.length, 10);
// 前端单页：Vue / React 接住的报错经 window.pearnlyReportError 交过来；CSRF 取单页自己放的 window.csrf_token
const spaPosts = [], spaWindow = { addEventListener() {}, csrf_token: 'spa' };
vm.runInNewContext(source, { window: spaWindow, Error, location: { pathname: '/pearnly-app/dms/editor' },
  fetch: (url, options) => { spaPosts.push({ options, body: JSON.parse(options.body) }); return Promise.resolve(); } });
spaWindow.pearnlyReportError('not an error', 'vue');
spaWindow.pearnlyReportError(new Error('render failed'), 'react');
assert.equal(spaPosts.length, 1);
assert.equal(spaPosts[0].options.headers['X-Frappe-CSRF-Token'], 'spa');
assert.deepEqual([spaPosts[0].body.kind, spaPosts[0].body.message, spaPosts[0].body.route], ['react', 'render failed', '/pearnly-app/dms/editor']);
// 登录会话、页面还没拿到令牌：服务端 CSRF 拒绝（400）→ 留着，页面拿到令牌后补发一次
const retryPosts = [], timers = [], retryWindow = { addEventListener() {} };
vm.runInNewContext(source, { window: retryWindow, Error, location: { pathname: '/stocktake' },
  setInterval: fn => { timers.push(fn); return timers.length; }, clearInterval: () => { timers.length = 0; },
  fetch: (url, options) => { retryPosts.push(options.headers['X-Frappe-CSRF-Token']);
    return Promise.resolve({ status: options.headers['X-Frappe-CSRF-Token'] ? 200 : 400 }); } });
retryWindow.pearnlyReportError(new Error('scan failed'), 'error');
setImmediate(() => {
  assert.deepEqual(retryPosts, ['']);
  assert.equal(timers.length, 1);
  timers[0]();
  assert.deepEqual(retryPosts, [''], 'no token yet: keep waiting');
  retryWindow.csrf_token = 'page-token';
  timers[0]();
  assert.deepEqual(retryPosts, ['', 'page-token']);
  assert.equal(timers.length, 0);
  console.log('desk errors: ok');
});
