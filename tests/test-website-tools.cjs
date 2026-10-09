// pearnly.com 免费工具的计算：数字转泰文金额、VAT、预扣税、税号校验、PromptPay 收款码、佛历换算、PDF 页码范围。
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
  const ok = t.taxId('0105599999997');
  assert.equal(ok.complete, true);
  assert.equal(ok.valid, true);
  assert.equal(ok.formatted, '0-1055-99999-99-7');
  assert.equal(t.taxId('0-1055-99999-99-7').valid, true);
  assert.equal(t.taxId('0105599999998').valid, false);
  assert.equal(t.taxId('12345').complete, false);
  assert.equal(t.taxId('').digits, '');
});

test('PromptPay payload follows the EMVCo layout with a CRC-16/CCITT check', () => {
  // 期望值的 CRC 另用 Python binascii.crc_hqx(…, 0xFFFF) 核过；二维码图片用 macOS Vision 扫回来也是这串
  assert.equal(t.promptpay('081-234-5678', '').payload,
    '00020101021129370016A0000006770101110113006681234567853037645802TH6304823E');
  assert.equal(t.promptpay('0812345678', '100').payload,
    '00020101021229370016A0000006770101110113006681234567853037645406100.005802TH6304F142');
  assert.equal(t.promptpay('0105599999997', '1,070.50').payload,
    '00020101021229370016A00000067701011102130105599999997530376454071070.505802TH63042556');
  assert.equal(t.promptpay('123456789012345', '').payload,
    '00020101021129390016A000000677010111031512345678901234553037645802TH6304AC13');
  assert.equal(t.crc16('123456789'), '29B1');   // CRC-16/CCITT-FALSE 的标准校验值
  assert.equal(t.promptpay('12345', '').error, 'target');
  assert.equal(t.promptpay('0812345678', '0').error, 'amount');
  assert.equal(t.promptpay('0812345678', 'abc').error, 'amount');
});

test('Buddhist and Gregorian years differ by 543 and dates are written the Thai way', () => {
  assert.deepEqual(t.year('2569'), { be: 2569, ce: 2026 });
  assert.deepEqual(t.year('2026'), { be: 2569, ce: 2026 });
  assert.equal(t.year('20x6'), null);
  assert.equal(t.year('0'), null);
  assert.deepEqual(t.thaiDate('2026-10-09'), { long: '9 ตุลาคม 2569', short: '9 ต.ค. 69', numeric: '09/10/2569' });
  assert.equal(t.thaiDate('2028-02-29').long, '29 กุมภาพันธ์ 2571');
  assert.equal(t.thaiDate('2026-02-29'), null);
});

test('PDF page ranges keep the typed order and stay inside the document', () => {
  assert.deepEqual(t.pageRange('1-3, 5, 8-', 10), [0, 1, 2, 4, 7, 8, 9]);
  assert.deepEqual(t.pageRange('3,1,3', 5), [2, 0]);
  assert.deepEqual(t.pageRange('2，4', 5), [1, 3]);
  assert.equal(t.pageRange('6', 5), null);
  assert.equal(t.pageRange('3-1', 5), null);
  assert.equal(t.pageRange('a', 5), null);
  assert.equal(t.pageRange('', 5), null);
});

test('tax ID company lookup reads the Revenue Department reply like the ERP does', () => {
  const fs = require('node:fs'), path = require('node:path');
  const fixture = name => fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8');
  // 同一份夹具在 tests/test_erp_tax_lookup.py 里给 ERP 的 rd_contracts.py 用
  const c = t.rdCompany(fixture('rd_vat_0105546015062.xml'));
  assert.deepEqual(c, { taxId: '0105546015062', branch: 0, name: 'บริษัท ซินเซียร์ไอซ์ จำกัด',
    address: '14 รามคำแหง 189 มีนบุรี มีนบุรี กรุงเทพมหานคร 10510', regBE: '2546-02-14' });
  assert.equal(t.rdExists(fixture('rd_tin_0105546015062.xml')), true);
  const notFound = '<vmsgerr><anyType xsi:type="xsd:string">ไม่พบข้อมูลที่ต้องการค้นหา &lt;br&gt; Data not found </anyType></vmsgerr>';
  assert.equal(t.rdCompany(notFound), null);
  assert.equal(t.rdExists('<vMessageErr><anyType>x</anyType></vMessageErr><vID /><vIsExist />'), false);
  assert.equal(t.rdFields('<vName><anyType>A &amp; B</anyType></vName>').Name, 'A & B');
});

