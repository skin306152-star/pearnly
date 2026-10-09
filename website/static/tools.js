/* pearnly.com 免费工具：全部在浏览器里算，不发请求。纯函数同时导出给 node 测试（tests/test-website-tools.cjs）。
 * 金额一律按“分”（สตางค์）用 BigInt 算，第三位小数四舍五入，避免浮点误差。
 * 文件类工具（图片转 PDF、合并 / 拆分 PDF）的页面逻辑在 files.js；这里只放它们要用的纯函数（页码范围）。 */
(function (root) {
  'use strict';

  var DIGITS = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า'];
  var PLACES = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน'];
  var MAX_DIGITS = 15;

  // 读一串整数（泰文），每 6 位一个“ล้าน”；个位的 1 前面有更高位时读 เอ็ด（同 Excel BAHTTEXT）。
  function readInteger(str) {
    str = String(str).replace(/^0+/, '');
    var out = '';
    for (var i = 0; i < str.length; i++) {
      var d = +str[i];
      var pos = str.length - 1 - i;
      var place = pos % 6;
      if (d !== 0) {
        if (place === 0 && d === 1 && i > 0) out += 'เอ็ด';
        else if (place === 1 && d === 1) out += 'สิบ';
        else if (place === 1 && d === 2) out += 'ยี่สิบ';
        else out += DIGITS[d] + PLACES[place];
      }
      if (place === 0 && pos > 0) out += 'ล้าน';
    }
    return out;
  }

  // "1,234.565" → { negative, integer: '1234', satang: 57 }；格式不对返回 null。
  function parseAmount(input) {
    var s = String(input == null ? '' : input).replace(/[,\s฿]/g, '');
    var m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(s);
    if (!m || (m[2] === '' && !m[3])) return null;
    var integer = (m[2] || '0').replace(/^0+(?=\d)/, '');
    if (integer.length > MAX_DIGITS) return null;
    var frac = (m[3] || '') + '000';
    var satang = +frac.slice(0, 2) + (+frac[2] >= 5 ? 1 : 0);
    if (satang === 100) {
      integer = (BigInt(integer) + 1n).toString();
      satang = 0;
    }
    return { negative: m[1] === '-', integer: integer, satang: satang };
  }

  function bahtText(input) {
    var a = parseAmount(input);
    if (!a) return null;
    var baht = readInteger(a.integer);
    if (!baht && !a.satang) return 'ศูนย์บาทถ้วน';
    var text = (baht ? baht + 'บาท' : '') + (a.satang ? readInteger(String(a.satang)) + 'สตางค์' : 'ถ้วน');
    return (a.negative ? 'ลบ' : '') + text;
  }

  function toSatang(input) {
    var a = parseAmount(input);
    if (!a || a.negative) return null;
    return BigInt(a.integer) * 100n + BigInt(a.satang);
  }

  // 正数 a*b/c 四舍五入到整数分。
  function mulDivRound(a, b, c) {
    return (a * b * 2n + c) / (2n * c);
  }

  // 税率用万分位（7% = 700），避免小数。
  function rateBp(input) {
    var a = parseAmount(input);
    if (!a || a.negative || a.integer.length > 3) return null;
    var bp = BigInt(a.integer) * 100n + BigInt(a.satang);
    return bp <= 10000n ? bp : null;
  }

  function vat(amount, rate, inclusive) {
    var x = toSatang(amount), bp = rateBp(rate);
    if (x === null || bp === null) return null;
    var base = inclusive ? mulDivRound(x, 10000n, 10000n + bp) : x;
    var tax = inclusive ? x - base : mulDivRound(base, bp, 10000n);
    return { base: base, vat: tax, total: base + tax };
  }

  // 预扣税只按未含 VAT 的金额算；pay = 应付对方的净额。
  function withholding(amount, inclusive, vatRate, whtRate) {
    var v = vat(amount, vatRate, inclusive), bp = rateBp(whtRate);
    if (!v || bp === null) return null;
    var wht = mulDivRound(v.base, bp, 10000n);
    return { base: v.base, vat: v.vat, total: v.total, wht: wht, pay: v.total - wht };
  }

  function formatSatang(x) {
    var neg = x < 0n;
    if (neg) x = -x;
    var baht = (x / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var satang = (x % 100n).toString().padStart(2, '0');
    return (neg ? '-' : '') + baht + '.' + satang;
  }

  // 13 位税号 / 身份证号的 MOD-11 校验位（同 pearnly_core.tax_documents.amounts.valid_tax_id）。
  function taxId(input) {
    var raw = String(input == null ? '' : input).replace(/[\s-]/g, '');
    if (!/^\d{13}$/.test(raw)) return { digits: raw, complete: false, valid: false, formatted: '' };
    var total = 0;
    for (var i = 0; i < 12; i++) total += +raw[i] * (13 - i);
    var valid = (11 - total % 11) % 10 === +raw[12];
    var formatted = [raw.slice(0, 1), raw.slice(1, 5), raw.slice(5, 10), raw.slice(10, 12), raw.slice(12)].join('-');
    return { digits: raw, complete: true, valid: valid, formatted: formatted };
  }

  // ---- PromptPay 收款码：按泰国央行公布的 EMVCo 格式拼出二维码内容，最后是 CRC-16/CCITT（初值 FFFF） ----
  function tlv(id, value) { return id + String(value.length).padStart(2, '0') + value; }
  function crc16(text) {
    var crc = 0xFFFF;
    for (var i = 0; i < text.length; i++) {
      crc ^= text.charCodeAt(i) << 8;
      for (var b = 0; b < 8; b++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
    }
    return crc.toString(16).toUpperCase().padStart(4, '0');
  }
  // 收款账号：手机号 10 位（转成 0066 开头的 13 位）、身份证号 / 税号 13 位、电子钱包 15 位。金额可空（空 = 付款人自己填）。
  function promptpay(target, amount) {
    var id = String(target == null ? '' : target).replace(/[\s-]/g, '');
    var kind;
    if (/^0\d{9}$/.test(id)) { kind = '01'; id = '0066' + id.slice(1); }
    else if (/^\d{13}$/.test(id)) kind = '02';
    else if (/^\d{15}$/.test(id)) kind = '03';
    else return { error: 'target' };
    var money = '';
    if (String(amount == null ? '' : amount).trim()) {
      var x = toSatang(amount);
      if (x === null || x <= 0n) return { error: 'amount' };
      money = formatSatang(x).replace(/,/g, '');
    }
    var body = tlv('00', '01') + tlv('01', money ? '12' : '11')
      + tlv('29', tlv('00', 'A000000677010111') + tlv(kind, id))
      + tlv('53', '764') + (money ? tlv('54', money) : '') + tlv('58', 'TH') + '6304';
    return { payload: body + crc16(body), kind: kind };
  }

  // ---- 佛历 / 公历 ----
  var MONTHS = ['มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน', 'พฤษภาคม', 'มิถุนายน', 'กรกฎาคม', 'สิงหาคม', 'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม'];
  var MONTHS_SHORT = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
  // 一个年份：2400 以上当作佛历，否则当作公历；相差 543 年。
  function year(input) {
    var s = String(input == null ? '' : input).trim();
    if (!/^\d{1,4}$/.test(s)) return null;
    var y = +s;
    if (y < 1) return null;
    return y >= 2400 ? { be: y, ce: y - 543 } : { be: y + 543, ce: y };
  }
  // 公历日期 "2026-10-09"（日期选择框的值）→ 泰文写法。
  function thaiDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!m) return null;
    var y = +m[1], mo = +m[2], d = +m[3];
    var dt = new Date(Date.UTC(y, mo - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    var be = y + 543;
    return {
      long: d + ' ' + MONTHS[mo - 1] + ' ' + be,
      short: d + ' ' + MONTHS_SHORT[mo - 1] + ' ' + String(be).slice(-2),
      numeric: String(d).padStart(2, '0') + '/' + String(mo).padStart(2, '0') + '/' + be,
    };
  }

  // ---- 拆分 PDF 的页码范围："1-3, 5, 8-" → 从 0 开始的页序号（按输入顺序，去重）；超出总页数或写错返回 null ----
  function pageRange(input, total) {
    var s = String(input == null ? '' : input).replace(/\s/g, '');
    if (!s || !(total > 0)) return null;
    var out = [], seen = {};
    var parts = s.split(/[,，]/);
    for (var i = 0; i < parts.length; i++) {
      var m = /^(\d+)?(?:(-)(\d+)?)?$/.exec(parts[i]);
      if (!m || (!m[1] && !m[3]) || (!m[1] && !m[2])) return null;
      var a = m[1] ? +m[1] : 1, b = m[2] ? (m[3] ? +m[3] : total) : a;
      if (a < 1 || b < a || b > total) return null;
      for (var p = a; p <= b; p++) if (!seen[p]) { seen[p] = 1; out.push(p - 1); }
    }
    return out;
  }

  var api = {
    readInteger: readInteger, parseAmount: parseAmount, bahtText: bahtText, toSatang: toSatang,
    vat: vat, withholding: withholding, formatSatang: formatSatang, taxId: taxId,
    crc16: crc16, promptpay: promptpay, year: year, thaiDate: thaiDate, pageRange: pageRange,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  root.PearnlyTools = api;   // files.js 用（页码范围）

  // ---- 页面绑定：每个工具是一个 form[data-tool]，结果写进 [data-out] ----
  function $(form, sel) { return form.querySelector(sel); }
  function val(form, name) {
    var el = form.elements[name];
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked;
    return el.value;
  }
  function show(form, key, text) {
    var el = $(form, '[data-out="' + key + '"]');
    if (el) el.textContent = text;
  }
  function state(form, name) {
    var nodes = form.querySelectorAll('[data-state]');
    for (var i = 0; i < nodes.length; i++) nodes[i].hidden = nodes[i].getAttribute('data-state') !== name;
  }

  var RENDER = {
    'baht-text': function (form) {
      var text = val(form, 'amount').trim() ? bahtText(val(form, 'amount')) : '';
      show(form, 'text', text || '');
      state(form, text === null ? 'invalid' : text ? 'ok' : 'empty');
    },
    vat: function (form) {
      var r = val(form, 'amount').trim() ? vat(val(form, 'amount'), val(form, 'rate'), val(form, 'mode') === 'inclusive') : undefined;
      if (r === undefined) return state(form, 'empty');
      if (!r) return state(form, 'invalid');
      show(form, 'base', formatSatang(r.base));
      show(form, 'vat', formatSatang(r.vat));
      show(form, 'total', formatSatang(r.total));
      state(form, 'ok');
    },
    'withholding-tax': function (form) {
      var custom = $(form, '[data-custom-rate]');
      var preset = val(form, 'preset');
      if (custom) custom.hidden = preset !== 'custom';
      var rate = preset === 'custom' ? val(form, 'rate') : preset;
      var vatRate = val(form, 'vat') ? '7' : '0';
      var r = val(form, 'amount').trim() ? withholding(val(form, 'amount'), val(form, 'mode') === 'inclusive', vatRate, rate) : undefined;
      if (r === undefined) return state(form, 'empty');
      if (!r) return state(form, 'invalid');
      show(form, 'base', formatSatang(r.base));
      show(form, 'vat', formatSatang(r.vat));
      show(form, 'wht', formatSatang(r.wht));
      show(form, 'pay', formatSatang(r.pay));
      state(form, 'ok');
    },
    'tax-id': function (form) {
      var r = taxId(val(form, 'id'));
      if (!r.digits) return state(form, 'empty');
      if (!r.complete) return state(form, 'incomplete');
      show(form, 'formatted', r.formatted);
      state(form, r.valid ? 'valid' : 'invalid');
    },
    // 二维码画在 canvas 上（qrcode-generator 只在这一页加载），下载成 PNG；下面写上收款账号和金额，方便打印贴在收银台
    'promptpay-qr': function (form) {
      var target = val(form, 'target').trim(), amount = val(form, 'amount');
      if (!target) return state(form, 'empty');
      var r = promptpay(target, amount);
      if (r.error) return state(form, r.error === 'amount' ? 'bad-amount' : 'bad-target');
      var canvas = $(form, 'canvas'), qr = root.qrcode && root.qrcode(0, 'M');
      if (!qr || !canvas) return state(form, 'empty');
      qr.addData(r.payload); qr.make();
      var n = qr.getModuleCount(), cell = Math.max(4, Math.floor(560 / (n + 8))), size = (n + 8) * cell;
      var ctx = canvas.getContext('2d');
      canvas.width = size; canvas.height = size;
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size); ctx.fillStyle = '#000';
      for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) if (qr.isDark(y, x)) ctx.fillRect((x + 4) * cell, (y + 4) * cell, cell, cell);
      var t = toSatang(amount);
      show(form, 'who', target.replace(/[\s-]/g, ''));
      show(form, 'how-much', t === null || !String(amount).trim() ? '' : formatSatang(t) + ' ฿');
      state(form, 'ok');
    },
    'buddhist-year': function (form) {
      var y = year(val(form, 'year')), d = thaiDate(val(form, 'date'));
      var yo = $(form, '[data-year]'), dout = $(form, '[data-date]');
      if (yo) { yo.hidden = !y; if (y) { show(form, 'be', String(y.be)); show(form, 'ce', String(y.ce)); } }
      var bad = $(form, '[data-state="bad-year"]');
      if (bad) bad.hidden = !!y || !String(val(form, 'year')).trim();
      if (dout) { dout.hidden = !d; if (d) { show(form, 'long', d.long); show(form, 'short', d.short); show(form, 'numeric', d.numeric); } }
    },
  };

  function bind(form) {
    var render = RENDER[form.getAttribute('data-tool')];
    if (!render) return;
    var run = function () { render(form); };
    form.addEventListener('input', run);
    form.addEventListener('change', run);
    form.addEventListener('submit', function (e) { e.preventDefault(); run(); });
    form.querySelectorAll('[data-copy]').forEach(function (copy) {
      if (!navigator.clipboard) return;
      copy.addEventListener('click', function () {
        var out = $(form, '[data-out="' + copy.getAttribute('data-copy') + '"]');
        if (out && out.textContent) navigator.clipboard.writeText(out.textContent);
      });
    });
    var save = $(form, '[data-save-png]');
    if (save) {
      save.addEventListener('click', function () {
        var canvas = $(form, 'canvas');
        if (!canvas || !canvas.toBlob) return;
        canvas.toBlob(function (blob) {
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = save.getAttribute('data-save-png');
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        }, 'image/png');
      });
    }
    run();
  }

  var forms = document.querySelectorAll('form[data-tool]');
  for (var i = 0; i < forms.length; i++) bind(forms[i]);
})(this);
