// 录入工作台原件上传（ADR-016）的浏览器一侧：直传 / 照旧经服务器 / 重试确认 / 失败原因，全部离线（假 fetch，不连站点、不连谷歌）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../apps/pearnly_cowork/pearnly_cowork/public/desk/post-file.js'), 'utf8');

function load(replies) {
  const calls = [];
  const queue = [...replies];
  const fetch = async (url, options = {}) => {
    const body = options.body instanceof URLSearchParams ? Object.fromEntries(options.body)
      : options.body instanceof FormData ? {form: [...options.body.keys()]} : options.body;
    calls.push({url, method: options.method, body, headers: options.headers});
    const [status, payload] = queue.shift();
    return {ok: status >= 200 && status < 300, status, json: async () => payload};
  };
  const frappe = {pearnly: {}, csrf_token: 'csrf', msgprint() {}};
  vm.runInNewContext(source, {frappe, fetch, URLSearchParams, FormData, File, setTimeout, $: () => ({})});
  return {upload: frappe.pearnly.upload_original, calls};
}

const file = new File([new Uint8Array(1234)], 'scan.pdf', {type: 'application/pdf'});
const SOURCE = {name: 'CWS-1', page_count: 3};
const SIGNED = {direct: 1, url: 'https://storage.googleapis.com/b/o?sig', headers: {'x-goog-if-generation-match': '0'}};

test('直传：要地址 → PUT 到谷歌存储 → 确认登记', async () => {
  const {upload, calls} = load([[200, {message: SIGNED}], [200, null], [200, {message: SOURCE}]]);
  assert.deepEqual(await upload(file, 'k1'), SOURCE);
  assert.equal(calls[0].url, '/api/method/pearnly_cowork.files.api.begin_upload');
  assert.deepEqual(calls[0].body, {filename: 'scan.pdf', byte_size: '1234', request_key: 'k1'});
  assert.equal(calls[0].headers['X-Frappe-CSRF-Token'], 'csrf');
  assert.deepEqual([calls[1].url, calls[1].method, calls[1].body, calls[1].headers], [SIGNED.url, 'PUT', file, SIGNED.headers]);
  assert.equal(calls[1].headers['X-Frappe-CSRF-Token'], undefined);   // 站点的 CSRF 不发给谷歌
  assert.equal(calls[2].url, '/api/method/pearnly_cowork.files.api.finish_upload');
  assert.deepEqual(calls[2].body, calls[0].body);
});

test('PUT 回 412（之前传过）照样去确认；Cloud Run 忙时隔几秒再确认', async () => {
  const waits = [];
  const {upload, calls} = load([[200, {message: SIGNED}], [412, null], [200, {message: {retry: 5}}],
    [200, {message: SOURCE}]]);
  assert.deepEqual(await upload(file, 'k1', {wait: async ms => waits.push(ms)}), SOURCE);
  assert.deepEqual(waits, [5000]);
  assert.equal(calls.length, 4);
});

test('没开直传：照旧 multipart 交给 files.api.upload，带幂等键', async () => {
  const {upload, calls} = load([[200, {message: {direct: 0}}], [200, {message: SOURCE}]]);
  assert.deepEqual(await upload(file, 'k 2'), SOURCE);
  assert.equal(calls[1].url, '/api/method/pearnly_cowork.files.api.upload?request_key=k+2');
  assert.deepEqual(calls[1].body, {form: ['file']});
});

test('同一个键已经登记过：不再上传', async () => {
  const {upload, calls} = load([[200, {message: {direct: 1, done: SOURCE}}]]);
  assert.deepEqual(await upload(file, 'k1'), SOURCE);
  assert.equal(calls.length, 1);
});

test('失败原因原样交回调用方（服务端提示 / 谷歌拒收 / 一直忙）', async () => {
  const refused = {_server_messages: JSON.stringify([JSON.stringify({message: 'This file type is not supported.'})])};
  let {upload} = load([[417, refused]]);
  await assert.rejects(upload(file, 'k1'), error => error.status === 417 && error._server_messages === refused._server_messages);
  ({upload} = load([[200, {message: SIGNED}], [400, null]]));
  await assert.rejects(upload(file, 'k1'), error => error.status === 400);
  ({upload} = load([[200, {message: SIGNED}], [200, null], ...Array(6).fill([200, {message: {retry: 1}}])]));
  await assert.rejects(upload(file, 'k1', {wait: async () => {}}), error => error.status === 503);
});

test('页面切走后不再确认', async () => {
  const {upload, calls} = load([[200, {message: SIGNED}], [200, null], [200, {message: {retry: 2}}]]);
  assert.equal(await upload(file, 'k1', {alive: () => false, wait: async () => {}}), null);
  assert.equal(calls.length, 3);
});