test('PDF to Excel turns cells into real numbers and dates and keeps IDs as text', () => {
  const c = s => t.sheetCell(s);
  assert.deepEqual(c('1,234.50'), { t: 'n', v: 1234.5, fmt: '#,##0.00' });
  assert.equal(c('(500.00)').v, -500);
  assert.equal(c('500.00-').v, -500);
  assert.equal(c('฿ 1,070').v, 1070);
  assert.equal(c('0812345678').t, 's');           // 以 0 开头的号码保持文字
  assert.equal(c('0105546015062').t, 's');
  assert.equal(c('12,34').t, 's');                // 千分位不对就不是数字
  assert.deepEqual(c('01/10/69').v, { y: 2026, m: 10, d: 1 });   // 两位佛历年
  assert.deepEqual(c('1/10/26').v, { y: 2026, m: 10, d: 1 });    // 两位公历年
  assert.deepEqual(c('01/10/2569').v, { y: 2026, m: 10, d: 1 });
  assert.deepEqual(c('9 ต.ค. 69').v, { y: 2026, m: 10, d: 9 });
  assert.deepEqual(c('09 ตุลาคม 2569').v, { y: 2026, m: 10, d: 9 });
  assert.deepEqual(c('01-Oct-26').v, { y: 2026, m: 10, d: 1 });
  assert.equal(c('31/02/69').t, 's');
  assert.equal(c(''), null);
  assert.equal(t.cleanThai('จํากัด เลขที\u0000'), 'จำกัด เลขที');
  assert.equal(t.cleanThai('ที\uF70A'), 'ที่');           // 旧式泰文字体私用区的声调
  assert.equal(t.cleanThai('\uF700าน'), 'ฐาน');
  assert.equal(t.garbled('ºÃÔÉÑ· ¨Ó¡Ñ´ ¨Ó¡Ñ´'), true);
  assert.equal(t.garbled('บริษัท จำกัด 1,234.00'), false);
});

test('PDF to Excel rebuilds rows and columns and drops repeated page headers', () => {
  // 两页同版式的对账单：抬头和表头每页重复，最后是页码
  const item = (str, x0, x1, y) => ({ str, x0, x1, y, h: 10 });
  const page = (rows, no) => [item('ธนาคารตัวอย่าง', 40, 140, 800), item('วันที่', 40, 70, 770), item('รายการ', 100, 140, 770), item('คงเหลือ', 300, 340, 770)]
    .concat(rows.map((r, i) => [item(r[0], 40, 85, 750 - i * 15), item(r[1], 100, 100 + r[1].length * 5, 750 - i * 15), item(r[2], 340 - r[2].length * 5, 340, 750 - i * 15)]).flat())
    .concat([item('หน้า ' + no + '/2', 180, 220, 60)]);
  const rows = t.tableFromPages([
    page([['01/10/69', 'โอนเงินเข้า', '1,000.00'], ['02/10/69', 'ค่าไฟฟ้า', '900.00'], ['03/10/69', 'PromptPay', '850.00'], ['04/10/69', 'โอนเงินเข้า', '1,850.00']], 1),
    page([['05/10/69', 'ค่าน้ำ', '1,700.00'], ['06/10/69', 'โอนเงินเข้า', '2,700.00'], ['07/10/69', 'PromptPay', '2,600.00'], ['08/10/69', 'ค่าโทรศัพท์', '2,100.00']], 2)]);
  assert.deepEqual(rows[0], ['ธนาคารตัวอย่าง', '', '']);
  assert.deepEqual(rows[1], ['วันที่', 'รายการ', 'คงเหลือ']);
  assert.equal(rows.length, 10);                  // 抬头、表头各一次 + 8 行交易，页码去掉
  assert.deepEqual(rows[9], ['08/10/69', 'ค่าโทรศัพท์', '2,100.00']);
});

test('Excel to PDF writes numbers and dates the way the cell format says', () => {
  const acc = '_(* #,##0.00_);_(* (#,##0.00);_(* "-"??_);_(@_)';
  assert.equal(t.formatNumber(1234.5, '#,##0.00'), '1,234.50');
  assert.equal(t.formatNumber(-1234.5, '#,##0.00;(#,##0.00)'), '(1,234.50)');
  assert.equal(t.formatNumber(-1234.5, '#,##0.00'), '-1,234.50');
  assert.equal(t.formatNumber(1234.5, acc), '1,234.50');
  assert.equal(t.formatNumber(-1234.5, acc), '(1,234.50)');
  assert.equal(t.formatNumber(0, acc), '-');
  assert.equal(t.formatNumber(0.075, '0.0%'), '7.5%');
  assert.equal(t.formatNumber(1500, '[$฿-41E]#,##0.00'), '฿1,500.00');
  assert.equal(t.formatNumber(1500, '"฿"#,##0'), '฿1,500');
  assert.equal(t.formatNumber(3.14159, 'General'), '3.14159');
  const d = new Date(Date.UTC(2026, 9, 9));
  assert.equal(t.formatDate(d, 'dd/mm/yyyy'), '09/10/2026');
  assert.equal(t.formatDate(d, 'd-mmm-yy'), '9-Oct-26');
  assert.equal(t.formatDate(d, 'General'), '09/10/2026');
});

