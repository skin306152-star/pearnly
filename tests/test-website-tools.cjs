// pearnly.com 免费工具的计算：数字转泰文金额、VAT、预扣税、税号校验。
const test = require('node:test');
const assert = require('node:assert/strict');
const t = require('../website/static/tools.js');

test('baht text follows the BAHTTEXT reading', () => {
  const cases = {
    '0': 'ศูนย์บาทถ้วน',
    '1': 'หนึ่งบาทถ้วน',
    '11': 'สิบเอ็ดบาทถ้วน',
    '21': 'ยี่สิบเอ็ดบาทถ้วน',
    '20': 'ยี่สิบบาทถ้วน',
    '101': 'หนึ่งร้อยเอ็ดบาทถ้วน',
    '1001': 'หนึ่งพันเอ็ดบาทถ้วน',
    '110': 'หนึ่งร้อยสิบบาทถ้วน',
    '1000000': 'หนึ่งล้านบาทถ้วน',
    '11000000': 'สิบเอ็ดล้านบาทถ้วน',
    '21500000': 'ยี่สิบเอ็ดล้านห้าแสนบาทถ้วน',
    '1000000000000': 'หนึ่งล้านล้านบาทถ้วน',
    '1,234.50': 'หนึ่งพันสองร้อยสามสิบสี่บาทห้าสิบสตางค์',
    '0.25': 'ยี่สิบห้าสตางค์',
    '0.01': 'หนึ่งสตางค์',
    '5.11': 'ห้าบาทสิบเอ็ดสตางค์',
    '99.995': 'หนึ่งร้อยบาทถ้วน',
    '-12': 'ลบสิบสองบาทถ้วน',
    '฿ 1,070': 'หนึ่งพันเจ็ดสิบบาทถ้วน',
  };
  for (const [input, expected] of Object.entries(cases)) assert.equal(t.bahtText(input), expected, input);
});

test('baht text rejects what is not an amount', () => {
  for (const input of ['', 'abc', '1.2.3', '.', '1234567890123456']) assert.equal(t.bahtText(input), null, input);
});

test('VAT adds or extracts 7% and rounds half up to the satang', () => {
  const ex = t.vat('1000', '7', false);
  assert.deepEqual([ex.base, ex.vat, ex.total], [100000n, 7000n, 107000n]);
  const inc = t.vat('1070', '7', true);
  assert.deepEqual([inc.base, inc.vat, inc.total], [100000n, 7000n, 107000n]);
  const odd = t.vat('100', '7', true); // 93.457… → 93.46 + 6.54
  assert.deepEqual([odd.base, odd.vat], [9346n, 654n]);
  assert.equal(t.vat('0.07', '7', false).vat, 0n);
  assert.equal(t.vat('0.08', '7', false).vat, 1n);
  assert.equal(t.vat('-5', '7', false), null);
  assert.equal(t.vat('100', '200', false), null);
});

test('withholding tax is taken on the amount before VAT', () => {
  const r = t.withholding('10000', false, '7', '3');
  assert.deepEqual([r.base, r.vat, r.wht, r.pay], [1000000n, 70000n, 30000n, 1040000n]);
  const inc = t.withholding('10700', true, '7', '3');
  assert.deepEqual([inc.base, inc.wht, inc.pay], [1000000n, 30000n, 1040000n]);
  const noVat = t.withholding('5000', false, '0', '5');
  assert.deepEqual([noVat.vat, noVat.wht, noVat.pay], [0n, 25000n, 475000n]);
  assert.equal(t.withholding('1000', false, '7', 'x'), null);
});

test('amounts format with thousands separators', () => {
  assert.equal(t.formatSatang(123456789n), '1,234,567.89');
  assert.equal(t.formatSatang(5n), '0.05');
  assert.equal(t.formatSatang(-150n), '-1.50');
});

test('tax id check digit matches the shared MOD-11 rule', () => {
  const ok = t.taxId('0105536112014');
  assert.equal(ok.complete, true);
  assert.equal(ok.valid, true);
  assert.equal(ok.formatted, '0-1055-36112-01-4');
  assert.equal(t.taxId('0-1055-36112-01-4').valid, true);
  assert.equal(t.taxId('0105536112015').valid, false);
  assert.equal(t.taxId('12345').complete, false);
  assert.equal(t.taxId('').digits, '');
});