test('PDF to Excel splits space-padded report lines and dates stuck to the next text', () => {
  const it = (str, x0, x1, y) => ({ str, x0, x1, y, h: 10 });
  // 一整行写成一串、列之间多个空格（系统打印的报表）；分隔线行去掉
  assert.deepEqual(t.tableFromPages([[it('Date      Description          Amount', 20, 380, 700), it('01/05/69  โอนเงินเข้า             1,000.00', 20, 380, 690),
    it('02/05/69  ค่าไฟฟ้า              250.50', 20, 380, 680), it('03/05/69  PromptPay           12.00', 20, 380, 670), it('04/05/69  ค่าน้ำ                 99.00', 20, 380, 660),
    it('----------------------------------', 20, 380, 650)]]),
    [['Date', 'Description', 'Amount'], ['01/05/69', 'โอนเงินเข้า', '1,000.00'], ['02/05/69', 'ค่าไฟฟ้า', '250.50'], ['03/05/69', 'PromptPay', '12.00'], ['04/05/69', 'ค่าน้ำ', '99.00']]);
  // 日期和单号挤在同一段
  assert.deepEqual(t.tableFromPages([[it('01/05/69 INV-001 ขายสินค้า', 20, 200, 700), it('1,000.00', 300, 340, 700), it('02/05/69 INV-002 ขายสินค้า', 20, 200, 690), it('250.00', 310, 340, 690),
    it('03/05/69 INV-003 ขาย', 20, 180, 680), it('80.00', 315, 340, 680), it('04/05/69 INV-004 ขาย', 20, 180, 670), it('5.00', 322, 340, 670)]]).map(r => r[0]),
    ['01/05/69', '02/05/69', '03/05/69', '04/05/69']);
  assert.deepEqual(t.pageLines([it('01/05/2569ณ วันที่', 20, 120, 700)])[0].chunks.map(c => c.text), ['01/05/2569', 'ณ วันที่']);   // 日期后面直接接泰文
});

test('PDF to Excel splits a column of "date + text" even when wrapped lines fill the gap', () => {
  const it = (str, x0, x1, y) => ({ str, x0, x1, y, h: 10 });
  const rows = t.tableFromPages([[it('01/05/69 IV0001 ขายสินค้า', 20, 160, 700), it('1,000.00', 300, 340, 700), it('ขายสินค้าต่อเนื่องยาว', 20, 150, 690),
    it('02/05/69 IV0002 ขาย', 20, 140, 680), it('250.00', 310, 340, 680), it('03/05/69 IV0003 ขาย', 20, 140, 670), it('80.00', 315, 340, 670)]]);
  assert.deepEqual(rows, [['01/05/69', 'IV0001 ขายสินค้า', '1,000.00'], ['', 'ขายสินค้าต่อเนื่องยาว', ''], ['02/05/69', 'IV0002 ขาย', '250.00'], ['03/05/69', 'IV0003 ขาย', '80.00']]);
});

test('Excel to PDF writes times from the cell format', () => {
  const d = new Date(Date.UTC(2026, 9, 9, 14, 5, 30));
  assert.equal(t.formatDate(d, 'h:mm'), '14:05');
  assert.equal(t.formatDate(d, 'yyyy-mm-dd h:mm:ss'), '2026-10-09 14:05:30');
  assert.equal(t.formatDate(d, 'h:mm AM/PM'), '2:05 PM');
  assert.equal(t.formatDate(d, 'mm-dd-yy'), '09/10/2026');
  assert.equal(t.formatDate(d, 'm/d/yy h:mm'), '09/10/2026 14:05');
  assert.equal(t.formatDate(new Date(Date.UTC(2026, 9, 9, 9, 5)), 'h:mm'), '9:05');
  assert.equal(t.formatDate(new Date(Date.UTC(2026, 9, 9, 9, 5)), 'hh:mm'), '09:05');
});
